"""Struck objects as sums of decaying modes (modal synthesis).

Mode frequencies follow a bar free at both ends, whose flexural frequencies are about proportional to (2n + 1)^2
(Russell, Penn State, "Flexural Vibration of a Rectangular Bar"): 1, 2.78, 5.44, 9.0 ... times the lowest. Each mode
falls to 1/e in t_d / f seconds, the frequency-independent decay of Klatzky, Pai and Krotkov (2000), who found decay
the strongest cue to an object's material; the presets are the t_d at which half their listeners chose each
material. The mode amplitudes are this skill's choice.

The strike. Hardness is carried by how long the contact force lasts, and its exact shape matters little (van den Doel,
Kry and Pai 2001, "FoleyAutomatic"): here a Gaussian, wider for a softer strike, which lets fewer of the higher modes
through and has no spectral null for a mode to fall into. A very hard contact is not one clean impulse: the same paper
reports micro collisions caused by the objects' own vibration, within about 15 ms, and finds a burst of impulses at
the first four modes convincing. A strike harder than MICRO_FROM takes that burst, more of it the harder it is (how
much is this skill's choice).

Other shapes: an ideal circular membrane, whose modes sit at the zeros of the Bessel functions, j_mn / j_01 (the
zeros from Abramowitz and Stegun, Handbook of Mathematical Functions, Table 9.5): 1, 1.593, 2.136, 2.295 ... An ideal
membrane is the idealisation; a real drum's air and tension move its modes. Or any list of ratios to the lowest mode,
for an object of the agent's own design."""
import numpy as np

from . import dsp

RATE = 48000
BAR_RATIOS = tuple(((2 * n + 1) ** 2) / 9 for n in range(1, 13))
BESSEL_ZEROS = (2.40483, 3.83171, 5.13562, 5.52008, 6.38016, 7.01559, 7.58834, 8.41724, 8.65373, 8.77148, 9.76102,
                9.93611)          # j_01, j_11, j_21, j_02, j_31, j_12, j_41, j_22, j_03, j_51, j_32, j_61
MEMBRANE_RATIOS = tuple(z / BESSEL_ZEROS[0] for z in BESSEL_ZEROS)
SHAPES = {'bar': BAR_RATIOS, 'membrane': MEMBRANE_RATIOS}
MATERIAL_TD = {'rubber': 10 ** 0.46, 'wood': 10 ** 0.50, 'glass': 10 ** 2.10, 'steel': 10 ** 2.65}
MATERIALS = tuple(MATERIAL_TD)
LIMIT_HZ = 20000.0
MICRO_FROM = 0.7                # hardness above which a strike chatters
MICRO_S = 0.015                 # the micro collisions' time, from van den Doel, Kry and Pai (2001)
MICRO_MODES = 4
MICRO_LEVEL = 0.35              # the first of a mode's impulses against the strike: this skill's choice


def mode_frequencies(f1, modes, detune_cents=0.0, rng=None, ratios=BAR_RATIOS):
    """The modes' frequencies: f1 times the ratios (a bar's by default), each detuned at random by up to
    detune_cents, those below LIMIT_HZ only."""
    out = []
    for ratio in tuple(ratios)[:int(modes)]:
        f = f1 * ratio
        if detune_cents and rng is not None:
            f *= 2 ** (float(rng.uniform(-1.0, 1.0)) * detune_cents / 1200)
        if f >= LIMIT_HZ:
            break
        out.append(f)
    return out


def contact(freqs, hardness):
    """The force of a strike on modes at `freqs` (Hz): a Gaussian pulse of unit area, whose -3 dB point moves up from
    300 Hz for the softest strike (a Gaussian of width sigma is 3 dB down at 0.1325 / sigma) until, near the hardest, the
    pulse is shorter than a sample and passes every mode; and above MICRO_FROM a burst of the same pulse at the periods of the first modes, fading over MICRO_S."""
    contact_hz = 300.0 * (20000.0 / 300.0) ** float(hardness)
    sigma = 0.1325 / contact_hz
    half = max(1, int(np.ceil(3 * sigma * RATE)))
    tp = np.arange(-half, half + 1) / RATE
    pulse = np.exp(-tp ** 2 / (2 * sigma ** 2))
    pulse = pulse / pulse.sum()
    amount = max(0.0, (float(hardness) - MICRO_FROM) / (1.0 - MICRO_FROM))
    if amount <= 0:
        return pulse
    length = int(MICRO_S * RATE)
    train = np.zeros(length)
    train[0] = 1.0
    for f in list(freqs)[:MICRO_MODES]:
        for k in range(1, int(MICRO_S * f) + 1):
            i = int(round(k * RATE / f))
            if i < length:
                train[i] += MICRO_LEVEL * amount * (1.0 - i / length)
    return np.convolve(train, pulse)


def render(f1, n, t_d, modes=6, hardness=0.5, detune_cents=0.0, rng=None, shape='bar', ratios=None):
    """n samples of a struck object with lowest mode f1 (Hz), decay parameter t_d, up to `modes` modes below
    20 kHz, hardness 0 (soft, dull) to 1 (hard, bright), each mode detuned at random by up to detune_cents; its modes
    those of `shape` (SHAPES) or the given ratios. Peak 1."""
    t = np.arange(n) / RATE
    ir = np.zeros(n)
    slope = 1.6 - 1.2 * float(hardness)            # amplitude of mode k falls as k^-slope
    table = tuple(ratios) if ratios else SHAPES[shape]
    freqs = mode_frequencies(f1, modes, detune_cents, rng, table)
    for k, f in enumerate(freqs, start=1):
        ir += k ** (-slope) * np.exp(-t * f / t_d) * np.sin(2 * np.pi * f * t)
    out = dsp.fft_convolve(ir, contact(freqs, hardness))[:n]
    peak = float(np.max(np.abs(out)))
    return out / peak if peak > 0 else out
