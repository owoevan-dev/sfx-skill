"""Surge XT layers: what a layer may say, its performance's identity, the MIDI it becomes, takes recorded once and replayed. The host here is a stand-in
that plays a tone from a random phase, so that two recordings differ as real Surge takes do."""
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import helpers
from sfxlib import project, render, surge, synth, variants, wav

RATE = 48000
FAKE_HOST = r'''
import array, json, math, os, random, sys, time
job = json.load(open(sys.argv[1], encoding="utf-8"))
log = os.environ.get("SFX_FAKE_SURGE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(job, patch_bytes=open(job["patch"], "rb").read().decode("latin-1"))) + "\n")
mode = os.environ.get("SFX_FAKE_SURGE_MODE", "")
if mode == "slow":
    time.sleep(0.4)
if mode == "fail":
    sys.stderr.write("the fake host stopped\n")
    sys.exit(3)
if mode == "no_board":
    sys.stderr.write("ImportError: DLL load failed while importing pedalboard_native\n")
    sys.exit(1)
if mode == "nothing":
    sys.exit(0)
frames = int(round(job["duration"] * job["rate"])) - job["trim"]
if mode == "short":
    frames = 2
level = {"silent": 0.0, "loud": 2.5}.get(mode, 0.8)
phase = 0.0 if mode == "fixed" else random.random() * 6.283
out = array.array("f")
for i in range(frames):
    x = level * math.sin(phase + 6.283185 * 440.0 * i / job["rate"]) * math.exp(-3.0 * i / job["rate"])
    if mode == "nan" and i == 10:
        x = float("nan")
    out.append(x)
    out.append(0.5 * x)
raw = out.tobytes()
import struct
fmt = struct.pack("<HHIIHH", 3, 2, job["rate"], job["rate"] * 8, 8, 32)
blob = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
open(job["out"], "wb").write(blob)
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fake_patch(name, synced=False):
    sync = ' temposync="1"' if synced else ''
    xml = (f'<?xml version="1.0"?><patch revision="16"><meta name="{name}" category="FX" comment="" author=""/><parameters><fx0_type type="0" value="1"/>'
           f'<fx0_p0 type="2" value="-2.000000"{sync}/><a_lfo0_rate type="2" value="1.000000"{sync}/></parameters></patch>').encode('latin-1')
    chunk = b'sub3' + struct.pack('<I', len(xml)) + b'\0' * 24 + xml + b'rest'
    return b'CcnK' + b'\0' * 4 + b'FPCh' + b'\0' * 44 + struct.pack('>I', len(chunk)) + chunk


class FakeSurge:
    """A music-production folder and configuration with a stand-in host, a small catalog and a checked plugin and pedalboard."""

    PATCHES = {'factory/fx/plain': ('patches_factory/FX/Plain.fxp', 'factory', 0, [], None, False),
               'factory/fx/synced': ('patches_factory/FX/Synced.fxp', 'factory', 0, ['tempo-synced'], None, True),
               'factory/fx/up': ('patches_factory/FX/Up.fxp', 'factory', 12, [], None, False),
               'factory/fx/down': ('patches_factory/FX/Down.fxp', 'factory', -12, [], None, False),
               'designer/atmospheres/glow': ('patches_3rdparty/Designer/Atmospheres/Glow.fxp', 'Designer', 0, [], None, False),
               'designer/mpe/multi': ('patches_3rdparty/Designer/MPE/Multi.fxp', 'Designer', 0, ['MPE'], None, False),
               'factory/fx/mute': ('patches_factory/FX/Mute.fxp', 'factory', 0, ['silent on a plain note'], None, False)}

    def __init__(self, base):
        self.base = Path(base)
        self.skill = self.base / 'music-production'
        (self.skill / 'scripts').mkdir(parents=True)
        (self.skill / 'references').mkdir()
        (self.skill / 'scripts' / 'studio.py').write_text('', encoding='utf-8')
        (self.skill / 'scripts' / 'surge_host.py').write_text(FAKE_HOST, encoding='utf-8')
        (self.skill / 'SKILL.md').write_text('---\nname: music-production\nmetadata:\n  version: "9.9.9"\n---\n', encoding='utf-8')
        patches = self.base / 'patches'
        presets = {}
        for key, (path, source, transpose, flags, licence, synced) in self.PATCHES.items():
            blob = fake_patch(key, synced)
            (patches / path).parent.mkdir(parents=True, exist_ok=True)
            (patches / path).write_bytes(blob)
            presets[key] = {'path': path, 'bytes': len(blob), 'sha256': sha(blob), 'source': source, 'category': path.split('/')[-2], 'name': Path(path).stem,
                            'role': 'fx', 'transpose': transpose, 'flags': flags, 'license': licence}
        (self.skill / 'references' / 'surge-catalog.json').write_text(json.dumps({'presets': presets}), encoding='utf-8')
        bundle = self.base / 'Surge XT.vst3' / 'Contents'
        bundle.mkdir(parents=True)
        self.binary = bundle / 'Surge XT.vst3'
        self.binary.write_bytes(b'plugin')
        pylib = self.base / 'pylib'
        (pylib / 'pedalboard').mkdir(parents=True)
        self.board_file = pylib / 'pedalboard' / '__init__.py'
        self.board_file.write_bytes(b'# board\n')
        info = pylib / 'pedalboard-0.9.26.dist-info'
        info.mkdir()
        digest = base64.urlsafe_b64encode(hashlib.sha256(b'# board\n').digest()).rstrip(b'=').decode()
        record = f'pedalboard/__init__.py,sha256={digest},8\npedalboard-0.9.26.dist-info/RECORD,,\n'.encode()
        (info / 'RECORD').write_bytes(record)
        self.config = {'surge': {'path': str(self.base / 'Surge XT.vst3'), 'binary': str(self.binary), 'sha256': sha(b'plugin'), 'version': '1.3.4', 'catalog': str(patches)},
                       'pedalboard': {'path': str(pylib), 'version': '0.9.26', 'record_sha256': sha(record)}}
        self.log = self.base / 'jobs.log'
        self.patches = patches

    def jobs(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.exists() else []

    def env(self, mode=''):
        return mock.patch.dict(os.environ, {'SFX_FAKE_SURGE_LOG': str(self.log), 'SFX_FAKE_SURGE_MODE': mode})

    def provider(self, folder, record=True):
        return surge.Provider(folder, self.config, self.skill, record=record, work=self.base / 'render' / surge.WORK)


def source(**extra):
    return {'type': 'surge', 'patch': 'factory/fx/plain', 'notes': [[0, 69, 0.1, 100]], 'tail': 0.1, 'slot': 'take:1', **extra}


def project_with(layer, variants_count=1, **sound):
    return project.validate({'schema': 'sound-effects/1', 'title': 'surge test', 'seed': 7, 'sounds': [
        {'id': 'spell', 'category': 'magic', 'variants': variants_count, 'layers': [layer], **sound}]})


LAYER = {'source': {'type': 'surge', 'patch': 'factory/fx/plain', 'notes': [[0, 69, 0.1, 100]], 'tail': 0.1}, 'start': 0.0, 'length': 0.2}


def copied(layer=LAYER):
    return json.loads(json.dumps(layer))


class Schema(unittest.TestCase):
    def check(self, **fields):
        layer = copied()
        layer['source'].update(fields)
        return project_with(layer)

    def test_a_full_layer_is_accepted(self):
        self.check(macros={'1': 0.6, '8': 0}, bend=[[0, 0], [0.1, 12]], bend_range=12, wheel=[[0, 0], [0.1, 1]], bpm=96, tail=2, take=3, cents=-100)

    def test_bad_fields_are_refused(self):
        bad = [{'patch': 'no slashes'}, {'patch': '../../x/y'}, {'patch': 7}, {'notes': []}, {'notes': [[0, 128, 0.1, 100]]}, {'notes': [[0, 60, 0, 100]]}, {'macros': {'9': 0.5}},
               {'macros': {'1': 1.5}}, {'macros': []}, {'bend': [[0, 3]]}, {'bend': [[0.2, 0], [0.1, 1]]}, {'bend': [[0]]}, {'bend_range': 0}, {'bend_range': 25}, {'wheel': [[0, 1.5]]},
               {'wheel': [[0.2, 0], [0.1, 1]]}, {'bpm': 10}, {'tail': 0}, {'tail': 20}, {'take': 0}, {'take': 17}, {'take': 1.5}, {'slot': 'take:1'}, {'cents': 9999}]
        for fields in bad:
            with self.assertRaises(project.ProjectError, msg=str(fields)):
                self.check(**fields)

    def test_two_unnamed_layers_of_one_performance_are_refused(self):
        left, right = dict(copied(), pan=-0.5), dict(copied(), pan=0.5)                # a doubled patch, for width
        sound = {'id': 'spell', 'category': 'magic', 'variants': 1, 'layers': [left, right]}
        data = {'schema': 'sound-effects/1', 'title': 'surge test', 'seed': 7, 'sounds': [sound]}
        with self.assertRaisesRegex(project.ProjectError, r'layers\[1\].*same Surge performance as layers\[0\]'):
            project.validate(json.loads(json.dumps(data)))
        right['source']['notes'] = [[0, 69, 0.1, 101]]                                  # another velocity: another performance
        project.validate(json.loads(json.dumps(data)))
        right['source']['notes'] = left['source']['notes']
        left['source']['take'], right['source']['take'] = 1, 2                          # or a take of its own each
        project.validate(json.loads(json.dumps(data)))

    def test_it_is_not_the_bed_of_a_loop(self):
        layer = dict(copied(), length=2.0)
        with self.assertRaises(project.ProjectError):
            project_with(layer, use='loop', loop={'seconds': 2})
        project_with(dict(copied(), length=0.5), use='loop', loop={'seconds': 2})

    def test_the_renderer_refuses_it_as_a_bed_too(self):
        with self.assertRaisesRegex(ValueError, 'bed of a loop'):
            synth.layer_signal(copied(), 1, 0, loop=True, instruments=object())

    def test_it_needs_something_to_play_it(self):
        with self.assertRaisesRegex(ValueError, 'take'):
            synth.layer_signal(copied(), 1, 0)


class Identity(unittest.TestCase):
    def test_what_follows_the_take_is_not_in_its_key(self):
        self.assertEqual(surge.key_of(surge.performance(source())), surge.key_of(surge.performance(source(cents=300))))

    def test_what_shapes_the_performance_is(self):
        base = surge.key_of(surge.performance(source()))
        for change in ({'patch': 'factory/fx/up'}, {'notes': [[0, 70, 0.1, 100]]}, {'notes': [[0, 69, 0.2, 100]]}, {'macros': {'1': 0.5}}, {'bend': [[0, 1]]}, {'wheel': [[0, 1]]},
                       {'bpm': 90}, {'tail': 0.2}, {'slot': 'take:2'}, {'slot': 'spell/0/1'}):
            self.assertNotEqual(base, surge.key_of(surge.performance(source(**change))), change)

    def test_the_order_notes_are_written_in_is_not_a_change(self):
        a = source(notes=[[0, 69, 0.1, 100], [0.2, 72, 0.1, 90]])
        b = source(notes=[[0.2, 72, 0.1, 90], [0, 69, 0.1, 100]])
        self.assertEqual(surge.key_of(surge.performance(a)), surge.key_of(surge.performance(b)))

    def test_defaults_written_out_are_the_same_performance(self):
        a = {'type': 'surge', 'patch': 'factory/fx/plain', 'notes': [[0, 69, 1, 100]], 'slot': 'take:1'}
        b = dict(a, notes=[[0.0, 69, 1.0, 100]], macros={}, bpm=120, tail=2.0, bend_range=7)
        self.assertEqual(surge.key_of(surge.performance(a)), surge.key_of(surge.performance(b)))


class Events(unittest.TestCase):
    def rows(self, transpose=0, **extra):
        perf = surge.performance(source(**extra))
        return [(bytes.fromhex(h), t) for h, t in surge.midi_events(perf, transpose)]

    def test_every_event_comes_after_the_lead(self):
        rows = self.rows()
        self.assertEqual(rows, [(bytes([0x90, 69, 100]), surge.LEAD_S), (bytes([0x80, 69, 64]), surge.LEAD_S + 0.1)])

    def test_the_catalog_transposition_is_played(self):
        self.assertEqual(self.rows(transpose=12)[0][0], bytes([0x90, 81, 100]))
        self.assertEqual(self.rows(transpose=-12)[0][0], bytes([0x90, 57, 100]))

    def test_two_notes_struck_at_once_on_one_key_play_once(self):
        rows = self.rows(notes=[[0, 60, 0.3, 100], [0, 60, 0.3, 90]])
        self.assertEqual([r[0][0] for r in rows], [0x90, 0x80])

    def test_a_key_outside_midi_never_becomes_an_event(self):
        with self.assertRaisesRegex(surge.SurgeError, 'outside MIDI'):
            self.rows(transpose=12, notes=[[0, 120, 0.1, 90]])

    def test_a_retriggered_key_ends_the_take_where_its_last_note_ends(self):
        perf = surge.performance(source(notes=[[0, 60, 1.0, 100], [0.1, 60, 0.1, 90]], tail=0.1))
        self.assertAlmostEqual(surge.length_s(perf), 0.2 + 0.1)
        offs = [t for h, t in surge.midi_events(perf, 0) if h.startswith('80')]
        self.assertAlmostEqual(max(offs), surge.LEAD_S + 0.2)

    def test_a_key_struck_again_ends_its_note_first(self):
        rows = self.rows(notes=[[0, 60, 0.5, 100], [0.2, 60, 0.1, 90]])
        statuses = [(r[0][0], round(r[1] - surge.LEAD_S, 3)) for r in rows]
        self.assertEqual(statuses, [(0x90, 0.0), (0x80, 0.2), (0x90, 0.2), (0x80, 0.3)])


    def test_a_bend_is_set_before_the_note_and_moves_in_small_steps(self):
        rows = self.rows(bend=[[0, 0], [0.1, 12]], bend_range=12)
        self.assertEqual(rows[0][0][0], 0xE0)
        self.assertEqual(rows[0][1], surge.LEAD_S)
        self.assertLess(rows.index(next(r for r in rows if r[0][0] == 0x90)), 2)
        bends = [r for r in rows if r[0][0] == 0xE0]
        gaps = np.diff([r[1] for r in bends])
        self.assertLessEqual(float(gaps.max()), surge.CONTROL_STEP_S + 1e-9)
        last = bends[-1][0]
        self.assertEqual(last[1] | (last[2] << 7), 16383)
        self.assertEqual(rows[0][0][1] | (rows[0][0][2] << 7), 8192)

    def test_every_point_of_a_curve_is_played(self):
        rows = self.rows(bend=[[0, 0], [0.002, 2], [0.004, 0]], bend_range=2)
        values = [(r[0][1] | (r[0][2] << 7), round(r[1] - surge.LEAD_S, 6)) for r in rows if r[0][0] == 0xE0]
        self.assertIn((16383, 0.002), values)
        self.assertEqual(values[-1][0], 8192)

    def test_at_one_time_as_the_host_has_it_an_off_comes_before_an_on(self):
        rows = self.rows(notes=[[0, 60, 0.30000000000000004, 100], [0.3, 60, 0.1, 90]])
        at = [(r[0][0], r[1]) for r in rows]
        self.assertEqual(at[1:3], [(0x80, round(surge.LEAD_S + 0.3, 6)), (0x90, round(surge.LEAD_S + 0.3, 6))])

    def test_the_wheel_is_cc1_and_stops_with_the_take(self):
        rows = self.rows(wheel=[[0, 0], [5.0, 1]])
        wheel = [r for r in rows if r[0][0] == 0xB0]
        self.assertTrue(all(r[0][1] == 1 for r in wheel))
        self.assertLessEqual(max(r[1] for r in wheel), surge.LEAD_S + 0.1 + 0.1 + 1e-9)     # the note and its tail


class Variation(unittest.TestCase):
    def spec(self, variant, **fields):
        layer = copied()
        layer['source'].update(fields, notes=[[0.1, 69, 0.1, 100]])
        sound = project_with(layer, variants_count=3, vary={'time_pct': 25, 'pitch_cents': 50})['sounds'][0]
        spec, drawn = variants.layer_spec(sound, 7, variant)
        return spec['layers'][0]['source'], drawn

    def test_without_a_take_each_variant_is_its_own_performance_in_its_own_time(self):
        a, _ = self.spec(0)
        b, drawn = self.spec(2, bend=[[0.1, 1]], wheel=[[0.1, 1]])
        self.assertEqual(a['slot'], 'spell/factory/fx/plain/0/0')
        self.assertEqual(b['slot'], 'spell/factory/fx/plain/0/2')
        time = 1 + drawn['time_pct'] / 100
        self.assertAlmostEqual(b['notes'][0][0], 0.1 * time)
        self.assertAlmostEqual(b['notes'][0][2], 0.1 * time)
        self.assertAlmostEqual(b['bend'][0][0], 0.1 * time)
        self.assertAlmostEqual(b['wheel'][0][0], 0.1 * time)
        self.assertAlmostEqual(b['cents'], drawn['pitch_cents'])

    def test_other_layers_coming_or_going_leave_a_take_where_it_is(self):
        surge_layer = copied()
        other = {'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.1}
        another_patch = dict(copied(), source=dict(copied()['source'], patch='factory/fx/up'))
        slots = []
        for layers in ([surge_layer], [other, surge_layer], [another_patch, surge_layer], [surge_layer, other, surge_layer]):
            sound = project_with(copied(), variants_count=2)['sounds'][0]
            sound['layers'] = json.loads(json.dumps(layers))
            spec, _ = variants.layer_spec(sound, 7, 1)
            slots.append([l['source']['slot'] for l in spec['layers'] if l['source'].get('patch') == 'factory/fx/plain'])
        self.assertEqual(slots[0], slots[1])
        self.assertEqual(slots[0], slots[2])
        self.assertEqual(slots[3][0], slots[0][0])
        self.assertNotEqual(slots[3][0], slots[3][1])                 # two layers on one patch are two performances

    def test_another_phrase_on_the_same_patch_coming_or_going_leaves_a_take_where_it_is(self):
        high, low = copied(), copied()
        high['source']['notes'] = [[0, 72, 0.1, 100]]
        low['source']['notes'] = [[0, 60, 0.1, 100]]
        keys = []
        for layers in ([high, low], [low], [low, high]):
            sound = project_with(copied(), variants_count=2)['sounds'][0]
            sound['layers'] = json.loads(json.dumps(layers))
            spec, _ = variants.layer_spec(sound, 7, 1)
            keys.append([surge.key_of(surge.performance(l['source'])) for l in spec['layers'] if l['source']['notes'][0][1] == 60])
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[0], keys[2])

    def test_a_named_take_is_one_performance_for_every_variant(self):
        a, _ = self.spec(0, take=2)
        b, drawn = self.spec(2, take=2, bend=[[0.1, 1]])
        self.assertEqual(a['slot'], 'take:2')
        self.assertEqual(b['slot'], 'take:2')
        self.assertEqual(b['notes'], [[0.1, 69, 0.1, 100]])
        self.assertEqual(b['bend'], [[0.1, 1]])
        self.assertAlmostEqual(b['cents'], drawn['pitch_cents'])
        self.assertEqual(surge.key_of(surge.performance(dict(a, bend=[[0.1, 1]]))), surge.key_of(surge.performance(b)))


class Takes(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(f'surge_{self.id().rsplit(".", 1)[-1]}')
        self.fake = FakeSurge(self.base)
        self.folder = self.base / 'game' / surge.FOLDER
        self.folder.parent.mkdir()

    def test_a_take_is_recorded_once_and_replayed_the_same(self):
        with self.fake.env():
            first = self.fake.provider(self.folder).render(source())
            second = self.fake.provider(self.folder).render(source())
        self.assertEqual(len(self.fake.jobs()), 1)
        np.testing.assert_array_equal(first, second)
        self.assertAlmostEqual(float(np.max(np.abs(first))), 0.5, places=4)

    def test_a_new_recording_would_differ(self):
        with self.fake.env():
            a = self.fake.provider(self.folder).render(source(slot='take:1'))
            b = self.fake.provider(self.folder).render(source(slot='take:2'))
        self.assertEqual(len(self.fake.jobs()), 2)
        self.assertFalse(np.array_equal(a[:1000], b[:1000]))

    def test_the_first_render_mixes_what_was_saved(self):
        with self.fake.env():
            x = self.fake.provider(self.folder).render(source())
        receipt = next(self.folder.glob('*.json'))
        saved = wav.read(self.folder / json.loads(receipt.read_text(encoding='utf-8'))['wav'])[0]
        np.testing.assert_array_equal(x, np.asarray(saved, dtype=np.float64))

    def test_replay_needs_neither_surge_nor_a_configuration(self):
        with self.fake.env():
            first = self.fake.provider(self.folder).render(source())
        moved = self.base / 'elsewhere' / surge.FOLDER
        moved.parent.mkdir()
        os.rename(self.folder, moved)
        again = surge.Provider(moved).render(source())
        np.testing.assert_array_equal(first, again)

    def test_with_no_take_and_no_word_to_record_it_stops(self):
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, '--record-takes --config'):
            self.fake.provider(self.folder, record=False).render(source())
        self.assertEqual(self.fake.jobs(), [])
        self.assertFalse(self.folder.exists())

    def test_a_changed_take_is_refused(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        receipt = json.loads(next(self.folder.glob('*.json')).read_text(encoding='utf-8'))
        path = self.folder / receipt['wav']
        path.write_bytes(path.read_bytes()[:-3] + b'abc')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'changed'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(len(self.fake.jobs()), 1)

    def test_a_missing_recording_is_refused_rather_than_made_again(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        receipt = json.loads(next(self.folder.glob('*.json')).read_text(encoding='utf-8'))
        os.rename(self.folder / receipt['wav'], self.folder / 'moved.bin')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'missing'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(len(self.fake.jobs()), 1)

    def test_a_receipt_it_did_not_write_is_refused(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        path = next(self.folder.glob('*.json'))
        good = json.loads(path.read_text(encoding='utf-8'))
        for text in ('not json', '{"schema": "other"}', json.dumps({**good, 'key': 'f' * 64}), json.dumps({**good, 'wav': '../../outside.wav'}),
                     json.dumps({**good, 'wav': '../../outside.wav', 'wav_sha256': '../../outside'}),
                     json.dumps({k: v for k, v in good.items() if k != 'patch'}), json.dumps({k: v for k, v in good.items() if k != 'made_with'})):
            path.write_text(text, encoding='utf-8')
            with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'not a receipt', msg=text[:30]):
                self.fake.provider(self.folder).render(source())

    def test_a_take_above_full_scale_gets_one_gain(self):
        with self.fake.env('loud'):
            x = self.fake.provider(self.folder).render(source())
        self.assertAlmostEqual(float(np.max(np.abs(x))), 0.5, places=4)
        top = int(np.argmax(np.abs(x[:, 0])))
        self.assertAlmostEqual(float(x[top, 1] / x[top, 0]), 0.5, places=3)

    def test_the_tail_is_kept_whole(self):
        with self.fake.env():
            x = self.fake.provider(self.folder).render(source(notes=[[0, 69, 0.1, 100]], tail=0.3))
        self.assertEqual(len(x), int(round((surge.LEAD_S + 0.4) * RATE)) - int(round(surge.LEAD_S * RATE)))

    def test_a_render_killed_before_its_receipt_leaves_no_take(self):
        real = os.replace
        calls = []

        def stop_at_receipt(a, b):
            if str(b).endswith('.json'):
                calls.append(b)
                raise KeyboardInterrupt
            return real(a, b)
        with self.fake.env(), mock.patch('sfxlib.surge.os.replace', stop_at_receipt), self.assertRaises(KeyboardInterrupt):
            self.fake.provider(self.folder).render(source())
        self.assertTrue(calls)
        self.assertEqual(list(self.folder.glob('*.json')), [])
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        self.assertEqual(len(list(self.folder.glob('*.json'))), 1)

    def test_two_renders_at_once_share_one_take(self):
        results = []

        def play():
            results.append(self.fake.provider(self.folder).render(source()))
        with self.fake.env('slow'):
            threads = [threading.Thread(target=play) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(self.fake.jobs()), 1)
        np.testing.assert_array_equal(results[0], results[1])

    def test_the_folder_holds_only_takes_and_the_work_files_are_emptied(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source(patch='factory/fx/synced'))
        names = sorted(p.name for p in self.folder.iterdir())
        self.assertEqual([n for n in names if not (n.endswith('.wav') or n.endswith('.json'))], ['.gdignore', '.lock'])
        self.assertEqual(len([n for n in names if n.endswith('.wav')]), 1)
        work = self.base / 'render' / surge.WORK
        self.assertEqual({p.name: p.stat().st_size for p in work.iterdir()}, {'job.json': 0, 'take.wav': 0, 'patch.fxp': 0})
        with self.fake.env('fail'), self.assertRaises(surge.SurgeError):
            self.fake.provider(self.folder).render(source(slot='take:2'))
        self.assertEqual({p.stat().st_size for p in work.iterdir()}, {0})

    def test_recording_needs_a_work_folder(self):
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'render'):
            surge.Provider(self.folder, self.fake.config, self.fake.skill, record=True).render(source())
        self.assertEqual(self.fake.jobs(), [])

    def test_relative_folders_reach_the_host_whole(self):
        here = os.getcwd()
        os.chdir(self.base)
        try:
            with self.fake.env():
                surge.Provider(Path('game') / surge.FOLDER, self.fake.config, 'music-production', record=True, work=Path('render') / surge.WORK).render(source())
        finally:
            os.chdir(here)
        job = self.fake.jobs()[0]
        self.assertTrue(all(Path(job[k]).is_absolute() for k in ('patch', 'out')))

    def test_a_take_of_the_wrong_length_is_not_kept(self):
        with self.fake.env('short'), self.assertRaisesRegex(surge.SurgeError, 'frames'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(list(self.folder.glob('*.json')), [])

    def test_a_file_already_named_like_the_take_but_holding_other_audio_is_not_taken_for_it(self):
        with self.fake.env('fixed'):
            self.fake.provider(self.folder).render(source())
        receipt = next(self.folder.glob('*.json'))
        record = json.loads(receipt.read_text(encoding='utf-8'))
        (self.base / 'aside').mkdir()
        os.rename(receipt, self.base / 'aside' / receipt.name)
        wav_path = self.folder / record['wav']
        wav_path.write_bytes(wav_path.read_bytes()[:-3] + b'abc')
        with self.fake.env('fixed'), self.assertRaisesRegex(surge.SurgeError, record['wav']):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(list(self.folder.glob('*.json')), [])

    def test_a_receipt_must_be_of_this_performance_and_length(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        path = next(self.folder.glob('*.json'))
        good = json.loads(path.read_text(encoding='utf-8'))
        for bad in ({'frames': good['frames'] + 1}, {'performance': dict(good['performance'], tail=9)}):
            path.write_text(json.dumps({**good, **bad}), encoding='utf-8')
            with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'receipt', msg=str(bad)[:30]):
                surge.Provider(self.folder).render(source())

    def test_a_missing_recording_stops_before_the_render_makes_its_folder(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(dict(source(), slot=None, take=1))
        receipt = json.loads(next(self.folder.glob('*.json')).read_text(encoding='utf-8'))
        (self.base / 'aside').mkdir()
        os.rename(self.folder / receipt['wav'], self.base / 'aside' / receipt['wav'])
        out = self.base / 'render_2'
        layer = copied()
        layer['source']['take'] = 1
        with self.assertRaisesRegex(render.RenderError, 'missing'):
            render.render(project_with(layer), out, takes=self.folder)
        self.assertFalse(out.exists())

    def test_staging_files_never_write_over_a_file_already_there(self):
        scratch = self.base / 'first' / surge.FOLDER
        scratch.parent.mkdir()
        with self.fake.env('fixed'):
            self.fake.provider(scratch).render(source())
        key = next(scratch.glob('*.json')).stem
        digest = next(scratch.glob('*.wav')).stem
        self.folder.mkdir()
        keep = [self.folder / f'.{digest}.wav.0.part', self.folder / f'.{key}.json.0.part']
        for path in keep:
            path.write_bytes(b'keep')
        with self.fake.env('fixed'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual([p.read_bytes() for p in keep], [b'keep', b'keep'])
        self.assertTrue((self.folder / f'{key}.json').is_file())

    def test_a_render_into_the_take_folder_is_refused(self):
        self.folder.mkdir()
        (self.folder.parent / 'build').mkdir()
        around = self.folder.parent / 'build' / '..' / surge.FOLDER                 # the same folder, spelt through another
        for out in (self.folder, self.folder / 'render_1', around, around / 'render_1'):
            with self.subTest(out=str(out)), self.assertRaisesRegex(render.RenderError, 'holds only takes'):
                render.render(project_with(copied()), out, takes=self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_render_beside_the_take_folder_spelt_through_it_renders(self):
        self.folder.mkdir()
        render.render(project_with({'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.1}), self.folder / '..' / 'render_1', takes=self.folder, page=False)
        self.assertTrue((self.folder.parent / 'render_1' / 'master').is_dir())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_take_shorter_than_its_performance_is_refused_on_replay(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        path = next(self.folder.glob('*.json'))
        good = json.loads(path.read_text(encoding='utf-8'))
        tail = dict(good['performance'], tail=0.9)          # a receipt rewritten for a longer performance, its take left short
        renamed = {**good, 'performance': tail, 'key': surge.key_of(tail)}
        (self.folder / f"{renamed['key']}.json").write_text(json.dumps(renamed), encoding='utf-8')
        with self.assertRaisesRegex(surge.SurgeError, 'frames'):
            surge.Provider(self.folder).render(source(tail=0.9))

    def test_the_folder_is_hidden_from_godot(self):
        with self.fake.env():
            self.fake.provider(self.folder).render(source())
        self.assertTrue((self.folder / '.gdignore').is_file())

    @unittest.skipUnless(os.name == 'nt', 'a directory junction is a Windows thing')
    def test_a_junction_is_not_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.folder), str(outside)], check=True, capture_output=True)
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'link'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(list(outside.iterdir()), [])

    def link_folder(self, link, target):
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
        else:
            os.symlink(target, link, target_is_directory=True)

    def test_takes_are_not_replayed_through_a_link(self):
        real = self.base / 'real_takes'
        with self.fake.env():
            surge.Provider(real, self.fake.config, self.fake.skill, record=True, work=self.base / 'render' / surge.WORK).render(source())
        self.link_folder(self.folder, real)
        with self.assertRaisesRegex(surge.SurgeError, 'link'):
            surge.Provider(self.folder).render(source())

    def test_a_linked_work_folder_is_not_written_through(self):
        (self.base / 'render').mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        self.link_folder(self.base / 'render' / surge.WORK, outside)
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'link'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_link_inside_the_folder_is_not_written_through(self):
        (self.base / 'render' / surge.WORK).mkdir(parents=True)
        victim = self.base / 'victim.wav'
        victim.write_bytes(b'keep')
        try:
            os.symlink(victim, self.base / 'render' / surge.WORK / 'take.wav')
        except OSError:
            self.skipTest('this account cannot make symbolic links')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'link'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(victim.read_bytes(), b'keep')


class Host(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(f'surge_{self.id().rsplit(".", 1)[-1]}')
        self.fake = FakeSurge(self.base)
        self.folder = self.base / 'game' / surge.FOLDER
        self.folder.parent.mkdir()

    def record(self, mode='', **extra):
        with self.fake.env(mode):
            return self.fake.provider(self.folder).render(source(**extra))

    def test_the_job_is_what_the_host_expects(self):
        self.record(macros={'2': 0.25}, bend=[[0, 1]], bend_range=5)
        job = self.fake.jobs()[0]
        self.assertEqual(job['plugin'], str(Path(self.fake.config['surge']['path']).resolve()))
        self.assertEqual(job['pylib'], str(Path(self.fake.config['pedalboard']['path']).resolve()))
        self.assertEqual(job['rate'], RATE)
        self.assertEqual(job['trim'], 4800)
        self.assertEqual(job['macros'], {'2': 0.25})
        self.assertEqual(job['bend_range'], 5)
        self.assertAlmostEqual(job['duration'], surge.LEAD_S + 0.1 + 0.1)
        self.assertEqual(job['patch_bytes'].encode('latin-1'), (self.fake.patches / 'patches_factory/FX/Plain.fxp').read_bytes())
        self.assertEqual(Path(job['patch']).parent, (self.base / 'render' / surge.WORK).resolve())

    def test_a_patch_built_an_octave_off_is_played_where_it_sounds_right(self):
        self.record(patch='factory/fx/up')
        self.assertEqual(bytes.fromhex(self.fake.jobs()[0]['events'][0][0])[1], 81)

    def test_a_third_party_patch_is_played(self):
        self.record(patch='designer/atmospheres/glow')
        receipt = json.loads(next(self.folder.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(receipt['patch']['source'], 'Designer')
        self.assertEqual(receipt['patch']['id'], 'designer/atmospheres/glow')

    def test_keys_the_patch_s_transposition_puts_outside_midi_are_refused_first(self):
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'written keys 120 fall outside .* an octave lower'):
            self.fake.provider(self.folder).render(source(patch='factory/fx/up', notes=[[0, 120, 0.1, 90], [0, 60, 0.1, 90]]))
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'written keys 5 fall outside .* an octave higher'):
            self.fake.provider(self.folder).render(source(patch='factory/fx/down', notes=[[0, 5, 0.1, 90]]))
        self.assertEqual(self.fake.jobs(), [])

    def test_patches_that_cannot_play_a_plain_note_are_refused(self):
        for patch, why in (('designer/mpe/multi', 'MPE'), ('factory/fx/mute', 'silent'), ('factory/fx/nothing', 'no patch')):
            with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, why):
                self.fake.provider(self.folder).render(source(patch=patch))
        self.assertEqual(self.fake.jobs(), [])

    def test_a_tempo_synced_patch_plays_a_private_copy_at_its_tempo(self):
        original = (self.fake.patches / 'patches_factory/FX/Synced.fxp').read_bytes()
        self.record(patch='factory/fx/synced', bpm=60)
        job = self.fake.jobs()[0]
        played = job['patch_bytes'].encode('latin-1')
        self.assertNotEqual(played, original)
        self.assertNotIn(b'temposync="1"', played)
        self.assertIn(b'<fx0_p0 type="2" value="-1.000000"', played)
        self.assertIn(b'<a_lfo0_rate type="2" value="0.000000"', played)
        self.assertEqual((self.fake.patches / 'patches_factory/FX/Synced.fxp').read_bytes(), original)

    def test_a_changed_plugin_patch_or_pedalboard_is_refused(self):
        patch = self.fake.patches / 'patches_factory/FX/Plain.fxp'
        keep = patch.read_bytes()
        patch.write_bytes(keep + b'x')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'patch'):
            self.fake.provider(self.folder).render(source())
        patch.write_bytes(keep)
        self.fake.binary.write_bytes(b'other')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'plugin'):
            self.fake.provider(self.folder).render(source())
        self.fake.binary.write_bytes(b'plugin')
        self.fake.board_file.write_bytes(b'# other\n')
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'pedalboard'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(self.fake.jobs(), [])

    def test_a_binary_outside_the_plugin_bundle_is_refused(self):
        other = self.base / 'other.vst3'
        other.write_bytes(b'plugin')
        self.fake.config['surge']['binary'] = str(other)
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, 'plugin'):
            self.fake.provider(self.folder).render(source())

    def test_no_surge_in_the_configuration_says_what_is_needed(self):
        with self.assertRaisesRegex(surge.SurgeError, 'music-production'):
            surge.Provider(self.folder, {}, self.fake.skill, record=True).render(source())
        with self.assertRaisesRegex(surge.SurgeError, '--config'):
            surge.Provider(self.folder, None, self.fake.skill, record=True).render(source())

    def test_a_failed_host_says_so(self):
        with self.assertRaisesRegex(surge.SurgeError, 'the fake host stopped'):
            self.record('fail')
        self.assertEqual(list(self.folder.glob('*.json')), [])

    def native(self, tag, top=True):
        """Put a compiled module of pedalboard for `tag` (such as cp399) where its wheel puts it, beside the package (or inside
        it), listed in its RECORD."""
        pylib = Path(self.fake.config['pedalboard']['path'])
        where = '' if top else 'pedalboard/'
        name = f'{where}pedalboard_native.{tag}-win_amd64.pyd' if os.name == 'nt' else f'{where}pedalboard_native.cpython-{tag[2:]}-x86_64-linux-gnu.so'
        (pylib / name).write_bytes(b'native')
        record = pylib / 'pedalboard-0.9.26.dist-info' / 'RECORD'
        digest = base64.urlsafe_b64encode(hashlib.sha256(b'native').digest()).rstrip(b'=').decode()
        record.write_bytes(record.read_bytes() + f'{name},sha256={digest},6\n'.encode())
        self.fake.config['pedalboard']['record_sha256'] = sha(record.read_bytes())

    def test_a_pedalboard_built_for_another_python_is_said_first(self):
        self.native('cp399', top=False)
        self.assertIn('3.99', surge.status(self.fake.config, self.fake.skill))
        self.native('cp398')
        self.assertIn('3.98', surge.status(self.fake.config, self.fake.skill))     # found beside the package, where its wheel puts it
        self.assertIn('3.99', surge.status(self.fake.config, self.fake.skill))
        with self.fake.env(), self.assertRaisesRegex(surge.SurgeError, '3.99'):
            self.fake.provider(self.folder).render(source())
        self.assertEqual(self.fake.jobs(), [])

    def test_a_pedalboard_built_for_this_python_passes(self):
        self.native(f'cp{sys.version_info[0]}{sys.version_info[1]}')
        self.assertTrue(surge.status(self.fake.config, self.fake.skill).startswith('OK'))

    def test_a_python_that_cannot_load_pedalboard_is_told(self):
        with self.assertRaisesRegex(surge.SurgeError, 'Python'):
            self.record('no_board')

    def test_a_silent_or_broken_take_is_not_kept(self):
        for mode, why in (('silent', 'silent'), ('nan', 'finite')):
            with self.assertRaisesRegex(surge.SurgeError, why):
                self.record(mode)
        self.assertEqual(list(self.folder.glob('*.json')), [])

    def test_a_host_that_writes_nothing_cannot_pass_off_the_last_take(self):
        self.record(slot='take:1')
        with self.assertRaisesRegex(surge.SurgeError, 'no usable audio'):
            self.record('nothing', slot='take:2')
        self.assertEqual(len(list(self.folder.glob('*.json'))), 1)


class Finding(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(f'surge_{self.id().rsplit(".", 1)[-1]}')
        self.fake = FakeSurge(self.base)
        self.presets = surge.load_catalog(self.fake.skill)

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), *args], capture_output=True, text=True, encoding='utf-8', env=helpers.child_env(), cwd=cwd or self.base)

    def test_every_patch_that_plays_a_plain_note_is_offered(self):
        found = surge.find(self.presets)
        self.assertIn('designer/atmospheres/glow', found)
        self.assertIn('factory/fx/plain', found)
        self.assertNotIn('designer/mpe/multi', found)
        self.assertNotIn('factory/fx/mute', found)

    def test_words_are_matched_whole_in_the_id_name_category_and_designer(self):
        self.assertEqual(surge.find(self.presets, 'glow'), ['designer/atmospheres/glow'])
        self.assertEqual(surge.find(self.presets, 'atmosphere'), ['designer/atmospheres/glow'])
        self.assertEqual(surge.find(self.presets, 'designer glow'), ['designer/atmospheres/glow'])
        self.assertEqual(surge.find(self.presets, 'glo'), [])
        self.assertEqual(surge.find(self.presets, 'glow plain'), [])

    def test_role_and_measured_character_narrow_it(self):
        self.presets['factory/fx/plain']['character'] = 'dark, slow swell'
        self.presets['factory/fx/plain']['role'] = 'pad'
        self.assertEqual(surge.find(self.presets, role='pad'), ['factory/fx/plain'])
        self.assertEqual(surge.find(self.presets, character='dark,slow'), ['factory/fx/plain'])
        self.assertEqual(surge.find(self.presets, character='bright'), [])

    def test_each_project_has_an_order_of_its_own_that_stays(self):
        presets = {f'factory/fx/p{i}': dict(self.presets['factory/fx/plain'], name=f'P{i}') for i in range(40)}
        one, two = surge.find(presets, project='hollow'), surge.find(presets, project='ember')
        self.assertEqual(sorted(one), sorted(two))
        self.assertNotEqual(one, two)
        self.assertEqual(one, surge.find(presets, project='hollow'))

    def test_the_command_takes_its_order_from_the_folder_it_runs_in(self):
        one, two = self.base / 'hollow', self.base / 'ember'
        one.mkdir(), two.mkdir()
        order = lambda cwd: [row['id'] for row in json.loads(self.cli('surge', '--json', '--music-skill', str(self.fake.skill), cwd=cwd).stdout)]
        self.assertEqual(order(one), surge.find(self.presets, project='hollow'))
        self.assertEqual(order(two), surge.find(self.presets, project='ember'))
        self.assertNotEqual(order(one), order(two))

    def test_the_command_lists_patches_and_how_to_play_one(self):
        done = self.cli('surge', '--find', 'glow', '--music-skill', str(self.fake.skill))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('designer/atmospheres/glow', done.stdout)
        self.assertIn('"type": "surge"', done.stdout)
        self.assertIn('--record-takes', done.stdout)

    def test_the_command_limits_what_it_shows_and_says_so(self):
        done = self.cli('surge', '--limit', '2', '--music-skill', str(self.fake.skill))
        self.assertIn('5 patches match, 2 shown', done.stdout)

    def test_it_can_answer_as_json(self):
        done = self.cli('surge', '--find', 'glow', '--json', '--music-skill', str(self.fake.skill))
        rows = json.loads(done.stdout)
        self.assertEqual([r['id'] for r in rows], ['designer/atmospheres/glow'])

    def test_one_patch_in_full(self):
        catalog = json.loads((self.fake.skill / 'references' / 'surge-catalog.json').read_text(encoding='utf-8'))
        catalog['presets']['factory/fx/up'].update(macros={'1': {'label': 'Shine', 'targets': ['filter 1 cutoff +12 semitones']}}, wheel=['LFO 1 depth +1.00'], character='bright, fast attack')
        (self.fake.skill / 'references' / 'surge-catalog.json').write_text(json.dumps(catalog), encoding='utf-8')
        done = self.cli('surge', '--id', 'factory/fx/up', '--music-skill', str(self.fake.skill))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for text in ('Shine', 'filter 1 cutoff', 'LFO 1 depth', 'bright, fast attack', '+12'):
            self.assertIn(text, done.stdout)

    def test_a_patch_that_cannot_be_played_says_why(self):
        done = self.cli('surge', '--id', 'designer/mpe/multi', '--music-skill', str(self.fake.skill))
        self.assertEqual(done.returncode, 1)
        self.assertIn('MPE', done.stdout)
        unknown = self.cli('surge', '--id', 'factory/fx/nothing', '--music-skill', str(self.fake.skill))
        self.assertEqual(unknown.returncode, 1)
        self.assertIn('no patch', unknown.stdout)

    def test_a_search_with_no_latin_word_says_so(self):
        done = self.cli('surge', '--find', '魔法', '--music-skill', str(self.fake.skill))
        self.assertEqual(done.returncode, 1)
        self.assertIn('A-Z', done.stdout)

    def test_without_music_production_it_says_what_is_needed(self):
        empty = self.base / 'nothing_here'
        empty.mkdir()
        done = self.cli('surge', '--find', 'glow', '--music-skill', str(empty))
        self.assertEqual(done.returncode, 1)
        self.assertIn('music-production', done.stdout)

    def test_surge_json_is_valid_whatever_the_console_can_show(self):
        catalog = self.fake.skill / 'references' / 'surge-catalog.json'
        data = json.loads(catalog.read_text(encoding='utf-8'))
        data['presets']['designer/atmospheres/glow']['name'] = 'Glow café'
        catalog.write_text(json.dumps(data), encoding='utf-8')
        env = dict(helpers.child_env(), PYTHONIOENCODING='cp950')         # a Windows console that has no é
        for args in (['--find', 'glow'], ['--id', 'designer/atmospheres/glow']):
            done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'surge', *args, '--music-skill', str(self.fake.skill), '--json'],
                                  capture_output=True, env=env, cwd=self.base, timeout=120)
            with self.subTest(args=args):
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn('Glow café', json.dumps(json.loads(done.stdout.decode('ascii')), ensure_ascii=False))

    def test_doctor_says_whether_surge_can_play(self):
        config = self.base / 'config.json'
        config.write_text(json.dumps(self.fake.config), encoding='utf-8')
        done = self.cli('doctor', '--music-skill', str(self.fake.skill))
        self.assertIn('Surge XT: give --config', done.stdout)
        self.fake.binary.write_bytes(b'other')
        changed = surge.status(self.fake.config, self.fake.skill)
        self.assertIn('plugin', changed)
        self.fake.binary.write_bytes(b'plugin')
        self.assertEqual(surge.status(self.fake.config, self.fake.skill), 'OK (5 of its 7 patches can play a layer; 2 are MPE or silent)')
        self.assertIn('not in this configuration', surge.status({}, self.fake.skill))
        self.assertIn('not found', surge.status(None, None))
        self.assertIn('--music-skill', surge.status(None, None))                 # a plugin install sits in a folder of its own
        with mock.patch.object(surge.mpbridge, 'find_music_skill', return_value=None), self.assertRaisesRegex(surge.SurgeError, '--music-skill'):
            surge.Provider(self.base / 'takes', self.fake.config, None, record=True)._skill()

    def test_doctor_counts_only_the_patches_its_patch_folder_holds(self):
        (self.fake.patches / self.fake.PATCHES['factory/fx/plain'][0]).unlink()
        self.assertEqual(surge.status(self.fake.config, self.fake.skill),
                         "OK (4 of its 7 patches can play a layer; 2 are MPE or silent; 1 is missing from its patch folder, which music-production's "
                         "bootstrap.py brings back)")
        for key in ('factory/fx/synced', 'factory/fx/up', 'factory/fx/down', 'designer/atmospheres/glow'):
            (self.fake.patches / self.fake.PATCHES[key][0]).unlink()
        self.assertEqual(surge.status(self.fake.config, self.fake.skill),
                         "none of its patches that can play a layer is in its patch folder: run music-production's bootstrap.py again")
        shutil.rmtree(self.fake.patches)
        self.assertEqual(surge.status(self.fake.config, self.fake.skill),
                         "the patch folder this configuration names is missing: run music-production's bootstrap.py again")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(f'surge_{self.id().rsplit(".", 1)[-1]}')
        self.fake = FakeSurge(self.base)
        self.game = self.base / 'game'
        self.game.mkdir()
        self.takes = self.game / surge.FOLDER

    def render(self, proj, name, record=True, config=True):
        return render.render(proj, self.game / name, config=self.fake.config if config else None, page=False, takes=self.takes, record_takes=record, music_skill=self.fake.skill)

    def masters(self, name):
        return {p.name: p.read_bytes() for p in sorted((self.game / name / 'master').glob('*.wav'))}

    def test_a_second_render_is_byte_identical(self):
        proj = project_with(copied(), variants_count=3, vary={'time_pct': 10, 'pitch_cents': 30})
        with self.fake.env():
            report = self.render(proj, 'one')
            self.assertEqual(report['surge_takes']['recorded'], 3)
            again = self.render(proj, 'two', record=False, config=False)
            self.assertEqual(again['surge_takes']['recorded'], 0)
        self.assertEqual(len(self.fake.jobs()), 3)
        self.assertEqual(self.masters('one'), self.masters('two'))

    def test_a_copy_without_its_takes_stops_before_making_the_render_folder(self):
        with self.fake.env(), self.assertRaisesRegex(render.RenderError, 'spell.*--record-takes'):
            self.render(project_with(copied()), 'one', record=False)
        self.assertFalse((self.game / 'one').exists())
        with self.fake.env():
            self.render(project_with(copied()), 'one')
        self.assertTrue((self.game / 'one' / 'master' / 'spell_01.wav').is_file())

    def test_editing_what_follows_the_take_records_nothing(self):
        with self.fake.env():
            self.render(project_with(copied()), 'one')
            layer = copied()
            layer['source']['cents'] = 100
            layer['gain_db'] = -3
            layer['filter'] = {'type': 'lowpass', 'cutoff': 2000}
            self.render(project_with(layer), 'two', record=False)
        self.assertEqual(len(self.fake.jobs()), 1)
        self.assertNotEqual(self.masters('one'), self.masters('two'))

    def test_a_named_take_is_recorded_once_for_all_variants(self):
        layer = copied()
        layer['source']['take'] = 1
        proj = project_with(layer, variants_count=4, vary={'time_pct': 20, 'pitch_cents': 40})
        with self.fake.env():
            self.render(proj, 'one')
        self.assertEqual(len(self.fake.jobs()), 1)

    def test_a_refused_patch_stops_before_the_render_folder(self):
        layer = copied()
        layer['source']['patch'] = 'designer/mpe/multi'
        with self.fake.env(), self.assertRaisesRegex(render.RenderError, 'MPE'):
            self.render(project_with(layer), 'one')
        self.assertFalse((self.game / 'one').exists())

    def test_the_render_records_what_it_played(self):
        with self.fake.env():
            self.render(project_with(copied()), 'one')
        resolved = json.loads((self.game / 'one' / 'resolved.json').read_text(encoding='utf-8'))
        row = resolved['surge_takes'][0]
        self.assertEqual(row['patch']['id'], 'factory/fx/plain')
        self.assertEqual(row['wav_sha256'], Path(row['wav']).stem)
        self.assertEqual(resolved['sounds'][0]['variants'][0]['layers'][0]['source'], 'surge:factory/fx/plain')

    def test_a_project_with_no_surge_layer_never_asks_for_the_folder(self):
        proj = project.validate({'schema': 'sound-effects/1', 'title': 'plain', 'sounds': [{'id': 'ping', 'category': 'ui', 'recipe': 'ui.confirm'}]})
        report = render.render(proj, self.game / 'plain', page=False)
        self.assertNotIn('surge_takes', report)
        self.assertFalse(self.takes.exists())

    def test_the_command_line(self):
        path = self.game / 'project.json'
        path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 'surge test', 'seed': 7, 'sounds': [
            {'id': 'spell', 'category': 'magic', 'variants': 1, 'layers': [copied()]}]}), encoding='utf-8')
        config = self.base / 'config.json'
        config.write_text(json.dumps(self.fake.config), encoding='utf-8')
        env = dict(helpers.child_env(), SFX_FAKE_SURGE_LOG=str(self.fake.log))
        command = [sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'render', str(path), '--no-page', '--config', str(config), '--music-skill', str(self.fake.skill)]
        stopped = subprocess.run([*command, '--output', str(self.game / 'r1')], capture_output=True, text=True, encoding='utf-8', env=env, cwd=self.game)
        self.assertEqual(stopped.returncode, 1)
        self.assertIn('--record-takes', stopped.stdout)
        done = subprocess.run([*command, '--output', str(self.game / 'r1'), '--record-takes'], capture_output=True, text=True, encoding='utf-8', env=env, cwd=self.game)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('1 recorded', done.stdout)
        self.assertTrue((self.game / surge.FOLDER).is_dir())
        no_config = [c for c in command if c not in (str(config), '--config', str(self.fake.skill), '--music-skill')]
        again = subprocess.run([*no_config, '--output', str(self.game / 'r2')], capture_output=True, text=True, encoding='utf-8', env=env, cwd=self.game)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.masters('r1'), self.masters('r2'))


if __name__ == '__main__':
    unittest.main()
