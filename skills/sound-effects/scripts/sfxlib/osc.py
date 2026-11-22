"""Oscillators that follow a pitch curve without aliasing.

The classic waveforms are built from their Fourier series (sums of sine partials), and each partial is faded out as it
approaches 20 kHz and left out above, so a sweep never produces components that fold back below the Nyquist frequency.
Two-operator FM (Chowning 1973) is computed at four times the rate and brought back with a low-pass filter."""
import math

import numpy as np

from . import dsp

RATE = 48000
LIMIT_HZ = 20000.0       # no partial above this
FADE_FROM_HZ = 18000.0   # partials fade out (raised cosine) between this and LIMIT_HZ
WAVES = ('sine', 'triangle', 'saw', 'square', 'pulse')


def phase(freq, phase0_deg=0.0, rate=RATE):
    """Instantaneous phase (radians) for a frequency per sample: starts at phase0_deg, advances by 2 pi f / rate."""
    freq = np.asarray(freq, dtype=np.float64)
    step = 2 * np.pi * freq / rate
    return math.radians(phase0_deg) + np.concatenate([[0.0], np.cumsum(step[:-1])])


def partial_gain(fk):
    """Gain of partials at frequencies fk: 1 below FADE_FROM_HZ, a raised-cosine fade to 0 at LIMIT_HZ, 0 above."""
    fk = np.asarray(fk, dtype=np.float64)
    x = np.clip((fk - FADE_FROM_HZ) / (LIMIT_HZ - FADE_FROM_HZ), 0.0, 1.0)
    return 0.5 * (1 + np.cos(np.pi * x))


def _series(phi, freq, coefficient, step=1, shift=0.0):
    """Sum over partials k = 1, 1 + step, ... of coefficient(k) * gain(k f) * sin(k (phi - shift))."""
    out = np.zeros(len(phi))
    fmin = float(np.min(freq)) if len(freq) else 0.0
    if fmin <= 0:
        fmin = 1.0
    k = 1
    while k * fmin < LIMIT_HZ:
        c = coefficient(k)
        if c:
            g = partial_gain(k * freq)
            if np.any(g > 0):
                out += c * g * np.sin(k * (phi - shift))
        k += step
    return out


def wave(kind, freq, duty=0.5, phase0_deg=0.0):
    """One of WAVES at the given frequency per sample, peak about 1 (band-limited edges overshoot by the Gibbs
    amount). Pulse widths other than 0.5 are scaled so that the longer side sits at full scale; the pulse has no DC."""
    freq = np.asarray(freq, dtype=np.float64)
    phi = phase(freq, phase0_deg)
    if kind == 'sine':
        return np.sin(phi) * partial_gain(freq)
    if kind == 'saw':
        return (2 / np.pi) * _series(phi, freq, lambda k: (-1) ** (k + 1) / k)
    if kind == 'square':
        return (4 / np.pi) * _series(phi, freq, lambda k: 1 / k, step=2)
    if kind == 'triangle':
        return (8 / np.pi ** 2) * _series(phi, freq, lambda k: (-1) ** ((k - 1) // 2) / k ** 2, step=2)
    if kind == 'pulse':
        d = float(duty)
        if not 0.01 <= d <= 0.99:
            raise ValueError(f'pulse duty must be between 0.01 and 0.99, not {duty}')
        s = _series(phi, freq, lambda k: 1 / k) - _series(phi, freq, lambda k: 1 / k, shift=2 * np.pi * d)
        return (2 / np.pi) * s / (2 * max(d, 1 - d))
    raise ValueError(f"unknown wave {kind!r}; use one of {', '.join(WAVES)}")


def _to_rate(values, n, factor):
    """Per-sample values (array or number) linearly interpolated to factor * n samples."""
    if np.ndim(values) == 0:
        return np.full(n * factor, float(values))
    values = np.asarray(values, dtype=np.float64)
    return np.interp(np.arange(n * factor) / factor, np.arange(len(values)), values)


def fm(freq, ratio, index, phase0_deg=0.0, loop=False):
    """Two-operator FM: sin(carrier phase + index * sin(ratio * carrier phase)), the modulator locked to the carrier.
    `index` is a number or a value per sample. Computed at four times the rate, then low-passed back.

    loop=True: freq and index are one period of a loop that holds whole numbers of carrier and modulator cycles
    (`whole_cycles`); the period is computed with its own end before it and its start after it, so the decimation
    filter sees the loop as it plays and the output's period joins its own start."""
    freq = np.asarray(freq, dtype=np.float64)
    n = len(freq)
    pad = (dsp.TAPS - 1) // (2 * dsp.FACTOR) + 2      # base samples the decimation filter reaches past the end
    if loop:
        idx = np.arange(-pad, n + pad)
        f_ext = np.take(freq, idx, mode='wrap')
        i_ext = index if np.ndim(index) == 0 else np.take(np.asarray(index, float), idx, mode='wrap')
        size, first = n + 2 * pad, pad
    else:
        f_ext = np.concatenate([freq, np.full(pad, freq[-1] if n else 0.0)])
        i_ext = index if np.ndim(index) == 0 else np.concatenate([np.asarray(index, float), np.full(pad, index[-1])])
        size, first = n + pad, 0
    f_up = _to_rate(f_ext, size, dsp.FACTOR)
    i_up = _to_rate(i_ext, size, dsp.FACTOR)
    phi_c = phase(f_up, 0.0, RATE * dsp.FACTOR)
    y = np.sin(math.radians(phase0_deg) + phi_c + i_up * np.sin(float(ratio) * phi_c))
    return dsp.downsample(y)[first:first + n]


def whole_cycles(freq):
    """A frequency per sample over one period of a loop, scaled so that the period holds a whole number of cycles (at
    least one), and the scale factor. The phase then comes back to its start at the end of the period, so the loop has
    no jump; over a loop of T seconds the frequency moves by less than 1 / (2T) Hz."""
    freq = np.asarray(freq, dtype=np.float64)
    cycles = float(np.sum(freq)) / RATE
    if cycles <= 0:
        return freq, 1.0
    factor = max(1.0, round(cycles)) / cycles
    return freq * factor, factor


def whole_rate(rate_hz, n):
    """A rate (Hz) rounded to a whole number of cycles (at least one) over n samples."""
    seconds = n / RATE
    return max(1.0, round(float(rate_hz) * seconds)) / seconds


def tremolo(n, rate_hz, depth):
    """A gain over n samples, 1 at its crests and 1 - depth in its troughs, at rate_hz."""
    t = np.arange(n) / RATE
    return 1.0 - float(depth) * 0.5 * (1.0 - np.cos(2 * np.pi * float(rate_hz) * t))


def vibrato(freq, rate_hz, depth_cents):
    """freq with a sine vibrato of depth_cents (each way) at rate_hz."""
    freq = np.asarray(freq, dtype=np.float64)
    t = np.arange(len(freq)) / RATE
    return freq * 2 ** (float(depth_cents) / 1200 * np.sin(2 * np.pi * float(rate_hz) * t))
