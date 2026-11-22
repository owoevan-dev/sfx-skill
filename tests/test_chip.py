"""The chip source: NES-like pulse, 32-step triangle and shift-register noise, stepped parameters, the console filter."""
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import chip, curves, synth

RATE = 48000


def spectrum(x):
    return np.abs(np.fft.rfft(x)) * 2 / len(x)


class Periods(unittest.TestCase):
    def test_pulse_pitch_lands_on_a_timer_period(self):
        # f = f_cpu / (16 (t + 1)); 440 Hz -> t = 253 -> 440.40 Hz
        self.assertAlmostEqual(chip.quantize_pitch(440.0, 'pulse'), 1789773.0 / (16 * 254), places=6)
        # f = f_cpu / (32 (t + 1)); 440 Hz -> t = round(127.1 - 1) = 126 -> 440.40 Hz
        self.assertAlmostEqual(chip.quantize_pitch(440.0, 'triangle'), 1789773.0 / (32 * 127), places=6)

    def test_timers_stop_at_their_longest_period(self):
        # 11 bits: the lowest pulse is f_cpu / (16 x 2048), about 54.6 Hz; the lowest triangle half that
        self.assertAlmostEqual(chip.quantize_pitch(20.0, 'pulse'), chip.F_CPU / (16 * 2048), places=6)
        self.assertAlmostEqual(chip.quantize_pitch(5.0, 'triangle'), chip.F_CPU / (32 * 2048), places=6)

    def test_noise_rate_snaps_to_the_ntsc_table(self):
        self.assertAlmostEqual(chip.quantize_pitch(30000.0, 'noise'), 1789773.0 / 64, places=3)
        self.assertEqual(len(chip.NOISE_PERIODS), 16)


class ShiftRegister(unittest.TestCase):
    def test_long_mode_repeats_after_32767_steps(self):
        seq = chip.lfsr_sequence('long')
        self.assertEqual(len(seq), 32767)

    def test_short_mode_repeats_after_93_or_31_steps(self):
        self.assertIn(len(chip.lfsr_sequence('short')), (93, 31))

    def test_first_steps_follow_the_feedback_rule(self):
        # from 1: feedback = bit0 xor bit1 = 1, shift right, bit 14 set -> 0x4000; the output bit is bit 0 before the shift
        seq = chip.lfsr_sequence('long')
        self.assertEqual(list(seq[:3]), [1, 0, 0])


class Waves(unittest.TestCase):
    def test_triangle_has_its_32_step_images(self):
        x = chip.wave('triangle', np.full(RATE, 440.0), quantize=False)
        s = spectrum(x)
        self.assertGreater(s[440], 0.7)
        # the staircase of 32 steps per period puts components around 31 and 33 times the fundamental
        self.assertGreater(s[31 * 440], 1e-3)
        self.assertGreater(s[33 * 440], 1e-3)
        self.assertLess(s[2 * 440], 1e-3)

    def test_pulse_duty_25_drops_every_fourth_harmonic(self):
        s = spectrum(chip.wave('pulse', np.full(RATE, 500.0), duty=0.25, quantize=False))
        self.assertLess(s[2000] / s[500], 1e-3)

    def test_noise_rate_shapes_its_spectrum(self):
        rng = np.random.default_rng(1)
        high = chip.wave('noise', np.full(RATE, 447443.2), rng=rng)
        low = chip.wave('noise', np.full(RATE, 2348.8), rng=rng)

        def share_above(x, hz):
            p = np.abs(np.fft.rfft(x)) ** 2
            f = np.fft.rfftfreq(len(x), 1 / RATE)
            return p[f > hz].sum() / p.sum()
        self.assertGreater(share_above(high, 5000), 0.5)
        self.assertLess(share_above(low, 5000), 0.1)
        self.assertTrue(np.isfinite(high).all() and np.isfinite(low).all())

    def test_noise_variants_start_at_different_places(self):
        a = chip.wave('noise', np.full(4800, 50000.0), rng=np.random.default_rng(1))
        b = chip.wave('noise', np.full(4800, 50000.0), rng=np.random.default_rng(2))
        self.assertFalse(np.array_equal(a, b))


class Stepping(unittest.TestCase):
    def test_parameters_change_only_on_frame_boundaries(self):
        values = chip.stepped([[0, 100.0, 'exp'], [0.1, 1000.0]], 4800, step_hz=60, default='exp')
        changes = np.nonzero(np.diff(values))[0] + 1
        self.assertTrue(all(c % 800 == 0 for c in changes), changes[:5])

    def test_volume_takes_sixteen_levels(self):
        levels = chip.quantize_levels(np.linspace(0, 1, 1000), 16)
        self.assertEqual(len(np.unique(levels)), 16)


class Layer(unittest.TestCase):
    def test_a_chip_layer_renders_with_stepped_volume(self):
        layer = {'source': {'type': 'chip', 'wave': 'pulse', 'duty': 0.5}, 'start': 0.0, 'length': 0.2,
                 'pitch': [[0, 300.0, 'exp'], [0.15, 900.0]], 'env': [[0, 1.0], [0.2, 0.0]]}
        x = synth.render_layer(layer, seed=1, index=0)
        self.assertTrue(np.isfinite(x).all())
        self.assertGreater(np.max(np.abs(x)), 0.5)

    def test_console_filter_follows_first_order_sections(self):
        # first-order sections mapped by the bilinear transform: an analog corner fc answers at the digital frequency
        # f as the prototype does at r = tan(pi f / fs) / tan(pi fc / fs)
        t = np.arange(RATE) / RATE
        for freq in (100.0, 1000.0, 16000.0):
            x = np.sin(2 * np.pi * freq * t)
            y = chip.console(x, 'nes')
            gain = 20 * np.log10(np.sqrt(np.mean(y[RATE // 2:] ** 2) / np.mean(x[RATE // 2:] ** 2)))
            r = lambda fc: np.tan(np.pi * freq / RATE) / np.tan(np.pi * fc / RATE)
            hp = lambda fc: 10 * np.log10(r(fc) ** 2 / (1 + r(fc) ** 2))
            lp = 10 * np.log10(1 / (1 + r(14000.0) ** 2))
            self.assertAlmostEqual(gain, hp(90.0) + hp(440.0) + lp, delta=0.05, msg=freq)


if __name__ == '__main__':
    unittest.main()
