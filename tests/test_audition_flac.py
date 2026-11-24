"""The audition page with FFmpeg: each variant as FLAC holding exactly the samples of the WAV it replaces, at its length."""
import base64
import json
import re
import subprocess
import sys
import unittest

import numpy as np

import helpers
import test_project  # registers test.beep
from sfxlib import dsp, project, record, render, wav

FFMPEG = helpers.ffmpeg_or_none()


def page_data(folder):
    html = (folder / 'audition.html').read_text(encoding='utf-8')
    return json.loads(re.search('<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)), len(html.encode('utf-8'))


@unittest.skipIf(FFMPEG is None, 'no FFmpeg')
class FlacPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = test_project.minimal()
        data['variants'] = 2
        cls.out = helpers.scratch('audition_flac') / 'render'
        render.render(project.validate(data), cls.out, ffmpeg=FFMPEG)
        cls.data, cls.size = page_data(cls.out)
        cls.resolved = record.load(cls.out)

    def test_each_variant_is_flac_with_the_same_samples_as_the_wav_it_replaces(self):
        for sound in self.data['sounds']:
            for v in sound['variants']:
                blob = base64.b64decode(v['audio'])
                self.assertEqual(blob[:4], b'fLaC')
                x = record.master(self.out, v['file']).astype(np.float64)
                expected = wav.encode(x, bits=16, dither=True, seed=dsp.seed_for(self.resolved['seed'], v['file'], 'page'))
                pcm = expected[expected.index(b'data') + 8:]
                decoded = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-f', 'flac', '-i', 'pipe:0', '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1'],
                                         input=blob, capture_output=True, check=True).stdout
                self.assertEqual(decoded, pcm)
                self.assertEqual(v['frames'], len(x))
                self.assertEqual(int.from_bytes(blob[18:26], 'big') & (2 ** 36 - 1), len(x))     # its length, in its header

    def test_the_page_is_smaller_than_with_wav(self):
        folder = helpers.scratch('audition_flac_size') / 'render'
        data = test_project.minimal()
        data['variants'] = 2
        render.render(project.validate(data), folder)
        audio = lambda page: sum(len(base64.b64decode(v['audio'])) for s in page['sounds'] for v in s['variants'])
        self.assertLess(audio(self.data), audio(page_data(folder)[0]))       # 43 % on a typical set of 28 files; two blips compress less
        self.assertLess(self.size, page_data(folder)[1])

    def test_the_command_writes_the_page_with_the_ffmpeg_it_is_given(self):
        folder = helpers.scratch('audition_flac_cli') / 'render'
        render.render(project.validate(test_project.minimal()), folder)
        self.assertEqual(base64.b64decode(page_data(folder)[0]['sounds'][0]['variants'][0]['audio'])[:4], b'RIFF')
        done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'audition', str(folder), '--ffmpeg', str(FFMPEG)], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(base64.b64decode(page_data(folder)[0]['sounds'][0]['variants'][0]['audio'])[:4], b'fLaC')


if __name__ == '__main__':
    unittest.main()
