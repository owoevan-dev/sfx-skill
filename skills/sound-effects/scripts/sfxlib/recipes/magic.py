"""Magic recipes: sparkles, casts, heals, curses and chimes from bell-like FM grains, rising and falling chords and
filtered noise. Only the motion, attention, roughness and material rules have sources; the sounds are this skill's own
design."""
from . import register
from .base import Param, Recipe
from .parts import (ATTENTION_RULE, DECAY_RULE, DOWN_RULE, ROUGH_RULE, UNEASE_RULE, UP_RULE, bell, noise, pattern_rng,
                    rough as rough_tremolo, st, tone)

PENTATONIC = (0, 2, 4, 7, 9, 12, 14, 16)


def _len(default, lo, hi):
    return Param('length', default, lo, hi, 's', vary='pct', amount=6.0, help='total length')


def _hz(name, default, lo, hi, cents=30.0, help=''):
    return Param(name, default, lo, hi, 'Hz', vary='cents', amount=cents, help=help)


def _grains(name, p, centre, start, span, count, gain_db=-6.0):
    """count short, bright FM grains scattered over [start, start + span) on a pentatonic set above `centre`; the
    first lands on `start`, so the scatter answers at once instead of after a random wait."""
    rng = pattern_rng(name, p)
    layers = []
    for i in range(int(count)):
        t = start + (0.0 if i == 0 else float(rng.uniform(0.0, span)))
        pitch = centre * st(int(rng.choice(PENTATONIC)))
        length = float(rng.uniform(0.08, 0.25))
        layers.append(bell(round(t, 6), length, pitch, [[0.0, 1.5, 'db'], [length, 0.2]], ratio=3.0,
                           gain_db=gain_db + float(rng.uniform(-3.0, 3.0))))
    return layers


# ---- sparkle --------------------------------------------------------------------------------------------------------

def _sparkle(p):
    length = p['length']
    air = noise(0.0, length, 'highpass', 6000.0, 0.7071,
                [[0.0, 0.0, 'cos'], [0.2 * length, 1.0, 'cos'], [length, 0.0]], color='white', gain_db=p['air_db'])
    return {'layers': _grains('magic.sparkle', p, p['centre_hz'], 0.0, 0.7 * length, p['density']) + [air],
            'chain': {'room': {'size': 0.8, 'decay': 0.6, 'mix': 0.2}}}


register(Recipe(
    name='magic.sparkle', category='magic', summary='a sparkle: bright, glassy grains scattered over a breath of air',
    params=(_hz('centre_hz', 2000.0, 800.0, 4000.0, help='lowest pitch of the grains'), _len(0.8, 0.3, 2.0),
            Param('density', 12.0, 4.0, 30.0, '', help='how many grains'),
            Param('air_db', -18.0, -40.0, -6.0, 'dB', help='level of the airy noise')),
    build=_sparkle, rules=(DECAY_RULE,)))


# ---- cast -----------------------------------------------------------------------------------------------------------

def _cast(p):
    length, hz = p['length'], p['base_hz']
    swell = [[0.0, 0.0, 'cos'], [0.01, 0.2], [0.6 * length, 1.0, 'cos'], [length, 0.0]]
    chord = [tone(0.0, length, [[0.0, hz * st(s), 'exp'], [0.6 * length, hz * st(s + 5)]], wave='triangle',
                  env=swell, cutoff=hz * 8.0, gain_db=-8.0) for s in (0, 7, 12)]
    whoosh = noise(0.0, 0.7 * length, 'bandpass', [[0.0, 400.0, 'exp'], [0.6 * length, 6000.0]], 2.0,
                   [[0.0, 0.0, 'cos'], [0.01, 0.2], [0.5 * length, 1.0, 'cos'], [0.7 * length, 0.0]])
    return {'layers': [whoosh] + chord + _grains('magic.cast', p, hz * 4.0, 0.55 * length, 0.3 * length, 4),
            'chain': {'room': {'size': 1.0, 'decay': 0.7, 'mix': 0.2}}}


register(Recipe(
    name='magic.cast', category='magic', summary='casting a spell: a rising whoosh and chord that bursts into sparks',
    params=(_hz('base_hz', 330.0, 150.0, 800.0, help='root of the rising chord'), _len(0.8, 0.4, 2.0)),
    build=_cast, rules=(UP_RULE,)))


# ---- heal -----------------------------------------------------------------------------------------------------------

def _heal(p):
    hz, n = p['base_hz'], p['note_spacing']
    layers = []
    steps = (0, 4, 7, 12, 16)
    for i, s in enumerate(steps):
        ring = 0.9 if i == len(steps) - 1 else 0.6
        layers.append(tone(i * n, ring, hz * st(s), wave='sine', env={'attack': 0.02, 'decay': ring - 0.02},
                           vibrato={'rate_hz': 5.5, 'depth_cents': 12.0}, gain_db=-4.0))
        layers.append(bell(i * n, ring, hz * st(s + 12), [[0.0, 0.8, 'db'], [ring, 0.1]], ratio=2.0, gain_db=-14.0,
                           env={'attack': 0.02, 'decay': ring - 0.02}))
    return {'layers': layers, 'chain': {'room': {'size': 1.2, 'decay': 1.0, 'mix': 0.25}}}


register(Recipe(
    name='magic.heal', category='magic', summary='healing: a soft, rising major arpeggio that rings and shimmers',
    params=(_hz('base_hz', 523.0, 250.0, 1000.0, help='first note'),
            Param('note_spacing', 0.09, 0.05, 0.2, 's', vary='pct', amount=5.0, help='time from note to note')),
    build=_heal, rules=(UP_RULE,)))


# ---- curse ----------------------------------------------------------------------------------------------------------

def _curse(p):
    length, hz, rough = p['length'], p['base_hz'], p['roughness']
    fall = lambda f: [[0.0, f, 'exp'], [length, f * 0.7]]
    env = [[0.0, 0.0, 'cos'], [0.05, 1.0], [0.75 * length, 0.8], [length, 0.0]]
    flutter = rough_tremolo(40.0 + 40.0 * rough, 0.9 * rough) if rough > 0 else None     # where it is most aversive
    layers = [tone(0.0, length, fall(hz * 2 ** (d / 1200)), wave='saw', env=env, tremolo=flutter,
                   cutoff=[[0.0, 1800.0, 'exp'], [length, 300.0]], gain_db=-6.0) for d in (0.0, 70.0 * rough, -45.0 * rough)]
    layers.append(noise(0.0, length, 'lowpass', 220.0, 0.7071, env, gain_db=-6.0))
    layers.append(noise(0.0, 0.6 * length, 'bandpass', [[0.0, 3000.0, 'exp'], [0.6 * length, 300.0]], 2.0,
                        [[0.0, 0.0, 'cos'], [0.02, 1.0], [0.6 * length, 0.0]], gain_db=-10.0))
    return {'layers': layers, 'chain': {'room': {'size': 1.4, 'decay': 1.2, 'mix': 0.2}}}


register(Recipe(
    name='magic.curse', category='magic', summary='a curse: low, beating tones sinking under a dark rumble',
    params=(_hz('base_hz', 110.0, 55.0, 260.0, cents=25.0, help='pitch of the low tones'), _len(1.2, 0.5, 3.0),
            Param('roughness', 0.7, 0.0, 1.0, '', vary='abs', amount=0.05, help='how harsh: detuned, beating tones and a fast flutter of the level')),
    build=_curse, rules=(DOWN_RULE, UNEASE_RULE, ROUGH_RULE, ATTENTION_RULE)))


# ---- pickup chime ---------------------------------------------------------------------------------------------------

def _chime(p):
    hz, gap, warmth = p['base_hz'], p['gap'], p['warmth_db']

    def body(start, length, f):
        held = 0.4 * length
        return tone(start, length, f, wave='sine', env={'attack': 0.006, 'hold': held, 'decay': length - 0.006 - held},
                    gain_db=warmth)
    # two bell notes a fifth apart, each over a held sine an octave down (the warmth), and a glint of grains
    layers = [bell(0.0, 0.5, hz, [[0.0, 0.8, 'db'], [0.5, 0.2]], ratio=3.0),
              bell(gap, 0.8, hz * st(7), [[0.0, 0.8, 'db'], [0.8, 0.2]], ratio=3.0),
              body(0.0, 0.5, hz / 2), body(gap, 0.8, hz * st(7) / 2)]
    layers += _grains('magic.pickup_chime', p, hz * 2.0, gap, 0.25, 3, gain_db=p['glint_db'])
    return {'layers': layers, 'chain': {'delay': {'time': 0.11, 'feedback': 0.3, 'mix': 0.18, 'damping_hz': 6000.0}}}


register(Recipe(
    name='magic.pickup_chime', category='magic',
    summary='a magical pickup: two bell notes rising a fifth over a warm octave below, with a glint',
    params=(_hz('base_hz', 784.0, 400.0, 2000.0, help='first note'),
            Param('gap', 0.09, 0.05, 0.2, 's', vary='pct', amount=5.0, help='time to the second note'),
            Param('warmth_db', -3.0, -40.0, 0.0, 'dB', vary='db', amount=1.0,
                  help='level of the held notes an octave down'),
            Param('glint_db', -16.0, -40.0, -6.0, 'dB', vary='db', amount=1.5, help='level of the glinting grains')),
    build=_chime, rules=(UP_RULE, DECAY_RULE)))
