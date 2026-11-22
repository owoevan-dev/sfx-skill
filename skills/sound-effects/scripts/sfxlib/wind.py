"""Wind: air moving past obstacles, after the aeroacoustic model of Selfridge, Moffat and Reiss (2017), "Sound synthesis
of objects swinging through air using physical models", Applied Sciences 7(11), 1177 (CC BY 4.0). From that paper:

- the wake behind an obstacle makes wide-band noise with very little content below the obstacle's Aeolian tone
  frequency and a brown-noise roll-off above it (the paper: brown noise high-passed at that frequency); its intensity
  grows with the eighth power of the air speed;
- a thin obstacle (a wire, a twig) also sings a narrow Aeolian tone at f = St u / d (Strouhal 1878), its width growing
  with the Reynolds number (`whistle_q` sets it here; the default is this skill's choice); its intensity grows with
  the sixth power of the speed; a drag tone sits at twice the frequency, its fluctuating force about a tenth of the
  lift's (what reaches a listener also depends on the angle).

Both frequencies follow the speed in proportion, so a wind is given by its frequencies at the mean speed: band_hz for
the wake (the size of the obstacles) and whistle_hz for a tone (0 for none).

The speed's movement is this skill's own model of gusts, not taken from a source: u(t) = u0 (1 + turbulence w(t)),
where w is a smooth random curve of unit variance whose spectrum is a Gaussian of width gust_hz (turbulence is then the
standard deviation of the speed over its mean); u never falls below MIN_SPEED of the mean. In a stereo sound with a
spread, the wake noise of the two channels is drawn separately (the gusts are shared); the tone stays in the middle."""
import math

import numpy as np

from . import filters, noise

RATE = 48000
RMS = 0.25
MIN_SPEED = 0.05
DRAG_AMPLITUDE = 0.1     # the drag tone's force against the lift's (the paper, citing its reference 11)
WAKE_Q = 0.7071


def gusts(n, rng, turbulence, gust_hz):
    """The speed over its mean at each of n samples (periodic over n: a loop's gusts join their own start)."""
    w = rng.standard_normal(n)
    spectrum = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1 / RATE)
    spectrum *= np.exp(-0.5 * (f / max(float(gust_hz), RATE / n)) ** 2)     # at least one slow swell over the sound
    spectrum[0] = 0.0
    w = np.fft.irfft(spectrum, n)
    sd = float(np.std(w))
    if sd > 0:
        w /= sd
    return np.maximum(1.0 + float(turbulence) * w, MIN_SPEED)


def _filter(x, kind, cutoff, q, loop):
    return filters.svf_circular(x, cutoff, q, kind) if loop else filters.svf(x, cutoff, q, kind)


def _normalized(x, level=RMS):
    """x scaled to an RMS of level (for frames, counting the power of all channels together)."""
    x = np.asarray(x, dtype=np.float64)
    power = float(np.mean(x ** 2)) if x.ndim == 1 else float(np.mean(np.sum(x ** 2, axis=1)))
    return x * (level / math.sqrt(power)) if power > 0 else x


def render(n, rng_for, band_hz=200.0, turbulence=0.2, gust_hz=0.2, whistle_hz=0.0, whistle_db=-12.0,
           whistle_q=60.0, channels=1, spread=0.0, loop=False):
    """n frames (n x channels) of wind. rng_for(name) gives the numpy Generator for each random part ('gusts',
    'wake', 'wake_b', 'whistle'), so changing one part leaves the others as they were. The wake comes at an RMS of
    RMS, the tone whistle_db under or over it."""
    speed = gusts(n, rng_for('gusts'), turbulence, gust_hz)
    cutoff = float(band_hz) * speed

    def wake(name):
        # intensity with the eighth power of the speed: amplitude speed^4, times sqrt(speed) because brown noise
        # above a cut-off carries power in proportion to 1 / cut-off, and the cut-off moves with the speed
        x = _filter(noise.make('brown', n, rng_for(name)), 'highpass', cutoff, WAKE_Q, loop)
        return x * speed ** 4.5

    a = wake('wake')
    if channels == 2 and spread > 0:
        b = wake('wake_b')
        theta = (1.0 - float(spread)) * math.pi / 4
        left = math.cos(theta) * a + math.sin(theta) * b
        right = math.sin(theta) * a + math.cos(theta) * b
        out = _normalized(np.stack([left, right], axis=1))   # each channel near RMS / sqrt 2, as a centred layer
    else:
        out = _normalized(a)[:, None]
        if channels == 2:
            out = np.repeat(out, 2, axis=1) * math.sqrt(0.5)
    if whistle_hz and whistle_hz > 0:
        source = rng_for('whistle').standard_normal(n)
        lift = _filter(source, 'bandpass', float(whistle_hz) * speed, float(whistle_q), loop)
        drag = _filter(source, 'bandpass', 2 * float(whistle_hz) * speed, float(whistle_q), loop)
        # intensity with the sixth power of the speed: amplitude speed^3, over sqrt(speed) because white noise
        # through a band-pass of constant q carries power in proportion to its centre, which moves with the speed
        tone = (lift + DRAG_AMPLITUDE * drag) * speed ** 2.5
        tone = _normalized(tone, RMS * 10 ** (float(whistle_db) / 20))
        out = out + (tone[:, None] * (1.0 if channels == 1 else math.sqrt(0.5)))
    return out
