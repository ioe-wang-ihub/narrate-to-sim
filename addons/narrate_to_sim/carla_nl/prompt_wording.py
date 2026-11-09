"""Composable English wording, shared by routing, parsing and repair matching.

Only affirmative, explicit grammatical forms are rewritten. This is not a bag
of stop words: unknown modifiers, conditions and negative clauses survive for
the checked reader. Numeric values and actor references are never invented.
"""
import re

from .prompt_english import normalize_english_quantities

NUM = r'\d+(?:\.\d+)?'
SEC = r'(?:seconds?|secs?|s)'
TIME = rf'({NUM})\s*-?\s*{SEC}\b'
ACTOR = r'(?:ego|vehicle_[1-6])'
CARDINAL = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
ORDINAL = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6}
SCENE = r'(?:simulation\s+run|(?:driving\s+)?(?:scenario|simulation))'
ACTION_HEAD = (r'(?:chang(?:e[sd]?|ing)|switch(?:es|ing)?|mov(?:e[sd]?|ing)|merg(?:e[sd]?|ing)|'
               r'shift(?:s|ing)?|turns?|go(?:es|ing)?|make[sd]?\s+a\s+lane\s+change|'
               r'perform[sd]?\s+a\s+lane\s+change|brak(?:e[sd]?|ing)|slow(?:s|ing)?\s+down|'
               r'decelerat(?:e[sd]?|ing)|accelerat(?:e[sd]?|ing)|speed(?:s|ing)?\s+up|'
               r'(?:reduc(?:e[sd]?|ing)|lower(?:s|ing)?|decreas(?:e[sd]?|ing)|drop(?:s|ping)?|cuts?)\s+(?:(?:its|the)\s+)?speed|'
               r'bring(?:s|ing)?\s+(?:(?:its|the)\s+)?speed\s+down)\b')


def _sub(pattern, replacement, text):
    return re.sub(pattern, replacement, text, flags=re.I)


def _start_time_sub(pattern, text):
    def replace(match):
        if re.search(r'\b(?:by|before|until|no\s+later\s+than)\s*$', text[:match.start()], re.I):
            return match[0]
        return f'at {match[1]} seconds'
    return _sub(pattern, replace, text)


def normalize_conversational_wording(text: str) -> str:
    """Normalize an unquoted chunk; caller preserves speech payloads."""
    names = r'(?:vehicles?|cars?|npc)'
    number = r'(?:[1-6]|one|two|three|four|five|six)'
    # Named IDs bind identically in declarations, event subjects and references.
    text = _sub(rf'\b(?:the\s+)?(?:(?:traffic|other|support)\s+)?{names}\s+(?:numbered|number|no\.)\s*({number})\b',
                lambda m: 'vehicle_' + str(CARDINAL.get(m[1].lower(), m[1])), text)
    text = _sub(rf'\b(?:the\s+)?(?:(?:traffic|support)\s+)?{names}[ _#-]*({number})\b',
                lambda m: 'vehicle_' + str(CARDINAL.get(m[1].lower(), m[1])), text)
    text = _sub(rf"\b(?:the\s+)?({'|'.join(ORDINAL)})\s+(?:car|vehicle)\b",
                lambda m: 'vehicle_' + str(ORDINAL[m[1].lower()]), text)
    text = _sub(r"\b(?:the\s+)?(?:ego(?:[ _-]+(?:vehicle|car))?|(?:host|subject|hero)\s+(?:vehicle|car)|(?:my|our)\s+(?:own\s+)?(?:vehicle|car))\b", 'ego', text)
    text = normalize_english_quantities(text)

    # Complete duration clauses only. Matching within an event or consuming an
    # arbitrary suffix would erase unsupported constraints such as conditions.
    headers = [
        rf'(?:run|let\s+the)\s+(?:the\s+)?{SCENE}\s+(?:run\s+)?for\s+{TIME}',
        rf'(?:make|have)\s+(?:the\s+)?{SCENE}\s+last\s+{TIME}',
        rf'set\s+(?:the\s+)?{SCENE}\s+duration\s+to\s+{TIME}',
        rf'give\s+(?:the\s+)?{SCENE}\s+a\s+duration\s+of\s+{TIME}',
        rf'use\s+a\s+duration\s+of\s+{TIME}\s+for\s+(?:the\s+)?{SCENE}',
        rf'(?:the\s+)?{SCENE}\s+(?:(?:(?:should|will)\s+|needs\s+to\s+)?lasts?|(?:(?:should|will)\s+)?runs?\s+for|has\s+a\s+duration\s+of)\s+{TIME}',
        rf'(?:the\s+)?{SCENE}\s*\'s\s+duration\s+is\s+{TIME}',
        rf'(?:the\s+)?total\s+(?:duration|running\s+time)\s+of\s+(?:the\s+)?{SCENE}\s+is\s+{TIME}',
        rf'for\s+(?:the\s+)?{SCENE}\s*,\s*use\s+a\s+total\s+duration\s+of\s+{TIME}',
        rf'(?:create|make|use)\s+a\s+{TIME}\s+{SCENE}',
        rf'I\s+want\s+(?:the\s+)?{SCENE}\s+to\s+last\s+{TIME}',
    ]
    for pattern in headers:
        text = _sub(rf'(?<![\w]){pattern}(?=\s*(?:[.;\n]|$))',
                    r'Create a \1-second scenario', text)

    # Explicit start-time syntax. A bare quantity before 'ahead' is untouched.
    text = _start_time_sub(rf'\bat\s+(?:the\s+)?{TIME}\s+mark\b', text)
    text = _start_time_sub(rf'\bat\s+(?:an\s+)?(?:elapsed\s+time|simulation\s+time|scenario\s+time)\s*(?:of\s+|=\s*|:\s*)?{TIME}', text)
    text = _start_time_sub(rf'\b{TIME}\s+(?:after\s+(?:the\s+)?(?:scenario|simulation)\s+(?:starts|begins)|into\s+the\s+(?:scenario|simulation))\b', text)
    text = _start_time_sub(rf'\b(?:when|once)\s+the\s+(?:scenario|simulation|scenario\s+clock|simulation\s+clock)\s+reaches\s+{TIME}', text)
    text = _start_time_sub(rf'\b(?:once|after|when)\s+{TIME}\s+have\s+elapsed\b', text)
    text = _start_time_sub(rf'\bat\s+{TIME}\s+on\s+the\s+(?:scenario|simulation)\s+clock\b', text)

    # Initial state phrases are scoped to an actor, not to event timing.
    text = _sub(rf'\b({ACTOR})\s+starts?\s+(?:off|out)\b', r'\1 starts', text)
    text = _sub(rf'\b({ACTOR})\s+(?:begins?|(?:will|should|shall)\s+start)\s+(?=in\b|at\b|driving\b|travelling\b|traveling\b|{NUM}\s*(?:m|metres?|meters?|{SEC})\b)', r'\1 starts ', text)
    text = _sub(rf'\bhave\s+({ACTOR})\s+start\b', r'\1 starts', text)
    text = _sub(rf'\b(?:start|place|put|set\s+up)\s+({ACTOR})\s+(?=in\b|at\b)', r'\1 starts ', text)
    text = _sub(rf'\b(?:initially|at\s+the\s+start(?:\s+of\s+the\s+(?:scenario|simulation))?)\s*,?\s*({ACTOR})\b', r'\1 initially', text)
    text = _sub(rf'\b({ACTOR}(?:\s+initially)?)\s+occupies\s+(?:the\s+)?(left|middle|right)\s+lane\b', r'\1 is in the \2 lane', text)
    text = _sub(rf'\b({ACTOR}(?:\s+initially)?)\s+is\s+moving\s+at\s+(?={NUM}\s*(?:mph|km/h|kmh|kph|m/s)\b)', r'\1 is at ', text)
    text = _sub(rf',\s*with\s+(?={ACTOR}\b)', ', ', text)

    # Obstacle introduction and appearance are independent of its spatial gap.
    text = _sub(r'\bposition\s+(?=(?:a|an|the|one)\s+(?:obstacle|cone|traffic\s+cone|barrier|barrel|box)\b)', 'place ', text)
    text = _sub(rf'\bwith\s+an?\s+appearance\s+time\s+of\s+{TIME}', r'at \1 seconds', text)
    text = _sub(r'\b(?:appearing|to\s+appear)\s+at\b', 'appears at', text)

    # Consume full braking phrases before shorter 'brake'/'reduce speed' rules.
    text = _sub(r'\bappl(?:y|ies|ying)\s+(?:(?:its|the)\s+)?brakes?\s+to\s+(?:reduce\s+(?:(?:its|the)\s+)?speed\s+to|slow\s+down\s+to|reach)\b', 'brakes to', text)
    text = _sub(r'\bappl(?:y|ies|ying)\s+(?:(?:its|the)\s+)?brakes?\s+to\b', 'brakes to', text)
    text = _sub(r'\b(?:lower(?:s|ing)?|decreas(?:e[sd]?|ing)|cuts?)\s+(?:(?:its|the)\s+)?speed\b|\bbring(?:s|ing)?\s+(?:(?:its|the)\s+)?speed\s+down\b', 'decelerates', text)

    # Affirmative action shells compose with every supported action. Keep
    # uncertain/negative auxiliaries visible; they are not equivalent commands.
    text = _sub(rf'\b(?:have|make)\s+({ACTOR})\s+(?={ACTION_HEAD})', r'\1 ', text)
    text = _sub(rf'\b(?:is\s+to|(?:begins?|starts?)\s+to|(?:will|should|shall))\s+(?={ACTION_HEAD})', '', text)
    text = _sub(rf'\band\s+takes?\s+{TIME}\s+to\s+reach\s+', r'over \1 seconds to ', text)
    return text
