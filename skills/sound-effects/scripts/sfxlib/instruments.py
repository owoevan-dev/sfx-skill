"""Recorded layers: notes played on the sample banks of music-production's configuration by its FluidSynth.

A layer with source {"type": "instrument", "soundfont": "vcsl", "program": 12, "notes": [[0, 72, 0.5, 100]]} is
rendered as a MIDI file (midi.py) through the configured FluidSynth with its own reverb and chorus off, at 48 kHz in
floating point, so the recording comes back dry and on time, ready to be layered with synthesis. Only banks whose
configured licence puts no condition on the sound they make may be used (CC0, the University of Iowa recordings
"without restrictions", the Unlicense, MIT; music-production's soundbanks.md and SKILL.md state that music rendered
with the banks its setup installs needs no credit): a sound made with this skill needs no attribution. A bank added
under other terms is refused.

The rendering is a pure function of the notes, the preset and the bank file (its SHA-256 from the configuration), so
each distinct layer is rendered once per render folder and reused (instruments/<key>.wav beside the MIDI file)."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from . import midi, wav

RATE = 48000
PEAK = 0.5                 # a rendered layer comes at this peak before the layer's gain
TAIL_S = 3.0               # the MIDI file holds this long after the last note so releases ring out
ALLOWED_LICENCES = ('CC0', 'University of Iowa', 'Unlicense', 'MIT')


class InstrumentError(Exception):
    """A recorded layer cannot be made (no configuration, an unknown bank, a bank under other terms, FluidSynth)."""


def allowed(licence):
    return isinstance(licence, str) and licence.strip().startswith(ALLOWED_LICENCES)


class Provider:
    """Renders recorded layers with the FluidSynth and banks of a music-production configuration (a dict) into
    `folder` (made when the first layer is rendered)."""

    def __init__(self, config, folder):
        self.config = config
        self.folder = Path(folder)
        self.done = {}

    def bank(self, name):
        """(path, licence, sha256) of a configured bank; 'default' is the configuration's own soundfont."""
        entry = self.config.get('soundfont') if name == 'default' else (self.config.get('soundfonts') or {}).get(name)
        if not isinstance(entry, dict) or not entry.get('path'):
            names = ', '.join(sorted((self.config.get('soundfonts') or {}).keys()))
            raise InstrumentError(f"no bank '{name}' in the music-production configuration (it has: default, {names})")
        if not allowed(entry.get('license')):
            raise InstrumentError(f"bank '{name}' is licensed '{entry.get('license')}', which may put conditions on the "
                                  'sound; this skill uses only banks whose sound needs no credit (CC0, Iowa, Unlicense, '
                                  'MIT)')
        path = Path(entry['path'])
        if not path.is_file():
            raise InstrumentError(f"bank '{name}' is configured at {path}, which is not a file")
        return path, entry.get('license'), entry.get('sha256', '')

    def fluidsynth(self):
        fs = self.config.get('fluidsynth')
        path = fs.get('path') if isinstance(fs, dict) else fs
        if not path or not Path(path).is_file():
            raise InstrumentError('the music-production configuration names no FluidSynth program')
        return Path(path), (fs.get('sha256', '') if isinstance(fs, dict) else '')

    def render(self, source):
        """The layer's recording as stereo frames (n x 2) at a peak of PEAK."""
        name = source.get('soundfont', 'default')
        bank_path, _, bank_sha = self.bank(name)
        fs_path, fs_sha = self.fluidsynth()
        notes = [tuple(float(v) if i in (0, 2) else int(v) for i, v in enumerate(n)) for n in source['notes']]
        key_data = {'bank': name, 'bank_sha256': bank_sha, 'fluidsynth_sha256': fs_sha,
                    'program': int(source.get('program', 0)), 'bank_number': int(source.get('bank', 0)),
                    'drums': bool(source.get('drums', False)), 'notes': notes, 'tail_s': TAIL_S}
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode('utf-8')).hexdigest()[:24]
        if key in self.done:
            return self.done[key]
        self.folder.mkdir(parents=True, exist_ok=True)
        mid, out = self.folder / f'{key}.mid', self.folder / f'{key}.wav'
        if not out.is_file():
            mid.write_bytes(midi.smf(notes, key_data['program'], key_data['bank_number'], key_data['drums'], TAIL_S))
            command = [str(fs_path), '-n', '-i', '-q', '-R', '0', '-C', '0', '-g', '0.5', '-r', str(RATE),
                       '-o', 'synth.midi-bank-select=mma', '-O', 'float', '-T', 'wav', '-F', str(out),
                       str(bank_path), str(mid)]
            proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                  timeout=300)
            if proc.returncode != 0 or not out.is_file():
                raise InstrumentError(f'FluidSynth failed: {(proc.stdout + proc.stderr).strip()[-400:]}')
            (self.folder / f'{key}.json').write_text(json.dumps(key_data, indent=1) + '\n', encoding='utf-8',
                                                    newline='\n')
        data, info = wav.read(out)
        if info['rate'] != RATE:
            raise InstrumentError(f'FluidSynth wrote {info["rate"]} Hz, not {RATE}')
        x = np.asarray(data, dtype=np.float64).reshape(len(data), -1)
        if x.shape[1] == 1:
            x = np.repeat(x, 2, axis=1)
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        if peak <= 0:
            raise InstrumentError(f"the notes made no sound on bank '{name}' preset {key_data['bank_number']}:"
                                  f"{key_data['program']} (outside the preset's keys?)")
        x = x * (PEAK / peak)
        self.done[key] = x
        return x


PHDR_RECORD = 38           # bytes of a preset header (SoundFont 2.04, section 7.2)


def _chunks(f, end):
    """(id, start, size) of the RIFF chunks from the file position up to `end`, each word-aligned."""
    import struct
    while f.tell() + 8 <= end:
        tag, size = f.read(4), struct.unpack('<I', f.read(4))[0]
        start = f.tell()
        yield tag, start, size
        f.seek(start + size + (size & 1))


def presets(path):
    """[(bank, program, name)] of a SoundFont (SF2, or SF3, which keeps the same headers) in order, read from its
    preset headers: the 'phdr' chunk of the 'pdta' list, 38-byte records of a 20-byte name, the preset (program) and
    bank numbers and three fields not needed here, closed by a terminal record (the SoundFont 2.04 specification,
    sections 5 and 7.2). Sample data is skipped, not read."""
    import struct
    path = Path(path)
    out = []
    with path.open('rb') as f:
        if f.read(4) != b'RIFF':
            raise InstrumentError(f'{path.name} is not a RIFF file')
        size = struct.unpack('<I', f.read(4))[0]
        if f.read(4) != b'sfbk':
            raise InstrumentError(f'{path.name} is not a SoundFont')
        for tag, start, length in list(_chunks(f, 8 + size)):
            if tag != b'LIST':
                continue
            f.seek(start)
            if f.read(4) != b'pdta':
                continue
            for sub, sub_start, sub_size in list(_chunks(f, start + length)):
                if sub != b'phdr':
                    continue
                f.seek(sub_start)
                data = f.read(sub_size)
                for i in range(sub_size // PHDR_RECORD - 1):             # the last record is the terminal one
                    record = data[i * PHDR_RECORD:(i + 1) * PHDR_RECORD]
                    name = record[:20].split(bytes(1))[0].decode('latin-1').strip()
                    program, bank = struct.unpack('<HH', record[20:24])
                    out.append((bank, program, name))
    return sorted(out)


def list_banks(config):
    """[{'name', 'licence', 'usable', 'presets', 'problem'}] for every bank of a music-production configuration
    ('default' first). Banks whose licence may put conditions on the sound are listed as not usable and not opened."""
    names = ['default'] + sorted((config.get('soundfonts') or {}).keys())
    out = []
    for name in names:
        entry = config.get('soundfont') if name == 'default' else config['soundfonts'][name]
        entry = entry if isinstance(entry, dict) else {}
        row = {'name': name, 'licence': entry.get('license'), 'usable': allowed(entry.get('license')), 'presets': [],
               'problem': None}
        if row['usable']:
            path = Path(entry.get('path') or '')
            if not entry.get('path') or not path.is_file():
                row['usable'], row['problem'] = False, 'its file is missing'
            else:
                try:
                    row['presets'] = presets(path)
                except (InstrumentError, OSError, ValueError) as error:
                    row['usable'], row['problem'] = False, str(error)
        out.append(row)
    return out


def placed(x, channels):
    """The recording for a sound of `channels`: a mono sound takes the louder channel, and a stereo sound keeps both
    unless they are anti-correlated, when the louder one plays in both; a mono recording plays in both channels.
    Spaced-microphone recordings can be partly out of phase between their channels and then cancel when summed
    (music-production's soundbank notes measure near-zero and negative left/right correlation on such samples)."""
    louder = int(np.argmax(np.sum(x ** 2, axis=0)))
    if channels == 1:
        return x[:, louder]
    if x.shape[1] == 1:              # a mono recording (FSD50K's are) in a stereo sound: centred in both channels
        return np.repeat(x, 2, axis=1) * np.sqrt(0.5)
    denom = float(np.sqrt(np.sum(x[:, 0] ** 2) * np.sum(x[:, 1] ** 2)))
    corr = float(np.sum(x[:, 0] * x[:, 1])) / denom if denom > 0 else 1.0
    if corr < 0:
        x = np.repeat(x[:, louder:louder + 1], 2, axis=1)
    return x * np.sqrt(0.5)          # each channel at the level of a centred mono layer


def detune(x, cents):
    """x (n x channels) played `cents` higher (or lower) by band-limited resampling: the frequency-domain picture is
    cut or padded to the new length, so the sound is shorter by the same ratio and nothing folds back."""
    if not cents:
        return x
    return _stretch(x, 2 ** (float(cents) / 1200))


def resample(x, from_rate, to_rate):
    """x (n x channels) recorded at from_rate as samples at to_rate: the same band-limited method, at the speed ratio
    from_rate / to_rate, so every frequency stays where it was."""
    if from_rate == to_rate:
        return x
    return _stretch(x, float(from_rate) / float(to_rate))


def _stretch(x, ratio):
    """x played `ratio` times faster (n / ratio samples long), its spectrum cut or padded in the frequency domain."""
    n = len(x)
    m = max(1, int(round(n / ratio)))
    pad = n + 4096                       # silence on both sides keeps the circular transform's ends apart
    spectrum = np.fft.rfft(np.concatenate([x, np.zeros((4096, x.shape[1]))]), axis=0)
    size = int(round(pad / ratio))
    keep = min(len(spectrum), size // 2 + 1)
    out = np.zeros((size // 2 + 1, x.shape[1]), dtype=complex)
    out[:keep] = spectrum[:keep]
    y = np.fft.irfft(out, size, axis=0) * (size / pad)
    return y[:m]
