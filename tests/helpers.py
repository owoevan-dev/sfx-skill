"""Shared test helpers: repository paths, the local settings file and scratch folders that are never deleted.

Tests write only into work/tests/<run>/..., a new folder per test run; nothing here removes files (the project's
rule: files are deleted only through a clean command, so scratch output is left for the user to clear)."""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'skills' / 'sound-effects' / 'scripts'
EXAMPLES = REPO / 'skills' / 'sound-effects' / 'examples'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO / 'tools') not in sys.path:
    sys.path.insert(0, str(REPO / 'tools'))

_RUN = time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}'
QUICK = os.environ.get('SFX_QUICK') == '1'                # check_all.py --quick: no FFmpeg, no Godot
# the tools a run must have (a comma-separated list, as CI sets it): one that is missing fails test_ci_tools, so that
# the tests needing it cannot pass by being skipped
REQUIRED = {name for name in os.environ.get('SFX_TEST_REQUIRE', '').split(',') if name}


def local():
    """tools/local.json (not committed): music_skill, music_config, tmp, work. Empty dict when missing."""
    path = REPO / 'tools' / 'local.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def ffmpeg_or_none():
    """The FFmpeg the tests use, found as the skill finds one (the environment, the music-production configuration of
    tools/local.json, the PATH), or None."""
    if QUICK:
        return None
    from sfxlib import ffmpeg, mpbridge
    config = None
    path = local().get('music_config')
    if path and Path(path).is_file():
        try:
            config = mpbridge.load_config(path)
        except mpbridge.MusicSkillError:
            config = None
    try:
        return ffmpeg.find(None, config)
    except ffmpeg.FFmpegError:
        return None


def godot_or_none():
    """A Godot 4 console build: the one SFX_TEST_GODOT names, or one in work/godot/bin; None without."""
    if QUICK:
        return None
    named = os.environ.get('SFX_TEST_GODOT')
    if named:
        return Path(named) if Path(named).is_file() else None
    found = sorted((REPO / 'work' / 'godot' / 'bin').glob('Godot_v4*_console.exe'))
    return found[-1] if found else None


def scratch(name):
    """A new folder for one test under work/tests/<run>/<name> (made, never cleared)."""
    base = Path(local().get('work') or REPO / 'work') / 'tests' / _RUN / name
    base.mkdir(parents=True, exist_ok=False)
    return base


def child_env():
    """Environment for child processes: TMP and TEMP from local.json, UTF-8 output, no bytecode files."""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    tmp = local().get('tmp')
    if tmp:
        env['TMP'] = env['TEMP'] = tmp
    return env
