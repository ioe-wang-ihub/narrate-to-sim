"""English quantities, actor vocabulary and speed-action wording.

Only recognized phrases are rewritten. Unknown words and conditions stay in
the sentence for semantic validation; quantities are never defaulted here.
"""
import re

from .prompt_feedback import PromptInputError, source_clause_at

NUM = r"\d+(?:\.\d+)?"
SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
NUMBER_WORDS = "|".join([*SMALL, *TENS, "hundred"])
ORDINALS = dict(zip("first second third fourth fifth sixth".split(), range(1, 7)))
DECELERATE = r"(?:brak(?:e[sd]?|ing)|slow(?:s|ing)?(?:\s+down)?|decelerat(?:e[sd]?|ing)|(?:reduc(?:e[sd]?|ing)|drops?)\s+(?:(?:its|the)\s+)?speed)"
ACCELERATE = r"(?:accelerat(?:e[sd]?|ing)|speed(?:s|ing)?\s+up|increas(?:e[sd]?|ing)\s+(?:(?:its|the)\s+)?speed|picks?\s+up\s+speed)"
STOP = r"(?:stops?|stopping|comes?\s+to\s+(?:a\s+)?(?:complete\s+)?(?:stop|standstill|halt))"
SET_SPEED = r"(?:chang(?:e[sd]?|ing)|sets?|setting|adjust(?:s|ing)?|switch(?:es|ing)?|updat(?:e[sd]?|ing))\s+(?:(?:its|the)\s+)?speed"
SPEED_ACTION = re.compile(rf"(?<![\w=])(?:{DECELERATE}|{ACCELERATE}|{STOP}|{SET_SPEED})\b", re.I)
CHECKED_SPEED_ACTION = re.compile(r'\b(?:decelerates|accelerates|adjusts speed)\b', re.I)
# Normalize the copula independently of field order and entity type. The
# semantic reader still checks explicit times, negation and conditions.
STATE_COPULA = re.compile(
    r"\b(?:(?:will|shall|should)(?:\s+initially)?\s+be|(?:is|are)\s+going\s+to\s+be)\s+", re.I)
# Route even negated/uncertain forms for checking. Only STATE_COPULA above
# is safe to normalize; unsupported modal qualifiers must remain visible.
STATE_COPULA_INTENT = re.compile(
    r"\b(?:(?:will|shall|should|may|might|can|could|would|must|won't|shan't|shouldn't|can't|couldn't|wouldn't|mustn't)"
    r"(?:\s+[a-z']+){0,3}\s+be\b|(?:is|are)(?:\s+not)?\s+going\s+to\s+(?:not\s+)?be\b)", re.I)
VEHICLE_STATE_INTENT = re.compile(
    r"\b(?:ego(?:\s+vehicle)?|vehicle_[1-6])\b\s*:?\s*"
    r"(?:is|are|will|shall|should|would|may|might|can|could|must|starts?|drives?|travels?|cruises?|initially)\b", re.I)
RATE = re.compile(rf"(?:at|with)(?:\s+(?:a|an))?(?:\s+(?:deceleration|acceleration))?(?:\s+of)?\s+({NUM})\s*m/s(?:\^?2|/s)\b", re.I)


def normalize_speed_assignments(text):
    """Keep neutral speed actions distinct from lane changes and initial speed.

    Direction is resolved against the executed profile later. Bare speed fields
    become actions only with their own nonzero time or explicit ramp duration.
    This runs before the legacy reader can overwrite an initial speed.
    """
    actor = r'(?:ego|vehicle_[1-6])'
    verb = r'(?:chang(?:e[sd]?|ing)|sets?|setting|adjust(?:s|ing)?|switch(?:es|ing)?|updat(?:e[sd]?|ing))'
    text = re.sub(rf'\b{verb}\s+(?:the\s+)?speed\s+of\s+({actor})\s*(?:to\b|=)',
                  r'\1 adjusts speed to ', text, flags=re.I)
    text = re.sub(rf"\b{verb}\s+({actor})(?:'s)?\s+(?:speed\s*)?(?:to\b|=)",
                  r'\1 adjusts speed to ', text, flags=re.I)
    text = re.sub(rf'\b{SET_SPEED}\s*(?:to\b|=)', 'adjusts speed to ', text, flags=re.I)
    text = re.sub(r'\b(?:will|shall|should)\s+(?=adjusts speed\b)', '', text, flags=re.I)
    pattern = re.compile(rf"\b({actor})(?:'s)?\s*:?\s+speed\s*(?:=|:|is\b|becomes?\b|will\s+be\b|changes?\s+to\b)\s*", re.I)
    clock = re.compile(rf'\b(?:at\s+|after\s+|(?:at\s+)?t\s*=\s*)({NUM})\s*(?:seconds?|secs?|s)\b', re.I)
    def assignment(match):
        left = max([0] + [m.end() for m in re.finditer(r'[.!?](?=\s|$)|[;\r\n]', text[:match.start()])])
        tail = re.split(r'[.!?](?=\s|$)|[;\r\n]|,|\band\b', text[match.end():], maxsplit=1, flags=re.I)[0]
        prefix = text[left:match.start()]
        # A previous actor's time cannot supply this actor's start time.
        prefix = re.split(r'\b(?:ego|vehicle_[1-6])\b|\band\b', prefix, flags=re.I)[-1]
        times = list(clock.finditer(prefix + ' ' + tail))
        ramp = re.search(r'\b(?:over|duration|within|lasting)\b', tail, re.I)
        if times and (any(float(m[1]) > 0 for m in times) or ramp):
            return match[1] + ' adjusts speed to '
        return match[0]
    return pattern.sub(assignment, text)


def correct_common_spellings(text: str) -> str:
    corrections = {
        "vehilce": "vehicle", "vehcile": "vehicle", "vechicle": "vehicle", "vechile": "vehicle", "vehcle": "vehicle",
        "trun": "turn", "chnage": "change", "chagne": "change", "brkae": "brake", "braek": "brake",
        "accelarate": "accelerate", "deccelerate": "decelerate", "duraton": "duration", "duartion": "duration",
        "secounds": "seconds", "secons": "seconds", "seocnds": "seconds", "speeed": "speed",
        "obstactle": "obstacle", "obstacal": "obstacle", "obsticle": "obstacle", "obstacel": "obstacle",
        "pedestrain": "pedestrian", "pedestran": "pedestrian",
        "infront": "in front", "infrontof": "in front of",
    }
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    label_typos = {typo: word for typo, word in corrections.items() if word in ('vehicle', 'obstacle', 'pedestrian')}
    for index in range(0, len(parts), 2):
        # An underscore belongs to the label, so the ordinary word boundary
        # does not match vehilce_1 or vehilce_<number>.
        parts[index] = re.sub(r'\b(?:' + '|'.join(label_typos) + r')(?=_(?:\d+\b|<number>|$))',
                              lambda m: label_typos[m[0].lower()], parts[index], flags=re.I)
        parts[index] = re.sub(r"\b(?:" + "|".join(corrections) + r")\b", lambda m: corrections[m[0].lower()], parts[index], flags=re.I)
        # This typo has a clear positional meaning only in the whole phrase.
        # Keep ordinary font references and quoted speech untouched.
        parts[index] = re.sub(r"\bin\s+font\s+of\b", "in front of", parts[index], flags=re.I)
    return "".join(parts)


def _number(words: str) -> float:
    words = words.lower().replace("-", " ").split()
    if "point" in words:
        i = words.index("point")
        if not words[i + 1:] or any(w not in SMALL or SMALL[w] > 9 for w in words[i + 1:]):
            raise ValueError("Ambiguous decimal number: " + " ".join(words))
        return _number(" ".join(words[:i])) + float("0." + "".join(str(SMALL[w]) for w in words[i + 1:]))
    ones = "|".join(w for w, v in SMALL.items() if 1 <= v <= 9)
    under_hundred = rf"(?:{'|'.join(SMALL)}|(?:{'|'.join(TENS)})(?: (?:{ones}))?)"
    if not re.fullmatch(rf"(?:{under_hundred}|(?:(?:{ones}) )?hundred(?: (?:and )?{under_hundred})?)", " ".join(words)):
        raise ValueError("Ambiguous number words: " + " ".join(words))
    value = 0
    for word in words:
        if word == "and":
            continue
        if word == "hundred":
            value = (value or 1) * 100
        else:
            value += SMALL.get(word, TENS.get(word, 0))
    return float(value)


def normalize_english_quantities(text: str) -> str:
    # Fractions precede whole-number substitutions.
    text = re.sub(rf"\b({NUMBER_WORDS})\s+and\s+a\s+half\b", lambda m: f"{_number(m[1]) + .5:g}", text, flags=re.I)
    text = re.sub(r"\bhalf\s+(?:a|an)\s+(?=second|minute|hour)", "0.5 ", text, flags=re.I)
    number_phrase = rf"\b(?:{NUMBER_WORDS})(?:(?:[ -]+(?:and[ -]+|point[ -]+)?)(?:{NUMBER_WORDS}))*\b"
    units = r"(?:seconds?|secs?|minutes?|mins?|hours?|milliseconds?|ms|mph|kph|km/h|m/s|miles?|kilomet(?:er|re)s?|met(?:er|re)s?|feet|ft|s)\b"
    def quantity(match):
        try:
            return f"{_number(match[0]):g}"
        except ValueError as error:
            raise PromptInputError(str(error), clause=source_clause_at(match.string, match.start())) from error
    text = re.sub(rf"{number_phrase}(?=\s*-?\s*{units})", quantity, text, flags=re.I)
    text = re.sub(rf"\b(vehicle|car|npc)\s+({NUMBER_WORDS})\b", lambda m: f"{m[1]} {int(_number(m[2]))}", text, flags=re.I)
    text = re.sub(rf"\b(?:the\s+)?({'|'.join(ORDINALS)})\s+(?:car|vehicle)\b", lambda m: f"vehicle_{ORDINALS[m[1].lower()]}", text, flags=re.I)
    text = re.sub(r"\b(?:vehicle|car|npc)\s+(?:no\.?|number)\s*([1-6])\b", r"vehicle_\1", text, flags=re.I)
    text = re.sub(r"\bnpc[ _-]*([1-6])\b", r"vehicle_\1", text, flags=re.I)
    text = re.sub(rf"\b({NUM})\s*minutes?\s+({NUM})\s*seconds?\b", lambda m: f"{float(m[1]) * 60 + float(m[2]):g} seconds", text, flags=re.I)
    text = re.sub(rf"\b({NUM})\s*(milliseconds?|ms|minutes?|mins?|hours?)\b", lambda m: f"{float(m[1]) * (.001 if m[2].lower().startswith(('millisecond', 'ms')) else 3600 if m[2].lower().startswith('hour') else 60):g} seconds", text, flags=re.I)
    text = re.sub(rf"\b({NUM})\s*(feet|ft)\b", lambda m: f"{float(m[1]) * .3048:g} metres", text, flags=re.I)
    return text


def normalize_english_phrases(text: str) -> str:
    text = normalize_english_quantities(text)
    text = re.sub(r'\bego\s+car\b', 'ego', text, flags=re.I)
    text = re.sub(r'\b(?:with\s+(?:an?\s+)?(?:initial\s+|starting\s+)?speed\s+(?:of\s+)?|at\s+(?:a\s+)?speed\s+of\s+)', 'at ', text, flags=re.I)
    text = re.sub(rf'\b(?:going|driving|travelling|traveling|cruising)\s+(?:at\s+)?(?={NUM}\s*(?:mph|km/h|kph|kmh|m/s)\b)', 'at ', text, flags=re.I)
    text = re.sub(r'\b(is|are)\s+(initially\s+)?(?:located|positioned)\s+', lambda m: m[1] + ' ' + (m[2] or ''), text, flags=re.I)
    timing_kind = lambda noun: {'lane change': 'lane', 'braking': 'decrease', 'acceleration': 'increase'}.get(noun.lower(), 'any')
    text = re.sub(rf'(^|[.!?;\n]\s*)(?:the\s+)?(maneuver|manoeuvre|lane change|braking|acceleration|action|this|it)\s+(?:takes?|lasts?)\s+({NUM})\s*(?:seconds?|secs?|s)\b',
                  lambda m: f'{m[1]}duration_kind={timing_kind(m[2])} over {m[3]} seconds', text, flags=re.I)
    text = re.sub(rf'\b(?:finishes?|ends?|completes?|completing)\s+(?:(?:the|this|its)\s+)?(maneuver|manoeuvre|lane change|braking|acceleration|action)\s+at\s+({NUM})\s*(?:seconds?|secs?|s)\b',
                  lambda m: f'finishes at {m[2]} seconds completion_kind={timing_kind(m[1])}', text, flags=re.I)
    text = re.sub(rf'\b(?:starting|beginning)\s+at\s+(?={NUM}\s*(?:seconds?|secs?|s)\b)', 'at ', text, flags=re.I)
    text = re.sub(rf'\bfrom\s+({NUM})\s*(?:seconds?|secs?|s)\s+until\s+({NUM})\s*(?:seconds?|secs?|s)\b', r'at \1 seconds until \2 seconds', text, flags=re.I)
    text = re.sub(r'\b(?:from|at)\s+(?:the\s+)?(?:start|beginning)\b', 'at 0s', text, flags=re.I)
    text = re.sub(r'\b(?:makes?|performs?)\s+(?:a\s+)?lane change\s+(?:to|into)\b', 'changes into', text, flags=re.I)
    text = re.sub(r'\b(?:moves?|merges?|shifts?|changes?)\s+(?:one|1)\s+lane\s+(?:to\s+)?(?:the\s+)?(left|right)\b', r'turn \1', text, flags=re.I)
    text = re.sub(r'\b(?:moves?|merges?|shifts?|changes?)\s+(?:to|into)\s+(?:the\s+)?adjacent lane\s+(?:on|to)\s+(?:the\s+)?(left|right)\b', r'turn \1', text, flags=re.I)
    text = re.sub(r'\bappl(?:y|ies|ying)\s+(?:the\s+)?brakes?\s+to\s+(?:reach\s+)?', 'brakes to ', text, flags=re.I)
    text = STATE_COPULA.sub('is ', text)
    text = re.sub(r"\b(?:will|shall|should)\s+(drive|travel|cruise)\b", r"\1s", text, flags=re.I)
    text = re.sub(r"\bstarts?\s+out\b", "starts", text, flags=re.I)
    text = re.sub(r"\bis\s+(initially\s+)?positioned\s+(?=in\b|on\b|at\b)",
                  lambda m: 'is ' + (m[1] or ''), text, flags=re.I)
    text = re.sub(rf"\brun\s+(?:the\s+)?(?:simulation|scenario)\s+for\s+({NUM})\s*(?:seconds?|secs?|s)\b|\b(?:the\s+)?scenario\s+lasts\s+({NUM})\s*(?:seconds?|secs?|s)\b", lambda m: f"Create a {m[1] or m[2]}-second scenario", text, flags=re.I)
    text = re.sub(r"\bthis is a\s+(?=\d+(?:\.\d+)?[- ]second)", "Create a ", text, flags=re.I)
    text = re.sub(r"\b(?:car|vehicle)\s+([a-f])\b", lambda m: f"vehicle_{ord(m[1].lower()) - ord('a') + 1}", text, flags=re.I)
    text = _bind_descriptive_actors(text)
    text = re.sub(r"\b(a|one|the|that|this)\s+vehicle\b(?![_#\d]|\s*[#\d])", r"\1 car", text, flags=re.I)
    text = re.sub(r"\bcruis(?:es|ing)\b", "driving", text, flags=re.I)
    text = re.sub(r"\bin front of me\b", "ahead of ego", text, flags=re.I)
    text = re.sub(r"\bat(?=\d)", "at ", text, flags=re.I)
    ordinal_time = {**ORDINALS, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "twentieth": 20, "thirtieth": 30}
    text = re.sub(rf"\bat\s+(?:the\s+)?({'|'.join(ordinal_time)})\s+second\b", lambda m: f"at {ordinal_time[m[1].lower()]} seconds", text, flags=re.I)
    text = re.sub(rf"(^|[.!?;\n]\s*)in\s+({NUM})\s*(?:seconds?|secs?|s)\b", r"\1At \2 seconds", text, flags=re.I)
    text = re.sub(rf"\bbetween\s+({NUM})\s+and\s+({NUM})\s*(?:seconds?|secs?|s)\b", r"from \1 to \2 seconds", text, flags=re.I)
    text = re.sub(rf"\bat\s+(\d{{1,2}}):(\d{{2}})(?:\.(\d+))?\b", lambda m: f"at {int(m[1]) * 60 + int(m[2]) + (float('0.' + m[3]) if m[3] else 0):g} seconds", text, flags=re.I)
    text = re.sub(rf"\bat\s+(?:the\s+)?({NUM})\s*(?:second|sec|s)\s+mark\b|\bat\s+second\s+({NUM})\b", lambda m: f"at {m[1] or m[2]} seconds", text, flags=re.I)
    text = re.sub(rf"\bfrom\s+({NUM})\s*(?:seconds?|secs?|s)?\s+to\s+({NUM})\s*(?:seconds?|secs?|s)\b", lambda m: f"at {m[1]} seconds and finishes at {m[2]} seconds", text, flags=re.I)
    text = re.sub(rf"\b(?:reaching\s+(?:that|the)\s+speed|coming\s+to\s+a\s+stop)\s+by\s+({NUM})\s*(?:seconds?|secs?|s)\b", r"finishes at \1 seconds", text, flags=re.I)
    text = re.sub(rf"\b({NUM})\s*(?:seconds?|secs?|s)\s+later\b", r"\1 seconds after completing the previous action", text, flags=re.I)
    text = re.sub(rf",\s*(holds?\s+(?:that|the same)\s+speed|stays?\s+stopped|remains?\s+stationary)\s+for\s+({NUM})\s*(?:seconds?|secs?|s)\s*,?\s*then\s+", lambda m: f", {m[2]} seconds after completing the previous {'stationary ' if not m[1].lower().startswith('hold') else ''}action, ", text, flags=re.I)
    text = re.sub(rf"\bfrom\s+({NUM})\s+to\s+({NUM})\s*(mph|km/h|kmh|kph|m/s)\b", r"from \1 \3 to \2 \3", text, flags=re.I)
    # Preserve 'for' after stopping: it can mean time held stationary, not
    # braking duration. The validator requests a clear stop/hold description.
    text = re.sub(rf"\b(?:{DECELERATE})\s+to\s+(?:a\s+)?(?:complete\s+)?(?:stop|standstill)\b", "decelerates to 0 km/h", text, flags=re.I)
    text = re.sub(rf"\b{STOP}\b", "decelerates to 0 km/h", text, flags=re.I)
    text = re.sub(rf"\b{DECELERATE}\b", "decelerates", text, flags=re.I)
    text = re.sub(r'\b(after\s+(?:completing|finishing|starting)(?:\s+that|\s+the)?)\s+decelerates\b', r'\1 braking', text, flags=re.I)
    text = re.sub(rf"\b{ACCELERATE}\b", "accelerates", text, flags=re.I)
    text = re.sub(r'\b(decelerates|accelerates)\s+(?:down|up)\s+to\b', r'\1 to', text, flags=re.I)
    text = re.sub(rf'\b(taking|takes|over|in)\s+({NUM}\s*(?:seconds?|secs?|s))\s+to\s+reach\s+that\s+speed\b', r'\1 \2', text, flags=re.I)
    text = re.sub(r"\b(?:begins?|starts?)\s+(?:to\s+)?(?=decelerates|accelerates)", "", text, flags=re.I)
    text = re.sub(r"\b(?:should|will|shall)\s+(?=decelerates|accelerates)", "", text, flags=re.I)
    return text


def _bind_descriptive_actors(text: str) -> str:
    """Descriptive names bind to declarations, never to arbitrary role IDs."""
    names = re.compile(r"\b(?:(?:the|a|one|that|this)\s+)?(lead|front|rear|following)\s+(?:car|vehicle)\b", re.I)
    used = {int(m[1]) for m in re.finditer(r"\b(?:(?:vehicle|car|npc)[ _#-]*|v)([1-6])\b", text, re.I)}
    bindings = {}
    for match in names.finditer(text):
        kind = "front" if match[1].lower() in ("lead", "front") else "rear"
        following = text[match.end():]
        declaration = re.match(rf"\s+(?:(?:is|starts|drives|travels)\s+)?(?:initially\s+)?(?:(?:in|on|at)\s+(?:the\s+)?)?(?:left|middle|right|same|{NUM}\s*(?:m|metres?|meters?|seconds?|s)\b)", following, re.I)
        if declaration and kind not in bindings:
            preference = [1, 2, 3, 4, 5, 6] if kind == "front" else [2, 1, 3, 4, 5, 6]
            role = next((i for i in preference if i not in used), None)
            if role is None:
                raise PromptInputError("Too many vehicles for a descriptive actor declaration.",
                                       clause=source_clause_at(text, match.start()))
            bindings[kind] = f"vehicle_{role}"
            used.add(role)
    def replace(match):
        kind = "front" if match[1].lower() in ("lead", "front") else "rear"
        if kind not in bindings:
            raise PromptInputError(f"Ambiguous {match[0]} reference: name vehicle 1/2/etc., or introduce that descriptive actor with its initial lane, gap and speed.",
                                   clause=source_clause_at(text, match.start()))
        return bindings[kind]
    return names.sub(replace, text)
