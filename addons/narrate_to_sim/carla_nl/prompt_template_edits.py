"""Context and checked, source-scoped edits for the Add to prompt forms."""
import math
import re

from .prompt_answers import _defined_numbers
from .prompt_language import normalize_prompt_characters, normalize_vehicle_language
from .prompt_recovery import raw_descriptions


def template_context(prompt):
    text = normalize_prompt_characters(prompt)
    try:
        text = normalize_vehicle_language(text)
    except ValueError:
        pass  # An unfinished draft still needs usable forms.
    from .natural_language_prompt import VEHICLE_EVENT
    ego = any(re.match(r'\s*ego\b', text[a:b], re.I) and not VEHICLE_EVENT.search(text[a:b])
              for a, b in raw_descriptions(text))
    return dict(ego=ego, **{kind: _defined_numbers(text, kind)
                           for kind in ('vehicle', 'obstacle', 'pedestrian')})


def next_number(context, kind):
    used = context[kind]
    return next((str(n) for n in range(1, 7 if kind == 'vehicle' else len(used) + 2)
                 if n not in used), '')


def validate_template_values(label, spec, values, prompt):
    """Report bad values before a builder can substitute its example defaults."""
    context = template_context(prompt)
    if label in ('takeover alert', 'collision takeover alert'):
        if values['trigger'] == 'Before collision (TTC)' and not context['obstacle']:
            raise ValueError('Add an Obstacle first, then choose it as the collision target.')
        if values['trigger'] == 'At time' and 'Visual' in values['mode'] and not context['vehicle']:
            raise ValueError('Visual target circles need a support vehicle. Add Vehicle ahead or Vehicle behind first, or choose Auditory.')
        return  # The alert builder validates its active modality/trigger fields.
    if label in ('front vehicle', 'rear vehicle') and len(context['vehicle']) >= 6:
        raise ValueError('All six vehicle numbers are in use. Edit an existing vehicle description or remove one before adding another.')
    numeric = {'n', 'd', 't', 'e', 'm', 'speed', 'gap', 'offset'}
    for key, title, kind, _default, options in spec['fields']:
        value = values[key].strip()
        if not value:
            if key == 'target' or (key == 'n' and label == 'timed gap target'):
                raise ValueError('Add Vehicle ahead or Vehicle behind first, then select that vehicle here.')
            if key in ('actor', 'reference') and not (context['ego'] or context['vehicle']):
                raise ValueError('Add an Ego vehicle with its initial lane and speed first, then select a vehicle here.')
            raise ValueError('Fill in {} before inserting.'.format(title))
        if kind == 'combo' and options and value not in options:
            raise ValueError('Choose {} from: {}.'.format(title, ', '.join(options)))
        if key == 'n' and not re.fullmatch(r'[1-9]\d*', value):
            raise ValueError('{} must be a positive whole number, e.g. 2.'.format(title))
        if key in numeric:
            try:
                number = float(value)
            except ValueError:
                raise ValueError('{} needs a number, e.g. 3.'.format(title)) from None
            if not math.isfinite(number) or number < 0 or (key in ('n', 'd') and number == 0):
                raise ValueError('{} must be {}.'.format(title, 'greater than zero' if key in ('n', 'd') else 'zero or greater'))
    namespaces = {'front vehicle': 'vehicle', 'rear vehicle': 'vehicle',
                  'obstacle barrier': 'obstacle', 'dropped obstacle': 'obstacle',
                  'pedestrian crossing': 'pedestrian'}
    if label in namespaces:
        namespace = namespaces[label]
        number = int(values['n'])
        if namespace == 'vehicle' and number > 6:
            raise ValueError('Use a vehicle number from 1 to 6.')
        if number in context[namespace]:
            raise ValueError('{} {} already exists. Use an unused number for a new {}.'.format(namespace.capitalize(), number, namespace))


def prepare_template_edit(prompt, label, snippet):
    """Replace singleton definitions; append actions without changing neighbors."""
    from .natural_language_prompt import DURATION, VEHICLE_EVENT
    from .prompt_clauses import SPEED
    singleton = label in ('scenario duration', 'ego lane/speed')
    targets = []
    canonical = snippet.strip().rstrip('.')
    if not singleton:
        existing = [prompt[a:b].strip().rstrip('.') for a, b in raw_descriptions(prompt)]
        added = [snippet[a:b].strip().rstrip('.') for a, b in raw_descriptions(snippet)]
        if added and any(existing[i:i + len(added)] == added for i in range(len(existing))):
            raise ValueError('This description is already in the prompt. Edit its existing line to change it.')
    for start, end in raw_descriptions(prompt):
        source = prompt[start:end]
        if not singleton and source.strip().rstrip('.') == canonical:
            raise ValueError('This description is already in the prompt. Edit its existing line to change it.')
        if not singleton:
            continue
        try:
            normalized = normalize_vehicle_language(source).strip()
        except ValueError:
            normalized = normalize_prompt_characters(source).strip()
        if label == 'scenario duration':
            matches = (re.match(r'(?:create|make|use|keep the total scenario duration)\b', normalized, re.I)
                       or re.match(r'(?:scenario\s+)?(?:duration|length|total time)\b', normalized, re.I))
            matches = matches and not re.search(r'\bego\b|\bvehicle_\d', normalized, re.I)
        else:
            matches = (re.match(r'(?:the\s+)?ego\b', normalized, re.I)
                       and not VEHICLE_EVENT.search(normalized)
                       and (re.search(r'\b(?:left|middle|right)\s+lane\b', normalized, re.I) or SPEED.search(normalized)))
        if matches:
            replacement = normalized.rstrip('.')
            if label == 'scenario duration':
                total = re.search(r'\d+(?:\.\d+)?', canonical)[0]
                def replace_total(match):
                    index = next(i for i, group in enumerate(match.groups(), 1) if group is not None)
                    left, right = match.start(index) - match.start(), match.end(index) - match.start()
                    return match[0][:left] + total + match[0][right:]
                if not DURATION.search(replacement):
                    raise ValueError('The existing duration description is incomplete. Use Analyze to repair it before changing its length.')
                replacement = DURATION.sub(replace_total, replacement)
            else:
                lane = re.search(r'\b(left|middle|right) lane\b', canonical, re.I)[0]
                speed = SPEED.search(canonical)[0]
                if re.search(r'\b(left|middle|right) lane\b', replacement, re.I):
                    replacement = re.sub(r'\b(left|middle|right) lane\b', lane, replacement, count=1, flags=re.I)
                else:
                    replacement = re.sub(r'\bego\b', 'Ego in the ' + lane, replacement, count=1, flags=re.I)
                replacement = SPEED.sub(lambda _m: speed, replacement, count=1) if SPEED.search(replacement) else replacement + ' at ' + speed
            targets.append((start, end, replacement))
    if targets:
        updated = prompt
        for index in range(len(targets) - 1, -1, -1):
            start, end, replacement = targets[index]
            updated = updated[:start] + replacement + updated[end:]
        return updated
    if label == 'ego lane/speed' and template_context(prompt)['ego']:
        raise ValueError('The Ego initial state shares a description with an action. Use Review to edit that description before adding a second initial state.')
    return prompt + ('\n' if prompt and not prompt.endswith('\n') else '') + snippet.rstrip() + '\n'


def validate_template_candidate(prompt, label, **conversion_options):
    """Return readiness; an initial definition may precede the missing Ego."""
    from .scanner_prompt_adapter import convert_prompt
    try:
        preview, items = convert_prompt(prompt, **conversion_options)
    except ValueError as error:
        requests = getattr(error, 'repair_requests', ())
        if (label in ('scenario duration', 'front vehicle', 'rear vehicle') and requests
                and all(request['role'] == 'ego' and not request.get('clause') for request in requests)):
            return False  # Keep the supplied definition; ask only for missing Ego facts.
        raise
    if not preview.ok or not items:
        raise ValueError(getattr(preview, 'message', '') or 'Complete the existing scenario before inserting this description.')
    selected_map = conversion_options.get('map_name')
    if selected_map and items[0].scenario['map_name'] != selected_map:
        raise ValueError('The map written in the prompt differs from the selected Map. Choose the same map before inserting.')
    return True
