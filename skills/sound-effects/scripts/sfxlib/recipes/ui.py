"""User-interface recipes: twelve earcons for menus and buttons.

The rules each recipe encodes are listed with it (sfx.py recipes --name ...) and gathered in references/ui-sounds.md.
The parameter values themselves (frequencies, times, levels) are this skill's own sound design, tuned during
development against measurements of CC0 game sound libraries: the game's developer judges them by ear on the
audition page."""
from . import register
from .base import Param, Recipe
from .parts import ROUGH_RULE, rough as rough_tremolo

MD_APPLY = 'Material Design 2, "Applying sound to UI"'
MD_ATTR = 'Material Design 2, "Sound attributes"'
BWE = 'Brewster, Wright and Edwards, "Guidelines for the Creation of Earcons"'
BWE_EDW = 'Brewster, Wright and Edwards, citing Edworthy, Loxley and Dennis (1991)'

RANGE_RULE = ('pitched content between 150 Hz and 5 kHz, where it is not easily masked and most listeners hear it',
              BWE)
SHORT_RULE = ('a sound of one or two notes keeps each note at 0.03 s or longer', BWE)
ACCENT_RULE = ('the first note slightly louder and the last note longer, so the figure sounds complete', BWE)
FREQUENT_RULE = ('a frequent primary sound stays simple and understated, fit to be heard often', MD_APPLY)
SOFT_RULE = ('a low-priority sound uses a softer, quieter timbre', MD_ATTR)
UP_RULE = ('upward motion for starting, opening and positive results', MD_ATTR)
DOWN_RULE = ('downward motion for ending and closing', MD_ATTR)
ATONAL_RULE = ('atonal (noise) sound for motion transitions and a feel of touch', MD_ATTR)
DRY_RULE = ('reverb and delay used sparingly; space is left to the game engine', MD_ATTR)


def _st(semitones):
    return 2 ** (semitones / 12)


def _cutoff(hz, brightness):
    """Low-pass cut-off for a note: just above the fundamental at brightness 0, eleven harmonics up at 1."""
    return min(16000.0, hz * (1.5 + 10.0 * brightness))


def _note(start, length, hz, brightness, gain_db=0.0, attack=0.0015, bend_cents=0.0, bend_time=0.012,
          wave='triangle'):
    pitch = hz if not bend_cents else [[0.0, hz * 2 ** (bend_cents / 1200), 'exp'], [bend_time, hz]]
    return {'source': {'type': 'osc', 'wave': wave}, 'start': start, 'length': length, 'pitch': pitch,
            'env': {'attack': attack, 'decay': max(length - attack, 0.005), 'curve': 'db'},
            'filter': {'type': 'lowpass', 'cutoff': _cutoff(hz, brightness), 'q': 0.7071}, 'gain_db': gain_db}


def _held(start, length, hz, brightness, gain_db=0.0, hold=0.6, attack=0.004, wave='triangle'):
    """A note held for the fraction `hold` of its length, then fading: a sustained note, where `_note` only decays."""
    held = max(length * hold - attack, 0.0)
    return {'source': {'type': 'osc', 'wave': wave}, 'start': start, 'length': length, 'pitch': hz,
            'env': {'attack': attack, 'hold': held, 'decay': max(length - attack - held, 0.005), 'curve': 'db'},
            'filter': {'type': 'lowpass', 'cutoff': _cutoff(hz, brightness), 'q': 0.7071}, 'gain_db': gain_db}


def _bell(start, length, hz, brightness, gain_db=0.0, ratio=1.4):
    """Two-operator FM with an inharmonic ratio and an index that decays with the note."""
    peak = 0.5 + 2.5 * brightness
    return {'source': {'type': 'fm', 'ratio': ratio, 'index': [[0.0, peak, 'db'], [length, 0.3]]},
            'start': start, 'length': length, 'pitch': hz,
            'env': {'attack': 0.001, 'decay': length - 0.001, 'curve': 'db'}, 'gain_db': gain_db}


def _click(start, centre_hz, gain_db, length=0.004):
    """A short band-passed noise burst: the touch of a key."""
    return {'source': {'type': 'noise', 'color': 'white'}, 'start': start, 'length': length,
            'env': [[0.0, 1.0, 'db'], [length, 0.0]],
            'filter': {'type': 'bandpass', 'cutoff': min(centre_hz, 9000.0), 'q': 1.5}, 'gain_db': gain_db}


def _base(default, lo, hi, cents):
    return Param('base_hz', default, lo, hi, 'Hz', vary='cents', amount=cents, help='the main pitch')


def _brightness(default):
    return Param('brightness', default, 0.0, 1.0, '', vary='abs', amount=0.06,
                 help='harmonic content: 0 nearly a sine, 1 bright')


# ---- tick -----------------------------------------------------------------------------------------------------------

def _tick(p):
    hz, length, b = p['base_hz'], p['length'], p['brightness']
    body = _note(0.0, length, hz, b, attack=0.0005)
    body['pitch'] = [[0.0, hz * 1.25, 'exp'], [0.006, hz]]
    return {'layers': [body, _click(0.0, hz * 1.6, -14.0)]}


register(Recipe(
    name='ui.tick', category='ui', trim_db=-6.0,
    summary='a very short tick for frequent, low-priority actions (scrolling, stepping through a list)',
    params=(_base(2000.0, 800.0, 4000.0, 40.0),
            Param('length', 0.035, 0.03, 0.08, 's', vary='pct', amount=8.0, help='total length'),
            _brightness(0.4)),
    build=_tick, rules=(FREQUENT_RULE, SOFT_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- hover ----------------------------------------------------------------------------------------------------------

def _hover(p):
    return {'layers': [_note(0.0, p['length'], p['base_hz'], p['brightness'], attack=0.004, bend_cents=-30.0,
                             bend_time=0.015)]}


register(Recipe(
    name='ui.hover', category='ui', trim_db=-9.0,
    summary='the softest cue, for the pointer moving onto an item',
    params=(_base(1400.0, 600.0, 3000.0, 30.0),
            Param('length', 0.06, 0.03, 0.15, 's', vary='pct', amount=8.0, help='total length'),
            _brightness(0.2)),
    build=_hover,
    rules=(SOFT_RULE, ('a softer attack is more ambient', MD_ATTR), FREQUENT_RULE, SHORT_RULE, RANGE_RULE)))


# ---- select ---------------------------------------------------------------------------------------------------------

def _select(p):
    hz, length, b = p['base_hz'], p['length'], p['brightness']
    return {'layers': [_note(0.0, length, hz, b, bend_cents=-50.0, bend_time=0.012), _click(0.0, hz * 2.0, -18.0)]}


register(Recipe(
    name='ui.select', category='ui', trim_db=-3.0,
    summary='choosing an item: one clear note',
    params=(_base(990.0, 300.0, 3000.0, 30.0),
            Param('length', 0.09, 0.04, 0.25, 's', vary='pct', amount=6.0, help='total length'),
            _brightness(0.5)),
    build=_select,
    rules=(('tonal sound for a state change', MD_ATTR), SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- confirm and back -----------------------------------------------------------------------------------------------

def _two_notes(p, direction):
    hz, n, b = p['base_hz'], p['note_length'], p['brightness']
    second = hz * _st(direction * p['interval'])
    return {'layers': [_note(0.0, n + 0.012, hz, b, gain_db=0.0),
                       _note(n, n * 1.6, second, b, gain_db=-2.0)]}


def _interval(default, hi):
    return Param('interval', default, 2.0, hi, 'semitones', help='the step from the first note to the second')


def _note_length(default, lo, hi):
    return Param('note_length', default, lo, hi, 's', vary='pct', amount=6.0,
                 help='length of the first note; the last is 1.6 times as long')


register(Recipe(
    name='ui.confirm', category='ui', trim_db=0.0,
    summary='a positive confirmation: two notes rising',
    params=(_base(660.0, 300.0, 2000.0, 30.0), _interval(7.0, 12.0), _note_length(0.07, 0.04, 0.2),
            _brightness(0.5)),
    build=lambda p: _two_notes(p, +1),
    rules=(UP_RULE, ACCENT_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))

register(Recipe(
    name='ui.back', category='ui', trim_db=-1.0,
    summary='going back or cancelling: two notes falling',
    params=(_base(740.0, 300.0, 2400.0, 30.0), _interval(5.0, 12.0), _note_length(0.06, 0.04, 0.2),
            _brightness(0.4)),
    build=lambda p: _two_notes(p, -1),
    rules=(DOWN_RULE, ACCENT_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- error ----------------------------------------------------------------------------------------------------------

def _error(p):
    hz, n, b, rough = p['base_hz'], p['pulse_length'], p['brightness'], p['roughness']
    layers = []
    for start, pitch in ((0.0, hz), (n + 0.035, hz * _st(-1))):
        for detune in (0.0, 100.0 * rough):
            layers.append({'source': {'type': 'osc', 'wave': 'square'}, 'start': start, 'length': n,
                           'pitch': pitch * 2 ** (detune / 1200),
                           'env': {'attack': 0.002, 'decay': n - 0.002, 'curve': 'db'},
                           'filter': {'type': 'lowpass', 'cutoff': pitch * (3.0 + 12.0 * b), 'q': 0.7071},
                           'gain_db': -3.0})
            if rough > 0:                           # a flutter of 40 to 80 Hz, where roughness is most aversive
                layers[-1]['tremolo'] = rough_tremolo(40.0 + 40.0 * rough, 0.9 * rough)
    # short pulses of a square start on one polarity and leave a little DC; a low cut takes it out
    return {'layers': layers, 'chain': {'eq': {'low_cut_hz': 60.0}}}


register(Recipe(
    name='ui.error', category='ui', trim_db=0.0,
    summary='refused or not possible: two low, rough pulses',
    params=(_base(220.0, 160.0, 600.0, 25.0),
            Param('roughness', 0.6, 0.0, 1.0, '', vary='abs', amount=0.06,
                  help='how harsh: paired tones detuned by up to a semitone, and a fast flutter of the level'),
            Param('pulse_length', 0.07, 0.04, 0.15, 's', vary='pct', amount=6.0, help='length of each pulse'),
            _brightness(0.5)),
    build=_error,
    rules=(('irregular harmonics and a distinct rhythm draw attention without loudness', BWE_EDW), ROUGH_RULE,
           ('a tone can tell users that something is not available', MD_APPLY), SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- toggles --------------------------------------------------------------------------------------------------------

def _toggle(p, up):
    hz, s, length, b = p['base_hz'], p['sweep'], p['length'], p['brightness']
    lo, hi = hz, hz * _st(s)
    start, end = (lo, hi) if up else (hi, lo)
    body = _note(0.0, length, hz, b, attack=0.001)
    body['pitch'] = [[0.0, start, 'exp'], [0.6 * length, end]]
    body['filter']['cutoff'] = _cutoff(max(start, end), b)
    return {'layers': [body, _click(0.0, 4000.0, -16.0)]}


def _toggle_params(brightness):
    return (_base(700.0, 300.0, 2500.0, 30.0),
            Param('sweep', 7.0, 2.0, 12.0, 'semitones', help='how far the pitch slides'),
            Param('length', 0.07, 0.04, 0.15, 's', vary='pct', amount=6.0, help='total length'),
            _brightness(brightness))


register(Recipe(
    name='ui.toggle_on', category='ui', trim_db=-3.0, summary='a switch turned on: a quick upward slide',
    params=_toggle_params(0.5), build=lambda p: _toggle(p, True),
    rules=(UP_RULE, ATONAL_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))

register(Recipe(
    name='ui.toggle_off', category='ui', trim_db=-3.0, summary='a switch turned off: a quick downward slide',
    params=_toggle_params(0.35), build=lambda p: _toggle(p, False),
    rules=(DOWN_RULE, ATONAL_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- open and close -------------------------------------------------------------------------------------------------

def _sweep_noise(length, b, rising):
    top = 2500.0 + 4000.0 * b
    a, z = (600.0, top) if rising else (top, 600.0)
    env = ([[0.0, 0.0, 'cos'], [0.001, 0.2, 'db'], [0.6 * length, 1.0, 'db'], [length, 0.0]] if rising else
           [[0.0, 0.0, 'cos'], [0.001, 1.0, 'db'], [length, 0.0]])
    return {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': length, 'env': env,
            'filter': {'type': 'bandpass', 'cutoff': [[0.0, a, 'exp'], [0.85 * length, z]], 'q': 1.2}}


def _glide(start, length, f0, f1, gain_db):
    return {'source': {'type': 'osc', 'wave': 'sine'}, 'start': start, 'length': length,
            'pitch': [[0.0, f0, 'exp'], [0.8 * length, f1]],
            'env': {'attack': 0.003, 'decay': length - 0.003, 'curve': 'db'}, 'gain_db': gain_db}


def _open(p):
    hz, length, b = p['base_hz'], p['length'], p['brightness']
    return {'layers': [_sweep_noise(length, b, True), _glide(0.0, length, hz, hz * 1.5, p['tone_db'])]}


def _close(p):
    hz, length, b = p['base_hz'], p['length'], p['brightness']
    tuck = {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 0.8 * length, 'length': 0.04, 'pitch': hz * 0.75,
            'env': {'attack': 0.002, 'decay': 0.038, 'curve': 'db'}, 'gain_db': -8.0}
    return {'layers': [_sweep_noise(length, b, False), _glide(0.0, length, hz * 1.5, hz, p['tone_db']), tuck]}


def _panel_params(length):
    return (_base(500.0, 200.0, 1500.0, 30.0),
            Param('length', length, 0.1, 0.6, 's', vary='pct', amount=6.0, help='length of the sweep'),
            _brightness(0.5),
            Param('tone_db', -10.0, -40.0, 0.0, 'dB', help='level of the gliding tone under the noise'))


register(Recipe(
    name='ui.open', category='ui', trim_db=-4.0, summary='a panel or menu opening: an airy rise',
    params=_panel_params(0.22), build=_open,
    rules=(ATONAL_RULE, UP_RULE, RANGE_RULE, DRY_RULE)))

register(Recipe(
    name='ui.close', category='ui', trim_db=-4.0, summary='a panel or menu closing: a falling sweep that settles',
    params=_panel_params(0.2), build=_close,
    rules=(ATONAL_RULE, DOWN_RULE, RANGE_RULE, DRY_RULE)))


# ---- notify ---------------------------------------------------------------------------------------------------------

def _notify(p):
    hz, n, decay, b = p['base_hz'], p['note_length'], p['decay'], p['brightness']
    second = hz * _st(p['interval'])
    layers = []
    for start, pitch, length, gain in ((0.0, hz, decay, 2.0), (n, second, decay * 1.5, 0.0)):
        layers.append(_bell(start, length, pitch, b, gain_db=gain - 2.0))
        layers.append({'source': {'type': 'osc', 'wave': 'sine'}, 'start': start, 'length': length, 'pitch': pitch,
                       'env': {'attack': 0.002, 'decay': length - 0.002, 'curve': 'db'}, 'gain_db': gain - 8.0})
    return {'layers': layers}


register(Recipe(
    name='ui.notify', category='ui', trim_db=0.0, summary='a notification: two bright, bell-like notes',
    params=(_base(1320.0, 600.0, 2400.0, 25.0),
            Param('interval', 5.0, 2.0, 9.0, 'semitones', help='the step up to the second note'),
            Param('note_length', 0.12, 0.06, 0.3, 's', vary='pct', amount=6.0, help='time from the first note to '
                  'the second'),
            Param('decay', 0.35, 0.15, 1.0, 's', vary='pct', amount=8.0, help='ring of the first note; the last '
                  'rings 1.5 times as long'),
            _brightness(0.6)),
    build=_notify,
    rules=(('brighter timbres for notifications, which must be heard in busy places', MD_ATTR),
           ('notifications are shorter than hero sounds and fit for repetition', MD_APPLY),
           ACCENT_RULE, SHORT_RULE, RANGE_RULE, DRY_RULE)))


# ---- success --------------------------------------------------------------------------------------------------------

def _success(p):
    hz, n, b, sparkle, ring = p['base_hz'], p['note_length'], p['brightness'], p['sparkle'], p['ring']
    layers = []
    steps = (0, 4, 7, 12)
    for i, step in enumerate(steps):
        pitch = hz * _st(step)
        last = i == len(steps) - 1
        length = ring if last else n * 1.6
        accent = 2.0 if i == 0 else 0.0
        layers.append(_held(i * n, length, pitch, b, gain_db=accent - 3.0, hold=0.45 if last else 0.6))
        layers.append(_bell(i * n, min(length, 0.3), pitch, b, gain_db=accent - 12.0))
    # the warmth: the root an octave down and its fifth, held under the last note
    t_last = (len(steps) - 1) * n
    layers.append(_held(t_last, ring, hz / 2, 0.2, gain_db=p['warmth_db'], hold=0.45))
    layers.append(_held(t_last, ring, hz * _st(7) / 2, 0.2, gain_db=p['warmth_db'] - 3.0, hold=0.45))
    return {'layers': layers,
            'chain': {'delay': {'time': 0.09, 'feedback': 0.25, 'mix': 0.08 + 0.22 * sparkle, 'damping_hz': 6000.0}}}


register(Recipe(
    name='ui.success', category='ui', trim_db=0.0,
    summary='completion or a reward: a short rising arpeggio of held notes over a warm chord that rings out',
    params=(_base(523.25, 300.0, 1500.0, 25.0),
            Param('note_length', 0.1, 0.0825, 0.25, 's', vary='pct', amount=5.0, help='time from note to note'),
            Param('ring', 0.7, 0.3, 1.5, 's', vary='pct', amount=5.0, help='length of the last note and its chord'),
            _brightness(0.5),
            Param('warmth_db', -6.0, -40.0, 0.0, 'dB', vary='db', amount=1.0,
                  help='level of the chord an octave down under the last note'),
            Param('sparkle', 0.5, 0.0, 1.0, '', vary='abs', amount=0.08, help='level of the short echo')),
    build=_success,
    rules=(('hero sounds celebrate a significant positive action, occur rarely and stay consistent', MD_APPLY),
           UP_RULE, ACCENT_RULE, ('notes of a longer figure not shorter than 0.0825 s', BWE), RANGE_RULE,
           ('a short echo only: effects used sparingly', MD_ATTR))))
