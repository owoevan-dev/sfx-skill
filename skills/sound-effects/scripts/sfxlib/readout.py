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
import os

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
BANDS = {'octave': (LABELS, CENTRES, CROSS_OCT), 'third': (THIRD_LABELS, THIRD_CENTRES, CROSS_OCT / 3)}
FRAME_DATA = ('hzs', 'covered_s', 'pieces', 'dbfs')   # what the Analysis keeps of each line and the views leave out
COMPARE_CENTS = 50.0                # compare: lines further apart than this are not one line changed ...
COMPARE_EVENT_MS = 30               # ... nor events further apart in time one event moved
SAME_CENTS, SAME_DB, SAME_MS = 5.0, 1.0, 5    # ... and smaller changes than these count as the same
LINE_MOVE_MS = 40                   # a line's ends are known to about this
RISE_SAME_DB = 3.0
DECAY_SMOOTH, DECAY_SMOOTH_MAX_MS = 5, 100    # decay: a band's decline read on its level smoothed over this many of
                                    # its resolutions (at most this long), so noise in it does not read as a fall
DECAY_MIN_DB = 10.0                 # a decline counts from this far under the band's peak
DECAY_FIT_DB = (5.0, 25.0)          # the slope is fitted from this far under the peak to this far (or as far as it goes)
LEVEL_OFF_MS, LEVEL_OFF_SHARE = 100, 0.3    # a decline levels off where the level's mean over this long (or this share
LEVEL_OFF_FALL, LEVEL_OFF_DB = 0.25, 3.0    # of the time it has fallen) changes, to the next such stretch, by under this
                                    # share of what its early fall would take there, and never more than this
SETTLE_DB = 1.0                     # ... from where the level first comes within this of the stretch it levels off to
RISE_MEAN_MS, DECAY_RISE_DB = 50, 6.0   # it rises again where its mean over this long climbs this far over its lowest
ATTACK_FROM, ATTACK_TO = 0.1, 0.9   # attack: from this share of the peak's amplitude to this one, on the rise that leads to it
ATTACK_NOTE_MS = 10                 # an attack this long from the file's first millisecond, already loud there, says so
LIST_SHOWN = 3                      # changed, new and gone lines and events printed, each, before "and N more"


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
    frequency (the lowest band takes everything under it, the highest everything over it). The centres must rise, and two
    changes may not overlap, which would break that sum."""
    if not (math.isfinite(cross) and cross >= 1e-6):
        raise ValueError(f'a change from one band to the next is at least 1e-6 octaves wide, not {cross!r}')
    if not all(math.isfinite(c) and c > 0 for c in centres):
        raise ValueError('band centres are frequencies above 0 Hz')
    logs = [math.log2(c) for c in centres]
    if any(b <= a for a, b in zip(logs, logs[1:])):
        raise ValueError('band centres must rise')
    if any(c - a < 2 * cross for a, c in zip(logs, logs[2:])):     # midpoints (c - a) / 2 octaves apart
        raise ValueError(f'changes {cross:g} octaves wide would overlap at these centres')
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
    a steady pitch's would read in the long window; `pieces`, the stretches it sounds in, in samples). Two windows: a long one (N_FFT)
    resolves low and close pitches, a short one (SHORT_FFT, from SHORT_FROM_HZ up) follows glides too quick for the long
    one; a short-window line that a long-window line already holds is dropped."""
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
        pieces, first = [], points[0][0]                       # the stretches it sounds in: a line joined across a gap is
        for prev, point in zip(points, points[1:]):            # silent in between
            if point[0] - prev[0] > GAP_FRAMES + 1:
                pieces.append([first * hop, (prev[0] + 1) * hop])
                first = point[0]
        pieces.append([first * hop, (points[-1][0] + 1) * hop])
        out.append({'hz': round(loud[1], 1), 'note': note_name(loud[1]), 'start_s': round(start, 3), 'end_s': round(end, 3),
                    'db': round(min(0.0, loud[2] - top), 1) + 0.0, 'median_hz': round(float(np.median(hzs)), 1),
                    'from_hz': round(hzs[0], 1), 'to_hz': round(hzs[-1], 1), 'low_hz': round(min(hzs), 1),
                    'high_hz': round(max(hzs), 1), 'hzs': hzs, 'dbfs': loud[2] - 20 * math.log10(long_fft / 4),
                    'covered_s': round(len(points) * hop / rate, 3), 'pieces': pieces})
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
    frequencies (`hzs`), the time its frames hold (`covered_s`), the stretches it sounds in (`pieces`, [from, to) samples) and
    its mains label; rolloff_hz. `events_all` lists every event, the main one marked; `channel_lines_events(c)` gives one
    channel's lines and events the same way."""

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
        self._per_channel = {}
        self._cum = None

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
        if kind not in BANDS:
            raise ValueError(f"bands are 'octave' or 'third', not {kind!r}")
        if kind == 'octave':
            return LABELS, CENTRES, self.channel_env
        if self._third is None:
            self._third = band_envelopes(self.x, self.rate, self.loop, per_channel=True, centres=THIRD_CENTRES,
                                         cross=BANDS['third'][2])
        return THIRD_LABELS, THIRD_CENTRES, self._third

    def band_power(self, s, e):
        """Each band's power from millisecond s to e, both channels, on one scale for every sound (its energy there over
        the samples there)."""
        if self._cum is None:
            energy = self.env * self.counts[None, :]
            self._cum = (np.concatenate([np.zeros((energy.shape[0], 1)), np.cumsum(energy, axis=1)], axis=1),
                         np.concatenate([[0.0], np.cumsum(self.counts)]))
        cum_e, cum_n = self._cum
        return (cum_e[:, e] - cum_e[:, s]) / max(float(cum_n[e] - cum_n[s]), 1.0)

    def channel_power(self, c):
        """One channel's power per millisecond."""
        return _per_ms(self.x[:, c] ** 2, self.rate)

    def channel_lines_events(self, c):
        """One channel's tonal lines (as `lines`, the mains label counting that channel's active part) and every event in it
        (as `events_all`), read once."""
        if c not in self._per_channel:
            raw = self.channel_power(c)
            loud = np.flatnonzero(raw >= raw.max() * 10 ** (-ONSET_DB / 10)) if np.any(raw > 0) else []
            active = 0.0 if not len(loud) else len(raw) / 1000 if self.loop else (loud[-1] + 1 - loud[0]) / 1000
            lines = tonal_lines(self.x[:, c], self.rate)
            for line in lines:
                line['mains'] = mains_line(line, active)
            env = np.minimum(self.channel_env[c], 2 * raw[None, :])
            self._per_channel[c] = (lines, events(env, int(np.argmax(raw)), self.loop, self.peak_at_ms, cap=None)[0])
        return self._per_channel[c]


def analyse(x, rate, loop=False, peak_at=None):
    """The one reading of a sound that the overview and the views share (see Analysis)."""
    return Analysis(x, rate, loop, peak_at)


def _overview_cells(a):
    """The overview's slices, each band's power over each, which bands lie wholly above what the file can hold, and the
    reference: the loudest of those cells that prints."""
    energy = a.env * a.counts[None, :]                           # each band's energy in each millisecond
    slices = _slices(len(a.raw), a.onset_ms, a.loop)
    power = [energy[:, lo:hi].sum(axis=1) / max(float(a.counts[lo:hi].sum()), 1.0) for _, lo, hi in slices]
    above = np.array([c / math.sqrt(2) >= a.rate / 2 for c in CENTRES])
    top = max(float(p[~above].max()) for p in power)             # the loudest cell that prints: a band's power over a slice
    return slices, power, above, top


def view_overview(a):
    """The overview of an Analysis as a dict: bands over time, up to LINES_SHOWN lines, up to EVENTS_SHOWN events, the
    roll-off (see the module's docstring)."""
    out = {'seconds': round(len(a.x) / a.rate, 3), 'loop': a.loop}
    if a.silent:
        out.update(silent=True, onset_s=None, peak_s=None, rolloff_hz=None, bands=None, lines=[], events=[])
        return out
    slices, power, above, top = _overview_cells(a)
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
    shown = [{k: v for k, v in line.items() if k not in FRAME_DATA} for line in shown]
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


def loop_energy(x, rate, total_ms, centres=CENTRES, cross=CROSS_OCT):
    """A loop played on for total_ms milliseconds: each channel's band energies (channels x bands x ms) and whole energy
    (channels x ms) in each millisecond, and the samples in each. Millisecond k holds samples round(k * rate / 1000) up to
    round((k + 1) * rate / 1000) of the loop repeated, so a repeat keeps its true time even when the loop is not a whole
    number of milliseconds long; the bands are split as the circle the loop is."""
    x = _channels(x)
    n = len(x)
    edges = np.round(np.arange(total_ms + 1) * rate / 1000).astype(np.int64)
    turns, at = edges // n, edges % n

    def per_ms(power):                                            # energy in each millisecond of power repeated
        cum = np.concatenate([[0.0], np.cumsum(power)])
        return np.diff(turns * cum[-1] + cum[at])

    weights = band_weights(np.fft.rfftfreq(n, 1 / rate), centres, cross)
    bands, whole = np.zeros((x.shape[1], len(centres), total_ms)), np.zeros((x.shape[1], total_ms))
    for c in range(x.shape[1]):
        spec = np.fft.rfft(x[:, c])
        for b in range(len(centres)):
            y = np.fft.irfft(spec * weights[b], n)
            bands[c, b] = per_ms(y * y)
        whole[c] = per_ms(x[:, c] ** 2)
    return bands, whole, np.diff(edges).astype(float)


def zoom_settings(start_s, end_s, bands='octave', hz=None, channel=None, slice_ms=None):
    """What a zoom asks for that no sound decides: (labels, centres, change width, the bands kept, the window's start and end
    in milliseconds, read to the microsecond). A kind of band, a time, a channel, a slice or a range that cannot be raises
    ValueError with a sentence."""
    if bands not in BANDS:
        raise ValueError(f"bands are 'octave' or 'third', not {bands!r}")
    labels, centres, cross = BANDS[bands]
    if not (math.isfinite(start_s) and math.isfinite(end_s)):
        raise ValueError('a window is given in seconds, as finite numbers')
    begin, finish = round(start_s * 1000, 3), round(end_s * 1000, 3)
    if finish <= begin:
        raise ValueError('the window is empty: --to must come after --from')
    if channel is not None and channel not in ('left', 'right'):
        raise ValueError(f"a channel is 'left' or 'right', not {channel!r}")
    if slice_ms is not None and int(slice_ms) < 1:
        raise ValueError('a slice is at least 1 ms')
    keep = [i for i, f in enumerate(centres) if hz is None or hz[0] <= f <= hz[1]]
    if not keep:
        raise ValueError('no band has its centre in that range')
    return labels, centres, cross, keep, begin, finish


def _secs(seconds):
    """Seconds with three decimals, or up to six where a time falls between whole milliseconds."""
    whole, _, part = f'{seconds:.6f}'.rstrip('0').partition('.')
    return f'{whole}.{part:0<3}'


def view_zoom(a, start_s, end_s, bands='octave', hz=None, channel=None, slice_ms=None):
    """Layer 1: the window from start_s to end_s, in seconds from the file's start, read to the microsecond as asked: it
    must overlap a one-shot; a loop's starts inside the loop, lasts at most one turn and may run past its end, round its
    join. It is read in whole milliseconds (every one it touches), in equal slices: the finest of ZOOM_SLICES_MS that gives
    at most ZOOM_ROWS rows (the coarsest when none does), or slice_ms, at most the window's length; it ends with its last
    whole slice, which may run past a one-shot's end, where the sound is silent. Octave or third-octave bands (those whose
    centre lies in hz, a (low, high) pair, when given), of both channels or of one ('left', 'right'). A cell is the band's
    power over its slice, in dB under the loudest stretch of that cell's length anywhere in the sound, in any band (from
    any millisecond of a one-shot, silent past its end, or of a loop's first two turns): no cell reads above 0, and the
    loudest reads 0 where a window can show it. A band too narrow to separate events as fine as the slices spans several
    rows, measured over all of them (past the window's end if need be): its value stands in the first and '^' in the rest.
    Every line and every event in the window is listed, at the time it comes there: a loop's, each time it comes round."""
    labels, centres, cross, keep, begin, finish = zoom_settings(start_s, end_s, bands, hz, channel, slice_ms)
    if channel is not None and a.x.shape[1] < 2:
        raise ValueError('the sound has one channel')
    n, total = len(a.x), len(a.raw)
    length = n * 1000 / a.rate                                    # the true length in ms
    if a.loop:
        if not 0 <= begin < length or finish - begin > length + 1e-9:
            raise ValueError(f'a loop window starts inside its {length / 1000:g} s and lasts at most that long')
        lo = min(math.floor(begin), total - 1)
    else:
        begin, finish = max(begin, 0.0), min(finish, length)
        if finish <= begin:
            raise ValueError(f'the window misses the sound, which lasts {length / 1000:g} s')
        lo = math.floor(begin)
    hi = min(math.ceil(finish), lo + total + 1)
    if slice_ms is None:
        width = next((w for w in ZOOM_SLICES_MS if -(-(hi - lo) // w) <= ZOOM_ROWS), ZOOM_SLICES_MS[-1])
    elif int(slice_ms) > hi - lo:
        raise ValueError(f'a slice is at most the window, here {hi - lo} ms')
    else:
        width = int(slice_ms)
    rows = -(-(hi - lo) // width)
    hi = lo + rows * width                                        # whole slices
    out = {'zoom': True, 'silent': a.silent, 'seconds': round(length / 1000, 6), 'loop': a.loop, 'from_s': lo / 1000,
           'to_s': hi / 1000, 'slice_ms': width, 'kind': bands, 'channel': channel or 'both'}
    if a.silent:
        return out
    c = None if channel is None else ('left', 'right').index(channel)
    spans = [max(1, math.ceil(_resolve_ms(f, bands) / width)) for f in centres]     # the rows each band's cell covers
    starts = -(-2 * n * 1000 // a.rate) if a.loop else total     # the reference's stretches start in these milliseconds
                                                                  # (a loop's first two turns, to the sample)
    end = max(hi, starts + max(spans) * width)
    if a.loop:
        each, whole, counts = loop_energy(a.x, a.rate, end, centres, cross)
        energy, raw = (_summed(each), _summed(whole)) if c is None else (each[c], whole[c])
    else:                                                         # silent past its end, its milliseconds whole
        each = a.band_set(bands)[2]
        energy, raw = np.zeros((len(centres), end)), np.zeros(end)
        energy[:, :total] = (_summed(each) if c is None else each[c]) * a.counts[None, :]
        raw[:total] = (a.raw if c is None else a.channel_power(c)) * a.counts
        counts = np.diff(np.round(np.arange(end + 1) * a.rate / 1000)).astype(float)
    energy = np.minimum(energy, 2 * raw[None, :])                 # as in the overview: the split's ringing cut off
    cum_e = np.concatenate([np.zeros((len(centres), 1)), np.cumsum(energy, axis=1)], axis=1)
    cum_n = np.concatenate([[0.0], np.cumsum(counts)])

    def power(i, s, e):                                           # band i's power from millisecond s to e
        return (cum_e[i, e] - cum_e[i, s]) / np.maximum(cum_n[e] - cum_n[s], 1.0)

    above = [f * 2 ** (-0.5 if bands == 'octave' else -1 / 6) >= a.rate / 2 for f in centres]
    top = 0.0                                                     # the loudest stretch of each band's cell length
    for i in range(len(centres)):
        if not above[i]:
            s = np.arange(starts)
            top = max(top, float(np.max(power(i, s, s + spans[i] * width))))
    least = 10 ** (-FLOOR_DB / 10)
    table, heads = [], {}
    for r in range(rows):
        s = lo + r * width
        db = []
        for i in keep:
            if above[i]:
                db.append('n/a')
            elif r % spans[i]:                                   # inside a cell the band cannot read finer
                db.append('^' if heads[i] is not None else None)
            else:
                q = float(power(i, s, s + spans[i] * width)) / top if top > 0 else 0.0
                heads[i] = int(round(10 * math.log10(q))) if q >= least else None
                db.append(heads[i])
        table.append({'label': f'{_secs(s / 1000)}-{_secs((s + width) / 1000)} s', 'from_ms': s, 'to_ms': s + width,
                      'db': db})
    lines, found = (a.lines, a.events_all) if c is None else a.channel_lines_events(c)
    turns = range(int(hi // length) + 2) if a.loop else range(1)
    events_in = []
    for event in found:
        t = round(event['time_s'] * 1000)
        events_in += [dict(event, time_s=round((t + k * length) / 1000, 6)) for k in turns if lo <= t + k * length < hi]
    events_in.sort(key=lambda event: event['time_s'])
    first, last = round(lo * a.rate / 1000), round(hi * a.rate / 1000)          # the window in samples
    lines_in = []
    for line in lines:
        for k in turns:
            if any(p0 + k * n < last and p1 + k * n > first for p0, p1 in line['pieces']):
                lines_in.append(dict({key: v for key, v in line.items() if key not in FRAME_DATA},
                                     start_s=round((line['pieces'][0][0] + k * n) / a.rate, 6),
                                     end_s=round((line['pieces'][-1][1] + k * n) / a.rate, 6)))
    coarse = {labels[i]: spans[i] * width for i in keep if spans[i] > 1 and not above[i]}
    return dict(out, bands={'labels': [labels[i] for i in keep], 'centres_hz': [centres[i] for i in keep], 'slices': table},
                coarse_ms=coarse, lines=lines_in, events=events_in)


def _line_text(line):
    """One tonal line as the readout prints it."""
    s = f'    {line["hz"]:g} Hz {line["note"]:<6} {_secs(line["start_s"])}-{_secs(line["end_s"])} s  {line["db"]:g} dB'
    if abs(_cents(line['to_hz'], line['from_hz'])) > GLIDE_CENTS:
        s += f'  moves {line["from_hz"]:g} -> {line["to_hz"]:g} Hz'
    if abs(_cents(line['high_hz'], line['low_hz'])) > GLIDE_CENTS:
        s += f'  ranges {line["low_hz"]:g}-{line["high_hz"]:g} Hz'
    if line.get('mains'):
        s += '  at a mains frequency: hum in a recording, or a drone that was meant'
    return s


def text_zoom(z, name):
    """The zoom as pages of lines, each within PAGE_LINES and PAGE_CHARS; every page but the last ends with how to see the
    next."""
    window = f'{name}: {_secs(z["from_s"])}-{_secs(z["to_s"])} s'
    if z['silent']:
        return [[f'{window}: silence (the whole sound is {z["seconds"]:g} s of silence)']]
    labels = z['bands']['labels']
    col = max(5, max(len(label) for label in labels) + 1)
    where = 'both channels' if z['channel'] == 'both' else f'the {z["channel"]} channel'
    past = ''
    if z['to_s'] > z['seconds']:
        past = (", past the loop's end round its join" if z['loop'] else
                f', the sound ends at {_secs(z["seconds"])} s and is silent after')
    head = [f'{window} in slices of {z["slice_ms"]} ms, {z["kind"]} bands, {where}{past}']
    intro = [f'  events in the window: {len(z["events"])}' + ('' if z['events'] else ' (none)')]
    for e in z['events']:
        rise = 'from silence' if e['rise_db'] is None else f'+{e["rise_db"]:g} dB'
        intro.append(f'    {_secs(e["time_s"])} s  {"main " if e["main"] else "     "} {rise} in {" ".join(e["bands"])}')
    intro.append(f'  tonal lines sounding in the window: {len(z["lines"])}' + ('' if z['lines'] else ' (none)'))
    intro += [_line_text(line) for line in z['lines']]
    if z['coarse_ms']:
        intro.append('  read coarser, as the band cannot separate finer: ' +
                     ', '.join(f'{band} over {ms} ms' for band, ms in z['coarse_ms'].items()))
    table = ['  bands (dB under the loudest stretch of each cell\'s length in the sound; . is more than 60 dB under; ^: '
             'the value above holds for this row too)',
             '  ' + 'time from file start'.ljust(22) + ''.join(f'{label:>{col}}' for label in labels)]
    rows = ['  ' + row['label'].ljust(22) + ''.join(f'{"." if v is None else v:>{col}}' for v in row['db'])
            for row in z['bands']['slices']]
    return _pages(head, intro + table + rows, len(intro) + len(table), table)


def view_compare(a, b):
    """Layer 2, compare: this sound (a) against another (b) on the overview's measures, to see whether an edit did what was
    meant. Each band's level difference per slice on one scale (a minus b in dB, taken before rounding, so a sound 6 dB
    quieter reads -6 wherever both sound): None where both are more than FLOOR_DB under the louder one's loudest cell,
    '>+N' or '<-N' where only one side is above that (N its height over that floor), 'n/a' above either file's reach.
    One-shots are aligned at their onsets and read over the slices both cover, what only the longer has said; loops from
    their starts over the shorter. Tonal lines are matched one to one (overlapping in time, then the nearest within
    COMPARE_CENTS; levels on one scale) and events one to one in time order within COMPARE_EVENT_MS, so a repeated note
    taken out shows as gone; and each one's roll-off. It describes: no score of likeness, nothing judged."""
    out = {'compare': True, 'loop': a.loop, 'seconds': [round(len(a.x) / a.rate, 3), round(len(b.x) / b.rate, 3)]}
    silent = [side for side, sound in (('this', a), ('that', b)) if sound.silent]
    if silent:
        return dict(out, silent=silent)
    if a.loop != b.loop:
        raise ValueError('a loop and a one-shot are not compared: one has a join, the other an onset and a tail')
    notes = []
    if a.loop:
        common, shift = min(len(a.raw), len(b.raw)), (0, 0)
        rows = [(label, s, e, s, e) for label, s, e in _slices(common, 0, True)]
        if len(a.raw) != len(b.raw):
            notes.append(f'read over the first {common / 1000:g} s, the shorter loop')
    else:
        common, shift = min(len(a.raw) - a.onset_ms, len(b.raw) - b.onset_ms), (a.onset_ms, b.onset_ms)
        rows = [(label, a.onset_ms + s, a.onset_ms + e, b.onset_ms + s, b.onset_ms + e)
                for label, s, e in _slices(common, 0, False)]
        if a.onset_ms > BEFORE_MS and b.onset_ms > BEFORE_MS:      # the same stretch right before each onset
            lead = min(a.onset_ms, b.onset_ms)
            rows.insert(0, ('before', a.onset_ms - lead, a.onset_ms, b.onset_ms - lead, b.onset_ms))
        for side, sound in (('this', a), ('that', b)):
            if len(sound.raw) - sound.onset_ms > common:
                notes.append(f'{side} one goes on to {(len(sound.raw) - sound.onset_ms) / 1000:g} s after its onset, '
                             'where the other has ended')
            if sound.onset_ms > BEFORE_MS >= (b if sound is a else a).onset_ms:
                notes.append(f'only {side} one has {sound.onset_ms} ms before its onset')
    above = np.array([c / math.sqrt(2) >= min(a.rate, b.rate) / 2 for c in CENTRES])
    powers = [(a.band_power(sa, ea), b.band_power(sb, eb)) for _, sa, ea, sb, eb in rows]
    top = max(_overview_cells(a)[3], _overview_cells(b)[3])       # the louder one's loudest cell anywhere, as the overview
    floor = top * 10 ** (-FLOOR_DB / 10)                          # finds it (a shorter slice here can hold more)
    table = []
    for (label, sa, ea, sb, eb), (p, q) in zip(rows, powers):
        db = []
        for i in range(len(CENTRES)):
            if above[i]:
                db.append('n/a')
            elif top <= 0 or (p[i] < floor and q[i] < floor):
                db.append(None)
            elif q[i] < floor:
                db.append(_bound('>+', 10 * math.log10(p[i] / floor)))
            elif p[i] < floor:
                db.append(_bound('<-', 10 * math.log10(q[i] / floor)))
            else:
                db.append(int(round(10 * math.log10(p[i] / q[i]))))
        table.append({'label': label, 'this_ms': [sa, ea], 'that_ms': [sb, eb], 'db': db})
    if a.loop:                                                     # a loop's lines as they sound in what both play
        shared = min(len(a.x) / a.rate, len(b.x) / b.rate)
        lines = _compare_lines(_trimmed_lines(a, shared), a.rate, _trimmed_lines(b, shared), b.rate, shift)
    else:
        lines = _compare_lines(a.lines, a.rate, b.lines, b.rate, shift)
    return dict(out, silent=[], common_s=round(common / 1000, 3), notes=notes,
                onset_s=None if a.loop else [round(a.onset_ms / 1000, 3), round(b.onset_ms / 1000, 3)],
                bands={'labels': list(LABELS), 'centres_hz': list(CENTRES), 'slices': table}, lines=lines,
                events=_compare_events(a.events_all, b.events_all, shift, common if a.loop else None),
                rolloff_hz=[a.rolloff_hz, b.rolloff_hz])


def _bound(sign, db):
    """'>+N' or '<-N': one side at least N dB over the floor (rounded down, so never more than is so)."""
    return f'{sign}{int(math.floor(db))}'


def _shown(line):
    return {k: line[k] for k in ('hz', 'note', 'start_s', 'end_s', 'db')}


def _trimmed_lines(sound, seconds):
    """A loop's tonal lines as they sound in its first `seconds`: tracked on its audio cut there, so that no frame hears
    past it, each level under the whole loop's loudest peak as its own lines are."""
    n = int(round(seconds * sound.rate))
    if n >= len(sound.x):
        return sound.lines
    lines = tonal_lines(sound.x[:n], sound.rate)
    if not sound.lines:
        return lines
    offset = sound.lines[0]['db'] - sound.lines[0]['dbfs']        # the whole loop's reference, from its loudest line
    return [dict(line, db=round(min(0.0, line['dbfs'] + offset), 1) + 0.0) for line in lines]


def _compare_lines(these, this_rate, those, that_rate, shift):
    """Lines matched one to one: sounding at the same time once aligned (piece by piece, so not across a line's silent
    gap), then the nearest in pitch (each line's median, steadier than its loudest frame, which an attack can pull) within
    COMPARE_CENTS; their ends compared in whole milliseconds."""
    def span(line, offset):
        return round(line['start_s'] * 1000) - offset, round(line['end_s'] * 1000) - offset

    def sounding(lines, rate, offset):
        return [[(p0 * 1000 / rate - offset, p1 * 1000 / rate - offset) for p0, p1 in line['pieces']] for line in lines]

    def overlap(xs, ys):
        return any(x0 < y1 and y0 < x1 for x0, x1 in xs for y0, y1 in ys)

    here, there = sounding(these, this_rate, shift[0]), sounding(those, that_rate, shift[1])
    pairs = sorted((abs(_cents(these[i]['median_hz'], those[j]['median_hz'])), i, j) for i in range(len(these))
                   for j in range(len(those))
                   if overlap(here[i], there[j]) and abs(_cents(these[i]['median_hz'], those[j]['median_hz'])) <= COMPARE_CENTS)
    mine, theirs, matched = set(), set(), []
    for _, i, j in pairs:
        if i not in mine and j not in theirs:
            mine.add(i)
            theirs.add(j)
            matched.append((i, j))
    same, changed = 0, []
    for i, j in sorted(matched):
        x, y = these[i], those[j]
        cents, change = _cents(x['median_hz'], y['median_hz']), x['dbfs'] - y['dbfs']
        moved = max(abs(p - q) for p, q in zip(span(x, shift[0]), span(y, shift[1])))
        if abs(cents) < SAME_CENTS and abs(change) < SAME_DB and moved <= LINE_MOVE_MS:
            same += 1
        else:
            changed.append(dict(_shown(x), median_hz=x['median_hz'], was_median_hz=y['median_hz'], cents=round(cents, 1),
                                db_change=round(change, 1), was_start_s=y['start_s'], was_end_s=y['end_s'],
                                moved=moved > LINE_MOVE_MS))
    return {'same': same, 'changed': changed, 'new': [_shown(these[i]) for i in range(len(these)) if i not in mine],
            'gone': [_shown(those[j]) for j in range(len(those)) if j not in theirs]}


def _compare_events(these, those, shift, until=None):
    """Events matched one to one in time order (once aligned, in whole milliseconds) within COMPARE_EVENT_MS; a loop's
    only before `until` ms."""
    if until is not None:
        these = [e for e in these if round(e['time_s'] * 1000) < until]
        those = [e for e in those if round(e['time_s'] * 1000) < until]
    at = [round(e['time_s'] * 1000) - shift[0] for e in these]
    was = [round(e['time_s'] * 1000) - shift[1] for e in those]
    i = j = same = 0
    changed, new, gone = [], [], []
    while i < len(these) or j < len(those):
        if j == len(those) or (i < len(these) and at[i] < was[j] - COMPARE_EVENT_MS):
            new.append(these[i])
            i += 1
        elif i == len(these) or was[j] < at[i] - COMPARE_EVENT_MS:
            gone.append(those[j])
            j += 1
        else:
            x, y = these[i], those[j]
            delta = at[i] - was[j]
            risen = ((x['rise_db'] is None) != (y['rise_db'] is None) or
                     (x['rise_db'] is not None and abs(x['rise_db'] - y['rise_db']) > RISE_SAME_DB))
            if abs(delta) < SAME_MS and not risen and x['bands'] == y['bands']:
                same += 1
            else:
                changed.append({'time_s': x['time_s'], 'was_time_s': y['time_s'], 'shift_ms': delta, 'rise_db': x['rise_db'],
                                'was_rise_db': y['rise_db'], 'bands': x['bands'], 'was_bands': y['bands']})
            i += 1
            j += 1
    return {'same': same, 'changed': changed, 'new': new, 'gone': gone}


def _rise(value):
    return 'from silence' if value is None else f'+{value:g} dB'


def _signed(value):
    n = int(round(value))
    return '0' if n == 0 else f'{n:+d}'


def text_compare(z, name, this, that):
    """The comparison as pages of lines (one, as a rule), each within PAGE_LINES and PAGE_CHARS."""
    who = f'{this} against {that}' if name == this else f'{name}: {this} against {that}'
    if z['silent']:
        quiet = ' and '.join(this if side == 'this' else that for side in z['silent'])
        return [[f'{who}: {quiet} {"is" if len(z["silent"]) == 1 else "are"} silent, so there is nothing to compare']]
    head = [f'{who}: this minus that, in dB, both on one scale']
    body = [f'  loops of {z["seconds"][0]:g} and {z["seconds"][1]:g} s, from their starts' if z['loop'] else
            f'  {z["seconds"][0]:g} and {z["seconds"][1]:g} s, aligned at their onsets ({z["onset_s"][0]:g} and '
            f'{z["onset_s"][1]:g} s)']
    body += [f'  {note}' for note in z['notes']]
    table = ["  bands (. both more than 60 dB under the louder one's loudest cell; >+N, <-N: only one side is above that, "
             'by N dB)',
             '  ' + ('time from start' if z['loop'] else 'after the onset').ljust(16) +
             ''.join(f'{label:>5}' for label in z['bands']['labels'])]
    rows = ['  ' + row['label'].ljust(16) +
            ''.join(f'{"." if v is None else _signed(v) if isinstance(v, int) else v:>5}' for v in row['db'])
            for row in z['bands']['slices']]
    lists = []
    for title, part in (('tonal lines', z['lines']), ('events', z['events'])):
        if not any((part['same'], part['changed'], part['new'], part['gone'])):
            lists.append(f'  {title}: none in either')
            continue
        lists.append(f'  {title}: {len(part["changed"])} changed, {len(part["new"])} new, {len(part["gone"])} gone, '
                     f'{part["same"]} the same' +
                     (' (a new or gone line at dB under its own sound\'s loudest peak)'
                      if title == 'tonal lines' and (part['new'] or part['gone']) else ''))
        if title == 'tonal lines':
            lists += _listed(part['changed'], lambda x: f'    changed: {x["was_median_hz"]:g} -> {x["median_hz"]:g} Hz '
                             f'{note_name(x["median_hz"])} (medians, {x["cents"]:+.0f} cents), level '
                             f'{_signed(x["db_change"])} dB' +
                             (f', {_secs(x["start_s"])}-{_secs(x["end_s"])} s (was {_secs(x["was_start_s"])}-'
                              f'{_secs(x["was_end_s"])} s)' if x['moved'] else ''))
            item = lambda x: f'{x["hz"]:g} Hz {_secs(x["start_s"])}-{_secs(x["end_s"])} s {x["db"]:g} dB'   # noqa: E731
        else:
            lists += _listed(part['changed'], lambda x: f'    changed: {_secs(x["time_s"])} s ({x["shift_ms"]:+d} ms once '
                             f'aligned), {_rise(x["rise_db"])} (was {_rise(x["was_rise_db"])}) in {" ".join(x["bands"])}'
                             f' (was {" ".join(x["was_bands"])})')
            item = lambda x: f'{_secs(x["time_s"])} s {_rise(x["rise_db"])} in {" ".join(x["bands"])}'   # noqa: E731
        for kind in ('new', 'gone'):
            if part[kind]:
                more = len(part[kind]) - LIST_SHOWN
                lists.append(f'    {kind}: ' + '; '.join(item(x) for x in part[kind][:LIST_SHOWN]) +
                             (f'; and {more} more (JSON lists every one)' if more > 0 else ''))
    ra, rb = z['rolloff_hz']
    lists.append(f'  roll-off: {_khz(ra) if ra else "none"} against {_khz(rb) if rb else "none"}')
    return _pages(head, body + table + rows + lists, len(body) + len(table), table)


def _listed(items, line):
    shown = [line(x) for x in items[:LIST_SHOWN]]
    if len(items) > LIST_SHOWN:
        shown.append(f'    and {len(items) - LIST_SHOWN} more changed (JSON lists every one)')
    return shown


def _smooth(power, width):
    """A centred moving average over `width` milliseconds, of what the file holds (its ends averaged over less)."""
    if width <= 1:
        return power
    n = len(power)
    cum = np.concatenate([[0.0], np.cumsum(power)])
    lo = np.clip(np.arange(n) - width // 2, 0, n)
    hi = np.clip(np.arange(n) - width // 2 + width, 0, n)
    return (cum[hi] - cum[lo]) / np.maximum(hi - lo, 1)


def view_decay(a):
    """Layer 2, decay: how each octave band rises and fades, in dB under the overview's loudest cell. The attack is read on
    the band's power smoothed to its resolution (about two periods of its width): from ATTACK_FROM to ATTACK_TO of its
    peak amplitude, both on the rise that leads to the peak. The decline is read on the power smoothed over DECAY_SMOOTH
    resolutions (at most DECAY_SMOOTH_MAX_MS), so noise in the band does not read as a fall, from that level's peak; it
    counts once it falls DECAY_MIN_DB (a dip that swells back before that is none: it is read from the highest point after
    it), and ends at a later strike (an event in the band, as the overview finds them), where it falls under the overview's
    floor, where it levels off (within LEVEL_OFF_DB of its last low for LEVEL_OFF_MS and LEVEL_OFF_SHARE of the time it
    took to fall), or cut by the file's end. Its slope in dB/s is fitted from DECAY_FIT_DB[0] to DECAY_FIT_DB[1] under the
    peak, or as far as it goes (Peeters et al. 2011's decrease slope kept to the decline), with the time to fall 60 dB at
    that slope, extrapolated when the decline is shorter; where the fitted stretch climbs back and the fit does not fall,
    no slope is given (`unsteady`). A band that never falls that far is held. A loop has no decay."""
    out = {'decay': True, 'seconds': round(len(a.x) / a.rate, 3), 'loop': a.loop, 'silent': a.silent}
    if a.silent or a.loop:
        return out
    _, _, above, top = _overview_cells(a)
    rows = []
    for i, (label, centre) in enumerate(zip(LABELS, CENTRES)):
        if above[i]:
            rows.append({'band': label, 'above': True})
            continue
        resolve = _resolve_ms(centre, 'octave')
        fine = _smooth(a.env[i], max(1, int(round(resolve))))
        width = min(DECAY_SMOOTH_MAX_MS, max(1, int(round(DECAY_SMOOTH * resolve))))
        steady = _smooth(a.env[i], width)
        level = 10 * np.log10(steady / top + TINY)
        peak_ms = int(np.argmax(level))
        if level[peak_ms] < -FLOOR_DB:
            rows.append({'band': label, 'under_floor': True})
            continue
        if len(level) < width:                                     # it reads more at once than the sound lasts
            rows.append({'band': label, 'too_short': True, 'reads_ms': width})
            continue
        amp = np.sqrt(np.maximum(fine, 0.0))
        near = slice(max(0, peak_ms - width // 2), min(len(amp), peak_ms + width // 2 + 1))
        top_ms = near.start + int(np.argmax(amp[near]))              # the fine peak of the same rise
        rise = top_ms
        while rise > 0 and amp[rise - 1] >= ATTACK_TO * amp[top_ms]:
            rise -= 1
        start = rise
        while start > 0 and amp[start - 1] >= ATTACK_FROM * amp[top_ms]:
            start -= 1
        row = {'band': label, 'peak_s': round(peak_ms / 1000, 3), 'peak_db': round(float(level[peak_ms]), 1),
               'attack_ms': rise - start, 'attack_s': [round(start / 1000, 3), round(rise / 1000, 3)]}
        own = math.sqrt(float(a.env[i][0]))                        # the first millisecond's own power, which no smoothing
        if start == 0 and min(own, amp[0]) >= ATTACK_FROM * amp[top_ms]:     # carries back from later: its 10 % lies
            row['starts_at'] = round(float(amp[0] / amp[top_ms]), 2)          # before the file, or in that millisecond
        strikes = [round(e['time_s'] * 1000) for e in a.events_all
                   if label in e['bands'] and round(e['time_s'] * 1000) > peak_ms + MERGE_MS]
        rows.append(dict(row, **_decline(level, peak_ms, strikes[0] if strikes else None, width)))
    return dict(out, bands=rows)


def _decline(level, peak_ms, strike_ms, width):
    """How a band's level, smoothed over `width` ms, falls from its peak (see view_decay): its slope, the time to fall 60
    dB at it, and how the decline ends. A fall from 5 to 25 dB under the peak (or as far as it goes) quicker than that
    smoothing is faster than the band can show: abrupt, with no slope."""
    peak = float(level[peak_ms])
    end, kind = len(level), 'cut'
    under = np.flatnonzero(level[peak_ms:] < -FLOOR_DB)
    if under.size:
        end, kind = peak_ms + int(under[0]), 'floor'
    if strike_ms is not None and strike_ms < end:                  # before the smoothing starts to hear it
        end, kind = max(peak_ms + 1, strike_ms - width // 2), 'strike'
    part = level[peak_ms:end]
    search = 0
    while True:
        swell = _rises(part[search:])
        if swell is None:
            break
        at = search + swell
        if peak - float(np.min(part[:at])) < DECAY_MIN_DB:         # a dip that swells back before falling 10 dB is no
            search = at                                            # decline: look on past it
            continue
        kind, rose_at = 'rises', peak_ms + at
        part = part[:int(np.argmin(part[:at])) + 1]                # the decline ends at its low before the swell
        break
    begin = 0                                                      # where the decline starts
    if search:                                                     # after a dip that swelled back: from the top after it
        down = np.flatnonzero(part[search:] <= peak - DECAY_MIN_DB)
        if down.size:
            begin = search + int(np.argmax(part[search:search + int(down[0]) + 1]))
    held = {'kind': 'held', **({'strike_s': round(strike_ms / 1000, 3)} if kind == 'strike' else {})}
    ending = {'kind': kind}
    if kind == 'floor':
        ending['at_s'] = round(end / 1000, 3)
    elif kind == 'strike':
        ending['at_s'] = round(strike_ms / 1000, 3)
    elif kind == 'rises':
        ending['at_s'] = round(rose_at / 1000, 3)
    if peak - float(np.min(part[begin:])) < DECAY_MIN_DB:          # it never falls 10 dB from there (a quiet band can
        return {'slope_db_s': None, 't60_s': None, 'extrapolated': None, 'abrupt': False,      # fall under the floor first)
                'end': ending if kind == 'floor' else held}
    first = begin + int(np.argmax(part[begin:] <= peak - DECAY_FIT_DB[0]))
    tenth = begin + int(np.argmax(part[begin:] <= peak - DECAY_MIN_DB))
    early = DECAY_MIN_DB - DECAY_FIT_DB[0]                          # dB from the 5 dB to the 10 dB crossing
    stop = _plateau(part, first, tenth, early / max(tenth - first, 1))
    if stop is not None:
        kind, part = 'levels_off', part[:stop + 1]
        ending = {'kind': kind, 'at_s': round((peak_ms + stop) / 1000, 3), 'db': round(float(part[stop]), 1)}
    elif kind == 'cut':
        ending['db'] = round(float(part[-1]), 1)
    deep = np.flatnonzero(part[first:] <= peak - DECAY_FIT_DB[1])
    last = first + int(deep[0]) if deep.size else len(part) - 1
    if last - first <= width:                                      # faster than the band's smoothing can show
        return {'slope_db_s': None, 't60_s': None, 'extrapolated': None, 'abrupt': True, 'end': ending}
    t = np.arange(first, last + 1) / 1000
    slope = float(np.polyfit(t, part[first:last + 1], 1)[0])
    if slope >= 0:                                                 # the fitted stretch climbs back: no slope to give
        return {'slope_db_s': None, 't60_s': None, 'extrapolated': None, 'abrupt': False, 'unsteady': True, 'end': ending}
    return {'slope_db_s': round(slope, 1), 't60_s': round(-60 / slope, 3),
            'extrapolated': bool(peak - float(np.min(part)) < 60), 'abrupt': False, 'end': ending}


def _rises(part):
    """Where a declining level rises again: its mean over RISE_MEAN_MS climbing DECAY_RISE_DB over its lowest since (a
    swell; a sudden strike is an event), as an index into part, or None."""
    w = RISE_MEAN_MS
    if len(part) < 2 * w:
        return None
    cum = np.concatenate([[0.0], np.cumsum(part)])
    means = (cum[w:] - cum[:-w]) / w
    up = np.flatnonzero(means >= np.minimum.accumulate(means) + DECAY_RISE_DB)
    return int(up[0]) + w // 2 if up.size else None


def _plateau(part, first, tenth, early_db_ms):
    """Where a decline levels off (an index into part, or None): from its 10 dB point on, the first place where the level's
    mean over a stretch (LEVEL_OFF_MS, or LEVEL_OFF_SHARE of the time since its 5 dB point) changes, to the next such
    stretch, by under LEVEL_OFF_FALL of what its early fall would take over it, and never more than LEVEL_OFF_DB; given
    as where the level first comes within SETTLE_DB of that next stretch's mean."""
    cum = np.concatenate([[0.0], np.cumsum(part)])
    for t in range(tenth, len(part)):
        w = max(LEVEL_OFF_MS, int(LEVEL_OFF_SHARE * (t - first)))
        if t + 2 * w > len(part):
            return None
        settled = (cum[t + 2 * w] - cum[t + w]) / w
        if abs(settled - (cum[t + w] - cum[t]) / w) <= min(LEVEL_OFF_FALL * early_db_ms * w, LEVEL_OFF_DB):
            near = np.flatnonzero(part[t:t + 2 * w] <= settled + SETTLE_DB)
            return t + int(near[0]) if near.size else t
    return None


def text_decay(z, name):
    """The decay view as pages of lines (one, as a rule)."""
    if z['silent']:
        return [[f'{name}: {z["seconds"]} s of silence']]
    if z['loop']:
        return [[f'{name}: a loop has no decay; its level over time is in the overview and the zoom']]
    head = [f'{name}: decay of each octave band (levels in dB against the overview\'s loudest cell, which a short peak '
            'can top; times from the file\'s start; ~ extrapolated)']
    body = ['  band  peak at   level  attack       slope  60 dB in  how it ends']
    for row in z['bands']:
        if row.get('above'):
            body.append(f'  {row["band"]:<5} above what the file can hold')
            continue
        if row.get('under_floor'):
            body.append(f'  {row["band"]:<5} more than 60 dB under throughout')
            continue
        if row.get('too_short'):
            body.append(f'  {row["band"]:<5} too short for this band, which reads {row["reads_ms"]} ms at once')
            continue
        end = row['end']
        how = {'floor': lambda: f'falls under the floor at {_secs(end["at_s"])} s',
               'levels_off': lambda: f'levels off at {end["db"]:g} dB from {_secs(end["at_s"])} s',
               'strike': lambda: f'a later strike at {_secs(end["at_s"])} s',
               'rises': lambda: f'rises again from {_secs(end["at_s"])} s',
               'cut': lambda: f'cut by the file\'s end at {end["db"]:g} dB, still falling',
               'held': lambda: 'held: never 10 dB under its peak' +
                               (f' before a later strike at {_secs(end["strike_s"])} s' if 'strike_s' in end else '')}
        extra = ('; faster than this band can show' if row['abrupt'] else '') + (
            '; no steady fall to fit a slope to' if row.get('unsteady') else '') + (
            f'; already at {row["starts_at"]:.0%} in the file\'s first millisecond'
            if 'starts_at' in row and row['attack_ms'] >= ATTACK_NOTE_MS else '')
        slope = '' if row['slope_db_s'] is None else f'{row["slope_db_s"]:.0f} dB/s'
        t60 = '' if row['t60_s'] is None else f'{row["t60_s"]:.2f} s' + ('~' if row['extrapolated'] else ' ')
        body.append(f'  {row["band"]:<5} {_secs(row["peak_s"])} s {_signed(row["peak_db"]):>6} {row["attack_ms"]:>4} ms '
                    f'{slope:>11} {t60:>9}  {how[end["kind"]]()}{extra}')
    return _pages(head, body)


def _pages(head, body, table_at=None, table=()):
    """Body lines cut into pages, each starting with head and, but for the last, ending with how to see the next page; a
    page with the blank line sfx.py prints after it keeps within PAGE_LINES and PAGE_CHARS as printed (a line's end is two
    characters on Windows). A page that starts among the table's rows (body lines from table_at on) repeats the table's
    heading first."""
    nines = '9' * len(str(len(body) + 1))                         # never more pages than body lines
    note = f'  page {nines} of {nines}: --page {nines} goes on'   # room kept for that line
    end = len(os.linesep)

    def fits(lines):
        page = head + lines + [note, '']
        return len(page) <= PAGE_LINES and sum(len(line) + end for line in page) <= PAGE_CHARS

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
        out += [_line_text(line) for line in r['lines']]
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
