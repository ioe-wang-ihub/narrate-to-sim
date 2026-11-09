"""Interactive (scanner-style) scenario runner for DReyeVR CARLA 0.9.13.

Like run_scenario_native but talks to DReyeVR (Tesla cockpit) instead of vanilla.
Provides the same REPL: spawn -> wait Enter -> play -> wait [r]eplay/[q]uit.

MUST RUN with the bundled Python 3.7 runtime (has carla 0.9.13):

    $py37 = ".\\runtime_python\\carla37\\python.exe"
    cd <PROJECT_ROOT>
    & $py37 -m carla_nl.run_scenario_dreyevr town04_decel_2lane_change

(or simpler:    .\\run_dreyevr.ps1)

What it does:
  1. Connects to DReyeVR CARLA (default 127.0.0.1:2013).
  2. Finds the auto-spawned DReyeVR Tesla pawn (harplab.dreyevr_vehicle.*).
  3. Picks the CARLA HD-map lane group near the Tesla. On Town04_Opt the
     highway can include extra outer lanes such as exit/off-ramp or shoulder
     lanes, so scenario Left/Middle/Right are mapped only to the three main
     through lanes.
  4. Spawns vehicle_1 (Lincoln MKZ) per the scenario.
  5. Pauses with "[Enter] to play, [q] to quit" prompt.
  6. Runs scripted trajectories in real time.
  7. Pauses with "[Enter] to replay, [q] to quit" prompt; on replay teleports
     vehicles back to t=0 and runs again.
  8. Cleans up vehicle_1 on quit (leaves the DReyeVR Tesla — it isn't ours).

Supports the subset of the v1 schema needed for the demo:
  - map_name, total_time_s, ego_speed_kmh, fixed_delta_seconds
  - vehicles.ego: lane (controls which lane the Tesla goes into),
      initial_gap_m (longitudinal offset from spawn anchor), speed_kmh
  - vehicles.vehicle_1..6: lane, initial_gap_m, speed_kmh, blueprint_filter,
      gap_points, time_gap_points, lane_change_points
"""

# NOTE: this file must stay Python 3.7 compatible.

from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from carla_nl.tor_adaptive import (
    adaptive_fields_from_mapping,
    normalize_manual_requirement,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_CARLA_SAVED_DIR = PROJECT_ROOT / "runtime_carla" / "WindowsNoEditor" / "CarlaUE4" / "Saved"
DEFAULT_G29_VISUAL_STEER_FILE = str(PACKAGED_CARLA_SAVED_DIR / "g29_visual_steer.txt")
DEFAULT_G29_SIM_STATE_FILE = str(PACKAGED_CARLA_SAVED_DIR / "g29_input_state.txt")
DEFAULT_G29_PY_STATE_FILE = str(PACKAGED_CARLA_SAVED_DIR / "g29_python_state.txt")
DEFAULT_TAKEOVER_ON_AUDIO_FILE = str(
    PROJECT_ROOT / "audio" / "tesla-autopilot-on-made-with-Voicemod (1).mp3"
)
DEFAULT_RETURN_AUTO_OFF_AUDIO_FILE = str(
    PROJECT_ROOT / "audio" / "tesla-autopilot-off-made-with-Voicemod.mp3"
)
COLLISION_CONFIRM_PREFIX = "COLLISION_CONFIRM_REQUEST "

try:
    import carla
except ImportError:
    print(
        "ERROR: 'carla' module not found. Use the bundled CARLA runtime:\n"
        "  runtime_python\\carla37\\python.exe -m carla_nl.run_scenario_dreyevr"
    )
    sys.exit(2)

# NOTE: we intentionally do NOT import carla_nl.schema here — that pulls in
# pydantic, which isn't in the friend's py37 venv. The JSON we read should
# already be in normalized form (run_scenario.py or run_scenario_native.py
# normalizes via the pydantic schema before saving). We just trust it.


# ============================================================================
# CLI
# ============================================================================

def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="carla_nl.run_scenario_dreyevr")
    p.add_argument("name", nargs="?", help="Scenario name (matches outputs/scenarios/<name>.json)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2013)
    p.add_argument("--ego-actor-id", type=int, default=None,
                   help="Use this vehicle actor exactly; do not deduplicate other ego vehicles.")
    p.add_argument(
        "--map-load-timeout-s",
        type=float,
        default=180.0,
        help="Seconds to wait for CARLA load_world. Large DReyeVR/nDisplay maps can take over a minute.",
    )
    p.add_argument(
        "--outputs-dir",
        default=str(Path.cwd() / "outputs" / "scenarios"),
    )
    p.add_argument(
        "--render-fps",
        type=float,
        default=60.0,
        help="Wall-clock pacing target (Hz). Default 60 matches the current DReyeVR experiment tick.",
    )
    p.add_argument(
        "--visual-hz",
        type=float,
        default=60.0,
        help="Target scenario tick rate. JSON files faster than this are capped to this rate by default.",
    )
    p.add_argument(
        "--time-sync-mode",
        choices=("adaptive", "fixed"),
        default="adaptive",
        help="adaptive measures nDisplay tick cost and raises fixed_delta when needed so scenario time matches wall time.",
    )
    p.add_argument(
        "--time-calibration-ticks",
        type=int,
        default=12,
        help="Number of hidden sync ticks used by adaptive time-sync calibration.",
    )
    p.add_argument(
        "--world-mode",
        choices=("sync", "async-visual"),
        default="sync",
        help="sync is deterministic; async-visual lets UE render freely while Python sends wall-clock controls.",
    )
    p.add_argument("--traffic-manager-port", type=int, default=8000)
    p.add_argument(
        "--npc-mode",
        choices=("auto", "vehicle-control", "velocity-script", "kinematic", "traffic-manager"),
        default="auto",
        help="auto uses wheel VehicleControl for scripted NPC lane changes and Traffic Manager for normal driving.",
    )
    p.add_argument(
        "--ego-mode",
        choices=("auto", "kinematic", "traffic-manager"),
        default="auto",
        help="auto uses Traffic Manager for ego driving unless the scenario needs exact scripted motion.",
    )
    p.add_argument(
        "--no-teleport-ego",
        action="store_true",
        help="Don't teleport the DReyeVR Tesla; use its current position as-is.",
    )
    p.add_argument(
        "--start-mode",
        choices=("fixed", "current", "spawn"),
        default="fixed",
        help="Scenario lane-path anchor. Default fixed keeps every scenario run at the same start.",
    )
    p.add_argument("--fixed-start-x", type=float, default=-515.25)
    p.add_argument("--fixed-start-y", type=float, default=240.96)
    p.add_argument("--fixed-start-z", type=float, default=0.45)
    p.add_argument(
        "--auto-start",
        action="store_true",
        help="Skip the initial 'press Enter to play' prompt.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Exit after one playthrough.",
    )
    p.add_argument(
        "--playlist-file",
        default="",
        help="Run a JSON playlist of scenario names in one runner process.",
    )
    p.add_argument(
        "--session-hold-s",
        type=float,
        default=0.0,
        help="Continuous-playlist drive time between scenarios.",
    )
    p.add_argument(
        "--continuous-session",
        action="store_true",
        help="Keep ego/world prepared for a continuous playlist handoff instead of freezing after each segment.",
    )
    p.add_argument(
        "--playlist-preload-actors",
        dest="playlist_preload_actors",
        action="store_true",
        default=True,
        help=(
            "Preload playlist NPC vehicles into an above-ground actor pool so continuous "
            "playlist handoffs avoid spawn hitches."
        ),
    )
    p.add_argument(
        "--no-playlist-preload-actors",
        dest="playlist_preload_actors",
        action="store_false",
        help="Disable playlist NPC actor preloading and spawn support vehicles per scenario.",
    )
    p.add_argument(
        "--playlist-pause-between-scenarios",
        action="store_true",
        help="Pause after each non-final playlist scenario and wait for playlist_continue on stdin.",
    )
    p.add_argument(
        "--playlist-current-start-after-first",
        action="store_true",
        default=True,
        help="In playlist mode, scenarios after the first use current ego position as anchor when they have no spawn_xodr.",
    )
    p.add_argument(
        "--no-playlist-current-start-after-first",
        action="store_false",
        dest="playlist_current_start_after_first",
        help="Use each scenario's normal fixed start anchor even after the first playlist item.",
    )
    p.add_argument(
        "--playlist-collision-recovery",
        dest="playlist_collision_recovery",
        action="store_true",
        default=True,
        help=(
            "When a playlist scenario collides, pause with a popup, clean the scene, "
            "restore ego to an on-road auto-driving state, and force the next scenario "
            "to use its fixed start anchor."
        ),
    )
    p.add_argument(
        "--no-playlist-collision-recovery",
        dest="playlist_collision_recovery",
        action="store_false",
        help="Disable popup/cleanup recovery for collisions during playlist scenarios.",
    )
    p.add_argument(
        "--no-collision-recovery-popup",
        dest="collision_recovery_popup",
        action="store_false",
        default=True,
        help="Run collision recovery without showing the blocking confirmation popup.",
    )
    p.add_argument(
        "--collision-confirm-via-stdin",
        action="store_true",
        help=(
            "On collision, print COLLISION_CONFIRM_REQUEST and wait for a "
            "collision_confirmed stdin command instead of showing a local popup. "
            "Used by the Level-3 session runner so the GUI layer owns the visible dialog."
        ),
    )
    p.add_argument(
        "--move-spectator",
        action="store_true",
        help="Move CARLA spectator to follow ego. Off by default to avoid nDisplay view jumps.",
    )
    p.add_argument(
        "--restore-ego-physics",
        action="store_true",
        help="Re-enable DReyeVR Tesla physics at the end. Off by default to avoid post-teleport settling jitter.",
    )
    p.add_argument(
        "--post-run-ego",
        choices=("freeze", "reset", "physics"),
        default="freeze",
        help="How to stabilize the DReyeVR ego after a scenario. Default freeze prevents post-run flying.",
    )
    p.add_argument(
        "--post-run-world",
        choices=("freeze", "restore"),
        default="freeze",
        help="Default freeze leaves CARLA synchronous/paused after the scenario to prevent DReyeVR ego drift.",
    )
    p.add_argument(
        "--lsl-markers",
        action="store_true",
        help="Emit scenario action markers to LSL via a local UDP-to-LSL bridge.",
    )
    p.add_argument("--lsl-stream-name", default="CarlaScenarioMarkers")
    p.add_argument("--lsl-telemetry-stream-name", default="CarlaEgoTelemetry")
    p.add_argument(
        "--no-lsl-telemetry",
        action="store_true",
        help="Disable the continuous CARLA ego telemetry LSL stream.",
    )
    p.add_argument("--lsl-bridge-host", default="127.0.0.1")
    p.add_argument("--lsl-bridge-port", type=int, default=17364)
    p.add_argument(
        "--lsl-bridge-python",
        default=os.environ.get(
            "CARLA_YJC_LSL_BRIDGE_PYTHON",
            str(PROJECT_ROOT / "runtime_python" / "aoi" / "python.exe"),
        ),
        help="Python with pylsl installed. Defaults to the bundled AOI runtime.",
    )
    p.add_argument("--visual-marker-host", default="127.0.0.1")
    p.add_argument("--visual-marker-port", type=int, default=17367)
    p.add_argument(
        "--no-visual-markers",
        action="store_true",
        help="Disable local UDP visual markers used by carla_nl.view_triple overlays.",
    )
    p.add_argument(
        "--tor-spotlight-file",
        default=os.environ.get(
            "CARLA_YJC_TOR_SPOTLIGHT_FILE",
            str(PACKAGED_CARLA_SAVED_DIR / "tor_spotlight.txt"),
        ),
        help="Small state file read by the DReyeVR HUD to dim outside the TOR stage-2 target circle.",
    )
    p.add_argument(
        "--no-tor-spotlight-file",
        action="store_true",
        help="Disable writing the DReyeVR HUD TOR spotlight state file.",
    )
    p.add_argument(
        "--tor-alert-delay-s",
        type=float,
        default=0.5,
        help="Derived AOI/TOR alert marker delay after the first support lane-change/FPE.",
    )
    p.add_argument("--tor-aoi-screen", default="current")
    p.add_argument("--tor-aoi-grid", default="middle_left")
    p.add_argument(
        "--takeover-require-aoi",
        action="store_true",
        default=True,
        help="Require AOI_FOUND feedback before SPACE can succeed. Enabled by default.",
    )
    p.add_argument(
        "--no-takeover-require-aoi",
        action="store_false",
        dest="takeover_require_aoi",
        help="Allow SPACE takeover without AOI_FOUND feedback.",
    )
    p.add_argument("--aoi-feedback-host", default="127.0.0.1")
    p.add_argument("--aoi-feedback-port", type=int, default=17365)
    p.add_argument(
        "--tor-visual-test",
        action="store_true",
        help=(
            "Force TOR stage-2 AOI spotlight visuals from the scenario runner itself. "
            "Use this to test red circle/box overlays without Neon, EmotiBit, or AOI feedback."
        ),
    )
    p.add_argument(
        "--tor-visual-test-delay-s",
        type=float,
        default=0.0,
        help="Seconds after TOR onset before forced stage-2 visual test begins.",
    )
    p.add_argument(
        "--tor-visual-test-mode",
        default="",
        help="Optional forced visual target mode, e.g. rear_mirror, manual_screen, vehicle_contour.",
    )
    p.add_argument(
        "--tor-visual-test-mirror",
        default="",
        help="Optional forced rear mirror side for visual test: left, center, or right.",
    )
    p.add_argument(
        "--tor-visual-test-screen",
        default="",
        help="Optional forced screen for visual test: left, center, or right.",
    )
    p.add_argument("--tor-visual-test-x", type=float, default=None)
    p.add_argument("--tor-visual-test-y", type=float, default=None)
    p.add_argument("--tor-visual-test-radius", type=float, default=None)
    p.add_argument("--tor-visual-test-width", type=float, default=None)
    p.add_argument("--tor-visual-test-height", type=float, default=None)
    p.add_argument(
        "--tor-visual-test-shape",
        default="",
        help="Optional forced shape for visual test: circle or rect.",
    )
    p.add_argument(
        "--tor-mirror-hud-test",
        action="store_true",
        help=(
            "Keep three static red HUD circles on the screen positions used for the left, rear, "
            "and right mirrors. This is a pure HUD/screen-space calibration mode."
        ),
    )
    p.add_argument(
        "--tor-mirror-hud-radius",
        type=float,
        default=None,
        help="Optional common normalized radius for all three mirror HUD circles.",
    )
    p.add_argument(
        "--tor-mirror-hud-left",
        default="",
        help="Optional left mirror target as screen,x,y,radius; defaults to the configured left rear mirror AOI.",
    )
    p.add_argument(
        "--tor-mirror-hud-rear",
        default="",
        help="Optional rear mirror target as screen,x,y,radius; defaults to the configured center rear mirror AOI.",
    )
    p.add_argument(
        "--tor-mirror-hud-right",
        default="",
        help="Optional right mirror target as screen,x,y,radius; defaults to the configured right rear mirror AOI.",
    )
    p.add_argument(
        "--disable-takeover-requests",
        action="store_true",
        help="Diagnostic mode: ignore takeover requests so natural closing distance can be measured.",
    )
    p.add_argument(
        "--takeover-input",
        choices=("keyboard", "g29", "keyboard+g29"),
        default="keyboard",
        help="Manual takeover input source. g29 replaces WASD with Logitech G29 steering-wheel movement.",
    )
    p.add_argument(
        "--g29-device-hint",
        default="G29,Logitech",
        help="Comma-separated joystick name hints used to find the Logitech G29 via WinMM.",
    )
    p.add_argument(
        "--g29-steer-threshold",
        type=float,
        default=0.08,
        help="Normalized wheel movement threshold that counts as intentional G29 takeover.",
    )
    p.add_argument(
        "--g29-takeover-hold-s",
        type=float,
        default=0.20,
        help="How long steering must stay past the G29 threshold before takeover is accepted.",
    )
    p.add_argument(
        "--g29-pedal-threshold",
        type=float,
        default=0.18,
        help="Deprecated/no-op; G29 takeover uses steering-wheel movement only.",
    )
    p.add_argument(
        "--g29-manual-control",
        action="store_true",
        default=True,
        help="After G29 takeover, drive ego with G29 steering and automatic cruise speed.",
    )
    p.add_argument(
        "--no-g29-manual-control",
        action="store_false",
        dest="g29_manual_control",
        help="Use G29 only as a takeover trigger, without applying steering control to ego.",
    )
    p.add_argument(
        "--g29-steer-deadzone",
        type=float,
        default=0.04,
        help="Normalized steering deadzone for G29 manual control.",
    )
    p.add_argument(
        "--g29-steer-gain",
        type=float,
        default=1.0,
        help="Gain applied to G29 steering before sending CARLA VehicleControl. 1.0 keeps the normalized wheel input linear.",
    )
    p.add_argument(
        "--g29-cruise-speed-kmh",
        type=float,
        default=0.0,
        help="Cruise speed during G29 manual control. 0 uses the scenario ego speed.",
    )
    p.add_argument(
        "--g29-velocity-assist",
        action="store_true",
        help="Also force ego target velocity during G29 manual control. Off by default so cockpit steering animation follows VehicleControl.",
    )
    p.add_argument(
        "--g29-yaw-assist",
        action="store_true",
        help="Also force ego target angular velocity during G29 manual control. Off by default because it can bypass cockpit wheel animation.",
    )
    p.add_argument(
        "--g29-visual-steer-file",
        default=DEFAULT_G29_VISUAL_STEER_FILE,
        help="File consumed by DReyeVR UE to animate the cockpit steering wheel during Python-side G29 control. Use empty string to disable.",
    )
    p.add_argument(
        "--g29-sim-state-file",
        default=DEFAULT_G29_SIM_STATE_FILE,
        help="File written by DReyeVR UE with simulator-internal G29 steering/button state. Use empty string to disable.",
    )
    p.add_argument(
        "--g29-python-state-file",
        default=DEFAULT_G29_PY_STATE_FILE,
        help="File written by Python with G29 steering/button state for DReyeVR UE to switch AUTO ON/OFF. Use empty string to disable.",
    )
    p.add_argument(
        "--g29-return-button",
        type=int,
        default=23,
        help="Zero-based G29 button index used instead of SPACE to return to autonomous driving after manual takeover.",
    )
    p.add_argument(
        "--takeover-on-audio-file",
        default=DEFAULT_TAKEOVER_ON_AUDIO_FILE,
        help="MP3/WAV file played when the driver takes over manually.",
    )
    p.add_argument(
        "--return-auto-off-audio-file",
        default=DEFAULT_RETURN_AUTO_OFF_AUDIO_FILE,
        help="MP3/WAV file played when control returns to autonomous driving.",
    )
    p.add_argument(
        "--no-takeover-audio",
        action="store_true",
        help="Disable takeover on/off sound effects.",
    )
    p.add_argument(
        "--distance-crash-threshold-m",
        type=float,
        default=0.0,
        help="Diagnostic mode: print when ego and --distance-crash-role are at or below this distance.",
    )
    p.add_argument("--distance-crash-role", default="vehicle_1")
    p.add_argument(
        "--tor-fallback-body-debug",
        action="store_true",
        help="Print high-frequency ego body state while a TOR fallback action is active.",
    )
    p.add_argument(
        "--tor-fallback-body-debug-interval-s",
        type=float,
        default=0.25,
        help="Seconds between --tor-fallback-body-debug samples.",
    )
    p.add_argument(
        "--queen-vehicles",
        "--ambient-traffic",
        dest="queen_vehicles",
        action="store_true",
        help="Spawn Traffic Manager background/queue vehicles around the scene for visual realism.",
    )
    p.add_argument(
        "--queen-vehicle-count",
        type=int,
        default=4,
        help="Number of background/queue vehicles to spawn when --queen-vehicles is enabled.",
    )
    p.add_argument(
        "--queen-vehicle-spacing-m",
        type=float,
        default=90.0,
        help="Base longitudinal spacing for background/queue vehicles.",
    )
    p.add_argument(
        "--queen-vehicle-safe-gap-m",
        type=float,
        default=70.0,
        help="Minimum same-lane gap from scenario vehicles for background/queue vehicles.",
    )
    p.add_argument(
        "--queen-vehicle-speed-kmh",
        type=float,
        default=50.0,
        help="Target Traffic Manager speed for background/queue vehicles.",
    )
    p.add_argument(
        "--queen-vehicle-despawn-distance-m",
        type=float,
        default=320.0,
        help="Destroy each background/queue vehicle after it drives this far from its spawn point. Use 0 to disable.",
    )
    p.add_argument(
        "--queen-vehicle-blueprints",
        default="vehicle.audi.etron,vehicle.lincoln.mkz_2020,vehicle.tesla.model3,vehicle.mercedes.coupe",
        help="Comma-separated blueprint filters used for background/queue vehicles.",
    )
    return p.parse_args(argv)


# ============================================================================
# Schema
# ============================================================================

def load_scenario(name, outputs_dir):
    path = outputs_dir / (name + ".json")
    if not path.exists():
        print("Scenario not found: " + str(path), file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


class KeyEdgeDetector(object):
    """Windows global key edge detector.

    Uses GetAsyncKeyState so the CARLA/nDisplay window can have focus while the
    runner still receives a takeover key press.
    """

    VK_CODES = {
        "SPACE": 0x20,
        "B": 0x42,
        "N": 0x4E,
        "W": 0x57,
        "A": 0x41,
        "S": 0x53,
        "D": 0x44,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
    }

    def __init__(self, keys):
        self._available = False
        self._last_state = {}
        self._keys = []
        for key in keys or []:
            key = _normalize_key_name(key)
            if key in self.VK_CODES and key not in self._keys:
                self._keys.append(key)
                self._last_state[key] = False
        if sys.platform.startswith("win") and self._keys:
            try:
                import ctypes
                self._user32 = ctypes.windll.user32
                self._available = True
            except Exception:
                self._available = False

    def available(self):
        return self._available

    def describe(self):
        if not self._keys:
            return "keyboard(no takeover keys)"
        return "keyboard(" + ",".join(sorted(self._keys)) + ")"

    def pressed_edges(self):
        edges = set()
        if not self._available:
            return edges
        for key in self._keys:
            cur = bool(self._user32.GetAsyncKeyState(self.VK_CODES[key]) & 0x8000)
            if cur and not self._last_state.get(key, False):
                edges.add(key)
            self._last_state[key] = cur
        return edges


class G29EdgeDetector(object):
    """Logitech G29 detector.

    Preferred source is the DReyeVR simulator bridge because UE can read the
    Logitech wheel without depending on a Python window being focused. WinMM and
    pygame remain fallbacks for older simulator builds.
    """

    is_g29 = True

    JOY_RETURNALL = 0x000000FF
    JOYERR_NOERROR = 0
    AXIS_NAMES = ("X", "Y", "Z", "R", "U", "V")

    def __init__(
        self,
        device_hint="G29,Logitech",
        steer_threshold=0.08,
        pedal_threshold=0.18,
        return_button=0,
        sim_state_file=DEFAULT_G29_SIM_STATE_FILE,
        takeover_hold_s=0.20,
    ):
        self._available = False
        self._backend = ""
        self._pygame = None
        self._joystick = None
        self._winmm = None
        self._joystick_id = None
        self._device_name = ""
        self._axis_ranges = {}
        self._baseline_axes = {}
        self._baseline_buttons = 0
        self._steer_axis_name = None
        self._steer_axis_reported = False
        self._last_active_tokens = set()
        self._pygame_pedal_baseline_settled = True
        self._warned_read_error = False
        self._sim_bridge_warned_stale = False
        self._steer_active_since = None
        self._center_offset = None
        self._center_last_update_s = 0.0
        self._center_reported = False
        self._sim_state_path = str(sim_state_file or "").strip()
        self._sim_bridge_enabled = bool(self._sim_state_path)
        self._steer_threshold = max(0.005, float(steer_threshold or 0.08))
        self._pedal_threshold = max(0.01, float(pedal_threshold or 0.18))
        self._return_button = max(0, int(return_button or 0))
        self._takeover_hold_s = max(0.0, float(takeover_hold_s or 0.0))
        self._hints = [
            str(part).strip().lower()
            for part in str(device_hint or "G29,Logitech").split(",")
            if str(part).strip()
        ]
        if sys.platform.startswith("win"):
            self._init_winmm()
        if not self._available:
            self._init_pygame()
        if not self._available and self._sim_bridge_enabled:
            self._message = "waiting for DReyeVR simulator G29 bridge"
        elif not self._available:
            self._message = getattr(self, "_message", "G29 not detected")

    def _init_winmm(self):
        if not sys.platform.startswith("win"):
            self._message = "WinMM joystick input is only available on Windows"
            return False
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as exc:
            self._message = "ctypes unavailable: {0}".format(exc)
            return False

        class JOYCAPSW(ctypes.Structure):
            _fields_ = [
                ("wMid", wintypes.WORD),
                ("wPid", wintypes.WORD),
                ("szPname", wintypes.WCHAR * 32),
                ("wXmin", wintypes.UINT),
                ("wXmax", wintypes.UINT),
                ("wYmin", wintypes.UINT),
                ("wYmax", wintypes.UINT),
                ("wZmin", wintypes.UINT),
                ("wZmax", wintypes.UINT),
                ("wNumButtons", wintypes.UINT),
                ("wPeriodMin", wintypes.UINT),
                ("wPeriodMax", wintypes.UINT),
                ("wRmin", wintypes.UINT),
                ("wRmax", wintypes.UINT),
                ("wUmin", wintypes.UINT),
                ("wUmax", wintypes.UINT),
                ("wVmin", wintypes.UINT),
                ("wVmax", wintypes.UINT),
                ("wCaps", wintypes.UINT),
                ("wMaxAxes", wintypes.UINT),
                ("wNumAxes", wintypes.UINT),
                ("wMaxButtons", wintypes.UINT),
                ("szRegKey", wintypes.WCHAR * 32),
                ("szOEMVxD", wintypes.WCHAR * 260),
            ]

        class JOYINFOEX(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("dwXpos", wintypes.DWORD),
                ("dwYpos", wintypes.DWORD),
                ("dwZpos", wintypes.DWORD),
                ("dwRpos", wintypes.DWORD),
                ("dwUpos", wintypes.DWORD),
                ("dwVpos", wintypes.DWORD),
                ("dwButtons", wintypes.DWORD),
                ("dwButtonNumber", wintypes.DWORD),
                ("dwReserved1", wintypes.DWORD),
                ("dwReserved2", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._JOYCAPSW = JOYCAPSW
        self._JOYINFOEX = JOYINFOEX
        try:
            self._winmm = ctypes.windll.winmm
            self._select_device()
            return self._available
        except Exception as exc:
            self._message = "G29 WinMM init failed: {0}".format(exc)
            return False

    def available(self):
        return self._available or self._sim_bridge_enabled

    def reset_for_scenario(self):
        self._last_active_tokens = set()
        self._steer_active_since = None
        self._warned_read_error = False
        self._sim_bridge_warned_stale = False

    def _center_correct_steer(self, steer):
        raw = max(-1.0, min(1.0, float(steer or 0.0)))
        now = time.monotonic()
        if self._center_offset is None:
            initial_center_limit = max(0.055, min(0.075, self._steer_threshold * 0.80))
            if abs(raw) <= initial_center_limit:
                self._center_offset = raw
            else:
                self._center_offset = 0.0
            self._center_last_update_s = now
            if abs(self._center_offset) >= 0.01 and not self._center_reported:
                print("G29 steering center offset calibrated: {0:+.3f}".format(self._center_offset))
                self._center_reported = True
        corrected = raw - float(self._center_offset or 0.0)
        center_deadzone = max(0.025, min(0.055, self._steer_threshold * 0.55))
        if (
            abs(raw) <= self._steer_threshold
            and abs(corrected) <= self._steer_threshold
            and now - self._center_last_update_s >= 0.10
        ):
            self._center_offset = float(self._center_offset or 0.0) * 0.96 + raw * 0.04
            self._center_last_update_s = now
            corrected = raw - float(self._center_offset or 0.0)
        if abs(corrected) <= center_deadzone:
            return 0.0
        return max(-1.0, min(1.0, corrected))

    def describe(self):
        if self._sim_bridge_enabled:
            if self._available:
                return "G29(sim_bridge={0}; fallback={1}, id={2}, return_button={3})".format(
                    self._sim_state_path,
                    self._backend or "unknown",
                    self._joystick_id,
                    self._return_button,
                )
            return "G29(sim_bridge={0}, return_button={1})".format(
                self._sim_state_path,
                self._return_button,
            )
        if self._available:
            return "G29({0}, backend={1}, id={2}, return_button={3})".format(
                self._device_name or "joystick",
                self._backend or "unknown",
                self._joystick_id,
                self._return_button,
            )
        return "G29 unavailable: {0}".format(getattr(self, "_message", "not detected"))

    def _init_pygame(self):
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
            import pygame
            pygame.init()
            pygame.joystick.init()
            count = int(pygame.joystick.get_count())
            if count <= 0:
                self._message = "pygame found no joystick devices"
                return False
            selected = None
            for joystick_id in range(count):
                joystick = pygame.joystick.Joystick(joystick_id)
                joystick.init()
                name = str(joystick.get_name() or "").strip()
                if any(hint in name.lower() for hint in self._hints):
                    selected = (joystick_id, name, joystick)
                    break
                if selected is None:
                    selected = (joystick_id, name, joystick)
            joystick_id, name, joystick = selected
            self._pygame = pygame
            self._joystick = joystick
            self._joystick_id = joystick_id
            self._device_name = name or "joystick"
            self._backend = "pygame"
            settle_until = time.monotonic() + 0.20
            while time.monotonic() < settle_until:
                try:
                    pygame.event.pump()
                except Exception:
                    pass
                time.sleep(0.02)
            self._baseline_axes = self._pygame_axes()
            self._baseline_buttons = self._pygame_buttons()
            self._pygame_pedal_baseline_settled = not all(
                abs(self._baseline_axes.get(str(idx), 0.0)) < 1e-6
                for idx in range(1, len(self._baseline_axes))
            )
            self._available = True
            return True
        except Exception as exc:
            self._message = "pygame joystick init failed: {0}".format(exc)
            return False

    def _pygame_axes(self):
        axes = {}
        if self._pygame is None or self._joystick is None:
            return axes
        try:
            for _event in self._pygame.event.get():
                pass
        except Exception:
            pass
        for idx in range(int(self._joystick.get_numaxes())):
            try:
                axes[str(idx)] = max(-1.0, min(1.0, float(self._joystick.get_axis(idx))))
            except Exception:
                axes[str(idx)] = 0.0
        return axes

    def _pygame_buttons(self):
        if self._joystick is None:
            return 0
        value = 0
        for idx in range(int(self._joystick.get_numbuttons())):
            try:
                if self._joystick.get_button(idx):
                    value |= (1 << idx)
            except Exception:
                pass
        return value

    def _read_sim_state(self):
        if not self._sim_bridge_enabled:
            return None
        try:
            path = Path(self._sim_state_path)
            stat = path.stat()
            if time.time() - stat.st_mtime > 3.0:
                if not self._sim_bridge_warned_stale:
                    print("WARNING: DReyeVR G29 simulator bridge is stale; using fallback joystick backend if available.")
                    self._sim_bridge_warned_stale = True
                return None
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        fields = {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        try:
            steer = max(-1.0, min(1.0, float(fields.get("steer", "0") or 0.0)))
        except Exception:
            steer = 0.0
        steer = self._center_correct_steer(steer)
        buttons = set()
        for raw in fields.get("buttons", "").replace(";", ",").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                buttons.add(int(raw))
            except Exception:
                pass
        self._sim_bridge_warned_stale = False
        return {"steer": steer, "buttons": buttons}

    def _active_tokens_from_steer_buttons(self, steer, buttons):
        active = set()
        steer = float(steer or 0.0)
        now = time.monotonic()
        if abs(steer) >= self._steer_threshold:
            if self._steer_active_since is None:
                self._steer_active_since = now
            steer_confirmed = (now - self._steer_active_since) >= self._takeover_hold_s
        else:
            self._steer_active_since = None
            steer_confirmed = False
        if steer_confirmed:
            active.update(["G29", "WHEEL"])
            active.add("WHEEL_LEFT" if steer < 0.0 else "WHEEL_RIGHT")
        if int(self._return_button) in set(buttons or []):
            active.update(["G29_RETURN", "G29_BUTTON_{0}".format(self._return_button + 1)])
        return active

    def _steer_delta_from_axes(self, axes, default_axis):
        if not axes:
            return default_axis, 0.0
        deltas = {}
        for name, value in axes.items():
            deltas[name] = float(value) - float(self._baseline_axes.get(name, 0.0))
        selected = self._steer_axis_name if self._steer_axis_name in deltas else None
        if selected is None or abs(deltas.get(selected, 0.0)) < self._steer_threshold:
            selected = max(deltas.keys(), key=lambda key: abs(deltas.get(key, 0.0)))
            if abs(deltas.get(selected, 0.0)) >= self._steer_threshold:
                self._steer_axis_name = selected
                if not self._steer_axis_reported:
                    print("G29 steering axis selected: {0}".format(selected))
                    self._steer_axis_reported = True
        if selected is None:
            selected = default_axis if default_axis in deltas else next(iter(deltas.keys()))
        steer = self._center_correct_steer(float(deltas.get(selected, 0.0)))
        return selected, steer

    def _pressed_edges_pygame(self):
        axes = self._pygame_axes()
        if (
            not self._pygame_pedal_baseline_settled
            and len(axes) > 1
            and abs(axes.get("0", 0.0) - self._baseline_axes.get("0", 0.0)) < self._steer_threshold
            and all(axes.get(str(idx), 0.0) >= 0.95 for idx in range(1, len(axes)))
        ):
            self._baseline_axes = dict(axes)
            self._last_active_tokens = set()
            self._pygame_pedal_baseline_settled = True
            return set()
        active = set()
        _axis_name, steer = self._steer_delta_from_axes(axes, "0")
        button_mask = self._pygame_buttons()
        buttons = set(
            idx for idx in range(max(64, self._return_button + 1))
            if button_mask & (1 << idx)
        )
        active = self._active_tokens_from_steer_buttons(steer, buttons)
        edges = active.difference(self._last_active_tokens)
        self._last_active_tokens = active
        return edges

    def current_steer(self):
        state = self._read_sim_state()
        if state is not None:
            return float(state.get("steer", 0.0))
        if not self._available:
            return 0.0
        if self._backend == "pygame":
            axes = self._pygame_axes()
            _axis_name, steer = self._steer_delta_from_axes(axes, "0")
            return steer
        info = self._read_info(self._joystick_id)
        if info is None:
            return 0.0
        axes = self._normalized_axes(info)
        _axis_name, steer = self._steer_delta_from_axes(axes, "X")
        return steer

    def takeover_active(self):
        if not self.available():
            return False
        try:
            active = self._active_tokens_from_steer_buttons(self.current_steer(), set())
            return "G29" in active
        except Exception:
            return False

    def _read_info(self, joystick_id):
        info = self._JOYINFOEX()
        info.dwSize = self._ctypes.sizeof(self._JOYINFOEX)
        info.dwFlags = self.JOY_RETURNALL
        rc = self._winmm.joyGetPosEx(int(joystick_id), self._ctypes.byref(info))
        if rc != self.JOYERR_NOERROR:
            return None
        return info

    def _select_device(self):
        candidates = []
        for joystick_id in range(int(self._winmm.joyGetNumDevs())):
            caps = self._JOYCAPSW()
            rc = self._winmm.joyGetDevCapsW(
                int(joystick_id),
                self._ctypes.byref(caps),
                self._ctypes.sizeof(caps),
            )
            if rc != self.JOYERR_NOERROR:
                continue
            info = self._read_info(joystick_id)
            if info is None:
                continue
            candidates.append((joystick_id, str(caps.szPname or "").strip(), caps, info))
        if not candidates:
            self._message = "no Windows joystick devices found"
            return
        selected = None
        for candidate in candidates:
            name_lower = candidate[1].lower()
            if any(hint in name_lower for hint in self._hints):
                selected = candidate
                break
        if selected is None:
            selected = candidates[0]
        joystick_id, name, caps, info = selected
        self._joystick_id = joystick_id
        self._device_name = name or "joystick"
        self._axis_ranges = {
            "X": (caps.wXmin, caps.wXmax),
            "Y": (caps.wYmin, caps.wYmax),
            "Z": (caps.wZmin, caps.wZmax),
            "R": (caps.wRmin, caps.wRmax),
            "U": (caps.wUmin, caps.wUmax),
            "V": (caps.wVmin, caps.wVmax),
        }
        self._baseline_axes = self._normalized_axes(info)
        self._baseline_buttons = int(info.dwButtons)
        self._backend = "winmm"
        self._available = True

    def _normalize_axis(self, name, raw_value):
        minimum, maximum = self._axis_ranges.get(name, (0, 65535))
        minimum = float(minimum)
        maximum = float(maximum)
        if maximum <= minimum:
            return 0.0
        return max(-1.0, min(1.0, ((float(raw_value) - minimum) / (maximum - minimum)) * 2.0 - 1.0))

    def _normalized_axes(self, info):
        raw = {
            "X": info.dwXpos,
            "Y": info.dwYpos,
            "Z": info.dwZpos,
            "R": info.dwRpos,
            "U": info.dwUpos,
            "V": info.dwVpos,
        }
        return dict((name, self._normalize_axis(name, raw[name])) for name in self.AXIS_NAMES)

    def pressed_edges(self):
        if not self._available:
            state = self._read_sim_state()
            if state is None:
                return set()
            active = self._active_tokens_from_steer_buttons(
                state.get("steer", 0.0),
                state.get("buttons", set()),
            )
            edges = active.difference(self._last_active_tokens)
            self._last_active_tokens = active
            return edges
        state = self._read_sim_state()
        if state is not None:
            active = self._active_tokens_from_steer_buttons(
                state.get("steer", 0.0),
                state.get("buttons", set()),
            )
            edges = active.difference(self._last_active_tokens)
            self._last_active_tokens = active
            return edges
        if self._backend == "pygame":
            return self._pressed_edges_pygame()
        info = self._read_info(self._joystick_id)
        if info is None:
            if not self._warned_read_error:
                print("WARNING: G29 joystick read failed; takeover input may be unavailable.")
                self._warned_read_error = True
            return set()

        axes = self._normalized_axes(info)
        _axis_name, steer = self._steer_delta_from_axes(axes, "X")
        buttons = set(
            idx for idx in range(max(64, self._return_button + 1))
            if int(info.dwButtons) & (1 << idx)
        )
        active = self._active_tokens_from_steer_buttons(steer, buttons)

        edges = active.difference(self._last_active_tokens)
        self._last_active_tokens = active
        return edges


class CombinedEdgeDetector(object):
    def __init__(self, detectors):
        self._detectors = [detector for detector in detectors if detector is not None]
        self.g29_available = any(
            getattr(detector, "is_g29", False) and detector.available()
            for detector in self._detectors
        )

    def available(self):
        return any(detector.available() for detector in self._detectors)

    def reset_for_scenario(self):
        for detector in self._detectors:
            reset = getattr(detector, "reset_for_scenario", None)
            if reset is not None:
                reset()

    def describe(self):
        if not self._detectors:
            return "no input detectors"
        return "; ".join(
            detector.describe() if hasattr(detector, "describe") else detector.__class__.__name__
            for detector in self._detectors
        )

    def pressed_edges(self):
        edges = set()
        for detector in self._detectors:
            edges.update(detector.pressed_edges())
        return edges

    def current_g29_steer(self):
        for detector in self._detectors:
            if getattr(detector, "is_g29", False) and detector.available():
                current_steer = getattr(detector, "current_steer", None)
                if current_steer is not None:
                    return current_steer()
        return 0.0

    def g29_takeover_active(self):
        for detector in self._detectors:
            if getattr(detector, "is_g29", False) and detector.available():
                takeover_active = getattr(detector, "takeover_active", None)
                if takeover_active is not None and takeover_active():
                    return True
        return False


def _normalize_key_name(key):
    text = str(key or "SPACE").strip().upper()
    aliases = {
        "ARROWUP": "UP",
        "UP_ARROW": "UP",
        "ARROWDOWN": "DOWN",
        "DOWN_ARROW": "DOWN",
        "ARROWLEFT": "LEFT",
        "LEFT_ARROW": "LEFT",
        "ARROWRIGHT": "RIGHT",
        "RIGHT_ARROW": "RIGHT",
        "SPACEBAR": "SPACE",
        "FORWARD": "W",
        "THROTTLE": "W",
        "LEFT_STEER": "A",
        "BRAKE": "S",
        "REVERSE": "S",
        "RIGHT_STEER": "D",
        "LOGITECH_G29": "G29",
        "LOGITECH G29": "G29",
        "G29_WHEEL": "G29",
        "STEERING_WHEEL": "G29",
        "WHEEL_TAKEOVER": "G29",
    }
    text = aliases.get(text, text)
    if text in ("空格", "SPACEBAR"):
        return "SPACE"
    return text


def _normalize_key_list(value, default=None):
    if value is None:
        value = default
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    else:
        parts = list(value or [])
    keys = []
    for item in parts:
        key = _normalize_key_name(item)
        if key and key not in keys:
            keys.append(key)
    return keys


def _takeover_request_keys(req):
    key = _normalize_key_name(req.get("key", "SPACE"))
    if key == "ANY_ARROW":
        return ["UP", "DOWN", "LEFT", "RIGHT"]
    keys = _normalize_key_list(req.get("correct_keys") or req.get("keys"), default=[key])
    if "ANY_ARROW" in keys:
        return ["UP", "DOWN", "LEFT", "RIGHT"]
    return keys or [key]


def _normalize_takeover_input_mode(value):
    text = str(value or "keyboard").strip().lower().replace("_", "+")
    aliases = {
        "both": "keyboard+g29",
        "g29+keyboard": "keyboard+g29",
        "keyboard-g29": "keyboard+g29",
        "keyboardg29": "keyboard+g29",
        "wheel": "g29",
        "logitech": "g29",
        "logitech+g29": "g29",
    }
    text = aliases.get(text, text)
    if text not in ("keyboard", "g29", "keyboard+g29"):
        return "keyboard"
    return text


def _takeover_mode_uses_g29(mode):
    return _normalize_takeover_input_mode(mode) in ("g29", "keyboard+g29")


def _augment_pressed_with_g29_hold(pressed, detector):
    pressed = set(pressed or [])
    if detector is None:
        return pressed
    g29_takeover_active = getattr(detector, "g29_takeover_active", None)
    if g29_takeover_active is not None and g29_takeover_active():
        pressed.add("G29")
    return pressed


def _apply_takeover_input_mode(takeover_states, experiment_tor_windows, args):
    mode = _normalize_takeover_input_mode(getattr(args, "takeover_input", "keyboard"))
    if not _takeover_mode_uses_g29(mode):
        return mode
    replace_keyboard_keys = mode == "g29"
    g29_return_button = max(0, int(getattr(args, "g29_return_button", 0) or 0))
    g29_return_label = "G29 button {0}".format(g29_return_button + 1)
    for req in takeover_states or []:
        if replace_keyboard_keys:
            req["key"] = "G29"
            req["keys"] = ["G29"]
            req["key_label"] = "G29"
        else:
            keys = _normalize_key_list(req.get("keys"), default=[req.get("key", "SPACE")])
            if "G29" not in keys:
                keys.append("G29")
            req["keys"] = keys
            req["key_label"] = "/".join(keys)
    for window in experiment_tor_windows or []:
        if replace_keyboard_keys:
            window["keys"] = ["G29"]
        else:
            keys = _normalize_key_list(window.get("keys"), default=["W", "A", "S", "D"])
            if "G29" not in keys:
                keys.append("G29")
            window["keys"] = keys
        window["return_key"] = "G29_RETURN"
        window["return_key_label"] = g29_return_label
        return_text = str(window.get("return_to_auto_text") or "").strip()
        if not return_text or "space" in return_text.lower():
            window["return_to_auto_text"] = "Please press {0} to return to autonomous driving.".format(
                g29_return_label)
    return mode


def _build_takeover_edge_detector(takeover_keys, args):
    mode = _normalize_takeover_input_mode(getattr(args, "takeover_input", "keyboard"))
    normalized_keys = _normalize_key_list(takeover_keys, default=[])
    detectors = []
    if mode == "keyboard":
        keyboard_keys = normalized_keys
    elif mode == "keyboard+g29":
        keyboard_keys = normalized_keys
    else:
        keyboard_keys = [key for key in normalized_keys if key == "SPACE"]
    if keyboard_keys:
        detectors.append(KeyEdgeDetector(keyboard_keys))
    if _takeover_mode_uses_g29(mode):
        g29_key = (
            str(getattr(args, "g29_device_hint", "G29,Logitech")),
            float(getattr(args, "g29_steer_threshold", 0.08) or 0.08),
            float(getattr(args, "g29_pedal_threshold", 0.18) or 0.18),
            int(getattr(args, "g29_return_button", 0) or 0),
            str(getattr(args, "g29_sim_state_file", DEFAULT_G29_SIM_STATE_FILE) or ""),
            float(getattr(args, "g29_takeover_hold_s", 0.20) or 0.20),
        )
        cached_key = getattr(args, "_cached_g29_detector_key", None)
        cached_detector = getattr(args, "_cached_g29_detector", None)
        if getattr(args, "continuous_session", False) and cached_key == g29_key and cached_detector is not None:
            reset = getattr(cached_detector, "reset_for_scenario", None)
            if reset is not None:
                reset()
            detectors.append(cached_detector)
        else:
            detector = G29EdgeDetector(
                device_hint=getattr(args, "g29_device_hint", "G29,Logitech"),
                steer_threshold=getattr(args, "g29_steer_threshold", 0.08),
                pedal_threshold=getattr(args, "g29_pedal_threshold", 0.18),
                return_button=getattr(args, "g29_return_button", 0),
                sim_state_file=getattr(args, "g29_sim_state_file", DEFAULT_G29_SIM_STATE_FILE),
                takeover_hold_s=getattr(args, "g29_takeover_hold_s", 0.20),
            )
            if getattr(args, "continuous_session", False):
                args._cached_g29_detector_key = g29_key
                args._cached_g29_detector = detector
            detectors.append(detector)
    return CombinedEdgeDetector(detectors)


def _marker_value(value):
    text = str(value)
    return text.replace(";", "_").replace(" ", "_").replace("\n", "_").replace("\r", "_")


def _format_marker(event, **fields):
    parts = [str(event)]
    for key in sorted(fields.keys()):
        value = fields[key]
        if value is None:
            continue
        if isinstance(value, float):
            value = "{0:.4f}".format(value)
        parts.append("{0}={1}".format(key, _marker_value(value)))
    return ";".join(parts)


class ScenarioMarkerOutlet(object):
    """UDP client feeding the pylsl bridge and local visual sidecars.

    The CARLA runner uses the Python 3.7 CARLA venv, which currently has no
    pylsl. A tiny bridge process in the AOI venv publishes the actual LSL
    stream while this runner sends action markers over localhost UDP. The
    optional visual marker channel is local-only and lets viewers react to the
    same scenario state without subscribing to LSL in this Python environment.
    """

    def __init__(self, args, scenario_name):
        self.lsl_enabled = bool(getattr(args, "lsl_markers", False))
        self.visual_enabled = not bool(getattr(args, "no_visual_markers", False))
        self.enabled = self.lsl_enabled or self.visual_enabled
        self.host = getattr(args, "lsl_bridge_host", "127.0.0.1")
        self.port = int(getattr(args, "lsl_bridge_port", 17364))
        self.visual_host = getattr(args, "visual_marker_host", "127.0.0.1")
        self.visual_port = int(getattr(args, "visual_marker_port", 17367))
        self.stream_name = getattr(args, "lsl_stream_name", "CarlaScenarioMarkers")
        self.telemetry_stream_name = getattr(args, "lsl_telemetry_stream_name", "CarlaEgoTelemetry")
        self.telemetry_enabled = self.lsl_enabled and not bool(getattr(args, "no_lsl_telemetry", False))
        self.scenario_name = scenario_name
        self._sock = None
        self._bridge_proc = None
        self._visual_warned = False
        if not self.enabled:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.lsl_enabled:
            self._start_bridge(args)
        atexit.register(self.close)
        self.push(
            "MARKER_STREAM_READY",
            scenario=scenario_name,
            stream=self.stream_name,
            telemetry_stream=self.telemetry_stream_name if self.telemetry_enabled else "",
        )

    def _start_bridge(self, args):
        py = getattr(args, "lsl_bridge_python", "")
        script = Path(__file__).resolve().parent / "lsl_marker_bridge.py"
        if not py or not Path(py).exists():
            print("WARNING: LSL marker bridge python not found: {0}".format(py))
            return
        if not script.exists():
            print("WARNING: LSL marker bridge script not found: {0}".format(script))
            return
        cmd = [
            py,
            "-u",
            str(script),
            "--port",
            str(self.port),
            "--stream-name",
            self.stream_name,
            "--source-id",
            "carla_scenario_{0}".format(_marker_value(self.scenario_name)),
        ]
        if self.telemetry_enabled:
            cmd += [
                "--telemetry-stream-name",
                self.telemetry_stream_name,
                "--telemetry-source-id",
                "carla_ego_{0}".format(_marker_value(self.scenario_name)),
            ]
        else:
            cmd.append("--no-telemetry")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._bridge_proc = subprocess.Popen(
                cmd,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            time.sleep(0.20)
        except Exception as exc:
            print("WARNING: failed to start LSL marker bridge: {0}".format(exc))
            self._bridge_proc = None

    def push(self, event, **fields):
        if not self.enabled or self._sock is None:
            return
        marker = _format_marker(event, **fields)
        payload = json.dumps(
            {
                "marker": marker,
                "stream_name": self.stream_name,
                "scenario": self.scenario_name,
                "sent_monotonic": time.monotonic(),
                "telemetry_stream_name": self.telemetry_stream_name,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if self.lsl_enabled:
            try:
                self._sock.sendto(payload, (self.host, self.port))
            except Exception as exc:
                print("WARNING: failed to send LSL marker '{0}': {1}".format(marker, exc))
        self._send_visual_payload(payload, marker)

    def push_telemetry(self, values):
        if not self.telemetry_enabled or self._sock is None:
            return
        payload = json.dumps(
            {
                "telemetry": {
                    "stream_name": self.telemetry_stream_name,
                    "scenario": self.scenario_name,
                    "values": values,
                },
                "sent_monotonic": time.monotonic(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self._sock.sendto(payload, (self.host, self.port))
        except Exception as exc:
            print("WARNING: failed to send CARLA ego telemetry: {0}".format(exc))

    def push_visual(self, event, **fields):
        if not self.visual_enabled or self._sock is None:
            return
        marker = _format_marker(event, **fields)
        payload = json.dumps(
            {
                "marker": marker,
                "stream_name": self.stream_name,
                "scenario": self.scenario_name,
                "sent_monotonic": time.monotonic(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_visual_payload(payload, marker)

    def _send_visual_payload(self, payload, marker):
        if not self.visual_enabled or self._sock is None:
            return
        try:
            self._sock.sendto(payload, (self.visual_host, self.visual_port))
        except Exception as exc:
            if not self._visual_warned:
                self._visual_warned = True
                print("WARNING: failed to send visual marker '{0}': {1}".format(marker, exc))

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._bridge_proc is not None:
            try:
                time.sleep(0.10)
                if self._bridge_proc.poll() is None:
                    self._bridge_proc.terminate()
            except Exception:
                pass
            self._bridge_proc = None


class AoiFeedbackReceiver(object):
    """Non-blocking local feedback receiver from the Neon AOI process."""

    def __init__(self, host="127.0.0.1", port=17365, enabled=True):
        self.enabled = bool(enabled)
        self.host = host
        self.port = int(port)
        self._sock = None
        if not self.enabled:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.host, self.port))
            self._sock.setblocking(False)
            print("AOI feedback receiver listening on {0}:{1}".format(self.host, self.port))
        except Exception as exc:
            print("WARNING: AOI feedback receiver unavailable: {0}".format(exc))
            self._sock = None
            self.enabled = False

    def drain(self):
        events = []
        if not self.enabled or self._sock is None:
            return events
        while True:
            try:
                payload, _addr = self._sock.recvfrom(4096)
            except socket.error:
                break
            try:
                text = payload.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"event": text}
                event = str(data.get("event", "") or "").strip()
                if event:
                    events.append((event, data))
            except Exception:
                pass
        return events

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class CollisionMonitor(object):
    def __init__(self, world, ego_actor, marker_outlet=None, owned_actors=None):
        self.world = world
        self.ego_actor = ego_actor
        self.marker_outlet = marker_outlet
        self.sensor = None
        self.count = 0
        self.latest = None
        self.raw_log_limit = 1
        self.raw_logged = 0
        self.suppress_events = False
        self._lock = threading.Lock()
        try:
            bp = world.get_blueprint_library().find("sensor.other.collision")
            self.sensor = world.spawn_actor(bp, carla.Transform(), attach_to=ego_actor)
            if owned_actors is not None:
                owned_actors.append(self.sensor)
            self.sensor.listen(self._on_collision)
            print("Collision monitor attached to ego.")
        except Exception as exc:
            print("WARNING: collision monitor unavailable: {0}".format(exc))
            self.sensor = None

    def _on_collision(self, event):
        other = getattr(event, "other_actor", None)
        impulse = getattr(event, "normal_impulse", None)
        impulse_mag = None
        if impulse is not None:
            try:
                impulse_mag = math.sqrt(impulse.x * impulse.x + impulse.y * impulse.y + impulse.z * impulse.z)
            except Exception:
                impulse_mag = None
        with self._lock:
            self.count += 1
            count = self.count
            should_log = (not self.suppress_events) and self.raw_logged < self.raw_log_limit
            if should_log:
                self.raw_logged += 1
            self.latest = {
                "other_actor_id": getattr(other, "id", None),
                "other_type_id": getattr(other, "type_id", None),
                "impulse": impulse_mag,
                "count": self.count,
            }
        if not should_log:
            return
        _push_marker(
            self.marker_outlet,
            "COLLISION_EVENT",
            collision_count=count,
            other_actor_id=getattr(other, "id", None),
            other_type_id=getattr(other, "type_id", None),
            impulse=impulse_mag,
        )

    def snapshot_count(self):
        with self._lock:
            return int(self.count)

    def snapshot(self):
        with self._lock:
            latest = dict(self.latest or {})
            latest["count"] = int(self.count)
            return latest

    def suppress_logging(self):
        with self._lock:
            self.suppress_events = True

    def close(self):
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except Exception:
                pass
            try:
                self.sensor.destroy()
            except Exception:
                pass
            self.sensor = None


def _push_marker(marker_outlet, event, **fields):
    _log_behavior(event, fields, marker_outlet=marker_outlet)
    if marker_outlet is not None:
        marker_outlet.push(event, **fields)


BEHAVIOR_LOG_EVENTS = set([
    "SCENARIO_START",
    "SCENARIO_END",
    "SCENARIO_PLAY_START",
    "SCENARIO_PLAY_END",
    "SCENARIO_DESIGN_WARNING",
    "VEHICLE_SPAWNED",
    "VEHICLE_REUSED",
    "VEHICLE_SCRIPT_START",
    "VEHICLE_SPEED_POINT",
    "VEHICLE_GAP_POINT",
    "VEHICLE_TIME_GAP_POINT",
    "VEHICLE_STOP_START",
    "VEHICLE_STOP_END",
    "LANE_CHANGE_START",
    "LANE_CHANGE_END",
    "FPE_ONSET",
    "HAZARD_ONSET",
    "HAZARD_END",
    "TOR_ALERT_ONSET",
    "ALERT_STAGE1_ONSET",
    "TOR_WINDOW_START",
    "TOR_WINDOW_END",
    "TOR_RESPONSE",
    "TOR_RESPONSE_LATE",
    "TOR_RESPONSE_IGNORED",
    "TOR_FINAL_REQUEST",
    "TOR_SUCCESS",
    "TOR_FAILURE",
    "TOR_NOTIFICATION",
    "AUTO_ON",
    "AUTO_OFF",
    "MANUAL_TAKEOVER",
    "MANUAL_COLLISION",
    "MANUAL_SAFE_HOLD_REACHED",
    "RETURN_TO_AUTO_REQUEST",
    "AUTO_RESUME",
    "SYSTEM_FALLBACK_START",
    "SYSTEM_FALLBACK_END",
    "COLLISION_EVENT",
    "TOR_FAIL_COLLISION",
    "ACCIDENT_DETECTED",
    "TAKEOVER_REQUEST_SHOWN",
    "TAKEOVER_AOI_CONFIRMED",
    "TAKEOVER_KEY",
    "TAKEOVER_KEY_IGNORED",
    "TAKEOVER_FORCE_BRAKE",
    "TAKEOVER_EXPIRED",
    "TOR_HOLD_START",
    "TOR_HOLD_RESOLVED",
    "TOR_HOLD_END",
    "TOR_HOLD_TIMEOUT",
    "AVOIDANCE_ACTION_START",
    "AVOIDANCE_ACTION_END",
    "OBSTACLE_DROP_SPAWNED",
    "OBSTACLE_DROP_DESTROYED",
    "PEDESTRIAN_SPAWNED",
    "PEDESTRIAN_CROSSING_START",
    "PEDESTRIAN_CROSSING_END",
    "PEDESTRIAN_DESTROYED",
    "DISTANCE_THRESHOLD_REACHED",
    "TOR_DYNAMIC_AOI_MODE_CHANGE",
    "COLLISION_RECOVERY_REQUIRED",
    "COLLISION_RECOVERY_START",
    "COLLISION_RECOVERY_END",
    "COLLISION_CONFIRM_POPUP_OPEN",
    "COLLISION_CONFIRM_POPUP_ACK",
])


def _log_behavior(event, fields, marker_outlet=None):
    if event not in BEHAVIOR_LOG_EVENTS:
        return
    fields = dict(fields or {})
    scenario = None
    try:
        scenario = getattr(marker_outlet, "scenario_name", None)
    except Exception:
        scenario = None
    scenario_t = fields.get("scenario_t", fields.get("scheduled_t", ""))
    if isinstance(scenario_t, float):
        time_text = "{0:.3f}s".format(scenario_t)
    elif scenario_t not in (None, ""):
        time_text = str(scenario_t) + "s"
    else:
        time_text = "?"
    ordered_keys = [
        "role",
        "actor_id",
        "event",
        "window",
        "response",
        "key",
        "expected_key",
        "outcome",
        "reason",
        "action",
        "from_lane",
        "to_lane",
        "lane",
        "speed_kmh",
        "target_speed_kmh",
        "gap_m",
        "time_gap_s",
        "kind",
        "source",
        "deadline_s",
        "rt_s",
        "valid",
        "correct",
        "aoi_confirmed",
        "scheduled_t",
        "duration_s",
    ]
    parts = []
    if scenario:
        parts.append("scenario={0}".format(_marker_value(scenario)))
    parts.append("t={0}".format(time_text))
    parts.append("event={0}".format(event))
    used = set(["scenario_t"])
    for key in ordered_keys:
        if key in fields and fields.get(key) is not None:
            parts.append("{0}={1}".format(key, _marker_value(fields.get(key))))
            used.add(key)
    for key in sorted(fields.keys()):
        if key in used or fields.get(key) is None:
            continue
        parts.append("{0}={1}".format(key, _marker_value(fields.get(key))))
    print("[BEHAVIOR] " + " ".join(parts))


def _calibrate_fixed_delta(world, current_fixed_delta, ticks):
    ticks = max(0, int(ticks))
    if ticks <= 0:
        return current_fixed_delta
    start = time.monotonic()
    completed = 0
    for _ in range(ticks):
        try:
            world.tick()
            completed += 1
        except Exception:
            break
    elapsed = time.monotonic() - start
    if completed <= 0 or elapsed <= 0.0:
        return current_fixed_delta
    observed = elapsed / float(completed)
    # Clamp out accidental long stalls while still allowing low-FPS nDisplay to
    # run in real time instead of stretching scenario seconds. The calibration
    # ticks only measure renderer/world tick cost; the live scenario loop also
    # spends time on lane-hold, vehicle-control and keyboard/takeover checks, so
    # leave a small headroom instead of running exactly at the measured limit.
    observed = max(1.0 / 60.0, min(0.10, observed))
    calibrated = max(current_fixed_delta, min(0.10, observed * 1.12))
    if calibrated > current_fixed_delta * 1.01:
        return calibrated
    return current_fixed_delta


def _apply_async_visual_mode(world, traffic_manager):
    settings = world.get_settings()
    settings.synchronous_mode = False
    try:
        settings.fixed_delta_seconds = None
    except Exception:
        pass
    world.apply_settings(settings)
    try:
        traffic_manager.set_synchronous_mode(False)
    except Exception:
        pass
    print("World switched to async-visual mode: UE4 renders freely; scenario controls follow wall time.")


# ============================================================================
# Lane geometry (mirror of run_scenario_native)
# ============================================================================

LANE_LEFT = "Left"
LANE_MIDDLE = "Middle"
LANE_RIGHT = "Right"
LANE_OUTER_RIGHT = "OuterRight"
LANE_QUEEN_FOURTH = "QueenFourth"
DEFAULT_VEHICLE_HALF_LENGTH_M = 2.25
DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M = DEFAULT_VEHICLE_HALF_LENGTH_M * 2.0
CARLA_STANDARD_GRAVITY_MPS2 = 9.80665
CARLA_OFFICIAL_DEFAULT_MASS_KG = 1000.0
CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M = 0.30
CARLA_OFFICIAL_DEFAULT_WHEEL_COUNT = 4
CARLA_OFFICIAL_DEFAULT_MAX_BRAKE_TORQUE_NM = 1500.0
CARLA_OFFICIAL_DEFAULT_TIRE_FRICTION = 2.0
CARLA_OFFICIAL_DEFAULT_ENGINE_TORQUE_NM = 500.0
CARLA_OFFICIAL_DEFAULT_FINAL_RATIO = 4.0
TIME_GAP_WARNING_LIMIT_RATIO = 0.5
TIME_GAP_NEAR_ZERO_S = 0.8


class LanePath(object):
    def __init__(self, name, transforms, step_m, origin_s=0.0):
        self.name = name
        self.transforms = transforms
        self.step_m = step_m
        self.origin_s = float(origin_s or 0.0)

    def sample(self, s):
        if not self.transforms:
            raise RuntimeError("LanePath " + self.name + " has no transforms")
        idx_f = (float(s or 0.0) + self.origin_s) / self.step_m
        if idx_f <= 0:
            return self.transforms[0]
        idx_lo = int(math.floor(idx_f))
        if idx_lo >= len(self.transforms) - 1:
            return self.transforms[-1]
        t = idx_f - idx_lo
        return _lerp_transform(self.transforms[idx_lo], self.transforms[idx_lo + 1], t)

    def nearest_s(self, location, fallback_s=0.0, window_m=60.0):
        if not self.transforms:
            return fallback_s
        center_idx = int(max(0.0, float(fallback_s or 0.0) + self.origin_s) / self.step_m)
        half = int(max(5.0, window_m) / self.step_m)
        lo = max(0, center_idx - half)
        hi = min(len(self.transforms) - 1, center_idx + half)
        best_idx = lo
        best_d2 = float("inf")
        for idx in range(lo, hi + 1):
            loc = self.transforms[idx].location
            dx = loc.x - location.x
            dy = loc.y - location.y
            dz = loc.z - location.z
            d2 = dx * dx + dy * dy + dz * dz
            if d2 < best_d2:
                best_d2 = d2
                best_idx = idx
        return best_idx * self.step_m - self.origin_s


def _lerp_transform(a, b, t):
    loc = carla.Location(
        a.location.x + (b.location.x - a.location.x) * t,
        a.location.y + (b.location.y - a.location.y) * t,
        a.location.z + (b.location.z - a.location.z) * t,
    )
    diff = ((b.rotation.yaw - a.rotation.yaw) + 180.0) % 360.0 - 180.0
    yaw = a.rotation.yaw + diff * t
    rot = carla.Rotation(
        pitch=a.rotation.pitch + (b.rotation.pitch - a.rotation.pitch) * t,
        yaw=yaw,
        roll=a.rotation.roll + (b.rotation.roll - a.rotation.roll) * t,
    )
    return carla.Transform(loc, rot)


# ============================================================================
# DReyeVR helpers
# ============================================================================

def _is_dreyevr_ego_actor(actor):
    role = actor.attributes.get("role_name", "")
    is_dreyevr = actor.type_id.startswith("harplab.dreyevr_vehicle")
    is_vehicle = actor.type_id.startswith("vehicle.") or is_dreyevr
    return is_dreyevr or (is_vehicle and role in ("hero", "ego"))


def _dreyevr_ego_sort_key(actor):
    return (0 if actor.type_id.startswith("harplab.dreyevr_vehicle") else 1, actor.id)


def _dreyevr_ego_candidates(world):
    return sorted(
        [actor for actor in world.get_actors() if _is_dreyevr_ego_actor(actor)],
        key=_dreyevr_ego_sort_key,
    )


def _select_scenario_ego(world, actor_id=None):
    if actor_id is not None:
        actor = world.get_actor(actor_id)
        if actor is None or not actor.type_id.startswith(("vehicle.", "harplab.dreyevr_vehicle")):
            raise ValueError("--ego-actor-id must identify an existing vehicle: {0}".format(actor_id))
        return actor, 0
    actor = wait_for_dreyevr_ego(world)
    if actor is None:
        return None, 0
    return _dedupe_dreyevr_egos(world, keep_actor=actor)


def _dedupe_dreyevr_egos(world, keep_actor=None):
    candidates = _dreyevr_ego_candidates(world)
    if not candidates:
        return None, 0
    # Keep the oldest DReyeVR pawn. In this build the real cockpit/view is tied
    # to the auto-spawned hero, while manually recovered harplab actors are just
    # duplicate vehicle actors.
    keep = candidates[0]
    destroyed = 0
    for actor in candidates:
        if actor.id == keep.id:
            continue
        try:
            if hasattr(actor, "set_autopilot"):
                actor.set_autopilot(False)
            actor.set_simulate_physics(False)
            _zero_actor_motion(actor)
            actor.destroy()
            destroyed += 1
        except Exception:
            pass
    if destroyed:
        try:
            settings = world.get_settings()
            if settings.synchronous_mode:
                world.tick()
            else:
                world.wait_for_tick()
        except Exception:
            pass
    return keep, destroyed


def _tick_world_once(world):
    try:
        settings = world.get_settings()
        if settings.synchronous_mode:
            world.tick()
        else:
            world.wait_for_tick()
        return True
    except Exception:
        return False


def _dreyevr_velocity_responsive(world, actor, test_speed_mps=2.0, ticks=4):
    if actor is None or not actor.type_id.startswith("harplab.dreyevr_vehicle"):
        return True
    try:
        actor.set_simulate_physics(True)
    except Exception:
        pass
    try:
        start = actor.get_transform().location
        xf = actor.get_transform()
        forward = xf.rotation.get_forward_vector()
        velocity = carla.Vector3D(
            forward.x * float(test_speed_mps),
            forward.y * float(test_speed_mps),
            forward.z * float(test_speed_mps),
        )
        for _ in range(max(1, int(ticks))):
            actor.set_target_velocity(velocity)
            _tick_world_once(world)
        speed = _actor_speed_mps(actor)
        end = actor.get_transform().location
        moved = math.sqrt(
            (float(end.x) - float(start.x)) ** 2
            + (float(end.y) - float(start.y)) ** 2
            + (float(end.z) - float(start.z)) ** 2
        )
        actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        return speed > 0.10 or moved > 0.03
    except Exception:
        return False


def _respawn_dreyevr_ego(world, stale_actor):
    if stale_actor is None:
        return None
    try:
        xf = stale_actor.get_transform()
        type_id = stale_actor.type_id
    except Exception:
        return None
    try:
        for actor in list(world.get_actors()):
            if actor.type_id.startswith("harplab.dreyevr_sensor"):
                try:
                    actor.destroy()
                except Exception:
                    pass
        stale_actor.destroy()
        _tick_world_once(world)
    except Exception:
        pass
    try:
        bp = world.get_blueprint_library().find(type_id)
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", "hero")
        fresh = world.try_spawn_actor(bp, xf)
        if fresh is None:
            lifted = carla.Transform(
                carla.Location(xf.location.x, xf.location.y, xf.location.z + 0.20),
                xf.rotation,
            )
            fresh = world.try_spawn_actor(bp, lifted)
        if fresh is not None:
            try:
                fresh.set_simulate_physics(True)
                fresh.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
                fresh.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
            except Exception:
                pass
            _tick_world_once(world)
        return fresh
    except Exception:
        return None


def _ensure_dreyevr_ego_responsive(world, actor):
    if actor is None or not actor.type_id.startswith("harplab.dreyevr_vehicle"):
        return actor
    if _dreyevr_velocity_responsive(world, actor):
        return actor
    print(
        "WARNING: DReyeVR ego actor {0} is stale after physics freeze; respawning cockpit ego.".format(
            actor.id
        )
    )
    fresh = _respawn_dreyevr_ego(world, actor)
    if fresh is None:
        print("WARNING: DReyeVR ego respawn failed; continuing with stale actor.")
        return actor
    if _dreyevr_velocity_responsive(world, fresh):
        print("DReyeVR ego respawned: old actor {0} -> new actor {1}.".format(actor.id, fresh.id))
    else:
        print("WARNING: respawned DReyeVR ego actor {0} still did not respond to velocity probe.".format(fresh.id))
    return fresh


def find_dreyevr_ego(world):
    """Return the auto-spawned DReyeVR Tesla pawn, or None."""
    candidates = _dreyevr_ego_candidates(world)
    return candidates[0] if candidates else None


def wait_for_dreyevr_ego(world, attempts=5):
    """DReyeVR actor queries can be empty right after a synchronous pause."""
    for attempt in range(max(1, int(attempts))):
        ego = find_dreyevr_ego(world)
        if ego is not None:
            return ego
        try:
            world.tick()
        except Exception:
            time.sleep(0.2)
        else:
            time.sleep(0.05)
    return find_dreyevr_ego(world)


def _apply_dualhighway_clear_weather(world):
    weather = carla.WeatherParameters.ClearNoon
    weather.cloudiness = 0.0
    weather.precipitation = 0.0
    weather.precipitation_deposits = 0.0
    weather.wind_intensity = 0.0
    weather.sun_azimuth_angle = 35.0
    weather.sun_altitude_angle = 65.0
    weather.fog_density = 0.0
    weather.fog_distance = 100000.0
    weather.fog_falloff = 0.0
    weather.wetness = 0.0
    for name, value in (
        ("scattering_intensity", 0.0),
        ("mie_scattering_scale", 0.0),
        ("rayleigh_scattering_scale", 0.033),
        ("dust_storm", 0.0),
    ):
        if hasattr(weather, name):
            setattr(weather, name, value)
    world.set_weather(weather)
    print("Weather preset applied: DualHighwayClearNoon sun_altitude=65 cloud=0 fog=0 wet=0")


def apply_weather_preset(world, preset_name):
    preset_name = str(preset_name or "Default")
    map_name = world.get_map().name
    if map_name.endswith("DualHighway_Opt") and preset_name in ("Default", "ClearNoon"):
        _apply_dualhighway_clear_weather(world)
        return
    if map_name.endswith(("Town04", "Town04_Opt")) and preset_name == "ClearNoon":
        preset_name = "CloudyNoon"
        print("Weather preset adjusted for Town04 brightness: ClearNoon -> CloudyNoon")
    if preset_name == "Default":
        print("Weather preset: Default (leaving current CARLA weather unchanged)")
        return
    preset = getattr(carla.WeatherParameters, preset_name, None)
    if preset is None:
        print("WARNING: unknown weather preset '{0}'; leaving weather unchanged.".format(preset_name))
        return
    world.set_weather(preset)
    print("Weather preset applied: " + preset_name)


def find_3_lane_stretch_near(world, anchor_location, search_radius_m=400.0, step_m=10.0):
    """Search for a waypoint that is in the MIDDLE of a 3-lane stretch, near anchor.

    Returns (middle_waypoint, distance_from_anchor) or (None, None).
    """
    cmap = world.get_map()
    start_wp = cmap.get_waypoint(
        anchor_location, project_to_road=True, lane_type=carla.LaneType.Driving
    )
    if start_wp is None:
        return None, None

    # Try the start waypoint first
    if _is_3_lane_middle(start_wp):
        return start_wp, 0.0

    # Walk forward and backward in step_m increments
    forward = [start_wp]
    backward = [start_wp]
    distance_traveled = 0.0
    while distance_traveled < search_radius_m:
        # Forward
        if forward:
            nxts = forward[-1].next(step_m)
            forward = nxts[:1]
            if forward and _is_3_lane_middle(forward[0]):
                return forward[0], distance_traveled + step_m
        # Backward
        if backward:
            prevs = backward[-1].previous(step_m)
            backward = prevs[:1]
            if backward and _is_3_lane_middle(backward[0]):
                return backward[0], -(distance_traveled + step_m)
        # Try also Left/Right neighbors of forward[0] if exists
        for cand in list(forward) + list(backward):
            if cand is None:
                continue
            # Maybe cand has left/right we can step onto
            for neighbor in (cand.get_left_lane(), cand.get_right_lane()):
                if neighbor and _is_3_lane_middle(neighbor):
                    return neighbor, distance_traveled
        distance_traveled += step_m
    return None, None


def _is_3_lane_middle(wp):
    """True iff wp has a Driving left neighbor AND a Driving right neighbor."""
    if wp is None:
        return False
    left = wp.get_left_lane()
    right = wp.get_right_lane()
    if not (left and right):
        return False
    if left.lane_type != carla.LaneType.Driving or right.lane_type != carla.LaneType.Driving:
        return False
    # Must be in the same road direction
    same_direction = (left.lane_id > 0) == (wp.lane_id > 0) and (right.lane_id > 0) == (wp.lane_id > 0)
    if not same_direction:
        return False
    return True


def _same_direction_driving_lane(wp, ref_wp):
    if wp is None or ref_wp is None:
        return False
    if wp.lane_type != carla.LaneType.Driving:
        return False
    return (wp.lane_id > 0) == (ref_wp.lane_id > 0)


def _lane_group_left_to_right(wp):
    """Return same-direction driving lanes around wp, ordered left to right."""
    lefts = []
    cur = wp.get_left_lane()
    while _same_direction_driving_lane(cur, wp):
        lefts.append(cur)
        cur = cur.get_left_lane()

    rights = []
    cur = wp.get_right_lane()
    while _same_direction_driving_lane(cur, wp):
        rights.append(cur)
        cur = cur.get_right_lane()

    return list(reversed(lefts)) + [wp] + rights


def _lane_width(wp):
    try:
        return float(wp.lane_width)
    except Exception:
        return 0.0


def _lane_desc(wp):
    if wp is None:
        return "(none)"
    return "lane_id={0} width={1:.2f}m".format(wp.lane_id, _lane_width(wp))


def _format_lane_group(lane_group):
    return ", ".join(_lane_desc(wp) for wp in lane_group)


def select_scenario_main_lanes(lane_group):
    """Select the three usable highway lanes for scenario Left/Middle/Right.

    The raw CARLA group may contain exit lanes or an emergency/shoulder lane.
    Scenario prompts should not be allowed to spawn into those lanes, so this
    returns a contiguous 3-lane subset that best represents the highway mainline.
    """
    if lane_group is None or len(lane_group) < 3:
        raise RuntimeError("Need at least three same-direction lanes")

    widths = [_lane_width(wp) for wp in lane_group]
    positive_widths = [w for w in widths if w > 0.0]
    if positive_widths:
        sorted_widths = sorted(positive_widths)
        median_width = sorted_widths[len(sorted_widths) // 2]
    else:
        median_width = 3.5

    # Emergency shoulders are often narrower than real highway lanes. Keep the
    # threshold conservative so normal CARLA lanes are not accidentally removed.
    min_main_width = max(2.70, median_width * 0.78)
    candidate_indexes = [
        idx for idx, width in enumerate(widths)
        if width <= 0.0 or width >= min_main_width
    ]

    contiguous_triples = []
    for start in range(0, len(lane_group) - 2):
        triple = [start, start + 1, start + 2]
        if all(idx in candidate_indexes for idx in triple):
            contiguous_triples.append(triple)

    if not contiguous_triples:
        # Fallback for maps with incomplete lane_width metadata: preserve the
        # Town04 highway convention that scenario lanes are the leftmost three
        # usable mainline lanes, with right-side exit/shoulder lanes ignored.
        if len(lane_group) >= 4 and widths[0] > 0.0 and widths[0] < min_main_width:
            chosen = [1, 2, 3]
        else:
            chosen = [0, 1, 2]
    else:
        # Keep the established Town04 behavior: Left/Middle/Right are the
        # leftmost usable three mainline lanes. Extra right-side lanes are
        # treated as exit/emergency candidates. If the far-left lane is too
        # narrow, the first valid triple naturally starts at lane 1.
        if len(lane_group) == 4 and len(contiguous_triples) > 1:
            left_narrow = widths[0] > 0.0 and widths[0] < min_main_width
            if left_narrow:
                chosen = [1, 2, 3]
            else:
                chosen = [0, 1, 2]
        else:
            chosen = contiguous_triples[0]

    selected = [lane_group[idx] for idx in chosen]
    ignored = [
        (idx, wp) for idx, wp in enumerate(lane_group)
        if idx not in chosen
    ]
    return selected, ignored, min_main_width, chosen


def find_mainroad_lane_group_near(world, anchor_location, min_lanes=4,
                                  search_radius_m=400.0, step_m=10.0):
    """Find a same-direction lane group near anchor.

    The selected scenario lane vocabulary uses three highway through lanes.
    Extra outer lanes can be exit/off-ramp or emergency/shoulder lanes and are
    not exposed as Left/Middle/Right.
    """
    cmap = world.get_map()
    start_wp = cmap.get_waypoint(
        anchor_location, project_to_road=True, lane_type=carla.LaneType.Driving
    )
    if start_wp is None:
        return None, None

    best_group = _lane_group_left_to_right(start_wp)
    best_dist = 0.0
    if len(best_group) >= min_lanes:
        return best_group, best_dist

    forward = [start_wp]
    backward = [start_wp]
    distance_traveled = 0.0
    while distance_traveled < search_radius_m:
        if forward:
            nxts = forward[-1].next(step_m)
            forward = nxts[:1]
            if forward:
                group = _lane_group_left_to_right(forward[0])
                if len(group) > len(best_group):
                    best_group = group
                    best_dist = distance_traveled + step_m
                if len(group) >= min_lanes:
                    return group, distance_traveled + step_m
        if backward:
            prevs = backward[-1].previous(step_m)
            backward = prevs[:1]
            if backward:
                group = _lane_group_left_to_right(backward[0])
                if len(group) > len(best_group):
                    best_group = group
                    best_dist = -(distance_traveled + step_m)
                if len(group) >= min_lanes:
                    return group, -(distance_traveled + step_m)
        distance_traveled += step_m

    if len(best_group) >= 3:
        return best_group, best_dist
    return None, None


def _map_allows_large_elevation(world):
    try:
        name = str(world.get_map().name)
    except Exception:
        return False
    return any(token in name for token in ("Town11", "Town12", "Town13", "Town15", "TownBig"))


def _location_height_is_reasonable(world, z):
    if _map_allows_large_elevation(world):
        return -20.0 <= z <= 300.0
    return -5.0 <= z <= 50.0


def find_mainroad_lane_group_from_spawns(world, min_lanes=4):
    """Fallback when the DReyeVR ego is already off-road or underground."""
    cmap = world.get_map()
    for idx, spawn in enumerate(cmap.get_spawn_points()):
        wp = cmap.get_waypoint(
            spawn.location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
        if wp is None:
            continue
        group = _lane_group_left_to_right(wp)
        if group and not _location_height_is_reasonable(world, group[0].transform.location.z):
            continue
        if len(group) >= min_lanes:
            return group, idx
    for idx, spawn in enumerate(cmap.get_spawn_points()):
        wp = cmap.get_waypoint(
            spawn.location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
        if wp is None:
            continue
        group = _lane_group_left_to_right(wp)
        if group and not _location_height_is_reasonable(world, group[0].transform.location.z):
            continue
        if len(group) >= 3:
            return group, idx
    return None, None


def find_mainroad_lane_group_from_xodr(world, spawn_xodr, min_lanes=4):
    if not isinstance(spawn_xodr, dict):
        return None, None
    try:
        road_id = int(spawn_xodr["road_id"])
        lane_id = int(spawn_xodr["lane_id"])
        s = float(spawn_xodr["s"])
    except Exception:
        print("WARNING: invalid spawn_xodr in scenario JSON; falling back to fixed anchor.")
        return None, None

    try:
        waypoint = world.get_map().get_waypoint_xodr(road_id, lane_id, s)
    except Exception as exc:
        print("WARNING: could not resolve spawn_xodr road={0} lane={1} s={2}: {3}".format(
            road_id, lane_id, s, exc))
        return None, None

    if waypoint is None:
        print("WARNING: spawn_xodr road={0} lane={1} s={2} did not resolve to a waypoint.".format(
            road_id, lane_id, s))
        return None, None

    group = _lane_group_left_to_right(waypoint)
    if len(group) >= min_lanes:
        return group, 0.0

    return find_mainroad_lane_group_near(
        world,
        waypoint.transform.location,
        min_lanes=min_lanes,
        search_radius_m=400.0,
        step_m=10.0,
    )


def build_left3_highway_lane_paths(
    lane_group,
    distance_m=500.0,
    step_m=1.0,
    backward_distance_m=0.0,
):
    """Build Left/Middle/Right from the selected three through lanes.

    lane_group must be ordered left-to-right. Extra outer lanes are intentionally
    ignored so emergency/shoulder/exit lanes are not possible scenario lanes.
    """
    if lane_group is None or len(lane_group) < 3:
        raise RuntimeError("Need at least three same-direction lanes")
    selected, ignored, min_main_width, selected_indexes = select_scenario_main_lanes(lane_group)
    left_wp, middle_wp, right_wp = selected
    print("  raw same-direction lane group: " + _format_lane_group(lane_group))
    print("  scenario lanes selected:")
    print("    Left   " + _lane_desc(left_wp))
    print("    Middle " + _lane_desc(middle_wp))
    print("    Right  " + _lane_desc(right_wp))
    if ignored:
        ignored_text = ", ".join(
            "{0} ({1})".format(_lane_desc(wp), "outer/emergency/exit candidate")
            for _, wp in ignored
        )
        print("  ignored non-scenario lane(s): " + ignored_text)
        print("  lane width threshold for shoulder filtering: {0:.2f}m".format(
            min_main_width))
    lane_paths = build_lane_paths_from_waypoints(left_wp, middle_wp, right_wp,
                                                 distance_m=distance_m,
                                                 step_m=step_m,
                                                 backward_distance_m=backward_distance_m)
    right_outer_candidates = [
        (idx, wp) for idx, wp in ignored
        if idx > max(selected_indexes)
    ]
    if right_outer_candidates:
        outer_idx, outer_wp = right_outer_candidates[-1]
        lane_paths[LANE_OUTER_RIGHT] = build_lane_path_from_waypoint(
            LANE_OUTER_RIGHT,
            outer_wp,
            distance_m=distance_m,
            step_m=step_m,
            backward_distance_m=backward_distance_m,
        )
        print("  Pull-over outer-right lane: left-to-right #{0} {1}".format(
            outer_idx + 1,
            _lane_desc(outer_wp),
        ))
    else:
        print("  Pull-over outer-right lane unavailable; fallback will use scenario Right lane.")
    queen_lane_index = 3
    if len(lane_group) > queen_lane_index and queen_lane_index not in selected_indexes:
        queen_wp = lane_group[queen_lane_index]
        lane_paths[LANE_QUEEN_FOURTH] = build_lane_path_from_waypoint(
            LANE_QUEEN_FOURTH,
            queen_wp,
            distance_m=distance_m,
            step_m=step_m,
            backward_distance_m=backward_distance_m,
        )
        print("  Queen close-background lane: left-to-right #4 " + _lane_desc(queen_wp))
    elif len(lane_group) > queen_lane_index:
        print("  Queen close-background lane disabled: left-to-right #4 is an active scenario lane.")
    return lane_paths


def build_lane_path_from_waypoint(name, waypoint, distance_m=500.0, step_m=1.0, backward_distance_m=0.0):
    back_xfs = []
    cur = waypoint
    distance = 0.0
    while distance < backward_distance_m and cur:
        prevs = cur.previous(step_m)
        if not prevs:
            break
        cur = prevs[0]
        back_xfs.append(cur.transform)
        distance += step_m

    transforms = list(reversed(back_xfs))
    origin_s = len(transforms) * step_m
    cur = waypoint
    distance = 0.0
    while distance < distance_m and cur:
        transforms.append(cur.transform)
        nexts = cur.next(step_m)
        if not nexts:
            break
        cur = nexts[0]
        distance += step_m
    if not transforms:
        raise RuntimeError("Could not build lane samples for " + str(name))
    return LanePath(name, transforms, step_m, origin_s=origin_s)


def build_lane_paths_from_waypoints(left_wp, middle_wp, right_wp,
                                    distance_m=500.0, step_m=1.0,
                                    backward_distance_m=0.0):
    left_back = []
    middle_back = []
    right_back = []
    cur_left = left_wp
    cur_middle = middle_wp
    cur_right = right_wp
    distance = 0.0
    while distance < backward_distance_m and cur_left and cur_middle and cur_right:
        prev_left = cur_left.previous(step_m)
        prev_middle = cur_middle.previous(step_m)
        prev_right = cur_right.previous(step_m)
        if not (prev_left and prev_middle and prev_right):
            break
        cur_left = prev_left[0]
        cur_middle = prev_middle[0]
        cur_right = prev_right[0]
        left_back.append(cur_left.transform)
        middle_back.append(cur_middle.transform)
        right_back.append(cur_right.transform)
        distance += step_m

    left_xfs = list(reversed(left_back))
    middle_xfs = list(reversed(middle_back))
    right_xfs = list(reversed(right_back))
    origin_s = len(middle_xfs) * step_m
    cur_left = left_wp
    cur_middle = middle_wp
    cur_right = right_wp
    distance = 0.0
    while distance < distance_m and cur_left and cur_middle and cur_right:
        left_xfs.append(cur_left.transform)
        middle_xfs.append(cur_middle.transform)
        right_xfs.append(cur_right.transform)
        next_left = cur_left.next(step_m)
        next_middle = cur_middle.next(step_m)
        next_right = cur_right.next(step_m)
        if not (next_left and next_middle and next_right):
            break
        cur_left = next_left[0]
        cur_middle = next_middle[0]
        cur_right = next_right[0]
        distance += step_m
    if not middle_xfs:
        raise RuntimeError("Could not build lane samples")
    return {
        LANE_LEFT: LanePath(LANE_LEFT, left_xfs, step_m, origin_s=origin_s),
        LANE_MIDDLE: LanePath(LANE_MIDDLE, middle_xfs, step_m, origin_s=origin_s),
        LANE_RIGHT: LanePath(LANE_RIGHT, right_xfs, step_m, origin_s=origin_s),
    }


def build_lane_paths(middle_wp, distance_m=500.0, step_m=1.0):
    middle_xfs = []
    left_xfs = []
    right_xfs = []
    cur = middle_wp
    distance = 0.0
    while distance < distance_m:
        middle_xfs.append(cur.transform)
        l = cur.get_left_lane()
        r = cur.get_right_lane()
        left_xfs.append(l.transform if l else cur.transform)
        right_xfs.append(r.transform if r else cur.transform)
        nxts = cur.next(step_m)
        if not nxts:
            break
        cur = nxts[0]
        distance += step_m
    if not middle_xfs:
        raise RuntimeError("Could not build lane samples")
    return {
        LANE_LEFT: LanePath(LANE_LEFT, left_xfs, step_m),
        LANE_MIDDLE: LanePath(LANE_MIDDLE, middle_xfs, step_m),
        LANE_RIGHT: LanePath(LANE_RIGHT, right_xfs, step_m),
    }


# ============================================================================
# Trajectory interpolation (mirror of native runner)
# ============================================================================

def sample_gap(t, initial_gap_m, gap_points):
    if not gap_points:
        return initial_gap_m
    pts = sorted(gap_points, key=lambda p: p["time_s"])
    if t <= pts[0]["time_s"]:
        if pts[0]["time_s"] <= 0:
            return pts[0]["gap_m"]
        frac = t / pts[0]["time_s"]
        return initial_gap_m + (pts[0]["gap_m"] - initial_gap_m) * frac
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a["time_s"] <= t <= b["time_s"]:
            span = b["time_s"] - a["time_s"]
            if span <= 0:
                return b["gap_m"]
            frac = (t - a["time_s"]) / span
            return a["gap_m"] + (b["gap_m"] - a["gap_m"]) * frac
    return pts[-1]["gap_m"]


def _sign_of(value, eps=1e-6):
    try:
        value = float(value)
    except Exception:
        return 0
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _time_gap_point_signs(time_gap_points):
    signs = []
    for point in sorted(time_gap_points or [], key=lambda p: p["time_s"]):
        signs.append((float(point.get("time_s", 0.0) or 0.0),
                      _sign_of(point.get("time_gap_s", 0.0))))
    return signs


def _time_gap_relation_hint(plan, t, sampled_time_gap_s):
    sign = _sign_of(sampled_time_gap_s)
    if sign:
        return sign

    previous_sign = 0
    next_sign = 0
    for point_t, point_sign in _time_gap_point_signs(plan.cfg.get("time_gap_points", [])):
        if point_t <= t:
            if point_sign:
                previous_sign = point_sign
        elif not next_sign and point_sign:
            next_sign = point_sign
            break
    if previous_sign:
        return previous_sign

    sign = _sign_of(getattr(plan, "initial_bumper_gap_m", None))
    if sign:
        return sign
    if next_sign:
        return next_sign
    sign = _sign_of(getattr(plan, "initial_s", 0.0))
    return sign or 1


def _initial_time_gap_relation_hint(cfg, initial_gap_m):
    sign = _sign_of(initial_gap_m)
    if sign:
        return sign
    for _point_t, point_sign in _time_gap_point_signs(cfg.get("time_gap_points", [])):
        if point_sign:
            return point_sign
    return 1


def _bumper_gap_to_center_delta_m(bumper_gap_m, bumper_clearance_m, relation_hint):
    bumper_gap_m = float(bumper_gap_m or 0.0)
    bumper_clearance_m = max(0.0, float(bumper_clearance_m or 0.0))
    relation = _sign_of(bumper_gap_m) or _sign_of(relation_hint) or 1
    return bumper_gap_m + relation * bumper_clearance_m


def _time_gap_to_center_delta_m(time_gap_s, reference_speed_mps, bumper_clearance_m, relation_hint):
    bumper_gap_m = max(0.001, float(reference_speed_mps or 0.0)) * float(time_gap_s or 0.0)
    return _bumper_gap_to_center_delta_m(bumper_gap_m, bumper_clearance_m, relation_hint)


def _cfg_initial_bumper_gap_m(cfg, reference_speed_mps=None):
    if cfg is None:
        return 0.0
    time_gap_s = cfg.get("initial_time_gap_s")
    if time_gap_s not in (None, ""):
        return float(time_gap_s or 0.0) * max(0.001, float(reference_speed_mps or 0.0))
    return float(cfg.get("initial_gap_m", 0.0) or 0.0)


def _time_gap_initial_bumper_gap_m(plan):
    value = getattr(plan, "initial_bumper_gap_m", None)
    if value is not None:
        return value
    return float(plan.initial_s or 0.0)


def sample_time_gap_s(
    t,
    initial_gap_m,
    time_gap_points,
    reference_speed_mps,
    initial_bumper_gap_m=None,
):
    reference_speed_mps = max(0.001, float(reference_speed_mps or 0.0))
    if initial_bumper_gap_m is None:
        initial_bumper_gap_m = initial_gap_m
    initial_time_gap_s = float(initial_bumper_gap_m or 0.0) / reference_speed_mps
    if not time_gap_points:
        return initial_time_gap_s
    pts = sorted(time_gap_points, key=lambda p: p["time_s"])
    if t <= pts[0]["time_s"]:
        if pts[0]["time_s"] <= 0:
            return float(pts[0]["time_gap_s"])
        frac = t / pts[0]["time_s"]
        return initial_time_gap_s + (float(pts[0]["time_gap_s"]) - initial_time_gap_s) * frac
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a["time_s"] <= t <= b["time_s"]:
            span = b["time_s"] - a["time_s"]
            if span <= 0:
                return float(b["time_gap_s"])
            frac = (t - a["time_s"]) / span
            return float(a["time_gap_s"]) + (
                float(b["time_gap_s"]) - float(a["time_gap_s"])
            ) * frac
    return float(pts[-1]["time_gap_s"])


def sample_time_gap_rate_s_per_s(
    t,
    initial_gap_m,
    time_gap_points,
    reference_speed_mps,
    initial_bumper_gap_m=None,
):
    reference_speed_mps = max(0.001, float(reference_speed_mps or 0.0))
    if initial_bumper_gap_m is None:
        initial_bumper_gap_m = initial_gap_m
    initial_time_gap_s = float(initial_bumper_gap_m or 0.0) / reference_speed_mps
    if not time_gap_points:
        return 0.0
    pts = sorted(time_gap_points, key=lambda p: p["time_s"])
    if t <= pts[0]["time_s"]:
        first_t = float(pts[0]["time_s"])
        if first_t <= 0.0:
            if len(pts) < 2:
                return 0.0
            first_gap_s = float(pts[0]["time_gap_s"])
            second_t = float(pts[1]["time_s"])
            second_gap_s = float(pts[1]["time_gap_s"])
            span = second_t - first_t
            return 0.0 if span <= 0.0 else (second_gap_s - first_gap_s) / span
        first_gap_s = float(pts[0]["time_gap_s"])
        return (first_gap_s - initial_time_gap_s) / first_t
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        a_t = float(a["time_s"])
        b_t = float(b["time_s"])
        if a_t <= t <= b_t:
            span = b_t - a_t
            if span <= 0.0:
                return 0.0
            return (
                float(b["time_gap_s"])
                - float(a["time_gap_s"])
            ) / span
    return 0.0


def sample_time_gap_follow_speed_mps(
    t,
    initial_gap_m,
    time_gap_points,
    reference_speed_mps,
    ego_current_speed_mps,
    initial_bumper_gap_m=None,
):
    rate = sample_time_gap_rate_s_per_s(
        t,
        initial_gap_m,
        time_gap_points,
        reference_speed_mps,
        initial_bumper_gap_m=initial_bumper_gap_m,
    )
    return max(0.0, float(ego_current_speed_mps or 0.0) + max(0.001, float(reference_speed_mps or 0.0)) * rate)


def _speed_point_value_mps(point, fallback_mps):
    try:
        return max(0.0, float(point.get("speed_kmh", 0.0) or 0.0)) / 3.6
    except Exception:
        return max(0.0, float(fallback_mps or 0.0))


def sample_speed_mps(t, initial_speed_mps, speed_points):
    if not speed_points:
        return max(0.0, float(initial_speed_mps or 0.0))
    pts = sorted(speed_points, key=lambda p: p["time_s"])
    base = max(0.0, float(initial_speed_mps or 0.0))
    if t <= float(pts[0]["time_s"]):
        first_t = float(pts[0]["time_s"])
        first_v = _speed_point_value_mps(pts[0], base)
        if first_t <= 0.0:
            return first_v
        frac = max(0.0, min(1.0, t / first_t))
        return base + (first_v - base) * frac
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        a_t = float(a["time_s"])
        b_t = float(b["time_s"])
        if a_t <= t <= b_t:
            span = b_t - a_t
            b_v = _speed_point_value_mps(b, base)
            if span <= 0:
                return b_v
            a_v = _speed_point_value_mps(a, base)
            frac = (t - a_t) / span
            return a_v + (b_v - a_v) * frac
    return _speed_point_value_mps(pts[-1], base)


def integrate_speed_m(t, initial_speed_mps, speed_points):
    if t <= 0.0:
        return 0.0
    if not speed_points:
        return max(0.0, float(initial_speed_mps or 0.0)) * t

    pts = sorted(speed_points, key=lambda p: p["time_s"])
    base = max(0.0, float(initial_speed_mps or 0.0))
    anchors = [(0.0, base)]
    for point in pts:
        point_t = max(0.0, float(point.get("time_s", 0.0) or 0.0))
        point_v = _speed_point_value_mps(point, base)
        if point_t <= 0.0:
            anchors[0] = (0.0, point_v)
            continue
        anchors.append((point_t, point_v))
    anchors.sort(key=lambda item: item[0])

    distance = 0.0
    prev_t, prev_v = anchors[0]
    if t <= prev_t:
        return prev_v * t
    for cur_t, cur_v in anchors[1:]:
        if t <= cur_t:
            span = max(0.0, t - prev_t)
            frac = 0.0 if cur_t <= prev_t else span / (cur_t - prev_t)
            end_v = prev_v + (cur_v - prev_v) * frac
            return distance + 0.5 * (prev_v + end_v) * span
        span = cur_t - prev_t
        if span > 0.0:
            distance += 0.5 * (prev_v + cur_v) * span
        prev_t, prev_v = cur_t, cur_v
    return distance + prev_v * max(0.0, t - prev_t)


class LaneState(object):
    def __init__(self, src_lane, dst_lane, blend):
        self.src_lane = src_lane
        self.dst_lane = dst_lane
        self.blend = blend


def sample_lane(t, initial_lane, lane_change_points):
    if not lane_change_points:
        return LaneState(initial_lane, initial_lane, 1.0)
    pts = sorted(lane_change_points, key=lambda p: p["time_s"])
    current_lane = initial_lane
    for p in pts:
        t_start = p["time_s"]
        t_dur = max(0.001, p["duration_s"])
        t_end = t_start + t_dur
        target = p["target_lane"]
        if t < t_start:
            return LaneState(current_lane, current_lane, 1.0)
        if t_start <= t < t_end:
            blend = (t - t_start) / t_dur
            blend = blend * blend * (3.0 - 2.0 * blend)
            return LaneState(current_lane, target, max(0.0, min(1.0, blend)))
        current_lane = target
    return LaneState(current_lane, current_lane, 1.0)


# ============================================================================
# Vehicle helpers
# ============================================================================

ROLE_ORDER = ["ego", "vehicle_1", "vehicle_2", "vehicle_3", "vehicle_4", "vehicle_5", "vehicle_6"]
KINEMATIC_VEHICLE_Z_OFFSET = 0.02
SPAWN_CLEARANCE_Z_OFFSET = 0.50
PHYSICS_VEHICLE_T0_Z_OFFSET = 0.30
PHYSICS_VEHICLE_T0_SETTLE_TICKS = 3
DREYEVR_EGO_TELEPORT_Z_OFFSET = KINEMATIC_VEHICLE_Z_OFFSET
UNSAFE_VEHICLE_BLUEPRINT_FALLBACKS = {
    # Present in the local asset tree, but this DReyeVR build can crash UE4 with
    # Assertion failed: MeshObject in SkeletalMesh.cpp when it is spawned.
    "vehicle.carlamotors.carlacola": "vehicle.mercedes.sprinter",
}


def make_blueprint(world, role, blueprint_filter):
    bp_lib = world.get_blueprint_library()
    requested_filter = str(blueprint_filter or "")
    safe_filter = UNSAFE_VEHICLE_BLUEPRINT_FALLBACKS.get(requested_filter.lower(), requested_filter)
    if safe_filter != requested_filter:
        print(
            "WARNING: blueprint {} is disabled for stability; using {} for {}.".format(
                requested_filter, safe_filter, role
            )
        )
    blueprint_filter = safe_filter
    cands = bp_lib.filter(blueprint_filter)
    if not cands:
        cands = bp_lib.filter("vehicle.lincoln.mkz_2020")
    bp = cands[0]
    bp.set_attribute("role_name", role)
    return bp


def _transform_with_lift(xf, lift_z=KINEMATIC_VEHICLE_Z_OFFSET):
    return carla.Transform(
        carla.Location(xf.location.x, xf.location.y, xf.location.z + lift_z),
        xf.rotation,
    )


def _zero_actor_motion(actor):
    try:
        actor.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
    except Exception:
        pass


def _apply_transform(actor, xf, lift_z=KINEMATIC_VEHICLE_Z_OFFSET):
    lifted = carla.Transform(
        carla.Location(xf.location.x, xf.location.y, xf.location.z + lift_z),
        xf.rotation,
    )
    actor.set_transform(lifted)
    _zero_actor_motion(actor)


def _is_dynamic_support_vehicle_plan(plan):
    return (
        getattr(plan, "role", "") != "ego"
        and not bool(getattr(plan, "kinematic", False))
        and getattr(plan, "control_mode", "") in (
            "velocity",
            "traffic-manager",
            "velocity-script",
            "vehicle-control",
        )
    )


def _hold_vehicle_brake(actor):
    if not hasattr(actor, "apply_control"):
        return
    try:
        actor.apply_control(carla.VehicleControl(
            throttle=0.0,
            brake=1.0,
            hand_brake=False,
            manual_gear_shift=False,
        ))
    except Exception:
        pass


def _place_physics_support_at_transform(actor, xf, lift_z=PHYSICS_VEHICLE_T0_Z_OFFSET):
    try:
        if hasattr(actor, "set_autopilot"):
            actor.set_autopilot(False)
    except Exception:
        pass
    _zero_actor_motion(actor)
    try:
        actor.set_simulate_physics(False)
    except Exception:
        pass
    _apply_transform(actor, xf, lift_z=lift_z)
    try:
        actor.set_simulate_physics(True)
    except Exception:
        pass
    _zero_actor_motion(actor)
    _hold_vehicle_brake(actor)


# ============================================================================
# Obstacle drops
# ============================================================================

DEFAULT_DROP_OFFSET_M = 0.5
OBSTACLE_KIND_BLUEPRINTS = {
    "barrel": ["static.prop.barrel", "static.prop.*barrel*"],
    "debris": ["static.prop.barrel", "static.prop.*barrel*"],
    "cone": ["static.prop.trafficcone01", "static.prop.trafficcone02", "static.prop.*cone*"],
    "barrier": ["static.prop.streetbarrier", "static.prop.*barrier*"],
    "box": ["static.prop.box01", "static.prop.box02", "static.prop.*box*"],
    "disabled vehicle": ["vehicle.citroen.c3", "vehicle.*c3*"],
}
OBSTACLE_KIND_MESH_PATHS = {
    "barrel": "/Game/Carla/Static/Dynamic/Trash/SM_Barrel.SM_Barrel",
    "debris": "/Game/Carla/Static/Dynamic/Trash/SM_Barrel.SM_Barrel",
}
OBSTACLE_KIND_HALF_LENGTH_M = {
    "barrel": 0.35,
    "debris": 0.35,
    "cone": 0.25,
    "barrier": 1.10,
    "box": 0.50,
    "disabled vehicle": DEFAULT_VEHICLE_HALF_LENGTH_M,
}


def _prepare_obstacle_drop_events(obstacle_configs, total_time_s):
    events = []
    for role, cfg in sorted(dict(obstacle_configs or {}).items()):
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled", False):
            continue
        if cfg.get("spawn_mode", "fixed") != "drop_from_vehicle":
            continue
        start_time_s = max(0.0, float(cfg.get("start_time_s", 0.0) or 0.0))
        end_value = cfg.get("end_time_s", total_time_s)
        end_time_s = float(total_time_s if end_value is None else end_value)
        if end_time_s <= start_time_s:
            end_time_s = float(total_time_s)
        events.append({
            "role": role,
            "cfg": cfg,
            "start_time_s": start_time_s,
            "end_time_s": end_time_s,
            "state": "pending",
            "actor": None,
        })
    events.sort(key=lambda event: (event["start_time_s"], event["role"]))
    return events


def _update_obstacle_drop_events(events, plan_by_role, world, t, total_time_s, spawned_actors, marker_outlet):
    for event in events:
        state = event.get("state", "pending")
        if state == "pending":
            if t + 1e-6 < event["start_time_s"]:
                continue
            if event["start_time_s"] > total_time_s:
                event["state"] = "skipped"
                continue
            cfg = event["cfg"]
            source_role = str(cfg.get("drop_source_role") or "")
            source_plan = plan_by_role.get(source_role)
            if source_plan is None or source_plan.actor is None:
                print(
                    "[t={0:.2f}s] obstacle_drop skipped {1}: source {2} not found".format(
                        t,
                        event["role"],
                        source_role or "(missing)",
                    )
                )
                event["state"] = "skipped"
                continue
            actor = _spawn_dropped_obstacle(world, event["role"], cfg, source_plan.actor)
            if actor is None:
                print(
                    "[t={0:.2f}s] obstacle_drop failed {1} kind={2} source={3}".format(
                        t,
                        event["role"],
                        _obstacle_display_kind(cfg),
                        source_role,
                    )
                )
                event["state"] = "failed"
                continue
            event["actor"] = actor
            event["state"] = "active"
            if spawned_actors is not None:
                spawned_actors.append(actor)
            print(
                "[t={0:.2f}s] obstacle_drop spawned {1} kind={2} source={3} actor_id={4}".format(
                    t,
                    event["role"],
                    _obstacle_display_kind(cfg),
                    source_role,
                    actor.id,
                )
            )
            _push_marker(
                marker_outlet,
                "OBSTACLE_DROP_SPAWNED",
                role=event["role"],
                kind=_obstacle_display_kind(cfg),
                source=source_role,
                actor_id=actor.id,
                scenario_t=t,
            )
        elif state == "active":
            if event["end_time_s"] < total_time_s and t + 1e-6 >= event["end_time_s"]:
                _destroy_obstacle_drop_event(event, spawned_actors, marker_outlet, t)


def _cleanup_obstacle_drop_events(events, spawned_actors, marker_outlet, t):
    for event in events:
        if event.get("state") == "active" and event.get("actor") is not None:
            _destroy_obstacle_drop_event(event, spawned_actors, marker_outlet, t)


def _destroy_obstacle_drop_event(event, spawned_actors, marker_outlet, t):
    actor = event.get("actor")
    if actor is None:
        event["state"] = "ended"
        return
    actor_id = getattr(actor, "id", None)
    destroyed = False
    try:
        _zero_actor_motion(actor)
        _apply_safe_brake(actor)
        destroyed = actor.destroy() is not False
    except Exception:
        pass
    if spawned_actors is not None and destroyed:
        try:
            spawned_actors.remove(actor)
        except ValueError:
            pass
    event["actor"] = None
    event["state"] = "ended"
    _push_marker(
        marker_outlet,
        "OBSTACLE_DROP_DESTROYED",
        role=event.get("role"),
        actor_id=actor_id,
        scenario_t=t,
    )


def _spawn_dropped_obstacle(world, role, cfg, source_actor):
    bp = _make_obstacle_blueprint(world, role, cfg)
    if bp is None:
        return None
    xf = _drop_obstacle_transform(world, cfg, source_actor)
    actor = world.try_spawn_actor(bp, xf)
    if actor is None:
        actor = world.try_spawn_actor(
            bp,
            carla.Transform(
                carla.Location(xf.location.x, xf.location.y, xf.location.z + 0.35),
                xf.rotation,
            ),
        )
    if actor is None:
        return None
    try:
        actor.set_simulate_physics(False)
    except Exception:
        pass
    try:
        actor.set_transform(xf)
    except Exception:
        pass
    _zero_actor_motion(actor)
    _apply_safe_brake(actor)
    return actor


def _drop_obstacle_transform(world, cfg, source_actor):
    source_xf = source_actor.get_transform()
    yaw_rad = math.radians(source_xf.rotation.yaw)
    fwd_x = math.cos(yaw_rad)
    fwd_y = math.sin(yaw_rad)
    right_x = math.cos(yaw_rad + math.pi / 2.0)
    right_y = math.sin(yaw_rad + math.pi / 2.0)
    source_half = _actor_half_length_m(source_actor)
    obstacle_half = _obstacle_half_length_m(cfg)
    drop_offset = max(0.0, float(cfg.get("drop_offset_m", DEFAULT_DROP_OFFSET_M) or 0.0))
    lateral_offset = float(cfg.get("lateral_offset_m", 0.0) or 0.0)
    center_back_m = source_half + obstacle_half + drop_offset
    loc = carla.Location(
        source_xf.location.x - fwd_x * center_back_m + right_x * lateral_offset,
        source_xf.location.y - fwd_y * center_back_m + right_y * lateral_offset,
        source_xf.location.z + 0.08,
    )
    try:
        wp = world.get_map().get_waypoint(
            loc,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if wp is not None:
            loc.z = wp.transform.location.z + 0.08
    except Exception:
        pass
    return carla.Transform(
        loc,
        carla.Rotation(pitch=0.0, yaw=source_xf.rotation.yaw, roll=0.0),
    )


def _make_obstacle_blueprint(world, role, cfg):
    bp_lib = world.get_blueprint_library()
    kind_key = _obstacle_kind_key(cfg)
    candidates = []
    blueprint_id = str(cfg.get("blueprint_id") or "").strip()
    if blueprint_id:
        candidates.append(blueprint_id)
        if "*" not in blueprint_id and "." in blueprint_id:
            family = blueprint_id.rsplit(".", 1)[-1]
            if family:
                candidates.append("static.prop.*{0}*".format(family))
    candidates.extend(OBSTACLE_KIND_BLUEPRINTS.get(kind_key, OBSTACLE_KIND_BLUEPRINTS["barrel"]))
    for pattern in candidates:
        bp = _find_blueprint(bp_lib, pattern)
        if bp is not None:
            _set_blueprint_attribute(bp, "role_name", role)
            return bp
    mesh_bp = _find_blueprint(bp_lib, "static.prop.mesh")
    if mesh_bp is None:
        return None
    _set_blueprint_attribute(mesh_bp, "role_name", role)
    mesh_path = OBSTACLE_KIND_MESH_PATHS.get(kind_key, OBSTACLE_KIND_MESH_PATHS["barrel"])
    _set_blueprint_attribute(mesh_bp, "mesh_path", mesh_path)
    _set_blueprint_attribute(mesh_bp, "mass", "25.0")
    return mesh_bp


def _find_blueprint(bp_lib, pattern):
    pattern = str(pattern or "").strip()
    if not pattern:
        return None
    if "*" not in pattern:
        try:
            return bp_lib.find(pattern)
        except Exception:
            pass
    try:
        matches = bp_lib.filter(pattern)
        if matches:
            return matches[0]
    except Exception:
        pass
    return None


def _set_blueprint_attribute(bp, name, value):
    try:
        if hasattr(bp, "has_attribute") and not bp.has_attribute(name) and name not in ("mesh_path", "mass"):
            return
    except Exception:
        pass
    try:
        bp.set_attribute(name, str(value))
    except Exception:
        pass


def _obstacle_kind_key(cfg):
    text = " ".join([
        str(cfg.get("blueprint_id") or ""),
        str(cfg.get("kind") or ""),
        str(cfg.get("label") or ""),
    ]).lower()
    if "disabled" in text and "vehicle" in text:
        return "disabled vehicle"
    if "barrier" in text:
        return "barrier"
    if "cone" in text:
        return "cone"
    if "box" in text or "crate" in text:
        return "box"
    if "barrel" in text or "debris" in text:
        return "barrel"
    return "barrel"


def _obstacle_display_kind(cfg):
    key = _obstacle_kind_key(cfg)
    if key in ("barrel", "debris"):
        return "Barrel"
    if key == "disabled vehicle":
        return "Disabled Vehicle"
    return key.title()


def _obstacle_half_length_m(cfg):
    key = _obstacle_kind_key(cfg)
    return float(OBSTACLE_KIND_HALF_LENGTH_M.get(key, OBSTACLE_KIND_HALF_LENGTH_M["barrel"]))


# ============================================================================
# Pedestrian crossings
# ============================================================================

PEDESTRIAN_BLUEPRINT_FILTER = "walker.pedestrian.*"
PEDESTRIAN_SIDE_MARGIN_MIN_M = 1.8
PEDESTRIAN_SIDE_MARGIN_MAX_M = 4.0
PEDESTRIAN_Z_OFFSET_M = 0.08
PEDESTRIAN_SPAWN_Z_OFFSETS_M = (1.2, 1.8, 0.45, 0.0)


def _prepare_pedestrian_events(pedestrian_configs, total_time_s):
    events = []
    for role, cfg in sorted(dict(pedestrian_configs or {}).items()):
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled", False):
            continue
        start_time_s = max(0.0, float(cfg.get("start_time_s", 0.0) or 0.0))
        duration_s = max(0.001, float(cfg.get("duration_s", 4.0) or 4.0))
        if start_time_s > float(total_time_s):
            state = "skipped"
        else:
            state = "pending"
        events.append({
            "role": role,
            "cfg": cfg,
            "start_time_s": start_time_s,
            "duration_s": duration_s,
            "end_time_s": start_time_s + duration_s,
            "state": state,
            "actor": None,
            "started": False,
            "ended": False,
        })
    events.sort(key=lambda event: (event["start_time_s"], event["role"]))
    return events


def _update_pedestrian_events(events, world, lane_paths, t, spawned_actors, marker_outlet):
    for event in events:
        state = event.get("state", "pending")
        if state in ("skipped", "failed", "ended"):
            continue
        if state == "waiting" and t + 1e-6 < event["start_time_s"]:
            actor = event.get("actor")
            if actor is not None:
                _zero_actor_motion(actor)
            continue
        if t + 1e-6 < event["start_time_s"]:
            continue
        if state == "pending":
            if not _spawn_pedestrian_event(
                event,
                world,
                lane_paths,
                t,
                spawned_actors,
                marker_outlet,
                state_after_spawn="active",
            ):
                print(
                    "[t={0:.2f}s] pedestrian spawn failed {1}".format(
                        t,
                        event["role"],
                    )
                )
                event["state"] = "failed"
                continue
        elif state == "waiting":
            event["state"] = "active"
        _update_pedestrian_actor_transform(event, lane_paths, t, marker_outlet)


def _pre_spawn_pedestrian_events(events, world, lane_paths, spawned_actors, marker_outlet):
    for event in events:
        if event.get("state") != "pending":
            continue
        if not _spawn_pedestrian_event(
            event,
            world,
            lane_paths,
            0.0,
            spawned_actors,
            marker_outlet,
            state_after_spawn="waiting",
        ):
            print(
                "[t=0.00s] pedestrian pre-spawn failed {0}; will retry at crossing start".format(
                    event.get("role")
                )
            )


def _spawn_pedestrian_event(event, world, lane_paths, t, spawned_actors, marker_outlet, state_after_spawn):
    actor = _spawn_pedestrian_actor(world, lane_paths, event["role"], event["cfg"])
    if actor is None:
        return False
    event["actor"] = actor
    event["state"] = state_after_spawn
    if spawned_actors is not None:
        spawned_actors.append(actor)
    print(
        "[t={0:.2f}s] pedestrian spawned {1} actor_id={2} {3}->{4}".format(
            t,
            event["role"],
            getattr(actor, "id", "?"),
            event["cfg"].get("start_side", "Left"),
            event["cfg"].get("end_side", "Right"),
        )
    )
    _push_marker(
        marker_outlet,
        "PEDESTRIAN_SPAWNED",
        role=event["role"],
        actor_id=getattr(actor, "id", None),
        scenario_t=t,
    )
    return True


def _cleanup_pedestrian_events(events, spawned_actors, marker_outlet, t):
    for event in events:
        if event.get("actor") is not None:
            _destroy_pedestrian_event(event, spawned_actors, marker_outlet, t)


def _spawn_pedestrian_actor(world, lane_paths, role, cfg):
    bp = _make_pedestrian_blueprint(world, role, cfg)
    if bp is None:
        return None
    xf = _pedestrian_crossing_transform(cfg, lane_paths, 0.0)
    actor = None
    for z_offset in PEDESTRIAN_SPAWN_Z_OFFSETS_M:
        spawn_xf = carla.Transform(
            carla.Location(xf.location.x, xf.location.y, xf.location.z + float(z_offset)),
            xf.rotation,
        )
        actor = world.try_spawn_actor(bp, spawn_xf)
        if actor is not None:
            break
    if actor is None:
        return None
    try:
        actor.set_simulate_physics(False)
    except Exception:
        pass
    try:
        actor.set_transform(xf)
    except Exception:
        pass
    _zero_actor_motion(actor)
    return actor


def _make_pedestrian_blueprint(world, role, cfg):
    bp_lib = world.get_blueprint_library()
    candidates = []
    blueprint_id = str(cfg.get("blueprint_id") or cfg.get("blueprint_filter") or "").strip()
    if blueprint_id:
        candidates.append(blueprint_id)
    candidates.append(PEDESTRIAN_BLUEPRINT_FILTER)
    candidates.append("walker.pedestrian.0001")
    for pattern in candidates:
        bp = _find_blueprint(bp_lib, pattern)
        if bp is not None:
            _set_blueprint_attribute(bp, "role_name", role)
            _set_blueprint_attribute(bp, "is_invincible", "false")
            return bp
    return None


def _update_pedestrian_actor_transform(event, lane_paths, t, marker_outlet):
    actor = event.get("actor")
    if actor is None:
        return
    start_time_s = float(event.get("start_time_s", 0.0) or 0.0)
    duration_s = max(0.001, float(event.get("duration_s", 4.0) or 4.0))
    progress = max(0.0, min(1.0, (float(t) - start_time_s) / duration_s))
    if not event.get("started"):
        event["started"] = True
        _push_marker(
            marker_outlet,
            "PEDESTRIAN_CROSSING_START",
            role=event.get("role"),
            actor_id=getattr(actor, "id", None),
            scenario_t=t,
            duration_s=duration_s,
        )
    xf = _pedestrian_crossing_transform(event.get("cfg", {}), lane_paths, progress)
    try:
        actor.set_transform(xf)
    except Exception:
        pass
    _zero_actor_motion(actor)
    if progress >= 1.0 and not event.get("ended"):
        event["ended"] = True
        event["state"] = "ended"
        _push_marker(
            marker_outlet,
            "PEDESTRIAN_CROSSING_END",
            role=event.get("role"),
            actor_id=getattr(actor, "id", None),
            scenario_t=t,
        )


def _destroy_pedestrian_event(event, spawned_actors, marker_outlet, t):
    actor = event.get("actor")
    actor_id = getattr(actor, "id", None)
    destroyed = False
    if actor is not None:
        try:
            _zero_actor_motion(actor)
            destroyed = actor.destroy() is not False
        except Exception:
            pass
    if spawned_actors is not None and actor is not None and destroyed:
        try:
            spawned_actors.remove(actor)
        except ValueError:
            pass
    event["actor"] = None
    event["state"] = "ended"
    _push_marker(
        marker_outlet,
        "PEDESTRIAN_DESTROYED",
        role=event.get("role"),
        actor_id=actor_id,
        scenario_t=t,
    )


def _pedestrian_crossing_transform(cfg, lane_paths, progress):
    start_loc, end_loc = _pedestrian_crossing_endpoints(cfg, lane_paths)
    progress = max(0.0, min(1.0, float(progress or 0.0)))
    loc = carla.Location(
        start_loc.x + (end_loc.x - start_loc.x) * progress,
        start_loc.y + (end_loc.y - start_loc.y) * progress,
        start_loc.z + (end_loc.z - start_loc.z) * progress,
    )
    yaw = math.degrees(math.atan2(end_loc.y - start_loc.y, end_loc.x - start_loc.x))
    return carla.Transform(loc, carla.Rotation(pitch=0.0, yaw=yaw, roll=0.0))


def _pedestrian_crossing_endpoints(cfg, lane_paths):
    s = float(cfg.get("gap_m", 0.0) or 0.0)
    left_path = lane_paths.get(LANE_LEFT) or lane_paths.get(LANE_MIDDLE) or next(iter(lane_paths.values()))
    right_path = lane_paths.get(LANE_RIGHT) or lane_paths.get(LANE_MIDDLE) or left_path
    left_tf = left_path.sample(s)
    right_tf = right_path.sample(s)
    left_loc = left_tf.location
    right_loc = right_tf.location
    dx = right_loc.x - left_loc.x
    dy = right_loc.y - left_loc.y
    lateral_mag = math.sqrt(dx * dx + dy * dy)
    if lateral_mag < 0.001:
        yaw_rad = math.radians(left_tf.rotation.yaw)
        right_x = math.cos(yaw_rad + math.pi / 2.0)
        right_y = math.sin(yaw_rad + math.pi / 2.0)
        lateral_mag = 7.0
    else:
        right_x = dx / lateral_mag
        right_y = dy / lateral_mag
    lane_width_est = max(3.0, lateral_mag / 2.0)
    margin = max(PEDESTRIAN_SIDE_MARGIN_MIN_M, min(PEDESTRIAN_SIDE_MARGIN_MAX_M, lane_width_est * 0.65))
    base_z = max(float(left_loc.z), float(right_loc.z)) + PEDESTRIAN_Z_OFFSET_M
    left_edge = carla.Location(left_loc.x - right_x * margin, left_loc.y - right_y * margin, base_z)
    right_edge = carla.Location(right_loc.x + right_x * margin, right_loc.y + right_y * margin, base_z)
    start_side = str(cfg.get("start_side", "Left") or "Left").strip().lower()
    if start_side.startswith("r"):
        return right_edge, left_edge
    return left_edge, right_edge


# ============================================================================
# Plan / play
# ============================================================================

class VehiclePlan(object):
    def __init__(self, role, cfg, actor, kinematic=True, control_mode=None):
        self.role = role
        self.cfg = cfg
        self.actor = actor
        self.kinematic = kinematic
        self.control_mode = control_mode or ("kinematic" if kinematic else "traffic-manager")
        self.initial_s = float(cfg.get("initial_gap_m", 0.0) or 0.0)
        self.initial_bumper_gap_m = None
        self.time_gap_bumper_clearance_m = 0.0
        self.speed_mps = float(cfg.get("speed_kmh", 0.0)) / 3.6
        self.current_lane = cfg.get("lane", LANE_MIDDLE)
        self._lane_change_fired = set()
        self._last_control_error_m = 0.0
        self._last_control_desired_s = self.initial_s
        self._last_control_current_s = self.initial_s
        self.tor_detached = False


def _configure_time_gap_bumper_references(plans, ego_plan):
    ego_speed_mps = max(0.001, float(ego_plan.cfg.get("speed_kmh", 0.0) or 0.0) / 3.6)
    for plan in plans:
        plan.time_gap_bumper_clearance_m = 0.0
        plan.initial_bumper_gap_m = None
        if plan.role == "ego" or plan.actor is None:
            continue
        if plan.cfg.get("gap_points", []) or not plan.cfg.get("time_gap_points", []):
            continue
        clearance_m = _combined_bumper_clearance_m(ego_plan.actor, plan.actor)
        # In time-gap scripts, initial_gap_m is interpreted as signed
        # bumper-to-bumper distance; CARLA still needs center-to-center s.
        initial_bumper_gap_m = _cfg_initial_bumper_gap_m(plan.cfg, ego_speed_mps)
        relation = _initial_time_gap_relation_hint(plan.cfg, initial_bumper_gap_m)
        plan.time_gap_bumper_clearance_m = clearance_m
        plan.initial_bumper_gap_m = initial_bumper_gap_m
        plan.initial_s = _bumper_gap_to_center_delta_m(
            initial_bumper_gap_m,
            clearance_m,
            relation,
        )
        print(
            "  time-gap bumper reference {0}: clearance={1:.2f}m "
            "initial_bumper_gap={2:.2f}m initial_center_s={3:.2f}m".format(
                plan.role,
                clearance_m,
                initial_bumper_gap_m,
                plan.initial_s,
            )
        )


def _initial_spawn_s_from_cfg(cfg, reference_speed_mps=None):
    initial_gap_m = _cfg_initial_bumper_gap_m(cfg, reference_speed_mps)
    if cfg.get("time_gap_points", []) and not cfg.get("gap_points", []):
        relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
        return _bumper_gap_to_center_delta_m(
            initial_gap_m,
            DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
            relation,
        )
    if cfg.get("initial_time_gap_s") not in (None, ""):
        relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
        return _bumper_gap_to_center_delta_m(
            initial_gap_m,
            DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
            relation,
        )
    return initial_gap_m


def _reset_to_t0(plans, lane_paths, world, warmup_ticks=2):
    has_dynamic_support = any(_is_dynamic_support_vehicle_plan(plan) for plan in plans)
    for plan in plans:
        if plan.actor is None:
            continue
        if not plan.kinematic:
            try:
                plan.actor.set_autopilot(False)
                if plan.role == "ego":
                    plan.actor.set_simulate_physics(True)
                    _release_vehicle_brake(plan.actor)
                else:
                    plan.actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            except Exception:
                pass
        lp = lane_paths[plan.cfg["lane"]]
        init_s = float(getattr(plan, "initial_s", plan.cfg.get("initial_gap_m", 0.0)) or 0.0)
        xf = lp.sample(init_s)
        if _is_dynamic_support_vehicle_plan(plan):
            _place_physics_support_at_transform(plan.actor, xf)
        else:
            _apply_transform(plan.actor, xf)
        if plan.role == "ego" and not plan.kinematic:
            try:
                plan.actor.set_simulate_physics(True)
            except Exception:
                pass
            _release_vehicle_brake(plan.actor)
        plan.current_lane = plan.cfg["lane"]
        plan._lane_change_fired.clear()
    settle_ticks = max(1, int(warmup_ticks))
    if has_dynamic_support:
        settle_ticks = max(settle_ticks, PHYSICS_VEHICLE_T0_SETTLE_TICKS)
    for _ in range(settle_ticks):
        for plan in plans:
            if plan.actor is not None:
                _zero_actor_motion(plan.actor)
                if _is_dynamic_support_vehicle_plan(plan):
                    _hold_vehicle_brake(plan.actor)
                elif plan.role == "ego" and not plan.kinematic:
                    _release_vehicle_brake(plan.actor)
        world.tick()


def _move_spectator_into_tesla(world, ego_actor):
    """Place the CARLA spectator at ego's driver seat (third-person-ish chase)."""
    # For DReyeVR the user's view is the Tesla cockpit; CARLA spectator is a different camera.
    # We still update it so an external observer can follow ego too.
    xf = ego_actor.get_transform()
    follow = carla.Transform(
        carla.Location(xf.location.x, xf.location.y, xf.location.z + 8.0),
        carla.Rotation(pitch=-30.0, yaw=xf.rotation.yaw, roll=0.0),
    )
    world.get_spectator().set_transform(follow)


def _lane_change_direction(src_lane, dst_lane):
    order = {LANE_LEFT: 0, LANE_MIDDLE: 1, LANE_RIGHT: 2}
    return order.get(dst_lane, 1) > order.get(src_lane, 1)  # True = right in CARLA TM.


def _lane_index(lane_name):
    return {LANE_LEFT: 0, LANE_MIDDLE: 1, LANE_RIGHT: 2}.get(lane_name, 1)


def _nearest_lane_name(lane_paths, location, fallback_lane=LANE_MIDDLE, fallback_s=0.0, window_m=80.0):
    best_lane = fallback_lane
    best_s = fallback_s
    best_d2 = float("inf")
    for lane_name, lane_path in lane_paths.items():
        try:
            lane_s = lane_path.nearest_s(location, fallback_s=fallback_s, window_m=window_m)
            lane_loc = lane_path.sample(lane_s).location
            dx = lane_loc.x - location.x
            dy = lane_loc.y - location.y
            dz = lane_loc.z - location.z
            d2 = dx * dx + dy * dy + dz * dz
            if d2 < best_d2 or (abs(d2 - best_d2) <= 1e-6 and lane_name == fallback_lane):
                best_d2 = d2
                best_lane = lane_name
                best_s = lane_s
        except Exception:
            pass
    return best_lane, best_s


def _needs_scripted_lane_control(cfg):
    """True when Traffic Manager is too coarse for the requested lane script."""
    points = cfg.get("lane_change_points", [])
    if not points:
        return False
    current_lane = cfg.get("lane", LANE_MIDDLE)
    previous_time = None
    for point in points:
        target_lane = point.get("target_lane", current_lane)
        if abs(_lane_index(target_lane) - _lane_index(current_lane)) > 1:
            return True
        point_time = float(point.get("time_s", 0.0))
        duration = float(point.get("duration_s", 0.0) or 0.0)
        if previous_time is not None and (point_time - previous_time) < 8.0:
            return True
        if duration > 0.0 and duration < 4.0:
            return True
        current_lane = target_lane
        previous_time = point_time
    return False


def _maybe_fire_tm_lane_changes(plan, traffic_manager, t):
    for idx, point in enumerate(plan.cfg.get("lane_change_points", [])):
        if idx in plan._lane_change_fired:
            continue
        if t >= float(point["time_s"]):
            source_lane = getattr(plan, "current_lane", plan.cfg["lane"])
            target_lane = point["target_lane"]
            direction = _lane_change_direction(source_lane, target_lane)
            try:
                traffic_manager.force_lane_change(plan.actor, direction)
                print(
                    "  t={0:.2f}s TrafficManager lane change {1}: {2}->{3}".format(
                        t, plan.role, source_lane, target_lane))
                plan.current_lane = target_lane
            except Exception as exc:
                print("WARNING: force_lane_change failed: " + str(exc))
            plan._lane_change_fired.add(idx)


def _configure_tm_vehicle(traffic_manager, actor, speed_kmh):
    try:
        actor.set_autopilot(True, traffic_manager.get_port())
        traffic_manager.auto_lane_change(actor, False)
        traffic_manager.ignore_lights_percentage(actor, 100.0)
        traffic_manager.ignore_signs_percentage(actor, 100.0)
        # Do not set Traffic Manager's leading-vehicle distance here; scenario
        # spacing should come from the JSON/prompt scripts, not TM avoidance.
        speed_limit = max(1.0, float(actor.get_speed_limit()))
        desired = max(1.0, float(speed_kmh))
        pct = (speed_limit - desired) / speed_limit * 100.0
        traffic_manager.vehicle_percentage_speed_difference(actor, pct)
        return True
    except Exception as exc:
        print("WARNING: failed to configure Traffic Manager vehicle: " + str(exc))
        return False


class QueenVehicle(object):
    def __init__(self, actor, lane, offset_m, index, speed_kmh, target_s=0.0):
        self.actor = actor
        self.lane = lane
        self.offset_m = float(offset_m)
        self.index = int(index)
        self.parked = False
        self.braking = False
        self.speed_kmh = float(speed_kmh)
        self.target_s = float(target_s or 0.0)
        self.spawn_s = float(target_s or 0.0)
        self.last_guard_reset_wall_s = 0.0
        self.last_speed_refresh_wall_s = 0.0
        self.last_stall_log_wall_s = 0.0


def _queen_vehicles_enabled(args):
    return bool(getattr(args, "queen_vehicles", False))


def _queen_vehicle_spacing_m(args):
    return max(60.0, float(getattr(args, "queen_vehicle_spacing_m", 90.0) or 90.0))


def _queen_vehicle_safe_gap_m(args):
    return max(50.0, float(getattr(args, "queen_vehicle_safe_gap_m", 70.0) or 70.0))


def _queen_runtime_safe_gap_m(args):
    return max(65.0, _queen_vehicle_safe_gap_m(args))


def _queen_vehicle_runtime_safe_gap_m(args, vehicle_or_lane):
    lane = getattr(vehicle_or_lane, "lane", vehicle_or_lane)
    if lane == LANE_QUEEN_FOURTH:
        return 25.0
    return _queen_runtime_safe_gap_m(args)


def _queen_vehicle_speed_kmh(args):
    value = getattr(args, "queen_vehicle_speed_kmh", 50.0)
    if value in (None, ""):
        value = 50.0
    return max(0.0, float(value))


def _queen_vehicle_despawn_distance_m(args):
    value = getattr(args, "queen_vehicle_despawn_distance_m", 320.0)
    if value in (None, ""):
        value = 320.0
    return max(0.0, float(value))


def _queen_vehicle_specs(args):
    count = max(0, int(getattr(args, "queen_vehicle_count", 4) or 0))
    spacing = _queen_vehicle_spacing_m(args)
    template = [
        (LANE_QUEEN_FOURTH, -0.80),
        (LANE_QUEEN_FOURTH, 0.80),
        (LANE_LEFT, -2.8),
        (LANE_RIGHT, 3.2),
        (LANE_QUEEN_FOURTH, -1.90),
        (LANE_QUEEN_FOURTH, 1.90),
        (LANE_RIGHT, -2.2),
        (LANE_LEFT, 3.0),
        (LANE_QUEEN_FOURTH, -3.00),
        (LANE_QUEEN_FOURTH, 3.00),
        (LANE_RIGHT, 5.2),
    ]
    specs = []
    for idx in range(count):
        lane, multiplier = template[idx % len(template)]
        lap = idx // len(template)
        offset = multiplier * spacing
        if lap:
            offset += (1 if offset >= 0.0 else -1) * lap * spacing * 1.8
        specs.append((lane, offset))
    return specs


def _queen_vehicle_required_backward_distance_m(args):
    if not _queen_vehicles_enabled(args):
        return 0.0
    rear_offsets = [-offset for _lane, offset in _queen_vehicle_specs(args) if offset < 0.0]
    return (max(rear_offsets) + 20.0) if rear_offsets else 0.0


def _queen_vehicle_forward_padding_m(args):
    if not _queen_vehicles_enabled(args):
        return 0.0
    front_offsets = [offset for _lane, offset in _queen_vehicle_specs(args) if offset > 0.0]
    return (max(front_offsets) + 30.0) if front_offsets else 0.0


def _lane_path_min_s(lane_path):
    return -float(getattr(lane_path, "origin_s", 0.0) or 0.0)


def _lane_path_max_s(lane_path):
    if not lane_path.transforms:
        return 0.0
    return (len(lane_path.transforms) - 1) * lane_path.step_m - float(lane_path.origin_s or 0.0)


def _parse_queen_blueprint_filters(args):
    raw = str(getattr(args, "queen_vehicle_blueprints", "") or "")
    filters = [part.strip() for part in raw.split(",") if part.strip()]
    return filters or ["vehicle.audi.etron", "vehicle.lincoln.mkz_2020", "vehicle.tesla.model3"]


def _make_queen_blueprint(world, args, index):
    filters = _parse_queen_blueprint_filters(args)
    role = "queen_vehicle_{0}".format(index + 1)
    for offset in range(len(filters)):
        blueprint_filter = filters[(index + offset) % len(filters)]
        try:
            return make_blueprint(world, role, blueprint_filter)
        except Exception:
            pass
    return make_blueprint(world, role, "vehicle.lincoln.mkz_2020")


def _queen_occupancy(plans, lane_paths, fallback_ego_s):
    occupancy = []
    for plan in plans or []:
        actor = getattr(plan, "actor", None)
        if actor is None:
            continue
        fallback_lane = getattr(plan, "current_lane", None) or plan.cfg.get("lane", LANE_MIDDLE)
        fallback_s = getattr(plan, "_last_control_current_s", None)
        if fallback_s is None:
            fallback_s = getattr(plan, "_last_control_desired_s", fallback_ego_s)
        try:
            lane, s = _nearest_lane_name(
                lane_paths,
                actor.get_transform().location,
                fallback_lane=fallback_lane,
                fallback_s=float(fallback_s or fallback_ego_s),
            )
            occupancy.append((lane, s, plan.role))
        except Exception:
            pass
    return occupancy


def _queen_live_occupancy(queen_vehicles, lane_paths, fallback_ego_s, exclude=None):
    occupancy = []
    for vehicle in queen_vehicles or []:
        if vehicle is exclude or getattr(vehicle, "parked", False):
            continue
        actor = getattr(vehicle, "actor", None)
        if actor is None:
            continue
        lane, s = _queen_current_lane_s(vehicle, lane_paths, fallback_ego_s)
        if lane is None:
            continue
        occupancy.append((lane, s, "queen_vehicle_{0}".format(vehicle.index + 1)))
    return occupancy


def _queen_target_clear(lane, target_s, occupancy, safe_gap_m):
    for occ_lane, occ_s, _role in occupancy:
        if occ_lane == lane and abs(float(target_s) - float(occ_s)) < safe_gap_m:
            return False
    return True


def _queen_safe_target_s(lane_path, lane, desired_s, occupancy, safe_gap_m, index):
    min_s = _lane_path_min_s(lane_path) + 6.0
    max_s = _lane_path_max_s(lane_path) - 6.0
    if min_s >= max_s:
        return None
    desired_s = max(min_s, min(max_s, float(desired_s)))
    if _queen_target_clear(lane, desired_s, occupancy, safe_gap_m):
        return desired_s
    direction = 1.0 if desired_s >= 0.0 else -1.0
    for step in range(1, 8):
        delta = direction * safe_gap_m * step
        for candidate in (desired_s + delta, desired_s - delta):
            candidate = max(min_s, min(max_s, candidate))
            if _queen_target_clear(lane, candidate, occupancy, safe_gap_m):
                return candidate
    # Last resort: put it far ahead in the same lane, out of the scripted scene.
    fallback = max(min_s, min(max_s, desired_s + 220.0 + index * 25.0))
    if _queen_target_clear(lane, fallback, occupancy, safe_gap_m):
        return fallback
    return None


def _refresh_queen_tm_speed(traffic_manager, actor, speed_kmh):
    if traffic_manager is None or actor is None:
        return False
    try:
        desired = max(5.0, float(speed_kmh))
        try:
            traffic_manager.set_desired_speed(actor, desired)
        except Exception:
            pass
        speed_limit = max(1.0, float(actor.get_speed_limit()))
        pct = (speed_limit - desired) / speed_limit * 100.0
        traffic_manager.vehicle_percentage_speed_difference(actor, pct)
        return True
    except Exception:
        return False


def _configure_queen_tm_vehicle(traffic_manager, actor, speed_kmh, safe_gap_m):
    if traffic_manager is None:
        return False
    try:
        try:
            actor.set_simulate_physics(True)
        except Exception:
            pass
        actor.set_autopilot(True, traffic_manager.get_port())
        traffic_manager.auto_lane_change(actor, False)
        traffic_manager.ignore_lights_percentage(actor, 100.0)
        traffic_manager.ignore_signs_percentage(actor, 100.0)
        try:
            traffic_manager.distance_to_leading_vehicle(actor, max(25.0, float(safe_gap_m)))
        except Exception:
            pass
        try:
            traffic_manager.random_left_lanechange_percentage(actor, 0.0)
            traffic_manager.random_right_lanechange_percentage(actor, 0.0)
        except Exception:
            pass
        _refresh_queen_tm_speed(traffic_manager, actor, speed_kmh)
        return True
    except Exception as exc:
        print("WARNING: failed to configure queen Traffic Manager vehicle: {0}".format(exc))
        return False


def _queen_ignores_scenario_actors(vehicle):
    return getattr(vehicle, "lane", None) == LANE_QUEEN_FOURTH


def _configure_queen_scenario_collision_policy(traffic_manager, vehicle, plans):
    actor = getattr(vehicle, "actor", None)
    if traffic_manager is None or actor is None:
        return
    independent_background = _queen_ignores_scenario_actors(vehicle)
    detect_collision = not independent_background
    try:
        traffic_manager.ignore_vehicles_percentage(actor, 100.0 if independent_background else 0.0)
    except Exception:
        pass
    for plan in plans or []:
        other = getattr(plan, "actor", None)
        if other is None:
            continue
        try:
            if int(getattr(other, "id", -1)) == int(getattr(actor, "id", -2)):
                continue
        except Exception:
            pass
        try:
            traffic_manager.collision_detection(actor, other, detect_collision)
        except Exception:
            pass


def _queen_speed_kmh(args):
    return _queen_vehicle_speed_kmh(args)


def _queen_reference_s(vehicle, fallback_ego_s):
    try:
        return float(getattr(vehicle, "target_s", None))
    except Exception:
        return float(fallback_ego_s or 0.0) + float(getattr(vehicle, "offset_m", 0.0) or 0.0)


def _queen_current_lane_s(vehicle, lane_paths, fallback_ego_s):
    actor = getattr(vehicle, "actor", None)
    if actor is None:
        return None, None
    try:
        fallback_s = _queen_reference_s(vehicle, fallback_ego_s)
        lane, s = _nearest_lane_name(
            lane_paths,
            actor.get_transform().location,
            fallback_lane=vehicle.lane,
            fallback_s=fallback_s,
            window_m=180.0,
        )
        return lane, s
    except Exception:
        return None, None


def _queen_lane_guard_reason(vehicle, lane_paths, ego_s):
    actor = getattr(vehicle, "actor", None)
    lane_path = lane_paths.get(getattr(vehicle, "lane", None))
    if actor is None or lane_path is None:
        return None
    try:
        xf = actor.get_transform()
        loc = xf.location
        fallback_s = _queen_reference_s(vehicle, ego_s)
        nearest_lane, _nearest_s = _nearest_lane_name(
            lane_paths,
            loc,
            fallback_lane=vehicle.lane,
            fallback_s=fallback_s,
            window_m=140.0,
        )
        intended_s = lane_path.nearest_s(
            loc,
            fallback_s=fallback_s,
            window_m=140.0,
        )
        expected = lane_path.sample(intended_s).location
        dx = float(loc.x) - float(expected.x)
        dy = float(loc.y) - float(expected.y)
        lateral_m = math.sqrt(dx * dx + dy * dy)
        z_delta = float(loc.z) - float(expected.z)
        if nearest_lane != vehicle.lane:
            return "left_intended_lane:{0}->{1}".format(vehicle.lane, nearest_lane)
        if lateral_m > 5.5:
            return "lateral_offset:{0:.1f}m".format(lateral_m)
        if z_delta > 1.2 or z_delta < -1.0:
            return "height_offset:{0:.1f}m".format(z_delta)
    except Exception:
        return None
    return None


def _reset_queen_vehicle_to_guarded_lane(
    vehicle,
    lane_paths,
    ego_s,
    traffic_manager,
    safe_gap_m,
    reason,
    occupancy=None,
    plans=None,
):
    actor = getattr(vehicle, "actor", None)
    lane_path = lane_paths.get(getattr(vehicle, "lane", None))
    if actor is None or lane_path is None:
        return False
    now = time.monotonic()
    if now - float(getattr(vehicle, "last_guard_reset_wall_s", 0.0) or 0.0) < 0.75:
        return False
    min_s = _lane_path_min_s(lane_path) + 8.0
    max_s = _lane_path_max_s(lane_path) - 8.0
    if min_s >= max_s:
        return False
    desired_s = max(min_s, min(max_s, _queen_reference_s(vehicle, ego_s)))
    target_s = _queen_safe_target_s(
        lane_path,
        vehicle.lane,
        desired_s,
        occupancy or [],
        safe_gap_m,
        vehicle.index,
    )
    if target_s is None:
        return False
    try:
        try:
            actor.set_autopilot(False)
        except Exception:
            pass
        try:
            actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
        except Exception:
            pass
        _zero_actor_motion(actor)
        actor.set_transform(_transform_with_lift(lane_path.sample(target_s), 0.08))
        try:
            actor.set_simulate_physics(True)
        except Exception:
            pass
        vehicle.target_s = target_s
        vehicle.braking = False
        vehicle.last_guard_reset_wall_s = now
        _configure_queen_tm_vehicle(traffic_manager, actor, vehicle.speed_kmh, safe_gap_m)
        _configure_queen_scenario_collision_policy(traffic_manager, vehicle, plans)
        print(
            "Queen lane guard reset {0}: role=queen_vehicle_{1} lane={2} s={3:.1f}".format(
                reason,
                vehicle.index + 1,
                vehicle.lane,
                target_s,
            )
        )
        return True
    except Exception as exc:
        print("WARNING: Queen lane guard reset failed: {0}".format(exc))
        return False


def _queen_safety_delta(queen_lane, queen_s, occupancy):
    best = None
    for occ_lane, occ_s, _role in occupancy:
        if occ_lane != queen_lane:
            continue
        delta = float(queen_s) - float(occ_s)
        if best is None or abs(delta) < abs(best):
            best = delta
    return best


def _park_queen_vehicle(actor):
    try:
        try:
            actor.set_autopilot(False)
        except Exception:
            pass
        try:
            actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
        except Exception:
            pass
        try:
            actor.set_simulate_physics(False)
        except Exception:
            pass
        actor.set_transform(carla.Transform(carla.Location(0.0, 0.0, -1000.0), carla.Rotation()))
        _zero_actor_motion(actor)
    except Exception:
        pass


def _tor_support_detach_roles(experiment_tor_windows):
    roles = set()
    active = False
    for window in experiment_tor_windows or []:
        state = window.get("fallback_recovery_state")
        window_active = bool(
            window.get("manual_active")
            or window.get("fallback_active")
            or state in (
                "pulling_over",
                "stop_hold",
                "awaiting_return_key",
                "returning_to_auto_lane",
            )
            or window.get("outcome") in ("success", "failure")
        )
        if not window_active:
            continue
        active = True
        fields = window.get("fields", {}) or {}
        for key in ("target_role", "visual_target_role", "role"):
            role = fields.get(key) or window.get(key)
            if role:
                roles.add(str(role))
    if active and not roles:
        roles.add("vehicle_1")
    return roles


def _tor_traffic_safety_active(experiment_tor_windows):
    return bool(_tor_support_detach_roles(experiment_tor_windows))


def _detach_tor_support_vehicle(plan, traffic_manager=None, marker_outlet=None, scenario_t=None, reason="tor_resolved"):
    actor = getattr(plan, "actor", None)
    if actor is None or getattr(plan, "tor_detached", False):
        return False
    try:
        try:
            actor.set_simulate_physics(True)
        except Exception:
            pass
        _zero_actor_motion(actor)
        _release_vehicle_brake(actor)
        released_to_tm = False
        if traffic_manager is not None and hasattr(actor, "set_autopilot"):
            released_to_tm = _configure_tm_vehicle(
                traffic_manager,
                actor,
                float(plan.cfg.get("speed_kmh", 0.0) or 0.0),
            )
            try:
                traffic_manager.distance_to_leading_vehicle(actor, 20.0)
            except Exception:
                pass
        if not released_to_tm:
            try:
                xf = actor.get_transform()
                forward = xf.rotation.get_forward_vector()
                speed_mps = max(0.0, float(getattr(plan, "speed_mps", 0.0) or 0.0))
                actor.set_target_velocity(carla.Vector3D(
                    forward.x * speed_mps,
                    forward.y * speed_mps,
                    forward.z * speed_mps,
                ))
            except Exception:
                _apply_safe_brake(actor)
        plan.tor_detached = True
        _push_marker(
            marker_outlet,
            "SCENARIO_ACTOR_DETACHED_AFTER_TOR",
            scenario_t=scenario_t,
            role=plan.role,
            actor_id=getattr(actor, "id", None),
            reason=reason,
            mode="traffic_manager" if released_to_tm else "velocity_hold",
        )
        print(
            "[t={0}] detached {1} from ego-following after TOR/MRM; actor released to {2}.".format(
                "?" if scenario_t is None else "{0:.2f}s".format(float(scenario_t)),
                plan.role,
                "Traffic Manager" if released_to_tm else "velocity hold",
            )
        )
        return True
    except Exception as exc:
        print("WARNING: failed to detach {0} after TOR/MRM: {1}".format(plan.role, exc))
        return False


def _despawn_queen_vehicle(vehicle, reason, travelled_m=None):
    actor = getattr(vehicle, "actor", None)
    if actor is None:
        return False
    try:
        try:
            actor.set_autopilot(False)
        except Exception:
            pass
        _zero_actor_motion(actor)
        try:
            actor.destroy()
        except Exception:
            _park_queen_vehicle(actor)
        vehicle.actor = None
        vehicle.parked = True
        print(
            "Queen despawn: role=queen_vehicle_{0} reason={1} travelled_m={2}".format(
                vehicle.index + 1,
                reason,
                "?" if travelled_m is None else "{0:.1f}".format(travelled_m),
            )
        )
        return True
    except Exception as exc:
        print("WARNING: Queen despawn failed: {0}".format(exc))
        return False


def _spawn_queen_vehicles(args, world, lane_paths, plans, ego_plan, traffic_manager, ego_speed_kmh):
    if not _queen_vehicles_enabled(args):
        return []
    specs = _queen_vehicle_specs(args)
    if not specs:
        return []
    reusable = []
    if getattr(args, "continuous_session", False):
        reusable = list(getattr(args, "_continuous_queen_vehicles", []) or [])
    safe_gap_m = _queen_runtime_safe_gap_m(args)
    ego_s = float(getattr(ego_plan, "initial_s", 0.0) or 0.0)
    occupancy = _queen_occupancy(plans, lane_paths, ego_s)
    vehicles = []
    for idx, (lane, offset_m) in enumerate(specs):
        lane_path = lane_paths.get(lane)
        if lane_path is None:
            continue
        target_s = _queen_safe_target_s(
            lane_path,
            lane,
            ego_s + offset_m,
            occupancy,
            safe_gap_m,
            idx,
        )
        if target_s is None:
            continue
        bp = _make_queen_blueprint(world, args, idx)
        xf = lane_path.sample(target_s)
        existing = reusable[idx] if idx < len(reusable) else None
        actor = getattr(existing, "actor", None)
        reused = _actor_is_usable(actor)
        if not reused:
            actor = None
            for lift_z in (SPAWN_CLEARANCE_Z_OFFSET, SPAWN_CLEARANCE_Z_OFFSET + 0.30):
                try:
                    actor = world.try_spawn_actor(bp, _transform_with_lift(xf, lift_z))
                except Exception:
                    actor = None
                if actor is not None:
                    break
        if actor is None:
            continue
        speed_kmh = _queen_speed_kmh(args)
        _place_physics_support_at_transform(actor, xf)
        if reused and existing is not None:
            vehicle = existing
            vehicle.actor = actor
            vehicle.lane = lane
            vehicle.offset_m = float(offset_m)
            vehicle.index = int(idx)
            vehicle.parked = False
            vehicle.braking = False
            vehicle.speed_kmh = float(speed_kmh)
            vehicle.target_s = float(target_s)
            vehicle.spawn_s = float(target_s)
            vehicle.tor_safety_parked = False
            vehicle.last_guard_reset_wall_s = 0.0
            vehicle.last_speed_refresh_wall_s = 0.0
            vehicle.last_stall_log_wall_s = 0.0
        else:
            vehicle = QueenVehicle(actor, lane, offset_m, idx, speed_kmh, target_s=target_s)
            vehicle.tor_safety_parked = False
        _configure_queen_tm_vehicle(
            traffic_manager,
            actor,
            speed_kmh,
            _queen_vehicle_runtime_safe_gap_m(args, vehicle),
        )
        _configure_queen_scenario_collision_policy(traffic_manager, vehicle, plans)
        vehicles.append(vehicle)
        occupancy.append((lane, target_s, "queen_vehicle_{0}".format(idx + 1)))
    if vehicles:
        try:
            world.tick()
        except Exception:
            pass
        if getattr(args, "continuous_session", False):
            setattr(args, "_continuous_queen_vehicles", vehicles)
        reused_count = sum(
            1 for vehicle in vehicles
            if any(vehicle is existing for existing in reusable)
        )
        print(
            "Queen/background Traffic Manager vehicles enabled: active {0}/{1} vehicle(s), reused={2}, spawned={3}, target_speed={4:.1f} km/h, despawn_after={5:.1f}m.".format(
                len(vehicles),
                len(specs),
                reused_count,
                max(0, len(vehicles) - reused_count),
                _queen_vehicle_speed_kmh(args),
                _queen_vehicle_despawn_distance_m(args),
            )
        )
    else:
        print("WARNING: Queen/background vehicles requested, but no visual vehicles could be spawned.")
    return vehicles


def _update_queen_vehicles(
    queen_vehicles,
    lane_paths,
    plans,
    ego_s,
    args,
    traffic_manager=None,
    tor_safety_active=False,
):
    if not queen_vehicles:
        return
    if tor_safety_active:
        for vehicle in queen_vehicles:
            actor = getattr(vehicle, "actor", None)
            if actor is None or getattr(vehicle, "parked", False):
                continue
            if getattr(vehicle, "tor_safety_parked", False):
                continue
            queen_lane, queen_s = _queen_current_lane_s(vehicle, lane_paths, ego_s)
            near_ego = False
            if queen_s is not None:
                near_ego = abs(float(queen_s) - float(ego_s or 0.0)) < _queen_vehicle_runtime_safe_gap_m(args, vehicle)
            in_pull_over_lane = queen_lane in (LANE_QUEEN_FOURTH, LANE_OUTER_RIGHT)
            if in_pull_over_lane or near_ego:
                _park_queen_vehicle(actor)
                safety_mode = "clear"
            else:
                try:
                    if hasattr(actor, "set_autopilot"):
                        actor.set_autopilot(False)
                except Exception:
                    pass
                _zero_actor_motion(actor)
                _apply_safe_brake(actor)
                safety_mode = "visible_hold"
            vehicle.braking = True
            vehicle.tor_safety_parked = True
            print(
                "Queen TOR safety {0}: role=queen_vehicle_{1} lane={2} reason=tor_or_mrm_active".format(
                    safety_mode,
                    vehicle.index + 1,
                    queen_lane,
                )
            )
        return
    scenario_occupancy = _queen_occupancy(plans, lane_paths, ego_s)
    despawn_distance_m = _queen_vehicle_despawn_distance_m(args)
    for vehicle in queen_vehicles:
        actor = vehicle.actor
        if actor is None or vehicle.parked:
            continue
        safe_gap_m = _queen_vehicle_runtime_safe_gap_m(args, vehicle)
        now = time.monotonic()
        if getattr(vehicle, "tor_safety_parked", False):
            vehicle.tor_safety_parked = False
            vehicle.braking = False
            _reset_queen_vehicle_to_guarded_lane(
                vehicle,
                lane_paths,
                ego_s,
                traffic_manager,
                safe_gap_m,
                "tor_safety_release",
                occupancy=scenario_occupancy,
                plans=plans,
            )
        if not vehicle.braking and now - float(getattr(vehicle, "last_speed_refresh_wall_s", 0.0) or 0.0) >= 0.50:
            if _refresh_queen_tm_speed(traffic_manager, actor, vehicle.speed_kmh):
                vehicle.last_speed_refresh_wall_s = now
        queen_occupancy = _queen_live_occupancy(
            queen_vehicles,
            lane_paths,
            ego_s,
            exclude=vehicle,
        )
        if _queen_ignores_scenario_actors(vehicle):
            occupancy = queen_occupancy
        else:
            occupancy = scenario_occupancy + queen_occupancy
        guard_reason = _queen_lane_guard_reason(vehicle, lane_paths, ego_s)
        if guard_reason:
            _reset_queen_vehicle_to_guarded_lane(
                vehicle,
                lane_paths,
                ego_s,
                traffic_manager,
                safe_gap_m,
                guard_reason,
                occupancy=occupancy,
                plans=plans,
            )
            continue
        queen_lane, queen_s = _queen_current_lane_s(vehicle, lane_paths, ego_s)
        if queen_lane is None:
            continue
        travelled_m = max(0.0, float(queen_s) - float(getattr(vehicle, "spawn_s", queen_s)))
        if despawn_distance_m > 0.0 and travelled_m >= despawn_distance_m:
            _despawn_queen_vehicle(vehicle, "distance_limit", travelled_m=travelled_m)
            continue
        if queen_lane == vehicle.lane:
            vehicle.target_s = float(queen_s)
        if (
            vehicle.lane == LANE_QUEEN_FOURTH
            and not vehicle.braking
            and _actor_speed_mps(actor) < 0.7
            and time.monotonic() - float(getattr(vehicle, "last_stall_log_wall_s", 0.0) or 0.0) >= 1.5
        ):
            vehicle.last_stall_log_wall_s = time.monotonic()
            print(
                "QueenFourth slow diagnostic: role=queen_vehicle_{0} nearest_lane={1} s={2:.1f} target_s={3:.1f} "
                "ego_s={4:.1f} autopilot_independent=True".format(
                    vehicle.index + 1,
                    queen_lane,
                    queen_s,
                    vehicle.target_s,
                    float(ego_s or 0.0),
                )
            )
        delta = _queen_safety_delta(queen_lane, queen_s, occupancy)
        if delta is None:
            if vehicle.braking:
                _configure_queen_tm_vehicle(traffic_manager, actor, vehicle.speed_kmh, safe_gap_m)
                _configure_queen_scenario_collision_policy(traffic_manager, vehicle, plans)
                vehicle.braking = False
            continue
        if delta < 0.0 and abs(delta) < safe_gap_m * 0.90:
            # Rear background vehicles should yield before they close on the
            # scripted scene. They remain physical TM actors, just held back.
            if not vehicle.braking:
                print(
                    "Queen safety hold: role=queen_vehicle_{0} lane={1} delta={2:.1f}m safe_gap={3:.1f}m".format(
                        vehicle.index + 1,
                        queen_lane,
                        delta,
                        safe_gap_m,
                    )
                )
            try:
                actor.set_autopilot(False)
            except Exception:
                pass
            try:
                actor.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0, hand_brake=True))
            except Exception:
                pass
            vehicle.braking = True
        elif vehicle.braking and abs(delta) > safe_gap_m * 1.25:
            _configure_queen_tm_vehicle(traffic_manager, actor, vehicle.speed_kmh, safe_gap_m)
            _configure_queen_scenario_collision_policy(traffic_manager, vehicle, plans)
            vehicle.braking = False
            vehicle.last_speed_refresh_wall_s = time.monotonic()
            print(
                "Queen safety release: role=queen_vehicle_{0} lane={1} delta={2:.1f}m".format(
                    vehicle.index + 1,
                    queen_lane,
                    delta,
                )
            )


def _destroy_queen_vehicles(queen_vehicles):
    destroyed = 0
    for vehicle in queen_vehicles or []:
        actor = getattr(vehicle, "actor", None)
        if actor is None:
            continue
        try:
            try:
                actor.set_autopilot(False)
            except Exception:
                pass
            _zero_actor_motion(actor)
            actor.destroy()
            destroyed += 1
        except Exception:
            pass
        vehicle.actor = None
    return destroyed


def _hold_collision_scene_for_confirmation(plans, queen_vehicles=None):
    held = 0
    for plan in plans or []:
        actor = getattr(plan, "actor", None)
        if actor is None:
            continue
        try:
            try:
                if hasattr(actor, "set_autopilot"):
                    actor.set_autopilot(False)
            except Exception:
                pass
            _zero_actor_motion(actor)
            _apply_safe_brake(actor)
            # Keep the DReyeVR ego physics alive for cockpit stability. Support
            # vehicles can be physics-frozen so the accident pose remains visible
            # while the confirmation popup waits for the operator.
            if getattr(plan, "role", "") != "ego":
                try:
                    actor.set_simulate_physics(False)
                except Exception:
                    pass
            held += 1
        except Exception:
            pass
    for vehicle in queen_vehicles or []:
        actor = getattr(vehicle, "actor", None)
        if actor is None:
            continue
        try:
            try:
                if hasattr(actor, "set_autopilot"):
                    actor.set_autopilot(False)
            except Exception:
                pass
            _zero_actor_motion(actor)
            _apply_safe_brake(actor)
            try:
                actor.set_simulate_physics(False)
            except Exception:
                pass
            held += 1
        except Exception:
            pass
    return held


def _destroy_continuous_queen_vehicles(args):
    vehicles = list(getattr(args, "_continuous_queen_vehicles", []) or [])
    destroyed = _destroy_queen_vehicles(vehicles)
    if destroyed:
        print("Destroying {0} continuous queen/background vehicle(s).".format(destroyed))
    setattr(args, "_continuous_queen_vehicles", [])
    return destroyed


def _normalize_angle_deg(angle):
    return (angle + 180.0) % 360.0 - 180.0


def _set_velocity_follow(actor, lane_path, s, speed_mps):
    """Hold a vehicle on a lane centerline with velocity + yaw commands."""
    lookahead_m = max(2.0, min(8.0, speed_mps * 0.25))
    here = lane_path.sample(s)
    ahead = lane_path.sample(s + lookahead_m)
    dx = ahead.location.x - here.location.x
    dy = ahead.location.y - here.location.y
    dz = ahead.location.z - here.location.z
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag < 0.001:
        yaw_rad = math.radians(here.rotation.yaw)
        dx, dy, dz, mag = math.cos(yaw_rad), math.sin(yaw_rad), 0.0, 1.0
    fwd_x = dx / mag
    fwd_y = dy / mag
    fwd_z = dz / mag

    cur = actor.get_transform()
    err_x = here.location.x - cur.location.x
    err_y = here.location.y - cur.location.y
    along_err = err_x * fwd_x + err_y * fwd_y
    lat_x = err_x - along_err * fwd_x
    lat_y = err_y - along_err * fwd_y

    lateral_gain = 1.35
    max_lateral_mps = max(2.0, min(5.0, speed_mps * 0.22))
    corr_x = lat_x * lateral_gain
    corr_y = lat_y * lateral_gain
    corr_mag = math.sqrt(corr_x * corr_x + corr_y * corr_y)
    if corr_mag > max_lateral_mps:
        scale = max_lateral_mps / corr_mag
        corr_x *= scale
        corr_y *= scale

    # Small longitudinal correction keeps ego near the scripted s position
    # without letting a large error yank the car across lanes.
    long_corr = max(-2.0, min(2.0, along_err * 0.18))
    vx = fwd_x * (speed_mps + long_corr) + corr_x
    vy = fwd_y * (speed_mps + long_corr) + corr_y
    vz = fwd_z * speed_mps
    actor.set_target_velocity(carla.Vector3D(vx, vy, vz))

    aim_x = ahead.location.x - cur.location.x + corr_x * 0.45
    aim_y = ahead.location.y - cur.location.y + corr_y * 0.45
    if abs(aim_x) > 0.01 or abs(aim_y) > 0.01:
        desired_yaw = math.degrees(math.atan2(aim_y, aim_x))
    else:
        desired_yaw = ahead.rotation.yaw
    yaw_err = _normalize_angle_deg(desired_yaw - cur.rotation.yaw)
    yaw_rate = max(-70.0, min(70.0, yaw_err * 4.0))
    actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, yaw_rate))


def _scripted_target_transform(plan, lane_paths, ego_s, t, ego_reference_speed_mps=0.0):
    gap_points = plan.cfg.get("gap_points", [])
    time_gap_points = plan.cfg.get("time_gap_points", [])
    speed_points = plan.cfg.get("speed_points", [])
    if gap_points:
        gap = sample_gap(t, plan.initial_s, gap_points)
        target_s = ego_s + gap
    elif time_gap_points:
        reference_speed_mps = max(0.001, float(ego_reference_speed_mps or 0.0))
        time_gap_s = sample_time_gap_s(
            t,
            plan.initial_s,
            time_gap_points,
            reference_speed_mps,
            initial_bumper_gap_m=_time_gap_initial_bumper_gap_m(plan),
        )
        relation = _time_gap_relation_hint(plan, t, time_gap_s)
        target_s = ego_s + _time_gap_to_center_delta_m(
            time_gap_s,
            reference_speed_mps,
            getattr(plan, "time_gap_bumper_clearance_m", DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M),
            relation,
        )
    elif speed_points:
        target_s = plan.initial_s + integrate_speed_m(t, plan.speed_mps, speed_points)
    else:
        target_s = plan.initial_s + plan.speed_mps * t
    lane_state = sample_lane(t, plan.cfg["lane"], plan.cfg.get("lane_change_points", []))
    src = lane_paths[lane_state.src_lane].sample(target_s)
    dst = lane_paths[lane_state.dst_lane].sample(target_s)
    return _lerp_transform(src, dst, lane_state.blend), target_s, lane_state


def _scripted_command_speed_mps(
    plan,
    t,
    fallback_speed_mps,
    ego_reference_speed_mps,
    ego_current_speed_mps,
):
    if plan.cfg.get("time_gap_points", []) and not plan.cfg.get("gap_points", []):
        command_mps = sample_time_gap_follow_speed_mps(
            t,
            plan.initial_s,
            plan.cfg.get("time_gap_points", []),
            ego_reference_speed_mps,
            ego_current_speed_mps,
            initial_bumper_gap_m=_time_gap_initial_bumper_gap_m(plan),
        )
        if getattr(plan, "control_mode", "") == "vehicle-control":
            error_m = float(getattr(plan, "_last_control_error_m", 0.0) or 0.0)
            if error_m > 1.0:
                command_mps += min(12.0, error_m * 0.35)
            elif error_m < -1.0:
                command_mps = max(0.0, command_mps + max(-10.0, error_m * 0.55))
            cap_mps = max(
                22.2,
                float(ego_reference_speed_mps or 0.0) + 16.0,
                float(fallback_speed_mps or 0.0) + 16.0,
            )
            command_mps = min(command_mps, cap_mps)
            if getattr(plan, "_last_control_lane_settle_active", False):
                command_mps = max(command_mps, 2.8)
        return command_mps
    command_mps = sample_speed_mps(t, fallback_speed_mps, plan.cfg.get("speed_points", []))
    if getattr(plan, "control_mode", "") == "vehicle-control":
        # Plain scripted vehicles previously had NO catch-up: once behind the
        # scripted s-position they stayed behind forever. Mirror the (gentler)
        # time-gap catch-up so they close the error instead of accumulating it.
        error_m = float(getattr(plan, "_last_control_error_m", 0.0) or 0.0)
        if error_m > 1.0:
            command_mps += min(8.0, error_m * 0.25)
        elif error_m < -1.0:
            command_mps = max(0.0, command_mps + max(-8.0, error_m * 0.45))
        cap_mps = max(
            22.2,
            float(ego_reference_speed_mps or 0.0) + 16.0,
            float(fallback_speed_mps or 0.0) + 16.0,
        )
        command_mps = min(command_mps, cap_mps)
    return command_mps


def _set_velocity_toward_transform(
    actor,
    target_xf,
    speed_mps,
    correction_gain=1.4,
    max_correction_mps=None,
    yaw_rate_cap_dps=60.0,
    yaw_gain=4.0,
):
    """Drive actor toward a scripted path without teleporting it each frame."""
    cur = actor.get_transform()
    yaw_rad = math.radians(target_xf.rotation.yaw)
    base_vx = math.cos(yaw_rad) * speed_mps
    base_vy = math.sin(yaw_rad) * speed_mps
    base_vz = 0.0

    dx = target_xf.location.x - cur.location.x
    dy = target_xf.location.y - cur.location.y
    dz = target_xf.location.z - cur.location.z
    correction_gain = max(0.0, float(correction_gain or 0.0))
    if max_correction_mps is None:
        max_correction_mps = max(5.0, min(14.0, speed_mps * 0.45))
    else:
        max_correction_mps = max(0.0, float(max_correction_mps or 0.0))
    corr_x = dx * correction_gain
    corr_y = dy * correction_gain
    corr_z = dz * min(0.6, correction_gain)
    corr_mag = math.sqrt(corr_x * corr_x + corr_y * corr_y + corr_z * corr_z)
    if corr_mag > max_correction_mps:
        scale = max_correction_mps / corr_mag
        corr_x *= scale
        corr_y *= scale
        corr_z *= scale

    actor.set_target_velocity(carla.Vector3D(base_vx + corr_x, base_vy + corr_y, base_vz + corr_z))

    yaw_err = _normalize_angle_deg(target_xf.rotation.yaw - cur.rotation.yaw)
    yaw_rate_cap_dps = max(0.0, float(yaw_rate_cap_dps or 0.0))
    yaw_gain = max(0.0, float(yaw_gain or 0.0))
    yaw_rate = max(-yaw_rate_cap_dps, min(yaw_rate_cap_dps, yaw_err * yaw_gain))
    actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, yaw_rate))


def _set_experiment_lane_target_velocity(actor, target_xf, speed_mps, gentle_pull_over=False):
    if gentle_pull_over:
        _set_velocity_toward_transform(
            actor,
            target_xf,
            speed_mps,
            correction_gain=0.65,
            max_correction_mps=max(1.1, min(2.4, float(speed_mps or 0.0) * 0.55)),
            yaw_rate_cap_dps=32.0,
            yaw_gain=2.2,
        )
    else:
        _set_velocity_toward_transform(actor, target_xf, speed_mps)


def _actor_speed_mps(actor):
    try:
        v = actor.get_velocity()
        return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    except Exception:
        return 0.0


def _actor_half_length_m(actor, fallback_m=DEFAULT_VEHICLE_HALF_LENGTH_M):
    try:
        half_length = float(actor.bounding_box.extent.x)
        if 0.2 <= half_length <= 8.0:
            return half_length
    except Exception:
        pass
    return float(fallback_m)


def _combined_bumper_clearance_m(ego_actor, actor):
    return _actor_half_length_m(ego_actor) + _actor_half_length_m(actor)


def _signed_bumper_gap_from_center_delta_m(center_delta_m, bumper_clearance_m):
    relation = _sign_of(center_delta_m)
    if not relation:
        relation = 1
    return float(center_delta_m or 0.0) - relation * max(0.0, float(bumper_clearance_m or 0.0))


def _bumper_overlap_m(center_delta_m, bumper_clearance_m):
    return max(0.0, max(0.0, float(bumper_clearance_m or 0.0)) - abs(float(center_delta_m or 0.0)))


def _lane_lateral_error_m(actor, lane_path, s):
    try:
        here = lane_path.sample(s)
        ahead = lane_path.sample(s + 4.0)
        cur = actor.get_transform().location
        dx = ahead.location.x - here.location.x
        dy = ahead.location.y - here.location.y
        mag = math.sqrt(dx * dx + dy * dy)
        if mag < 0.001:
            yaw_rad = math.radians(here.rotation.yaw)
            dx, dy, mag = math.cos(yaw_rad), math.sin(yaw_rad), 1.0
        fwd_x = dx / mag
        fwd_y = dy / mag
        err_x = cur.x - here.location.x
        err_y = cur.y - here.location.y
        return err_x * (-fwd_y) + err_y * fwd_x
    except Exception:
        return 0.0


def _actual_actor_lane(world, actor):
    try:
        wp = world.get_map().get_waypoint(
            actor.get_transform().location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if wp is None:
            return "lane_id=?"
        return "road={0} lane_id={1}".format(wp.road_id, wp.lane_id)
    except Exception:
        return "lane_id=?"


def _actual_actor_lane_ids(world, actor):
    try:
        wp = world.get_map().get_waypoint(
            actor.get_transform().location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if wp is None:
            return 0, 0
        return int(wp.road_id), int(wp.lane_id)
    except Exception:
        return 0, 0


def _push_ego_telemetry(
    marker_outlet,
    world,
    ego_plan,
    lane_path,
    ego_s,
    expected_ego_s,
    scenario_t,
    frame,
    manual_control_active=False,
):
    if marker_outlet is None or ego_plan is None or ego_plan.actor is None or lane_path is None:
        return
    try:
        actor = ego_plan.actor
        tf = actor.get_transform()
        loc = tf.location
        actual_s = float(ego_s or 0.0)
        target_s = float(expected_ego_s or 0.0)
        lane_tf = lane_path.sample(actual_s)
        lane_yaw = float(lane_tf.rotation.yaw)
        yaw = float(tf.rotation.yaw)
        speed_mps = _actor_speed_mps(actor)
        road_id, lane_id = _actual_actor_lane_ids(world, actor)
        marker_outlet.push_telemetry(
            [
                float(scenario_t or 0.0),
                time.monotonic(),
                float(frame),
                float(getattr(actor, "id", 0) or 0),
                float(loc.x),
                float(loc.y),
                float(loc.z),
                float(speed_mps),
                float(speed_mps) * 3.6,
                yaw,
                lane_yaw,
                _normalize_angle_deg(yaw - lane_yaw),
                _lane_lateral_error_m(actor, lane_path, actual_s),
                actual_s,
                target_s,
                target_s - actual_s,
                float(road_id),
                float(lane_id),
                1.0 if manual_control_active else 0.0,
                0.0 if manual_control_active else 1.0,
            ]
        )
    except Exception as exc:
        print("WARNING: CARLA ego telemetry sample failed: {0}".format(exc))


def _vector_xy_speed_mps(vector):
    return math.sqrt(float(vector.x) * float(vector.x) + float(vector.y) * float(vector.y))


def _vector_xy_yaw_deg(vector):
    speed = _vector_xy_speed_mps(vector)
    if speed < 0.001:
        return None
    return math.degrees(math.atan2(float(vector.y), float(vector.x)))


def _tor_fallback_phase(action, elapsed):
    if not action:
        return ""
    brake_duration = max(0.001, float(action.get("brake_duration_s", 0.0) or 0.0))
    lane_duration = max(0.001, float(action.get("lane_change_duration_s", 0.0) or 0.0))
    if elapsed < brake_duration:
        return "brake"
    if elapsed < brake_duration + lane_duration:
        return "lane_change"
    return "final_brake"


def _print_tor_fallback_body_debug(
    world,
    actor,
    lane_path,
    actual_s,
    expected_ego_s,
    target_xf,
    action,
    t,
):
    if actor is None or lane_path is None or action is None:
        return
    try:
        tf = actor.get_transform()
        loc = tf.location
        lane_tf = lane_path.sample(float(actual_s or 0.0))
        lane_yaw = float(lane_tf.rotation.yaw)
        yaw = float(tf.rotation.yaw)
        vel = actor.get_velocity()
        ang = actor.get_angular_velocity()
        acc = actor.get_acceleration()
        speed_mps = math.sqrt(vel.x * vel.x + vel.y * vel.y + vel.z * vel.z)
        vel_yaw = _vector_xy_yaw_deg(vel)
        vel_yaw_text = "nan" if vel_yaw is None else "{0:.1f}".format(vel_yaw)
        vel_yaw_err_text = "nan" if vel_yaw is None else "{0:.1f}".format(
            _normalize_angle_deg(vel_yaw - lane_yaw)
        )
        road_id, lane_id = _actual_actor_lane_ids(world, actor)
        elapsed = max(0.0, float(t or 0.0) - float(action.get("started_at", t) or t))
        target_dx = target_dy = target_dist = target_yaw = target_yaw_err = None
        if target_xf is not None:
            target_dx = float(target_xf.location.x) - float(loc.x)
            target_dy = float(target_xf.location.y) - float(loc.y)
            target_dist = math.sqrt(target_dx * target_dx + target_dy * target_dy)
            target_yaw = float(target_xf.rotation.yaw)
            target_yaw_err = _normalize_angle_deg(target_yaw - yaw)
        print(
            "TOR_FALLBACK_BODY "
            "t={t:.3f} phase={phase} elapsed={elapsed:.3f} "
            "from_lane={from_lane} to_lane={to_lane} road={road} lane_id={lane_id} "
            "loc={loc} yaw={yaw:.1f} lane_yaw={lane_yaw:.1f} yaw_err={yaw_err:.1f} "
            "speed_kmh={speed_kmh:.1f} vel_yaw={vel_yaw} vel_yaw_err={vel_yaw_err} "
            "ang_z={ang_z:.2f} acc_xy={acc_xy:.2f} "
            "lat_err={lat_err:.2f} actual_s={actual_s:.1f} expected_s={expected_s:.1f} "
            "target_dx={target_dx} target_dy={target_dy} target_dist={target_dist} "
            "target_yaw={target_yaw} target_yaw_err={target_yaw_err}".format(
                t=float(t or 0.0),
                phase=_tor_fallback_phase(action, elapsed),
                elapsed=elapsed,
                from_lane=action.get("from_lane"),
                to_lane=action.get("to_lane"),
                road=road_id,
                lane_id=lane_id,
                loc=_format_location(loc),
                yaw=yaw,
                lane_yaw=lane_yaw,
                yaw_err=_normalize_angle_deg(yaw - lane_yaw),
                speed_kmh=speed_mps * 3.6,
                vel_yaw=vel_yaw_text,
                vel_yaw_err=vel_yaw_err_text,
                ang_z=float(ang.z),
                acc_xy=_vector_xy_speed_mps(acc),
                lat_err=_lane_lateral_error_m(actor, lane_path, float(actual_s or 0.0)),
                actual_s=float(actual_s or 0.0),
                expected_s=float(expected_ego_s or 0.0),
                target_dx="nan" if target_dx is None else "{0:.2f}".format(target_dx),
                target_dy="nan" if target_dy is None else "{0:.2f}".format(target_dy),
                target_dist="nan" if target_dist is None else "{0:.2f}".format(target_dist),
                target_yaw="nan" if target_yaw is None else "{0:.1f}".format(target_yaw),
                target_yaw_err="nan" if target_yaw_err is None else "{0:.1f}".format(target_yaw_err),
            ),
            flush=True,
        )
    except Exception as exc:
        print("WARNING: TOR fallback body debug failed: {0}".format(exc))


def _apply_vehicle_control_toward_transform(actor, target_xf, speed_mps, precision=False):
    """Follow a scripted target using normal vehicle controls.

    This keeps wheel contact, suspension, and gravity in charge. It is less
    mathematically exact than set_transform/set_target_velocity, but it avoids
    the floating/flying artifact caused by forcing a world-space velocity.
    """
    if not hasattr(actor, "apply_control"):
        return
    cur = actor.get_transform()
    dx = target_xf.location.x - cur.location.x
    dy = target_xf.location.y - cur.location.y
    dist = math.sqrt(dx * dx + dy * dy)
    current_speed = _actor_speed_mps(actor)
    desired_speed = max(0.0, speed_mps)
    if dist > 0.25:
        target_yaw = math.degrees(math.atan2(dy, dx))
    else:
        target_yaw = target_xf.rotation.yaw
    heading_err = _normalize_angle_deg(target_yaw - cur.rotation.yaw)
    path_yaw_err = _normalize_angle_deg(target_xf.rotation.yaw - cur.rotation.yaw)

    # A large diagonal target during a lane change can make low-speed support
    # vehicles visibly point their nose left/right before the body catches up.
    # Keep the steering correction gentle and let the path-yaw term dominate.
    low_speed_target = desired_speed < 4.0 or (precision and current_speed < 14.0)
    heading_limit = 8.0 if low_speed_target else 28.0
    heading_err = max(-heading_limit, min(heading_limit, heading_err))
    steer = heading_err / 60.0 + path_yaw_err / 240.0
    steer_limit = 0.14 if low_speed_target else (0.34 if current_speed < 4.0 else 0.48)
    steer = max(-steer_limit, min(steer_limit, steer))

    speed_err = desired_speed - current_speed
    throttle_base = 0.18 if precision else 0.12
    throttle_gain = 0.22 if precision else 0.16
    # Non-precision ceiling was 0.12+0.58=0.70 throttle, which physically caps
    # heavy blueprints (e.g. audi.etron) near ~78 km/h -- support vehicles in
    # ~96 km/h scenarios could never hold speed and drifted behind ego. Allow
    # full throttle when far below the commanded speed; small-error behaviour
    # (same base/gain) is unchanged.
    throttle_extra = 0.82 if precision else 0.88
    throttle = 0.0 if desired_speed < 0.2 else throttle_base + max(0.0, min(throttle_extra, speed_err * throttle_gain))
    brake = 0.0
    if desired_speed < 0.2:
        brake = 0.35
    elif speed_err < -0.5:
        throttle = 0.0
        brake_cap = 0.75 if precision else 0.55
        brake_gain = 0.16 if precision else 0.12
        brake = max(0.0, min(brake_cap, -speed_err * brake_gain))
    if abs(heading_err) > 65.0:
        throttle = min(throttle, 0.25)
        brake = max(brake, 0.10)

    try:
        actor.apply_control(carla.VehicleControl(
            throttle=throttle,
            steer=steer,
            brake=brake,
            hand_brake=False,
            manual_gear_shift=False,
        ))
        if precision:
            yaw_rate = max(-28.0, min(28.0, path_yaw_err * 3.0))
            actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, yaw_rate))
    except Exception:
        pass


def _vehicle_control_script_target(
    plan,
    lane_paths,
    ego_s,
    t,
    ego_reference_speed_mps=0.0,
    ego_current_speed_mps=0.0,
):
    """Return a near lookahead target for wheel-control scripted driving."""
    desired_xf, desired_s, lane_state = _scripted_target_transform(
        plan, lane_paths, ego_s, t, ego_reference_speed_mps)
    current_path = lane_paths[lane_state.dst_lane if lane_state.blend >= 0.5 else lane_state.src_lane]
    actor_location = plan.actor.get_transform().location
    current_s = current_path.nearest_s(
        actor_location,
        fallback_s=desired_s,
        window_m=140.0,
    )
    dst_path = lane_paths[lane_state.dst_lane]
    dst_s = dst_path.nearest_s(actor_location, fallback_s=current_s, window_m=140.0)
    lane_lateral_error_m = abs(_lane_lateral_error_m(plan.actor, dst_path, dst_s))
    lane_settle_active = (
        bool(plan.cfg.get("lane_change_points", []))
        and (lane_state.blend < 1.0 or lane_lateral_error_m > 0.65)
    )
    plan._last_control_lane_lateral_error_m = lane_lateral_error_m
    plan._last_control_lane_settle_active = lane_settle_active
    plan._last_control_desired_s = desired_s
    plan._last_control_current_s = current_s
    plan._last_control_error_m = desired_s - current_s
    speed_mps = _scripted_command_speed_mps(
        plan,
        t,
        plan.speed_mps,
        ego_reference_speed_mps,
        ego_current_speed_mps,
    )
    if lane_settle_active and speed_mps < 2.8:
        speed_mps = 2.8
    lookahead_m = max(8.0, min(18.0, speed_mps * 0.55 if speed_mps > 0.0 else 12.0))
    control_s = max(current_s + 5.0, min(desired_s, current_s + lookahead_m))
    src = lane_paths[lane_state.src_lane].sample(control_s)
    dst = lane_paths[lane_state.dst_lane].sample(control_s)
    return _lerp_transform(src, dst, lane_state.blend), desired_s, lane_state


def _release_vehicle_brake(actor):
    if not hasattr(actor, "apply_control"):
        return
    try:
        actor.apply_control(carla.VehicleControl(
            throttle=0.0,
            brake=0.0,
            hand_brake=False,
            manual_gear_shift=False,
        ))
    except Exception:
        pass


def _force_release_vehicle_for_run(actor, world=None, ticks=2):
    try:
        if hasattr(actor, "set_autopilot"):
            actor.set_autopilot(False)
        actor.set_simulate_physics(True)
    except Exception:
        pass
    for _ in range(max(1, int(ticks or 1))):
        _release_vehicle_brake(actor)
        try:
            actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        except Exception:
            pass
        if world is None:
            continue
        try:
            settings = world.get_settings()
            if settings.synchronous_mode:
                world.tick()
            else:
                world.wait_for_tick()
        except Exception:
            break
    _release_vehicle_brake(actor)


def _shape_manual_steer(raw_steer, deadzone=0.02, gain=1.0):
    raw_steer = max(-1.0, min(1.0, float(raw_steer or 0.0)))
    deadzone = max(0.0, min(0.5, float(deadzone or 0.0)))
    gain = max(0.05, min(3.0, float(gain or 1.0)))
    magnitude = abs(raw_steer)
    if magnitude <= deadzone:
        return 0.0
    shaped = (magnitude - deadzone) / max(0.001, 1.0 - deadzone)
    return max(-1.0, min(1.0, math.copysign(shaped * gain, raw_steer)))


def _write_g29_visual_steer(args, steer):
    path_text = str(getattr(args, "g29_visual_steer_file", "") or "").strip()
    if not path_text:
        return False
    steer = max(-1.0, min(1.0, float(steer or 0.0)))
    now = time.monotonic()
    last_value = getattr(_write_g29_visual_steer, "_last_value", None)
    last_time = getattr(_write_g29_visual_steer, "_last_time", 0.0)
    if last_value is not None and abs(steer - last_value) < 0.002 and (now - last_time) < 0.25:
        return True
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text("{0:.6f}\n".format(steer), encoding="ascii")
        os.replace(str(tmp_path), str(path))
        _write_g29_visual_steer._last_value = steer
        _write_g29_visual_steer._last_time = now
        return True
    except Exception:
        return False


def write_g29_python_state(path_text, steer=0.0, active=False, return_pressed=False):
    path_text = str(path_text or "").strip()
    if not path_text:
        return False
    steer = max(-1.0, min(1.0, float(steer or 0.0)))
    active = bool(active)
    return_pressed = bool(return_pressed)
    now = time.monotonic()
    wall_now = time.time()
    last_state = getattr(write_g29_python_state, "_last_state", None)
    state = (round(steer, 4), active, return_pressed)
    last_time = getattr(write_g29_python_state, "_last_time", 0.0)
    if last_state == state and (now - last_time) < 0.08:
        return True
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(
            "timestamp_wall_s={0:.6f}\nsteer={1:.6f}\nactive={2}\nreturn={3}\n".format(
                wall_now,
                steer,
                int(active),
                int(return_pressed),
            ),
            encoding="ascii",
        )
        os.replace(str(tmp_path), str(path))
        write_g29_python_state._last_state = state
        write_g29_python_state._last_time = now
        return True
    except Exception:
        return False


def _write_g29_python_state(args, detector=None, pressed=None, active=None, steer=None):
    path_text = str(getattr(args, "g29_python_state_file", "") or "").strip()
    if not path_text:
        return False
    pressed = set(pressed or [])
    if steer is None:
        steer = 0.0
        if detector is not None:
            current_steer = getattr(detector, "current_g29_steer", None)
            if current_steer is not None:
                try:
                    steer = float(current_steer())
                except Exception:
                    steer = 0.0
    threshold = max(0.005, float(getattr(args, "g29_steer_threshold", 0.08) or 0.08))
    if active is None:
        active = ("G29" in pressed) or ("WHEEL" in pressed) or abs(float(steer or 0.0)) >= threshold
    now = time.monotonic()
    if "G29_RETURN" in pressed:
        _write_g29_python_state._return_until = now + 0.50
    return_pressed = now < getattr(_write_g29_python_state, "_return_until", 0.0)
    return write_g29_python_state(path_text, steer=steer, active=active, return_pressed=return_pressed)


def _apply_g29_manual_control(actor, detector, desired_speed_mps, args):
    if detector is None:
        return None
    current_g29_steer = getattr(detector, "current_g29_steer", None)
    if current_g29_steer is None:
        return None

    raw_steer = current_g29_steer()
    steer = _shape_manual_steer(
        raw_steer,
        deadzone=getattr(args, "g29_steer_deadzone", 0.02),
        gain=getattr(args, "g29_steer_gain", 1.0),
    )
    cruise_kmh = float(getattr(args, "g29_cruise_speed_kmh", 0.0) or 0.0)
    target_speed_mps = cruise_kmh / 3.6 if cruise_kmh > 0.0 else max(0.0, float(desired_speed_mps or 0.0))
    current_speed_mps = _actor_speed_mps(actor)
    speed_err = target_speed_mps - current_speed_mps

    throttle = 0.0
    brake = 0.0
    if target_speed_mps < 0.2:
        brake = 0.35
    elif speed_err > 0.25:
        throttle = max(0.12, min(0.65, 0.18 + speed_err * 0.08))
    elif speed_err < -1.0:
        brake = max(0.0, min(0.35, -speed_err * 0.06))
    else:
        throttle = 0.05

    if abs(steer) > 0.45:
        throttle = min(throttle, 0.35)

    try:
        if hasattr(actor, "set_autopilot"):
            actor.set_autopilot(False)
        actor.set_simulate_physics(True)
    except Exception:
        pass
    velocity_control_applied = False
    angular_control_applied = False
    vehicle_control_applied = False
    vehicle_control_available = hasattr(actor, "apply_control")
    try:
        use_velocity_assist = getattr(args, "g29_velocity_assist", False) or not vehicle_control_available
        if use_velocity_assist and hasattr(actor, "set_target_velocity"):
            xf = actor.get_transform()
            forward = xf.rotation.get_forward_vector()
            actor.set_target_velocity(carla.Vector3D(
                forward.x * target_speed_mps,
                forward.y * target_speed_mps,
                forward.z * target_speed_mps,
            ))
            velocity_control_applied = True
        use_yaw_assist = getattr(args, "g29_yaw_assist", False) or not vehicle_control_available
        if use_yaw_assist and hasattr(actor, "set_target_angular_velocity"):
            actor.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, steer * 70.0))
            angular_control_applied = True
    except Exception:
        pass
    if vehicle_control_available:
        try:
            actor.apply_control(carla.VehicleControl(
                throttle=throttle,
                steer=steer,
                brake=brake,
                hand_brake=False,
                reverse=False,
                manual_gear_shift=False,
            ))
            vehicle_control_applied = True
        except Exception:
            pass
    if not (velocity_control_applied or angular_control_applied or vehicle_control_applied):
        return None
    _write_g29_visual_steer(args, steer)
    _write_g29_python_state(args, detector=detector, steer=steer, active=True)
    return {
        "raw_steer": raw_steer,
        "steer": steer,
        "throttle": throttle,
        "brake": brake,
        "target_speed_mps": target_speed_mps,
        "current_speed_mps": current_speed_mps,
        "velocity_control": velocity_control_applied,
        "angular_control": angular_control_applied,
        "vehicle_control": vehicle_control_applied,
    }


def _apply_safe_brake(actor):
    if not hasattr(actor, "apply_control"):
        return
    try:
        actor.apply_control(carla.VehicleControl(
            throttle=0.0,
            brake=1.0,
            hand_brake=True,
            manual_gear_shift=False,
        ))
    except Exception:
        pass


def _freeze_actor_at_transform(actor, xf, world=None, ticks=2, disable_physics=True):
    try:
        if hasattr(actor, "set_autopilot"):
            actor.set_autopilot(False)
    except Exception:
        pass
    _zero_actor_motion(actor)
    _apply_safe_brake(actor)
    if disable_physics:
        try:
            actor.set_simulate_physics(False)
        except Exception:
            pass
    try:
        actor.set_transform(xf)
    except Exception:
        pass
    _zero_actor_motion(actor)
    _apply_safe_brake(actor)
    if disable_physics:
        try:
            actor.set_simulate_physics(False)
        except Exception:
            pass
    else:
        try:
            actor.set_simulate_physics(True)
        except Exception:
            pass
    if world is not None:
        for _ in range(max(0, int(ticks))):
            try:
                world.tick()
            except Exception:
                break


def _prime_dynamic_motion(plans, lane_paths, fallback_speed_kmh):
    fallback_speed_mps = max(0.0, float(fallback_speed_kmh or 0.0)) / 3.6
    for plan in plans:
        if plan.kinematic or plan.actor is None:
            continue
        if plan.control_mode not in ("velocity", "traffic-manager", "velocity-script", "vehicle-control"):
            continue
        speed_mps = plan.speed_mps if plan.speed_mps > 0.0 else fallback_speed_mps
        speed_mps = sample_speed_mps(0.0, speed_mps, plan.cfg.get("speed_points", []))
        if speed_mps <= 0.0:
            continue
        lane_name = getattr(plan, "current_lane", plan.cfg["lane"])
        lane_path = lane_paths.get(lane_name, lane_paths[plan.cfg["lane"]])
        if plan.role == "ego":
            _force_release_vehicle_for_run(plan.actor, world=None, ticks=1)
        else:
            _release_vehicle_brake(plan.actor)
        _set_velocity_follow(plan.actor, lane_path, plan.initial_s, speed_mps)
        print("  primed " + plan.role + " initial velocity="
              + "{0:.1f} km/h".format(speed_mps * 3.6))


def _normalize_runtime_vehicle_speeds(normalized):
    vehicles = normalized.get("vehicles", {})
    ego_cfg = vehicles.get("ego", {})
    ego_speed = float(ego_cfg.get("speed_kmh", normalized.get("ego_speed_kmh", 60.0)) or 0.0)
    if ego_speed <= 0.0:
        ego_speed = float(normalized.get("ego_speed_kmh", 60.0) or 60.0)
    if ego_speed <= 0.0:
        ego_speed = 60.0
    normalized["ego_speed_kmh"] = ego_speed
    if ego_cfg is not None:
        ego_cfg["speed_kmh"] = ego_speed
    for role in ROLE_ORDER[1:]:
        cfg = vehicles.get(role)
        if not cfg or not cfg.get("enabled", False):
            continue
        speed = float(cfg.get("speed_kmh", 0.0) or 0.0)
        if speed <= 0.0:
            cfg["speed_kmh"] = ego_speed
            print("WARNING: " + role + " speed_kmh was <= 0; using ego speed "
                  + "{0:.1f} km/h".format(ego_speed))


def _required_lane_path_distance_m(vehicles_cfg, total_time_s, ego_speed_mps):
    """Build enough HD-map samples for the farthest enabled actor.

    The previous estimate only used ego distance, which is too short when an
    NPC starts far ahead. Once a vehicle reaches the end of its lane samples,
    it appears to stop even though the scenario is still running.
    """
    ego_cfg = vehicles_cfg.get("ego", {}) if vehicles_cfg else {}
    ego_start_s = max(0.0, float(ego_cfg.get("initial_gap_m", 0.0) or 0.0))
    ego_end_s = ego_start_s + max(0.0, ego_speed_mps) * total_time_s
    required = ego_end_s
    for role in ROLE_ORDER:
        cfg = vehicles_cfg.get(role) if vehicles_cfg else None
        if not cfg or not cfg.get("enabled", False):
            continue
        speed_mps = max(0.0, float(cfg.get("speed_kmh", 0.0) or 0.0)) / 3.6
        if speed_mps <= 0.0:
            speed_mps = ego_speed_mps
        initial_gap_m = _cfg_initial_bumper_gap_m(cfg, ego_speed_mps)
        if cfg.get("time_gap_points", []) and not cfg.get("gap_points", []):
            initial_relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
            initial_gap_m = _bumper_gap_to_center_delta_m(
                initial_gap_m,
                DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                initial_relation,
            )
        elif cfg.get("initial_time_gap_s") not in (None, ""):
            initial_relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
            initial_gap_m = _bumper_gap_to_center_delta_m(
                initial_gap_m,
                DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                initial_relation,
            )
        gaps = [initial_gap_m]
        for point in cfg.get("gap_points", []) or []:
            try:
                gaps.append(float(point.get("gap_m", 0.0) or 0.0))
            except Exception:
                pass
        for point in ([] if cfg.get("gap_points", []) else (cfg.get("time_gap_points", []) or [])):
            try:
                bumper_gap_m = float(point.get("time_gap_s", 0.0) or 0.0) * max(0.001, ego_speed_mps)
                relation = _sign_of(bumper_gap_m) or _initial_time_gap_relation_hint(cfg, bumper_gap_m)
                gaps.append(_bumper_gap_to_center_delta_m(
                    bumper_gap_m,
                    DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                    relation,
                ))
            except Exception:
                pass
        max_start_s = max(0.0, max(gaps))
        if role == "ego":
            actor_end_s = max_start_s + speed_mps * total_time_s
        elif cfg.get("gap_points", []):
            actor_end_s = ego_end_s + max_start_s
        elif cfg.get("time_gap_points", []):
            actor_end_s = ego_end_s + max_start_s
        elif cfg.get("speed_points", []):
            actor_end_s = max_start_s + integrate_speed_m(total_time_s, speed_mps, cfg.get("speed_points", []))
        else:
            actor_end_s = max_start_s + speed_mps * total_time_s
        required = max(required, actor_end_s)
    return max(300.0, required + 300.0)


def _required_pedestrian_path_distance_m(pedestrian_configs):
    required = 0.0
    for _role, cfg in sorted(dict(pedestrian_configs or {}).items()):
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        try:
            required = max(required, float(cfg.get("gap_m", 0.0) or 0.0))
        except Exception:
            pass
    return required + 60.0 if required > 0.0 else 0.0


def _required_lane_path_backward_distance_m(vehicles_cfg, ego_speed_mps):
    """Return how far behind the scenario anchor lane samples must extend."""
    min_s = 0.0
    for role in ROLE_ORDER:
        cfg = vehicles_cfg.get(role) if vehicles_cfg else None
        if not cfg or not cfg.get("enabled", False):
            continue
        try:
            initial_gap_m = _cfg_initial_bumper_gap_m(cfg, ego_speed_mps)
            if cfg.get("time_gap_points", []) and not cfg.get("gap_points", []):
                initial_relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
                initial_gap_m = _bumper_gap_to_center_delta_m(
                    initial_gap_m,
                    DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                    initial_relation,
                )
            elif cfg.get("initial_time_gap_s") not in (None, ""):
                initial_relation = _initial_time_gap_relation_hint(cfg, initial_gap_m)
                initial_gap_m = _bumper_gap_to_center_delta_m(
                    initial_gap_m,
                    DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                    initial_relation,
                )
            min_s = min(min_s, initial_gap_m)
        except Exception:
            pass
        for point in cfg.get("gap_points", []) or []:
            try:
                min_s = min(min_s, float(point.get("gap_m", 0.0) or 0.0))
            except Exception:
                pass
        for point in ([] if cfg.get("gap_points", []) else (cfg.get("time_gap_points", []) or [])):
            try:
                bumper_gap_m = (
                    float(point.get("time_gap_s", 0.0) or 0.0)
                    * max(0.001, ego_speed_mps)
                )
                relation = _sign_of(bumper_gap_m) or _initial_time_gap_relation_hint(cfg, bumper_gap_m)
                min_s = min(
                    min_s,
                    _bumper_gap_to_center_delta_m(
                        bumper_gap_m,
                        DEFAULT_TIME_GAP_BUMPER_CLEARANCE_M,
                        relation,
                    ),
                )
            except Exception:
                pass
    if min_s >= 0.0:
        return 0.0
    return abs(min_s) + 80.0


def _support_vehicle_has_scripted_lane_change(vehicles_cfg):
    for role in ROLE_ORDER[1:]:
        cfg = vehicles_cfg.get(role)
        if not cfg or not cfg.get("enabled", False):
            continue
        if cfg.get("lane_change_points", []):
            return True
        if cfg.get("lane_change_enabled", False) and cfg.get("lane_change_target_lane") != cfg.get("lane"):
            return True
    return False


def _actor_is_usable(actor):
    if actor is None:
        return False
    try:
        return bool(getattr(actor, "is_alive", True))
    except Exception:
        return True


def _hidden_playlist_transform(world, index, lane_paths=None):
    # Pooled playlist actors must not be visible or collidable between
    # playlist items. Keep them far above/outside the played area instead of
    # underground; underground parking can render a clipped roof for a frame
    # when UE4 reuses the actor.
    try:
        spawn_points = world.get_map().get_spawn_points()
        if spawn_points:
            base = spawn_points[0]
            return carla.Transform(
                carla.Location(
                    x=base.location.x + 2500.0 + float(index) * 8.0,
                    y=base.location.y + 2500.0,
                    z=base.location.z + 6.0,
                ),
                base.rotation,
            )
    except Exception:
        pass
    return carla.Transform(
        carla.Location(x=2500.0 + float(index) * 8.0, y=2500.0, z=6.0),
        carla.Rotation(pitch=0.0, yaw=0.0, roll=0.0),
    )


def _playlist_actor_pool(args):
    pool = getattr(args, "_playlist_actor_pool", None)
    if pool is None:
        pool = {}
        setattr(args, "_playlist_actor_pool", pool)
    return pool


def _playlist_actor_key(scenario_name, role):
    return (str(scenario_name or ""), str(role or ""))


def _format_playlist_actor_key(key):
    try:
        scenario_name, role = key
    except Exception:
        return str(key)
    return "{0}:{1}".format(scenario_name, role)


def _playlist_owned_actors(args):
    actors = getattr(args, "_playlist_owned_actors", None)
    if actors is None:
        actors = []
        setattr(args, "_playlist_owned_actors", actors)
    return actors


def _playlist_actor_pool_enabled(args):
    return bool(
        getattr(args, "continuous_session", False)
        and getattr(args, "playlist_preload_actors", False)
    )


def _continuous_playlist_has_followup(args):
    if not getattr(args, "continuous_session", False):
        return False
    try:
        index = int(getattr(args, "_playlist_index", 0) or 0)
        count = int(getattr(args, "_playlist_count", 0) or 0)
    except Exception:
        return False
    return count > 0 and index < count - 1


def _park_playlist_actor_pool(args, world, lane_paths=None, plans=None):
    pool = getattr(args, "_playlist_actor_pool", None) or {}
    detached_actor_ids = set()
    for plan in plans or []:
        if getattr(plan, "tor_detached", False):
            actor = getattr(plan, "actor", None)
            if actor is not None:
                detached_actor_ids.add(getattr(actor, "id", None))
    for index, key in enumerate(sorted(pool.keys(), key=lambda item: _format_playlist_actor_key(item))):
        actor = pool.get(key)
        if not _actor_is_usable(actor):
            continue
        if getattr(actor, "id", None) in detached_actor_ids:
            print(
                "Skipping playlist pool park for {0}: actor was detached after TOR/MRM and remains TM-managed.".format(
                    _format_playlist_actor_key(key)
                )
            )
            continue
        try:
            _zero_actor_motion(actor)
            _apply_safe_brake(actor)
            actor.set_simulate_physics(False)
            actor.set_transform(_hidden_playlist_transform(world, index, lane_paths=lane_paths))
        except Exception as exc:
            print("WARNING: could not park playlist actor {0}: {1}".format(
                _format_playlist_actor_key(key), exc))


def _destroy_playlist_actor_pool(args):
    actors = list(getattr(args, "_playlist_owned_actors", []) or [])
    if not actors:
        return
    print("Destroying {0} preloaded playlist actor(s).".format(len(actors)))
    for actor in actors:
        if not _actor_is_usable(actor):
            continue
        try:
            _zero_actor_motion(actor)
            _apply_safe_brake(actor)
            actor.destroy()
        except Exception:
            pass
    setattr(args, "_playlist_owned_actors", [])
    setattr(args, "_playlist_actor_pool", {})


def _preload_playlist_actor_pool(args, world, lane_paths, skip_scenario_name=None):
    if not _playlist_actor_pool_enabled(args):
        return
    specs = getattr(args, "_playlist_actor_pool_specs", None) or {}
    if not specs:
        return
    pool = _playlist_actor_pool(args)
    owned = _playlist_owned_actors(args)
    spawn_lane = lane_paths.get(LANE_RIGHT) or lane_paths.get(LANE_MIDDLE) or lane_paths.get(LANE_LEFT)
    if spawn_lane is None:
        return
    skip_scenario_name = str(skip_scenario_name or "")
    skipped_current = 0
    sorted_items = sorted(specs.items(), key=lambda item: _format_playlist_actor_key(item[0]))
    for index, (key, cfg) in enumerate(sorted_items):
        if key in pool and _actor_is_usable(pool.get(key)):
            continue
        scenario_name, role = key
        if skip_scenario_name and scenario_name == skip_scenario_name:
            # The active scenario spawns these fresh at their scripted t=0
            # transforms and inserts them into the pool itself. Reusing an
            # actor teleported in from the hidden park spot can render
            # body-clipped (roof only) for the first seconds of the run.
            skipped_current += 1
            continue
        try:
            bp = make_blueprint(world, role, cfg.get("blueprint_filter", "vehicle.audi.etron"))
            spawn_s = 20.0 + index * 8.0
            actor = world.try_spawn_actor(bp, _transform_with_lift(spawn_lane.sample(spawn_s), SPAWN_CLEARANCE_Z_OFFSET))
            if actor is None:
                actor = world.try_spawn_actor(bp, _transform_with_lift(spawn_lane.sample(spawn_s + 20.0), SPAWN_CLEARANCE_Z_OFFSET))
            if actor is None:
                print("WARNING: could not preload playlist actor for {0}; it will spawn on demand.".format(
                    _format_playlist_actor_key(key)))
                continue
            actor.set_simulate_physics(False)
            actor.set_transform(_hidden_playlist_transform(world, index, lane_paths=lane_paths))
            pool[key] = actor
            owned.append(actor)
            print("  preloaded playlist actor {0} id={1} blueprint={2}".format(
                _format_playlist_actor_key(key),
                actor.id,
                cfg.get("blueprint_filter", ""),
            ))
        except Exception as exc:
            print("WARNING: could not preload playlist actor {0}: {1}".format(
                _format_playlist_actor_key(key), exc))
    if skipped_current:
        print(
            "Continuous playlist preload: {0} actor(s) of {1} will spawn directly "
            "at their scenario positions instead of the hidden pool.".format(
                skipped_current, skip_scenario_name
            )
        )


def _stabilize_ego_after_run(args, ego_plan, lane_paths, world, total_time_s, ego_speed_mps):
    if ego_plan is None or ego_plan.actor is None:
        return
    if args.post_run_ego == "physics" or args.restore_ego_physics:
        try:
            if hasattr(ego_plan.actor, "set_autopilot"):
                ego_plan.actor.set_autopilot(False)
            _zero_actor_motion(ego_plan.actor)
            _apply_safe_brake(ego_plan.actor)
            ego_plan.actor.set_simulate_physics(True)
        except Exception:
            pass
        print("Post-run ego stabilized with physics enabled.")
        return

    lane_name = ego_plan.cfg.get("lane", LANE_LEFT)
    lane_path = lane_paths[lane_name]
    if args.post_run_ego == "reset":
        stable_s = max(0.0, float(ego_plan.cfg.get("initial_gap_m", 0.0)))
        label = "reset to t=0"
        stable_xf = lane_path.sample(stable_s)
    elif getattr(args, "_tor_hold_used", False):
        # A TOR hold extended the run past the scripted timeline; freeze the
        # ego where it actually is instead of teleporting it to the planned
        # total_time_s position.
        try:
            stable_xf = ego_plan.actor.get_transform()
        except Exception:
            stable_xf = lane_path.sample(
                max(0.0, float(ego_plan.cfg.get("initial_gap_m", 0.0))) + ego_speed_mps * total_time_s
            )
        try:
            stable_s = lane_path.nearest_s(
                stable_xf.location,
                fallback_s=max(0.0, float(ego_plan.cfg.get("initial_gap_m", 0.0))) + ego_speed_mps * total_time_s,
                window_m=600.0,
            )
        except Exception:
            stable_s = 0.0
        label = "frozen at TOR-hold end (current position)"
    else:
        stable_s = max(0.0, float(ego_plan.cfg.get("initial_gap_m", 0.0))) + ego_speed_mps * total_time_s
        label = "frozen at scenario end"
        stable_xf = lane_path.sample(stable_s)
    disable_physics = not ego_plan.actor.type_id.startswith("harplab.dreyevr_vehicle")
    _freeze_actor_at_transform(
        ego_plan.actor, stable_xf, world=world, ticks=2, disable_physics=disable_physics)
    if disable_physics:
        print("Post-run ego " + label + " at s={0:.1f}m with physics disabled and controls stopped.".format(stable_s))
    else:
        print("Post-run ego " + label + " at s={0:.1f}m; world pause holds it stable.".format(stable_s))


def _recover_ego_to_road_autodrive(args, ego_plan, lane_paths, world, traffic_manager, ego_speed_mps):
    if ego_plan is None or ego_plan.actor is None:
        return None
    actor = ego_plan.actor
    preferred_lane = ego_plan.cfg.get("lane", LANE_MIDDLE)
    if preferred_lane not in (LANE_LEFT, LANE_MIDDLE, LANE_RIGHT):
        preferred_lane = LANE_MIDDLE
    if preferred_lane not in lane_paths:
        preferred_lane = LANE_MIDDLE if LANE_MIDDLE in lane_paths else next(iter(lane_paths.keys()))
    lane_path = lane_paths[preferred_lane]
    try:
        loc = actor.get_transform().location
    except Exception:
        loc = lane_path.sample(float(getattr(ego_plan, "initial_s", 0.0) or 0.0)).location
    fallback_s = float(getattr(ego_plan, "_last_control_current_s", None) or getattr(ego_plan, "initial_s", 0.0) or 0.0)
    try:
        nearest_s = lane_path.nearest_s(loc, fallback_s=fallback_s, window_m=600.0)
    except Exception:
        nearest_s = fallback_s
    min_s = _lane_path_min_s(lane_path)
    max_s = _lane_path_max_s(lane_path)
    if max_s > min_s:
        recovery_s = max(min_s + 5.0, min(max_s - 15.0, nearest_s))
    else:
        recovery_s = nearest_s
    recovery_xf = lane_path.sample(recovery_s)
    speed_mps = max(0.0, float(ego_speed_mps or 0.0))
    try:
        if hasattr(actor, "set_autopilot"):
            actor.set_autopilot(False)
        actor.set_simulate_physics(True)
    except Exception:
        pass
    try:
        _apply_transform(actor, recovery_xf, lift_z=DREYEVR_EGO_TELEPORT_Z_OFFSET)
    except Exception:
        pass
    _force_release_vehicle_for_run(actor, world=world, ticks=2)
    _release_vehicle_brake(actor)
    try:
        _set_velocity_follow(actor, lane_path, recovery_s, speed_mps)
    except Exception:
        pass
    try:
        settings = world.get_settings()
        if settings.synchronous_mode:
            world.tick()
    except Exception:
        pass
    if traffic_manager is not None:
        try:
            _apply_async_visual_mode(world, traffic_manager)
        except Exception:
            pass
    return {
        "lane": preferred_lane,
        "s": recovery_s,
        "speed_kmh": speed_mps * 3.6,
    }


def _prepare_takeover_requests(raw_requests):
    requests = []
    for idx, req in enumerate(raw_requests or []):
        try:
            time_s = float(req.get("time_s", 0.0) or 0.0)
            target_speed_mps = max(0.0, float(req.get("target_speed_kmh", 0.0) or 0.0)) / 3.6
            duration_s = max(0.001, float(req.get("decel_duration_s", 2.0) or 2.0))
            timeout_s = max(0.001, float(req.get("timeout_s", 6.0) or 6.0))
            budget_s = req.get("takeover_budget_s", None)
            budget_s = None if budget_s is None else max(0.001, float(budget_s))
            collision_buffer_s = max(0.0, float(req.get("collision_buffer_s", 3.0) or 3.0))
            recovery_after_s = req.get("recovery_after_s", None)
            recovery_after_s = None if recovery_after_s is None else max(0.0, float(recovery_after_s))
            recovery_duration_s = max(0.001, float(req.get("recovery_duration_s", 2.0) or 2.0))
            recover_speed_kmh = req.get("recover_speed_kmh", None)
            recover_speed_mps = None if recover_speed_kmh is None else max(0.0, float(recover_speed_kmh)) / 3.6
        except Exception:
            print("WARNING: ignoring invalid takeover request #{0}".format(idx + 1))
            continue
        collision_time_s = None
        force_brake_time_s = None
        if budget_s is not None:
            collision_time_s = time_s + budget_s
            force_brake_time_s = max(time_s, collision_time_s - min(collision_buffer_s, budget_s))
            end_time_s = force_brake_time_s
        else:
            end_time_s = time_s + timeout_s
        keys = _takeover_request_keys(req)
        requests.append({
            "time_s": time_s,
            "end_time_s": end_time_s,
            "key": _normalize_key_name(req.get("key", "SPACE")),
            "keys": keys,
            "key_label": "/".join(keys),
            "target_speed_mps": target_speed_mps,
            "duration_s": duration_s,
            "message": str(req.get("message", "TAKE OVER REQUEST") or "TAKE OVER REQUEST"),
            "budget_s": budget_s,
            "collision_time_s": collision_time_s,
            "collision_buffer_s": collision_buffer_s,
            "force_brake_time_s": force_brake_time_s,
            "force_brake_on_miss": bool(req.get("force_brake_on_miss", False)),
            "force_brake_active": False,
            "recovery_after_s": recovery_after_s,
            "recovery_duration_s": recovery_duration_s,
            "recover_speed_mps": recover_speed_mps,
            "shown": False,
            "triggered": False,
            "expired": False,
            "trigger_time_s": None,
            "start_speed_mps": None,
            "aoi_confirmed": False,
            "aoi_confirm_time_s": None,
        })
    requests.sort(key=lambda item: item["time_s"])
    return requests


def _apply_aoi_feedback_to_takeover_requests(requests, feedback_receiver, t, marker_outlet=None):
    if not requests or feedback_receiver is None:
        return
    for event, fields in feedback_receiver.drain():
        if event not in ("AOI_FOUND", "TARGET_ACQUIRED", "TAKEOVER_AOI_FOUND", "TOR_READY"):
            continue
        applied = False
        for req in requests:
            if req.get("triggered") or req.get("expired"):
                continue
            if t < req["time_s"] or t > req["end_time_s"]:
                continue
            if not req.get("aoi_confirmed"):
                req["aoi_confirmed"] = True
                req["aoi_confirm_time_s"] = t
                applied = True
        if applied:
            _push_marker(
                marker_outlet,
                "TAKEOVER_AOI_CONFIRMED",
                scenario_t=t,
                feedback_event=event,
                source=fields.get("source", "neon_aoi"),
                aoi_grid=fields.get("aoi_grid"),
                rt_s=fields.get("rt_s"),
            )
            print("[t={0:.2f}s] AOI confirmed for takeover via {1}.".format(t, event))


def _update_takeover_requests(requests, detector, t, current_command_speed_mps, current_actor_speed_mps,
                              marker_outlet=None, require_aoi=True, runtime_args=None):
    if not requests:
        return current_command_speed_mps

    pressed = detector.pressed_edges() if detector is not None else set()
    pressed = _augment_pressed_with_g29_hold(pressed, detector)
    if runtime_args is not None:
        _write_g29_python_state(runtime_args, detector=detector, pressed=pressed)
    for req in requests:
        key_label = req.get("key_label") or req.get("key", "SPACE")
        if not req["shown"] and t >= req["time_s"]:
            req["shown"] = True
            _push_marker(
                marker_outlet,
                "TAKEOVER_REQUEST_SHOWN",
                scenario_t=t,
                key=key_label,
                target_speed_kmh=req["target_speed_mps"] * 3.6,
                budget_s=req.get("budget_s"),
                collision_time_s=req.get("collision_time_s"),
                deadline_s=req.get("end_time_s"),
                require_aoi=bool(require_aoi),
                recovery_after_s=req.get("recovery_after_s"),
                recover_speed_kmh=None if req.get("recover_speed_mps") is None else req.get("recover_speed_mps") * 3.6,
            )
            print("")
            if req.get("budget_s") is not None:
                print(
                    "[t={0:.2f}s] {1}: budget={2:.2f}s, predicted collision t={3:.2f}s, "
                    "press [{4}] before t={5:.2f}s to slow ego to {6:.1f} km/h".format(
                        t,
                        req["message"],
                        req["budget_s"],
                        req["collision_time_s"],
                        key_label,
                        req["end_time_s"],
                        req["target_speed_mps"] * 3.6,
                    )
                )
            else:
                print("[t={0:.2f}s] {1}: press [{2}] to slow ego to {3:.1f} km/h".format(
                    t, req["message"], key_label, req["target_speed_mps"] * 3.6))
            print("")
        if req["triggered"]:
            continue
        pressed_for_req = sorted(set(req.get("keys") or [req.get("key", "SPACE")]).intersection(pressed))
        if req["time_s"] <= t <= req["end_time_s"] and pressed_for_req:
            pressed_key = pressed_for_req[0]
            if require_aoi and not req.get("aoi_confirmed"):
                _push_marker(
                    marker_outlet,
                    "TAKEOVER_KEY_IGNORED",
                    scenario_t=t,
                    key=pressed_key,
                    expected_key=key_label,
                    reason="aoi_not_confirmed",
                    deadline_s=req.get("end_time_s"),
                )
                print("[t={0:.2f}s] TAKEOVER key [{1}] ignored: AOI not confirmed yet.".format(
                    t, pressed_key))
                continue
            req["triggered"] = True
            req["trigger_time_s"] = t
            req["start_speed_mps"] = max(current_actor_speed_mps, current_command_speed_mps)
            _push_marker(
                marker_outlet,
                "TAKEOVER_KEY",
                scenario_t=t,
                key=pressed_key,
                expected_key=key_label,
                target_speed_kmh=req["target_speed_mps"] * 3.6,
                aoi_confirmed=bool(req.get("aoi_confirmed")),
                aoi_confirm_time_s=req.get("aoi_confirm_time_s"),
            )
            print("[t={0:.2f}s] TAKEOVER key [{1}] received; ego target speed {2:.1f} km/h over {3:.1f}s".format(
                t, pressed_key, req["target_speed_mps"] * 3.6, req["duration_s"]))
        elif t > req["end_time_s"] and not req["expired"]:
            req["expired"] = True
            if req.get("force_brake_on_miss", False):
                req["force_brake_active"] = True
                _push_marker(
                    marker_outlet,
                    "TAKEOVER_FORCE_BRAKE",
                    scenario_t=t,
                    deadline_s=req.get("end_time_s"),
                    collision_time_s=req.get("collision_time_s"),
                    aoi_confirmed=bool(req.get("aoi_confirmed")),
                )
                print("[t={0:.2f}s] TAKEOVER deadline missed; forced ego braking active.".format(t))
            else:
                _push_marker(
                    marker_outlet,
                    "TAKEOVER_EXPIRED",
                    scenario_t=t,
                    deadline_s=req.get("end_time_s"),
                    aoi_confirmed=bool(req.get("aoi_confirmed")),
                )
                print("[t={0:.2f}s] TAKEOVER request expired without key press.".format(t))

    speed_mps = current_command_speed_mps
    for req in requests:
        if req.get("force_brake_active", False):
            speed_mps = 0.0
            continue
        if not req["triggered"]:
            continue
        elapsed = max(0.0, t - float(req["trigger_time_s"]))
        start_speed = float(req["start_speed_mps"])
        target_speed = float(req["target_speed_mps"])
        recovery_after_s = req.get("recovery_after_s")
        recover_speed_mps = req.get("recover_speed_mps")
        recovering = False
        if recovery_after_s is not None and recover_speed_mps is not None and elapsed >= recovery_after_s:
            recovering = True
            recovery_elapsed = max(0.0, elapsed - float(recovery_after_s))
            recovery_blend = min(1.0, recovery_elapsed / float(req.get("recovery_duration_s", 2.0)))
            candidate = target_speed + (float(recover_speed_mps) - target_speed) * recovery_blend
        else:
            blend = min(1.0, elapsed / req["duration_s"])
            candidate = start_speed + (target_speed - start_speed) * blend
        if recovering and float(recover_speed_mps) >= target_speed:
            speed_mps = max(speed_mps, candidate)
        elif target_speed <= start_speed:
            speed_mps = min(speed_mps, candidate)
        else:
            speed_mps = max(speed_mps, candidate)
    return max(0.0, speed_mps)


def _takeover_force_brake_active(requests):
    return any(req.get("force_brake_active", False) for req in requests or [])


def _as_float(value, default=0.0):
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _speak_async(text, delay_s=0.0):
    text = str(text or "").strip()
    if not text:
        return

    def worker():
        delay = max(0.0, float(delay_s or 0.0))
        if delay > 0.0:
            time.sleep(delay)
        _speak_now(text)

    if delay_s and float(delay_s) > 0.0:
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        return
    _speak_now(text)


def _speak_now(text):
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "$voice = New-Object -ComObject SAPI.SpVoice; "
        "$voice.Speak([Console]::In.ReadToEnd()) | Out-Null",
    ]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=flags,
        )
        try:
            proc.stdin.write(text)
            proc.stdin.close()
        except Exception:
            pass
    except Exception as exc:
        print("WARNING: voice prompt failed: {0}".format(exc))


def _play_audio_file_async(path_text, label="audio"):
    path_text = str(path_text or "").strip()
    if not path_text:
        return False
    try:
        path = Path(path_text)
        if not path.exists():
            print("WARNING: {0} file not found: {1}".format(label, path))
            return False
        ps = (
            "Add-Type -AssemblyName PresentationCore; "
            "$path=[Console]::In.ReadToEnd().Trim(); "
            "$player=New-Object System.Windows.Media.MediaPlayer; "
            "$player.Open([Uri]::new($path)); "
            "$player.Volume=1.0; "
            "$player.Play(); "
            "$deadline=(Get-Date).AddSeconds(8); "
            "while ((Get-Date) -lt $deadline) { "
            "Start-Sleep -Milliseconds 50; "
            "if ($player.NaturalDuration.HasTimeSpan -and "
            "$player.Position -ge $player.NaturalDuration.TimeSpan) { break } "
            "} "
            "$player.Close();"
        )
        cmd = ["powershell.exe", "-NoProfile", "-Sta", "-Command", ps]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=flags,
        )
        try:
            proc.stdin.write(str(path))
            proc.stdin.close()
        except Exception:
            pass
        return True
    except Exception as exc:
        print("WARNING: {0} playback failed: {1}".format(label, exc))
        return False


def _play_takeover_on_audio(args):
    if bool(getattr(args, "no_takeover_audio", False)):
        return False
    return _play_audio_file_async(
        getattr(args, "takeover_on_audio_file", DEFAULT_TAKEOVER_ON_AUDIO_FILE),
        label="takeover on audio",
    )


def _play_return_auto_off_audio(args):
    if bool(getattr(args, "no_takeover_audio", False)):
        return False
    return _play_audio_file_async(
        getattr(args, "return_auto_off_audio_file", DEFAULT_RETURN_AUTO_OFF_AUDIO_FILE),
        label="return-auto off audio",
    )


def _alarm_async():
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "[Console]::Beep(1200,350); [Console]::Beep(800,350); [Console]::Beep(1200,350)",
    ]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception:
        pass


def _popup_async(title, text):
    title = str(title or "TOR FAIL").strip() or "TOR FAIL"
    text = str(text or "").strip()
    if not text:
        return
    escaped_title = title.replace("'", "''")
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$msg=[Console]::In.ReadToEnd(); "
        "[System.Windows.Forms.MessageBox]::Show($msg, '{0}', 'OK', 'Error') | Out-Null".format(escaped_title),
    ]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=flags,
        )
        try:
            proc.stdin.write(text)
            proc.stdin.close()
        except Exception:
            pass
    except Exception as exc:
        print("WARNING: popup prompt failed: {0}".format(exc))


def _popup_blocking(title, text, icon="Warning"):
    title = str(title or "Scenario alert").strip() or "Scenario alert"
    text = str(text or "").strip()
    if not text:
        return False
    escaped_title = title.replace("'", "''")
    icon_text = str(icon or "Warning").strip().lower()
    icon_flag = 0x30 if icon_text in ("warning", "exclamation") else 0x10
    # OK + icon + task-modal + topmost + set foreground.
    # Calling user32 from this Python process is more reliable than spawning a
    # hidden PowerShell child while the runner is managed by the session GUI.
    message_type = 0x0 | icon_flag | 0x2000 | 0x40000 | 0x10000
    if os.name == "nt":
        try:
            import ctypes
            rc = ctypes.windll.user32.MessageBoxW(None, text, title, int(message_type))
            return bool(rc)
        except Exception as exc:
            print("WARNING: native blocking popup failed, falling back to PowerShell: {0}".format(exc))
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Sta",
        "-Command",
        "Add-Type -TypeDefinition @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public class NativeMessageBox {\n"
        "  [DllImport(\"user32.dll\", CharSet=CharSet.Unicode)]\n"
        "  public static extern int MessageBox(IntPtr hWnd, string text, string caption, int type);\n"
        "}\n"
        "'@; "
        "$msg=[Console]::In.ReadToEnd(); "
        "[NativeMessageBox]::MessageBox([IntPtr]::Zero, $msg, '{0}', {1}) | Out-Null".format(
            escaped_title,
            int(message_type),
        ),
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            err = str(proc.stderr or proc.stdout or "").strip()
            print("WARNING: PowerShell popup failed rc={0}: {1}".format(proc.returncode, err))
            return False
        return True
    except Exception as exc:
        print("WARNING: blocking popup prompt failed: {0}".format(exc))
        return False


def _format_collision_recovery_popup(scenario_name, collision_info):
    info = collision_info if isinstance(collision_info, dict) else {}
    scenario_name = str(scenario_name or "scenario")
    other = info.get("other_type_id") or "unknown actor"
    count = info.get("count")
    scenario_t = info.get("scenario_t")
    impulse = info.get("impulse")
    lines = [
        "Collision detected in scenario: {0}".format(scenario_name),
        "",
        "Click OK to clean this scene and return the ego vehicle to on-road autonomous driving.",
        "The next playlist item will start from its fixed scenario anchor instead of the current crashed ego position.",
        "",
        "Other actor: {0}".format(other),
    ]
    if count is not None:
        lines.append("Collision count: {0}".format(count))
    if scenario_t is not None:
        try:
            lines.append("Scenario time: {0:.2f}s".format(float(scenario_t)))
        except Exception:
            lines.append("Scenario time: {0}".format(scenario_t))
    if impulse is not None:
        try:
            lines.append("Impulse: {0:.1f}".format(float(impulse)))
        except Exception:
            lines.append("Impulse: {0}".format(impulse))
    return "\n".join(lines)


def _collision_confirm_payload(scenario_name, collision_info):
    info = collision_info if isinstance(collision_info, dict) else {}
    payload = {
        "scenario_name": str(scenario_name or "scenario"),
        "scenario_t": info.get("scenario_t"),
        "collision_count": info.get("count"),
        "other_actor_id": info.get("other_actor_id"),
        "other_type_id": info.get("other_type_id"),
        "impulse": info.get("impulse"),
    }
    return payload


def _wait_for_collision_confirm_stdin(scenario_name, collision_info):
    payload = _collision_confirm_payload(scenario_name, collision_info)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        encoded = "{}"
    print(COLLISION_CONFIRM_PREFIX + encoded, flush=True)
    print("COLLISION_CONFIRM_STDIN_WAIT waiting_for_collision_confirmed=1", flush=True)
    accepted = set([
        "collision_confirmed",
        "collision_continue",
        "continue",
        "ok",
        "start",
    ])
    while True:
        try:
            line = sys.stdin.readline()
        except Exception as exc:
            print("WARNING: collision confirmation stdin read failed: {0}".format(exc), flush=True)
            return False
        if line == "":
            print("WARNING: collision confirmation stdin closed; continuing recovery.", flush=True)
            return False
        command = str(line or "").strip().lower()
        if not command:
            continue
        if command in accepted:
            print("COLLISION_CONFIRM_ACK command={0}".format(command), flush=True)
            return True
        print("Ignoring unknown collision confirmation command: {0}".format(command), flush=True)


def _collision_recovery_enabled(args):
    if not bool(getattr(args, "playlist_collision_recovery", True)):
        return False
    return True


def _response_action_for_key(key, ego_speed_kmh):
    key = _normalize_key_name(key)
    ego_speed_kmh = max(0.0, float(ego_speed_kmh or 0.0))
    if key == "UP":
        return {
            "type": "accelerate",
            "target_speed_kmh": ego_speed_kmh + 15.0,
            "duration_s": 1.5,
            "recovery_after_s": 5.0,
            "recover_speed_kmh": ego_speed_kmh,
            "recovery_duration_s": 2.0,
        }
    if key == "DOWN":
        return {
            "type": "brake",
            "target_speed_kmh": 0.0,
            "duration_s": 1.5,
            "recovery_after_s": 5.0,
            "recover_speed_kmh": ego_speed_kmh,
            "recovery_duration_s": 2.0,
        }
    if key == "LEFT":
        return {"type": "lane_change_left", "duration_s": 1.0}
    if key == "RIGHT":
        return {"type": "lane_change_right", "duration_s": 1.0}
    return {"type": "keep_lane", "duration_s": 0.0}


def _normalize_tor_action(action, response_key, ego_speed_kmh):
    if isinstance(action, dict):
        out = dict(action)
    else:
        out = {"type": str(action or "keep_lane")}
    action_type = str(out.get("type") or out.get("action") or "keep_lane").strip().lower()
    if action_type == "response_action":
        out = _response_action_for_key(response_key, ego_speed_kmh)
        action_type = out["type"]
    elif action_type == "accelerate":
        base = _response_action_for_key("UP", ego_speed_kmh)
        base.update(out)
        out = base
    elif action_type == "brake":
        base = _response_action_for_key("DOWN", ego_speed_kmh)
        base.update(out)
        out = base
    elif action_type in ("left", "lane_left"):
        action_type = "lane_change_left"
        out["type"] = action_type
    elif action_type in ("right", "lane_right"):
        action_type = "lane_change_right"
        out["type"] = action_type
    elif action_type in (
        "brake_pull_over",
        "brake_then_pull_over",
        "brake_and_pull_over",
        "pull_over",
        "roadside_stop",
        "pull_over_right",
    ):
        base = {
            "type": "brake_pull_over",
            "pull_over_speed_kmh": 15.0,
            "brake_duration_s": 1.5,
            "lane_change_duration_s": 4.0,
            "final_brake_duration_s": 2.5,
            "target_lane": LANE_OUTER_RIGHT,
        }
        base.update(out)
        out = base
        action_type = "brake_pull_over"
    else:
        out["type"] = action_type
    out["type"] = action_type
    return out


def _adjacent_lane(current_lane, direction):
    lanes = [LANE_LEFT, LANE_MIDDLE, LANE_RIGHT]
    try:
        idx = lanes.index(current_lane)
    except ValueError:
        idx = 1
    if direction == "left":
        idx = max(0, idx - 1)
    elif direction == "right":
        idx = min(len(lanes) - 1, idx + 1)
    return lanes[idx]


def _resolve_pull_over_target_lane(target_lane, lane_context):
    raw = str(target_lane or LANE_OUTER_RIGHT).strip()
    lowered = raw.lower().replace("-", "_").replace(" ", "_")
    rightmost_lane = lane_context.get("rightmost_lane", LANE_RIGHT)
    if lowered in (
        "",
        "outerright",
        "outer_right",
        "rightmost",
        "far_right",
        "fourth",
        "lane_4",
        "4",
        "roadside",
        "shoulder",
        "curb",
        "pull_over",
    ):
        return rightmost_lane
    if lowered in ("right", "lane_right"):
        return raw if raw == LANE_RIGHT else LANE_RIGHT
    if lowered in ("middle", "center", "lane_middle"):
        return LANE_MIDDLE
    if lowered in ("left", "lane_left"):
        return LANE_LEFT
    if raw in (LANE_LEFT, LANE_MIDDLE, LANE_RIGHT, LANE_OUTER_RIGHT, LANE_QUEEN_FOURTH):
        if raw == LANE_OUTER_RIGHT:
            return rightmost_lane
        return raw
    return rightmost_lane


def _as_float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _actor_location(plan):
    """Live world location of a plan's actor, or None."""
    actor = getattr(plan, "actor", None) if plan is not None else None
    if actor is None:
        return None
    try:
        return actor.get_transform().location
    except Exception:
        return None


def _actor_speed_magnitude_mps(plan):
    actor = getattr(plan, "actor", None) if plan is not None else None
    if actor is None:
        return None
    try:
        v = actor.get_velocity()
        return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    except Exception:
        return None


def _resolve_trigger_target_plan(target_role, ego_plan, plan_by_role):
    """Plan whose gap/TTC to the ego drives a gap/ttc TOR trigger.

    An explicit role (e.g. 'vehicle_1') is used directly; 'lead'/unknown falls
    back to the nearest non-ego vehicle to the ego.
    """
    plan_by_role = plan_by_role or {}
    if target_role and target_role not in ("", "lead", "nearest") and target_role in plan_by_role:
        return plan_by_role.get(target_role)
    ego_loc = _actor_location(ego_plan)
    if ego_loc is None:
        return None
    best, best_d = None, None
    for role, plan in plan_by_role.items():
        if role == "ego" or plan is None or getattr(plan, "actor", None) is None:
            continue
        loc = _actor_location(plan)
        if loc is None:
            continue
        try:
            d = ego_loc.distance(loc)
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = plan, d
    return best


def _eval_gap_ttc_trigger(window, ego_plan, plan_by_role):
    """Decide whether a gap/ttc TOR window should fire now.

    Returns (fired: bool, metric_value: float|None). Only meaningful for
    trigger_mode in {'gap','ttc'}; 'time' windows return (False, None) so the
    existing time-based path is untouched.
    """
    mode = (window.get("trigger_mode") or "time").lower()
    if mode not in ("gap", "ttc"):
        return False, None
    target = _resolve_trigger_target_plan(window.get("trigger_target_role"), ego_plan, plan_by_role)
    ego_loc = _actor_location(ego_plan)
    tgt_loc = _actor_location(target)
    if ego_loc is None or tgt_loc is None:
        return False, None
    try:
        dist = float(ego_loc.distance(tgt_loc))
    except Exception:
        return False, None
    if mode == "gap":
        thr = _as_float_or_none(window.get("trigger_gap_m"))
        if thr is None:
            return False, None
        return (dist <= thr), dist
    # ttc: distance / closing speed (ego faster than target => approaching)
    thr = _as_float_or_none(window.get("trigger_ttc_s"))
    if thr is None:
        return False, None
    ego_speed = _actor_speed_magnitude_mps(ego_plan)
    tgt_speed = _actor_speed_magnitude_mps(target)
    if ego_speed is None or tgt_speed is None:
        return False, None
    closing = ego_speed - tgt_speed
    if closing <= 0.05:  # not approaching -> no finite TTC
        return False, None
    ttc = dist / closing
    return (ttc <= thr), ttc


def _prepare_experiment_tor_windows(experiment_events, takeover_requests, tor_alerts, total_time_s, ego_speed_kmh):
    events = experiment_events if isinstance(experiment_events, dict) else {}
    raw_windows = list(events.get("tor_windows", []) or [])
    if not raw_windows:
        for idx, req in enumerate(takeover_requests or []):
            onset = _as_float(req.get("time_s"), 0.0)
            timeout = _as_float(req.get("timeout_s"), 6.0)
            raw_windows.append({
                "name": "legacy_takeover_{0}".format(idx + 1),
                "alert_onset_s": onset,
                "tor_deadline_s": onset + max(0.001, timeout),
                "failure_action": {
                    "type": "brake",
                    "target_speed_kmh": req.get("target_speed_kmh", 0.0),
                    "duration_s": req.get("decel_duration_s", 2.0),
                    "recovery_after_s": req.get("recovery_after_s", 5.0),
                    "recover_speed_kmh": req.get("recover_speed_kmh", ego_speed_kmh),
                    "recovery_duration_s": req.get("recovery_duration_s", 2.0),
                },
                "notification_text": req.get("message", ""),
                "source": "takeover_requests",
            })
    if not raw_windows:
        for idx, alert in enumerate(tor_alerts or []):
            onset = _as_float(alert.get("time_s"), 0.0)
            raw_windows.append({
                "name": "tor_alert_{0}".format(idx + 1),
                "alert_onset_s": onset,
                "tor_deadline_s": min(float(total_time_s), onset + 6.0),
                "failure_action": "brake",
                "notification_text": "",
                "source": "tor_alerts",
            })

    windows = []
    for idx, raw in enumerate(raw_windows):
        try:
            onset = max(0.0, _as_float(raw.get("alert_onset_s", raw.get("tor_onset_s", raw.get("time_s"))), 0.0))
            deadline = _as_float(raw.get("tor_deadline_s", raw.get("deadline_s")), onset + 6.0)
            deadline = max(onset + 0.001, deadline)
        except Exception:
            print("WARNING: ignoring invalid experiment TOR window #{0}".format(idx + 1))
            continue
        keys = _normalize_key_list(raw.get("manual_keys"), default=["W", "A", "S", "D"])
        fields = dict(raw.get("fields", {}) or {})
        fields.update(adaptive_fields_from_mapping(fields))
        fields.update(adaptive_fields_from_mapping(raw))
        if raw.get("stage2_direction") not in (None, "") and not fields.get("stage2_direction"):
            fields["stage2_direction"] = raw.get("stage2_direction")
        if raw.get("stage2_speech_template") not in (None, "") and not fields.get("stage2_speech_template"):
            fields["stage2_speech_template"] = raw.get("stage2_speech_template")
        if raw.get("stage2_text") not in (None, "") and not fields.get("stage2_text"):
            fields["stage2_text"] = raw.get("stage2_text")
        fields.setdefault("stage2_direction", "front")
        fields.setdefault("stage2_speech_template", "Look {direction}.")
        fields.setdefault("stage_speech_min_gap_s", raw.get("stage_speech_min_gap_s", 0.5))
        fields.setdefault("stage1_requirement", "gaze_screen")
        fields.setdefault("stage1_delay_s", 0.0)
        fields.setdefault("stage2_requirement", "target_fixation")
        fields.setdefault("stage2_delay_s", 0.0)
        fields.setdefault("target_fixation_s", raw.get("target_fixation_s", 0.5))
        fields.setdefault("manual_requires", "none")
        ready_text = str(raw.get("ready_text") or "Please take over.").strip()
        if ready_text.lower() == "hazard ahead. please take over.":
            ready_text = "Please take over."
        windows.append({
            "name": str(raw.get("name") or "tor_{0}".format(idx + 1)),
            "onset_s": onset,
            "deadline_s": deadline,
            "keys": keys,
            "failure_action": raw.get("failure_action", "brake"),
            "notification_text": str(raw.get("notification_text") or ""),
            "alert_text": str(raw.get("alert_text") or ""),
            "ready_text": ready_text,
            "success_text": str(raw.get("success_text") or "Manual take-over successful."),
            "failure_text": str(raw.get("failure_text") or "Take-over missed. The vehicle is taking avoidance action."),
            "collision_fail_text": str(raw.get("collision_fail_text") or "Accident detected after manual take-over. TOR failed. Please restart the scenario."),
            "return_to_auto_text": str(raw.get("return_to_auto_text") or "Please press space to return to autonomous driving."),
            "manual_safe_seconds": max(0.0, _as_float(raw.get("manual_safe_seconds"), 4.0)),
            "trigger_mode": str(raw.get("trigger_mode", "time") or "time").lower(),
            "trigger_gap_m": _as_float_or_none(raw.get("trigger_gap_m")),
            "trigger_ttc_s": _as_float_or_none(raw.get("trigger_ttc_s")),
            "trigger_target_role": str(raw.get("trigger_target_role", "lead") or "lead"),
            "fields": fields,
            "shown": False,
            "ended": False,
            "outcome": None,
            "manual_active": False,
            "manual_key": None,
            "manual_start_s": None,
            "manual_collision_count": 0,
            "manual_collision_failed": False,
            "return_requested": False,
            "auto_resumed": False,
            "fallback_active": False,
            "aoi_confirmed": False,
            "aoi_confirm_time_s": None,
            "ready_notified": False,
            "last_dynamic_aoi_s": None,
            "stage2_visual_active": False,
            "stage2_visual_started_at": None,
            "stage2_speech_at": None,
            "stage_speech_min_gap_s": max(0.0, _as_float(fields.get("stage_speech_min_gap_s"), 0.5)),
            "target_fixation_started_at": None,
            "target_fixation_required_s": max(0.001, _as_float(fields.get("target_fixation_s"), 0.5)),
            "target_fixation_confirmed": False,
            "visual_target_role": None,
            "visual_target_mode": None,
            "visual_target_mirror": None,
            "visual_target_shape": None,
            "visual_target_screen": None,
            "visual_target_x": None,
            "visual_target_y": None,
            "visual_target_radius": None,
            "visual_target_width": None,
            "visual_target_height": None,
            "late_keys": set(),
        })
    windows.sort(key=lambda item: item["onset_s"])
    return windows


def _build_experiment_scheduled_events(experiment_events):
    events = []
    cfg = experiment_events if isinstance(experiment_events, dict) else {}
    for idx, hazard in enumerate(cfg.get("hazards", []) or []):
        try:
            onset = max(0.0, _as_float(hazard.get("onset_s"), 0.0))
        except Exception:
            continue
        fields = dict(hazard.get("fields", {}) or {})
        fields.update({
            "name": hazard.get("name", "hazard"),
        })
        events.append({
            "time_s": onset,
            "event": "HAZARD_ONSET",
            "key": "experiment_hazard_onset:{0}".format(idx),
            **fields,
        })
        end_s = hazard.get("end_s")
        if end_s not in (None, ""):
            events.append({
                "time_s": max(onset, _as_float(end_s, onset)),
                "event": "HAZARD_END",
                "key": "experiment_hazard_end:{0}".format(idx),
                **fields,
            })
    for idx, marker in enumerate(cfg.get("custom_markers", []) or []):
        try:
            t = max(0.0, _as_float(marker.get("time_s"), 0.0))
        except Exception:
            continue
        event = str(marker.get("event") or "").strip()
        if not event:
            continue
        fields = dict(marker.get("fields", {}) or {})
        events.append({
            "time_s": t,
            "event": event,
            "key": "experiment_custom:{0}:{1}".format(idx, event),
            **fields,
        })
    events.sort(key=lambda item: item["time_s"])
    return events


TOR_VISUAL_FEEDBACK_EVENTS = set([
    "ALERT_STAGE2_ONSET",
    "TARGET_FIXATION_START",
    "TARGET_FIXATION_BREAK",
    "TARGET_FIXATION_CONFIRMED",
])


def _feedback_window_matches(window, fields, t):
    window_name = str(fields.get("window") or "").strip()
    if window_name and window_name != str(window.get("name") or ""):
        return False
    if window.get("ended"):
        return False
    return float(window["onset_s"]) <= float(t) <= float(window["deadline_s"]) + 1.0


def _remember_tor_visual_target(window, fields):
    role = fields.get("target_role") or fields.get("role")
    if role not in (None, ""):
        window["visual_target_role"] = str(role)
    for src, dst in (
        ("target_mode", "visual_target_mode"),
        ("mirror_view", "visual_target_mirror"),
        ("target_shape", "visual_target_shape"),
        ("target_screen", "visual_target_screen"),
        ("target_x", "visual_target_x"),
        ("target_y", "visual_target_y"),
        ("target_radius", "visual_target_radius"),
        ("target_width", "visual_target_width"),
        ("target_height", "visual_target_height"),
    ):
        value = fields.get(src)
        if value not in (None, ""):
            window[dst] = value


def _apply_tor_visual_feedback(window, event, fields, t):
    _remember_tor_visual_target(window, fields)
    window["stage2_visual_active"] = True
    if window.get("stage2_visual_started_at") is None:
        window["stage2_visual_started_at"] = t
    required_s = fields.get("required_fixation_s") or fields.get("fixation_s")
    if required_s not in (None, ""):
        window["target_fixation_required_s"] = max(0.001, _as_float(required_s, 0.5))
    if event == "ALERT_STAGE2_ONSET":
        window["stage2_speech_at"] = t
        stage2_speech_enabled = str(fields.get("stage2_speech_enabled", "true")).strip().lower()
        window["stage2_speech_enabled"] = stage2_speech_enabled not in ("0", "false", "no", "off")
        window["stage_speech_min_gap_s"] = max(
            0.0,
            _as_float(fields.get("stage_speech_min_gap_s"), _as_float(window.get("stage_speech_min_gap_s"), 0.5)),
        )
        window["target_fixation_started_at"] = None
        window["target_fixation_confirmed"] = False
    elif event == "TARGET_FIXATION_START":
        if window.get("stage2_speech_at") is None:
            window["stage2_speech_at"] = t
        window["target_fixation_started_at"] = t
        window["target_fixation_confirmed"] = False
    elif event == "TARGET_FIXATION_BREAK":
        window["target_fixation_started_at"] = None
        window["target_fixation_confirmed"] = False
    elif event == "TARGET_FIXATION_CONFIRMED":
        required = max(0.001, _as_float(window.get("target_fixation_required_s"), 0.5))
        if window.get("target_fixation_started_at") is None:
            window["target_fixation_started_at"] = max(float(window["onset_s"]), float(t) - required)
        window["target_fixation_confirmed"] = True


def _visual_feedback_marker_fields(window, fields, t):
    marker_fields = dict(fields or {})
    marker_fields.pop("event", None)
    marker_fields["scenario_t"] = t
    marker_fields.setdefault("window", window.get("name"))
    marker_fields.setdefault("target_role", window.get("visual_target_role"))
    marker_fields.setdefault("target_mode", window.get("visual_target_mode"))
    marker_fields.setdefault("mirror_view", window.get("visual_target_mirror"))
    marker_fields.setdefault("target_shape", window.get("visual_target_shape"))
    marker_fields.setdefault("target_screen", window.get("visual_target_screen"))
    marker_fields.setdefault("target_x", window.get("visual_target_x"))
    marker_fields.setdefault("target_y", window.get("visual_target_y"))
    marker_fields.setdefault("target_radius", window.get("visual_target_radius"))
    marker_fields.setdefault("target_width", window.get("visual_target_width"))
    marker_fields.setdefault("target_height", window.get("visual_target_height"))
    marker_fields.setdefault("required_fixation_s", window.get("target_fixation_required_s"))
    return marker_fields


def _apply_aoi_feedback_to_experiment_tor_windows(windows, feedback_receiver, t, marker_outlet=None):
    if not windows or feedback_receiver is None:
        return
    for event, fields in feedback_receiver.drain():
        if event in TOR_VISUAL_FEEDBACK_EVENTS:
            for window in windows:
                if _feedback_window_matches(window, fields, t):
                    _apply_tor_visual_feedback(window, event, fields, t)
                    if marker_outlet is not None:
                        marker_outlet.push_visual(
                            event,
                            **_visual_feedback_marker_fields(window, fields, t)
                        )
            continue
        if event not in ("AOI_FOUND", "TARGET_ACQUIRED", "TAKEOVER_AOI_FOUND", "TOR_READY"):
            continue
        applied = False
        for window in windows:
            if not _feedback_window_matches(window, fields, t):
                continue
            if event == "TOR_READY":
                _apply_tor_visual_feedback(window, "TARGET_FIXATION_CONFIRMED", fields, t)
                if marker_outlet is not None:
                    marker_outlet.push_visual(
                        "TOR_READY",
                        **_visual_feedback_marker_fields(window, fields, t)
                    )
            if not window.get("aoi_confirmed"):
                window["aoi_confirmed"] = True
                window["aoi_confirm_time_s"] = t
                applied = True
                if event == "TOR_READY" and not window.get("ready_notified"):
                    window["ready_notified"] = True
                    ready_text = str(window.get("ready_text") or "")
                    _push_marker(
                        marker_outlet,
                        "TOR_FINAL_REQUEST",
                        scenario_t=t,
                        window=window.get("name"),
                        source=fields.get("source", "neon_aoi"),
                        screen=fields.get("screen"),
                        aoi_x=fields.get("aoi_x"),
                        aoi_y=fields.get("aoi_y"),
                        target_screen=fields.get("target_screen"),
                        target_x=fields.get("target_x"),
                        target_y=fields.get("target_y"),
                        target_radius=fields.get("target_radius"),
                        target_shape=fields.get("target_shape"),
                        target_width=fields.get("target_width"),
                        target_height=fields.get("target_height"),
                    )
                    if ready_text:
                        speech_delay_s = 0.0
                        stage2_speech_at = window.get("stage2_speech_at")
                        if stage2_speech_at is not None and window.get("stage2_speech_enabled", True):
                            min_gap_s = max(0.0, _as_float(window.get("stage_speech_min_gap_s"), 0.5))
                            speech_delay_s = max(0.0, float(stage2_speech_at) + min_gap_s - float(t))
                        _push_marker(
                            marker_outlet,
                            "TOR_NOTIFICATION",
                            scenario_t=t,
                            window=window.get("name"),
                            outcome="tor_ready",
                            text=ready_text,
                            speech_delay_s=speech_delay_s,
                        )
                        _speak_async(ready_text, delay_s=speech_delay_s)
        if applied:
            _push_marker(
                marker_outlet,
                "TAKEOVER_AOI_CONFIRMED",
                scenario_t=t,
                feedback_event=event,
                source=fields.get("source", "neon_aoi"),
                aoi_grid=fields.get("aoi_grid") or fields.get("target_grid"),
                rt_s=fields.get("rt_s"),
            )


def _tor_visual_test_screen(value, default="center"):
    text = str(value or "").strip().lower()
    if text == "middle":
        text = "center"
    if text in ("left", "center", "right"):
        return text
    return default


def _tor_visual_test_fields(window, args, ego_plan, plan_by_role, t):
    fields = dict(window.get("fields", {}) or {})
    role = str(
        fields.get("target_role")
        or fields.get("dynamic_aoi_role")
        or fields.get("hazard_role")
        or "vehicle_1"
    )
    target = _dynamic_vehicle_aoi_target(window, ego_plan, plan_by_role, t)
    if target is None:
        direction = str(fields.get("stage2_direction") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if direction in ("left_rear", "right_rear", "rear", "center_rear", "back", "behind"):
            side = str(getattr(args, "tor_visual_test_mirror", "") or "").strip().lower()
            if side not in ("left", "center", "right"):
                side = _rear_mirror_side(fields, 0.0)
            target = _rear_mirror_aoi_target(fields, side, role, None, None, None, None)
        else:
            target = {
                "role": role,
                "target_mode": "manual_screen",
                "target_screen": "center",
                "target_x": 0.5,
                "target_y": 0.55,
                "target_radius": 0.20,
            }
    marker_fields = {
        "event": "ALERT_STAGE2_ONSET",
        "source": "tor_visual_test",
        "window": window.get("name"),
        "stage2_speech_enabled": "false",
        "stage_speech_min_gap_s": window.get("stage_speech_min_gap_s"),
        "required_fixation_s": window.get("target_fixation_required_s"),
    }
    marker_fields.update(target or {})

    forced_mode = str(getattr(args, "tor_visual_test_mode", "") or "").strip()
    if forced_mode:
        marker_fields["target_mode"] = forced_mode
    forced_mirror = str(getattr(args, "tor_visual_test_mirror", "") or "").strip().lower()
    if forced_mirror:
        marker_fields["mirror_view"] = forced_mirror
        if str(marker_fields.get("target_mode") or "").strip().lower() == "rear_mirror":
            defaults = REAR_MIRROR_AOI_DEFAULTS.get(forced_mirror)
            if defaults:
                marker_fields.setdefault("target_screen", defaults["target_screen"])
                marker_fields.setdefault("target_x", defaults["target_x"])
                marker_fields.setdefault("target_y", defaults["target_y"])
                marker_fields.setdefault("target_radius", defaults["target_radius"])
    forced_screen = str(getattr(args, "tor_visual_test_screen", "") or "").strip()
    if forced_screen:
        marker_fields["target_screen"] = _tor_visual_test_screen(forced_screen)
    forced_shape = str(getattr(args, "tor_visual_test_shape", "") or "").strip().lower()
    if forced_shape:
        marker_fields["target_shape"] = "rect" if forced_shape in ("rect", "rectangle", "box", "square") else "circle"
    for arg_name, field_name in (
        ("tor_visual_test_x", "target_x"),
        ("tor_visual_test_y", "target_y"),
        ("tor_visual_test_radius", "target_radius"),
        ("tor_visual_test_width", "target_width"),
        ("tor_visual_test_height", "target_height"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            marker_fields[field_name] = value
    if marker_fields.get("target_screen") in (None, ""):
        marker_fields["target_screen"] = "center"
    if marker_fields.get("target_x") in (None, ""):
        marker_fields["target_x"] = 0.5
    if marker_fields.get("target_y") in (None, ""):
        marker_fields["target_y"] = 0.55
    if marker_fields.get("target_radius") in (None, ""):
        marker_fields["target_radius"] = 0.20
    return marker_fields


def _apply_tor_visual_test_if_needed(window, t, marker_outlet, runtime_args, ego_plan, plan_by_role):
    if runtime_args is None or not bool(getattr(runtime_args, "tor_visual_test", False)):
        return
    if window.get("ended") or window.get("tor_visual_test_applied"):
        return
    delay_s = max(0.0, _as_float(getattr(runtime_args, "tor_visual_test_delay_s", 0.0), 0.0))
    if float(t) < float(window.get("onset_s", 0.0)) + delay_s:
        return
    fields = _tor_visual_test_fields(window, runtime_args, ego_plan, plan_by_role, t)
    _apply_tor_visual_feedback(window, "ALERT_STAGE2_ONSET", fields, t)
    window["tor_visual_test_applied"] = True
    if marker_outlet is not None:
        marker_outlet.push_visual(
            "ALERT_STAGE2_ONSET",
            **_visual_feedback_marker_fields(window, fields, t)
        )
    _push_marker(
        marker_outlet,
        "TOR_VISUAL_TEST_STAGE2",
        scenario_t=t,
        window=window.get("name"),
        target_mode=window.get("visual_target_mode"),
        mirror_view=window.get("visual_target_mirror"),
        target_screen=window.get("visual_target_screen"),
        target_x=window.get("visual_target_x"),
        target_y=window.get("visual_target_y"),
        target_radius=window.get("visual_target_radius"),
        target_shape=window.get("visual_target_shape"),
    )
    print(
        "[t={0:.2f}s] TOR visual test: forced stage-2 target mode={1} mirror={2} "
        "screen={3} x={4} y={5} radius={6}.".format(
            t,
            window.get("visual_target_mode"),
            window.get("visual_target_mirror"),
            window.get("visual_target_screen"),
            window.get("visual_target_x"),
            window.get("visual_target_y"),
            window.get("visual_target_radius"),
        )
    )


def _start_experiment_action(action, response_key, outcome, window, t, current_speed_mps,
                             ego_speed_mps, lane_context, marker_outlet=None):
    normalized = _normalize_tor_action(action, response_key, ego_speed_mps * 3.6)
    action_type = str(normalized.get("type") or "keep_lane")
    state = {
        "type": action_type,
        "window": window.get("name"),
        "outcome": outcome,
        "response_key": response_key,
        "started_at": t,
        "duration_s": max(0.0, _as_float(normalized.get("duration_s"), 0.0)),
        "return_to_auto_text": str(window.get("return_to_auto_text") or ""),
        "return_key": str(window.get("return_key") or "SPACE"),
        "return_key_label": str(window.get("return_key_label") or window.get("return_key") or "SPACE"),
        "end_emitted": False,
        "complete": False,
    }
    if action_type in ("accelerate", "brake"):
        target_kmh = _as_float(normalized.get("target_speed_kmh"), current_speed_mps * 3.6)
        state.update({
            "start_speed_mps": max(0.0, current_speed_mps),
            "target_speed_mps": max(0.0, target_kmh / 3.6),
            "recovery_after_s": None if normalized.get("recovery_after_s") in (None, "") else max(0.0, _as_float(normalized.get("recovery_after_s"), 0.0)),
            "recover_speed_mps": None if normalized.get("recover_speed_kmh") in (None, "") else max(0.0, _as_float(normalized.get("recover_speed_kmh"), ego_speed_mps * 3.6) / 3.6),
            "recovery_duration_s": max(0.001, _as_float(normalized.get("recovery_duration_s"), 2.0)),
        })
    elif action_type in ("lane_change_left", "lane_change_right"):
        direction = "left" if action_type == "lane_change_left" else "right"
        from_lane = lane_context.get("lane", LANE_MIDDLE)
        to_lane = _adjacent_lane(from_lane, direction)
        state.update({
            "from_lane": from_lane,
            "to_lane": to_lane,
            "direction": direction,
            "duration_s": max(0.001, _as_float(normalized.get("duration_s"), 1.0)),
        })
    elif action_type == "brake_pull_over":
        from_lane = lane_context.get("lane", LANE_MIDDLE)
        target_lane = _resolve_pull_over_target_lane(
            normalized.get("target_lane"),
            lane_context,
        )
        brake_duration_s = max(0.001, _as_float(normalized.get("brake_duration_s"), 1.5))
        lane_change_duration_s = max(0.001, _as_float(normalized.get("lane_change_duration_s"), 2.5))
        final_brake_duration_s = max(0.001, _as_float(normalized.get("final_brake_duration_s"), 1.5))
        current_speed = max(0.0, current_speed_mps)
        pull_over_speed = max(0.0, _as_float(normalized.get("pull_over_speed_kmh"), 15.0) / 3.6)
        if current_speed > 0.0:
            pull_over_speed = min(current_speed, pull_over_speed)
        state.update({
            "from_lane": from_lane,
            "to_lane": target_lane,
            "target_lane": target_lane,
            "start_speed_mps": current_speed,
            "pull_over_speed_mps": pull_over_speed,
            "target_speed_mps": 0.0,
            "brake_duration_s": brake_duration_s,
            "lane_change_duration_s": lane_change_duration_s,
            "final_brake_duration_s": final_brake_duration_s,
            "duration_s": brake_duration_s + lane_change_duration_s + final_brake_duration_s,
            "post_stop_wait_s": max(0.0, _as_float(normalized.get("post_stop_wait_s"), 5.0)),
            "return_lane": _resolve_pull_over_target_lane(
                normalized.get("return_lane", LANE_RIGHT),
                {"rightmost_lane": lane_context.get("rightmost_lane", LANE_RIGHT)},
            ),
            "return_speed_mps": max(
                0.0,
                _as_float(normalized.get("return_speed_kmh"), ego_speed_mps * 3.6) / 3.6,
            ),
            "return_lane_change_duration_s": max(
                0.001,
                _as_float(normalized.get("return_lane_change_duration_s"), 4.0),
            ),
        })
    _push_marker(
        marker_outlet,
        "AVOIDANCE_ACTION_START",
        scenario_t=t,
        window=window.get("name"),
        outcome=outcome,
        response=response_key,
        action=action_type,
        duration_s=state.get("duration_s"),
        from_lane=state.get("from_lane"),
        to_lane=state.get("to_lane"),
        target_speed_kmh=None if state.get("target_speed_mps") is None else state.get("target_speed_mps") * 3.6,
        pull_over_speed_kmh=None if state.get("pull_over_speed_mps") is None else state.get("pull_over_speed_mps") * 3.6,
    )
    print("[t={0:.2f}s] avoidance action start: {1} ({2})".format(t, action_type, outcome))
    return state


REAR_MIRROR_AOI_DEFAULTS = {
    "center": {"target_screen": "center", "target_x": 0.50, "target_y": 0.24, "target_radius": 0.16},
    "left": {"target_screen": "left", "target_x": 0.76, "target_y": 0.62, "target_radius": 0.16},
    "right": {"target_screen": "right", "target_x": 0.24, "target_y": 0.62, "target_radius": 0.16},
}


MANUAL_AOI_OVERRIDE_DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "extensions"
    / "level3_tor_demo"
    / "manual_aoi_override.json"
)
_MANUAL_AOI_OVERRIDE_CACHE = {"path": None, "mtime": None, "data": None}


def _as_bool(value, default=False):
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "enabled"):
        return True
    if text in ("0", "false", "no", "n", "off", "disabled"):
        return False
    return bool(default)


def _first_field(fields, *names):
    for name in names:
        value = fields.get(name)
        if value not in (None, ""):
            return value
    return None


def _manual_aoi_override_path(fields):
    raw = _first_field(fields, "manual_aoi_override_file", "manual_aoi_file", "screen_aoi_override_file")
    if not raw:
        raw = os.environ.get("CARLA_YJC_MANUAL_AOI_FILE", "")
    if raw:
        path = Path(str(raw))
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return path
    return MANUAL_AOI_OVERRIDE_DEFAULT_PATH


def _load_manual_aoi_override(fields):
    path = _manual_aoi_override_path(fields)
    try:
        stat = path.stat()
    except Exception:
        return None
    cache = _MANUAL_AOI_OVERRIDE_CACHE
    if cache.get("path") == str(path) and cache.get("mtime") == stat.st_mtime:
        return cache.get("data")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        data = None
    cache["path"] = str(path)
    cache["mtime"] = stat.st_mtime
    cache["data"] = data
    return data


def _manual_screen_aoi_target(fields, role):
    override = _load_manual_aoi_override(fields)
    if not isinstance(override, dict):
        return None
    mode = str(override.get("mode") or "").strip().lower()
    enabled = _as_bool(override.get("enabled"), False)
    if not enabled or mode not in ("manual_screen", "manual", "screen"):
        return None
    screen = str(override.get("screen") or "center").strip().lower()
    if screen == "middle":
        screen = "center"
    if screen not in ("left", "center", "right"):
        screen = "center"
    x = max(0.0, min(1.0, _as_float(override.get("x"), 0.5)))
    y = max(0.0, min(1.0, _as_float(override.get("y"), 0.5)))
    width = max(0.02, min(1.0, _as_float(override.get("width"), 0.20)))
    height = max(0.02, min(1.0, _as_float(override.get("height"), 0.20)))
    radius = max(0.02, min(0.80, _as_float(override.get("radius"), max(width, height) * 0.5)))
    return {
        "role": str(override.get("role") or role or "manual_aoi"),
        "target_mode": "manual_screen",
        "target_shape": "rect",
        "target_screen": screen,
        "target_x": x,
        "target_y": y,
        "target_width": width,
        "target_height": height,
        "target_radius": radius,
    }


def _manual_screen_aoi_active(fields):
    return _manual_screen_aoi_target(fields, "manual_aoi") is not None


def _sample_target_time_gap_s(target_plan, ego_plan, t):
    if target_plan is None or ego_plan is None or t is None:
        return None
    cfg = getattr(target_plan, "cfg", {}) or {}
    if cfg.get("initial_time_gap_s") in (None, "") and not cfg.get("time_gap_points", []):
        return None
    ego_cfg = getattr(ego_plan, "cfg", {}) or {}
    ego_reference_speed_mps = max(
        0.001,
        _as_float(ego_cfg.get("speed_kmh"), 0.0) / 3.6,
        float(getattr(ego_plan, "speed_mps", 0.0) or 0.0),
    )
    try:
        return sample_time_gap_s(
            t,
            getattr(target_plan, "initial_s", 0.0),
            cfg.get("time_gap_points", []) or [],
            ego_reference_speed_mps,
            initial_bumper_gap_m=_time_gap_initial_bumper_gap_m(target_plan),
        )
    except Exception:
        return None


def _rear_mirror_aoi_enabled(fields):
    raw = _first_field(fields, "rear_mirror_aoi_enabled", "rear_aoi_when_behind", "mirror_aoi_when_behind")
    if raw not in (None, ""):
        return _as_bool(raw, False)
    direction = str(fields.get("stage2_direction") or "").strip().lower().replace("-", "_").replace(" ", "_")
    return direction in ("left_rear", "right_rear", "rear", "center_rear", "back", "behind")


def _rear_mirror_side(fields, lateral_m):
    raw = str(
        _first_field(fields, "rear_mirror_side", "rear_aoi_side", "mirror_side")
        or fields.get("stage2_direction")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "left": "left",
        "left_rear": "left",
        "left_back": "left",
        "leftbehind": "left",
        "right": "right",
        "right_rear": "right",
        "right_back": "right",
        "rightbehind": "right",
        "center": "center",
        "middle": "center",
        "rear": "center",
        "center_rear": "center",
        "back": "center",
        "behind": "center",
    }
    if raw in aliases:
        return aliases[raw]
    if lateral_m < -0.75:
        return "left"
    if lateral_m > 0.75:
        return "right"
    return "center"


def _rear_mirror_aoi_target(fields, side, role, time_gap_s, longitudinal_m, lateral_m, distance_m):
    side = side if side in REAR_MIRROR_AOI_DEFAULTS else "center"
    defaults = REAR_MIRROR_AOI_DEFAULTS[side]
    screen = str(_first_field(
        fields,
        "{0}_rear_mirror_screen".format(side),
        "rear_mirror_{0}_screen".format(side),
        "{0}_mirror_screen".format(side),
    ) or defaults["target_screen"]).strip().lower()
    if screen == "middle":
        screen = "center"
    if screen not in ("left", "center", "right"):
        screen = defaults["target_screen"]
    x = max(0.0, min(1.0, _as_float(_first_field(
        fields,
        "{0}_rear_mirror_x".format(side),
        "rear_mirror_{0}_x".format(side),
        "{0}_mirror_x".format(side),
    ), defaults["target_x"])))
    y = max(0.0, min(1.0, _as_float(_first_field(
        fields,
        "{0}_rear_mirror_y".format(side),
        "rear_mirror_{0}_y".format(side),
        "{0}_mirror_y".format(side),
    ), defaults["target_y"])))
    radius = max(0.03, min(0.45, _as_float(_first_field(
        fields,
        "{0}_rear_mirror_radius".format(side),
        "rear_mirror_{0}_radius".format(side),
        "{0}_mirror_radius".format(side),
        "rear_mirror_radius",
    ), defaults["target_radius"])))
    return {
        "role": role,
        "target_mode": "rear_mirror",
        "mirror_view": side,
        "target_screen": screen,
        "target_x": x,
        "target_y": y,
        "target_radius": radius,
        "time_gap_s": time_gap_s,
        "longitudinal_m": longitudinal_m,
        "lateral_m": lateral_m,
        "distance_m": distance_m,
    }


def _dynamic_vehicle_aoi_target(window, ego_plan, plan_by_role, t=None):
    fields = dict(window.get("fields", {}) or {})
    role = str(
        fields.get("target_role")
        or fields.get("dynamic_aoi_role")
        or fields.get("hazard_role")
        or "vehicle_1"
    )
    manual_target = _manual_screen_aoi_target(fields, role)
    if manual_target is not None:
        return manual_target
    target_plan = (plan_by_role or {}).get(role)
    if ego_plan is None or target_plan is None:
        return None
    if getattr(ego_plan, "actor", None) is None or getattr(target_plan, "actor", None) is None:
        return None
    try:
        ego_xf = ego_plan.actor.get_transform()
        target_xf = target_plan.actor.get_transform()
        ego_loc = ego_xf.location
        target_loc = target_xf.location
        dx = float(target_loc.x - ego_loc.x)
        dy = float(target_loc.y - ego_loc.y)
        dz = float(target_loc.z - ego_loc.z)
        yaw = math.radians(float(ego_xf.rotation.yaw))
        fwd_x = math.cos(yaw)
        fwd_y = math.sin(yaw)
        right_x = -math.sin(yaw)
        right_y = math.cos(yaw)
        longitudinal_m = dx * fwd_x + dy * fwd_y
        lateral_m = dx * right_x + dy * right_y
        distance_m = math.sqrt(dx * dx + dy * dy + dz * dz)
        time_gap_s = _sample_target_time_gap_s(target_plan, ego_plan, t)
        rear_threshold_s = _as_float(fields.get("rear_mirror_until_time_gap_s"), 0.0)
        if _rear_mirror_aoi_enabled(fields):
            rear_by_time_gap = time_gap_s is not None and time_gap_s < rear_threshold_s
            rear_by_actual_pose = time_gap_s is None and longitudinal_m <= -2.0
            if rear_by_time_gap or rear_by_actual_pose:
                side = _rear_mirror_side(fields, lateral_m)
                return _rear_mirror_aoi_target(
                    fields,
                    side,
                    role,
                    time_gap_s,
                    longitudinal_m,
                    lateral_m,
                    distance_m,
                )
        if longitudinal_m <= -2.0:
            return None
        bearing_deg = math.degrees(math.atan2(lateral_m, max(0.5, longitudinal_m)))
        full_fov_deg = max(45.0, _as_float(fields.get("dynamic_full_fov_deg"), 135.0))
        total_x = max(0.0, min(1.0, 0.5 + bearing_deg / full_fov_deg))
        if total_x < (1.0 / 3.0):
            screen = "left"
            x_norm = total_x * 3.0
        elif total_x < (2.0 / 3.0):
            screen = "center"
            x_norm = (total_x - (1.0 / 3.0)) * 3.0
        else:
            screen = "right"
            x_norm = (total_x - (2.0 / 3.0)) * 3.0
        y_norm = 0.55
        multiplier = max(0.25, _as_float(fields.get("dynamic_radius_multiplier"), 2.0))
        base_radius = max(0.035, min(0.16, 4.0 / max(5.0, distance_m)))
        radius = max(0.06, min(0.35, base_radius * multiplier))
        return {
            "role": role,
            "target_screen": screen,
            "target_x": x_norm,
            "target_y": y_norm,
            "target_radius": radius,
            "target_mode": "vehicle_contour",
            "radius_multiplier": multiplier,
            "time_gap_s": time_gap_s,
            "longitudinal_m": longitudinal_m,
            "lateral_m": lateral_m,
            "distance_m": distance_m,
            "bearing_deg": bearing_deg,
        }
    except Exception:
        return None


def _emit_dynamic_tor_aoi(window, t, ego_plan, plan_by_role, marker_outlet=None, force=False):
    if window.get("ended"):
        return
    fields = dict(window.get("fields", {}) or {})
    interval_s = max(0.05, _as_float(fields.get("dynamic_aoi_update_s"), 0.20))
    last_t = window.get("last_dynamic_aoi_s")
    if not force and last_t is not None and (t - float(last_t)) < interval_s:
        return
    target = _dynamic_vehicle_aoi_target(window, ego_plan, plan_by_role, t=t)
    if not target:
        return
    prev_mode = window.get("visual_target_mode")
    prev_mirror = window.get("visual_target_mirror")
    window["last_dynamic_aoi_s"] = t
    window["visual_target_role"] = target.get("role")
    window["visual_target_mode"] = target.get("target_mode")
    window["visual_target_mirror"] = target.get("mirror_view")
    window["visual_target_shape"] = target.get("target_shape")
    window["visual_target_screen"] = target.get("target_screen")
    window["visual_target_x"] = target.get("target_x")
    window["visual_target_y"] = target.get("target_y")
    window["visual_target_radius"] = target.get("target_radius")
    window["visual_target_width"] = target.get("target_width")
    window["visual_target_height"] = target.get("target_height")
    _push_marker(
        marker_outlet,
        "TOR_DYNAMIC_AOI",
        scenario_t=t,
        window=window.get("name"),
        stage2_direction=fields.get("stage2_direction"),
        stage2_speech_template=fields.get("stage2_speech_template"),
        stage2_text=fields.get("stage2_text"),
        stage_speech_min_gap_s=fields.get("stage_speech_min_gap_s"),
        **target,
    )
    next_mode = window.get("visual_target_mode")
    next_mirror = window.get("visual_target_mirror")
    if prev_mode != next_mode or prev_mirror != next_mirror:
        _push_marker(
            marker_outlet,
            "TOR_DYNAMIC_AOI_MODE_CHANGE",
            scenario_t=t,
            window=window.get("name"),
            role=target.get("role"),
            from_mode=prev_mode,
            to_mode=next_mode,
            from_mirror=prev_mirror,
            to_mirror=next_mirror,
            time_gap_s=target.get("time_gap_s"),
            longitudinal_m=target.get("longitudinal_m"),
            lateral_m=target.get("lateral_m"),
            distance_m=target.get("distance_m"),
        )


def _tor_visual_target_role(window):
    role = window.get("visual_target_role")
    if role:
        return str(role)
    fields = dict(window.get("fields", {}) or {})
    return str(
        fields.get("target_role")
        or fields.get("dynamic_aoi_role")
        or fields.get("hazard_role")
        or "vehicle_1"
    )


def _tor_visual_progress(window, t):
    if window.get("target_fixation_confirmed"):
        return 1.0
    started_at = window.get("target_fixation_started_at")
    if started_at is None:
        return 0.0
    required_s = max(0.001, _as_float(window.get("target_fixation_required_s"), 0.5))
    return max(0.0, min(1.0, (float(t) - float(started_at)) / required_s))


def _debug_red_color():
    try:
        return carla.Color(255, 0, 0, 255)
    except TypeError:
        return carla.Color(255, 0, 0)


def _draw_debug_vehicle_aoi(world, ego_actor, target_actor, progress, confirmed):
    if world is None or target_actor is None:
        return
    try:
        target_xf = target_actor.get_transform()
        target_loc = target_xf.location
        extent = getattr(getattr(target_actor, "bounding_box", None), "extent", None)
        ex = float(getattr(extent, "x", 2.2) or 2.2)
        ey = float(getattr(extent, "y", 1.0) or 1.0)
        ez = float(getattr(extent, "z", 0.8) or 0.8)
        center = carla.Location(
            x=float(target_loc.x),
            y=float(target_loc.y),
            z=float(target_loc.z) + max(1.15, ez + 0.65),
        )
        if ego_actor is not None:
            ego_loc = ego_actor.get_transform().location
            nx = float(ego_loc.x - center.x)
            ny = float(ego_loc.y - center.y)
            norm = math.sqrt(nx * nx + ny * ny)
        else:
            yaw = math.radians(float(target_xf.rotation.yaw))
            nx = math.cos(yaw)
            ny = math.sin(yaw)
            norm = 1.0
        if norm < 0.001:
            yaw = math.radians(float(target_xf.rotation.yaw))
            nx = math.cos(yaw)
            ny = math.sin(yaw)
            norm = 1.0
        nx /= norm
        ny /= norm
        right_x = -ny
        right_y = nx
        red = _debug_red_color()
        life_time = 0.20
        if confirmed or progress >= 0.995:
            world.debug.draw_point(center, size=0.24, color=red, life_time=life_time, persistent_lines=False)
            return
        shrink = max(0.08, 1.0 - max(0.0, min(1.0, float(progress or 0.0))))
        horizontal_radius = max(1.8, ex * 1.20, ey * 2.2) * shrink
        vertical_radius = max(1.15, ez * 2.1) * shrink
        if shrink <= 0.14:
            world.debug.draw_point(center, size=0.18, color=red, life_time=life_time, persistent_lines=False)
        segments = 40
        prev = None
        for idx in range(segments + 1):
            angle = (2.0 * math.pi * idx) / float(segments)
            c = math.cos(angle)
            s = math.sin(angle)
            point = carla.Location(
                x=center.x + right_x * horizontal_radius * c,
                y=center.y + right_y * horizontal_radius * c,
                z=center.z + vertical_radius * s,
            )
            if prev is not None:
                world.debug.draw_line(
                    prev,
                    point,
                    thickness=0.06,
                    color=red,
                    life_time=life_time,
                    persistent_lines=False,
                )
            prev = point
        world.debug.draw_point(center, size=0.07 + 0.12 * float(progress or 0.0), color=red, life_time=life_time, persistent_lines=False)
    except Exception:
        pass


REAR_MIRROR_DEBUG_DEFAULTS = {
    "center": {
        "forward_m": 1.05,
        "right_m": 0.00,
        "up_m": 1.82,
        "radius_x_m": 0.50,
        "radius_z_m": 0.24,
        "inward_m": 0.00,
    },
    "left": {
        "forward_m": 1.70,
        "right_m": -1.34,
        "up_m": 1.32,
        "radius_x_m": 0.56,
        "radius_z_m": 0.38,
        "inward_m": 0.08,
    },
    "right": {
        "forward_m": 1.70,
        "right_m": 1.34,
        "up_m": 1.32,
        "radius_x_m": 0.56,
        "radius_z_m": 0.38,
        "inward_m": 0.08,
    },
}


def _mirror_debug_value(fields, side, key, default):
    value = _first_field(
        fields,
        "{0}_rear_mirror_debug_{1}".format(side, key),
        "rear_mirror_{0}_debug_{1}".format(side, key),
        "{0}_mirror_debug_{1}".format(side, key),
        "rear_mirror_debug_{1}".format(key),
    )
    return _as_float(value, default)


def _draw_debug_rear_mirror_aoi(world, ego_actor, window, progress, confirmed):
    if world is None or ego_actor is None:
        return
    try:
        fields = dict(window.get("fields", {}) or {})
        side = str(window.get("visual_target_mirror") or fields.get("rear_mirror_side") or "center")
        side = side.strip().lower().replace("-", "_").replace(" ", "_")
        if side in ("middle", "rear", "center_rear", "back", "behind"):
            side = "center"
        elif side in ("left_rear", "left_back", "leftbehind"):
            side = "left"
        elif side in ("right_rear", "right_back", "rightbehind"):
            side = "right"
        if side not in REAR_MIRROR_DEBUG_DEFAULTS:
            side = "center"
        defaults = REAR_MIRROR_DEBUG_DEFAULTS[side]
        forward_m = _mirror_debug_value(fields, side, "forward_m", defaults["forward_m"])
        right_m = _mirror_debug_value(fields, side, "right_m", defaults["right_m"])
        up_m = _mirror_debug_value(fields, side, "up_m", defaults["up_m"])
        legacy_radius = _mirror_debug_value(fields, side, "radius_m", 0.0)
        radius_x = max(0.04, _mirror_debug_value(
            fields,
            side,
            "radius_x_m",
            legacy_radius if legacy_radius > 0.0 else defaults["radius_x_m"],
        ))
        radius_z = max(0.04, _mirror_debug_value(
            fields,
            side,
            "radius_z_m",
            legacy_radius if legacy_radius > 0.0 else defaults["radius_z_m"],
        ))
        inward_m = _mirror_debug_value(fields, side, "inward_m", defaults["inward_m"])

        ego_xf = ego_actor.get_transform()
        ego_loc = ego_xf.location
        yaw = math.radians(float(ego_xf.rotation.yaw))
        fwd_x = math.cos(yaw)
        fwd_y = math.sin(yaw)
        right_x = -math.sin(yaw)
        right_y = math.cos(yaw)
        if side == "left":
            right_m += abs(inward_m)
        elif side == "right":
            right_m -= abs(inward_m)
        center = carla.Location(
            x=float(ego_loc.x) + fwd_x * forward_m + right_x * right_m,
            y=float(ego_loc.y) + fwd_y * forward_m + right_y * right_m,
            z=float(ego_loc.z) + up_m,
        )

        if confirmed or bool(window.get("target_fixation_confirmed")):
            return
        red = _debug_red_color()
        life_time = 0.20
        if side in ("left", "right"):
            h_axis_x, h_axis_y = fwd_x, fwd_y
        else:
            h_axis_x, h_axis_y = right_x, right_y
        corners = [
            carla.Location(
                x=center.x - h_axis_x * radius_x,
                y=center.y - h_axis_y * radius_x,
                z=center.z - radius_z,
            ),
            carla.Location(
                x=center.x + h_axis_x * radius_x,
                y=center.y + h_axis_y * radius_x,
                z=center.z - radius_z,
            ),
            carla.Location(
                x=center.x + h_axis_x * radius_x,
                y=center.y + h_axis_y * radius_x,
                z=center.z + radius_z,
            ),
            carla.Location(
                x=center.x - h_axis_x * radius_x,
                y=center.y - h_axis_y * radius_x,
                z=center.z + radius_z,
            ),
        ]
        for idx in range(4):
            world.debug.draw_line(
                corners[idx],
                corners[(idx + 1) % 4],
                thickness=0.10,
                color=red,
                life_time=life_time,
                persistent_lines=False,
            )
    except Exception:
        pass


_TOR_SPOTLIGHT_LAST_TEXT = None
_TOR_SPOTLIGHT_LAST_WRITE_WALL = 0.0


def _write_tor_spotlight_file(args, active, window=None, progress=0.0, t=0.0, force=False):
    global _TOR_SPOTLIGHT_LAST_TEXT, _TOR_SPOTLIGHT_LAST_WRITE_WALL
    if args is None or bool(getattr(args, "no_tor_spotlight_file", False)):
        return
    path_text = str(getattr(args, "tor_spotlight_file", "") or "").strip()
    if not path_text:
        return
    now_wall = time.monotonic()
    if active and window is not None:
        screen = str(window.get("visual_target_screen") or "center").strip().lower()
        target_mode = str(window.get("visual_target_mode") or "").strip().lower()
        mirror_view = str(window.get("visual_target_mirror") or "").strip().lower()
        x = _as_float(window.get("visual_target_x"), 0.5)
        y = _as_float(window.get("visual_target_y"), 0.55)
        radius = _as_float(window.get("visual_target_radius"), 0.20)
        shape = str(window.get("visual_target_shape") or "circle").strip().lower()
        width = _as_float(window.get("visual_target_width"), radius * 2.0)
        height = _as_float(window.get("visual_target_height"), radius * 2.0)
        extra = ""
        if target_mode:
            extra += ";mode={0}".format(target_mode)
        if mirror_view:
            extra += ";mirror={0}".format(mirror_view)
        text = (
            "active=1;screen={screen};x={x:.4f};y={y:.4f};radius={radius:.4f};"
            "shape={shape};width={width:.4f};height={height:.4f};"
            "progress={progress:.4f};alpha=0.16;scenario_t={t:.4f}{extra};"
            "expires_at={expires_at:.3f}"
        ).format(
            screen=screen,
            x=max(0.0, min(1.0, x)),
            y=max(0.0, min(1.0, y)),
            radius=max(0.01, min(0.80, radius)),
            shape="rect" if shape in ("rect", "rectangle", "box", "square") else "circle",
            width=max(0.01, min(1.0, width)),
            height=max(0.01, min(1.0, height)),
            progress=max(0.0, min(1.0, float(progress or 0.0))),
            t=float(t or 0.0),
            extra=extra,
            expires_at=time.time() + 2.5,
        )
    else:
        text = "active=0;scenario_t={0:.4f}".format(float(t or 0.0))
    if (
        not force
        and text == _TOR_SPOTLIGHT_LAST_TEXT
        and now_wall - _TOR_SPOTLIGHT_LAST_WRITE_WALL < 0.05
    ):
        return
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="ascii")
        _TOR_SPOTLIGHT_LAST_TEXT = text
        _TOR_SPOTLIGHT_LAST_WRITE_WALL = now_wall
    except Exception:
        pass


def _mirror_hud_target_from_spec(spec, defaults, radius_override=None):
    text = str(spec or "").strip()
    screen = str(defaults.get("target_screen") or "center")
    x = _as_float(defaults.get("target_x"), 0.5)
    y = _as_float(defaults.get("target_y"), 0.55)
    radius = _as_float(defaults.get("target_radius"), 0.16)
    if text:
        parts = [part.strip() for part in text.split(",")]
        if len(parts) >= 4:
            screen = _tor_visual_test_screen(parts[0], screen)
            x = _as_float(parts[1], x)
            y = _as_float(parts[2], y)
            radius = _as_float(parts[3], radius)
    if radius_override is not None:
        radius = _as_float(radius_override, radius)
    return "{screen},{x:.4f},{y:.4f},{radius:.4f}".format(
        screen=_tor_visual_test_screen(screen),
        x=max(0.0, min(1.0, x)),
        y=max(0.0, min(1.0, y)),
        radius=max(0.01, min(0.80, radius)),
    )


def _write_tor_mirror_hud_file(args, t=0.0, force=False):
    global _TOR_SPOTLIGHT_LAST_TEXT, _TOR_SPOTLIGHT_LAST_WRITE_WALL
    if args is None or bool(getattr(args, "no_tor_spotlight_file", False)):
        return
    path_text = str(getattr(args, "tor_spotlight_file", "") or "").strip()
    if not path_text:
        return
    radius_override = getattr(args, "tor_mirror_hud_radius", None)
    targets = [
        _mirror_hud_target_from_spec(
            getattr(args, "tor_mirror_hud_left", ""),
            REAR_MIRROR_AOI_DEFAULTS["left"],
            radius_override,
        ),
        _mirror_hud_target_from_spec(
            getattr(args, "tor_mirror_hud_rear", ""),
            REAR_MIRROR_AOI_DEFAULTS["center"],
            radius_override,
        ),
        _mirror_hud_target_from_spec(
            getattr(args, "tor_mirror_hud_right", ""),
            REAR_MIRROR_AOI_DEFAULTS["right"],
            radius_override,
        ),
    ]
    text = (
        "active=1;dim=0;shape=circle;targets={targets};progress=0.0000;"
        "alpha=0.0000;scenario_t={t:.4f};expires_at={expires_at:.3f}"
    ).format(
        targets="|".join(targets),
        t=float(t or 0.0),
        expires_at=time.time() + 2.5,
    )
    now_wall = time.monotonic()
    if (
        not force
        and text == _TOR_SPOTLIGHT_LAST_TEXT
        and now_wall - _TOR_SPOTLIGHT_LAST_WRITE_WALL < 0.05
    ):
        return
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="ascii")
        _TOR_SPOTLIGHT_LAST_TEXT = text
        _TOR_SPOTLIGHT_LAST_WRITE_WALL = now_wall
    except Exception:
        pass


def _draw_tor_vehicle_aoi_visuals(world, windows, ego_plan, plan_by_role, t, args=None):
    if args is not None and bool(getattr(args, "tor_mirror_hud_test", False)):
        _write_tor_mirror_hud_file(args, t=t)
        return
    if not windows or not plan_by_role:
        _write_tor_spotlight_file(args, False, t=t)
        return
    ego_actor = getattr(ego_plan, "actor", None)
    spotlight_active = False
    for window in windows:
        if window.get("ended") or not window.get("stage2_visual_active"):
            continue
        progress = _tor_visual_progress(window, t)
        role = _tor_visual_target_role(window)
        if str(window.get("visual_target_mode") or "").strip().lower() == "rear_mirror":
            _write_tor_spotlight_file(args, True, window=window, progress=progress, t=t)
            spotlight_active = True
            _draw_debug_rear_mirror_aoi(
                world,
                ego_actor,
                window,
                progress,
                bool(window.get("target_fixation_confirmed")),
            )
            continue
        _write_tor_spotlight_file(args, True, window=window, progress=progress, t=t)
        spotlight_active = True
        target_plan = plan_by_role.get(role)
        target_actor = getattr(target_plan, "actor", None) if target_plan is not None else None
        if target_actor is None:
            continue
        _draw_debug_vehicle_aoi(
            world,
            ego_actor,
            target_actor,
            progress,
            bool(window.get("target_fixation_confirmed")),
        )
    if not spotlight_active:
        _write_tor_spotlight_file(args, False, t=t)


def _finish_experiment_tor_window(window, outcome, reason, response_key, t, current_speed_mps,
                                  ego_speed_mps, lane_context, marker_outlet=None):
    window["ended"] = True
    window["outcome"] = outcome
    window["response_key"] = response_key
    rt_s = None if response_key is None else max(0.0, t - float(window["onset_s"]))
    _push_marker(
        marker_outlet,
        "TOR_WINDOW_END",
        scenario_t=t,
        window=window.get("name"),
        outcome=outcome,
        reason=reason,
        response=response_key,
        rt_s=rt_s,
        onset_s=window.get("onset_s"),
        deadline_s=window.get("deadline_s"),
    )
    marker = "TOR_SUCCESS" if outcome == "success" else "TOR_FAILURE"
    _push_marker(
        marker_outlet,
        marker,
        scenario_t=t,
        window=window.get("name"),
        reason=reason,
        response=response_key,
        rt_s=rt_s,
        aoi_confirmed=bool(window.get("aoi_confirmed")),
        aoi_confirm_time_s=window.get("aoi_confirm_time_s"),
    )
    if window.get("notification_text"):
        _push_marker(
            marker_outlet,
            "TOR_NOTIFICATION",
            scenario_t=t,
            window=window.get("name"),
            outcome=outcome,
            text=window.get("notification_text"),
        )
    if outcome == "success":
        _push_marker(
            marker_outlet,
            "TAKEOVER_KEY",
            scenario_t=t,
            key=response_key,
            expected_key="/".join(window.get("keys") or []),
            aoi_confirmed=bool(window.get("aoi_confirmed")),
            aoi_confirm_time_s=window.get("aoi_confirm_time_s"),
        )
        return None
    else:
        _push_marker(
            marker_outlet,
            "TAKEOVER_EXPIRED" if response_key is None else "TAKEOVER_FORCE_BRAKE",
            scenario_t=t,
            deadline_s=window.get("deadline_s"),
            reason=reason,
            aoi_confirmed=bool(window.get("aoi_confirmed")),
        )
        action = window.get("failure_action", "brake")
    return _start_experiment_action(
        action,
        response_key,
        outcome,
        window,
        t,
        current_speed_mps,
        ego_speed_mps,
        lane_context,
        marker_outlet=marker_outlet,
    )


def _tor_manual_requirement_met(window):
    fields = dict(window.get("fields", {}) or {})
    requirement = normalize_manual_requirement(fields.get("manual_requires", "none"))
    if requirement == "stage2":
        return bool(window.get("stage2_visual_active")), requirement
    if requirement in ("ready", "aoi"):
        return bool(window.get("aoi_confirmed")), requirement
    if requirement == "fixation":
        return bool(window.get("target_fixation_confirmed")), requirement
    return True, requirement


def _build_return_to_auto_lane_action(window, t, lane_context, ego_speed_mps, resume_key):
    return_lane = str(window.get("fallback_return_lane") or LANE_RIGHT)
    if return_lane in ("", LANE_OUTER_RIGHT, LANE_QUEEN_FOURTH):
        return_lane = LANE_RIGHT
    current_lane = lane_context.get("lane") or window.get("fallback_target_lane") or LANE_OUTER_RIGHT
    target_speed_mps = max(
        0.0,
        _as_float(window.get("fallback_return_speed_mps"), ego_speed_mps),
    )
    return {
        "type": "return_to_auto_lane",
        "window": window.get("name"),
        "outcome": "fallback_recovery",
        "response_key": resume_key,
        "started_at": t,
        "duration_s": max(0.001, _as_float(window.get("fallback_return_lane_change_duration_s"), 4.0)),
        "from_lane": current_lane,
        "to_lane": return_lane,
        "start_speed_mps": 0.0,
        "target_speed_mps": target_speed_mps,
        "return_key": window.get("return_key") or "SPACE",
        "return_key_label": window.get("return_key_label") or window.get("return_key") or "SPACE",
        "end_emitted": False,
        "complete": False,
    }


def _update_fallback_recovery_tor_window(window, t, resume_pressed, resume_key, return_key,
                                         return_key_label, ego_speed_mps, lane_context,
                                         marker_outlet=None, runtime_args=None):
    state = window.get("fallback_recovery_state")
    if not state:
        return None
    action_end_s = (
        float(window.get("fallback_action_started_at", t) or t)
        + max(0.0, float(window.get("fallback_action_duration_s", 0.0) or 0.0))
    )
    if state == "pulling_over" and t >= action_end_s:
        window["fallback_recovery_state"] = "stop_hold"
        window["fallback_stop_hold_started_at"] = action_end_s
        state = "stop_hold"
        _push_marker(
            marker_outlet,
            "FALLBACK_STOP_HOLD_START",
            scenario_t=t,
            window=window.get("name"),
            action=window.get("fallback_action_type"),
            wait_s=window.get("fallback_post_stop_wait_s"),
            target_lane=window.get("fallback_target_lane"),
        )
        print("[t={0:.2f}s] fallback pull-over complete; holding stopped vehicle for {1:.1f}s.".format(
            t, float(window.get("fallback_post_stop_wait_s", 5.0) or 5.0)))

    if state == "stop_hold":
        wait_s = max(0.0, float(window.get("fallback_post_stop_wait_s", 5.0) or 5.0))
        hold_started_at = float(window.get("fallback_stop_hold_started_at", action_end_s) or action_end_s)
        if t < hold_started_at + wait_s:
            return None
        window["fallback_recovery_state"] = "awaiting_return_key"
        state = "awaiting_return_key"
        if not window.get("return_requested"):
            window["return_requested"] = True
            _push_marker(
                marker_outlet,
                "RETURN_TO_AUTO_REQUEST",
                scenario_t=t,
                window=window.get("name"),
                key=return_key,
                text=window.get("return_to_auto_text"),
                hold_s=wait_s,
            )
            _push_marker(
                marker_outlet,
                "TOR_NOTIFICATION",
                scenario_t=t,
                window=window.get("name"),
                outcome="return_to_auto_request",
                text=window.get("return_to_auto_text"),
            )
            _speak_async(window.get("return_to_auto_text"))
            print("[t={0:.2f}s] fallback stop held for {1:.1f}s; press {2} to return to AUTO.".format(
                t, wait_s, return_key_label))

    if state == "awaiting_return_key":
        if not resume_pressed:
            return None
        action = _build_return_to_auto_lane_action(window, t, lane_context, ego_speed_mps, resume_key)
        window["fallback_recovery_state"] = "returning_to_auto_lane"
        window["fallback_return_action_started_at"] = t
        window["fallback_return_action_duration_s"] = action.get("duration_s")
        _push_marker(
            marker_outlet,
            "RETURN_TO_AUTO_CONFIRMED",
            scenario_t=t,
            window=window.get("name"),
            key=resume_key,
            from_lane=action.get("from_lane"),
            to_lane=action.get("to_lane"),
        )
        _push_marker(
            marker_outlet,
            "AVOIDANCE_ACTION_START",
            scenario_t=t,
            window=window.get("name"),
            outcome=action.get("outcome"),
            response=resume_key,
            action=action.get("type"),
            duration_s=action.get("duration_s"),
            from_lane=action.get("from_lane"),
            to_lane=action.get("to_lane"),
            target_speed_kmh=action.get("target_speed_mps", 0.0) * 3.6,
        )
        print("[t={0:.2f}s] {1} received; returning ego from {2} to {3} before AUTO ON.".format(
            t,
            return_key_label if resume_key == "G29_RETURN" else resume_key,
            action.get("from_lane"),
            action.get("to_lane"),
        ))
        return action

    if state == "returning_to_auto_lane":
        started_at = float(window.get("fallback_return_action_started_at", t) or t)
        duration_s = max(0.001, float(window.get("fallback_return_action_duration_s", 0.0) or 0.0))
        if t < started_at + duration_s or window.get("auto_resumed"):
            return None
        window["fallback_recovery_state"] = "resumed"
        window["fallback_active"] = False
        window["auto_resumed"] = True
        _push_marker(
            marker_outlet,
            "AUTO_RESUME",
            scenario_t=t,
            window=window.get("name"),
            key=window.get("return_key") or "SPACE",
            reason="fallback_recovery",
        )
        _push_marker(
            marker_outlet,
            "AUTO_ON",
            scenario_t=t,
            window=window.get("name"),
            mode="auto_on",
        )
        _push_marker(
            marker_outlet,
            "TOR_NOTIFICATION",
            scenario_t=t,
            window=window.get("name"),
            outcome="auto_resumed",
            text="Autonomous driving resumed.",
        )
        _play_return_auto_off_audio(runtime_args)
        print("[t={0:.2f}s] fallback recovery complete; AUTO ON resumed in {1} lane.".format(
            t, window.get("fallback_return_lane") or LANE_RIGHT))
    return None


def _update_experiment_tor_windows(windows, detector, t, current_command_speed_mps, current_actor_speed_mps,
                                   ego_speed_mps, lane_context, marker_outlet=None, require_aoi=True,
                                   collision_monitor=None, ego_plan=None, plan_by_role=None, runtime_args=None):
    if not windows:
        return []
    actions = []
    pressed = detector.pressed_edges() if detector is not None else set()
    pressed = _augment_pressed_with_g29_hold(pressed, detector)
    if runtime_args is not None:
        _write_g29_python_state(runtime_args, detector=detector, pressed=pressed)
    resume_key = "G29_RETURN" if "G29_RETURN" in pressed else ("SPACE" if "SPACE" in pressed else None)
    resume_pressed = resume_key is not None
    for window in windows:
        manual_keys = window.get("keys") or ["W", "A", "S", "D"]
        manual_pressed = sorted(set(manual_keys).intersection(pressed))
        key_label = "/".join(manual_keys)
        return_key = window.get("return_key") or "SPACE"
        return_key_label = window.get("return_key_label") or return_key
        if not window.get("shown") and (window.get("trigger_mode") or "time").lower() in ("gap", "ttc"):
            fired, metric = _eval_gap_ttc_trigger(window, ego_plan, plan_by_role)
            if fired:
                # Convert the gap/TTC hit into the existing time path: stamp the
                # alert onset to now and preserve the configured response window.
                win_len = max(0.0, float(window["deadline_s"]) - float(window["onset_s"]))
                window["onset_s"] = t
                window["deadline_s"] = t + win_len
                trig_fields = window.setdefault("fields", {})
                trig_fields["trigger_mode"] = (window.get("trigger_mode") or "time").lower()
                trig_fields["trigger_metric"] = round(float(metric), 3) if metric is not None else None
        if not window.get("shown") and t >= window["onset_s"]:
            window["shown"] = True
            fields = dict(window.get("fields", {}) or {})
            _push_marker(
                marker_outlet,
                "TOR_ALERT_ONSET",
                scenario_t=t,
                window=window.get("name"),
                aoi_screen=fields.get("aoi_screen", "current"),
                aoi_grid=fields.get("aoi_grid", "middle_left"),
                deadline_s=window.get("deadline_s"),
                stage2_direction=fields.get("stage2_direction"),
                stage2_speech_template=fields.get("stage2_speech_template"),
                stage2_text=fields.get("stage2_text"),
                stage_speech_min_gap_s=fields.get("stage_speech_min_gap_s"),
                stage1_requirement=fields.get("stage1_requirement"),
                stage1_delay_s=fields.get("stage1_delay_s"),
                stage2_requirement=fields.get("stage2_requirement"),
                stage2_delay_s=fields.get("stage2_delay_s"),
                target_fixation_s=fields.get("target_fixation_s"),
                manual_requires=fields.get("manual_requires"),
                trigger_mode=fields.get("trigger_mode", "time"),
                trigger_metric=fields.get("trigger_metric"),
            )
            _push_marker(
                marker_outlet,
                "ALERT_STAGE1_ONSET",
                scenario_t=t,
                window=window.get("name"),
                deadline_s=window.get("deadline_s"),
            )
            _push_marker(
                marker_outlet,
                "TOR_WINDOW_START",
                scenario_t=t,
                window=window.get("name"),
                manual_keys=key_label,
                deadline_s=window.get("deadline_s"),
                require_aoi=False,
            )
            _push_marker(marker_outlet, "AUTO_ON", scenario_t=t, window=window.get("name"), mode="auto_on")
            if window.get("alert_text"):
                _push_marker(
                    marker_outlet,
                    "TOR_NOTIFICATION",
                    scenario_t=t,
                    window=window.get("name"),
                    outcome="alert",
                    text=window.get("alert_text"),
                )
            _emit_dynamic_tor_aoi(window, t, ego_plan, plan_by_role, marker_outlet=marker_outlet, force=True)
            print("[t={0:.2f}s] TOR ALERT: {1}, deadline={2:.2f}s, press [{3}] before the deadline.".format(
                t, window.get("name"), window.get("deadline_s"), key_label))

        if not window.get("shown"):
            continue
        if not window.get("ended"):
            _emit_dynamic_tor_aoi(window, t, ego_plan, plan_by_role, marker_outlet=marker_outlet)
            _apply_tor_visual_test_if_needed(
                window,
                t,
                marker_outlet,
                runtime_args,
                ego_plan,
                plan_by_role,
            )

        if window.get("manual_active") and not window.get("auto_resumed"):
            collision_count = collision_monitor.snapshot_count() if collision_monitor is not None else 0
            if collision_count > int(window.get("manual_collision_count", 0) or 0):
                latest_collision = collision_monitor.snapshot() if collision_monitor is not None else {"count": collision_count}
                window["manual_collision_count"] = collision_count
                window["manual_start_s"] = t
                window["return_requested"] = False
                if not window.get("manual_collision_failed"):
                    window["manual_collision_failed"] = True
                    window["outcome"] = "failure"
                    fail_text = window.get("collision_fail_text")
                    _push_marker(
                        marker_outlet,
                        "MANUAL_COLLISION",
                        scenario_t=t,
                        window=window.get("name"),
                        key=window.get("manual_key"),
                        collision_count=collision_count,
                        other_actor_id=latest_collision.get("other_actor_id"),
                        other_type_id=latest_collision.get("other_type_id"),
                        impulse=latest_collision.get("impulse"),
                    )
                    _push_marker(
                        marker_outlet,
                        "TOR_FAILURE",
                        scenario_t=t,
                        window=window.get("name"),
                        reason="manual_collision",
                        response=window.get("manual_key"),
                        collision_count=collision_count,
                        other_actor_id=latest_collision.get("other_actor_id"),
                        other_type_id=latest_collision.get("other_type_id"),
                    )
                    _push_marker(
                        marker_outlet,
                        "TOR_FAIL_COLLISION",
                        scenario_t=t,
                        window=window.get("name"),
                        response=window.get("manual_key"),
                        collision_count=collision_count,
                        other_actor_id=latest_collision.get("other_actor_id"),
                        other_type_id=latest_collision.get("other_type_id"),
                    )
                    _push_marker(
                        marker_outlet,
                        "ACCIDENT_DETECTED",
                        scenario_t=t,
                        window=window.get("name"),
                        source="post_takeover_collision",
                        collision_count=collision_count,
                        other_actor_id=latest_collision.get("other_actor_id"),
                        other_type_id=latest_collision.get("other_type_id"),
                    )
                    _push_marker(
                        marker_outlet,
                        "TOR_NOTIFICATION",
                        scenario_t=t,
                        window=window.get("name"),
                        outcome="manual_collision",
                        text=fail_text,
                    )
                    _alarm_async()
                    _speak_async(fail_text)
                    if collision_monitor is not None:
                        collision_monitor.suppress_logging()
                    _popup_async("TOR FAIL", fail_text + "\n\nPlease stop and restart this scenario.")
                    print("[t={0:.2f}s] TOR FAIL: collision after manual takeover; other={1}; restart scenario.".format(
                        t, latest_collision.get("other_type_id") or "unknown"))
            if window.get("manual_collision_failed"):
                continue
            safe_elapsed = t - float(window.get("manual_start_s", t) or t)
            if (not window.get("return_requested")) and safe_elapsed >= float(window.get("manual_safe_seconds", 4.0)):
                window["return_requested"] = True
                _push_marker(
                    marker_outlet,
                    "MANUAL_SAFE_HOLD_REACHED",
                    scenario_t=t,
                    window=window.get("name"),
                    safe_s=safe_elapsed,
                    collision_count=collision_count,
                )
                _push_marker(
                    marker_outlet,
                    "RETURN_TO_AUTO_REQUEST",
                    scenario_t=t,
                    window=window.get("name"),
                    key=return_key,
                    text=window.get("return_to_auto_text"),
                )
                _push_marker(
                    marker_outlet,
                    "TOR_NOTIFICATION",
                    scenario_t=t,
                    window=window.get("name"),
                    outcome="return_to_auto_request",
                    text=window.get("return_to_auto_text"),
                )
                _speak_async(window.get("return_to_auto_text"))
                print("[t={0:.2f}s] manual driving held safely for {1:.2f}s; press {2} for AUTO ON.".format(
                    t, safe_elapsed, return_key_label))
            if resume_pressed:
                window["manual_active"] = False
                window["auto_resumed"] = True
                window["return_requested"] = True
                _push_marker(
                    marker_outlet,
                    "AUTO_RESUME",
                    scenario_t=t,
                    window=window.get("name"),
                    key=resume_key,
                )
                _push_marker(
                    marker_outlet,
                    "AUTO_ON",
                    scenario_t=t,
                    window=window.get("name"),
                    mode="auto_on",
                )
                _play_return_auto_off_audio(runtime_args)
                print("[t={0:.2f}s] {1} received; AUTO ON resumed.".format(
                    t, return_key_label if resume_key == "G29_RETURN" else resume_key))
            continue

        recovery_action = _update_fallback_recovery_tor_window(
            window,
            t,
            resume_pressed,
            resume_key,
            return_key,
            return_key_label,
            ego_speed_mps,
            lane_context,
            marker_outlet=marker_outlet,
            runtime_args=runtime_args,
        )
        if recovery_action is not None:
            actions.append(recovery_action)
        if window.get("fallback_recovery_state") in (
            "pulling_over",
            "stop_hold",
            "awaiting_return_key",
            "returning_to_auto_lane",
        ):
            continue

        if window.get("ended"):
            if manual_pressed:
                for key in manual_pressed:
                    late_key = "{0}:{1:.3f}".format(key, t)
                    if late_key in window.get("late_keys", set()):
                        continue
                    window["late_keys"].add(late_key)
                    _push_marker(
                        marker_outlet,
                        "TOR_RESPONSE_LATE",
                        scenario_t=t,
                        window=window.get("name"),
                        response=key,
                        outcome=window.get("outcome"),
                    )
            continue

        if window["onset_s"] <= t <= window["deadline_s"] and manual_pressed:
            response = manual_pressed[0]
            requirement_met, manual_requirement = _tor_manual_requirement_met(window)
            if not requirement_met:
                for key in manual_pressed:
                    _push_marker(
                        marker_outlet,
                        "TOR_RESPONSE_BLOCKED",
                        scenario_t=t,
                        window=window.get("name"),
                        response=key,
                        reason="manual_requirement_not_met",
                        manual_requires=manual_requirement,
                        stage2_visual_active=bool(window.get("stage2_visual_active")),
                        aoi_confirmed=bool(window.get("aoi_confirmed")),
                        target_fixation_confirmed=bool(window.get("target_fixation_confirmed")),
                        deadline_s=window.get("deadline_s"),
                    )
                continue
            _push_marker(
                marker_outlet,
                "TOR_RESPONSE",
                scenario_t=t,
                window=window.get("name"),
                response=response,
                valid=True,
                correct=True,
                rt_s=max(0.0, t - float(window["onset_s"])),
                deadline_s=window.get("deadline_s"),
                aoi_confirmed=bool(window.get("aoi_confirmed")),
            )
            window["ended"] = True
            window["outcome"] = "success"
            window["manual_active"] = True
            window["manual_key"] = response
            window["manual_start_s"] = t
            window["manual_collision_count"] = collision_monitor.snapshot_count() if collision_monitor is not None else 0
            _push_marker(
                marker_outlet,
                "TOR_WINDOW_END",
                scenario_t=t,
                window=window.get("name"),
                outcome="success",
                reason="manual_takeover",
                response=response,
                rt_s=max(0.0, t - float(window["onset_s"])),
                onset_s=window.get("onset_s"),
                deadline_s=window.get("deadline_s"),
            )
            _push_marker(
                marker_outlet,
                "TOR_SUCCESS",
                scenario_t=t,
                window=window.get("name"),
                reason="manual_takeover",
                response=response,
                rt_s=max(0.0, t - float(window["onset_s"])),
            )
            _push_marker(
                marker_outlet,
                "TAKEOVER_KEY",
                scenario_t=t,
                key=response,
                expected_key=key_label,
                aoi_confirmed=bool(window.get("aoi_confirmed")),
                aoi_confirm_time_s=window.get("aoi_confirm_time_s"),
            )
            _push_marker(
                marker_outlet,
                "AUTO_OFF",
                scenario_t=t,
                window=window.get("name"),
                mode="manual",
                key=response,
            )
            _push_marker(
                marker_outlet,
                "MANUAL_TAKEOVER",
                scenario_t=t,
                window=window.get("name"),
                key=response,
                collision_count=window.get("manual_collision_count"),
            )
            _push_marker(
                marker_outlet,
                "TOR_NOTIFICATION",
                scenario_t=t,
                window=window.get("name"),
                outcome="success",
                text=window.get("success_text"),
            )
            _play_takeover_on_audio(runtime_args)
            _speak_async(window.get("success_text"))
            print("[t={0:.2f}s] TOR SUCCESS: manual takeover via [{1}]; AUTO OFF.".format(t, response))
        elif t > window["deadline_s"] and not window.get("ended"):
            for response in manual_pressed:
                _push_marker(
                    marker_outlet,
                    "TOR_RESPONSE_LATE",
                    scenario_t=t,
                    window=window.get("name"),
                    response=response,
                    deadline_s=window.get("deadline_s"),
                )
            action = _finish_experiment_tor_window(
                window,
                "failure",
                "missed_deadline",
                None,
                t,
                max(current_actor_speed_mps, current_command_speed_mps),
                ego_speed_mps,
                lane_context,
                marker_outlet=marker_outlet,
            )
            window["fallback_active"] = True
            if action and action.get("type") == "brake_pull_over":
                window["fallback_recovery_state"] = "pulling_over"
                window["fallback_action_type"] = action.get("type")
                window["fallback_action_started_at"] = action.get("started_at", t)
                window["fallback_action_duration_s"] = action.get("duration_s", 0.0)
                window["fallback_post_stop_wait_s"] = action.get("post_stop_wait_s", 5.0)
                window["fallback_target_lane"] = action.get("to_lane")
                window["fallback_return_lane"] = action.get("return_lane", LANE_RIGHT)
                window["fallback_return_speed_mps"] = action.get("return_speed_mps", ego_speed_mps)
                window["fallback_return_lane_change_duration_s"] = action.get("return_lane_change_duration_s", 4.0)
                window["return_requested"] = False
                window["auto_resumed"] = False
            _push_marker(
                marker_outlet,
                "SYSTEM_FALLBACK_START",
                scenario_t=t,
                window=window.get("name"),
                action=action.get("type") if action else None,
            )
            _push_marker(
                marker_outlet,
                "TOR_NOTIFICATION",
                scenario_t=t,
                window=window.get("name"),
                outcome="missed_deadline",
                text=window.get("failure_text"),
            )
            _speak_async(window.get("failure_text"))
            print("[t={0:.2f}s] TOR FAILURE: no takeover input [{1}] before deadline {2:.2f}s; fallback action={3}.".format(
                t, key_label, window.get("deadline_s"), action.get("type") if action else "none"))
            actions.append(action)
        elif t > window["deadline_s"] and manual_pressed:
            for response in manual_pressed:
                _push_marker(
                    marker_outlet,
                    "TOR_RESPONSE_LATE",
                    scenario_t=t,
                    window=window.get("name"),
                    response=response,
                    deadline_s=window.get("deadline_s"),
                )
    return [action for action in actions if action is not None]


def _smoothstep(value):
    value = max(0.0, min(1.0, float(value or 0.0)))
    return value * value * (3.0 - 2.0 * value)


def _update_experiment_actions(actions, t, base_speed_mps, lane_context, lane_paths, expected_ego_s,
                               marker_outlet=None, windows_by_name=None):
    speed_mps = max(0.0, float(base_speed_mps or 0.0))
    lane_target_xf = None
    lane_sample_lane = lane_context.get("lane", LANE_MIDDLE)
    for action in actions:
        if action.get("complete"):
            continue
        action_type = action.get("type")
        elapsed = max(0.0, t - float(action.get("started_at", t)))
        duration_s = max(0.001, float(action.get("duration_s", 0.0) or 0.0))
        done = False
        if action_type in ("accelerate", "brake"):
            start_speed = float(action.get("start_speed_mps", speed_mps) or 0.0)
            target_speed = float(action.get("target_speed_mps", speed_mps) or 0.0)
            recovery_after = action.get("recovery_after_s")
            recover_speed = action.get("recover_speed_mps")
            if recovery_after is not None and recover_speed is not None and elapsed >= float(recovery_after):
                rec_elapsed = elapsed - float(recovery_after)
                rec_duration = max(0.001, float(action.get("recovery_duration_s", 2.0) or 2.0))
                blend = min(1.0, rec_elapsed / rec_duration)
                candidate = target_speed + (float(recover_speed) - target_speed) * blend
                done = blend >= 1.0
            else:
                blend = min(1.0, elapsed / duration_s)
                candidate = start_speed + (target_speed - start_speed) * blend
                done = (recovery_after is None or recover_speed is None) and blend >= 1.0
            if target_speed <= start_speed:
                speed_mps = min(speed_mps, candidate)
            else:
                speed_mps = max(speed_mps, candidate)
        elif action_type in ("lane_change_left", "lane_change_right"):
            from_lane = action.get("from_lane", lane_context.get("lane", LANE_MIDDLE))
            to_lane = action.get("to_lane", from_lane)
            if from_lane not in lane_paths:
                from_lane = lane_context.get("lane", LANE_MIDDLE)
            if to_lane not in lane_paths:
                to_lane = LANE_RIGHT if LANE_RIGHT in lane_paths else from_lane
            blend = _smoothstep(elapsed / duration_s)
            src = lane_paths[from_lane].sample(expected_ego_s)
            dst = lane_paths[to_lane].sample(expected_ego_s)
            lane_target_xf = _lerp_transform(src, dst, blend)
            lane_sample_lane = to_lane if blend >= 0.5 else from_lane
            done = elapsed >= duration_s
            if done:
                lane_context["lane"] = to_lane
        elif action_type == "return_to_auto_lane":
            from_lane = action.get("from_lane", lane_context.get("lane", LANE_OUTER_RIGHT))
            to_lane = action.get("to_lane", LANE_RIGHT)
            if from_lane not in lane_paths:
                from_lane = lane_context.get("lane", LANE_OUTER_RIGHT)
            if from_lane not in lane_paths:
                from_lane = LANE_RIGHT if LANE_RIGHT in lane_paths else lane_sample_lane
            if to_lane not in lane_paths:
                to_lane = LANE_RIGHT if LANE_RIGHT in lane_paths else from_lane
            start_speed = max(0.0, float(action.get("start_speed_mps", speed_mps) or 0.0))
            target_speed = max(0.0, float(action.get("target_speed_mps", speed_mps) or 0.0))
            blend = _smoothstep(elapsed / duration_s)
            src = lane_paths[from_lane].sample(expected_ego_s)
            dst = lane_paths[to_lane].sample(expected_ego_s)
            lane_target_xf = _lerp_transform(src, dst, blend)
            lane_sample_lane = to_lane if blend >= 0.5 else from_lane
            speed_mps = start_speed + (target_speed - start_speed) * blend
            done = elapsed >= duration_s
            if done:
                lane_context["lane"] = to_lane
                lane_sample_lane = to_lane
        elif action_type == "brake_pull_over":
            from_lane = action.get("from_lane", lane_context.get("lane", LANE_MIDDLE))
            to_lane = action.get("to_lane", LANE_RIGHT)
            if from_lane not in lane_paths:
                from_lane = lane_context.get("lane", LANE_MIDDLE)
            if to_lane not in lane_paths:
                to_lane = LANE_RIGHT if LANE_RIGHT in lane_paths else from_lane
            start_speed = float(action.get("start_speed_mps", speed_mps) or 0.0)
            pull_speed = float(action.get("pull_over_speed_mps", min(speed_mps, start_speed)) or 0.0)
            brake_duration = max(0.001, float(action.get("brake_duration_s", 1.5) or 1.5))
            lane_duration = max(0.001, float(action.get("lane_change_duration_s", 2.5) or 2.5))
            final_brake_duration = max(0.001, float(action.get("final_brake_duration_s", 1.5) or 1.5))
            if elapsed < brake_duration:
                blend = min(1.0, elapsed / brake_duration)
                candidate = start_speed + (pull_speed - start_speed) * blend
                speed_mps = min(speed_mps, candidate) if pull_speed <= start_speed else max(speed_mps, candidate)
                lane_sample_lane = from_lane
            elif elapsed < brake_duration + lane_duration:
                lane_elapsed = elapsed - brake_duration
                blend = _smoothstep(lane_elapsed / lane_duration)
                src = lane_paths[from_lane].sample(expected_ego_s)
                dst = lane_paths[to_lane].sample(expected_ego_s)
                lane_target_xf = _lerp_transform(src, dst, blend)
                lane_sample_lane = to_lane if blend >= 0.5 else from_lane
                speed_mps = min(speed_mps, pull_speed) if pull_speed <= speed_mps else pull_speed
            else:
                lane_context["lane"] = to_lane
                lane_sample_lane = to_lane
                final_elapsed = elapsed - brake_duration - lane_duration
                blend = min(1.0, final_elapsed / final_brake_duration)
                candidate = pull_speed * (1.0 - blend)
                speed_mps = min(speed_mps, candidate)
                done = blend >= 1.0
                if not done:
                    lane_target_xf = lane_paths[to_lane].sample(expected_ego_s)
            if done:
                lane_context["lane"] = to_lane
        elif action_type == "keep_lane":
            done = True

        if done and not action.get("end_emitted"):
            action["end_emitted"] = True
            action["complete"] = True
            _push_marker(
                marker_outlet,
                "AVOIDANCE_ACTION_END",
                scenario_t=t,
                window=action.get("window"),
                outcome=action.get("outcome"),
                response=action.get("response_key"),
                action=action_type,
                from_lane=action.get("from_lane"),
                to_lane=action.get("to_lane"),
            )
            if action.get("outcome") == "failure":
                _push_marker(
                    marker_outlet,
                    "SYSTEM_FALLBACK_END",
                    scenario_t=t,
                    window=action.get("window"),
                    action=action_type,
                )
                if action_type != "brake_pull_over":
                    _push_marker(
                        marker_outlet,
                        "RETURN_TO_AUTO_REQUEST",
                        scenario_t=t,
                        window=action.get("window"),
                        key=action.get("return_key") or "SPACE",
                        text=action.get("return_to_auto_text"),
                    )
                    _push_marker(
                        marker_outlet,
                        "TOR_NOTIFICATION",
                        scenario_t=t,
                        window=action.get("window"),
                        outcome="fallback_complete",
                        text=action.get("return_to_auto_text"),
                    )
                    _push_marker(
                        marker_outlet,
                        "AUTO_ON",
                        scenario_t=t,
                        window=action.get("window"),
                        mode="auto_on",
                    )
                    _speak_async(action.get("return_to_auto_text"))
                    # Non-pull-over fallbacks resume AUTO right here, so mark the
                    # window resolved for the hold-until-resolution end policy.
                    window_obj = (windows_by_name or {}).get(action.get("window"))
                    if window_obj is not None:
                        window_obj["fallback_active"] = False
                        window_obj["auto_resumed"] = True
            elif action_type == "return_to_auto_lane":
                _push_marker(
                    marker_outlet,
                    "RETURN_TO_AUTO_ACTION_END",
                    scenario_t=t,
                    window=action.get("window"),
                    response=action.get("response_key"),
                    from_lane=action.get("from_lane"),
                    to_lane=action.get("to_lane"),
                )
    active = [action for action in actions if not action.get("complete")]
    return max(0.0, speed_mps), lane_target_xf, lane_sample_lane, active


def _location_distance_m(a, b):
    dx = float(a.x) - float(b.x)
    dy = float(a.y) - float(b.y)
    dz = float(a.z) - float(b.z)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _format_location(loc):
    return "({0:.1f},{1:.1f},{2:.2f})".format(
        float(loc.x),
        float(loc.y),
        float(loc.z),
    )


def _plan_lane_state_and_target_s(plan, lane_paths, ego_s, t, ego_reference_speed_mps):
    if plan.role == "ego":
        lane_name = plan.cfg.get("lane", LANE_MIDDLE)
        return lane_name, lane_name, 1.0, float(ego_s or 0.0)
    try:
        _xf, target_s, lane_state = _scripted_target_transform(
            plan,
            lane_paths,
            ego_s,
            t,
            ego_reference_speed_mps,
        )
        return lane_state.src_lane, lane_state.dst_lane, lane_state.blend, target_s
    except Exception:
        lane_name = plan.cfg.get("lane", LANE_MIDDLE)
        return lane_name, lane_name, 1.0, float(plan.initial_s or 0.0)


def _print_vehicle_telemetry(
    plans,
    lane_paths,
    world,
    ego_plan,
    ego_s,
    expected_ego_s,
    t,
    ego_reference_speed_mps,
):
    ego_loc = ego_plan.actor.get_transform().location if ego_plan and ego_plan.actor else None
    print("    vehicle telemetry:")
    plan_actor_ids = set()
    for plan in plans:
        if plan.actor is None:
            print("      {0:<9} actor=none enabled={1}".format(
                plan.role,
                bool(plan.cfg.get("enabled", False)),
            ))
            continue
        plan_actor_ids.add(plan.actor.id)
        tf = plan.actor.get_transform()
        loc = tf.location
        src_lane, dst_lane, blend, target_s = _plan_lane_state_and_target_s(
            plan,
            lane_paths,
            ego_s,
            t,
            ego_reference_speed_mps,
        )
        actual_lane_for_path = dst_lane if blend >= 0.5 else src_lane
        lane_path = lane_paths.get(actual_lane_for_path, lane_paths.get(plan.cfg.get("lane", LANE_MIDDLE)))
        if plan.role == "ego":
            target_s = float(expected_ego_s or 0.0)
            actual_s = float(ego_s or 0.0)
        else:
            actual_s = lane_path.nearest_s(loc, fallback_s=target_s, window_m=180.0)
        long_gap_m = actual_s - float(ego_s or 0.0)
        target_error_m = float(target_s or 0.0) - float(actual_s or 0.0)
        if plan.role == "ego":
            bumper_clearance_m = 0.0
        else:
            bumper_clearance_m = float(getattr(plan, "time_gap_bumper_clearance_m", 0.0) or 0.0)
            if bumper_clearance_m <= 0.0 and ego_plan and ego_plan.actor:
                bumper_clearance_m = _combined_bumper_clearance_m(ego_plan.actor, plan.actor)
        bumper_gap_m = _signed_bumper_gap_from_center_delta_m(long_gap_m, bumper_clearance_m)
        overlap_m = _bumper_overlap_m(long_gap_m, bumper_clearance_m)
        dist_m = _location_distance_m(loc, ego_loc) if ego_loc is not None else 0.0
        lateral_err = _lane_lateral_error_m(plan.actor, lane_path, actual_s)
        lane_yaw = lane_path.sample(actual_s).rotation.yaw if lane_path is not None else 0.0
        yaw_err = _normalize_angle_deg(float(tf.rotation.yaw) - float(lane_yaw))
        print(
            "      {role:<9} id={actor_id:<5} mode={mode:<15} cfg_lane={cfg_lane:<6} "
            "path={src}->{dst} blend={blend:.2f} target_s={target_s:.1f}m "
            "actual_s={actual_s:.1f}m target_err={target_err:.1f}m "
            "gap_to_ego={gap:.1f}m bumper_gap={bumper_gap:.1f}m "
            "overlap={overlap:.1f}m dist={dist:.1f}m "
            "lat_err={lat:.2f}m yaw={yaw:.1f} lane_yaw={lane_yaw:.1f} yaw_err={yaw_err:.1f} "
            "{actual_lane} loc={loc} z={z:.2f} speed={speed:.1f}km/h".format(
                role=plan.role,
                actor_id=plan.actor.id,
                mode=plan.control_mode,
                cfg_lane=plan.cfg.get("lane", "?"),
                src=src_lane,
                dst=dst_lane,
                blend=blend,
                target_s=target_s,
                actual_s=actual_s,
                target_err=target_error_m,
                gap=long_gap_m,
                bumper_gap=bumper_gap_m,
                overlap=overlap_m,
                dist=dist_m,
                lat=lateral_err,
                yaw=float(tf.rotation.yaw),
                lane_yaw=float(lane_yaw),
                yaw_err=float(yaw_err),
                actual_lane=_actual_actor_lane(world, plan.actor),
                loc=_format_location(loc),
                z=float(loc.z),
                speed=_actor_speed_mps(plan.actor) * 3.6,
            )
        )

    try:
        for actor in world.get_actors().filter("*vehicle*"):
            if actor.id in plan_actor_ids:
                continue
            role = actor.attributes.get("role_name", "")
            loc = actor.get_transform().location
            dist_m = _location_distance_m(loc, ego_loc) if ego_loc is not None else 0.0
            print(
                "      extra_vehicle id={0:<5} role={1} type={2} {3} "
                "dist_to_ego={4:.1f}m loc={5} speed={6:.1f}km/h".format(
                    actor.id,
                    role or "(none)",
                    actor.type_id,
                    _actual_actor_lane(world, actor),
                    dist_m,
                    _format_location(loc),
                    _actor_speed_mps(actor) * 3.6,
                )
            )
    except Exception as exc:
        print("      extra_vehicle_scan_failed: {0}".format(exc))


def _first_lane_change_start_s(plan):
    points = plan.cfg.get("lane_change_points", []) or []
    if not points:
        return None
    times = []
    for point in points:
        try:
            times.append(float(point.get("time_s", 0.0) or 0.0))
        except Exception:
            pass
    return min(times) if times else None


def _build_lane_change_events(plans, tor_alert_delay_s, tor_aoi_screen, tor_aoi_grid):
    events = []
    first_support_start = None
    first_support_role = None
    for plan in plans:
        points = sorted(plan.cfg.get("lane_change_points", []), key=lambda p: p["time_s"])
        current_lane = plan.cfg.get("lane", LANE_MIDDLE)
        for idx, point in enumerate(points):
            start_t = float(point.get("time_s", 0.0) or 0.0)
            duration_s = max(0.0, float(point.get("duration_s", 0.0) or 0.0))
            target_lane = point.get("target_lane", current_lane)
            events.append({
                "time_s": start_t,
                "event": "LANE_CHANGE_START",
                "key": "lc_start:{0}:{1}".format(plan.role, idx),
                "role": plan.role,
                "from_lane": current_lane,
                "to_lane": target_lane,
                "duration_s": duration_s,
            })
            events.append({
                "time_s": start_t + duration_s,
                "event": "LANE_CHANGE_END",
                "key": "lc_end:{0}:{1}".format(plan.role, idx),
                "role": plan.role,
                "from_lane": current_lane,
                "to_lane": target_lane,
                "duration_s": duration_s,
            })
            if plan.role != "ego":
                if first_support_start is None or start_t < first_support_start:
                    first_support_start = start_t
                    first_support_role = plan.role
            current_lane = target_lane
    if first_support_start is not None:
        events.append({
            "time_s": first_support_start,
            "event": "FPE_ONSET",
            "key": "fpe:first_support_lane_change",
            "role": first_support_role,
        })
        events.append({
            "time_s": first_support_start + max(0.0, float(tor_alert_delay_s)),
            "event": "TOR_ALERT_ONSET",
            "key": "tor_alert:first_support_lane_change",
            "role": first_support_role,
            "delay_after_fpe_s": max(0.0, float(tor_alert_delay_s)),
            "aoi_screen": tor_aoi_screen,
            "aoi_grid": tor_aoi_grid,
            "aoi_x": 1.0 / 6.0 if str(tor_aoi_grid).lower() in ("middle_left", "middle-left") else None,
            "aoi_y": 0.5 if str(tor_aoi_grid).lower() in ("middle_left", "middle-left") else None,
        })
    events.sort(key=lambda item: item["time_s"])
    return events


def _build_vehicle_behavior_events(plans):
    events = []
    for plan in plans or []:
        cfg = plan.cfg or {}
        role = plan.role
        events.append({
            "time_s": 0.0,
            "event": "VEHICLE_SCRIPT_START",
            "key": "vehicle_script_start:{0}".format(role),
            "role": role,
            "lane": cfg.get("lane"),
            "speed_kmh": cfg.get("speed_kmh"),
            "control_mode": getattr(plan, "control_mode", ""),
        })
        for idx, point in enumerate(cfg.get("speed_points", []) or []):
            events.append({
                "time_s": max(0.0, _as_float(point.get("time_s"), 0.0)),
                "event": "VEHICLE_SPEED_POINT",
                "key": "vehicle_speed_point:{0}:{1}".format(role, idx),
                "role": role,
                "speed_kmh": point.get("speed_kmh"),
            })
        for idx, point in enumerate(cfg.get("gap_points", []) or []):
            events.append({
                "time_s": max(0.0, _as_float(point.get("time_s"), 0.0)),
                "event": "VEHICLE_GAP_POINT",
                "key": "vehicle_gap_point:{0}:{1}".format(role, idx),
                "role": role,
                "gap_m": point.get("gap_m"),
            })
        for idx, point in enumerate(cfg.get("time_gap_points", []) or []):
            events.append({
                "time_s": max(0.0, _as_float(point.get("time_s"), 0.0)),
                "event": "VEHICLE_TIME_GAP_POINT",
                "key": "vehicle_time_gap_point:{0}:{1}".format(role, idx),
                "role": role,
                "time_gap_s": point.get("time_gap_s"),
            })
        if cfg.get("stop_enabled", False):
            stop_t = max(0.0, _as_float(cfg.get("stop_time_s"), 0.0))
            stop_duration = max(0.0, _as_float(cfg.get("stop_duration_s"), 0.0))
            events.append({
                "time_s": stop_t,
                "event": "VEHICLE_STOP_START",
                "key": "vehicle_stop_start:{0}".format(role),
                "role": role,
                "duration_s": stop_duration,
            })
            events.append({
                "time_s": stop_t + stop_duration,
                "event": "VEHICLE_STOP_END",
                "key": "vehicle_stop_end:{0}".format(role),
                "role": role,
                "duration_s": stop_duration,
            })
    events.sort(key=lambda item: (item["time_s"], item["key"]))
    return events


def _wheel_radius_m(wheel):
    try:
        radius = float(getattr(wheel, "radius", 0.0) or 0.0)
    except Exception:
        return CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M
    # CARLA WheelPhysicsControl radius is exposed with the official default 30.0,
    # matching centimeters in UE vehicle physics.
    if radius > 3.0:
        return max(0.001, radius / 100.0)
    return max(0.001, radius)


def _physics_control_longitudinal_limits(physics_control):
    mass_kg = CARLA_OFFICIAL_DEFAULT_MASS_KG
    try:
        mass_kg = max(1.0, float(getattr(physics_control, "mass", mass_kg) or mass_kg))
    except Exception:
        pass

    wheels = []
    try:
        wheels = list(getattr(physics_control, "wheels", []) or [])
    except Exception:
        wheels = []
    if not wheels:
        wheels = [None] * CARLA_OFFICIAL_DEFAULT_WHEEL_COUNT

    radius_values = []
    brake_force_n = 0.0
    tire_friction_values = []
    for wheel in wheels:
        radius_m = CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M if wheel is None else _wheel_radius_m(wheel)
        radius_values.append(radius_m)
        try:
            brake_torque_nm = float(getattr(wheel, "max_brake_torque", CARLA_OFFICIAL_DEFAULT_MAX_BRAKE_TORQUE_NM) or 0.0)
        except Exception:
            brake_torque_nm = CARLA_OFFICIAL_DEFAULT_MAX_BRAKE_TORQUE_NM
        try:
            tire_friction_values.append(float(getattr(wheel, "tire_friction", CARLA_OFFICIAL_DEFAULT_TIRE_FRICTION) or 0.0))
        except Exception:
            tire_friction_values.append(CARLA_OFFICIAL_DEFAULT_TIRE_FRICTION)
        brake_force_n += max(0.0, brake_torque_nm) / max(0.001, radius_m)

    mean_radius_m = sum(radius_values) / max(1, len(radius_values))
    mean_tire_friction = sum(tire_friction_values) / max(1, len(tire_friction_values))
    tire_limit_mps2 = max(0.0, mean_tire_friction) * CARLA_STANDARD_GRAVITY_MPS2
    brake_limit_mps2 = min(brake_force_n / mass_kg, tire_limit_mps2)

    max_engine_torque_nm = CARLA_OFFICIAL_DEFAULT_ENGINE_TORQUE_NM
    try:
        torque_curve = list(getattr(physics_control, "torque_curve", []) or [])
        torque_values = [abs(float(getattr(item, "y", 0.0) or 0.0)) for item in torque_curve]
        torque_values = [value for value in torque_values if value > 0.0]
        if torque_values:
            max_engine_torque_nm = max(torque_values)
    except Exception:
        pass
    final_ratio = CARLA_OFFICIAL_DEFAULT_FINAL_RATIO
    try:
        final_ratio = abs(float(getattr(physics_control, "final_ratio", final_ratio) or final_ratio))
    except Exception:
        pass
    gear_ratio = 1.0
    try:
        forward_gears = list(getattr(physics_control, "forward_gears", []) or [])
        gear_values = [abs(float(getattr(gear, "ratio", 0.0) or 0.0)) for gear in forward_gears]
        gear_values = [value for value in gear_values if value > 0.0]
        if gear_values:
            gear_ratio = max(gear_values)
    except Exception:
        pass
    drive_force_n = max_engine_torque_nm * max(0.0, final_ratio) * max(0.0, gear_ratio) / max(0.001, mean_radius_m)
    accel_limit_mps2 = min(drive_force_n / mass_kg, tire_limit_mps2)

    return {
        "accel_mps2": max(0.001, accel_limit_mps2),
        "decel_mps2": max(0.001, brake_limit_mps2),
        "source": "actor.get_physics_control()",
    }


def _carla_official_default_longitudinal_limits():
    class _DefaultWheel(object):
        radius = CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M
        max_brake_torque = CARLA_OFFICIAL_DEFAULT_MAX_BRAKE_TORQUE_NM
        tire_friction = CARLA_OFFICIAL_DEFAULT_TIRE_FRICTION

    class _DefaultPhysics(object):
        mass = CARLA_OFFICIAL_DEFAULT_MASS_KG
        wheels = [_DefaultWheel()] * CARLA_OFFICIAL_DEFAULT_WHEEL_COUNT
        torque_curve = []
        final_ratio = CARLA_OFFICIAL_DEFAULT_FINAL_RATIO
        forward_gears = []

    limits = _physics_control_longitudinal_limits(_DefaultPhysics())
    limits["source"] = "CARLA official default VehiclePhysicsControl/WheelPhysicsControl"
    return limits


def _plan_longitudinal_limits(plan):
    actor = getattr(plan, "actor", None)
    if actor is not None and hasattr(actor, "get_physics_control"):
        try:
            return _physics_control_longitudinal_limits(actor.get_physics_control())
        except Exception:
            pass
    return _carla_official_default_longitudinal_limits()


def _time_gap_transition_design_issues(plan, ego_speed_mps):
    cfg = plan.cfg or {}
    points = []
    for point in cfg.get("time_gap_points", []) or []:
        try:
            points.append({
                "time_s": max(0.0, float(point.get("time_s", 0.0) or 0.0)),
                "time_gap_s": float(point.get("time_gap_s", 0.0) or 0.0),
            })
        except Exception:
            continue
    if not points:
        return []
    points.sort(key=lambda item: item["time_s"])
    reference_speed_mps = max(0.001, float(ego_speed_mps or 0.0))
    physics_limits = _plan_longitudinal_limits(plan)
    initial_gap_s = _time_gap_initial_bumper_gap_m(plan) / reference_speed_mps
    anchors = []
    if points[0]["time_s"] > 0.0:
        anchors.append((0.0, initial_gap_s))
    for point in points:
        anchors.append((point["time_s"], point["time_gap_s"]))

    issues = []
    prev_t, prev_gap_s = anchors[0]
    for cur_t, cur_gap_s in anchors[1:]:
        dt = cur_t - prev_t
        if dt <= 0.0:
            issues.append({
                "issue": "non_increasing_time_gap_point",
                "severity": "warning",
                "start_t": prev_t,
                "end_t": cur_t,
                "from_time_gap_s": prev_gap_s,
                "to_time_gap_s": cur_gap_s,
                "duration_s": dt,
                "implied_accel_mps2": 0.0,
                "recommended_min_duration_s": 0.0,
            })
            prev_t, prev_gap_s = cur_t, cur_gap_s
            continue
        delta_gap_m = (cur_gap_s - prev_gap_s) * reference_speed_mps
        implied_accel_mps2 = 2.0 * delta_gap_m / (dt * dt)
        abs_accel_mps2 = abs(implied_accel_mps2)
        hard_limit_mps2 = float(physics_limits["decel_mps2"] if implied_accel_mps2 < 0.0 else physics_limits["accel_mps2"])
        warning_limit_mps2 = hard_limit_mps2 * TIME_GAP_WARNING_LIMIT_RATIO
        if abs_accel_mps2 > warning_limit_mps2:
            issues.append({
                "issue": "rapid_time_gap_transition",
                "severity": "severe" if abs_accel_mps2 > hard_limit_mps2 else "warning",
                "start_t": prev_t,
                "end_t": cur_t,
                "from_time_gap_s": prev_gap_s,
                "to_time_gap_s": cur_gap_s,
                "duration_s": dt,
                "implied_accel_mps2": implied_accel_mps2,
                "physics_limit_mps2": hard_limit_mps2,
                "warning_limit_mps2": warning_limit_mps2,
                "physics_limit_source": physics_limits["source"],
                "recommended_min_duration_s": (
                    2.0 * abs(delta_gap_m) / max(0.001, warning_limit_mps2)
                ) ** 0.5,
            })
        prev_t, prev_gap_s = cur_t, cur_gap_s
    return issues


def _emit_scenario_design_warnings(plans, ego_speed_mps, experiment_tor_windows, marker_outlet=None):
    deadlines = []
    for window in experiment_tor_windows or []:
        try:
            deadlines.append(float(window.get("deadline_s")))
        except Exception:
            pass
    earliest_deadline_s = min(deadlines) if deadlines else None
    for plan in plans or []:
        if plan.role == "ego":
            continue
        cfg = plan.cfg or {}
        time_gap_points = cfg.get("time_gap_points", []) or []
        if not time_gap_points:
            continue
        has_lane_change = bool(cfg.get("lane_change_enabled", False) or cfg.get("lane_change_points", []))
        for point in time_gap_points:
            try:
                time_gap_s = float(point.get("time_gap_s"))
            except Exception:
                continue
            if abs(time_gap_s) >= TIME_GAP_NEAR_ZERO_S:
                continue
            scheduled_t = max(0.0, _as_float(point.get("time_s"), 0.0))
            bumper_gap_m = time_gap_s * max(0.001, float(ego_speed_mps or 0.0))
            issue = "near_zero_time_gap"
            if abs(time_gap_s) <= 0.05:
                issue = "zero_time_gap_collision_target"
            recommendation = "use_gap_points_or_time_gap_above_0.8s_for_stable_cut_in"
            if has_lane_change:
                recommendation = "avoid_zero_time_gap_during_lane_change_or_expect_braking_stall_collision"
            after_deadline = earliest_deadline_s is not None and scheduled_t > earliest_deadline_s
            _push_marker(
                marker_outlet,
                "SCENARIO_DESIGN_WARNING",
                scenario_t=0.0,
                role=plan.role,
                source="time_gap_points",
                issue=issue,
                scheduled_t=scheduled_t,
                time_gap_s=time_gap_s,
                bumper_gap_m=bumper_gap_m,
                after_tor_deadline=after_deadline,
                recommendation=recommendation,
            )
        for issue_info in _time_gap_transition_design_issues(plan, ego_speed_mps):
            scheduled_t = max(0.0, _as_float(issue_info.get("end_t"), 0.0))
            after_deadline = earliest_deadline_s is not None and scheduled_t > earliest_deadline_s
            _push_marker(
                marker_outlet,
                "SCENARIO_DESIGN_WARNING",
                scenario_t=0.0,
                role=plan.role,
                source="time_gap_points",
                issue=issue_info.get("issue"),
                severity=issue_info.get("severity"),
                scheduled_t=scheduled_t,
                start_t=issue_info.get("start_t"),
                end_t=issue_info.get("end_t"),
                from_time_gap_s=issue_info.get("from_time_gap_s"),
                to_time_gap_s=issue_info.get("to_time_gap_s"),
                duration_s=issue_info.get("duration_s"),
                implied_accel_mps2=issue_info.get("implied_accel_mps2"),
                physics_limit_mps2=issue_info.get("physics_limit_mps2"),
                warning_limit_mps2=issue_info.get("warning_limit_mps2"),
                physics_limit_source=issue_info.get("physics_limit_source"),
                recommended_min_duration_s=issue_info.get("recommended_min_duration_s"),
                after_tor_deadline=after_deadline,
                recommendation="extend_time_gap_transition_or_use_speed_mode_if_vehicle_dynamics_are_the_variable",
            )


def _build_explicit_tor_alert_events(tor_alerts, tor_aoi_screen, tor_aoi_grid):
    events = []
    for idx, alert in enumerate(tor_alerts or []):
        try:
            time_s = max(0.0, float(alert.get("time_s", 0.0) or 0.0))
        except Exception:
            continue
        grid = alert.get("aoi_grid", tor_aoi_grid)
        events.append({
            "time_s": time_s,
            "event": "TOR_ALERT_ONSET",
            "key": "tor_alert:explicit:{0}".format(idx),
            "role": alert.get("role", "session"),
            "delay_after_fpe_s": alert.get("delay_after_fpe_s"),
            "aoi_screen": alert.get("aoi_screen", tor_aoi_screen),
            "aoi_grid": grid,
            "aoi_x": 1.0 / 6.0 if str(grid).lower() in ("middle_left", "middle-left") else None,
            "aoi_y": 0.5 if str(grid).lower() in ("middle_left", "middle-left") else None,
            "source": "explicit_tor_onset",
        })
    return events


def _emit_scheduled_markers(marker_outlet, events, fired_keys, t):
    if marker_outlet is None:
        return
    for event in events:
        if event["key"] in fired_keys:
            continue
        if t + 1e-6 < event["time_s"]:
            continue
        fired_keys.add(event["key"])
        fields = dict(event)
        fields.pop("event", None)
        fields.pop("key", None)
        fields["scenario_t"] = t
        fields["scheduled_t"] = event["time_s"]
        _push_marker(marker_outlet, event["event"], **fields)


def _tor_resolution_pending(windows, active_actions):
    """True while a fired TOR interaction still needs operator resolution.

    A window counts as pending from alert onset until AUTO ON is resumed
    (manual takeover + return-to-auto, or missed-TOR fallback + recovery).
    Collision-failed manual takeovers are excluded because the collision
    monitor already pauses the scenario for operator confirmation.
    """
    for action in active_actions or []:
        if not action.get("complete"):
            return True
    for window in windows or []:
        if not window.get("shown"):
            continue
        if window.get("manual_collision_failed"):
            continue
        if not window.get("auto_resumed"):
            return True
    return False


def _play_once(args, plans, lane_paths, world, ego_plan, fixed_delta, total_time_s, ego_speed_mps,
               traffic_manager=None, takeover_requests=None, obstacle_configs=None,
               pedestrian_configs=None, spawned_actors=None, marker_outlet=None,
               tor_alerts=None, experiment_events=None, queen_vehicles=None):
    profile_followup_setup = (
        bool(getattr(args, "continuous_session", False))
        and int(getattr(args, "_playlist_index", 0) or 0) > 0
    )
    profile_start = time.monotonic()
    ego_initial_gap = ego_plan.initial_s
    use_wall_clock = args.time_sync_mode == "adaptive"
    n_ticks = int(round(total_time_s / fixed_delta))
    if args.render_fps > 0:
        render_cap_s = 1.0 / args.render_fps
        if args.time_sync_mode == "adaptive":
            pace_per_tick_s = max(render_cap_s, fixed_delta)
        else:
            pace_per_tick_s = min(render_cap_s, fixed_delta)
    else:
        pace_per_tick_s = fixed_delta
    wall_start = time.monotonic()
    progress_last = -1
    setattr(args, "_last_run_collision", None)
    experiment_cfg = experiment_events if isinstance(experiment_events, dict) else {}
    # hold_until_tor_resolution needs the experiment-window machinery, so when
    # the flag is set also lift legacy takeover_requests / tor_alerts payloads
    # (e.g. adapter duration=auto output) into experiment TOR windows.
    build_experiment_windows = bool(experiment_cfg.get("tor_windows")) or (
        bool(experiment_cfg.get("hold_until_tor_resolution"))
        and bool(takeover_requests or tor_alerts)
    )
    experiment_tor_windows = _prepare_experiment_tor_windows(
        experiment_cfg,
        takeover_requests,
        tor_alerts,
        total_time_s,
        ego_speed_mps * 3.6,
    ) if build_experiment_windows else []
    if experiment_cfg.get("hold_until_tor_resolution") and not experiment_tor_windows:
        print(
            "WARNING: hold_until_tor_resolution is set but this scenario has no TOR "
            "windows / takeover requests; the scenario will end at total_time_s."
        )
    takeover_states = [] if experiment_tor_windows else _prepare_takeover_requests(takeover_requests)
    takeover_input_mode = _apply_takeover_input_mode(takeover_states, experiment_tor_windows, args)
    takeover_keys = []
    for req in takeover_states:
        for key in req.get("keys") or [req.get("key", "SPACE")]:
            if key not in takeover_keys:
                takeover_keys.append(key)
    for window in experiment_tor_windows:
        for key in window.get("keys") or []:
            if key not in takeover_keys:
                takeover_keys.append(key)
        if "SPACE" not in takeover_keys:
            takeover_keys.append("SPACE")
    detector_start = time.monotonic()
    key_detector = _build_takeover_edge_detector(takeover_keys, args)
    detector_elapsed = time.monotonic() - detector_start
    if takeover_states or experiment_tor_windows:
        if key_detector.available():
            print("Takeover input listener active for: {0}".format(key_detector.describe()))
        else:
            print("WARNING: takeover input listener is unavailable on this system: {0}".format(
                key_detector.describe()))
        if _takeover_mode_uses_g29(takeover_input_mode) and not getattr(key_detector, "g29_available", False):
            print("WARNING: Logitech G29 takeover input was requested but no G29 joystick is readable.")
    g29_manual_control_enabled = (
        bool(getattr(args, "g29_manual_control", True))
        and _takeover_mode_uses_g29(takeover_input_mode)
        and bool(getattr(key_detector, "g29_available", False))
    )
    if g29_manual_control_enabled:
        cruise_kmh = float(getattr(args, "g29_cruise_speed_kmh", 0.0) or 0.0)
        cruise_text = "{0:.1f} km/h".format(cruise_kmh) if cruise_kmh > 0.0 else "scenario ego speed"
        print("G29 manual steering control enabled: steer=wheel axis, pedals/buttons ignored, cruise={0}.".format(
            cruise_text))
        print("G29 cockpit steering visual bridge: {0}".format(
            getattr(args, "g29_visual_steer_file", "") or "(disabled)"))
        _write_g29_visual_steer(args, 0.0)
        _write_g29_python_state(args, detector=key_detector, active=False, steer=0.0)
    aoi_start = time.monotonic()
    aoi_feedback = AoiFeedbackReceiver(
        getattr(args, "aoi_feedback_host", "127.0.0.1"),
        getattr(args, "aoi_feedback_port", 17365),
        enabled=bool(takeover_states or experiment_tor_windows) and bool(getattr(args, "takeover_require_aoi", True)),
    )
    aoi_elapsed = time.monotonic() - aoi_start
    if use_wall_clock:
        print("Adaptive wall-clock scenario clock active: event t follows real wall time.")
    ego_script_s = ego_initial_gap
    ego_command_speed_mps = ego_speed_mps
    prev_t = 0.0
    tick_idx = 0
    hold_until_tor_resolution = bool(experiment_cfg.get("hold_until_tor_resolution")) and bool(experiment_tor_windows)
    tor_hold_max_s = max(0.0, _as_float(experiment_cfg.get("tor_hold_max_s"), 300.0))
    tor_hold_resume_grace_s = max(0.0, _as_float(experiment_cfg.get("tor_hold_resume_grace_s"), 5.0))
    tor_hold_active = False
    tor_hold_resolved_at = None
    max_wall_clock_ticks = int(math.ceil(total_time_s / max(0.001, min(pace_per_tick_s, fixed_delta)))) + 240
    if hold_until_tor_resolution:
        max_wall_clock_ticks += int(math.ceil(
            (tor_hold_max_s + tor_hold_resume_grace_s) / max(0.001, min(pace_per_tick_s, fixed_delta))
        ))
        print(
            "Scenario end policy: hold after {0:g}s until the take-over interaction completes "
            "(max +{1:g}s), then drive {2:g}s in AUTO before scenario end.".format(
                total_time_s, tor_hold_max_s, tor_hold_resume_grace_s))
    scheduled_events = _build_lane_change_events(
        plans,
        getattr(args, "tor_alert_delay_s", 0.5),
        getattr(args, "tor_aoi_screen", "2"),
        getattr(args, "tor_aoi_grid", "middle_left"),
    )
    scheduled_events.extend(_build_vehicle_behavior_events(plans))
    scheduled_events.sort(key=lambda item: item["time_s"])
    explicit_tor_events = _build_explicit_tor_alert_events(
        tor_alerts,
        getattr(args, "tor_aoi_screen", "2"),
        getattr(args, "tor_aoi_grid", "middle_left"),
    )
    if experiment_tor_windows:
        scheduled_events = [
            event for event in scheduled_events
            if event.get("event") != "TOR_ALERT_ONSET"
        ]
    elif explicit_tor_events:
        scheduled_events = [
            event for event in scheduled_events
            if event.get("event") != "TOR_ALERT_ONSET"
        ]
        scheduled_events.extend(explicit_tor_events)
        scheduled_events.sort(key=lambda item: item["time_s"])
    elif not takeover_states:
        # The scenario declares NO TOR content at all (no tor_windows, no
        # tor_alerts, no takeover_requests): run plain. Drop the lane-change-
        # synthesized TOR alerts so no AOI/adaptive prompts appear; FPE_ONSET
        # data markers are kept for analysis.
        synthesized = sum(1 for event in scheduled_events if event.get("event") == "TOR_ALERT_ONSET")
        if synthesized:
            scheduled_events = [
                event for event in scheduled_events
                if event.get("event") != "TOR_ALERT_ONSET"
            ]
            print(
                "No TOR requested by this scenario; suppressed {0} synthesized "
                "lane-change TOR alert(s) (plain run).".format(synthesized)
            )
    scheduled_events.extend(_build_experiment_scheduled_events(experiment_cfg))
    scheduled_events.sort(key=lambda item: item["time_s"])
    fired_marker_keys = set()
    active_experiment_actions = []
    ego_lane_context = {
        "lane": ego_plan.cfg.get("lane", LANE_MIDDLE),
        "rightmost_lane": LANE_OUTER_RIGHT if LANE_OUTER_RIGHT in lane_paths else LANE_RIGHT,
    }
    plan_by_role = dict((plan.role, plan) for plan in plans)
    obstacle_drop_events = _prepare_obstacle_drop_events(obstacle_configs, total_time_s)
    pedestrian_events = _prepare_pedestrian_events(pedestrian_configs, total_time_s)
    if pedestrian_events:
        print("Prepared {0} pedestrian crossing event(s).".format(len(pedestrian_events)))
    cleanup_registry = getattr(args, "_cleanup_registry", None)
    collision_monitor = CollisionMonitor(world, ego_plan.actor, marker_outlet=marker_outlet,
        owned_actors=cleanup_registry["sensors"] if cleanup_registry is not None else None)
    collision_break_info = None
    _emit_scenario_design_warnings(plans, ego_speed_mps, experiment_tor_windows, marker_outlet=marker_outlet)
    distance_threshold_m = max(0.0, float(getattr(args, "distance_crash_threshold_m", 0.0) or 0.0))
    distance_role = getattr(args, "distance_crash_role", "vehicle_1")
    distance_plan = None
    distance_lc_start_s = None
    distance_threshold_hit = False
    g29_manual_debug_next_s = 0.0
    g29_manual_unavailable_logged = False
    tor_fallback_body_debug_next_s = 0.0
    if distance_threshold_m > 0.0:
        for plan in plans:
            if plan.role == distance_role:
                distance_plan = plan
                distance_lc_start_s = _first_lane_change_start_s(plan)
                break
        if distance_plan is None:
            print("WARNING: distance diagnostic role not found: {0}".format(distance_role))
        else:
            print(
                "Distance diagnostic active: ego vs {0}, threshold={1:.2f}m, lane_change_start={2}".format(
                    distance_role,
                    distance_threshold_m,
                    "{0:.2f}s".format(distance_lc_start_s) if distance_lc_start_s is not None else "(none)",
                )
            )
    if profile_followup_setup:
        print(
            "Continuous playlist follow-up play setup profile: "
            "input={0:.3f}s aoi_socket={1:.3f}s total_before_start_marker={2:.3f}s".format(
                detector_elapsed,
                aoi_elapsed,
                time.monotonic() - profile_start,
            )
        )
    _push_marker(
        marker_outlet,
        "SCENARIO_PLAY_START",
        scenario_t=0.0,
        total_time_s=total_time_s,
        ego_speed_kmh=ego_speed_mps * 3.6,
    )
    if experiment_cfg.get("standard_markers_enabled", True):
        _push_marker(
            marker_outlet,
            "SCENARIO_START",
            scenario_t=0.0,
            total_time_s=total_time_s,
            ego_speed_kmh=ego_speed_mps * 3.6,
        )
    _pre_spawn_pedestrian_events(
        pedestrian_events,
        world,
        lane_paths,
        spawned_actors,
        marker_outlet,
    )

    t = 0.0
    while True:
        if _playback_stop_requested(args):
            print("Playback cancellation requested; cleaning up.")
            break
        tick_wall_start = time.monotonic()
        if use_wall_clock:
            t = 0.0 if tick_idx == 0 else time.monotonic() - wall_start
            if not tor_hold_active:
                t = min(total_time_s, t)
            dt_scenario = max(0.0, t - prev_t)
        else:
            t = tick_idx * fixed_delta
            if not tor_hold_active:
                t = min(total_time_s, t)
            dt_scenario = fixed_delta
        if experiment_tor_windows:
            _apply_aoi_feedback_to_experiment_tor_windows(
                experiment_tor_windows,
                aoi_feedback,
                t,
                marker_outlet=marker_outlet,
            )
            new_actions = _update_experiment_tor_windows(
                experiment_tor_windows,
                key_detector,
                t,
                ego_command_speed_mps,
                _actor_speed_mps(ego_plan.actor),
                ego_speed_mps,
                ego_lane_context,
                marker_outlet=marker_outlet,
                require_aoi=bool(getattr(args, "takeover_require_aoi", True)),
                collision_monitor=collision_monitor,
                ego_plan=ego_plan,
                plan_by_role=plan_by_role,
                runtime_args=args,
            )
            active_experiment_actions.extend(new_actions)
        else:
            _apply_aoi_feedback_to_takeover_requests(
                takeover_states,
                aoi_feedback,
                t,
                marker_outlet=marker_outlet,
            )
            ego_command_speed_mps = _update_takeover_requests(
                takeover_states,
                key_detector,
                t,
                ego_command_speed_mps,
                _actor_speed_mps(ego_plan.actor),
                marker_outlet=marker_outlet,
                require_aoi=bool(getattr(args, "takeover_require_aoi", True)),
                runtime_args=args,
            )
        _emit_scheduled_markers(marker_outlet, scheduled_events, fired_marker_keys, t)
        takeover_force_brake = _takeover_force_brake_active(takeover_states)
        manual_control_active = any(
            window.get("manual_active") and not window.get("auto_resumed")
            for window in experiment_tor_windows
        )
        tor_detach_roles = _tor_support_detach_roles(experiment_tor_windows)
        tor_traffic_safety_active = _tor_traffic_safety_active(experiment_tor_windows)

        ego_lp = lane_paths[ego_lane_context.get("lane", ego_plan.cfg["lane"])]
        expected_ego_s = ego_script_s
        ego_action_target_xf = None
        ego_action_lane = ego_lane_context.get("lane", ego_plan.cfg["lane"])
        gentle_lane_action_control = False
        active_pull_over_action = None
        if active_experiment_actions:
            action_sample_s = ego_s if ego_s is not None else expected_ego_s
            ego_command_speed_mps, ego_action_target_xf, ego_action_lane, active_experiment_actions = _update_experiment_actions(
                active_experiment_actions,
                t,
                ego_command_speed_mps,
                ego_lane_context,
                lane_paths,
                action_sample_s,
                marker_outlet=marker_outlet,
                windows_by_name=dict(
                    (window.get("name"), window) for window in experiment_tor_windows
                ),
            )
            for action in active_experiment_actions:
                if action.get("type") == "brake_pull_over" and not action.get("complete"):
                    active_pull_over_action = action
                    break
            gentle_lane_action_control = any(
                action.get("type") in ("brake_pull_over", "return_to_auto_lane") and not action.get("complete")
                for action in active_experiment_actions
            )
            ego_lp = lane_paths[ego_action_lane]
        if manual_control_active:
            try:
                if hasattr(ego_plan.actor, "set_autopilot"):
                    ego_plan.actor.set_autopilot(False)
                if not g29_manual_control_enabled:
                    _release_vehicle_brake(ego_plan.actor)
            except Exception:
                pass
            if g29_manual_control_enabled:
                g29_status = _apply_g29_manual_control(
                    ego_plan.actor,
                    key_detector,
                    ego_command_speed_mps if ego_command_speed_mps > 0.0 else ego_speed_mps,
                    args,
                )
                if g29_status is None and not g29_manual_unavailable_logged:
                    print(
                        "WARNING: G29 manual ctrl unavailable for ego actor: "
                        "apply_control={0} set_target_velocity={1} set_target_angular_velocity={2}".format(
                            int(hasattr(ego_plan.actor, "apply_control")),
                            int(hasattr(ego_plan.actor, "set_target_velocity")),
                            int(hasattr(ego_plan.actor, "set_target_angular_velocity")),
                        )
                    )
                    g29_manual_unavailable_logged = True
                if g29_status is not None and t >= g29_manual_debug_next_s:
                    print(
                        "G29 manual ctrl: raw_steer={0:.3f} steer={1:.3f} "
                        "speed={2:.1f}/{3:.1f}km/h throttle={4:.2f} brake={5:.2f} "
                        "api=vel:{6} yaw:{7} veh:{8}".format(
                            g29_status["raw_steer"],
                            g29_status["steer"],
                            g29_status["current_speed_mps"] * 3.6,
                            g29_status["target_speed_mps"] * 3.6,
                            g29_status["throttle"],
                            g29_status["brake"],
                            int(bool(g29_status.get("velocity_control"))),
                            int(bool(g29_status.get("angular_control"))),
                            int(bool(g29_status.get("vehicle_control"))),
                        )
                    )
                    g29_manual_debug_next_s = t + 1.0
            ego_loc = ego_plan.actor.get_transform().location
            actual_lane, actual_s = _nearest_lane_name(
                lane_paths,
                ego_loc,
                fallback_lane=ego_lane_context.get("lane", ego_plan.cfg["lane"]),
                fallback_s=expected_ego_s,
            )
            ego_lane_context["lane"] = actual_lane
            ego_lp = lane_paths[actual_lane]
            ego_s = actual_s
            expected_ego_s = actual_s
        else:
            if g29_manual_control_enabled:
                _write_g29_visual_steer(args, 0.0)
                _write_g29_python_state(args, detector=key_detector, active=False, steer=0.0)
            if ego_plan.control_mode == "kinematic":
                ego_s = expected_ego_s
                ego_xf = ego_action_target_xf if ego_action_target_xf is not None else ego_lp.sample(ego_s)
                _apply_transform(ego_plan.actor, ego_xf)
            elif ego_plan.control_mode == "velocity":
                if takeover_force_brake:
                    _apply_safe_brake(ego_plan.actor)
                elif ego_action_target_xf is not None:
                    _release_vehicle_brake(ego_plan.actor)
                    _set_experiment_lane_target_velocity(
                        ego_plan.actor,
                        ego_action_target_xf,
                        ego_command_speed_mps,
                        gentle_pull_over=gentle_lane_action_control,
                    )
                else:
                    _release_vehicle_brake(ego_plan.actor)
                    _set_velocity_follow(ego_plan.actor, ego_lp, expected_ego_s, ego_command_speed_mps)
                ego_s = ego_lp.nearest_s(
                    ego_plan.actor.get_transform().location,
                    fallback_s=expected_ego_s,
                    window_m=80.0,
                )
            else:
                if takeover_force_brake:
                    _apply_safe_brake(ego_plan.actor)
                elif ego_action_target_xf is not None:
                    _release_vehicle_brake(ego_plan.actor)
                    _set_experiment_lane_target_velocity(
                        ego_plan.actor,
                        ego_action_target_xf,
                        ego_command_speed_mps,
                        gentle_pull_over=gentle_lane_action_control,
                    )
                else:
                    _maybe_fire_tm_lane_changes(ego_plan, traffic_manager, t)
                ego_s = ego_lp.nearest_s(
                    ego_plan.actor.get_transform().location,
                    fallback_s=expected_ego_s,
                    window_m=80.0,
                )

        if (
            getattr(args, "tor_fallback_body_debug", False)
            and active_pull_over_action is not None
            and t >= tor_fallback_body_debug_next_s
        ):
            _print_tor_fallback_body_debug(
                world,
                ego_plan.actor,
                ego_lp,
                ego_s,
                expected_ego_s,
                ego_action_target_xf,
                active_pull_over_action,
                t,
            )
            tor_fallback_body_debug_next_s = t + max(
                0.01,
                float(getattr(args, "tor_fallback_body_debug_interval_s", 0.25) or 0.25),
            )

        ego_actual_speed_mps = _actor_speed_mps(ego_plan.actor)
        # During a TOR hold the scripted timeline is over: freeze NPC script
        # time at total_time_s so scripted vehicles hold their end-of-script
        # targets instead of racing toward the sampled lane-path end.
        npc_script_t = min(t, total_time_s) if tor_hold_active else t
        for plan in plans:
            if plan.role == "ego":
                continue
            if plan.role in tor_detach_roles:
                _detach_tor_support_vehicle(
                    plan,
                    traffic_manager=traffic_manager,
                    marker_outlet=marker_outlet,
                    scenario_t=t,
                )
                continue
            if not plan.kinematic:
                if plan.control_mode == "traffic-manager":
                    _maybe_fire_tm_lane_changes(plan, traffic_manager, npc_script_t)
                elif plan.control_mode == "vehicle-control":
                    target_xf, _target_s, _lane_state = _vehicle_control_script_target(
                        plan, lane_paths, ego_s, npc_script_t, ego_speed_mps, ego_actual_speed_mps)
                    base_speed_mps = plan.speed_mps if plan.speed_mps > 0.0 else ego_speed_mps
                    speed_mps = _scripted_command_speed_mps(
                        plan,
                        npc_script_t,
                        base_speed_mps,
                        ego_speed_mps,
                        ego_actual_speed_mps,
                    )
                    precision_control = bool(
                        plan.cfg.get("time_gap_points", [])
                        and not plan.cfg.get("gap_points", [])
                    )
                    _release_vehicle_brake(plan.actor)
                    _apply_vehicle_control_toward_transform(
                        plan.actor,
                        target_xf,
                        speed_mps,
                        precision=precision_control,
                    )
                elif plan.control_mode == "velocity-script":
                    target_xf, _target_s, _lane_state = _scripted_target_transform(
                        plan, lane_paths, ego_s, npc_script_t, ego_speed_mps)
                    base_speed_mps = plan.speed_mps if plan.speed_mps > 0.0 else ego_speed_mps
                    speed_mps = _scripted_command_speed_mps(
                        plan,
                        npc_script_t,
                        base_speed_mps,
                        ego_speed_mps,
                        ego_actual_speed_mps,
                    )
                    _release_vehicle_brake(plan.actor)
                if plan.control_mode == "velocity-script":
                    _set_velocity_toward_transform(plan.actor, target_xf, speed_mps)
                continue
            xf, _target_s, _lane_state = _scripted_target_transform(
                plan, lane_paths, ego_s, npc_script_t, ego_speed_mps)
            _apply_transform(plan.actor, xf)

        _update_obstacle_drop_events(
            obstacle_drop_events,
            plan_by_role,
            world,
            t,
            total_time_s,
            spawned_actors,
            marker_outlet,
        )
        _update_pedestrian_events(
            pedestrian_events,
            world,
            lane_paths,
            t,
            spawned_actors,
            marker_outlet,
        )
        _update_queen_vehicles(
            queen_vehicles,
            lane_paths,
            plans,
            ego_s,
            args,
            traffic_manager=traffic_manager,
            tor_safety_active=tor_traffic_safety_active,
        )
        _push_ego_telemetry(
            marker_outlet,
            world,
            ego_plan,
            ego_lp,
            ego_s,
            expected_ego_s,
            t,
            tick_idx,
            manual_control_active=manual_control_active,
        )

        _draw_tor_vehicle_aoi_visuals(
            world,
            experiment_tor_windows,
            ego_plan,
            plan_by_role,
            t,
            args=args,
        )

        if distance_plan is not None and not distance_threshold_hit:
            try:
                dist_m = _location_distance_m(
                    ego_plan.actor.get_transform().location,
                    distance_plan.actor.get_transform().location,
                )
                if dist_m <= distance_threshold_m:
                    distance_threshold_hit = True
                    since_lc = None
                    if distance_lc_start_s is not None:
                        since_lc = t - distance_lc_start_s
                    print(
                        "DISTANCE_THRESHOLD_REACHED role={0} threshold={1:.2f}m "
                        "distance={2:.2f}m scenario_t={3:.2f}s since_lane_change_start={4}".format(
                            distance_role,
                            distance_threshold_m,
                            dist_m,
                            t,
                            "{0:.2f}s".format(since_lc) if since_lc is not None else "(unknown)",
                        )
                    )
                    _push_marker(
                        marker_outlet,
                        "DISTANCE_THRESHOLD_REACHED",
                        role=distance_role,
                        threshold_m=distance_threshold_m,
                        distance_m=dist_m,
                        scenario_t=t,
                        since_lane_change_start_s=since_lc,
                    )
            except Exception as exc:
                print("WARNING: distance diagnostic failed: {0}".format(exc))
                distance_plan = None

        if args.world_mode == "async-visual":
            # UE4 is free-running in async-visual mode; this loop is only the
            # scenario controller. Avoid world.tick() here because it would
            # re-gate the renderer from Python.
            pass
        else:
            world.tick()
        if manual_control_active:
            ego_script_s = ego_s
        else:
            ego_script_s += ego_command_speed_mps * dt_scenario
        progress_pct = int(min(t, total_time_s) / total_time_s * 100)
        if progress_pct != progress_last and progress_pct % 10 == 0:
            wall = time.monotonic() - wall_start
            print("  t={0:.2f}s ({1:3d}%)  wall={2:.2f}s".format(t, progress_pct, wall))
            _print_vehicle_telemetry(
                plans,
                lane_paths,
                world,
                ego_plan,
                ego_s,
                expected_ego_s,
                t,
                ego_speed_mps,
            )
            progress_last = progress_pct

        if collision_monitor is not None and collision_monitor.snapshot_count() > 0:
            collision_break_info = collision_monitor.snapshot()
            collision_break_info["scenario_t"] = t
            setattr(args, "_last_run_collision", collision_break_info)
            if tor_hold_active:
                _push_marker(marker_outlet, "TOR_HOLD_END", scenario_t=t, reason="collision")
            _push_marker(
                marker_outlet,
                "COLLISION_RECOVERY_REQUIRED",
                scenario_t=t,
                collision_count=collision_break_info.get("count"),
                other_actor_id=collision_break_info.get("other_actor_id"),
                other_type_id=collision_break_info.get("other_type_id"),
                impulse=collision_break_info.get("impulse"),
            )
            print(
                "[t={0:.2f}s] collision detected; pausing scenario for cleanup/recovery confirmation.".format(t)
            )
            held_count = _hold_collision_scene_for_confirmation(plans, queen_vehicles=queen_vehicles)
            if held_count:
                print(
                    "Collision confirmation hold: froze/braked {0} actor(s) until popup is acknowledged.".format(
                        held_count
                    )
                )
            break

        prev_t = t
        if use_wall_clock and tick_idx >= max_wall_clock_ticks:
            print("WARNING: adaptive wall-clock loop hit safety tick limit.")
            break
        scenario_time_done = (t >= total_time_s) if use_wall_clock else (tick_idx >= n_ticks)
        if scenario_time_done:
            hold_pending = hold_until_tor_resolution and _tor_resolution_pending(
                experiment_tor_windows, active_experiment_actions
            )
            hold_elapsed_s = max(0.0, t - total_time_s)
            if hold_pending and hold_elapsed_s < tor_hold_max_s:
                tor_hold_resolved_at = None
                if not tor_hold_active:
                    tor_hold_active = True
                    _push_marker(
                        marker_outlet,
                        "TOR_HOLD_START",
                        scenario_t=t,
                        total_time_s=total_time_s,
                        hold_max_s=tor_hold_max_s,
                    )
                    print(
                        "[t={0:.2f}s] scenario time complete; holding scene until the "
                        "take-over interaction finishes (max +{1:g}s).".format(t, tor_hold_max_s)
                    )
            elif tor_hold_active and not hold_pending and tor_hold_resume_grace_s > 0.0:
                # Resolved during the hold: let AUTO driving settle for the grace
                # window before ending, instead of freezing the scene on the
                # participant's resume press.
                if tor_hold_resolved_at is None:
                    tor_hold_resolved_at = t
                    _push_marker(
                        marker_outlet,
                        "TOR_HOLD_RESOLVED",
                        scenario_t=t,
                        resume_drive_s=tor_hold_resume_grace_s,
                    )
                    print(
                        "[t={0:.2f}s] take-over interaction resolved; driving {1:g}s in AUTO "
                        "before scenario end.".format(t, tor_hold_resume_grace_s)
                    )
                if t - tor_hold_resolved_at >= tor_hold_resume_grace_s:
                    _push_marker(marker_outlet, "TOR_HOLD_END", scenario_t=t)
                    print("[t={0:.2f}s] post-resume AUTO drive complete; ending scenario.".format(t))
                    break
            else:
                if tor_hold_active:
                    if hold_pending:
                        _push_marker(
                            marker_outlet,
                            "TOR_HOLD_TIMEOUT",
                            scenario_t=t,
                            hold_max_s=tor_hold_max_s,
                        )
                        print(
                            "WARNING: TOR hold reached +{0:g}s without resolution; ending scenario.".format(
                                tor_hold_max_s
                            )
                        )
                    else:
                        _push_marker(marker_outlet, "TOR_HOLD_END", scenario_t=t)
                        print("[t={0:.2f}s] take-over interaction resolved; ending scenario.".format(t))
                elif hold_until_tor_resolution and hold_pending:
                    print(
                        "WARNING: hold_until_tor_resolution set but hold cap is 0; ending "
                        "with the take-over interaction unresolved."
                    )
                break

        if pace_per_tick_s > 0:
            spent = time.monotonic() - tick_wall_start
            remaining = pace_per_tick_s - spent
            if remaining > 0:
                time.sleep(remaining)
        tick_idx += 1
    if distance_plan is not None and not distance_threshold_hit:
        print(
            "DISTANCE_THRESHOLD_NOT_REACHED role={0} threshold={1:.2f}m total_time={2:.2f}s".format(
                distance_role,
                distance_threshold_m,
                total_time_s,
            )
        )
    setattr(args, "_tor_hold_used", tor_hold_active)
    cancelled = _playback_stop_requested(args)
    final_t = collision_break_info.get("scenario_t") if collision_break_info else (t if cancelled else max(total_time_s, t))
    if cancelled:
        args._cancelled_playback_time_s = final_t
    if experiment_cfg.get("standard_markers_enabled", True):
        _push_marker(
            marker_outlet,
            "SCENARIO_END",
            scenario_t=final_t,
            reason="collision" if collision_break_info else "cancelled" if cancelled else None,
        )
    _push_marker(
        marker_outlet,
        "SCENARIO_PLAY_END",
        scenario_t=final_t,
        reason="collision" if collision_break_info else "cancelled" if cancelled else None,
    )
    _write_tor_spotlight_file(args, False, t=final_t, force=True)
    _cleanup_obstacle_drop_events(obstacle_drop_events, spawned_actors, marker_outlet, final_t)
    _cleanup_pedestrian_events(pedestrian_events, spawned_actors, marker_outlet, final_t)
    if obstacle_drop_events or pedestrian_events:
        try:
            world.tick()
        except Exception:
            pass
    aoi_feedback.close()
    if collision_monitor is not None:
        if collision_monitor.snapshot_count() > 0 and getattr(args, "_last_run_collision", None) is None:
            collision_info = collision_monitor.snapshot()
            collision_info["scenario_t"] = final_t
            setattr(args, "_last_run_collision", collision_info)
        collision_monitor.close()
    _write_g29_visual_steer(args, 0.0)
    _write_g29_python_state(args, active=False, steer=0.0)


# ============================================================================
# Main
# ============================================================================

def _playback_stop_requested(args):
    event = getattr(args, "_stop_event", None)
    return event is not None and event.is_set()


def _destroy_spawned_actors(actors, errors=None):
    destroyed_any = False
    for actor in actors:
        try:
            if errors is not None and not actor.is_alive:
                continue
            _zero_actor_motion(actor)
            _apply_safe_brake(actor)
            destroyed = actor.destroy()
            if errors is not None and destroyed is False:
                raise RuntimeError("destroy() returned False")
            destroyed_any = True
        except Exception as exc:
            if errors is not None:
                errors.append("Actor {0}: {1}".format(actor.id, exc))
    return destroyed_any


def run(args, normalized):
    if getattr(args, "_stop_event", None) is not None:
        args._cancelled_playback_time_s = 0.0
    if _playback_stop_requested(args):
        return 0
    continuous_followup = (
        bool(getattr(args, "continuous_session", False))
        and int(getattr(args, "_playlist_index", 0) or 0) > 0
    )
    marker_outlet = ScenarioMarkerOutlet(args, normalized.get("scenario_name", args.name))
    print("Connecting to DReyeVR CARLA at " + args.host + ":" + str(args.port) + "...")
    _push_marker(marker_outlet, "SCENARIO_CONNECT_START", host=args.host, port=args.port)
    client = carla.Client(args.host, args.port)
    client.set_timeout(15.0)
    world = client.get_world()
    if _playback_stop_requested(args):
        marker_outlet.close()
        return 0
    expected_world = getattr(args, "_expected_world_id", None)
    if expected_world is not None and world.id != expected_world:
        print("ERROR: CARLA world changed. Refresh and select the ego again.", file=sys.stderr)
        marker_outlet.close()
        return 1
    cur_map = world.get_map().name
    print("Server version: " + client.get_server_version() + "  Map: " + cur_map)
    _push_marker(marker_outlet, "SCENARIO_CONNECTED", map=cur_map, server=client.get_server_version())

    target_map = normalized["map_name"]
    if _playback_stop_requested(args):
        marker_outlet.close()
        return 0
    if not cur_map.endswith(target_map):
        if not getattr(args, "_allow_map_load", True):
            print("ERROR: Connected map differs; borrowed servers cannot be reloaded.", file=sys.stderr)
            marker_outlet.close()
            return 1
        map_load_timeout_s = max(15.0, float(args.map_load_timeout_s or 180.0))
        if str(target_map).endswith("Town13"):
            load_hint = "this may take 60-120s"
        else:
            load_hint = "timeout {0:.0f}s".format(map_load_timeout_s)
        print("Loading " + target_map + "... (" + load_hint + ")")
        client.set_timeout(map_load_timeout_s)
        try:
            world = client.load_world(target_map)
        finally:
            client.set_timeout(max(30.0, min(map_load_timeout_s, 60.0)))
        time.sleep(2.0)
    weather_preset = normalized.get("weather_preset", "Default")
    if _playback_stop_requested(args):
        marker_outlet.close()
        return 0
    if (
        getattr(args, "continuous_session", False)
        and getattr(args, "_last_weather_preset", None) == weather_preset
    ):
        print("Weather preset unchanged for continuous playlist: " + str(weather_preset))
    else:
        apply_weather_preset(world, weather_preset)
        if getattr(args, "continuous_session", False):
            args._last_weather_preset = weather_preset

    try:
        tesla, duplicate_egos_destroyed = _select_scenario_ego(world, getattr(args, "ego_actor_id", None))
    except ValueError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    if tesla is None:
        print("ERROR: no DReyeVR Tesla (harplab.dreyevr_vehicle.*) found. "
              "Is this DReyeVR CARLA?", file=sys.stderr)
        return 1
    if duplicate_egos_destroyed:
        print(
            "DReyeVR ego cleanup: kept actor {0}, removed {1} duplicate ego actor(s).".format(
                tesla.id,
                duplicate_egos_destroyed,
            )
        )
    tesla = _ensure_dreyevr_ego_responsive(world, tesla)
    if tesla is None:
        print("ERROR: DReyeVR ego was not responsive and could not be respawned.", file=sys.stderr)
        return 1
    print("Found DReyeVR Tesla: id={0}  type={1}  role={2}".format(
        tesla.id, tesla.type_id, tesla.attributes.get("role_name", "(none)")))
    tesla_location = tesla.get_transform().location
    print("  current location: " + str(tesla_location))
    _push_marker(marker_outlet, "EGO_FOUND", actor_id=tesla.id, type_id=tesla.type_id)

    # Find highway lanes from a stable scenario anchor. By default this does
    # not use Tesla's current location, because the ego is left at the end of
    # each previous run and would otherwise move the next scenario start.
    start_mode = args.start_mode
    if args.no_teleport_ego and start_mode != "current":
        print("WARNING: --no-teleport-ego forces --start-mode current")
        start_mode = "current"

    lane_group, found_dist = None, None
    spawn_xodr = normalized.get("spawn_xodr")
    if start_mode == "fixed":
        if spawn_xodr:
            print("Using scenario spawn_xodr as scenario start anchor: " + str(spawn_xodr))
            lane_group, found_dist = find_mainroad_lane_group_from_xodr(
                world, spawn_xodr, min_lanes=3)
        if lane_group is None:
            lane_anchor = carla.Location(
                x=float(args.fixed_start_x),
                y=float(args.fixed_start_y),
                z=float(args.fixed_start_z),
            )
            print("Using fixed scenario start anchor: " + str(lane_anchor))
            print("Searching for a main-road lane group near fixed anchor...")
            lane_group, found_dist = find_mainroad_lane_group_near(
                world, lane_anchor, min_lanes=4,
                search_radius_m=600.0, step_m=10.0)
    elif start_mode == "current":
        lane_anchor = tesla_location
        print("Using current Tesla location as scenario start anchor.")
        print("Searching for a main-road lane group near Tesla...")
        if not _location_height_is_reasonable(world, lane_anchor.z):
            print("WARNING: Tesla height is abnormal (z={0:.1f}); ignoring current position.".format(
                lane_anchor.z))
        else:
            lane_group, found_dist = find_mainroad_lane_group_near(
                world, lane_anchor, min_lanes=4,
                search_radius_m=400.0, step_m=10.0)
    else:
        print("Using map spawn fallback as scenario start anchor.")

    if lane_group is None:
        print("WARNING: no usable highway lane group found from requested start anchor. Trying map spawns.")
        lane_group, spawn_idx = find_mainroad_lane_group_from_spawns(world, min_lanes=4)
        if lane_group is None:
            print("ERROR: no usable highway lane group found in map spawn points.", file=sys.stderr)
            return 1
        print("  lane group recovered from map spawn index {0}  lanes={1}".format(
            spawn_idx, len(lane_group)))
    else:
        print("  lane group found at offset {0:+.1f}m  lanes={1}".format(
            found_dist if found_dist else 0.0, len(lane_group)))
    print("  selecting Left/Middle/Right from highway mainline lanes only")
    if len(lane_group) >= 4:
        print("  extra outer lanes will be treated as emergency/shoulder/exit candidates")

    _normalize_runtime_vehicle_speeds(normalized)

    # Build lane paths from the left three through lanes.
    scenario_fixed_delta = float(normalized["fixed_delta_seconds"])
    visual_hz = max(1.0, float(args.visual_hz))
    visual_fixed_delta = 1.0 / visual_hz
    fixed_delta = max(scenario_fixed_delta, visual_fixed_delta)
    if fixed_delta > scenario_fixed_delta:
        print(
            "Capping scenario tick rate: fixed_delta {0:.4f}s -> {1:.4f}s ({2:.1f} Hz)".format(
                scenario_fixed_delta, fixed_delta, 1.0 / fixed_delta))
    total_time_s = normalized["total_time_s"]
    ego_speed_kmh = normalized["ego_speed_kmh"]
    ego_speed_mps = ego_speed_kmh / 3.6
    vehicles_cfg = normalized["vehicles"]
    pedestrian_configs = normalized.get("pedestrians", {})
    required_distance_m = _required_lane_path_distance_m(vehicles_cfg, total_time_s, ego_speed_mps)
    required_distance_m = max(required_distance_m, _required_pedestrian_path_distance_m(pedestrian_configs))
    hold_events_cfg = normalized.get("experiment_events") or {}
    if hold_events_cfg.get("hold_until_tor_resolution"):
        # The TOR hold can extend the run past total_time_s (grace drive in
        # AUTO plus some manual driving); give the sampled lane paths headroom.
        hold_grace_s = max(0.0, _as_float(hold_events_cfg.get("tor_hold_resume_grace_s"), 5.0))
        required_distance_m += ego_speed_mps * (hold_grace_s + 30.0)
    required_backward_distance_m = _required_lane_path_backward_distance_m(vehicles_cfg, ego_speed_mps)
    if _queen_vehicles_enabled(args):
        required_distance_m += _queen_vehicle_forward_padding_m(args)
        required_backward_distance_m = max(
            required_backward_distance_m,
            _queen_vehicle_required_backward_distance_m(args),
        )
    print("  Required lane path distance: {0:.1f}m".format(required_distance_m))
    if required_backward_distance_m > 0.0:
        print("  Required backward lane path distance: {0:.1f}m".format(
            required_backward_distance_m))
    lane_paths = build_left3_highway_lane_paths(
        lane_group,
        distance_m=required_distance_m,
        step_m=1.0,
        backward_distance_m=required_backward_distance_m,
    )
    print("  Lane samples: Left={0}  Middle={1}  Right={2}".format(
        len(lane_paths[LANE_LEFT].transforms),
        len(lane_paths[LANE_MIDDLE].transforms),
        len(lane_paths[LANE_RIGHT].transforms),
    ))

    # Get the ego config; teleport Tesla into scenario's ego.lane at initial_gap_m
    ego_cfg = vehicles_cfg.get("ego")
    if ego_cfg is None or not ego_cfg.get("enabled", False):
        print("ERROR: scenario has no enabled ego config.", file=sys.stderr)
        return 1

    ego_precise_script = (
        bool(ego_cfg.get("gap_points", []))
        or bool(ego_cfg.get("time_gap_points", []))
        or bool(ego_cfg.get("lane_change_points", []))
        or bool(ego_cfg.get("stop_enabled", False))
    )
    support_scripted_lane_change = _support_vehicle_has_scripted_lane_change(vehicles_cfg)
    if args.ego_mode == "kinematic":
        ego_control_mode = "kinematic"
    elif args.ego_mode == "traffic-manager":
        ego_control_mode = "traffic-manager" if hasattr(tesla, "set_autopilot") else "velocity"
        if ego_control_mode != "traffic-manager":
            print("WARNING: DReyeVR ego has no set_autopilot; using velocity-follow mode instead.")
    else:
        if ego_precise_script:
            ego_control_mode = "kinematic"
        elif hasattr(tesla, "set_target_velocity"):
            ego_control_mode = "velocity"
            if support_scripted_lane_change:
                print("INFO: support vehicle scripted lane change detected; ego stays in velocity lane-hold mode.")
            else:
                print("INFO: ego has no lane-change script; using velocity lane-hold mode.")
        elif hasattr(tesla, "set_autopilot"):
            ego_control_mode = "traffic-manager"
        else:
            ego_control_mode = "kinematic"
    ego_kinematic = (ego_control_mode == "kinematic")

    # Switch to synchronous mode before teleporting/spawning. This prevents a
    # visible render frame where ego/NPCs appear at an intermediate spawn pose
    # and then snap to the scripted t=0 pose.
    if _playback_stop_requested(args):
        marker_outlet.close()
        return 0
    original_settings = world.get_settings()
    sync_settings = carla.WorldSettings(
        synchronous_mode=True,
        fixed_delta_seconds=fixed_delta,
        no_rendering_mode=False,
    )
    settings_already_sync = (
        bool(getattr(original_settings, "synchronous_mode", False))
        and abs(float(original_settings.fixed_delta_seconds or 0.0) - float(fixed_delta)) < 1e-6
        and not bool(getattr(original_settings, "no_rendering_mode", False))
    )
    if settings_already_sync:
        print("World already in synchronous setup mode (fixed_delta=" + str(fixed_delta) + "s)")
    else:
        world.apply_settings(sync_settings)
    traffic_manager = client.get_trafficmanager(args.traffic_manager_port)
    traffic_manager.set_synchronous_mode(True)
    try:
        traffic_manager.set_random_device_seed(7)
        traffic_manager.set_hybrid_physics_mode(False)
    except Exception:
        pass
    if args.time_sync_mode == "adaptive" and args.world_mode == "sync":
        calibrated_fixed_delta = _calibrate_fixed_delta(
            world,
            fixed_delta,
            args.time_calibration_ticks,
        )
        if calibrated_fixed_delta > fixed_delta:
            print(
                "Adaptive high-FPS real-time mode: fixed_delta {0:.4f}s -> {1:.4f}s "
                "({2:.1f} Hz observed)".format(
                    fixed_delta,
                    calibrated_fixed_delta,
                    1.0 / calibrated_fixed_delta,
                )
            )
            fixed_delta = calibrated_fixed_delta
            sync_settings.fixed_delta_seconds = fixed_delta
            world.apply_settings(sync_settings)
            traffic_manager.set_synchronous_mode(True)
        else:
            print(
                "Adaptive high-FPS real-time mode: fixed_delta {0:.4f}s kept "
                "({1:.1f} Hz target is reachable)".format(
                    fixed_delta,
                    1.0 / fixed_delta,
                )
            )
    elif args.world_mode == "async-visual":
        print("Async-visual selected: using synchronous setup, then free-running UE4 during playback.")
    if not settings_already_sync:
        print("World switched to synchronous setup mode (fixed_delta=" + str(fixed_delta) + "s)")

    if _playback_stop_requested(args):
        world.apply_settings(original_settings)
        traffic_manager.set_synchronous_mode(bool(original_settings.synchronous_mode))
        marker_outlet.close()
        return 0
    if not args.no_teleport_ego:
        ego_lp_init = lane_paths[ego_cfg["lane"]]
        ego_init_xf = ego_lp_init.sample(float(ego_cfg.get("initial_gap_m", 0.0) or 0.0))
        print("Teleporting DReyeVR Tesla into lane=" + ego_cfg["lane"] + " ...")
        if tesla.type_id.startswith("harplab.dreyevr_vehicle"):
            try:
                tesla.set_simulate_physics(True)
            except Exception:
                pass
        else:
            tesla.set_simulate_physics(False)
        _apply_transform(tesla, ego_init_xf, lift_z=DREYEVR_EGO_TELEPORT_Z_OFFSET)
    if ego_control_mode != "kinematic":
        _force_release_vehicle_for_run(tesla, world, ticks=1 if continuous_followup else 2)

    # Build plans for ego + support vehicles
    plans = []
    ego_plan = VehiclePlan(
        "ego", ego_cfg, tesla,
        kinematic=ego_kinematic,
        control_mode=ego_control_mode,
    )
    plans.append(ego_plan)
    print("  ego mode=" + ego_plan.control_mode)
    _push_marker(marker_outlet, "EGO_READY", mode=ego_plan.control_mode, lane=ego_cfg["lane"],
                 speed_kmh=normalized["ego_speed_kmh"])
    cleanup_registry = getattr(args, "_cleanup_registry", None)
    spawned_actors = cleanup_registry["actors"] if cleanup_registry is not None else []
    queen_vehicles = []
    try:
        if _playback_stop_requested(args):
            return 0
        scenario_actor_name = normalized.get("scenario_name", getattr(args, "name", ""))
        _preload_playlist_actor_pool(args, world, lane_paths, skip_scenario_name=scenario_actor_name)
        actor_pool = _playlist_actor_pool(args) if _playlist_actor_pool_enabled(args) else None
        for role in ROLE_ORDER[1:]:
            if _playback_stop_requested(args):
                return 0
            cfg = vehicles_cfg.get(role)
            if not cfg or not cfg.get("enabled", False):
                continue
            bp = make_blueprint(world, role, cfg["blueprint_filter"])
            lp = lane_paths[cfg["lane"]]
            init_s = _initial_spawn_s_from_cfg(cfg, ego_speed_mps)
            xf = lp.sample(init_s)
            pool_key = _playlist_actor_key(scenario_actor_name, role)
            actor = actor_pool.get(pool_key) if actor_pool is not None else None
            reused_actor = _actor_is_usable(actor)
            if reused_actor:
                _zero_actor_motion(actor)
            else:
                lifted = _transform_with_lift(xf, SPAWN_CLEARANCE_Z_OFFSET)
                actor = world.try_spawn_actor(bp, lifted)
                if actor is None:
                    # Retry slightly ahead
                    xf2 = lp.sample(init_s + 5.0)
                    lifted2 = _transform_with_lift(xf2, SPAWN_CLEARANCE_Z_OFFSET)
                    actor = world.try_spawn_actor(bp, lifted2)
                if actor is None:
                    raise RuntimeError("Failed to spawn " + role)
                spawned_actors.append(actor)
                if actor_pool is not None:
                    actor_pool[pool_key] = actor
                    _playlist_owned_actors(args).append(actor)
            has_gap_or_stop_script = (
                bool(cfg.get("gap_points", []))
                or bool(cfg.get("time_gap_points", []))
                or bool(cfg.get("speed_points", []))
                or bool(cfg.get("stop_enabled", False))
            )
            has_lane_change_script = bool(cfg.get("lane_change_points", []))
            needs_scripted_lane_control = _needs_scripted_lane_control(cfg)
            if args.npc_mode == "kinematic":
                npc_control_mode = "kinematic"
            elif args.npc_mode == "traffic-manager":
                npc_control_mode = "traffic-manager"
            elif args.npc_mode == "vehicle-control":
                npc_control_mode = "vehicle-control"
            elif args.npc_mode == "velocity-script":
                npc_control_mode = "velocity-script"
            elif has_gap_or_stop_script or has_lane_change_script or needs_scripted_lane_control:
                npc_control_mode = "vehicle-control"
            else:
                npc_control_mode = "traffic-manager"
            use_kinematic = (npc_control_mode == "kinematic")

            if npc_control_mode == "kinematic":
                actor.set_simulate_physics(False)
                _apply_transform(actor, xf)
                mode_msg = "kinematic"
            elif npc_control_mode == "velocity-script":
                _place_physics_support_at_transform(actor, xf)
                mode_msg = "velocity-script"
            elif npc_control_mode == "vehicle-control":
                _place_physics_support_at_transform(actor, xf)
                mode_msg = "vehicle-control"
            else:
                _place_physics_support_at_transform(actor, xf)
                _configure_tm_vehicle(traffic_manager, actor, cfg.get("speed_kmh", normalized["ego_speed_kmh"]))
                mode_msg = "traffic-manager"

            plan = VehiclePlan(
                role, cfg, actor,
                kinematic=use_kinematic,
                control_mode=npc_control_mode,
            )
            plan.initial_s = init_s
            plan._last_control_desired_s = init_s
            plan._last_control_current_s = init_s
            plans.append(plan)
            if cfg.get("time_gap_points", []) and not cfg.get("gap_points", []):
                print(
                    "  {6} {0} (id={1}) in lane={2} initial_bumper_gap_m={3} "
                    "spawn_center_s={4:.2f} mode={5}".format(
                        role,
                        actor.id,
                        cfg["lane"],
                        _cfg_initial_bumper_gap_m(cfg, ego_speed_mps),
                        init_s,
                        mode_msg,
                        "reused" if reused_actor else "spawned",
                    )
                )
            elif cfg.get("initial_time_gap_s") not in (None, ""):
                print(
                    "  {6} {0} (id={1}) in lane={2} initial_time_gap_s={3} "
                    "initial_bumper_gap_m={4:.2f} spawn_center_s={5:.2f} mode={7}".format(
                        role,
                        actor.id,
                        cfg["lane"],
                        cfg.get("initial_time_gap_s"),
                        _cfg_initial_bumper_gap_m(cfg, ego_speed_mps),
                        init_s,
                        "reused" if reused_actor else "spawned",
                        mode_msg,
                    )
                )
            else:
                print("  " + ("reused " if reused_actor else "spawned ") + role
                      + " (id=" + str(actor.id) + ") in lane="
                      + cfg["lane"] + " initial_gap_m=" + str(init_s) + " mode=" + mode_msg)
            _push_marker(
                marker_outlet,
                "VEHICLE_REUSED" if reused_actor else "VEHICLE_SPAWNED",
                role=role,
                actor_id=actor.id,
                lane=cfg["lane"],
                initial_gap_m=init_s,
                initial_time_gap_s=cfg.get("initial_time_gap_s"),
                initial_bumper_gap_m=(
                    _cfg_initial_bumper_gap_m(cfg, ego_speed_mps)
                    if cfg.get("initial_time_gap_s") not in (None, "")
                    else None
                ),
                spawn_center_s=init_s,
                speed_kmh=cfg.get("speed_kmh", normalized["ego_speed_kmh"]),
                mode=mode_msg,
            )

        _configure_time_gap_bumper_references(plans, ego_plan)
        startup_warmup_ticks = 1 if continuous_followup else 3
        if continuous_followup:
            print("Continuous playlist follow-up: reducing setup warmup ticks to 1.")
        _reset_to_t0(plans, lane_paths, world, warmup_ticks=startup_warmup_ticks)
        for plan in plans:
            if plan.control_mode == "traffic-manager":
                ok = _configure_tm_vehicle(
                    traffic_manager, plan.actor,
                    plan.cfg.get("speed_kmh", normalized["ego_speed_kmh"]))
                if not ok and plan.role == "ego":
                    print("WARNING: ego Traffic Manager failed; falling back to velocity-follow ego.")
                    plan.control_mode = "velocity"
                    plan.kinematic = False
            elif plan.control_mode in ("velocity", "velocity-script", "vehicle-control"):
                _zero_actor_motion(plan.actor)
        queen_vehicles = _spawn_queen_vehicles(
            args,
            world,
            lane_paths,
            plans,
            ego_plan,
            traffic_manager,
            normalized["ego_speed_kmh"],
        )
        if args.move_spectator:
            _move_spectator_into_tesla(world, tesla)

        # ===== REPL =====
        iteration = 0
        while True:
            iteration += 1
            if iteration > 1:
                print("\n--- Resetting to t=0 ---")
                if args.world_mode == "async-visual":
                    world.apply_settings(sync_settings)
                    traffic_manager.set_synchronous_mode(True)
                _reset_to_t0(plans, lane_paths, world, warmup_ticks=startup_warmup_ticks)
                for plan in plans:
                    if not plan.kinematic:
                        plan._lane_change_fired.clear()
                    if plan.control_mode == "traffic-manager":
                        _configure_tm_vehicle(traffic_manager, plan.actor, plan.cfg.get("speed_kmh", normalized["ego_speed_kmh"]))
                    elif plan.control_mode in ("velocity", "velocity-script", "vehicle-control"):
                        _zero_actor_motion(plan.actor)
                _update_queen_vehicles(
                    queen_vehicles,
                    lane_paths,
                    plans,
                    ego_plan.initial_s,
                    args,
                    traffic_manager=traffic_manager,
                )
                if args.move_spectator:
                    _move_spectator_into_tesla(world, tesla)

            if args.auto_start and iteration == 1:
                pass
            else:
                print("")
                print("All vehicles parked at t=0. The DReyeVR Tesla cockpit is your view.")
                print("  (You can look around the cockpit before pressing Enter.)")
                print("")
                try:
                    ans = input("Press [Enter] to play, [q] to quit: ").strip().lower()
                except EOFError:
                    ans = "q"
                if ans in ("q", "quit", "exit"):
                    print("Quit requested.")
                    return 0

            _prime_dynamic_motion(plans, lane_paths, normalized["ego_speed_kmh"])
            if args.world_mode == "async-visual":
                _apply_async_visual_mode(world, traffic_manager)
            print("Playing " + normalized["scenario_name"] + " for "
                  + str(total_time_s) + "s ...")
            takeover_requests = [] if args.disable_takeover_requests else normalized.get("takeover_requests", [])
            if args.disable_takeover_requests:
                print("Diagnostic mode: takeover requests disabled; ego will not force-brake at TOR deadline.")
            _play_once(args, plans, lane_paths, world, ego_plan, fixed_delta,
                       total_time_s, ego_speed_mps, traffic_manager=traffic_manager,
                       takeover_requests=takeover_requests,
                       obstacle_configs=normalized.get("obstacles", {}),
                       pedestrian_configs=pedestrian_configs,
                       spawned_actors=spawned_actors,
                       marker_outlet=marker_outlet,
                       tor_alerts=normalized.get("tor_alerts", []),
                       experiment_events=normalized.get("experiment_events", {}),
                       queen_vehicles=queen_vehicles)
            collision_info = getattr(args, "_last_run_collision", None)
            if collision_info:
                print("Scenario stopped after collision; waiting for recovery confirmation.")
                if _collision_recovery_enabled(args) and bool(getattr(args, "collision_recovery_popup", True)):
                    _push_marker(
                        marker_outlet,
                        "COLLISION_CONFIRM_POPUP_OPEN",
                        scenario_t=collision_info.get("scenario_t") if isinstance(collision_info, dict) else None,
                        collision_count=collision_info.get("count") if isinstance(collision_info, dict) else None,
                    )
                    print("COLLISION_CONFIRM_POPUP_OPEN waiting_for_operator_ok=1")
                    if bool(getattr(args, "collision_confirm_via_stdin", False)):
                        popup_ok = _wait_for_collision_confirm_stdin(
                            normalized.get("scenario_name", args.name),
                            collision_info,
                        )
                    else:
                        popup_ok = _popup_blocking(
                            "CARLA scenario collision",
                            _format_collision_recovery_popup(normalized.get("scenario_name", args.name), collision_info),
                            icon="Warning",
                        )
                    print("COLLISION_CONFIRM_POPUP_ACK ok={0}".format(1 if popup_ok else 0))
                    _push_marker(
                        marker_outlet,
                        "COLLISION_CONFIRM_POPUP_ACK",
                        scenario_t=collision_info.get("scenario_t") if isinstance(collision_info, dict) else None,
                        ok=popup_ok,
                    )
                elif _collision_recovery_enabled(args):
                    print("Collision recovery popup disabled; proceeding to cleanup/recovery.")
            else:
                print("Scenario cancelled." if _playback_stop_requested(args) else "Scenario completed.")

            if args.once:
                return 0
            print("")
            try:
                ans = input("[r]eplay or [q]uit? (Enter = replay): ").strip().lower()
            except EOFError:
                ans = "q"
            if ans in ("q", "quit", "exit"):
                print("Quit requested.")
                return 0

    finally:
        # Cleanup and stabilize while the world is still synchronous. Restoring
        # async before freezing the DReyeVR pawn can let the vehicle controller
        # and physics wake up from a scripted pose and launch the ego.
        _write_g29_visual_steer(args, 0.0)
        _write_g29_python_state(args, active=False, steer=0.0)
        _push_marker(marker_outlet, "SCENARIO_CLEANUP_START", actors=len(spawned_actors))
        collision_info = getattr(args, "_last_run_collision", None)
        collision_recovery = bool(collision_info and _collision_recovery_enabled(args))
        if collision_recovery:
            _push_marker(
                marker_outlet,
                "COLLISION_RECOVERY_START",
                scenario_t=collision_info.get("scenario_t") if isinstance(collision_info, dict) else None,
                collision_count=collision_info.get("count") if isinstance(collision_info, dict) else None,
                other_actor_id=collision_info.get("other_actor_id") if isinstance(collision_info, dict) else None,
                other_type_id=collision_info.get("other_type_id") if isinstance(collision_info, dict) else None,
            )
            try:
                world.apply_settings(sync_settings)
                traffic_manager.set_synchronous_mode(True)
                print("Collision recovery: world returned to synchronous cleanup mode.")
            except Exception:
                pass
        if getattr(args, "continuous_session", False) and not collision_recovery:
            setattr(args, "_continuous_queen_vehicles", queen_vehicles)
            if queen_vehicles:
                print(
                    "Keeping {0} queen/background vehicle(s) alive for continuous playlist handoff.".format(
                        len(queen_vehicles)
                    )
                )
        else:
            queen_destroyed = _destroy_queen_vehicles(queen_vehicles)
            if queen_destroyed:
                print("Cleaning up {0} queen/background visual vehicle(s)...".format(queen_destroyed))
                try:
                    world.tick()
                except Exception:
                    pass
        if collision_recovery and _playlist_actor_pool_enabled(args):
            print("Collision recovery: destroying preloaded playlist actors instead of reusing them.")
            _destroy_playlist_actor_pool(args)
            try:
                world.tick()
            except Exception:
                pass
        elif _playlist_actor_pool_enabled(args):
            print("Parking preloaded playlist actors for next segment instead of destroying them.")
            _park_playlist_actor_pool(args, world, lane_paths=lane_paths, plans=plans)
        else:
            print("Cleaning up " + str(len(spawned_actors)) + " spawned actor(s)...")
            destroyed_any = _destroy_spawned_actors(spawned_actors,
                cleanup_registry["errors"] if cleanup_registry is not None else None)
            if destroyed_any:
                try:
                    world.tick()
                except Exception:
                    pass
        playlist_has_followup = _continuous_playlist_has_followup(args) and not collision_recovery
        if playlist_has_followup:
            try:
                _apply_async_visual_mode(world, traffic_manager)
                print("Continuous session handoff: world left async so the visual feed keeps moving before the next playlist item.")
            except Exception:
                pass
        elif args.world_mode == "async-visual" and args.post_run_world != "restore":
            try:
                world.apply_settings(sync_settings)
                traffic_manager.set_synchronous_mode(True)
                print("World returned to synchronous pause after async-visual playback.")
            except Exception:
                pass
        if collision_recovery:
            recovery_details = _recover_ego_to_road_autodrive(
                args,
                ego_plan,
                lane_paths,
                world,
                traffic_manager,
                ego_speed_mps,
            )
            if recovery_details:
                print(
                    "Collision recovery: ego returned to {0} lane at s={1:.1f}m and resumed auto-driving at {2:.1f} km/h.".format(
                        recovery_details.get("lane"),
                        recovery_details.get("s", 0.0),
                        recovery_details.get("speed_kmh", 0.0),
                    )
                )
                _push_marker(
                    marker_outlet,
                    "COLLISION_RECOVERY_END",
                    lane=recovery_details.get("lane"),
                    recovered_s=recovery_details.get("s"),
                    speed_kmh=recovery_details.get("speed_kmh"),
                )
            else:
                print("Collision recovery: could not recover ego to road auto-driving state.")
        elif getattr(args, "continuous_session", False):
            print("Continuous session handoff: ego left at scenario end for playlist hold/next segment.")
        else:
            stabilize_time_s = getattr(args, "_cancelled_playback_time_s", total_time_s) if _playback_stop_requested(args) else total_time_s
            _stabilize_ego_after_run(args, ego_plan, lane_paths, world, stabilize_time_s, ego_speed_mps)
        if playlist_has_followup:
            print("World kept in async handoff mode for the next playlist item.")
        elif args.post_run_world == "restore" or args.post_run_ego == "physics" or args.restore_ego_physics:
            try:
                traffic_manager.set_synchronous_mode(False)
                world.apply_settings(original_settings)
                print("World settings restored after scenario.")
            except Exception:
                pass
        elif collision_recovery:
            print("World left async after collision recovery so ego remains in on-road auto-driving state.")
        else:
            print("World left in synchronous pause after scenario to prevent ego drift.")
        _push_marker(marker_outlet, "SCENARIO_CLEANUP_END", actors=len(spawned_actors))
        if marker_outlet is not None:
            marker_outlet.close()


def _playlist_names_from_file(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        values = payload.get("scenario_names", [])
    else:
        values = payload
    names = []
    for item in values or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("scenario_name")
        else:
            name = item
        name = str(name or "").strip()
        if name:
            names.append(name)
    return names


def _playlist_actor_specs(normalized_items):
    specs = {}
    for normalized in normalized_items or []:
        scenario_name = str(normalized.get("scenario_name", "") or "")
        vehicles = normalized.get("vehicles", {}) or {}
        for role in ROLE_ORDER[1:]:
            cfg = vehicles.get(role)
            if not cfg or not cfg.get("enabled", False):
                continue
            specs[_playlist_actor_key(scenario_name, role)] = dict(cfg)
    return specs


def _drive_ego_forward_for_session_hold(args, hold_s, speed_kmh):
    hold_s = max(0.0, float(hold_s or 0.0))
    if hold_s <= 0.0:
        return
    print("Continuous playlist hold: driving ego for {0:.1f}s before next scenario.".format(hold_s))
    client = carla.Client(args.host, args.port)
    client.set_timeout(8.0)
    world = client.get_world()
    ego = wait_for_dreyevr_ego(world, attempts=2)
    if ego is None:
        print("WARNING: ego not found during continuous playlist hold.")
        time.sleep(hold_s)
        return
    settings = world.get_settings()
    fixed_delta = float(settings.fixed_delta_seconds or 0.0166667)
    if fixed_delta <= 0.0:
        fixed_delta = 0.0166667
    speed_mps = max(0.0, float(speed_kmh or 0.0) / 3.6)
    try:
        current_speed = _actor_speed_mps(ego)
        if current_speed > 0.5:
            speed_mps = current_speed
    except Exception:
        pass
    ticks = max(1, int(round(hold_s / fixed_delta)))
    wall_start = time.monotonic()
    for tick in range(ticks):
        try:
            xf = ego.get_transform()
            forward = xf.rotation.get_forward_vector()
            ego.set_target_velocity(carla.Vector3D(
                forward.x * speed_mps,
                forward.y * speed_mps,
                forward.z * speed_mps,
            ))
            if settings.synchronous_mode:
                world.tick()
            else:
                world.wait_for_tick()
        except Exception as exc:
            print("WARNING: continuous playlist hold stopped early: {0}".format(exc))
            break
        elapsed_target = (tick + 1) * fixed_delta
        remaining = elapsed_target - (time.monotonic() - wall_start)
        if remaining > 0.0:
            time.sleep(remaining)
    print("Continuous playlist hold complete.")


def _wait_for_playlist_continue(args, index, count, scenario_name, next_scenario_name):
    payload = {
        "phase": "after_scenario",
        "scenario_index": int(index) + 1,
        "scenario_count": int(count),
        "scenario_name": str(scenario_name),
        "next_scenario_name": str(next_scenario_name),
    }
    print(
        "PLAYLIST_PAUSE_REQUEST " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )
    while True:
        line = sys.stdin.readline()
        if line == "":
            print("PLAYLIST_PAUSE_ABORT reason=stdin_closed", flush=True)
            return 130
        command = line.strip().lower()
        if command in ("playlist_continue", "continue", "start", "questionnaire_done"):
            print("PLAYLIST_PAUSE_CONTINUE command={0}".format(command or "start"), flush=True)
            return 0
        if command in ("stop", "quit", "exit", "restart"):
            print("PLAYLIST_PAUSE_ABORT command={0}".format(command), flush=True)
            return 130
        print("PLAYLIST_PAUSE_IGNORED command={0}".format(command), flush=True)


def main(argv=None):
    args = parse_args(argv)
    outputs_dir = Path(args.outputs_dir)
    if args.playlist_file:
        names = _playlist_names_from_file(args.playlist_file)
        if not names:
            print("ERROR: playlist is empty: " + str(args.playlist_file), file=sys.stderr)
            return 2
        args.continuous_session = True
        args.auto_start = True
        args.once = True
        normalized_items = [load_scenario(name, outputs_dir) for name in names]
        args._playlist_actor_pool = {}
        args._playlist_owned_actors = []
        if args.playlist_preload_actors:
            args._playlist_actor_pool_specs = _playlist_actor_specs(normalized_items)
        else:
            args._playlist_actor_pool_specs = {}
            print(
                "Continuous playlist actor preload disabled; support vehicles will spawn on-road per scenario."
            )
        if args._playlist_actor_pool_specs:
            print(
                "Continuous playlist actor pool will preload scenario-role actors: "
                + ", ".join(
                    _format_playlist_actor_key(key)
                    for key in sorted(
                        args._playlist_actor_pool_specs.keys(),
                        key=lambda item: _format_playlist_actor_key(item),
                    )
                )
            )
        try:
            for idx, normalized in enumerate(normalized_items):
                args.name = names[idx]
                args._playlist_index = idx
                args._playlist_count = len(normalized_items)
                playlist_start_mode = getattr(args, "_playlist_original_start_mode", None)
                if playlist_start_mode is None:
                    playlist_start_mode = args.start_mode
                    args._playlist_original_start_mode = playlist_start_mode
                force_fixed_after_collision = bool(getattr(args, "_playlist_force_fixed_start_after_collision", False))
                if force_fixed_after_collision:
                    args.start_mode = playlist_start_mode if playlist_start_mode != "current" else "fixed"
                    setattr(args, "_playlist_force_fixed_start_after_collision", False)
                    print(
                        "Continuous playlist handoff: previous item had a collision; using fixed scenario start anchor for "
                        + str(names[idx])
                        + "."
                    )
                elif (
                    idx > 0
                    and args.playlist_current_start_after_first
                    and not normalized.get("spawn_xodr")
                ):
                    args.start_mode = "current"
                    print(
                        "Continuous playlist handoff: using current ego position as start anchor for "
                        + str(names[idx])
                        + " (no spawn_xodr in scenario)."
                    )
                else:
                    args.start_mode = playlist_start_mode
                print("Schema OK: " + normalized["scenario_name"])
                print("  playlist_item={0}/{1} map={2} total_time={3}s ego={4} km/h".format(
                    idx + 1,
                    len(names),
                    normalized["map_name"],
                    normalized["total_time_s"],
                    normalized["ego_speed_kmh"],
                ))
                rc = run(args, normalized)
                if rc != 0:
                    return rc
                if getattr(args, "_last_run_collision", None):
                    setattr(args, "_playlist_force_fixed_start_after_collision", True)
                if idx < len(names) - 1:
                    if args.playlist_pause_between_scenarios:
                        pause_rc = _wait_for_playlist_continue(
                            args,
                            idx,
                            len(names),
                            names[idx],
                            names[idx + 1],
                        )
                        if pause_rc != 0:
                            return pause_rc
                    _drive_ego_forward_for_session_hold(
                        args,
                        args.session_hold_s,
                        normalized.get("ego_speed_kmh", 30.0),
                    )
            return 0
        finally:
            _destroy_continuous_queen_vehicles(args)
            _destroy_playlist_actor_pool(args)
    if not args.name:
        print("ERROR: scenario name or --playlist-file is required.", file=sys.stderr)
        return 2
    normalized = load_scenario(args.name, outputs_dir)
    print("Schema OK: " + normalized["scenario_name"])
    print("  map=" + normalized["map_name"]
          + "  total_time=" + str(normalized["total_time_s"]) + "s"
          + "  ego=" + str(normalized["ego_speed_kmh"]) + " km/h")
    return run(args, normalized)


if __name__ == "__main__":
    sys.exit(main())
