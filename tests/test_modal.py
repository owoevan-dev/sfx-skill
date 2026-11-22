"""The modal source: bar modes, material decay, contact hardness, the limit of hearing, a clean start."""
import unittest

import numpy as np

import helpers  # noqa: F401
from sfxlib import modal, synth

RATE = 48000


def peak_near(x, hz, width=0.03):
    s = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    band = (f > hz * (1 - width)) & (f < hz * (1 + width))
    return f[band][np.argmax(s[band])], s[band].max() / s.max()


class Modes(unittest.TestCase):
    def test_bar_modes_follow_two_n_plus_one_squared(self):
        self.assertAlmostEqual(modal.BAR_RATIOS[1], 25 / 9)
        self.assertAlmostEqual(modal.BAR_RATIOS[2], 49 / 9)
        x = modal.render(500.0, RATE, t_d=300.0, modes=4, hardness=1.0)
        for ratio in modal.BAR_RATIOS[:3]:
            freq, rel = peak_near(x, 500.0 * ratio)
            self.assertAlmostEqual(freq, 500.0 * ratio, delta=500.0 * ratio * 0.01)
            self.assertGreater(rel, 1e-3)

    def test_each_mode_decays_in_t_d_over_its_frequency(self):
        # one mode: after t_e = t_d / f the envelope has fallen to 1/e
        f, t_d = 1000.0, 100.0
        x = modal.render(f, RATE, t_d=t_d, modes=1, hardness=1.0)
        t_e = t_d / f
        def rms(t):
            i = int(t * RATE)
            return np.sqrt(np.mean(x[i:i + 480] ** 2))
        self.assertAlmostEqual(rms(0.02 + t_e) / rms(0.02), np.exp(-1), delta=0.02)

    def test_materials_carry_the_listening_studys_decays(self):
        self.assertAlmostEqual(modal.MATERIAL_TD['steel'], 10 ** 2.65)
        self.assertLess(modal.MATERIAL_TD['wood'], modal.MATERIAL_TD['glass'])

    def test_a_harder_contact_is_brighter(self):
        soft = modal.render(400.0, RATE // 2, t_d=100.0, modes=8, hardness=0.0)
        hard = modal.render(400.0, RATE // 2, t_d=100.0, modes=8, hardness=1.0)
        def centroid(x):
            s = np.abs(np.fft.rfft(x)) ** 2
            f = np.fft.rfftfreq(len(x), 1 / RATE)
            return (s * f).sum() / s.sum()
        self.assertGreater(centroid(hard), centroid(soft) * 1.3)

    def test_small_changes_of_pitch_and_hardness_change_the_level_a_little(self):
        # the contact pulse must have no spectral nulls for a mode to fall into (a Hann pulse had them: a glass tink's
        # variants then differed by 11 dB in loudness)
        from sfxlib import loudness
        levels = []
        for f in (2360.0, 2380.0, 2400.0):
            for h in (0.84, 0.86, 0.88, 0.89):
                levels.append(loudness.max_momentary(modal.render(f, RATE // 4, t_d=126.0, modes=5, hardness=h)))
        self.assertLess(max(levels) - min(levels), 1.5)

    def test_modes_past_20_khz_are_left_out_and_the_start_is_silent(self):
        self.assertTrue(all(f < 20000.0 for f in modal.mode_frequencies(3000.0, 12)))
        self.assertEqual(len(modal.mode_frequencies(3000.0, 12)), 3)     # 3, 8.3 and 16.3 kHz; 27 kHz left out
        x = modal.render(3000.0, RATE // 4, t_d=50.0, modes=12, hardness=1.0)
        p = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / RATE)
        self.assertLess(p[f > 20500].sum() / p.sum(), 1e-3)            # only the leakage of the highest mode
        self.assertLess(abs(x[0]), 1e-9)   # silent at the start (the FFT leaves rounding noise near 1e-16)
        self.assertLess(np.argmax(np.abs(x) > 0.01 * np.abs(x).max()), int(0.002 * RATE))


class Contact(unittest.TestCase):
    """van den Doel, Kry and Pai (2001): hardness is the duration of the force; a very hard contact is not one clean
    impulse but a burst of micro collisions at the dominant modes, within about 15 ms."""
    MODES = [500.0, 1389.0, 2722.0, 4500.0, 6722.0]

    def bumps(self, force):
        high = force > 0.02 * force.max()
        return int(np.sum(high[1:] & ~high[:-1])) + int(high[0])

    def test_a_soft_strike_is_one_pulse(self):
        self.assertEqual(self.bumps(modal.contact(self.MODES, 0.4)), 1)

    def test_a_very_hard_strike_chatters_within_fifteen_ms(self):
        force = modal.contact(self.MODES, 1.0)
        self.assertGreater(self.bumps(force), 5)
        self.assertLessEqual(np.flatnonzero(force > 0.02 * force.max())[-1] / RATE, 0.0155)

    def test_the_chatter_grows_with_hardness_and_uses_the_first_four_modes(self):
        harder = modal.contact(self.MODES, 1.0)
        hard = modal.contact(self.MODES, 0.85)
        self.assertGreater(float(np.sum(harder[48:])), float(np.sum(hard[48:])))
        without_fifth = modal.contact(self.MODES[:4], 1.0)
        np.testing.assert_allclose(without_fifth, harder)

    def test_it_reaches_the_render(self):
        plain = modal.render(500.0, RATE // 2, t_d=300.0, modes=5, hardness=0.69)
        chatter = modal.render(500.0, RATE // 2, t_d=300.0, modes=5, hardness=1.0)
        self.assertFalse(np.allclose(plain, chatter))
        self.assertLessEqual(float(np.max(np.abs(chatter))), 1.0 + 1e-9)


class Layer(unittest.TestCase):
    def test_a_modal_layer_renders_and_differs_by_seed_with_detune(self):
        layer = {'source': {'type': 'modal', 'material': 'glass', 'modes': 6, 'hardness': 0.8, 'detune_cents': 10},
                 'start': 0, 'length': 0.4, 'pitch': 2000}
        a = synth.render_layer(layer, seed=1, index=0)
        b = synth.render_layer(layer, seed=2, index=0)
        self.assertTrue(np.isfinite(a).all())
        self.assertFalse(np.allclose(a, b))



def bessel_j(m, x, terms=60):
    """J_m(x) by its power series (fine for x below about 15)."""
    import math
    return sum((-1) ** k * (x / 2) ** (2 * k + m) / (math.factorial(k) * math.factorial(k + m)) for k in range(terms))


def spectral_peaks(x, count):
    """Frequencies of the `count` strongest local maxima of x's spectrum (zero-padded to 1 s resolution or finer)."""
    n = max(len(x), 4 * 48000)
    s = np.abs(np.fft.rfft(x, n))
    f = np.fft.rfftfreq(n, 1 / 48000)
    local = np.flatnonzero((s[1:-1] > s[:-2]) & (s[1:-1] > s[2:])) + 1
    best = local[np.argsort(s[local])[::-1][:count]]
    return sorted(f[best])


class Shapes(unittest.TestCase):
    def test_the_membrane_table_holds_the_zeros_of_the_bessel_functions(self):
        orders = (0, 1, 2, 0, 3, 1, 4, 2, 0, 5, 3, 6)
        for m, z in zip(orders, modal.BESSEL_ZEROS):
            self.assertLess(abs(bessel_j(m, z)), 2e-5, (m, z))
        self.assertEqual(list(modal.BESSEL_ZEROS), sorted(modal.BESSEL_ZEROS))

    def test_a_membrane_and_a_list_of_ratios_ring_at_their_modes(self):
        for kwargs, ratios in ((dict(shape='membrane'), modal.MEMBRANE_RATIOS[:3]),
                               (dict(ratios=[1.0, 2.5, 4.1]), (1.0, 2.5, 4.1))):
            x = modal.render(200.0, 48000, t_d=300.0, modes=3, hardness=0.9, **kwargs)
            for got, want in zip(spectral_peaks(x, 3), ratios):
                self.assertAlmostEqual(got, 200.0 * want, delta=200.0 * want * 0.005)

    def test_shapes_and_ratios_are_checked(self):
        from sfxlib import project
        for src in ({'type': 'modal', 'shape': 'membrane', 'ratios': [1, 2]}, {'type': 'modal', 'ratios': [1, 3, 2]},
                    {'type': 'modal', 'ratios': [1, 80]}, {'type': 'modal', 'shape': 'plate'}):
            sound = {'id': 'x', 'category': 'impact', 'layers': [{'source': src, 'length': 0.3, 'pitch': 200.0}]}
            with self.assertRaises(project.ProjectError, msg=str(src)):
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})


if __name__ == '__main__':
    unittest.main()
