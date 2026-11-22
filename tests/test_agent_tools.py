"""What an agent reaches for while it designs: words that mean words, the events of a recording, a take's own level, one
sound rendered alone, and output that says what it means."""
import json
import subprocess
import sys
import unittest

import numpy as np

import helpers
import test_samples as ts
import make_fsd50k_packs
from sfxlib import denoise, fsd50k, project, render, samples, variants

RATE = 48000
SFX = helpers.SCRIPTS / 'sfx.py'


def run(*args, env=None):
    return subprocess.run([sys.executable, str(SFX), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=env or helpers.child_env(), timeout=300)


def write_project(folder, sounds, **extra):
    path = folder / 'project.json'
    path.write_text(json.dumps({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': sounds, **extra}),
                    encoding='utf-8')
    return path


UI = {'id': 'ok', 'category': 'ui', 'recipe': 'ui.confirm'}
TICK = {'id': 'tick', 'category': 'ui', 'recipe': 'ui.tick'}


def row(name, title, tags=(), labels=()):
    return {'library': 'pack', 'file': name, 'title': title, 'tags': list(tags), 'labels': list(labels), 'seconds': 1.0,
            'channels': 1, 'measures': {'range_db': 30.0, 'peak_db': -3.0}, 'screen': []}


class WholeWords(unittest.TestCase):
    INDEX = {'packs': {'pack': {'title': 'Pack', 'licence': 'CC0-1.0', 'files': 8}}, 'files': [
        row('1', 'crow calling'), row('2', 'large crowd'), row('3', 'microwave door'), row('4', 'old wooden chest'),
        row('5', 'orchestra tuning'), row('6', 'door slamming'), row('7', 'two crows'), row('8', 'grasshopper')]}

    def names(self, text):
        return {f['file'] for f in samples.find(self.INDEX, text)}

    def test_a_word_is_not_part_of_another(self):
        self.assertEqual(self.names('crow'), {'1', '7'})                  # not 'crowd'
        self.assertEqual(self.names('chest'), {'4'})                      # not 'orchestra'
        self.assertEqual(self.names('grass'), set())                      # not 'grasshopper'
        self.assertEqual(self.names('wave'), set())                       # not 'microwave'

    def test_a_word_finds_its_forms(self):
        self.assertEqual(self.names('slam'), {'6'})                       # slamming
        self.assertEqual(self.names('crows'), {'1', '7'})
        self.assertEqual(self.names('calls'), set())


class RecordingEvents(unittest.TestCase):
    @staticmethod
    def strike(x, at, amp, seed=4, rise=0.0):
        rng = np.random.default_rng(seed)
        i = int(at * RATE)
        n = 9600
        shape = np.exp(-np.arange(n) / 1200)
        if rise:
            m = int(rise * RATE)
            shape[:m] *= np.linspace(0.02, 1.0, m)
        x[i:i + n] += amp * np.clip(rng.standard_normal(n), -2.5, 2.5) / 2.5 * shape

    def test_each_event_has_its_onset_peak_level_and_end(self):
        x = np.zeros(2 * RATE)
        self.strike(x, 0.5, 0.9)
        self.strike(x, 1.25, 0.2)
        found = denoise.events(x, RATE)
        self.assertEqual(len(found), 2)
        self.assertAlmostEqual(found[0]['onset'], 0.5, delta=0.003)
        self.assertAlmostEqual(found[1]['onset'], 1.25, delta=0.003)
        self.assertTrue(found[0]['onset'] <= found[0]['peak'] <= found[0]['onset'] + 0.06)
        self.assertAlmostEqual(found[0]['db'], 0.0, delta=0.1)
        self.assertLess(found[1]['db'], -8.0)
        self.assertTrue(found[0]['peak'] < found[0]['end'] < 1.25)

    def test_the_peak_of_a_slow_attack_is_where_the_sound_is(self):
        x = np.zeros(RATE)
        self.strike(x, 0.4, 0.8, rise=0.06)
        found = denoise.events(x, RATE)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]['peak'], found[0]['onset'] + 0.02)

    def test_a_long_recording_lists_the_first_events_and_says_how_many_more(self):
        folder = helpers.scratch('agent_events_many')
        x = np.zeros((6 * RATE, 1))
        for i in range(40):
            self.strike(x[:, 0], 0.1 + 0.14 * i, 0.5, seed=i)
        members = {'walk.wav': ts.wav_bytes(np.clip(x, -0.9, 0.9), rate=RATE), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Walk Pack', members), folder / 'library', licence='CC0-1.0')
        out = run('samples', '--library', folder / 'library', '--events', 'walk-pack/walk').stdout
        takes = json.loads(out[out.index('[{'):].splitlines()[0])
        self.assertEqual(len(takes), 16)                     # all a layer can hold
        self.assertIn('40 events', out)
        self.assertIn('--limit 0', out)
        out = run('samples', '--library', folder / 'library', '--events', 'walk-pack/walk', '--limit', '0').stdout
        self.assertEqual(sum(1 for line in out.splitlines() if line.endswith(' dB')), 40)       # the table lists them all
        self.assertEqual(len(json.loads(out[out.index('[{'):].splitlines()[0])), 16)            # the paste holds what a layer can

    def test_the_command_prints_takes_to_paste(self):
        folder = helpers.scratch('agent_events')
        x = np.zeros((RATE, 1))
        self.strike(x[:, 0], 0.25, 0.8)
        self.strike(x[:, 0], 0.7, 0.5, seed=5)
        members = {'hit.wav': ts.wav_bytes(x, rate=RATE), 'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Hit Pack', members), folder / 'library', licence='CC0-1.0')
        proc = run('samples', '--library', folder / 'library', '--events', 'hit-pack/hit')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('2 events', proc.stdout)
        takes = json.loads(proc.stdout[proc.stdout.index('[{'):].splitlines()[0])
        self.assertEqual([t['file'] for t in takes], ['hit', 'hit'])
        self.assertAlmostEqual(takes[0]['offset'], 0.248, delta=0.004)
        self.assertAlmostEqual(takes[1]['offset'], 0.698, delta=0.004)


class TakeGain(unittest.TestCase):
    def layer(self, take):
        return {'id': 'x', 'category': 'impact', 'layers': [
            {'source': {'type': 'sample', 'library': 'p', 'file': [take, 'b']}, 'length': 0.2, 'gain_db': -3.0}]}

    def test_a_take_can_carry_its_own_gain(self):
        sound = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 2,
                                  'sounds': [self.layer({'file': 'a', 'offset': 0.1, 'gain_db': -6.0})]})['sounds'][0]
        first = variants.layer_spec(sound, 1, 0)[0]['layers'][0]
        second = variants.layer_spec(sound, 1, 1)[0]['layers'][0]
        self.assertEqual((first['source']['file'], first['gain_db']), ('a', -9.0))
        self.assertEqual((second['source']['file'], second['gain_db']), ('b', -3.0))

    def test_a_gain_out_of_range_is_refused(self):
        with self.assertRaises(project.ProjectError):
            project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [self.layer({'file': 'a', 'gain_db': 99})]})


class OneSoundAlone(unittest.TestCase):
    def test_render_can_take_some_sounds(self):
        folder = helpers.scratch('agent_one_sound')
        path = write_project(folder, [UI, TICK], flows=[{'name': 'both', 'sounds': ['ok', 'tick']}])
        proc = run('render', path, '--output', folder / 'r', '--sound', 'tick')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        resolved = json.loads((folder / 'r' / 'resolved.json').read_text(encoding='utf-8'))
        self.assertEqual([s['id'] for s in resolved['sounds']], ['tick'])
        self.assertEqual(resolved['flows'], [])
        self.assertIn('the whole project', proc.stdout)

    def test_an_unknown_sound_is_named(self):
        folder = helpers.scratch('agent_one_sound_unknown')
        proc = run('render', write_project(folder, [UI]), '--output', folder / 'r', '--sound', 'nope')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('nope', proc.stdout)

    def test_a_render_can_leave_out_the_page(self):
        folder = helpers.scratch('agent_no_page')
        proc = run('render', write_project(folder, [UI]), '--output', folder / 'r', '--no-page')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((folder / 'r' / 'audition.html').exists())
        self.assertTrue((folder / 'r' / 'check.json').is_file())
        self.assertEqual(run('audition', folder / 'r').returncode, 0)
        self.assertTrue((folder / 'r' / 'audition.html').is_file())


class LayerReport(unittest.TestCase):
    def test_each_variant_says_how_loud_each_layer_is_and_when(self):
        folder = helpers.scratch('agent_layer_report')
        sound = {'id': 'hit', 'category': 'impact', 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'pitch': 300.0, 'start': 0.1, 'length': 0.2, 'gain_db': -12.0,
             'env': [[0, 0], [0.05, 1], [0.2, 0]]},
            {'source': {'type': 'noise', 'color': 'white'}, 'length': 0.05, 'gain_db': -30.0}]}
        render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [sound]}), folder / 'r')
        resolved = json.loads((folder / 'r' / 'resolved.json').read_text(encoding='utf-8'))
        layers = resolved['sounds'][0]['variants'][0]['layers']
        self.assertEqual([l['source'] for l in layers], ['osc', 'noise'])
        self.assertAlmostEqual(layers[0]['peak_s'], 0.15, delta=0.01)
        self.assertAlmostEqual(layers[0]['peak_db'], -12.0, delta=1.0)
        self.assertLess(layers[1]['peak_db'], layers[0]['peak_db'])

    def test_a_recorded_layer_is_named(self):
        folder = helpers.scratch('agent_layer_report_rec')
        members = {'hit.wav': ts.wav_bytes(ts.tone(300, 0.3, rate=RATE, channels=1), rate=RATE),
                   'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Hit Pack', members), folder / 'library', licence='CC0-1.0')
        sound = {'id': 'hit', 'category': 'impact',
                 'layers': [{'source': {'type': 'sample', 'library': 'hit-pack', 'file': 'hit'}, 'length': 0.2}]}
        render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [sound]}), folder / 'r',
                      samples=folder / 'library')
        resolved = json.loads((folder / 'r' / 'resolved.json').read_text(encoding='utf-8'))
        self.assertEqual(resolved['sounds'][0]['variants'][0]['layers'][0]['source'], 'hit-pack/hit')


class WhatOutputSays(unittest.TestCase):
    def test_reaching_the_limiters_allowance_is_said_to_be_normal(self):
        folder = helpers.scratch('agent_limit_note')
        out = folder / 'r'
        render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [UI]}), out)
        path = out / 'resolved.json'
        resolved = json.loads(path.read_text(encoding='utf-8'))
        resolved['sounds'][0]['variants'][0]['limited_db'] = 6.0
        path.write_text(json.dumps(resolved), encoding='utf-8')
        self.assertIn('normal', run('check', out).stdout)

    def test_doctor_reports_the_sample_library(self):
        folder = helpers.scratch('agent_doctor')
        env = dict(helpers.child_env(), SFX_SKILL_LIBRARY=str(folder / 'library'))
        self.assertIn('sample library: none yet', run('doctor', '--music-skill', '', env=env).stdout)
        samples.add(ts.as_folder(folder / 'Pack', ts.pack_members()), folder / 'library', licence='CC0-1.0')
        out = run('doctor', '--music-skill', '', env=env).stdout
        self.assertIn('sample library:', out)
        self.assertIn('1 pack, 3 recordings', out)

    def test_a_fine_map_prints_times_it_can_tell_apart(self):
        folder = helpers.scratch('agent_map_hop')
        members = {'hit.wav': ts.wav_bytes(ts.tone(300, 0.1, rate=RATE, channels=1), rate=RATE),
                   'License.txt': ts.CC0_TEXT.encode('utf-8')}
        samples.add(ts.as_folder(folder / 'Hit Pack', members), folder / 'library', licence='CC0-1.0')
        out = run('samples', '--library', folder / 'library', '--map', 'hit-pack/hit', '--hop', '0.002').stdout
        self.assertIn('0.002', out)
        self.assertIn('0.004', out)

    def test_a_closed_pipe_ends_the_command_quietly(self):
        proc = subprocess.Popen([sys.executable, str(SFX), 'recipes'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=helpers.child_env())
        proc.stdout.close()
        err = proc.stderr.read().decode('utf-8', 'replace')
        proc.wait(timeout=60)
        proc.stderr.close()
        self.assertEqual(err.strip(), '')

    def test_pictures_can_be_drawn_again(self):
        folder = helpers.scratch('agent_pictures')
        out = folder / 'r'
        render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 2, 'sounds': [UI]}), out)
        self.assertEqual(run('spectrogram', out).returncode, 0)
        again = run('spectrogram', out, '--all-variants')
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(len(list((out / 'spectrograms').glob('*.png'))), 2)


class Flows(unittest.TestCase):
    def test_a_flow_can_overlap_its_sounds(self):
        data = {'schema': 'sound-effects/1', 'title': 't', 'sounds': [UI, TICK],
                'flows': [{'name': 'combo', 'sounds': ['ok', 'tick'], 'gap': -0.05}]}
        self.assertEqual(project.validate(data)['flows'][0]['gap'], -0.05)


class WhatALibraryHolds(unittest.TestCase):
    """A library an agent draws from without hearing it: adult recordings, speech and singing are screened out."""

    def test_adult_titles_and_tags_are_screened(self):
        for title, tags in (('Sex groaning 3', []), ('moan', ['erotic']), ('Woman_orgasm_02', []), ('x', ['porn-core'])):
            self.assertEqual(fsd50k.content_reasons(title, tags, ['Screaming', 'Human_voice']), ['adult content'], title)

    def test_a_voice_that_may_hold_words_is_screened(self):
        for labels in (['Human_voice'], ['Yell', 'Shout', 'Human_voice'], ['Chatter', 'Crowd'], ['Whispering', 'Human_voice'],
                       ['Speech', 'Human_voice'], ['Singing', 'Music']):
            self.assertEqual(fsd50k.content_reasons('take 3', [], labels), ['speech or singing'], labels)

    def test_a_voice_without_words_is_not(self):
        for labels in (['Screaming', 'Shout', 'Human_voice'], ['Laughter', 'Human_voice'], ['Breathing', 'Respiratory_sounds'],
                       ['Cough', 'Human_voice'], ['Crying_and_sobbing', 'Human_voice'], ['Rain']):
            self.assertEqual(fsd50k.content_reasons('take 3', [], labels), [], labels)

    def test_digital_silence_is_not_a_noise_floor(self):
        rng = np.random.default_rng(2)
        body = 0.1 * rng.standard_normal(RATE)
        padded = np.concatenate([np.zeros(RATE), body, np.zeros(RATE)])
        m = fsd50k.measure(padded, RATE)
        self.assertLess(m['range_db'], 6.0)                 # steady noise, whatever silence is put around it
        self.assertGreater(m['floor_db'], -30.0)
        self.assertIn('noisy', fsd50k.screen(m, True))

    def test_a_quiet_floor_still_counts(self):
        rng = np.random.default_rng(2)
        x = 0.0005 * rng.standard_normal(2 * RATE)
        x[RATE // 2:RATE // 2 + 19200] += 0.5 * np.sin(np.arange(19200) * 0.2)     # more than the top 5% of frames
        self.assertGreater(fsd50k.measure(x, RATE)['range_db'], 30.0)

    def test_a_word_inside_another_is_not_adult(self):
        for title in ('Essex rain', 'string sextet', 'Sussex harbour', 'Middlesex station', 'cucumber'):
            self.assertEqual(fsd50k.content_reasons(title, [], ['Rain']), [], title)

    def test_speech_and_singing_are_screened(self):
        for label in ('Speech', 'Child_speech_and_kid_speaking', 'Female_singing'):
            self.assertEqual(fsd50k.content_reasons('a take', [], [label]), ['speech or singing'], label)
        self.assertEqual(fsd50k.content_reasons('a breath', [], ['Breathing', 'Human_voice']), [])

    def test_the_screen_carries_them(self):
        measures = {'clipped_runs': 0, 'top_db': -10.0, 'peak_db': -3.0, 'range_db': 40.0}
        self.assertEqual(fsd50k.screen(measures, True, content=['adult content']), ['adult content'])

    def test_no_pack_holds_them_even_if_an_example_named_one(self):
        rows = [{'library': 'fsd50k-eval', 'file': '1', 'licence': 'CC0-1.0', 'screen': ['adult content']},
                {'library': 'fsd50k-eval', 'file': '2', 'licence': 'CC0-1.0', 'screen': ['speech or singing']},
                {'library': 'fsd50k-eval', 'file': '3', 'licence': 'CC0-1.0', 'screen': ['noisy']}]
        chosen = make_fsd50k_packs.chosen({'files': rows}, also=[('fsd50k-eval', n) for n in '123'])
        self.assertEqual([r['file'] for r in chosen], ['3'])


class Foley(unittest.TestCase):
    def test_footsteps_and_doors_have_a_category(self):
        sound = {'id': 'step', 'category': 'foley',
                 'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.1}]}
        self.assertEqual(project.validate({'schema': 'sound-effects/1', 'title': 't', 'sounds': [sound]})['sounds'][0]['category'],
                         'foley')


if __name__ == '__main__':
    unittest.main()
