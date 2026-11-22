"""Finding FFmpeg without music-production (ffmpeg.py): --ffmpeg, the environment, music-production's configuration,
then the PATH; a named FFmpeg that is missing is an error, not a fall-through."""
import os
import unittest
from unittest import mock

import helpers
from sfxlib import ffmpeg


class Find(unittest.TestCase):
    def setUp(self):
        self.folder = helpers.scratch(f'ffmpeg_{self._testMethodName}')
        self.exe = self.folder / 'ffmpeg.exe'
        self.exe.write_bytes(b'stand-in')
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop(ffmpeg.ENV, None)

    def tearDown(self):
        self.env.stop()

    def test_the_order_is_flag_environment_configuration_path(self):
        other = self.folder / 'other.exe'
        other.write_bytes(b'stand-in')
        config = {'ffmpeg': {'path': str(other)}}
        with mock.patch('shutil.which', return_value=str(self.folder / 'on_path.exe')):
            self.assertEqual(ffmpeg.find(self.exe, config), self.exe)
            os.environ[ffmpeg.ENV] = str(other)
            self.assertEqual(ffmpeg.find(None, None), other)
            del os.environ[ffmpeg.ENV]
            self.assertEqual(ffmpeg.find(None, config), other)
            self.assertEqual(ffmpeg.find(None, None).name, 'on_path.exe')
        with mock.patch('shutil.which', return_value=None):
            self.assertIsNone(ffmpeg.find(None, None))

    def test_a_relative_path_stays_the_program_it_names(self):
        before = os.getcwd()
        os.chdir(self.folder)
        try:
            found = ffmpeg.find(os.path.join('.', 'ffmpeg.exe'))
        finally:
            os.chdir(before)
        self.assertTrue(found.is_absolute())
        self.assertEqual(found, self.exe.resolve())

    def test_a_named_ffmpeg_that_is_missing_is_an_error(self):
        with self.assertRaisesRegex(ffmpeg.FFmpegError, '--ffmpeg'):
            ffmpeg.find(self.folder / 'missing.exe')
        os.environ[ffmpeg.ENV] = str(self.folder / 'missing.exe')
        with self.assertRaisesRegex(ffmpeg.FFmpegError, ffmpeg.ENV):
            ffmpeg.find(None)


if __name__ == '__main__':
    unittest.main()
