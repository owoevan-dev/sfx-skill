"""Exports: the files each engine takes, Godot's randomizers and loops (checked in Godot itself when one is found,
SFX_TEST_GODOT or work/godot/bin), the engine suggestions, encoded formats decoded back, and the refusals."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import helpers
from sfxlib import export, mpbridge, project, render, wav

PROJECT = {
    'schema': 'sound-effects/1', 'title': 'Export test', 'variants': 3,
    'sounds': [
        {'id': 'blip', 'category': 'ui', 'vary': {'pitch_cents': 50},
         'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.2, 'pitch': 880.0}]},
        {'id': 'knock', 'category': 'impact', 'recipe': 'impact.wood_knock', 'variants': 2},
        {'id': 'hum', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'channels': 2, 'variants': 1,
         'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'start': 0.0, 'length': 2.0, 'spread': 1.0,
                     'filter': {'type': 'lowpass', 'cutoff': 1500.0}}]},
    ],
}


ffmpeg_or_none, godot_or_none = helpers.ffmpeg_or_none, helpers.godot_or_none


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch(f'export_{cls.__name__.lower()}')
        cls.render = cls.base / 'render'
        render.render(project.validate(PROJECT), cls.render)
        cls.resolved = json.loads((cls.render / 'resolved.json').read_text(encoding='utf-8'))

    def export(self, name, target, **kw):
        out = self.base / name
        return export.export(self.render, out, target, **kw), out


class Godot(Base):
    def test_files_randomizers_and_loops(self):
        manifest, out = self.export('godot', 'godot')
        self.assertEqual(manifest['flags'], [])
        names = sorted(p.name for p in (out / 'sfx').iterdir())
        self.assertEqual(names, ['blip.tres', 'blip_01.wav', 'blip_02.wav', 'blip_03.wav', 'hum_01.wav', 'knock.tres',
                                 'knock_01.wav', 'knock_02.wav'])
        text = (out / 'sfx' / 'blip.tres').read_text(encoding='utf-8')
        self.assertEqual(text, export.godot_randomizer('blip', ['blip_01.wav', 'blip_02.wav', 'blip_03.wav'],
                                                       'res://sfx', 'wav'))
        for line in ('playback_mode = 0', 'random_pitch = 1.0', 'random_volume_offset_db = 0.0', 'streams_count = 3',
                     '[ext_resource type="AudioStreamWAV" path="res://sfx/blip_02.wav" id="2_blip_02"]'):
            self.assertIn(line, text)
        for f in manifest['sounds'][0]['files']:
            self.assertEqual((f['bits'], wav.info(out / f['path'])['bits']), (16, 16))
        recorded = {f['path'] for f in json.loads((out / 'files.json').read_text(encoding='utf-8'))}
        self.assertTrue({f['path'] for s in manifest['sounds'] for f in s['files']} <= recorded)

    def test_a_loop_plays_its_period_exactly_in_godot(self):
        manifest, out = self.export('godot_loop', 'godot')
        master, _ = wav.read(self.render / 'master' / 'hum_01.wav')
        n = len(master)
        info = wav.info(out / 'sfx' / 'hum_01.wav')
        self.assertEqual(info['frames'], n + 1)
        self.assertEqual(info['loops'], [(0, n + 1)])            # the smpl chunk stores loop_end = n
        data, _ = wav.read(out / 'sfx' / 'hum_01.wav')
        self.assertTrue(np.array_equal(data[0], data[-1]))
        played = export.godot_playback(n + 1, 0, n, 3 * n)
        self.assertEqual([i % n for i in played], list(range(n)) * 3)
        # the same loop written with the smpl convention alone (loop_end = n - 1) loses a frame on every pass
        self.assertNotEqual(export.godot_playback(n, 0, n - 1, 3 * n), list(range(n)) * 3)

    def test_the_repeated_frame_is_the_first_frame_bit_for_bit(self):
        """The frame repeated after a Godot loop's end must be the first frame as written, dither included: dithering
        the period with its repeat appended would give the two copies different dither."""
        folder = helpers.scratch('godot_repeat')
        rng = np.random.default_rng(11)
        for seed in range(20):
            x = rng.uniform(-0.5, 0.5, (480, 2))
            path = folder / f'loop_{seed}.wav'
            path.write_bytes(export.godot_loop_wav(x, seed))
            data, info = wav.read(path)
            self.assertEqual((info['frames'], info['loops']), (481, [(0, 481)]))
            self.assertTrue(np.array_equal(data[0], data[-1]), seed)

    def test_res_path(self):
        manifest, out = self.export('godot_res', 'godot', res_path='res://audio/sfx/')
        self.assertIn('path="res://audio/sfx/knock_01.wav"', (out / 'sfx' / 'knock.tres').read_text(encoding='utf-8'))
        self.assertEqual(manifest['res_path'], 'res://audio/sfx')

    @unittest.skipUnless(godot_or_none(), 'no Godot (SFX_TEST_GODOT, or work/godot/bin)')
    def test_godot_itself_reads_the_export(self):
        manifest, out = self.export('godot_real', 'godot')
        proj = self.base / 'godot_project'
        shutil.copytree(out / 'sfx', proj / 'sfx')
        (proj / 'project.godot').write_text('config_version=5\n\n[application]\n\nconfig/name="sfx export test"\n',
                                            encoding='utf-8')
        (proj / 'check.gd').write_text(
            'extends SceneTree\n\nfunc _init():\n'
            '\tvar r = load("res://sfx/blip.tres")\n'
            '\tprint("TRES ", r.get_class(), " ", r.streams_count, " ", r.playback_mode, " ", r.get_stream(2).resource_path)\n'
            '\tvar w = load("res://sfx/hum_01.wav")\n'
            '\tprint("LOOP ", w.loop_mode, " ", w.loop_begin, " ", w.loop_end, " ", w.stereo)\n'
            '\tquit()\n', encoding='utf-8')
        godot = str(godot_or_none())
        subprocess.run([godot, '--headless', '--path', str(proj), '--import'], capture_output=True, timeout=300,
                       env=helpers.child_env())
        result = subprocess.run([godot, '--headless', '--path', str(proj), '--script', 'res://check.gd'],
                                capture_output=True, text=True, timeout=300, env=helpers.child_env())
        lines = [l for l in result.stdout.splitlines() if l.startswith(('TRES', 'LOOP'))]
        n = len(wav.read(self.render / 'master' / 'hum_01.wav')[0])
        self.assertEqual(lines, ['TRES AudioStreamRandomizer 3 0 res://sfx/blip_03.wav', f'LOOP 1 0 {n} true'],
                         result.stdout + result.stderr)


class Targets(Base):
    def test_generic_keeps_the_24_bit_masters(self):
        manifest, out = self.export('generic', 'generic')
        for v in self.resolved['sounds'][0]['variants']:
            name = Path(v['file']).name
            self.assertEqual((out / 'sfx' / '24bit' / name).read_bytes(), (self.render / v['file']).read_bytes())
            self.assertEqual(wav.info(out / 'sfx' / '16bit' / name)['bits'], 16)

    def test_engine_suggestions(self):
        unity, _ = self.export('unity', 'unity')
        by_id = {s['id']: s['engine'] for s in unity['sounds']}
        self.assertEqual((by_id['blip']['compression_format'], by_id['blip']['load_type']), ('PCM', 'Decompress On Load'))
        self.assertIn('Audio Random Container', by_id['blip']['variants'])
        self.assertEqual(by_id['knock']['compression_format'], 'ADPCM')
        self.assertEqual((by_id['hum']['compression_format'], by_id['hum']['load_type']), ('Vorbis', 'Compressed In Memory'))
        unreal, _ = self.export('unreal', 'unreal')
        by_id = {s['id']: s['engine'] for s in unreal['sounds']}
        self.assertIn('Looping', by_id['hum']['loop'])
        self.assertIn('Random', by_id['knock']['variants'])
        web, _ = self.export('web', 'web')
        self.assertIn('loop = true', {s['id']: s['engine'] for s in web['sounds']}['hum']['loop'])
        self.assertTrue(web['sources'])

    def test_roblox_counts_one_upload_per_variant(self):
        with mock.patch.object(export, 'ROBLOX_ALLOWANCE', {'unverified': 5, 'id_verified': 2000}):
            manifest, _ = self.export('roblox', 'roblox')
        self.assertEqual(manifest['roblox_assets']['uploads'], 6)
        self.assertEqual([f['kind'] for f in manifest['flags']], ['roblox_allowance'])
        self.assertEqual({s['id']: s['engine']['assets'] for s in manifest['sounds']}, {'blip': 3, 'knock': 2,
                                                                                         'hum': 1})

    def test_refusals(self):
        cases = [dict(target='fmod'), dict(target='godot', formats=['flac']), dict(target='web', formats=['ogg']),
                 dict(target='godot', res_path='audio/sfx')]
        for i, kw in enumerate(cases):
            with self.assertRaises(export.ExportError, msg=str(kw)):
                export.export(self.render, self.base / f'refused_{i}', **kw)
        taken = self.base / 'taken'
        taken.mkdir()
        (taken / 'x.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(export.ExportError):
            export.export(self.render, taken, 'generic')

    def test_time_shift_is_found(self):
        x = np.random.default_rng(1).standard_normal((48000, 1)) * 0.1
        y = np.concatenate([np.zeros((120, 1)), x])[:48000]
        self.assertAlmostEqual(export.time_shift_ms(x, y), 2.5)

    def test_the_command_line(self):
        out = self.base / 'cli'
        proc = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'export', str(self.render), '--target',
                               'unreal', '--output', str(out)], capture_output=True, text=True, env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('manifest.json', proc.stdout)


@unittest.skipUnless(ffmpeg_or_none(), 'no FFmpeg')
class Encoded(Base):
    def test_formats_are_decoded_back_and_measured(self):
        has_ogg = 'libvorbis' in mpbridge.ffmpeg_features(ffmpeg_or_none())['encoders']    # Homebrew's ffmpeg has none
        manifest, out = self.export('encoded', 'web', formats=['wav', 'mp3'] + ['ogg'] * has_ogg, ffmpeg=ffmpeg_or_none())
        self.assertEqual('web_ogg' in [f['kind'] for f in manifest['flags']], has_ogg)
        for s in manifest['sounds']:
            for f in s['files']:
                self.assertTrue((out / f['path']).is_file())
                if f['format'] in ('ogg', 'mp3'):
                    # a short file's decoded end may move by up to a long Vorbis block (2048 frames); its start may not
                    self.assertLessEqual(abs(f['frames_added']), 2048, f['path'])
                    self.assertAlmostEqual(f['time_shift_ms'], 0.0, delta=1.0, msg=f['path'])
                if f['format'] == 'ogg' and s['use'] == 'loop':
                    self.assertEqual(f['frames_added'], 0, f['path'])

    def test_a_format_this_ffmpeg_cannot_write_is_refused_before_anything_is_written(self):
        out = self.base / 'no_vorbis'
        lacking = {'encoders': {'pcm_s16le', 'libmp3lame', 'flac'}, 'decoders': set(), 'filters': set()}
        with mock.patch.object(export.mpbridge, 'ffmpeg_features', return_value=lacking):
            with self.assertRaises(export.ExportError) as caught:
                export.export(self.render, out, 'web', formats=['wav', 'ogg'], ffmpeg=ffmpeg_or_none())
        self.assertIn('Ogg', str(caught.exception))
        self.assertIn('libvorbis', str(caught.exception))
        self.assertFalse(out.exists())

    def test_flac_holds_exactly_the_16_bit_file(self):
        manifest, out = self.export('flac', 'generic', formats=['wav', 'flac'], ffmpeg=ffmpeg_or_none())
        a, _ = wav.read(out / 'sfx' / '16bit' / 'knock_01.wav')
        b = export.decode(ffmpeg_or_none(), out / 'sfx' / 'knock_01.flac', 1)
        self.assertLess(float(np.max(np.abs(a.reshape(-1, 1).astype(np.float64) - b))), 1e-6)


if __name__ == '__main__':
    unittest.main()
