"""The link to an installed music-production skill: finding it, reading its configuration, running its command line.

Only music-production's configuration file and its `studio.py` command line are used, never its modules (see the
repository's MUSIC_PRODUCTION_CONTRACT.md). Everything here works without music-production: synthesis, measurement
and the audition page need only NumPy; FFmpeg from its configuration is needed for exports and cross-checks."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_ENCODERS = ('pcm_s16le', 'pcm_s24le', 'pcm_f32le', 'libvorbis', 'libmp3lame', 'flac')
REQUIRED_DECODERS = ('vorbis', 'mp3', 'flac')
REQUIRED_FILTERS = ('ebur128', 'aresample')


class MusicSkillError(Exception):
    """music-production or its configuration is missing or not as this skill expects."""


def _is_skill(folder):
    return folder is not None and (Path(folder) / 'scripts' / 'studio.py').is_file()


def find_music_skill(explicit=None, here=None, home=None):
    """The music-production folder: `explicit` (an error if it is not one), else a music-production folder beside this
    skill in the same skills folder, else ~/.claude/skills/music-production; None when none is found."""
    if explicit:
        folder = Path(explicit)
        if not _is_skill(folder):
            raise MusicSkillError(f'{folder} is not a music-production folder (no scripts/studio.py in it)')
        return folder
    here = Path(here) if here else Path(__file__).resolve()
    home = Path(home) if home else Path.home()
    # here = .../skills/sound-effects/scripts/sfxlib/mpbridge.py, so parents[3] is the skills folder
    candidates = []
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / 'music-production')
    candidates.append(home / '.claude' / 'skills' / 'music-production')
    for folder in candidates:
        if _is_skill(folder):
            return folder
    return None


def load_config(path):
    """music-production's configuration (the JSON its bootstrap.py writes) as a dict."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise MusicSkillError(f'configuration not found: {path}') from None
    except (OSError, ValueError) as error:
        raise MusicSkillError(f'configuration {path} could not be read: {error}') from None


def ffmpeg_path(config):
    """The FFmpeg program the configuration names (field ffmpeg.path), checked to exist."""
    value = (config.get('ffmpeg') or {}).get('path') if isinstance(config, dict) else None
    if not value:
        raise MusicSkillError('the configuration has no ffmpeg.path')
    path = Path(value)
    if not path.is_file():
        raise MusicSkillError(f'ffmpeg.path names a file that is not there: {path}')
    return path


def child_env(tmp=None):
    """Environment for child processes: TMP and TEMP set where given, UTF-8 output, no bytecode files."""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    if tmp:
        env['TMP'] = env['TEMP'] = str(tmp)
    return env


def run_studio(skill, args, env=None, timeout=600):
    """Run music-production's studio.py with `args`; returns the completed process (never raises on its exit code)."""
    command = [sys.executable, str(Path(skill) / 'scripts' / 'studio.py'), *[str(a) for a in args]]
    return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          env=env or child_env(), timeout=timeout)


_FLAGS = re.compile(r'^[A-Z.]{2,6}$')


def parse_listing(text):
    """Names in the output of `ffmpeg -encoders` or `ffmpeg -filters`: lines of a flag column (letters and dots, such as
    "A....D" or "T.C") followed by the name; the legend lines ("V..... = Video") are left out."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and _FLAGS.match(parts[0]) and parts[1] != '=':
            names.add(parts[1])
    return names


def ffmpeg_features(ffmpeg, env=None):
    """{'encoders', 'decoders', 'filters'}: the sets of names this FFmpeg lists."""
    def listing(flag):
        proc = subprocess.run([str(ffmpeg), '-hide_banner', flag], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=env or child_env(), timeout=60)
        return parse_listing(proc.stdout)
    return {'encoders': listing('-encoders'), 'decoders': listing('-decoders'), 'filters': listing('-filters')}


def missing_features(features):
    """The required encoders, decoders and filters this FFmpeg lacks (sorted)."""
    return (sorted(set(REQUIRED_ENCODERS) - features['encoders']) + sorted(set(REQUIRED_DECODERS) -
            features['decoders']) + sorted(set(REQUIRED_FILTERS) - features['filters']))
