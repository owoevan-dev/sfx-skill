"""Dynamics: the compressor's static curve and timing, its loop form, the sliding minimum and the true-peak limiter."""
import math
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import dynamics, loudness

RATE = 48000


def square(n, amplitude, period=96):
    x = np.where((np.arange(n) // (period // 2)) % 2 == 0, 1.0, -1.0) * amplitude
    return x


class StaticCurve(unittest.TestCase):
    def test_below_above_and_a_continuous_knee(self):
        t, r, w = -20.0, 4.0, 10.0
        x = np.array([-40.0, -25.0 - 1e-9, -25.0, -15.0, -15.0 + 1e-9, 0.0])
        y = dynamics.static_curve(x, t, r, w)
        self.assertAlmostEqual(y[0], -40.0)
        self.assertAlmostEqual(y[5], t + (0.0 - t) / r)
        self.assertAlmostEqual(y[1], y[2], places=6)          # into the knee
        self.assertAlmostEqual(y[3], y[4], places=6)          # out of the knee
        self.assertAlmostEqual(y[3], t + (-15.0 - t) / r, places=9)

    def test_a_hard_knee(self):
        y = dynamics.static_curve(np.array([-30.0, -10.0]), -20.0, 2.0, 0.0)
        self.assertEqual(list(y), [-30.0, -15.0])


class Compressor(unittest.TestCase):
    def test_a_steady_level_settles_on_the_static_curve(self):
        x = square(RATE, 0.5)                                   # |x| constant: -6.02 dB
        y = dynamics.compress(x, threshold_db=-20.0, ratio=4.0, knee_db=0.0, attack_ms=2.0, release_ms=50.0,
                              makeup_db=3.0)
        level = 20 * math.log10(0.5)
        expected = 0.5 * 10 ** ((-20.0 + (level + 20.0) / 4 - level + 3.0) / 20)
        self.assertAlmostEqual(float(np.max(np.abs(y[RATE // 2:]))), expected, places=6)

    def test_attack_time_is_the_10_to_90_percent_time(self):
        n = RATE // 2
        x = np.concatenate([square(n, 0.001), square(n, 1.0)])
        attack_ms = 10.0
        y = dynamics.compress(x, threshold_db=-30.0, ratio=10.0, knee_db=0.0, attack_ms=attack_ms, release_ms=50.0)
        gain_db = 20 * np.log10(np.abs(y[n:]) / 1.0)
        final = gain_db[-1]
        t10 = int(np.argmax(gain_db <= 0.1 * final))
        t90 = int(np.argmax(gain_db <= 0.9 * final))
        self.assertAlmostEqual(t90 - t10, attack_ms / 1000 * RATE, delta=2)

    def test_no_mix_is_the_input_and_stereo_shares_one_gain(self):
        x = np.random.default_rng(1).standard_normal((4800, 2)) * 0.3
        self.assertTrue(np.array_equal(dynamics.compress(x, mix=0.0), x))
        y = dynamics.compress(x, threshold_db=-30.0, ratio=8.0)
        ratio = y / np.where(x == 0, 1, x)
        self.assertLess(float(np.max(np.abs(ratio[:, 0] - ratio[:, 1]))), 1e-12)

    def test_a_loop_is_compressed_into_its_steady_state(self):
        x = np.random.default_rng(2).standard_normal(RATE // 2) * 0.3
        x[:2400] *= 3.0
        loop = dynamics.compress(x, threshold_db=-20.0, ratio=4.0, attack_ms=5.0, release_ms=100.0, loop=True)
        tiled = dynamics.compress(np.tile(x, 4), threshold_db=-20.0, ratio=4.0, attack_ms=5.0, release_ms=100.0)
        self.assertLess(float(np.max(np.abs(loop - tiled[2 * len(x):3 * len(x)]))), 1e-9)


class ChainStage(unittest.TestCase):
    def test_the_chain_compressor_follows_its_static_curve(self):
        """A held tone settles where the static curve puts its peak level (the fast attack catches each peak and the
        slow release holds the gain between them)."""
        from sfxlib import project, synth
        tone = {'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.6, 'pitch': 220.0,
                            'gain_db': -6.0206, 'env': [[0, 1.0], [0.6, 1.0]]}],
                'chain': {'compressor': {'threshold_db': -20.0, 'ratio': 4.0, 'knee_db': 0.0, 'attack_ms': 1.0,
                                         'release_ms': 200.0, 'makeup_db': 2.0}}}
        project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [dict(tone, id='t', category='ui')]})
        x = synth.render_sound(tone, seed=1)[:, 0]
        level = -6.0206
        expected = 10 ** ((-20.0 + (level + 20.0) / 4 + 2.0) / 20)
        self.assertAlmostEqual(20 * math.log10(float(np.max(np.abs(x[12000:24000])))), 20 * math.log10(expected),
                               delta=0.3)

    def test_compressor_fields_are_checked(self):
        from sfxlib import project
        bad = {'id': 'x', 'category': 'impact', 'layers': [{'source': {'type': 'noise'}, 'length': 0.1}],
               'chain': {'compressor': {'ratio': 50.0, 'treshold_db': -10.0}}}
        with self.assertRaises(project.ProjectError) as caught:
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [bad]})
        text = '\n'.join(caught.exception.errors)
        self.assertIn('ratio', text)
        self.assertIn("did you mean 'threshold_db'", text)


class Limiter(unittest.TestCase):
    def test_running_min_matches_brute_force(self):
        v = np.random.default_rng(3).random(500)
        for width in (1, 2, 3, 7, 64, 100):
            padded = np.concatenate([v, np.full(width, v[-1])])
            brute = np.array([padded[i:i + width].min() for i in range(len(v))])
            self.assertTrue(np.array_equal(dynamics.running_min(v, width), brute), width)

    def test_a_signal_under_the_ceiling_is_untouched(self):
        x = np.sin(2 * np.pi * 440 * np.arange(4800) / RATE) * 0.5
        y, reduction = dynamics.limit(x, -1.0)
        self.assertTrue(np.array_equal(x, y))
        self.assertEqual(reduction, 0.0)

    def test_peaks_are_held_under_the_ceiling_smoothly_and_only_near_them(self):
        rng = np.random.default_rng(4)
        x = rng.standard_normal((RATE, 2)) * 0.05
        x[24000:24048] += 1.4                                   # a burst far over -1 dBTP
        y, reduction = dynamics.limit(x, -1.0, lookahead_ms=1.5, release_ms=60.0)
        self.assertLessEqual(loudness.true_peak_db(y), -1.0 + 1e-6)
        self.assertGreater(reduction, 3.0)
        gain = y[:, 0] / x[:, 0]
        self.assertTrue(np.allclose(gain[:20000], 1.0))         # long before the burst
        self.assertTrue(np.allclose(gain[-4000:], 1.0, atol=1e-6))   # long after (the release has recovered)
        ratio = y / x
        self.assertLess(float(np.max(np.abs(ratio[:, 0] - ratio[:, 1]))), 1e-9)
        width = int(round(1.5 / 1000 * RATE))
        self.assertLessEqual(float(np.max(-np.diff(gain))), 1.0 / width + 1e-9)   # the fall is a ramp, no step

    def test_a_hold_keeps_the_limiter_from_leaving_an_offset_on_a_lopsided_wave(self):
        """A lopsided wave (tall narrow peaks, shallow troughs: a 60 Hz pulse of 20 % duty) limited 6 dB: a gain that
        dips at every peak and recovers between them takes more off the peaks than the troughs and leaves an offset;
        held for 25 ms (more than a period), the gain stays down across the wave and the offset all but goes."""
        n = RATE
        phase = (np.arange(n) * 60 / RATE) % 1.0
        x = np.where(phase < 0.2, 0.8, -0.2) * 2.0           # mean 0, peaks four times the troughs
        x = x[:, None]
        plain, _ = dynamics.limit(x, -1.0)
        held, reduction = dynamics.limit(x, -1.0, hold_ms=25.0)
        self.assertGreater(reduction, 5.0)
        self.assertLessEqual(loudness.true_peak_db(held), -1.0 + 1e-6)
        self.assertGreater(abs(float(np.mean(plain))), 0.005)
        self.assertLess(abs(float(np.mean(held))), abs(float(np.mean(plain))) / 10)


if __name__ == '__main__':
    unittest.main()
