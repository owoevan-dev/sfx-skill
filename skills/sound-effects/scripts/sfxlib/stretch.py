"""Time stretching without a change of pitch, for a recording's sustain, ring or texture.

Two methods:

- 'phase-locked' (the default): the phase vocoder with the phase locking of Laroche and Dolson (1999), as reviewed by
  Driedger and Müller ("A Review of Time-Scale Modification of Music Signals", Applied Sciences 6(2), 2016). Frames of 4096 samples at 48 kHz (85 ms: the review gives roughly 100 ms as usual for the
  frequency resolution this method needs) are read from the recording and laid down a quarter frame apart (less when shortening), the frame at output time t read around input time t / factor, so that an event at T
  lands at factor x T. Each spectral peak's phase advances at its own measured frequency, and the bins around a peak
  keep their phase relation to it, which removes most of the 'phasiness' plain phase vocoders leave. Several channels
  share one phase track, each keeping its own phase difference from it, so their image does not drift; the track is
  taken from their sum after turning each channel, per frequency, into phase with the strongest (so channels in
  opposite polarity add instead of cancelling).
- 'smear': random phases for every frame, with long frames (a quarter second): the method of Nasca's Paulstretch as
  the Csound manual describes it (overlap-add with a Hann window, scrambled phase, very large windows). Transients
  are smeared into a smooth, even texture: the method for long drones made from a creak, a hum or a breath, at factors
  of two and more. Frames with unrelated phases add in power, so the sum is scaled accordingly.

A phase vocoder loses the phase relation within a frame and so smears transients (Driedger and Müller 2016).
`keep_attack_ms` leaves the first milliseconds out of the stretching: they play at their own speed, only what follows
them is stretched, and the two meet in a 10 ms crossfade."""
import math

import numpy as np

RATE = 48000
N = 4096
HOP = N // 4
SMEAR_N = 12000                 # a quarter second at 48 kHz: this skill's choice
FADE_S = 0.010
METHODS = ('phase-locked', 'smear')
MIN_FACTOR, MAX_FACTOR = 0.25, 16.0


def _window(n):
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)      # periodic Hann


def _frame(x, centre, n):
    """n samples of x (frames x channels) around `centre`, zeros beyond its ends."""
    out = np.zeros((n, x.shape[1]))
    start = centre - n // 2
    a, b = max(start, 0), min(start + n, len(x))
    if b > a:
        out[a - start:b - start] = x[a:b]
    return out


def _lay(out, norm, y, weight, at):
    """Add a frame into the output at sample `at`, as much of it as falls inside, and its weight into `norm`."""
    a, b = max(at, 0), min(at + len(y), len(out))
    if b > a:
        out[a:b] += y[a - at:b - at]
        norm[a:b] += weight[a - at:b - at]


def _turns(x, w):
    """Per frequency bin and channel, the phase turn that brings the channel into line with the strongest channel over
    the whole recording (bins x channels)."""
    channels = x.shape[1]
    bins = N // 2 + 1
    if channels == 1:
        return np.ones((bins, 1), dtype=complex)
    cross = np.zeros((bins, channels, channels), dtype=complex)
    for centre in range(N // 2, max(len(x), N // 2 + 1), N // 2):
        s = np.fft.rfft(_frame(x, centre, N) * w[:, None], axis=0)
        cross += s[:, :, None] * s[:, None, :].conj()
    power = np.real(np.diagonal(cross, axis1=1, axis2=2))
    lead = power.argmax(axis=1)
    return np.exp(-1j * np.angle(cross[np.arange(bins), :, lead]))


def _phase_locked(x, factor):
    """x (n x channels) stretched by the phase-locked vocoder."""
    w = _window(N)
    hop = max(1, int(round(HOP * min(1.0, factor))))    # analysis frames stay at most a quarter frame apart
    length = int(round(len(x) * factor))
    out = np.zeros((length + 2 * N, x.shape[1]))
    norm = np.zeros(len(out))
    turn = _turns(x, w)
    omega = 2 * np.pi * np.arange(N // 2 + 1) / N
    synth = before = None
    before_a = 0
    for centre in range(-N, length + N + hop, hop):
        a = int(round(centre / factor))
        spec = np.fft.rfft(_frame(x, a, N) * w[:, None], axis=0)
        ref = (spec * turn).sum(axis=1)
        mag, phase = np.abs(ref), np.angle(ref)
        if before is None or not np.any(before):
            synth = phase.copy()                        # the first frame, or the first after silence: its own phases
        else:
            peaks = np.flatnonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:])) + 1
            if len(peaks):
                step = max(1, a - before_a)
                dphi = phase[peaks] - before[peaks] - omega[peaks] * step
                dphi = (dphi + np.pi) % (2 * np.pi) - np.pi
                peak_phase = synth[peaks] + (omega[peaks] + dphi / step) * hop
                edges = np.concatenate([[0], (peaks[:-1] + peaks[1:]) // 2 + 1, [len(mag)]])
                owner = np.repeat(np.arange(len(peaks)), np.diff(edges))
                synth = (peak_phase[owner] + phase - phase[peaks][owner]) % (2 * np.pi)
            else:
                synth = (synth + omega * hop) % (2 * np.pi)
        before, before_a = (phase if np.any(mag) else np.zeros_like(phase)), a
        y = np.fft.irfft(spec * np.exp(1j * (synth - phase))[:, None], N, axis=0) * w[:, None]
        _lay(out, norm, y, w * w, centre - N // 2 + N)
    good = norm > 1e-6
    out[good] /= norm[good][:, None]
    return out[N:N + length]


def _smear(x, factor, seed):
    """x stretched by random-phase frames, every channel with the same phases: each keeps its own level, and a wide
    recording comes out narrower."""
    rng = np.random.default_rng(seed)
    n = SMEAR_N
    w = _window(n)
    level = 1 / math.sqrt(float(np.mean(w * w)))        # the energy the analysis window took
    hop = max(1, int(round(n / 4 * min(1.0, factor))))  # analysis frames stay at most a quarter frame apart
    length = int(round(len(x) * factor))
    out = np.zeros((length + 2 * n, x.shape[1]))
    norm = np.zeros(len(out))
    for centre in range(-n, length + n + hop, hop):
        spec = np.fft.rfft(_frame(x, int(round(centre / factor)), n) * w[:, None], axis=0)
        phases = np.exp(1j * rng.uniform(0, 2 * np.pi, n // 2 + 1))[:, None]
        y = np.fft.irfft(np.abs(spec) * phases, n, axis=0) * w[:, None] * level
        _lay(out, norm, y, w * w, centre - n // 2 + n)
    good = norm > 1e-6
    out[good] /= np.sqrt(norm[good])[:, None]           # unrelated phases add in power
    return out[n:n + length]


def reach(frames, factor, keep_attack_ms=0.0):
    """How many frames of the recording make the first `frames` frames of its stretched form."""
    keep = int(round(float(keep_attack_ms) / 1000 * RATE))
    if keep >= frames:
        return int(frames)
    return keep + int(math.ceil((frames - keep) / float(factor)))


def stretch(x, factor, method='phase-locked', keep_attack_ms=0.0, seed=0):
    """x (frames n x channels, or mono) made `factor` times longer (0.25-16) without a change of pitch. A clip no
    longer than the kept attack is all attack and comes back unchanged."""
    x = np.asarray(x, dtype=np.float64)
    mono = x.ndim == 1
    x2 = x.reshape(len(x), -1)
    factor = float(factor)
    if not MIN_FACTOR <= factor <= MAX_FACTOR:
        raise ValueError(f'a stretch factor must lie in {MIN_FACTOR:g}-{MAX_FACTOR:g}')
    if method not in METHODS:
        raise ValueError(f"unknown stretch method {method!r} (methods: {', '.join(METHODS)})")
    keep = int(round(float(keep_attack_ms) / 1000 * RATE))
    if keep > 0 and keep >= len(x2):
        return x.copy()

    def run(part):
        return _phase_locked(part, factor) if method == 'phase-locked' else _smear(part, factor, seed)
    if keep <= 0:
        y = run(x2)
    else:
        # the attack plays untouched; from its end the recording goes on at its own speed for FADE_S while the
        # stretched rest (which starts at the same point of the recording) fades in over it
        rest = run(x2[keep:])
        fade = min(int(FADE_S * RATE), len(rest), len(x2) - keep)
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / max(fade, 1))
        overlap = x2[keep:keep + fade] * (1 - ramp)[:, None] + rest[:fade] * ramp[:, None]
        y = np.concatenate([x2[:keep], overlap, rest[fade:]])
    return y[:, 0] if mono else y
