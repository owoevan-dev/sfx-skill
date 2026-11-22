"""Breakpoint curves evaluated at sample times: boundaries on whole samples, segment shapes, clamping."""
import math
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import curves

RATE = 48000


class Curves(unittest.TestCase):
    def test_a_number_is_a_constant(self):
        np.testing.assert_array_equal(curves.evaluate(440, 10), np.full(10, 440.0))

    def test_points_land_on_whole_samples(self):
        # 0.01 s is sample 480: the hold switches exactly there
        values = curves.evaluate([[0, 1.0, 'hold'], [0.01, 2.0]], 1000)
        self.assertEqual(values[479], 1.0)
        self.assertEqual(values[480], 2.0)
        self.assertEqual(curves.samples(0.01), 480)

    def test_linear_and_clamped_ends(self):
        values = curves.evaluate([[0.001, 0.0], [0.003, 1.0]], 200)  # samples 48 to 144
        self.assertEqual(values[0], 0.0)
        self.assertAlmostEqual(values[96], 0.5, places=9)
        self.assertEqual(values[199], 1.0)

    def test_exponential_segments_pass_the_geometric_mean(self):
        values = curves.evaluate([[0, 100.0, 'exp'], [0.002, 400.0]], 97)  # samples 0 to 96
        self.assertAlmostEqual(values[48], 200.0, places=6)

    def test_db_segments_are_straight_in_decibels_and_reach_zero(self):
        values = curves.evaluate([[0, 1.0, 'db'], [0.002, 0.0]], 97)
        self.assertEqual(values[96], 0.0)
        # halfway in dB between 0 dB and the -80 dB floor
        self.assertAlmostEqual(20 * math.log10(values[48]), -40.0, delta=0.01)

    def test_unknown_curve_name_is_an_error(self):
        with self.assertRaises(ValueError):
            curves.evaluate([[0, 1.0, 'wobble'], [0.1, 2.0]], 10)

    def test_times_must_not_go_back(self):
        with self.assertRaises(ValueError):
            curves.evaluate([[0.1, 1.0], [0.05, 2.0]], 10)

    def test_envelope_shorthand(self):
        pts = curves.envelope_points({'attack': 0.002, 'hold': 0.01, 'decay': 0.1, 'curve': 'db'})
        self.assertEqual(pts[0][:2], [0.0, 0.0])
        self.assertEqual(pts[1][:2], [0.002, 1.0])
        self.assertEqual(pts[-1][:2], [0.112, 0.0])
        values = curves.evaluate(pts, curves.samples(0.112) + 1)
        self.assertEqual(values[-1], 0.0)
        self.assertEqual(values[curves.samples(0.002)], 1.0)


if __name__ == '__main__':
    unittest.main()
