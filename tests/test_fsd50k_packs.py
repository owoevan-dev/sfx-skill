"""The project's own packs of FSD50K's CC0 recordings: built from a library, fetched in resumable steps, checked at each
step and imported; and the dataset's downloads cleared only when they are known files that a library already holds."""
import contextlib
import csv
import hashlib
import io
import json
import unittest
import zipfile
from unittest import mock

import numpy as np

import helpers
import make_fsd50k_packs as maker
import test_fsd50k as data
from sfxlib import clean, fsd50k, samples


def setUpModule():
    """The packs made here are stand-ins, and this version accepts only the published index."""
    global _published
    _published = mock.patch.object(fsd50k, 'RELEASE_INDEX_SHA256', '')
    _published.start()


def tearDownModule():
    _published.stop()


def command(*args):
    """(exit code, what it printed) of sfx.py in this process, where the stand-in packs are accepted."""
    import sfx
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sfx.main([str(a) for a in args])
    return code, out.getvalue()


def wav_for(seed, seconds=0.4):
    return data.wav_bytes(data.clean_clip(seconds=seconds, seed=seed))


def row(pack, number, blob, screen=()):
    return {'library': pack, 'file': number, 'path': f'{pack}/{number}.wav', 'sha256': hashlib.sha256(blob).hexdigest(),
            'seconds': 0.4, 'channels': 1, 'rate': 44100, 'licence': 'CC0-1.0', 'measures': {'peak_db': -6.0},
            'title': f'clip {number}', 'author': f'author{number}', 'page': f'https://freesound.org/s/{number}/',
            'tags': ['x'], 'labels': ['Door'], 'predominant': True, 'screen': list(screen)}


def stand_in_library(folder, count=6):
    """A library with both packs: half of each passes, one has an author's note, one is not CC0."""
    index = samples.new_index()
    for p, base in (('fsd50k-eval', 100), ('fsd50k-dev', 200)):
        (folder / p).mkdir(parents=True)
        rows = []
        for i in range(count):
            number = str(base + i)
            blob = wav_for(base + i)
            (folder / p / f'{number}.wav').write_bytes(blob)
            r = row(p, number, blob, () if i % 2 == 0 else ('noisy',))
            rows.append(r)
        rows[2]['screen'] = ['author note']
        rows[4]['licence'] = 'CC-BY-4.0'
        index['files'] += rows
        index['packs'][p] = {'title': f'FSD50K {p}', 'author': 'Freesound authors', 'licence': 'CC0-1.0',
                             'page': fsd50k.PAGE, 'status': 'verified', 'files': count, 'passing': 3}
    samples._write_index(folder, index)
    return index


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = helpers.scratch('fsd50k_packs_build')
        cls.index = stand_in_library(cls.folder / 'library')
        cls.built = maker.build(cls.folder / 'library', cls.folder / 'packs', part_bytes=40000, log=lambda m: None,
                                also=())

    def test_only_cc0_recordings_that_pass_every_screen_go_in(self):
        self.assertEqual([(r['library'], r['file']) for r in self.built['files']],
                         [('fsd50k-eval', '100'), ('fsd50k-dev', '200')])

    def test_the_parts_hold_the_recordings_unchanged_under_the_size_limit(self):
        self.assertEqual(len(self.built['parts']), 2)          # a clip is 35 kB of audio, a part holds 40 kB
        seen = {}
        for part in self.built['parts']:
            path = self.folder / 'packs' / part['name']
            self.assertEqual(fsd50k.sha256_of(path), part['sha256'])
            self.assertEqual(path.stat().st_size, part['bytes'])
            with zipfile.ZipFile(path) as z:
                self.assertEqual(len(z.namelist()), part['files'])
                for name in z.namelist():
                    seen[name] = hashlib.sha256(z.read(name)).hexdigest()
        wanted = {f"{r['library']}/{r['file']}.wav": r['sha256'] for r in self.built['files']}
        self.assertEqual(seen, wanted)

    def test_credits_notice_and_sums(self):
        rows = list(csv.DictReader(io.StringIO((self.folder / 'packs' / 'CREDITS.csv').read_text(encoding='utf-8'))))
        self.assertEqual([(r['file'], r['author'], r['freesound_page']) for r in rows],
                         [('100', 'author100', 'https://freesound.org/s/100/'),
                          ('200', 'author200', 'https://freesound.org/s/200/')])
        notice = (self.folder / 'packs' / 'NOTICE.txt').read_text(encoding='utf-8')
        for words in ('Fonseca', 'vol. 30, pp. 829-852', 'doi.org/10.5281/zenodo.4060432', 'CC BY 4.0',
                      'creativecommons.org/licenses/by/4.0', 'CC0 1.0', 'What was changed', 'No warranties',
                      'do not endorse', 'template=remove_recording.yml'):
            self.assertIn(words, notice)
        for line in (self.folder / 'packs' / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ')
            self.assertEqual(fsd50k.sha256_of(self.folder / 'packs' / name), digest)

    def test_a_recording_an_example_plays_goes_in_unless_its_author_asks_for_something(self):
        rows = samples.load_index(self.folder / 'library')
        named = [('fsd50k-eval', '101'), ('fsd50k-eval', '102')]          # 'noisy'; an author's note
        files = [(r['library'], r['file']) for r in maker.chosen(rows, also=named)]
        self.assertIn(('fsd50k-eval', '101'), files)
        self.assertNotIn(('fsd50k-eval', '102'), files)

    def test_the_real_examples_are_found(self):
        self.assertIn(('fsd50k-eval', '54269'), maker.example_files())

    def test_an_entry_without_screens_is_not_taken_for_a_pass(self):
        index = samples.load_index(self.folder / 'library')
        rows = [dict(r) for r in index['files']]
        del rows[0]['screen']
        self.assertNotIn(rows[0]['file'], [r['file'] for r in maker.chosen({'files': rows}, also=())])

    def test_a_library_without_both_sets_is_refused(self):
        index = samples.load_index(self.folder / 'library')
        del index['packs']['fsd50k-dev']
        index['files'] = [r for r in index['files'] if r['library'] != 'fsd50k-dev']
        partial = helpers.scratch('fsd50k_packs_partial')
        (partial / 'fsd50k-eval').mkdir()
        for r in index['files']:
            (partial / r['path']).write_bytes((self.folder / 'library' / r['path']).read_bytes())
        samples._write_index(partial, index)
        with self.assertRaisesRegex(SystemExit, 'fsd50k-dev'):
            maker.build(partial, partial / 'out', log=lambda m: None, also=())

    def test_the_notice_says_some_recordings_are_there_for_the_examples(self):
        self.assertIn('examples', (self.folder / 'packs' / 'NOTICE.txt').read_text(encoding='utf-8'))

    def test_a_recording_larger_than_a_part_is_refused(self):
        with self.assertRaisesRegex(SystemExit, 'larger'):
            maker.build(self.folder / 'library', self.folder / 'packs_small', part_bytes=1000, log=lambda m: None,
                        also=())

    def test_the_zip_records_do_not_depend_on_the_system_that_built_them(self):
        for part in self.built['parts']:
            with zipfile.ZipFile(self.folder / 'packs' / part['name']) as z:
                for info in z.infolist():
                    self.assertEqual((info.create_system, info.external_attr >> 16), (3, 0o100644))

    def test_the_same_library_builds_the_same_parts(self):
        again = maker.build(self.folder / 'library', self.folder / 'packs_again', part_bytes=40000, log=lambda m: None,
                            also=())
        self.assertEqual([p['sha256'] for p in again['parts']], [p['sha256'] for p in self.built['parts']])


class Import(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'fsd50k_packs_{self._testMethodName}')
        stand_in_library(self.folder / 'source')
        maker.build(self.folder / 'source', self.folder / 'packs', log=lambda m: None, also=())

    def test_a_new_library_gets_the_recordings_and_their_entries(self):
        r = fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertEqual((r['added'], r['files']), (2, 2))
        index = samples.load_index(self.folder / 'library')
        self.assertEqual(sorted(index['packs']), ['fsd50k-dev', 'fsd50k-eval'])
        self.assertEqual(index['packs']['fsd50k-eval']['status'], fsd50k.PACK_STATUS)
        lib = samples.Library(self.folder / 'library')
        x = lib.recording('fsd50k-eval', '100')
        self.assertAlmostEqual(float(np.max(np.abs(x))), samples.PEAK, places=6)
        self.assertEqual(lib.provenance('fsd50k-dev', '200')['author'], 'author200')

    def test_importing_again_changes_nothing(self):
        fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        before = (self.folder / 'library' / samples.INDEX).read_bytes()
        r = fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertEqual(r['added'], 0)
        self.assertEqual(json.loads((self.folder / 'library' / samples.INDEX).read_bytes()), json.loads(before))

    def test_a_damaged_part_is_refused_before_anything_is_written(self):
        part = next((self.folder / 'packs').glob('fsd50k-cc0-*.zip'))
        blob = bytearray(part.read_bytes())
        blob[len(blob) // 2] ^= 0xFF
        part.write_bytes(bytes(blob))
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'damaged'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertFalse((self.folder / 'library' / 'fsd50k-eval').exists())

    def test_a_part_with_a_recording_the_index_does_not_name_is_refused(self):
        path = self.folder / 'packs' / fsd50k.PACK_INDEX
        index = json.loads(path.read_text(encoding='utf-8'))
        index['files'] = index['files'][:1]
        path.write_text(json.dumps(index), encoding='utf-8')
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'does not name'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)

    def test_an_index_with_an_unsafe_part_name_is_refused(self):
        path = self.folder / 'packs' / fsd50k.PACK_INDEX
        index = json.loads(path.read_text(encoding='utf-8'))
        index['parts'][0]['name'] = '../outside.zip'
        path.write_text(json.dumps(index), encoding='utf-8')
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'part name'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)

    def rewrite_index(self, change):
        path = self.folder / 'packs' / fsd50k.PACK_INDEX
        index = json.loads(path.read_text(encoding='utf-8'))
        change(index)
        path.write_text(json.dumps(index), encoding='utf-8')

    def test_an_entry_no_part_holds_is_refused_before_anything_is_written(self):
        self.rewrite_index(lambda index: index.update(parts=[]))
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'no part holds'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertFalse((self.folder / 'library' / samples.INDEX).exists())

    def test_an_entry_with_a_path_of_its_own_is_refused(self):
        self.rewrite_index(lambda index: index['files'][0].update(path='fsd50k-eval/../../elsewhere.wav'))
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'entry'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)

    def test_an_entry_that_is_not_cc0_is_refused(self):
        self.rewrite_index(lambda index: index['files'][0].update(licence='CC-BY-4.0'))
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'entry'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)

    def test_an_index_other_than_the_one_this_version_knows_is_refused_offline(self):
        with mock.patch.object(fsd50k, 'RELEASE_INDEX_SHA256', '0' * 64):
            with self.assertRaisesRegex(fsd50k.FSD50KError, 'expects'):
                fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertFalse((self.folder / 'library').exists())

    def test_a_corrected_pack_updates_the_entry_of_a_recording_already_there(self):
        fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.rewrite_index(lambda index: index['files'][0].update(author='the right name'))
        fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        rows = {f['file']: f for f in samples.load_index(self.folder / 'library')['files']}
        self.assertEqual(rows['100']['author'], 'the right name')

    def test_a_file_already_there_with_other_bytes_is_never_replaced(self):
        (self.folder / 'library' / 'fsd50k-eval').mkdir(parents=True)
        keep = self.folder / 'library' / 'fsd50k-eval' / '100.wav'
        keep.write_bytes(b'not this recording')
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'differs'):
            fsd50k.add_packs(self.folder / 'packs', self.folder / 'library', log=lambda m: None)
        self.assertEqual(keep.read_bytes(), b'not this recording')

    def test_the_full_set_from_zenodo_can_follow_the_packs(self):
        clips = data.three_clips()
        raw = helpers.scratch('fsd50k_packs_full_dataset')
        data.dataset(raw, clips)
        library = self.folder / 'library'
        index = samples.new_index()
        (library / 'fsd50k-eval').mkdir(parents=True)
        blob = data.wav_bytes(clips['101'][3])
        (library / 'fsd50k-eval' / '101.wav').write_bytes(blob)
        index['files'] = [row('fsd50k-eval', '101', blob)]
        index['packs']['fsd50k-eval'] = {'title': 'x', 'author': 'y', 'licence': 'CC0-1.0', 'page': fsd50k.PAGE,
                                         'status': fsd50k.PACK_STATUS, 'files': 1, 'passing': 1}
        samples._write_index(library, index)
        r = fsd50k.add(raw, library, 'eval', check_md5=False, log=lambda m: None)
        self.assertEqual(r['files'], 2)                                # both CC0 clips, the first not twice
        names = [f['file'] for f in samples.load_index(library)['files']]
        self.assertEqual(sorted(names), ['101', '202'])
        self.assertEqual(samples.load_index(library)['packs']['fsd50k-eval']['status'], 'unchecked')


class Serving:
    """A stand-in release: serves files from a folder, honouring ranges, dropping the first read of each large file."""

    class Response:
        def __init__(self, status, body, drop_after=None):
            self.status, self.body, self.drop_after, self.at = status, body, drop_after, 0
            self.headers = {}

        def read(self, n):
            if self.drop_after is not None and self.at >= self.drop_after:
                raise OSError('connection reset')
            end = self.at + n if self.drop_after is None else min(self.at + n, self.drop_after)
            block = self.body[self.at:end]
            self.at += len(block)
            return block

    def __init__(self, folder):
        self.folder, self.requests, self.dropped = folder, [], set()

    def __call__(self, url, headers):
        name = url.rsplit('/', 1)[1]
        self.requests.append((name, headers.get('Range')))
        body = (self.folder / name).read_bytes()
        start = int(headers['Range'][6:-1]) if 'Range' in headers else 0
        drop = None
        if name.endswith('.zip') and name not in self.dropped:
            self.dropped.add(name)
            drop = len(body) // 2
        response = self.Response(206 if start else 200, body[start:], drop)
        if start:
            response.headers['Content-Range'] = f'bytes {start}-{len(body) - 1}/{len(body)}'
        return response


class Download(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'fsd50k_packs_{self._testMethodName}')
        stand_in_library(self.folder / 'source')
        maker.build(self.folder / 'source', self.folder / 'release', log=lambda m: None, also=())
        self.server = Serving(self.folder / 'release')

    def fetch(self, index_sha256=''):
        return fsd50k.download_packs(self.folder / 'data', url='https://example.invalid/r', index_sha256=index_sha256,
                                     opener=self.server, sleep=lambda s: None, log=lambda m: None)

    def test_it_fetches_every_part_resuming_a_dropped_one(self):
        index = self.fetch()
        for part in index['parts']:
            self.assertEqual(fsd50k.sha256_of(self.folder / 'data' / part['name']), part['sha256'])
        self.assertTrue(any(r for name, r in self.server.requests if name.endswith('.zip')))     # a range was asked
        r = fsd50k.add_packs(self.folder / 'data', self.folder / 'library', log=lambda m: None)
        self.assertEqual(r['added'], 2)

    def test_a_second_run_fetches_only_the_index_if_anything(self):
        self.fetch()
        self.server.requests.clear()
        self.fetch()
        self.assertFalse([n for n, _ in self.server.requests if n.endswith('.zip')])

    def test_an_index_that_is_not_the_one_expected_is_refused(self):
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'expects'):
            self.fetch(index_sha256='0' * 64)
        self.assertFalse((self.folder / 'data' / fsd50k.PACK_INDEX).exists())

    def test_without_a_known_sum_the_index_is_fetched_fresh(self):
        self.fetch()
        path = self.folder / 'release' / fsd50k.PACK_INDEX
        index = json.loads(path.read_text(encoding='utf-8'))
        index['files'][0]['author'] = 'corrected'
        path.write_text(json.dumps(index), encoding='utf-8')
        self.assertEqual(self.fetch()['files'][0]['author'], 'corrected')

    def test_the_expected_index_passes(self):
        digest = fsd50k.sha256_of(self.folder / 'release' / fsd50k.PACK_INDEX)
        self.assertTrue(self.fetch(index_sha256=digest)['parts'])

    def test_without_a_release_it_says_so(self):
        with mock.patch.object(fsd50k, 'RELEASE_URL', ''):
            with self.assertRaisesRegex(fsd50k.FSD50KError, 'zenodo'):
                fsd50k.download_packs(self.folder / 'data', opener=self.server, sleep=lambda s: None,
                                      log=lambda m: None)


class ListDownloads(unittest.TestCase):
    """What the library holds in full is listed; nothing here moves or removes a download."""

    def setUp(self):
        self.folder = helpers.scratch(f'fsd50k_clear_{self._testMethodName}')
        stand_in_library(self.folder / 'source')
        maker.build(self.folder / 'source', self.folder / 'data', log=lambda m: None, also=())
        self.library = self.folder / 'library'
        fsd50k.add_packs(self.folder / 'data', self.library, log=lambda m: None)

    def listed(self):
        return clean.downloads(self.folder / 'data', self.library)

    def parts(self):
        return sorted(p.name for p in (self.folder / 'data').glob('fsd50k-cc0-*.zip'))

    def test_imported_parts_are_listed_and_left_where_they_are(self):
        where = self.folder / 'data'
        before = {p.name: p.read_bytes() for p in where.iterdir()}
        p = self.listed()
        self.assertEqual(sorted(item['name'] for item in p['held']), self.parts())
        self.assertEqual({q.name: q.read_bytes() for q in where.iterdir()}, before)

    def test_a_part_the_library_does_not_hold_is_not_called_held(self):
        index = samples.load_index(self.library)
        lost = index['files'].pop(0)
        samples._write_index(self.library, index)
        p = self.listed()
        with zipfile.ZipFile(self.folder / 'data' / self.parts()[0]) as z:
            self.assertIn(f"{lost['library']}/{lost['file']}.wav", z.namelist())
        self.assertIn(self.parts()[0], {item['name'] for item in p['left']})
        self.assertNotIn(self.parts()[0], {item['name'] for item in p['held']})

    def test_an_empty_copy_in_the_library_keeps_its_part(self):
        index = samples.load_index(self.library)
        (self.library / index['files'][0]['path']).write_bytes(b'')
        p = self.listed()
        with zipfile.ZipFile(self.folder / 'data' / self.parts()[0]) as z:
            self.assertIn(index['files'][0]['path'], z.namelist())
        self.assertIn(self.parts()[0], {item['name'] for item in p['left']})
        self.assertNotIn(self.parts()[0], {item['name'] for item in p['held']})

    def test_a_name_that_only_starts_like_a_part_is_not_listed(self):
        stray = self.folder / 'data' / f'{self.parts()[0]}.bad-mix.wav'
        stray.write_bytes(b'not a partial download')
        partial = self.folder / 'data' / f'{self.parts()[0]}.part'
        partial.write_bytes(b'a partial download')
        numbered = self.folder / 'data' / f'{self.parts()[0]}.bad-2'
        numbered.write_bytes(b'a set-aside download')
        held = {item['name'] for item in self.listed()['held']}
        self.assertNotIn(stray.name, held)
        self.assertIn(partial.name, held)
        self.assertIn(numbered.name, held)

    def test_a_damaged_part_and_a_foreign_file_are_not_called_held(self):
        part = self.folder / 'data' / self.parts()[0]
        blob = bytearray(part.read_bytes())
        blob[len(blob) // 2] ^= 0xFF
        part.write_bytes(bytes(blob))
        (self.folder / 'data' / 'my notes.txt').write_text('mine', encoding='utf-8')
        p = self.listed()
        self.assertIn(part.name, {item['name'] for item in p['left']})
        held = {item['name'] for item in p['held']}
        self.assertNotIn(part.name, held)
        self.assertNotIn('my notes.txt', held)

    def test_the_dataset_archives_are_listed_once_their_set_is_imported(self):
        raw = helpers.scratch('fsd50k_clear_raw')
        data.dataset(raw, data.three_clips())
        names = fsd50k.files_for('eval')[2:]
        sums = {n: fsd50k.md5_of(raw / n) for n in names}
        (raw / 'something.bin').write_bytes(b'x')
        with mock.patch.dict(fsd50k.KNOWN_MD5, sums):
            before = clean.downloads(raw, self.library)                       # a release pack is not the set
            self.assertEqual(before['held'], [])
            lib2 = self.folder / 'library2'
            fsd50k.add(raw, lib2, 'eval', check_md5=False, log=lambda m: None)
            after = clean.downloads(raw, lib2)
        self.assertEqual(sorted(item['name'] for item in after['held']), sorted(names))
        self.assertNotIn('something.bin', [i['name'] for i in after['held']])
        one = samples.load_index(lib2)['files'][0]
        (lib2 / one['path']).write_bytes(b'')                 # a recording lost since the import
        with mock.patch.dict(fsd50k.KNOWN_MD5, sums):
            lost = clean.downloads(raw, lib2)
        self.assertEqual(lost['held'], [])

    def test_a_recording_gone_from_the_index_keeps_the_dataset_archives(self):
        raw = helpers.scratch('fsd50k_clear_raw_gone')
        data.dataset(raw, data.three_clips())
        names = fsd50k.files_for('eval')[2:]
        sums = {n: fsd50k.md5_of(raw / n) for n in names}
        lib2 = self.folder / 'library2'
        with mock.patch.dict(fsd50k.KNOWN_MD5, sums):
            fsd50k.add(raw, lib2, 'eval', check_md5=False, log=lambda m: None)
            self.assertTrue(clean.downloads(raw, lib2)['held'])
            index = samples.load_index(lib2)
            index['files'].pop(0)
            samples._write_index(lib2, index)
            self.assertEqual(clean.downloads(raw, lib2)['held'], [])

    def test_the_command_lists_them_and_touches_nothing(self):
        code, out = command('clean', self.folder / 'data', '--downloads', '--library', self.library)
        self.assertEqual(code, 0, out)
        self.assertIn('fully imported', out)
        self.assertIn('delete them yourself', out)
        self.assertIn('CC0', out)                                             # the dataset's other recordings are not taken
        self.assertTrue(all((self.folder / 'data' / n).is_file() for n in self.parts()))


class CommandLine(unittest.TestCase):
    def test_asking_for_zenodo_imports_from_zenodo_even_beside_a_pack_index(self):
        import sfx
        folder = helpers.scratch('fsd50k_packs_source')
        stand_in_library(folder / 'source')
        maker.build(folder / 'source', folder / 'data', log=lambda m: None, also=())
        done = {'pack': 'fsd50k-eval', 'status': 'verified', 'files': 1, 'passing': 1, 'folder': 'x'}
        with mock.patch.object(sfx.fsd50k, 'download') as download, \
                mock.patch.object(sfx.fsd50k, 'add', return_value=done) as add, \
                mock.patch.object(sfx.fsd50k, 'add_packs') as add_packs, mock.patch('builtins.print'):
            code = sfx.main(['samples', '--library', str(folder / 'library'), '--download-fsd50k', str(folder / 'data'),
                             '--source', 'zenodo'])
        self.assertEqual(code, 0)
        download.assert_called_once()
        add.assert_called_once()
        add_packs.assert_not_called()

    def test_add_fsd50k_recognises_a_folder_of_packs(self):
        folder = helpers.scratch('fsd50k_packs_cli')
        stand_in_library(folder / 'source')
        maker.build(folder / 'source', folder / 'packs', log=lambda m: None, also=())
        code, out = command('samples', '--library', folder / 'library', '--add-fsd50k', folder / 'packs')
        self.assertEqual(code, 0, out)
        self.assertIn('2 recordings', out)
        self.assertEqual(len(samples.load_index(folder / 'library')['files']), 2)


if __name__ == '__main__':
    unittest.main()
