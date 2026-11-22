"""The effects of a sound's fixed chain: EQ, saturation (drive), delay, a small room, fades.

Delay and room are linear and time-invariant, so they are applied exactly in the frequency domain (their feedback
loops summed as geometric series) on a signal padded for their tails; drive runs at four times the rate.

With loop=True the input is one period of a loop and so is the output: the linear effects use their transfer
functions at the period's DFT frequencies (the tails fold back onto the start, as they do when the loop plays on), and
drive runs on the period with a little of its own end before it and of its start after it."""
import math

import numpy as np

from . import dsp, filters
from .curves import samples
from .loudness import fast_length

RATE = 48000
MAX_TAIL_S = 4.0


def _frames(x):
    x = np.asarray(x, dtype=np.float64)
    return (x[:, None], True) if x.ndim == 1 else (x, False)


def _per_channel(x, fn):
    frames, mono = _frames(x)
    out = [fn(frames[:, c], c) for c in range(frames.shape[1])]
    length = max(len(o) for o in out)
    y = np.zeros((length, len(out)))
    for c, o in enumerate(out):
        y[:len(o), c] = o
    return y[:, 0] if mono else y


LOOP_PAD = 2 * ((dsp.TAPS - 1) // (2 * dsp.FACTOR)) + 16   # base samples the two resampling filters reach, and some


def drive(x, amount, loop=False):
    """Soft saturation tanh(g x) / tanh(g), g from 1 (amount 0) to 10 (amount 1), at four times the rate."""
    amount = float(amount)
    if amount <= 0:
        return np.array(x, dtype=np.float64, copy=True)
    g = 1 + 9 * min(amount, 1.0)

    def one(ch, _):
        if not loop:
            return dsp.downsample(np.tanh(g * dsp.upsample(ch)) / math.tanh(g))[:len(ch)]
        n = len(ch)
        padded = np.take(ch, np.arange(-LOOP_PAD, n + LOOP_PAD), mode='wrap')
        return dsp.downsample(np.tanh(g * dsp.upsample(padded)) / math.tanh(g))[LOOP_PAD:LOOP_PAD + n]
    return _per_channel(x, one)


def _one_pole(z, cutoff_hz):
    p = math.exp(-2 * math.pi * cutoff_hz / RATE)
    return (1 - p) / (1 - p * z)


def _z(n):
    return np.exp(-2j * np.pi * np.fft.rfftfreq(n))


def _circular(ch, response):
    """One period of a loop through a transfer function given as response(z) on the period's DFT frequencies."""
    n = len(ch)
    return np.fft.irfft(np.fft.rfft(ch) * response(_z(n)), n)


def delay(x, time_s, feedback, mix, damping_hz=None, loop=False):
    """Feedback delay: the dry signal plus mix times echoes every time_s, each feedback times the one before
    (feedback up to 0.95), optionally low-passed at damping_hz on every pass. The output is longer by the echoes'
    tail (until they fall 80 dB, at most MAX_TAIL_S); a loop keeps its length."""
    d = max(1, samples(time_s))
    fb = min(max(float(feedback), 0.0), 0.95)
    echoes = math.ceil(math.log(1e-4) / math.log(fb)) + 1 if fb > 0 else 1
    tail = min(d * echoes, int(MAX_TAIL_S * RATE))
    wrap = d * (math.ceil(math.log(1e-7) / math.log(fb)) + 2) if fb > 0 else d

    def response(z):
        path = z ** d * (_one_pole(z, damping_hz) if damping_hz else 1.0)
        return 1 + float(mix) * path / (1 - fb * path)

    def one(ch, _):
        if loop:
            return _circular(ch, response)
        total = len(ch) + tail
        size = fast_length(total + wrap)
        return filters.causal(ch, np.fft.irfft(np.fft.rfft(ch, size) * response(_z(size)), size)[:total])
    return _per_channel(x, one)


DAMPING_HZ = 6000.0                   # one-pole low-pass inside each loop of the room


FDN_MS = (29.7, 37.1, 41.1, 43.7, 47.3, 53.9, 59.3, 61.7)   # this skill's eight delay lengths at size 1
FDN_CHUNK = 16384                                            # frequencies solved at once
OUTPUT_SIGNS = (np.ones(8), np.array([1.0, -1.0] * 4))       # orthogonal output taps: one per channel


def _hadamard(n):
    """The n x n Hadamard matrix (n a power of two), normalized so it is orthogonal (lossless)."""
    h = np.array([[1.0]])
    while len(h) < n:
        h = np.block([[h, h], [h, -h]])
    return h / math.sqrt(n)


def _prime_at_least(m):
    m = max(2, int(m))
    while any(m % k == 0 for k in range(2, int(math.isqrt(m)) + 1)):
        m += 1
    return m


def room_delays(size=1.0):
    """The network's eight delay lengths in samples: FDN_MS scaled by size, each moved up to a prime so that no two
    share a factor (Smith's prime-power lengths)."""
    return [_prime_at_least(samples(ms * float(size) / 1000)) for ms in FDN_MS]


def room(x, size=1.0, decay_s=0.6, mix=0.2, loop=False):
    """A room: a feedback delay network of eight delay lines mixed through a Hadamard matrix, with a one-pole low-pass
    in each loop so that highs die sooner, as air absorption makes them (Jot and Chaigne 1991; Smith, Physical Audio
    Signal Processing, FDN Reverberation). Each line's gain 10^(-3 M / (Fs decay_s)) makes the network fall 60 dB in
    decay_s; size scales the delays. At size 1 the total delay meets Schroeder's mode density (0.15 T60 Fs) for decays
    up to 2.49 s. The channels take orthogonal output taps, so their reverberation is decorrelated. Solved exactly in
    the frequency domain; the output is longer by the reverberation's tail, and a loop keeps its length."""
    decay_s = max(float(decay_s), 0.05)
    tail = min(int(decay_s * 1.3 * RATE), int(MAX_TAIL_S * RATE))
    lengths = np.array(room_delays(size))
    gains = 10 ** (-3 * lengths / (RATE * decay_s))
    matrix = _hadamard(len(lengths))
    inject = np.ones(len(lengths)) / math.sqrt(len(lengths))
    eye = np.eye(len(lengths))

    def one(ch, c):
        taps = OUTPUT_SIGNS[c % 2] / math.sqrt(len(lengths))

        def response(z):
            lp = _one_pole(z, DAMPING_HZ)
            wet = np.empty(len(z), dtype=complex)
            for s in range(0, len(z), FDN_CHUNK):
                zz = z[s:s + FDN_CHUNK]
                delay = zz[:, None] ** lengths[None, :]
                loss = gains[None, :] * lp[s:s + FDN_CHUNK, None]
                system = eye[None] - delay[:, :, None] * matrix[None] * loss[:, None, :]
                state = np.linalg.solve(system, (delay * inject[None, :])[:, :, None])[:, :, 0]
                wet[s:s + FDN_CHUNK] = state @ taps
            return 1 + float(mix) * wet

        if loop:
            return _circular(ch, response)
        total = len(ch) + tail
        n = fast_length(total + int(2.5 * decay_s * RATE))
        return filters.causal(ch, np.fft.irfft(np.fft.rfft(ch, n) * response(_z(n)), n)[:total])
    return _per_channel(x, one)


def eq(x, low_cut_hz=None, high_cut_hz=None, peak=None, loop=False):
    """Two-pole low cut and high cut (q 0.707) and one bell {'hz', 'gain_db', 'q'}; same length."""
    sections = []
    if low_cut_hz:
        sections.append(filters.biquad('highpass', float(low_cut_hz), 0.7071))
    if high_cut_hz:
        sections.append(filters.biquad('lowpass', float(high_cut_hz), 0.7071))
    if peak:
        sections.append(filters.biquad('peak', float(peak['hz']), float(peak.get('q', 1.0)), float(peak['gain_db'])))
    if not sections:
        return np.array(x, dtype=np.float64, copy=True)
    run = filters.apply_circular if loop else filters.apply
    return _per_channel(x, lambda ch, _: run(ch, sections))


TILT_HZ = 1000.0


def tilt(x, db, loop=False):
    """Brighter (db > 0) or darker (db < 0) around TILT_HZ: a low shelf of -db/2 and a high shelf of +db/2 there (the
    Audio EQ Cookbook's shelves), so the highs end db above the lows; same length."""
    if not db:
        return np.array(x, dtype=np.float64, copy=True)
    sections = [filters.biquad('lowshelf', TILT_HZ, gain_db=-float(db) / 2),
                filters.biquad('highshelf', TILT_HZ, gain_db=float(db) / 2)]
    run = filters.apply_circular if loop else filters.apply
    return _per_channel(x, lambda ch, _: run(ch, sections))


def fade(x, fade_in_s=0.0, fade_out_s=0.0):
    """Raised-cosine fades: the first sample of a fade-in is 0, full level from the sample after it; the last sample of a
    fade-out is 0."""
    frames, mono = _frames(x)
    y = frames.copy()
    n = len(y)
    m_in = min(samples(fade_in_s), n)
    if m_in > 0:
        y[:m_in] *= (np.sin(np.pi / 2 * np.arange(m_in) / m_in) ** 2)[:, None]
    m_out = min(samples(fade_out_s), n)
    if m_out > 0:
        y[n - m_out:] *= (np.sin(np.pi / 2 * (1 - (np.arange(m_out) + 1) / m_out)) ** 2)[:, None]
    return y[:, 0] if mono else y
