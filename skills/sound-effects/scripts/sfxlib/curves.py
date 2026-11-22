"""Breakpoint curves: a number (constant) or points [time, value] or [time, value, curve], times in seconds from the
start of their layer. Every time is rounded to a whole sample at 48 kHz; the curve named on a point shapes the segment
from that point to the next:

  lin   straight line
  exp   straight in log value (for frequencies; both ends above zero, else straight)
  db    straight in decibels down to a floor of -80 dB (for gains; a segment ending at 0 reaches 0 at its end)
  cos   raised cosine (smooth start and end)
  hold  keeps the value until the next point

Before the first point the first value holds, after the last point the last."""
import math

import numpy as np

RATE = 48000
CURVES = ('lin', 'exp', 'db', 'cos', 'hold')
FLOOR_DB = -80.0


def samples(t, rate=RATE):
    """Seconds to a whole number of samples (rounded)."""
    return int(round(float(t) * rate))


def normalize(spec):
    """[(time, value, curve or None)] from a number or a list of points; checks shapes, names and order."""
    if isinstance(spec, (int, float)):
        return [(0.0, float(spec), None)]
    if not isinstance(spec, (list, tuple)) or not spec:
        raise ValueError(f'a curve is a number or a list of [time, value] points, not {spec!r}')
    out = []
    for point in spec:
        if not isinstance(point, (list, tuple)) or len(point) not in (2, 3):
            raise ValueError(f'a curve point is [time, value] or [time, value, curve], not {point!r}')
        t, v = float(point[0]), float(point[1])
        curve = point[2] if len(point) == 3 else None
        if curve is not None and curve not in CURVES:
            raise ValueError(f"unknown curve {curve!r}; use one of {', '.join(CURVES)}")
        if t < 0 or not math.isfinite(t) or not math.isfinite(v):
            raise ValueError(f'curve point {point!r} needs a time of 0 or more and finite numbers')
        if out and t < out[-1][0]:
            raise ValueError(f'curve times must not go back ({t} after {out[-1][0]})')
        out.append((t, v, curve))
    return out


def _segment(v0, v1, frac, curve):
    if curve == 'hold':
        return np.full(len(frac), v0)
    if curve == 'exp' and v0 > 0 and v1 > 0:
        return v0 * (v1 / v0) ** frac
    if curve == 'db':
        d0 = 20 * math.log10(v0) if v0 > 10 ** (FLOOR_DB / 20) else FLOOR_DB
        d1 = 20 * math.log10(v1) if v1 > 10 ** (FLOOR_DB / 20) else FLOOR_DB
        sign = -1.0 if (v0 < 0 or v1 < 0) else 1.0
        return sign * 10 ** ((d0 + (d1 - d0) * frac) / 20)
    if curve == 'cos':
        return v0 + (v1 - v0) * (1 - np.cos(np.pi * frac)) / 2
    return v0 + (v1 - v0) * frac


def evaluate(spec, n, rate=RATE, default='lin'):
    """The curve's value at each of n samples."""
    pts = normalize(spec)
    out = np.empty(n)
    bounds = [samples(t, rate) for t, _, _ in pts]
    first = min(bounds[0], n)
    out[:first] = pts[0][1]
    for i in range(len(pts) - 1):
        b0, b1 = bounds[i], bounds[i + 1]
        if b1 <= b0 or b0 >= n:
            continue
        k = np.arange(b0, min(b1, n))
        frac = (k - b0) / (b1 - b0)
        out[k] = _segment(pts[i][1], pts[i + 1][1], frac, pts[i][2] or default)
    last = bounds[-1]
    if last < n:
        out[max(last, 0):] = pts[-1][1]
    return out


def envelope_points(shape):
    """Points for the shorthand {'attack', 'hold', 'decay', 'curve'} (seconds; curve shapes the decay, default 'db'):
    0 at the start, full level after the attack, held, then down to 0 at the end of the decay."""
    attack = float(shape.get('attack', 0.0))
    hold = float(shape.get('hold', 0.0))
    decay = float(shape.get('decay', 0.0))
    curve = shape.get('curve', 'db')
    if curve not in CURVES:
        raise ValueError(f"unknown curve {curve!r}; use one of {', '.join(CURVES)}")
    pts = [[0.0, 0.0]] if attack > 0 else []
    pts.append([round(attack, 9), 1.0])
    if hold > 0:
        pts.append([round(attack + hold, 9), 1.0])
    pts[-1] = pts[-1] + [curve]
    pts.append([round(attack + hold + decay, 9), 0.0])
    return pts
