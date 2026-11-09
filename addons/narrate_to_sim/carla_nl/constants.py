"""Canonical CARLA constants — copied verbatim from
hf_carla_scenario.py so our v1 schema stays compatible with the friend's runner.
"""

from __future__ import annotations

# Source: hf_carla_scenario.py:132-145 (DEFAULT_MAPS)
KNOWN_MAPS: tuple[str, ...] = (
    "DualHighway_Opt",
    "Town01",
    "Town02",
    "Town03",
    "Town04",
    "Town04_Opt",
    "Town05",
    "Town06",
    "Town07",
    "Town12",
    "Town13",
    "Town15",
    "Town10HD_Opt",
)

# Short descriptions to help Claude pick a map for an NL prompt.
MAP_DESCRIPTIONS: dict[str, str] = {
    "DualHighway_Opt": "Straight dual highway test map for controlled lane-change/TTC experiments",
    "Town01": "Small town with a single highway loop and basic intersections",
    "Town02": "Smaller town with a residential feel",
    "Town03": "Larger urban map with a roundabout and tunnels",
    "Town04": "Multi-lane highway loop + small town — preferred for car-following",
    "Town04_Opt": "Optimized variant of Town04 for lower-end GPUs",
    "Town05": "Squared grid map with multi-lane roads",
    "Town06": "Long highways with many lanes and exits",
    "Town07": "Rural environment with narrow roads",
    "Town12": "Large mixed-environment map",
    "Town13": "Long multi-lane highway — straight highway spawn {road_id:1393, lane_id:-2, s:1700.0}",
    "Town15": "Mixed urban/rural large map",
    "Town10HD_Opt": "Small high-detail downtown environment",
}

# Source: hf_carla_scenario.py:48 (SUPPORT_ROLE_ORDER)
SUPPORT_ROLE_ORDER: tuple[str, ...] = tuple(
    f"vehicle_{i}" for i in range(1, 7)
)
# Source: hf_carla_scenario.py:49 (ROLE_ORDER)
ROLE_ORDER: tuple[str, ...] = ("ego",) + SUPPORT_ROLE_ORDER

# Source: hf_carla_scenario.py:53 (LANE_NAMES)
LANE_NAMES: tuple[str, ...] = ("Left", "Middle", "Right")

# Source: hf_carla_scenario.py:147-155 (DEFAULT_BLUEPRINT_FILTERS)
DEFAULT_BLUEPRINT_FILTERS: dict[str, str] = {
    "ego": "vehicle.lincoln.mkz_2020",
    "vehicle_1": "vehicle.audi.etron",
    "vehicle_2": "vehicle.dodge.charger_2020",
    "vehicle_3": "vehicle.mercedes.coupe_2020",
    "vehicle_4": "vehicle.tesla.model3",
    "vehicle_5": "vehicle.nissan.patrol_2021",
    "vehicle_6": "vehicle.mini.cooper_s_2021",
}

# Short prompt-friendly aliases for locally available CARLA/DReyeVR vehicle
# blueprints. The 0.9.13 DReyeVR content tree includes these vehicle assets.
VEHICLE_BLUEPRINT_ALIASES: dict[str, str] = {
    # CarlaCola exists in the asset tree, but this DReyeVR build can crash UE4
    # with a SkeletalMesh assertion when it is spawned. Use Sprinter as the
    # stable cargo vehicle for prompt-side "truck" wording.
    "truck": "vehicle.mercedes.sprinter",
    "trunk": "vehicle.mercedes.sprinter",  # common typo in quick prompts
    "cargo": "vehicle.mercedes.sprinter",
    "cargo_truck": "vehicle.mercedes.sprinter",
    "cola_truck": "vehicle.mercedes.sprinter",
    "carlacola": "vehicle.mercedes.sprinter",
    "van": "vehicle.mercedes.sprinter",
    "sprinter": "vehicle.mercedes.sprinter",
    "delivery_van": "vehicle.mercedes.sprinter",
    "suv": "vehicle.nissan.patrol_2021",
    "patrol": "vehicle.nissan.patrol_2021",
    "jeep": "vehicle.jeep.wrangler_rubicon",
    "offroad": "vehicle.jeep.wrangler_rubicon",
    "pickup": "vehicle.tesla.cybertruck",
    "cybertruck": "vehicle.tesla.cybertruck",
    "firetruck": "vehicle.carlamotors.firetruck",
    "fire_truck": "vehicle.carlamotors.firetruck",
    "ambulance": "vehicle.ford.ambulance",
    "sedan": "vehicle.lincoln.mkz_2020",
    "car": "vehicle.lincoln.mkz_2020",
    "audi": "vehicle.audi.etron",
    "etron": "vehicle.audi.etron",
    "charger": "vehicle.dodge.charger_2020",
    "coupe": "vehicle.mercedes.coupe_2020",
    "tesla": "vehicle.tesla.model3",
    "model3": "vehicle.tesla.model3",
    "mini": "vehicle.mini.cooper_s_2021",
}

# Source: hf_carla_scenario.py:173-189 (FALLBACK_WEATHER_PRESETS)
WEATHER_PRESETS: tuple[str, ...] = (
    "Default",
    "ClearNoon",
    "CloudyNoon",
    "WetNoon",
    "WetCloudyNoon",
    "SoftRainNoon",
    "MidRainyNoon",
    "HardRainNoon",
    "ClearSunset",
    "CloudySunset",
    "WetSunset",
    "WetCloudySunset",
    "SoftRainSunset",
    "MidRainSunset",
    "HardRainSunset",
)

WEATHER_DESCRIPTIONS: dict[str, str] = {
    "Default": "CARLA default (typically Clear)",
    "ClearNoon": "Clear sky, bright sunlight overhead",
    "CloudyNoon": "Overcast daytime, no precipitation",
    "WetNoon": "Wet roads, no active rain, daytime",
    "WetCloudyNoon": "Overcast + wet roads, no active rain",
    "SoftRainNoon": "Light rain falling, daytime",
    "MidRainyNoon": "Moderate rain, daytime",
    "HardRainNoon": "Heavy rain, daytime",
    "ClearSunset": "Clear sky, low golden sun",
    "CloudySunset": "Overcast at sunset (low light)",
    "WetSunset": "Wet roads at sunset",
    "WetCloudySunset": "Overcast + wet roads at sunset",
    "SoftRainSunset": "Light rain at sunset",
    "MidRainSunset": "Moderate rain at sunset",
    "HardRainSunset": "Heavy rain at sunset",
}

OBSTACLE_KINDS: tuple[str, ...] = (
    "Barrier",
    "Cone",
    "Debris",
    "Disabled Vehicle",
)

CAMERA_MODES: tuple[str, ...] = ("off", "driver", "chase", "top", "bird")

# Town13 has a known-good straight highway spawn that's used by generated scenarios.
TOWN13_DEFAULT_SPAWN_XODR: dict[str, float] = {
    "road_id": 1393,
    "lane_id": -2,
    "s": 1700.0,
}

DEFAULT_FIXED_DELTA_SECONDS: float = 1.0 / 60.0
DEFAULT_TOTAL_TIME_S: float = 24.0
DEFAULT_EGO_SPEED_KMH: float = 60.0
DEFAULT_WEATHER_PRESET: str = "CloudyNoon"
DEFAULT_MAP: str = "Town04"
DEFAULT_CAMERA_MODE: str = "off"
DEFAULT_LANE_CHANGE_DURATION_S: float = 3.0
DEFAULT_STOP_DURATION_S: float = 2.0
