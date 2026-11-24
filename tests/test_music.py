"""sfx.py music: what a music-production project declares, and the written notes of the bars an agent names."""
import itertools
import json
import subprocess
import sys
import unittest
from unittest import mock

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

    def test_rubato_that_slows_nothing_is_not_said(self):
        one_section = [{'id': 'all', 'start_bar': 0, 'bars': 4}]
        for rubato in ({'phrase_ratio': 0.9, 'phrase_beats': 1}, {'final_ratio': 0.6}, {'final_ratio': 1, 'final_beats': 4}):
            info = music.describe(piece(sections=one_section, clock={'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'tail_seconds': 3, 'rubato': rubato}))
            with self.subTest(rubato=rubato):
                self.assertNotIn('rubato', '\n'.join(music.overview(info)))     # no boundary to ease into, no final beats, no slowing
                self.assertEqual(info['render_seconds'], 11.0)
        info = music.describe(piece(clock={'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'rubato': {'phrase_ratio': 0.9, 'phrase_beats': 1}}))
        self.assertIn('rubato', '\n'.join(music.overview(info)))                # two sections: it eases into the second

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

    def test_a_part_on_a_kit_s_bank_has_no_pitch_classes(self):
        for fields in ({'bank': 128}, {'patch': {'bank': 128, 'program': 0}}):
            song = piece(tracks=[part('kit', [(0, 0.5, 36)], **fields)])
            with self.subTest(fields=fields):
                self.assertNotIn('pitch_classes', music.describe(song)['tracks'][0])
                self.assertTrue(music.quote(song, 'kit', 1, 1)['drums'])
        pad = piece(tracks=[part('pad', [(0, 0.5, 60)], engine='synth', bank=128)])      # a synthesizer's bank says nothing of its sound
        self.assertIn('pitch_classes', music.describe(pad)['tracks'][0])
        melodic = piece(tracks=[part('keys', [(0, 0.5, 60)], bank=128, patch={'bank': 0, 'program': 4})])
        self.assertIn('pitch_classes', music.describe(melodic)['tracks'][0])

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

    def test_a_tempo_change_at_the_very_end_is_placed_there(self):
        info = music.describe(piece(clock={'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120], [16, 60]]}))
        self.assertEqual((info['tempo'][1]['bar'], info['tempo'][1]['beat']), (5, 1.0))
        self.assertIn('60 at the end', '\n'.join(music.overview(info)))

    def test_project_text_stays_on_its_line(self):
        song = piece(title='Line one\nLine two' + chr(0x202E) + chr(7))
        song['sections'][0]['intent'] = 'a\r\nb' + chr(0x2028) + 'c'
        lines = music.overview(music.describe(song))
        self.assertEqual(lines[0].count('Line one Line two'), 1)
        self.assertTrue(any('(a b c)' in line for line in lines))
        for line in lines:
            self.assertFalse(set(line) & {'\n', '\r', chr(0x202E), chr(7), chr(0x2028)}, line)


class HostileFiles(unittest.TestCase):
    def test_only_a_file_is_read(self):
        with self.assertRaisesRegex(music.MusicError, 'not a file'):
            music.load(folder())

    def test_no_more_is_read_than_a_project_holds(self):
        path = saved(piece())
        with mock.patch.object(music, 'MAX_FILE_BYTES', 50), self.assertRaisesRegex(music.MusicError, 'more than'):
            music.load(path)

    def test_a_number_too_large_for_a_float_is_refused_not_a_crash(self):
        song = piece()
        song['tracks'][0]['notes'][0]['on'] = 10 ** 400
        with self.assertRaisesRegex(music.MusicError, 'lead'):
            music.describe(song)
        with self.assertRaisesRegex(music.MusicError, 'tempo'):
            music.Clock({'bars': 4, 'meter': [4, 4], 'tempo': [[0, 10 ** 400]]})

    def test_a_track_whose_id_is_not_a_track_id_is_refused(self):
        for bad in (['lead2'], 'L' * 3, 'a' * 49, 7):
            song = piece()
            song['tracks'].append(part('other', [(0, 1, 60)]))
            song['tracks'][-1]['id'] = bad
            with self.subTest(bad=bad), self.assertRaises(music.MusicError):
                music.describe(song)
            self.assertEqual(len(music.quote(song, 'lead', 2, 2)['notes']), 2)      # the part asked for still reads

    def test_a_tiny_file_is_read_without_a_large_buffer(self):
        import tracemalloc
        path = saved(piece())
        tracemalloc.start()
        music.load(path)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertLess(peak, 8 * 2 ** 20)

    def test_a_file_too_large_is_refused_before_its_pieces_are_joined(self):
        import tracemalloc
        path = folder() / 'large.json'
        path.write_bytes(b' ' * (4 * 2 ** 20 + 1))
        with mock.patch.object(music, 'MAX_FILE_BYTES', 4 * 2 ** 20), mock.patch.object(music, 'CHUNK', 2 ** 16):
            tracemalloc.start()
            try:
                with self.assertRaisesRegex(music.MusicError, 'more than'):
                    music.load(path)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 6 * 2 ** 20)                  # what was read, and not a joined copy of it as well

    def test_more_tracks_or_notes_than_a_project_holds_are_refused(self):
        many = piece(tracks=[part(f't{i}', [(0, 1, 60)]) for i in range(65)])
        with self.assertRaisesRegex(music.MusicError, '64'):
            music.load(saved(many))
        with mock.patch.object(music, 'MAX_NOTES_IN_PROJECT', 4), self.assertRaisesRegex(music.MusicError, 'notes'):
            music.load(saved(piece()))

    def test_positions_outside_the_score_are_refused_not_a_crash(self):
        song = piece(harmony=[{'bar': 1, 'beat': 10 ** 400, 'symbol': 'C'}])
        self.assertEqual(music.describe(song)['harmony'], {'as_written': song['harmony']})
        song = piece(clock={'bars': 4, 'meter': [1, 16], 'tempo': [[0, 120]]}, sections=[{'id': 'all', 'start_bar': 0, 'bars': 4}])
        song['tracks'][0]['notes'] = [{'id': 'lead_0', 'on': 1.7e308, 'duration': 1, 'pitch': 60}]
        with self.assertRaisesRegex(music.MusicError, 'lead'):
            music.describe(song)
        with self.assertRaisesRegex(music.MusicError, 'tempo'):
            music.Clock({'bars': 1, 'meter': [4, 4], 'tempo': [[0, 120], [9, 60]]})

    def test_a_quote_of_a_part_with_odd_fields_is_not_a_crash(self):
        song = piece()
        song['tracks'][0]['engine'] = {'name': 'soundfont'}
        song['tracks'][0]['performance']['cc'] = [[0, 1, 10 ** 400], [0, 7, 90]]
        song['tracks'][0]['performance']['bend'] = [[0, 10 ** 400]]
        song['tracks'][0]['performance']['legato'] = {'gap_ms': 10 ** 400}
        song['tracks'][0]['performance']['notes']['lead_2']['timing_ms'] = 10 ** 400
        self.assertIsInstance(music.quote(song, 'lead', 2, 2)['leaves_out'], list)
        self.assertTrue(music.overview(music.describe(song)))

    def test_a_note_id_too_long_for_the_schema_is_refused(self):
        song = piece()
        song['tracks'][0]['notes'][0]['id'] = 'n' * 121
        with self.assertRaisesRegex(music.MusicError, 'id'):
            music.describe(song)


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
        line = '\n'.join(music.quote_lines(q))
        self.assertIn('written to run across the start of bar 2', line)          # as written: its gate may end it sooner
        self.assertNotIn('still sound', line)

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
        cases = [('offset', {'offset_ms': -12}, None), ('bend', {'bend': [[0, 0], [1, 1]]}, None),
                 ('timing', None, {'timing_ms': 8}), ('gate', None, {'gate': 0.8})]
        for word, performance, note in cases:
            song = piece()
            song['tracks'][0]['performance'].update(performance or {})
            song['tracks'][0]['performance']['notes']['lead_2'].update(note or {})
            q = music.quote(song, 'lead', 2, 2)
            with self.subTest(word=word):
                self.assertTrue(any(word in item for item in q['leaves_out']), q['leaves_out'])
                self.assertIn('written score', '\n'.join(music.quote_lines(q)))

    def test_swing_is_named_only_where_it_moves_a_quoted_note(self):
        song = piece(tracks=[part('lead', [(4, 1, 60), (5, 1, 62), (6, 0.5, 64), (6.5, 0.5, 65)])])
        song['tracks'][0]['performance']['swing'] = {'ratio': 0.62}
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's swing"])          # the off-beat eighth is moved
        song['tracks'][0]['notes'] = song['tracks'][0]['notes'][:2]
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])                           # quarter notes on the beat are not
        song['tracks'][0]['performance']['swing'] = {'ratio': 0.62, 'unit': 1e-320}                     # no unit music-production takes
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])

    def test_a_sustain_pedal_is_named(self):
        song = piece()
        song['tracks'][0]['performance']['cc'] = [[0, 64, 127], [7.9, 64, 0]]
        self.assertTrue(any('sustain' in item for item in music.quote(song, 'lead', 2, 2)['leaves_out']))
        song['tracks'][0]['performance']['cc'] = [[1, 64, 0]]                    # a pedal never pressed
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])

    def test_a_reversed_part_names_only_what_its_render_does(self):
        song = piece()
        track = song['tracks'][0]
        track['reverse'] = True
        track['performance'].update(offset_ms=-20, bend=[[0, 1]], swing={'ratio': 0.6})
        track['performance']['notes']['lead_2'].update(gate=0.5, timing_ms=9)
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ['each note played backwards'])
        song['clock']['rubato'] = {'final_ratio': 0.6, 'final_beats': 16}
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["the piece's rubato", 'each note played backwards'])
        track['key_map'] = {'67': 79}
        self.assertIn("this part's key map", music.quote(song, 'lead', 2, 2)['leaves_out'])

    def test_a_mod_wheel_and_other_controllers_are_named(self):
        song = piece()
        song['tracks'][0]['performance']['cc'] = [[0, 1, 90]]
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's mod wheel"])
        song['tracks'][0]['performance']['cc'] = [[0, 11, 90]]
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's controller CC11"])

    def test_controllers_are_named_only_where_the_engine_hears_them_in_the_bars(self):
        song = piece()
        lead = song['tracks'][0]
        lead.update(engine='surge', surge={'preset': 'factory/pads/bright'})
        lead['performance']['cc'] = [[0, 74, 100], [8, 1, 90]]          # brightness in bar 1, which Surge XT is not sent; the wheel in bar 3
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], [])
        self.assertEqual(music.quote(song, 'lead', 3, 3)['leaves_out'], ["this part's mod wheel"])

    def test_a_pedal_or_wheel_held_into_the_bars_is_named_and_one_let_go_before_them_is_not(self):
        song = piece()
        song['tracks'][0]['performance']['cc'] = [[0, 64, 127], [0, 1, 50]]
        self.assertEqual(music.quote(song, 'lead', 3, 3)['leaves_out'], ["this part's sustain pedal", "this part's mod wheel"])
        song['tracks'][0]['performance']['cc'] = [[0, 64, 127], [3, 64, 0], [0, 1, 50], [3, 1, 0]]
        self.assertEqual(music.quote(song, 'lead', 3, 3)['leaves_out'], [])

    def test_each_engine_names_the_controllers_it_plays(self):
        song = piece()
        lead = song['tracks'][0]
        lead['performance']['cc'] = [[4, 11, 60], [4, 74, 66], [4, 7, 90], [4, 1, 32]]
        lead['engine'] = 'additive'                     # expression, brightness and the wheel as vibrato, here 74 and 1 at their rest
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's controller CC11"])
        lead['performance']['cc'].append([6, 1, 0])
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's mod wheel", "this part's controller CC11"])
        lead['engine'] = 'synth'                        # none
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])
        lead['engine'] = 'soundfont'                    # any its preset answers
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's mod wheel", "this part's controllers CC7, CC11 and CC74"])
        lead['performance']['cc'] = [[8, 7, 90]]        # set only after the bars
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], [])

    def test_a_ramp_is_read_between_its_points(self):
        song = piece()
        lead = song['tracks'][0]
        lead.update(engine='additive')
        lead['performance']['cc'] = [[0, 11, 100], [16, 11, 40]]      # 100 is its rest: the ramp leaves it at once
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], ["this part's controller CC11"])
        lead['performance']['cc_mode'] = 'step'                      # held at 100 until beat 16
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], [])

    def test_a_bend_is_named_only_where_it_reaches_the_bars(self):
        song = piece()
        song['tracks'][0]['performance']['bend'] = [[8, 0], [8.5, 1], [9, 0]]
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], [])
        self.assertEqual(music.quote(song, 'lead', 3, 3)['leaves_out'], ["this part's pitch bend"])
        song['tracks'][0]['performance']['bend'] = [[2, 1]]                 # jumps there and stays
        self.assertEqual(music.quote(song, 'lead', 3, 3)['leaves_out'], ["this part's pitch bend"])

    def test_a_slur_that_joins_quoted_notes_is_named_and_the_written_notes_stay(self):
        song = piece(tracks=[part('lead', [(0, 1, 60), (1, 1, 62), (3, 1, 70)])])
        song['tracks'][0]['performance'].update(legato={'max_interval': 5, 'glide_ms': 25, 'gap_ms': 40, 'max_notes': 4}, bend_range=5)
        q = music.quote(song, 'lead', 1, 1)
        self.assertEqual([n[1] for n in q['notes']], [60, 62, 70])
        self.assertEqual(q['leaves_out'], ["this part's slurs (1 of these notes glides on from the note before it, with no attack of its own)"])
        song['tracks'][0]['performance']['legato']['mode'] = 'crossfade'
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], ["this part's crossfade legato"])
        del song['tracks'][0]['performance']['legato']['mode']
        song['tracks'][0]['performance']['notes']['lead_0']['gate'] = 0.5          # played short, the two no longer meet
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], ['per-note gate'])
        song['tracks'][0]['engine'] = 'synth'                                       # legato is a SoundFont part's
        del song['tracks'][0]['performance']['notes']['lead_0']['gate']
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], [])

    def test_a_slur_is_grouped_as_the_part_is_played(self):
        song = piece(tracks=[part('lead', [(0, 1, 60), (1, 1, 62), (2, 1, 67)])])
        perf = song['tracks'][0]['performance']
        perf.update(legato={}, bend_range=5)                                        # an empty legato is no legato
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], [])
        perf['legato'] = {'max_interval': 5}
        self.assertTrue(any('slurs (1 of' in item for item in music.quote(song, 'lead', 1, 1)['leaves_out']))
        song['tracks'][0]['key_map'] = {'62': 74}                                   # played on 74: too far to join 60
        self.assertEqual(music.quote(song, 'lead', 1, 1)['leaves_out'], ["this part's key map"])
        swung = piece(tracks=[part('lead', [(0.5, 0.4, 60), (1, 0.5, 62)])])        # 50 ms apart as written
        swung['tracks'][0]['performance'].update(legato={'max_interval': 5}, bend_range=5)
        self.assertEqual(music.quote(swung, 'lead', 1, 1)['leaves_out'], [])
        swung['tracks'][0]['performance']['swing'] = {'ratio': 0.66}                 # swung, 34 ms: joined
        self.assertEqual(music.quote(swung, 'lead', 1, 1)['leaves_out'][-1], "this part's slurs (1 of these notes glides on from the note before it, with no attack of its own)")

    def test_a_drum_machine_or_noise_part_names_no_bend_and_a_drum_machine_no_gate(self):
        for engine, gate_named in (('drum_synth', False), ('noise', True)):
            song = piece(tracks=[part('kit', [(4, 0.5, 36), (5, 0.5, 38)], engine=engine)])
            song['tracks'][0]['performance']['bend'] = [[4, 1]]
            song['tracks'][0]['performance']['notes']['kit_0']['gate'] = 0.5
            with self.subTest(engine=engine):
                self.assertEqual(music.quote(song, 'kit', 2, 2)['leaves_out'], ['per-note gate'] if gate_named else [])

    def test_a_dynamics_curve_is_named(self):
        song = piece()
        song['tracks'][0]['performance']['dynamics'] = [[0, 40], [16, 100]]
        self.assertEqual(music.quote(song, 'lead', 2, 2)['leaves_out'], ["this part's dynamics curve, which sets its loudness in place of velocity"])

    def test_the_piece_s_rubato_is_named_where_it_slows_the_bars(self):
        slowed = lambda beats: piece(clock={'bars': 4, 'meter': [4, 4], 'tempo': [[0, 120]], 'rubato': {'final_ratio': 0.6, 'final_beats': beats}})
        self.assertIn("the piece's rubato", music.quote(slowed(10), 'lead', 2, 2)['leaves_out'])      # the last 10 beats reach bar 2
        self.assertNotIn("the piece's rubato", music.quote(slowed(4), 'lead', 2, 2)['leaves_out'])    # the last 4 do not

    def test_a_slur_under_the_piece_s_rubato_is_said_to_be_as_written(self):
        song = piece(tracks=[part('lead', [(14.5, 0.4375, 60), (15, 0.5, 62)])])                      # 31 ms apart as written
        song['tracks'][0]['performance'].update(legato={'max_interval': 5}, bend_range=5)
        self.assertEqual(music.quote(song, 'lead', 4, 4)['leaves_out'],
                         ["this part's slurs (1 of these notes glides on from the note before it, with no attack of its own)"])
        song['clock']['rubato'] = {'final_ratio': 0.6, 'final_beats': 4}                                  # slowed, they part
        said = music.quote(song, 'lead', 4, 4)['leaves_out']
        self.assertEqual(said[0], "the piece's rubato")
        self.assertEqual(said[-1], "this part's slurs (as written, 1 of these notes glides on from the note before it, with no attack of its "
                                   "own; the piece's rubato can change that)")

    def test_a_noise_part_says_its_articulations_choose_its_sounds(self):
        line = '\n'.join(music.quote_lines(music.quote(piece(tracks=[part('fx', [(0, 0.5, 48)], engine='noise')]), 'fx', 1, 1)))
        self.assertIn("articulation", line)
        self.assertNotIn('keys of its kit', line)
        kit = '\n'.join(music.quote_lines(music.quote(piece(tracks=[part('kit', [(0, 0.5, 36)], engine='drum_synth')]), 'kit', 1, 1)))
        self.assertIn('keys of its kit', kit)

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
