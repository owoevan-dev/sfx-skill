"""Clean lists, and moves aside: a folder goes whole into the project's .sfx-trash, only when all of it is what this skill
wrote and it is older than the version kept. Nothing is deleted, exports stay unless asked for, downloads are only listed."""
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers
from test_import_safety import link_dir
from sfxlib import clean, export, project, render

START = 1_700_000_000


def tiny(title):
    return project.validate({'schema': 'sound-effects/1', 'title': title, 'variants': 2, 'sounds': [
        {'id': 'tick', 'category': 'ui', 'vary': {'pitch_cents': 30},
         'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.05, 'pitch': 1500.0}]}]})


def tree(folder):
    """{path: bytes} of every file under a folder."""
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}


def names(items):
    return [item['folder'] for item in items]


class Project(unittest.TestCase):
    def setUp(self):
        self.proj = helpers.scratch('clean_' + self._testMethodName[5:45]) / 'project'
        self.clock = START

    def dated(self, folder, record):
        """The record's time, a step on from the last whatever the grain of the file system's clock."""
        self.clock += 10
        os.utime(folder / record, (self.clock, self.clock))

    def make(self, *folders, title='Clean test'):
        for name in folders:
            render.render(tiny(title), self.proj / name, page=False)
            self.dated(self.proj / name, 'resolved.json')

    def exported(self, source, name):
        export.export(self.proj / source, self.proj / name, 'generic', formats=('wav',))
        self.dated(self.proj / name, 'manifest.json')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), *map(str, args)], capture_output=True,
                              text=True, encoding='utf-8', env=helpers.child_env())


class NothingIsDeleted(Project):
    def test_nothing_moves_unless_asked(self):
        self.make('render_1', 'render_2')
        before = tree(self.proj)
        result = clean.clean(self.proj, ['render_2'])
        self.assertEqual(names(result['move']), ['render_1'])
        self.assertEqual((result['moved'], result['trash']), ([], None))
        self.assertEqual(tree(self.proj), before)
        self.assertFalse((self.proj / clean.TRASH).exists())

    def test_a_folder_goes_whole_and_can_come_back(self):
        self.make('render_1', 'render_2')
        before = tree(self.proj / 'render_1')
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(names(result['moved']), ['render_1'])
        self.assertFalse((self.proj / 'render_1').exists())
        place = Path(result['trash'])
        self.assertEqual(place.parent, self.proj / clean.TRASH)
        self.assertEqual(tree(place / 'render_1'), before)
        os.replace(place / 'render_1', self.proj / 'render_1')
        self.assertEqual(tree(self.proj / 'render_1'), before)

    def test_the_skill_holds_no_code_that_deletes_a_file(self):
        calls = re.compile(r'\bos\.(?:remove|unlink|rmdir|removedirs)\b|\.unlink\(|\.rmdir\(|\brmtree\b')
        for path in helpers.SCRIPTS.rglob('*.py'):
            self.assertIsNone(calls.search(path.read_text(encoding='utf-8')), path.name)

    def test_git_and_godot_pass_the_trash_by(self):
        self.make('render_1', 'render_2')
        place = Path(clean.clean(self.proj, ['render_2'], move=True)['trash'])
        self.assertTrue((self.proj / clean.TRASH / '.gdignore').is_file())
        env = {k: v for k, v in helpers.child_env().items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')}
        self.assertEqual(subprocess.run(['git', 'init', '-q'], cwd=self.proj, env=env, capture_output=True).returncode, 0)
        rel = f'{clean.TRASH}/{place.name}/render_1/resolved.json'
        self.assertEqual(subprocess.run(['git', 'check-ignore', '-q', rel], cwd=self.proj, env=env).returncode, 0)

    def test_two_cleans_do_not_share_a_folder(self):
        self.make('render_1', 'render_2', 'render_3')
        with mock.patch.object(clean.time, 'strftime', return_value='20260101-000000'):     # both within one second
            first = clean.clean(self.proj, ['render_2'], move=True)['trash']
            second = clean.clean(self.proj, ['render_3'], move=True)['trash']
        self.assertNotEqual(first, second)
        self.assertTrue((Path(first) / 'render_1').is_dir())
        self.assertTrue((Path(second) / 'render_2').is_dir())

    def test_what_is_in_the_trash_is_not_looked_at_again(self):
        self.make('render_1', 'render_2', 'render_3')
        clean.clean(self.proj, ['render_2'], move=True)
        p = clean.plan(self.proj, ['render_3'])
        self.assertEqual(names(p['move']), ['render_2'])
        self.assertNotIn(clean.TRASH, names(p['left']))
        self.assertGreater(p['trash_bytes'], 0)

    def test_a_trash_that_is_a_link_is_refused(self):
        self.make('render_1', 'render_2')
        outside = self.proj.parent / 'outside'
        outside.mkdir()
        link_dir(self.proj / clean.TRASH, outside)
        with self.assertRaises(clean.CleanError):
            clean.clean(self.proj, ['render_2'], move=True)
        self.assertTrue((self.proj / 'render_1' / 'resolved.json').is_file())
        self.assertEqual(list(outside.iterdir()), [])


    def test_a_trash_with_nothing_left_in_it_holds_nothing(self):
        self.make('render_1', 'render_2')
        place = Path(clean.clean(self.proj, ['render_2'], move=True)['trash'])
        os.replace(place / 'render_1', self.proj / 'render_1')
        self.assertEqual(clean.plan(self.proj, ['render_2'])['trash_bytes'], 0)

    def test_the_trash_is_never_taken_for_a_render(self):
        self.make('render_1', 'render_2')
        trash = self.proj / clean.TRASH
        trash.mkdir()
        for name in ('resolved.json', 'files.json'):
            shutil.copyfile(self.proj / 'render_1' / name, trash / name)
        p = clean.plan(self.proj, ['render_2'])
        self.assertNotIn(clean.TRASH, names(p['move']) + names(p['left']))

    def test_a_file_named_like_the_trash_is_refused(self):
        self.make('render_1', 'render_2')
        (self.proj / clean.TRASH).write_text('mine', encoding='utf-8')
        with self.assertRaises(clean.CleanError):
            clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual((self.proj / clean.TRASH).read_text(encoding='utf-8'), 'mine')
        self.assertTrue((self.proj / 'render_1' / 'resolved.json').is_file())

    def test_a_trash_that_cannot_be_made_stops_the_move(self):
        self.make('render_1', 'render_2')
        with mock.patch.object(clean.Path, 'mkdir', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(clean.CleanError):
                clean.clean(self.proj, ['render_2'], move=True)
        self.assertTrue((self.proj / 'render_1' / 'resolved.json').is_file())


class AFolderStaysWhole(Project):
    def test_a_note_keeps_it(self):
        self.make('render_1', 'render_2')
        (self.proj / 'render_1' / 'notes.txt').write_text('my listening notes', encoding='utf-8')
        before = tree(self.proj / 'render_1')
        self.assertEqual(clean.plan(self.proj, ['render_2'])['move'], [])       # the list says so before anything is asked
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(result['moved'], [])
        self.assertEqual(tree(self.proj / 'render_1'), before)
        self.assertEqual(result['left'][0]['stays'], [{'path': 'notes.txt', 'why': 'not made by this skill'}])

    def test_a_changed_file_keeps_it(self):
        self.make('render_1', 'render_2')
        with open(self.proj / 'render_1' / 'master' / 'tick_01.wav', 'ab') as f:
            f.write(b'\0\0')
        before = tree(self.proj / 'render_1')
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(result['moved'], [])
        self.assertEqual(tree(self.proj / 'render_1'), before)
        self.assertEqual(result['left'][0]['stays'], [{'path': 'master/tick_01.wav', 'why': 'changed since it was written'}])

    def test_a_link_inside_keeps_it_and_is_not_followed(self):
        self.make('render_1', 'render_2')
        outside = self.proj.parent / 'outside'
        outside.mkdir()
        (outside / 'theirs.txt').write_text('theirs', encoding='utf-8')
        link_dir(self.proj / 'render_1' / 'linked', outside)
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(result['moved'], [])
        self.assertEqual(result['left'][0]['stays'], [{'path': 'linked', 'why': 'a link'}])
        self.assertEqual((outside / 'theirs.txt').read_text(encoding='utf-8'), 'theirs')

    def test_a_folder_of_theirs_keeps_it_even_when_empty(self):
        self.make('render_1', 'render_2')
        (self.proj / 'render_1' / 'my_voice_sessions').mkdir()
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(result['moved'], [])
        self.assertEqual(result['left'][0]['stays'], [{'path': 'my_voice_sessions', 'why': 'not made by this skill'}])

    def test_a_folder_that_changes_after_the_plan_stays(self):
        self.make('render_1', 'render_2')
        p = clean.plan(self.proj, ['render_2'])
        (self.proj / 'render_1' / 'late.txt').write_text('written meanwhile', encoding='utf-8')
        result = clean.move_planned(p)
        self.assertEqual(result['moved'], [])
        self.assertTrue((self.proj / 'render_1' / 'late.txt').is_file())
        self.assertIn('render_1', names(result['left']))

    def test_a_folder_in_use_stays_and_is_named(self):
        self.make('render_1', 'render_2')

        def busy(source, target):
            raise PermissionError(13, 'in use')
        result = clean.clean(self.proj, ['render_2'], move=True, rename=busy)
        self.assertEqual(result['moved'], [])
        self.assertIn('render_1', names(result['left']))
        self.assertTrue((self.proj / 'render_1' / 'resolved.json').is_file())

    def test_a_spectrogram_of_the_render_is_its_own(self):
        self.make('render_1', 'render_2')
        self.assertEqual(self.run_cli('spectrogram', self.proj / 'render_1').returncode, 0)
        self.assertEqual(names(clean.plan(self.proj, ['render_2'])['move']), ['render_1'])

    def test_a_listing_that_cannot_be_read_keeps_it(self):
        self.make('render_1', 'render_2')
        (self.proj / 'render_1' / 'files.json').write_text('{ not json', encoding='utf-8')
        p = clean.plan(self.proj, ['render_2'])
        self.assertEqual(p['move'], [])
        self.assertIn('render_1', names(p['left']))


class WhatIsNewerStays(Project):
    def test_a_render_after_the_kept_one_stays(self):
        self.make('render_1', 'render_2', 'render_3')
        p = clean.plan(self.proj, ['render_2'])
        self.assertEqual(names(p['move']), ['render_1'])
        self.assertEqual(names(p['left']), ['render_3'])
        self.assertIn('newer', p['left'][0]['why'])

    def test_with_two_kept_the_newest_counts(self):
        self.make('render_1', 'render_2', 'render_3', 'render_4')
        p = clean.plan(self.proj, ['render_1', 'render_3'])
        self.assertEqual(names(p['move']), ['render_2'])
        self.assertEqual(names(p['left']), ['render_4'])

    def test_rebuilding_a_page_does_not_make_a_render_newer(self):
        self.make('render_1', 'render_2')
        self.assertEqual(self.run_cli('audition', self.proj / 'render_1').returncode, 0)
        self.assertEqual(names(clean.plan(self.proj, ['render_2'])['move']), ['render_1'])

    def test_each_project_goes_by_its_own_kept_version(self):
        self.make('ui_1', 'ui_2', title='UI')
        self.make('foley_1', title='Foley')
        p = clean.plan(self.proj, ['ui_1', 'foley_1'])
        self.assertEqual(p['move'], [])
        self.assertEqual(names(p['left']), ['ui_2'])
        self.assertIn('newer', p['left'][0]['why'])

    def test_a_title_that_is_not_text_does_not_stop_it(self):
        self.make('render_1', 'render_2')
        path = self.proj / 'render_1' / 'resolved.json'
        import json
        data = json.loads(path.read_text(encoding='utf-8'))
        data['title'] = ['not', 'text']
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(names(clean.plan(self.proj, ['render_2'])['left']), ['render_1'])


class ExportsStay(Project):
    def setUp(self):
        super().setUp()
        self.make('render_1')
        self.exported('render_1', 'export_1')
        self.make('render_2')
        self.exported('render_2', 'export_2')

    def test_they_are_not_touched_by_default(self):
        before = tree(self.proj / 'export_1')
        result = clean.clean(self.proj, ['render_2'], move=True)
        self.assertEqual(names(result['moved']), ['render_1'])
        self.assertEqual(tree(self.proj / 'export_1'), before)
        self.assertEqual(sorted(names(result['left'])), ['export_1', 'export_2'])

    def test_asked_for_an_old_one_goes_and_the_kept_renders_stays(self):
        result = clean.clean(self.proj, ['render_2'], move=True, exports=True)
        self.assertEqual(sorted(names(result['moved'])), ['export_1', 'render_1'])
        self.assertEqual(names(result['left']), ['export_2'])
        self.assertIn('kept render', result['left'][0]['why'])


class Refusals(Project):
    def test_what_cannot_be_kept(self):
        self.make('render_1', 'render_2')
        (self.proj / 'misc').mkdir()
        (self.proj / 'misc' / 'readme.txt').write_text('not a render', encoding='utf-8')
        for keep in ([], [self.proj / 'misc'], [self.proj / 'render_2' / 'master']):
            with self.assertRaises(clean.CleanError):
                clean.plan(self.proj, keep)

    def test_an_export_cannot_be_the_version_kept(self):
        self.make('render_1', 'render_2')
        self.exported('render_1', 'approved')
        with self.assertRaisesRegex(clean.CleanError, 'name the render'):
            clean.plan(self.proj, ['approved'])

    def test_another_project_and_other_folders_are_left_alone(self):
        self.make('render_1', 'render_2')
        self.make('other_game', title='Another game')
        (self.proj / 'misc').mkdir()
        p = clean.plan(self.proj, ['render_2'])
        self.assertEqual(names(p['move']), ['render_1'])
        self.assertEqual(names(p['left']), ['other_game'])
        self.assertIn('another project', p['left'][0]['why'])


class CommandLine(Project):
    def test_it_lists_and_moves_nothing(self):
        self.make('render_1', 'render_2')
        before = tree(self.proj)
        proc = self.run_cli('clean', self.proj, '--keep', 'render_2')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('would move to .sfx-trash: render_1', proc.stdout)
        self.assertIn('nothing was moved', proc.stdout)
        self.assertEqual(tree(self.proj), before)

    def test_with_trash_it_moves_and_says_how_to_undo_it(self):
        self.make('render_1', 'render_2')
        proc = self.run_cli('clean', self.proj, '--keep', 'render_2', '--trash')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('moved to .sfx-trash: render_1', proc.stdout)
        self.assertIn('move it back', proc.stdout)
        self.assertFalse((self.proj / 'render_1').exists())

    def test_it_will_not_move_downloads(self):
        self.proj.mkdir(parents=True)
        proc = self.run_cli('clean', self.proj, '--downloads', '--trash')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('only lists', proc.stdout)


if __name__ == '__main__':
    unittest.main()
