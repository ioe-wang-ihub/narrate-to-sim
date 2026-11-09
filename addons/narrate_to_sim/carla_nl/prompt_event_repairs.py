"""Expose conflicting event values as fields instead of reusing invalid values."""
import re
from .prompt_english import RATE


def negative_value_example(clause):
    """Ask only for invalid signed quantities, retaining their original units."""
    def field(match):
        prefix = clause[:match.start()]
        suffix = clause[match.end():]
        unit = re.match(r'\s*(km/h|kmh|kph|mph|m/s|seconds?|secs?|s|metres?|meters?|m)\b', suffix, re.I)
        unit = unit[1].lower() if unit else ''
        if unit in ('km/h', 'kmh', 'kph', 'mph', 'm/s'):
            action = re.search(r'\b(to|from|by)\s*$', prefix, re.I)
            name = {'to': 'target', 'from': 'source', 'by': 'change'}[action[1].lower()] if action else 'speed'
        elif unit in ('m', 'metre', 'metres', 'meter', 'meters'):
            name = 'distance'
        elif re.search(r'\b(?:over|within|for|duration\s*=?)\s*$', prefix, re.I):
            name = 'total' if re.search(r'\bscenario\b', prefix, re.I) else 'duration'
        elif re.search(r'\b(?:until|finishes at|ends at)\s*$', prefix, re.I):
            name = 'end'
        elif re.search(r'\b(?:ahead|behind)\b', suffix, re.I) and not re.search(r'\b(?:at|after)\s*$', prefix, re.I):
            name = 'gap'
        else:
            name = 'start'
        return '<' + name + '>'
    return re.sub(r'(?<!\w)-\s*\d+(?:\.\d+)?', field, clause)


def editable_event_example(example, reason, role, *, event_start=None):
    lower = reason.lower()
    number = r'\d+(?:\.\d+)?'
    unit = r'(km/h|kmh|kph|mph|m/s)'
    if 'a lane-change speed cannot be discarded' in lower or 'rates require a speed-change action' in lower:
        return separate_lane_speed_example(example, role, event_start=event_start)
    if 'source speed' in lower:
        count = 0
        def source(match):
            nonlocal count
            count += 1
            return 'from <source> ' + match[1] if count == 1 else ''
        example = re.sub(rf'\bfrom\s+{number}\s*{unit}\b', source, example, flags=re.I)
    elif 'final speed' in lower:
        count = 0
        def final(match):
            nonlocal count
            count += 1
            return 'then keeps <target> ' + match[1] if count == 1 else ''
        example = re.sub(rf'\bthen keeps\s+{number}\s*{unit}\b', final, example, flags=re.I)
    elif 'target speed' in lower:
        targets = 0
        def target(match):
            nonlocal targets
            targets += 1
            if targets > 1:
                return ''
            mode = match[1].lower()
            return mode + (' <change> ' if mode == 'by' else ' <target> ') + match[2]
        example = re.sub(rf'\b(to|by)\s+{number}\s*{unit}\b', target, example, flags=re.I)
        if 'conflicting speed values' in lower:
            # The extra "at 30km/h" or bare speed has no separate timed
            # action. Let the author choose one target; retain source/hold.
            example = re.sub(rf'\b(?:(from|then\s+keeps|at)\s+)?{number}\s*{unit}\b',
                             lambda m: m[0] if m[1] and m[1].lower() != 'at' else '', example, flags=re.I)
    if any(term in lower for term in ('duration must be positive', 'conflicting event duration', 'conflicting rate and event duration')):
        for pattern, field in [(rf'\bover\s+{number}s\b', 'over <duration>s'),
                               (rf'\bfinishes at\s+{number}s\b', 'finishes at <end>s')]:
            seen = False
            def timing(match):
                nonlocal seen
                if seen:
                    return ''
                seen = True
                return field
            example = re.sub(pattern, timing, example, flags=re.I)
    if 'one positive acceleration/deceleration rate' in lower:
        example = RATE.sub(' ', example).rstrip(' .') + ' at <rate>m/s2.'
    elif 'rate wording' in lower:
        example = RATE.sub(lambda m: 'at ' + m[1] + 'm/s2', example)
    if any(term in lower for term in ('overlap', 'chronological order', 'relative time has no preceding', 'one absolute or relative event time', 'one absolute event start time')):
        # A relative start can be invalid too. Replace the whole time prefix,
        # retaining the actor, action, duration and target exactly as supplied.
        actor = re.search(r'\b' + re.escape(role) + r'\b', example, re.I)
        if actor:
            example = 'At <start>s ' + example[actor.start():]
    if any(term in lower for term in ('unchanged destination', 'beyond the available lanes', 'direction and destination', 'ego lane is ambiguous')):
        example = re.sub(r'\bturn (?:left|right)\b', 'changes into the <destination> lane', example, flags=re.I)
        example = re.sub(r'\b(?:(?:left|right)\s+)?(?:into|to)\s+(?:the\s+)?(?:left|middle|right|ego)\s+lane\b',
                         'into the <destination> lane', example, flags=re.I)
    if 'source lane' in lower:
        count = 0
        def source_lane(match):
            nonlocal count
            count += 1
            return 'from the <lane> lane' if count == 1 else ''
        example = re.sub(r'\bfrom\s+(?:the\s+)?(?:left|middle|right)(?:\s+lane)?\b', source_lane, example, flags=re.I)
    if 'one lane-change destination' in lower:
        destinations = 0
        def destination(match):
            nonlocal destinations
            destinations += 1
            return ' into the <destination> lane' if destinations == 1 else ''
        example = re.sub(r'\s+(?:and\s+)?(?:(?:left|right)\s+)?(?:into|to)\s+(?:the\s+)?(?:left|middle|right|ego)\s+lane\b',
                         destination, example, flags=re.I)
    return example


def separate_lane_speed_example(example, role, *, event_start=None):
    """Keep the lane maneuver and ask for the independent speed intent."""
    # Match the subject used by _event_guidance, including its editable label.
    role = 'vehicle_<number>' if role == 'anonymous' else role
    rates = [match[0] for match in RATE.finditer(example)]
    lane = RATE.sub(' ', example)
    speeds = re.findall(r'\b(?:at\s+)?(\d+(?:\.\d+)?\s*(?:km/h|kmh|kph|mph|m/s))\b', lane, re.I)
    lane = re.sub(r'\b(?:at\s+)?\d+(?:\.\d+)?\s*(?:km/h|kmh|kph|mph|m/s)\b', ' ', lane, flags=re.I)
    lane = re.sub(r'\s+', ' ', lane).strip(' .')
    if not re.search(r'\bover\s+|\bfinishes at\s+', lane, re.I):
        lane += ' over <duration>s'
    # Keep the original maneuver last so a following 'then' still refers to
    # that maneuver. Bind its relative start before inserting the speed action.
    if not re.match(r'At\s+(?:\d+(?:\.\d+)?|<start>)s\b', lane, re.I):
        actor = re.search(r'\b' + re.escape(role) + r'(?!\w)', lane, re.I)
        if actor:
            time = format(event_start, '.15g') if event_start is not None else '<start>'
            lane = f'At {time}s ' + lane[actor.start():]
    speed = speeds[0] if len(speeds) == 1 else '<target> km/h'
    timing = rates[0] if len(rates) == 1 else 'at <rate>m/s2' if rates else 'over <duration>s'
    return f'At <start>s {role} <speed-action> to {speed} {timing}.\n{lane}.'
