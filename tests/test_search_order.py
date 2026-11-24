"""A search: an order of the project's own within coarse bands of cleanliness, partial matches when few hold every word, music last."""
import json
import subprocess
import sys
import unittest

import helpers
from sfxlib import samples


def rec(name, title='', labels=(), clean=30.0, screen=(), seconds=1.0, tags=()):
    return {'library': 'fsd50k-eval', 'file': name, 'title': title or name, 'labels': list(labels), 'tags': list(tags), 'seconds': seconds,
            'channels': 1, 'measures': {'range_db': clean}, 'screen': list(screen)}


def library(*files):
    return {'packs': {'fsd50k-eval': {'title': 'A dataset', 'licence': 'CC0-1.0', 'files': len(files)}}, 'files': list(files)}


class OrderOfAProject(unittest.TestCase):
    index = library(*[rec(f'door_{i:02d}', labels=['Door'], clean=40.0 - i * 0.1) for i in range(30)])

    def names(self, project, text='door', index=None):
        return [f['file'] for f in samples.find(index or self.index, text, project=project)]

    def test_two_projects_start_from_different_recordings(self):
        self.assertNotEqual(self.names('hollow')[:5], self.names('ember')[:5])
        self.assertEqual(sorted(self.names('hollow')), sorted(self.names('ember')))

    def test_one_project_sees_the_same_order_each_time(self):
        self.assertEqual(self.names('hollow'), self.names('hollow'))
        shuffled = library(*reversed(self.index['files']))
        self.assertEqual(self.names('hollow', index=shuffled), self.names('hollow'))

    def test_the_cleanest_band_comes_first_in_every_project(self):
        index = library(*[rec(f'clean_{i}', labels=['Door'], clean=50.0) for i in range(6)], *[rec(f'noisy_{i}', labels=['Door'], clean=43.9) for i in range(6)])
        for project in ('a', 'b', 'c', 'd'):
            self.assertEqual({n[:5] for n in self.names(project, index=index)[:6]}, {'clean'}, project)

    def test_a_band_is_counted_down_from_the_best_of_its_rank(self):
        index = library(rec('best', labels=['Door'], clean=44.1), rec('close', labels=['Door'], clean=38.2), rec('next', labels=['Door'], clean=38.0))
        orders = {tuple(self.names(p, index=index)) for p in 'abcdefghij'}
        self.assertTrue(all(o[2] == 'next' for o in orders))                    # 6.1 dB under the best: the next band
        self.assertEqual({o[0] for o in orders}, {'best', 'close'})             # 44.1 and 38.2 lie within one band

    def test_cleanliness_above_the_cap_is_all_one(self):
        index = library(rec('a', labels=['Door'], clean=90.0), rec('b', labels=['Door'], clean=60.0))
        self.assertEqual({tuple(self.names(p, index=index))[0] for p in 'abcdefghij'}, {'a', 'b'})

    def test_passing_still_comes_before_failing(self):
        index = library(rec('fails', labels=['Door'], clean=59.0, screen=['clipped']), rec('passes', labels=['Door'], clean=10.0))
        self.assertEqual(self.names('x', index=index), ['passes', 'fails'])

    def test_where_the_word_is_still_comes_before_cleanliness(self):
        index = library(rec('thing', title='door slam', labels=['Door'], clean=10.0), rec('other', title='a slam', labels=['Door'], clean=50.0),
                        rec('tagged', title='a noise', tags=['door'], clean=59.0))
        self.assertEqual(self.names('x', index=index), ['thing', 'other', 'tagged'])


class SomeOfTheWords(unittest.TestCase):
    def test_few_full_matches_go_on_with_partial_ones_after_them(self):
        index = library(rec('heavy_door', labels=['Door']), rec('door_a', labels=['Door'], clean=59.0), rec('heavy_box'), rec('plain'))
        found = samples.find(index, 'heavy door', project='x')
        self.assertEqual(found[0]['file'], 'heavy_door')
        self.assertNotIn('matched_words', found[0])
        self.assertEqual(sorted((f['file'], tuple(f['matched_words'])) for f in found[1:]), [('door_a', ('door',)), ('heavy_box', ('heavy',))])

    def test_enough_full_matches_add_nothing(self):
        index = library(*[rec(f'heavy_door_{i}', labels=['Door']) for i in range(samples.FULL_MATCHES)], rec('door_a', labels=['Door']))
        found = samples.find(index, 'heavy door')
        self.assertEqual(len(found), samples.FULL_MATCHES)
        self.assertFalse(any('matched_words' in f for f in found))

    def test_four_full_matches_are_few_and_five_are_enough(self):
        four = library(*[rec(f'heavy_door_{i}', labels=['Door']) for i in range(4)], rec('door_a', labels=['Door']))
        self.assertEqual(len(samples.find(four, 'heavy door')), 5)
        five = library(*[rec(f'heavy_door_{i}', labels=['Door']) for i in range(5)], rec('door_a', labels=['Door']))
        self.assertEqual(len(samples.find(five, 'heavy door')), 5)

    def test_most_words_come_first(self):
        index = library(rec('big_old_door'), rec('big_door', clean=20.0), rec('door', clean=59.0))     # more words before a cleaner recording
        found = samples.find(index, 'big old door', project='x')
        self.assertEqual([f['file'] for f in found], ['big_old_door', 'big_door', 'door'])
        self.assertEqual(found[1]['matched_words'], ['big', 'door'])

    def test_one_word_has_no_partial_matches(self):
        self.assertEqual(samples.find(library(rec('box')), 'door'), [])

    def test_the_library_is_not_changed(self):
        index = library(rec('door_a'), rec('heavy_b'))
        before = json.dumps(index, sort_keys=True)
        samples.find(index, 'heavy door')
        self.assertEqual(json.dumps(index, sort_keys=True), before)

    def test_every_filter_holds_for_partial_matches(self):
        index = library(rec('heavy_door'), rec('door_fails', screen=['clipped']), rec('door_long', seconds=9.0), rec('door_metal', tags=['metal']), rec('door_ok'))
        found = [f['file'] for f in samples.find(index, 'heavy door', passing=True, exclude=['metal'], max_seconds=5.0)]
        self.assertEqual(sorted(found), ['door_ok', 'heavy_door'])

    def test_partial_matches_rank_by_where_their_own_words_are(self):
        index = library(rec('a', title='door in the labels too', labels=['Door'], clean=20.0), rec('b', title='door only here', clean=59.0))
        found = samples.find(index, 'heavy door', project='x')
        self.assertEqual([f['file'] for f in found], ['a', 'b'])


class MusicLast(unittest.TestCase):
    def test_recordings_labelled_only_as_music_come_after_the_others(self):
        index = library(rec('bow_violin', labels=['Bowed_string_instrument', 'Musical_instrument', 'Music'], clean=59.0),
                        rec('bow_and_arrow', labels=['Whoosh_and_swoosh_and_swish'], clean=12.0))
        self.assertEqual([f['file'] for f in samples.find(index, 'bow')], ['bow_and_arrow', 'bow_violin'])

    def test_a_word_that_asks_for_music_keeps_them_in_their_place(self):
        index = library(rec('violin_bow', labels=['Bowed_string_instrument', 'Music'], clean=59.0), rec('violin_case', labels=['Tap'], clean=12.0))
        self.assertEqual([f['file'] for f in samples.find(index, 'violin')][0], 'violin_bow')

    def test_a_plural_asks_too(self):
        index = library(rec('drums_a', labels=['Drum', 'Music'], clean=59.0), rec('drums_b', labels=['Tap'], clean=12.0))
        self.assertEqual(samples.find(index, 'drums')[0]['file'], 'drums_a')

    def test_a_recording_with_another_class_is_not_only_music(self):
        index = library(rec('hit_a', labels=['Thump_and_thud', 'Drum', 'Music'], clean=59.0), rec('hit_b', labels=['Tap'], clean=12.0))
        self.assertEqual(samples.find(index, 'hit')[0]['file'], 'hit_a')

    def test_no_labels_is_not_music(self):
        self.assertFalse(samples._only_music(rec('x')))

    def test_a_class_of_the_world_is_not_on_the_list(self):
        for label in ('Bell', 'Chime', 'Cowbell', 'Wind_chime', 'Church_bell', 'Door', 'Wind'):
            self.assertNotIn(label, samples.MUSIC_LABELS)
        for word in ('wind', 'bow', 'bell', 'key', 'door'):
            self.assertNotIn(word, samples.MUSIC_WORDS)


class TheCommand(unittest.TestCase):
    def setUp(self):
        self.base = helpers.scratch(f'search_{self.id().rsplit(".", 1)[-1]}')
        self.library = self.base / 'library'
        self.library.mkdir()
        files = [rec(f'door_{i}', labels=['Door']) for i in range(12)] + [rec('heavy_box', labels=['Thump_and_thud'])]
        (self.library / samples.INDEX).write_text(json.dumps({'schema': samples.SCHEMA, 'packs': {'fsd50k-eval': {'title': 'A dataset', 'licence': 'CC0-1.0', 'files': len(files)}}, 'files': files}), encoding='utf-8')

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'samples', '--library', str(self.library), *args], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=helpers.child_env(), cwd=cwd or self.base, timeout=120)

    def test_the_order_comes_from_the_folder_it_runs_in(self):
        one, two = self.base / 'hollow', self.base / 'ember'
        one.mkdir(), two.mkdir()
        order = lambda cwd: [f['file'] for f in json.loads(self.cli('--find', 'door', '--json', cwd=cwd).stdout)['files']]
        index = json.loads((self.library / samples.INDEX).read_text(encoding='utf-8'))
        self.assertEqual(order(one), [f['file'] for f in samples.find(index, 'door', project='hollow')])
        self.assertNotEqual(order(one), order(two))

    def test_partial_matches_are_marked_in_text_and_json(self):
        done = self.cli('--find', 'heavy door')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('(holds only: door)', done.stdout)
        self.assertIn('(holds only: heavy)', done.stdout)
        self.assertIn("0 recordings match 'heavy door', and 13 more hold some of its words (marked)", done.stdout)
        data = json.loads(self.cli('--find', 'heavy door', '--json').stdout)
        self.assertEqual((data['matched'], data['matched_some_words']), (13, 13))
        self.assertEqual(data['files'][0]['matched_words'] in (['door'], ['heavy']), True)

    def test_a_full_search_reads_as_before(self):
        done = self.cli('--find', 'door')
        self.assertIn("12 recordings match 'door'", done.stdout)
        self.assertNotIn('holds only', done.stdout)


if __name__ == '__main__':
    unittest.main()
