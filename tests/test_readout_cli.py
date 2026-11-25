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


def printed(*args):
    """What sfx.py writes, line ends as they are (CRLF on Windows)."""
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, env=helpers.child_env(),
                          timeout=300).stdout.decode('utf-8', 'replace')


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

    def test_a_master_the_reader_does_not_take_is_refused_in_a_sentence(self):
        import shutil
        copy = self.base / 'render_changed'
        if not copy.exists():
            shutil.copytree(self.render, copy)
        channels, frames = 6, 4800
        data = struct.pack('<' + 'h' * channels * frames, *([1000] * channels * frames))
        fmt = struct.pack('<HHIIHH', 1, channels, 48000, 48000 * channels * 2, channels * 2, 16)
        body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
        masters = sorted((copy / 'master').glob('beep_*.wav'))         # every variant: the readout reads the first, and
        self.assertTrue(masters)                                       # a directory lists its files in no fixed order
        for master in masters:
            master.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
        proc = run('readout', copy, '--sound', 'beep', '--json')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('readout:', proc.stdout)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_something_else_is_refused_in_a_sentence(self):
        proc = run('readout', self.base / 'project.json')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('neither a render folder nor a WAV file', proc.stdout)
        self.assertEqual(run('readout', self.render, '--sound', 'nothing').returncode, 1)

    def test_a_window_is_zoomed_with_its_options(self):
        proc = run('readout', self.render, '--sound', 'beep', '--from', '0', '--to', '0.1')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('in slices of', proc.stdout)
        self.assertIn('events in the window', proc.stdout)
        rows = json.loads(run('readout', self.render, '--sound', 'beep', '--from', '0', '--to', '0.1', '--bands', 'third',
                              '--hz', '500-2000', '--json').stdout)
        self.assertTrue(rows[0]['zoom'])
        self.assertEqual(rows[0]['bands']['labels'], ['500', '630', '800', '1k', '1.25k', '1.6k', '2k'])

    def test_a_long_zoom_pages_and_a_page_past_the_last_is_refused(self):
        args = ('readout', self.render, '--sound', 'air', '--from', '0', '--to', '2', '--bands', 'third')
        first, second = run(*args), run(*args, '--page', '2')
        self.assertIn('--page 2', first.stdout)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotEqual(first.stdout, second.stdout)
        late = run(*args, '--page', '99')
        self.assertEqual(late.returncode, 1)
        self.assertIn('pages at these settings', late.stdout)

    def test_a_page_one_sound_lacks_is_said_and_the_others_still_print(self):
        args = ('readout', self.render, '--from', '0', '--to', '2', '--slice-ms', '10', '--page', '5')
        proc = run(*args)                                              # the beep fills fewer pages than the air
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('readout: beep fills', proc.stdout)
        self.assertIn('air: 0.000-2.000 s in slices of 10 ms', proc.stdout)
        self.assertIn('page 5 of', proc.stdout)
        self.assertEqual(run(*args, '--sound', 'beep').returncode, 1)

    def test_a_window_one_sound_lacks_is_said_and_the_others_still_print(self):
        proc = run('readout', self.render, '--from', '0.6', '--to', '0.9', '--slice-ms', '5', '--page', '2')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)  # the beep is shorter than 0.6 s
        self.assertIn('readout: beep:', proc.stdout)
        self.assertIn('page 2 of', proc.stdout)
        rows = json.loads(run('readout', self.render, '--from', '0.6', '--to', '0.9', '--json').stdout)
        self.assertEqual([(r['sound'], 'error' in r) for r in rows], [('beep', True), ('air', False)])
        alone = run('readout', self.render, '--sound', 'beep', '--from', '0.6', '--to', '0.9', '--json')
        self.assertEqual((alone.returncode, 'error' in json.loads(alone.stdout)[0]), (1, True))   # no sound read
        once = run('readout', self.render, '--from', '0', '--to', '0.1', '--slice-ms', '0')
        self.assertEqual((once.returncode, once.stdout.count('at least 1 ms')), (1, 1))   # said once, not per sound

    def test_huge_values_end_with_the_sound_or_are_refused_in_a_sentence(self):
        proc = run('readout', self.render, '--sound', 'beep', '--from', '0', '--to', '1e308')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = run('readout', self.render, '--sound', 'beep', '--from', '0', '--to', '0.1', '--slice-ms', str(2 ** 64))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('slice', proc.stdout)
        self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_page_goes_with_the_text_and_a_range_is_read_as_numbers(self):
        window = ('readout', self.render, '--sound', 'beep', '--from', '0', '--to', '0.1')
        proc = run(*window, '--page', '2', '--json')                   # JSON holds the whole window
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('--page', proc.stdout)
        self.assertEqual(run(*window, '--hz=').returncode, 1)
        rows = json.loads(run(*window, '--hz', '1e-3-1000', '--json').stdout)
        self.assertEqual(rows[0]['bands']['labels'][-1], '1k')

    def test_the_help_says_how_a_long_window_is_sliced(self):
        self.assertIn('100 for a window over 4 s', ' '.join(run('readout', '--help').stdout.split()))

    def test_a_printed_page_keeps_within_the_budget(self):
        for extra in ((), ('--bands', 'third'), ('--hz', '1000-1000', '--slice-ms', '1'),
                      ('--slice-ms', '1', '--bands', 'third', '--hz', '800-20000')):
            for page in ('1', '2'):
                with self.subTest(extra=extra, page=page):
                    out = printed('readout', self.render, '--sound', 'air', '--from', '0', '--to', '2', *extra,
                                  '--page', page)
                    self.assertLessEqual(out.count('\n'), 25)
                    self.assertLessEqual(len(out), 2500)                # as printed: CRLF on Windows counts twice

    def test_bad_zoom_values_are_refused_in_a_sentence(self):
        window = ('--from', '0', '--to', '0.1')
        for extra, words in ((window + ('--slice-ms', '0'), 'at least 1 ms'), (('--from', '0', '--to', 'inf'), 'seconds'),
                             (('--from', 'nan', '--to', '0.1'), 'seconds'), (window + ('--channel', 'right'), 'one channel')):
            with self.subTest(extra=extra):
                proc = run('readout', self.render, '--sound', 'beep', *extra)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn(words, proc.stdout)
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        quiet = self.base / 'quiet.wav'
        wav.write(quiet, np.zeros(4800, dtype=np.float32), 48000)
        proc = run('readout', quiet, '--variant', '2')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('render folder', proc.stdout)

    def test_a_decay_view_reads_each_band(self):
        proc = run('readout', self.render, '--view', 'decay')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('beep: decay of each octave band', proc.stdout)
        self.assertIn('air: a loop has no decay', proc.stdout)
        rows = json.loads(run('readout', self.render, '--sound', 'beep', '--view', 'decay', '--json').stdout)
        self.assertTrue(rows[0]['decay'] and rows[0]['bands'])
        proc = run('readout', self.render, '--view', 'decay', '--against', self.render)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('--against goes with --view compare', proc.stdout)

    def test_zoom_options_are_checked_in_a_sentence(self):
        self.assertIn('both --from and --to', run('readout', self.render, '--to', '0.1').stdout)
        self.assertIn('go with --from and --to', run('readout', self.render, '--bands', 'third').stdout)
        self.assertIn('window is empty', run('readout', self.render, '--sound', 'beep', '--from', '0.3', '--to', '0.2').stdout)
        self.assertIn('LOW-HIGH', run('readout', self.render, '--from', '0', '--to', '0.1', '--hz', 'lots').stdout)

    def test_a_variant_is_read_by_its_number(self):
        base = helpers.scratch('readout-cli-variants')
        project = base / 'project.json'
        sound = dict(PROJECT['sounds'][0], vary={'pitch_cents': 400})      # variants a few semitones apart
        project.write_text(json.dumps(dict(PROJECT, sounds=[sound])), encoding='utf-8')
        proc = run('render', project, '--output', base / 'render_1', '--no-page')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = json.loads(run('readout', base / 'render_1', '--variant', '2', '--json').stdout)
        both = json.loads(run('readout', base / 'render_1', '--all-variants', '--json').stdout)
        self.assertEqual([r['sound'] for r in rows], ['beep, variant 2'])
        self.assertNotEqual(both[0]['lines'], both[1]['lines'])
        self.assertEqual(rows[0], both[1])
        self.assertEqual(run('readout', self.render, '--variant', '9').returncode, 1)
        self.assertEqual(run('readout', self.render, '--variant', '2', '--all-variants').returncode, 1)


class CompareCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = helpers.scratch('readout-compare')
        first = cls.base / 'project.json'
        first.write_text(json.dumps(PROJECT), encoding='utf-8')
        beep = dict(PROJECT['sounds'][0], layers=[dict(PROJECT['sounds'][0]['layers'][0], pitch=900)])   # 39 cents up
        second = cls.base / 'project2.json'
        second.write_text(json.dumps(dict(PROJECT, sounds=[beep])), encoding='utf-8')                  # and no air
        for project, folder in ((first, 'render_1'), (second, 'render_2')):
            proc = run('render', project, '--output', cls.base / folder, '--no-page')
            assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_two_renders_are_compared_sound_by_sound(self):
        args = ('readout', self.base / 'render_2', '--view', 'compare', '--against', self.base / 'render_1')
        proc = run(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('beep: render_2 against render_1', proc.stdout)
        self.assertIn('tonal lines: 1 changed, 0 new, 0 gone, 0 the same', proc.stdout)
        self.assertIn('+39 cents', proc.stdout)
        self.assertIn('only in render_1: air', proc.stdout)
        rows = json.loads(run(*args, '--json').stdout)
        self.assertEqual([(r['sound'], 'error' in r) for r in rows], [('beep', False), ('air', True)])
        self.assertEqual([round(line['cents']) for line in rows[0]['lines']['changed']], [39])

    def test_a_sound_only_the_other_render_has_is_named(self):
        args = ('readout', self.base / 'render_2', '--view', 'compare', '--against', self.base / 'render_1', '--sound', 'air')
        proc = run(*args)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('only in render_1: air', proc.stdout)
        proc = run(*args, '--json')
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout), [{'sound': 'air', 'compare': True, 'error': 'only in render_1'}])

    def test_two_wav_files_are_compared_directly(self):
        x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(24000) / 48000)).astype(np.float32)
        loud, quiet = self.base / 'loud.wav', self.base / 'quiet.wav'
        wav.write(loud, x, 48000)
        wav.write(quiet, x / 2, 48000)
        rows = json.loads(run('readout', loud, '--view', 'compare', '--against', quiet, '--json').stdout)
        cells = {v for row in rows[0]['bands']['slices'] for v in row['db'] if isinstance(v, int)}
        self.assertEqual(cells, {6})

    def test_compare_options_are_checked_in_a_sentence(self):
        one, two = self.base / 'render_1', self.base / 'render_2'
        for args, words in (((two, '--view', 'compare'), '--against'), ((two, '--against', one), '--view compare'),
                            ((two, '--view', 'compare', '--against', one, '--from', '0', '--to', '0.1'), 'zoom'),
                            ((two, '--view', 'compare', '--against', one, '--page', '2', '--json'), '--page'),
                            ((two, '--view', 'compare', '--against', self.base / 'nothing.wav'), 'neither')):
            with self.subTest(args=args):
                proc = run('readout', *args)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn(words, proc.stdout)
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)
        loud = self.base / 'alone.wav'
        wav.write(loud, np.zeros(4800, dtype=np.float32), 48000)
        proc = run('readout', two, '--view', 'compare', '--against', loud)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('two render folders or two WAV files', proc.stdout)


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
