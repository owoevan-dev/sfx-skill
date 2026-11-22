"""sfxkit: the builders a project script is written with give plain project JSON that validates and renders."""
import json
import subprocess
import sys
import unittest

import numpy as np

import helpers
import sfxkit
import test_samples as ts
from sfxkit import (bp, clip, crack, decay, equal_power, fm, hp, lands, looped, lp, modal, noise, rec, rumble, save, sound, speed,
                    swell, take, thump, tone, whoosh, wind)
from sfxlib import project, render, samples

RATE = 48000


def validated(layers, **extra):
    return project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1,
                             'sounds': [sound('x', 'impact', 'a note', layers, **extra)]})['sounds'][0]


class Builders(unittest.TestCase):
    def test_every_synthesis_block_is_a_valid_layer(self):
        validated([tone(0.0, 0.2, 440.0), tone(0.1, 0.2, 880.0, wave='saw', filt=lp(3000), vibrato={'rate_hz': 5.0, 'depth_cents': 8.0},
                                               tremolo={'rate_hz': 40.0, 'depth': 0.5}),
                   noise(0.0, 0.3, 'white', filt=hp(2000), env=decay(0.3)), noise(0.0, 0.3, filt=bp(800, 2.0), env=swell(0.3)),
                   modal(0.0, 0.5, 300.0, 1.2), thump(0.0, 120, 45, 0.3), rumble(0.0, 150, 0.6), crack(0.0), whoosh(0.0, 0.4, 400, 3000)])

    def test_a_struck_object_can_take_its_own_ratios(self):
        layer = modal(0.0, 0.5, 300.0, 1.2, ratios=[1.0, 2.1, 3.7])
        validated([layer])
        self.assertEqual(layer['source']['ratios'], [1.0, 2.1, 3.7])
        self.assertNotIn('modes', layer['source'])

    def test_fm_and_wind_are_valid_layers(self):
        validated([fm(0.0, 0.3, 440.0, ratio=1.4, index=[[0, 2.0], [0.3, 0.2]], filt=lp(4000)),
                   wind(0.0, 1.0, band_hz=300, turbulence=0.3, gust_hz=0.5)])

    def test_the_decay_of_a_struck_object_is_described_as_the_format_gives_it(self):
        self.assertNotIn('60 dB', sfxkit.modal.__doc__)
        self.assertIn('t_d / f', sfxkit.modal.__doc__)

    def test_a_recording_with_takes_is_a_valid_layer(self):
        layer = rec('some-pack', [take('a', 0.5, denoise={'noise': [0.0, 0.3]}), take('b', 1.0, gain=-3.0), 'c'], 0.1, 0.4, cents=-200,
                    gain=-2.0, filt=hp(100), pan=-0.5, reverse=False, stretch={'factor': 1.5, 'keep_attack_ms': 20})
        validated([layer], channels=2)
        self.assertEqual(layer['source']['file'][1], {'file': 'b', 'offset': 1.0, 'gain_db': -3.0})
        self.assertEqual(layer['source']['file'][0]['denoise'], {'noise': [0.0, 0.3]})
        validated([rec('some-pack', 'a', 0.0, 0.4, denoise={'noise': [0.0, 0.3]})])

    def test_a_takes_list_from_the_events_command_can_be_passed_on(self):
        printed = json.loads('[{"file": "54269", "offset": 0.359}, {"file": "54269", "offset": 4.583}]')
        validated([rec('some-pack', printed, 0.0, 0.3)])

    def test_envelopes_begin_and_end_where_they_should(self):
        for env in (clip(1.0), decay(1.0), swell(1.0), equal_power(1.0, 0.2)):
            self.assertEqual(env[-1][1], 0)
            self.assertAlmostEqual(env[-1][0], 1.0)
            self.assertEqual(env[0][0], 0)

    def test_equal_power_keeps_the_power_through_a_crossfade(self):
        env = equal_power(1.0, 0.25)
        rise, fall = env[:len(env) // 2], env[len(env) // 2:]
        self.assertEqual(len(rise), len(fall))
        for up, down in zip(rise, fall):
            self.assertAlmostEqual(up[1] ** 2 + down[1] ** 2, 1.0, places=3)

    def test_a_moment_of_a_recording_lands_where_it_is_asked_to(self):
        self.assertAlmostEqual(speed(1200), 2.0)
        start = lands(1.5, 0.5, -1200, 3.0)                  # an octave down, the moment 1 s in plays 2 s in
        self.assertAlmostEqual(start, 1.0)


class Writing(unittest.TestCase):
    def test_save_writes_a_project_the_command_accepts(self):
        folder = helpers.scratch('sfxkit_save')
        path = save(folder / 'project.json', 'A game', [sound('blip', 'ui', 'a blip', [tone(0.0, 0.1, 880.0)])], variants=2)
        proc = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'validate', str(path)], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['variants'], 2)

    def test_save_refuses_a_project_with_a_mistake_and_says_where(self):
        folder = helpers.scratch('sfxkit_bad')
        with self.assertRaises(ValueError) as caught:
            save(folder / 'project.json', 'A game', [sound('blip', 'nonsense', 'x', [tone(0.0, 0.1, 880.0)])])
        self.assertIn('category', str(caught.exception))
        self.assertFalse((folder / 'project.json').exists())


class ALoopFromARecording(unittest.TestCase):
    def test_it_renders_without_a_seam(self):
        folder = helpers.scratch('sfxkit_loop')
        rng = np.random.default_rng(11)
        x = 0.2 * rng.standard_normal((RATE * 8, 1))
        members = {'rain.wav': ts.wav_bytes(np.clip(x, -0.9, 0.9), rate=RATE), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Rain Pack', members), folder / 'library', licence='CC0-1.0')
        layers = looped('rain-pack', 'rain', 4.0, 1.0, (0.5, 3.0))
        self.assertEqual(len(layers), 2)
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            sound('rain_loop', 'ambience', 'rain', layers, use='loop', loop={'seconds': 4.0})]})
        render.render(proj, folder / 'r', samples=folder / 'library')
        check = json.loads((folder / 'r' / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual([f['kind'] for f in check['flags']], [])


class Documented(unittest.TestCase):
    def test_the_example_in_the_reference_runs(self):
        text = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'building-blocks.md').read_text(encoding='utf-8')
        code = text.split('```python')[1].split('```')[0].replace('PATH/TO/sound-effects/scripts', helpers.SCRIPTS.as_posix())
        folder = helpers.scratch('sfxkit_doc_example')
        (folder / 'build.py').write_text(code, encoding='utf-8')
        proc = subprocess.run([sys.executable, 'build.py'], cwd=folder, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        project.load(folder / 'project.json')

    def test_every_builder_is_in_the_reference(self):
        text = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'building-blocks.md').read_text(encoding='utf-8')
        for name in sfxkit.__all__:
            self.assertIn(f'`{name}(', text, name)


if __name__ == '__main__':
    unittest.main()
