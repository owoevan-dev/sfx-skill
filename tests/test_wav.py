"""WAV writing (ours) read back by the reader copied from music-production: sample formats, dither, loops."""
import struct
import unittest

import numpy as np

import helpers
from sfxlib import wav


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(self.id().rsplit('.', 1)[-1])
        t = np.arange(4800) / 48000
        self.signal = 0.5 * np.sin(2 * np.pi * 440 * t)

    def test_24_bit_round_trip_within_one_step(self):
        path = self.base / 'a.wav'
        wav.write(path, self.signal, bits=24, dither=False)
        data, info = wav.read(path)
        self.assertEqual((info['rate'], info['bits'], info['channels'], info['frames']), (48000, 24, 1, 4800))
        self.assertLessEqual(np.max(np.abs(data[:, 0] - self.signal)), 1 / 2 ** 23)

    def test_float_round_trip_is_exact_to_float32(self):
        path = self.base / 'f.wav'
        wav.write(path, self.signal, bits=32)
        data, info = wav.read(path)
        self.assertEqual(info['tag'], 3)
        np.testing.assert_array_equal(data[:, 0], self.signal.astype(np.float32))

    def test_stereo_keeps_channel_order(self):
        path = self.base / 's.wav'
        stereo = np.stack([self.signal, -0.25 * self.signal], axis=1)
        wav.write(path, stereo, bits=24, dither=False)
        data, info = wav.read(path)
        self.assertEqual(info['channels'], 2)
        self.assertLessEqual(np.max(np.abs(data - stereo)), 1 / 2 ** 23)

    def test_values_beyond_full_scale_are_refused(self):
        with self.assertRaises(ValueError):
            wav.write(self.base / 'x.wav', np.array([0.0, 1.5, 0.0]), bits=16)
        with self.assertRaises(ValueError):
            wav.write(self.base / 'y.wav', np.array([0.0, np.nan]), bits=16)


class Dither(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(self.id().rsplit('.', 1)[-1])

    def test_tpdf_dither_on_silence_stays_within_one_step_with_half_step_rms(self):
        path = self.base / 'd.wav'
        wav.write(path, np.zeros(100000), bits=16, dither=True, seed=7)
        data, _ = wav.read(path)
        steps = np.round(data[:, 0] * 32768).astype(int)
        self.assertTrue(set(np.unique(steps)) <= {-1, 0, 1})
        self.assertAlmostEqual(float(np.mean(steps)), 0.0, delta=0.02)
        # rounding of triangular dither of two steps peak to peak: error variance 1/6 + 1/12 = 1/4 step squared
        self.assertAlmostEqual(float(np.sqrt(np.mean(steps.astype(float) ** 2))), 0.5, delta=0.03)

    def test_dither_is_reproducible_by_seed(self):
        a, b = self.base / 'a.wav', self.base / 'b.wav'
        sig = 0.1 * np.sin(np.arange(2000) * 0.05)
        wav.write(a, sig, bits=16, dither=True, seed=3)
        wav.write(b, sig, bits=16, dither=True, seed=3)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_without_dither_values_round_to_the_nearest_step(self):
        path = self.base / 'r.wav'
        wav.write(path, np.array([0.3 / 32768, 0.7 / 32768, -0.7 / 32768]), bits=16, dither=False)
        data, _ = wav.read(path)
        np.testing.assert_array_equal(np.round(data[:, 0] * 32768), [0, 1, -1])


class Loops(unittest.TestCase):
    def test_smpl_loop_is_read_back_as_written(self):
        base = helpers.scratch('loops')
        path = base / 'loop.wav'
        wav.write(path, np.zeros(48000), bits=16, dither=False, loop=(1000, 47000))
        self.assertEqual(wav.info(path)['loops'], [(1000, 47000)])
        raw = path.read_bytes()
        at = raw.index(b'smpl')
        size = struct.unpack_from('<I', raw, at + 4)[0]
        self.assertEqual(size, 36 + 24)
        # the chunk names the last frame played: end - 1
        self.assertEqual(struct.unpack_from('<6I', raw, at + 8 + 36)[2:4], (1000, 46999))


if __name__ == '__main__':
    unittest.main()
