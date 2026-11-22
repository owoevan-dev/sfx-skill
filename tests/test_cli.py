"""The command line: validate, render, check, recipes, expand (run as a user would run them)."""
import json
import subprocess
import sys
import unittest

import helpers

SFX = helpers.SCRIPTS / 'sfx.py'

LAYERS_PROJECT = {
    'schema': 'sound-effects/1', 'title': 'CLI Test', 'variants': 2,
    'sounds': [{'id': 'blip', 'category': 'ui', 'layers': [
        {'source': {'type': 'osc', 'wave': 'triangle'}, 'start': 0, 'length': 0.08, 'pitch': [[0, 900, 'exp'],
         [0.02, 1200]], 'env': {'attack': 0.001, 'decay': 0.079}}], 'vary': {'pitch_cents': 40}}]}


def run(*args):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=300)


class CommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch('cli')
        cls.project = cls.base / 'project.json'
        cls.project.write_text(json.dumps(LAYERS_PROJECT), encoding='utf-8')

    def test_validate_passes_and_counts(self):
        proc = run('validate', self.project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('1 sound', proc.stdout)
        self.assertIn('2 files', proc.stdout)

    def test_validate_lists_errors_with_the_nearest_field(self):
        bad = json.loads(json.dumps(LAYERS_PROJECT))
        bad['sounds'][0]['layers'][0]['lenght'] = bad['sounds'][0]['layers'][0].pop('length')
        path = self.base / 'bad.json'
        path.write_text(json.dumps(bad), encoding='utf-8')
        proc = run('validate', path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did you mean 'length'", proc.stdout + proc.stderr)

    def test_render_then_check(self):
        out = self.base / 'render_1'
        proc = run('render', self.project, '--output', out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((out / 'master' / 'blip_01.wav').is_file())
        self.assertIn('target', proc.stdout)
        proc = run('check', out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('flags', proc.stdout)

    def test_audition_rewrites_the_page_and_the_file_list(self):
        out = self.base / 'render_3'
        self.assertEqual(run('render', self.project, '--output', out).returncode, 0)
        proc = run('audition', out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        files = json.loads((out / 'files.json').read_text(encoding='utf-8'))
        self.assertIn('audition.html', [f['path'] for f in files])
        self.assertNotIn(b'\r\n', (out / 'files.json').read_bytes())

    def test_render_refuses_a_folder_with_files(self):
        out = self.base / 'render_2'
        self.assertEqual(run('render', self.project, '--output', out).returncode, 0)
        proc = run('render', self.project, '--output', out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('already holds files', proc.stdout + proc.stderr)


class Banks(unittest.TestCase):
    def test_banks_lists_the_presets_of_the_usable_banks(self):
        from test_instruments import fake_bank
        folder = helpers.scratch('cli_banks')
        bank = folder / 'bank.sf2'
        bank.write_bytes(fake_bank([(0, 11, 'Vibraphone'), (0, 12, 'Marimba')]))
        config = folder / 'config.json'
        config.write_text(json.dumps({'soundfont': {'path': str(bank), 'license': 'MIT'},
                                      'soundfonts': {'free': {'path': str(bank), 'license': 'CC0-1.0'},
                                                     'credit': {'path': str(bank), 'license': 'CC-BY-4.0'}}}),
                          encoding='utf-8')
        proc = run('banks', '--config', config, '--find', 'vibra')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('free', proc.stdout)
        self.assertIn('0:11 Vibraphone', proc.stdout)
        self.assertNotIn('Marimba', proc.stdout)
        self.assertIn('credit: not usable', proc.stdout)
        listing = json.loads(run('banks', '--config', config, '--json').stdout)
        self.assertEqual({b['name']: len(b['presets']) for b in listing}, {'default': 2, 'free': 2, 'credit': 0})


if __name__ == '__main__':
    unittest.main()
