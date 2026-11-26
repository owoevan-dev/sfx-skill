"""The readout: a sound as numbers an agent reads (octave bands over time, tonal lines, events, roll-off)."""
import json
import math
import os
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

    def test_a_short_loop_labels_its_slices_in_milliseconds(self):
        r = readout.readout(tone(1000, 0.06, 0.3), RATE, loop=True)    # twelve slices of 5 ms
        labels = [s['label'] for s in r['bands']['slices']]
        self.assertTrue(all(label.endswith(' ms') for label in labels), labels)
        self.assertEqual(len(set(labels)), len(labels), labels)

    def test_the_loudest_cell_reads_0_db_even_for_noise(self):
        rng = np.random.default_rng(5)                                # its loudest millisecond stands over any slice's power
        r = readout.readout(faded(rng.standard_normal(RATE) * 0.2), RATE)
        cells = [v for s in r['bands']['slices'] for v in s['db'] if isinstance(v, int)]
        self.assertEqual(max(cells), 0)


class Core(unittest.TestCase):
    """The one reading every view shares: audio and numbers in, nothing about files."""

    def test_the_analysis_keeps_every_event_while_the_overview_shows_ten(self):
        ping = faded(tone(3000, 0.01, 0.5), 1)
        x = np.zeros(int(2.6 * RATE))
        for k in range(12):
            start = int((0.05 + 0.2 * k) * RATE)
            x[start:start + len(ping)] += ping
        a = readout.analyse(x, RATE)
        self.assertEqual(len(a.events_all), 12)
        self.assertEqual(len(readout.view_overview(a)['events']), readout.EVENTS_SHOWN)

    def test_each_channel_keeps_its_own_bands(self):
        left, right = faded(tone(250, 0.5, 0.4)), faded(tone(4000, 0.5, 0.4))
        a = readout.analyse(np.stack([left, right], axis=1), RATE)
        low, high = readout.LABELS.index('250'), readout.LABELS.index('4k')
        self.assertGreater(a.channel_env[0, low].sum(), 1000 * a.channel_env[0, high].sum())
        self.assertGreater(a.channel_env[1, high].sum(), 1000 * a.channel_env[1, low].sum())
        both = readout.band_envelopes(np.stack([left, right], axis=1), RATE)
        self.assertTrue(np.allclose(a.channel_env.sum(axis=0), both))

    def test_the_overview_leaves_the_lines_frame_data_in_the_analysis(self):
        a = readout.analyse(faded(tone(440, 0.5)), RATE)
        r = readout.view_overview(a)
        self.assertNotIn('hzs', r['lines'][0])
        self.assertIn('hzs', a.lines[0])


class Zoom(unittest.TestCase):
    """Layer 1: a window of the sound in finer slices, third octaves, one channel, pages."""

    def test_a_window_gets_slices_fine_enough_and_in_file_time(self):
        z = readout.view_zoom(readout.analyse(faded(tone(1000, 1.0, 0.4)), RATE), 0.2, 0.3)
        rows = z['bands']['slices']
        self.assertEqual(z['slice_ms'], 5)                            # 1 ms would give 100 rows and 2 ms 50
        self.assertLessEqual(len(rows), readout.ZOOM_ROWS)
        self.assertEqual((rows[0]['from_ms'], rows[-1]['to_ms']), (200, 300))
        z = readout.view_zoom(readout.analyse(faded(tone(1000, 1.0, 0.4)), RATE), 0.2, 0.307, slice_ms=10)
        self.assertEqual((z['to_s'], z['bands']['slices'][-1]['to_ms']), (0.31, 310))     # the last slice is whole

    def test_a_band_never_shows_finer_than_it_can_resolve(self):
        z = readout.view_zoom(readout.analyse(faded(tone(40, 1.0, 0.4)), RATE), 0.3, 0.33, slice_ms=1)
        low = [row['db'][readout.LABELS.index('31')] for row in z['bands']['slices']]
        high = [row['db'][readout.LABELS.index('4k')] for row in z['bands']['slices']]
        self.assertIn('^', low)                                        # about 22 Hz wide: some 90 ms to resolve
        self.assertNotIn('^', high)

    def test_every_event_in_the_window_is_listed(self):
        ping = faded(tone(3000, 0.01, 0.5), 1)
        x = np.zeros(int(2.6 * RATE))
        for k in range(12):
            start = int((0.05 + 0.2 * k) * RATE)
            x[start:start + len(ping)] += ping
        self.assertEqual(len(readout.view_zoom(readout.analyse(x, RATE), 0.0, 2.6)['events']), 12)

    def test_a_line_already_sounding_at_the_left_edge_is_listed(self):
        z = readout.view_zoom(readout.analyse(faded(tone(440, 1.0, 0.4)), RATE), 0.5, 0.6)
        self.assertTrue(any(abs(line['hz'] - 440) < 2 for line in z['lines']), z['lines'])

    def test_third_octave_bands_add_up_to_the_sound(self):
        freqs = np.geomspace(5, 24000, 2000)
        total = readout.band_weights(freqs, readout.THIRD_CENTRES, readout.CROSS_OCT / 3).sum(axis=0)
        self.assertTrue(np.allclose(total, 1.0, atol=1e-9))

    def test_third_octaves_and_a_range_pick_the_bands(self):
        z = readout.view_zoom(readout.analyse(faded(tone(1000, 0.5, 0.4)), RATE), 0.1, 0.2, bands='third', hz=(500, 2000))
        self.assertEqual(z['bands']['labels'], ['500', '630', '800', '1k', '1.25k', '1.6k', '2k'])

    def test_one_channel_reads_only_that_channel(self):
        left, right = faded(tone(250, 0.5, 0.4)), faded(tone(4000, 0.5, 0.4))
        z = readout.view_zoom(readout.analyse(np.stack([left, right], axis=1), RATE), 0.1, 0.2, channel='left')
        self.assertTrue(all(row['db'][readout.LABELS.index('4k')] is None for row in z['bands']['slices']))
        low = [row['db'][readout.LABELS.index('250')] for row in z['bands']['slices']]     # some rows read coarser: '^'
        self.assertTrue(all(isinstance(v, int) or v == '^' for v in low) and any(isinstance(v, int) for v in low), low)

    def test_no_cell_reads_above_0_db(self):
        rng = np.random.default_rng(6)
        z = readout.view_zoom(readout.analyse(faded(rng.standard_normal(RATE) * 0.2), RATE), 0.31, 0.37, slice_ms=1)
        self.assertLessEqual(max(v for row in z['bands']['slices'] for v in row['db'] if isinstance(v, int)), 0)

    def test_a_long_window_pages_within_the_budget(self):
        rng = np.random.default_rng(7)
        z = readout.view_zoom(readout.analyse(faded(rng.standard_normal(6 * RATE) * 0.2), RATE), 0.0, 6.0, bands='third')
        pages = readout.text_zoom(z, 'noise')
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len('\n'.join(page)), readout.PAGE_CHARS)
            self.assertLessEqual(len(page), readout.PAGE_LINES)
        self.assertIn('--page 2', '\n'.join(pages[0]))
        self.assertIn('time from file start', '\n'.join(pages[1]))      # a page among the rows names the columns again

    def test_a_quiet_window_reads_under_the_loud_part_of_the_sound(self):
        decay = tone(1000, 1.0, 0.5) * np.exp(-np.arange(RATE) / (0.15 * RATE))
        z = readout.view_zoom(readout.analyse(faded(decay), RATE), 0.8, 0.9)
        self.assertLess(max(v for row in z['bands']['slices'] for v in row['db'] if isinstance(v, int)), -10)

    def test_a_loop_window_may_cross_its_join(self):
        z = readout.view_zoom(readout.analyse(tone(63, 2.0, 0.4), RATE, loop=True), 1.9, 2.1)
        rows = z['bands']['slices']
        self.assertEqual((rows[0]['from_ms'], rows[-1]['to_ms']), (1900, 2100))
        self.assertTrue(all(isinstance(row['db'][readout.LABELS.index('63')], (int, str)) for row in rows))
        x = burst(np.zeros(2 * RATE), 0.0, hz=4000)                    # a burst at the loop's start comes back after its end
        z = readout.view_zoom(readout.analyse(x, RATE, loop=True), 1.9, 2.1)
        self.assertIsNone(cell(z, 1995, '4k'))
        self.assertIsInstance(cell(z, 2000, '4k'), int)

    def test_an_empty_window_is_refused(self):
        with self.assertRaises(ValueError):
            readout.view_zoom(readout.analyse(faded(tone(440, 0.5)), RATE), 0.3, 0.3)


def burst(x, at_s, hz=8000, ms=10, amp=0.3, rate=RATE):
    n, start = int(ms * rate / 1000), int(round(at_s * rate))
    x[start:start + n] += faded(amp * np.sin(2 * np.pi * hz * np.arange(n) / rate), 1)
    return x


def cell(z, from_ms, band):
    row = next(r for r in z['bands']['slices'] if r['from_ms'] == from_ms)
    return row['db'][z['bands']['labels'].index(band)]


class ZoomReview(unittest.TestCase):
    """Cases from the zoom's first review."""

    def test_a_cell_reads_the_same_whatever_window_holds_it(self):
        x = burst(burst(np.zeros(int(0.6 * RATE)), 0.105, amp=0.6), 0.305)   # the louder burst off the 10 ms grid
        a = readout.analyse(x, RATE)
        early = readout.view_zoom(a, 0.095, 0.395, slice_ms=10)              # holds the louder burst whole
        late = readout.view_zoom(a, 0.295, 0.595, slice_ms=10)               # does not hold it
        self.assertEqual(cell(early, 305, '8k'), cell(late, 305, '8k'))

    def test_a_coarse_band_is_measured_over_its_whole_span(self):
        a = readout.analyse(faded(tone(40, 1.0, 0.4)), RATE)
        one, two = readout.view_zoom(a, 0.3, 0.301, slice_ms=1), readout.view_zoom(a, 0.308, 0.309, slice_ms=1)
        self.assertLessEqual(abs(cell(one, 300, '31') - cell(two, 308, '31')), 1)
        self.assertEqual((one['coarse_ms']['31'], one['coarse_ms']['250']), (90, 12))
        self.assertNotIn('4k', one['coarse_ms'])
        x = np.zeros(RATE)
        x[int(0.35 * RATE):] = faded(tone(40, 0.65, 0.4))              # comes in 50 ms into the 90 ms cell
        b = readout.analyse(x, RATE)
        energy = b.env[0] * b.counts                                   # the 31 Hz band's energy in each millisecond
        loudest = max(energy[s:s + 90].sum() for s in range(len(energy) - 89))
        fine = readout.view_zoom(b, 0.3, 0.301, slice_ms=1)
        self.assertEqual(cell(fine, 300, '31'), int(round(10 * math.log10(energy[300:390].sum() / loudest))))

    def test_a_steady_low_loop_reads_0_db(self):
        z = readout.view_zoom(readout.analyse(tone(30, 0.9, 0.4), RATE, loop=True), 0.1, 0.5)
        self.assertEqual(max(v for r in z['bands']['slices'] for v in r['db'] if isinstance(v, int)), 0)

    def test_a_loop_a_fraction_of_a_millisecond_long_wraps_at_its_true_length(self):
        x = np.zeros(4824)                                            # 100.5 ms
        x[:48] = 0.3 * np.sin(2 * np.pi * 8000 * np.arange(48) / RATE)   # its first millisecond comes back at 100.5-101.5
        z = readout.view_zoom(readout.analyse(x, RATE, loop=True), 0.095, 0.105, slice_ms=1)
        self.assertEqual([isinstance(cell(z, ms, '8k'), int) for ms in (99, 100, 101, 102)], [False, True, True, False])
        self.assertEqual([e['time_s'] for e in z['events']], [0.1005])

    def test_the_window_snaps_to_whole_milliseconds_and_keeps_out_what_lies_outside(self):
        ping = faded(tone(3000, 0.01, 0.5), 1)
        x = np.zeros(int(2.6 * RATE))
        for k in range(12):
            start = int((0.05 + 0.2 * k) * RATE)
            x[start:start + len(ping)] += ping
        a = readout.analyse(x, RATE)
        self.assertEqual([e['time_s'] for e in readout.view_zoom(a, 0.4, 0.8)['events']], [0.45, 0.65])
        z = readout.view_zoom(a, 0.2504, 0.6504)                       # every millisecond the window touches is read
        self.assertEqual(z['from_s'], 0.25)                            # and shown as the window it reads
        self.assertEqual([e['time_s'] for e in z['events']], [0.25, 0.45, 0.65])

    def test_a_loops_repeat_counts_only_inside_the_window(self):
        a = readout.analyse(burst(np.zeros(int(0.12 * RATE)), 0.02, hz=3000, ms=5), RATE, loop=True)
        self.assertEqual(readout.view_zoom(a, 0.110, 0.140)['events'], [])
        self.assertEqual(len(readout.view_zoom(a, 0.110, 0.141)['events']), 1)

    def test_one_channel_lists_only_its_own_lines_and_events(self):
        right = np.zeros(int(0.6 * RATE))
        right[int(0.3 * RATE):] = faded(tone(1000, 0.3, 0.4))
        a = readout.analyse(np.stack([np.zeros_like(right), right], axis=1), RATE)
        left = readout.view_zoom(a, 0.2, 0.5, channel='left')
        self.assertEqual((left['lines'], left['events']), ([], []))
        both = readout.view_zoom(a, 0.2, 0.5, channel='right')
        self.assertTrue(both['lines'] and both['events'])

    def test_one_channel_is_cut_off_by_its_own_power(self):
        left = np.zeros(int(0.6 * RATE))
        left[int(0.3 * RATE)] = 0.5                                    # a click out of digital silence
        right = np.random.default_rng(9).standard_normal(len(left)) * 0.2
        z = readout.view_zoom(readout.analyse(np.stack([left, right], axis=1), RATE), 0.29, 0.3, slice_ms=1,
                              channel='left', hz=(4000, 16000))        # bands that read 1 ms slices as they are
        self.assertTrue(all(v is None for row in z['bands']['slices'] for v in row['db']), z['bands']['slices'])

    def test_a_line_is_not_listed_inside_its_silent_gap(self):
        x = np.zeros(int(0.5 * RATE))
        x[int(0.05 * RATE):int(0.2 * RATE)] = faded(tone(1000, 0.15, 0.4))
        x[int(0.3 * RATE):int(0.45 * RATE)] = faded(tone(1000, 0.15, 0.4))
        a = readout.analyse(x, RATE)
        self.assertEqual(readout.view_zoom(a, 0.245, 0.255)['lines'], [])
        self.assertEqual(len(readout.view_zoom(a, 0.35, 0.36)['lines']), 1)

    def test_third_octave_cells_follow_the_signal(self):
        z = readout.view_zoom(readout.analyse(faded(tone(1250, 0.5, 0.4)), RATE), 0.1, 0.2, bands='third')
        self.assertIsInstance(cell(z, 100, '1.25k'), int)               # inside the 1 kHz octave, outside its third
        self.assertIsNone(cell(z, 100, '1k'))
        self.assertIsNone(cell(z, 100, '1.6k'))

    def test_crossfades_wider_than_the_band_spacing_are_refused(self):
        with self.assertRaises(ValueError):
            readout.band_weights(np.geomspace(20, 20000, 100), (800.0, 1000.0, 1250.0), 1.0)

    def test_pages_leave_room_for_a_blank_line_and_long_page_numbers(self):
        end = len(os.linesep)                                          # as printed: two characters on Windows

        def note(digits):
            return len(f'  page {"9" * digits} of {"9" * digits}: --page {"9" * digits} goes on')

        def filling(room, ends, times):                                # lines that fill room exactly, each with its end
            k = room // (120 + ends)
            return (['x' * 120] * k + ['y' * (room - k * (120 + ends) - ends)]) * times

        under_two = readout.PAGE_CHARS - (1 + end) - end - (note(2) + end)     # a page's body under a two-digit note
        under_one = readout.PAGE_CHARS - (1 + 1) - 1 - (note(3) + 1)            # ... and under one-character line ends
        for body in (['x'] * 100, filling(under_two, end, 110), filling(under_one, 1, 30)):
            pages = readout._pages(['h'], body)                        # full by lines; by characters past page 99; by
            self.assertGreater(len(pages), 3)                          # characters where a line's end is two
            for page in pages:
                printed = page + ['']                                  # sfx.py prints a blank line after each page
                self.assertLessEqual(len(printed), readout.PAGE_LINES)
                self.assertLessEqual(sum(len(line) + end for line in printed), readout.PAGE_CHARS)

    def test_bad_windows_slices_and_channels_are_refused_even_for_silence(self):
        silent = readout.analyse(np.zeros(RATE), RATE)
        for args, kwargs in [((0.3, 0.2), {}), ((0.1, 0.2), {'channel': 'right'}), ((0.1, 0.2), {'slice_ms': 0}),
                             ((0.1, math.inf), {})]:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(ValueError):
                readout.view_zoom(silent, *args, **kwargs)


class ZoomReview2(unittest.TestCase):
    """Cases from the zoom's second review."""

    def test_a_loops_reference_looks_two_turns_in(self):
        x = np.zeros(4812)                                            # 100.25 ms: each turn falls differently on the grid
        start = int(round(0.0805 * RATE))
        x[start:start + 48] = 0.3 * np.sin(2 * np.pi * 8000 * np.arange(48) / RATE)
        z = readout.view_zoom(readout.analyse(x, RATE, loop=True), 0.1, 0.2, slice_ms=1)
        self.assertEqual(cell(z, 181, '8k'), 0)                         # the third turn's whole millisecond is not counted

    def test_no_window_of_a_loop_reads_above_0_db_and_its_loudest_reads_0(self):
        x = np.zeros(4812)                                            # 100.25 ms: two turns end at 200.5 ms
        x[24:72] = 0.3 * np.sin(2 * np.pi * 8000 * np.arange(48) / RATE)       # a millisecond from 0.5 ms
        a = readout.analyse(x, RATE, loop=True)
        tops = []
        for lo in range(101):
            for part in (0.0002, 0.0009):
                if lo + part * 1000 < 100.25:
                    z = readout.view_zoom(a, lo / 1000 + part, lo / 1000 + part + 0.1002, slice_ms=1)
                    tops.append(max(v for r in z['bands']['slices'] for v in r['db'] if isinstance(v, int)))
        self.assertEqual(max(tops), 0)                                # no stretch from 201 ms, past two turns, counts

    def test_a_loops_milliseconds_round_to_the_nearest_sample(self):
        x = np.zeros(4410)                                            # 100 ms at 44.1 kHz
        x[264] = 0.5                                                  # millisecond 5 ends at sample round(6 * 44.1) = 265
        z = readout.view_zoom(readout.analyse(x, 44100, loop=True), 0.0, 0.01, slice_ms=1)
        self.assertEqual([isinstance(cell(z, ms, '8k'), int) for ms in (4, 5, 6)], [False, True, False])

    def test_a_window_is_checked_as_asked_before_it_is_snapped(self):
        quiet = readout.analyse(np.zeros(RATE), RATE)
        for args in ((0.0204, 0.0204), (0.0208, 0.0202), (0.0209999999995, 0.021)):      # times are read to the microsecond
            with self.subTest(args=args), self.assertRaises(ValueError):
                readout.view_zoom(quiet, *args)
        short = readout.analyse(burst(np.zeros(4812), 0.05), RATE, loop=True)          # 100.25 ms
        with self.assertRaises(ValueError):
            readout.view_zoom(short, 0.1003, 0.12)                      # starts past the loop's end
        whole = readout.analyse(burst(np.zeros(48048), 0.5), RATE, loop=True)          # 1.001 s
        with self.assertRaises(ValueError):
            readout.view_zoom(whole, 1.001, 1.002)                      # starts at its end
        turn = readout.analyse(burst(np.zeros(int(0.12 * RATE)), 0.02, hz=3000, ms=5), RATE, loop=True)
        z = readout.view_zoom(turn, 0.0205, 0.1405)                     # exactly one turn
        self.assertEqual((z['from_s'], z['to_s']), (0.02, 0.145))
        with self.assertRaises(ValueError):
            readout.view_zoom(turn, 0.02, 0.1405)                       # longer than one turn

    def test_a_window_past_a_one_shots_end_is_refused_and_its_last_slice_counts_silence(self):
        short = readout.analyse(burst(np.zeros(4812), 0.05), RATE)    # 100.25 ms
        with self.assertRaises(ValueError):
            readout.view_zoom(short, 0.1003, 0.1008)
        z = readout.view_zoom(short, 0.09, 0.2, slice_ms=1)           # its last millisecond holds a quarter of sound
        self.assertEqual((z['to_s'], z['bands']['slices'][-1]['to_ms'], z['seconds']), (0.101, 101, 0.10025))
        self.assertIn('the sound ends at 0.10025 s', readout.text_zoom(z, 'short')[0][0])
        a = readout.analyse(burst(np.zeros(int(0.1 * RATE)), 0.09), RATE)     # a burst in its last 10 ms, one 100 ms slice
        z = readout.view_zoom(a, 0.0, 0.1, slice_ms=100)
        self.assertEqual(max(v for v in z['bands']['slices'][0]['db'] if isinstance(v, int)), 0)

    def test_a_loop_window_past_its_end_says_so_to_the_microsecond(self):
        a = readout.analyse(burst(np.zeros(4836), 0.05), RATE, loop=True)         # 100.75 ms
        z = readout.view_zoom(a, 0.099, 0.101, slice_ms=1)
        self.assertEqual(z['seconds'], 0.10075)
        self.assertIn('round its join', readout.text_zoom(z, 'loop')[0][0])

    def test_a_silent_sounds_window_is_named(self):
        z = readout.view_zoom(readout.analyse(np.zeros(RATE), RATE), 0.2, 0.3)
        self.assertEqual((z['silent'], z['from_s'], z['to_s']), (True, 0.2, 0.3))
        self.assertIn('0.200-0.300 s', readout.text_zoom(z, 'quiet')[0][0])

    def test_a_loops_events_and_lines_are_listed_at_each_time_they_come_in_the_window(self):
        a = readout.analyse(burst(np.zeros(int(0.12 * RATE)), 0.02, hz=3000, ms=5), RATE, loop=True)
        self.assertEqual([e['time_s'] for e in readout.view_zoom(a, 0.110, 0.141)['events']], [0.14])
        self.assertEqual([e['time_s'] for e in readout.view_zoom(a, 0.01, 0.13, slice_ms=50)['events']], [0.02, 0.14])
        x = np.zeros(RATE // 2)
        x[int(0.05 * RATE):int(0.25 * RATE)] = faded(tone(1000, 0.2, 0.4))
        b = readout.analyse(x, RATE, loop=True)
        first = b.lines[0]['pieces'][0][0]
        lines = readout.view_zoom(b, 0.4, 0.9)['lines']
        self.assertEqual([line['start_s'] for line in lines], [round((first + len(x)) / RATE, 6)])
        lines = readout.view_zoom(b, 0.2, 0.7)['lines']                 # it comes twice
        self.assertEqual([line['start_s'] for line in lines], [round(first / RATE, 6), round((first + len(x)) / RATE, 6)])

    def test_a_loops_line_is_placed_to_the_sample(self):
        rate, n = 44100, 17872                                         # a turn of 405.26 ms
        x = np.zeros(n)
        x[4410:12789] = 0.4 * np.sin(2 * np.pi * 1000 * np.arange(8379) / rate)    # 1 kHz from 0.100 to 0.290 s
        a = readout.analyse(x, rate, loop=True)
        self.assertEqual(a.lines[0]['pieces'][0][0], 5060)            # the tracker's first frame of it, in samples
        self.assertEqual(readout.view_zoom(a, 0.4, 0.52)['lines'], [])   # it comes again at sample 22932, 0.520 s exactly
        line = readout.view_zoom(a, 0.4, 0.521)['lines'][0]
        self.assertEqual((line['start_s'], line['end_s']), (0.52, round((a.lines[0]['pieces'][-1][1] + n) / rate, 6)))

    def test_a_line_starting_inside_a_millisecond_is_listed_there(self):
        rate = 44100                                                   # frames fall between whole milliseconds
        x = np.zeros(int(0.4 * rate))
        n = int(0.2 * rate)
        x[int(0.1 * rate):int(0.1 * rate) + n] = 0.4 * np.sin(2 * np.pi * 1000 * np.arange(n) / rate)
        a = readout.analyse(x, rate)
        start_ms = a.lines[0]['pieces'][0][0] * 1000 / rate
        self.assertGreaterEqual(start_ms % 1, 0.5)
        z = readout.view_zoom(a, math.floor(start_ms) / 1000, (math.floor(start_ms) + 1) / 1000, slice_ms=1)
        self.assertEqual(len(z['lines']), 1)

    def test_one_channels_mains_label_counts_that_channels_active_part(self):
        left = np.zeros(2 * RATE)
        left[int(0.1 * RATE):int(0.8 * RATE)] = faded(tone(60, 0.7, 0.4))
        a = readout.analyse(np.stack([left, faded(tone(1000, 2.0, 0.4))], axis=1), RATE)
        z = readout.view_zoom(a, 0.2, 0.7, channel='left')
        self.assertTrue(any(line['mains'] for line in z['lines']), z['lines'])
        self.assertIn('at a mains frequency', '\n'.join(readout.text_zoom(z, 'hum')[0]))

    def test_changes_of_no_width_are_refused(self):
        for cross in (0.0, -0.1, math.nan, 5e-324):
            with self.subTest(cross=cross), self.assertRaises(ValueError):
                readout.band_weights(np.geomspace(20, 20000, 100), (800.0, 1000.0, 1250.0), cross)
        with self.assertRaises(ValueError):
            readout.band_weights(np.geomspace(20, 20000, 100), (800.0, math.nan, 1250.0), 1 / 18)

    def test_huge_values_are_clipped_or_refused_in_a_sentence(self):
        a = readout.analyse(burst(np.zeros(RATE), 0.3), RATE)
        self.assertEqual(readout.view_zoom(a, 0.0, 1e308)['to_s'], 1.0)      # a one-shot's window ends with the sound
        with self.assertRaises(ValueError):
            readout.view_zoom(a, 0.1, 0.2, slice_ms=2 ** 64)             # a slice longer than the window


def column(z, band):
    i = z['bands']['labels'].index(band)
    return [row['db'][i] for row in z['bands']['slices']]


def numbers(z):
    return [v for row in z['bands']['slices'] for v in row['db'] if isinstance(v, int)]


def toned_noise(seconds=0.5, seed=1):
    return faded(tone(880, seconds, 0.4) + 0.05 * np.random.default_rng(seed).standard_normal(int(seconds * RATE)))


class Compare(unittest.TestCase):
    """Layer 2, compare: this sound against another, on the overview's measures."""

    def test_the_same_sound_shows_no_difference(self):
        a = readout.analyse(toned_noise(), RATE)
        z = readout.view_compare(a, a)
        self.assertTrue(numbers(z) and all(v == 0 for v in numbers(z)))
        self.assertEqual([z['lines'][k] for k in ('changed', 'new', 'gone')], [[], [], []])
        self.assertEqual([z['events'][k] for k in ('changed', 'new', 'gone')], [[], [], []])

    def test_a_sound_6_db_quieter_reads_minus_6_wherever_both_sound(self):
        x = toned_noise()
        z = readout.view_compare(readout.analyse(x / 2, RATE), readout.analyse(x, RATE))
        self.assertTrue(numbers(z) and all(v == -6 for v in numbers(z)), numbers(z))
        self.assertEqual([round(line['db_change']) for line in z['lines']['changed']], [-6])
        self.assertEqual(z['events']['changed'], [])                   # rises are ratios: the same

    def test_a_shelf_shows_in_its_bands(self):
        noise = np.random.default_rng(2).standard_normal(RATE) * 0.1
        freqs = np.fft.rfftfreq(len(noise), 1 / RATE)
        bright = np.fft.irfft(np.fft.rfft(noise) * np.where(freqs >= 5657, 2.0, 1.0), len(noise))   # +6 dB from the 8 kHz octave
        z = readout.view_compare(readout.analyse(faded(bright), RATE), readout.analyse(faded(noise), RATE))
        self.assertEqual({v for v in column(z, '8k') + column(z, '16k') if isinstance(v, int)}, {6})
        self.assertEqual({v for v in column(z, '1k') if isinstance(v, int)}, {0})

    def test_a_line_20_cents_sharp_is_named(self):
        z = readout.view_compare(readout.analyse(faded(tone(440 * 2 ** (20 / 1200), 0.5, 0.4)), RATE),
                                 readout.analyse(faded(tone(440, 0.5, 0.4)), RATE))
        self.assertEqual([round(line['cents']) for line in z['lines']['changed']], [20])
        self.assertEqual((z['lines']['new'], z['lines']['gone']), ([], []))

    def test_one_of_a_repeated_note_taken_out_shows_as_gone(self):
        def beeps(times):
            x = np.zeros(int(1.2 * RATE))
            for t in times:
                x[int(t * RATE):int(t * RATE) + int(0.1 * RATE)] += faded(tone(660, 0.1, 0.4))
            return x

        z = readout.view_compare(readout.analyse(beeps([0.1, 0.9]), RATE), readout.analyse(beeps([0.1, 0.5, 0.9]), RATE))
        self.assertEqual([round(e['time_s'], 2) for e in z['events']['gone']], [0.5])
        self.assertEqual([round(line['start_s'], 1) for line in z['lines']['gone']], [0.5])
        self.assertEqual((z['events']['new'], z['lines']['new'], z['lines']['changed']), ([], [], []))

    def test_pitches_too_far_apart_are_a_new_line_and_a_gone_one(self):
        z = readout.view_compare(readout.analyse(faded(tone(480, 0.5, 0.4)), RATE),
                                 readout.analyse(faded(tone(440, 0.5, 0.4)), RATE))       # 151 cents
        self.assertEqual((len(z['lines']['new']), len(z['lines']['gone']), z['lines']['changed']), (1, 1, []))

    def test_what_both_hold_before_their_onsets_is_compared_over_the_same_stretch(self):
        lead = 0.0015 * np.random.default_rng(5).standard_normal(int(0.05 * RATE))      # under the onset's 40 dB
        x = np.concatenate([lead, faded(tone(660, 0.3, 0.4))])
        before = readout.view_compare(readout.analyse(x, RATE), readout.analyse(x * 0.5, RATE))['bands']['slices'][0]
        self.assertEqual((before['label'], {v for v in before['db'] if isinstance(v, int)}), ('before', {6}))
        padded = np.concatenate([np.zeros(int(0.15 * RATE)), x])                       # more silence before the same lead
        before = readout.view_compare(readout.analyse(padded, RATE), readout.analyse(x, RATE))['bands']['slices'][0]
        self.assertEqual({v for v in before['db'] if isinstance(v, int)}, {0})

    def test_the_floor_is_under_the_louder_sounds_loudest_cell_anywhere(self):
        n = int(0.3 * RATE)
        start = faded(0.04 * np.sin(2 * np.pi * 1000 * np.arange(n) / RATE)
                      + 0.4 * 10 ** (-66 / 20) * np.sin(2 * np.pi * 4000 * np.arange(n) / RATE))
        longer = np.concatenate([start, np.zeros(int(0.1 * RATE)), faded(tone(1000, 0.1, 0.4))])     # an accent after
        z = readout.view_compare(readout.analyse(longer, RATE), readout.analyse(start, RATE))
        self.assertTrue(all(v is None for v in column(z, '4k')), column(z, '4k'))       # 66 dB under that accent
        self.assertEqual((readout._bound('>+', 5.73), readout._bound('<-', 5.73)), ('>+5', '<-5'))    # never more than is so

    def test_loops_of_unequal_length_list_changes_only_where_both_play(self):
        x = burst(burst(np.zeros(RATE), 0.2, hz=3000, ms=5), 0.83, hz=3000, ms=5)
        x[int(0.7 * RATE):int(0.9 * RATE)] += faded(tone(1000, 0.2, 0.3))           # a line only the longer loop plays
        z = readout.view_compare(readout.analyse(x, RATE, loop=True), readout.analyse(x[:int(0.6 * RATE)], RATE, loop=True))
        listed = z['events']['new'] + z['events']['gone'] + z['lines']['new'] + z['lines']['gone']
        self.assertTrue(all(item.get('time_s', item.get('start_s')) < 0.6 for item in listed), listed)

    def test_lines_are_matched_where_they_sound_not_across_their_gaps(self):
        click = burst(np.zeros(int(0.7 * RATE)), 0.0, hz=3000, ms=3, amp=0.4)            # one onset for both
        a, b = readout.analyse(click, RATE), readout.analyse(click.copy(), RATE)

        def line(pieces_ms):
            return dict(hz=660.0, note='E5+2', start_s=pieces_ms[0][0] / 1000, end_s=pieces_ms[-1][1] / 1000, db=0.0,
                        median_hz=660.0, from_hz=660.0, to_hz=660.0, low_hz=660.0, high_hz=660.0, mains=False,
                        hzs=[660.0], covered_s=0.1, dbfs=-10.0, pieces=[[s * 48, e * 48] for s, e in pieces_ms])

        a.lines, b.lines = [line([(255, 340), (485, 570)])], [line([(390, 435)])]      # the other sounds inside the gap
        z = readout.view_compare(a, b)
        self.assertEqual((z['lines']['changed'], len(z['lines']['new']), len(z['lines']['gone'])), ([], 1, 1))

    def test_events_exactly_30_ms_apart_are_one_event_moved(self):
        def hits(at):
            return burst(burst(np.zeros(int(1.2 * RATE)), 0.0, hz=3000, ms=3, amp=0.4), at, hz=3000, ms=3, amp=0.4)

        z = readout.view_compare(readout.analyse(hits(1.003), RATE), readout.analyse(hits(1.033), RATE))
        self.assertEqual((z['events']['new'], z['events']['gone']), ([], []))
        self.assertEqual([e['shift_ms'] for e in z['events']['changed']], [-30])

    def test_a_line_changed_under_1_db_counts_as_the_same(self):
        x = faded(tone(440, 0.5, 0.3))
        z = readout.view_compare(readout.analyse(x * 10 ** (0.96 / 20), RATE), readout.analyse(x, RATE))
        self.assertEqual((z['lines']['changed'], z['lines']['same']), ([], 1))

    def test_bands_above_either_files_reach_read_na(self):
        slow, fast = 22050, 48000
        a = readout.analyse(0.3 * np.sin(2 * np.pi * 1000 * np.arange(slow // 2) / slow), slow)
        b = readout.analyse(0.3 * np.sin(2 * np.pi * 1000 * np.arange(fast // 2) / fast), fast)
        z = readout.view_compare(a, b)
        self.assertEqual(set(column(z, '16k')), {'n/a'})
        self.assertEqual({v for v in column(z, '1k') if isinstance(v, int)}, {0})

    def test_one_shots_are_aligned_at_their_onsets(self):
        x = faded(tone(660, 0.3, 0.4))
        late = np.concatenate([np.zeros(int(0.05 * RATE)), x])
        z = readout.view_compare(readout.analyse(late, RATE), readout.analyse(x, RATE))
        self.assertTrue(numbers(z) and all(v == 0 for v in numbers(z)), numbers(z))
        self.assertEqual((z['events']['changed'], z['lines']['changed']), ([], []))
        self.assertEqual(z['onset_s'], [0.05, 0.0])

    def test_a_longer_one_shots_tail_is_said(self):
        x = faded(tone(660, 0.6, 0.4))
        z = readout.view_compare(readout.analyse(x, RATE), readout.analyse(x[:int(0.3 * RATE)], RATE))
        self.assertEqual(z['common_s'], 0.3)
        self.assertIn('goes on', '\n'.join(readout.text_compare(z, 'tone', 'long', 'short')[0]))

    def test_loops_of_unequal_length_are_read_over_the_shorter(self):
        x = np.random.default_rng(3).standard_normal(2 * RATE) * 0.1
        z = readout.view_compare(readout.analyse(x, RATE, loop=True), readout.analyse(x[:int(1.5 * RATE)], RATE, loop=True))
        self.assertEqual((z['common_s'], z['seconds'], len(z['bands']['slices'])), (1.5, [2.0, 1.5], 12))
        self.assertIn('the first 1.5 s', '\n'.join(readout.text_compare(z, 'air', 'long', 'short')[0]))

    def test_a_band_only_one_side_sounds_in_reads_as_a_bound(self):
        low = faded(tone(250, 0.5, 0.4))
        z = readout.view_compare(readout.analyse(low + faded(tone(4000, 0.5, 0.4)), RATE), readout.analyse(low, RATE))
        self.assertTrue(all(isinstance(v, str) and v.startswith('>+') for v in column(z, '4k')[1:-1]), column(z, '4k'))
        z = readout.view_compare(readout.analyse(low, RATE), readout.analyse(low + faded(tone(4000, 0.5, 0.4)), RATE))
        self.assertTrue(all(isinstance(v, str) and v.startswith('<-') for v in column(z, '4k')[1:-1]), column(z, '4k'))

    def test_a_loop_and_a_one_shot_or_a_silent_sound_say_why_not(self):
        x = toned_noise()
        with self.assertRaises(ValueError):
            readout.view_compare(readout.analyse(x, RATE, loop=True), readout.analyse(x, RATE))
        z = readout.view_compare(readout.analyse(np.zeros(RATE), RATE), readout.analyse(x, RATE))
        self.assertEqual(z['silent'], ['this'])
        self.assertIn('silent', readout.text_compare(z, 'x', 'a', 'b')[0][0])

    def test_a_comparison_fits_one_page(self):
        x = toned_noise(2.0, 4)
        z = readout.view_compare(readout.analyse(x * 0.7, RATE), readout.analyse(x, RATE))
        within_a_page(self, readout.text_compare(z, 'noise', 'render_2', 'render_1'))
        freqs = 100 * 1.47 ** np.arange(12)

        def chord(cents):
            return faded(sum(tone(f * 2 ** (cents / 1200), 2.0, 0.05) for f in freqs))

        z = readout.view_compare(readout.analyse(chord(20), RATE), readout.analyse(chord(0), RATE))
        self.assertGreater(len(z['lines']['changed']), readout.LIST_SHOWN)      # a busy change still fits a page
        within_a_page(self, readout.text_compare(z, 'chord', 'render_2', 'render_1'))


def within_a_page(test, pages):
    """One page, which with the blank line after it keeps within the budget as printed."""
    test.assertEqual(len(pages), 1)
    printed = pages[0] + ['']
    test.assertLessEqual(len(printed), readout.PAGE_LINES)
    test.assertLessEqual(sum(len(line) + len(os.linesep) for line in printed), readout.PAGE_CHARS)


def decaying(freq, rate_db_s, seconds, amp=0.4, at=0.0, total=None):
    """A tone struck at `at` s, falling rate_db_s, in a file `total` s long (2 ms fade in)."""
    n = int((total or seconds) * RATE)
    x = np.zeros(n)
    t = np.arange(int(seconds * RATE)) / RATE
    y = amp * np.sin(2 * np.pi * freq * t) * 10 ** (-rate_db_s * t / 20)
    y[:96] *= np.linspace(0, 1, 96)
    start = int(at * RATE)
    x[start:start + len(y)] += y[:n - start]
    return x


def band(z, label):
    return next(row for row in z['bands'] if row['band'] == label)


class Decay(unittest.TestCase):
    """Layer 2, decay: how each octave band rises and fades."""

    def test_bands_fade_at_their_own_known_rates(self):
        x = decaying(1000, 60, 1.5) + decaying(4000, 120, 1.5)
        z = readout.view_decay(readout.analyse(x, RATE))
        low, high = band(z, '1k'), band(z, '4k')
        self.assertAlmostEqual(low['slope_db_s'], -60, delta=6)
        self.assertAlmostEqual(high['slope_db_s'], -120, delta=12)
        self.assertAlmostEqual(low['t60_s'], 1.0, delta=0.1)
        self.assertEqual((low['end']['kind'], high['end']['kind']), ('floor', 'floor'))
        self.assertAlmostEqual(high['end']['at_s'], 0.5, delta=0.06)

    def test_a_band_held_then_cut_has_no_fade(self):
        for hz, label in ((1000, '1k'), (125, '125')):                 # a low band's smoothing would read as a fade
            x = np.zeros(int(0.8 * RATE))
            x[:int(0.5 * RATE)] = tone(hz, 0.5, 0.4)
            x[:240] *= np.linspace(0, 1, 240)                          # no fade out: cut at 0.5 s
            row = band(readout.view_decay(readout.analyse(x, RATE)), label)
            with self.subTest(band=label):
                self.assertEqual((row['slope_db_s'], row['t60_s'], row['abrupt']), (None, None, True))
                self.assertEqual(row['end']['kind'], 'floor')
                self.assertAlmostEqual(row['end']['at_s'], 0.5, delta=0.06)

    def test_a_band_too_slow_for_a_short_sound_says_so(self):
        n = int(0.067 * RATE)
        t = np.arange(n) / RATE
        z = readout.view_decay(readout.analyse(faded(0.3 * np.sin(2 * np.pi * 63 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t)), RATE))
        self.assertTrue(band(z, '63')['too_short'])                    # it reads 100 ms at once
        self.assertNotIn('too_short', band(z, '1k'))
        self.assertIn('too short', '\n'.join(readout.text_decay(z, 'toggle')[0]))

    def test_a_fast_attack_from_silence_has_no_note_of_its_first_millisecond(self):
        text = '\n'.join(readout.text_decay(readout.view_decay(readout.analyse(decaying(1000, 60, 1.0), RATE)), 'tap')[0])
        self.assertNotIn('already', text)

    def test_no_time_is_read_past_a_short_sounds_end(self):
        n = int(0.067 * RATE)
        t = np.arange(n) / RATE
        z = readout.view_decay(readout.analyse(faded(0.3 * np.sin(2 * np.pi * 63 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t)), RATE))
        times = [row.get(k) for row in z['bands'] for k in ('peak_s',)] + [row.get('end', {}).get('at_s') for row in z['bands']]
        self.assertTrue(all(v is None or v <= z['seconds'] for v in times), times)

    def test_the_attack_leads_to_the_reported_peak(self):
        x = np.zeros(RATE)
        x[int(0.2 * RATE):int(0.5 * RATE)] = faded(tone(1000, 0.3, 0.24), 50)          # a broad hump
        x[int(0.7 * RATE):int(0.702 * RATE)] = tone(1000, 0.002, 0.4)                  # and a 2 ms spike, higher at 3 ms
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertLess(row['peak_s'], 0.6)
        self.assertLessEqual(row['attack_s'][1], row['peak_s'] + 0.01)

    def test_a_band_already_sounding_when_the_file_starts_says_so(self):
        t = np.arange(int(0.6 * RATE)) / RATE
        x = 0.4 * np.minimum(0.47 + t / 0.3 * 0.53, 1.0) * np.sin(2 * np.pi * 1000 * t)
        z = readout.view_decay(readout.analyse(x, RATE))
        self.assertAlmostEqual(band(z, '1k')['starts_at'], 0.47, delta=0.08)
        self.assertIn('already', '\n'.join(readout.text_decay(z, 'rec')[0]))

    def test_a_decline_into_a_sustain_levels_off_even_when_the_sustain_is_cut_later(self):
        t = np.arange(int(1.5 * RATE)) / RATE
        env = np.maximum(10 ** (-60 * t / 20), 10 ** (-15 / 20))
        env[t >= 1.2] = 0.0
        x = 0.4 * env * np.sin(2 * np.pi * 1000 * t)
        x[:96] *= np.linspace(0, 1, 96)
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'levels_off')
        self.assertAlmostEqual(row['end']['db'], -15, delta=3)
        self.assertAlmostEqual(row['slope_db_s'], -60, delta=6)

    def test_a_dip_that_swells_again_rises_and_does_not_level_off(self):
        t = np.arange(RATE) / RATE
        db = np.where(t < 0.4, -62.5 * t, np.minimum(-25 + (t - 0.4) * 50, -10))       # down 25 dB, back up to -10
        x = 0.4 * 10 ** (db / 20) * np.sin(2 * np.pi * 1000 * t)
        x[:96] *= np.linspace(0, 1, 96)
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'rises')
        self.assertAlmostEqual(row['end']['at_s'], 0.52, delta=0.06)

    def test_a_slow_fade_cut_by_the_files_end_does_not_level_off(self):
        self.assertEqual(band(readout.view_decay(readout.analyse(decaying(1000, 20, 0.85), RATE)), '1k')['end']['kind'], 'cut')

    def test_a_quiet_band_that_falls_under_the_floor_says_so(self):
        faint = np.zeros(int(0.5 * RATE))
        faint[:int(0.1 * RATE)] = faded(tone(8000, 0.1, 0.4 * 10 ** (-55 / 20)))
        row = band(readout.view_decay(readout.analyse(faded(tone(250, 0.5, 0.4)) + faint, RATE)), '8k')
        self.assertEqual(row['end']['kind'], 'floor')

    def test_a_decline_that_reaches_a_noise_floor_levels_off_there(self):
        noise = 0.0052 * np.random.default_rng(6).standard_normal(int(1.5 * RATE))    # about 50 dB under the tone in 1 kHz
        row = band(readout.view_decay(readout.analyse(decaying(1000, 60, 1.5) + noise, RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'levels_off')
        self.assertAlmostEqual(row['end']['db'], -50, delta=4)
        self.assertAlmostEqual(row['slope_db_s'], -60, delta=6)
        self.assertTrue(row['extrapolated'])                           # it fell some 50 dB, not 60

    def test_a_second_strike_ends_the_decline(self):
        x = decaying(1000, 60, 1.0) + decaying(1000, 60, 0.6, amp=0.28, at=0.4, total=1.0)   # a 21 dB rise, still quieter
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'strike')
        self.assertAlmostEqual(row['end']['at_s'], 0.4, delta=0.02)
        self.assertAlmostEqual(row['slope_db_s'], -60, delta=6)

    def test_a_reverse_swell_has_a_long_attack_and_no_decline(self):
        t = np.arange(RATE) / RATE
        row = band(readout.view_decay(readout.analyse(0.4 * t * np.sin(2 * np.pi * 1000 * t), RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'held')
        self.assertAlmostEqual(row['attack_ms'], 800, delta=40)       # 10 to 90 % of its amplitude

    def test_a_short_attack_is_read_from_10_to_90_percent(self):
        x = np.zeros(int(0.4 * RATE))
        x[:int(0.3 * RATE)] = tone(1000, 0.3, 0.4)
        x[:240] *= np.linspace(0, 1, 240)                              # 5 ms: 10 to 90 % takes 4 ms
        self.assertAlmostEqual(band(readout.view_decay(readout.analyse(x, RATE)), '1k')['attack_ms'], 4, delta=2)

    def test_a_band_with_nothing_in_it_is_said_to_be_under_the_floor(self):
        row = band(readout.view_decay(readout.analyse(decaying(1000, 60, 1.0), RATE)), '16k')
        self.assertTrue(row['under_floor'])

    def test_a_click_in_another_band_is_not_this_bands_strike(self):
        x = burst(decaying(1000, 60, 1.5), 0.3, hz=8000, ms=3, amp=0.3)
        self.assertEqual(band(readout.view_decay(readout.analyse(x, RATE)), '1k')['end']['kind'], 'floor')

    def test_a_decline_cut_by_the_files_end_says_where_it_was(self):
        row = band(readout.view_decay(readout.analyse(decaying(1000, 30, 0.6), RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'cut')
        self.assertAlmostEqual(row['end']['db'], -18, delta=3)

    def test_the_slope_is_read_where_the_band_first_falls(self):
        x = decaying(1000, 120, 1.5) + decaying(1150, 20, 1.5, amp=0.4 * 10 ** (-35 / 20))     # a long faint tail
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertAlmostEqual(row['slope_db_s'], -120, delta=12)

    def test_bands_above_the_files_reach_are_said(self):
        slow = 22050
        x = 0.4 * np.sin(2 * np.pi * 1000 * np.arange(slow) / slow) * 10 ** (-60 * np.arange(slow) / slow / 20)
        self.assertTrue(band(readout.view_decay(readout.analyse(x, slow)), '16k')['above'])

    def test_a_loop_has_no_decay(self):
        z = readout.view_decay(readout.analyse(toned_noise(), RATE, loop=True))
        self.assertTrue(z['loop'])
        self.assertNotIn('bands', z)
        self.assertIn('no decay', readout.text_decay(z, 'air')[0][0])

    def test_the_decay_fits_one_page(self):
        z = readout.view_decay(readout.analyse(decaying(1000, 60, 1.5) + 0.01 * toned_noise(1.5), RATE))
        pages = readout.text_decay(z, 'bell')
        within_a_page(self, pages)
        self.assertEqual(len(pages[0]), 2 + len(z['bands']))          # the head, the columns, one row a band
        self.assertIn('slope', '\n'.join(pages[0]))


class ReleaseReview(unittest.TestCase):
    """Cases from the release's final review."""

    def test_unequal_loops_compare_a_line_only_over_what_both_play(self):
        x = tone(440, 1.0, 0.1)
        x[int(0.72 * RATE):] *= 4                                      # 12 dB up, after the shorter loop has ended
        z = readout.view_compare(readout.analyse(x, RATE, loop=True), readout.analyse(tone(440, 0.6, 0.1), RATE, loop=True))
        self.assertEqual((z['lines']['same'], z['lines']['changed'], z['lines']['new'], z['lines']['gone']), (1, [], [], []))

    def test_no_frame_of_the_longer_loop_hears_past_what_both_play(self):
        x = tone(220, 1.0, 1.0)
        n = int(0.6 * RATE)
        x *= np.interp(np.arange(RATE), [0, n, n + int(0.03 * RATE), RATE], [0.1, 0.1, 0.4, 0.4])   # up 12 dB just after
        x = np.round(x * 32767) / 32767                                # as a 16-bit file holds it
        longer, shorter = readout.analyse(x, RATE, loop=True), readout.analyse(x[:n], RATE, loop=True)
        z = readout.view_compare(longer, shorter)
        self.assertEqual((z['lines']['same'], z['lines']['changed'], z['lines']['new'], z['lines']['gone']), (1, [], [], []))
        cut = readout._trimmed_lines(longer, 0.6)                      # its level under the whole loop's loudest peak
        self.assertAlmostEqual(cut[0]['db'], longer.lines[0]['db'] - 12, delta=0.5)

    def test_line_ends_moved_exactly_40_ms_count_as_the_same(self):
        def beep(at):
            x = np.zeros(2 * RATE)
            start = int(round(at * RATE))
            x[start:start + int(0.19 * RATE)] = faded(tone(660, 0.19, 0.4), 8)
            return readout.analyse(x, RATE, loop=True)

        z = readout.view_compare(beep(1.0375), beep(1.0))               # its tracked ends move 40 and 35 ms
        self.assertEqual((z['lines']['same'], z['lines']['changed']), (1, []))

    def test_the_floor_is_under_the_overviews_loudest_cell_not_a_shorter_slices(self):
        quiet = 0.4 * 10 ** (-62 / 20)                                 # 58 dB under the longer loop's loudest overview cell
        longer = tone(8000, 1.0, quiet)
        longer[int(0.05 * RATE):int(0.1 * RATE)] += tone(1000, 0.05, 0.4)      # an accent that one 50 ms slice holds whole
        shorter = tone(8000, 0.6, quiet)
        z = readout.view_compare(readout.analyse(longer, RATE, loop=True), readout.analyse(shorter, RATE, loop=True))
        self.assertEqual(set(column(z, '8k')[2:]), {0})                # after the accent, whose edges reach 8k themselves

    def test_silence_before_a_fade_in_is_not_a_band_already_sounding(self):
        x = np.zeros(int(0.5 * RATE))
        body = tone(63, 0.495, 0.4)
        body[:720] *= np.linspace(0, 1, 720)                           # 15 ms in, after 5 ms of digital silence
        x[240:] = body
        z = readout.view_decay(readout.analyse(x, RATE))
        self.assertNotIn('starts_at', band(z, '63'))
        self.assertNotIn('already', '\n'.join(readout.text_decay(z, 'hum')[0]))

    def test_a_dip_that_comes_back_before_the_fall_does_not_hold_the_decline(self):
        t = np.arange(int(1.2 * RATE)) / RATE
        db = np.interp(t, [0, 0.1, 0.2, 0.3, 1.0], [0, -8.8, -1, -1, -85])     # 8.8 dB down and back, then 84 dB down
        x = 0.4 * 10 ** (db / 20) * np.sin(2 * np.pi * 1000 * t)
        x[:96] *= np.linspace(0, 1, 96)
        row = band(readout.view_decay(readout.analyse(x, RATE)), '1k')
        self.assertEqual(row['end']['kind'], 'floor')
        self.assertAlmostEqual(row['slope_db_s'], -120, delta=12)

    def test_a_fit_that_does_not_fall_gives_no_slope_and_says_so(self):
        level = np.interp(np.arange(2400), [0, 100, 200, 300, 500, 510, 550, 551, 590, 600, 1200, 2200],
                          [0, -8, -1, -1, -1, -6.5, -6.5, -2.5, -2.5, -10.2, -10.2, -70])   # a dip that swells back; then
        row = readout._decline(level, 0, None, 14)                     # down, back near the peak, down to a long -10 dB
        self.assertEqual((row['slope_db_s'], row['t60_s'], row.get('unsteady')), (None, None, True))
        self.assertEqual(row['end']['kind'], 'levels_off')
        z = {'silent': False, 'loop': False, 'bands': [dict(row, band='1k', peak_s=0.0, peak_db=0.0, attack_ms=1)]}
        self.assertIn('no steady fall', '\n'.join(readout.text_decay(z, 'drone')[0]))

    def test_a_short_bump_back_near_the_peak_does_not_move_where_the_decline_starts(self):
        t = np.arange(RATE) / RATE
        db = -120 * t
        db[(t >= 0.12) & (t < 0.13)] = -4                              # 10 ms back within 5 dB of the peak, 14 dB down
        x = 0.4 * 10 ** (db / 20) * np.sin(2 * np.pi * 2000 * t)
        x[:96] *= np.linspace(0, 1, 96)
        self.assertAlmostEqual(band(readout.view_decay(readout.analyse(x, RATE)), '2k')['slope_db_s'], -120, delta=8)


if __name__ == '__main__':
    unittest.main()
