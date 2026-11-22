"""Spectral subtraction for sample layers, and the time map that finds events and noise without listening."""
import unittest

import numpy as np

import helpers
import test_fsd50k
from sfxlib import denoise, fsd50k, project, samples, synth

RATE = 48000


def tone_in_noise(seconds=2.0, seed=4):
    """A 1 kHz tone from 0.5 to 1.0 s in steady white noise 34 dB under it."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.5 * np.sin(2 * np.pi * 1000 * t) * ((t >= 0.5) & (t < 1.0))
    return tone + 0.01 * rng.standard_normal(len(t)), tone


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-12)


class Subtract(unittest.TestCase):
    def test_frames_rebuild_the_recording_exactly(self):
        x = np.random.default_rng(1).standard_normal(5000)
        frames, length = denoise._frames(x)
        win = denoise._window()
        acc = np.zeros(length)
        for i, f in enumerate(frames):
            acc[i * denoise.HOP:i * denoise.HOP + denoise.N] += f * win * win
        np.testing.assert_allclose(acc[denoise.N:denoise.N + len(x)], x, atol=1e-12)

    def test_noise_falls_by_about_reduce_db_and_the_tone_stays(self):
        x, tone = tone_in_noise()
        y = denoise.subtract(x, RATE, [0.0, 0.4], reduce_db=20)
        quiet = slice(int(1.3 * RATE), int(1.9 * RATE))
        self.assertLess(rms_db(y[quiet]) - rms_db(x[quiet]), -17)
        self.assertGreater(rms_db(y[quiet]) - rms_db(x[quiet]), -23)          # the floor: no more than reduce_db
        loud = slice(int(0.6 * RATE), int(0.9 * RATE))
        self.assertLess(abs(rms_db(y[loud]) - rms_db(tone[loud])), 0.5)
        y10 = denoise.subtract(x, RATE, [0.0, 0.4], reduce_db=10)
        self.assertGreater(rms_db(y10[quiet]), rms_db(y[quiet]) + 7)

    def test_stereo_channels_are_cleaned_each_on_their_own(self):
        x, _ = tone_in_noise()
        st = np.stack([x, 0.5 * x], axis=1)
        y = denoise.subtract(st, RATE, [0.0, 0.4])
        self.assertEqual(y.shape, st.shape)
        np.testing.assert_allclose(y[:, 1], 0.5 * y[:, 0], atol=1e-9)

    def test_a_bad_noise_stretch_is_refused(self):
        x, _ = tone_in_noise()
        for stretch in ([1.5, 2.5], [0.2, 0.25], [0.4, 0.2]):
            with self.assertRaises(denoise.DenoiseError):
                denoise.subtract(x, RATE, stretch)


class Map(unittest.TestCase):
    def test_events_pitch_quiet_stretch_and_bands_are_found(self):
        x, _ = tone_in_noise()
        rows = denoise.time_map(x, RATE, hop=0.1)
        loud = [r['t'] for r in rows if r['rms_db'] > -20]
        self.assertEqual((loud[0], loud[-1]), (0.5, 0.9))
        t = np.arange(RATE) / RATE
        pulses = (np.sin(2 * np.pi * 100 * t) > 0.95).astype(float) - 0.05   # a 100 Hz pulse train
        self.assertAlmostEqual(denoise.time_map(pulses, RATE, hop=0.2)[2]['pitch_hz'], 100, delta=2)
        a, b, _ = denoise.quietest(x, RATE)
        self.assertTrue(b <= 0.5 or a >= 1.0)
        levels = denoise.bands(x, RATE, 0.6, 0.9)
        self.assertEqual(max(levels, key=levels.get), 1000)


class Layer(unittest.TestCase):
    def test_a_sample_layer_can_take_out_its_recording_noise(self):
        x, _ = tone_in_noise(seconds=1.5)
        folder = test_fsd50k.dataset(helpers.scratch('denoise-data'), {
            '7': (test_fsd50k.CC0, 'tone in noise', ['Hum'], x[::1], True)})
        library_dir = helpers.scratch('denoise-lib')
        fsd50k.add(folder, library_dir, check_md5=False, log=lambda *a: None)
        library = samples.Library(library_dir)
        src = {'type': 'sample', 'library': 'fsd50k-eval', 'file': '7'}
        plain = synth.render_sound({'layers': [{'source': src, 'length': 1.4}]}, seed=1, samples=library)
        clean = synth.render_sound({'layers': [{'source': dict(src, denoise={'noise': [0.0, 0.4]}),
                                                'length': 1.4}]}, seed=1, samples=library)
        quiet = slice(int(1.1 * RATE), int(1.3 * RATE))
        self.assertLess(rms_db(clean[quiet]), rms_db(plain[quiet]) - 15)
        with self.assertRaisesRegex(samples.SampleError, 'noise stretch'):
            synth.render_sound({'layers': [{'source': dict(src, denoise={'noise': [5.0, 6.0]}), 'length': 1.4}]},
                               seed=1, samples=library)


class Schema(unittest.TestCase):
    def errors(self, d):
        src = {'type': 'sample', 'library': 'fsd50k-eval', 'file': '7', 'denoise': d}
        try:
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                {'id': 'x', 'category': 'impact', 'layers': [{'source': src, 'length': 0.3}]}]})
        except project.ProjectError as error:
            return error.errors
        return []

    def test_denoise_fields_are_checked(self):
        self.assertEqual(self.errors({'noise': [0, 0.4]}), [])
        self.assertEqual(self.errors({'noise': [0, 0.4], 'reduce_db': 12, 'over': 3}), [])
        for bad in ({'noise': [0.3, 0.35]}, {'noise': 'start'}, {'noise': [0, 0.4], 'reduce_db': 80},
                    {'noise': [0, 0.4], 'over': 0.5}, {'noise': [0, 0.4], 'amount': 1}, [0, 0.4]):
            self.assertTrue(self.errors(bad), bad)


if __name__ == '__main__':
    unittest.main()
