"""The seven impact recipes: registered with their rules, valid and rendering at every parameter's limits, the
modal validator's checks, and the example project rendering without a flag."""
import json
import unittest

import numpy as np

import helpers
from sfxlib import project, recipes, render, synth, traits
from test_recipes_ui import settings

IMPACT = ('impact.punch', 'impact.thud', 'impact.wood_knock', 'impact.metal_hit', 'impact.metal_clang',
          'impact.glass_tink', 'impact.swing')
EXAMPLE = helpers.EXAMPLES / 'clockwork-yard-impacts.json'


class Recipes(unittest.TestCase):
    def test_all_seven_are_registered_with_rules(self):
        for name in IMPACT:
            recipe = recipes.get(name)
            self.assertEqual(recipe.category, 'impact')
            self.assertTrue(recipe.rules, name)

    def test_only_interface_sounds_claim_the_feedback_latency_source(self):
        """Kaaresoja (2016) measured touchscreen button feedback, which is why check flags onsets for ui sounds only;
        no recipe of another category may cite it as its rule."""
        from sfxlib import check
        for recipe in recipes.all_recipes():
            if recipe.category not in check.ONSET_CATEGORIES:
                self.assertFalse(any('Kaaresoja' in source for _, source in recipe.rules), recipe.name)

    def test_every_setting_is_valid_and_renders(self):
        for name in IMPACT:
            recipe = recipes.get(name)
            for params in settings(recipe):
                spec = recipe.build(params)
                sound = {'id': 'x', 'category': 'impact', 'layers': spec['layers']}
                if spec.get('chain'):
                    sound['chain'] = spec['chain']
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})
                audio = synth.render_sound(spec, seed=1, channels=1)
                self.assertTrue(np.isfinite(audio).all(), (name, params))
                self.assertGreater(float(np.max(np.abs(audio))), 0.01, (name, params))

    def test_materials_decay_in_the_studys_order(self):
        """Klatzky, Pai and Krotkov: material is heard in the decay. Wood (with its hollow body) has died to -20 dB
        within a tenth of a second; a damped metal hit later than wood; glass rings on, and a free metal clang for
        over a second."""
        def decay20(name):
            recipe = recipes.get(name)
            audio = synth.render_sound(recipe.build(recipe.defaults()), seed=1, channels=1)
            return traits.measures(audio)['decay20_ms']
        wood, glass = decay20('impact.wood_knock'), decay20('impact.glass_tink')
        self.assertLess(wood, 100)
        self.assertGreater(glass, wood)
        self.assertGreater(decay20('impact.metal_clang'), 1000)


class Validator(unittest.TestCase):
    def test_modal_fields_are_checked(self):
        def errors(src, pitch=500):
            try:
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                    {'id': 'x', 'category': 'impact', 'layers': [{'source': src, 'length': 0.2, 'pitch': pitch}]}]})
            except project.ProjectError as error:
                return error.errors
            return []
        self.assertEqual(errors({'type': 'modal', 'material': 'steel'}), [])
        self.assertTrue(errors({'type': 'modal', 'material': 'gold'}))
        self.assertTrue(errors({'type': 'modal', 'material': 'steel', 't_d': 100}))
        self.assertTrue(errors({'type': 'modal', 'modes': 30}))
        self.assertTrue(errors({'type': 'modal'}, pitch=15000))


class Example(unittest.TestCase):
    def test_the_example_project_renders_with_no_flags(self):
        proj = project.load(EXAMPLE)
        self.assertEqual(sorted(s['recipe'] for s in proj['sounds']), sorted(IMPACT))
        out = helpers.scratch('example_impact') / 'render'
        render.render(proj, out)
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))


class LongRing(unittest.TestCase):
    def test_a_long_clang_fades_out_instead_of_being_cut(self):
        r = recipes.get('impact.metal_clang')
        values = {p.name: p.default for p in r.params}
        values.update(base_hz=150.0, ring=1.5)
        x = synth.render_sound(r.build(values), seed=1)[:, 0]
        before_the_end = x[-int(0.035 * 48000):-int(0.015 * 48000)]
        self.assertLess(float(np.sqrt(np.mean(before_the_end ** 2))), 0.03 * float(np.max(np.abs(x))))


if __name__ == '__main__':
    unittest.main()
