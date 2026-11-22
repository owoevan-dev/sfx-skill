"""The example of what recordings can do (examples/hollow-road-recorded.json): one sound per technique of the guide's
section 6, valid, playing only recordings a release's packs carry, and rendering without a flag when FSD50K is at hand."""
import json
import unittest

import helpers
import make_fsd50k_packs as maker
from sfxlib import project, render, samples
from test_fsd50k import real_fsd50k

EXAMPLE = helpers.EXAMPLES / 'hollow-road-recorded.json'


def sources(sound):
    return [layer['source'] for layer in sound['layers'] if layer['source']['type'] == 'sample']


class Design(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proj = project.load(EXAMPLE)
        cls.sounds = {s['id']: s for s in cls.proj['sounds']}

    def test_each_technique_is_shown(self):
        every = [src for s in self.sounds.values() for src in sources(s)]
        self.assertTrue(any(src.get('reverse') for src in every))                               # reversal
        self.assertTrue(any(src.get('cents', 0) <= -1200 for src in every))                     # size
        stretches = [src['time_stretch'] for src in every if 'time_stretch' in src]
        self.assertTrue(any(ts.get('method') == 'smear' for ts in stretches))                   # a long texture
        self.assertTrue(any(ts.get('keep_attack_ms') for ts in stretches))                      # a kept attack
        loops = [s for s in self.sounds.values() if s.get('use') == 'loop']
        self.assertTrue(loops and all(sources(s) for s in loops))                               # a loop from a recording
        bounce = self.sounds['stone_bounce']['layers']
        gaps = [b['start'] - a['start'] for a, b in zip(bounce, bounce[1:])]
        self.assertTrue(all(later < earlier for earlier, later in zip(gaps, gaps[1:])))         # shrinking gaps

    def test_every_sound_says_why(self):
        for sound in self.sounds.values():
            self.assertTrue(sound.get('notes'), sound['id'])

    def test_the_guide_names_the_example(self):
        guide = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'sound-design.md').read_text(encoding='utf-8')
        self.assertIn(f'examples/{EXAMPLE.name}', guide)


@unittest.skipUnless(real_fsd50k(), 'no FSD50K library in tools/local.json (or --quick)')
class WithTheLibrary(unittest.TestCase):
    def test_it_renders_with_no_flags(self):
        out = helpers.scratch('example_recorded') / 'render'
        render.render(project.load(EXAMPLE), out, samples=real_fsd50k())
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(check['flags'], [], json.dumps(check['flags'], indent=1))

    def test_a_release_pack_would_carry_every_recording_it_plays(self):
        index = samples.load_index(real_fsd50k())
        carried = {(r['library'], r['file']) for r in maker.chosen(index, maker.example_files())}
        wanted = {(src['library'], take['file'] if isinstance(take, dict) else take)
                  for s in project.load(EXAMPLE)['sounds'] for src in sources(s)
                  for take in (src['file'] if isinstance(src['file'], list) else [src['file']])}
        self.assertEqual(wanted - carried, set())


if __name__ == '__main__':
    unittest.main()
