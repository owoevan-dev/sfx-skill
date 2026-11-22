"""Faults that reach the delivered sound and that someone working without ears learns only from the check: a layer that
runs out of its recording, a noise stretch read from the wrong recording, variants that land at different times, what
holds a set's level down; and the settings that followed a sound's category when its length mattered more."""
import json
import math
import subprocess
import sys
import types
import unittest
from unittest import mock

import numpy as np

import helpers
import test_samples as ts
import sfxkit
from sfxlib import artifacts, denoise, drops, export, loudness, measure, project, render, samples, synth, traits, variants

RATE = 48000
SFX = helpers.SCRIPTS / 'sfx.py'


def noise_recording(seconds, amp=0.3, seed=1):
    return amp * np.clip(np.random.default_rng(seed).standard_normal((int(seconds * RATE), 1)), -2.5, 2.5) / 2.5


def pack(folder, **recordings):
    members = {f'{name}.wav': ts.wav_bytes(x, rate=RATE) for name, x in recordings.items()}
    members['License.txt'] = ts.CC0_TEXT.encode('utf-8')
    samples.add(ts.as_folder(folder / 'Pack', members), folder / 'library', licence='CC0-1.0')
    return folder / 'library'


def checked(sounds, **extra):
    return project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': sounds, **extra})


def errors(sound):
    try:
        checked([sound])
    except project.ProjectError as error:
        return error.errors
    return []


def rendered(folder, sounds, library=None, name='r', **extra):
    render.render(checked(sounds, **extra), folder / name, samples=library, page=False)
    return (json.loads((folder / name / 'check.json').read_text(encoding='utf-8')),
            json.loads((folder / name / 'resolved.json').read_text(encoding='utf-8')))


def kinds(report, kind):
    return [f for f in report['flags'] if f['kind'] == kind]


def rec(file='a', length=1.0, env=None, start=0.0, **source):
    layer = {'source': {'type': 'sample', 'library': 'pack', 'file': file, **source}, 'start': start, 'length': length}
    if env is not None:
        layer['env'] = env
    return layer


class ARecordingThatRunsOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = helpers.scratch('unreported_runs_out')
        cls.library = pack(cls.folder, a=noise_recording(2.0), b=noise_recording(0.5, seed=2))

    def flags(self, name, layer, **sound):
        report, _ = rendered(self.folder, [{'id': 'x', 'category': 'foley', 'layers': [layer], **sound}], self.library, name)
        return kinds(report, 'short_recording')

    def test_a_layer_longer_than_its_recording_is_flagged(self):
        found = self.flags('longer', rec(length=3.5))
        self.assertEqual(len(found), 1)
        self.assertIn('pack/a', found[0]['message'])
        self.assertIn('3.50', found[0]['message'])
        self.assertIn('2.00', found[0]['message'])

    def test_a_layer_inside_its_recording_is_not(self):
        self.assertEqual(self.flags('inside', rec(length=1.9)), [])

    def test_the_offset_counts(self):
        self.assertEqual(len(self.flags('late', rec(length=1.0, offset=1.5))), 1)
        self.assertEqual(self.flags('early', rec(length=1.0, offset=0.5)), [])

    def test_a_lower_pitch_and_a_stretch_make_more_of_it(self):
        self.assertEqual(self.flags('down', rec(length=3.5, cents=-1200)), [])
        self.assertEqual(len(self.flags('further', rec(length=5.0, cents=-1200))), 1)
        self.assertEqual(self.flags('stretched', rec(length=3.5, time_stretch={'factor': 2.0})), [])

    def test_an_envelope_already_closed_there_leaves_no_hole(self):
        self.assertEqual(self.flags('closed', rec(length=3.5, env=[[0, 0], [0.01, 1], [1.9, 0], [3.5, 0]])), [])

    def test_a_recording_that_has_died_away_leaves_no_hole(self):
        folder = helpers.scratch('unreported_died_away')
        x = noise_recording(1.0)
        x[:, 0] *= np.exp(-np.arange(len(x)) / (0.08 * RATE))
        library = pack(folder, ring=x)
        report, _ = rendered(folder, [{'id': 'x', 'category': 'foley', 'layers': [rec(file='ring', length=1.5)]}], library)
        self.assertEqual(kinds(report, 'short_recording'), [])

    def test_a_steady_recording_with_one_tall_spike_is_still_seen_to_stop(self):
        folder = helpers.scratch('unreported_spike_then_stop')
        x = noise_recording(1.0, amp=0.01)
        x[RATE // 2] = 0.9                                  # the peak is far above the level it stops at
        library = pack(folder, rain=x)
        report, _ = rendered(folder, [{'id': 'x', 'category': 'foley', 'layers': [rec(file='rain', length=1.5)]}], library)
        self.assertEqual(len(kinds(report, 'short_recording')), 1)

    def test_an_event_of_a_loop_is_held_to_it_too(self):
        report, _ = rendered(self.folder, [{'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 6},
                                            'layers': [rec(start=1.0, length=4.0)]}], self.library, 'loop_event')
        self.assertEqual(len(kinds(report, 'short_recording')), 1)

    def test_each_variant_is_read_with_its_own_take(self):
        found = self.flags('takes', rec(file=['a', 'b'], length=1.0), variants=2)
        self.assertEqual(len(found), 1)
        self.assertIn('x_02', found[0]['file'])
        self.assertIn('pack/b', found[0]['message'])


class ANoiseStretchBelongsToOneRecording(unittest.TestCase):
    NOISE = {'noise': [0.0, 0.4]}

    @staticmethod
    def sound(file, **source):
        return {'id': 'x', 'category': 'foley', 'variants': 2, 'layers': [rec(file=file, length=0.3, **source)]}

    def test_one_stretch_cannot_serve_two_recordings(self):
        found = errors(self.sound(['a', {'file': 'b', 'offset': 1.0}], denoise=self.NOISE))
        self.assertEqual(len(found), 1)
        self.assertIn('denoise', found[0])
        self.assertIn('each take', found[0])

    def test_takes_of_one_recording_may_share_one(self):
        self.assertEqual(errors(self.sound([{'file': 'a', 'offset': 0.5}, {'file': 'a', 'offset': 1.5}],
                                           denoise=self.NOISE)), [])

    def test_a_take_can_carry_its_own(self):
        sound = self.sound([{'file': 'a', 'denoise': {'noise': [0.0, 0.4]}}, {'file': 'b', 'denoise': {'noise': [1.0, 1.4]}}])
        self.assertEqual(errors(sound), [])
        valid = checked([sound])['sounds'][0]
        sources = [variants.layer_spec(valid, 1, v)[0]['layers'][0]['source'] for v in (0, 1)]
        self.assertEqual([s['denoise']['noise'] for s in sources], [[0.0, 0.4], [1.0, 1.4]])

    def test_a_takes_own_stretch_is_checked(self):
        found = errors(self.sound([{'file': 'a', 'denoise': {'noise': [0.0, 0.01]}}, 'b']))
        self.assertTrue(any('denoise' in e for e in found), found)

    def test_the_take_is_cleaned_with_it(self):
        folder = helpers.scratch('unreported_take_denoise')
        x = noise_recording(2.0, amp=0.05)
        x[RATE:] += 0.4 * np.sin(2 * np.pi * 440 * np.arange(RATE) / RATE)[:, None]
        library = pack(folder, a=x)
        sound = self.sound([{'file': 'a', 'denoise': self.NOISE}, {'file': 'a'}])
        _, resolved = rendered(folder, [sound], library)
        clean, plain = (v['layers'][0]['peak_db'] for v in resolved['sounds'][0]['variants'])
        self.assertLess(clean, plain - 6.0)


class VariantsLandTogether(unittest.TestCase):
    @staticmethod
    def struck(seconds, strikes, floor=0.012):
        x = noise_recording(seconds, amp=floor, seed=5)
        for at in strikes:
            i, n = int(at * RATE), 4800
            x[i:i + n, 0] += 0.8 * np.exp(-np.arange(n) / 600) * np.sign(np.sin(np.arange(n) * 0.31))
        return np.clip(x, -0.95, 0.95)

    def test_the_landing_is_not_moved_by_a_noise_floor(self):
        x = self.struck(0.3, [0.05])
        self.assertLess(measure.onset_ms(x), 5.0)
        self.assertAlmostEqual(measure.landing(x)['strike_ms'], 50.0, delta=2.0)

    def run_takes(self, name, takes):
        folder = helpers.scratch(f'unreported_timing_{name}')
        library = pack(folder, a=self.struck(3.0, [0.5, 1.5, 2.5]))
        report, _ = rendered(folder, [{'id': 'step', 'category': 'foley', 'variants': len(takes),
                                       'layers': [rec(file=takes, length=0.3)]}], library)
        return kinds(report, 'variant_timing')

    def test_takes_landing_apart_are_flagged(self):
        found = self.run_takes('apart', [{'file': 'a', 'offset': 0.495}, {'file': 'a', 'offset': 1.44}])
        self.assertEqual(len(found), 1)
        self.assertIn('offset', found[0]['message'])

    def test_takes_landing_together_are_not(self):
        self.assertEqual(self.run_takes('together', [{'file': 'a', 'offset': 0.495}, {'file': 'a', 'offset': 1.495}]), [])

    def test_a_swell_that_moves_is_not(self):
        folder = helpers.scratch('unreported_timing_swell')
        sound = {'id': 'rise', 'category': 'scifi', 'variants': 4, 'vary': {'time_pct': 40}, 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'pitch': 220, 'length': 1.6, 'env': [[0, 0], [1.2, 1], [1.6, 0]]}]}
        report, _ = rendered(folder, [sound])
        self.assertEqual(kinds(report, 'variant_timing'), [])


class WhatHoldsTheSetDown(unittest.TestCase):
    SPIKE = {'id': 'spike', 'category': 'impact',
             'layers': [{'source': {'type': 'noise', 'color': 'white'}, 'length': 0.008}]}
    PAD = {'id': 'pad', 'category': 'scifi', 'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'pitch': 220, 'length': 1.0}]}

    def holds(self, name, limit):
        folder = helpers.scratch(f'unreported_holding_{name}')
        level = {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': limit}
        _, first = rendered(folder, [self.SPIKE, self.PAD], level=level)
        said = first['level']['next']
        self.assertEqual(said['sound'], 'spike')
        self.assertEqual(said['then'], 'pad_01')
        self.assertGreater(said['rise_db'], 1.0)
        _, second = rendered(folder, [dict(self.SPIKE, trim_db=-said['rise_db']), self.PAD], name='r2', level=level)
        self.assertAlmostEqual(second['level']['target_lufs'] - first['level']['target_lufs'], said['rise_db'], delta=0.25)

    def test_it_says_what_a_trim_would_give(self):
        self.holds('plain', 0.0)

    def test_with_the_limiter_too(self):
        self.holds('limited', 6.0)

    def test_a_set_of_one_sound_has_nothing_to_say(self):
        folder = helpers.scratch('unreported_holding_one')
        _, resolved = rendered(folder, [self.PAD])
        self.assertIsNone(resolved['level'].get('next'))

    def test_the_command_says_it(self):
        folder = helpers.scratch('unreported_holding_cli')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [self.SPIKE, self.PAD]}),
                        encoding='utf-8')
        out = subprocess.run([sys.executable, str(SFX), 'render', str(path), '--output', str(folder / 'r'), '--no-page'],
                             capture_output=True, text=True, encoding='utf-8', errors='replace', env=helpers.child_env())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('trim_db', out.stdout)
        self.assertIn('spike', out.stdout)


class WhatEachFileReports(unittest.TestCase):
    def test_a_one_shot_has_both_loudness_measures(self):
        folder = helpers.scratch('unreported_both_measures')
        report, _ = rendered(folder, [{'id': 'pad', 'category': 'scifi',
                                       'layers': [{'source': {'type': 'osc'}, 'pitch': 220, 'length': 1.0}]}])
        self.assertIsNotNone(report['files'][0]['integrated_lufs'])
        self.assertIsNotNone(report['files'][0]['max_momentary_lufs'])

    def test_a_loop_has_no_attack_or_decay(self):
        folder = helpers.scratch('unreported_loop_traits')
        report, _ = rendered(folder, [{'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2},
                                       'layers': [{'source': {'type': 'noise'}, 'length': 2.0}]}])
        traits = report['sounds'][0]['traits']
        for name in ('attack_ms', 'decay20_ms', 'peak_s', 'active_s'):
            self.assertNotIn(name, traits)
        self.assertIn('centroid_hz', traits)


class EventsOfALoopScatter(unittest.TestCase):
    SOUND = {'id': 'flicker', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'variants': 3,
             'vary': {'scatter_s': 0.5}, 'layers': [
                 {'source': {'type': 'noise'}, 'length': 4.0, 'gain_db': -18.0},
                 {'source': {'type': 'noise', 'color': 'white'}, 'start': 0.1, 'length': 0.05, 'env': {'attack': 0.001, 'decay': 0.049}},
                 {'source': {'type': 'noise', 'color': 'white'}, 'start': 2.0, 'length': 0.05, 'env': {'attack': 0.001, 'decay': 0.049}}]}

    def starts(self, variant):
        valid = checked([self.SOUND])['sounds'][0]
        return [layer.get('start', 0.0) for layer in variants.layer_spec(valid, 1, variant)[0]['layers']]

    def test_events_move_and_the_bed_stays(self):
        self.assertEqual(self.starts(0), [0.0, 0.1, 2.0])
        moved = self.starts(1)
        self.assertEqual(moved[0], 0.0)
        self.assertNotAlmostEqual(moved[2], 2.0, places=3)
        self.assertLessEqual(abs(moved[2] - 2.0), 0.5)
        self.assertNotEqual(self.starts(2)[2], moved[2])

    def test_an_event_moved_before_the_start_comes_round_from_the_end(self):
        for variant in range(1, 3):
            for start in self.starts(variant):
                self.assertTrue(0.0 <= start < 4.0, start)

    def test_it_belongs_to_loops(self):
        found = errors({'id': 'x', 'category': 'foley', 'vary': {'scatter_s': 0.2},
                        'layers': [{'source': {'type': 'noise'}, 'length': 0.2}]})
        self.assertTrue(any('scatter_s' in e for e in found), found)

    def test_the_loop_still_joins(self):
        report, _ = rendered(helpers.scratch('unreported_scatter_render'), [self.SOUND])
        self.assertEqual(kinds(report, 'seam'), [])


class UnityAdviceByLength(unittest.TestCase):
    def test_a_short_hit_takes_adpcm_and_a_long_creak_does_not(self):
        self.assertEqual(export._unity({'category': 'foley', 'use': 'one_shot'}, 0.4, 4)['compression_format'], 'ADPCM')
        self.assertNotEqual(export._unity({'category': 'foley', 'use': 'one_shot'}, 4.0, 3)['compression_format'], 'ADPCM')

    def test_a_short_loop_stays_in_memory_and_a_long_one_streams(self):
        self.assertEqual(export._unity({'category': 'ambience', 'use': 'loop'}, 6.0, 1)['load_type'], 'Compressed In Memory')
        self.assertEqual(export._unity({'category': 'ambience', 'use': 'loop'}, 48.0, 1)['load_type'], 'Streaming')


class TheChainIsShown(unittest.TestCase):
    def test_the_format_page_shows_one_that_validates(self):
        text = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'project-format.md').read_text(encoding='utf-8')
        block = text[text.index('## The chain'):].split('```json')[1].split('```')[0]
        chain = json.loads(block)['chain']
        self.assertEqual(errors({'id': 'x', 'category': 'impact', 'chain': chain,
                                 'layers': [{'source': {'type': 'noise'}, 'length': 0.2}]}), [])

    def test_a_chain_written_as_a_list_is_told_the_shape(self):
        found = errors({'id': 'x', 'category': 'impact', 'chain': [{'room': {'size': 1}}],
                        'layers': [{'source': {'type': 'noise'}, 'length': 0.2}]})
        self.assertEqual(len(found), 1)
        self.assertIn('"room"', found[0])


def tone(hz, seconds, amp=0.5):
    return (amp * np.sin(2 * np.pi * hz * np.arange(int(seconds * RATE)) / RATE))[:, None]


def cli(*args):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=helpers.child_env(), timeout=300)


class ACentredLayer(unittest.TestCase):
    def test_its_channels_are_the_same_on_any_maths_library(self):
        # on some systems the sine and cosine of 45 degrees differ in their last bit
        fake = types.SimpleNamespace(pi=math.pi, sqrt=math.sqrt, cos=math.cos, sin=lambda t: math.sin(t) * (1 + 2.3e-16))
        x = np.random.default_rng(3).standard_normal(4800)
        with mock.patch.object(synth, 'math', fake):
            out = synth.place(x, 0.0, 2)
        self.assertTrue(np.array_equal(out[:, 0], out[:, 1]))

    def test_so_are_a_population_of_bubbles_with_no_spread(self):
        real = np.sin
        with mock.patch.object(drops.np, 'sin', lambda t: real(t) * (1 + 2.3e-16)):
            x = drops.render(RATE // 2, np.random.default_rng(5), 500.0, channels=2, spread=0.0)
        self.assertTrue(np.array_equal(x[:, 0], x[:, 1]))

    def test_it_keeps_the_constant_power_level(self):
        out = synth.place(np.ones(10), 0.0, 2)
        self.assertAlmostEqual(float(out[0, 0]), math.sqrt(0.5), places=12)


class ShortSoundsAreNotPushedUp(unittest.TestCase):
    """Hearing adds up a short sound's energy over about 200 ms, so a 400 ms window reads it too low and the level step
    then pushes it above the sounds it should sit under."""
    CLICK = {'id': 'click', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'pitch': 1000, 'length': 0.06}]}
    PAD = {'id': 'pad', 'category': 'scifi', 'layers': [{'source': {'type': 'osc'}, 'pitch': 1000, 'length': 1.0}]}

    def test_a_sound_of_100_ms_reads_3_db_under_the_same_sound_held(self):
        self.assertAlmostEqual(loudness.brief(tone(997, 1.0)) - loudness.brief(tone(997, 0.1)), 3.01, delta=0.15)

    def test_a_sound_held_past_200_ms_reads_no_louder(self):
        self.assertAlmostEqual(loudness.brief(tone(997, 0.25)), loudness.brief(tone(997, 2.0)), delta=0.1)

    def test_it_reads_silence_as_silence(self):
        self.assertEqual(loudness.brief(np.zeros((RATE, 1))), -np.inf)

    def test_the_level_step_matches_one_shots_by_it(self):
        folder = helpers.scratch('unreported_brief_match')
        report, resolved = rendered(folder, [self.CLICK, self.PAD], level={'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 0.0})
        values = [f['brief_lufs'] for f in report['files']]
        self.assertAlmostEqual(values[0], values[1], delta=0.1)
        self.assertEqual({s['level_measure'] for s in resolved['sounds']}, {'brief'})

    def test_a_click_no_longer_comes_out_above_a_held_sound(self):
        folder = helpers.scratch('unreported_brief_balance')
        report, _ = rendered(folder, [self.CLICK, self.PAD], level={'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 0.0})
        click, pad = report['files']
        # equal in energy over 200 ms: a 60 ms tone stands 10 log10(200 / 60) = 5.2 dB over the held tone, not 8.2
        self.assertAlmostEqual(click['sample_peak_db'] - pad['sample_peak_db'], 5.2, delta=0.4)


class ASetCanBeHeldAtALevel(unittest.TestCase):
    A = {'id': 'a', 'category': 'scifi', 'layers': [{'source': {'type': 'osc'}, 'pitch': 500, 'length': 0.6}]}
    B = {'id': 'b', 'category': 'scifi', 'layers': [{'source': {'type': 'noise'}, 'length': 0.6}]}
    SPIKE = {'id': 'spike', 'category': 'impact', 'layers': [{'source': {'type': 'noise', 'color': 'white'}, 'length': 0.004}]}

    @staticmethod
    def level(target, **extra):
        return {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 6.0, 'target_lufs': target, **extra}

    def test_the_set_sits_where_it_is_asked(self):
        report, resolved = rendered(helpers.scratch('unreported_pin'), [self.A, dict(self.B, trim_db=-3)], level=self.level(-26.0))
        self.assertAlmostEqual(resolved['level']['target_lufs'], -26.0, places=3)
        self.assertAlmostEqual(report['files'][0]['brief_lufs'], -26.0, delta=0.05)
        self.assertAlmostEqual(report['files'][1]['brief_lufs'], -29.0, delta=0.05)

    def test_a_trim_then_moves_only_its_own_sound(self):
        folder = helpers.scratch('unreported_pin_trim')
        first, _ = rendered(folder, [self.A, self.B], level=self.level(-26.0))
        second, _ = rendered(folder, [dict(self.A, trim_db=-2), self.B], name='r2', level=self.level(-26.0))
        self.assertAlmostEqual(second['files'][1]['brief_lufs'], first['files'][1]['brief_lufs'], delta=0.02)
        self.assertAlmostEqual(first['files'][0]['brief_lufs'] - second['files'][0]['brief_lufs'], 2.0, delta=0.05)

    def test_a_level_the_peaks_cannot_reach_gives_way_and_says_why(self):
        _, resolved = rendered(helpers.scratch('unreported_pin_high'), [self.A, self.SPIKE], level=self.level(-2.0))
        self.assertLess(resolved['level']['target_lufs'], -2.0)
        self.assertEqual(resolved['level']['limited_by'], 'spike_01')

    def test_it_is_checked(self):
        for bad in ({'target_lufs': -90.0}, {'mode': 'peak', 'target_lufs': -20.0}):
            with self.assertRaises(project.ProjectError) as caught:
                checked([self.A], level=bad)
            self.assertTrue(any('target_lufs' in e for e in caught.exception.errors))

    def test_the_command_says_it_is_held(self):
        folder = helpers.scratch('unreported_pin_cli')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [self.A, self.B],
                                    'level': {'target_lufs': -26}}), encoding='utf-8')
        out = cli('render', path, '--output', folder / 'r', '--no-page')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('target_lufs', out.stdout)


class ReadingASetAtAGlance(unittest.TestCase):
    def test_a_small_speaker_loses_a_low_sound_and_keeps_a_high_one(self):
        self.assertLess(traits.measures(tone(60, 0.5))['small_speaker_db'], -30.0)
        self.assertGreater(traits.measures(tone(2000, 0.5))['small_speaker_db'], -0.5)

    def test_check_prints_a_line_of_traits_per_sound(self):
        folder = helpers.scratch('unreported_traits_table')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [
            {'id': 'hit', 'category': 'impact', 'recipe': 'impact.punch'},
            {'id': 'rain', 'category': 'ambience', 'recipe': 'ambience.rain_light', 'loop': {'seconds': 2}, 'variants': 1}]}),
                        encoding='utf-8')
        self.assertEqual(cli('render', path, '--output', folder / 'r', '--no-page').returncode, 0)
        out = cli('check', folder / 'r', '--traits').stdout
        lines = [line for line in out.splitlines() if line.startswith(('hit', 'rain'))]
        self.assertEqual(len(lines), 2, out)
        self.assertIn('small', out)
        self.assertNotIn('--traits', cli('check', folder / 'r').stdout.split('flags')[0])

    def test_a_recipes_layers_can_be_read_without_a_project(self):
        out = cli('recipes', '--name', 'impact.punch', '--layers')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        built = json.loads(out.stdout)
        self.assertTrue(built['layers'] and all('source' in layer for layer in built['layers']))
        self.assertIn('chain', built)

    def test_a_loop_recipes_layers_too(self):
        built = json.loads(cli('recipes', '--name', 'ambience.rain_light', '--layers').stdout)
        self.assertTrue(built['layers'])

    def test_layers_need_a_recipe_named(self):
        out = cli('recipes', '--layers')
        self.assertEqual(out.returncode, 1)
        self.assertIn('--name', out.stdout)


class TheAuditionPageSaysIt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = helpers.scratch('unreported_page')
        project_ = checked([{'id': 'hit', 'category': 'impact', 'trim_db': -0.2,
                             'layers': [{'source': {'type': 'noise'}, 'length': 0.1}]},
                            {'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 1},
                             'layers': [{'source': {'type': 'noise'}, 'length': 1.0}]}])
        render.render(project_, folder / 'r')
        cls.page = (folder / 'r' / 'audition.html').read_text(encoding='utf-8')

    def test_a_small_trim_is_not_shown_as_zero(self):
        self.assertNotIn('fmt(s.trim_db, 0)', self.page)
        self.assertIn('fmt(s.trim_db, 1)', self.page)

    def test_it_says_a_loop_can_run_under_the_other_sounds(self):
        self.assertIn('keeps playing while you try other sounds', self.page)

    def test_a_press_does_not_wait_on_a_context_that_may_never_resume(self):
        self.assertNotIn('await ctx.resume()', self.page)

    def test_it_names_the_measure_it_shows(self):
        self.assertIn('LUFS (loudest 200 ms)', self.page)
        self.assertNotIn('LUFS max momentary', self.page)


class ReadingARecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = helpers.scratch('unreported_reading')
        x = VariantsLandTogether.struck(3.0, [0.5, 0.62, 1.5, 1.62, 2.4])     # a heel and a toe, twice, and one more
        x[int(1.4 * RATE):int(2.0 * RATE)] *= 0.25                             # the second step is quieter
        cls.library = pack(cls.folder, walk=x)

    def takes(self, *extra):
        out = cli('samples', '--library', self.library, '--events', 'pack/walk', *extra).stdout
        return json.loads(out[out.index('[{'):].splitlines()[0])

    def test_a_heel_and_a_toe_can_count_as_one_step(self):
        self.assertEqual(len(self.takes()), 5)
        steps = self.takes('--min-gap', '0.3')
        self.assertEqual(len(steps), 3)
        self.assertAlmostEqual(steps[1]['offset'], 1.498, delta=0.01)

    def test_takes_can_come_evened_out(self):
        plain, even = self.takes('--min-gap', '0.3'), self.takes('--min-gap', '0.3', '--even')
        self.assertNotIn('gain_db', plain[1])
        self.assertAlmostEqual(even[1]['gain_db'], 12.0, delta=1.5)
        self.assertNotIn('gain_db', even[0])

    def test_a_take_with_its_gain_is_a_valid_layer(self):
        self.assertEqual(errors({'id': 'x', 'category': 'foley', 'variants': 3, 'layers': [
            {'source': {'type': 'sample', 'library': 'pack', 'file': self.takes('--even', '--min-gap', '0.3')}, 'length': 0.2}]}), [])

    def test_a_recording_can_be_drawn_before_building_on_it(self):
        out = self.folder / 'pictures'
        proc = cli('samples', '--library', self.library, '--spectrogram', 'pack/walk', '--output', out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((out / 'pack_walk.spectrogram.png').is_file())
        self.assertIn('pack_walk.spectrogram.png', proc.stdout)

    def test_a_search_says_which_tag_brought_a_recording_in(self):
        entry = {'file': '1', 'library': 'p', 'title': 'rain in a garage', 'labels': ['Rain'], 'tags': ['Garden', 'grass', 'wet']}
        self.assertEqual(samples.matched_tags(entry, 'grass'), ['grass'])
        self.assertEqual(samples.matched_tags(entry, 'rain'), [])


class WhereAClickComesFrom(unittest.TestCase):
    def test_the_flag_names_the_layer_that_peaks_there(self):
        folder = helpers.scratch('unreported_click_layer')
        report, _ = rendered(folder, [{'id': 'x', 'category': 'impact', 'layers': [
            {'source': {'type': 'osc'}, 'pitch': 200, 'length': 1.0, 'env': [[0, 0], [0.01, 1], [0.9, 1], [1.0, 0]]},
            {'source': {'type': 'noise', 'color': 'white'}, 'start': 0.5, 'length': 0.001, 'gain_db': 6, 'env': [[0, 1], [0.001, 1]]}]}],
                             level={'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 0.0})
        found = kinds(report, 'click')
        self.assertEqual(len(found), 1)
        self.assertIn('layer 2 (noise)', found[0]['message'])


class AnEventUnderTheBed(unittest.TestCase):
    @staticmethod
    def sound(event_gain):
        return {'id': 'wind', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'variants': 1, 'layers': [
            {'source': {'type': 'noise', 'color': 'pink'}, 'length': 4.0},
            {'source': {'type': 'osc'}, 'pitch': 1000, 'start': 1.0, 'length': 0.3, 'gain_db': event_gain,
             'env': {'attack': 0.005, 'decay': 0.295}}]}

    def test_one_it_drowns_is_flagged(self):
        report, resolved = rendered(helpers.scratch('unreported_buried'), [self.sound(-40.0)])
        self.assertLess(resolved['sounds'][0]['variants'][0]['layers'][1]['over_rest_db'], 0.0)
        found = kinds(report, 'buried_event')
        self.assertEqual(len(found), 1)
        self.assertIn('layer 2', found[0]['message'])

    def test_one_that_rises_over_it_is_not(self):
        report, resolved = rendered(helpers.scratch('unreported_not_buried'), [self.sound(-3.0)])
        self.assertGreater(resolved['sounds'][0]['variants'][0]['layers'][1]['over_rest_db'], 6.0)
        self.assertEqual(kinds(report, 'buried_event'), [])

    def test_a_long_take_of_a_crossfade_is_not_an_event(self):
        s = self.sound(-40.0)
        s['layers'][1]['length'] = 3.5
        s['layers'][1]['env'] = {'attack': 0.5, 'decay': 3.0}
        _, resolved = rendered(helpers.scratch('unreported_long_take'), [s])
        self.assertNotIn('over_rest_db', resolved['sounds'][0]['variants'][0]['layers'][1])


class WhatARenderLeavesBehind(unittest.TestCase):
    def test_a_godot_project_skips_a_render_folder(self):
        folder = helpers.scratch('unreported_gdignore')
        rendered(folder, [{'id': 'x', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'pitch': 880, 'length': 0.1}]}])
        self.assertTrue((folder / 'r' / '.gdignore').is_file())

    def test_the_level_advice_says_it_is_optional(self):
        folder = helpers.scratch('unreported_holding_optional')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            WhatHoldsTheSetDown.SPIKE, WhatHoldsTheSetDown.PAD]}), encoding='utf-8')
        out = cli('render', path, '--output', folder / 'r', '--no-page').stdout
        self.assertIn('stand out', out)
        self.assertIn('level.target_lufs', out)


class ABedIsABed(unittest.TestCase):
    def test_the_usual_builders_make_a_steady_one(self):
        s = sfxkit.sound('hum', 'ambience', 'a hum', [sfxkit.tone(0.0, 4.0, 120.0), sfxkit.noise(0.0, 4.0, 'brown'),
                                                      sfxkit.tone(1.0, 0.5, 900.0)], use='loop', loop={'seconds': 4})
        self.assertNotIn('env', s['layers'][0])
        self.assertNotIn('env', s['layers'][1])
        self.assertIn('env', s['layers'][2])            # an event keeps its decay
        report, _ = rendered(helpers.scratch('unreported_steady_bed'), [dict(s, variants=1)])
        self.assertLess(abs(report['files'][0]['halves_db']), 3.0)

    def test_a_loop_much_louder_in_one_half_is_flagged(self):
        report, _ = rendered(helpers.scratch('unreported_uneven_loop'), [
            {'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'variants': 1, 'layers': [
                {'source': {'type': 'noise'}, 'length': 4.0, 'env': [[0, 0], [0.1, 1, 'db'], [4, 0]]}]}])
        self.assertEqual(len(kinds(report, 'loop_halves')), 1)

    def test_a_recording_as_a_bed_is_told_what_to_do(self):
        found = errors({'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'layers': [rec(length=2.0)]})
        self.assertEqual(len(found), 1)
        self.assertIn('a recording', found[0])
        self.assertNotIn('instrument', found[0])
        self.assertIn('looped', found[0])


class TakesAcrossPacks(unittest.TestCase):
    def test_a_layer_can_take_from_two_packs(self):
        base = helpers.scratch('unreported_two_packs')
        library = base / 'library'
        for name, freq in (('One', 300), ('Two', 700)):
            samples.add(ts.as_folder(base / name, {'x.wav': ts.wav_bytes(tone(freq, 0.5), rate=RATE),
                                                   'License.txt': ts.CC0_TEXT.encode('utf-8')}), library, pack=name.lower(), licence='CC0-1.0')
        layer = {'source': {'type': 'sample', 'library': 'one', 'file': ['x', {'library': 'two', 'file': 'x'}]}, 'length': 0.3}
        report, resolved = rendered(base, [{'id': 'hit', 'category': 'foley', 'variants': 2, 'layers': [layer]}], library)
        self.assertEqual([v['layers'][0]['source'] for v in resolved['sounds'][0]['variants']], ['one/x', 'two/x'])
        self.assertEqual(len(resolved['recordings']), 2)

    def test_the_noise_of_one_recording_is_not_used_on_another_pack(self):
        found = errors({'id': 'x', 'category': 'foley', 'variants': 2, 'layers': [
            {'source': {'type': 'sample', 'library': 'one', 'file': ['x', {'library': 'two', 'file': 'x'}],
                        'denoise': {'noise': [0.0, 0.4]}}, 'length': 0.3}]})
        self.assertTrue(any('denoise' in e for e in found), found)

    def test_a_bad_pack_name_in_a_take_is_refused(self):
        self.assertTrue(errors({'id': 'x', 'category': 'foley', 'layers': [
            {'source': {'type': 'sample', 'library': 'one', 'file': [{'library': '../up', 'file': 'x'}]}, 'length': 0.3}]}))

    def test_the_builder_takes_a_pack(self):
        self.assertEqual(sfxkit.take('x', 0.5, library='two')['library'], 'two')


class WhatTheToolsSay(unittest.TestCase):
    def test_a_sound_rendered_alone_at_a_held_level_is_not_told_it_will_move(self):
        folder = helpers.scratch('unreported_alone_held')
        path = folder / 'project.json'
        sounds = [WhatHoldsTheSetDown.PAD, dict(WhatHoldsTheSetDown.PAD, id='pad2')]
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': sounds,
                                    'level': {'target_lufs': -24}}), encoding='utf-8')
        out = cli('render', path, '--output', folder / 'r', '--no-page', '--sound', 'pad').stdout
        self.assertNotIn('levelled among themselves', out)

    def test_it_is_told_when_the_level_is_not_held(self):
        folder = helpers.scratch('unreported_alone_free')
        path = folder / 'project.json'
        sounds = [WhatHoldsTheSetDown.PAD, dict(WhatHoldsTheSetDown.PAD, id='pad2')]
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': sounds}), encoding='utf-8')
        self.assertIn('levelled among themselves', cli('render', path, '--output', folder / 'r', '--no-page', '--sound', 'pad').stdout)

    def test_the_time_map_reads_a_whistle_and_no_pitch_in_noise(self):
        tone_at = lambda hz: 0.5 * np.sin(np.arange(4800) * 2 * np.pi * hz / RATE)
        self.assertAlmostEqual(denoise._pitch(tone_at(1000), RATE), 1000.0, delta=25.0)
        self.assertAlmostEqual(denoise._pitch(tone_at(110), RATE), 110.0, delta=2.0)
        self.assertEqual(denoise._pitch(0.2 * np.random.default_rng(3).standard_normal(4800), RATE), 0.0)

    def test_a_search_puts_a_match_in_the_title_first(self):
        row = lambda name, title, labels: {'library': 'p', 'file': name, 'title': title, 'tags': [], 'labels': labels, 'seconds': 1.0,
                                           'channels': 1, 'measures': {'range_db': 40.0}, 'screen': []}
        index = {'packs': {'p': {'title': 'P', 'licence': 'CC0-1.0', 'files': 3}},
                 'files': [row('1', 'river', ['Walk_and_footsteps']), row('2', 'footsteps on tile', []),
                           row('3', 'footsteps in mud', ['Walk_and_footsteps'])]}
        self.assertEqual([f['file'] for f in samples.find(index, 'footsteps')], ['3', '2', '1'])

    def test_a_struck_door_is_not_flagged_for_its_body_being_louder(self):
        x = np.zeros((RATE // 2, 1))
        x[int(0.003 * RATE):int(0.006 * RATE), 0] = 0.4
        n = int(0.1 * RATE)
        x[int(0.045 * RATE):int(0.045 * RATE) + n, 0] += 0.7 * np.sin(2 * np.pi * 70 * np.arange(n) / RATE) * np.hanning(n)
        self.assertIsNone(artifacts.peak_at_finding(x, 0.003))

    def test_a_late_hit_is_still_flagged(self):
        x = np.zeros((RATE // 2, 1))
        x[int(0.3 * RATE):int(0.31 * RATE), 0] = 0.7
        self.assertIsNotNone(artifacts.peak_at_finding(x, 0.003))

    def test_a_mono_loop_stays_in_memory_however_long(self):
        self.assertEqual(export._unity({'category': 'ambience', 'use': 'loop', 'channels': 1}, 20.0, 1)['load_type'], 'Compressed In Memory')
        self.assertEqual(export._unity({'category': 'ambience', 'use': 'loop', 'channels': 2}, 20.0, 1)['load_type'], 'Streaming')

    def test_the_traits_table_shows_the_momentary_level_too(self):
        folder = helpers.scratch('unreported_traits_moment')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [WhatHoldsTheSetDown.PAD]}),
                        encoding='utf-8')
        cli('render', path, '--output', folder / 'r', '--no-page')
        self.assertIn('moment', cli('check', folder / 'r', '--traits').stdout)


class ChipVoices(unittest.TestCase):
    def test_a_quick_attack_does_not_start_with_a_silent_step(self):
        layer = {'source': {'type': 'chip', 'wave': 'pulse', 'duty': 0.5, 'quantize': False}, 'pitch': 880.0, 'length': 0.2,
                 'env': {'attack': 0.001, 'decay': 0.199}}
        x = synth.layer_signal(layer, 1, 0)
        self.assertGreater(float(np.max(np.abs(x[:int(0.003 * RATE)]))), 0.1)

    def test_the_builder_falls_evenly_through_the_volume_steps(self):
        layer = sfxkit.chip(0.0, 0.4, 440.0)
        self.assertEqual(layer['source']['type'], 'chip')
        self.assertEqual(errors({'id': 'x', 'category': 'retro', 'layers': [layer]}), [])
        x = synth.layer_signal(layer, 1, 0)
        late = x[int(0.3 * RATE):int(0.33 * RATE)]
        self.assertGreater(float(np.max(np.abs(late))), 0.05)         # a fall in dB would have reached the zero step by now

    def test_the_format_page_says_what_a_db_fall_does(self):
        text = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'project-format.md').read_text(encoding='utf-8')
        self.assertIn('falls in dB', text)


class ThePageCanActLikeAPhone(unittest.TestCase):
    def test_it_has_the_control_and_the_filter(self):
        base = helpers.scratch('unreported_phone_page')
        render.render(checked([{'id': 'x', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'pitch': 880, 'length': 0.1}]}]),
                      base / 'r')
        page = (base / 'r' / 'audition.html').read_text(encoding='utf-8')
        self.assertIn('id="phone"', page)
        self.assertIn("type = 'highpass'", page)


class SearchingTheLibrary(unittest.TestCase):
    @staticmethod
    def row(name, title, labels, screen=()):
        return {'library': 'p', 'file': name, 'title': title, 'tags': [], 'labels': labels, 'seconds': 1.0, 'channels': 1,
                'measures': {'range_db': 40.0, 'peak_db': -3.0}, 'screen': list(screen)}

    def test_leaving_out_a_word_does_not_leave_out_its_whole_class(self):
        index = {'packs': {'p': {'title': 'P', 'licence': 'CC0-1.0', 'files': 2}},
                 'files': [self.row('1', 'hen clucking', ['Chicken_and_rooster']), self.row('2', 'rooster at dawn', ['Chicken_and_rooster'])]}
        self.assertEqual([f['file'] for f in samples.find(index, 'chicken', exclude=['rooster'])], ['1'])

    def test_leaving_out_voices_still_works(self):
        index = {'packs': {'p': {'title': 'P', 'licence': 'CC0-1.0', 'files': 2}},
                 'files': [self.row('1', 'leaves', ['Human_voice']), self.row('2', 'leaves', ['Rustle'])]}
        self.assertEqual([f['file'] for f in samples.find(index, 'leaves', exclude=['voice'])], ['2'])

    def library(self, name):
        folder = helpers.scratch(f'unreported_listing_{name}')
        return pack(folder, a=noise_recording(0.5), b=noise_recording(0.5, seed=2))

    def test_the_bare_command_does_not_list_every_recording(self):
        out = cli('samples', '--library', self.library('bare')).stdout
        self.assertIn('2 recordings', out)
        self.assertNotIn('pack/a', out)
        self.assertIn('--find', out)

    def test_a_limit_still_lists_them(self):
        self.assertIn('pack/a', cli('samples', '--library', self.library('limit'), '--limit', '5').stdout)

    def test_it_lists_the_classes_it_holds(self):
        index = {'packs': {}, 'files': [self.row('1', 'x', ['Door', 'Wood']), self.row('2', 'y', ['Door'], ['noisy'])]}
        self.assertEqual(samples.labels(index), {'Door': 2, 'Wood': 1})
        self.assertEqual(samples.labels(index, passing=True), {'Door': 1, 'Wood': 1})


class ThePlacesTheToolsPoint(unittest.TestCase):
    def test_the_quietest_stretch_is_not_digital_silence(self):
        x = np.zeros(3 * RATE)
        x[RATE:2 * RATE] = 0.02 * np.random.default_rng(2).standard_normal(RATE)
        x[int(1.4 * RATE):int(1.5 * RATE)] += 0.4
        start, end, db = denoise.quietest(x, RATE)
        self.assertTrue(1.0 <= start and end <= 2.0, (start, end))
        self.assertGreater(db, -60.0)

    def test_silence_alone_still_gives_a_stretch(self):
        self.assertEqual(denoise.quietest(np.zeros(RATE), RATE)[:1], (0.0,))

    def test_a_short_tick_is_not_read_as_far_peakier_than_a_held_sound(self):
        click = tone(1000, 0.04)
        held = tone(1000, 1.0)
        self.assertLess(traits.measures(click)['plr_db'] - traits.measures(held)['plr_db'], 8.0)

    def test_the_traits_table_shows_the_bands(self):
        folder = helpers.scratch('unreported_traits_bands')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [WhatHoldsTheSetDown.PAD]}),
                        encoding='utf-8')
        cli('render', path, '--output', folder / 'r', '--no-page')
        out = cli('check', folder / 'r', '--traits').stdout
        self.assertIn('bands', out)
        row = next(line for line in out.splitlines() if line.startswith('pad'))
        self.assertRegex(row, r'[0-9]+/[0-9]+/[0-9]+/[0-9]+/[0-9]+/[0-9]+')


if __name__ == '__main__':
    unittest.main()
