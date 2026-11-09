"""Small, checked adapters for scene introductions and existing object types."""
import re

from .prompt_feedback import PromptInputError, missing_information, source_clause_at
from .prompt_positions import DIRECTION as POSITION_DIRECTION, extract_position, missing_position_field, partial_position_guidance

NUM = r"\d+(?:\.\d+)?"
PEDESTRIAN_NAME_RE = re.compile(r'\b(?:pedestrian|ped|person|walker)(?:[ _#-]*(\d+))?\b', re.I)
OBSTACLE_NAME_RE = re.compile(r'\bobstacle(?:[ _#-]*(\d+))?\b', re.I)
TIME = rf"({NUM})\s*(?:seconds?|secs?|s)\b"
START = re.compile(rf"\b(?:at|after)\s+{TIME}", re.I)
END = re.compile(rf"\b(?:until|end(?:s)?(?:\s+at)?|finishes at|disappears at)\s+{TIME}", re.I)
DURATION = re.compile(rf"\b(?:over|for|in|taking|lasting)\s+{TIME}", re.I)
LANE = re.compile(r"\b(left|middle|right)\s+lane\b", re.I)
DIRECTION = re.compile(r"\bfrom\s+(?:the\s+)?(left|right)(?:\s+side)?\s+to\s+(?:the\s+)?(left|right)(?:\s+side)?\b", re.I)
KINDS = r"(?:traffic\s+cone|road\s+barrier|disabled\s+vehicle|barrier|cone|barrel|debris|box|crate)"
KIND = re.compile(rf"\b({KINDS})\b", re.I)
ENTITY = re.compile(r"^(obstacle_\d+|pedestrian_\d+)\b", re.I)
INTRO = r"(?:add|place|put|include|spawn|there is|there's)"
ENTITY_INTENT = re.compile(rf"\b{INTRO}\s+(?:(?:a|an|one|another|the|two|three|four|five|six|\d+)\s+)?(?:cars?|vehicles?|pedestrians?|person|walker|obstacles?|{KINDS})\b", re.I)
COMMA_SUBJECT = rf",\s*(?=(?:{INTRO}\b|(?:obstacle|pedestrian)(?:[ _-]*\d+)?\b|person\b|walker\b))"
INTRO_PREFIX = rf"(^|[.!?;\n]\s*|,?\s+and\s+|{COMMA_SUBJECT})(?:(at\s+{NUM}\s*(?:seconds?|secs?|s)\b)\s*,?\s*)?(?:please\s+)?"
DESCRIPTION = rf"(?:{INTRO}\s+)?(?:(?:a|an|one|another|the)\s+)?(?:stationary\s+)?"
SUBJECT = rf"(vehicle_[1-6]|(?:car|vehicle)(?![ _#-]*\d)\b|obstacle(?:[ _-]*\d+)?\b|pedestrian(?:[ _-]*\d+)?\b|person\b|walker\b|{KINDS}\b)"
INTRODUCTION = re.compile(INTRO_PREFIX + DESCRIPTION + SUBJECT, re.I)


def entity_requires_checked_reader(text):
    if re.search(r'\b(?:person|walker)[ _#-]*\d+\b', text, re.I):
        return True  # These names are normalized by the checked entity reader.
    if ENTITY_INTENT.search(text):
        return True
    for match in INTRODUCTION.finditer(text):
        name = match[3].lower()
        if name.startswith('vehicle_') or name in ('car', 'vehicle'):
            if re.search(r'\b(?:a|one|another)\s+(?:car|vehicle)\b', match[0], re.I):
                return True
            continue
        # Keep existing object extension fields on their legacy path, unless
        # they use a shared natural-language position that needs validation.
        tail = re.split(r'[.!?](?=\s|$)|[;\r\n]', text[match.end():], maxsplit=1)[0]
        if re.match(r'\s*:', tail) and re.search(r'\blane\s*=', tail, re.I):
            _, position = extract_position(tail, allow_vehicle_reference=True)
            if position is None and not re.search(rf'\b{POSITION_DIRECTION}\b', tail, re.I):
                continue
        return True
    return False


def normalize_entity_introductions(text):
    # Numbered pedestrians keep their own namespace before introduction/ID
    # allocation. Mixed prose and wizard shorthand must identify the same walker.
    text = PEDESTRIAN_NAME_RE.sub(lambda m: 'pedestrian_' + m[1] if m[1] else m[0], text)
    text = re.sub(r'\b(?:of\s+)?type\s+(?=' + KINDS + r'\b)', '', text, flags=re.I)
    used = {kind: {int(m[1]) for m in re.finditer(rf"\b{kind}[ _-]*(\d+)\b", text, re.I)}
            for kind in ('vehicle', 'obstacle', 'pedestrian')}
    # An event mentioning vehicle 2 must not reserve that ID before an
    # earlier 'add another car' has had a chance to introduce vehicle 2.
    used['vehicle'] = {int(m[1]) for m in re.finditer(
        r'\bvehicle_(\d+)\b(?=\s*(?::|[.!?;\n]|$|\b(?:in|on|at|left|middle|right|is|starts|drives|travels)\b))', text, re.I)}

    def allocate(kind, clause=''):
        number = next(i for i in range(1, len(used[kind]) + 2) if i not in used[kind])
        if kind == 'vehicle' and number > 6:
            raise PromptInputError("The scenario supports six support vehicles. Remove one or reuse an existing vehicle ID; split larger scenes if necessary. / 最多六輛配角車，請刪除一輛、重用現有車號，或拆成不同場景。", clause=clause)
        used[kind].add(number)
        return f'{kind}_{number}'

    plural = re.search(rf'\b{INTRO}\s+(two|three|four|five|six|\d+)\s+(?:cars|vehicles)\b', text, re.I)
    if plural:
        clause = source_clause_at(text, plural.start())
        count = int(plural[1]) if plural[1].isdigit() else dict(zip('two three four five six'.split(), range(2, 7)))[plural[1].lower()]
        if not 1 <= count <= 6:
            raise PromptInputError('Use one to six support vehicles. Remove extra cars or split the scene. / 配角車數量請使用一至六輛，或拆分場景。', clause=clause,
                                   example='vehicle_<number> in the <lane> lane at <speed> km/h <gap> seconds <ahead of/behind> ego.')
        issues = []
        for _ in range(count):
            role = allocate('vehicle', clause)
            issues.append((role, ['initial lane? / 初始車道？', 'initial speed with units? / 初始速度與單位？',
                                  'initial gap from ego? / 相對 ego 的初始前後車距？'],
                           f'{role} in the <lane> lane at <speed> km/h <gap> seconds <ahead of/behind> ego.'))
        # One introduction owns all requested definitions. Replacing it with
        # only one vehicle would lose the others; separate unbound buttons also
        # cannot identify which text they should replace.
        raise missing_information([('vehicles',
            [f'{role}: {field}' for role, fields, _ in issues for field in fields],
            '\n'.join(example for _, _, example in issues), [], clause)])

    # Preserve boundaries and all unrecognized words. Only introductions are
    # rewritten; event references never allocate a new actor.
    # A comma can introduce an object after a vehicle action. Restrict this
    # boundary to explicit introductions/subjects so ', a barrier' can remain
    # an object's description rather than silently allocating another object.

    def replace(match):
        before, timed, name = match.groups()
        clause = source_clause_at(text, match.start(3))
        boundary = '. ' if 'and' in before.lower() or before.lstrip().startswith(',') else before
        name = name.lower()
        # 'The car' references are kept for the existing pronoun resolver.
        introduction = match[0][len(before):]
        explicit_new = re.search(rf"\b{INTRO}\b|\banother\b", introduction, re.I)
        if KIND.fullmatch(name) and not explicit_new and re.match(r"\s+is present (?:from the start|at 0s)\b", text[match.end():], re.I):
            return match[0]
        if name.startswith('vehicle_') or name in ('car', 'vehicle'):
            indefinite = re.search(r'\b(?:a|one)\s+(?:stationary\s+)?(?:car|vehicle)\b', introduction, re.I)
            if not explicit_new and not indefinite:
                return match[0]
            if re.search(r'\bstationary\b', introduction, re.I):
                raise PromptInputError('A stationary car cannot have a moving initial speed. For a parked obstruction, write: Add a disabled vehicle in the <lane> lane <distance> metres ahead. / 靜止車輛不能指定行駛速度；停放車輛可用 disabled vehicle 障礙物表示。', clause=clause,
                                       example='Add a disabled vehicle in the <lane> lane <distance> metres ahead of ego.')
            if timed and float(START.search(timed)[1]) != 0:
                raise PromptInputError(f"Vehicle spawn at {timed} is unsupported: vehicles currently start at time 0. Define its initial lane, speed and gap at time 0, then describe timed driving actions, or use a runner with delayed vehicle spawning. / 車輛目前從第 0 秒出現，不能將延遲出現改成初始車輛。\nOriginal: {match[0].strip()}", clause=clause)
            role = name if name.startswith('vehicle_') else allocate('vehicle', clause)
            return boundary + role + ' '
        kind = 'pedestrian' if name.startswith(('pedestrian', 'person', 'walker')) else 'obstacle'
        if kind == 'pedestrian' and re.search(r'\bstationary\b', introduction, re.I):
            raise PromptInputError('A stationary pedestrian cannot cross the road. Describe a crossing with direction, distance, start and duration, or remove the crossing request. / 靜止行人與橫穿動作矛盾，請說明實際行為；此輸入支援橫穿行人。', clause=clause)
        numbered = re.search(r'[_ -](\d+)$|(?<=[a-z])(\d+)$', name)
        role = f'{kind}_{next(g for g in numbered.groups() if g)}' if numbered else allocate(kind, clause)
        used[kind].add(int(role.split('_')[1]))
        type_text = name if KIND.fullmatch(name) else ''
        return boundary + role + ' ' + type_text + (' ' + timed if timed else '') + ' '

    text = INTRODUCTION.sub(replace, text)
    # State shorthand remains valid for objects as well as vehicles.
    text = re.sub(r"\b(obstacle_\d+)\s+(left|middle|right)\b(?!\s+lane)", r"\1 in the \2 lane", text, flags=re.I)
    return text


def normalize_pedestrian_shorthand(sentence):
    """Read the wizard's crossing and metre fields inside a prose scenario."""
    if re.match(r'^obstacle_\d+\b', sentence, re.I):
        sentence = re.sub(rf'\bend\s*[:=]\s*({NUM})\s*(?:seconds?|secs?|s)\b', r'until \1 seconds', sentence, flags=re.I)
        # Keep the known distance while leaving the missing vehicle explicit.
        return re.sub(rf'([:,]\s*)({NUM})\s*(?:metres?|meters?|m)\b(?=\s*(?:,|$))',
                      r'\1\2 metres ahead', sentence, flags=re.I)
    if not re.match(r'^pedestrian_\d+\b', sentence, re.I):
        return sentence
    sentence = re.sub(r'\b(?:from\s+)?(left|right)\s*(?:->|=>|→|to)\s*(left|right)\b',
                      r'from \1 to \2', sentence, flags=re.I)
    # A bare distance means ahead of ego when the crossing starts. Explicit
    # initial-position phrases keep their fixed reference; times are not gaps.
    return re.sub(rf'([:,]\s*)({NUM})\s*(?:metres?|meters?|m)\b(?=\s*(?:,|$))',
                  r'\1\2 metres ahead of ego', sentence, flags=re.I)


def _check_supplied_timing(original, role, starts, ends, durations):
    if len(starts) > 1 or len(ends) > 1 or len(durations) > 1:
        raise PromptInputError(
            f'{role}: Conflicting supplied times; specify one start and one end/duration. / 時間重複，請指定一個開始時間及一組完成時間／用時。',
            clause=original, reason='Conflicting object times / 物件時間重複', code='entity_timing')
    start = float(starts[0][1]) if starts else 0.0 if role.startswith('obstacle') else None
    if start is not None and ends and durations:
        end, duration = float(ends[0][1]), float(durations[0][1])
        if abs(end - start - duration) > 1e-6:
            raise PromptInputError(
                f'{role}: End time conflicts with duration: {start:g}s + {duration:g}s = {start + duration:g}s, not {end:g}s. / 開始時間加用時不等於完成時間，請確認要修改哪一個值。',
                clause=original, reason=f'End time conflicts with duration: {start:g} + {duration:g} = {start + duration:g}, not {end:g}', code='entity_timing')


def _entity_unrecognized(body, allow_partial=False):
    is_obstacle = body.lower().startswith('obstacle')
    if allow_partial:
        partial = partial_position_guidance(body)
        if partial:
            start, end = partial[3]
            body = body[:start] + ' ' + body[end:]
    rest = ENTITY.sub('', body)
    rest = re.sub(r'\b(?:kind|type)\s*=\s*', ' ', rest, flags=re.I)
    for pattern in (END, START, DURATION):
        rest = pattern.sub(' ', rest)
    rest = KIND.sub(' ', LANE.sub(' ', rest)) if is_obstacle else DIRECTION.sub(' ', rest)
    rest = re.sub(r"\b(?:a|an|the|one|is|in|at|of|ego|initial|position|stationary|road|obstacle|crossing|crosses|walks|walking|across|and|appears|present)\b", ' ', rest, flags=re.I)
    return re.sub(r'\s+', ' ', rest).strip(' ,:')


def entity_missing_information(sentence):
    original = sentence
    match = ENTITY.match(sentence)
    if not match:
        return None
    role = match[1].lower()
    sentence, position = extract_position(sentence)
    direction_conflict = False
    # Supplied contradictions must not disappear merely because a different
    # field is missing and the template would otherwise replace their values.
    if role.startswith('obstacle'):
        if len(KIND.findall(sentence)) > 1 or len(LANE.findall(sentence)) > 1:
            raise PromptInputError('Specify one obstacle type and one lane. / 同一障礙物請指定一個種類及一個車道。',
                                   clause=original, code='entity_conflict')
    else:
        directions = list(DIRECTION.finditer(sentence))
        direction_conflict = len(directions) > 1 or bool(directions and directions[0][1].lower() == directions[0][2].lower())
        if re.search(r'\bstationary\b', sentence, re.I):
            raise PromptInputError('A crossing pedestrian cannot remain stationary. Confirm the intended behavior. / 橫穿與保持靜止矛盾，請確認實際行為。',
                                   clause=original, code='entity_conflict')
    fields = []
    recognized = []
    needs_reference = role.startswith('obstacle') and (
        position is not None and not position.reference_explicit
        or position is None and not re.search(r'\b(?:ego|vehicle_\d+)\b', original, re.I))
    if needs_reference:
        fields.append('Reference vehicle: write ego or a defined vehicle number (for example vehicle_1). The distance is measured when this obstacle appears.')
    if position is None:
        field = missing_position_field(sentence)
        if role.startswith('pedestrian'):
            field = field.replace('distance ahead/behind initial ego', 'distance ahead/behind ego at crossing start')
        elif role.startswith('obstacle'):
            field = field.replace('distance ahead/behind initial ego', 'distance ahead/behind the reference vehicle at appearance')
        fields.append(field)
    else:
        recognized.append(f"ego's planned position at {position.value:g}s" if position.unit == 'route_time' else
                          position.phrase().replace(' ego', ' <reference vehicle>') if needs_reference else position.phrase())
    partial = partial_position_guidance(sentence) if position is None else None
    named_reference = re.search(r'\b(?:ego|vehicle_\d+)\b', original, re.I)
    default_reference = (named_reference[0] if named_reference else '<reference vehicle>') if role.startswith('obstacle') else 'ego'
    location = position.phrase() if position else partial[0] if partial else f'<distance> metres ahead of {default_reference}'
    if needs_reference:
        location = re.sub(r'\bego\b', '<reference vehicle>', location)
    if partial:
        recognized.extend([re.sub(r'\bego\b', '<reference vehicle>', value) if needs_reference else value for value in partial[2]])
    starts, ends, durations = list(START.finditer(END.sub(' ', sentence))), list(END.finditer(sentence)), list(DURATION.finditer(sentence))
    _check_supplied_timing(original, role, starts, ends, durations)
    if len(starts) == 1:
        recognized.append(f'appearance at {starts[0][1]}s')
    if role.startswith('obstacle'):
        if not KIND.search(sentence):
            fields.append('obstacle type, e.g. barrier or cone? / 障礙物種類？')
        if not LANE.search(sentence):
            fields.append('lane: left, middle or right? / 放在哪個車道？')
        kinds, lanes = list(KIND.finditer(sentence)), list(LANE.finditer(sentence))
        kind = kinds[0][1] if len(kinds) == 1 else '<type>'
        lane = lanes[0][1].lower() if len(lanes) == 1 else '<lane>'
        example = f'Obstacle {role.split("_")[1]}: a {kind} in the {lane} lane {location}'
        if len(kinds) == 1:
            recognized.append(kind)
        if len(lanes) == 1:
            recognized.append(f'{lane} lane')
        if len(starts) == 1:
            example += f' at {starts[0][1]}s'
        if len(ends) == 1:
            example += f' until {ends[0][1]}s'
            recognized.append(f'end at {ends[0][1]}s')
        if len(durations) == 1:
            example += f' for {durations[0][1]}s'
            recognized.append(f'duration {durations[0][1]}s')
        example += '.'
    else:
        if direction_conflict or not DIRECTION.search(sentence):
            fields.append('crossing direction: from left to right or right to left? / 橫穿方向？')
        if not starts:
            fields.append('crossing start time in seconds? / 第幾秒開始橫穿？')
        if not DURATION.search(sentence) and not END.search(sentence):
            fields.append('crossing duration or completion time? / 橫穿用時或完成時間？')
        direction = None if direction_conflict else DIRECTION.search(sentence)
        crossing = direction[0] if direction else 'from <side> to <side>'
        if direction:
            recognized.append(crossing)
        if len(durations) == 1:
            recognized.append(f'duration {durations[0][1]}s')
        if len(ends) == 1:
            recognized.append(f'end at {ends[0][1]}s')
        start = starts[0][1] if len(starts) == 1 else '<start>'
        lifetime = ' '.join([f'over {m[1]}s' for m in durations] + [f'until {m[1]}s' for m in ends]) or 'over <duration>s'
        example = f'Pedestrian {role.split("_")[1]} crossing {crossing} {location} at {start}s {lifetime}.'
    extra = _entity_unrecognized(sentence, allow_partial=True)
    notice = ''
    if fields and extra:
        from .prompt_feedback import replacement_omission_notice
        notice = replacement_omission_notice(extra)
        fields.append(f'Unrecognized requirement: {extra}. It is not included in the replacement; review whether the suggested object description matches your intent.')
    return (role, fields, example, recognized, original, notice) if fields else None


def entity_explicit_end(sentence):
    """Return a valid supplied lifetime's end, excluding position time gaps.

    An omitted end follows the scenario duration, so it cannot justify changing
    that duration. Reversed/conflicting intervals need their own object repair.
    """
    match = ENTITY.match(sentence)
    if not match:
        return None
    original, role = sentence, match[1].lower()
    sentence = re.sub(r'\b(?:from|at) (?:the )?start\b', 'at 0s', sentence, flags=re.I)
    sentence, _ = extract_position(sentence)
    starts = list(START.finditer(END.sub(' ', sentence)))
    ends, durations = list(END.finditer(sentence)), list(DURATION.finditer(sentence))
    _check_supplied_timing(original, role, starts, ends, durations)
    if not starts and role.startswith('pedestrian'):
        return None
    start = float(starts[0][1]) if starts else 0.0
    end = float(ends[0][1]) if ends else start + float(durations[0][1]) if durations else None
    return end if end is not None and 0 <= start < end else None


def parse_scene_entity(sentence, total, warnings):
    original = sentence
    role = ENTITY.match(sentence)[1].lower()
    is_obstacle = role.startswith('obstacle')
    sentence = re.sub(r'\b(?:from|at) (?:the )?start\b', 'at 0s', sentence, flags=re.I)
    sentence, position = extract_position(sentence)
    starts, ends, durations = list(START.finditer(END.sub(' ', sentence))), list(END.finditer(sentence)), list(DURATION.finditer(sentence))
    _check_supplied_timing(original, role, starts, ends, durations)
    if len(starts) > 1 or len(ends) > 1 or len(durations) > 1 or position is None:
        raise PromptInputError(f'{role}: Specify one distance, start and end/duration; remove conflicting values. / 請保留一組距離及起訖時間。\n{sentence}')
    start = float(starts[0][1]) if starts else 0.0
    end = float(ends[0][1]) if ends else start + float(durations[0][1]) if durations else total
    if not 0 <= start < end <= total:
        # Only the invalid times become questions. In particular, keep the
        # object's number so collision alerts still refer to the same object.
        body = DURATION.sub(' ', START.sub(' ', END.sub(' ', sentence))).strip()
        if ends:
            timing = 'at <start>s until <end>s'
            reason = (f'{role}: End time {end:g}s must be after start time {start:g}s '
                      f'and within the scenario duration ({total:g}s). Enter the intended start and end times.')
        elif durations:
            timing = f'at {start:g}s over <duration>s'
            reason = f'{role}: Duration must be greater than 0. Enter how long it should remain or cross after {start:g}s.'
        else:
            timing = 'at <start>s'
            reason = f'{role}: Start time {start:g}s must be before the scenario ends at {total:g}s. Enter an earlier start time.'
        example = f'{body} {position.phrase()} {timing}.'
        raise PromptInputError(reason, clause=original, reason=reason, code='entity_timing', example=example)
    rest = ENTITY.sub('', sentence)
    rest = re.sub(r'\b(?:kind|type)\s*=\s*', ' ', rest, flags=re.I)
    for pattern in (END, START, DURATION):
        rest = pattern.sub(' ', rest)
    if is_obstacle:
        kinds, lanes = list(KIND.finditer(rest)), list(LANE.finditer(rest))
        if len(kinds) != 1 or len(lanes) != 1:
            raise PromptInputError(f'{role}: Specify one obstacle type and one lane. / 請指定一個障礙物種類與車道。\n{sentence}')
        kind = kinds[0][1].lower().replace('traffic ', '').replace('road ', '')
        assets = {'cone': ('Cone', 'static.prop.trafficcone01'), 'barrier': ('Barrier', 'static.prop.streetbarrier'),
                  'barrel': ('Debris', 'static.prop.barrel'), 'debris': ('Debris', 'static.prop.barrel'),
                  'box': ('Debris', 'static.prop.box01'), 'crate': ('Debris', 'static.prop.box01'),
                  'disabled vehicle': ('Disabled Vehicle', 'vehicle.citroen.c3')}
        cfg = dict(kind=assets[kind][0], blueprint_id=assets[kind][1], label=kind.title(),
                   lane=lanes[0][1].title(), end_time_s=end, spawn_mode='fixed')
        cfg.update(position_reference='initial_ego' if position.initial else 'vehicle_at_spawn',
                   reference_role=position.reference_role, _source_clause=original)
        rest = KIND.sub(' ', LANE.sub(' ', rest))
        if not starts:
            warnings.append(f'{role}: no appearance time specified; present from 0s until {end:g}s.')
    else:
        if re.search(r'\bstationary\b', sentence, re.I):
            raise PromptInputError(f'{role}: A crossing pedestrian cannot remain stationary. Remove the contradictory stationary/crossing instruction. / 橫穿與保持靜止矛盾，請確認實際行為。\n{sentence}')
        directions = list(DIRECTION.finditer(rest))
        if len(directions) != 1 or directions[0][1].lower() == directions[0][2].lower():
            raise PromptInputError(f'{role}: Choose a crossing from left to right or right to left. / 請指定不同的起點與終點側。\n{sentence}')
        cfg = dict(start_side=directions[0][1].title(), end_side=directions[0][2].title(), duration_s=end-start)
        cfg['position_reference'] = 'initial_ego' if position.initial else 'ego_at_crossing'
        rest = DIRECTION.sub(' ', rest)
    rest = _entity_unrecognized(sentence)
    if rest.strip(' ,:'):
        number = role.split('_')[1]
        if is_obstacle:
            example = f'Obstacle {number}: a {kind} in the {cfg["lane"].lower()} lane {position.phrase()} at {start:g}s until {end:g}s.'
        else:
            example = f'Pedestrian {number} crossing from {cfg["start_side"].lower()} to {cfg["end_side"].lower()} {position.phrase()} at {start:g}s over {end-start:g}s.'
        reference_hint = 'a named vehicle at appearance, or explicitly the initial ego position' if is_obstacle else 'ego at crossing start, or explicitly the initial ego position'
        raise PromptInputError(f'{role}: Unsupported or ambiguous clause: {sentence}\nUnrecognized: {rest.strip()}\nSpecify a distance relative to {reference_hint} and explicit times; remove or rephrase the unsupported condition/action.',
                               clause=sentence, reason='Unsupported object description', code='entity',
                               unrecognized=rest.strip(' ,:'), example=example,
                               recognized=[position.phrase(), f'appearance at {start:g}s', f'end at {end:g}s'])
    cfg['_position_spec'] = position
    cfg.update(enabled=True, start_time_s=start, lock_time_s=0, world_locked=True)
    return role, cfg
