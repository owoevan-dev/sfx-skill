"""A sound's traits, measured for the agent to read: how dense, bright, long and balanced it is.

The agent cannot hear; these numbers describe what a listener would notice first, so a change can be aimed and its
effect checked. They are information, not a verdict, and are compared with nothing: check.json gives them per sound
(the median of its variants) beside its flags.

- plr_db: true peak less the loudness of the loudest 200 ms (loudness.brief), so that a short sound and a held one can be
  compared. High: peaky, little body, the
  sound levels quietly; low: dense, little left of its attack.
- crest_db: true peak less the RMS of the active part.
- centroid_hz: the spectral centroid of the active part, the energy-weighted mean frequency (Peeters, Giordano, Susini,
  Misdariis and McAdams 2011, "The Timbre Toolbox"); higher reads brighter and thinner, lower darker.
- flatness: spectral flatness, the geometric over the arithmetic mean of the energy spectrum between 100 Hz and
  16 kHz (Peeters et al. 2011): near 1 noise-like, near 0 tonal.
- active_s, lead_ms, attack_ms, decay20_ms, peak_s: from the first to the last millisecond within 40 dB of the peak;
  silence before it; from its start to the peak; from the peak to the last millisecond within 20 dB of it; the time
  of the loudest millisecond. The 40 and 20 dB edges are this skill's choices. A loop has no start or end for them to
  count from, so its summary leaves them out.
- small_speaker_db: the share of the energy at 400 Hz and above, in dB: roughly how much of the sound is left on a
  phone's speaker, which gives little below a few hundred Hz (400 Hz is this skill's stand-in, not a measured device).
- bands: shares of the energy between 20 Hz and 20 kHz in six bands (sub 20-60, low 60-150, low-mid 150-500, mid
  500-2000, high-mid 2-5 kHz, high 5-20 kHz; this skill's division, with 150 Hz-5 kHz the pitch range Brewster,
  Wright and Edwards give for earcons)."""
import math

import numpy as np

from . import artifacts, loudness, measure

RATE = 48000
EDGE_DB = -40.0
DECAY_DB = -20.0
BANDS = ((20, 60, 'sub'), (60, 150, 'low'), (150, 500, 'low_mid'), (500, 2000, 'mid'), (2000, 5000, 'high_mid'),
         (5000, 20000, 'high'))
FLAT_RANGE = (100.0, 16000.0)
SMALL_SPEAKER_HZ = 400.0
SUMMARIZED = ('plr_db', 'crest_db', 'centroid_hz', 'flatness', 'small_speaker_db', 'active_s', 'attack_ms', 'decay20_ms',
              'peak_s')
TIMED = ('active_s', 'attack_ms', 'decay20_ms', 'peak_s')


def envelope_db(x):
    """1 ms RMS envelope (dB, all channels' power together), one value per millisecond."""
    e = artifacts.per_ms(np.sum(np.asarray(x, dtype=np.float64) ** 2, axis=1), RATE)
    if len(e) == 0:
        return np.array([-200.0])
    return 10 * np.log10(np.maximum(e, 1e-20))


def measures(x):
    """The traits of one sound (frames n x channels, or mono)."""
    x = np.asarray(x, dtype=np.float64)
    x = x[:, None] if x.ndim == 1 else x
    env = envelope_db(x)
    peak_i = int(np.argmax(env))
    top = float(env[peak_i])
    active = np.flatnonzero(env >= top + EDGE_DB)
    first, last = int(active[0]), int(active[-1])
    above = np.flatnonzero(env[peak_i:] >= top + DECAY_DB)
    seg = x[first * (RATE // 1000):(last + 1) * (RATE // 1000)]
    rms = math.sqrt(float(np.mean(np.sum(seg ** 2, axis=1) / x.shape[1]))) if len(seg) else 0.0
    tp_db = loudness.true_peak_db(x)
    mm = loudness.max_momentary(x)
    short = loudness.brief(x)
    freqs, spectrum = measure.energy_spectrum(seg)
    total = float(np.sum(spectrum[(freqs >= 20) & (freqs < 20000)])) or 1e-30
    shares = {name: float(np.sum(spectrum[(freqs >= lo) & (freqs < hi)])) / total for lo, hi, name in BANDS}
    kept = float(np.sum(spectrum[(freqs >= SMALL_SPEAKER_HZ) & (freqs < 20000)]))
    band = spectrum[(freqs >= FLAT_RANGE[0]) & (freqs < FLAT_RANGE[1])]
    most = float(band.max()) if len(band) else 0.0
    # an exactly empty bin counts, at 120 dB under the strongest (its logarithm would otherwise have no value)
    flat = float(np.exp(np.mean(np.log(np.maximum(band, most * 1e-12)))) / np.mean(band)) if most > 0 else 0.0
    return {
        'duration_s': len(x) / RATE, 'active_s': (last - first + 1) / 1000.0,
        'lead_ms': float(first), 'attack_ms': float(peak_i - first), 'peak_s': peak_i / 1000.0,
        'decay20_ms': float(above[-1]) if len(above) else 0.0,
        'true_peak_db': tp_db, 'max_momentary_lufs': mm,
        'plr_db': (tp_db - short) if math.isfinite(short) else None,
        'crest_db': (tp_db - 20 * math.log10(rms)) if rms > 0 else None,
        'centroid_hz': measure.centroid_hz(seg), 'flatness': flat, 'small_speaker_db': 10 * math.log10(max(kept / total, 1e-6)),
        'bands': shares, 'channels': x.shape[1],
    }


def summary(variants, loop=False):
    """A sound's traits as the median of its variants' (frames each): the SUMMARIZED values and the band shares; for a
    loop without the TIMED ones."""
    rows = [measures(x) for x in variants]
    out = {}
    for key in SUMMARIZED:
        if loop and key in TIMED:
            continue
        values = [r[key] for r in rows if r[key] is not None and math.isfinite(r[key])]
        out[key] = round(float(np.median(values)), 4) if values else None
    out['bands'] = {name: round(float(np.median([r['bands'][name] for r in rows])), 4) for _, _, name in BANDS}
    return out
