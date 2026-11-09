"""Source-bound edits from structured diagnostics back to the user's draft."""
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re

from .prompt_language import normalize_prompt_characters, normalize_vehicle_aliases, normalize_vehicle_language

SENTENCE_END = re.compile(r'[.!?](?=\s|$)|[。！？;\r\n]+')
SOFT_BOUNDARY = re.compile(r'[,，]|\s+(?:and\s+then|and|then|while)\s+', re.I)


@dataclass(frozen=True)
class PromptRepair:
    prompt: str
    role: str
    example: str
    start: int = -1
    end: int = -1
    replacement: str = ''
    reason: str = ''
    source_clause: str = ''
    request_id: int = 0
    unrecognized: str = ''
    needs_refresh: bool = False
    recovery: bool = False

    @property
    def available(self):
        return self.start >= 0

    @property
    def button_label(self):
        insertion = self.available and self.start == self.end
        if self.role == 'scenario duration':
            return ('Add duration' if insertion else 'Replace duration') + '  ↙'
        return ('Add definition' if insertion else 'Replace description') + '  ↙'

    def apply(self, current):
        if current != self.prompt:
            raise ValueError('Prompt changed; click Analyze again. / 左側內容已修改，請重新按 Analyze，再套用新提示。')
        if not self.available:
            raise ValueError(self.reason)
        return current[:self.start] + self.replacement + current[self.end:]


class PromptRepairSession:
    """Track independent repairs, refreshing diagnostics when source text changes."""

    def __init__(self, prompt, repairs):
        self.prompt = prompt
        self.pending = {item: item for item in repairs}
        self.editable = []
        self.applied = set()
        self.checkpoints = {}
        self._checkpoint()

    def _checkpoint(self):
        self.checkpoints[self.prompt] = (dict(self.pending), list(self.editable), set(self.applied))

    def restore(self, current):
        """Recognize exact Undo/Redo checkpoints without trusting arbitrary edits."""
        if current == self.prompt or current not in self.checkpoints:
            return False
        pending, editable, applied = self.checkpoints[current]
        self.prompt = current
        self.pending, self.editable, self.applied = dict(pending), list(editable), set(applied)
        return True

    def _shift(self, start, end, length):
        delta = length - (end - start)
        for key, item in list(self.pending.items()):
            if not item.available:
                continue
            if item.start >= end:
                self.pending[key] = replace(item, start=item.start + delta, end=item.end + delta)
            elif item.end > start:
                # Re-analyze overlapping alternatives before another replacement.
                self.pending[key] = replace(item, start=-1, end=-1,
                    reason='Another template changed this description. Its suggestion will refresh automatically.')
        spans = []
        for left, right in self.editable:
            if left <= start and end <= right:
                spans.append((left, right + delta))
            elif left >= end:
                spans.append((left + delta, right + delta))
            elif right <= start:
                spans.append((left, right))
        self.editable = spans

    def _rebase(self, current):
        edits = [op for op in SequenceMatcher(None, self.prompt, current, autojunk=False).get_opcodes()
                 if op[0] != 'equal']
        if any(not any(left <= start and end <= right for left, right in self.editable)
               for _, start, end, _, _ in edits):
            raise ValueError('Prompt changed outside the inserted templates; click Analyze again. / 原句已修改，請重新 Analyze；已加入的 <...> 可直接填寫。')
        for _, start, end, new_start, new_end in reversed(edits):
            self._shift(start, end, new_end - new_start)
        self.prompt = current
        self._checkpoint()

    def _finish(self, original, item, current):
        result = item.apply(current)
        self.pending.pop(original)
        self.prompt = result
        self._shift(item.start, item.end, len(item.replacement))
        self.editable.append((item.start, item.start + len(item.replacement)))
        self.applied.add(original)
        self._checkpoint()
        return item

    def apply(self, original, current):
        self.restore(current)
        if original not in self.pending:
            raise ValueError('This suggestion is already applied. Use Undo or Analyze again.')
        if not self.pending[original].available:
            raise ValueError(self.pending[original].reason)
        self._rebase(current)
        return self._finish(original, replace(self.pending[original], prompt=current), current)

    def apply_automatic(self, original, current, answers=None):
        """Refresh edited source text and apply without relying on a selection."""
        self.restore(current)
        if original not in self.pending:
            raise ValueError('This suggestion is already applied. Use Undo or Analyze again.')
        if self.pending[original].recovery:
            return self._apply_recovery(original, current, answers)
        refreshed = False
        try:
            self._rebase(current)
        except ValueError:
            self._refresh_sources(current)
            refreshed = True
        item = self.pending[original]
        if (not item.available or item.needs_refresh) and not refreshed:
            self._refresh_sources(current)
            item = self.pending[original]
        if not item.available or item.needs_refresh:
            raise ValueError('This description no longer has the same issue; suggestions have been refreshed.')
        item = self._answer_item(original, item, answers)
        return self._finish(original, replace(item, prompt=current), current)

    def _answer_item(self, original, item, answers):
        if answers is None:
            return item
        from .prompt_answers import fill_answers
        if item.example != original.example:
            raise ValueError('This description changed; review the refreshed questions before answering.')
        return replace(item, replacement=fill_answers(item.replacement, answers))

    def _apply_recovery(self, original, current, answers=None):
        # This path must work even when the semantic reader itself has failed.
        operations = SequenceMatcher(None, self.prompt, current, autojunk=False).get_opcodes()
        for key, item in list(self.pending.items()):
            start, end = _map_source_range(item.start, item.end, operations, len(current))
            unchanged = current[start:end] == self.prompt[item.start:item.end]
            if item.start == item.end and current != self.prompt:
                unchanged = False
            self.pending[key] = replace(item, prompt=current, start=start, end=end,
                                        needs_refresh=item.needs_refresh or not unchanged)
        self.editable = [_map_source_range(a, b, operations, len(current)) for a, b in self.editable]
        self.prompt = current
        self._checkpoint()
        item = self.pending[original]
        if item.needs_refresh:
            raise ValueError('This description changed; refresh its recovery wording.')
        return self._finish(original, self._answer_item(original, item, answers), current)

    def _refresh_sources(self, current):
        # The semantic reader can report unfinished inserted templates together
        # with other issues. Bypass only the editor's placeholder preflight, not
        # the semantic checks, so earlier template fields remain editable.
        from .natural_language_prompt import parse_prose_prompt
        from .standalone_cues import split_cue_lines
        from .takeover_alerts import alert_boundary_mask
        try:
            from .prompt_feedback import prompt_placeholder_fields
            if prompt_placeholder_fields(current):
                source, _ = split_cue_lines(normalize_prompt_characters(current))
                parse_prose_prompt(source)
            else:
                # Includes cue/schema validation and duration repairs which
                # run after the traffic reader. Refresh their source too.
                from .scanner_prompt_adapter import convert_prompt
                convert_prompt(current)
        except ValueError as error:
            fresh = build_prompt_repairs(current, error)
        else:
            fresh = []
        operations = SequenceMatcher(None, self.prompt, current, autojunk=False).get_opcodes()
        boundary_mask = alert_boundary_mask(self.prompt)
        used = set()
        pending = {}
        def identities(item):
            return [index for index, new in enumerate(fresh) if new.available
                    and _source_key(new.source_clause) == _source_key(item.source_clause)
                    and (item.source_clause or item.role == new.role)]
        # Reserve unchanged descriptions before considering changed ranges.
        # A diff can align a deleted clause's common 'At ' with the next clause;
        # overlap alone must never transfer that next clause's suggestion.
        ordered = sorted(self.pending.items(), key=lambda pair: not bool(identities(pair[1])))
        for original, item in ordered:
            if item.available:
                start, end = _map_source_range(item.start, item.end, operations, len(current))
                exact = [index for index in identities(item) if index not in used]
                crossed_boundary = any(tag in ('delete', 'replace') and left < item.end and item.start < right
                                       and any(item.start <= boundary.start() <= item.end
                                               for pattern in (SENTENCE_END, SOFT_BOUNDARY)
                                               for boundary in pattern.finditer(boundary_mask, left, right))
                                       for tag, left, right, _, _ in operations)
                candidates = [(index, new) for index, new in enumerate(fresh) if index not in used and new.available
                              and (index in exact if exact else not crossed_boundary and not item.needs_refresh and (
                                  (item.start == item.end and new.start == new.end and item.role == new.role)
                                  or (start < new.end and new.start < end)))]
                if candidates:
                    index, new = min(candidates, key=lambda pair:
                                     (abs(pair[1].start - start), abs(pair[1].end - end)))
                    used.add(index)
                    pending[original] = replace(new, request_id=item.request_id)
                else:
                    pending[original] = replace(item, prompt=current, start=start, end=end, needs_refresh=True)
            else:
                # Unlocated old diagnostics can be upgraded only by source
                # identity, never by picking an arbitrary actor or sentence.
                candidates = [(index, new) for index, new in enumerate(fresh) if index not in used
                              and new.available and _source_key(new.source_clause) == _source_key(item.source_clause)]
                if candidates:
                    index, new = candidates[0]
                    used.add(index)
                    pending[original] = replace(new, request_id=item.request_id)
                else:
                    pending[original] = replace(item, prompt=current, needs_refresh=True)
        self.editable = [_map_source_range(start, end, operations, len(current)) for start, end in self.editable]
        self.pending = pending
        self.prompt = current
        self._checkpoint()

def _clauses(text):
    # Use the same lexical and shared-value rules that produced the diagnostic.
    from .natural_language_prompt import _resolve_shared_values
    sentences = [s.strip() for s in SENTENCE_END.split(normalize_vehicle_language(text)) if s.strip()]
    return tuple(re.sub(r'\s+', ' ', s).strip(' ,').casefold()
                 for s in _resolve_shared_values(sentences))


def _map_source_range(start, end, operations, length):
    def boundary(position, opening):
        for tag, left, right, new_left, new_right in operations:
            if tag == 'equal' and left <= position < right:
                return new_left + position - left
            if tag != 'equal' and left <= position <= right:
                if left == right or position == right:
                    return new_right
                return new_left if opening else new_right
        return length
    return boundary(start, True), boundary(end, False)


def _source_ranges(prompt):
    # Quotes and HTML escapes are payload; punctuation inside them is not a
    # clause boundary. Never split decimals, speed units or actor identifiers.
    masked = list(prompt)
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|&(?:#\w+|\w+);', prompt):
        masked[match.start():match.end()] = ' ' * (match.end() - match.start())
    mask = ''.join(masked)
    from .takeover_alerts import alert_boundary_mask
    mask = alert_boundary_mask(mask)
    mask = re.sub(r'<br\s*/?>', lambda m: '\n' * len(m[0]), mask, flags=re.I)
    spans = []
    # A newline can be an instruction boundary or a wrapped phrase. Consider
    # both; exact clause normalization below decides which interpretation fits.
    for boundary in (SENTENCE_END, re.compile(r'[.!?](?=\s|$)|[。！？;]+')):
        first = 0
        for match in boundary.finditer(mask):
            spans.append((first, match.start()))
            first = match.end()
        spans.append((first, len(prompt)))
    ranges = set()
    for left, right in spans:
        starts = {left} | {left + m.start() for m in re.finditer(r'(?<!\w)(?=\w)', mask[left:right])}
        ends = {right} | {left + m.start() for m in SOFT_BOUNDARY.finditer(mask[left:right])}
        for start in starts:
            for end in ends:
                while start < end and prompt[start].isspace():
                    start += 1
                while end > start and prompt[end - 1].isspace():
                    end -= 1
                if start < end:
                    ranges.add((start, end))
    return sorted(ranges, key=lambda pair: (pair[1] - pair[0], pair[0]))


def build_prompt_repairs(prompt, error):
    """Locate editable templates without requiring the source to parse correctly.

    This does not guess by actor ID, regenerate a whole prompt, or change the
    driving grammar. Duplicate descriptions keep their occurrence identity.
    """
    from .natural_language_prompt import VEHICLE_EVENT, _check_event_words
    requests = getattr(error, 'repair_requests', ())
    source_attributed = bool(requests or getattr(error, 'clause', ''))
    if not requests:
        from .prompt_feedback import format_prompt_problem
        requests = []
        format_prompt_problem(prompt, error, repair_requests=requests)
        if not requests:
            return []
    try:
        original = _clauses(prompt)
    except ValueError:
        original = ()
    ranges = _source_ranges(prompt)
    if not source_attributed:
        # A fallback diagnostic may display the whole draft for context. That
        # is not permission to replace multiple instructions when parsing fails.
        ranges = [span for span in ranges if len([part for part in SENTENCE_END.split(
                  prompt[span[0]:span[1]]) if part.strip()]) <= 1]
    local_clauses = {}
    for span in ranges:
        try:
            local_clauses[span] = _clauses(prompt[span[0]:span[1]])
        except ValueError:
            local_clauses[span] = ()
    if getattr(error, 'code', '') == 'ego_conflict' or getattr(error, 'repair_definitions', ()):
        merged = _merge_explicit_definitions(prompt, error, requests[0], ranges, local_clauses, original)
        if merged is not None:
            return [merged]
    result = []
    occurrences = {}
    for request_id, request in enumerate(requests):
        role, example, clause = request['role'], request['example'], request['clause']
        template = '\n'.join(line.strip() for line in example.splitlines())
        notice = request.get('notice', '')
        unrecognized = getattr(error, 'unrecognized', '')
        if re.search(r"\b(?:\w+n't|not|never|cannot|unless|if|when|without|might|may|maybe|possibly|probably|could|would|provided)\b", clause, re.I):
            notice = (notice + '\n' if notice else '') + 'Review the conditional or negative meaning after replacement. Ctrl+Z restores the original.'
        if VEHICLE_EVENT.search(clause):
            try:
                _check_event_words(clause, role)
            except ValueError as exc:
                unrecognized = ' '.join((getattr(exc, 'unrecognized', '') or clause).split())
                notice = (
                    'Review the template before Analyze; it may change the original meaning.\n'
                    f'• Unrecognized text: "{unrecognized}"\n'
                    '• Edit the replacement as needed. Ctrl+Z restores the original.')
        source_key = _source_key(clause)
        occurrence = request.get('source_occurrence', occurrences.get(source_key, 0))
        occurrences[source_key] = occurrence + 1
        fallback = _source_repair(prompt, role, example, clause, ranges, local_clauses,
                                  occurrence, notice, request_id, unrecognized)
        if not clause and role in ('ego', 'scenario duration') and original:
            result.append(PromptRepair(prompt, role, example, 0, 0, template + '\n', request_id=request_id))
            continue
        try:
            target = _clauses(clause)
            inserted = _clauses(template)
        except ValueError:
            result.append(fallback)
            continue
        indices = [i for i, value in enumerate(original) if len(target) == 1 and value == target[0]]
        if len(target) != 1 or not indices:
            result.append(fallback)
            continue
        index = indices[min(occurrence, len(indices) - 1)]
        expected = original[:index] + inserted + original[index + 1:]
        found = None
        # Rank local exact matches (including an omitted subject) first. Every
        # proposed splice still gets the same full-context verification below.
        # Cache local normalization across requests to keep multi-event drafts
        # responsive instead of re-normalizing the full draft for every word.
        without_subject = lambda s: re.sub(r'\b(?:ego|vehicle_[1-6])\s+', '', s, count=1)
        preferred = [span for span in ranges if len(local_clauses[span]) == 1
                     and without_subject(local_clauses[span][0]) == without_subject(target[0])]
        preferred_set = set(preferred)
        candidates = preferred + [span for span in ranges if span not in preferred_set]
        for start, end in candidates:
            # Complete examples have a subject. A compact action without a
            # comma needs a sentence boundary before that new subject.
            prefix = prompt[:start].rstrip()
            separator = '. ' if prefix and prefix[-1] not in '.,;!?。！？，' else ''
            for lead in ('', separator) if separator else ('',):
                # Keep existing punctuation/conjunctions: introducing a full
                # stop before a comma would detach a later omitted subject.
                replacement = lead + (template.rstrip('.') if prompt[end:].strip() else template)
                candidate = prompt[:start] + replacement + prompt[end:]
                try:
                    matches = _clauses(candidate) == expected
                except ValueError:
                    matches = False
                if matches:
                    found = PromptRepair(prompt, role, example, start, end, replacement,
                                         reason=notice, source_clause=prompt[start:end], request_id=request_id,
                                         unrecognized=unrecognized)
                    break
            if found:
                break
        result.append(found or fallback)
    return [_preserve_duration_header(item) for item in result]


def _merge_explicit_definitions(prompt, error, request, ranges, local_clauses, original):
    """One undoable splice, retaining intervening instructions verbatim.

    Only explicitly attributed conflicting definitions can be removed. Check the
    full normalized clause list so omitted event subjects cannot change owners.
    """
    definitions = getattr(error, 'repair_definitions', ()) or getattr(error, 'ego_definitions', ())
    role = request['role']
    occurrences, spans = {}, []
    for clause in definitions:
        key = _source_key(clause)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        item = _source_repair(prompt, role, request['example'], clause, ranges, local_clauses,
                              occurrence, request['notice'], 0, '')
        if not item.available:
            return None
        spans.append((item.start, item.end))
    spans = sorted(set(spans))
    if len(spans) < 2:
        return None
    targets = [part for clause in definitions for part in _clauses(clause)]
    retained = tuple(part for part in original if part not in targets)
    template = request['example'].rstrip('.')
    # Keep the initial definition near the beginning when possible, while
    # respecting actions that inherit ego from the later declaration.
    for keep in (spans[0], spans[-1]):
        start, end = spans[0][0], spans[-1][1]
        edits = []
        for left, right in spans:
            if (left, right) == keep:
                edits.append((left, right, template))
            else:
                separator = re.match(r'(?:[\s.,;!?。！？，]|\b(?:and|then)\b)*', prompt[right:end], re.I)
                edits.append((left, right + separator.end(), ''))
        replacement = prompt[start:end]
        for left, right, text in reversed(edits):
            replacement = replacement[:left-start] + text + replacement[right-start:]
        if keep != spans[-1] and prompt[end:].lstrip().startswith(('.', ';')):
            replacement = replacement.rstrip(' \t\r\n.;')
        candidate = prompt[:start] + replacement + prompt[end:]
        try:
            clauses = list(_clauses(candidate))
            proposed, = _clauses(template)
            if clauses.count(proposed) != 1:
                continue
            clauses.remove(proposed)
            if tuple(clauses) != retained:
                continue
        except ValueError:
            continue
        return PromptRepair(prompt, role, request['example'], start, end, replacement,
                            reason=request['notice'], source_clause=prompt[start:end])
    return None


def _preserve_duration_header(item):
    # Rewriting a DSL header as prose would change reader routing and lose
    # existing scanner defaults. Change only its number in the author's syntax.
    if item.role != 'scenario duration' or not item.available or item.start == item.end:
        return item
    from .prompt_language import SCENARIO_DURATION_HEADER
    from .natural_language_prompt import DURATION
    source = item.prompt[item.start:item.end]
    original = SCENARIO_DURATION_HEADER.fullmatch(source)
    proposed = DURATION.search(item.example)
    if original and proposed:
        number = next(value for value in proposed.groups() if value is not None)
        replacement = source[:original.start(1)] + number + source[original.end(1):]
        return replace(item, replacement=replacement)
    return item


def _source_key(text):
    """A lexical identity still works when full semantic normalization fails."""
    try:
        text = normalize_vehicle_aliases(normalize_prompt_characters(text))
    except ValueError:
        text = normalize_prompt_characters(text)
    return re.sub(r'\s+', ' ', text).strip(' .,!?:;\r\n').casefold()


def _source_repair(prompt, role, example, clause, ranges, local_clauses,
                   occurrence, notice, request_id, unrecognized):
    """Locate the complete diagnostic clause without requiring a valid template.

    Do not infer a target from a vehicle ID or cursor selection. A normalized
    clause or literal source identity must match, including its occurrence.
    """
    key = _source_key(clause)
    try:
        target = _clauses(clause)
    except ValueError:
        target = ()
    masked = re.sub(r'"(?:\\.|[^"\\])*"|&(?:#\w+|\w+);', lambda m: ' ' * len(m[0]), clause)
    from .takeover_alerts import alert_boundary_mask
    masked = alert_boundary_mask(masked)
    single_source = len([part for part in SENTENCE_END.split(masked) if part.strip()]) <= 1
    candidates = [(start, end) for start, end in ranges
                  if ((single_source and key and _source_key(prompt[start:end]) == key)
                  or (len(target) == 1 and local_clauses[start, end] == target))]
    # Several suffix ranges can normalize to the same clause ('the ego' vs
    # 'ego'). Keep its entire source phrase, then distinguish later occurrences.
    spans = []
    for start, end in sorted(candidates, key=lambda pair: (pair[0], -pair[1])):
        if not spans or start >= spans[-1][1]:
            spans.append((start, end))
    item = PromptRepair(prompt, role, example, source_clause=clause, request_id=request_id,
                        unrecognized=unrecognized, reason=notice)
    if not spans:
        return replace(item, reason=(notice + '\n' if notice else '') +
                       'The description changed; its suggestions will refresh automatically.')
    start, end = spans[min(occurrence, len(spans) - 1)]
    template = '\n'.join(line.strip() for line in example.splitlines())
    if prompt[start:end].rstrip().endswith((',', '，')):
        template = template.rstrip('.') + prompt[start:end].rstrip()[-1]
    elif (prompt[end:].lstrip().startswith(('.', ';', '!', '?', ',', '，'))
          or re.match(r'\s*(?:and|then|while)\b', prompt[end:], re.I)):
        template = template.rstrip('.')
    return replace(item, start=start, end=end, replacement=template, source_clause=prompt[start:end])
