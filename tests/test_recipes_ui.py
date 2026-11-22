"""The twelve UI recipes: registered with their rules, pitched content inside 150 Hz - 5 kHz at every parameter's
limits, notes long enough, and the example project rendering without a single flag."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import curves, project, recipes, render, synth

UI = ('ui.tick', 'ui.hover', 'ui.select', 'ui.confirm', 'ui.back', 'ui.error', 'ui.toggle_on', 'ui.toggle_off',
      'ui.open', 'ui.close', 'ui.notify', 'ui.success')
EXAMPLE = helpers.EXAMPLES / 'lantern-market-ui.json'


def settings(recipe):
    """The defaults, then each parameter at its low and its high end with the others at their defaults."""
    yield recipe.defaults()
    for p in recipe.params:
        for value in (p.lo, p.hi):
            params = recipe.defaults()
            params[p.name] = float(value)
            yield params


def pitched_values(layer):
    if layer['source']['type'] not in ('osc', 'fm'):
        return []
    return [v for _, v, _ in curves.normalize(layer['pitch'])]


class Recipes(unittest.TestCase):
    def test_all_twelve_are_registered_with_rules(self):
        names = [r.name for r in recipes.all_recipes() if r.category == 'ui']
        for name in UI:
            self.assertIn(name, names)
            recipe = recipes.get(name)
            self.assertTrue(recipe.rules, name)
            for rule, source in recipe.rules:
                self.assertTrue(rule and source, name)

    def test_pitched_content_stays_between_150_hz_and_5_khz(self):
        for name in UI:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(params)
                for layer in spec['layers']:
                    for value in pitched_values(layer):
                        self.assertGreaterEqual(value, 150.0 - 1e-6, (name, params))
                        self.assertLessEqual(value, 5000.0 + 1e-6, (name, params))

    def test_every_setting_builds_a_valid_sound_that_renders(self):
        for name in UI:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(params)
                sound = {'id': 'x', 'category': 'ui', 'layers': spec['layers']}
                if spec.get('chain'):
                    sound['chain'] = spec['chain']
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})
                audio = synth.render_sound(spec, seed=1, channels=1)
                self.assertTrue(np.isfinite(audio).all(), (name, params))
                self.assertGreater(float(np.max(np.abs(audio))), 0.01, (name, params))

    def test_tonal_notes_are_at_least_30_ms(self):
        for name in UI:
            recipe = recipes.get(name)
            for params in settings(recipe):
                for layer in recipe.build(params)['layers']:
                    if layer['source']['type'] in ('osc', 'fm'):
                        self.assertGreaterEqual(layer['length'], 0.03 - 1e-9, (name, params))

    def test_figures_of_more_than_two_notes_keep_notes_of_82_5_ms(self):
        self.assertGreaterEqual(recipes.get('ui.success').param('note_length').lo, 0.0825)


class Example(unittest.TestCase):
    def test_the_example_project_renders_with_no_flags(self):
        proj = project.load(EXAMPLE)
        self.assertEqual(sorted(s['recipe'] for s in proj['sounds']), sorted(UI))
        out = helpers.scratch('example_ui') / 'render'
        report = render.render(proj, out)
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))
        self.assertEqual(report['files'], 12 * proj['variants'])


if __name__ == '__main__':
    unittest.main()
