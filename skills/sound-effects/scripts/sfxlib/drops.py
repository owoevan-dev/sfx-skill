"""Rain, drips and streams as a population of bubbles, after van den Doel (2004), "Physically-based models for liquid
sounds", Proceedings of ICAD 04.

A bubble of radius r (metres) rings at the Minnaert frequency f = 3 / r and is damped by d = 0.13 / r + 0.0072 r^-1.5
(valid above 0.15 mm); its sound is a decaying sinusoid, sin(2 pi f t) e^(-d t). A bubble formed near the surface rises
in pitch, f0 (1 + xi d t), with xi about 0.1 for drops; here the phase is the integral of that frequency.

The population (the paper's section 5): 50 radii spaced evenly in log between r_min and r_max (the paper finds 50
values aurally satisfactory), each created as its own Poisson process. The radii share the total rate in proportion to
r^-gamma (the paper: gamma from 0 to 3 sounds like streams, above about 5 very much like rain). Each bubble's gain is
D r^alpha (alpha = 1.5 predicted; smaller sounds closer, larger more distant) with a depth factor D = rnd^beta (beta 10
sounds best where it is audible at all, in sparse sounds). Only bubbles with D above rise_cutoff rise in pitch (about
0.9 helps sparse sounds). Total rates: up to 1000 per second, dripping to light rain; up to 10 000, dense with single
drops still heard; up to 100 000, blended, heavy rain or waterfalls; more makes no audible difference.

The gains here are relative to the largest radius, and the whole layer comes at an RMS of RMS (as noise does), so
layers mix predictably; the level step sets the file's level. In a stereo sound each bubble gets its own place, evenly
between -spread and +spread (constant-power pan)."""
import math

import numpy as np

from .loudness import fast_length
from .osc import partial_gain

RATE = 48000
RADII = 50
RMS = 0.25
TAIL_DB = -80.0          # a bubble's ring is computed until it has fallen this far ...
TAPER = 0.1              # ... the last tenth of it tapered to 0 with a raised cosine
DIRECT_FACTOR = 0.5      # scatter rings directly while (bubbles x ring length) < this x n log2 n, else use the FFT
R_MIN_M, R_MAX_M = 0.0002, 0.05   # the radii allowed: 0.2 mm (15 kHz) to 5 cm (60 Hz), the paper's simulator range


def frequency(r):
    """Minnaert frequency (Hz) of a bubble of radius r metres, f = 3 / r."""
    return 3.0 / r


def damping(r):
    """Damping (1/s) of a bubble of radius r metres, d = 0.13 / r + 0.0072 r^-1.5."""
    return 0.13 / r + 0.0072 * r ** -1.5


def ring(r, rise=0.0):
    """One bubble's sound, peak near 1: a sinusoid at f0 (1 + rise d t) decaying as e^(-d t), until TAIL_DB, the end
    tapered; partials are faded out as the rising pitch nears 20 kHz (osc.partial_gain)."""
    f0, d = frequency(r), damping(r)
    n = max(2, int(math.ceil(math.log(10 ** (-TAIL_DB / 20)) / d * RATE)))
    t = np.arange(n) / RATE
    sigma = float(rise) * d
    phase = 2 * np.pi * f0 * (t + 0.5 * sigma * t * t)
    out = np.sin(phase) * np.exp(-d * t) * partial_gain(f0 * (1 + sigma * t))
    m = max(1, int(TAPER * n))
    out[n - m:] *= 0.5 * (1 + np.cos(np.pi * (np.arange(m) + 1) / m))
    return out


def radii(r_min_mm, r_max_mm):
    """The population's RADII radii in metres, evenly spaced in log from r_min to r_max."""
    lo, hi = float(r_min_mm) / 1000, float(r_max_mm) / 1000
    if not R_MIN_M <= lo <= hi <= R_MAX_M:
        raise ValueError(f'bubble radii must lie within 0.2 to 50 mm with r_min <= r_max, not {r_min_mm}..{r_max_mm}')
    return lo * (hi / lo) ** (np.arange(RADII) / (RADII - 1))


def render(n, rng, rate, r_min_mm=0.2, r_max_mm=3.0, gamma=5.0, alpha=1.5, beta=10.0, rise=0.1, rise_cutoff=0.9,
           channels=1, spread=0.0, loop=False):
    """n frames (n x channels) of a bubble population drawn from the numpy Generator rng.

    loop=False: bubbles start anywhere in the n frames and their rings are cut at the end (a layer's envelope closes
    it). loop=True: the n frames are one period of a loop; a ring that runs past the end continues from the start, as
    it does when the loop plays on, so the period joins its own start."""
    r = radii(r_min_mm, r_max_mm)
    weights = r ** -float(gamma)
    rates = float(rate) * weights / weights.sum()
    seconds = n / RATE
    longest = len(ring(r[-1]))
    size = n if loop else n + longest
    fft_size = n if loop else fast_length(size)
    direct = np.zeros((channels, size))
    spectrum = np.zeros((channels, fft_size // 2 + 1), dtype=complex)
    threshold = DIRECT_FACTOR * fft_size * math.log2(max(fft_size, 2))
    for k, radius in enumerate(r):
        count = int(rng.poisson(rates[k] * seconds))
        start = rng.integers(0, n, count)
        depth = rng.random(count) ** float(beta)
        place = rng.uniform(-1.0, 1.0, count) * float(spread)
        if count == 0:
            continue
        gain = depth * (radius / r[-1]) ** float(alpha)
        if channels == 1:
            pans = [np.ones(count)]
        else:
            theta = (place + 1) * math.pi / 4
            pans = [np.cos(theta), np.sin(theta)]
            centred = place == 0                    # one gain: the sine and cosine of 45 degrees can differ in the last bit
            pans[0][centred] = pans[1][centred] = math.sqrt(0.5)
        rising = depth > float(rise_cutoff) if rise > 0 else np.zeros(count, dtype=bool)
        for kind, chosen in ((0.0, ~rising), (float(rise), rising)):
            if not chosen.any():
                continue
            sound = ring(radius, kind)
            where, level = start[chosen], gain[chosen]
            if chosen.sum() * len(sound) < threshold:
                _scatter(direct, sound, where, level, [p[chosen] for p in pans], size, loop)
            else:
                if loop and len(sound) > n:
                    sound = np.bincount(np.arange(len(sound)) % n, weights=sound, minlength=n)
                response = np.fft.rfft(sound, fft_size)
                for c, p in enumerate(pans):
                    train = np.bincount(where, weights=level * p[chosen], minlength=fft_size)
                    spectrum[c] += np.fft.rfft(train) * response
    out = direct[:, :n] + np.fft.irfft(spectrum, fft_size, axis=1)[:, :n]
    power = float(np.mean(np.sum(out ** 2, axis=0)))
    if power > 0:
        out *= RMS / math.sqrt(power)
    return out.T


def _scatter(acc, sound, where, level, pans, size, loop, chunk=4_000_000):
    """Add level x pan x sound at each start into acc (channels x size), wrapping round when loop, in chunks."""
    length = len(sound)
    per = max(1, chunk // length)
    offsets = np.arange(length)
    for i in range(0, len(where), per):
        idx = where[i:i + per, None] + offsets[None, :]
        if loop:
            idx %= size
        flat = idx.ravel()
        for c, p in enumerate(pans):
            vals = ((level[i:i + per] * p[i:i + per])[:, None] * sound[None, :]).ravel()
            acc[c] += np.bincount(flat, weights=vals, minlength=size)
