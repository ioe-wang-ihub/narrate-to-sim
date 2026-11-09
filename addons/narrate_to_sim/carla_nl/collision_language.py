"""Checked collision timing grammar with source spans for prompt repairs."""
import re

NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
SECONDS = r'(?:seconds?|secs?|secounds?|secons|seocnds|s)\b\.?'
QUOTED = re.compile(r'"(?:\\.|[^"\\])*"')
EGO = r'(?:the\s+)?(?:ego(?:[ _-]+(?:vehicle|vehilce|vehcile|vechicle|vechile|vehcle|car))?|(?:host|subject)\s+(?:vehicle|car)|(?:my|our)\s+(?:car|vehicle)|we|i)\b'
ORDINALS = dict(zip('first second third fourth fifth sixth seventh eighth ninth tenth'.split(), range(1, 11)))
CARDINALS = dict(zip('one two three four five six seven eight nine ten'.split(), range(1, 11)))
ENTITY = r'(?P<entity>obstacle|obsticle|obstactle|obstacal|obstacel|obstcale|object|hazard|traffic\s+cone|cone|road\s+barrier|barrier|barrel|box)'
OBSTACLE = (r'(?P<collision_target>(?:the\s+|an?\s+)?(?:(?P<ordinal>' + '|'.join(ORDINALS) + r')\s+)?' + ENTITY
            + r'(?:[ _#-]*(?:(?:number|no\.?|num)\s*)?(?P<obstacle>[1-9]\d*|' + '|'.join(CARDINALS) + r'))?\b)')
GAP = r'(?P<ttc>' + NUMBER + r')[\s-]*' + SECONDS
BEFORE = r'(?:before|prior\s+to|ahead\s+of|in\s+advance\s+of)'
WHEN = r'(?:when|once|as\s+soon\s+as)'
AUX = r'(?:(?:will|would)\s+|(?:is|are|am)\s+(?:going\s+to|predicted\s+to|expected\s+to|estimated\s+to)\s+)?'
HIT = r'(?:hits?|hitting|collid(?:es?|ing)\s+with|crash(?:es|ing)?\s+into|reach(?:es|ing)?|strik(?:es?|ing)|runs?\s+into|running\s+into|bumps?\s+into|bumping\s+into|makes?\s+contact\s+with|making\s+contact\s+with|contacts?)'
IMPACT = r'(?:(?:the|a|an)\s+)?(?:(?:predicted|expected|estimated|potential)\s+)?(?:collision|collison|impact|contact|crash)'
# Each alternative contains exactly one target capture when compiled separately.
EVENTS = (
    r'(?:' + EGO + r'\s+)?' + AUX + HIT + r'\s+' + OBSTACLE,
    IMPACT + r'\s+(?:with\s+|between\s+' + EGO + r'\s+and\s+)' + OBSTACLE,
    OBSTACLE + r'\s+(?:is|gets)\s+(?:hit|struck)\s+by\s+' + EGO,
    IMPACT,
)
TTC = r'(?:the\s+)?(?:(?:remaining|estimated|predicted)\s+)?(?:ttc|time[ -](?:to|until)[ -](?:collision|impact|contact))'
COMPARISON = r'(?P<comparison>is\s+less\s+than\s+or\s+equal\s+to|is\s+no\s+more\s+than|is\s+at\s+most|is\s+down\s+to|gets\s+down\s+to|drops\s+to|falls\s+to|is\s+below|is\s+above|is\s+greater\s+than|is\s+at\s+least|reaches|becomes|hits|is|<=|≤|>=|>|=)'
LEAD_WORDS = r'(?:(?:\s+(?:of|advance|spoken|speech|voice|verbal|takeover|take[ -]over|warning|alert|reminder|request|lead\s+time|for|the)))*'


def _patterns():
    patterns = []
    for event in EVENTS:
        patterns.extend((
            r'(?:at\s+)?' + GAP + LEAD_WORDS + r'\s+' + BEFORE + r'\s+' + event,
            WHEN + r'\s+' + EGO + r'\s+(?:is|are|am|gets\s+to)\s+(?:(?:only|just)\s+)?' + GAP + r'\s+(?:away\s+)?from\s+' + event,
            r'(?:' + WHEN + r'\s+(?:there\s+(?:are|is)\s+)?|with\s+)(?:only\s+)?' + GAP + r'\s+(?:(?:are|is)\s+)?(?:left|remaining|remain)\s+(?:before|until|till)\s+' + event,
            WHEN + r'\s+' + EGO + r'\s+(?:has|have)\s+(?:(?:only|just)\s+)?' + GAP + r'\s+(?:left|remaining|to\s+go)\s+(?:before|until|till)\s+' + event,
            WHEN + r'\s+' + event + r'\s+in\s+' + GAP,
        ))
    relation = r'(?:to|with|for)\s+' + OBSTACLE
    for target in (relation, r'between\s+' + EGO + r'\s+and\s+' + OBSTACLE):
        patterns.append(WHEN + r'\s+' + TTC + r'\s+' + target + r'\s+' + COMPARISON + r'\s*(?:a\s+threshold\s+of\s+)?' + GAP + r'(?:\s+or\s+less)?')
    patterns.extend((
        r'(?:at|with)\s+(?:a\s+)?' + TTC + r'\s+(?:threshold\s+)?of\s+' + GAP + r'\s+' + relation,
        r'at\s+' + GAP + r'\s+ttc\s+' + relation,
        WHEN + r'\s+' + TTC + r'\s+' + COMPARISON + r'\s*' + GAP + r'(?:\s+or\s+less)?',
    ))
    return tuple(re.compile(r'(?<![\w.])' + p, re.I) for p in patterns)


COLLISION_TIMING = _patterns()


def collision_matches(text):
    # Prefer a whole countdown clause over its nested "7 seconds before...".
    candidates = sorted((m for p in COLLISION_TIMING for m in p.finditer(text)),
                        key=lambda m: (-(m.end() - m.start()), m.start()))
    accepted = []
    for match in candidates:
        if not any(match.start() < other.end() and other.start() < match.end() for other in accepted):
            accepted.append(match)
    return sorted(accepted, key=lambda m: m.start())


def collision_timing(text):
    matches = collision_matches(text)
    if not matches:
        return text, None, []
    first = matches[0]
    threshold = float(first['ttc'])
    problems = []
    if len(matches) > 1 or not 0 < threshold < float('inf'):
        threshold = None
        problems.append('Choose one positive time before collision, e.g. 7 seconds.')
    comparison = ' '.join(first.groupdict().get('comparison', '').lower().split())
    if comparison in ('>', '>=', 'is above', 'is greater than', 'is at least'):
        problems.append('This condition asks for a larger TTC. The suggested alert starts when TTC falls to the threshold; review this change before replacing.')
    number = first.groupdict().get('obstacle')
    ordinal = first.groupdict().get('ordinal')
    role = str(CARDINALS.get(number.lower(), number)) if number else str(ORDINALS[ordinal.lower()]) if ordinal else ''
    trigger = dict(kind='obstacle_ttc', threshold_s=threshold, target_role='obstacle_' + role if role else '')
    if ordinal and number and int(role) != ORDINALS[ordinal.lower()]:
        problems.append('The obstacle ordinal and number disagree. Choose the intended obstacle.')
        trigger['target_role'] = '<obstacle>'
    entity = ' '.join((first.groupdict().get('entity') or '').lower().split())
    kind = {'cone': 'Cone', 'traffic cone': 'Cone', 'barrier': 'Barrier', 'road barrier': 'Barrier', 'barrel': 'Barrel', 'box': 'Box'}.get(entity)
    if kind:
        trigger['target_kind'] = kind
    for match in reversed(matches):
        text = text[:match.start()] + ' ' + text[match.end():]
    return text, trigger, problems


def collision_target_example(source, normalize_numbers):
    mask = QUOTED.sub(lambda m: ' ' * len(m[0]), normalize_numbers(source, preserve_offsets=True))
    matches = collision_matches(mask)
    if matches:
        match = matches[0]
        if match.groupdict().get('collision_target'):
            start, end = match.span('collision_target')
            return source[:start] + '<obstacle>' + source[end:]
        ttc = re.search(TTC, match[0], re.I)
        at = match.start() + ttc.end() if ttc else match.end()
        return source[:at] + (' to ' if ttc else ' with ') + '<obstacle>' + source[at:]
    field = re.search(r'\bobstacle\s*=\s*\S+', QUOTED.sub(lambda m: ' ' * len(m[0]), source), re.I)
    if field:
        return source[:field.start()] + 'obstacle=<obstacle>' + source[field.end():]
    return source + ' obstacle=<obstacle>'
