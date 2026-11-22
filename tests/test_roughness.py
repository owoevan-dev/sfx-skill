"""Roughness: a layer's fast amplitude modulation (`tremolo`), and the sounds that use it to warn or unsettle. Arnal et
al. (2015): screams and alarms concentrate their modulation at about 30-150 Hz, and that roughness is what makes them
alarming; Arnal et al. (2019): it is most aversive at 40-80 Hz."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import project, recipes, render, synth

RATE = 48000


def envelope(x):
    """The amplitude envelope (the magnitude of the analytic signal)."""
    n = len(x)
    h = np.zeros(n)
    h[0] = 1
    h[1:(n + 1) // 2] = 2
    if n % 2 == 0:
        h[n // 2] = 1
    return np.abs(np.fft.ifft(np.fft.fft(x) * h))


def modulation(x):
    """(rates in Hz, power of the envelope's modulation at each)."""
    e = envelope(x)
    e = (e - e.mean()) * np.hanning(len(e))
    return np.fft.rfftfreq(len(e), 1 / RATE), np.abs(np.fft.rfft(e)) ** 2


def tone(**extra):
    return dict({'source': {'type': 'osc', 'wave': 'sine'}, 'length': 1.0, 'pitch': 1000.0,
                 'env': {'attack': 0.002, 'hold': 0.95, 'decay': 0.048}}, **extra)


class Tremolo(unittest.TestCase):
    def test_it_modulates_the_level_at_its_rate_and_depth(self):
        x = synth.render_layer(tone(tremolo={'rate_hz': 70.0, 'depth': 0.8}), seed=1, index=0)
        mid = x[RATE // 10:9 * RATE // 10]
        f, p = modulation(mid)
        keep = f > 5
        self.assertAlmostEqual(float(f[keep][int(np.argmax(p[keep]))]), 70.0, delta=1.5)
        e = envelope(mid)[480:-480]
        self.assertAlmostEqual(float(e.min() / e.max()), 0.2, delta=0.03)      # depth 0.8: down to a fifth

    def test_it_never_raises_the_level(self):
        plain = synth.render_layer(tone(), seed=1, index=0)
        rough = synth.render_layer(tone(tremolo={'rate_hz': 40.0, 'depth': 1.0}), seed=1, index=0)
        self.assertLessEqual(float(np.max(np.abs(rough))), float(np.max(np.abs(plain))) + 1e-9)

    def test_no_depth_changes_nothing(self):
        plain = synth.render_layer(tone(), seed=1, index=0)
        none = synth.render_layer(tone(tremolo={'rate_hz': 40.0, 'depth': 0.0}), seed=1, index=0)
        np.testing.assert_allclose(none, plain)

    def test_its_fields_are_checked(self):
        def errors(value):
            try:
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                    {'id': 'x', 'category': 'impact', 'layers': [tone(tremolo=value)]}]})
            except project.ProjectError as error:
                return error.errors
            return []
        self.assertEqual(errors({'rate_hz': 70, 'depth': 0.5}), [])
        for bad in ({'rate_hz': 0.01, 'depth': 0.5}, {'rate_hz': 500, 'depth': 0.5}, {'rate_hz': 70, 'depth': 1.5},
                    {'rate_hz': 70}, {'depth': 0.5}, {'rate_hz': 70, 'depth': 0.5, 'phase': 1}, 70):
            self.assertTrue(errors(bad), bad)

    def test_a_loop_bed_with_it_still_joins(self):
        bed = {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 2.0, 'pitch': 220.0,
               'tremolo': {'rate_hz': 33.3, 'depth': 0.7}}
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'drone', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'layers': [bed]}]})
        out = helpers.scratch('tremolo_loop') / 'render'
        render.render(proj, out)
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual([f for f in check['flags'] if f['kind'] == 'seam'], [])


class Recipes(unittest.TestCase):
    def test_the_error_and_the_curse_flutter_in_the_alarm_range(self):
        for name in ('ui.error', 'magic.curse'):
            recipe = recipes.get(name)
            values = {p.name: p.default for p in recipe.params}
            for roughness in (0.3, 0.6, 1.0):
                values['roughness'] = roughness
                flutters = [layer['tremolo'] for layer in recipe.build(values)['layers'] if 'tremolo' in layer]
                self.assertTrue(flutters, (name, roughness))
                for t in flutters:
                    self.assertTrue(40.0 <= t['rate_hz'] <= 80.0, (name, t))     # where it is most aversive
                    self.assertAlmostEqual(t['depth'], 0.9 * roughness)
            values['roughness'] = 0.0
            self.assertFalse(any('tremolo' in layer for layer in recipe.build(values)['layers']), name)

    def test_friendly_sounds_stay_smooth(self):
        for name in ('ui.confirm', 'magic.heal', 'magic.pickup_chime'):
            built = recipes.get(name).build({p.name: p.default for p in recipes.get(name).params})
            self.assertFalse(any('tremolo' in layer for layer in built['layers']), name)

    def test_the_rules_name_their_sources(self):
        for name in ('ui.error', 'magic.curse'):
            sources = ' '.join(source for _, source in recipes.get(name).rules)
            self.assertIn('Arnal', sources, name)
        self.assertIn('Blumstein', ' '.join(source for _, source in recipes.get('magic.curse').rules))


if __name__ == '__main__':
    unittest.main()
