"""Adapter from the SCANeR prompt parser to this repo's CARLA v1 JSON.

The source package under scanner_prompt_gui_to_carla_0913_source_20260524 is
kept as the parser/source of truth. This module only translates its parsed
ScenarioParametersV2 objects into the JSON shape consumed by
carla_nl.run_scenario_dreyevr and the existing schema validator.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import constants as C
from .schema import validate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER_SOURCE_ROOT = PROJECT_ROOT / "scanner_prompt_gui_to_carla_0913_source_20260524"
SCANNER_TOOL_DIR = SCANNER_SOURCE_ROOT / "SCANER_tool"
DEFAULT_SCANNER_TEMPLATE = SCANNER_SOURCE_ROOT / "references" / "scaner_scenarios" / "ERTR_LC_Keep_CR.sce"
SCANNER_AUDIO_DIR = SCANNER_TOOL_DIR / "prompt_auditory_cues"

FRONTEND_ROLE_MAP = {
    "front": "vehicle_1",
    "rear": "vehicle_2",
    "left": "vehicle_3",
}

PROMPT_ROLE_TOKEN_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b",
    re.IGNORECASE,
)
PROMPT_SPEED_RE = re.compile(
    r"(?:speed|velocity|速度|速率|车速|車速)\s*(?:=|:|is|at|to|为|是)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>km/h|kmh|kph|mph|m/s|mps)?",
    re.IGNORECASE,
)
PROMPT_VEHICLE_BLUEPRINT_RE = re.compile(
    r"(?:blueprint(?:_filter)?|bp)\s*(?:=|:|is|as)?\s*"
    r"(?P<blueprint>vehicle\.[A-Za-z0-9_.-]+)\b",
    re.IGNORECASE,
)
PROMPT_VEHICLE_MODEL_RE = re.compile(
    r"(?:vehicle[\s_-]*type|model|type|body|è½¦åž‹|è»Šåž‹)\s*(?:=|:|is|as)?\s*"
    r"(?P<model>[A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)?)\b",
    re.IGNORECASE,
)
PROMPT_UNIT_SPEED_RE = re.compile(
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>km/h|kmh|kph|mph|m/s|mps)\b",
    re.IGNORECASE,
)
PROMPT_AHEAD_METERS_RE = re.compile(
    r"(?:initial[\s_-]*gap|gap|ahead|front)\s*(?:=|:|is|at|to)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:m|meter|meters|metre|metres)\b",
    re.IGNORECASE,
)
PROMPT_AHEAD_TIME_RE = re.compile(
    r"(?:initial[\s_-]*gap|time[\s_-]*gap|gap|ahead|front|headway|spacing)\s*"
    r"(?:=|:|is|at|to)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*"
    r"(?:s|sec|secs|second|seconds)?\b",
    re.IGNORECASE,
)
# Scenario-level duration: an explicit "scenario duration" anywhere always
# wins; a bare "duration" only counts when it starts its own line/segment, so
# the per-event ``duration=Ns`` of lane-change / pedestrian lines cannot be
# misread as the scenario total time.
PROMPT_SCENARIO_DURATION_PREFIXED_RE = re.compile(
    r"scenario\s*duration\s*(?:=|:|is|of)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
PROMPT_SCENARIO_DURATION_BARE_LINE_RE = re.compile(
    r"^[ \t]*duration\s*(?:=|:|is|of)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE | re.MULTILINE,
)
PROMPT_SCENARIO_DURATION_AUTO_RE = re.compile(
    r"(?:scenario\s*|^[ \t]*)duration\s*(?:=|:|is)?\s*"
    r"(?:auto(?:matic(?:ally)?)?|until[\s_-]*takeover)\b",
    re.IGNORECASE | re.MULTILINE,
)
# NOTE: deliberately NO "to" connector here -- "lane to left" is a lane-CHANGE
# phrase (movement), not an initial-lane assignment. Including it made the live
# Design refresh rewrite the vehicle's starting lane while the user was still
# typing a "... change lane to left" line that had no t= prefix yet.
PROMPT_LANE_RE = re.compile(
    r"\blane\s*(?:=|:|is)?\s*(?P<lane>left|middle|right)\b",
    re.IGNORECASE,
)
PROMPT_LANE_CHANGE_RE = re.compile(
    r"(?:\bt\b|\btime\b|\bat\b|start|from)\s*(?:=|:|is)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?"
    r"\s*[:,-]?\s*"
    r"(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))"
    # optional filler verbs between the role and the connector, so conversational
    # forms like "v1 go back to middle" / "v1 change lane to left" also parse.
    r"(?:\s+(?:go(?:es)?|moves?|returns?|comes?|gets?|drifts?|merges?|switch(?:es)?|"
    r"chang(?:e[sd]?|ing)|cut(?:s|ting)?|shift(?:s|ing)?|lanes?|over|back|then|again))*"
    r"\s*(?:->|=>|to|into|toward[s]?|(?P<turn>turns?))\s*(?:the\s+)?(?P<lane>left|middle|right)\b"
    r"(?:.*?(?:duration|over|\bin\b|within|tak(?:es?|ing))\s*(?:=|:|is)?\s*"
    r"(?P<duration>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?)?",
    re.IGNORECASE,
)
# The same action with its timestamp after the destination, e.g.
# "v1 go to middle lane at t =10". Keep the match on one prompt fragment.
PROMPT_LANE_CHANGE_TRAILING_TIME_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r"(?:\s+(?:go(?:es)?|moves?|returns?|comes?|gets?|drifts?|merges?|switch(?:es)?|"
    r"chang(?:e[sd]?|ing)|cut(?:s|ting)?|shift(?:s|ing)?|lanes?|over|back|then|again))*"
    r"\s*(?:->|=>|to|into|toward[s]?|(?P<turn>turns?))\s*(?:the\s+)?(?P<lane>left|middle|right)\b"
    r"(?:\s+lane)?\s+(?:at\s+)?(?:\bt\b|\btime\b|\bat\b|start|from)\s*(?:=|:|is)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:seconds|second|secs|sec|s)?\b"
    r"(?:\s*[,:-]?\s*(?:duration|over|\bin\b|within|tak(?:es?|ing))\s*(?:=|:|is)?\s*"
    r"(?P<duration>[0-9]+(?:\.[0-9]+)?)\s*(?:seconds|second|secs|sec|s)?\b)?",
    re.IGNORECASE,
)
PROMPT_BEHIND_METERS_RE = re.compile(
    r"(?:behind|rear)\s*(?:=|:|is|at|to)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:m|meter|meters|metre|metres)\b",
    re.IGNORECASE,
)
PROMPT_BEHIND_TIME_RE = re.compile(
    r"(?:behind|rear)\s*(?:=|:|is|at|to)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*"
    r"(?:s|sec|secs|second|seconds)?\b",
    re.IGNORECASE,
)
PROMPT_LANE_CHANGE_INTENT_RE = re.compile(
    r"\b(?:cuts?[\s_-]*in(?:to)?|cuts?[\s_-]*out|lane[\s_-]*change|change\s+lanes?|"
    r"switch\s+lanes?|merge|moves?\s+over)\b|"
    r"變道|变道|切入|切出|換道|换道",
    re.IGNORECASE,
)
# Conversational ("prose") variants where the value precedes the keyword, so the
# parser accepts plain sentences in addition to structured "key=value" tokens.
PROMPT_LANE_PROSE_RE = re.compile(r"\b(?P<lane>left|middle|right)\s+lane\b", re.IGNORECASE)
PROMPT_AHEAD_TIME_PROSE_RE = re.compile(
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\s+(?:ahead|in\s+front)\b",
    re.IGNORECASE,
)
PROMPT_BEHIND_TIME_PROSE_RE = re.compile(
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\s+behind\b",
    re.IGNORECASE,
)
# "After 10 seconds, vehicle 1 takes 3 seconds to cut into the ego lane from the right."
# [^.] keeps each match inside one sentence. target 'ego' resolves to the ego lane.
PROMPT_PROSE_LANE_CHANGE_RE = re.compile(
    r"after\s+(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b"
    r"[^.]*?\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r"[^.]*?(?:takes?\s+(?P<duration>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\s+to\s+)?"
    r"(?:cuts?|change[sd]?|moves?|merges?|switch(?:es)?)\b[^.]*?"
    r"(?:in(?:to)?|to|toward[s]?)\s+(?:the\s+)?(?P<target>ego|left|middle|right)\s+lane\b",
    re.IGNORECASE,
)
PROMPT_TAKEOVER_RE = re.compile(r"\b(?:take[\s_-]*over|takeover|tor|n[\s_-]*key)\b|接管", re.IGNORECASE)
PROMPT_TIME_RE = re.compile(
    r"(?:\bt\b|\btime\b|\bat\b|start|from|在)\s*(?:=|:|is|为|是)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds|秒)\b",
    re.IGNORECASE,
)
PROMPT_DURATION_RE = re.compile(
    r"(?:duration|decel(?:eration)?|over|用时|持续)\s*(?:=|:|is|为|是)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds|秒)\b",
    re.IGNORECASE,
)
PROMPT_TIMEOUT_RE = re.compile(
    r"(?:timeout|window|within|等待|窗口)\s*(?:=|:|is|为|是)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds|秒)\b",
    re.IGNORECASE,
)
PROMPT_TAKEOVER_BUDGET_RE = re.compile(
    r"(?:take[\s_-]*over[\s_-]*budget|takeover[\s_-]*budget|tor[\s_-]*budget|"
    r"\bttc\b|time[\s_-]*to[\s_-]*collision|collision[\s_-]*time|budget)\s*"
    r"(?:=|:|is|of)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
PROMPT_COLLISION_BUFFER_RE = re.compile(
    r"(?:collision[\s_-]*(?:buffer|guard|margin)|brake[\s_-]*(?:buffer|margin)|"
    r"force[\s_-]*brake[\s_-]*(?:before|margin)|before[\s_-]*collision)\s*"
    r"(?:=|:|is|of)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
PROMPT_KEY_RE = re.compile(
    r"(?:key|press|按|按下)\s*(?:=|:|is|为|是)?\s*(?P<key>space|空格|b|brake|n)\b",
    re.IGNORECASE,
)
PROMPT_TARGET_SPEED_RE = re.compile(
    r"(?:target(?:_speed)?|to|slow\s*to|brake\s*to|降低到|减速到|刹车到|降到)\s*(?:=|:|is|为|是)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>km/h|kmh|kph|mph|m/s|mps)?",
    re.IGNORECASE,
)
PROMPT_GAP_PROFILE_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r".*?\b(?:gap[\s_-]*points?|gap[\s_-]*profile|dynamic[\s_-]*gap|distance[\s_-]*profile)\b",
    re.IGNORECASE,
)
PROMPT_GAP_POINT_RE = re.compile(
    r"(?:\bt\b|\btime\b)?\s*(?:=|:)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?"
    r"\s*(?:=|:|->|to|gap\s*=?)\s*"
    r"(?P<gap>[-+]?[0-9]+(?:\.[0-9]+)?)\s*(?:m|meter|meters|metre|metres)\b",
    re.IGNORECASE,
)
PROMPT_TIME_GAP_PROFILE_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r".*?\b(?:time[\s_-]*gap[\s_-]*points?|time[\s_-]*gap[\s_-]*profile|"
    r"headway[\s_-]*points?|headway[\s_-]*profile)\b",
    re.IGNORECASE,
)
PROMPT_TIME_GAP_POINT_RE = re.compile(
    r"(?:\bt\b|\btime\b)?\s*(?:=|:)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?"
    r"\s*(?:=|:|->|to|time[\s_-]*gap\s*=?)\s*"
    r"(?P<gap>[-+]?[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
PROMPT_TIME_GAP_EVENT_RE = re.compile(
    r"(?:\bt\b|\btime\b|\bat|from)\s*(?:=|:|is)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?"
    r"\s*[:,-]?\s*"
    r"(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r".*?\b(?:time[\s_-]*gap|headway)\b\s*(?:=|:|is|to)?\s*"
    r"(?P<gap>[-+]?[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
PROMPT_SPEED_PROFILE_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r".*?\b(?:speed[\s_-]*points?|speed[\s_-]*profile|dynamic[\s_-]*speed|velocity[\s_-]*profile)\b",
    re.IGNORECASE,
)
PROMPT_SPEED_POINT_RE = re.compile(
    r"(?:\bt\b|\btime\b)?\s*(?:=|:)?\s*"
    r"(?P<time>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?"
    r"\s*(?:=|:|->|to|speed\s*=?)\s*"
    r"(?P<speed>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>km/h|kmh|kph|mph|m/s|mps)?\b",
    re.IGNORECASE,
)
PROMPT_LONGITUDINAL_MODE_RE = re.compile(
    r"\b(?P<role>ego|hero|tesla|front|rear|v[1-6]|vehicle[\s_-]*[1-6]|left[\s_-]*(?:vehicle|car))\b"
    r".*?\b(?:longitudinal[\s_-]*(?:mode|control)|control[\s_-]*mode|speed[\s_-]*control)"
    r"\s*(?:=|:|is|as|to)?\s*"
    r"(?P<mode>time[\s_-]*gap|headway|speed|speed[\s_-]*points?|velocity|"
    r"fixed[\s_-]*speed|constant[\s_-]*speed|fixed)\b",
    re.IGNORECASE,
)
PROMPT_LONGITUDINAL_MODE_TOKEN_RE = re.compile(
    r",?\s*\b(?:longitudinal[\s_-]*(?:mode|control)|control[\s_-]*mode|speed[\s_-]*control)"
    r"\s*(?:=|:|is|as|to)?\s*"
    r"(?:time[\s_-]*gap|headway|speed|speed[\s_-]*points?|velocity|"
    r"fixed[\s_-]*speed|constant[\s_-]*speed|fixed)\b",
    re.IGNORECASE,
)
PROMPT_DROP_INTENT_RE = re.compile(
    r"\b(?:drops?|releases?|drop[\s_-]*(?:obstacle|object)?)\b|掉落|落下|丢下|釋放|释放",
    re.IGNORECASE,
)
PROMPT_DROP_KIND_RE = re.compile(
    r"\b(?:drops?|releases?|drop[\s_-]*(?:obstacle|object)?)\s+"
    r"(?P<kind>disabled\s+vehicle|traffic\s+cone|barrel|cone|barrier|debris|box|crate|obstacle|object)\b",
    re.IGNORECASE,
)
PROMPT_DROP_OFFSET_RE = re.compile(
    r"(?:drop[\s_-]*offset|offset|rear[\s_-]*offset)\s*(?:=|:|is|at)?\s*"
    r"(?P<value>[+-]?[0-9]+(?:\.[0-9]+)?)\s*(?:m|meter|meters|metre|metres)?\b",
    re.IGNORECASE,
)
PROMPT_DROP_END_RE = re.compile(
    r"(?:end|until|remove|stop)\s*(?:=|:|at|is)?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?\b",
    re.IGNORECASE,
)
PROMPT_SCANNER_OBSTACLE_DSL_RE = re.compile(
    r"^\s*(?:obstacle|obs|o)\s*_?\s*[1-6]?\s*[:：].*\bt\s*="
    r".*\bend\s*=",
    re.IGNORECASE | re.MULTILINE,
)
PROMPT_PEDESTRIAN_INTENT_RE = re.compile(
    r"\b(?:ped(?:estrian)?|walker|person)\b",
    re.IGNORECASE,
)
PROMPT_PEDESTRIAN_SIDE_TRANSITION_RE = re.compile(
    r"\b(?:from\s+)?(?P<start>left|middle|right)\s*(?:->|=>|\bto\b)\s*"
    r"(?P<end>left|middle|right)\b",
    re.IGNORECASE,
)
PROMPT_PEDESTRIAN_INCOMPLETE_TRANSITION_RE = re.compile(
    r"\b(?:left|middle|right)\s*(?:->|=>|\bto\b)\s*"
    r"(?=$|[,.;:]|\bat\b|\bafter\b|\baround\b|\bt\s*=|\bfor\b|\bduration\b|\d)",
    re.IGNORECASE,
)
MPH_TO_KMH = 1.609344
AUTOFILL_DURATION_S = 35.0
AUTOFILL_EGO_LANE = "left"
AUTOFILL_EGO_SPEED_KMH = 80.0
AUTOFILL_VEHICLE_LANE = "middle"
AUTOFILL_GAP_TIME_S = 7.0
AUTOFILL_LANE_CHANGE_TIME_S = 7.0
AUTOFILL_LANE_CHANGE_DURATION_S = 4.0
DEFAULT_DROP_OFFSET_M = 0.5
SCANNER_OBSTACLE_DSL_START_S = 5.0
SCANNER_OBSTACLE_DSL_END_S = 30.0

SCANNER_VEHICLE_BLUEPRINTS = {
    "ExecutiveCar": "vehicle.lincoln.mkz_2020",
    "Citroen_C3_Red": "vehicle.audi.etron",
    "Citroen_C2": "vehicle.dodge.charger_2020",
    "Citroen_C4_Oriental_Blue": "vehicle.mercedes.coupe_2020",
}

SAFE_NAME_RE = re.compile(r"[^a-z0-9_]+")
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


@dataclass(frozen=True)
class ConvertedScenario:
    name: str
    scenario: dict[str, Any]
    summaries: list[str]
    warnings: list[str]


def _load_scanner_api() -> dict[str, Any]:
    if not SCANNER_TOOL_DIR.exists():
        raise FileNotFoundError(f"Scanner source not found: {SCANNER_TOOL_DIR}")
    scanner_path = str(SCANNER_TOOL_DIR)
    if scanner_path not in sys.path:
        sys.path.insert(0, scanner_path)
    import simple_hf_scenario_gui as scanner_gui  # type: ignore

    return {
        "build_prompt_preview_v2": scanner_gui.build_prompt_preview_v2,
        "sort_prompt_lines_by_timeline_v2": scanner_gui.sort_prompt_lines_by_timeline_v2,
        "lane_change_events_for_vehicle_v2": scanner_gui.lane_change_events_for_vehicle_v2,
        "summarize_prompt_params_v2": scanner_gui.summarize_prompt_params_v2,
        "vehicle_enabled_v2": scanner_gui.vehicle_enabled_v2,
        "iter_obstacle_configs_v2": scanner_gui.iter_obstacle_configs_v2,
    }


def scanner_examples() -> str:
    return (
        "scenario duration=35s\n"
        "ego: lane=left, speed=80km/h\n"
        "v1: lane=middle, ahead=7s, speed=72km/h\n"
        "t=7s: v1 -> left, duration=4s\n\n"
        "scenario duration=45s\n"
        "ego: lane=middle, speed=55mph\n"
        "obstacle1: barrier, lane=middle, 100m ahead of ego, t=10s, end=35s\n"
    )


def _prepare_gap_time_prompt(prompt_text: str, *, extra_gap_roles: set[str] | None = None) -> tuple[str, list[str]]:
    """Normalize prompt text for CARLA's gap-time workflow.

    The old SCANeR prompt GUI intentionally refused vague lane-change prompts.
    For CARLA authoring we keep that grammar, but add a narrow auto-fix layer:
    missing duration/ego/gap/lane-change lines are completed with visible
    warnings instead of switching to the removed TTC fallback.
    """
    text = str(prompt_text or "").strip()
    if not text:
        return text, []

    text = _normalize_prompt_turns(text)

    # Normalize before SCANeR validates lane-change intent; adapter overrides
    # alone cannot recover a preview rejected by its time-first grammar.
    text = "\n".join(
        PROMPT_LANE_CHANGE_TRAILING_TIME_RE.sub(
            lambda match: (
                f"t={match.group('time')}s: {match.group('role')} -> {match.group('lane')}, "
                f"duration={match.group('duration') or C.DEFAULT_LANE_CHANGE_DURATION_S}s"
            ),
            fragment,
        )
        for fragment in re.split(r"[\r\n;]+", text)
    )

    warnings: list[str] = []
    additions: list[str] = []
    roles = _prompt_roles_present(text)
    lane_overrides = _prompt_vehicle_lane_overrides(text)
    speed_overrides = _prompt_vehicle_speed_overrides(text)
    lane_change_overrides = _prompt_lane_change_overrides(text)
    prose_lane_changes = _prompt_prose_lane_change_overrides(text, lane_overrides)
    gap_roles = set(extra_gap_roles or set())
    gap_roles.update(_prompt_vehicle_gap_time_roles(text))
    has_lane_change_intent = bool(PROMPT_LANE_CHANGE_INTENT_RE.search(text)) or bool(lane_change_overrides)

    if _explicit_prompt_scenario_duration(text) is None:
        if PROMPT_SCENARIO_DURATION_AUTO_RE.search(text) is not None:
            warnings.append(
                "Scenario duration=auto: the scene holds after the scripted time "
                "until the take-over interaction completes."
            )
        else:
            additions.append(f"scenario duration={AUTOFILL_DURATION_S:g}s")
            warnings.append(f"Auto-filled scenario duration: {AUTOFILL_DURATION_S:g}s.")

    if "ego" not in roles:
        additions.append(f"ego: lane={AUTOFILL_EGO_LANE}, speed={AUTOFILL_EGO_SPEED_KMH:g}km/h")
        warnings.append(
            "Auto-filled ego defaults: "
            f"lane={_lane(AUTOFILL_EGO_LANE)}, speed={AUTOFILL_EGO_SPEED_KMH:g} km/h."
        )
    else:
        if "ego" not in lane_overrides:
            additions.append(f"ego: lane={AUTOFILL_EGO_LANE}")
            warnings.append(f"Auto-filled ego lane: {_lane(AUTOFILL_EGO_LANE)}.")
        if "ego" not in speed_overrides:
            additions.append(f"ego: speed={AUTOFILL_EGO_SPEED_KMH:g}km/h")
            warnings.append(f"Auto-filled ego speed: {AUTOFILL_EGO_SPEED_KMH:g} km/h.")

    support_roles = {
        role
        for role in (roles | set(lane_change_overrides))
        if role in C.SUPPORT_ROLE_ORDER
    }
    if has_lane_change_intent and not support_roles:
        support_roles.add("vehicle_1")

    for role in sorted(support_roles, key=_role_sort_key):
        actor_label = _role_prompt_label(role)
        missing_lane = role not in lane_overrides
        missing_gap = role not in gap_roles
        if missing_lane or missing_gap:
            line_parts = [f"{actor_label}:"]
            if missing_lane:
                line_parts.append(f"lane={AUTOFILL_VEHICLE_LANE}")
            if missing_gap:
                line_parts.append(f"ahead={AUTOFILL_GAP_TIME_S:g}s")
            additions.append(" ".join([line_parts[0], ", ".join(line_parts[1:])]))
            if missing_lane:
                warnings.append(
                    f"Auto-filled {role} lane: {_lane(AUTOFILL_VEHICLE_LANE)}."
                )
            if missing_gap:
                warnings.append(
                    f"Auto-filled {role} initial time gap: ahead={AUTOFILL_GAP_TIME_S:g}s."
                )

    if has_lane_change_intent and not lane_change_overrides:
        if prose_lane_changes:
            # Conversational lane change(s): emit the structured line(s) using the
            # prose-parsed timing/target so the normal pipeline handles them.
            for role in sorted(prose_lane_changes, key=_role_sort_key):
                start_lane = _lane(lane_overrides.get(role, AUTOFILL_VEHICLE_LANE))
                for point in prose_lane_changes[role]:
                    target_lane = _lane(point["target_lane"])
                    change_time = float(point["time_s"])
                    change_duration = float(point["duration_s"])
                    additions.append(
                        f"t={change_time:g}s: "
                        f"{_role_prompt_label(role)} -> {target_lane.lower()}, "
                        f"duration={change_duration:g}s"
                    )
                    warnings.append(
                        f"Parsed {role} lane change from prose: {start_lane}->{target_lane} "
                        f"at {change_time:g}s over {change_duration:g}s."
                    )
        else:
            for role in sorted(support_roles, key=_role_sort_key):
                if role in lane_change_overrides:
                    continue
                start_lane = _lane(lane_overrides.get(role, AUTOFILL_VEHICLE_LANE))
                ego_lane = _lane(lane_overrides.get("ego", AUTOFILL_EGO_LANE))
                target_lane = ego_lane if ego_lane != start_lane else ("Left" if start_lane != "Left" else "Middle")
                additions.append(
                    f"t={AUTOFILL_LANE_CHANGE_TIME_S:g}s: "
                    f"{_role_prompt_label(role)} -> {target_lane.lower()}, "
                    f"duration={AUTOFILL_LANE_CHANGE_DURATION_S:g}s"
                )
                warnings.append(
                    f"Auto-filled {role} lane change: {start_lane}->{target_lane} "
                    f"at {AUTOFILL_LANE_CHANGE_TIME_S:g}s over {AUTOFILL_LANE_CHANGE_DURATION_S:g}s."
                )
                break

    if not additions:
        return text, warnings
    return text.rstrip() + "\n" + "\n".join(additions) + "\n", warnings


def build_preview(prompt_text: str, *, template_path: Path | None = None, requested_output: str = "HF_Prompt_CARLA.sce") -> Any:
    api = _load_scanner_api()
    template = template_path or DEFAULT_SCANNER_TEMPLATE
    return api["build_prompt_preview_v2"](
        template_path=template,
        prompt_text=prompt_text,
        requested_output=requested_output,
    )


def sort_prompt_by_timeline(prompt_text: str) -> str:
    api = _load_scanner_api()
    return api["sort_prompt_lines_by_timeline_v2"](prompt_text)


def apply_visible_safe_defaults(prompt_text: str) -> tuple[str, list[str]]:
    from .prompt_feedback import check_unfilled_placeholders
    check_unfilled_placeholders(prompt_text)
    from .natural_language_prompt import is_prose_prompt, parse_prose_prompt
    normalized, expanded = _normalize_prompt_and_reader_intent(prompt_text)
    if expanded or is_prose_prompt(normalized):
        _, warnings = parse_prose_prompt(normalized)
        return prompt_text, warnings
    _check_obstacle_prompt_details(normalized)
    _check_pedestrian_prompt_details(normalized)
    return _prepare_gap_time_prompt(normalized)


def _normalize_prompt_for_reader(prompt_text: str) -> str:
    return _normalize_prompt_and_reader_intent(prompt_text)[0]


def _normalize_prompt_and_reader_intent(prompt_text: str) -> tuple[str, bool]:
    from .prompt_language import normalize_prompt_characters, normalize_reader_wording
    from .prompt_positions import normalize_initial_position_clauses
    normalized, expanded = normalize_reader_wording(normalize_prompt_characters(prompt_text))
    return normalize_initial_position_clauses(normalized), expanded


def convert_prompt(
    prompt_text: str,
    *,
    template_path: Path | None = None,
    requested_output: str = "HF_Prompt_CARLA.sce",
    name: str | None = None,
    map_name: str = "Town04_Opt",
    weather_preset: str = "CloudyNoon",
    rpc_port: int = 2013,
    quality_level: str = "Epic",
    strict: bool = False,
) -> tuple[Any, list[ConvertedScenario]]:
    from .prompt_feedback import check_unfilled_placeholders
    check_unfilled_placeholders(prompt_text)
    from .prompt_language import normalize_prompt_characters
    raw_prompt_text = normalize_prompt_characters(prompt_text)
    from .standalone_cues import split_cue_lines, validate_cues
    cue_sources = []
    raw_prompt_text, standalone_cues = split_cue_lines(raw_prompt_text, source_clauses=cue_sources)
    raw_prompt_text, expanded_wording = _normalize_prompt_and_reader_intent(raw_prompt_text)
    from .natural_language_prompt import is_prose_prompt, parse_prose_prompt

    def checked_cues(payload):
        try:
            from .execution_contract import validate_execution_data
            from .audio_events import file_cues
            validate_execution_data(payload)
            file_cues(payload)
            return validate_cues(standalone_cues, payload, source_clauses=cue_sources)
        except ValueError as exc:
            requirements = getattr(exc, 'duration_requirements', ())
            if requirements:
                from .natural_language_prompt import duration_requirement_error
                raise duration_requirement_error(raw_prompt_text, payload['total_time_s'], requirements) from exc
            raise

    if expanded_wording or is_prose_prompt(raw_prompt_text):
        partial, warnings = parse_prose_prompt(raw_prompt_text)
        scenario_name = scenario_name_from_output(name or requested_output, fallback="prose_scenario")
        partial = {
            "scenario_name": scenario_name, "map_name": map_name,
            "weather_preset": weather_preset, "rpc_port": int(rpc_port),
            "quality_level": quality_level, "spawn_point_index": 0,
            "launcher_script": "start_dreyevr_ndisplay.ps1", "render_mode": "nDisplay",
            "camera_mode": "off", "dreyevr": True, **partial,
        }
        partial['standalone_cues'] = checked_cues(partial)
        ok, payload = validate(partial)
        if not ok:
            raise ValueError(f"Prose scenario failed CARLA schema validation: {payload}")
        preview = SimpleNamespace(ok=True, route="checked_prose", message="", language="zh" if re.search(r"[\u4e00-\u9fff]", raw_prompt_text) else "en", params_list=[], output_names=[scenario_name])
        return preview, [ConvertedScenario(scenario_name, payload, _summarize_carla_scenario(payload), warnings)]
    _check_obstacle_prompt_details(raw_prompt_text)
    _check_pedestrian_prompt_details(raw_prompt_text)
    incomplete_pedestrian_fragments = _incomplete_pedestrian_prompt_fragments(raw_prompt_text)
    if strict and incomplete_pedestrian_fragments:
        _raise_incomplete_pedestrian_prompt(incomplete_pedestrian_fragments)
    _raise_if_unsupported_initial_meter_gaps(raw_prompt_text)
    _raise_if_unsupported_longitudinal_prompt_modes(raw_prompt_text)
    time_gap_point_overrides = _prompt_vehicle_time_gap_point_overrides(raw_prompt_text)
    speed_point_overrides = _prompt_vehicle_speed_point_overrides(raw_prompt_text)
    longitudinal_mode_overrides = _prompt_vehicle_longitudinal_mode_overrides(raw_prompt_text)
    (
        time_gap_point_overrides,
        speed_point_overrides,
        longitudinal_mode_warnings,
    ) = _select_prompt_longitudinal_mode(
        time_gap_point_overrides,
        speed_point_overrides,
        longitudinal_mode_overrides,
    )
    _raise_if_conflicting_prompt_longitudinal_modes(time_gap_point_overrides, speed_point_overrides)
    blueprint_overrides = _prompt_vehicle_blueprint_overrides(raw_prompt_text)
    dropped_obstacles = _prompt_dropped_obstacles(raw_prompt_text)
    scanner_prompt_text = _strip_carla_extension_fragments(raw_prompt_text)
    prompt_text, prompt_warnings = _prepare_gap_time_prompt(
        scanner_prompt_text,
        extra_gap_roles=set(time_gap_point_overrides) | set(speed_point_overrides),
    )
    if incomplete_pedestrian_fragments:
        prompt_warnings.append(
            "Scanner-compatible pedestrian defaults completed incomplete fragment(s): {0}.".format(
                "; ".join(incomplete_pedestrian_fragments[:3])
            )
        )
    prompt_warnings.extend(longitudinal_mode_warnings)
    if longitudinal_mode_overrides:
        for role, mode in sorted(longitudinal_mode_overrides.items(), key=lambda item: _role_sort_key(item[0])):
            prompt_warnings.append(
                "Selected {0} longitudinal mode for {1}.".format(
                    _longitudinal_mode_label(mode),
                    role,
                )
            )
    declared_drop_source_roles = _prompt_roles_present(prompt_text)
    if time_gap_point_overrides:
        for role, points in sorted(time_gap_point_overrides.items(), key=lambda item: _role_sort_key(item[0])):
            prompt_warnings.append(
                "Parsed {0} dynamic time-gap point(s) for {1}.".format(len(points), role)
            )
    if speed_point_overrides:
        for role, points in sorted(speed_point_overrides.items(), key=lambda item: _role_sort_key(item[0])):
            prompt_warnings.append(
                "Parsed {0} dynamic speed point(s) for {1}.".format(len(points), role)
            )
    if dropped_obstacles:
        prompt_warnings.append(
            "Parsed {0} dropped obstacle event(s) from prompt text.".format(len(dropped_obstacles))
        )
    if blueprint_overrides:
        for role, blueprint in sorted(blueprint_overrides.items(), key=lambda item: _role_sort_key(item[0])):
            prompt_warnings.append(
                "Parsed {0} vehicle model as {1}.".format(role, blueprint)
            )
    speed_overrides = _prompt_vehicle_speed_overrides(prompt_text)
    initial_time_gap_overrides = _prompt_vehicle_gap_time_overrides(prompt_text)
    gap_overrides: dict[str, float] = {}
    lane_overrides = _prompt_vehicle_lane_overrides(prompt_text)
    # Conversational lane changes were already emitted as structured lines by
    # _prepare_gap_time_prompt, so they are picked up here like any "role -> lane".
    lane_change_overrides = _prompt_lane_change_overrides(prompt_text)
    takeover_fragments = _prompt_takeover_fragments(raw_prompt_text)
    takeover_requests: list[dict[str, Any]] = []
    if takeover_fragments:
        from .prompt_feedback import PromptInputError
        reason = ('This legacy TOR command does not execute in the prompt runner. '
                  'Use a takeover alert with its time, channel and duration, or configure a driver '
                  'response deadline and fallback action in the Level-3 Session Builder. '
                  'The replacement below creates an alert only; budget, key and target-speed settings need the Session Builder.')
        raise PromptInputError(reason, clause=takeover_fragments[0], reason=reason, code='takeover_alert',
                               example='Takeover alert auditory speech at <start>s for <duration>s.')
    # SCANeR's source parser disables ego lane changes. These recognized
    # events are applied by the CARLA adapter after the source preview.
    scanner_preview_text = prompt_text
    if "ego" in lane_change_overrides:
        scanner_preview_text = "\n".join(
            PROMPT_LANE_CHANGE_RE.sub(
                lambda match: "" if _prompt_role_to_carla_role(match.group("role")) == "ego" else match.group(0),
                fragment,
            )
            for fragment in re.split(r"[\r\n;]+", prompt_text)
        )
    preview = build_preview(
        scanner_preview_text,
        template_path=template_path,
        requested_output=requested_output,
    )
    if not getattr(preview, "ok", False):
        return preview, []

    converted: list[ConvertedScenario] = []
    params_list = list(getattr(preview, "params_list", []) or [])
    for index, params in enumerate(params_list, start=1):
        raw_name = name or getattr(params, "output_filename", "")
        scenario_name = scenario_name_from_output(str(raw_name), fallback=f"scanner_prompt_{index}")
        if len(params_list) > 1 and name:
            scenario_name = scenario_name_from_output(f"{scenario_name}_{index}", fallback=f"scanner_prompt_{index}")
        converted.append(
            params_to_carla_scenario(
                params,
                scenario_name=scenario_name,
                map_name=map_name,
                weather_preset=weather_preset,
                rpc_port=rpc_port,
                quality_level=quality_level,
                prompt_text=prompt_text,
                speed_overrides=speed_overrides,
                gap_overrides=gap_overrides,
                initial_time_gap_overrides=initial_time_gap_overrides,
                lane_overrides=lane_overrides,
                lane_change_overrides=lane_change_overrides,
                time_gap_point_overrides=time_gap_point_overrides,
                speed_point_overrides=speed_point_overrides,
                longitudinal_mode_overrides=longitudinal_mode_overrides,
                blueprint_overrides=blueprint_overrides,
                dropped_obstacles=dropped_obstacles,
                declared_drop_source_roles=declared_drop_source_roles,
                takeover_requests=takeover_requests,
                prompt_warnings=prompt_warnings,
            )
        )
    for item in converted:
        item.scenario['standalone_cues'] = checked_cues(item.scenario)
        from .standalone_cues import describe_cue
        for cue in standalone_cues:
            item.summaries.append(describe_cue(cue))
    return preview, converted


def params_to_carla_scenario(
    params: Any,
    *,
    scenario_name: str,
    map_name: str,
    weather_preset: str,
    rpc_port: int,
    quality_level: str,
    prompt_text: str | None = None,
    speed_overrides: dict[str, float] | None = None,
    gap_overrides: dict[str, float] | None = None,
    initial_time_gap_overrides: dict[str, float] | None = None,
    lane_overrides: dict[str, str] | None = None,
    lane_change_overrides: dict[str, list[dict[str, Any]]] | None = None,
    time_gap_point_overrides: dict[str, list[dict[str, Any]]] | None = None,
    speed_point_overrides: dict[str, list[dict[str, Any]]] | None = None,
    longitudinal_mode_overrides: dict[str, str] | None = None,
    blueprint_overrides: dict[str, str] | None = None,
    dropped_obstacles: list[dict[str, Any]] | None = None,
    declared_drop_source_roles: set[str] | None = None,
    takeover_requests: list[dict[str, Any]] | None = None,
    prompt_warnings: list[str] | None = None,
) -> ConvertedScenario:
    api = _load_scanner_api()
    vehicles = _convert_vehicles(params, api, speed_overrides=speed_overrides, gap_overrides=gap_overrides)
    if prompt_text is not None:
        # SCANeR's template enables a front car even for ego-only prompts.
        # CARLA should only spawn support vehicles requested by the author.
        declared_roles = _prompt_roles_present(prompt_text)
        for role in C.SUPPORT_ROLE_ORDER:
            if role not in declared_roles:
                vehicles[role] = _default_vehicle(role, enabled=False)
    _apply_vehicle_initial_time_gap_overrides(vehicles, initial_time_gap_overrides or {})
    _apply_vehicle_lane_overrides(vehicles, lane_overrides or {})
    _apply_lane_change_overrides(vehicles, lane_change_overrides or {})
    ego_speed_for_gap = float(vehicles.get("ego", {}).get("speed_kmh", C.DEFAULT_EGO_SPEED_KMH) or C.DEFAULT_EGO_SPEED_KMH)
    extension_warnings: list[str] = []
    extension_warnings.extend(
        _apply_vehicle_time_gap_point_overrides(
            vehicles,
            time_gap_point_overrides or {},
            ego_speed_for_gap,
        )
    )
    extension_warnings.extend(
        _apply_vehicle_speed_point_overrides(vehicles, speed_point_overrides or {})
    )
    _apply_vehicle_blueprint_overrides(vehicles, blueprint_overrides or {})
    _normalize_vehicle_speeds(vehicles, preserve_roles=set(speed_overrides or {}) | set(speed_point_overrides or {}))
    ego_speed = float(vehicles["ego"]["speed_kmh"])
    longitudinal_warnings = _enforce_prompt_longitudinal_modes(
        vehicles,
        ego_speed,
        longitudinal_mode_overrides or {},
    )
    _raise_if_converted_unsupported_longitudinal_modes(vehicles)
    takeover_request_list = list(takeover_requests or [])
    _align_takeover_budget_requests(takeover_request_list, vehicles)
    scanner_total_time_s = float(getattr(params, "total_experiment_time_s", C.DEFAULT_TOTAL_TIME_S))
    duration_auto = prompt_scenario_duration_is_auto(prompt_text or "")
    # duration=auto: ignore stray numeric durations (e.g. lane-change lines) and
    # keep the scanner default as the scripted timeline; the runtime hold policy
    # extends the scene until the take-over interaction completes.
    prompt_total_time_s = None if duration_auto else _explicit_prompt_scenario_duration(prompt_text or "")
    total_time_s = prompt_total_time_s if prompt_total_time_s is not None else scanner_total_time_s
    audio_cues = [
        {"time_s": float(event.time_s), "file_name": str(event.file_name)}
        for event in list(getattr(params, "audio_events", []) or [])
    ]
    pedestrians = _convert_pedestrians(params)
    if pedestrians and prompt_text is not None:
        # The legacy parser treats a pedestrian's left -> right as a front-car
        # lane change. Only retain vehicle changes explicitly requested here.
        for role, cfg in vehicles.items():
            if role not in (lane_change_overrides or {}):
                cfg['lane_change_enabled'] = False
                cfg['lane_change_points'] = []
    obstacles = _convert_obstacles(params, api)
    _append_dropped_obstacles(
        obstacles,
        dropped_obstacles or [],
        vehicles,
        total_time_s,
        declared_drop_source_roles or set(),
    )
    total_time_s = _apply_current_scanner_hazard_defaults(
        vehicles,
        pedestrians,
        obstacles,
        prompt_text or "",
        total_time_s=total_time_s,
    )
    warnings = list(prompt_warnings or [])
    warnings.extend(extension_warnings)
    warnings.extend(longitudinal_warnings)
    warnings.extend(_time_gap_profile_design_warnings(vehicles, ego_speed))
    warnings.extend(_conversion_warnings(params, vehicles, pedestrians, obstacles, audio_cues))

    partial = {
        "scenario_name": scenario_name,
        "map_name": map_name,
        "weather_preset": weather_preset,
        "total_time_s": total_time_s,
        "ego_speed_kmh": ego_speed,
        "fixed_delta_seconds": C.DEFAULT_FIXED_DELTA_SECONDS,
        "spawn_point_index": 0,
        "rpc_port": int(rpc_port),
        "launcher_script": "start_dreyevr_ndisplay.ps1",
        "quality_level": quality_level,
        "render_mode": "nDisplay",
        "camera_mode": "off",
        "dreyevr": True,
        "audio_library_root": str(SCANNER_AUDIO_DIR) if audio_cues else "",
        "audio_cues": audio_cues,
        "takeover_requests": takeover_request_list,
        "vehicles": vehicles,
        "pedestrians": pedestrians,
        "obstacles": obstacles,
    }
    ok, payload = validate(partial)
    if not ok:
        raise ValueError(f"Converted scanner prompt failed CARLA schema validation: {payload}")

    if duration_auto:
        events = payload.setdefault("experiment_events", {})
        if isinstance(events, dict):
            events["hold_until_tor_resolution"] = True

    summaries = _summarize_carla_scenario(payload)
    return ConvertedScenario(
        name=str(payload["scenario_name"]),
        scenario=payload,
        summaries=summaries,
        warnings=warnings,
    )


def scenario_name_from_output(output_filename: str, *, fallback: str = "scanner_prompt") -> str:
    stem = Path(str(output_filename or "")).stem.lower()
    name = SAFE_NAME_RE.sub("_", stem).strip("_")
    name = re.sub(r"_+", "_", name)
    if not name:
        name = fallback
    if not re.match(r"^[a-z]", name):
        name = f"scenario_{name}"
    return name[:80].rstrip("_") or fallback


def write_converted_scenarios(converted: list[ConvertedScenario], outputs_dir: Path) -> list[Path]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for item in converted:
        path = outputs_dir / f"{item.name}.json"
        path.write_text(_json_dumps(item.scenario), encoding="utf-8")
        written.append(path)
    return written


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _summarize_carla_scenario(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    from .standalone_cues import describe_cue
    lines.extend(describe_cue(cue) for cue in payload.get('standalone_cues', []))
    vehicles = payload.get("vehicles", {}) or {}
    ego = vehicles.get("ego", {}) or {}
    lines.append("Scenario duration: {0:g} s".format(float(payload.get("total_time_s", 0.0) or 0.0)))
    lines.append(
        "Ego: lane={0}, speed={1:g} km/h".format(
            ego.get("lane", "Middle"),
            float(ego.get("speed_kmh", payload.get("ego_speed_kmh", 0.0)) or 0.0),
        )
    )

    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role, {}) or {}
        if not cfg.get("enabled", False):
            continue
        initial_time_gap_s = cfg.get("initial_time_gap_s")
        if initial_time_gap_s not in (None, ""):
            time_gap_s = float(initial_time_gap_s or 0.0)
            relation = "ahead" if time_gap_s >= 0.0 else "behind"
            gap_text = "{0} {1:g} s".format(relation, abs(time_gap_s))
        else:
            initial_gap_m = float(cfg.get("initial_gap_m", 0.0) or 0.0)
            relation = "ahead" if initial_gap_m >= 0.0 else "behind"
            gap_text = "{0} {1:g} m".format(relation, abs(initial_gap_m))
        lines.append(
            "{0}: lane={1}, {2}, speed={3:g} km/h".format(
                role,
                cfg.get("lane", "Middle"),
                gap_text,
                float(cfg.get("speed_kmh", 0.0) or 0.0),
            )
        )
        for point in cfg.get("lane_change_points", []) or []:
            lines.append(
                "  lane change: {0} -> {1} at {2:g} s over {3:g} s".format(
                    cfg.get("lane", "Middle"),
                    point.get("target_lane", cfg.get("lane", "Middle")),
                    float(point.get("time_s", 0.0) or 0.0),
                    float(point.get("duration_s", 0.0) or 0.0),
                )
            )
        gap_points = cfg.get("gap_points", []) or []
        if gap_points:
            profile = ", ".join(
                "{0:g}s->{1:g}m".format(
                    float(point.get("time_s", 0.0) or 0.0),
                    float(point.get("gap_m", 0.0) or 0.0),
                )
                for point in gap_points
            )
            lines.append("  gap profile: " + profile)
        time_gap_points = cfg.get("time_gap_points", []) or []
        if time_gap_points:
            profile = ", ".join(
                "{0:g}s->{1:g}s".format(
                    float(point.get("time_s", 0.0) or 0.0),
                    float(point.get("time_gap_s", 0.0) or 0.0),
                )
                for point in time_gap_points
            )
            lines.append("  time-gap profile: " + profile)
        speed_points = cfg.get("speed_points", []) or []
        if speed_points:
            profile = ", ".join(
                "{0:g}s->{1:g}km/h".format(
                    float(point.get("time_s", 0.0) or 0.0),
                    float(point.get("speed_kmh", 0.0) or 0.0),
                )
                for point in speed_points
            )
            lines.append("  speed profile: " + profile)

    pedestrians = payload.get("pedestrians", {}) or {}
    for role, cfg in sorted(pedestrians.items()):
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        lines.append(
            "{0}: {1} crosses {2} -> {3}, gap={4:g} m ({7}), t={5:g} s, duration={6:g} s".format(
                role,
                cfg.get("label", role),
                cfg.get("start_side", "Left"),
                cfg.get("end_side", "Right"),
                float(cfg.get("gap_m", 0.0) or 0.0),
                float(cfg.get("start_time_s", 0.0) or 0.0),
                float(cfg.get("duration_s", 0.0) or 0.0),
                'relative to ego at crossing start' if cfg.get('position_reference') == 'ego_at_crossing' else 'from initial ego position',
            )
        )

    obstacles = payload.get("obstacles", {}) or {}
    for role, cfg in sorted(obstacles.items()):
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        if cfg.get('spawn_mode', 'fixed') == 'fixed':
            from .obstacle_positions import obstacle_position_text
            lines.append('{}: {}, lane={}, {}, active={:g}-{:g} s'.format(
                role, cfg.get('label', role), cfg.get('lane', 'Middle'), obstacle_position_text(cfg),
                float(cfg.get('start_time_s', 0)), float(cfg.get('end_time_s', 0))))
            continue
        lines.append(
            "{0}: {1}, lane={2}, gap={3:g} m, active={4:g}-{5:g} s".format(
                role,
                cfg.get("label", role),
                cfg.get("lane", "Middle"),
                float(cfg.get("gap_m", 0.0) or 0.0),
                float(cfg.get("start_time_s", 0.0) or 0.0),
                float(cfg.get("end_time_s", 0.0) or 0.0),
            )
        )

    for req in payload.get("takeover_requests", []) or []:
        if req.get("takeover_budget_s") is not None:
            fpe = float(req.get("time_s", 0.0) or 0.0)
            budget = float(req.get("takeover_budget_s", 0.0) or 0.0)
            buffer_s = float(req.get("collision_buffer_s", 3.0) or 3.0)
            collision_t = fpe + budget
            deadline_t = collision_t - buffer_s
            lines.append(
                "Takeover budget: FPE={0:g}s, budget={1:g}s, collision={2:g}s, deadline={3:g}s".format(
                    fpe, budget, collision_t, deadline_t
                )
            )
            calibration = req.get("budget_calibration", {}) or {}
            if calibration.get("method") == "adjust_support_vehicle_speed":
                lines.append(
                    "  calibrated {0} speed: {1:g} -> {2:g} km/h".format(
                        calibration.get("role", "vehicle"),
                        float(calibration.get("original_speed_kmh", 0.0) or 0.0),
                        float(calibration.get("adjusted_speed_kmh", 0.0) or 0.0),
                    )
                )
            elif calibration.get("method") == "adjust_initial_gap":
                lines.append(
                    "  calibrated {0} gap: {1:g} -> {2:g} m".format(
                        calibration.get("role", "vehicle"),
                        float(calibration.get("original_gap_m", 0.0) or 0.0),
                        float(calibration.get("adjusted_gap_m", 0.0) or 0.0),
                    )
                )
        else:
            lines.append(
                "Takeover request: t={0:g}s, key={1}, target={2:g} km/h, window={3:g}s".format(
                    float(req.get("time_s", 0.0) or 0.0),
                    req.get("key", "SPACE"),
                    float(req.get("target_speed_kmh", 0.0) or 0.0),
                    float(req.get("timeout_s", 0.0) or 0.0),
                )
            )
    for role, cfg in sorted((payload.get("obstacles", {}) or {}).items()):
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        if cfg.get("spawn_mode") == "drop_from_vehicle":
            lines.append(
                "Dropped obstacle: t={0:g}s, {1} drops {2}, offset={3:g}m".format(
                    float(cfg.get("start_time_s", 0.0) or 0.0),
                    cfg.get("drop_source_role", "vehicle"),
                    cfg.get("label") or cfg.get("kind", role),
                    float(cfg.get("drop_offset_m", DEFAULT_DROP_OFFSET_M) or 0.0),
                )
            )
    return lines


def _convert_vehicles(
    params: Any,
    api: dict[str, Any],
    *,
    speed_overrides: dict[str, float] | None = None,
    gap_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    vehicles: dict[str, Any] = {}
    scanner_vehicles = dict(getattr(params, "vehicles", {}) or {})
    for scanner_key, cfg in scanner_vehicles.items():
        role = "ego" if scanner_key == "ego" else FRONTEND_ROLE_MAP.get(scanner_key)
        if role is None:
            continue
        vehicles[role] = _convert_vehicle_cfg(role, cfg, api, force_enabled=(role == "ego"))

    extra_roles = [role for role in C.SUPPORT_ROLE_ORDER if role not in vehicles]
    extra_role_index = 0
    for cfg in list(getattr(params, "extra_vehicles", []) or []):
        role = _scanner_extra_vehicle_role(cfg)
        if role in C.SUPPORT_ROLE_ORDER:
            existing = vehicles.get(role)
            if existing is None or not existing.get("enabled", False):
                vehicles[role] = _convert_vehicle_cfg(role, cfg, api)
                continue
        while extra_role_index < len(extra_roles) and extra_roles[extra_role_index] in vehicles:
            extra_role_index += 1
        if extra_role_index >= len(extra_roles):
            continue
        role = extra_roles[extra_role_index]
        extra_role_index += 1
        vehicles[role] = _convert_vehicle_cfg(role, cfg, api)

    vehicles.setdefault("ego", _default_vehicle("ego", enabled=True))
    for role in C.SUPPORT_ROLE_ORDER:
        vehicles.setdefault(role, _default_vehicle(role, enabled=False))
    _apply_vehicle_speed_overrides(vehicles, speed_overrides or {})
    _apply_vehicle_gap_overrides(vehicles, gap_overrides or {})
    _normalize_vehicle_speeds(vehicles, preserve_roles=set(speed_overrides or {}))
    return vehicles


def _prompt_roles_present(prompt_text: str) -> set[str]:
    roles: set[str] = set()
    for match in PROMPT_ROLE_TOKEN_RE.finditer(prompt_text or ""):
        role = _prompt_role_to_carla_role(match.group("role"))
        if role:
            roles.add(role)
    return roles


def _scanner_extra_vehicle_role(cfg: Any) -> str | None:
    for attr in ("key", "label", "name"):
        value = str(getattr(cfg, attr, "") or "")
        match = re.search(
            r"(?:^|[^a-z0-9])(?:prompt[^a-z0-9]*)?vehicle[^a-z0-9]*(?P<num>[1-6])(?:[^0-9]|$)",
            value,
            re.IGNORECASE,
        )
        if match:
            return f"vehicle_{match.group('num')}"
    return None


def _role_prompt_label(role: str) -> str:
    if role == "ego":
        return "ego"
    match = re.fullmatch(r"vehicle_([1-6])", role)
    if match:
        return f"v{match.group(1)}"
    return role


def _role_sort_key(role: str) -> int:
    if role == "ego":
        return 0
    match = re.fullmatch(r"vehicle_([1-6])", role)
    if match:
        return int(match.group(1))
    return 99


def _segment_role_matches(fragment: str) -> list["re.Match[str]"]:
    """Role tokens that *define* an actor, excluding references.

    In conversational prose a role word can appear as a reference rather than a
    new actor definition, e.g. "1.5 seconds ahead of ego" or "the ego lane".
    Those must not start a new per-actor segment (otherwise the next clause's
    attributes leak onto the wrong actor). We drop a role token when it is the
    object of "of"/"than" or is immediately followed by "lane".
    """
    kept: list["re.Match[str]"] = []
    for match in PROMPT_ROLE_TOKEN_RE.finditer(fragment):
        before = fragment[: match.start()].rstrip()
        prev_word = before.rsplit(None, 1)[-1].lower() if before else ""
        if prev_word in ("of", "than"):
            continue
        after = fragment[match.end():].lstrip().lower()
        if after.startswith("lane"):
            continue
        kept.append(match)
    return kept


def _prompt_vehicle_speed_overrides(prompt_text: str) -> dict[str, float]:
    """Extract explicit per-actor speed settings from the raw prompt text.

    The upstream SCANeR parser is good at geometry/events but can miss speeds
    for support actors. Keep this as a narrow CARLA-side overlay: only segments
    that name an actor and a speed are applied, so lane-change timings such as
    ``t=5s: v1 -> left, duration=5s`` are ignored.
    """
    overrides: dict[str, float] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        role_matches = _segment_role_matches(fragment)
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role:
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            speed_match = PROMPT_SPEED_RE.search(segment)
            if speed_match is None and _has_speed_word(segment):
                speed_match = PROMPT_UNIT_SPEED_RE.search(segment)
            if speed_match is None:
                continue
            overrides[role] = _prompt_speed_to_kmh(
                float(speed_match.group("value")),
                speed_match.group("unit"),
            )
    return overrides


def _prompt_vehicle_blueprint_overrides(prompt_text: str) -> dict[str, str]:
    """Extract prompt-side vehicle model/blueprint choices.

    Supported examples:
      v1: lane=right, ahead=3s, speed=30km/h, model=truck
      v1: lane=right, ahead=3s, type=van
      v1: lane=right, ahead=3s, blueprint=vehicle.carlamotors.carlacola
    """
    overrides: dict[str, str] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        role_matches = list(PROMPT_ROLE_TOKEN_RE.finditer(fragment))
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role or role == "ego":
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            blueprint_match = PROMPT_VEHICLE_BLUEPRINT_RE.search(segment)
            if blueprint_match is not None:
                overrides[role] = str(blueprint_match.group("blueprint")).strip().lower()
                continue
            model_match = PROMPT_VEHICLE_MODEL_RE.search(segment)
            if model_match is None:
                continue
            overrides[role] = _blueprint_for_prompt_vehicle_model(model_match.group("model"))
    return overrides


def _blueprint_for_prompt_vehicle_model(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Vehicle model is empty.")
    if text.lower().startswith("vehicle."):
        return text.lower()
    normalized = re.sub(r"[\s_-]+", "_", text.lower()).strip("_")
    aliases = {
        re.sub(r"[\s_-]+", "_", alias.lower()).strip("_"): blueprint
        for alias, blueprint in C.VEHICLE_BLUEPRINT_ALIASES.items()
    }
    blueprint = aliases.get(normalized)
    if blueprint:
        return blueprint
    raise ValueError(
        "Unknown vehicle model '{0}'. Use one of: {1}; or specify blueprint=vehicle.<make>.<model>.".format(
            text,
            ", ".join(sorted(C.VEHICLE_BLUEPRINT_ALIASES)),
        )
    )


def _prompt_dropped_obstacles(prompt_text: str) -> list[dict[str, Any]]:
    """Extract CARLA-only obstacle drop events from prompt text."""
    dropped: list[dict[str, Any]] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        fragment = fragment.strip()
        if not fragment or PROMPT_DROP_INTENT_RE.search(fragment) is None:
            continue
        role_match = PROMPT_ROLE_TOKEN_RE.search(fragment)
        if role_match is None:
            raise ValueError(
                "Dropped obstacle prompt needs a source vehicle role, e.g. "
                "'t=8s: v1 drops barrel'."
            )
        source_role = _prompt_role_to_carla_role(role_match.group("role"))
        if not source_role:
            raise ValueError("Dropped obstacle source vehicle is not recognized: {0}".format(fragment))
        start_time_s = _first_float(PROMPT_TIME_RE, fragment, default=0.0)
        end_time_s = _first_optional_float(PROMPT_DROP_END_RE, fragment)
        offset_m = _first_float(PROMPT_DROP_OFFSET_RE, fragment, default=DEFAULT_DROP_OFFSET_M)
        dropped.append(
            {
                "source_role": source_role,
                "kind_text": _prompt_drop_kind(fragment),
                "start_time_s": start_time_s,
                "end_time_s": end_time_s,
                "drop_offset_m": max(0.0, offset_m),
            }
        )
    return dropped


def _prompt_drop_kind(fragment: str) -> str:
    match = PROMPT_DROP_KIND_RE.search(fragment or "")
    if match is None:
        return "barrel"
    kind = re.sub(r"\s+", " ", match.group("kind").strip().lower())
    if kind in ("obstacle", "object"):
        return "barrel"
    if kind == "traffic cone":
        return "cone"
    if kind == "crate":
        return "box"
    return kind


def _strip_carla_extension_fragments(prompt_text: str) -> str:
    """Remove CARLA-only prompt fragments before passing text to SCANeR parser."""
    kept: list[str] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        fragment = fragment.strip()
        if not fragment:
            continue
        if PROMPT_LONGITUDINAL_MODE_RE.search(fragment):
            fragment = _strip_prompt_longitudinal_mode_token(fragment)
            if not fragment:
                continue
        if PROMPT_TIME_GAP_PROFILE_RE.search(fragment):
            continue
        if PROMPT_TIME_GAP_EVENT_RE.search(fragment):
            continue
        if PROMPT_GAP_PROFILE_RE.search(fragment):
            continue
        if PROMPT_SPEED_PROFILE_RE.search(fragment):
            continue
        if PROMPT_DROP_INTENT_RE.search(fragment):
            continue
        kept.append(fragment)
    return "\n".join(kept)


def _strip_prompt_longitudinal_mode_token(fragment: str) -> str:
    cleaned = PROMPT_LONGITUDINAL_MODE_TOKEN_RE.sub("", fragment or "")
    cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
    cleaned = re.sub(r":\s*,", ":", cleaned)
    cleaned = cleaned.strip(" ,")
    if PROMPT_ROLE_TOKEN_RE.fullmatch(cleaned.strip(": ")):
        return ""
    return cleaned


def _raise_if_unsupported_longitudinal_prompt_modes(prompt_text: str) -> None:
    """Reject dynamic meter-gap scripts; speed/time-gap scripts are allowed."""
    unsupported: list[str] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        fragment = fragment.strip()
        if not fragment:
            continue
        if PROMPT_GAP_PROFILE_RE.search(fragment):
            unsupported.append(fragment)
    if not unsupported:
        return
    examples = "; ".join(unsupported[:3])
    raise ValueError(
        "Prompt GUI does not support dynamic meter gap_points. Use either a "
        "time-gap profile such as 'v1 time_gap_points: 0s=4.2s, 8.5s=1.0s' "
        "or a speed profile such as 'v1 longitudinal_mode=speed; "
        "v1 speed_points: 0s=30km/h, 8.5s=8km/h'. "
        "Unsupported fragment(s): {0}".format(examples)
    )


def _check_obstacle_prompt_details(prompt_text):
    from .prompt_language import normalize_vehicle_language
    from .prompt_scene_entities import entity_missing_information
    from .prompt_feedback import missing_information
    issues = []
    for sentence in re.split(r'[.!?](?=\s|$)|[\r\n]+', normalize_vehicle_language(prompt_text)):
        if re.match(r'\s*obstacle_\d+\b', sentence, re.I):
            issue = entity_missing_information(sentence.strip())
            if issue:
                issues.append(issue)
    if issues:
        raise missing_information(issues)


def _check_pedestrian_prompt_details(prompt_text):
    """Use the same required crossing details on both prose and DSL routes."""
    from .prompt_language import normalize_vehicle_language
    from .prompt_scene_entities import entity_missing_information
    from .prompt_feedback import missing_information
    issues = []
    for fragment in re.split(r'[\r\n;]+', prompt_text):
        if not re.search(r'\b(?:ped(?:estrian)?|person|walker)[ _#-]*\d+\b', fragment, re.I):
            continue
        for sentence in re.split(r'[.!?](?=\s|$)|[\r\n]+', normalize_vehicle_language(fragment)):
            if sentence.strip().lower().startswith('pedestrian_'):
                issue = entity_missing_information(sentence.strip())
                if issue:
                    issues.append(issue)
    if issues:
        raise missing_information(issues)


def _incomplete_pedestrian_prompt_fragments(prompt_text: str) -> list[str]:
    fragments: list[str] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        fragment = fragment.strip()
        if not fragment or PROMPT_PEDESTRIAN_INTENT_RE.search(fragment) is None:
            continue
        has_side = re.search(r"\b(?:left|middle|right)\b", fragment, flags=re.IGNORECASE) is not None
        has_direction_word = re.search(
            r"\b(?:from|cross(?:es|ing)?|walk(?:s|ing)?|go(?:es|ing)?|to)\b|->|=>",
            fragment,
            flags=re.IGNORECASE,
        ) is not None
        complete_transition = PROMPT_PEDESTRIAN_SIDE_TRANSITION_RE.search(fragment) is not None
        incomplete_transition = PROMPT_PEDESTRIAN_INCOMPLETE_TRANSITION_RE.search(fragment) is not None
        if incomplete_transition or (has_side and has_direction_word and not complete_transition):
            fragments.append(fragment)
    return fragments


def _raise_incomplete_pedestrian_prompt(fragments: list[str]) -> None:
    if not fragments:
        return
    raise ValueError(
        "Pedestrian prompt is incomplete: specify both start and end side, "
        "for example 'ped1: left -> right, 35m, t=2s, duration=4s'. "
        "Problem fragment: {0}".format(fragments[0])
    )


def _raise_if_incomplete_pedestrian_prompt(prompt_text: str) -> None:
    _raise_incomplete_pedestrian_prompt(_incomplete_pedestrian_prompt_fragments(prompt_text))


def _raise_if_conflicting_prompt_longitudinal_modes(
    time_gap_point_overrides: dict[str, list[dict[str, Any]]],
    speed_point_overrides: dict[str, list[dict[str, Any]]],
) -> None:
    conflicts = sorted(
        set(time_gap_point_overrides or {}) & set(speed_point_overrides or {}),
        key=_role_sort_key,
    )
    if not conflicts:
        return
    raise ValueError(
        "Each vehicle can use only one dynamic longitudinal mode. "
        "Remove either time_gap_points or speed_points for: {0}.".format(
            ", ".join(conflicts)
        )
    )


def _normalize_prompt_longitudinal_mode(value: str) -> str | None:
    token = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if token in ("speed", "speed_point", "speed_points", "velocity"):
        return "speed"
    if token in ("time_gap", "headway"):
        return "time_gap"
    if token in ("fixed", "fixed_speed", "constant_speed"):
        return "fixed_speed"
    return None


def _longitudinal_mode_label(mode: str) -> str:
    if mode == "time_gap":
        return "time-gap"
    if mode == "fixed_speed":
        return "fixed-speed"
    return "speed"


def _prompt_vehicle_longitudinal_mode_overrides(prompt_text: str) -> dict[str, str]:
    """Extract explicit per-vehicle longitudinal mode selection commands."""
    overrides: dict[str, str] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        for match in PROMPT_LONGITUDINAL_MODE_RE.finditer(fragment):
            role = _prompt_role_to_carla_role(match.group("role"))
            mode = _normalize_prompt_longitudinal_mode(match.group("mode"))
            if not role or not mode:
                continue
            overrides[role] = mode
    return overrides


def _select_prompt_longitudinal_mode(
    time_gap_point_overrides: dict[str, list[dict[str, Any]]],
    speed_point_overrides: dict[str, list[dict[str, Any]]],
    longitudinal_mode_overrides: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[str]]:
    """Apply explicit mode choices before the generic conflict check."""
    time_gap_points = {
        role: list(points or [])
        for role, points in (time_gap_point_overrides or {}).items()
    }
    speed_points = {
        role: list(points or [])
        for role, points in (speed_point_overrides or {}).items()
    }
    warnings: list[str] = []
    for role, mode in sorted((longitudinal_mode_overrides or {}).items(), key=lambda item: _role_sort_key(item[0])):
        if mode == "speed" and role in time_gap_points:
            time_gap_points.pop(role, None)
            warnings.append(
                "Ignored {0} time_gap_points because speed longitudinal mode is selected.".format(role)
            )
        elif mode == "time_gap" and role in speed_points:
            speed_points.pop(role, None)
            warnings.append(
                "Ignored {0} speed_points because time-gap longitudinal mode is selected.".format(role)
            )
        elif mode == "fixed_speed":
            dropped: list[str] = []
            if role in time_gap_points:
                time_gap_points.pop(role, None)
                dropped.append("time_gap_points")
            if role in speed_points:
                speed_points.pop(role, None)
                dropped.append("speed_points")
            if dropped:
                warnings.append(
                    "Ignored {0} {1} because fixed-speed longitudinal mode is selected.".format(
                        role,
                        " and ".join(dropped),
                    )
                )
    return time_gap_points, speed_points, warnings


def _raise_if_unsupported_initial_meter_gaps(prompt_text: str) -> None:
    """Vehicle initial spacing must be expressed as seconds in prompt GUI."""
    unsupported: list[str] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        fragment = fragment.strip()
        if not fragment:
            continue
        role_matches = list(PROMPT_ROLE_TOKEN_RE.finditer(fragment))
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role or role == "ego":
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            if PROMPT_BEHIND_METERS_RE.search(segment) or PROMPT_AHEAD_METERS_RE.search(segment):
                unsupported.append(segment.strip())
    if not unsupported:
        return
    examples = "; ".join(unsupported[:3])
    raise ValueError(
        "Prompt GUI now supports vehicle initial spacing only as time gaps. "
        "Use seconds, for example 'v1: lane=right, ahead=4.2s' or "
        "'v2: lane=middle, behind=3s'. Meter-based vehicle spacing is not "
        "accepted. Unsupported fragment(s): {0}".format(examples)
    )


def _initial_time_gap_for_cfg(cfg: dict[str, Any], ego_speed_mps: float) -> float:
    value = cfg.get("initial_time_gap_s")
    if value not in (None, ""):
        return float(value or 0.0)
    return float(cfg.get("initial_gap_m", 0.0) or 0.0) / max(0.001, ego_speed_mps)


def _time_gap_points_from_gap_points(
    points: list[dict[str, Any]],
    ego_speed_mps: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for point in points or []:
        out.append(
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "time_gap_s": float(point.get("gap_m", 0.0) or 0.0) / max(0.001, ego_speed_mps),
            }
        )
    out.sort(key=lambda point: point["time_s"])
    return out


def _carla_official_default_longitudinal_limits() -> dict[str, Any]:
    brake_force_n = (
        CARLA_OFFICIAL_DEFAULT_WHEEL_COUNT
        * CARLA_OFFICIAL_DEFAULT_MAX_BRAKE_TORQUE_NM
        / max(0.001, CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M)
    )
    brake_limit_mps2 = brake_force_n / max(1.0, CARLA_OFFICIAL_DEFAULT_MASS_KG)
    tire_limit_mps2 = CARLA_OFFICIAL_DEFAULT_TIRE_FRICTION * CARLA_STANDARD_GRAVITY_MPS2
    drive_force_n = (
        CARLA_OFFICIAL_DEFAULT_ENGINE_TORQUE_NM
        * CARLA_OFFICIAL_DEFAULT_FINAL_RATIO
        / max(0.001, CARLA_OFFICIAL_DEFAULT_WHEEL_RADIUS_M)
    )
    drive_limit_mps2 = drive_force_n / max(1.0, CARLA_OFFICIAL_DEFAULT_MASS_KG)
    return {
        "accel_mps2": min(drive_limit_mps2, tire_limit_mps2),
        "decel_mps2": min(brake_limit_mps2, tire_limit_mps2),
        "source": "CARLA official default VehiclePhysicsControl/WheelPhysicsControl",
    }


def _time_gap_profile_design_warnings(
    vehicles: dict[str, Any],
    ego_speed_kmh: float,
) -> list[str]:
    """Warn when a time-gap profile asks for an implausibly fast HF transition."""
    warnings: list[str] = []
    ego_speed_mps = max(0.001, float(ego_speed_kmh or 0.0) / 3.6)
    physics_limits = _carla_official_default_longitudinal_limits()
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role)
        if not cfg or not cfg.get("enabled", False):
            continue
        points = [
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "time_gap_s": float(point.get("time_gap_s", 0.0) or 0.0),
            }
            for point in list(cfg.get("time_gap_points", []) or [])
            if isinstance(point, dict)
        ]
        if not points:
            continue
        points.sort(key=lambda point: point["time_s"])

        for point in points:
            gap_s = float(point["time_gap_s"])
            if abs(gap_s) < TIME_GAP_NEAR_ZERO_S:
                warnings.append(
                    "Time-gap profile warning for {0}: target at {1:g}s is {2:g}s, "
                    "below the {3:g}s near-zero headway threshold.".format(
                        role,
                        float(point["time_s"]),
                        gap_s,
                        TIME_GAP_NEAR_ZERO_S,
                    )
                )

        initial_gap_s = _initial_time_gap_for_cfg(cfg, ego_speed_mps)
        anchors: list[tuple[float, float]] = []
        if points[0]["time_s"] > 0.0:
            anchors.append((0.0, initial_gap_s))
        for point in points:
            anchors.append((max(0.0, float(point["time_s"])), float(point["time_gap_s"])))

        prev_t, prev_gap_s = anchors[0]
        for cur_t, cur_gap_s in anchors[1:]:
            dt = cur_t - prev_t
            if dt <= 0.0:
                warnings.append(
                    "Time-gap profile warning for {0}: duplicate/non-increasing point at {1:g}s; "
                    "time-gap transition duration must be greater than 0s.".format(role, cur_t)
                )
                prev_t, prev_gap_s = cur_t, cur_gap_s
                continue

            delta_gap_s = cur_gap_s - prev_gap_s
            delta_gap_m = delta_gap_s * ego_speed_mps
            implied_accel_mps2 = 2.0 * delta_gap_m / (dt * dt)
            abs_accel_mps2 = abs(implied_accel_mps2)
            hard_limit_mps2 = float(
                physics_limits["decel_mps2"] if implied_accel_mps2 < 0.0 else physics_limits["accel_mps2"]
            )
            warning_limit_mps2 = hard_limit_mps2 * TIME_GAP_WARNING_LIMIT_RATIO
            if abs_accel_mps2 <= warning_limit_mps2:
                prev_t, prev_gap_s = cur_t, cur_gap_s
                continue

            recommended_duration_s = (2.0 * abs(delta_gap_m) / max(0.001, warning_limit_mps2)) ** 0.5
            severity = "severe" if abs_accel_mps2 > hard_limit_mps2 else "warning"
            action = "closing" if implied_accel_mps2 < 0.0 else "opening"
            warnings.append(
                "Time-gap profile {0} for {1}: {2:g}s->{3:g}s changes headway "
                "from {4:g}s to {5:g}s over {6:g}s at ego {7:g} km/h, implying about "
                "{8:g} m/s^2 {9}; CARLA-derived {10} limit is {11:g} m/s^2 "
                "(warning above {12:g} m/s^2, source: {13}); extend this transition "
                "to at least {14:g}s to stay below the warning band.".format(
                    severity,
                    role,
                    prev_t,
                    cur_t,
                    prev_gap_s,
                    cur_gap_s,
                    dt,
                    float(ego_speed_kmh or 0.0),
                    implied_accel_mps2,
                    action,
                    "braking" if implied_accel_mps2 < 0.0 else "acceleration",
                    hard_limit_mps2,
                    warning_limit_mps2,
                    physics_limits["source"],
                    recommended_duration_s,
                )
            )
            prev_t, prev_gap_s = cur_t, cur_gap_s
    return warnings


def _enforce_prompt_longitudinal_modes(
    vehicles: dict[str, Any],
    ego_speed_kmh: float,
    longitudinal_mode_overrides: dict[str, str] | None = None,
) -> list[str]:
    """Normalize prompt GUI output to one dynamic longitudinal mode per vehicle."""
    ego_speed_mps = max(0.001, float(ego_speed_kmh or 0.0) / 3.6)
    selected_modes = longitudinal_mode_overrides or {}
    warnings: list[str] = []
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role)
        if not cfg or not cfg.get("enabled", False):
            continue

        initial_time_gap_s = _initial_time_gap_for_cfg(cfg, ego_speed_mps)
        cfg["initial_time_gap_s"] = initial_time_gap_s
        cfg["initial_gap_m"] = 0.0

        gap_points = list(cfg.get("gap_points", []) or [])
        time_gap_points = list(cfg.get("time_gap_points", []) or [])
        speed_points = list(cfg.get("speed_points", []) or [])
        selected_mode = selected_modes.get(role)

        if selected_mode == "fixed_speed":
            dropped: list[str] = []
            if gap_points:
                dropped.append("gap_points")
            if time_gap_points:
                dropped.append("time_gap_points")
            if speed_points:
                dropped.append("speed_points")
            if dropped:
                warnings.append(
                    "Dropped {0} {1} because fixed-speed longitudinal mode is selected.".format(
                        role,
                        " and ".join(dropped),
                    )
                )
            cfg["gap_points"] = []
            cfg["time_gap_points"] = []
            cfg["speed_points"] = []
            continue

        if selected_mode == "speed":
            if gap_points:
                warnings.append(
                    "Dropped {0} generated gap_points because speed longitudinal mode is selected.".format(role)
                )
            if time_gap_points:
                warnings.append(
                    "Dropped {0} time_gap_points because speed longitudinal mode is selected.".format(role)
                )
            gap_points = []
            time_gap_points = []
        elif selected_mode == "time_gap":
            if speed_points:
                warnings.append(
                    "Dropped {0} speed_points because time-gap longitudinal mode is selected.".format(role)
                )
            speed_points = []

        if gap_points and not time_gap_points and not speed_points:
            time_gap_points = _time_gap_points_from_gap_points(gap_points, ego_speed_mps)
            warnings.append(
                "Converted {0} generated gap_points to time_gap_points.".format(role)
            )
        elif gap_points and time_gap_points:
            warnings.append(
                "Dropped {0} generated gap_points because explicit time_gap_points are present.".format(role)
            )
        elif gap_points and speed_points:
            warnings.append(
                "Dropped {0} generated gap_points because explicit speed_points are present.".format(role)
            )
        cfg["gap_points"] = []

        if time_gap_points and speed_points:
            raise ValueError(
                "Each vehicle can use only one dynamic longitudinal mode. "
                "{0} has both time_gap_points and speed_points.".format(role)
            )

        if time_gap_points:
            time_gap_points = [
                {
                    "time_s": float(point.get("time_s", 0.0) or 0.0),
                    "time_gap_s": float(point.get("time_gap_s", 0.0) or 0.0),
                }
                for point in time_gap_points
            ]
            time_gap_points.sort(key=lambda point: point["time_s"])
            if time_gap_points[0]["time_s"] <= 0.0:
                cfg["initial_time_gap_s"] = time_gap_points[0]["time_gap_s"]
            cfg["time_gap_points"] = time_gap_points
            cfg["speed_points"] = []
            continue

        if speed_points:
            speed_points = [
                {
                    "time_s": float(point.get("time_s", 0.0) or 0.0),
                    "speed_kmh": max(0.0, float(point.get("speed_kmh", 0.0) or 0.0)),
                }
                for point in speed_points
            ]
            speed_points.sort(key=lambda point: point["time_s"])
            if speed_points[0]["time_s"] <= 0.0:
                cfg["speed_kmh"] = speed_points[0]["speed_kmh"]
            cfg["speed_points"] = speed_points
            cfg["time_gap_points"] = []
            continue

        cfg["time_gap_points"] = []
        cfg["speed_points"] = []
    return warnings


def _raise_if_converted_unsupported_longitudinal_modes(vehicles: dict[str, Any]) -> None:
    """Reject unsupported or ambiguous dynamic longitudinal scripts."""
    offenders: list[str] = []
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role) or {}
        if not cfg.get("enabled", False):
            continue
        if cfg.get("gap_points"):
            offenders.append("{0}.gap_points".format(role))
        if cfg.get("time_gap_points") and cfg.get("speed_points"):
            offenders.append("{0}.time_gap_points+speed_points".format(role))
    if not offenders:
        return
    raise ValueError(
        "Prompt GUI produced unsupported or ambiguous longitudinal field(s): {0}. "
        "Use one mode per vehicle: speed_points or time_gap_points.".format(
            ", ".join(offenders)
        )
    )


def _prompt_vehicle_gap_point_overrides(prompt_text: str) -> dict[str, list[dict[str, Any]]]:
    """Extract dynamic CARLA gap profiles from prompt text.

    Supported syntax example:
      v1 gap_points: 0s=35m, 5s=32m, 7s=24m, 10s=14m
    """
    overrides: dict[str, list[dict[str, Any]]] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        profile_match = PROMPT_GAP_PROFILE_RE.search(fragment)
        if profile_match is None:
            continue
        role = _prompt_role_to_carla_role(profile_match.group("role"))
        if not role or role == "ego":
            continue
        points = [
            {
                "time_s": float(match.group("time")),
                "gap_m": float(match.group("gap")),
            }
            for match in PROMPT_GAP_POINT_RE.finditer(fragment)
        ]
        if not points:
            continue
        points.sort(key=lambda point: float(point.get("time_s", 0.0) or 0.0))
        overrides[role] = points
    return overrides


def _prompt_vehicle_time_gap_point_overrides(prompt_text: str) -> dict[str, list[dict[str, Any]]]:
    """Extract dynamic time-gap profiles for non-ego CARLA vehicles.

    Supported syntax examples:
      v1 time_gap_points: 5s=6s, 15s=3s
      t=5s: v1 time gap=6s
    """
    overrides: dict[str, list[dict[str, Any]]] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        profile_match = PROMPT_TIME_GAP_PROFILE_RE.search(fragment)
        if profile_match is not None:
            role = _prompt_role_to_carla_role(profile_match.group("role"))
            if role == 'ego':
                raise ValueError('Ego cannot have a time-gap profile relative to itself. Put the headway on a support vehicle, or use ego speed_points.')
            if not role:
                continue
            points = [
                {
                    "time_s": float(match.group("time")),
                    "time_gap_s": float(match.group("gap")),
                }
                for match in PROMPT_TIME_GAP_POINT_RE.finditer(fragment)
            ]
            if points:
                overrides.setdefault(role, []).extend(points)
            continue

        event_match = PROMPT_TIME_GAP_EVENT_RE.search(fragment)
        if event_match is None:
            continue
        role = _prompt_role_to_carla_role(event_match.group("role"))
        if role == 'ego':
            raise ValueError('Ego cannot have a time gap relative to itself. Put the headway on a support vehicle.')
        if not role:
            continue
        overrides.setdefault(role, []).append(
            {
                "time_s": float(event_match.group("time")),
                "time_gap_s": float(event_match.group("gap")),
            }
        )

    for role, points in list(overrides.items()):
        points.sort(key=lambda point: float(point.get("time_s", 0.0) or 0.0))
        overrides[role] = points
    return overrides


def _prompt_vehicle_speed_point_overrides(prompt_text: str) -> dict[str, list[dict[str, Any]]]:
    """Extract dynamic speed profiles for ego and support vehicles."""
    overrides: dict[str, list[dict[str, Any]]] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        profile_match = PROMPT_SPEED_PROFILE_RE.search(fragment)
        if profile_match is None:
            continue
        role = _prompt_role_to_carla_role(profile_match.group("role"))
        if not role:
            continue
        points = [
            {
                "time_s": float(match.group("time")),
                "speed_kmh": _prompt_speed_to_kmh(
                    float(match.group("speed")),
                    match.group("unit") or "km/h",
                ),
            }
            for match in PROMPT_SPEED_POINT_RE.finditer(fragment)
        ]
        if not points:
            continue
        points.sort(key=lambda point: float(point.get("time_s", 0.0) or 0.0))
        overrides[role] = points
    return overrides


def _prompt_vehicle_gap_overrides(prompt_text: str) -> dict[str, float]:
    """Legacy helper for meter-based actor gaps.

    Prompt GUI now rejects these before conversion; keep this parser available
    only for older internal call sites.
    """
    overrides: dict[str, float] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        role_matches = list(PROMPT_ROLE_TOKEN_RE.finditer(fragment))
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role or role == "ego":
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            behind_match = PROMPT_BEHIND_METERS_RE.search(segment)
            if behind_match is not None:
                overrides[role] = -float(behind_match.group("value"))
                continue
            ahead_match = PROMPT_AHEAD_METERS_RE.search(segment)
            if ahead_match is not None:
                overrides[role] = float(ahead_match.group("value"))
    return overrides


def _prompt_vehicle_gap_time_roles(prompt_text: str) -> set[str]:
    roles: set[str] = set()
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        role_matches = _segment_role_matches(fragment)
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role or role == "ego":
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            if (
                PROMPT_BEHIND_TIME_RE.search(segment)
                or PROMPT_AHEAD_TIME_RE.search(segment)
                or PROMPT_BEHIND_TIME_PROSE_RE.search(segment)
                or PROMPT_AHEAD_TIME_PROSE_RE.search(segment)
            ):
                roles.add(role)
    return roles


def _prompt_vehicle_gap_time_overrides(prompt_text: str) -> dict[str, float]:
    """Extract actor gaps expressed as signed seconds.

    Meter-based vehicle initial spacing is rejected before conversion.
    """
    overrides: dict[str, float] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        role_matches = _segment_role_matches(fragment)
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role or role == "ego":
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            if PROMPT_BEHIND_METERS_RE.search(segment) or PROMPT_AHEAD_METERS_RE.search(segment):
                continue
            behind_match = PROMPT_BEHIND_TIME_RE.search(segment) or PROMPT_BEHIND_TIME_PROSE_RE.search(segment)
            if behind_match is not None:
                overrides[role] = -float(behind_match.group("value"))
                continue
            ahead_match = PROMPT_AHEAD_TIME_RE.search(segment) or PROMPT_AHEAD_TIME_PROSE_RE.search(segment)
            if ahead_match is not None:
                overrides[role] = float(ahead_match.group("value"))
    return overrides


def _prompt_vehicle_lane_overrides(prompt_text: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        # Lane words inside a lane-CHANGE statement are change TARGETS, not the
        # vehicle's initial lane ("at t=20s, v1 change lane to left" must not
        # rewrite v1's starting lane). Mask those spans out.
        change_spans = [m.span() for m in PROMPT_LANE_CHANGE_RE.finditer(fragment)]
        change_spans += [m.span() for m in PROMPT_LANE_CHANGE_TRAILING_TIME_RE.finditer(fragment)]
        change_spans += [m.span() for m in PROMPT_PROSE_LANE_CHANGE_RE.finditer(fragment)]

        def _inside_change(start: int, end: int) -> bool:
            return any(s <= start and end <= e for s, e in change_spans)

        role_matches = _segment_role_matches(fragment)
        for index, match in enumerate(role_matches):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role:
                continue
            next_start = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(fragment)
            segment = fragment[match.start():next_start]
            offset = match.start()
            # "lane=middle" (structured) or "in the middle lane" (prose);
            # structured form keeps priority over prose, as before.
            for lane_match in list(PROMPT_LANE_RE.finditer(segment)) + list(PROMPT_LANE_PROSE_RE.finditer(segment)):
                if _inside_change(offset + lane_match.start(), offset + lane_match.end()):
                    continue
                overrides[role] = _lane(lane_match.group("lane"))
                break
    return overrides


def _normalize_prompt_turns(prompt_text: str) -> str:
    """Resolve relative turns against each actor's chronological lane history."""
    if not re.search(r"\bturns?\b", prompt_text, re.IGNORECASE):
        return prompt_text
    lanes = _prompt_vehicle_lane_overrides(prompt_text)
    # Include existing prose events in the same timeline before resolving
    # relative directions, so mixed command styles retain all lane changes.
    prompt_text = "\n".join(
        PROMPT_PROSE_LANE_CHANGE_RE.sub(
            lambda match: (
                f"t={match.group('time')}s: {match.group('role')} -> "
                f"{lanes.get('ego', 'Middle') if match.group('target').lower() == 'ego' else match.group('target')}, "
                f"duration={match.group('duration') or C.DEFAULT_LANE_CHANGE_DURATION_S}s"
            ),
            fragment,
        )
        for fragment in re.split(r"[\r\n;]+", prompt_text)
    )
    fragments = re.split(r"[\r\n;]+", prompt_text)
    events: list[tuple[float, int, re.Match[str]]] = []
    for index, fragment in enumerate(fragments):
        matches = list(PROMPT_LANE_CHANGE_RE.finditer(fragment))
        matches += list(PROMPT_LANE_CHANGE_TRAILING_TIME_RE.finditer(fragment))
        for turn in re.finditer(r"\bturns?\s+(?:left|right)\b", fragment, re.IGNORECASE):
            if not any(match.start() <= turn.start() < match.end() for match in matches):
                raise ValueError(f"Specify a vehicle and time for lane change: {fragment.strip()}")
        events.extend((float(match.group("time")), index, match) for match in matches)

    previous_end: dict[str, float] = {}
    replacements: dict[int, list[tuple[int, int, str]]] = {}
    lane_order = ("Left", "Middle", "Right")
    for time_s, index, match in sorted(events, key=lambda event: event[0]):
        role = _prompt_role_to_carla_role(match.group("role"))
        if not role:
            continue
        target = _lane(match.group("lane"))
        duration = float(match.group("duration") or C.DEFAULT_LANE_CHANGE_DURATION_S)
        if match.group("turn"):
            if time_s < previous_end.get(role, 0.0):
                raise ValueError(f"{role} is still changing lanes at {time_s:g}s; move the turn after it finishes.")
            initial_lane = AUTOFILL_EGO_LANE if role == "ego" else AUTOFILL_VEHICLE_LANE
            current = lanes.get(role, _lane(initial_lane))
            if target not in ("Left", "Right"):
                raise ValueError("Use turn left/right for an adjacent lane, or go to middle lane.")
            target_index = lane_order.index(current) + (-1 if target == "Left" else 1)
            if not 0 <= target_index < len(lane_order):
                raise ValueError(f"No adjacent {target.lower()} lane for {role} in {current} at {time_s:g}s.")
            target = lane_order[target_index]
            replacement = (
                f"t={time_s:g}s: {match.group('role')} -> {target.lower()}, duration={duration:g}s"
            )
            replacements.setdefault(index, []).append((match.start(), match.end(), replacement))
        lanes[role] = target
        previous_end[role] = time_s + duration
    for index, changes in replacements.items():
        for start, end, replacement in sorted(changes, reverse=True):
            fragments[index] = fragments[index][:start] + replacement + fragments[index][end:]
    return "\n".join(fragments)


def _prompt_lane_change_overrides(prompt_text: str) -> dict[str, list[dict[str, Any]]]:
    prompt_text = _normalize_prompt_turns(prompt_text or "")
    overrides: dict[str, list[dict[str, Any]]] = {}
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        matches = list(PROMPT_LANE_CHANGE_RE.finditer(fragment))
        matches += list(PROMPT_LANE_CHANGE_TRAILING_TIME_RE.finditer(fragment))
        for match in sorted(matches, key=lambda item: item.start()):
            role = _prompt_role_to_carla_role(match.group("role"))
            if not role:
                continue
            duration = match.group("duration")
            point = {
                "time_s": float(match.group("time")),
                "target_lane": _lane(match.group("lane")),
                "duration_s": float(duration) if duration is not None else C.DEFAULT_LANE_CHANGE_DURATION_S,
            }
            overrides.setdefault(role, []).append(point)
    for points in overrides.values():
        points.sort(key=lambda point: float(point.get("time_s", 0.0) or 0.0))
    return overrides


def _prompt_prose_lane_change_overrides(
    prompt_text: str, lane_overrides: dict[str, str]
) -> dict[str, list[dict[str, Any]]]:
    """Parse conversational lane changes, e.g. 'After 10 seconds, vehicle 1 takes
    3 seconds to cut into the ego lane from the right.' -> {vehicle_1: [{time_s:10,
    target_lane:<ego lane>, duration_s:3}]}. Complements the structured
    'role -> lane' form (PROMPT_LANE_CHANGE_RE)."""
    overrides: dict[str, list[dict[str, Any]]] = {}
    for match in PROMPT_PROSE_LANE_CHANGE_RE.finditer(prompt_text or ""):
        role = _prompt_role_to_carla_role(match.group("role"))
        if not role:
            continue
        target_raw = match.group("target").lower()
        if target_raw == "ego":
            target_lane = lane_overrides.get("ego") or _lane("middle")
        else:
            target_lane = _lane(target_raw)
        duration = match.group("duration")
        overrides.setdefault(role, []).append(
            {
                "time_s": float(match.group("time")),
                "target_lane": target_lane,
                "duration_s": float(duration) if duration is not None else C.DEFAULT_LANE_CHANGE_DURATION_S,
            }
        )
    for points in overrides.values():
        points.sort(key=lambda point: float(point.get("time_s", 0.0) or 0.0))
    return overrides


def _has_speed_word(text: str) -> bool:
    if re.search(
        r"\b(?:speed|velocity|driving|drives|going|travel(?:ing|ling|s)?|cruising|moving)\b"
        r"|速度|速率|车速|車速",
        text or "",
        re.IGNORECASE,
    ):
        return True
    # A bare "60 mph" with an explicit speed unit also counts (the unit regex
    # never matches plain "1.5 seconds", so this stays safe).
    return bool(PROMPT_UNIT_SPEED_RE.search(text or ""))


def _prompt_role_to_carla_role(token: str) -> str | None:
    normalized = re.sub(r"[\s_-]+", "", str(token or "").strip().lower())
    if normalized in ("ego", "hero", "tesla"):
        return "ego"
    if normalized == "front":
        return "vehicle_1"
    if normalized == "rear":
        return "vehicle_2"
    if normalized in ("leftvehicle", "leftcar"):
        return "vehicle_3"
    match = re.fullmatch(r"(?:v|vehicle)([1-6])", normalized)
    if match:
        return f"vehicle_{match.group(1)}"
    return None


def _prompt_speed_to_kmh(value: float, unit: str | None) -> float:
    normalized_unit = re.sub(r"\s+", "", str(unit or "mph").lower())
    if normalized_unit in ("km/h", "kmh", "kph"):
        return value
    if normalized_unit in ("m/s", "mps"):
        return value * 3.6
    return value * MPH_TO_KMH


def _prompt_takeover_requests(prompt_text: str) -> list[dict[str, Any]]:
    return []


def _prompt_takeover_fragments(prompt_text: str) -> list[str]:
    fragments: list[str] = []
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        if PROMPT_TAKEOVER_RE.search(fragment):
            fragments.append(fragment.strip())
    return fragments


def _align_takeover_budget_requests(requests: list[dict[str, Any]], vehicles: dict[str, Any]) -> None:
    first_lane_change_time = _first_support_lane_change_time(vehicles)
    for req in requests:
        budget = req.get("takeover_budget_s")
        if budget is None:
            continue
        try:
            budget_s = max(0.001, float(budget))
            buffer_s = max(0.0, float(req.get("collision_buffer_s", 3.0) or 3.0))
        except (TypeError, ValueError):
            continue
        if first_lane_change_time is not None and float(req.get("time_s", 0.0) or 0.0) <= 0.0:
            req["time_s"] = first_lane_change_time
        req["takeover_budget_s"] = budget_s
        req["collision_buffer_s"] = buffer_s
        req["force_brake_on_miss"] = bool(req.get("force_brake_on_miss", True))
        req["timeout_s"] = max(0.001, budget_s - min(buffer_s, budget_s))


def _first_support_lane_change_time(vehicles: dict[str, Any]) -> float | None:
    candidates: list[float] = []
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role) or {}
        for point in cfg.get("lane_change_points", []) or []:
            try:
                candidates.append(float(point.get("time_s", 0.0)))
            except (TypeError, ValueError):
                pass
        if cfg.get("lane_change_enabled", False) and cfg.get("lane_change_target_lane") != cfg.get("lane"):
            try:
                candidates.append(float(cfg.get("lane_change_time_s", 0.0)))
            except (TypeError, ValueError):
                pass
    return min(candidates) if candidates else None


def _calibrate_takeover_budget_requests(
    requests: list[dict[str, Any]],
    vehicles: dict[str, Any],
    *,
    explicit_speeds: dict[str, float],
    explicit_gaps: dict[str, float],
) -> None:
    """Make the precipitating event satisfy the requested takeover budget.

    The experimental definition is:
      - first precipitating event (FPE) = lane-change start time
      - takeover budget = TTC at FPE if the driver does nothing

    A TTC condition needs one free variable. If both ego and support vehicle
    speeds are explicit in the prompt, preserve those speeds and solve the
    initial gap. Otherwise preserve the authored gap and solve support speed.

    For a support car initially ahead of ego, the no-action TTC at FPE is:

        TTC = (initial_gap - collision_clearance - closing_speed * FPE_time)
              / closing_speed

    Therefore:

        initial_gap = collision_clearance + closing_speed * (FPE_time + TTC)
        closing_speed = (initial_gap - collision_clearance) / (FPE_time + TTC)

    `initial_gap_m` is CARLA longitudinal center-to-center distance, so a small
    clearance is subtracted to approximate bumper contact.
    """
    ego_cfg = vehicles.get("ego") or {}
    ego_speed_kmh = float(ego_cfg.get("speed_kmh", C.DEFAULT_EGO_SPEED_KMH) or C.DEFAULT_EGO_SPEED_KMH)
    ego_speed_mps = ego_speed_kmh / 3.6
    if ego_speed_mps <= 0.0:
        return

    for req in requests:
        budget = req.get("takeover_budget_s")
        if budget is None:
            continue
        role = _first_support_lane_change_role_at_time(vehicles, float(req.get("time_s", 0.0) or 0.0))
        if role is None:
            continue
        cfg = vehicles.get(role) or {}
        if cfg.get("gap_points"):
            req["budget_calibration"] = {
                "role": role,
                "method": "skipped_gap_points",
                "reason": "explicit gap_points control TTC instead of speed",
            }
            continue
        if cfg.get("time_gap_points"):
            req["budget_calibration"] = {
                "role": role,
                "method": "skipped_time_gap_points",
                "reason": "explicit time_gap_points control TTC instead of speed",
            }
            continue
        try:
            fpe_time_s = max(0.0, float(req.get("time_s", 0.0) or 0.0))
            budget_s = max(0.001, float(budget))
            initial_gap_m = float(cfg.get("initial_gap_m", 0.0) or 0.0)
            original_speed_kmh = float(cfg.get("speed_kmh", ego_speed_kmh) or ego_speed_kmh)
            collision_clearance_m = max(0.0, float(req.get("collision_clearance_m", 5.0) or 5.0))
        except (TypeError, ValueError):
            continue

        support_speed_explicit = role in explicit_speeds
        ego_speed_explicit = "ego" in explicit_speeds
        if support_speed_explicit and ego_speed_explicit:
            support_speed_mps = original_speed_kmh / 3.6
            closing_mps = ego_speed_mps - support_speed_mps
            if closing_mps <= 0.0:
                req["budget_calibration"] = {
                    "role": role,
                    "method": "skipped_unreachable_gap",
                    "reason": "support vehicle speed must be lower than ego speed for rear-end TTC",
                    "ego_speed_kmh": ego_speed_kmh,
                    "support_speed_kmh": original_speed_kmh,
                    "takeover_budget_s": budget_s,
                }
                continue
            adjusted_gap_m = collision_clearance_m + closing_mps * (fpe_time_s + budget_s)
            cfg["initial_gap_m"] = adjusted_gap_m
            req["collision_clearance_m"] = collision_clearance_m
            req["budget_calibration"] = {
                "role": role,
                "method": "adjust_initial_gap",
                "ego_speed_kmh": ego_speed_kmh,
                "support_speed_kmh": original_speed_kmh,
                "original_gap_m": initial_gap_m,
                "adjusted_gap_m": adjusted_gap_m,
                "collision_clearance_m": collision_clearance_m,
                "fpe_time_s": fpe_time_s,
                "takeover_budget_s": budget_s,
                "closing_speed_mps": closing_mps,
                "predicted_collision_time_s": fpe_time_s + budget_s,
            }
            continue

        if initial_gap_m <= collision_clearance_m:
            req["budget_calibration"] = {
                "role": role,
                "method": "skipped_invalid_gap",
                "initial_gap_m": initial_gap_m,
                "collision_clearance_m": collision_clearance_m,
            }
            continue

        closing_mps = (initial_gap_m - collision_clearance_m) / (fpe_time_s + budget_s)
        vehicle_speed_mps = ego_speed_mps - closing_mps
        if vehicle_speed_mps <= 0.1:
            req["budget_calibration"] = {
                "role": role,
                "method": "skipped_unreachable_speed",
                "required_speed_kmh": vehicle_speed_mps * 3.6,
                "ego_speed_kmh": ego_speed_kmh,
                "initial_gap_m": initial_gap_m,
                "collision_clearance_m": collision_clearance_m,
                "fpe_time_s": fpe_time_s,
                "takeover_budget_s": budget_s,
            }
            continue

        adjusted_speed_kmh = vehicle_speed_mps * 3.6
        cfg["speed_kmh"] = adjusted_speed_kmh
        req["collision_clearance_m"] = collision_clearance_m
        req["budget_calibration"] = {
            "role": role,
            "method": "adjust_support_vehicle_speed",
            "ego_speed_kmh": ego_speed_kmh,
            "original_speed_kmh": original_speed_kmh,
            "adjusted_speed_kmh": adjusted_speed_kmh,
            "initial_gap_m": initial_gap_m,
            "collision_clearance_m": collision_clearance_m,
            "fpe_time_s": fpe_time_s,
            "takeover_budget_s": budget_s,
            "closing_speed_mps": closing_mps,
            "predicted_collision_time_s": fpe_time_s + budget_s,
        }


def _first_support_lane_change_role_at_time(vehicles: dict[str, Any], time_s: float) -> str | None:
    best_role: str | None = None
    best_time: float | None = None
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role) or {}
        times: list[float] = []
        for point in cfg.get("lane_change_points", []) or []:
            try:
                times.append(float(point.get("time_s", 0.0)))
            except (TypeError, ValueError):
                pass
        if cfg.get("lane_change_enabled", False) and cfg.get("lane_change_target_lane") != cfg.get("lane"):
            try:
                times.append(float(cfg.get("lane_change_time_s", 0.0)))
            except (TypeError, ValueError):
                pass
        for candidate in times:
            if candidate < 0.0:
                continue
            if abs(candidate - time_s) > 0.25:
                continue
            if best_time is None or candidate < best_time:
                best_time = candidate
                best_role = role
    if best_role is not None:
        return best_role
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role) or {}
        if cfg.get("enabled") and (cfg.get("lane_change_points") or cfg.get("lane_change_enabled")):
            return role
    return None


def _convert_n_key_takeover_requests(params: Any, vehicles: dict[str, Any]) -> list[dict[str, Any]]:
    n_key = getattr(params, "n_key_intervention", None)
    if n_key is None or not bool(getattr(n_key, "enabled", False)):
        return []
    vehicle_key = str(getattr(n_key, "lane_change_vehicle_key", "front") or "front")
    role = "ego" if vehicle_key == "ego" else FRONTEND_ROLE_MAP.get(vehicle_key, vehicle_key)
    cfg = vehicles.get(role, {})
    lane_changes = list(cfg.get("lane_change_points", []) or [])
    if not lane_changes:
        return []
    base_time_s = float(lane_changes[0].get("time_s", 0.0))
    start_offset_s = float(getattr(n_key, "window_start_offset_s", 1.0) or 1.0)
    end_offset_s = float(getattr(n_key, "window_end_offset_s", start_offset_s + 6.0) or (start_offset_s + 6.0))
    ego_speed_kmh = float(vehicles.get("ego", {}).get("speed_kmh", C.DEFAULT_EGO_SPEED_KMH) or C.DEFAULT_EGO_SPEED_KMH)
    speed_delta_mph = float(getattr(n_key, "ego_speed_delta_mph", 10.0) or 10.0)
    target_speed_kmh = max(0.0, ego_speed_kmh - (speed_delta_mph * MPH_TO_KMH))
    return [
        {
            "time_s": max(0.0, base_time_s + start_offset_s),
            "key": _normalize_prompt_key(getattr(n_key, "key_name", "N")),
            "target_speed_kmh": target_speed_kmh,
            "decel_duration_s": 2.0,
            "timeout_s": max(0.001, end_offset_s - start_offset_s),
            "message": "TAKE OVER REQUEST",
        }
    ]


def _first_float(pattern: Any, text: str, *, default: float) -> float:
    match = pattern.search(text or "")
    if not match:
        return default
    return float(match.group("value"))


def _first_optional_float(pattern: Any, text: str) -> float | None:
    match = pattern.search(text or "")
    if not match:
        return None
    return float(match.group("value"))


def _duration_search_text(prompt_text: str) -> str:
    """Normalize prompt text for duration matching.

    Semicolons separate prompt segments exactly like newlines do in the
    conversion pipeline, so treat them as line breaks here too. This keeps
    raw-text call sites (GUI quick fix, launcher) consistent with
    convert_prompt's normalized text.
    """
    return re.sub(r"[;\r]+", "\n", str(prompt_text or ""))


def _explicit_prompt_scenario_duration(prompt_text: str) -> float | None:
    text = _duration_search_text(prompt_text)
    match = PROMPT_SCENARIO_DURATION_PREFIXED_RE.search(text) or PROMPT_SCENARIO_DURATION_BARE_LINE_RE.search(text)
    if not match:
        return None
    return max(0.001, float(match.group("value")))


def prompt_scenario_duration_is_auto(prompt_text: str) -> bool:
    """True when the prompt requests an open-ended scenario duration.

    ``scenario duration=auto`` (also ``automatic`` / ``until takeover``) keeps
    the scene alive after the scripted total time until the take-over
    interaction resolves, instead of cutting it off at a fixed duration. When
    both an auto and a numeric duration are present, auto wins (numeric
    ``duration=`` also appears in lane-change lines, so it cannot veto the
    auto request).
    """
    return bool(PROMPT_SCENARIO_DURATION_AUTO_RE.search(_duration_search_text(prompt_text)))


def _normalize_prompt_key(value: str) -> str:
    normalized = str(value or "SPACE").strip().upper()
    if normalized in ("SPACE", "空格"):
        return "SPACE"
    if normalized in ("BRAKE", "B"):
        return "B"
    return normalized[:16] or "SPACE"


def _apply_vehicle_speed_overrides(vehicles: dict[str, Any], speed_overrides: dict[str, float]) -> None:
    for role, speed_kmh in speed_overrides.items():
        cfg = vehicles.get(role)
        if not cfg or speed_kmh < 0.0:
            continue
        cfg["speed_kmh"] = float(speed_kmh)


def _apply_vehicle_gap_overrides(vehicles: dict[str, Any], gap_overrides: dict[str, float]) -> None:
    for role, gap_m in gap_overrides.items():
        cfg = vehicles.get(role)
        if not cfg:
            continue
        cfg["initial_gap_m"] = float(gap_m)
        cfg["initial_time_gap_s"] = None


def _apply_vehicle_initial_time_gap_overrides(vehicles: dict[str, Any], time_gap_overrides: dict[str, float]) -> None:
    for role, time_gap_s in time_gap_overrides.items():
        cfg = vehicles.get(role)
        if not cfg:
            continue
        cfg["enabled"] = True
        cfg["initial_time_gap_s"] = float(time_gap_s)
        cfg["initial_gap_m"] = 0.0


def _apply_vehicle_gap_point_overrides(
    vehicles: dict[str, Any],
    gap_point_overrides: dict[str, list[dict[str, Any]]],
) -> None:
    for role, points in gap_point_overrides.items():
        cfg = vehicles.get(role)
        if not cfg or not points:
            continue
        normalized_points = [
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "gap_m": float(point.get("gap_m", 0.0) or 0.0),
            }
            for point in points
        ]
        normalized_points.sort(key=lambda point: point["time_s"])
        cfg["enabled"] = True
        cfg["gap_points"] = normalized_points
        cfg["initial_time_gap_s"] = None
        if normalized_points[0]["time_s"] <= 0.0:
            cfg["initial_gap_m"] = normalized_points[0]["gap_m"]


def _apply_vehicle_time_gap_point_overrides(
    vehicles: dict[str, Any],
    time_gap_point_overrides: dict[str, list[dict[str, Any]]],
    ego_speed_kmh: float,
) -> list[str]:
    warnings: list[str] = []
    ego_speed_mps = max(0.001, float(ego_speed_kmh or 0.0) / 3.6)
    for role, points in time_gap_point_overrides.items():
        cfg = vehicles.get(role)
        if not cfg or not points:
            continue
        initial_time_gap_s = _initial_time_gap_for_cfg(cfg, ego_speed_mps)
        normalized_points = [
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "time_gap_s": float(point.get("time_gap_s", 0.0) or 0.0),
            }
            for point in points
        ]
        normalized_points.sort(key=lambda point: point["time_s"])
        cfg["enabled"] = True
        cfg["time_gap_points"] = normalized_points
        if normalized_points[0]["time_s"] <= 0.0:
            override_time_gap_s = normalized_points[0]["time_gap_s"]
            if abs(override_time_gap_s - initial_time_gap_s) > 0.05:
                warnings.append(
                    "0s time_gap_points for {0} overrides initial headway: "
                    "{1:g}s -> {2:g}s.".format(
                        role,
                        initial_time_gap_s,
                        override_time_gap_s,
                    )
                )
            cfg["initial_time_gap_s"] = normalized_points[0]["time_gap_s"]
            cfg["initial_gap_m"] = 0.0
    return warnings


def _apply_vehicle_speed_point_overrides(
    vehicles: dict[str, Any],
    speed_point_overrides: dict[str, list[dict[str, Any]]],
) -> list[str]:
    warnings: list[str] = []
    for role, points in speed_point_overrides.items():
        cfg = vehicles.get(role)
        if not cfg or not points:
            continue
        initial_speed_kmh = max(0.0, float(cfg.get("speed_kmh", 0.0) or 0.0))
        normalized_points = [
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "speed_kmh": max(0.0, float(point.get("speed_kmh", 0.0) or 0.0)),
            }
            for point in points
        ]
        normalized_points.sort(key=lambda point: point["time_s"])
        cfg["enabled"] = True
        cfg["speed_points"] = normalized_points
        if normalized_points[0]["time_s"] <= 0.0:
            override_speed_kmh = normalized_points[0]["speed_kmh"]
            if abs(override_speed_kmh - initial_speed_kmh) > 0.05:
                warnings.append(
                    "0s speed_points for {0} overrides initial speed: "
                    "{1:g} km/h -> {2:g} km/h.".format(
                        role,
                        initial_speed_kmh,
                        override_speed_kmh,
                    )
                )
            cfg["speed_kmh"] = normalized_points[0]["speed_kmh"]
    return warnings


def _apply_vehicle_blueprint_overrides(vehicles: dict[str, Any], blueprint_overrides: dict[str, str]) -> None:
    for role, blueprint in blueprint_overrides.items():
        cfg = vehicles.get(role)
        if cfg is None:
            cfg = _default_vehicle(role, enabled=False)
            vehicles[role] = cfg
        cfg["blueprint_filter"] = str(blueprint or "").strip().lower()


def _apply_vehicle_lane_overrides(vehicles: dict[str, Any], lane_overrides: dict[str, str]) -> None:
    for role, lane in lane_overrides.items():
        cfg = vehicles.get(role)
        if not cfg:
            continue
        cfg["lane"] = _lane(lane)


def _apply_lane_change_overrides(vehicles: dict[str, Any], lane_change_overrides: dict[str, list[dict[str, Any]]]) -> None:
    for role, points in lane_change_overrides.items():
        cfg = vehicles.get(role)
        if not cfg or not points:
            continue
        normalized_points = [
            {
                "time_s": float(point.get("time_s", 0.0) or 0.0),
                "target_lane": _lane(point.get("target_lane", "Middle")),
                "duration_s": max(0.001, float(point.get("duration_s", C.DEFAULT_LANE_CHANGE_DURATION_S) or C.DEFAULT_LANE_CHANGE_DURATION_S)),
            }
            for point in points
        ]
        normalized_points.sort(key=lambda point: point["time_s"])
        first = normalized_points[0]
        cfg["enabled"] = True
        cfg["lane_change_points"] = normalized_points
        cfg["lane_change_enabled"] = True
        cfg["lane_change_time_s"] = first["time_s"]
        cfg["lane_change_target_lane"] = first["target_lane"]
        cfg["lane_change_duration_s"] = first["duration_s"]


def _normalize_vehicle_speeds(vehicles: dict[str, Any], preserve_roles=()) -> None:
    """Keep migrated SCANeR roles aligned with the CARLA runner.

    In the SCANeR prompt tool, v1/front is the lane-changing support car. CARLA
    keeps that as vehicle_1 while ego is the DReyeVR Tesla. Some imported SCANeR
    variants can leave a support vehicle speed at 0 when the vehicle is meant to
    follow ego timing; in CARLA that makes Traffic Manager start the actor from a
    full stop. Treat non-positive support speeds as "same as ego".
    """
    ego_cfg = vehicles.get("ego") or {}
    ego_speed = float(ego_cfg.get("speed_kmh", C.DEFAULT_EGO_SPEED_KMH))
    if ego_speed <= 0.0 and 'ego' not in preserve_roles:
        ego_speed = C.DEFAULT_EGO_SPEED_KMH
        ego_cfg["speed_kmh"] = ego_speed
    for role in C.SUPPORT_ROLE_ORDER:
        cfg = vehicles.get(role)
        if not cfg or not cfg.get("enabled", False):
            continue
        speed = float(cfg.get("speed_kmh", 0.0) or 0.0)
        if speed <= 0.0 and role not in preserve_roles:
            cfg["speed_kmh"] = ego_speed


def _convert_vehicle_cfg(role: str, cfg: Any, api: dict[str, Any], *, force_enabled: bool = False) -> dict[str, Any]:
    enabled = bool(force_enabled or api["vehicle_enabled_v2"](cfg))
    lane_changes = [
        {
            "time_s": float(event.time_s),
            "target_lane": _lane(event.target_lane),
            "duration_s": max(0.001, float(event.duration_s)),
        }
        for event in api["lane_change_events_for_vehicle_v2"](cfg)
    ]
    gap_points = [
        {"time_s": float(point.time_s), "gap_m": float(point.gap_m)}
        for point in list(getattr(cfg, "gap_points", []) or [])
    ]
    time_gap_points = [
        {"time_s": float(point.time_s), "time_gap_s": float(point.time_gap_s)}
        for point in list(getattr(cfg, "time_gap_points", []) or [])
    ]
    speed_points = [
        {"time_s": float(point.time_s), "speed_kmh": float(point.speed_kmh)}
        for point in list(getattr(cfg, "speed_points", []) or [])
    ]
    first_lc = lane_changes[0] if lane_changes else None
    default = _default_vehicle(role, enabled=enabled)
    default.update(
        {
            "enabled": enabled,
            "lane": _lane(getattr(cfg, "initial_lane", "Middle")),
            "initial_gap_m": float(getattr(cfg, "initial_offset_m", 0.0)),
            "initial_time_gap_s": getattr(cfg, "initial_time_gap_s", None),
            "speed_kmh": float(getattr(cfg, "speed_kmh", C.DEFAULT_EGO_SPEED_KMH)),
            "blueprint_filter": _blueprint_for_scanner_vehicle(role, str(getattr(cfg, "name", ""))),
            "gap_points": gap_points,
            "time_gap_points": time_gap_points,
            "speed_points": speed_points,
            "lane_change_enabled": bool(lane_changes),
            "lane_change_time_s": float(first_lc["time_s"]) if first_lc else float(getattr(cfg, "lane_change_time_s", 8.0)),
            "lane_change_target_lane": str(first_lc["target_lane"]) if first_lc else _lane(getattr(cfg, "lane_change_target_lane", "Middle")),
            "lane_change_duration_s": float(first_lc["duration_s"]) if first_lc else float(getattr(cfg, "lane_change_duration_s", C.DEFAULT_LANE_CHANGE_DURATION_S) or C.DEFAULT_LANE_CHANGE_DURATION_S),
            "lane_change_points": lane_changes,
            "stop_enabled": bool(getattr(cfg, "stop_enabled", False)),
            "stop_time_s": float(getattr(cfg, "stop_time_s", 14.0)),
            "stop_duration_s": C.DEFAULT_STOP_DURATION_S,
        }
    )
    return default


def _default_vehicle(role: str, *, enabled: bool) -> dict[str, Any]:
    return {
        "role": role,
        "enabled": enabled,
        "lane": "Middle",
        "initial_gap_m": 0.0,
        "initial_time_gap_s": None,
        "speed_kmh": C.DEFAULT_EGO_SPEED_KMH,
        "blueprint_filter": C.DEFAULT_BLUEPRINT_FILTERS.get(role, "vehicle.lincoln.mkz_2020"),
        "gap_points": [],
        "time_gap_points": [],
        "speed_points": [],
        "lane_change_enabled": False,
        "lane_change_time_s": 8.0,
        "lane_change_target_lane": "Middle",
        "lane_change_duration_s": C.DEFAULT_LANE_CHANGE_DURATION_S,
        "lane_change_points": [],
        "stop_enabled": False,
        "stop_time_s": 14.0,
        "stop_duration_s": C.DEFAULT_STOP_DURATION_S,
    }


def _convert_pedestrians(params: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role, ped in dict(getattr(params, "pedestrians", {}) or {}).items():
        if not bool(getattr(ped, "enabled", False)):
            continue
        out[str(role)] = {
            "role": str(role),
            "enabled": True,
            "label": str(getattr(ped, "label", role) or role),
            "gap_m": float(getattr(ped, "gap_m", 0.0)),
            "position_reference": "ego_at_crossing",
            "start_time_s": float(getattr(ped, "start_time_s", 0.0)),
            "duration_s": float(getattr(ped, "duration_s", 4.0)),
            "start_side": _side(getattr(ped, "start_side", "Left")),
            "end_side": _side(getattr(ped, "end_side", "Right")),
            "world_locked": True,
            "lock_time_s": float(getattr(ped, "lock_time_s", getattr(ped, "start_time_s", 0.0))),
        }
    return out


def _convert_obstacles(params: Any, api: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for obstacle in api["iter_obstacle_configs_v2"](params):
        role = str(getattr(obstacle, "role", f"obstacle_{len(out) + 1}"))
        if not bool(getattr(obstacle, "enabled", False)):
            continue
        out[role] = {
            "role": role,
            "enabled": True,
            "label": str(getattr(obstacle, "label", role) or role),
            "kind": _obstacle_kind(getattr(obstacle, "kind", "Cone")),
            "blueprint_id": "",
            "spawn_mode": "fixed",
            "lane": _lane(getattr(obstacle, "lane", "Middle")),
            "gap_m": float(getattr(obstacle, "gap_m", 0.0)),
            "lateral_offset_m": float(getattr(obstacle, "lateral_offset_m", 0.0)),
            "start_time_s": float(getattr(obstacle, "start_time_s", 0.0)),
            "end_time_s": float(getattr(obstacle, "end_time_s", getattr(params, "total_experiment_time_s", C.DEFAULT_TOTAL_TIME_S))),
            "world_locked": True,
            "lock_time_s": float(getattr(obstacle, "lock_time_s", getattr(obstacle, "start_time_s", 0.0))),
            "drop_source_role": "",
            "drop_offset_m": DEFAULT_DROP_OFFSET_M,
        }
    return out


def _append_dropped_obstacles(
    obstacles: dict[str, Any],
    dropped_obstacles: list[dict[str, Any]],
    vehicles: dict[str, Any],
    total_time_s: float,
    declared_source_roles: set[str],
) -> None:
    if not dropped_obstacles:
        return
    next_index = 1
    for drop in dropped_obstacles:
        source_role = str(drop.get("source_role") or "")
        source_cfg = vehicles.get(source_role)
        source_declared = source_role == "ego" or source_role in declared_source_roles
        if (
            not source_declared
            or not isinstance(source_cfg, dict)
            or not source_cfg.get("enabled", False)
        ):
            raise ValueError(
                "Dropped obstacle source {0} must be defined and enabled. "
                "Add a line such as 'v1: lane=middle, ahead=3s, speed=30km/h'.".format(
                    source_role or "(missing)"
                )
            )
        start_time_s = float(drop.get("start_time_s", 0.0) or 0.0)
        end_time_value = drop.get("end_time_s")
        end_time_s = float(total_time_s if end_time_value is None else end_time_value)
        if end_time_s <= start_time_s:
            raise ValueError(
                "Dropped obstacle end time ({0:g}s) must be after start time ({1:g}s).".format(
                    end_time_s,
                    start_time_s,
                )
            )
        while "obstacle_{0}".format(next_index) in obstacles:
            next_index += 1
        role = "obstacle_{0}".format(next_index)
        next_index += 1
        kind_text = str(drop.get("kind_text") or "barrel")
        drop_offset_value = drop.get("drop_offset_m")
        drop_offset_m = (
            DEFAULT_DROP_OFFSET_M if drop_offset_value is None else float(drop_offset_value)
        )
        obstacles[role] = {
            "role": role,
            "enabled": True,
            "label": "Dropped {0}".format(_drop_display_kind(kind_text)),
            "kind": _obstacle_kind(kind_text),
            "blueprint_id": _drop_blueprint_id(kind_text),
            "spawn_mode": "drop_from_vehicle",
            "lane": _lane(source_cfg.get("lane", "Middle")),
            "gap_m": 0.0,
            "lateral_offset_m": 0.0,
            "start_time_s": start_time_s,
            "end_time_s": end_time_s,
            "world_locked": True,
            "lock_time_s": start_time_s,
            "drop_source_role": source_role,
            "drop_offset_m": drop_offset_m,
        }


def _apply_current_scanner_hazard_defaults(
    vehicles: dict[str, Any],
    pedestrians: dict[str, Any],
    obstacles: dict[str, Any],
    prompt_text: str,
    *,
    total_time_s: float,
) -> float:
    """Preserve explicit obstacle DSL timing instead of SCANeR defaults."""
    for fragment in re.split(r"[\r\n;]+", prompt_text or ""):
        match = re.match(r"\s*(?:obstacle|obs|o)\s*_?\s*([1-6]?)\s*[:：]", fragment, re.I)
        if not match:
            continue
        cfg = obstacles.get('obstacle_' + (match.group(1) or '1'))
        if not cfg or not cfg.get('enabled') or cfg.get('spawn_mode', 'fixed') != 'fixed':
            continue
        kind_match = re.match(r'\s*(disabled vehicle|traffic cone|barrier|barrel|debris|cone|box|crate)\b',
                              fragment[match.end():], re.I)
        if kind_match:
            kind = kind_match.group(1).lower()
            display = _drop_display_kind(kind)
            cfg['kind'] = 'Debris' if display in ('Box', 'Barrel') else display
            cfg['blueprint_id'] = _drop_blueprint_id(kind)
        start = re.search(r'\bt\s*=\s*([0-9]+(?:\.[0-9]+)?)', fragment, re.I)
        end = re.search(r'\bend\s*=\s*([0-9]+(?:\.[0-9]+)?)', fragment, re.I)
        if start:
            cfg['start_time_s'] = float(start.group(1))
        if end:
            cfg['end_time_s'] = float(end.group(1))
        cfg['lock_time_s'] = 0.0
    return float(total_time_s)


def _drop_display_kind(kind_text: str) -> str:
    normalized = re.sub(r"[\s_-]+", " ", str(kind_text or "barrel").strip().lower())
    if normalized in ("barrel", "debris"):
        return "Barrel"
    if normalized in ("cone", "traffic cone"):
        return "Cone"
    if normalized == "barrier":
        return "Barrier"
    if normalized in ("box", "crate"):
        return "Box"
    if normalized == "disabled vehicle":
        return "Disabled Vehicle"
    return normalized.title() if normalized else "Barrel"


def _drop_blueprint_id(kind_text: str) -> str:
    normalized = re.sub(r"[\s_-]+", " ", str(kind_text or "barrel").strip().lower())
    mapping = {
        "barrel": "static.prop.barrel",
        "debris": "static.prop.barrel",
        "cone": "static.prop.trafficcone01",
        "traffic cone": "static.prop.trafficcone01",
        "barrier": "static.prop.streetbarrier",
        "box": "static.prop.box01",
        "crate": "static.prop.box01",
        "disabled vehicle": "vehicle.citroen.c3",
    }
    return mapping.get(normalized, "static.prop.barrel")


def _conversion_warnings(
    params: Any,
    vehicles: dict[str, Any],
    pedestrians: dict[str, Any],
    obstacles: dict[str, Any],
    audio_cues: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    enabled_support = [role for role, cfg in vehicles.items() if role != "ego" and cfg.get("enabled")]
    if len(enabled_support) > len(C.SUPPORT_ROLE_ORDER):
        warnings.append("Only six support vehicles are supported by the CARLA v1 runner.")
    if pedestrians:
        warnings.append("Pedestrians will be played as kinematic walker actors by carla_nl.run_scenario_dreyevr.")
    merge_cfg = getattr(params, "merge", None)
    if merge_cfg is not None and bool(getattr(merge_cfg, "enabled", False)):
        warnings.append("SCANeR merge metadata was detected; CARLA migration keeps vehicle timing but does not create a merge-ramp map.")
    n_key = getattr(params, "n_key_intervention", None)
    if n_key is not None and bool(getattr(n_key, "enabled", False)):
        warnings.append("SCANeR N-key metadata is ignored by prompt JSON gap-time mode; use explicit TOR prompt lines for takeover requests.")
    return warnings


def _lane(value: Any) -> str:
    text = str(value or "Middle").strip().title()
    if text not in C.LANE_NAMES:
        return "Middle"
    return text


def _side(value: Any) -> str:
    text = str(value or "Left").strip().title()
    return text if text in {"Left", "Right"} else "Left"


def _obstacle_kind(value: Any) -> str:
    text = str(value or "Cone").strip().title()
    aliases = {
        "Disabled": "Disabled Vehicle",
        "Disabledvehicle": "Disabled Vehicle",
        "DisabledVehicle": "Disabled Vehicle",
        "Disabled Vehicle": "Disabled Vehicle",
        "Barrel": "Debris",
    }
    text = aliases.get(text.replace(" ", ""), aliases.get(text, text))
    return text if text in C.OBSTACLE_KINDS else "Cone"


def _blueprint_for_scanner_vehicle(role: str, scanner_name: str) -> str:
    if scanner_name in SCANNER_VEHICLE_BLUEPRINTS:
        return SCANNER_VEHICLE_BLUEPRINTS[scanner_name]
    return C.DEFAULT_BLUEPRINT_FILTERS.get(role, "vehicle.lincoln.mkz_2020")
