"""Loudness and true peak of a sound at 48 kHz.

The K-weighting and the momentary and integrated loudness (k_response ... integrated) are copied unchanged from
music-production 0.8.0 (main ba4338a), scripts/musiclib/measure.py: ITU-R BS.1770-4 K-weighting applied in the
frequency domain, momentary loudness in 400 ms windows and gated integrated loudness. MIT License, Copyright (c) 2026
music-skill contributors; the full notice is in LICENSES/music-production-MIT.txt beside this skill.

max_momentary, brief and true_peak_db are this skill's, and so are the loop measures (loop_momentary, loop_integrated,
loop_range, loop_true_peak_db), which read a loop's period as it plays on: from the period repeated, or circularly.
"""
import functools
import math

import numpy as np

RATE = 48000

# ITU-R BS.1770-4 K-weighting at 48 kHz: (b, a) of the shelf and of the RLB high-pass.
SHELF = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585])

RLB = ([1, -2, 1], [1, -1.99004745483398, 0.99007225036621])

@functools.lru_cache(maxsize=2)
def k_response(n):
    """Complex K-weighting response at the rfft bins of an n-sample signal, read-only.  Kept for the last two lengths
    while a stage measures (a render measures every bus and its stems at one length, and computing the response took
    as long as the FFT); release() lets them go when the stage is done: n/2 + 1 complex values are about 115 MB for a
    five-minute song."""
    z = np.exp(-2j * np.pi * np.fft.rfftfreq(n))
    h = np.ones(len(z), dtype=complex)
    for b, a in (SHELF, RLB):
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    h.setflags(write=False)
    return h

def release():
    """Drop the kept K-weighting responses: called when a render or an analysis has done its measuring."""
    k_response.cache_clear()

def fast_length(n):
    """The smallest 2^a * 3^b * 5^c of at least n.  A song's sample count often has a large prime factor (4,285,090 =
    2 * 5 * 428,509), and an FFT of that length takes about four times as long."""
    best, p5 = 1 << (n - 1).bit_length(), 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            q = p35
            while q < n:
                q *= 2
            best, p35 = min(best, q), p35 * 3
        p5 *= 5
    return best

def k_filter(x):
    """K-weighted copy of x (frames, or frames x channels) as float64.  The signal is zero-padded to a fast FFT
    length, which also keeps the filter's decay from wrapping round to the start."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 2:
        return np.zeros_like(x)
    m = fast_length(len(x))
    h = k_response(m)
    spectrum = np.fft.rfft(x, n=m, axis=0)
    return np.fft.irfft(spectrum * (h if x.ndim == 1 else h[:, None]), n=m, axis=0)[:len(x)]

def level(y):
    """Ungated mean level of an already K-weighted signal, summed over channels as BS.1770 does (LUFS-like)."""
    y = np.asarray(y, dtype=np.float64)
    if y.ndim == 1:
        y = y[:, None]
    power = float(np.mean(y ** 2, axis=0).sum()) if len(y) else 0.0
    return -0.691 + 10 * math.log10(max(power, 1e-20))

def k_level(x):
    return level(k_filter(x))

K_SIZE, K_MARGIN = 1 << 16, 4096

@functools.lru_cache(maxsize=1)
def _k_block_response():
    return k_response.__wrapped__(K_SIZE).astype(np.complex64)

def k_power(x):
    """The K-weighted power of `x` at each sample, summed over its channels: k_filter(x) squared, to about 1e-6 of
    it.  The filter runs in single precision over blocks of 2**16 samples whose margins
    (4096 samples, some twenty time constants of the high-pass, its slowest part) are discarded; k_filter's one
    transform of the whole signal took 2 to 4 s for a mix of four minutes, where this takes 0.8 s.  `x` is read block by block and never copied
    whole, so a file's samples can be given as they are mapped."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    n, step = len(x), K_SIZE - 2 * K_MARGIN
    h = _k_block_response()[:, None]
    out = np.empty(n)
    for s in range(0, n, step):
        a, b = max(0, s - K_MARGIN), min(n, s + step + K_MARGIN)
        seg = np.zeros((K_SIZE, x.shape[1]), dtype=np.float32)
        seg[a - (s - K_MARGIN):b - (s - K_MARGIN)] = x[a:b]
        y = np.fft.irfft(np.fft.rfft(seg, axis=0) * h, n=K_SIZE, axis=0)
        keep = min(step, n - s)
        out[s:s + keep] = (y[K_MARGIN:K_MARGIN + keep].astype(np.float64) ** 2).sum(axis=1)
    return out

def momentary(x):
    """Momentary loudness (LUFS) of `x` as BS.1770 defines it, K-weighted in 400 ms windows, here every 100 ms (the
    75% overlap its gated measure uses).  A signal shorter than one window gives one value, the window filled out
    with silence (this skill's reading, see integrated)."""
    power = k_power(x)
    c = np.concatenate([[0.0], np.cumsum(power)])
    window, hop = int(.4 * RATE), int(.1 * RATE)
    starts = np.arange(0, max(1, len(power) - window + 1), hop)
    return -0.691 + 10 * np.log10(np.maximum((c[np.minimum(starts + window, len(power))] - c[starts]) / window, 1e-20))

def integrated(x):
    """Integrated loudness (LUFS) of `x` by ITU-R BS.1770-4: the mean power of its 400 ms blocks (75% overlap) that
    pass the absolute gate at -70 LUFS and then the relative gate 10 LU under the mean of those.  None for silence.
    Computed here, so that two signals in memory are measured alike; a file's loudness for delivery is FFmpeg's.

    A signal shorter than one block (400 ms) has no integrated loudness in the standard, which counts whole blocks
    only.  Here it is read as one block, with silence after it, so that a bus of a few milliseconds still has a level
    to be matched by: that reading is this skill's and not BS.1770's (100 ms of a tone reads 6 dB under the tone)."""
    blocks = momentary(x)
    power = 10 ** ((blocks + 0.691) / 10)
    kept = power[blocks > -70]
    if not len(kept):
        return None
    kept = kept[kept > kept.mean() * 10 ** (-10 / 10)]
    return -0.691 + 10 * math.log10(float(kept.mean()))


# ---- This skill's measures ----------------------------------------------------------------------------------------

WINDOW = int(0.4 * RATE)  # the momentary window of BS.1770 and EBU Tech 3341
BRIEF_WINDOW = int(0.2 * RATE)  # how long hearing adds up a short sound's energy (see brief)


def _loudest_window(x, window):
    """The highest K-weighted loudness (LUFS) of `x` over windows of `window` samples starting at every sample, so the
    figure does not depend on where the sound sits in its file. A sound shorter than a window is read as one window
    with silence after it. -inf for silence."""
    power = k_power(x)
    if not len(power) or not np.any(power > 0):
        return -np.inf
    c = np.concatenate([[0.0], np.cumsum(power)])
    if len(power) <= window:
        best = c[-1] / window
    else:
        best = float(np.max(c[window:] - c[:-window])) / window
    if best <= 0:
        return -np.inf
    return -0.691 + 10 * math.log10(best)


def max_momentary(x):
    """The highest momentary loudness (LUFS) of `x`: 400 ms windows, as BS.1770 defines them. A 100 ms sound reads 6 dB
    under the same sound held for 400 ms."""
    return _loudest_window(x, WINDOW)


def brief(x):
    """The loudness (LUFS) of the loudest 200 ms of `x`, K-weighted: what a one-shot is levelled by. For sounds under
    about 200 ms perceived loudness follows the sound's total energy, and a sound held longer gets no louder (Scharf
    1978, as reviewed by Oceák, Winkler and Sussman 2006), so a 100 ms sound reads 3 dB under the same sound held. The
    400 ms of the momentary window would read it 6 dB under, and a level step going by that pushes short sounds up by
    as much again. Taking 200 ms as the window is this skill's reading of that figure; BS.1770 does not define it."""
    return _loudest_window(x, BRIEF_WINDOW)


@functools.lru_cache(maxsize=1)
def _oversampling_filter(factor=4, taps_per_phase=32, beta=8.0):
    """Low-pass FIR for `factor` times oversampling: a Kaiser-windowed sinc with its cut-off at the original Nyquist
    frequency, scaled by `factor` so the zero-stuffed signal keeps its level."""
    n = factor * taps_per_phase + 1
    m = np.arange(n) - (n - 1) / 2
    h = np.sinc(m / factor) * np.kaiser(n, beta)
    return h * factor / np.sum(h)


def true_peak_db(x, factor=4):
    """True peak (dBTP) as BS.1770 defines it: the sample peak of the signal oversampled four times (at 48 kHz), here
    with a 129-tap Kaiser-windowed sinc interpolator; the largest over channels. -inf for silence."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    h = _oversampling_filter(factor)
    peak = 0.0
    for ch in range(x.shape[1]):
        up = np.zeros(len(x) * factor)
        up[::factor] = x[:, ch]
        size = fast_length(len(up) + len(h) - 1)
        y = np.fft.irfft(np.fft.rfft(up, size) * np.fft.rfft(h, size), size)[:len(up) + len(h) - 1]
        peak = max(peak, float(np.max(np.abs(y))), float(np.max(np.abs(x[:, ch]))))
    return 20 * math.log10(peak) if peak > 0 else -np.inf


# ---- Loops: a period measured as it plays on ------------------------------------------------------------------------

SHORT_TERM = 3 * RATE       # the short-term window of EBU Tech 3341 / 3342
HOP = int(0.1 * RATE)       # windows every 100 ms (Tech 3342: at least 10 Hz)
LRA_ABS_GATE = -70.0        # EBU Tech 3342: absolute gate (LUFS),
LRA_REL_GATE = -20.0        # relative gate (LU under the power mean of what passed the absolute gate),
LRA_PERCENTILES = (10, 95)  # and the low and high percentiles whose difference is the loudness range


def _frames(x):
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


def _loop_windows(x, window):
    """Loudness (LUFS) of windows of `window` samples starting every HOP within a loop's period, read from the period
    repeated (enough times that every window and the K-weighting's start-up fit before and after it), so a window
    across the join is measured like any other."""
    frames = _frames(x)
    n = len(frames)
    reps = 2 + int(math.ceil(window / max(n, 1)))
    power = k_power(np.concatenate([frames] * reps))
    c = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(n, 2 * n, HOP)
    return -0.691 + 10 * np.log10(np.maximum((c[starts + window] - c[starts]) / window, 1e-20))


def loop_momentary(x):
    """Momentary loudness (400 ms windows) of a loop's period as it plays on, every 100 ms."""
    return _loop_windows(x, WINDOW)


def loop_integrated(x):
    """Integrated loudness of a loop's period as it plays on: the momentary blocks of loop_momentary through the gates
    `integrated` applies (absolute -70 LUFS, relative -10 LU). None for silence."""
    blocks = loop_momentary(x)
    power = 10 ** ((blocks + 0.691) / 10)
    kept = power[blocks > -70]
    if not len(kept):
        return None
    kept = kept[kept > kept.mean() * 10 ** (-10 / 10)]
    return -0.691 + 10 * math.log10(float(kept.mean()))


def loop_range(x):
    """Loudness range (LU) of a loop's period as it plays on, by EBU Tech 3342: short-term loudness (3 s windows,
    every 100 ms, wrapping round the join) through an absolute gate at -70 LUFS and a relative gate 20 LU under the
    power mean of what passed, then the 95th percentile less the 10th (the percentile indices as the document's
    reference code takes them). None for silence."""
    st = _loop_windows(x, SHORT_TERM)
    st = st[st >= LRA_ABS_GATE]
    if not len(st):
        return None
    mean = 10 * math.log10(float(np.mean(10 ** (st / 10))))
    st = np.sort(st[st >= mean + LRA_REL_GATE])
    n = len(st)
    # MATLAB's round((n - 1) p / 100 + 1), one-based, rounds halves up: floor(v + 0.5) as a zero-based index
    low = st[int(math.floor((n - 1) * LRA_PERCENTILES[0] / 100 + 0.5))]
    high = st[int(math.floor((n - 1) * LRA_PERCENTILES[1] / 100 + 0.5))]
    return float(high - low)


def loop_true_peak_db(x, factor=4):
    """True peak of a loop's period as it plays on: true_peak_db's interpolator run circularly, so the join is
    interpolated from the samples on both of its sides (a file read on its own starts from silence, and the
    interpolator would ring there)."""
    frames = _frames(x)
    h = _oversampling_filter(factor)
    peak = 0.0
    for ch in range(frames.shape[1]):
        up = np.zeros(len(frames) * factor)
        up[::factor] = frames[:, ch]
        folded = np.bincount(np.arange(len(h)) % len(up), weights=h, minlength=len(up))
        y = np.fft.irfft(np.fft.rfft(up) * np.fft.rfft(folded), len(up))
        peak = max(peak, float(np.max(np.abs(y))), float(np.max(np.abs(frames[:, ch]))))
    return 20 * math.log10(peak) if peak > 0 else -np.inf
