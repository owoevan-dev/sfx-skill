"""Ambience loops: rain from populations of bubbles (drops source, van den Doel 2004) over a wash of filtered noise, and
wind from the wake and Aeolian tone of air passing obstacles (wind source, Selfridge, Moffat and Reiss 2017). Every
layer lasts the whole loop, so each period joins its own start (synth.render_loop). The bubble and wind physics and
the parameter ranges named in the rules come from those papers; the washes, the gusts and the default values are this
skill's own design."""
from . import register
from .base import Param, Recipe

VDD = 'van den Doel (2004), "Physically-based models for liquid sounds", ICAD'
SMR = 'Selfridge, Moffat and Reiss (2017), "Sound synthesis of objects swinging through air using physical models"'

RAIN_RATE_RULE = ('up to about 1000 bubbles per second sounds like dripping or light rain, up to 10 000 dense with '
                  'single drops still heard, up to 100 000 blended like heavy rain; more makes no audible difference',
                  VDD)
RAIN_SIZE_RULE = ('bubble rates falling as 1/r^gamma with gamma above about 5 sound very much like rain (0 to 3 like '
                  'streams); a smaller gain exponent sounds closer, a larger one more distant', VDD)
WAKE_RULE = ('the wake of an obstacle gives wide-band noise from its Aeolian frequency up, growing with the eighth '
             'power of the air speed; both move up in proportion to the speed', SMR)
TONE_RULE = ('a thin obstacle sings a narrow Aeolian tone f = St u / d, growing with the sixth power of '
             'the speed, with a drag tone an octave up', SMR)


def _bed(seconds, source, gain_db=0.0, spread=1.0, flt=None):
    layer = {'source': source, 'start': 0.0, 'length': float(seconds), 'gain_db': float(gain_db),
             'spread': float(spread)}
    if flt:
        layer['filter'] = flt
    return layer


def _wash(seconds, low_hz, high_hz, gain_db):
    """Pink noise through a two-pole band-pass whose -3 dB points are low_hz and high_hz: centred on their geometric
    mean, q = centre / (high_hz - low_hz)."""
    centre = (low_hz * high_hz) ** 0.5
    q = centre / (high_hz - low_hz)
    return _bed(seconds, {'type': 'noise', 'color': 'pink'}, gain_db, 1.0,
                {'type': 'bandpass', 'cutoff': round(centre, 3), 'q': round(max(q, 0.1), 4)})


# ---- light rain -----------------------------------------------------------------------------------------------------

def _rain_light(p):
    s = p['seconds']
    drops = {'type': 'drops', 'rate': p['rate'], 'r_min_mm': 0.2, 'r_max_mm': p['drop_mm'], 'size_slope': 5.0,
             'gain_slope': p['distance'], 'depth': 10.0, 'rise': 0.1, 'rise_cutoff': 0.9}
    return {'layers': [_bed(s, drops, 0.0, 0.8), _wash(s, 500.0, 6000.0, p['wash_db'])]}


register(Recipe(
    name='ambience.rain_light', category='ambience', use='loop',
    summary='light rain on water and wet ground: single drops over a soft wash',
    params=(Param('rate', 1500.0, 200.0, 8000.0, '/s', vary='pct', amount=10.0, help='bubbles per second'),
            Param('drop_mm', 3.0, 1.0, 6.0, 'mm', vary='pct', amount=5.0, help='largest bubble radius'),
            Param('distance', 1.5, 0.8, 2.5, '', help='gain exponent: lower sounds closer, higher further away'),
            Param('wash_db', -12.0, -40.0, 0.0, 'dB', help='level of the wash of distant rain')),
    build=_rain_light, rules=(RAIN_RATE_RULE, RAIN_SIZE_RULE)))


# ---- heavy rain -----------------------------------------------------------------------------------------------------

def _rain_heavy(p):
    s = p['seconds']
    drops = {'type': 'drops', 'rate': p['rate'], 'r_min_mm': 0.2, 'r_max_mm': p['drop_mm'], 'size_slope': 5.0,
             'gain_slope': p['distance'], 'depth': 10.0, 'rise': 0.1, 'rise_cutoff': 0.9}
    rumble = _bed(s, {'type': 'noise', 'color': 'brown'}, p['rumble_db'], 1.0,
                  {'type': 'lowpass', 'cutoff': 300.0, 'q': 0.7071})
    return {'layers': [_bed(s, drops, 0.0, 1.0), _wash(s, 300.0, 8000.0, p['wash_db']), rumble]}


register(Recipe(
    name='ambience.rain_heavy', category='ambience', use='loop',
    summary='heavy rain: a dense, blended downpour with a low rumble',
    params=(Param('rate', 30000.0, 8000.0, 100000.0, '/s', vary='pct', amount=10.0, help='bubbles per second'),
            Param('drop_mm', 4.0, 1.5, 8.0, 'mm', vary='pct', amount=5.0, help='largest bubble radius'),
            Param('distance', 1.5, 0.8, 2.5, '', help='gain exponent: lower sounds closer, higher further away'),
            Param('wash_db', -6.0, -30.0, 6.0, 'dB', help='level of the wash of distant rain'),
            Param('rumble_db', -12.0, -40.0, 0.0, 'dB', help='level of the low rumble')),
    build=_rain_heavy, rules=(RAIN_RATE_RULE, RAIN_SIZE_RULE)))


# ---- breeze ---------------------------------------------------------------------------------------------------------

def _breeze(p):
    s = p['seconds']
    wind = {'type': 'wind', 'band_hz': p['band_hz'], 'turbulence': p['turbulence'], 'gust_hz': p['gust_hz'],
            'whistle_hz': 0.0}
    return {'layers': [_bed(s, wind, 0.0, 1.0)]}


register(Recipe(
    name='ambience.breeze', category='ambience', use='loop',
    summary='a breeze: soft, wide air that rises and falls gently',
    params=(Param('band_hz', 250.0, 80.0, 1000.0, 'Hz', vary='cents', amount=100.0,
                  help='where the wake noise starts at the mean speed (smaller obstacles: higher)'),
            Param('turbulence', 0.12, 0.03, 0.3, '', help='spread of the speed over its mean'),
            Param('gust_hz', 0.15, 0.03, 0.6, 'Hz', help='how quickly the speed changes')),
    build=_breeze, rules=(WAKE_RULE,)))


# ---- gusty wind -----------------------------------------------------------------------------------------------------

def _wind_gusty(p):
    s = p['seconds']
    wind = {'type': 'wind', 'band_hz': p['band_hz'], 'turbulence': p['turbulence'], 'gust_hz': p['gust_hz'],
            'whistle_hz': p['whistle_hz'], 'whistle_db': p['whistle_db'], 'whistle_q': 60.0}
    return {'layers': [_bed(s, wind, 0.0, 1.0)]}


register(Recipe(
    name='ambience.wind_gusty', category='ambience', use='loop',
    summary='gusty wind: strong air in gusts, with a whistle that rises with each gust',
    params=(Param('band_hz', 120.0, 50.0, 600.0, 'Hz', vary='cents', amount=100.0,
                  help='where the wake noise starts at the mean speed (smaller obstacles: higher)'),
            Param('turbulence', 0.25, 0.1, 0.5, '', help='spread of the speed over its mean'),
            Param('gust_hz', 0.3, 0.05, 1.0, 'Hz', help='how quickly the speed changes'),
            Param('whistle_hz', 600.0, 200.0, 2500.0, 'Hz', vary='cents', amount=150.0,
                  help='the whistle at the mean speed'),
            Param('whistle_db', -14.0, -40.0, 0.0, 'dB', help='level of the whistle against the wake')),
    build=_wind_gusty, rules=(WAKE_RULE, TONE_RULE)))
