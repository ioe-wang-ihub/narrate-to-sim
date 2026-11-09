"""Map scene elements back to the prompt segment that produced them.

Foundation for paper Step 3 ("each timeline mark links back to the prompt
segment ... click a mark to edit the action prompt segment"). Narrate-to-Sim
prompts are line-oriented (one line per element: ego, v1, ped1, obstacle, TOR,
marker, ...), so provenance is per line: this indexes each prompt line with its
character span and a classified kind, and resolves the source line for a given
scene element (role/kind). Pure standard library; unit-testable offline.

A timeline UI can use ``source_line_for`` to highlight/edit the originating
prompt text for a clicked mark, then re-run the existing prompt conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
import unicodedata

from .prompt_scene_entities import PEDESTRIAN_NAME_RE, OBSTACLE_NAME_RE

# (kind, pattern) in priority order -- first match wins.
_KIND_PATTERNS = [
    ("title", re.compile(r"^\s*title\s*[:\-]", re.I)),
    ("duration", re.compile(r"^\s*(?:scenario\s+)?duration\b", re.I)),
    ("pedestrian", PEDESTRIAN_NAME_RE),
    ("obstacle", OBSTACLE_NAME_RE),
    ("obstacle", re.compile(r"\bobstacle\b|\bbarrier\b|\bcone\b|\bdebris\b|\bdrops?\b|\bdisabled vehicle\b", re.I)),
    ("tor", re.compile(r"\btor\b|take[\s-]*over|takeover|\balert\b|\bdeadline\b|\bhazard\b", re.I)),
    ("marker", re.compile(r"\bmarker[s]?\b", re.I)),
    ("lane_change", re.compile(r"->|→|\blane[\s_-]*change\b|\bcut[\s-]*in\b|\bmerge[s]?\b", re.I)),
    ("time_gap", re.compile(r"\btime[\s_-]*gap\b|\bheadway\b", re.I)),
    ("ego", re.compile(r"^\s*ego\b", re.I)),
    ("vehicle", re.compile(r"^\s*v\d+\b|\bvehicle[_\s]?\d+\b", re.I)),
]

_ROLE_RE = re.compile(r"\b(ego|v\d+|vehicle[_\s]?\d+|ped\d*|pedestrian\d*|obstacle\d*)\b", re.I)


@dataclass
class LineSpan:
    line_no: int          # 0-based line index
    text: str             # the line content (stripped)
    char_start: int       # offset of the line in the original prompt
    char_end: int         # offset just past the line content
    kind: str             # classified element kind
    roles: list[str]      # role tokens mentioned (e.g. ["v1"])


def _semantic_text(text):
    text = unicodedata.normalize('NFKC', text).replace('\\_', '_')
    return re.sub(r'"(?:\\.|[^"\\])*"', lambda m: ' ' * len(m[0]), text)


def classify_line(text: str) -> str:
    text = _semantic_text(text)
    from .cue_language import cue_head
    if cue_head(text):
        return 'tor'
    if re.match(r'^\s*(?:(?:at|after)\s+\d+(?:\.\d+)?\s*(?:seconds?|secs?|s)\s*[:,]?\s*)?'
                r'(?:(?:speech(?:[ -]based)?|auditory|audio|visual|beep)\s+)*'
                r'(?:cue\b|take[\s-]*over\b|takeover\b|tor\b|warning\b|alert\b)', text, re.I):
        return 'tor'  # An alert target is a reference, not an object definition.
    for kind, pat in _KIND_PATTERNS:
        if pat.search(text):
            return kind
    return "unknown"


def _roles_in(text: str) -> list[str]:
    text = _semantic_text(text)
    return ([m.group(1).lower().replace(" ", "_") for m in _ROLE_RE.finditer(text)]
            + [m[0] for m in PEDESTRIAN_NAME_RE.finditer(text)]
            + [m[0] for m in OBSTACLE_NAME_RE.finditer(text)])


def index_prompt_lines(prompt: str) -> list[LineSpan]:
    """Return a :class:`LineSpan` for every non-empty line of ``prompt``."""
    spans: list[LineSpan] = []
    offset = 0
    for line_no, raw in enumerate((prompt or "").split("\n")):
        stripped = raw.strip()
        if stripped:
            start = offset + (len(raw) - len(raw.lstrip()))
            end = start + len(stripped)
            spans.append(
                LineSpan(
                    line_no=line_no,
                    text=stripped,
                    char_start=start,
                    char_end=end,
                    kind=classify_line(stripped),
                    roles=_roles_in(stripped),
                )
            )
        offset += len(raw) + 1  # +1 for the newline
    # Match the reader's allocation: reserve explicit IDs, then assign unnamed
    # pedestrians in source order. Keep the original spans unchanged.
    used = {int(m[1]) for span in spans for m in PEDESTRIAN_NAME_RE.finditer(_semantic_text(span.text)) if m[1]}
    for span in spans:
        if span.kind != 'pedestrian':
            continue
        for match in PEDESTRIAN_NAME_RE.finditer(_semantic_text(span.text)):
            if not match[1]:
                number = next(i for i in range(1, len(used) + 2) if i not in used)
                used.add(number)
                span.roles.append('pedestrian_' + str(number))
    used_obstacles = {int(m[1]) for span in spans
                      if span.kind == 'obstacle'
                      for m in OBSTACLE_NAME_RE.finditer(_semantic_text(span.text)) if m[1]}
    for span in spans:
        if span.kind != 'obstacle':
            continue
        names = list(OBSTACLE_NAME_RE.finditer(_semantic_text(span.text)))
        if not names or any(not match[1] for match in names):
            number = next(i for i in range(1, len(used_obstacles) + 2) if i not in used_obstacles)
            used_obstacles.add(number)
            span.roles.append('obstacle_' + str(number))
    return spans


def _norm_role(role: Optional[str]) -> str:
    if not role:
        return ""
    r = str(role).lower().strip()
    pedestrian = PEDESTRIAN_NAME_RE.fullmatch(r)
    if pedestrian and pedestrian[1]:
        return 'pedestrian_' + pedestrian[1]
    obstacle = OBSTACLE_NAME_RE.fullmatch(r)
    if obstacle and obstacle[1]:
        return 'obstacle_' + obstacle[1]
    # vehicle_1 -> v1 alias so "v1" lines match "vehicle_1" scene roles
    m = re.match(r"vehicle[_\s]?(\d+)$", r)
    if m:
        return "v" + m.group(1)
    return r


def source_line_for(prompt: str, role: Optional[str] = None, kind: Optional[str] = None) -> Optional[LineSpan]:
    """Best source line for a scene element identified by role and/or kind.

    Role match wins (e.g. an event on ``vehicle_1`` -> the ``v1`` line); a kind
    match (e.g. ``tor``/``pedestrian``) is the fallback. Returns None if neither
    resolves.
    """
    spans = index_prompt_lines(prompt)
    want_role = _norm_role(role)
    if want_role:
        # exact role token, accounting for v1/vehicle_1 aliasing
        for span in spans:
            if want_role in [_norm_role(r) for r in span.roles]:
                if span.kind == 'tor' and kind in ('vehicle', 'ego', 'obstacle', 'pedestrian'):
                    continue
                if kind is None or span.kind == kind or kind == "vehicle":
                    return span
        if kind == 'obstacle' and want_role.startswith('obstacle_'):
            return None  # Do not navigate to a cue that only references this object.
        for span in spans:  # role present, ignore kind
            if want_role in [_norm_role(r) for r in span.roles]:
                if span.kind == 'tor' and kind in ('vehicle', 'ego', 'obstacle', 'pedestrian'):
                    continue
                return span
    if kind:
        if want_role.startswith(('pedestrian_', 'obstacle_')):
            return None  # Never redirect a missing numbered object to a different one.
        for span in spans:
            if span.kind == kind:
                return span
    return None
