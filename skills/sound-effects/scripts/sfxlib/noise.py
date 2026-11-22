"""Seeded noise: white, pink (power per hertz falling 3 dB per octave) and brown (6 dB per octave), shaped in the
frequency domain from 20 Hz up, so they have no DC and repeat seamlessly over their own length. Every noise comes at an
RMS of RMS (about -12 dBFS), so its peaks stay near full scale."""
import numpy as np

RATE = 48000
RMS = 0.25
COLORS = ('white', 'pink', 'brown')
LOW_HZ = 20.0


def make(color, n, rng):
    """n samples of noise of the given colour from the numpy Generator rng."""
    if color not in COLORS:
        raise ValueError(f"unknown noise colour {color!r}; use one of {', '.join(COLORS)}")
    x = rng.standard_normal(n)
    if color != 'white' and n > 1:
        spectrum = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        weight = np.zeros(len(freqs))
        keep = freqs >= LOW_HZ
        weight[keep] = freqs[keep] ** (-0.5 if color == 'pink' else -1.0)
        x = np.fft.irfft(spectrum * weight, n)
    rms = float(np.sqrt(np.mean(x ** 2))) if n else 0.0
    return x * (RMS / rms) if rms > 0 else x
