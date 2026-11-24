"""sfx.py music: what a music-production project declares, and the written notes of the bars an agent names."""
import itertools
import json
import subprocess
import sys
import unittest

import helpers
from sfxlib import music, project

SFX = helpers.SCRIPTS / 'sfx.py'
_FOLDERS = itertools.count()


def folder():
    return helpers.scratch(f'music_{next(_FOLDERS)}')


def part(tid, notes, velocity=80, **fields):
    """A track holding notes (on, duration, pitch), each with its velocity."""
    ids = [f'{tid}_{i}' for i in range(len(notes))]
    return dict({'id': tid, 'role': 'melody', 'engine': 'soundfont', 'range': [36, 96],
                 'notes': [{'id': i, 'on': on, 'duration': d, 'pitch': p} for i, (on, d, p) in zip(ids, notes)],
                 'performance': {'notes': {i: {'velocity': min(127, velocity + n)} for n, i in enumerate(ids)}},
                 'mix': {'level_dbfs': -20}}, **fields)


def piece(**over):
    """Four bars of 4/4 at 120: a quarter note is 0.5 s and a bar 2 s."""
    song = {'schema': music.SCHEMA, 'title': 'A test piece',
            'clock': {'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'tail_seconds': 3},
            'sections': [{'id': 'first', 'start_bar': 0, 'bars': 2, 'intent': 'question'}, {'id': 'second', 'start_bar': 2, 'bars': 2}],
            'tracks': [part('lead', [(0, 1, 60), (3, 2.5, 64), (5, 1, 67), (7, 3, 72), (8, 1, 71)])],
            'mix': {'chain': 'room'}}
    song.update(over)
    return song


def saved(song, name='song.json'):
    path = folder() / name
    path.write_text(json.dumps(song), encoding='utf-8')
    return path


def run(*args):
    return subprocess.run([sys.executable, str(SFX), 'music', *map(str, args)], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=helpers.child_env(), timeout=120)


class TheClock(unittest.TestCase):
    def test_six_eight_with_a_tempo_change_counts_in_quarter_notes(self):
        clock = music.Clock({'bars': 8, 'meter': [6, 8], 'tempo': [[0, 120], [1, 60]]})
        self.assertEqual(clock.quarters_per_bar, 3)
        self.assertAlmostEqual(clock.seconds(clock.bar_start(5)), 11.5)
        self.assertAlmostEqual(clock.seconds(1.5) - clock.seconds(0.5), 0.75)
        self.assertAlmostEqual(clock.end, 23.5)

    def test_a_tempo_is_held_as_midi_holds_it(self):
        clock = music.Clock({'bars': 1, 'meter': [4, 4], 'tempo': [[0, 97]]})
        self.assertEqual(clock.spb[0], round(60_000_000 / 97) / 1_000_000)
        self.assertEqual(clock.seconds(1), clock.spb[0])

    def test_bars_are_counted_from_one(self):
        clock = music.Clock({'bars': 4, 'meter': [7, 8], 'tempo': [[0, 120]]})
        self.assertEqual(clock.quarters_per_bar, 3.5)
        self.assertEqual(clock.bar_of(0), 1)
        self.assertEqual(clock.bar_of(3.5), 2)
        self.assertEqual(clock.bar_of(3.5 - 1e-9), 2)        # on the line, as a sum of thirds may land
        self.assertEqual(clock.bar_of(3.4), 1)
        self.assertEqual(clock.place(4.5), (2, 2.0))

    def test_a_clock_that_cannot_be_read_is_named(self):
        for clock, word in (({'bars': 0, 'meter': [4, 4], 'tempo': [[0, 120]]}, 'bars'),
                            ({'bars': True, 'meter': [4, 4], 'tempo': [[0, 120]]}, 'bars'),
                            ({'bars': 1, 'meter': [4, 3], 'tempo': [[0, 120]]}, 'meter'),
                            ({'bars': 1, 'meter': [4], 'tempo': [[0, 120]]}, 'meter'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': []}, 'tempo'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': [[1, 120]]}, 'tempo'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': [[0, 120], [0, 60]]}, 'tempo'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': [[0, 0]]}, 'tempo'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': [[0, float('nan')]]}, 'tempo'),
                            ({'bars': 1, 'meter': [4, 4], 'tempo': [[0, 120]], 'tail_seconds': 'x'}, 'tail'),
                            ('clock', 'clock')):
            with self.subTest(clock=clock), self.assertRaisesRegex(music.MusicError, word):
                music.Clock(clock)


class Reading(unittest.TestCase):
    def test_only_a_music_production_project_is_read(self):
        for text, word in (('{"schema": "sfx-project/1"}', 'not a music-production project'), ('[]', 'not a music-production project'),
                           ('{nope', 'JSON'), ('[' * 200000, 'JSON')):
            path = folder() / 'song.json'
            path.write_text(text, encoding='utf-8')
            with self.subTest(text=text[:20]), self.assertRaisesRegex(music.MusicError, word):
                music.load(path)
        with self.assertRaisesRegex(music.MusicError, 'cannot read'):
            music.load(folder() / 'none.json')

    def test_a_note_that_cannot_be_read_names_its_track(self):
        for field, value in (('on', 'x'), ('on', -1), ('duration', 0), ('pitch', 128), ('pitch', 60.5), ('pitch', True)):
            song = piece()
            song['tracks'][0]['notes'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(music.MusicError, 'lead'):
                music.describe(song)

    def test_sections_are_printed_from_bar_one_with_their_times(self):
        info = music.describe(piece())
        self.assertEqual(info['sections'][0], {'id': 'first', 'bars': [1, 2], 'start_s': 0.0, 'end_s': 4.0, 'intent': 'question'})
        self.assertEqual(info['sections'][1]['bars'], [3, 4])
        self.assertEqual(info['sections'][1]['start_s'], 4.0)

    def test_the_score_and_the_render_are_told_apart(self):
        info = music.describe(piece())
        self.assertEqual((info['score_seconds'], info['tail_seconds'], info['render_seconds']), (8.0, 3.0, 11.0))
        text = '\n'.join(music.overview(info))
        self.assertIn('Score: 8 s', text)
        self.assertIn('11 s', text)

    def test_a_tempo_change_is_placed_by_bar_and_beat(self):
        clock = {'bars': 4, 'meter': [6, 8], 'tempo': [[0, 120], [4, 60]]}
        info = music.describe(piece(clock=clock))
        self.assertEqual(info['tempo'], [{'bar': 1, 'beat': 1.0, 'bpm': 120.0}, {'bar': 2, 'beat': 2.0, 'bpm': 60.0}])
        self.assertIn('60 at bar 2 beat 2', '\n'.join(music.overview(info)))

    def test_rubato_is_said(self):
        clock = {'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'rubato': {'final_ratio': 0.6, 'final_beats': 4}}
        info = music.describe(piece(clock=clock))
        self.assertIn('rubato', '\n'.join(music.overview(info)))
        self.assertIsNone(info['render_seconds'])                  # the render's length is not worked out, so none is claimed
        self.assertNotIn('rubato', '\n'.join(music.overview(music.describe(piece()))))

    def test_no_declared_harmony_is_said_and_no_key_is_guessed(self):
        info = music.describe(piece())
        self.assertIsNone(info['harmony'])
        text = '\n'.join(music.overview(info))
        self.assertIn('Harmony: none declared', text)
        self.assertNotIn('key', text.lower().replace('no key is worked out', ''))

    def test_a_transposed_chord_is_shown_beside_its_symbol(self):
        harmony = [{'bar': 2, 'symbol': 'G'}, {'bar': 1, 'beat': 2, 'symbol': 'C/E', 'transpose_semitones': 2}, {'bar': 1, 'symbol': 'F#m7/A'}]
        info = music.describe(piece(harmony=harmony))
        self.assertEqual([(h['bar'], h['beat'], h['symbol']) for h in info['harmony']], [(1, 1, 'F#m7/A'), (1, 3, 'C/E'), (2, 1, 'G')])
        self.assertEqual(info['harmony'][1]['sounding'], 'D/F#')
        text = '\n'.join(music.overview(info))
        self.assertIn('C/E (+2: D/F#)', text)
        self.assertIn('bar 1 beat 3', text)

    def test_chord_symbols_move_by_root_and_bass(self):
        for symbol, shift, want in (('C/E', 2, 'D/F#'), ('Am7', -2, 'Gm7'), ('Bb', 1, 'B'), ('C6/9', 2, 'D6/9'), ('C6/9/E', 2, 'D6/9/F#'), ('Cmaj7', 12, 'Cmaj7'),
                                    ('B' + chr(0x266D) + 'm', 2, 'Cm'), ('N.C.', 2, None)):
            with self.subTest(symbol=symbol):
                self.assertEqual(music.sounding(symbol, shift), want)

    def test_older_free_form_harmony_is_printed_as_it_is(self):
        for written in ({'plan': 'C to G, then home'}, ['C', 'G'], 'C then G'):
            info = music.describe(piece(harmony=written))
            self.assertEqual(info['harmony'], {'as_written': written})
            self.assertIn('as written', '\n'.join(music.overview(info)))

    def test_a_declared_plan_is_printed(self):
        plan = {'identity': 'a slow rising line', 'high_point': {'section': 'second'}}
        info = music.describe(piece(plan=plan))
        self.assertEqual(info['plan'], plan)
        self.assertIn('a slow rising line', '\n'.join(music.overview(info)))

    def test_a_plan_of_any_shape_is_printed_entry_by_entry(self):
        plan = {'identity': 'a slow line', 'sections': [{'section': 'first', 'leads': 'flute'}, {'section': 'second'}], 'note': 7}
        lines = music.overview(music.describe(piece(plan=plan)))
        self.assertIn('  identity: a slow line', lines)
        self.assertIn('    {"section": "first", "leads": "flute"}', lines)
        self.assertIn('  note: 7', lines)
        self.assertIn('some words', '\n'.join(music.overview(music.describe(piece(plan='some words')))))

    def test_a_chord_is_never_broken_across_lines(self):
        harmony = [{'bar': bar, 'beat': 1.5, 'symbol': 'Cmaj7/E'} for bar in range(1, 5)] * 8
        lines = [line for line in music.overview(music.describe(piece(harmony=harmony))) if line.startswith('  bar ')]
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertRegex(line, r'^  bar [0-9]+ beat 2.5: Cmaj7/E(; bar [0-9]+ beat 2.5: Cmaj7/E)*$')

    def test_a_track_says_what_it_holds(self):
        row = music.describe(piece())['tracks'][0]
        self.assertEqual((row['id'], row['role'], row['range'], row['notes'], row['first_bar'], row['last_bar']), ('lead', 'melody', [36, 96], 5, 1, 3))
        self.assertEqual((row['lowest'], row['highest']), (60, 72))
        self.assertEqual(row['pitch_classes'], ['C', 'E', 'G', 'B'])
        self.assertIn('C E G B', '\n'.join(music.overview(music.describe(piece()))))

    def test_drums_and_noise_have_no_pitch_classes(self):
        hits = [(0, 0.5, 36), (1, 0.5, 38)]
        for fields in ({'drums': True}, {'engine': 'drum_synth'}, {'engine': 'noise'}):
            row = music.describe(piece(tracks=[part('kit', hits, **fields)]))['tracks'][0]
            with self.subTest(fields=fields):
                self.assertEqual(row['notes'], 2)
                self.assertNotIn('pitch_classes', row)
                self.assertNotIn('lowest', row)

    def test_a_part_without_notes_is_listed(self):
        row = music.describe(piece(tracks=[part('rest', [])]))['tracks'][0]
        self.assertEqual(row['notes'], 0)
        self.assertNotIn('first_bar', row)

    def test_project_text_stays_on_its_line(self):
        song = piece(title='Line one\nLine two' + chr(0x202E) + chr(7))
        song['sections'][0]['intent'] = 'a\r\nb' + chr(0x2028) + 'c'
        lines = music.overview(music.describe(song))
        self.assertEqual(lines[0].count('Line one Line two'), 1)
        self.assertTrue(any('(a b c)' in line for line in lines))
        for line in lines:
            self.assertFalse(set(line) & {'\n', '\r', chr(0x202E), chr(7), chr(0x2028)}, line)


class Quoting(unittest.TestCase):
    def test_time_zero_is_the_start_of_the_first_bar_and_rests_stay(self):
        q = music.quote(piece(), 'lead', 2, 2)
        self.assertEqual(q['notes'], [[0.5, 67, 0.5, 82], [1.5, 72, 0.5, 83]])
        self.assertEqual((q['start_s'], q['length_s']), (2.0, 2.0))

    def test_a_note_past_the_end_is_cut_there_and_counted(self):
        q = music.quote(piece(), 'lead', 2, 2)
        self.assertEqual(q['cut'], 1)
        self.assertEqual(music.quote(piece(), 'lead', 2, 3)['cut'], 0)
        self.assertEqual(music.quote(piece(), 'lead', 2, 3)['notes'][1], [1.5, 72, 1.5, 83])
        self.assertIn('1 note ran past the end of bar 2', '\n'.join(music.quote_lines(q)))

    def test_a_note_begun_earlier_is_counted_and_left_out(self):
        q = music.quote(piece(), 'lead', 2, 2)
        self.assertEqual(q['rang_in'], 1)
        self.assertNotIn(64, [n[1] for n in q['notes']])
        self.assertEqual(music.quote(piece(), 'lead', 1, 1)['rang_in'], 0)
        self.assertIn('still sound', '\n'.join(music.quote_lines(q)))

    def test_a_note_ending_on_the_line_is_neither_cut_nor_ringing(self):
        song = piece(tracks=[part('lead', [(3, 1, 60), (4, 4, 62)])])
        q = music.quote(song, 'lead', 2, 2)
        self.assertEqual((q['cut'], q['rang_in']), (0, 0))
        self.assertEqual(q['notes'], [[0.0, 62, 2.0, 81]])

    def test_a_note_a_hair_before_the_line_belongs_to_the_bar(self):
        song = piece(tracks=[part('lead', [(4 - 1e-9, 1, 60), (8 - 1e-9, 1, 62)])])
        q = music.quote(song, 'lead', 2, 2)
        self.assertEqual([n[1] for n in q['notes']], [60])
        self.assertEqual(q['notes'][0][0], 0.0)

    def test_times_follow_the_tempo_map(self):
        song = piece(clock={'bars': 8, 'meter': [6, 8], 'tempo': [[0, 120], [1, 60]]}, tracks=[part('lead', [(0.5, 1, 60), (12, 1.5, 62)])],
                     sections=[{'id': 'all', 'start_bar': 0, 'bars': 8}])
        self.assertEqual(music.quote(song, 'lead', 1, 1)['notes'], [[0.5 * 0.5, 60, 0.75, 80]])
        q = music.quote(song, 'lead', 5, 5)
        self.assertEqual((q['start_s'], q['notes']), (11.5, [[0.0, 62, 1.5, 81]]))

    def test_velocity_comes_from_the_performance(self):
        song = piece()
        song['tracks'][0]['performance']['notes']['lead_2']['velocity'] = 111
        self.assertEqual(music.quote(song, 'lead', 2, 2)['notes'][0][3], 111)
        del song['tracks'][0]['performance']['notes']['lead_2']
        with self.assertRaisesRegex(music.MusicError, 'lead_2'):
            music.quote(song, 'lead', 2, 2)

    def test_what_the_render_also_plays_is_named(self):
        cases = [('swing', {'swing': {'ratio': 0.62}}, None), ('offset', {'offset_ms': -12}, None), ('bend', {'bend': [[0, 0], [1, 1]]}, None),
                 ('timing', None, {'timing_ms': 8}), ('gate', None, {'gate': 0.8})]
        for word, performance, note in cases:
            song = piece()
            song['tracks'][0]['performance'].update(performance or {})
            song['tracks'][0]['performance']['notes']['lead_2'].update(note or {})
            q = music.quote(song, 'lead', 2, 2)
            with self.subTest(word=word):
                self.assertTrue(any(word in item for item in q['leaves_out']), q['leaves_out'])
                self.assertIn('written score', '\n'.join(music.quote_lines(q)))

    def test_the_piece_s_rubato_is_named(self):
        q = music.quote(piece(clock={'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'rubato': {'final_ratio': 0.6, 'final_beats': 4}}), 'lead', 2, 2)
        self.assertTrue(any('rubato' in item for item in q['leaves_out']))

    def test_a_plain_part_claims_nothing_left_out(self):
        song = piece()
        song['tracks'][0]['performance']['swing'] = {'ratio': 0.5}
        song['tracks'][0]['performance']['offset_ms'] = 0
        song['tracks'][0]['performance']['notes']['lead_2'].update({'timing_ms': 0, 'gate': 1})
        q = music.quote(song, 'lead', 2, 2)
        self.assertEqual(q['leaves_out'], [])
        self.assertNotIn('written score', '\n'.join(music.quote_lines(q)))

    def test_per_note_expression_outside_the_bars_is_not_named(self):
        song = piece()
        song['tracks'][0]['performance']['notes']['lead_0'].update({'timing_ms': 9, 'gate': 0.5})
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])

    def test_a_key_map_on_a_quoted_note_is_named(self):
        song = piece()
        song['tracks'][0]['key_map'] = {'67': 79}
        self.assertTrue(any('key map' in item for item in music.quote(song, 'lead', 2, 2)['leaves_out']))
        song['tracks'][0]['key_map'] = {'60': 72}
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])

    def test_a_drum_part_says_its_pitches_are_keys(self):
        q = music.quote(piece(tracks=[part('kit', [(0, 0.5, 36)], drums=True)]), 'kit', 1, 1)
        self.assertTrue(q['drums'])
        self.assertIn('drum', '\n'.join(music.quote_lines(q)))
        self.assertFalse(music.quote(piece(), 'lead', 2, 2)['drums'])

    def test_more_than_a_layer_holds_asks_for_less(self):
        full = part('lead', [(i * 0.25, 0.25, 60) for i in range(65)])
        song = piece(clock={'bars': 5, 'meter': [4, 4], 'tempo': [[0, 120]]}, tracks=[full], sections=[{'id': 'all', 'start_bar': 0, 'bars': 5}])
        with self.assertRaisesRegex(music.MusicError, '65 notes.*64'):
            music.quote(song, 'lead', 1, 5)
        self.assertEqual(len(music.quote(song, 'lead', 1, 4)['notes']), 64)

    def test_longer_than_a_layer_holds_asks_for_less(self):
        song = piece(clock={'bars': 16, 'meter': [4, 4], 'tempo': [[0, 120]]}, sections=[{'id': 'all', 'start_bar': 0, 'bars': 16}])
        self.assertEqual(music.quote(song, 'lead', 1, 15)['length_s'], 30.0)
        with self.assertRaisesRegex(music.MusicError, '32.0 s.*30 s'):
            music.quote(song, 'lead', 1, 16)

    def test_bars_outside_the_piece_and_unknown_tracks_are_refused(self):
        with self.assertRaisesRegex(music.MusicError, 'bars 1 to 4'):
            music.quote(piece(), 'lead', 3, 5)
        with self.assertRaisesRegex(music.MusicError, 'lead'):
            music.quote(piece(), 'nope', 1, 1)

    def test_bars_with_no_notes_say_where_the_part_plays(self):
        with self.assertRaisesRegex(music.MusicError, 'no notes in bar 4.*bars 1 to 3'):
            music.quote(piece(), 'lead', 4, 4)
        with self.assertRaisesRegex(music.MusicError, 'no notes'):
            music.quote(piece(tracks=[part('lead', [])]), 'lead', 1, 1)

    def test_bars_are_read_from_text(self):
        for text, want in (('5', (5, 5)), ('5-6', (5, 6)), ('12-12', (12, 12))):
            self.assertEqual(music.parse_bars(text), want)
        for text in ('', '6-5', '0-3', 'a', '1-2-3', '5-', '-5', '١-٢', '1.5'):
            with self.subTest(text=text), self.assertRaises(music.MusicError):
                music.parse_bars(text)

    def test_quoted_notes_are_a_valid_layer(self):
        notes = music.quote(piece(), 'lead', 1, 3)['notes']
        for source in ({'type': 'instrument', 'notes': notes}, {'type': 'surge', 'patch': 'factory/fx/fireworks', 'notes': notes}):
            with self.subTest(type=source['type']):
                project.validate({'schema': 'sound-effects/1', 'title': 'quoted', 'seed': 1, 'sounds': [
                    {'id': 'theme', 'category': 'magic', 'layers': [{'source': source, 'length': 7.0}]}]})

    def test_the_shortest_written_note_keeps_a_length(self):
        song = piece(clock={'bars': 1, 'meter': [4, 4], 'tempo': [[0, 300]]}, tracks=[part('lead', [(1, 1 / 480, 60)])],
                     sections=[{'id': 'all', 'start_bar': 0, 'bars': 1}])
        self.assertGreater(music.quote(song, 'lead', 1, 1)['notes'][0][2], 0)


class Command(unittest.TestCase):
    def test_the_overview(self):
        proc = run(saved(piece()))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for word in ('A test piece', '4/4', 'first', 'bars 1-2', 'lead'):
            self.assertIn(word, proc.stdout)

    def test_the_overview_as_json(self):
        proc = run(saved(piece()), '--json')
        self.assertEqual(json.loads(proc.stdout)['tracks'][0]['id'], 'lead')

    def test_a_quote_prints_notes_a_layer_takes(self):
        proc = run(saved(piece()), '--track', 'lead', '--bars', '2')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = next(l for l in proc.stdout.splitlines() if l.startswith('"notes"'))
        self.assertEqual(json.loads('{' + line + '}')['notes'], [[0.5, 67, 0.5, 82], [1.5, 72, 0.5, 83]])

    def test_a_quote_as_json(self):
        proc = run(saved(piece()), '--track', 'lead', '--bars', '2-3', '--json')
        self.assertEqual(json.loads(proc.stdout)['bars'], [2, 3])

    def test_track_and_bars_go_together(self):
        for args in (('--track', 'lead'), ('--bars', '2')):
            proc = run(saved(piece()), *args)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('--track', proc.stdout)

    def test_a_refusal_is_one_line_and_no_traceback(self):
        path = folder() / 'not.json'
        path.write_text('{"schema": 1}', encoding='utf-8')
        for args in ((path,), (saved(piece()), '--track', 'lead', '--bars', '9'), (path.with_name('none.json'),)):
            proc = run(*args)
            with self.subTest(args=args):
                self.assertEqual(proc.returncode, 1)
                self.assertTrue(proc.stdout.startswith('music: '), proc.stdout)
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
