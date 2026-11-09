"""Stock CARLA playback, isolated from the Tk event loop and lab launchers."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]


def stock_scenario(scenario, blueprint=""):
    result = copy.deepcopy(scenario)
    ego = result.setdefault("vehicles", {}).setdefault("ego", {})
    selected = blueprint.strip() or ego.get("blueprint_filter", "vehicle.audi.tt")
    if not re.fullmatch(r"vehicle\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", selected):
        raise ValueError("Ego blueprint must be an exact stock vehicle ID, e.g. vehicle.audi.tt.")
    ego["blueprint_filter"] = selected
    result.update(dreyevr=False, launcher_script="", render_mode="onscreen", camera_mode="off",
                  takeover_requests=[], tor_alerts=[], experiment_events={})
    return result


def validate_port(port):
    port = int(port)
    if not 1024 <= port <= 65434:
        raise ValueError("RPC port must be between 1024 and 65434.")
    return port


def find_server(root):
    root = Path(root).expanduser().resolve()
    for base in (root, root / "WindowsNoEditor"):
        exe = base / "CarlaUE4/Binaries/Win64/CarlaUE4-Win64-Shipping.exe"
        if exe.is_file():
            return exe
    raise ValueError("CARLA executable not found. Browse to the extracted stock CARLA folder.")


def runner_arguments(name, port, ego_id):
    return [name, "--port", str(port), "--traffic-manager-port", str(port + 100),
            "--ego-actor-id", str(ego_id), "--auto-start", "--once", "--time-sync-mode", "fixed",
            "--visual-hz", "20", "--render-fps", "20", "--disable-takeover-requests",
            "--no-takeover-audio", "--no-collision-recovery-popup", "--post-run-world", "restore",
            "--no-visual-markers", "--no-tor-spotlight-file", "--no-takeover-require-aoi",
            "--g29-visual-steer-file", "", "--g29-sim-state-file", "", "--g29-python-state-file", ""]


class StopFile:
    def __init__(self, path):
        self.path = Path(path)

    def is_set(self):
        return self.path.exists()


class PlaybackCancelled(Exception):
    pass


def check_cancelled(stop):
    if stop.is_set():
        raise PlaybackCancelled()


class PlaybackController:
    def __init__(self, log, root=ROOT):
        self.root = Path(root)
        self.log = log
        self.server = None
        self.server_port = None
        self.worker = None
        self.run_dir = None
        self.last_result = None
        self.busy = False
        self._stop = threading.Event()

    def start(self, scenario, *, start_server, carla_root, port, ego_id=None, resx=1280, resy=720,
              binding=None):
        if self.busy:
            raise ValueError("A scenario is already running. Stop it before starting another.")
        port = validate_port(port)
        scenario = stock_scenario(scenario)
        exe = find_server(carla_root) if start_server else None
        if ego_id is not None and (int(ego_id) <= 0 or start_server):
            raise ValueError("Existing ego ID requires Run Existing CARLA and a positive actor ID.")
        if self.server and self.server.poll() is None and self.server_port != port:
            raise ValueError("Close the owned CARLA window before changing the RPC port.")
        self.run_dir = self.root / "outputs" / "stock_runs" / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
        self.run_dir.mkdir(parents=True)
        request = dict(scenario=scenario, port=port, ego_id=ego_id,
                       allow_map_load=bool(start_server))
        if binding is not None:
            if start_server or binding.get("actor_id") != ego_id:
                raise ValueError("An explicit ego binding must target the connected server and selected actor.")
            request["binding"] = dict(binding)
        (self.run_dir / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.last_result = None
        self.worker = None
        self._stop.clear()
        self.busy = True
        threading.Thread(target=self._run, args=(exe, port, int(resx), int(resy)), daemon=True).start()

    def _run(self, exe, port, resx, resy):
        try:
            if exe and (self.server is None or self.server.poll() is not None):
                for value in (port, port + 1, port + 100):
                    with socket.socket() as probe:
                        if os.name == "nt":
                            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                        try:
                            probe.bind(("127.0.0.1", value))
                        except OSError as exc:
                            raise ValueError(f"Port {value} is busy. Choose another RPC port or Run Existing CARLA.") from exc
                self.log("Starting stock CARLA in a visible window...")
                with (self.run_dir / "server.log").open("w", encoding="utf-8") as log:
                    self.server = subprocess.Popen(
                        [str(exe), "-windowed", f"-ResX={resx}", f"-ResY={resy}",
                         f"-carla-rpc-port={port}", "-quality-level=Low", "-nosplash"],
                        cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT)
                self.server_port = port
                deadline = time.monotonic() + 180
                while True:
                    if self._stop.is_set():
                        self.last_result = {"status": "CANCELLED"}
                        return
                    if self.server.poll() is not None:
                        raise RuntimeError("CARLA exited during startup. See server.log and CARLA/Saved/Logs.")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("CARLA did not open its RPC port within 180 seconds.")
                        self._stop.wait(0.5)
                if self._stop.wait(3):
                    self.last_result = {"status": "CANCELLED"}
                    return
            if self._stop.is_set():
                self.last_result = {"status": "CANCELLED"}
                return
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            env["PYTHONIOENCODING"] = "utf-8"
            cache = self.root / "runtime" / "carla-cache"
            cache.mkdir(parents=True, exist_ok=True)
            env["CARLA_CACHE_DIR"] = str(cache)
            with (self.run_dir / "playback.log").open("w", encoding="utf-8") as log:
                self.worker = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-u", str(Path(__file__).resolve()),
                     "--request", str(self.run_dir / "request.json")], cwd=self.root, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                for line in self.worker.stdout:
                    log.write(line)
                    log.flush()
                    self.log(line.rstrip())
            rc = self.worker.wait()
            result_path = self.run_dir / "result.json"
            self.last_result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {
                "status": "FAILED", "error": f"Playback worker exited ({rc}) without a result."}
            self.log("STATUS: " + self.last_result["status"])
        except Exception as exc:
            if self.worker is not None and self.worker.poll() is None:
                try:
                    (self.run_dir / "stop.request").touch()
                except OSError:
                    pass
                # Drain even if logging failed: do not orphan a pipe-blocked worker.
                for _ in self.worker.stdout:
                    pass
                self.worker.wait()
            self.last_result = {"status": "FAILED", "error": str(exc)}
            self.log("STATUS: Failed: " + str(exc))
            (self.run_dir / "result.json").write_text(json.dumps(self.last_result, indent=2), encoding="utf-8")
        finally:
            self.busy = False

    def stop(self):
        self._stop.set()
        if self.busy and self.run_dir:
            (self.run_dir / "stop.request").touch()
            self.log("STATUS: Stopping; waiting for actor cleanup...")

    def close_server(self):
        if self.busy:
            raise ValueError("Stop playback and wait for cleanup before closing CARLA.")
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=15)
        self.server = None
        self.server_port = None


def playback_worker(request_path):
    sys.path.insert(0, str(ROOT))
    import carla
    from carla_nl import run_scenario_dreyevr as runner

    directory = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    scenario = stock_scenario(request["scenario"])
    stop = StopFile(directory / "stop.request")
    result = {"status": "FAILED", "scenario": scenario["scenario_name"]}
    world = ego = camera = None
    settings = weather = spectator_transform = None
    borrowed_state = None
    callback_id = None
    world_touched = False
    owned = []
    runner_resources = {"actors": [], "sensors": [], "errors": []}
    positions, images = [], []
    try:
        if stop.is_set():
            result["status"] = "CANCELLED"
            return 0
        client = carla.Client("127.0.0.1", request["port"], 2)
        client.set_timeout(90)
        result["client_version"] = client.get_client_version()
        result["server_version"] = client.get_server_version()
        if result["client_version"] != result["server_version"]:
            raise ValueError("CARLA client/server versions differ; install the matching Python wheel.")
        world = client.get_world()
        check_cancelled(stop)
        if request.get("binding"):
            from carla_nl.stock_addon import validate_binding, validate_versions
            validate_versions(result["client_version"], result["server_version"])
            validate_binding(world, request["binding"])
        map_name = scenario["map_name"]
        if world.get_map().name.rsplit("/", 1)[-1] != map_name:
            if not request["allow_map_load"]:
                raise ValueError("Existing server map differs. Select its map or start a separate CARLA instance.")
            print("Loading " + map_name, flush=True)
            check_cancelled(stop)
            world = client.load_world(map_name)
        settings, weather = world.get_settings(), world.get_weather()
        if settings.synchronous_mode:
            raise ValueError("Existing world is synchronous. Stop its other controller before playback.")
        spectator_transform = world.get_spectator().get_transform()
        if stop.is_set():
            result["status"] = "CANCELLED"
            return 0
        if request.get("ego_id"):
            ego = world.get_actor(int(request["ego_id"]))
            if ego is None or not ego.type_id.startswith("vehicle."):
                raise ValueError("Existing ego ID must identify a live stock vehicle.")
            borrowed_state = (ego.get_transform(), ego.get_control(),
                              ego.get_velocity(), ego.get_angular_velocity())
        else:
            bp = world.get_blueprint_library().find(scenario["vehicles"]["ego"]["blueprint_filter"])
            bp.set_attribute("role_name", "narrate_ego")
            for transform in world.get_map().get_spawn_points():
                check_cancelled(stop)
                ego = world.try_spawn_actor(bp, transform)
                if ego is not None:
                    owned.append(ego)
                    break
            if ego is None:
                raise RuntimeError("No free vehicle spawn point on this map.")
        result.update(ego_id=ego.id, ego_blueprint=ego.type_id, borrowed_ego=bool(borrowed_state))
        check_cancelled(stop)
        world_touched = True
        camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
        camera_bp.set_attribute("image_size_x", "960")
        camera_bp.set_attribute("image_size_y", "540")
        camera_bp.set_attribute("sensor_tick", "0.5")
        camera = world.spawn_actor(camera_bp, carla.Transform(carla.Location(x=-7, z=3),
                                   carla.Rotation(pitch=-15)), attach_to=ego)
        owned.append(camera)

        def capture(image):
            images[:] = [image]

        camera.listen(capture)

        def follow(snapshot):
            try:
                state = snapshot.find(ego.id)
                if state is None:
                    return
                transform = state.get_transform()
                p = transform.location
                if len(positions) < 100000:
                    positions.append([p.x, p.y, p.z])
                view = transform.transform(carla.Location(x=-7, z=3))
                world.get_spectator().set_transform(carla.Transform(view,
                    carla.Rotation(pitch=-15, yaw=transform.rotation.yaw)))
            except RuntimeError:
                pass

        callback_id = world.on_tick(follow)
        options = runner.parse_args(runner_arguments(scenario["scenario_name"], request["port"], ego.id))
        options._stop_event = stop
        options._cleanup_registry = runner_resources
        options._allow_map_load = request["allow_map_load"]
        if request.get("binding"):
            options._expected_world_id = request["binding"]["world_id"]
            # A private TM endpoint avoids changing another client's TM settings.
            with socket.socket() as endpoint:
                endpoint.bind(("127.0.0.1", 0))
                options.traffic_manager_port = endpoint.getsockname()[1]
        check_cancelled(stop)
        rc = runner.run(options, scenario)
        result["return_code"] = rc
        result["collision"] = getattr(options, "_last_run_collision", None)
        result["status"] = ("CANCELLED" if stop.is_set() else "FAILED" if rc else
                            "COLLISION" if result["collision"] else "COMPLETED")
        result["sample_count"] = len(positions)
        result["ego_displacement_m"] = math.dist(positions[0], positions[-1]) if positions else 0
        if images:
            images[-1].save_to_disk(str(directory / "camera.png"))
            channel = bytes(images[-1].raw_data)[0::400]
            result["image_nonblank"] = bool(channel and max(channel) - min(channel) > 40)
    except PlaybackCancelled:
        result["status"] = "CANCELLED"
    except Exception:
        result["error"] = traceback.format_exc()
        print(result["error"], flush=True)
    finally:
        cleanup_errors = list(runner_resources["errors"])

        def cleanup(action):
            try:
                action()
            except Exception as exc:
                cleanup_errors.append(str(exc))

        if callback_id is not None:
            cleanup(lambda: world.remove_on_tick(callback_id))
        def destroy_owned(actor):
            if not actor.is_alive:
                return
            if actor.type_id.startswith("sensor.") and actor.is_listening:
                cleanup(actor.stop)
            if actor.destroy() is False:
                raise RuntimeError("Owned actor {0} destroy() returned False".format(actor.id))

        for actor in reversed(owned + runner_resources["actors"] + runner_resources["sensors"]):
            cleanup(lambda actor=actor: destroy_owned(actor))
        if borrowed_state and ego and world_touched:
            cleanup(lambda: ego.set_simulate_physics(True))
            cleanup(lambda: ego.set_transform(borrowed_state[0]))
            cleanup(lambda: ego.apply_control(borrowed_state[1]))
            cleanup(lambda: ego.set_target_velocity(borrowed_state[2]))
            cleanup(lambda: ego.set_target_angular_velocity(borrowed_state[3]))
        if settings is not None and world_touched:
            cleanup(lambda: world.apply_settings(settings))
            cleanup(lambda: world.set_weather(weather))
        if spectator_transform is not None and world_touched:
            cleanup(lambda: world.get_spectator().set_transform(spectator_transform))
        result["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            result["status"] = "FAILED"
        result["module_origins"] = {name: str(module.__file__) for name, module in list(sys.modules.items())
                                    if name.startswith("carla_nl") and getattr(module, "__file__", None)}
        (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] in ("COMPLETED", "CANCELLED", "COLLISION") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    raise SystemExit(playback_worker(parser.parse_args().request))
