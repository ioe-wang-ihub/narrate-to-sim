"""Resolve local actor/state/action clauses without inventing scenario values."""
import re

NUM = r"\d+(?:\.\d+)?"
TIME = rf"(?:(?:at|after)\s+)?{NUM}\s*(?:seconds?|secs?|s)\b(?:\s+after\s+(?:completing|starting)\s+the\s+previous\s+(?:stationary\s+)?action)?\s*[:,]?"
ACTOR = r"(?:ego|vehicle_[1-6]|(?:a|one|the|that|this)\s+(?:lead\s+)?car|it)\b"
ACTION = r"(?:changes?\s+(?:into|to)|moves?\s+(?:into|to)|turns?\s+(?:left|right)|starts?\s+changing|begins?\s+changing|decelerates|accelerates|adjusts speed)\b"
SPEED = re.compile(rf"{NUM}\s*(?:km/h|kmh|kph|mph|m/s|metres per second|meters per second|公里每小时|千米每小时)\b", re.I)


def normalize_vehicle_clauses(text: str) -> str:
    result = []
    for sentence in re.split(r"[.!?](?=\s|$)|[。！？;\r\n]+", text):
        sentence = sentence.strip(" ,")
        if not sentence:
            continue
        # A comma may introduce another actor or a timed continuation of the
        # current actor. The comma following an event's time is not a boundary.
        cuts = [0]
        joins = [""]
        for match in re.finditer(r",\s*(?:(?:and\s+)?then\s+|(?:and|while)\s+)?|\s+(?:and\s+)?then\s+|\s+(?:and|while)\s+(?=" + ACTION + "|" + ACTOR + ")", sentence, re.I):
            if re.fullmatch(TIME, sentence[cuts[-1]:match.start()].strip(), re.I):
                continue
            right = sentence[match.end():]
            has_subject = re.search(rf"\b{ACTOR}", sentence[:match.start()], re.I)
            next_subject = re.match(ACTOR, right, re.I)
            next_event = re.match(rf"{TIME}\s*(?:{ACTOR}\s+)?{ACTION}", right, re.I)
            following_action = re.match(ACTION, right, re.I)
            left = sentence[cuts[-1]:match.start()]
            if (following_action and not re.search(ACTION, left, re.I)
                    and not SPEED.search(left) and not re.search(r'\bsame speed\b', left, re.I)
                    and not re.match(r'decelerates|accelerates|adjusts speed', right, re.I)):
                # Keep compact lane clauses together so their single trailing
                # speed can still supply the initial state below.
                following_action = None
            if has_subject and (next_subject or next_event or following_action):
                cuts.extend([match.start(), match.end()])
                joins.append(match[0].strip(" ,").lower())
        cuts.append(len(sentence))
        fragments = [sentence[cuts[i]:cuts[i + 1]].strip(" ,") for i in range(0, len(cuts) - 1, 2)]
        local_actor = None
        previous_action = False
        for fragment, join in zip(fragments, joins):
            # Bare '10s turn left' is an absolute time in a timed action clause.
            fragment = re.sub(rf"^({NUM}\s*(?:seconds?|secs?|s))\s*[:,]?\s*(?={ACTOR}|{ACTION})", r"At \1, ", fragment, flags=re.I)
            timed = re.match(rf"^({TIME})\s*", fragment, re.I)
            body_start = timed.end() if timed else 0
            body = fragment[body_start:]
            actor = re.match(rf"(?:the\s+)?({ACTOR})", body, re.I)
            if actor:
                if actor[1].lower() == "it" and local_actor and re.fullmatch(r"ego|vehicle_[1-6]", local_actor, re.I):
                    fragment = fragment[:body_start] + local_actor + body[actor.end():]
                else:
                    local_actor = actor[1]
            elif local_actor and re.match(ACTION, body, re.I):
                fragment = fragment[:body_start] + local_actor + " " + body
            has_time = timed or re.search(rf"\b(?:at|after)\s+{NUM}\s*(?:seconds?|secs?|s)\b", fragment, re.I)
            if not has_time and previous_action and re.search(ACTION, fragment, re.I) and join in ("and", "while", "then", "and then"):
                reference = "completing" if "then" in join else "starting"
                fragment = f"0 seconds after {reference} the previous clause action, " + fragment
            elif join in ("and", "while", "then", "and then"):
                # 'Then two seconds later ego ...' refers to the immediately
                # preceding clause even when its subject is a different car.
                fragment = re.sub(r"\bprevious action\b", "previous clause action", fragment, flags=re.I)

            # 'ego at right go middle at 10s 60mph' combines an initial state
            # with an action. A single speed belongs to that initial state.
            subject = re.match(r"(?:the\s+)?(ego|vehicle_[1-6])\b", fragment, re.I)
            action = re.search(ACTION, fragment, re.I)
            if subject and action and re.search(r"\b(?:left|middle|right|same|ego) lane\b", fragment[:action.start()], re.I):
                initial, event = fragment[:action.start()].strip(" ,"), fragment[action.start():]
                initial = re.sub(r"\s+(?:and\s+then|and|then)$", "", initial, flags=re.I)
                speeds = list(SPEED.finditer(fragment))
                if len(speeds) == 1 and speeds[0].start() >= action.start() and not re.match(r"decelerates|accelerates|adjusts speed", event, re.I):
                    speed = speeds[0]
                    initial += " at " + speed[0]
                    offset = action.start()
                    event = event[:speed.start() - offset] + " " + event[speed.end() - offset:]
                result.append(initial)
                result.append(subject[1] + " " + event)
            else:
                result.append(fragment)
            previous_action = bool(re.search(ACTION, fragment, re.I))
    return ".\n".join(result)
