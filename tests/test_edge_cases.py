"""Narrow cases that give a wrong answer quietly: a licence line about something other than the sound, a link where the
skill reads or writes, a credit that lands on the wrong person, and readings that misjudge a clean recording."""
import hashlib
import io
import os
import stat
import types
import unittest
from unittest import mock

import numpy as np

import helpers
import test_samples as ts
from test_import_safety import link_dir
from test_unreported_faults import checked, cli, rendered, tone
import sfxkit
from sfxlib import check, denoise, export, fsd50k, licences, project, record, render, samples, spectrogram, wav

RATE = 48000
BLIP = {'id': 'blip', 'category': 'ui', 'layers': [{'source': {'type': 'osc'}, 'pitch': 880, 'length': 0.1}]}


def one(freq):
    return ts.wav_bytes(tone(freq, 0.3), rate=RATE)


class TheLicenceIsThePersonsToSay(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch('edge_licence_' + self._testMethodName[5:40])

    def pack(self, text, name='Pack'):
        return ts.as_folder(self.base / name, {'tap.wav': one(300), 'door.wav': one(500), 'README.txt': text.encode('utf-8')})

    def asked(self, source):
        with self.assertRaises(samples.SampleError) as caught:
            samples.add(source, self.base / 'library')
        self.assertFalse((self.base / 'library' / samples.INDEX).exists())
        return str(caught.exception)

    def test_nothing_is_imported_until_the_person_says(self):
        self.assertIn('ask the person', self.asked(self.pack(ts.CC0_TEXT)))

    def test_the_question_quotes_what_came_with_the_sounds(self):
        for i, text in enumerate(('Cover art: CC0. Audio: contact the recorder for permission.',
                                  'All sounds are CC0\nexcept door.wav.')):
            message = self.asked(self.pack(text, f'Pack{i}'))
            for line in text.splitlines():
                self.assertIn(line, message)

    def test_a_named_licence_goes_through(self):
        report = samples.add(self.pack(ts.CC0_TEXT), self.base / 'library', licence='CC0')
        self.assertEqual((report['licence'], report['added']), ('CC0-1.0', 2))
        self.assertIn('licence file', report['licence_status'])

    def test_the_files_still_stop_a_name_they_contradict(self):
        for i, text in enumerate(('Licensed under CC BY 4.0.', 'All rights reserved.')):
            with self.assertRaisesRegex(samples.SampleError, '--confirm-licence'):
                samples.add(self.pack(text, f'Pack{i}'), self.base / 'library', licence='CC0')
        self.assertFalse((self.base / 'library' / samples.INDEX).exists())

    def test_what_is_quoted_is_bounded_and_printable(self):
        long = 'CC0\n' + '\n'.join(f'line {i} \x1b[31m' + 'x' * 400 for i in range(500))
        members = {'tap.wav': one(300)}
        members.update({f'licence_{i}.txt': long.encode('utf-8') for i in range(20)})
        message = self.asked(ts.as_folder(self.base / 'Long', members))
        self.assertLess(len(message), 40000)
        self.assertNotIn('\x1b', message)
        self.assertIn('more lines', message)
        self.assertIn('more text files', message)

    def test_the_command_asks_and_imports_nothing(self):
        proc = cli('samples', '--library', self.base / 'library', '--add', self.pack('Sounds: CC0\nexcept door.wav.'))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('except door.wav', proc.stdout)
        self.assertIn('--licence', proc.stdout)
        self.assertFalse((self.base / 'library' / samples.INDEX).exists())


class ALinkIsNotFollowed(unittest.TestCase):
    def rendered(self, name):
        base = helpers.scratch(f'edge_link_{name}')
        render.render(checked([BLIP]), base / 'r', page=False)
        return base

    def test_a_picture_that_is_a_link_is_not_written_through(self):
        base = self.rendered('picture')
        victim = base / 'victim.png'
        victim.write_bytes(b'keep me')
        (base / 'r' / 'spectrograms').mkdir()
        try:
            os.symlink(victim, base / 'r' / 'spectrograms' / 'blip_01.png')
        except OSError:
            self.skipTest('this system does not let a test make a link')
        proc = cli('spectrogram', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(victim.read_bytes(), b'keep me')

    def test_a_pictures_folder_that_is_a_link_is_not_written_through(self):
        base = self.rendered('pictures')
        elsewhere = base / 'elsewhere'
        elsewhere.mkdir()
        link_dir(base / 'r' / 'spectrograms', elsewhere)
        proc = cli('spectrogram', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_master_that_is_a_link_is_not_drawn(self):
        base = self.rendered('master')
        outside = base / 'outside.wav'
        wav.write(outside, tone(440, 0.2), rate=RATE, bits=24)
        master = base / 'r' / 'master' / 'blip_01.wav'
        master.replace(base / 'r' / 'moved.wav')
        try:
            os.symlink(outside, master)
        except OSError:
            self.skipTest('this system does not let a test make a link')
        proc = cli('spectrogram', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('link', proc.stdout)

    def test_a_master_at_another_rate_is_not_drawn_as_a_render(self):
        base = self.rendered('rate')
        wav.write(base / 'r' / 'master' / 'blip_01.wav', tone(440, 0.2), rate=44100, bits=24)
        proc = cli('spectrogram', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_a_picture_someone_changed_is_not_drawn_over(self):
        base = self.rendered('annotated')
        self.assertEqual(cli('spectrogram', base / 'r').returncode, 0)
        picture = base / 'r' / 'spectrograms' / 'blip_01.png'
        picture.write_bytes(picture.read_bytes() + b'with my notes')
        marked = picture.read_bytes()
        proc = cli('spectrogram', base / 'r')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(picture.read_bytes(), marked)

    def test_a_picture_of_someone_elses_is_not_drawn_over(self):
        base = self.rendered('theirs')
        (base / 'r' / 'spectrograms').mkdir()
        (base / 'r' / 'spectrograms' / 'blip_01.png').write_bytes(b'mine')
        self.assertEqual(cli('spectrogram', base / 'r').returncode, 1)
        self.assertEqual((base / 'r' / 'spectrograms' / 'blip_01.png').read_bytes(), b'mine')

    def test_its_own_picture_can_be_drawn_again(self):
        base = self.rendered('again')
        for _ in range(2):
            proc = cli('spectrogram', base / 'r')
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_release_index_is_not_written_through_a_link(self):
        base = helpers.scratch('edge_link_index')
        victim = base / 'victim.json'
        victim.write_bytes(b'keep me')
        blob = b'{"parts": []}'
        try:
            os.symlink(victim, base / f'{fsd50k.PACK_INDEX}.part')
        except OSError:
            self.skipTest('this system does not let a test make a link')
        with self.assertRaises(fsd50k.FSD50KError):
            fsd50k.download_packs(base, url='http://example.invalid', index_sha256=hashlib.sha256(blob).hexdigest(),
                                  opener=lambda *a: io.BytesIO(blob), log=lambda m: None)
        self.assertEqual(victim.read_bytes(), b'keep me')

    def test_the_release_index_refuses_a_link_wherever_links_can_be_made(self):
        base = helpers.scratch('edge_link_index_seen')
        blob = b'{"parts": []}'
        with mock.patch.object(fsd50k, 'is_link', lambda path: str(path).endswith('.part')):
            with self.assertRaises(fsd50k.FSD50KError):
                fsd50k.download_packs(base, url='http://example.invalid', index_sha256=hashlib.sha256(blob).hexdigest(),
                                      opener=lambda *a: io.BytesIO(blob), log=lambda m: None)
        self.assertFalse((base / f'{fsd50k.PACK_INDEX}.part').exists())
        self.assertFalse((base / fsd50k.PACK_INDEX).exists())

    def test_a_folder_linked_into_a_pack_is_not_imported(self):
        base = helpers.scratch('edge_link_pack')
        outside = base / 'outside'
        outside.mkdir()
        (outside / 'theirs.wav').write_bytes(one(500))
        pack = ts.as_folder(base / 'Pack', {'mine.wav': one(300), 'License.txt': ts.CC0_TEXT.encode('utf-8')})
        link_dir(pack / 'more', outside)
        self.assertEqual(samples.add(pack, base / 'library', licence='CC0-1.0')['added'], 1)
        self.assertEqual([f['file'] for f in samples.load_index(base / 'library')['files']], ['mine'])

    def test_a_file_the_system_keeps_in_another_form_is_not_a_link(self):
        def seen(tag):
            return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_reparse_tag=tag,
                                         st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        for tag, expected in ((0x9000701A, False), (0x80000017, False), (0xA000000C, True), (0xA0000003, True)):
            with mock.patch.object(record.os, 'lstat', lambda path: seen(tag)):
                self.assertEqual(record.is_link('x.wav'), expected, hex(tag))


class Credits(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch('edge_credit_' + self._testMethodName[5:40])
        self.library = self.base / 'library'

    def add(self, name, freq, **kw):
        return samples.add(ts.as_folder(self.base / name, {f'{name.lower()}.wav': one(freq)}), self.library,
                           licence='CC-BY-4.0', pack='footsteps', **kw)

    def test_another_creator_in_the_same_pack_must_be_named(self):
        self.add('A', 300, author='Alice')
        with self.assertRaisesRegex(samples.SampleError, 'Alice'):
            self.add('B', 500)

    def test_another_creator_does_not_take_the_first_ones_page(self):
        self.add('A', 300, author='Alice', page='https://example.org/alice')
        self.add('B', 500, author='Bob')
        library = samples.Library(self.library)
        self.assertEqual((library.provenance('footsteps', 'b')['author'], library.provenance('footsteps', 'b')['page']),
                         ('Bob', ''))
        self.assertEqual((library.provenance('footsteps', 'a')['author'], library.provenance('footsteps', 'a')['page']),
                         ('Alice', 'https://example.org/alice'))

    def test_the_first_creator_does_not_take_a_later_ones_page(self):
        self.add('A', 300, author='Alice')
        self.add('B', 500, author='Bob', page='https://example.org/bob')
        library = samples.Library(self.library)
        self.assertEqual(library.provenance('footsteps', 'a')['page'], '')
        self.assertEqual(library.provenance('footsteps', 'b')['page'], 'https://example.org/bob')

    def test_more_by_the_same_creator_keeps_the_page(self):
        self.add('A', 300, author='Alice', page='https://example.org/alice')
        self.add('B', 500, author='Alice')
        self.assertEqual(samples.Library(self.library).provenance('footsteps', 'b')['page'], 'https://example.org/alice')

    def test_each_file_names_its_own_recordings(self):
        for name, licence, freq in (('Sa', 'CC-BY-SA-4.0', 300), ('Nc', 'CC-BY-NC-4.0', 500)):
            samples.add(ts.as_folder(self.base / name, {'x.wav': one(freq)}), self.library, licence=licence, author='A',
                        pack=name.lower())
        layer = {'source': {'type': 'sample', 'library': 'sa', 'file': ['x', {'library': 'nc', 'file': 'x'}]}, 'length': 0.2}
        _, resolved = rendered(self.base, [{'id': 'mix', 'category': 'foley', 'variants': 2, 'layers': [layer]}], self.library)
        self.assertEqual([v['recordings'] for v in resolved['sounds'][0]['variants']], [['sa/x'], ['nc/x']])
        text = licences.note(resolved['recordings'], resolved['sounds'])
        self.assertIn('mix_01: attribution, share-alike', text)
        self.assertIn('mix_02: attribution, non-commercial', text)
        manifest = export.export(self.base / 'r', self.base / 'out', 'generic', formats=('wav',))
        files = manifest['sounds'][0]['files']
        self.assertEqual({tuple(f['recordings']) for f in files if f['variant'] == 1}, {('sa/x',)})
        self.assertEqual({tuple(f['recordings']) for f in files if f['variant'] == 2}, {('nc/x',)})

    def test_files_that_ask_the_same_are_not_listed_one_by_one(self):
        self.add('A', 300, author='Alice')
        self.add('B', 500, author='Bob')
        layer = {'source': {'type': 'sample', 'library': 'footsteps', 'file': ['a', 'b']}, 'length': 0.2}
        _, resolved = rendered(self.base, [{'id': 'step', 'category': 'foley', 'variants': 2, 'layers': [layer]}], self.library)
        self.assertNotIn('step_01:', licences.note(resolved['recordings'], resolved['sounds']))


class EnvelopesOnABed(unittest.TestCase):
    def test_one_asked_for_stays(self):
        asked = sfxkit.clip(2, 0.2, 0.2)
        s = sfxkit.sound('b', 'ambience', 'n', [sfxkit.tone(0.0, 2.0, 220, env=asked)], use='loop', loop={'seconds': 2})
        self.assertEqual(s['layers'][0]['env'], asked)
        asked = sfxkit.decay(2)
        s = sfxkit.sound('b', 'ambience', 'n', [sfxkit.noise(0.0, 2.0, env=asked)], use='loop', loop={'seconds': 2})
        self.assertEqual(s['layers'][0]['env'], asked)

    def test_the_default_one_goes_at_the_default_length(self):
        for layer in (sfxkit.tone(0.0, 12.0, 220), sfxkit.noise(0.0, 12.0), sfxkit.wind(0.0, 12.0), sfxkit.fm(0.0, 12.0, 110)):
            s = sfxkit.sound('b', 'ambience', 'n', [layer], use='loop')
            self.assertNotIn('env', s['layers'][0])

    def test_a_layer_shorter_than_the_loop_keeps_it(self):
        s = sfxkit.sound('b', 'ambience', 'n', [sfxkit.tone(0.0, 2.0, 220)], use='loop', loop={'seconds': 4})
        self.assertIn('env', s['layers'][0])


class Readings(unittest.TestCase):
    def test_a_low_rate_recording_is_not_called_lossy(self):
        rate = 22050
        burst = np.random.default_rng(3).standard_normal(rate) * np.exp(-np.arange(rate) / (0.1 * rate)) * 0.4
        self.assertNotIn('lossy original', fsd50k.screen(fsd50k.measure(burst, rate), None))

    def test_a_full_rate_recording_with_no_top_is_still_called_lossy(self):
        rate = 44100
        burst = np.random.default_rng(3).standard_normal(rate) * np.exp(-np.arange(rate) / (0.1 * rate)) * 0.4
        spectrum = np.fft.rfft(burst)
        spectrum[np.fft.rfftfreq(rate, 1 / rate) > 15000] = 0
        self.assertIn('lossy original', fsd50k.screen(fsd50k.measure(np.fft.irfft(spectrum, rate), rate), None))

    def test_a_clipped_channel_of_a_stereo_recording_is_found(self):
        x = np.clip(0.2 * np.random.default_rng(1).standard_normal((RATE, 2)), -0.8, 0.8)
        x[1000:1012, 0] = 1.0
        x[:, 1] *= 0.2
        measured = fsd50k.measure(x, RATE)
        self.assertGreater(measured['clipped_runs'], 0)
        self.assertIn('clipped', fsd50k.screen(measured, None))

    def test_the_loudest_moment_of_a_44k_recording_is_where_it_is(self):
        rate = 44100
        x = np.zeros((rate * 60, 1))
        x[int(59.95 * rate):int(59.95 * rate) + 40] = 0.8
        _, scale = spectrogram.picture(x, rate, width=240)
        self.assertAlmostEqual(scale['peak_s'], 59.95, delta=0.002)

    def test_a_low_rate_recording_shows_nothing_above_what_it_can_hold(self):
        rate = 16000
        x = 0.3 * np.random.default_rng(2).standard_normal((rate, 1))
        image, _ = spectrogram.picture(x, rate, width=240)
        top = spectrogram.ENVELOPE_H + spectrogram.GAP
        row_hz = spectrogram.F_LO * (spectrogram.F_HI / spectrogram.F_LO) ** (
            np.arange(spectrogram.HEIGHT)[::-1] / (spectrogram.HEIGHT - 1))
        self.assertEqual(float(np.median(image[top + np.flatnonzero(row_hz > 12000)].sum(axis=2))), 0.0)
        self.assertGreater(float(np.median(image[top + np.flatnonzero((row_hz > 1000) & (row_hz < 6000))].sum(axis=2))), 0.0)

    def test_the_takes_it_prints_can_be_pasted_into_a_layer(self):
        import json
        base = helpers.scratch('edge_takes')
        x = np.zeros(5 * RATE)
        for i in range(20):
            at = int((0.1 + i * 0.24) * RATE)
            x[at:at + 480] = 0.7 * np.exp(-np.arange(480) / 60) * np.sin(2 * np.pi * 900 * np.arange(480) / RATE)
        samples.add(ts.as_folder(base / 'Steps', {'walk.wav': ts.wav_bytes(x[:, None], rate=RATE),
                                                  'License.txt': ts.CC0_TEXT.encode('utf-8')}), base / 'library', pack='steps', licence='CC0-1.0')
        proc = cli('samples', '--library', base / 'library', '--events', 'steps/walk', '--even')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        takes = json.loads(next(line for line in proc.stdout.splitlines() if line.startswith('[')))
        self.assertEqual(len(takes), project.MAX_TAKES)
        checked([{'id': 'walk', 'category': 'foley', 'layers': [
            {'source': {'type': 'sample', 'library': 'steps', 'file': takes}, 'length': 0.2}]}])

    def test_expanding_a_recipe_keeps_its_crackle(self):
        import json
        base = helpers.scratch('edge_expand')
        path = base / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
            {'id': 'boom', 'category': 'scifi', 'recipe': 'scifi.explosion'}]}), encoding='utf-8')
        proc = cli('expand', path, '--output', base / 'expanded.json')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIs(json.loads((base / 'expanded.json').read_text(encoding='utf-8'))['sounds'][0].get('crackle'), True)

    def test_hits_either_side_of_the_middle_are_not_a_silent_half(self):
        x = np.zeros(int(1.1 * RATE))
        for at in (0.52, 0.72):
            i = int(at * RATE)
            x[i:i + 960] = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(960) / RATE)
        self.assertFalse(check.measure_loop(x[:, None])['half_silent'])

    def test_a_half_with_nothing_in_it_is_still_silent(self):
        x = np.zeros(4 * RATE)
        x[:RATE] = 0.3 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)
        self.assertTrue(check.measure_loop(x[:, None])['half_silent'])

    def test_a_high_tone_is_read_in_its_own_octave(self):
        for hz in (1318.51, 1046.5, 880.0, 523.25, 220.0):
            frame = np.sin(2 * np.pi * hz * np.arange(int(0.1 * RATE)) / RATE)
            self.assertAlmostEqual(denoise._pitch(frame, RATE), hz, delta=hz * 0.005, msg=str(hz))

    def test_a_tone_with_overtones_is_read_at_its_lowest(self):
        t = np.arange(int(0.1 * RATE)) / RATE
        frame = sum(np.sin(2 * np.pi * 220 * k * t) / k for k in range(1, 9))
        self.assertAlmostEqual(denoise._pitch(frame, RATE), 220, delta=2)

    def test_not_reads_the_pack_too(self):
        index = {'packs': {'indoor-foley': {'title': 'Indoor foley'}},
                 'files': [{'library': 'indoor-foley', 'file': 'door', 'seconds': 1.0}]}
        self.assertEqual(len(samples.find(index, 'door')), 1)
        self.assertEqual(samples.find(index, 'door', exclude=['indoor']), [])
        self.assertEqual(samples.find(index, 'door', exclude=['foley']), [])

    def test_not_does_not_read_the_title_of_the_dataset(self):
        index = {'packs': {'fsd50k-dev': {'title': 'FSD50K development set, CC0 recordings'}},
                 'files': [{'library': 'fsd50k-dev', 'file': '1', 'title': 'door slam', 'seconds': 1.0}]}
        self.assertEqual(len(samples.find(index, 'door', exclude=['recordings'])), 1)


if __name__ == '__main__':
    unittest.main()
