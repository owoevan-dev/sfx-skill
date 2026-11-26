"""The doctor command and the lines it prints."""
import platform
from pathlib import Path
import numpy

from .. import mpbridge, samples, surge
from .common import _ffmpeg


def _library_line(named=None):
    folder = Path(named) if named else samples.default_library()
    if not (folder / samples.INDEX).is_file():
        return f'sample library: none yet at {folder} (needed only for recorded layers; sfx.py samples tells how to fill it)'
    try:
        index = samples.offered(samples.load_index(folder))
    except samples.SampleError as error:
        return f'sample library: {error}'
    packs, files = len(index['packs']), len(index['files'])
    clean = sum(1 for f in index['files'] if not f.get('screen'))
    return f"sample library: {folder}: {packs} pack{'s' if packs != 1 else ''}, {files} recordings, {clean} pass every screen"


def _ffmpeg_line(ff, missing):
    """What doctor says of this FFmpeg. It is optional, so one that lacks an encoder costs that format and fails nothing."""
    if not missing:
        return f'FFmpeg: OK ({ff})'
    lost = [name for encoder, name in (('libvorbis', 'Ogg'), ('libmp3lame', 'MP3'), ('flac', 'FLAC')) if encoder in missing]
    costs = f", so no {' or '.join(lost)} exports" if lost else ''
    hint = " (on macOS, Homebrew's ffmpeg-full has libvorbis)" if 'libvorbis' in missing else ''
    return f"FFmpeg: {ff}, lacks {', '.join(missing)}{costs}; the rest works{hint}"


def cmd_doctor(args):
    ok = True                           # this script stops before here without Python 3.10 and NumPy
    print(f'Python {platform.python_version()}: OK')
    print(f'NumPy {numpy.__version__}: OK')
    print('synthesis: OK')

    try:
        skill = mpbridge.find_music_skill(args.music_skill or None)
    except mpbridge.MusicSkillError as error:
        print(f'music-production: {error}')
        return 1
    print(f'music-production: {skill}' if skill else
          'music-production: not found (needed only for instrument and Surge layers; one installed as a plugin sits in a folder of its '
          'own: name it with --music-skill DIR)')
    try:
        config = mpbridge.load_config(args.config) if args.config else None
    except mpbridge.MusicSkillError as error:
        print(f'configuration: {error}')
        return 1
    print(f'Surge XT: {surge.status(config, skill)}')

    print(_library_line(args.library))
    found, ff = _ffmpeg(args, 'FFmpeg')
    if not found:
        return 1
    if ff is None:
        print('FFmpeg: not found (needed only for FLAC, Ogg and MP3 exports and recordings that are not WAV; pass '
              '--ffmpeg PATH or put ffmpeg on the PATH)')
    else:
        print(_ffmpeg_line(ff, mpbridge.missing_features(mpbridge.ffmpeg_features(ff, env=mpbridge.child_env(args.tmp)))))
    if args.config:
        if skill:
            proc = mpbridge.run_studio(skill, ['doctor', '--config', args.config], env=mpbridge.child_env(args.tmp))
            if proc.returncode == 0:
                print('music-production doctor: OK')
            else:
                print('music-production doctor: FAILED')
                print((proc.stdout + proc.stderr).strip()[-600:])
                ok = False
    return 0 if ok else 1
