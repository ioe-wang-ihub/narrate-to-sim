"""Explain temporal phrases without treating every seconds value as a timestamp."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re

from .collision_language import collision_matches, collision_timing, QUOTED, NUMBER
from .prompt_language import normalize_prompt_characters, NUMBERED_VEHICLE_RE
from .prompt_positions import EGO_POSITION, OFFSET_PATTERNS, extract_position, extract_ego_vehicle_relation
from .prompt_scene_entities import PEDESTRIAN_NAME_RE
from .takeover_alerts import _number_words, alert_boundary_mask, ALERT, WARNING_REQUEST


@dataclass(frozen=True)
class TimeHint:
    kind: str
    title: str
    description: str
    jump_time_s: float = None


TOKEN = re.compile(r'(?P<lead>\b(?:at|after|in|from|until|till|over|for|taking|lasting|lasts|duration(?:\s+of)?|starts?(?:\s+at)?|ends?(?:\s+at)?|t|time)\s*[:=]?\s*)?'
                   r'(?P<value>' + NUMBER + r')[\s-]*(?:seconds?|secs?|s)\b', re.I)
OBJECT = re.compile(r'\b(?:obstacle[ _#-]*\d*|cone|barrier|barrel|debris|box)\b|' + PEDESTRIAN_NAME_RE.pattern, re.I)
ACTOR = re.compile(OBJECT.pattern + r'|\b(?:vehicle_[1-6]|ego)\b', re.I)
PEDESTRIAN_DESCRIPTION = re.compile(
    r'^\s*(?:(?:at|after)\s+' + NUMBER + r'\s*(?:seconds?|secs?|s)\b\s*,?\s*)?'
    r'(?:(?:add|place|put|include|spawn|a|an|the)\s+)*' + PEDESTRIAN_NAME_RE.pattern, re.I)
OBSTACLE_DESCRIPTION = re.compile(
    r'^\s*(?:(?:at|after)\s+' + NUMBER + r'\s*(?:seconds?|secs?|s)\b\s*,?\s*)?'
    r'(?:(?:add|place|put|include|spawn|a|an|the|stationary)\s+)*'
    r'(?:obstacle[ _#-]*\d*|cone|barrier|barrel|debris|box|disabled vehicle)\b', re.I)
POINT = re.compile(r'(?P<time>\d+(?:\.\d+)?)(?:\s*(?:seconds?|secs?|s))?\s*=\s*'
                   r'(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s|m|km/h|kmh|mph)?(?=\s*(?:,|$))', re.I)


@lru_cache(maxsize=8)
def _prepare(text):
    normalized = NUMBERED_VEHICLE_RE.sub(r'vehicle_\1', _number_words(normalize_prompt_characters(text)))
    operations = SequenceMatcher(None, text, normalized, autojunk=False).get_opcodes()
    return normalized, operations


def _review(message):
    return TimeHint('review', 'Time meaning needs review', message.split(' / ')[0])


def _route(value, direction='ahead'):
    return TimeHint('route_position', 'Ego route position · {:g} s {}'.format(value, direction),
                    'A fixed road position based on ego\'s planned travel over {:g} seconds, {} of its initial position. '
                    'The object\'s appearance time is set separately.'.format(value, direction))


def _pedestrian_distance(value, direction='ahead of', initial=False, seconds=False):
    reference = 'the initial ego position' if initial else 'ego at crossing start'
    conversion = ('The seconds offset is converted to metres using ego\'s planned speed profile. '
                  if seconds else '')
    return TimeHint('distance', 'Pedestrian crossing position', conversion +
                    'Places the crossing {:g} {} {} {}. The crossing location stays fixed once crossing begins; '
                    'this is not the crossing start time.'.format(value, 'seconds' if seconds else 'metres', direction, reference))


def time_hint_at(text, offset):
    """Return the meaning at an ORIGINAL source offset, or None for other text.

    These explain recognized phrases; Analyze still validates the full request.
    Only a known scenario clock time supplies a timeline jump.
    """
    if not 0 <= offset < len(text):
        return None
    normalized, operations = _prepare(text)
    point = None
    for kind, old_start, old_end, new_start, new_end in operations:
        if old_start <= offset < old_end:
            if kind == 'delete':
                return None
            point = new_start + (offset - old_start if kind == 'equal' else min(offset - old_start, max(0, new_end - new_start - 1)))
            break
    if point is None or any(m.start() <= point < m.end() for m in QUOTED.finditer(normalized)):
        return None
    masked = QUOTED.sub(lambda m: ' ' * len(m[0]), normalized)
    boundaries = list(re.finditer(r'[.!?](?=\s|$)|[;\r\n]+', alert_boundary_mask(masked)))
    start = max((m.end() for m in boundaries if m.end() <= point), default=0)
    end = min((m.start() for m in boundaries if m.start() > point), default=len(masked))
    clause, cursor = masked[start:end], point - start
    pedestrian = bool(PEDESTRIAN_DESCRIPTION.search(clause))
    obstacle = bool(OBSTACLE_DESCRIPTION.search(clause))
    from .cue_language import cue_head
    sensory_cue = bool(cue_head(clause) or re.match(r'\s*cue\b', clause, re.I))

    for match in collision_matches(clause):
        if match.start() <= cursor < match.end():
            _, trigger, problems = collision_timing(match[0])
            if problems:
                return _review(problems[0])
            value = trigger['threshold_s']
            target = trigger['target_role'] or 'the ' + trigger.get('target_kind', 'obstacle').lower()
            return TimeHint('ttc', 'Live TTC · {:g} s'.format(value),
                            'Triggers once when predicted collision time to {} reaches {:g} seconds or less, '
                            'using current positions and closing speed. The scenario trigger time depends on vehicle motion.'.format(target, value))

    for match in EGO_POSITION.finditer(clause):
        if match.start() <= cursor < match.end():
            try:
                _, position = extract_position(match[0], allow_vehicle_reference=obstacle)
                return _route(position.value)
            except ValueError as exc:
                return _review(str(exc))

    for pattern in OFFSET_PATTERNS:
        for match in pattern.finditer(clause):
            if not match.start() <= cursor < match.end():
                continue
            try:
                inverse = extract_ego_vehicle_relation(clause.strip())
            except ValueError as exc:
                return _review(str(exc))
            if inverse is not None:
                _, actor, position = inverse
                direction = 'behind' if position.sign > 0 else 'ahead of'
                unit = 'seconds' if position.unit == 'seconds' else 'metres'
                return TimeHint('headway' if unit == 'seconds' else 'distance', 'Initial vehicle gap',
                                'Ego starts {:g} {} {} {}. Seconds express initial headway based on ego speed; this is not an event time.'.format(position.value, unit, direction, actor))
            prefix = clause[:match.start()]
            if re.fullmatch(r'seconds?|secs?|s', match['unit'], re.I) and re.search(r'\b(?:at|after)\s*$|\b(?:t|time)\s*=\s*$', prefix, re.I):
                continue
            if re.search(r'[-+]\s*$', prefix):
                return _review('Use a positive position amount and specify ahead or behind.')
            try:
                _, position = extract_position(match[0], allow_vehicle_reference=obstacle)
            except ValueError as exc:
                return _review(str(exc))
            if position is None:
                continue  # "at 10s ahead ..." states appearance, not spacing.
            if pedestrian:
                return _pedestrian_distance(position.value, 'ahead of' if position.sign > 0 else 'behind',
                                            position.initial, position.unit == 'seconds')
            if obstacle and not position.initial:
                if not position.reference_explicit:
                    return _review('Name ego or a defined vehicle, e.g. 100m ahead of vehicle 1. The distance is measured when the obstacle appears.')
                from .obstacle_positions import obstacle_position_text
                cfg = dict(position_reference='vehicle_at_spawn', reference_role=position.reference_role,
                           gap_m=position.sign * position.value,
                           time_gap_s=position.sign * position.value if position.unit == 'seconds' else None)
                # The precise appearance clock is validated by Analyze; this
                # tooltip explains the offset without inventing a start time.
                explanation = obstacle_position_text(cfg).replace('at appearance (0s)', 'when the obstacle appears')
                return TimeHint('distance', 'Obstacle position at appearance', explanation + '.')
            if position.unit != 'seconds':
                return TimeHint('distance', 'Distance from ego',
                                'Sets a spatial distance from ego. This value does not specify when an event starts.')
            subjects = list(ACTOR.finditer(clause[:match.start()]))
            is_object = bool(subjects and OBJECT.fullmatch(subjects[-1][0]))
            direction = 'ahead' if position.sign > 0 else 'behind'
            if is_object or position.initial:
                return _route(position.value, direction)
            return TimeHint('headway', 'Time gap · {:g} s {}'.format(position.value, direction),
                            'Sets the vehicle\'s initial spacing {} of ego as a {:g}-second headway, based on ego speed. '
                            'This is a spacing value.'.format(direction, position.value))

    if pedestrian:
        for match in re.finditer(r'[:,]\s*(?P<value>\d+(?:\.\d+)?)\s*(?:metres?|meters?|m)\b(?=\s*(?:,|$))', clause, re.I):
            if match.start('value') <= cursor < match.end():
                return _pedestrian_distance(float(match['value']))
    if obstacle:
        for match in re.finditer(r'[:,]\s*\d+(?:\.\d+)?\s*(?:metres?|meters?|m)\b(?=\s*(?:,|$))', clause, re.I):
            if match.start() <= cursor < match.end():
                return _review('Name ego or a defined vehicle after the distance, e.g. 100m ahead of ego. Distance is measured at obstacle appearance.')

    profile = re.search(r'\b(time_gap_points|gap_points|speed_points)\b', clause, re.I)
    if profile:
        for match in POINT.finditer(clause):
            if match.start() <= cursor < match.end():
                time, value = float(match['time']), float(match['value'])
                if cursor < match.start('value'):
                    return TimeHint('start', 'Scenario time · {:g} s'.format(time),
                                    'This update applies {:g} seconds after the scenario starts.'.format(time), time)
                if profile[1].lower() == 'time_gap_points':
                    return TimeHint('headway', 'Target time gap · {:g} s'.format(value),
                                    'Sets target headway to {:g} seconds at scenario time {:g}s. Positive is ahead of ego; negative is behind.'.format(value, time), time)
                if profile[1].lower() == 'gap_points':
                    return TimeHint('distance', 'Target distance · {:g} m'.format(value),
                                    'Sets the target gap to {:g} metres at scenario time {:g}s.'.format(value, time), time)
                return None  # The existing speed tooltip supplies units.

    for match in TOKEN.finditer(clause):
        if not match.start() <= cursor < match.end():
            continue
        value = float(match['value'])
        if value < 0:
            return _review('Use a non-negative scenario time and a positive duration.')
        lead = (match['lead'] or '').lower().strip(' :=')
        prefix, suffix = clause[:match.start()], clause[match.end():]
        if re.search(r'\b(?:scenario\s*[:=]?\s*|create\s+(?:a\s+)?|total\s+)$', prefix, re.I) or re.match(r'\s+scenario\b', suffix, re.I) or (lead.startswith('duration') and not prefix.strip()):
            return TimeHint('scenario_duration', 'Scenario duration · {:g} s'.format(value),
                            'The complete scenario lasts {:g} seconds from t=0.'.format(value), value)
        if lead.startswith(('over', 'for', 'taking', 'lasting', 'lasts', 'duration')) or (pedestrian and lead == 'in'):
            if value == 0:
                return _review('Specify a positive duration.')
            return TimeHint('duration', 'Duration · {:g} s'.format(value),
                            ('The crossing lasts ' if pedestrian else 'The action or alert lasts ') +
                            '{:g} seconds from its start.'.format(value))
        if lead.startswith(('until', 'till', 'end')):
            return TimeHint('end', 'End time · {:g} s'.format(value),
                            'The event or object ends at scenario time {:g}s.'.format(value), value)
        if lead.startswith(('at', 'after', 'in', 'from', 'start', 'time', 't')):
            # A local object declaration or explicit appearance verb identifies
            # its lifetime; otherwise describe a generic scenario event.
            local_prefix = re.split(r',|\band(?:\s+then)?\b', prefix, flags=re.I)[-1]
            following_actor = re.match(r'\s*,?\s*(?:the\s+|an?\s+)?(ego|vehicle_[1-6]|obstacle\b|cone\b|barrier\b)', suffix, re.I)
            appearance = bool(OBJECT.search(local_prefix) or re.search(r'\bappear(?:s|ing)?\b', local_prefix, re.I))
            if ALERT.search(local_prefix) or WARNING_REQUEST.search(local_prefix) or sensory_cue:
                appearance = False
            if following_actor:
                appearance = bool(OBJECT.fullmatch(following_actor[1]))
            if pedestrian and not following_actor and not ALERT.search(local_prefix):
                return TimeHint('start', 'Crossing starts · {:g} s'.format(value),
                                'The crossing starts {:g} seconds after the scenario starts.'.format(value), value)
            if appearance:
                return TimeHint('appearance', 'Appearance time · {:g} s'.format(value),
                                'The object appears {:g} seconds after the scenario starts.'.format(value), value)
            return TimeHint('start', 'Scenario time · {:g} s'.format(value),
                            'The event starts {:g} seconds after the scenario starts.'.format(value), value)
        return TimeHint('unspecified', 'Time value · {:g} s'.format(value),
                        'Analyze to confirm whether this is a start time, a duration, or a position reference.')
    return None
