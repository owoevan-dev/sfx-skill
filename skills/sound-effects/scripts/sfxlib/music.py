"""Reading a music-production project: what its file declares, and the written notes of the bars an agent names.

This reads one song.json and needs nothing else of music-production. It shows what the project says and works nothing out: it
detects no key, finds no motif and fits nothing to the music. Which notes go into a sound is the agent's choice.

Clock counts seconds as Clock does in music-production 0.9.2, scripts/musiclib/model.py:

    MIT License, Copyright (c) 2026 music-skill contributors; the full notice is in
    LICENSES/music-production-MIT.txt beside this skill."""
import bisect
import json
import math
import re
import unicodedata
from pathlib import Path

from .project import MAX_LENGTH_S, MAX_NOTES

SCHEMA = 'music-production/v1'
MAX_FILE_BYTES = 256 * 1024 * 1024      # music-production's 500,000 notes, at some 300 bytes each with their performance
NAMES = 'C C# D Eb E F F# G Ab A Bb B'.split()      # as music-production's review spells pitches
LETTERS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
SHARPS, FLATS = '#' + chr(0x266F), 'b' + chr(0x266D)
UNPITCHED = ('drum_synth', 'noise')     # engines whose notes name no pitch
ON_THE_LINE = 1e-6                      # quarter notes: a note this close to a bar line is on it
TRACK_ID = re.compile('[a-z][a-z0-9_]{0,47}')     # music-production's track ids
NOTE_ID_CHARS = 120                     # and its longest note id
SUSTAIN = 64                            # the controller of the sustain pedal, which holds notes past their written ends
MOD_WHEEL = 1
EXPRESSION = 11
BRIGHTNESS = 74
PEDALS = range(64, 70)                  # switch controllers, which step whatever a part's cc_mode says
# The controllers each engine plays, each with the value it rests at until the part sets it, as music-production renders a part:
# Surge XT is sent the mod wheel and the sustain pedal; the additive synth reads expression, brightness and the mod wheel (its
# vibrato); the built-in synthesizer, the drum machine and the noise engine read none. A SoundFont preset may answer any.
HEARD = {'surge': {MOD_WHEEL: 0, SUSTAIN: 0}, 'additive': {MOD_WHEEL: 32, EXPRESSION: 100, BRIGHTNESS: 66},
         'synth': {}, 'drum_synth': {}, 'noise': {}}
AT_REST = {MOD_WHEEL: 0, SUSTAIN: 0}    # on a SoundFont part, whose preset decides where its other controllers rest
MAX_TRACKS = 64                         # music-production's limits on a project
MAX_NOTES_IN_PROJECT = 500_000
CHUNK = 2 ** 20                         # a file is read this much at a time, so a small one takes a small buffer
TEMPO_SHOWN = 16                        # how many tempo points the overview names (--json gives them all)
WIDTH = 110                             # of a line of the chord list
PLAN_CHARS = 400                        # of a line of the plan (--json gives it whole)
_CHORD = re.compile(f'([A-Ga-g][{SHARPS}{FLATS}]?)([^ ]*?)(?:/([A-Ga-g][{SHARPS}{FLATS}]?))?')
_BARS = re.compile('([0-9]{1,4})(?:-([0-9]{1,4}))?')


class MusicError(ValueError):
    """A project that cannot be read, or a selection of it that cannot be quoted."""


def _finite(value):
    try:
        return math.isfinite(value)
    except OverflowError:               # an integer too large for a float
        return False


def _number(value, what, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _finite(value) \
            or (lo is not None and value < lo) or (hi is not None and value > hi):
        limits = '' if lo is None else f' from {lo:g}' + ('' if hi is None else f' to {hi:g}')
        raise MusicError(f'{what} is {value!r}: expected a number{limits}')
    return value


def _count(value, what, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise MusicError(f'{what} is {value!r}: expected a whole number from {lo} to {hi}')
    return value


def _text(value, limit=160):
    """Text of the project on one line of output: no control, format or line-separator characters."""
    kept = ''.join(' ' if unicodedata.category(ch) in ('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp') else ch for ch in str(value))
    kept = ' '.join(kept.split())
    return kept if len(kept) <= limit else kept[:limit - 3] + '...'


class Clock:
    """Quarter notes to seconds as music-production counts them: `tempo` is a step map of [quarter note, quarters a minute],
    each tempo held as the whole microseconds per quarter note that MIDI stores. Bars are counted from one."""

    def __init__(self, clock):
        if not isinstance(clock, dict):
            raise MusicError("the project's clock is not an object")
        self.bars = _count(clock.get('bars'), 'clock.bars', 1, 512)
        meter = clock.get('meter')
        if not (isinstance(meter, list) and len(meter) == 2):
            raise MusicError(f'clock.meter is {meter!r}: expected [beats, beat unit], such as [6, 8]')
        beats, unit = _count(meter[0], "the meter's beats", 1, 16), _count(meter[1], "the meter's beat unit", 2, 16)
        if unit not in (2, 4, 8, 16):
            raise MusicError(f'clock.meter is {meter!r}: the beat unit is 2, 4, 8 or 16')
        self.meter = [beats, unit]
        self.quarters_per_bar = beats * 4 / unit
        tempo = clock.get('tempo')
        if not (isinstance(tempo, list) and 1 <= len(tempo) <= 2048 and all(isinstance(p, list) and len(p) == 2 for p in tempo)):
            raise MusicError('clock.tempo is not a list of 1 to 2048 points [quarter note, quarters a minute]')
        self.beats = [float(_number(p[0], 'a position in clock.tempo', 0, self.quarters_per_bar * self.bars)) for p in tempo]
        self.bpm = [float(_number(p[1], 'a tempo in clock.tempo', 30, 300)) for p in tempo]
        if self.beats[0] != 0 or any(b <= a for a, b in zip(self.beats, self.beats[1:])):
            raise MusicError('clock.tempo starts at quarter note 0 and moves forward')
        self.spb = [round(60_000_000 / bpm) / 1_000_000 for bpm in self.bpm]
        self.times = [0.0]
        for i in range(1, len(self.beats)):
            self.times.append(self.times[-1] + (self.beats[i] - self.beats[i - 1]) * self.spb[i - 1])
        self.tail = float(_number(clock.get('tail_seconds', 5), 'clock.tail_seconds', 1, 15))
        self.end = self.seconds(self.quarters_per_bar * self.bars)

    def seconds(self, beat):
        i = max(0, bisect.bisect_right(self.beats, beat) - 1)
        return self.times[i] + (beat - self.beats[i]) * self.spb[i]

    def bar_start(self, bar):
        return (bar - 1) * self.quarters_per_bar

    def bar_of(self, beat):
        return min(self.bars, int((beat + ON_THE_LINE) // self.quarters_per_bar) + 1)

    def place(self, beat):
        """(bar, beat of that bar), both counted from one; the beat in quarter notes."""
        bar = self.bar_of(beat)
        return bar, beat - self.bar_start(bar) + 1


def load(path):
    """The project at this path; MusicError says what is wrong with it."""
    path = Path(path)
    if not path.exists():
        raise MusicError(f'cannot read {path}: there is no such file')
    if not path.is_file():
        raise MusicError(f'{path} is not a file')
    try:
        chunks, size = [], 0
        with open(path, 'rb') as f:
            while size <= MAX_FILE_BYTES:                # never more, whatever the file says of its size
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
    except OSError as error:
        raise MusicError(f'cannot read {path}: {error}') from error
    if size > MAX_FILE_BYTES:                            # before the pieces are joined, which would hold them twice
        raise MusicError(f'{path} holds more than {MAX_FILE_BYTES:,} bytes, more than a music-production project holds')
    data = b''.join(chunks)
    del chunks
    try:
        song = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise MusicError(f'{path} is no readable JSON file: {_text(error)}') from None
    found = song.get('schema') if isinstance(song, dict) else None
    if found != SCHEMA:
        raise MusicError(f"{path} is not a music-production project (its schema is {_text(found)!r}, not '{SCHEMA}')")
    for key, kind in (('sections', list), ('tracks', list)):
        if not isinstance(song.get(key), kind):
            raise MusicError(f"{path}: the project's {key} is not a list")
    if not 1 <= len(song['tracks']) <= MAX_TRACKS:
        raise MusicError(f"{path}: a music-production project has 1 to {MAX_TRACKS} tracks, and this one has {len(song['tracks'])}")
    notes = sum(len(t['notes']) for t in song['tracks'] if isinstance(t, dict) and isinstance(t.get('notes'), list))
    if notes > MAX_NOTES_IN_PROJECT:
        raise MusicError(f'{path}: a music-production project holds at most {MAX_NOTES_IN_PROJECT:,} notes, and this one {notes:,}')
    return song


def _pitch_class(name):
    value = LETTERS[name[0].upper()]
    for mark in name[1:]:
        value += 1 if mark in SHARPS else -1 if mark in FLATS else 0
    return value % 12


def sounding(symbol, semitones):
    """A chord symbol moved by this many semitones, its root and its bass; None when it has no root to move."""
    match = _CHORD.fullmatch(symbol.strip())
    if match is None:
        return None
    root, quality, bass = match.groups()
    moved = NAMES[(_pitch_class(root) + semitones) % 12] + quality
    return moved + '/' + NAMES[(_pitch_class(bass) + semitones) % 12] if bass else moved


def _chord(entry):
    """One entry of the present harmony list, or None for anything else."""
    if not (isinstance(entry, dict) and isinstance(entry.get('symbol'), str) and isinstance(entry.get('bar'), int)
            and not isinstance(entry['bar'], bool)):
        return None
    offset, shift = entry.get('beat', 0), entry.get('transpose_semitones', 0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not _finite(offset) \
            or isinstance(shift, bool) or not isinstance(shift, int):
        return None
    row = {'bar': entry['bar'], 'beat': offset + 1, 'symbol': entry['symbol']}
    if shift:
        row['transpose_semitones'] = shift
        row['sounding'] = sounding(entry['symbol'], shift)
    return row


def _harmony(written):
    """The declared chords in order; an older free-form harmony comes back as it is."""
    if written is None:
        return None
    rows = [_chord(entry) for entry in written] if isinstance(written, list) else [None]
    if any(row is None for row in rows):
        return {'as_written': written}
    return sorted(rows, key=lambda row: (row['bar'], row['beat']))


def _notes(track, total):
    """A part's written notes in order: (on, duration, pitch, id), in quarter notes, each within the score's `total`."""
    tid = track.get('id')
    written = track.get('notes')
    if not isinstance(written, list):
        raise MusicError(f"track {_text(tid)}: its notes are not a list")
    out = []
    for note in written:
        if not (isinstance(note, dict) and isinstance(note.get('id'), str) and 1 <= len(note['id']) <= NOTE_ID_CHARS):
            raise MusicError(f'track {_text(tid)}: a note has no id of 1 to {NOTE_ID_CHARS} characters')
        where = f'track {_text(tid)}, note {_text(note["id"])}'
        out.append((float(_number(note.get('on'), f'{where}: on', 0, total)), float(_number(note.get('duration'), f'{where}: duration', 1e-9, total)),
                    _count(note.get('pitch'), f'{where}: pitch', 0, 127), note['id']))
    return sorted(out, key=lambda n: n[:3])


def _kit(track):
    """A sampled part on a kit's bank (128), as music-production's renderer reads it: the patch's bank if it names one, else the
    part's own. A synthesizer's bank says nothing of its sound."""
    holder = track['patch'] if isinstance(track.get('patch'), dict) and track['patch'] else track
    return track.get('engine') == 'soundfont' and holder.get('bank', 0) == 128


def _pitched(track):
    return track.get('drums') is not True and track.get('engine') not in UNPITCHED and not _kit(track)


def _track_row(track, clock):
    if not isinstance(track, dict) or not isinstance(track.get('id'), str) or not TRACK_ID.fullmatch(track['id']):
        raise MusicError(f"a track of this project has no track id (lower-case letters, digits and underscores): {_text(track.get('id') if isinstance(track, dict) else track, 60)}")
    notes = _notes(track, clock.quarters_per_bar * clock.bars)
    row = {'id': track['id'], 'role': track.get('role'), 'engine': track.get('engine'), 'notes': len(notes)}
    for key in ('name', 'range'):
        if key in track:
            row[key] = track[key]
    if notes:
        row['first_bar'], row['last_bar'] = clock.bar_of(notes[0][0]), max(clock.bar_of(n[0]) for n in notes)
        if _pitched(track):
            pitches = {n[2] for n in notes}
            row['lowest'], row['highest'] = min(pitches), max(pitches)
            row['pitch_classes'] = [NAMES[pc] for pc in sorted({p % 12 for p in pitches})]
    return row


def _section_row(section, clock):
    if not isinstance(section, dict):
        raise MusicError('a section of this project is not an object')
    name = _text(section.get('id'))
    start = _count(section.get('start_bar'), f'section {name}: start_bar', 0, clock.bars - 1)
    bars = _count(section.get('bars'), f'section {name}: bars', 1, clock.bars - start)
    first, last = start + 1, start + bars
    row = {'id': section.get('id'), 'bars': [first, last], 'start_s': round(clock.seconds(clock.bar_start(first)), 3),
           'end_s': round(clock.seconds(clock.bar_start(last + 1)), 3)}
    if 'intent' in section:
        row['intent'] = section['intent']
    return row


def describe(song):
    """What the project declares, as one dictionary (the command's --json)."""
    clock = Clock(song.get('clock'))
    rubato = bool(song['clock'].get('rubato'))          # it stretches the played length, which is not worked out here
    tempo = []
    for beat, bpm in zip(clock.beats, clock.bpm):
        bar, within = clock.place(beat) if beat < clock.quarters_per_bar * clock.bars - ON_THE_LINE else (clock.bars + 1, 1.0)   # at the end
        tempo.append({'bar': bar, 'beat': within, 'bpm': bpm})
    return {'title': song.get('title'), 'meter': clock.meter, 'bars': clock.bars, 'quarters_per_bar': clock.quarters_per_bar, 'tempo': tempo,
            'rubato': rubato, 'score_seconds': round(clock.end, 3), 'tail_seconds': clock.tail,
            'render_seconds': None if rubato else round(clock.end + clock.tail, 3), 'sections': [_section_row(s, clock) for s in song['sections']],
            'harmony': _harmony(song.get('harmony')), 'plan': song.get('plan'), 'tracks': [_track_row(t, clock) for t in song['tracks']]}


def _and(items):
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def _at(bar, beat):
    return f'bar {bar}' if abs(beat - 1) < ON_THE_LINE else f'bar {bar} beat {beat:g}'


def _note_name(pitch):
    return f'{NAMES[pitch % 12]}{pitch // 12 - 1}'


def _track_line(row):
    words = [_text(row['id']), _text(row.get('role') or 'no role'), _text(row.get('engine') or 'no engine')]
    if isinstance(row.get('range'), list) and len(row['range']) == 2:
        words.append(f"range {_text(row['range'][0])}-{_text(row['range'][1])}")
    if not row['notes']:
        return '  ' + ', '.join(words) + ': no notes'
    held = f"{row['notes']} note{'s' if row['notes'] != 1 else ''} in bars {row['first_bar']}-{row['last_bar']}"
    if 'pitch_classes' in row:
        held += f": {_note_name(row['lowest'])} to {_note_name(row['highest'])}, pitch classes {' '.join(row['pitch_classes'])}"
    else:
        held += ' (a drum or noise part: its notes are no pitches)'
    return '  ' + ', '.join(words) + ': ' + held


def _harmony_lines(harmony):
    if not harmony:
        return ['Harmony: none declared. No key is worked out from the notes.']
    if isinstance(harmony, dict):
        return ['Harmony, as written in the project:', '  ' + _text(json.dumps(harmony['as_written'], ensure_ascii=False), 2000)]
    chords = []
    for row in harmony:
        chord = _text(row['symbol'])
        if 'transpose_semitones' in row:
            chord += f" ({row['transpose_semitones']:+d}: {_text(row['sounding'] or 'not a symbol it can move')})"
        chords.append(f"{_at(row['bar'], row['beat'])}: {chord}")
    lines, line = ['Harmony, as the project declares it:'], ''
    for chord in chords:                                # a line ends between two chords, never inside one
        if line and len(line) + len(chord) + 2 > WIDTH:
            lines.append('  ' + line)
            line = ''
        line += ('; ' if line else '') + chord
    return lines + ['  ' + line]


def _plan_lines(plan):
    """The plan as it is written, a line for each of its entries: it has no form to fill, so none is assumed."""
    def one(value):
        return _text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), PLAN_CHARS)
    if not isinstance(plan, dict):
        return ['  ' + one(plan)]
    lines = []
    for key, value in plan.items():
        if isinstance(value, list):
            lines += [f'  {_text(key)}:'] + [f'    {one(item)}' for item in value]
        else:
            lines.append(f'  {_text(key)}: {one(value)}')
    return lines


def overview(info):
    """The lines `sfx.py music` prints for a whole project."""
    numerator, unit = info['meter']
    lines = [f"{_text(info['title'] or 'Untitled piece')}: {numerator}/{unit}, {info['bars']} bar{'s' if info['bars'] != 1 else ''} "
             f"of {info['quarters_per_bar']:g} quarter notes"]
    shown = [f"{row['bpm']:g} at {'the end' if row['bar'] > info['bars'] else _at(row['bar'], row['beat'])}" for row in info['tempo'][:TEMPO_SHOWN]]
    more = len(info['tempo']) - len(shown)
    lines.append('Tempo (quarter notes a minute): ' + '; '.join(shown) + (f'; {more} more changes (--json lists them)' if more else ''))
    if info['rubato']:
        lines.append(f"Score: {info['score_seconds']:g} s as written. Its rubato changes the played length, which is not worked out here, and a render "
                     f"keeps {info['tail_seconds']:g} s more for its tail. Every time below is the written score's.")
    else:
        lines.append(f"Score: {info['score_seconds']:g} s. A render runs {info['tail_seconds']:g} s longer for its tail ({info['render_seconds']:g} s).")
    lines += ['', 'Sections:']
    for row in info['sections']:
        first, last = row['bars']
        span = f'bar {first}' if first == last else f'bars {first}-{last}'
        lines.append(f"  {_text(row['id'])}: {span}, {row['start_s']:g} to {row['end_s']:g} s" + (f" ({_text(row['intent'])})" if row.get('intent') else ''))
    lines.append('')
    lines += _harmony_lines(info['harmony'])
    if info['plan']:
        lines += ['', 'Plan, as the project declares it:'] + _plan_lines(info['plan'])
    lines += ['', 'Tracks:'] + [_track_line(row) for row in info['tracks']]
    lines += ['', 'Quote a part for an instrument or surge layer: sfx.py music SONG --track ID --bars 5-6']
    return lines


def parse_bars(text):
    """'5' or '5-6', counted from one, to (first, last)."""
    match = _BARS.fullmatch(text)
    first = int(match.group(1)) if match else 0
    last = int(match.group(2) or first) if match else 0
    if not 1 <= first <= last:
        raise MusicError(f"--bars {_text(text)!r}: expected a bar or a range counted from one, such as 5 or 5-6")
    return first, last


def _real(value, default):
    """A number the performance gives, else `default`: music-production validates a project, and this only reads one."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default


def _during(points, start, stop, rest, ramp):
    """The values a control takes from beat `start` up to `stop`, as music-production plays its [beat, value] points (in order):
    `rest` until the first point, which it may jump to, then each held to the next or, `ramp`, moving straight to it, the last
    held. A ramp passes only between its ends, so the value at `start`, at each point inside and, ramping, on the way to `stop`
    bound them all."""
    def at(beat, on):
        before = [p for p in points if (p[0] <= beat + ON_THE_LINE if on else p[0] < beat - ON_THE_LINE)]
        if not before:
            return rest
        (b0, v0), after = before[-1], points[len(before):]
        if not ramp or not after:
            return v0
        b1, v1 = after[0]
        return v0 + (v1 - v0) * (beat - b0) / (b1 - b0) if b1 > b0 else v1
    return [at(start, True)] + [v for b, v in points if start + ON_THE_LINE < b < stop - ON_THE_LINE] + ([at(stop, False)] if ramp else [])


def _pairs(items):
    """The [x, y] pairs of a list in the project, as (float, float), leaving out what is not one."""
    return [(float(p[0]), float(p[1])) for p in (items if isinstance(items, list) else [])
            if isinstance(p, list) and len(p) == 2 and _real(p[0], None) is not None and _real(p[1], None) is not None]


def _controls(track, performance, start, stop):
    """The bend and the controllers that reach beats `start` to `stop`, a value set before them too, as the part's engine hears them."""
    out = []
    bend = sorted(_pairs(performance.get('bend')), key=lambda p: p[0])         # of two points on one beat the later written holds
    if bend and any(abs(v) > 1e-9 for v in _during(bend, start, stop, 0.0, True)):
        out.append("this part's pitch bend")
    engine = track.get('engine')
    heard = HEARD.get(engine)                                                   # None: any controller, as a SoundFont preset may answer
    ramp = performance.get('cc_mode', 'linear' if engine == 'additive' else 'step') != 'step'
    by = {}
    for c in performance.get('cc') if isinstance(performance.get('cc'), list) else []:
        if isinstance(c, list) and len(c) == 3 and all(_real(x, None) is not None for x in c):
            by.setdefault(int(c[1]), []).append((float(c[0]), float(c[2])))
    pedal, wheel, others = False, False, []
    for control in sorted(by):
        if heard is not None and control not in heard:
            continue
        points = sorted(by[control])
        rest = (AT_REST if heard is None else heard).get(control)
        if rest is None:                       # a SoundFont controller rests where its preset puts it: any value set before the bars end holds in them
            if points[0][0] < stop - ON_THE_LINE:
                others.append(control)
            continue
        values = _during(points, start, stop, rest, ramp and control not in PEDALS)
        if control == SUSTAIN:
            pedal = pedal or max(values) >= 64
        elif any(abs(v - rest) > 1e-9 for v in values):
            wheel = wheel or control == MOD_WHEEL
            if control != MOD_WHEEL:
                others.append(control)
    out += ["this part's sustain pedal"] * pedal + ["this part's mod wheel"] * wheel
    if others:
        out.append(f"this part's controller{'s' if len(others) > 1 else ''} " + _and([f'CC{c}' for c in others]))
    return out


def _slurs(track, performance, every, picked, clock):
    """What a SoundFont part's legato (music-production's performance.legato) does to the selected notes, grouping the part's notes
    as it does by their played times: per-note timing and gate (swing moves a note's start and end alike, and is left aside)."""
    legato = performance.get('legato')
    if track.get('engine') != 'soundfont' or not isinstance(legato, dict):
        return []
    played = performance.get('notes') if isinstance(performance.get('notes'), dict) else {}
    notes = []
    for on, duration, pitch, note_id in every:
        entry = played.get(note_id) if isinstance(played.get(note_id), dict) else {}
        begin = clock.seconds(on) + _real(entry.get('timing_ms'), 0.0) / 1000
        notes.append((begin, begin + (clock.seconds(on + duration) - clock.seconds(on)) * _real(entry.get('gate'), 1.0), pitch, note_id))
    notes.sort(key=lambda n: (n[0], n[2]))
    chosen = {note[3] for note in picked}
    gap = _real(legato.get('gap_ms'), 40.0) / 1000
    if legato.get('mode') == 'crossfade':   # a note that follows its neighbour, not struck with others, enters from the bank's legato recording
        span = _real(legato.get('max_interval'), 12.0)
        onsets = {}
        for n in notes:
            onsets[n[0]] = onsets.get(n[0], 0) + 1
        enters = any(b[3] in chosen and b[0] - a[1] <= gap and b[2] != a[2] and abs(b[2] - a[2]) <= span and onsets[b[0]] == 1
                     for a, b in zip(notes, notes[1:]))
        return ["this part's crossfade legato"] if enters else []
    span, most = _real(legato.get('max_interval'), 5.0), _real(legato.get('max_notes'), 4.0)
    group, joined = [], 0
    for note in notes:
        if group and note[0] - group[-1][1] <= gap and note[2] != group[-1][2] and abs(note[2] - group[0][2]) <= span and len(group) < most:
            group.append(note)
            joined += note[3] in chosen
        else:
            group = [note]
    if not joined:
        return []
    one = joined == 1
    return [f"this part's slurs ({joined} of these notes {'glides' if one else 'glide'} on from the note before {'it' if one else 'them'}, "
            f"with no attack of {'its' if one else 'their'} own)"]


def _left_out(song, track, picked, every, clock, start, stop):
    """What the render plays beyond the written notes of this selection, from beat `start` up to `stop`: what reaches these bars,
    a controller set before them too, and what the part's engine plays."""
    performance = track.get('performance') if isinstance(track.get('performance'), dict) else {}
    played = performance.get('notes') if isinstance(performance.get('notes'), dict) else {}
    entries = [played.get(note[3]) or {} for note in picked]
    out = []
    if song['clock'].get('rubato'):
        out.append("the piece's rubato")
    key_map = track.get('key_map')
    mapped = isinstance(key_map, dict) and any(key_map.get(str(note[2]), note[2]) != note[2] for note in picked)
    if track.get('reverse') is True:              # each note renders on its own: no swing, timing, gate, offset, bend or controller applies
        return out + ['each note played backwards'] + (["this part's key map"] if mapped else [])
    swing = performance.get('swing')
    if isinstance(swing, dict) and swing.get('ratio', 0.5) != 0.5:
        out.append("this part's swing")
    if performance.get('offset_ms'):
        out.append("this part's time offset")
    if any(entry.get('timing_ms') for entry in entries):
        out.append('per-note timing')
    if any(entry.get('gate', 1) != 1 for entry in entries):
        out.append('per-note gate')
    out += _slurs(track, performance, every, picked, clock)
    if track.get('engine') == 'soundfont' and isinstance(performance.get('dynamics'), list) and performance['dynamics']:
        out.append("this part's dynamics curve, which sets its loudness in place of velocity")
    out += _controls(track, performance, start, stop)
    if mapped:
        out.append("this part's key map")
    return out


def quote(song, track_id, first, last):
    """The written notes of one part in bars first to last, as [time s, MIDI pitch, length s, velocity] from the first bar's start."""
    clock = Clock(song.get('clock'))
    tracks = {t['id']: t for t in song['tracks'] if isinstance(t, dict) and isinstance(t.get('id'), str)}
    if track_id not in tracks:
        raise MusicError(f"there is no track '{_text(track_id)}': this project has " + ', '.join(_text(key) for key in tracks))
    track = tracks[track_id]
    if not 1 <= first <= last <= clock.bars:
        raise MusicError(f'bars {first}-{last}: this piece has bars 1 to {clock.bars}')
    start, stop = clock.bar_start(first), clock.bar_start(last + 1)
    zero = clock.seconds(start)
    length = clock.seconds(stop) - zero
    span = f'bar {first}' if first == last else f'bars {first}-{last}'
    if length > MAX_LENGTH_S + 1e-9:
        raise MusicError(f'{span} last {length:.1f} s and a layer holds {MAX_LENGTH_S:g} s: ask for fewer bars')
    every = _notes(track, clock.quarters_per_bar * clock.bars)
    picked = [note for note in every if start - ON_THE_LINE <= note[0] < stop - ON_THE_LINE]
    if not picked:
        where = f'it plays in bars {clock.bar_of(every[0][0])} to {max(clock.bar_of(n[0]) for n in every)}' if every else 'it holds no notes at all'
        raise MusicError(f"track '{_text(track_id)}' has no notes in {span}: {where}")
    if len(picked) > MAX_NOTES:
        raise MusicError(f'{span} of {_text(track_id)} hold {len(picked)} notes and a layer holds {MAX_NOTES}: ask for fewer bars')
    performance = track.get('performance') if isinstance(track.get('performance'), dict) else {}
    played = performance.get('notes') if isinstance(performance.get('notes'), dict) else {}
    notes, cut = [], 0
    for on, duration, pitch, note_id in picked:
        entry = played.get(note_id)
        if not isinstance(entry, dict):
            raise MusicError(f'track {_text(track_id)}, note {_text(note_id)} has no velocity in performance.notes')
        velocity = _count(entry.get('velocity'), f'track {_text(track_id)}, note {_text(note_id)}: velocity', 1, 127)
        ends = on + duration
        if ends > stop + ON_THE_LINE:
            cut += 1
        at = max(0.0, clock.seconds(on) - zero)
        notes.append([round(at, 4), pitch, max(0.0001, round(clock.seconds(min(ends, stop)) - zero - at, 4)), velocity])
    rang_in = sum(1 for note in every if note[0] < start - ON_THE_LINE and note[0] + note[1] > start + ON_THE_LINE)
    return {'track': track_id, 'bars': [first, last], 'start_s': round(zero, 4), 'length_s': round(length, 4), 'notes': notes, 'cut': cut,
            'rang_in': rang_in, 'leaves_out': _left_out(song, track, picked, every, clock, start, stop), 'drums': not _pitched(track)}


def quote_lines(q):
    """The lines `sfx.py music --track --bars` prints."""
    first, last = q['bars']
    span = f'bar {first}' if first == last else f'bars {first}-{last}'
    count = len(q['notes'])
    lines = [f"{_text(q['track'])}, {span}: {count} note{'s' if count != 1 else ''} over {q['length_s']:g} s. Time 0 is the start of bar {first}, "
             f"{q['start_s']:g} s into the piece. For an instrument or surge layer:", '"notes": ' + json.dumps(q['notes'])]
    if q['cut']:
        lines.append(f"{q['cut']} note{' ran' if q['cut'] == 1 else 's ran'} past the end of bar {last} and {'is' if q['cut'] == 1 else 'are'} cut there.")
    if q['rang_in']:
        lines.append(f"{q['rang_in']} note{'' if q['rang_in'] == 1 else 's'} begun earlier still sound{'s' if q['rang_in'] == 1 else ''} at the start of bar {first} "
                     f"and {'is' if q['rang_in'] == 1 else 'are'} not included.")
    if q['leaves_out']:
        lines.append('This is the written score. The render also plays ' + _and(q['leaves_out']) + ', which these notes leave out.')
    if q['drums']:
        lines.append('This is a drum or noise part: its notes are the keys of its kit, which a pitched instrument plays as plain pitches.')
    return lines
