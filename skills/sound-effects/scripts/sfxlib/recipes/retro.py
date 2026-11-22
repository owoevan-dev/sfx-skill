"""Retro recipes: eight-bit-style game sounds on the chip voices (chip.py), which follow the NES's sound hardware as
the NESdev Wiki documents it. The figures (pitches, slides, arpeggios) are this skill's own; none copies a known
game's sound."""
from . import register
from .base import Param, Recipe
from .. import chip

NES_PULSE = 'NESdev Wiki, "APU Pulse"'
NES_TRI = 'NESdev Wiki, "APU Triangle"'
NES_NOISE = 'NESdev Wiki, "APU Noise"'
NES_ENV = 'NESdev Wiki, "APU Envelope"'
NES_FRAME = 'NESdev Wiki, "APU Frame Counter"'
NES_MIXER = 'NESdev Wiki, "APU Mixer"'

DUTY_RULE = ('pulse duty cycles of 12.5, 25, 50 or 75 %', NES_PULSE)
PERIOD_RULE = ('pitches only on the periods the 11-bit timers allow', NES_PULSE)
VOLUME_RULE = ('volume in 16 practically linear steps; the envelope decays as a stepped saw', NES_ENV)
STEP_RULE = ('parameters change in steps (envelopes at about 240 Hz, sweeps at 120 Hz, the frame interrupt at 60 Hz);'
             ' these recipes step at 60 Hz', NES_FRAME)
TRI_RULE = ('a 32-step triangle wave', NES_TRI)
NOISE_RULE = ('1-bit noise from a 15-bit shift register, long or short mode, at 16 clock rates', NES_NOISE)
CONSOLE_RULE = ("the console's output filters: high-pass at 90 and 440 Hz and low-pass at 14 kHz (NES), high-pass at "
                '37 Hz (Famicom)', NES_MIXER)
BASE_RULES = (DUTY_RULE, PERIOD_RULE, VOLUME_RULE, STEP_RULE, CONSOLE_RULE)


def _st(semitones):
    return 2 ** (semitones / 12)


def _layer(start, length, wave, pitch, env, duty=0.5, mode='long', gain_db=0.0):
    return {'source': {'type': 'chip', 'wave': wave, 'duty': chip.snap_duty(duty), 'mode': mode, 'step_hz': 60.0},
            'start': start, 'length': length, 'pitch': pitch, 'env': env, 'gain_db': gain_db}


FRAME = 1 / 60  # one step of the chip voices' parameters


def _decay(length, peak=1.0):
    """A linear decay that reaches silence one step before the layer ends, so a stepped volume never stops on a
    sounding step."""
    return [[0.0, peak], [max(length - FRAME, 0.01), 0.0]]


def _gate(length):
    """Full volume held, then down to silence over the last steps before the layer ends."""
    return [[0.0, 1.0, 'hold'], [max(length - 2 * FRAME, 0.0), 1.0], [max(length - FRAME, 0.01), 0.0]]


def _on(length):
    """A triangle's envelope: it has no volume control on the hardware, so it sounds until it stops."""
    return [[0.0, 1.0, 'hold'], [length, 1.0]]


def _console(p):
    model = {1: 'nes', 2: 'famicom'}.get(int(round(p['console'])))
    return {'console': {'model': model}} if model else {}


def _base(default, lo, hi, cents=25.0):
    return Param('base_hz', default, lo, hi, 'Hz', vary='cents', amount=cents, help='the main pitch')


def _length(default, lo, hi, name='length', help='total length'):
    return Param(name, default, lo, hi, 's', vary='pct', amount=6.0, help=help)


DUTY = Param('duty', 0.5, 0.125, 0.75, '', help='pulse duty, snapped to 12.5, 25, 50 or 75 %')
CONSOLE = Param('console', 2.0, 0.0, 2.0, '', help='output filter: 0 none, 1 NES (high-pass at 90 and 440 Hz: '
                                                     'thinner and peakier), 2 Famicom (high-pass at 37 Hz)')


def _duty(default):
    return Param('duty', default, 0.125, 0.75, '', help='pulse duty, snapped to 12.5, 25, 50 or 75 %')


# ---- jump -----------------------------------------------------------------------------------------------------------

def _jump(p):
    hz, length = p['base_hz'], p['length']
    top = hz * _st(p['sweep'])
    pitch = [[0.0, hz, 'exp'], [0.6 * length, top]]
    return {'layers': [_layer(0.0, length, 'pulse', pitch, _decay(length), p['duty'])], 'chain': _console(p)}


register(Recipe(
    name='retro.jump', category='retro', summary='a jump: a pulse sliding up as it fades',
    params=(_base(300.0, 100.0, 800.0, 30.0),
            Param('sweep', 12.0, 3.0, 24.0, 'semitones', help='how far the pitch slides up'),
            _length(0.22, 0.08, 0.5), _duty(0.5), CONSOLE),
    build=_jump, rules=BASE_RULES))


# ---- coin -----------------------------------------------------------------------------------------------------------

def _coin(p):
    hz, n, tail = p['base_hz'], p['note'], p['tail']
    notes = ((0.0, hz, n), (n, hz * _st(7), n), (2 * n, hz * 2, tail))
    layers = []
    for i, (start, pitch, length) in enumerate(notes):
        env = _decay(length) if i == 2 else [[0.0, 1.0, 'hold'], [length, 1.0]]
        layers.append(_layer(start, length, 'pulse', pitch, env, p['duty']))
    return {'layers': layers, 'chain': _console(p)}


register(Recipe(
    name='retro.coin', category='retro', summary='a pickup: three quick notes up, root, fifth and octave',
    params=(_base(1046.5, 400.0, 2500.0, 20.0),
            _length(0.045, 0.03, 0.1, 'note', 'length of each of the first two notes'),
            _length(0.25, 0.1, 0.6, 'tail', 'ring of the last note'), _duty(0.25), CONSOLE),
    build=_coin, rules=BASE_RULES))


# ---- powerup --------------------------------------------------------------------------------------------------------

def _powerup(p):
    hz, length, rise, rate = p['base_hz'], p['length'], p['rise'], p['rate']
    count = max(3, int(length * rate))
    pattern = (0, 4, 7)
    points = []
    for k in range(count):
        offset = rise * k / count + pattern[k % 3]
        points.append([round(k / rate, 6), hz * _st(offset), 'hold'])
    end = count / rate
    env = [[0.0, 1.0], [0.7 * end, 0.85], [end - FRAME, 0.0]]
    gate = _gate(end)
    bass = [[t, f / 2, c] for t, f, c in points]
    return {'layers': [_layer(0.0, end, 'pulse', points, env, p['duty']),
                       _layer(0.0, end, 'triangle', bass, gate, gain_db=-6.0)], 'chain': _console(p)}


register(Recipe(
    name='retro.powerup', category='retro', summary='a power-up: a fast arpeggio climbing as it plays',
    params=(_base(262.0, 130.0, 700.0, 20.0), _length(0.6, 0.3, 1.2),
            Param('rise', 12.0, 5.0, 24.0, 'semitones', help='how far the arpeggio climbs over its length'),
            Param('rate', 20.0, 8.0, 30.0, 'notes/s', help='notes a second (each held a whole number of steps)'),
            _duty(0.5), CONSOLE),
    build=_powerup, rules=BASE_RULES + (TRI_RULE,)))


# ---- shoot ----------------------------------------------------------------------------------------------------------

def _shoot(p):
    length = p['length']
    pitch = [[0.0, p['start_hz'], 'exp'], [length, min(p['end_hz'], 0.7 * p['start_hz'])]]    # a shot always falls
    burst = 0.03
    return {'layers': [_layer(0.0, length, 'pulse', pitch, _decay(length), p['duty']),
                       _layer(0.0, burst, 'noise', chip.NOISE_RATES[1], _decay(burst), mode='short',
                              gain_db=p['noise_db'])],
            'chain': _console(p)}


register(Recipe(
    name='retro.shoot', category='retro', summary='a shot: a thin pulse diving down with a burst of noise',
    params=(Param('start_hz', 1800.0, 600.0, 6000.0, 'Hz', vary='cents', amount=30.0, help='pitch at the start'),
            Param('end_hz', 220.0, 80.0, 800.0, 'Hz', vary='cents', amount=30.0, help='pitch at the end'),
            _length(0.16, 0.06, 0.4), _duty(0.125),
            Param('noise_db', -8.0, -40.0, 0.0, 'dB', help='level of the noise burst'), CONSOLE),
    build=_shoot, rules=BASE_RULES + (NOISE_RULE,)))


# ---- hit ------------------------------------------------------------------------------------------------------------

def _hit(p):
    hz, length = p['base_hz'], p['length']
    rate = chip.NOISE_RATES[int(round(p['noise_index']))]
    drop = [[0.0, hz, 'exp'], [0.8 * length, hz * 0.5]]
    return {'layers': [_layer(0.0, length, 'noise', rate, _decay(length)),
                       _layer(0.0, 0.8 * length, 'pulse', drop, _decay(0.8 * length, 0.8), p['duty'], gain_db=-4.0)],
            'chain': _console(p)}


register(Recipe(
    name='retro.hit', category='retro', summary='getting hurt: a crunch of noise over a falling pulse',
    params=(_base(320.0, 120.0, 900.0, 30.0), _length(0.09, 0.05, 0.4),
            Param('noise_index', 12.0, 0.0, 15.0, '', help='noise clock rate, 0 (highest) to 15 (lowest)'),
            _duty(0.5), CONSOLE),
    build=_hit, rules=BASE_RULES + (NOISE_RULE,)))


# ---- explosion ------------------------------------------------------------------------------------------------------

def _explosion(p):
    length = p['length']
    first, last = int(round(p['start_index'])), int(round(p['end_index']))
    last = max(last, first + 1)                                       # it always moves down the rates
    steps = last - first + 1
    points = [[round(length * i / steps, 6), chip.NOISE_RATES[first + i], 'hold'] for i in range(steps)]
    boom = [[0.0, 110.0, 'exp'], [0.5 * length, 40.0]]
    return {'layers': [_layer(0.0, length, 'noise', points, _decay(length)),
                       _layer(0.0, 0.5 * length, 'triangle', boom, _on(0.5 * length), gain_db=-6.0)],
            'chain': _console(p)}


register(Recipe(
    name='retro.explosion', category='retro', summary='an explosion: noise falling through the clock rates as it fades',
    params=(_length(0.9, 0.3, 2.0),
            Param('start_index', 3.0, 0.0, 9.0, '', help='noise clock rate at the start (0 highest)'),
            Param('end_index', 13.0, 6.0, 15.0, '', help='noise clock rate at the end (15 lowest)'), CONSOLE),
    build=_explosion, rules=(VOLUME_RULE, STEP_RULE, NOISE_RULE, TRI_RULE, CONSOLE_RULE)))


# ---- blip -----------------------------------------------------------------------------------------------------------

def _blip(p):
    return {'layers': [_layer(0.0, p['length'], 'pulse', p['base_hz'], _decay(p['length']), p['duty'])],
            'chain': _console(p)}


register(Recipe(
    name='retro.blip', category='retro', summary='a menu blip: one short pulse note',
    params=(_base(1320.0, 300.0, 4000.0, 25.0), _length(0.07, 0.03, 0.2), _duty(0.5), CONSOLE),
    build=_blip, rules=BASE_RULES))


# ---- extra life -----------------------------------------------------------------------------------------------------

def _extra_life(p):
    hz, n, tail = p['base_hz'], p['note'], p['tail']
    steps = (0, 2, 4, 7, 9, 12)
    points = [[round(i * n, 6), hz * _st(s), 'hold'] for i, s in enumerate(steps)]
    end = len(steps) * n + tail
    env = [[0.0, 1.0, 'hold'], [(len(steps) - 1) * n, 1.0], [end - FRAME, 0.0]]
    bass = [[t, f / 2, c] for t, f, c in points]
    return {'layers': [_layer(0.0, end, 'pulse', points, env, p['duty']),
                       _layer(0.0, end, 'triangle', bass, _on(end), gain_db=-8.0)],
            'chain': _console(p)}


register(Recipe(
    name='retro.extra_life', category='retro', summary='an extra life: a quick pentatonic run up an octave',
    params=(_base(523.0, 250.0, 1200.0, 20.0), _length(0.06, 0.03, 0.12, 'note', 'length of each note'),
            _length(0.2, 0.05, 0.5, 'tail', 'ring of the last note'), _duty(0.25), CONSOLE),
    build=_extra_life, rules=BASE_RULES + (TRI_RULE,)))
