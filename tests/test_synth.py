"""Layers and whole sounds: sample-exact starts, determinism, seeds, panning, the fixed chain and the tidy ending."""
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import synth

RATE = 48000


def layer(**kw):
    base = {'source': {'type': 'osc', 'wave': 'sine', 'phase': 90}, 'start': 0.0, 'length': 0.05, 'pitch': 1000,
            'env': [[0, 1.0], [0.04, 1.0], [0.05, 0.0]], 'gain_db': 0.0}
    base.update(kw)
    return base


class Layers(unittest.TestCase):
    def test_a_layer_starts_on_its_exact_sample(self):
        sound = synth.render_sound({'layers': [layer(start=0.01)]}, seed=1, channels=1)
        first = int(np.argmax(np.abs(sound[:, 0]) > 0))
        self.assertEqual(first, 480)

    def test_rendering_is_deterministic(self):
        spec = {'layers': [layer(), layer(source={'type': 'noise', 'color': 'pink'}, gain_db=-12)]}
        a = synth.render_sound(spec, seed=7, channels=1)
        b = synth.render_sound(spec, seed=7, channels=1)
        np.testing.assert_array_equal(a, b)
        c = synth.render_sound(spec, seed=8, channels=1)
        self.assertFalse(np.array_equal(a, c))

    def test_two_noise_layers_get_different_streams(self):
        spec = {'layers': [layer(source={'type': 'noise', 'color': 'white'}),
                           layer(source={'type': 'noise', 'color': 'white'}, gain_db=-200)]}
        one = synth.render_layer(spec['layers'][0], seed=3, index=0)
        two = synth.render_layer(spec['layers'][0], seed=3, index=1)
        self.assertFalse(np.array_equal(one, two))

    def test_pan_is_equal_power(self):
        left = synth.render_sound({'layers': [layer(pan=-1.0)]}, seed=1, channels=2)
        self.assertLess(np.max(np.abs(left[:, 1])), 1e-12)
        centre = synth.render_sound({'layers': [layer(pan=0.0)]}, seed=1, channels=2)
        np.testing.assert_allclose(centre[:, 0], centre[:, 1])
        self.assertAlmostEqual(float(np.max(np.abs(centre[:, 0]))), np.sqrt(0.5), delta=0.01)

    def test_envelope_reaches_zero_at_the_layer_end_even_when_written_open(self):
        sound = synth.render_sound({'layers': [layer(env=[[0, 1.0], [0.05, 1.0]])]}, seed=1, channels=1)
        self.assertEqual(sound[-1, 0], 0.0)

    def test_swept_filter_and_fm_layers_render(self):
        spec = {'layers': [
            layer(source={'type': 'noise', 'color': 'white'},
                  filter={'type': 'bandpass', 'cutoff': [[0, 500.0, 'exp'], [0.05, 5000.0]], 'q': 4.0}),
            layer(source={'type': 'fm', 'ratio': 2.0, 'index': [[0, 3.0], [0.05, 0.0]]}, pitch=[[0, 600.0, 'exp'],
                  [0.05, 900.0]], drive=0.5)]}
        sound = synth.render_sound(spec, seed=2, channels=1)
        self.assertTrue(np.isfinite(sound).all())
        self.assertGreater(np.max(np.abs(sound)), 0.1)


class Chain(unittest.TestCase):
    def test_delay_lengthens_the_sound_and_the_ending_is_tidy(self):
        dry = synth.render_sound({'layers': [layer()]}, seed=1, channels=1)
        wet = synth.render_sound({'layers': [layer()], 'chain': {'delay': {'time': 0.08, 'feedback': 0.3,
                                                                           'mix': 0.4}}}, seed=1, channels=1)
        self.assertGreater(len(wet), len(dry) + int(0.08 * RATE))
        self.assertEqual(wet[-1, 0], 0.0)

    def test_dc_is_removed(self):
        # a decaying sine that starts from rest has a positive mean; the sound's DC block takes it out
        spec = {'layers': [layer(source={'type': 'osc', 'wave': 'sine', 'phase': 0}, pitch=120,
                                 env=[[0, 1.0, 'db'], [0.05, 0.0]])]}
        sound = synth.render_sound(spec, seed=1, channels=1)
        self.assertLess(abs(float(np.mean(sound))), 0.001)

    def test_trailing_near_silence_is_trimmed(self):
        spec = {'layers': [layer(), layer(start=0.5, gain_db=-120)]}
        sound = synth.render_sound(spec, seed=1, channels=1)
        self.assertLess(len(sound), int(0.1 * RATE))


if __name__ == '__main__':
    unittest.main()
