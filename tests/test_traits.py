"""A sound's traits (traits.py): the measures the agent reads in check.json, compared with nothing."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import project, render, traits

RATE = 48000


class Measures(unittest.TestCase):
    def test_a_short_click_is_short_and_peaky_and_a_held_tone_is_long_and_dense(self):
        rng = np.random.default_rng(1)
        click = np.zeros((RATE // 2, 1))
        click[:1200, 0] = rng.standard_normal(1200) * np.exp(-np.arange(1200) / 200)
        t = np.arange(RATE) / RATE
        held = (0.5 * np.sin(2 * np.pi * 440 * t))[:, None]
        c, h = traits.measures(click), traits.measures(held)
        self.assertLess(c['active_s'], 0.05)
        self.assertAlmostEqual(h['active_s'], 1.0, delta=0.01)
        self.assertGreater(c['plr_db'], h['plr_db'] + 6)
        self.assertAlmostEqual(h['centroid_hz'], 440, delta=15)
        self.assertLess(h['flatness'], 0.05)                   # a pure tone: tonal
        self.assertGreater(c['flatness'], h['flatness'])        # noise is flatter

    def test_a_digital_tone_with_empty_bins_reads_tonal(self):
        x = np.tile([0.0, 0.5, 0.0, -0.5], 12288 // 4)            # 12 kHz: every other bin holds exactly nothing
        self.assertLess(traits.measures(x)['flatness'], 0.01)

    def test_the_summary_is_the_median_of_the_variants(self):
        t = np.arange(RATE // 2) / RATE
        tones = [(0.5 * np.sin(2 * np.pi * f * t))[:, None] for f in (300, 600, 1200)]
        s = traits.summary(tones)
        self.assertAlmostEqual(s['centroid_hz'], 600, delta=20)
        self.assertEqual(set(s['bands']), {name for _, _, name in traits.BANDS})


class Report(unittest.TestCase):
    def test_check_gives_each_sound_its_traits_and_names_no_library(self):
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [
            {'id': 'p', 'category': 'impact', 'recipe': 'impact.punch'},
            {'id': 'x', 'category': 'ui', 'layers': [{'source': {'type': 'noise'}, 'length': 0.1}]}]})
        out = helpers.scratch('traits_report') / 'render'
        render.render(proj, out)
        text = (out / 'check.json').read_text(encoding='utf-8')
        sounds = {s['id']: s for s in json.loads(text)['sounds']}
        for sid in ('p', 'x'):
            tr = sounds[sid]['traits']
            for key in traits.SUMMARIZED:
                self.assertIn(key, tr)
            self.assertGreater(tr['active_s'], 0)
        self.assertNotIn('reference', sounds['p'])

    def test_a_sound_may_no_longer_name_a_reference_group(self):
        with self.assertRaises(project.ProjectError) as caught:
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                {'id': 'p', 'category': 'impact', 'recipe': 'impact.punch', 'reference': 'impact_punch'}]})
        self.assertIn('reference', '\n'.join(caught.exception.errors))


if __name__ == '__main__':
    unittest.main()
