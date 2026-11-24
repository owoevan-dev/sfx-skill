"""Recorded layers: the MIDI file, which banks may be used, detuning and placement, the layer in a sound, the schema,
variants, and a real render on music-production's FluidSynth and banks (skipped without its configuration)."""
import json
import os
import struct
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import helpers
from sfxlib import instruments, midi, project, render, synth, variants, wav

RATE = 48000
QUICK = os.environ.get('SFX_QUICK') == '1'


def read_smf(data):
    """(division, [(tick, bytes of the event)]) of a format-0 file written by midi.smf."""
    assert data[:4] == b'MThd'
    _, fmt, tracks, division = struct.unpack('>IHHH', data[4:14])
    assert (fmt, tracks) == (0, 1) and data[14:18] == b'MTrk'
    body = data[22:22 + struct.unpack('>I', data[18:22])[0]]
    i, tick, events = 0, 0, []
    while i < len(body):
        delta = 0
        while True:
            b = body[i]
            i += 1
            delta = (delta << 7) | (b & 0x7F)
            if not b & 0x80:
                break
        tick += delta
        if body[i] == 0xFF:
            length = body[i + 2]
            events.append((tick, body[i:i + 3 + length]))
            i += 3 + length
        else:
            size = 2 if (body[i] & 0xF0) == 0xC0 else 3
            events.append((tick, body[i:i + size]))
            i += size
    return division, events


def config_or_none():
    if QUICK:
        return None
    path = helpers.local().get('music_config')
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding='utf-8'))


def marimba_bank(config):
    """A bank with a marimba at program 12: VCSL where the configuration names it, else its default General MIDI bank (a new
    setup of music-production has only that one)."""
    return 'vcsl' if 'vcsl' in (config.get('soundfonts') or {}) else 'default'


class Midi(unittest.TestCase):
    def test_the_file_holds_the_preset_the_notes_and_the_tail(self):
        division, events = read_smf(midi.smf([(0.0, 72, 0.5, 100), (0.25, 79, 0.5, 90)], program=12, bank=130,
                                             tail_s=2.0))
        self.assertEqual(division, 960)
        kinds = [(t, bytes(e)) for t, e in events]
        self.assertIn((0, bytes([0xFF, 0x51, 0x03, 0x0F, 0x42, 0x40])), kinds)       # 1 000 000 us a quarter
        self.assertIn((0, bytes([0xB0, 0, 1])), kinds)                                # bank 130 = 1 x 128 + 2
        self.assertIn((0, bytes([0xB0, 32, 2])), kinds)
        self.assertIn((0, bytes([0xC0, 12])), kinds)
        self.assertIn((0, bytes([0x90, 72, 100])), kinds)
        self.assertIn((240, bytes([0x90, 79, 90])), kinds)
        self.assertIn((480, bytes([0x80, 72, 0])), kinds)
        self.assertIn((720, bytes([0x80, 79, 0])), kinds)
        self.assertEqual(kinds[-1], (720 + 1920, bytes([0xFF, 0x2F, 0x00])))

    def test_drums_play_on_channel_ten(self):
        _, events = read_smf(midi.smf([(0.0, 52, 0.2, 100)], program=48, bank=128, drums=True))
        self.assertIn(bytes([0x99, 52, 100]), [bytes(e) for _, e in events])


class Banks(unittest.TestCase):
    def test_only_banks_whose_sound_needs_no_credit(self):
        for licence in ('CC0-1.0 source samples: VCSL', 'University of Iowa Electronic Music Studios, Musical',
                        'Unlicense (public-domain dedication)', 'MIT; retain named sample-author notices'):
            self.assertTrue(instruments.allowed(licence), licence)
        for licence in ('CC-BY-4.0', 'GPL-3.0', '', None, 'All rights reserved; CC0 later'):
            self.assertFalse(instruments.allowed(licence), licence)

    def test_unknown_refused_and_missing_banks_are_named(self):
        folder = helpers.scratch('instrument_banks')
        sf2 = folder / 'bank.sf2'
        sf2.write_bytes(b'RIFF')
        config = {'soundfonts': {'free': {'path': str(sf2), 'license': 'CC0-1.0'},
                                 'credit': {'path': str(sf2), 'license': 'CC-BY-4.0'},
                                 'gone': {'path': str(folder / 'missing.sf2'), 'license': 'CC0-1.0'}}}
        provider = instruments.Provider(config, folder / 'out')
        self.assertEqual(provider.bank('free')[0], sf2)
        for name, words in (('nope', 'it has: default, credit, free, gone'), ('credit', 'CC-BY-4.0'),
                            ('gone', 'not a file')):
            with self.assertRaises(instruments.InstrumentError) as caught:
                provider.bank(name)
            self.assertIn(words, str(caught.exception))


def riff_chunk(tag, data):
    import struct
    return tag + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')


def preset_record(name, program, bank, bag=0):
    import struct
    return name.encode('ascii').ljust(20, b'\0') + struct.pack('<HHHIII', program, bank, bag, 0, 0, 0)


def fake_bank(presets):
    """A minimal SoundFont 2 file: an INFO list, sample data of odd length and a pdta list whose phdr holds the
    presets and the terminal record."""
    phdr = b''.join(preset_record(n, p, b, i) for i, (b, p, n) in enumerate(presets)) + preset_record('EOP', 0, 0, 9)
    body = (b'sfbk' + riff_chunk(b'LIST', b'INFO' + riff_chunk(b'ifil', b'\x02\0\x04\0'))
            + riff_chunk(b'LIST', b'sdta' + riff_chunk(b'smpl', b'\x01\x02\x03'))
            + riff_chunk(b'LIST', b'pdta' + riff_chunk(b'phdr', phdr) + riff_chunk(b'pbag', b'\0' * 4)))
    return riff_chunk(b'RIFF', body)


class Presets(unittest.TestCase):
    def test_the_presets_of_a_bank_are_read_from_its_headers(self):
        folder = helpers.scratch('instrument_presets')
        path = folder / 'bank.sf2'
        path.write_bytes(fake_bank([(0, 12, 'Marimba'), (128, 0, 'Standard Kit'), (0, 9, 'Glockenspiel')]))
        self.assertEqual(instruments.presets(path), [(0, 9, 'Glockenspiel'), (0, 12, 'Marimba'),
                                                     (128, 0, 'Standard Kit')])

    def test_the_usable_banks_and_their_presets_are_listed(self):
        folder = helpers.scratch('instrument_listing')
        path = folder / 'bank.sf2'
        path.write_bytes(fake_bank([(0, 11, 'Vibraphone')]))
        config = {'soundfont': {'path': str(path), 'license': 'MIT'},
                  'soundfonts': {'free': {'path': str(path), 'license': 'CC0-1.0'},
                                 'credit': {'path': str(path), 'license': 'CC-BY-4.0'}}}
        listing = {b['name']: b for b in instruments.list_banks(config)}
        self.assertEqual(sorted(listing), ['credit', 'default', 'free'])
        self.assertTrue(listing['free']['usable'])
        self.assertEqual(listing['free']['presets'], [(0, 11, 'Vibraphone')])
        self.assertFalse(listing['credit']['usable'])
        self.assertEqual(listing['credit']['presets'], [])          # a bank under other terms is not opened


class Shaping(unittest.TestCase):
    def test_detune_moves_the_pitch_and_shortens_the_sound(self):
        t = np.arange(RATE) / RATE
        x = np.sin(2 * np.pi * 1000.0 * t)[:, None] * np.hanning(RATE)[:, None]
        y = instruments.detune(x, 100.0)
        self.assertAlmostEqual(len(y), RATE / 2 ** (100 / 1200), delta=2)
        spectrum = np.abs(np.fft.rfft(y[:, 0], 4 * RATE))
        peak = np.argmax(spectrum) * RATE / (4 * RATE)
        self.assertAlmostEqual(peak, 1000.0 * 2 ** (100 / 1200), delta=1.0)
        self.assertIs(instruments.detune(x, 0.0), x)

    def test_placement_avoids_cancelling_a_spaced_pair(self):
        rng = np.random.default_rng(1)
        a = rng.standard_normal(4800)
        opposed = np.stack([a, -0.8 * a + 0.1 * rng.standard_normal(4800)], axis=1)
        self.assertTrue(np.array_equal(instruments.placed(opposed, 1), opposed[:, 0]))
        stereo = instruments.placed(opposed, 2)
        self.assertTrue(np.allclose(stereo[:, 0], stereo[:, 1]))
        together = np.stack([a, 0.9 * a], axis=1)
        self.assertTrue(np.allclose(instruments.placed(together, 2), together * np.sqrt(0.5)))


class FakeBanks:
    """Stands in for instruments.Provider: a 1 kHz tone, stereo, at the layer's notes' first time."""

    def render(self, source):
        t = np.arange(RATE // 2) / RATE
        x = 0.5 * np.sin(2 * np.pi * 1000.0 * t) * np.exp(-6 * t)
        return np.stack([x, x], axis=1)


class Layer(unittest.TestCase):
    LAYER = {'source': {'type': 'instrument', 'soundfont': 'vcsl', 'program': 12, 'notes': [[0, 84, 0.3, 100]]},
             'start': 0.0, 'length': 0.3}

    def test_a_layer_needs_the_banks_and_fits_its_length(self):
        with self.assertRaises(ValueError):
            synth.render_sound({'layers': [self.LAYER]}, seed=1)
        x = synth.layer_signal(self.LAYER, 1, 0, instruments=FakeBanks())
        self.assertEqual(x.shape, (int(0.3 * RATE),))
        stereo = synth.layer_signal(self.LAYER, 1, 0, channels=2, instruments=FakeBanks())
        self.assertEqual(stereo.shape, (int(0.3 * RATE), 2))

    def test_the_schema(self):
        good = {'id': 'chime', 'category': 'magic', 'layers': [self.LAYER]}
        project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [good]})
        bad_notes = [[0, 128, 0.3, 100]], [[0, 84, 0, 100]], [[0, 84, 0.3, 0]], [[0, 84.5, 0.3, 100]], [], [[0, 84]]
        for notes in bad_notes:
            layer = json.loads(json.dumps(self.LAYER))
            layer['source']['notes'] = notes
            with self.assertRaises(project.ProjectError, msg=str(notes)):
                project.validate({'schema': 'sound-effects/1', 'title': 't',
                                  'sounds': [{'id': 'x', 'category': 'magic', 'layers': [layer]}]})
        loop_bed = dict(self.LAYER, length=2.0)
        with self.assertRaises(project.ProjectError):
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
                {'id': 'x', 'category': 'ambience', 'use': 'loop', 'loop': {'seconds': 2}, 'layers': [loop_bed]}]})

    def test_variants_detune_and_stretch_the_notes(self):
        sound = project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
            {'id': 'x', 'category': 'magic', 'layers': [self.LAYER], 'vary': {'pitch_cents': 50, 'time_pct': 10}}]})
        sound = sound['sounds'][0]
        spec, drawn = variants.layer_spec(sound, 7, 2)
        src = spec['layers'][0]['source']
        self.assertAlmostEqual(src['cents'], drawn['pitch_cents'])
        self.assertAlmostEqual(src['notes'][0][2], 0.3 * (1 + drawn['time_pct'] / 100))


class TickedBanks:
    """Stands in for instruments.Provider: a bright struck note; on program 9, with a tick left in the sample 1.25 s in."""

    def __init__(self, config=None, folder=None):
        pass

    def render(self, source):
        t = np.arange(int(3.2 * RATE)) / RATE
        x = sum(np.sin(2 * np.pi * f * t) for f in (3400, 5200, 7900)) / 3 * np.exp(-5 * t)
        if source.get('program') == 9:
            k, n = int(1.25 * RATE), int(0.03 * RATE)
            x[k:k + n] += 0.4 * np.random.default_rng(3).standard_normal(n) * np.exp(-np.arange(n) / (0.01 * RATE))
        x = 0.5 * x / np.max(np.abs(x))
        return np.stack([x, x], axis=1)


class StrayAttackFlag(unittest.TestCase):
    """The flag for an attack no note explains, through a render."""

    @staticmethod
    def note(program=9, **layer):
        return dict({'source': {'type': 'instrument', 'soundfont': 'vcsl', 'program': program, 'notes': [[0, 84, 2.5, 90]]},
                     'start': 0.0, 'length': 2.0}, **layer)

    def stray(self, layers, **sound):
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [
            dict({'id': 'bell', 'category': 'magic', 'variants': 1, 'layers': layers}, **sound)]})
        self.runs = getattr(self, 'runs', 0) + 1
        out = helpers.scratch(f'stray_{self._testMethodName}_{self.runs}') / 'render'
        with mock.patch.object(render.instruments, 'Provider', TickedBanks):
            render.render(proj, out, config={'stand-in': True}, page=False)
        self.resolved = json.loads((out / 'resolved.json').read_text(encoding='utf-8'))
        return [f for f in json.loads((out / 'check.json').read_text(encoding='utf-8'))['flags'] if f['kind'] == 'stray_attack']

    def test_a_tick_the_layer_plays_is_named_at_its_time_in_the_sound(self):
        found = self.stray([self.note(start=0.1)])
        self.assertEqual(len(found), 1)
        self.assertIn('1.350', found[0]['message'])
        self.assertIn('layer 1', found[0]['message'])

    def test_a_clean_note_raises_nothing_and_records_nothing(self):
        self.assertEqual(self.stray([self.note(program=12)]), [])
        self.assertNotIn('stray_attacks_s', self.resolved['sounds'][0]['variants'][0]['layers'][0])

    def test_cents_move_the_tick_and_not_the_grace_after_a_note(self):
        up = self.stray([self.note(length=0.4, source=dict(self.note()['source'], cents=3600))])     # eight times faster: at 0.156 s
        self.assertEqual(len(up), 1)
        self.assertIn('0.156', up[0]['message'])
        down = self.stray([self.note(length=2.7, source=dict(self.note()['source'], cents=-1200))])  # half speed: at 2.5 s
        self.assertEqual(len(down), 1)
        self.assertIn('2.500', down[0]['message'])

    def test_a_layer_that_ends_before_the_tick_is_clean(self):
        self.assertEqual(self.stray([self.note(length=1.2)]), [])

    def test_a_layer_faded_before_the_tick_is_clean(self):
        self.assertEqual(self.stray([self.note(env=[[0, 1], [1.0, 1], [1.2, 0.05], [2.0, 0]])]), [])

    def test_an_envelope_that_opens_late_on_a_held_note_is_not_an_attack(self):
        self.assertEqual(self.stray([self.note(program=12, env=[[0, 0], [0.6, 0], [0.601, 1], [1.9, 1], [2.0, 0]])]), [])

    def test_a_later_variant_is_read_too(self):
        found = self.stray([self.note()], variants=3, vary={'time_pct': 5})
        self.assertEqual(sorted(f['file'] for f in found), ['master/bell_01.wav', 'master/bell_02.wav', 'master/bell_03.wav'])

    def test_in_a_loop_it_is_named_where_it_lands_in_the_period(self):
        bed = {'source': {'type': 'noise', 'color': 'pink'}, 'length': 2.0, 'gain_db': -30}
        found = self.stray([bed, self.note(start=1.7, length=1.4)], category='ambience', use='loop', loop={'seconds': 2})
        self.assertEqual(len(found), 1)
        self.assertIn('0.950', found[0]['message'])
        self.assertIn('layer 2', found[0]['message'])

    def test_a_surge_layer_is_not_read(self):
        class Ticked:                    # stands in for the takes a surge layer plays
            def render(self, source):
                return TickedBanks().render({'program': 9})
        for kind, read in (('surge', False), ('instrument', True)):
            source = {'type': kind, 'patch': 'factory/fx/test', 'notes': [[0, 84, 2.5, 90]]} if kind == 'surge' else self.note()['source']
            info = {}
            synth.layer_signal({'source': source, 'length': 2.0}, 1, 0, 2, instruments=Ticked(), info=info)
            with self.subTest(kind=kind):
                self.assertEqual(bool(info.get('stray')), read)

    def test_it_is_not_a_crackle(self):
        self.assertEqual(len(self.stray([self.note()], crackle=True)), 1)


@unittest.skipUnless(config_or_none(), "no music-production configuration in tools/local.json (or --quick)")
class RealBanks(unittest.TestCase):
    def test_a_marimba_note_renders_at_its_pitch_and_repeats(self):
        sound = {'id': 'mallet', 'category': 'ui', 'variants': 1, 'layers': [
            {'source': {'type': 'instrument', 'soundfont': marimba_bank(config_or_none()), 'program': 12,
                        'notes': [[0.0, 72, 0.4, 100]]},
             'start': 0.0, 'length': 0.8}]}
        proj = project.validate({'schema': 'sound-effects/1', 'title': 'marimba test', 'sounds': [sound]})
        base = helpers.scratch('instrument_real')
        masters = []
        for name in ('one', 'two'):
            render.render(proj, base / name, config=config_or_none())
            masters.append((base / name / 'master' / 'mallet_01.wav').read_bytes())
            self.assertTrue(list((base / name / 'instruments').glob('*.mid')))
        self.assertEqual(masters[0], masters[1])
        x = wav.read(base / 'one' / 'master' / 'mallet_01.wav')[0].astype(np.float64).reshape(-1)
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        peak = np.argmax(spectrum) * RATE / len(x)
        self.assertAlmostEqual(peak, 440 * 2 ** ((72 - 69) / 12), delta=5.0)
        listed = {f['path'] for f in json.loads((base / 'one' / 'files.json').read_text(encoding='utf-8'))}
        self.assertTrue(any(p.startswith('instruments/') for p in listed))

    def test_a_refused_bank_stops_the_render(self):
        config = json.loads(json.dumps(config_or_none()))
        bank = marimba_bank(config)
        (config['soundfont'] if bank == 'default' else config['soundfonts'][bank])['license'] = 'CC-BY-4.0: someone'
        sound = {'id': 'mallet', 'category': 'ui', 'variants': 1, 'layers': [
            {'source': {'type': 'instrument', 'soundfont': bank, 'program': 12, 'notes': [[0.0, 72, 0.4, 100]]},
             'start': 0.0, 'length': 0.8}]}
        proj = project.validate({'schema': 'sound-effects/1', 'title': 'refused', 'sounds': [sound]})
        with self.assertRaises(render.RenderError) as caught:
            render.render(proj, helpers.scratch('instrument_refused') / 'render', config=config)
        self.assertIn('CC-BY-4.0', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
