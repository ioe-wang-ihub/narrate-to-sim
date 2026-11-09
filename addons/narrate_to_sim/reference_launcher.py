from __future__ import print_function

import json
import ast
import csv
import math
import os
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from carla_nl.tor_adaptive import (
    ADAPTIVE_FIELD_KEYS,
    RETURN_TO_AUTO_TEXT,
    MANUAL_REQUIREMENTS,
    STAGE1_REQUIREMENTS,
    STAGE2_REQUIREMENTS,
    adaptive_fields_from_mapping,
    format_tor_adaptive_prompt,
    parse_tor_adaptive_prompt,
    TOR_ADAPTIVE_PROMPT_EXAMPLE,
)
from carla_nl.prompt_completion import PROMPT_COMPLETIONS as SCANNER_PROMPT_COMPLETIONS
from carla_nl.prompt_language import NUMBERED_VEHICLE_RE, SECONDS_UNIT
from carla_nl.prompt_scene_entities import PEDESTRIAN_NAME_RE, OBSTACLE_NAME_RE
from carla_nl.cue_language import (
    BEEP as PROMPT_CUE_BEEP_RE, SPEECH as PROMPT_CUE_SPEECH_RE,
    CIRCLE as PROMPT_CUE_CIRCLE_RE,
)
from carla_nl.process_cleanup import terminate_process_tree
from carla_nl.takeover_alerts import (
    build_alert_snippet, ALERT as PROMPT_ALERT_RE, WARNING_REQUEST as PROMPT_WARNING_RE,
    VISUAL as PROMPT_VISUAL_RE, AUDIO as PROMPT_AUDIO_RE,
    BEEP as PROMPT_BEEP_RE, SPEECH as PROMPT_SPEECH_RE,
)
from carla_nl.prompt_english import NUMBER_WORDS


PROJECT_ROOT = Path(__file__).resolve().parent / "unbundled" / 'Carla_Prepack_17fbcf2'
FRIEND_ROOT = Path(__file__).resolve().parent / "unbundled" / 'Carla_scnario_Builder_Source_Portable_20260514-132535'
PY37 = FRIEND_ROOT / "carla-venv-0.9.13-py37" / "Scripts" / "python.exe"
START_CARLA_PS1 = PROJECT_ROOT / "start_dreyevr_ndisplay.ps1"
PROMPT_GUI_PS1 = PROJECT_ROOT / "start_prompt_scenario_gui.ps1"
RUN_SCENARIO_PY = PROJECT_ROOT / "run_scenario.py"
OUTPUT_SCENARIOS = PROJECT_ROOT / "outputs" / "scenarios"
SCRIPTED_SCENARIOS = PROJECT_ROOT / "scenarios"
AOI_DIR = Path(__file__).resolve().parent / "unbundled" / 'aoi'
AOI_PYTHON = Path(__file__).resolve().parent / "unbundled" / 'python.exe'
AOI_SCRIPT = PROJECT_ROOT / "extensions" / "level3_tor_demo" / "aoi_emotibit_monitor.py"
MANUAL_TOR_MARKER_SCRIPT = PROJECT_ROOT / "carla_nl" / "manual_tor_marker.py"
CARLA_LSL_DATA_READER_MODULE = "carla_nl.lsl_data_reader"
CARLA_LSL_DATA_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "lsl_data"
LEVEL3_SESSION_CONFIG = PROJECT_ROOT / "extensions" / "level3_tor_demo" / "experiment_session.json"
LEVEL3_SESSION_MODULE = "extensions.level3_tor_demo.experiment_session_runner"
SURVEY_GENERATOR_ROOT = PROJECT_ROOT / "SurveyGenerator-Windows-0506"
SURVEY_GENERATOR_RUN_PS1 = SURVEY_GENERATOR_ROOT / "run.ps1"
LAB_RECORDER_EXE = Path(__file__).resolve().parent / "unbundled" / 'LabRecorder.exe'
LAB_RECORDER_CLI_EXE = Path(__file__).resolve().parent / "unbundled" / 'LabRecorderCLI.exe'
EMOTIBIT_OSCILLOSCOPE_EXE = Path(__file__).resolve().parent / "unbundled" / 'EmotiBitOscilloscope.exe'
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "launcher_logs"
LOG_PATH = LOG_DIR / "carla_scenario_launcher_yjc.log"
TEST_LOG_DIR = PROJECT_ROOT / "test"
SCENARIO_GROUPS_PATH = RUNTIME_DIR / "scenario_groups.json"
SCENARIO_PROMPTS_PATH = RUNTIME_DIR / "scenario_prompts.json"
SCENARIO_PROMPT_HISTORY_PATH = RUNTIME_DIR / "scenario_prompt_history.json"
PLAYLIST_LIBRARY_PATH = RUNTIME_DIR / "level3_session_playlists.json"
USER_ROOT = PROJECT_ROOT.parent
_CLEANUP_SCENE_LOCK = threading.Lock()


def make_test_log_path(label):
    TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(label or "scenario"))
    safe = safe.strip("_") or "scenario"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return TEST_LOG_DIR / "{}_{}.log".format(stamp, safe[:80])


def write_test_log_line(handle, text):
    if handle is None:
        return
    try:
        handle.write(str(text).rstrip() + "\n")
        handle.flush()
    except Exception:
        pass

HOST = "127.0.0.1"
RPC_PORT = 2013
DEFAULT_LSL_STREAM = "CarlaScenarioMarkers"
DEFAULT_CARLA_TELEMETRY_STREAM = "CarlaEgoTelemetry"
DEFAULT_LSL_BRIDGE_HOST = "127.0.0.1"
DEFAULT_LSL_BRIDGE_PORT = 17364
DEFAULT_AOI_CORNER_IDS = "left:0,1,2,3;center:4,5,6,7;right:8,9,10,11"
DEFAULT_AOI_GRID = "middle_left"
DEFAULT_TOR_ALERT_DELAY_S = "0.5"
DEFAULT_AOI_FEEDBACK_PORT = "17365"
DEFAULT_NEON_IP = ""  # Empty means discover Neon on the local network.
DEFAULT_CARLA_MAP = "Town04_Opt"
TOR_ACTION_OPTIONS = (
    "response_action",
    "accelerate",
    "brake",
    "lane_change_left",
    "lane_change_right",
    "keep_lane",
)
TOR_STAGE2_DIRECTION_OPTIONS = (
    "front",
    "left_front",
    "left_rear",
    "right_front",
    "right_rear",
)
# Control display: 2560x1600; three 1920x1080 simulator displays to its right.
NDISPLAY_CFG = PROJECT_ROOT / "ndisplay" / "3screen.cfg"
NDISPLAY_WIN_X = 2560
NDISPLAY_WIN_Y = 0
NDISPLAY_RES_X = 5760
NDISPLAY_RES_Y = 1080
DISPLAY_MODES = ("Three screens", "Single screen")
DISPLAY_SETTINGS_PATH = RUNTIME_DIR / "display_settings.json"
CARLA_MAPS = ("Town04_Opt", "DualHighway_Opt", "Town04", "Town13")


def lsl_marker_value(value):
    text = str(value)
    return text.replace(";", "_").replace(" ", "_").replace("\n", "_").replace("\r", "_")


def format_lsl_marker(event, **fields):
    parts = [str(event)]
    for key in sorted(fields.keys()):
        value = fields[key]
        if value is None:
            continue
        if isinstance(value, float):
            value = "{0:.4f}".format(value)
        parts.append("{0}={1}".format(key, lsl_marker_value(value)))
    return ";".join(parts)


def send_lsl_udp_marker(stream_name, event, **fields):
    marker = format_lsl_marker(event, **fields)
    payload = json.dumps(
        {
            "marker": marker,
            "stream_name": stream_name or DEFAULT_LSL_STREAM,
            "scenario": "carla_gui",
            "sent_monotonic": time.monotonic(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (DEFAULT_LSL_BRIDGE_HOST, DEFAULT_LSL_BRIDGE_PORT))
    finally:
        sock.close()

# VS Code One Dark Pro surfaces, with brighter text for authoring guidance.
INKSPACE_COLORS = {
    "canvas": "#21252b",
    "surface": "#282c34",
    "surface_alt": "#2c313a",
    "surface_subtle": "#333842",
    "surface_selected": "#404858",
    "surface_hover": "#343b46",
    "field": "#282c34",
    "border": "#3e4452",
    "border_strong": "#596273",
    "text": "#dcdfe4",
    "text_muted": "#abb2bf",
    "text_subtle": "#9aa3b2",
    "brand": "#61afef",
    "brand_hover": "#9cc8f6",
    "brand_pressed": "#4f99d5",
    "brand_tint": "#303c50",
    "brand_border": "#528bff",
    "danger": "#f87171",
    "danger_soft": "#351d2a",
    "warning_bg": "#3b3217",
    "warning_text": "#f2c94c",
    "success": "#5eead4",
    "on_accent": "#1b1f26",
}


def configure_inkspace_style(root):
    from tkinter import ttk

    c = INKSPACE_COLORS
    root.configure(bg=c["canvas"])
    root.option_add("*Font", "{Segoe UI} 10")
    root.option_add("*Listbox.Font", "{Segoe UI} 10")
    root.option_add("*Listbox.Background", c["field"])
    root.option_add("*Listbox.Foreground", c["text"])
    root.option_add("*Listbox.selectBackground", c["brand"])
    root.option_add("*Listbox.selectForeground", c["on_accent"])
    root.option_add("*TCombobox*Listbox.background", c["field"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["brand"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
    root.option_add("*Text.Background", c["field"])
    root.option_add("*Text.Foreground", c["text"])
    root.option_add("*Text.insertBackground", c["text"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        ".",
        background=c["canvas"],
        foreground=c["text"],
        fieldbackground=c["field"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        troughcolor=c["surface_subtle"],
        focuscolor=c["brand_border"],
        font=("Segoe UI", 10),
    )
    style.configure("TFrame", background=c["canvas"])
    style.configure("Surface.TFrame", background=c["surface"])
    style.configure("TLabel", background=c["canvas"], foreground=c["text"])
    style.configure("Title.TLabel", background=c["canvas"], foreground=c["text"], font=("Segoe UI", 15, "bold"))
    style.configure("Section.TLabel", background=c["canvas"], foreground=c["text"], font=("Segoe UI", 11, "bold"))
    style.configure("Muted.TLabel", background=c["canvas"], foreground=c["text_muted"])
    style.configure(
        "TLabelframe",
        background=c["surface"],
        bordercolor=c["border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=c["surface"],
        foreground=c["text_muted"],
        font=("Segoe UI", 9, "bold"),
    )
    style.configure(
        "TButton",
        background=c["surface_alt"],
        foreground=c["text"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        padding=(10, 6),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", c["surface_subtle"]), ("active", c["surface_hover"]), ("disabled", c["surface_subtle"])],
        foreground=[("pressed", c["brand_hover"]), ("active", c["brand_hover"]), ("disabled", c["text_subtle"])],
        bordercolor=[("focus", c["brand_border"]), ("active", c["border_strong"])],
    )
    style.configure(
        "Accent.TButton",
        background=c["brand"],
        foreground=c["on_accent"],
        bordercolor=c["brand"],
        padding=(10, 6),
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", c["brand_pressed"]), ("active", c["brand_hover"]), ("disabled", c["surface_subtle"])],
        foreground=[("disabled", c["text_subtle"])],
    )
    style.configure("TCheckbutton", background=c["canvas"], foreground=c["text"], padding=(2, 4))
    style.map(
        "TCheckbutton",
        background=[("active", c["canvas"]), ("pressed", c["canvas"])],
        foreground=[("active", c["brand_hover"]), ("disabled", c["text_subtle"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=c["field"],
        foreground=c["text"],
        insertcolor=c["text"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        padding=(6, 5),
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", c["surface_subtle"]), ("readonly", c["field"])],
        foreground=[("disabled", c["text_subtle"])],
        bordercolor=[("focus", c["brand_border"])],
    )
    style.configure(
        "TCombobox",
        fieldbackground=c["field"],
        background=c["surface_alt"],
        foreground=c["text"],
        arrowcolor=c["text_muted"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        padding=(6, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", c["field"]), ("disabled", c["surface_subtle"])],
        foreground=[("readonly", c["text"]), ("disabled", c["text_subtle"])],
        background=[("active", c["surface_hover"])],
        bordercolor=[("focus", c["brand_border"])],
    )
    style.configure("TPanedwindow", background=c["canvas"])
    style.configure("TNotebook", background=c["canvas"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["surface_alt"], foreground=c["text_muted"], padding=(12, 7))
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["surface"]), ("active", c["surface_hover"])],
        foreground=[("selected", c["text"]), ("active", c["brand_hover"])],
    )
    style.configure(
        "Treeview",
        background=c["field"],
        fieldbackground=c["field"],
        foreground=c["text"],
        bordercolor=c["border"],
        rowheight=24,
    )
    style.configure(
        "Treeview.Heading",
        background=c["surface_alt"],
        foreground=c["text_muted"],
        relief="flat",
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", c["surface_selected"])],
        foreground=[("selected", c["text"])],
    )
    return style


def configure_inkspace_window(window):
    try:
        window.configure(bg=INKSPACE_COLORS["canvas"])
    except Exception:
        pass


def enable_window_maximize(window, start_maximized=True):
    """Make a Toplevel user-maximizable with an F11 fullscreen toggle.

    Tk ``transient()`` windows lose their maximize/minimize boxes on Windows, so
    big editor windows should NOT be marked transient. This helper makes the
    window resizable, binds F11 to toggle true fullscreen (F11 again to exit),
    and (optionally) opens it maximized so its content is fully visible.
    """
    try:
        window.resizable(True, True)
    except Exception:
        pass

    def _toggle_fullscreen(_event=None):
        try:
            window.attributes("-fullscreen", not bool(window.attributes("-fullscreen")))
        except Exception:
            pass
        return "break"

    try:
        window.bind("<F11>", _toggle_fullscreen)
    except Exception:
        pass
    if start_maximized:
        # Defer until the window is mapped; 'zoomed' before mapping is ignored.
        try:
            window.after(10, lambda: window.state("zoomed"))
        except Exception:
            pass


def style_inkspace_listbox(widget):
    c = INKSPACE_COLORS
    widget.configure(
        bg=c["field"],
        fg=c["text"],
        selectbackground=c["brand"],
        selectforeground=c["on_accent"],
        activestyle="none",
        relief="flat",
        highlightthickness=1,
        highlightbackground=c["border"],
        highlightcolor=c["brand_border"],
        bd=0,
        font=("Segoe UI", 10),
    )


def style_inkspace_text(widget, monospace=True):
    c = INKSPACE_COLORS
    widget.configure(
        bg=c["field"],
        fg=c["text"],
        insertbackground=c["text"],
        selectbackground=c["surface_selected"],
        selectforeground=c["text"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=c["border"],
        highlightcolor=c["brand_border"],
        bd=0,
        padx=8,
        pady=8,
        font=("Consolas", 10) if monospace else ("Segoe UI", 10),
    )

# Large maps (Town11/12/13/15) are streamed from per-tile sublevels. Booting one
# as the UE4 *startup* map does not stream the ground tiles in this DReyeVR
# 0.9.13 build — you get a half-sky / half-blank screen. They must instead be
# entered at runtime via client.load_world() after CARLA is up on a normal map.
# Mirrors run_scenario_dreyevr._map_allows_large_elevation.
LARGE_MAP_TOKENS = ("Town11", "Town12", "Town13", "Town15", "TownBig")

QUALITY_PRESETS = (
    ("TunedHigh", "Tuned high (recommended)"),
    ("Balanced", "Balanced"),
    ("Performance", "Performance"),
    ("RawEpic", "Raw Epic / no perf preset"),
)
QUALITY_PRESET_LABELS = [label for _key, label in QUALITY_PRESETS]
QUALITY_PRESET_BY_LABEL = {label: key for key, label in QUALITY_PRESETS}
QUALITY_PRESET_ARGS = {
    "TunedHigh": ["-Quality", "Low", "-VisualPreset", "TunedHigh"],
    "Balanced": ["-Quality", "Epic", "-VisualPreset", "Balanced"],
    "Performance": ["-Quality", "Low", "-VisualPreset", "Performance"],
    "RawEpic": ["-Quality", "Epic", "-NoPerfFlags"],
}
DEFAULT_SCENARIO_GROUP = "Ungrouped"
SCENARIO_SORT_OPTIONS = (
    "Modified newest",
    "Modified oldest",
    "Name A-Z",
    "Name Z-A",
    "Duration shortest",
    "Duration longest",
)
QUEEN_DENSITY_PRESETS = (
    {
        "key": "sparse",
        "label": "Sparse (4 vehicles)",
        "count": 4,
        "spacing_m": 100.0,
        "safe_gap_m": 75.0,
    },
    {
        "key": "moderate",
        "label": "Slightly crowded (7 vehicles)",
        "count": 7,
        "spacing_m": 80.0,
        "safe_gap_m": 60.0,
    },
    {
        "key": "dense",
        "label": "Very crowded (11 vehicles)",
        "count": 11,
        "spacing_m": 60.0,
        "safe_gap_m": 50.0,
    },
)
QUEEN_DENSITY_LABELS = tuple(preset["label"] for preset in QUEEN_DENSITY_PRESETS)
DEFAULT_QUEEN_DENSITY_LABEL = QUEEN_DENSITY_LABELS[0]
DEFAULT_QUEEN_SPEED_KMH = 50.0
DEFAULT_QUEEN_DESPAWN_DISTANCE_M = 320.0


def queen_density_preset(value=None):
    text = str(value or "").strip()
    normalized = text.lower().replace("_", " ").replace("-", " ")
    for preset in QUEEN_DENSITY_PRESETS:
        if text == preset["label"] or normalized == preset["key"].replace("_", " "):
            return preset
    for preset in QUEEN_DENSITY_PRESETS:
        if str(preset["count"]) in text:
            return preset
    return QUEEN_DENSITY_PRESETS[0]


def queen_density_label_from_config(queen_config):
    queen = queen_config or {}
    density = str(queen.get("density") or "").strip()
    if density:
        return queen_density_preset(density)["label"]
    try:
        count = int(queen.get("count", 4))
    except Exception:
        count = 4
    return queen_density_preset(str(count))["label"]


def apply_queen_density_config(queen_config, value):
    queen = queen_config
    preset = queen_density_preset(value)
    queen["density"] = preset["key"]
    queen["count"] = preset["count"]
    queen["spacing_m"] = preset["spacing_m"]
    queen["safe_gap_m"] = preset["safe_gap_m"]
    return queen


def queen_speed_kmh(value=None):
    try:
        speed = float(str(value if value not in (None, "") else DEFAULT_QUEEN_SPEED_KMH).strip())
    except Exception:
        speed = DEFAULT_QUEEN_SPEED_KMH
    return max(0.0, speed)


def queen_speed_text(value=None):
    speed = queen_speed_kmh(value)
    if abs(speed - round(speed)) < 0.001:
        return str(int(round(speed)))
    return "{0:.1f}".format(speed)


def apply_queen_speed_config(queen_config, value):
    queen_config["speed_kmh"] = queen_speed_kmh(value)
    return queen_config


def queen_despawn_distance_m(value=None):
    try:
        distance = float(str(value if value not in (None, "") else DEFAULT_QUEEN_DESPAWN_DISTANCE_M).strip())
    except Exception:
        distance = DEFAULT_QUEEN_DESPAWN_DISTANCE_M
    return max(0.0, distance)


def queen_despawn_text(value=None):
    distance = queen_despawn_distance_m(value)
    if abs(distance - round(distance)) < 0.001:
        return str(int(round(distance)))
    return "{0:.1f}".format(distance)


def apply_queen_despawn_config(queen_config, value):
    queen_config["despawn_distance_m"] = queen_despawn_distance_m(value)
    return queen_config
PROMPT_EXAMPLE = (
    "Create a 40-second scenario.\n"
    "Ego in the middle lane at 30 km/h.\n"
    "Vehicle 1 in the right lane at 30 km/h 4.2 seconds ahead of ego.\n"
    "At 6s vehicle_1 changes into the middle lane over 3s.\n"
    "Takeover alert visual and auditory speech at 7s for 4s targeting vehicle_1.\n"
)
PROMPT_COMPLETIONS = (
    ("takeover alert", "Takeover alert visual and auditory speech at 10s for 4s targeting v1\n"),
    ("collision takeover alert", "Takeover alert visual and auditory speech 7s before ego hits obstacle 1 for 4s\n"),
    ("pedestrian crossing", "Pedestrian 1 crossing from left to right 35 metres ahead of ego at 5s over 4s.\n"),
    ("beep alert", "beep at=7s duration=4s\n"),
    ("takeover speech", "speech at=7s duration=4s text=\"Please take over.\"\n"),
    ("target circle", "target circle at=7s duration=4s target=v1\n"),
    ("scenario duration", "Create a 40-second scenario.\n"),
    ("ego lane speed", "Ego in the middle lane at 30 km/h.\n"),
    ("front vehicle", "Vehicle 1 in the right lane at 30 km/h 4.2 seconds ahead of ego.\n"),
    ("rear vehicle", "Vehicle 1 in the right lane at 30 km/h 20 seconds behind ego.\n"),
    ("time gap profile", "vehicle_1 longitudinal_mode=time_gap\nvehicle_1 time_gap_points: 5s=6s, 15s=3s\n"),
    ("speed change", "At 10s vehicle_1 changes speed to 20 km/h over 3s.\n"),
    ("lane change", "At 6s vehicle_1 changes into the middle lane over 3s.\n"),
    ("obstacle", "Obstacle 1: a barrier in the middle lane 100 metres ahead of <reference vehicle> at 10s until 20s.\n"),
    ("dropped obstacle", "Obstacle 1: at 8s vehicle_1 drops barrel, offset=0.5m, end=30s\n"),
)
PROMPT_HISTORY_LIMIT = 5
PROMPT_GUIDE_MAX_VEHICLES = 6
PROMPT_GUIDE_STEPS = ("Scene", "Vehicles", "Obstacles", "Pedestrians", "Alert")
PROMPT_BLUEPRINT_MODEL_NAMES = {
    "vehicle.carlamotors.carlacola": "truck",
    "vehicle.mercedes.sprinter": "truck",
    "vehicle.nissan.patrol_2021": "suv",
    "vehicle.jeep.wrangler_rubicon": "jeep",
    "vehicle.tesla.cybertruck": "pickup",
    "vehicle.carlamotors.firetruck": "firetruck",
    "vehicle.ford.ambulance": "ambulance",
}
PROMPT_DEFAULT_BLUEPRINTS = {
    "ego": "vehicle.lincoln.mkz_2020",
    "vehicle_1": "vehicle.audi.etron",
    "vehicle_2": "vehicle.dodge.charger_2020",
    "vehicle_3": "vehicle.mercedes.coupe_2020",
    "vehicle_4": "vehicle.tesla.model3",
    "vehicle_5": "vehicle.nissan.patrol_2021",
    "vehicle_6": "vehicle.mini.cooper_s_2021",
}


@dataclass
class ScenarioItem(object):
    name: str
    kind: str
    path: Path
    label: str
    group: str = DEFAULT_SCENARIO_GROUP
    modified_ts: float = 0.0
    duration_s: object = None
    map_name: str = ""
    weather_preset: str = ""


@dataclass
class XodrLaneCandidate(object):
    road_id: str
    lane_id: int
    lane_type: str
    scenario_lane: object
    polygon: list
    samples: list
    selectable: bool


_XODR_MAP_CACHE = {}


def write_log(message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S "))
            handle.write(message)
            handle.write("\n")
    except OSError:
        pass


def write_exception(exc):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ERROR "))
            handle.write(str(exc))
            handle.write("\n")
            handle.write(traceback.format_exc())
            handle.write("\n")
    except OSError:
        pass


def scenario_metadata_key(path):
    try:
        rel = Path(path).resolve().relative_to(PROJECT_ROOT.resolve())
    except Exception:
        rel = Path(path)
    return str(rel).replace("\\", "/").lower()


def normalize_group(group):
    text = str(group or "").strip()
    return text or DEFAULT_SCENARIO_GROUP


def make_safe_slug(value, fallback="item"):
    text = str(value or "").strip().lower()
    chars = []
    last_sep = False
    for ch in text:
        if ch.isalnum():
            chars.append(ch)
            last_sep = False
        elif ch in ("-", "_", " ", ".", "/"):
            if not last_sep:
                chars.append("_")
                last_sep = True
    slug = "".join(chars).strip("_")
    return slug or str(fallback or "item")


def load_playlist_library():
    if not PLAYLIST_LIBRARY_PATH.exists():
        return []
    try:
        raw = json.loads(PLAYLIST_LIBRARY_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    if isinstance(raw, dict):
        raw_items = raw.get("playlists", [])
    else:
        raw_items = raw
    if not isinstance(raw_items, list):
        return []
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        scenario_names = [str(name).strip() for name in item.get("scenario_names", []) if str(name).strip()]
        if not scenario_names:
            continue
        name = str(item.get("name") or "Playlist").strip() or "Playlist"
        group = normalize_group(item.get("group"))
        playlist_id = str(item.get("id") or "").strip()
        if not playlist_id:
            playlist_id = "{}_{}".format(make_safe_slug(group, "group"), make_safe_slug(name, "playlist"))
        items.append(
            {
                "id": playlist_id,
                "name": name,
                "group": group,
                "updated_at": str(item.get("updated_at") or ""),
                "scenario_names": scenario_names,
                "scenario_overrides": dict(item.get("scenario_overrides", {}) or {}),
                "tor": dict(item.get("tor", {}) or {}),
                "freeze_probe": dict(item.get("freeze_probe", {}) or {}),
                "emotibit": dict(item.get("emotibit", {}) or {}),
                "questionnaire": dict(item.get("questionnaire", {}) or {}),
            }
        )
    return items


def save_playlist_library(items):
    cleaned = []
    seen = set()
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        scenario_names = [str(name).strip() for name in item.get("scenario_names", []) if str(name).strip()]
        if not scenario_names:
            continue
        name = str(item.get("name") or "Playlist").strip() or "Playlist"
        group = normalize_group(item.get("group"))
        playlist_id = str(item.get("id") or "").strip()
        if not playlist_id:
            playlist_id = "{}_{}_{}".format(
                make_safe_slug(group, "group"),
                make_safe_slug(name, "playlist"),
                time.strftime("%Y%m%d_%H%M%S"),
            )
        if playlist_id in seen:
            playlist_id = "{}_{}".format(playlist_id, time.strftime("%H%M%S"))
        seen.add(playlist_id)
        cleaned.append(
            {
                "id": playlist_id,
                "name": name,
                "group": group,
                "updated_at": str(item.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
                "scenario_names": scenario_names,
                "scenario_overrides": dict(item.get("scenario_overrides", {}) or {}),
                "tor": dict(item.get("tor", {}) or {}),
                "freeze_probe": dict(item.get("freeze_probe", {}) or {}),
                "emotibit": dict(item.get("emotibit", {}) or {}),
                "questionnaire": dict(item.get("questionnaire", {}) or {}),
            }
        )
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PLAYLIST_LIBRARY_PATH.write_text(
        json.dumps({"version": 1, "playlists": cleaned}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_scenario_groups():
    if not SCENARIO_GROUPS_PATH.exists():
        return {}
    try:
        raw = json.loads(SCENARIO_GROUPS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    groups = {}
    for key, value in raw.items():
        group = normalize_group(value)
        if group != DEFAULT_SCENARIO_GROUP:
            groups[str(key)] = group
    return groups


def save_scenario_groups(groups):
    cleaned = {}
    for key, value in dict(groups or {}).items():
        group = normalize_group(value)
        if group != DEFAULT_SCENARIO_GROUP:
            cleaned[str(key)] = group
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SCENARIO_GROUPS_PATH.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_scenario_prompts():
    if not SCENARIO_PROMPTS_PATH.exists():
        return {}
    try:
        raw = json.loads(SCENARIO_PROMPTS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    prompts = {}
    for key, value in raw.items():
        prompt = str(value or "").strip()
        if prompt:
            prompts[str(key)] = prompt
    return prompts


def save_scenario_prompts(prompts):
    cleaned = {}
    for key, value in dict(prompts or {}).items():
        prompt = str(value or "").strip()
        if prompt:
            cleaned[str(key)] = prompt
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SCENARIO_PROMPTS_PATH.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def set_scenario_prompt(path, prompt_text):
    prompts = load_scenario_prompts()
    key = scenario_metadata_key(path)
    prompt = str(prompt_text or "").strip()
    if prompt:
        prompts[key] = prompt
    else:
        prompts.pop(key, None)
    save_scenario_prompts(prompts)


def get_scenario_prompt(path):
    return load_scenario_prompts().get(scenario_metadata_key(path), "")


def delete_scenario_prompt(path):
    prompts = load_scenario_prompts()
    prompts.pop(scenario_metadata_key(path), None)
    save_scenario_prompts(prompts)


def load_scenario_prompt_history():
    if not SCENARIO_PROMPT_HISTORY_PATH.exists():
        return {}
    try:
        raw = json.loads(SCENARIO_PROMPT_HISTORY_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    history = {}
    for key, entries in raw.items():
        cleaned_entries = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                prompt = str(entry.get("prompt") or "").strip()
                if prompt:
                    cleaned_entries.append(
                        {
                            "ts": str(entry.get("ts") or ""),
                            "prompt": prompt,
                        }
                    )
            else:
                prompt = str(entry or "").strip()
                if prompt:
                    cleaned_entries.append({"ts": "", "prompt": prompt})
        if cleaned_entries:
            history[str(key)] = cleaned_entries[:PROMPT_HISTORY_LIMIT]
    return history


def save_scenario_prompt_history(history):
    cleaned = {}
    for key, entries in dict(history or {}).items():
        cleaned_entries = []
        for entry in list(entries or []):
            if not isinstance(entry, dict):
                continue
            prompt = str(entry.get("prompt") or "").strip()
            if prompt:
                cleaned_entries.append(
                    {
                        "ts": str(entry.get("ts") or ""),
                        "prompt": prompt,
                    }
                )
        if cleaned_entries:
            cleaned[str(key)] = cleaned_entries[:PROMPT_HISTORY_LIMIT]
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SCENARIO_PROMPT_HISTORY_PATH.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_scenario_prompt_history(path):
    return load_scenario_prompt_history().get(scenario_metadata_key(path), [])


def record_scenario_prompt_history(path, prompt_text):
    prompt = str(prompt_text or "").strip()
    if not prompt:
        return
    history = load_scenario_prompt_history()
    key = scenario_metadata_key(path)
    entries = list(history.get(key, []))
    entries = [entry for entry in entries if str(entry.get("prompt") or "").strip() != prompt]
    entries.insert(
        0,
        {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": prompt,
        },
    )
    history[key] = entries[:PROMPT_HISTORY_LIMIT]
    save_scenario_prompt_history(history)


def delete_scenario_prompt_history(path):
    history = load_scenario_prompt_history()
    history.pop(scenario_metadata_key(path), None)
    save_scenario_prompt_history(history)


def read_prompt_json_summary(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def write_prompt_json_payload(path, payload):
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def scenario_ego_spawn_hints(item):
    if item is None or item.kind != "prompt_json":
        return None, None
    payload = read_prompt_json_summary(item.path)
    spawn_xodr = payload.get("spawn_xodr")
    if not isinstance(spawn_xodr, dict):
        spawn_xodr = None
    spawn_index = payload.get("spawn_point_index")
    if spawn_index in (None, ""):
        spawn_index = None
    return spawn_xodr, spawn_index


def file_modified_ts(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_modified(ts):
    if not ts:
        return "no timestamp"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def format_duration(value):
    duration = float_or_none(value)
    if duration is None:
        return ""
    if abs(duration - round(duration)) < 0.001:
        return "{:.0f}s".format(duration)
    return "{:.1f}s".format(duration)


def format_prompt_number(value, default=0.0):
    number = float_or_none(value)
    if number is None:
        number = float(default)
    if abs(number - round(number)) < 0.001:
        return "{:.0f}".format(number)
    return "{:.3f}".format(number).rstrip("0").rstrip(".")


def prompt_lane(value):
    return str(value or "middle").strip().lower()


def role_to_prompt_name(role):
    role = str(role or "").strip().lower()
    if role == "ego":
        return "ego"
    if role.startswith("vehicle_"):
        suffix = role.split("_", 1)[1]
        if suffix.isdigit():
            return "v{}".format(suffix)
    return role or "vehicle"


def prompt_vehicle_model_clause(role, blueprint_filter):
    blueprint = str(blueprint_filter or "").strip().lower()
    if not blueprint:
        return ""
    if blueprint == PROMPT_DEFAULT_BLUEPRINTS.get(str(role or "").strip().lower(), ""):
        return ""
    model_name = PROMPT_BLUEPRINT_MODEL_NAMES.get(blueprint)
    if model_name:
        return "model={}".format(model_name)
    if blueprint.startswith("vehicle."):
        return "blueprint={}".format(blueprint)
    return ""


def scenario_payload_to_prompt(payload):
    if not isinstance(payload, dict):
        return PROMPT_EXAMPLE
    events = payload.get("experiment_events") if isinstance(payload.get("experiment_events"), dict) else {}
    if events.get("hold_until_tor_resolution"):
        duration_line = "scenario duration=auto"
    else:
        duration_line = "scenario duration={}s".format(format_prompt_number(payload.get("total_time_s"), 24.0))
    from carla_nl.standalone_cues import format_cue
    lines = [duration_line]
    lines.extend(format_cue(cue) for cue in payload.get('standalone_cues', []))
    vehicles = payload.get("vehicles") if isinstance(payload.get("vehicles"), dict) else {}
    ego = vehicles.get("ego") if isinstance(vehicles.get("ego"), dict) else {}
    ego_speed_kmh = float_or_none(ego.get("speed_kmh", payload.get("ego_speed_kmh", 60.0)))
    ego_speed_mps = max(0.001, (ego_speed_kmh if ego_speed_kmh is not None else 60.0) / 3.6)
    lines.append(
        "ego: lane={}, speed={}km/h".format(
            prompt_lane(ego.get("lane", "middle")),
            format_prompt_number(ego_speed_kmh, 60.0),
        )
    )
    for role in sorted(vehicles, key=_prompt_role_sort_key):
        if role == "ego":
            continue
        cfg = vehicles.get(role)
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        name = role_to_prompt_name(role)
        initial_time_gap = float_or_none(cfg.get("initial_time_gap_s"))
        gap = float_or_none(cfg.get("initial_gap_m"))
        if initial_time_gap is not None:
            if initial_time_gap < 0:
                gap_text = "behind={}s".format(format_prompt_number(abs(initial_time_gap)))
            else:
                gap_text = "ahead={}s".format(format_prompt_number(initial_time_gap))
        elif gap is None:
            gap_text = "ahead=0s"
        elif gap < 0:
            gap_text = "behind={}s".format(format_prompt_number(abs(gap) / ego_speed_mps))
        else:
            gap_text = "ahead={}s".format(format_prompt_number(gap / ego_speed_mps))
        parts = [
            "lane={}".format(prompt_lane(cfg.get("lane", "middle"))),
            gap_text,
            "speed={}km/h".format(format_prompt_number(cfg.get("speed_kmh", 60.0))),
        ]
        model_clause = prompt_vehicle_model_clause(role, cfg.get("blueprint_filter"))
        if model_clause:
            parts.append(model_clause)
        lines.append(
            "{}: {}".format(
                name,
                ", ".join(parts),
            )
        )
        for point in _lane_change_points_for_prompt(cfg):
            lines.append(
                "t={}s: {} -> {}, duration={}s".format(
                    format_prompt_number(point.get("time_s", cfg.get("lane_change_time_s", 8.0))),
                    name,
                    prompt_lane(point.get("target_lane", cfg.get("lane_change_target_lane", "middle"))),
                    format_prompt_number(point.get("duration_s", cfg.get("lane_change_duration_s", 3.0))),
                )
        )
        has_time_gap_points = bool(cfg.get("time_gap_points"))
        has_speed_points = bool(cfg.get("speed_points"))
        if has_time_gap_points and not has_speed_points:
            lines.append("{} longitudinal_mode=time_gap".format(name))
        elif has_speed_points and not has_time_gap_points:
            lines.append("{} longitudinal_mode=speed".format(name))
        _append_points_prompt(lines, name, "time_gap_points", cfg.get("time_gap_points"), "time_gap_s", "s")
        _append_points_prompt(lines, name, "gap_points", cfg.get("gap_points"), "gap_m", "m")
        _append_points_prompt(lines, name, "speed_points", cfg.get("speed_points"), "speed_kmh", "km/h")

    pedestrians = payload.get("pedestrians") if isinstance(payload.get("pedestrians"), dict) else {}
    for role in sorted(pedestrians):
        cfg = pedestrians.get(role)
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        gap = float(cfg.get('gap_m', 0.0))
        direction = 'ahead of' if gap >= 0 else 'behind'
        reference = 'ego' if cfg.get('position_reference') == 'ego_at_crossing' else 'the initial ego position'
        lines.append(
            "{} crosses from {} to {} {}m {} {} at {}s over {}s.".format(
                role,
                prompt_lane(cfg.get("start_side", "left")),
                prompt_lane(cfg.get("end_side", "right")),
                format_prompt_number(abs(gap)), direction, reference,
                format_prompt_number(cfg.get("start_time_s", 0.0)),
                format_prompt_number(cfg.get("duration_s", 4.0)),
            )
        )

    obstacles = payload.get("obstacles") if isinstance(payload.get("obstacles"), dict) else {}
    for role in sorted(obstacles):
        cfg = obstacles.get(role)
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        if cfg.get("spawn_mode") == "drop_from_vehicle":
            source_name = role_to_prompt_name(cfg.get("drop_source_role", "vehicle_1"))
            kind_text = str(cfg.get("label") or cfg.get("kind") or "barrel").lower()
            blueprint_id = str(cfg.get("blueprint_id") or "").lower()
            if "barrel" in blueprint_id or "barrel" in kind_text:
                kind_text = "barrel"
            elif "cone" in blueprint_id or "cone" in kind_text:
                kind_text = "cone"
            elif "barrier" in blueprint_id or "barrier" in kind_text:
                kind_text = "barrier"
            else:
                kind_text = str(cfg.get("kind", "barrel")).lower()
            lines.append(
                "{}: t={}s: {} drops {}, offset={}m, end={}s".format(
                    role,
                    format_prompt_number(cfg.get("start_time_s", 0.0)),
                    source_name,
                    kind_text,
                    format_prompt_number(cfg.get("drop_offset_m", 0.5)),
                    format_prompt_number(cfg.get("end_time_s", payload.get("total_time_s", 24.0))),
                )
            )
            continue
        obstacle_kind = str(cfg.get("kind", "barrier")).lower()
        label_kind = str(cfg.get('label', '')).lower()
        if label_kind in ('cone', 'barrier', 'barrel', 'box', 'crate', 'debris', 'disabled vehicle'):
            obstacle_kind = label_kind
        if "box" in str(cfg.get("blueprint_id", "")).lower():
            obstacle_kind = "box"
        from carla_nl.obstacle_positions import obstacle_prompt_position
        lines.append(
            "{}: a {} in the {} lane {}, at {}s until {}s.".format(
                role,
                obstacle_kind,
                prompt_lane(cfg.get("lane", "middle")),
                obstacle_prompt_position(cfg),
                format_prompt_number(cfg.get("start_time_s", 0.0)),
                format_prompt_number(cfg.get("end_time_s", payload.get("total_time_s", 24.0))),
            )
        )
    return "\n".join(lines).strip() + "\n"


def _prompt_role_sort_key(role):
    role = str(role or "")
    if role == "ego":
        return (0, 0, role)
    if role.startswith("vehicle_"):
        try:
            return (1, int(role.split("_", 1)[1]), role)
        except ValueError:
            pass
    return (2, 0, role)


def _lane_change_points_for_prompt(cfg):
    points = cfg.get("lane_change_points")
    if isinstance(points, list) and points:
        return [point for point in points if isinstance(point, dict)]
    if cfg.get("lane_change_enabled"):
        return [
            {
                "time_s": cfg.get("lane_change_time_s", 8.0),
                "target_lane": cfg.get("lane_change_target_lane", "middle"),
                "duration_s": cfg.get("lane_change_duration_s", 3.0),
            }
        ]
    return []


def _append_points_prompt(lines, role_name, label, points, value_key, unit):
    if not isinstance(points, list) or not points:
        return
    chunks = []
    for point in points:
        if not isinstance(point, dict):
            continue
        chunks.append(
            "{}s={}{}".format(
                format_prompt_number(point.get("time_s", 0.0)),
                format_prompt_number(point.get(value_key, 0.0)),
                unit,
            )
        )
    if chunks:
        lines.append("{} {}: {}".format(role_name, label, ", ".join(chunks)))


KMH_PER_MPH = 1.609344

# clause-level keys managed by the Design-tab vehicle editor; everything else
# on a definition line (model=, blueprint=, ...) is preserved verbatim.
_VEHICLE_LANE_CLAUSE_RE = re.compile(r"^lane\s*[=:]", re.I)
_VEHICLE_SPEED_CLAUSE_RE = re.compile(r"^speed\s*[=:]", re.I)
_VEHICLE_GAP_CLAUSE_RE = re.compile(
    r"^(?:ahead|behind|front|rear|initial[\s_-]*gap|time[\s_-]*gap|gap|headway|spacing)\s*[=:]",
    re.I,
)
# no left \b: "50mph" has no word boundary between the digit and "m"
_VEHICLE_MPH_RE = re.compile(r"(?<![A-Za-z])mph\b", re.I)
_PROMPT_TITLE_OR_DURATION_RE = re.compile(r"^\s*(?:title\s*[:\-]|(?:scenario\s+)?duration\b)", re.I)
_ANY_VEHICLE_DEF_RE = re.compile(r"^\s*(?:ego|v\d+|vehicle[\s_-]?\d+)\s*:", re.I)


def vehicle_prompt_def_re(name):
    """Regex matching the prompt definition line for a vehicle prompt name.

    Definition lines start with ``ego:`` / ``v1:`` (alias ``vehicle_1:``);
    lane-change (``t=6s: v1 -> ...``) and per-point lines (``v1 speed_points:``)
    intentionally do not match.
    """
    name = str(name or "").strip().lower()
    if name.startswith("v") and name[1:].isdigit():
        return re.compile(r"^\s*(?:v{0}|vehicle[\s_-]?{0})\s*:".format(name[1:]), re.I)
    return re.compile(r"^\s*{}\s*:".format(re.escape(name or "ego")), re.I)


def rewrite_vehicle_prompt_line(prompt, role, lane=None, speed_kmh=None,
                                gap_mode=None, gap_value=None, gap_unit="s"):
    """Rewrite (or create) the ``ego:`` / ``vN:`` definition line of a prompt.

    Only the managed clauses (lane=, speed=, ahead=/behind=) are replaced; any
    other clause on the line is preserved, and a ``None`` value leaves that
    clause untouched. A speed clause written in mph stays in mph (converted
    from ``speed_kmh``). Returns ``(line_no, text, is_insert)`` with a 0-based
    ``line_no``; ``is_insert`` means insert ``text`` as a new line at
    ``line_no`` instead of replacing the existing one.
    """
    name = role_to_prompt_name(role)
    lane_clause = "lane={}".format(prompt_lane(lane)) if lane is not None else None
    gap_clause = None
    if name != "ego" and gap_mode in ("ahead", "behind") and gap_value is not None:
        unit = "m" if str(gap_unit or "s").strip().lower().startswith("m") else "s"
        gap_clause = "{}={}{}".format(gap_mode, format_prompt_number(abs(float(gap_value))), unit)

    def speed_clause_for(existing_clause):
        if speed_kmh is None:
            return None
        if existing_clause is not None and _VEHICLE_MPH_RE.search(existing_clause):
            return "speed={}mph".format(format_prompt_number(float(speed_kmh) / KMH_PER_MPH))
        return "speed={}km/h".format(format_prompt_number(float(speed_kmh)))

    lines = _split_prompt_lines(prompt)
    def_re = vehicle_prompt_def_re(name)
    line_no = None
    for index, line in enumerate(lines):
        if def_re.match(line):
            line_no = index
            break

    if line_no is None:
        parts = [clause for clause in (lane_clause, gap_clause, speed_clause_for(None)) if clause]
        new_line = "{}: {}".format(name, ", ".join(parts))
        insert_at = 0
        for index, line in enumerate(lines):
            if _ANY_VEHICLE_DEF_RE.match(line) or _PROMPT_TITLE_OR_DURATION_RE.match(line):
                insert_at = index + 1
        return insert_at, new_line, True

    existing = lines[line_no]
    head, _sep, rest = existing.partition(":")
    clauses = [chunk.strip() for chunk in rest.split(",") if chunk.strip()]
    new_clauses = []
    done_lane = lane_clause is None
    done_speed = speed_kmh is None
    done_gap = gap_clause is None
    for clause in clauses:
        if not done_lane and _VEHICLE_LANE_CLAUSE_RE.match(clause):
            new_clauses.append(lane_clause)
            done_lane = True
            continue
        if not done_speed and _VEHICLE_SPEED_CLAUSE_RE.match(clause):
            new_clauses.append(speed_clause_for(clause))
            done_speed = True
            continue
        if not done_gap and _VEHICLE_GAP_CLAUSE_RE.match(clause):
            new_clauses.append(gap_clause)
            done_gap = True
            continue
        new_clauses.append(clause)
    # clauses missing from the line are added in canonical order
    # (lane, gap, speed) ahead of any unmanaged extras such as model=.
    if not done_lane:
        new_clauses.insert(0, lane_clause)
    if not done_gap:
        position = 0
        for index, clause in enumerate(new_clauses):
            if _VEHICLE_LANE_CLAUSE_RE.match(clause):
                position = index + 1
                break
        new_clauses.insert(position, gap_clause)
    if not done_speed:
        position = 0
        for index, clause in enumerate(new_clauses):
            if _VEHICLE_LANE_CLAUSE_RE.match(clause) or _VEHICLE_GAP_CLAUSE_RE.match(clause):
                position = index + 1
        new_clauses.insert(position, speed_clause_for(None))
    indent = existing[: len(existing) - len(existing.lstrip())]
    return line_no, "{}{}: {}".format(indent, head.strip(), ", ".join(new_clauses)), False


def _prompt_role_token_pattern(name):
    """Regex fragment matching one role token (v1 also matches vehicle_1, not v12)."""
    name = str(name or "").strip().lower()
    if name.startswith("v") and name[1:].isdigit():
        return r"(?:v{0}|vehicle[\s_-]?{0})(?![0-9])".format(name[1:])
    return r"{}".format(re.escape(name or "ego"))


def _prompt_role_mention_re(name):
    return re.compile(
        r"(?<![A-Za-z0-9_])" + _prompt_role_token_pattern(name) + r"(?![A-Za-z_])", re.I
    )


def _split_prompt_lines(prompt):
    """Split prompt text into lines, dropping stray CR so every helper edits
    and re-emits LF-only lines (tk widgets keep pasted CR otherwise)."""
    return [
        line[:-1] if line.endswith("\r") else line
        for line in (prompt or "").split("\n")
    ]


# experiment/TOR metadata lines may mention a vehicle ("... target=v1") but do
# not belong to its block; new role lines must not be inserted below them.
_PROMPT_NON_ROLE_LINE_RE = re.compile(r"^\s*(?:tor|takeover|experiment|alert)\b", re.I)


def _prompt_insert_index_after_role_lines(lines, name):
    """0-based index to insert a new line for ``name``: after the last line
    that belongs to the role (definition / points / lane change / mode lines;
    experiment-TOR lines that merely mention it do not count); else after the
    vehicle definition / title / duration block; else at the top."""
    mention_re = _prompt_role_mention_re(name)
    insert_at = None
    for index, line in enumerate(lines):
        if _PROMPT_NON_ROLE_LINE_RE.match(line):
            continue
        if mention_re.search(line):
            insert_at = index + 1
    if insert_at is not None:
        return insert_at
    insert_at = 0
    for index, line in enumerate(lines):
        if _ANY_VEHICLE_DEF_RE.match(line) or _PROMPT_TITLE_OR_DURATION_RE.match(line):
            insert_at = index + 1
    return insert_at


def upsert_lane_change_prompt_line(prompt, role, time_s, target_lane,
                                   duration_s=3.0, time_tol_s=0.25):
    """Add or update a ``t={t}s: v1 -> lane, duration=..s`` line.

    A lane-change line for the same role within ``time_tol_s`` of ``time_s``
    gets its target lane replaced (time text and trailing clauses such as
    duration= stay); otherwise a new line is inserted after the role's other
    prompt lines. Returns ``(line_no, text, is_insert)`` (0-based line_no).
    """
    name = role_to_prompt_name(role)
    # same time-prefix forms the parser's PROMPT_LANE_CHANGE_RE accepts
    line_re = re.compile(
        r"^(?P<indent>\s*)(?:t|time|at|start|from)\s*(?:=|:|is)?\s*"
        r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*s?\s*[:,-]?\s*"
        + _prompt_role_token_pattern(name)
        + r"\s*(?:->|=>|to|into)\s*(?P<lane>left|middle|right)\b(?P<rest>.*)$",
        re.I,
    )
    lines = _split_prompt_lines(prompt)
    for index, line in enumerate(lines):
        match = line_re.match(line)
        if match and abs(float(match.group("time")) - float(time_s)) <= time_tol_s:
            new_line = "{}t={}s: {} -> {}{}".format(
                match.group("indent"),
                match.group("time"),
                name,
                prompt_lane(target_lane),
                match.group("rest") or "",
            )
            return index, new_line, False
    new_line = "t={}s: {} -> {}, duration={}s".format(
        format_prompt_number(time_s),
        name,
        prompt_lane(target_lane),
        format_prompt_number(duration_s, 3.0),
    )
    return _prompt_insert_index_after_role_lines(lines, name), new_line, True


# mirrors the pair spellings PROMPT_TIME_GAP_POINT_RE / PROMPT_SPEED_POINT_RE
# accept: optional t=/time prefix, optional seconds unit, '='/':'/'->'/'to'
# (and the kind keyword) as the time-value separator.
_POINTS_PAIR_TIME_RE = re.compile(
    r"^\s*(?:t|time)?\s*[=:]?\s*(?P<time>[0-9]+(?:\.[0-9]+)?)"
    r"\s*(?:s|sec|secs|second|seconds)?\s*"
    r"(?:=|:|->|to\b|time[\s_-]*gap\s*=?|speed\s*=?)",
    re.I,
)


def upsert_points_prompt_line(prompt, role, kind, time_s, value, time_tol_s=0.25):
    """Add or update one keyframe on a ``v1 time_gap_points:`` / ``v1
    speed_points:`` line.

    ``kind`` is ``"time_gap"`` (value in seconds, negative = behind ego) or
    ``"speed"`` (value in km/h). Every profile-head spelling the parser
    accepts (e.g. ``time_gap_profile``, ``headway_points``, ``speed_profile``,
    ``velocity_profile``) is recognized and normalized to the canonical head.
    With several profile lines for the role, the one already holding a point
    within ``time_tol_s`` is edited (the parser merges all of them, so editing
    the first would leave a stale conflicting point). An existing point within
    ``time_tol_s`` is replaced; other points are preserved verbatim and kept
    time-sorted. When the role has no points line yet, one is created after
    its other prompt lines. Returns ``(line_no, text, is_insert)``.
    """
    name = role_to_prompt_name(role)
    if kind == "time_gap":
        head_re = re.compile(
            r"^(?P<indent>\s*)" + _prompt_role_token_pattern(name)
            + r"\s+(?:time[\s_-]*gap|headway)[\s_-]*(?:points?|profile)\s*:\s*(?P<body>.*)$",
            re.I,
        )
        head_text = "{} time_gap_points:".format(name)
        pair_text = "{}s={}s".format(format_prompt_number(time_s), format_prompt_number(value))
    elif kind == "speed":
        head_re = re.compile(
            r"^(?P<indent>\s*)" + _prompt_role_token_pattern(name)
            + r"\s+(?:speed[\s_-]*(?:points?|profile)|dynamic[\s_-]*speed|velocity[\s_-]*profile)"
            + r"\s*:\s*(?P<body>.*)$",
            re.I,
        )
        head_text = "{} speed_points:".format(name)
        pair_text = "{}s={}km/h".format(format_prompt_number(time_s), format_prompt_number(value))
    else:
        raise ValueError("unsupported points kind: {}".format(kind))

    def chunk_time_of(chunk):
        time_match = _POINTS_PAIR_TIME_RE.match(chunk)
        return float(time_match.group("time")) if time_match else None

    lines = _split_prompt_lines(prompt)
    matches = [
        (index, match)
        for index, match in ((index, head_re.match(line)) for index, line in enumerate(lines))
        if match
    ]
    target = None
    for index, match in matches:
        for chunk in match.group("body").split(","):
            chunk_time = chunk_time_of(chunk.strip())
            if chunk_time is not None and abs(chunk_time - float(time_s)) <= time_tol_s:
                target = (index, match)
                break
        if target is not None:
            break
    if target is None and matches:
        target = matches[0]

    if target is not None:
        index, match = target
        pairs = []
        replaced = False
        for chunk in match.group("body").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk_time = chunk_time_of(chunk)
            if (not replaced and chunk_time is not None
                    and abs(chunk_time - float(time_s)) <= time_tol_s):
                pairs.append((float(time_s), pair_text))
                replaced = True
            else:
                sort_key = chunk_time if chunk_time is not None else float("inf")
                pairs.append((sort_key, chunk))
        if not replaced:
            pairs.append((float(time_s), pair_text))
        pairs.sort(key=lambda item: item[0])
        new_line = "{}{} {}".format(
            match.group("indent"), head_text, ", ".join(text for _key, text in pairs)
        )
        return index, new_line, False
    new_line = "{} {}".format(head_text, pair_text)
    return _prompt_insert_index_after_role_lines(lines, name), new_line, True


def ensure_longitudinal_mode_prompt_line(prompt, role, mode):
    """Make sure the role's effective ``longitudinal_mode`` is ``mode``.

    The parser resolves repeated declarations last-wins, so the decision is
    based on the LAST matching line; rewriting it (instead of the first)
    guarantees the requested mode actually takes effect. Trailing clauses on
    the line (``, model=...``) are preserved. Returns ``(line_no, text,
    is_insert)`` or ``None`` when the effective mode is already correct.
    """
    name = role_to_prompt_name(role)
    line_re = re.compile(
        r"^(?P<indent>\s*)" + _prompt_role_token_pattern(name)
        + r"\s+(?:longitudinal[\s_-]*(?:mode|control)|control[\s_-]*mode|speed[\s_-]*control)"
        + r"\s*(?:=|:)\s*(?P<mode>[A-Za-z_ -]+?)\s*(?P<rest>,.*)?$",
        re.I,
    )
    want = "speed" if "speed" in str(mode).lower() else "time_gap"
    lines = _split_prompt_lines(prompt)
    last = None
    for index, line in enumerate(lines):
        match = line_re.match(line)
        if match:
            last = (index, match)
    if last is not None:
        index, match = last
        current = match.group("mode").strip().lower().replace("-", "_").replace(" ", "_")
        if "fixed" in current or "constant" in current:
            current = "fixed_speed"
        elif ("speed" in current or "velocity" in current) and "gap" not in current:
            current = "speed"
        else:
            current = "time_gap"
        if current == want:
            return None
        return index, "{}{} longitudinal_mode={}{}".format(
            match.group("indent"), name, want, match.group("rest") or ""
        ), False
    new_line = "{} longitudinal_mode={}".format(name, want)
    return _prompt_insert_index_after_role_lines(lines, name), new_line, True


def build_scenario_label(item):
    prefix = "[Prompt JSON]" if item.kind == "prompt_json" else "[Built-in]"
    details = []
    if item.map_name:
        details.append(item.map_name)
    duration = format_duration(item.duration_s)
    if duration:
        details.append(duration)
    if item.modified_ts:
        details.append(format_modified(item.modified_ts))
    suffix = " - " + " | ".join(details) if details else ""
    return "{} {}{}".format(prefix, item.name, suffix)


def sort_scenario_items(items, sort_option):
    sort_option = str(sort_option or SCENARIO_SORT_OPTIONS[0])
    if sort_option == "Modified oldest":
        return sorted(items, key=lambda item: (item.modified_ts or 0.0, item.name.lower()))
    if sort_option == "Name A-Z":
        return sorted(items, key=lambda item: (item.name.lower(), item.modified_ts or 0.0))
    if sort_option == "Name Z-A":
        return sorted(items, key=lambda item: (item.name.lower(), item.modified_ts or 0.0), reverse=True)
    if sort_option == "Duration shortest":
        return sorted(
            items,
            key=lambda item: (
                float_or_none(item.duration_s) is None,
                float_or_none(item.duration_s) or 0.0,
                item.name.lower(),
            ),
        )
    if sort_option == "Duration longest":
        return sorted(
            items,
            key=lambda item: (
                float_or_none(item.duration_s) is not None,
                float_or_none(item.duration_s) or -1.0,
                item.name.lower(),
            ),
            reverse=True,
        )
    return sorted(items, key=lambda item: (-(item.modified_ts or 0.0), item.name.lower()))


def available_scenario_groups(items=None):
    values = set()
    if items:
        for item in items:
            group = normalize_group(item.group)
            if group != DEFAULT_SCENARIO_GROUP:
                values.add(group)
    for group in load_scenario_groups().values():
        group = normalize_group(group)
        if group != DEFAULT_SCENARIO_GROUP:
            values.add(group)
    return sorted(values, key=lambda value: value.lower())


def socket_open(host, port, timeout_s=0.5):
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout_s)
        sock.close()
        return True
    except OSError:
        return False


def run_py37(code, timeout_s=15):
    if not PY37.exists():
        raise RuntimeError("Python 3.7 venv not found: {}".format(PY37))
    proc = subprocess.run(
        [str(PY37), "-c", code],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_s,
    )
    return proc.returncode, proc.stdout


def parse_last_json_line(output):
    for line in reversed(str(output or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise RuntimeError("No JSON payload found in Python output:\n{}".format(output))


def last_non_log_line(output):
    for line in reversed(str(output or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("WARNING:") or line.startswith("INFO:"):
            continue
        return line
    return ""


def xodr_path_for_map(map_name):
    name = str(map_name or DEFAULT_CARLA_MAP).strip()
    # CARLA keeps standard maps' OpenDRIVE in a flat Maps/OpenDrive/ folder,
    # but large maps (Town11/12/13/15) nest it under Maps/<Map>/OpenDrive/.
    # Search every "Maps" root we know about, trying both layouts. The
    # carlaCache 0.9.13 root must stay first so the small standard maps
    # resolve exactly as before.
    # The project may live under Documents while CARLA stays in the user home.
    map_roots = []
    for install_root in dict.fromkeys((USER_ROOT, Path.home())):
        map_roots.extend([
            install_root / "carlaCache" / "0.9.13" / "Carla" / "Maps",
            install_root / "CARLA_0.9.16" / "CarlaUE4" / "Content" / "Carla" / "Maps",
            install_root / "carla-dreyevr-0.9.13" / "Unreal" / "CarlaUE4" / "Content" / "Carla" / "Maps",
            install_root / "carla-src-0.9.16" / "Unreal" / "CarlaUE4" / "Content" / "Carla" / "Maps",
        ])
    candidates = []
    for root in map_roots:
        candidates.append(root / "OpenDrive" / "{}.xodr".format(name))
        candidates.append(root / name / "OpenDrive" / "{}.xodr".format(name))
    candidates.append(PROJECT_ROOT / "{}.xodr".format(name))
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _section_end_s(road, section_index):
    end_s = road.length
    if section_index + 1 < len(road.sections):
        end_s = min(end_s, road.sections[section_index + 1].s)
    return end_s


def _scenario_lane_for_xodr_lane(section, side_name, lane):
    if side_name == "right":
        ordered = [item for item in section.right if item.lane_type == "driving"]
        ordered = sorted(ordered, key=lambda item: abs(item.lane_id))
    else:
        ordered = [item for item in section.left if item.lane_type == "driving"]
        ordered = sorted(ordered, key=lambda item: item.lane_id, reverse=True)
    labels = ("Left", "Middle", "Right")
    for index, item in enumerate(ordered[:3]):
        if item.lane_id == lane.lane_id:
            return labels[index]
    return None


def load_xodr_map_data(map_name, step_m=6.0):
    map_name = str(map_name or DEFAULT_CARLA_MAP).strip()
    xodr_path = xodr_path_for_map(map_name)
    cache_key = (map_name, str(xodr_path), float(step_m))
    if cache_key in _XODR_MAP_CACHE:
        return _XODR_MAP_CACHE[cache_key]
    if not xodr_path.exists():
        raise RuntimeError("OpenDRIVE file not found: {}".format(xodr_path))

    from tools import render_town04_paved_map as xodr_render

    roads = xodr_render.parse_roads(xodr_path)
    candidates = []
    all_points = []
    for road in roads:
        if not road.sections or not road.geometries:
            continue
        for section_index, section in enumerate(road.sections):
            end_s = _section_end_s(road, section_index)
            for side_name, lanes in (("left", section.left), ("right", section.right)):
                for lane in lanes:
                    if lane.lane_type not in xodr_render.PAVED_LANE_TYPES:
                        continue
                    polygon = xodr_render.lane_polygon(road, section, end_s, lane, side_name, step_m)
                    if not polygon:
                        continue
                    all_points.extend(polygon)
                    scenario_lane = None
                    samples = []
                    if lane.lane_type == "driving":
                        scenario_lane = _scenario_lane_for_xodr_lane(section, side_name, lane)
                        for road_s in xodr_render.sample_values(section.s, end_s, max(2.0, step_m)):
                            inner, outer = xodr_render.side_offset_pair(section, road, lane, road_s, side_name)
                            center = (inner + outer) / 2.0
                            x, y = xodr_render.lateral_point(road, road_s, center)
                            samples.append((x, y, road_s))
                    candidates.append(
                        XodrLaneCandidate(
                            road_id=str(road.road_id),
                            lane_id=int(lane.lane_id),
                            lane_type=str(lane.lane_type),
                            scenario_lane=scenario_lane,
                            polygon=polygon,
                            samples=samples,
                            selectable=bool(scenario_lane),
                        )
                    )
    if not all_points:
        raise RuntimeError("No paved OpenDRIVE lane geometry found in {}".format(xodr_path))
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    data = {
        "map_name": map_name,
        "xodr_path": xodr_path,
        "candidates": candidates,
        "bounds": (min_x, min_y, max_x, max_y),
    }
    _XODR_MAP_CACHE[cache_key] = data
    return data


def nearest_xodr_lane(data, x, y):
    best = None
    best_d2 = None
    for candidate in data.get("candidates", []):
        if candidate.lane_type != "driving" or not candidate.samples:
            continue
        for sx, sy, road_s in candidate.samples:
            dx = sx - x
            dy = sy - y
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = (candidate, sx, sy, road_s, math.sqrt(d2))
    return best


def carla_ready():
    if not socket_open(HOST, RPC_PORT, 0.5):
        return False, "RPC offline"
    code = (
        "import carla; "
        "c=carla.Client('127.0.0.1',2013); "
        "c.set_timeout(5); "
        "w=c.get_world(); "
        "print(w.get_map().name)"
    )
    try:
        rc, out = run_py37(code, timeout_s=10)
    except Exception as exc:
        return False, str(exc)
    if rc != 0:
        return False, out.strip()
    map_name = last_non_log_line(out) or "(unknown map)"
    return True, map_name


def query_dreyevr_egos():
    if not socket_open(HOST, RPC_PORT, 0.5):
        return []
    code = r"""
import json
import carla

c = carla.Client('127.0.0.1', 2013)
c.set_timeout(8)
w = c.get_world()
actors = list(w.get_actors())
if not actors:
    settings = w.get_settings()
    for _ in range(5):
        try:
            if settings.synchronous_mode:
                w.tick()
            else:
                w.wait_for_tick()
        except Exception:
            pass
        actors = list(w.get_actors())
        if actors:
            break
egos = []
for a in actors:
    tid = a.type_id
    role = a.attributes.get('role_name', '')
    is_dreyevr = tid.startswith('harplab.dreyevr_vehicle')
    is_vehicle = tid.startswith('vehicle.') or is_dreyevr
    if is_dreyevr or (is_vehicle and role in ('hero', 'ego')):
        try:
            loc = a.get_transform().location
            xyz = [loc.x, loc.y, loc.z]
        except Exception:
            xyz = [None, None, None]
        egos.append({
            "id": a.id,
            "type_id": tid,
            "role_name": role,
            "x": xyz[0],
            "y": xyz[1],
            "z": xyz[2],
        })
print(json.dumps({"egos": egos}, separators=(',', ':')))
"""
    rc, out = run_py37(code, timeout_s=15)
    if rc != 0:
        raise RuntimeError(out.strip() or "DReyeVR ego query failed")
    payload = parse_last_json_line(out)
    egos = payload.get("egos", [])
    return egos if isinstance(egos, list) else []


def dreyevr_ego_count():
    try:
        return len(query_dreyevr_egos())
    except Exception:
        return 0


def wait_for_dreyevr_ego(emit, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        count = dreyevr_ego_count()
        if count > 0:
            emit("DReyeVR ego ready: {} actor(s).".format(count))
            return True
        emit("Waiting for DReyeVR ego actor...")
        time.sleep(2)
    return False


def dedupe_dreyevr_egos(emit):
    egos = query_dreyevr_egos()
    if len(egos) <= 1:
        return len(egos)

    code = r"""
import json
import carla

c = carla.Client('127.0.0.1', 2013)
c.set_timeout(8)
w = c.get_world()
actors = list(w.get_actors())
if not actors:
    settings = w.get_settings()
    for _ in range(5):
        try:
            if settings.synchronous_mode:
                w.tick()
            else:
                w.wait_for_tick()
        except Exception:
            pass
        actors = list(w.get_actors())
        if actors:
            break
heroes = []
for a in actors:
    tid = a.type_id
    role = a.attributes.get('role_name', '')
    is_dreyevr = tid.startswith('harplab.dreyevr_vehicle')
    is_vehicle = tid.startswith('vehicle.') or is_dreyevr
    if is_dreyevr or (is_vehicle and role in ('hero', 'ego')):
        heroes.append(a)
heroes.sort(key=lambda a: (0 if a.type_id.startswith('harplab.dreyevr_vehicle') else 1, a.id))
kept = heroes[0] if heroes else None
destroyed = []
for extra in heroes[1:]:
    try:
        destroyed.append(extra.id)
        extra.destroy()
    except Exception:
        pass
try:
    settings = w.get_settings()
    if settings.synchronous_mode:
        w.tick()
    else:
        w.wait_for_tick()
except Exception:
    pass
print(json.dumps({
    "kept_id": kept.id if kept is not None else None,
    "destroyed_ids": destroyed,
}, separators=(',', ':')))
"""
    rc, out = run_py37(code, timeout_s=20)
    if rc != 0:
        raise RuntimeError(out.strip() or "DReyeVR ego dedupe failed")
    payload = parse_last_json_line(out)
    destroyed_ids = payload.get("destroyed_ids", [])
    emit(
        "DReyeVR ego cleanup kept actor {} and removed {} duplicate ego vehicle(s).".format(
            payload.get("kept_id"),
            len(destroyed_ids) if isinstance(destroyed_ids, list) else 0,
        )
    )
    return 1 if payload.get("kept_id") is not None else 0


def normalize_dreyevr_egos(emit):
    try:
        egos = query_dreyevr_egos()
    except Exception as exc:
        emit("WARN: DReyeVR ego query failed: {}".format(exc))
        return 0
    if len(egos) <= 1:
        return len(egos)
    try:
        return dedupe_dreyevr_egos(emit)
    except Exception as exc:
        emit("WARN: duplicate DReyeVR ego cleanup failed: {}".format(exc))
        return len(egos)


def recover_missing_dreyevr_ego(emit, spawn_xodr=None, spawn_index=None):
    count = normalize_dreyevr_egos(emit)
    if count > 0:
        return True

    if isinstance(spawn_xodr, dict):
        try:
            result = teleport_ego_to_xodr(spawn_xodr)
            emit(
                "Recovered missing DReyeVR ego in current world at OpenDRIVE "
                "road={} lane={} s={} actor={}.".format(
                    result.get("road_id"),
                    result.get("lane_id"),
                    result.get("s"),
                    result.get("actor_id"),
                )
            )
            return True
        except Exception as exc:
            emit("WARN: OpenDRIVE ego recovery failed: {}".format(exc))

    if spawn_index not in (None, ""):
        try:
            result = teleport_ego_to_spawn(spawn_index)
            emit(
                "Recovered missing DReyeVR ego in current world at spawn {} actor={}.".format(
                    result.get("spawn_index"),
                    result.get("actor_id"),
                )
            )
            return True
        except Exception as exc:
            emit("WARN: spawn-index ego recovery failed: {}".format(exc))

    try:
        result = teleport_ego_to_spawn(0)
        emit(
            "Recovered missing DReyeVR ego in current world at fallback spawn 0 actor={}.".format(
                result.get("actor_id")
            )
        )
        return True
    except Exception as exc:
        emit("WARN: fallback ego recovery failed: {}".format(exc))
    return dreyevr_ego_count() > 0


def teleport_ego_to_spawn(spawn_index):
    code = r"""
import json
import carla

idx = int(__SPAWN_INDEX__)
c = carla.Client('127.0.0.1', 2013)
c.set_timeout(8)
w = c.get_world()
m = w.get_map()
spawns = m.get_spawn_points()
if idx < 0 or idx >= len(spawns):
    raise RuntimeError("spawn index out of range: {}".format(idx))
heroes = [
    a for a in w.get_actors().filter('*vehicle*')
    if a.attributes.get('role_name', '') in ('hero', 'ego') or a.type_id.startswith('harplab.dreyevr_vehicle')
]
if heroes:
    heroes.sort(key=lambda a: (0 if a.type_id.startswith('harplab.dreyevr_vehicle') else 1, a.id))
    ego = heroes[0]
    for extra in heroes[1:]:
        try:
            if hasattr(extra, 'set_autopilot'):
                extra.set_autopilot(False)
            extra.set_simulate_physics(False)
            extra.destroy()
        except Exception:
            pass
else:
    bp = w.get_blueprint_library().find('harplab.dreyevr_vehicle.model3')
    if bp.has_attribute('role_name'):
        bp.set_attribute('role_name', 'hero')
    ego = w.try_spawn_actor(bp, spawns[idx])
    if ego is None:
        raise RuntimeError("failed to spawn DReyeVR ego at spawn {}".format(idx))
sp = spawns[idx]
sp.location.z += 0.80
ego.set_simulate_physics(True)
ego.set_transform(sp)
ego.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
ego.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
ego.set_simulate_physics(True)
if hasattr(ego, "apply_control"):
    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False, manual_gear_shift=False))
settings = w.get_settings()
try:
    if settings.synchronous_mode:
        w.tick()
    else:
        w.wait_for_tick()
except Exception:
    pass
try:
    ego.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    ego.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    if hasattr(ego, "apply_control"):
        ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False, manual_gear_shift=False))
except Exception:
    pass
xf = ego.get_transform()
print(json.dumps({
    "map": m.name,
    "actor_id": ego.id,
    "type_id": ego.type_id,
    "spawn_index": idx,
    "x": xf.location.x,
    "y": xf.location.y,
    "z": xf.location.z,
    "yaw": xf.rotation.yaw,
}, separators=(',', ':')))
""".replace("__SPAWN_INDEX__", str(int(spawn_index)))
    rc, out = run_py37(code, timeout_s=20)
    if rc != 0:
        raise RuntimeError(out.strip() or "teleport failed")
    return parse_last_json_line(out)


def teleport_ego_to_xodr(spawn_xodr):
    road_id = int(spawn_xodr["road_id"])
    lane_id = int(spawn_xodr["lane_id"])
    road_s = float(spawn_xodr["s"])
    code = r"""
import json
import carla

road_id = int(__ROAD_ID__)
lane_id = int(__LANE_ID__)
road_s = float(__ROAD_S__)
c = carla.Client('127.0.0.1', 2013)
c.set_timeout(8)
w = c.get_world()
m = w.get_map()
wp = m.get_waypoint_xodr(road_id, lane_id, road_s)
if wp is None:
    raise RuntimeError("OpenDRIVE waypoint not found road={} lane={} s={}".format(road_id, lane_id, road_s))
xf = wp.transform
xf.location.z += 0.80
heroes = [
    a for a in w.get_actors().filter('*vehicle*')
    if a.attributes.get('role_name', '') in ('hero', 'ego') or a.type_id.startswith('harplab.dreyevr_vehicle')
]
if heroes:
    heroes.sort(key=lambda a: (0 if a.type_id.startswith('harplab.dreyevr_vehicle') else 1, a.id))
    ego = heroes[0]
    for extra in heroes[1:]:
        try:
            if hasattr(extra, 'set_autopilot'):
                extra.set_autopilot(False)
            extra.set_simulate_physics(False)
            extra.destroy()
        except Exception:
            pass
else:
    bp = w.get_blueprint_library().find('harplab.dreyevr_vehicle.model3')
    if bp.has_attribute('role_name'):
        bp.set_attribute('role_name', 'hero')
    ego = w.try_spawn_actor(bp, xf)
    if ego is None:
        raise RuntimeError("failed to spawn DReyeVR ego at OpenDRIVE waypoint")
ego.set_simulate_physics(True)
ego.set_transform(xf)
ego.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
ego.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
ego.set_simulate_physics(True)
if hasattr(ego, "apply_control"):
    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False, manual_gear_shift=False))
settings = w.get_settings()
try:
    if settings.synchronous_mode:
        w.tick()
    else:
        w.wait_for_tick()
except Exception:
    pass
try:
    ego.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    ego.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    if hasattr(ego, "apply_control"):
        ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0, hand_brake=False, manual_gear_shift=False))
except Exception:
    pass
xf = ego.get_transform()
print(json.dumps({
    "map": m.name,
    "actor_id": ego.id,
    "type_id": ego.type_id,
    "road_id": road_id,
    "lane_id": lane_id,
    "s": road_s,
    "x": xf.location.x,
    "y": xf.location.y,
    "z": xf.location.z,
    "yaw": xf.rotation.yaw,
}, separators=(',', ':')))
"""
    code = code.replace("__ROAD_ID__", str(road_id))
    code = code.replace("__LANE_ID__", str(lane_id))
    code = code.replace("__ROAD_S__", repr(road_s))
    rc, out = run_py37(code, timeout_s=20)
    if rc != 0:
        raise RuntimeError(out.strip() or "OpenDRIVE teleport failed")
    return parse_last_json_line(out)


def list_scenarios():
    items = []
    groups = load_scenario_groups()
    if OUTPUT_SCENARIOS.exists():
        for path in sorted(OUTPUT_SCENARIOS.glob("*.json")):
            key = scenario_metadata_key(path)
            summary = read_prompt_json_summary(path)
            item = ScenarioItem(
                name=path.stem,
                kind="prompt_json",
                path=path,
                label="",
                group=normalize_group(groups.get(key)),
                modified_ts=file_modified_ts(path),
                duration_s=float_or_none(summary.get("total_time_s")),
                map_name=str(summary.get("map_name") or ""),
                weather_preset=str(summary.get("weather_preset") or ""),
            )
            item.label = build_scenario_label(item)
            items.append(item)
    if SCRIPTED_SCENARIOS.exists():
        for path in sorted(SCRIPTED_SCENARIOS.glob("scenario*.py")):
            if path.name.startswith("__") or path.name == "common.py":
                continue
            key = scenario_metadata_key(path)
            item = ScenarioItem(
                name=path.stem,
                kind="scripted",
                path=path,
                label="",
                group=normalize_group(groups.get(key)),
                modified_ts=file_modified_ts(path),
            )
            item.label = build_scenario_label(item)
            items.append(item)
    return items


def is_large_map(map_name):
    name = str(map_name or "").strip()
    return any(token in name for token in LARGE_MAP_TOKENS)


def boot_map_for(map_name):
    """Return the map UE4 should actually *start* on.

    Large maps must not be the UE4 startup map (their streaming tiles never load
    that way in this DReyeVR build); boot a normal map and load_world() into the
    large map at runtime instead.
    """
    name = str(map_name or DEFAULT_CARLA_MAP).strip() or DEFAULT_CARLA_MAP
    return DEFAULT_CARLA_MAP if is_large_map(name) else name


def load_display_mode():
    try:
        mode = json.loads(DISPLAY_SETTINGS_PATH.read_text(encoding="utf-8"))["display_mode"]
        return mode if mode in DISPLAY_MODES else DISPLAY_MODES[0]
    except (OSError, ValueError, KeyError, TypeError):
        return DISPLAY_MODES[0]


def save_display_mode(mode):
    if mode not in DISPLAY_MODES:
        raise ValueError("Choose Three screens or Single screen.")
    DISPLAY_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DISPLAY_SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"display_mode": mode}, indent=2), encoding="utf-8")
    temporary.replace(DISPLAY_SETTINGS_PATH)


def launch_carla_process(visual_preset="TunedHigh", map_name=DEFAULT_CARLA_MAP, display_mode="Three screens"):
    if not START_CARLA_PS1.exists():
        raise RuntimeError("CARLA launcher not found: {}".format(START_CARLA_PS1))
    if display_mode not in DISPLAY_MODES:
        raise ValueError("Choose Three screens or Single screen.")
    single = display_mode == "Single screen"
    cfg = PROJECT_ROOT / "ndisplay" / "single-screen.cfg" if single else NDISPLAY_CFG
    if not cfg.exists():
        raise RuntimeError("Display configuration not found: {}".format(cfg))
    visual_preset = visual_preset if visual_preset in QUALITY_PRESET_ARGS else "TunedHigh"
    map_name = str(map_name or DEFAULT_CARLA_MAP).strip() or DEFAULT_CARLA_MAP
    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(START_CARLA_PS1),
        "-Map",
        map_name,
        "-NoFullscreen",
        "-Cfg",
        str(cfg),
        "-ResX",
        str(1280 if single else NDISPLAY_RES_X),
        "-ResY",
        str(720 if single else NDISPLAY_RES_Y),
        "-WinX",
        str(0 if single else NDISPLAY_WIN_X),
        "-WinY",
        str(NDISPLAY_WIN_Y),
        "-MaxPerfMode",
    ] + QUALITY_PRESET_ARGS[visual_preset]
    if single:
        args.append("-Bordered")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        creationflags=creationflags,
    )


def launch_prompt_generator():
    if not PROMPT_GUI_PS1.exists():
        raise RuntimeError("Prompt GUI launcher not found: {}".format(PROMPT_GUI_PS1))
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROMPT_GUI_PS1),
        ],
        cwd=str(PROJECT_ROOT),
        creationflags=creationflags,
    )


def _tag_layout_json_from_text(corner_ids):
    try:
        layout = {}
        for segment in str(corner_ids or DEFAULT_AOI_CORNER_IDS).split(";"):
            segment = segment.strip()
            if not segment:
                continue
            name, raw_ids = segment.split(":", 1)
            layout[name.strip().lower()] = {
                "corner_ids": [int(part.strip()) for part in raw_ids.split(",") if part.strip()]
            }
        for name in ("left", "center", "right"):
            if name not in layout:
                raise ValueError("missing {}".format(name))
        return json.dumps(layout, separators=(",", ":"))
    except Exception:
        layout = {
            "left": {"corner_ids": [0, 1, 2, 3]},
            "center": {"corner_ids": [4, 5, 6, 7]},
            "right": {"corner_ids": [8, 9, 10, 11]},
        }
        return json.dumps(layout, separators=(",", ":"))


def launch_aoi_process(stream_name, corner_ids, grid_name, neon_ip="", no_voice=False,
                       latency_log=False, start_minimized=False, no_emotibit=False):
    if not AOI_PYTHON.exists():
        raise RuntimeError("AOI Python venv not found: {}".format(AOI_PYTHON))
    if not AOI_SCRIPT.exists():
        raise RuntimeError("AOI script not found: {}".format(AOI_SCRIPT))
    args = [
        str(AOI_PYTHON),
        "-u",
        "-m",
        "extensions.level3_tor_demo.aoi_emotibit_monitor",
        "--scenario-marker-stream",
        stream_name or DEFAULT_LSL_STREAM,
        "--driver-state-marker-stream",
        "Level3DriverStateMarkers",
        "--tag-layout-json",
        _tag_layout_json_from_text(corner_ids),
        "--target-grid",
        grid_name or DEFAULT_AOI_GRID,
        "--feedback-host",
        "127.0.0.1",
        "--feedback-port",
        DEFAULT_AOI_FEEDBACK_PORT,
        "--dynamic-target-ttl-s",
        "1.0",
        "--target-fixation-s",
        "0.5",
        "--frame-timeout-s",
        "0.05",
        "--gaze-drain-limit",
        "3",
        "--tag-quad-decimate",
        "3.0",
        "--tag-nthreads",
        "2",
        "--tag-detection-interval-s",
        "0.30",
        "--preview-max-width",
        "960",
        "--preview-max-fps",
        "15.0",
        "--world-frame-ready-timeout-s",
        "25.0",
    ]
    neon_ip = str(neon_ip or "").strip()
    if neon_ip:
        args += ["--ip", neon_ip]
    if no_emotibit:
        args.append("--no-emotibit")
    if no_voice:
        args.append("--no-stage-audio")
    return subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def build_lsl_data_reader_command(stream_names, output_dir=None, session_label="gui"):
    from carla_nl.lsl_data_reader import parse_stream_names

    py = AOI_PYTHON if AOI_PYTHON.exists() else Path(sys.executable)
    names = parse_stream_names(stream_names)
    if not names:
        names = [DEFAULT_LSL_STREAM, DEFAULT_CARLA_TELEMETRY_STREAM]
    args = [
        str(py),
        "-u",
        "-m",
        CARLA_LSL_DATA_READER_MODULE,
        "--output-dir",
        str(output_dir or CARLA_LSL_DATA_OUTPUT_DIR),
        "--session-label",
        str(session_label or "gui"),
        "--csv",
        "--resolve-timeout-s",
        "300",
        "--resolve-call-timeout-s",
        "0.05",
        "--status-every-s",
        "5",
    ]
    for name in names:
        args += ["--stream-name", name]
    return args


def launch_lsl_data_reader_process(stream_names, output_dir=None, session_label="gui"):
    args = build_lsl_data_reader_command(
        stream_names,
        output_dir=output_dir or CARLA_LSL_DATA_OUTPUT_DIR,
        session_label=session_label,
    )
    return subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def launch_level3_session_process(config_path, scenario_dir, preflight_only=False, dry_run=False,
                                  wait_for_start=False, interactive=False, exit_after_run=False):
    py = AOI_PYTHON if AOI_PYTHON.exists() else Path(sys.executable)
    args = [
        str(py),
        "-u",
        "-m",
        LEVEL3_SESSION_MODULE,
        "--config",
        str(config_path),
        "--scenario-dir",
        str(scenario_dir),
    ]
    if preflight_only:
        args.append("--preflight-only")
    if dry_run:
        args.append("--dry-run")
    if wait_for_start:
        args.append("--wait-for-start-stdin")
    if interactive:
        args.append("--interactive-session-stdin")
    if exit_after_run:
        args.append("--exit-after-run")
    env = os.environ.copy()
    env["CARLA_LAUNCHER_QUESTIONNAIRE_HANDOFF"] = "1"
    return subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


def launch_neon_pupil_test_process(stream_name, corner_ids, grid_name, neon_ip=""):
    if not AOI_PYTHON.exists():
        raise RuntimeError("AOI Python venv not found: {}".format(AOI_PYTHON))
    tag_layout = _tag_layout_json_from_text(corner_ids or DEFAULT_AOI_CORNER_IDS)
    args = [
        str(AOI_PYTHON),
        "-u",
        "-m",
        "extensions.level3_tor_demo.neon_apriltag_aoi_test",
        "--scenario-marker-stream",
        stream_name or DEFAULT_LSL_STREAM,
        "--tag-layout-json",
        tag_layout,
        "--target-grid",
        grid_name or DEFAULT_AOI_GRID,
        "--listen-carla-aoi",
        "--hide-driver-aoi",
    ]
    neon_ip = str(neon_ip or "").strip()
    if neon_ip:
        args += ["--ip", neon_ip]
    return subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def lsl_marker_stream_visible(name="CarlaScenarioMarkers", timeout=1.0):
    """Best-effort check that an LSL stream is resolvable. None if pylsl absent."""
    try:
        import pylsl
    except Exception:
        return None
    try:
        return bool(pylsl.resolve_byprop("name", name, timeout=float(timeout)))
    except Exception:
        return None


def launch_lab_recorder_process():
    if not LAB_RECORDER_EXE.exists():
        raise RuntimeError("LabRecorder.exe not found: {}".format(LAB_RECORDER_EXE))
    proc = subprocess.Popen(
        [str(LAB_RECORDER_EXE)],
        cwd=str(LAB_RECORDER_EXE.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Best-effort readiness feedback: confirm the marker timeline LabRecorder will
    # record is already on the network (non-blocking beyond a short resolve).
    visible = lsl_marker_stream_visible()
    if visible is True:
        print("LabRecorder launched; CarlaScenarioMarkers stream is visible (record timeline ready).", flush=True)
    elif visible is False:
        print("LabRecorder launched; CarlaScenarioMarkers not visible yet — start the marker bridge / scenario so it is recorded.", flush=True)
    return proc


def _latest_survey_script(directory):
    candidates = [path for path in Path(directory).glob("*_survey.py") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def build_questionnaire_launch_command(root, language="zh", script_path=""):
    root = Path(str(root or SURVEY_GENERATOR_ROOT))
    script_text = str(script_path or "").strip()
    if script_text:
        target = Path(script_text)
        if not target.is_absolute():
            target = root / target
        if target.is_dir():
            latest = _latest_survey_script(target)
            if latest is None:
                raise RuntimeError("Questionnaire script directory has no *_survey.py: {}".format(target))
            target = latest
        if not target.exists():
            raise RuntimeError("Questionnaire survey script not found: {}".format(target))
        venv_python = root / ".venv" / "Scripts" / "python.exe"
        py = venv_python if venv_python.exists() else Path(sys.executable)
        return [str(py), str(target)], target.parent

    run_ps1 = root / "run.ps1"
    if not run_ps1.exists():
        raise RuntimeError("SurveyGenerator run.ps1 not found: {}".format(run_ps1))
    language = str(language or "zh").strip().lower()
    if language not in ("en", "zh"):
        language = "zh"
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(run_ps1),
        "-Language",
        language,
    ], root


def launch_questionnaire_process(root, language="zh", script_path=""):
    cmd, cwd = build_questionnaire_launch_command(root, language=language, script_path=script_path)
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def launch_emotibit_oscilloscope_process(emit):
    from extensions.level3_tor_demo.emotibit_oscilloscope import start_emotibit_oscilloscope

    config = {
        "emotibit": {
            "enabled": True,
            "auto_start_oscilloscope": True,
            "oscilloscope_path": str(EMOTIBIT_OSCILLOSCOPE_EXE),
            "reuse_existing_oscilloscope": True,
            "oscilloscope_start_minimized": False,
            # The launcher may run under a Python without pylsl. The AOI process
            # still verifies the streams using the AOI research venv.
            "verify_lsl_streams": False,
            "auto_select_lsl": False,
        }
    }
    return start_emotibit_oscilloscope(config, emit=emit)


def run_manual_tor_marker(stream_name, grid_name, wait_before_alert_s, alert_delay_s):
    if not AOI_PYTHON.exists():
        raise RuntimeError("AOI Python venv not found: {}".format(AOI_PYTHON))
    if not MANUAL_TOR_MARKER_SCRIPT.exists():
        raise RuntimeError("Manual TOR marker script not found: {}".format(MANUAL_TOR_MARKER_SCRIPT))
    proc = subprocess.run(
        [
            str(AOI_PYTHON),
            str(MANUAL_TOR_MARKER_SCRIPT),
            "--stream-name",
            stream_name or DEFAULT_LSL_STREAM,
            "--wait-before-alert-s",
            str(wait_before_alert_s),
            "--alert-delay-s",
            str(alert_delay_s),
            "--aoi-screen",
            "current",
            "--aoi-grid",
            grid_name or DEFAULT_AOI_GRID,
            "--aoi-x",
            "0.1667",
            "--aoi-y",
            "0.5000",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout


def wait_for_carla(emit, timeout_s=180):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ready, message = carla_ready()
        if ready:
            emit("CARLA ready: {}".format(message))
            return True
        emit("Waiting for CARLA RPC {}:{} - {}".format(HOST, RPC_PORT, message))
        time.sleep(2)
    raise RuntimeError("CARLA did not become ready within {}s".format(timeout_s))


def _cleanup_scene_impl(emit):
    if not socket_open(HOST, RPC_PORT, 0.5):
        emit("cleanup skipped: CARLA RPC offline.")
        return
    code = r"""
import carla
c = carla.Client('127.0.0.1', 2013)
c.set_timeout(8)
w = c.get_world()
s = w.get_settings()
s.synchronous_mode = True
s.fixed_delta_seconds = 1.0 / 30.0
w.apply_settings(s)
actors = list(w.get_actors())
if not actors:
    for _ in range(5):
        try:
            w.tick()
        except Exception:
            pass
        actors = list(w.get_actors())
        if actors:
            break
destroyed = 0
ego_candidates = []
for a in actors:
    tid = a.type_id
    role = a.attributes.get('role_name', '')
    is_dreyevr = tid.startswith('harplab.dreyevr_vehicle')
    is_vehicle = tid.startswith('vehicle.') or is_dreyevr
    if is_dreyevr or (is_vehicle and role in ('ego', 'hero')):
        ego_candidates.append(a)
ego_candidates.sort(key=lambda a: (0 if a.type_id.startswith('harplab.dreyevr_vehicle') else 1, a.id))
keep_ego_id = ego_candidates[0].id if ego_candidates else None
for a in ego_candidates[1:]:
    try:
        if hasattr(a, 'set_autopilot'):
            a.set_autopilot(False)
        a.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        a.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        a.set_simulate_physics(False)
        a.destroy()
        destroyed += 1
    except Exception:
        pass
ego_frozen = 0
for a in list(w.get_actors()):
    tid = a.type_id
    role = a.attributes.get('role_name', '')
    is_dreyevr = tid.startswith('harplab.dreyevr_vehicle')
    is_vehicle = tid.startswith('vehicle.') or is_dreyevr
    if is_dreyevr or (is_vehicle and role in ('ego', 'hero')):
        if keep_ego_id is not None and a.id != keep_ego_id:
            continue
        try:
            if hasattr(a, 'set_autopilot'):
                a.set_autopilot(False)
            a.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            a.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            if hasattr(a, 'apply_control'):
                a.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
            a.set_simulate_physics(True)
            ego_frozen += 1
        except Exception:
            pass
from carla_nl.scene_cleanup import scenario_actors, destroy_actors
pending = scenario_actors(w)
destroyed += destroy_actors(w, pending, c.get_trafficmanager(8000))
for a in list(w.get_actors()):
    tid = a.type_id
    role = a.attributes.get('role_name', '')
    if tid.startswith('harplab.dreyevr_vehicle') or role in ('ego', 'hero'):
        try:
            if hasattr(a, 'set_autopilot'):
                a.set_autopilot(False)
            a.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            a.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            if hasattr(a, 'apply_control'):
                a.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
            a.set_simulate_physics(True)
        except Exception:
            pass
print('cleanup_ego_frozen={}'.format(ego_frozen))
print('cleanup_destroyed={}'.format(destroyed))
print('cleanup_remaining={}'.format(len(pending)))
"""
    rc, out = run_py37(code, timeout_s=20)
    for line in out.splitlines():
        if line.strip():
            emit(line.strip())
    if rc != 0:
        raise RuntimeError("Scene cleanup failed with code {}. Check the remaining object IDs above before starting another scenario.".format(format_return_code(rc)))


def cleanup_scene(emit):
    with _CLEANUP_SCENE_LOCK:
        _cleanup_scene_impl(emit)


def stop_carla_processes(emit):
    cmd = r"""
$names = @('UE4Editor','CarlaUE4-Win64-Shipping','CarlaUE4')
$targets = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.ProcessName })
if ($targets.Count -eq 0) {
    Write-Output 'No CARLA simulator process found.'
    exit 0
}
foreach ($p in $targets) {
    try {
        Write-Output ("Stopping {0} pid={1}" -f $p.ProcessName, $p.Id)
        Stop-Process -Id $p.Id -Force -ErrorAction Stop
    } catch {
        Write-Output ("WARN: failed to stop {0} pid={1}: {2}" -f $p.ProcessName, $p.Id, $_.Exception.Message)
    }
}
Start-Sleep -Seconds 2
$survivors = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.ProcessName })
if ($survivors.Count -gt 0) {
    foreach ($p in $survivors) {
        Write-Output ("WARN: CARLA process still alive {0} pid={1}" -f $p.ProcessName, $p.Id)
    }
    exit 1
}
exit 0
"""
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.stdout.strip():
        emit(proc.stdout.strip())
    if proc.returncode != 0:
        emit("WARN: failed to stop CARLA processes (exit code {}).".format(proc.returncode))
        return False
    emit("CARLA simulator stopped.")
    return True


def format_return_code(return_code):
    if return_code is None:
        return "running"
    unsigned = int(return_code) & 0xFFFFFFFF
    return "{} (0x{:08X})".format(return_code, unsigned)


class MapSpawnPicker(object):
    def __init__(self, parent, map_name, initial_spawn=None, initial_lane="Middle", on_accept=None):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.parent = parent
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.map_name = str(map_name or DEFAULT_CARLA_MAP)
        self.on_accept = on_accept
        self.data = load_xodr_map_data(self.map_name)
        self.selection = None
        self.marker_items = []

        root_widget = getattr(parent, "top", None) or getattr(parent, "root", None)
        self.top = tk.Toplevel(root_widget)
        self.top.title("Pick Ego Spawn on 2D Map - {}".format(self.map_name))
        self.top.geometry("980x760")
        self.top.minsize(820, 620)
        configure_inkspace_window(self.top)
        if root_widget is not None:
            self.top.transient(root_widget)

        self.status_var = tk.StringVar(value="Click a driving lane on the OpenDRIVE map.")
        self.detail_var = tk.StringVar(value="")
        self.lane_var = tk.StringVar(value=str(initial_lane or "Middle"))
        self.accept_button = None

        self._build_ui()
        self._draw_map()
        if isinstance(initial_spawn, dict):
            self._select_from_spawn(initial_spawn, initial_lane)
        self.top.grab_set()

    def _build_ui(self):
        ttk = self.ttk
        tk = self.tk
        root = ttk.Frame(self.top, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="OpenDRIVE 2D paved-road map: {}".format(self.data.get("xodr_path")),
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))

        body = ttk.Frame(root)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            body,
            bg=INKSPACE_COLORS["field"],
            highlightthickness=1,
            highlightbackground=INKSPACE_COLORS["border"],
            highlightcolor=INKSPACE_COLORS["brand_border"],
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda _event: self._draw_map())

        controls = ttk.Frame(root)
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Ego lane").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Combobox(
            controls,
            textvariable=self.lane_var,
            values=("Left", "Middle", "Right"),
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(controls, textvariable=self.detail_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.accept_button = ttk.Button(controls, text="Use This Spawn", command=self._accept, state="disabled")
        self.accept_button.grid(row=0, column=3, sticky="e", padx=(8, 0))
        ttk.Button(controls, text="Cancel", command=self.top.destroy).grid(row=0, column=4, sticky="e", padx=(8, 0))

    def _world_to_canvas(self, x, y):
        min_x, min_y, max_x, max_y = self.data["bounds"]
        width = max(10, self.canvas.winfo_width())
        height = max(10, self.canvas.winfo_height())
        pad = 18.0
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        scale = min((width - pad * 2.0) / span_x, (height - pad * 2.0) / span_y)
        used_w = span_x * scale
        used_h = span_y * scale
        off_x = (width - used_w) / 2.0
        off_y = (height - used_h) / 2.0
        cx = off_x + (float(x) - min_x) * scale
        cy = off_y + (max_y - float(y)) * scale
        return cx, cy

    def _canvas_to_world(self, cx, cy):
        min_x, min_y, max_x, max_y = self.data["bounds"]
        width = max(10, self.canvas.winfo_width())
        height = max(10, self.canvas.winfo_height())
        pad = 18.0
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        scale = min((width - pad * 2.0) / span_x, (height - pad * 2.0) / span_y)
        used_w = span_x * scale
        used_h = span_y * scale
        off_x = (width - used_w) / 2.0
        off_y = (height - used_h) / 2.0
        x = min_x + (float(cx) - off_x) / scale
        y = max_y - (float(cy) - off_y) / scale
        return x, y

    def _draw_map(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        for candidate in self.data.get("candidates", []):
            coords = []
            for x, y in candidate.polygon:
                coords.extend(self._world_to_canvas(x, y))
            if len(coords) < 6:
                continue
            if candidate.lane_type == "shoulder":
                fill = "#b7b7b0"
                outline = "#9b9b94"
            elif candidate.selectable:
                fill = "#3b3d40"
                outline = "#292b2e"
            else:
                fill = "#56585b"
                outline = "#45474a"
            self.canvas.create_polygon(coords, fill=fill, outline=outline, width=1)
        self._draw_selection_marker()

    def _select_from_spawn(self, spawn_xodr, initial_lane):
        road_id = str(spawn_xodr.get("road_id", ""))
        lane_id = int(spawn_xodr.get("lane_id", 0) or 0)
        road_s = float(spawn_xodr.get("s", 0.0) or 0.0)
        best = None
        best_d = None
        for candidate in self.data.get("candidates", []):
            if str(candidate.road_id) != road_id or int(candidate.lane_id) != lane_id:
                continue
            for sx, sy, sample_s in candidate.samples:
                distance = abs(sample_s - road_s)
                if best_d is None or distance < best_d:
                    best_d = distance
                    best = (candidate, sx, sy, sample_s, 0.0)
        if best is not None:
            self._set_selection(best, requested_s=road_s, override_lane=initial_lane)

    def _on_click(self, event):
        x, y = self._canvas_to_world(event.x, event.y)
        nearest = nearest_xodr_lane(self.data, x, y)
        if nearest is None:
            self.status_var.set("No driving lane found near click.")
            return
        self._set_selection(nearest)

    def _set_selection(self, nearest, requested_s=None, override_lane=None):
        candidate, sx, sy, road_s, distance_m = nearest
        if requested_s is not None:
            road_s = float(requested_s)
        scenario_lane = candidate.scenario_lane
        self.selection = {
            "map_name": self.map_name,
            "road_id": int(candidate.road_id),
            "lane_id": int(candidate.lane_id),
            "s": float(road_s),
            "x": float(sx),
            "y": float(sy),
            "ego_lane": str(override_lane or scenario_lane or self.lane_var.get() or "Middle"),
            "selectable": bool(candidate.selectable),
        }
        if scenario_lane:
            self.lane_var.set(str(override_lane or scenario_lane))
        self._draw_selection_marker()
        self._sync_selection_text(distance_m)

    def _draw_selection_marker(self):
        if not hasattr(self, "canvas"):
            return
        for item in self.marker_items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self.marker_items = []
        if not self.selection:
            return
        cx, cy = self._world_to_canvas(self.selection["x"], self.selection["y"])
        radius = 6
        self.marker_items.append(
            self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill="#d5272f", outline="#8b1118", width=2)
        )

    def _sync_selection_text(self, distance_m=0.0):
        if not self.selection:
            return
        self.selection["ego_lane"] = self.lane_var.get()
        detail = "road={road_id} lane_id={lane_id} s={s:.2f}m -> ego lane={ego_lane}".format(**self.selection)
        if not self.selection.get("selectable"):
            detail += "  (this OpenDRIVE lane is outside the supported Left/Middle/Right driving-lane set)"
            self.status_var.set("Pick one of the main driving lanes, not shoulder/outer ignored lane.")
            if self.accept_button is not None:
                self.accept_button.configure(state="disabled")
        else:
            self.status_var.set("Selected spawn from map; click Use This Spawn.")
            if self.accept_button is not None:
                self.accept_button.configure(state="normal")
        if distance_m:
            detail += "  click distance={:.1f}m".format(distance_m)
        self.detail_var.set(detail)

    def _accept(self):
        if not self.selection:
            return
        self.selection["ego_lane"] = self.lane_var.get()
        if self.on_accept is not None:
            self.on_accept(dict(self.selection))
        self.top.destroy()


def parse_custom_marker_text(value):
    markers = []
    text = str(value or "").strip()
    if not text:
        return markers
    for segment in text.replace("\n", ",").split(","):
        segment = segment.strip()
        if not segment:
            continue
        if ":" in segment:
            time_text, event = segment.split(":", 1)
        elif "=" in segment:
            time_text, event = segment.split("=", 1)
        else:
            continue
        try:
            time_s = max(0.0, float(time_text.strip()))
        except Exception:
            continue
        event = event.strip().upper().replace(" ", "_")
        if event:
            markers.append({"time_s": time_s, "event": event})
    return markers


def format_custom_marker_text(markers):
    parts = []
    for marker in markers or []:
        if not isinstance(marker, dict):
            continue
        event = str(marker.get("event") or "").strip()
        if not event:
            continue
        try:
            time_s = float(marker.get("time_s", 0.0) or 0.0)
        except Exception:
            time_s = 0.0
        parts.append("{:.3g}:{}".format(time_s, event))
    return ", ".join(parts)


def first_experiment_tor_window(payload):
    events = payload.get("experiment_events") if isinstance(payload, dict) else {}
    windows = events.get("tor_windows") if isinstance(events, dict) else []
    if windows and isinstance(windows[0], dict):
        return windows[0]
    return {}


def scenario_requires_tor(payload):
    """True when the scenario JSON declares any TOR interaction
    (experiment_events.tor_windows, tor_alerts, or takeover_requests)."""
    if not isinstance(payload, dict):
        return False
    if payload.get("takeover_requests") or payload.get("tor_alerts"):
        return True
    events = payload.get("experiment_events")
    return bool(isinstance(events, dict) and events.get("tor_windows"))


VIS_LANE_ORDER = ("Left", "Middle", "Right")
VIS_LANE_Y = {"Left": 0.0, "Middle": 1.0, "Right": 2.0}
VIS_LANE_COLOR = {
    "ego": "#2563eb",
    "vehicle": "#f97316",
    "decision": "#7c3aed",
    "hazard": "#dc2626",
    "motion": "#059669",
    "pedestrian": "#5eead4",
}


def visual_float(value, default=0.0):
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def visual_sample_at(samples, t):
    """Interpolate a vehicle sample list (t, s, lane_y, speed) at time ``t``."""
    if not samples:
        return None
    if t <= samples[0]["t"]:
        return samples[0]
    for index in range(1, len(samples)):
        left = samples[index - 1]
        right = samples[index]
        if t <= right["t"]:
            span = max(0.001, right["t"] - left["t"])
            blend = max(0.0, min(1.0, (t - left["t"]) / span))
            return {
                "t": t,
                "s": left["s"] + (right["s"] - left["s"]) * blend,
                "lane_y": left["lane_y"] + (right["lane_y"] - left["lane_y"]) * blend,
                "speed_kmh": left["speed_kmh"] + (right["speed_kmh"] - left["speed_kmh"]) * blend,
            }
    return samples[-1]


def build_scenario_visual_obstacles(payload, vehicle_models, total_time_s):
    """Build drawable obstacle markers from the scenario payload.

    Fixed obstacles sit at their ``gap_m`` longitudinal position; obstacles
    dropped from a vehicle inherit the source vehicle's position/lane at the
    drop time so they render where they would actually appear.
    """
    obstacles = payload.get("obstacles") if isinstance(payload.get("obstacles"), dict) else {}
    by_role = {vehicle["role"]: vehicle for vehicle in vehicle_models}
    models = []
    for role in sorted(obstacles):
        cfg = obstacles.get(role)
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        lane = cfg.get("lane", "Middle")
        if lane not in VIS_LANE_Y:
            lane = "Middle"
        start = max(0.0, visual_float(cfg.get("start_time_s"), 0.0))
        end = max(start, visual_float(cfg.get("end_time_s"), total_time_s))
        spawn_mode = cfg.get("spawn_mode", "fixed")
        s_pos = visual_float(cfg.get("gap_m"), 0.0)
        lane_y = VIS_LANE_Y[lane]
        if spawn_mode == "fixed" and cfg.get("position_reference") == "vehicle_at_spawn":
            from carla_nl.obstacle_positions import planned_obstacle_position
            s_pos = planned_obstacle_position(cfg, payload.get("vehicles", {}), payload.get("ego_speed_kmh", 50))
        if spawn_mode == "drop_from_vehicle":
            source = by_role.get(cfg.get("drop_source_role") or "vehicle_1")
            if source is not None:
                sample = visual_sample_at(source.get("samples"), start)
                if sample is not None:
                    s_pos = sample["s"]
                    lane_y = sample["lane_y"]
        models.append({
            "role": role,
            "label": str(cfg.get("label") or cfg.get("kind") or "obstacle"),
            "kind": str(cfg.get("kind") or "obstacle"),
            "lane": lane,
            "lane_y": lane_y,
            "s": s_pos,
            "start_time_s": start,
            "end_time_s": end,
            "spawn_mode": spawn_mode,
            "drop_source_role": str(cfg.get("drop_source_role") or ""),
            "position_reference": cfg.get("position_reference", "initial_ego"),
            "reference_role": cfg.get("reference_role", ""),
            "gap_m": cfg.get("gap_m", 0),
            "time_gap_s": cfg.get("time_gap_s"),
        })
    return models


def visual_lane_name(value, default="Middle"):
    text = str(value or default).strip().title()
    return text if text in VIS_LANE_Y else default


def build_scenario_visual_pedestrians(payload, ego_samples):
    """Plan a fixed road crossing using ego's estimated position at its start."""
    pedestrians = payload.get("pedestrians") or {}
    models = []
    for role, cfg in pedestrians.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        start = max(0.0, visual_float(cfg.get("start_time_s")))
        duration = max(0.001, visual_float(cfg.get("duration_s"), 4.0))
        reference = cfg.get("position_reference", "initial_ego")
        s_pos = visual_float(cfg.get("gap_m"))
        if reference == "ego_at_crossing":
            s_pos += visual_sample_s(ego_samples, start)
        start_side = str(cfg.get("start_side", "Left")).title()
        end_side = str(cfg.get("end_side", "Right")).title()
        models.append({
            "role": role,
            "label": str(role).replace("pedestrian_", "ped"),
            "s": s_pos,
            "start_time_s": start,
            "end_time_s": start + duration,
            "start_side": start_side,
            "end_side": end_side,
            "start_lane_y": -0.7 if start_side == "Left" else 2.7,
            "end_lane_y": -0.7 if end_side == "Left" else 2.7,
            "position_reference": reference,
        })
    return models


def visual_obstacle_phase(obstacle, time_s):
    if time_s < obstacle["start_time_s"]:
        return "scheduled"
    return "active" if time_s < obstacle["end_time_s"] else "ended"


def visual_pedestrian_at(pedestrian, t):
    start, end = pedestrian["start_time_s"], pedestrian["end_time_s"]
    progress = max(0.0, min(1.0, (float(t) - start) / max(0.001, end - start)))
    return {
        "s": pedestrian["s"],
        "lane_y": pedestrian["start_lane_y"] + progress * (
            pedestrian["end_lane_y"] - pedestrian["start_lane_y"]),
        "progress": progress,
        "phase": "scheduled" if t < start else "crossing" if t < end else "complete",
    }


def visual_role_sort_key(role):
    role = str(role or "")
    if role == "ego":
        return (0, 0, role)
    if role.startswith("vehicle_"):
        try:
            return (1, int(role.split("_", 1)[1]), role)
        except Exception:
            pass
    return (2, 0, role)


def visual_vehicle_label(role):
    role = str(role or "")
    if role == "ego":
        return "ego"
    if role.startswith("vehicle_"):
        suffix = role.split("_", 1)[1]
        if suffix.isdigit():
            return "v{}".format(suffix)
    return role


def visual_sorted_points(points, value_key):
    out = []
    for point in list(points or []):
        if not isinstance(point, dict):
            continue
        out.append((visual_float(point.get("time_s"), 0.0), visual_float(point.get(value_key), 0.0)))
    out.sort(key=lambda item: item[0])
    return out


def visual_value_at(points, t, default=0.0):
    if not points:
        return float(default)
    t = float(t)
    first_t, first_value = points[0]
    if t < first_t and first_t > 0.0:
        return float(default)
    if t <= first_t:
        return first_value
    for index in range(1, len(points)):
        t0, v0 = points[index - 1]
        t1, v1 = points[index]
        if t <= t1:
            if abs(t1 - t0) < 1e-6:
                return v1
            blend = max(0.0, min(1.0, (t - t0) / (t1 - t0)))
            return v0 + (v1 - v0) * blend
    return points[-1][1]


def visual_times(total_time_s, dt=0.5):
    total = max(0.0, float(total_time_s or 0.0))
    step = max(0.05, float(dt or 0.5))
    times = []
    t = 0.0
    while t < total:
        times.append(round(t, 4))
        t += step
    if not times or abs(times[-1] - total) > 1e-6:
        times.append(round(total, 4))
    return times


def visual_sample_s(samples, t):
    if not samples:
        return 0.0
    t = float(t)
    if t <= samples[0]["t"]:
        return samples[0]["s"]
    for index in range(1, len(samples)):
        left = samples[index - 1]
        right = samples[index]
        if t <= right["t"]:
            span = right["t"] - left["t"]
            if abs(span) < 1e-6:
                return right["s"]
            blend = max(0.0, min(1.0, (t - left["t"]) / span))
            return left["s"] + (right["s"] - left["s"]) * blend
    return samples[-1]["s"]


def visual_lane_y_at(cfg, t):
    current = visual_lane_name(cfg.get("lane", "Middle"))
    changes = []
    points = cfg.get("lane_change_points")
    if isinstance(points, list) and points:
        changes = [point for point in points if isinstance(point, dict)]
    elif cfg.get("lane_change_enabled"):
        changes = [{
            "time_s": cfg.get("lane_change_time_s", 0.0),
            "target_lane": cfg.get("lane_change_target_lane", current),
            "duration_s": cfg.get("lane_change_duration_s", 3.0),
        }]
    changes.sort(key=lambda point: visual_float(point.get("time_s"), 0.0))
    t = float(t)
    for point in changes:
        start = visual_float(point.get("time_s"), 0.0)
        duration = max(0.001, visual_float(point.get("duration_s"), 3.0))
        target = visual_lane_name(point.get("target_lane"), current)
        if t < start:
            break
        if t <= start + duration:
            blend = max(0.0, min(1.0, (t - start) / duration))
            return VIS_LANE_Y[current] + (VIS_LANE_Y[target] - VIS_LANE_Y[current]) * blend
        current = target
    return VIS_LANE_Y[current]


# the runtime treats a prompt time gap as bumper-to-bumper and adds half a
# vehicle length per car when converting to center distance; mirror it so the
# preview (and the click-editor's prefilled gaps) match runtime positions.
# Keep in sync with DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M in run_scenario_dreyevr.
VIS_TIME_GAP_BUMPER_CLEARANCE_M = 4.5


def visual_time_gap_center_delta_m(time_gap_s, ego_speed_mps):
    """Signed center-to-center delta for a bumper time gap (runtime formula)."""
    bumper_m = visual_float(time_gap_s, 0.0) * max(0.001, ego_speed_mps)
    if bumper_m > 0.0:
        return bumper_m + VIS_TIME_GAP_BUMPER_CLEARANCE_M
    if bumper_m < 0.0:
        return bumper_m - VIS_TIME_GAP_BUMPER_CLEARANCE_M
    return 0.0


def visual_center_delta_to_time_gap_s(center_delta_m, ego_speed_mps):
    """Inverse of :func:`visual_time_gap_center_delta_m` (0 inside the overlap band)."""
    delta = visual_float(center_delta_m, 0.0)
    magnitude = max(0.0, abs(delta) - VIS_TIME_GAP_BUMPER_CLEARANCE_M)
    if magnitude <= 0.0:
        return 0.0
    gap = magnitude / max(0.001, ego_speed_mps)
    return gap if delta >= 0.0 else -gap


def visual_vehicle_initial_s(role, cfg, ego_speed_mps):
    if role == "ego":
        return visual_float(cfg.get("initial_gap_m"), 0.0)
    if cfg.get("initial_time_gap_s") not in (None, ""):
        return visual_time_gap_center_delta_m(cfg.get("initial_time_gap_s"), ego_speed_mps)
    return visual_float(cfg.get("initial_gap_m"), 0.0)


def visual_integrated_samples(role, cfg, total_time_s, times, ego_speed_mps):
    from carla_nl.prompt_speed_events import planned_distance_m
    from carla_nl.vehicle_motion import motion_config
    cfg = motion_config(cfg)
    speed_points = visual_sorted_points(cfg.get("speed_points", []), "speed_kmh")
    base_speed_kmh = visual_float(cfg.get("speed_kmh"), 50.0)
    if speed_points and speed_points[0][0] <= 0.0:
        base_speed_kmh = speed_points[0][1]
    if not speed_points or speed_points[0][0] > 0.0:
        speed_points.insert(0, (0.0, base_speed_kmh))
    distance_cfg = {"speed_kmh": base_speed_kmh,
                    "speed_points": [{"time_s": t, "speed_kmh": speed} for t, speed in speed_points]}
    samples = []
    initial_s = visual_vehicle_initial_s(role, cfg, ego_speed_mps)
    for t in times:
        t = float(t)
        samples.append({
            "t": t,
            "s": initial_s + planned_distance_m(distance_cfg, t),
            "lane_y": visual_lane_y_at(cfg, t),
            "speed_kmh": visual_value_at(speed_points, t, base_speed_kmh),
        })
    return samples


def visual_relative_samples(role, cfg, times, ego_samples, ego_speed_mps):
    gap_points = visual_sorted_points(cfg.get("gap_points", []), "gap_m")
    time_gap_points = visual_sorted_points(cfg.get("time_gap_points", []), "time_gap_s")
    if time_gap_points:
        default_gap = cfg.get("initial_time_gap_s")
        if default_gap in (None, ""):
            default_gap = visual_float(cfg.get('initial_gap_m'), 0.0) / max(.001, ego_speed_mps)
        if time_gap_points[0][0] > 0:
            time_gap_points.insert(0, (0.0, visual_float(default_gap, 0.0)))
        samples = []
        for t in times:
            t = float(t)
            gap_s = visual_value_at(time_gap_points, t, visual_float(default_gap, 0.0))
            s = visual_sample_s(ego_samples, t) + visual_time_gap_center_delta_m(gap_s, ego_speed_mps)
            samples.append({
                "t": t,
                "s": s,
                "lane_y": visual_lane_y_at(cfg, t),
                "speed_kmh": visual_float(cfg.get("speed_kmh"), 50.0),
            })
        return samples
    if gap_points:
        default_gap = visual_vehicle_initial_s(role, cfg, ego_speed_mps)
        if gap_points[0][0] > 0:
            gap_points.insert(0, (0.0, default_gap))
        samples = []
        for t in times:
            t = float(t)
            gap_m = visual_value_at(gap_points, t, default_gap)
            samples.append({
                "t": t,
                "s": visual_sample_s(ego_samples, t) + gap_m,
                "lane_y": visual_lane_y_at(cfg, t),
                "speed_kmh": visual_float(cfg.get("speed_kmh"), 50.0),
            })
        return samples
    return None


def build_scenario_visual_model(payload):
    payload = payload if isinstance(payload, dict) else {}
    total_time_s = max(0.1, visual_float(payload.get("total_time_s"), 20.0))
    ego_speed_kmh = visual_float(payload.get("ego_speed_kmh"), 50.0)
    ego_speed_mps = max(0.001, ego_speed_kmh / 3.6)
    vehicles = payload.get("vehicles") if isinstance(payload.get("vehicles"), dict) else {}
    ego_cfg = dict(vehicles.get("ego", {}) or {})
    ego_cfg.setdefault("role", "ego")
    ego_cfg.setdefault("enabled", True)
    ego_cfg.setdefault("lane", "Middle")
    ego_cfg.setdefault("speed_kmh", ego_speed_kmh)
    times = visual_times(total_time_s, dt=0.5)
    if any(ped.get("enabled") for ped in (payload.get("pedestrians") or {}).values()):
        # Split at speed knots and crossing starts so interpolation at an
        # off-grid crossing start preserves the authored distance exactly.
        extra_times = [visual_float(ped.get("start_time_s"))
                       for ped in payload["pedestrians"].values() if ped.get("enabled")]
        extra_times.extend(t for t, _speed in visual_sorted_points(ego_cfg.get("speed_points", []), "speed_kmh"))
        times = sorted(set(times + [t for t in extra_times if 0 <= t <= total_time_s]))
    ego_samples = visual_integrated_samples("ego", ego_cfg, total_time_s, times, ego_speed_mps)
    vehicle_models = [{
        "role": "ego",
        "label": "ego",
        "cfg": ego_cfg,
        "samples": ego_samples,
        "color": VIS_LANE_COLOR["ego"],
    }]
    for role in sorted(vehicles, key=visual_role_sort_key):
        if role == "ego":
            continue
        cfg = vehicles.get(role)
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        samples = visual_relative_samples(role, cfg, times, ego_samples, ego_speed_mps)
        if samples is None:
            samples = visual_integrated_samples(role, cfg, total_time_s, times, ego_speed_mps)
        vehicle_models.append({
            "role": role,
            "label": visual_vehicle_label(role),
            "cfg": cfg,
            "samples": samples,
            "color": VIS_LANE_COLOR["vehicle"],
        })
    events, decisions = build_scenario_visual_events(payload, vehicle_models, total_time_s, ego_speed_mps)
    decision_tree = build_scenario_visual_decision_tree(payload, decisions)
    obstacle_models = build_scenario_visual_obstacles(payload, vehicle_models, total_time_s)
    pedestrian_models = build_scenario_visual_pedestrians(payload, ego_samples)
    all_s = []
    for vehicle in vehicle_models:
        all_s.extend(sample["s"] for sample in vehicle["samples"])
    all_s.extend(obstacle["s"] for obstacle in obstacle_models)
    all_s.extend(pedestrian["s"] for pedestrian in pedestrian_models)
    min_s = min(all_s) if all_s else -20.0
    max_s = max(all_s) if all_s else 120.0
    span = max(60.0, max_s - min_s)
    pad = max(20.0, span * 0.08)
    return {
        "scenario_name": payload.get("scenario_name", "scenario"),
        "total_time_s": total_time_s,
        "ego_speed_kmh": ego_speed_kmh,
        "times": times,
        "vehicles": vehicle_models,
        "obstacles": obstacle_models,
        "pedestrians": pedestrian_models,
        "events": sorted(events, key=lambda item: (item["time_s"], item["order"], item["label"])),
        "conditional_events": build_conditional_cue_events(payload),
        "decisions": decisions,
        "decision_tree": decision_tree,
        "min_s": min_s - pad,
        "max_s": max_s + pad,
    }


def visual_add_event(events, time_s, category, label, details="", branch_id=None, order=50):
    events.append({
        "time_s": max(0.0, visual_float(time_s, 0.0)),
        "category": category,
        "label": str(label or ""),
        "details": str(details or ""),
        "branch_id": branch_id,
        "order": int(order),
    })


def visual_add_decision(decisions, time_s, title, requirement, yes_text, no_text):
    branch_id = "decision_{}".format(len(decisions) + 1)
    decisions.append({
        "id": branch_id,
        "time_s": max(0.0, visual_float(time_s, 0.0)),
        "title": str(title or ""),
        "requirement": str(requirement or ""),
        "yes": str(yes_text or ""),
        "no": str(no_text or ""),
    })
    return branch_id


def visual_find_decision(decisions, title):
    for decision in decisions:
        if decision.get("title") == title:
            return decision
    return None


def visual_tree_node(title, time_s=0.0, rule="", summary="", branch_id=None, children=None, meta=None):
    return {
        "title": str(title or ""),
        "time_s": max(0.0, visual_float(time_s, 0.0)),
        "rule": str(rule or ""),
        "summary": str(summary or ""),
        "branch_id": branch_id,
        "meta": dict(meta or {}),
        "children": list(children or []),
    }


def build_scenario_visual_decision_tree(payload, decisions):
    experiment = payload.get("experiment_events") if isinstance(payload.get("experiment_events"), dict) else {}
    roots = []
    for window_index, window in enumerate(experiment.get("tor_windows", []) or []):
        if not isinstance(window, dict):
            continue
        fields = dict(window.get("fields", {}) or {})
        fields.update(adaptive_fields_from_mapping(fields))
        onset = visual_float(window.get("alert_onset_s", window.get("tor_onset_s", 0.0)), 0.0)
        deadline = visual_float(window.get("tor_deadline_s"), onset + 6.0)
        name = str(window.get("name") or "tor")
        stage1_req = str(fields.get("stage1_requirement", "gaze_screen"))
        stage1_delay = visual_float(fields.get("stage1_delay_s"), 0.0)
        stage2_req = str(fields.get("stage2_requirement", "target_fixation"))
        stage2_delay = visual_float(fields.get("stage2_delay_s"), 0.0)
        fixation_s = visual_float(fields.get("target_fixation_s"), 0.5)
        manual_req = str(fields.get("manual_requires", "none"))
        failure_action = str(window.get("failure_action", "brake"))
        window_meta = {"window_index": window_index}

        def meta(kind):
            data = dict(window_meta)
            data["kind"] = kind
            return data

        stage1_decision = visual_find_decision(decisions, "{}: Stage1 -> Stage2".format(name))
        stage2_decision = visual_find_decision(decisions, "{}: Stage2 -> Ready".format(name))
        manual_decision = visual_find_decision(decisions, "{}: Manual response".format(name))
        estimated_stage2 = onset + (stage1_delay if stage1_req in ("delay", "none") else 0.0)
        estimated_ready = estimated_stage2 + (stage2_delay if stage2_req == "delay" else 0.0)
        stage2_rule = stage2_req
        if stage2_req == "target_fixation":
            stage2_rule = "{} {:.2f}s".format(stage2_req, fixation_s)

        manual_node = visual_tree_node(
            "Manual response gate",
            onset,
            "manual_requires={}".format(manual_req),
            "W/A/S/D response is accepted only when this rule is satisfied.",
            branch_id=manual_decision.get("id") if manual_decision else None,
            meta=meta("manual"),
            children=[
                visual_tree_node("YES: TOR_SUCCESS -> AUTO_OFF", onset, summary="Participant takes manual control.", meta=meta("manual")),
                visual_tree_node("NO/early: blocked or keep waiting", onset, summary="Invalid early key is blocked; no valid key by deadline triggers fallback.", meta=meta("manual")),
            ],
        )
        stage2_node = visual_tree_node(
            "Stage2 gate",
            estimated_stage2,
            stage2_rule,
            "Checks whether the participant has completed the second alert requirement.",
            branch_id=stage2_decision.get("id") if stage2_decision else None,
            meta=meta("stage2"),
            children=[
                visual_tree_node("YES: TOR_READY", estimated_ready, summary="Ready prompt becomes active.", children=[manual_node], meta=meta("stage2")),
                visual_tree_node("NO: stay in Stage2", estimated_stage2, summary="Keep waiting until the deadline.", meta=meta("stage2")),
            ],
        )
        stage1_node = visual_tree_node(
            "Stage1 gate",
            onset,
            stage1_req,
            "Initial alert stage; decides when Stage2 can start.",
            branch_id=stage1_decision.get("id") if stage1_decision else None,
            meta=meta("stage1"),
            children=[
                visual_tree_node("YES: enter Stage2", estimated_stage2, summary="AOI target and Stage2 prompt are enabled.", children=[stage2_node], meta=meta("stage1")),
                visual_tree_node("NO: stay in Stage1", onset, summary="Stage2 is not active yet.", meta=meta("stage1")),
            ],
        )
        roots.append(
            visual_tree_node(
                "{} TOR window".format(name),
                onset,
                "deadline={:.2f}s".format(deadline),
                "Failure action at deadline: {}".format(failure_action),
                meta=meta("window"),
                children=[
                    stage1_node,
                    visual_tree_node("Deadline missed", deadline, failure_action, "No valid takeover before deadline.", meta=meta("deadline")),
                ],
            )
        )
    return roots


def build_conditional_cue_events(payload):
    from carla_nl.standalone_cues import describe_cue
    return [dict(time_s=None, category='alert', label='Standalone ' + cue['kind'],
                 condition='TTC <= {:g}s to {}'.format(cue['trigger']['threshold_s'], cue['trigger']['target_role']),
                 details=describe_cue(cue))
            for cue in payload.get('standalone_cues', []) if cue.get('trigger')]


def build_scenario_visual_events(payload, vehicle_models, total_time_s, ego_speed_mps):
    events = []
    decisions = []
    for obstacle in build_scenario_visual_obstacles(payload, vehicle_models, total_time_s):
        details = "{} · {} lane · {:g}m".format(obstacle["kind"], obstacle["lane"], obstacle["s"])
        visual_add_event(events, obstacle["start_time_s"], "obstacle", obstacle["role"] + " appears", details, order=13)
        visual_add_event(events, obstacle["end_time_s"], "obstacle", obstacle["role"] + " removed", details, order=12)
    for cue in payload.get('standalone_cues', []):
        if cue.get('trigger'):
            continue  # Live TTC has no known position on the fixed timeline.
        detail = cue.get('text') or cue.get('target_role') or 'beep'
        visual_add_event(events, cue['time_s'], 'alert', 'Standalone ' + cue['kind'], detail, order=14)
        visual_add_event(events, cue['time_s'] + cue['duration_s'], 'alert', cue['kind'] + ' ends', order=15)
    for role, ped in payload.get('pedestrians', {}).items():
        if ped.get('enabled'):
            reference = ('relative to ego at crossing start' if ped.get('position_reference') == 'ego_at_crossing'
                         else 'from initial ego position')
            visual_add_event(events, ped['start_time_s'], 'pedestrian', role + ' crossing',
                             '{} -> {}, {:g}m {}'.format(ped['start_side'], ped['end_side'], ped['gap_m'], reference))
            visual_add_event(events, ped['start_time_s'] + ped['duration_s'], 'pedestrian', role + ' crossing ends')
    for vehicle in vehicle_models:
        role = vehicle["role"]
        cfg = vehicle["cfg"]
        label = vehicle["label"]
        visual_add_event(
            events,
            0.0,
            "vehicle",
            "{} start".format(label),
            "lane={} speed={}km/h s={:.1f}m".format(
                cfg.get("lane", "Middle"),
                format_prompt_number(cfg.get("speed_kmh", 0.0)),
                vehicle["samples"][0]["s"] if vehicle["samples"] else 0.0,
            ),
            order=5 if role == "ego" else 10,
        )
        for point in _lane_change_points_for_prompt(cfg):
            start = visual_float(point.get("time_s"), 0.0)
            duration = max(0.001, visual_float(point.get("duration_s"), 3.0))
            target = point.get("target_lane", cfg.get("lane", "Middle"))
            visual_add_event(events, start, "lane", "{} lane change -> {}".format(label, target), "duration={:.2f}s".format(duration), order=20)
            visual_add_event(events, start + duration, "lane", "{} lane change end".format(label), "now in {}".format(target), order=21)
        for point in cfg.get("time_gap_points", []) or []:
            if isinstance(point, dict):
                visual_add_event(
                    events,
                    point.get("time_s", 0.0),
                    "gap",
                    "{} time gap -> {}s".format(label, format_prompt_number(point.get("time_gap_s", 0.0))),
                    "visualized as ego_s + time_gap * ego_speed",
                    order=30,
                )
        for point in cfg.get("speed_points", []) or []:
            if isinstance(point, dict):
                visual_add_event(
                    events,
                    point.get("time_s", 0.0),
                    "speed",
                    "{} speed -> {}km/h".format(label, format_prompt_number(point.get("speed_kmh", 0.0))),
                    "longitudinal position is integrated from this speed profile",
                    order=31,
                )
        if cfg.get("stop_enabled"):
            visual_add_event(events, cfg.get("stop_time_s", 0.0), "speed", "{} stop".format(label), "duration={}s".format(cfg.get("stop_duration_s", 0.0)), order=32)

    experiment = payload.get("experiment_events") if isinstance(payload.get("experiment_events"), dict) else {}
    for hazard in experiment.get("hazards", []) or []:
        if not isinstance(hazard, dict):
            continue
        visual_add_event(events, hazard.get("onset_s", 0.0), "hazard", "Hazard onset", str(hazard.get("name", "hazard")), order=15)
        if hazard.get("end_s") not in (None, ""):
            visual_add_event(events, hazard.get("end_s", 0.0), "hazard", "Hazard end", str(hazard.get("name", "hazard")), order=16)
    for marker in experiment.get("custom_markers", []) or []:
        if not isinstance(marker, dict):
            continue
        visual_add_event(events, marker.get("time_s", 0.0), "marker", marker.get("event", "CUSTOM_MARKER"), "custom experiment marker", order=70)
    for window in experiment.get("tor_windows", []) or []:
        if not isinstance(window, dict):
            continue
        fields = dict(window.get("fields", {}) or {})
        fields.update(adaptive_fields_from_mapping(fields))
        onset = visual_float(window.get("alert_onset_s", window.get("tor_onset_s", 0.0)), 0.0)
        deadline = visual_float(window.get("tor_deadline_s"), onset + 6.0)
        name = str(window.get("name") or "tor")
        stage1_req = str(fields.get("stage1_requirement", "gaze_screen"))
        stage1_delay = visual_float(fields.get("stage1_delay_s"), 0.0)
        stage2_req = str(fields.get("stage2_requirement", "target_fixation"))
        stage2_delay = visual_float(fields.get("stage2_delay_s"), 0.0)
        fixation_s = visual_float(fields.get("target_fixation_s"), 0.5)
        manual_req = str(fields.get("manual_requires", "none"))
        branch_id = visual_add_decision(
            decisions,
            onset,
            "{}: Stage1 -> Stage2".format(name),
            stage1_req,
            "If requirement is met: ALERT_STAGE2_ONSET, AOI target appears, stage2 speech plays.",
            "If not met: stays in Stage1; later manual response may be blocked depending on manual rule.",
        )
        visual_add_event(events, onset, "decision", "TOR alert / Stage1", "deadline={:.2f}s requirement={}".format(deadline, stage1_req), branch_id=branch_id, order=1)
        estimated_stage2 = onset + (stage1_delay if stage1_req in ("delay", "none") else 0.0)
        if stage1_req in ("delay", "none"):
            visual_add_event(events, estimated_stage2, "decision", "Stage2 earliest onset", "stage1 requirement={}".format(stage1_req), branch_id=branch_id, order=2)
        branch_id = visual_add_decision(
            decisions,
            estimated_stage2,
            "{}: Stage2 -> Ready".format(name),
            stage2_req,
            "If requirement is met: TOR_READY, ready speech is sent, final takeover request is active.",
            "If not met or fixation breaks: remains in Stage2 until the deadline.",
        )
        detail = "requirement={}".format(stage2_req)
        if stage2_req == "target_fixation":
            detail += " fixation={:.2f}s".format(fixation_s)
        visual_add_event(events, estimated_stage2, "decision", "Stage2 gate", detail, branch_id=branch_id, order=3)
        if stage2_req == "delay":
            visual_add_event(events, estimated_stage2 + stage2_delay, "decision", "TOR ready earliest", "stage2 delay={:.2f}s".format(stage2_delay), branch_id=branch_id, order=4)
        branch_id = visual_add_decision(
            decisions,
            onset,
            "{}: Manual response".format(name),
            "manual_requires={}".format(manual_req),
            "If requirement is satisfied before deadline: TOR_SUCCESS, AUTO_OFF, manual takeover starts.",
            "If key is pressed too early: TOR_RESPONSE_BLOCKED; if no valid key by deadline: failure action.",
        )
        visual_add_event(events, onset, "decision", "Manual gate", "keys={} requirement={}".format("/".join(window.get("manual_keys") or ["W", "A", "S", "D"]), manual_req), branch_id=branch_id, order=6)
        visual_add_event(events, deadline, "decision", "TOR deadline", "missed => {}".format(window.get("failure_action", "brake")), branch_id=branch_id, order=7)
    return events, decisions


class ScenarioDesignVisualizer(object):
    def __init__(self, owner, item):
        import tkinter as tk
        from tkinter import ttk

        self.owner = owner
        self.tk = tk
        self.ttk = ttk
        self.item = item
        self.source_prompt = get_scenario_prompt(item.path)
        self.payload = read_prompt_json_summary(item.path)
        if not self.payload:
            raise RuntimeError("Scenario JSON could not be read: {}".format(item.path))
        self.model = build_scenario_visual_model(self.payload)
        self.current_t = 0.0
        self.selected_event = None
        self.selected_decision_node = None
        self.decision_tree_nodes = {}
        self._syncing_selection = False
        self.edit_window_index = None
        self.vehicle_canvas_hits = []
        self.selected_vehicle_role = "ego"
        self.playing = False
        self.top = tk.Toplevel(owner.root)
        self.top.title("Scenario Design Visualizer - {}".format(item.name))
        self.top.geometry("1220x760")
        self.top.minsize(980, 620)
        configure_inkspace_window(self.top)
        enable_window_maximize(self.top)
        self.edit_alert_onset_var = tk.StringVar(value="")
        self.edit_deadline_var = tk.StringVar(value="")
        self.edit_failure_action_var = tk.StringVar(value="brake")
        self.edit_stage1_var = tk.StringVar(value="gaze_screen")
        self.edit_stage1_delay_var = tk.StringVar(value="0")
        self.edit_stage2_var = tk.StringVar(value="target_fixation")
        self.edit_stage2_delay_var = tk.StringVar(value="0")
        self.edit_fixation_var = tk.StringVar(value="0.5")
        self.edit_manual_var = tk.StringVar(value="none")
        self.edit_status_var = tk.StringVar(value="Select a TOR node to edit.")
        self.vehicle_role_var = tk.StringVar(value="ego")
        self.vehicle_lane_var = tk.StringVar(value="Middle")
        self.vehicle_position_mode_var = tk.StringVar(value="time_gap_s")
        self.vehicle_position_value_var = tk.StringVar(value="0")
        self.vehicle_speed_var = tk.StringVar(value=format_prompt_number(self.model.get("ego_speed_kmh", 50.0)))
        self.vehicle_status_var = tk.StringVar(value="Select a vehicle on the map or from the list.")
        self._build_ui()
        self._populate_events()
        self._redraw_all()
        self.top.grab_set()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk
        root = ttk.Frame(self.top, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        title = "{} | {:.1f}s | ego {:.1f}km/h".format(
            self.model["scenario_name"],
            self.model["total_time_s"],
            self.model["ego_speed_kmh"],
        )
        ttk.Label(root, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        panes = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        panes.add(left, weight=3)
        self.scene_canvas = tk.Canvas(
            left,
            bg=INKSPACE_COLORS["field"],
            highlightthickness=1,
            highlightbackground=INKSPACE_COLORS["border"],
            highlightcolor=INKSPACE_COLORS["brand_border"],
        )
        self.scene_canvas.grid(row=0, column=0, sticky="nsew")
        self.scene_canvas.bind("<Configure>", lambda _event: self._redraw_scene())
        self.scene_canvas.bind("<Button-1>", self._on_scene_click)
        self._build_vehicle_editor(left, row=1)

        right = ttk.Frame(panes, padding=(10, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        right.rowconfigure(5, weight=1)
        right.rowconfigure(7, weight=1)
        panes.add(right, weight=2)
        ttk.Label(right, text="Decision / Event Timeline", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.timeline_canvas = tk.Canvas(
            right,
            height=110,
            bg=INKSPACE_COLORS["field"],
            highlightthickness=1,
            highlightbackground=INKSPACE_COLORS["border"],
            highlightcolor=INKSPACE_COLORS["brand_border"],
        )
        self.timeline_canvas.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        self.timeline_canvas.bind("<Button-1>", self._on_timeline_click)
        self.timeline_canvas.bind("<Configure>", lambda _event: self._redraw_timeline())
        decision_header = ttk.Frame(right)
        decision_header.grid(row=2, column=0, sticky="ew")
        decision_header.columnconfigure(0, weight=1)
        ttk.Label(decision_header, text="TOR Decision Flow", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(decision_header, text="Edit Node...", command=self._edit_selected_flow_node).grid(row=0, column=1, sticky="e")
        self.decision_tree = ttk.Treeview(right, columns=("time", "rule"), show="tree headings", height=8)
        self.decision_tree.heading("#0", text="decision path")
        self.decision_tree.heading("time", text="t")
        self.decision_tree.heading("rule", text="rule/action")
        self.decision_tree.column("#0", width=250, stretch=True)
        self.decision_tree.column("time", width=58, stretch=False)
        self.decision_tree.column("rule", width=160, stretch=True)
        self.decision_tree.grid(row=3, column=0, sticky="nsew", pady=(6, 8))
        self.decision_tree.bind("<<TreeviewSelect>>", lambda _event: self._select_decision_tree_node())
        self.decision_tree.bind("<Double-1>", lambda _event: self._edit_selected_flow_node())
        ttk.Label(right, text="Event List", style="Section.TLabel").grid(row=4, column=0, sticky="w")
        self.event_tree = ttk.Treeview(right, columns=("time", "category", "label"), show="headings", height=7)
        self.event_tree.heading("time", text="t")
        self.event_tree.heading("category", text="type")
        self.event_tree.heading("label", text="event")
        self.event_tree.column("time", width=58, stretch=False)
        self.event_tree.column("category", width=82, stretch=False)
        self.event_tree.column("label", width=280, stretch=True)
        self.event_tree.grid(row=5, column=0, sticky="nsew", pady=(6, 8))
        self.event_tree.bind("<<TreeviewSelect>>", lambda _event: self._select_tree_event())
        ttk.Label(right, text="Selected Decision Branch", style="Section.TLabel").grid(row=6, column=0, sticky="sw")
        self.details_text = tk.Text(right, height=8, wrap="word")
        style_inkspace_text(self.details_text)
        self.details_text.grid(row=7, column=0, sticky="nsew", pady=(6, 0))
        self._build_tor_editor(right, row=8)

        controls = ttk.Frame(root)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(3, weight=1)
        self.time_label = ttk.Label(controls, text="t=0.0s")
        self.time_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Button(controls, text="Prev Event", command=lambda: self._step_event(-1)).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(controls, text="Next Event", command=lambda: self._step_event(1)).grid(row=0, column=2, padx=(0, 10))
        self.time_var = tk.DoubleVar(value=0.0)
        self.time_scale = ttk.Scale(
            controls,
            from_=0.0,
            to=self.model["total_time_s"],
            variable=self.time_var,
            command=self._on_time_changed,
        )
        self.time_scale.grid(row=0, column=3, sticky="ew")
        ttk.Button(controls, text="Edit Source Prompt...", command=self._open_source_prompt_editor).grid(
            row=0, column=4, padx=(10, 0)
        )
        ttk.Button(controls, text="Close", command=self.top.destroy).grid(row=0, column=5, padx=(10, 0))

    def _open_source_prompt_editor(self):
        """Step 3 provenance: link the selected timeline mark back to its source
        prompt segment, let the researcher edit it, and regenerate the preview."""
        tk = self.tk
        ttk = self.ttk
        from tkinter import messagebox, scrolledtext

        try:
            from carla_nl import scanner_prompt_adapter
            from carla_nl.prompt_provenance import source_line_for
        except Exception as exc:
            messagebox.showerror("Edit Source Prompt", "Provenance/adapter unavailable:\n{}".format(exc), parent=self.top)
            return

        prompt = self.source_prompt or ""
        if not prompt.strip():
            messagebox.showinfo(
                "Edit Source Prompt",
                "No source prompt is stored for this scenario (it may have been created outside the prompt dialog).",
                parent=self.top,
            )
            return

        role = getattr(self, "selected_vehicle_role", None) or "ego"
        kind = None
        ev = getattr(self, "selected_event", None)
        if isinstance(ev, dict):
            role = ev.get("role") or role
            cat = ev.get("category")
            if cat:
                kind = str(cat).strip().lower().replace(" ", "_")
        span = source_line_for(prompt, role=role, kind=kind)

        win = tk.Toplevel(self.top)
        win.title("Edit Source Prompt - {}".format(self.item.name))
        win.geometry("740x580")
        try:
            configure_inkspace_window(win)
        except Exception:
            pass
        win.transient(self.top)

        info = "Selected element: role={}, kind={}".format(role, kind or "-")
        info += "   ->   source prompt line {}".format(span.line_no + 1) if span is not None else "   ->   (no matching prompt line found)"
        ttk.Label(win, text=info, wraplength=712, justify="left").pack(anchor="w", padx=12, pady=(12, 6))

        txt = scrolledtext.ScrolledText(win, wrap="word", height=18, font=("Consolas", 10), undo=True)
        txt.pack(fill="both", expand=True, padx=12)
        txt.insert("1.0", prompt)
        if span is not None:
            try:
                txt.tag_configure("prov", background="#fff3b0", foreground="#000000")
                txt.tag_add("prov", "{}.0".format(span.line_no + 1), "{}.end".format(span.line_no + 1))
                txt.see("{}.0".format(span.line_no + 1))
            except Exception:
                pass

        status = tk.StringVar(value="Edit the prompt, then Regenerate Preview. Save to persist for running.")
        ttk.Label(win, textvariable=status).pack(anchor="w", padx=12, pady=(6, 0))

        def regenerate(persist):
            new_prompt = txt.get("1.0", "end").strip()
            if not new_prompt:
                status.set("Prompt is empty.")
                return
            try:
                _preview, converted = scanner_prompt_adapter.convert_prompt(
                    new_prompt,
                    name=self.payload.get("scenario_name") or self.item.name,
                    map_name=self.payload.get("map_name"),
                    weather_preset=self.payload.get("weather_preset"),
                )
            except Exception as exc:
                status.set("Convert failed: {}".format(exc))
                return
            if not converted:
                status.set("No scenario produced from the prompt.")
                return
            new_scenario = dict(getattr(converted[0], "scenario", {}) or {})
            # Preserve metadata the prompt text does not carry.
            for key in ("task", "condition", "spawn_point_index",
                        "spawn_xodr", "camera_mode", "dreyevr", "rpc_port", "quality_level"):
                if key in self.payload and key not in new_scenario:
                    new_scenario[key] = self.payload[key]
            # experiment_events needs special handling: the schema dump always
            # emits a default-empty dict, so "key not in new_scenario" never
            # fires and the saved TOR windows / hold keys would be wiped.
            old_events = self.payload.get("experiment_events")
            new_events = new_scenario.get("experiment_events")
            if isinstance(old_events, dict) and old_events.get("tor_windows"):
                if not (isinstance(new_events, dict) and new_events.get("tor_windows")):
                    merged_events = dict(old_events)
                    if isinstance(new_events, dict) and new_events.get("hold_until_tor_resolution"):
                        merged_events["hold_until_tor_resolution"] = True
                    new_scenario["experiment_events"] = merged_events
            self.payload = new_scenario
            self.model = build_scenario_visual_model(new_scenario)
            self.source_prompt = new_prompt
            try:
                self._populate_events()
                self._redraw_all()
            except Exception as exc:
                status.set("Preview rebuild error: {}".format(exc))
                return
            if persist:
                try:
                    write_prompt_json_payload(self.item.path, new_scenario)
                    prompts = load_scenario_prompts()
                    prompts[scenario_metadata_key(self.item.path)] = new_prompt
                    save_scenario_prompts(prompts)
                    status.set("Saved and preview regenerated.")
                    if hasattr(self.owner, "emit"):
                        self.owner.emit("Regenerated scenario from edited prompt: {}".format(self.item.path))
                except Exception as exc:
                    status.set("Preview regenerated; save failed: {}".format(exc))
            else:
                status.set("Preview regenerated (not saved).")

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=12, pady=12)
        ttk.Button(buttons, text="Regenerate Preview", command=lambda: regenerate(False)).pack(side="left")
        ttk.Button(buttons, text="Save to Scenario", command=lambda: regenerate(True)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        try:
            win.grab_set()
        except Exception:
            pass

    def _build_vehicle_editor(self, parent, row):
        ttk = self.ttk
        edit = ttk.LabelFrame(parent, text="Edit Vehicle Position", padding=8)
        edit.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        for col in (1, 3, 5):
            edit.columnconfigure(col, weight=1)
        ttk.Label(edit, text="Vehicle").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.vehicle_combo = ttk.Combobox(
            edit,
            textvariable=self.vehicle_role_var,
            values=self._vehicle_role_values(),
            state="readonly",
            width=16,
        )
        self.vehicle_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.vehicle_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_vehicle_editor(self.vehicle_role_var.get()))
        ttk.Label(edit, text="Lane").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Combobox(
            edit,
            textvariable=self.vehicle_lane_var,
            values=VIS_LANE_ORDER,
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Label(edit, text="Speed").grid(row=0, column=4, sticky="w", padx=(0, 4))
        ttk.Entry(edit, textvariable=self.vehicle_speed_var, width=8).grid(row=0, column=5, sticky="ew")
        ttk.Label(edit, text="Position mode").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            edit,
            textvariable=self.vehicle_position_mode_var,
            values=("time_gap_s", "gap_m"),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ttk.Label(edit, text="Value").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Entry(edit, textvariable=self.vehicle_position_value_var, width=10).grid(row=1, column=3, sticky="ew", padx=(0, 8), pady=(6, 0))
        buttons = ttk.Frame(edit)
        buttons.grid(row=1, column=4, columnspan=2, sticky="ew", pady=(6, 0))
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Save Vehicle", command=self._save_vehicle_editor).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(buttons, text="Reload", command=self._reload_vehicle_editor).grid(row=0, column=1, sticky="w")
        ttk.Label(edit, textvariable=self.vehicle_status_var).grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _vehicle_role_values(self):
        return [vehicle["role"] for vehicle in self.model.get("vehicles", [])]

    def _vehicle_cfg(self, role):
        vehicles = self.payload.setdefault("vehicles", {})
        if not isinstance(vehicles, dict):
            vehicles = {}
            self.payload["vehicles"] = vehicles
        cfg = vehicles.get(role)
        if not isinstance(cfg, dict):
            cfg = {"role": role, "enabled": True}
            vehicles[role] = cfg
        return cfg

    def _refresh_vehicle_editor_choices(self):
        values = self._vehicle_role_values()
        try:
            self.vehicle_combo.configure(values=values)
        except Exception:
            pass
        if self.selected_vehicle_role not in values:
            self.selected_vehicle_role = values[0] if values else "ego"
        if values:
            self._load_vehicle_editor(self.selected_vehicle_role)

    def _load_vehicle_editor(self, role):
        role = str(role or "ego")
        self.selected_vehicle_role = role
        self.vehicle_role_var.set(role)
        cfg = self._vehicle_cfg(role)
        self.vehicle_lane_var.set(visual_lane_name(cfg.get("lane", "Middle")))
        if role == "ego":
            self.vehicle_position_mode_var.set("gap_m")
            self.vehicle_position_value_var.set("0")
            speed = cfg.get("speed_kmh", self.payload.get("ego_speed_kmh", self.model.get("ego_speed_kmh", 50.0)))
        else:
            if cfg.get("initial_time_gap_s") not in (None, ""):
                self.vehicle_position_mode_var.set("time_gap_s")
                self.vehicle_position_value_var.set(format_prompt_number(cfg.get("initial_time_gap_s"), 0.0))
            else:
                self.vehicle_position_mode_var.set("gap_m")
                self.vehicle_position_value_var.set(format_prompt_number(cfg.get("initial_gap_m"), 0.0))
            speed = cfg.get("speed_kmh", self.model.get("ego_speed_kmh", 50.0))
        self.vehicle_speed_var.set(format_prompt_number(speed, self.model.get("ego_speed_kmh", 50.0)))
        self.vehicle_status_var.set("Editing {}. Click a vehicle rectangle to select another one.".format(role))
        self._redraw_scene()

    def _save_vehicle_editor(self):
        role = self.vehicle_role_var.get().strip() or self.selected_vehicle_role or "ego"
        try:
            cfg = self._vehicle_cfg(role)
            lane = visual_lane_name(self.vehicle_lane_var.get())
            speed = max(0.0, float(self.vehicle_speed_var.get().strip() or "0"))
            cfg["lane"] = lane
            cfg["speed_kmh"] = speed
            if role == "ego":
                self.payload["ego_speed_kmh"] = speed
                cfg["initial_gap_m"] = 0.0
                cfg["initial_time_gap_s"] = None
                if not cfg.get("lane_change_enabled", False):
                    cfg["lane_change_target_lane"] = lane
            else:
                value = float(self.vehicle_position_value_var.get().strip() or "0")
                mode = self.vehicle_position_mode_var.get().strip() or "time_gap_s"
                if mode == "time_gap_s":
                    cfg["initial_time_gap_s"] = value
                    cfg["initial_gap_m"] = 0.0
                else:
                    cfg["initial_gap_m"] = value
                    cfg["initial_time_gap_s"] = None
            write_prompt_json_payload(self.item.path, self.payload)
        except Exception as exc:
            write_exception(exc)
            self.vehicle_status_var.set("Save failed.")
            try:
                self.owner.messagebox.showerror("Save vehicle position", str(exc), parent=self.top)
            except Exception:
                pass
            return False
        self.selected_vehicle_role = role
        self.vehicle_status_var.set("Saved {}; visualization refreshed.".format(role))
        self.owner.emit("Updated vehicle '{}' in '{}'.".format(role, self.item.name))
        try:
            self.owner.refresh_scenarios(select_key=scenario_metadata_key(self.item.path))
        except Exception:
            pass
        self._reload_visual_model(select_window_index=self.edit_window_index, select_vehicle_role=role)
        return True

    def _reload_vehicle_editor(self):
        self.payload = read_prompt_json_summary(self.item.path)
        self._reload_visual_model(select_window_index=self.edit_window_index, select_vehicle_role=self.selected_vehicle_role)

    def _build_tor_editor(self, parent, row):
        ttk = self.ttk
        edit = ttk.LabelFrame(parent, text="Edit Selected TOR Window", padding=8)
        edit.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        for col in (1, 3):
            edit.columnconfigure(col, weight=1)
        ttk.Label(edit, text="Alert").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(edit, textvariable=self.edit_alert_onset_var, width=8).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(edit, text="Deadline").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Entry(edit, textvariable=self.edit_deadline_var, width=8).grid(row=0, column=3, sticky="ew")
        ttk.Label(edit, text="Fallback").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            edit,
            textvariable=self.edit_failure_action_var,
            values=TOR_ACTION_OPTIONS,
            state="readonly",
            width=18,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ttk.Label(edit, text="Manual").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            edit,
            textvariable=self.edit_manual_var,
            values=MANUAL_REQUIREMENTS,
            state="readonly",
            width=18,
        ).grid(row=1, column=3, sticky="ew", pady=(6, 0))
        ttk.Label(edit, text="Stage1").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            edit,
            textvariable=self.edit_stage1_var,
            values=STAGE1_REQUIREMENTS,
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ttk.Label(edit, text="Delay").grid(row=2, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Entry(edit, textvariable=self.edit_stage1_delay_var, width=8).grid(row=2, column=3, sticky="ew", pady=(6, 0))
        ttk.Label(edit, text="Stage2").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            edit,
            textvariable=self.edit_stage2_var,
            values=STAGE2_REQUIREMENTS,
            state="readonly",
            width=18,
        ).grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        ttk.Label(edit, text="Fix/delay").grid(row=3, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        compact = ttk.Frame(edit)
        compact.grid(row=3, column=3, sticky="ew", pady=(6, 0))
        compact.columnconfigure(0, weight=1)
        compact.columnconfigure(1, weight=1)
        ttk.Entry(compact, textvariable=self.edit_fixation_var, width=7).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Entry(compact, textvariable=self.edit_stage2_delay_var, width=7).grid(row=0, column=1, sticky="ew")
        buttons = ttk.Frame(edit)
        buttons.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Save TOR Rules", command=self._save_tor_editor).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(buttons, text="Reload", command=self._reload_tor_editor).grid(row=0, column=1, sticky="w")
        ttk.Label(buttons, textvariable=self.edit_status_var).grid(row=0, column=2, sticky="e")

    def _populate_events(self):
        for iid in self.event_tree.get_children():
            self.event_tree.delete(iid)
        self._populate_decision_tree()
        for index, event in enumerate(self.model["events"]):
            iid = str(index)
            self.event_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "{:.2f}".format(event["time_s"]),
                    event["category"],
                    event["label"],
                ),
            )
        for index, event in enumerate(self.model.get('conditional_events', [])):
            self.event_tree.insert('', 'end', iid='conditional_{}'.format(index),
                                   values=(event['condition'], event['category'], event['label']))
        if self.model["events"]:
            self.event_tree.selection_set("0")
            self.selected_event = self.model["events"][0]
        self._refresh_vehicle_editor_choices()

    def _populate_decision_tree(self):
        for iid in self.decision_tree.get_children():
            self.decision_tree.delete(iid)
        self.decision_tree_nodes = {}

        def add_node(parent, node, index_path):
            iid = "decision_tree_{}".format("_".join(str(part) for part in index_path))
            self.decision_tree_nodes[iid] = node
            time_text = "{:.2f}".format(node["time_s"]) if node.get("time_s") is not None else ""
            self.decision_tree.insert(
                parent,
                "end",
                iid=iid,
                text=node.get("title", ""),
                values=(time_text, node.get("rule", "")),
                open=True,
            )
            for child_index, child in enumerate(node.get("children", []) or []):
                add_node(iid, child, index_path + [child_index])

        roots = self.model.get("decision_tree") or []
        for root_index, root in enumerate(roots):
            add_node("", root, [root_index])
        if not roots:
            self.decision_tree.insert("", "end", text="No TOR decision tree", values=("", ""))
            self.edit_status_var.set("No TOR window found in this scenario.")
        else:
            first_iid = "decision_tree_0"
            self.decision_tree.selection_set(first_iid)
            self.decision_tree.see(first_iid)
            self.selected_decision_node = self.decision_tree_nodes.get(first_iid)
            self._load_editor_from_window(0)

    def _tor_windows(self):
        experiment = self.payload.get("experiment_events") if isinstance(self.payload.get("experiment_events"), dict) else {}
        windows = experiment.get("tor_windows") if isinstance(experiment.get("tor_windows"), list) else []
        return windows

    def _load_editor_from_window(self, index):
        windows = self._tor_windows()
        if index is None or index < 0 or index >= len(windows):
            self.edit_window_index = None
            self.edit_status_var.set("Select a TOR node to edit.")
            return
        window = windows[index]
        if not isinstance(window, dict):
            self.edit_window_index = None
            self.edit_status_var.set("Selected TOR window is not editable.")
            return
        fields = dict(window.get("fields", {}) or {})
        fields.update(adaptive_fields_from_mapping(fields))
        onset = visual_float(window.get("alert_onset_s", window.get("tor_onset_s", 0.0)), 0.0)
        deadline = visual_float(window.get("tor_deadline_s"), onset + 6.0)
        self.edit_window_index = index
        self.edit_alert_onset_var.set(format_prompt_number(onset))
        self.edit_deadline_var.set(format_prompt_number(deadline))
        self.edit_failure_action_var.set(str(window.get("failure_action") or "brake"))
        self.edit_stage1_var.set(str(fields.get("stage1_requirement", "gaze_screen")))
        self.edit_stage1_delay_var.set(format_prompt_number(fields.get("stage1_delay_s", 0.0)))
        self.edit_stage2_var.set(str(fields.get("stage2_requirement", "target_fixation")))
        self.edit_stage2_delay_var.set(format_prompt_number(fields.get("stage2_delay_s", 0.0)))
        self.edit_fixation_var.set(format_prompt_number(fields.get("target_fixation_s", 0.5)))
        self.edit_manual_var.set(str(fields.get("manual_requires", "none")))
        self.edit_status_var.set("Editing TOR window {}.".format(index + 1))

    def _adaptive_text_from_editor(self):
        return (
            "stage1 -> stage2: require={stage1}, delay={stage1_delay}s\n"
            "stage2 -> ready: require={stage2}, fixation={fixation}s, delay={stage2_delay}s\n"
            "manual: require={manual}"
        ).format(
            stage1=self.edit_stage1_var.get().strip() or "gaze_screen",
            stage1_delay=format_prompt_number(self.edit_stage1_delay_var.get(), 0.0),
            stage2=self.edit_stage2_var.get().strip() or "target_fixation",
            fixation=format_prompt_number(self.edit_fixation_var.get(), 0.5),
            stage2_delay=format_prompt_number(self.edit_stage2_delay_var.get(), 0.0),
            manual=self.edit_manual_var.get().strip() or "none",
        )

    def _save_tor_editor(self):
        windows = self._tor_windows()
        index = self.edit_window_index
        if index is None or index < 0 or index >= len(windows):
            self.edit_status_var.set("Select a TOR node before saving.")
            return False
        try:
            onset = max(0.0, float(self.edit_alert_onset_var.get().strip() or "0"))
            deadline = max(onset + 0.001, float(self.edit_deadline_var.get().strip() or str(onset + 6.0)))
            adaptive_text = self._adaptive_text_from_editor()
            adaptive_fields = parse_tor_adaptive_prompt(adaptive_text)
            window = windows[index]
            fields = dict(window.get("fields", {}) or {})
            fields.update(adaptive_fields)
            fields["adaptive_prompt"] = adaptive_text
            window["alert_onset_s"] = onset
            window["tor_onset_s"] = onset
            window["tor_deadline_s"] = deadline
            window["failure_action"] = self.edit_failure_action_var.get().strip() or "brake"
            window["fields"] = fields
            write_prompt_json_payload(self.item.path, self.payload)
        except Exception as exc:
            write_exception(exc)
            self.edit_status_var.set("Save failed.")
            try:
                self.owner.messagebox.showerror("Save TOR rules", str(exc), parent=self.top)
            except Exception:
                pass
            return False
        self.edit_status_var.set("Saved TOR rules; visualization refreshed.")
        self.owner.emit("Updated TOR rules in '{}'.".format(self.item.name))
        try:
            self.owner.refresh_scenarios(select_key=scenario_metadata_key(self.item.path))
        except Exception:
            pass
        self._reload_visual_model(select_window_index=index)
        return True

    def _reload_tor_editor(self):
        self.payload = read_prompt_json_summary(self.item.path)
        self._reload_visual_model(select_window_index=self.edit_window_index)

    def _reload_visual_model(self, select_window_index=None, select_vehicle_role=None):
        self.model = build_scenario_visual_model(self.payload)
        self.selected_event = None
        self.selected_decision_node = None
        if select_vehicle_role:
            self.selected_vehicle_role = select_vehicle_role
        self._populate_events()
        windows = self._tor_windows()
        if select_window_index is not None and windows:
            index = max(0, min(len(windows) - 1, int(select_window_index)))
            iid = "decision_tree_{}".format(index)
            if iid in self.decision_tree_nodes:
                self.decision_tree.selection_set(iid)
                self.decision_tree.see(iid)
                self.selected_decision_node = self.decision_tree_nodes.get(iid)
            self._load_editor_from_window(index)
        if select_vehicle_role:
            self._load_vehicle_editor(select_vehicle_role)
        self._redraw_all()


    def _on_time_changed(self, _value=None):
        self.current_t = float(self.time_var.get())
        self._redraw_all()

    def _sample_vehicle_at(self, vehicle, t):
        samples = vehicle["samples"]
        if not samples:
            return {"t": t, "s": 0.0, "lane_y": 1.0, "speed_kmh": 0.0}
        if t <= samples[0]["t"]:
            return samples[0]
        for index in range(1, len(samples)):
            left = samples[index - 1]
            right = samples[index]
            if t <= right["t"]:
                span = max(0.001, right["t"] - left["t"])
                blend = max(0.0, min(1.0, (t - left["t"]) / span))
                return {
                    "t": t,
                    "s": left["s"] + (right["s"] - left["s"]) * blend,
                    "lane_y": left["lane_y"] + (right["lane_y"] - left["lane_y"]) * blend,
                    "speed_kmh": left["speed_kmh"] + (right["speed_kmh"] - left["speed_kmh"]) * blend,
                }
        return samples[-1]

    def _scene_xy(self, s, lane_y):
        # Embedded previews can be shorter than their requested widget size.
        # Project into the visible canvas, not a fictitious minimum viewport.
        width = max(1, self.scene_canvas.winfo_width())
        height = max(1, self.scene_canvas.winfo_height())
        left = 72
        right = width - 32
        top = min(50, height * 0.30)
        bottom = height - (60 if height >= 220 else 34)
        min_s = self.model["min_s"]
        max_s = self.model["max_s"]
        x = left + (float(s) - min_s) / max(1.0, max_s - min_s) * max(1.0, right - left)
        y = top + float(lane_y) / 2.0 * max(1.0, bottom - top)
        if self.model.get("pedestrians"):
            # Reserve both road edges for people, including head/feet and labels.
            curb_top = min(48, height * 0.32)
            curb_bottom = height - (48 if height >= 220 else 18)
            y = curb_top + (float(lane_y) + 0.7) / 3.4 * max(1.0, curb_bottom - curb_top)
        return x, y

    def _redraw_scene(self):
        canvas = self.scene_canvas
        canvas.delete("all")
        self.vehicle_canvas_hits = []
        self.pedestrian_canvas_hits = []
        self.obstacle_canvas_hits = []
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        left = 72
        right = width - 32
        for lane in VIS_LANE_ORDER:
            y = self._scene_xy(0.0, VIS_LANE_Y[lane])[1]
            canvas.create_line(left, y, right, y, fill=INKSPACE_COLORS["border_strong"], width=2)
            canvas.create_text(16, y, text=lane, anchor="w", fill=INKSPACE_COLORS["text_muted"])
        for tick_s in self._distance_ticks():
            x, _y = self._scene_xy(tick_s, 0.0)
            canvas.create_line(x, 34, x, height - 28, fill=INKSPACE_COLORS["surface_subtle"])
            canvas.create_text(x, 20, text="{:.0f}m".format(tick_s), fill=INKSPACE_COLORS["text_subtle"])
        for vehicle in self.model["vehicles"]:
            points = []
            for sample in vehicle["samples"]:
                points.extend(self._scene_xy(sample["s"], sample["lane_y"]))
            if len(points) >= 4:
                canvas.create_line(*points, fill=vehicle["color"], width=2, smooth=True)
        for pedestrian in self.model.get("pedestrians", []):
            start_xy = self._scene_xy(pedestrian["s"], pedestrian["start_lane_y"])
            end_xy = self._scene_xy(pedestrian["s"], pedestrian["end_lane_y"])
            canvas.create_line(*start_xy, *end_xy, fill=INKSPACE_COLORS["text_subtle"],
                               width=2, dash=(4, 4), arrow="last", arrowshape=(8, 10, 4),
                               tags=("pedestrian_path", pedestrian["role"]))
        for vehicle in self.model["vehicles"]:
            sample = self._sample_vehicle_at(vehicle, self.current_t)
            x, y = self._scene_xy(sample["s"], sample["lane_y"])
            w = 30 if vehicle["role"] == "ego" else 24
            h = 16
            selected = vehicle["role"] == self.selected_vehicle_role
            outline = INKSPACE_COLORS["warning_text"] if selected else INKSPACE_COLORS["border_strong"]
            outline_width = 3 if selected else 1
            canvas.create_rectangle(
                x - w / 2,
                y - h / 2,
                x + w / 2,
                y + h / 2,
                fill=vehicle["color"],
                outline=outline,
                width=outline_width,
            )
            canvas.create_text(x, y - 18, text=vehicle["label"], fill=INKSPACE_COLORS["text"])
            canvas.create_text(x, y + 20, text="{:.0f}m".format(sample["s"]), fill=INKSPACE_COLORS["text_muted"])
            self.vehicle_canvas_hits.append({"role": vehicle["role"], "x": x, "y": y, "w": w, "h": h})
        self._draw_scene_obstacles()
        for pedestrian in self.model.get("pedestrians", []):
            sample = visual_pedestrian_at(pedestrian, self.current_t)
            x, y = self._scene_xy(sample["s"], sample["lane_y"])
            phase = sample["phase"]
            color = VIS_LANE_COLOR["pedestrian"] if phase != "scheduled" else INKSPACE_COLORS["text_subtle"]
            tags = ("pedestrian_marker", pedestrian["role"])
            # A person silhouette is distinct from the rectangular vehicles.
            canvas.create_oval(x - 4, y - 13, x + 4, y - 5, outline=color, width=2,
                               fill=color if phase != "scheduled" else INKSPACE_COLORS["field"], tags=tags)
            canvas.create_line(x, y - 4, x, y + 4, fill=color, width=2, tags=tags)
            canvas.create_line(x - 7, y + 3, x, y - 2, x + 7, y + 3, fill=color, width=2, tags=tags)
            canvas.create_line(x - 6, y + 12, x, y + 4, x + 6, y + 12, fill=color, width=2, tags=tags)
            if phase == "scheduled":
                status = "Starts at {:g}s".format(pedestrian["start_time_s"])
            elif phase == "crossing":
                status = "Crossing · {:.0f}%".format(sample["progress"] * 100)
            else:
                status = "Crossed at {:g}s".format(pedestrian["end_time_s"])
            text_on_right = x < width / 2
            label = canvas.create_text(
                x + (16 if text_on_right else -16), y,
                text="{} · {} → {}\n{}".format(pedestrian["label"], pedestrian["start_side"],
                                                 pedestrian["end_side"], status),
                anchor="w" if text_on_right else "e", justify="left", fill=color,
                font=("Segoe UI", 9), tags=tags)
            # Prefer the free side of the person; try nearby positions before
            # allowing a label to cover a vehicle or another person's label.
            blocked = [(hit["x"] - 24, hit["y"] - 28, hit["x"] + 24, hit["y"] + 28)
                       for hit in self.vehicle_canvas_hits]
            blocked.extend(hit["label_bbox"] for hit in self.pedestrian_canvas_hits)
            blocked.extend(hit["label_bbox"] for hit in self.obstacle_canvas_hits)
            for other in self.model.get("pedestrians", []):
                other_sample = visual_pedestrian_at(other, self.current_t)
                px, py = self._scene_xy(other_sample["s"], other_sample["lane_y"])
                blocked.append((px - 11, py - 15, px + 11, py + 15))
            candidates = []
            for dy in (0, -32, 32):
                for on_right in (text_on_right, not text_on_right):
                    tx, ty = x + (16 if on_right else -16), y + dy
                    anchor = "w" if on_right else "e"
                    canvas.itemconfigure(label, anchor=anchor)
                    canvas.coords(label, tx, ty)
                    bx1, by1, bx2, by2 = canvas.bbox(label)
                    if bx1 < left + 4 or bx2 > width - 4 or by1 < 34 or by2 > height - 4:
                        continue
                    overlap = sum(max(0, min(bx2, b[2]) - max(bx1, b[0])) *
                                  max(0, min(by2, b[3]) - max(by1, b[1])) for b in blocked)
                    candidates.append((overlap, tx, ty, anchor))
            if candidates:
                _, tx, ty, anchor = min(candidates, key=lambda candidate: candidate[0])
                canvas.itemconfigure(label, anchor=anchor)
                canvas.coords(label, tx, ty)
            else:
                canvas.itemconfigure(label, anchor="w" if text_on_right else "e")
                canvas.coords(label, x + (16 if text_on_right else -16), y)
            box = canvas.bbox(label)
            backdrop = canvas.create_rectangle(box[0] - 2, box[1], box[2] + 2, box[3],
                                               fill=INKSPACE_COLORS["field"], outline="", tags=tags)
            canvas.tag_lower(backdrop, label)
            self.pedestrian_canvas_hits.append({"role": pedestrian["role"], "x": x, "y": y,
                                                "label_bbox": box})
        # Small embedded canvases reserve their bottom margin for vehicle labels.
        if height >= 220:
            canvas.create_text(
                left,
                height - 18,
                text="Estimated paths; simulation motion may differ.",
                anchor="w",
                fill=INKSPACE_COLORS["text_subtle"],
            )

    def _draw_scene_obstacles(self):
        """Separate identity callouts while keeping every pin at its true road position."""
        canvas = self.scene_canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        obstacles = self.model.get("obstacles", [])
        blocked = [(hit["x"] - 24, hit["y"] - 28, hit["x"] + 24, hit["y"] + 28)
                   for hit in self.vehicle_canvas_hits]
        pins = []
        for obstacle in obstacles:
            x, y = self._scene_xy(obstacle["s"], obstacle["lane_y"])
            pins.append((obstacle, x, y, visual_obstacle_phase(obstacle, self.current_t)))
            blocked.append((x - 12, y - 12, x + 12, y + 12))
        for obstacle, x, y, phase in pins:
            role = obstacle["role"]
            name = role.replace("_", " ").capitalize()
            color = VIS_LANE_COLOR["hazard"] if phase == "active" else INKSPACE_COLORS["text_muted"]
            status = {"scheduled": "Scheduled", "active": "Present", "ended": "Removed"}[phase]
            label_text = "{} · {}\n{:g}–{:g}s · {} · {:g}m".format(
                name, obstacle["kind"], obstacle["start_time_s"], obstacle["end_time_s"], status, obstacle["s"])
            if obstacle["spawn_mode"] == "drop_from_vehicle":
                label_text += "\nDrop from " + role_to_prompt_name(obstacle["drop_source_role"])
            elif obstacle["position_reference"] == "vehicle_at_spawn":
                from carla_nl.obstacle_positions import obstacle_prompt_position
                label_text += "\n" + obstacle_prompt_position(obstacle) + " at appearance"
            label = canvas.create_text(x, y, text=label_text, fill=color, justify="left",
                                       font=("Segoe UI", 9), width=max(80, width - 88), tags=("obstacle_label", role))
            candidates = []
            for dy in (-26, 26, -70, 70, 0, -114, 114):
                for on_right in (x < width / 2, x >= width / 2):
                    tx, ty, anchor = x + (18 if on_right else -18), y + dy, "w" if on_right else "e"
                    canvas.itemconfigure(label, anchor=anchor)
                    canvas.coords(label, tx, ty)
                    box = canvas.bbox(label)
                    # Callouts may move horizontally on narrow panels; their
                    # leader still points to the unchanged road coordinate.
                    shift = max(0, 76 - box[0]) - max(0, box[2] - (width - 4))
                    if shift:
                        tx += shift
                        canvas.coords(label, tx, ty)
                        box = canvas.bbox(label)
                    if box[0] < 76 or box[2] > width - 4 or box[1] < 34 or box[3] > height - 4:
                        continue
                    overlap = sum(max(0, min(box[2], b[2]) - max(box[0], b[0])) *
                                  max(0, min(box[3], b[3]) - max(box[1], b[1])) for b in blocked)
                    candidates.append((overlap, tx, ty, anchor))
            if candidates:
                _, tx, ty, anchor = min(candidates, key=lambda item: item[0])
                canvas.itemconfigure(label, anchor=anchor)
                canvas.coords(label, tx, ty)
            else:
                canvas.itemconfigure(label, anchor="center")
                canvas.coords(label, width / 2, min(height - 24, max(52, y)))
            box = canvas.bbox(label)
            leader = canvas.create_line(x, y, max(box[0], min(x, box[2])), (box[1] + box[3]) / 2,
                                         fill=color, dash=(2, 2), tags=("obstacle_link", role))
            canvas.tag_lower(leader, label)
            background = canvas.create_rectangle(box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1,
                                                 fill=INKSPACE_COLORS["field"], outline="",
                                                 tags=("obstacle_label_background", role))
            canvas.tag_lower(background, label)
            blocked.append((box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3))
            self.obstacle_canvas_hits.append({"role": role, "x": x, "y": y, "phase": phase, "label_bbox": box})
        # A future/removed obstacle at the same point must never hide the active one.
        for obstacle, x, y, phase in sorted(pins, key=lambda pin: pin[3] == "active"):
            active = phase == "active"
            color = VIS_LANE_COLOR["hazard"] if active else INKSPACE_COLORS["text_subtle"]
            size = 9 if active else 7
            canvas.create_polygon(x, y - size, x + size, y, x, y + size, x - size, y,
                                  fill=color if active else INKSPACE_COLORS["field"], outline=color,
                                  width=2 if active else 1, tags=("obstacle_marker", obstacle["role"]))

    def _on_scene_click(self, event):
        if not self.vehicle_canvas_hits:
            return
        nearest = min(
            self.vehicle_canvas_hits,
            key=lambda item: (event.x - item["x"]) ** 2 + (event.y - item["y"]) ** 2,
        )
        dx = abs(event.x - nearest["x"])
        dy = abs(event.y - nearest["y"])
        if dx <= max(24, nearest["w"]) and dy <= max(24, nearest["h"] * 2):
            self._load_vehicle_editor(nearest["role"])

    def _distance_ticks(self):
        from tkinter import font as tkfont
        min_s = self.model["min_s"]
        max_s = self.model["max_s"]
        font = tkfont.nametofont("TkDefaultFont", root=self.scene_canvas)
        label_width = max(font.measure("{:.0f}m".format(value)) for value in (min_s, max_s))
        plot_width = max(1, self.scene_canvas.winfo_width() - 104)
        raw_step = max(1.0, max_s - min_s) * max(64, label_width + 16) / plot_width
        magnitude = 10.0 ** math.floor(math.log10(raw_step))
        step = next(value * magnitude for value in (1, 2, 5, 10) if value * magnitude >= raw_step)
        start = math.ceil(min_s / step) * step
        ticks = []
        value = start
        while value <= max_s:
            ticks.append(value)
            value += step
        return ticks

    def _redraw_timeline(self):
        canvas = self.timeline_canvas
        canvas.delete("all")
        width = max(260, canvas.winfo_width())
        height = max(90, canvas.winfo_height())
        left = 42
        right = width - 16
        y = 44
        total = max(0.1, self.model["total_time_s"])
        canvas.create_line(left, y, right, y, fill=INKSPACE_COLORS["border_strong"], width=2)
        for tick in range(int(math.floor(total)) + 1):
            if tick % max(1, int(math.ceil(total / 8.0))) != 0 and tick not in (0, int(total)):
                continue
            x = left + tick / total * (right - left)
            canvas.create_line(x, y - 6, x, y + 6, fill=INKSPACE_COLORS["border_strong"])
            canvas.create_text(x, y + 20, text="{}s".format(tick), fill=INKSPACE_COLORS["text_subtle"])
        for index, event in enumerate(self.model["events"]):
            x = left + event["time_s"] / total * (right - left)
            color = self._event_color(event)
            r = 5 if event["category"] == "decision" else 3
            canvas.create_oval(x - r, y - 22 - r, x + r, y - 22 + r, fill=color, outline="")
            if index % 3 == 0 or event["category"] in ("decision", "hazard"):
                canvas.create_text(x, y - 42, text=event["category"][:4], fill=color)
        current_x = left + self.current_t / total * (right - left)
        canvas.create_line(current_x, 10, current_x, height - 10, fill=INKSPACE_COLORS["brand"], width=2)

    def _event_color(self, event):
        category = event.get("category")
        if category == "decision":
            return VIS_LANE_COLOR["decision"]
        if category in ("hazard", "obstacle"):
            return VIS_LANE_COLOR["hazard"]
        if category == "pedestrian":
            return VIS_LANE_COLOR["pedestrian"]
        if category in ("lane", "speed", "gap"):
            return VIS_LANE_COLOR["motion"]
        return INKSPACE_COLORS["text_subtle"]

    def _on_timeline_click(self, event):
        width = max(260, self.timeline_canvas.winfo_width())
        left = 42
        right = width - 16
        total = max(0.1, self.model["total_time_s"])
        if event.x < left:
            t = 0.0
        elif event.x > right:
            t = total
        else:
            t = (event.x - left) / max(1.0, right - left) * total
        self.time_var.set(t)
        self.current_t = t
        self._select_nearest_event(t)
        self._redraw_all()

    def _select_tree_event(self):
        selection = self.event_tree.selection()
        if not selection:
            return
        if selection[0].startswith('conditional_'):
            self.selected_event = self.model['conditional_events'][int(selection[0].split('_')[1])]
            self.selected_decision_node = None
            self._redraw_details()
            return
        index = int(selection[0])
        if index < 0 or index >= len(self.model["events"]):
            return
        if not self._syncing_selection:
            self.selected_decision_node = None
        self.selected_event = self.model["events"][index]
        self.time_var.set(self.selected_event["time_s"])
        self.current_t = self.selected_event["time_s"]
        self._redraw_all()

    def _select_decision_tree_node(self):
        selection = self.decision_tree.selection()
        if not selection:
            return
        node = self.decision_tree_nodes.get(selection[0])
        if not node:
            return
        self.selected_decision_node = node
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        if "window_index" in meta:
            try:
                self._load_editor_from_window(int(meta.get("window_index")))
            except Exception:
                pass
        self.current_t = visual_float(node.get("time_s"), self.current_t)
        self.time_var.set(self.current_t)
        branch_id = node.get("branch_id")
        if branch_id:
            matching = [
                (index, event)
                for index, event in enumerate(self.model["events"])
                if event.get("branch_id") == branch_id
            ]
            if matching:
                index, event = min(matching, key=lambda item: abs(item[1]["time_s"] - self.current_t))
                self.selected_event = event
                self._syncing_selection = True
                try:
                    self.event_tree.selection_set(str(index))
                    self.event_tree.see(str(index))
                finally:
                    self._syncing_selection = False
        self._redraw_all()

    def _selected_flow_node(self):
        selection = self.decision_tree.selection()
        if not selection:
            return None
        return self.decision_tree_nodes.get(selection[0])

    def _edit_selected_flow_node(self):
        node = self._selected_flow_node()
        if not node:
            try:
                self.owner.messagebox.showinfo("Edit flow node", "Select a TOR flow node first.", parent=self.top)
            except Exception:
                pass
            return
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        if "window_index" not in meta:
            try:
                self.owner.messagebox.showinfo("Edit flow node", "This node is generated from the flow and has no editable TOR window.", parent=self.top)
            except Exception:
                pass
            return
        try:
            self._load_editor_from_window(int(meta.get("window_index")))
        except Exception:
            pass
        kind = str(meta.get("kind") or "window")
        tk = self.tk
        ttk = self.ttk
        top = tk.Toplevel(self.top)
        top.title("Edit Flow Node - {}".format(node.get("title", "TOR")))
        top.geometry("420x260")
        top.minsize(360, 220)
        configure_inkspace_window(top)
        top.transient(self.top)
        root = ttk.Frame(top, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        ttk.Label(root, text=node.get("title", "TOR node"), style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        local_vars = {}
        row = 1

        def add_entry(label, key, value):
            nonlocal row
            local_vars[key] = tk.StringVar(value=str(value))
            ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
            ttk.Entry(root, textvariable=local_vars[key], width=16).grid(row=row, column=1, sticky="ew", pady=(0, 6))
            row += 1

        def add_combo(label, key, value, values):
            nonlocal row
            local_vars[key] = tk.StringVar(value=str(value))
            ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
            ttk.Combobox(
                root,
                textvariable=local_vars[key],
                values=values,
                state="readonly",
                width=20,
            ).grid(row=row, column=1, sticky="ew", pady=(0, 6))
            row += 1

        if kind in ("window", "deadline"):
            add_entry("Alert onset", "alert", self.edit_alert_onset_var.get())
            add_entry("Deadline", "deadline", self.edit_deadline_var.get())
            add_combo("Fallback", "fallback", self.edit_failure_action_var.get(), TOR_ACTION_OPTIONS)
        elif kind == "stage1":
            add_combo("Stage1 require", "stage1", self.edit_stage1_var.get(), STAGE1_REQUIREMENTS)
            add_entry("Stage1 delay", "stage1_delay", self.edit_stage1_delay_var.get())
        elif kind == "stage2":
            add_combo("Stage2 require", "stage2", self.edit_stage2_var.get(), STAGE2_REQUIREMENTS)
            add_entry("Fixation seconds", "fixation", self.edit_fixation_var.get())
            add_entry("Stage2 delay", "stage2_delay", self.edit_stage2_delay_var.get())
        elif kind == "manual":
            add_combo("Manual require", "manual", self.edit_manual_var.get(), MANUAL_REQUIREMENTS)
        else:
            add_entry("Deadline", "deadline", self.edit_deadline_var.get())

        status = tk.StringVar(value="")
        buttons = ttk.Frame(root)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(2, weight=1)

        def save_node():
            try:
                if "alert" in local_vars:
                    self.edit_alert_onset_var.set(local_vars["alert"].get().strip())
                if "deadline" in local_vars:
                    self.edit_deadline_var.set(local_vars["deadline"].get().strip())
                if "fallback" in local_vars:
                    self.edit_failure_action_var.set(local_vars["fallback"].get().strip())
                if "stage1" in local_vars:
                    self.edit_stage1_var.set(local_vars["stage1"].get().strip())
                if "stage1_delay" in local_vars:
                    self.edit_stage1_delay_var.set(local_vars["stage1_delay"].get().strip())
                if "stage2" in local_vars:
                    self.edit_stage2_var.set(local_vars["stage2"].get().strip())
                if "fixation" in local_vars:
                    self.edit_fixation_var.set(local_vars["fixation"].get().strip())
                if "stage2_delay" in local_vars:
                    self.edit_stage2_delay_var.set(local_vars["stage2_delay"].get().strip())
                if "manual" in local_vars:
                    self.edit_manual_var.set(local_vars["manual"].get().strip())
                if not self._save_tor_editor():
                    status.set("Save failed.")
                    return
            except Exception as exc:
                write_exception(exc)
                status.set("Save failed.")
                try:
                    self.owner.messagebox.showerror("Edit flow node", str(exc), parent=top)
                except Exception:
                    pass
                return
            top.destroy()

        ttk.Button(buttons, text="Save Node", command=save_node).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=top.destroy).grid(row=0, column=1, sticky="w")
        ttk.Label(buttons, textvariable=status).grid(row=0, column=2, sticky="e")
        top.grab_set()

    def _select_nearest_event(self, t):
        if not self.model["events"]:
            return
        index, _event = min(
            enumerate(self.model["events"]),
            key=lambda item: abs(item[1]["time_s"] - t),
        )
        self.event_tree.selection_set(str(index))
        self.event_tree.see(str(index))
        self.selected_event = self.model["events"][index]
        self.selected_decision_node = None

    def _step_event(self, delta):
        events = self.model["events"]
        if not events:
            return
        selection = self.event_tree.selection()
        if selection and not selection[0].startswith('conditional_'):
            index = int(selection[0])
        else:
            index = 0
        index = max(0, min(len(events) - 1, index + int(delta)))
        self.event_tree.selection_set(str(index))
        self.event_tree.see(str(index))
        self.selected_event = events[index]
        self.selected_decision_node = None
        self.time_var.set(self.selected_event["time_s"])
        self.current_t = self.selected_event["time_s"]
        self._redraw_all()

    def _redraw_details(self):
        event = self.selected_event
        lines = []
        node = self.selected_decision_node
        if node:
            lines.append("[{:.2f}s] Decision tree - {}".format(node["time_s"], node["title"]))
            if node.get("rule"):
                lines.append("Rule/action: {}".format(node["rule"]))
            if node.get("summary"):
                lines.append(node["summary"])
            branch_id = node.get("branch_id")
            branch = next((item for item in self.model["decisions"] if item["id"] == branch_id), None)
            if branch:
                lines.append("")
                lines.append("Requirement: {}".format(branch["requirement"]))
                lines.append("YES: {}".format(branch["yes"]))
                lines.append("NO: {}".format(branch["no"]))
        elif event:
            when = event.get('condition') or '{:.2f}s'.format(event['time_s'])
            lines.append('[{}] {} - {}'.format(when, event['category'], event['label']))
            if event.get("details"):
                lines.append(event["details"])
            branch_id = event.get("branch_id")
            branch = next((item for item in self.model["decisions"] if item["id"] == branch_id), None)
            if branch:
                lines.append("")
                lines.append("Decision: {}".format(branch["title"]))
                lines.append("Requirement: {}".format(branch["requirement"]))
                lines.append("If satisfied: {}".format(branch["yes"]))
                lines.append("If not satisfied: {}".format(branch["no"]))
        if not lines:
            lines.append("Select a decision-tree node, event row, or timeline marker to inspect branches.")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", "\n".join(lines))

    def _redraw_all(self):
        self.time_label.configure(text="t={:.2f}s".format(self.current_t))
        self._redraw_scene()
        self._redraw_timeline()
        self._redraw_details()


class EmbeddedScenarioDesignPreview(ScenarioDesignVisualizer):
    """Live, read-only design preview rendered inside another panel.

    It reuses ScenarioDesignVisualizer's scene/timeline drawing but skips the
    Toplevel window, the disk read, the editors and the decision/event trees so
    it can update straight from an unsaved, in-memory scenario payload while the
    user edits in the Add Scenario dialog. Call :meth:`update` with a scenario
    dict to re-render.
    """

    # map an event category -> the prompt-provenance "kind" it should resolve to
    _CATEGORY_KIND = {
        "lane": "lane_change",
        "gap": "time_gap",
        "decision": "tor",
        "hazard": "tor",
        "marker": "marker",
        "obstacle": "obstacle",
        "pedestrian": "pedestrian",
        "vehicle": "vehicle",
    }

    def __init__(self, owner, parent):
        import tkinter as tk
        from tkinter import ttk

        self.owner = owner
        self.tk = tk
        self.ttk = ttk
        self.item = None
        self.payload = {}
        self.model = build_scenario_visual_model({})
        self.current_t = 0.0
        self.selected_event = None
        self.selected_decision_node = None
        self.decision_tree_nodes = {}
        self.selected_vehicle_role = "ego"
        self.vehicle_canvas_hits = []
        self._has_data = False
        self._embed_syncing = False
        self.playing = False
        self._play_job = None
        self._vehicle_editor_win = None
        self._relink_role_after_update = None
        self._build_embedded_ui(parent)

    def _build_embedded_ui(self, parent):
        tk = self.tk
        ttk = self.ttk
        shell = ttk.Frame(parent)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.design_viewport = tk.Canvas(
            shell, bg=INKSPACE_COLORS["field"], highlightthickness=0,
            width=400, height=400,
        )
        self.design_viewport.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.design_viewport.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.design_viewport.configure(yscrollcommand=scrollbar.set)
        frame = self.design_content = ttk.Frame(self.design_viewport, padding=4)
        self._design_content_window = self.design_viewport.create_window(
            0, 0, window=frame, anchor="nw",
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=3)
        frame.rowconfigure(6, weight=2)
        frame.rowconfigure(8, weight=1)

        self.summary_var = tk.StringVar(value="Analyze (or edit the wizard) to preview the design. Click a vehicle to edit it.")
        self.design_summary = ttk.Label(
            frame, textvariable=self.summary_var, style="Section.TLabel", wraplength=380,
        )
        self.design_summary.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.scene_canvas = tk.Canvas(
            frame,
            bg=INKSPACE_COLORS["field"],
            height=170,
            highlightthickness=1,
            highlightbackground=INKSPACE_COLORS["border"],
            highlightcolor=INKSPACE_COLORS["brand_border"],
        )
        self.scene_canvas.grid(row=1, column=0, sticky="nsew")
        self.scene_canvas.bind("<Configure>", lambda _event: self._redraw_scene())
        self.scene_canvas.bind("<Button-1>", self._on_embed_scene_click)
        self.scene_canvas.bind("<Motion>", self._on_embed_scene_motion)

        ttk.Label(frame, text="Timeline (click a node to jump to its prompt line)", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(5, 0)
        )
        self.timeline_canvas = tk.Canvas(
            frame,
            height=76,
            bg=INKSPACE_COLORS["field"],
            highlightthickness=1,
            highlightbackground=INKSPACE_COLORS["border"],
            highlightcolor=INKSPACE_COLORS["brand_border"],
        )
        self.timeline_canvas.grid(row=3, column=0, sticky="ew", pady=(3, 4))
        self.timeline_canvas.bind("<Configure>", lambda _event: self._redraw_timeline())
        self.timeline_canvas.bind("<Button-1>", self._on_timeline_click)

        controls = ttk.Frame(frame)
        controls.grid(row=4, column=0, sticky="ew")
        controls.columnconfigure(3, weight=1)
        self.play_button = ttk.Button(controls, text="Play", width=8, command=self._toggle_play)
        self.play_button.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.play_speed_var = tk.StringVar(value="1x")
        ttk.Combobox(
            controls, textvariable=self.play_speed_var, values=("0.5x", "1x", "2x", "4x"),
            state="readonly", width=5,
        ).grid(row=0, column=1, sticky="w", padx=(0, 10))
        self.time_label = ttk.Label(controls, text="t=0.00s")
        self.time_label.grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.time_var = tk.DoubleVar(value=0.0)
        self.time_scale = ttk.Scale(
            controls,
            from_=0.0,
            to=max(0.1, self.model["total_time_s"]),
            variable=self.time_var,
            command=self._on_time_changed,
        )
        self.time_scale.grid(row=0, column=3, sticky="ew")

        ttk.Label(frame, text="Events (click to highlight + edit its prompt line)", style="Section.TLabel").grid(
            row=5, column=0, sticky="w", pady=(5, 0)
        )
        self.event_tree = ttk.Treeview(frame, columns=("time", "type", "label"), show="headings", height=6)
        self.event_tree.heading("time", text="When")
        self.event_tree.heading("type", text="Who")
        self.event_tree.heading("label", text="What happens")
        self.event_tree.column("time", width=86, stretch=False)
        self.event_tree.column("type", width=88, stretch=False)
        self.event_tree.column("label", width=300, stretch=True)
        self.event_tree.grid(row=6, column=0, sticky="nsew", pady=(3, 0))
        self.event_tree.bind("<<TreeviewSelect>>", self._on_embed_event_selected)

        # TOR Decision Flow -- only shown when the scenario has a TOR window.
        self.decision_label = ttk.Label(frame, text="TOR Decision Flow (click a node to jump to its alert prompt)", style="Section.TLabel")
        self.decision_label.grid(row=7, column=0, sticky="w", pady=(5, 0))
        self.decision_tree = ttk.Treeview(frame, columns=("time", "rule"), show="tree headings", height=5)
        self.decision_tree.heading("#0", text="decision path")
        self.decision_tree.heading("time", text="t")
        self.decision_tree.heading("rule", text="rule/req")
        self.decision_tree.column("#0", width=230, stretch=True)
        self.decision_tree.column("time", width=54, stretch=False)
        self.decision_tree.column("rule", width=150, stretch=True)
        self.decision_tree.grid(row=8, column=0, sticky="nsew", pady=(3, 0))
        self.decision_tree.bind("<<TreeviewSelect>>", self._on_embed_decision_selected)
        # hidden until a TOR window exists
        self.decision_label.grid_remove()
        self.decision_tree.grid_remove()

        self._design_layout_job = None
        self.design_viewport.bind("<Configure>", self._schedule_embedded_layout)
        frame.bind("<Configure>", self._schedule_embedded_layout)
        self.design_viewport.bind("<Destroy>", self._cancel_embedded_layout)
        # Keep scrolling local to this preview; editable controls and trees
        # retain their own wheel behavior.
        for widget in (self.design_viewport, frame, self.scene_canvas,
                       self.timeline_canvas, self.design_summary):
            widget.bind("<MouseWheel>", self._scroll_embedded_viewport)

    def _cancel_embedded_layout(self, _event=None):
        if self._design_layout_job is not None:
            self.design_viewport.after_cancel(self._design_layout_job)
            self._design_layout_job = None

    def _schedule_embedded_layout(self, _event=None):
        self._cancel_embedded_layout()
        self._design_layout_job = self.design_viewport.after_idle(self._layout_embedded_viewport)

    def _layout_embedded_viewport(self):
        self._design_layout_job = None
        viewport = self.design_viewport
        width = viewport.winfo_width()
        if width <= 1:
            return
        wraplength = max(1, width - 8)
        if int(self.design_summary.cget("wraplength")) != wraplength:
            self.design_summary.configure(wraplength=wraplength)
            viewport.itemconfigure(self._design_content_window, width=width)
            # Let Tk settle wrapping before measuring the content's height.
            self._schedule_embedded_layout()
            return
        # Preserve each section's natural height instead of shrinking the road
        # underneath the event tables. Small windows can scroll to the tables.
        height = max(viewport.winfo_height(), self.design_content.winfo_reqheight())
        viewport.itemconfigure(self._design_content_window, width=width, height=height)
        viewport.configure(scrollregion=(0, 0, width, height))

    def _scroll_embedded_viewport(self, event):
        if event.delta and self.design_viewport.yview() != (0.0, 1.0):
            self.design_viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _redraw_all(self):
        self.time_label.configure(text="t={:.2f}s".format(self.current_t))
        self._redraw_scene()
        self._redraw_timeline()

    # --- playback ------------------------------------------------------------
    def _play_speed(self):
        text = str(self.play_speed_var.get() or "1x").lower().replace("x", "").strip()
        try:
            return max(0.1, float(text))
        except Exception:
            return 1.0

    def _toggle_play(self):
        if not self._has_data:
            return
        self.playing = not self.playing
        if self.playing:
            total = max(0.1, self.model["total_time_s"])
            if self.current_t >= total - 1e-3:  # restart from the beginning
                self.current_t = 0.0
                self.time_var.set(0.0)
            self.play_button.configure(text="Pause")
            self._play_step()
        else:
            self.play_button.configure(text="Play")

    def _play_step(self):
        if not self.playing:
            return
        try:
            total = max(0.1, self.model["total_time_s"])
            self.current_t += 0.04 * self._play_speed()  # ~25 fps wall clock
            if self.current_t >= total:
                self.current_t = total
                self.time_var.set(total)
                self._redraw_all()
                self.playing = False
                self.play_button.configure(text="Play")
                return
            self.time_var.set(self.current_t)
            self._redraw_all()
            self._play_job = self.scene_canvas.after(40, self._play_step)
        except Exception:
            self.playing = False

    # --- node -> prompt provenance -------------------------------------------
    def _event_role(self, event):
        import re
        match = re.search(r"\b(ego|v\d+|pedestrian_\d+|obstacle_\d+)\b", str(event.get("label") or ""), re.I)
        return match.group(1).lower() if match else None

    def _event_kind(self, event):
        return self._CATEGORY_KIND.get(str(event.get("category") or "").strip().lower())

    # --- click a vehicle on the scene -> edit -> rewrite its prompt line -----
    def _embed_obstacle_hit_at(self, x, y):
        hits = getattr(self, "obstacle_canvas_hits", [])
        for hit in reversed(hits):
            box = hit["label_bbox"]
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return hit
        nearby = [hit for hit in hits if abs(hit["x"] - x) <= 12 and abs(hit["y"] - y) <= 12]
        if len(nearby) == 1:
            return nearby[0]
        active = [hit for hit in nearby if hit["phase"] == "active"]
        return active[0] if len(active) == 1 else None

    def _embed_pedestrian_hit_at(self, x, y):
        for hit in reversed(getattr(self, "pedestrian_canvas_hits", [])):
            box = hit.get("label_bbox")
            if (abs(x - hit["x"]) <= 12 and abs(y - hit["y"]) <= 16
                    or box and box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                return hit
        return None

    def _embed_vehicle_hit_at(self, x, y):
        hits = self.vehicle_canvas_hits or []
        if not hits:
            return None
        nearest = min(hits, key=lambda item: (x - item["x"]) ** 2 + (y - item["y"]) ** 2)
        if abs(x - nearest["x"]) <= max(24, nearest["w"]) and abs(y - nearest["y"]) <= max(24, nearest["h"] * 2):
            return nearest
        return None

    def _on_embed_scene_motion(self, event):
        cursor = "hand2" if (self._has_data and (
            self._embed_pedestrian_hit_at(event.x, event.y) or self._embed_obstacle_hit_at(event.x, event.y)
            or self._embed_vehicle_hit_at(event.x, event.y))) else ""
        try:
            if self.scene_canvas.cget("cursor") != cursor:
                self.scene_canvas.configure(cursor=cursor)
        except Exception:
            pass

    def _on_embed_scene_click(self, event):
        if not self._has_data:
            return
        obstacle = self._embed_obstacle_hit_at(event.x, event.y)
        if obstacle is not None:
            if self.playing:
                self._toggle_play()
            for index, item in enumerate(self.model.get("events", [])):
                if item["category"] == "obstacle" and item["label"] == obstacle["role"] + " appears":
                    self._embed_select_event(index)
                    break
            return
        pedestrian = self._embed_pedestrian_hit_at(event.x, event.y)
        if pedestrian is not None:
            if self.playing:
                self._toggle_play()
            for index, item in enumerate(self.model.get("events", [])):
                if item["category"] == "pedestrian" and item["label"] == pedestrian["role"] + " crossing":
                    self._embed_select_event(index)
                    break
            return
        hit = self._embed_vehicle_hit_at(event.x, event.y)
        if hit is None:
            return
        if self.playing:  # freeze the timeline at the clicked moment
            self._toggle_play()
        role = hit["role"]
        self.selected_vehicle_role = role
        self._redraw_scene()
        self._highlight_prompt_for_event({"category": "vehicle", "label": role_to_prompt_name(role)})
        self._open_embed_vehicle_editor(role, event)

    def _model_vehicle(self, role):
        for vehicle in self.model.get("vehicles", []) or []:
            if vehicle.get("role") == role:
                return vehicle
        return None

    @staticmethod
    def _lane_name_from_y(lane_y):
        best = "Middle"
        best_distance = None
        for lane_name in VIS_LANE_ORDER:
            distance = abs(float(lane_y) - VIS_LANE_Y[lane_name])
            if best_distance is None or distance < best_distance:
                best, best_distance = lane_name, distance
        return best

    def _open_embed_vehicle_editor(self, role, event):
        tk = self.tk
        ttk = self.ttk
        old = getattr(self, "_vehicle_editor_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        name = role_to_prompt_name(role)
        time_s = max(0.0, round(float(self.current_t or 0.0) * 10.0) / 10.0)
        at_start = time_s < 0.05
        vehicles = self.payload.get("vehicles") if isinstance(self.payload.get("vehicles"), dict) else {}
        cfg = vehicles.get(role) if isinstance(vehicles.get(role), dict) else {}
        model_vehicle = self._model_vehicle(role)
        ego_vehicle = self._model_vehicle("ego")
        has_time_gap_profile = bool(cfg.get("time_gap_points"))
        has_speed_profile = bool(cfg.get("speed_points"))

        # the editor shows the definition values at t=0 and the *sampled
        # timeline state* at t>0 (lane mid-change, interpolated speed, the
        # live gap to ego), so edits anchor to what is on screen.
        shown_time_gap = None
        if at_start:
            lane_now = visual_lane_name(cfg.get("lane", "Middle"))
            speed_now = float_or_none(
                cfg.get("speed_kmh", self.payload.get("ego_speed_kmh") if role == "ego" else None)
            )
            shown_time_gap = float_or_none(cfg.get("initial_time_gap_s"))
            if shown_time_gap is None:
                gap_m = float_or_none(cfg.get("initial_gap_m"))
                if gap_m not in (None, 0.0):
                    ego_mps = max(0.1, visual_float(self.model.get("ego_speed_kmh"), 50.0) / 3.6)
                    shown_time_gap = gap_m / ego_mps
        else:
            sample = (
                self._sample_vehicle_at(model_vehicle, time_s)
                if model_vehicle is not None
                else {"s": 0.0, "lane_y": 1.0, "speed_kmh": 0.0}
            )
            lane_now = self._lane_name_from_y(sample.get("lane_y", 1.0))
            speed_now = float_or_none(sample.get("speed_kmh"))
            if role != "ego" and ego_vehicle is not None:
                ego_sample = self._sample_vehicle_at(ego_vehicle, time_s)
                ego_mps = max(0.1, visual_float(ego_sample.get("speed_kmh"), 50.0) / 3.6)
                center_delta_m = (
                    visual_float(sample.get("s"), 0.0) - visual_float(ego_sample.get("s"), 0.0)
                )
                # prompt time gaps are bumper-to-bumper at runtime; convert the
                # center delta so the prefilled value round-trips exactly
                shown_time_gap = visual_center_delta_to_time_gap_s(center_delta_m, ego_mps)

        speed_editable = at_start or (role != "ego" and not has_time_gap_profile)
        gap_editable = role != "ego" and (at_start or not has_speed_profile)
        # the converter has no executable ego lane-change event; an ego lane
        # keyframe would make the whole prompt fail conversion.
        lane_editable = at_start or role != "ego"

        win = tk.Toplevel(self.scene_canvas)
        self._vehicle_editor_win = win
        win.title("Edit {} @ t={}s".format(name, format_prompt_number(time_s)))
        win.resizable(False, False)
        try:
            configure_inkspace_window(win)
        except Exception:
            pass
        try:
            win.transient(self.scene_canvas.winfo_toplevel())
        except Exception:
            pass
        win.geometry("+{}+{}".format(event.x_root + 12, event.y_root + 12))

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        if at_start:
            mode_text = "t=0: edits the {} definition line.".format(name)
        else:
            mode_text = "t={}s: writes timeline keyframes (lane change / gap / speed points).".format(
                format_prompt_number(time_s)
            )
        ttk.Label(frame, text=mode_text, wraplength=260, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        lane_var = tk.StringVar(value=lane_now)
        speed_var = tk.StringVar(value="" if speed_now is None else format_prompt_number(speed_now))
        gap_mode_var = tk.StringVar(value="behind" if (shown_time_gap or 0.0) < 0 else "ahead")
        gap_value_var = tk.StringVar(
            value="" if shown_time_gap is None else format_prompt_number(abs(shown_time_gap))
        )
        orig_lane = visual_lane_name(lane_now)
        orig_speed = speed_now
        orig_gap_signed = shown_time_gap

        ttk.Label(frame, text="Lane").grid(row=1, column=0, sticky="w", padx=(0, 6))
        ttk.Combobox(
            frame, textvariable=lane_var, values=VIS_LANE_ORDER,
            state="readonly" if lane_editable else "disabled", width=9,
        ).grid(row=1, column=1, sticky="ew")
        ttk.Label(frame, text="Speed (km/h)").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        speed_entry = ttk.Entry(
            frame, textvariable=speed_var, width=9,
            state="normal" if speed_editable else "disabled",
        )
        speed_entry.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        if role != "ego":
            ttk.Label(frame, text="Gap (s)").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
            gap_row = ttk.Frame(frame)
            gap_row.grid(row=3, column=1, sticky="ew", pady=(6, 0))
            gap_state = "readonly" if gap_editable else "disabled"
            ttk.Combobox(
                gap_row, textvariable=gap_mode_var, values=("ahead", "behind"),
                state=gap_state, width=7,
            ).pack(side="left")
            ttk.Entry(
                gap_row, textvariable=gap_value_var, width=7,
                state="normal" if gap_editable else "disabled",
            ).pack(side="left", padx=(4, 0))

        if not lane_editable:
            field_hint = "ego lane/speed can only be edited at t=0 (no ego keyframe support)."
        elif not speed_editable and role == "ego":
            field_hint = "ego speed can only be edited at t=0 (no ego speed profile)."
        elif not speed_editable:
            field_hint = "speed follows the time_gap profile; edit gap instead."
        elif not gap_editable and role != "ego":
            field_hint = "gap follows the speed profile; edit speed instead."
        else:
            field_hint = "Applies to the prompt text; the preview refreshes from it."
        status_var = tk.StringVar(value=field_hint)
        ttk.Label(frame, textvariable=status_var, wraplength=260, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        def close(_event=None):
            self._vehicle_editor_win = None
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            # the Add Scenario dialog is modal; give it back its grab
            owner_top = getattr(self.owner, "top", None)
            if owner_top is not None:
                try:
                    owner_top.grab_set()
                except Exception:
                    pass

        def apply(_event=None):
            speed_text = speed_var.get().strip() if speed_editable else ""
            gap_text = gap_value_var.get().strip() if gap_editable else ""
            try:
                speed_kmh = float(speed_text) if speed_text else None
                if speed_kmh is not None and speed_kmh < 0:
                    raise ValueError("speed must be >= 0")
                gap_value = float(gap_text) if gap_text else None
            except ValueError:
                status_var.set("Speed and gap must be numbers.")
                return
            if at_start:
                gap_mode = gap_mode_var.get() if (role != "ego" and gap_value is not None) else None
                if self._apply_embed_vehicle_edit(
                    role,
                    lane=lane_var.get(),
                    speed_kmh=speed_kmh,
                    gap_mode=gap_mode,
                    gap_value=gap_value,
                    gap_unit="s",
                ):
                    close()
                else:
                    status_var.set("Could not update the prompt line.")
                return
            gap_signed = None
            if gap_value is not None:
                gap_signed = -abs(gap_value) if gap_mode_var.get() == "behind" else abs(gap_value)
            lane_changed = lane_editable and visual_lane_name(lane_var.get()) != orig_lane
            speed_changed = (
                speed_editable and speed_kmh is not None
                and (orig_speed is None or abs(speed_kmh - orig_speed) > 0.05)
            )
            gap_changed = (
                gap_editable and gap_signed is not None
                and (orig_gap_signed is None or abs(gap_signed - orig_gap_signed) > 0.05)
            )
            if not (lane_changed or speed_changed or gap_changed):
                status_var.set("No changes at t={}s.".format(format_prompt_number(time_s)))
                return
            if speed_changed and gap_changed:
                status_var.set(
                    "Change gap or speed at a keyframe, not both (they are exclusive longitudinal modes)."
                )
                return
            if self._apply_embed_vehicle_keyframe_edit(
                role,
                time_s,
                lane=lane_var.get() if lane_changed else None,
                speed_kmh=speed_kmh if speed_changed else None,
                time_gap_s=gap_signed if gap_changed else None,
            ):
                close()
            else:
                status_var.set("Could not update the prompt.")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Apply to Prompt", command=apply, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right")
        win.bind("<Return>", apply)
        win.bind("<Escape>", close)
        win.protocol("WM_DELETE_WINDOW", close)
        try:
            win.grab_set()
        except Exception:
            pass
        if speed_editable:
            speed_entry.focus_set()

    def _apply_embed_vehicle_edit(self, role, lane, speed_kmh, gap_mode, gap_value, gap_unit):
        owner = self.owner
        widget = getattr(owner, "prompt_text", None)
        if widget is None:
            return False
        prompt = widget.get("1.0", "end-1c")
        try:
            line_no, new_line, is_insert = rewrite_vehicle_prompt_line(
                prompt, role, lane=lane, speed_kmh=speed_kmh,
                gap_mode=gap_mode, gap_value=gap_value, gap_unit=gap_unit,
            )
        except Exception as exc:
            write_exception(exc)
            return False
        tk_line = line_no + 1
        try:
            if is_insert:
                last_line = int(widget.index("end-1c").split(".")[0])
                if tk_line > last_line:
                    widget.insert("end", "\n" + new_line)
                    tk_line = last_line + 1
                else:
                    widget.insert("{}.0".format(tk_line), new_line + "\n")
            else:
                widget.delete("{}.0".format(tk_line), "{}.0 lineend".format(tk_line))
                widget.insert("{}.0".format(tk_line), new_line)
        except Exception as exc:
            write_exception(exc)
            return False
        # editing the widget fires <<Modified>> -> syntax highlight + debounced
        # design refresh, so the canvas re-renders from the updated prompt.
        self._relink_role_after_update = role
        try:
            widget.tag_configure("design_link", background=INKSPACE_COLORS["brand_tint"])
            widget.tag_remove("design_link", "1.0", "end")
            widget.tag_add("design_link", "{}.0".format(tk_line), "{}.0 lineend".format(tk_line))
            widget.see("{}.0".format(tk_line))
        except Exception:
            pass
        status = getattr(owner, "status_var", None)
        if status is not None:
            status.set("Design edit -> prompt line {}: {}".format(tk_line, new_line.strip()))
        return True

    def _apply_embed_vehicle_keyframe_edit(self, role, time_s, lane=None, speed_kmh=None, time_gap_s=None):
        """Write time-anchored edits at ``time_s`` into the owner's prompt.

        lane -> upsert a ``t=..s: v1 -> lane`` line; time_gap_s -> upsert a
        point on the ``v1 time_gap_points:`` line; speed_kmh -> upsert a point
        on ``v1 speed_points:`` plus the required ``longitudinal_mode=speed``
        line. Each operation re-reads the widget text, so earlier inserts
        cannot skew later line positions.
        """
        owner = self.owner
        widget = getattr(owner, "prompt_text", None)
        if widget is None:
            return False
        name = role_to_prompt_name(role)
        operations = []
        if lane is not None and name != "ego":  # backstop: converter rejects ego lane keyframes
            operations.append(
                ("lane", lambda prompt: upsert_lane_change_prompt_line(prompt, role, time_s, lane))
            )
        if time_gap_s is not None:
            operations.append(
                ("time_gap", lambda prompt: upsert_points_prompt_line(prompt, role, "time_gap", time_s, time_gap_s))
            )
        if speed_kmh is not None:
            operations.append(
                ("mode", lambda prompt: ensure_longitudinal_mode_prompt_line(prompt, role, "speed"))
            )
            operations.append(
                ("speed", lambda prompt: upsert_points_prompt_line(prompt, role, "speed", time_s, speed_kmh))
            )
        edited_tk_lines = []
        summaries = []
        notes = []
        for op_kind, operation in operations:
            prompt = widget.get("1.0", "end-1c")
            try:
                edit = operation(prompt)
            except Exception as exc:
                write_exception(exc)
                return False
            if edit is None:  # e.g. longitudinal_mode already declared
                continue
            line_no, text, is_insert = edit
            if is_insert and op_kind in ("time_gap", "speed"):
                # a brand-new points line switches the vehicle from Traffic
                # Manager following to scripted control for the whole run
                notes.append(
                    "{} now follows a scripted {} profile".format(
                        name, "time-gap" if op_kind == "time_gap" else "speed"
                    )
                )
            tk_line = line_no + 1
            try:
                if is_insert:
                    last_line = int(widget.index("end-1c").split(".")[0])
                    if tk_line > last_line:
                        widget.insert("end", "\n" + text)
                        tk_line = last_line + 1
                    else:
                        widget.insert("{}.0".format(tk_line), text + "\n")
                    # an insert above an already-edited line shifts it down
                    edited_tk_lines = [
                        previous + 1 if tk_line <= previous else previous
                        for previous in edited_tk_lines
                    ]
                else:
                    widget.delete("{}.0".format(tk_line), "{}.0 lineend".format(tk_line))
                    widget.insert("{}.0".format(tk_line), text)
            except Exception as exc:
                write_exception(exc)
                return False
            edited_tk_lines.append(tk_line)
            summaries.append(text.strip())
        if not edited_tk_lines:
            return True
        self._relink_role_after_update = role
        try:
            widget.tag_configure("design_link", background=INKSPACE_COLORS["brand_tint"])
            widget.tag_remove("design_link", "1.0", "end")
            for tk_line in edited_tk_lines:
                widget.tag_add("design_link", "{}.0".format(tk_line), "{}.0 lineend".format(tk_line))
            widget.see("{}.0".format(edited_tk_lines[-1]))
        except Exception:
            pass
        status = getattr(owner, "status_var", None)
        if status is not None:
            message = "Design edit @ t={}s -> {}".format(
                format_prompt_number(time_s), " | ".join(summaries)
            )
            if notes:
                message += "  (note: {})".format("; ".join(notes))
            status.set(message)
        return True

    def _scrub_to_x(self, x):
        width = max(260, self.timeline_canvas.winfo_width())
        left = 42
        right = width - 16
        total = max(0.1, self.model["total_time_s"])
        if x <= left:
            t = 0.0
        elif x >= right:
            t = total
        else:
            t = (x - left) / max(1.0, right - left) * total
        self.time_var.set(t)
        self.current_t = t
        self._redraw_all()

    def _on_timeline_click(self, event):
        events = self.model.get("events") or []
        if not self._has_data or not events:
            self._scrub_to_x(event.x)
            return
        width = max(260, self.timeline_canvas.winfo_width())
        left = 42
        right = width - 16
        total = max(0.1, self.model["total_time_s"])
        if event.x <= left:
            t = 0.0
        elif event.x >= right:
            t = total
        else:
            t = (event.x - left) / max(1.0, right - left) * total
        index, _evt = min(enumerate(events), key=lambda item: abs(item[1]["time_s"] - t))
        self._embed_select_event(index)

    def _on_embed_event_selected(self, _event=None):
        if self._embed_syncing:
            return
        selection = self.event_tree.selection()
        if not selection:
            return
        if selection[0] == getattr(self, "_embed_selected_tree_row", None):
            return  # Tk delivers programmatic selection events after syncing.
        if selection[0].startswith('conditional_'):
            self._embed_selected_tree_row = selection[0]
            event = self.model['conditional_events'][int(selection[0].split('_')[1])]
            self.selected_event = event
            self.owner.status_var.set(event['details'] + ' · Evaluated live during simulation.')
            return
        try:
            row = self._embed_event_rows[int(selection[0])]
            index = row["event_indices"][0]
        except Exception:
            return
        self._embed_selected_tree_row = selection[0]
        self._embed_select_event(index, sync_tree=False)

    def _embed_select_event(self, index, sync_tree=True):
        events = self.model.get("events") or []
        if index < 0 or index >= len(events):
            return
        evt = events[index]
        self.selected_event = evt
        self.current_t = evt["time_s"]
        self.time_var.set(evt["time_s"])
        if sync_tree:
            self._embed_syncing = True
            try:
                row = str(self._embed_event_row_by_index[index])
                self._embed_selected_tree_row = row
                self.event_tree.selection_set(row)
                self.event_tree.see(row)
            except Exception:
                pass
            finally:
                self._embed_syncing = False
        self._redraw_all()
        self._highlight_prompt_for_event(evt)

    def _highlight_prompt_for_event(self, event):
        owner = self.owner
        prompt_widget = getattr(owner, "prompt_text", None)
        status = getattr(owner, "status_var", None)
        if prompt_widget is None or not isinstance(event, dict):
            return
        try:
            from carla_nl.prompt_provenance import source_line_for
        except Exception:
            return
        prompt = prompt_widget.get("1.0", "end")
        span = source_line_for(prompt, role=self._event_role(event), kind=self._event_kind(event))
        try:
            prompt_widget.tag_remove("design_link", "1.0", "end")
        except Exception:
            pass
        if span is None:
            if status is not None:
                status.set("No prompt line maps to this node ({}).".format(event.get("label", "")))
            return
        line = span.line_no + 1
        try:
            prompt_widget.tag_configure("design_link", background=INKSPACE_COLORS["brand_tint"])
            prompt_widget.tag_add("design_link", "{}.0".format(line), "{}.0 lineend".format(line))
            prompt_widget.see("{}.0".format(line))
            prompt_widget.mark_set("insert", "{}.0".format(line))
            prompt_widget.focus_set()
        except Exception:
            pass
        if status is not None:
            status.set("Node -> prompt line {}: {}".format(line, span.text))

    def _populate_embed_events(self):
        from carla_nl.prompt_review import design_event_rows
        tree = getattr(self, "event_tree", None)
        if tree is None:
            return
        self._embed_syncing = True
        self._embed_selected_tree_row = None
        try:
            for iid in tree.get_children():
                tree.delete(iid)
            prompt_widget = getattr(self.owner, "prompt_text", None)
            prompt = prompt_widget.get("1.0", "end-1c") if prompt_widget is not None else ""
            self._embed_event_rows = design_event_rows(self.payload, self.model.get("events") or [], prompt)
            self._embed_event_row_by_index = {}
            for index, row in enumerate(self._embed_event_rows):
                for event_index in row["event_indices"]:
                    self._embed_event_row_by_index.setdefault(event_index, index)
                start, end = row["start_s"], row["end_s"]
                tree.insert("", "end", iid=str(index), values=(
                    "{:g}–{:g}s".format(start, end) if end > start else "{:g}s".format(start),
                    row["actor"], row["description"],
                ))
            for index, event in enumerate(self.model.get('conditional_events', [])):
                tree.insert('', 'end', iid='conditional_{}'.format(index),
                            values=(event['condition'], 'Takeover alert', event['details']))
        finally:
            self._embed_syncing = False

    def _populate_embed_decision_tree(self):
        """Fill the TOR decision tree; show the section only when one exists."""
        tree = getattr(self, "decision_tree", None)
        if tree is None:
            return
        roots = self.model.get("decision_tree") or []
        self._embed_syncing = True
        try:
            for iid in tree.get_children():
                tree.delete(iid)
            self.decision_tree_nodes = {}

            def add_node(parent, node, path):
                iid = "dt_{}".format("_".join(str(part) for part in path))
                self.decision_tree_nodes[iid] = node
                time_text = "{:.2f}".format(node["time_s"]) if node.get("time_s") is not None else ""
                tree.insert(
                    parent, "end", iid=iid,
                    text=node.get("title", ""),
                    values=(time_text, node.get("rule", "")),
                    open=True,
                )
                for child_index, child in enumerate(node.get("children", []) or []):
                    add_node(iid, child, path + [child_index])

            for root_index, root in enumerate(roots):
                add_node("", root, [root_index])
        finally:
            self._embed_syncing = False
        if roots:
            self.decision_label.grid()
            tree.grid()
        else:
            self.decision_label.grid_remove()
            tree.grid_remove()

    def _on_embed_decision_selected(self, _event=None):
        if self._embed_syncing:
            return
        selection = self.decision_tree.selection()
        if not selection:
            return
        node = self.decision_tree_nodes.get(selection[0])
        if not isinstance(node, dict):
            return
        self.selected_decision_node = node
        self.selected_event = None
        t = node.get("time_s")
        if t is not None:
            self.current_t = float(t)
            self.time_var.set(float(t))
            self._redraw_all()
        # decision nodes map back to the TOR / alert prompt line
        self._highlight_prompt_for_event({"category": "decision", "label": node.get("title", "")})

    def update(self, payload):
        """Rebuild the visual model from an in-memory scenario dict and redraw."""
        self.payload = payload if isinstance(payload, dict) else {}
        self.model = build_scenario_visual_model(self.payload)
        self._has_data = bool(self.payload)
        self.selected_event = None
        relink_role = self._relink_role_after_update
        self._relink_role_after_update = None
        owner_prompt = getattr(self.owner, "prompt_text", None)
        if owner_prompt is not None and relink_role is None:
            try:
                owner_prompt.tag_remove("design_link", "1.0", "end")
            except Exception:
                pass
        total = max(0.1, self.model["total_time_s"])
        try:
            self.time_scale.configure(to=total)
        except Exception:
            pass
        if self.current_t > total:
            self.current_t = total
            self.time_var.set(total)
        self.selected_decision_node = None
        self._populate_embed_events()
        self._populate_embed_decision_tree()
        from carla_nl.prompt_review import vehicle_review_lines
        prompt = owner_prompt.get("1.0", "end-1c") if owner_prompt is not None else ""
        facts = vehicle_review_lines(payload, prompt)
        ego_summary = next((line for line in facts if line.startswith("Ego:")), "")
        instructions = "Click a vehicle to edit its description."
        if self.model.get("obstacles"):
            instructions = "Click a vehicle to edit; click an obstacle label to locate its description."
        if self.model.get("pedestrians"):
            instructions = "Click a vehicle to edit; click a pedestrian to locate its description."
            if self.model.get("obstacles"):
                instructions = "Click a vehicle to edit; click a person or obstacle label to locate its description."
            if any(p["position_reference"] == "ego_at_crossing" for p in self.model["pedestrians"]):
                instructions += "\nCrossing preview uses planned ego position at crossing start."
        obstacle_count = len(self.model.get("obstacles", []))
        if obstacle_count:
            ego_summary += " · {} obstacle{}".format(obstacle_count, "s" if obstacle_count != 1 else "")
            if any(o.get("position_reference") == "vehicle_at_spawn" for o in self.model["obstacles"]):
                instructions += "\nObstacle locations use planned vehicle positions at appearance; CARLA uses actual positions."
        self.scene_canvas.configure(height=260 if self.model.get("pedestrians") or obstacle_count > 1 else 170)
        self._base_summary = "{} · {:g}s · {}\n{}".format(
            self.model.get("scenario_name", "scenario"), self.model.get("total_time_s", 0.0), ego_summary, instructions)
        self.summary_var.set(self._base_summary)
        self._schedule_embedded_layout()
        self._redraw_all()

    def set_stale(self, stale):
        if self._has_data:
            prefix = "Design shows the previous analysis. Click Analyze to update.\n" if stale else ""
            self.summary_var.set(prefix + self._base_summary)


def open_tor_adaptive_prompt_dialog(parent, tk, ttk, scrolledtext, text_var, title="TOR Adaptive Prompt"):
    top = tk.Toplevel(parent)
    top.title(title)
    top.geometry("760x520")
    top.minsize(620, 420)
    configure_inkspace_window(top)
    try:
        top.transient(parent)
    except Exception:
        pass

    root = ttk.Frame(top, padding=12)
    root.pack(fill="both", expand=True)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    ttk.Label(root, text="Edit adaptive TOR stage rules", style="Section.TLabel").grid(row=0, column=0, sticky="w")
    text = scrolledtext.ScrolledText(root, wrap="word", height=14, font=("Consolas", 10), undo=True)
    style_inkspace_text(text)
    text.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
    initial = str(text_var.get() or "").strip() or TOR_ADAPTIVE_PROMPT_EXAMPLE
    text.insert("1.0", initial)

    hint = (
        "Examples: stage1 -> stage2: require=gaze_screen | "
        "stage2 -> ready: require=target_fixation, fixation=0.5s | manual: require=ready"
    )
    ttk.Label(root, text=hint).grid(row=2, column=0, sticky="ew", pady=(0, 8))

    buttons = ttk.Frame(root)
    buttons.grid(row=3, column=0, sticky="ew")
    buttons.columnconfigure(2, weight=1)

    def load_default():
        text.delete("1.0", "end")
        text.insert("1.0", TOR_ADAPTIVE_PROMPT_EXAMPLE)

    def save_and_close():
        value = text.get("1.0", "end").strip()
        # Validate now so typos are caught before the scenario JSON is generated.
        parse_tor_adaptive_prompt(value)
        text_var.set(value)
        top.destroy()

    ttk.Button(buttons, text="Default", command=load_default).grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Button(buttons, text="Save", command=save_and_close).grid(row=0, column=1, sticky="w")
    ttk.Button(buttons, text="Cancel", command=top.destroy).grid(row=0, column=3, sticky="e")
    text.focus_set()
    top.grab_set()
    return top


# Prompt command-bar font.
PROMPT_FONT = ("Consolas", 11)
PROMPT_FONT_BOLD = ("Consolas", 11, "bold")


def add_scenario_sash_position(total_width, left_fraction=0.52, min_pane_width=560):
    """Return the Add Scenario prompt/design sash position for a mapped width."""
    width = max(0, int(total_width))
    if width <= 0:
        return 0
    if width < int(min_pane_width) * 2:
        return width // 2
    target = int(round(width * float(left_fraction)))
    return max(int(min_pane_width), min(width - int(min_pane_width), target))


def add_scenario_compact_style(widget_class, current_style=""):
    """Map a ttk widget to its dialog-local compact style, if applicable."""
    widget_class = str(widget_class or "")
    current_style = str(current_style or "")
    if widget_class == "TButton":
        if current_style == "Accent.TButton":
            return "AddScenario.Accent.TButton"
        return "AddScenario.TButton"
    if widget_class == "TLabel":
        label_roles = {
            "Title.TLabel": "AddScenario.Title.TLabel",
            "Section.TLabel": "AddScenario.Section.TLabel",
            "Muted.TLabel": "AddScenario.Muted.TLabel",
        }
        return label_roles.get(current_style, "AddScenario.TLabel")
    style_by_class = {
        "TMenubutton": "AddScenario.TMenubutton",
        "TEntry": "AddScenario.TEntry",
        "TCombobox": "AddScenario.TCombobox",
        "TCheckbutton": "AddScenario.TCheckbutton",
        "TLabelframe": "AddScenario.TLabelframe",
        "TNotebook": "AddScenario.TNotebook",
        "Treeview": "AddScenario.Treeview",
    }
    return style_by_class.get(widget_class)

# Five reading categories; fields and diagnostics have separate treatments.
PROMPT_SYNTAX_COLORS = {
    "ego": "#91bdff",
    "vehicle": "#91bdff",
    "obstacle": "#91bdff",
    "pedestrian": "#91bdff",
    "action": "#c7a7ff",
    "alert": "#c7a7ff",
    "op": "#c7a7ff",
    "time": "#79d9e8",
    "speed": "#f0bd87",
    "lane": "#a4d7b1",
    "gap": "#a4d7b1",
    "kw": "#79d9e8",
}

PROMPT_FIELD_COLORS = {"foreground": "#ffe0a3", "background": "#493c24"}
PROMPT_SYNTAX_LEGEND = (("ego", "Vehicles / people / objects"), ("action", "Actions / alerts"),
                        ("time", "Time"), ("speed", "Speed"), ("lane", "Position"))

# Hover tooltip text per category.
PROMPT_SYNTAX_TOOLTIP = {
    "ego": "Ego — the driven (Tesla) vehicle",
    "vehicle": "Vehicle — a support vehicle (vN)",
    "lane": "Lane — driving lane (left / middle / right)",
    "speed": "Speed — target speed (mph, km/h or m/s)",
    "time": "Time — event time / duration (seconds)",
    "gap": "Gap — headway to ego (ahead / behind)",
    "obstacle": "Obstacle — static or dropped hazard",
    "pedestrian": "Pedestrian — a person crossing the road (pedN)",
    "alert": "Alert — warning, takeover reminder or presentation channel",
    "action": "Action — a requested maneuver or speed change",
    "op": "Direction — lane change or pedestrian crossing (→)",
    "kw": "Scenario header — total duration of the scene",
}

# (category, regex) in PRIORITY order; the first claim on a character span wins.
_PROMPT_NUMBER = r'(?:[+-]?(?:\d+(?:\.\d+)?|\.\d+)|(?:' + NUMBER_WORDS + r')(?:[ -]+(?:' + NUMBER_WORDS + r'|and|point))*)'
_PROMPT_POSITION = r'(?:ahead(?:\s+of)?|behind(?:\s+of)?|in\s+front\s+of|in\s+back\s+of|at\s+the\s+rear\s+of)'
_PROMPT_CATEGORY_RULES = [
    ("gap", re.compile(rf"\b{_PROMPT_NUMBER}[\s-]*(?:(?:{SECONDS_UNIT}|met(?:er|re)s?|m)\b\s*)?{_PROMPT_POSITION}\b", re.I)),
    ("gap", re.compile(rf"\b(?:ahead|behind)\s*=\s*\d+(?:\.\d+)?(?:\s*{SECONDS_UNIT})?\b", re.I)),
    ("gap", re.compile(rf"\b{_PROMPT_POSITION}\b", re.I)),
    ("speed", re.compile(rf"\b{_PROMPT_NUMBER}\s*(?:mph|km\s*/?\s*h|kmh|kph|m/s|miles\s+(?:per|an)\s+hour|kilomet(?:er|re)s?\s+per\s+hour|met(?:er|re)s?\s+per\s+second)\b", re.I)),
    ("speed", re.compile(r"\bspeed\b", re.I)),
    ("gap", re.compile(rf"\b{_PROMPT_NUMBER}\s*(?:met(?:er|re)s?|m)\b(?!\s*/)", re.I)),
    ("time", re.compile(rf"\bt\s*=\s*\d+(?:\.\d+)?(?:\s*{SECONDS_UNIT})?\b", re.I)),
    ("time", re.compile(r"\b(?:duration|deadline|ttc|time[ -](?:to|until)[ -](?:collision|impact))\b", re.I)),
    ("time", re.compile(rf"\b{_PROMPT_NUMBER}[\s-]*{SECONDS_UNIT}\b", re.I)),
    ("alert", PROMPT_CUE_BEEP_RE),
    ("alert", PROMPT_CUE_SPEECH_RE),
    ("alert", PROMPT_CUE_CIRCLE_RE),
    ("alert", PROMPT_WARNING_RE),
    ("alert", PROMPT_ALERT_RE),
    ("alert", PROMPT_VISUAL_RE),
    ("alert", PROMPT_AUDIO_RE),
    ("alert", PROMPT_BEEP_RE),
    ("alert", PROMPT_SPEECH_RE),
    ("alert", re.compile(r"\b(?:experiment|markers?|fallback|stage2|ready_text|standard_markers)\b", re.I)),
    ("obstacle", re.compile(r"\b(?:obstacle|obstactle|obsticle|barrier|traffic\s+cone|cone|debris|barrel|box|hazard)(?:[ _#-]*(?:(?:number|no\.)\s*)?[1-9]\d*)?\b", re.I)),
    ("pedestrian", PEDESTRIAN_NAME_RE),
    ("action", re.compile(r"\b(?:turn(?:s|ing)?|chang(?:e|es|ing)|merg(?:e|es|ing)|brak(?:e|es|ing)|accelerat(?:e|es|ing)|decelerat(?:e|es|ing)|slow(?:s|ing)?(?:\s+down)?|stop(?:s|ping)?|go(?:es|ing)?|mov(?:e|es|ing)|cut(?:s|ting)?\s+in|switch(?:es|ing)?|cross(?:es|ing)?|appear(?:s|ing)?|drop(?:s|ping)?)(?:\s+(?:to|into|towards?))?\b", re.I)),
    ("lane", re.compile(r"\b(?:left|middle|right|center|centre)(?:\s+lane)?\b", re.I)),
    ("lane", re.compile(r"\blane\b", re.I)),
    ("vehicle", NUMBERED_VEHICLE_RE),
    ("ego", re.compile(r"\b(?:ego(?:[ _-]+(?:vehicle|vehilce|car))?|(?:host|subject|my|our)\s+(?:vehicle|car))\b", re.I)),
    ("op", re.compile(r"->|=>|→")),
    ("kw", re.compile(r"\bscenario\b", re.I)),
]

# Timed point lists (e.g. "v1 time_gap_points: 7.4s=3s, 18.5s=6s"): the LEFT side
# of each pair is the scenario time t (WHEN the change happens) and the RIGHT side
# is the target value (the new time gap / speed). They get different colours and
# different click-to-Design behaviour.
_PROMPT_POINTS_LINE_RE = re.compile(r"\b(time_gap_points|gap_points|speed_points)\b", re.I)
_PROMPT_POINT_PAIR_RE = re.compile(
    rf"(\d+(?:\.\d+)?(?:\s*{SECONDS_UNIT})?)(\s*=\s*)(\d+(?:\.\d+)?\s*(?:{SECONDS_UNIT}|m|km\s*/?\s*h|kmh|mph)?)(?=\s*(?:,|$))",
    re.I,
)
_PROMPT_TIMELINE_LINE_RE = re.compile(rf"^\s*t\s*=\s*(\d+(?:\.\d+)?)(?:\s*{SECONDS_UNIT})?\s*:", re.I)


def _snippet_int(text, default="1"):
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    return digits or default


# Parameter forms for the snippet bar: instead of inserting a template full of
# placeholder values the user has to re-edit one by one, clicking a snippet
# opens a small form (defaults pre-filled) and inserts the finished line(s).
# fields: (key, label, kind, default, options) -- kind is "entry" or "combo".
SNIPPET_PARAM_FORMS = {
    "takeover alert": {
        "fields": [("mode", "Modality", "combo", "Visual", ("Visual", "Auditory", "Visual + Auditory")),
                   ("audio", "Auditory type", "combo", "Beep", ("Beep", "Speech")),
                   ("target", "Target vehicle", "combo", "v1", ("v1", "v2", "v3", "v4", "v5", "v6")),
                   ("trigger", "Trigger", "combo", "At time", ("At time", "Before collision (TTC)")),
                   ("t", "Starts at (s)", "entry", "10", None),
                   ("ttc", "Time to collision (s)", "entry", "7", None),
                   ("obstacle", "Collision obstacle", "entry", "obstacle 1", None),
                   ("d", "Duration (s)", "entry", "4", None),
                   ("text", "Message", "entry", "Please take over.", None)],
        "build": build_alert_snippet,
    },
    "scenario duration": {
        "fields": [("d", "Duration (s)", "entry", "40", None)],
        "build": lambda v: "Create a {}-second scenario.".format(v["d"].strip()),
    },
    "ego lane/speed": {
        "fields": [
            ("lane", "Lane", "combo", "middle", ("left", "middle", "right")),
            ("speed", "Speed", "entry", "60", None),
            ("unit", "Unit", "combo", "km/h", ("km/h", "mph")),
        ],
        "build": lambda v: "Ego in the {} lane at {} {}.".format(v["lane"], v["speed"].strip(), v["unit"]),
    },
    "v1 lane + gap": {
        "fields": [
            ("n", "Vehicle #", "entry", "1", None),
            ("lane", "Lane", "combo", "right", ("left", "middle", "right")),
            ("rel", "Position", "combo", "ahead", ("ahead", "behind")),
            ("gap", "Gap (s)", "entry", "1.5", None),
            ("speed", "Speed", "entry", "60", None),
            ("unit", "Unit", "combo", "km/h", ("km/h", "mph")),
        ],
        "build": lambda v: "Vehicle {} in the {} lane at {} {} {} seconds {} ego.".format(
            v["n"].strip(), v["lane"], v["speed"].strip(), v["unit"], v["gap"].strip(), 'ahead of' if v["rel"] == 'ahead' else 'behind'
        ),
    },
    "lane change": {
        "fields": [
            ("t", "At time (s)", "entry", "10", None),
            ("actor", "Vehicle", "combo", "vehicle_1", ("ego", "vehicle_1", "vehicle_2", "vehicle_3", "vehicle_4", "vehicle_5", "vehicle_6")),
            ("lane", "Target lane", "combo", "middle", ("left", "middle", "right")),
            ("d", "Duration (s)", "entry", "3", None),
        ],
        "build": lambda v: "At {}s {} changes into the {} lane over {}s.".format(
            v["t"].strip(), v["actor"], v["lane"], v["d"].strip()
        ),
    },
    "speed change": {
        "fields": [("actor", "Vehicle", "combo", "vehicle_1", ("ego", "vehicle_1", "vehicle_2", "vehicle_3", "vehicle_4", "vehicle_5", "vehicle_6")),
                   ("t", "Starts at (s)", "entry", "10", None),
                   ("speed", "Target speed", "entry", "20", None),
                   ("unit", "Speed unit", "combo", "km/h", ("km/h", "mph")),
                   ("d", "Change duration (s)", "entry", "3", None)],
        "build": lambda v: 'At {}s {} changes speed to {} {} over {}s.'.format(v['t'], v['actor'], v['speed'], v['unit'], v['d']),
    },
    "timed gap target": {
        "fields": [
            ("n", "Vehicle #", "entry", "1", None),
            ("points", "Points (t=gap, ...)", "entry", "5s=6s, 15s=3s", None),
        ],
        "build": lambda v: "v{0} longitudinal_mode=time_gap\nv{0} time_gap_points: {1}".format(
            _snippet_int(v["n"]), v["points"].strip() or "5s=6s, 15s=3s"
        ),
    },
    "obstacle barrier": {
        "fields": [
            ("n", "Obstacle number", "entry", "1", None),
            ("kind", "Kind", "combo", "barrier", ("barrier", "cone", "debris", "barrel", "box")),
            ("lane", "Lane", "combo", "middle", ("left", "middle", "right")),
            ("m", "Distance (m)", "entry", "100", None),
            ("direction", "Position at appearance", "combo", "ahead of", ("ahead of", "behind")),
            ("reference", "Reference vehicle (required)", "combo", "", ("ego", "v1", "v2", "v3", "v4", "v5", "v6")),
            ("t", "Appears at (s)", "entry", "10", None),
            ("e", "Ends at (s)", "entry", "30", None),
        ],
        "build": lambda v: "Obstacle {}: a {} in the {} lane {} metres {} {} at {}s until {}s.".format(
            v.get('n', '1'), v["kind"], v["lane"], v["m"].strip() or "100", v.get('direction', 'ahead of'),
            v.get('reference', '').strip() or '<reference vehicle>', v["t"].strip() or "10", v["e"].strip() or "30"
        ),
    },
    "pedestrian crossing": {
        "fields": [("n", "Pedestrian number", "entry", "1", None),
                   ("side", "From", "combo", "left", ("left", "right")),
                   ("end", "To", "combo", "right", ("left", "right")),
                   ("m", "Ahead of ego at crossing start (m)", "entry", "35", None),
                   ("t", "At time (s)", "entry", "5", None),
                   ("d", "Duration (s)", "entry", "4", None)],
        "build": lambda v: 'Pedestrian {} crossing from {} to {} {} metres ahead of ego at {}s over {}s.'.format(v['n'], v['side'], v['end'], v['m'], v['t'], v['d']),
    },
    "beep alert": {
        "fields": [("t", "At time (s)", "entry", "7", None), ("d", "Duration (s)", "entry", "4", None)],
        "build": lambda v: 'beep at={}s duration={}s'.format(v['t'], v['d']),
    },
    "takeover speech": {
        "fields": [("t", "At time (s)", "entry", "7", None),
                   ("d", "Maximum duration (s)", "entry", "4", None),
                   ("text", "Speech", "entry", "Please take over.", None)],
        "build": lambda v: 'speech at={}s duration={}s text={}'.format(v['t'], v['d'], json.dumps(v['text'], ensure_ascii=False)),
    },
    "target circle": {
        "fields": [("t", "At time (s)", "entry", "7", None), ("d", "Duration (s)", "entry", "4", None),
                   ("target", "Target vehicle", "combo", "v1", ("v1", "v2", "v3", "v4", "v5", "v6"))],
        "build": lambda v: 'target circle at={}s duration={}s target={}'.format(v['t'], v['d'], v['target']),
    },
    "dropped obstacle": {
        "fields": [("n", "Obstacle number", "entry", "1", None),
                   ("actor", "Drop from vehicle", "combo", "vehicle_1", ("ego", "vehicle_1", "vehicle_2", "vehicle_3", "vehicle_4", "vehicle_5", "vehicle_6")),
                   ("kind", "Object", "combo", "barrel", ("barrel", "cone", "barrier", "debris", "box")),
                   ("t", "Drops at (s)", "entry", "8", None),
                   ("offset", "Distance behind vehicle (m)", "entry", "0.5", None),
                   ("e", "Ends at (s)", "entry", "30", None)],
        "build": lambda v: 'Obstacle {}: at {}s {} drops {}, offset={}m, end={}s'.format(v['n'], v['t'], v['actor'], v['kind'], v['offset'], v['e']),
    },
    # "batch variants" stays a verbatim insert (multi-scenario construct).
}

# The authoring menu uses only forms tested with the checked prose reader.
AUTHORING_TEMPLATE_MENU = (
    ('scenario duration', 'Scenario duration'), ('ego lane/speed', 'Ego vehicle'),
    ('front vehicle', 'Vehicle ahead'), ('rear vehicle', 'Vehicle behind'),
    ('lane change', 'Lane change'), ('speed change', 'Speed change'),
    ('obstacle barrier', 'Obstacle'), ('pedestrian crossing', 'Pedestrian crossing'),
    ('takeover alert', 'Takeover alert'), ('collision takeover alert', 'Collision takeover alert'),
    ('beep alert', 'Beep alert'), ('takeover speech', 'Takeover speech'),
    ('target circle', 'Target circle'), ('timed gap target', 'Time gap profile'),
    ('dropped obstacle', 'Dropped obstacle'),
)


class AddScenarioDialog(object):
    def __init__(self, owner, edit_item=None):
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk

        try:
            from carla_nl import constants as scenario_constants
            from carla_nl import scanner_prompt_adapter
        except Exception as exc:
            write_exception(exc)
            owner.messagebox.showerror("Add Scenario", "Prompt generator could not be loaded:\n{}".format(exc))
            return

        self.owner = owner
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.scrolledtext = scrolledtext
        self.constants = scenario_constants
        self.adapter = scanner_prompt_adapter
        self.converted = []
        self.edit_item = edit_item
        self.edit_path = Path(edit_item.path) if edit_item is not None else None
        self.edit_payload = read_prompt_json_summary(self.edit_path) if self.edit_path is not None else {}
        self.is_edit = edit_item is not None
        self.dialog_heading = "Edit scenario" if self.is_edit else "Create a scenario"
        self.save_button_text = "Save Changes" if self.is_edit else "Save Scenario"
        self.history_entries = []
        self.original_prompt_text = ""
        # True once the prompt text carries "experiment ..." lines; from then
        # on those lines are the source of truth for the Alert settings, so
        # deleting one in the editor turns the matching setting off on save.
        self._experiment_lines_in_prompt = False

        self.top = tk.Toplevel(owner.root)
        self.top.title("Edit Scenario" if self.is_edit else "Add Scenario")
        self.top.geometry("1080x720")
        self.top.minsize(900, 620)
        configure_inkspace_window(self.top)
        enable_window_maximize(self.top)

        default_group = owner.group_var.get().strip()
        if edit_item is not None:
            default_group = "" if normalize_group(edit_item.group) == DEFAULT_SCENARIO_GROUP else edit_item.group
        self.name_var = tk.StringVar(value=edit_item.name if edit_item is not None else "")
        self.group_var = tk.StringVar(value=default_group)
        self.map_var = tk.StringVar(value=str(self.edit_payload.get("map_name") or owner.selected_map()))
        self.weather_var = tk.StringVar(value=str(self.edit_payload.get("weather_preset") or "CloudyNoon"))
        self.quality_var = tk.StringVar(value=str(self.edit_payload.get("quality_level") or "Epic"))
        self.status_var = tk.StringVar(value="Ready")
        self.analysis_state_var = tk.StringVar(value="Not analyzed")
        self._analysis_snapshot = None
        self._analysis_result = ("Not analyzed", "text_muted")
        self.history_var = tk.StringVar(value="")
        self.spawn_xodr = self._initial_spawn_xodr()
        ego_cfg = {}
        try:
            vehicles = self.edit_payload.get("vehicles") if isinstance(self.edit_payload, dict) else {}
            ego_cfg = vehicles.get("ego") if isinstance(vehicles, dict) else {}
        except Exception:
            ego_cfg = {}
        self.spawn_ego_lane = tk.StringVar(value=str(ego_cfg.get("lane") or "Middle"))
        self.spawn_summary_var = tk.StringVar(value="")
        self.spawn_button_var = tk.StringVar(value="Start: Default")
        self.map_var.trace_add("write", self._on_spawn_map_changed)
        exp_events = self.edit_payload.get("experiment_events") if isinstance(self.edit_payload, dict) else {}
        exp_events = exp_events if isinstance(exp_events, dict) else {}
        first_tor = first_experiment_tor_window(self.edit_payload)
        first_hazard = {}
        hazards = exp_events.get("hazards") if isinstance(exp_events.get("hazards"), list) else []
        if hazards and isinstance(hazards[0], dict):
            first_hazard = hazards[0]
        self.exp_standard_markers_var = tk.BooleanVar(value=bool(exp_events.get("standard_markers_enabled", True)))
        self.exp_tor_enabled_var = tk.BooleanVar(value=bool(first_tor))
        self.exp_tor_onset_var = tk.StringVar(
            value=str(first_tor.get("alert_onset_s", first_tor.get("tor_onset_s", ""))) if first_tor else ""
        )
        self.exp_tor_deadline_var = tk.StringVar(value=str(first_tor.get("tor_deadline_s", "")) if first_tor else "")
        self.exp_failure_action_var = tk.StringVar(value=str(first_tor.get("failure_action", "brake")) if first_tor else "brake")
        first_tor_fields = dict(first_tor.get("fields", {}) or {}) if first_tor else {}
        stage2_direction = "front"
        if first_tor:
            stage2_direction = str(first_tor_fields.get("stage2_direction", first_tor.get("stage2_direction", "front")) or "front")
        ready_text = str(first_tor.get("ready_text", "")) if first_tor else "Please take over."
        if ready_text.strip().lower() == "hazard ahead. please take over.":
            ready_text = "Please take over."
        self.exp_stage2_direction_var = tk.StringVar(
            value=stage2_direction
        )
        self.exp_adaptive_prompt_var = tk.StringVar(value=format_tor_adaptive_prompt(first_tor_fields))
        self.exp_ready_text_var = tk.StringVar(
            value=ready_text
        )
        self.exp_hazard_onset_var = tk.StringVar(value=str(first_hazard.get("onset_s", "")) if first_hazard else "")
        self.exp_custom_markers_var = tk.StringVar(value=format_custom_marker_text(exp_events.get("custom_markers", [])))
        self._guide_step_index = 0
        self._guide_step_buttons = {}
        self._guide_step_frames = {}
        self._guide_trace_tokens = []
        self.guide_collapsed_var = tk.BooleanVar(value=True)
        self.guide_duration_var = tk.StringVar(value="35")
        self.guide_ego_lane_var = tk.StringVar(value="Left")
        self.guide_ego_speed_var = tk.StringVar(value="80")
        self.guide_speed_unit_var = tk.StringVar(value="km/h")
        self.guide_vehicle_enabled_var = tk.BooleanVar(value=False)
        self.guide_vehicle_count_var = tk.StringVar(value="1")
        self.guide_vehicle_lane_var = tk.StringVar(value="Middle")
        self.guide_vehicle_relative_var = tk.StringVar(value="ahead")
        self.guide_vehicle_gap_var = tk.StringVar(value="7")
        self.guide_vehicle_speed_var = tk.StringVar(value="72")
        self.guide_vehicle_target_lane_var = tk.StringVar(value="Left")
        self.guide_lane_change_time_var = tk.StringVar(value="7")
        self.guide_lane_change_duration_var = tk.StringVar(value="4")
        self.guide_vehicle_lane_vars = {1: self.guide_vehicle_lane_var}
        self.guide_vehicle_relative_vars = {1: self.guide_vehicle_relative_var}
        self.guide_vehicle_gap_vars = {1: self.guide_vehicle_gap_var}
        self.guide_vehicle_speed_vars = {1: self.guide_vehicle_speed_var}
        self.guide_vehicle_target_lane_vars = {1: self.guide_vehicle_target_lane_var}
        self.guide_vehicle_lc_time_vars = {1: self.guide_lane_change_time_var}
        self.guide_vehicle_lc_duration_vars = {1: self.guide_lane_change_duration_var}
        for vehicle_index in range(2, PROMPT_GUIDE_MAX_VEHICLES + 1):
            self.guide_vehicle_lane_vars[vehicle_index] = tk.StringVar(
                value=("Right", "Left", "Middle", "Left", "Right")[vehicle_index - 2]
            )
            self.guide_vehicle_relative_vars[vehicle_index] = tk.StringVar(value="ahead" if vehicle_index % 2 else "behind")
            self.guide_vehicle_gap_vars[vehicle_index] = tk.StringVar(value=str(5 + vehicle_index))
            self.guide_vehicle_speed_vars[vehicle_index] = tk.StringVar(value="80")
            self.guide_vehicle_target_lane_vars[vehicle_index] = tk.StringVar(value="Keep")
            self.guide_vehicle_lc_time_vars[vehicle_index] = tk.StringVar(value="")
            self.guide_vehicle_lc_duration_vars[vehicle_index] = tk.StringVar(value="4")
        self.guide_obstacle_enabled_var = tk.BooleanVar(value=False)
        self.guide_obstacle_kind_var = tk.StringVar(value="barrier")
        self.guide_obstacle_mode_var = tk.StringVar(value="fixed")
        self.guide_obstacle_lane_var = tk.StringVar(value="Middle")
        self.guide_obstacle_distance_var = tk.StringVar(value="100")
        self.guide_obstacle_reference_var = tk.StringVar(value="")
        self.guide_obstacle_direction_var = tk.StringVar(value="ahead of")
        self.guide_obstacle_start_var = tk.StringVar(value="10")
        self.guide_obstacle_end_var = tk.StringVar(value="30")
        self.guide_obstacle_drop_source_var = tk.StringVar(value="v1")
        self.guide_obstacle_offset_var = tk.StringVar(value="0.5")
        self.guide_ped_enabled_var = tk.BooleanVar(value=False)
        self.guide_ped_side_var = tk.StringVar(value="left")
        self.guide_ped_end_var = tk.StringVar(value="right")
        self.guide_ped_gap_var = tk.StringVar(value="35")
        self.guide_ped_time_var = tk.StringVar(value="5")
        self.guide_ped_duration_var = tk.StringVar(value="4")
        self._configure_compact_styles()
        self._build_ui()
        self._apply_compact_widget_styles(self.top)
        # Maximization is deferred by enable_window_maximize(), so place the
        # sash shortly afterwards using the final mapped width.
        self.top.after(120, self._apply_compact_pane_split)
        self._refresh_spawn_summary()
        if self.is_edit:
            self._load_existing_scenario()
        # New scenarios start blank: no auto name, no example prompt.
        # The Example button still loads the sample on demand.
        self._schedule_design_refresh()
        self._highlight_prompt_syntax()
        self._analysis_input_vars = [self.name_var, self.group_var, self.map_var,
            self.weather_var, self.quality_var, self.spawn_ego_lane,
            self.exp_standard_markers_var, self.exp_tor_enabled_var,
            self.exp_tor_onset_var, self.exp_tor_deadline_var, self.exp_failure_action_var,
            self.exp_stage2_direction_var, self.exp_adaptive_prompt_var, self.exp_ready_text_var,
            self.exp_hazard_onset_var, self.exp_custom_markers_var]
        for variable in self._analysis_input_vars:
            variable.trace_add("write", self._refresh_analysis_state)
        self._refresh_analysis_state()
        self.top.grab_set()
        self.name_entry.focus_set()

    def _configure_compact_styles(self):
        """Define styles used only by widgets inside this Add Scenario dialog."""
        style = self.ttk.Style(self.top)
        c = INKSPACE_COLORS
        body_font = ("Segoe UI", 10)

        style.configure("AddScenario.TLabel", background=c["canvas"], foreground=c["text"], font=body_font)
        style.configure(
            "AddScenario.Title.TLabel",
            background=c["canvas"], foreground=c["text"], font=("Segoe UI", 18, "bold"),
        )
        style.configure(
            "AddScenario.Section.TLabel",
            background=c["canvas"], foreground=c["text"], font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "AddScenario.Muted.TLabel",
            background=c["canvas"], foreground=c["text_muted"], font=body_font,
        )
        style.configure("AddScenario.TButton", font=body_font, padding=(12, 7))
        style.configure("AddScenario.TMenubutton", font=body_font, padding=(12, 7))
        style.configure(
            "AddScenario.Accent.TButton",
            background=c["brand"], foreground=c["on_accent"], bordercolor=c["brand"],
            font=("Segoe UI", 10, "bold"), padding=(16, 7),
        )
        style.map(
            "AddScenario.Accent.TButton",
            background=[
                ("pressed", c["brand_pressed"]),
                ("active", c["brand_hover"]),
                ("disabled", c["surface_subtle"]),
            ],
            foreground=[("disabled", c["text_subtle"])],
        )
        style.configure("AddScenario.TEntry", font=body_font, padding=(5, 3))
        style.configure("AddScenario.TCombobox", font=body_font, padding=(5, 3))
        style.configure("AddScenario.TCheckbutton", font=body_font, padding=(2, 2))
        style.configure("AddScenario.TLabelframe", background=c["canvas"], borderwidth=1)
        style.configure(
            "AddScenario.TLabelframe.Label",
            background=c["surface"], foreground=c["text_muted"], font=("Segoe UI", 8, "bold"),
        )
        style.configure("AddScenario.TNotebook", background=c["canvas"], borderwidth=0)
        style.configure(
            "AddScenario.TNotebook.Tab",
            background=c["surface_alt"], foreground=c["text_muted"], font=body_font, padding=(9, 5),
        )
        style.map(
            "AddScenario.TNotebook.Tab",
            background=[("selected", c["surface"]), ("active", c["surface_hover"])],
            foreground=[("selected", c["text"]), ("active", c["brand_hover"])],
        )
        style.configure(
            "AddScenario.Treeview",
            background=c["field"], fieldbackground=c["field"], foreground=c["text"],
            font=body_font, rowheight=21,
        )
        style.configure(
            "AddScenario.Treeview.Heading",
            background=c["surface_alt"], foreground=c["text_muted"], font=("Segoe UI", 8, "bold"),
        )

    def _apply_compact_widget_styles(self, parent):
        """Assign compact ttk styles without modifying the launcher's defaults."""
        pending = list(parent.winfo_children())
        while pending:
            widget = pending.pop()
            try:
                pending.extend(widget.winfo_children())
                current_style = widget.cget("style") if "style" in widget.keys() else ""
                compact_style = add_scenario_compact_style(widget.winfo_class(), current_style)
                if compact_style:
                    widget.configure(style=compact_style)
                    # The app's *Font option is a widget override of ttk styles.
                    # Set label fonts explicitly so headings retain hierarchy.
                    if widget.winfo_class() == "TLabel":
                        widget.configure(font=self.ttk.Style(self.top).lookup(compact_style, "font"))
            except Exception:
                continue

    def _apply_compact_pane_split(self):
        """Give Design 48% of the mapped width once, preserving later sash drags."""
        panes = getattr(self, "content_panes", None)
        if panes is None:
            return
        try:
            width = panes.winfo_width()
            if width > 1:
                panes.sashpos(0, add_scenario_sash_position(width))
        except Exception as exc:
            write_exception(exc)

    def _initial_spawn_xodr(self):
        self._spawn_is_default = not self.is_edit
        payload = self.edit_payload
        if self._spawn_is_default:
            payload = read_prompt_json_summary(OUTPUT_SCENARIOS / "low_speed_tor_new.json")
            if payload.get("map_name") != self.map_var.get():
                return None
        if isinstance(payload, dict):
            spawn = payload.get("spawn_xodr")
            if isinstance(spawn, dict):
                try:
                    return {
                        "road_id": int(spawn.get("road_id")),
                        "lane_id": int(spawn.get("lane_id")),
                        "s": float(spawn.get("s")),
                    }
                except Exception:
                    return None
        return None

    def _on_spawn_map_changed(self, *_args):
        if getattr(self, "_spawn_is_default", False):
            self.spawn_xodr = self._initial_spawn_xodr()
            self._refresh_spawn_summary()

    def _build_ui(self):
        ttk = self.ttk
        tk = self.tk

        root = ttk.Frame(self.top, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=self.dialog_heading, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Describe it. Refine the details. Make it ready to run.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Button(header, text="Guided setup", command=self._open_prompt_wizard).grid(row=0, column=1, sticky="e", padx=(0, 8))
        ttk.Button(header, text="Command reference", command=self._open_prompt_cheatsheet).grid(row=0, column=2, sticky="e")

        config = self.scenario_bar = ttk.Frame(root)
        config.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for col in (1, 3, 5, 7):
            config.columnconfigure(col, weight=2 if col == 1 else 1)
        ttk.Label(config, text="Name").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.name_entry = ttk.Entry(config, textvariable=self.name_var, width=14)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(config, text="Group").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Combobox(
            config,
            textvariable=self.group_var,
            values=available_scenario_groups(self.owner.scenarios),
            width=6,
        ).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Label(config, text="Map").grid(row=0, column=4, sticky="w", padx=(0, 4))
        ttk.Combobox(
            config,
            textvariable=self.map_var,
            values=self.constants.KNOWN_MAPS,
            state="readonly",
            width=11,
        ).grid(row=0, column=5, sticky="ew", padx=(0, 8))
        ttk.Label(config, text="Weather").grid(row=0, column=6, sticky="w", padx=(0, 4))
        ttk.Combobox(
            config,
            textvariable=self.weather_var,
            values=self.constants.WEATHER_PRESETS,
            state="readonly",
            width=11,
        ).grid(row=0, column=7, sticky="ew", padx=(0, 8))

        ttk.Label(config, text="Quality").grid(row=0, column=8, sticky="w", padx=(0, 4))
        ttk.Combobox(
            config,
            textvariable=self.quality_var,
            values=("Low", "Medium", "High", "Epic"),
            state="readonly",
            width=6,
        ).grid(row=0, column=9, sticky="w", padx=(0, 8))
        self.spawn_button = ttk.Menubutton(config, textvariable=self.spawn_button_var, width=19)
        self.spawn_button.grid(row=0, column=10, sticky="e")
        self.spawn_menu = tk.Menu(self.spawn_button, tearoff=False)
        self.spawn_menu.add_command(label="Current start", state="disabled")
        self.spawn_menu.add_separator()
        self.spawn_menu.add_command(label="Pick on 2D Map...", command=self.pick_spawn_on_map)
        self.spawn_menu.add_command(label="Clear Start", command=self.clear_spawn_xodr)
        self.spawn_button.configure(menu=self.spawn_menu)

        panes = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        panes.grid(row=2, column=0, sticky="nsew")
        self.content_panes = panes

        left = ttk.Frame(panes, padding=(0, 0, 12, 0))
        left.rowconfigure(4, weight=1)
        left.columnconfigure(0, weight=1)
        panes.add(left, weight=1)

        prompt_top = ttk.Frame(left)
        prompt_top.grid(row=0, column=0, sticky="ew", pady=(4, 10))
        prompt_top.columnconfigure(0, weight=1)
        ttk.Label(prompt_top, text="01  Describe your scenario", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(prompt_top, text="Include lanes, speeds, relative positions and event times.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._build_start_wizard(left, row=1)
        self.guide_shell.grid_remove()

        toolbar = ttk.Frame(left)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(2, weight=1)
        insert_button = ttk.Menubutton(toolbar, text="＋ Add to prompt")
        insert_menu = tk.Menu(insert_button, tearoff=False)
        advanced_menu = tk.Menu(insert_menu, tearoff=False)
        for index, (key, label) in enumerate(AUTHORING_TEMPLATE_MENU):
            menu = insert_menu if index < 9 else advanced_menu
            menu.add_command(label=label + '...', command=lambda value=key: self._open_authoring_template(value))
        insert_menu.add_separator()
        insert_menu.add_cascade(label="More templates", menu=advanced_menu)
        insert_button.configure(menu=insert_menu)
        insert_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(toolbar, text="Load example", command=self._load_example).grid(row=0, column=1, sticky="w")
        self.prompt_undo_button = ttk.Button(toolbar, text="↶ Undo", command=self._undo_prompt_edit, state="disabled")
        self.prompt_undo_button.grid(row=0, column=2, sticky="w", padx=(8, 8))
        tools_button = ttk.Menubutton(toolbar, text="Tools ▾")
        tools_menu = tk.Menu(tools_button, tearoff=False)
        tools_menu.add_command(label="Guided setup", command=self._open_prompt_wizard)
        tools_menu.add_command(label="Use defaults", command=self._apply_safe_defaults_to_prompt)
        tools_menu.add_command(label="Refresh scenario timeline", command=self._format_prompt_by_timeline)
        tools_menu.add_separator()
        tools_menu.add_command(label="Command reference", command=self._open_prompt_cheatsheet)
        tools_menu.add_command(label="Autocomplete", command=self.show_completion_menu)
        tools_button.configure(menu=tools_menu)
        tools_button.grid(row=0, column=3, sticky="e")

        self.prompt_legend = tk.Frame(left, bg=INKSPACE_COLORS["canvas"])
        self.prompt_legend.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        for column, (category, label) in enumerate(PROMPT_SYNTAX_LEGEND):
            tk.Label(self.prompt_legend, text="● " + label, fg=PROMPT_SYNTAX_COLORS[category],
                     bg=INKSPACE_COLORS["canvas"], font=("Segoe UI", 9)).grid(
                         row=0, column=column, sticky="w", padx=(0, 10))
        tk.Label(self.prompt_legend, text="<Fill in>", fg=PROMPT_FIELD_COLORS["foreground"],
                 bg=PROMPT_FIELD_COLORS["background"], font=("Segoe UI", 9), padx=5).grid(
                     row=0, column=len(PROMPT_SYNTAX_LEGEND), sticky="w")

        self.prompt_text = self.scrolledtext.ScrolledText(left, wrap="word", height=12, font=PROMPT_FONT, undo=True)
        style_inkspace_text(self.prompt_text)
        self.prompt_text.configure(font=("Consolas", 13), spacing1=4, spacing2=3, spacing3=6,
                                   padx=16, pady=16, background=INKSPACE_COLORS["surface"])
        self._configure_prompt_syntax_tags()
        self.prompt_text.grid(row=4, column=0, sticky="nsew")
        self.prompt_text.bind("<<Modified>>", self._on_prompt_modified, add="+")
        self.top.bind("<Destroy>", self._on_prompt_widget_destroy, add="+")
        self.prompt_text.bind("<KeyRelease>", self._schedule_design_refresh, add="+")
        self.prompt_text.bind("<Button-1>", self._on_prompt_field_click, add="+")
        self.prompt_text.bind("<Tab>", self._on_prompt_field_key)
        self.prompt_text.bind("<Shift-Tab>", lambda event: self._on_prompt_field_key(event, reverse=True))
        self.prompt_text.bind("<ISO_Left_Tab>", lambda event: self._on_prompt_field_key(event, reverse=True))
        self.prompt_text.bind("<Return>", self._on_prompt_field_key)
        self.prompt_empty_hint = tk.Label(
            self.prompt_text, text="Start with the scene you have in mind.\n\nFor example:\nEgo is in the middle lane at 60 mph.\nVehicle 1 is on the right at 60 mph, 1.5 seconds ahead.\nAt 10 seconds, it merges into ego's lane over 3 seconds.",
            bg=INKSPACE_COLORS["surface"], fg=INKSPACE_COLORS["text_subtle"],
            font=("Segoe UI", 11), justify="left", anchor="nw", wraplength=390,
        )
        self.prompt_empty_hint.place(x=18, y=18, relwidth=0.90)
        self.prompt_empty_hint.bind("<Button-1>", lambda _event: (self.prompt_empty_hint.place_forget(), self.prompt_text.focus_set()))
        self.prompt_text.bind("<Configure>", lambda event: self.prompt_empty_hint.configure(wraplength=max(200, event.width - 44)), add="+")

        field_bar = ttk.Frame(left)
        field_bar.grid(row=5, column=0, sticky="ew", pady=(6, 8))
        field_bar.columnconfigure(0, weight=1)
        self.prompt_field_hint = ttk.Label(field_bar, text="Tab / Enter: next field · Shift+Tab: previous", style="Muted.TLabel")
        self.prompt_field_hint.grid(row=0, column=0, sticky="w")
        self.next_field_button = ttk.Button(field_bar, text="Next field (Tab)", command=self._next_prompt_field)
        self.next_field_button.grid(row=0, column=1, sticky="e")
        self.next_field_button.configure(state="disabled")

        self.timeline_toggle = ttk.Button(left, text="▸ Scenario timeline", command=self._toggle_prompt_timeline)
        self.timeline_toggle.grid(row=6, column=0, sticky="w", pady=(0, 8))
        self.timeline_text = self.scrolledtext.ScrolledText(left, wrap="word", height=5, font=PROMPT_FONT, state="disabled")
        style_inkspace_text(self.timeline_text)
        self.timeline_text.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        self.timeline_text.grid_remove()
        buttons = ttk.Frame(left)
        buttons.grid(row=8, column=0, sticky="ew", pady=(4, 0))
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Analyze  →", command=self.preview, style="Accent.TButton").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(buttons, text=self.save_button_text, command=self.save).grid(row=0, column=1, sticky="w")
        ttk.Button(buttons, text="Clear", command=self._clear_prompt).grid(row=0, column=3, sticky="e", padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).grid(row=0, column=4, sticky="e")
        status_label = ttk.Label(left, textvariable=self.status_var, style="Muted.TLabel", wraplength=450)
        status_label.grid(row=9, column=0, sticky="ew", pady=(8, 0))
        left.bind("<Configure>", lambda event: status_label.configure(wraplength=max(240, event.width - 28)))

        right = ttk.Frame(panes, padding=(5, 0, 0, 0))
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)
        panes.add(right, weight=1)
        review_header = ttk.Frame(right)
        review_header.grid(row=0, column=0, sticky="ew", pady=(4, 12))
        review_header.columnconfigure(0, weight=1)
        ttk.Label(review_header, text="02  Review & refine", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.analysis_state_label = tk.Label(review_header, textvariable=self.analysis_state_var,
            bg=INKSPACE_COLORS["canvas"], fg=INKSPACE_COLORS["text_muted"], font=("Segoe UI", 10, "bold"))
        self.analysis_state_label.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.prompt_guidance_banner = self.tk.Frame(
            right, bg=INKSPACE_COLORS["surface_alt"], padx=14, pady=12,
            highlightthickness=1, highlightbackground=INKSPACE_COLORS["border"],
        )
        self.prompt_guidance_banner.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.prompt_guidance_title = self.tk.Label(
            self.prompt_guidance_banner, text="", anchor="w", justify="left",
            bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text"],
            font=("Segoe UI", 12, "bold"),
        )
        self.prompt_guidance_banner.columnconfigure(0, weight=1)
        self.prompt_guidance_title.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.prompt_guidance_subtitle = tk.Label(
            self.prompt_guidance_banner, text="", anchor="w", justify="left",
            bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"], font=("Segoe UI", 10),
        )
        self.prompt_guidance_subtitle.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.prompt_repair_all_button = self.tk.Button(
            self.prompt_guidance_banner, text="Apply all templates",
            command=self._apply_all_prompt_repairs, bg=INKSPACE_COLORS["brand"],
            fg=INKSPACE_COLORS["canvas"], relief="flat", font=("Segoe UI", 10, "bold"),
            padx=10, pady=5, cursor="hand2",
        )
        self.prompt_guidance_banner.bind("<Configure>", lambda event: (
            self.prompt_guidance_title.configure(wraplength=max(200, event.width - 32)),
            self.prompt_guidance_subtitle.configure(wraplength=max(200, event.width - 32))))
        self.prompt_guidance_banner.grid_remove()
        self.tabs = ttk.Notebook(right)
        self.tabs.grid(row=2, column=0, sticky="nsew")
        self.preview_text = self._text_tab("Review")
        self.review_empty_hint = tk.Label(
            self.preview_text, text="Your scenario review will appear here.\n\n1  Describe the scene on the left.\n2  Click Analyze to check the details.\n3  Refine the suggestions, then save.",
            bg=INKSPACE_COLORS["field"], fg=INKSPACE_COLORS["text_muted"],
            font=("Segoe UI", 11), justify="left", anchor="nw", wraplength=350,
        )
        self.review_empty_hint.place(x=20, y=24, relwidth=0.88)
        self.preview_text.bind("<Configure>", lambda event: self.review_empty_hint.configure(wraplength=max(200, event.width - 44)), add="+")
        self.preview_copy_menu = self.tk.Menu(self.top, tearoff=False)
        self.preview_text.bind("<Button-3>", self._show_preview_copy_menu)
        self.json_text = self._text_tab("JSON")
        self.design_frame = self.ttk.Frame(self.tabs)
        self.tabs.add(self.design_frame, text="Design")
        try:
            self.design_preview = EmbeddedScenarioDesignPreview(self, self.design_frame)
        except Exception as exc:
            write_exception(exc)
            self.design_preview = None
        self.history_frame, self.history_listbox, self.history_text = self._history_tab()

    def _build_start_wizard(self, parent, row):
        tk = self.tk
        ttk = self.ttk
        c = INKSPACE_COLORS
        self.guide_shell = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"])
        self.guide_shell.grid(row=row, column=0, sticky="ew", pady=(6, 0))
        self.guide_shell.columnconfigure(0, weight=1)

        header = tk.Frame(self.guide_shell, bg=c["surface"], padx=8, pady=5)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Start Wizard", bg=c["surface"], fg=c["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Replace", command=self._guide_replace_prompt).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(header, text="Append", command=self._guide_append_prompt).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(header, text="Insert + Analyze", command=self._guide_build_and_analyze, style="Accent.TButton").grid(row=0, column=4, padx=(0, 6))
        self.guide_collapse_button = ttk.Button(header, text="Collapse", command=self._toggle_guide_collapsed)
        self.guide_collapse_button.grid(row=0, column=5)

        self.guide_body = tk.Frame(self.guide_shell, bg=c["surface_alt"], padx=8, pady=7)
        self.guide_body.grid(row=1, column=0, sticky="ew")
        self.guide_body.columnconfigure(0, weight=5, uniform="guide")
        self.guide_body.columnconfigure(1, weight=4, uniform="guide")

        form = tk.Frame(self.guide_body, bg=c["surface_alt"])
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        form.columnconfigure(0, weight=1)

        preview_shell = tk.Frame(self.guide_body, bg=c["border"])
        preview_shell.grid(row=0, column=1, sticky="nsew")
        preview_shell.columnconfigure(0, weight=1)
        preview_shell.rowconfigure(1, weight=1)
        tk.Label(
            preview_shell,
            text="wizard.preview",
            bg=c["field"],
            fg=c["brand_hover"],
            padx=8,
            pady=4,
            font=("Consolas", 9, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
        self.guide_preview_text = self.scrolledtext.ScrolledText(
            preview_shell,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        style_inkspace_text(self.guide_preview_text)
        self.guide_preview_text.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))

        self.guide_stepper_frame = tk.Frame(form, bg=c["surface_alt"])
        self.guide_stepper_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for step_name in PROMPT_GUIDE_STEPS:
            button = tk.Button(
                self.guide_stepper_frame,
                text=step_name,
                command=lambda name=step_name: self._guide_set_step(name),
                bg=c["surface_alt"],
                fg=c["text"],
                activebackground=c["surface_hover"],
                activeforeground=c["brand_hover"],
                relief="flat",
                padx=5,
                pady=4,
                font=("Segoe UI", 8, "bold"),
                highlightthickness=1,
                highlightbackground=c["border"],
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 2))
            self._guide_step_buttons[step_name] = button

        self.guide_step_content = tk.Frame(form, bg=c["surface_alt"])
        self.guide_step_content.grid(row=1, column=0, sticky="nsew")
        self.guide_step_content.columnconfigure(0, weight=1)

        scene = self._guide_create_step_frame("Scene")
        self._guide_labeled_widget(scene, 0, 0, "Duration s (blank=auto)", self._guide_entry(scene, self.guide_duration_var, width=7))
        self._guide_labeled_widget(scene, 1, 0, "Ego lane", self._guide_combo(scene, self.guide_ego_lane_var, ("Left", "Middle", "Right"), width=10))
        speed_box = tk.Frame(scene, bg=c["surface_alt"])
        ttk.Entry(speed_box, textvariable=self.guide_ego_speed_var, width=7).pack(side="left")
        ttk.Combobox(speed_box, textvariable=self.guide_speed_unit_var, values=("km/h", "mph"), state="readonly", width=7).pack(side="left", padx=(4, 0))
        self._guide_labeled_widget(scene, 1, 2, "Ego speed", speed_box)

        vehicles = self._guide_create_step_frame("Vehicles")
        vehicle_section = tk.Frame(vehicles, bg=c["surface_alt"], highlightthickness=1, highlightbackground=c["border"])
        vehicle_section.grid(row=0, column=0, columnspan=4, sticky="ew")
        vehicle_section.columnconfigure(0, weight=1)
        vehicle_header = tk.Frame(vehicle_section, bg=c["surface_alt"], padx=8, pady=6)
        vehicle_header.grid(row=0, column=0, sticky="ew")
        vehicle_header.columnconfigure(1, weight=1)
        tk.Checkbutton(
            vehicle_header,
            text="Vehicles",
            variable=self.guide_vehicle_enabled_var,
            command=self._on_guide_vehicle_enabled_changed,
            bg=c["surface_alt"],
            fg=c["text"],
            activebackground=c["surface_alt"],
            activeforeground=c["brand_hover"],
            selectcolor=c["field"],
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.guide_vehicle_count_label = tk.Label(vehicle_header, text="", bg=c["surface_alt"], fg=c["text_muted"], font=("Segoe UI", 9, "bold"))
        self.guide_vehicle_count_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(vehicle_header, text="Add Vehicle", command=self._guide_add_vehicle).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(vehicle_header, text="Remove Vehicle", command=self._guide_remove_vehicle).grid(row=0, column=3, padx=(6, 0))
        self.guide_vehicle_rows_frame = tk.Frame(vehicle_section, bg=c["surface_alt"], padx=8)
        self.guide_vehicle_rows_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.guide_vehicle_rows_frame.columnconfigure(0, weight=1)
        self._refresh_guide_vehicle_rows()

        obstacles = self._guide_create_step_frame("Obstacles")
        self._guide_check(obstacles, 0, 0, "Obstacle", self.guide_obstacle_enabled_var)
        self._guide_labeled_widget(obstacles, 0, 2, "Kind", self._guide_combo(obstacles, self.guide_obstacle_kind_var, ("barrier", "cone", "debris", "barrel", "box"), width=18))
        obstacle_box = tk.Frame(obstacles, bg=c["surface_alt"])
        self.guide_obstacle_lane_combo = ttk.Combobox(obstacle_box, textvariable=self.guide_obstacle_lane_var, values=("Left", "Middle", "Right"), state="readonly", width=9)
        self.guide_obstacle_lane_combo.pack(side="left")
        self.guide_obstacle_distance_entry = ttk.Entry(obstacle_box, textvariable=self.guide_obstacle_distance_var, width=6)
        self.guide_obstacle_distance_entry.pack(side="left", padx=(4, 0))
        self.guide_obstacle_distance_unit = tk.Label(obstacle_box, text="m", bg=c["surface_alt"], fg=c["text_muted"])
        self.guide_obstacle_distance_unit.pack(side="left", padx=(2, 0))
        ttk.Entry(obstacle_box, textvariable=self.guide_obstacle_start_var, width=5).pack(side="left", padx=(8, 0))
        tk.Label(obstacle_box, text="s-", bg=c["surface_alt"], fg=c["text_muted"]).pack(side="left", padx=(2, 0))
        ttk.Entry(obstacle_box, textvariable=self.guide_obstacle_end_var, width=5).pack(side="left", padx=(2, 0))
        tk.Label(obstacle_box, text="s", bg=c["surface_alt"], fg=c["text_muted"]).pack(side="left", padx=(2, 0))
        self._guide_labeled_widget(obstacles, 1, 0, "Placement", obstacle_box, columnspan=3)
        self._guide_labeled_widget(obstacles, 2, 0, "Mode", self._guide_combo(obstacles, self.guide_obstacle_mode_var, ("fixed", "drop from vehicle"), width=18))
        drop_box = tk.Frame(obstacles, bg=c["surface_alt"])
        self.guide_obstacle_drop_source_combo = ttk.Combobox(drop_box, textvariable=self.guide_obstacle_drop_source_var, values=("v1", "v2", "v3", "v4"), state="readonly", width=6)
        self.guide_obstacle_drop_source_combo.pack(side="left")
        tk.Label(drop_box, text="offset", bg=c["surface_alt"], fg=c["text_muted"]).pack(side="left", padx=(8, 2))
        self.guide_obstacle_offset_entry = ttk.Entry(drop_box, textvariable=self.guide_obstacle_offset_var, width=5)
        self.guide_obstacle_offset_entry.pack(side="left")
        tk.Label(drop_box, text="m", bg=c["surface_alt"], fg=c["text_muted"]).pack(side="left", padx=(2, 0))
        self._guide_labeled_widget(obstacles, 2, 2, "Drop from", drop_box)
        reference_box = tk.Frame(obstacles, bg=c["surface_alt"])
        self.guide_obstacle_direction_combo = ttk.Combobox(reference_box, textvariable=self.guide_obstacle_direction_var,
                                                          values=("ahead of", "behind"), state="readonly", width=9)
        self.guide_obstacle_direction_combo.pack(side="left")
        self.guide_obstacle_reference_combo = ttk.Combobox(reference_box, textvariable=self.guide_obstacle_reference_var,
                                                          values=("ego", "v1", "v2", "v3", "v4", "v5", "v6"), state="readonly", width=9)
        self.guide_obstacle_reference_combo.pack(side="left", padx=(5, 0))
        self._guide_labeled_widget(obstacles, 3, 0, "Reference vehicle (required)", reference_box, columnspan=3)
        self._guide_update_obstacle_fields()
        tk.Label(
            obstacles,
            text="Choose ego or a defined vehicle. Distance is measured when the obstacle appears; it then stays fixed on the road. Drop mode uses the dropping vehicle instead.",
            bg=c["surface_alt"],
            fg=c["text_muted"],
            anchor="w",
            wraplength=520,
            justify="left",
            font=("Segoe UI", 8),
        ).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        pedestrians = self._guide_create_step_frame("Pedestrians")
        self._guide_check(pedestrians, 0, 0, "Enable pedestrian", self.guide_ped_enabled_var)
        for row, column, label, variable, choices in (
            (1, 0, "From", self.guide_ped_side_var, ("left", "right")),
            (1, 2, "To", self.guide_ped_end_var, ("left", "right")),
            (2, 0, "Ahead of ego at crossing start (m)", self.guide_ped_gap_var, None),
            (2, 2, "At time (s)", self.guide_ped_time_var, None),
            (3, 0, "Duration (s)", self.guide_ped_duration_var, None),
        ):
            widget = self._guide_combo(pedestrians, variable, choices, width=8) if choices else self._guide_entry(pedestrians, variable, width=8)
            self._guide_labeled_widget(pedestrians, row, column, label, widget)
        alert = self._guide_create_step_frame("Alert")
        self._guide_check(alert, 0, 0, "Standard markers", self.exp_standard_markers_var)
        self._guide_check(alert, 0, 2, "Full adaptive TOR", self.exp_tor_enabled_var)
        self._guide_labeled_widget(alert, 1, 0, "Alert start", self._guide_entry(alert, self.exp_tor_onset_var, width=8))
        self._guide_labeled_widget(alert, 1, 2, "Latest TOR", self._guide_entry(alert, self.exp_tor_deadline_var, width=8))
        self._guide_labeled_widget(alert, 2, 0, "Fallback", self._guide_combo(alert, self.exp_failure_action_var, TOR_ACTION_OPTIONS, width=16))
        self._guide_labeled_widget(alert, 2, 2, "Hazard onset", self._guide_entry(alert, self.exp_hazard_onset_var, width=8))
        self._guide_labeled_widget(alert, 3, 0, "Custom markers", self._guide_entry(alert, self.exp_custom_markers_var, width=34), columnspan=3)
        self._guide_labeled_widget(alert, 4, 0, "Stage 2 look", self._guide_combo(alert, self.exp_stage2_direction_var, TOR_STAGE2_DIRECTION_OPTIONS, width=14))
        self._guide_labeled_widget(alert, 4, 2, "Stage 3 speech", self._guide_entry(alert, self.exp_ready_text_var, width=28))
        ttk.Button(alert, text="Adaptive Prompt...", command=self.open_adaptive_prompt_editor).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        cue_buttons = ttk.Frame(alert)
        cue_buttons.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        ttk.Label(cue_buttons, text="Standalone:").pack(side="left")
        for label in ('takeover alert', 'beep alert', 'takeover speech', 'target circle'):
            ttk.Button(cue_buttons, text=label, command=lambda name=label: self._insert_completion_snippet(name)).pack(side="left", padx=2)

        nav = tk.Frame(form, bg=c["surface_alt"])
        nav.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        nav.columnconfigure(1, weight=1)
        self.guide_back_button = ttk.Button(nav, text="Back", command=self._guide_previous_step)
        self.guide_back_button.grid(row=0, column=0, sticky="w")
        self.guide_step_hint_label = tk.Label(nav, text="", bg=c["surface_alt"], fg=c["text_muted"], font=("Segoe UI", 9, "bold"))
        self.guide_step_hint_label.grid(row=0, column=1, sticky="ew", padx=8)
        self.guide_next_button = ttk.Button(nav, text="Next Step", command=self._guide_next_step)
        self.guide_next_button.grid(row=0, column=2, sticky="e")

        self._watch_guide_variables()
        self._guide_set_step(PROMPT_GUIDE_STEPS[self._guide_step_index], update_status=False)
        self._refresh_guide_preview()
        if self.guide_collapsed_var.get():
            self.guide_body.grid_remove()
            self.guide_collapse_button.configure(text="Open")

    def _guide_create_step_frame(self, step_name):
        frame = self.tk.Frame(self.guide_step_content, bg=INKSPACE_COLORS["surface_alt"])
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.grid_remove()
        self._guide_step_frames[step_name] = frame
        return frame

    def _guide_labeled_widget(self, parent, row, column, label, widget, columnspan=1):
        self.tk.Label(
            parent,
            text=label,
            bg=INKSPACE_COLORS["surface_alt"],
            fg=INKSPACE_COLORS["text_muted"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=3)
        widget.grid(row=row, column=column + 1, columnspan=columnspan, sticky="ew", pady=3)

    def _guide_combo(self, parent, variable, values, width=12):
        combo = self.ttk.Combobox(parent, textvariable=variable, values=values, width=width, state="readonly")
        combo.bind("<<ComboboxSelected>>", lambda _event: self._on_guide_variable_changed())
        return combo

    def _guide_entry(self, parent, variable, width=8):
        entry = self.ttk.Entry(parent, textvariable=variable, width=width)
        entry.bind("<KeyRelease>", lambda _event: self._on_guide_variable_changed())
        return entry

    def _guide_check(self, parent, row, column, text, variable):
        self.tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=self._on_guide_variable_changed,
            bg=INKSPACE_COLORS["surface_alt"],
            fg=INKSPACE_COLORS["text"],
            activebackground=INKSPACE_COLORS["surface_alt"],
            activeforeground=INKSPACE_COLORS["brand_hover"],
            selectcolor=INKSPACE_COLORS["field"],
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=row, column=column, columnspan=2, sticky="w", pady=3)

    def _guide_vehicle_count(self):
        try:
            count = int(str(self.guide_vehicle_count_var.get()).strip())
        except Exception:
            count = 1 if self.guide_vehicle_enabled_var.get() else 0
        return max(0, min(PROMPT_GUIDE_MAX_VEHICLES, count))

    def _guide_drop_source_index(self):
        text = str(self.guide_obstacle_drop_source_var.get() or "v1").strip().lower()
        digits = "".join(ch for ch in text if ch.isdigit())
        try:
            idx = int(digits) if digits else 1
        except Exception:
            idx = 1
        return max(1, min(PROMPT_GUIDE_MAX_VEHICLES, idx))

    def _guide_ensure_drop_vehicle(self):
        """Protection: a dropped obstacle needs its source vehicle defined.

        When the obstacle is enabled in drop-from-vehicle mode, auto-enable the
        referenced vehicle (and grow the vehicle count if needed) so Analyze
        won't fail with a 'vehicle not defined' error. Only ever adds vehicles,
        never removes them; guarded against trace re-entrancy.
        """
        if getattr(self, "_guide_syncing_drop", False):
            return
        if not self.guide_obstacle_enabled_var.get():
            return
        if not str(self.guide_obstacle_mode_var.get() or "").strip().lower().startswith("drop"):
            return
        needed = self._guide_drop_source_index()
        expected = "v{}".format(needed)
        self._guide_syncing_drop = True
        try:
            if str(self.guide_obstacle_drop_source_var.get()).strip().lower() != expected:
                self.guide_obstacle_drop_source_var.set(expected)
            changed = False
            if not self.guide_vehicle_enabled_var.get():
                self.guide_vehicle_enabled_var.set(True)
                changed = True
            if self._guide_vehicle_count() < needed:
                self.guide_vehicle_count_var.set(str(needed))
                changed = True
            if changed:
                self._refresh_guide_vehicle_rows()
                self.status_var.set("Auto-enabled {} so it can drop the obstacle.".format(expected))
        finally:
            self._guide_syncing_drop = False

    def _guide_update_obstacle_fields(self):
        """Enable only the placement fields that apply to the current mode.

        ``fixed`` uses lane + distance; ``drop from vehicle`` uses the source
        vehicle + offset (the obstacle inherits the vehicle's lane/position), so
        the inapplicable inputs are greyed out to avoid confusion.
        """
        drop = str(self.guide_obstacle_mode_var.get() or "").strip().lower().startswith("drop")
        for widget, enabled, readonly in (
            (getattr(self, "guide_obstacle_lane_combo", None), not drop, True),
            (getattr(self, "guide_obstacle_distance_entry", None), not drop, False),
            (getattr(self, "guide_obstacle_reference_combo", None), not drop, True),
            (getattr(self, "guide_obstacle_direction_combo", None), not drop, True),
            (getattr(self, "guide_obstacle_drop_source_combo", None), drop, True),
            (getattr(self, "guide_obstacle_offset_entry", None), drop, False),
        ):
            if widget is None:
                continue
            if enabled:
                state = "readonly" if readonly else "normal"
            else:
                state = "disabled"
            try:
                widget.configure(state=state)
            except Exception:
                pass
        unit = getattr(self, "guide_obstacle_distance_unit", None)
        if unit is not None:
            try:
                unit.configure(fg=INKSPACE_COLORS["text_subtle" if drop else "text_muted"])
            except Exception:
                pass

    def _on_guide_vehicle_enabled_changed(self):
        if self.guide_vehicle_enabled_var.get() and self._guide_vehicle_count() == 0:
            self.guide_vehicle_count_var.set("1")
        self._refresh_guide_vehicle_rows()
        self._refresh_guide_preview()

    def _guide_add_vehicle(self):
        count = self._guide_vehicle_count()
        if count >= PROMPT_GUIDE_MAX_VEHICLES:
            self.status_var.set("Start Wizard supports up to {} vehicles.".format(PROMPT_GUIDE_MAX_VEHICLES))
            return
        self.guide_vehicle_enabled_var.set(True)
        self.guide_vehicle_count_var.set(str(count + 1))
        self._refresh_guide_vehicle_rows()
        self._refresh_guide_preview()
        self.status_var.set("Added vehicle {}.".format(count + 1))

    def _guide_remove_vehicle(self):
        count = self._guide_vehicle_count()
        if count <= 0:
            self.status_var.set("No wizard vehicle to remove.")
            return
        next_count = count - 1
        self.guide_vehicle_count_var.set(str(next_count))
        if next_count == 0:
            self.guide_vehicle_enabled_var.set(False)
        self._refresh_guide_vehicle_rows()
        self._refresh_guide_preview()
        self.status_var.set("Removed vehicle {}.".format(count))

    def _refresh_guide_vehicle_rows(self):
        frame = getattr(self, "guide_vehicle_rows_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        active_count = self._guide_vehicle_count() if self.guide_vehicle_enabled_var.get() else 0
        if getattr(self, "guide_vehicle_count_label", None) is not None:
            self.guide_vehicle_count_label.configure(text="{} active / max {}".format(active_count, PROMPT_GUIDE_MAX_VEHICLES))
        if active_count <= 0:
            self.tk.Label(
                frame,
                text="No support vehicles. Use Add Vehicle to create v1.",
                bg=INKSPACE_COLORS["surface_alt"],
                fg=INKSPACE_COLORS["text_muted"],
                anchor="w",
            ).grid(row=0, column=0, sticky="ew")
            return
        header = self.tk.Frame(frame, bg=INKSPACE_COLORS["surface_alt"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        for text, width in (("Actor", 5), ("Lane", 12), ("Gap", 20), ("Speed", 14), ("Lane change", 34)):
            self.tk.Label(header, text=text, bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"], width=width, anchor="w", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 5))
        for vehicle_index in range(1, active_count + 1):
            row = self.tk.Frame(frame, bg=INKSPACE_COLORS["surface_alt"])
            row.grid(row=vehicle_index, column=0, sticky="ew", pady=2)
            self.tk.Label(row, text="v{}".format(vehicle_index), bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text"], width=5, anchor="w", font=("Consolas", 9, "bold")).pack(side="left", padx=(0, 5))
            self._guide_combo(row, self.guide_vehicle_lane_vars[vehicle_index], ("Left", "Middle", "Right"), width=10).pack(side="left", padx=(0, 5))
            gap_box = self.tk.Frame(row, bg=INKSPACE_COLORS["surface_alt"])
            gap_box.pack(side="left", padx=(0, 5))
            self._guide_combo(gap_box, self.guide_vehicle_relative_vars[vehicle_index], ("ahead", "behind"), width=7).pack(side="left")
            self._guide_entry(gap_box, self.guide_vehicle_gap_vars[vehicle_index], width=5).pack(side="left", padx=(3, 0))
            self.tk.Label(gap_box, text="s", bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"]).pack(side="left", padx=(2, 0))
            speed_box = self.tk.Frame(row, bg=INKSPACE_COLORS["surface_alt"])
            speed_box.pack(side="left", padx=(0, 5))
            self._guide_entry(speed_box, self.guide_vehicle_speed_vars[vehicle_index], width=6).pack(side="left")
            self.tk.Label(speed_box, textvariable=self.guide_speed_unit_var, bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"], width=5, anchor="w").pack(side="left", padx=(3, 0))
            lc_box = self.tk.Frame(row, bg=INKSPACE_COLORS["surface_alt"])
            lc_box.pack(side="left")
            self._guide_combo(lc_box, self.guide_vehicle_target_lane_vars[vehicle_index], ("Keep", "Left", "Middle", "Right"), width=8).pack(side="left")
            self._guide_entry(lc_box, self.guide_vehicle_lc_time_vars[vehicle_index], width=5).pack(side="left", padx=(3, 0))
            self.tk.Label(lc_box, text="s /", bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"]).pack(side="left", padx=(2, 0))
            self._guide_entry(lc_box, self.guide_vehicle_lc_duration_vars[vehicle_index], width=5).pack(side="left")
            self.tk.Label(lc_box, text="s", bg=INKSPACE_COLORS["surface_alt"], fg=INKSPACE_COLORS["text_muted"]).pack(side="left", padx=(2, 0))

    def _guide_set_step(self, step_name, update_status=True):
        if step_name not in PROMPT_GUIDE_STEPS:
            return
        self._guide_step_index = PROMPT_GUIDE_STEPS.index(step_name)
        for name, frame in self._guide_step_frames.items():
            if name == step_name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        self._refresh_guide_step_chrome()
        if update_status:
            self.status_var.set("Start Wizard step: {}.".format(step_name))

    def _guide_next_step(self):
        next_index = min(len(PROMPT_GUIDE_STEPS) - 1, self._guide_step_index + 1)
        self._guide_set_step(PROMPT_GUIDE_STEPS[next_index])

    def _guide_previous_step(self):
        previous_index = max(0, self._guide_step_index - 1)
        self._guide_set_step(PROMPT_GUIDE_STEPS[previous_index])

    def _refresh_guide_step_chrome(self):
        active_step = PROMPT_GUIDE_STEPS[self._guide_step_index]
        for step_name, button in self._guide_step_buttons.items():
            selected = step_name == active_step
            button.configure(
                bg=INKSPACE_COLORS["brand_tint"] if selected else INKSPACE_COLORS["surface_alt"],
                fg=INKSPACE_COLORS["brand_hover"] if selected else INKSPACE_COLORS["text"],
                highlightbackground=INKSPACE_COLORS["brand_border"] if selected else INKSPACE_COLORS["border"],
            )
        if getattr(self, "guide_back_button", None) is not None:
            self.guide_back_button.configure(state="normal" if self._guide_step_index > 0 else "disabled")
        if getattr(self, "guide_next_button", None) is not None:
            self.guide_next_button.configure(
                text="Finish" if self._guide_step_index >= len(PROMPT_GUIDE_STEPS) - 1 else "Next Step",
                state="normal" if self._guide_step_index < len(PROMPT_GUIDE_STEPS) - 1 else "disabled",
            )
        if getattr(self, "guide_step_hint_label", None) is not None:
            self.guide_step_hint_label.configure(text="{}/{}  {}".format(self._guide_step_index + 1, len(PROMPT_GUIDE_STEPS), active_step))

    def _watch_guide_variables(self):
        if self._guide_trace_tokens:
            return
        for variable in self._guide_variables():
            token = variable.trace_add("write", lambda *_args: self._on_guide_variable_changed())
            self._guide_trace_tokens.append((variable, token))

    def _guide_variables(self):
        return (
            self.guide_duration_var,
            self.guide_ego_lane_var,
            self.guide_ego_speed_var,
            self.guide_speed_unit_var,
            self.guide_vehicle_enabled_var,
            self.guide_vehicle_count_var,
            *tuple(self.guide_vehicle_lane_vars.values()),
            *tuple(self.guide_vehicle_relative_vars.values()),
            *tuple(self.guide_vehicle_gap_vars.values()),
            *tuple(self.guide_vehicle_speed_vars.values()),
            *tuple(self.guide_vehicle_target_lane_vars.values()),
            *tuple(self.guide_vehicle_lc_time_vars.values()),
            *tuple(self.guide_vehicle_lc_duration_vars.values()),
            self.guide_obstacle_enabled_var,
            self.guide_obstacle_kind_var,
            self.guide_obstacle_mode_var,
            self.guide_obstacle_lane_var,
            self.guide_obstacle_distance_var,
            self.guide_obstacle_reference_var,
            self.guide_obstacle_direction_var,
            self.guide_obstacle_start_var,
            self.guide_obstacle_end_var,
            self.guide_obstacle_drop_source_var,
            self.guide_obstacle_offset_var,
            self.guide_ped_enabled_var, self.guide_ped_side_var, self.guide_ped_end_var,
            self.guide_ped_gap_var, self.guide_ped_time_var, self.guide_ped_duration_var,
            self.exp_standard_markers_var,
            self.exp_tor_enabled_var,
            self.exp_tor_onset_var,
            self.exp_tor_deadline_var,
            self.exp_failure_action_var,
            self.exp_hazard_onset_var,
            self.exp_custom_markers_var,
            self.exp_stage2_direction_var,
            self.exp_ready_text_var,
        )

    def _on_guide_variable_changed(self):
        if not self.top.winfo_exists():
            return
        self._guide_ensure_drop_vehicle()
        self._guide_update_obstacle_fields()
        self._refresh_guide_preview()
        self._schedule_design_refresh()

    def _toggle_guide_collapsed(self):
        collapsed = not self.guide_collapsed_var.get()
        self.guide_collapsed_var.set(collapsed)
        if collapsed:
            self.guide_body.grid_remove()
            self.guide_shell.grid_remove()
            self.guide_collapse_button.configure(text="Open")
            self.status_var.set("Start Wizard collapsed.")
        else:
            self.guide_shell.grid()
            self.guide_body.grid(row=1, column=0, sticky="ew")
            self.guide_collapse_button.configure(text="Collapse")
            self._refresh_guide_preview()
            self.status_var.set("Start Wizard ready.")

    def _open_prompt_wizard(self):
        if self.guide_collapsed_var.get():
            self._toggle_guide_collapsed()
        self._guide_set_step(PROMPT_GUIDE_STEPS[self._guide_step_index], update_status=False)
        self.status_var.set("Start Wizard ready.")

    def _guide_prompt_text(self):
        traffic = self._guide_traffic_prompt_text().strip()
        experiment = self._guide_experiment_prompt_text().strip()
        parts = [part for part in (traffic, experiment) if part]
        return "\n\n".join(parts).strip() + "\n"

    def _guide_traffic_prompt_text(self):
        def value(variable, fallback):
            text = str(variable.get()).strip()
            return text or fallback
        def lane(variable):
            return value(variable, "Middle").lower()

        speed_unit = value(self.guide_speed_unit_var, "km/h")
        duration_text = str(self.guide_duration_var.get()).strip()
        if duration_text:
            duration_line = "scenario duration={}s".format(duration_text)
        elif self.exp_tor_enabled_var.get():
            # Blank duration + TOR alert: open-ended scene that holds until the
            # take-over interaction completes instead of a fixed time limit.
            duration_line = "scenario duration=auto"
        else:
            duration_line = "scenario duration=35s"
        lines = [
            duration_line,
            "ego: lane={}, speed={}{}".format(lane(self.guide_ego_lane_var), value(self.guide_ego_speed_var, "80"), speed_unit),
        ]
        active_vehicle_count = self._guide_vehicle_count() if self.guide_vehicle_enabled_var.get() else 0
        for vehicle_index in range(1, active_vehicle_count + 1):
            vehicle_speed = value(self.guide_vehicle_speed_vars[vehicle_index], "")
            vehicle_line = "v{}: lane={}, {}={}s".format(
                vehicle_index,
                lane(self.guide_vehicle_lane_vars[vehicle_index]),
                value(self.guide_vehicle_relative_vars[vehicle_index], "ahead"),
                value(self.guide_vehicle_gap_vars[vehicle_index], "7"),
            )
            if vehicle_speed:
                vehicle_line += ", speed={}{}".format(vehicle_speed, speed_unit)
            lines.append(vehicle_line)
        for vehicle_index in range(1, active_vehicle_count + 1):
            target_lane = value(self.guide_vehicle_target_lane_vars[vehicle_index], "Keep")
            lc_time = value(self.guide_vehicle_lc_time_vars[vehicle_index], "")
            if target_lane != "Keep" and lc_time:
                lines.append("t={}s: v{} -> {}, duration={}s".format(
                    lc_time,
                    vehicle_index,
                    target_lane.lower(),
                    value(self.guide_vehicle_lc_duration_vars[vehicle_index], "4"),
                ))
        if self.guide_obstacle_enabled_var.get():
            if value(self.guide_obstacle_mode_var, "fixed").lower().startswith("drop"):
                lines.append("t={}s: {} drops {}, offset={}m, end={}s".format(
                    value(self.guide_obstacle_start_var, "8"),
                    value(self.guide_obstacle_drop_source_var, "v1"),
                    value(self.guide_obstacle_kind_var, "barrel"),
                    value(self.guide_obstacle_offset_var, "0.5"),
                    value(self.guide_obstacle_end_var, "30"),
                ))
            else:
                lines.append("obstacle1: {}, lane={}, {}m {} {}, t={}s, end={}s".format(
                    value(self.guide_obstacle_kind_var, "barrier"),
                    lane(self.guide_obstacle_lane_var),
                    value(self.guide_obstacle_distance_var, "100"),
                    value(self.guide_obstacle_direction_var, "ahead of"),
                    value(self.guide_obstacle_reference_var, "<reference vehicle>"),
                    value(self.guide_obstacle_start_var, "10"),
                    value(self.guide_obstacle_end_var, "30"),
                ))
        if self.guide_ped_enabled_var.get():
            lines.append('ped1: {} -> {}, {}m, t={}s, duration={}s'.format(
                self.guide_ped_side_var.get(), self.guide_ped_end_var.get(), self.guide_ped_gap_var.get(),
                self.guide_ped_time_var.get(), self.guide_ped_duration_var.get()))
        return "\n".join(lines).strip() + "\n"

    def _guide_experiment_prompt_text(self):
        def on_off(value):
            return "on" if bool(value) else "off"

        def seconds_token(value):
            text = str(value or "").strip()
            if not text:
                return ""
            return text if text.lower().endswith("s") else text + "s"

        def quote_text(value):
            text = str(value or "").strip()
            if not text:
                return "\"\""
            return "\"" + text.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

        lines = [
            "experiment standard_markers={}".format(on_off(self.exp_standard_markers_var.get())),
        ]
        alert_enabled = bool(self.exp_tor_enabled_var.get())
        onset = seconds_token(self.exp_tor_onset_var.get())
        deadline = seconds_token(self.exp_tor_deadline_var.get())
        if alert_enabled or onset or deadline:
            alert_line = "experiment alert={} start={} deadline={} fallback={}".format(
                on_off(alert_enabled),
                onset or "7s",
                deadline or "",
                self.exp_failure_action_var.get().strip() or "brake",
            ).strip()
            lines.append(alert_line)
            lines.append(
                "experiment stage2={} ready_text={}".format(
                    self.exp_stage2_direction_var.get().strip() or "front",
                    quote_text(self.exp_ready_text_var.get().strip() or "Please take over."),
                )
            )
        hazard = seconds_token(self.exp_hazard_onset_var.get())
        if hazard:
            lines.append("experiment hazard={}".format(hazard))
        custom_markers = self.exp_custom_markers_var.get().strip()
        if custom_markers:
            lines.append("experiment custom={}".format(custom_markers))
        return "\n".join(lines).strip() + "\n"

    def _split_experiment_prompt_lines(self, prompt_text):
        scenario_lines = []
        experiment_lines = []
        for line in str(prompt_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith("experiment ") or lowered.startswith("experiment:"):
                experiment_lines.append(stripped)
            else:
                scenario_lines.append(line)
        return "\n".join(scenario_lines).strip(), experiment_lines

    def _apply_prompt_experiment_overrides(self, prompt_text):
        scenario_text, experiment_lines = self._split_experiment_prompt_lines(prompt_text)
        if experiment_lines or self._experiment_lines_in_prompt:
            # The prompt carries the experiment settings. Reset to defaults
            # first, then re-apply only the lines still present, so deleting
            # an "experiment ..." line (e.g. the TOR alert) actually removes
            # that setting instead of resurrecting the stale loaded value.
            self._reset_experiment_prompt_settings()
            for line in experiment_lines:
                self._apply_prompt_experiment_line(line)
            self._refresh_guide_preview()
        self._experiment_lines_in_prompt = bool(experiment_lines)
        return scenario_text, len(experiment_lines)

    def _reset_experiment_prompt_settings(self):
        """Defaults applied before re-reading experiment lines from the prompt.

        The TOR adaptive prompt has no prompt-line syntax, so it is left
        untouched here.
        """
        self.exp_standard_markers_var.set(True)
        self.exp_tor_enabled_var.set(False)
        self.exp_tor_onset_var.set("")
        self.exp_tor_deadline_var.set("")
        self.exp_failure_action_var.set("brake")
        self.exp_stage2_direction_var.set("front")
        self.exp_ready_text_var.set("Please take over.")
        self.exp_hazard_onset_var.set("")
        self.exp_custom_markers_var.set("")

    def _apply_prompt_experiment_line(self, line):
        body = str(line or "").strip()
        if body.lower().startswith("experiment"):
            body = body[len("experiment"):].strip()
        if body.startswith(":"):
            body = body[1:].strip()
        if not body:
            return
        lowered = body.lower()
        if lowered.startswith("custom"):
            value = self._experiment_prompt_value_after_equals(body)
            self.exp_custom_markers_var.set("" if self._experiment_prompt_is_off(value) else value)
            return
        values = self._experiment_prompt_key_values(body)
        if not values:
            return
        if "standard" in values and "standard_markers" not in values:
            values["standard_markers"] = values["standard"]
        if "standard_markers" in values:
            self.exp_standard_markers_var.set(self._experiment_prompt_bool(values["standard_markers"], default=True))

        has_alert_fields = any(key in values for key in ("alert", "enabled", "start", "alert_start", "onset", "deadline", "latest", "latest_tor", "fallback"))
        if has_alert_fields:
            alert_value = values.get("alert", values.get("enabled"))
            if alert_value is not None:
                enabled = self._experiment_prompt_bool(alert_value, default=True)
                self.exp_tor_enabled_var.set(enabled)
                if not enabled:
                    self.exp_tor_onset_var.set("")
                    self.exp_tor_deadline_var.set("")
            elif any(key in values for key in ("start", "alert_start", "onset", "deadline", "latest", "latest_tor")):
                self.exp_tor_enabled_var.set(True)
            onset = values.get("start", values.get("alert_start", values.get("onset")))
            if onset is not None and not self._experiment_prompt_is_off(onset):
                self.exp_tor_onset_var.set(self._experiment_prompt_seconds_text(onset))
            deadline = values.get("deadline", values.get("latest", values.get("latest_tor")))
            if deadline is not None and not self._experiment_prompt_is_off(deadline):
                self.exp_tor_deadline_var.set(self._experiment_prompt_seconds_text(deadline))
            fallback = values.get("fallback")
            if fallback:
                self.exp_failure_action_var.set(str(fallback).strip())

        hazard = values.get("hazard", values.get("hazard_onset"))
        if hazard is not None:
            self.exp_hazard_onset_var.set("" if self._experiment_prompt_is_off(hazard) else self._experiment_prompt_seconds_text(hazard))

        stage2 = values.get("stage2", values.get("stage2_look"))
        if stage2:
            self.exp_stage2_direction_var.set(str(stage2).strip())
        ready_text = values.get("ready_text", values.get("speech"))
        if ready_text is not None:
            self.exp_ready_text_var.set(str(ready_text).strip())
        custom = values.get("custom", values.get("custom_markers"))
        if custom is not None:
            self.exp_custom_markers_var.set("" if self._experiment_prompt_is_off(custom) else str(custom).strip())

    def _experiment_prompt_value_after_equals(self, text):
        if "=" not in str(text or ""):
            return ""
        return str(text).split("=", 1)[1].strip()

    def _experiment_prompt_key_values(self, text):
        try:
            tokens = shlex.split(str(text or "").replace(",", " "))
        except Exception:
            tokens = str(text or "").replace(",", " ").split()
        values = {}
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = key.strip().lower().replace("-", "_")
            if not key:
                continue
            values[key] = value.strip()
        return values

    def _experiment_prompt_bool(self, value, default=False):
        text = str(value or "").strip().lower()
        if text in ("1", "on", "true", "yes", "enabled", "enable"):
            return True
        if text in ("0", "off", "false", "no", "disabled", "disable", "none"):
            return False
        return bool(default)

    def _experiment_prompt_is_off(self, value):
        return str(value or "").strip().lower() in ("", "0", "off", "false", "no", "none", "null", "-")

    def _experiment_prompt_seconds_text(self, value):
        text = str(value or "").strip().strip("\"'")
        if text.lower().endswith("s"):
            text = text[:-1]
        return text.strip()

    def _join_prompt_and_experiment_text(self, scenario_text, experiment_text):
        scenario = str(scenario_text or "").strip()
        experiment = str(experiment_text or "").strip()
        if scenario and experiment:
            return scenario + "\n\n" + experiment + "\n"
        if scenario:
            return scenario + "\n"
        if experiment:
            return experiment + "\n"
        return ""

    def _refresh_guide_preview(self):
        preview = getattr(self, "guide_preview_text", None)
        if preview is None:
            return
        text = (
            "Prompt Text (inserted)\n"
            "----------------------\n"
            "{}\n"
            "Experiment Markers / Alert Deadline (saved to JSON)\n"
            "-----------------------------------------------\n"
            "{}"
        ).format(self._guide_prompt_text(), self._guide_experiment_preview_text())
        self._set_text(preview, text)

    def _guide_experiment_preview_text(self):
        lines = [
            "standard markers: {}".format("on" if self.exp_standard_markers_var.get() else "off"),
        ]
        alert_enabled = bool(self.exp_tor_enabled_var.get())
        onset = self.exp_tor_onset_var.get().strip()
        deadline = self.exp_tor_deadline_var.get().strip()
        if alert_enabled or onset or deadline:
            lines.extend([
                "alert: {}".format("on" if alert_enabled else "off"),
                "alert start: {}".format(onset or "(required when enabled)"),
                "latest TOR: {}".format(deadline or "(alert start + 6s)"),
                "fallback: {}".format(self.exp_failure_action_var.get().strip() or "brake"),
                "stage 2 look: {}".format(self.exp_stage2_direction_var.get().strip() or "front"),
                "stage 3 speech: {}".format(self.exp_ready_text_var.get().strip() or "Please take over."),
            ])
        else:
            lines.append("alert: off")
        hazard_onset = self.exp_hazard_onset_var.get().strip()
        if hazard_onset:
            lines.append("hazard marker: {}s".format(hazard_onset))
        custom_markers = self.exp_custom_markers_var.get().strip()
        if custom_markers:
            lines.append("custom markers: {}".format(custom_markers))
        return "\n".join(lines).strip() + "\n"

    def _guide_replace_prompt(self):
        self._maybe_apply_guide_scenario_name()
        self._replace_prompt_text(self._guide_prompt_text())
        self.status_var.set("Start Wizard prompt inserted.")

    def _guide_append_prompt(self):
        current = self.prompt_text.get("1.0", "end-1c")
        if current and not current.endswith("\n"):
            self.prompt_text.insert("end", "\n")
        if current.strip():
            self.prompt_text.insert("end", "\n")
        self.prompt_text.insert("end", self._guide_prompt_text())
        self.prompt_text.focus_set()
        self.status_var.set("Start Wizard prompt appended.")

    def _guide_build_and_analyze(self):
        self._maybe_apply_guide_scenario_name()
        self._replace_prompt_text(self._guide_prompt_text())
        self.preview()

    def _replace_prompt_text(self, text):
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", text)
        self.prompt_text.focus_set()

    def _maybe_apply_guide_scenario_name(self):
        current = self.adapter.scenario_name_from_output(self.name_var.get(), fallback="new_prompt_scenario")
        if current not in {"new_prompt_scenario", "low_speed_tor_new", "low_speed_alert_new"}:
            return
        self.name_var.set(self.adapter.scenario_name_from_output("wizard_prompt", fallback="wizard_prompt"))

    def _append_prompt_snippet_text(self, text, label):
        current = self.prompt_text.get("1.0", "end-1c")
        if current and not current.endswith("\n"):
            self.prompt_text.insert("end", "\n")
        self.prompt_text.insert("end", text.rstrip() + "\n")
        self.prompt_text.focus_set()
        self.status_var.set("Inserted: {}".format(label))

    def _insert_completion_snippet(self, label):
        spec = SNIPPET_PARAM_FORMS.get(label)
        if spec is not None:
            self._open_snippet_param_dialog(label, spec)
            return
        item = next((candidate for candidate in SCANNER_PROMPT_COMPLETIONS if candidate.label == label), None)
        if item is None:
            self.status_var.set("Snippet not found: {}".format(label))
            return
        self._append_prompt_snippet_text(item.snippet, label)

    def _open_authoring_template(self, label):
        from carla_nl.prompt_template_edits import template_context, next_number
        current = self.prompt_text.get('1.0', 'end-1c')
        context = template_context(current)
        initial_values = {}
        if label in ('scenario duration', 'ego lane/speed'):
            try:
                _preview, items = self.adapter.convert_prompt(current)
                scenario = items[0].scenario
                ego = scenario['vehicles']['ego']
                initial_values = (dict(d='{:g}'.format(scenario['total_time_s'])) if label == 'scenario duration'
                                  else dict(lane=ego['lane'].lower(), speed='{:g}'.format(ego['speed_kmh']), unit='km/h'))
            except (ValueError, KeyError, IndexError):
                pass
        alias = {'front vehicle': 'v1 lane + gap', 'rear vehicle': 'v1 lane + gap',
                 'collision takeover alert': 'takeover alert'}
        original = SNIPPET_PARAM_FORMS[alias.get(label, label)]
        fields = []
        vehicles = ['vehicle_' + str(n) for n in sorted(context['vehicle'])]
        actors = (['ego'] if context['ego'] else []) + vehicles
        namespace = {'front vehicle': 'vehicle', 'rear vehicle': 'vehicle',
                     'obstacle barrier': 'obstacle', 'dropped obstacle': 'obstacle',
                     'pedestrian crossing': 'pedestrian'}.get(label)
        for key, title, kind, default, options in original['fields']:
            default = initial_values.get(key, default)
            if key == 'n' and namespace:
                default = next_number(context, namespace)
            elif key == 'rel' and label in ('front vehicle', 'rear vehicle'):
                default = 'ahead' if label == 'front vehicle' else 'behind'
            elif key == 'actor':
                options = tuple(actors)
                default = vehicles[0] if vehicles else (actors[0] if actors else '')
            elif key == 'target':
                options = tuple('v' + str(n) for n in sorted(context['vehicle']))
                default = options[0] if options else ''
            elif key == 'reference':
                options = tuple(actors)
                default = ''  # The user explicitly chooses the reference vehicle.
            elif key == 'obstacle':
                options = tuple('obstacle ' + str(n) for n in sorted(context['obstacle']))
                default, kind = (options[0] if options else ''), 'combo'
            elif key == 'trigger' and label == 'collision takeover alert':
                default = 'Before collision (TTC)'
            elif key == 'n' and label == 'timed gap target':
                options = tuple(str(n) for n in sorted(context['vehicle']))
                default, kind = (options[0] if options else ''), 'combo'
            fields.append((key, title, kind, default, options))
        self._open_snippet_param_dialog(label, dict(original, fields=fields), checked=True)

    def _template_error_message(self, error):
        from carla_nl.prompt_guidance_layout import english_guidance
        requests = getattr(error, 'repair_requests', ())
        if requests:
            details = [request['role'].replace('_', ' ').capitalize() + ': ' + english_guidance(field)
                       for request in requests for field in request.get('fields', ())]
            message = '\n'.join('• ' + detail for detail in details[:3])
        else:
            message = english_guidance(str(error)).strip()
        # Bound the form's error area so its controls remain reachable.
        message = '\n'.join(message.splitlines()[:7])
        if len(message) > 600:
            message = message[:597] + '…'
        return message + '\nYour draft is unchanged. Revise the fields above, or close this form and use Analyze to repair the existing description.'

    def _open_snippet_param_dialog(self, label, spec, checked=False):
        """Small form that asks for the snippet's values, then inserts the
        finished line(s) -- no placeholder editing needed."""
        tk = self.tk
        ttk = self.ttk
        previous = getattr(self, '_snippet_dialog', None)
        if previous is not None and previous.winfo_exists():
            previous.destroy()
        top = tk.Toplevel(self.top)
        self._snippet_dialog = top
        top.title(label)
        configure_inkspace_window(top)
        top.transient(self.top)
        top.resizable(False, False)

        frame = ttk.Frame(top, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        variables = {}
        widgets = {}
        field_labels = {}
        top.variables, top.fields = variables, widgets
        first_widget = None
        for row, (key, field_label, kind, default, options) in enumerate(spec["fields"]):
            field_labels[key] = ttk.Label(frame, text=field_label)
            field_labels[key].grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            var = tk.StringVar(value=default)
            variables[key] = var
            if kind == "combo":
                widget = ttk.Combobox(frame, textvariable=var, values=list(options or ()), state="readonly", width=26 if label == 'takeover alert' else 16)
            else:
                widget = ttk.Entry(frame, textvariable=var, width=18)
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            widgets[key] = widget
            if first_widget is None:
                first_widget = widget

        error_var = tk.StringVar(value='')
        top.error_var = error_var
        ttk.Label(frame, textvariable=error_var, foreground='#ef9a9a', wraplength=420).grid(
            row=len(spec['fields']), column=0, columnspan=2, sticky='ew')
        if label in ('takeover alert', 'collision takeover alert'):
            def update_channels(*_args):
                visual = 'Visual' in variables['mode'].get()
                auditory = 'Auditory' in variables['mode'].get()
                collision = variables['trigger'].get() == 'Before collision (TTC)'
                widgets['audio'].configure(state='readonly' if auditory else 'disabled')
                widgets['target'].configure(state='readonly' if visual and not collision else 'disabled')
                for widget in (widgets['target'], field_labels['target']):
                    widget.grid_remove() if collision else widget.grid()
                widgets['t'].configure(state='disabled' if collision else 'normal')
                for key in ('ttc', 'obstacle'):
                    widgets[key].configure(state='normal' if collision else 'disabled')
                widgets['text'].configure(state='normal' if visual or (auditory and variables['audio'].get() == 'Speech') else 'disabled')
            variables['mode'].trace_add('write', update_channels)
            variables['audio'].trace_add('write', update_channels)
            variables['trigger'].trace_add('write', update_channels)
            update_channels()

        if checked:
            preview_var = tk.StringVar()
            top.preview_var = preview_var
            def update_preview(*_args):
                error_var.set('')
                try:
                    preview_var.set(spec['build']({key: var.get() for key, var in variables.items()}))
                except (ValueError, KeyError):
                    preview_var.set('Complete the fields above to preview the description.')
            for variable in variables.values():
                variable.trace_add('write', update_preview)
            update_preview()
            ttk.Label(frame, text='Description to insert' + (' / update existing definition' if label in ('scenario duration', 'ego lane/speed') else ''),
                      style='Section.TLabel').grid(row=len(spec['fields']) + 1, column=0, columnspan=2, sticky='w', pady=(8, 3))
            ttk.Label(frame, textvariable=preview_var, wraplength=450, justify='left').grid(
                row=len(spec['fields']) + 2, column=0, columnspan=2, sticky='ew')

        def do_insert(_event=None):
            if not top.winfo_exists() or getattr(top, '_inserting', False):
                return 'break'
            top._inserting = True
            values = {key: var.get() for key, var in variables.items()}
            try:
                if checked:
                    from carla_nl.prompt_template_edits import validate_template_values, prepare_template_edit, validate_template_candidate
                    current = self.prompt_text.get('1.0', 'end-1c')
                    validate_template_values(label, spec, values, current)
                text = spec["build"](values)
                if checked:
                    proposed = prepare_template_edit(current, label, text)
                    candidate_ready = validate_template_candidate(proposed, label, map_name=self.map_var.get(),
                                                                  weather_preset=self.weather_var.get(), quality_level=self.quality_var.get())
            except ValueError as exc:
                error_var.set(self._template_error_message(exc) if checked else str(exc))
                top._inserting = False
                return 'break'
            except Exception as exc:
                write_exception(exc)
                error_var.set('Could not check this description. Your draft is unchanged; revise the fields and try again.')
                top._inserting = False
                return 'break'
            top.destroy()
            if checked:
                widget = self.prompt_text
                widget.edit_separator()
                widget.replace('1.0', 'end-1c', proposed)
                widget.edit_separator()
                self._highlight_prompt_syntax()
                self._on_prompt_modified()
                self.status_var.set('Description checked and applied. Click Analyze to review the scenario. Ctrl+Z restores your draft.')
                if not candidate_ready:
                    self.preview()
                    self.status_var.set('Description added. Complete the missing Ego lane and speed in Review, or use Add to prompt → Ego vehicle.')
            else:
                self._append_prompt_snippet_text(text, label)
            return 'break'

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(spec["fields"]) + (3 if checked else 1), column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        top.insert_button = ttk.Button(buttons, text="Insert", command=do_insert, style="Accent.TButton")
        top.insert_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=top.destroy).grid(row=0, column=1, sticky="ew")
        top.bind("<Return>", do_insert)
        top.bind("<Escape>", lambda _event: top.destroy())

        # place the popup near the prompt box and focus the first field
        try:
            top.update_idletasks()
            x = self.prompt_text.winfo_rootx() + 60
            y = self.prompt_text.winfo_rooty() + 30
            x = max(10, min(x, top.winfo_screenwidth() - top.winfo_reqwidth() - 20))
            y = max(10, min(y, top.winfo_screenheight() - top.winfo_reqheight() - 70))
            top.geometry("+{}+{}".format(x, y))
        except Exception:
            pass
        top.grab_set()
        if first_widget is not None:
            first_widget.focus_set()

    def _open_prompt_cheatsheet(self):
        top = self.tk.Toplevel(self.top)
        top.title("Prompt Command Cheatsheet")
        top.geometry("760x520")
        configure_inkspace_window(top)
        root = self.ttk.Frame(top, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        self.ttk.Label(root, text="Prompt Command Cheatsheet", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        search_var = self.tk.StringVar()
        self.ttk.Entry(root, textvariable=search_var).grid(row=1, column=0, sticky="ew", pady=(10, 8))
        text = self.scrolledtext.ScrolledText(root, wrap="word", font=("Consolas", 10), state="disabled")
        style_inkspace_text(text)
        text.grid(row=2, column=0, sticky="nsew")

        def render():
            query = search_var.get().strip().lower()
            chunks = []
            for item in SCANNER_PROMPT_COMPLETIONS:
                searchable = "{}\n{}\n{}".format(item.label, item.snippet, " ".join(item.aliases)).lower()
                if query and query not in searchable:
                    continue
                chunks.append("{}\n{}\n".format(item.label, item.snippet))
            self._set_text(text, "\n".join(chunks))

        search_var.trace_add("write", lambda *_args: render())
        render()
        self.ttk.Button(root, text="Close", command=top.destroy).grid(row=3, column=0, sticky="ew", pady=(10, 0))

    @staticmethod
    def _arrange_prompt_timeline(text):
        """ONE fixed layout: definition lines on top, then 't=Ns:' timeline lines
        sorted by time (stable for equal t). Idempotent by construction."""
        defs = []
        timeline = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = _PROMPT_TIMELINE_LINE_RE.match(stripped)
            if match:
                timeline.append((float(match.group(1)), stripped))
            else:
                defs.append(stripped)
        timeline.sort(key=lambda item: item[0])
        out = list(defs)
        if defs and timeline:
            out.append("")
        out.extend(line for _t, line in timeline)
        return "\n".join(out).strip()

    def _format_prompt_by_timeline(self):
        """Show the converted events without replacing the author's prompt."""
        try:
            _preview, converted = self._convert()
            self._update_timeline(converted)
            if not self.timeline_text.frame.winfo_manager():
                self._toggle_prompt_timeline()
            self.status_var.set("Timeline refreshed below. Your Prompt is unchanged.")
        except ValueError as exc:
            self._show_prompt_guidance(exc)
        except Exception as exc:
            self.messagebox.showerror("Timeline failed", str(exc), parent=self.top)

    def _update_timeline(self, converted):
        lines = []
        for item in converted:
            model = build_scenario_visual_model(item.scenario)
            lines.append('{} ({:g}s)'.format(item.name, model['total_time_s']))
            for event in model['events']:
                lines.append('t={:g}s: {}  {}'.format(event['time_s'], event['label'], event['details']).rstrip())
            for event in model.get('conditional_events', []):
                lines.append('When {}: {}'.format(event['condition'], event['details']))
            lines.append('')
        self._set_text(self.timeline_text, '\n'.join(lines))

    def _canonical_timeline_prompt(self, scenario_text):
        """Convert prompt text and regenerate it as canonical timeline lines.

        Returns the canonical text, "" when nothing useful was produced, or
        None when conversion failed (an error dialog has been shown). Batch
        prompts (multiple scenarios) are left alone -- regenerating only the
        first variant would drop the others.
        """
        try:
            _preview, converted = self.adapter.convert_prompt(
                scenario_text,
                name=self.adapter.scenario_name_from_output(self.name_var.get(), fallback="prompt_scenario"),
            )
            if getattr(_preview, "route", "") == "checked_prose":
                raise ValueError("Format Timeline is currently for structured prompts. Edit prose sentences directly; converting them back to wizard commands can lose obstacle or TOR settings. / 自然语言请直接修改原句；格式化暂不支持无损转换。")
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Format failed", str(exc), parent=self.top)
            return None
        if not converted or len(converted) != 1:
            return ""
        scenario = dict(getattr(converted[0], "scenario", {}) or {})
        try:
            return scenario_payload_to_prompt(scenario).strip()
        except Exception as exc:
            write_exception(exc)
            return ""

    def _apply_safe_defaults_to_prompt(self):
        prompt_text = self.prompt_text.get("1.0", "end").strip()
        if not prompt_text:
            self.status_var.set("Prompt is empty.")
            return
        scenario_text, experiment_count = self._apply_prompt_experiment_overrides(prompt_text)
        if not scenario_text:
            self.status_var.set("Traffic prompt is empty.")
            return
        try:
            updated, warnings = self.adapter.apply_visible_safe_defaults(scenario_text)
        except ValueError as exc:
            self._show_prompt_guidance(exc)
            return
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Use Defaults failed", str(exc), parent=self.top)
            return
        updated_prompt = self._join_prompt_and_experiment_text(updated, self._guide_experiment_prompt_text() if experiment_count else "")
        if updated_prompt.strip() == prompt_text.strip():
            self.status_var.set("No additional safe defaults were needed.")
            return
        self._replace_prompt_text(updated_prompt)
        self._set_text(self.preview_text, ("\n".join(warnings) if warnings else "Safe defaults applied.") + "\n")
        self.status_var.set("Safe defaults appended to the prompt.")

    def _clear_prompt(self):
        self._prompt_field_navigation_active = False
        self._analysis_snapshot = None
        self.prompt_text.delete("1.0", "end")
        self._refresh_analysis_state()
        self._set_text(self.timeline_text, "")
        self._set_text(self.preview_text, "")
        self._set_text(self.json_text, "")
        self.status_var.set("Prompt cleared.")

    def _text_tab(self, name):
        frame = self.ttk.Frame(self.tabs)
        text = self.scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 10), state="disabled")
        style_inkspace_text(text)
        text.pack(fill="both", expand=True)
        self.tabs.add(frame, text=name)
        return text

    def open_adaptive_prompt_editor(self):
        try:
            open_tor_adaptive_prompt_dialog(
                self.top,
                self.tk,
                self.ttk,
                self.scrolledtext,
                self.exp_adaptive_prompt_var,
                title="Scenario TOR Adaptive Prompt",
            )
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("TOR Adaptive Prompt", str(exc), parent=self.top)

    def _history_tab(self):
        frame = self.ttk.Frame(self.tabs, padding=6)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        top = self.ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(0, weight=1)
        self.ttk.Label(top, textvariable=self.history_var).grid(row=0, column=0, sticky="w")
        self.ttk.Button(top, text="Restore", command=self.restore_selected_history).grid(row=0, column=1, sticky="e")
        listbox = self.tk.Listbox(frame, exportselection=False, height=5)
        style_inkspace_listbox(listbox)
        listbox.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        listbox.bind("<<ListboxSelect>>", lambda _event: self._sync_history_text())
        listbox.bind("<Double-1>", lambda _event: self.restore_selected_history())
        text = self.scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 10), state="disabled")
        style_inkspace_text(text)
        text.grid(row=2, column=0, sticky="nsew")
        frame.rowconfigure(2, weight=2)
        self.tabs.add(frame, text="History")
        return frame, listbox, text

    def _load_example(self):
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", PROMPT_EXAMPLE)
        self.original_prompt_text = PROMPT_EXAMPLE.strip()
        self._experiment_lines_in_prompt = bool(self._split_experiment_prompt_lines(PROMPT_EXAMPLE)[1])
        self._load_history_entries(current_prompt=PROMPT_EXAMPLE)
        if self.name_var.get().strip() in ("", "new_prompt_scenario"):
            self.name_var.set("low_speed_alert_new")
        self.status_var.set("Example loaded")

    def _load_existing_scenario(self):
        prompt = get_scenario_prompt(self.edit_path)
        if prompt:
            status = "Loaded saved prompt"
        else:
            prompt = scenario_payload_to_prompt(self.edit_payload)
            status = "Loaded editable draft from JSON"
        if self._has_experiment_prompt_settings():
            _scenario_text, experiment_lines = self._split_experiment_prompt_lines(prompt)
            if not experiment_lines:
                prompt = self._join_prompt_and_experiment_text(prompt, self._guide_experiment_prompt_text())
        self._experiment_lines_in_prompt = bool(self._split_experiment_prompt_lines(prompt)[1])
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", prompt)
        self.original_prompt_text = str(prompt or "").strip()
        if self.edit_payload:
            self._set_text(self.json_text, json.dumps(self.edit_payload, indent=2, ensure_ascii=False) + "\n")
        self._load_history_entries(current_prompt=prompt)
        self.status_var.set(status)

    def _has_experiment_prompt_settings(self):
        return (
            not bool(self.exp_standard_markers_var.get())
            or bool(self.exp_tor_enabled_var.get())
            or bool(self.exp_tor_onset_var.get().strip())
            or bool(self.exp_tor_deadline_var.get().strip())
            or bool(self.exp_hazard_onset_var.get().strip())
            or bool(self.exp_custom_markers_var.get().strip())
        )

    def _refresh_spawn_summary(self):
        self._refresh_analysis_state()
        if not self.spawn_xodr:
            self.spawn_summary_var.set("fixed default start")
            self.spawn_button_var.set("Start: Default")
        elif getattr(self, "_spawn_is_default", False):
            self.spawn_summary_var.set(
                "Low Speed TOR: road={road_id} lane_id={lane_id} s={s:.2f}m (prompt lane)".format(**self.spawn_xodr)
            )
            self.spawn_button_var.set("Start: Low Speed TOR")
        else:
            self.spawn_summary_var.set(
                "road={road_id} lane_id={lane_id} s={s:.2f}m, ego lane={lane}".format(
                    road_id=self.spawn_xodr.get("road_id"),
                    lane_id=self.spawn_xodr.get("lane_id"),
                    s=float(self.spawn_xodr.get("s", 0.0) or 0.0),
                    lane=self.spawn_ego_lane.get(),
                )
            )
            self.spawn_button_var.set("Start: Custom")
        self.spawn_menu.entryconfigure(0, label=self.spawn_summary_var.get())
        self.spawn_menu.entryconfigure(3, state="normal" if self.spawn_xodr else "disabled")

    def pick_spawn_on_map(self):
        def accept(selection):
            self._spawn_is_default = False
            self.spawn_xodr = {
                "road_id": int(selection["road_id"]),
                "lane_id": int(selection["lane_id"]),
                "s": float(selection["s"]),
            }
            self.spawn_ego_lane.set(str(selection.get("ego_lane") or "Middle"))
            self.map_var.set(str(selection.get("map_name") or self.map_var.get()))
            self._refresh_spawn_summary()
            self.status_var.set("Selected custom OpenDRIVE start from 2D map; click Preview or Save.")

        try:
            MapSpawnPicker(
                self,
                self.map_var.get(),
                initial_spawn=self.spawn_xodr,
                initial_lane=self.spawn_ego_lane.get(),
                on_accept=accept,
            )
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Pick Spawn", str(exc), parent=self.top)

    def clear_spawn_xodr(self):
        self._spawn_is_default = False
        self.spawn_xodr = None
        self._refresh_spawn_summary()
        self.status_var.set("Custom OpenDRIVE start cleared.")

    def _set_text(self, widget, value):
        if widget is getattr(self, "preview_text", None):
            if getattr(self, "review_empty_hint", None) is not None:
                if value.strip():
                    self.review_empty_hint.place_forget()
                else:
                    self.review_empty_hint.place(x=20, y=24, relwidth=0.88)
            for button in getattr(self, "prompt_repair_buttons", ()):
                button.destroy()
            for form in getattr(self, "prompt_answer_forms", {}).values():
                form.destroy()
            self.prompt_answer_forms = {}
            self.prompt_repair_buttons = []
            self.prompt_repair_controls = {}
            self.prompt_repair_session = None
            self.prompt_diagnostic_links = {}
            self.prompt_detail_sections = {}
            for tag in widget.tag_names():
                if tag.startswith(("diagnostic_link_", "guidance_detail_")):
                    widget.tag_delete(tag)
            self.prompt_text.tag_remove("prompt_unrecognized", "1.0", "end")
            self.prompt_text.tag_remove("prompt_problem_focus", "1.0", "end")
            self.prompt_repair_all_button.grid_remove()
            banner = getattr(self, "prompt_guidance_banner", None)
            if banner is not None:
                banner.grid_remove()
            widget.configure(font=("Consolas", 10), spacing1=0, spacing2=0, spacing3=0)
            for tag in widget.tag_names():
                if tag.startswith("guidance_"):
                    widget.tag_remove(tag, "1.0", "end")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _load_history_entries(self, current_prompt=""):
        if not self.is_edit or self.edit_path is None:
            self.history_entries = []
        else:
            entries = list(get_scenario_prompt_history(self.edit_path))
            current = str(current_prompt or "").strip()
            if current:
                entries = [entry for entry in entries if str(entry.get("prompt") or "").strip() != current]
                entries.insert(0, {"ts": "current", "prompt": current})
            self.history_entries = entries[:PROMPT_HISTORY_LIMIT]
        self._refresh_history_ui()

    def _refresh_history_ui(self):
        self.history_listbox.delete(0, "end")
        if not self.history_entries:
            self.history_var.set("No prompt history yet")
            self._set_text(self.history_text, "")
            return
        self.history_var.set("Prompt history: newest first, max {} entries".format(PROMPT_HISTORY_LIMIT))
        for index, entry in enumerate(self.history_entries):
            ts = str(entry.get("ts") or "")
            label = "Current prompt" if ts == "current" else "Saved {}".format(ts or "(unknown time)")
            first_line = str(entry.get("prompt") or "").strip().splitlines()
            if first_line:
                label += " - " + first_line[0][:60]
            self.history_listbox.insert("end", label)
        self.history_listbox.selection_set(0)
        self._sync_history_text()

    def _sync_history_text(self):
        selection = self.history_listbox.curselection()
        if not selection:
            self._set_text(self.history_text, "")
            return
        index = selection[0]
        if index < 0 or index >= len(self.history_entries):
            self._set_text(self.history_text, "")
            return
        self._set_text(self.history_text, str(self.history_entries[index].get("prompt") or "") + "\n")

    def restore_selected_history(self):
        selection = self.history_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if index < 0 or index >= len(self.history_entries):
            return
        prompt = str(self.history_entries[index].get("prompt") or "").strip()
        if not prompt:
            return
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", prompt + "\n")
        self.status_var.set("Restored prompt from history; click Preview or Save Changes.")
        self.tabs.select(0)

    def show_completion_menu(self, event=None):
        menu = self.tk.Menu(self.top, tearoff=False)
        prefix = self._completion_prefix()
        added = 0
        for label, snippet in PROMPT_COMPLETIONS:
            haystack = label.lower() + " " + snippet.lower()
            if prefix and prefix not in haystack:
                continue
            menu.add_command(
                label=label,
                command=lambda value=snippet: self._insert_completion(value),
            )
            added += 1
        if added == 0:
            for label, snippet in PROMPT_COMPLETIONS:
                menu.add_command(
                    label=label,
                    command=lambda value=snippet: self._insert_completion(value),
                )
        try:
            if event is not None and getattr(event, "x_root", 0) and getattr(event, "y_root", 0):
                menu.tk_popup(event.x_root, event.y_root)
            else:
                x = self.prompt_text.winfo_rootx() + 18
                y = self.prompt_text.winfo_rooty() + 18
                menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def _completion_prefix(self):
        try:
            token = self.prompt_text.get("insert wordstart", "insert").strip().lower()
        except Exception:
            return ""
        return token

    def _insert_completion(self, snippet):
        try:
            self.prompt_text.delete("insert wordstart", "insert")
        except Exception:
            pass
        if self.prompt_text.index("insert").split(".")[1] != "0":
            self.prompt_text.insert("insert", "\n")
        self.prompt_text.insert("insert", snippet)
        self.prompt_text.focus_set()
        self.status_var.set("Inserted autocomplete snippet.")

    def _show_preview_copy_menu(self, event):
        widget = self.preview_text
        index = widget.index("@{},{}".format(event.x, event.y))
        line = int(index.split(".")[0])
        example = ""
        # Tk indices refer to logical lines, so a wrapped example is copied
        # in full. Adjacent example lines form one block; actor labels and
        # explanation text are outside the guidance_example tag.
        if "guidance_example" in widget.tag_names("{}.0".format(line)):
            first = last = line
            while first > 1 and "guidance_example" in widget.tag_names("{}.0".format(first - 1)):
                first -= 1
            while "guidance_example" in widget.tag_names("{}.0".format(last + 1)):
                last += 1
            example = "\n".join(
                value.strip() for value in widget.get("{}.0".format(first), "{}.end".format(last)).splitlines()
            )
        selection = ""
        if widget.tag_ranges("sel"):
            selection = widget.get("sel.first", "sel.last")
        menu = self.preview_copy_menu
        menu.delete(0, "end")
        menu.add_command(
            label="Copy example",
            state="normal" if example else "disabled",
            command=lambda: self._copy_preview_text(example, example=True),
        )
        menu.add_command(
            label="Copy selection", accelerator="Ctrl+C",
            state="normal" if selection else "disabled",
            command=lambda: self._copy_preview_text(selection),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _copy_preview_text(self, text, example=False):
        if not text:
            return
        self.top.clipboard_clear()
        self.top.clipboard_append(text)
        self.status_var.set(
            "Example copied. Paste into your prompt and fill the <fields>."
            if example else "Selected text copied."
        )

    def _show_prompt_guidance(self, error):
        """Emphasize missing facts before recognized values and examples."""
        from carla_nl.prompt_feedback import format_prompt_problem, prompt_placeholder_fields
        from carla_nl.prompt_guidance_layout import grouped_guidance, english_guidance
        if getattr(error, "code", "") == "placeholders":
            # Conversion trims whitespace and removes experiment directives.
            # Editor selections must use offsets in the untouched user draft.
            error.placeholder_fields = [(field.start(), field.end()) for field in
                                        prompt_placeholder_fields(self.prompt_text.get("1.0", "end-1c"))]
        self.converted = []
        self._set_text(self.json_text, "")
        if getattr(self, "design_preview", None) is not None:
            self.design_preview.update({})
        layout = grouped_guidance(error, self.prompt_text.get("1.0", "end-1c"))
        message = layout[0] if layout else english_guidance(format_prompt_problem(self.prompt_text.get("1.0", "end"), error))
        self._set_text(self.preview_text, message)
        count = getattr(error, "missing_count", 0)
        self.prompt_guidance_title.configure(text=(
            "{} detail{} to complete".format(count, "" if count == 1 else "s") if count else "Let's refine this description"
        ))
        requests = getattr(error, "repair_requests", ())
        duration_only = bool(requests) and all(item["role"] == "scenario duration" for item in requests)
        self.prompt_guidance_subtitle.configure(text=(
            "Answer below to fill the highlighted fields, or type directly in your Prompt. Tab / Enter moves to the next field."
            if getattr(error, "code", "") == "placeholders" else
            "Apply the suggested duration, then click Analyze. Your object descriptions stay in place."
            if duration_only else "Fill in missing details or use Replace description below. The original text to replace is shown beside each suggestion."
        ))
        self.prompt_guidance_banner.grid()
        widget = self.preview_text
        c = INKSPACE_COLORS
        widget.configure(font=("Segoe UI", 11), spacing1=2, spacing2=3, spacing3=4, padx=14, pady=14,
                         highlightthickness=0)
        widget.tag_configure("guidance_heading", foreground=c["warning_text"], font=("Segoe UI", 12, "bold"), spacing1=8, spacing3=8)
        widget.tag_configure("guidance_secondary", foreground=c["text_muted"], font=("Segoe UI", 10, "bold"), spacing1=6, spacing3=4)
        widget.tag_configure("guidance_role", foreground=c["brand_hover"], background=c["surface_alt"],
                             font=("Segoe UI", 12, "bold"), spacing1=6, spacing3=6, lmargin1=10)
        widget.tag_configure("guidance_missing", foreground=c["text"], lmargin1=10, lmargin2=24, spacing3=5)
        widget.tag_configure("guidance_context", foreground=c["text_muted"], font=("Segoe UI", 10), lmargin1=8, lmargin2=24)
        widget.tag_configure("guidance_source", foreground=c["text_muted"], font=("Segoe UI", 10), lmargin1=10, lmargin2=10)
        widget.tag_configure("guidance_example", background=c["surface_alt"], foreground=c["text"], font=("Consolas", 11), lmargin1=10, lmargin2=10, spacing1=8, spacing3=8)
        section = "missing"
        for number, line in enumerate(message.splitlines(), 1):
            if layout:
                tag = layout[1][number - 1]
                if tag:
                    widget.tag_add(tag, "{}.0".format(number), "{}.end".format(number))
                continue
            if line.startswith(("Recognized", "Problem clause")):
                section = "context"
            elif line.startswith(("Unrecognized", "Why")):
                section = "missing"
            elif line.startswith("Example structure"):
                section = "example"
            elif line.startswith("Next step"):
                section = "missing"
            if line.startswith(("Please provide", "Please revise", "Recognized", "Example structure", "Next step", "Problem clause", "Unrecognized", "Unfilled template", "Why")):
                tag = "guidance_heading" if section == "missing" else "guidance_context" if section == "context" else "guidance_secondary"
            elif line.startswith("  Description /"):
                tag = "guidance_context"
            elif re.fullmatch(r"(?:ego|vehicle_\d+|obstacle_\d+|pedestrian_\d+):", line):
                tag = "guidance_role"
            elif line.strip():
                tag = "guidance_" + section
            else:
                continue
            widget.tag_add(tag, "{}.0".format(number), "{}.end".format(number))
        if getattr(error, "code", "") == "placeholders":
            self._bind_prompt_diagnostics(error, message, [])
            self._add_prompt_placeholder_answers(error)
        elif self._add_prompt_repair_buttons(error, message) is False:
            return  # Recovery has already rendered working source-bound cards.
        if getattr(error, "code", "") != "placeholders":
            self._collapse_prompt_details()
        widget.see("1.0")
        self._set_text(self.timeline_text, "Complete the details in Review to build the scenario timeline.")
        self.tabs.select(0)
        self.status_var.set("Needs input · Complete the details in Review, then analyze again.")
        detail_count = len(error.placeholder_fields) if getattr(error, "code", "") == "placeholders" else count
        self._record_analysis_result(
            "{} detail{} needed".format(detail_count, "" if detail_count == 1 else "s") if detail_count else "Needs input",
            "warning_text")
        if getattr(error, "code", "") == "placeholders":
            start, end = error.placeholder_fields[0]
            self._focus_prompt_field(start, end)
            self.status_var.set("Type your value · Tab / Enter: next field · Shift+Tab: previous")

    def _add_prompt_repair_buttons(self, error, message, repairs=None):
        from carla_nl.prompt_repairs import build_prompt_repairs, PromptRepairSession
        prompt = self.prompt_text.get("1.0", "end-1c")
        if repairs is None:
            repairs = build_prompt_repairs(prompt, error)
        unresolved = next((item for item in repairs if not item.available), None)
        if unresolved is not None:
            self._show_prompt_recovery(unresolved)
            return False
        placements = []
        search_offsets = {}
        for repair in repairs:
            needle = "\n".join("  " + line.strip() for line in repair.example.splitlines())
            offset = message.find(needle, search_offsets.get(needle, 0))
            if offset < 0:
                addition = "\nSuggested wording\n" + needle + "\n"
                offset = len(message) + addition.index(needle)
                message += addition
                self.preview_text.configure(state="normal")
                self.preview_text.insert("end-1c", addition)
                self.preview_text.configure(state="disabled")
            search_offsets[needle] = offset + len(needle)
            line = message.count("\n", 0, offset + len(needle)) + 1
            placements.append((line, repair))
        self.prompt_repair_session = PromptRepairSession(prompt, [item for _, item in placements])
        if not any(item.recovery for item in repairs):
            self._bind_prompt_diagnostics(error, message, placements)
        widget = self.preview_text
        widget.configure(state="normal")
        # Insert from the bottom so the original report's line indices remain
        # valid. Each control is on its own line, outside the copy-example tag.
        for line, repair in sorted(placements, key=lambda item: item[0], reverse=True):
            widget.insert("{}.end".format(line), "\n")
            index = "{}.0".format(line + 1)
            label = repair.button_label
            button = self.tk.Button(
                widget, text=label, command=lambda item=repair: self._apply_prompt_repair(item),
                state="normal",
                bg=INKSPACE_COLORS["brand_tint"], fg=INKSPACE_COLORS["brand_hover"],
                activebackground=INKSPACE_COLORS["surface_selected"], activeforeground=INKSPACE_COLORS["text"], relief="flat",
                font=("Segoe UI", 10, "bold"), padx=12, pady=7, cursor="hand2",
            )
            widget.window_create(index, window=button, padx=10, pady=5)
            if repair.unrecognized:
                widget.insert("{}.end".format(line + 1), '\n• Revise unrecognized words: "{}".'.format(
                    ' '.join(repair.unrecognized.split())), "guidance_missing")
            if repair.reason:
                from carla_nl.prompt_guidance_layout import english_guidance
                widget.insert("{}.end".format(line + 1), "\n  " + english_guidance(repair.reason), "guidance_context")
            if repair.available and repair.start < repair.end:
                source_line = prompt.count("\n", 0, repair.start) + 1
                end_line = prompt.count("\n", 0, repair.end) + 1
                line_label = str(source_line) if source_line == end_line else "{}–{}".format(source_line, end_line)
                source = ' '.join(prompt[repair.start:repair.end].split())
                widget.insert("{}.end".format(line + 1), '\nReplaces line{} {}: "{}"'.format(
                    "" if source_line == end_line else "s", line_label, source), "guidance_source")
            widget.tag_remove("guidance_example", index, "{}.end".format(line + 1))
            self.prompt_repair_buttons.insert(0, button)
            self.prompt_repair_controls[repair] = button
            from carla_nl.prompt_answers import answer_fields
            if answer_fields(repair.example):
                from carla_nl.prompt_guidance_layout import english_guidance
                requests = getattr(error, "repair_requests", ())
                request = requests[repair.request_id] if repair.request_id < len(requests) else {}
                context = " · ".join(english_guidance(fact) for fact in request.get("recognized", ()))
                example_lines = len(repair.example.splitlines())
                form_index = "{}.0".format(max(1, line - example_lines))
                # Questions, wording, source and replacement controls are all
                # primary actions. Only explanatory context belongs in Details.
                if not repair.unrecognized and not repair.recovery:
                    requests = getattr(error, "repair_requests", ())
                    if repair.request_id < len(requests) and len(requests[repair.request_id].get("fields", ())) == len(answer_fields(repair.example)):
                        role_start = widget.tag_prevrange("guidance_role", form_index)
                        if role_start:
                            missing = widget.tag_ranges("guidance_missing")
                            widget.tag_configure("guidance_form_hint", elide=True)
                            for left, right in zip(missing[::2], missing[1::2]):
                                if widget.compare(left, ">=", role_start[0]) and widget.compare(right, "<=", form_index):
                                    widget.tag_add("guidance_form_hint", str(left) + " linestart", str(right) + " lineend+1c")
                self._insert_prompt_answer_form(repair, form_index, context=context)
        widget.configure(state="disabled")

        if not any(item.recovery for item in repairs) and sum(item.available for item in repairs) > 1:
            self.prompt_repair_all_button.configure(state="normal", text="Apply all templates  ↙")
            self.prompt_repair_all_button.grid(row=0, column=1, sticky="e")

    def _insert_prompt_answer_form(self, repair, index, question=None, context='', field_info=None):
        from carla_nl.prompt_answer_form import PromptAnswerForm
        from carla_nl.prompt_guidance_layout import question_source_context
        widget = self.preview_text
        if repair.available and repair.start < repair.end:
            context = question_source_context(repair.prompt, repair.start, repair.end, field=field_info is not None)
        form = PromptAnswerForm(widget, repair.example, INKSPACE_COLORS,
            lambda answers, item=repair: self._apply_prompt_repair(item, answers=answers),
            auto_choice=not bool(repair.unrecognized or repair.reason), question=question, context=context,
            field_info=field_info, prompt=repair.prompt)
        widget.insert(index, "\n")
        widget.window_create(index, window=form, padx=10, pady=3, stretch=True)
        for tag in widget.tag_names():
            if tag.startswith(("guidance_", "diagnostic_link_")):
                widget.tag_remove(tag, index, str(index) + " lineend+1c")
        self.prompt_answer_forms[repair] = form

    def _add_prompt_placeholder_answers(self, error):
        from carla_nl.prompt_answers import answer_fields
        from carla_nl.prompt_repairs import PromptRepair, PromptRepairSession
        prompt = self.prompt_text.get("1.0", "end-1c")
        repairs = []
        fields = answer_fields(prompt)
        for field in fields:
            token = prompt[field.start:field.end]
            repairs.append(PromptRepair(prompt, "field", token, field.start, field.end,
                                        token, source_clause=token, recovery=True))
        self.prompt_repair_session = PromptRepairSession(prompt, repairs)
        widget = self.preview_text
        widget.configure(state="normal")
        roles = widget.tag_ranges("guidance_role")[::2]
        for repair, heading, field in reversed(list(zip(repairs, roles, fields))):
            index = widget.index(str(heading) + " lineend+1c")
            widget.tag_configure("guidance_form_hint", elide=True)
            widget.tag_add("guidance_form_hint", index, index + " lineend+1c")
            self._insert_prompt_answer_form(repair, index, field_info=field)
        widget.configure(state="disabled")

    def _show_prompt_recovery(self, original=None):
        """An editing route independent of parsing and diagnostic formatting."""
        from carla_nl.prompt_recovery import build_recovery_repairs
        prompt = self.prompt_text.get("1.0", "end-1c")
        repairs = build_recovery_repairs(prompt, original)
        self.converted = []
        self._set_text(self.json_text, "")
        if getattr(self, "design_preview", None) is not None:
            try:
                self.design_preview.update({})
            except Exception as exc:
                write_exception(exc)
                self.design_preview._has_data = False
        rows = ["Repair your description", "Your draft is kept. Each button replaces only the description shown above it.",
                "Check the suggested meaning, fill <fields>, then Analyze. Ctrl+Z restores the original."]
        for number, repair in enumerate(repairs, 1):
            line = prompt.count("\n", 0, repair.start) + 1
            rows += ["", "{:02d} · Line {}".format(number, line),
                     "Original: " + (repair.source_clause or "Empty description"), "Suggested wording"]
            rows.extend("  " + part.strip() for part in repair.example.splitlines())
        message = "\n".join(rows)
        self._set_text(self.preview_text, message)
        self.preview_text.configure(font=("Segoe UI", 11), padx=14, pady=14, spacing3=6)
        c = INKSPACE_COLORS
        self.preview_text.tag_configure("guidance_example", background=c["surface_alt"], foreground=c["text"],
                                        font=("Consolas", 11), lmargin1=10, lmargin2=10)
        self.preview_text.tag_configure("guidance_role", foreground=c["brand_hover"], font=("Segoe UI", 12, "bold"))
        for number, line in enumerate(message.splitlines(), 1):
            tag = "guidance_example" if line.startswith("  ") else "guidance_role" if re.match(r"\d+ · Line", line) else None
            if tag:
                self.preview_text.tag_add(tag, "{}.0".format(number), "{}.end".format(number))
        self._add_prompt_repair_buttons(None, message, repairs=repairs)
        self.prompt_guidance_title.configure(text="Replacement available")
        self.prompt_guidance_subtitle.configure(text="Use the button for the description you want to repair. No text selection needed.")
        self.prompt_guidance_banner.grid()
        self._set_text(self.timeline_text, "Repair the description and Analyze before inspecting the timeline.")
        self.tabs.select(0)
        self.preview_text.see("1.0")
        self.status_var.set("Draft preserved · Replace a description, fill its fields, then Analyze.")
        self._record_analysis_result("Repair available" if prompt.strip() else "Needs input", "warning_text")

    def _collapse_prompt_details(self):
        """Keep context in the report, with one disclosure per description."""
        widget = self.preview_text
        roles = widget.tag_ranges("guidance_role")
        starts = [str(index) for index in roles[::2]] or ["1.0"]
        bounds = list(zip(starts, starts[1:] + [widget.index("end-1c")]))
        context = []
        for tag in ("guidance_context",):
            ranges = widget.tag_ranges(tag)
            context.extend(zip(ranges[::2], ranges[1::2]))
        widget.configure(state="normal")
        for number in reversed(range(len(bounds))):
            first, last = bounds[number]
            lines = set()
            for start, end in context:
                if widget.compare(start, ">=", last) or widget.compare(end, "<=", first):
                    continue
                start = widget.index(first if widget.compare(start, "<", first) else start)
                end = widget.index(last if widget.compare(end, ">", last) else end)
                left, right = int(start.split('.')[0]), int(end.split('.')[0])
                if widget.compare(start, "==", "{}.end".format(left)):
                    left += 1  # context starts at the newline after a button
                if end.endswith('.0'):
                    right -= 1
                lines.update(range(left, right + 1))
            # Never elide a whole line containing an embedded action or source
            # preview, even when Tk inherited an adjacent context tag.
            protected = {int(index.split('.')[0]) for kind, value, index in
                         widget.dump(first, last, window=True) if kind == 'window'}
            for tag in ("guidance_source", "guidance_example", "guidance_secondary"):
                tagged = widget.tag_ranges(tag)
                for left, right in zip(tagged[::2], tagged[1::2]):
                    protected.update(range(int(str(left).split('.')[0]), int(str(right).split('.')[0]) + 1))
            lines.difference_update(protected)
            if not lines:
                continue
            # Hidden context should not leave several empty rows before the
            # questions. Preserve embedded controls and visible source lines.
            for line in list(lines):
                for step in (-1, 1):
                    adjacent = line + step
                    while (int(first.split('.')[0]) <= adjacent < int(last.split('.')[0])
                           and adjacent not in protected
                           and not widget.get(f"{adjacent}.0", f"{adjacent}.end").strip()):
                        lines.add(adjacent)
                        adjacent += step
            body = "guidance_detail_body_{}".format(number)
            toggle = "guidance_detail_toggle_{}".format(number)
            widget.tag_configure(body, elide=True)
            for line in sorted(lines):
                widget.tag_add(body, "{}.0".format(line), "{}.0".format(line + 1))
            widget.tag_configure(toggle, foreground=INKSPACE_COLORS["brand_hover"],
                                 font=("Segoe UI", 10), spacing1=4, spacing3=6)
            widget.insert(last, "\n▸ Details\n", (toggle,))
            # Disclosures change presentation, not the source-editor focus.
            for tag in self.prompt_diagnostic_links:
                for a, b in zip(widget.tag_ranges(toggle)[::2], widget.tag_ranges(toggle)[1::2]):
                    widget.tag_remove(tag, a, b)
            widget.tag_bind(toggle, "<Button-1>", lambda _event, key=number: self._toggle_prompt_details(key))
            widget.tag_bind(toggle, "<Enter>", lambda _event: widget.configure(cursor="hand2"))
            widget.tag_bind(toggle, "<Leave>", lambda _event: widget.configure(cursor="xterm"))
            self.prompt_detail_sections[number] = (body, toggle)
        self.prompt_detail_sections = dict(sorted(self.prompt_detail_sections.items()))
        widget.configure(state="disabled")

    def _toggle_prompt_details(self, key):
        widget = self.preview_text
        body, toggle = self.prompt_detail_sections[key]
        collapsed = str(widget.tag_cget(body, "elide")) == "1"
        widget.tag_configure(body, elide=not collapsed)
        start, end = widget.tag_ranges(toggle)
        widget.configure(state="normal")
        widget.delete(start, end)
        widget.insert(start, "\n▾ Hide details\n" if collapsed else "\n▸ Details\n", (toggle,))
        widget.configure(state="disabled")
        return "break"

    def _bind_prompt_diagnostics(self, error, message, placements):
        """Tag report ranges before embedded buttons shift their text indices."""
        widget = self.preview_text
        prompt = self.prompt_text.get("1.0", "end-1c")
        self._prompt_diagnostic_snapshot = prompt
        self.prompt_diagnostic_links = {}

        def unique_span(text, left=0, right=None):
            if not text:
                return None
            pattern = r"\s+".join(re.escape(part) for part in text.split())
            hits = list(re.finditer(pattern, prompt[left:right], re.I))
            return (left + hits[0].start(), left + hits[0].end()) if len(hits) == 1 else None

        def bind(start, end, span, repair=None, field=False):
            tag = "diagnostic_link_{}".format(len(self.prompt_diagnostic_links))
            self.prompt_diagnostic_links[tag] = (span, repair, field)
            widget.tag_add(tag, start, end)
            widget.tag_bind(tag, "<Button-1>", lambda _event, key=tag: self._focus_prompt_diagnostic(key))
            widget.tag_bind(tag, "<Enter>", lambda _event: widget.configure(cursor="hand2"))
            widget.tag_bind(tag, "<Leave>", lambda _event: widget.configure(cursor="xterm"))

        if getattr(error, "code", "") == "placeholders":
            roles = widget.tag_ranges("guidance_role")
            for index, span in enumerate(error.placeholder_fields):
                start = roles[index * 2]
                end = roles[(index + 1) * 2] if (index + 1) * 2 < len(roles) else "end-1c"
                bind(start, end, span, field=True)
            return

        for line, repair in placements:
            span = (repair.start, repair.end) if repair.available and repair.start < repair.end else None
            if span is None:
                span = unique_span(repair.source_clause) or unique_span(repair.unrecognized)
            heading = widget.tag_prevrange("guidance_role", "{}.end".format(line))
            start = heading[0] if heading else "1.0"
            bind(start, "{}.end".format(line), span, repair=repair)
            unknown = unique_span(repair.unrecognized, *span) if span else None
            if unknown:
                self.prompt_text.tag_add("prompt_unrecognized", "1.0+{}c".format(unknown[0]), "1.0+{}c".format(unknown[1]))
        if not placements:
            span = unique_span(getattr(error, "clause", ""))
            unknown = unique_span(getattr(error, "unrecognized", ""), *(span or (0, None)))
            if unknown:
                self.prompt_text.tag_add("prompt_unrecognized", "1.0+{}c".format(unknown[0]), "1.0+{}c".format(unknown[1]))
            bind("1.0", "end-1c", span or unknown)
        self.prompt_text.tag_raise("prompt_unrecognized")

    def _focus_prompt_diagnostic(self, key):
        widget = self.prompt_text
        widget.tag_remove("prompt_problem_focus", "1.0", "end")
        entry = getattr(self, "prompt_diagnostic_links", {}).get(key)
        if entry is None:
            return "break"
        span, repair, field = entry
        current = widget.get("1.0", "end-1c")
        if current != self._prompt_diagnostic_snapshot:
            session = getattr(self, "prompt_repair_session", None)
            pending = session.pending.get(repair) if session is not None else None
            if session is None or current != session.prompt or pending is None or not pending.available:
                self.status_var.set("This description has changed. Click Analyze to refresh its location.")
                return "break"
            span = (pending.start, pending.end)
        if span is None or span[0] == span[1]:
            self.status_var.set("Apply the suggested wording to locate its source automatically or add the missing definition.")
            return "break"
        if field:
            self._focus_prompt_field(*span)
        else:
            start, end = ("1.0+{}c".format(offset) for offset in span)
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_remove("design_link", "1.0", "end")
            widget.tag_add("prompt_problem_focus", start, end)
            widget.tag_raise("prompt_problem_focus")
            widget.tag_raise("prompt_field")
            widget.tag_raise("sel")
            widget.mark_set("insert", start)
            widget.see(start)
            widget.focus_set()
            self.status_var.set("Source highlighted · Edit this description or apply its suggested wording.")
        return "break"

    def _apply_all_prompt_repairs(self):
        for repair in list(self.prompt_repair_controls):
            pending = self.prompt_repair_session.pending.get(repair)
            if pending is not None and pending.available:
                if not self._apply_prompt_repair(repair):
                    break

    def _apply_prompt_repair(self, repair, answers=None):
        try:
            return self._apply_prompt_repair_checked(repair, answers)
        except Exception as exc:
            write_exception(exc)
            self._show_prompt_recovery(repair)
            return False

    def _apply_prompt_repair_checked(self, repair, answers=None):
        widget = self.prompt_text
        current = widget.get("1.0", "end-1c")
        original = repair
        try:
            repair = self.prompt_repair_session.apply_automatic(original, current, answers=answers)
        except ValueError:
            if original.recovery:
                self._show_prompt_recovery(original)
            else:
                self.preview()
            return False
        except Exception as exc:
            write_exception(exc)
            self._show_prompt_recovery(original)
            return False
        auto_separators = widget.cget("autoseparators")
        widget.edit_separator()
        widget.configure(autoseparators=False)
        start = "1.0+{}c".format(repair.start)
        try:
            widget.replace(start, "1.0+{}c".format(repair.end), repair.replacement)
        except self.tk.TclError as exc:
            write_exception(exc)
            self.prompt_repair_session.restore(current)
            self._show_prompt_recovery(original)
            return False
        finally:
            widget.configure(autoseparators=auto_separators)
            widget.edit_separator()
        self.converted = []
        self._set_text(self.json_text, "")
        if getattr(self, "design_preview", None) is not None:
            self.design_preview.update({})
        self._set_text(self.timeline_text, "Fill the highlighted fields, then click Analyze.")
        self._highlight_prompt_syntax()
        placeholder = re.search(r"<[^<>]+>", repair.replacement)
        cursor = repair.start + (placeholder.start() if placeholder else len(repair.replacement))
        self._focus_prompt_field(cursor, cursor + len(placeholder[0]) if placeholder else cursor)
        self._show_prompt_replacement_highlight(repair.start, repair.start + len(repair.replacement))
        self._refresh_prompt_repair_controls()
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        draft = widget.get("1.0", "end-1c")
        if not self.prompt_repair_session.pending and not prompt_placeholder_fields(draft):
            # Run once after Tk finishes this atomic edit. A newer keystroke or
            # Undo cancels this check rather than validating an obsolete draft.
            self.top.after_idle(lambda: self._verify_completed_prompt_repair(draft))
        return True

    def _refresh_prompt_undo(self):
        widget = self.prompt_text
        available = widget.tk.getboolean(widget.tk.call(widget._w, "edit", "canundo"))
        self.prompt_undo_button.configure(state="normal" if available else "disabled")

    def _undo_prompt_edit(self):
        """Use native edit order, including typing performed after a replacement."""
        try:
            self.prompt_text.edit_undo()
        except self.tk.TclError:
            self._refresh_prompt_undo()
            return
        self._clear_prompt_replacement_highlight()
        self._on_prompt_modified()
        self.prompt_text.focus_set()
        self.prompt_text.see("insert")
        self.status_var.set("Last edit undone · Analyze to check this version. Ctrl+Y to redo.")

    def _on_prompt_widget_destroy(self, event):
        # Tk removes Python/Tcl command names on widget destruction, but leaves
        # after/after_idle jobs queued. A later callback can reuse the Python
        # ID in that name, turning an old timer into an unrelated menu action.
        # Destroy also propagates through this toplevel's child bindtags.
        widget = event.widget
        commands = set(getattr(widget, "_tclCommands", ()) or ())
        if commands:
            for job in self.top.tk.call("after", "info"):
                try:
                    script = self.top.tk.call("after", "info", job)[0]
                    command = self.top.tk.splitlist(script)[0]
                    if command in commands:
                        widget.after_cancel(job)
                except self.tk.TclError:
                    pass  # Another child cleanup may already have cancelled it.
        if widget is self.top:
            self._clear_prompt_replacement_highlight()

    def _clear_prompt_replacement_highlight(self):
        timer = getattr(self, "_prompt_replacement_after", None)
        if timer is not None:
            self.top.after_cancel(timer)
        self._prompt_replacement_after = None
        self._prompt_replacement_snapshot = None
        if self.prompt_text.winfo_exists():
            self.prompt_text.tag_remove("prompt_replaced", "1.0", "end")
        if self.status_var.get().startswith("Replacement highlighted"):
            self.status_var.set(self.status_var.get().replace("Replacement highlighted", "Description updated", 1))

    def _show_prompt_replacement_highlight(self, start, end):
        self._clear_prompt_replacement_highlight()
        widget = self.prompt_text
        widget.tag_add("prompt_replaced", "1.0+{}c".format(start), "1.0+{}c".format(end))
        widget.tag_raise("prompt_replaced")
        for tag in ("prompt_unrecognized", "prompt_field", "sel"):
            widget.tag_raise(tag)
        self._prompt_replacement_snapshot = widget.get("1.0", "end-1c")
        self._prompt_replacement_after = self.top.after(3000, self._clear_prompt_replacement_highlight)
        self._refresh_prompt_undo()

    def _verify_completed_prompt_repair(self, draft):
        if self.top.winfo_exists() and self.prompt_text.get("1.0", "end-1c") == draft:
            self.preview()

    def _refresh_prompt_repair_controls(self):
        session = self.prompt_repair_session
        from carla_nl.prompt_answers import answer_fields
        from carla_nl.prompt_guidance_layout import question_source_context
        prompt = self.prompt_text.get("1.0", "end-1c")
        fields = {field.start: field for field in answer_fields(prompt)}
        for item, form in getattr(self, "prompt_answer_forms", {}).items():
            form.set_applied(item in session.applied)
            pending = session.pending.get(item)
            if pending is not None and pending.available and not pending.needs_refresh:
                form.refresh_number_hints(prompt, fields.get(pending.start) if item.role == "field" else None)
                if pending.start < pending.end:
                    form.context_label.configure(text=question_source_context(prompt, pending.start, pending.end, field=item.role == "field"))
        for item, button in self.prompt_repair_controls.items():
            label = item.button_label
            button.configure(state="normal" if item in session.pending else "disabled",
                             text="✓ Applied to prompt" if item in session.applied else label,
                             disabledforeground=INKSPACE_COLORS["success"] if item in session.applied else INKSPACE_COLORS["text_subtle"])
        remaining = len(session.pending)
        located = sum(item.available and not item.recovery for item in session.pending.values())
        self.prompt_repair_all_button.configure(state="normal" if located else "disabled")
        self.prompt_guidance_title.configure(text="{} description{} updated".format(
            len(session.applied), "" if len(session.applied) == 1 else "s") if session.applied else "Change undone")
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        has_fields = bool(prompt_placeholder_fields(self.prompt_text.get("1.0", "end-1c")))
        next_step = ("Type into the selected <field>. Tab / Enter moves to the next; Shift+Tab goes back." if has_fields else
                     "Complete the remaining questions below or use Replace description." if remaining else
                     "Review the updated prompt, then click Analyze.")
        self.prompt_guidance_subtitle.configure(text=next_step + (" {} more item{} to review below.".format(
            remaining, "" if remaining == 1 else "s") if remaining else " Then click Analyze." if has_fields else ""))
        highlighted = bool(self.prompt_text.tag_ranges("prompt_replaced"))
        feedback = "Replacement highlighted" if highlighted else "Earlier draft restored"
        next_action = ("Tab / Enter: next field" if has_fields else "Complete remaining questions" if remaining else
                       "Verifying completed repairs" if highlighted else "Analyze to check this version")
        self.status_var.set(feedback + " · " + next_action + " · Undo or Ctrl+Z restores the last edit")

    def _focus_prompt_field(self, start, end):
        widget = self.prompt_text
        widget.tag_remove("sel", "1.0", "end")
        index = "1.0+{}c".format(start)
        widget.mark_set("insert", index)
        if end > start:
            widget.configure(selectbackground=INKSPACE_COLORS["brand"],
                             selectforeground=INKSPACE_COLORS["on_accent"],
                             inactiveselectbackground=INKSPACE_COLORS["brand"])
            widget.tag_add("sel", index, "1.0+{}c".format(end))
            widget.tag_raise("sel")
            if self._prompt_field_at(index) is not None:
                self._prompt_field_navigation_active = True
                widget.mark_set("prompt_field_entry_start", index)
                widget.mark_gravity("prompt_field_entry_start", "left")
                widget.mark_set("prompt_field_entry_end", "1.0+{}c".format(end))
                widget.mark_gravity("prompt_field_entry_end", "right")
        widget.see(index)
        widget.focus_set()

    def _append_prompt_template(self, snippet):
        """A menu insertion must never delete the word under the caret."""
        widget = self.prompt_text
        current = widget.get("1.0", "end-1c")
        widget.edit_separator()
        widget.insert("end-1c", ("\n" if current and not current.endswith("\n") else "") + snippet)
        widget.edit_separator()
        widget.mark_set("insert", "end-1c")
        widget.see("insert")
        widget.focus_set()
        self._highlight_prompt_syntax()

    def _toggle_prompt_timeline(self):
        if self.timeline_text.frame.winfo_manager():
            self.timeline_text.grid_remove()
            self.timeline_toggle.configure(text="▸ Scenario timeline")
        else:
            self.timeline_text.grid()
            self.timeline_toggle.configure(text="▾ Scenario timeline")

    def _next_prompt_field(self, reverse=False):
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        widget = self.prompt_text
        fields = prompt_placeholder_fields(widget.get("1.0", "end-1c"))
        if not fields:
            return
        selected = bool(widget.tag_ranges("sel"))
        index = "sel.first" if selected else "insert"
        offset = len(widget.get("1.0", index))
        if reverse:
            field = next((field for field in reversed(fields) if field.start() < offset), fields[-1])
        else:
            field = next((field for field in fields if (field.start() > offset if selected else field.end() > offset)), fields[0])
        self._focus_prompt_field(field.start(), field.end())

    def _on_prompt_field_key(self, event, reverse=False):
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        if event.state & 0x4:  # Preserve normal Ctrl+Tab focus traversal.
            return
        widget = self.prompt_text
        active = getattr(self, "_prompt_field_navigation_active", False)
        in_field = (active and widget.compare("insert", ">=", "prompt_field_entry_start")
                    and widget.compare("insert", "<=", "prompt_field_entry_end"))
        if event.keysym == "Return" and not in_field:
            return  # Ordinary multiline authoring keeps its Enter key.
        fields = prompt_placeholder_fields(widget.get("1.0", "end-1c"))
        if fields:
            widget.edit_separator()
            self._next_prompt_field(reverse=reverse)
            return "break"
        if in_field:
            widget.edit_separator()
            self._prompt_field_navigation_active = False
            self.preview()
            return "break"

    def _prompt_field_at(self, index):
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        widget = self.prompt_text
        offset = len(widget.get("1.0", index))
        return next((field for field in prompt_placeholder_fields(widget.get("1.0", "end-1c"))
                     if field.start() <= offset < field.end()), None)

    def _on_prompt_field_click(self, event):
        widget = self.prompt_text
        index = widget.index("@{},{}".format(event.x, event.y))
        field = self._prompt_field_at(index)
        if field is not None:
            self._hide_prompt_tooltip()
            self._focus_prompt_field(field.start(), field.end())
            return "break"  # Keep Text's default click from collapsing selection.
        self._prompt_field_navigation_active = False

    def preview(self):
        try:
            return self._preview_checked()
        except Exception as exc:
            write_exception(exc)
            self._show_prompt_recovery()
            return False

    def _preview_checked(self):
        # A failed edit must not leave the previous successful JSON presented
        # as the result of the new prompt.
        self.converted = []
        self.analysis_state_var.set("Analyzing…")
        self._set_text(self.json_text, "")
        try:
            preview, converted = self._convert()
        except ValueError as exc:
            if getattr(self, "design_preview", None) is not None:
                self.design_preview.update({})
            self._show_prompt_guidance(exc)
            return False
        if not getattr(preview, "ok", False):
            if getattr(self, "design_preview", None) is not None:
                self.design_preview.update({})
            message = getattr(preview, "message", "Prompt could not be converted.")
            self._show_prompt_guidance(ValueError(message))
            self._set_text(self.json_text, "")
            return False
        self.converted = converted
        self._prompt_field_navigation_active = False
        self._set_text(self.preview_text, self._preview_report(preview, converted))
        self.preview_text.configure(font=("Segoe UI", 11), padx=14, pady=14, spacing1=2, spacing2=3, spacing3=5)
        self.preview_text.tag_configure("guidance_heading", foreground=INKSPACE_COLORS["success"], font=("Segoe UI", 14, "bold"), spacing3=8)
        self.preview_text.tag_add("guidance_heading", "1.0", "1.end")
        for number, line in enumerate(self.preview_text.get("1.0", "end-1c").splitlines(), 1):
            if line == "Warnings":
                self.preview_text.tag_configure("guidance_warning", foreground=INKSPACE_COLORS["warning_text"], font=("Segoe UI", 11, "bold"))
                self.preview_text.tag_add("guidance_warning", "{}.0".format(number), "{}.end".format(number))
        self._set_text(self.json_text, self._json_report(converted))
        self._update_design_preview(converted)
        self._record_analysis_result("Ready", "success")
        self.tabs.select(self.design_frame if self.design_preview is not None else 0)
        self.status_var.set(
            "Analysis complete: {} scenario(s). Review the details, then click {}.".format(
                len(converted),
                self.save_button_text,
            )
        )
        return True

    def _configure_prompt_syntax_tags(self):
        """Shared reading categories; emphasize actors, actions and scene headers."""
        widget = self.prompt_text
        for category, color in PROMPT_SYNTAX_COLORS.items():
            tag = "syn_" + category
            widget.tag_configure(tag, foreground=color)
            if category in ("ego", "vehicle", "obstacle", "pedestrian", "action", "alert", "op", "kw"):
                widget.tag_configure(tag, font=("Consolas", 13, "bold"))
            # hover reminder + click-to-Design linkage on the coloured block
            widget.tag_bind(tag, "<Motion>", self._on_prompt_token_hover)
            widget.tag_bind(tag, "<Leave>", lambda _event: self._hide_prompt_tooltip())
            widget.tag_bind(tag, "<Button-1>", self._on_prompt_token_click)
        widget.tag_configure("prompt_field", **PROMPT_FIELD_COLORS,
                             font=("Consolas", 13, "bold"), underline=True)
        widget.tag_configure("prompt_unrecognized", foreground=INKSPACE_COLORS["danger"],
                             background=INKSPACE_COLORS["danger_soft"], underline=True,
                             underlinefg=INKSPACE_COLORS["danger"])
        widget.tag_configure("prompt_problem_focus", background=INKSPACE_COLORS["brand_tint"])
        widget.tag_configure("prompt_replaced", background="#28433f")

    def _prompt_token_at(self, index):
        """Return (category, start_index, end_index, token) at a text index, or None."""
        widget = getattr(self, "prompt_text", None)
        if widget is None:
            return None
        for category in PROMPT_SYNTAX_COLORS:
            rng = widget.tag_prevrange("syn_" + category, "{}+1c".format(index))
            if rng and widget.compare(rng[0], "<=", index) and widget.compare(index, "<", rng[1]):
                return category, rng[0], rng[1], widget.get(rng[0], rng[1])
        return None

    def _on_prompt_token_hover(self, event):
        widget = getattr(self, "prompt_text", None)
        if widget is None:
            return
        index = widget.index("@{},{}".format(event.x, event.y))
        hit = self._prompt_token_at(index)
        if hit is None:
            self._hide_prompt_tooltip()
            return
        category, _start, _end, token = hit
        label = PROMPT_SYNTAX_TOOLTIP.get(category, category)
        if category in ("time", "gap"):
            from carla_nl.prompt_time_hints import time_hint_at
            offset = len(widget.get("1.0", index))
            hint = time_hint_at(widget.get("1.0", "end-1c"), offset)
            if hint is not None:
                link = "Ctrl+click to inspect t={:g}s in Design.".format(hint.jump_time_s) if hint.jump_time_s is not None else "Ctrl+click to inspect in Design; the timeline stays at its current time."
                self._show_prompt_tooltip("{}\n{}\n{}\nAnalyze checks the complete description.".format(
                    hint.title, hint.description, link), event.x_root, event.y_root)
                return
        # Timed point pairs: clarify WHEN (left) vs target value (right).
        line_no, click_col = (int(part) for part in index.split("."))
        line_text = widget.get("{}.0".format(line_no), "{}.0 lineend".format(line_no))
        points_match = _PROMPT_POINTS_LINE_RE.search(line_text)
        if points_match:
            for pair in _PROMPT_POINT_PAIR_RE.finditer(line_text):
                if pair.start(1) <= click_col < pair.end(1):
                    label = "Time — when this change applies (scenario t)"
                    break
                if pair.start(3) <= click_col < pair.end(3):
                    if points_match.group(1).lower().startswith("speed"):
                        label = "Speed — new target speed from this t"
                    else:
                        label = "Gap — new target time gap from this t"
                    break
        self._show_prompt_tooltip(
            "{}\n{}\n(Ctrl+click to inspect in Design)".format(label, token), event.x_root, event.y_root
        )

    def _on_prompt_token_click(self, event):
        """Ctrl+click inspects a token; ordinary clicks only move the caret."""
        if not (getattr(event, "state", 0) & 0x0004):
            return
        widget = getattr(self, "prompt_text", None)
        if widget is None:
            return
        index = widget.index("@{},{}".format(event.x, event.y))
        if self._prompt_field_at(index) is not None:
            return
        hit = self._prompt_token_at(index)
        if hit is None:
            return
        category, _start, _end, token = hit
        hint = None
        if category in ("time", "gap"):
            from carla_nl.prompt_time_hints import time_hint_at
            hint = time_hint_at(widget.get("1.0", "end-1c"), len(widget.get("1.0", index)))
        line_no = int(index.split(".")[0])
        line_text = widget.get("{}.0".format(line_no), "{}.0 lineend".format(line_no))

        def first_float(text):
            match = re.search(r"(\d+(?:\.\d+)?)", text or "")
            return float(match.group(1)) if match else None

        role = None
        jump_t = None
        role_match = NUMBERED_VEHICLE_RE.search(line_text)
        if re.match(r"\s*ego\b", line_text, re.I):
            role = "ego"
        elif role_match:
            role = "v" + role_match.group(1)
        line_t_match = re.search(rf"\bt\s*=\s*(\d+(?:\.\d+)?)(?:\s*{SECONDS_UNIT})?\b", line_text, re.I)
        line_t = float(line_t_match.group(1)) if line_t_match else None

        # Timed point pairs (time_gap_points / speed_points): the two sides of a
        # pair link differently. Left (time) -> jump to that t; right (value) ->
        # select the vehicle AND jump to the pair's t (when the value applies).
        pair_hit = None
        if _PROMPT_POINTS_LINE_RE.search(line_text):
            click_col = int(index.split(".")[1])
            for pair in _PROMPT_POINT_PAIR_RE.finditer(line_text):
                if pair.start(1) <= click_col < pair.end(1):
                    pair_hit = ("left", pair)
                    break
                if pair.start(3) <= click_col < pair.end(3):
                    pair_hit = ("right", pair)
                    break

        if pair_hit is not None:
            side, pair = pair_hit
            jump_t = first_float(pair.group(1))
            if side == "right":
                pass  # keep the line's role selected too -> select vehicle + jump
            else:
                role = None  # pure time click: just scrub the timeline
        elif hint is not None:
            jump_t = hint.jump_time_s
        elif category == "ego":
            role = "ego"
        elif category == "vehicle":
            token_match = NUMBERED_VEHICLE_RE.search(token)
            if token_match:
                role = "v" + token_match.group(1)
        elif category == "pedestrian":
            match = PEDESTRIAN_NAME_RE.fullmatch(unicodedata.normalize('NFKC', token))
            role = 'pedestrian_' + match[1] if match and match[1] else None
        elif category == "obstacle":
            match = OBSTACLE_NAME_RE.fullmatch(unicodedata.normalize('NFKC', token))
            role = 'obstacle_' + match[1] if match and match[1] else None
        elif category == "time":
            jump_t = first_float(token) if any(ch.isdigit() for ch in token) else line_t
        elif category in ("lane", "speed", "gap", "op"):
            jump_t = line_t  # timeline rows: jump to the event time when the line has one
        self._design_apply_link(category, token, role=role, jump_t=jump_t)

    def _design_apply_link(self, category, token, role=None, jump_t=None):
        preview = getattr(self, "design_preview", None)
        if preview is None:
            return
        if not getattr(preview, "_has_data", False):
            self._auto_refresh_design()
        if not getattr(preview, "_has_data", False):
            self.status_var.set("Design link: analyze the prompt first (click Analyze).")
            return
        model = getattr(preview, "model", {}) or {}
        if category == 'obstacle':
            candidates = [(i, event) for i, event in enumerate(model.get('events', []))
                          if event.get('category') == 'obstacle'
                          and event.get('label', '').endswith(' appears')
                          and (not role or event['label'] == role + ' appears')]
            if len(candidates) == 1:
                self.tabs.select(self.design_frame)
                preview._embed_select_event(candidates[0][0])
            else:
                self.status_var.set('Analyze the current obstacle description, then click its number to inspect it.')
            return
        if category == 'pedestrian':
            candidates = [(i, event) for i, event in enumerate(model.get('events', []))
                          if event.get('category') == 'pedestrian'
                          and event.get('label', '').endswith(' crossing')
                          and (not role or event['label'] == role + ' crossing')]
            if len(candidates) == 1:
                self.tabs.select(self.design_frame)
                preview._embed_select_event(candidates[0][0])
            else:
                self.status_var.set('Analyze the current pedestrian description to inspect its crossing.')
            return
        # category-specific fallbacks that need the visual model
        if jump_t is None and category == "alert":
            decision_events = [e for e in model.get("events", []) if e.get("category") == "decision"]
            if decision_events:
                jump_t = float(decision_events[0].get("time_s", 0.0))
        applied = []
        if role:
            design_role = "vehicle_" + role[1:] if re.match(r"^v\d+$", role) else role
            if any(v.get("role") == design_role for v in model.get("vehicles", [])):
                preview.selected_vehicle_role = design_role
                applied.append(design_role)
        if jump_t is not None:
            total = max(0.1, float(model.get("total_time_s", 0.1)))
            jump_t = max(0.0, min(total, float(jump_t)))
            preview.current_t = jump_t
            try:
                preview.time_var.set(jump_t)
            except Exception:
                pass
            applied.append("t={:.1f}s".format(jump_t))
        try:
            preview._redraw_all()
        except Exception as exc:
            write_exception(exc)
        try:
            self.tabs.select(self.design_frame)
        except Exception:
            pass
        if applied:
            self.status_var.set("Design ⇠ {}: {}".format(token.strip(), ", ".join(applied)))
        else:
            self.status_var.set("Design opened for '{}'.".format(token.strip()))

    def _show_prompt_tooltip(self, text, x_root, y_root):
        tip = getattr(self, "_prompt_tooltip", None)
        if tip is None:
            tip = self.tk.Toplevel(self.top)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            label = self.tk.Label(
                tip,
                justify="left",
                bg=INKSPACE_COLORS["surface_alt"],
                fg=INKSPACE_COLORS["text"],
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 10),
                wraplength=420,
                padx=12,
                pady=10,
            )
            label.pack()
            self._prompt_tooltip = tip
            self._prompt_tooltip_label = label
        self._prompt_tooltip_label.configure(text=text)
        try:
            self._prompt_tooltip.update_idletasks()
            left, top = self.top.winfo_rootx(), self.top.winfo_rooty()
            right = left + self.top.winfo_width() - self._prompt_tooltip.winfo_reqwidth() - 8
            bottom = top + self.top.winfo_height() - self._prompt_tooltip.winfo_reqheight() - 8
            x = max(left + 8, min(int(x_root) + 14, right))
            y = max(top + 8, min(int(y_root) + 18, bottom))
            self._prompt_tooltip.wm_geometry("+{}+{}".format(x, y))
            self._prompt_tooltip.deiconify()
        except Exception:
            pass

    def _hide_prompt_tooltip(self):
        tip = getattr(self, "_prompt_tooltip", None)
        if tip is not None:
            try:
                tip.withdraw()
            except Exception:
                pass

    def _highlight_prompt_syntax(self):
        """Highlight prompt tokens by semantic category (mirrors the legend palette)."""
        widget = getattr(self, "prompt_text", None)
        if widget is None:
            return
        for category in PROMPT_SYNTAX_COLORS:
            widget.tag_remove("syn_" + category, "1.0", "end")
        text = widget.get("1.0", "end-1c")
        if hasattr(self, "prompt_empty_hint"):
            if text:
                self.prompt_empty_hint.place_forget()
            else:
                self.prompt_empty_hint.place(x=18, y=18, relwidth=0.90)
        for line_index, original_line in enumerate(text.split("\n"), start=1):
            # Read fullwidth text without editing it. Compatibility characters
            # may expand, so keep source offsets rather than assuming equal length.
            normalized = [unicodedata.normalize("NFKC", char) for char in original_line]
            line = "".join(normalized)
            source_offsets = [index for index, part in enumerate(normalized) for _char in part]
            claimed = [False] * len(line)
            for quoted in re.finditer(r'"(?:\\.|[^"\\])*"', line):
                claimed[quoted.start():quoted.end()] = [True] * len(quoted.group())

            def _claim(category, start, end):
                if start >= end or any(claimed[start:end]):
                    return
                for index in range(start, end):
                    claimed[index] = True
                widget.tag_add("syn_" + category, "{}.{}".format(line_index, source_offsets[start]),
                               "{}.{}".format(line_index, source_offsets[end - 1] + 1))

            # Timed point pairs first: left side = WHEN (time), right side = target value.
            points_match = _PROMPT_POINTS_LINE_RE.search(line)
            if points_match:
                value_category = "speed" if points_match.group(1).lower().startswith("speed") else "gap"
                for pair in _PROMPT_POINT_PAIR_RE.finditer(line):
                    _claim("time", pair.start(1), pair.end(1))
                    _claim(value_category, pair.start(3), pair.end(3))
            for category, rule in _PROMPT_CATEGORY_RULES:
                for match in rule.finditer(line):
                    _claim(category, match.start(), match.end())
        # keep the provenance/design line highlight visible over the token colours
        from carla_nl.prompt_feedback import prompt_placeholder_fields
        fields = prompt_placeholder_fields(text)
        widget.tag_remove("prompt_field", "1.0", "end")
        for field in fields:
            widget.tag_add("prompt_field", "1.0+{}c".format(field.start()), "1.0+{}c".format(field.end()))
        if hasattr(self, "prompt_field_hint"):
            self.prompt_field_hint.configure(text="{} fields · Tab / Enter: next · Shift+Tab: previous".format(len(fields)) if fields else "Confirm the final field with Tab / Enter to Analyze." if getattr(self, "_prompt_field_navigation_active", False) else "Your words stay editable. Analyze when ready.")
            self.next_field_button.configure(state="normal" if fields else "disabled")
        try:
            widget.tag_raise("design_link")
        except Exception:
            pass
        # A linked line must never hide an error, an editable field, or selection.
        for tag in ("prompt_replaced", "prompt_problem_focus", "prompt_unrecognized", "prompt_field", "sel"):
            widget.tag_raise(tag)

    def _analysis_signature(self):
        return (self.prompt_text.get("1.0", "end-1c"),
                tuple(variable.get() for variable in getattr(self, "_analysis_input_vars", ())),
                json.dumps(self.spawn_xodr, sort_keys=True), bool(getattr(self, "_spawn_is_default", False)))

    def _record_analysis_result(self, label, color):
        self._analysis_snapshot = self._analysis_signature()
        self._analysis_result = (label, color)
        self._refresh_analysis_state()

    def _refresh_analysis_state(self, *_args):
        if not hasattr(self, "analysis_state_label") or not hasattr(self, "prompt_text") or not self.top.winfo_exists():
            return
        current = self._analysis_signature()
        if current == self._analysis_snapshot:
            label, color = self._analysis_result
        elif not current[0].strip():
            label, color = "Not analyzed", "text_muted"
        else:
            label, color = "Changes pending", "warning_text"
        previous = self.analysis_state_var.get()
        self.analysis_state_var.set(label)
        self.analysis_state_label.configure(fg=INKSPACE_COLORS[color])
        design = getattr(self, "design_preview", None)
        if design is not None:
            design.set_stale(label == "Changes pending")
        if label == "Changes pending" and self.status_var.get().startswith("Analysis complete"):
            self.status_var.set("Changes pending · Design shows the latest valid preview. Click Analyze to check this draft.")
        elif label == "Ready" and previous == "Changes pending":
            self.status_var.set("Ready · Analyzed description restored.")

    def _on_prompt_modified(self, _event=None):
        widget = self.prompt_text
        self._refresh_prompt_undo()
        if getattr(self, "_prompt_replacement_snapshot", None) is not None and widget.get("1.0", "end-1c") != self._prompt_replacement_snapshot:
            self._clear_prompt_replacement_highlight()
        self._refresh_analysis_state()
        # Undo can clear Tk's modified flag; restore controls even on that edge.
        session = getattr(self, "prompt_repair_session", None)
        if session is not None and session.restore(widget.get("1.0", "end-1c")):
            self._refresh_prompt_repair_controls()
        try:
            if not widget.edit_modified():
                return
            widget.edit_modified(False)
        except Exception:
            return
        self._highlight_prompt_syntax()
        if widget.get("1.0", "end-1c") != getattr(self, "_prompt_diagnostic_snapshot", None):
            widget.tag_remove("prompt_unrecognized", "1.0", "end")
            widget.tag_remove("prompt_problem_focus", "1.0", "end")
        self._schedule_design_refresh()

    def _update_design_preview(self, converted=None):
        """Push the in-memory scenario into the embedded Design tab."""
        self._update_timeline(converted if converted is not None else self.converted)
        preview_widget = getattr(self, "design_preview", None)
        if preview_widget is None:
            return
        items = converted if converted is not None else self.converted
        if not items:
            return
        try:
            preview_widget.update(items[0].scenario)
        except Exception as exc:
            write_exception(exc)

    def _schedule_design_refresh(self, *_args):
        """Debounce live design refreshes while the user types/edits."""
        if getattr(self, "design_preview", None) is None:
            return
        job = getattr(self, "_design_refresh_job", None)
        if job is not None:
            try:
                self.top.after_cancel(job)
            except Exception:
                pass
        self._design_refresh_job = self.top.after(450, self._auto_refresh_design)

    def _auto_refresh_design(self):
        """Silently convert the current prompt and refresh the Design tab only.

        Never touches the text tabs, status line or message boxes, so partial /
        invalid intermediate input during editing is ignored quietly.
        """
        self._design_refresh_job = None
        self._refresh_analysis_state()
        preview_widget = getattr(self, "design_preview", None)
        if preview_widget is None:
            return
        try:
            _preview, converted = self._convert()
        except Exception:
            self._set_text(self.timeline_text, 'Waiting for a complete, valid Prompt...')
            return
        if converted:
            self._update_timeline(converted)
            try:
                preview_widget.update(converted[0].scenario)
            except Exception as exc:
                write_exception(exc)

    def save(self):
        if not self.preview():
            return
        paths = [OUTPUT_SCENARIOS / "{}.json".format(item.name) for item in self.converted]
        existing = [
            path for path in paths
            if path.exists() and not self._same_path(path, self.edit_path)
        ]
        if existing:
            names = "\n".join(str(path) for path in existing)
            if not self.messagebox.askyesno(
                "Overwrite scenario",
                "The following scenario file already exists. Overwrite it?\n\n{}".format(names),
                parent=self.top,
            ):
                return
        try:
            written = self.adapter.write_converted_scenarios(self.converted, OUTPUT_SCENARIOS)
            groups = load_scenario_groups()
            prompts = load_scenario_prompts()
            group = normalize_group(self.group_var.get())
            prompt_text = self.prompt_text.get("1.0", "end").strip()
            for path in written:
                key = scenario_metadata_key(path)
                if group == DEFAULT_SCENARIO_GROUP:
                    groups.pop(key, None)
                else:
                    groups[key] = group
                if prompt_text:
                    prompts[key] = prompt_text
                    if self.original_prompt_text and self.original_prompt_text != prompt_text:
                        record_scenario_prompt_history(path, self.original_prompt_text)
                    record_scenario_prompt_history(path, prompt_text)
            save_scenario_groups(groups)
            save_scenario_prompts(prompts)
        except Exception as exc:
            write_exception(exc)
            self.status_var.set("Save failed")
            self.messagebox.showerror("Save scenario failed", str(exc), parent=self.top)
            return
        select_key = scenario_metadata_key(written[0]) if written else None
        self.owner.refresh_scenarios(select_key=select_key)
        action = "Updated" if self.is_edit else "Added"
        self.owner.emit("{} {} scenario file(s).".format(action, len(written)))
        for path in written:
            self.owner.emit("Saved scenario: {}".format(path))
        self.top.destroy()

    def _same_path(self, left, right):
        if left is None or right is None:
            return False
        try:
            return Path(left).resolve() == Path(right).resolve()
        except Exception:
            return str(left).lower() == str(right).lower()

    def _convert(self):
        prompt_text = self.prompt_text.get("1.0", "end").strip()
        if not prompt_text:
            raise ValueError("Prompt is empty.")
        prompt_text, experiment_count = self._apply_prompt_experiment_overrides(prompt_text)
        if not prompt_text:
            raise ValueError("Traffic prompt is empty; add scenario duration, ego, vehicles, or obstacles before experiment alert lines.")
        self._duration_auto_requested = bool(
            getattr(self.adapter, "prompt_scenario_duration_is_auto", lambda _text: False)(prompt_text)
        )
        name = self.adapter.scenario_name_from_output(self.name_var.get(), fallback="prompt_scenario")
        self.name_var.set(name)
        preview, converted = self.adapter.convert_prompt(
            prompt_text,
            name=name,
            map_name=self.map_var.get(),
            weather_preset=self.weather_var.get(),
            rpc_port=RPC_PORT,
            quality_level=self.quality_var.get(),
        )
        self._prose_owns_tor = getattr(preview, "route", "") == "checked_prose"
        if self._prose_owns_tor and converted and converted[0].scenario.get("map_name") != self.map_var.get():
            raise ValueError("Map conflict: the prose map differs from the selected Map. Select the same map before analyzing. / 描述与上方 Map 不一致，请先统一地图。")
        if self._prose_owns_tor and experiment_count and (
            self.exp_tor_enabled_var.get() or self.exp_tor_onset_var.get().strip()
            or self.exp_tor_deadline_var.get().strip()
        ):
            raise ValueError("Use one source for TOR settings: prose alert/deadline sentences or the structured experiment alert lines. / 请只用一种方式设置 TOR，避免两套时间相互覆盖。")
        if experiment_count:
            for item in converted:
                item.warnings.append("Parsed {} experiment prompt line(s) into Alert settings.".format(experiment_count))
        self._apply_spawn_selection(converted)
        self._apply_experiment_events(converted)
        return preview, converted

    def _apply_experiment_events(self, converted):
        custom_markers = parse_custom_marker_text(self.exp_custom_markers_var.get())
        hazard_onset_text = self.exp_hazard_onset_var.get().strip()
        tor_enabled = bool(self.exp_tor_enabled_var.get())
        tor_onset_text = self.exp_tor_onset_var.get().strip()
        tor_deadline_text = self.exp_tor_deadline_var.get().strip()
        for item in converted:
            events = {
                "standard_markers_enabled": bool(self.exp_standard_markers_var.get()),
                "custom_markers": list(custom_markers),
                "hazards": [],
                "tor_windows": [],
            }
            if hazard_onset_text:
                events["hazards"].append({
                    "name": "hazard",
                    "onset_s": max(0.0, float(hazard_onset_text)),
                })
            if getattr(self, "_prose_owns_tor", False):
                # Prose is the source of truth, including an explicitly absent
                # TOR. Empty or stale wizard controls must not erase/resurrect it.
                adaptive_text = self.exp_adaptive_prompt_var.get().strip()
                adaptive_fields = parse_tor_adaptive_prompt(adaptive_text)
                for parsed_window in item.scenario.get("experiment_events", {}).get("tor_windows", []):
                    window = dict(parsed_window)
                    window["fields"] = {
                        "aoi_screen": "current",
                        "aoi_grid": self.owner.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
                        "target_role": "vehicle_1",
                        "dynamic_radius_multiplier": 2.0,
                        **adaptive_fields,
                        **dict(window.get("fields") or {}),
                    }
                    events["tor_windows"].append(window)
            elif tor_enabled or tor_onset_text or tor_deadline_text:
                if not tor_onset_text:
                    raise ValueError("Alert start time is required when TOR deadline is enabled.")
                onset_s = max(0.0, float(tor_onset_text))
                deadline_s = max(onset_s + 0.001, float(tor_deadline_text or (onset_s + 6.0)))
                keys = self.owner.tor_manual_keys()
                adaptive_text = self.exp_adaptive_prompt_var.get().strip()
                adaptive_fields = parse_tor_adaptive_prompt(adaptive_text)
                adaptive_fields.setdefault("adaptive_prompt", adaptive_text)
                adaptive_fields.setdefault("stage2_direction", self.exp_stage2_direction_var.get().strip() or "front")
                adaptive_fields.setdefault("stage2_speech_template", "Look {direction}.")
                adaptive_fields.setdefault("stage_speech_min_gap_s", 0.5)
                events["tor_windows"].append({
                    "name": "{}_tor".format(item.name),
                    "alert_onset_s": onset_s,
                    "tor_onset_s": onset_s,
                    "tor_deadline_s": deadline_s,
                    "manual_keys": keys,
                    "failure_action": self.exp_failure_action_var.get().strip() or "brake",
                    "notification_text": "",
                    "alert_text": "",
                    "ready_text": self.exp_ready_text_var.get().strip() or "Please take over.",
                    "return_to_auto_text": RETURN_TO_AUTO_TEXT,
                    "manual_safe_seconds": 4.0,
                    "fields": {
                        "aoi_screen": "current",
                        "aoi_grid": self.owner.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
                        "target_role": "vehicle_1",
                        "dynamic_radius_multiplier": 2.0,
                        **adaptive_fields,
                    },
                })
            duration_auto = bool(getattr(self, "_duration_auto_requested", False))
            if duration_auto:
                if events["tor_windows"]:
                    events["hold_until_tor_resolution"] = True
                    window = events["tor_windows"][0]
                    # Open-ended duration still needs a scripted timeline that
                    # covers the TOR deadline; the runtime hold handles the rest.
                    min_total_s = float(window["tor_deadline_s"]) + 2.0
                    if float(item.scenario.get("total_time_s", 0.0) or 0.0) < min_total_s:
                        item.scenario["total_time_s"] = min_total_s
                else:
                    item.warnings.append(
                        "Duration=auto requires the Alert/TOR deadline to be enabled; "
                        "using the default fixed duration instead."
                    )
            # When editing an existing scenario, keep its hold policy and hold
            # tuning keys instead of silently dropping them on re-save. Only
            # while a TOR window still exists -- a held scene with no TOR to
            # resolve would never end.
            existing_events = {}
            if self.is_edit and isinstance(self.edit_payload, dict):
                maybe_events = self.edit_payload.get("experiment_events")
                existing_events = maybe_events if isinstance(maybe_events, dict) else {}
            if events["tor_windows"]:
                for hold_key in ("hold_until_tor_resolution", "tor_hold_max_s", "tor_hold_resume_grace_s"):
                    if hold_key in events:
                        continue
                    hold_value = existing_events.get(hold_key)
                    if hold_value is not None:
                        events[hold_key] = hold_value
            item.scenario["experiment_events"] = events
            if events.get("hold_until_tor_resolution"):
                item.summaries.append(
                    "Duration=auto: scene holds after {:.0f}s until the take-over interaction completes.".format(
                        float(item.scenario.get("total_time_s", 0.0) or 0.0)
                    )
                )
            if events["tor_windows"]:
                window = events["tor_windows"][0]
                item.summaries.append(
                    "Experiment TOR: alert={:.2f}s latest={:.2f}s fallback={}".format(
                        float(window.get("alert_onset_s", window["tor_onset_s"])),
                        float(window["tor_deadline_s"]),
                        window.get("failure_action", "brake"),
                    )
                )
            if events["hazards"]:
                item.summaries.append("Hazard marker at {:.2f}s".format(float(events["hazards"][0]["onset_s"])))
            if events["custom_markers"]:
                item.summaries.append("{} custom experiment marker(s)".format(len(events["custom_markers"])))

    def _apply_spawn_selection(self, converted):
        if not self.spawn_xodr:
            return
        spawn = {
            "road_id": int(self.spawn_xodr["road_id"]),
            "lane_id": int(self.spawn_xodr["lane_id"]),
            "s": float(self.spawn_xodr["s"]),
        }
        ego_lane = self.spawn_ego_lane.get() if self.spawn_ego_lane.get() in ("Left", "Middle", "Right") else "Middle"
        for item in converted:
            if getattr(self, "_spawn_is_default", False):
                item.scenario["spawn_point_index"] = None
                item.scenario["spawn_xodr"] = dict(spawn)
                item.summaries.append(
                    "Default start (Low Speed TOR): road={road_id}, lane_id={lane_id}, s={s:.2f}m".format(**spawn)
                )
                continue
            if getattr(self, "_prose_owns_tor", False) and item.scenario.get("vehicles", {}).get("ego", {}).get("lane") != ego_lane:
                raise ValueError("Ego lane conflicts with the picked map start. Update the start lane or the prose before analyzing. / 自车车道与地图起点车道冲突，请先统一。")
            item.scenario["spawn_point_index"] = None
            item.scenario["spawn_xodr"] = dict(spawn)
            vehicles = item.scenario.get("vehicles")
            if isinstance(vehicles, dict) and isinstance(vehicles.get("ego"), dict):
                vehicles["ego"]["lane"] = ego_lane
                if not vehicles["ego"].get("lane_change_enabled", False):
                    vehicles["ego"]["lane_change_target_lane"] = ego_lane
            item.summaries.append(
                "Custom start: road={road_id}, lane_id={lane_id}, s={s:.2f}m, ego lane={ego_lane}".format(
                    ego_lane=ego_lane,
                    **spawn,
                )
            )

    def _preview_report(self, preview, converted):
        from carla_nl.prompt_review import scenario_review_lines
        from carla_nl.prompt_guidance_layout import english_guidance
        lines = []
        lines.append("Review before saving" if any(item.warnings for item in converted) else "Ready to save")
        lines.append("Check the summary or open Design to inspect the scene.")
        if len(converted) > 1:
            lines.append("{} scenarios generated".format(len(converted)))
        lines.append("")
        for item in converted:
            scenario = item.scenario
            lines.append("[{}]".format(item.name))
            lines.append(
                "Map: {} | Weather: {}".format(
                    scenario.get("map_name"),
                    scenario.get("weather_preset"),
                )
            )
            lines.append("")
            lines.append("Summary")
            for line in scenario_review_lines(scenario, self.prompt_text.get("1.0", "end-1c"), item.summaries):
                lines.append(line if line.startswith("  ") else "• " + line)
            lines.append("")
            if item.warnings:
                lines.append("Warnings")
                lines.extend("- {}".format(english_guidance(line)) for line in item.warnings)
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _json_report(self, converted):
        if not converted:
            return ""
        if len(converted) == 1:
            return json.dumps(converted[0].scenario, indent=2, ensure_ascii=False) + "\n"
        return json.dumps([item.scenario for item in converted], indent=2, ensure_ascii=False) + "\n"


class SessionBuilderDialog(object):
    def __init__(self, owner):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.owner = owner
        self.tk = tk
        self.ttk = ttk
        self.scrolledtext = scrolledtext
        self.top = tk.Toplevel(owner.root)
        self.top.title("Playlist Builder - Level-3 TOR Session")
        self.top.geometry("1160x820")
        self.top.minsize(980, 720)
        configure_inkspace_window(self.top)
        self.top.transient(owner.root)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.available_items = []
        self.available_entries = []
        self.playlist = []
        self.scenario_overrides = {}
        self.library_items = []
        self.library_entries = []
        self.active_playlist_id = ""
        self.default_tor_adaptive_prompt = TOR_ADAPTIVE_PROMPT_EXAMPLE
        self.playlist_name_var = tk.StringVar(value="Level-3 Playlist")
        self.playlist_group_var = tk.StringVar(value="")
        self.hold_var = tk.StringVar(value="10.0")
        self.tor_onset_var = tk.StringVar(value="")
        self.tor_deadline_var = tk.StringVar(value="")
        self.tor_failure_action_var = tk.StringVar(value="brake")
        self.tor_stage2_direction_var = tk.StringVar(value="front")
        self.tor_adaptive_prompt_var = tk.StringVar(value=TOR_ADAPTIVE_PROMPT_EXAMPLE)
        self.tor_hazard_onset_var = tk.StringVar(value="")
        self.tor_custom_markers_var = tk.StringVar(value="")
        self.freeze_probe_enabled_var = tk.BooleanVar(value=False)
        self.freeze_probe_command_var = tk.StringVar(value="")
        self._reload_available_items()
        self._load_library()
        self._load_config()
        self._build_ui()
        self._refresh_lists()
        self.top.grab_set()

    def _load_config(self):
        try:
            from extensions.level3_tor_demo.config import load_session_config

            cfg = load_session_config(LEVEL3_SESSION_CONFIG)
        except Exception:
            cfg = {"scenario_names": [], "tor": {}, "freeze_probe": {}}
        playlist_meta = cfg.get("playlist", {}) if isinstance(cfg.get("playlist"), dict) else {}
        self.active_playlist_id = str(playlist_meta.get("id") or "").strip()
        name = str(playlist_meta.get("name") or cfg.get("session_name") or "Level-3 Playlist").strip()
        group = normalize_group(playlist_meta.get("group"))
        self.playlist_name_var.set(name or "Level-3 Playlist")
        self.playlist_group_var.set("" if group == DEFAULT_SCENARIO_GROUP else group)
        known = set(item.name for item in self.available_items)
        self.playlist = [name for name in cfg.get("scenario_names", []) if name in known]
        self.scenario_overrides = {}  # per-scenario TOR override removed; scenarios use their own TOR
        self.hold_var.set(str(cfg.get("tor", {}).get("inter_scene_hold_s", 10.0)))
        self.tor_deadline_var.set("" if cfg.get("tor", {}).get("default_deadline_s") in (None, "") else str(cfg.get("tor", {}).get("default_deadline_s")))
        self.tor_failure_action_var.set(str(cfg.get("tor", {}).get("default_failure_action", "brake") or "brake"))
        self.tor_stage2_direction_var.set(str(cfg.get("tor", {}).get("stage2_direction", "front") or "front"))
        self.default_tor_adaptive_prompt = format_tor_adaptive_prompt(cfg.get("tor", {}))
        self.tor_adaptive_prompt_var.set(self.default_tor_adaptive_prompt)
        self.freeze_probe_enabled_var.set(bool(cfg.get("freeze_probe", {}).get("enabled", False)))
        self.freeze_probe_command_var.set(str(cfg.get("freeze_probe", {}).get("command") or ""))
        self.owner.tor_decision_enabled_var.set(bool(cfg.get("tor", {}).get("enabled", True)))

    def _load_library(self):
        self.library_items = load_playlist_library()

    def _reload_available_items(self):
        self.available_items = [item for item in self.owner.scenarios if item.kind == "prompt_json"]

    def _build_available_entries(self):
        entries = []
        if self.owner.group_view_var.get():
            grouped = {}
            for item in self.available_items:
                grouped.setdefault(normalize_group(item.group), []).append(item)
            group_names = sorted(grouped, key=lambda value: (value == DEFAULT_SCENARIO_GROUP, value.lower()))
            for group_name in group_names:
                entries.append((None, group_name))
                for item in grouped[group_name]:
                    entries.append((item, item.label))
        else:
            for item in self.available_items:
                entries.append((item, item.label))
        return entries

    def refresh_available(self):
        self.owner.refresh_scenarios(preserve_selection=True)
        self._reload_available_items()
        self._refresh_lists()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk
        root = ttk.Frame(self.top, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(2, weight=1)
        root.columnconfigure(3, weight=1)
        root.rowconfigure(2, weight=1)

        filters = ttk.Frame(root)
        filters.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(5, weight=1)
        ttk.Label(filters, text="Sort").grid(row=0, column=0, sticky="w", padx=(0, 6))
        sort_combo = ttk.Combobox(
            filters,
            textvariable=self.owner.scenario_sort_var,
            values=SCENARIO_SORT_OPTIONS,
            state="readonly",
            width=18,
        )
        sort_combo.grid(row=0, column=1, sticky="w", padx=(0, 12))
        sort_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_available())
        ttk.Checkbutton(
            filters,
            text="Group",
            variable=self.owner.group_view_var,
            command=self.refresh_available,
        ).grid(row=0, column=2, sticky="e")
        ttk.Button(filters, text="Refresh", command=self.refresh_available).grid(row=0, column=3, sticky="e", padx=(12, 0))
        ttk.Label(filters, text="Playlist").grid(row=0, column=4, sticky="e", padx=(14, 6))
        self.playlist_name_entry = ttk.Entry(filters, textvariable=self.playlist_name_var, width=22)
        self.playlist_name_entry.grid(row=0, column=5, sticky="ew")
        ttk.Label(filters, text="Group").grid(row=0, column=6, sticky="e", padx=(10, 6))
        ttk.Entry(filters, textvariable=self.playlist_group_var, width=16).grid(row=0, column=7, sticky="ew")

        ttk.Label(root, text="Available prompt JSON scenarios", style="Section.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(root, text="Session playlist", style="Section.TLabel").grid(row=1, column=2, sticky="w")

        self.available_box = tk.Listbox(root, selectmode=tk.EXTENDED, exportselection=False)
        style_inkspace_listbox(self.available_box)
        self.available_box.grid(row=2, column=0, sticky="nsew", pady=(6, 8))
        self.available_box.bind("<Double-1>", lambda _event: self.add_selected())
        self.playlist_box = tk.Listbox(root, selectmode=tk.EXTENDED, exportselection=False)
        style_inkspace_listbox(self.playlist_box)
        self.playlist_box.grid(row=2, column=2, sticky="nsew", pady=(6, 8))
        self.playlist_box.bind("<Double-1>", lambda _event: self.open_selected_prompt_editor())
        self._build_library_panel(root)

        tools = ttk.Frame(root)
        tools.grid(row=2, column=1, sticky="ns", padx=10, pady=(6, 8))
        ttk.Button(tools, text="Add >", command=self.add_selected).pack(fill="x", pady=(0, 6))
        ttk.Button(tools, text="< Remove", command=self.remove_selected).pack(fill="x", pady=(0, 18))
        ttk.Button(tools, text="Up", command=lambda: self.move_selected(-1)).pack(fill="x", pady=(0, 6))
        ttk.Button(tools, text="Down", command=lambda: self.move_selected(1)).pack(fill="x", pady=(0, 18))
        ttk.Button(tools, text="Counterbalance...", command=self.counterbalance_order).pack(fill="x", pady=(0, 6))
        ttk.Button(tools, text="Open Prompt", command=self.open_selected_prompt_editor).pack(fill="x", pady=(0, 6))

        settings = ttk.LabelFrame(root, text="Between Scenarios", padding=8)
        settings.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        settings.columnconfigure(3, weight=1)
        ttk.Label(settings, text="Phone-task hold (s)").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.hold_var, width=10).grid(row=0, column=1, sticky="w", padx=(8, 18))
        ttk.Checkbutton(settings, text="Freeze probe", variable=self.freeze_probe_enabled_var).grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.freeze_probe_command_var).grid(row=0, column=3, sticky="ew", padx=(8, 0))
        ttk.Checkbutton(
            settings,
            text="Enable TOR decision tree / Neon AOI gate",
            variable=self.owner.tor_decision_enabled_var,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Save Playlist", command=self.save, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(buttons, text="Close", command=self.close).grid(row=0, column=1, sticky="ew")

    def _build_library_panel(self, root):
        tk = self.tk
        ttk = self.ttk
        library = ttk.LabelFrame(root, text="Saved Playlists", padding=8)
        library.grid(row=0, column=3, rowspan=5, sticky="nsew", padx=(12, 0))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(1, weight=1)
        ttk.Label(library, text="Double-click to load as active playlist.", style="Muted.TLabel").grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )
        self.library_box = tk.Listbox(library, exportselection=False)
        style_inkspace_listbox(self.library_box)
        self.library_box.grid(row=1, column=0, sticky="nsew")
        self.library_box.bind("<<ListboxSelect>>", lambda _event: self.load_library_selection_metadata())
        self.library_box.bind("<Double-1>", lambda _event: self.load_selected_library_playlist())

        buttons = ttk.Frame(library)
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for col in range(2):
            buttons.columnconfigure(col, weight=1)
        ttk.Button(buttons, text="Load", command=self.load_selected_library_playlist).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(buttons, text="Duplicate", command=self.duplicate_selected_library_playlist).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(buttons, text="Delete", command=self.delete_selected_library_playlist).grid(
            row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0)
        )
        ttk.Button(buttons, text="Save As New", command=self.save_as_new_library_playlist).grid(
            row=1, column=1, sticky="ew", pady=(6, 0)
        )
        ttk.Button(buttons, text="New Playlist", command=self.start_new_playlist, style="Accent.TButton").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

    def _refresh_lists(self):
        self.available_box.delete(0, "end")
        self.available_entries = self._build_available_entries()
        for item, label in self.available_entries:
            if item is None:
                self.available_box.insert("end", "[{}]".format(label))
                try:
                    self.available_box.itemconfig(
                        self.available_box.size() - 1,
                        foreground=INKSPACE_COLORS["brand_hover"],
                        background=INKSPACE_COLORS["surface_alt"],
                    )
                except Exception:
                    pass
            else:
                prefix = "  " if self.owner.group_view_var.get() else ""
                self.available_box.insert("end", prefix + label)
        self.playlist_box.delete(0, "end")
        label_by_name = {item.name: item.label for item in self.available_items}
        for index, name in enumerate(self.playlist):
            self.playlist_box.insert("end", "{:02d}. {}".format(index + 1, label_by_name.get(name, name)))
        self._refresh_library_list()

    def _build_library_entries(self):
        entries = []
        grouped = {}
        for item in self.library_items:
            grouped.setdefault(normalize_group(item.get("group")), []).append(item)
        group_names = sorted(grouped, key=lambda value: (value == DEFAULT_SCENARIO_GROUP, value.lower()))
        for group_name in group_names:
            entries.append((None, group_name))
            for item in sorted(grouped[group_name], key=lambda row: str(row.get("name", "")).lower()):
                entries.append((item, str(item.get("name") or "Playlist")))
        return entries

    def _refresh_library_list(self):
        if not hasattr(self, "library_box"):
            return
        self.library_box.delete(0, "end")
        self.library_entries = self._build_library_entries()
        for item, label in self.library_entries:
            if item is None:
                self.library_box.insert("end", "[{}]".format(label))
                try:
                    self.library_box.itemconfig(
                        self.library_box.size() - 1,
                        foreground=INKSPACE_COLORS["brand_hover"],
                        background=INKSPACE_COLORS["surface_alt"],
                    )
                except Exception:
                    pass
                continue
            active = " *" if item.get("id") and item.get("id") == self.active_playlist_id else ""
            scenario_count = len(item.get("scenario_names", []) or [])
            q_cfg = item.get("questionnaire", {}) if isinstance(item.get("questionnaire"), dict) else {}
            q_schedule = q_cfg.get("schedule", []) if isinstance(q_cfg.get("schedule", []), list) else []
            q_text = " q:{}".format(len(q_schedule)) if q_schedule else ""
            self.library_box.insert("end", "  {}{} | {} scenario(s){}".format(label, active, scenario_count, q_text))

    def _selected_library_item(self):
        if not hasattr(self, "library_box"):
            return None
        selection = self.library_box.curselection()
        if len(selection) != 1:
            return None
        index = int(selection[0])
        if not (0 <= index < len(self.library_entries)):
            return None
        item = self.library_entries[index][0]
        return item if isinstance(item, dict) else None

    def load_library_selection_metadata(self):
        return

    def _current_playlist_name(self):
        text = self.playlist_name_var.get().strip()
        return text or "Level-3 Playlist"

    def _current_playlist_group(self):
        return normalize_group(self.playlist_group_var.get())

    def _make_library_item(self, playlist_id=None, name=None, group=None):
        return {
            "id": playlist_id or self.active_playlist_id or "{}_{}".format(
                make_safe_slug(group or self._current_playlist_group(), "group"),
                make_safe_slug(name or self._current_playlist_name(), "playlist"),
            ),
            "name": name or self._current_playlist_name(),
            "group": group or self._current_playlist_group(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scenario_names": list(self.playlist),
            "scenario_overrides": {},
            "tor": {
                "enabled": bool(self.owner.tor_decision_enabled_var.get()),
                "inter_scene_hold_s": self.hold_var.get().strip() or "10.0",
                "default_failure_action": self.tor_failure_action_var.get().strip() or "brake",
                "stage2_direction": self.tor_stage2_direction_var.get().strip() or "front",
                "adaptive_prompt": self.tor_adaptive_prompt_var.get().strip(),
            },
            "freeze_probe": {
                "enabled": bool(self.freeze_probe_enabled_var.get()),
                "command": self.freeze_probe_command_var.get().strip(),
            },
            "emotibit": {
                "enabled": bool(self.owner.emotibit_enabled_var.get()),
                "baseline_seconds": self.owner.emotibit_baseline_var.get().strip() or "2.0",
                "absolute_threshold": self.owner.emotibit_abs_threshold_var.get().strip() or "1.5",
                "z_threshold": self.owner.emotibit_z_threshold_var.get().strip() or "8.0",
            },
            "questionnaire": self._questionnaire_config_from_owner(),
        }

    def _questionnaire_config_from_owner(self):
        try:
            schedule = self.owner.questionnaire_schedule()
        except Exception:
            schedule = []
        return {
            "enabled": bool(self.owner.questionnaire_enabled_var.get()),
            "root": self.owner.questionnaire_root_var.get().strip() or str(SURVEY_GENERATOR_ROOT),
            "language": self.owner.questionnaire_language_var.get().strip() or "zh",
            "script_path": self.owner.questionnaire_script_var.get().strip(),
            "schedule": schedule,
            "run_before_session": bool(self.owner.questionnaire_before_var.get()),
            "run_between_scenarios": bool(self.owner.questionnaire_between_var.get()),
            "run_after_session": bool(self.owner.questionnaire_after_var.get()),
            "wait": bool(self.owner.questionnaire_wait_var.get()),
            "timeout_s": 0.0,
            "continue_on_error": False,
            "handoff_to_launcher": True,
        }

    def _upsert_library_item(self, item):
        items = [dict(existing) for existing in self.library_items]
        replaced = False
        for idx, existing in enumerate(items):
            if existing.get("id") == item.get("id"):
                items[idx] = dict(item)
                replaced = True
                break
        if not replaced:
            items.append(dict(item))
        save_playlist_library(items)
        self.library_items = load_playlist_library()
        self._refresh_library_list()

    def _apply_library_item_to_builder(self, item):
        if not isinstance(item, dict):
            return
        known = set(row.name for row in self.available_items)
        self.active_playlist_id = str(item.get("id") or "").strip()
        self.playlist_name_var.set(str(item.get("name") or "Level-3 Playlist"))
        group = normalize_group(item.get("group"))
        self.playlist_group_var.set("" if group == DEFAULT_SCENARIO_GROUP else group)
        self.playlist = [name for name in item.get("scenario_names", []) if name in known]
        self.scenario_overrides = {}  # per-scenario TOR override removed; scenarios use their own TOR
        tor = item.get("tor", {}) if isinstance(item.get("tor"), dict) else {}
        self.owner.tor_decision_enabled_var.set(bool(tor.get("enabled", self.owner.tor_decision_enabled_var.get())))
        if tor.get("inter_scene_hold_s") not in (None, ""):
            self.hold_var.set(str(tor.get("inter_scene_hold_s")))
        if tor.get("default_failure_action"):
            self.tor_failure_action_var.set(str(tor.get("default_failure_action")))
        if tor.get("stage2_direction"):
            self.tor_stage2_direction_var.set(str(tor.get("stage2_direction")))
        if tor.get("adaptive_prompt"):
            self.tor_adaptive_prompt_var.set(str(tor.get("adaptive_prompt")))
        freeze = item.get("freeze_probe", {}) if isinstance(item.get("freeze_probe"), dict) else {}
        self.freeze_probe_enabled_var.set(bool(freeze.get("enabled", False)))
        self.freeze_probe_command_var.set(str(freeze.get("command") or ""))
        emotibit = item.get("emotibit", {}) if isinstance(item.get("emotibit"), dict) else {}
        if "enabled" in emotibit:
            self.owner.emotibit_enabled_var.set(bool(emotibit.get("enabled", False)))
        if emotibit.get("baseline_seconds") not in (None, ""):
            self.owner.emotibit_baseline_var.set(str(emotibit.get("baseline_seconds")))
        if emotibit.get("absolute_threshold") not in (None, ""):
            self.owner.emotibit_abs_threshold_var.set(str(emotibit.get("absolute_threshold")))
        if emotibit.get("z_threshold") not in (None, ""):
            self.owner.emotibit_z_threshold_var.set(str(emotibit.get("z_threshold")))
        questionnaire = item.get("questionnaire", {}) if isinstance(item.get("questionnaire"), dict) else {}
        if questionnaire:
            self.owner.questionnaire_enabled_var.set(bool(questionnaire.get("enabled", False)))
            self.owner.questionnaire_before_var.set(bool(questionnaire.get("run_before_session", False)))
            self.owner.questionnaire_between_var.set(bool(questionnaire.get("run_between_scenarios", False)))
            self.owner.questionnaire_after_var.set(bool(questionnaire.get("run_after_session", False)))
            self.owner.questionnaire_wait_var.set(bool(questionnaire.get("wait", True)))
            if questionnaire.get("root"):
                self.owner.questionnaire_root_var.set(str(questionnaire.get("root")))
            if questionnaire.get("language"):
                self.owner.questionnaire_language_var.set(str(questionnaire.get("language")))
            self.owner.questionnaire_script_var.set(str(questionnaire.get("script_path") or ""))
            schedule = questionnaire.get("schedule", [])
            if not isinstance(schedule, list):
                schedule = []
            self.owner.questionnaire_schedule_var.set(json.dumps(schedule, ensure_ascii=False, indent=2))
        self._refresh_lists()

    def load_selected_library_playlist(self):
        item = self._selected_library_item()
        if item is None:
            self.owner.messagebox.showinfo("Level-3 TOR Session", "Select a saved playlist first.")
            return
        self._apply_library_item_to_builder(item)
        self.owner.emit("Loaded playlist '{}'.".format(item.get("name", "Playlist")))

    def start_new_playlist(self):
        """Reset the builder to author a brand-new playlist.

        Clears the scenario list and detaches from the loaded library entry
        (empty id), so one click on Save Playlist creates a fresh saved
        playlist instead of overwriting the active one.
        """
        self.active_playlist_id = ""
        self.playlist = []
        self.scenario_overrides = {}
        self.playlist_name_var.set("New Playlist")
        self.playlist_group_var.set("")
        try:
            self.library_box.selection_clear(0, "end")
        except Exception:
            pass
        self._refresh_lists()
        try:
            self.playlist_name_entry.focus_set()
            self.playlist_name_entry.selection_range(0, "end")
        except Exception:
            pass
        self.owner.emit("Started a new playlist; add scenarios and click Save Playlist.")

    def save_as_new_library_playlist(self):
        if not self.playlist:
            self.owner.messagebox.showerror("Level-3 TOR Session", "Add at least one scenario before saving.")
            return
        name = self._current_playlist_name()
        group = self._current_playlist_group()
        playlist_id = "{}_{}_{}".format(make_safe_slug(group, "group"), make_safe_slug(name, "playlist"), time.strftime("%Y%m%d_%H%M%S"))
        self.active_playlist_id = playlist_id
        self.save()

    def duplicate_selected_library_playlist(self):
        item = self._selected_library_item()
        if item is None:
            self.owner.messagebox.showinfo("Level-3 TOR Session", "Select a saved playlist first.")
            return
        duplicate = dict(item)
        duplicate["id"] = "{}_copy_{}".format(make_safe_slug(item.get("id"), "playlist"), time.strftime("%H%M%S"))
        duplicate["name"] = "{} Copy".format(item.get("name") or "Playlist")
        duplicate["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._upsert_library_item(duplicate)
        self.owner.emit("Duplicated playlist '{}'.".format(item.get("name", "Playlist")))

    def delete_selected_library_playlist(self):
        item = self._selected_library_item()
        if item is None:
            self.owner.messagebox.showinfo("Level-3 TOR Session", "Select a saved playlist first.")
            return
        if not self.owner.messagebox.askyesno(
            "Level-3 TOR Session",
            "Delete saved playlist '{}' from the library?".format(item.get("name", "Playlist")),
        ):
            return
        self.library_items = [row for row in self.library_items if row.get("id") != item.get("id")]
        save_playlist_library(self.library_items)
        self.library_items = load_playlist_library()
        if self.active_playlist_id == item.get("id"):
            self.active_playlist_id = ""
        self._refresh_library_list()
        self.owner.emit("Deleted playlist '{}'.".format(item.get("name", "Playlist")))

    def add_selected(self):
        for index in self.available_box.curselection():
            item = self.available_entries[int(index)][0]
            if item is None:
                continue
            name = item.name
            if name not in self.playlist:
                self.playlist.append(name)
        self._refresh_lists()

    def remove_selected(self):
        for index in sorted((int(i) for i in self.playlist_box.curselection()), reverse=True):
            if 0 <= index < len(self.playlist):
                del self.playlist[index]
        self._refresh_lists()

    def _selected_playlist_names(self):
        names = []
        for index in self.playlist_box.curselection():
            index = int(index)
            if 0 <= index < len(self.playlist):
                names.append(self.playlist[index])
        return names

    def _scenario_item_by_name(self, name):
        for item in self.available_items:
            if item.name == name:
                return item
        self.refresh_available()
        for item in self.available_items:
            if item.name == name:
                return item
        return None

    def open_selected_prompt_editor(self):
        names = self._selected_playlist_names()
        if len(names) != 1:
            self.owner.messagebox.showinfo("Level-3 TOR Session", "Select exactly one playlist scenario first.")
            return
        item = self._scenario_item_by_name(names[0])
        if item is None:
            self.owner.messagebox.showerror("Level-3 TOR Session", "Scenario not found: {}".format(names[0]))
            return
        if item.kind != "prompt_json":
            self.owner.messagebox.showinfo(
                "Level-3 TOR Session",
                "Only prompt JSON scenarios can be opened in the prompt editor.",
            )
            return
        try:
            AddScenarioDialog(self.owner, edit_item=item)
            self.owner.emit("Opened prompt editor for '{}' from Level-3 Session Builder.".format(item.name))
        except Exception as exc:
            write_exception(exc)
            self.owner.messagebox.showerror("Level-3 TOR Session", str(exc))

    def move_selected(self, delta):
        selected = sorted(int(i) for i in self.playlist_box.curselection())
        if delta > 0:
            selected = list(reversed(selected))
        new_selected = []
        for index in selected:
            target = index + int(delta)
            if target < 0 or target >= len(self.playlist):
                new_selected.append(index)
                continue
            self.playlist[index], self.playlist[target] = self.playlist[target], self.playlist[index]
            new_selected.append(target)
        self._refresh_lists()
        for index in new_selected:
            self.playlist_box.selection_set(index)

    def counterbalance_order(self):
        """Reorder the playlist with a balanced Latin-square (Williams) order.

        Asks for the participant number and applies the assigned balanced order so
        each scenario appears in each position equally across participants.
        """
        from tkinter import messagebox, simpledialog

        if len(self.playlist) < 2:
            messagebox.showinfo(
                "Counterbalance",
                "Add at least two scenarios to the playlist before counterbalancing.",
            )
            return
        try:
            from carla_nl.counterbalance import order_count, order_for_participant
        except Exception as exc:
            messagebox.showerror("Counterbalance", "Counterbalance module unavailable:\n{}".format(exc))
            return
        n_orders = order_count(len(self.playlist))
        participant = simpledialog.askinteger(
            "Counterbalance",
            (
                "Participant number (1-based).\n\n"
                "{} scenarios -> {} balanced Latin-square orders; "
                "participants beyond {} reuse the orders cyclically."
            ).format(len(self.playlist), n_orders, n_orders),
            minvalue=1,
            initialvalue=1,
        )
        if participant is None:
            return
        self.playlist = order_for_participant(self.playlist, participant - 1)
        self._refresh_lists()
        messagebox.showinfo(
            "Counterbalance",
            "Applied balanced order {} of {} for participant {}.".format(
                ((participant - 1) % n_orders) + 1, n_orders, participant
            ),
        )

    def save(self):
        try:
            if not self.playlist:
                raise RuntimeError("Add at least one prompt JSON scenario to the playlist.")
            hold_s = float(self.hold_var.get().strip() or "10.0")
            emotibit_baseline_s = max(0.0, float(self.owner.emotibit_baseline_var.get().strip() or "2.0"))
            emotibit_abs_threshold = max(0.0, float(self.owner.emotibit_abs_threshold_var.get().strip() or "1.5"))
            emotibit_z_threshold = max(0.0, float(self.owner.emotibit_z_threshold_var.get().strip() or "8.0"))
            from extensions.level3_tor_demo.config import load_session_config, normalize_tag_layout, save_session_config

            cfg = load_session_config(LEVEL3_SESSION_CONFIG)
            playlist_name = self._current_playlist_name()
            playlist_group = self._current_playlist_group()
            if not self.active_playlist_id:
                self.active_playlist_id = "{}_{}_{}".format(
                    make_safe_slug(playlist_group, "group"),
                    make_safe_slug(playlist_name, "playlist"),
                    time.strftime("%Y%m%d_%H%M%S"),
                )
            cfg["playlist"] = {
                "id": self.active_playlist_id,
                "name": playlist_name,
                "group": playlist_group,
            }
            cfg["scenario_names"] = list(self.playlist)
            cfg["scenario_overrides"] = {}  # per-scenario TOR override removed; scenarios keep their own TOR
            questionnaire_cfg = cfg.get("questionnaire", {}) or {}
            cfg.setdefault("execution", {})["mode"] = (
                "stepped"
                if questionnaire_cfg.get("enabled") and questionnaire_cfg.get("run_between_scenarios")
                else "continuous"
            )
            cfg["neon"]["ip"] = self.owner.neon_ip_var.get().strip()
            cfg["neon"]["enabled"] = bool(self.owner.aoi_enabled_var.get())
            cfg["neon"]["latency_log"] = bool(self.owner.aoi_latency_log_var.get())
            cfg["neon"]["no_voice"] = bool(self.owner.aoi_no_voice_var.get())
            cfg["neon"]["preview"] = True
            cfg["emotibit"]["enabled"] = bool(self.owner.emotibit_enabled_var.get())
            cfg["emotibit"]["auto_start_oscilloscope"] = bool(self.owner.emotibit_enabled_var.get())
            cfg["emotibit"]["auto_start_bridge"] = False
            cfg["emotibit"]["oscilloscope_path"] = str(EMOTIBIT_OSCILLOSCOPE_EXE)
            cfg["emotibit"]["baseline_seconds"] = emotibit_baseline_s
            cfg["emotibit"]["absolute_threshold"] = emotibit_abs_threshold
            cfg["emotibit"]["z_threshold"] = emotibit_z_threshold
            cfg["tag_layout"] = normalize_tag_layout(
                self.owner.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS
            )
            cfg["lsl"]["stream_name"] = self.owner.lsl_stream_name()
            cfg["lsl"]["telemetry_stream_name"] = DEFAULT_CARLA_TELEMETRY_STREAM
            cfg["lsl"]["feedback_port"] = int(DEFAULT_AOI_FEEDBACK_PORT)
            cfg.setdefault("queen_vehicles", {})
            cfg["queen_vehicles"]["enabled"] = bool(self.owner.queen_vehicles_var.get())
            apply_queen_density_config(cfg["queen_vehicles"], self.owner.queen_density_var.get())
            apply_queen_speed_config(cfg["queen_vehicles"], self.owner.queen_speed_var.get())
            apply_queen_despawn_config(cfg["queen_vehicles"], self.owner.queen_despawn_var.get())
            cfg["tor"]["target_grid"] = self.owner.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID
            cfg["tor"]["enabled"] = bool(self.owner.tor_decision_enabled_var.get())
            cfg["tor"]["visual_test"] = bool(self.owner.tor_visual_test_var.get())
            cfg["tor"]["mirror_hud_test"] = bool(self.owner.tor_mirror_hud_test_var.get())
            cfg["tor"]["inter_scene_hold_s"] = max(0.0, hold_s)
            cfg["tor"]["takeover_input"] = self.owner.takeover_input_mode()
            cfg["tor"]["default_manual_keys"] = self.owner.tor_manual_keys()
            cfg["tor"]["default_failure_action"] = self.tor_failure_action_var.get().strip() or "brake"
            cfg["tor"]["stage2_direction"] = self.tor_stage2_direction_var.get().strip() or "front"
            cfg["tor"]["stage2_speech_template"] = "Look {direction}."
            cfg["tor"]["stage_speech_min_gap_s"] = 0.5
            adaptive_text = self.tor_adaptive_prompt_var.get().strip()
            adaptive_fields = parse_tor_adaptive_prompt(adaptive_text)
            adaptive_fields.setdefault("adaptive_prompt", adaptive_text)
            adaptive_fields.setdefault("stage2_direction", cfg["tor"]["stage2_direction"])
            adaptive_fields.setdefault("stage2_speech_template", cfg["tor"]["stage2_speech_template"])
            adaptive_fields.setdefault("stage_speech_min_gap_s", cfg["tor"]["stage_speech_min_gap_s"])
            cfg["tor"].update(adaptive_fields)
            cfg["tor"]["ready_text"] = "Please take over."
            cfg["tor"].pop("default_correct_keys", None)
            cfg["tor"].pop("default_success_action", None)
            cfg["freeze_probe"]["enabled"] = bool(self.freeze_probe_enabled_var.get())
            cfg["freeze_probe"]["command"] = self.freeze_probe_command_var.get().strip()
            save_session_config(LEVEL3_SESSION_CONFIG, cfg)
            self._upsert_library_item(self._make_library_item(playlist_id=self.active_playlist_id, name=playlist_name, group=playlist_group))
            self.owner.refresh_level3_session_summary()
            self.owner.emit(
                "Saved Level-3 TOR playlist '{}' [{}]: {}".format(
                    playlist_name,
                    playlist_group,
                    ", ".join(self.playlist),
                )
            )
            self.close()
        except Exception as exc:
            write_exception(exc)
            self.owner.messagebox.showerror("Level-3 TOR Session", str(exc))

    def close(self):
        self.top.destroy()


GENERATED_SURVEY_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generated by the CARLA scenario launcher (Generate Survey from Prompt).

Self-contained: the launcher renders SURVEY_DEFINITION directly during
playlist handoff; running this file standalone opens the same questionnaire
in a dark-themed tkinter window and appends responses to a CSV next to it.
"""
import csv
import json
import os
import time
from pathlib import Path

SURVEY_DEFINITION = json.loads(__SURVEY_JSON__)
RESPONSE_BASENAME = __RESPONSE_BASENAME__

# Dark survey palette (kept in sync with SURVEY_STYLE in the launcher).
WINDOW_BG = "#0a0f1e"
CARD_BG = "#0e1626"
CARD_BORDER = "#1f2c47"
QUESTION_BG = "#13233f"
QUESTION_BORDER = "#3f6fb5"
ACCENT_BAR = "#6aa5ff"
ACCENT = "#5b9dff"
TEXT_PRIMARY = "#eef3fb"
TEXT_MUTED = "#8b98ad"
BTN_BG = "#0f1a2e"
BTN_BORDER = "#2a3c5e"
BTN_HOVER = "#16243d"
BTN_SELECTED_BG = "#1b3a6b"
BTN_SELECTED_BORDER = "#6aa5ff"
BADGE_REQUIRED_BG = "#1f4fd8"
BADGE_OPTIONAL_BG = "#33415e"
SAVE_BG = "#2563eb"
SAVE_HOVER = "#1d4ed8"
CLOSE_RED = "#e5534b"
FONT_FAMILY = "Segoe UI"


def _env(name):
    return os.environ.get(name, "")


def _numeric_avg(numeric):
    """numeric: list of (value, scale_min, scale_max) for answered numeric questions.

    Returns the rounded mean only when every answered numeric question shares
    one (scale_min, scale_max) pair; otherwise an empty string.
    """
    if not numeric:
        return ""
    if len(set((mn, mx) for _value, mn, mx in numeric)) != 1:
        return ""
    return round(sum(value for value, _mn, _mx in numeric) / len(numeric), 4)


def _resolve_response_csv(directory, basename, headers):
    """Pick the responses CSV; on header column-count mismatch, suffix _2, _3..."""
    suffix = 1
    while True:
        name = "{0}_responses.csv".format(basename) if suffix == 1 else "{0}_responses_{1}.csv".format(basename, suffix)
        path = Path(directory) / name
        if not path.exists():
            return path
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                first = next(csv.reader(handle), None)
        except Exception:
            first = None
        if first is None or len(first) == len(headers):
            return path
        suffix += 1


def _type_label(question):
    """Human-readable widget-type caption for a rendered question card."""
    q_type = str(question.get("type", "slider"))
    if q_type == "point_select":
        return "Point Buttons"
    if q_type == "slider":
        return "Slider"
    if q_type == "true_false":
        return "True / False"
    if q_type == "choice":
        if question.get("multi"):
            return "Multiple Choice (select all that apply)"
        return "Multiple Choice"
    return "Free Text"


def _anchor_text(value, label):
    """Anchor caption for one end of a numeric scale ('1  Not at all' or '1')."""
    label = str(label or "").strip()
    if label:
        return "{0}  {1}".format(value, label)
    return str(value)


def _auto_instructions(definition):
    """Auto instructions line when ALL numeric questions share one scale.

    Returns an empty string when there are no numeric questions or their
    (scale_min, scale_max) pairs differ. Labels included only when uniform.
    """
    numeric = [
        question
        for question in (definition.get("questions") or [])
        if str(question.get("type", "")) in ("slider", "point_select")
    ]
    if not numeric:
        return ""
    scales = set()
    for question in numeric:
        try:
            scales.add((
                int(question.get("scale_min", definition.get("scale_min", 1))),
                int(question.get("scale_max", definition.get("scale_max", 7))),
            ))
        except Exception:
            return ""
    if len(scales) != 1:
        return ""
    lo, hi = next(iter(scales))
    lo_labels = set(str(q.get("scale_min_label") or "").strip() for q in numeric)
    hi_labels = set(str(q.get("scale_max_label") or "").strip() for q in numeric)
    lo_text = str(lo)
    hi_text = str(hi)
    if len(lo_labels) == 1 and next(iter(lo_labels)):
        lo_text = "{0} ({1})".format(lo, next(iter(lo_labels)))
    if len(hi_labels) == 1 and next(iter(hi_labels)):
        hi_text = "{0} ({1})".format(hi, next(iter(hi_labels)))
    return "Please answer each question using the response scale from {0} to {1}.".format(lo_text, hi_text)


def _header_instructions(definition):
    """Instructions line for the survey header card ('' -> hide the line)."""
    text = str(definition.get("instructions") or "").strip()
    if text:
        return text
    return _auto_instructions(definition)


def main():
    import tkinter as tk
    from tkinter import messagebox, ttk

    definition = SURVEY_DEFINITION
    root = tk.Tk()
    root.title(definition.get("title", "Questionnaire"))
    root.geometry("980x760+80+60")
    root.minsize(760, 560)
    root.configure(bg=WINDOW_BG)

    outer = tk.Frame(root, bg=WINDOW_BG)
    outer.pack(fill="both", expand=True, padx=16, pady=16)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(2, weight=1)

    # --- 1. Header card: title + instructions + red close (no save) ---
    header_wrap = tk.Frame(outer, bg=CARD_BORDER)
    header_wrap.grid(row=0, column=0, columnspan=2, sticky="ew")
    header = tk.Frame(header_wrap, bg=CARD_BG)
    header.pack(fill="both", expand=True, padx=1, pady=1)
    header.columnconfigure(0, weight=1)
    instructions = _header_instructions(definition)
    tk.Label(
        header,
        text=definition.get("title", "Questionnaire"),
        font=(FONT_FAMILY, 18, "bold"),
        bg=CARD_BG,
        fg=TEXT_PRIMARY,
        anchor="w",
        justify="left",
    ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2 if instructions else 12))
    close_wrap = tk.Frame(header, bg=CLOSE_RED)
    close_wrap.grid(row=0, column=1, sticky="ne", padx=12, pady=12)
    tk.Button(
        close_wrap,
        text="✕",
        command=root.destroy,
        font=(FONT_FAMILY, 11, "bold"),
        bg=CARD_BG,
        fg=CLOSE_RED,
        activebackground=BTN_HOVER,
        activeforeground=CLOSE_RED,
        relief="flat",
        bd=0,
        cursor="hand2",
        padx=9,
        pady=2,
        highlightthickness=0,
    ).pack(padx=1, pady=1)
    if instructions:
        tk.Label(
            header,
            text=instructions,
            font=(FONT_FAMILY, 10),
            bg=CARD_BG,
            fg=TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=840,
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

    # --- 2. Static page row (no pagination implemented) ---
    page_row = tk.Frame(outer, bg=WINDOW_BG)
    page_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))
    tk.Label(page_row, text="Page 1 / 1", font=(FONT_FAMILY, 9, "bold"), bg=WINDOW_BG, fg=ACCENT).pack(anchor="w")
    tk.Label(page_row, text="Page 1", font=(FONT_FAMILY, 12, "bold"), bg=WINDOW_BG, fg=TEXT_PRIMARY).pack(anchor="w")

    # --- 3. Scrollable body ---
    canvas = tk.Canvas(outer, bg=WINDOW_BG, highlightthickness=0, bd=0)
    scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.grid(row=2, column=0, sticky="nsew")
    scroll.grid(row=2, column=1, sticky="ns")
    body = tk.Frame(canvas, bg=WINDOW_BG)
    body.columnconfigure(0, weight=1)
    window_id = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
    canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    def make_button(parent, label, size, padx_inner, pady_inner):
        """A flat dark button inside a 1px frame-as-border wrapper."""
        wrap = tk.Frame(parent, bg=BTN_BORDER)
        button = tk.Button(
            wrap,
            text=label,
            font=(FONT_FAMILY, size, "bold"),
            bg=BTN_BG,
            fg=TEXT_PRIMARY,
            activebackground=BTN_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            cursor="hand2",
            highlightthickness=0,
            padx=padx_inner,
            pady=pady_inner,
        )
        button.pack(fill="both", expand=True, padx=1, pady=1)
        return wrap, button

    def make_point_button(parent, label):
        """A fixed-size square scale button (number 18pt bold)."""
        wrap = tk.Frame(parent, bg=BTN_BORDER, width=58, height=58)
        wrap.pack_propagate(False)
        button = tk.Button(
            wrap,
            text=label,
            font=(FONT_FAMILY, 18, "bold"),
            bg=BTN_BG,
            fg=TEXT_PRIMARY,
            activebackground=BTN_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            cursor="hand2",
            highlightthickness=0,
        )
        button.pack(fill="both", expand=True, padx=1, pady=1)
        return wrap, button

    def style_buttons(items, selected):
        for value, wrap, button in items:
            if value in selected:
                wrap.configure(bg=BTN_SELECTED_BORDER)
                button.configure(bg=BTN_SELECTED_BG)
            else:
                wrap.configure(bg=BTN_BORDER)
                button.configure(bg=BTN_BG)

    records = []
    for idx, question in enumerate(definition.get("questions", []), start=1):
        q_type = str(question.get("type", "slider"))
        required = bool(question.get("required", True))
        record = {"type": q_type, "question": question, "answered": False}

        card_wrap = tk.Frame(body, bg=CARD_BORDER)
        card_wrap.grid(row=idx - 1, column=0, sticky="ew", pady=(0, 10))
        card = tk.Frame(card_wrap, bg=CARD_BG, padx=12, pady=12)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        card.columnconfigure(0, weight=1)

        # Row A: Qn + widget-type caption + Required/Optional badge.
        head = tk.Frame(card, bg=CARD_BG)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="Q{0}".format(idx), font=(FONT_FAMILY, 10, "bold"), bg=CARD_BG, fg=ACCENT).grid(row=0, column=0, sticky="w")
        tk.Label(head, text=_type_label(question), font=(FONT_FAMILY, 10), bg=CARD_BG, fg=TEXT_MUTED).grid(row=0, column=1, sticky="w", padx=(10, 0))
        tk.Label(
            head,
            text="Required" if required else "Optional",
            font=(FONT_FAMILY, 8, "bold"),
            bg=BADGE_REQUIRED_BG if required else BADGE_OPTIONAL_BG,
            fg="#ffffff" if required else TEXT_MUTED,
            padx=8,
            pady=2,
        ).grid(row=0, column=2, sticky="e")

        # Row B: QUESTION block with thick accent bar on the left edge.
        block_wrap = tk.Frame(card, bg=QUESTION_BORDER)
        block_wrap.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        block = tk.Frame(block_wrap, bg=QUESTION_BG)
        block.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(block, bg=ACCENT_BAR, width=5).pack(side="left", fill="y")
        q_body = tk.Frame(block, bg=QUESTION_BG)
        q_body.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        tk.Label(q_body, text="QUESTION", font=(FONT_FAMILY, 8, "bold"), bg=QUESTION_BG, fg=ACCENT).pack(anchor="w")
        tk.Label(
            q_body,
            text=str(question.get("text", "")),
            font=(FONT_FAMILY, 13, "bold"),
            bg=QUESTION_BG,
            fg=TEXT_PRIMARY,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        if q_type in ("slider", "point_select"):
            min_v = int(question.get("scale_min", definition.get("scale_min", 1)))
            max_v = int(question.get("scale_max", definition.get("scale_max", 7)))
            # Row C: scale anchors (bare number when no label present).
            anchors = tk.Frame(card, bg=CARD_BG)
            anchors.grid(row=2, column=0, sticky="ew", pady=(8, 0))
            anchors.columnconfigure(0, weight=1)
            anchors.columnconfigure(1, weight=1)
            tk.Label(
                anchors,
                text=_anchor_text(min_v, question.get("scale_min_label")),
                font=(FONT_FAMILY, 10),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                anchors,
                text=_anchor_text(max_v, question.get("scale_max_label")),
                font=(FONT_FAMILY, 10),
                bg=CARD_BG,
                fg=TEXT_MUTED,
            ).grid(row=0, column=1, sticky="e")
            var = tk.IntVar(value=int(round((min_v + max_v) / 2.0)))
            if q_type == "slider":
                value_label = tk.Label(card, text=str(var.get()), font=(FONT_FAMILY, 12, "bold"), bg=CARD_BG, fg=ACCENT)
                value_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
                scale = tk.Scale(
                    card,
                    from_=min_v,
                    to=max_v,
                    orient="horizontal",
                    troughcolor=BTN_BG,
                    bg=ACCENT,
                    fg=ACCENT,
                    activebackground=ACCENT,
                    highlightthickness=0,
                    bd=0,
                    showvalue=0,
                    relief="flat",
                    sliderrelief="flat",
                    cursor="hand2",
                )
                scale.set(var.get())

                def on_slide(value, rec=record, target=var, lab=value_label):
                    target.set(int(round(float(value))))
                    lab.configure(text=str(target.get()))
                    rec["answered"] = True

                scale.configure(command=on_slide)
                scale.grid(row=4, column=0, sticky="ew", pady=(4, 0))
                record["scale"] = scale
            else:
                grid_f = tk.Frame(card, bg=CARD_BG)
                grid_f.grid(row=3, column=0, sticky="w", pady=(8, 0))
                items = []
                for offset, value in enumerate(range(min_v, max_v + 1)):
                    wrap, button = make_point_button(grid_f, str(value))
                    wrap.grid(row=offset // 10, column=offset % 10, padx=(0, 6), pady=(0, 6))
                    items.append((value, wrap, button))

                def on_point(value, rec=record, target=var, btns=items):
                    target.set(value)
                    rec["answered"] = True
                    style_buttons(btns, {value})

                for value, _wrap, button in items:
                    button.configure(command=lambda v=value, cb=on_point: cb(v))
                record["buttons"] = items
            record["var"] = var
        elif q_type == "true_false":
            var = tk.StringVar(value="")
            row_f = tk.Frame(card, bg=CARD_BG)
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            items = []
            for value in ("True", "False"):
                wrap, button = make_button(row_f, value, 11, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                items.append((value, wrap, button))

            def on_true_false(value, rec=record, target=var, btns=items):
                target.set(value)
                rec["answered"] = True
                style_buttons(btns, {value})

            for value, _wrap, button in items:
                button.configure(command=lambda v=value, cb=on_true_false: cb(v))
            record["var"] = var
            record["buttons"] = items
        elif q_type == "choice" and question.get("multi"):
            # Checkbutton semantics: independent BooleanVar toggle buttons;
            # the CSV cell stays the single "; "-joined selection list.
            row_f = tk.Frame(card, bg=CARD_BG)
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            vars_list = []
            items = []
            for option in [str(o) for o in (question.get("options") or [])]:
                flag = tk.BooleanVar(value=False)
                wrap, button = make_button(row_f, option, 10, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                vars_list.append((option, flag))
                items.append((option, wrap, button))

                def on_toggle(flag=flag, wrap=wrap, button=button, rec=record):
                    flag.set(not flag.get())
                    rec["answered"] = True
                    if flag.get():
                        wrap.configure(bg=BTN_SELECTED_BORDER)
                        button.configure(bg=BTN_SELECTED_BG)
                    else:
                        wrap.configure(bg=BTN_BORDER)
                        button.configure(bg=BTN_BG)

                button.configure(command=on_toggle)
            record["vars"] = vars_list
            record["buttons"] = items
        elif q_type == "choice":
            var = tk.StringVar(value="")
            row_f = tk.Frame(card, bg=CARD_BG)
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            items = []
            for option in [str(o) for o in (question.get("options") or [])]:
                wrap, button = make_button(row_f, option, 10, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                items.append((option, wrap, button))

            def on_choice(option, rec=record, target=var, btns=items):
                target.set(option)
                rec["answered"] = True
                style_buttons(btns, {option})

            for option, _wrap, button in items:
                button.configure(command=lambda v=option, cb=on_choice: cb(v))
            record["var"] = var
            record["buttons"] = items
        else:
            text_wrap = tk.Frame(card, bg=BTN_BORDER)
            text_wrap.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            text = tk.Text(
                text_wrap,
                height=4,
                wrap="word",
                bg=BTN_BG,
                fg=TEXT_PRIMARY,
                insertbackground=TEXT_PRIMARY,
                bd=0,
                relief="flat",
                highlightthickness=0,
                font=(FONT_FAMILY, 10),
                padx=8,
                pady=6,
            )
            text.pack(fill="both", expand=True, padx=1, pady=1)
            record["text"] = text
        records.append(record)

    def save():
        values = []
        numeric = []
        for idx, record in enumerate(records, start=1):
            question = record["question"]
            q_type = record["type"]
            required = bool(question.get("required", True))
            if q_type in ("slider", "point_select"):
                if required and not record.get("answered"):
                    messagebox.showwarning("Incomplete", "Please answer Question {0} before saving.".format(idx), parent=root)
                    return
                value = record["var"].get() if record.get("answered") else ""
                values.append(value)
                if value != "":
                    min_v = int(question.get("scale_min", definition.get("scale_min", 1)))
                    max_v = int(question.get("scale_max", definition.get("scale_max", 7)))
                    numeric.append((float(value), min_v, max_v))
            elif q_type == "choice" and question.get("multi"):
                selected = [option for option, flag in record["vars"] if flag.get()]
                if required and not selected:
                    messagebox.showwarning("Incomplete", "Please answer Question {0} before saving.".format(idx), parent=root)
                    return
                values.append("; ".join(selected))
            elif q_type in ("true_false", "choice"):
                value = record["var"].get()
                if required and not value:
                    messagebox.showwarning("Incomplete", "Please answer Question {0} before saving.".format(idx), parent=root)
                    return
                values.append(value)
            else:
                value = record["text"].get("1.0", "end").strip()
                if required and not value:
                    messagebox.showwarning("Incomplete", "Please answer Question {0} before saving.".format(idx), parent=root)
                    return
                values.append(value)
        headers = [
            "timestamp", "survey_title", "carla_session", "questionnaire_phase", "questionnaire_label",
            "questionnaire_sequence_index", "questionnaire_sequence_count", "scenario_name",
            "scenario_index", "scenario_count", "scale_min", "scale_max",
        ]
        headers.extend("q{0}".format(i) for i in range(1, len(records) + 1))
        headers.append("numeric_avg")
        row = [
            time.strftime("%Y-%m-%dT%H:%M:%S"), definition.get("title", ""),
            _env("CARLA_SESSION_NAME"), _env("CARLA_QUESTIONNAIRE_PHASE"), _env("CARLA_QUESTIONNAIRE_LABEL"),
            _env("CARLA_QUESTIONNAIRE_SEQUENCE_INDEX"), _env("CARLA_QUESTIONNAIRE_SEQUENCE_COUNT"),
            _env("CARLA_QUESTIONNAIRE_SCENARIO"), _env("CARLA_QUESTIONNAIRE_SCENARIO_INDEX"),
            _env("CARLA_QUESTIONNAIRE_SCENARIO_COUNT"), definition.get("scale_min", ""), definition.get("scale_max", ""),
        ]
        row.extend(values)
        row.append(_numeric_avg(numeric))
        output_path = _resolve_response_csv(Path(__file__).resolve().parent, RESPONSE_BASENAME, headers)
        exists = output_path.exists()
        with output_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if not exists:
                writer.writerow(headers)
            writer.writerow(row)
        print("SURVEY_SAVED {0}".format(output_path), flush=True)
        root.destroy()

    # --- 4. Action row ---
    actions = tk.Frame(outer, bg=WINDOW_BG)
    actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
    actions.columnconfigure(0, weight=1)
    save_button = tk.Button(
        actions,
        text="Save Responses",
        command=save,
        font=(FONT_FAMILY, 10, "bold"),
        bg=SAVE_BG,
        fg="#ffffff",
        activebackground=SAVE_HOVER,
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        cursor="hand2",
        padx=18,
        pady=8,
        highlightthickness=0,
    )
    save_button.grid(row=0, column=1, sticky="e")

    # Testability hooks (no behavior change): smoke checks drive the form
    # through these instead of traversing the widget tree.
    root._survey_records = records
    root._survey_save = save
    root._survey_save_button = save_button
    root._survey_instructions = instructions
    root.mainloop()


if __name__ == "__main__":
    main()
'''


def renderer_survey_definition(survey, warnings=None):
    """Convert survey_prompt_parser output into the launcher questionnaire schema.

    ``warnings`` (optional list) collects human-readable notes whenever the
    rendered form differs from the apparent parser intent.
    """
    questions = []
    for item in survey.get("questions", []):
        if not isinstance(item, dict):
            continue
        q_type = str(item.get("type", "")).strip().lower()
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if q_type == "likert":
            lo = item.get("scale_min")
            hi = item.get("scale_max")
            lo = 1 if lo is None else int(lo)
            hi = 7 if hi is None else int(hi)
            question = {
                "text": text,
                "type": "point_select" if hi - lo < 10 else "slider",
                "required": True,
                "scale_min": lo,
                "scale_max": hi,
            }
            # Optional anchor labels from the parser (e.g. 1=Not at all .. 7=Extremely)
            # flow through onto the rendered numeric question when present.
            min_label = str(item.get("scale_min_label") or "").strip()
            max_label = str(item.get("scale_max_label") or "").strip()
            if min_label:
                question["scale_min_label"] = min_label
            if max_label:
                question["scale_max_label"] = max_label
            questions.append(question)
        elif q_type in ("yes_no", "yesno", "boolean", "true_false"):
            questions.append({"text": text, "type": "true_false", "required": True})
        elif q_type in ("multiple_choice", "choice") and item.get("options"):
            question = {
                "text": text,
                "type": "choice",
                "required": True,
                "options": [str(option) for option in item.get("options") or []],
            }
            if item.get("multi"):
                question["multi"] = True
            questions.append(question)
        else:
            if warnings is not None and q_type in ("multiple_choice", "choice"):
                warnings.append(
                    "Q{}: multiple choice had no options -> rendered as optional free-text".format(len(questions) + 1)
                )
            elif warnings is not None and q_type not in ("open", "text", ""):
                warnings.append(
                    "Q{}: unsupported type '{}' -> rendered as optional free-text".format(len(questions) + 1, q_type)
                )
            questions.append({"text": text, "type": "text", "required": False})
    return {
        "title": str(survey.get("title") or "Survey"),
        "instructions": str(survey.get("instructions") or ""),
        "scale_min": 1,
        "scale_max": 7,
        "questions": questions,
    }


def describe_survey_definition(definition, warnings=None):
    """Describe the FINAL rendered questionnaire (widget types, ranges, options).

    Returns one line per question plus an optional WARNINGS section, so the
    Generate Survey preview shows exactly what participants will get.
    """
    lines = ["Title: {}".format(definition.get("title", "Survey"))]
    instructions = str(definition.get("instructions") or "").strip()
    if instructions:
        lines.append("Instructions: {}".format(instructions))
    questions = definition.get("questions", []) or []
    if not questions:
        lines.append("(no questions)")
    for idx, question in enumerate(questions, start=1):
        q_type = str(question.get("type", "slider"))
        req = "required" if question.get("required", True) else "optional"
        if q_type in ("slider", "point_select"):
            lo = question.get("scale_min", definition.get("scale_min", 1))
            hi = question.get("scale_max", definition.get("scale_max", 7))
            lo_label = str(question.get("scale_min_label") or "").strip()
            hi_label = str(question.get("scale_max_label") or "").strip()
            label_bits = []
            if lo_label:
                label_bits.append("{}={}".format(lo, lo_label))
            if hi_label:
                label_bits.append("{}={}".format(hi, hi_label))
            if label_bits:
                tag = "[{} {}-{} ({}), {}]".format(q_type, lo, hi, " .. ".join(label_bits), req)
            else:
                tag = "[{} {}-{}, {}]".format(q_type, lo, hi, req)
        elif q_type == "true_false":
            tag = "[true_false, {}]".format(req)
        elif q_type == "choice":
            options = " | ".join(str(option) for option in question.get("options") or [])
            kind = "choice multi" if question.get("multi") else "choice"
            tag = "[{}: {}, {}]".format(kind, options, req)
        else:
            tag = "[text, {}]".format(req)
        lines.append("Q{}. {} {}".format(idx, tag, str(question.get("text", ""))))
    if warnings:
        lines.append("")
        lines.append("WARNINGS:")
        lines.extend("- {}".format(warning) for warning in warnings)
    return "\n".join(lines)


# Dark survey palette shared by both questionnaire render paths (the generated
# *_survey.py template above keeps its own self-contained copy of these values).
SURVEY_STYLE = {
    "window_bg": "#0a0f1e",
    "card_bg": "#0e1626",
    "card_border": "#1f2c47",
    "question_bg": "#13233f",
    "question_border": "#3f6fb5",
    "accent_bar": "#6aa5ff",
    "accent": "#5b9dff",
    "text_primary": "#eef3fb",
    "text_muted": "#8b98ad",
    "btn_bg": "#0f1a2e",
    "btn_border": "#2a3c5e",
    "btn_hover": "#16243d",
    "btn_selected_bg": "#1b3a6b",
    "btn_selected_border": "#6aa5ff",
    "badge_required_bg": "#1f4fd8",
    "badge_optional_bg": "#33415e",
    "save_bg": "#2563eb",
    "save_hover": "#1d4ed8",
    "close_red": "#e5534b",
    "font_family": "Segoe UI",
}


def survey_type_label(question):
    """Human-readable widget-type caption for a rendered question card."""
    q_type = str(question.get("type", "slider"))
    if q_type == "point_select":
        return "Point Buttons"
    if q_type == "slider":
        return "Slider"
    if q_type == "true_false":
        return "True / False"
    if q_type == "choice":
        if question.get("multi"):
            return "Multiple Choice (select all that apply)"
        return "Multiple Choice"
    return "Free Text"


def survey_anchor_text(value, label):
    """Anchor caption for one end of a numeric scale ('1  Not at all' or '1')."""
    label = str(label or "").strip()
    if label:
        return "{}  {}".format(value, label)
    return str(value)


def survey_auto_scale_instructions(definition):
    """Auto instructions line when ALL numeric questions share one scale.

    Returns '' when there are no numeric questions or their (scale_min,
    scale_max) pairs differ. Anchor labels are included only when uniform.
    """
    numeric = [
        question
        for question in (definition.get("questions") or [])
        if str(question.get("type", "")) in ("slider", "point_select")
    ]
    if not numeric:
        return ""
    scales = set()
    for question in numeric:
        try:
            scales.add((
                int(question.get("scale_min", definition.get("scale_min", 1))),
                int(question.get("scale_max", definition.get("scale_max", 7))),
            ))
        except Exception:
            return ""
    if len(scales) != 1:
        return ""
    lo, hi = next(iter(scales))
    lo_labels = set(str(q.get("scale_min_label") or "").strip() for q in numeric)
    hi_labels = set(str(q.get("scale_max_label") or "").strip() for q in numeric)
    lo_text = str(lo)
    hi_text = str(hi)
    if len(lo_labels) == 1 and next(iter(lo_labels)):
        lo_text = "{} ({})".format(lo, next(iter(lo_labels)))
    if len(hi_labels) == 1 and next(iter(hi_labels)):
        hi_text = "{} ({})".format(hi, next(iter(hi_labels)))
    return "Please answer each question using the response scale from {} to {}.".format(lo_text, hi_text)


def survey_header_instructions(definition):
    """Instructions line for the survey header card ('' -> hide the line)."""
    text = str(definition.get("instructions") or "").strip()
    if text:
        return text
    return survey_auto_scale_instructions(definition)


def compute_numeric_avg(numeric):
    """numeric: list of (value, scale_min, scale_max) for answered numeric questions.

    Returns the rounded mean only when every answered numeric question shares
    one (scale_min, scale_max) pair; otherwise an empty string.
    """
    if not numeric:
        return ""
    if len(set((mn, mx) for _value, mn, mx in numeric)) != 1:
        return ""
    return round(sum(value for value, _mn, _mx in numeric) / len(numeric), 4)


def resolve_response_csv_path(directory, basename, headers):
    """Pick the responses CSV; on header column-count mismatch, suffix _2, _3..."""
    suffix = 1
    while True:
        name = "{}_responses.csv".format(basename) if suffix == 1 else "{}_responses_{}.csv".format(basename, suffix)
        path = Path(directory) / name
        if not path.exists():
            return path
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                first = next(csv.reader(handle), None)
        except Exception:
            first = None
        if first is None or len(first) == len(headers):
            return path
        suffix += 1


def write_generated_survey_script(survey, out_dir):
    """Save a prompt-generated survey as a self-contained *_survey.py; return its path."""
    definition = renderer_survey_definition(survey)
    if not definition["questions"]:
        raise RuntimeError("The survey needs at least one question.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = make_safe_slug(definition.get("title", "survey"), "survey")
    basename = "{}_{}".format(slug, time.strftime("%Y%m%d_%H%M%S"))
    out_path = out_dir / "{}_survey.py".format(basename)
    payload = json.dumps(definition, ensure_ascii=False, indent=2)
    content = (
        GENERATED_SURVEY_TEMPLATE
        .replace("__SURVEY_JSON__", json.dumps(payload, ensure_ascii=False))
        .replace("__RESPONSE_BASENAME__", json.dumps(basename))
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path, definition


def open_generate_survey_dialog(owner, parent_window, on_saved=None):
    """NL -> survey generator dialog; saves a *_survey.py into the survey library.

    No SurveyGenerator package or path needed: the saved script renders through
    the launcher's built-in questionnaire dialog during playlist handoff.
    """
    import tkinter as tk
    from tkinter import messagebox, ttk

    win = tk.Toplevel(parent_window)
    win.title("Generate Survey from Prompt")
    win.geometry("680x660")
    try:
        configure_inkspace_window(win)
    except Exception:
        pass
    win.transient(parent_window)

    ttk.Label(win, text="Describe the survey in plain language:", style="Section.TLabel").pack(
        anchor="w", padx=12, pady=(12, 4)
    )
    txt = tk.Text(win, height=8, wrap="word")
    txt.pack(fill="x", padx=12)
    txt.insert(
        "1.0",
        "Title: Post-trial\n"
        "Add NASA-TLX\n"
        "1-7 Likert: How stressed did you feel?\n"
        "Yes/No: Did you notice the takeover request?\n"
        "Multiple choice: Which hazard did you see? options: pedestrian, vehicle, none\n"
        "Open: Any comments?",
    )
    tips = (
        "Syntax tips - one item per line; a TYPE keyword is required:\n"
        "  Scale 1-N    1-10 rating: How is the workload?      (also: Rate 1-10 / 1-7 likert / 1-10 评分)\n"
        "  Yes/No       Yes/No: Did you notice the takeover request?\n"
        "  Choice       Multiple choice: Which hazard? options: pedestrian, vehicle, none\n"
        "  Open text    Open: Any comments?\n"
        "  Built-ins    Add NASA-TLX / Add SUS / Add SAM       (own line, BEFORE any Open item)\n"
        "  Title        Title: Post-drive workload             (first line)\n"
        "Bare \"(1-10)\" without a keyword is treated as open text."
    )
    ttk.Label(win, text=tips, style="Muted.TLabel", justify="left", font=("Consolas", 9)).pack(
        anchor="w", padx=12, pady=(6, 0)
    )
    group_row = ttk.Frame(win)
    group_row.pack(fill="x", padx=12, pady=(8, 0))
    ttk.Label(group_row, text="Group (folder)").pack(side="left")
    group_var = tk.StringVar(value="")
    try:
        _groups_root = RUNTIME_DIR / "generated_surveys"
        existing_groups = (
            sorted((p.name for p in _groups_root.iterdir() if p.is_dir()), key=str.lower)
            if _groups_root.exists()
            else []
        )
    except Exception:
        existing_groups = []
    ttk.Combobox(group_row, textvariable=group_var, values=existing_groups, width=24).pack(
        side="left", padx=(8, 0)
    )
    ttk.Label(
        group_row,
        text="pick an existing group or type a new one; empty = no group",
        style="Muted.TLabel",
    ).pack(side="left", padx=(10, 0))
    ttk.Label(win, text="Preview:", style="Section.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
    preview = tk.Text(win, height=12, wrap="word", state="disabled")
    preview.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    state = {"survey": None}

    def do_generate():
        from carla_nl.survey_prompt_parser import summarize, survey_from_prompt

        survey = survey_from_prompt(txt.get("1.0", "end"))
        state["survey"] = survey
        warnings = []
        definition = renderer_survey_definition(survey, warnings)
        preview_text = (
            summarize(survey)
            + "\n\n=== Final rendering (what participants will see) ===\n"
            + describe_survey_definition(definition, warnings)
        )
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", preview_text)
        preview.configure(state="disabled")

    def do_save():
        # Always re-parse the CURRENT text box content (and refresh the
        # preview) so what is saved is exactly what the user typed.
        do_generate()
        survey = state.get("survey")
        if not survey or not survey.get("questions"):
            messagebox.showinfo("Generate Survey", "Add at least one question to the description first.", parent=win)
            return
        out_root = RUNTIME_DIR / "generated_surveys"
        group = group_var.get().strip()
        if group:
            out_root = out_root / make_safe_slug(group, "group")
        try:
            out_path, definition = write_generated_survey_script(survey, out_root)
        except Exception as exc:
            write_exception(exc)
            messagebox.showerror("Generate Survey", str(exc), parent=win)
            return
        entry = {"label": definition.get("title", "survey"), "script_path": str(out_path)}
        if callable(on_saved):
            on_saved(entry)
        messagebox.showinfo(
            "Generate Survey",
            "Saved {} question(s) to:\n{}\n\nIt is now available in the Survey Library (Build Schedule...).".format(
                len(definition["questions"]), out_path
            ),
            parent=win,
        )
        win.destroy()

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(buttons, text="Generate / Preview", command=do_generate).pack(side="left")
    ttk.Button(buttons, text="Save & Add", command=do_save, style="Accent.TButton").pack(side="left", padx=(8, 0))
    ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
    try:
        win.grab_set()
    except Exception:
        pass
    do_generate()


class QuestionnaireScheduleBuilderDialog(object):
    def __init__(self, owner, on_saved=None):
        import tkinter as tk
        from tkinter import filedialog, ttk

        self.owner = owner
        self.on_saved = on_saved
        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.top = tk.Toplevel(owner.root)
        self.top.title("Questionnaire Schedule Builder")
        self.top.geometry("1280x680")
        self.top.minsize(1120, 600)
        configure_inkspace_window(self.top)
        self.top.transient(owner.root)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.playlist_library = load_playlist_library()
        self.selected_playlist_id = ""  # "" = active session config
        self.playlist = self._load_playlist()
        self.entries = self._load_schedule_entries()
        self.selected_target_index = 0
        self.library_items = []
        self._saved_snapshot = self._entries_snapshot()
        self._build_ui()
        self._refresh_targets()
        self._refresh_questionnaires()
        self._refresh_library()
        self.top.grab_set()

    def _selected_library_playlist(self):
        if not self.selected_playlist_id:
            return None
        for item in self.playlist_library:
            if str(item.get("id") or "") == self.selected_playlist_id:
                return item
        return None

    @staticmethod
    def _active_playlist_id():
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            return str((cfg.get("playlist") or {}).get("id") or "").strip()
        except Exception:
            return ""

    def _playlist_combo_values(self):
        values = ["(Active session)"]
        for item in self.playlist_library:
            name = str(item.get("name") or "untitled")
            group = str(item.get("group") or "").strip()
            count = len(item.get("scenario_names") or [])
            values.append("{}{}  ({} scn)".format(name, "  [{}]".format(group) if group else "", count))
        return values

    def _load_playlist(self):
        item = self._selected_library_playlist()
        if item is not None:
            return [str(name) for name in item.get("scenario_names") or [] if str(name).strip()]
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            return [str(name) for name in cfg.get("scenario_names", []) if str(name).strip()]
        except Exception:
            return []

    def _label_for_scenario(self, scenario_name):
        for item in getattr(self.owner, "scenarios", []):
            if item.name == scenario_name:
                return item.label
        return scenario_name

    def _target_key(self, target_index):
        if 0 <= target_index < len(self.playlist):
            return "after_scenario:{}".format(target_index + 1)
        return "after_session"

    def _target_title(self, target_index):
        if 0 <= target_index < len(self.playlist):
            return "{:02d}. After {}".format(target_index + 1, self._label_for_scenario(self.playlist[target_index]))
        return "After full playlist"

    def _entry_from_item(self, item, fallback=None):
        if isinstance(item, str):
            path = item
            label = Path(path).stem
        elif isinstance(item, dict):
            path = str(item.get("script_path") or (fallback or {}).get("script_path") or "").strip()
            label = str(item.get("label") or Path(path).stem or "questionnaire").strip()
        else:
            return None
        if label.endswith("_survey"):
            label = label[:-7]
        return {"label": label or "questionnaire", "script_path": path}

    def _load_schedule_entries(self):
        entries = {self._target_key(idx): [] for idx in range(len(self.playlist))}
        entries["after_session"] = []
        item = self._selected_library_playlist()
        if item is not None:
            questionnaire = item.get("questionnaire") if isinstance(item.get("questionnaire"), dict) else {}
            schedule = questionnaire.get("schedule") if isinstance(questionnaire.get("schedule"), list) else []
        else:
            try:
                schedule = self.owner.questionnaire_schedule()
            except Exception:
                schedule = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            phase = str(item.get("phase", item.get("when", "after_scenario")) or "after_scenario").strip().lower()
            phase = phase.replace("-", "_").replace(" ", "_")
            target_key = None
            if phase in ("after", "after_session", "session_end"):
                target_key = "after_session"
            elif phase in ("after_scenario", "after_scenarios", "scenario_end", "between", "between_scenario", "between_scenarios"):
                index_value = item.get("scenario_index", item.get("after_scenario_index", None))
                if index_value in (None, "") and item.get("scenario_name"):
                    try:
                        index_value = self.playlist.index(str(item.get("scenario_name"))) + 1
                    except ValueError:
                        index_value = None
                try:
                    index_int = int(index_value)
                except Exception:
                    index_int = 0
                if 1 <= index_int <= len(self.playlist):
                    target_key = "after_scenario:{}".format(index_int)
            if target_key is None or target_key not in entries:
                continue
            nested = item.get("questionnaires", None)
            if nested is None:
                entry = self._entry_from_item(item)
                if entry:
                    entries[target_key].append(entry)
            elif isinstance(nested, list):
                for nested_item in nested:
                    entry = self._entry_from_item(nested_item, fallback=item)
                    if entry:
                        entries[target_key].append(entry)
        return entries

    def _default_survey_dir(self):
        root = Path.home() / "study_results" / "custom_surveys"
        return root if root.exists() else Path.home()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk
        root = ttk.Frame(self.top, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=2, uniform="builder")
        root.columnconfigure(1, weight=3, uniform="builder")
        root.columnconfigure(2, weight=3, uniform="builder")
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Playlist Questionnaire Schedule", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Playlist").grid(row=0, column=2, sticky="e", padx=(12, 6))
        self.playlist_combo = ttk.Combobox(header, values=self._playlist_combo_values(), state="readonly", width=36)
        self.playlist_combo.grid(row=0, column=3, sticky="e")
        self.playlist_combo.current(0)
        self.playlist_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_playlist_selected())

        left = ttk.LabelFrame(root, text="1. Pick Target", padding=8)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.target_box = tk.Listbox(left, exportselection=False, height=14)
        style_inkspace_listbox(self.target_box)
        self.target_box.grid(row=0, column=0, sticky="nsew")
        self.target_box.bind("<<ListboxSelect>>", lambda _event: self._select_target_from_list())

        target_buttons = ttk.Frame(left)
        target_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        target_buttons.columnconfigure(0, weight=1)
        target_buttons.columnconfigure(1, weight=1)
        ttk.Button(target_buttons, text="Edit Playlist...", command=self.open_playlist_builder).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(target_buttons, text="Refresh", command=self.refresh_playlist).grid(row=0, column=1, sticky="ew")

        library = ttk.LabelFrame(root, text="2. Survey Library (double-click to add)", padding=8)
        library.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(0, weight=1)
        self.library_box = tk.Listbox(library, exportselection=False, height=14, selectmode="extended")
        style_inkspace_listbox(self.library_box)
        self.library_box.grid(row=0, column=0, sticky="nsew")
        self.library_box.bind("<Double-1>", lambda _event: self.add_library_selection())
        lib_buttons = ttk.Frame(library)
        lib_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        lib_buttons.columnconfigure(0, weight=1)
        lib_buttons.columnconfigure(1, weight=1)
        ttk.Button(
            lib_buttons,
            text="Add to Target",
            command=self.add_library_selection,
            style="Accent.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            lib_buttons,
            text="Add After Every Scenario",
            command=lambda: self.add_library_selection(to_all=True),
        ).grid(row=0, column=1, sticky="ew")
        ttk.Button(lib_buttons, text="Generate from Prompt...", command=self.generate_survey_from_prompt).grid(
            row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0)
        )
        ttk.Button(lib_buttons, text="Open Generator", command=self.owner.launch_questionnaire).grid(
            row=1, column=1, sticky="ew", pady=(6, 0)
        )
        ttk.Button(lib_buttons, text="Refresh Library", command=self._refresh_library).grid(
            row=2, column=0, sticky="ew", padx=(0, 6), pady=(6, 0)
        )
        ttk.Button(lib_buttons, text="Browse Files...", command=self.add_surveys).grid(
            row=2, column=1, sticky="ew", pady=(6, 0)
        )

        right = ttk.LabelFrame(root, text="3. Questionnaires For Selected Target", padding=8)
        right.grid(row=1, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.selected_target_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.selected_target_var, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.questionnaire_box = tk.Listbox(right, exportselection=False, height=14)
        style_inkspace_listbox(self.questionnaire_box)
        self.questionnaire_box.grid(row=1, column=0, sticky="nsew", pady=(6, 8))

        q_buttons = ttk.Frame(right)
        q_buttons.grid(row=2, column=0, sticky="ew")
        for col in range(3):
            q_buttons.columnconfigure(col, weight=1)
        ttk.Button(q_buttons, text="Remove", command=self.remove_selected).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(q_buttons, text="Up", command=lambda: self.move_selected(-1)).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(q_buttons, text="Down", command=lambda: self.move_selected(1)).grid(row=0, column=2, sticky="ew")

        bottom = ttk.Frame(root)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        ttk.Button(bottom, text="Save Schedule", command=self.save, style="Accent.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(bottom, text="Close", command=self.close).grid(row=0, column=1, sticky="ew")

    def _select_target_from_list(self):
        selection = self.target_box.curselection()
        if not selection:
            return
        self.selected_target_index = int(selection[0])
        self._refresh_questionnaires()

    def _refresh_targets(self):
        self.target_box.delete(0, "end")
        total = len(self.playlist) + 1
        for idx in range(total):
            key = self._target_key(idx)
            count = len(self.entries.get(key, []))
            self.target_box.insert("end", "{}  [{}]".format(self._target_title(idx), count))
        if total:
            self.selected_target_index = max(0, min(self.selected_target_index, total - 1))
            self.target_box.selection_clear(0, "end")
            self.target_box.selection_set(self.selected_target_index)
            self.target_box.activate(self.selected_target_index)

    def _refresh_questionnaires(self):
        key = self._target_key(self.selected_target_index)
        self.selected_target_var.set(self._target_title(self.selected_target_index))
        self.questionnaire_box.delete(0, "end")
        for idx, entry in enumerate(self.entries.get(key, []), start=1):
            self.questionnaire_box.insert(
                "end",
                "{:02d}. {} | {}".format(idx, entry.get("label", "questionnaire"), entry.get("script_path", "")),
            )

    def _entries_snapshot(self):
        return json.dumps(self.entries, sort_keys=True, ensure_ascii=False)

    def _scan_survey_library(self):
        custom_root = Path.home() / "study_results" / "custom_surveys"
        generated_root = RUNTIME_DIR / "generated_surveys"
        candidates = []
        try:
            if custom_root.exists():
                candidates.extend(custom_root.rglob("*_survey.py"))
        except Exception:
            pass
        try:
            if generated_root.exists():
                candidates.extend(generated_root.rglob("*_survey.py"))
                candidates.extend(generated_root.rglob("*.survey.json"))
        except Exception:
            pass
        items = []
        seen = set()
        for path in candidates:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            label = path.stem
            if label.endswith("_survey"):
                label = label[: -len("_survey")]
            if label.endswith(".survey"):
                label = label[: -len(".survey")]
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            group = path.parent.name
            for scan_root in (custom_root, generated_root):
                try:
                    parts = path.relative_to(scan_root).parts
                except Exception:
                    continue
                # group = first subfolder under the scan root; "" = saved at root (ungrouped)
                group = parts[0] if len(parts) > 1 else ""
                break
            items.append({"label": label or "questionnaire", "path": str(path), "mtime": mtime, "group": group})
        # cluster by group (ungrouped first), newest first inside each group
        items.sort(key=lambda item: (item["group"].lower(), -item["mtime"]))
        return items

    def _refresh_library(self):
        self.library_items = self._scan_survey_library()
        self.library_box.delete(0, "end")
        for item in self.library_items:
            stamp = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(item["mtime"]))
                if item["mtime"]
                else "?"
            )
            group = item.get("group") or "(no group)"
            self.library_box.insert("end", "[{}]  {}  |  {}".format(group, item["label"], stamp))
        if not self.library_items:
            self.library_box.insert("end", "(no surveys found; use Generate from Prompt or Browse Files)")

    def _selected_library_entries(self):
        if not self.library_items:
            return []
        entries = []
        for index in self.library_box.curselection():
            if 0 <= int(index) < len(self.library_items):
                item = self.library_items[int(index)]
                entries.append({"label": item["label"], "script_path": item["path"]})
        return entries

    def add_library_selection(self, to_all=False):
        entries = self._selected_library_entries()
        if not entries:
            self.owner.messagebox.showinfo(
                "Add Survey", "Select one or more surveys in the library first.", parent=self.top
            )
            return
        if to_all:
            if not self.playlist:
                self.owner.messagebox.showinfo(
                    "Add Survey", "The playlist is empty; edit the playlist first.", parent=self.top
                )
                return
            keys = ["after_scenario:{}".format(idx + 1) for idx in range(len(self.playlist))]
        else:
            keys = [self._target_key(self.selected_target_index)]
        for key in keys:
            for entry in entries:
                self.entries.setdefault(key, []).append(dict(entry))
        self._refresh_targets()
        self._refresh_questionnaires()

    def generate_survey_from_prompt(self):
        """NL -> survey via the shared generator; adds the result to the selected target."""

        def on_saved(entry):
            key = self._target_key(self.selected_target_index)
            self.entries.setdefault(key, []).append(dict(entry))
            self._refresh_targets()
            self._refresh_questionnaires()
            self._refresh_library()

        open_generate_survey_dialog(self.owner, self.top, on_saved=on_saved)

    def add_surveys(self):
        key = self._target_key(self.selected_target_index)
        paths = self.filedialog.askopenfilenames(
            parent=self.top,
            title="Select generated questionnaire scripts",
            initialdir=str(self._default_survey_dir()),
            filetypes=(("Generated survey scripts", "*_survey.py"), ("Python scripts", "*.py"), ("All files", "*.*")),
        )
        if not paths:
            return
        for path_text in paths:
            path = Path(path_text)
            label = path.stem[:-7] if path.stem.endswith("_survey") else path.stem
            self.entries.setdefault(key, []).append({"label": label or "questionnaire", "script_path": str(path)})
        self._refresh_targets()
        self._refresh_questionnaires()

    def remove_selected(self):
        key = self._target_key(self.selected_target_index)
        rows = self.entries.get(key, [])
        for index in sorted((int(i) for i in self.questionnaire_box.curselection()), reverse=True):
            if 0 <= index < len(rows):
                del rows[index]
        self._refresh_targets()
        self._refresh_questionnaires()

    def move_selected(self, delta):
        key = self._target_key(self.selected_target_index)
        rows = self.entries.get(key, [])
        selection = self.questionnaire_box.curselection()
        if len(selection) != 1:
            return
        index = int(selection[0])
        new_index = index + int(delta)
        if not (0 <= index < len(rows) and 0 <= new_index < len(rows)):
            return
        rows[index], rows[new_index] = rows[new_index], rows[index]
        self._refresh_questionnaires()
        self.questionnaire_box.selection_set(new_index)
        self.questionnaire_box.activate(new_index)

    def _on_playlist_selected(self):
        index = self.playlist_combo.current()
        new_id = "" if index <= 0 else str(self.playlist_library[index - 1].get("id") or "")
        if new_id == self.selected_playlist_id:
            return
        if self._entries_snapshot() != self._saved_snapshot:
            if not self.owner.messagebox.askyesno(
                "Questionnaire Schedule",
                "Switching playlist discards unsaved schedule changes. Continue?",
                parent=self.top,
            ):
                # revert the combobox to the current selection
                current = 0
                if self.selected_playlist_id:
                    for i, item in enumerate(self.playlist_library):
                        if str(item.get("id") or "") == self.selected_playlist_id:
                            current = i + 1
                            break
                self.playlist_combo.current(current)
                return
        self.selected_playlist_id = new_id
        self.playlist = self._load_playlist()
        self.entries = self._load_schedule_entries()
        self._saved_snapshot = self._entries_snapshot()
        self.selected_target_index = 0
        self._refresh_targets()
        self._refresh_questionnaires()

    def refresh_playlist(self):
        self.playlist_library = load_playlist_library()
        # keep the same playlist selected after the library reloads
        values = self._playlist_combo_values()
        self.playlist_combo.configure(values=values)
        current = 0
        if self.selected_playlist_id:
            for i, item in enumerate(self.playlist_library):
                if str(item.get("id") or "") == self.selected_playlist_id:
                    current = i + 1
                    break
            else:
                self.selected_playlist_id = ""  # playlist was deleted
        self.playlist_combo.current(current)
        self.playlist = self._load_playlist()
        current_entries = dict(self.entries)
        self.entries = {self._target_key(idx): current_entries.get(self._target_key(idx), []) for idx in range(len(self.playlist))}
        self.entries["after_session"] = current_entries.get("after_session", [])
        self.selected_target_index = 0
        self._refresh_targets()
        self._refresh_questionnaires()

    def open_playlist_builder(self):
        try:
            SessionBuilderDialog(self.owner)
            self.refresh_playlist()
        except Exception as exc:
            write_exception(exc)
            self.owner.messagebox.showerror("Questionnaire Schedule", str(exc))

    def _build_schedule(self):
        schedule = []
        for idx, scenario_name in enumerate(self.playlist, start=1):
            rows = self.entries.get("after_scenario:{}".format(idx), [])
            if not rows:
                continue
            schedule.append(
                {
                    "phase": "after_scenario",
                    "scenario_index": idx,
                    "scenario_name": scenario_name,
                    "questionnaires": [
                        {"label": str(row.get("label") or "questionnaire"), "script_path": str(row.get("script_path") or "")}
                        for row in rows
                    ],
                }
            )
        rows = self.entries.get("after_session", [])
        if rows:
            schedule.append(
                {
                    "phase": "after_session",
                    "questionnaires": [
                        {"label": str(row.get("label") or "questionnaire"), "script_path": str(row.get("script_path") or "")}
                        for row in rows
                    ],
                }
            )
        return schedule

    def save(self):
        schedule = self._build_schedule()
        item = self._selected_library_playlist()
        if item is not None:
            # Save the schedule INTO the selected saved playlist's library entry
            # so it travels with that playlist.
            try:
                questionnaire = item.get("questionnaire") if isinstance(item.get("questionnaire"), dict) else {}
                questionnaire["enabled"] = bool(schedule)
                questionnaire["schedule"] = schedule
                item["questionnaire"] = questionnaire
                save_playlist_library(self.playlist_library)
            except Exception as exc:
                write_exception(exc)
                self.owner.messagebox.showerror("Questionnaire Schedule", str(exc))
                return
            self._saved_snapshot = self._entries_snapshot()
            playlist_name = str(item.get("name") or "playlist")
            if self._active_playlist_id() == str(item.get("id") or ""):
                # also the ACTIVE playlist -> apply to the live session below too
                self.owner.emit(
                    "Saved questionnaire schedule into playlist '{}' ({} trigger(s)); also applying to the active session.".format(
                        playlist_name, len(schedule)
                    )
                )
            else:
                self.owner.emit(
                    "Saved questionnaire schedule into playlist '{}' ({} trigger(s)). Load that playlist to run it.".format(
                        playlist_name, len(schedule)
                    )
                )
                self.close()
                return
        self.owner.questionnaire_enabled_var.set(bool(schedule))
        self.owner.questionnaire_before_var.set(False)
        self.owner.questionnaire_between_var.set(False)
        self.owner.questionnaire_after_var.set(False)
        self.owner.questionnaire_wait_var.set(True)
        self.owner.start_saved_playlist_var.set(True)
        self.owner.questionnaire_schedule_var.set(json.dumps(schedule, ensure_ascii=False, indent=2))
        widget = getattr(self.owner, "questionnaire_schedule_text_widget", None)
        if widget is not None:
            try:
                widget.delete("1.0", "end")
                widget.insert("1.0", self.owner.questionnaire_schedule_var.get())
            except Exception:
                pass
        try:
            self.owner._sync_level3_config_from_extension_settings()
            self.owner.refresh_level3_session_summary()
            self.owner.refresh_extension_status()
            if callable(self.on_saved):
                self.on_saved()
            self.owner.emit("Saved questionnaire schedule with {} trigger(s).".format(len(schedule)))
            self._saved_snapshot = self._entries_snapshot()
            self.close()
        except Exception as exc:
            write_exception(exc)
            self.owner.messagebox.showerror("Questionnaire Schedule", str(exc))

    def close(self):
        if self._entries_snapshot() != self._saved_snapshot:
            if not self.owner.messagebox.askyesno(
                "Questionnaire Schedule",
                "The schedule has unsaved changes. Discard them?",
                parent=self.top,
            ):
                return
        self.top.destroy()


class ExtensionsPanel(object):
    """Extensions control panel embedded in the main window's right side."""

    MODULE_TABS = {
        "session": 0,
        "playlist": 1,
        "questionnaire": 2,
        "aoi": 3,
        "lsl": 3,
        "reader": 3,
        "emotibit": 3,
        "recorder": 3,
        "manual": 3,
    }

    def __init__(self, owner, parent):
        import tkinter as tk
        from tkinter import ttk

        self.owner = owner
        self.tk = tk
        self.ttk = ttk
        self.selected_module_key = "session"
        self._build_ui(parent)
        self._select_module(self.selected_module_key)
        self._schedule_module_refresh()

    def _build_ui(self, parent):
        ttk = self.ttk
        root = ttk.Frame(parent)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Experiment Extensions", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Refresh Summary", command=self._refresh_after_change).grid(
            row=0, column=1, rowspan=2, sticky="e", padx=(8, 0)
        )
        ttk.Label(
            header,
            textvariable=self.owner.extension_status_var,
            style="Muted.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        tabs = (
            ("Session", self._build_session_tab),
            ("Playlist", self._build_playlist_detail),
            ("Questionnaire", self._build_questionnaire_detail),
            ("Devices & Streams", self._build_devices_tab),
        )
        for title, builder in tabs:
            page = ttk.Frame(self.notebook, padding=10)
            page.columnconfigure(0, weight=1)
            self.notebook.add(page, text=title)
            builder(page)

    def _build_session_tab(self, parent):
        ttk = self.ttk
        parent.columnconfigure(0, weight=1)
        summary = ttk.LabelFrame(parent, text="Overview", padding=8)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        summary.columnconfigure(0, weight=1)
        ttk.Label(
            summary,
            textvariable=self.owner.level3_session_summary_var,
            wraplength=860,
            justify="left",
        ).grid(row=0, column=0, sticky="ew")
        body = ttk.Frame(parent)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self._build_session_detail(body)

    def _build_devices_tab(self, parent):
        ttk = self.ttk
        # Three independent stacks so one tall device block cannot inflate the
        # whole tab through shared grid rows.
        columns = []
        for col in range(3):
            parent.columnconfigure(col, weight=1, uniform="devices")
            holder = ttk.Frame(parent)
            holder.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            holder.columnconfigure(0, weight=1)
            columns.append(holder)
        parent.rowconfigure(0, weight=1)

        def add_block(column, title, builder):
            frame = ttk.LabelFrame(columns[column], text=title, padding=8)
            frame.pack(fill="x", pady=(0, 8))
            builder(frame)

        add_block(0, "Neon AOI", self._build_aoi_detail)
        add_block(0, "LSL Markers", self._build_lsl_detail)
        add_block(1, "EmotiBit", self._build_emotibit_detail)
        add_block(1, "Lab Recorder", self._build_recorder_detail)
        add_block(2, "CARLA LSL Data Reader", self._build_reader_detail)
        add_block(2, "Manual TOR Test", self._build_manual_detail)

    def _refresh_after_change(self):
        self.owner.refresh_extension_status()
        self.owner.refresh_level3_session_summary()
        self._refresh_playlist_summary()

    def _schedule_module_refresh(self):
        try:
            self.owner.refresh_extension_status()
            self._refresh_playlist_summary()
            self.owner.root.after(1000, self._schedule_module_refresh)
        except Exception:
            pass

    def _select_module(self, key):
        self.selected_module_key = str(key)
        try:
            self.notebook.select(self.MODULE_TABS.get(str(key), 0))
        except Exception:
            pass

    def _grid_buttons(self, parent, row, buttons):
        for col in range(len(buttons)):
            parent.columnconfigure(col, weight=1)
        for col, (label, command, style) in enumerate(buttons):
            ttk_style = style or "TButton"
            self.ttk.Button(parent, text=label, command=command, style=ttk_style).grid(
                row=row, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0)
            )

    def _build_lsl_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            parent,
            text="Enable LSL markers for Start Session",
            variable=self.owner.lsl_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text="Stream").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(parent, textvariable=self.owner.lsl_stream_var).grid(row=1, column=1, sticky="ew", pady=(10, 0))

    def _build_reader_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            parent,
            text="Auto-start data reader with Start Session",
            variable=self.owner.lsl_data_reader_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(parent, text="Streams").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(parent, textvariable=self.owner.lsl_data_reader_streams_var).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(10, 0))
        ttk.Button(buttons, text="Launch", command=self.owner.launch_lsl_data_reader).pack(side="left")
        ttk.Button(buttons, text="Stop", command=self.owner.stop_lsl_data_reader).pack(side="left", padx=(6, 0))
        ttk.Label(parent, textvariable=self.owner.lsl_data_reader_output_var, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

    def _build_aoi_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            parent,
            text="Auto-start AOI with Start Session",
            variable=self.owner.aoi_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text="Neon IP (blank = auto)").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(parent, textvariable=self.owner.neon_ip_var).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(parent, text="Tag layout").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(parent, textvariable=self.owner.aoi_corner_ids_var).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(parent, text="Alert +s").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(parent, textvariable=self.owner.tor_alert_delay_var, width=10).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            parent,
            text="Enable TOR decision tree / Neon AOI gate",
            variable=self.owner.tor_decision_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        toggles = ttk.Frame(parent)
        toggles.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(toggles, text="Latency log", variable=self.owner.aoi_latency_log_var, command=self._refresh_after_change).pack(side="left")
        ttk.Checkbutton(toggles, text="No voice", variable=self.owner.aoi_no_voice_var, command=self._refresh_after_change).pack(side="left", padx=(12, 0))
        buttons = ttk.Frame(parent)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._grid_buttons(buttons, 0, (
            ("Launch AOI / LSL", self.owner.launch_aoi, None),
            ("Stop AOI / LSL", self.owner.stop_aoi, None),
        ))
        ttk.Label(
            parent,
            text="Leave IP blank to discover Neon when AOI starts. Keep Neon Companion open on the same Ethernet / Wi-Fi LAN. Enter an IP only for a manual connection.",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_emotibit_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            parent,
            text="Open Oscilloscope with Start Session",
            variable=self.owner.emotibit_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text="App").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Label(parent, text=str(EMOTIBIT_OSCILLOSCOPE_EXE), style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(10, 0))
        buttons = ttk.Frame(parent)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._grid_buttons(buttons, 0, (
            ("Launch EmotiBit", self.owner.launch_emotibit, None),
            ("Stop EmotiBit", self.owner.stop_emotibit, None),
        ))
        ttk.Label(parent, text="Movement detection", style="Section.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 4)
        )
        movement = ttk.Frame(parent)
        movement.grid(row=4, column=0, columnspan=2, sticky="ew")
        for col in range(6):
            movement.columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)
        ttk.Label(movement, text="Baseline s").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(movement, textvariable=self.owner.emotibit_baseline_var, width=8).grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(movement, text="Abs threshold").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(movement, textvariable=self.owner.emotibit_abs_threshold_var, width=8).grid(row=0, column=3, sticky="w", padx=(0, 12))
        ttk.Label(movement, text="Z threshold").grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Entry(movement, textvariable=self.owner.emotibit_z_threshold_var, width=8).grid(row=0, column=5, sticky="w")
        presets = ttk.Frame(parent)
        presets.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(presets, text="Medium", command=lambda: self._set_emotibit_movement_preset(2.0, 0.9, 5.5)).pack(side="left")
        ttk.Button(presets, text="Large movement", command=lambda: self._set_emotibit_movement_preset(2.0, 1.5, 8.0)).pack(side="left", padx=(8, 0))
        ttk.Button(presets, text="Very large", command=lambda: self._set_emotibit_movement_preset(2.0, 2.5, 10.0)).pack(side="left", padx=(8, 0))
        ttk.Label(
            parent,
            text="Flags head/body movement during takeover; written into the session config when you Save Playlist.",
            style="Muted.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _set_emotibit_movement_preset(self, baseline_s, abs_threshold, z_threshold):
        self.owner.emotibit_baseline_var.set(str(baseline_s))
        self.owner.emotibit_abs_threshold_var.set(str(abs_threshold))
        self.owner.emotibit_z_threshold_var.set(str(z_threshold))
        self._refresh_after_change()

    def _build_recorder_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            parent,
            text="Open LabRecorder with Start Session",
            variable=self.owner.lab_recorder_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text="App").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Label(parent, text=str(LAB_RECORDER_EXE), style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(10, 0))
        buttons = ttk.Frame(parent)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._grid_buttons(buttons, 0, (
            ("Launch LabRecorder", self.owner.launch_lab_recorder, None),
            ("Stop LabRecorder", self.owner.stop_lab_recorder, None),
        ))

    def _build_questionnaire_detail(self, parent):
        tk = self.tk
        ttk = self.ttk
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)
        ttk.Checkbutton(
            parent,
            text="Run questionnaire with saved playlist",
            variable=self.owner.questionnaire_enabled_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        phase_row = ttk.Frame(parent)
        phase_row.grid(row=0, column=2, columnspan=2, sticky="e")
        ttk.Checkbutton(phase_row, text="Before", variable=self.owner.questionnaire_before_var, command=self._refresh_after_change).pack(side="left")
        ttk.Checkbutton(phase_row, text="Between", variable=self.owner.questionnaire_between_var, command=self._refresh_after_change).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(phase_row, text="After", variable=self.owner.questionnaire_after_var, command=self._refresh_after_change).pack(side="left", padx=(8, 0))
        ttk.Label(parent, text="Language").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Combobox(
            parent,
            textvariable=self.owner.questionnaire_language_var,
            values=("zh", "en"),
            state="readonly",
            width=8,
        ).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Checkbutton(parent, text="Wait until closed", variable=self.owner.questionnaire_wait_var, command=self._refresh_after_change).grid(
            row=1, column=2, sticky="w", padx=(8, 8), pady=(10, 0)
        )
        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Launch", command=self.owner.launch_questionnaire).pack(side="left")
        ttk.Button(buttons, text="Stop", command=self.owner.stop_questionnaire).pack(side="left", padx=(6, 0))
        builder_row = ttk.Frame(parent)
        builder_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        for col in range(4):
            builder_row.columnconfigure(col, weight=1)
        ttk.Button(
            builder_row,
            text="Generate Survey...",
            command=self._generate_survey_from_panel,
            style="Accent.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(
            builder_row,
            text="Build Schedule...",
            command=lambda: self.owner.open_questionnaire_schedule_builder(on_saved=self._refresh_after_change),
            style="Accent.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(builder_row, text="Edit Saved Playlist...", command=self.owner.open_level3_session_builder).grid(
            row=0, column=2, sticky="ew", padx=(0, 8)
        )
        ttk.Button(builder_row, text="Questionnaire-only Test", command=self.owner.enable_questionnaire_only_mode).grid(
            row=0, column=3, sticky="ew"
        )
        ttk.Label(parent, text="Schedule JSON").grid(row=3, column=0, sticky="nw", padx=(0, 8), pady=(10, 0))
        schedule_text = tk.Text(parent, height=8, wrap="none", undo=True)
        style_inkspace_text(schedule_text)
        schedule_text.insert("1.0", self.owner.questionnaire_schedule_var.get().strip() or "[]")
        schedule_text.grid(row=3, column=1, columnspan=3, sticky="nsew", pady=(10, 0))
        parent.rowconfigure(3, weight=1)
        self.owner.questionnaire_schedule_text_widget = schedule_text

        def on_schedule_change(_event=None):
            self.owner.questionnaire_schedule_var.set(schedule_text.get("1.0", "end").strip())
            self._refresh_after_change()

        schedule_text.bind("<KeyRelease>", on_schedule_change)

    def _generate_survey_from_panel(self):
        def on_saved(entry):
            self.owner.emit("Generated survey saved: {}".format(entry.get("script_path")))
            self._refresh_after_change()

        open_generate_survey_dialog(self.owner, self.owner.root, on_saved=on_saved)

    def _active_playlist_info(self):
        """Return (active_playlist_name, scenario_count, saved_playlist_count)."""
        name = ""
        count = 0
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            name = str((cfg.get("playlist") or {}).get("name") or "").strip()
            count = len([n for n in cfg.get("scenario_names", []) if str(n).strip()])
        except Exception:
            pass
        try:
            saved = len(load_playlist_library())
        except Exception:
            saved = 0
        return name, count, saved

    def _refresh_playlist_summary(self):
        var = getattr(self, "_playlist_active_var", None)
        if var is None:
            return
        name, count, saved = self._active_playlist_info()
        text = "Active playlist:  {}   ({} scenario(s))  |  {} saved".format(name or "(none)", count, saved)
        if var.get() != text:
            var.set(text)
        self._refresh_playlist_library_box()

    def _refresh_playlist_library_box(self):
        box = getattr(self, "_playlist_lib_box", None)
        if box is None:
            return
        try:
            items = load_playlist_library()
        except Exception:
            items = []
        if items == getattr(self, "_playlist_lib_items", None) and box.size():
            return
        self._playlist_lib_items = items
        box.delete(0, "end")
        for item in items:
            cnt = len(item.get("scenario_names") or [])
            label = "{}  ({} scn)".format(str(item.get("name") or "untitled"), cnt)
            group = str(item.get("group") or "").strip()
            if group:
                label += "  [{}]".format(group)
            box.insert("end", label)
        if not items:
            box.insert("end", "(no saved playlists yet - click Create New Playlist)")

    def _open_selected_library_playlist(self):
        """Open the builder with the playlist selected in the panel list
        loaded; falls back to the active playlist when nothing is selected."""
        box = getattr(self, "_playlist_lib_box", None)
        items = getattr(self, "_playlist_lib_items", [])
        item = None
        if box is not None and items:
            selection = box.curselection()
            if selection and selection[0] < len(items):
                item = items[selection[0]]
        self.owner.open_level3_session_builder(load_item=item)

    def _delete_selected_library_playlist(self):
        from tkinter import messagebox
        box = getattr(self, "_playlist_lib_box", None)
        items = getattr(self, "_playlist_lib_items", [])
        if box is None or not items:
            return
        selection = box.curselection()
        if not selection or selection[0] >= len(items):
            return
        item = items[selection[0]]
        name = str(item.get("name") or "untitled")
        if not messagebox.askyesno("Delete playlist", "Delete saved playlist '{}'?".format(name), parent=self.owner.root):
            return
        remaining = [it for index, it in enumerate(items) if index != selection[0]]
        try:
            save_playlist_library(remaining)
        except Exception as exc:
            write_exception(exc)
            messagebox.showerror("Delete playlist", str(exc), parent=self.owner.root)
            return
        self._refresh_playlist_library_box()
        if hasattr(self.owner, "emit"):
            self.owner.emit("Deleted saved playlist: {}".format(name))
        self._refresh_after_change()

    def _build_playlist_detail(self, parent):
        tk = self.tk
        ttk = self.ttk
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        self._playlist_active_var = self.tk.StringVar(value="")
        ttk.Label(
            parent,
            textvariable=self._playlist_active_var,
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")

        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.columnconfigure(2, weight=0)
        ttk.Button(
            actions, text="Build / Edit Playlist...",
            command=self.owner.open_level3_session_builder, style="Accent.TButton",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            actions, text="Create New Playlist...",
            command=lambda: self.owner.open_level3_session_builder(new_playlist=True),
        ).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self._refresh_after_change).grid(row=0, column=2, sticky="e")

        ttk.Label(parent, text="Saved playlists", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 4))
        lib_box = tk.Listbox(parent, exportselection=False, height=6)
        style_inkspace_listbox(lib_box)
        lib_box.grid(row=3, column=0, sticky="nsew")
        lib_box.bind("<Double-1>", lambda _event: self._open_selected_library_playlist())
        self._playlist_lib_box = lib_box
        self._playlist_lib_items = []
        self._refresh_playlist_summary()

        lib_buttons = ttk.Frame(parent)
        lib_buttons.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        lib_buttons.columnconfigure(0, weight=1)
        lib_buttons.columnconfigure(1, weight=1)
        ttk.Button(lib_buttons, text="Open in Builder", command=self._open_selected_library_playlist).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(lib_buttons, text="Delete", command=self._delete_selected_library_playlist).grid(row=0, column=1, sticky="ew")

        run = ttk.Frame(parent)
        run.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        run.columnconfigure(0, weight=1)
        run.columnconfigure(1, weight=1)
        ttk.Button(run, text="Start Session", command=self.owner.start_session, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(run, text="Stop Session", command=self.owner.stop_level3_session).grid(row=0, column=1, sticky="ew")

        ttk.Label(
            parent,
            text="Tip: per-participant counterbalancing (Latin square) lives inside Build / Edit Playlist; each scenario uses its own built-in TOR.",
            style="Muted.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=7, column=0, sticky="w", pady=(10, 0))

    def _build_session_detail(self, parent):
        ttk = self.ttk
        for col in range(4):
            parent.columnconfigure(col, weight=1)
        ttk.Button(parent, text="Start Session", command=self.owner.start_session, style="Accent.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        self.owner.restart_session_button = ttk.Button(parent, text="Restart Session", command=self.owner.restart_level3_session)
        self.owner.restart_session_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.owner.stop_session_button = ttk.Button(parent, text="Stop Session", command=self.owner.stop_level3_session)
        self.owner.stop_session_button.grid(row=0, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(parent, text="Edit Saved Playlist...", command=self.owner.open_level3_session_builder).grid(row=0, column=3, sticky="ew")

        # --- Run ---
        ttk.Separator(parent, orient="horizontal").grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(parent, text="Run", style="Section.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            parent,
            text="Wait for manual confirmation after playlist/scenario is ready",
            variable=self.owner.confirm_before_scenario_var,
            command=self._refresh_after_change,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.owner.confirm_start_button = ttk.Button(parent, text="Start Run", command=self.owner.confirm_pending_scenario_start)
        self.owner.confirm_start_button.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.owner._set_confirm_start_button_enabled(self.owner.session_start_confirm_pending)
        ttk.Label(parent, textvariable=self.owner.scenario_start_confirm_state_var, style="Muted.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )

        # --- Takeover ---
        ttk.Separator(parent, orient="horizontal").grid(row=6, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(parent, text="Takeover", style="Section.TLabel").grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            parent,
            text="Use Logitech G29 takeover",
            variable=self.owner.g29_takeover_var,
            command=self._refresh_after_change,
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(
            parent,
            text="Background vehicles (Queen) live in the top bar; the manual TOR trigger and visual-test toggles are under Devices & Streams → Manual TOR Test; the TOR decision / Neon AOI gate is under Devices & Streams → Neon AOI.",
            style="Muted.TLabel",
            wraplength=860,
            justify="left",
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(12, 0))

    def _build_manual_detail(self, parent):
        ttk = self.ttk
        parent.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            parent,
            text="TOR visual test (no Neon)",
            variable=self.owner.tor_visual_test_var,
            command=self._refresh_after_change,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            parent,
            text="Mirror HUD test",
            variable=self.owner.tor_mirror_hud_test_var,
            command=self._refresh_after_change,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(parent)
        buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._grid_buttons(buttons, 0, (
            ("Manual TOR Test", self.owner.manual_tor_test, "Accent.TButton"),
        ))


def load_survey_script_definition(script_path):
    path = Path(str(script_path or "")).expanduser()
    if not path.exists():
        raise RuntimeError("Survey script not found: {}".format(path))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definition = None
    response_basename = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if "SURVEY_DEFINITION" in names:
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "loads"
                and value.args
            ):
                definition = json.loads(ast.literal_eval(value.args[0]))
        elif "RESPONSE_BASENAME" in names:
            response_basename = str(ast.literal_eval(node.value))
    if not isinstance(definition, dict):
        raise RuntimeError("Survey definition not found in {}".format(path))
    if not response_basename:
        response_basename = make_safe_slug(definition.get("title", "survey"), "survey")
    return definition, response_basename, path


class LauncherQuestionnaireDialog(object):
    def __init__(self, owner, payload, on_done):
        self.owner = owner
        self.payload = dict(payload or {})
        self.on_done = on_done
        self.tk = owner.tk
        self.ttk = owner.ttk
        self.definition, self.response_basename, self.script_path = load_survey_script_definition(
            self.payload.get("script_path")
        )
        self.answers = []
        self.top = self.tk.Toplevel(owner.root)
        self.top.title(self.definition.get("title", "Questionnaire"))
        self.top.geometry("980x760+80+60")
        self.top.minsize(760, 560)
        self.top.configure(bg=SURVEY_STYLE["window_bg"])
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        self._build()
        self.top.after(200, self._bring_front)

    def _bring_front(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
            self.top.after(1500, lambda: self.top.attributes("-topmost", False))
        except Exception:
            pass

    def _build(self):
        tk = self.tk
        ttk = self.ttk
        style = SURVEY_STYLE
        font_family = style["font_family"]
        root = tk.Frame(self.top, bg=style["window_bg"])
        root.pack(fill="both", expand=True, padx=16, pady=16)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # --- 1. Header card: title + instructions + red close button.
        # The close button mirrors the window-manager close exactly: destroy
        # the Toplevel without saving and WITHOUT calling on_done.
        header_wrap = tk.Frame(root, bg=style["card_border"])
        header_wrap.grid(row=0, column=0, columnspan=2, sticky="ew")
        header = tk.Frame(header_wrap, bg=style["card_bg"])
        header.pack(fill="both", expand=True, padx=1, pady=1)
        header.columnconfigure(0, weight=1)
        self.header_instructions = survey_header_instructions(self.definition)
        tk.Label(
            header,
            text=self.definition.get("title", "Questionnaire"),
            font=(font_family, 18, "bold"),
            bg=style["card_bg"],
            fg=style["text_primary"],
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2 if self.header_instructions else 12))
        close_wrap = tk.Frame(header, bg=style["close_red"])
        close_wrap.grid(row=0, column=1, sticky="ne", padx=12, pady=12)
        self.close_button = tk.Button(
            close_wrap,
            text="✕",
            command=self.top.destroy,
            font=(font_family, 11, "bold"),
            bg=style["card_bg"],
            fg=style["close_red"],
            activebackground=style["btn_hover"],
            activeforeground=style["close_red"],
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=9,
            pady=2,
            highlightthickness=0,
        )
        self.close_button.pack(padx=1, pady=1)
        if self.header_instructions:
            tk.Label(
                header,
                text=self.header_instructions,
                font=(font_family, 10),
                bg=style["card_bg"],
                fg=style["text_muted"],
                anchor="w",
                justify="left",
                wraplength=840,
            ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        # --- 2. Static page row (no pagination implemented).
        page_row = tk.Frame(root, bg=style["window_bg"])
        page_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        tk.Label(page_row, text="Page 1 / 1", font=(font_family, 9, "bold"), bg=style["window_bg"], fg=style["accent"]).pack(anchor="w")
        tk.Label(page_row, text="Page 1", font=(font_family, 12, "bold"), bg=style["window_bg"], fg=style["text_primary"]).pack(anchor="w")

        # --- 3. Scrollable body (mousewheel bound to this canvas only).
        canvas = tk.Canvas(root, bg=style["window_bg"], highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")
        body = tk.Frame(canvas, bg=style["window_bg"])
        body.columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def resize(event):
            canvas.itemconfigure(window_id, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", resize)
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))

        for idx, question in enumerate(self.definition.get("questions", []), start=1):
            self._question_row(body, idx, question)

        # --- 4. Action row.
        actions = tk.Frame(root, bg=style["window_bg"])
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        actions.columnconfigure(0, weight=1)
        self.save_button = tk.Button(
            actions,
            text="Save Responses",
            command=self._save,
            font=(font_family, 10, "bold"),
            bg=style["save_bg"],
            fg="#ffffff",
            activebackground=style["save_hover"],
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=18,
            pady=8,
            highlightthickness=0,
        )
        self.save_button.grid(row=0, column=1, sticky="e")

    def _make_button(self, parent, label, size, padx_inner, pady_inner):
        """A flat dark button inside a 1px frame-as-border wrapper."""
        tk = self.tk
        style = SURVEY_STYLE
        wrap = tk.Frame(parent, bg=style["btn_border"])
        button = tk.Button(
            wrap,
            text=label,
            font=(style["font_family"], size, "bold"),
            bg=style["btn_bg"],
            fg=style["text_primary"],
            activebackground=style["btn_hover"],
            activeforeground=style["text_primary"],
            relief="flat",
            bd=0,
            cursor="hand2",
            highlightthickness=0,
            padx=padx_inner,
            pady=pady_inner,
        )
        button.pack(fill="both", expand=True, padx=1, pady=1)
        return wrap, button

    def _make_point_button(self, parent, label):
        """A fixed-size square scale button (number 18pt bold)."""
        tk = self.tk
        style = SURVEY_STYLE
        wrap = tk.Frame(parent, bg=style["btn_border"], width=58, height=58)
        wrap.pack_propagate(False)
        button = tk.Button(
            wrap,
            text=label,
            font=(style["font_family"], 18, "bold"),
            bg=style["btn_bg"],
            fg=style["text_primary"],
            activebackground=style["btn_hover"],
            activeforeground=style["text_primary"],
            relief="flat",
            bd=0,
            cursor="hand2",
            highlightthickness=0,
        )
        button.pack(fill="both", expand=True, padx=1, pady=1)
        return wrap, button

    @staticmethod
    def _style_buttons(items, selected):
        style = SURVEY_STYLE
        for value, wrap, button in items:
            if value in selected:
                wrap.configure(bg=style["btn_selected_border"])
                button.configure(bg=style["btn_selected_bg"])
            else:
                wrap.configure(bg=style["btn_border"])
                button.configure(bg=style["btn_bg"])

    def _question_row(self, parent, idx, question):
        tk = self.tk
        style = SURVEY_STYLE
        font_family = style["font_family"]
        q_type = str(question.get("type", "slider"))
        required = bool(question.get("required", True))
        record = {"type": q_type, "question": question, "answered": False}

        card_wrap = tk.Frame(parent, bg=style["card_border"])
        card_wrap.grid(row=idx - 1, column=0, sticky="ew", pady=(0, 10))
        card = tk.Frame(card_wrap, bg=style["card_bg"], padx=12, pady=12)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        card.columnconfigure(0, weight=1)

        # Row A: Qn + widget-type caption + Required/Optional badge.
        head = tk.Frame(card, bg=style["card_bg"])
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="Q{}".format(idx), font=(font_family, 10, "bold"), bg=style["card_bg"], fg=style["accent"]).grid(row=0, column=0, sticky="w")
        tk.Label(head, text=survey_type_label(question), font=(font_family, 10), bg=style["card_bg"], fg=style["text_muted"]).grid(row=0, column=1, sticky="w", padx=(10, 0))
        tk.Label(
            head,
            text="Required" if required else "Optional",
            font=(font_family, 8, "bold"),
            bg=style["badge_required_bg"] if required else style["badge_optional_bg"],
            fg="#ffffff" if required else style["text_muted"],
            padx=8,
            pady=2,
        ).grid(row=0, column=2, sticky="e")

        # Row B: QUESTION block with thick accent bar on the left edge.
        block_wrap = tk.Frame(card, bg=style["question_border"])
        block_wrap.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        block = tk.Frame(block_wrap, bg=style["question_bg"])
        block.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(block, bg=style["accent_bar"], width=5).pack(side="left", fill="y")
        q_body = tk.Frame(block, bg=style["question_bg"])
        q_body.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        tk.Label(q_body, text="QUESTION", font=(font_family, 8, "bold"), bg=style["question_bg"], fg=style["accent"]).pack(anchor="w")
        tk.Label(
            q_body,
            text=str(question.get("text", "")),
            font=(font_family, 13, "bold"),
            bg=style["question_bg"],
            fg=style["text_primary"],
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        if q_type in ("slider", "point_select"):
            min_v = int(question.get("scale_min", self.definition.get("scale_min", 1)))
            max_v = int(question.get("scale_max", self.definition.get("scale_max", 7)))
            # Row C: scale anchors (bare number when no label present).
            anchors = tk.Frame(card, bg=style["card_bg"])
            anchors.grid(row=2, column=0, sticky="ew", pady=(8, 0))
            anchors.columnconfigure(0, weight=1)
            anchors.columnconfigure(1, weight=1)
            tk.Label(
                anchors,
                text=survey_anchor_text(min_v, question.get("scale_min_label")),
                font=(font_family, 10),
                bg=style["card_bg"],
                fg=style["text_muted"],
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                anchors,
                text=survey_anchor_text(max_v, question.get("scale_max_label")),
                font=(font_family, 10),
                bg=style["card_bg"],
                fg=style["text_muted"],
            ).grid(row=0, column=1, sticky="e")
            var = tk.IntVar(value=int(round((min_v + max_v) / 2)))
            if q_type == "slider":
                value_label = tk.Label(card, text=str(var.get()), font=(font_family, 12, "bold"), bg=style["card_bg"], fg=style["accent"])
                value_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
                scale = tk.Scale(
                    card,
                    from_=min_v,
                    to=max_v,
                    orient="horizontal",
                    troughcolor=style["btn_bg"],
                    bg=style["accent"],
                    fg=style["accent"],
                    activebackground=style["accent"],
                    highlightthickness=0,
                    bd=0,
                    showvalue=0,
                    relief="flat",
                    sliderrelief="flat",
                    cursor="hand2",
                )
                scale.set(var.get())

                def on_slide(value, rec=record, target=var, lab=value_label):
                    target.set(int(round(float(value))))
                    lab.configure(text=str(target.get()))
                    rec["answered"] = True

                scale.configure(command=on_slide)
                scale.grid(row=4, column=0, sticky="ew", pady=(4, 0))
                record["scale"] = scale
            else:
                grid_f = tk.Frame(card, bg=style["card_bg"])
                grid_f.grid(row=3, column=0, sticky="w", pady=(8, 0))
                items = []
                for offset, value in enumerate(range(min_v, max_v + 1)):
                    wrap, button = self._make_point_button(grid_f, str(value))
                    wrap.grid(row=offset // 10, column=offset % 10, padx=(0, 6), pady=(0, 6))
                    items.append((value, wrap, button))

                def on_point(value, rec=record, target=var, btns=items):
                    target.set(value)
                    rec["answered"] = True
                    self._style_buttons(btns, {value})

                for value, _wrap, button in items:
                    button.configure(command=lambda v=value, cb=on_point: cb(v))
                record["buttons"] = items
            record["var"] = var
        elif q_type == "true_false":
            var = tk.StringVar(value="")
            row_f = tk.Frame(card, bg=style["card_bg"])
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            items = []
            for value in ("True", "False"):
                wrap, button = self._make_button(row_f, value, 11, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                items.append((value, wrap, button))

            def on_true_false(value, rec=record, target=var, btns=items):
                target.set(value)
                rec["answered"] = True
                self._style_buttons(btns, {value})

            for value, _wrap, button in items:
                button.configure(command=lambda v=value, cb=on_true_false: cb(v))
            record["var"] = var
            record["buttons"] = items
        elif q_type == "choice" and question.get("multi"):
            # Checkbutton semantics: independent BooleanVar toggle buttons;
            # the CSV cell stays the single "; "-joined selection list.
            row_f = tk.Frame(card, bg=style["card_bg"])
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            vars_list = []
            items = []
            for option in [str(option) for option in (question.get("options") or [])]:
                flag = tk.BooleanVar(value=False)
                wrap, button = self._make_button(row_f, option, 10, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                vars_list.append((option, flag))
                items.append((option, wrap, button))

                def on_toggle(flag=flag, wrap=wrap, button=button, rec=record):
                    flag.set(not flag.get())
                    rec["answered"] = True
                    if flag.get():
                        wrap.configure(bg=style["btn_selected_border"])
                        button.configure(bg=style["btn_selected_bg"])
                    else:
                        wrap.configure(bg=style["btn_border"])
                        button.configure(bg=style["btn_bg"])

                button.configure(command=on_toggle)
            record["vars"] = vars_list
            record["buttons"] = items
        elif q_type == "choice":
            var = tk.StringVar(value="")
            row_f = tk.Frame(card, bg=style["card_bg"])
            row_f.grid(row=2, column=0, sticky="w", pady=(10, 0))
            items = []
            for option in [str(option) for option in (question.get("options") or [])]:
                wrap, button = self._make_button(row_f, option, 10, 18, 10)
                wrap.pack(side="left", padx=(0, 8))
                items.append((option, wrap, button))

            def on_choice(option, rec=record, target=var, btns=items):
                target.set(option)
                rec["answered"] = True
                self._style_buttons(btns, {option})

            for option, _wrap, button in items:
                button.configure(command=lambda v=option, cb=on_choice: cb(v))
            record["var"] = var
            record["buttons"] = items
        else:
            text_wrap = tk.Frame(card, bg=style["btn_border"])
            text_wrap.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            text = tk.Text(
                text_wrap,
                height=4,
                wrap="word",
                bg=style["btn_bg"],
                fg=style["text_primary"],
                insertbackground=style["text_primary"],
                bd=0,
                relief="flat",
                highlightthickness=0,
                font=(font_family, 10),
                padx=8,
                pady=6,
            )
            text.pack(fill="both", expand=True, padx=1, pady=1)
            record["text"] = text
        self.answers.append(record)

    def _headers(self):
        headers = [
            "timestamp",
            "survey_title",
            "carla_session",
            "questionnaire_phase",
            "questionnaire_label",
            "questionnaire_sequence_index",
            "questionnaire_sequence_count",
            "scenario_name",
            "scenario_index",
            "scenario_count",
            "scale_min",
            "scale_max",
        ]
        headers.extend("q{}".format(idx) for idx in range(1, len(self.answers) + 1))
        headers.append("numeric_avg")
        return headers

    def _save(self):
        values = []
        numeric = []
        for idx, record in enumerate(self.answers, start=1):
            question = record["question"]
            q_type = record["type"]
            required = bool(question.get("required", True))
            if q_type in ("slider", "point_select"):
                if required and not record.get("answered"):
                    self.owner.messagebox.showwarning("Incomplete", "Please answer Question {} before saving.".format(idx), parent=self.top)
                    return
                value = record["var"].get() if record.get("answered") else ""
                values.append(value)
                if value != "":
                    min_v = int(question.get("scale_min", self.definition.get("scale_min", 1)))
                    max_v = int(question.get("scale_max", self.definition.get("scale_max", 7)))
                    numeric.append((float(value), min_v, max_v))
            elif q_type == "choice" and question.get("multi"):
                selected = [option for option, flag in record["vars"] if flag.get()]
                if required and not selected:
                    self.owner.messagebox.showwarning("Incomplete", "Please answer Question {} before saving.".format(idx), parent=self.top)
                    return
                values.append("; ".join(selected))
            elif q_type in ("true_false", "choice"):
                value = record["var"].get()
                if required and not value:
                    self.owner.messagebox.showwarning("Incomplete", "Please answer Question {} before saving.".format(idx), parent=self.top)
                    return
                values.append(value)
            else:
                value = record["text"].get("1.0", "end").strip()
                if required and not value:
                    self.owner.messagebox.showwarning("Incomplete", "Please answer Question {} before saving.".format(idx), parent=self.top)
                    return
                values.append(value)
        row = [
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            self.definition.get("title", ""),
            self.payload.get("session_name", "") or os.environ.get("CARLA_SESSION_NAME", ""),
            self.payload.get("phase", ""),
            self.payload.get("label", ""),
            self.payload.get("sequence_index", ""),
            self.payload.get("sequence_count", ""),
            self.payload.get("scenario_name", ""),
            self.payload.get("scenario_index", ""),
            self.payload.get("scenario_count", ""),
            self.definition.get("scale_min", ""),
            self.definition.get("scale_max", ""),
        ]
        row.extend(values)
        row.append(compute_numeric_avg(numeric))
        headers = self._headers()
        output_path = resolve_response_csv_path(self.script_path.parent, self.response_basename, headers)
        exists = output_path.exists()
        with output_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if not exists:
                writer.writerow(headers)
            writer.writerow(row)
        self.owner.emit("Launcher questionnaire saved: {}".format(output_path))
        try:
            self.on_done()
        finally:
            self.top.destroy()


class LauncherGui(object):
    def __init__(self):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("CARLA SOURCE 17fbcf2 - Scenario Launcher")
        window_width = min(1720, self.root.winfo_screenwidth() - 40)
        window_height = min(960, self.root.winfo_screenheight() - 90)
        self.root.geometry("{}x{}+10+10".format(window_width, window_height))
        self.root.minsize(min(1240, window_width), min(720, window_height))
        self.style = configure_inkspace_style(self.root)

        self.queue = queue.Queue()
        self.worker = None
        self.scenario_proc = None
        self.carla_proc = None
        self.aoi_proc = None
        self.aoi_ready_event = None
        self.session_start_confirm_event = None
        self.session_start_cancel_event = None
        self.session_start_confirm_pending = False
        self.session_start_confirm_label = ""
        self.session_start_confirm_proc = None
        self.lsl_ready_beacon_stop_event = None
        self.lsl_ready_beacon_thread = None
        self.g29_wait_monitor_proc = None
        self.g29_wait_monitor_thread = None
        self.start_run_button = None
        self.confirm_start_button = None
        self.restart_session_button = None
        self.stop_session_button = None
        self.level3_session_proc = None
        self.emotibit_proc = None
        self.lab_recorder_proc = None
        self.lsl_data_reader_proc = None
        self.lsl_data_reader_ready_event = None
        self.questionnaire_proc = None
        self.scenarios = []
        self.listbox_items = []
        self.map_spawn_selection = None
        self.status_var = tk.StringVar(value="Ready")
        self.map_var = tk.StringVar(value=DEFAULT_CARLA_MAP)
        self.spawn_var = tk.StringVar(value="No map spawn picked yet.")
        self.scenario_sort_var = tk.StringVar(value=SCENARIO_SORT_OPTIONS[0])
        self.group_view_var = tk.BooleanVar(value=True)
        self.group_var = tk.StringVar(value="")
        self.run_from_current_start_var = tk.BooleanVar(value=False)
        self.quality_var = tk.StringVar(value="Tuned high (recommended)")
        self.display_mode_var = tk.StringVar(value=load_display_mode())
        self.display_status_var = tk.StringVar(value="Choose a layout, then apply. Applying restarts CARLA.")
        self.async_visual_var = tk.BooleanVar(value=True)
        self.lsl_enabled_var = tk.BooleanVar(value=True)
        self.aoi_enabled_var = tk.BooleanVar(value=False)
        self.emotibit_enabled_var = tk.BooleanVar(value=True)
        try:
            _emotibit_cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig")).get("emotibit", {}) or {}
        except Exception:
            _emotibit_cfg = {}
        self.emotibit_baseline_var = tk.StringVar(value=str(_emotibit_cfg.get("baseline_seconds", "2.0")))
        self.emotibit_abs_threshold_var = tk.StringVar(value=str(_emotibit_cfg.get("absolute_threshold", "1.5")))
        self.emotibit_z_threshold_var = tk.StringVar(value=str(_emotibit_cfg.get("z_threshold", "8.0")))
        self.tor_decision_enabled_var = tk.BooleanVar(value=True)
        self.tor_visual_test_var = tk.BooleanVar(value=False)
        self.tor_mirror_hud_test_var = tk.BooleanVar(value=False)
        self.g29_takeover_var = tk.BooleanVar(value=True)
        self.lab_recorder_enabled_var = tk.BooleanVar(value=False)
        self.lsl_data_reader_enabled_var = tk.BooleanVar(value=False)
        self.questionnaire_enabled_var = tk.BooleanVar(value=False)
        self.questionnaire_before_var = tk.BooleanVar(value=False)
        self.questionnaire_between_var = tk.BooleanVar(value=True)
        self.questionnaire_after_var = tk.BooleanVar(value=False)
        self.questionnaire_wait_var = tk.BooleanVar(value=True)
        self.confirm_before_scenario_var = tk.BooleanVar(value=True)
        self.start_saved_playlist_var = tk.BooleanVar(value=True)
        self.aoi_no_voice_var = tk.BooleanVar(value=False)
        self.aoi_latency_log_var = tk.BooleanVar(value=False)
        self.queen_vehicles_var = tk.BooleanVar(value=False)
        self.queen_density_var = tk.StringVar(value=DEFAULT_QUEEN_DENSITY_LABEL)
        self.queen_speed_var = tk.StringVar(value=queen_speed_text(DEFAULT_QUEEN_SPEED_KMH))
        self.queen_despawn_var = tk.StringVar(value=queen_despawn_text(DEFAULT_QUEEN_DESPAWN_DISTANCE_M))
        self.lsl_stream_var = tk.StringVar(value=DEFAULT_LSL_STREAM)
        self.lsl_data_reader_streams_var = tk.StringVar(
            value="{}, {}, Level3DriverStateMarkers".format(
                DEFAULT_LSL_STREAM,
                DEFAULT_CARLA_TELEMETRY_STREAM,
            )
        )
        self.lsl_data_reader_output_var = tk.StringVar(
            value="Output: {}".format(CARLA_LSL_DATA_OUTPUT_DIR)
        )
        self.questionnaire_root_var = tk.StringVar(value=str(SURVEY_GENERATOR_ROOT))
        self.questionnaire_language_var = tk.StringVar(value="zh")
        self.questionnaire_script_var = tk.StringVar(value="")
        self.questionnaire_schedule_var = tk.StringVar(value="[]")
        self.questionnaire_schedule_text_widget = None
        self.neon_ip_var = tk.StringVar(value=DEFAULT_NEON_IP)
        self.aoi_corner_ids_var = tk.StringVar(value=DEFAULT_AOI_CORNER_IDS)
        self.aoi_grid_var = tk.StringVar(value=DEFAULT_AOI_GRID)
        self.tor_alert_delay_var = tk.StringVar(value=DEFAULT_TOR_ALERT_DELAY_S)
        self.extension_status_var = tk.StringVar(value="")
        self.level3_session_summary_var = tk.StringVar(value="")
        self.scenario_start_confirm_state_var = tk.StringVar(value="No pending scenario start.")
        self.quick_tor_controller = None
        self.quick_tor_output_dir = None
        self.quick_tor_status_var = tk.StringVar(value="Ready - Neon and G29 will connect before the two scenarios start.")

        self._load_extension_settings_into_vars()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close_launcher)
        self.refresh_extension_status()
        self.refresh_level3_session_summary()
        self.refresh_scenarios()
        self.root.after(100, self._drain_queue)

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="DReyeVR CARLA 0.9.13 Scenario Launcher",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        display_frame = ttk.Frame(header)
        display_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(display_frame, text="Display", style="Section.TLabel").pack(side="left", padx=(0, 10))
        self.display_mode_combo = ttk.Combobox(display_frame, textvariable=self.display_mode_var,
                                              values=DISPLAY_MODES, state="readonly", width=16)
        self.display_mode_combo.pack(side="left", padx=(0, 10))
        self.display_mode_combo.bind("<<ComboboxSelected>>", self._display_mode_selected)
        self.display_apply_button = ttk.Button(display_frame, text="Apply & restart CARLA",
                                               command=self.apply_display_mode)
        self.display_apply_button.pack(side="left", padx=(0, 12))
        ttk.Label(display_frame, textvariable=self.display_status_var, style="Muted.TLabel").pack(side="left")
        quality_frame = ttk.Frame(header)
        quality_frame.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(quality_frame, text="Map").pack(side="left", padx=(0, 6))
        ttk.Combobox(
            quality_frame,
            textvariable=self.map_var,
            values=CARLA_MAPS,
            state="readonly",
            width=18,
        ).pack(side="left", padx=(0, 12))
        ttk.Label(quality_frame, text="Visual preset").pack(side="left", padx=(0, 6))
        quality_combo = ttk.Combobox(
            quality_frame,
            textvariable=self.quality_var,
            values=QUALITY_PRESET_LABELS,
            state="readonly",
            width=26,
        )
        quality_combo.pack(side="left")
        ttk.Checkbutton(
            quality_frame,
            text="Async visual FPS",
            variable=self.async_visual_var,
        ).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(
            quality_frame,
            text="Queen vehicles",
            variable=self.queen_vehicles_var,
            command=self.refresh_extension_status,
        ).pack(side="left", padx=(12, 0))
        ttk.Label(quality_frame, text="Density").pack(side="left", padx=(8, 6))
        queen_density_combo = ttk.Combobox(
            quality_frame,
            textvariable=self.queen_density_var,
            values=QUEEN_DENSITY_LABELS,
            state="readonly",
            width=26,
        )
        queen_density_combo.pack(side="left")
        queen_density_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_extension_status())
        ttk.Label(quality_frame, text="Speed").pack(side="left", padx=(8, 6))
        queen_speed_entry = ttk.Entry(quality_frame, textvariable=self.queen_speed_var, width=6)
        queen_speed_entry.pack(side="left")
        queen_speed_entry.bind("<KeyRelease>", lambda _event: self.refresh_extension_status())
        queen_speed_entry.bind("<FocusOut>", lambda _event: self.refresh_extension_status())
        ttk.Label(quality_frame, text="Disappear").pack(side="left", padx=(8, 6))
        queen_despawn_entry = ttk.Entry(quality_frame, textvariable=self.queen_despawn_var, width=6)
        queen_despawn_entry.pack(side="left")
        queen_despawn_entry.bind("<KeyRelease>", lambda _event: self.refresh_extension_status())
        queen_despawn_entry.bind("<FocusOut>", lambda _event: self.refresh_extension_status())

        quick = ttk.LabelFrame(self.root, text="Adaptive TOR - Two-Scenario Experiment", padding=12)
        quick.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        quick.columnconfigure(0, weight=1)
        ttk.Label(quick, text="low_speed_tor_new  →  leftbehind", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        quick_actions = ttk.Frame(quick)
        quick_actions.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.quick_tor_start_button = ttk.Button(quick_actions, text="Run Adaptive TOR (2 Scenarios)", command=self.start_adaptive_quick, style="Accent.TButton")
        self.quick_tor_start_button.pack(side="left", padx=(0, 12))
        self.quick_tor_restart_button = ttk.Button(quick_actions, text="Restart (2 Scenarios)", command=self.restart_adaptive_quick, state="disabled")
        self.quick_tor_restart_button.pack(side="left", padx=(0, 12))
        self.quick_tor_stop_button = ttk.Button(quick_actions, text="Stop Experiment", command=self.stop_adaptive_quick, state="disabled")
        self.quick_tor_stop_button.pack(side="left", padx=(0, 12))
        ttk.Button(quick_actions, text="Open Records", command=self.open_adaptive_results).pack(side="left")
        ttk.Label(quick, text="Live gaze adaptation | G29 steering takeover | Button 24: return to auto | Auto-save", style="Muted.TLabel", wraplength=1000).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(quick, textvariable=self.quick_tor_status_var, wraplength=1000).grid(row=3, column=0, sticky="w", pady=(8, 0))

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        left = ttk.Frame(main, padding=8)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        main.add(left, weight=1)

        ttk.Label(left, text="Scenario Library", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        library_tools = ttk.Frame(left)
        library_tools.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        library_tools.columnconfigure(1, weight=1)
        ttk.Label(library_tools, text="Sort").grid(row=0, column=0, sticky="w", padx=(0, 6))
        sort_combo = ttk.Combobox(
            library_tools,
            textvariable=self.scenario_sort_var,
            values=SCENARIO_SORT_OPTIONS,
            state="readonly",
            width=18,
        )
        sort_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        sort_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_scenarios(preserve_selection=True))
        ttk.Checkbutton(
            library_tools,
            text="Group",
            variable=self.group_view_var,
            command=lambda: self.refresh_scenarios(preserve_selection=True),
        ).grid(row=0, column=2, sticky="e")
        self.listbox = tk.Listbox(left, exportselection=False, height=18, width=42)
        style_inkspace_listbox(self.listbox)
        self.listbox.grid(row=2, column=0, sticky="nsew", pady=(8, 8))
        self.listbox.bind("<Double-1>", lambda _event: self.edit_selected_scenario())
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self._sync_selected_group())

        scenario_tools = ttk.LabelFrame(left, text="Scenario Management", padding=8)
        scenario_tools.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        scenario_tools.columnconfigure(1, weight=1)
        ttk.Label(scenario_tools, text="Group").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.group_combo = ttk.Combobox(
            scenario_tools,
            textvariable=self.group_var,
            values=(),
            width=24,
        )
        self.group_combo.grid(row=0, column=1, columnspan=2, sticky="ew")
        ttk.Button(scenario_tools, text="Set Group", command=self.set_selected_group).grid(
            row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0)
        )
        ttk.Button(scenario_tools, text="Add Scenario", command=self.add_scenario).grid(
            row=1, column=1, sticky="ew", padx=(0, 6), pady=(8, 0)
        )
        ttk.Button(scenario_tools, text="Delete", command=self.delete_selected_scenario).grid(
            row=1, column=2, sticky="ew", pady=(8, 0)
        )

        spawn_frame = ttk.LabelFrame(left, text="Ego Spawn", padding=8)
        spawn_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        spawn_frame.columnconfigure(0, weight=1)
        ttk.Button(
            spawn_frame,
            text="Pick Map Spawn",
            command=self.pick_map_spawn,
            style="Accent.TButton",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(spawn_frame, textvariable=self.spawn_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(
            spawn_frame,
            text="Run from current ego position",
            variable=self.run_from_current_start_var,
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        actions = ttk.Frame(left)
        actions.grid(row=5, column=0, sticky="ew")
        for col in range(2):
            actions.columnconfigure(col, weight=1)
        ttk.Button(actions, text="Refresh", command=self.refresh_scenarios).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="Launch / Stop CARLA", command=self.toggle_carla).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(actions, text="Load Selected", command=self.load_selected, style="Accent.TButton").grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        self.start_run_button = ttk.Button(
            actions,
            text="Start Run",
            command=self.confirm_pending_scenario_start,
            style="Accent.TButton",
            state="disabled",
        )
        self.start_run_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))
        self._set_confirm_start_button_enabled(self.session_start_confirm_pending)
        ttk.Button(actions, text="Stop Scenario", command=self.stop_scenario).grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(actions, text="Visualize Design", command=self.visualize_selected_scenario).grid(
            row=2, column=1, sticky="ew", padx=(6, 0), pady=(8, 0)
        )

        right = ttk.PanedWindow(main, orient=tk.VERTICAL)
        main.add(right, weight=2)

        self._main_sash_initialized = False

        def _init_sash(_event=None):
            if self._main_sash_initialized or main.winfo_width() < 900:
                return
            self._main_sash_initialized = True
            try:
                main.sashpos(0, 410)
            except Exception:
                pass

        main.bind("<Configure>", _init_sash)

        extensions_host = ttk.Frame(right, padding=8)
        right.add(extensions_host, weight=3)
        self.extensions_panel = ExtensionsPanel(self, extensions_host)

        log_frame = ttk.Frame(right, padding=8)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        right.add(log_frame, weight=1)
        ttk.Label(log_frame, text="Launcher Log", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(log_frame, wrap="word", height=8)
        style_inkspace_text(self.log_text)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def start_adaptive_quick(self):
        if self.quick_tor_controller is not None or (self.worker is not None and self.worker.is_alive()):
            self.quick_tor_status_var.set("A task is already running. Stop the current experiment first.")
            return
        for proc in (self.scenario_proc, self.aoi_proc):
            if proc is not None and proc.poll() is None:
                self.quick_tor_status_var.set("Stop the current scenario or standalone AOI before starting Adaptive TOR.")
                return
        try:
            from extensions.level3_tor_demo.quick_start import QuickStartController
            cfg = self._sync_level3_config_from_extension_settings(persist=False)
            preset = self.selected_visual_preset()
        except Exception as exc:
            self.quick_tor_status_var.set("Preparation failed: {}".format(exc))
            return
        def progress(text):
            def update():
                if self.quick_tor_controller is controller:
                    self.quick_tor_status_var.set(text)
                    self.quick_tor_restart_button.configure(state="normal" if controller.can_restart else "disabled")
                    self.quick_tor_output_dir = controller.run_dir
            self.root.after(0, update)
        controller = QuickStartController(sys.modules[__name__], progress, self.emit, self._handle_questionnaire_handoff)
        self.quick_tor_controller = controller
        self.quick_tor_start_button.configure(state="disabled")
        self.quick_tor_restart_button.configure(state="disabled")
        self.quick_tor_stop_button.configure(state="normal")
        self.quick_tor_status_var.set("Preparing the two-scenario experiment...")
        def finished():
            self.quick_tor_output_dir = controller.run_dir
            self.quick_tor_controller = None
            self.quick_tor_start_button.configure(state="normal")
            self.quick_tor_restart_button.configure(state="disabled")
            self.quick_tor_stop_button.configure(state="disabled")
        def task():
            try:
                controller.run(cfg, visual_preset=preset)
            finally:
                self.root.after(0, finished)
        self._start_worker(task)

    def restart_adaptive_quick(self):
        controller = self.quick_tor_controller
        if controller is not None and controller.restart():
            self.quick_tor_restart_button.configure(state="disabled")
            self.quick_tor_status_var.set("Restarting both scenarios with the existing eye-tracking connection...")

    def stop_adaptive_quick(self):
        if self.quick_tor_controller is not None:
            self.quick_tor_restart_button.configure(state="disabled")
            self.quick_tor_status_var.set("Stopping the experiment and saving records...")
            self.quick_tor_controller.cancel()

    def open_adaptive_results(self):
        directory = self.quick_tor_output_dir or (RUNTIME_DIR / "adaptive_runs")
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def _close_launcher(self):
        if self.quick_tor_controller is not None:
            self.stop_adaptive_quick()
            self.root.after(100, self._close_launcher)
            return
        self.root.destroy()

    def emit(self, message):
        write_log(message)
        self.queue.put(str(message))

    def _drain_queue(self):
        while True:
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                break
            self.status_var.set(message)
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
        self.root.after(100, self._drain_queue)

    def _start_worker(self, target):
        if self.worker is not None and self.worker.is_alive():
            self.emit("A task is already running.")
            return

        def run():
            try:
                target()
            except Exception as exc:
                write_exception(exc)
                self.emit("ERROR: {}".format(exc))

        self.worker = threading.Thread(target=run)
        self.worker.daemon = True
        self.worker.start()

    def _start_urgent_worker(self, target):
        def run():
            try:
                target()
            except Exception as exc:
                write_exception(exc)
                self.emit("ERROR: {}".format(exc))

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return thread

    def _selected_item_or_none(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if index < 0 or index >= len(self.listbox_items):
            return None
        return self.listbox_items[index]

    def _sync_selected_group(self):
        item = self._selected_item_or_none()
        if item is not None:
            group = normalize_group(item.group)
            self.group_var.set("" if group == DEFAULT_SCENARIO_GROUP else group)

    def _scenario_key_for_item(self, item):
        return scenario_metadata_key(item.path)

    def _refresh_group_values(self):
        groups = available_scenario_groups(self.scenarios)
        self.group_combo["values"] = groups

    def refresh_scenarios(self, preserve_selection=False, select_key=None):
        if preserve_selection and select_key is None:
            current = self._selected_item_or_none()
            if current is not None:
                select_key = self._scenario_key_for_item(current)
        self.scenarios = sort_scenario_items(list_scenarios(), self.scenario_sort_var.get())
        self.listbox_items = []
        self.listbox.delete(0, "end")
        selected_index = None
        if self.group_view_var.get():
            grouped = {}
            for item in self.scenarios:
                grouped.setdefault(normalize_group(item.group), []).append(item)
            group_names = sorted(grouped, key=lambda value: (value == DEFAULT_SCENARIO_GROUP, value.lower()))
            for group_name in group_names:
                self.listbox.insert("end", "== {} ==".format(group_name))
                self.listbox_items.append(None)
                header_index = self.listbox.size() - 1
                try:
                    self.listbox.itemconfig(
                        header_index,
                        fg=INKSPACE_COLORS["brand_hover"],
                        bg=INKSPACE_COLORS["surface_alt"],
                    )
                except Exception:
                    pass
                for item in grouped[group_name]:
                    self.listbox.insert("end", "  " + item.label)
                    self.listbox_items.append(item)
                    if select_key and self._scenario_key_for_item(item) == select_key:
                        selected_index = self.listbox.size() - 1
        else:
            for item in self.scenarios:
                self.listbox.insert("end", item.label)
                self.listbox_items.append(item)
                if select_key and self._scenario_key_for_item(item) == select_key:
                    selected_index = self.listbox.size() - 1

        if selected_index is None:
            for index, item in enumerate(self.listbox_items):
                if item is not None:
                    selected_index = index
                    break
        if selected_index is not None:
            self.listbox.selection_set(selected_index)
            self.listbox.see(selected_index)
        self._refresh_group_values()
        self._sync_selected_group()
        self.emit("{} scenario(s) loaded.".format(len(self.scenarios)))

    def selected_item(self):
        item = self._selected_item_or_none()
        if item is None:
            raise RuntimeError("Select a scenario first.")
        return item

    def set_selected_group(self):
        try:
            item = self.selected_item()
        except Exception as exc:
            self.messagebox.showinfo("CARLA Launcher", str(exc))
            return
        group = normalize_group(self.group_var.get())
        key = self._scenario_key_for_item(item)
        groups = load_scenario_groups()
        if group == DEFAULT_SCENARIO_GROUP:
            groups.pop(key, None)
            group_text = DEFAULT_SCENARIO_GROUP
        else:
            groups[key] = group
            group_text = group
        save_scenario_groups(groups)
        self.refresh_scenarios(select_key=key)
        self.emit("Scenario '{}' assigned to group '{}'.".format(item.name, group_text))

    def delete_selected_scenario(self):
        try:
            item = self.selected_item()
        except Exception as exc:
            self.messagebox.showinfo("CARLA Launcher", str(exc))
            return
        if item.kind != "prompt_json":
            self.messagebox.showinfo(
                "CARLA Launcher",
                "Built-in Python scenarios are code files and are not deleted from this GUI.",
            )
            return
        if self.scenario_proc is not None and self.scenario_proc.poll() is None:
            self.messagebox.showinfo("CARLA Launcher", "Stop the running scenario before deleting files.")
            return
        if not self.messagebox.askyesno(
            "Delete scenario",
            "Delete {}?\n\n{}".format(item.name, item.path),
        ):
            return
        key = self._scenario_key_for_item(item)
        try:
            item.path.unlink()
            groups = load_scenario_groups()
            groups.pop(key, None)
            save_scenario_groups(groups)
            delete_scenario_prompt(item.path)
            delete_scenario_prompt_history(item.path)
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Delete scenario", str(exc))
            return
        self.refresh_scenarios()
        self.emit("Deleted scenario '{}'.".format(item.name))

    def add_scenario(self):
        AddScenarioDialog(self)

    def edit_selected_scenario(self):
        try:
            item = self.selected_item()
        except Exception as exc:
            self.messagebox.showinfo("CARLA Launcher", str(exc))
            return
        if item.kind != "prompt_json":
            self.messagebox.showinfo(
                "CARLA Launcher",
                "Built-in Python scenarios are code files. Use Load Selected, then Start Run.",
            )
            return
        AddScenarioDialog(self, edit_item=item)

    def visualize_selected_scenario(self):
        try:
            item = self.selected_item()
        except Exception as exc:
            self.messagebox.showinfo("CARLA Launcher", str(exc))
            return
        if item.kind != "prompt_json":
            self.messagebox.showinfo(
                "CARLA Launcher",
                "Only prompt JSON scenarios can be visualized.",
            )
            return
        try:
            ScenarioDesignVisualizer(self, item)
            self.emit("Opened design visualizer for '{}'.".format(item.name))
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Scenario Design Visualizer", str(exc))

    def lsl_stream_name(self):
        return self.lsl_stream_var.get().strip() or DEFAULT_LSL_STREAM

    def carla_lsl_reader_stream_names(self):
        from carla_nl.lsl_data_reader import parse_stream_names

        names = parse_stream_names(self.lsl_data_reader_streams_var.get())
        marker_stream = self.lsl_stream_name()
        if marker_stream and marker_stream not in names:
            names.insert(0, marker_stream)
        if DEFAULT_CARLA_TELEMETRY_STREAM not in names:
            names.append(DEFAULT_CARLA_TELEMETRY_STREAM)
        return names or [DEFAULT_LSL_STREAM, DEFAULT_CARLA_TELEMETRY_STREAM]

    def tor_alert_delay_s(self):
        value = self.tor_alert_delay_var.get().strip() or DEFAULT_TOR_ALERT_DELAY_S
        return float(value)

    def takeover_input_mode(self):
        return "g29" if self.g29_takeover_var.get() else "keyboard"

    def tor_manual_keys(self):
        return ["G29"] if self.g29_takeover_var.get() else ["W", "A", "S", "D"]

    def selected_map(self):
        value = self.map_var.get().strip()
        return value if value in CARLA_MAPS else DEFAULT_CARLA_MAP

    def _load_extension_settings_into_vars(self):
        try:
            from extensions.level3_tor_demo.config import load_session_config

            cfg = load_session_config(LEVEL3_SESSION_CONFIG)
        except Exception:
            return
        queen = cfg.get("queen_vehicles", {}) or {}
        self.queen_vehicles_var.set(bool(queen.get("enabled", False)))
        self.queen_density_var.set(queen_density_label_from_config(queen))
        self.queen_speed_var.set(queen_speed_text(queen.get("speed_kmh", DEFAULT_QUEEN_SPEED_KMH)))
        self.queen_despawn_var.set(queen_despawn_text(queen.get("despawn_distance_m", DEFAULT_QUEEN_DESPAWN_DISTANCE_M)))
        neon = cfg.get("neon", {}) or {}
        self.aoi_enabled_var.set(bool(neon.get("enabled", False)))
        self.neon_ip_var.set(str(neon.get("ip") or "").strip())
        self.aoi_latency_log_var.set(bool(neon.get("latency_log", False)))
        self.aoi_no_voice_var.set(bool(neon.get("no_voice", False)))
        emotibit = cfg.get("emotibit", {}) or {}
        self.emotibit_enabled_var.set(bool(emotibit.get("enabled", True)))
        lsl = cfg.get("lsl", {}) or {}
        if lsl.get("stream_name"):
            self.lsl_stream_var.set(str(lsl.get("stream_name")))
        reader = cfg.get("lsl_data_reader", {}) or {}
        self.lsl_data_reader_enabled_var.set(bool(reader.get("auto_start", False)))
        if reader.get("stream_names"):
            self.lsl_data_reader_streams_var.set(", ".join(str(item) for item in reader.get("stream_names") or []))
        questionnaire = cfg.get("questionnaire", {}) or {}
        self.questionnaire_enabled_var.set(bool(questionnaire.get("enabled", False)))
        self.questionnaire_before_var.set(bool(questionnaire.get("run_before_session", False)))
        self.questionnaire_between_var.set(bool(questionnaire.get("run_between_scenarios", True)))
        self.questionnaire_after_var.set(bool(questionnaire.get("run_after_session", False)))
        self.questionnaire_wait_var.set(bool(questionnaire.get("wait", True)))
        if questionnaire.get("root"):
            self.questionnaire_root_var.set(str(questionnaire.get("root")))
        if questionnaire.get("language"):
            self.questionnaire_language_var.set(str(questionnaire.get("language")))
        self.questionnaire_script_var.set(str(questionnaire.get("script_path") or ""))
        schedule = questionnaire.get("schedule", [])
        if not isinstance(schedule, list):
            schedule = []
        self.questionnaire_schedule_var.set(json.dumps(schedule, ensure_ascii=False, indent=2))
        tor = cfg.get("tor", {}) or {}
        self.tor_decision_enabled_var.set(bool(tor.get("enabled", True)))
        self.tor_visual_test_var.set(bool(tor.get("visual_test", self.tor_visual_test_var.get())))
        self.tor_mirror_hud_test_var.set(bool(tor.get("mirror_hud_test", self.tor_mirror_hud_test_var.get())))
        if tor.get("target_grid"):
            self.aoi_grid_var.set(str(tor.get("target_grid")))
        self.g29_takeover_var.set(str(tor.get("takeover_input", "g29")).lower() != "keyboard")

    def refresh_extension_status(self):
        lsl = "LSL on" if self.lsl_enabled_var.get() else "LSL off"
        aoi = "AOI auto" if self.aoi_enabled_var.get() else "AOI manual/off"
        aoi_running = self.aoi_proc is not None and self.aoi_proc.poll() is None
        emotibit_enabled = "EmotiBit auto" if self.emotibit_enabled_var.get() else "EmotiBit manual/off"
        emotibit_running = self.emotibit_proc is not None and self.emotibit_proc.poll() is None
        takeover = "G29 takeover" if self.g29_takeover_var.get() else "WASD takeover"
        tor_decision = "TOR tree on" if self.tor_decision_enabled_var.get() else "TOR tree off"
        recorder_enabled = "Recorder auto" if self.lab_recorder_enabled_var.get() else "Recorder manual/off"
        recorder_running = self.lab_recorder_proc is not None and self.lab_recorder_proc.poll() is None
        reader_enabled = "Reader auto" if self.lsl_data_reader_enabled_var.get() else "Reader manual/off"
        reader_running = self.lsl_data_reader_proc is not None and self.lsl_data_reader_proc.poll() is None
        questionnaire_enabled = "Questionnaire on" if self.questionnaire_enabled_var.get() else "Questionnaire off"
        questionnaire_running = self.questionnaire_proc is not None and self.questionnaire_proc.poll() is None
        preset = queen_density_preset(self.queen_density_var.get())
        queen = (
            "Queen vehicles on: {0}, {1} km/h, disappear {2}m".format(
                preset["label"],
                queen_speed_text(self.queen_speed_var.get()),
                queen_despawn_text(self.queen_despawn_var.get()),
            )
            if self.queen_vehicles_var.get()
            else "Queen vehicles off"
        )
        self.extension_status_var.set(
            "{} | {} ({}) | {} ({}) | {} ({}) | {} | {} | {} ({}) | {} ({}) | {}".format(
                lsl,
                reader_enabled,
                "running" if reader_running else "stopped",
                aoi,
                "running" if aoi_running else "stopped",
                emotibit_enabled,
                "running" if emotibit_running else "stopped",
                takeover,
                tor_decision,
                recorder_enabled,
                "running" if recorder_running else "stopped",
                questionnaire_enabled,
                "running" if questionnaire_running else "stopped",
                queen,
            )
        )

    def _set_scenario_start_confirm_state(self, text):
        self.root.after(0, lambda value=str(text): self.scenario_start_confirm_state_var.set(value))

    def _set_confirm_start_button_enabled(self, enabled):
        def update():
            for button in (self.confirm_start_button, self.start_run_button):
                if button is None:
                    continue
                try:
                    button.configure(state="normal" if enabled else "disabled")
                except Exception:
                    pass

        self.root.after(0, update)

    def _start_lsl_ready_beacon(self, label):
        self._stop_lsl_ready_beacon()
        if not self.lsl_enabled_var.get():
            return
        stop_event = threading.Event()
        self.lsl_ready_beacon_stop_event = stop_event
        stream_name = self.lsl_stream_name()
        label_text = str(label or "selected scenario")

        def worker():
            self.emit(
                "CARLA LSL ready beacon active on {} while waiting for scenario confirmation.".format(
                    stream_name
                )
            )
            while not stop_event.is_set():
                try:
                    send_lsl_udp_marker(
                        stream_name,
                        "CARLA_GUI_READY_FOR_CONFIRM",
                        label=label_text,
                        pid=os.getpid(),
                    )
                except Exception:
                    pass
                stop_event.wait(1.0)

        thread = threading.Thread(target=worker, daemon=True)
        self.lsl_ready_beacon_thread = thread
        thread.start()

    def _stop_lsl_ready_beacon(self):
        stop_event = self.lsl_ready_beacon_stop_event
        if stop_event is not None:
            stop_event.set()
        self.lsl_ready_beacon_stop_event = None
        self.lsl_ready_beacon_thread = None

    def _start_g29_wait_monitor(self, label):
        self._stop_g29_wait_monitor()
        if not self.g29_takeover_var.get():
            return
        cmd = [
            str(PY37),
            "-u",
            "-m",
            "carla_nl.g29_auto_off_monitor",
            "--host",
            HOST,
            "--port",
            str(RPC_PORT),
            "--label",
            str(label or "selected scenario"),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self.emit("WARN: failed to start G29 wait monitor: {}".format(exc))
            return
        self.g29_wait_monitor_proc = proc
        self.emit("G29 wait monitor active; rebuilt DReyeVR C++ handles wheel AUTO state before Start Run.")

        def reader():
            try:
                for line in proc.stdout or []:
                    line = str(line).strip()
                    if line:
                        self.emit("G29 wait monitor: {}".format(line))
            except Exception:
                pass

        thread = threading.Thread(target=reader, daemon=True)
        self.g29_wait_monitor_thread = thread
        thread.start()

    def _stop_g29_wait_monitor(self):
        proc = self.g29_wait_monitor_proc
        self.g29_wait_monitor_proc = None
        self.g29_wait_monitor_thread = None
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _send_level3_session_command(self, command, label):
        proc = self.level3_session_proc
        if proc is None or proc.poll() is not None:
            proc = self.session_start_confirm_proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if proc.stdin is None:
                return False
            proc.stdin.write(str(command).strip().lower() + "\n")
            proc.stdin.flush()
            self.emit("{} command sent to persistent Level-3 session.".format(label))
            if str(command).strip().lower() in ("start", "restart", "quit"):
                self._stop_lsl_ready_beacon()
                self._stop_g29_wait_monitor()
            return True
        except OSError as exc:
            self.emit("Failed to send {} command: {}".format(label, exc))
            return False

    def _handle_questionnaire_handoff(self, text, proc):
        raw = str(text).replace("QUESTIONNAIRE_HANDOFF", "", 1).strip()
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self.emit("QUESTIONNAIRE_HANDOFF parse failed: {}".format(exc))
            return

        def done():
            try:
                if proc is not None and proc.poll() is None and proc.stdin is not None:
                    proc.stdin.write("questionnaire_done\n")
                    proc.stdin.flush()
                    self.emit("Questionnaire handoff completed; continuing playlist.")
            except OSError as exc:
                self.emit("Failed to return questionnaire handoff: {}".format(exc))

        def open_dialog():
            try:
                LauncherQuestionnaireDialog(self, payload, done)
            except Exception as exc:
                write_exception(exc)
                self.emit("QUESTIONNAIRE_HANDOFF dialog failed: {}".format(exc))
                self.messagebox.showerror("Questionnaire", str(exc))
                done()

        self.root.after(0, open_dialog)

    def confirm_pending_scenario_start(self):
        event = self.session_start_confirm_event
        proc = self.session_start_confirm_proc
        if self.session_start_confirm_pending and proc is not None and proc.poll() is None:
            label = self.session_start_confirm_label or "saved playlist"
            try:
                if proc.stdin is not None:
                    proc.stdin.write("start\n")
                    proc.stdin.flush()
            except OSError as exc:
                self.emit("Failed to confirm {}: {}".format(label, exc))
                return
            self.emit("Manual confirmation received; starting {} now.".format(label))
            self.scenario_start_confirm_state_var.set("Confirmed. Starting {}...".format(label))
            self.session_start_confirm_pending = False
            self.session_start_confirm_proc = None
            self.session_start_confirm_label = ""
            self._stop_lsl_ready_beacon()
            self._stop_g29_wait_monitor()
            self._set_confirm_start_button_enabled(False)
            return
        if event is None or not self.session_start_confirm_pending:
            message = "No scenario is waiting for manual start confirmation. Use Load Selected or Start Session first, then wait until the playlist/scenario is ready."
            self.emit(message)
            self.scenario_start_confirm_state_var.set("No pending scenario start. Use Load Selected or Start Session first.")
            self._set_confirm_start_button_enabled(False)
            self.messagebox.showinfo("Start Run", message)
            return
        label = self.session_start_confirm_label or "selected scenario"
        self.emit("Manual confirmation received; starting {} now.".format(label))
        self.scenario_start_confirm_state_var.set("Confirmed. Starting {}...".format(label))
        self._set_confirm_start_button_enabled(False)
        self._stop_lsl_ready_beacon()
        self._stop_g29_wait_monitor()
        event.set()

    def _wait_for_manual_scenario_start(self, item, force=None):
        wait_for_start = self.confirm_before_scenario_var.get() if force is None else bool(force)
        if not wait_for_start:
            return True
        event = threading.Event()
        cancel_event = threading.Event()
        self.session_start_confirm_event = event
        self.session_start_cancel_event = cancel_event
        self.session_start_confirm_pending = True
        label = getattr(item, "label", None) or getattr(item, "name", "selected scenario")
        self.session_start_confirm_label = str(label)
        self._set_confirm_start_button_enabled(True)
        self._set_scenario_start_confirm_state("Waiting to start: {}. Press Start Run.".format(label))
        self.emit(
            "Scenario is ready for {}. Press 'Start Run' to begin playback.".format(
                label
            )
        )
        self._start_lsl_ready_beacon(label)
        self._start_g29_wait_monitor(label)
        try:
            while True:
                if event.wait(timeout=0.25):
                    return True
                if cancel_event.is_set():
                    self.emit("Start Run cancelled for {} before playback.".format(label))
                    self._set_scenario_start_confirm_state("Cancelled pending start: {}.".format(label))
                    return False
        finally:
            self.session_start_confirm_pending = False
            self.session_start_confirm_event = None
            self.session_start_cancel_event = None
            self.session_start_confirm_label = ""
            self._stop_lsl_ready_beacon()
            self._stop_g29_wait_monitor()
            self._set_confirm_start_button_enabled(False)
            self._set_scenario_start_confirm_state("No pending scenario start.")

    def _arm_process_start_confirmation(self, proc, label):
        self.session_start_confirm_proc = proc
        self.session_start_confirm_event = None
        self.session_start_cancel_event = None
        self.session_start_confirm_pending = True
        self.session_start_confirm_label = str(label or "saved playlist")
        self._set_confirm_start_button_enabled(True)
        self._set_scenario_start_confirm_state(
            "Waiting to start: {}. Press Start Run.".format(self.session_start_confirm_label)
        )
        self._start_lsl_ready_beacon(self.session_start_confirm_label)
        self._start_g29_wait_monitor(self.session_start_confirm_label)

    def refresh_level3_session_summary(self):
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            names = [str(name) for name in cfg.get("scenario_names", [])]
            hold_s = cfg.get("tor", {}).get("inter_scene_hold_s", 10)
            freeze = bool(cfg.get("freeze_probe", {}).get("enabled", False))
            mode = cfg.get("execution", {}).get("mode", "continuous")
            override_count = len(cfg.get("scenario_overrides", {}) or {})
            emotibit_cfg = cfg.get("emotibit", {}) or {}
            queen_cfg = cfg.get("queen_vehicles", {}) or {}
            questionnaire_cfg = cfg.get("questionnaire", {}) or {}
            playlist_meta = cfg.get("playlist", {}) if isinstance(cfg.get("playlist"), dict) else {}
            playlist_name = str(playlist_meta.get("name") or "Saved playlist")
            playlist_group = normalize_group(playlist_meta.get("group"))
            queen_enabled = bool(queen_cfg.get("enabled", False))
            questionnaire_enabled = bool(questionnaire_cfg.get("enabled", False))
            tor_enabled = bool(cfg.get("tor", {}).get("enabled", True))
            takeover_label = str(cfg.get("tor", {}).get("takeover_input", "keyboard") or "keyboard")
            queen_preset = queen_density_preset(
                queen_cfg.get("density") or queen_cfg.get("count") or DEFAULT_QUEEN_DENSITY_LABEL
            )
            if names:
                queen_label = (
                    "{0}, {1} km/h, disappear {2}m".format(
                        queen_preset["label"],
                        queen_speed_text(queen_cfg.get("speed_kmh", DEFAULT_QUEEN_SPEED_KMH)),
                        queen_despawn_text(queen_cfg.get("despawn_distance_m", DEFAULT_QUEEN_DESPAWN_DISTANCE_M)),
                    )
                    if queen_enabled
                    else "off"
                )
                questionnaire_phases = []
                if questionnaire_cfg.get("run_before_session"):
                    questionnaire_phases.append("before")
                if questionnaire_cfg.get("run_between_scenarios"):
                    questionnaire_phases.append("between")
                if questionnaire_cfg.get("run_after_session"):
                    questionnaire_phases.append("after")
                questionnaire_schedule = questionnaire_cfg.get("schedule", [])
                questionnaire_schedule_count = len(questionnaire_schedule) if isinstance(questionnaire_schedule, list) else 0
                questionnaire_label = (
                    (
                        "schedule:{} {}".format(questionnaire_schedule_count, questionnaire_cfg.get("language", "zh"))
                        if questionnaire_schedule_count
                        else "{} {}".format("/".join(questionnaire_phases) or "manual", questionnaire_cfg.get("language", "zh"))
                    )
                    if questionnaire_enabled
                    else "off"
                )
                text = "{} [{}]: {} scenario(s): {} | mode={} | hold={}s | takeover={} | TOR tree={} | TOR overrides={} | movement abs={} z={} | queen={} | questionnaire={} | freeze probe={}".format(
                    playlist_name,
                    playlist_group,
                    len(names),
                    ", ".join(names[:5]),
                    mode,
                    hold_s,
                    takeover_label,
                    "on" if tor_enabled else "off",
                    override_count,
                    emotibit_cfg.get("absolute_threshold", 1.5),
                    emotibit_cfg.get("z_threshold", 8.0),
                    queen_label,
                    questionnaire_label,
                    "on" if freeze else "off",
                )
            else:
                text = "No saved playlist yet."
        except Exception:
            text = "No saved playlist yet."
        self.level3_session_summary_var.set(text)

    def open_level3_session_builder(self, new_playlist=False, load_item=None):
        self.refresh_scenarios(preserve_selection=True)
        dialog = SessionBuilderDialog(self)
        if new_playlist:
            dialog.start_new_playlist()
        elif isinstance(load_item, dict):
            dialog._apply_library_item_to_builder(load_item)
        return dialog

    def open_questionnaire_schedule_builder(self, on_saved=None):
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            names = [str(name) for name in cfg.get("scenario_names", []) if str(name).strip()]
        except Exception:
            names = []
        if not names:
            self.messagebox.showinfo(
                "Questionnaire Schedule",
                "Create and save a playlist first, then build the questionnaire schedule.",
            )
            self.open_level3_session_builder()
            return
        QuestionnaireScheduleBuilderDialog(self, on_saved=on_saved)

    def enable_questionnaire_only_mode(self):
        self.start_saved_playlist_var.set(True)
        self.questionnaire_enabled_var.set(True)
        self.questionnaire_wait_var.set(True)
        self.tor_decision_enabled_var.set(False)
        self.aoi_enabled_var.set(False)
        self.emotibit_enabled_var.set(False)
        self.lab_recorder_enabled_var.set(False)
        self.lsl_data_reader_enabled_var.set(False)
        try:
            self._sync_level3_config_from_extension_settings()
        except Exception as exc:
            self.emit(str(exc))
            self.messagebox.showerror("Questionnaire-only Test", str(exc))
            return
        self._persist_questionnaire_only_to_playlist_library()
        self.refresh_extension_status()
        self.refresh_level3_session_summary()
        self.emit(
            "Questionnaire-only test mode enabled: saved playlist + questionnaire on; TOR/AOI/EmotiBit/recorders off."
        )
        self.messagebox.showinfo(
            "Questionnaire-only Test",
            "Questionnaire-only test mode is ready. Press Start Session to run the saved playlist without AOI, TOR, or EmotiBit.",
        )

    def _persist_questionnaire_only_to_playlist_library(self):
        try:
            cfg = json.loads(LEVEL3_SESSION_CONFIG.read_text(encoding="utf-8-sig"))
            playlist_id = str((cfg.get("playlist", {}) or {}).get("id") or "").strip()
            if not playlist_id:
                return
            items = load_playlist_library()
            changed = False
            for item in items:
                if str(item.get("id") or "").strip() != playlist_id:
                    continue
                item.setdefault("tor", {})["enabled"] = False
                item.setdefault("emotibit", {})["enabled"] = False
                questionnaire = item.setdefault("questionnaire", {})
                if isinstance(questionnaire, dict):
                    questionnaire["enabled"] = True
                    questionnaire["wait"] = True
                changed = True
                break
            if changed:
                save_playlist_library(items)
        except Exception as exc:
            self.emit("WARNING: could not update playlist library questionnaire-only flags: {}".format(exc))

    def questionnaire_schedule_text(self):
        widget = getattr(self, "questionnaire_schedule_text_widget", None)
        if widget is not None:
            try:
                text = widget.get("1.0", "end").strip()
                self.questionnaire_schedule_var.set(text)
                return text
            except Exception:
                pass
        return self.questionnaire_schedule_var.get().strip()

    def questionnaire_schedule(self):
        text = self.questionnaire_schedule_text()
        if not text:
            return []
        try:
            schedule = json.loads(text)
        except Exception as exc:
            raise RuntimeError("Questionnaire Schedule JSON is invalid: {}".format(exc))
        if not isinstance(schedule, list):
            raise RuntimeError("Questionnaire Schedule JSON must be a list.")
        return schedule

    def _sync_level3_config_from_extension_settings(self, persist=True):
        try:
            from extensions.level3_tor_demo.config import load_session_config, normalize_tag_layout, save_session_config

            cfg = load_session_config(LEVEL3_SESSION_CONFIG)
            cfg["neon"]["ip"] = self.neon_ip_var.get().strip()
            # "Auto-start AOI with Start Session" also drives the session path:
            # the runner skips the Neon AOI monitor when neon.enabled is False.
            cfg["neon"]["enabled"] = bool(self.aoi_enabled_var.get())
            cfg["neon"]["latency_log"] = bool(self.aoi_latency_log_var.get())
            cfg["neon"]["no_voice"] = bool(self.aoi_no_voice_var.get())
            cfg["neon"]["preview"] = True
            cfg["emotibit"]["enabled"] = bool(self.emotibit_enabled_var.get())
            cfg["emotibit"]["auto_start_oscilloscope"] = bool(self.emotibit_enabled_var.get())
            cfg["emotibit"]["auto_start_bridge"] = False
            cfg["emotibit"]["oscilloscope_path"] = str(EMOTIBIT_OSCILLOSCOPE_EXE)
            cfg["tag_layout"] = normalize_tag_layout(
                self.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS
            )
            cfg["lsl"]["stream_name"] = self.lsl_stream_name()
            cfg["lsl"]["feedback_port"] = int(DEFAULT_AOI_FEEDBACK_PORT)
            cfg["lsl_data_reader"] = {
                "auto_start": bool(self.lsl_data_reader_enabled_var.get()),
                "stream_names": self.carla_lsl_reader_stream_names(),
                "output_dir": str(CARLA_LSL_DATA_OUTPUT_DIR),
            }
            cfg["questionnaire"] = {
                "enabled": bool(self.questionnaire_enabled_var.get()),
                "root": self.questionnaire_root_var.get().strip() or str(SURVEY_GENERATOR_ROOT),
                "language": self.questionnaire_language_var.get().strip() or "zh",
                "script_path": self.questionnaire_script_var.get().strip(),
                "schedule": self.questionnaire_schedule(),
                "run_before_session": bool(self.questionnaire_before_var.get()),
                "run_between_scenarios": bool(self.questionnaire_between_var.get()),
                "run_after_session": bool(self.questionnaire_after_var.get()),
                "wait": bool(self.questionnaire_wait_var.get()),
                "timeout_s": 0.0,
                "continue_on_error": False,
                "handoff_to_launcher": True,
            }
            cfg.setdefault("execution", {})["mode"] = (
                "stepped"
                if cfg["questionnaire"]["enabled"]
                and (
                    cfg["questionnaire"]["run_between_scenarios"]
                    or any(
                        str(item.get("phase", item.get("when", "after_scenario"))).strip().lower().replace("-", "_").replace(" ", "_")
                        in ("between", "between_scenario", "between_scenarios")
                        for item in cfg["questionnaire"]["schedule"]
                        if isinstance(item, dict)
                    )
                )
                else "continuous"
            )
            cfg.setdefault("queen_vehicles", {})
            cfg["queen_vehicles"]["enabled"] = bool(self.queen_vehicles_var.get())
            apply_queen_density_config(cfg["queen_vehicles"], self.queen_density_var.get())
            apply_queen_speed_config(cfg["queen_vehicles"], self.queen_speed_var.get())
            apply_queen_despawn_config(cfg["queen_vehicles"], self.queen_despawn_var.get())
            cfg["tor"]["target_grid"] = self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID
            cfg["tor"]["enabled"] = bool(self.tor_decision_enabled_var.get())
            cfg["tor"]["visual_test"] = bool(self.tor_visual_test_var.get())
            cfg["tor"]["mirror_hud_test"] = bool(self.tor_mirror_hud_test_var.get())
            cfg["tor"]["takeover_input"] = self.takeover_input_mode()
            cfg["tor"]["default_manual_keys"] = self.tor_manual_keys()
            if persist:
                save_session_config(LEVEL3_SESSION_CONFIG, cfg)
                self.refresh_level3_session_summary()
            return cfg
        except Exception as exc:
            write_exception(exc)
            raise RuntimeError("Failed to sync saved playlist extension settings: {}".format(exc))

    def _carla_map_matches(self, ready_message, map_name):
        text = str(ready_message or "")
        return text.endswith(map_name) or text.endswith("/" + map_name)

    def _scenario_map_for_item(self, item):
        if item is not None and item.kind == "prompt_json":
            map_name = str(item.map_name or "").strip()
            if map_name:
                return map_name
        return self.selected_map()

    def _bring_up_carla_for_map(
        self,
        target_map,
        require_ego,
        switch_into_target,
        ego_spawn_xodr=None,
        ego_spawn_index=None,
        display_mode=None,
    ):
        """Make CARLA ready for target_map.

        Large maps cannot be the UE4 startup map (their tiles never stream that
        way in this DReyeVR build -> half-sky / half-blank screen), so we boot a
        normal map (boot_map_for) and, when switch_into_target is set,
        load_world() into the large map at runtime. The scenario run path lets
        run_scenario_dreyevr perform that load_world itself, so it passes
        switch_into_target=False and only needs CARLA up on the boot map.
        """
        target_map = str(target_map or DEFAULT_CARLA_MAP).strip() or DEFAULT_CARLA_MAP
        boot_map = boot_map_for(target_map)
        preset = self.selected_visual_preset()
        ready, message = carla_ready()
        on_target = self._carla_map_matches(message, target_map)
        on_boot = self._carla_map_matches(message, boot_map)

        need_launch = (not ready) or (not on_target and not on_boot)
        if not need_launch and require_ego and dreyevr_ego_count() <= 0:
            self.emit(
                "CARLA ready on {} but no DReyeVR ego actor exists; attempting in-world cockpit ego recovery.".format(
                    message
                )
            )
            if recover_missing_dreyevr_ego(self.emit, spawn_xodr=ego_spawn_xodr, spawn_index=ego_spawn_index):
                self.emit("DReyeVR ego recovered without relaunching CARLA.")
            else:
                self.emit(
                    "In-world DReyeVR ego recovery failed; relaunching CARLA to restore the cockpit ego."
                )
                need_launch = True

        if need_launch:
            if ready:
                self.emit("CARLA is ready on {}; relaunching boot map {}.".format(message, boot_map))
                if not stop_carla_processes(self.emit):
                    ready_after_stop, stop_message = carla_ready()
                    if ready_after_stop:
                        raise RuntimeError(
                            "CARLA is still responding on {}; not launching a second simulator.".format(
                                stop_message
                            )
                        )
            else:
                self.emit("CARLA is not ready; launching with map={} visual_preset={}.".format(boot_map, preset))
            if is_large_map(target_map):
                self.emit(
                    "Note: {} is a streaming Large Map; booting {} first, then "
                    "load_world({}) at runtime.".format(target_map, boot_map, target_map)
                )
            self.emit("Launching CARLA with map={} visual_preset={}.".format(boot_map, preset))
            self.carla_proc = launch_carla_process(preset, boot_map, display_mode or self.selected_display_mode())
            wait_for_carla(self.emit)
        else:
            self.emit("CARLA already ready: {}".format(message))

        # Large maps must be entered via load_world, never as the startup map.
        if switch_into_target and is_large_map(target_map):
            ready, message = carla_ready()
            if not self._carla_map_matches(message, target_map):
                self._load_world_via_api(target_map)

        if require_ego:
            ego_ready = normalize_dreyevr_egos(self.emit) > 0
            if not ego_ready:
                ego_ready = wait_for_dreyevr_ego(self.emit, timeout_s=10)
                if ego_ready:
                    normalize_dreyevr_egos(self.emit)
            if not ego_ready:
                ego_ready = recover_missing_dreyevr_ego(
                    self.emit,
                    spawn_xodr=ego_spawn_xodr,
                    spawn_index=ego_spawn_index,
                )
            if not ego_ready and not wait_for_dreyevr_ego(self.emit, timeout_s=10):
                raise RuntimeError(
                    "DReyeVR ego actor did not appear on {}; not starting scenario.".format(target_map)
                )

    def _load_world_via_api(self, map_name):
        """Switch the running CARLA into map_name via the Python API.

        This is the supported way to enter a streaming Large Map; loading such a
        map as the UE4 startup map leaves its tiles unstreamed (blank ground).
        """
        map_name = str(map_name or "").strip()
        if not map_name:
            return False
        self.emit(
            "Switching CARLA into {} via load_world (large-map streaming, may take 60-120s)...".format(map_name)
        )
        code = (
            "import carla, time\n"
            "c = carla.Client('127.0.0.1', 2013)\n"
            "c.set_timeout(200.0)\n"
            "cur = c.get_world().get_map().name\n"
            "if not cur.endswith(__MAP__):\n"
            "    c.load_world(__MAP__)\n"
            "    time.sleep(3.0)\n"
            "print('MAPNOW=' + c.get_world().get_map().name)\n"
        ).replace("__MAP__", repr(map_name))
        try:
            rc, out = run_py37(code, timeout_s=240)
        except Exception as exc:
            self.emit("WARN: load_world({}) failed: {}".format(map_name, exc))
            return False
        line = last_non_log_line(out)
        if rc != 0:
            self.emit("WARN: load_world({}) rc={}: {}".format(map_name, rc, line or out.strip()[:200]))
            return False
        self.emit("CARLA now on: {}".format(line.replace("MAPNOW=", "") if line else "(unknown)"))
        return True

    def _ensure_carla_ready_for_item(self, item):
        # Scenario runs let run_scenario_dreyevr do the load_world() into the
        # (possibly large) scenario map, so we only need CARLA up on the boot map.
        ego_spawn_xodr, ego_spawn_index = scenario_ego_spawn_hints(item)
        self._bring_up_carla_for_map(
            self._scenario_map_for_item(item),
            require_ego=(item.kind == "prompt_json"),
            switch_into_target=False,
            ego_spawn_xodr=ego_spawn_xodr,
            ego_spawn_index=ego_spawn_index,
        )

    def pick_map_spawn(self):
        def accept(selection):
            self.map_spawn_selection = {
                "road_id": int(selection["road_id"]),
                "lane_id": int(selection["lane_id"]),
                "s": float(selection["s"]),
            }
            label = "OpenDRIVE road={road_id} lane={lane_id} s={s:.2f}m".format(
                **self.map_spawn_selection
            )
            self.spawn_var.set(label)

            def task():
                ready, message = carla_ready()
                if not ready:
                    raise RuntimeError("CARLA RPC is not ready: {}".format(message))
                result = teleport_ego_to_xodr(self.map_spawn_selection)
                self.root.after(0, lambda: self.run_from_current_start_var.set(True))
                self.emit(
                    "Moved ego actor {actor_id} to OpenDRIVE road={road_id} lane={lane_id} s={s:.2f}: "
                    "x={x:.1f} y={y:.1f} z={z:.2f} yaw={yaw:.1f}".format(**result)
                )
                self.emit("Run from current ego position enabled.")

            self._start_worker(task)

        try:
            MapSpawnPicker(
                self,
                self.selected_map(),
                initial_spawn=self.map_spawn_selection,
                initial_lane="Middle",
                on_accept=accept,
            )
        except Exception as exc:
            write_exception(exc)
            self.messagebox.showerror("Pick Map Spawn", str(exc))

    def _pump_process_output(self, proc, label, ready_event=None, ready_patterns=None):
        patterns = tuple(ready_patterns or ())
        seen_patterns = set()

        def run():
            try:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        if ready_event is not None and patterns:
                            for idx, pattern in enumerate(patterns):
                                if pattern in line:
                                    seen_patterns.add(idx)
                            if len(seen_patterns) == len(patterns):
                                ready_event.set()
                        self.emit("{}: {}".format(label, line))
            except Exception as exc:
                write_exception(exc)
                self.emit("{} output reader failed: {}".format(label, exc))
            finally:
                self.root.after(0, self.refresh_extension_status)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return thread

    def _wait_for_aoi_ready(self, timeout_s=30.0):
        proc = self.aoi_proc
        event = self.aoi_ready_event
        if proc is None or proc.poll() is not None:
            raise RuntimeError("AOI / LSL monitor is not running; cannot start the scenario.")
        if event is None or event.is_set():
            return
        self.emit("Waiting for AOI / Neon / EmotiBit monitor readiness before enabling scenario start...")
        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            if event.wait(timeout=0.25):
                self.emit("AOI / Neon / EmotiBit monitor ready.")
                return
            if proc.poll() is not None:
                raise RuntimeError(
                    "AOI / Neon / EmotiBit monitor exited before it became ready. Check Neon IP/network/LSL and restart Start Session."
                )
        raise RuntimeError(
            "AOI / Neon / EmotiBit monitor did not become ready within {:.0f}s. Check Neon IP/network, EmotiBit LSL, and the camera stream.".format(
                float(timeout_s)
            )
        )

    def _wait_for_lsl_data_reader_ready(self, timeout_s=15.0):
        """Wait until the CARLA LSL data reader is subscribed (listening) before
        the scenario starts, so it records markers/telemetry from t=0. Non-fatal:
        the reader is an optional logger, so a timeout warns and continues."""
        proc = self.lsl_data_reader_proc
        event = self.lsl_data_reader_ready_event
        if proc is None or proc.poll() is not None or event is None or event.is_set():
            return
        self.emit("Waiting for the CARLA LSL data reader to subscribe before the scenario starts...")
        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            if event.wait(timeout=0.25):
                self.emit("CARLA LSL data reader is listening; markers/telemetry will be recorded from scenario start.")
                return
            if proc.poll() is not None:
                self.emit("WARNING: CARLA LSL data reader exited before it became ready; continuing without it.")
                return
        self.emit(
            "WARNING: CARLA LSL data reader did not report readiness within {:.0f}s; "
            "continuing (it may miss the first markers).".format(float(timeout_s))
        )

    def _ensure_aoi_running(self, force=False):
        if not force and not self.aoi_enabled_var.get():
            return
        if self.aoi_proc is not None and self.aoi_proc.poll() is None:
            self.emit("AOI / LSL monitor already running.")
            self.root.after(0, self.refresh_extension_status)
            return
        self.emit(
            "Starting AOI / LSL monitor stream={} neon_ip={} tags={} grid={}".format(
                self.lsl_stream_name(),
                self.neon_ip_var.get().strip() or "(auto)",
                self.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS,
                self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
            )
        )
        self.aoi_ready_event = threading.Event()
        self.aoi_proc = launch_aoi_process(
            self.lsl_stream_name(),
            self.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS,
            self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
            neon_ip=self.neon_ip_var.get().strip(),
            no_voice=self.aoi_no_voice_var.get(),
            latency_log=self.aoi_latency_log_var.get(),
            no_emotibit=not self.emotibit_enabled_var.get(),
        )
        self.emit("AOI / LSL monitor started pid={}".format(self.aoi_proc.pid))
        self.root.after(0, self.refresh_extension_status)
        ready_patterns = ["AOI_MONITOR_READY"]
        if self.emotibit_enabled_var.get():
            ready_patterns.append("EmotiBit stream opened: GYRO_Z")
        self._pump_process_output(
            self.aoi_proc,
            "AOI",
            ready_event=self.aoi_ready_event,
            ready_patterns=ready_patterns,
        )

    def _ensure_lsl_data_reader_running(self, force=False):
        if not force and not self.lsl_data_reader_enabled_var.get():
            return
        if self.lsl_data_reader_proc is not None and self.lsl_data_reader_proc.poll() is None:
            self.emit("CARLA LSL data reader already running.")
            self.root.after(0, self.refresh_extension_status)
            return
        streams = self.carla_lsl_reader_stream_names()
        self.emit(
            "Starting CARLA LSL data reader streams={} output={}".format(
                ", ".join(streams),
                CARLA_LSL_DATA_OUTPUT_DIR,
            )
        )
        label = "carla_{0}".format(time.strftime("%Y%m%d_%H%M%S"))
        self.lsl_data_reader_ready_event = threading.Event()
        self.lsl_data_reader_proc = launch_lsl_data_reader_process(
            streams,
            output_dir=CARLA_LSL_DATA_OUTPUT_DIR,
            session_label=label,
        )
        self.emit("CARLA LSL data reader started pid={}".format(self.lsl_data_reader_proc.pid))
        self.root.after(0, self.refresh_extension_status)
        # CARLA_LSL_READER_READY prints once the reader's writer is open and its
        # resolve loop is listening -- i.e. it is subscribed BEFORE the scenario
        # produces markers (it fires at startup, not when streams resolve, so it
        # never deadlocks on the scenario-produced marker streams).
        self._pump_process_output(
            self.lsl_data_reader_proc,
            "CarlaLSL",
            ready_event=self.lsl_data_reader_ready_event,
            ready_patterns=("CARLA_LSL_READER_READY",),
        )

    def launch_lsl_data_reader(self):
        self._start_worker(lambda: self._ensure_lsl_data_reader_running(force=True))

    def stop_lsl_data_reader(self):
        def task():
            proc = self.lsl_data_reader_proc
            if proc is None or proc.poll() is not None:
                self.emit("CARLA LSL data reader is not running from this launcher.")
                self.lsl_data_reader_proc = None
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit("Stopping CARLA LSL data reader...")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
            self.emit("CARLA LSL data reader stopped.")
            self.lsl_data_reader_proc = None
            self.root.after(0, self.refresh_extension_status)

        self._start_urgent_worker(task)

    def preflight(self):
        def task():
            self.emit("Project root: {}".format(PROJECT_ROOT))
            self.emit("Python 3.7: {} {}".format(PY37, "OK" if PY37.exists() else "MISSING"))
            self.emit("CARLA launcher: {} {}".format(START_CARLA_PS1, "OK" if START_CARLA_PS1.exists() else "MISSING"))
            self.emit("AOI Python: {} {}".format(AOI_PYTHON, "OK" if AOI_PYTHON.exists() else "MISSING"))
            self.emit("AOI script: {} {}".format(AOI_SCRIPT, "OK" if AOI_SCRIPT.exists() else "MISSING"))
            self.emit("EmotiBit Oscilloscope: {} {}".format(
                EMOTIBIT_OSCILLOSCOPE_EXE,
                "OK" if EMOTIBIT_OSCILLOSCOPE_EXE.exists() else "MISSING",
            ))
            self.emit("LSL stream: {}".format(self.lsl_stream_name()))
            self.emit("CARLA LSL reader streams: {}".format(", ".join(self.carla_lsl_reader_stream_names())))
            self.emit("CARLA LSL reader output: {}".format(CARLA_LSL_DATA_OUTPUT_DIR))
            self.emit("Questionnaire root: {}".format(self.questionnaire_root_var.get().strip() or SURVEY_GENERATOR_ROOT))
            self.emit("Questionnaire script: {}".format(self.questionnaire_script_var.get().strip() or "(SurveyGenerator main app)"))
            self.emit("Neon IP: {}".format(self.neon_ip_var.get().strip() or "(auto-discovery)"))
            self.emit("AOI tags: {}".format(self.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS))
            self.emit("Prompt JSON folder: {}".format(OUTPUT_SCENARIOS))
            self.emit("Selected CARLA map: {}".format(self.selected_map()))
            self.emit("Visual preset: {}".format(self.selected_visual_preset()))
            ready, message = carla_ready()
            self.emit("CARLA RPC: {}".format(message if ready else "offline / {}".format(message)))
            self.emit("Scenario count: {}".format(len(list_scenarios())))

        self._start_worker(task)

    def selected_visual_preset(self):
        return QUALITY_PRESET_BY_LABEL.get(self.quality_var.get(), "TunedHigh")

    def selected_display_mode(self):
        variable = getattr(self, "display_mode_var", None)
        return variable.get() if variable is not None else DISPLAY_MODES[0]

    def _display_mode_selected(self, _event=None):
        mode = self.selected_display_mode()
        try:
            save_display_mode(mode)
        except (OSError, ValueError) as exc:
            self.display_status_var.set("Could not save display choice: {}".format(exc))
            return
        self.display_status_var.set("{} selected. Apply to restart CARLA, or use on the next launch.".format(mode))

    def apply_display_mode(self):
        for name in ("scenario_proc", "level3_session_proc"):
            proc = getattr(self, name, None)
            if proc is not None and proc.poll() is None:
                self.display_status_var.set("Stop the running scenario/session before switching displays.")
                return
        if getattr(self, "quick_tor_controller", None) is not None:
            self.display_status_var.set("Stop the adaptive TOR experiment before switching displays.")
            return
        if self.worker is not None and self.worker.is_alive():
            self.display_status_var.set("Wait for the current task to finish, then apply the display layout.")
            return
        mode, target_map = self.selected_display_mode(), self.selected_map()
        try:
            save_display_mode(mode)
        except (OSError, ValueError) as exc:
            self.display_status_var.set("Could not save display choice: {}".format(exc))
            return
        self.display_status_var.set("Applying {} — restarting CARLA...".format(mode))
        self.display_mode_combo.configure(state="disabled")
        self.display_apply_button.configure(state="disabled")

        def task():
            result = "{} active.".format(mode)
            try:
                if not stop_carla_processes(self.emit):
                    raise RuntimeError("CARLA could not stop; no second simulator was opened. Retry Apply.")
                self.carla_proc = None
                self._bring_up_carla_for_map(target_map, require_ego=False, switch_into_target=True,
                                             display_mode=mode)
                self.emit(result)
            except Exception as exc:
                result = "Display switch failed: {}".format(exc)
                raise
            finally:
                def finished(message=result):
                    self.display_status_var.set(message)
                    self.display_mode_combo.configure(state="readonly")
                    self.display_apply_button.configure(state="normal")
                self.root.after(0, finished)

        self._start_worker(task)

    def launch_carla(self):
        def task():
            # Boot a normal map; for a large map, load_world() into it at runtime
            # so the launch lands on the requested map instead of a blank screen.
            self._bring_up_carla_for_map(
                self.selected_map(), require_ego=False, switch_into_target=True
            )

        self._start_worker(task)

    def toggle_carla(self):
        proc_running = self.carla_proc is not None and self.carla_proc.poll() is None
        ready, _message = carla_ready()
        if proc_running or ready:
            self.stop_carla()
        else:
            self.launch_carla()

    def launch_aoi(self):
        self._start_worker(lambda: self._ensure_aoi_running(force=True))

    def launch_neon_pupil_test(self):
        def task():
            if self.aoi_proc is not None and self.aoi_proc.poll() is None:
                self.emit("AOI / Neon monitor is already running. Stop it before starting Neon Pupil Test.")
                return
            self.emit(
                "Starting Neon Pupil Test: ip={} tag36h11 layout=left 0-3, center 4-7, right 8-11.".format(
                    self.neon_ip_var.get().strip() or "(auto)"
                )
            )
            self.aoi_proc = launch_neon_pupil_test_process(
                self.lsl_stream_name(),
                self.aoi_corner_ids_var.get().strip() or DEFAULT_AOI_CORNER_IDS,
                self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
                neon_ip=self.neon_ip_var.get().strip(),
            )
            self.emit("Neon Pupil Test started pid={}".format(self.aoi_proc.pid))
            self.root.after(0, self.refresh_extension_status)
            self._pump_process_output(self.aoi_proc, "NeonTest")

        self._start_worker(task)

    def _ensure_emotibit_running(self, force=False):
        if not force and not self.emotibit_enabled_var.get():
            return
        if self.emotibit_proc is not None and self.emotibit_proc.poll() is None:
            self.emit("EmotiBit Oscilloscope already running from this launcher.")
            self.root.after(0, self.refresh_extension_status)
            return
        self.emit("Starting EmotiBit Oscilloscope...")
        self.emotibit_proc = launch_emotibit_oscilloscope_process(self.emit)
        if self.emotibit_proc is not None:
            self.emit("EmotiBit Oscilloscope started pid={}".format(self.emotibit_proc.pid))
        self.root.after(0, self.refresh_extension_status)

    def launch_emotibit(self):
        self._start_worker(lambda: self._ensure_emotibit_running(force=True))

    def start_session(self):
        # Sessions always run the saved playlist; single scenarios are run via
        # Load Selected / Start Run in the left panel instead.
        self.start_saved_playlist_var.set(True)
        self.refresh_level3_session_summary()
        self.run_level3_session()

    def _ensure_carla_ready_for_selected_map(self):
        self._bring_up_carla_for_map(
            self.selected_map(), require_ego=False, switch_into_target=True
        )

    def _ensure_carla_running_for_level3_session(self):
        ready, message = carla_ready()
        if ready:
            selected_map = self.selected_map()
            if self._carla_map_matches(message, selected_map):
                self.emit("CARLA already ready for Level-3 playlist: {}".format(message))
            else:
                self.emit(
                    "CARLA already running on {}; Level-3 playlist will reuse it without closing the simulator.".format(
                        message
                    )
                )
                self.emit(
                    "If the playlist scenarios require another map, the runner may load that world inside the existing CARLA process."
                )
            return
        map_name = self.selected_map()
        boot_map = boot_map_for(map_name)
        preset = self.selected_visual_preset()
        self.emit("CARLA is offline; launching for Level-3 playlist with map={} visual_preset={}.".format(
            boot_map, preset))
        if is_large_map(map_name):
            self.emit(
                "Note: {} is a streaming Large Map; booting {} and the per-scenario "
                "runner will load_world() into the target map.".format(map_name, boot_map)
            )
        self.carla_proc = launch_carla_process(preset, boot_map, self.selected_display_mode())
        wait_for_carla(self.emit)

    def preflight_level3_session(self):
        self._run_level3_session(preflight_only=True)

    def run_level3_session(self):
        proc = self.level3_session_proc
        if proc is not None and proc.poll() is None:
            def start_ready_session():
                self._ensure_carla_running_for_level3_session()
                if self._send_level3_session_command("start", "Start Session"):
                    self.session_start_confirm_pending = False
                    self.session_start_confirm_proc = None
                    self._set_confirm_start_button_enabled(False)
                    self._set_scenario_start_confirm_state("Start requested; reusing connected devices.")
            self._start_urgent_worker(start_ready_session)
            return
        self._run_level3_session(preflight_only=False, confirm_start=False)

    def restart_level3_session(self):
        proc = self.level3_session_proc
        if proc is not None and proc.poll() is None:
            if self._send_level3_session_command("restart", "Restart Session"):
                self.session_start_confirm_pending = False
                self.session_start_confirm_event = None
                self.session_start_confirm_label = ""
                if self.session_start_confirm_proc is proc:
                    self.session_start_confirm_proc = None
                self._set_confirm_start_button_enabled(False)
                self._set_scenario_start_confirm_state("Restarting scenarios; AOI and devices stay connected.")
            return
        self.emit("No persistent Level-3 session is running; starting a new saved playlist session.")
        self.start_saved_playlist_var.set(True)
        self.refresh_level3_session_summary()
        self.run_level3_session()

    def stop_level3_session(self):
        if getattr(self, "quick_tor_controller", None) is not None:
            self.stop_adaptive_quick()
            return
        proc = self.level3_session_proc
        if proc is None or proc.poll() is not None:
            proc = self.session_start_confirm_proc
        if proc is not None and proc.poll() is None:
            if self._send_level3_session_command("stop", "Stop Session"):
                self.session_start_confirm_pending = False
                self.session_start_confirm_event = None
                self.session_start_confirm_label = ""
                if self.session_start_confirm_proc is proc:
                    self.session_start_confirm_proc = None
                self._set_confirm_start_button_enabled(False)
                self._set_scenario_start_confirm_state("Stopping playback; devices stay connected for the next Start Session.")
            return

        proc = self.scenario_proc
        if proc is not None and proc.poll() is None:
            self.emit("No persistent Level-3 session is running; stopping current scenario/session process.")
            self.stop_scenario()
            return

        self.emit("No Level-3 session is running.")
        self._set_scenario_start_confirm_state("No running session to stop.")

    def _disable_session_tor_if_unused(self):
        """If no playlist scenario declares/injects TOR, disable the adaptive
        TOR/AOI stack in THIS run's session config (UI settings stay untouched;
        the next sync rewrites tor.enabled from the checkbox again)."""
        try:
            from extensions.level3_tor_demo.config import load_session_config, save_session_config

            cfg = load_session_config(LEVEL3_SESSION_CONFIG)
        except Exception:
            return
        if not bool((cfg.get("tor") or {}).get("enabled", False)):
            return  # already off
        overrides = cfg.get("scenario_overrides") if isinstance(cfg.get("scenario_overrides"), dict) else {}

        def _override_injects_tor(name):
            override = overrides.get(name)
            if not isinstance(override, dict):
                return False
            return any(
                override.get(key) not in (None, "", False)
                for key in ("alert_onset_s", "tor_onset_s", "tor_deadline_s")
            )

        names = [str(name) for name in cfg.get("scenario_names", []) if str(name).strip()]
        if not names:
            return
        for name in names:
            payload = read_prompt_json_summary(OUTPUT_SCENARIOS / "{}.json".format(name))
            if scenario_requires_tor(payload) or _override_injects_tor(name):
                return  # at least one scenario uses TOR -> keep the stack
        try:
            cfg.setdefault("tor", {})["enabled"] = False
            save_session_config(LEVEL3_SESSION_CONFIG, cfg)
            self.emit(
                "No scenario in this playlist requests TOR; adaptive TOR/AOI stack disabled for this run."
            )
        except Exception as exc:
            write_exception(exc)

    def _run_level3_session(self, preflight_only=False, confirm_start=None):
        def task():
            if self.scenario_proc is not None and self.scenario_proc.poll() is None:
                self.emit("A scenario/session is already running. Stop it first.")
                return
            if not LEVEL3_SESSION_CONFIG.exists():
                self.emit("Level-3 TOR session config not found: {}".format(LEVEL3_SESSION_CONFIG))
                return
            try:
                self._sync_level3_config_from_extension_settings()
            except Exception as exc:
                self.emit(str(exc))
                return
            self._disable_session_tor_if_unused()
            if not preflight_only and self.emotibit_enabled_var.get():
                self._ensure_emotibit_running()
            if not preflight_only:
                self._ensure_carla_running_for_level3_session()
            action = "Preflighting" if preflight_only else "Running"
            self.emit("{} Level-3 TOR session playlist.".format(action))
            log_path = make_test_log_path("level3_session")
            self.emit("Test log: {}".format(log_path))
            wait_for_start = (not preflight_only) and (
                bool(self.confirm_before_scenario_var.get()) if confirm_start is None else bool(confirm_start))
            proc = None
            try:
                with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
                    write_test_log_line(log_file, "{} Level-3 TOR session playlist.".format(action))
                    proc = launch_level3_session_process(
                        LEVEL3_SESSION_CONFIG,
                        OUTPUT_SCENARIOS,
                        preflight_only=preflight_only,
                        wait_for_start=wait_for_start,
                        interactive=(not preflight_only),
                    )
                    self.scenario_proc = proc
                    if not preflight_only:
                        self.level3_session_proc = proc
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        text = line.rstrip()
                        if text:
                            write_test_log_line(log_file, text)
                            if text.startswith("SESSION_READY_FOR_CONFIRM"):
                                label = text.replace("SESSION_READY_FOR_CONFIRM", "", 1).strip() or "saved playlist"
                                self._arm_process_start_confirmation(proc, label)
                            elif text.startswith("QUESTIONNAIRE_HANDOFF "):
                                self._handle_questionnaire_handoff(text, proc)
                            self.emit(text)
                    rc = proc.wait()
                    write_test_log_line(log_file, "Level-3 TOR session exited with code {}".format(format_return_code(rc)))
                    self.emit("Level-3 TOR session exited with code {}".format(format_return_code(rc)))
                    if not preflight_only:
                        if rc == 0:
                            self.emit("Level-3 TOR session completed; stopping ego and cleaning scene.")
                        else:
                            self.emit("Level-3 TOR session failed; cleaning scene so the next run is not stuck.")
                        cleanup_scene(self.emit)
            finally:
                if self.scenario_proc is proc:
                    self.scenario_proc = None
                if self.level3_session_proc is proc:
                    self.level3_session_proc = None
                if self.session_start_confirm_proc is proc:
                    self.session_start_confirm_proc = None
                    self.session_start_confirm_pending = False
                    self.session_start_confirm_label = ""
                    self._set_confirm_start_button_enabled(False)
                    self._stop_g29_wait_monitor()
                    self._set_scenario_start_confirm_state("No pending scenario start.")

        self._start_worker(task)

    def stop_aoi(self):
        def task():
            proc = self.aoi_proc
            if proc is None or proc.poll() is not None:
                self.emit("AOI / LSL monitor is not running from this launcher.")
                self.aoi_proc = None
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit("Stopping AOI / LSL monitor...")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
            self.emit("AOI / LSL monitor stopped.")
            self.aoi_proc = None
            self.root.after(0, self.refresh_extension_status)

        self._start_urgent_worker(task)

    def stop_emotibit(self):
        def task():
            proc = self.emotibit_proc
            if proc is None or proc.poll() is not None:
                self.emit("EmotiBit Oscilloscope is not running from this launcher.")
                self.emotibit_proc = None
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit("Stopping EmotiBit Oscilloscope...")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
            self.emit("EmotiBit Oscilloscope stopped.")
            self.emotibit_proc = None
            self.root.after(0, self.refresh_extension_status)

        self._start_urgent_worker(task)

    def _ensure_lab_recorder_running(self, force=False):
        if not force and not self.lab_recorder_enabled_var.get():
            return
        if self.lab_recorder_proc is not None and self.lab_recorder_proc.poll() is None:
            self.emit("LabRecorder already running.")
            self.root.after(0, self.refresh_extension_status)
            return
        self.emit("Starting LabRecorder...")
        self.lab_recorder_proc = launch_lab_recorder_process()
        self.emit("LabRecorder started pid={}".format(self.lab_recorder_proc.pid))
        self.root.after(0, self.refresh_extension_status)

    def launch_lab_recorder(self):
        self._start_worker(lambda: self._ensure_lab_recorder_running(force=True))

    def stop_lab_recorder(self):
        def task():
            proc = self.lab_recorder_proc
            if proc is None or proc.poll() is not None:
                self.emit("LabRecorder is not running from this launcher.")
                self.lab_recorder_proc = None
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit("Stopping LabRecorder...")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
            self.emit("LabRecorder stopped.")
            self.lab_recorder_proc = None
            self.root.after(0, self.refresh_extension_status)

        self._start_urgent_worker(task)

    def launch_questionnaire(self):
        def task():
            if self.questionnaire_proc is not None and self.questionnaire_proc.poll() is None:
                self.emit("Questionnaire is already running from this launcher.")
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit(
                "Starting questionnaire root={} language={} script={}.".format(
                    self.questionnaire_root_var.get().strip() or str(SURVEY_GENERATOR_ROOT),
                    self.questionnaire_language_var.get().strip() or "zh",
                    self.questionnaire_script_var.get().strip() or "(SurveyGenerator main app)",
                )
            )
            self.questionnaire_proc = launch_questionnaire_process(
                self.questionnaire_root_var.get().strip() or str(SURVEY_GENERATOR_ROOT),
                language=self.questionnaire_language_var.get().strip() or "zh",
                script_path=self.questionnaire_script_var.get().strip(),
            )
            self.emit("Questionnaire started pid={}".format(self.questionnaire_proc.pid))
            self.root.after(0, self.refresh_extension_status)
            self._pump_process_output(self.questionnaire_proc, "Questionnaire")

        self._start_worker(task)

    def stop_questionnaire(self):
        def task():
            proc = self.questionnaire_proc
            if proc is None or proc.poll() is not None:
                self.emit("Questionnaire is not running from this launcher.")
                self.questionnaire_proc = None
                self.root.after(0, self.refresh_extension_status)
                return
            self.emit("Stopping questionnaire...")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
            self.emit("Questionnaire stopped.")
            self.questionnaire_proc = None
            self.root.after(0, self.refresh_extension_status)

        self._start_urgent_worker(task)

    def manual_tor_test(self):
        def task():
            self._ensure_aoi_running(force=True)
            self.emit("Sending manual TOR marker on stream={}.".format(self.lsl_stream_name()))
            rc, out = run_manual_tor_marker(
                self.lsl_stream_name(),
                self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
                wait_before_alert_s=2.0,
                alert_delay_s=self.tor_alert_delay_s(),
            )
            for line in out.splitlines():
                if line.strip():
                    self.emit("TOR test: {}".format(line.strip()))
            self.emit("Manual TOR marker exited with code {}".format(format_return_code(rc)))

        self._start_worker(task)

    def _append_queen_vehicle_args(self, cmd):
        if not self.queen_vehicles_var.get():
            return cmd
        preset = queen_density_preset(self.queen_density_var.get())
        cmd += [
            "--queen-vehicles",
            "--queen-vehicle-count",
            str(preset["count"]),
            "--queen-vehicle-spacing-m",
            str(preset["spacing_m"]),
            "--queen-vehicle-safe-gap-m",
            str(preset["safe_gap_m"]),
            "--queen-vehicle-speed-kmh",
            queen_speed_text(self.queen_speed_var.get()),
            "--queen-vehicle-despawn-distance-m",
            queen_despawn_text(self.queen_despawn_var.get()),
        ]
        return cmd

    def _start_scenario_process(self, item, use_extensions=False):
        if item.kind == "prompt_json":
            cmd = [
                str(PY37),
                "-u",
                "-m",
                "carla_nl.run_scenario_dreyevr",
                item.name,
                "--host",
                HOST,
                "--port",
                str(RPC_PORT),
                "--auto-start",
                "--once",
            ]
            requires_tor = self._item_requires_tor(item)
            if self.g29_takeover_var.get():
                cmd += ["--takeover-input", self.takeover_input_mode()]
            if requires_tor and self.tor_visual_test_var.get():
                cmd += ["--tor-visual-test"]
            if requires_tor and self.tor_mirror_hud_test_var.get():
                cmd += ["--tor-mirror-hud-test"]
            if use_extensions and self.lsl_enabled_var.get():
                # LSL markers/telemetry stay on for data sync even without TOR.
                cmd += [
                    "--lsl-markers",
                    "--lsl-stream-name",
                    self.lsl_stream_name(),
                    "--lsl-telemetry-stream-name",
                    DEFAULT_CARLA_TELEMETRY_STREAM,
                ]
                if requires_tor:
                    cmd += [
                        "--tor-alert-delay-s",
                        str(self.tor_alert_delay_s()),
                        "--tor-aoi-screen",
                        "current",
                        "--tor-aoi-grid",
                        self.aoi_grid_var.get().strip() or DEFAULT_AOI_GRID,
                        "--takeover-require-aoi",
                        "--aoi-feedback-host",
                        "127.0.0.1",
                        "--aoi-feedback-port",
                        DEFAULT_AOI_FEEDBACK_PORT,
                    ]
                else:
                    self.emit("Scenario has no TOR content; running plain (no AOI gate / adaptive TOR args).")
            if self.async_visual_var.get():
                cmd += ["--world-mode", "async-visual"]
            if self.run_from_current_start_var.get():
                cmd += ["--start-mode", "current", "--no-teleport-ego"]
            self._append_queen_vehicle_args(cmd)
        elif item.kind == "scripted":
            cmd = [str(PY37), "-u", str(RUN_SCENARIO_PY), item.name]
        else:
            raise RuntimeError("Unsupported scenario kind: {}".format(item.kind))

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if item.kind == "scripted" and proc.stdin is not None:
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
            except OSError:
                pass
        return proc

    def _item_requires_tor(self, item):
        """True when the scenario item declares TOR content; scripted scenarios
        keep legacy behaviour (assumed TOR-capable)."""
        if getattr(item, "kind", "") != "prompt_json":
            return True
        try:
            return scenario_requires_tor(read_prompt_json_summary(item.path))
        except Exception:
            return True

    def _run_selected_task(self, item, use_extensions=False, force_manual_start=None):
        if self.scenario_proc is not None and self.scenario_proc.poll() is None:
            self.emit("A scenario is already running. Stop it first.")
            return
        proc = None
        if use_extensions:
            if self.lsl_data_reader_enabled_var.get():
                self._ensure_lsl_data_reader_running()
                self._wait_for_lsl_data_reader_ready()
            if self.emotibit_enabled_var.get():
                self._ensure_emotibit_running()
            if self.lsl_enabled_var.get() and self.aoi_enabled_var.get():
                if self._item_requires_tor(item):
                    self._ensure_aoi_running()
                    self._wait_for_aoi_ready()
                else:
                    self.emit("Scenario has no TOR content; skipping AOI monitor for this run.")
            if self.lab_recorder_enabled_var.get():
                self._ensure_lab_recorder_running()
        self._ensure_carla_ready_for_item(item)
        if use_extensions:
            if not self._wait_for_manual_scenario_start(item, force=force_manual_start):
                self.emit("Loaded scenario was cancelled before Start Run; scenario process was not launched.")
                return
        self.emit(
            "Running {}{}".format(
                item.label,
                " with extensions" if use_extensions else "",
            )
        )
        log_path = make_test_log_path(item.name)
        self.emit("Test log: {}".format(log_path))
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
                write_test_log_line(log_file, "Running {}".format(item.label))
                proc = self._start_scenario_process(item, use_extensions=use_extensions)
                self.scenario_proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        write_test_log_line(log_file, line)
                        self.emit(line)
                rc = proc.wait()
                write_test_log_line(log_file, "Scenario exited with code {}".format(format_return_code(rc)))
                self.emit("Scenario exited with code {}".format(format_return_code(rc)))
                if rc == 0:
                    self.emit("Scenario completed; stopping ego and cleaning scene.")
                    cleanup_scene(self.emit)
                else:
                    self.emit("Scenario failed; cleaning scene so the next run is not stuck.")
                    cleanup_scene(self.emit)
        finally:
            if self.scenario_proc is proc:
                self.scenario_proc = None

    def _load_selected_item(self, item, force_manual_start=True):
        def task():
            if self.scenario_proc is not None and self.scenario_proc.poll() is None:
                self.emit("A scenario is already running. Stop it first.")
                return
            if self.session_start_confirm_pending:
                self.emit("A scenario is already loaded and waiting for Start Run.")
                return
            self._run_selected_task(
                item,
                use_extensions=True,
                force_manual_start=force_manual_start,
            )

        self._start_worker(task)

    def load_selected(self):
        try:
            item = self.selected_item()
        except Exception as exc:
            self.messagebox.showinfo("CARLA Launcher", str(exc))
            return
        self._load_selected_item(item, force_manual_start=True)

    def run_selected(self):
        self.load_selected()

    def stop_scenario(self):
        if getattr(self, "quick_tor_controller", None) is not None:
            self.stop_adaptive_quick()
            return
        def task():
            if self.session_start_confirm_pending and self.session_start_cancel_event is not None:
                label = self.session_start_confirm_label or "selected scenario"
                self.emit("Cancelling pending Start Run for {}.".format(label))
                self.session_start_confirm_pending = False
                self.session_start_cancel_event.set()
                self._stop_lsl_ready_beacon()
                self._stop_g29_wait_monitor()
                self._set_confirm_start_button_enabled(False)
                return
            proc = self.scenario_proc
            if proc is None:
                self.emit("No scenario process is running; Stop Scenario ignored.")
                return
            if proc.poll() is not None:
                self.emit("Scenario process already exited; Stop Scenario ignored to avoid duplicate cleanup.")
                return
            if proc is not None and proc.poll() is None:
                if proc is self.level3_session_proc and proc.stdin is not None:
                    self.emit("Stopping persistent Level-3 session...")
                    try:
                        proc.stdin.write("quit\n")
                        proc.stdin.flush()
                    except OSError:
                        pass
                else:
                    self.emit("Stopping scenario process...")
                    terminate_process_tree(proc)
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.emit("Session shutdown timed out; stopping its process tree, including HUD.")
                    terminate_process_tree(proc)
                self.emit("Scenario/session process stopped.")
                self.scenario_proc = None
                if proc is self.level3_session_proc:
                    self.level3_session_proc = None
                if proc is self.session_start_confirm_proc:
                    self.session_start_confirm_proc = None
                    self.session_start_confirm_pending = False
                    self.session_start_confirm_label = ""
                    self._set_confirm_start_button_enabled(False)
            cleanup_scene(self.emit)

        self._start_urgent_worker(task)

    def clean_scene(self):
        self._start_worker(lambda: cleanup_scene(self.emit))

    def stop_carla(self):
        if getattr(self, "quick_tor_controller", None) is not None:
            self.stop_adaptive_quick()
            self.root.after(100, self.stop_carla)
            return
        def task():
            proc = self.scenario_proc
            if proc is not None and proc.poll() is None:
                if proc is self.level3_session_proc and proc.stdin is not None:
                    self.emit("Stopping persistent Level-3 session before CARLA shutdown...")
                    try:
                        proc.stdin.write("quit\n")
                        proc.stdin.flush()
                    except OSError:
                        pass
                else:
                    self.emit("Stopping scenario process before CARLA shutdown...")
                    terminate_process_tree(proc)
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.emit("Session shutdown timed out; stopping its process tree, including HUD.")
                    terminate_process_tree(proc)
                self.scenario_proc = None
                if proc is self.level3_session_proc:
                    self.level3_session_proc = None
                cleanup_scene(self.emit)
            stop_carla_processes(self.emit)

        self._start_urgent_worker(task)

    def open_prompt_gui(self):
        def task():
            launch_prompt_generator()
            self.emit("Prompt generator opened.")

        self._start_worker(task)

    def mainloop(self):
        self.root.mainloop()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--list":
        for item in list_scenarios():
            print(item.label)
        return 0
    if argv and argv[0] == "--preflight":
        ready, message = carla_ready()
        print("Project root:", PROJECT_ROOT)
        print("Python 3.7:", PY37, "OK" if PY37.exists() else "MISSING")
        print("CARLA launcher:", START_CARLA_PS1, "OK" if START_CARLA_PS1.exists() else "MISSING")
        print("EmotiBit Oscilloscope:", EMOTIBIT_OSCILLOSCOPE_EXE, "OK" if EMOTIBIT_OSCILLOSCOPE_EXE.exists() else "MISSING")
        print("CARLA RPC:", message if ready else "offline / " + message)
        print("Scenario count:", len(list_scenarios()))
        return 0
    LauncherGui().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
