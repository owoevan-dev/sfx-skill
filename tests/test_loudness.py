"""Loudness and true peak: known answers from the definitions, and FFmpeg's ebur128 on the same files."""
import re
import subprocess
import unittest

import numpy as np

import helpers
from sfxlib import loudness, wav

RATE = 48000


def tone(freq, seconds, amp=1.0, phase=0.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return amp * np.sin(2 * np.pi * freq * t + phase)


class KnownAnswers(unittest.TestCase):
    def test_full_scale_997_hz_reads_minus_3_01(self):
        # BS.1770: a 0 dBFS sine near 1 kHz in one channel reads -3.01
        self.assertAlmostEqual(loudness.integrated(tone(997, 2.0)), -3.01, delta=0.05)
        self.assertAlmostEqual(loudness.max_momentary(tone(997, 2.0)), -3.01, delta=0.05)

    def test_a_short_sound_reads_lower_by_its_share_of_the_400_ms_window(self):
        short = tone(997, 0.1)
        self.assertAlmostEqual(loudness.max_momentary(short), -3.01 + 10 * np.log10(0.1 / 0.4), delta=0.1)

    def test_max_momentary_finds_the_loudest_window_wherever_it_starts(self):
        quiet = tone(997, 1.0, amp=0.01)
        loud = tone(997, 0.4, amp=1.0)
        sig = np.concatenate([quiet[:12345], loud, quiet])
        self.assertAlmostEqual(loudness.max_momentary(sig), -3.01, delta=0.05)

    def test_true_peak_of_a_quarter_rate_sine_at_45_degrees(self):
        # samples land at +-0.707 while the waveform between them reaches 1.0; the ends are faded in and out, since a
        # sine cut off abruptly really does overshoot near the cut (the reconstruction rings there)
        sig = tone(RATE / 4, 1.0, amp=1.0, phase=np.pi / 4)
        fade = np.sin(np.linspace(0, np.pi / 2, 4800)) ** 2
        sig[:4800] *= fade
        sig[-4800:] *= fade[::-1]
        self.assertAlmostEqual(20 * np.log10(np.max(np.abs(sig))), -3.01, delta=0.01)
        self.assertAlmostEqual(loudness.true_peak_db(sig), 0.0, delta=0.1)

    def test_true_peak_is_at_least_the_sample_peak(self):
        rng = np.random.default_rng(1)
        sig = 0.5 * rng.standard_normal(4800)
        self.assertGreaterEqual(loudness.true_peak_db(sig) + 1e-9, 20 * np.log10(np.max(np.abs(sig))))

    def test_silence_has_no_loudness(self):
        self.assertIsNone(loudness.integrated(np.zeros(48000)))
        self.assertEqual(loudness.max_momentary(np.zeros(48000)), -np.inf)
        self.assertEqual(loudness.true_peak_db(np.zeros(100)), -np.inf)


@unittest.skipUnless(helpers.ffmpeg_or_none(), 'no FFmpeg')
class AgainstFFmpeg(unittest.TestCase):
    def ebur128(self, path):
        ffmpeg = helpers.ffmpeg_or_none()
        proc = subprocess.run([str(ffmpeg), '-hide_banner', '-nostats', '-i', str(path), '-filter_complex',
                               'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env(), timeout=120)
        log = proc.stderr
        summary = log[log.rindex('Summary:'):]
        integrated = float(re.search(r'I:\s+(-?[\d.]+) LUFS', summary).group(1))
        peak = float(re.search(r'True peak:\s+Peak:\s+(-?[\d.]+) dBFS', summary).group(1))
        moments = [float(v) for v in re.findall(r'M:\s*(-?[\d.]+)', log[:log.rindex('Summary:')])]
        return integrated, peak, max(moments)

    def test_integrated_true_peak_and_momentary_agree(self):
        base = helpers.scratch('ffmpeg_loudness')
        rng = np.random.default_rng(5)
        burst = rng.standard_normal(RATE) * np.exp(-np.arange(RATE) / (0.2 * RATE)) * 0.3
        sig = np.concatenate([np.zeros(RATE // 10), burst + tone(440, 1.0, 0.2) + tone(3150, 1.0, 0.05),
                              tone(997, 1.0, 0.1)])
        path = base / 'mix.wav'
        wav.write(path, sig, bits=24, dither=False)
        data, _ = wav.read(path)
        x = data[:, 0].astype(np.float64)
        ff_i, ff_tp, ff_m = self.ebur128(path)
        self.assertAlmostEqual(loudness.integrated(x), ff_i, delta=0.1)
        self.assertAlmostEqual(loudness.true_peak_db(x), ff_tp, delta=0.2)
        # FFmpeg reports momentary every 100 ms, so its maximum can only be at or under ours
        self.assertLessEqual(ff_m, loudness.max_momentary(x) + 0.05)
        self.assertGreater(ff_m, loudness.max_momentary(x) - 0.5)


if __name__ == '__main__':
    unittest.main()
