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


def with_a_held_tone():
    """The minimal project and a second of tone, which FLAC holds in far fewer bytes than WAV (a click of milliseconds may not)."""
    data = test_project.minimal()
    data['variants'] = 2
    data['sounds'].append({'id': 'hum', 'category': 'ui', 'layers': [{'source': {'type': 'osc', 'wave': 'sine'}, 'pitch': 220, 'length': 1.0,
                                                                      'env': [[0, 0], [0.01, 1], [0.9, 1], [1.0, 0]]}]})
    return data


def page_data(folder):
    html = (folder / 'audition.html').read_text(encoding='utf-8')
    return json.loads(re.search('<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)), len(html.encode('utf-8'))


@unittest.skipIf(FFMPEG is None, 'no FFmpeg')
class FlacPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = helpers.scratch('audition_flac') / 'render'
        render.render(project.validate(with_a_held_tone()), cls.out, ffmpeg=FFMPEG)
        cls.data, cls.size = page_data(cls.out)
        cls.resolved = record.load(cls.out)

    def test_each_variant_is_flac_with_the_same_samples_as_the_wav_it_replaces_or_that_wav_when_smaller(self):
        kinds = []
        for sound in self.data['sounds']:
            for v in sound['variants']:
                blob = base64.b64decode(v['audio'])
                x = record.master(self.out, v['file']).astype(np.float64)
                expected = wav.encode(x, bits=16, dither=True, seed=dsp.seed_for(self.resolved['seed'], v['file'], 'page'))
                kinds.append((sound['id'], blob[:4]))
                if blob[:4] == b'RIFF':
                    self.assertEqual(blob, expected)
                    continue
                self.assertEqual(blob[:4], b'fLaC')
                self.assertLess(len(blob), len(expected))
                pcm = expected[expected.index(b'data') + 8:]
                decoded = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-f', 'flac', '-i', 'pipe:0', '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1'],
                                         input=blob, capture_output=True, check=True).stdout
                self.assertEqual(decoded, pcm)
                self.assertEqual(v['frames'], len(x))
                self.assertEqual(int.from_bytes(blob[18:26], 'big') & (2 ** 36 - 1), len(x))     # its length, in its header
        self.assertEqual({k for s, k in kinds if s == 'hum'}, {b'fLaC'})

    def test_the_page_is_smaller_than_with_wav(self):
        folder = helpers.scratch('audition_flac_size') / 'render'
        render.render(project.validate(with_a_held_tone()), folder)
        audio = lambda page: sum(len(base64.b64decode(v['audio'])) for s in page['sounds'] for v in s['variants'])
        self.assertLess(audio(self.data), audio(page_data(folder)[0]))       # 43 % on a typical set of 28 files; two blips compress less
        self.assertLess(self.size, page_data(folder)[1])

    def test_the_command_writes_the_page_with_the_ffmpeg_it_is_given(self):
        folder = helpers.scratch('audition_flac_cli') / 'render'
        render.render(project.validate(with_a_held_tone()), folder)
        hum = lambda: next(s for s in page_data(folder)[0]['sounds'] if s['id'] == 'hum')['variants'][0]['audio']
        self.assertEqual(base64.b64decode(hum())[:4], b'RIFF')
        done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'audition', str(folder), '--ffmpeg', str(FFMPEG)], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(base64.b64decode(hum())[:4], b'fLaC')


if __name__ == '__main__':
    unittest.main()
