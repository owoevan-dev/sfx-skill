"""A sound still at its recipe's default parameters gets a note, which is no flag."""
import json
import subprocess
import sys
import unittest

import helpers
from sfxlib import check, project, recipes, render


class TheNote(unittest.TestCase):
    def render(self, sound):
        out = helpers.scratch(f'recipe_note_{self.id().rsplit(".", 1)[-1]}') / 'render'
        render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'seed': 3, 'variants': 1, 'sounds': [sound]}), out, page=False)
        return out, json.loads((out / 'check.json').read_text(encoding='utf-8'))

    def test_a_recipe_at_its_defaults_gets_a_note_and_no_flag(self):
        _, report = self.render({'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm'})
        self.assertEqual([(n['kind'], n['sound']) for n in report['notes']], [('recipe_defaults', 'ok')])
        self.assertIn("uses this recipe's default parameters", report['notes'][0]['message'])
        self.assertIn('ui.confirm', report['notes'][0]['message'])
        self.assertEqual(report['flags'], [])

    def test_changing_one_parameter_clears_it(self):
        recipe = recipes.get('ui.confirm')
        p = recipe.params[0]
        value = p.default + (p.hi - p.default) / 2 if p.hi > p.default else p.default - (p.default - p.lo) / 2
        _, report = self.render({'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm', 'params': {p.name: value}})
        self.assertEqual(report['notes'], [])

    def test_a_parameter_written_at_its_default_is_still_the_default(self):
        recipe = recipes.get('ui.confirm')
        p = recipe.params[0]
        _, report = self.render({'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm', 'params': {p.name: p.default}})
        self.assertEqual(len(report['notes']), 1)

    def test_a_sound_of_layers_gets_none(self):
        _, report = self.render({'id': 'own', 'category': 'ui', 'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'pitch': 660, 'length': 0.1}]})
        self.assertEqual(report['notes'], [])

    def test_a_recipe_this_version_does_not_have_is_no_error(self):
        self.assertFalse(check.at_recipe_defaults({'recipe': 'gone.recipe', 'params': {}}))

    def test_the_commands_print_it_after_the_flags(self):
        path = helpers.scratch('recipe_note_cli') / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'seed': 3, 'variants': 1,
                                    'sounds': [{'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm'}]}), encoding='utf-8')
        out = path.parent / 'render'
        done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'render', str(path), '--output', str(out), '--no-page'], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('0 flags', done.stdout)
        self.assertIn("note: ok uses this recipe's default parameters", done.stdout)
        done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'check', str(out)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                              env=helpers.child_env(), timeout=120)
        self.assertIn("note: ok uses this recipe's default parameters", done.stdout)


if __name__ == '__main__':
    unittest.main()
