"""What a careful reading of the skill against its promises found: commands that could damage a person's files or read past
their folder, a licence misread, numbers that were not what they said, and valid projects that failed."""
import json
import math
import os
import subprocess
import sys
import unittest
from unittest import mock

import numpy as np

import helpers
import test_samples as ts
from test_unreported_faults import checked, cli, errors, kinds, noise_recording, pack, rec, rendered, tone
import sfxkit
from sfxlib import clean, denoise, dynamics, fsd50k, licences, loudness, project, render, samples, stretch, wav

RATE = 48000
BLIP = {'id': 'blip', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'pitch': 880, 'length': 0.1}]}
DOUBLE = dict(BLIP, variants=2)


def strict(path):
    def refuse(name):
        raise ValueError(f'{name} in JSON')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=refuse)


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


class CleaningKeepsWhatAPersonAdded(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch('hardening_clean_' + self._testMethodName[5:45])
        for name in ('r1', 'r2'):
            render.render(checked([BLIP]), self.base / name)

    def stays(self):
        planned = clean.plan(self.base, [self.base / 'r2'])
        item = next((i for i in planned['left'] if i['folder'] == 'r1'), {})
        return {s['path']: s['why'] for s in item.get('stays', [])}

    def test_a_rebuilt_page_does_not_adopt_a_note(self):
        (self.base / 'r1' / 'listening-notes.txt').write_text('keep the second one', encoding='utf-8')
        self.assertIn('listening-notes.txt', self.stays())
        self.assertEqual(cli('audition', self.base / 'r1').returncode, 0)
        self.assertIn('listening-notes.txt', self.stays())

    def test_a_rebuilt_page_does_not_adopt_an_edited_master(self):
        master = self.base / 'r1' / 'master' / 'blip_01.wav'
        master.write_bytes(master.read_bytes() + b'\0\0')
        self.assertEqual(cli('audition', self.base / 'r1').returncode, 0)
        self.assertEqual(self.stays().get('master/blip_01.wav'), 'changed since it was written')

    def test_the_page_it_rebuilt_is_still_its_own(self):
        self.assertEqual(cli('audition', self.base / 'r1').returncode, 0)
        self.assertEqual(self.stays(), {})

    @unittest.skipUnless(os.name == 'nt', 'a directory junction is a Windows thing')
    def test_a_junction_is_not_followed(self):
        outside = helpers.scratch('hardening_clean_outside')
        render.render(checked([BLIP]), outside / 'theirs')
        link = self.base / 'linked'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside / 'theirs')], check=True, capture_output=True)
        self.assertNotIn('linked', [i['folder'] for i in clean.plan(self.base, [self.base / 'r2'])['move']])
        with self.assertRaises(clean.CleanError):
            clean.plan(self.base, [link])
        clean.clean(self.base, [self.base / 'r2'], move=True)
        self.assertTrue((outside / 'theirs' / 'resolved.json').is_file())


class ARenderFolderIsNotTrusted(unittest.TestCase):
    def tampered(self, name, change):
        base = helpers.scratch(f'hardening_record_{name}')
        render.render(checked([DOUBLE]), base / 'deep' / 'r')
        path = base / 'deep' / 'r' / 'resolved.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        change(data['sounds'][0])
        write(path, data)
        return base, base / 'deep' / 'r'

    def test_a_sound_id_cannot_write_outside_an_export(self):
        base, r = self.tampered('id', lambda s: s.update(id='../../escaped'))
        proc = cli('export', r, '--target', 'godot', '--output', base / 'deep' / 'out')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        self.assertEqual(list(base.rglob('escaped*')), [])

    def test_a_variant_cannot_pull_a_file_from_elsewhere_into_the_page(self):
        base, r = self.tampered('file', lambda s: s['variants'][0].update(file='../../private.wav'))
        wav.write(base / 'private.wav', tone(440, 0.2), rate=RATE, bits=16)
        before = (r / 'audition.html').read_bytes()
        proc = cli('audition', r)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        self.assertEqual((r / 'audition.html').read_bytes(), before)


class APictureDoesNotReplaceAFile(unittest.TestCase):
    def test_a_wav_beside_artwork_of_its_name(self):
        base = helpers.scratch('hardening_picture')
        wav.write(base / 'cover.wav', tone(440, 0.3), rate=RATE, bits=16)
        (base / 'cover.png').write_bytes(b'the artwork')
        proc = cli('spectrogram', base / 'cover.wav')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((base / 'cover.png').read_bytes(), b'the artwork')
        self.assertTrue((base / 'cover.spectrogram.png').is_file())


class ADownloadDoesNotWriteThroughALink(unittest.TestCase):
    def test_a_partial_file_that_is_a_link_is_refused(self):
        base = helpers.scratch('hardening_part')
        victim = base / 'victim.bin'
        victim.write_bytes(b'keep me')
        try:
            os.symlink(victim, base / 'data.zip.part')
        except OSError:
            self.skipTest('this system does not let a test make a link')
        with self.assertRaises(fsd50k.FSD50KError):
            fsd50k._fetch(lambda *a: self.fail('nothing should be fetched'), 'http://x/', base, 'data.zip', 10, lambda m: None,
                          sleep=lambda s: None)
        self.assertEqual(victim.read_bytes(), b'keep me')


class NamesThatDifferOnlyInCase(unittest.TestCase):
    def test_both_recordings_survive(self):
        base = helpers.scratch('hardening_case')
        members = {'one/Hit.wav': ts.wav_bytes(tone(300, 0.3), rate=RATE), 'two/hit.wav': ts.wav_bytes(tone(600, 0.3), rate=RATE),
                   'License.txt': ts.CC0_TEXT.encode('utf-8')}
        report = samples.add(ts.as_zip(base / 'Pack.zip', members), base / 'library', licence='CC0-1.0')
        self.assertEqual(report['added'], 2)
        rows = samples.load_index(base / 'library')['files']
        self.assertEqual(len({r['path'].casefold() for r in rows}), 2)
        for r in rows:
            self.assertEqual(samples._sha256((base / 'library' / r['path']).read_bytes()), r['sha256'])


class WhatALicenceCovers(unittest.TestCase):
    def test_a_plain_grant_for_the_whole_pack_raises_no_doubt(self):
        for text in (ts.CC0_TEXT, 'All sounds in this pack are CC0.', 'Art, music and sound effects: CC0 1.0.\nCredit is not required.'):
            self.assertEqual(licences.doubts(text), [], text)

    def test_a_credit_gives_what_cc_by_asks(self):
        line = licences.credit({'recording': 'Door', 'author': 'Alice', 'licence': 'CC-BY-4.0',
                                'page': 'https://example.org/door'})
        for part in ('"Door"', 'Alice', 'https://example.org/door', 'CC-BY-4.0',
                     'https://creativecommons.org/licenses/by/4.0/', 'changed'):
            self.assertIn(part, line)

    def test_the_note_says_to_keep_the_notices(self):
        text = licences.note([{'recording': 'Door', 'author': 'Alice', 'licence': 'CC-BY-4.0', 'conditions': ['attribution']}])
        self.assertIn('notice', text)

    def test_share_alike_licences_that_cannot_be_mixed(self):
        self.assertTrue(licences.clash(['CC-BY-SA-4.0', 'CC-BY-NC-SA-4.0']))
        self.assertTrue(licences.clash(['CC-BY-SA-4.0', 'CC-BY-NC-4.0']))
        for fine in (['CC-BY-4.0', 'CC-BY-SA-4.0'], ['CC-BY-NC-4.0', 'CC-BY-NC-SA-4.0'], ['CC0-1.0', 'CC-BY-SA-4.0'], ['CC0-1.0']):
            self.assertEqual(licences.clash(fine), '', fine)

    def test_a_render_refuses_to_mix_them_in_one_sound(self):
        base = helpers.scratch('hardening_clash')
        library = base / 'library'
        for name, licence, freq in (('Sa', 'CC-BY-SA-4.0', 300), ('Nc', 'CC-BY-NC-SA-4.0', 500)):
            source = ts.as_folder(base / name, {'x.wav': ts.wav_bytes(tone(freq, 0.3), rate=RATE)})
            samples.add(source, library, licence=licence, author='A', pack=name.lower())
        layer = lambda p: {'source': {'type': 'sample', 'library': p, 'file': 'x'}, 'length': 0.2}
        with self.assertRaises(render.RenderError) as caught:
            render.render(checked([{'id': 'mix', 'category': 'foley', 'layers': [layer('sa'), layer('nc')]}]), base / 'r', samples=library)
        self.assertIn('mix', str(caught.exception))


class TheReleaseCheck(unittest.TestCase):
    def test_a_full_check_with_no_release_named_fails(self):
        import check_release
        online = AssertionError('the check went online')
        with mock.patch.object(check_release.fsd50k, 'RELEASE_URL', ''), \
                mock.patch.object(check_release, 'check_links', side_effect=online), \
                mock.patch.object(check_release, 'check_install', side_effect=online), \
                mock.patch.object(sys, 'argv', ['check_release.py', '--full', 'data', 'library']):
            with self.assertRaises(SystemExit) as caught:
                check_release.main()
        self.assertIn('names no release', str(caught.exception))

    def test_a_full_check_downloads_and_leaves_the_links_to_the_other_job(self):
        import check_release
        for argv, wanted in ((['--full', 'data', 'library'], ['install']), ([], ['links'])):
            called = []
            with mock.patch.object(check_release.fsd50k, 'RELEASE_URL', 'https://example.invalid/release'), \
                    mock.patch.object(check_release, 'check_links', side_effect=lambda: called.append('links')), \
                    mock.patch.object(check_release, 'check_install', side_effect=lambda *a: called.append('install')), \
                    mock.patch.object(sys, 'argv', ['check_release.py'] + argv):
                check_release.main()
            with self.subTest(argv=argv):
                self.assertEqual(called, wanted)        # the download checks the index and every part by their sums

    def test_a_dataset_record_that_refuses_the_runner_is_not_a_missing_one(self):
        import urllib.error
        import check_release
        refused = lambda code: urllib.error.HTTPError(check_release.DATASET, code, 'refused', {}, None)
        for code in (403, 429):                         # a site refusing a shared cloud runner: said, and the check goes on
            with self.subTest(code=code), mock.patch.object(check_release, 'size_of', side_effect=refused(code)):
                check_release.dataset_answers()
        for code in (404, 410, 500):                    # a record that is gone, or a site that fails
            with self.subTest(code=code), mock.patch.object(check_release, 'size_of', side_effect=refused(code)), \
                    self.assertRaises(urllib.error.HTTPError):
                check_release.dataset_answers()


class TheDocumentsSayWhatHappens(unittest.TestCase):
    SKILL = helpers.REPO / 'skills' / 'sound-effects'

    def test_work_goes_in_the_project_and_not_in_the_skill(self):
        text = (self.SKILL / 'SKILL.md').read_text(encoding='utf-8')
        self.assertNotIn("Commands run from this skill's folder", text)
        self.assertIn("from the game's project folder", text)

    def test_the_noise_reduction_is_described_as_it_works(self):
        text = (self.SKILL / 'references' / 'project-format.md').read_text(encoding='utf-8')
        self.assertNotIn('taking away at most', text)

    def test_the_install_line_works_from_where_it_is_read(self):
        text = (helpers.REPO / 'README.md').read_text(encoding='utf-8')
        self.assertIn('requirements.txt', text)
        self.assertIn("from this repository's folder", text)


class MastersAreAsARenderWroteThem(unittest.TestCase):
    def test_a_master_at_another_rate_is_refused(self):
        base = helpers.scratch('hardening_rate')
        render.render(checked([BLIP]), base / 'r')
        master = base / 'r' / 'master' / 'blip_01.wav'
        master.write_bytes(wav.encode(tone(441, 1.0), rate=44100, bits=24))
        for command in (['check', base / 'r'], ['export', base / 'r', '--target', 'unity', '--output', base / 'out']):
            proc = cli(*command)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('44100', proc.stdout)
            self.assertNotIn('Traceback', proc.stdout + proc.stderr)


class LoopsJoin(unittest.TestCase):
    @staticmethod
    def bed(env):
        return {'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 1},
                'layers': [{'source': {'type': 'osc'}, 'pitch': 220, 'length': 1.0, 'env': env}]}

    def test_an_envelope_read_at_the_loops_end(self):
        self.assertTrue(errors(self.bed([[0, 0], [1, 1], [2, 0]])))

    def test_an_envelope_that_returns_is_accepted(self):
        self.assertEqual(errors(self.bed([[0, 0.5], [0.5, 1], [1, 0.5]])), [])

    def test_one_that_holds_past_its_last_point(self):
        self.assertEqual(errors(self.bed([[0, 0.7], [0.5, 0.7]])), [])


class QuietSounds(unittest.TestCase):
    def test_a_loop_under_the_gate_leaves_valid_json(self):
        base = helpers.scratch('hardening_quiet')
        sound = {'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'variants': 2, 'trim_db': -24,
                 'layers': [{'source': {'type': 'noise'}, 'length': 2.0}]}
        rendered(base, [sound], level={'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 0.0, 'target_lufs': -55.0})
        strict(base / 'r' / 'check.json')

    def test_peak_mode_levels_a_very_quiet_loop(self):
        base = helpers.scratch('hardening_quiet_peak')
        sound = {'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 1}, 'variants': 1,
                 'layers': [{'source': {'type': 'osc'}, 'pitch': 220, 'length': 1.0, 'gain_db': -80}]}
        _, resolved = rendered(base, [sound], level={'mode': 'peak', 'ceiling_dbtp': -1.0})
        self.assertGreater(resolved['sounds'][0]['variants'][0]['gain_db'], 60.0)


class TheLimiterReportsWhatItDid(unittest.TestCase):
    def test_a_loop_reports_what_its_period_lost(self):
        x = 0.5 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)
        x[0] = 1.0
        audio = (x * 10 ** (5.0 / 20))[:, None]
        y, said = render._limit(audio, -1.0, True)
        first = 20 * math.log10(float(np.max(np.abs(audio))) / float(np.max(np.abs(y))))
        self.assertAlmostEqual(said, first, delta=0.35)


class TheLevelAdvice(unittest.TestCase):
    def test_it_counts_the_level_asked_for(self):
        tick = {'id': 'tick', 'category': 'ui', 'layers': [{'source': {'type': 'noise', 'color': 'white'}, 'length': 0.003}]}
        pad = {'id': 'pad', 'category': 'scifi', 'layers': [{'source': {'type': 'osc'}, 'pitch': 220, 'length': 1.0}]}
        level = {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': 0.0, 'target_lufs': -15.0}
        base = helpers.scratch('hardening_advice')
        _, first = rendered(base, [tick, pad], level=level)
        said = first['level']['next']['rise_db']
        _, second = rendered(base, [dict(tick, trim_db=-said), pad], name='r2', level=level)
        self.assertAlmostEqual(second['level']['target_lufs'] - first['level']['target_lufs'], said, delta=0.25)


class ValidProjectsRender(unittest.TestCase):
    def test_a_take_late_in_a_long_recording(self):
        base = helpers.scratch('hardening_late')
        x = noise_recording(40.0, amp=0.01)
        x[35 * RATE:35 * RATE + 2400, 0] += 0.8
        library = pack(base, long=x)
        out = cli('samples', '--library', library, '--events', 'pack/long').stdout
        takes = json.loads(out[out.index('[{'):].splitlines()[0])
        self.assertGreater(takes[-1]['offset'], 30.0)
        sound = {'id': 'x', 'category': 'foley', 'layers': [rec(file=[takes[-1]], length=0.1)]}
        self.assertEqual(errors(sound), [])
        rendered(base, [sound], library)

    def test_a_layer_of_one_millisecond(self):
        rendered(helpers.scratch('hardening_short_layer'), [{'id': 'x', 'category': 'ui', 'layers': [
            {'source': {'type': 'osc'}, 'pitch': 1200, 'length': 0.001}]}])

    def test_a_short_clean_click_is_not_called_noisy(self):
        x = np.zeros(4800)
        x[2000:2030] = 0.5 * np.sin(np.arange(30) * 0.7)
        self.assertNotIn('noisy', fsd50k.screen(fsd50k.measure(x, RATE), None))


class AStereoRecordingKeepsItsPlace(unittest.TestCase):
    def test_a_smeared_recording_panned_hard_left_stays_left(self):
        m = 0.1 * np.random.default_rng(6).standard_normal(2 * RATE)
        y = stretch.stretch(np.stack([m, np.zeros_like(m)], axis=1), 2.0, 'smear', seed=3)
        self.assertLess(float(np.max(np.abs(y[:, 1]))), 1e-9)
        self.assertGreater(float(np.max(np.abs(y[:, 0]))), 0.05)

    def test_a_centred_one_stays_centred(self):
        m = 0.1 * np.random.default_rng(5).standard_normal(2 * RATE)
        y = stretch.stretch(np.stack([m, m], axis=1), 2.0, 'smear', seed=3)
        self.assertGreater(float(np.corrcoef(y[:, 0], y[:, 1])[0, 1]), 0.99)

    def test_the_format_page_says_a_wide_recording_narrows(self):
        text = (helpers.REPO / 'skills' / 'sound-effects' / 'references' / 'project-format.md').read_text(encoding='utf-8')
        self.assertIn('comes out narrower', text)


class LinksInARenderFolder(unittest.TestCase):
    def linked(self, name, make):
        base = helpers.scratch(f'hardening_links_{name}')
        render.render(checked([BLIP]), base / 'r')
        outside = base / 'outside.wav'
        wav.write(outside, tone(440, 0.2), rate=RATE, bits=24)
        try:
            make(base / 'r', outside)
        except OSError:
            self.skipTest('this system does not let a test make a link')
        return base

    def test_a_master_that_is_a_link_is_refused(self):
        def make(r, outside):
            (r / 'master' / 'blip_01.wav').replace(r / 'moved.wav')
            os.symlink(outside, r / 'master' / 'blip_01.wav')
        base = self.linked('master', make)
        proc = cli('audition', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('link', proc.stdout)

    def test_a_page_that_is_a_link_is_not_written_through(self):
        def make(r, outside):
            (r / 'audition.html').replace(r / 'moved.html')
            (r.parent / 'target.html').write_text('theirs', encoding='utf-8')
            os.symlink(r.parent / 'target.html', r / 'audition.html')
        base = self.linked('page', make)
        proc = cli('audition', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual((base / 'target.html').read_text(encoding='utf-8'), 'theirs')

    @unittest.skipUnless(os.name == 'nt', 'a directory junction is a Windows thing')
    def test_clean_does_not_go_through_a_junction_inside_a_render(self):
        base = helpers.scratch('hardening_links_inner')
        for name in ('r1', 'r2'):
            render.render(checked([BLIP]), base / 'project' / name)
        library = helpers.scratch('hardening_links_library')
        (library / 'incoming').mkdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(base / 'project' / 'r1' / 'linked'), str(library)], check=True,
                       capture_output=True)
        clean.clean(base / 'project', [base / 'project' / 'r2'], move=True)
        self.assertTrue((library / 'incoming').is_dir())

    @unittest.skipUnless(os.name == 'nt', 'needs a file system that ignores case')
    def test_the_export_of_a_kept_render_stays_whatever_the_case_of_its_name(self):
        from sfxlib import export
        base = helpers.scratch('hardening_keep_case')
        render.render(checked([BLIP]), base / 'render_3')
        render.render(checked([BLIP]), base / 'render_1')
        export.export(base / 'render_3', base / 'godot', 'godot')
        planned = clean.plan(base, [base / 'RENDER_3'], exports=True)
        self.assertIn('kept render', {i['folder']: i['why'] for i in planned['left']}['godot'])


class WhoAndWhatALicenceCovers(unittest.TestCase):
    def test_a_second_pack_of_the_same_name_does_not_take_the_first_authors_name(self):
        base = helpers.scratch('hardening_same_pack')
        library = base / 'library'
        for who, freq in (('Alice', 300), ('Bob', 500)):
            (base / who).mkdir()
            ts.as_folder(base / who / 'Footsteps', {'s.wav': ts.wav_bytes(tone(freq, 0.3), rate=RATE)})
        samples.add(base / 'Alice' / 'Footsteps', library, licence='CC-BY-4.0', author='Alice')
        with self.assertRaises(samples.SampleError) as caught:
            samples.add(base / 'Bob' / 'Footsteps', library, licence='CC-BY-4.0')
        self.assertIn('Alice', str(caught.exception))
        self.assertEqual(samples.add(base / 'Bob' / 'Footsteps', library, licence='CC-BY-4.0', author='Alice')['added'], 1)

    def base_with_nc(self, name):
        base = helpers.scratch(name)
        samples.add(ts.as_folder(base / 'Nc', {'x.wav': ts.wav_bytes(tone(300, 0.4), rate=RATE)}), base / 'library', licence='CC-BY-NC-4.0',
                    author='A', pack='nc')
        return base

    def test_the_note_names_only_the_sounds_that_use_the_recording(self):
        base = self.base_with_nc('hardening_note_sounds')
        step = {'id': 'step', 'category': 'foley', 'layers': [{'source': {'type': 'sample', 'library': 'nc', 'file': 'x'}, 'length': 0.2}]}
        _, resolved = rendered(base, [BLIP, step], base / 'library')
        self.assertEqual([s['id'] for s in resolved['sounds'] if s.get('recordings')], ['step'])
        text = licences.note(resolved['recordings'], resolved['sounds'])
        self.assertIn('step', text)
        self.assertNotIn('blip', text)
        self.assertIn('Only', text)

    def test_takes_that_never_play_together_do_not_clash(self):
        base = helpers.scratch('hardening_takes_apart')
        library = base / 'library'
        for name, licence, freq in (('Sa', 'CC-BY-SA-4.0', 300), ('Nc', 'CC-BY-NC-4.0', 500)):
            samples.add(ts.as_folder(base / name, {'x.wav': ts.wav_bytes(tone(freq, 0.3), rate=RATE)}), library, licence=licence, author='A',
                        pack=name.lower())
        layer = {'source': {'type': 'sample', 'library': 'sa', 'file': ['x', {'library': 'nc', 'file': 'x'}]}, 'length': 0.2}
        rendered(base, [{'id': 'mix', 'category': 'foley', 'variants': 2, 'layers': [layer]}], library)


class ReadingLoopsAndRecordings(unittest.TestCase):
    def test_a_wind_bed_from_the_builder_stays_at_full_level(self):
        s = sfxkit.sound('w', 'ambience', 'wind', [sfxkit.wind(0.0, 2.0)], use='loop', loop={'seconds': 2})
        self.assertNotIn('env', s['layers'][0])

    def test_a_loop_with_a_silent_half_is_flagged(self):
        report, _ = rendered(helpers.scratch('hardening_silent_half'), [
            {'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 4}, 'variants': 1, 'layers': [
                {'source': {'type': 'osc'}, 'pitch': 220, 'start': 0.2, 'length': 1.0}]}])
        self.assertEqual(len(kinds(report, 'loop_halves')), 1)

    def test_a_strike_at_the_very_start_is_an_event(self):
        x = np.zeros(RATE)
        for at in (0, RATE // 2):
            x[at:at + 4800] += 0.8 * np.exp(-np.arange(4800) / 600) * np.sign(np.sin(np.arange(4800) * 0.31))
        found = denoise.events(x, RATE)
        self.assertEqual(len(found), 2)
        self.assertLess(found[0]['onset'], 0.005)

    def test_a_steady_recording_has_no_event_at_its_start(self):
        x = 0.3 * np.random.default_rng(2).standard_normal(RATE)
        self.assertEqual(denoise.events(x, RATE), [])

    def test_low_tones_have_a_pitch(self):
        for hz in (50, 60, 80, 100, 220, 1000):
            frame = 0.5 * np.sin(2 * np.pi * hz * np.arange(4800) / RATE)
            self.assertAlmostEqual(denoise._pitch(frame, RATE), hz, delta=max(2.0, hz * 0.03), msg=str(hz))

    def test_leaving_out_a_word_also_reads_a_plain_label(self):
        row = {'library': 'p', 'file': '1', 'title': 'door', 'tags': [], 'labels': ['Speech', 'Male_speech_and_man_speaking'], 'seconds': 1.0,
               'channels': 1, 'measures': {'range_db': 40.0}, 'screen': []}
        index = {'packs': {'p': {'title': 'P', 'licence': 'CC0-1.0', 'files': 1}}, 'files': [row]}
        self.assertEqual(samples.find(index, 'door', exclude=['speech']), [])

    def test_a_sound_rendered_alone_at_a_held_level_is_told_the_set_can_still_pull_it_down(self):
        folder = helpers.scratch('hardening_alone_held')
        path = folder / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'level': {'target_lufs': -16},
                                    'sounds': [BLIP, dict(BLIP, id='other')]}), encoding='utf-8')
        out = cli('render', path, '--output', folder / 'r', '--no-page', '--sound', 'blip').stdout
        self.assertIn('a sound left out', out)


class ThePage(unittest.TestCase):
    def test_it_plays_at_the_rate_of_its_files(self):
        base = helpers.scratch('hardening_page')
        render.render(checked([BLIP]), base / 'r')
        self.assertIn('sampleRate: 48000', (base / 'r' / 'audition.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
