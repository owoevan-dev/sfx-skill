"""Faults the agent cannot hear (artifacts.py) and the spectrogram it can look at (spectrogram.py): isolated clicks are
flagged and strikes are not, a sound's loudest moment is checked against `peak_at`, and the pictures are valid PNGs
with energy where it belongs."""
import json
import struct
import subprocess
import sys
import unittest
import zlib

import numpy as np

import helpers
from sfxlib import artifacts, project, render, spectrogram, traits

RATE = 48000


def hum(seconds=1.0, amp=0.2):
    t = np.arange(int(seconds * RATE)) / RATE
    return amp * np.sin(2 * np.pi * 220 * t)


class Clicks(unittest.TestCase):
    def test_an_isolated_spike_is_found(self):
        x = hum()
        x[int(0.5 * RATE)] += 0.6
        found = artifacts.clicks(x[:, None])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0], 0.5, delta=0.002)

    def test_a_strike_and_its_ring_are_not_clicks(self):
        # a small strike early in a hum that swells to its loudest at the end: the strike is not the sound's peak,
        # so only its slow fall tells it from a click
        rng = np.random.default_rng(4)
        t = np.arange(RATE) / RATE
        x = 0.6 * t * np.sin(2 * np.pi * 220 * t)
        n = int(0.06 * RATE)
        k = int(0.2 * RATE)
        x[k:k + n] += rng.standard_normal(n) * np.exp(-np.arange(n) / (0.02 * RATE)) * 0.1
        self.assertGreater(artifacts.loudest_s(x[:, None]), 0.9)
        self.assertEqual(artifacts.clicks(x[:, None]), [])

    def test_a_tick_that_is_the_sound_is_not_reported(self):
        x = np.zeros(int(0.3 * RATE))
        k = int(0.1 * RATE)                                   # far enough in that only the peak's guard spares it
        x[k:k + 10] = 0.9
        x += 1e-5 * np.random.default_rng(5).standard_normal(len(x))
        self.assertEqual(artifacts.clicks(x[:, None]), [])

    def test_the_last_edge_of_a_thin_pulse_train_is_not_a_click(self):
        # a chip's narrow pulse at a low pitch: an edge every 8 ms, the last with nothing after it
        x = np.zeros(int(0.3 * RATE))
        for k in range(int(0.02 * RATE), int(0.2 * RATE), 384):
            x[k:k + 12] = 0.4
        x += 1e-5 * np.random.default_rng(6).standard_normal(len(x))
        self.assertEqual(artifacts.clicks(x[:, None]), [])

    def test_two_clicks_close_together_are_both_found(self):
        x = hum()
        for at in (0.2, 0.207):
            x[int(at * RATE)] += 0.6
        found = artifacts.clicks(x[:, None])
        self.assertEqual(len(found), 2, found)
        self.assertAlmostEqual(found[0], 0.2, delta=0.002)
        self.assertAlmostEqual(found[1], 0.207, delta=0.002)

    def test_a_click_in_opposite_channels_is_found(self):
        x = hum()
        x[int(0.5 * RATE)] += 0.6
        found = artifacts.clicks(np.stack([x, -x], axis=1))
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0], 0.5, delta=0.002)

    def test_the_last_partial_millisecond_counts(self):
        x = np.zeros(48047)
        x[-10:] = 0.5
        self.assertAlmostEqual(artifacts.loudest_s(x[:, None]), 1.0, delta=0.002)
        self.assertGreater(traits.measures(x)['peak_s'], 0.99)

    def test_a_loops_peak_is_compared_around_its_seam(self):
        x = np.zeros(RATE)
        x[int(0.995 * RATE):int(0.999 * RATE)] = 0.5
        self.assertIsNone(artifacts.peak_at_finding(x[:, None], 0.005, loop=True))
        self.assertIsNotNone(artifacts.peak_at_finding(x[:, None], 0.005))


def struck(seconds=2.6, start=0.0, decay=5.0):
    """A bright struck note from `start`, dying away."""
    t = np.arange(int(seconds * RATE)) / RATE
    x = sum(np.sin(2 * np.pi * f * t) for f in (3400, 5200, 7900)) / 3
    return x * np.exp(-decay * np.maximum(t - start, 0)) * (t >= start)


def with_tick(x, at, amp=0.4, ms=30):
    """x with a short hit at `at` seconds that rings on for some milliseconds, as a tick left in a sample does."""
    k, n = int(at * RATE), int(ms * RATE / 1000)
    y = x.copy()
    y[k:k + n] += amp * np.random.default_rng(3).standard_normal(n) * np.exp(-np.arange(n) / (0.01 * RATE))
    return y


class StrayAttacks(unittest.TestCase):
    """An instrument's own performance: an attack with no note there."""

    def test_a_lone_tick_long_after_its_note_is_found(self):
        found = artifacts.stray_attacks(with_tick(struck(), 1.24)[:, None], [0.0])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0], 1.24, delta=0.002)

    def test_the_tick_of_a_clean_note_is_not_invented(self):
        self.assertEqual(artifacts.stray_attacks(struck()[:, None], [0.0]), [])

    def test_a_sample_whose_attack_comes_late_is_not_one(self):
        self.assertEqual(artifacts.stray_attacks(struck(start=0.095)[:, None], [0.0]), [])

    def test_a_low_pulse_train_is_not_one(self):
        x = np.zeros(int(2.6 * RATE))
        for k in range(0, len(x) - 12, 1333):                 # 36 Hz: each period is an edge
            x[k:k + 12] = 0.4
        x += 1e-5 * np.random.default_rng(6).standard_normal(len(x))
        self.assertEqual(artifacts.stray_attacks(x[:, None], [0.0]), [])

    def test_several_after_one_note_are_the_instruments_own(self):
        x = struck()
        for at in (0.6, 1.1, 1.7):
            x = with_tick(x, at)
        self.assertEqual(artifacts.stray_attacks(x[:, None], [0.0]), [])

    def test_a_tick_just_after_a_later_note_is_that_notes_attack(self):
        x = struck(seconds=2.6) + struck(seconds=2.6, start=1.1)
        self.assertEqual(artifacts.stray_attacks(with_tick(x, 1.24)[:, None], [0.0, 1.1]), [])
        found = artifacts.stray_attacks(with_tick(x, 1.8)[:, None], [0.0, 1.1])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0], 1.8, delta=0.002)

    def test_each_note_is_judged_on_its_own(self):
        x = struck(seconds=4.0) + struck(seconds=4.0, start=2.0)
        found = artifacts.stray_attacks(with_tick(with_tick(x, 1.0), 3.2)[:, None], [0.0, 2.0])
        self.assertEqual(len(found), 2)
        self.assertAlmostEqual(found[0], 1.0, delta=0.002)
        self.assertAlmostEqual(found[1], 3.2, delta=0.002)

    def test_a_quiet_note_is_judged_against_its_own_loudest_moment(self):
        x = struck(seconds=4.0) + 0.05 * struck(seconds=4.0, start=2.0)
        found = artifacts.stray_attacks(with_tick(x, 3.2, amp=0.02)[:, None], [0.0, 2.0])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0], 3.2, delta=0.002)

    def test_a_chord_is_one_start(self):
        found = artifacts.stray_attacks(with_tick(struck(), 1.24)[:, None], [0.0, 0.0, 0.0])
        self.assertEqual(len(found), 1)

    def test_a_tick_in_one_channel_or_in_opposite_polarity_is_found(self):
        x, clean = with_tick(struck(), 1.24), struck()
        self.assertEqual(len(artifacts.stray_attacks(np.stack([clean, x], axis=1), [0.0])), 1)
        self.assertEqual(len(artifacts.stray_attacks(np.stack([x, -x], axis=1), [0.0])), 1)

    def test_a_rise_over_two_milliseconds_is_one_attack(self):
        x = struck()
        k = int(1.24 * RATE)
        n = int(0.03 * RATE)
        burst = 0.4 * np.random.default_rng(3).standard_normal(n) * np.exp(-np.arange(n) / (0.01 * RATE))
        burst[:40] *= 0.02                                     # the first millisecond holds only the start of it
        x[k:k + n] += burst
        self.assertEqual(len(artifacts.stray_attacks(x[:, None], [0.0])), 1)

    def test_nothing_to_judge(self):
        self.assertEqual(artifacts.stray_attacks(np.zeros((100, 2)), [0.0]), [])
        self.assertEqual(artifacts.stray_attacks(struck()[:, None], []), [])


class Render(unittest.TestCase):
    def render(self, sounds, name):
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': sounds})
        out = helpers.scratch(name) / 'render'
        render.render(proj, out)
        return json.loads((out / 'check.json').read_text(encoding='utf-8')), out

    def test_check_flags_a_click_and_a_missed_peak_at(self):
        body = {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.8, 'pitch': 220,
                'env': [[0, 0], [0.01, 1], [0.7, 1, 'cos'], [0.8, 0]], 'gain_db': -6}
        click = {'source': {'type': 'noise', 'color': 'white'}, 'start': 0.5, 'length': 0.001,
                 'env': [[0, 1], [0.001, 0]], 'gain_db': 6}
        swell = {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 0.3, 'length': 0.2, 'pitch': 880,
                 'env': [[0, 0], [0.19, 1], [0.2, 0]], 'gain_db': 6}
        check, _ = self.render([
            {'id': 'clicky', 'category': 'impact', 'layers': [body, click]},
            {'id': 'crackly', 'category': 'impact', 'crackle': True, 'layers': [body, click]},
            {'id': 'clean', 'category': 'impact', 'layers': [body]},
            {'id': 'late', 'category': 'impact', 'peak_at': 0.05, 'layers': [body, swell]},
            {'id': 'on_time', 'category': 'impact', 'peak_at': 0.49, 'layers': [body, swell]}], 'artifacts_render')
        kinds = {(f['kind'], f['sound']) for f in check['flags']}
        self.assertIn(('click', 'clicky'), kinds)
        self.assertNotIn(('click', 'clean'), kinds)
        self.assertNotIn(('click', 'crackly'), kinds)          # it crackles on purpose
        self.assertIn(('peak_at', 'late'), kinds)
        self.assertNotIn(('peak_at', 'on_time'), kinds)
        traits = {s['id']: s['traits'] for s in check['sounds']}
        self.assertAlmostEqual(traits['on_time']['peak_s'], 0.49, delta=0.02)

    def test_a_loop_is_checked_against_its_peak_at(self):
        bed = {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 2.0, 'pitch': 220, 'gain_db': -18}
        beat = {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 1.2, 'length': 0.2, 'pitch': 880, 'gain_db': 0,
                'env': [[0, 0], [0.1, 1], [0.2, 0]]}                  # at its loudest at 1.3 s
        loop = {'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'layers': [bed, beat]}
        check, _ = self.render([dict(loop, id='late', peak_at=0.1), dict(loop, id='on_time', peak_at=1.3)],
                               'artifacts_loop_peak')
        kinds = {(f['kind'], f['sound']) for f in check['flags']}
        self.assertIn(('peak_at', 'late'), kinds)
        self.assertNotIn(('peak_at', 'on_time'), kinds)

    def test_peak_at_is_checked_by_the_project(self):
        with self.assertRaises(project.ProjectError):
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                {'id': 'x', 'category': 'ui', 'peak_at': -1, 'layers': [
                    {'source': {'type': 'noise'}, 'length': 0.1}]}]})


class Picture(unittest.TestCase):
    def test_a_tone_draws_its_line_and_the_png_is_valid(self):
        t = np.arange(RATE) / RATE
        x = (0.5 * np.sin(2 * np.pi * 1000 * t))[:, None]
        img, scale = spectrogram.picture(x)
        self.assertEqual(img.shape[:2], (scale['height'], scale['width']))
        spec = img[spectrogram.ENVELOPE_H + spectrogram.GAP:].astype(float).sum(axis=2)
        middle = spec[:, scale['width'] // 2]
        row = int(np.argmax(middle))
        f = spectrogram.F_LO * (spectrogram.F_HI / spectrogram.F_LO) ** ((spectrogram.HEIGHT - 1 - row)
                                                                        / (spectrogram.HEIGHT - 1))
        self.assertAlmostEqual(f, 1000, delta=60)
        path = helpers.scratch('spectrogram_png') / 'tone.png'
        spectrogram.write(x, path)
        data = path.read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        w, h = struct.unpack('>II', data[16:24])
        self.assertEqual((w, h), (scale['width'], scale['height']))
        length = struct.unpack('>I', data[33:37])[0]
        self.assertEqual(data[37:41], b'IDAT')
        raw = zlib.decompress(data[41:41 + length])
        self.assertEqual(len(raw), h * (1 + 3 * w))

    def row_hz(self, img, column):
        spec = img[spectrogram.ENVELOPE_H + spectrogram.GAP:].astype(float).sum(axis=2)
        row = int(np.argmax(spec[:, column]))
        return spectrogram.F_LO * (spectrogram.F_HI / spectrogram.F_LO) ** ((spectrogram.HEIGHT - 1 - row)
                                                                            / (spectrogram.HEIGHT - 1))

    def test_channels_in_opposite_polarity_still_draw(self):
        t = np.arange(RATE) / RATE
        x = 0.5 * np.sin(2 * np.pi * 1000 * t)
        img, scale = spectrogram.picture(np.stack([x, -x], axis=1))
        self.assertAlmostEqual(self.row_hz(img, scale['width'] // 2), 1000, delta=60)
        envelope = img[:spectrogram.ENVELOPE_H, scale['width'] // 2].astype(float).sum(axis=1)
        self.assertGreater(int(np.sum(envelope > 0)), spectrogram.ENVELOPE_H // 2)

    def test_a_narrow_high_tone_is_not_skipped_between_rows(self):
        t = np.arange(RATE) / RATE
        for hz in (15000, 15437, 17123):
            x = 0.5 * np.sin(2 * np.pi * hz * t) + 0.0005 * np.sin(2 * np.pi * 500 * t)
            img, scale = spectrogram.picture(x[:, None])
            self.assertAlmostEqual(self.row_hz(img, scale['width'] // 2) / hz, 1.0, delta=0.05, msg=hz)

    def test_silence_draws_dark(self):
        img, scale = spectrogram.picture(np.zeros((RATE, 1)))
        spec = img[spectrogram.ENVELOPE_H + spectrogram.GAP:]
        self.assertEqual(int(np.median(spec)), 0)
        column = img[:spectrogram.ENVELOPE_H, scale['width'] // 2 + 3].astype(float).sum(axis=1)
        self.assertLessEqual(int(np.sum(column > 0)), 1)

    def test_the_command_draws_a_render(self):
        body = {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.3, 'pitch': 440}
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [
            {'id': 'beep', 'category': 'ui', 'layers': [body]}]})
        out = helpers.scratch('spectrogram_cli') / 'render'
        render.render(proj, out)
        proc = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'spectrogram', str(out)],
                              capture_output=True, text=True, encoding='utf-8', env=helpers.child_env(), timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((out / 'spectrograms' / 'beep_01.png').is_file())
        self.assertFalse((out / 'spectrograms' / 'beep_02.png').exists())
        self.assertIn('loudest at', proc.stdout)


if __name__ == '__main__':
    unittest.main()
