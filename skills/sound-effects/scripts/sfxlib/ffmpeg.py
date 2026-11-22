"""Finding FFmpeg, which this skill needs only to encode FLAC, Ogg Vorbis and MP3 exports and to decode recordings that
are not WAV. Synthesis, WAV masters, measurement, FSD50K's recordings (WAV) and the audition page need nothing.

The order, first found wins: a path the agent passes (--ffmpeg), the SFX_SKILL_FFMPEG environment variable, the FFmpeg
an installed music-production skill's configuration names (--config), then `ffmpeg` on the PATH (shutil.which)."""
import os
import shutil
from pathlib import Path

ENV = 'SFX_SKILL_FFMPEG'


class FFmpegError(Exception):
    """A named FFmpeg is missing."""


def find(explicit=None, config=None):
    """The FFmpeg to use (a Path), or None when there is none. `config` is music-production's configuration (a dict).
    A path passed or set in the environment that does not exist is an error, not a fall-through."""
    for given, where in ((explicit, '--ffmpeg'), (os.environ.get(ENV), ENV)):
        if given:
            p = Path(given)
            if not p.is_file():
                raise FFmpegError(f'{where} names {p}, which is not a file')
            return p.resolve()                # a bare relative name would be looked up on the PATH
    if config:
        from . import mpbridge
        try:
            return Path(mpbridge.ffmpeg_path(config))
        except mpbridge.MusicSkillError:
            pass
    found = shutil.which('ffmpeg')
    return Path(found) if found else None
