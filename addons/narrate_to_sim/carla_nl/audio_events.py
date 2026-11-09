"""Execute decoded SCANeR WAV cues through the common cue lifecycle."""
import math
from pathlib import Path
import subprocess
import sys
import wave


def file_cues(payload):
    cues = []
    for item in payload.get('audio_cues', []):
        name = item['file_name']
        path = Path(payload.get('audio_library_root') or '.') / name
        try:
            with wave.open(str(path), 'rb') as stream:
                duration = stream.getnframes() / float(stream.getframerate())
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError('Audio file {} cannot be played. Choose an existing PCM WAV file '
                             'and check audio_library_root.'.format(path)) from exc
        start = float(item['time_s'])
        if not math.isfinite(start) or start < 0 or duration <= 0:
            raise ValueError('Audio {} needs a nonnegative start and a nonempty WAV file.'.format(name))
        if start + duration > float(payload['total_time_s']) + 1e-6:
            error = ValueError('Audio {} ends at {:g}s. Extend the scenario duration or start it earlier.'.format(
                name, start + duration))
            error.duration_requirements = [('audio ' + name, start + duration)]
            raise error
        cues.append(dict(kind='audio_file', time_s=start, duration_s=duration,
                         text=name, file_path=str(path.resolve()), target_role=''))
    return cues


def start_audio(cue):
    if cue['kind'] != 'audio_file':
        from .standalone_cues import start_cue_audio
        return start_cue_audio(cue)
    code = 'import sys,winsound; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_NODEFAULT)'
    return subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', code, cue['file_path']],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
