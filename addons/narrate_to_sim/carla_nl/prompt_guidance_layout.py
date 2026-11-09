"""Presentation of authoring diagnostics, separate from parsing and repair edits."""
import re


def question_source_context(prompt, start, end, *, field=False):
    """Show the owning description even when several vehicles share a line."""
    if field:
        from .takeover_alerts import alert_boundary_mask
        mask = re.sub(r'"(?:\\.|[^"\\])*"|&(?:#\w+|\w+);', lambda m: ' ' * len(m[0]), prompt)
        mask = alert_boundary_mask(mask)
        subject = r'(?:ego\b|(?:vehicle|car|v|obstacle|pedestrian)[ _#-]*(?:\d+|<number>))'
        boundaries = list(re.finditer(r'[.!?](?=\s|$)|[。！？;\r\n]+|[,，]\s*(?=(?:and\s+)?' + subject +
                                     r')|\s+and\s+(?=' + subject + ')', mask, re.I))
        left = max((m.end() for m in boundaries if m.end() <= start), default=0)
        right = min((m.start() for m in boundaries if m.start() >= end), default=len(prompt))
    else:
        left, right = start, end
    source = ' '.join(prompt[left:right].strip().split())
    line = prompt.count('\n', 0, start) + 1
    return f'Your description (line {line}):\n“{source}”' if source else ''


def english_guidance(text):
    """Remove bilingual explanation suffixes, preserving the English details."""
    lines = []
    for line in text.splitlines():
        parts = line.split(' / ')
        for index, part in enumerate(parts[1:], 1):
            if re.search(r'[\u3400-\u9fff]', part):
                line = ' / '.join(parts[:index])
                break
        lines.append(line)
    return '\n'.join(lines)


def grouped_guidance(error, prompt=''):
    """Keep each description's missing facts immediately beside its template.

    Return text plus line styles; the template stays byte-for-byte identical to
    the diagnostic so repair matching and right-click copying remain reliable.
    """
    if getattr(error, 'code', '') == 'placeholders':
        from .prompt_feedback import PLACEHOLDER_HINTS
        rows = [('guidance_heading', 'Unfilled template fields'),
                ('guidance_context', 'Answer below, or fill the highlighted <fields> directly in your prompt.')]
        for number, (start, end) in enumerate(error.placeholder_fields, 1):
            token = prompt[start:end]
            line = prompt.count('\n', 0, start) + 1
            hint = english_guidance(PLACEHOLDER_HINTS.get(token[1:-1].lower(), 'Enter a value.'))
            rows += [('', ''), ('guidance_role', f'{number:02d}   {token} · Line {line}'),
                     ('guidance_missing', '• ' + hint)]
        return '\n'.join(text for _, text in rows), [tag for tag, _ in rows]
    requests = getattr(error, 'repair_requests', ())
    if not requests:
        return None
    roles = list(dict.fromkeys(request['role'] for request in requests))
    requests = sorted(requests, key=lambda request: roles.index(request['role']))
    rows = []
    for number, request in enumerate(requests, 1):
        role = request['role'].replace('_', ' ').capitalize()
        if rows:
            rows.append(('', ''))
        rows.append(('guidance_role', f'{number:02d}   {role}'))
        for field in dict.fromkeys(request.get('fields', ())):
            # Keep the English meaning once; do not repeat every sentence in
            # two languages. Examples and the next-action hint carry the flow.
            field = english_guidance(field)
            if field.startswith('initial lane:'):
                field = 'Choose an initial lane: left, middle or right.'
            elif field.startswith('initial speed with units?'):
                field = 'Add the initial speed and unit, e.g. 60 mph.'
            elif field.startswith(('event duration, completion time, or acceleration/deceleration rate:',
                                   'event duration or completion time')):
                rows.append(('guidance_missing', '• Add the event duration, e.g. over 3s.'))
                rows.append(('guidance_context', 'You can also give a completion time' +
                             (' or acceleration/deceleration rate (m/s²).' if 'rate:' in field else '.')))
                continue
            rows.append(('guidance_missing', '• ' + field))
        rows.append(('guidance_secondary', 'Suggested wording'))
        rows.extend(('guidance_example', '  ' + line.strip())
                    for line in request['example'].splitlines())
        known = request.get('recognized', ())
        if known:
            rows.append(('guidance_context', 'Kept from your description: ' + '; '.join(known)))
    return '\n'.join(text for _, text in rows), [tag for tag, _ in rows]
