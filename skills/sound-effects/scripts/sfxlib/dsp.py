"""Small signal helpers: stable seeds, FFT convolution, a low-pass FIR and four-times over- and down-sampling with the
filters' delay taken back, so that a sample stays on its sample."""
import functools
import hashlib

import numpy as np

from .loudness import fast_length

RATE = 48000


def seed_for(*parts):
    """A 64-bit seed from any parts (project seed, sound id, variant, purpose): the same parts give the same seed on
    every machine and run (Python's own hash() changes between runs)."""
    digest = hashlib.sha256(':'.join(str(p) for p in parts).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'little')


def fft_convolve(x, h):
    """Full linear convolution of 1-D x and h."""
    n = len(x) + len(h) - 1
    size = fast_length(n)
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[:n]


@functools.lru_cache(maxsize=8)
def fir_lowpass(cutoff_hz, rate, taps, beta=8.0):
    """Kaiser-windowed sinc low-pass with unit gain at 0 Hz; `taps` odd."""
    m = np.arange(taps) - (taps - 1) / 2
    w = 2 * cutoff_hz / rate
    h = w * np.sinc(w * m) * np.kaiser(taps, beta)
    h = h / np.sum(h)
    h.setflags(write=False)
    return h


FACTOR = 4
TAPS = 257            # (TAPS - 1) is a multiple of FACTOR, so the delays of two filters come back to whole samples
CUTOFF_HZ = 21000.0   # passes everything up to 20 kHz and stops before 24 kHz, the Nyquist frequency at 48 kHz


def upsample(x, factor=FACTOR):
    """x at `factor` times the rate: zero-stuffed and low-passed, delay taken back (same start, len(x) * factor)."""
    h = fir_lowpass(CUTOFF_HZ, RATE * factor, TAPS) * factor
    up = np.zeros(len(x) * factor)
    up[::factor] = x
    delay = (TAPS - 1) // 2
    return fft_convolve(up, h)[delay:delay + len(up)]


def downsample(y, factor=FACTOR):
    """y (at `factor` times the rate) back at the base rate: low-passed, delay taken back, every factor-th sample."""
    h = fir_lowpass(CUTOFF_HZ, RATE * factor, TAPS)
    delay = (TAPS - 1) // 2
    return fft_convolve(y, h)[delay:delay + len(y)][::factor]
