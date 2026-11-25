"""A sound as numbers an agent can read: octave bands over time, tonal lines, events and the roll-off.

The agent cannot hear, and it reads exact values from a table far better than from a picture (Liu et al. 2023, "DePlot:
one-shot visual language reasoning by plot-to-table translation", Findings of ACL; Rahmanzadehgervi et al. 2024, "Vision
language models are blind", ACCV). The spectrogram stays as a second look for what no line here names.

- Bands over time: ten octave bands at the nominal centres of IEC 61260-1 (31.5 Hz to 16 kHz), made by this skill's own
  split (one FFT of the whole sound, each band a raised-cosine window on log frequency between the geometric midpoints of
  its neighbours, so the ten add up to the sound; not an IEC class filter). A cell is the band's loudest millisecond (1 ms
  RMS) in a slice of time, in dB under the loudest cell of the whole table. A one-shot's slices start at its onset (the
  first millisecond within 40 dB of its loudest) and double in width, fine where an attack lives and coarse where a tail
  does (this skill's choice); a loop has twelve equal slices.
- Tonal lines: spectral peaks at least 12 dB over the average of each side of the third of an octave around them (at least
  8 bins each side), whose instantaneous frequency agrees with both neighbouring bins' (Friedman 1985; Charpentier 1986),
  joined from frame to frame within 40 cents (McAulay and Quatieri 1986); a level from a parabola through the three values
  around each peak (Smith and Serra 1987, PARSHL). A line lasting at least 80 % of the active part at a multiple of 50 or 60 Hz is labelled
  as a mains frequency: hum in a recording, or a drone that was meant. A label, never a flag.
- Events: a millisecond in a band rising at least 12 dB over that band's loudest millisecond 5 to 25 ms before it
  (relative rises in bands: Klapuri 1999, ICASSP; Bello et al. 2005, IEEE TSAP 13(5)); rises within 30 ms are one event.
- Roll-off: the frequency under which 95 % of the active part's energy lies (Peeters et al. 2011, the Timbre Toolbox).

Thresholds are this skill's choices and named as such in references/checks.md."""
import math

import numpy as np

CENTRES = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
LABELS = ('31', '63', '125', '250', '500', '1k', '2k', '4k', '8k', '16k')
CROSS_OCT = 1 / 6                   # the width of the raised-cosine change from one band to the next, in octaves
FLOOR_DB = 60.0                     # cells further under the loudest print as '.'
ONSET_DB = 40.0                     # the onset: the first millisecond within this of the loudest (as traits' active_s)
EDGES_MS = tuple(5 * 2 ** k for k in range(16))   # 5, 10, 20, ... ms after the onset
BEFORE_MS = 5                       # anything more than this before the onset gets a slice of its own
LOOP_SLICES = 12
N_FFT = 4096                        # the tonal lines' long window (about 85 ms at 48 kHz)
SHORT_FFT = 1024                    # ... and the short one (about 21 ms), for quick glides
SHORT_FROM_HZ = 200.0
HOP_S = 0.005
PEAK_OVER_DB = 12.0
NEIGHBOUR_OCT = 1 / 3               # the stretch of spectrum a peak must stand over
NEIGHBOUR_BINS = 8                  # ... and at least this many bins to each side, past the window's main lobe
LINE_RANGE_DB = 60.0
JOIN_CENTS = 40.0
GAP_FRAMES = 1                      # a line survives this many frames without a peak
JOIN_GAP_S = 0.15                   # pieces of one pitch closer than this in time are one line
GAP_CENTS = 15.0                    # ... when they agree this closely across a gap (40 cents where they touch)
IF_FRAMES = 5                       # frames the instantaneous frequency is averaged over (25 ms at a 5 ms hop)
MIN_LINE_S = 0.04
GLIDE_CENTS = 50.0                  # a line moving further than this prints both of its ends
LINES_SHOWN = 8
PEAKS_PER_FRAME = 40
MAINS = (50.0, 60.0)
MAINS_TOL = 0.015
MAINS_HARMONICS = 8
MAINS_SHARE = 0.8
MAINS_MIN_S = 0.5                   # and at least this long, so a short low thump is never one
RISE_DB = 12.0
RISE_FROM_MS = (5, 25)
LONE_RISE_DB = 20.0                 # a rise in one band alone counts from this
SILENCE_DB = 80.0                   # a rise from further than this under the loudest is a start from silence
EVENT_RANGE_DB = 40.0
MERGE_MS = 30
LEAD_MS = 5                         # an event this soon after the loudest millisecond still leads into it
EVENTS_SHOWN = 10
ROLLOFF_SHARE = 0.95
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
TINY = 1e-20


def _channels(x):
    x = np.nan_to_num(np.asarray(x, dtype=np.float64))
    if len(x) == 0:
        return np.zeros((0, 1))
    return x.reshape(len(x), -1)


def _sample(ms, rate):
    """The sample at which millisecond `ms` starts (rounded, so 44.1 kHz keeps time over a long file)."""
    return int(round(ms * rate / 1000))


def _per_ms(power, rate):
    """Mean of a power signal over each millisecond (the last one may be short)."""
    n = len(power)
    if n == 0:
        return np.zeros(0)
    blocks = max(1, int(math.ceil(n * 1000 / rate)))
    edges = np.minimum(np.round(np.arange(blocks + 1) * rate / 1000).astype(np.int64), n)
    edges[-1] = n
    counts = np.diff(edges)
    out = np.zeros(blocks)
    filled = counts > 0
    out[filled] = np.add.reduceat(power, edges[:-1][filled]) / counts[filled]
    return out


def band_weights(freqs):
    """The ten bands' weights over these frequencies: raised-cosine changes CROSS_OCT wide at the geometric midpoints between
    the centres, so the weights add up to 1 at every frequency (the lowest band takes everything under it, the highest
    everything over it)."""
    u = np.log2(np.maximum(freqs, 1e-9))
    rises = [np.ones_like(u)]
    half = CROSS_OCT / 2
    for a, b in zip(CENTRES[:-1], CENTRES[1:]):
        d = u - math.log2(math.sqrt(a * b))
        rise = np.clip((d + half) / (2 * half), 0.0, 1.0)
        rises.append(0.5 - 0.5 * np.cos(np.pi * rise))
    rises.append(np.zeros_like(u))
    return np.array([rises[k] * (1.0 - rises[k + 1]) for k in range(len(CENTRES))])


def band_envelopes(x, rate, loop=False):
    """Each band's power per millisecond (bands x ms), all channels together. A one-shot is padded so that the bands'
    ringing does not wrap round onto its start; a loop is split as the circle it is."""
    x = _channels(x)
    n = len(x)
    nfft = n if loop else 1 << int(math.ceil(math.log2(n + rate)))
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    weights = band_weights(freqs)
    out = np.zeros((len(CENTRES), len(_per_ms(np.zeros(n), rate))))
    for c in range(x.shape[1]):
        spec = np.fft.rfft(x[:, c], nfft)
        for b in range(len(CENTRES)):
            y = np.fft.irfft(spec * weights[b], nfft)[:n]
            out[b] += _per_ms(y * y, rate)
    return out


def _pow2(value):
    return 1 << max(8, int(round(math.log2(value))))


def _slices(total_ms, onset_ms, loop):
    """(label, from_ms, to_ms) for the table; one-shots from the onset, loops in twelve equal parts."""
    if loop:
        edges = np.linspace(0, total_ms, LOOP_SLICES + 1)
        cuts = sorted({int(round(e)) for e in edges})
        return [(f'{a / 1000:.2f}-{b / 1000:.2f} s', a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
    out = []
    if onset_ms > BEFORE_MS:
        out.append(('before', 0, onset_ms))
    start = 0
    for edge in EDGES_MS:
        a, b = onset_ms + start, min(total_ms, onset_ms + edge)
        if a >= total_ms:
            break
        if b > a:
            out.append((_span(start, edge if onset_ms + edge <= total_ms else total_ms - onset_ms), a, b))
        start = edge
    if onset_ms + start < total_ms:
        out.append((_span(start, total_ms - onset_ms), onset_ms + start, total_ms))
    return out


def _span(a_ms, b_ms):
    if b_ms < 1000:
        return f'{a_ms}-{b_ms} ms'
    return f'{a_ms / 1000:g}-{b_ms / 1000:g} s'


def note_name(hz):
    """'C6+2' for 1048 Hz (A4 = 440 Hz; cents from the nearest equal-tempered note)."""
    midi = 69 + 12 * math.log2(hz / 440.0)
    near = int(round(midi))
    cents = int(round(100 * (midi - near)))
    sign = '+' if cents >= 0 else '-'
    return f'{NOTE_NAMES[near % 12]}{near // 12 - 1}{sign}{abs(cents)}'


def _cents(a, b):
    return 1200 * math.log2(a / b)


def _peaks(x, rate, n_fft, hop, min_hz):
    """Tonal peaks per frame: (frame, Hz, dB). A peak is a local maximum PEAK_OVER_DB over the average of each side of its
    neighbourhood (NEIGHBOUR_OCT, at least NEIGHBOUR_BINS bins a side). It counts only when the instantaneous frequencies of
    its bin and of both bins beside it agree within a quarter of a bin, as the channels around a sinusoid do and those of
    noise do not (Friedman 1985, ICASSP; Charpentier 1986, ICASSP). The instantaneous frequency comes from the phase of the cross-spectrum between
    the frame and the same frame one sample later, averaged over IF_FRAMES frames, so that noise beside a steady pitch does
    not break it up; it is the frequency given."""
    n = len(x)
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)
    pad = np.concatenate([np.zeros(n_fft // 2), x, np.zeros(n_fft + 1)])
    frames = max(1, 1 + (n - 1) // hop)
    bins = n_fft // 2 + 1
    step = rate / n_fft
    freqs = np.arange(bins) * step
    k = np.arange(bins)
    lo = np.minimum(np.floor(freqs * 2 ** (-NEIGHBOUR_OCT / 2) / step).astype(int), k - NEIGHBOUR_BINS)
    hi = np.maximum(np.ceil(freqs * 2 ** (NEIGHBOUR_OCT / 2) / step).astype(int) + 1, k + NEIGHBOUR_BINS + 1)
    lo, hi = np.clip(lo, 0, bins - 1), np.clip(hi, 1, bins)
    usable = (freqs >= min_hz) & (freqs <= min(20000.0, rate / 2 - step))
    left_to, left_n = np.maximum(k - 1, lo + 1), np.maximum(k - 1 - lo, 1)          # each side of a peak on its own: at a
    right_from, right_n = np.minimum(k + 2, hi - 1), np.maximum(hi - k - 2, 1)     # steep edge one side is silent, the other not
    found = []
    loudest = -math.inf                                         # the loudest bin, tonal or not
    chunk = max(1, (1 << 19) // n_fft)
    side = IF_FRAMES // 2
    for f0 in range(0, frames, chunk):
        idx = np.arange(f0, min(frames, f0 + chunk))
        wide = np.arange(max(0, f0 - side), min(frames, idx[-1] + 1 + side))   # the frames around, for the average
        seg = np.stack([pad[i * hop:i * hop + n_fft + 1] for i in wide])
        now, later = np.fft.rfft(seg[:, :-1] * win, axis=1), np.fft.rfft(seg[:, 1:] * win, axis=1)
        cross = np.cumsum(np.concatenate([np.zeros((1, bins), complex), later * np.conj(now)]), axis=0)
        rows = idx - wide[0]
        top_row = np.minimum(rows + side + 1, len(wide))
        low_row = np.maximum(rows - side, 0)
        inst = np.angle(cross[top_row] - cross[low_row]) * rate / (2 * np.pi)
        db = 20 * np.log10(np.abs(now[rows]) + TINY)
        if usable.any():
            loudest = max(loudest, float(db[:, usable].max()))
        csum = np.concatenate([np.zeros((len(idx), 1)), np.cumsum(db, axis=1)], axis=1)
        around = np.maximum((csum[:, left_to] - csum[:, lo]) / left_n, (csum[:, hi] - csum[:, right_from]) / right_n)
        peak = np.zeros_like(db, dtype=bool)
        peak[:, 1:-1] = ((db[:, 1:-1] > db[:, :-2]) & (db[:, 1:-1] >= db[:, 2:])
                         & (np.abs(inst[:, :-2] - inst[:, 1:-1]) <= 0.25 * step)
                         & (np.abs(inst[:, 2:] - inst[:, 1:-1]) <= 0.25 * step))
        peak &= (db >= around + PEAK_OVER_DB) & usable & (np.abs(inst - freqs) <= step)
        for row, frame in enumerate(idx):
            ks = np.flatnonzero(peak[row])
            if len(ks) > PEAKS_PER_FRAME:
                ks = ks[np.argsort(db[row, ks])[-PEAKS_PER_FRAME:]]
            for j in ks:
                a, b, c = db[row, j - 1], db[row, j], db[row, j + 1]
                den = a - 2 * b + c
                p = 0.5 * (a - c) / den if den != 0 else 0.0
                found.append((int(frame), float(inst[row, j]), float(b - 0.25 * (a - c) * p)))
    return found, frames, loudest


def _tracks(found, frames, hop, rate, floor):
    """Peaks joined from frame to frame within JOIN_CENTS (McAulay and Quatieri 1986), a gap of one frame allowed; lines
    shorter than MIN_LINE_S dropped. Levels are absolute here."""
    by_frame = {}
    for frame, hz, level in sorted(found, key=lambda q: -q[2]):
        if level >= floor:
            here = by_frame.setdefault(frame, [])
            if all(abs(_cents(hz, h)) > JOIN_CENTS for h, _ in here):
                here.append((hz, level))
    tracks, active = [], []
    for frame in range(frames):
        taken = set()
        for hz, level in sorted(by_frame.get(frame, []), key=lambda q: -q[1]):
            best = None
            for track in active:
                if id(track) in taken:
                    continue
                d = abs(_cents(hz, track['last']))
                if d <= JOIN_CENTS and (best is None or d < best[0]):
                    best = (d, track)
            if best is None:
                track = {'points': [], 'miss': 0, 'last': hz}
                active.append(track)
                tracks.append(track)
            else:
                track = best[1]
            track['points'].append((frame, hz, level))
            track['last'] = hz
            track['miss'] = 0
            taken.add(id(track))
        for track in list(active):
            if id(track) not in taken:
                track['miss'] += 1
                if track['miss'] > GAP_FRAMES:
                    active.remove(track)
    pieces = sorted((track['points'] for track in tracks), key=lambda pts: pts[0][0])
    joined = []
    for pts in pieces:                                          # pieces of one pitch, end to start, become one line
        for prev in joined:
            gap = (pts[0][0] - prev[-1][0]) * hop / rate
            near = JOIN_CENTS if gap <= 2 * hop / rate else GAP_CENTS     # across a gap only the same pitch, not a neighbour
            if 0 < gap <= JOIN_GAP_S and abs(_cents(pts[0][1], prev[-1][1])) <= near:
                prev.extend(pts)
                break
        else:
            joined.append(list(pts))
    lines = []
    for pts in joined:
        start, end = pts[0][0] * hop / rate, (pts[-1][0] + 1) * hop / rate
        if end - start < MIN_LINE_S:
            continue
        loud = max(pts, key=lambda q: q[2])
        hzs = [q[1] for q in pts]
        lines.append({'hz': loud[1], 'level': loud[2], 'start_s': start, 'end_s': end, 'hzs': hzs, 'points': pts,
                      'median_hz': float(np.median(hzs)), 'from_hz': hzs[0], 'to_hz': hzs[-1]})
    return lines


def tonal_lines(x, rate):
    """The tracked tonal lines, loudest first, each a dict (times in seconds from the file's start; levels in dB under the
    loudest spectral peak, tonal or not: the long window's loudest bin or the loudest pitch, a short-window peak scaled as
    a steady pitch's would read in the long window). Two windows: a long one (N_FFT) resolves low and close pitches, a short one (SHORT_FFT, from
    SHORT_FROM_HZ up) follows glides too quick for the long one; a short-window line that a long-window line already holds
    is dropped."""
    x = _channels(x)
    if len(x) == 0 or not np.any(x):
        return []
    hop = max(1, int(round(rate * HOP_S)))
    long_fft, short_fft = _pow2(N_FFT * rate / 48000), _pow2(SHORT_FFT * rate / 48000)   # the same durations at any rate
    gain = 20 * math.log10(long_fft / short_fft)               # a sinusoid's peak in the short window is this much lower
    long_found, short_found = [], []
    long_frames = short_frames = 1
    loudest = -math.inf
    for c in range(x.shape[1]):                                 # each channel on its own, so opposite polarity cannot cancel
        if not np.any(x[:, c]):
            continue
        found, long_frames, peak = _peaks(x[:, c], rate, long_fft, hop, 20.0)
        long_found += found
        loudest = max(loudest, peak)
        found, short_frames, _ = _peaks(x[:, c], rate, short_fft, hop, SHORT_FROM_HZ)
        short_found += [(f, hz, level + gain) for f, hz, level in found]
    if not long_found and not short_found:
        return []
    top = max(level for _, _, level in long_found + short_found)
    floor = top - LINE_RANGE_DB                                 # lines are tracked down to this under the loudest one,
    top = max(top, loudest)                                     # and their levels given under the loudest peak, tonal or not
    pieces = _tracks(long_found, long_frames, hop, rate, floor) + _tracks(short_found, short_frames, hop, rate, floor)
    out = []
    for points in _join([p['points'] for p in pieces], hop, rate):     # one pitch seen by both windows is one line
        start, end = points[0][0] * hop / rate, (points[-1][0] + 1) * hop / rate
        if end - start < MIN_LINE_S:
            continue
        loud = max(points, key=lambda q: q[2])
        hzs = [q[1] for q in points]
        out.append({'hz': round(loud[1], 1), 'note': note_name(loud[1]), 'start_s': round(start, 3), 'end_s': round(end, 3),
                    'db': round(min(0.0, loud[2] - top), 1) + 0.0, 'median_hz': round(float(np.median(hzs)), 1),
                    'from_hz': round(hzs[0], 1), 'to_hz': round(hzs[-1], 1), 'low_hz': round(min(hzs), 1),
                    'high_hz': round(max(hzs), 1), 'hzs': hzs,
                    'covered_s': round(len(points) * hop / rate, 3)})
    out.sort(key=lambda line: -line['db'])
    return out


def _union(a, b):
    """Points of both, one per frame, the louder where both have one."""
    best = {}
    for point in a + b:
        if point[0] not in best or point[2] > best[point[0]][2]:
            best[point[0]] = point
    return [best[f] for f in sorted(best)]


def _join(lines, hop, rate):
    """Lines of one pitch that overlap or meet end to start (within JOIN_GAP_S, at frequencies within JOIN_CENTS where they
    touch, GAP_CENTS across a gap) become one: what both windows see of a pitch, and a glide that one window loses and the
    other picks up, is one line."""
    gap = max(1, int(round(JOIN_GAP_S * rate / hop)))
    out = []
    for points in sorted((sorted(p) for p in lines), key=lambda p: p[0][0]):
        for prev in out:
            if points[0][0] - prev[-1][0] > gap or points[0][0] < prev[0][0]:
                continue
            here = {f: hz for f, hz, _ in prev}
            meet = here.get(points[0][0]) or prev[-1][1] if points[0][0] > prev[-1][0] else here.get(points[0][0])
            if meet is None:
                nearest = min(here, key=lambda f: abs(f - points[0][0]))
                meet = here[nearest]
            near = JOIN_CENTS if points[0][0] - prev[-1][0] <= 2 else GAP_CENTS
            if abs(_cents(points[0][1], meet)) <= near:
                prev[:] = _union(prev, points)
                break
        else:
            out.append(list(points))
    return out


def mains_line(line, active_s):
    """True for a line at a mains frequency: at least MAINS_MIN_S long and MAINS_SHARE of the active part, with at least
    MAINS_SHARE of its points within MAINS_TOL of one and the same multiple of 50 or 60 Hz (so a glide through 60 Hz is none)."""
    held = line.get('covered_s', line['end_s'] - line['start_s'])         # the frames that hold the pitch, not gaps between
    if active_s <= 0 or held < MAINS_MIN_S or held < MAINS_SHARE * active_s:
        return False
    hzs = line.get('hzs') or [line['median_hz']]
    for base in MAINS:
        for n in range(1, MAINS_HARMONICS + 1):
            target = n * base
            if sum(abs(h - target) <= MAINS_TOL * target for h in hzs) >= MAINS_SHARE * len(hzs):
                return True
    return False


def events(env, peak_ms, loop=False, peak_at_ms=None):
    """The events (dicts: time in seconds from the file's start, the largest rise in dB, the bands that rose) and how many
    were found. The main one is the event nearest peak_at_ms when the project declares it, else the last to start before
    the loudest millisecond (or within LEAD_MS after it) with no silence (MERGE_MS with no band within EVENT_RANGE_DB of the
    loudest) between them. A loop counts both round its join, where rises on both sides within MERGE_MS are one event."""
    if env.size == 0 or not np.any(env > 0):
        return [], 0
    level = 10 * np.log10(env + TINY)
    floor = level.max() - EVENT_RANGE_DB
    near, far = RISE_FROM_MS
    width = far - near + 1
    if loop:                                                    # a loop's start follows its own end, wrapped as often as needed
        lead = np.tile(level, (1, -(-far // level.shape[1])))[:, -far:]
    else:
        lead = np.full((level.shape[0], far), -np.inf)
    padded = np.concatenate([lead, level], axis=1)                    # a loop's start follows its own end
    windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=1)    # bands x (T + near) x width
    before = windows[:, :level.shape[1], :].max(axis=2)                          # ms t looks at t-25 ... t-5
    silent_before = before < level.max() - SILENCE_DB
    rise = np.where(silent_before, np.inf, level - before)
    cand = (rise >= RISE_DB) & (level >= floor)
    lone = cand.sum(axis=0) == 1                                # one band alone must rise further: noise swells in one band
    cand &= ~(lone[None, :] & (rise < LONE_RISE_DB) & ~silent_before)
    times = np.flatnonzero(cand.any(axis=0))
    groups = []
    for t in times:
        if groups and t - groups[-1]['start'] <= MERGE_MS:
            g = groups[-1]
        else:
            g = {'start': int(t), 'rise': 0.0, 'bands': set()}
            groups.append(g)
        rows = np.flatnonzero(cand[:, t])
        g['rise'] = max(g['rise'], float(rise[rows, t].max()))
        g['bands'].update(LABELS[r] for r in rows)
    if not groups:
        return [], 0
    total = level.shape[1]
    if loop and len(groups) > 1 and groups[0]['start'] + total - groups[-1]['start'] <= MERGE_MS:
        first = groups.pop(0)                                   # rises on both sides of a loop's join are one event, from
        groups[-1]['rise'] = max(groups[-1]['rise'], first['rise'])     # where it starts before the join
        groups[-1]['bands'] |= first['bands']
    if peak_at_ms is not None:                                   # the moment the project says the sound peaks

        def off(g):                                             # round a loop's join, as the peak_at check measures it
            d = abs(g['start'] - peak_at_ms)
            return min(d % total, total - d % total) if loop else d

        main = min(range(len(groups)), key=lambda i: off(groups[i]))
    else:                                                       # else the event that leads into the loudest millisecond: its
        quiet = ~(level >= floor).any(axis=0)                   # attack reaches back to the last silence, MERGE_MS in which no
        limit = total - 1 if loop else peak_ms                  # band comes within EVENT_RANGE_DB of the loudest (round a loop)
        back = quiet[np.arange(peak_ms - 1, peak_ms - 1 - limit, -1) % total]      # nearest first
        held = np.concatenate([[0], np.cumsum(back)])
        silences = np.flatnonzero(held[MERGE_MS:] - held[:-MERGE_MS] == MERGE_MS)  # a dip of a few ms is no silence
        reach = int(silences[0]) if silences.size else limit

        def ahead(g):                                           # how long before the loudest millisecond an event starts
            d = peak_ms - g['start']
            return (d + LEAD_MS) % total - LEAD_MS if loop else d

        leading = [i for i, g in enumerate(groups) if -LEAD_MS <= ahead(g) <= reach]
        main = min(leading, key=lambda i: ahead(groups[i])) if leading else None    # none: a swell, or silence between
    keep = sorted(range(len(groups)), key=lambda i: (main is None or i != main, -groups[i]['rise']))[:EVENTS_SHOWN]
    out = []
    for i in sorted(keep, key=lambda i: groups[i]['start']):
        g = groups[i]
        out.append({'time_s': round(g['start'] / 1000, 3), 'rise_db': round(g['rise'], 1) if math.isfinite(g['rise']) else None,
                    'bands': [b for b in LABELS if b in g['bands']], 'main': i == main})
    return out, len(groups)


def rolloff_hz(x, rate, share=ROLLOFF_SHARE):
    """The frequency under which `share` of the energy between 20 Hz and 20 kHz lies."""
    x = _channels(x)
    if len(x) == 0 or not np.any(x):
        return None
    nfft = max(8192, 1 << int(math.ceil(math.log2(2 * len(x)))))    # padded, so a short click has a spectrum to read
    power = np.zeros(nfft // 2 + 1)
    for c in range(x.shape[1]):
        power += np.abs(np.fft.rfft(x[:, c], nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    keep = (freqs >= 20.0) & (freqs <= 20000.0)
    if not np.any(keep) or power[keep].sum() <= 0:
        return None
    cum = np.cumsum(power[keep])
    return round(float(freqs[keep][np.searchsorted(cum, share * cum[-1])]), 1)


def readout(x, rate, loop=False, peak_at=None):
    """The readout of one sound as a dict (see the module's docstring)."""
    x = _channels(x)
    raw = _per_ms((x * x).sum(axis=1), rate) if len(x) else np.zeros(0)
    out = {'seconds': round(len(x) / rate, 3), 'loop': bool(loop)}
    if raw.size == 0 or not np.any(raw > 0):
        out.update(silent=True, onset_s=None, peak_s=None, rolloff_hz=None, bands=None, lines=[], events=[])
        return out
    peak_ms = int(np.argmax(raw))
    loud = raw >= raw[peak_ms] * 10 ** (-ONSET_DB / 10)
    onset_ms = 0 if loop else int(np.argmax(loud))
    last_ms = len(raw) - 1 if loop else int(len(raw) - 1 - np.argmax(loud[::-1]))
    env = band_envelopes(x, rate, loop)
    env = np.minimum(env, 2 * raw[None, :])                     # no band holds more than the sound in that millisecond: the
    top = env.max()                                              # split rings before a sudden sound, and that is cut off here
    rows = []
    above = [c / math.sqrt(2) >= rate / 2 for c in CENTRES]    # a band wholly above what the file can hold
    for label, a, b in _slices(len(raw), onset_ms, loop):
        cells = env[:, a:b].max(axis=1)
        db = 10 * np.log10(np.maximum(cells, TINY) / top)
        rows.append({'label': label, 'from_ms': int(a), 'to_ms': int(b),
                     'db': ['n/a' if gone else (int(round(v)) if v >= -FLOOR_DB else None) for v, gone in zip(db, above)]})
    active_s = (last_ms + 1 - onset_ms) / 1000
    lines = tonal_lines(x, rate)
    for line in lines:
        line['mains'] = mains_line(line, active_s)
    found = events(env, peak_ms, loop, None if peak_at is None else int(round(peak_at * 1000)))
    shown = lines[:LINES_SHOWN]
    hum = [line for line in lines[LINES_SHOWN:] if line['mains']]
    if hum and not any(line['mains'] for line in shown):         # a hum is never hidden behind louder partials
        shown = shown[:LINES_SHOWN - 1] + hum[:1]
    for line in lines:
        line.pop('hzs', None)
        line.pop('covered_s', None)
    segment = x[_sample(onset_ms, rate):_sample(last_ms + 1, rate)]
    out.update(silent=False, onset_s=round(onset_ms / 1000, 3), peak_s=round(peak_ms / 1000, 3),
               active_s=round(active_s, 3), rolloff_hz=rolloff_hz(segment, rate),
               bands={'centres_hz': list(CENTRES), 'labels': list(LABELS), 'slices': rows},
               lines=shown, lines_found=len(lines),
               events=found[0], events_found=found[1],
               main_by='peak_at' if peak_at is not None else 'loudest')
    return out


def _khz(hz):
    return f'{hz / 1000:.1f} kHz' if hz >= 1000 else f'{hz:.0f} Hz'


def text(r, name):
    """The readout as the lines sfx.py prints."""
    if r.get('silent'):
        return [f'{name}: {r["seconds"]} s of silence']
    head = f'{name}: {r["seconds"]} s'
    if not r['loop']:
        head += f', onset at {r["onset_s"]} s, loudest at {r["peak_s"]} s'
    else:
        head += ', a loop'
    if r['rolloff_hz']:
        head += f', 95 % of its energy between 20 Hz and 20 kHz under {_khz(r["rolloff_hz"])}'
    out = [head, '  bands (dB under the loudest cell; . is more than 60 dB under)',
           '  ' + ('time from file start' if r['loop'] else 'after the onset').ljust(16) +
           ''.join(f'{lab:>5}' for lab in r['bands']['labels'])]
    for row in r['bands']['slices']:
        out.append('  ' + row['label'].ljust(16) + ''.join(f'{"." if v is None else v:>5}' for v in row['db']))
    if any(v == 'n/a' for v in r['bands']['slices'][0]['db']):
        out.append('  (n/a: the band lies above what this file\'s sample rate can hold)')
    if r['lines']:
        extra = f' ({r["lines_found"]} found, the {len(r["lines"])} loudest)' if r['lines_found'] > len(r['lines']) else ''
        out.append(f'  tonal lines (dB under the loudest spectral peak; ends to about 40 ms){extra}')
        for line in r['lines']:
            s = f'    {line["hz"]:g} Hz {line["note"]:<6} {line["start_s"]:.3f}-{line["end_s"]:.3f} s  {line["db"]:g} dB'
            if abs(_cents(line['to_hz'], line['from_hz'])) > GLIDE_CENTS:
                s += f'  moves {line["from_hz"]:g} -> {line["to_hz"]:g} Hz'
            if abs(_cents(line['high_hz'], line['low_hz'])) > GLIDE_CENTS:
                s += f'  ranges {line["low_hz"]:g}-{line["high_hz"]:g} Hz'
            if line.get('mains'):
                s += '  at a mains frequency: hum in a recording, or a drone that was meant'
            out.append(s)
    else:
        out.append('  tonal lines: none (noise, clicks or a sound without steady pitches)')
    if r['events']:
        how = 'nearest the declared peak_at' if r.get('main_by') == 'peak_at' else 'leading into the loudest millisecond'
        if not any(e['main'] for e in r['events']):
            how += ', and none does: the sound swells into it'
        more = f'; {r["events_found"]} found, the {len(r["events"])} largest shown' if r.get('events_found', 0) > len(r['events']) else ''
        out.append(f'  events (time from file start, rise over the 5-25 ms before, bands that rose; main: the one {how}{more})')
        for e in r['events']:
            mark = 'main ' if e['main'] else '     '
            rise = 'from silence' if e['rise_db'] is None else f'+{e["rise_db"]:g} dB'
            out.append(f'    {e["time_s"]:.3f} s  {mark} {rise} in {" ".join(e["bands"])}')
    else:
        out.append('  events: none')
    return out
