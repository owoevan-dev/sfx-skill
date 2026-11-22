"""What someone meets the first time they use the skill: a Python without NumPy, packs kept in two libraries, the lines
an import stops on, the level report, the numbers on a loop's card, the units of a recording's map and the words of the
documents."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

import helpers
import test_audition
import test_project  # registers test.beep
import test_samples as ts
from sfxlib import audition, denoise, project, render, samples

RATE = 48000
SKILL = helpers.REPO / 'skills' / 'sound-effects'
SFX = helpers.SCRIPTS / 'sfx.py'
LEGAL_CODE = ('Creative Commons Attribution 4.0 International Public License\n'
              'By exercising the Licensed Rights You accept these terms.\n'
              'Section 1 Definitions.\nSection 2 License grant.\nSection 6 Term and Termination.\n'
              'Section 5 Disclaimer and Limitation of Liability.\n'
              + 'Copyright in the Licensed Material is held by Licensor and may be used by You.\n' * 70)
CARD_TEXTS = """
function texts(node, out) {
  if (node.textContent) out.push(node.textContent);
  for (const c of node.children || []) texts(c, out);
  return out;
}
console.log(JSON.stringify(texts(ids['cards'].children[0], [])));
"""


def run(*args, env=None):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=env or helpers.child_env(), timeout=300)


def read(name):
    return (SKILL / name).read_text(encoding='utf-8')


class WithoutNumPy(unittest.TestCase):
    def test_the_first_command_says_what_to_install(self):
        folder = helpers.scratch('first_use_no_numpy')
        (folder / 'numpy.py').write_text("raise ImportError('No module named numpy')\n", encoding='utf-8')
        proc = run('doctor', env=dict(helpers.child_env(), PYTHONPATH=str(folder)))
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertNotIn('Traceback', out)
        self.assertIn('-m pip install -r', out)
        self.assertIn('requirements.txt', out)


class NarrowConsole(unittest.TestCase):
    """A console or pipe in a narrow code page (cp950 on a Traditional Chinese Windows) cannot show every character of a
    recording's title or a project's: the command goes on and prints them as escapes."""

    def env(self):
        return dict(helpers.child_env(), PYTHONIOENCODING='cp950')

    def test_render_goes_on_past_a_title_the_console_cannot_show(self):
        folder = helpers.scratch('first_use_narrow_render')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 'Café テスト', 'variants': 1,
                                    'sounds': [{'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm'}]},
                                   ensure_ascii=False), encoding='utf-8')
        proc = run('render', path, '--output', folder / 'render', env=self.env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((folder / 'render' / 'audition.html').is_file())
        self.assertIn('Caf\\xe9', proc.stdout)

    def test_a_search_goes_on_past_a_title_the_console_cannot_show(self):
        folder = helpers.scratch('first_use_narrow_find')
        pack = {'Gläser.wav': ts.wav_bytes(ts.tone(880, 0.3)), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Glass Pack', pack), folder / 'library', licence='CC0-1.0')
        proc = run('samples', '--library', folder / 'library', '--find', 'glass', env=self.env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('Gl\\xe4ser', proc.stdout)


def _row(name, title, tags, labels, seconds, range_db, screen=()):
    return {'library': 'pack', 'file': name, 'title': title, 'tags': list(tags), 'labels': list(labels), 'seconds': seconds,
            'channels': 1, 'measures': {'range_db': range_db, 'peak_db': -3.0}, 'screen': list(screen)}


class Searching(unittest.TestCase):
    """A library of thousands: every word counts wherever it is, the best come first, and a word or a length can be
    ruled out."""

    def index(self):
        return {'packs': {'pack': {'title': 'Pack', 'licence': 'CC0-1.0', 'files': 5}}, 'files': [
            _row('1', 'old door', ['creak'], ['Door'], 4.0, 20.0),
            _row('2', 'door slam', ['heavy'], ['Door'], 2.0, 35.0),
            _row('3', 'creaky gate', ['door', 'wood'], ['Squeak'], 9.0, 40.0),
            _row('4', 'door creak with traffic', ['street'], ['Door'], 25.0, 30.0),
            _row('5', 'kitchen', ['creak'], ['Cupboard'], 3.0, 50.0, ['noisy'])]}

    def names(self, *args, **kw):
        return [f['file'] for f in samples.find(self.index(), *args, **kw)]

    def test_every_word_must_be_there_wherever_it_is(self):
        self.assertEqual(set(self.names('door creak')), {'1', '3', '4'})

    def test_the_named_class_comes_first_then_the_cleanest(self):
        self.assertEqual(self.names('door'), ['2', '4', '1', '3'])

    def test_a_recording_that_fails_a_screen_comes_after_those_that_pass(self):
        self.assertEqual(self.names('creak')[-1], '5')

    def test_a_word_or_a_length_can_be_ruled_out(self):
        self.assertEqual(set(self.names('door creak', exclude=['traffic'])), {'1', '3'})
        self.assertEqual(set(self.names('door', max_seconds=5.0)), {'1', '2'})
        self.assertEqual(set(self.names('door', min_seconds=5.0)), {'3', '4'})

    def test_the_command_shows_the_best_few_and_says_how_many_more(self):
        folder = helpers.scratch('first_use_find_limit')
        members = {f'knock{i:02d}.wav': ts.wav_bytes(ts.tone(300 + 10 * i, 0.1, channels=1)) for i in range(40)}
        members['License.txt'] = ts.CC0_TEXT.encode('utf-8')
        samples.add(ts.as_folder(folder / 'Knock Pack', members), folder / 'library', licence='CC0-1.0')
        out = run('samples', '--library', folder / 'library', '--find', 'knock').stdout
        self.assertEqual(sum('knock-pack/knock' in line for line in out.splitlines()), 25)
        self.assertIn('40 recordings match', out)
        self.assertIn('--limit 0', out)
        out = run('samples', '--library', folder / 'library', '--find', 'knock', '--limit', '0').stdout
        self.assertEqual(sum('knock-pack/knock' in line for line in out.splitlines()), 40)


class Events(unittest.TestCase):
    def test_strikes_are_found_with_their_levels(self):
        x = np.zeros(2 * RATE)
        rng = np.random.default_rng(4)
        for at, amp in ((0.5, 0.8), (1.25, 0.2)):
            i = int(at * RATE)
            x[i:i + 4800] += amp * rng.standard_normal(4800) * np.exp(-np.arange(4800) / 900)
        events = denoise.events(x, RATE)
        self.assertEqual([round(e['onset'], 2) for e in events], [0.5, 1.25])
        self.assertAlmostEqual(events[0]['db'], 0.0, delta=0.5)
        self.assertLess(events[1]['db'], -8.0)

    def test_a_steady_sound_has_no_events(self):
        x = 0.3 * np.random.default_rng(4).standard_normal(RATE)
        self.assertEqual(denoise.events(x, RATE), [])

    def test_the_map_lists_them(self):
        folder = helpers.scratch('first_use_map_events')
        x = np.zeros((RATE, 1))
        x[24000:26400, 0] = 0.2 * np.random.default_rng(3).standard_normal(2400) * np.exp(-np.arange(2400) / 500)
        members = {'hit.wav': ts.wav_bytes(x, rate=RATE), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Hit Pack', members), folder / 'library', licence='CC0-1.0')
        proc = run('samples', '--library', folder / 'library', '--map', 'hit-pack/hit')
        self.assertIn('Events: 0.50 s', proc.stdout)


class SystemErrors(unittest.TestCase):
    def test_a_folder_that_cannot_be_made_is_a_message(self):
        folder = helpers.scratch('first_use_bad_output')
        (folder / 'a file').write_text('in the way', encoding='utf-8')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm'}]}), encoding='utf-8')
        proc = run('render', path, '--output', folder / 'a file' / 'render')
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertNotIn('Traceback', out)
        self.assertIn('render:', out)


class SeveralLibraries(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'first_use_{self._testMethodName}')
        self.a, self.b = self.folder / 'a', self.folder / 'b'
        samples.add(ts.as_folder(self.folder / 'Wood Pack', ts.pack_members()), self.a, licence='CC0-1.0')
        crate = {'crate.wav': ts.wav_bytes(ts.tone(880, 0.3)), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(self.folder / 'Crate Pack', crate), self.b, licence='CC0-1.0')

    def test_two_libraries_read_as_one(self):
        library = samples.Library([self.a, self.b])
        self.assertGreater(len(library.recording('wood-pack', 'woodKnock_light_000')), 0)
        self.assertGreater(len(library.recording('crate-pack', 'crate')), 0)
        self.assertEqual(library.provenance('crate-pack', 'crate')['library'], 'crate-pack')

    def test_one_folder_named_twice_is_one_library(self):
        samples.Library([self.a, self.a]).entry('wood-pack', 'woodKnock_light_000')

    def test_a_pack_held_by_two_libraries_is_refused(self):
        samples.add(ts.as_folder(self.folder / 'Wood Pack', ts.pack_members()), self.b, licence='CC0-1.0')
        with self.assertRaises(samples.SampleError) as caught:
            samples.Library([self.a, self.b])
        self.assertIn('wood-pack', str(caught.exception))
        self.assertIn(str(self.b), str(caught.exception))

    def test_a_missing_pack_says_another_library_can_be_named(self):
        with self.assertRaises(samples.SampleError) as caught:
            samples.Library(self.a).entry('crate-pack', 'crate')
        self.assertIn('--samples', str(caught.exception))

    def test_render_takes_both(self):
        path = self.folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'both', 'category': 'impact', 'layers': [
                {'source': {'type': 'sample', 'library': 'wood-pack', 'file': 'woodKnock_light_000'}, 'length': 0.3},
                {'source': {'type': 'sample', 'library': 'crate-pack', 'file': 'crate'}, 'length': 0.3}]}]}),
            encoding='utf-8')
        proc = run('render', path, '--output', self.folder / 'render', '--samples', self.a, '--samples', self.b)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        resolved = json.loads((self.folder / 'render' / 'resolved.json').read_text(encoding='utf-8'))
        self.assertEqual({r['library'] for r in resolved['recordings']}, {'wood-pack', 'crate-pack'})

    def test_the_map_names_the_unit_of_its_bands(self):
        proc = run('samples', '--library', self.a, '--map', 'wood-pack/woodKnock_light_000')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('octave bands (dBFS RMS)', proc.stdout)


class ImportStop(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'first_use_{self._testMethodName}')

    def stop(self, members):
        with self.assertRaises(samples.SampleError) as caught:
            samples.add(ts.as_folder(self.folder / 'crates', members), self.folder / 'library', licence='CC-BY-4.0')
        return str(caught.exception)

    def test_a_missing_author_shows_where_the_name_may_be(self):
        text = 'Crate hits by Mara Quill\nLicensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/\n'
        message = self.stop(ts.pack_members(licence_text=text))
        self.assertIn('--author', message)
        self.assertIn('License.txt: Crate hits by Mara Quill', message)

    def test_the_words_of_a_legal_text_are_not_offered_as_a_name(self):
        members = ts.pack_members(licence_text=LEGAL_CODE)
        members['Readme.txt'] = b'Recorded by Mara Quill in 2026.\n'
        message = self.stop(members)
        self.assertIn('Readme.txt: Recorded by Mara Quill', message)
        self.assertNotIn('held by Licensor', message)


class LevelReport(unittest.TestCase):
    def test_check_says_what_set_the_level_and_what_was_limited(self):
        out = helpers.scratch('first_use_check') / 'render'
        render.render(project.validate(test_project.minimal()), out)
        path = out / 'resolved.json'
        resolved = json.loads(path.read_text(encoding='utf-8'))
        variant = resolved['sounds'][0]['variants'][0]
        variant['limited_db'] = 6.0
        path.write_text(json.dumps(resolved), encoding='utf-8')
        proc = run('check', out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('set by', proc.stdout)
        self.assertRegex(proc.stdout, rf"limited: .*{Path(variant['file']).stem} 6\.0 dB")


class LoopCard(unittest.TestCase):
    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_each_variant_shows_its_own_measures(self):
        data = {'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [
            {'id': 'rain', 'category': 'ambience', 'recipe': 'ambience.rain_light', 'loop': {'seconds': 2.0}}]}
        base = helpers.scratch('first_use_loop')
        out = base / 'render'
        render.render(project.validate(data), out)
        path = out / 'check.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        for f, (spread, halves) in zip(report['files'], ((1.5, 0.2), (6.5, -3.6))):
            f['loudness_range_lu'], f['halves_db'] = spread, halves
        path.write_text(json.dumps(report), encoding='utf-8')
        audition.build(out)
        harness = base / 'card.js'
        harness.write_text(test_audition.HARNESS.split('const firstCard')[0] + CARD_TEXTS, encoding='utf-8')
        proc = subprocess.run(['node', str(harness), str(out / 'audition.html')], capture_output=True, text=True,
                              encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        words = ' '.join(json.loads(proc.stdout.strip().splitlines()[-1]))
        for expected in ('1.5 LU', '6.5 LU', '+0.2 dB', '-3.6 dB'):
            self.assertIn(expected, words)


class MapBands(unittest.TestCase):
    def test_a_tone_reads_at_its_own_level(self):
        t = np.arange(RATE) / RATE
        levels = denoise.bands(0.5 * np.sin(2 * np.pi * 1500 * t), RATE, 0.2, 0.8)
        self.assertAlmostEqual(levels[1000], 20 * np.log10(0.5 / np.sqrt(2)), delta=0.2)

    def test_the_bands_of_noise_add_up_to_its_level(self):
        x = np.random.default_rng(1).standard_normal(RATE) * 0.1
        levels = denoise.bands(x, RATE, 0.2, 0.8)
        total = 10 * np.log10(sum(10 ** (v / 10) for v in levels.values()))
        self.assertAlmostEqual(total, -20.0, delta=0.4)


class Words(unittest.TestCase):
    def test_a_pack_under_conditions_is_not_said_to_be_refused(self):
        text = read('references/project-format.md')
        self.assertNotIn('only when its licence is CC0', text)
        self.assertIn('carry over to the sound', text)

    def test_the_overview_does_not_deny_using_recordings(self):
        text = read('SKILL.md')
        self.assertNotIn('nothing is copied from a sound library', text)
        self.assertIn('packs the user imports', ' '.join(text.split('---', 2)[2].split('## Workflow')[0].split()))

    def test_expanding_a_recipe_says_what_it_keeps(self):
        self.assertIn('differ only in pitch and time', read('SKILL.md'))

    def test_paths_say_where_they_start(self):
        self.assertIn("from the game's project folder", read('SKILL.md'))
        self.assertIn("This skill's `examples/` folder", read('references/sound-design.md'))
        self.assertTrue((SKILL / 'examples' / 'hollow-road-recorded.json').is_file())

    def test_several_libraries_are_documented(self):
        self.assertIn('--samples A --samples B', read('references/sound-design.md'))


if __name__ == '__main__':
    unittest.main()
