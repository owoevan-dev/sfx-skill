"""A render folder as the commands that read it take it. Its records may have come from someone else, so the names in them
are checked before any is used as a path: a sound is a plain id and a master a file in master/, and a master must still be
at the rate a render writes."""
import json
import os
import re
import stat
from pathlib import Path

from . import RATE, wav

SOUND_RE = re.compile(r'[a-z][a-z0-9_]{0,31}')
WRITTEN = ('resolved.json', 'check.json', 'audition.html', 'files.json', 'report.json', 'master', 'spectrograms')
MASTER_RE = re.compile(r'master/[a-z][a-z0-9_]{0,31}_[0-9]{2}\.wav')
NAME_SURROGATE = 0x20000000        # Windows: the bit of a reparse tag saying that it stands for another path


class RecordError(ValueError):
    """A render folder's record cannot be used as it stands."""


def is_link(path):
    """True for a symbolic link and, on Windows, a reparse point that stands for another path: a directory junction is
    not `is_symlink`. A file the system keeps in another form (a cloud placeholder, a compressed file) has a reparse point
    too, and is an ordinary file to read."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_reparse_tag', 0) & NAME_SURROGATE)


def load(folder):
    """resolved.json of a render folder, after checking that what a render writes there is not a link and that every sound
    id and master path in it is one a render writes."""
    for name in WRITTEN:
        if is_link(Path(folder) / name):
            raise RecordError(f'{Path(folder) / name} is a link: a render holds its own files, so it is not read or written')
    data = json.loads((Path(folder) / 'resolved.json').read_text(encoding='utf-8'))
    for sound in data.get('sounds', []):
        if not SOUND_RE.fullmatch(str(sound.get('id'))):
            raise RecordError(f"{folder}: {sound.get('id')!r} is not a sound id this skill writes; the record was changed")
        for v in sound.get('variants', []):
            if not MASTER_RE.fullmatch(str(v.get('file'))):
                raise RecordError(f"{folder}: {v.get('file')!r} is not a master file of this render; the record was changed")
    return data


def master(folder, rel):
    """A master file's samples, refused when it is no longer at the rate a render writes: played as if it were, an edited
    file would change in pitch and length."""
    if is_link(Path(folder) / rel):
        raise RecordError(f'{rel} is a link: a render holds its own files, so it is not read')
    data, info = wav.read(Path(folder) / rel)
    if info['rate'] != RATE:
        raise RecordError(f"{rel} is at {info['rate']} Hz, not the {RATE} Hz of a render: it was changed after rendering. "
                          'Render again, or resample it')
    return data
