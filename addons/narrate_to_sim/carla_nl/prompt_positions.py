"""Shared position grammar and meaning for vehicles, obstacles and pedestrians.

Extract locations before reading event times. Keep conventional vehicle
headway distinct from an explicitly named position on ego's planned route.
"""
from dataclasses import dataclass, replace
import re

from .prompt_feedback import PromptInputError
from .prompt_speed_events import planned_distance_m
from .prompt_english import normalize_english_quantities

NUM = r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)'
SECONDS = r'(?:seconds?|secs?|s)'
UNIT = r'(?:seconds?|secs?|s|kilometres?|kilometers?|km|metres?|meters?|miles?|mi|feet|foot|ft|m)'
FORWARD_DIRECTION = r'(?:in\s+(?:the\s+)?front|(?:at|to)\s+the\s+front|ahead|front)'
REAR_DIRECTION = r'(?:in\s+(?:the\s+)?(?:back|rear)|(?:at|to)\s+(?:the\s+)?(?:back|rear)|behind|back|rearward|rear)'
DIRECTION = rf'(?:{FORWARD_DIRECTION}|{REAR_DIRECTION})'
QUANTITY = rf'(?P<value>{NUM})\s*-?\s*(?P<unit>{UNIT})(?![\w/])'
REFERENCE = (r'(?:the\s+)?(?:(?P<initial>initial|starting)\s+)?'
             r'(?P<actor>ego(?:\s+vehicle)?(?:\x27s)?|me|my\s+(?:car|vehicle)|vehicle_\d+)'
             r'(?:\s+(?P<post_initial>initial|starting))?'
             r'(?:\s+(?:position|location|point))?\b')
OPTIONAL_REFERENCE = rf'(?:\s+(?:(?:of|from|relative\s+to)\s+)?{REFERENCE})?'
OFFSET_PATTERNS = [
    re.compile(rf'(?<![\w.])(?<!\d,){QUANTITY}\s+(?:(?:gap|headway|distance)\s+)?(?P<direction>{DIRECTION})\b{OPTIONAL_REFERENCE}', re.I),
    re.compile(rf'\b(?P<direction>{DIRECTION})\s*(?:=|:|by)?\s*{QUANTITY}{OPTIONAL_REFERENCE}', re.I),
    re.compile(rf'\b(?P<direction>{DIRECTION})\s+(?:(?:of|from)\s+)?{REFERENCE}\s+(?:by\s+)?{QUANTITY}', re.I),
]
TIME_VALUE = rf'({NUM})\s*-?\s*{SECONDS}\b'
EGO_POSITION = re.compile(
    rf'\b(?:at\s+)?(?:the\s+)?(?:position|location|point|spot)\s+(?:that\s+)?ego\s+(?:(?:will|would)\s+)?reach(?:es)?\s+(?:at|after)\s+{TIME_VALUE}'
    rf"|\b(?:at\s+ego(?:'s)?|ego's)\s+(?:position|location)\s+(?:at|after)\s+{TIME_VALUE}"
    rf'|\bwhere\s+ego\s+(?:(?:will|would)\s+be|is)\s+(?:at|after)\s+{TIME_VALUE}'
    rf"|\b(?:at\s+)?ego(?:'s)?\s+{TIME_VALUE}\s+(?:position|location)", re.I)
EGO_POSITION_INTENT = re.compile(r"\b(?:at\s+ego(?:'s)?|ego's)\s+(?:position|location)\b|\b(?:position|location|point|spot)\s+(?:that\s+)?ego\b|\bwhere\s+ego\b", re.I)


@dataclass(frozen=True)
class Position:
    value: float
    unit: str
    sign: int = 1
    initial: bool = False
    reference_role: str = 'ego'
    reference_explicit: bool = False

    def phrase(self):
        if self.unit == 'route_time':
            return f'at the position ego reaches at {self.value:.15g}s'
        direction = 'ahead of' if self.sign > 0 else 'behind'
        reference = 'the initial ego position' if self.initial else self.reference_role
        unit = 'seconds' if self.unit == 'seconds' else 'metres'
        return f'{self.value:.15g} {unit} {direction} {reference}'


def extract_position(sentence, *, allow_vehicle_reference=False):
    """Return the unconsumed clause and at most one checked position."""
    allow_vehicle_reference = allow_vehicle_reference or bool(re.match(r'\s*obstacle[ _-]*\d+\b', sentence, re.I))
    matches = []
    for match in EGO_POSITION.finditer(sentence):
        value = float(next(g for g in match.groups() if g is not None).replace(',', ''))
        matches.append((match.start(), match.end(), Position(value, 'route_time', initial=True, reference_explicit=True)))
    for pattern in OFFSET_PATTERNS:
        for match in pattern.finditer(sentence):
            if any(start <= match.start() and end >= match.end() for start, end, _ in matches):
                continue
            fields = match.groupdict()
            if re.search(r'[-+]\s*$', sentence[:match.start()]):
                raise PromptInputError('Use an unsigned position amount and specify ahead or behind. / 距離請填非負數，再用 ahead 或 behind 指定前後方向。', clause=sentence)
            if (pattern is OFFSET_PATTERNS[0] and re.fullmatch(SECONDS, fields['unit'], re.I)
                    and re.search(r'\b(?:at|after)\s*$|\b(?:t|time)\s*=\s*$', sentence[:match.start()], re.I)):
                # 'obstacle at 10s ahead ...' explicitly states appearance;
                # its missing location must not be invented from that clock.
                continue
            actor = (fields.get('actor') or 'ego').lower().removesuffix("'s")
            actor = re.sub(r'\s+vehicle$', '', actor)
            if actor in ('me', 'my car', 'my'):
                actor = 'ego'
            if actor != 'ego' and not allow_vehicle_reference:
                raise PromptInputError('Position reference must currently be relative to ego. Specify a distance from ego or its planned position; a different vehicle reference cannot be silently treated as ego. / 目前位置參考對象須為 ego；請換算為相對 ego 的位置，不能將其他車輛當成 ego。', clause=sentence)
            unit = 'seconds' if re.fullmatch(SECONDS, fields['unit'], re.I) else 'metres'
            source_unit = fields['unit'].lower()
            scale = (1000 if source_unit.startswith(('km', 'kilomet')) else
                     1609.344 if source_unit.startswith('mi') else
                     .3048 if source_unit in ('feet', 'foot', 'ft') else 1)
            direction = ' '.join(fields['direction'].lower().split())
            sign = -1 if re.fullmatch(REAR_DIRECTION, direction, re.I) else 1
            initial = bool(fields.get('initial') or fields.get('post_initial'))
            if initial and actor != 'ego':
                raise PromptInputError('Use the named vehicle at obstacle appearance, or explicitly the initial ego position.', clause=sentence)
            spec = Position(float(fields['value'].replace(',', '')) * scale, unit, sign, initial,
                            reference_role=actor, reference_explicit=bool(fields.get('actor')))
            matches.append((match.start(), match.end(), spec))
    if len(matches) > 1:
        raise PromptInputError('Specify one position per object: use a distance, a seconds offset, or one ego route-time reference. Remove the competing position. / 每個對象只指定一組位置；請保留公尺距離、秒數距離或 ego 路線參考時間其中一項。', clause=sentence)
    if not matches:
        return sentence, None
    start, end, spec = matches[0]
    # A dangling connector means an unrecognized reference, not implicit ego.
    tail = sentence[end:]
    # A position can be followed by another field or timing/action clause.
    # Any other adjacent words are an unconsumed reference, not implicit ego.
    continuation = r'(?:at|after|in|on|for|over|until|lasting|and|while|then|is|appears|present|end|duration|lane|speed|t|time|with|must|should|shall|will|can|could|may|might|does|do|did|not|never|if|unless|when)\b'
    if (re.match(r'\s+(?:of|from|relative\s+to)\b', tail, re.I)
            or (re.match(r'\s+[a-z]', tail, re.I) and not re.match(r'\s+' + continuation, tail, re.I))):
        raise PromptInputError('Position reference is missing or unsupported. Name ego, or the initial ego position. / 位置參考對象缺漏或未支援，請指定 ego 或 ego 初始位置。', clause=sentence)
    return sentence[:start] + ' ' + sentence[end:], spec


def extract_ego_vehicle_relation(sentence):
    """Read ego's inverse gap to a named car, keeping its other words checked."""
    body = re.sub(r'^and\s+', '', sentence, flags=re.I)
    if not re.match(r'(?:the\s+)?ego\b', body, re.I):
        return None
    for pattern in OFFSET_PATTERNS:
        for match in pattern.finditer(body):
            actor = match.groupdict().get('actor') or ''
            if not re.fullmatch(r'vehicle_[1-6]', actor, re.I):
                continue
            # Only the named reference is changed for the shared extractor.
            # It still checks units, competing positions, signs and trailing words.
            start, end = match.span('actor')
            translated = body[:start] + 'ego' + body[end:]
            try:
                rest, position = extract_position(translated)
            except PromptInputError as exc:
                exc.clause = sentence
                raise
            if position is None:
                return None
            if position.initial:
                raise PromptInputError('Specify the initial gap between ego and the named vehicle, without a route-position reference.', clause=sentence)
            return rest.strip(), actor.lower(), replace(position, sign=-position.sign)
    return None


def missing_position_field(sentence):
    partial = partial_position_guidance(sentence)
    if partial:
        return partial[1]
    if EGO_POSITION_INTENT.search(sentence):
        return 'ego position reference time with units? / 放在 ego 按計畫行駛到第幾秒的位置？請附時間單位。'
    if re.search(rf'\b{NUM}\s+(?!{SECONDS}\b)(?:\w+\s+)?{DIRECTION}\b', sentence, re.I):
        return 'position amount and units (seconds or metres)? / 位置數值與單位？可用秒或公尺。'
    return "distance ahead/behind initial ego (m or seconds), or ego's position at <time>s? / 距 ego 前後幾公尺或幾秒，或 ego 按計畫行駛到第幾秒的位置？"


def partial_position_guidance(sentence):
    """Preserve a supplied amount/direction without assigning a missing unit."""
    obstacle = bool(re.match(r'\s*obstacle[ _-]*\d+\b', sentence, re.I))

    def reference_for(fields):
        if fields.get('initial') or fields.get('post_initial'):
            return 'the initial ego position'
        actor = (fields.get('actor') or 'ego').lower()
        return actor if actor.startswith('vehicle_') else 'ego'
    route = EGO_POSITION_INTENT.search(sentence)
    if route:
        value = re.search(rf'\b(?:at|after)\s+({NUM})(?![\d.])', sentence[route.end():], re.I)
        if value:
            end = route.end() + value.end()
            return (f'at the position ego reaches at {value[1]} <time-unit>',
                    f'ego position reference time units for {value[1]}: s or seconds? / 已知位置參考秒數 {value[1]}，請補 s 或 seconds。',
                    [f'ego position reference value {value[1]} (time unit needed)'], (route.start(), end))
        return ('at the position ego reaches at <reference>s',
                'ego position reference time with units? / ego 行駛到第幾秒的位置？',
                ['planned ego position reference'], route.span())
    amount_only = rf'(?P<value>{NUM})(?![\w.])'
    for pattern in OFFSET_PATTERNS:
        match = re.search(pattern.pattern.replace(QUANTITY, amount_only), sentence, re.I)
        if not match:
            continue
        fields = match.groupdict()
        actor = (fields.get('actor') or 'ego').lower()
        if actor.startswith('vehicle_') and not obstacle:
            raise PromptInputError('Position reference must currently be relative to ego; convert the reference before completing the units.', clause=sentence)
        direction = 'behind' if re.fullmatch(REAR_DIRECTION, fields['direction'], re.I) else 'ahead of'
        reference = reference_for(fields)
        value = fields['value']
        return (f'{value} <position-unit> {direction} {reference}',
                f'position units for {value} {direction}: seconds or metres? / 已知數值 {value} 與前後方向，請補單位（s 或 m）。',
                [f'{value} {direction} {reference} (unit needed)'], match.span())
    for match in re.finditer(rf'(?<![\w.]){QUANTITY}(?:\s+(?:from|relative\s+to)\s+{REFERENCE})?', sentence, re.I):
        if re.search(r'\b(?:at|after|over|for|within|until|in|from|lasting|taking|takes|lasts|duration)\s*$', sentence[:match.start()], re.I):
            continue
        if (match.groupdict().get('actor') or 'ego').startswith('vehicle_') and not obstacle:
            raise PromptInputError('Position reference must currently be relative to ego; convert the reference before choosing a direction.', clause=sentence)
        quantity = sentence[match.start():match.end('unit')]
        reference = reference_for(match.groupdict())
        return (f'{quantity} <ahead of/behind> {reference}',
                f'direction for {quantity}: ahead of or behind {reference}? / 已知 {quantity}，請補前方（ahead of）或後方（behind）。',
                [quantity + ' (direction needed)'], match.span())
    direction_only = re.search(rf'\b(?P<direction>{DIRECTION})\b{OPTIONAL_REFERENCE}', sentence, re.I)
    if direction_only:
        fields = direction_only.groupdict()
        if (fields.get('actor') or 'ego').startswith('vehicle_') and not obstacle:
            raise PromptInputError('Position reference must currently be relative to ego; convert the reference before completing the distance.', clause=sentence)
        direction = 'behind' if re.fullmatch(REAR_DIRECTION, fields['direction'], re.I) else 'ahead of'
        reference = reference_for(fields)
        return (f'<distance> metres {direction} {reference}',
                f'distance {direction} {reference} with units (m or seconds)? / 已知前後方向，請補距離數值與單位。',
                [f'{direction} {reference} (distance needed)'], direction_only.span())
    return None


def position_requires_checked_reader(text):
    """Preserve legacy extension DSL; route new position meanings for checking."""
    if EGO_POSITION_INTENT.search(text) or EGO_POSITION.search(text):
        return True
    for pattern in OFFSET_PATTERNS:
        for match in pattern.finditer(text):
            if (match.groupdict().get('initial') or match.groupdict().get('post_initial')
                    or not re.fullmatch(SECONDS, match['unit'], re.I)):
                return True
    for clause in re.split(r'[.!?](?=\s|$)|[;\r\n]+', text):
        if re.match(r'\s*vehicle_[1-6]\s*:', clause, re.I) and re.search(rf'\b{DIRECTION}\b', clause, re.I):
            _, position = extract_position(clause)
            if position is None:
                return True
    return False


def normalize_initial_position_clauses(text):
    """Validate structured initial states before legacy defaults can hide gaps.

    Ordinary seconds offsets keep the legacy reader and its extension fields.
    New road-position meanings go through the checked reader unchanged.
    Quoted speech and timed profile/event clauses are not initial declarations.
    """
    parts = re.split(r'("(?:\\.|[^"\\])*"|[.!?](?=\s|$)|[;\r\n]+)', text)
    for index in range(0, len(parts), 2):
        clause = parts[index]
        if not re.match(r'\s*vehicle_[1-6]\s*:', clause, re.I):
            continue
        clause = normalize_english_quantities(clause)
        rest, position = extract_position(clause)
        if position is not None and re.search(rf'\b{DIRECTION}\b', rest, re.I):
            raise PromptInputError('Specify one position with a value, units and direction; remove or complete the extra position clause. / 請指定一組完整的位置數值、單位及前後方向，刪除或補齊多餘的位置敘述。')
        if position is None or position.initial or position.unit != 'seconds':
            continue
        direction = 'ahead' if position.sign > 0 else 'behind'
        parts[index] = rest.rstrip(' ,') + f', {direction}={position.value:.15g}s'
    return ''.join(parts)


def resolve_positions(objects, ego, total, warnings, *, vehicles=False, reference_vehicles=None):
    for role, cfg in objects.items():
        spec = cfg.pop('_position_spec', None)
        source = cfg.pop('_source_clause', '')
        if spec is None:
            continue
        if cfg.get('position_reference') == 'vehicle_at_spawn':
            from .prompt_feedback import missing_information
            from .vehicle_motion import planned_vehicle_state
            reference = spec.reference_role
            defined = reference_vehicles or {'ego': ego}
            if reference not in defined or not defined[reference].get('enabled', reference == 'ego'):
                names = ', '.join(name for name, value in defined.items() if value.get('enabled', name == 'ego'))
                example = (f'Obstacle {role.split("_")[-1]}: a {cfg["label"].lower()} in the {cfg["lane"].lower()} lane '
                           f'{spec.value:g} {"seconds" if spec.unit == "seconds" else "metres"} '
                           f'{"ahead of" if spec.sign > 0 else "behind"} <reference vehicle> '
                           f'at {cfg["start_time_s"]:g}s until {cfg["end_time_s"]:g}s.')
                raise missing_information([(role, [f'Reference vehicle {reference} is not defined. Choose {names}, or define {reference} first.'],
                                            example, [spec.phrase()], source)])
            cfg['reference_role'] = reference
            cfg['time_gap_s'] = spec.sign * spec.value if spec.unit == 'seconds' else None
            _, speed = planned_vehicle_state(defined, reference, cfg['start_time_s'], ego['speed_kmh'])
            cfg['gap_m'] = spec.sign * (spec.value * speed / 3.6 if spec.unit == 'seconds' else spec.value)
            warnings.append(f'{role}: offset {spec.phrase()} is measured when it appears at {cfg["start_time_s"]:g}s; it then stays fixed on the road.')
            continue
        # Ordinary following gaps remain initial bumper/headway quantities.
        # Naming the initial position explicitly instead denotes a road point.
        headway = vehicles and not spec.initial and spec.unit != 'route_time'
        if headway:
            gap_s = spec.value if spec.unit == 'seconds' else spec.value / (ego['speed_kmh'] / 3.6)
            cfg.update(initial_time_gap_s=spec.sign * gap_s, initial_gap_m=0)
            continue
        if spec.unit != 'metres' and not 0 <= spec.value <= total:
            raise PromptInputError(f'{role}: Position reference time must be between 0s and scenario duration {total:g}s. Extend the scenario or revise the reference time. / 位置參考時間須介於 0 與 {total:g} 秒；請延長場景或修改秒數。')
        distance = spec.sign * (spec.value if spec.unit == 'metres' else planned_distance_m(ego, spec.value))
        cfg['initial_gap_m' if vehicles else 'gap_m'] = distance
        if vehicles:
            cfg['initial_time_gap_s'] = None
        if cfg.get('position_reference') == 'ego_at_crossing':
            warnings.append(f'{role}: crossing position is {distance:g}m relative to ego when the crossing starts at {cfg["start_time_s"]:g}s, then stays fixed on the road.')
        elif spec.unit == 'metres':
            warnings.append(f'{role}: initial location is measured from the initial ego position. / 初始位置以 ego 初始位置為基準。')
        else:
            warnings.append(f"{role}: planned position {spec.phrase()} ({spec.value:g}s) = {distance:g}m from the initial ego position, including ego speed changes. The specified lane is independent; manual driving does not relocate this position. / 秒數位置按 ego 速度計畫換算為 {distance:g} 公尺；車道獨立指定，接管駕駛不會改動位置。")
