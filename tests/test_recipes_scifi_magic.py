"""The sci-fi and magic recipes: registered with rules, valid and rendering at every parameter's limits, grain
patterns that follow the parameters, and both example projects rendering without a flag."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import project, recipes, render, synth
from test_recipes_ui import settings

SCIFI = ('scifi.zap', 'scifi.charge', 'scifi.shield_hit', 'scifi.teleport', 'scifi.console_beep', 'scifi.power_down',
         'scifi.explosion')
MAGIC = ('magic.sparkle', 'magic.cast', 'magic.heal', 'magic.curse', 'magic.pickup_chime')
EXAMPLES = {'scifi': helpers.EXAMPLES / 'star-courier-scifi.json',
            'magic': helpers.EXAMPLES / 'moonlit-grove-magic.json'}


class Recipes(unittest.TestCase):
    def test_registered_with_rules(self):
        for name in SCIFI + MAGIC:
            recipe = recipes.get(name)
            self.assertEqual(recipe.category, name.split('.')[0])
            self.assertTrue(recipe.rules, name)

    def test_every_setting_is_valid_and_renders(self):
        for name in SCIFI + MAGIC:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(params)
                sound = {'id': 'x', 'category': recipe.category, 'layers': spec['layers']}
                if spec.get('chain'):
                    sound['chain'] = spec['chain']
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})
                audio = synth.render_sound(spec, seed=1, channels=1)
                self.assertTrue(np.isfinite(audio).all(), (name, params))
                self.assertGreater(float(np.max(np.abs(audio))), 0.01, (name, params))

    def test_grain_patterns_follow_the_parameters_and_repeat(self):
        recipe = recipes.get('magic.sparkle')
        a = recipe.build(recipe.defaults())
        b = recipe.build(recipe.defaults())
        params = recipe.defaults()
        params['centre_hz'] *= 1.01
        c = recipe.build(params)
        starts = lambda spec: [layer['start'] for layer in spec['layers'][:-1]]
        self.assertEqual(starts(a), starts(b))
        self.assertNotEqual(starts(a), starts(c))

    def test_sparkle_answers_at_once(self):
        """The first grain starts with the sound, so a sparkle does not lag behind the event that triggers it."""
        recipe = recipes.get('magic.sparkle')
        for params in settings(recipe):
            grains = recipe.build(params)['layers'][:-1]
            self.assertEqual(min(layer['start'] for layer in grains), 0.0, params)


class Examples(unittest.TestCase):
    def test_both_examples_render_with_no_flags(self):
        for category, path in EXAMPLES.items():
            proj = project.load(path)
            expected = SCIFI if category == 'scifi' else MAGIC
            self.assertEqual(sorted(s['recipe'] for s in proj['sounds']), sorted(expected))
            out = helpers.scratch(f'example_{category}') / 'render'
            render.render(proj, out)
            check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
            self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))


class Promises(unittest.TestCase):
    def build(self, name, **values):
        r = recipes.get(name)
        params = {p.name: p.default for p in r.params}
        params.update(values)
        return r.build(params)

    def test_a_zap_always_dives(self):
        for layer in self.build('scifi.zap', start_hz=800.0, end_hz=1500.0)['layers'][:3]:
            self.assertLess(layer['pitch'][-1][1], layer['pitch'][0][1])

    def test_rules_claim_no_more_than_their_sources(self):
        everything = ' '.join(text for r in recipes.all_recipes() for text, _ in r.rules)
        self.assertNotIn('inharmonic ring is heard', everything)
        self.assertNotIn('1 to 3 %', everything)
        self.assertNotIn('a tenth of the amplitude', everything)
        charge = [source for _, source in recipes.get('scifi.charge').rules]
        self.assertFalse(any('Blumstein' in s for s in charge))     # its rise is a glide, the study's were abrupt


if __name__ == '__main__':
    unittest.main()
