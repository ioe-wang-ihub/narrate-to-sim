"""Timed sensory cues, independent of TOR decisions (Python 3.7 compatible)."""
import json
import math
import re
import shlex
import subprocess
import sys
import time


def _cue_error(line, message, field=None, cue=None):
    from .prompt_feedback import PromptInputError
    original = line
    if cue is not None:
        line = format_cue(cue)
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    kind = tokens[1].lower() if len(tokens) > 1 and tokens[1].lower() in ('beep', 'speech', 'circle', 'visual') else '<cue>'
    fields = dict(token.split('=', 1) for token in tokens[2:] if '=' in token)
    time = '<start>s' if field == 'at' else fields.get('at', '<start>s')
    duration = '<duration>s' if field == 'duration' else fields.get('duration', '4s')
    if 'ttc' in fields:
        threshold = '<ttc>s' if field == 'ttc' else fields['ttc']
        obstacle = '<obstacle>' if field == 'obstacle' else fields.get('obstacle', '<obstacle>')
        example = 'cue {} ttc={} obstacle={} duration={}'.format(kind, threshold, obstacle, duration)
    else:
        example = 'cue {} at={} duration={}'.format(kind, time, duration)
    if kind in ('speech', 'visual'):
        example += ' text=' + ('<text>' if field == 'text' else json.dumps(fields.get('text', 'Please take over.'), ensure_ascii=False))
    elif kind == 'circle':
        target_field = '<obstacle>' if fields.get('target', '').lower().startswith('obstacle') else '<vehicle>'
        example += ' target=' + (target_field if field == 'target' else fields.get('target', 'v1'))
    if cue is not None and kind != 'visual' and not re.match(r'\s*cue\b', original, re.I):
        example = re.sub(r'^cue\s+', '', example)
    return PromptInputError(message, clause=original.strip(), reason=message, code='cue', example=example)


def split_cue_lines(text, source_clauses=None):
    from .cue_language import split_standalone_phrases
    from .takeover_alerts import extract_takeover_alerts
    original = text
    sources = []
    text, cues = split_standalone_phrases(text, sources)
    text, takeover_cues = extract_takeover_alerts(text, sources)
    cues.extend(takeover_cues)
    traffic = []
    for line in str(text).splitlines():
        if not re.match(r'^\s*cue\b', line, re.I):
            traffic.append(line)
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            raise _cue_error(line, 'Close the quoted cue text, then specify its start and duration.', 'text') from exc
        if len(tokens) < 3 or tokens[1].lower() not in ('beep', 'speech', 'circle', 'visual'):
            raise _cue_error(line, 'Use cue beep, cue speech, cue visual, or cue circle with at=Ns.')
        kind = tokens[1].lower()
        fields = {}
        allowed = {'at', 'duration', 'ttc', 'obstacle'} | ({'text'} if kind in ('speech', 'visual') else {'target'} if kind == 'circle' else set())
        for token in tokens[2:]:
            key, sep, value = token.partition('=')
            if not sep or key not in allowed or key in fields:
                raise _cue_error(line, 'Invalid or duplicate cue field: ' + token, key if key in fields else None)
            fields[key] = value
        if 'at' in fields and 'ttc' in fields:
            raise _cue_error(line, 'Choose one cue trigger: at=Ns or ttc=Ns. Suggested wording keeps the collision condition.')
        if 'at' not in fields and 'ttc' not in fields:
            raise _cue_error(line, 'Cue needs an explicit at=Ns time.', 'at')
        numbers = {}
        timing = 'ttc' if 'ttc' in fields else 'at'
        for key, value in [(timing, fields[timing]), ('duration', fields.get('duration', '4s'))]:
            try:
                numbers[key] = float(value.rstrip('s'))
            except ValueError as exc:
                raise _cue_error(line, 'Cue {} needs a number in seconds.'.format(key), key) from exc
        cue = dict(kind=kind, time_s=numbers.get('at'), duration_s=numbers['duration'],
                   text=fields.get('text', 'Please take over.' if kind in ('speech', 'visual') else ''),
                   target_role=re.sub(r'^v([1-6])$', r'vehicle_\1', fields.get('target', 'vehicle_1')) if kind == 'circle' else '')
        if timing == 'ttc':
            cue['trigger'] = dict(kind='obstacle_ttc', threshold_s=numbers['ttc'], target_role=fields.get('obstacle', ''))
        elif 'obstacle' in fields:
            raise _cue_error(line, 'An obstacle trigger needs ttc=Ns, not a fixed at=Ns time.')
        cues.append(cue)
        sources.append(line.strip())
    # Extraction passes must not reorder events or detach a validation error
    # from its original clause. Channels belonging to one clause stay together.
    paired = sorted(zip(cues, sources), key=lambda pair: original.find(pair[1]))
    cues = [pair[0] for pair in paired]
    if source_clauses is not None:
        source_clauses.extend(pair[1] for pair in paired)
    return '\n'.join(traffic), cues


def validate_cues(cues, payload, source_clauses=()):
    overruns = []
    for index, cue in enumerate(cues):
        cue.setdefault('trigger', None)
        line = source_clauses[index] if index < len(source_clauses) else format_cue(cue)
        from .cue_language import cue_head
        standalone = bool(cue_head(line))
        def cue_error(message, field):
            error = _cue_error(line, message, field, cue if standalone else None)
            if standalone:
                related = [item for pos, item in enumerate(cues)
                           if pos < len(source_clauses) and source_clauses[pos] == line] or [cue]
                error.example = '\n'.join(_cue_error(line, message,
                    field if item is cue or field in ('at', 'duration', 'ttc', 'obstacle') else None,
                    item).example for item in related)
            return error
        trigger = cue.get('trigger')
        duration = float(cue['duration_s'])
        if trigger:
            from .prompt_feedback import missing_information
            from .takeover_alerts import collision_target_example
            threshold = trigger.get('threshold_s')
            if threshold is None or not math.isfinite(float(threshold)) or float(threshold) <= 0:
                raise cue_error('TTC must be positive and finite, e.g. ttc=7s.', 'ttc')
            enabled = {role: cfg for role, cfg in payload.get('obstacles', {}).items() if cfg.get('enabled')}
            defined_roles = ', '.join(enabled) or 'none'
            kind = trigger.get('target_kind')
            if kind:
                # Barrel/Box are stored as Debris with a specific blueprint.
                enabled = {role: cfg for role, cfg in enabled.items()
                           if cfg.get('kind', '').lower() == kind.lower()
                           or re.search(r'\b' + re.escape(kind) + r'\d*\b',
                                        '{} {}'.format(cfg.get('label', ''), cfg.get('blueprint_id', '')), re.I)}
            role = trigger.get('target_role', '')
            if not role and len(enabled) == 1:
                role = next(iter(enabled))
            if role not in enabled:
                reason = 'Choose which obstacle should trigger this alert. Defined obstacles: {}. Define a missing obstacle before using it.'.format(defined_roles)
                if kind:
                    reason += ' Matching {} objects: {}.'.format(kind.lower(), ', '.join(enabled) or 'none')
                example = (cue_error(reason, 'obstacle').example if standalone else collision_target_example(line))
                raise missing_information([('Takeover alert', [reason], example,
                    ['trigger: TTC <= {:g}s'.format(float(threshold))], line)])
            trigger['target_role'] = role
            trigger.pop('target_kind', None)
            if cue['kind'] == 'circle' and not cue.get('target_role'):
                cue['target_role'] = role
            t = 0.0
        else:
            t = float(cue['time_s'])
        if not math.isfinite(t) or not math.isfinite(duration) or t < 0 or duration <= 0:
            raise cue_error('Cue time must be nonnegative and duration must be positive and finite.',
                             'at' if not math.isfinite(t) or t < 0 else 'duration')
        if not trigger and (t >= payload['total_time_s'] or t + duration > payload['total_time_s']):
            overruns.append(('cue ' + str(index + 1), t + duration))
        if cue['kind'] in ('speech', 'visual') and not cue['text'].strip():
            raise cue_error('Alert message must not be empty.', 'text')
        if cue['kind'] == 'circle':
            role = cue['target_role']
            targets = dict(payload.get('vehicles', {}), **payload.get('obstacles', {}))
            if not targets.get(role, {}).get('enabled'):
                natural_alert = not re.match(r'^cue\b', line, re.I)
                enabled = ', '.join(name for name, config in targets.items()
                                    if config.get('enabled') and not (natural_alert and name == 'ego'))
                reason = 'Circle target {} has no initial description in this scenario. '.format(role)
                reason += ('Choose a defined circle target: {}.'.format(enabled) if enabled else
                           'No target vehicle or obstacle is defined yet. Add its initial description first, then use its number here.')
                if standalone:
                    raise cue_error(reason, 'target')
                if natural_alert:
                    from .prompt_feedback import PromptInputError
                    from .takeover_alerts import target_repair_example
                    example = target_repair_example(line)
                    raise PromptInputError(reason, clause=line, reason=reason, code='takeover_alert', example=example)
                raise _cue_error(line, reason, 'target')
            if not trigger and role in payload.get('obstacles', {}):
                target = targets[role]
                available_start = float(target.get('start_time_s', 0.0))
                available_end = float(target.get('end_time_s', payload['total_time_s']))
                if t < available_start or t + duration > available_end:
                    from .prompt_feedback import missing_information
                    reason = ('Circle target {} is available from {:g}s until {:g}s, but the alert needs it '
                              'from {:g}s until {:g}s. Choose an alert interval wholly inside the target lifetime, '
                              'or revise the obstacle appearance/removal times. Keep both descriptions.').format(
                                  role, available_start, available_end, t, t + duration)
                    # A natural alert can own text, circle and audio channels.
                    # Replace its whole clause with all its decoded channels so
                    # fixing the circle timing cannot discard speech or text.
                    related = [item for pos, item in enumerate(cues)
                               if pos < len(source_clauses) and source_clauses[pos] == line] or [cue]
                    examples = []
                    for item in related:
                        example = re.sub(r'^(cue \w+ at=)\S+', r'\1<start>s', format_cue(item))
                        if duration > available_end - available_start:
                            example = re.sub(r' duration=\S+', ' duration=<duration>s', example, count=1)
                        examples.append(example)
                    raise missing_information([('Target circle', [reason], '\n'.join(examples),
                        ['target: ' + role, 'requested duration: {:g}s'.format(duration)], line)])
    if overruns:
        error = _cue_error(source_clauses[0] if source_clauses else format_cue(cues[0]), 'Cue must fit inside the scenario duration.')
        error.duration_requirements = overruns
        raise error
    return cues


def format_cue(cue):
    trigger = cue.get('trigger')
    if trigger:
        line = 'cue {} ttc={:g}s obstacle={} duration={:g}s'.format(cue['kind'], trigger['threshold_s'], trigger['target_role'], cue['duration_s'])
    else:
        line = 'cue {} at={:g}s duration={:g}s'.format(cue['kind'], cue['time_s'], cue['duration_s'])
    if cue['kind'] in ('speech', 'visual'):
        line += ' text=' + json.dumps(cue['text'], ensure_ascii=False)
    elif cue['kind'] == 'circle':
        line += ' target=' + cue['target_role'].replace('vehicle_', 'v')
    return line


def describe_cue(cue):
    names = {'visual': 'Visual takeover text', 'circle': 'Target circle',
             'speech': 'Auditory Speech', 'beep': 'Auditory Beep'}
    detail = cue.get('text') or cue.get('target_role') or ''
    trigger = cue.get('trigger')
    timing = ('when live TTC to {} reaches {:g}s'.format(trigger['target_role'], trigger['threshold_s'])
              if trigger else 'at {:g}s'.format(cue['time_s']))
    return '{} {} for {:g}s{}'.format(names[cue['kind']], timing, cue['duration_s'],
                                           ': ' + detail if detail else '')


def start_cue_audio(cue):
    if cue['kind'] == 'beep':
        code = ('import time,winsound\n'
                'end=time.monotonic()+float(__import__("sys").argv[1])\n'
                'while time.monotonic()<end:\n'
                ' winsound.Beep(1200,150)\n'
                ' time.sleep(.15)\n')
        # These helpers use only the standard library. Bypass the Windows
        # venv redirector so terminate() owns the actual playback process.
        command = [getattr(sys, '_base_executable', sys.executable), '-c', code, str(cue['duration_s'])]
    else:
        command = ['powershell.exe', '-NoProfile', '-Command',
                   '[Console]::InputEncoding = [System.Text.Encoding]::UTF8; '
                   'Add-Type -AssemblyName System.Speech; '
                   '$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                   '$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()']
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if cue['kind'] == 'speech':
        proc.stdin.write(cue['text'])
    proc.stdin.close()
    return proc


def start_cue_visual(cue):
    import os
    from pathlib import Path
    command = [getattr(sys, '_base_executable', sys.executable), str(Path(__file__).with_name('cue_visual.py')), str(os.getpid())]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    proc.stdin.write(cue['text'])
    proc.stdin.close()
    return proc


class CuePlayer:
    def __init__(self, cues, start_audio=start_cue_audio, emit=lambda *args, **kwargs: None, start_visual=start_cue_visual):
        self.cues = list(cues)
        self.start_audio, self.emit = start_audio, emit
        self.start_visual = start_visual
        self.started, self.ended, self.processes = set(), set(), {}
        self.onsets = {}
        self.wall_onsets = {}
        self.triggered, self.unavailable = {}, set()

    def _stop_audio(self, index):
        proc = self.processes.pop(index, None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()

    def _fail(self, index, t, reason):
        cue = self.cues[index]
        message = 'Cue {} ({}) {}. Check this cue and its playback helper, then replay the scenario.'.format(
            index + 1, cue['kind'], reason)
        self.emit('SCENARIO_CUE_FAILED', cue=index + 1, kind=cue['kind'], scenario_t=t, reason=reason)
        raise RuntimeError(message)

    def _check_process(self, index, t):
        proc = self.processes.get(index)
        if proc is not None:
            code = proc.poll()
            if code is not None and code != 0:
                self._fail(index, t, 'playback helper exited with code {}'.format(code))

    def _end(self, index, t):
        self._stop_audio(index)
        self.ended.add(index)
        self.emit('SCENARIO_CUE_END', cue=index + 1, kind=self.cues[index]['kind'], scenario_t=t)

    def update(self, t, trigger_metrics=None, available_circle_targets=None, time_tolerance_s=0.0):
        circles = []
        for index, cue in enumerate(self.cues):
            if index in self.ended:
                continue
            self._check_process(index, t)
            trigger = cue.get('trigger')
            if trigger and index not in self.triggered:
                metric = (trigger_metrics or {}).get(trigger['target_role'])
                if metric is None or not math.isfinite(metric) or not 0 <= metric <= trigger['threshold_s']:
                    continue
                self.triggered[index] = metric
            if not trigger and t < cue['time_s']:
                continue
            if index in self.started and t + max(0.0, time_tolerance_s) >= self.onsets[index] + cue['duration_s']:
                if cue['kind'] == 'audio_file' and self.processes[index].poll() is None:
                    if time.monotonic() > self.wall_onsets[index] + cue['duration_s'] + 2.0:
                        self._fail(index, t, 'audio file playback did not finish')
                    continue  # Include helper startup time; never truncate a WAV.
                self._end(index, t)
                continue
            if (cue['kind'] == 'circle' and available_circle_targets is not None
                    and cue['target_role'] not in available_circle_targets):
                if index in self.started:
                    self._fail(index, t, 'target {} became unavailable before the requested duration ended'.format(cue['target_role']))
                if index not in self.unavailable:
                    self.unavailable.add(index)
                    self.emit('SCENARIO_CUE_UNAVAILABLE', cue=index + 1, kind=cue['kind'],
                              scenario_t=t, target_role=cue['target_role'])
                continue
            if index not in self.started:
                try:
                    if cue['kind'] == 'visual':
                        self.processes[index] = self.start_visual(cue)
                    elif cue['kind'] != 'circle':
                        self.processes[index] = self.start_audio(cue)
                except (OSError, subprocess.SubprocessError) as exc:
                    self._fail(index, t, 'could not start playback: {}'.format(exc))
                self._check_process(index, t)
                # A delayed frame must still present the complete requested
                # duration, even if it skipped the entire nominal interval.
                self.onsets[index] = t
                self.wall_onsets[index] = time.monotonic()
                self.started.add(index)
                self.emit('SCENARIO_CUE_START', cue=index + 1, kind=cue['kind'], scenario_t=t,
                          duration_s=cue['duration_s'], text=cue.get('text', ''), target_role=cue.get('target_role', ''),
                          trigger=trigger or {}, observed_ttc_s=self.triggered.get(index),
                          scheduled_t=cue.get('time_s'))
            if cue['kind'] == 'circle':
                circles.append(cue)
        return circles

    def finish(self, t, interrupted=False, time_tolerance_s=0.0):
        """Audit completion before runner cleanup; unmet TTC is not a fired cue."""
        for index, cue in enumerate(self.cues):
            if index in self.ended:
                continue
            if interrupted:
                self.emit('SCENARIO_CUE_CANCELLED', cue=index + 1, kind=cue['kind'], scenario_t=t)
                continue
            self._check_process(index, t)
            if cue.get('trigger') and index not in self.triggered:
                self.emit('SCENARIO_CUE_NOT_TRIGGERED', cue=index + 1, kind=cue['kind'],
                          scenario_t=t, trigger=cue['trigger'])
                continue
            if index not in self.started:
                self._fail(index, t, 'did not start before the scenario ended; target {} may be unavailable'.format(
                    cue.get('target_role') or '(none)'))
            if t + max(0.0, time_tolerance_s) < self.onsets[index] + cue['duration_s']:
                self._fail(index, t, 'scenario ended before the requested duration completed')
            if cue['kind'] == 'audio_file' and self.processes[index].poll() is None:
                try:
                    remaining = max(0.0, self.wall_onsets[index] + cue['duration_s'] + 2.0 - time.monotonic())
                    self.processes[index].wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    self._fail(index, t, 'audio file playback did not finish')
                self._check_process(index, t)
            self._end(index, t)

    def close(self):
        for index in list(self.processes):
            self._stop_audio(index)
