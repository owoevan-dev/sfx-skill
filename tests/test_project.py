"""The project file: validation with every error at once and the nearest field name, defaults, recipes' parameters."""
import copy
import unittest

import helpers  # noqa: F401
from sfxlib import project, recipes
from sfxlib.recipes.base import Param, Recipe

BEEP = Recipe(
    name='test.beep', category='ui', summary='a test beep',
    params=(Param('base_hz', 880.0, 150.0, 5000.0, 'Hz', vary='cents', amount=30.0),
            Param('length', 0.1, 0.02, 1.0, 's', vary='pct', amount=5.0),
            Param('brightness', 0.5, 0.0, 1.0, '', vary='abs', amount=0.05)),
    build=lambda p: {'layers': [{'source': {'type': 'osc', 'wave': 'triangle'}, 'start': 0.0,
                                 'length': p['length'], 'pitch': p['base_hz'],
                                 'env': {'attack': 0.002, 'decay': p['length'] - 0.002}}]})
recipes.register(BEEP)


def minimal():
    return {'schema': 'sound-effects/1', 'title': 'Test Set',
            'sounds': [{'id': 'beep', 'category': 'ui', 'recipe': 'test.beep', 'params': {'base_hz': 660}},
                       {'id': 'hand_made', 'category': 'ui', 'layers': [
                           {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': 0.05,
                            'env': [[0, 1.0, 'db'], [0.05, 0.0]], 'filter': {'type': 'bandpass', 'cutoff': 3000,
                                                                              'q': 2.0}}],
                        'chain': {'delay': {'time': 0.05, 'feedback': 0.2, 'mix': 0.2}}}]}


def errors_of(data):
    try:
        project.validate(data)
    except project.ProjectError as error:
        return error.errors
    return []


class Validate(unittest.TestCase):
    def test_a_minimal_project_passes_and_gets_defaults(self):
        p = project.validate(minimal())
        self.assertEqual(p['variants'], 4)
        self.assertEqual(p['channels'], 1)
        self.assertEqual(p['level'], {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 6.0})
        self.assertIsInstance(p['seed'], int)
        self.assertEqual(p['seed'], project.validate(minimal())['seed'])   # from the title, the same every time
        beep = p['sounds'][0]
        self.assertEqual(beep['params'], {'base_hz': 660.0, 'length': 0.1, 'brightness': 0.5})
        self.assertEqual(beep['variants'], 4)
        self.assertEqual(beep['trim_db'], 0.0)

    def test_unknown_fields_name_the_nearest_known_one(self):
        data = minimal()
        data['sounds'][1]['layers'][0]['sorce'] = data['sounds'][1]['layers'][0].pop('source')
        data['titel'] = 'x'
        found = errors_of(data)
        self.assertTrue(any("sounds[1].layers[0].sorce" in e and "'source'" in e for e in found), found)
        self.assertTrue(any("titel" in e and "'title'" in e for e in found), found)

    def test_all_errors_are_reported_together(self):
        data = minimal()
        data['sounds'][0]['id'] = 'Bad Id'
        data['sounds'][1]['category'] = 'gui'
        data['variants'] = 40
        self.assertGreaterEqual(len(errors_of(data)), 3)

    def test_ids_must_be_unique(self):
        data = minimal()
        data['sounds'][1]['id'] = 'beep'
        self.assertTrue(any('beep' in e and 'unique' in e for e in errors_of(data)))

    def test_recipe_and_layers_are_exclusive(self):
        data = minimal()
        data['sounds'][0]['layers'] = copy.deepcopy(data['sounds'][1]['layers'])
        self.assertTrue(errors_of(data))
        del data['sounds'][0]['layers'], data['sounds'][0]['recipe']
        self.assertTrue(errors_of(data))

    def test_recipe_names_and_parameters_are_checked(self):
        data = minimal()
        data['sounds'][0]['recipe'] = 'test.bep'
        self.assertTrue(any("'test.beep'" in e for e in errors_of(data)))
        data = minimal()
        data['sounds'][0]['params'] = {'base_hz': 9000}
        self.assertTrue(any('base_hz' in e and '5000' in e for e in errors_of(data)))
        data['sounds'][0]['params'] = {'bas_hz': 700}
        self.assertTrue(any("'base_hz'" in e for e in errors_of(data)))

    def test_vary_names_must_be_varying_parameters(self):
        data = minimal()
        data['sounds'][0]['vary'] = {'base_hz': 50, 'colour': 1}
        self.assertTrue(any('colour' in e for e in errors_of(data)))

    def test_curves_and_sources_are_checked(self):
        data = minimal()
        layer = data['sounds'][1]['layers'][0]
        layer['env'] = [[0.1, 1.0], [0.05, 0.0]]
        layer['source'] = {'type': 'noise', 'color': 'blue'}
        found = errors_of(data)
        self.assertTrue(any('env' in e for e in found), found)
        self.assertTrue(any('color' in e for e in found), found)

    def test_flows_name_existing_sounds(self):
        data = minimal()
        data['flows'] = [{'name': 'press', 'sounds': ['beep', 'missing']}]
        self.assertTrue(any('missing' in e for e in errors_of(data)))

    def test_schema_name_is_required(self):
        data = minimal()
        data['schema'] = 'sound-effects/9'
        self.assertTrue(errors_of(data))


if __name__ == '__main__':
    unittest.main()
