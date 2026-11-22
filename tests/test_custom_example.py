"""The worked example of references/sound-design.md: an original game's sounds designed from its brief (a shared style,
sounds written as layers, a recipe adapted, instrument and sample layers, a loop), valid, and rendering without a flag
when music-production's configuration (its marimba) and FSD50K's evaluation set (the gate, door and footsteps) are at
hand."""
import json
import unittest

import helpers
from sfxlib import project, render
from test_instruments import config_or_none
from test_fsd50k import real_fsd50k

EXAMPLE = helpers.EXAMPLES / 'tidewatch-keep-custom.json'


class Design(unittest.TestCase):
    def test_the_example_uses_what_the_guide_describes(self):
        proj = project.load(EXAMPLE)
        sounds = {s['id']: s for s in proj['sounds']}
        self.assertTrue(proj['style']['space'] and proj['style']['tilt_db'])
        self.assertTrue(any('recipe' in s and s['params'] for s in sounds.values()))           # a recipe adapted
        layers = [layer for s in sounds.values() for layer in s.get('layers', [])]
        self.assertTrue(any(layer['source']['type'] == 'instrument' for layer in layers))      # an instrument
        takes = [layer['source']['file'] for layer in layers if layer['source']['type'] == 'sample']
        self.assertTrue(any(isinstance(t, list) and len(t) > 1 for t in takes))               # recordings, takes
        self.assertTrue(any(layer['source'].get('ratios') for layer in layers))               # an object of its own
        self.assertTrue(any(s.get('loop') for s in sounds.values()))                          # a loop
        self.assertTrue(any(s['style'] is False for s in sounds.values()))                    # dry interface sounds
        for sound in sounds.values():
            self.assertTrue(sound.get('notes'), sound['id'])                                  # each says why

    def test_the_guide_names_the_example(self):
        guide = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'sound-design.md').read_text(
            encoding='utf-8')
        self.assertIn(f'examples/{EXAMPLE.name}', guide)


@unittest.skipUnless(config_or_none() and real_fsd50k(), 'no music-production configuration or FSD50K library in '
                     'tools/local.json (or --quick)')
class Render(unittest.TestCase):
    def test_the_example_renders_with_no_flags(self):
        out = helpers.scratch('example_custom') / 'render'
        render.render(project.load(EXAMPLE), out, config=config_or_none(), samples=real_fsd50k())
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))


if __name__ == '__main__':
    unittest.main()
