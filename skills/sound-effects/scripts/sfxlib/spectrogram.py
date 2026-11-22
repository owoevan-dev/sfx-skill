"""A spectrogram picture of a sound, for the agent to look at: it cannot hear, but it can see where energy lies.

The picture (PNG, written with the standard library only) shows, from the top: the level envelope (1 ms RMS, the
loudest millisecond marked), then the spectrogram, the short-time Fourier transform (Hann window of 2048 samples,
a hop of one column's time, all channels' power together), on a logarithmic frequency axis from 20 Hz to 20 kHz, each
row showing the strongest bin of its band, the level in dB from 80 dB under the loudest cell (black) to the loudest
(white-yellow; silence stays black). Faint lines mark 100 Hz, 1 kHz and 10 kHz across, and every
100 ms down (every second brighter). The colours run black, violet, red, orange, yellow: this skill's own ramp.

Reading it: a click is a thin vertical line; a strike a bright column at its onset fading to the right; a tone or a
ring a horizontal line; noise a broad smear; weight shows at the bottom (under 150 Hz), air at the top."""
import struct
import zlib
from pathlib import Path

import numpy as np

from . import artifacts

RATE = 48000
N = 2048
HEIGHT = 256
ENVELOPE_H = 48
GAP = 2
F_LO, F_HI = 20.0, 20000.0
RANGE_DB = 80.0
RAMP = ((0.0, (0, 0, 0)), (0.25, (60, 15, 110)), (0.5, (180, 40, 70)), (0.75, (245, 140, 30)), (1.0, (255, 250, 200)))


def _colour(v):
    """v in 0-1 to RGB along RAMP (arrays)."""
    out = np.zeros(v.shape + (3,))
    for (a, ca), (b, cb) in zip(RAMP[:-1], RAMP[1:]):
        m = (v >= a) & (v <= b)
        t = ((v - a) / (b - a))[m][:, None]
        out[m] = np.array(ca) * (1 - t) + np.array(cb) * t
    return out


def _png(path, rgb):
    """Write rgb (h x w x 3, uint8) as a PNG (RFC 2083: IHDR, one IDAT, IEND; filter 0 on every row)."""
    h, w, _ = rgb.shape
    raw = b''.join(b'\x00' + rgb[r].tobytes() for r in range(h))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    Path(path).write_bytes(data)


def picture(x, rate=RATE, width=None):
    """The picture as an h x w x 3 uint8 array, and its scale {'seconds', 'column_ms', 'peak_s'}."""
    x = np.asarray(x, dtype=np.float64).reshape(len(x), -1)
    seconds = len(x) / rate
    if width is None:
        width = int(min(1200, max(240, round(seconds * 100))))      # 10 ms a column, within 240-1200 columns
    hop = max(1, len(x) // width)
    pad = np.concatenate([np.zeros((N // 2, x.shape[1])), x, np.zeros((N, x.shape[1]))])
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)
    cols = []
    for c in range(width):
        seg = pad[c * hop:c * hop + N]
        if len(seg) < N:
            seg = np.concatenate([seg, np.zeros((N - len(seg), x.shape[1]))])
        cols.append(np.sum(np.abs(np.fft.rfft(seg * win[:, None], axis=0)) ** 2, axis=1))
    power = np.array(cols).T                                         # bins x columns
    freqs = np.fft.rfftfreq(N, 1 / rate)
    rows = F_LO * (F_HI / F_LO) ** (np.arange(HEIGHT)[::-1] / (HEIGHT - 1))
    half = (F_HI / F_LO) ** (0.5 / (HEIGHT - 1))                     # half a row, as a ratio of frequency
    lo = np.clip(np.searchsorted(freqs, rows / half), 1, len(freqs) - 1)
    hi = np.maximum(np.searchsorted(freqs, rows * half), lo + 1)
    cells = np.array([power[a:b].max(axis=0) for a, b in zip(lo, hi)])
    cells[rows / half >= rate / 2] = 0.0                             # a file holds nothing above half its rate
    db = 10 * np.log10(np.maximum(cells, 1e-20))
    v = np.clip((db - (db.max() - RANGE_DB)) / RANGE_DB, 0, 1) if np.any(cells > 1e-20) else np.zeros(db.shape)
    spec = _colour(v)
    for f in (100.0, 1000.0, 10000.0):
        r = int(round((HEIGHT - 1) * (1 - np.log(f / F_LO) / np.log(F_HI / F_LO))))
        spec[r] = np.maximum(spec[r], 55)
    ms = rate // 1000
    each = artifacts.per_ms(np.sum(x ** 2, axis=1), rate)
    env = 10 * np.log10(np.maximum(each, 1e-20)) if len(each) else np.zeros(1)
    peak_s = float(np.argmax(env)) * ms / rate if len(each) else 0.0         # a block is rate // 1000 samples
    env_cols = np.interp(np.arange(width) * hop / ms, np.arange(len(env)), env)
    env_v = np.clip((env_cols - (env.max() - 60.0)) / 60.0, 0, 1) if np.any(each > 1e-20) else np.zeros(width)
    top = np.zeros((ENVELOPE_H, width, 3))
    heights = (env_v * (ENVELOPE_H - 1)).astype(int)
    for c in range(width):
        top[ENVELOPE_H - 1 - heights[c]:, c] = (110, 170, 230)
    peak_col = int(round(peak_s * rate / hop))
    if 0 <= peak_col < width:
        top[:, peak_col] = (255, 255, 255)
    img = np.concatenate([top, np.full((GAP, width, 3), 30.0), spec], axis=0)
    for c in range(width):
        t = c * hop / rate
        prev = (c - 1) * hop / rate
        if c and int(t * 10) != int(prev * 10):
            bright = 90 if int(t) != int(prev) else 40
            img[:, c] = np.maximum(img[:, c], bright)
    return img.astype(np.uint8), {'seconds': round(seconds, 3), 'column_ms': round(1000 * hop / rate, 2),
                                  'peak_s': round(peak_s, 3), 'width': width, 'height': img.shape[0]}


def write(x, path, rate=RATE, width=None):
    """Write the picture of x to path (PNG); returns its scale."""
    img, scale = picture(x, rate, width)
    _png(path, img)
    return scale
