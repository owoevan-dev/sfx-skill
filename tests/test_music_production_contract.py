"""The contract with the installed music-production (MUSIC_PRODUCTION_CONTRACT.md), run against the copy and the
configuration named in tools/local.json. Skipped without that file or under check_all.py --quick."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import helpers
from sfxlib import mpbridge

LOCAL = helpers.local()
SKIP = os.environ.get('SFX_QUICK') == '1' or not LOCAL.get('music_skill') or not LOCAL.get('music_config')


@unittest.skipIf(SKIP, 'needs tools/local.json with music_skill and music_config (and not --quick)')
class Contract(unittest.TestCase):
    def test_skill_is_found_where_local_json_says(self):
        found = mpbridge.find_music_skill(LOCAL['music_skill'])
        self.assertTrue((found / 'scripts' / 'studio.py').is_file())

    def test_config_names_an_ffmpeg_with_the_needed_codecs_and_filters(self):
        ffmpeg = mpbridge.ffmpeg_path(mpbridge.load_config(LOCAL['music_config']))
        features = mpbridge.ffmpeg_features(ffmpeg, env=helpers.child_env())
        self.assertEqual(mpbridge.missing_features(features), [])

    def test_the_export_command_lines_round_trip(self):
        import numpy as np
        from sfxlib import export, wav
        ffmpeg = mpbridge.ffmpeg_path(mpbridge.load_config(LOCAL['music_config']))
        folder = helpers.scratch('contract_codecs')
        t = np.arange(48000) / 48000
        x = (0.3 * np.sin(2 * np.pi * 440 * t))[:, None]
        wav.write(folder / 'master.wav', x, bits=24, dither=False)
        for fmt in ('ogg', 'mp3'):
            export.encode(ffmpeg, fmt, folder / 'master.wav', folder / f'out.{fmt}')
        export.encode(ffmpeg, 'flac', wav.encode(x, bits=16, dither=False), folder / 'out.flac')
        for fmt in ('ogg', 'mp3', 'flac'):
            y = export.decode(ffmpeg, folder / f'out.{fmt}', 1)
            self.assertGreater(len(y), 40000, fmt)
            self.assertLess(abs(float(np.sqrt(np.mean(y[:40000] ** 2))) - 0.3 / np.sqrt(2)), 0.01, fmt)

    def test_the_config_names_fluidsynth_and_banks_whose_sound_needs_no_credit(self):
        import json
        from sfxlib import instruments
        config = json.loads(Path(LOCAL['music_config']).read_text(encoding='utf-8'))
        fluidsynth = instruments.Provider(config, Path('.')).fluidsynth()[0]
        proc = subprocess.run([str(fluidsynth), '--version'], capture_output=True, text=True)
        self.assertIn('FluidSynth', proc.stdout)
        self.assertTrue(instruments.allowed(config['soundfont']['license']))
        named = config.get('soundfonts') or {}              # a default setup has only the default bank
        refused = [name for name, entry in named.items() if not instruments.allowed(entry.get('license'))]
        self.assertEqual(refused, [])

    def test_every_configured_bank_shows_its_presets(self):
        """sfx.py banks reads each bank file's preset headers (SF2 and SF3 alike): every bank of the configuration
        opens and lists presets, and the default bank holds a General MIDI piano at 0:0."""
        import json
        from sfxlib import instruments
        config = json.loads(Path(LOCAL['music_config']).read_text(encoding='utf-8'))
        rows = instruments.list_banks(config)
        self.assertEqual([r['name'] for r in rows if not r['presets']], [])
        default = [r for r in rows if r['name'] == 'default'][0]
        self.assertIn(0, [program for bank, program, _ in default['presets'] if bank == 0])

    def test_a_surge_take_is_recorded_through_its_host_and_replayed(self):
        config = json.loads(Path(LOCAL['music_config']).read_text(encoding='utf-8'))
        if not (config.get('surge') or {}).get('catalog'):
            self.assertNotIn('surge', helpers.REQUIRED, 'this run needs Surge XT and its setup came out without it')
            self.skipTest('this music-production setup has no Surge XT with its patches')
        from sfxlib import surge
        folder = helpers.scratch('contract_surge') / surge.FOLDER
        source = {'type': 'surge', 'patch': 'factory/fx/fireworks', 'notes': [[0.0, 72, 0.4, 100]], 'tail': 0.6, 'bpm': 96, 'macros': {'1': 0.5},
                  'bend': [[0, 0], [0.4, 2]], 'wheel': [[0, 0], [0.4, 1]], 'slot': 'take:1'}
        played = surge.Provider(folder, config, LOCAL['music_skill'], record=True).render(source)
        self.assertLessEqual(abs(len(played) - 48000), 2)
        self.assertAlmostEqual(float(abs(played).max()), 0.5, places=3)
        self.assertTrue((surge.Provider(folder).render(source) == played).all())

    def _piece(self):
        notes = [{'id': f'lead_{i}', 'on': on, 'duration': 0.5, 'pitch': 60 + i} for i, on in enumerate((0, 1.5, 12.0, 14.5))]
        song = {'schema': 'music-production/v1', 'title': 'Contract piece', 'clock': {'bars': 8, 'meter': [6, 8], 'tempo': [[0, 120], [1, 60], [13.5, 97]], 'tail_seconds': 2},
                'sections': [{'id': 'a', 'start_bar': 0, 'bars': 4}, {'id': 'b', 'start_bar': 4, 'bars': 4}],
                'tracks': [{'id': 'lead', 'role': 'melody', 'engine': 'soundfont', 'program': 0, 'range': [48, 84], 'notes': notes,
                            'performance': {'notes': {n['id']: {'velocity': 90} for n in notes}}, 'mix': {'level_dbfs': -20}}],
                'mix': {'chain': 'room'}}
        path = helpers.scratch('contract_music_' + self.id().rsplit('.', 1)[-1]) / 'song.json'
        path.write_text(json.dumps(song), encoding='utf-8')
        return path

    def test_the_music_command_counts_a_project_as_music_production_does(self):
        from sfxlib import music
        path = self._piece()
        proc = mpbridge.run_studio(mpbridge.find_music_skill(LOCAL['music_skill']), ['validate', path], env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-800:])
        theirs = json.loads(proc.stdout)
        info = music.describe(music.load(path))
        self.assertEqual(info['render_seconds'], theirs['seconds'])
        self.assertEqual((info['bars'], len(info['tracks']), info['tracks'][0]['notes']), (theirs['bars'], theirs['tracks'], theirs['notes']))

    def test_quoted_notes_play_on_an_instrument_layer(self):
        import numpy as np
        from sfxlib import music, project, render, wav
        quoted = music.quote(music.load(self._piece()), 'lead', 5, 5)['notes']
        self.assertEqual(len(quoted), 2)
        proj = project.validate({'schema': 'sound-effects/1', 'title': 'quoted theme', 'sounds': [{'id': 'theme', 'category': 'magic', 'variants': 1, 'layers': [
            {'source': {'type': 'instrument', 'program': 0, 'notes': quoted}, 'start': 0.0, 'length': max(n[0] + n[2] for n in quoted) + 0.2}]}]})
        folder = helpers.scratch('contract_music_render') / 'render'
        render.render(proj, folder, config=json.loads(Path(LOCAL['music_config']).read_text(encoding='utf-8')))
        self.assertGreater(float(np.abs(wav.read(folder / 'master' / 'theme_01.wav')[0]).max()), 0.01)

    def test_studio_doctor_passes_with_the_config(self):
        skill = mpbridge.find_music_skill(LOCAL['music_skill'])
        proc = mpbridge.run_studio(skill, ['doctor', '--config', LOCAL['music_config']], env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-800:])


if __name__ == '__main__':
    unittest.main()
