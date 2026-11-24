"""Surge XT layers: a performance played once on a Surge patch, kept beside the project as a take, and replayed.

A layer with source {"type": "surge", "patch": "factory/fx/fireworks", "notes": [[0, 72, 1.2, 100]]} is played by the Surge XT
that music-production's setup installs. Surge's oscillators, LFOs and noise run freely, so two renders of one note differ: the
rendering is a take, not a function of its inputs. So a take is recorded once, only when asked (`sfx.py render --record-takes`),
into the folder surge_takes beside the project file, and every later render plays that file. With its takes, a project renders
the same without Surge.

A take's key is the hash of what shapes the performance (the patch, the notes, the controls, and which performance it is) and of
nothing that happens to it afterwards, so a change of pitch, gain, filter or placement records nothing. Its audio is stored under
its own hash, and a receipt named by the key is the one step that commits it: a render stopped at any point leaves at most an
unused file. An operating-system lock keeps two renders from recording one key twice.

Surge XT and pedalboard are GPL-3.0. Nothing here loads them: music-production's scripts/surge_host.py, a separate program, is
given a job file and writes a WAV (the repository's MUSIC_PRODUCTION_CONTRACT.md). No patch and no part of Surge ships with this
skill.

at_tempo and board_intact are copied from music-production 0.9.2, scripts/musiclib/surge.py:

    MIT License, Copyright (c) 2026 music-skill contributors; the full notice is in
    LICENSES/music-production-MIT.txt beside this skill."""
import base64
import csv
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import mpbridge, wav
from .record import is_link
from .samples import _forms, _words, locked

RATE = 48000
PEAK = 0.5                     # a take is kept at this peak, as other recorded layers come before the layer's gain
FOLDER = 'surge_takes'         # beside the project file
WORK = '.work'                 # inside it: the job, the patch and the take of the recording under way, reused each time
SCHEMA = 'sfx-surge-take/1'
LEAD_S = 0.1                   # the host plays this long before the first event and trims it, as music-production's render does
CONTROL_STEP_S = 0.005         # a bend or the mod wheel moves in steps at most this far apart
DEFAULT_BPM = 120.0
DEFAULT_TAIL_S = 2.0
DEFAULT_BEND_RANGE = 2
HOST_TIMEOUT_S = 300
SILENT = 1e-6
WAV_NAME = re.compile(r'^[0-9a-f]{64}\.wav$')
UNPLAYABLE = {'MPE': 'it is an MPE patch, made for a controller that sends each note its own expression',
              'silent on a plain note': 'it is silent on a plain note'}


class SurgeError(ValueError):
    """A Surge layer cannot be played: no take and no word to record one, a take that changed, or Surge itself."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _n(value):
    """A time or level as one canonical number: 1 and 1.0 are the same, and rounding noise of a microsecond is not a change."""
    v = round(float(value), 6)
    return int(v) if v == int(v) else v


def performance(source):
    """What shapes a take, in one canonical form: the patch, the notes, the controls and which performance this is. `slot` is the
    variant's own (sound/layer/variant) or a named take."""
    bend = [[_n(t), _n(s)] for t, s in source.get('bend', [])]
    return {'format': 1, 'patch': source['patch'],
            'notes': [[_n(t), int(pitch), _n(length), int(velocity)] for t, pitch, length, velocity in source['notes']],
            'macros': {str(k): _n(source['macros'][k]) for k in sorted(source.get('macros', {}), key=str)},
            'bend': bend, 'bend_range': int(source.get('bend_range', DEFAULT_BEND_RANGE)) if bend else DEFAULT_BEND_RANGE,
            'wheel': [[_n(t), _n(v)] for t, v in source.get('wheel', [])],
            'bpm': _n(source.get('bpm', DEFAULT_BPM)), 'tail': _n(source.get('tail', DEFAULT_TAIL_S)),
            'slot': source.get('slot') or f"take:{int(source.get('take', 1))}"}


def key_of(perf):
    return _sha(json.dumps(perf, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def _note_end(perf):
    return max(t + length for t, _, length, _ in perf['notes'])


def _curve(points, end):
    """(seconds, value) along a curve, at most CONTROL_STEP_S apart, from 0 to its last point or `end`. Before its first point it
    holds that point's value."""
    times, values = [p[0] for p in points], [p[1] for p in points]
    last = min(times[-1], end)
    count = int(math.ceil(last / CONTROL_STEP_S - 1e-9))
    at = np.linspace(0.0, last, count + 1) if count > 0 else np.zeros(1)
    return zip(at.tolist(), np.interp(at, times, values).tolist())


def midi_events(perf, transpose):
    """The performance as the host takes it: [hex MIDI, seconds], LEAD_S late. Each key is played at its written pitch plus the
    patch's `transpose`, kept within MIDI's range; a key struck again ends its earlier note; at one time controls come first, then
    note-offs, then note-ons."""
    rows = []
    keys = {}
    for t, pitch, length, velocity in sorted(perf['notes']):
        keys.setdefault(max(0, min(127, pitch + transpose)), []).append([t, t + length, velocity])
    for key, notes in keys.items():
        for note, following in zip(notes, notes[1:]):
            note[1] = min(note[1], following[0])
        for start, stop, velocity in notes:
            if stop > start:
                rows.append((start, 2, bytes([0x90, key, velocity])))
                rows.append((stop, 1, bytes([0x80, key, 64])))
    end = _note_end(perf) + perf['tail']
    if perf['bend']:
        last = None
        for t, semitones in _curve(perf['bend'], end):
            value = max(0, min(16383, int(round(8192 + 8192 * semitones / perf['bend_range']))))
            if value != last:
                rows.append((t, 0, bytes([0xE0, value & 0x7F, value >> 7])))
                last = value
    if perf['wheel']:
        last = None
        for t, level in _curve(perf['wheel'], end):
            value = max(0, min(127, int(round(level * 127))))
            if value != last:
                rows.append((t, 0, bytes([0xB0, 1, value])))
                last = value
    rows.sort(key=lambda row: (row[0], row[1]))
    return [[data.hex(), round(t + LEAD_S, 6)] for t, _, data in rows]


SYNCED_DELAY = re.compile(r'fx(\d+)_p[01]$')  # a Delay effect's (type 1) left and right times
SYNCED_RATE = re.compile(r'[ab]_lfo\d+_rate$')


def at_tempo(fxp, bpm):
    """The .fxp `fxp` with its tempo-synced Delay times and LFO rates turned into free-running values for `bpm`, and
    the names of the parameters changed."""
    size = struct.unpack('>I', fxp[56:60])[0]
    chunk = fxp[60:60 + size]
    if fxp[:4] != b'CcnK' or chunk[:4] != b'sub3':
        raise ValueError('not a Surge patch')
    xml_size = struct.unpack('<I', chunk[4:8])[0]
    xml = chunk[32:32 + xml_size].decode('latin-1')
    fx_types = {m.group(1): float(m.group(2)) for m in re.finditer(r'<fx(\d+)_type [^>]*?value="([-+\d.eE]+)"', xml)}
    changed = []

    def free(m):
        name, attrs = m.group(1), m.group(2)
        if 'temposync="1"' not in attrs:
            return m.group(0)
        delay = SYNCED_DELAY.match(name)
        if delay and fx_types.get(delay.group(1)) == 1:
            shift = math.log2(120 / bpm)
        elif SYNCED_RATE.match(name):
            shift = math.log2(bpm / 120)
        else:
            return m.group(0)
        value = float(re.search(r'value="([-+\d.eE]+)"', attrs).group(1))
        attrs = re.sub(r'value="[-+\d.eE]+"', f'value="{value + shift:.6f}"', attrs, count=1).replace('temposync="1"', 'temposync="0"')
        changed.append(name)
        return f'<{name} {attrs}/>'

    new = re.sub(r'<(\w+) ([^>]*?)/>', free, xml).encode('latin-1')
    chunk = chunk[:4] + struct.pack('<I', len(new)) + chunk[8:32] + new + chunk[32 + xml_size:]
    # Only the chunk size changes; Surge leaves the header's byteSize at 0, and so does this.
    return fxp[:56] + struct.pack('>I', len(chunk)) + chunk + fxp[60 + size:], changed


def board_intact(pylib, record_sha256=None):
    """Whether the installed pedalboard is the files pip installed: every file its RECORD lists has the SHA-256 and
    size given there (PEP 376 and the wheel format: RECORD is the installer's list of the files it wrote, with their
    hashes), and no code file is in the package that RECORD does not list. With `record_sha256` (bootstrap writes it),
    RECORD itself must be the one installed."""
    pylib = Path(pylib)
    records = sorted(pylib.glob('pedalboard-*.dist-info/RECORD'))
    if len(records) != 1:
        return False
    data = records[0].read_bytes()
    if record_sha256 and _sha(data) != record_sha256:
        return False
    listed = set()
    for row in csv.reader(data.decode('utf-8').splitlines()):
        if len(row) < 3 or not row[1]:
            continue  # RECORD lists itself (and .pyc files) without a hash
        name, (algo, _, digest) = row[0], row[1].partition('=')
        f = (pylib / name).resolve()
        if pylib.resolve() not in f.parents or algo != 'sha256' or not f.is_file() or str(f.stat().st_size) != row[2]:
            return False
        h = hashlib.sha256(f.read_bytes()).digest()
        if base64.urlsafe_b64encode(h).rstrip(b'=').decode() != digest:
            return False
        listed.add(f)
    code = ('.py', '.pyd', '.so', '.dll', '.dylib')
    return not any(f.suffix in code and f.resolve() not in listed for f in (pylib / 'pedalboard').rglob('*'))


def load_catalog(skill):
    """{patch id: what music-production's catalog says of it}: every patch Surge XT comes with, measured."""
    path = Path(skill) / 'references' / 'surge-catalog.json'
    try:
        presets = json.loads(path.read_text(encoding='utf-8'))['presets']
    except (OSError, ValueError, KeyError, TypeError):
        raise SurgeError(f'music-production ({_version(skill)}) has no Surge catalog this skill can read') from None
    if not isinstance(presets, dict):
        raise SurgeError(f'music-production ({_version(skill)}) has no Surge catalog this skill can read')
    return presets


def refused(entry):
    """Why a catalog patch cannot play a layer's notes, or None."""
    for flag, why in UNPLAYABLE.items():
        if flag in entry.get('flags', []):
            return why
    return None


def find(presets, words='', role=None, character=None, project=''):
    """The ids of the patches that can be played and hold every word (or a regular form of it, whole) in their id, name, category or
    designer, of `role`, whose measured character holds each of the comma-separated `character` words. Each project sees them in an order
    of its own, the same each time, so that two games do not start from the same patches."""
    wanted = [_forms(w) for w in _words(words)]
    traits = [t.strip().lower() for t in (character or '').split(',') if t.strip()]
    found = []
    for key, entry in presets.items():
        if refused(entry) or (role and entry.get('role') != role):
            continue
        if not all(t in str(entry.get('character', '')).lower() for t in traits):
            continue
        held = set(_words(' '.join((key.replace('/', ' '), str(entry.get('name', '')), str(entry.get('category', '')), str(entry.get('source', ''))))))
        if all(forms & held for forms in wanted):
            found.append(key)
    return sorted(found, key=lambda key: hashlib.sha256(f'{project}\0{key}'.encode('utf-8')).digest())


def _flags(entry):
    return [f for f in entry.get('flags', []) if f not in UNPLAYABLE]


def _macro_labels(entry):
    macros = entry.get('macros') or {}
    return [f"{k}: {v.get('label')}" for k, v in sorted(macros.items()) if isinstance(v, dict) and v.get('label')]


def summary(key, entry):
    """One line for a list of patches."""
    m = entry.get('measured') or {}
    numbers = [f'{m[k]:g} {unit}' if unit != 'Hz' else f'centroid {m[k]:.0f} Hz' for k, unit in (('centroid_hz', 'Hz'), ('attack_s', 's attack'), ('tail_s', 's tail'))
               if isinstance(m.get(k), (int, float))]
    extra = f" [{', '.join(_flags(entry))}]" if _flags(entry) else ''
    labels = f" macros {', '.join(_macro_labels(entry))}" if _macro_labels(entry) else ''
    return f"{key}: {entry.get('character') or 'not measured'}; {', '.join(numbers)}{extra}{labels}"


def describe(key, entry):
    """A patch in full: what it is, how it measured, and what its controls move."""
    lines = [f"{key}: {entry.get('name')}, category {entry.get('category')}, role {entry.get('role')}, by {entry.get('source')}",
             f"  its own stated licence: {entry.get('license') or 'none'} (the audio you make with Surge XT is yours; the patch file is not shipped)",
             f'  {summary(key, entry).split(": ", 1)[1]}']
    if entry.get('transpose'):
        lines.append(f"  built to sound an octave off: its keys are played {entry['transpose']:+d} semitones, which this skill does, so write the pitch you want to hear")
    if entry.get('wheel'):
        lines.append(f"  mod wheel (`wheel`): {', '.join(str(t) for t in entry['wheel'])}")
    for number, macro in sorted((entry.get('macros') or {}).items()):
        if isinstance(macro, dict):
            lines.append(f"  macro {number} ({macro.get('label') or 'no label'}): {', '.join(str(t) for t in macro.get('targets', []))}")
    return lines


def status(config, skill):
    """What `doctor` says of Surge XT, which only surge layers need."""
    if skill is None:
        return "not found (only surge layers need it; music-production's setup installs it)"
    if config is None:
        return 'give --config to check it (only surge layers need it)'
    if not config.get('surge') or not config.get('pedalboard') or not config['surge'].get('catalog'):
        return "not in this configuration (only surge layers need it; music-production's bootstrap.py installs it)"
    try:
        Provider(None, config, skill)._locate()
        presets = load_catalog(skill)
    except SurgeError as error:
        return str(error)
    return f'OK ({sum(1 for entry in presets.values() if not refused(entry))} patches to play)'


def _write(path, data):
    with open(path, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class Provider:
    """The takes of one project, in `folder`. A take is replayed from there with no Surge. With `record`, one that is missing is
    played by the Surge of the music-production configuration `config`."""

    def __init__(self, folder, config=None, music_skill=None, record=False):
        self.folder = Path(folder) if folder else None
        self.config = config
        self.music_skill = music_skill
        self.record = record
        self.done = {}
        self.used = {}
        self.recorded = 0
        self._set_up = None
        self._presets = None

    def _no_link(self, path):
        if is_link(path):
            raise SurgeError(f'{Path(path).name} in the project is a link, which a Surge take is never read or written through')

    def _receipt(self, key):
        if self.folder is None:
            raise SurgeError(f"a Surge layer's take lives in {FOLDER} beside the project file: render it with sfx.py render")
        self._no_link(self.folder)
        path = self.folder / f'{key}.json'
        if not os.path.lexists(path):
            return None
        self._no_link(path)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        if not (isinstance(data, dict) and data.get('schema') == SCHEMA and data.get('key') == key and isinstance(data.get('wav'), str)
                and WAV_NAME.match(data['wav']) and data.get('wav_sha256') == data['wav'][:-4] and isinstance(data.get('patch'), dict)
                and isinstance(data['patch'].get('id'), str) and isinstance(data.get('made_with'), dict)):
            raise SurgeError(f'{path.name} in {FOLDER} is not a receipt this skill wrote: move it out of the folder')
        return data

    def _read(self, receipt):
        path = self.folder / receipt['wav']
        self._no_link(path)
        if not path.is_file():
            raise SurgeError(f"the recording of take {receipt['key'][:12]} ({receipt['patch']['id']}) is missing from {FOLDER}: put it back, "
                             f"or move its receipt {receipt['key']}.json out of the folder and record the take again")
        if _sha(path.read_bytes()) != receipt['wav_sha256']:
            raise SurgeError(f"the recording of take {receipt['key'][:12]} ({receipt['patch']['id']}) has changed since it was recorded: put the "
                             f"original back, or move {receipt['wav']} and its receipt out of the folder and record the take again")
        x, info = wav.read(path)
        if info['rate'] != RATE or info['channels'] != 2:
            raise SurgeError(f"the recording of take {receipt['key'][:12]} is not 48 kHz stereo")
        return np.asarray(x, dtype=np.float64)

    def _skill(self):
        try:
            skill = mpbridge.find_music_skill(self.music_skill)
        except mpbridge.MusicSkillError as error:
            raise SurgeError(str(error)) from None
        if skill is None:
            raise SurgeError('recording a Surge take needs the music-production skill, which installs Surge XT')
        return Path(skill)

    def _locate(self):
        """(plugin, pedalboard folder, host, skill folder), each checked as music-production checks them before it plays a part."""
        if self._set_up:
            return self._set_up
        if self.config is None:
            raise SurgeError("recording a Surge take needs music-production's configuration: add --config")
        found, board = self.config.get('surge'), self.config.get('pedalboard')
        if not found or not board or not found.get('catalog'):
            raise SurgeError("this music-production configuration has no Surge XT with its patches: music-production's bootstrap.py installs them")
        skill = self._skill()
        host = skill / 'scripts' / 'surge_host.py'
        if not host.is_file():
            raise SurgeError(f'this music-production ({_version(skill)}) has no scripts/surge_host.py')
        plugin, binary, pylib = Path(found['path']), Path(found.get('binary', found['path'])), Path(board['path'])
        same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        if not (same(binary, plugin) or any(same(plugin, up) for up in binary.parents)):
            raise SurgeError(f"the Surge XT binary checked ({binary.name}) is not the plugin that would load ({plugin.name}): run music-production's bootstrap.py again")
        if not binary.is_file() or _sha(binary.read_bytes()) != found.get('sha256'):
            raise SurgeError(f"the Surge XT plugin is missing or changed ({binary.name}): run music-production's bootstrap.py again")
        if not (pylib / 'pedalboard').is_dir() or not board_intact(pylib, board.get('record_sha256')):
            raise SurgeError("pedalboard is not the package music-production installed: run its bootstrap.py again")
        self._set_up = plugin, pylib, host, skill
        return self._set_up

    def _entry(self, patch):
        if self._presets is None:
            self._presets = load_catalog(self._skill())
        found = self._presets.get(patch)
        if found is None:
            raise SurgeError(f"there is no patch '{patch}' in Surge's catalog (sfx.py surge --find WORDS lists them)")
        why = refused(found)
        if why:
            raise SurgeError(f"patch '{patch}' cannot be used: {why}")
        return found

    def check(self, source):
        """Before a render makes its folder: this layer's take is there, or may be recorded."""
        perf = performance(source)
        if self._receipt(key_of(perf)) is None:
            if not self.record:
                raise self._no_take(perf)
            self._locate()
            self._entry(perf['patch'])

    @staticmethod
    def _no_take(perf):
        return SurgeError(f"its Surge layer ({perf['patch']}) has no recorded take: render with --record-takes to record one, or bring back the "
                          f"project's {FOLDER} folder")

    def render(self, source):
        """The layer's take as stereo frames (n x 2) at a peak of PEAK."""
        perf = performance(source)
        key = key_of(perf)
        if key in self.done:
            return self.done[key]
        receipt = self._receipt(key)
        if receipt is None:
            if not self.record:
                raise self._no_take(perf)
            receipt = self._record(perf, key)
        x = self._read(receipt)
        self.done[key] = x
        self.used[key] = receipt
        return x

    def _record(self, perf, key):
        plugin, pylib, host, skill = self._locate()
        entry = self._entry(perf['patch'])
        patch = Path(self.config['surge']['catalog']) / entry['path']
        blob = patch.read_bytes() if patch.is_file() else b''
        if len(blob) != entry['bytes'] or _sha(blob) != entry['sha256']:
            raise SurgeError(f"the patch file of '{perf['patch']}' is missing or changed: run music-production's bootstrap.py again")
        self._no_link(self.folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._no_link(self.folder / '.lock')
        with locked(self.folder):
            found = self._receipt(key)             # another render may have recorded it while this one waited
            if found is not None:
                return found
            return self._play(perf, key, entry, blob, patch, plugin, pylib, host, skill)

    def _play(self, perf, key, entry, blob, patch, plugin, pylib, host, skill):
        work = self.folder / WORK
        self._no_link(work)
        work.mkdir(exist_ok=True)
        hidden = self.folder / '.gdignore'             # inside a Godot project the editor would import every file here
        if not os.path.lexists(hidden):
            with open(hidden, 'xb'):
                pass
        job_file, out, staged_wav, staged_receipt, private = (work / n for n in ('job.json', 'take.wav', 'new.wav', 'new.json', 'patch.fxp'))
        for path in (job_file, out, staged_wav, staged_receipt, private):
            self._no_link(path)
        fxp, synced = at_tempo(blob, perf['bpm'])
        if synced:
            _write(private, fxp)
            patch = private
        _write(out, b'')                               # so that a host writing nothing cannot pass off the last take
        events = midi_events(perf, int(entry.get('transpose', 0)))
        _write(job_file, json.dumps({'plugin': str(plugin), 'pylib': str(pylib), 'patch': str(patch), 'out': str(out), 'rate': RATE, 'events': events,
                                     'duration': LEAD_S + _note_end(perf) + perf['tail'], 'trim': int(round(LEAD_S * RATE)), 'macros': perf['macros'],
                                     'bend_range': perf['bend_range']}).encode('utf-8'))
        try:
            proc = subprocess.run([sys.executable, str(host), str(job_file)], cwd=str(host.parent), capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', env=mpbridge.child_env(), timeout=HOST_TIMEOUT_S, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise SurgeError(f"Surge XT did not finish '{perf['patch']}' in {HOST_TIMEOUT_S} s") from None
        if proc.returncode != 0:
            said = (proc.stdout + proc.stderr).strip()[-300:]
            if 'pedalboard' in said and re.search(r'ImportError|ModuleNotFoundError|DLL load failed', said):
                raise SurgeError(f"this Python ({sys.version_info[0]}.{sys.version_info[1]}) cannot load the pedalboard music-production installed: "
                                 f'run its bootstrap.py with this Python. {said}')
            raise SurgeError(f"Surge XT failed to play '{perf['patch']}' (music-production {_version(skill)}): {said}")
        try:
            data, info = wav.read(out)
        except ValueError as error:
            text = 'audio that is not finite' if 'finite' in str(error).lower() else f'no usable audio ({error})'
            raise SurgeError(f"Surge XT gave {text} for '{perf['patch']}' (music-production {_version(skill)})") from None
        x = np.asarray(data, dtype=np.float64)
        if info['rate'] != RATE or x.ndim != 2 or x.shape[1] != 2 or not len(x):
            raise SurgeError(f"Surge XT gave no 48 kHz stereo audio for '{perf['patch']}'")
        peak = float(np.max(np.abs(x)))
        if peak <= SILENT:
            raise SurgeError(f"this take of '{perf['patch']}' was silent: try other notes for this patch")
        encoded = wav.encode(x * (PEAK / peak), rate=RATE, bits=24, dither=False)
        digest = _sha(encoded)
        final = self.folder / f'{digest}.wav'
        self._no_link(final)
        if not final.exists():
            _write(staged_wav, encoded)
            os.replace(staged_wav, final)
        receipt = {'schema': SCHEMA, 'key': key, 'performance': perf, 'wav': final.name, 'wav_sha256': digest, 'frames': int(len(x)),
                   'patch': {k: entry.get(k) for k in ('name', 'source', 'category', 'license', 'sha256', 'transpose')} | {'id': perf['patch']},
                   'made_with': {'surge': self.config['surge'].get('version'), 'plugin_sha256': self.config['surge'].get('sha256'),
                                 'pedalboard': self.config['pedalboard'].get('version'), 'host_sha256': _sha(host.read_bytes()),
                                 'music_production': _version(skill)}}
        _write(staged_receipt, (json.dumps(receipt, indent=1) + '\n').encode('utf-8'))
        os.replace(staged_receipt, self.folder / f'{key}.json')
        self.recorded += 1
        return receipt

    def listing(self):
        """What the render played, for its record."""
        return [{'take': key, 'wav': r['wav'], 'wav_sha256': r['wav_sha256'], 'patch': r['patch'], 'made_with': r['made_with']}
                for key, r in sorted(self.used.items())]

    def size(self):
        """The bytes of the takes in the folder (no link is followed)."""
        if self.folder is None or is_link(self.folder) or not self.folder.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file() and not is_link(p))


class Players:
    """What a render plays recorded layers with: music-production's banks (or None) and the project's Surge takes."""

    def __init__(self, banks, takes):
        self.banks = banks
        self.takes = takes

    def render(self, source):
        if source.get('type') == 'surge':
            return self.takes.render(source)
        if self.banks is None:
            raise ValueError("an instrument layer plays on music-production's sample banks: render with its configuration (--config)")
        return self.banks.render(source)


def _version(skill):
    """music-production's version, from its SKILL.md, for messages and receipts."""
    try:
        found = re.search(r'version:\s*"?([0-9][^"\s]*)', (Path(skill) / 'SKILL.md').read_text(encoding='utf-8'))
    except OSError:
        return 'unknown'
    return found.group(1) if found else 'unknown'
