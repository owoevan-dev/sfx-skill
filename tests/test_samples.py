"""Recorded sample layers: importing sounds a person has (a folder or a zip; the licence read from its files or named
by the person, refused when unknown, contradicted or forbidding adaptations; safe extraction; duplicates; the index),
finding files, and playing them as layers (rate, channels, offset, reverse, pitch, takes per variant), with the
licence checked again at render and the recordings, and what their licences ask, named in the render's records."""
import hashlib
import io
import json
import unittest
import zipfile

import numpy as np

import helpers
from sfxlib import licences, project, samples, synth, variants, wav

RATE = 48000
CC0_TEXT = ('Sound pack (1.0)\n\nLicense: Creative Commons Zero (CC0)\n'
            'http://creativecommons.org/publicdomain/zero/1.0/\nFree to use in any project.\n')
BY_TEXT = 'These sounds are licensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/\n'
ND_TEXT = 'Licence: https://creativecommons.org/licenses/by-nd/4.0/\n'


def tone(freq, seconds, rate=44100, channels=2, amp=0.5, right=1.0):
    t = np.arange(int(seconds * rate)) / rate
    x = amp * np.sin(2 * np.pi * freq * t) * np.exp(-3 * t)
    if channels == 1:
        return x[:, None]
    return np.stack([x, right * x], axis=1)


def wav_bytes(x, rate=44100):
    return wav.encode(x, rate=rate, bits=24, dither=False)


def pack_members(licence_text=CC0_TEXT, extra=None):
    members = {'Audio/woodKnock_light_000.wav': wav_bytes(tone(600, 0.3)),
               'Audio/woodKnock_light_001.wav': wav_bytes(tone(500, 0.3)),
               'Audio/footstep wood (1).wav': wav_bytes(tone(300, 0.2, channels=1)),
               'Shop.url': b'[InternetShortcut]\r\nURL=https://example.invalid/\r\n',
               'Audio/desktop.ini': b'[.ShellClassInfo]\r\n',
               '__MACOSX/Audio/._woodKnock_light_000.wav': b'\x00\x05\x16\x07'}
    if licence_text is not None:
        members['License.txt'] = licence_text.encode('utf-8')
    members.update(extra or {})
    return members


def as_folder(folder, members):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in members.items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return folder


def as_zip(path, members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in members.items():
            z.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


def dominant_hz(x):
    x = np.asarray(x, dtype=np.float64)
    x = x[:, 0] if x.ndim == 2 else x
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / RATE)[int(np.argmax(spectrum))])


class Licences(unittest.TestCase):
    def test_names_urls_and_conditions(self):
        self.assertEqual(licences.detect(CC0_TEXT)[0][0], 'CC0-1.0')
        self.assertEqual(licences.detect(BY_TEXT)[0][0], 'CC-BY-4.0')
        self.assertEqual(licences.detect('Attribution-NonCommercial 4.0 International')[0][0], 'CC-BY-NC-4.0')
        self.assertEqual(licences.detect('see creativecommons.org/licenses/by-nc-sa/3.0/')[0][0], 'CC-BY-NC-SA-3.0')
        self.assertEqual(licences.detect('Thanks for downloading!'), [])
        for named, expected in (('cc0', 'CC0-1.0'), ('CC BY 4.0', 'CC-BY-4.0'), ('cc-by-sa-4', 'CC-BY-SA-4.0'),
                                ('own', 'own'), ('Public Domain', 'public domain'), ('nonsense', None)):
            self.assertEqual(licences.normalize(named), expected, named)
        self.assertEqual(licences.conditions('CC0-1.0'), [])
        self.assertEqual(licences.conditions('CC-BY-SA-4.0'), ['attribution', 'share-alike'])
        self.assertFalse(licences.usable('CC-BY-ND-4.0')[0])
        self.assertTrue(licences.usable('CC-BY-NC-4.0')[0])

    def test_the_note_says_no_credit_or_gives_the_credits(self):
        free = [{'file': 'a', 'licence': 'CC0-1.0', 'conditions': []}]
        self.assertIn('no credit', licences.note(free))
        bound = free + [{'file': 'b', 'recording': 'Door slam', 'author': 'someone', 'licence': 'CC-BY-4.0',
                         'conditions': ['attribution'], 'page': 'https://example.invalid/b'}]
        text = licences.note(bound)
        self.assertIn('attribution', text)
        self.assertIn('"Door slam" by someone (https://example.invalid/b), CC-BY-4.0', text)


class Import(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'samples_{self._testMethodName}')
        self.library = self.folder / 'library'

    def test_a_folder_with_a_cc0_licence_file_is_imported_and_indexed(self):
        src = as_folder(self.folder / 'My Wood Pack', pack_members())
        r = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual((r['pack'], r['licence'], r['conditions'], r['added']), ('my-wood-pack', 'CC0-1.0', [], 3))
        self.assertIn('licence file', r['licence_status'])
        index = samples.load_index(self.library)
        files = {f['file']: f for f in index['files']}
        self.assertEqual(sorted(files), ['footstep_wood_1', 'woodKnock_light_000', 'woodKnock_light_001'])
        entry = files['woodKnock_light_000']
        self.assertEqual((entry['library'], entry['channels'], entry['rate'], entry['licence']),
                         ('my-wood-pack', 2, 44100, 'CC0-1.0'))
        self.assertEqual(entry['source_path'], 'Audio/woodKnock_light_000.wav')
        self.assertIn('peak_db', entry['measures'])
        self.assertIsInstance(entry['screen'], list)
        self.assertNotIn('not predominant', entry['screen'])           # no ratings: not a reason
        self.assertEqual(entry['sha256'], hashlib.sha256((self.library / entry['path']).read_bytes()).hexdigest())
        self.assertEqual(files['footstep_wood_1']['title'], 'footstep wood (1)')
        names = {p.name for p in (self.library / 'my-wood-pack').rglob('*')}
        self.assertTrue(any(n.startswith('LICENCE-') for n in names))
        for left_out in ('Shop.url', 'desktop.ini', '._woodKnock_light_000.wav'):
            self.assertNotIn(left_out, names)

    def test_a_zip_is_imported_the_same_way(self):
        r = samples.add(as_zip(self.folder / 'pack.zip', pack_members()), self.library, licence='CC0-1.0')
        self.assertEqual((r['pack'], r['added']), ('pack', 3))

    def test_without_a_licence_the_person_is_asked_and_their_word_is_kept(self):
        src = as_folder(self.folder / 'mine', pack_members(licence_text=None))
        with self.assertRaisesRegex(samples.SampleError, 'ask the person'):
            samples.add(src, self.library, licence=None)
        self.assertFalse((self.library / samples.INDEX).exists())
        r = samples.add(src, self.library, licence='own', author='the person')
        self.assertEqual((r['licence'], r['conditions'], r['licence_status']), ('own', [], 'named by the person'))
        entry = samples.load_index(self.library)['files'][0]
        self.assertEqual(entry['author'], 'the person')

    def test_a_named_licence_must_agree_with_the_files(self):
        src = as_folder(self.folder / 'by', pack_members(licence_text=BY_TEXT))
        with self.assertRaisesRegex(samples.SampleError, 'not CC0-1.0'):
            samples.add(src, self.library, licence='CC0')
        r = samples.add(src, self.library, licence='CC BY 4.0', author='A. Recordist', page='https://example.invalid/p')
        self.assertEqual((r['licence'], r['conditions']), ('CC-BY-4.0', ['attribution']))
        self.assertIn('matches', r['licence_status'])

    def test_a_licence_forbidding_adaptations_is_refused(self):
        src = as_folder(self.folder / 'nd', pack_members(licence_text=ND_TEXT))
        with self.assertRaisesRegex(samples.SampleError, 'adapted material'):
            samples.add(src, self.library, licence='CC BY-ND 4.0')
        with self.assertRaisesRegex(samples.SampleError, 'not a licence'):
            samples.add(as_folder(self.folder / 'x', pack_members(licence_text=None)), self.library, licence='free-ish')

    def test_unsafe_member_names_refuse_the_whole_archive(self):
        for bad in ('../evil.wav', '/abs/evil.wav', 'Z' + ':/evil.wav', 'Audio/../../evil.wav'):   # a drive
            with self.assertRaises(samples.SampleError, msg=bad):
                samples.add(as_zip(self.folder / 'unsafe.zip', pack_members(extra={bad: wav_bytes(tone(100, 0.1))})),
                            self.library, licence='CC0-1.0')
        self.assertFalse(self.library.exists())
        self.assertFalse((self.folder.parent / 'evil.wav').exists())

    def test_importing_again_leaves_out_duplicates_and_adds_new_files(self):
        src = as_folder(self.folder / 'pack', pack_members())
        samples.add(src, self.library, licence='CC0-1.0')
        again = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual((again['added'], again['duplicates'], again['files']), (0, 3, 3))
        (src / 'Audio' / 'woodKnock_light_000 copy.wav').write_bytes(wav_bytes(tone(450, 0.3)))
        more = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual((more['added'], more['files']), (1, 4))

    def test_a_file_that_needs_ffmpeg_is_left_out_with_its_reason(self):
        src = as_folder(self.folder / 'mixed', pack_members(extra={'Audio/extra.ogg': b'OggS not really'}))
        r = samples.add(src, self.library, licence='CC0-1.0')
        self.assertEqual(r['added'], 3)
        self.assertEqual([rel for rel, _ in r['skipped']], ['Audio/extra.ogg'])
        self.assertIn('FFmpeg', r['skipped'][0][1])

    def test_find_matches_names_titles_and_packs(self):
        samples.add(as_folder(self.folder / 'Wood Pack', pack_members()), self.library, licence='CC0-1.0')
        index = samples.load_index(self.library)
        self.assertEqual([f['file'] for f in samples.find(index, 'WOOD_knock light')],
                         ['woodKnock_light_000', 'woodKnock_light_001'])
        self.assertEqual(len(samples.find(index, 'wood pack')), 3)
        self.assertEqual([f['file'] for f in samples.find(index, 'footstep wood (1)')], ['footstep_wood_1'])


class Layer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = helpers.scratch('samples_layer')
        cls.library_path = folder / 'library'
        samples.add(as_folder(folder / 'test-sounds', pack_members(
            extra={'Audio/high_7k.wav': wav_bytes(tone(7000, 0.3))})), cls.library_path, licence='CC0-1.0')
        cls.library = samples.Library(cls.library_path)

    def render(self, src, length=0.3, channels=1):
        layer = {'source': dict({'type': 'sample', 'library': 'test-sounds'}, **src), 'length': length}
        return synth.render_sound({'layers': [layer]}, seed=1, channels=channels, samples=self.library)

    def test_a_recording_plays_at_its_pitch_at_48_khz_and_at_the_recordings_level(self):
        x = self.render({'file': 'woodKnock_light_000'}, length=0.25)
        self.assertAlmostEqual(dominant_hz(x[:9600]), 600, delta=8)
        self.assertAlmostEqual(float(np.max(np.abs(x))), samples.PEAK, delta=0.02)

    def test_offset_reverse_and_cents(self):
        plain = self.render({'file': 'woodKnock_light_000'})
        later = self.render({'file': 'woodKnock_light_000', 'offset': 0.1})
        self.assertLess(float(np.max(np.abs(later[:2400]))), float(np.max(np.abs(plain[:2400]))) * 0.8)
        backwards = self.render({'file': 'woodKnock_light_000', 'reverse': True}, length=0.3)
        self.assertGreater(float(np.max(np.abs(backwards[-4800:]))), float(np.max(np.abs(backwards[:4800]))))
        up = self.render({'file': 'woodKnock_light_000', 'cents': 1200}, length=0.12)
        self.assertAlmostEqual(dominant_hz(up[:4800]), 1200, delta=20)

    def test_four_octaves_each_way_stay_band_limited_and_only_the_needed_part_is_resampled(self):
        down = self.render({'file': 'woodKnock_light_000', 'cents': -3600}, length=2.4)
        self.assertAlmostEqual(dominant_hz(down), 75, delta=3)             # 600 Hz three octaves lower
        env = np.abs(down[:, 0])
        self.assertGreater(float(np.max(env[int(1.6 * RATE):int(2.0 * RATE)])), 0.01)   # 0.3 s now lasts 2.4 s
        # 7 kHz two octaves up is 28 kHz, above 24 kHz: band-limited, it vanishes instead of folding back
        up = self.render({'file': 'high_7k', 'cents': 2400}, length=0.05)
        self.assertLess(float(np.max(np.abs(up))), 1e-3)
        quick = self.render({'file': 'woodKnock_light_000', 'cents': -4800, 'offset': 0.0}, length=0.5)
        self.assertLess(len(quick), int(0.6 * RATE))           # the layer's length, not the 4.8 s the slowing makes
        from unittest import mock
        seen = []
        real = synth.recorded.detune
        with mock.patch.object(synth.recorded, 'detune', side_effect=lambda x, c: (seen.append(len(x)), real(x, c))[1]):
            self.render({'file': 'woodKnock_light_000', 'cents': 2400}, length=0.01)
        self.assertLessEqual(seen[0], int(0.01 * RATE * 4) + 4096 + 1)   # only what the layer reaches, not all 14,400
        for cents, ok in ((4800, True), (-4800, True), (4900, False), (-4900, False)):
            src = {'type': 'sample', 'library': 'test-sounds', 'file': 'woodKnock_light_000', 'cents': cents}
            try:
                project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                    {'id': 'x', 'category': 'impact', 'layers': [{'source': src, 'length': 0.3}]}]})
                accepted = True
            except project.ProjectError:
                accepted = False
            self.assertEqual(accepted, ok, cents)

    def test_a_mono_sound_takes_the_louder_channel(self):
        folder = helpers.scratch('samples_channels')
        samples.add(as_folder(folder / 'test-sounds', pack_members(
            extra={'Audio/woodKnock_light_000.wav': wav_bytes(tone(600, 0.3, right=-0.3))})), folder / 'library', licence='CC0-1.0')
        library = samples.Library(folder / 'library')
        layer = {'source': {'type': 'sample', 'library': 'test-sounds', 'file': 'woodKnock_light_000'}, 'length': 0.3}
        mono = synth.render_sound({'layers': [layer]}, seed=1, channels=1, samples=library)
        stereo = synth.render_sound({'layers': [layer]}, seed=1, channels=2, samples=library)
        self.assertAlmostEqual(float(np.max(np.abs(mono))), samples.PEAK, delta=0.02)
        self.assertGreater(float(np.corrcoef(stereo[:, 0], stereo[:, 1])[0, 1]), 0.99)    # no cancelling pair

    def test_each_variant_takes_its_take_in_turn(self):
        p = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 3, 'sounds': [
            {'id': 'knock', 'category': 'impact', 'layers': [
                {'source': {'type': 'sample', 'library': 'test-sounds',
                            'file': ['woodKnock_light_000', 'woodKnock_light_001']}, 'length': 0.3}]}]})
        sound = p['sounds'][0]
        chosen = [variants.spec_for(sound, p['seed'], v)[0]['layers'][0]['source']['file'] for v in range(3)]
        self.assertEqual(chosen, ['woodKnock_light_000', 'woodKnock_light_001', 'woodKnock_light_000'])

    def test_a_take_can_start_at_its_own_offset(self):
        # a field recording of someone walking holds many steps: each take names its step's time
        p = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 3, 'sounds': [
            {'id': 'step', 'category': 'impact', 'layers': [
                {'source': {'type': 'sample', 'library': 'fsd50k-eval', 'offset': 0.1,
                            'file': [{'file': '427684', 'offset': 1.25}, {'file': '427684', 'offset': 2.5},
                                     '423710']}, 'length': 0.3}]}]})
        sound = p['sounds'][0]
        chosen = [variants.spec_for(sound, p['seed'], v)[0]['layers'][0]['source'] for v in range(3)]
        self.assertEqual([(s['file'], s['offset']) for s in chosen],
                         [('427684', 1.25), ('427684', 2.5), ('423710', 0.1)])

    def test_without_a_library_or_with_a_refused_licence_the_layer_stops(self):
        layer = {'source': {'type': 'sample', 'library': 'test-sounds', 'file': 'woodKnock_light_000'}, 'length': 0.3}
        with self.assertRaises(ValueError) as caught:
            synth.render_sound({'layers': [layer]}, seed=1)
        self.assertIn('--samples', str(caught.exception))
        index = samples.load_index(self.library_path)
        for f in index['files']:
            f['licence'] = 'CC-BY-ND-4.0'
        folder = helpers.scratch('samples_refused')
        (folder / 'index.json').write_text(json.dumps(index), encoding='utf-8')
        with self.assertRaises(samples.SampleError) as caught:
            samples.Library(folder).recording('test-sounds', 'woodKnock_light_000')
        self.assertIn('adapted material', str(caught.exception))
        with self.assertRaises(samples.SampleError) as caught:
            self.library.recording('test-sounds', 'woodKnock_heavy_000')
        self.assertIn('no recording', str(caught.exception))


class Records(unittest.TestCase):
    def render_with(self, licence_text, licence='CC0-1.0', author=''):
        from sfxlib import export, render
        folder = helpers.scratch(f'samples_records_{licence or "file"}')
        samples.add(as_folder(folder / 'test-sounds', pack_members(licence_text=licence_text)), folder / 'library',
                    licence=licence, author=author, page='https://example.invalid/pack')
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [
            {'id': 'knock', 'category': 'impact', 'layers': [
                {'source': {'type': 'sample', 'library': 'test-sounds',
                            'file': ['woodKnock_light_000', 'woodKnock_light_001']}, 'length': 0.3},
                {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.2, 'pitch': 110, 'gain_db': -6}]},
            {'id': 'beep', 'category': 'ui', 'layers': [
                {'source': {'type': 'osc', 'wave': 'triangle'}, 'length': 0.1, 'pitch': 880}]}]})
        out = folder / 'render'
        render.render(proj, out, samples=folder / 'library')
        resolved = json.loads((out / 'resolved.json').read_text(encoding='utf-8'))
        manifest = export.export(out, folder / 'export', 'generic')
        page = (out / 'audition.html').read_text(encoding='utf-8')
        return folder, resolved, manifest, page

    def test_the_render_and_its_exports_name_every_recording_and_say_no_credit_is_needed(self):
        folder, resolved, manifest, page = self.render_with(CC0_TEXT)
        index = {f['file']: f for f in samples.load_index(folder / 'library')['files']}
        self.assertEqual([(r['file'], r['licence'], r['sha256'], r['conditions']) for r in resolved['recordings']],
                         [(name, 'CC0-1.0', index[name]['sha256'], [])
                          for name in ('woodKnock_light_000', 'woodKnock_light_001')])
        self.assertEqual(manifest['recordings'], resolved['recordings'])
        self.assertIn('no credit', manifest['recordings_note'])
        self.assertIn('no credit', page)

    def test_a_licence_with_conditions_is_carried_into_every_record(self):
        _, resolved, manifest, page = self.render_with(BY_TEXT, licence='CC-BY-4.0', author='A. Recordist')
        self.assertTrue(all(r['conditions'] == ['attribution'] for r in resolved['recordings']))
        self.assertIn('Credits:', manifest['recordings_note'])
        self.assertIn('A. Recordist', manifest['recordings_note'])
        self.assertNotIn('no credit', manifest['recordings_note'])
        self.assertIn('A. Recordist', page)

    def test_a_render_without_recordings_records_none(self):
        from sfxlib import render
        folder = helpers.scratch('samples_none')
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'beep', 'category': 'ui', 'layers': [
                {'source': {'type': 'osc', 'wave': 'triangle'}, 'length': 0.1, 'pitch': 880}]}]})
        render.render(proj, folder / 'render')
        resolved = json.loads((folder / 'render' / 'resolved.json').read_text(encoding='utf-8'))
        self.assertNotIn('recordings', resolved)


class CommandLine(unittest.TestCase):
    def run_sfx(self, *args, env=None):
        import subprocess
        import sys
        return subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), *map(str, args)], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', env=env or helpers.child_env(),
                              timeout=300)

    def test_samples_explains_imports_finds_and_renders(self):
        folder = helpers.scratch('samples_cli')
        env = dict(helpers.child_env(), SFX_SKILL_LIBRARY=str(folder / 'default_library'))
        proc = self.run_sfx('samples', env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('--download-fsd50k', proc.stdout)
        self.assertIn('--add FOLDER_OR_ZIP', proc.stdout)
        src = as_folder(folder / 'Wood Pack', pack_members())
        proc = self.run_sfx('samples', '--add', src, env=env)               # it asks first, quoting the licence file
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('Creative Commons Zero', proc.stdout)
        self.assertFalse((folder / 'default_library' / samples.INDEX).exists())
        proc = self.run_sfx('samples', '--add', src, '--licence', 'CC0-1.0', env=env)       # into the default library
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('wood-pack: 3 recordings added', proc.stdout)
        self.assertIn('licence CC0-1.0', proc.stdout)
        self.assertTrue((folder / 'default_library' / samples.INDEX).is_file())
        proc = self.run_sfx('samples', '--find', 'wood knock', env=env)
        self.assertIn('wood-pack/woodKnock_light_000', proc.stdout)
        self.assertNotIn('footstep', proc.stdout)
        unsure = as_folder(folder / 'unsure', pack_members(licence_text=None))
        proc = self.run_sfx('samples', '--add', unsure, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('ask the person', proc.stdout)
        project_path = folder / 'project.json'
        project_path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [
            {'id': 'knock', 'category': 'impact', 'layers': [
                {'source': {'type': 'sample', 'library': 'wood-pack', 'file': 'woodKnock_light_000'},
                 'length': 0.3}]}]}), encoding='utf-8')
        proc = self.run_sfx('render', project_path, '--output', folder / 'render', env=env)   # default library
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
