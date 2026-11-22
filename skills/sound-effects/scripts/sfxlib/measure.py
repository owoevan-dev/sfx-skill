"""Measurements of one sound at 48 kHz, reported as measurements (none of them says whether a sound is good).

Thresholds here are this skill's choices, named where they are used; the reasons are in references/checks.md."""
import math

import numpy as np

RATE = 48000
ONSET_REL_DB = -40.0      # a sound starts at its first sample within 40 dB of its peak
STRIKE_REL_DB = -12.0     # a sound lands at its first millisecond within 12 dB of its loudest
ENDING_MS = 1.0           # an ending counts as cut when its last millisecond still reaches ...
ENDING_REL_DB = -40.0     # ... within 40 dB of the peak
CLIP = 0.999              # samples at or beyond this magnitude count as clipped
BANDS_HZ = (150.0, 5000.0)  # low / mid / high split (the pitch range Brewster, Wright and Edwards give for earcons)


def _frames(x):
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


def _envelope(x):
    """Largest magnitude over channels at each frame."""
    return np.max(np.abs(_frames(x)), axis=1)


def peak_db(x):
    peak = float(np.max(_envelope(x))) if len(x) else 0.0
    return 20 * math.log10(peak) if peak > 0 else -np.inf


def onset_ms(x):
    """Milliseconds from the first frame to the first frame within ONSET_REL_DB of the peak (0 for silence)."""
    env = _envelope(x)
    peak = float(np.max(env)) if len(env) else 0.0
    if peak <= 0:
        return 0.0
    first = int(np.argmax(env >= peak * 10 ** (ONSET_REL_DB / 20)))
    return first * 1000.0 / RATE


def landing(x):
    """When the sound lands: `strike_ms`, from the first frame to the first millisecond (RMS, all channels) within
    STRIKE_REL_DB of the loudest one (a recording's noise floor before the strike moves onset_ms and does not move this),
    and `strike_rise_ms`, from there to the loudest millisecond (short for a strike, long for a swell)."""
    frames = _frames(x)
    hop = RATE // 1000
    count = len(frames) // hop
    if count == 0:
        return {'strike_ms': 0.0, 'strike_rise_ms': 0.0}
    power = np.mean(np.sum(frames[:count * hop] ** 2, axis=1).reshape(count, hop), axis=1)
    top = float(np.max(power))
    if top <= 0:
        return {'strike_ms': 0.0, 'strike_rise_ms': 0.0}
    first = int(np.argmax(power >= top * 10 ** (STRIKE_REL_DB / 10)))
    return {'strike_ms': float(first), 'strike_rise_ms': float(int(np.argmax(power)) - first)}


def ending(x):
    """The ending: `last_ms_rel_db`, the largest magnitude in the last ENDING_MS against the peak; `last_10ms_rel_db`,
    the RMS of the last 10 ms against the peak; `cut`, whether the last millisecond reaches ENDING_REL_DB of the peak
    (a sound that stops at a high level clicks)."""
    env = _envelope(x)
    peak = float(np.max(env)) if len(env) else 0.0
    if peak <= 0:
        return {'last_ms_rel_db': -np.inf, 'last_10ms_rel_db': -np.inf, 'cut': False}
    last = env[-max(1, int(ENDING_MS * RATE / 1000)):]
    tail = _frames(x)[-max(1, int(0.01 * RATE)):]
    last_db = 20 * math.log10(max(float(np.max(last)), 1e-12) / peak)
    rms = math.sqrt(float(np.mean(tail ** 2)))
    return {'last_ms_rel_db': last_db, 'last_10ms_rel_db': 20 * math.log10(max(rms, 1e-12) / peak),
            'cut': last_db >= ENDING_REL_DB}


def dc_offset(x):
    """The largest channel mean (full scale = 1)."""
    means = np.mean(_frames(x), axis=0)
    return float(means[np.argmax(np.abs(means))])


def clipped(x):
    """Number of samples at or beyond CLIP."""
    return int(np.sum(np.abs(_frames(x)) >= CLIP))


def energy_spectrum(x):
    """(frequencies, energy per bin, all channels together) of the whole sound, with no window: each moment counts by
    its energy, as in a long-term average spectrum. A window over a whole one-shot would weight its middle and all but
    hide its onset, where a struck or plucked sound puts much of its energy."""
    frames = _frames(x)
    n = len(frames)
    power = np.zeros(n // 2 + 1)
    for ch in range(frames.shape[1]):
        power += np.abs(np.fft.rfft(frames[:, ch])) ** 2
    return np.fft.rfftfreq(n, 1 / RATE), power



def band_shares(x):
    """Shares of the power below BANDS_HZ[0] ('low'), between ('mid') and above BANDS_HZ[1] ('high')."""
    freqs, power = energy_spectrum(x)
    total = float(np.sum(power))
    if total <= 0:
        return {'low': 0.0, 'mid': 0.0, 'high': 0.0}
    low = float(np.sum(power[freqs < BANDS_HZ[0]])) / total
    high = float(np.sum(power[freqs > BANDS_HZ[1]])) / total
    return {'low': low, 'mid': max(0.0, 1.0 - low - high), 'high': high}


AUDIBLE_HZ = (20.0, 20000.0)


def centroid_hz(x):
    """Spectral centroid of the whole sound: the power-weighted mean frequency over AUDIBLE_HZ. An offset or a rumble
    below 20 Hz (common in recordings, and inaudible) would otherwise pull it down."""
    freqs, power = energy_spectrum(x)
    keep = (freqs >= AUDIBLE_HZ[0]) & (freqs < AUDIBLE_HZ[1])
    total = float(np.sum(power[keep]))
    return float(np.sum(freqs[keep] * power[keep]) / total) if total > 0 else 0.0


def stereo(x):
    """For two channels: `correlation` of left and right, and `mono_sum_change_db`, the level of their average against
    the mean level of the two (0 dB for identical channels, very low when they cancel)."""
    frames = _frames(x)
    if frames.shape[1] != 2:
        return None
    left, right = frames[:, 0], frames[:, 1]
    denom = math.sqrt(float(np.sum(left ** 2)) * float(np.sum(right ** 2)))
    corr = float(np.sum(left * right)) / denom if denom > 0 else 0.0
    mono = float(np.mean(((left + right) / 2) ** 2))
    each = float(np.mean((left ** 2 + right ** 2) / 2))
    change = 10 * math.log10(mono / each) if mono > 0 and each > 0 else -120.0
    return {'correlation': corr, 'mono_sum_change_db': max(change, -120.0)}
