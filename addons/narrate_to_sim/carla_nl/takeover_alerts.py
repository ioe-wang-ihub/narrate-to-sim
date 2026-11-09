"""Checked natural-English timed alerts; independent from adaptive TOR decisions."""
import json
import re

from .prompt_feedback import PromptInputError
from .collision_language import collision_timing, collision_matches, collision_target_example as _collision_target_example

NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
SECONDS = r'(?:seconds?|secs?|s)\b'
VALUE = r'(' + NUMBER + r')\s*' + SECONDS
WARNING_NAME = r'(?:(?:driver|collision|safety|hazard)\s+)?(?:(?:warning|warnning|warnign|waring|warrning)s?(?:\s+(?:alerts?|notifications?|messages?|signals?))?|alerts?|alarms?|cautions?|heads[ -]up)'
ALERT = re.compile(r'\b(?:take[ -]?over(?:\s+(?:alerts?|requests?|warnings?|notifications?|reminders?|prompts?|signals?|cues?|alarms?|cautions?))?|tor|handover\s+(?:alert|request|warning)|request\s+to\s+take\s+over|' + WARNING_NAME + r')\b', re.I)
WARNING_REQUEST = re.compile(r'\b(?:warn(?:s|ing)?|alert(?:s|ing)?|notif(?:y|ies|ying))\s+(?:me|us|(?:the\s+)?driver)\b', re.I)
VISUAL = re.compile(r'\b(?:visual|visually|on[ -]screen|screen|display|displayed|show|shown|textual|text[ -]based|popup|pop[ -]up|hud)\b', re.I)
AUDIO = re.compile(r'\b(?:auditory|audio|audible|acoustic|sound|sounding)\b', re.I)
BEEP = re.compile(r'\b(?:beeps?|beeping|beep[ -]beep|b[ei]b[ei]|buzzer|buzzing|tones?|chimes?|ding|alarm\s+sound)\b', re.I)
SPEECH = re.compile(r'\b(?:speech(?:[ -]based)?|spoken|voice(?:[ -]based)?|verbally|verbal|tts|text[ -]to[ -]speech|speak|speaks|say|read\s+aloud)\b', re.I)
QUOTED = re.compile(r'"(?:\\.|[^"\\])*"')
TARGET = re.compile(r'\b(?:target(?:ing)?|around|circle(?:\s+around)?|highlight(?:ing)?|on)\s*=?\s*(?:the\s+)?((?:vehicle|vehilce|car|v)[ _#-]*[1-6]|obstacle[ _#-]*[1-9]\d*|ego)\b', re.I)
CONVERSATIONAL_ALERT = re.compile(r'\b(?:warn\s+(?:me|us|the\s+driver)|(?:give\s+(?:me|us|the\s+driver)\s+)?(?:a\s+)?(?:spoken\s+)?heads[ -]up|(?:tell|ask|remind)\s+(?:me|us|the\s+driver)\s+(?:verbally\s+)?to\s+take\s+(?:over|(?:back\s+)?control)|let\s+(?:me|us|the\s+driver)\s+know|say|announce)\b', re.I)


def collision_target_example(source):
    """Replace only the collision reference, never words in quoted messages."""
    return _collision_target_example(source, _number_words)


def target_repair_example(source):
    mask = QUOTED.sub(lambda m: ' ' * len(m[0]), source)
    target = TARGET.search(mask)
    if target:
        field = '<obstacle>' if target[1].lower().startswith('obstacle') else '<vehicle>'
        return source[:target.start(1)] + field + source[target.end(1):]
    return source + ' targeting <vehicle>'


def _number_words(text, preserve_offsets=False):
    from .prompt_english import NUMBER_WORDS, _number
    word = r'(?:' + NUMBER_WORDS + r'|point|and)'
    pattern = r'\b' + word + r'(?:[\s-]+' + word + r')*\b(?=[\s-]*(?:seconds?|secs?)\b)'
    def convert(match):
        try:
            value = '{:g}'.format(_number(match[0]))
            return value.ljust(len(match[0]) if preserve_offsets else 0)
        except ValueError:
            return match[0]  # Malformed numbers remain visible for correction.
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(pattern, convert, parts[index], flags=re.I)
    return ''.join(parts)


def alert_boundary_mask(text):
    """Protect only internal timing abbreviations; retain sentence endings."""
    masked = QUOTED.sub(lambda m: ' ' * len(m[0]), text)
    result = list(text)
    for match in collision_matches(_number_words(masked, preserve_offsets=True)):
        end = match.end() - (1 if text[match.end() - 1:match.end()] == '.' else 0)
        for point in re.finditer(r'\.(?=\s|$)', text[match.start():end]):
            result[match.start() + point.start()] = ' '
    return ''.join(result)


def _parse_alert(source, standalone_kind=None):
    text = _number_words(source)
    quoted = list(QUOTED.finditer(text))
    message = 'Please take over.'
    if len(quoted) == 1:
        try:
            message = json.loads(quoted[0][0])
        except ValueError:
            # Natural quoted messages are not required to be JSON strings.
            message = re.sub(r'\\(["\\])', r'\1', quoted[0][0][1:-1])
    rest = QUOTED.sub(' ', text).lower()
    visual, audio = bool(VISUAL.search(rest)), bool(AUDIO.search(rest))
    beep, speech = bool(BEEP.search(rest)), bool(SPEECH.search(rest))
    # "saying" introduces either the visual or spoken message; by itself it
    # means speech only when a visual channel was not requested.
    speech = speech or (bool(re.search(r'\bsaying\b', rest)) and not visual)
    standalone_circle = standalone_kind == 'circle'
    if standalone_kind:
        from .cue_language import BEEP as CUE_BEEP, SPEECH as CUE_SPEECH, CIRCLE
        # "show a circle" is a circle instruction, not an extra text overlay.
        # Normalize whole phrases before target extraction removes their nouns.
        rest = CIRCLE.sub(lambda m: 'circle around' if re.search(r'\baround\s*$', m[0]) else 'circle', rest)
        rest = CUE_BEEP.sub('beep', rest)
        rest = CUE_SPEECH.sub(lambda m: 'saying' if re.search(r'\bsaying\b', m[0]) else 'speech', rest)
        visual = bool(VISUAL.search(rest))
        beep = bool(BEEP.search(rest))
        speech = bool(SPEECH.search(rest)) or (bool(re.search(r'\bsaying\b', rest)) and not visual)
        standalone_circle = visual or bool(CIRCLE.search(rest))
        audio = beep or speech
    mode = ('visual' if visual else '')
    if audio or beep or speech:
        mode += (' and ' if mode else '') + 'auditory ' + ('<audio>' if beep == speech else 'beep' if beep else 'speech')
    mode = mode or '<alert-mode>'
    target = TARGET.search(rest)
    target_role = ('ego' if target[1] == 'ego' else ('obstacle_' if target[1].startswith('obstacle') else 'vehicle_') + re.search(r'\d+', target[1])[0]) if target else '<vehicle>'
    if target:
        rest = rest[:target.start()] + ' ' + rest[target.end():]

    rest, trigger, problems = collision_timing(rest)
    if trigger and re.search(r'\b(?:tell|ask|remind|say|announce)\b', rest) and not (visual or beep or speech):
        speech = True
        mode = 'auditory speech'
    if visual and trigger and not target:
        target_role = ''  # Resolve the collision obstacle after traffic parsing.
    # Consume intervals before standalone start/duration expressions.
    interval = re.search(r'\bfrom\s+' + VALUE + r'\s*(?:to|until|through)\s+' + VALUE, rest)
    starts, ends, durations = [], [], []
    if interval:
        starts.append(float(interval[1])); ends.append(float(interval[2]))
        rest = rest[:interval.start()] + ' ' + rest[interval.end():]
    specs = [
        (ends, r'\b(?:until|till|ending\s+at|ends?\s+at|stop(?:s)?\s+at)\s*=?\s*' + VALUE),
        (durations, r'\b(?:for|lasting|lasts?|duration(?:\s+of)?|over)\s*[:=]?\s*' + VALUE),
        (starts, r'\b(?:at\s*(?:t\s*=\s*)?=?\s*|t\s*=\s*|time\s*[:=]\s*|start(?:s|ing)?\s+(?:at\s+)?|(?:after|in)\s+)' + VALUE),
        (starts, r'\bat\s+(?:second|sec)\s+(' + NUMBER + r')\b'),
    ]
    for values, pattern in specs:
        def collect(match):
            values.append(float(match[1]))
            return ' '
        rest = re.sub(pattern, collect, rest)
    start = starts[0] if len(starts) == 1 and starts[0] >= 0 else None
    duration = durations[0] if len(durations) == 1 and durations[0] > 0 else None
    if start is None and not trigger:
        problems.append('Specify one non-negative alert start time, e.g. at 10s.')
    if trigger and (starts or ends):
        problems.append('This alert has both a collision condition and an absolute time. The suggested wording keeps the collision condition; remove it only if a fixed-time alert was intended.')
    if len(durations) > 1 or (durations and duration is None):
        problems.append('Specify one positive alert duration, e.g. for 3s.')
    if ends:
        if len(ends) != 1 or start is None or ends[0] <= start:
            duration = None
            problems.append('The alert end must follow its start; enter a positive duration.')
        elif durations and duration != ends[0] - start:
            duration = None
            problems.append('The duration and end time disagree; choose the intended duration.')
        else:
            duration = ends[0] - start
    elif not durations:
        duration = 4.0
    if '<alert-mode>' in mode and not standalone_kind:
        problems.append('Choose Visual, Auditory Beep, Auditory Speech, or Visual with either audio type.')
    if '<audio>' in mode and not (standalone_kind and beep and speech):
        problems.append('Choose one auditory type: Beep or Speech.')
    if ((visual and not standalone_kind) or standalone_circle) and (target_role == '<vehicle>' or target_role == 'ego' and not standalone_kind):
        target_role = '<vehicle>'
        problems.append('Choose the defined target vehicle to circle, e.g. vehicle 1.')
    if target and standalone_kind and not standalone_circle:
        problems.append('Only a target circle uses a target vehicle; create a separate circle request if intended.')
    elif target and not visual and not standalone_circle:
        problems.append('A target circle requires Visual; select Visual with the intended audio type.')
        mode = 'visual and ' + mode
        visual = True
    if len(quoted) > 1 or not message.strip():
        message = '<text>'
        problems.append('Provide one non-empty alert message.')
    if standalone_kind and quoted and not (speech or visual):
        problems.append('Beep and target circle do not speak text. The suggested wording adds Speech to read your message; keep it only if intended.')
        speech = True  # Preserve the message in a reviewable repair, never drop it.

    rest = WARNING_REQUEST.sub(' ', rest)
    rest = CONVERSATIONAL_ALERT.sub(' ', rest)
    rest = ALERT.sub(' ', rest)
    for pattern in ((CUE_BEEP, CUE_SPEECH, CIRCLE) if standalone_kind else ()) + (VISUAL, AUDIO, BEEP, SPEECH):
        rest = pattern.sub(' ', rest)
    if standalone_circle:
        rest = re.sub(r'\bcircle\b', ' ', rest)
    if trigger:
        rest = CONVERSATIONAL_ALERT.sub(' ', rest)
        rest = re.sub(r'\b(?:me|us|need|make|by|verbally|handover|reminder|lead\s+time)\b', ' ', rest)
    rest = re.sub(r'\b(?:me|us|system)\b', ' ', rest)
    rest = re.sub(r'\b(?:please|i|want|would|like|to|a|an|the|driver|drivers|issue|trigger|play|give|add|activate|provide|set|starts?|happen|happens|occur|occurs|send|emit|produce|generate|present|deliver|use|using|with|via|as|and|plus|both|should|will|must|needs?|be|is|it|saying|message|text|based|alert|notification|warning|request|of|for|sound|signal|takeover)\b', ' ', rest)
    unknown = rest.strip(' \t,:.!=+-()[]')
    if unknown:
        problems.append('Unrecognized alert instruction: ' + unknown + '. Review the suggested wording before replacing.')

    example = 'Takeover alert {} at {}s for {}s'.format(mode, '{:g}'.format(start) if start is not None else '<start>', '{:g}'.format(duration) if duration is not None else '<duration>')
    if trigger:
        reference = trigger['target_role'] or 'the obstacle'
        if trigger.get('target_kind') and reference != '<obstacle>':
            reference = trigger['target_kind'].lower() + (' ' + trigger['target_role'].split('_')[-1] if trigger['target_role'] else '')
        example = 'Takeover alert {} {}s before ego hits {} for {}s'.format(
            mode, '{:g}'.format(trigger['threshold_s']) if trigger['threshold_s'] is not None else '<ttc>',
            reference, '{:g}'.format(duration) if duration is not None else '<duration>')
    if visual and target_role:
        example += ' targeting ' + target_role
    if visual or speech or message != 'Please take over.':
        example += ' saying ' + (message if message == '<text>' else json.dumps(message, ensure_ascii=False))
    kinds = (['visual', 'circle'] if visual else []) + (['beep'] if beep else ['speech'] if speech else [])
    if standalone_kind:
        kinds = (['visual'] if visual else []) + (['circle'] if standalone_circle else []) + (['beep'] if beep else []) + (['speech'] if speech else [])
        timing = 'at={}s duration={}s'.format('{:g}'.format(start) if start is not None else '<start>',
                                                 '{:g}'.format(duration) if duration is not None else '<duration>')
        if trigger:
            timing = '{}s before ego hits {} for {}s'.format(
                '{:g}'.format(trigger['threshold_s']) if trigger['threshold_s'] is not None else '<ttc>',
                trigger['target_role'] or 'the obstacle', '{:g}'.format(duration) if duration is not None else '<duration>')
        examples = []
        for kind in kinds:
            line = kind + ' ' + timing
            if kind == 'circle':
                line += ' target=' + (target_role or '<vehicle>')
            if kind in ('speech', 'visual'):
                line += ' text=' + (message if message == '<text>' else json.dumps(message, ensure_ascii=False))
            # Visual text has an established explicit cue grammar.
            if kind == 'visual':
                line = 'cue ' + line
                if trigger:
                    line = 'cue visual ttc={}s obstacle={} duration={}s text={}'.format(
                        '{:g}'.format(trigger['threshold_s']) if trigger['threshold_s'] is not None else '<ttc>',
                        trigger['target_role'] or '<obstacle>',
                        '{:g}'.format(duration) if duration is not None else '<duration>',
                        message if message == '<text>' else json.dumps(message, ensure_ascii=False))
            examples.append(line)
        example = '\n'.join(examples)
    if problems:
        if not unknown:
            from .prompt_feedback import missing_information
            known = (['start: {:g}s'.format(start)] if start is not None else [])
            known += (['duration: {:g}s'.format(duration)] if duration is not None else [])
            if trigger and trigger['threshold_s'] is not None:
                known.append('trigger: live TTC <= {:g}s to {}'.format(trigger['threshold_s'], trigger['target_role'] or 'the obstacle'))
            label = {'beep': 'Beep', 'speech': 'Speech', 'circle': 'Target circle'}.get(standalone_kind, 'Takeover alert')
            error = missing_information([(label, problems, example, known, source)])
            error.example = example
            error.code = 'cue' if standalone_kind else 'takeover_alert'
            raise error
        raise PromptInputError(' '.join(problems), clause=source, reason=' '.join(problems),
                               code='cue' if standalone_kind else 'takeover_alert', example=example)
    result = [dict(kind=kind, time_s=None if trigger else start, duration_s=duration,
                 text=message if kind in ('visual', 'speech') else '',
                 target_role=target_role if kind == 'circle' else '') for kind in kinds]
    if trigger:
        for cue in result:
            cue['trigger'] = dict(trigger)
    return result


def extract_takeover_alerts(text, source_clauses=None):
    """Keep quoted punctuation and original source clauses for automatic Replace."""
    mask = QUOTED.sub(lambda m: ' ' * len(m[0]), text)
    mask = alert_boundary_mask(mask)
    timed = r'(?:(?:at|after|in)\s+(?:t\s*=\s*)?' + NUMBER + r'\s*' + SECONDS + r'\s*[:,]?\s*)?'
    introduction = r'(?:(?:please|then)\s+)?(?:(?:issue|give|trigger|play|show|add|activate|provide|sound|send)\s+(?:(?:a|an|the)\s+)?)?(?:visual|auditory|audio|beep|spoken|speech|take[ -]?over|tor|ego|vehicle[_ ]?[1-6]|' + WARNING_NAME + r'|' + WARNING_REQUEST.pattern + r')\b'
    imperative = r'(?:issue|give|trigger|play|show|add|activate|provide|sound|send|warn|notify|alert)\b'
    boundary = re.compile(r'[.!?](?=\s|$)|[;\r\n]+|(?:,\s*(?:and\s+)?|\s+and\s+then\s+)(?=' + timed + introduction + r')|\s+and\s+(?=' + timed + r'(?:' + imperative + r'|take[ -]?over\b|tor\b|' + WARNING_NAME + r'\b))', re.I)
    cues, removals, left, preceding = [], [], 0, 0
    for match in list(boundary.finditer(mask)) + [None]:
        right = match.start() if match else len(text)
        if match and re.fullmatch(timed, mask[left:right].strip(), re.I):
            continue  # Keep "At 10s, play ..." together.
        if match and match[0].lstrip().startswith(','):
            remaining, condition, _ = collision_timing(_number_words(mask[left:right].strip()))
            if condition and not remaining.strip():
                continue  # Keep "When TTC reaches ..., play ..." together.
        if match and match[0].lstrip().startswith(',') and ALERT.search(mask[left:right]):
            following = re.split(r'[,;\n]', mask[match.end():], maxsplit=1)[0]
            if not ALERT.search(following) and not re.match(timed + r'(?:ego|vehicle[_ ]?[1-6])\b', following, re.I):
                continue  # "An auditory alert, beep, starting at ...".
        offset = preceding
        source = text[left:right].strip()
        left = match.end() if match else len(text)
        preceding = match.start() if match else len(text)
        unquoted = QUOTED.sub(' ', source)
        is_collision = bool(collision_timing(_number_words(unquoted))[1])
        if not (ALERT.search(unquoted) or WARNING_REQUEST.search(unquoted) or (is_collision and CONVERSATIONAL_ALERT.search(unquoted))) or re.match(r'\s*cue\b', source, re.I):
            continue
        if re.search(r'\b(?:budget|takeover_budget|key|timeout|collision_buffer|target_speed)\s*=', unquoted, re.I):
            continue  # Legacy TOR builder commands keep their existing route.
        if not is_collision and not any(p.search(unquoted) for p in (VISUAL, AUDIO, BEEP, SPEECH)) and re.search(
                r'\b(?:ttc|time.to.collision|manual steering|return to autonomous|return to auto|g29|steering wheel)\b', unquoted, re.I):
            continue  # Let the existing TOR reader explain these controls.
        # Existing adaptive TOR sentences supply a deadline or explicitly no TOR.
        if not any(p.search(unquoted) for p in (VISUAL, AUDIO, BEEP, SPEECH)) and (
                re.search(r'\bby\s+' + VALUE, unquoted, re.I)
                or re.match(r'(?:there is no|do not add a|no) takeover request', source, re.I)
                or (re.search(r'\btake\s+over\s+by\s+' + VALUE, mask, re.I)
                    and re.match(r'(?:issue|give|trigger).*\b(?:alert|warning)\s+at\b', source, re.I))):
            continue
        parsed = _parse_alert(source)
        end = match.end() if match else len(text)
        if removals and offset <= removals[-1][1]:
            removals[-1] = (removals[-1][0], end)
        else:
            removals.append((offset, end))
        cues.extend(parsed)
        if source_clauses is not None:
            source_clauses.extend([source] * len(parsed))
    for start, end in reversed(removals):
        text = text[:start] + '\n' + text[end:]
    return text, cues


def build_alert_snippet(values):
    """Shared UI builder: values are checked by the same parser as free text."""
    mode = values['mode'].lower().replace(' + ', ' and ')
    line = 'Takeover alert ' + mode
    if 'auditory' in mode:
        line += ' ' + values['audio'].lower()
    collision = values.get('trigger') == 'Before collision (TTC)'
    if collision:
        line += ' {}s before ego hits {}'.format(values['ttc'].strip(), values['obstacle'].strip())
    else:
        line += ' at {}s'.format(values['t'].strip())
    line += ' for {}s'.format(values['d'].strip())
    if 'visual' in mode and not collision:
        line += ' targeting ' + values['target'].strip()
    if 'visual' in mode or values['audio'].lower() == 'speech':
        line += ' saying ' + json.dumps(values['text'].strip(), ensure_ascii=False)
    _parse_alert(line)
    return line
