"""FSD50K as a sample library: the split-archive reader, the CC0 import with screening, the download, and finding
recordings by their titles, tags and labels. Small stand-in datasets are built here; nothing is fetched."""
import hashlib
import io
import json
import struct
import unittest
import zipfile
import zlib
from pathlib import Path

import numpy as np

import helpers
from sfxlib import fsd50k, samples, synth, wav

CC0 = 'http://creativecommons.org/publicdomain/zero/1.0/'
CCBY = 'http://creativecommons.org/licenses/by/3.0/'


def split_archive(folder, stem, members, split_at, zip64=False, deflate=True):
    """Write an Info-ZIP-style split archive stem.z01 ... stem.zip holding `members` ({name: bytes}); `split_at` are
    byte positions where a new part begins (all before the central directory)."""
    stream, entries = bytearray(b'PK\x07\x08'), []
    for name, data in members.items():
        method = 8 if deflate else 0
        if deflate:
            c = zlib.compressobj(6, zlib.DEFLATED, -15)
            comp = c.compress(data) + c.flush()
        else:
            comp = data
        crc = zlib.crc32(data) & 0xFFFFFFFF
        nb = name.encode('utf-8')
        entries.append((nb, method, crc, len(comp), len(data), len(stream)))
        stream += struct.pack('<IHHHHHIIIHH', 0x04034b50, 20, 0, method, 0, 0, crc, len(comp), len(data), len(nb), 0)
        stream += nb + comp
    starts = [0] + sorted(split_at)
    assert all(s < len(stream) for s in starts)
    last = len(starts) - 1

    def where(pos):
        k = max(i for i, s in enumerate(starts) if s <= pos)
        return k, pos - starts[k]
    cd = bytearray()
    for nb, method, crc, csize, usize, pos in entries:
        disk, off = where(pos)
        if zip64:
            extra = struct.pack('<HHQQQI', 0x0001, 28, usize, csize, off, disk)
            fields = (0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF, 0xFFFFFFFF)
        else:
            extra = b''
            fields = (csize, usize, disk, off)
        cs, us, dk, of = fields
        cd += struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 45, 45, 0, method, 0, 0, crc, cs, us, len(nb), len(extra),
                          0, dk, 0, 0, of) + nb + extra
    cd_off = len(stream) - starts[last]
    stream += cd
    if zip64:
        z64_off = len(stream) - starts[last]
        stream += struct.pack('<IQHHIIQQQQ', 0x06064b50, 44, 45, 45, last, last, len(entries), len(entries), len(cd),
                              cd_off)
        stream += struct.pack('<IIQI', 0x07064b50, last, z64_off, last + 1)
        stream += struct.pack('<IHHHHIIH', 0x06054b50, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    else:
        stream += struct.pack('<IHHHHIIH', 0x06054b50, last, last, len(entries), len(entries), len(cd), cd_off, 0)
    bounds = starts + [len(stream)]
    paths = []
    for i in range(len(starts)):
        name = f'{stem}.zip' if i == last else f'{stem}.z{i + 1:02d}'
        (Path(folder) / name).write_bytes(bytes(stream[bounds[i]:bounds[i + 1]]))
        paths.append(Path(folder) / name)
    return paths


def wav_bytes(x, rate=44100):
    return wav.encode(np.asarray(x, dtype=np.float64), rate=rate, bits=16, dither=False)


def clean_clip(seconds=1.0, rate=44100, seed=1):
    """A decaying noise burst over a very low floor: passes every screen."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    return 0.15 * rng.standard_normal(len(t)) * np.exp(-t / 0.15) + 1e-4 * rng.standard_normal(len(t))


def noisy_clip(seconds=1.0, rate=44100, seed=2):
    """Steady quiet noise: little range between body and floor, and a low peak."""
    rng = np.random.default_rng(seed)
    return 0.02 * rng.standard_normal(int(seconds * rate))


def dataset(folder, clips, zip64=True, split=True):
    """A stand-in FSD50K folder: metadata and ground-truth zips and the eval audio split archive. `clips` maps a clip
    number to (licence URL, title, labels, audio, predominant)."""
    folder = Path(folder)
    info = {k: {'title': v[1], 'description': v[5] if len(v) > 5 else '', 'tags': ['tag-' + v[1]], 'license': v[0],
                'uploader': f'user{k}'}
            for k, v in clips.items()}
    ratings = {k: {'/m/x': [1.0, 1.0] if v[4] else [0.5]} for k, v in clips.items()}
    with zipfile.ZipFile(folder / fsd50k.META_ZIP, 'w') as z:
        z.writestr('FSD50K.metadata/eval_clips_info_FSD50K.json', json.dumps(info))
        z.writestr('FSD50K.metadata/pp_pnp_ratings_FSD50K.json', json.dumps(ratings))
    rows = ['fname,labels,mids'] + [f'{k},"{",".join(v[2])}","/m/x"' for k, v in clips.items()]
    with zipfile.ZipFile(folder / fsd50k.TRUTH_ZIP, 'w') as z:
        z.writestr('FSD50K.ground_truth/eval.csv', '\n'.join(rows) + '\n')
    members = {f'FSD50K.eval_audio/{k}.wav': wav_bytes(v[3]) for k, v in clips.items()}
    total = sum(len(b) + 60 for b in members.values())
    split_archive(folder, 'FSD50K.eval_audio', members, [total // 2] if split else [], zip64=zip64)
    return folder


def three_clips():
    return {'101': (CC0, 'gate_1', ['Door', 'Slam'], clean_clip(), True),
            '202': (CC0, 'room hum', ['Hum'], noisy_clip(), False),
            '303': (CCBY, 'engine', ['Engine'], clean_clip(seed=3), True)}


class SplitArchive(unittest.TestCase):
    def check(self, zip64, deflate):
        folder = helpers.scratch(f'fsd50k-split-{zip64}-{deflate}')
        rng = np.random.default_rng(5)
        data = {f'a/{i}.bin': rng.integers(0, 255, 3000 + 700 * i, dtype=np.uint8).tobytes() for i in range(5)}
        split_archive(folder, 'set', data, [4000, 11000], zip64=zip64, deflate=deflate)
        parts = fsd50k.Parts(folder / 'set.zip')
        try:
            self.assertEqual(len(parts.paths), 3)
            ms = fsd50k.members(parts)
            self.assertEqual([m.name for m in ms], list(data))
            self.assertGreater(len({m.disk for m in ms}), 1)             # members begin in different parts
            for m in ms:
                self.assertEqual(fsd50k.read_member(parts, m), data[m.name])
        finally:
            parts.close()

    def test_members_are_read_across_parts(self):
        self.check(zip64=False, deflate=True)
        self.check(zip64=False, deflate=False)

    def test_zip64_end_records_and_extra_fields_are_read(self):
        self.check(zip64=True, deflate=True)

    def test_a_damaged_member_is_refused(self):
        folder = helpers.scratch('fsd50k-damaged')
        data = {'x.bin': bytes(range(256)) * 20}
        (path,) = split_archive(folder, 'set', data, [], deflate=False)
        blob = bytearray(path.read_bytes())
        blob[100] ^= 0xFF
        path.write_bytes(bytes(blob))
        parts = fsd50k.Parts(path)
        try:
            with self.assertRaisesRegex(fsd50k.FSD50KError, 'CRC'):
                fsd50k.read_member(parts, fsd50k.members(parts)[0])
        finally:
            parts.close()

    def test_a_missing_part_is_refused(self):
        folder = helpers.scratch('fsd50k-missing-part')
        data = {f'{i}.bin': bytes([i]) * 4000 for i in range(4)}
        paths = split_archive(folder, 'set', data, [9000], deflate=False)
        lone = helpers.scratch('fsd50k-missing-part-lone')
        (lone / 'set.zip').write_bytes(paths[-1].read_bytes())
        parts = fsd50k.Parts(lone / 'set.zip')
        try:
            with self.assertRaises(fsd50k.FSD50KError):
                for m in fsd50k.members(parts):
                    fsd50k.read_member(parts, m)
        finally:
            parts.close()


class Screen(unittest.TestCase):
    def test_each_screen_catches_its_fault_and_a_clean_recording_passes(self):
        rate = 44100
        clean = fsd50k.measure(clean_clip(), rate)
        self.assertEqual(fsd50k.screen(clean, True), [])
        clipped = np.clip(10 * clean_clip(), -1, 1)
        self.assertIn('clipped', fsd50k.screen(fsd50k.measure(clipped, rate), True))
        spec = np.fft.rfft(clean_clip())
        spec[np.fft.rfftfreq(len(clean_clip()), 1 / rate) > 15000] = 0          # a lossy encoder's cut
        self.assertIn('lossy original', fsd50k.screen(fsd50k.measure(np.fft.irfft(spec, len(clean_clip())), rate), True))
        self.assertIn('quiet', fsd50k.screen(fsd50k.measure(0.05 * clean_clip(), rate), True))
        self.assertIn('noisy', fsd50k.screen(fsd50k.measure(noisy_clip() * 20, rate), True))
        self.assertEqual(fsd50k.screen(clean, False), ['not predominant'])


class SteadySounds(unittest.TestCase):
    MEASURES = {'peak_db': -3.0, 'clipped_runs': 0, 'floor_db': -20.0, 'range_db': 6.0, 'top_db': -10.0}

    def test_a_steady_sound_is_not_called_noisy_for_having_no_quiet_part(self):
        for rated in ({'Rain': 1.0}, {'Fire': 0.75}, {'Waves_and_surf': 1.0, 'Ocean': 1.0}, {'Engine': 1.0}, {'Crowd': 1.0}):
            self.assertTrue(fsd50k.is_steady(rated), rated)
        self.assertEqual(fsd50k.screen(self.MEASURES, True, steady=True), [])

    def test_an_event_is_not_steady_even_with_wind_behind_it(self):
        for rated in ({'Knock': 1.0, 'Wind': 0.5}, {'Knock': 1.0, 'Wind': 1.0}, {'Door': 1.0}, {'Rain': 0.5}, {},
                      {'Water': 1.0}):
            self.assertFalse(fsd50k.is_steady(rated), rated)
        self.assertEqual(fsd50k.screen(self.MEASURES, True), ['noisy'])

    def test_every_other_screen_still_holds_for_a_steady_sound(self):
        bad = dict(self.MEASURES, clipped_runs=2, top_db=-40.0, peak_db=-30.0)
        self.assertEqual(fsd50k.screen(bad, False, note=True, steady=True),
                         ['clipped', 'lossy original', 'quiet', 'not predominant', 'author note'])

    def test_the_steady_classes_exclude_events_and_broad_parents(self):
        self.assertTrue({'Rain', 'Wind', 'Fire', 'Engine', 'Crowd'} <= fsd50k.STEADY)
        self.assertFalse({'Door', 'Knock', 'Explosion', 'Glass', 'Music', 'Water', 'Vehicle'} & fsd50k.STEADY)

    def test_the_import_reads_which_class_is_predominant(self):
        loud_steady = noisy_clip() * 10
        clips = {'401': (CC0, 'rain', ['Rain'], loud_steady, True), '402': (CC0, 'door', ['Door'], loud_steady, True)}
        library = helpers.scratch('fsd50k-steady-lib')
        fsd50k.add(dataset(helpers.scratch('fsd50k-steady-data'), clips), library, check_md5=False, log=lambda *a: None)
        files = {f['file']: f for f in samples.load_index(library)['files']}
        self.assertEqual(files['401']['screen'], [])
        self.assertEqual(files['402']['screen'], ['noisy'])


class Import(unittest.TestCase):
    def setUp(self):
        self.folder = dataset(helpers.scratch(f'fsd50k-data-{self._testMethodName}'), three_clips())
        self.library = helpers.scratch(f'fsd50k-lib-{self._testMethodName}')

    def test_only_cc0_recordings_are_imported_with_provenance_and_screen(self):
        r = fsd50k.add(self.folder, self.library, check_md5=False, log=lambda *a: None)
        self.assertEqual((r['pack'], r['files'], r['passing']), ('fsd50k-eval', 2, 1))
        index = samples.load_index(self.library)
        files = {f['file']: f for f in index['files']}
        self.assertEqual(sorted(files), ['101', '202'])                    # the CC BY clip is left out
        self.assertFalse((self.library / 'fsd50k-eval' / '303.wav').exists())
        gate = files['101']
        self.assertEqual((gate['title'], gate['author'], gate['labels'], gate['licence']),
                         ('gate_1', 'user101', ['Door', 'Slam'], 'CC0-1.0'))
        self.assertEqual(gate['page'], 'https://freesound.org/s/101/')
        self.assertEqual(gate['screen'], [])
        self.assertIn('noisy', files['202']['screen'])
        self.assertIn('not predominant', files['202']['screen'])
        self.assertEqual(index['packs']['fsd50k-eval']['licence'], 'CC0-1.0')
        self.assertEqual(fsd50k.add(self.folder, self.library, check_md5=False)['status'],
                         'already imported, metadata refreshed')

    def test_files_are_checked_against_the_known_md5_sums(self):
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'MD5'):
            fsd50k.add(self.folder, self.library, log=lambda *a: None)
        self.assertFalse((self.library / samples.INDEX).exists())
        saved = dict(fsd50k.KNOWN_MD5)
        try:
            for name in fsd50k.files_for('eval'):
                fsd50k.KNOWN_MD5[name] = fsd50k.md5_of(self.folder / name)
            r = fsd50k.add(self.folder, self.library, log=lambda *a: None)
        finally:
            fsd50k.KNOWN_MD5.clear()
            fsd50k.KNOWN_MD5.update(saved)
        self.assertEqual(r['status'], 'verified')

    def test_an_interrupted_import_keeps_matching_files_and_refuses_changed_ones(self):
        (self.library / 'fsd50k-eval').mkdir()
        (self.library / 'fsd50k-eval' / '101.wav').write_bytes(wav_bytes(three_clips()['101'][3]))
        self.assertEqual(fsd50k.add(self.folder, self.library, check_md5=False, log=lambda *a: None)['files'], 2)
        other = helpers.scratch('fsd50k-lib-changed')
        (other / 'fsd50k-eval').mkdir()
        (other / 'fsd50k-eval' / '101.wav').write_bytes(b'RIFF not the same')
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'differs'):
            fsd50k.add(self.folder, other, check_md5=False, log=lambda *a: None)
        self.assertEqual((other / 'fsd50k-eval' / '101.wav').read_bytes(), b'RIFF not the same')

    def test_an_unsafe_member_name_is_refused(self):
        folder = helpers.scratch('fsd50k-data-unsafe')
        dataset(folder, three_clips(), split=False)
        members = {'../101.wav': wav_bytes(clean_clip())}
        split_archive(folder, 'FSD50K.eval_audio', members, [])
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'unsafe'):
            fsd50k.add(folder, self.library, check_md5=False, log=lambda *a: None)

    def test_imported_recordings_play_and_name_their_own_author(self):
        fsd50k.add(self.folder, self.library, check_md5=False, log=lambda *a: None)
        library = samples.Library(self.library)
        layer = {'source': {'type': 'sample', 'library': 'fsd50k-eval', 'file': '101'}, 'length': 0.5}
        x = synth.render_sound({'layers': [layer]}, seed=1, samples=library)
        self.assertGreater(np.max(np.abs(x)), 0.1)
        p = library.provenance('fsd50k-eval', '101')
        self.assertEqual((p['author'], p['page'], p['recording'], p['licence']),
                         ('user101', 'https://freesound.org/s/101/', 'gate_1', 'CC0-1.0'))

    def test_a_mono_recording_plays_in_a_stereo_sound_and_pans(self):
        # FSD50K's recordings are mono; a stereo sound used to assume every recording had two channels
        fsd50k.add(self.folder, self.library, check_md5=False, log=lambda *a: None)
        library = samples.Library(self.library)
        src = {'type': 'sample', 'library': 'fsd50k-eval', 'file': '101'}
        mono = synth.render_sound({'layers': [{'source': src, 'length': 0.5}]}, seed=1, samples=library)
        centre = synth.render_sound({'layers': [{'source': src, 'length': 0.5}]}, seed=1, channels=2,
                                    samples=library)
        self.assertEqual(centre.shape[1], 2)
        np.testing.assert_allclose(centre[:, 0], centre[:, 1], atol=1e-12)
        np.testing.assert_allclose(np.sum(centre ** 2), np.sum(mono.reshape(len(mono), -1) ** 2), rtol=0.02)
        left = synth.render_sound({'layers': [{'source': src, 'length': 0.5, 'pan': -0.8}]}, seed=1, channels=2,
                                  samples=library)
        self.assertGreater(np.sum(left[:, 0] ** 2), 5 * np.sum(left[:, 1] ** 2))


class AuthorNotesThatWaive(unittest.TestCase):
    def test_saying_no_credit_is_needed_is_not_a_request(self):
        for text in ('A man walks across a marble hall. NO ATTRIBUTION. I explicitly give permission to use this sound '
                     'without attribution.',
                     'Feel free to use this for anything you so choose. No credit needed.',
                     'You may use this sound for whatever you wish--no attribution required. :) Enjoy!',
                     'Taking off a poster. No attribution needed, use it freely.'):
            self.assertIsNone(fsd50k.author_note(text), text)

    def test_any_condition_or_wish_beside_it_stays_a_note(self):
        for text in ('No attribution needed, all rights waived - just send me a message if and how you use this.',
                     'Do not use this without attribution.',
                     "No credit needed, but I'd love to hear what you make.",
                     'Use of this sound does not require a credit, but a link to my work is appreciated.',
                     'No attribution required for non-commercial use only.',
                     'Credit me if used! Danke!',
                     'Free for personal use; ask for permission first.',
                     'No credit needed, ask for permission first.',
                     'No credit needed. Please send me a copy.',
                     'No attribution required. Send me a link when it is out.',
                     'Send me a copy of your game.'):
            self.assertIsNotNone(fsd50k.author_note(text), text)

    def test_a_request_late_in_a_long_description_is_what_the_note_shows(self):
        text = 'Recorded on a quiet morning near the river with two microphones. ' * 12 + 'Please credit me as Someone.'
        note = fsd50k.author_note(text)
        self.assertIn('Please credit me', note)
        self.assertLessEqual(len(note), fsd50k.NOTE_CHARS)


class AuthorNotes(unittest.TestCase):
    """CC0 puts no condition on a recording, but some authors ask for credit or permission in its description; such a
    recording is flagged so the agent reads the note and respects it."""

    def test_a_description_asking_for_credit_is_flagged_and_kept_out_of_passing(self):
        clips = three_clips()
        clips['101'] = clips['101'] + ('Recorded at the old cemetery. Free to use, but please credit me in your game.',)
        folder = dataset(helpers.scratch('fsd50k-notes-data'), clips)
        library = helpers.scratch('fsd50k-notes-lib')
        r = fsd50k.add(folder, library, check_md5=False, log=lambda *a: None)
        self.assertEqual(r['passing'], 0)
        index = samples.load_index(library)
        files = {f['file']: f for f in index['files']}
        self.assertIn('author note', files['101']['screen'])
        self.assertIn('please credit me', files['101']['author_note'])
        self.assertNotIn('author_note', files['202'])
        self.assertEqual(samples.find(index, 'gate', passing=True), [])
        self.assertEqual([f['file'] for f in samples.find(index, 'gate')], ['101'])

    def test_importing_again_refreshes_notes_and_screens_without_touching_the_audio(self):
        library = helpers.scratch('fsd50k-refresh-lib')
        fsd50k.add(dataset(helpers.scratch('fsd50k-refresh-data-1'), three_clips()), library, check_md5=False,
                   log=lambda *a: None)
        audio = (library / 'fsd50k-eval' / '101.wav').read_bytes()
        clips = three_clips()
        clips['101'] = clips['101'] + ('Let me know if you use it commercially.',)
        r = fsd50k.add(dataset(helpers.scratch('fsd50k-refresh-data-2'), clips), library, check_md5=False,
                       log=lambda *a: None)
        self.assertEqual((r['status'], r['files'], r['passing']), ('already imported, metadata refreshed', 2, 0))
        files = {f['file']: f for f in samples.load_index(library)['files']}
        self.assertIn('author note', files['101']['screen'])
        self.assertEqual((library / 'fsd50k-eval' / '101.wav').read_bytes(), audio)
        self.assertEqual(samples.load_index(library)['packs']['fsd50k-eval']['passing'], 0)


class Download(unittest.TestCase):
    class Server:
        def __init__(self, files, honour_range=True, checksum_override=None):
            self.files, self.honour_range, self.calls = files, honour_range, []
            self.checksum_override = checksum_override or {}

        def __call__(self, url, headers):
            self.calls.append((url, dict(headers)))
            if url == fsd50k.FILES_API:
                entries = [{'key': k, 'size': len(v),
                            'checksum': self.checksum_override.get(k, 'md5:' + hashlib.md5(v).hexdigest()),
                            'links': {'content': f'{fsd50k.FILES_API}/{k}/content'}} for k, v in self.files.items()]
                return Download.Response(200, json.dumps({'entries': entries}).encode())
            key = url.split('/')[-2]
            data = self.files[key]
            rng = headers.get('Range')
            if rng and self.honour_range:
                start = int(rng.split('=')[1].rstrip('-'))
                return Download.Response(206, data[start:])
            return Download.Response(200, data)

    class Response:
        def __init__(self, status, body):
            self.status, self.headers, self._b = status, {}, io.BytesIO(body)

        def read(self, n=-1):
            return self._b.read(n)

    def setUp(self):
        self.saved = dict(fsd50k.KNOWN_MD5)
        self.files = {name: f'content of {name}'.encode() * 50 for name in fsd50k.files_for('eval')}
        for k, v in self.files.items():
            fsd50k.KNOWN_MD5[k] = hashlib.md5(v).hexdigest()

    def tearDown(self):
        fsd50k.KNOWN_MD5.clear()
        fsd50k.KNOWN_MD5.update(self.saved)

    def test_files_arrive_checked_with_the_crawl_delay_and_a_part_resumes(self):
        folder = helpers.scratch('fsd50k-download')
        part_name = fsd50k.files_for('eval')[2]
        (folder / f'{part_name}.part').write_bytes(self.files[part_name][:100])
        server, slept = self.Server(self.files), []
        fsd50k.download(folder, 'eval', opener=server, sleep=slept.append, log=lambda *a: None)
        for k, v in self.files.items():
            self.assertEqual((folder / k).read_bytes(), v)
        self.assertEqual(slept, [fsd50k.CRAWL_DELAY_S] * len(self.files))
        ranged = [h for u, h in server.calls if part_name in u]
        self.assertEqual(ranged, [{'Range': 'bytes=100-'}])
        again = self.Server(self.files)
        fsd50k.download(folder, 'eval', opener=again, sleep=slept.append, log=lambda *a: None)
        self.assertEqual(again.calls, [])                                   # all there: nothing fetched

    def test_a_server_ignoring_the_range_still_gives_the_whole_file(self):
        folder = helpers.scratch('fsd50k-download-norange')
        name = fsd50k.files_for('eval')[0]
        (folder / f'{name}.part').write_bytes(b'stale bytes')
        fsd50k.download(folder, 'eval', opener=self.Server(self.files, honour_range=False), sleep=lambda s: None,
                        log=lambda *a: None)
        self.assertEqual((folder / name).read_bytes(), self.files[name])

    def test_a_changed_record_is_refused(self):
        folder = helpers.scratch('fsd50k-download-changed')
        name = fsd50k.files_for('eval')[0]
        server = self.Server(self.files, checksum_override={name: 'md5:' + '0' * 32})
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'changed'):
            fsd50k.download(folder, 'eval', opener=server, sleep=lambda s: None, log=lambda *a: None)
        self.assertFalse((folder / name).exists())


class DownloadSurvives(unittest.TestCase):
    """A long download meets dropped connections, restarts of the machine and a damaged partial file."""
    Server = Download.Server
    setUp = Download.setUp
    tearDown = Download.tearDown

    class Flaky:
        """Serves the files, but the first `drops` content responses break off after `after` bytes."""

        def __init__(self, files, drops=1, after=120, content_range=True, then=None):
            self.files, self.drops, self.after, self.calls, self.content_range = files, drops, after, [], content_range
            self.then = after if then is None else then

        def __call__(self, url, headers):
            self.calls.append((url, dict(headers)))
            if url == fsd50k.FILES_API:
                entries = [{'key': k, 'size': len(v), 'checksum': 'md5:' + hashlib.md5(v).hexdigest(),
                            'links': {'content': f'{fsd50k.FILES_API}/{k}/content'}} for k, v in self.files.items()]
                return Download.Response(200, json.dumps({'entries': entries}).encode())
            data = self.files[url.split('/')[-2]]
            start = int(headers['Range'].split('=')[1].rstrip('-')) if headers.get('Range') else 0
            body = data[start:]
            drop = self.drops > 0
            self.drops -= int(drop)
            response = DownloadSurvives.Breaking(206 if start else 200, body, self.after if drop else None)
            if drop:
                self.after = self.then
            if start and self.content_range:
                response.headers = {'Content-Range': f'bytes {start}-{len(data) - 1}/{len(data)}'}
            return response

    class Breaking(Download.Response):
        def __init__(self, status, body, after):
            super().__init__(status, body)
            self.after, self.sent = after, 0

        def read(self, n=-1):
            if self.after is not None and self.sent >= self.after:
                raise ConnectionResetError('the connection dropped')
            limit = n if self.after is None else min(n, self.after - self.sent)
            block = self._b.read(limit)
            self.sent += len(block)
            return block

    def fetch(self, folder, server, slept=None):
        return fsd50k.download(folder, 'eval', opener=server, sleep=(slept if slept is not None else []).append,
                               log=lambda *a: None)

    def test_a_dropped_connection_is_resumed_where_it_stopped(self):
        folder = helpers.scratch('fsd50k-download-dropped')
        server, slept = self.Flaky(self.files), []
        self.fetch(folder, server, slept)
        for k, v in self.files.items():
            self.assertEqual((folder / k).read_bytes(), v)
        first = fsd50k.files_for('eval')[0]
        ranges = [h.get('Range') for u, h in server.calls if first in u]
        self.assertEqual(ranges, [None, 'bytes=120-'])
        self.assertEqual(len(slept), len(self.files) + 1)                  # the retry waits as a new request does

    def test_a_connection_that_keeps_dropping_stops_with_the_part_kept(self):
        folder = helpers.scratch('fsd50k-download-dead')
        server = self.Flaky(self.files, drops=10 ** 6, after=7, then=0)   # some bytes once, then nothing
        with self.assertRaisesRegex(fsd50k.FSD50KError, 'run the download again'):
            self.fetch(folder, server)
        first = fsd50k.files_for('eval')[0]
        self.assertGreater((folder / f'{first}.part').stat().st_size, 0)

    def test_a_damaged_part_is_set_aside_and_fetched_again(self):
        folder = helpers.scratch('fsd50k-download-damaged')
        first = fsd50k.files_for('eval')[0]
        damaged = b'x' * len(self.files[first])                          # full length, wrong bytes
        (folder / f'{first}.part').write_bytes(damaged)
        self.fetch(folder, self.Server(self.files))
        self.assertEqual((folder / first).read_bytes(), self.files[first])
        kept = sorted(folder.glob(f'{first}.bad-*'))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), damaged)                   # set aside, never deleted

    def test_a_part_longer_than_the_file_is_set_aside(self):
        folder = helpers.scratch('fsd50k-download-long')
        first = fsd50k.files_for('eval')[0]
        (folder / f'{first}.part').write_bytes(b'y' * (len(self.files[first]) + 5))
        server = self.Server(self.files)
        self.fetch(folder, server)
        self.assertEqual((folder / first).read_bytes(), self.files[first])
        self.assertEqual(len(list(folder.glob(f'{first}.bad-*'))), 1)
        self.assertNotIn('Range', [k for u, h in server.calls if first in u for k in h])

    def test_a_range_answered_from_elsewhere_starts_over(self):
        folder = helpers.scratch('fsd50k-download-elsewhere')
        first = fsd50k.files_for('eval')[0]
        (folder / f'{first}.part').write_bytes(self.files[first][:100])
        server = self.Server(self.files)
        real = server.__call__

        class Wrong:
            def __call__(self, url, headers):
                r = real(url, headers)
                if headers.get('Range') and first in url:
                    r = Download.Response(206, self_files[first])
                    r.headers = {'Content-Range': f'bytes 0-{len(self_files[first]) - 1}/{len(self_files[first])}'}
                return r
        self_files = self.files
        self.fetch(folder, Wrong())
        self.assertEqual((folder / first).read_bytes(), self.files[first])


class CommandLine(unittest.TestCase):
    def test_download_only_fetches_and_does_not_import(self):
        import sfx
        from unittest import mock
        folder = helpers.scratch('fsd50k-cli-download-only')
        with mock.patch.object(fsd50k, 'download', return_value=[]) as got,                 mock.patch.object(fsd50k, 'add') as imported, mock.patch('builtins.print'):
            code = sfx.main(['samples', '--library', str(folder / 'lib'), '--download-fsd50k', str(folder / 'data'),
                             '--set', 'dev', '--source', 'zenodo', '--download-only'])
        self.assertEqual(code, 0)
        got.assert_called_once()
        imported.assert_not_called()
        with mock.patch.object(fsd50k, 'download', return_value=[]),                 mock.patch.object(fsd50k, 'add', return_value={'pack': 'p', 'status': 's', 'files': 0, 'passing': 0,
                                                                'folder': 'f'}) as imported,                 mock.patch('builtins.print'):
            sfx.main(['samples', '--library', str(folder / 'lib'), '--download-fsd50k', str(folder / 'data'),
                      '--source', 'zenodo'])
        imported.assert_called_once()


def real_fsd50k():
    """The FSD50K library named in tools/local.json ('fsd50k'), or None (or --quick)."""
    import os
    path = helpers.local().get('fsd50k')
    if os.environ.get('SFX_QUICK') == '1' or not path or not (Path(path) / samples.INDEX).is_file():
        return None
    return path


@unittest.skipUnless(real_fsd50k(), 'no FSD50K library in tools/local.json (or --quick)')
class RealLibrary(unittest.TestCase):
    def test_the_evaluation_set_is_imported_verified_and_plays(self):
        index = samples.load_index(real_fsd50k())
        pack = index['packs']['fsd50k-eval']
        self.assertEqual((pack['status'], pack['licence'], pack['files'], pack['passing']),
                         ('verified', 'CC0-1.0', 4914, 1814))           # steady sounds no longer called noisy
        self.assertTrue(all(f['licence'] == 'CC0-1.0' for f in index['files'] if f['library'] == 'fsd50k-eval'))
        library = samples.Library(real_fsd50k())
        gate = {'type': 'sample', 'library': 'fsd50k-eval', 'file': '54269', 'offset': 3.45}
        x = synth.render_sound({'layers': [{'source': gate, 'length': 2.1}]}, seed=1, samples=library)
        y = synth.render_sound({'layers': [{'source': dict(gate, denoise={'noise': [0.06, 0.36]}), 'length': 2.1}]},
                               seed=1, samples=library)
        # between the squeal and the strike: the outdoor noise there is not quite the noise of the stretch, so it
        # falls about 5 dB (measured 2026-11-21), less than the 15 dB inside the stretch itself
        gap = slice(int(0.8 * 48000), int(1.1 * 48000))
        self.assertLess(float(np.sqrt(np.mean(y[gap] ** 2))), 0.7 * float(np.sqrt(np.mean(x[gap] ** 2))))
        strike = slice(int(1.13 * 48000), int(1.3 * 48000))
        self.assertAlmostEqual(float(np.sqrt(np.mean(y[strike] ** 2))), float(np.sqrt(np.mean(x[strike] ** 2))),
                               delta=0.05 * float(np.sqrt(np.mean(x[strike] ** 2))))


class Find(unittest.TestCase):
    def test_titles_tags_and_labels_are_searched_and_screens_can_filter(self):
        folder = dataset(helpers.scratch('fsd50k-find-data'), three_clips())
        library = helpers.scratch('fsd50k-find-lib')
        fsd50k.add(folder, library, check_md5=False, log=lambda *a: None)
        index = samples.load_index(library)
        self.assertEqual([f['file'] for f in samples.find(index, 'gate')], ['101'])       # title
        self.assertEqual([f['file'] for f in samples.find(index, 'slam')], ['101'])       # label
        self.assertEqual([f['file'] for f in samples.find(index, 'tag-room')], ['202'])   # tag
        self.assertEqual([f['file'] for f in samples.find(index, 'fsd50k')], ['101', '202'])
        self.assertEqual([f['file'] for f in samples.find(index, 'fsd50k', passing=True)], ['101'])


if __name__ == '__main__':
    unittest.main()
