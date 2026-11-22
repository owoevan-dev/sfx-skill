"""Sci-fi recipes: zaps, charges, shields, teleports, console beeps, power-downs and explosions from FM, filtered
noise, decaying modes and simple waves. Only the motion, attention and layering rules have sources; the sounds
themselves are this skill's own design, the zap and the explosion tuned during development against measurements
of CC0 game sound libraries."""
from . import register
from .base import Param, Recipe
from .parts import ATTENTION_RULE, DECAY_RULE, DOWN_RULE, LAYER_RULE, UP_RULE, bell, noise, pattern_rng, st, tone


def _len(default, lo, hi):
    return Param('length', default, lo, hi, 's', vary='pct', amount=6.0, help='total length')


def _hz(name, default, lo, hi, cents=40.0, help=''):
    return Param(name, default, lo, hi, 'Hz', vary='cents', amount=cents, help=help)


BRIGHT = Param('brightness', 0.5, 0.0, 1.0, '', vary='abs', amount=0.06, help='0 dark, 1 bright and gritty')


# ---- zap ------------------------------------------------------------------------------------------------------------

def _zap(p):
    length, b = p['length'], p['brightness']
    end = min(p['end_hz'], 0.7 * p['start_hz'])                       # a zap always dives
    dive = lambda k: [[0.0, p['start_hz'] * k, 'exp'], [length, end * k]]
    held = 0.25 * length
    env = {'attack': 0.002, 'hold': held, 'decay': length - 0.002 - held}
    return {'layers': [
        bell(0.0, length, dive(1.0), [[0.0, 2.0 + 6.0 * b, 'db'], [length, 0.5]], ratio=1.0, env=env),
        tone(0.0, length, dive(0.5), wave='square', cutoff=3000.0 + 6000.0 * b, gain_db=-10.0, env=env),
        tone(0.0, length, dive(0.25), wave='triangle', gain_db=p['body_db'], env=env),
        noise(0.0, 0.015, 'highpass', 4000.0, 0.7071, [[0.0, 1.0, 'db'], [0.015, 0.0]], color='white',
              gain_db=-14.0)],
        'chain': {'delay': {'time': 0.06, 'feedback': 0.25, 'mix': 0.15, 'damping_hz': 5000.0}}}


register(Recipe(
    name='scifi.zap', category='scifi',
    summary='a laser shot: a bright FM tone diving in pitch, held for a moment, over a body two octaves down',
    params=(_hz('start_hz', 1600.0, 800.0, 5000.0, help='pitch at the start'),
            _hz('end_hz', 200.0, 100.0, 1500.0, help='pitch at the end'), _len(0.3, 0.08, 0.8),
            Param('brightness', 0.3, 0.0, 1.0, '', vary='abs', amount=0.06, help='0 dark, 1 bright and gritty'),
            Param('body_db', -3.0, -40.0, 0.0, 'dB', vary='db', amount=1.0,
                  help='level of the body two octaves down')),
    build=_zap, rules=(ATTENTION_RULE, LAYER_RULE)))


# ---- charge ---------------------------------------------------------------------------------------------------------

def _charge(p):
    length, b = p['length'], p['brightness']
    rise = [[0.0, p['start_hz'], 'exp'], [length, p['end_hz']]]
    env = [[0.0, 0.0, 'cos'], [0.01, 0.15], [0.85 * length, 1.0, 'cos'], [length, 0.0]]
    return {'layers': [
        tone(0.0, length, rise, wave='saw', env=env, cutoff=[[0.0, 300.0, 'exp'], [length, 2000.0 + 8000.0 * b]],
             q=2.0, vibrato={'rate_hz': 9.0, 'depth_cents': 25.0}),
        tone(0.0, length, [[t, f * 2.0, 'exp'] for t, f, *_ in rise], wave='sine', env=env, gain_db=-8.0)]}


register(Recipe(
    name='scifi.charge', category='scifi', summary='charging up: a buzzing tone rising and opening up',
    params=(_hz('start_hz', 110.0, 50.0, 400.0, help='pitch at the start'),
            _hz('end_hz', 1100.0, 400.0, 3000.0, help='pitch at the end'), _len(0.9, 0.3, 2.5), BRIGHT),
    build=_charge, rules=(UP_RULE,)))


# ---- shield hit -----------------------------------------------------------------------------------------------------

def _shield(p):
    hz, length = p['base_hz'], p['length']
    return {'layers': [
        {'source': {'type': 'modal', 't_d': 30.0 + 200.0 * p['ring'], 'modes': 6, 'hardness': 0.8,
                    'detune_cents': 20.0},
         'start': 0.0, 'length': length, 'pitch': hz,
         'env': [[0.0, 1.0, 'hold'], [length - 0.01, 1.0, 'cos'], [length, 0.0]]},
        bell(0.0, length, hz * 1.5, [[0.0, 4.0, 'db'], [length, 0.2]], ratio=1.41, gain_db=-6.0),
        noise(0.0, 0.03, 'bandpass', 3000.0, 1.0, [[0.0, 1.0, 'db'], [0.03, 0.0]], color='white', gain_db=-10.0)],
        'chain': {'room': {'size': 0.6, 'decay': 0.4, 'mix': 0.15}}}


register(Recipe(
    name='scifi.shield_hit', category='scifi', summary='a hit on an energy shield: a metallic strike with an electric ring',
    params=(_hz('base_hz', 600.0, 200.0, 2000.0, cents=30.0, help='lowest mode of the strike'),
            Param('ring', 0.5, 0.0, 1.0, '', vary='abs', amount=0.05, help='how long the strike rings'),
            _len(0.6, 0.25, 1.5)),
    build=_shield, rules=(DECAY_RULE,)))


# ---- teleport -------------------------------------------------------------------------------------------------------

def _teleport(p):
    length, hz = p['length'], p['base_hz']
    swell = [[0.0, 0.0, 'cos'], [0.01, 0.1], [0.8 * length, 1.0, 'cos'], [length, 0.0]]
    return {'layers': [
        noise(0.0, length, 'bandpass', [[0.0, 300.0, 'exp'], [0.9 * length, 8000.0]], 3.0, swell),
        bell(0.0, length, [[0.0, hz, 'exp'], [length, hz * st(24)]], [[0.0, 1.0], [length, 4.0]], ratio=2.01,
             env=swell, gain_db=-6.0),
        tone(0.0, length, [[0.0, hz * st(7), 'exp'], [length, hz * st(31)]], wave='triangle', env=swell,
             gain_db=-12.0, vibrato={'rate_hz': 14.0, 'depth_cents': 40.0})],
        'chain': {'room': {'size': 1.2, 'decay': 0.8, 'mix': 0.25}}}


register(Recipe(
    name='scifi.teleport', category='scifi', summary='teleporting: a shimmering rise that swells and vanishes',
    params=(_hz('base_hz', 300.0, 120.0, 800.0, cents=30.0, help='pitch the shimmer starts from'),
            _len(1.0, 0.4, 2.5)),
    build=_teleport, rules=(UP_RULE,)))


# ---- console beep ---------------------------------------------------------------------------------------------------

def _beep(p):
    hz, n, count = p['base_hz'], p['beep_length'], int(round(p['beeps']))
    layers = []
    for i in range(count):
        pitch = hz if i % 2 == 0 else hz * st(-5)
        layers.append(tone(i * n * 1.5, n, pitch, wave='square', env=[[0.0, 1.0, 'hold'], [n - 0.004, 1.0, 'cos'],
                                                                      [n, 0.0]], cutoff=hz * 6.0))
    return {'layers': layers}


register(Recipe(
    name='scifi.console_beep', category='scifi', summary='a console reporting: a few short square beeps',
    params=(_hz('base_hz', 1200.0, 400.0, 3000.0, cents=25.0, help='pitch of the first beep'),
            Param('beep_length', 0.06, 0.03, 0.15, 's', vary='pct', amount=5.0, help='length of each beep'),
            Param('beeps', 3.0, 1.0, 6.0, '', help='how many beeps, alternating a fourth apart')),
    build=_beep, rules=(ATTENTION_RULE,)))


# ---- power down -----------------------------------------------------------------------------------------------------

def _power_down(p):
    length, b = p['length'], p['brightness']
    fall = [[0.0, p['start_hz'], 'exp'], [length, 40.0]]
    env = [[0.0, 1.0], [0.8 * length, 0.6], [length, 0.0, 'lin']]
    return {'layers': [
        tone(0.0, length, fall, wave='saw', env=env, cutoff=[[0.0, 1500.0 + 6000.0 * b, 'exp'], [length, 200.0]],
             vibrato={'rate_hz': 5.0, 'depth_cents': 30.0}),
        tone(0.0, length, [[t, f * 0.5, 'exp'] for t, f, *_ in fall], wave='square', env=env, cutoff=800.0,
             gain_db=-8.0),
        tone(length * 0.92, 0.08, 55.0, wave='sine', env={'attack': 0.002, 'decay': 0.078}, gain_db=-4.0)]}


register(Recipe(
    name='scifi.power_down', category='scifi', summary='powering down: a buzz sinking and closing until it stops',
    params=(_hz('start_hz', 800.0, 200.0, 2000.0, cents=30.0, help='pitch at the start'), _len(1.2, 0.4, 3.0),
            BRIGHT),
    build=_power_down, rules=(DOWN_RULE,)))


# ---- explosion ------------------------------------------------------------------------------------------------------

def _explosion(p):
    length, hz = p['length'], p['boom_hz']
    attack = lambda a, end: [[0.0, 0.0], [a, 1.0, 'db'], [end, 0.0]]
    layers = [
        noise(0.0, 0.012, 'highpass', 1500.0, 0.7071, [[0.0, 1.0, 'db'], [0.012, 0.0]], color='white', gain_db=-6.0),
        tone(0.0, 0.9 * length, [[0.0, hz, 'exp'], [0.3, max(0.36 * hz, 20.0)]], wave='sine',
             env=attack(0.004, 0.9 * length)),
        # the blast closes from bright to dull; the crunch fills the mids; the rumble outlasts both (narrow bands of
        # noise carry little power, hence their gains)
        noise(0.0, length, 'lowpass', [[0.0, 3500.0, 'exp'], [0.6 * length, 300.0]], 0.7071,
              attack(0.006, length), color='pink', gain_db=8.0),
        noise(0.0, 0.5 * length, 'bandpass', 700.0, 0.8, attack(0.005, 0.5 * length), color='pink',
              gain_db=14.0 + p['crunch_db']),
        noise(0.0, 1.2 * length, 'lowpass', 160.0, 0.7071, attack(0.04, 1.2 * length), color='brown',
              gain_db=10.0 + p['rumble_db'])]
    rng = pattern_rng('scifi.explosion', p)
    for _ in range(int(p['debris'])):
        t = float(rng.uniform(0.05, 0.6 * length))
        n = float(rng.uniform(0.008, 0.03))
        layers.append(noise(round(t, 6), n, 'bandpass', float(rng.uniform(1500.0, 5000.0)), 1.2,
                            [[0.0, 1.0, 'db'], [n, 0.0]], color='white',
                            gain_db=-14.0 - 12.0 * t / length + float(rng.uniform(-3.0, 3.0))))
    return {'layers': layers, 'chain': {'saturation': {'drive': 0.4, 'mix': 0.5}}}


register(Recipe(
    name='scifi.explosion', category='scifi',
    summary='an explosion: a crack, a falling boom, a blast closing from bright to dull, a crunch, debris and rumble',
    params=(_hz('boom_hz', 110.0, 50.0, 200.0, help='where the boom starts falling from'), _len(1.6, 0.6, 4.0),
            Param('crunch_db', 0.0, -30.0, 6.0, 'dB', vary='db', amount=1.5, help='level of the mid-range crunch'),
            Param('rumble_db', 0.0, -30.0, 6.0, 'dB', vary='db', amount=1.5, help='level of the low rumble'),
            Param('debris', 8.0, 0.0, 30.0, '', help='how many bits of debris crackle')),
    build=_explosion, rules=(LAYER_RULE,), crackle=True))
