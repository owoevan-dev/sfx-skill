"""Chip voices after the NES's sound hardware, as the NESdev Wiki documents it (APU Pulse, APU Triangle, APU Noise,
APU Envelope, APU Frame Counter, APU Mixer):

- pulse waves with duty cycles of 12.5, 25, 50 and 75 %, on the pitches the 11-bit timer allows,
  f = f_cpu / (16 (t + 1));
- a 32-step triangle (15 down to 0, then 0 up to 15), f = f_cpu / (32 (t + 1));
- 1-bit noise from a 15-bit shift register whose feedback is bit 0 xor bit 1 (long mode, 32767 steps) or bit 0 xor
  bit 6 (short mode, 93 or 31 steps), clocked at one of 16 rates, f_cpu / period;
- volume in 16 practically linear steps, and parameters that change in steps (envelopes and sweeps at about 240 and
  120 Hz, the frame interrupt at about 60 Hz);
- the console's output filters: first-order high-pass at 90 and at 440 Hz and low-pass at 14 kHz (NES), or a first-
  order high-pass at 37 Hz (Famicom).

The waveforms are band-limited (built from their Fourier series, as osc.py builds its waves), so they sound as the
console's analogue output does rather than with the folding of a naive digital copy; the noise is area-sampled."""
import functools
import math

import numpy as np

from . import curves, filters, osc

RATE = 48000
F_CPU = 1789773.0                      # NTSC CPU clock (Hz)
DUTIES = (0.125, 0.25, 0.5, 0.75)
NOISE_PERIODS = (4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068)   # NTSC, CPU cycles
NOISE_RATES = tuple(F_CPU / p for p in NOISE_PERIODS)
TRIANGLE_STEPS = tuple(range(15, -1, -1)) + tuple(range(16))
WAVES = ('pulse', 'triangle', 'noise')
MODES = ('long', 'short')
CONSOLES = ('nes', 'famicom')


def snap_duty(duty):
    """The nearest of the four duty cycles."""
    return min(DUTIES, key=lambda d: abs(d - float(duty)))


def quantize_pitch(freq, wave):
    """Frequencies moved to the nearest the hardware can make: a pulse or triangle timer period, or a noise rate."""
    f = np.asarray(freq, dtype=np.float64)
    if wave == 'pulse':
        t = np.clip(np.round(F_CPU / (16 * np.maximum(f, 1.0)) - 1), 8, 2047)
        out = F_CPU / (16 * (t + 1))
    elif wave == 'triangle':
        t = np.clip(np.round(F_CPU / (32 * np.maximum(f, 1.0)) - 1), 0, 2047)
        out = F_CPU / (32 * (t + 1))
    elif wave == 'noise':
        rates = np.array(NOISE_RATES)
        idx = np.argmin(np.abs(np.log(np.maximum(f, 1.0))[..., None] - np.log(rates)), axis=-1)
        out = rates[idx]
    else:
        raise ValueError(f"unknown chip wave {wave!r}; use one of {', '.join(WAVES)}")
    return float(out) if np.ndim(freq) == 0 else out


@functools.lru_cache(maxsize=2)
def lfsr_sequence(mode):
    """The output bits (bit 0 before each shift) of one full period of the noise shift register from its power-up
    value 1, as a read-only array."""
    tap = 6 if mode == 'short' else 1
    if mode not in MODES:
        raise ValueError(f"unknown noise mode {mode!r}; use long or short")
    state, bits = 1, []
    while True:
        bits.append(state & 1)
        feedback = (state ^ (state >> tap)) & 1
        state = (state >> 1) | (feedback << 14)
        if state == 1:
            break
    out = np.array(bits, dtype=np.int8)
    out.setflags(write=False)
    return out


@functools.lru_cache(maxsize=1)
def _triangle_coefficients(max_k=4096):
    """Complex Fourier coefficients of the 32-step triangle (each step held for 1/32 of the period), DC removed."""
    steps = (np.array(TRIANGLE_STEPS, dtype=np.float64) - 7.5) / 7.5
    n = len(steps)
    k = np.arange(1, max_k + 1)
    dft = np.array([np.sum(steps * np.exp(-2j * np.pi * kk * np.arange(n) / n)) for kk in range(1, n + 1)]) / n
    hold = np.sinc(k / n) * np.exp(-1j * np.pi * k / n)
    return dft[(k - 1) % n] * hold


def _triangle(freq, phase0_deg=0.0):
    phi = osc.phase(freq, phase0_deg)
    fmin = max(float(np.min(freq)), 1.0)
    coeff = _triangle_coefficients()
    out = np.zeros(len(phi))
    for k in range(1, len(coeff) + 1):
        if k * fmin >= osc.LIMIT_HZ:
            break
        c = coeff[k - 1]
        if abs(c) < 1e-9:
            continue
        g = osc.partial_gain(k * freq)
        if np.any(g > 0):
            out += 2 * abs(c) * g * np.cos(k * phi + np.angle(c))
    return out


def _noise(rate, mode, rng):
    """Area-sampled shift-register noise at a clock rate per sample (Hz), starting at a random step."""
    seq = 1.0 - 2.0 * lfsr_sequence(mode)          # sounding (+1) while bit 0 is clear, as the mixer gates it
    seq = seq - seq.mean()
    length = len(seq)
    cum = np.concatenate([[0.0], np.cumsum(seq)])
    total = cum[-1]
    step = np.asarray(rate, dtype=np.float64) / RATE
    start = float(rng.integers(0, length)) if rng is not None else 0.0
    edges = start + np.concatenate([[0.0], np.cumsum(step)])

    def integral(x):
        whole = np.floor(x / length)
        rest = x - whole * length
        return whole * total + np.interp(rest, np.arange(length + 1), cum)
    area = integral(edges[1:]) - integral(edges[:-1])
    return area / np.maximum(step, 1e-12)


def wave(kind, freq, duty=0.5, quantize=True, rng=None, mode='long', phase0_deg=0.0):
    """A chip voice at a frequency per sample (for noise: the shift register's clock rate per sample)."""
    freq = np.asarray(freq, dtype=np.float64)
    if quantize:
        freq = quantize_pitch(freq, kind)
    if kind == 'pulse':
        return osc.wave('pulse', freq, duty=snap_duty(duty), phase0_deg=phase0_deg)
    if kind == 'triangle':
        return _triangle(freq, phase0_deg)
    if kind == 'noise':
        return _noise(freq, mode, rng)
    raise ValueError(f"unknown chip wave {kind!r}; use one of {', '.join(WAVES)}")


def stepped(spec, n, step_hz, default='lin', opening=False):
    """A curve that changes only at the start of each step of 1/step_hz seconds (held between); smooth at 0. `opening`,
    for an envelope: the first step holds the highest value the curve reaches in it, so that a quick attack does not begin
    with a whole silent step."""
    full = curves.evaluate(spec, n, default=default)
    if not step_hz or step_hz <= 0:
        return full
    frame = RATE / float(step_hz)
    idx = np.minimum((np.floor(np.arange(n) / frame) * frame).astype(np.int64), n - 1)
    out = full[idx]
    if opening:
        first = max(1, min(n, int(math.ceil(frame))))
        out[:first] = np.max(full[:first])
    return out


def quantize_levels(values, levels=16):
    """Volumes rounded to `levels` evenly spaced steps between 0 and 1."""
    steps = max(int(levels), 2) - 1
    return np.round(np.clip(values, 0.0, 1.0) * steps) / steps


def console(x, model, loop=False):
    """The console's output filters (first-order sections, bilinear transform pre-warped at each corner); a loop's
    period through them circularly (filters.apply_circular)."""
    sections = {'nes': [('highpass', 90.0), ('highpass', 440.0), ('lowpass', 14000.0)],
                'famicom': [('highpass', 37.0)]}.get(model)
    if sections is None:
        raise ValueError(f"unknown console {model!r}; use one of {', '.join(CONSOLES)}")
    coeffs = [filters.first_order(kind, fc) for kind, fc in sections]
    run = filters.apply_circular if loop else filters.apply
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        return run(x, coeffs)
    return np.stack([run(x[:, c], coeffs) for c in range(x.shape[1])], axis=1)
