"""The readout and MIDI commands as a user runs them, and the description in FSD50K's content screen."""
import json
import struct
import subprocess
import sys
import unittest

import numpy as np

import helpers
from sfxlib import fsd50k, wav

SFX = helpers.SCRIPTS / 'sfx.py'

PROJECT = {
    'schema': 'sound-effects/1', 'title': 'Readout Test', 'variants': 2,
    'sounds': [
        {'id': 'beep', 'category': 'ui', 'peak_at': 0.01, 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'start': 0, 'length': 0.4, 'pitch': 880,
             'env': {'attack': 0.005, 'decay': 0.395}}]},
        {'id': 'air', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'layers': [
            {'source': {'type': 'noise', 'color': 'pink'}, 'start': 0, 'length': 2, 'gain_db': -6}]}]}


def run(*args):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=300)


def vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def tiny_midi():
    body = b''.join(vlq(d) + e for d, e in [(0, b'\xff\x51\x03\x07\xa1\x20'), (0, b'\xc0\x0b'), (0, b'\x90\x48\x64'),
                                             (480, b'\x80\x48\x00'), (0, b'\x90\x4c\x60'), (480, b'\x80\x4c\x00')])
    body += b'\x00\xff\x2f\x00'
    return b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) + b'MTrk' + struct.pack('>I', len(body)) + body


class ReadoutCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch('readout-cli')
        project = cls.base / 'project.json'
        project.write_text(json.dumps(PROJECT), encoding='utf-8')
        cls.render = cls.base / 'render_1'
        proc = run('render', project, '--output', cls.render, '--no-page')
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_render_is_read_sound_by_sound(self):
        proc = run('readout', self.render)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('beep:', proc.stdout)
        self.assertIn('A5', proc.stdout)                                  # 880 Hz
        self.assertIn('air:', proc.stdout)
        self.assertIn('a loop', proc.stdout)
        self.assertIn('nearest the declared peak_at', proc.stdout)

    def test_json_and_one_sound_with_all_variants(self):
        proc = run('readout', self.render, '--sound', 'beep', '--all-variants', '--json')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = json.loads(proc.stdout)
        self.assertEqual([r['sound'] for r in rows], ['beep, variant 1', 'beep, variant 2'])
        self.assertTrue(all(r['lines'] and r['main_by'] == 'peak_at' for r in rows))

    def test_a_wav_file_and_a_wav_looped_by_its_smpl_chunk(self):
        x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype(np.float32)
        plain = self.base / 'tone.wav'
        wav.write(plain, x, 48000)
        proc = run('readout', plain)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('A4', proc.stdout)
        self.assertIn('onset at', proc.stdout)
        master = next((self.render / 'master').glob('air_*.wav'))       # the render's loop carries its smpl chunk
        proc = run('readout', master)
        self.assertIn('a loop', proc.stdout)

    def test_a_wav_layout_the_reader_does_not_take_is_refused_in_a_sentence(self):
        channels, frames = 6, 4800
        data = struct.pack('<' + 'h' * channels * frames, *([1000] * channels * frames))
        fmt = struct.pack('<HHIIHH', 1, channels, 48000, 48000 * channels * 2, channels * 2, 16)
        body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
        path = self.base / 'room.wav'
        path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
        proc = run('readout', path, '--json')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('readout:', proc.stdout)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_something_else_is_refused_in_a_sentence(self):
        proc = run('readout', self.base / 'project.json')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('neither a render folder nor a WAV file', proc.stdout)
        self.assertEqual(run('readout', self.render, '--sound', 'nothing').returncode, 1)


class MidiCommand(unittest.TestCase):
    def test_a_midi_file_is_listed_and_quoted(self):
        base = helpers.scratch('midi-cli')
        path = base / 'theme.mid'
        path.write_bytes(tiny_midi())
        proc = run('music', path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('a Standard MIDI File', proc.stdout)
        self.assertIn('program 11', proc.stdout)
        proc = run('music', path, '--track', '1', '--bars', '1')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"notes": [[0.0, 72, 0.5, 100], [0.5, 76, 0.5, 96]]', proc.stdout)
        proc = run('music', path, '--track', '1')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('go together', proc.stdout)
        bad = base / 'bad.mid'
        bad.write_bytes(b'nope')
        proc = run('music', bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('not a Standard MIDI File', proc.stdout)


class ContentScreen(unittest.TestCase):
    def test_the_description_is_read_too_and_stays_optional(self):
        self.assertEqual(fsd50k.content_reasons('rain', ['water'], ['Rain']), [])
        self.assertEqual(fsd50k.content_reasons('rain', ['water'], ['Rain'], 'recorded on a porch'), [])
        self.assertIn('adult content', fsd50k.content_reasons('ambience', ['room'], ['Breathing'], 'an erotic scene'))


if __name__ == '__main__':
    unittest.main()
