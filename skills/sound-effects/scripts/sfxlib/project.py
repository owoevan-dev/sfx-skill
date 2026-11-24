"""The project file, schema sound-effects/1: validation (every error at once, with the nearest known field name) and
defaults. references/project-format.md describes every field; this module is the check of it."""
import copy
import difflib
import json
import math
import re
from pathlib import Path

from . import chip, curves, dsp, modal, recipes
from .filters import KINDS as FILTER_KINDS
from .level import DEFAULT_LIMIT_DB
from .noise import COLORS
from .osc import WAVES

SCHEMA = 'sound-effects/1'
CATEGORIES = ('ui', 'retro', 'scifi', 'magic', 'impact', 'foley', 'ambience')
USES = ('one_shot', 'loop')
ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
MAX_LENGTH_S = 30.0
MAX_NOTES = 64
MAX_LOOP_S = 60.0
DEFAULT_LOOP_S = 12.0      # this skill's default loop length (keeps the audition page small)
LAYER_VARY = {'pitch_cents': 1200.0, 'time_pct': 50.0, 'cutoff_cents': 2400.0, 'gain_db': 12.0, 'scatter_s': 30.0}
VARY_LIMITS = {'cents': 1200.0, 'pct': 50.0, 'db': 12.0}


class ProjectError(Exception):
    """The project does not validate; .errors lists every problem as 'path: message'."""

    def __init__(self, errors):
        super().__init__('\n'.join(errors))
        self.errors = list(errors)


def _join(path, key):
    return f'{path}.{key}' if path else str(key)


class _Checker:
    def __init__(self):
        self.errors = []

    def err(self, path, message):
        self.errors.append(f'{path or "project"}: {message}')

    def fields(self, obj, path, allowed, required=()):
        if not isinstance(obj, dict):
            self.err(path, 'expected an object ({...})')
            return False
        for key in obj:
            if key not in allowed:
                near = difflib.get_close_matches(str(key), list(allowed), n=1, cutoff=0.6)
                hint = f"; did you mean '{near[0]}'?" if near else f"; known fields: {', '.join(sorted(allowed))}"
                self.err(_join(path, key), 'unknown field' + hint)
        for key in required:
            if key not in obj:
                self.err(path, f"missing required field '{key}'")
        return True

    def number(self, obj, key, path, lo, hi, default=None, integer=False, required=False):
        if key not in obj:
            if required:
                self.err(path, f"missing required field '{key}'")
            return default
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.err(_join(path, key), f'expected a number, not {value!r}')
            return default
        if integer and int(value) != value:
            self.err(_join(path, key), f'expected a whole number, not {value}')
            return default
        if not lo <= value <= hi:
            self.err(_join(path, key), f'{value} is outside {lo:g} to {hi:g}')
            return default
        return int(value) if integer else float(value)

    def choice(self, obj, key, path, choices, default=None, required=False):
        if key not in obj:
            if required:
                self.err(path, f"missing required field '{key}'")
            return default
        value = obj[key]
        if value not in choices:
            near = difflib.get_close_matches(str(value), list(choices), n=1, cutoff=0.5)
            hint = f"; did you mean '{near[0]}'?" if near else ''
            self.err(_join(path, key), f"{value!r} is not one of {', '.join(choices)}{hint}")
            return default
        return value

    def curve(self, obj, key, path, lo, hi, required=False):
        if key not in obj:
            if required:
                self.err(path, f"missing required field '{key}'")
            return
        try:
            points = curves.normalize(obj[key])
        except ValueError as error:
            self.err(_join(path, key), str(error))
            return
        for t, v, _ in points:
            if not lo <= v <= hi:
                self.err(_join(path, key), f'value {v:g} at {t:g} s is outside {lo:g} to {hi:g}')
                return
            if t > MAX_LOOP_S:
                self.err(_join(path, key), f'time {t:g} s is past {MAX_LOOP_S:g} s')
                return


DROPS_FIELDS = {'rate', 'r_min_mm', 'r_max_mm', 'size_slope', 'gain_slope', 'depth', 'rise', 'rise_cutoff'}
WIND_FIELDS = {'band_hz', 'turbulence', 'gust_hz', 'whistle_hz', 'whistle_db', 'whistle_q'}
INSTRUMENT_FIELDS = {'soundfont', 'program', 'bank', 'drums', 'notes', 'cents'}
SURGE_FIELDS = {'patch', 'notes', 'macros', 'bend', 'bend_range', 'wheel', 'bpm', 'tail', 'take', 'cents'}
SURGE_PATCH_RE = re.compile(r'^[a-z0-9_]{1,64}(/[a-z0-9_]{1,64}){1,4}$')
BANK_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
SAMPLE_FIELDS = {'library', 'file', 'offset', 'cents', 'reverse', 'denoise', 'time_stretch'}
STRETCH_FIELDS = {'factor', 'method', 'keep_attack_ms'}
DENOISE_FIELDS = {'noise', 'reduce_db', 'over'}
TAKE_FIELDS = {'library', 'file', 'offset', 'gain_db', 'denoise'}
MAX_TAKES = 16
MAX_CENTS = 4800.0      # four octaves either way: this skill's choice (varispeed to 1/16 or 16 times)
SOURCE_FIELDS = ({'type', 'wave', 'duty', 'phase', 'ratio', 'index', 'color', 'mode', 'step_hz', 'levels', 'quantize',
                  'material', 't_d', 'modes', 'hardness', 'detune_cents', 'shape', 'ratios'}
                 | DROPS_FIELDS | WIND_FIELDS | INSTRUMENT_FIELDS | SAMPLE_FIELDS | SURGE_FIELDS)
SOURCE_TYPES = ('osc', 'fm', 'noise', 'chip', 'modal', 'drops', 'wind', 'instrument', 'sample', 'surge')
MAX_TAKE_GAIN_DB = 40.0


def _check_denoise(c, d, where):
    from .samples import MAX_SECONDS
    if not isinstance(d, dict):
        c.err(where, 'expected {"noise": [start, end], "reduce_db": 20, "over": 2}')
        return
    for key in d:
        if key not in DENOISE_FIELDS:
            c.err(_join(where, key), f"unknown field (known: {', '.join(sorted(DENOISE_FIELDS))})")
    noise = d.get('noise')
    if not (isinstance(noise, list) and len(noise) == 2 and all(isinstance(v, (int, float))
                                                                and not isinstance(v, bool) for v in noise)
            and 0 <= noise[0] and noise[1] - noise[0] >= 0.1 and noise[1] <= MAX_SECONDS):
        c.err(_join(where, 'noise'), f'{noise!r}: expected [start, end] in seconds of the recording, a stretch '
              f'that holds only its noise, at least 0.1 s long, within {MAX_SECONDS:g} s (sfx.py samples --map '
              'finds one)')
    c.number(d, 'reduce_db', where, 3.0, 40.0)
    c.number(d, 'over', where, 1.0, 6.0)


def _check_sample(c, src, path):
    from .samples import FILE_RE, LIBRARY_RE, MAX_SECONDS
    library = src.get('library')
    if not (isinstance(library, str) and LIBRARY_RE.match(library)):
        c.err(_join(path, 'library'), f'{library!r}: expected the id of a pack in the sample library (lower-case '
              'letters, digits and -; sfx.py samples lists them)')
    files = src.get('file')
    takes = files if isinstance(files, list) else [files]

    def take_ok(t):
        if isinstance(t, str):
            return bool(FILE_RE.match(t))
        if not (isinstance(files, list) and isinstance(t, dict) and set(t) <= TAKE_FIELDS):
            return False
        if 'library' in t and not (isinstance(t['library'], str) and LIBRARY_RE.match(t['library'])):
            return False
        off, gain = t.get('offset', 0.0), t.get('gain_db', 0.0)
        return (isinstance(t.get('file'), str) and bool(FILE_RE.match(t['file'])) and isinstance(off, (int, float))
                and not isinstance(off, bool) and 0 <= off <= MAX_SECONDS and isinstance(gain, (int, float))
                and not isinstance(gain, bool) and abs(gain) <= MAX_TAKE_GAIN_DB)
    if not 1 <= len(takes) <= MAX_TAKES or not all(take_ok(t) for t in takes):
        c.err(_join(path, 'file'), f'{files!r}: expected the name of a recording, or a list of 1 to {MAX_TAKES} takes '
              '(one for each variant in turn), each a name or {"file": name, "offset": seconds, "gain_db": dB, '
              '"denoise": {...}, "library": pack} for a take that starts at its own time in the recording, at its own level (within '
              f'{MAX_TAKE_GAIN_DB:g} dB), cleaned by a noise stretch of its own recording')
    for i, t in enumerate(takes):
        if isinstance(t, dict) and 'denoise' in t:
            _check_denoise(c, t['denoise'], f"{_join(path, 'file')}[{i}].denoise")
    c.number(src, 'offset', path, 0.0, MAX_SECONDS)
    c.number(src, 'cents', path, -MAX_CENTS, MAX_CENTS)
    if 'reverse' in src and not isinstance(src['reverse'], bool):
        c.err(_join(path, 'reverse'), 'expected true or false')
    if 'time_stretch' in src:
        from .stretch import MAX_FACTOR, METHODS, MIN_FACTOR
        ts, where = src['time_stretch'], _join(path, 'time_stretch')
        if not isinstance(ts, dict) or 'factor' not in ts:
            c.err(where, 'expected {"factor": 2, "method": "phase-locked" or "smear", "keep_attack_ms": 0}')
        else:
            for key in ts:
                if key not in STRETCH_FIELDS:
                    c.err(_join(where, key), f"unknown field (known: {', '.join(sorted(STRETCH_FIELDS))})")
            c.number(ts, 'factor', where, MIN_FACTOR, MAX_FACTOR)
            if 'method' in ts and ts['method'] not in METHODS:
                c.err(_join(where, 'method'), f"{ts['method']!r}: expected one of {', '.join(METHODS)}")
            c.number(ts, 'keep_attack_ms', where, 0.0, 2000.0)
    if 'denoise' in src:
        names = {(t.get('library', library), t.get('file')) if isinstance(t, dict) else (library, t) for t in takes
                 if isinstance(t, str) or (isinstance(t, dict) and isinstance(t.get('file'), str)
                                           and isinstance(t.get('library', library), str))}
        if len(names) > 1:
            c.err(_join(path, 'denoise'), f'its noise stretch is in seconds of one recording, and these takes come from '
                  f'{len(names)}: give each take its own "denoise"')
        _check_denoise(c, src['denoise'], _join(path, 'denoise'))


def _check_instrument(c, src, path):
    name = src.get('soundfont', 'default')
    if not (isinstance(name, str) and BANK_NAME_RE.match(name)):
        c.err(_join(path, 'soundfont'), f'{name!r} is not the name of a bank in the configuration')
    c.number(src, 'program', path, 0, 127, integer=True)
    c.number(src, 'bank', path, 0, 16383, integer=True)
    c.number(src, 'cents', path, -MAX_CENTS, MAX_CENTS)
    if 'drums' in src and not isinstance(src['drums'], bool):
        c.err(_join(path, 'drums'), 'expected true or false')
    _check_notes(c, src, path)


def _check_surge(c, src, path):
    patch = src.get('patch')
    if not (isinstance(patch, str) and SURGE_PATCH_RE.match(patch)):
        c.err(_join(path, 'patch'), f'{patch!r}: expected the id of a Surge patch, such as factory/fx/fireworks')
    _check_notes(c, src, path)
    c.number(src, 'cents', path, -MAX_CENTS, MAX_CENTS)
    macros = src.get('macros', {})
    if not isinstance(macros, dict) or not all(k in tuple('12345678') and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
                                               for k, v in macros.items()):
        c.err(_join(path, 'macros'), f'{macros!r}: expected {{"1": 0.5}}: macros 1 to 8, each 0 to 1')
    bend_range = c.number(src, 'bend_range', path, 1, 24, default=2, integer=True)
    for key, lo, hi, unit in (('bend', -bend_range, bend_range, 'semitones within bend_range'), ('wheel', 0.0, 1.0, '0 to 1')):
        if key not in src:
            continue
        points = src[key]
        ok = (isinstance(points, list) and 1 <= len(points) <= 64
              and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)
                      and 0 <= p[0] <= MAX_LENGTH_S and lo <= p[1] <= hi for p in points)
              and all(a[0] <= b[0] for a, b in zip(points, points[1:])))
        if not ok:
            c.err(_join(path, key), f'{points!r}: expected 1 to 64 points [time s, {unit}], in time order, within {MAX_LENGTH_S:g} s')
    c.number(src, 'bpm', path, 30.0, 300.0)
    c.number(src, 'tail', path, 0.1, 15.0)
    c.number(src, 'take', path, 1, MAX_TAKES, integer=True)


def _check_notes(c, src, path):
    notes = src.get('notes')
    if not isinstance(notes, list) or not 1 <= len(notes) <= MAX_NOTES:
        c.err(_join(path, 'notes'), 'expected 1 to 64 notes, each [time s, MIDI pitch, length s, velocity]')
        return
    for i, note in enumerate(notes):
        ok = (isinstance(note, list) and len(note) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                    for v in note))
        if ok:
            t, pitch, length, velocity = note
            ok = (0 <= t <= MAX_LENGTH_S and int(pitch) == pitch and 0 <= pitch <= 127 and 0 < length <= MAX_LENGTH_S
                  and int(velocity) == velocity and 1 <= velocity <= 127)
        if not ok:
            c.err(f'{_join(path, "notes")}[{i}]', f'{note!r}: expected [time 0-30 s, MIDI pitch 0-127, length up to '
                  '30 s, velocity 1-127]')


def _check_source(c, src, path):
    if not c.fields(src, path, SOURCE_FIELDS, ('type',)):
        return None
    kind = c.choice(src, 'type', path, SOURCE_TYPES)
    allowed = {'osc': {'type', 'wave', 'duty', 'phase'}, 'fm': {'type', 'ratio', 'index', 'phase'},
               'noise': {'type', 'color'},
               'chip': {'type', 'wave', 'duty', 'mode', 'step_hz', 'levels', 'quantize'},
               'modal': {'type', 'material', 't_d', 'modes', 'hardness', 'detune_cents', 'shape', 'ratios'},
               'drops': {'type'} | DROPS_FIELDS, 'wind': {'type'} | WIND_FIELDS,
               'instrument': {'type'} | INSTRUMENT_FIELDS, 'sample': {'type'} | SAMPLE_FIELDS,
               'surge': {'type'} | SURGE_FIELDS}.get(kind)
    if allowed:
        for key in src:
            if key not in allowed and key in SOURCE_FIELDS:
                c.err(_join(path, key), f"does not apply to a source of type '{kind}'")
    if kind == 'instrument':
        _check_instrument(c, src, path)
    if kind == 'surge':
        _check_surge(c, src, path)
    if kind == 'sample':
        _check_sample(c, src, path)
        return 'instrument'
    if kind == 'drops':
        c.number(src, 'rate', path, 1.0, 100000.0)
        lo = c.number(src, 'r_min_mm', path, 0.2, 50.0, default=0.2)
        hi = c.number(src, 'r_max_mm', path, 0.2, 50.0, default=3.0)
        if lo is not None and hi is not None and lo > hi:
            c.err(path, f'r_min_mm ({lo:g}) must not be above r_max_mm ({hi:g})')
        c.number(src, 'size_slope', path, 0.0, 10.0)
        c.number(src, 'gain_slope', path, 0.0, 3.0)
        c.number(src, 'depth', path, 0.1, 20.0)
        c.number(src, 'rise', path, 0.0, 2.0)
        c.number(src, 'rise_cutoff', path, 0.0, 1.0)
        return 'drops'
    if kind == 'wind':
        c.number(src, 'band_hz', path, 30.0, 5000.0)
        c.number(src, 'turbulence', path, 0.0, 0.6)
        c.number(src, 'gust_hz', path, 0.02, 2.0)
        c.number(src, 'whistle_hz', path, 0.0, 4000.0)
        c.number(src, 'whistle_db', path, -60.0, 12.0)
        c.number(src, 'whistle_q', path, 5.0, 200.0)
        return 'wind'
    if kind == 'modal':
        c.choice(src, 'material', path, modal.MATERIALS)
        c.number(src, 't_d', path, 1.0, 3000.0)
        if 'material' in src and 't_d' in src:
            c.err(path, "give either 'material' or 't_d', not both")
        c.number(src, 'modes', path, 1, 16, integer=True)
        c.number(src, 'hardness', path, 0.0, 1.0)
        c.number(src, 'detune_cents', path, 0.0, 100.0)
        c.choice(src, 'shape', path, tuple(modal.SHAPES))
        if 'ratios' in src:
            r = src['ratios']
            ok = isinstance(r, list) and 1 <= len(r) <= 16 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and 1.0 <= v <= 64.0 for v in r)
            if not ok or sorted(r) != list(r):
                c.err(_join(path, 'ratios'), 'expected 1 to 16 rising ratios to the lowest mode, each 1 to 64')
            if 'shape' in src:
                c.err(path, "give either 'shape' or 'ratios', not both")
        return 'modal'
    if kind == 'chip':
        wave = c.choice(src, 'wave', path, chip.WAVES, default='pulse')
        if 'duty' in src and src['duty'] not in chip.DUTIES:
            c.err(_join(path, 'duty'), f"{src['duty']!r} is not one of {', '.join(str(d) for d in chip.DUTIES)}")
        c.choice(src, 'mode', path, chip.MODES)
        c.number(src, 'step_hz', path, 0.0, 1000.0)
        c.number(src, 'levels', path, 2, 256, integer=True)
        if 'quantize' in src and not isinstance(src['quantize'], bool):
            c.err(_join(path, 'quantize'), 'expected true or false')
        return f'chip_{wave}'
    if kind == 'osc':
        c.choice(src, 'wave', path, WAVES)
        c.number(src, 'duty', path, 0.01, 0.99)
        c.number(src, 'phase', path, -360, 360)
    elif kind == 'fm':
        c.number(src, 'ratio', path, 0.01, 32.0)
        c.curve(src, 'index', path, 0.0, 50.0)
        c.number(src, 'phase', path, -360, 360)
    elif kind == 'noise':
        c.choice(src, 'color', path, COLORS)
    return kind


LAYER_FIELDS = {'source', 'start', 'length', 'pitch', 'vibrato', 'tremolo', 'env', 'filter', 'drive', 'gain_db', 'pan',
                'spread'}
SPREAD_KINDS = ('noise', 'drops', 'wind')


def _check_layer(c, layer, path, limit=MAX_LENGTH_S):
    """Check one layer; returns its source kind (None when it cannot be told)."""
    if not c.fields(layer, path, LAYER_FIELDS, ('source', 'length')):
        return None
    kind = _check_source(c, layer.get('source'), _join(path, 'source')) if 'source' in layer else None
    c.number(layer, 'start', path, 0.0, limit)
    c.number(layer, 'length', path, 1e-3, limit)
    if 'spread' in layer:
        c.number(layer, 'spread', path, 0.0, 1.0)
        if kind is not None and kind not in SPREAD_KINDS:
            c.err(_join(path, 'spread'), f"applies to noise, drops and wind sources, not '{kind}'")
    if kind in ('osc', 'fm'):
        c.curve(layer, 'pitch', path, 20.0, 20000.0, required=True)
    elif kind in ('chip_pulse', 'chip_triangle'):
        c.curve(layer, 'pitch', path, 20.0, 12000.0, required=True)
    elif kind == 'chip_noise':
        c.curve(layer, 'pitch', path, 400.0, 450000.0, required=True)
    elif kind == 'modal':
        c.curve(layer, 'pitch', path, 20.0, 10000.0, required=True)
    if kind == 'surge':                      # a take is played back as recorded, at its written notes
        for key in ('pitch', 'vibrato'):
            if key in layer:
                c.err(_join(path, key), "does not move a surge layer, whose take plays its written notes: move it with the source's cents or bend")
    if 'vibrato' in layer and c.fields(layer['vibrato'], _join(path, 'vibrato'), {'rate_hz', 'depth_cents'}):
        c.number(layer['vibrato'], 'rate_hz', _join(path, 'vibrato'), 0.1, 40.0)
        c.number(layer['vibrato'], 'depth_cents', _join(path, 'vibrato'), 0.0, 1200.0)
    if 'tremolo' in layer and c.fields(layer['tremolo'], _join(path, 'tremolo'), {'rate_hz', 'depth'},
                                       ('rate_hz', 'depth')):
        c.number(layer['tremolo'], 'rate_hz', _join(path, 'tremolo'), 0.1, 200.0)
        c.number(layer['tremolo'], 'depth', _join(path, 'tremolo'), 0.0, 1.0)
    if 'env' in layer:
        env = layer['env']
        if isinstance(env, dict):
            p = _join(path, 'env')
            if c.fields(env, p, {'attack', 'hold', 'decay', 'curve'}):
                for key in ('attack', 'hold', 'decay'):
                    c.number(env, key, p, 0.0, MAX_LENGTH_S)
                c.choice(env, 'curve', p, curves.CURVES)
        else:
            c.curve(layer, 'env', path, 0.0, 4.0)
    if 'filter' in layer:
        p = _join(path, 'filter')
        if c.fields(layer['filter'], p, {'type', 'cutoff', 'q'}, ('type', 'cutoff')):
            c.choice(layer['filter'], 'type', p, FILTER_KINDS)
            c.curve(layer['filter'], 'cutoff', p, 10.0, 24000.0)
            c.number(layer['filter'], 'q', p, 0.1, 40.0)
    c.number(layer, 'drive', path, 0.0, 1.0)
    c.number(layer, 'gain_db', path, -120.0, 24.0)
    c.number(layer, 'pan', path, -1.0, 1.0)
    return kind


BED_KINDS = ('noise', 'drops', 'wind', 'osc', 'fm')


def _ends_agree(spec, seconds):
    """A number, or a curve with the same value at its start and at the loop's end, so that the loop joins up (a point
    past the end of the loop is never reached)."""
    try:
        points = curves.normalize(spec)
    except ValueError:
        return True          # reported where the curve itself is checked
    values = curves.evaluate(points, curves.samples(seconds) + 1)
    return abs(float(values[0]) - float(values[-1])) <= 1e-6 * max(abs(float(values[0])), abs(float(values[-1])), 1e-6)


def _check_loop_layer(c, layer, kind, path, seconds):
    """A layer of a loop: a bed (start 0, the loop's whole length) must be able to join its own start; any other layer
    is an event, folded onto the loop, and must start inside it."""
    if not isinstance(layer, dict) or 'length' not in layer:
        return
    try:
        start = float(layer.get('start', 0.0))
        length = float(layer['length'])
    except (TypeError, ValueError):
        return
    period = curves.samples(seconds)
    if curves.samples(start) >= period:
        c.err(_join(path, 'start'), f'{start:g} s is not inside the loop of {seconds:g} s')
        return
    if not (curves.samples(start) == 0 and max(1, curves.samples(length)) == period):
        return
    if isinstance(layer.get('source'), dict) and layer['source'].get('type') == 'sample':
        c.err(_join(path, 'source'), 'a recording cannot run through a whole loop (its end would not meet its start): '
              'play it as a shorter event, or as two crossfaded takes (`looped` in sfxkit, sound-design.md, section 6)')
    elif kind is not None and kind.split('_')[0] not in BED_KINDS:
        c.err(_join(path, 'source'), f"a {kind.split('_')[0]} source cannot run through a whole loop (it has no "
              "steady state); give the layer a shorter length to play it as an event")
    for key in ('pitch', 'env'):
        if key in layer and isinstance(layer[key], dict):
            c.err(_join(path, key), 'a layer lasting the whole loop takes a curve, not an attack/decay shape')
        elif key in layer and not _ends_agree(layer[key], seconds):
            c.err(_join(path, key), 'a layer lasting the whole loop needs the same value at its start and its end')
    flt = layer.get('filter')
    if isinstance(flt, dict) and 'cutoff' in flt and not _ends_agree(flt['cutoff'], seconds):
        c.err(_join(_join(path, 'filter'), 'cutoff'), 'a layer lasting the whole loop needs the same value at its '
              'start and its end')
    src = layer.get('source')
    if isinstance(src, dict) and 'index' in src and not _ends_agree(src['index'], seconds):
        c.err(_join(_join(path, 'source'), 'index'), 'a layer lasting the whole loop needs the same value at its '
              'start and its end')


CHAIN_FIELDS = {'console', 'eq', 'tilt', 'saturation', 'compressor', 'delay', 'room', 'fade'}
COMPRESSOR_FIELDS = {'threshold_db', 'ratio', 'knee_db', 'attack_ms', 'release_ms', 'makeup_db', 'mix'}


def _check_chain(c, chain, path):
    if not isinstance(chain, dict):
        c.err(path, 'expected an object with one entry per stage, for example {"compressor": {"ratio": 3}, '
              '"room": {"size": 1, "decay": 0.6, "mix": 0.15}}')
        return
    if not c.fields(chain, path, CHAIN_FIELDS):
        return
    if 'tilt' in chain:
        p = _join(path, 'tilt')
        if c.fields(chain['tilt'], p, {'db'}, ('db',)):
            c.number(chain['tilt'], 'db', p, -12.0, 12.0)
    if 'compressor' in chain:
        p = _join(path, 'compressor')
        if c.fields(chain['compressor'], p, COMPRESSOR_FIELDS):
            comp = chain['compressor']
            c.number(comp, 'threshold_db', p, -60.0, 0.0)
            c.number(comp, 'ratio', p, 1.0, 20.0)
            c.number(comp, 'knee_db', p, 0.0, 24.0)
            c.number(comp, 'attack_ms', p, 0.1, 200.0)
            c.number(comp, 'release_ms', p, 5.0, 2000.0)
            c.number(comp, 'makeup_db', p, -12.0, 24.0)
            c.number(comp, 'mix', p, 0.0, 1.0)
    if 'console' in chain:
        p = _join(path, 'console')
        if c.fields(chain['console'], p, {'model'}, ('model',)):
            c.choice(chain['console'], 'model', p, chip.CONSOLES)
    if 'eq' in chain:
        p = _join(path, 'eq')
        if c.fields(chain['eq'], p, {'low_cut_hz', 'high_cut_hz', 'peak'}):
            c.number(chain['eq'], 'low_cut_hz', p, 10.0, 20000.0)
            c.number(chain['eq'], 'high_cut_hz', p, 20.0, 24000.0)
            if 'peak' in chain['eq']:
                pp = _join(p, 'peak')
                if c.fields(chain['eq']['peak'], pp, {'hz', 'gain_db', 'q'}, ('hz', 'gain_db')):
                    c.number(chain['eq']['peak'], 'hz', pp, 20.0, 20000.0)
                    c.number(chain['eq']['peak'], 'gain_db', pp, -24.0, 24.0)
                    c.number(chain['eq']['peak'], 'q', pp, 0.1, 20.0)
    if 'saturation' in chain:
        p = _join(path, 'saturation')
        if c.fields(chain['saturation'], p, {'drive', 'mix'}):
            c.number(chain['saturation'], 'drive', p, 0.0, 1.0)
            c.number(chain['saturation'], 'mix', p, 0.0, 1.0)
    if 'delay' in chain:
        p = _join(path, 'delay')
        if c.fields(chain['delay'], p, {'time', 'feedback', 'mix', 'damping_hz'}, ('time',)):
            c.number(chain['delay'], 'time', p, 0.001, 2.0)
            c.number(chain['delay'], 'feedback', p, 0.0, 0.95)
            c.number(chain['delay'], 'mix', p, 0.0, 1.0)
            c.number(chain['delay'], 'damping_hz', p, 200.0, 20000.0)
    if 'room' in chain:
        p = _join(path, 'room')
        if c.fields(chain['room'], p, {'size', 'decay', 'mix'}):
            c.number(chain['room'], 'size', p, 0.2, 3.0)
            c.number(chain['room'], 'decay', p, 0.05, 4.0)
            c.number(chain['room'], 'mix', p, 0.0, 1.0)
    if 'fade' in chain:
        p = _join(path, 'fade')
        if c.fields(chain['fade'], p, {'in', 'out'}):
            c.number(chain['fade'], 'in', p, 0.0, 5.0)
            c.number(chain['fade'], 'out', p, 0.0, 5.0)


SOUND_FIELDS = {'id', 'category', 'use', 'loop', 'recipe', 'params', 'layers', 'chain', 'vary', 'variants', 'channels',
                'trim_db', 'notes', 'style', 'peak_at', 'crackle'}


def _check_use(c, sound, path, recipe_use):
    """(use, loop settings or None): a recipe's sounds take the recipe's use; `loop` belongs to loops only."""
    written = c.choice(sound, 'use', path, USES)
    use = written or recipe_use or 'one_shot'
    if recipe_use and written and written != recipe_use:
        c.err(_join(path, 'use'), f"the recipe {sound.get('recipe')} makes {recipe_use.replace('_', '-')} sounds")
        use = recipe_use
    if use != 'loop':
        if 'loop' in sound:
            c.err(_join(path, 'loop'), 'belongs to a sound with "use": "loop"')
        return use, None
    settings = sound.get('loop', {})
    seconds = DEFAULT_LOOP_S
    if c.fields(settings, _join(path, 'loop'), {'seconds'}):
        seconds = c.number(settings, 'seconds', _join(path, 'loop'), 1.0, MAX_LOOP_S, default=DEFAULT_LOOP_S)
    return use, {'seconds': seconds}


def _check_sound(c, sound, path, defaults):
    if not c.fields(sound, path, SOUND_FIELDS, ('id', 'category')):
        return None
    out = {}
    sid = sound.get('id')
    if 'id' in sound and not (isinstance(sid, str) and ID_RE.match(sid)):
        c.err(_join(path, 'id'), f'{sid!r} must be lower-case letters, digits and _, starting with a letter, at '
                                 'most 32 characters')
    out['id'] = sid
    out['category'] = c.choice(sound, 'category', path, CATEGORIES)
    recipe_use = None
    if isinstance(sound.get('recipe'), str):
        try:
            recipe_use = recipes.get(sound['recipe']).use
        except KeyError:
            recipe_use = None          # reported below
    out['use'], loop = _check_use(c, sound, path, recipe_use)
    if loop:
        out['loop'] = loop
    out['variants'] = c.number(sound, 'variants', path, 1, 16, default=defaults['variants'], integer=True)
    out['channels'] = c.number(sound, 'channels', path, 1, 2, default=defaults['channels'], integer=True)
    if 'style' in sound and not isinstance(sound['style'], bool):
        c.err(_join(path, 'style'), "expected true or false (false: the project's style does not apply)")
    out['style'] = sound.get('style', True) is not False
    if 'notes' in sound and not isinstance(sound['notes'], str):
        c.err(_join(path, 'notes'), 'expected text')
    out['notes'] = sound.get('notes', '')
    has_recipe, has_layers = 'recipe' in sound, 'layers' in sound
    if has_recipe == has_layers:
        c.err(path, "give either 'recipe' (with 'params') or 'layers', not both and not neither")
        return out
    vary = sound.get('vary', {})
    if not isinstance(vary, dict):
        c.err(_join(path, 'vary'), 'expected an object of names and amounts')
        vary = {}
    if has_recipe:
        for key in ('chain',):
            if key in sound:
                c.err(_join(path, key), "belongs to a sound written as layers; a recipe makes its own")
        try:
            recipe = recipes.get(sound['recipe'])
        except KeyError as error:
            c.err(_join(path, 'recipe'), error.args[0])
            return out
        out['recipe'] = recipe.name
        params = sound.get('params', {})
        if not c.fields(params, _join(path, 'params'), {p.name for p in recipe.params}):
            params = {}
        values = recipe.defaults()
        for p in recipe.params:
            v = c.number(params, p.name, _join(path, 'params'), p.lo, p.hi)
            if v is not None:
                values[p.name] = v
        out['params'] = values
        amounts = {p.name: float(p.amount) for p in recipe.params if p.vary}
        for name, amount in vary.items():
            p = recipe.param(name)
            if p is None or not p.vary:
                varying = ', '.join(sorted(amounts)) or 'none'
                c.err(_join(_join(path, 'vary'), name), f'not a varying parameter of {recipe.name} ({varying})')
                continue
            limit = VARY_LIMITS.get(p.vary, p.hi - p.lo)
            v = c.number(vary, name, _join(path, 'vary'), 0.0, limit)
            if v is not None:
                amounts[name] = v
        out['vary'] = amounts
        out['trim_db'] = c.number(sound, 'trim_db', path, -24.0, 6.0, default=float(recipe.trim_db))
    else:
        layers = sound['layers']
        if not isinstance(layers, list) or not layers:
            c.err(_join(path, 'layers'), 'expected a list of at least one layer')
        else:
            for i, layer in enumerate(layers):
                lpath = f'{path}.layers[{i}]'
                kind = _check_layer(c, layer, lpath, MAX_LOOP_S if loop else MAX_LENGTH_S)
                if loop:
                    _check_loop_layer(c, layer, kind, lpath, loop['seconds'])
        if 'params' in sound:
            c.err(_join(path, 'params'), "belongs to a sound made by a recipe")
        if 'chain' in sound:
            _check_chain(c, sound['chain'], _join(path, 'chain'))
            if loop and isinstance(sound['chain'], dict) and 'fade' in sound['chain']:
                c.err(_join(_join(path, 'chain'), 'fade'), 'a loop has no fades: it plays on from its end into its '
                      'start')
        if loop and 'time_pct' in vary:
            c.err(_join(_join(path, 'vary'), 'time_pct'), 'a loop keeps its length; vary pitch, cut-off or gain '
                  'instead')
        if not loop and 'scatter_s' in vary:
            c.err(_join(_join(path, 'vary'), 'scatter_s'), "moves a loop's events between its variants; the layers of a "
                  'one-shot stay where they are written (use time_pct, or takes)')
        out['layers'] = copy.deepcopy(layers)
        out['chain'] = copy.deepcopy(sound.get('chain', {}))
        amounts = {}
        for name in vary:
            if name not in LAYER_VARY:
                near = difflib.get_close_matches(name, list(LAYER_VARY), n=1, cutoff=0.5)
                hint = f"; did you mean '{near[0]}'?" if near else f"; use {', '.join(LAYER_VARY)}"
                c.err(_join(_join(path, 'vary'), name), 'unknown' + hint)
                continue
            v = c.number(vary, name, _join(path, 'vary'), 0.0, LAYER_VARY[name])
            if v is not None:
                amounts[name] = v
        out['vary'] = amounts
        out['trim_db'] = c.number(sound, 'trim_db', path, -24.0, 6.0, default=0.0)
    if 'peak_at' in sound:
        out['peak_at'] = c.number(sound, 'peak_at', path, 0.0, MAX_LOOP_S)
    crackle = sound.get('crackle')
    if crackle is not None and not isinstance(crackle, bool):
        c.err(_join(path, 'crackle'), 'expected true or false')
    elif crackle is not None:
        out['crackle'] = crackle
    elif isinstance(sound.get('recipe'), str):
        try:
            out['crackle'] = bool(recipes.get(sound['recipe']).crackle)
        except KeyError:
            pass                       # an unknown recipe is reported above
    return out


TOP_FIELDS = {'schema', 'title', 'seed', 'variants', 'channels', 'level', 'style', 'flows', 'sounds', 'notes'}


def validate(data):
    """The project with every default filled in; raises ProjectError listing every problem."""
    c = _Checker()
    if not c.fields(data, '', TOP_FIELDS, ('schema', 'title', 'sounds')):
        raise ProjectError(c.errors)
    out = {'schema': SCHEMA}
    if data.get('schema') != SCHEMA:
        c.err('schema', f"must be '{SCHEMA}', not {data.get('schema')!r}")
    title = data.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        c.err('title', 'expected a title of 1 to 80 characters')
        title = 'untitled'
    out['title'] = title
    seed = c.number(data, 'seed', '', 0, 2 ** 31 - 1, integer=True)
    out['seed'] = seed if seed is not None else dsp.seed_for('title', title) % (2 ** 31)
    out['variants'] = c.number(data, 'variants', '', 1, 16, default=4, integer=True)
    out['channels'] = c.number(data, 'channels', '', 1, 2, default=1, integer=True)
    level = data.get('level', {})
    out['level'] = {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': DEFAULT_LIMIT_DB}
    if c.fields(level, 'level', {'mode', 'ceiling_dbtp', 'limit_db', 'target_lufs'}):
        out['level']['mode'] = c.choice(level, 'mode', 'level', ('match', 'peak'), default='match')
        out['level']['ceiling_dbtp'] = c.number(level, 'ceiling_dbtp', 'level', -12.0, 0.0, default=-1.0)
        out['level']['limit_db'] = c.number(level, 'limit_db', 'level', 0.0, 12.0, default=DEFAULT_LIMIT_DB)
        if 'target_lufs' in level:
            out['level']['target_lufs'] = c.number(level, 'target_lufs', 'level', -60.0, 0.0)
            if out['level']['mode'] == 'peak':
                c.err('level.target_lufs', 'belongs to the "match" mode; "peak" puts each file at the ceiling')
    out['style'] = {}
    style = data.get('style', {})
    if c.fields(style, 'style', {'space', 'tilt_db'}):
        if 'space' in style and style['space'] is not None:
            if c.fields(style['space'], 'style.space', {'size', 'decay', 'mix'}):
                space = {'size': c.number(style['space'], 'size', 'style.space', 0.2, 3.0, default=1.0),
                         'decay': c.number(style['space'], 'decay', 'style.space', 0.05, 4.0, default=0.6),
                         'mix': c.number(style['space'], 'mix', 'style.space', 0.0, 1.0, default=0.2)}
                out['style']['space'] = space
        tilt = c.number(style, 'tilt_db', 'style', -12.0, 12.0)
        if tilt:
            out['style']['tilt_db'] = tilt
    out['notes'] = data.get('notes', '')
    sounds = data.get('sounds')
    out['sounds'] = []
    if not isinstance(sounds, list) or not sounds:
        c.err('sounds', 'expected a list of at least one sound')
    else:
        seen = set()
        for i, sound in enumerate(sounds):
            s = _check_sound(c, sound, f'sounds[{i}]', out)
            if s is None:
                continue
            if s.get('id') in seen:
                c.err(f'sounds[{i}].id', f"'{s['id']}' is used twice; ids must be unique")
            seen.add(s.get('id'))
            out['sounds'].append(s)
    flows = data.get('flows', [])
    out['flows'] = []
    ids = {s.get('id') for s in out['sounds']}
    loops = {s.get('id') for s in out['sounds'] if s.get('use') == 'loop'}
    if not isinstance(flows, list):
        c.err('flows', 'expected a list')
        flows = []
    for i, flow in enumerate(flows):
        path = f'flows[{i}]'
        if not c.fields(flow, path, {'name', 'sounds', 'gap'}, ('name', 'sounds')):
            continue
        names = flow.get('sounds')
        if not isinstance(names, list) or not names:
            c.err(_join(path, 'sounds'), 'expected a list of sound ids')
            names = []
        for name in names:
            if name not in ids:
                c.err(_join(path, 'sounds'), f"'{name}' is not a sound of this project")
            elif name in loops:
                c.err(_join(path, 'sounds'), f"'{name}' is a loop; a flow plays one-shot sounds in a row")
        out['flows'].append({'name': str(flow.get('name')), 'sounds': list(names),
                             'gap': c.number(flow, 'gap', path, -2.0, 2.0, default=0.25)})
    if c.errors:
        raise ProjectError(c.errors)
    return out


def load(path):
    """Read and validate a project file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ProjectError([f'project file not found: {path}']) from None
    except ValueError as error:
        raise ProjectError([f'{path} is not valid JSON: {error}']) from None
    return validate(data)
