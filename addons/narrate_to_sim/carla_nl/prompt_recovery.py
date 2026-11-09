"""Parser-independent repair cards for recovering from authoring failures."""
from difflib import SequenceMatcher
import html
import re
import unicodedata

from .prompt_repairs import PromptRepair, _map_source_range


def _scenario_recovery_wording(text):
    """Keep an explicit header duration without depending on scenario parsing."""
    from .prompt_english import normalize_english_phrases
    from .prompt_language import SCENARIO_DURATION_HEADER

    try:
        header = normalize_english_phrases(text)
    except ValueError:
        return 'Create a <total>-second scenario.'
    header = SCENARIO_DURATION_HEADER.sub(
        lambda match: 'Create a {}-second scenario'.format(match[1]), header)
    # Match the entire header: a second value, event time, negative value or
    # unclear quantity must not become the scenario length by accident.
    match = re.fullmatch(
        r'\s*(?:(?:create|make|use)\s+)?(?:an?\s+)?'
        r'(\d+(?:\.\d+)?)\s*[- ]\s*(?:seconds?|secs?|s)\s+'
        r'(?:(Town\d+(?:_Opt)?)\s+)?scenario\s*\.?\s*', header, re.I)
    if match and float(match[1]) > 0:
        map_name = re.sub(r'(?i)town', 'Town', match[2] or '')
        map_name = re.sub(r'(?i)_opt', '_Opt', map_name)
        return 'Create a {}-second {}scenario.'.format(match[1], map_name + ' ' if map_name else '')
    return 'Create a <total>-second scenario.'


def raw_descriptions(prompt):
    # Mask payload punctuation without changing editor offsets.
    mask = re.sub(r'"(?:\\.|[^"\\])*"|&(?:#\w+|\w+);', lambda m: ' ' * len(m[0]), prompt)
    mask = re.sub(r'<br\s*/?>', lambda m: '\n' * len(m[0]), mask, flags=re.I)
    subject = r'(?:at\s+\d|ego\b|(?:vehicle|vehilce|car|obstacle)(?:\b|_)|(?:ped(?:estrian)?|person|walker)(?:[ _#-]*\d+)?\b|v\d\b|add\b)'
    action = r'(?:brak\w*|slow\w*|decelerat\w*|accelerat\w*|chang\w*|turn\w*|merg\w*|switch\w*|mov\w*|go\w*)\b'
    boundary = re.compile(r'[.!?](?=\s|$)|[。！？;\r\n]+|(?:[,，]\s*(?:(?:and\s+)?then\s+)?|\s+(?:and\s+then|and|then|while)\s+)(?=' + subject + '|' + action + ')', re.I)
    left = 0
    for right, following in [(m.start(), m.end()) for m in boundary.finditer(mask)] + [(len(prompt), len(prompt))]:
        if right < len(prompt) and prompt[right] in ',，' and re.fullmatch(
                r'\s*(?:at|after)\s+(?:\d+(?:\.\d+)?|<(?:start|time)>)\s*(?:seconds?|secs?|s)\s*', mask[left:right], re.I):
            continue  # "At 10s, ego ..." is one instruction, not two repairs.
        start, end = left, right
        while start < end and prompt[start].isspace():
            start += 1
        while end > start and prompt[end - 1].isspace():
            end -= 1
        if start < end:
            yield start, end
        left = following


def recovery_wording(source):
    """Retain only explicit simple facts; missing or unclear facts stay fields."""
    text = unicodedata.normalize('NFKC', html.unescape(source)).lower()
    text = text.replace('vehilce', 'vehicle').replace('trun', 'turn')
    if not text.strip():
        return 'Create a <total>-second scenario.\nEgo in the <lane> lane at <speed> km/h.'
    # Classify the instruction before objects mentioned by its trigger or
    # literal speech. A takeover warning is never a new obstacle/vehicle.
    from .takeover_alerts import ALERT, WARNING_REQUEST, CONVERSATIONAL_ALERT, _parse_alert
    from .cue_language import cue_head
    unquoted = re.sub(r'"(?:\\.|[^"\\])*"', ' ', text)
    cue = re.match(r'\s*cue\b', unquoted) or cue_head(source)
    if cue or ALERT.search(unquoted) or WARNING_REQUEST.search(unquoted) or CONVERSATIONAL_ALERT.search(unquoted):
        try:
            if cue:
                from .standalone_cues import split_cue_lines
                split_cue_lines(source)
            else:
                _parse_alert(source)
            return source  # Valid alerts need no changes if another reader failed.
        except ValueError as error:
            if getattr(error, 'example', ''):
                return error.example
        except Exception:
            pass  # Recovery remains usable even if the alert reader itself fails.
        mode = 'visual' if re.search(r'\bvisual\b|\bcircle\b', unquoted) else 'auditory'
        if mode == 'auditory':
            mode += ' beep' if re.search(r'\bbeep\b', unquoted) else ' speech' if re.search(r'\bspeech\b', unquoted) else ' <audio>'
        timing = ('<ttc>s before ego hits <obstacle>' if re.search(r'\bttc\b|\bbefore\b', unquoted)
                  else 'at <start>s')
        target = ' targeting <vehicle>' if mode == 'visual' else ''
        return f'Takeover alert {mode} {timing} for <duration>s{target}.'
    from .prompt_language import normalize_vehicle_aliases
    try:
        text = normalize_vehicle_aliases(text)
    except ValueError:
        pass  # Recovery must remain usable when malformed quantities caused the original error.
    number = r'\d+(?:\.\d+)?'
    seconds = r'(?:seconds?|secs?|s)\b'
    speed = rf'(?<![\w.+-]){number}\s*(?:mph|km/h|kph|kmh|m/s)\b'
    def value(pattern, default, group=1):
        match = re.search(pattern, text)
        return next((part for part in match.groups()[group - 1:] if part is not None), default) if match else default
    actor = value(r'\b(?:vehicle|car|v)[ _#-]*(\d+)\b', '')
    ego_subject = re.match(r'\s*(?:(?:at|after)\s+' + number + r'\s*' + seconds + r'\s*,?\s*)?(?:the\s+)?ego\b', text)
    valid_actor = actor if actor and 1 <= int(actor) <= 6 else '<number>'
    role = 'ego' if ego_subject and not actor else 'vehicle_' + valid_actor
    lane = value(r'\b(?:in|on|at)\s+(?:the\s+)?(left|middle|right)\b|^(?:ego|(?:vehicle|car|v)[ _#-]*\d+)\s+(left|middle|right)\b', '<lane>')
    start = value(rf'\bat\s+({number})\s*{seconds}', '<start>')
    duration = value(rf'\b(?:over|duration\s*[:=]?)\s*({number})\s*{seconds}', '<duration>')
    if re.search(r'\b(?:obstacle|cone|barrier|barrel|debris|disabled vehicle)\b', text):
        return 'Obstacle <number>: a <type> in the <lane> lane <distance> metres <ahead of/behind> <reference vehicle> at <start>s until <end>s.'
    if re.search(r'\b(?:ped(?:estrian)?|person|walker)(?:[ _#-]*\d+)?\b', text):
        return 'Pedestrian <number> crossing from <side> to <side> <distance> metres <ahead of/behind> ego at <start>s over <duration>s.'
    if re.search(r'\b(?:brak\w*|slow\w*|decelerat\w*|stop\w*|accelerat\w*)\b|speed\s+up|adjusts speed', text):
        action = ('changes speed' if 'adjusts speed' in text else
                  'accelerates' if re.search(r'accelerat|speed\s+up', text) else 'brakes')
        target = value(rf'\bto\s+({speed})', '<target> km/h')
        return f'At {start}s {role} {action} to {target} over {duration}s.'
    if re.search(r'\b(?:chang\w*|turn\w*|merg\w*|switch\w*|mov\w*|go\w*|shift\w*)\b', text):
        destination = value(r'\b(?:to|into)\s+(?:the\s+)?(left|middle|right)\b', '<destination>')
        return f'At {start}s {role} changes into the {destination} lane over {duration}s.'
    if re.search(r'\b(?:duration|length|total time|scenario|simulation)\b', text) and not re.search(r'\bego\b|vehicle', text):
        return _scenario_recovery_wording(text)
    initial_speed = value(rf'({speed})', '<speed> km/h')
    gap = '' if role == 'ego' else ' <gap> seconds <ahead of/behind> ego'
    return f'{role} in the {lane} lane at {initial_speed}{gap}.'


def build_recovery_repairs(prompt, original=None):
    """Each fallback button owns one current description; never guess an actor."""
    ranges = list(raw_descriptions(prompt))
    example = None
    if original is not None and original.available and original.start < original.end:
        operations = SequenceMatcher(None, original.prompt, prompt, autojunk=False).get_opcodes()
        start, end = _map_source_range(original.start, original.end, operations, len(prompt))
        source = original.prompt[original.start:original.end]
        if prompt[start:end] == source:
            ranges, example = [(start, end)], original.example
    if not ranges:
        ranges = [(len(prompt), len(prompt))]
    result = []
    for number, (start, end) in enumerate(ranges):
        source = prompt[start:end]
        template = example or recovery_wording(source)
        replacement = template.rstrip('.') if prompt[end:].strip() else template
        result.append(PromptRepair(prompt, 'description', template, start, end, replacement,
                                   source_clause=source, request_id=number, recovery=True))
    return result
