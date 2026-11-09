"""Lexical and sentence normalization shared by the checked vehicle reader.

No scenario values are invented here. Unconsumed words remain for validation.
"""
import html
import re
import unicodedata

from .prompt_clauses import normalize_vehicle_clauses
from .prompt_english import correct_common_spellings, normalize_english_phrases, normalize_speed_assignments, CHECKED_SPEED_ACTION
from .prompt_scene_entities import normalize_entity_introductions, normalize_pedestrian_shorthand
from .prompt_positions import extract_position, extract_ego_vehicle_relation
from .prompt_feedback import PromptInputError
from .prompt_wording import normalize_conversational_wording

NUM = r"\d+(?:\.\d+)?"
SECONDS_UNIT = r"(?:seconds?|secs?|s)"
SCENARIO_DURATION_HEADER = re.compile(
    rf"(?im)(?:^|(?<=[.;\n]))\s*(?:scenario\s+)?(?:duration|length|total time)\s*(?:=|:|is|of)?\s*({NUM})\s*{SECONDS_UNIT}\b")
NUMBERED_VEHICLE_RE = re.compile(r"(?<![a-z0-9_])(?:vehicle|car|v)[ \t_#-]*([1-6])(?![a-z0-9_])", re.I)
LANE_VERBS = r"(?:go(?:es|ing)?|mov(?:e[sd]?|ing)|merg(?:e[sd]?|ing)|chang(?:e[sd]?|ing)|switch(?:es|ing)?|shift(?:s|ing)?|head(?:s|ing)?)"
RELATIVE_LANE_ACTION = re.compile(rf"(?<!lane )\b{LANE_VERBS}\s+(left|right)\b(?!\s+(?:lane|into|to)\b)", re.I)
ABSOLUTE_LANE_ACTION = re.compile(rf"(?<!lane )\b(?:{LANE_VERBS}(?:\s+lanes?)?(?:\s+over)?\s+(?:(?:to|into)\s+)?|turns?\s+(?:to|into)\s+)(?:the\s+)?(left|middle|right|ego)(?:\s+lane)?\b(?!\s+(?:into|to)\b)", re.I)


def conversational_lane_actions(text: str):
    """Share action recognition between routing and normalization."""
    return [m for pattern in (RELATIVE_LANE_ACTION, ABSOLUTE_LANE_ACTION) for m in pattern.finditer(text)]


def _timing_action_kind(sentence):
    kinds = []
    if re.search(r'\bdecelerates\b', sentence, re.I):
        kinds.append('decrease')
    if re.search(r'\baccelerates\b', sentence, re.I):
        kinds.append('increase')
    if re.search(r'\badjusts speed\b', sentence, re.I):
        kinds.append('speed')
    if re.search(r'\b(?:changes? into|turns? (?:left|right)|moves? into)\b', sentence, re.I):
        kinds.append('lane')
    return kinds[0] if len(kinds) == 1 else None


def _check_timing_kind(sentence, expected):
    actual = _timing_action_kind(sentence)
    if actual is None or (expected != 'any' and actual != expected):
        names = {'lane': 'lane change', 'decrease': 'braking', 'increase': 'acceleration', 'any': 'action'}
        raise PromptInputError(
            f'The duration/completion refers to {names[expected]}, but the associated action is different or ambiguous. Put each action and its own timing in one sentence. / 用時或完成時間所指的動作不一致，請把時間寫回對應動作的同一句。',
            clause=sentence, code='timing_reference')


def normalize_prompt_characters(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "→": "->", "⇒": "->", "−": "-", "\u200b": None, "\ufeff": None}))
    text = correct_common_spellings(text)
    # Text copied from Markdown may escape an identifier's underscore. Speech
    # payloads retain their literal backslashes and are handled separately.
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'\b(vehicle|car|v|npc|obstacle|pedestrian|ped|walker|person)\\_(?=\d|<number>)',
                             r'\1_', parts[index], flags=re.I)
    return ''.join(parts)


def normalize_vehicle_aliases(text: str) -> str:
    return normalize_reader_wording(text)[0]


def normalize_reader_wording(text: str) -> tuple[str, bool]:
    # Resolve aliases before choosing a reader. In particular, a DSL header
    # must not make 'car 1' work differently from 'Vehicle 1'. Speech is payload.
    # Remember expanded grammar: normalizing it into legacy-looking text must
    # not allow the field-extracting scanner to discard unknown intent/defaults.
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    expanded = False
    for index in range(0, len(parts), 2):
        parts[index] = NUMBERED_VEHICLE_RE.sub(r"vehicle_\1", parts[index])
        parts[index] = re.sub(r"\bego\s+(?:vehicle|car)\b", "ego", parts[index], flags=re.I)
        normalized = normalize_speed_assignments(normalize_conversational_wording(parts[index]))
        expanded = expanded or normalized.casefold() != parts[index].casefold()
        parts[index] = normalized
    return "".join(parts), expanded


def normalize_vehicle_language(text: str) -> str:
    text = normalize_vehicle_aliases(normalize_prompt_characters(text))
    text = normalize_english_phrases(text)
    text = re.sub(r"\bone and a half\b", "1.5", text, flags=re.I)
    words = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety".split(), list(range(21)) + list(range(30, 100, 10))))
    for word, value in sorted(words.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"\b{word}\b(?=\s*(?:seconds?\b|mph\b|miles\b|-lane\b))", str(value), text, flags=re.I)
        text = re.sub(rf"\bvehicle\s+{word}\b", f"vehicle_{value}", text, flags=re.I)
    text = re.sub(r"\b(?:miles per hour|miles an hour|miles/hour)\b|英里[/／]小时", "mph", text, flags=re.I)
    text = re.sub(r"\b(?:kilomet(?:er|re)s? per hour|kilomet(?:er|re)s?/hour|kmph)\b", "km/h", text, flags=re.I)
    text = re.sub(r"\b(?:mps|meters/second|metres/second|met(?:er|re)s? per second)\b(?!\s+squared)", "m/s", text, flags=re.I)
    text = re.sub(r"\b(?:center|centre)\b", "middle", text, flags=re.I)
    text = re.sub(r"\b(?:ego(?:\s+vehicle)?'s|my)\s+lane\b", "ego lane", text, flags=re.I)
    text = re.sub(r"\b(?:I'm|I am|my vehicle)\b", "Ego", text, flags=re.I)
    text = re.sub(r"\b(?:hero|host vehicle|my car)\b", "ego", text, flags=re.I)
    text = re.sub(r"(?<![a-z0-9_])(?:(?:vehicle|car)[ _#-]*|v)([1-6])(?![a-z0-9_])|车辆\s*([1-6])", lambda m: "vehicle_" + (m[1] or m[2]), text, flags=re.I)
    text = text.replace("三车道高速公路", "Road: 3-lane highway. ")
    text = text.replace("自车所在车道", "ego lane").replace("自车", "ego")
    text = re.sub(rf"({NUM})\s*秒场景", r"\1-second scenario", text)
    text = text.replace("只有一辆前车", "A lead car").replace("在同一条", "in the ")
    text = re.sub(rf"ego(前方|后方)\s*({NUM})\s*米处?", lambda m: f" {m[2]} metres {'ahead of' if m[1] == '前方' else 'behind'} ego ", text)
    text = re.sub(rf"场景\s*({NUM})\s*秒", r"Create a \1-second scenario", text)
    text = re.sub(rf"(?:位于)?ego前方\s*({NUM})\s*秒", r" \1 seconds ahead of ego ", text)
    text = re.sub(rf"(?:位于)?ego后方\s*({NUM})\s*秒", r" \1 seconds behind ego ", text)
    text = re.sub(rf"第\s*({NUM})\s*秒|({NUM})\s*秒时", lambda m: "At " + (m[1] or m[2]) + " seconds, ", text)
    for chinese, english in (("中间车道", "middle lane"), ("中车道", "middle lane"), ("右车道", "right lane"), ("左车道", "left lane")):
        text = text.replace(chinese, " " + english + " ")
    text = re.sub(r"(?:开始)?向(左|右)变道进入", lambda m: " changes " + ("left" if m[1] == "左" else "right") + " into ", text)
    text = re.sub(r"(?:开始)?(?:变道到|切入|变道进入)", " changes into ", text)
    text = re.sub(r"变道持续|用时|持续", " over ", text)
    text = re.sub(rf"({NUM})\s*秒", r"\1 seconds", text)
    text = re.sub(r"(?:速度(?:也是|也|是)?|也?以)(?=\s*\d)", " at ", text)
    text = text.replace("行驶", " ").replace("在", " in ")
    text = re.sub(r"\bego\s+vehicle\b", "ego", text, flags=re.I)
    text = re.sub(r"\b(?:ahead|behind) of me\b", lambda m: m[0].replace("me", "ego"), text, flags=re.I)
    text = re.sub(r"(?m)^\s*[-•]\s*", "", text)
    text = re.sub(r"\bEvent\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\bplease\s+(?=create|make|use)", "", text, flags=re.I)
    text = re.sub(r"\b(?:I'd like|I would like|I want)\s+(?:to\s+create\s+)?(?=a\s+\d)", "Create ", text, flags=re.I)
    text = re.sub(rf"\b({NUM})\s+seconds?\s+(?=(?:Town\d+(?:_Opt)?\s+)?scenario\b)", r"\1-second ", text, flags=re.I)
    text = re.sub(r"\bscenario\s+where\s+(?=ego\b|vehicle_[1-6]\b)", "scenario. ", text, flags=re.I)
    text = re.sub(r"\bWhen the scenario starts,\s*", "", text, flags=re.I)
    text = re.sub(r"\bOn a (?:three|3)-lane highway,\s*", "Road: 3-lane highway. ", text, flags=re.I)
    text = normalize_entity_introductions(text)
    text = re.sub(r"\b(?:put|have)\s+(?=ego\b|(?:a|that|this) car\b)", "", text, flags=re.I)
    text = re.sub(rf"({NUM}) seconds after the start", r"At \1 seconds", text, flags=re.I)
    text = re.sub(r"\b(?:the\s+)?maneu?ver\s+lasts?\s+", "over ", text, flags=re.I)
    text = re.sub(r"\b(?:the\s+)?manoeuvre\s+lasts?\s+", "over ", text, flags=re.I)
    # Normalize only core DSL fields when a prose header or event selected
    # this reader. Extension fields remain visible and are rejected here.
    text = SCENARIO_DURATION_HEADER.sub(
        lambda m: m[0][:len(m[0]) - len(m[0].lstrip())] + f"Create a {m[1]}-second scenario", text)
    text = re.sub(r"\blane\s*=\s*(left|middle|right)\b", r"in the \1 lane", text, flags=re.I)
    text = re.sub(r"\b(ego|vehicle_[1-6])\s+lane\s+(left|middle|right)\b", r"\1 in the \2 lane", text, flags=re.I)
    text = re.sub(r"\bspeed\s*=\s*", "at ", text, flags=re.I)
    text = re.sub(rf"\b(ahead|behind)\s*=\s*({NUM})\s*{SECONDS_UNIT}\b", r"\2 seconds \1", text, flags=re.I)
    text = re.sub(rf"\b(ahead of|behind)\s+(?:the\s+)?ego\s+by\s+({NUM})\s*(seconds?|secs?|s|metres?|meters?|m)\b", r"\2 \3 \1 ego", text, flags=re.I)
    text = re.sub(rf"\b(ahead|behind)\s+({NUM})\s*(seconds?|secs?|s|metres?|meters?|m)\b", r"\2 \3 \1", text, flags=re.I)
    # A named event duration uses the same units with or without DSL punctuation.
    # Scenario headers were consumed above, so this cannot change total time.
    text = re.sub(rf"\b(?:with\s+(?:a\s+)?)?duration\s*(?:(?:=|:|is\b|of\b)\s*)?({NUM})\s*{SECONDS_UNIT}\b", r"over \1 seconds", text, flags=re.I)
    text = re.sub(rf"\b(?:at\s+)?t\s*=\s*({NUM})(?:\s*{SECONDS_UNIT})?\b\s*:", r"At \1 seconds, ", text, flags=re.I)
    text = re.sub(r"\b(ego|vehicle_[1-6])\s*(?:->|=>)\s*(left|middle|right)\b", r"\1 changes into the \2 lane", text, flags=re.I)
    text = re.sub(rf"\b(?:at\s+)?t\s*=\s*({NUM})(?:\s*{SECONDS_UNIT})?\b", r"at \1 seconds", text, flags=re.I)
    text = re.sub(r'(^|[.!?;\n]\s*)at\s+0(?:\.0+)?\s+seconds\s*,?\s*(?=(?:ego|vehicle_[1-6])\s+in\s+the\s+(?:left|middle|right)\s+lane\b)',
                  r'\1', text, flags=re.I)
    text = re.sub(rf"@\s*({NUM})\s*(?:seconds?|secs?|s)\b", r"at \1 seconds", text, flags=re.I)
    text = re.sub(rf"({NUM})\s*(?:seconds?|secs?|s)\s+into\s+the\s+scenario\b", r"at \1 seconds", text, flags=re.I)
    # Explicit 'to' names a destination. A bare 'turn left/right' remains a
    # relative adjacent-lane command, checked against the actor's current lane.
    text = RELATIVE_LANE_ACTION.sub(r"turn \1", text)
    text = ABSOLUTE_LANE_ACTION.sub(r"changes into the \1 lane", text)
    # Consume an affirmative progressive auxiliary only before an already
    # recognized action. Qualifiers such as 'not' or 'might' remain checked.
    text = re.sub(r"\b(?:is|are)\s+(?=changes?\b|turns?\b|decelerates\b|accelerates\b|adjusts speed\b)", "", text, flags=re.I)
    text = re.sub(r"\b(?:should|will|shall)\s+(?=changes?\b|turns?\b|moves?\b)", "", text, flags=re.I)
    text = re.sub(rf"\band\s+take\s+({NUM})\s*(?:seconds?|secs?|s)\s+to\s+complete\s+(?:the\s+)?lane\s+change\b", r"over \1 seconds", text, flags=re.I)
    # Initial lane shorthand, including 'ego right' and 'v1 at middle'.
    text = re.sub(r"\b(?:in|on)\s+(?:the\s+)?(left|middle|right)(?:\s+lane)?\b", r"in the \1 lane", text, flags=re.I)
    text = re.sub(r"\b(ego|vehicle_[1-6])\s*(?::\s*)?(?:(?:(?:is|starts)\s+)?(?:at|in|on)\s+(?:the\s+)?)?(left|middle|right)(?!\s+lane\b)\b", r"\1 in the \2 lane", text, flags=re.I)
    text = normalize_vehicle_clauses(text)
    # Attach a duration-only sentence to the immediately preceding action.
    sentences = []
    for sentence in re.split(r"[.!?](?=\s|$)|[。！？;\r\n]+", text):
        sentence = re.sub(r"\s+", " ", sentence).strip(" ,")
        if not sentence:
            continue
        sentence = normalize_pedestrian_shorthand(sentence)
        if re.search(r'\b(?:changes?|turns?|moves?|merges?|switches?|go(?:es)?)\b', sentence, re.I):
            sentence = re.sub(r'\b(from|to|into)\s+(?:the\s+)?(left|middle|right)(?!\s+lane\b)\b',
                              r'\1 the \2 lane', sentence, flags=re.I)
        for completion in re.finditer(r'\bcompletion_kind=(lane|decrease|increase|any)\b', sentence):
            _check_timing_kind(sentence[:completion.start()], completion[1])
        sentence = re.sub(r'\s*\bcompletion_kind=(?:lane|decrease|increase|any)\b', '', sentence)
        if re.search(r"\b(?:changes? into|turns? (?:left|right))\b", sentence, re.I):
            sentence = re.sub(rf"\b(?:for|lasting)\s+({NUM})\s*(?:seconds?|secs?|s)\b", r"over \1 seconds", sentence, flags=re.I)
        elif CHECKED_SPEED_ACTION.search(sentence):
            sentence = re.sub(rf'\blasting\s+({NUM})\s*{SECONDS_UNIT}\b', r'over \1 seconds', sentence, flags=re.I)
            # "stops for 4s" can be a stationary hold. Nonzero targets or
            # speed differences followed by "for" describe the speed ramp.
            if re.search(rf'\b(?:to|by)\s+(?!0+(?:\.0+)?\s*(?:km/h|mph|kph|kmh|m/s)\b){NUM}\s*(?:km/h|mph|kph|kmh|m/s)\b', sentence, re.I):
                sentence = re.sub(rf'\bfor\s+({NUM})\s*{SECONDS_UNIT}\b', r'over \1 seconds', sentence, flags=re.I)
        elif extract_ego_vehicle_relation(sentence) is not None:
            # The reader checks this against the named car's initial state.
            # Keep the original subject/reference rather than treating car as ego.
            sentence = re.sub(r"\bat\s+0(?:\.0+)?\s*(?:seconds?|secs?|s)\b", " ", sentence, flags=re.I)
        elif re.match(r"(?:the\s+)?(?:ego|vehicle_[1-6])\b", sentence, re.I) and not CHECKED_SPEED_ACTION.search(sentence):
            sentence, position = extract_position(sentence)
            sentence = re.sub(r"\bat\s+0(?:\.0+)?\s*(?:seconds?|secs?|s)\b", " ", sentence, flags=re.I)
            if position is not None:
                sentence += ' ' + position.phrase()
        duration_sentence = re.fullmatch(rf'(?:duration_kind=(lane|decrease|increase|any)\s+)?(over {NUM}\s*{SECONDS_UNIT})', sentence, re.I)
        if duration_sentence:
            if not sentences or not re.search(r"chang|mov|merg|cut|switch|turn|->|decelerates|accelerates|adjusts speed", sentences[-1], re.I):
                raise PromptInputError(f"Duration has no preceding maneuver: {sentence}", clause=sentence)
            _check_timing_kind(sentences[-1], duration_sentence[1] or 'any')
            sentences[-1] += " " + duration_sentence[2]
        else:
            sentences.append(sentence)
    return ".\n".join(sentences) + "."
