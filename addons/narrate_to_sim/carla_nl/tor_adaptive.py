"""Helpers for configurable TOR adaptive-stage prompts.

The prompt is intentionally small and line-oriented so it can be edited inside
the launcher without requiring a separate schema editor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


RETURN_TO_AUTO_TEXT = "Please return to automated driving."


def normalize_return_to_auto_text(value: Any) -> str:
    text = str(value or "").strip()
    lower = text.lower()
    if not text or (lower.startswith("please press ") and
                    "to return to autonomous driving" in lower):
        return RETURN_TO_AUTO_TEXT
    return text


STAGE1_REQUIREMENTS = (
    "gaze_screen",
    "motion",
    "motion_and_gaze",
    "motion_or_gaze",
    "delay",
    "none",
)
STAGE2_REQUIREMENTS = (
    "target_fixation",
    "target_hit",
    "gaze_screen",
    "delay",
    "none",
)
MANUAL_REQUIREMENTS = (
    "none",
    "stage2",
    "ready",
    "aoi",
    "fixation",
)

ADAPTIVE_FIELD_KEYS = (
    "adaptive_prompt",
    "stage1_requirement",
    "stage1_delay_s",
    "stage2_requirement",
    "stage2_delay_s",
    "target_fixation_s",
    "manual_requires",
    "stage2_direction",
    "stage2_speech_template",
    "stage2_text",
    "stage_speech_min_gap_s",
)

TOR_ADAPTIVE_PROMPT_EXAMPLE = """stage1 -> stage2: require=gaze_screen
stage2 -> ready: require=target_fixation, fixation=0.5s
stage2: direction=front, speech=Look {direction}., min_gap=0.5s
manual: require=none"""


def _clean_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_stage1_requirement(value: Any, default: str = "gaze_screen") -> str:
    key = _clean_token(value)
    aliases = {
        "": default,
        "gaze": "gaze_screen",
        "screen": "gaze_screen",
        "gaze_in_screen": "gaze_screen",
        "look_screen": "gaze_screen",
        "motion": "motion",
        "movement": "motion",
        "hand": "motion",
        "hand_motion": "motion",
        "motion_gaze": "motion_and_gaze",
        "motion_and_gaze": "motion_and_gaze",
        "movement_and_gaze": "motion_and_gaze",
        "motion+gaze": "motion_and_gaze",
        "motion_or_gaze": "motion_or_gaze",
        "movement_or_gaze": "motion_or_gaze",
        "delay": "delay",
        "timer": "delay",
        "time": "delay",
        "none": "none",
        "immediate": "none",
        "off": "none",
    }
    normalized = aliases.get(key, key)
    return normalized if normalized in STAGE1_REQUIREMENTS else default


def normalize_stage2_requirement(value: Any, default: str = "target_fixation") -> str:
    key = _clean_token(value)
    aliases = {
        "": default,
        "fixation": "target_fixation",
        "target_fixation": "target_fixation",
        "gaze_target": "target_fixation",
        "gaze_in_target": "target_fixation",
        "target_hit": "target_hit",
        "target": "target_hit",
        "hit": "target_hit",
        "screen": "gaze_screen",
        "gaze_screen": "gaze_screen",
        "gaze_in_screen": "gaze_screen",
        "delay": "delay",
        "timer": "delay",
        "time": "delay",
        "none": "none",
        "immediate": "none",
        "off": "none",
    }
    normalized = aliases.get(key, key)
    return normalized if normalized in STAGE2_REQUIREMENTS else default


def normalize_manual_requirement(value: Any, default: str = "none") -> str:
    key = _clean_token(value)
    aliases = {
        "": default,
        "none": "none",
        "immediate": "none",
        "off": "none",
        "stage2": "stage2",
        "stage_2": "stage2",
        "stage2_onset": "stage2",
        "ready": "ready",
        "tor_ready": "ready",
        "aoi": "aoi",
        "aoi_confirmed": "aoi",
        "fixation": "fixation",
        "target_fixation": "fixation",
        "target_fixation_confirmed": "fixation",
    }
    normalized = aliases.get(key, key)
    return normalized if normalized in MANUAL_REQUIREMENTS else default


def _parse_seconds(value: Any, default: float = 0.0) -> float:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return float(default)
    multiplier = 1.0
    if text.endswith("ms"):
        multiplier = 0.001
        text = text[:-2]
    elif text.endswith("sec"):
        text = text[:-3]
    elif text.endswith("secs"):
        text = text[:-4]
    elif text.endswith("s"):
        text = text[:-1]
    try:
        return max(0.0, float(text.strip()) * multiplier)
    except Exception:
        return float(default)


def _split_assignments(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    normalized = str(text or "").replace(";", ",")
    for segment in normalized.split(","):
        part = segment.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = _clean_token(key)
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def _apply_stage2_visual_field(fields: Dict[str, Any], key: str, value: Any) -> None:
    if key in ("direction", "look", "stage2_direction"):
        fields["stage2_direction"] = str(value or "").strip() or "front"
    elif key in ("speech", "speech_template", "stage2_speech", "stage2_speech_template"):
        fields["stage2_speech_template"] = str(value or "").strip() or "Look {direction}."
    elif key in ("text", "stage2_text"):
        fields["stage2_text"] = str(value or "").strip()
    elif key in ("min_gap", "speech_gap", "stage_speech_min_gap_s"):
        fields["stage_speech_min_gap_s"] = _parse_seconds(value, 0.5)


def parse_tor_adaptive_prompt(text: Any) -> Dict[str, Any]:
    """Parse a compact TOR-adaptive prompt into tor_window fields."""

    prompt = str(text or "").strip()
    fields: Dict[str, Any] = {}
    if not prompt:
        return fields
    fields["adaptive_prompt"] = prompt
    for raw_line in prompt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        left = ""
        body = line
        if ":" in line:
            left, body = line.split(":", 1)
            left = _clean_token(left)
        assignments = _split_assignments(body)
        if not assignments and "=" in line and ":" not in line:
            assignments = _split_assignments(line)
        if not assignments:
            continue

        if "stage1" in left and "stage2" in left:
            req = assignments.get("require") or assignments.get("requires") or assignments.get("condition")
            if req is not None:
                fields["stage1_requirement"] = normalize_stage1_requirement(req)
            delay = assignments.get("delay") or assignments.get("after") or assignments.get("stage1_delay")
            if delay is not None:
                fields["stage1_delay_s"] = _parse_seconds(delay, 0.0)
            continue
        if "stage2" in left and ("ready" in left or "stage3" in left):
            req = assignments.get("require") or assignments.get("requires") or assignments.get("condition")
            if req is not None:
                fields["stage2_requirement"] = normalize_stage2_requirement(req)
            fixation = (
                assignments.get("fixation")
                or assignments.get("target_fixation")
                or assignments.get("target_fixation_s")
                or assignments.get("required_fixation_s")
            )
            if fixation is not None:
                fields["target_fixation_s"] = _parse_seconds(fixation, 0.5)
            delay = assignments.get("delay") or assignments.get("after") or assignments.get("stage2_delay")
            if delay is not None:
                fields["stage2_delay_s"] = _parse_seconds(delay, 0.0)
            continue
        if left == "stage2":
            for key, value in assignments.items():
                _apply_stage2_visual_field(fields, key, value)
            continue
        if left == "manual":
            req = assignments.get("require") or assignments.get("requires") or assignments.get("manual_requires")
            if req is not None:
                fields["manual_requires"] = normalize_manual_requirement(req)
            continue

        for key, value in assignments.items():
            if key in ("stage1", "stage1_requirement", "stage1_require", "stage1_requires"):
                fields["stage1_requirement"] = normalize_stage1_requirement(value)
            elif key in ("stage1_delay", "stage1_delay_s"):
                fields["stage1_delay_s"] = _parse_seconds(value, 0.0)
            elif key in ("stage2", "stage2_requirement", "stage2_require", "stage2_requires"):
                fields["stage2_requirement"] = normalize_stage2_requirement(value)
            elif key in ("stage2_delay", "stage2_delay_s"):
                fields["stage2_delay_s"] = _parse_seconds(value, 0.0)
            elif key in ("fixation", "fixation_s", "target_fixation", "target_fixation_s", "required_fixation_s"):
                fields["target_fixation_s"] = _parse_seconds(value, 0.5)
            elif key in ("manual", "manual_require", "manual_requires", "manual_requirement"):
                fields["manual_requires"] = normalize_manual_requirement(value)
            else:
                _apply_stage2_visual_field(fields, key, value)
    return normalize_tor_adaptive_fields(fields)


def normalize_tor_adaptive_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(fields or {})
    if "stage1_requirement" in normalized:
        normalized["stage1_requirement"] = normalize_stage1_requirement(normalized.get("stage1_requirement"))
    if "stage2_requirement" in normalized:
        normalized["stage2_requirement"] = normalize_stage2_requirement(normalized.get("stage2_requirement"))
    if "manual_requires" in normalized:
        normalized["manual_requires"] = normalize_manual_requirement(normalized.get("manual_requires"))
    for key, default in (
        ("stage1_delay_s", 0.0),
        ("stage2_delay_s", 0.0),
        ("target_fixation_s", 0.5),
        ("stage_speech_min_gap_s", 0.5),
    ):
        if key in normalized:
            normalized[key] = _parse_seconds(normalized.get(key), default)
    return normalized


def format_tor_adaptive_prompt(fields: Optional[Dict[str, Any]]) -> str:
    values = normalize_tor_adaptive_fields(dict(fields or {}))
    raw = str(values.get("adaptive_prompt") or "").strip()
    if raw:
        return raw
    stage1 = values.get("stage1_requirement", "gaze_screen")
    stage2 = values.get("stage2_requirement", "target_fixation")
    fixation = values.get("target_fixation_s", 0.5)
    direction = values.get("stage2_direction", "front")
    speech = values.get("stage2_speech_template", "Look {direction}.")
    min_gap = values.get("stage_speech_min_gap_s", 0.5)
    manual = values.get("manual_requires", "none")
    return (
        "stage1 -> stage2: require={0}\n"
        "stage2 -> ready: require={1}, fixation={2:g}s\n"
        "stage2: direction={3}, speech={4}, min_gap={5:g}s\n"
        "manual: require={6}"
    ).format(stage1, stage2, float(fixation), direction, speech, float(min_gap), manual)


def adaptive_fields_from_mapping(mapping: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = dict(mapping or {})
    fields: Dict[str, Any] = {}
    for key in ADAPTIVE_FIELD_KEYS:
        if key != "adaptive_prompt" and key in source and source.get(key) not in (None, ""):
            fields[key] = source.get(key)
    prompt = source.get("adaptive_prompt")
    if prompt:
        fields.update(parse_tor_adaptive_prompt(prompt))
    return normalize_tor_adaptive_fields(fields)
