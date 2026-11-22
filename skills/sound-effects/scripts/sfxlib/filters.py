"""Two-pole filters: low-pass, high-pass, band-pass (0 dB at its centre) and notch.

A cut-off that moves runs through a state-variable filter integrated with the trapezoidal rule, which stays stable
however fast the cut-off moves (Zavalishin, The Art of VA Filter Design, the zero-delay-feedback state-variable
filter; written here in the form with two integrator states). A fixed cut-off uses the same transfer function, the
bilinear transform of the analog two-pole prototypes pre-warped at the cut-off (the forms of R. Bristow-Johnson's Audio
EQ Cookbook), applied in the frequency domain; the two paths give the same output."""
import math

import numpy as np

from .loudness import fast_length

RATE = 48000
KINDS = ('lowpass', 'highpass', 'bandpass', 'notch')
MIN_HZ, MAX_HZ = 10.0, 0.45 * RATE


def svf(x, cutoff, q, kind):
    """Filter x with a cut-off per sample (Hz) and resonance q."""
    if kind not in KINDS:
        raise ValueError(f"unknown filter {kind!r}; use one of {', '.join(KINDS)}")
    if q <= 0:
        raise ValueError(f'filter q must be above 0, not {q}')
    x = np.asarray(x, dtype=np.float64)
    g = np.tan(np.pi * np.clip(np.asarray(cutoff, dtype=np.float64), MIN_HZ, MAX_HZ) / RATE)
    k = 1.0 / q
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    xs, a1s, a2s, a3s = x.tolist(), a1.tolist(), a2.tolist(), a3.tolist()
    ic1 = ic2 = 0.0
    low, band = [0.0] * len(xs), [0.0] * len(xs)
    for i, v0 in enumerate(xs):
        v3 = v0 - ic2
        v1 = a1s[i] * ic1 + a2s[i] * v3
        v2 = ic2 + a2s[i] * ic1 + a3s[i] * v3
        ic1 = 2.0 * v1 - ic1
        ic2 = 2.0 * v2 - ic2
        low[i] = v2
        band[i] = v1
    low, band = np.array(low), np.array(band)
    if kind == 'lowpass':
        return low
    if kind == 'bandpass':
        return k * band
    if kind == 'highpass':
        return x - k * band - low
    return x - k * band


def biquad(kind, f0, q=0.7071, gain_db=0.0):
    """(b, a) of a two-pole section: KINDS plus 'peak' (a bell of gain_db) and 'lowshelf' / 'highshelf' (shelves of
    gain_db, slope 1), as the Audio EQ Cookbook gives them."""
    w0 = 2 * math.pi * min(max(f0, MIN_HZ), MAX_HZ) / RATE
    cw, alpha = math.cos(w0), math.sin(w0) / (2 * q)
    if kind == 'lowpass':
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif kind == 'highpass':
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif kind == 'bandpass':
        b = (alpha, 0.0, -alpha)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif kind == 'notch':
        b = (1.0, -2 * cw, 1.0)
        a = (1 + alpha, -2 * cw, 1 - alpha)
    elif kind == 'peak':
        amp = 10 ** (gain_db / 40)
        b = (1 + alpha * amp, -2 * cw, 1 - alpha * amp)
        a = (1 + alpha / amp, -2 * cw, 1 - alpha / amp)
    elif kind in ('lowshelf', 'highshelf'):
        amp = 10 ** (gain_db / 40)
        al = math.sin(w0) / math.sqrt(2)                 # shelf slope 1
        sq = 2 * math.sqrt(amp) * al
        if kind == 'lowshelf':
            b = (amp * ((amp + 1) - (amp - 1) * cw + sq), 2 * amp * ((amp - 1) - (amp + 1) * cw),
                 amp * ((amp + 1) - (amp - 1) * cw - sq))
            a = ((amp + 1) + (amp - 1) * cw + sq, -2 * ((amp - 1) + (amp + 1) * cw), (amp + 1) + (amp - 1) * cw - sq)
        else:
            b = (amp * ((amp + 1) + (amp - 1) * cw + sq), -2 * amp * ((amp - 1) + (amp + 1) * cw),
                 amp * ((amp + 1) + (amp - 1) * cw - sq))
            a = ((amp + 1) - (amp - 1) * cw + sq, 2 * ((amp - 1) - (amp + 1) * cw), (amp + 1) - (amp - 1) * cw - sq)
    else:
        raise ValueError(f"unknown filter {kind!r}")
    return b, a


def first_order(kind, fc):
    """(b, a) of a first-order 'highpass' or 'lowpass' section, the bilinear transform pre-warped at fc."""
    k = math.tan(math.pi * min(max(fc, 1.0), MAX_HZ) / RATE)
    a1 = (k - 1) / (k + 1)
    if kind == 'highpass':
        return (1 / (1 + k), -1 / (1 + k), 0.0), (1.0, a1, 0.0)
    if kind == 'lowpass':
        return (k / (1 + k), k / (1 + k), 0.0), (1.0, a1, 0.0)
    raise ValueError(f'unknown first-order filter {kind!r}')


def _decay_samples(a):
    """Samples for a section's response to fall by about 180 dB (its poles' radius), between 256 and 5 s. A first-order
    section (a[2] = 0) has its one pole at -a[1] / a[0]."""
    if a[0] and a[2] == 0:
        radius = abs(a[1] / a[0])
        if radius <= 0:
            return 256
    else:
        radius = math.sqrt(abs(a[2] / a[0])) if a[0] else 0.0
    if radius <= 0 or radius >= 1:
        return 5 * RATE
    return int(min(5 * RATE, max(256, math.log(1e-9) / math.log(radius))))


def apply(x, sections, length=None):
    """Run x (1-D) through the sections [(b, a), ...] in the frequency domain, zero-padded so the responses finish;
    the first `length` samples (default len(x))."""
    x = np.asarray(x, dtype=np.float64)
    length = len(x) if length is None else length
    tail = sum(_decay_samples(a) for _, a in sections)
    size = fast_length(max(len(x), length) + tail)
    h = response_at(sections, size)
    return causal(x, np.fft.irfft(np.fft.rfft(x, size) * h, size)[:length])


def causal(x, y):
    """y with every sample before x's first non-zero sample set to 0: the filters here are causal, and the rest of
    their responses that the frequency-domain product wraps round to the start would otherwise leave rounding noise
    there (around 1e-9), so a sound that begins late would no longer begin on its exact sample."""
    nz = np.flatnonzero(np.asarray(x) != 0)
    first = int(nz[0]) if len(nz) else len(y)
    y[:min(first, len(y))] = 0.0
    return y


def filter(x, kind, cutoff, q=0.7071, loop=False):
    """Filter x: a fixed cut-off (a number, or a curve that does not move) through `apply`, a moving one through
    `svf`. With loop=True, x (and a moving cut-off) are one period of a loop and the output is the loop's periodic
    steady state (`apply_circular`, `svf_circular`)."""
    cutoff_arr = np.asarray(cutoff, dtype=np.float64)
    if cutoff_arr.ndim == 0 or np.all(cutoff_arr == cutoff_arr.flat[0]):
        if kind not in KINDS:
            raise ValueError(f"unknown filter {kind!r}; use one of {', '.join(KINDS)}")
        sections = [biquad(kind, float(cutoff_arr.flat[0]), q)]
        return apply_circular(x, sections) if loop else apply(x, sections)
    if loop:
        return svf_circular(x, cutoff_arr, q, kind)
    return svf(x, cutoff_arr, q, kind)


# ---- loops: one period of a periodic signal in, one period of the periodic steady state out ----------------------

def response_at(sections, n):
    """The sections' transfer function at the n-point DFT's frequencies (rfft order)."""
    z = np.exp(-2j * np.pi * np.fft.rfftfreq(n))
    h = np.ones(len(z), dtype=complex)
    for b, a in sections:
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return h


def apply_circular(x, sections):
    """x taken as one period of a signal that repeats for ever, through the sections: the output's period, exactly.
    The transfer function sampled at the n frequencies of the DFT is the response folded onto one period, so the part
    of each response that runs past the end comes back in at the start, as it does when a loop plays on."""
    x = np.asarray(x, dtype=np.float64)
    return np.fft.irfft(np.fft.rfft(x) * response_at(sections, len(x)), len(x))


SETTLE_NEPERS = 20.7       # e^-20.7 is about 1e-9 (-180 dB): the start-up of a run-in is forgotten by then


def settle_samples(cutoff_hz, q):
    """Samples for the state-variable filter's start-up to fall 180 dB at the lowest cut-off: its slowest pole decays
    at 2 pi f (k/2 - sqrt(k^2/4 - 1)) per second with k = 1/q (pi f / q when q >= 0.5)."""
    f = min(max(float(cutoff_hz), MIN_HZ), MAX_HZ)
    k = 1.0 / q
    rate = 2 * math.pi * f * (k / 2 - math.sqrt(max(k * k / 4 - 1, 0.0)))
    return int(math.ceil(SETTLE_NEPERS / rate * RATE)) + 1


def svf_circular(x, cutoff, q, kind):
    """x and its cut-off per sample as one period of a loop through `svf`: the filter first runs over the end of the
    period, long enough for its start from rest to be forgotten (at most one whole period), then over the period,
    whose output is kept. Its state on entering the period is then the state the period's own end leaves."""
    x = np.asarray(x, dtype=np.float64)
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=np.float64), x.shape)
    n = len(x)
    pre = min(n, settle_samples(float(np.min(cutoff)), q))
    xe = np.concatenate([x[n - pre:], x])
    ce = np.concatenate([cutoff[n - pre:], cutoff])
    return svf(xe, ce, q, kind)[pre:]
