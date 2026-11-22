"""Measurements of one sound: onset, ending, DC, clipping, spectrum shares, centroid, stereo."""
import unittest

import numpy as np

import helpers  # noqa: F401  (import paths)
from sfxlib import measure, traits

RATE = 48000


def tone(freq, seconds, amp=0.5):
    return amp * np.sin(2 * np.pi * freq * np.arange(int(seconds * RATE)) / RATE)


class Onset(unittest.TestCase):
    def test_onset_counts_the_leading_silence(self):
        sig = np.concatenate([np.zeros(240), tone(1000, 0.1)])
        self.assertAlmostEqual(measure.onset_ms(sig), 240 / RATE * 1000, delta=0.05)

    def test_a_sound_that_starts_at_once_has_no_onset_delay(self):
        sig = np.cos(2 * np.pi * 1000 * np.arange(4800) / RATE) * 0.5
        self.assertEqual(measure.onset_ms(sig), 0.0)

    def test_low_noise_before_the_sound_does_not_count_as_its_start(self):
        rng = np.random.default_rng(0)
        sig = np.concatenate([1e-4 * rng.standard_normal(480), tone(1000, 0.1)])  # -80 dB of the peak, below -40
        self.assertAlmostEqual(measure.onset_ms(sig), 10.0, delta=0.1)


class Ending(unittest.TestCase):
    def test_an_abrupt_cut_is_seen(self):
        ending = measure.ending(tone(1000, 0.2))
        self.assertTrue(ending['cut'])

    def test_a_faded_ending_is_not_a_cut(self):
        sig = tone(1000, 0.2)
        fade = int(0.02 * RATE)
        sig[-fade:] *= np.cos(np.linspace(0, np.pi / 2, fade)) ** 2
        sig[-1] = 0.0
        self.assertFalse(measure.ending(sig)['cut'])


class Basics(unittest.TestCase):
    def test_dc_offset(self):
        self.assertAlmostEqual(measure.dc_offset(tone(1000, 0.1) + 0.01), 0.01, delta=1e-4)

    def test_clipped_samples(self):
        self.assertEqual(measure.clipped(np.array([0.0, 1.0, -1.0, 0.5, 0.9995])), 3)

    def test_band_shares_and_centroid(self):
        for freq, band in ((100, 'low'), (1000, 'mid'), (8000, 'high')):
            shares = measure.band_shares(tone(freq, 0.3))
            self.assertGreater(shares[band], 0.99, (freq, shares))
        self.assertAlmostEqual(measure.centroid_hz(tone(1000, 0.3)), 1000, delta=10)

    def test_every_moment_counts_by_its_energy(self):
        """A sound's spectrum is its energy spectrum: a short bright onset holding half the energy is half the
        spectrum, however a window over the whole sound would weight its start."""
        onset = 0.775 * tone(1000, 0.02)              # 0.775^2 x 0.02 s = 0.2^2 x 0.3 s: equal energy
        body = 0.2 * tone(100, 0.3)
        x = np.concatenate([onset, body])
        self.assertAlmostEqual(measure.band_shares(x)['mid'], 0.5, delta=0.05)
        self.assertAlmostEqual(traits.measures(x)['bands']['mid'], 0.5, delta=0.05)

    def test_the_centroid_counts_only_audible_frequencies(self):
        """An offset or a rumble below 20 Hz, common in recordings, is inaudible and must not pull the centroid
        down (the band shares leave it out too)."""
        x = tone(1000, 0.3)
        n = np.arange(len(x)) / 48000
        for added in (0.3, 0.3 * np.sin(2 * np.pi * 8 * n)):
            self.assertAlmostEqual(measure.centroid_hz(x + added), 1000, delta=10)


class Stereo(unittest.TestCase):
    def test_correlation_and_mono_sum(self):
        left = tone(500, 0.2)
        same = measure.stereo(np.stack([left, left], axis=1))
        self.assertAlmostEqual(same['correlation'], 1.0, delta=1e-6)
        self.assertAlmostEqual(same['mono_sum_change_db'], 0.0, delta=0.01)
        opposite = measure.stereo(np.stack([left, -left], axis=1))
        self.assertAlmostEqual(opposite['correlation'], -1.0, delta=1e-6)
        self.assertLess(opposite['mono_sum_change_db'], -60)


if __name__ == '__main__':
    unittest.main()
