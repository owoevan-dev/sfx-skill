"""A sound as numbers an agent can read: octave bands over time, tonal lines, events and the roll-off.

The agent cannot hear, and it reads exact values from a table far better than from a picture (Liu et al. 2023, "DePlot:
one-shot visual language reasoning by plot-to-table translation", Findings of ACL; Rahmanzadehgervi et al. 2024, "Vision
language models are blind", ACCV). The spectrogram stays as a second look for what no line here names.

- Bands over time: ten octave bands at the nominal centres of IEC 61260-1 (31.5 Hz to 16 kHz), made by this skill's own
  split (one FFT of the whole sound, each band a raised-cosine window on log frequency between the geometric midpoints of
  its neighbours, so the ten add up to the sound; not an IEC class filter). A cell is the band's power over a slice of
  time (its energy in the slice over the slice's length, one measurement per slice), in dB under the loudest cell of the
  whole table; a brief sound inside a long slice is averaged down there, and can still be found as an event when it meets
  the events' thresholds (at most ten events print, with the number found). A one-shot's slices start at its onset (the
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
AGREE_SHARE = 0.8                   # lines that overlap are one where this share of their common frames agree
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
THIRD_CENTRES = (20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0, 400.0, 500.0, 630.0,
                 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0, 4000.0, 5000.0, 6300.0, 8000.0, 10000.0, 12500.0,
                 16000.0, 20000.0)          # IEC 61260-1's nominal third-octave centres
THIRD_LABELS = ('20', '25', '31', '40', '50', '63', '80', '100', '125', '160', '200', '250', '315', '400', '500', '630', '800',
                '1k', '1.25k', '1.6k', '2k', '2.5k', '3.15k', '4k', '5k', '6.3k', '8k', '10k', '12.5k', '16k', '20k')
ZOOM_SLICES_MS = (1, 2, 5, 10, 20, 50, 100)   # a zoom's slice: the finest of these that gives at most ZOOM_ROWS rows
ZOOM_ROWS = 40
PAGE_LINES = 25                     # a page of a view holds at most this many lines ...
PAGE_CHARS = 2500                   # ... and characters, and says how to see the rest


def _channels(x):
    x = np.nan_to_num(np.asarray(x, dtype=np.float64))
    if len(x) == 0:
        return np.zeros((0, 1))
    return x.reshape(len(x), -1)


def _sample(ms, rate):
    """The sample at which millisecond `ms` starts (rounded, so 44.1 kHz keeps time over a long file)."""
    return int(round(ms * rate / 1000))


def _ms_edges(n, rate):
    """The first sample of each millisecond of n samples, and n after the last (the last millisecond may be short)."""
    blocks = max(1, int(math.ceil(n * 1000 / rate)))
    edges = np.minimum(np.round(np.arange(blocks + 1) * rate / 1000).astype(np.int64), n)
    edges[-1] = n
    return edges


def _per_ms(power, rate):
    """Mean of a power signal over each millisecond (the last one may be short)."""
    n = len(power)
    if n == 0:
        return np.zeros(0)
    edges = _ms_edges(n, rate)
    counts = np.diff(edges)
    out = np.zeros(len(counts))
    filled = counts > 0
    out[filled] = np.add.reduceat(power, edges[:-1][filled]) / counts[filled]
    return out


def band_weights(freqs, centres=CENTRES, cross=CROSS_OCT):
    """The bands' weights over these frequencies (the ten octave bands unless other centres are given): raised-cosine
    changes `cross` octaves wide at the geometric midpoints between the centres, so the weights add up to 1 at every
    frequency (the lowest band takes everything under it, the highest everything over it)."""
    u = np.log2(np.maximum(freqs, 1e-9))
    rises = [np.ones_like(u)]
    half = cross / 2
    for a, b in zip(centres[:-1], centres[1:]):
        d = u - math.log2(math.sqrt(a * b))
        rise = np.clip((d + half) / (2 * half), 0.0, 1.0)
        rises.append(0.5 - 0.5 * np.cos(np.pi * rise))
    rises.append(np.zeros_like(u))
    return np.array([rises[k] * (1.0 - rises[k + 1]) for k in range(len(centres))])


def band_envelopes(x, rate, loop=False, per_channel=False, centres=CENTRES, cross=CROSS_OCT):
    """Each band's power per millisecond (bands x ms), all channels together, or each channel's (channels x bands x ms)
    with per_channel. A one-shot is padded so that the bands' ringing does not wrap round onto its start; a loop is split
    as the circle it is."""
    x = _channels(x)
    n = len(x)
    nfft = n if loop else 1 << int(math.ceil(math.log2(n + rate)))
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    weights = band_weights(freqs, centres, cross)
    each = np.zeros((x.shape[1], len(centres), len(_per_ms(np.zeros(n), rate))))
    for c in range(x.shape[1]):
        spec = np.fft.rfft(x[:, c], nfft)
        for b in range(len(centres)):
            y = np.fft.irfft(spec * weights[b], nfft)[:n]
            each[c, b] = _per_ms(y * y, rate)
    return each if per_channel else _summed(each)


def _summed(each):
    """The channels' band powers added in channel order (the order the readout has always used)."""
    out = np.zeros(each.shape[1:])
    for c in range(each.shape[0]):
        out += each[c]
    return out


def _pow2(value):
    return 1 << max(8, int(round(math.log2(value))))


def _slices(total_ms, onset_ms, loop):
    """(label, from_ms, to_ms) for the table; one-shots from the onset, loops in twelve equal parts."""
    if loop:
        edges = np.linspace(0, total_ms, LOOP_SLICES + 1)
        cuts = sorted({int(round(e)) for e in edges})
        short = total_ms < 1000                                  # a short loop counts in milliseconds, as a one-shot's attack
        return [(f'{a}-{b} ms' if short else f'{a / 1000:.2f}-{b / 1000:.2f} s', a, b)
                for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
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
    other picks up, is one line. Lines that overlap must agree within JOIN_CENTS over AGREE_SHARE of the frames they share,
    so a pitch held in one channel and a glide leaving it in the other stay two."""
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
            shared = [abs(_cents(hz, here[f])) <= JOIN_CENTS for f, hz, _ in points if f in here]
            if abs(_cents(points[0][1], meet)) <= near and (not shared or sum(shared) >= AGREE_SHARE * len(shared)):
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


def events(env, peak_ms, loop=False, peak_at_ms=None, cap=EVENTS_SHOWN):
    """The events (dicts: time in seconds from the file's start, the largest rise in dB, the bands that rose) and how many
    were found: at most `cap`, the main one and then the largest rises, in time order (every one with cap None). The main one is the event nearest peak_at_ms when the project declares it, else the last to start before
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
    keep = sorted(range(len(groups)), key=lambda i: (main is None or i != main, -groups[i]['rise']))
    keep = keep[:cap] if cap is not None else keep
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


class Analysis:
    """One sound read once: what the overview and every view take from it. It holds audio and numbers only, never a path,
    a render folder or a project, so that another skill can use it as it is. Times are milliseconds from the file's start.

    x, rate, loop, peak_at: the input (x as frames x channels). raw: the whole sound's power per millisecond; silent: no
    power at all. For a sound that is not silent: peak_ms, onset_ms (the first millisecond within ONSET_DB of the loudest;
    0 for a loop), last_ms, active_s; channel_env, each channel's band powers per millisecond (channels x bands x ms); env,
    their sum, no band above twice the sound's power in its millisecond (the split rings before a sudden sound, and that is
    cut off); counts, the samples in each millisecond; lines, every tonal line, loudest first, with its frame-by-frame
    frequencies (`hzs`), the time its frames hold (`covered_s`) and its mains label; rolloff_hz. `events_all` lists every
    event, the main one marked."""

    def __init__(self, x, rate, loop=False, peak_at=None):
        self.x, self.rate, self.loop, self.peak_at = _channels(x), rate, bool(loop), peak_at
        x = self.x
        self.raw = _per_ms((x * x).sum(axis=1), rate) if len(x) else np.zeros(0)
        self.silent = self.raw.size == 0 or not np.any(self.raw > 0)
        if self.silent:
            return
        raw = self.raw
        self.peak_ms = int(np.argmax(raw))
        loud = raw >= raw[self.peak_ms] * 10 ** (-ONSET_DB / 10)
        self.onset_ms = 0 if loop else int(np.argmax(loud))
        self.last_ms = len(raw) - 1 if loop else int(len(raw) - 1 - np.argmax(loud[::-1]))
        self.active_s = (self.last_ms + 1 - self.onset_ms) / 1000
        self.channel_env = band_envelopes(x, rate, loop, per_channel=True)
        self.env = np.minimum(_summed(self.channel_env), 2 * raw[None, :])
        self.counts = np.diff(_ms_edges(len(x), rate)).astype(float)
        self.lines = tonal_lines(x, rate)
        for line in self.lines:
            line['mains'] = mains_line(line, self.active_s)
        self.rolloff_hz = rolloff_hz(x[_sample(self.onset_ms, rate):_sample(self.last_ms + 1, rate)], rate)
        self._events_all = None
        self._third = None

    @property
    def peak_at_ms(self):
        return None if self.peak_at is None else int(round(self.peak_at * 1000))

    @property
    def events_all(self):
        if self._events_all is None:
            self._events_all = events(self.env, self.peak_ms, self.loop, self.peak_at_ms, cap=None)[0]
        return self._events_all

    def band_set(self, kind='octave'):
        """(labels, centres, each channel's band powers per millisecond) of the octave or the third-octave bands."""
        if kind == 'octave':
            return LABELS, CENTRES, self.channel_env
        if kind != 'third':
            raise ValueError(f"bands are 'octave' or 'third', not {kind!r}")
        if self._third is None:
            self._third = band_envelopes(self.x, self.rate, self.loop, per_channel=True, centres=THIRD_CENTRES,
                                         cross=CROSS_OCT / 3)
        return THIRD_LABELS, THIRD_CENTRES, self._third

    def channel_power(self, c):
        """One channel's power per millisecond."""
        return _per_ms(self.x[:, c] ** 2, self.rate)


def analyse(x, rate, loop=False, peak_at=None):
    """The one reading of a sound that the overview and the views share (see Analysis)."""
    return Analysis(x, rate, loop, peak_at)


def view_overview(a):
    """The overview of an Analysis as a dict: bands over time, up to LINES_SHOWN lines, up to EVENTS_SHOWN events, the
    roll-off (see the module's docstring)."""
    out = {'seconds': round(len(a.x) / a.rate, 3), 'loop': a.loop}
    if a.silent:
        out.update(silent=True, onset_s=None, peak_s=None, rolloff_hz=None, bands=None, lines=[], events=[])
        return out
    energy = a.env * a.counts[None, :]                           # each band's energy in each millisecond
    slices = _slices(len(a.raw), a.onset_ms, a.loop)
    power = [energy[:, lo:hi].sum(axis=1) / max(float(a.counts[lo:hi].sum()), 1.0) for _, lo, hi in slices]
    above = np.array([c / math.sqrt(2) >= a.rate / 2 for c in CENTRES])   # a band wholly above what the file can hold
    top = max(float(p[~above].max()) for p in power)             # the loudest cell that prints: a band's power over a slice
    least = 10 ** (-FLOOR_DB / 10)
    rows = []
    for (label, lo, hi), cells in zip(slices, power):
        ratio = cells / top if top > 0 else np.zeros_like(cells)  # a cell with no energy stays under the floor
        rows.append({'label': label, 'from_ms': int(lo), 'to_ms': int(hi),
                     'db': ['n/a' if gone else (int(round(10 * math.log10(q))) if q >= least else None)
                            for q, gone in zip(ratio, above)]})
    shown = a.lines[:LINES_SHOWN]
    hum = [line for line in a.lines[LINES_SHOWN:] if line['mains']]
    if hum and not any(line['mains'] for line in shown):         # a hum is never hidden behind louder partials
        shown = shown[:LINES_SHOWN - 1] + hum[:1]
    shown = [{k: v for k, v in line.items() if k not in ('hzs', 'covered_s')} for line in shown]
    found = events(a.env, a.peak_ms, a.loop, a.peak_at_ms)
    out.update(silent=False, onset_s=round(a.onset_ms / 1000, 3), peak_s=round(a.peak_ms / 1000, 3),
               active_s=round(a.active_s, 3), rolloff_hz=a.rolloff_hz,
               bands={'centres_hz': list(CENTRES), 'labels': list(LABELS), 'slices': rows},
               lines=shown, lines_found=len(a.lines),
               events=found[0], events_found=found[1],
               main_by='peak_at' if a.peak_at is not None else 'loudest')
    return out


def readout(x, rate, loop=False, peak_at=None):
    """The readout of one sound as a dict: the overview of its Analysis (see the module's docstring)."""
    return view_overview(analyse(x, rate, loop, peak_at))


def _resolve_ms(centre, kind):
    """About two periods of a band's width: a slice finer than this cannot separate what happens inside that band."""
    width = centre * ((2 ** 0.5 - 2 ** -0.5) if kind == 'octave' else (2 ** (1 / 6) - 2 ** (-1 / 6)))
    return 2000.0 / width


def view_zoom(a, start_s, end_s, bands='octave', hz=None, channel=None, slice_ms=None):
    """Layer 1: the window from start_s to end_s (seconds from the file's start; a loop's window may run past its end, round
    its join) in equal slices (the finest of ZOOM_SLICES_MS that gives at most ZOOM_ROWS rows, or slice_ms), in the octave or
    third-octave bands (those whose centre lies in hz, a (low, high) pair, when given), of both channels or of one ('left',
    'right'). A cell is the band's power over its slice, in dB under the loudest cell this slicing gives anywhere in the
    sound. A band too narrow to separate events as fine as the slices spans several rows: its value stands in the first and
    '^' in the rest. Every line and every event in the window is listed."""
    if a.silent:
        return {'zoom': True, 'silent': True, 'seconds': round(len(a.x) / a.rate, 3)}
    labels, centres, each = a.band_set(bands)
    if channel is None:
        env, raw = _summed(each), a.raw
    else:
        if channel not in ('left', 'right'):
            raise ValueError(f"a channel is 'left' or 'right', not {channel!r}")
        if each.shape[0] < 2:
            raise ValueError('the sound has one channel')
        c = 0 if channel == 'left' else 1
        env, raw = each[c], a.channel_power(c)
    env = np.minimum(env, 2 * raw[None, :])                     # as in the overview: the split's ringing cut off
    total = env.shape[1]
    lo, hi = int(round(start_s * 1000)), int(round(end_s * 1000))
    if not a.loop:
        lo, hi = max(lo, 0), min(hi, total)
    elif lo < 0 or lo >= total or hi - lo > total:
        raise ValueError(f'a loop window starts inside its {total / 1000:g} s and lasts at most that long')
    if hi <= lo:
        raise ValueError('the window is empty: --to must come after --from, inside the sound')
    width = int(slice_ms) if slice_ms else next((w for w in ZOOM_SLICES_MS if -(-(hi - lo) // w) <= ZOOM_ROWS),
                                                ZOOM_SLICES_MS[-1])
    if width < 1:
        raise ValueError('a slice is at least 1 ms')
    keep = [i for i, f in enumerate(centres) if hz is None or hz[0] <= f <= hz[1]]
    if not keep:
        raise ValueError('no band has its centre in that range')
    reps = 2 if a.loop else 1                                     # a loop's window may run on round its join
    cum_e = np.concatenate([np.zeros((env.shape[0], 1)), np.cumsum(np.tile(env * a.counts[None, :], (1, reps)), axis=1)],
                           axis=1)
    cum_n = np.concatenate([[0.0], np.cumsum(np.tile(a.counts, reps))])

    def power(s, e):                                              # each band's power from millisecond s to e
        return (cum_e[:, e] - cum_e[:, s]) / max(float(cum_n[e] - cum_n[s]), 1.0)

    above = np.array([f * 2 ** (-0.5 if bands == 'octave' else -1 / 6) >= a.rate / 2 for f in centres])
    edges = list(range(lo, hi, width)) + [hi]
    cells = [power(s, e) for s, e in zip(edges[:-1], edges[1:])]
    grid = [power(s, min(s + width, total)) for s in range(0, total, width)]
    top = max(float(np.max(p[~above])) for p in grid + cells)     # the loudest cell this slicing gives anywhere
    least = 10 ** (-FLOOR_DB / 10)
    spans = [max(1, math.ceil(_resolve_ms(f, bands) / width)) for f in centres]
    rows, heads = [], {}
    for r, (s, e) in enumerate(zip(edges[:-1], edges[1:])):
        db = []
        for i in keep:
            if above[i]:
                db.append('n/a')
            elif r % spans[i]:                                   # inside a cell the band cannot read finer
                db.append('^' if isinstance(heads[i], int) else heads[i])
            else:
                q = power(s, edges[min(r + spans[i], len(edges) - 1)])[i] / top if top > 0 else 0.0
                heads[i] = int(round(10 * math.log10(q))) if q >= least else None
                db.append(heads[i])
        rows.append({'label': f'{s / 1000:.3f}-{e / 1000:.3f} s', 'from_ms': s, 'to_ms': e, 'db': db})
    start, end, length = lo / 1000, hi / 1000, total / 1000

    def inside(t0, t1):                                           # overlaps the window, round the join for a loop
        return (t0 < end and t1 > start) or (a.loop and t0 + length < end and t1 + length > start)

    lines = [{k: v for k, v in line.items() if k not in ('hzs', 'covered_s')} for line in a.lines
             if inside(line['start_s'], line['end_s'])]
    found = [e for e in a.events_all if inside(e['time_s'], e['time_s'] + 1e-9)]
    coarse = {labels[i]: spans[i] * width for i in keep if spans[i] > 1 and not above[i]}
    return {'zoom': True, 'silent': False, 'seconds': round(len(a.x) / a.rate, 3), 'loop': a.loop, 'from_s': start,
            'to_s': end, 'slice_ms': width, 'kind': bands, 'channel': channel or 'both',
            'bands': {'labels': [labels[i] for i in keep], 'centres_hz': [centres[i] for i in keep], 'slices': rows},
            'coarse_ms': coarse, 'lines': lines, 'events': found}


def text_zoom(z, name):
    """The zoom as pages of lines, each within PAGE_LINES and PAGE_CHARS; every page but the last ends with how to see the
    next."""
    if z.get('silent'):
        return [[f'{name}: {z["seconds"]} s of silence']]
    labels = z['bands']['labels']
    col = max(5, max(len(label) for label in labels) + 1)
    where = 'both channels' if z['channel'] == 'both' else f'the {z["channel"]} channel'
    head = [f'{name}: {z["from_s"]:g}-{z["to_s"]:g} s in slices of {z["slice_ms"]} ms, {z["kind"]} bands, {where}'
            + (", past the loop's end round its join" if z['loop'] and z['to_s'] > z['seconds'] else '')]
    intro = [f'  events in the window: {len(z["events"])}' + ('' if z['events'] else ' (none)')]
    for e in z['events']:
        rise = 'from silence' if e['rise_db'] is None else f'+{e["rise_db"]:g} dB'
        intro.append(f'    {e["time_s"]:.3f} s  {"main " if e["main"] else "     "} {rise} in {" ".join(e["bands"])}')
    intro.append(f'  tonal lines sounding in the window: {len(z["lines"])}' + ('' if z['lines'] else ' (none)'))
    intro += [f'    {line["hz"]:g} Hz {line["note"]:<6} {line["start_s"]:.3f}-{line["end_s"]:.3f} s  {line["db"]:g} dB'
              for line in z['lines']]
    if z['coarse_ms']:
        intro.append('  read coarser, as the band cannot separate finer: ' +
                     ', '.join(f'{band} over {ms} ms' for band, ms in z['coarse_ms'].items()))
    table = ['  bands (dB under the loudest cell this slicing gives anywhere in the sound; . is more than 60 dB under; ^: '
             'the value above holds for this row too)',
             '  ' + 'time from file start'.ljust(22) + ''.join(f'{label:>{col}}' for label in labels)]
    rows = ['  ' + row['label'].ljust(22) + ''.join(f'{"." if v is None else v:>{col}}' for v in row['db'])
            for row in z['bands']['slices']]
    return _pages(head, intro + table + rows, len(intro) + len(table), table)


def _pages(head, body, table_at=None, table=()):
    """Body lines cut into pages, each starting with head and, but for the last, ending with how to see the next page; a
    page that starts among the table's rows (body lines from table_at on) repeats the table's heading first."""
    note = '  page 99 of 99: --page 99 goes on'                    # room kept for that line

    def fits(lines):
        page = head + lines + [note]
        return len(page) <= PAGE_LINES and len('\n'.join(page)) <= PAGE_CHARS

    chunks, current = [], []
    for k, line in enumerate(body):
        if current and not fits(current + [line]):
            chunks.append(current)
            current = list(table) if table_at is not None and k >= table_at else []
        current.append(line)
    chunks.append(current)
    return [head + chunk + ([f'  page {k} of {len(chunks)}: --page {k + 1} goes on'] if k < len(chunks) else [])
            for k, chunk in enumerate(chunks, start=1)]


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
