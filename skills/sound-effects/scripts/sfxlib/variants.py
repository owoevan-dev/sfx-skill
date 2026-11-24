"""Variants: each variant of a sound redraws its varying values from a stream seeded by the project's seed, the sound's
id, the variant's number and the value's name, so a variant repeats exactly, the first variant (number 0) keeps the
written values, and changing one sound or one value leaves every other draw as it was.

Recipe sounds vary their parameters as each parameter defines (recipes/base.py). Sounds written as layers vary as a
whole: pitch_cents (every pitch), time_pct (every start, length and curve time), cutoff_cents (every filter cut-off)
and gain_db (each layer's gain on its own), each by up to its amount either way; in a loop, scatter_s moves each event
(not a bed) on its own, coming round from the other end. A sample layer whose `file` is a list of takes plays take
n mod k in variant n (the first variant the first take); it moves in pitch by resampling."""
import copy

import numpy as np

from . import curves, dsp, recipes, surge, synth


def _u(project_seed, sound_id, variant, name):
    if variant == 0:
        return 0.0
    rng = np.random.default_rng(dsp.seed_for(project_seed, sound_id, variant, 'vary', name))
    return float(rng.uniform(-1.0, 1.0))


def recipe_params(sound, project_seed, variant):
    """(parameter values for this variant, {name: u drawn}) for a sound made by a recipe."""
    recipe = recipes.get(sound['recipe'])
    params = dict(sound['params'])
    drawn = {}
    for p in recipe.params:
        if not p.vary:
            continue
        amount = float(sound.get('vary', {}).get(p.name, 0.0))
        u = _u(project_seed, sound['id'], variant, p.name) if amount > 0 else 0.0
        drawn[p.name] = u
        if u == 0.0:
            continue
        value = params[p.name]
        if p.vary == 'cents':
            value *= 2 ** (u * amount / 1200)
        elif p.vary == 'pct':
            value *= 1 + u * amount / 100
        else:
            value += u * amount
        params[p.name] = float(min(max(value, p.lo), p.hi))
    return params, drawn


def _scale_curve(spec, value=1.0, time=1.0):
    if isinstance(spec, (int, float)):
        return float(spec) * value
    return [[float(pt[0]) * time, float(pt[1]) * value] + list(pt[2:]) for pt in spec]


def layer_spec(sound, project_seed, variant):
    """({'layers', 'chain'} for this variant, {name: value drawn}) for a sound written as layers."""
    layers = copy.deepcopy(sound['layers'])
    vary = sound.get('vary', {})
    drawn = {}
    for name in ('pitch_cents', 'time_pct', 'cutoff_cents'):
        amount = float(vary.get(name, 0.0))
        drawn[name] = _u(project_seed, sound['id'], variant, name) * amount if amount > 0 else 0.0
    pitch = 2 ** (drawn['pitch_cents'] / 1200)
    time = 1 + drawn['time_pct'] / 100
    cutoff = 2 ** (drawn['cutoff_cents'] / 1200)
    gain_amount = float(vary.get('gain_db', 0.0))
    drawn['gain_db'] = []
    scatter = float(vary.get('scatter_s', 0.0))
    if scatter > 0:
        seconds = sound['loop']['seconds']
        drawn['scatter_s'] = []
    played = {}
    for i, layer in enumerate(layers):
        g = _u(project_seed, sound['id'], variant, f'gain_db:{i}') * gain_amount if gain_amount > 0 else 0.0
        drawn['gain_db'].append(g)
        src = layer.get('source', {})
        if src.get('type') == 'surge':                  # which performance this is: a named take, or this variant's own
            if 'take' in src:
                src['slot'] = f"take:{int(src['take'])}"
            else:                                       # the sound's n-th layer playing this same performance: a layer playing
                same = surge.key_of(dict(surge.performance(src), slot=None))        # anything else, coming or going, leaves it be
                n = played[same] = played.get(same, -1) + 1
                src['slot'] = f"{sound['id']}/{src['patch']}/{n}/{variant}"
        if src.get('type') == 'sample' and isinstance(src.get('file'), list):
            take = src['file'][variant % len(src['file'])]
            if isinstance(take, dict):                  # a take that starts at its own time in the recording
                src['file'] = take['file']
                if 'library' in take:
                    src['library'] = take['library']
                if 'offset' in take:
                    src['offset'] = take['offset']
                if 'gain_db' in take:
                    layer['gain_db'] = float(layer.get('gain_db', 0.0)) + float(take['gain_db'])
                if 'denoise' in take:                   # the noise of this recording, in its own seconds
                    src['denoise'] = take['denoise']
            else:
                src['file'] = take
        if scatter > 0:
            bed = synth.is_bed(layer, max(1, curves.samples(seconds)))
            move = 0.0 if bed else _u(project_seed, sound['id'], variant, f'scatter:{i}') * scatter
            drawn['scatter_s'].append(move)
            if move:
                layer['start'] = (float(layer.get('start', 0.0)) + move) % seconds
        if variant == 0:
            continue
        if 'pitch' in layer:
            layer['pitch'] = _scale_curve(layer['pitch'], pitch, time)
        layer['start'] = float(layer.get('start', 0.0)) * time
        layer['length'] = float(layer['length']) * time
        env = layer.get('env')
        if isinstance(env, dict):
            layer['env'] = {k: (float(v) * time if k in ('attack', 'hold', 'decay') else v) for k, v in env.items()}
        elif env is not None:
            layer['env'] = _scale_curve(env, 1.0, time)
        src = layer.get('source', {})
        if src.get('type') == 'fm' and not isinstance(src.get('index', 1.0), (int, float)):
            src['index'] = _scale_curve(src['index'], 1.0, time)
        if src.get('type') == 'instrument':               # a recording moves in pitch by resampling, in time by its notes
            src['cents'] = float(src.get('cents', 0.0)) + drawn['pitch_cents']
            src['notes'] = [[float(n[0]) * time, n[1], float(n[2]) * time, n[3]] for n in src.get('notes', [])]
        if src.get('type') == 'sample':                   # a recording moves in pitch by resampling
            src['cents'] = float(src.get('cents', 0.0)) + drawn['pitch_cents']
        if src.get('type') == 'surge':                    # a take moves in pitch by resampling; a named take keeps its notes
            src['cents'] = float(src.get('cents', 0.0)) + drawn['pitch_cents']
            if 'take' not in src:
                src['notes'] = [[float(n[0]) * time, n[1], float(n[2]) * time, n[3]] for n in src['notes']]
                for name in ('bend', 'wheel'):
                    if name in src:
                        src[name] = [[float(p[0]) * time, p[1]] for p in src[name]]
        if 'filter' in layer:
            layer['filter']['cutoff'] = _scale_curve(layer['filter']['cutoff'], cutoff, time)
        if g:
            layer['gain_db'] = float(layer.get('gain_db', 0.0)) + g
    return {'layers': layers, 'chain': copy.deepcopy(sound.get('chain', {}))}, drawn


def spec_for(sound, project_seed, variant):
    """({'layers', 'chain'}, values drawn, parameters or None) for any sound and variant. A loop recipe also receives
    the loop's length as 'seconds'."""
    if 'recipe' in sound:
        params, drawn = recipe_params(sound, project_seed, variant)
        given = dict(params, seconds=sound['loop']['seconds']) if sound.get('loop') else params
        built = recipes.get(sound['recipe']).build(given)
        return {'layers': built['layers'], 'chain': built.get('chain', {})}, drawn, params
    spec, drawn = layer_spec(sound, project_seed, variant)
    return spec, drawn, None


def with_style(spec, style, enabled=True):
    """The spec with the project's style: its space as the room of a sound that has none, its tilt added to the
    sound's own. A sound with "style": false keeps its own chain."""
    if not enabled or not style:
        return spec
    chain = dict(spec.get('chain') or {})
    if style.get('space') and 'room' not in chain:
        chain['room'] = dict(style['space'])
    if style.get('tilt_db'):
        chain['tilt'] = {'db': float(chain.get('tilt', {}).get('db', 0.0)) + float(style['tilt_db'])}
    return dict(spec, chain=chain)

