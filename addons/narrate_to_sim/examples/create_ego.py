"""Create an ordinary stock ego; Ctrl+C removes only this script's vehicle."""

import argparse
import time

import carla


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--blueprint", default="vehicle.audi.tt")
    parser.add_argument("--role-name", default="narrate_ego")
    args = parser.parse_args()
    client = carla.Client("127.0.0.1", args.port, 2)
    client.set_timeout(20)
    if client.get_client_version() != "0.9.16" or client.get_server_version() != "0.9.16":
        raise RuntimeError("Use CARLA 0.9.16 for both the simulator and Python API.")
    world = client.get_world()
    if world.get_map().name.rsplit("/", 1)[-1] != "Town04":
        raise RuntimeError("Load Town04 on your fresh CARLA server first; see INTRODUCTION.md.")
    if world.get_settings().synchronous_mode:
        raise RuntimeError("Stop other synchronous controllers before creating the tutorial ego.")
    blueprint = world.get_blueprint_library().find(args.blueprint)
    if not blueprint.id.startswith("vehicle."):
        raise ValueError("Choose a stock vehicle blueprint.")
    blueprint.set_attribute("role_name", args.role_name)
    ego = None
    try:
        for transform in reversed(world.get_map().get_spawn_points()):
            ego = world.try_spawn_actor(blueprint, transform)
            if ego:
                break
        if ego is None:
            raise RuntimeError("No free vehicle spawn point.")
        ego.set_autopilot(False)
        ego.apply_control(carla.VehicleControl(hand_brake=True))
        print(f"Ego ID: {ego.id} | {ego.type_id} | role_name={args.role_name}", flush=True)
        print("Keep this terminal open. Refresh CARLA in the GUI and select this ID.", flush=True)
        print("After scenario playback has stopped, press Ctrl+C here to remove this ego.", flush=True)
        while ego.is_alive:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if ego is not None and ego.is_alive:
            ego.destroy()


if __name__ == "__main__":
    main()
