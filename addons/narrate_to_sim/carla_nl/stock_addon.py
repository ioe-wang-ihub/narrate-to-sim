"""Stock-CARLA authoring contracts and read-only connection discovery."""

from pathlib import Path

from carla_nl.stock_prompt_runtime import stock_scenario, validate_port


def validate_versions(client, server):
    if client != "0.9.16" or server != "0.9.16":
        raise ValueError(f"This add-on requires CARLA 0.9.16 on both sides; client={client}, server={server}.")


def convert_for_stock(scenario, blueprint, port):
    unsupported = [key for key in ("takeover_requests", "tor_alerts", "audio_events", "cue_events",
                                   "audio_cues", "standalone_cues")
                   if scenario.get(key)]
    events = scenario.get("experiment_events") or {}
    unsupported.extend("experiment_events." + key for key, value in events.items()
                       if key != "standard_markers_enabled" and value)
    if unsupported:
        raise ValueError("Experiment alerts/audio/markers are not supported in this traffic-only add-on: "
                         + ", ".join(unsupported) + ". Remove these commands before Analyze/Run.")
    result = stock_scenario(scenario, blueprint)
    result["rpc_port"] = validate_port(port)
    return result


def validate_binding(world, binding):
    if world.id != binding["world_id"]:
        raise ValueError("The CARLA world changed. Refresh and select the ego again.")
    if binding.get("actor_id") is None:
        return None
    actor = world.get_actor(int(binding["actor_id"]))
    if (actor is None or actor.type_id != binding["blueprint"]
            or actor.attributes.get("role_name", "") != binding["role_name"]):
        raise ValueError("The selected ego no longer matches. Refresh and select a live vehicle.")
    return actor


def inspect_server(port, root):
    import carla

    port = validate_port(port)
    client = carla.Client("127.0.0.1", port, 2)
    client.set_timeout(12)
    validate_versions(client.get_client_version(), client.get_server_version())
    world = client.get_world()
    carla_map = world.get_map()
    map_name = carla_map.name.rsplit("/", 1)[-1]
    directory = Path(root) / "runtime" / "maps"
    directory.mkdir(parents=True, exist_ok=True)
    # Only server-provided OpenDRIVE is cached; never search the old lab install.
    (directory / (map_name + ".xodr")).write_text(carla_map.to_opendrive(), encoding="utf-8")
    actors = [dict(world_id=world.id, actor_id=actor.id, blueprint=actor.type_id,
                   role_name=actor.attributes.get("role_name", ""))
              for actor in world.get_actors().filter("vehicle.*")]
    blueprints = sorted(bp.id for bp in world.get_blueprint_library().filter("vehicle.*")
                        if bp.has_attribute("number_of_wheels")
                        and int(bp.get_attribute("number_of_wheels")) == 4)
    return dict(port=port, map_name=map_name, world_id=world.id, actors=actors,
                blueprints=blueprints, synchronous=world.get_settings().synchronous_mode)
