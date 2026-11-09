"""Conservative prose reader for the traffic intents supported by CARLA v1.

This is a bounded grammar, not an LLM. Resolve the subject of each sentence
before reading its values. Reject unhandled sentences instead of completing
them with an unrelated demonstration scenario. The wizard DSL stays separate.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any

from .prompt_language import SCENARIO_DURATION_HEADER, conversational_lane_actions, normalize_prompt_characters, normalize_vehicle_aliases, normalize_vehicle_language
from .prompt_english import RATE, SPEED_ACTION, CHECKED_SPEED_ACTION, STATE_COPULA_INTENT, VEHICLE_STATE_INTENT
from .prompt_speed_events import apply_speed_event, speed_event_duration, validate_stationary_hold
from .prompt_scene_entities import ENTITY, entity_requires_checked_reader, entity_missing_information, entity_explicit_end, parse_scene_entity
from .prompt_positions import extract_position, extract_ego_vehicle_relation, missing_position_field, partial_position_guidance, position_requires_checked_reader, resolve_positions
from .prompt_feedback import PromptInputError, STATE_INTERVAL_HINT, missing_information


NUMBER = r"\d+(?:\.\d+)?"
SECONDS = r"(?:seconds?|secs?|s|秒)"
VALUE_SECONDS = rf"({NUMBER})\s*-?\s*{SECONDS}\b"
SPEED = re.compile(rf"({NUMBER})\s*(km/h|kmh|kph|mph|m/s|metres per second|meters per second|公里每小时|千米每小时)", re.I)
LANE = re.compile(r"\b(left|middle|right)\s+lane\b|左(?:侧)?车道|中间车道|中车道|右(?:侧)?车道", re.I)
ABSOLUTE_TIME = re.compile(rf"\b(?:at|after)\s+{VALUE_SECONDS}|第\s*({NUMBER})\s*秒", re.I)
RELATIVE_TIME = re.compile(rf"({NUMBER}|one|two|three|four|five|six|seven|eight|nine|ten)\s*{SECONDS}\s+after\s+(?:completing|finishing|starting)(?:\s+that|\s+the)?\s+(?:lane change|previous (?:(?:stationary|clause) )?action|braking|acceleration|maneuver)", re.I)
EVENT_DURATION = re.compile(rf"(?:over|within|taking|takes|in)\s+{VALUE_SECONDS}|({NUMBER})-second\s+lane change|(?:用时|持续)\s*({NUMBER})\s*秒", re.I)
END_TIME = re.compile(rf"(?:and\s+)?(?:finishes?|ends?|completes?|completing)(?:\s+the\s+lane\s+change)?\s+at\s+{VALUE_SECONDS}", re.I)
STATE_INTERVAL = re.compile(rf"\b(?:for|over|within|until|lasting|taking)\s+{VALUE_SECONDS}", re.I)
VEHICLE_EVENT = re.compile(r"\b(?:chang(?:e[sd]?|ing)|mov(?:es?|ing)|merg(?:es?|ing)|cuts?|switch(?:es)?|go(?:es)?)\b.*\b(?:lane|into)\b|\bturns?\s+(?:left|right)\b|lane change|变道|切入|slowing down|slows down|decelerate|accelerate|adjusts speed|减速", re.I)
STATE_ROLE = re.compile(r"^(?:the\s+)?(ego|vehicle_[1-6])\b|^(?:a|one|the)\s+(?:lead\s+)?car\b", re.I)
DURATION = re.compile(
    rf"({NUMBER})\s*-\s*seconds?\b(?=\s+(?:Town\d+(?:_Opt)?\s+)?scenario\b)|(?:for|lasting(?:\s+exactly)?|(?:total\s+)?scenario\s+duration(?:\s+exactly)?)\s+({NUMBER})\s*{SECONDS}\b|({NUMBER})\s*秒场景", re.I)


def is_prose_prompt(text: str) -> bool:
    text = normalize_prompt_characters(text)
    # Literal speech text is payload, not a driving instruction.
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    # The legacy scanner extracts matching fields rather than validating all
    # prose. Never let it discard a negation, uncertainty or condition.
    if re.search(r"\b(?:not|never|cannot|can't|won't|don't|doesn't|didn't|unless|if|when|without|might|may|maybe|possibly|probably|could|would|provided)\b", text, re.I):
        return True
    if position_requires_checked_reader(text):
        return True
    if entity_requires_checked_reader(text):
        return True
    if STATE_COPULA_INTENT.search(text) or VEHICLE_STATE_INTENT.search(text):
        return True
    if SPEED_ACTION.search(text) and re.search(r"\b(?:ego|v[1-6]|vehicle_[1-6]|vehicle|car|npc|I)\b", text, re.I):
        return True
    # A prose header takes precedence even if a graphical editor inserted a
    # DSL line. Mixed text must fail explicitly instead of falling back.
    if re.search(r"(?:^|[\n;])\s*(?:create|make|use)\b.*\b(?:scenario|Town\d|seconds)\b|创建.*场景", text, re.I):
        return True
    if re.search(r"\b(?:ego|v[1-6]|vehicle[ _#-]*[1-6])\s+(?:(?:is|starts|drives|travels)\s+)?(?:(?:at|in|on)\s+(?:the\s+)?)?(?:left|middle|right|center|centre)\b", text, re.I):
        return True
    # Keep the established extension DSL, but send natural-language events
    # and sentence-level durations through the checked reader.
    if re.search(r"(?:^|[\n;])\s*(?:scenario\s+duration\s*=|(?:ego|v[1-6]|vehicle_[1-6])\s*:.*\blane\s*=|t\s*=)", text, re.I):
        # The legacy scanner does not read these prose duration markers.
        # Route before it can replace an explicitly stated duration with 3s.
        event_body = SCENARIO_DURATION_HEADER.sub(" ", text)
        if re.search(r"\b(?:duration\s+of|with\s+(?:a\s+)?duration)\b", event_body, re.I):
            return True
        # Retain existing DSL defaults only for actions its parser actually
        # recognizes. Newly normalized verbs must not disappear on that route.
        from .scanner_prompt_adapter import PROMPT_LANE_CHANGE_RE, PROMPT_LANE_CHANGE_TRAILING_TIME_RE, PROMPT_PROSE_LANE_CHANGE_RE
        legacy_actions = [m for pattern in (PROMPT_LANE_CHANGE_RE, PROMPT_LANE_CHANGE_TRAILING_TIME_RE, PROMPT_PROSE_LANE_CHANGE_RE) for m in pattern.finditer(text)]
        actions = conversational_lane_actions(text)
        # The SCANeR intent gate rejects 'switch lanes' even when the adapter
        # extracts it; conversational duration words also need the checked path.
        if actions and re.search(r"\bswitch(?:es|ing)?\s+lanes?\b|\b(?:for|lasting)\s+\d+(?:\.\d+)?\s*(?:seconds?|secs?|s)\b", text, re.I):
            return True
        if any(not any(old.start() <= action.start() and old.end() >= action.end() for old in legacy_actions) for action in actions):
            return True
        return bool(re.search(r"maneuver|manoeuvre|begins?\s+moving|ego vehicle's lane|\b(?:when|if)\b.*(?:TTC|lane|light)", text, re.I))
    return bool(re.search(r"\bego\b|\bI'm\b|\bI am driving\b|\bmy (?:vehicle|car)\b|自车|车辆\d|车道", text, re.I))


def _error(sentence: str, detail: str = "Unsupported or ambiguous request", **context) -> ValueError:
    event_start = context.pop('event_start', None)
    if 'example' not in context and VEHICLE_EVENT.search(sentence):
        body = ABSOLUTE_TIME.sub(' ', RELATIVE_TIME.sub(' ', END_TIME.sub(' ', sentence))).lstrip(' ,')
        role = re.match(r'(?:the\s+)?(ego|vehicle_[1-6]|it)\b', body, re.I)
        if role:
            example, recognized = _event_guidance(sentence, 'anonymous' if role[1].lower() == 'it' else role[1].lower())
            from .prompt_event_repairs import editable_event_example
            edited = editable_event_example(example, detail, role[1].lower(), event_start=event_start)
            if edited != example:
                recognized = []  # Invalid values are not "kept" by this edit.
            example = edited
            context.update(example=example, recognized=recognized)
    return PromptInputError(f"{detail}: {sentence}", clause=sentence, reason=detail, **context)


def _speed(sentence: str) -> float:
    match = SPEED.search(sentence)
    if not match:
        raise _error(sentence, "Missing speed with units")
    value = float(match[1])
    unit = match[2].lower()
    return value * (1.609344 if unit == "mph" else 3.6 if unit in {"m/s", "metres per second", "meters per second"} else 1.0)


def _lane(sentence: str, ego_lane: str | None = None) -> str:
    match = LANE.search(sentence)
    if match:
        value = match[0].lower()
        return "Left" if value.startswith(("left", "左")) else "Right" if value.startswith(("right", "右")) else "Middle"
    if ego_lane and re.search(r"same\s+lane|ego\s+lane|同.*车道", sentence, re.I):
        return ego_lane
    raise _error(sentence, "Missing initial lane")


def _gap(sentence: str) -> float:
    match = re.search(rf"({NUMBER})\s*(?:metres?|meters?|m)\s+(ahead|in front|behind)\b", sentence, re.I)
    if match:
        return float(match[1]) * (-1 if match[2].lower() == "behind" else 1)
    match = re.search(rf"(前方|后方)\s*({NUMBER})\s*米", sentence)
    if match:
        return float(match[2]) * (-1 if match[1] == "后方" else 1)
    raise _error(sentence, "Unsupported initial state: missing initial distance in metres")


def _check_state_words(sentence: str) -> None:
    original = sentence
    sentence, position = extract_position(sentence)
    if position is not None and re.match(r'(?:the\s+)?ego\b', sentence, re.I):
        raise _error(sentence, "Ego's initial road position is selected in the map settings; do not offset ego relative to itself")
    if len(SPEED.findall(sentence)) != 1 or len(LANE.findall(sentence)) > 1:
        raise _error(sentence, "Specify one speed and one initial lane per vehicle")
    gaps = re.findall(rf"{NUMBER}\s*(?:metres?|meters?|m)\s+(?:ahead|in front|behind)\b|(?:前方|后方)\s*{NUMBER}\s*米", sentence, re.I)
    if len(gaps) > 1:
        raise _error(sentence, "Specify one initial distance per vehicle")
    if re.match(r"(?:the\s+)?ego\b|自车", sentence, re.I) and re.search(r"\bcar\b|前车|后车", sentence, re.I):
        raise _error(sentence, "Describe each vehicle in a separate sentence")
    reference = re.search(r"(?:ahead|in front|behind)\s+of\s+(?:the\s+)?(\w+)", sentence, re.I)
    if reference and reference[1].lower() != "ego":
        raise _error(sentence, "Initial distance must be relative to ego")
    reference = re.search(r"(?:ahead|in front|behind)\s+(?:of\s+)?(?:the\s+)?(ego|vehicle_[1-6]|(?:lead\s+)?car)\b", sentence, re.I)
    if reference and reference[1].lower() != "ego":
        raise _error(sentence, "Initial distance must be relative to ego")
    rest = _state_unrecognized(original)
    if rest.strip(" ,，、:"):
        role = (STATE_ROLE.match(original)[1] or 'vehicle_1').lower()
        example, recognized = _state_guidance(original, role)
        if STATE_INTERVAL.search(sentence):
            raise _error(original, 'A state interval does not specify the following action / 狀態時間尚未指定後續動作',
                         code='state_interval', unrecognized=rest.strip(' ,，、:'), example=example, recognized=recognized)
        raise _error(original, code='state', unrecognized=rest.strip(' ,，、:'), example=example, recognized=recognized)


def _event_duration(sentence: str, start: float) -> float:
    matches = list(EVENT_DURATION.finditer(sentence))
    ends = list(END_TIME.finditer(sentence))
    if len(ends) > 1 or len(matches) > 1:
        raise _error(sentence, "Conflicting event durations or completion times")
    if not matches and not ends:
        raise _error(sentence, "Missing event duration")
    value = float(next(g for g in matches[0].groups() if g is not None)) if matches else float(ends[0][1]) - start
    if ends and abs(start + value - float(ends[0][1])) > 1e-6:
        raise _error(sentence, "Conflicting event duration and completion time")
    if value <= 0:
        raise _error(sentence, "Event duration must be positive")
    return value


def _event_time(sentence: str, previous_end: float | None) -> float:
    sentence = END_TIME.sub(" ", sentence)
    relative = RELATIVE_TIME.search(sentence)
    if relative:
        if ABSOLUTE_TIME.search(sentence) or len(RELATIVE_TIME.findall(sentence)) != 1:
            raise _error(sentence, "Use one absolute or relative event time")
        if previous_end is None:
            raise _error(sentence, "Relative time has no preceding action")
        value = relative[1].lower()
        words = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))
        return previous_end + (words[value] if value in words else float(value))
    matches = list(ABSOLUTE_TIME.finditer(sentence))
    if len(matches) > 1:
        raise _error(sentence, "Specify one absolute event start time; this action has multiple start times")
    if len(matches) != 1:
        raise _error(sentence, "Missing absolute event time")
    match = matches[0]
    return float(next(g for g in match.groups() if g is not None))


def _ordered_instructions(sentences, vehicles, event_bounds):
    """Bind narrative references first, then yield events at their actual times.

    The caller records each checked (start, end) in event_bounds after a yield.
    This also resolves relative times following rate-derived speed changes.
    """
    events = []
    for index, sentence in enumerate(sentences):
        if VEHICLE_EVENT.search(sentence):
            try:
                role = _event_subject(sentence)
            except ValueError:
                # Global constraints such as 'neither vehicle changes lane'
                # have their own checked branch in the caller.
                pass
            else:
                events.append((index, sentence, role))
                continue
        yield index, sentence, None

    previous, previous_kind, previous_clause = {}, {}, None
    pending = {}
    for index, sentence, role in events:
        if role == 'anonymous':
            support = [name for name in vehicles if name != 'ego']
            if len(support) != 1:
                raise _error(sentence, 'Ambiguous vehicle reference; name the vehicle')
            role = support[0]
        if role not in vehicles:
            raise _error(sentence, 'Event refers to an undefined vehicle')
        relative = RELATIVE_TIME.search(sentence)
        dependency, starting = None, False
        if relative:
            wording = relative[0].lower()
            starting = 'starting' in wording
            dependency = previous.get(role)
            if 'previous clause action' in wording:
                dependency = previous_clause
            for kind in ('lane change', 'braking', 'acceleration'):
                if wording.endswith(kind):
                    dependency = previous_kind.get((role, kind))
                    break
        pending[index] = (sentence, role, dependency, starting)
        kind = ('acceleration' if re.search(r'\baccelerates\b', sentence, re.I) else
                'braking' if re.search(r'slowing|slows|decelerate|减速', sentence, re.I) else
                'speed change' if CHECKED_SPEED_ACTION.search(sentence) else 'lane change')
        previous[role] = previous_kind[(role, kind)] = previous_clause = index

    while pending:
        ready = []
        for index, (sentence, role, dependency, starting) in pending.items():
            if dependency is not None and dependency not in event_bounds:
                continue
            anchor = event_bounds[dependency][0 if starting else 1] if dependency is not None else None
            ready.append((_event_time(sentence, anchor), role != 'ego', index, anchor))
        # Dependencies always point to an earlier narrative instruction, so
        # at least one event is ready; missing anchors are checked above.
        _, _, index, anchor = min(ready)
        sentence, _, _, _ = pending.pop(index)
        yield index, sentence, anchor


def _event_subject(sentence: str) -> str:
    rest = ABSOLUTE_TIME.sub(" ", RELATIVE_TIME.sub(" ", END_TIME.sub(" ", sentence))).lstrip(" ,")
    if re.match(r"(?:the\s+)?ego\b", rest, re.I):
        return "ego"
    match = re.match(r"(?:the\s+)?(vehicle_[1-6])\b", rest, re.I)
    if match:
        return match[1].lower()
    if re.match(r"(?:(?:this|that|the)\s+(?:lead\s+)?car\b|it\b)", rest, re.I):
        return "anonymous"
    raise _error(sentence, "Event subject is ambiguous or names an undefined car")


def _check_event_words(sentence: str, role: str = '') -> None:
    rest = RATE.sub(" ", RELATIVE_TIME.sub(" ", sentence))
    relative_turns = list(re.finditer(r"\bturns?\s+(?:left|right)\b", rest, re.I))
    absolute_lane_action = re.search(r"lane change|changes? into|changing into", EVENT_DURATION.sub(" ", rest), re.I)
    if len(relative_turns) > 1 or (relative_turns and absolute_lane_action):
        raise _error(sentence, "Put each timed action in a separate sentence")
    if (absolute_lane_action or relative_turns) and CHECKED_SPEED_ACTION.search(rest):
        raise _error(sentence, "Put each timed action in a separate sentence")
    rest = END_TIME.sub(" ", rest)
    rest = ABSOLUTE_TIME.sub(" ", EVENT_DURATION.sub(" ", rest))
    rest = re.sub(r"\bfrom\s+(?:the\s+)?(?:right|left|middle)(?:\s+lane)?\b", " ", rest, flags=re.I)
    # Repair validation also sees relative commands before the parser resolves
    # their destination against the actor's current lane.
    rest = re.sub(r"\bturns?\s+(?:left|right)\b", " ", rest, flags=re.I)
    rest = re.sub(r"\b(?:left|right)\b(?=\s+into\b)", " ", rest, flags=re.I)
    rest = re.sub(rf"\bat\s+(?={SPEED.pattern})", " ", rest, flags=re.I)
    # 'by' is valid for a speed difference, not a discarded deadline marker.
    rest = re.sub(rf"\bby\s+(?={SPEED.pattern})", " ", rest, flags=re.I)
    rest = LANE.sub(" ", SPEED.sub(" ", rest))
    rest = re.sub(r"\bvehicle_[1-6]\b", " ", rest, flags=re.I)
    rest = re.sub(r'\badjusts speed\b', ' ', rest, flags=re.I)
    rest = re.sub(r"\b(?:ego|vehicle|car|lead|the|a|this|that|it|starts|begins|changes|changing|change|moves?|moving|merges?|merging|cuts?|switch(?:es)?|go(?:es)?|into|to|in|over|and|completes|lane|smoothly|from|then|keeps|slowing|down|slows|decelerates?|accelerates?|taking|takes)\b", " ", rest, flags=re.I)
    if rest.strip(" ,"):
        example, recognized = _event_guidance(sentence, role or _event_subject(sentence))
        raise _error(sentence, code='event', unrecognized=rest.strip(' ,'), example=example, recognized=recognized)


def _incomplete_speed_match(body):
    partial = partial_position_guidance(body)
    if partial:
        start, end = partial[3]
        body = body[:start] + ' ' * (end - start) + body[end:]
    # Preserve offsets so both guidance and residual-word checks can remove
    # exactly this partial value, without swallowing following requirements.
    body = ABSOLUTE_TIME.sub(lambda m: ' ' * len(m[0]), body)
    body = re.sub(rf'\b{NUMBER}\s*{SECONDS}\b', lambda m: ' ' * len(m[0]), body, flags=re.I)
    match = re.search(rf'(?<![\w.])({NUMBER})(?![\d.])(?:\s*([a-zA-Z/]+))?', body)
    if not match:
        return None
    value, unit = match.groups()
    if unit and unit.lower() in ('in', 'on', 'at', 'and', 'initially', 'with', 'without', 'while', 'for', 'until', 'over', 'after', 'before', 'when', 'if', 'unless'):
        return value, None, match.span(1)
    return value, unit, match.span()


def _incomplete_speed(body):
    partial = _incomplete_speed_match(body)
    if not partial:
        return None
    value, unit, _span = partial
    if unit:
        return ('<converted-speed> km/h',
                f'unsupported speed unit: {value} {unit}. Convert the value to km/h, mph or m/s; do not just relabel it. / 速度單位 {unit} 未支援，請先換算 {value}，不可只更換單位名稱。',
                [f'{value} {unit} (conversion needed)'])
    return (f'{value} <speed-unit>',
            f'speed unit for {value}: km/h, mph or m/s? / 已知速度數值 {value}，請補單位。',
            [f'speed value {value} (unit needed)'])


def _state_unrecognized(sentence, allow_partial=False):
    rest, _position = extract_position(sentence)
    if allow_partial:
        partial = partial_position_guidance(rest)
        if partial:
            start, end = partial[3]
            rest = rest[:start] + ' ' * (end - start) + rest[end:]
        partial_speed = _incomplete_speed_match(rest) if not SPEED.search(rest) else None
        if partial_speed:
            start, end = partial_speed[2]
            rest = rest[:start] + ' ' + rest[end:]
    rest = SPEED.sub(' ', rest)
    rest = LANE.sub(' ', rest)
    if allow_partial:
        rest = re.sub(r'\bsame\s+(?:speed|lane)(?:\s+as\s+(?:the\s+)?ego)?\b', ' ', rest, flags=re.I)
    rest = re.sub(rf"{NUMBER}\s*(?:metres?|meters?|m)\s+(?:ahead|in front|behind)\b|(前方|后方)\s*{NUMBER}\s*米", ' ', rest, flags=re.I)
    rest = re.sub(rf"{NUMBER}\s*(?:seconds?|secs?|s)\s+(?:ahead|in front|behind)\b", ' ', rest, flags=re.I)
    rest = re.sub(r'\bvehicle_[1-6]\b', ' ', rest, flags=re.I)
    rest = re.sub(r"\b(?:the|ego|vehicle|drives|travels|starts|stays|is|in|at|one|a|lead|car|same|lane|of|driving|travelling|traveling|initially|keeps|continues|straight|also|doing|speed|and)\b|只有一辆前车|自车|在同一条|在|以|行驶|处|也", ' ', rest, flags=re.I)
    return re.sub(r'\s+', ' ', rest).strip(' ,，、:')


def _state_guidance(sentence, role, position_hint=None):
    """Preserve only unambiguous values; never invent a missing scene fact."""
    body, position = extract_position(sentence)
    position = position or position_hint
    lanes, speeds = list(LANE.finditer(body)), list(SPEED.finditer(body))
    lane = _lane(body).lower() if len(lanes) == 1 else '<lane>'
    speed = speeds[0][0] if len(speeds) == 1 else '<speed> km/h'
    if not lanes and role != 'ego' and re.search(r'\b(?:same|ego) lane\b', body, re.I):
        lane = 'ego'
    if not speeds and role != 'ego' and re.search(r'\bsame speed\b', body, re.I):
        speed = 'the same speed as ego'
    partial_speed = _incomplete_speed(body) if not speeds and speed != 'the same speed as ego' else None
    if partial_speed:
        speed = partial_speed[0]
    example = f'{role} in the {lane} lane at {speed}'
    recognized = ([f'{lane} lane'] if lane != '<lane>' else []) + ([speed] if '<speed>' not in speed else [])
    if partial_speed:
        recognized = ([f'{lane} lane'] if lane != '<lane>' else []) + partial_speed[2]
    if role != 'ego':
        partial = partial_position_guidance(body) if position is None else None
        example += ' ' + (position.phrase() if position else partial[0] if partial else '<gap> seconds <ahead of/behind> ego')
        if partial:
            recognized.extend(partial[2])
    if position:
        recognized.append(position.phrase())
    # Unsupported requirements belong in the visible diagnostic/omission
    # notice. Re-inserting them makes even a completed replacement fail again.
    return example + '.', recognized


def _event_guidance(sentence, role):
    role = role if role != 'anonymous' else 'vehicle_<number>'
    # Completion times contain "at Ns" too. Use the same exclusions as the
    # event reader, and retain all supplied constraints for later validation.
    timing_body = RELATIVE_TIME.sub(' ', END_TIME.sub(' ', sentence))
    times = list(ABSOLUTE_TIME.finditer(timing_body))
    relatives = list(RELATIVE_TIME.finditer(sentence))
    durations = list(EVENT_DURATION.finditer(sentence))
    ends, rates = list(END_TIME.finditer(sentence)), list(RATE.finditer(sentence))
    value = lambda match: next(g for g in match.groups() if g is not None)
    recognized = [f'start at {value(match)}s' for match in times] + [match[0] for match in relatives]
    recognized += [f'duration {value(match)}s' for match in durations]
    speed_values = re.findall(
        rf'\b(?:(?:from|to|by|at|then\s+keeps)\s+)?{NUMBER}\s*(?:km/h|kmh|kph|mph|m/s)\b',
        RATE.sub(' ', sentence), re.I)
    if CHECKED_SPEED_ACTION.search(sentence):
        # Retain source, target, final hold and even conflicting extra speeds.
        # A repair example must not hide a validation error by dropping facts.
        if not any(re.match(r'(?:to|by)\b', item, re.I) for item in speed_values):
            speed_values.append('to <target> km/h')
        action = ('brakes' if re.search(r'\bdecelerates\b', sentence, re.I) else
                  'changes speed' if re.search(r'\badjusts speed\b', sentence, re.I) else 'accelerates') + ' ' + ' '.join(speed_values)
    else:
        turn = re.search(r'\bturns?\s+(left|right)\b', sentence, re.I)
        targets = re.findall(r'\b(?:(?:left|right)\s+)?(?:into|to)\s+(?:the\s+)?(?:left|middle|right|ego)\s+lane\b', sentence[turn.end():] if turn else sentence, re.I)
        action = (f'changes {turn[1]}' if targets else f'turn {turn[1]}') if turn else 'changes'
        if targets:
            action += ' ' + ' '.join(targets)
        elif not turn:
            action += ' into the <destination> lane'
        sources = re.findall(r'\bfrom\s+(?:the\s+)?(?:left|middle|right)(?:\s+lane)?\b', sentence, re.I)
        if sources:
            action += ' ' + ' '.join(sources)
        if speed_values:
            action += ' ' + ' '.join(speed_values)
    recognized.append(action)
    suffix_parts = [f'over {value(match)}s' for match in durations]
    suffix_parts += [f'finishes at {match[1]}s' for match in ends] + [match[0] for match in rates]
    recognized += [f'finishes at {match[1]}s' for match in ends] + [match[0] for match in rates]
    suffix = ' '.join(suffix_parts) if suffix_parts else 'over <duration>s'
    prefix_parts = [f'At {value(match)}s' for match in times] + [match[0] + ',' for match in relatives]
    prefix = (' '.join(prefix_parts) if prefix_parts else 'At <start>s') + ' '
    return f'{prefix}{role} {action} {suffix}.', recognized


def _scenario_duration_issues(sentences, total, headers, requirements=()):
    """Repair the total duration while retaining the descriptions that need it."""
    overrun = [(ENTITY.match(sentence)[1], end) for sentence in sentences if ENTITY.match(sentence)
               if (end := entity_explicit_end(sentence)) is not None and end > total]
    overrun.extend((label, end) for label, end in requirements if end > total)
    for sentence in sentences:
        actor = ENTITY.match(sentence) or STATE_ROLE.match(sentence)
        if not actor or VEHICLE_EVENT.search(sentence):
            continue
        _, spec = extract_position(sentence)
        entity = ENTITY.match(sentence)
        headway = (spec and not spec.initial and spec.unit != 'route_time'
                   and (not entity or entity[1].startswith('obstacle')))
        if spec and not headway and spec.unit != 'metres' and spec.value > total:
            overrun.append((actor[1] + ' position reference', spec.value))
    if not overrun:
        return []
    # Six-digit :g output can round down below an object's end, making its
    # suggested repair fail forever. Plain round-trip decimals also stay within
    # the grammar for large values (which do not accept scientific notation).
    seconds = lambda value: format(Decimal(str(value)).normalize(), 'f')
    required = seconds(max(end for _, end in overrun))
    duration_headers = [header for header in headers if DURATION.search(header)]
    current = f'{seconds(total)}s' + ('' if duration_headers else ' (default; no duration was specified)')
    fields = [f'Scenario duration is {current}, but your descriptions require at least {required}s. '
              f'Apply the suggested duration to keep their timing. To keep a {seconds(total)}s scenario, '
              'revise the event times or position references instead.']
    known = [f'{role} ends at {seconds(end)}s' for role, end in overrun]
    if not duration_headers:
        return [('scenario duration', fields, f'Create a {required}-second scenario.', known)]

    def extend(match):
        group = next(i for i, value in enumerate(match.groups(), 1) if value is not None)
        start, end = match.start(group) - match.start(), match.end(group) - match.start()
        return match[0][:start] + required + match[0][end:]

    # All consistent declarations must change together, including duplicates;
    # otherwise fixing one header would create a new conflicting-duration error.
    return [('scenario duration', fields, DURATION.sub(extend, header) + '.', known, header)
            for header in duration_headers]


def duration_requirement_error(traffic_text, total, requirements):
    """Shared header repair for validators running after the traffic reader."""
    normalized = normalize_vehicle_language(traffic_text)
    headers = [s.strip() for s in re.split(r'[.!?](?=\s|$)|[。！？;\r\n]+', normalized)
               if re.match(r'\s*(?:create|make|use|keep the total scenario duration)\b|创建', s, re.I)]
    return missing_information(_scenario_duration_issues([], total, headers, requirements))


def _check_missing_information(sentences: list[str], total=None, headers=(), position_hints=None) -> None:
    """Report absent values together after wording/subject normalization."""
    missing = []
    defined = {(STATE_ROLE.search(s)[1] or 'vehicle_1').lower() for s in sentences
               if STATE_ROLE.search(s) and not VEHICLE_EVENT.search(s)}
    state_example = lambda role: f'{role} in the <lane> lane at <speed> km/h' + (' <gap> seconds <ahead of/behind> ego.' if role != 'ego' else '.')
    if 'ego' not in defined:
        missing.append(('ego', ['initial lane: left, middle or right? / ego 的初始車道？',
                                'initial speed with units? / ego 的初始速度與單位？'], state_example('ego')))
    requested_states = {'ego'} if 'ego' not in defined else set()
    for sentence in sentences:
        if ENTITY.match(sentence):
            issue = entity_missing_information(sentence)
            if issue:
                missing.append(issue)
            continue
        if re.fullmatch(r"neither vehicle changes lane", sentence, re.I):
            continue
        if VEHICLE_EVENT.search(sentence):
            try:
                subject = _event_subject(sentence)
            except ValueError:
                raise _error(sentence, "Unsupported event subject or instruction")
            support_roles = [role for role in defined if role != 'ego']
            if subject == 'anonymous' and len(support_roles) == 1:
                subject = support_roles[0]
            fields = []
            if subject == 'anonymous':
                fields.append('which vehicle performs this action? Name ego or vehicle 1–6. / 這個動作由哪輛車執行？請用明確車號取代 it / that car。')
            needs_state = subject != 'anonymous' and subject not in defined and subject not in requested_states
            if needs_state:
                fields.extend(['initial lane for this undefined vehicle? / 此車尚未定義，初始車道是？',
                               'initial speed with units? / 初始速度與單位？',
                               'initial gap ahead of/behind ego? / 相對 ego 的初始前後車距？'])
                requested_states.add(subject)
            if not ABSOLUTE_TIME.search(END_TIME.sub(' ', sentence)) and not RELATIVE_TIME.search(sentence):
                fields.append('event start time / 动作开始时间？')
            if not EVENT_DURATION.search(sentence) and not END_TIME.search(sentence) and not RATE.search(sentence):
                if CHECKED_SPEED_ACTION.search(sentence):
                    fields.append('event duration, completion time, or acceleration/deceleration rate: over <duration>s, finishes at <end>s, or at <rate>m/s2? / 請擇一補上動作用時、完成時間或加／減速度；開始時間不代表煞車用時。')
                else:
                    fields.append('event duration or completion time / 动作持续时间或完成时间？')
            if CHECKED_SPEED_ACTION.search(sentence) and not re.search(rf"\b(?:to|by)\s+{NUMBER}\s*(?:km/h|kmh|kph|mph|m/s)\b", sentence, re.I):
                fields.append('target speed, speed change amount, or stop / 目标速度、速度变化量或停车？')
            if fields or subject in requested_states:
                example = (state_example(subject) + '\n  ' if needs_state else '')
                event_example, recognized = _event_guidance(sentence, subject)
                if not CHECKED_SPEED_ACTION.search(sentence) and (SPEED.search(sentence) or RATE.search(sentence)):
                    from .prompt_event_repairs import separate_lane_speed_example
                    event_example = separate_lane_speed_example(event_example, subject)
                    fields.append('Give the speed change its own action and start time. The lane change and supplied speed/rate are kept; fill each missing duration or target below.')
                example += event_example
                missing.append(('vehicle_<number>' if subject == 'anonymous' else subject, fields, example, recognized, sentence))
        elif STATE_ROLE.search(sentence):
            role = (STATE_ROLE.search(sentence)[1] or "vehicle_1").lower()
            if len(SPEED.findall(sentence)) > 1 or len(LANE.findall(sentence)) > 1:
                raise _error(sentence, 'Specify one speed and one initial lane per vehicle')
            position_body, position = extract_position(sentence)
            position = position or (position_hints or {}).get(role)
            spawn_times = ABSOLUTE_TIME.findall(position_body)
            if any(float(next(value for value in values if value)) != 0 for values in spawn_times):
                raise PromptInputError(f'{role}: Delayed vehicle spawn is unsupported; vehicles start at time 0. Define initial lane, speed and gap at time 0, then specify timed driving actions. / 車輛目前只能從第 0 秒存在，請區分初始位置與之後的動作。\n{sentence}', clause=sentence, code='vehicle_spawn')
            fields = []
            continuation = re.search(r'\bkeeps|continues\b', sentence, re.I) and any(
                (STATE_ROLE.search(other)[1] or 'vehicle_1').lower() == role and LANE.search(other)
                for other in sentences if STATE_ROLE.search(other) and not VEHICLE_EVENT.search(other))
            if not continuation and not LANE.search(sentence) and not re.search(r'same lane|ego lane', sentence, re.I):
                fields.append('initial lane: left, middle or right? / 初始車道？')
            if not SPEED.search(sentence) and not re.search(r'same speed', sentence, re.I):
                partial_speed = _incomplete_speed(position_body)
                fields.append(partial_speed[1] if partial_speed else 'initial speed with units (km/h, mph or m/s)? / 初始速度與單位？')
            if role.lower() != "ego" and position is None:
                fields.append('initial gap: ' + missing_position_field(sentence))
            if fields:
                omitted = [match[0] for match in STATE_INTERVAL.finditer(position_body)]
                if omitted:
                    fields.append(STATE_INTERVAL_HINT)
                extra = STATE_INTERVAL.sub('', _state_unrecognized(sentence, allow_partial=True)).strip(' ,')
                if extra:
                    fields.append(f'Unrecognized requirement: {extra}. It is not included in the replacement; review whether the suggested initial state matches your intent.')
                    omitted.append(extra)
                from .prompt_feedback import replacement_omission_notice
                notice = replacement_omission_notice('; '.join(omitted)) if omitted else ''
                example, recognized = _state_guidance(sentence, role, position_hint=position)
                missing.append((role, fields, example, recognized, sentence, notice))
    if total is not None:
        missing.extend(_scenario_duration_issues(sentences, total, headers))
    if missing:
        raise missing_information(missing)


def _resolve_shared_values(sentences: list[str]) -> list[str]:
    """Resolve explicitly shared speed and actor-local omitted speed units."""
    state_indices = [i for i, s in enumerate(sentences) if STATE_ROLE.search(s) and not VEHICLE_EVENT.search(s)]
    shared_indices = []
    for i, sentence in enumerate(sentences):
        shared = re.fullmatch(r"(both|all)\s+(?:vehicles|cars)\s+(?:are\s+)?(?:(?:travelling|traveling|driving|going|moving)\s+(?:at\s+)?|at\s+)(.+)", sentence, re.I)
        if not shared:
            continue
        if not SPEED.fullmatch(shared[2]) or len(state_indices) < 2 or (shared[1].lower() == "both" and len(state_indices) != 2):
            raise _error(sentence, "Shared speed needs the stated number of explicit vehicles and one speed")
        for index in state_indices:
            existing = SPEED.search(sentences[index])
            if existing and abs(_speed(existing[0]) - _speed(shared[2])) > 1e-6:
                raise _error(sentence, "Conflicting shared speed and initial speed")
            if not existing:
                sentences[index] += " at " + shared[2]
        shared_indices.append(i)
    units = {}
    for i in state_indices:
        match = SPEED.search(sentences[i])
        if match:
            units[(STATE_ROLE.search(sentences[i])[1] or "vehicle_1").lower()] = match[2]
    for i, sentence in enumerate(sentences):
        if not CHECKED_SPEED_ACTION.search(sentence):
            continue
        try:
            subject = _event_subject(sentence)
        except ValueError:
            continue
        if subject == "anonymous" and len([r for r in units if r != "ego"]) == 1:
            subject = next(r for r in units if r != "ego")
        if subject not in units:
            continue
        # Do not attach a speed unit to a number already followed by a unit,
        # including invalid units that should remain visible to validation.
        sentences[i] = re.sub(rf"\b(to|by)\s+({NUMBER})(?=\s*(?:[,.;]|$)|\s+(?:over|in|taking|within|and)\b)", lambda m: f"{m[1]} {m[2]} {units[subject]}", sentence, flags=re.I)
    return [s for i, s in enumerate(sentences) if i not in shared_indices]


def _prepare_ego_relations(sentences):
    """Separate initial-state supplements from new vehicle definitions."""
    relations, ordinary, position_hints = [], [], {}
    for sentence in sentences:
        relation = extract_ego_vehicle_relation(sentence)
        if relation is None:
            ordinary.append(sentence)
            continue
        body, role, position = relation
        rest = _state_unrecognized(body)
        if rest or len(LANE.findall(body)) > 1 or len(SPEED.findall(body)) > 1:
            raise _error(sentence, 'Describe one initial relationship without timed actions or conditions',
                         unrecognized=rest, example=f'Ego {position.value:g} {position.unit} ' +
                         ('behind' if position.sign > 0 else 'ahead of') + f' {role}.')
        relations.append((sentence, body, role, position))
    ego_defined = any(STATE_ROLE.match(s) and (STATE_ROLE.match(s)[1] or '').lower() == 'ego'
                      and not VEHICLE_EVENT.search(s) for s in ordinary)
    for sentence, body, role, position in relations:
        owns_ego_state = not ego_defined
        if not ego_defined:
            missing = []
            if not LANE.search(body):
                missing.append('ego initial lane: left, middle or right?')
            if not SPEED.search(body):
                missing.append('ego initial speed with units?')
            if missing:
                example, known = _state_guidance(body, 'ego')
                direction = 'behind' if position.sign > 0 else 'ahead of'
                example = example.rstrip('.') + f' {position.value:g} {position.unit} {direction} {role}.'
                raise missing_information([('ego', missing, example, known, sentence)])
            ordinary.insert(0, body)
            ego_defined = True
        indices = [i for i, s in enumerate(ordinary) if STATE_ROLE.match(s)
                   and (STATE_ROLE.match(s)[1] or '').lower() == role and not VEHICLE_EVENT.search(s)]
        if not indices:
            raise _error(sentence, f'{role} is referenced but has no initial definition. Add its lane and speed; its gap is already specified.',
                         example=(body + '.\n' if owns_ego_state else '') + f'{role} in the <lane> lane at <speed> km/h {position.phrase()}.')
        # The inverse sentence can supply a missing gap. Existing gaps remain
        # unchanged and are checked for agreement after all positions resolve.
        for index in indices:
            _, existing = extract_position(ordinary[index])
            if existing is None and not partial_position_guidance(ordinary[index]):
                position_hints.setdefault(role, position)
    return ordinary, relations, position_hints


def _check_ego_relations(relations, vehicles):
    ego = vehicles['ego']
    for sentence, body, role, position in relations:
        if LANE.search(body) and _lane(body) != ego['lane']:
            raise _error(sentence, f"Conflicting ego lane: its initial lane is {ego['lane']}. Correct this supplemental description.",
                         example=LANE.sub('<lane> lane', sentence) + '.')
        if SPEED.search(body) and abs(_speed(body) - ego['speed_kmh']) > 1e-6:
            raise _error(sentence, f"Conflicting ego speed: its initial speed is {ego['speed_kmh']:g} km/h.",
                         example=SPEED.sub('<speed> km/h', sentence) + '.')
        cfg = vehicles[role]
        speed_mps = ego['speed_kmh'] / 3.6
        claimed_gap = position.sign * position.value * (speed_mps if position.unit == 'seconds' else 1)
        actual_gap = (cfg['initial_time_gap_s'] * speed_mps if cfg.get('initial_time_gap_s') is not None
                      else cfg.get('initial_gap_m', 0))
        if abs(claimed_gap - actual_gap) > 1e-6:
            direction = 'behind' if actual_gap >= 0 else 'ahead of'
            # Both amount and direction may conflict. Retain the known actor,
            # and use the direction implied by its existing initial state.
            example = body + f' <gap> {position.unit} {direction} {role}.'
            expected = abs(actual_gap) / (speed_mps if position.unit == 'seconds' else 1)
            raise _error(sentence, f'Conflicting initial gap: the existing {role} definition places ego {expected:g} {position.unit} {direction} {role}. Revise the gap here or edit that vehicle definition.',
                         example=example)


def _extract_checked_obstacle_drops(sentences):
    """Keep the legacy drop command, after checking every supplied field."""
    remaining, drops = [], []
    for sentence in sentences:
        if (not re.search(r'\b(?:drops?|releases?)\b', sentence, re.I)
                or re.search(r'\bdrops?\s+(?:(?:its|the)\s+)?speed\b', sentence, re.I)):
            remaining.append(sentence)
            continue
        match = re.fullmatch(
            rf'(?:(obstacle[ _-]*[1-9]\d*)\s*:\s*)?'
            rf'(?:(?:t\s*=\s*|at\s*)({NUMBER})\s*{SECONDS}\s*[:,]?\s*)?'
            r'(ego|vehicle_[1-6])\s+(?:drops?|releases?)\s+'
            r'(disabled vehicle|traffic cone|barrel|cone|barrier|debris|box|crate|obstacle|object)\b(.*)',
            sentence, re.I)
        if match is None:
            raise _error(sentence, "Use 't=8s: vehicle_1 drops barrel, offset=0.5m, end=30s' with one source vehicle and obstacle kind")
        role = 'obstacle_' + re.search(r'\d+$', match[1])[0] if match[1] else None
        kind = match[4].lower()
        kind = {'traffic cone': 'cone', 'crate': 'box', 'obstacle': 'barrel', 'object': 'barrel'}.get(kind, kind)
        drop = dict(role=role, source_role=match[3].lower(), kind_text=kind,
                    start_time_s=float(match[2] or 0), end_time_s=None, drop_offset_m=.5,
                    source_clause=sentence)
        rest, supplied = match[5].strip(), set()
        if rest and not rest.startswith(','):
            raise _error(sentence, 'Separate drop options with commas; remove unsupported drop instructions')
        for option in rest[1:].split(',') if rest else []:
            field = re.fullmatch(rf'\s*(offset|drop_offset|rear_offset|end|until)\s*=\s*([+-]?{NUMBER})\s*([a-z]*)\s*', option, re.I)
            if field is None:
                raise _error(sentence, f'Invalid drop option: {option.strip() or "(empty)"}. Use offset=<metres>m or end=<seconds>s')
            key = 'drop_offset_m' if 'offset' in field[1].lower() else 'end_time_s'
            unit = field[3].lower()
            allowed_units = ('', 'm', 'metre', 'metres', 'meter', 'meters') if key == 'drop_offset_m' else ('', 's', 'sec', 'secs', 'second', 'seconds')
            value = float(field[2])
            if key in supplied or unit not in allowed_units or not math.isfinite(value) or value < 0:
                raise _error(sentence, 'Drop options need one nonnegative offset in metres and one end time in seconds')
            supplied.add(key)
            drop[key] = value
        if not math.isfinite(drop['start_time_s']):
            raise _error(sentence, 'Drop appearance time must be finite')
        drops.append(drop)
    return remaining, drops


def _append_checked_obstacle_drops(drops, obstacles, vehicles, total):
    from .scanner_prompt_adapter import _append_dropped_obstacles
    requirements = []
    # Reserve explicit drop IDs before assigning any unnamed drop; fixed
    # obstacles are already present, so neither group can overwrite the other.
    for drop in sorted(drops, key=lambda item: item['role'] is None):
        role, sentence = drop['role'], drop['source_clause']
        if role in obstacles:
            raise _error(sentence, f'Duplicate {role}; use a different obstacle number')
        target = {} if role else obstacles
        try:
            _append_dropped_obstacles(target, [drop], vehicles, total, set(vehicles))
        except ValueError as exc:
            raise _error(sentence, str(exc)) from exc
        if role:
            cfg = target['obstacle_1']
            cfg['role'] = role
            obstacles[role] = cfg
        end = total if drop['end_time_s'] is None else drop['end_time_s']
        requirements.append((role or 'Dropped obstacle', end))
    return requirements


def _motion_definition_error(sentence, detail, role, example, definitions=()):
    error = _error(sentence, detail, example=example)
    error.repair_requests = [dict(role=role, clause=sentence, example=example,
                                 fields=[], recognized=[], notice=detail)]
    error.repair_definitions = list(definitions)
    return error


def _extract_checked_motion_profiles(sentences):
    """Read complete legacy profile declarations without discarding extra text."""
    remaining, profiles, modes = [], {}, {}
    for sentence in sentences:
        prefix = re.match(r'^(ego|vehicle_[1-6])\s*:?[ \t]+(speed_points|gap_points|time_gap_points|longitudinal_mode)\b', sentence, re.I)
        if not prefix:
            remaining.append(sentence)
            continue
        role, key = prefix[1].lower(), prefix[2].lower()
        body = sentence[prefix.end():].strip()
        if key == 'longitudinal_mode':
            declarations = [line for line in sentences if re.match(
                rf'^{re.escape(role)}\s*:?[ \t]+longitudinal_mode\b', line, re.I)]
            if len(declarations) > 1:
                raise _motion_definition_error(sentence, f'Choose one movement mode for {role}; Replace merges its repeated mode declarations and keeps other actions.',
                                               role, f'{role} longitudinal_mode=<motion-mode>', declarations)
            mode = re.fullmatch(r'=\s*(speed|time_gap|fixed_speed)', body, re.I)
            if mode is None:
                raise _motion_definition_error(sentence, 'Use longitudinal_mode=speed, time_gap or fixed_speed; remove extra words',
                                               role, f'{role} longitudinal_mode=<motion-mode>')
            modes[role] = (mode[1].lower(), sentence)
            continue
        if role in profiles:
            raise _error(sentence, f'Conflicting movement profiles for {role}; use one profile declaration')
        if not body.startswith(':'):
            raise _error(sentence, f'Use {key}: followed by comma-separated time=value points with units')
        points, times = [], set()
        field = {'speed_points': 'speed_kmh', 'gap_points': 'gap_m', 'time_gap_points': 'time_gap_s'}[key]
        unit = r'(?:metres?|meters?|m)' if key == 'gap_points' else SECONDS
        for chunk in body[1:].split(','):
            pair = re.fullmatch(rf'\s*({NUMBER})\s*{SECONDS}\s*=\s*(.+?)\s*', chunk, re.I)
            if pair is None:
                raise _error(sentence, f'Invalid {key} point: {chunk.strip() or "(empty)"}. Use time in seconds=value with units')
            at, raw = float(pair[1]), pair[2]
            if key == 'speed_points':
                if not SPEED.fullmatch(raw):
                    raise _error(sentence, 'Speed profile points need nonnegative speeds with km/h, mph or m/s units; remove extra words')
                value = _speed(raw)
            else:
                amount = re.fullmatch(rf'([+-]?{NUMBER})\s*{unit}', raw, re.I)
                if amount is None:
                    raise _error(sentence, f'{key} points need signed values in {"metres" if key == "gap_points" else "seconds"}; remove extra words')
                value = float(amount[1])
            if not math.isfinite(at + value):
                raise _error(sentence, 'Profile times and values must be finite')
            if at in times:
                raise _error(sentence, f'Conflicting {key} values at {at:g}s; keep one value at each time')
            times.add(at)
            points.append({'time_s': at, field: value})
        profiles[role] = (key, sorted(points, key=lambda p: p['time_s']), sentence)
    return remaining, profiles, modes


def _apply_checked_motion_profiles(profiles, modes, vehicles):
    requirements = []
    for role, (key, points, sentence) in profiles.items():
        if role not in vehicles:
            raise _error(sentence, f'{role} is undefined; define its initial lane, speed and position first')
        cfg = vehicles[role]
        if any(cfg.get(name) for name in ('speed_points', 'gap_points', 'time_gap_points')):
            raise _error(sentence, f'Conflicting movement for {role}; choose one profile or prose speed actions')
        cfg[key] = points
        requirements.append((role + ' ' + key, points[-1]['time_s']))
    for role, (mode, sentence) in modes.items():
        if role not in vehicles:
            raise _error(sentence, f'{role} is undefined; define its initial lane, speed and position first')
        cfg = vehicles[role]
        expected = {'speed': 'speed_points', 'time_gap': 'time_gap_points', 'fixed_speed': None}[mode]
        if any(cfg.get(key) and key != expected for key in ('speed_points', 'gap_points', 'time_gap_points')):
            if cfg.get('gap_points'):
                profile_sentence = profiles[role][2]
                raise _motion_definition_error(sentence, f'Keep the explicit distance gap profile for {role}; Replace removes the conflicting mode declaration.',
                                               role, profile_sentence, (sentence, profile_sentence))
            compatible = 'time_gap' if cfg.get('time_gap_points') else 'speed'
            raise _motion_definition_error(sentence, f'The {mode} mode conflicts with the supplied movement for {role}; Replace selects {compatible} and keeps the supplied changes.',
                                           role, f'{role} longitudinal_mode={compatible}')
        if mode == 'time_gap' and not cfg.get('time_gap_points'):
            raise _motion_definition_error(sentence, f'{role} time_gap mode needs a target gap and its time. Enter the target spacing ahead of ego in seconds.',
                                           role, f'{role} time_gap_points: <start>s=<gap>s')
    return requirements


def parse_prose_prompt(text: str) -> tuple[dict[str, Any], list[str]]:
    """Return explicit scenario fields; leave only technical defaults to schema."""
    text = normalize_vehicle_aliases(normalize_prompt_characters(text))
    clauses = [s.strip() for s in re.split(r'[.!?](?=\s|$)|[。！？;\r\n]+', text) if s.strip()]
    clauses, obstacle_drops = _extract_checked_obstacle_drops(clauses)
    clauses, motion_profiles, motion_modes = _extract_checked_motion_profiles(clauses)
    # Only checked signed gap points are exempt from the prose negative-value
    # guard. Malformed declarations and all remaining instructions stay checked.
    # Read profiles before prose normalization trims sentence-end commas.
    text = normalize_vehicle_language('.\n'.join(clauses))
    source_sentences = [s.strip() for s in re.split(r'[.!?](?=\s|$)|[。！？;\r\n]+', text) if s.strip()]
    if re.search(r"(?<!\w)-\s*\d", text):
        clause = next(s for s in source_sentences if re.search(r"(?<!\w)-\s*\d", s))
        from .prompt_event_repairs import negative_value_example
        raise _error(clause, "Negative speeds, distances or times are not supported; use 'behind' for rear spacing",
                     example=negative_value_example(clause))
    if re.search(r"(?:time to collision|\bttc\b|碰撞时间)", text, re.I):
        clause = next(s for s in source_sentences if re.search(r"time to collision|\bttc\b|碰撞时间", s, re.I))
        raise _error(clause, "TTC trigger sentences are not supported by this prose reader: the current runner also has a time fallback. Configure TTC plus its time fallback explicitly in the scenario settings. / TTC 条件尚不能完整转换，请显式配置 TTC 和时间兜底。")
    if re.search(r"(?:return|autonomous).*button|button.*(?:return|autonomous)|按键.*返回", text, re.I):
        clause = next(s for s in source_sentences if re.search(r'(?:return|autonomous).*button|button.*(?:return|autonomous)|按键.*返回', s, re.I))
        raise _error(clause, "Return button mapping is a launcher setting, not a scenario prompt setting. Set G29 return button in the launcher, then remove the button-mapping sentence. / 返回按键请在主界面设置；删除这句后可转换其余场景。")
    for clause in source_sentences:
        if re.search(r'\bpedestrian(?:_\d+)?\b|行人', clause, re.I) and re.search(r'metres per second|meters per second|m/s|米每秒', clause, re.I):
            raise _error(clause, "Pedestrian speed in m/s cannot be preserved by the current schema; specify crossing duration in the Start Wizard instead. / 当前行人模型需在向导中指定横穿用时，不能把步行速度悄悄换成默认用时。")

    # 'for 5s' on an object describes its lifetime, not the entire scenario.
    headers = [s for s in re.split(r'[.!?](?=\s|$)|[。！？;\r\n]+', text)
               if re.match(r'\s*(?:create|make|use|keep the total scenario duration)\b|创建', s, re.I)]
    durations = [float(next(g for g in m.groups() if g is not None)) for header in headers for m in DURATION.finditer(header)]
    if len(set(durations)) > 1 or (durations and durations[0] <= 0):
        raise _error(headers[-1].strip(), "Specify one positive, consistent scenario duration", example='Create a <total>-second scenario.')
    total = durations[0] if durations else 35.0
    out: dict[str, Any] = {"total_time_s": total, "vehicles": {}, "pedestrians": {}, "obstacles": {}}
    maps = set(re.findall(r"\bTown\d+(?:_Opt)?\b", text, re.I))
    if len(maps) > 1:
        first_map = re.search(r"\bTown\d+(?:_Opt)?\b", text, re.I)[0]
        clause = next(s for s in source_sentences if any(m.casefold() != first_map.casefold()
                      for m in re.findall(r"\bTown\d+(?:_Opt)?\b", s, re.I)))
        example = re.sub(r"\bTown\d+(?:_Opt)?\b", first_map, clause, flags=re.I)
        if re.fullmatch(r'Town\d+(?:_Opt)?', example, re.I):
            example = 'Use ' + example
        raise _error(clause, "Conflicting maps; keep one map for this scenario", example=example + '.')
    if maps:
        out["map_name"] = next(iter(maps))
    vehicles = out["vehicles"]
    event_bounds = {}
    stationary_holds = []
    ego_lane_references = []
    tor: dict[str, Any] = {}
    no_tor = no_cars = no_pedestrians = no_lane_changes = keep_speeds = False
    warnings: list[str] = []
    if not durations:
        warnings.append("Auto-filled scenario duration: 35s.")

    sentences = list(filter(None, (s.strip() for s in re.split(r"[.!?](?=\s|$)|[。！？;\r\n]+", text))))
    sentences, ego_relations, position_hints = _prepare_ego_relations(sentences)
    sentences = _resolve_shared_values(sentences)
    _check_missing_information(sentences, total, headers, position_hints)
    # Resolve explicit initial conditions first, even if the author described
    # events before the vehicles. The scheduler binds relative references in
    # narrative order and executes the resulting events chronologically.
    states = [s for s in sentences if STATE_ROLE.search(s) and not VEHICLE_EVENT.search(s)]
    sentences = sorted(states, key=lambda s: not bool(re.match(r"(?:the\s+)?ego\b", s, re.I))) + [s for s in sentences if s not in states]
    seen_sources = {}
    duration_requirements = []
    for event_index, sentence, anchor in _ordered_instructions(sentences, vehicles, event_bounds):
        source_occurrence = seen_sources.get(sentence.casefold(), 0)
        seen_sources[sentence.casefold()] = source_occurrence + 1
        lowered = sentence.lower()
        if ENTITY.match(sentence):
            try:
                role, cfg = parse_scene_entity(sentence, total, warnings)
            except PromptInputError as exc:
                if not exc.clause:
                    exc.clause = sentence
                raise
            group = out['obstacles' if role.startswith('obstacle') else 'pedestrians']
            if role in group:
                example = ENTITY.sub(role.split('_')[0] + '_<number>', sentence, count=1)
                raise _error(sentence, f'Duplicate {role}; use a different object number or revise its original definition',
                             example=example, source_occurrence=source_occurrence)
            group[role] = cfg
            continue
        if re.fullmatch(r"(?:road:\s*)?(?:three|3)[- ]lane highway", lowered):
            warnings.append("Road: using three logical scenario lanes; verify the selected map segment's physical lane count before running. / 使用三个场景车道，实际道路车道数请核对地图。")
            continue
        if re.match(r"(?:create|make|use|keep the total scenario duration)\b|创建", sentence, re.I):
            # Validate headers too: 'Create ... with heavy rain' is a request,
            # not decoration that may be discarded.
            rest = DURATION.sub(" ", sentence)
            rest = re.sub(r"Town\d+(?:_Opt)?", " ", rest, flags=re.I)
            rest = re.sub(r"\b(?:create|make|use|keep|the|a|an|total|scenario|duration|exactly|on|for|map)\b|创建|一个|地图上的|场景", " ", rest, flags=re.I)
            if rest.strip(" ,"):
                raise _error(sentence)
            continue
        if re.fullmatch(r"(?:there is no|do not add a|no) takeover request|不发出接管请求", lowered):
            no_tor = True
            continue
        if re.fullmatch(r"(?:there are no other vehicles(?: and no pedestrians)?|do not add other vehicles|no other vehicles)", lowered):
            no_cars = True
            no_pedestrians |= "no pedestrians" in lowered
            continue
        if re.fullmatch(r"both vehicles keep their lane and speed for the whole scenario|两车全程保持车道和速度,不发出接管请求", lowered):
            no_lane_changes = True
            keep_speeds = True
            no_tor |= "不发出接管请求" in sentence
            continue
        if re.fullmatch(r"neither vehicle changes lane", lowered):
            no_lane_changes = True
            continue

        if re.match(r"(?:issue|give|the driver|trigger).*\b(?:takeover|take over|warning)\b", lowered):
            onset = re.search(rf"(?:alert|warning)\s+at\s+{VALUE_SECONDS}", sentence, re.I)
            deadline = re.search(rf"(?:take over\s+)?by\s+{VALUE_SECONDS}", sentence, re.I)
            if onset:
                if "tor_onset_s" in tor and tor["tor_onset_s"] != float(onset[1]):
                    raise _error(sentence, "Conflicting TOR alert times; only one prose TOR window is supported")
                tor["alert_onset_s"] = tor["tor_onset_s"] = float(onset[1])
            if deadline:
                if "tor_deadline_s" in tor and tor["tor_deadline_s"] != float(deadline[1]):
                    raise _error(sentence, "Conflicting TOR deadlines")
                tor["tor_deadline_s"] = float(deadline[1])
            if "g29" in lowered:
                tor["manual_keys"] = ["G29"]
            if not onset and not deadline:
                raise _error(sentence, "TOR needs an explicit alert time and deadline")
            rest = re.sub(rf"\b(?:at|by)\s+{VALUE_SECONDS}", " ", sentence, flags=re.I)
            rest = re.sub(r"\b(?:issue|a|takeover|take|over|alert|warning|the|driver|must|by|turning|logitech|g29|steering|wheel)\b", " ", rest, flags=re.I)
            if rest.strip(" ,"):
                raise _error(sentence, "Unsupported TOR input or instruction")
            continue

        event = bool(VEHICLE_EVENT.search(sentence))
        if event:
            role = _event_subject(sentence)
            if role == "anonymous":
                support_roles = [r for r in vehicles if r != "ego"]
                if len(support_roles) != 1:
                    raise _error(sentence, "Ambiguous vehicle reference; name the vehicle")
                role = support_roles[0]
            if role not in vehicles:
                raise _error(sentence, "Event refers to an undefined vehicle")
            cfg = vehicles[role]
            lane_sentence = sentence
            turn = re.search(r"\bturns?\s+(left|right)\b", sentence, re.I)
            if turn and not re.search(r'\b(?:into|to)\s+(?:the\s+)?(?:left|middle|right|ego)\s+lane\b', sentence[turn.end():], re.I):
                points = cfg.get("lane_change_points", [])
                initial = points[-1]["target_lane"] if points else cfg["lane"]
                lanes = ["Left", "Middle", "Right"]
                index = lanes.index(initial) + (-1 if turn[1].lower() == "left" else 1)
                if not 0 <= index < len(lanes):
                    raise _error(sentence, "Turn goes beyond the available lanes")
                lane_sentence = sentence[:turn.start()] + f"changes into the {lanes[index]} lane" + sentence[turn.end():]
            _check_event_words(sentence, role)
            relative = RELATIVE_TIME.search(sentence)
            start = _event_time(sentence, anchor)
            if relative and "stationary" in relative[0].lower():
                stationary_holds.append((role, anchor, start))
            if RATE.search(sentence):
                if not CHECKED_SPEED_ACTION.search(sentence):
                    raise _error(sentence, "Acceleration rates require a speed-change action", event_start=start)
                explicit = _event_duration(sentence, start) if EVENT_DURATION.search(sentence) or END_TIME.search(sentence) else None
                try:
                    duration = speed_event_duration(sentence, start, cfg, _speed, explicit)
                except ValueError as exc:
                    raise _error(sentence, str(exc)) from exc
            else:
                duration = _event_duration(sentence, start)
            if start + duration > total:
                duration_requirements.append((role + ' event', start + duration))
            if CHECKED_SPEED_ACTION.search(sentence) or '减速' in sentence:
                try:
                    apply_speed_event(cfg, sentence, start, duration, _speed, SPEED)
                except ValueError as exc:
                    current = cfg['speed_points'][-1]['speed_kmh'] if cfg.get('speed_points') else cfg['speed_kmh']
                    detail = str(exc)
                    if 'overlap' in detail.lower():
                        detail += f' Start this action at or after {cfg["speed_points"][-1]["time_s"]:g}s.'
                    if 'overlap' not in detail.lower():
                        detail += f' Current speed before this action: {current:g} km/h. / 動作開始前速度為 {current:g} km/h。'
                    raise _error(sentence, detail) from exc
            else:
                if SPEED.search(sentence):
                    raise _error(sentence, "Describe speed changes as a separate timed action; a lane-change speed cannot be discarded", event_start=start)
                targets = list(re.finditer(r"(?:into|to)\s+(?:the\s+)?(left|middle|right|ego)\s+lane", lane_sentence, re.I))
                source = re.search(r"\bfrom\s+(?:the\s+)?(left|middle|right)(?:\s+lane)?\b", lane_sentence, re.I)
                if len(re.findall(r"\bfrom\s+(?:the\s+)?(?:left|middle|right)(?:\s+lane)?\b", lane_sentence, re.I)) > 1:
                    points = cfg.get('lane_change_points', [])
                    initial = points[-1]['target_lane'] if points else cfg['lane']
                    raise _error(sentence, f"Specify one lane-change source lane: current lane is {initial}.")
                lane_mentions = len(re.findall(r"\b(?:left|middle|right|ego)\s+lane\b", lane_sentence, re.I))
                if len(targets) != 1 or lane_mentions > 1 + bool(source and re.search(r"\blane\b", source[0])):
                    raise _error(sentence, "Specify one lane-change destination")
                target = re.search(r"(?:into|to)\s+(?:the\s+)?(left|middle|right|ego)\s+lane|(?:变道到|切入)(左车道|中间车道|右车道)", lane_sentence, re.I)
                if not target:
                    raise _error(sentence, "Missing lane-change destination")
                if target[1] and target[1].lower() == "ego":
                    ego_lane_references.append((sentence, start))
                    dest = vehicles["ego"]["lane"]
                    for ego_point in vehicles["ego"].get("lane_change_points", []):
                        if start >= ego_point["time_s"] + ego_point["duration_s"]:
                            dest = ego_point["target_lane"]
                        elif start >= ego_point["time_s"]:
                            raise _error(sentence, "Ego lane is ambiguous during ego's lane change; name a concrete destination")
                else:
                    dest = _lane(target[0])
                points = cfg.setdefault("lane_change_points", [])
                initial = points[-1]["target_lane"] if points else cfg["lane"]
                if source and source[1].title() != initial:
                    raise _error(sentence, f"Conflicting lane-change source lane: current lane is {initial}. Use that lane as the source, or revise the earlier lane plan.")
                direction = re.search(r"\b(left|right)\s+into\b", sentence, re.I)
                if direction:
                    displacement = ["Left", "Middle", "Right"].index(dest) - ["Left", "Middle", "Right"].index(initial)
                    if (direction[1].lower() == "left" and displacement >= 0) or (direction[1].lower() == "right" and displacement <= 0):
                        raise _error(sentence, "Conflicting lane-change direction and destination")
                if dest == initial:
                    raise _error(sentence, f'Lane change has an unchanged destination. Current lane is {initial}; choose a different destination.')
                if points and start < points[-1]["time_s"] + points[-1]["duration_s"]:
                    raise _error(sentence, f'Lane change overlaps the previous event. Start at or after {points[-1]["time_s"] + points[-1]["duration_s"]:g}s.')
                points.append({"time_s": start, "target_lane": dest, "duration_s": duration})
                cfg.update(lane_change_enabled=True, lane_change_time_s=points[0]["time_s"], lane_change_duration_s=points[0]["duration_s"], lane_change_target_lane=points[0]["target_lane"])
            event_bounds[event_index] = (start, start + duration)
            continue

        if re.match(r"(?:the\s+)?ego\b|自车", sentence, re.I):
            _check_state_words(sentence)
            speed = _speed(sentence)
            if "ego" in vehicles:
                lane_changed = LANE.search(sentence) and _lane(sentence) != vehicles["ego"]["lane"]
                if lane_changed or speed != vehicles["ego"]["speed_kmh"]:
                    definitions = [s for s in states if re.match(r'(?:the\s+)?ego\b', s, re.I)]
                    for definition in definitions:
                        _check_state_words(definition)
                    # Preview the latest initial state; applying the repair is
                    # an explicit choice, not silent last-definition-wins.
                    example, recognized = _state_guidance(definitions[-1], 'ego')
                    error = _error(sentence, "Conflicting ego initial definitions", code='ego_conflict',
                                   example=example, recognized=recognized, source_occurrence=source_occurrence)
                    error.ego_definitions = definitions
                    error.repair_requests = [dict(role='ego', clause=sentence, example=example,
                        source_occurrence=source_occurrence,
                        fields=['Merge the repeated ego initial definitions using the latest lane and speed shown below. Other vehicles and actions are kept.'],
                        notice='Merge the ego initial definitions into one. Review the proposed lane and speed before replacing. Ctrl+Z restores all original descriptions.')]
                    raise error
            else:
                vehicles["ego"] = {"enabled": True, "lane": _lane(sentence), "speed_kmh": speed}
                out["ego_speed_kmh"] = speed
            continue
        if re.match(r"vehicle_[1-6]\b|(?:a|one|the)\s+(?:lead\s+)?car\b|只有一辆前车", sentence, re.I):
            role_match = re.match(r"vehicle_[1-6]\b", sentence, re.I)
            role = role_match[0].lower() if role_match else "vehicle_1"
            if re.search(r"(?:at\s+)?(?:the\s+)?same speed(?:\s+as\s+ego)?", sentence, re.I):
                if "ego" not in vehicles:
                    raise _error(sentence, "Same speed needs an explicit ego speed")
                sentence = re.sub(r"(?:at\s+)?(?:the\s+)?same speed(?:\s+as\s+ego)?", f"at {vehicles['ego']['speed_kmh']} km/h", sentence, flags=re.I)
            _check_state_words(sentence)
            if "ego" not in vehicles:
                raise _error(sentence, "Define ego's initial lane and speed before support vehicles")
            if role in vehicles:
                example, recognized = _state_guidance(sentence, 'vehicle_<number>')
                raise _error(sentence, f'Duplicate {role}: revise its existing initial definition or use a different vehicle number. / 車號 {role} 重複定義；請修改原句或使用不同車號。', example=example, recognized=recognized, source_occurrence=source_occurrence)
            _, position = extract_position(sentence)
            position = position or position_hints.get(role)
            vehicles[role] = {"enabled": True, "lane": _lane(sentence, vehicles["ego"]["lane"]), "speed_kmh": _speed(sentence), "initial_gap_m": 0, '_position_spec': position}
            continue
        if re.match(r"place one (?:stationary )?(?:road )?barrier\b", sentence, re.I):
            if out["obstacles"]:
                raise _error(sentence, "Only one barrier is supported in this prose form")
            if len(LANE.findall(sentence)) != 1:
                raise _error(sentence, "Specify one obstacle lane")
            rest = ABSOLUTE_TIME.sub(" ", sentence)
            rest = LANE.sub(" ", rest)
            rest = re.sub(rf"{NUMBER}\s*(?:metres?|meters?|m)\s+ahead\b", " ", rest, flags=re.I)
            rest = re.sub(r"\b(?:place|one|stationary|road|barrier|in|the|of|initial|ego|position)\b", " ", rest, flags=re.I)
            if rest.strip(" ,"):
                raise _error(sentence)
            start = _event_time(sentence, None) if ABSOLUTE_TIME.search(sentence) else 0
            if start >= total:
                raise _error(sentence, "Obstacle event is beyond scenario duration")
            out["obstacles"]["obstacle_1"] = {"enabled": True, "kind": "Barrier", "lane": _lane(sentence), "gap_m": _gap(sentence), "start_time_s": start, "end_time_s": total, "lock_time_s": 0, "world_locked": True}
            continue
        if re.fullmatch(r"the barrier is present (?:from the start|at 0s)", lowered) and out["obstacles"]:
            barriers = [cfg for cfg in out['obstacles'].values() if cfg['kind'] == 'Barrier']
            if len(barriers) != 1:
                raise _error(sentence, 'Ambiguous barrier reference; put the appearance time in its numbered obstacle definition')
            if barriers[0]["start_time_s"] != 0:
                raise _error(sentence, "Conflicting obstacle appearance times")
            continue
        raise _error(sentence)

    duration_requirements.extend((role, end) for role, end in
                                 _apply_checked_motion_profiles(motion_profiles, motion_modes, vehicles)
                                 if end > total)
    duration_requirements.extend((role, end) for role, end in
                                 _append_checked_obstacle_drops(obstacle_drops, out['obstacles'], vehicles, total)
                                 if end > total)
    for sentence, time in ego_lane_references:
        if any(point['time_s'] <= time < point['time_s'] + point['duration_s']
               for point in vehicles['ego'].get('lane_change_points', [])):
            raise _error(sentence, "Ego lane is ambiguous during ego's lane change; name a concrete destination")
    for role, hold_start, hold_end in stationary_holds:
        validate_stationary_hold(vehicles[role], hold_start, hold_end)
    if "ego" not in vehicles:
        raise _error(text, "Missing ego definition")
    if no_cars and any(role != "ego" for role in vehicles):
        raise _error(text, "Conflicting vehicle count")
    if no_pedestrians and out["pedestrians"]:
        raise _error(text, "Conflicting pedestrian count")
    if no_lane_changes and any(v.get("lane_change_points") for v in vehicles.values()):
        raise _error(text, "Conflicting lane-change requirements")
    if keep_speeds and any(v.get("speed_points") for v in vehicles.values()):
        raise _error(text, "Conflicting constant-speed and speed-change requirements")
    if duration_requirements:
        raise missing_information(_scenario_duration_issues([], total, headers, duration_requirements))
    resolve_positions(vehicles, vehicles['ego'], total, warnings, vehicles=True)
    _check_ego_relations(ego_relations, vehicles)
    resolve_positions(out['obstacles'], vehicles['ego'], total, warnings, reference_vehicles=vehicles)
    resolve_positions(out['pedestrians'], vehicles['ego'], total, warnings)
    if tor:
        onset, deadline = tor.get("tor_onset_s"), tor.get("tor_deadline_s")
        if no_tor or onset is None or deadline is None or not 0 <= onset < deadline <= total:
            raise _error(text, "TOR needs consistent onset/deadline within scenario duration")
        tor.update(name="prompt_tor", failure_action="brake", ready_text="Please take over.")
        out["experiment_events"] = {"tor_windows": [tor]}
    warnings.append("Vehicle and object descriptions, spacing and actions were checked. Review the generated timeline before running.")
    return out, warnings
