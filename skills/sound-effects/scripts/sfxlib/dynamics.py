"""Dynamics: a compressor for a sound's chain and a true-peak limiter for the level step.

Compressor (feed-forward, after Giannoulis, Massberg and Reiss (2012), "Digital dynamic range compressor design: a
tutorial and analysis", JAES 60(6); the same structure as the MathWorks Audio Toolbox compressor): the level in dB
(the largest magnitude over channels, so the channels share one gain), a static characteristic with a soft knee

    y = x                                   when 2 (x - T) < -W
    y = x + (1/R - 1) (x - T + W/2)^2 / (2W) when 2 |x - T| <= W
    y = T + (x - T) / R                      when 2 (x - T) > W

then the gain y - x smoothed by one-pole filters, the attack one while the gain falls and the release one while it
rises, each with its 10-90 % time (alpha = exp(-ln 9 / (rate x time))), then make-up gain and a dry/wet mix.

Limiter: the true peak (the signal four times oversampled, as BS.1770 measures it) held under a ceiling. The gain each
oversampled sample needs is taken as a running minimum over a short look-ahead and smoothed over the same length, so
the gain is already down when the peak arrives and has no step; it recovers with a one-pole release. Offline, the look
ahead costs no delay. A signal whose true peak stays under the ceiling is returned unchanged."""
import math

import numpy as np

from . import dsp, loudness

RATE = 48000
LN9 = math.log(9.0)


def _coefficient(seconds):
    return math.exp(-LN9 / (RATE * seconds)) if seconds > 0 else 0.0


def static_curve(x_db, threshold_db, ratio, knee_db):
    """The compressor's output level (dB) for input levels x_db (array)."""
    x = np.asarray(x_db, dtype=np.float64)
    t, r, w = float(threshold_db), float(ratio), float(knee_db)
    y = x.copy()
    over = 2 * (x - t) > w
    y[over] = t + (x[over] - t) / r
    if w > 0:
        knee = np.abs(2 * (x - t)) <= w
        y[knee] = x[knee] + (1 / r - 1) * (x[knee] - t + w / 2) ** 2 / (2 * w)
    return y


def _smooth(gain_db, attack_s, release_s, start=0.0):
    a, r = _coefficient(attack_s), _coefficient(release_s)
    out = [0.0] * len(gain_db)
    g = float(start)
    for i, target in enumerate(gain_db.tolist()):
        coef = a if target < g else r
        g = coef * g + (1 - coef) * target
        out[i] = g
    return np.array(out)


def compress(x, threshold_db=-18.0, ratio=4.0, knee_db=6.0, attack_ms=5.0, release_ms=80.0, makeup_db=0.0,
             mix=1.0, loop=False):
    """x (n, or n x channels) compressed; same shape. With loop=True, x is one period of a loop: the gain runs in over
    the period's end first (as long as the release needs to forget its start), so it joins its own start."""
    frames = np.asarray(x, dtype=np.float64)
    mono = frames.ndim == 1
    frames = frames[:, None] if mono else frames
    n = len(frames)
    level = 20 * np.log10(np.maximum(np.max(np.abs(frames), axis=1), 1e-9))
    gain = static_curve(level, threshold_db, ratio, knee_db) - level
    if loop:
        pre = min(n, int(math.ceil(20.7 / LN9 * max(attack_ms, release_ms) / 1000 * RATE)) + 1)
        smoothed = _smooth(np.concatenate([gain[n - pre:], gain]), attack_ms / 1000, release_ms / 1000)[pre:]
    else:
        smoothed = _smooth(gain, attack_ms / 1000, release_ms / 1000)
    wet = frames * (10 ** ((smoothed + float(makeup_db)) / 20))[:, None]
    out = (1 - float(mix)) * frames + float(mix) * wet
    return out[:, 0] if mono else out


def running_min(values, width):
    """min(values[i:i + width]) for each i (the window looks ahead, running past the end over the last value), by
    doubling: O(n log width)."""
    values = np.asarray(values, dtype=np.float64)
    n = len(values)
    if width <= 1 or n == 0:
        return values.copy()
    m = np.concatenate([values, np.full(width, values[-1])])     # m[i] = min over [i, i + span)
    span = 1
    while span * 2 <= width:
        m = np.minimum(m[:len(m) - span], m[span:])
        span *= 2
    return np.minimum(m[:n], m[width - span:width - span + n])


def _limit_gain(frames, ceiling, width, release, hold=0):
    n = len(frames)
    factor = dsp.FACTOR
    peak = np.max(np.abs(frames), axis=1)
    for c in range(frames.shape[1]):
        up = np.abs(dsp.upsample(frames[:, c]))
        peak = np.maximum(peak, up[:n * factor].reshape(n, factor).max(axis=1))
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    # every sample of the look-ahead window [i, i + 2 width) needs at most `held`; averaging `held` over the width
    # before i keeps the gain under every sample's need and makes its fall a ramp
    # with a hold, the window also reaches `hold` samples back: the gain stays down that long after a peak
    held = running_min(np.concatenate([np.full(hold, need[0]), need]), 2 * width + hold)[:n]
    smooth = np.convolve(np.concatenate([np.full(width - 1, held[0]), held]), np.ones(width) / width, mode='valid')
    out = smooth[:n].tolist()
    g = out[0]
    for i, v in enumerate(out):                                 # falls at once, recovers with the release
        g = v if v < g else release * g + (1 - release) * v
        out[i] = g
    return np.array(out)


def limit(x, ceiling_dbtp=-1.0, lookahead_ms=1.5, release_ms=60.0, tries=4, hold_ms=0.0, span=None):
    """x (n, or n x channels) with its true peak held under ceiling_dbtp; returns (y, largest gain reduction in dB, over
    the frames `span` (start, end) when a caller keeps only those).
    The gain is computed from the four-times oversampled peaks; should the gained signal's own true peak still pass
    the ceiling by a hair, the target is lowered by that much and the gain computed again (at most `tries` times).
    hold_ms keeps the gain down that long after each peak before the release begins: held for more than a period of a
    low, lopsided wave, the gain no longer dips at every tall peak and recovers in the troughs, which would take more
    off one polarity than the other and leave an offset."""
    frames = np.asarray(x, dtype=np.float64)
    mono = frames.ndim == 1
    frames = frames[:, None] if mono else frames
    if loudness.true_peak_db(frames) <= ceiling_dbtp:
        return (frames[:, 0] if mono else frames).copy(), 0.0
    width = max(1, int(round(lookahead_ms / 1000 * RATE)))
    release = _coefficient(release_ms / 1000)
    hold = max(0, int(round(hold_ms / 1000 * RATE)))
    target = float(ceiling_dbtp)
    for _ in range(tries):
        gain = _limit_gain(frames, 10 ** (target / 20), width, release, hold)
        y = frames * gain[:, None]
        over = loudness.true_peak_db(y) - ceiling_dbtp
        if over <= 0:
            break
        target -= over + 0.01
    kept = gain if span is None else gain[span[0]:span[1]]
    reduction = -20 * math.log10(max(float(np.min(kept)), 1e-12))
    return (y[:, 0] if mono else y), reduction
