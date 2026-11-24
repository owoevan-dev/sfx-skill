"""Loops: circular filters, effects and tones that join their own start; the drops and wind sources; the loop render,
its loudness, true peak and checks; the schema's loop rules; the ambience recipes and their example."""
import json
import math
import shutil
import subprocess
import unittest
from unittest import mock

import numpy as np

import helpers
from sfxlib import check, curves, dsp, drops, filters, fx, loudness, osc, project, recipes, render, synth, wav, wind
from test_recipes_ui import settings

RATE = 48000
AMBIENCE = ('ambience.rain_light', 'ambience.rain_heavy', 'ambience.breeze', 'ambience.wind_gusty')
EXAMPLE = helpers.EXAMPLES / 'lighthouse-cove-ambience.json'


def settled_period(fn, x, copies):
    """fn run (as a one-shot) over x repeated `copies` times; its second-to-last period: long after the start-up,
    and with a whole period after it, for processes that look ahead (the resampling filters of drive)."""
    n = len(x)
    y = np.asarray(fn(np.concatenate([x] * copies)))
    return y[(copies - 2) * n:(copies - 1) * n]


def join_steps(x):
    """(the step from the last frame to the first, the largest step between neighbouring frames inside)."""
    x = np.asarray(x, dtype=np.float64)
    x = x[:, None] if x.ndim == 1 else x
    return float(np.max(np.abs(x[0] - x[-1]))), float(np.max(np.abs(np.diff(x, axis=0))))


def noise(n, seed=1):
    return np.random.default_rng(seed).standard_normal(n) * 0.25


def project_with(sound, **top):
    data = {'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]}
    data.update(top)
    return data


def errors(data):
    try:
        project.validate(data)
    except project.ProjectError as error:
        return error.errors
    return []


class CircularFilters(unittest.TestCase):
    def test_a_fixed_filter_gives_the_steady_state(self):
        x = noise(24000)
        sections = [filters.biquad('lowpass', 300.0, 5.0)]
        ref = settled_period(lambda s: filters.apply(s, sections), x, 6)
        self.assertLess(np.max(np.abs(filters.apply_circular(x, sections) - ref)), 1e-9)

    def test_a_moving_filter_gives_the_steady_state(self):
        n = 24000
        x = noise(n, 2)
        t = np.arange(n) / RATE
        cutoff = 1000.0 * 2 ** np.sin(2 * np.pi * t / (n / RATE))
        ref = settled_period(lambda s: filters.svf(s, np.tile(cutoff, 3), 4.0, 'bandpass'), x, 3)
        self.assertLess(np.max(np.abs(filters.svf_circular(x, cutoff, 4.0, 'bandpass') - ref)), 1e-9)

    def test_an_overdamped_filter_settles_more_slowly(self):
        self.assertGreater(filters.settle_samples(100.0, 0.1), filters.settle_samples(100.0, 0.7071))


class CircularEffects(unittest.TestCase):
    def setUp(self):
        self.x = noise(24000, 3)

    def check_steady(self, loop_fn, plain_fn, copies, tol):
        ref = settled_period(lambda s: plain_fn(s)[:len(s)], self.x, copies)
        self.assertLess(np.max(np.abs(loop_fn(self.x) - ref)), tol)

    def test_delay(self):
        self.check_steady(lambda s: fx.delay(s, 0.05, 0.5, 0.5, 3000.0, loop=True),
                          lambda s: fx.delay(s, 0.05, 0.5, 0.5, 3000.0), 6, 1e-7)

    def test_room(self):
        self.check_steady(lambda s: fx.room(s, 1.2, 0.5, 0.3, loop=True), lambda s: fx.room(s, 1.2, 0.5, 0.3), 6, 1e-7)

    def test_eq_and_console(self):
        self.check_steady(lambda s: fx.eq(s, 80.0, 6000.0, {'hz': 1000.0, 'gain_db': 6.0}, loop=True),
                          lambda s: fx.eq(s, 80.0, 6000.0, {'hz': 1000.0, 'gain_db': 6.0}), 4, 1e-9)
        from sfxlib import chip
        self.check_steady(lambda s: chip.console(s, 'nes', loop=True), lambda s: chip.console(s, 'nes'), 4, 1e-9)

    def test_drive(self):
        self.check_steady(lambda s: fx.drive(s, 0.6, loop=True), lambda s: fx.drive(s, 0.6), 3, 1e-9)


class WholeCycles(unittest.TestCase):
    def test_a_period_holds_whole_cycles_and_the_pitch_barely_moves(self):
        n = 10 * RATE
        freq, factor = osc.whole_cycles(np.full(n, 61.3))
        cycles = np.sum(freq) / RATE
        self.assertAlmostEqual(cycles, round(cycles), places=6)
        self.assertLess(abs(freq[0] - 61.3), 1 / (2 * 10))

    def test_tone_beds_join_their_start(self):
        for source, extra in (({'type': 'fm', 'ratio': 1.37, 'index': 2.0}, {}),
                              ({'type': 'osc', 'wave': 'saw'}, {'vibrato': {'rate_hz': 0.7, 'depth_cents': 30.0}})):
            layer = dict({'source': source, 'start': 0.0, 'length': 2.0, 'pitch': 61.3}, **extra)
            x = synth.render_loop({'layers': [layer]}, seed=1, channels=1, seconds=2.0)
            step, inner = join_steps(x)
            self.assertLessEqual(step, inner * 1.01, source['type'])


class Drops(unittest.TestCase):
    def test_a_ring_follows_the_bubble_model(self):
        r = 0.001
        f, d = drops.frequency(r), drops.damping(r)
        self.assertAlmostEqual(f, 3000.0)
        self.assertAlmostEqual(d, 0.13 / r + 0.0072 * r ** -1.5)
        x = drops.ring(r)
        t = np.arange(len(x)) / RATE
        k = int(0.85 * len(x))
        self.assertLess(np.max(np.abs(x[:k] - np.sin(2 * np.pi * f * t[:k]) * np.exp(-d * t[:k]))), 1e-12)

    def test_a_rising_ring_rises(self):
        x = drops.ring(0.004, rise=0.5)
        crossings = np.flatnonzero(np.diff(np.signbit(x).astype(int)) != 0)
        gaps = np.diff(crossings)
        self.assertLess(np.mean(gaps[-20:]), np.mean(gaps[:20]))

    def test_radii_are_fifty_log_spaced_and_bounded(self):
        r = drops.radii(0.2, 5.0)
        self.assertEqual(len(r), 50)
        self.assertAlmostEqual(r[0], 0.0002)
        self.assertAlmostEqual(r[-1], 0.005)
        self.assertTrue(np.allclose(np.diff(np.log(r)), np.log(25) / 49))
        with self.assertRaises(ValueError):
            drops.radii(0.1, 5.0)

    def test_the_direct_and_fft_paths_agree(self):
        for loop in (False, True):
            runs = []
            for factor in (1e12, 0.0):
                with mock.patch.object(drops, 'DIRECT_FACTOR', factor):
                    runs.append(drops.render(RATE, np.random.default_rng(4), 3000.0, 0.2, 20.0, gamma=2.0,
                                             channels=2, spread=0.7, loop=loop))
            self.assertLess(np.max(np.abs(runs[0] - runs[1])), 1e-9, loop)

    def test_a_loop_of_drops_joins_and_comes_at_the_set_rms(self):
        x = drops.render(RATE, np.random.default_rng(5), 2000.0, 0.2, 10.0, gamma=3.0, loop=True)
        step, inner = join_steps(x)
        self.assertLessEqual(step, inner)
        self.assertAlmostEqual(float(np.sqrt(np.mean(x ** 2))), drops.RMS, places=9)
        centred = drops.render(RATE, np.random.default_rng(5), 500.0, channels=2, spread=0.0)
        self.assertTrue(np.array_equal(centred[:, 0], centred[:, 1]))


class Wind(unittest.TestCase):
    N = 20 * RATE

    def rng_for(self, part):
        return np.random.default_rng(dsp.seed_for(3, part))

    def windows(self, x, g, law, w=RATE // 5):
        k = len(x) // w
        p = np.array([np.mean(x[i * w:(i + 1) * w] ** 2) for i in range(k)])
        q = np.array([np.mean(g[i * w:(i + 1) * w] ** law) for i in range(k)])
        return np.polyfit(np.log10(q), np.log10(p), 1)[0]

    def test_gusts_have_the_set_spread_and_repeat(self):
        g = wind.gusts(self.N, self.rng_for('gusts'), 0.1, 0.3)
        self.assertAlmostEqual(float(np.std(g)), 0.1, delta=0.005)
        self.assertAlmostEqual(float(np.mean(g)), 1.0, delta=0.005)
        step, inner = join_steps(g)
        self.assertLessEqual(step, inner)

    def test_the_wake_grows_with_the_eighth_power_of_the_speed(self):
        g = wind.gusts(self.N, self.rng_for('gusts'), 0.3, 0.3)
        wake = wind.render(self.N, self.rng_for, band_hz=150.0, turbulence=0.3, gust_hz=0.3)[:, 0]
        self.assertAlmostEqual(self.windows(wake, g, 8), 1.0, delta=0.06)

    def test_the_tone_grows_with_the_sixth_power_of_the_speed(self):
        g = wind.gusts(self.N, self.rng_for('gusts'), 0.3, 0.3)
        tone = wind.render(self.N, self.rng_for, band_hz=150.0, turbulence=0.3, gust_hz=0.3, whistle_hz=800.0,
                           whistle_db=40.0, whistle_q=60.0)[:, 0]
        self.assertAlmostEqual(self.windows(tone, g, 6), 1.0, delta=0.06)

    def test_the_tone_sits_at_its_frequency_and_the_wake_starts_at_its_band(self):
        n = 4 * RATE
        tone = wind.render(n, self.rng_for, band_hz=400.0, turbulence=0.0, whistle_hz=900.0, whistle_db=40.0)[:, 0]
        spectrum = np.abs(np.fft.rfft(tone)) ** 2
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        self.assertAlmostEqual(freqs[np.argmax(spectrum)], 900.0, delta=900.0 * 0.02)
        wake = wind.render(n, self.rng_for, band_hz=400.0, turbulence=0.0)[:, 0]
        spectrum = np.abs(np.fft.rfft(wake)) ** 2
        # brown noise (power falling as 1/f^2) high-passed at band_hz: its power density peaks at band_hz itself
        centres = 50.0 * 2 ** (np.arange(0, 22) / 3)
        density = [np.mean(spectrum[(freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))]) for c in centres]
        peak = centres[int(np.argmax(density))]
        self.assertLess(abs(math.log2(peak / 400.0)), 0.5, peak)


class RenderLoop(unittest.TestCase):
    def test_a_loop_is_exactly_one_period_and_an_event_wraps_round(self):
        bed = {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': 1.5, 'gain_db': -60.0}
        event = {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 1.4, 'length': 0.3, 'pitch': 440.0}
        x = synth.render_loop({'layers': [bed, event]}, seed=1, channels=1, seconds=1.5)[:, 0]
        self.assertEqual(len(x), curves.samples(1.5))
        start = np.max(np.abs(x[:int(0.15 * RATE)]))
        middle = np.max(np.abs(x[int(0.4 * RATE):int(1.3 * RATE)]))
        self.assertGreater(start, 0.3)
        self.assertLess(middle, start * 10 ** (-40 / 20))

    def test_a_bed_with_a_moving_filter_drive_and_room_joins(self):
        layer = {'source': {'type': 'noise', 'color': 'brown'}, 'start': 0.0, 'length': 1.0, 'drive': 0.5,
                 'filter': {'type': 'lowpass', 'cutoff': [[0, 300.0], [0.5, 3000.0, 'exp'], [1.0, 300.0]], 'q': 2.0}}
        x = synth.render_loop({'layers': [layer], 'chain': {'room': {'size': 1.0, 'decay': 1.5, 'mix': 0.4}}},
                              seed=2, channels=2, seconds=1.0)
        step, inner = join_steps(x)
        self.assertLessEqual(step, inner)

    def test_a_bed_is_not_faded_at_its_join(self):
        for source in ({'type': 'noise', 'color': 'white'}, {'type': 'drops', 'rate': 20000.0},
                       {'type': 'wind', 'band_hz': 300.0}):
            layer = {'source': source, 'start': 0.0, 'length': 1.0}
            seam = check.loop_seam(synth.render_loop({'layers': [layer]}, seed=4, channels=1, seconds=1.0))
            self.assertGreaterEqual(seam['join_level_db'], seam['quietest_inner_level_db'], source['type'])

    def test_what_cannot_loop_is_refused(self):
        chip_bed = {'source': {'type': 'chip', 'wave': 'pulse'}, 'start': 0.0, 'length': 1.0, 'pitch': 440.0}
        with self.assertRaises(ValueError):
            synth.render_loop({'layers': [chip_bed]}, seed=1, channels=1, seconds=1.0)
        bed = {'source': {'type': 'noise'}, 'start': 0.0, 'length': 1.0}
        with self.assertRaises(ValueError):
            synth.render_loop({'layers': [bed], 'chain': {'fade': {'out': 0.1}}}, seed=1, channels=1, seconds=1.0)

    def test_spread_decorrelates_noise_in_stereo(self):
        bed = {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': 2.0, 'spread': 1.0}
        x = synth.render_loop({'layers': [bed]}, seed=3, channels=2, seconds=2.0)
        corr = np.corrcoef(x[:, 0], x[:, 1])[0, 1]
        self.assertLess(abs(corr), 0.05)
        bed['spread'] = 0.0
        x = synth.render_loop({'layers': [bed]}, seed=3, channels=2, seconds=2.0)
        self.assertTrue(np.allclose(x[:, 0], x[:, 1]))


class LoopLoudness(unittest.TestCase):
    @staticmethod
    def tones(levels_db, seconds=20.0):
        """EBU Tech 3342's minimum-requirement signals: a stereo 1 kHz sine, in phase in both channels, at each
        per-channel peak level for `seconds`."""
        n = int(seconds * RATE)
        t = np.arange(n) / RATE
        parts = [10 ** (db / 20) * np.sin(2 * np.pi * 1000.0 * t) for db in levels_db]
        x = np.concatenate(parts)
        return np.stack([x, x], axis=1)

    def test_loudness_range_meets_tech_3342(self):
        for levels, expected in (((-20, -30), 10.0), ((-20, -15), 5.0), ((-40, -20), 20.0),
                                 ((-50, -35, -20, -35, -50), 15.0)):
            self.assertAlmostEqual(loudness.loop_range(self.tones(levels)), expected, delta=1.0, msg=str(levels))

    def test_a_loop_reads_as_its_endless_repetition(self):
        x = np.stack([noise(2 * RATE, 7), noise(2 * RATE, 8)], axis=1)
        self.assertAlmostEqual(loudness.loop_integrated(x), loudness.integrated(np.tile(x, (10, 1))), delta=0.05)

    def test_true_peak_reads_the_join_from_both_sides(self):
        t = np.arange(RATE) / RATE
        x = 0.5 * np.cos(2 * np.pi * 1000.0 * t)      # whole cycles; the file starts at its peak
        self.assertAlmostEqual(loudness.loop_true_peak_db(x), 20 * math.log10(0.5), delta=0.05)
        self.assertGreater(loudness.true_peak_db(x), loudness.loop_true_peak_db(x) + 0.1)


class Checks(unittest.TestCase):
    def fake_render(self, name, frames, use='loop'):
        folder = helpers.scratch(name)
        (folder / 'master').mkdir()
        wav.write(folder / 'master' / 'x_01.wav', frames, bits=24, dither=False,
                  loop=(0, len(frames)) if use == 'loop' else None)
        resolved = {'level': {'mode': 'match', 'ceiling_dbtp': -1.0, 'target_lufs': None, 'limited_by': None},
                    'sounds': [{'id': 'x', 'category': 'ambience', 'use': use, 'vary': {},
                                'variants': [{'n': 1, 'file': 'master/x_01.wav'}]}]}
        (folder / 'resolved.json').write_text(json.dumps(resolved), encoding='utf-8')
        return check.run(folder)

    def test_a_loop_that_jumps_at_its_join_is_flagged(self):
        t = np.arange(RATE) / RATE
        broken = self.fake_render('seam_broken', 0.5 * np.sin(2 * np.pi * 100.25 * t))
        self.assertEqual([f['kind'] for f in broken['flags']], ['seam'])
        whole = self.fake_render('seam_whole', 0.5 * np.sin(2 * np.pi * 100.0 * t))
        self.assertEqual(whole['flags'], [])
        self.assertIsNotNone(whole['files'][0]['loudness_range_lu'])

    def test_a_loop_faded_at_its_ends_is_flagged(self):
        x = 0.5 * noise(RATE, 13)
        m = curves.samples(0.002)
        x[:m] *= np.linspace(0.0, 1.0, m)
        x[-m:] *= np.linspace(1.0, 0.0, m)
        faded = self.fake_render('seam_faded', x)
        self.assertEqual([f['kind'] for f in faded['flags']], ['seam'])
        self.assertEqual(self.fake_render('seam_unfaded', 0.5 * noise(RATE, 13))['flags'], [])

    def test_decorrelated_channels_are_not_called_anti_correlated(self):
        a, b = noise(2 * RATE, 11), noise(2 * RATE, 12)
        b = b - 0.03 * a
        loose = self.fake_render('stereo_loose', 0.5 * np.stack([a, b], axis=1))
        self.assertLess(loose['files'][0]['stereo']['correlation'], 0.0)
        self.assertEqual(loose['flags'], [])
        opposed = self.fake_render('stereo_opposed', 0.5 * np.stack([a, -0.9 * a + 0.1 * b], axis=1))
        self.assertEqual([f['kind'] for f in opposed['flags']], ['stereo'])


class ALoopAgainstTheOneShots(unittest.TestCase):
    """A bed is levelled on its integrated loudness and a hit on its loudest 200 ms, so left untrimmed the bed reads as loud as
    the hits and sits on top of them: the first agent to use the published skill from nothing handed that over unflagged."""

    def flags(self, name, bed_db, hit_db=-20.0, beds=1):
        folder = helpers.scratch(name)
        (folder / 'master').mkdir()
        variants = []
        for k in range(beds):
            x = noise(2 * RATE, 21 + k)
            x = x * 10 ** ((bed_db - loudness.loop_integrated(x)) / 20)
            file = f'master/bed_{k + 1:02d}.wav'
            wav.write(folder / file, x, bits=24, dither=False, loop=(0, len(x)))
            variants.append({'n': k + 1, 'file': file})
        sounds = [{'id': 'bed', 'category': 'ambience', 'use': 'loop', 'vary': {}, 'variants': variants}]
        if hit_db is not None:
            t = np.arange(int(0.3 * RATE)) / RATE
            hit = np.sin(2 * np.pi * 500.0 * t) * np.exp(-t / 0.05)
            hit = hit * 10 ** ((hit_db - loudness.brief(hit)) / 20)
            wav.write(folder / 'master' / 'hit_01.wav', hit, bits=24, dither=False)
            sounds.append({'id': 'hit', 'category': 'impact', 'use': 'one_shot', 'vary': {},
                           'variants': [{'n': 1, 'file': 'master/hit_01.wav'}]})
        resolved = {'level': {'mode': 'match', 'ceiling_dbtp': -1.0, 'target_lufs': None, 'limited_by': None}, 'sounds': sounds}
        (folder / 'resolved.json').write_text(json.dumps(resolved), encoding='utf-8')
        return [f for f in check.run(folder)['flags'] if f['kind'] == 'loop_level']

    def test_a_bed_as_loud_as_the_hits_is_flagged_once(self):
        found = self.flags('loop_level_even', -20.0, beds=2)
        self.assertEqual([f['sound'] for f in found], ['bed'])
        self.assertIn('trim_db', found[0]['message'])

    def test_a_bed_just_under_the_hits_is_flagged(self):
        self.assertEqual(len(self.flags('loop_level_close', -22.0)), 1)

    def test_a_bed_trimmed_under_the_hits_is_not(self):
        self.assertEqual(self.flags('loop_level_under', -24.0), [])

    def test_loops_with_nothing_to_cover_are_not(self):
        self.assertEqual(self.flags('loop_level_alone', -20.0, hit_db=None), [])


class Schema(unittest.TestCase):
    def test_a_loop_recipe_makes_loops_of_twelve_seconds_unless_told(self):
        proj = project.validate(project_with({'id': 'rain', 'category': 'ambience', 'recipe': 'ambience.rain_light'}))
        self.assertEqual(proj['sounds'][0]['use'], 'loop')
        self.assertEqual(proj['sounds'][0]['loop'], {'seconds': 12.0})
        proj = project.validate(project_with({'id': 'rain', 'category': 'ambience', 'recipe': 'ambience.rain_light',
                                              'loop': {'seconds': 30}}))
        self.assertEqual(proj['sounds'][0]['loop'], {'seconds': 30.0})

    def test_loop_rules(self):
        bed = {'source': {'type': 'noise'}, 'start': 0.0, 'length': 4.0}
        cases = {
            'recipe use': {'id': 'r', 'category': 'ambience', 'recipe': 'ambience.breeze', 'use': 'one_shot'},
            'loop on a one-shot': {'id': 'r', 'category': 'ui', 'recipe': 'ui.tick', 'loop': {'seconds': 4}},
            'seconds': {'id': 'r', 'category': 'ambience', 'recipe': 'ambience.breeze', 'loop': {'seconds': 90}},
            'chip bed': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                         'layers': [{'source': {'type': 'chip'}, 'start': 0.0, 'length': 4.0, 'pitch': 440.0}]},
            'env shape': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                          'layers': [dict(bed, env={'attack': 0.1, 'decay': 3.9})]},
            'ends': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                     'layers': [dict(bed, filter={'type': 'lowpass', 'cutoff': [[0, 500.0], [4.0, 900.0]]})]},
            'fade': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'layers': [bed],
                     'chain': {'fade': {'out': 0.5}}},
            'time_pct': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'layers': [bed],
                         'vary': {'time_pct': 5}},
            'event outside': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                              'layers': [bed, dict(bed, start=4.5, length=0.2)]},
            'spread on osc': {'id': 'r', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'length': 0.2,
                                                                     'pitch': 440.0, 'spread': 0.5}]},
            'radii': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                      'layers': [dict(bed, source={'type': 'drops', 'r_min_mm': 5.0, 'r_max_mm': 1.0})]},
            'wind range': {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4},
                           'layers': [dict(bed, source={'type': 'wind', 'turbulence': 0.9})]},
        }
        for name, sound in cases.items():
            self.assertTrue(errors(project_with(sound)), name)
        flow = project_with({'id': 'r', 'category': 'ambience', 'recipe': 'ambience.breeze'},
                            flows=[{'name': 'f', 'sounds': ['r']}])
        self.assertTrue(errors(flow))
        good = {'id': 'r', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'channels': 2,
                'layers': [dict(bed, spread=1.0, env=[[0, 0.5], [2.0, 1.0, 'cos'], [4.0, 0.5]]),
                           {'source': {'type': 'drops', 'rate': 300.0}, 'start': 0.0, 'length': 4.0, 'spread': 0.5},
                           {'source': {'type': 'modal', 'material': 'wood'}, 'start': 3.9, 'length': 0.4,
                            'pitch': 300.0}]}
        self.assertEqual(errors(project_with(good)), [])


class AmbienceRecipes(unittest.TestCase):
    def test_registered_as_loops_with_rules(self):
        for name in AMBIENCE:
            recipe = recipes.get(name)
            self.assertEqual((recipe.category, recipe.use), ('ambience', 'loop'))
            self.assertTrue(recipe.rules, name)

    def test_every_setting_is_valid_and_renders(self):
        for name in AMBIENCE:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(dict(params, seconds=2.0))
                sound = {'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2.0},
                         'layers': spec['layers'], 'channels': 2}
                project.validate(project_with(sound))
                x = synth.render_loop(spec, seed=1, channels=2, seconds=2.0)
                self.assertTrue(np.isfinite(x).all(), (name, params))
                self.assertGreater(float(np.max(np.abs(x))), 0.01, (name, params))


class Example(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = helpers.scratch('ambience_example') / 'render'
        render.render(project.load(EXAMPLE), cls.out)
        cls.check = json.loads((cls.out / 'check.json').read_text(encoding='utf-8'))
        cls.resolved = json.loads((cls.out / 'resolved.json').read_text(encoding='utf-8'))

    def test_renders_with_no_flags(self):
        self.assertEqual(self.check['flags'], [], json.dumps(self.check['flags'], indent=1))
        self.assertEqual(sorted(s['recipe'] for s in self.resolved['sounds']), sorted(AMBIENCE))

    def test_masters_carry_their_loop(self):
        for sound in self.resolved['sounds']:
            self.assertEqual(sound['level_measure'], 'integrated')
            for v in sound['variants']:
                meta = wav.info(self.out / v['file'])
                self.assertEqual(meta['channels'], 2)
                self.assertEqual(meta['loops'], [(0, meta['frames'])], v['file'])


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class AuditionLoop(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dataText = html.split('<script type="application/json" id="data">')[1].split('</script>')[0];
const script = html.split('<script>')[1].split('</script>')[0];
const ids = {};
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.textContent = ''; this.value = '';
    this.listeners = {}; this.clientWidth = 300; this.clientHeight = 48;
    const set = new Set();
    this.classList = { add: c => set.add(c), remove: c => set.delete(c), toggle: () => {}, has: c => set.has(c) };
  }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') ids[v] = this; if (k === 'value') this.value = v; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  getContext() { return { clearRect() {}, fillRect() {} }; }
  select() {}
}
for (const m of html.split('<script')[0].matchAll(/\sid="([^"]+)"/g)) {      // the elements the page's markup declares
  const id = m[1];
  const e = new El('div'); e.value = id === 'master' ? '-6' : (id === 'interval' ? '150' : ''); ids[id] = e;
}
ids['data'] = { textContent: dataText };
const started = [];
class Ctx {
  constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
  resume() { return Promise.resolve(); }
  createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
  createBiquadFilter() { return { type: '', frequency: { value: 0 }, connect() {} }; }
  createBufferSource() {
    const src = { loop: false, buffer: null, connect() {}, stop() { this.stopped = true; },
                  start(when, offset) { started.push({ loop: src.loop, when: when, offset: offset || 0 }); } };
    return src;
  }
  decodeAudioData() { return Promise.resolve({ duration: 5.0, getChannelData: () => new Float32Array(16) }); }
}
global.document = { getElementById: id => ids[id] || null, createElement: t => new El(t), documentElement: {},
                    execCommand: () => true, querySelectorAll: () => [] };
global.window = { localStorage: { getItem: () => null, setItem: () => {} }, devicePixelRatio: 1, AudioContext: Ctx, addEventListener() {} };
global.getComputedStyle = () => ({ getPropertyValue: () => '#000' });
global.atob = s => Buffer.from(s, 'base64').toString('binary');
Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true, writable: true });   // not Node's own
(0, eval)(script);
function find(node, pred) {
  if (pred(node)) return node;
  for (const c of node.children || []) { const f = find(c, pred); if (f) return f; }
  return null;
}
(async () => {
  const card = ids['cards'].children[0];
  const labels = [];
  find(card, n => { if (n.tag === 'button') labels.push(n.textContent); return false; });
  const first = find(card, n => n.tag === 'button' && n.textContent === '1');
  for (const f of first.listeners['click']) await f();
  const join = find(card, n => n.tag === 'button' && n.textContent === 'hear the join');
  for (const f of join.listeners['click']) await f();
  console.log(JSON.stringify({ labels: labels, started: started }));
})();
"""

    def test_a_loop_plays_looping_and_the_join_starts_near_the_end(self):
        base = helpers.scratch('audition_loop')
        sound = {'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2},
                 'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': 2.0}]}
        out = base / 'render'
        render.render(project.validate(project_with(sound, variants=2)), out)
        harness = base / 'harness.js'
        harness.write_text(self.HARNESS, encoding='utf-8')
        result = subprocess.run(['node', str(harness), str(out / 'audition.html')], capture_output=True, text=True,
                                timeout=60, env=helpers.child_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn('hear the join', data['labels'])
        self.assertIn('stop', data['labels'])
        self.assertNotIn('random', data['labels'])
        self.assertEqual(data['started'][0], {'loop': True, 'when': 0, 'offset': 0})
        self.assertEqual(data['started'][1]['loop'], True)
        self.assertAlmostEqual(data['started'][1]['offset'], 3.0)   # the stand-in buffer lasts 5 s: 2 s before its end


class ShortGusts(unittest.TestCase):
    def test_a_loop_shorter_than_its_gusts_still_moves(self):
        from sfxlib import wind
        speed = wind.gusts(48000, np.random.default_rng(1), 0.2, 0.03)         # one second, a gust every half minute
        self.assertAlmostEqual(float(np.std(speed)), 0.2, delta=0.03)


if __name__ == '__main__':
    unittest.main()
