"""The eight retro recipes on the chip voices: registered with their rules, valid at every parameter's limits, inside
the hardware's ranges, and the example project rendering without a flag. Also the validator's chip checks."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import chip, curves, project, recipes, render, synth
from test_recipes_ui import settings

RETRO = ('retro.jump', 'retro.coin', 'retro.powerup', 'retro.shoot', 'retro.hit', 'retro.explosion', 'retro.blip',
         'retro.extra_life')
EXAMPLE = helpers.EXAMPLES / 'pocket-arcade-retro.json'


class Recipes(unittest.TestCase):
    def test_all_eight_are_registered_with_rules(self):
        for name in RETRO:
            recipe = recipes.get(name)
            self.assertEqual(recipe.category, 'retro')
            self.assertTrue(recipe.rules, name)

    def test_every_setting_is_valid_inside_the_hardware_ranges_and_renders(self):
        for name in RETRO:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(params)
                sound = {'id': 'x', 'category': 'retro', 'layers': spec['layers']}
                if spec.get('chain'):
                    sound['chain'] = spec['chain']
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})
                for layer in spec['layers']:
                    src = layer['source']
                    self.assertEqual(src['type'], 'chip', name)
                    self.assertIn(src['duty'], chip.DUTIES)
                    values = [v for _, v, _ in curves.normalize(layer['pitch'])]
                    if src['wave'] == 'noise':
                        for v in values:
                            self.assertIn(round(v, 3), [round(r, 3) for r in chip.NOISE_RATES], name)
                    else:
                        self.assertTrue(all(20.0 <= v <= 12000.0 for v in values), (name, params))
                audio = synth.render_sound(spec, seed=1, channels=1)
                self.assertTrue(np.isfinite(audio).all(), (name, params))
                self.assertGreater(float(np.max(np.abs(audio))), 0.01, (name, params))


class Validator(unittest.TestCase):
    def errors(self, layer, chain=None):
        sound = {'id': 'x', 'category': 'retro', 'layers': [layer]}
        if chain:
            sound['chain'] = chain
        try:
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})
        except project.ProjectError as error:
            return error.errors
        return []

    def test_chip_fields_are_checked(self):
        base = {'source': {'type': 'chip', 'wave': 'pulse', 'duty': 0.3}, 'start': 0, 'length': 0.1, 'pitch': 440}
        self.assertTrue(any('duty' in e for e in self.errors(base)))
        noise = {'source': {'type': 'chip', 'wave': 'noise'}, 'start': 0, 'length': 0.1, 'pitch': 100}
        self.assertTrue(any('pitch' in e for e in self.errors(noise)))
        ok = {'source': {'type': 'chip', 'wave': 'noise', 'mode': 'short'}, 'start': 0, 'length': 0.1,
              'pitch': 27965.2}
        self.assertEqual(self.errors(ok), [])
        self.assertTrue(any('model' in e for e in self.errors(ok, {'console': {'model': 'snes'}})))
        self.assertTrue(any('color' in e for e in self.errors(
            {'source': {'type': 'chip', 'wave': 'pulse', 'color': 'pink'}, 'start': 0, 'length': 0.1, 'pitch': 440})))


class Example(unittest.TestCase):
    def test_the_example_project_renders_with_no_flags(self):
        proj = project.load(EXAMPLE)
        self.assertEqual(sorted(s['recipe'] for s in proj['sounds']), sorted(RETRO))
        out = helpers.scratch('example_retro') / 'render'
        render.render(proj, out)
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))


class Promises(unittest.TestCase):
    def build(self, name, **values):
        r = recipes.get(name)
        params = {p.name: p.default for p in r.params}
        params.update(values)
        return r.build(params)

    def test_a_shot_always_falls(self):
        pitch = self.build('retro.shoot', start_hz=600.0, end_hz=800.0)['layers'][0]['pitch']
        self.assertLess(pitch[-1][1], pitch[0][1])

    def test_an_explosion_always_moves_down_the_noise_rates(self):
        pitch = self.build('retro.explosion', start_index=9.0, end_index=6.0)['layers'][0]['pitch']
        rates = [point[1] for point in pitch]
        self.assertGreater(len(rates), 1)
        self.assertLess(rates[-1], rates[0])

    def test_a_triangle_has_no_volume_steps(self):
        # the hardware's triangle has no volume control: it sounds or it does not
        for name in ('retro.extra_life', 'retro.explosion'):
            for layer in self.build(name)['layers']:
                if layer['source']['wave'] == 'triangle':
                    values = {point[1] for point in layer['env']}
                    self.assertEqual(values, {1.0}, name)


if __name__ == '__main__':
    unittest.main()
