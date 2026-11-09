"""Exercise the reused Add Scenario actions; optionally own a stock CARLA server."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carla-root", type=Path)
    parser.add_argument("--port", type=int, default=4336)
    args = parser.parse_args()
    cache = ROOT / "runtime" / "carla-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["CARLA_CACHE_DIR"] = str(cache)
    from tools.stock_add_scenario import AddonOwner, EXAMPLE, load_workspace
    from carla_nl.stock_prompt_runtime import find_server
    from tkinter import messagebox

    report = {"scope": "LOCAL_REHEARSAL_NOT_A_TARGET_PC", "cases": [], "errors": []}
    server = owner = world = None
    borrowed = []
    root = ROOT / "runtime" / ("rehearsal_" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    report_path = root / "report.json"
    try:
        if args.carla_root:
            import carla
            for port in (args.port, args.port + 1):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            executable = find_server(args.carla_root)
            server = subprocess.Popen([str(executable), "-windowed", "-ResX=1280",
                "-ResY=720", "-quality-level=Low", f"-carla-rpc-port={args.port}", "-nosplash"],
                cwd=executable.parent)
            print("Waiting for owned CARLA RPC port...", flush=True)
            deadline = time.monotonic() + 180
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", args.port), timeout=1):
                        pass
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Owned rehearsal server did not become ready.")
                    time.sleep(1)
            # CARLA clients created before RPC starts can retain a failed connection.
            time.sleep(3)
            client = carla.Client("127.0.0.1", args.port, 2)
            client.set_timeout(120)
            world = client.get_world()
            print("Owned CARLA ready; loading Town04...", flush=True)
            if not world.get_map().name.endswith("/Town04"):
                world = client.load_world("Town04")
            time.sleep(2)

        module = load_workspace(ROOT)
        from tools.preview_add_scenario import relocate_workspace
        relocate_workspace(module, root)
        module.xodr_path_for_map = lambda name: ROOT / "runtime" / "maps" / (str(name) + ".xodr")
        owner = AddonOwner(module, ROOT, args.port)
        def error(title, detail, **kwargs):
            report["errors"].append(str(title) + ": " + str(detail))
        messagebox.showerror = error
        messagebox.askokcancel = lambda *a, **k: True
        owner.root.report_callback_exception = lambda *exc: error("Tk callback", repr(exc[1]))

        def pump_until(predicate, timeout=30):
            end = time.monotonic() + timeout
            while not predicate():
                owner.root.update()
                if report["errors"]:
                    raise AssertionError(report["errors"])
                if time.monotonic() > end:
                    raise TimeoutError("GUI action timed out")
                time.sleep(0.05)
            owner.root.update()

        dialog = owner.open_dialog()
        dialog.name_var.set("addon_rehearsal")
        dialog._replace_prompt_text(EXAMPLE)
        dialog.blueprint_var.set("vehicle.audi.tt")
        assert dialog.preview()
        owner.root.update()
        assert dialog.converted[0].scenario["vehicles"]["ego"]["blueprint_filter"] == "vehicle.audi.tt"
        assert dialog.converted[0].scenario["dreyevr"] is False
        report["tabs"] = [dialog.tabs.tab(tab, "text") for tab in dialog.tabs.tabs()]
        assert report["tabs"] == ["Review", "JSON", "Design", "History"]
        report["design_items"] = len(dialog.design_preview.scene_canvas.find_all())
        assert report["design_items"] > 5
        dialog.save()
        owner.root.update()
        assert len(owner.scenarios) == 1
        dialog = owner.open_dialog(owner.scenarios[0])
        assert dialog.prompt_text.get("1.0", "end").strip() == EXAMPLE.strip()
        assert dialog.blueprint_var.get() == "vehicle.audi.tt"
        assert dialog.preview()
        report["save_reopen"] = True

        if world is not None:
            baseline_ids = {actor.id for actor in world.get_actors()}
            settings = world.get_settings()
            weather = str(world.get_weather())
            spectator = world.get_spectator().get_transform()
            for blueprint, role in (("vehicle.tesla.model3", "my_lab_ego"), ("vehicle.audi.tt", "hero")):
                bp = world.get_blueprint_library().find(blueprint)
                bp.set_attribute("role_name", role)
                for transform in reversed(world.get_map().get_spawn_points()):
                    actor = world.try_spawn_actor(bp, transform)
                    if actor:
                        actor.apply_control(carla.VehicleControl(hand_brake=True))
                        borrowed.append(actor)
                        break
                else:
                    raise RuntimeError("Cannot spawn rehearsal actor")
            time.sleep(1)
            original_poses = [actor.get_transform() for actor in borrowed]
            owner.refresh_connection()
            pump_until(lambda: not owner.connecting)
            assert owner.connection["map_name"] == "Town04"
            picker = module.MapSpawnPicker(dialog, "Town04")
            owner.root.update()
            report["map_canvas_items"] = len(picker.canvas.find_all())
            assert report["map_canvas_items"] > 50
            picker.top.destroy()
            dialog.top.grab_set()

            for index in range(4):
                dialog.ego_var.set("New vehicle for each run")
                dialog.blueprint_var.set("vehicle.audi.tt")
                if index in (1, 2):
                    label = next(label for label, item in dialog.actors.items()
                                 if item["actor_id"] == borrowed[0].id)
                    dialog.ego_var.set(label)
                dialog.ego_changed()
                dialog._replace_prompt_text(EXAMPLE.replace("duration=12s", "duration=60s")
                                            if index == 2 else EXAMPLE)
                dialog.name_var.set("addon_case_" + str(index + 1))
                dialog.run_button.invoke()
                assert owner.controller.busy, "Run button did not start playback"
                print("RUN CASE " + str(index + 1), flush=True)
                if index == 2:
                    log = owner.controller.run_dir / "playback.log"
                    pump_until(lambda: (not owner.controller.busy or
                        (log.exists() and "event=SCENARIO_PLAY_START" in log.read_text(encoding="utf-8"))), 120)
                    start = time.monotonic()
                    pump_until(lambda: time.monotonic() - start > 1.5)
                    dialog.stop_button.invoke()
                pump_until(lambda: not owner.controller.busy, 180)
                # Let the GUI consume completion and re-enable Run before replay.
                pump_until(lambda: not owner.was_running)
                result = owner.controller.last_result
                report["cases"].append(result)
                expected = "CANCELLED" if index == 2 else "COMPLETED"
                assert result["status"] == expected, result
                assert not result["cleanup_errors"], result
                assert result["image_nonblank"], result
                if index in (1, 2):
                    assert result["ego_id"] == borrowed[0].id
                    assert result["ego_blueprint"] == "vehicle.tesla.model3"
                assert all(world.get_actor(actor.id) is not None for actor in borrowed)
                assert all(actor.get_location().distance(pose.location) < 0.5
                           for actor, pose in zip(borrowed, original_poses))
                current_ids = {actor.id for actor in world.get_actors()}
                assert current_ids == baseline_ids | {actor.id for actor in borrowed}, current_ids - baseline_ids
                assert world.get_settings().synchronous_mode == settings.synchronous_mode
                assert world.get_settings().fixed_delta_seconds == settings.fixed_delta_seconds
                assert str(world.get_weather()) == weather
                assert world.get_spectator().get_location().distance(spectator.location) < 0.01
            report["borrowed_ego_and_other_actor_preserved"] = True
            report["world_settings_weather_spectator_restored"] = True
            from carla_nl import stock_prompt_runtime, run_scenario_dreyevr as runner
            fault_dir = root / "fault_injection"
            fault_dir.mkdir()
            request = json.loads((owner.controller.run_dir / "request.json").read_text(encoding="utf-8"))
            request["ego_id"] = borrowed[0].id
            request["binding"] = next(a for a in owner.connection["actors"] if a["actor_id"] == borrowed[0].id)
            request_path = fault_dir / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            original_warning = runner._emit_scenario_design_warnings
            def inject_failure(*a, **kw):
                raise RuntimeError("Intentional rehearsal error after collision sensor spawn")
            runner._emit_scenario_design_warnings = inject_failure
            try:
                assert stock_prompt_runtime.playback_worker(request_path) == 1
            finally:
                runner._emit_scenario_design_warnings = original_warning
            fault = json.loads((fault_dir / "result.json").read_text(encoding="utf-8"))
            assert fault["status"] == "FAILED" and "Intentional rehearsal" in fault["error"]
            assert not fault["cleanup_errors"], fault
            assert {actor.id for actor in world.get_actors()} == baseline_ids | {actor.id for actor in borrowed}
            assert world.get_settings().synchronous_mode == settings.synchronous_mode
            assert borrowed[0].get_location().distance(original_poses[0].location) < 0.5
            report["exception_path_collision_sensor_cleanup"] = True
            original_placement = runner._place_physics_support_at_transform
            runner._place_physics_support_at_transform = inject_failure
            try:
                assert stock_prompt_runtime.playback_worker(request_path) == 1
            finally:
                runner._place_physics_support_at_transform = original_placement
            fault = json.loads((fault_dir / "result.json").read_text(encoding="utf-8"))
            assert fault["status"] == "FAILED" and "Intentional rehearsal" in fault["error"]
            assert not fault["cleanup_errors"], fault
            assert {actor.id for actor in world.get_actors()} == baseline_ids | {actor.id for actor in borrowed}
            assert world.get_settings().synchronous_mode == settings.synchronous_mode
            assert borrowed[0].get_location().distance(original_poses[0].location) < 0.5
            report["exception_path_support_vehicle_cleanup"] = True
        report["module_origins"] = {name: str(module.__file__) for name, module in list(sys.modules.items())
            if (name.startswith(("carla_nl", "tools")) or name in ("reference_launcher", "simple_hf_scenario_gui"))
            and getattr(module, "__file__", None)}
        assert all(Path(path).resolve().is_relative_to(ROOT) for path in report["module_origins"].values())
        manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
        assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                   for name, digest in manifest["files"].items())
        assert not report["errors"], report["errors"]
        report["passed"] = True
    except Exception:
        report["errors"].append(traceback.format_exc())
        report["passed"] = False
        print(report["errors"][-1], flush=True)
    finally:
        if owner is not None:
            owner.controller.stop()
            while owner.controller.busy:
                owner.root.update()
                time.sleep(0.1)
            owner.root.destroy()
        for actor in borrowed:
            try:
                if actor.is_alive:
                    actor.destroy()
            except RuntimeError:
                pass
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=20)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report.get("passed"), "report": str(report_path)}), flush=True)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
