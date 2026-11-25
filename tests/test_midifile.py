"""Standard MIDI Files for `sfx.py music`: parts, tempo and meter maps, quotes and refusals."""
import struct
import unittest
from pathlib import Path
from unittest import mock

import helpers
from sfxlib import midifile
from sfxlib.music import MusicError

PPQ = 480


def vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def track(events, end=True):
    """events: [(delta ticks, bytes)]; the end-of-track meta event is added unless end is False."""
    body = b''.join(vlq(d) + e for d, e in events)
    if end:
        body += b'\x00\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(body)) + body


def smf(tracks, fmt=1, division=PPQ):
    return b'MThd' + struct.pack('>IHHH', 6, fmt, len(tracks), division) + b''.join(tracks)


def tempo(bpm):
    return b'\xff\x51\x03' + int(round(60e6 / bpm)).to_bytes(3, 'big')


def meter(n, d_power, bb=8):
    return b'\xff\x58\x04' + bytes([n, d_power, 24, bb])


def name(text):
    raw = text.encode('latin-1')
    return b'\xff\x03' + vlq(len(raw)) + raw


class Files(unittest.TestCase):
    def write(self, data, label):
        folder = helpers.scratch(f'midi-{self.id().rsplit(".", 1)[-1][:40]}-{label}')
        path = Path(folder) / f'{label}.mid'
        path.write_bytes(data)
        return path

    def conductor_and_melody(self):
        conductor = track([(0, name('Theme')), (0, tempo(120)), (0, meter(4, 2))])
        melody = track([(0, name('Flute')), (0, b'\xc0\x49'),
                        (0, b'\x90\x48\x64'), (PPQ, b'\x48\x00'),          # running status, velocity 0 as note-off
                        (0, b'\x4c\x50'), (PPQ, b'\x80\x4c\x40'),
                        (2 * PPQ, b'\x90\x4f\x70'), (4 * PPQ, b'\x80\x4f\x40')])
        return self.write(smf([conductor, melody]), 'theme')


class Parsing(Files):
    def test_a_format_1_file_lists_its_part_with_name_program_and_range(self):
        song = midifile.load(self.conductor_and_melody())
        self.assertEqual(song['title'], 'Theme')
        self.assertEqual([p['id'] for p in song['parts']], ['2'])
        part = song['parts'][0]
        self.assertEqual((part['name'], part['program'], part['channel']), ('Flute', 0x49, 1))
        self.assertEqual([n[2] for n in part['notes']], [0x48, 0x4C, 0x4F])
        text = '\n'.join(midifile.overview(song))
        self.assertIn('C5 to G5', text)
        self.assertIn('--bars 1-2', text)                       # an example inside the file
        self.assertIn('program 73', text)

    def test_tempo_changes_and_meter_changes_give_seconds_and_bars(self):
        conductor = track([(0, tempo(120)), (0, meter(3, 2)), (3 * PPQ, tempo(60)), (0, meter(4, 2))])
        notes = track([(0, b'\x90\x3c\x64'), (3 * PPQ, b'\x80\x3c\x00'), (0, b'\x90\x3e\x64'), (4 * PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([conductor, notes]), 'maps'))
        self.assertEqual(song['bar_ticks'][:3], [0, 3 * PPQ, 7 * PPQ])
        self.assertAlmostEqual(midifile.seconds(song, 3 * PPQ), 1.5)      # three quarters at 120
        self.assertAlmostEqual(midifile.seconds(song, 7 * PPQ), 5.5)      # then four at 60
        q = midifile.quote(song, '2', 2, 2)
        self.assertEqual(q['notes'], [[0.0, 0x3E, 4.0, 0x64]])

    def test_the_thirty_seconds_field_sets_the_bar_length(self):
        conductor = track([(0, meter(4, 2, bb=4))])                    # 32 x 4 / (4 x 4) = 8 MIDI quarters a bar
        notes = track([(0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (9 * PPQ, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([conductor, notes]), 'bb'))
        self.assertEqual(song['bar_ticks'][1], 8 * PPQ)

    def test_a_format_0_file_gives_one_part_per_channel_and_names_the_drums(self):
        one = track([(0, tempo(100)), (0, b'\x90\x40\x60'), (0, b'\x99\x24\x7f'), (PPQ, b'\x80\x40\x00'), (0, b'\x89\x24\x00')])
        song = midifile.load(self.write(smf([one], fmt=0), 'format0'))
        self.assertEqual(sorted(p['id'] for p in song['parts']), ['1.1', '1.10'])
        drums = next(p for p in song['parts'] if p['id'] == '1.10')
        self.assertTrue(drums['drums'])
        self.assertIn('General MIDI percussion', '\n'.join(midifile.overview(song)))

    def test_a_key_struck_again_ends_the_earlier_note_there(self):
        notes = track([(0, b'\x90\x3c\x64'), (PPQ, b'\x90\x3c\x50'), (PPQ, b'\x80\x3c\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'again'))
        self.assertEqual([(n[0], n[1]) for n in song['parts'][0]['notes']], [(0, PPQ), (PPQ, 2 * PPQ)])

    def test_a_note_on_and_a_note_off_of_one_key_at_one_tick_end_the_old_note(self):
        notes = track([(0, b'\x90\x3c\x64'), (PPQ, b'\x90\x3c\x50'), (0, b'\x80\x3c\x00'), (PPQ, b'\x80\x3c\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'same-tick'))
        self.assertEqual([(n[0], n[1], n[3]) for n in song['parts'][0]['notes']], [(0, PPQ, 0x64), (PPQ, 2 * PPQ, 0x50)])

    def test_sysex_and_meta_events_are_skipped_and_cancel_running_status(self):
        notes = track([(0, b'\xf0\x03\x7e\x7f\xf7'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'sysex'))
        self.assertEqual(len(song['parts'][0]['notes']), 1)
        broken = track([(0, b'\x90\x3c\x64'), (0, b'\xff\x01\x01x'), (PPQ, b'\x3c\x00')])   # data byte after a meta event
        with self.assertRaisesRegex(MusicError, 'running status'):
            midifile.load(self.write(smf([broken], fmt=0), 'running'))


class Refusals(Files):
    def test_smpte_division_format_2_and_cut_files_are_refused_in_a_sentence(self):
        notes = track([(0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00')])
        with self.assertRaisesRegex(MusicError, 'SMPTE'):
            midifile.load(self.write(smf([notes], division=0xE728), 'smpte'))
        with self.assertRaisesRegex(MusicError, 'format 2'):
            midifile.load(self.write(smf([notes], fmt=2), 'format2'))
        with self.assertRaisesRegex(MusicError, 'past the end'):
            midifile.load(self.write(smf([notes])[:-3], 'cut'))
        with self.assertRaisesRegex(MusicError, 'not a Standard MIDI File'):
            midifile.load(self.write(b'RIFF' + b'\x00' * 20, 'riff'))

    def test_a_file_over_the_limit_is_refused_before_it_is_read(self):
        path = self.conductor_and_melody()
        with mock.patch.object(midifile, 'MAX_BYTES', 10), mock.patch.object(Path, 'read_bytes') as read:
            with self.assertRaisesRegex(MusicError, 'at most'):
                midifile.load(path)
            read.assert_not_called()

    def test_a_delta_time_past_four_bytes_is_refused(self):
        body = b'\xff\xff\xff\xff\x7f\x90\x3c\x64'
        data = smf([b'MTrk' + struct.pack('>I', len(body)) + body], fmt=0)
        with self.assertRaisesRegex(MusicError, 'four bytes'):
            midifile.load(self.write(data, 'vlq'))


class Quotes(Files):
    def test_a_quote_counts_from_the_bar_and_carries_the_rights_line(self):
        song = midifile.load(self.conductor_and_melody())
        q = midifile.quote(song, '2', 1, 1)
        self.assertEqual(q['notes'], [[0.0, 0x48, 0.5, 0x64], [0.5, 0x4C, 0.5, 0x50]])
        lines = midifile.quote_lines(q)
        self.assertTrue(lines[1].startswith('"notes": [[0.0, 72, 0.5, 100]'))
        self.assertIn('keeps its rights', '\n'.join(lines))

    def test_a_note_past_the_last_bar_is_cut_and_one_from_before_is_counted(self):
        song = midifile.load(self.conductor_and_melody())
        q = midifile.quote(song, '2', 1, 1)
        self.assertEqual(q['cut'], 0)
        last = midifile.quote(song, '2', 2, 2)                        # G5 runs from beat 1 of bar 2 for 4 beats: whole
        self.assertEqual(last['notes'], [[0.0, 0x4F, 2.0, 0x70]])
        conductor = track([(0, meter(4, 2))])
        long = track([(0, b'\x90\x3c\x64'), (6 * PPQ, b'\x80\x3c\x00'), (0, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([conductor, long]), 'cut'))
        first = midifile.quote(song, '2', 1, 1)
        self.assertEqual(first['cut'], 1)
        self.assertAlmostEqual(first['notes'][0][2], 2.0)
        second = midifile.quote(song, '2', 2, 2)
        self.assertEqual(second['rang_in'], 1)

    def test_a_sustain_pedal_in_the_bars_is_named(self):
        notes = track([(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (0, b'\xb0\x40\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'pedal'))
        q = midifile.quote(song, '1', 1, 1)
        self.assertEqual(q['controls'], ['sustain pedal'])
        self.assertIn('sustain pedal', '\n'.join(midifile.quote_lines(q)))

    def test_unknown_parts_and_bars_outside_the_file_are_refused(self):
        song = midifile.load(self.conductor_and_melody())
        with self.assertRaisesRegex(MusicError, "no part '9'"):
            midifile.quote(song, '9', 1, 1)
        with self.assertRaisesRegex(MusicError, 'bars 1 to'):
            midifile.quote(song, '2', 1, 40)


class ReviewCases(Files):
    """Cases from the first review of 0.3: each failed before its fix."""

    def test_the_last_tempo_and_meter_at_one_tick_hold(self):
        conductor = track([(0, tempo(120)), (0, tempo(150)), (0, meter(4, 2)), (0, meter(3, 2))])
        notes = track([(0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (2 * PPQ, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([conductor, notes]), 'order'))
        self.assertAlmostEqual(midifile.seconds(song, PPQ), 0.4)                 # a quarter at 150
        self.assertEqual(song['bar_ticks'][1], 3 * PPQ)                           # bars of 3/4
        self.assertEqual(midifile.bar_of(song, 3 * PPQ), 2)

    def test_the_length_is_the_last_event_not_the_last_bar(self):
        notes = track([(0, tempo(120)), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'length'))
        self.assertEqual(midifile.describe(song)['seconds'], 0.5)
        self.assertIn(', 0.5 s', midifile.overview(song)[0])

    def test_the_program_in_effect_at_the_first_note_and_later_changes(self):
        notes = track([(0, b'\xc0\x00'), (0, b'\xc0\x28'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'),
                       (3 * PPQ, b'\xc0\x5f'), (0, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'program'))
        part = song['parts'][0]
        self.assertEqual(part['program'], 0x28)
        self.assertIn('then program 95 from bar 2', '\n'.join(midifile.overview(song)))

    def test_a_pedal_held_from_before_the_bars_is_named(self):
        notes = track([(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'),
                       (3 * PPQ, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00'), (PPQ // 2, b'\xb0\x40\x00')])
        song = midifile.load(self.write(smf([notes], fmt=0), 'held'))
        self.assertEqual(midifile.quote(song, '1', 2, 2)['controls'], ['sustain pedal'])
        lifted = track([(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (0, b'\xb0\x40\x00'),
                        (3 * PPQ, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([lifted], fmt=0), 'lifted'))
        self.assertEqual(midifile.quote(song, '1', 2, 2)['controls'], [])


class ReviewCases2(Files):
    """Cases from the second review of 0.3."""

    def test_a_setup_track_sets_the_program_and_pedal_of_its_channel(self):
        setup = track([(0, b'\xc0\x28'), (0, b'\xb0\x40\x7f'), (6 * PPQ, b'\xb0\x40\x00')])
        notes = track([(4 * PPQ, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), setup, notes]), 'setup'))
        part = next(p for p in song['parts'] if p['id'] == '3')
        self.assertEqual(part['program'], 0x28)
        self.assertEqual(midifile.quote(song, '3', 2, 2)['controls'], ['sustain pedal'])

    def test_a_pedal_lifted_at_the_first_tick_of_the_bars_or_reset_is_not_held(self):
        lifted = track([(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (3 * PPQ, b'\xb0\x40\x00'),
                        (0, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([lifted], fmt=0), 'lifted-at-bar'))
        self.assertEqual(midifile.quote(song, '1', 2, 2)['controls'], [])
        reset = track([(0, b'\xb0\x40\x7f'), (0, b'\xe0\x00\x50'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'),
                       (PPQ, b'\xb0\x79\x00'), (2 * PPQ, b'\x90\x3e\x64'), (PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([reset], fmt=0), 'reset'))
        self.assertEqual(midifile.quote(song, '1', 2, 2)['controls'], [])


class ReviewCases3(Files):
    """Cases from the third review of 0.3."""

    def test_tracks_sharing_a_channel_keep_their_own_program_and_pedal(self):
        # MuseScore and music-production put such tracks on two ports (FF 21); other programs reuse a channel outright
        piano = track([(0, name('Piano')), (0, b'\xff\x21\x01\x00'), (0, b'\xc0\x00'),
                       (0, b'\x90\x3c\x64'), (4 * PPQ, b'\x80\x3c\x00'), (0, b'\x90\x3e\x64'), (4 * PPQ, b'\x80\x3e\x00')])
        strings = track([(0, name('Strings')), (0, b'\xff\x21\x01\x01'), (0, b'\xc0\x30'), (0, b'\xb0\x40\x7f'),
                         (0, b'\x90\x43\x64'), (4 * PPQ, b'\x80\x43\x00'), (0, b'\x90\x45\x64'), (4 * PPQ, b'\x80\x45\x00'),
                         (0, b'\xb0\x40\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), piano, strings]), 'shared'))
        parts = {p['id']: p for p in song['parts']}
        self.assertEqual((parts['2']['program'], parts['3']['program']), (0, 0x30))
        self.assertEqual(midifile.quote(song, '2', 2, 2)['controls'], [])
        self.assertEqual(midifile.quote(song, '3', 2, 2)['controls'], ['sustain pedal'])

    def test_a_track_name_in_utf8_reads_as_written_and_latin1_still_reads(self):
        raw = 'Flûte 主旋律'.encode('utf-8')
        melody = track([(0, b'\xff\x03' + vlq(len(raw)) + raw), (0, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00')])
        song = midifile.load(self.write(smf([melody], fmt=0), 'utf8'))
        self.assertEqual(song['parts'][0]['name'], 'Flûte 主旋律')
        older = track([(0, name('Flûte')), (0, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00')])
        song = midifile.load(self.write(smf([older], fmt=0), 'latin1'))
        self.assertEqual(song['parts'][0]['name'], 'Flûte')


class ReviewCases4(Files):
    """Cases from the fourth review of 0.3."""

    def test_a_setup_reset_before_a_pedal_at_one_tick_leaves_it_down(self):
        setup = track([(0, b'\xb0\x79\x00')])                         # Reset All Controllers, then the piano's pedal
        piano = track([(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (2 * PPQ, b'\x80\x3c\x00'),
                       (2 * PPQ, b'\x90\x3e\x64'), (2 * PPQ, b'\x80\x3e\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), setup, piano]), 'reset-then-pedal'))
        self.assertEqual(midifile.quote(song, '3', 1, 1)['controls'], ['sustain pedal'])

    def test_a_track_whose_own_program_comes_later_opens_on_its_channels(self):
        setup = track([(0, b'\xc0\x28')])
        melody = track([(PPQ // 2, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00'),
                        (PPQ * 5 // 2, b'\xc0\x30'), (0, b'\x90\x4a\x64'), (PPQ, b'\x80\x4a\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), setup, melody]), 'later-own'))
        part = next(p for p in song['parts'] if p['id'] == '3')
        self.assertEqual(part['program'], 0x28)
        self.assertEqual(part['later_programs'], [(4 * PPQ, 0x30)])

    def test_a_setup_tracks_later_change_is_listed_for_a_part_that_sets_none(self):
        setup = track([(0, b'\xc0\x28'), (8 * PPQ, b'\xc0\x30')])
        melody = track([(0, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00'), (11 * PPQ, b'\x90\x4a\x64'), (PPQ, b'\x80\x4a\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), setup, melody]), 'setup-later'))
        part = next(p for p in song['parts'] if p['id'] == '3')
        self.assertEqual((part['program'], part['later_programs']), (0x28, [(8 * PPQ, 0x30)]))


class ReviewCases5(Files):
    """Cases from the fifth review of 0.3."""

    def test_a_setup_tracks_later_change_counts_beside_the_parts_own_opening(self):
        melody = track([(0, b'\xc0\x00'), (0, b'\x90\x3c\x64'), (PPQ, b'\x80\x3c\x00'), (3 * PPQ, b'\x90\x40\x64'),
                        (PPQ, b'\x80\x40\x00')])
        setup = track([(3 * PPQ, b'\xc0\x28')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), melody, setup]), 'setup-later-own'))
        part = next(p for p in song['parts'] if p['id'] == '2')
        self.assertEqual((part['program'], part['later_programs']), (0, [(3 * PPQ, 0x28)]))

    def test_a_staff_that_sets_no_program_takes_the_other_staffs(self):
        right = track([(0, b'\xc0\x28'), (0, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00'), (7 * PPQ, b'\xc0\x30'),
                       (0, b'\x90\x4a\x64'), (PPQ, b'\x80\x4a\x00')])
        left = track([(0, b'\x90\x30\x64'), (PPQ, b'\x80\x30\x00'), (7 * PPQ, b'\x90\x32\x64'), (PPQ, b'\x80\x32\x00')])
        song = midifile.load(self.write(smf([track([(0, meter(4, 2))]), right, left]), 'staves'))
        part = next(p for p in song['parts'] if p['id'] == '3')
        self.assertEqual((part['program'], part['later_programs']), (0x28, [(8 * PPQ, 0x30)]))

    def test_the_title_is_the_first_tracks_name_else_the_files(self):
        horn = track([(0, name('horn')), (0, b'\x90\x48\x64'), (PPQ, b'\x80\x48\x00')])
        song = midifile.load(self.write(smf([track([(0, tempo(100))]), horn]), 'composition'))
        self.assertEqual(song['title'], 'composition')
        self.assertEqual(song['parts'][0]['name'], 'horn')


if __name__ == '__main__':
    unittest.main()
