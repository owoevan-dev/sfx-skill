"""Importing sounds a person has, when the files are mixed, misleading or hostile: every licence a text names is seen, a
mention is not a grant, restrictions stop the automatic reading, attribution is kept, writes stay inside the library,
archives and decoders are bounded, an interrupted or concurrent import loses nothing."""
import json
import os
import struct
import subprocess
import sys
import threading
import unittest
import zipfile
from unittest import mock

import numpy as np

import helpers
import test_samples as ts
from sfxlib import licences, project, samples, synth

ND_URL = 'https://creativecommons.org/licenses/by-nd/4.0/'


def link_dir(link, target):
    """A directory link: a junction on Windows (needs no privilege), a symlink elsewhere."""
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def understate_size(path, name, declared):
    """Rewrite a zip so that member `name` claims `declared` uncompressed bytes in both its headers."""
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(name)
    struct.pack_into('<I', data, info.header_offset + 22, declared)
    pos = data.find(b'PK\x01\x02')
    while pos >= 0:
        n, m, k = struct.unpack_from('<HHH', data, pos + 28)
        if bytes(data[pos + 46:pos + 46 + n]).decode('utf-8') == name:
            struct.pack_into('<I', data, pos + 24, declared)
            break
        pos = data.find(b'PK\x01\x02', pos + 46 + n + m + k)
    path.write_bytes(bytes(data))


class ReadingLicences(unittest.TestCase):
    def names(self, text):
        return {name for name, _ in licences.detect(text)}

    def kinds(self, text):
        return {kind for kind, _ in licences.doubts(text)}

    def test_every_licence_a_text_names_is_found(self):
        text = ('Metadata: https://creativecommons.org/publicdomain/zero/1.0/\n'
                'Audio: Attribution-NoDerivatives 4.0 International\n')
        self.assertEqual(self.names(text), {'CC0-1.0', 'CC-BY-ND-4.0'})
        self.assertEqual(self.names('Music CC BY 4.0, effects CC BY-NC 3.0'), {'CC-BY-4.0', 'CC-BY-NC-3.0'})

    def test_spdx_identifiers_and_older_versions_are_read(self):
        self.assertEqual(self.names('SPDX-License-Identifier: CC-BY-NC-ND-4.0'), {'CC-BY-NC-ND-4.0'})
        self.assertEqual(self.names('https://creativecommons.org/licenses/by-nc/2.5/'), {'CC-BY-NC-2.5'})
        self.assertEqual(self.names('https://creativecommons.org/licenses/by-nd-nc/1.0/'), {'CC-BY-NC-ND-1.0'})
        self.assertEqual(licences.conditions('CC-BY-NC-2.5'), ['attribution', 'non-commercial'])
        self.assertFalse(licences.usable('CC-BY-ND-2.0')[0])
        self.assertEqual(licences.normalize('cc by-sa 2.5'), 'CC-BY-SA-2.5')

    def test_a_mention_is_not_a_grant(self):
        for text in ('These recordings are not public domain. All rights reserved.',
                     'This pack is not CC0.',
                     'Some sounds came from public domain recordings, the others are ours.'):
            self.assertEqual(self.names(text), set(), text)
            self.assertTrue(self.kinds(text), text)

    def test_a_clear_public_domain_statement_is_a_grant(self):
        for text in ('These sounds are released into the public domain.', 'License: public domain',
                     'I dedicate this pack to the public domain.', 'This work is public domain.'):
            self.assertEqual(self.names(text), {'public domain'}, text)
            self.assertEqual(self.kinds(text), set(), text)

    def test_restrictions_raise_a_doubt(self):
        for text, kind in (('(c) 2024 Someone. All rights reserved.', 'all rights reserved'),
                           ('For personal use only.', 'non-commercial'),
                           ('Not for commercial use.', 'non-commercial'),
                           ('You may not modify the recordings.', 'no derivatives'),
                           ('You may not redistribute these files.', 'no redistribution'),
                           ('Not for resale.', 'no redistribution'),
                           ('Do not use to train machine learning models.', 'AI or machine-learning terms'),
                           ('No AI use.', 'AI or machine-learning terms'),
                           ('Distributed under the GPL.', 'another licence'),
                           ('See the End User License Agreement.', 'another licence'),
                           ('Licensed CC BY.', 'a Creative Commons licence without a version')):
            self.assertIn(kind, self.kinds(text), text)

    def test_ordinary_free_terms_raise_no_doubt(self):
        for text in (ts.CC0_TEXT, ts.BY_TEXT, 'Free for personal and commercial use. CC0.',
                     'CC0: no attribution required, royalty free.', 'You can modify and share them. CC0 1.0.'):
            self.assertEqual(self.kinds(text), set(), text)

    def test_a_licences_own_legal_text_reads_as_that_licence(self):
        by = ('Creative Commons Attribution 4.0 International Public License\n'
              'You may not offer or impose any additional or different terms.\n'
              'Creative Commons is not a party to its public licenses. The text of the Creative Commons\n'
              'public licenses is dedicated to the public domain under the CC0 Public Domain Dedication.\n')
        self.assertEqual(self.names(by), {'CC-BY-4.0'})
        self.assertEqual(self.kinds(by), set())
        nc = ('Creative Commons Attribution-NonCommercial 4.0 International Public License\n'
              'NonCommercial means not primarily intended for commercial advantage.\n')
        self.assertEqual(self.names(nc), {'CC-BY-NC-4.0'})
        self.assertEqual(self.kinds(nc), set())
        cc0 = ('CC0 1.0 Universal\nAffirmer dedicates the Work to the public domain.\n'
               'No trademark rights held by Affirmer are waived.\n')
        self.assertEqual(self.names(cc0), {'CC0-1.0'})
        self.assertEqual(self.kinds(cc0), set())

    def test_a_clause_added_to_a_full_legal_text_is_still_seen(self):
        code = ('Creative Commons Attribution 3.0 Unported\n1. Definitions\n3. License Grant\n4. Restrictions\n'
                'You may not sublicense the Work.\nYou must not distort, mutilate, modify the Work.\n'
                '7. Termination\nTerm and Termination\nDisclaimer of warranties\nLimitation on liability\n')
        code += 'The licensor offers the work as is.\n' * 120
        self.assertEqual(self.names(code), {'CC-BY-3.0'})
        self.assertEqual(self.kinds(code), set())
        self.assertIn('AI or machine-learning terms', self.kinds(code + 'Additional terms: no AI use.\n'))
        self.assertIn('all rights reserved', self.kinds(code + 'All rights reserved.\n'))

    def test_share_alike_says_compatible_licences_count(self):
        self.assertIn('compatible', licences.MEANING['share-alike'])


class Settling(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'import_{self._testMethodName}')
        self.library = self.folder / 'library'

    def source(self, text, extra=None, name='src'):
        return ts.as_folder(self.folder / name, ts.pack_members(licence_text=text, extra=extra))

    def test_own_does_not_silently_override_a_restrictive_licence(self):
        src = self.source(ts.ND_TEXT)
        with self.assertRaisesRegex(samples.SampleError, '--confirm-licence'):
            samples.add(src, self.library, licence='own')
        self.assertFalse((self.library / samples.INDEX).exists())
        r = samples.add(src, self.library, licence='own', confirm_licence=True)
        self.assertEqual(r['licence'], 'own')
        self.assertIn('over what its files say', r['licence_status'])
        self.assertIn('CC-BY-ND-4.0', r['licence_status'])

    def test_naming_one_licence_of_a_mixed_pack_needs_the_persons_confirmation(self):
        src = self.source(ts.CC0_TEXT + f'Voice clips: {ND_URL}\n')
        with self.assertRaisesRegex(samples.SampleError, '--confirm-licence'):
            samples.add(src, self.library, licence='CC0')
        with self.assertRaisesRegex(samples.SampleError, 'by-nd/4.0'):            # asked, it quotes both lines
            samples.add(src, self.library, licence=None)

    def test_a_restriction_stops_a_named_licence(self):
        src = self.source(ts.CC0_TEXT + 'All rights reserved.\n')
        with self.assertRaisesRegex(samples.SampleError, '--confirm-licence'):
            samples.add(src, self.library, licence='CC0')
        self.assertFalse((self.library / samples.INDEX).exists())

    def test_no_licence_text_is_missed_for_its_name_or_encoding(self):
        for i, (name, data) in enumerate((
                ('LICENSE_voice.txt', f'{ND_URL}\n'.encode()),
                ('license-audio.md', b'Attribution-NoDerivatives 4.0 International\n'),
                ('notes.txt', b'Voice: CC-BY-ND-4.0\n'),
                ('terms.txt', 'Voice: CC-BY-ND-4.0\n'.encode('utf-16')),
                ('about.html', f'<a href="{ND_URL}">licence</a>'.encode()))):
            src = self.source(ts.CC0_TEXT, {name: data}, f'src{i}')
            with self.assertRaises(samples.SampleError, msg=name):
                samples.add(src, self.library, licence='CC0')
            self.assertFalse((self.library / samples.INDEX).exists(), name)

    def test_a_licence_document_it_cannot_read_stops_the_import(self):
        for i, (name, data) in enumerate((('License Agreement.pdf', b'%PDF-1.4 ...'),
                                          ('LICENSE.txt', b'x' * (samples.MAX_TEXT_BYTES + 1)))):
            src = self.source(None, {name: data}, f'src{i}')
            with self.assertRaisesRegex(samples.SampleError, 'read', msg=name):
                samples.add(src, self.library, licence='own')
            r = samples.add(src, self.folder / f'library{i}', licence='own', confirm_licence=True)
            self.assertEqual(r['added'], 3)

    def test_a_licence_asking_for_credit_needs_a_name(self):
        src = self.source(ts.BY_TEXT)
        with self.assertRaisesRegex(samples.SampleError, '--author'):
            samples.add(src, self.library, licence='CC-BY-4.0')
        r = samples.add(src, self.library, licence='CC-BY-4.0', author='A. Recordist')
        self.assertEqual(r['conditions'], ['attribution'])
        more = self.source(ts.BY_TEXT, {'Audio/late.wav': ts.wav_bytes(ts.tone(777, 0.2))}, 'more')
        with self.assertRaisesRegex(samples.SampleError, 'A. Recordist'):        # who made these is the person's to say
            samples.add(more, self.library, licence='CC-BY-4.0', pack=r['pack'])
        again = samples.add(more, self.library, licence='CC-BY-4.0', pack=r['pack'], author='A. Recordist')
        self.assertEqual(again['added'], 1)
        entry = next(f for f in samples.load_index(self.library)['files'] if f['file'] == 'late')
        self.assertEqual(entry['author'], 'A. Recordist')

    def test_the_same_audio_under_another_licence_is_reported_not_merged(self):
        samples.add(self.source(None, name='a'), self.library, licence='own', pack='mine')
        r = samples.add(self.source(ts.BY_TEXT, name='b'), self.library, licence='CC-BY-4.0', author='A. Recordist',
                        pack='theirs')
        self.assertEqual((r['added'], r['duplicates']), (0, 0))
        self.assertEqual(len(r['skipped']), 3)
        self.assertIn('mine', r['skipped'][0][1])
        self.assertIn('own', r['skipped'][0][1])

    def test_the_same_audio_under_the_same_terms_is_a_duplicate(self):
        samples.add(self.source(ts.CC0_TEXT, name='a'), self.library, pack='first', licence='CC0-1.0')
        r = samples.add(self.source(ts.CC0_TEXT, name='b'), self.library, pack='second', licence='CC0-1.0')
        self.assertEqual((r['added'], r['duplicates'], r['skipped']), (0, 3, []))

    def test_every_pack_id_it_makes_is_one_a_project_accepts(self):
        for stem, want in (('my_pack.v2', 'my-pack-v2'), ('A  B__C', 'a-b-c'), ('...', 'sounds')):
            src = ts.as_zip(self.folder / f'{stem}.zip', ts.pack_members())
            lib = self.folder / f'lib_{want}'
            r = samples.add(src, lib, licence='CC0-1.0')
            self.assertEqual(r['pack'], want)
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                {'id': 'x', 'category': 'impact', 'layers': [{'source': {
                    'type': 'sample', 'library': r['pack'], 'file': 'woodKnock_light_000'}, 'length': 0.1}]}]})

    def test_a_folder_keeps_its_whole_name(self):
        src = ts.as_folder(self.folder / 'my.pack.v2', ts.pack_members())
        self.assertEqual(samples.add(src, self.folder / 'lib_dotted', licence='CC0-1.0')['pack'], 'my-pack-v2')

    def test_the_command_takes_the_persons_confirmation(self):
        src = self.source(ts.ND_TEXT)
        env = dict(helpers.child_env(), SFX_SKILL_LIBRARY=str(self.library))
        run = [sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'samples', '--add', str(src), '--licence', 'own']
        proc = subprocess.run(run, capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('--confirm-licence', proc.stdout)
        proc = subprocess.run(run + ['--confirm-licence'], capture_output=True, text=True, encoding='utf-8', env=env,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class StayingInside(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'import_{self._testMethodName}')
        self.library = self.folder / 'library'
        self.library.mkdir()
        self.outside = self.folder / 'outside'
        self.outside.mkdir()

    def test_a_pack_folder_linking_elsewhere_is_refused(self):
        link_dir(self.library / 'src', self.outside)
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        with self.assertRaisesRegex(samples.SampleError, 'outside'):
            samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_staging_folder_linking_elsewhere_is_refused(self):
        link_dir(self.library / '.staging', self.outside)
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        with self.assertRaisesRegex(samples.SampleError, 'outside'):
            samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_an_existing_file_at_a_name_is_never_written_through(self):
        pack = self.library / 'src'
        pack.mkdir()
        keep = pack / 'woodKnock_light_000.wav'
        keep.write_bytes(b'not this audio')
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        r = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual(r['added'], 3)
        self.assertEqual(keep.read_bytes(), b'not this audio')
        names = {f['file'] for f in samples.load_index(self.library)['files']}
        self.assertIn('woodKnock_light_000_2', names)


class Bounds(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'import_{self._testMethodName}')
        self.library = self.folder / 'library'

    def test_a_folder_counts_its_total_size(self):
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        with mock.patch.object(samples, 'MAX_TOTAL_BYTES', 1000):
            with self.assertRaisesRegex(samples.SampleError, 'more than'):
                samples.add(src, self.library, licence='CC0-1.0')

    def test_a_zip_counts_its_directory_entries_before_loading_them(self):
        path = self.folder / 'many.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for i in range(30):
                z.writestr(f'd{i}/', b'')
            z.writestr('a.wav', ts.wav_bytes(ts.tone(300, 0.1)))
        with mock.patch.object(samples, 'MAX_MEMBERS', 20):
            with mock.patch.object(zipfile, 'ZipFile', side_effect=AssertionError('the directory was loaded')):
                with self.assertRaisesRegex(samples.SampleError, 'more than 20'):
                    samples.add(path, self.library, licence='CC0-1.0')

    def test_a_member_that_understates_its_size_is_read_in_bounded_steps_and_refused(self):
        path = self.folder / 'bomb.zip'
        blob = ts.wav_bytes(ts.tone(300, 0.3)) + b'\x00' * (4 << 20)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('LICENSE.txt', ts.CC0_TEXT)
            z.writestr('a.wav', blob)
        understate_size(path, 'a.wav', 1000)
        sizes = []
        real = zipfile.ZipExtFile.read

        def read(self, n=-1):
            sizes.append(n)
            return real(self, n)
        with mock.patch.object(zipfile.ZipExtFile, 'read', read):
            with self.assertRaises(samples.SampleError):
                samples.add(path, self.library, licence='CC0-1.0')
        self.assertTrue(sizes)
        self.assertTrue(all(n is not None and 0 < n <= samples.CHUNK for n in sizes), sizes[:5])

    def test_a_damaged_member_is_a_refusal_not_a_crash(self):
        path = self.folder / 'bad.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('LICENSE.txt', ts.CC0_TEXT)
            z.writestr('a.wav', ts.wav_bytes(ts.tone(300, 0.1)))
        data = bytearray(path.read_bytes())
        at = data.find(b'data', data.find(b'a.wav'))
        data[at + 100] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(samples.SampleError):
            samples.add(path, self.library, licence='CC0-1.0')

    def test_ffmpeg_reads_only_the_container_its_name_says_and_is_bounded(self):
        path = self.folder / 'clip.ogg'
        path.write_bytes(b'#EXTM3U\nfile:secret.wav\n')
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            pcm = np.zeros(4800, dtype='<f4').tobytes()
            banner = 'Duration: 00:00:00.10, Stream #0:0: Audio: vorbis, 48000 Hz, mono, fltp'
            return subprocess.CompletedProcess(cmd, 0, pcm, banner if kw.get('text') else banner.encode())
        with mock.patch.object(samples.subprocess, 'run', side_effect=fake):
            x, rate = samples.decode(path, 'ffmpeg')
        self.assertEqual((rate, x.shape), (48000, (4800, 1)))
        for cmd in calls:
            i = cmd.index('-i')
            self.assertEqual(cmd[cmd.index('-f'):cmd.index('-f') + 2], ['-f', 'ogg'], cmd)
            self.assertLess(cmd.index('-f'), i)
            self.assertEqual(cmd[cmd.index('-protocol_whitelist') + 1], 'file')
            self.assertIn('-nostdin', cmd)
        self.assertIn('-t', calls[-1])

    def test_a_recording_longer_than_the_limit_is_refused(self):
        path = self.folder / 'long.wav'
        path.write_bytes(ts.wav_bytes(ts.tone(300, 1.0)))
        with mock.patch.object(samples, 'MAX_SECONDS', 0.5):
            with self.assertRaisesRegex(samples.SampleError, 'longer'):
                samples.decode(path)


class Interrupted(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'import_{self._testMethodName}')
        self.library = self.folder / 'library'

    def test_a_retry_after_an_interruption_indexes_what_was_already_moved(self):
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        with mock.patch.object(samples, '_write_index', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                samples.add(src, self.library, licence='CC0-1.0')
        r = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual((r['added'], r['files'], r['skipped']), (3, 3, []))
        names = sorted(p.name for p in (self.library / 'src').iterdir() if p.suffix == '.wav')
        self.assertEqual(len(names), 3)                 # no second copies

    def test_the_index_is_replaced_whole_or_not_at_all(self):
        src = ts.as_folder(self.folder / 'src', ts.pack_members())
        samples.add(src, self.library, licence='CC0-1.0')
        before = (self.library / samples.INDEX).read_bytes()
        index = samples.load_index(self.library)
        with mock.patch.object(samples.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                samples._write_index(self.library, index)
        self.assertEqual((self.library / samples.INDEX).read_bytes(), before)

    def test_two_imports_at_once_keep_both_packs(self):
        a = ts.as_folder(self.folder / 'a', ts.pack_members())
        b = ts.as_folder(self.folder / 'b', {'x.wav': ts.wav_bytes(ts.tone(880, 0.2)), 'License.txt':
                                             ts.CC0_TEXT.encode()})
        first_is_inside = threading.Event()
        real = samples.decode
        errors = []

        def slow(path, ffmpeg=None):
            first_is_inside.set()
            threading.Event().wait(0.4)
            return real(path, ffmpeg)

        def importing(src):
            try:
                samples.add(src, self.library, licence='CC0-1.0')
            except Exception as error:      # a failure in a thread would otherwise pass unseen
                errors.append(error)
        with mock.patch.object(samples, 'decode', slow):
            one = threading.Thread(target=importing, args=(a,))
            one.start()
            self.assertTrue(first_is_inside.wait(20))
            two = threading.Thread(target=importing, args=(b,))
            two.start()
            one.join(60)
            two.join(60)
        self.assertEqual(errors, [])
        index = json.loads((self.library / samples.INDEX).read_text(encoding='utf-8'))
        self.assertEqual(sorted(index['packs']), ['a', 'b'])
        self.assertEqual(len(index['files']), 4)


class KeptAttack(unittest.TestCase):
    def test_a_layer_with_a_kept_attack_gets_enough_of_its_recording(self):
        folder = helpers.scratch('import_kept_attack')
        t = np.arange(4 * 44100) / 44100
        steady = 0.5 * np.sin(2 * np.pi * 440 * t)
        src = ts.as_folder(folder / 'long', {'steady.wav': ts.wav_bytes(steady[:, None]),
                                             'License.txt': ts.CC0_TEXT.encode()})
        samples.add(src, folder / 'library', licence='CC0-1.0')
        library = samples.Library(folder / 'library')
        layer = {'source': {'type': 'sample', 'library': 'long', 'file': 'steady',
                            'time_stretch': {'factor': 2.0, 'keep_attack_ms': 800}}, 'length': 2.0}
        x = synth.render_sound({'layers': [layer]}, seed=1, samples=library)
        tail = x[int(1.85 * 48000):int(1.95 * 48000)]
        self.assertGreater(float(np.sqrt(np.mean(tail ** 2))), 0.2)


if __name__ == '__main__':
    unittest.main()
