"""Shared English wording for independent sensory alerts (Python 3.7)."""
import re

from .takeover_alerts import BEEP as _BEEP, SPEECH as _SPEECH, QUOTED, NUMBER, SECONDS, _number_words, alert_boundary_mask

BEEP = re.compile(r'\b(?:(?:play|sound|emit|trigger|start|produce|generate)\s+(?:(?:a|an|the)\s+)?)?'
                  r'(?:(?:audio|auditory|audible)\s+)?' + _BEEP.pattern + r'(?:\s+(?:sound|tone))?', re.I)
SPEECH = re.compile(r'\b(?:(?:play|use|give|trigger|start)\s+(?:(?:a|an|the)\s+)?)?'
                    r'(?:(?:audio|auditory|audible)\s+)?(?:' + _SPEECH.pattern +
                    r'|saying\b|announce\b|announcement\b|read\s+out\b)(?:\s+(?:message|prompt|notification))?', re.I)
CIRCLE = re.compile(r'\b(?:(?:draw|show|display|put|place|add)\s+(?:(?:a|an|the)\s+)?)?'
                    r'(?:target\s+circles?|circles?|rings?|outlines?|highlight(?:ing)?)\b(?:\s+around)?', re.I)

_CLOCK = r'(?:(?:at\s*(?:t\s*)?=?|after|in|t\s*=)\s*' + NUMBER + r'\s*' + SECONDS + r'\s*[:,]?\s*)'
_INTERVAL = r'(?:from\s+' + NUMBER + r'\s*' + SECONDS + r'\s*(?:to|until|through)\s*' + NUMBER + r'\s*' + SECONDS + r'\s*[:,]?\s*)'
_POLITE = r'(?:(?:please|then|can you|could you|i want to|i would like to|i want|i would like)\s+)*'
_PREFIX = re.compile(_POLITE + r'(?:' + _CLOCK + '|' + _INTERVAL + r')?' + _POLITE +
                     r'(?:(?:play|sound|emit|trigger|add|activate|give|start|use|make|produce|generate|show|display|draw|put|place)\s+)?'
                     r'(?:(?:a|an|the)\s+)?', re.I)


def cue_head(source):
    """Identify a standalone instruction, never an actor or quoted message."""
    text = _number_words(source)
    if re.match(r'\s*cue\b', text, re.I):
        return None  # Existing explicit cue grammar retains its own validation.
    mask = QUOTED.sub(lambda m: ' ' * len(m[0]), text)
    for kind, pattern in [('circle', CIRCLE), ('beep', BEEP), ('speech', SPEECH)]:
        match = pattern.search(mask)
        if match:
            prefix = mask[:match.start()].strip()
            from .collision_language import collision_timing
            remainder, condition, _ = collision_timing(prefix)
            if (_PREFIX.fullmatch(prefix + (' ' if match.start() else ''))
                    or condition and not remainder.strip(' ,')):
                return kind, match.start(), match.end(), text
    return None


def normalize_cue_source(head):
    kind, start, end, text = head
    prefix = text[:start]
    # These words were validated by the anchored instruction prefix; preserve
    # any clock interval, not request scaffolding such as "please play a".
    prefix = re.sub(_POLITE, '', prefix, count=1, flags=re.I)
    prefix = re.sub(r'\b(?:please|then|play|sound|emit|trigger|add|activate|give|start|use|make|produce|generate|show|display|draw|put|place|a|an|the)\b', ' ', prefix, flags=re.I)
    text = prefix + ' ' + kind + (' around' if kind == 'circle' and re.search(r'\baround\s*$', text[start:end], re.I) else '') + text[end:]
    # Compact collision fields and natural collision phrases share one reader.
    # Only unquoted fields are rewritten; speech text is literal.
    mask = QUOTED.sub(lambda m: ' ' * len(m[0]), text)
    threshold = re.search(r'\bttc\s*=\s*(' + NUMBER + r'\s*' + SECONDS + r')', mask, re.I)
    obstacle = re.search(r'\bobstacle\s*=\s*(obstacle[ _#-]*\d+)', mask, re.I)
    if threshold:
        reference = obstacle[1] if obstacle else 'the obstacle'
        edits = [(threshold.start(), threshold.end(), threshold[1] + ' before ego hits ' + reference)]
        if obstacle:
            edits.append((obstacle.start(), obstacle.end(), ' '))
        for left, right, replacement in sorted(edits, reverse=True):
            text = text[:left] + replacement + text[right:]
    return text


def split_standalone_phrases(text, source_clauses=None):
    """Extract independent requests while retaining exact repair source spans."""
    from .takeover_alerts import _parse_alert, ALERT, WARNING_REQUEST
    mask = alert_boundary_mask(QUOTED.sub(lambda m: ' ' * len(m[0]), text))
    # Split only before a new instruction head. Commas inside timing/message
    # fields and conjunctions inside a combined takeover alert remain intact.
    candidates = list(re.finditer(r'[.!?](?=\s|$)|[;\r\n]+|,\s*(?:(?:and\s+)?then\s+|and\s+)?|\s+and(?:\s+then)?\s+', mask, re.I))
    boundaries = []
    left = 0
    for match in candidates:
        punctuation = bool(re.match(r'[.!?;\r\n]', match[0]))
        if not punctuation:
            tail = mask[match.end():].lstrip()
            next_alert = re.match(r'(?:take[ -]?over|tor|warning|alert|cue)\b', tail, re.I)
            if not cue_head(tail) and not next_alert:
                continue
            before = mask[left:match.start()].strip()
            from .collision_language import collision_timing
            remainder, condition, _ = collision_timing(_number_words(before))
            if condition and not remainder.strip(' ,'):
                continue
            timed_before = bool(condition or re.search(NUMBER + r'\s*' + SECONDS, _number_words(before), re.I))
            if (ALERT.search(before) or WARNING_REQUEST.search(before)) and not cue_head(before) and not timed_before:
                continue
            if not cue_head(before) and not re.search(r'\b(?:ego|vehicle|v[1-6]|cue)\b', before, re.I) and not (timed_before and ALERT.search(before)):
                continue
            if cue_head(before) and not condition and not re.search(NUMBER + r'\s*' + SECONDS, _number_words(before), re.I):
                continue  # "beep and speech at 7s" shares the timing.
            if re.fullmatch(_POLITE + r'(?:' + _CLOCK + '|' + _INTERVAL + r')', mask[left:match.start()].strip() + ' ', re.I):
                continue  # "At 7s, beep ..." is one request.
        boundaries.append((match.start(), match.end()))
        left = match.end()
    cues, traffic, errors, left = [], [], [], 0
    for right, following in boundaries + [(len(text), len(text))]:
        source = text[left:right].strip()
        head = cue_head(source)
        if head:
            try:
                parsed = _parse_alert(normalize_cue_source(head), standalone_kind=head[0])
            except ValueError as exc:
                # Diagnostics must target the user's original sentence.
                exc.clause = source
                # Structured missing-info rows also carry their own source.
                if hasattr(exc, 'repair_requests'):
                    for request in exc.repair_requests:
                        request['clause'] = source
                errors.append(exc)
                parsed = []
            cues.extend(parsed)
            if source_clauses is not None:
                source_clauses.extend([source] * len(parsed))
        elif source:
            traffic.append(source)
        left = following
    if errors:
        if len(errors) == 1:
            raise errors[0]
        from .prompt_feedback import missing_information
        issues = []
        for error in errors:
            requests = getattr(error, 'repair_requests', None) or [dict(
                role='Alert', fields=[getattr(error, 'reason', str(error))],
                example=error.example, clause=error.clause)]
            issues.extend((request['role'], request.get('fields', []), request['example'],
                           request.get('recognized', []), request['clause']) for request in requests)
        raise missing_information(issues)
    return '\n'.join(traffic) if cues else text, cues
