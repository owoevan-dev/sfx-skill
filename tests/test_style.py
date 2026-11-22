"""Game-wide style: a shared space and a tilt applied to every sound that does not opt out."""
import unittest

import helpers  # noqa: F401
from sfxlib import measure, project, synth, variants

BASE = {'schema': 'sound-effects/1', 'title': 't'}
TONE = {'id': 'tone', 'category': 'ui', 'layers': [{'source': {'type': 'noise', 'color': 'pink'}, 'length': 0.3}]}


def validated(style, *sounds):
    return project.validate(dict(BASE, style=style, sounds=list(sounds)))


class Style(unittest.TestCase):
    def test_the_space_goes_to_sounds_without_a_room_of_their_own(self):
        own = dict(TONE, id='own', chain={'room': {'size': 0.5, 'decay': 0.3, 'mix': 0.1}})
        dry = dict(TONE, id='dry', style=False)
        proj = validated({'space': {'size': 1.5, 'decay': 1.2, 'mix': 0.25}, 'tilt_db': -3.0}, TONE, own, dry)
        chains = {s['id']: variants.with_style(variants.spec_for(s, proj['seed'], 0)[0], proj['style'], s['style'])[
            'chain'] for s in proj['sounds']}
        self.assertEqual(chains['tone']['room'], {'size': 1.5, 'decay': 1.2, 'mix': 0.25})
        self.assertEqual(chains['tone']['tilt'], {'db': -3.0})
        self.assertEqual(chains['own']['room'], {'size': 0.5, 'decay': 0.3, 'mix': 0.1})
        self.assertEqual(chains['dry'], {})

    def test_a_positive_tilt_brightens(self):
        spec = {'layers': TONE['layers']}
        plain = synth.render_sound(spec, seed=1)
        bright = synth.render_sound(dict(spec, chain={'tilt': {'db': 6.0}}), seed=1)
        self.assertGreater(measure.centroid_hz(bright), measure.centroid_hz(plain) * 1.2)

    def test_style_values_are_checked(self):
        for style in ({'space': {'size': 9}}, {'tilt_db': 30}, {'spaces': {}}):
            with self.assertRaises(project.ProjectError, msg=str(style)):
                validated(style, TONE)
        with self.assertRaises(project.ProjectError):
            validated({}, dict(TONE, style='yes'))


if __name__ == '__main__':
    unittest.main()
