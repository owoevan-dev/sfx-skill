"""The check of a render: every master file measured as it is on disk, the variants of each sound compared, and the
findings that deserve a look listed as flags. A flag is a question for whoever revises the sound; nothing here says a
sound is good. The thresholds are named below, with their reasons in references/checks.md."""
import json
import math
from pathlib import Path

import numpy as np

from . import artifacts, loudness, measure, record, traits

RATE = 48000
ONSET_FLAG_MS = 1.0                  # ui sounds should start at once (audio feedback budget: 20-70 ms)
ONSET_CATEGORIES = ('ui',)
DC_FLAG = 0.001                      # -60 dBFS of offset
PEAK_TOLERANCE_DB = 0.1              # the 24-bit dither may lift a true peak a hair over the ceiling
OUTSIDE_SHARE_FLAG = 0.5             # ui sounds with most of their energy outside 150 Hz - 5 kHz
VARIANT_SPREAD_FLAG_DB = 0.5         # variants of one sound should sit at one loudness after the level step
STRIKE_SPREAD_FLAG_MS = 20.0         # variants of a struck sound landing further apart than this fall unevenly in turn
CLICK_LAYER_S = 0.005                # a layer peaking this close to a click is named in the flag
BURIED_DB = 3.0                      # an event of a loop less than this over the rest there, in its own octave
LOOP_UNDER_DB = 3.0                  # a loop closer than this to the loudest one-shot sits on top of it (this skill's choice)
HALVES_FLAG_DB = 20.0               # a loop this much louder in one half pulses each time round
STRIKE_WITHIN_MS = 150.0             # ... counting those that land this soon in their file
STRIKE_RISE_MS = 50.0                # ... and reach their loudest this soon after (a swell has no landing to keep)
THIRD_OCTAVES = 100.0 * 2 ** (np.arange(0, 22) / 3)   # 100 Hz to about 12.7 kHz
SEAM_WINDOW_S = 0.1                  # a loop's join: spectra of 100 ms windows compared across it and inside
SEAM_FLAG_RATIO = 2.0                # a join flagged when it jumps more than twice the largest step inside the loop
JOIN_WINDOW_S = 0.002                # ... or when the 2 ms across it are 3 dB quieter than any 2 ms inside the loop
SEAM_DIP_MARGIN_DB = 3.0
SEAM_DIP_DB = -6.0                   #     and at least 6 dB under the loop's median 2 ms level (a dip, a fade)
STEREO_FLAG_CORRELATION = -0.2       # independent channels wander around 0; below this they really cancel


def band_levels_db(x):
    """Energy in third-octave bands from 100 Hz (dB, relative to full-scale power), for comparing variants."""
    frames = np.asarray(x, dtype=np.float64)
    frames = frames[:, None] if frames.ndim == 1 else frames
    n = len(frames)
    power = np.zeros(n // 2 + 1)
    for ch in range(frames.shape[1]):
        power += np.abs(np.fft.rfft(frames[:, ch] * np.hanning(n))) ** 2
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    out = []
    for c in THIRD_OCTAVES:
        band = power[(freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))]
        out.append(10 * math.log10(float(np.sum(band)) + 1e-30))
    return np.array(out)


def spectral_distance_db(a, b):
    """Mean absolute difference of two sounds' third-octave levels, over bands within 60 dB of the louder sound's
    strongest band. This skill's measure of how far a variant moved, not a perceptual scale."""
    la, lb = band_levels_db(a), band_levels_db(b)
    top = max(np.max(la), np.max(lb))
    keep = np.maximum(la, lb) > top - 60
    return float(np.mean(np.abs(la[keep] - lb[keep]))) if np.any(keep) else 0.0


def measure_file(x):
    return {'duration_s': len(x) / RATE, 'sample_peak_db': measure.peak_db(x),
            'true_peak_db': loudness.true_peak_db(x), 'brief_lufs': loudness.brief(x),
            'max_momentary_lufs': loudness.max_momentary(x), 'integrated_lufs': loudness.integrated(x), 'onset_ms': measure.onset_ms(x), **measure.landing(x),
            'ending': measure.ending(x), 'dc_offset': measure.dc_offset(x),
            'clipped': measure.clipped(x), 'bands': measure.band_shares(x), 'centroid_hz': measure.centroid_hz(x),
            'stereo': measure.stereo(x)}


def loop_seam(x):
    """How a loop's period joins its own start, against how it moves inside: `step`, the largest jump from its last
    frame to its first (over channels), and `largest_inner_step`, the largest jump between neighbouring frames inside;
    `spectral_db`, the spectral distance (spectral_distance_db) between its last and first 100 ms, and
    `inner_spectral_db_p95`, the 95th percentile of that distance between neighbouring 100 ms windows inside;
    `join_level_db`, the level of the 2 ms across the join, and `quietest_inner_level_db`, the level of the quietest
    2 ms inside (windows every 1 ms), both against the median 2 ms level (None for silence)."""
    frames = np.asarray(x, dtype=np.float64)
    frames = frames[:, None] if frames.ndim == 1 else frames
    step = float(np.max(np.abs(frames[0] - frames[-1])))
    inner = float(np.max(np.abs(np.diff(frames, axis=0)))) if len(frames) > 1 else 0.0
    w = int(SEAM_WINDOW_S * RATE)
    count = len(frames) // w
    out = {'step': step, 'largest_inner_step': inner, 'spectral_db': None, 'inner_spectral_db_p95': None,
           'join_level_db': None, 'quietest_inner_level_db': None}
    w2 = int(JOIN_WINDOW_S * RATE)
    half = w2 // 2
    if len(frames) >= 4 * w2:
        power = np.sum(frames ** 2, axis=1)
        c = np.concatenate([[0.0], np.cumsum(power)])
        starts = np.arange(0, len(frames) - w2 + 1, half)
        levels = (c[starts + w2] - c[starts]) / w2
        median = float(np.median(levels))
        across = float(np.mean(np.concatenate([power[-half:], power[:w2 - half]])))
        if median > 0:
            out['join_level_db'] = 10 * math.log10(max(across, 1e-30) / median)
            out['quietest_inner_level_db'] = 10 * math.log10(max(float(np.min(levels)), 1e-30) / median)
    if count >= 3:
        windows = [frames[i * w:(i + 1) * w] for i in range(count)]
        inner_d = [spectral_distance_db(windows[i], windows[i + 1]) for i in range(count - 1)]
        out['spectral_db'] = spectral_distance_db(frames[-w:], frames[:w])
        out['inner_spectral_db_p95'] = float(np.percentile(inner_d, 95))
    return out


def _half_silent(x, half, a, b):
    """True when one half holds next to nothing: it has no loudness of its own and its peak lies far under the other's. (A
    short half can sound and still have no loudness, when what it holds falls outside its complete 400 ms blocks.)"""
    if (a is None) == (b is None):
        return False
    quiet, loud = (x[:half], x[half:]) if a is None else (x[half:], x[:half])
    return bool(measure.peak_db(quiet) < measure.peak_db(loud) - HALVES_FLAG_DB)


def measure_loop(x):
    """A loop's period measured as it plays on: true peak and loudness read circularly (loudness.loop_*), the two
    halves' integrated loudness compared, the join (loop_seam); no onset or ending, which a loop does not have."""
    half = len(x) // 2
    a, b = loudness.integrated(x[:half]), loudness.integrated(x[half:])
    return {'duration_s': len(x) / RATE, 'sample_peak_db': measure.peak_db(x),
            'true_peak_db': loudness.loop_true_peak_db(x), 'max_momentary_lufs': float(np.max(loudness.loop_momentary(x))),
            'integrated_lufs': loudness.loop_integrated(x), 'loudness_range_lu': loudness.loop_range(x),
            'halves_db': (b - a) if a is not None and b is not None else None, 'half_silent': _half_silent(x, half, a, b),
            'onset_ms': None, 'ending': None, 'seam': loop_seam(x), 'dc_offset': measure.dc_offset(x),
            'clipped': measure.clipped(x), 'bands': measure.band_shares(x), 'centroid_hz': measure.centroid_hz(x),
            'stereo': measure.stereo(x)}


def _finite(value):
    """JSON has no infinities: -inf becomes None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    return value


def run(render_dir):
    """Measure the render in render_dir, write check.json there and return it."""
    folder = Path(render_dir)
    resolved = record.load(folder)
    ceiling = resolved['level']['ceiling_dbtp']
    files, sounds, flags = [], [], []

    def flag(kind, sound, file, message):
        flags.append({'kind': kind, 'sound': sound, 'file': file, 'message': message})

    for sound in resolved['sounds']:
        audio, strikes = [], []
        is_loop = sound.get('use') == 'loop'
        for v in sound['variants']:
            data = record.master(folder, v['file'])
            x = data.astype(np.float64)
            audio.append(x)
            m = measure_loop(x) if is_loop else measure_file(x)
            m['file'] = v['file']
            m['sound'] = sound['id']
            files.append(m)
            name = v['file']
            if not is_loop and m['strike_ms'] <= STRIKE_WITHIN_MS and m['strike_rise_ms'] <= STRIKE_RISE_MS:
                strikes.append(m['strike_ms'])
            for number, layer in enumerate(v.get('layers', []), 1):
                if 'runs_out_s' in layer:
                    flag('short_recording', sound['id'], name, f"layer {number} ({layer['source']}) lasts "
                         f"{layer['length_s']:.2f} s, but at this offset, pitch and stretch its recording runs out after "
                         f"{layer['runs_out_s']:.2f} s and it falls silent: start earlier in the recording, shorten the "
                         'layer or use a longer one')
            if is_loop and m['half_silent']:
                flag('loop_halves', sound['id'], name, 'one of its halves is silent: a gap that comes back each time round')
            elif is_loop and m['halves_db'] is not None and abs(m['halves_db']) > HALVES_FLAG_DB:
                flag('loop_halves', sound['id'], name, f"its second half is {m['halves_db']:+.1f} dB against its first: a "
                     'swell or a gap that repeats each time round (a whole-loop layer with a fading envelope?)')
            buried = [(i, layer) for i, layer in enumerate(v.get('layers', []), 1)
                      if layer.get('over_rest_db') is not None and layer['over_rest_db'] < BURIED_DB]
            if buried:
                listed = ', '.join(f"layer {i} ({layer['source']}, {layer['over_rest_db']:+.0f} dB)" for i, layer in buried[:5])
                flag('buried_event', sound['id'], name, f'{len(buried)} of its events barely rise over the rest of the loop at their '
                     f'loudest moment, in their own octave: {listed}. They will hardly be heard: raise them, or lower or '
                     'filter the bed in their band')
            if is_loop and m['seam']['step'] > SEAM_FLAG_RATIO * m['seam']['largest_inner_step']:
                flag('seam', sound['id'], name, f"the loop jumps by {m['seam']['step']:.4f} where its end meets its "
                     f"start, more than twice any step inside it ({m['seam']['largest_inner_step']:.4f}): it may click "
                     'there')
            seam = m.get('seam') or {}
            join, quietest = seam.get('join_level_db'), seam.get('quietest_inner_level_db')
            if join is not None and join < quietest - SEAM_DIP_MARGIN_DB and join < SEAM_DIP_DB:
                flag('seam', sound['id'], name, f'the 2 ms where the loop meets its start sit {join:.0f} dB under its '
                     'median level, quieter than anywhere inside it: a dip (a fade?) will be heard each time round')
            if not is_loop and sound['category'] in ONSET_CATEGORIES and m['onset_ms'] > ONSET_FLAG_MS:
                flag('onset', sound['id'], name, f"starts {m['onset_ms']:.1f} ms after the file begins; a "
                     f"{sound['category']} sound should start at once (audio feedback has a total budget of about "
                     '20-70 ms, Kaaresoja 2016, and the device takes part of it)')
            if not is_loop and m['ending']['cut']:
                flag('ending', sound['id'], name, f"its last millisecond is still at {m['ending']['last_ms_rel_db']:.0f} "
                     'dB of its peak: it stops at a level and will click')
            if abs(m['dc_offset']) > DC_FLAG:
                flag('dc', sound['id'], name, f"DC offset {m['dc_offset']:+.4f}")
            if m['clipped']:
                flag('clipped', sound['id'], name, f"{m['clipped']} samples at full scale")
            if m['true_peak_db'] > ceiling + PEAK_TOLERANCE_DB:
                flag('true_peak', sound['id'], name, f"true peak {m['true_peak_db']:.2f} dBTP over the ceiling "
                     f'{ceiling:.1f}')
            outside = m['bands']['low'] + m['bands']['high']
            if sound['category'] == 'ui' and outside > OUTSIDE_SHARE_FLAG:
                flag('band', sound['id'], name, f"{outside:.0%} of its energy lies outside 150 Hz - 5 kHz, the "
                     'pitch range Brewster, Wright and Edwards give for earcons')
            if not is_loop:
                m['clicks'] = artifacts.clicks(x)
                if m['clicks'] and not sound.get('crackle'):
                    shown = ', '.join(f'{t:.3f}' for t in m['clicks'][:5])
                    where = [f"layer {i} ({layer['source']})" for i, layer in enumerate(v.get('layers', []), 1)
                             if any(abs(layer['peak_s'] - t) <= CLICK_LAYER_S for t in m['clicks'])]
                    near = f" (where {', '.join(where[:3])} peaks)" if where else ''
                    flag('click', sound['id'], name, f"isolated click{'s' if len(m['clicks']) > 1 else ''} at {shown} s{near}: a "
                         'spike above 3 kHz that falls back within milliseconds, far above what surrounds it (a cut '
                         'into a waveform, a reversed transient ending abruptly, a recording\'s crackle); intended?')
            if v is sound['variants'][0]:
                finding = artifacts.peak_at_finding(x, sound.get('peak_at'), loop=is_loop)
                if finding:
                    flag('peak_at', sound['id'], name, finding)
            if m['stereo'] and m['stereo']['correlation'] < STEREO_FLAG_CORRELATION:
                flag('stereo', sound['id'], name, f"left and right are anti-correlated ({m['stereo']['correlation']:.2f})"
                     '; the sound may cancel when summed to mono')
        loud = [(loudness.loop_integrated(x) if is_loop else loudness.brief(x)) for x in audio]
        loud = [-np.inf if v is None else v for v in loud]
        spread = max(loud) - min(loud) if all(math.isfinite(v) for v in loud) else None   # a file under the loudness gate
        first = audio[0]
        moves = []
        for x in audio[1:]:
            moves.append({'length_change_pct': 100 * (len(x) / len(first) - 1),
                          'centroid_change_pct': 100 * (measure.centroid_hz(x) / max(measure.centroid_hz(first), 1e-9)
                                                        - 1),
                          'spectral_distance_db': spectral_distance_db(first, x)})
        if len(audio) > 1 and spread is not None and spread > VARIANT_SPREAD_FLAG_DB and not sound['vary'].get('level_db'):
            flag('variant_level', sound['id'], sound['variants'][0]['file'],
                 f'its variants differ by {spread:.1f} dB in loudness')
        if len(strikes) > 1 and max(strikes) - min(strikes) > STRIKE_SPREAD_FLAG_MS:
            flag('variant_timing', sound['id'], sound['variants'][0]['file'],
                 f'its variants land {min(strikes):.0f} to {max(strikes):.0f} ms after their files start, so played in '
                 "turn they fall unevenly: line them up (each take's offset, from samples --events, or a layer's start)")
        sounds.append({'id': sound['id'], 'category': sound['category'], 'variants': len(audio),
                       'variant_loudness_spread_db': spread, 'variants_against_first': moves,
                       'traits': traits.summary(audio, loop=is_loop)})
    hits = [f['brief_lufs'] for f in files if f.get('brief_lufs') is not None and math.isfinite(f['brief_lufs'])]
    for sound in resolved['sounds'] if hits else ():
        beds = [f for f in files if sound.get('use') == 'loop' and f['sound'] == sound['id']
                and f['integrated_lufs'] is not None and math.isfinite(f['integrated_lufs'])]
        if beds:
            bed = max(beds, key=lambda f: f['integrated_lufs'])
            if bed['integrated_lufs'] > max(hits) - LOOP_UNDER_DB:
                flag('loop_level', sound['id'], bed['file'], f"this loop ({bed['integrated_lufs']:.1f} LUFS) is within "
                     f'{LOOP_UNDER_DB:.0f} dB of the loudest one-shot ({max(hits):.1f} LUFS, its loudest 200 ms), so it will sit on '
                     'top of the sounds played over it: give it a negative trim_db, or leave it if this loop is meant to lead')
    report = {'level': resolved['level'], 'files': [_finite(f) for f in files], 'sounds': sounds, 'flags': flags}
    (folder / 'check.json').write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8', newline='\n')
    return report
