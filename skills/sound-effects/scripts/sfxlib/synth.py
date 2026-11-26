"""Rendering: one layer, then a whole sound (its layers summed, then its fixed chain, then a tidy ending), or a loop.

A layer runs source -> filter -> drive -> envelope -> gain -> placement. After the layers are summed the chain runs in
a fixed order, each stage only when the sound names it: console, eq, tilt, saturation, compressor, delay, room, fade;
then a DC block.
Random parts are drawn from streams seeded by the sound's seed, the layer's position and the part's name, so renders
repeat exactly.

Placement in a stereo sound: a mono layer is panned with the constant-power law (pan -1 left to +1 right). A noise,
drops or wind layer with a spread above 0 comes out of its source as two channels, already at the level of a centred
layer (noise: two independent streams mixed so that their correlation is cos(spread x 90 degrees); drops: each bubble
placed on its own; wind: the wake drawn twice), and its pan then works as a balance.

Loops (render_loop): the sound is one period of a loop. A layer that starts at 0 and lasts the whole period is a bed:
its source, filter and drive run circularly, so the period joins its own start (noise shaped over the period, bubbles
and filter tails wrapping round, tones holding whole numbers of cycles). Any other layer is an event: rendered as in a
one-shot and folded onto the period (what runs past the end comes in at the start). The chain and the DC block run
circularly too, and nothing is trimmed or faded."""
import math

import numpy as np

from . import artifacts, chip, curves, dsp, drops, dynamics, filters, fx, modal, noise, osc, wind
from . import instruments as recorded
from . import stretch as timestretch

RATE = 48000
END_FADE_S = 0.002       # an envelope written open is closed by this fade at the layer's end
TRIM_REL_DB = -70.0      # the sound ends this long after its last sample within 70 dB of its peak:
TRIM_FADE_S = 0.005      # a 5 ms fade, so the file never stops at a level
DC_BLOCK_HZ = 10.0       # a first-order high-pass below hearing takes out any DC (radiated sound has none)
EVENT_MAX_S = 3.0        # a layer of a loop up to this long is an event that should be heard over the rest of the loop
OVER_REST_S = 0.05       # ... judged over its loudest 50 ms
RUN_OUT_MIN_S = 0.01     # a layer that outlasts its recording by less than this is only rounding
RUN_OUT_LEVEL = 0.1      # ... and a layer under -20 dB there, by its envelope and how far the recording has died away
STRAY_ENVELOPE = 0.1     # a tick the layer's envelope holds under -20 dB is not in the sound
RUN_OUT_TAIL_S = 0.01    #     (the recording's last 10 ms against its level over the part played), has let it go
SPREAD_SOURCES = ('noise', 'drops', 'wind')
BED_SOURCES = ('noise', 'drops', 'wind', 'osc', 'fm')


def _rng(seed, index, part=None):
    parts = (seed, 'layer', index) if part is None else (seed, 'layer', index, part)
    return np.random.default_rng(dsp.seed_for(*parts))


def _source(layer, freq, n, seed, index, channels=1, loop=False, ratio=None, instruments=None, samples=None, info=None):
    """The source's samples: 1-D, or n x 2 for a spread source or a recording in a stereo sound (see the module's
    notes). `instruments` renders recorded layers (instruments.Provider). `info`, a dict, is told where a recording
    ended if it ended before the layer did."""
    src = layer['source']
    kind = src['type']
    spread = float(layer.get('spread', 0.0)) if channels == 2 else 0.0
    if kind in ('instrument', 'surge'):
        if loop:
            raise ValueError(f"{'an instrument' if kind == 'instrument' else 'a Surge'} source cannot be the bed of a loop; "
                             'play it as an event (a shorter layer)')
        if instruments is None:
            raise ValueError("an instrument layer plays on music-production's sample banks: render with its "
                             'configuration (--config)' if kind == 'instrument' else
                             "a Surge layer plays a take kept beside the project: render it with sfx.py render")
        played = instruments.render(src)
        if kind == 'instrument' and info is not None:      # read on the notes as the bank plays them, before anything is done to them
            speed = 2 ** (float(src.get('cents', 0.0)) / 1200)
            info['stray'] = [int(round(t * RATE / speed)) for t in artifacts.stray_attacks(played, [note[0] for note in src['notes']])]
        x = recorded.placed(recorded.detune(played, src.get('cents', 0.0)), channels)
        out = np.zeros((n,) + x.shape[1:])
        out[:min(n, len(x))] = x[:n]
        return out
    if kind == 'sample':
        return _sample(src, n, channels, loop, samples, seed=(int(seed) * 1000003 + int(index)) & 0xFFFFFFFF, info=info)
    if kind == 'osc':
        return osc.wave(src.get('wave', 'sine'), freq, duty=src.get('duty', 0.5), phase0_deg=src.get('phase', 0.0))
    if kind == 'fm':
        index_curve = curves.evaluate(src.get('index', 1.0), n)
        return osc.fm(freq, ratio=src.get('ratio', 1.0) if ratio is None else ratio, index=index_curve,
                      phase0_deg=src.get('phase', 0.0), loop=loop)
    if kind == 'noise':
        a = noise.make(src.get('color', 'white'), n, _rng(seed, index))
        if spread <= 0:
            return a
        b = noise.make(src.get('color', 'white'), n, _rng(seed, index, 'b'))
        theta = (1.0 - spread) * math.pi / 4
        return np.stack([math.cos(theta) * a + math.sin(theta) * b, math.sin(theta) * a + math.cos(theta) * b],
                        axis=1) * math.sqrt(0.5)
    if kind == 'drops':
        frames = drops.render(n, _rng(seed, index), rate=src.get('rate', 1000.0), r_min_mm=src.get('r_min_mm', 0.2),
                              r_max_mm=src.get('r_max_mm', 3.0), gamma=src.get('size_slope', 5.0),
                              alpha=src.get('gain_slope', 1.5), beta=src.get('depth', 10.0),
                              rise=src.get('rise', 0.1), rise_cutoff=src.get('rise_cutoff', 0.9),
                              channels=2 if spread > 0 else 1, spread=spread, loop=loop)
        return frames[:, 0] if frames.shape[1] == 1 else frames
    if kind == 'wind':
        frames = wind.render(n, lambda part: _rng(seed, index, part), band_hz=src.get('band_hz', 200.0),
                             turbulence=src.get('turbulence', 0.2), gust_hz=src.get('gust_hz', 0.2),
                             whistle_hz=src.get('whistle_hz', 0.0), whistle_db=src.get('whistle_db', -12.0),
                             whistle_q=src.get('whistle_q', 60.0), channels=2 if spread > 0 else 1, spread=spread,
                             loop=loop)
        return frames[:, 0] if frames.shape[1] == 1 else frames
    if loop:
        raise ValueError(f"a {kind} source cannot be the bed of a loop; play it as an event (a shorter layer)")
    if kind == 'chip':
        return chip.wave(src.get('wave', 'pulse'), freq, duty=src.get('duty', 0.5), quantize=src.get('quantize', True),
                         rng=_rng(seed, index), mode=src.get('mode', 'long'))
    if kind == 'modal':
        t_d = src['t_d'] if 't_d' in src else modal.MATERIAL_TD[src.get('material', 'wood')]
        return modal.render(float(freq[0]), n, t_d=t_d, modes=src.get('modes', 6), hardness=src.get('hardness', 0.5),
                            detune_cents=src.get('detune_cents', 0.0), rng=_rng(seed, index),
                            shape=src.get('shape', 'bar'), ratios=src.get('ratios'))
    raise ValueError(f"unknown source type {kind!r}; use osc, fm, noise, chip, modal, drops or wind")


SAMPLE_MARGIN = 4096          # samples beyond what a sample layer reaches, resampled with it


def _sample(src, n, channels, loop, samples, seed=0, info=None):
    """A sample layer's recording (samples.py): reversed when asked, from `offset` seconds of the recording, moved by
    `cents` (band-limited resampling, so a recording played higher is also shorter), placed for the sound's channels
    as an instrument layer is, and cut or padded to the layer's length. `time_stretch` lengthens (or shortens) it
    without a change of pitch first (stretch.py)."""
    if loop:
        raise ValueError('a sample source cannot be the bed of a loop; play it as an event (a shorter layer)')
    if samples is None:
        raise ValueError('a sample layer plays a recording of a sound library: render with --samples LIBRARY '
                         '(sfx.py samples)')
    x = samples.recording(src['library'], src['file'], src.get('denoise'))
    if src.get('reverse'):
        x = x[::-1]
    start = curves.samples(src.get('offset', 0.0))
    if start >= len(x):
        raise ValueError(f"offset {src.get('offset')} s is past the end of {src['file']} ({len(x) / RATE:.3f} s)")
    cents = float(src.get('cents', 0.0))
    ts = src.get('time_stretch')
    factor = float(ts['factor']) if ts else 1.0
    # only the part of the recording the layer can reach is processed (with a margin for the transforms' edges): four
    # octaves down, a 30 s recording would otherwise become eight minutes of samples
    margin = SAMPLE_MARGIN + (timestretch.SMEAR_N if ts and ts.get('method') == 'smear' else timestretch.N) * bool(ts)
    heard = int(math.ceil(n * 2 ** (cents / 1200)))
    need = (timestretch.reach(heard, factor, ts.get('keep_attack_ms', 0.0)) if ts else heard) + margin
    x = x[start:start + need]
    if ts:
        x = timestretch.stretch(x, factor, ts.get('method', 'phase-locked'), ts.get('keep_attack_ms', 0.0), seed)
    x = recorded.placed(recorded.detune(x, cents), channels)
    if info is not None and len(x) < n:
        whole = float(np.sqrt(np.mean(x ** 2)))
        info['ends'] = len(x)
        info['end_level'] = float(np.sqrt(np.mean(x[-curves.samples(RUN_OUT_TAIL_S):] ** 2))) / whole if whole > 0 else 0.0
    out = np.zeros((n,) + x.shape[1:])
    out[:min(n, len(x))] = x[:n]
    return out


def _envelope_points(layer, n):
    env = layer.get('env')
    if env is None:
        length = n / RATE
        fade = min(END_FADE_S, length / 2)
        return [[0.0, 0.0, 'cos'], [fade, 1.0], [length - fade, 1.0, 'cos'], [length, 0.0]]
    if isinstance(env, dict):
        return curves.envelope_points(env)
    return env


def _end_fade(values, n):
    if values[-1] != 0.0:
        m = min(curves.samples(END_FADE_S), n)
        values[n - m:] *= np.sin(np.pi / 2 * (1 - (np.arange(m) + 1) / m)) ** 2
    return values


def _envelope(layer, n, loop=False):
    """The layer's envelope per sample. A bed of a loop has none (full level throughout) or a curve whose first and
    last values agree, and is never closed by a fade."""
    if loop:
        env = layer.get('env')
        if env is None:
            return np.ones(n)
        if isinstance(env, dict):
            raise ValueError('the bed of a loop takes an envelope curve whose first and last values agree, not a shape')
        return curves.evaluate(env, n)
    return _end_fade(curves.evaluate(_envelope_points(layer, n), n), n)


def _per_channel(sig, fn):
    if sig.ndim == 1:
        return fn(sig)
    return np.stack([fn(sig[:, c]) for c in range(sig.shape[1])], axis=1)


def layer_signal(layer, seed, index, channels=1, loop=False, instruments=None, samples=None, info=None):
    """The layer's samples from its own start, envelope and gain applied, not yet placed: 1-D, or n x 2 for a spread
    source in a stereo sound. loop=True renders it as the bed of a loop (see the module's notes). A chip layer's pitch
    and envelope change in steps of 1/step_hz seconds and its envelope takes `levels` steps (chip.py). `info`, a dict,
    receives where a recording ran out (`ends`, in samples) and how much of it was still sounding there (`level_there`)."""
    n = max(1, curves.samples(layer['length']))
    src = layer['source']
    if src['type'] == 'chip':
        if loop:
            raise ValueError('a chip source cannot be the bed of a loop; play it as an event (a shorter layer)')
        step_hz = src.get('step_hz', 60.0)
        freq = chip.stepped(layer.get('pitch', 440.0), n, step_hz, default='exp')
        sig = _source(layer, freq, n, seed, index)
        env = _end_fade(chip.quantize_levels(chip.stepped(_envelope_points(layer, n), n, step_hz, opening=True),
                                             src.get('levels', 16)), n)
    else:
        freq = curves.evaluate(layer.get('pitch', 440.0), n, default='exp')
        vib = layer.get('vibrato')
        if vib:
            rate = vib.get('rate_hz', 5.0)
            freq = osc.vibrato(freq, osc.whole_rate(rate, n) if loop else rate, vib.get('depth_cents', 0.0))
        ratio = None
        if loop and src['type'] in ('osc', 'fm'):
            freq, _ = osc.whole_cycles(freq)
            if src['type'] == 'fm':
                cycles = float(np.sum(freq)) / RATE
                ratio = max(1.0, round(float(src.get('ratio', 1.0)) * cycles)) / cycles
        sig = _source(layer, freq, n, seed, index, channels, loop, ratio, instruments, samples, info)
        env = None
    flt = layer.get('filter')
    if flt:
        cutoff = flt['cutoff']
        cutoff = cutoff if isinstance(cutoff, (int, float)) else curves.evaluate(cutoff, n, default='exp')
        sig = _per_channel(sig, lambda x: filters.filter(x, flt['type'], cutoff, flt.get('q', 0.7071), loop=loop))
    if layer.get('drive'):
        sig = fx.drive(sig, layer['drive'], loop=loop)
    trem = layer.get('tremolo')
    if trem:
        gain = osc.tremolo(n, osc.whole_rate(trem['rate_hz'], n) if loop else trem['rate_hz'], trem['depth'])
        sig = sig * (gain if sig.ndim == 1 else gain[:, None])
    if env is None:
        env = _envelope(layer, n, loop)
    if info is not None and 'ends' in info:
        info['level_there'] = float(env[min(info['ends'], n - 1)]) * info['end_level']
    if info is not None and info.get('stray'):           # a tick the layer ends or fades before is not in the sound
        info['stray'] = [k for k in info['stray'] if k < n and float(env[k]) >= STRAY_ENVELOPE]
    sig = sig * (env if sig.ndim == 1 else env[:, None])
    return sig * 10 ** (float(layer.get('gain_db', 0.0)) / 20)


def render_layer(layer, seed, index):
    """A layer of a mono sound: its samples (1-D) from its own start, envelope and gain applied."""
    return layer_signal(layer, seed, index)


def place(sig, pan, channels):
    """Frames (n x channels): a mono signal panned with the constant-power law, a two-channel one balanced."""
    if channels == 1:
        return sig[:, None]
    pan = min(max(float(pan), -1.0), 1.0)
    theta = (pan + 1) * math.pi / 4
    # at the centre both gains are one number: the sine and cosine of 45 degrees differ in their last bit on some systems
    left, right = (math.sqrt(0.5), math.sqrt(0.5)) if pan == 0 else (math.cos(theta), math.sin(theta))
    if sig.ndim == 1:
        return np.stack([left * sig, right * sig], axis=1)
    return np.stack([math.sqrt(2) * left * sig[:, 0], math.sqrt(2) * right * sig[:, 1]], axis=1)


def _chain(x, chain, loop=False):
    if not chain:
        return x
    if 'console' in chain:
        x = chip.console(x, chain['console'].get('model', 'nes'), loop=loop)
    if 'eq' in chain:
        e = chain['eq']
        x = fx.eq(x, e.get('low_cut_hz'), e.get('high_cut_hz'), e.get('peak'), e.get('low_shelf'), e.get('high_shelf'),
                  loop=loop)
    if 'tilt' in chain:
        x = fx.tilt(x, chain['tilt'].get('db', 0.0), loop=loop)
    if 'saturation' in chain:
        s = chain['saturation']
        mix = float(s.get('mix', 1.0))
        x = (1 - mix) * x + mix * fx.drive(x, s.get('drive', 0.5), loop=loop)
    if 'compressor' in chain:
        c = chain['compressor']
        x = dynamics.compress(x, c.get('threshold_db', -18.0), c.get('ratio', 4.0), c.get('knee_db', 6.0),
                              c.get('attack_ms', 5.0), c.get('release_ms', 80.0), c.get('makeup_db', 0.0),
                              c.get('mix', 1.0), loop=loop)
    if 'delay' in chain:
        d = chain['delay']
        x = fx.delay(x, d['time'], d.get('feedback', 0.3), d.get('mix', 0.3), d.get('damping_hz'), loop=loop)
    if 'room' in chain:
        r = chain['room']
        x = fx.room(x, r.get('size', 1.0), r.get('decay', 0.6), r.get('mix', 0.2), loop=loop)
    if 'fade' in chain:
        if loop:
            raise ValueError('a loop has no fades: it plays on from its end into its start')
        f = chain['fade']
        x = fx.fade(x, f.get('in', 0.0), f.get('out', 0.0))
    return x


def _tidy_ending(x):
    """Cut after the last frame within TRIM_REL_DB of the peak plus TRIM_FADE_S, faded to exactly 0."""
    env = np.max(np.abs(x), axis=1)
    peak = float(np.max(env)) if len(env) else 0.0
    if peak <= 0:
        return x
    last = int(np.nonzero(env >= peak * 10 ** (TRIM_REL_DB / 20))[0][-1])
    m = curves.samples(TRIM_FADE_S)
    end = min(len(x), last + 1 + m)
    y = x[:end].copy()
    m = min(m, end)
    y[end - m:] *= (np.sin(np.pi / 2 * (1 - (np.arange(m) + 1) / m)) ** 2)[:, None]
    return y


def _layer_report(layer, frames, start, info=None):
    """What a layer contributes before the chain: its source, peak in dB and when it peaks in the sound, in seconds;
    with `runs_out_s` and `length_s` when its recording ended while the envelope still held it (a hole in the sound)."""
    source = layer.get('source', {})
    name = (f"{source['library']}/{source['file']}" if source.get('type') == 'sample' else
            f"surge:{source['patch']}" if source.get('type') == 'surge' else source.get('type', ''))
    level = np.max(np.abs(frames.reshape(len(frames), -1)), axis=1) if len(frames) else np.zeros(1)
    top = int(np.argmax(level))
    out = {'source': name, 'peak_db': round(20 * math.log10(max(float(level[top]), 1e-10)), 1),
           'peak_s': round((start + top) / RATE, 3)}
    if info and info.get('level_there', 0.0) >= RUN_OUT_LEVEL and len(frames) - info['ends'] >= RUN_OUT_MIN_S * RATE:
        out['runs_out_s'] = round(info['ends'] / RATE, 3)
        out['length_s'] = round(len(frames) / RATE, 3)
    if info and info.get('stray'):
        out['stray_attacks_s'] = [round((start + k) / RATE, 3) for k in info['stray']]
    return out


def render_sound(spec, seed, channels=1, instruments=None, samples=None, report=None):
    """A one-shot sound {'layers': [...], 'chain': {...}} as frames (n x channels). `report`, a list, receives what each
    layer contributes."""
    rendered = []
    for i, layer in enumerate(spec['layers']):
        start = curves.samples(layer.get('start', 0.0))
        info = {}
        rendered.append((start, place(layer_signal(layer, seed, i, channels, instruments=instruments, samples=samples,
                                                   info=info),
                                      layer.get('pan', 0.0), channels)))
        if report is not None:
            report.append(_layer_report(layer, rendered[-1][1], start, info))
    total = max(start + len(frames) for start, frames in rendered)
    out = np.zeros((total, channels))
    for start, frames in rendered:
        out[start:start + len(frames)] += frames
    out = _chain(out, spec.get('chain'))
    if out.ndim == 1:
        out = out[:, None]
    return _tidy_ending(_dc_block(out))


def is_bed(layer, period):
    """True when the layer starts at 0 and lasts exactly the loop's period (in whole samples)."""
    return curves.samples(layer.get('start', 0.0)) == 0 and max(1, curves.samples(layer['length'])) == period


def _fold(out, frames, start):
    """Add frames into the period `out` from `start`, continuing from the period's start whenever they run past its end."""
    period = len(out)
    pos, i = start % period, 0
    while i < len(frames):
        take = min(period - pos, len(frames) - i)
        out[pos:pos + take] += frames[i:i + take]
        i += take
        pos = 0


def _over_the_rest(frames, start, mix):
    """How far an event of a loop stands over the rest of the loop, in dB: their energies over the event's loudest 50 ms,
    in the octave around the event's own centre of energy (a bird over low wind is clear at a level a broadband reading
    would call buried). `mix` is the loop with the event in it, before the chain."""
    w = max(1, curves.samples(OVER_REST_S))
    event = frames.mean(axis=1)
    event = np.pad(event, (0, max(0, w - len(event))))
    energy = np.concatenate([[0.0], np.cumsum(event ** 2)])
    a = int(np.argmax(energy[w:] - energy[:-w]))
    rest = mix[(start + a + np.arange(w)) % len(mix)].mean(axis=1) - event[a:a + w]
    window = np.hanning(w)
    e, r = (np.abs(np.fft.rfft(part * window)) ** 2 for part in (event[a:a + w], rest))
    f = np.fft.rfftfreq(w, 1 / RATE)
    audible = (f >= 20) & (f < 20000)
    if not float(np.sum(e[audible])) > 0:
        return None
    centre = float(np.sum(f[audible] * e[audible]) / np.sum(e[audible]))
    band = (f >= centre / math.sqrt(2)) & (f < centre * math.sqrt(2))
    return round(min(60.0, 10 * math.log10(max(float(np.sum(e[band])), 1e-20) / max(float(np.sum(r[band])), 1e-20))), 1)


def render_loop(spec, seed, channels, seconds, instruments=None, samples=None, report=None):
    """One period of a loop of `seconds` as frames (period x channels); see the module's notes."""
    period = max(1, curves.samples(seconds))
    out = np.zeros((period, channels))
    events = []
    for i, layer in enumerate(spec['layers']):
        bed = is_bed(layer, period)
        info = {}
        frames = place(layer_signal(layer, seed, i, channels, loop=bed, instruments=instruments, samples=samples, info=info),
                       layer.get('pan', 0.0),
                       channels)
        _fold(out, frames, curves.samples(layer.get('start', 0.0)))
        if report is not None:
            report.append(_layer_report(layer, frames, curves.samples(layer.get('start', 0.0)), info))
            if not bed and len(frames) <= EVENT_MAX_S * RATE and len(spec['layers']) > 1:
                events.append((report[-1], frames, curves.samples(layer.get('start', 0.0))))
    for row, frames, start in events:
        over = _over_the_rest(frames, start, out)
        if over is not None:
            row['over_rest_db'] = over
    out = _chain(out, spec.get('chain'), loop=True)
    return _dc_block(out, loop=True)


def _dc_block(x, loop=False):
    """The sound through a first-order high-pass at DC_BLOCK_HZ, lengthened by 0.1 s for the filter to settle; a loop's
    period through it circularly, keeping its length."""
    section = [filters.first_order('highpass', DC_BLOCK_HZ)]
    if loop:
        return np.stack([filters.apply_circular(x[:, c], section) for c in range(x.shape[1])], axis=1)
    length = len(x) + int(0.1 * RATE)
    return np.stack([filters.apply(x[:, c], section, length) for c in range(x.shape[1])], axis=1)
