"""Questions and validated answers for the reader's existing template fields."""
from dataclasses import dataclass
import math
import re

from .prompt_feedback import prompt_placeholder_fields


QUESTIONS = {
    'type': 'What kind of obstacle should appear?',
    'lane': 'Which lane should it be in?',
    'destination': 'Which lane should it move into?',
    'ahead of/behind': 'Where is it relative to the ego vehicle?',
    'side': 'Which side?',
    'speed-unit': 'Which unit is the speed in?',
    'speed-action': 'Should this vehicle accelerate or brake?',
    'position-unit': 'Is this gap measured in metres or seconds?',
    'time-unit': 'Which time unit?',
    'speed': 'What is the initial speed?',
    'target': 'What speed should it reach?',
    'change': 'By how much should its speed change?',
    'source': 'What speed does it start at?',
    'converted-speed': 'What is the speed converted to km/h?',
    'duration': 'How long should the action last?',
    'start': 'When should it start?', 'end': 'When should it finish?',
    'time': 'At what time?', 'total': 'How long should the scenario last?',
    'reference': "Use the ego vehicle's planned position at what time?",
    'distance': 'What distance should separate the object and its reference?',
    'gap': 'What is the gap from the ego vehicle?',
    'rate': 'What acceleration or braking rate should it use?',
    'number': 'Which vehicle or object is this? Enter its number.',
    'vehicle': 'Which vehicle? Enter ego or a defined vehicle, e.g. v1.',
    'reference vehicle': 'Which vehicle should this obstacle be placed relative to when it appears?',
    'cue': 'Which cue should be used?', 'text': 'What should the alert message say?',
    'motion-mode': 'How should this vehicle control its speed?',
    'alert-mode': 'How should the takeover alert be presented?',
    'audio': 'Which auditory alert should be played?',
    'ttc': 'How many seconds before predicted collision should the alert start?',
    'obstacle': 'Which defined obstacle? Enter its number or name, e.g. obstacle 1.',
}
CHOICES = {
    'motion-mode': (('speed', 'Follow speed changes'), ('time_gap', 'Follow a time gap profile'),
                    ('fixed_speed', 'Keep the initial speed')),
    'type': (('cone', 'Traffic cone'), ('barrier', 'Road barrier'), ('barrel', 'Barrel'),
             ('box', 'Box'), ('disabled vehicle', 'Disabled vehicle')),
    'lane': tuple((s.lower(), s) for s in ('Left', 'Middle', 'Right')),
    'destination': tuple((s.lower(), s) for s in ('Left', 'Middle', 'Right')),
    'side': (('left', 'Left'), ('right', 'Right')),
    'ahead of/behind': (('ahead of', 'Ahead of ego'), ('behind', 'Behind ego')),
    'speed-unit': (('km/h', 'km/h'), ('mph', 'mph'), ('m/s', 'm/s')),
    'speed-action': (('accelerates', 'Accelerate'), ('brakes', 'Brake')),
    'position-unit': (('m', 'Metres (m)'), ('s', 'Time gap (s)')),
    'time-unit': (('s', 'Seconds'),),
    'cue': (('beep', 'Beep'), ('speech', 'Speech'), ('circle', 'Circle'), ('visual', 'Visual text')),
    'audio': (('beep', 'Beep'), ('speech', 'Speech — Please take over.')),
    'alert-mode': (('visual', 'Visual'), ('auditory beep', 'Auditory · Beep'),
                   ('auditory speech', 'Auditory · Speech'), ('visual and auditory beep', 'Visual + Beep'),
                   ('visual and auditory speech', 'Visual + Speech')),
}
NUMERIC = {'ttc', 'speed', 'target', 'change', 'source', 'converted-speed', 'duration', 'start', 'end',
           'time', 'total', 'reference', 'distance', 'gap', 'rate', 'number'}
UNIT = re.compile(r'\s*-?\s*(km/h|kmh|kph|mph|m/s2|m/s²|m/s|seconds?|secs?|s|metres?|meters?|m)(?![\w/])', re.I)


@dataclass(frozen=True)
class AnswerField:
    key: tuple
    start: int
    end: int
    question: str
    choices: tuple = ()
    unit: str = ''
    units: tuple = ()
    hint: str = ''
    number_kind: str = ''
    allow_ego: bool = True


def _question_context(clause, name, question, unit):
    """Describe the owning instruction, not unrelated words elsewhere in a draft."""
    from .takeover_alerts import ALERT
    from .cue_language import cue_head
    alert = bool(ALERT.search(clause) or re.match(r'\s*cue\b', clause, re.I) or cue_head(clause))
    match = re.search(r'\b(ego|(?:vehicle|car|v|obstacle|pedestrian)[ _#-]*(?:\d+|<number>))', clause, re.I)
    subject = re.sub(r'[ _#-]+', ' ', match[0]).capitalize() if match else 'This vehicle'
    subject = re.sub(r'^(?:Car|V)\s*(?=\d)', 'Vehicle ', subject)
    obstacle = subject.lower().startswith('obstacle')
    pedestrian = subject.lower().startswith('pedestrian')
    from .prompt_english import SET_SPEED
    action = ('changing speed' if '<speed-action>' in clause.lower() or re.search(SET_SPEED, clause, re.I) else
              'braking' if re.search(r'\b(?:brak\w*|slow\w*|decelerat\w*)\b', clause, re.I) else
              'accelerating' if re.search(r'\baccelerat\w*|speed\s+up', clause, re.I) else
              'crossing' if pedestrian else 'changing lanes')
    hint = ''
    if re.search(r'\btime_gap_points\s*:', clause, re.I):
        if name in ('start', 'time'):
            return (f'At what scenario time should {subject} reach this target gap?',
                    'Enter seconds after the scenario starts. The gap changes toward the target at that time.')
        if name == 'gap':
            return (f'What target time gap ahead of ego should {subject} reach?',
                    'Enter seconds of spacing ahead of ego at the specified time, e.g. 3.')
    if name in ('start', 'end', 'time'):
        if alert:
            question = 'At what scenario time should the alert {}?'.format('end' if name == 'end' else 'start')
        elif obstacle:
            question = f'At what scenario time should {subject} ' + ('be removed?' if name == 'end' else 'appear?')
        else:
            question = f'At what scenario time should {subject} ' + ('finish ' if name == 'end' else 'start ') + action + '?'
        hint = 'Enter seconds after the scenario starts: 10 means t = 10s.'
    elif name == 'duration':
        if alert:
            question = 'How long should the alert last?'
        elif obstacle:
            question = f'How long should {subject} remain in the scene?'
        else:
            question = f'How long should {subject} take for {action}?'
        hint = 'Enter only the number: 3 means it lasts 3 seconds from its start.'
    elif name == 'speed-action':
        question = f'Should {subject} accelerate or brake?'
        hint = 'Choose the speed action, then give its own start time. The lane change keeps its original timing.'
    elif name == 'motion-mode':
        question = f'How should {subject} control its speed?'
        hint = 'Choose one mode. Time gap requires a time_gap_points profile; fixed speed keeps its initial speed.'
    elif name == 'speed':
        question = f'What is the initial speed of {subject}?'
        hint = 'Enter 0 for a stationary vehicle, or a positive speed, and choose its unit, e.g. 30 mph.'
    elif name in ('target', 'source', 'change'):
        question = (f'What speed should {subject} reach?' if name == 'target' else
                    f'What speed does {subject} have when this action starts?' if name == 'source' else
                    f'By how much should {subject} change speed?')
        hint = 'Enter only the number; choose the speed unit beside the box.'
    elif name == 'lane':
        question = f'Which lane should {subject} start in?'
    elif name == 'destination':
        question = f'Which lane should {subject} move into?'
    elif name == 'ttc':
        hint = 'Live collision prediction: 7 means alert when the predicted time to collision reaches 7 seconds.'
    elif name == 'reference':
        hint = 'Enter the time for ego’s planned road position. The object’s appearance time is set separately.'
    elif name in ('distance', 'gap'):
        if unit.lower() in ('s', 'sec', 'secs', 'second', 'seconds'):
            hint = ('Enter seconds of travel from the initial ego position.' if (obstacle or pedestrian) and 'initial' in clause.lower() else
                    "Enter a time gap; it uses the reference vehicle's speed when the obstacle appears." if obstacle else
                    'Enter seconds of ego’s planned travel to locate this crossing.' if pedestrian else
                    'Enter the initial time gap in seconds; spacing is based on ego’s speed.')
        else:
            hint = 'Enter only the distance number, e.g. 30 for 30 metres.'
    elif name == 'total':
        hint = 'Enter the total scenario length in seconds, e.g. 50.'
    elif name == 'rate':
        hint = 'Enter a positive number in m/s², e.g. 2. The action determines acceleration or braking.'
    return question, hint


class AnswerValidationError(ValueError):
    def __init__(self, message, field_key):
        super().__init__(message)
        self.field_key = field_key


def _number_is_reference(clause, prefix, kind):
    if re.search(r'\b(?:takeover|take over|tor|alert|warning|cue|target(?:ing)?|hits?|collision)\b', prefix, re.I):
        return True
    if re.search(r'\b(?:ahead(?: of)?|behind|relative to)\s+(?:the\s+)?\w*[ _#-]*$', prefix, re.I):
        return True
    if kind != 'vehicle':
        return False
    from .prompt_english import SPEED_ACTION
    from .prompt_language import conversational_lane_actions
    from .natural_language_prompt import VEHICLE_EVENT
    actions = (list(SPEED_ACTION.finditer(clause)) + list(conversational_lane_actions(clause))
               + list(VEHICLE_EVENT.finditer(clause)) + list(re.finditer(r'<speed-action>', clause, re.I)))
    if not actions:
        return False
    before_action = clause[:min(action.start() for action in actions)]
    # A compact declaration may also contain a later action. Its label still
    # introduces the initial state when lane/speed precede the action.
    return not re.search(r'\b(?:left|middle|right)\b|\d\s*(?:mph|km/h|kmh|kph|m/s)\b', before_action, re.I)


def _defined_numbers(source, kind):
    """Reference examples come from declarations, never other bare references."""
    from .prompt_recovery import raw_descriptions
    role = r'(?:vehicle|car|v)' if kind == 'vehicle' else kind
    numbers = set()
    for start, end in raw_descriptions(source):
        clause = source[start:end]
        match = re.match(r'\s*(?:the\s+)?' + role + r'[ _#-]*(\d+)\b', clause, re.I)
        if match and not _number_is_reference(clause, clause[:match.start(1)], kind):
            numbers.add(int(match[1]))
    return numbers


def answer_fields(template, *, prompt=None):
    from .prompt_recovery import raw_descriptions
    from .prompt_language import normalize_prompt_characters, normalize_vehicle_language
    descriptions = list(raw_descriptions(template))
    source = normalize_prompt_characters(template if prompt is None else prompt)
    source = re.sub(r'"(?:\\.|[^"\\])*"', '', source)
    # Object normalization assigns IDs to unnamed objects. A deliberately
    # unfilled label must not reserve a phantom number in the suggestions.
    named_source = '\n'.join(source[start:end] for start, end in raw_descriptions(source)
                             if not re.search(r'<number>', source[start:end], re.I))
    try:
        source = normalize_vehicle_language(named_source)
    except ValueError:
        pass  # Guidance must remain available for incomplete/invalid drafts.
    fields, occurrences = [], {}
    for match in prompt_placeholder_fields(template):
        name = match[1].lower()
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        question = QUESTIONS[name]
        hint, number_kind = '', ''
        left, right = next(((left, right) for left, right in descriptions if left <= match.start() < right), (0, len(template)))
        clause = normalize_prompt_characters(template[left:right])
        prefix = normalize_prompt_characters(template[left:match.start()])
        suffix = UNIT.match(template, match.end()) if name in NUMERIC else None
        unit = suffix[1] if suffix else ''
        if unit.lower() in ('km/h', 'kmh', 'kph', 'mph', 'm/s'):
            unit = {'kmh': 'km/h', 'kph': 'km/h'}.get(unit.lower(), unit.lower())
        question, hint = _question_context(clause, name, question, unit)
        from .takeover_alerts import ALERT
        allow_ego = bool(re.match(r'\s*cue\b', clause, re.I)) or not bool(ALERT.search(clause))
        if name == 'number':
            actor = re.search(r'\b(vehicle|car|v|npc|obstacle|pedestrian)[ _#-]*(?:(?:number|no\.?)\s*)?$', prefix, re.I)
            if actor:
                token = actor[0] + match[0]
                if actor[1].lower() in ('vehicle', 'car', 'v', 'npc'):
                    from .prompt_language import NUMBERED_VEHICLE_RE
                    used = {int(m[1]) for m in NUMBERED_VEHICLE_RE.finditer(source)}
                    unused = next((number for number in range(1, 7) if number not in used), None)
                    number_kind = 'vehicle'
                    question = f'Which vehicle is {token}? Enter its number (1 to 6).'
                    lines = ['This is one vehicle\'s label, not the number of vehicles.',
                             '• Enter only a whole number from 1 to 6.']
                    if used:
                        lines.append('• Numbers already used: ' + ', '.join(str(n) for n in sorted(used)) + '. Use a different number for a new vehicle.')
                    if unused is not None:
                        lines.append(f'• Example: enter {unused} to change {token} to vehicle_{unused}.')
                    else:
                        lines.append('• All six numbers are in use. For an existing vehicle, edit its original definition or name it in an action.')
                    hint = '\n'.join(lines)
                else:
                    number_kind = actor[1].lower()
                    used = {int(m[1]) for m in re.finditer(r'\b' + number_kind + r'[ _#-]*(\d+)\b', source, re.I)}
                    unused = next(n for n in range(1, len(used) + 2) if n not in used)
                    question = f'Which {number_kind} is {token}? Enter its number.'
                    hint = (f'This number labels one {number_kind} so you can refer to it later.\n'
                            f'• Enter only a positive whole number.\n')
                    if used:
                        hint += '• Numbers already used: ' + ', '.join(str(n) for n in sorted(used)) + '. Use a different number for a new object.\n'
                    hint += f'• Example: enter {unused} to change {token} to {number_kind}_{unused}.'
                if _number_is_reference(clause, prefix, number_kind):
                    question = f'Which existing {number_kind} does this description refer to? Enter its number.'
                    defined = _defined_numbers(source, number_kind)
                    hint = f'Use the number of an existing {number_kind} from its initial description.'
                    if defined:
                        hint += '\n• Defined numbers: ' + ', '.join(str(n) for n in sorted(defined)) + '.'
                        number = min(defined)
                        hint += f'\n• Example: enter {number} to refer to {number_kind}_{number}.'
                    else:
                        hint += f'\n• No numbered {number_kind} initial description was found. Add its initial description first, then use that same number here.'
        if name in ('vehicle', 'obstacle', 'reference vehicle'):
            kind = 'vehicle' if name == 'reference vehicle' else name
            names = [f'{kind}_{number}' for number in sorted(_defined_numbers(source, kind))]
            if kind == 'vehicle':
                if not allow_ego:
                    question = 'Which defined target vehicle should be circled? Enter v1 to v6.'
                hint = ('Enter v1 to v6; ego cannot be circled by this takeover alert.' if not allow_ego else
                        'Enter ego or a vehicle label, e.g. v1 for vehicle_1.')
                if name == 'reference vehicle':
                    hint += '\nThe distance is measured from this vehicle when the obstacle appears; the obstacle then stays fixed on the road.'
            else:
                circle = re.search(r'\b(?:target(?:ing)?|around|circle(?:\s+around)?|highlight(?:ing)?|on)\s*=?\s*(?:the\s+)?$', prefix, re.I)
                question = ('Which defined obstacle should be circled? Enter its number or label.' if circle else question)
                hint = ('Enter the obstacle label, e.g. obstacle 1. This chooses the circle target.' if circle else
                        'Enter the obstacle label, e.g. obstacle 1. This chooses the collision obstacle.')
            hint += ('\n• Defined in this prompt: ' + ', '.join(names) + '.' if names else
                      '\n• You can use ego. Define another vehicle before choosing its label.' if kind == 'vehicle' and allow_ego else
                     '\n• No numbered ' + name + ' initial description was found. Add its initial description first.')
        if name == 'side':
            destination = re.search(r'\bto\s*$', template[:match.start()], re.I)
            question = 'Which side should it cross to?' if destination else 'Which side should it start from?'
        if name in ('distance', 'gap'):
            direction = re.match(r'\s*(?:metres?|meters?|m|seconds?|secs?|s)?\s*(ahead(?: of)?|behind)', template[match.end():], re.I)
            if direction:
                relation = 'behind' if direction[1].lower() == 'behind' else 'ahead of'
                obstacle = bool(re.match(r'\s*obstacle[ _-]*', clause, re.I))
                pedestrian = bool(re.match(r'\s*pedestrian[ _-]*', clause, re.I))
                reference = ('the initial ego position' if 'initial' in clause.lower() else
                             'the reference vehicle when this obstacle appears' if obstacle else
                             'ego when crossing starts' if pedestrian else 'the ego vehicle')
                question = f'What is the gap {relation} {reference}?'
        if name == 'lane' and re.search(r'\b(?:to|into)\s+(?:the\s+)?$', template[:match.start()], re.I):
            question = 'Which lane should it move into?'
        elif name == 'lane' and re.search(r'\bfrom\s+(?:the\s+)?$', prefix, re.I):
            question = 'Which lane is this vehicle in just before this lane change?'
            hint = 'Use its lane after any earlier lane changes. The error message above shows the expected lane.'
        units = ('km/h', 'mph', 'm/s') if unit in ('km/h', 'kmh', 'kph', 'mph', 'm/s') and name != 'converted-speed' else (unit,) if unit else ()
        choices = CHOICES.get(name, ())
        if name == 'ahead of/behind' and re.match(r'\s*obstacle[ _-]*', clause, re.I):
            question = 'Should the obstacle appear ahead of or behind the reference vehicle?'
            choices = (('ahead of', 'Ahead of reference'), ('behind', 'Behind reference'))
        fields.append(AnswerField((name, occurrence), match.start(), suffix.end() if suffix else match.end(),
                                  question, choices, unit, units, hint, number_kind, allow_ego))
    return fields


def fill_answers(template, answers, *, fields=None):
    fields = answer_fields(template) if fields is None else fields
    if set(answers) != {field.key for field in fields}:
        raise ValueError('Complete each question below before updating the description.')
    edits = []
    # Validate in reading order, then splice backwards to preserve offsets.
    for field in fields:
        try:
            value = _filled_value(template, field, answers[field.key])
        except ValueError as exc:
            raise AnswerValidationError(str(exc), field.key) from exc
        edits.append((field.start, field.end, value))
    # A complete form can validate an interval before changing the draft.
    # Compare only fields in the same instruction, never different objects.
    from .prompt_recovery import raw_descriptions
    for left, right in raw_descriptions(template):
        local = [field for field in fields if left <= field.start < right]
        starts = [field for field in local if field.key[0] == 'start']
        if len(starts) != 1:
            continue
        start = float(answers[starts[0].key][0])
        for field in local:
            if field.key[0] == 'end' and float(answers[field.key][0]) <= start:
                raise AnswerValidationError(
                    f'End time must be after start time ({start:g}s). Enter an end time greater than {start:g}s.', field.key)
    result = template
    for start, end, value in reversed(edits):
        result = result[:start] + value + result[end:]
    return result


def _filled_value(template, field, answer):
    value, unit = answer
    value = value.strip()
    name = field.key[0]
    if not value:
        raise ValueError(field.question + ' Enter a value or select an option.')
    if field.choices:
        if value not in dict(field.choices):
            raise ValueError(field.question + ' Select one of the options shown.')
    elif name in NUMERIC:
        if not re.fullmatch(r'(?:\d+(?:\.\d+)?|\.\d+)', value):
            raise ValueError(field.question + ' Enter only the number (0 or greater), e.g. 3. Use the unit shown beside the box.')
        if not math.isfinite(float(value)):
            raise ValueError(field.question + ' Enter a finite number.')
        if value.startswith('.'):
            value = '0' + value
        if name in ('ttc', 'duration', 'total', 'rate', 'number') and float(value) <= 0:
            raise ValueError(field.question + ' Enter a number greater than 0.')
        if name == 'number' and not value.isdigit():
            raise ValueError('Enter a whole number for this ' + (field.number_kind or 'vehicle or object') + ', e.g. 3. Decimals are not valid numbers for these labels.')
        if name == 'number' and field.number_kind == 'vehicle' and not 1 <= int(value) <= 6:
            raise ValueError('Enter a vehicle number from 1 to 6, e.g. 3 for vehicle_3.')
    elif name == 'obstacle':
        match = re.fullmatch(r'(?:obstacle[ _#-]*)?([1-9]\d*)', value, re.I)
        if not match:
            raise ValueError('Enter a defined obstacle, e.g. obstacle 1.')
        value = 'obstacle_' + match[1]
    elif name in ('vehicle', 'reference vehicle'):
        valid = re.fullmatch(r'ego|v[1-6]|vehicle[_ ]?[1-6]', value, re.I)
        if not valid or (value.lower() == 'ego' and not field.allow_ego):
            raise ValueError('Enter a defined vehicle, v1 to v6; ego cannot be this takeover alert target.' if not field.allow_ego else
                             'Enter ego or a defined vehicle, e.g. v1.')
        value = 'ego' if value.lower() == 'ego' else 'vehicle_' + value[-1]
    elif name == 'text':
        value = '"' + value.strip('"').replace('"', '\\"') + '"'
    if field.unit:
        if unit not in field.units:
            raise ValueError(field.question + ' Select a unit from the list.')
        old = template[field.start:field.end]
        token_end = old.index('>') + 1
        value += old[token_end:] if unit == field.unit else ' ' + unit
    return value
