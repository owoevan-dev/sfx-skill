"""Small layer builders shared by the sci-fi and magic recipes, and a stream of pattern numbers that follows the
parameters, so each variant (whose parameters differ) scatters its grains differently while a render repeats exactly."""
import numpy as np

from .. import dsp

MD_ATTR = 'Material Design 2, "Sound attributes"'
BWE_EDW = 'Brewster, Wright and Edwards, citing Edworthy, Loxley and Dennis (1991)'
KPK = 'Klatzky, Pai and Krotkov (2000), "Perception of material from contact sounds"'
LAYERING = ('Metcalfe, "Designer Sounds", Sound On Sound (October 2013); Bazil, "Layers Of Complexity", Sound On Sound '
            '(November 2012)')

ARNAL = ('Arnal, Flinker, Kleinschmidt, Giraud and Poeppel (2015), "Human screams occupy a privileged niche in the '
         'communication soundscape"; Arnal, Kleinschmidt, Spinelli, Giraud and Mégevand (2019), "The rough sound of '
         'salience enhances aversion through neural synchronisation"')
BLUMSTEIN = 'Blumstein, Bryant and Kaye (2012), "The sound of arousal in music is context-dependent"'

UP_RULE = ('upward motion for starting, opening and positive results', MD_ATTR)
DOWN_RULE = ('downward motion for ending and closing', MD_ATTR)
ATTENTION_RULE = ('irregular harmonics, a wide pitch range and rapid onsets draw attention', BWE_EDW)
DECAY_RULE = ('a sound that decays slowly is heard as glass or metal rather than rubber or wood', KPK)
ROUGH_RULE = ('a level that flutters at about 30 to 150 Hz is rough, the quality that makes screams and alarms alarming; '
              'repeated clicks were most aversive at 40 to 80 Hz', ARNAL)
UNEASE_RULE = ('noise added to music makes it feel less pleasant', BLUMSTEIN)
LAYER_RULE = ('a big hit is built from parts with separate jobs: an attack, a sustain and release that may come from '
              'other sounds, a low "sweetener" for weight, and detail such as debris for what broke', LAYERING)


def st(semitones):
    return 2 ** (semitones / 12)


def pattern_rng(name, params):
    """A numpy Generator seeded from the recipe's name and its parameters (rounded), for scattering grains."""
    return np.random.default_rng(dsp.seed_for(name, *(f'{k}={params[k]:.6g}' for k in sorted(params))))


def rough(rate_hz, depth):
    """A layer's tremolo in the roughness region."""
    return {'rate_hz': float(rate_hz), 'depth': float(depth)}


def tone(start, length, pitch, wave='sine', env=None, cutoff=None, q=0.7071, gain_db=0.0, vibrato=None, tremolo=None):
    layer = {'source': {'type': 'osc', 'wave': wave}, 'start': start, 'length': length, 'pitch': pitch,
             'env': env if env is not None else {'attack': 0.002, 'decay': max(length - 0.002, 0.005)},
             'gain_db': gain_db}
    if cutoff is not None:
        layer['filter'] = {'type': 'lowpass', 'cutoff': cutoff, 'q': q}
    if vibrato:
        layer['vibrato'] = vibrato
    if tremolo:
        layer['tremolo'] = tremolo
    return layer


def bell(start, length, pitch, index, ratio=1.4, gain_db=0.0, env=None):
    return {'source': {'type': 'fm', 'ratio': ratio, 'index': index}, 'start': start, 'length': length,
            'pitch': pitch, 'env': env if env is not None else {'attack': 0.001, 'decay': length - 0.001},
            'gain_db': gain_db}


def noise(start, length, kind, cutoff, q, env, color='pink', gain_db=0.0):
    return {'source': {'type': 'noise', 'color': color}, 'start': start, 'length': length, 'env': env,
            'filter': {'type': kind, 'cutoff': cutoff, 'q': q}, 'gain_db': gain_db}
