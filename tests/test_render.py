"""A whole render: files, level matching on disk, exact rerenders, independence of sounds, refusals, checks."""
import copy
import json
import unittest
from unittest import mock

import numpy as np

import helpers
import test_project  # registers test.beep
from sfxlib import loudness, project, render, wav


def two_sounds(variants=3):
    data = test_project.minimal()
    data['variants'] = variants
    return data


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch('render')
        cls.out = cls.base / 'r1'
        cls.report = render.render(project.validate(two_sounds()), cls.out)
        cls.resolved = json.loads((cls.out / 'resolved.json').read_text(encoding='utf-8'))

    def test_every_variant_is_written_with_its_records(self):
        names = sorted(p.name for p in (self.out / 'master').iterdir())
        self.assertEqual(names, [f'{s}_{n:02d}.wav' for s in ('beep', 'hand_made') for n in (1, 2, 3)])
        for name in ('resolved.json', 'check.json', 'files.json', 'report.json'):
            self.assertTrue((self.out / name).is_file(), name)
        files = json.loads((self.out / 'files.json').read_text(encoding='utf-8'))
        self.assertEqual(len([f for f in files if f['path'].startswith('master/')]), 6)

    def test_levels_on_disk_match_and_stay_under_the_ceiling(self):
        target = self.resolved['level']['target_lufs']
        for sound in self.resolved['sounds']:
            for v in sound['variants']:
                data, info = wav.read(self.out / v['file'])
                self.assertEqual((info['rate'], info['bits']), (48000, 24))
                x = data.astype(np.float64)
                self.assertAlmostEqual(loudness.brief(x), target + sound['trim_db'], delta=0.05)
                self.assertLessEqual(loudness.true_peak_db(x), -1.0 + 0.05)

    def test_a_rerender_is_byte_identical(self):
        again = self.base / 'r2'
        render.render(project.validate(two_sounds()), again)
        for p in (self.out / 'master').iterdir():
            self.assertEqual(p.read_bytes(), (again / 'master' / p.name).read_bytes(), p.name)

    def test_changing_one_sound_moves_another_only_by_one_gain(self):
        data = two_sounds()
        data['sounds'][0]['params'] = {'base_hz': 1200}
        other = self.base / 'r3'
        render.render(project.validate(data), other)
        for n in (1, 2, 3):
            a, _ = wav.read(self.out / 'master' / f'hand_made_{n:02d}.wav')
            b, _ = wav.read(other / 'master' / f'hand_made_{n:02d}.wav')
            a, b = a[:, 0].astype(float), b[:, 0].astype(float)
            ratio = np.sum(a * b) / np.sum(a * a)
            self.assertLess(np.max(np.abs(b - ratio * a)), 1e-5)

    def test_an_existing_folder_with_files_is_refused(self):
        with self.assertRaises(render.RenderError):
            render.render(project.validate(two_sounds()), self.out)

    def test_variants_differ_and_the_first_is_the_written_sound(self):
        beep = next(s for s in self.resolved['sounds'] if s['id'] == 'beep')
        self.assertEqual(beep['variants'][0]['params']['base_hz'], 660.0)
        self.assertNotEqual(beep['variants'][1]['params']['base_hz'], 660.0)
        a, _ = wav.read(self.out / 'master' / 'beep_01.wav')
        b, _ = wav.read(self.out / 'master' / 'beep_02.wav')
        self.assertFalse(len(a) == len(b) and np.array_equal(a, b))

    def test_check_report_measures_every_file(self):
        check = json.loads((self.out / 'check.json').read_text(encoding='utf-8'))
        self.assertEqual(len(check['files']), 6)
        one = check['files'][0]
        for key in ('true_peak_db', 'max_momentary_lufs', 'onset_ms', 'ending', 'bands', 'centroid_hz'):
            self.assertIn(key, one)
        self.assertIn('flags', check)


class Flags(unittest.TestCase):
    def test_a_late_start_in_a_ui_sound_is_flagged(self):
        data = two_sounds(variants=1)
        data['sounds'][1]['layers'][0]['start'] = 0.01
        out = helpers.scratch('render_flags') / 'r'
        render.render(project.validate(data), out)
        check = json.loads((out / 'check.json').read_text(encoding='utf-8'))
        self.assertTrue(any(f['kind'] == 'onset' and f['sound'] == 'hand_made' for f in check['flags']),
                        check['flags'])



class TheFolder(unittest.TestCase):
    def test_a_second_render_into_the_same_folder_at_once_stops(self):
        out = helpers.scratch('render_claim') / 'render'
        real = render._surge_takes

        def meanwhile(*args):                    # another render takes the folder while this one checks its takes
            (out / 'master').mkdir(parents=True)
            return real(*args)
        with mock.patch.object(render, '_surge_takes', meanwhile), self.assertRaisesRegex(render.RenderError, 'another render'):
            render.render(project.validate(test_project.minimal()), out, page=False)
        self.assertEqual([p.name for p in out.iterdir()], ['master'])

if __name__ == '__main__':
    unittest.main()
