"""Impact recipes: blows, knocks and clangs from decaying modes (modal.py) and filtered noise, and a swing through
the air. Material is carried mostly by how fast the modes decay (Klatzky, Pai and Krotkov 2000); a hit is built as a
stack of layers (a transient, a body, a low layer, a tail), as sound designers describe it. The figures are this
skill's own sound design, tuned during development against measurements of CC0 game sound libraries."""
from . import register
from .base import Param, Recipe
from .parts import LAYER_RULE

KPK = 'Klatzky, Pai and Krotkov (2000), "Perception of material from contact sounds"'
RUSSELL = 'Russell, Penn State, "Flexural Vibration of a Rectangular Bar"'
SMR = 'Selfridge, Moffat and Reiss (2017), "Sound Synthesis of Objects Swinging through Air Using Physical Models"'
FENTON = ("Fenton, Lee and Wakefield, \"Hybrid Multiresolution Analysis of 'Punch' in Musical Signals\", AES 138th "
          'Convention (2015)')

PUNCH_RULE = ('punch is a short period of significant change in power, tied to the energy at that moment and to the '
              'loudness around it', FENTON)

DECAY_RULE = ('material is heard mostly in how fast the sound decays: fast for rubber and wood, slow for glass and '
              'steel', KPK)
TD_RULE = ("each mode falls to 1/e in t_d / f; the materials use the study's regression estimates of the t_d at which half its listeners would choose "
           'them', KPK)
BAR_RULE = ('mode frequencies of a bar free at both ends, about in the ratio (2n + 1)^2', RUSSELL)


def _modal(start, length, f1, material=None, t_d=None, modes=6, hardness=0.5, detune=0.0, gain_db=0.0):
    src = {'type': 'modal', 'modes': modes, 'hardness': hardness, 'detune_cents': detune}
    if t_d is not None:
        src['t_d'] = t_d
    else:
        src['material'] = material
    return {'source': src, 'start': start, 'length': length, 'pitch': f1,
            'env': [[0.0, 1.0, 'hold'], [length - 0.01, 1.0, 'cos'], [length, 0.0]], 'gain_db': gain_db}


def _noise_burst(start, length, kind, cutoff, q, gain_db, color='white'):
    return {'source': {'type': 'noise', 'color': color}, 'start': start, 'length': length,
            'env': [[0.0, 1.0, 'db'], [length, 0.0]], 'filter': {'type': kind, 'cutoff': cutoff, 'q': q},
            'gain_db': gain_db}


def _f1(default, lo, hi, cents=40.0):
    return Param('base_hz', default, lo, hi, 'Hz', vary='cents', amount=cents, help='the lowest mode or body pitch')


HARD = Param('hardness', 0.6, 0.0, 1.0, '', vary='abs', amount=0.05, help='0 a soft mallet, 1 a hard strike')


# ---- punch ----------------------------------------------------------------------------------------------------------

def _punch(p):
    hz, length = p['base_hz'], p['length']
    body_len = 0.55 * length
    thump = {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 0.0, 'length': length,
             'pitch': [[0.0, 2.2 * hz, 'exp'], [0.06, hz]], 'env': [[0.0, 0.0], [0.018, 1.0, 'db'], [length, 0.0]],
             'gain_db': 0.0}
    # the flesh: pink noise in the low mids (a narrow band of noise carries little power, hence the gain)
    body = {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': body_len,
            'env': [[0.0, 0.0], [0.004, 1.0, 'db'], [body_len, 0.0]],
            'filter': {'type': 'bandpass', 'cutoff': 2.75 * hz, 'q': 1.0}, 'gain_db': 14.0 + p['body_db']}
    slap = {'source': {'type': 'noise', 'color': 'white'}, 'start': 0.0, 'length': 0.02,
            'env': [[0.0, 1.0, 'db'], [0.02, 0.0]], 'filter': {'type': 'bandpass', 'cutoff': 1500.0, 'q': 0.8},
            'gain_db': p['snap_db']}
    return {'layers': [thump, body, slap], 'chain': {'saturation': {'drive': 0.3, 'mix': 0.5}}}


register(Recipe(
    name='impact.punch', category='impact',
    summary='a punch landing: a low thump that swells for a moment, the flesh in the low mids and a slap on top',
    params=(_f1(95.0, 50.0, 200.0), Param('length', 0.45, 0.2, 0.9, 's', vary='pct', amount=8.0,
                                          help='time for the thump to die away'),
            Param('body_db', 0.0, -18.0, 6.0, 'dB', vary='db', amount=1.5, help='level of the low-mid flesh'),
            Param('snap_db', -12.0, -30.0, 0.0, 'dB', vary='db', amount=1.5, help='level of the slap on top')),
    build=_punch, rules=(LAYER_RULE, PUNCH_RULE)))


# ---- thud -----------------------------------------------------------------------------------------------------------

def _thud(p):
    hz, length = p['base_hz'], p['length']
    return {'layers': [_modal(0.0, length, hz, 'rubber', modes=4, hardness=p['hardness']),
                       _noise_burst(0.0, 0.06, 'lowpass', 250.0, 0.7071, -8.0, color='pink')]}


register(Recipe(
    name='impact.thud', category='impact', summary='something heavy landing on a soft floor: a low, dull thud',
    params=(_f1(90.0, 45.0, 250.0), Param('length', 0.25, 0.1, 0.6, 's', vary='pct', amount=6.0),
            Param('hardness', 0.3, 0.0, 1.0, '', vary='abs', amount=0.05, help='0 a soft fall, 1 a hard one')),
    build=_thud, rules=(DECAY_RULE, TD_RULE, BAR_RULE)))


# ---- wood knock -----------------------------------------------------------------------------------------------------

HOLLOW_TD = 5.0   # the hollow body behind a struck board rings a little longer than the board's own modes: t_d 5
                  # (log 0.7), still far nearer the study's wood (log 0.5) than its glass (log 2.1)


def _wood(p):
    hz, length = p['base_hz'], p['length']
    return {'layers': [_modal(0.0, length, hz, 'wood', modes=6, hardness=p['hardness'], detune=6.0),
                       _modal(0.0, length, 0.55 * hz, t_d=HOLLOW_TD, modes=2, hardness=0.3, gain_db=p['hollow_db']),
                       _noise_burst(0.0, 0.004, 'bandpass', min(hz * 8, 6000.0), 1.2, -16.0)]}


register(Recipe(
    name='impact.wood_knock', category='impact',
    summary='a knock on wood: the struck board, the hollow body behind it and the click of contact',
    params=(_f1(200.0, 80.0, 800.0, 30.0),
            Param('length', 0.3, 0.1, 0.6, 's', vary='pct', amount=6.0, help='where the last of the ring is cut'),
            HARD, Param('hollow_db', -4.0, -30.0, 6.0, 'dB', vary='db', amount=1.0,
                        help='level of the hollow body (low for a solid block)')),
    build=_wood, rules=(DECAY_RULE, TD_RULE, BAR_RULE, LAYER_RULE)))


# ---- metal clang ----------------------------------------------------------------------------------------------------

def _metal(p):
    hz = p['base_hz']
    t_d = 10 ** 2.65 * p['ring']
    length = min(2.5, max(0.3, 5 * t_d / hz))
    ring = _modal(0.0, length, hz, t_d=t_d, modes=8, hardness=p['hardness'], detune=15.0)
    ring['env'] = [[0.0, 1.0, 'hold'], [0.7 * length, 1.0, 'cos'], [length, 0.0]]     # a long ring fades, it is not cut
    return {'layers': [ring, _noise_burst(0.0, 0.006, 'highpass', 3000.0, 0.7071, -14.0)]}


register(Recipe(
    name='impact.metal_clang', category='impact', summary='a strike on metal: bright, inharmonic and long ringing',
    params=(_f1(420.0, 150.0, 2000.0, 30.0),
            Param('ring', 1.0, 0.25, 1.5, '', vary='pct', amount=8.0, help="decay against the study's steel value"),
            Param('hardness', 0.7, 0.0, 1.0, '', vary='abs', amount=0.05, help='0 a soft mallet, 1 a hard strike')),
    build=_metal, rules=(DECAY_RULE, TD_RULE, BAR_RULE)))


# ---- metal hit ------------------------------------------------------------------------------------------------------

def _metal_hit(p):
    hz, ring = p['base_hz'], p['ring']
    # the struck object's modes, damped by its hold or its load, and a faint free ring of a few higher modes at the
    # study's steel decay, which carries the metal
    return {'layers': [_modal(0.0, 0.4, hz, t_d=p['hit_td'], modes=9, hardness=p['hardness'], detune=15.0),
                       _modal(0.0, ring, 2.78 * hz, 'steel', modes=3, hardness=0.8, detune=15.0, gain_db=p['ring_db']),
                       _noise_burst(0.0, 0.005, 'highpass', 3000.0, 0.7071, -14.0)]}


register(Recipe(
    name='impact.metal_hit', category='impact',
    summary='a hit on a metal object (a pipe, a box, a tool): bright and inharmonic, quickly damped, with a faint ring',
    params=(_f1(450.0, 150.0, 2000.0, 30.0),
            Param('hit_td', 12.0, 3.0, 100.0, '', vary='pct', amount=8.0,
                  help='decay of the struck modes, as t_d (each mode falls to 1/e in t_d / f seconds): about 3 for '
                       "wood, 12 a held or loaded metal object, 126 the study's glass, 447 its steel"),
            Param('ring_db', -28.0, -60.0, -6.0, 'dB', vary='db', amount=1.5, help='level of the free ring'),
            Param('ring', 0.5, 0.1, 2.5, 's', vary='pct', amount=8.0, help='where the free ring is cut'),
            Param('hardness', 0.75, 0.0, 1.0, '', vary='abs', amount=0.05, help='0 a soft mallet, 1 a hard strike')),
    build=_metal_hit, rules=(DECAY_RULE, TD_RULE, BAR_RULE, LAYER_RULE)))


# ---- glass tink -----------------------------------------------------------------------------------------------------

def _glass(p):
    hz = p['base_hz']
    t_d = 10 ** 2.10
    length = min(1.5, max(0.2, 5 * t_d / hz))
    return {'layers': [_modal(0.0, length, hz, 'glass', modes=5, hardness=p['hardness'], detune=8.0)]}


register(Recipe(
    name='impact.glass_tink', category='impact', summary='a light tap on glass: high, clear and short ringing',
    params=(_f1(2400.0, 1000.0, 6000.0, 30.0),
            Param('hardness', 0.85, 0.0, 1.0, '', vary='abs', amount=0.04, help='0 a soft tap, 1 a hard one')),
    build=_glass, rules=(DECAY_RULE, TD_RULE, BAR_RULE)))


# ---- swing ----------------------------------------------------------------------------------------------------------

def _swing(p):
    length, top = p['length'], p['peak_hz']
    # the speed rises and falls once; the tone follows it, and the drag tone sits an octave above, ten times weaker
    points = [[length * i / 8, s] for i, s in enumerate((0.15, 0.4, 0.7, 0.92, 1.0, 0.85, 0.55, 0.25, 0.05))]
    cutoff = [[t, max(150.0, top * s), 'exp'] for t, s in points]
    drag = [[t, max(300.0, 2 * top * s), 'exp'] for t, s in points]
    env = [[t, s * s] for t, s in points]
    env[0] = [0.0, 0.02]
    env[-1] = [length, 0.0]
    return {'layers': [
        {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': length, 'env': env,
         'filter': {'type': 'bandpass', 'cutoff': cutoff, 'q': p['tone']}},
        {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': length, 'env': env,
         'filter': {'type': 'bandpass', 'cutoff': drag, 'q': p['tone']}, 'gain_db': -20.0}]}


register(Recipe(
    name='impact.swing', category='impact', summary='a swing through the air: a whoosh that rises and falls with speed',
    params=(Param('peak_hz', 900.0, 300.0, 3000.0, 'Hz', vary='cents', amount=60.0,
                  help='centre of the whoosh at the fastest point (thin, fast objects higher)'),
            Param('length', 0.35, 0.15, 0.8, 's', vary='pct', amount=8.0),
            Param('tone', 2.5, 0.8, 8.0, '', help='how tonal the whoosh is (filter q)')),
    build=_swing,
    rules=(("an object swung through air: the tone's frequency is proportional to the air speed (Strouhal), and the "
            "drag tone sits at twice the frequency, its fluctuating force about a tenth of the lift's", SMR),)))
