"""Band-limited oscillators and FM against their Fourier series and Bessel coefficients."""
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import osc

RATE = 48000
N = RATE  # one second: FFT bins fall on whole hertz


def spectrum(x):
    """Amplitude per 1 Hz bin of a one-second signal (a sine of amplitude a reads a)."""
    return np.abs(np.fft.rfft(x)) * 2 / len(x)


def db(v):
    return 20 * np.log10(max(v, 1e-12))


class Waves(unittest.TestCase):
    def test_sine_has_one_component(self):
        s = spectrum(osc.wave('sine', np.full(N, 1000.0)))
        self.assertAlmostEqual(s[1000], 1.0, places=6)
        s[1000] = 0
        self.assertLess(np.max(s), 1e-6)

    def test_square_has_odd_harmonics_and_nothing_above_the_limit(self):
        f = 3000.0
        s = spectrum(osc.wave('square', np.full(N, f)))
        for k in (1, 3, 5):
            self.assertAlmostEqual(s[int(k * f)], 4 / (np.pi * k), delta=1e-4)
        self.assertLess(s[6000], 1e-6)                 # no even harmonics
        # 21 kHz would be harmonic 7, past the 20 kHz limit; a naive square would also fold 27 kHz onto 21 kHz
        self.assertLess(db(s[21000]), -100)
        harmonics = [int(k * f) for k in range(1, 8)]
        rest = np.delete(s, harmonics)
        self.assertLess(db(np.max(rest)), -100)       # no aliased components anywhere

    def test_saw_and_triangle_follow_their_series(self):
        f = 1000.0
        saw = spectrum(osc.wave('saw', np.full(N, f)))
        tri = spectrum(osc.wave('triangle', np.full(N, f)))
        for k in (1, 2, 5):
            self.assertAlmostEqual(saw[int(k * f)], 2 / (np.pi * k), delta=1e-4)
        for k in (1, 3, 5):
            self.assertAlmostEqual(tri[int(k * f)], 8 / (np.pi ** 2 * k ** 2), delta=1e-4)
        self.assertLess(tri[2000], 1e-6)

    def test_pulse_duty_removes_every_fourth_harmonic_at_25_percent(self):
        f = 500.0
        s = spectrum(osc.wave('pulse', np.full(N, f), duty=0.25))
        self.assertLess(db(s[2000]) - db(s[500]), -80)
        self.assertGreater(s[1000], 0.1)

    def test_partials_fade_out_near_the_limit_during_a_sweep(self):
        freq = np.geomspace(200.0, 12000.0, N)
        x = osc.wave('saw', freq)
        self.assertTrue(np.isfinite(x).all())
        # the gain of a partial is 1 well below the limit and 0 above it
        self.assertEqual(osc.partial_gain(np.array([10000.0, 19000.0, 20500.0]))[0], 1.0)
        self.assertEqual(osc.partial_gain(np.array([20500.0]))[0], 0.0)

    def test_a_sweep_has_no_folded_partials(self):
        # a saw rising from 5 to 10 kHz: in the last 2048 samples the fundamental sweeps about 9.79-10 kHz and the
        # second partial 19.57-20 kHz (fading out); a partial left in past the Nyquist frequency would fold back
        # (the third, at 29.4-30 kHz, to 18.0-18.6 kHz; the fourth to about 8.0-8.8 kHz)
        freq = np.linspace(5000.0, 10000.0, N)
        x = osc.wave('saw', freq)
        size = 2048
        frame = x[-size:] * np.hanning(size)
        s = np.abs(np.fft.rfft(frame))
        f = np.fft.rfftfreq(size, 1 / RATE)
        lo, hi = freq[-size], freq[-1]
        near = np.zeros(len(f), dtype=bool)
        for k in (1, 2):
            near |= (f > k * lo - 300) & (f < k * hi + 300)
        self.assertLess(db(np.max(s[~near]) / np.max(s)), -80)

    def test_frequency_follows_a_curve_through_the_phase(self):
        x = osc.wave('sine', np.full(N, 440.0), phase0_deg=90)
        self.assertAlmostEqual(x[0], 1.0, places=9)
        crossings = np.sum((x[:-1] < 0) & (x[1:] >= 0))
        self.assertIn(crossings, (439, 440))

    def test_unknown_wave_is_an_error(self):
        with self.assertRaises(ValueError):
            osc.wave('circle', np.full(10, 100.0))


class FM(unittest.TestCase):
    def test_sidebands_follow_bessel_coefficients(self):
        # carrier 6 kHz, modulator 1 kHz, index 1: J0 0.7652, J1 0.4401, J2 0.1149, J3 0.0196
        s = spectrum(osc.fm(np.full(N, 6000.0), ratio=1 / 6, index=1.0))
        for k, j in ((0, 0.7652), (1, 0.4401), (2, 0.1149), (3, 0.0196)):
            self.assertAlmostEqual(s[6000 + 1000 * k], j, delta=0.002)
            self.assertAlmostEqual(s[6000 - 1000 * k], j, delta=0.002)

    def test_zero_index_is_a_sine_and_length_is_kept(self):
        x = osc.fm(np.full(1000, 1000.0), ratio=2.0, index=0.0)
        self.assertEqual(len(x), 1000)
        ref = osc.wave('sine', np.full(1000, 1000.0))
        self.assertLess(np.max(np.abs(x[50:-50] - ref[50:-50])), 1e-3)


class Vibrato(unittest.TestCase):
    def test_vibrato_keeps_the_mean_pitch(self):
        f = osc.vibrato(np.full(N, 440.0), rate_hz=6.0, depth_cents=50)
        self.assertAlmostEqual(float(np.exp(np.mean(np.log(f)))), 440.0, delta=0.5)
        self.assertAlmostEqual(float(np.max(f)), 440 * 2 ** (50 / 1200), delta=0.05)


if __name__ == '__main__':
    unittest.main()
