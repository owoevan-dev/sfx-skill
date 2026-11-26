"""Noise colours, the state-variable filter (swept and static paths agree), and the fixed-chain effects."""
import math
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import filters, fx, noise, project, synth

RATE = 48000


def octave_slope_db(x, lo=200.0, hi=6400.0):
    """Power slope in dB per octave between lo and hi, from averaged band powers."""
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    centres = lo * 2 ** np.arange(0, int(np.log2(hi / lo)) + 1)
    powers = [np.mean(spec[(freqs >= c / 2 ** 0.5) & (freqs < c * 2 ** 0.5)]) for c in centres]
    return np.polyfit(np.log2(centres), 10 * np.log10(powers), 1)[0]


class Noise(unittest.TestCase):
    def test_white_noise_level_and_seed(self):
        a = noise.make('white', RATE, np.random.default_rng(1))
        b = noise.make('white', RATE, np.random.default_rng(1))
        np.testing.assert_array_equal(a, b)
        self.assertAlmostEqual(float(np.sqrt(np.mean(a ** 2))), noise.RMS, delta=0.005)

    def test_pink_and_brown_slopes(self):
        pink = noise.make('pink', 4 * RATE, np.random.default_rng(2))
        brown = noise.make('brown', 4 * RATE, np.random.default_rng(3))
        # mean power per hertz in each octave falls 3 dB per octave for pink and 6 for brown
        self.assertAlmostEqual(octave_slope_db(pink), -3.0, delta=0.7)
        self.assertAlmostEqual(octave_slope_db(brown), -6.0, delta=0.7)
        for x in (pink, brown):
            self.assertAlmostEqual(float(np.sqrt(np.mean(x ** 2))), noise.RMS, delta=0.005)
            self.assertLess(abs(float(np.mean(x))), 0.01)


class Filters(unittest.TestCase):
    def gain_db(self, kind, cutoff, q, freq):
        t = np.arange(RATE) / RATE
        x = np.sin(2 * np.pi * freq * t)
        y = filters.filter(x, kind, cutoff, q)
        return 20 * math.log10(np.sqrt(np.mean(y[RATE // 2:] ** 2)) / np.sqrt(np.mean(x[RATE // 2:] ** 2)))

    def test_lowpass_response_matches_the_two_pole_prototype(self):
        # the analog two-pole low-pass, mapped by the bilinear transform with the cut-off pre-warped
        fc, q = 1000.0, 0.7071
        for freq in (100, 1000, 8000):
            r = math.tan(math.pi * freq / RATE) / math.tan(math.pi * fc / RATE)
            expected = -10 * math.log10((1 - r * r) ** 2 + (r / q) ** 2)
            self.assertAlmostEqual(self.gain_db('lowpass', fc, q, freq), expected, delta=0.05)

    def test_bandpass_peaks_at_0_db_and_highpass_mirrors(self):
        self.assertAlmostEqual(self.gain_db('bandpass', 2000.0, 4.0, 2000), 0.0, delta=0.05)
        self.assertAlmostEqual(self.gain_db('highpass', 1000.0, 0.7071, 10000), 0.0, delta=0.1)

    def test_swept_and_static_paths_agree_for_a_constant_cutoff(self):
        rng = np.random.default_rng(4)
        x = rng.standard_normal(9600)
        for kind in ('lowpass', 'highpass', 'bandpass', 'notch'):
            static = filters.filter(x, kind, 1500.0, 2.0)
            swept = filters.svf(x, np.full(len(x), 1500.0), 2.0, kind)
            self.assertLess(np.max(np.abs(static - swept)), 1e-6, kind)

    def test_fast_resonant_sweeps_stay_stable(self):
        rng = np.random.default_rng(5)
        x = rng.standard_normal(RATE)
        t = np.arange(RATE) / RATE
        cutoff = 20.0 * (1000.0 ** (0.5 + 0.5 * np.sin(2 * np.pi * 50 * t)))  # 20 Hz to 20 kHz, 50 times a second
        y = filters.svf(x, cutoff, 30.0, 'lowpass')
        self.assertTrue(np.isfinite(y).all())
        self.assertLess(np.max(np.abs(y)), 200.0)


class Effects(unittest.TestCase):
    def test_drive_of_zero_leaves_the_signal_and_drive_keeps_full_scale(self):
        t = np.arange(4800) / RATE
        x = 0.8 * np.sin(2 * np.pi * 300 * t)
        np.testing.assert_allclose(fx.drive(x, 0.0), x, atol=1e-12)
        y = fx.drive(x, 1.0)
        self.assertEqual(len(y), len(x))
        self.assertLessEqual(np.max(np.abs(y)), 1.02)
        self.assertGreater(np.max(np.abs(y)), 0.95)

    def test_delay_echoes_land_on_their_samples(self):
        x = np.zeros(4800)
        x[0] = 1.0
        y = fx.delay(x, time_s=0.01, feedback=0.5, mix=1.0)
        self.assertAlmostEqual(y[0], 1.0, places=6)
        self.assertAlmostEqual(y[480], 1.0, places=4)
        self.assertAlmostEqual(y[960], 0.5, places=4)
        self.assertAlmostEqual(y[1440], 0.25, places=4)
        self.assertGreater(len(y), 4800)  # the tail is kept

    def test_room_decays_by_60_db_near_its_decay_time(self):
        x = np.zeros(RATE)
        x[0] = 1.0
        y = fx.room(x, size=1.0, decay_s=0.5, mix=1.0)
        wet = y.copy()
        wet[0] = 0
        energy = np.cumsum(wet[::-1] ** 2)[::-1]
        level = 10 * np.log10(energy / energy[0] + 1e-30)
        t60 = np.argmax(level < -60) / RATE
        self.assertAlmostEqual(t60, 0.5, delta=0.15)
        self.assertTrue(np.isfinite(y).all())

    def test_room_has_the_mode_density_schroeder_asks_for(self):
        """Schroeder and Logan's criterion as Smith gives it (Physical Audio Signal Processing, Mode Density
        Requirement): the network's total delay at least 0.15 T60 Fs, here for decays up to 2 s at size 1."""
        self.assertGreaterEqual(sum(fx.room_delays(1.0)), 0.15 * 2.0 * RATE)

    def test_room_channels_are_decorrelated(self):
        x = np.zeros((RATE, 2))
        x[0] = 1.0
        y = fx.room(x, size=1.0, decay_s=0.8, mix=1.0)
        wet = y[1:]
        corr = np.corrcoef(wet[:, 0], wet[:, 1])[0, 1]
        self.assertLess(abs(corr), 0.2)

    def test_fades_are_sample_exact(self):
        y = fx.fade(np.ones(4800), fade_in_s=0.001, fade_out_s=0.002)
        self.assertEqual(y[0], 0.0)
        self.assertEqual(y[48], 1.0)
        self.assertEqual(y[-1], 0.0)
        self.assertLess(y[-50], 1.0)
        self.assertEqual(y[4800 - 97], 1.0)

    def test_eq_low_cut(self):
        t = np.arange(RATE) / RATE
        x = np.sin(2 * np.pi * 50 * t)
        y = fx.eq(x, low_cut_hz=200.0)
        ratio = np.sqrt(np.mean(y[RATE // 2:] ** 2) / np.mean(x[RATE // 2:] ** 2))
        self.assertLess(20 * math.log10(ratio), -20)


def change_db(f, **shelves):
    """How many dB eq moves a steady sine of f Hz (whole periods of its second half, after the filter has settled)."""
    x = np.sin(2 * np.pi * f * np.arange(RATE) / RATE)
    y = fx.eq(x, **shelves)
    return 10 * math.log10(np.mean(y[RATE // 2:] ** 2) / np.mean(x[RATE // 2:] ** 2))


def band_db(y, low, high):
    y = y.mean(axis=1) if y.ndim == 2 else y
    power = np.abs(np.fft.rfft(y)) ** 2
    hz = np.fft.rfftfreq(len(y), 1 / RATE)
    return 10 * math.log10(power[(hz >= low) & (hz < high)].sum())


class EqShelves(unittest.TestCase):
    """eq's low and high shelves (the Audio EQ Cookbook's, slope 1): everything below or above hz moves by gain_db, half
    of it at hz."""
    LOW = {'low_shelf': {'hz': 200.0, 'gain_db': -6.0}}
    HIGH = {'high_shelf': {'hz': 2000.0, 'gain_db': 6.0}}

    def test_a_shelf_moves_its_side_by_its_gain_and_half_of_it_at_its_frequency(self):
        self.assertAlmostEqual(change_db(50, **self.LOW), -6.0, delta=0.1)
        self.assertAlmostEqual(change_db(200, **self.LOW), -3.0, delta=0.02)
        self.assertAlmostEqual(change_db(5000, **self.LOW), 0.0, delta=0.02)
        self.assertAlmostEqual(change_db(8000, **self.HIGH), 6.0, delta=0.1)
        self.assertAlmostEqual(change_db(2000, **self.HIGH), 3.0, delta=0.02)
        self.assertAlmostEqual(change_db(50, **self.HIGH), 0.0, delta=0.02)

    def test_both_shelves_apply_together(self):
        self.assertAlmostEqual(change_db(50, **self.LOW, **self.HIGH), -6.0, delta=0.1)
        self.assertAlmostEqual(change_db(8000, **self.LOW, **self.HIGH), 6.0, delta=0.1)

    def test_each_shelf_cuts_and_boosts_as_asked(self):
        for name, hz, far in (('low_shelf', 200.0, 50), ('high_shelf', 2000.0, 8000)):
            for gain in (-6.0, 6.0):
                with self.subTest(shelf=name, gain=gain):
                    self.assertAlmostEqual(change_db(far, **{name: {'hz': hz, 'gain_db': gain}}), gain, delta=0.1)
                    self.assertAlmostEqual(change_db(hz, **{name: {'hz': hz, 'gain_db': gain}}), gain / 2, delta=0.02)

    def test_a_sound_chain_with_both_shelves_moves_both_ends(self):
        spec = {'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.5}]}
        plain = synth.render_sound(spec, seed=1)
        shelved = synth.render_sound(dict(spec, chain={'eq': {'low_shelf': {'hz': 300.0, 'gain_db': -6.0},
                                                              'high_shelf': {'hz': 3000.0, 'gain_db': 6.0}}}), seed=1)

        def moved(lo, hi):                                             # against the middle, which both shelves leave
            return (band_db(shelved, lo, hi) - band_db(plain, lo, hi)) - (band_db(shelved, 800, 1200) - band_db(plain, 800, 1200))

        self.assertAlmostEqual(moved(20, 60), -6.0, delta=0.7)
        self.assertAlmostEqual(moved(12000, 20000), 6.0, delta=0.7)

    def test_a_sound_chain_passes_its_shelves_to_eq(self):
        spec = {'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.5}]}
        plain = synth.render_sound(spec, seed=1)
        for name, hz, gain, inside, outside in (('low_shelf', 300.0, -6.0, (20, 60), (3000, 10000)),
                                                ('high_shelf', 3000.0, 6.0, (12000, 20000), (20, 200))):
            shelved = synth.render_sound(dict(spec, chain={'eq': {name: {'hz': hz, 'gain_db': gain}}}), seed=1)
            moved = ((band_db(shelved, *inside) - band_db(plain, *inside))
                     - (band_db(shelved, *outside) - band_db(plain, *outside)))
            self.assertAlmostEqual(moved, gain, delta=0.5, msg=name)

    def test_shelf_values_are_checked(self):
        sound = {'id': 's', 'category': 'ui', 'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.3}]}

        def validated(eq):
            return project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [dict(sound, chain={'eq': eq})]})

        validated({'low_shelf': {'hz': 200, 'gain_db': -3}, 'high_shelf': {'hz': 4000, 'gain_db': 2}})
        for eq in ({'low_shelf': {'hz': 200}}, {'high_shelf': {'gain_db': 2}}, {'low_shelf': {'hz': 5, 'gain_db': -3}},
                   {'high_shelf': {'hz': 4000, 'gain_db': 30}}, {'low_shelf': {'hz': 200, 'gain_db': -3, 'q': 1}},
                   {'high_shelf': 2000}):
            with self.assertRaises(project.ProjectError, msg=str(eq)):
                validated(eq)


if __name__ == '__main__':
    unittest.main()
