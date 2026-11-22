"""Time stretching without a change of pitch (stretch.py): the phase-locked vocoder keeps a tone's pitch at any length
and the channels' relation, an attack can be kept unstretched, random-phase smearing makes long textures, and a
sample layer can stretch its recording."""
import unittest

import numpy as np

import helpers
import test_samples
from sfxlib import project, samples, stretch, synth

RATE = 48000


def dominant(x):
    x = np.asarray(x, dtype=np.float64)
    x = x[:, 0] if x.ndim == 2 else x
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / RATE)[int(np.argmax(spectrum))])


def tone(freq=1000.0, seconds=1.0, amp=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t))[:, None]


class PhaseLocked(unittest.TestCase):
    def test_length_changes_and_pitch_stays(self):
        x = tone(1000, 1.0)
        for factor in (0.5, 2.0, 4.0):
            y = stretch.stretch(x, factor)
            self.assertAlmostEqual(len(y), int(round(len(x) * factor)), delta=1)
            mid = y[len(y) // 4:3 * len(y) // 4]
            self.assertAlmostEqual(dominant(mid), 1000, delta=12, msg=factor)
            self.assertAlmostEqual(float(np.sqrt(np.mean(mid ** 2))), 0.5 / np.sqrt(2), delta=0.06, msg=factor)

    def test_identical_channels_stay_identical_and_a_level_difference_stays(self):
        x = tone(440, 0.8)
        st = np.hstack([x, 0.5 * x])
        y = stretch.stretch(st, 2.5)
        np.testing.assert_allclose(y[:, 1], 0.5 * y[:, 0], atol=1e-6)

    def test_an_attack_can_be_kept_unstretched(self):
        rng = np.random.default_rng(3)
        x = np.zeros((RATE, 1))
        x[:960, 0] = rng.standard_normal(960) * np.exp(-np.arange(960) / 150)   # a 20 ms click
        x[:, 0] += 0.2 * np.sin(2 * np.pi * 300 * np.arange(RATE) / RATE) * np.exp(-np.arange(RATE) / 20000)
        y = stretch.stretch(x, 3.0, keep_attack_ms=30)
        n = int(0.025 * RATE)
        np.testing.assert_allclose(y[:n, 0], x[:n, 0], atol=1e-9)     # the attack, untouched
        self.assertAlmostEqual(len(y), int(0.03 * RATE) + int(round((RATE - int(0.03 * RATE)) * 3.0)), delta=2)


    def test_long_factors_leave_no_gaps(self):
        x = tone(440, 1.0)
        hop = int(0.005 * RATE)
        for factor in (6.0, 8.0, 16.0):
            y = stretch.stretch(x, factor)[:, 0]
            mid = y[len(y) // 4:3 * len(y) // 4]
            rms = np.sqrt(np.mean(mid[:len(mid) // hop * hop].reshape(-1, hop) ** 2, axis=1))
            self.assertGreater(float(rms.min()), 0.6 * float(rms.mean()), factor)
            self.assertAlmostEqual(dominant(mid), 440, delta=6, msg=factor)

    def test_an_event_lands_at_its_stretched_time(self):
        burst = np.random.default_rng(1).standard_normal(int(0.004 * RATE))
        for method, factors, delta in (('phase-locked', (0.5, 2.0, 6.0), 0.012), ('smear', (2.0, 6.0), 0.04)):
            for factor in factors:
                x = np.zeros((RATE, 1))
                k = int(0.4 * RATE)
                x[k:k + len(burst), 0] = burst
                y = stretch.stretch(x, factor, method=method, seed=1)[:, 0] ** 2
                centre = float(np.sum(np.arange(len(y)) * y) / np.sum(y)) / RATE
                self.assertAlmostEqual(centre, (0.4 + 0.002) * factor, delta=delta, msg=(method, factor))

    def test_one_times_gives_the_recording_back(self):
        x = np.random.default_rng(2).standard_normal((RATE // 2, 2)) * 0.1
        y = stretch.stretch(x, 1.0)
        np.testing.assert_allclose(y, x, atol=1e-6)

    def test_channels_in_opposite_polarity_keep_their_pitch(self):
        x = tone(440, 0.8)
        for factor in (1.0, 2.0):
            y = stretch.stretch(np.hstack([x, -x]), factor)
            mid = y[len(y) // 4:3 * len(y) // 4]
            self.assertAlmostEqual(dominant(mid), 440, delta=4, msg=factor)
            self.assertGreater(float(np.sqrt(np.mean(mid[:, 0] ** 2))), 0.2, factor)
            np.testing.assert_allclose(y[:, 1], -y[:, 0], atol=1e-6)

    def test_two_tones_a_semitone_apart_keep_their_own_peaks(self):
        # a frame near 100 ms, as Driedger and Müller (2016) give for the phase vocoder, resolves them
        t = np.arange(RATE) / RATE
        x = (0.25 * np.sin(2 * np.pi * 440 * t) + 0.25 * np.sin(2 * np.pi * 466.16 * t))[:, None]
        y = stretch.stretch(x, 3.0)[:, 0]
        mid = y[RATE // 2:len(y) - RATE // 2]
        spectrum = np.abs(np.fft.rfft(mid * np.hanning(len(mid))))
        freqs = np.fft.rfftfreq(len(mid), 1 / RATE)

        def at(f):
            return float(spectrum[np.abs(freqs - f) < 2].max())
        self.assertLess(abs(20 * np.log10(at(440) / at(466.16))), 3.0)
        self.assertLess(20 * np.log10(at(453.08) / at(440)), -20.0)

    def test_a_kept_attack_stays_out_of_the_stretching(self):
        # what follows the attack depends only on what follows it in the recording
        rng = np.random.default_rng(5)
        x = rng.standard_normal((RATE // 2, 1)) * 0.1
        other = x.copy()
        keep = int(0.03 * RATE)
        other[:keep] = rng.standard_normal((keep, 1)) * 0.9
        for method in stretch.METHODS:
            a = stretch.stretch(x, 3.0, method, keep_attack_ms=30, seed=1)
            b = stretch.stretch(other, 3.0, method, keep_attack_ms=30, seed=1)
            np.testing.assert_array_equal(a[keep:], b[keep:])
            np.testing.assert_array_equal(b[:keep], other[:keep])

    def test_an_attack_covering_the_whole_clip_leaves_it_unchanged(self):
        x = np.random.default_rng(3).standard_normal((int(0.02 * RATE), 1)) * 0.2
        np.testing.assert_array_equal(stretch.stretch(x, 3.0, keep_attack_ms=30), x)


class Smear(unittest.TestCase):
    def test_the_strongest_shortening_skips_nothing(self):
        x = np.zeros((RATE, 1))
        x[18000, 0] = 1.0                                     # between two frames when they are a whole frame apart
        y = stretch.stretch(x, 0.25, method='smear', seed=1)
        self.assertGreater(float(np.max(np.abs(y))), 1e-4)

    def test_smearing_keeps_the_level(self):
        x = tone(800, 1.0)
        y = stretch.stretch(x, 4.0, method='smear', seed=1)
        mid = y[RATE:3 * RATE, 0]
        self.assertAlmostEqual(float(np.sqrt(np.mean(mid ** 2))), 0.5 / np.sqrt(2), delta=0.08)


    def test_smearing_stretches_keeps_the_band_and_follows_its_seed(self):
        x = tone(800, 1.0)
        y = stretch.stretch(x, 6.0, method='smear', seed=1)
        self.assertAlmostEqual(len(y), 6 * RATE, delta=1)
        self.assertAlmostEqual(dominant(y[RATE:5 * RATE]), 800, delta=20)
        again = stretch.stretch(x, 6.0, method='smear', seed=1)
        np.testing.assert_array_equal(y, again)
        other = stretch.stretch(x, 6.0, method='smear', seed=2)
        self.assertFalse(np.allclose(y, other))


class Layer(unittest.TestCase):
    def test_a_sample_layer_stretches_its_recording_and_the_fields_are_checked(self):
        folder = helpers.scratch('stretch_layer')
        samples.add(test_samples.as_folder(folder / 'test-sounds', test_samples.pack_members()), folder / 'library', licence='CC0-1.0')
        library = samples.Library(folder / 'library')
        src = {'type': 'sample', 'library': 'test-sounds', 'file': 'woodKnock_light_000'}
        plain = synth.render_sound({'layers': [{'source': src, 'length': 1.2}]}, seed=1, samples=library)
        long = synth.render_sound({'layers': [{'source': dict(src, time_stretch={'factor': 3.0}), 'length': 1.2}]},
                                  seed=1, samples=library)

        def active(x):
            e = np.abs(x[:, 0])
            return np.flatnonzero(e > 0.01 * e.max())[-1] / RATE
        self.assertGreater(active(long), 2.2 * active(plain))
        self.assertAlmostEqual(dominant(long[:int(0.6 * RATE)]), 600, delta=10)

        def errors(ts):
            try:
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                    {'id': 'x', 'category': 'impact', 'layers': [{'source': dict(src, time_stretch=ts),
                                                                  'length': 0.3}]}]})
            except project.ProjectError as error:
                return error.errors
            return []
        self.assertEqual(errors({'factor': 2}), [])
        self.assertEqual(errors({'factor': 8, 'method': 'smear'}), [])
        self.assertEqual(errors({'factor': 1.5, 'keep_attack_ms': 40}), [])
        for bad in ({'factor': 0.1}, {'factor': 40}, {'factor': 2, 'method': 'magic'}, {}, 2,
                    {'factor': 2, 'keep_attack_ms': -1}, {'factor': 2, 'speed': 1}):
            self.assertTrue(errors(bad), bad)


if __name__ == '__main__':
    unittest.main()
