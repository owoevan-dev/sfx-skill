"""Builders for writing a sound-effects project from a short Python script, for sets too large to write by hand.

Each returns the plain JSON of the project format (a layer, a filter, an envelope), so that what a script builds stays
readable and editable in the file it writes. references/building-blocks.md lists them with an example.

    import sys
    sys.path.insert(0, 'PATH/TO/sound-effects/scripts')
    from sfxkit import *

    slam = sound('door_slam', 'foley', 'A heavy door: the recorded slam, with weight under it.', [
        rec('fsd50k-dev', '216872', 0.0, 0.5, offset=0.02),
        thump(0.01, 110, 48, 0.25, gain=-6),
    ], chain={'room': {'size': 0.7, 'decay': 0.6, 'mix': 0.12}})
    save('project.json', 'My game', [slam], variants=3)

The default times and levels here are this skill's starting points, to be changed for the sound."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfxlib import project as _project  # noqa: E402

__all__ = ['hp', 'lp', 'bp', 'clip', 'decay', 'swell', 'equal_power', 'speed', 'lands', 'take', 'rec', 'looped', 'tone', 'noise',
           'chip', 'fm', 'wind', 'modal', 'thump', 'rumble', 'crack', 'whoosh', 'sound', 'save']


def hp(hz, q=0.7071):
    """A high-pass filter, for a layer's "filter"."""
    return {'type': 'highpass', 'cutoff': hz, 'q': q}


def lp(hz, q=0.7071):
    """A low-pass filter."""
    return {'type': 'lowpass', 'cutoff': hz, 'q': q}


def bp(hz, q=1.0):
    """A band-pass filter."""
    return {'type': 'bandpass', 'cutoff': hz, 'q': q}


def clip(length, fade_in=0.003, fade_out=0.04):
    """An envelope: full level between two short fades, for playing a stretch of a recording as it is."""
    return [[0, 0, 'cos'], [fade_in, 1], [max(length - fade_out, fade_in), 1, 'cos'], [length, 0]]


def decay(length, attack=0.002):
    """An envelope: a fast rise, then a fall even in decibels to silence at `length`."""
    return [[0, 0], [attack, 1, 'db'], [length, 0]]


def swell(length, top=0.85):
    """An envelope that rises to its top at `top` of its length and closes quickly: for a reversed sound running into a
    hit."""
    return [[0, 0, 'cos'], [top * length, 1, 'cos'], [length, 0]]


def equal_power(length, fade, steps=8):
    """An envelope with equal-power fades at both ends: two unrelated takes crossfaded with it keep their power."""
    rise = [[fade * i / steps, math.sin(math.pi / 2 * i / steps)] for i in range(steps + 1)]
    fall = [[length - fade + fade * i / steps, math.cos(math.pi / 2 * i / steps)] for i in range(steps + 1)]
    points = [[round(t, 5), round(v, 5)] for t, v in rise + fall]
    points[-1][1] = 0
    return points


def speed(cents):
    """How much faster a recording moved by `cents` plays."""
    return 2 ** (cents / 1200)


def lands(event, offset, cents, when):
    """The layer `start` that makes the recording's moment `event` play at `when`, for a layer starting `offset` seconds
    into the recording and moved by `cents`."""
    return when - (event - offset) / speed(cents)


def take(file, offset=0.0, gain=0.0, denoise=None, library=None):
    """One take of a layer's list: a recording, where in it to start, its own level in dB and, when the takes come from
    different recordings, its own noise stretch. `library`: its pack, when it is not the layer's. Recordings arrive at the
    level of their loudest moment, so the takes of one layer usually need different gains."""
    out = {'file': file, 'offset': round(offset, 4)}
    if library:
        out['library'] = library
    if gain:
        out['gain_db'] = gain
    if denoise:
        out['denoise'] = denoise
    return out


class _Default(list):
    """An envelope a builder chose because none was asked for: `sound` takes it off a layer that lasts a whole loop."""


def _layer(source, start, length, env, gain, filt=None, pan=None, **extra):
    layer = {'source': source, 'start': round(max(0.0, start), 4), 'length': round(length, 4), 'env': env}
    if gain:
        layer['gain_db'] = gain
    if filt:
        layer['filter'] = filt
    if pan is not None:
        layer['pan'] = pan
    layer.update({k: v for k, v in extra.items() if v})
    return layer


def rec(pack, file, start, length, offset=0.0, cents=0, gain=0.0, env=None, filt=None, reverse=False, pan=None, denoise=None,
        stretch=None, tremolo=None):
    """A layer playing a recording of the library. `file` is a name, or a list of takes (see `take`; `samples --events`
    prints one), of which each variant plays the next. `offset`: where in the recording to start. `cents` moves it in pitch and
    speed; `stretch` ({"factor": 2, "keep_attack_ms": 20}) changes its length alone."""
    source = {'type': 'sample', 'library': pack, 'file': file}
    if offset:
        source['offset'] = round(offset, 4)
    if cents:
        source['cents'] = cents
    if reverse:
        source['reverse'] = True
    if denoise:
        source['denoise'] = denoise
    if stretch:
        source['time_stretch'] = stretch
    return _layer(source, start, length, clip(length) if env is None else env, gain, filt, pan, tremolo=tremolo)


def looped(pack, file, seconds, fade, offsets, cents=0, gain=0.0, filt=None, pan=None, stretch=None):
    """The layers of a seamless loop `seconds` long from one recording: two takes from unrelated moments (`offsets`), each half
    the loop plus a crossfade of `fade` seconds, the second running over the loop's end into its start. The recording must
    hold `seconds / 2 + fade` after each offset (at the speed `cents` plays it)."""
    if len(offsets) != 2:
        raise ValueError('a loop from a recording takes two offsets')
    half = seconds / 2
    return [rec(pack, file, i * half, half + fade, offset=at, cents=cents, gain=gain, env=equal_power(half + fade, fade), filt=filt,
                pan=pan, stretch=stretch) for i, at in enumerate(offsets)]


def tone(start, length, pitch, wave='sine', env=None, gain=0.0, filt=None, vibrato=None, tremolo=None):
    """An oscillator. `pitch` is in Hz, or a curve [[time, Hz], ...]."""
    layer = _layer({'type': 'osc', 'wave': wave}, start, length, _Default(decay(length)) if env is None else env, gain, filt,
                   vibrato=vibrato, tremolo=tremolo)
    layer['pitch'] = pitch
    return layer


def noise(start, length, color='pink', env=None, filt=None, gain=0.0, spread=None):
    """Noise: 'white', 'pink' or 'brown'. A filter whose cutoff is a curve makes it move."""
    layer = _layer({'type': 'noise', 'color': color}, start, length, _Default(decay(length)) if env is None else env, gain, filt)
    if spread is not None:
        layer['spread'] = spread
    return layer


def chip(start, length, pitch, wave='pulse', duty=0.5, env=None, gain=0.0, filt=None, step_hz=60.0):
    """A chip voice after the NES: 'pulse', 'triangle' or 'noise'. Without `env` it falls evenly to silence: its envelope
    takes 16 volume steps, so a fall in dB would reach the lowest step and stop early."""
    source = {'type': 'chip', 'wave': wave, 'step_hz': step_hz}
    if wave == 'pulse':
        source['duty'] = duty
    layer = _layer(source, start, length, [[0, 1], [length, 0]] if env is None else env, gain, filt)
    layer['pitch'] = pitch
    return layer


def fm(start, length, pitch, ratio=1.0, index=1.0, env=None, gain=0.0, filt=None):
    """Two-operator FM: a carrier at `pitch` Hz (or a curve), a modulator at `ratio` times it, `index` its depth (or a
    curve). Bells at ratios like 1.4 and 3.5, brass near 1."""
    layer = _layer({'type': 'fm', 'ratio': ratio, 'index': index}, start, length, _Default(decay(length)) if env is None else env,
                   gain, filt)
    layer['pitch'] = pitch
    return layer


def wind(start, length, band_hz=200.0, turbulence=0.2, gust_hz=0.2, env=None, gain=0.0, filt=None, spread=None):
    """Air past obstacles: wake noise from `band_hz` up, its speed moved by `turbulence` at gusts below `gust_hz`."""
    layer = _layer({'type': 'wind', 'band_hz': band_hz, 'turbulence': turbulence, 'gust_hz': gust_hz}, start, length,
                   _Default(clip(length, 0.2, 0.2)) if env is None else env, gain, filt)
    if spread is not None:
        layer['spread'] = spread
    return layer


def modal(start, length, hz, t_d, modes=6, hardness=0.8, gain=0.0, detune=15.0, ratios=None):
    """A struck object: its modes above `hz`, each falling to 1/e in t_d / f seconds (`t_d` is higher for metal and glass,
    lower for wood and rubber). `ratios`: the modes of an object of your own design, as rising ratios to the lowest."""
    source = {'type': 'modal', 't_d': t_d, 'hardness': hardness, 'detune_cents': detune}
    if ratios:
        source['ratios'] = list(ratios)
    else:
        source['modes'] = modes
    layer = _layer(source, start, length, [[0, 1, 'hold'], [0.7 * length, 1, 'cos'], [length, 0]], gain)
    layer['pitch'] = hz
    return layer


def thump(start, high, low, length, gain=0.0, glide=0.08):
    """Low weight under a hit: a sine falling from `high` to `low` Hz in `glide` seconds."""
    return tone(start, length, [[0, high, 'exp'], [glide, low]], env=decay(length, 0.003), gain=gain)


def rumble(start, cutoff, length, gain=0.0, attack=0.01):
    """Low mass: brown noise under a low-pass at `cutoff` Hz, dying away."""
    return noise(start, length, 'brown', env=decay(length, attack), filt=lp(cutoff), gain=gain)


def crack(start, gain=-6.0, cutoff=2500.0, length=0.008):
    """The contact at the front of a hit: a few milliseconds of white noise above `cutoff` Hz."""
    return noise(start, length, 'white', env=[[0, 1, 'db'], [length, 0]], filt=hp(cutoff), gain=gain)


def whoosh(start, length, low, high, gain=0.0, q=1.5, color='pink', peak=0.6):
    """Air moving past: noise through a band-pass that sweeps from `low` up to `high` Hz at `peak` of its length and back."""
    top = peak * length
    return noise(start, length, color, env=[[0, 0, 'cos'], [top, 1, 'cos'], [length, 0]],
                 filt={'type': 'bandpass', 'cutoff': [[0, low, 'exp'], [top, high, 'exp'], [length, low]], 'q': q}, gain=gain)


def sound(sid, category, notes, layers, chain=None, **extra):
    """One sound of the project. `extra` takes its other fields: channels, variants, trim_db, vary, use, loop, peak_at, style,
    crackle."""
    seconds = (extra.get('loop') or {}).get('seconds', _project.DEFAULT_LOOP_S) if extra.get('use') == 'loop' else None
    if seconds:                     # a layer lasting the whole loop is a bed: a builder's default envelope would fade it at the join
        layers = [{k: v for k, v in layer.items() if k != 'env'}
                  if layer.get('start', 0) == 0 and layer['length'] == seconds and isinstance(layer.get('env'), _Default)
                  else layer for layer in layers]
    out = {'id': sid, 'category': category, 'notes': notes, 'layers': layers}
    if chain:
        out['chain'] = chain
    out.update(extra)
    return out


def save(path, title, sounds, **extra):
    """Check the project and write it to `path` (returned). A mistake raises ValueError listing what is wrong and where, and
    nothing is written. `extra` takes the project's other fields: variants, seed, style, flows, level, notes."""
    data = {'schema': 'sound-effects/1', 'title': title, 'sounds': sounds, **extra}
    try:
        _project.validate(json.loads(json.dumps(data)))
    except _project.ProjectError as error:
        raise ValueError('\n'.join(error.errors)) from None
    path = Path(path)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    return path
