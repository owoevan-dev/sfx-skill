"""Variants (seeded parameter draws) and the set's level step."""
import math
import unittest

import helpers  # noqa: F401
import test_project  # registers test.beep
from sfxlib import level, project, variants


def beep_sound(**over):
    p = project.validate(test_project.minimal())
    sound = p['sounds'][0]
    sound.update(over)
    return p, sound


class Variants(unittest.TestCase):
    def test_the_first_variant_keeps_the_written_values(self):
        p, sound = beep_sound()
        params, drawn = variants.recipe_params(sound, p['seed'], 0)
        self.assertEqual(params, sound['params'])
        self.assertTrue(all(u == 0.0 for u in drawn.values()))

    def test_draws_repeat_and_differ_between_variants(self):
        p, sound = beep_sound()
        a, _ = variants.recipe_params(sound, p['seed'], 2)
        b, _ = variants.recipe_params(sound, p['seed'], 2)
        c, _ = variants.recipe_params(sound, p['seed'], 3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_draws_stay_within_their_amounts_and_ranges(self):
        p, sound = beep_sound()
        for v in range(1, 40):
            params, drawn = variants.recipe_params(sound, p['seed'], v)
            cents = 1200 * math.log2(params['base_hz'] / sound['params']['base_hz'])
            self.assertLessEqual(abs(cents), 30.0 + 1e-9)
            self.assertLessEqual(abs(params['length'] / sound['params']['length'] - 1), 0.05 + 1e-12)
            self.assertLessEqual(abs(params['brightness'] - 0.5), 0.05 + 1e-12)
        _, edge = beep_sound(params={'base_hz': 5000.0, 'length': 0.1, 'brightness': 1.0})
        for v in range(1, 20):
            params, _ = variants.recipe_params(edge, p['seed'], v)
            self.assertLessEqual(params['base_hz'], 5000.0)
            self.assertLessEqual(params['brightness'], 1.0)

    def test_a_zero_amount_fixes_a_parameter(self):
        p, sound = beep_sound()
        sound['vary'] = dict(sound['vary'], base_hz=0.0)
        for v in range(1, 10):
            self.assertEqual(variants.recipe_params(sound, p['seed'], v)[0]['base_hz'], sound['params']['base_hz'])

    def test_each_parameter_has_its_own_stream(self):
        p, sound = beep_sound()
        full, _ = variants.recipe_params(sound, p['seed'], 5)
        sound['vary'] = {'base_hz': sound['vary']['base_hz']}   # the others fixed
        alone, _ = variants.recipe_params(sound, p['seed'], 5)
        self.assertEqual(full['base_hz'], alone['base_hz'])

    def test_layer_sounds_scale_pitch_time_and_cutoff(self):
        layers = [{'source': {'type': 'osc', 'wave': 'sine'}, 'start': 0.01, 'length': 0.1,
                   'pitch': [[0, 400.0], [0.1, 800.0]], 'env': [[0, 1.0], [0.1, 0.0]],
                   'filter': {'type': 'lowpass', 'cutoff': 2000.0}}]
        vary = {'pitch_cents': 100.0, 'time_pct': 10.0, 'cutoff_cents': 200.0}
        spec, drawn = variants.layer_spec({'layers': layers, 'chain': {}, 'vary': vary, 'id': 's'}, 9, 4)
        f = 2 ** (drawn['pitch_cents'] / 1200)
        t = 1 + drawn['time_pct'] / 100
        self.assertAlmostEqual(spec['layers'][0]['pitch'][1][1], 800.0 * f)
        self.assertAlmostEqual(spec['layers'][0]['length'], 0.1 * t)
        self.assertAlmostEqual(spec['layers'][0]['start'], 0.01 * t)
        self.assertAlmostEqual(spec['layers'][0]['env'][1][0], 0.1 * t)
        self.assertAlmostEqual(spec['layers'][0]['filter']['cutoff'], 2000.0 * 2 ** (drawn['cutoff_cents'] / 1200))
        same, _ = variants.layer_spec({'layers': layers, 'chain': {}, 'vary': vary, 'id': 's'}, 9, 0)
        self.assertEqual(same['layers'], layers)


class Level(unittest.TestCase):
    def test_match_brings_every_file_to_one_loudness_under_the_ceiling(self):
        files = [{'key': 'a', 'loudness': -20.0, 'true_peak': -6.0, 'trim_db': 0.0},
                 {'key': 'b', 'loudness': -30.0, 'true_peak': -10.0, 'trim_db': 0.0},
                 {'key': 'c', 'loudness': -25.0, 'true_peak': -3.0, 'trim_db': -6.0}]
        plan = level.plan(files, mode='match', ceiling=-1.0)
        after = {f['key']: (f['loudness'] + plan['gains'][f['key']], f['true_peak'] + plan['gains'][f['key']])
                 for f in files}
        self.assertAlmostEqual(after['a'][0], after['b'][0])
        self.assertAlmostEqual(after['c'][0], after['a'][0] - 6.0)
        self.assertLessEqual(max(tp for _, tp in after.values()), -1.0 + 1e-9)
        self.assertAlmostEqual(max(tp for _, tp in after.values()), -1.0)
        self.assertEqual(plan['limited_by'], 'b')     # the peakiest file sets the level for all

    def test_peak_mode_puts_each_true_peak_at_the_ceiling_less_its_trim(self):
        files = [{'key': 'a', 'loudness': -20.0, 'true_peak': -6.0, 'trim_db': 0.0},
                 {'key': 'b', 'loudness': -30.0, 'true_peak': -10.0, 'trim_db': -3.0}]
        plan = level.plan(files, mode='peak', ceiling=-1.0)
        self.assertAlmostEqual(-6.0 + plan['gains']['a'], -1.0)
        self.assertAlmostEqual(-10.0 + plan['gains']['b'], -4.0)

    def test_silence_cannot_be_levelled(self):
        with self.assertRaises(ValueError):
            level.plan([{'key': 'a', 'loudness': -math.inf, 'true_peak': -math.inf, 'trim_db': 0}], 'match', -1)

    def test_a_limit_lets_the_set_rise_and_says_how_far_each_file_passes_the_ceiling(self):
        files = [{'key': 'steady', 'loudness': -20.0, 'true_peak': -10.0, 'trim_db': 0.0},
                 {'key': 'peaky', 'loudness': -30.0, 'true_peak': -5.0, 'trim_db': 0.0}]
        plain = level.plan(files, mode='match', ceiling=-1.0)
        limited = level.plan(files, mode='match', ceiling=-1.0, limit_db=6.0)
        self.assertAlmostEqual(limited['target_lufs'], plain['target_lufs'] + 6.0)
        self.assertAlmostEqual(limited['over_db']['peaky'], 6.0)
        self.assertAlmostEqual(limited['over_db']['steady'], 0.0)     # under the ceiling: nothing to limit

    def test_the_render_limits_a_peaky_file_instead_of_lowering_the_set(self):
        from sfxlib import render
        import json
        import numpy as np
        from sfxlib import loudness, wav
        tone = {'id': 'tone', 'category': 'ui', 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.5, 'pitch': 440.0}]}
        # a hit: a 4 ms burst far above a body 20 dB under it (the body carries the loudness, the burst the peak)
        click = {'id': 'click', 'category': 'ui', 'layers': [
            {'source': {'type': 'noise', 'color': 'white'}, 'length': 0.004, 'env': [[0, 1.0], [0.004, 0.0]]},
            {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.3, 'pitch': 200.0, 'gain_db': -20.0}]}
        base = helpers.scratch('level_limit')
        out = {}
        for limit in (0.0, 6.0):
            proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1,
                                     'level': {'mode': 'match', 'ceiling_dbtp': -1.0, 'limit_db': limit},
                                     'sounds': [tone, click]})
            folder = base / f'limit_{int(limit)}'
            render.render(proj, folder)
            resolved = json.loads((folder / 'resolved.json').read_text(encoding='utf-8'))
            measured = {}
            for s in resolved['sounds']:
                x = wav.read(folder / s['variants'][0]['file'])[0].astype(np.float64)
                measured[s['id']] = (loudness.brief(x), loudness.true_peak_db(x),
                                     s['variants'][0].get('limited_db', 0.0))
            out[limit] = (resolved['level']['target_lufs'], measured)
        self.assertGreater(out[6.0][0], out[0.0][0] + 3.0)              # the set rose
        loud, peak, limited = out[6.0][1]['click']
        self.assertLessEqual(peak, -1.0 + 0.1)                         # the peaky file stays under the ceiling
        self.assertGreater(limited, 1.0)
        self.assertLessEqual(limited, 6.0 + 0.05)                      # by no more than the limit
        self.assertAlmostEqual(loud, out[6.0][1]['tone'][0], delta=0.1)  # and still matches the set's loudness

    def test_limiting_lopsided_sounds_leaves_no_offset(self):
        """A limiter takes more off a waveform's taller polarity (a driven narrow pulse), which leaves an offset that the
        render's own DC block, run before the level step, cannot see. The level step's limiter holds its gain across the
        wave, and a file whose limiting would still leave an offset the check flags is limited less: a moderately
        lopsided buzz is still limited hard and carries no offset; an extreme one carries none either. Both stay under
        the ceiling at the set's loudness."""
        from sfxlib import check, measure, render
        import json
        import numpy as np
        from sfxlib import loudness, wav

        def buzz(sid, hz, duty, drive):
            return {'id': sid, 'category': 'impact', 'layers': [
                {'source': {'type': 'osc', 'wave': 'pulse', 'duty': duty}, 'length': 1.0, 'pitch': hz, 'drive': drive,
                 'env': [[0, 0, 'cos'], [0.02, 1.0], [0.9, 1.0, 'cos'], [1.0, 0]]}]}
        tone = {'id': 'tone', 'category': 'ui', 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.5, 'pitch': 440.0}]}
        base = helpers.scratch('level_offset')
        for sid, sound, hard in (('moderate', buzz('moderate', 70.0, 0.2, 0.3), True),
                                 ('extreme', buzz('extreme', 40.0, 0.12, 0.6), False)):
            folder = base / sid
            render.render(project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1,
                                            'sounds': [tone, sound]}), folder)
            resolved = json.loads((folder / 'resolved.json').read_text(encoding='utf-8'))
            files = {s['id']: s['variants'][0] for s in resolved['sounds']}
            x = wav.read(folder / files[sid]['file'])[0].astype(np.float64)
            if hard:
                self.assertGreater(files[sid]['limited_db'], 3.0)
            self.assertLess(abs(measure.dc_offset(x)), check.DC_FLAG, sid)
            self.assertLessEqual(loudness.true_peak_db(x), -1.0 + 0.1)
            y = wav.read(folder / files['tone']['file'])[0].astype(np.float64)
            self.assertAlmostEqual(loudness.brief(x), loudness.brief(y), delta=0.1)

    def test_a_spike_cannot_be_made_louder_by_limiting_and_still_matches(self):
        """A spike that is nearly all peak loses loudness with every dB of peak it gives up, so the set's level
        barely rises; the files still meet one loudness."""
        from sfxlib import render
        import json
        import numpy as np
        from sfxlib import loudness, wav
        tone = {'id': 'tone', 'category': 'ui', 'layers': [
            {'source': {'type': 'osc', 'wave': 'sine'}, 'length': 0.5, 'pitch': 440.0}]}
        spike = {'id': 'spike', 'category': 'ui', 'layers': [
            {'source': {'type': 'noise', 'color': 'white'}, 'length': 0.4, 'env': [[0, 1.0], [0.002, 0.02, 'db'],
                                                                                  [0.4, 0.0]]}]}
        folder = helpers.scratch('level_spike') / 'render'
        proj = project.validate({'schema': 'sound-effects/1', 'title': 't', 'variants': 1, 'sounds': [tone, spike]})
        render.render(proj, folder)
        resolved = json.loads((folder / 'resolved.json').read_text(encoding='utf-8'))
        loud = [loudness.brief(wav.read(folder / s['variants'][0]['file'])[0].astype(np.float64))
                for s in resolved['sounds']]
        self.assertAlmostEqual(loud[0], loud[1], delta=0.1)


if __name__ == '__main__':
    unittest.main()
