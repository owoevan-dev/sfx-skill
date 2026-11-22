"""Paths people really have: a folder named in Chinese with spaces in it, and one longer than Windows's 260 characters."""
import json
import os
import subprocess
import sys
import unittest

import numpy as np

import helpers
import test_samples as ts

RATE = 48000
SFX = helpers.SCRIPTS / 'sfx.py'
PROJECT = {'schema': 'sound-effects/1', 'title': '測試音效', 'variants': 2, 'sounds': [
    {'id': 'blip', 'category': 'ui', 'recipe': 'ui.confirm'},
    {'id': 'hum', 'category': 'ambience', 'recipe': 'ambience.rain_light', 'loop': {'seconds': 2}, 'variants': 1}]}


def run(*args, env=None):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=env or helpers.child_env(), timeout=300)


class AFolderInChinese(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch('paths_chinese') / '我的 遊戲' / '音效 專案'
        cls.base.mkdir(parents=True)

    def ok(self, *args):
        proc = run(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        return proc

    def test_the_whole_way_through(self):
        path = self.base / '專案.json'
        path.write_text(json.dumps(PROJECT, ensure_ascii=False), encoding='utf-8')
        self.ok('validate', path)
        self.ok('render', path, '--output', self.base / '第一版')
        self.assertTrue((self.base / '第一版' / 'audition.html').is_file())
        self.ok('check', self.base / '第一版', '--traits')
        self.ok('spectrogram', self.base / '第一版', '--sound', 'blip')
        self.ok('audition', self.base / '第一版')
        self.ok('export', self.base / '第一版', '--target', 'godot', '--output', self.base / '匯出')
        self.assertTrue((self.base / '匯出' / 'manifest.json').is_file())
        self.ok('clean', self.base, '--keep', '第一版')

    def test_a_pack_imported_from_one_plays(self):
        x = 0.3 * np.sin(2 * np.pi * 440 * np.arange(RATE) / RATE)[:, None]
        source = ts.as_folder(self.base / '素材 包', {'門聲.wav': ts.wav_bytes(x, rate=RATE),
                                                    'License.txt': ts.CC0_TEXT.encode('utf-8')})
        library = self.base / '音效庫'
        self.ok('samples', '--library', library, '--add', source, '--licence', 'CC0-1.0', '--pack', 'doors')
        listed = self.ok('samples', '--library', library, '--json').stdout
        name = json.loads(listed)['files'][0]['file']
        project = {'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'door', 'category': 'foley', 'layers': [
                {'source': {'type': 'sample', 'library': 'doors', 'file': name}, 'length': 0.5}]}]}
        path = self.base / 'pack.json'
        path.write_text(json.dumps(project), encoding='utf-8')
        self.ok('render', path, '--output', self.base / '含錄音', '--samples', library)


class APathTooLong(unittest.TestCase):
    def test_it_works_or_says_so_without_a_traceback(self):
        base = helpers.scratch('paths_long')
        path = base / 'project.json'
        path.write_text(json.dumps(PROJECT, ensure_ascii=False), encoding='utf-8')
        deep = base
        while len(str(deep)) < 300:
            deep = deep / ('long_folder_name_' + 'x' * 30)
        proc = run('render', path, '--output', deep / 'render_1')
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        if proc.returncode == 0:
            self.assertTrue(os.path.isfile(str(deep / 'render_1' / 'resolved.json')))
        else:
            self.assertEqual(proc.returncode, 1)
            self.assertIn('render', proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
