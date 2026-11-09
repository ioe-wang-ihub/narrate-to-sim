"""Pydantic v2 models for the v1 CARLA scenario JSON schema.

The schema mirrors the friend's runner exactly so files produced here can be
fed to Start-CARLA-HF-Scenario.ps1 unchanged. `merge_defaults()` takes a sparse
dict (what Claude emits) and returns a complete dict (what the runner expects).
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import constants as C
from .vehicle_motion import motion_config


Lane = Literal["Left", "Middle", "Right"]
Side = Literal["Left", "Right"]
ObstacleKind = Literal["Barrier", "Cone", "Debris", "Disabled Vehicle"]
ObstacleSpawnMode = Literal["fixed", "drop_from_vehicle"]
CameraMode = Literal["off", "driver", "chase", "top", "bird"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GapPoint(_Strict):
    time_s: float
    gap_m: float


class TimeGapPoint(_Strict):
    time_s: float
    time_gap_s: float


class SpeedPoint(_Strict):
    time_s: float
    speed_kmh: float


class LaneChangePoint(_Strict):
    time_s: float
    target_lane: Lane
    duration_s: float = C.DEFAULT_LANE_CHANGE_DURATION_S


class AudioCue(_Strict):
    time_s: float
    file_name: str


class TakeoverRequest(_Strict):
    # Extra keys are allowed here so experiment-specific timing metadata can
    # travel through validation without forcing every legacy JSON to grow.
    model_config = ConfigDict(extra="allow")

    time_s: float
    key: str = "SPACE"
    target_speed_kmh: float = 0.0
    decel_duration_s: float = 2.0
    timeout_s: float = 6.0
    message: str = "TAKE OVER REQUEST"


class ExperimentMarker(_Strict):
    time_s: float
    event: str
    fields: dict[str, Any] = Field(default_factory=dict)


class ExperimentHazard(_Strict):
    name: str = "hazard"
    onset_s: float
    end_s: Optional[float] = None
    fields: dict[str, Any] = Field(default_factory=dict)


class ExperimentTorWindow(_Strict):
    name: str = "tor"
    alert_onset_s: Optional[float] = None
    tor_onset_s: float
    tor_deadline_s: float
    # Trigger mode: "time" fires at tor_onset_s (default, unchanged); "gap"/"ttc"
    # fire when the live gap (m) / time-to-collision (s) to trigger_target_role
    # drops to the threshold, with tor_onset_s acting as a time fallback.
    trigger_mode: Literal["time", "gap", "ttc"] = "time"
    trigger_gap_m: Optional[float] = None
    trigger_ttc_s: Optional[float] = None
    trigger_target_role: str = "lead"
    correct_responses: list[str] = Field(default_factory=list)
    manual_keys: list[str] = Field(default_factory=list)
    success_action: Any = "response_action"
    failure_action: Any = "brake_pull_over"
    notification_text: str = ""
    alert_text: str = ""
    ready_text: str = ""
    success_text: str = ""
    failure_text: str = ""
    return_to_auto_text: str = ""
    collision_fail_text: str = ""
    manual_safe_seconds: float = 4.0
    fields: dict[str, Any] = Field(default_factory=dict)


class ExperimentEvents(_Strict):
    standard_markers_enabled: bool = True
    custom_markers: list[ExperimentMarker] = Field(default_factory=list)
    hazards: list[ExperimentHazard] = Field(default_factory=list)
    tor_windows: list[ExperimentTorWindow] = Field(default_factory=list)
    # Open-ended duration: keep the scene alive after total_time_s until the
    # take-over interaction resolves (AUTO ON resumed), capped by the hold max.
    # After resolution the ego keeps driving in AUTO for the grace window so the
    # scene does not freeze on the participant's resume press.
    hold_until_tor_resolution: bool = False
    tor_hold_max_s: float = 300.0
    tor_hold_resume_grace_s: float = 5.0


class TorAlert(_Strict):
    time_s: float
    role: str = "session"
    aoi_screen: str = "current"
    aoi_grid: str = "middle_left"
    delay_after_fpe_s: Optional[float] = None


class XodrSpec(_Strict):
    road_id: int
    lane_id: int
    s: float


class VehicleCfg(_Strict):
    role: str
    enabled: bool = False
    lane: Lane = "Middle"
    initial_gap_m: float = 0.0
    initial_time_gap_s: Optional[float] = None
    speed_kmh: float = C.DEFAULT_EGO_SPEED_KMH
    blueprint_filter: str = "vehicle.lincoln.mkz_2020"
    gap_points: list[GapPoint] = Field(default_factory=list)
    time_gap_points: list[TimeGapPoint] = Field(default_factory=list)
    speed_points: list[SpeedPoint] = Field(default_factory=list)
    lane_change_enabled: bool = False
    lane_change_time_s: float = 8.0
    lane_change_target_lane: Lane = "Middle"
    lane_change_duration_s: float = C.DEFAULT_LANE_CHANGE_DURATION_S
    lane_change_points: list[LaneChangePoint] = Field(default_factory=list)
    stop_enabled: bool = False
    stop_time_s: float = 14.0
    stop_duration_s: float = C.DEFAULT_STOP_DURATION_S


class PedestrianCfg(_Strict):
    role: str
    enabled: bool = False
    label: str = ""
    gap_m: float = 0.0
    # Old saved scenes use fixed positions measured from the scenario anchor.
    position_reference: Literal['initial_ego', 'ego_at_crossing'] = 'initial_ego'
    start_time_s: float = 0.0
    duration_s: float = 4.0
    start_side: Side = "Left"
    end_side: Side = "Right"
    world_locked: bool = True
    lock_time_s: float = 0.0


class ObstacleCfg(_Strict):
    role: str
    enabled: bool = False
    label: str = ""
    kind: ObstacleKind = "Cone"
    blueprint_id: str = ""
    spawn_mode: ObstacleSpawnMode = "fixed"
    lane: Lane = "Middle"
    gap_m: float = 0.0
    position_reference: Literal['initial_ego', 'vehicle_at_spawn'] = 'initial_ego'
    reference_role: str = ''
    time_gap_s: Optional[float] = None
    lateral_offset_m: float = 0.0
    start_time_s: float = 0.0
    end_time_s: float = 24.0
    world_locked: bool = True
    lock_time_s: float = 0.0
    drop_source_role: str = ""
    drop_offset_m: float = 0.5


_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class CueCollisionTrigger(_Strict):
    kind: Literal['obstacle_ttc'] = 'obstacle_ttc'
    threshold_s: float = Field(gt=0, allow_inf_nan=False)
    target_role: str = Field(pattern=r'^obstacle_[1-9]\d*$')


class StandaloneCue(_Strict):
    kind: Literal['beep', 'speech', 'circle', 'visual']
    time_s: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    duration_s: float = Field(default=4.0, gt=0, allow_inf_nan=False)
    text: str = ''
    target_role: str = ''
    trigger: Optional[CueCollisionTrigger] = None

    @model_validator(mode='after')
    def check_trigger(self):
        if (self.time_s is None) == (self.trigger is None):
            raise ValueError('Specify exactly one cue start: time_s or a collision trigger.')
        return self


class ScenarioV1(_Strict):
    version: Literal[1] = 1
    scenario_name: str
    # Study-level trial-card metadata (paper Step 1): the participant task and the
    # experimental condition this scenario represents. Travel with the scenario so
    # logs/playlists can group by condition. Optional; default empty.
    task: str = ""
    condition: str = ""
    scenario_context: str = ""
    map_name: str = C.DEFAULT_MAP
    weather_preset: str = C.DEFAULT_WEATHER_PRESET
    total_time_s: float = C.DEFAULT_TOTAL_TIME_S
    ego_speed_kmh: float = C.DEFAULT_EGO_SPEED_KMH
    fixed_delta_seconds: float = C.DEFAULT_FIXED_DELTA_SECONDS
    spawn_point_index: Optional[int] = None
    spawn_xodr: Optional[XodrSpec] = None
    rpc_port: Optional[int] = None
    launcher_script: Optional[str] = None
    quality_level: Optional[str] = None
    render_mode: Optional[str] = None
    camera_mode: CameraMode = C.DEFAULT_CAMERA_MODE  # type: ignore[assignment]
    dreyevr: bool = False
    audio_library_root: str = ""
    audio_cues: list[AudioCue] = Field(default_factory=list)
    standalone_cues: list[StandaloneCue] = Field(default_factory=list)
    takeover_requests: list[TakeoverRequest] = Field(default_factory=list)
    tor_alerts: list[TorAlert] = Field(default_factory=list)
    experiment_events: ExperimentEvents = Field(default_factory=ExperimentEvents)
    vehicles: dict[str, VehicleCfg]
    pedestrians: dict[str, PedestrianCfg] = Field(default_factory=dict)
    obstacles: dict[str, ObstacleCfg] = Field(default_factory=dict)

    @field_validator("scenario_name")
    @classmethod
    def _scenario_name_format(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "scenario_name must match [a-z0-9_]+ (lowercase ASCII letters, "
                f"digits, underscore); got: {v!r}"
            )
        return v

    @field_validator("map_name")
    @classmethod
    def _map_known(cls, v: str) -> str:
        if v not in C.KNOWN_MAPS:
            raise ValueError(
                f"Unknown map {v!r}; pick from {list(C.KNOWN_MAPS)}"
            )
        return v

    @field_validator("weather_preset")
    @classmethod
    def _weather_known(cls, v: str) -> str:
        if v not in C.WEATHER_PRESETS:
            raise ValueError(
                f"Unknown weather_preset {v!r}; pick from {list(C.WEATHER_PRESETS)}"
            )
        return v


def _default_vehicle(role: str) -> dict[str, Any]:
    """Return a full default vehicle dict for the given role."""
    blueprint = C.DEFAULT_BLUEPRINT_FILTERS.get(role, "vehicle.lincoln.mkz_2020")
    enabled_default = role == "ego"
    return {
        "role": role,
        "enabled": enabled_default,
        "lane": "Middle",
        "initial_gap_m": 0.0,
        "initial_time_gap_s": None,
        "speed_kmh": C.DEFAULT_EGO_SPEED_KMH,
        "blueprint_filter": blueprint,
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


def merge_defaults(partial: dict[str, Any]) -> dict[str, Any]:
    """Fill in defaults so the result is a complete launcher-ready scenario.

    Claude emits only the fields the user explicitly requested; this fills in
    everything else (all 7 vehicle roles, top-level defaults, etc.). The output
    is a plain dict ready to feed to ScenarioV1.model_validate().
    """
    out: dict[str, Any] = dict(partial)
    out.setdefault("version", 1)
    out.setdefault("task", "")
    out.setdefault("condition", "")
    out.setdefault("scenario_context", "")
    out.setdefault("map_name", C.DEFAULT_MAP)
    out.setdefault("weather_preset", C.DEFAULT_WEATHER_PRESET)
    out.setdefault("total_time_s", C.DEFAULT_TOTAL_TIME_S)
    out.setdefault("ego_speed_kmh", C.DEFAULT_EGO_SPEED_KMH)
    out.setdefault("fixed_delta_seconds", C.DEFAULT_FIXED_DELTA_SECONDS)
    out.setdefault("spawn_point_index", None)
    out.setdefault("camera_mode", C.DEFAULT_CAMERA_MODE)
    out.setdefault("audio_library_root", "")
    out.setdefault("audio_cues", [])
    out.setdefault("takeover_requests", [])
    out.setdefault("tor_alerts", [])
    out.setdefault("experiment_events", {})

    # Town13 has a known-good spawn — fill it in if user picked Town13 and
    # didn't specify any spawn info.
    if (
        out["map_name"] == "Town13"
        and out.get("spawn_point_index") is None
        and out.get("spawn_xodr") is None
    ):
        out["spawn_xodr"] = dict(C.TOWN13_DEFAULT_SPAWN_XODR)

    # Vehicles: ensure all 7 canonical roles exist, with caller's overrides applied.
    user_vehicles: dict[str, Any] = dict(out.get("vehicles", {}))
    merged_vehicles: dict[str, Any] = {}
    for role in C.ROLE_ORDER:
        base = _default_vehicle(role)
        override = user_vehicles.get(role, {})
        if isinstance(override, dict):
            # Carry role field even if user omitted it
            override = {**override, "role": role}
            base.update(override)
        merged_vehicles[role] = motion_config(base)
    # If user added a role outside ROLE_ORDER, keep it as-is (will fail validation
    # later with a clearer message than us silently dropping it).
    for role, cfg in user_vehicles.items():
        if role not in C.ROLE_ORDER:
            merged_vehicles[role] = cfg
    out["vehicles"] = merged_vehicles

    # Pedestrians and obstacles: do NOT auto-populate — their presence
    # determines which runner the launcher uses (vehicle-only vs actor-aware).
    # Just ensure each user-provided entry has a `role` field set.
    out["pedestrians"] = _normalize_role_dict(out.get("pedestrians", {}))
    out["obstacles"] = _normalize_role_dict(out.get("obstacles", {}))

    return out


def _normalize_role_dict(d: Any) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    out = {}
    for key, value in d.items():
        if isinstance(value, dict):
            value = {**value, "role": value.get("role", key)}
        out[key] = value
    return out


def validate(partial: dict[str, Any]) -> tuple[bool, Any]:
    """Merge defaults and validate. Return (ok, normalized_or_errors).

    On success: (True, dict) — the dict is the model_dump() output, ready to save.
    On failure: (False, list[dict]) — each dict has {path, message}.
    """
    try:
        merged = merge_defaults(partial)
    except Exception as exc:  # defensive — merge_defaults shouldn't really raise
        return False, [{"path": "(root)", "message": f"merge_defaults failed: {exc}"}]
    try:
        model = ScenarioV1.model_validate(merged)
    except Exception as exc:
        errors = _extract_errors(exc)
        return False, errors
    return True, model.model_dump(mode="json")


def _extract_errors(exc: Exception) -> list[dict[str, str]]:
    """Pull structured errors out of a pydantic ValidationError, else fall back."""
    errs = getattr(exc, "errors", None)
    if callable(errs):
        out = []
        for e in errs():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            out.append({"path": loc or "(root)", "message": str(e.get("msg", ""))})
        return out or [{"path": "(root)", "message": str(exc)}]
    return [{"path": "(root)", "message": str(exc)}]
