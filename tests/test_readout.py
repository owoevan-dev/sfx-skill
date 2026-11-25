"""The readout: a sound as numbers an agent reads (octave bands over time, tonal lines, events, roll-off)."""
import json
import unittest

import numpy as np

import helpers  # noqa: F401  (import paths)
from sfxlib import readout

RATE = 48000


def tone(freq, seconds, amp=0.5):
    return amp * np.sin(2 * np.pi * freq * np.arange(int(seconds * RATE)) / RATE)


def faded(x, ms=5):
    n = int(RATE * ms / 1000)
    ramp = np.linspace(0, 1, n)
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


class Bands(unittest.TestCase):
    def test_the_weights_add_up_to_one_everywhere(self):
        freqs = np.geomspace(5, 24000, 2000)
        total = readout.band_weights(freqs).sum(axis=0)
        self.assertTrue(np.allclose(total, 1.0, atol=1e-9))

    def test_low_passed_noise_leaves_the_top_bands_empty_and_the_roll_off_under_the_cut(self):
        rng = np.random.default_rng(3)
        spec = np.fft.rfft(rng.standard_normal(RATE))
        spec[np.fft.rfftfreq(RATE, 1 / RATE) > 2000] = 0
        r = readout.readout(faded(np.fft.irfft(spec, RATE) * 0.3), RATE)
        middle = r['bands']['slices'][len(r['bands']['slices']) // 2]['db']
        self.assertIsNone(middle[readout.LABELS.index('8k')])
        self.assertIsNone(middle[readout.LABELS.index('16k')])
        self.assertLess(r['rolloff_hz'], 2100)

    def test_a_one_shot_starts_its_slices_at_the_onset_and_doubles_them(self):
        x = np.concatenate([np.zeros(int(0.2 * RATE)), faded(tone(1000, 0.5))])
        r = readout.readout(x, RATE)
        labels = [s['label'] for s in r['bands']['slices']]
        self.assertEqual(r['onset_s'], 0.2)
        self.assertEqual(labels[:4], ['before', '0-5 ms', '5-10 ms', '10-20 ms'])

    def test_a_loop_has_twelve_equal_slices_and_no_event_at_its_start(self):
        rng = np.random.default_rng(4)
        r = readout.readout(rng.standard_normal(3 * RATE) * 0.1, RATE, loop=True)
        self.assertEqual(len(r['bands']['slices']), readout.LOOP_SLICES)
        self.assertEqual(r['events'], [])


class Lines(unittest.TestCase):
    def test_a_tone_is_found_at_its_frequency_with_its_note(self):
        lines = readout.readout(faded(tone(1000, 0.5)), RATE)['lines']
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0]['hz'], 1000, delta=1)
        self.assertEqual(lines[0]['note'], 'B5+21')

    def test_a_chord_shows_each_of_its_notes(self):
        chord = sum(tone(f, 1.0, 0.2) for f in (440.0, 554.37, 659.26))
        notes = sorted(line['note'][:-2].rstrip('+-') for line in readout.readout(faded(chord), RATE)['lines'])
        self.assertEqual(notes, ['A4', 'C#5', 'E5'])

    def test_a_glide_prints_both_of_its_ends(self):
        x = 0.5 * np.sin(2 * np.pi * np.cumsum(np.linspace(300, 1200, RATE)) / RATE)
        line = readout.readout(faded(x), RATE)['lines'][0]
        self.assertLess(line['from_hz'], 350)
        self.assertGreater(line['to_hz'], 1100)
        self.assertIn('moves', '\n'.join(readout.text(readout.readout(faded(x), RATE), 'glide')))

    def test_noise_holds_no_tonal_line_white_pink_or_cut_off_steeply(self):
        for seed in range(4):
            rng = np.random.default_rng(seed)
            white = rng.standard_normal(2 * RATE) * 0.2
            spec = np.fft.rfft(rng.standard_normal(RATE))
            steep = np.fft.irfft(np.where(np.fft.rfftfreq(RATE, 1 / RATE) > 2000, 0, spec), RATE) * 0.3
            pink = np.fft.irfft(np.fft.rfft(rng.standard_normal(2 * RATE)) / np.maximum(1, np.sqrt(np.arange(RATE + 1))), 2 * RATE)
            for name, x in (('white', white), ('steep', steep), ('pink', pink / np.abs(pink).max() * 0.5)):
                with self.subTest(seed=seed, noise=name):
                    self.assertEqual(readout.readout(faded(x), RATE)['lines'], [])

    def test_a_tone_over_noise_is_one_line(self):
        rng = np.random.default_rng(3)
        x = rng.standard_normal(2 * RATE) * 0.05 + 0.03 * np.sin(2 * np.pi * 1000 * np.arange(2 * RATE) / RATE)
        lines = readout.readout(faded(x), RATE)['lines']
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0]['hz'], 1000, delta=2)

    def test_a_quiet_hum_under_a_chord_is_labelled_a_mains_frequency(self):
        t = np.arange(2 * RATE) / RATE
        chord = sum(0.2 * np.sin(2 * np.pi * f * t) for f in (440.0, 554.37, 659.26)) * np.exp(-t / 0.4)
        hum = 0.6 * 10 ** (-50 / 20) * np.sin(2 * np.pi * 60 * t)
        lines = readout.readout(faded(chord + hum), RATE)['lines']
        mains = [line for line in lines if line['mains']]
        self.assertEqual(len(mains), 1)
        self.assertAlmostEqual(mains[0]['hz'], 60, delta=1)

    def test_a_short_low_thump_is_no_mains_line(self):
        x = np.concatenate([faded(tone(60, 0.15)), np.zeros(RATE)])
        self.assertFalse(any(line['mains'] for line in readout.readout(x, RATE)['lines']))


class LinesMore(unittest.TestCase):
    def test_a_slow_glide_through_60_hz_is_no_mains_line(self):
        x = 0.5 * np.sin(2 * np.pi * np.cumsum(np.linspace(40, 80, 2 * RATE)) / RATE)
        self.assertFalse(any(line['mains'] for line in readout.readout(faded(x), RATE)['lines']))

    def test_a_60_hz_tone_shorter_than_the_minimum_is_no_mains_line_even_when_it_is_the_whole_sound(self):
        self.assertFalse(any(line['mains'] for line in readout.readout(faded(tone(60, 0.3)), RATE)['lines']))

    def test_a_hum_is_shown_even_behind_more_louder_partials_than_the_list_holds(self):
        t = np.arange(2 * RATE) / RATE
        partials = sum(0.08 * np.sin(2 * np.pi * 220 * k * t) for k in range(1, readout.LINES_SHOWN + 3))
        hum = 0.003 * np.sin(2 * np.pi * 50 * t)
        r = readout.readout(faded(partials + hum), RATE)
        self.assertGreater(r['lines_found'], readout.LINES_SHOWN)
        self.assertTrue(any(line['mains'] for line in r['lines']))

    def test_opposite_polarity_channels_do_not_cancel(self):
        x = faded(tone(1000, 0.5))
        r = readout.readout(np.stack([x, -x], axis=1), RATE)
        self.assertEqual(len(r['lines']), 1)

    def test_a_loop_is_split_as_a_circle_so_its_start_keeps_its_low_end(self):
        loop = tone(63, 2.0, 0.4)                                    # 126 whole cycles: it joins without a seam
        env = readout.band_envelopes(loop, RATE, loop=True)[readout.LABELS.index('63')]
        start, middle = 10 * np.log10(env[:8].mean()), 10 * np.log10(env[900:1100].mean())
        self.assertLessEqual(abs(start - middle), 1)                 # padded, the first 8 ms come out 3.5 dB low

    def test_bands_above_a_low_sample_rate_are_marked_unavailable(self):
        rate = 22050
        x = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(rate) / rate)
        r = readout.readout(x, rate)
        self.assertEqual(r['bands']['slices'][0]['db'][readout.LABELS.index('16k')], 'n/a')
        self.assertIn('n/a', '\n'.join(readout.text(r, 'low rate')))


class Events(unittest.TestCase):
    def test_the_declared_peak_at_chooses_the_main_event_over_a_louder_click(self):
        rng = np.random.default_rng(8)
        x = np.zeros(int(1.6 * RATE))
        n = int(0.3 * RATE)
        x[:n] = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.05 * RATE)) * 0.2
        x[int(1.2 * RATE):int(1.2 * RATE) + 24] += 0.9               # louder than the strike
        loudest = readout.readout(x, RATE)
        declared = readout.readout(x, RATE, peak_at=0.0)
        self.assertAlmostEqual(next(e for e in loudest['events'] if e['main'])['time_s'], 1.2, delta=0.01)
        self.assertEqual(next(e for e in declared['events'] if e['main'])['time_s'], 0.0)
        self.assertIn('peak_at', '\n'.join(readout.text(declared, 'strike')))

    def test_a_click_after_a_burst_is_an_event_of_its_own(self):
        rng = np.random.default_rng(6)
        x = np.zeros(int(1.6 * RATE))
        n = int(0.3 * RATE)
        x[:n] = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.05 * RATE)) * 0.5
        x[int(1.2 * RATE):int(1.2 * RATE) + 24] += 0.4
        events = readout.readout(x, RATE)['events']
        self.assertTrue(events[0]['main'])
        self.assertTrue(any(abs(e['time_s'] - 1.2) <= 0.01 and not e['main'] for e in events), events)

    def test_an_event_after_the_loudest_moment_is_not_the_main_one(self):
        t = np.arange(int(0.6 * RATE)) / RATE
        swell = tone(500, 0.6, 0.6) * np.minimum(t / 0.1, np.exp(-(t - 0.1) / 0.08))     # loudest at 0.1 s
        x = swell.copy()
        x[int(0.12 * RATE):int(0.12 * RATE) + 24] += 0.05                                 # a quiet click 20 ms after it
        r = readout.readout(faded(x), RATE)
        main = next(e for e in r['events'] if e['main'])
        self.assertLess(main['time_s'], 0.1)

    def test_the_main_event_leads_into_the_loudest_moment(self):
        swell = faded(tone(500, 0.6) * np.linspace(0, 1, int(0.6 * RATE)) ** 3)
        strike = np.zeros(int(0.4 * RATE))
        strike[:2400] = np.random.default_rng(7).standard_normal(2400) * np.exp(-np.arange(2400) / 300)
        r = readout.readout(np.concatenate([swell, strike]), RATE)
        main = [e for e in r['events'] if e['main']]
        self.assertEqual(len(main), 1)
        self.assertAlmostEqual(main[0]['time_s'], 0.6, delta=0.01)


class Output(unittest.TestCase):
    def test_silence_and_a_single_millisecond_do_not_fail(self):
        silent = readout.readout(np.zeros(4800), RATE)
        self.assertTrue(silent['silent'])
        self.assertEqual((silent['lines'], silent['events'], silent['rolloff_hz'], silent['bands']), ([], [], None, None))
        r = readout.readout(np.full(48, 0.1), RATE)
        self.assertFalse(r['silent'])
        self.assertTrue(readout.text(r, 'tick'))

    def test_the_json_carries_no_nan_and_matches_the_text(self):
        chord = sum(tone(f, 0.8, 0.2) for f in (440.0, 660.0))
        r = readout.readout(faded(chord), RATE)
        dumped = json.dumps(r, allow_nan=False)
        self.assertIn('lines', json.loads(dumped))
        text = '\n'.join(readout.text(r, 'chord'))
        for line in r['lines']:
            self.assertIn(line['note'], text)

    def test_stereo_is_read_as_both_channels_together(self):
        x = faded(tone(1000, 0.5))
        mono = readout.readout(x, RATE)
        stereo = readout.readout(np.stack([x, np.zeros_like(x)], axis=1), RATE)
        self.assertEqual(stereo['lines'][0]['note'], mono['lines'][0]['note'])


class ReviewCases(unittest.TestCase):
    """Cases from the first review of 0.3: each failed before its fix."""

    def test_44100_hz_keeps_time_over_a_long_file(self):
        rate = 44100
        x = np.zeros(10 * rate)
        x[8 * rate:8 * rate + rate // 2] = 0.5 * np.sin(2 * np.pi * 440 * np.arange(rate // 2) / rate)
        r = readout.readout(x, rate)
        self.assertAlmostEqual(r['onset_s'], 8.0, delta=0.002)
        self.assertEqual(readout._sample(8000, rate), 352800)              # the roll-off's window starts there too
        self.assertAlmostEqual(r['bands']['slices'][-1]['to_ms'], 10000, delta=1)

    def test_digital_silence_before_a_click_stays_silent(self):
        x = np.zeros(RATE)
        x[RATE // 2:RATE // 2 + 48] = 0.5
        r = readout.readout(x, RATE)
        before = r['bands']['slices'][0]
        self.assertEqual(before['label'], 'before')
        self.assertTrue(all(v is None for v in before['db']), before['db'])
        self.assertAlmostEqual(r['events'][0]['time_s'], 0.5, delta=0.002)

    def test_one_pitch_is_one_line_steady_or_gliding(self):
        self.assertEqual(len(readout.readout(faded(tone(1000, 1.0)), RATE)['lines']), 1)
        glide = 0.5 * np.sin(2 * np.pi * np.cumsum(np.linspace(150, 270, RATE)) / RATE)
        self.assertEqual(len(readout.readout(faded(glide), RATE)['lines']), 1)

    def test_higher_sample_rates_still_find_a_bass_tone(self):
        for rate, hz in ((96000, 30.0), (192000, 60.0)):
            x = 0.5 * np.sin(2 * np.pi * hz * np.arange(rate) / rate)
            with self.subTest(rate=rate):
                lines = readout.readout(faded(x) if rate == RATE else x, rate)['lines']
                self.assertTrue(any(abs(line['hz'] - hz) < 1 for line in lines), lines)

    def test_bursts_with_silent_gaps_are_no_mains_line(self):
        burst = 0.4 * np.sin(2 * np.pi * 240 * np.arange(int(0.1 * RATE)) / RATE)
        x = np.concatenate([np.concatenate([faded(burst, 2), np.zeros(int(0.1 * RATE))]) for _ in range(10)])
        self.assertFalse(any(line['mains'] for line in readout.readout(x, RATE)['lines']))

    def test_a_one_millisecond_click_has_a_roll_off(self):
        x = np.zeros(RATE // 10)
        x[96:144] = 0.5                                              # exactly millisecond 2: the active part is the click alone
        self.assertIsNotNone(readout.readout(x, RATE)['rolloff_hz'])

    def test_a_neighbouring_pitch_after_a_gap_starts_a_line_of_its_own(self):
        t = np.arange(int(1.4 * RATE)) / RATE
        x = np.where(t < 0.2, 0.3 * np.sin(2 * np.pi * 1197.6 * t), 0.0)              # a partial of an opening note
        x = x + np.where(t >= 0.26, 0.5 * np.sin(2 * np.pi * 1174.7 * t), 0.0)        # then D6, 34 cents lower
        lines = readout.readout(faded(x), RATE)['lines']
        d6 = [line for line in lines if abs(line['hz'] - 1174.7) < 3]
        self.assertTrue(d6 and d6[0]['start_s'] >= 0.24, lines)

    def test_the_number_of_events_found_is_given_when_the_list_is_cut(self):
        ping = faded(tone(3000, 0.01, 0.5), 1)
        x = np.zeros(int(2.6 * RATE))
        for k in range(12):
            start = int((0.05 + 0.2 * k) * RATE)
            x[start:start + len(ping)] += ping
        r = readout.readout(x, RATE)
        self.assertEqual(r['events_found'], 12)
        self.assertEqual(len(r['events']), readout.EVENTS_SHOWN)
        self.assertIn('12 found', '\n'.join(readout.text(r, 'pings')))


class ReviewCases2(unittest.TestCase):
    """Cases from the second review of 0.3."""

    def test_a_swell_has_no_main_event_and_a_late_click_is_not_one(self):
        t = np.arange(2 * RATE) / RATE
        x = 0.5 * np.sin(2 * np.pi * 500 * t) * np.sin(np.pi * t / 2) ** 2          # swells to its loudest at 1 s
        x[int(1.4 * RATE):int(1.4 * RATE) + 24] += 0.05
        r = readout.readout(faded(x), RATE)
        self.assertFalse(any(e['main'] for e in r['events']), r['events'])
        self.assertIn('none does', '\n'.join(readout.text(r, 'swell')))

    def test_a_siren_that_comes_back_shows_its_range(self):
        t = np.arange(2 * RATE) / RATE
        hz = 440 * 2 ** np.clip(np.sin(np.pi * t / 2), 0, 1)                         # 440 up to 880 and back
        x = 0.5 * np.sin(2 * np.pi * np.cumsum(hz) / RATE)
        r = readout.readout(faded(x), RATE)
        line = max(r['lines'], key=lambda line: line['high_hz'] - line['low_hz'])
        self.assertGreater(line['high_hz'], 800)
        self.assertLess(line['low_hz'], 480)
        self.assertIn('ranges', '\n'.join(readout.text(r, 'siren')))

    def test_a_loop_shorter_than_the_history_wraps_round(self):
        x = 0.4 * np.sin(2 * np.pi * 1000 * np.arange(960) / RATE)                    # 20 ms, 20 whole cycles
        self.assertEqual(readout.readout(x, RATE, loop=True)['events'], [])

    def test_a_file_of_no_frames_is_silence(self):
        self.assertTrue(readout.readout(np.zeros((0, 2)), RATE)['silent'])


class ReviewCases3(unittest.TestCase):
    """Cases from the third review of 0.3."""

    def swell_after(self, bed):
        """A tap at 0.05 s, then a 500 Hz swell from 0.5 s to its loudest at 1.5 s, over a recording's faint noise (more
        than 60 dB under); `bed` sounds from the tap on."""
        x = 0.0003 * np.random.default_rng(5).standard_normal(2 * RATE)
        k = np.arange(int(0.02 * RATE))
        x[int(0.05 * RATE):int(0.05 * RATE) + len(k)] = 0.5 * np.exp(-k / (0.003 * RATE)) * np.sin(2 * np.pi * 3000 * k / RATE)
        x[int(0.05 * RATE):] += bed * np.sin(2 * np.pi * 500 * np.arange(int(0.05 * RATE), 2 * RATE) / RATE)
        t = np.arange(int(0.5 * RATE), 2 * RATE)
        swell = np.where(t < 1.5 * RATE, ((t - 0.5 * RATE) / RATE) ** 2, np.exp(-(t - 1.5 * RATE) / (0.1 * RATE)))
        x[int(0.5 * RATE):] += 0.8 * swell * np.sin(2 * np.pi * 500 * t / RATE)
        return readout.readout(faded(x), RATE)

    def test_a_tap_before_silence_does_not_lead_a_later_swell(self):
        r = self.swell_after(0.0)
        self.assertTrue(r['events'])
        self.assertFalse(any(e['main'] for e in r['events']), r['events'])
        held = self.swell_after(0.03)                                   # a bed under the swell carries the tap into it
        self.assertEqual([e['time_s'] for e in held['events'] if e['main']], [0.05])

    def test_a_dip_of_a_few_milliseconds_is_no_silence(self):
        x = np.zeros(RATE // 2)
        x[480:528] = 0.02 * np.sin(2 * np.pi * 4000 * np.arange(48) / RATE)           # a faint tick at 10 ms, then 3 ms of
        k = np.arange(int(0.2 * RATE))                                                # nothing before the body at 14 ms
        x[672:672 + len(k)] += 0.5 * np.exp(-k / (0.05 * RATE)) * np.sin(2 * np.pi * 1000 * k / RATE) * np.minimum(1, k / 480)
        r = readout.readout(x, RATE)
        self.assertEqual([e['time_s'] for e in r['events'] if e['main']], [0.01], r['events'])

    def test_an_event_across_a_loop_join_is_one_event(self):
        x = 0.001 * np.random.default_rng(3).standard_normal(RATE)
        x[int(0.5 * RATE):int(0.53 * RATE)] += 0.5 * np.sin(2 * np.pi * 1000 * np.arange(int(0.03 * RATE)) / RATE)
        whole = readout.readout(x, RATE, loop=True)
        turned = readout.readout(np.roll(x, int(0.498 * RATE)), RATE, loop=True)    # the ping now starts at 0.998 s
        self.assertEqual([(e['time_s'], e['main']) for e in whole['events']], [(0.5, True)])
        self.assertEqual([(e['time_s'], e['main']) for e in turned['events']], [(0.998, True)])
        self.assertEqual(turned['events_found'], 1)

    def test_a_line_level_is_under_the_loudest_spectral_peak_tonal_or_not(self):
        x = 0.001 * np.sin(2 * np.pi * 500 * np.arange(RATE) / RATE)
        x[:48] = 0.8 * np.sin(2 * np.pi * 2000 * np.arange(48) / RATE)               # a 1 ms click over a faint ring
        r = readout.readout(x, RATE)
        line = next(line for line in r['lines'] if abs(line['hz'] - 500) < 5)
        self.assertLess(line['db'], -20)


class ReviewCases4(unittest.TestCase):
    """Cases from the fourth review of 0.3."""

    def test_a_loop_matches_peak_at_round_its_join(self):
        x = 0.001 * np.random.default_rng(7).standard_normal(RATE)
        burst = np.sin(2 * np.pi * 1000 * np.arange(int(0.03 * RATE)) / RATE)
        x[int(0.4 * RATE):int(0.43 * RATE)] += 0.2 * burst                          # a quieter hit at 0.4 s
        hit = np.zeros(RATE)
        hit[:len(burst)] = 0.5 * burst
        x += np.roll(hit, int(0.995 * RATE))                                         # the main hit, across the join
        r = readout.readout(x, RATE, loop=True, peak_at=0.003)
        self.assertEqual([e['time_s'] for e in r['events'] if e['main']], [0.995], r['events'])


class ReviewCases5(unittest.TestCase):
    """Cases from the fifth review of 0.3."""

    def test_a_pitch_held_in_one_channel_keeps_its_line_when_the_other_glides_away(self):
        t = np.arange(int(1.5 * RATE)) / RATE
        hz = np.where(t < 0.25, 440.0, np.where(t < 1.0, 440.0 * 2 ** ((t - 0.25) / 0.75), 880.0))
        x = np.stack([0.4 * np.sin(2 * np.pi * 440 * t), 0.4 * np.sin(2 * np.pi * np.cumsum(hz) / RATE)], axis=1)
        r = readout.readout(x * np.minimum(1, np.minimum(t, 1.5 - t) / 0.005)[:, None], RATE)
        held = [line for line in r['lines'] if abs(line['low_hz'] - 440) < 10 and abs(line['high_hz'] - 440) < 10
                and line['end_s'] - line['start_s'] > 1.2]
        self.assertEqual(len(held), 1, r['lines'])
        self.assertTrue(any(line['high_hz'] > 860 and line['low_hz'] < 460 for line in r['lines']), r['lines'])


class SliceCells(unittest.TestCase):
    """A cell is the band's power over its slice, one measurement per slice, not the band's loudest millisecond in it."""

    @staticmethod
    def _sound(tone_ends_ms):
        x = np.zeros(int(0.9 * RATE))
        x[:int(0.2 * RATE)] += faded(tone(250, 0.2, 0.6))           # the loudest cell, the same in both sounds
        a, b = int(0.3 * RATE), int(tone_ends_ms / 1000 * RATE)
        x[a:b] += faded(tone(2000, (b - a) / RATE, 0.05))
        return x

    @staticmethod
    def _cell(r, label, band):
        row = next(s for s in r['bands']['slices'] if s['label'] == label)
        return row['db'][readout.LABELS.index(band)]

    def test_a_band_sounding_through_half_a_slice_reads_3_db_under_one_sounding_through_all_of_it(self):
        whole = self._cell(readout.readout(self._sound(700), RATE), '320-640 ms', '2k')
        half = self._cell(readout.readout(self._sound(480), RATE), '320-640 ms', '2k')
        self.assertAlmostEqual(whole - half, 3, delta=1)

    def test_a_brief_click_in_a_long_slice_is_averaged_in_its_row_and_named_as_an_event(self):
        x = self._sound(700)
        x[int(0.85 * RATE):int(0.85 * RATE) + 48] += 0.5            # 1 ms, late in the last slice
        r = readout.readout(x, RATE)
        self.assertTrue(any(abs(e['time_s'] - 0.85) < 0.003 for e in r['events']), r['events'])
        self.assertLess(self._cell(r, '640-900 ms', '4k'), self._cell(r, '320-640 ms', '2k') - 15)  # a held tone outweighs it

    def test_a_band_above_the_sample_rate_never_sets_the_reference(self):
        rate = 22625                                                  # 16 kHz is n/a here, yet the split leaves it energy
        x = np.zeros(1811)
        x[0], x[-1] = 0.05, 0.0625
        x[1787:1810] = np.resize([-0.5, 0.5], 23)
        r = readout.readout(x, rate)
        cells = [v for s in r['bands']['slices'] for v in s['db'] if isinstance(v, int)]
        self.assertEqual(max(cells), 0)

    def test_digital_silence_before_a_tiny_sound_stays_under_the_floor(self):
        x = np.zeros(int(0.05 * RATE))
        x[480] = 12 / 2 ** 23                                         # 12 steps of a 24-bit file, at 10 ms
        before = readout.readout(x, RATE)['bands']['slices'][0]
        self.assertEqual(before['label'], 'before')
        self.assertTrue(all(v is None for v in before['db']), before['db'])

    def test_the_loudest_cell_reads_0_db_even_for_noise(self):
        rng = np.random.default_rng(5)                                # its loudest millisecond stands over any slice's power
        r = readout.readout(faded(rng.standard_normal(RATE) * 0.2), RATE)
        cells = [v for s in r['bands']['slices'] for v in s['db'] if isinstance(v, int)]
        self.assertEqual(max(cells), 0)


if __name__ == '__main__':
    unittest.main()
