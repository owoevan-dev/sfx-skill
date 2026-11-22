"""The contract with the installed music-production (MUSIC_PRODUCTION_CONTRACT.md), run against the copy and the
configuration named in tools/local.json. Skipped without that file or under check_all.py --quick."""
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

    def test_studio_doctor_passes_with_the_config(self):
        skill = mpbridge.find_music_skill(LOCAL['music_skill'])
        proc = mpbridge.run_studio(skill, ['doctor', '--config', LOCAL['music_config']], env=helpers.child_env())
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-800:])


if __name__ == '__main__':
    unittest.main()
