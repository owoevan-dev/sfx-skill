"""Standard MIDI Files for `sfx.py music`, so a game's music written in any program can be quoted as well.

Reads the format of The MIDI Association's "Standard MIDI Files 1.0" (RP-001): a header chunk (format 0 or 1, a division
in ticks per quarter note) and track chunks of events with variable-length delta times, running status, note-on with
velocity 0 as a note-off, and the meta events for tempo (FF 51), time signature (FF 58), track name (FF 03, read as UTF-8
when it is valid UTF-8, else as Latin-1) and the end of a track (FF 2F). Every other event is skipped by its length; chunks of other types are skipped whole. Format 2 (independent
patterns) and SMPTE divisions are refused in a sentence, as are a file over MAX_BYTES and a chunk that runs past the end.

A part is one track's notes on one channel. Channel 10 is General MIDI's percussion: its numbers are the keys of a kit.
A part's program is the last its own track sets on its channel by its first note (where its track has set none by then,
the one its channel has from the other tracks); its pedal and bend are its own track's and those of a track without notes
(a setup track). The title is the first track's name, else the file's.
A note-off ends the earliest note still open on its key and channel (so a note-on and a note-off of one key at one tick end the old
note, not the new one), a note still sounding when its key is struck again ends there, and a note left open at the end of its
track ends there.
Times come from the tempo map (120 quarter notes a minute until the first tempo event), bars from the time signatures
(4/4 until the first); a signature that does not fall on a bar line starts a new bar where it falls. Nothing in the file
is executed, and the quote is the file's notes as written."""
import json
import math
from pathlib import Path

from .music import MAX_LENGTH_S, MAX_NOTES, MusicError, NAMES, ON_THE_LINE, _and, _text

MAX_BYTES = 4 * 1024 * 1024
MAX_TRACK_CHUNKS = 256
DEFAULT_TEMPO_US = 500_000              # 120 quarter notes a minute, the format's default
DRUM_CHANNEL = 9                        # channel 10, counted from 1
SUSTAIN = 64
RESET_ALL = 121
TEMPO_SHOWN = 16
SUFFIXES = ('.mid', '.midi', '.smf')


def is_midi(path):
    return Path(path).suffix.lower() in SUFFIXES


def _vlq(data, pos, end):
    """A variable-length quantity (at most four bytes) at pos; returns (value, next pos)."""
    value = 0
    for i in range(4):
        if pos >= end:
            raise MusicError('the file ends inside a delta time or a length')
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MusicError('a delta time or a length runs past four bytes, which the format does not allow')


def _decoded(raw):
    """A track name: UTF-8 as most programs now write it, else one byte a character as older files have it."""
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    return text.strip() or None


def _track(data, pos, end, index):
    """The events of one track chunk: notes [(on tick, off tick, key, velocity, channel)], tempo and meter events, the name,
    programs [(tick, channel, program)], sustain and pitch-bend ticks per channel, and the last tick."""
    tick = 0
    status = None
    open_notes = {}
    notes, tempos, meters, programs = [], [], [], []
    controls = {}
    name = None
    ended = False
    seq = 0
    while pos < end:
        delta, pos = _vlq(data, pos, end)
        tick += delta
        seq += 1
        if pos >= end:
            raise MusicError(f'track {index} ends in the middle of an event')
        byte = data[pos]
        if byte == 0xFF:
            if pos + 2 > end:
                raise MusicError(f'track {index} ends in the middle of a meta event')
            kind = data[pos + 1]
            length, pos = _vlq(data, pos + 2, end)
            if pos + length > end:
                raise MusicError(f'track {index}: a meta event runs past the end of its track')
            body = data[pos:pos + length]
            pos += length
            status = None                           # a meta event cancels running status
            if kind == 0x51 and length == 3:
                us = int.from_bytes(body, 'big')
                if us > 0:
                    tempos.append((tick, seq, us))
            elif kind == 0x58 and length >= 2:
                numerator, power = body[0], body[1]
                thirty_seconds = body[3] if length >= 4 and body[3] else 8     # notated 32nd notes in a MIDI quarter note
                if 1 <= numerator <= 32 and power <= 6:
                    meters.append((tick, seq, numerator, 2 ** power, thirty_seconds))
            elif kind == 0x03 and name is None:
                name = _decoded(body)
            elif kind == 0x2F:
                ended = True
                break
            continue
        if byte in (0xF0, 0xF7):
            length, pos = _vlq(data, pos + 1, end)
            if pos + length > end:
                raise MusicError(f'track {index}: a system exclusive event runs past the end of its track')
            pos += length
            status = None
            continue
        if byte & 0x80:
            status = byte
            pos += 1
        elif status is None:
            raise MusicError(f'track {index}: a data byte comes with no status before it (running status without a status)')
        kind, channel = status & 0xF0, status & 0x0F
        size = 1 if kind in (0xC0, 0xD0) else 2
        if pos + size > end:
            raise MusicError(f'track {index} ends in the middle of a channel message')
        a = data[pos]
        b = data[pos + 1] if size == 2 else 0
        pos += size
        if a > 127 or b > 127:
            raise MusicError(f'track {index}: a channel message carries a value over 127')
        if kind == 0x90 and b > 0:
            open_notes.setdefault((channel, a), []).append((tick, b))
        elif kind == 0x80 or (kind == 0x90 and b == 0):
            waiting = open_notes.get((channel, a))
            if waiting:                             # the earliest note still open on this key ends here
                on, velocity = waiting.pop(0)
                notes.append((on, tick, a, velocity, channel))
        elif kind == 0xC0:
            programs.append((tick, channel, a))
        elif kind == 0xB0 and a == SUSTAIN:
            controls.setdefault(channel, []).append(('sustain pedal', tick, b >= 64))
        elif kind == 0xB0 and a == RESET_ALL:                   # Reset All Controllers: the pedal up, the bend centred
            controls.setdefault(channel, []).extend([('sustain pedal', tick, False), ('pitch bend', tick, False)])
        elif kind == 0xE0:
            controls.setdefault(channel, []).append(('pitch bend', tick, (a | (b << 7)) != 8192))
    for (channel, key), waiting in open_notes.items():
        notes += [(on, tick, key, velocity, channel) for on, velocity in waiting]
    return {'notes': _one_at_a_time(notes), 'tempos': tempos, 'meters': meters, 'programs': programs, 'controls': controls, 'name': name,
            'last': tick, 'ended': ended}


def _one_at_a_time(notes):
    """One note at a time on a key and channel: a note still sounding when its key is struck again ends there; of two note-ons
    at the same tick the one held longer is kept (the key sounds until its last release); a note with no length is dropped."""
    out, last = [], {}
    for on, off, key, velocity, channel in sorted(notes, key=lambda n: (n[4], n[2], n[0], -n[1])):
        prev = last.get((channel, key))
        if prev is not None and prev[0] == on:
            continue
        if prev is not None and on < prev[1]:
            out[prev[2]] = (prev[0], on, key, out[prev[2]][3], channel)
        if off > on:
            last[(channel, key)] = (on, off, len(out))
            out.append((on, off, key, velocity, channel))
    return [n for n in out if n[1] > n[0]]


def load(path):
    """The file's parts, tempo map and meter map as a dict (raises MusicError)."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise MusicError(f'{path}: {error.strerror or error}') from None
    if size > MAX_BYTES:
        raise MusicError(f'{path.name} is {size / 2 ** 20:.1f} MiB and a MIDI file here may be at most {MAX_BYTES // 2 ** 20} MiB')
    data = path.read_bytes()
    if len(data) < 14 or data[:4] != b'MThd':
        raise MusicError(f'{path.name} is not a Standard MIDI File (no MThd header)')
    length = int.from_bytes(data[4:8], 'big')
    if length < 6 or 8 + length > len(data):
        raise MusicError(f'{path.name}: its header chunk is cut short')
    fmt, count, division = (int.from_bytes(data[8 + i:10 + i], 'big') for i in (0, 2, 4))
    if fmt == 2:
        raise MusicError(f'{path.name} is a format 2 file (independent patterns), which has no single timeline to quote from')
    if fmt not in (0, 1):
        raise MusicError(f'{path.name}: format {fmt} is not a Standard MIDI File format')
    if division & 0x8000:
        raise MusicError(f'{path.name} counts time in SMPTE frames, not in ticks per quarter note, so it has no bars to quote')
    if division == 0:
        raise MusicError(f'{path.name}: its division is 0 ticks per quarter note')
    pos = 8 + length
    tracks = []
    while pos < len(data):
        if pos + 8 > len(data):
            raise MusicError(f'{path.name}: a chunk header is cut short at byte {pos}')
        kind, size_ = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], 'big')
        start, end = pos + 8, pos + 8 + size_
        if end > len(data):
            raise MusicError(f'{path.name}: a chunk runs past the end of the file')
        if kind == b'MTrk':
            if len(tracks) >= MAX_TRACK_CHUNKS:
                raise MusicError(f'{path.name} holds more than {MAX_TRACK_CHUNKS} tracks')
            tracks.append(_track(data, start, end, len(tracks) + 1))
        pos = end
    if not tracks:
        raise MusicError(f'{path.name} holds no track')
    tempos = sorted((t, i, s, us) for i, tr in enumerate(tracks) for t, s, us in tr['tempos'])     # file order within a tick
    tempos = [(t, us) for t, _, _, us in tempos]
    meters = sorted((t, i, s, n, d, bb) for i, tr in enumerate(tracks) for t, s, n, d, bb in tr['meters'])
    meters = list({t: (t, n, d, bb) for t, _, _, n, d, bb in meters}.values())          # the last at each tick holds
    song = {'path': path, 'format': fmt, 'ppq': division, 'tracks': tracks, 'last': max(tr['last'] for tr in tracks)}
    song['tempo'] = _tempo_map(tempos)
    song['bar_ticks'] = _bars(meters, division, max(song['last'], max((n[1] for tr in tracks for n in tr['notes']), default=0)))
    song['meters'] = meters if meters and meters[0][0] == 0 else [(0, 4, 4, 8)] + meters
    song['end'] = max([song['last']] + [n[1] for tr in tracks for n in tr['notes']])
    song['parts'] = _parts(tracks)
    song['title'] = tracks[0]['name'] or path.stem               # SMF 1.0: the first track's name names the sequence
    return song


def _tempo_map(tempos):
    """[(tick, us per quarter)], starting at tick 0; a later event at the same tick wins."""
    out = {0: DEFAULT_TEMPO_US}
    for tick, us in tempos:
        out[tick] = us
    return sorted(out.items())


def _bars(meters, ppq, last):
    """The tick at which each bar starts, the last entry the end of the final bar holding `last`."""
    changes = {0: (4, 4, 8)}
    for tick, n, d, bb in meters:
        changes[tick] = (n, d, bb)
    points = sorted(changes.items())
    starts = []
    tick = 0
    i = 0
    while True:
        while i + 1 < len(points) and points[i + 1][0] <= tick:
            i += 1
        n, d, bb = points[i][1]
        length = max(1, round(32 * n * ppq / (d * bb)))          # SMF 1.0: a bar is 32 nn / (2^dd bb) MIDI quarter notes
        nxt = points[i + 1][0] if i + 1 < len(points) else None
        starts.append(tick)
        if tick >= last and len(starts) > 1:
            break
        if nxt is not None and tick < nxt < tick + length:     # a signature inside a bar starts a new bar where it falls
            tick = nxt
        else:
            tick += length
        if len(starts) > 100_000:
            break
    return starts


def _parts(tracks):
    """Tracks that share a channel are often separate instruments (on two ports, FF 21, or a channel reused), so a part
    keeps its own track's program and pedal. A part whose track has set no program by its first note opens on the one its
    channel has from the other tracks then; a setup track without notes adds its controllers, in track order at one tick
    as a player takes them."""
    parts = []
    for index, tr in enumerate(tracks, start=1):
        channels = sorted({n[4] for n in tr['notes']})
        for channel in channels:
            notes = sorted(n for n in tr['notes'] if n[4] == channel)
            first = notes[0][0]
            pid = str(index) if len(channels) == 1 else f'{index}.{channel + 1}'
            own = [(t, p) for t, ch, p in tr['programs'] if ch == channel]
            rest = sorted(((t, p) for j, other in enumerate(tracks, start=1) if j != index
                           for t, ch, p in other['programs'] if ch == channel), key=lambda c: c[0])    # stable: track order
            program = next((p for t, p in reversed(own) if t <= first), None)
            if program is None:
                program = next((p for t, p in reversed(rest) if t <= first), None)
            later = [(t, p) for t, p in (own or rest) if t > first]
            controls = [e for j, other in enumerate(tracks, start=1) if j == index or not other['notes']
                        for e in other['controls'].get(channel, [])]
            parts.append({'id': pid, 'track': index, 'channel': channel + 1, 'name': tr['name'], 'program': program,
                          'later_programs': later, 'notes': notes, 'drums': channel == DRUM_CHANNEL, 'controls': controls})
    return parts


def seconds(song, tick):
    """The time of a tick in seconds from the file's start."""
    total, prev_tick, prev_us = 0.0, 0, DEFAULT_TEMPO_US
    for t, us in song['tempo']:
        if t >= tick:
            break
        total += (t - prev_tick) * prev_us
        prev_tick, prev_us = t, us
    return (total + (tick - prev_tick) * prev_us) / (song['ppq'] * 1e6)


def bar_of(song, tick):
    starts = song['bar_ticks']
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= tick:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def _note_name(key):
    return f'{NAMES[key % 12]}{key // 12 - 1}'


def overview(song):
    """The lines `sfx.py music FILE.mid` prints."""
    bars = len(song['bar_ticks']) - 1
    lines = [f"{_text(song['title'])}: a Standard MIDI File (format {song['format']}, {song['ppq']} ticks a quarter note), {bars} bar"
             f"{'s' if bars != 1 else ''}, {seconds(song, song['end']):g} s"]
    shown = [f"{60e6 / us:g} at bar {bar_of(song, t)}" for t, us in song['tempo'][:TEMPO_SHOWN]]
    more = len(song['tempo']) - len(shown)
    lines.append('Tempo (quarter notes a minute): ' + '; '.join(shown) + (f'; {more} more changes (--json lists them)' if more else ''))
    lines.append('Meter: ' + '; '.join(f'{n}/{d} from bar {bar_of(song, t)}' for t, n, d, _ in song['meters']))
    lines += ['', 'Parts (a track\'s notes on one channel):']
    for part in song['parts']:
        lo, hi = min(n[2] for n in part['notes']), max(n[2] for n in part['notes'])
        first, last = bar_of(song, part['notes'][0][0]), bar_of(song, max(n[0] for n in part['notes']))
        words = [f"{part['id']}: {_text(part['name']) if part['name'] else 'no name'}", f"channel {part['channel']}"]
        if part['drums']:
            words.append("General MIDI percussion (its notes are a kit's keys)")
        elif part['program'] is not None:
            words.append(f"program {part['program']} (General MIDI, counted from 0)")
        if part['later_programs'] and not part['drums']:
            t, p = part['later_programs'][0]
            more = len(part['later_programs']) - 1
            words.append(f"then program {p} from bar {bar_of(song, t)}" + (f" and {more} more change{'s' if more > 1 else ''}" if more else ''))
        span = 'bar ' + str(first) if first == last else f'bars {first}-{last}'
        range_ = f'keys {lo}-{hi}' if part['drums'] else f'{_note_name(lo)} to {_note_name(hi)}'
        words.append(f"{len(part['notes'])} notes, {range_}, {span}")
        lines.append('  ' + ', '.join(words))
    if not song['parts']:
        lines.append('  none: this file holds no notes')
    example = f'--bars 1-{min(2, bars)}' if bars > 1 else '--bars 1'
    lines += ['', f'Quote a part for an instrument or surge layer: sfx.py music FILE.mid --track ID {example}']
    return lines


def describe(song):
    """The JSON form of the overview."""
    return {'title': song['title'], 'format': song['format'], 'ppq': song['ppq'], 'bars': len(song['bar_ticks']) - 1,
            'seconds': round(seconds(song, song['end']), 4),
            'tempo': [{'bar': bar_of(song, t), 'tick': t, 'bpm': round(60e6 / us, 4)} for t, us in song['tempo']],
            'meter': [{'bar': bar_of(song, t), 'tick': t, 'meter': [n, d]} for t, n, d, _ in song['meters']],
            'parts': [{'id': p['id'], 'track': p['track'], 'channel': p['channel'], 'name': p['name'], 'program': p['program'],
                       'later_programs': [{'bar': bar_of(song, t), 'program': pr} for t, pr in p['later_programs']],
                       'drums': p['drums'], 'notes': len(p['notes']), 'lowest': min(n[2] for n in p['notes']),
                       'highest': max(n[2] for n in p['notes'])} for p in song['parts']]}


def quote(song, part_id, first, last):
    """The notes of one part in bars first to last, as [time s, MIDI key, length s, velocity] from the first bar's start, in
    the dict music.quote gives (with 'controls' for a sustain pedal or pitch bend the part sends in those bars)."""
    parts = {p['id']: p for p in song['parts']}
    if part_id not in parts:
        have = ', '.join(parts) if parts else 'none'
        raise MusicError(f"there is no part '{_text(part_id)}': this file's parts are {have} (sfx.py music FILE.mid lists them)")
    part = parts[part_id]
    bars = len(song['bar_ticks']) - 1
    if not 1 <= first <= last <= bars:
        raise MusicError(f'bars {first}-{last}: this file has bars 1 to {bars}')
    start, stop = song['bar_ticks'][first - 1], song['bar_ticks'][last]
    zero = seconds(song, start)
    length = seconds(song, stop) - zero
    span = f'bar {first}' if first == last else f'bars {first}-{last}'
    if length > MAX_LENGTH_S + 1e-9:
        raise MusicError(f'{span} last {length:.1f} s and a layer holds {MAX_LENGTH_S:g} s: ask for fewer bars')
    picked = [n for n in part['notes'] if start <= n[0] < stop]
    if not picked:
        bars_with = sorted({bar_of(song, n[0]) for n in part['notes']})
        raise MusicError(f"part '{_text(part_id)}' has no notes in {span}: it plays in bars {bars_with[0]} to {bars_with[-1]}")
    if len(picked) > MAX_NOTES:
        raise MusicError(f'{span} of part {_text(part_id)} hold {len(picked)} notes and a layer holds {MAX_NOTES}: ask for fewer bars')
    notes, cut = [], 0
    for on, off, key, velocity, _ in picked:
        if off > stop:
            cut += 1
        at = seconds(song, on) - zero
        notes.append([round(at, 4), key, max(0.0001, round(seconds(song, min(off, stop)) - zero - at, 4)), velocity])
    rang_in = sum(1 for n in part['notes'] if n[0] < start < n[1])
    controls = sorted({kind for kind in ('sustain pedal', 'pitch bend') if _in_effect(part['controls'], kind, start, stop)})
    return {'track': part_id, 'bars': [first, last], 'start_s': round(zero, 4), 'length_s': round(length, 4), 'notes': notes,
            'cut': cut, 'rang_in': rang_in, 'controls': controls, 'drums': part['drums'], 'source': 'midi'}


def _in_effect(events, kind, start, stop):
    """True when the controller `kind` is down (a pedal) or off centre (a bend) at some moment of [start, stop): held from
    before the bars or set within them."""
    state = False
    for k, tick, on in sorted((e for e in events if e[0] == kind), key=lambda e: e[1]):
        if tick >= stop:
            break
        if tick <= start:                                        # at the bars' first tick it has already changed
            state = on
        elif state or on:
            return True
    return state


def quote_lines(q):
    """The lines `sfx.py music FILE.mid --track --bars` prints."""
    first, last = q['bars']
    span = f'bar {first}' if first == last else f'bars {first}-{last}'
    count = len(q['notes'])
    lines = [f"part {_text(q['track'])}, {span}: {count} note{'s' if count != 1 else ''} over {q['length_s']:g} s as the file writes them. "
             f"Time 0 is the start of bar {first}, {q['start_s']:g} s into the file. For an instrument or surge layer:",
             '"notes": ' + json.dumps(q['notes'])]
    if q['cut']:
        lines.append(f"{q['cut']} note{' ran' if q['cut'] == 1 else 's ran'} past the end of bar {last} and {'is' if q['cut'] == 1 else 'are'} cut there.")
    if q['rang_in']:
        lines.append(f"{q['rang_in']} note{'' if q['rang_in'] == 1 else 's'} sounding across the start of bar {first} "
                     f"{'is' if q['rang_in'] == 1 else 'are'} not included.")
    if q['controls']:
        lines.append('The file also sends ' + _and(list(q['controls'])) + ' in these bars, which these notes leave out.')
    if q['drums']:
        lines.append('This is a General MIDI percussion part: its notes are the keys of a kit, which a pitched instrument plays as plain pitches.')
    lines.append('A quoted melody keeps its rights: quote music that is yours or that you may use.')
    return lines
