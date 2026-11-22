"""`sfx.py doctor`: what synthesis needs passes everywhere; FFmpeg and music-production are checked when named."""
import os
import subprocess
import sys
import unittest

import helpers

SFX = helpers.SCRIPTS / 'sfx.py'
LOCAL = helpers.local()


def run(*args):
    return subprocess.run([sys.executable, str(SFX), *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=helpers.child_env(), timeout=300)


class Doctor(unittest.TestCase):
    def test_without_music_production_synthesis_still_passes(self):
        proc = run('doctor', '--music-skill', '')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('NumPy', proc.stdout)
        self.assertIn('synthesis: OK', proc.stdout)

    def test_a_named_folder_that_is_not_music_production_fails(self):
        base = helpers.scratch('doctor_bad_skill')
        proc = run('doctor', '--music-skill', str(base))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('not a music-production folder', proc.stdout + proc.stderr)

    @unittest.skipIf(os.environ.get('SFX_QUICK') == '1' or not LOCAL.get('music_config'), 'needs music_config')
    def test_full_setup_passes(self):
        proc = run('doctor', '--config', LOCAL['music_config'], '--music-skill', LOCAL['music_skill'])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for line in ('FFmpeg: OK', 'music-production doctor: OK'):
            self.assertIn(line, proc.stdout)


class AnFFmpegWithoutAnEncoder(unittest.TestCase):
    def test_it_is_no_failure_and_says_what_it_costs(self):
        import sfx
        line = sfx._ffmpeg_line('/opt/bin/ffmpeg', ['libvorbis'])
        self.assertIn('libvorbis', line)
        self.assertIn('no Ogg', line)
        self.assertIn('ffmpeg-full', line)

    def test_a_complete_one_is_ok(self):
        import sfx
        self.assertEqual(sfx._ffmpeg_line('/opt/bin/ffmpeg', []), 'FFmpeg: OK (/opt/bin/ffmpeg)')


if __name__ == '__main__':
    unittest.main()
