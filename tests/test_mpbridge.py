"""Finding music-production and reading its configuration, on folder layouts made for the test."""
import json
import unittest
from pathlib import Path

import helpers
from sfxlib import mpbridge


def _fake_skill(root):
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts' / 'studio.py').write_text('# stand-in\n', encoding='utf-8')
    return root


class FindMusicSkill(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(self.id().rsplit('.', 1)[-1])

    def test_explicit_path_wins(self):
        given = _fake_skill(self.base / 'given')
        beside = _fake_skill(self.base / 'skills' / 'music-production')
        here = self.base / 'skills' / 'sound-effects' / 'scripts' / 'sfxlib' / 'mpbridge.py'
        self.assertEqual(mpbridge.find_music_skill(str(given), here=here, home=self.base / 'home'), given)
        self.assertEqual(mpbridge.find_music_skill(None, here=here, home=self.base / 'home'), beside)

    def test_explicit_path_without_studio_is_an_error(self):
        with self.assertRaises(mpbridge.MusicSkillError):
            mpbridge.find_music_skill(str(self.base / 'nothing'), here=self.base / 'x.py', home=self.base / 'home')

    def test_user_skills_folder_is_the_last_place(self):
        home = self.base / 'home'
        found = _fake_skill(home / '.claude' / 'skills' / 'music-production')
        here = self.base / 'elsewhere' / 'skills' / 'sound-effects' / 'scripts' / 'sfxlib' / 'mpbridge.py'
        self.assertEqual(mpbridge.find_music_skill(None, here=here, home=home), found)

    def test_nothing_found_gives_none(self):
        here = self.base / 'a' / 'skills' / 'sound-effects' / 'scripts' / 'sfxlib' / 'mpbridge.py'
        self.assertIsNone(mpbridge.find_music_skill(None, here=here, home=self.base / 'home'))


class Config(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(self.id().rsplit('.', 1)[-1])

    def test_ffmpeg_path_is_read(self):
        exe = self.base / 'ffmpeg.exe'
        exe.write_bytes(b'')
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps({'ffmpeg': {'path': str(exe)}}), encoding='utf-8')
        self.assertEqual(mpbridge.ffmpeg_path(mpbridge.load_config(cfg)), exe)

    def test_missing_ffmpeg_field_names_the_field(self):
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps({'fluidsynth': {}}), encoding='utf-8')
        with self.assertRaises(mpbridge.MusicSkillError) as caught:
            mpbridge.ffmpeg_path(mpbridge.load_config(cfg))
        self.assertIn('ffmpeg.path', str(caught.exception))

    def test_ffmpeg_that_is_not_there_is_an_error(self):
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps({'ffmpeg': {'path': str(self.base / 'missing.exe')}}), encoding='utf-8')
        with self.assertRaises(mpbridge.MusicSkillError):
            mpbridge.ffmpeg_path(mpbridge.load_config(cfg))

    def test_unreadable_config_is_an_error(self):
        cfg = self.base / 'config.json'
        cfg.write_text('{not json', encoding='utf-8')
        with self.assertRaises(mpbridge.MusicSkillError):
            mpbridge.load_config(cfg)
        with self.assertRaises(mpbridge.MusicSkillError):
            mpbridge.load_config(self.base / 'absent.json')

    def test_ffmpeg_listings_are_parsed_with_every_flag_column(self):
        encoders = ('Encoders:\n V..... = Video\n A..... = Audio\n ------\n'
                    ' A....D pcm_s16le            PCM signed 16-bit little-endian\n'
                    ' A..X.D vorbis               Vorbis\n')
        filters = ('Filters:\n  T.. = Timeline support\n  ... = no flags\n'
                   ' TSC acompressor       A->A       Audio compressor.\n'
                   ' .. ebur128          A->N       EBU R128 scanner.\n'
                   ' T.C aresample        A->A       Resample audio data.\n')
        self.assertEqual(mpbridge.parse_listing(encoders), {'pcm_s16le', 'vorbis'})
        self.assertEqual(mpbridge.parse_listing(filters), {'acompressor', 'ebur128', 'aresample'})

    def test_child_env_sets_tmp_and_temp(self):
        env = mpbridge.child_env(tmp=str(self.base))
        self.assertEqual(env['TMP'], str(self.base))
        self.assertEqual(env['TEMP'], str(self.base))
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')


if __name__ == '__main__':
    unittest.main()
