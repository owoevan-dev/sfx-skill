"""The audition page: one self-contained HTML file whose embedded audio is the render's, no network, valid script."""
import base64
import io
import json
import re
import shutil
import subprocess
import unittest

import numpy as np

import helpers
import test_project  # registers test.beep
from sfxlib import audition, page_text, project, render, wav


HARNESS = r"""
// Runs the page's script against a minimal stand-in for the DOM (no audio) and reports what it built.
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dataText = html.split('<script type="application/json" id="data">')[1].split('</script>')[0];
const script = html.split('<script>')[1].split('</script>')[0];
const ids = {};
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.textContent = ''; this.value = '';
    this.listeners = {}; this.clientWidth = 300; this.clientHeight = 48;
    const set = new Set();
    this.classList = { add: c => set.add(c), remove: c => set.delete(c),
                       toggle: (c, on) => { if (on) set.add(c); else set.delete(c); }, has: c => set.has(c) };
  }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') ids[v] = this; if (k === 'value') this.value = v; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  getContext() { return { clearRect() {}, fillRect() {} }; }
  select() {}
}
// every element the page's markup declares (outside its scripts), so a script that looks for one the page lacks fails
for (const m of html.split('<script')[0].matchAll(/\sid="([^"]+)"/g)) {
  const id = m[1];
  const e = new El('div');
  e.value = id === 'master' ? '-6' : (id === 'interval' ? '150' : '');
  ids[id] = e;
}
ids['data'] = { textContent: dataText };
global.document = { getElementById: id => ids[id] || null, createElement: t => new El(t), documentElement: {},
                    execCommand: () => true, querySelectorAll: () => [] };
global.writes = [];
global.window = { localStorage: { getItem: () => null, setItem: (k, v) => { global.writes.push(k); if (!k.endsWith(':language')) global.saved = v; } }, devicePixelRatio: 1,
                  addEventListener() {} };
global.getComputedStyle = () => ({ getPropertyValue: () => '#000' });
global.atob = s => Buffer.from(s, 'base64').toString('binary');
// Node has a navigator of its own, which an assignment does not replace
Object.defineProperty(globalThis, 'navigator', { value: { languages: (process.argv[3] || '').split(',').filter(Boolean) }, configurable: true, writable: true });
(0, eval)(script);
function find(node, pred) {
  if (pred(node)) return node;
  for (const c of node.children || []) { const f = find(c, pred); if (f) return f; }
  return null;
}
const firstCard = ids['cards'].children[0];
const keep = find(firstCard, n => n.tag === 'button' && n.textContent.startsWith('keep'));
keep.listeners['click'].forEach(f => f());
const feedback = JSON.parse(ids['feedback'].value);
const result = { cards: ids['cards'].children.length, flows: ids['flows'].children.length,
                 feedback_sounds: Object.keys(feedback.sounds),
                 verdict_after_click: feedback.sounds[Object.keys(feedback.sounds)[0]].verdict,
                 keep_label: keep.textContent, about: ids['about'].textContent,
                 saved_sounds: Object.keys((JSON.parse(global.saved || '{}').sounds) || {}) };
const before = global.writes.length;          // then the language changes: what does that write?
ids['lang'].value = 'ja';
ids['lang'].listeners['change'].forEach(f => f());
result.language_writes = global.writes.slice(before);
console.log(JSON.stringify(result));
"""

TWO_WINDOWS = r"""
// The page open twice on one browser's storage, each window in a context of its own; a write fires 'storage' in the other
// window only, as a browser does. Window B opens first and is stale when A saves a verdict and a note; then B writes its
// general feedback and moves a level.
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dataText = html.split('<script type="application/json" id="data">')[1].split('</script>')[0];
const script = html.split('<script>')[1].split('</script>')[0];
const store = new Map();
const windows = [];
function open() {
  const ids = {};
  class El {
    constructor(tag) {
      this.tag = tag; this.children = []; this.attrs = {}; this.textContent = ''; this.value = '';
      this.listeners = {}; this.clientWidth = 300; this.clientHeight = 48;
      const set = new Set();
      this.classList = { add: c => set.add(c), remove: c => set.delete(c), toggle: (c, on) => { if (on) set.add(c); else set.delete(c); } };
    }
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') ids[v] = this; if (k === 'value') this.value = v; }
    appendChild(c) { this.children.push(c); return c; }
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
    getContext() { return { clearRect() {}, fillRect() {} }; }
    select() {}
  }
  for (const m of html.split('<script')[0].matchAll(/\sid="([^"]+)"/g)) {
    const e = new El('div');
    e.value = m[1] === 'master' ? '-6' : (m[1] === 'interval' ? '150' : '');
    ids[m[1]] = e;
  }
  ids['data'] = { textContent: dataText };
  const me = { ids, heard: {} };
  const win = { devicePixelRatio: 1, addEventListener: (t, f) => (me.heard[t] = me.heard[t] || []).push(f),
                localStorage: { getItem: k => (store.has(k) ? store.get(k) : null),
                                setItem: (k, v) => { store.set(k, String(v)); if (process.argv[3] === 'quiet') return;   // a browser that tells no other window
                                                     for (const w of windows) if (w !== me) (w.heard.storage || []).forEach(f => f({ key: k })); } } };
  const context = { window: win, navigator: { languages: [] }, console, setTimeout, clearTimeout, atob: s => Buffer.from(s, 'base64').toString('binary'),
                    getComputedStyle: () => ({ getPropertyValue: () => '#000' }),
                    document: { getElementById: id => ids[id] || null, createElement: t => new El(t), documentElement: {}, execCommand: () => true,
                                querySelectorAll: () => [] } };
  vm.createContext(context);
  vm.runInContext(script, context);
  windows.push(me);
  return me;
}
function find(node, pred) {
  if (pred(node)) return node;
  for (const c of node.children || []) { const f = find(c, pred); if (f) return f; }
  return null;
}
const fire = (el, type) => el.listeners[type].forEach(f => f());
const b = open();
const a = open();
const cardA = a.ids['cards'].children[0];
fire(find(cardA, n => n.tag === 'button' && n.textContent.startsWith('keep')), 'click');
const noteA = find(cardA, n => n.tag === 'textarea');
noteA.value = 'trim the ring';
fire(noteA, 'input');
const bSees = JSON.parse(b.ids['feedback'].value);
b.ids['general'].value = 'balance the set';
fire(b.ids['general'], 'input');
const afterGeneral = JSON.parse(store.get([...store.keys()].find(k => !k.endsWith(':language'))));
const slider = find(b.ids['cards'].children[0], n => n.tag === 'input' && n.attrs.type === 'range');
slider.value = '-3';
fire(slider, 'input');
const key = [...store.keys()].find(k => !k.endsWith(':language'));
console.log(JSON.stringify({ b_sees: bSees, after_general: afterGeneral, saved: JSON.parse(store.get(key)), b_copies: JSON.parse(b.ids['feedback'].value) }));
"""


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = test_project.minimal()
        data['variants'] = 2
        data['sounds'][0]['notes'] = 'a note with </script><b>markup</b> in it'
        data['flows'] = [{'name': 'both', 'sounds': ['beep', 'hand_made']}]
        cls.base = helpers.scratch('audition')
        cls.out = cls.base / 'render'
        render.render(project.validate(data), cls.out)
        cls.html = (cls.out / 'audition.html').read_text(encoding='utf-8')
        m = re.search(r'<script type="application/json" id="data">(.*?)</script>', cls.html, re.S)
        cls.data = json.loads(m.group(1))

    def test_every_variant_is_embedded_and_matches_its_master(self):
        for sound in self.data['sounds']:
            for v in sound['variants']:
                raw = base64.b64decode(v['audio'])
                path = self.base / f"{sound['id']}_{v['n']}.wav"
                path.write_bytes(raw)
                page, info = wav.read(path)
                master, _ = wav.read(self.out / v['file'])
                self.assertEqual(info['bits'], 16)
                self.assertEqual(len(page), len(master))
                self.assertLess(float(np.max(np.abs(page - master))), 2.5 / 32768)

    def test_no_network_and_no_broken_markup(self):
        self.assertNotRegex(self.html, r'(src|href)\s*=\s*["\']https?:')
        block = re.search(r'<script type="application/json" id="data">(.*?)</script>', self.html, re.S).group(1)
        self.assertNotIn('</', block)
        self.assertEqual(self.data['sounds'][0]['notes'], 'a note with </script><b>markup</b> in it')
        self.assertEqual(self.data['flows'][0]['sounds'], ['beep', 'hand_made'])

    def test_music_is_picked_from_the_computer_and_goes_nowhere(self):
        self.assertRegex(self.html, r'<input type="file" id="musicfile"')
        script = re.search(r'<script>(.*?)</script>', self.html, re.S).group(1)
        self.assertNotRegex(script, r'fetch\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|createObjectURL|FileReader')
        self.assertEqual(len(re.findall(r'localStorage', script)), 5)         # the listener's choices (read, read again before a write, written)
                                                                              # and language (read, written)

    def test_the_feedback_says_nothing_of_the_music(self):
        script = re.search(r'<script>(.*?)</script>', self.html, re.S).group(1)
        feedback = script[script.index('function showFeedback'):script.index('function setup')]
        self.assertNotIn('music', feedback[:feedback.index('// Music')])

    def test_the_page_is_listed_in_files_json(self):
        files = json.loads((self.out / 'files.json').read_text(encoding='utf-8'))
        self.assertIn('audition.html', [f['path'] for f in files])

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_the_page_builds_its_cards_and_feedback_in_a_stub_dom(self):
        harness = self.base / 'harness.js'
        harness.write_text(HARNESS, encoding='utf-8')
        page = self.out / 'audition.html'
        proc = subprocess.run(['node', str(harness), str(page)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result['cards'], 2)
        self.assertEqual(result['flows'], 1)
        self.assertEqual(result['feedback_sounds'], ['beep', 'hand_made'])
        self.assertEqual(result['verdict_after_click'], 'keep')

    def stub(self, languages=''):
        harness = self.base / 'harness.js'
        harness.write_text(HARNESS, encoding='utf-8')
        proc = subprocess.run(['node', str(harness), str(self.out / 'audition.html'), languages], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_the_page_speaks_the_browsers_language_and_the_feedback_does_not_change(self):
        for languages, word, keep in (('', 'sounds', 'keep'), ('fr-FR,de', 'sounds', 'keep'), ('zh-TW', '個音效', 'keep · 保留'), ('zh-Hant-TW', '個音效', 'keep · 保留'),
                                      ('zh-HK', '個音效', 'keep · 保留'), ('zh-CN', '个音效', 'keep · 保留'), ('ja-JP', '効果音', 'keep · 採用'), ('ko', '효과음', 'keep · 유지'),
                                      ('fr,ja', '効果音', 'keep · 採用')):
            with self.subTest(languages=languages):
                result = self.stub(languages)
                self.assertIn(word, result['about'])
                self.assertEqual(result['keep_label'], keep)
                self.assertEqual(result['verdict_after_click'], 'keep')

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_a_sound_named_like_a_property_of_every_object_keeps_its_choice(self):
        data = test_project.minimal()
        data['sounds'] = [dict(data['sounds'][0], id='constructor')]
        out = helpers.scratch('audition_constructor') / 'render'
        render.render(project.validate(data), out)
        harness = out.parent / 'harness.js'
        harness.write_text(HARNESS, encoding='utf-8')
        proc = subprocess.run(['node', str(harness), str(out / 'audition.html'), ''], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result['verdict_after_click'], 'keep')
        self.assertEqual(result['saved_sounds'], ['constructor'])

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def two_windows(self, quiet=False):
        harness = self.base / 'two_windows.js'
        harness.write_text(TWO_WINDOWS, encoding='utf-8')
        proc = subprocess.run(['node', str(harness), str(self.out / 'audition.html')] + (['quiet'] if quiet else []), capture_output=True, text=True,
                              encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        first = next(iter(result['saved']['sounds']))
        for saved in (result['after_general'], result['saved']):
            self.assertEqual(saved['sounds'][first]['verdict'], 'keep')
            self.assertEqual(saved['sounds'][first]['note'], 'trim the ring')
            self.assertEqual(saved['general'], 'balance the set')
        self.assertEqual(result['saved']['sounds'][first]['gain_db'], -3)
        self.assertEqual(result['b_copies']['sounds'][first]['verdict'], 'keep')    # what window B copies holds window A's choice
        self.assertEqual(result['b_copies']['general'], 'balance the set')
        return result, first

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_two_windows_editing_different_things_keep_each_others_feedback(self):
        result, first = self.two_windows()
        self.assertEqual(result['b_sees']['sounds'][first]['note'], 'trim the ring')   # window B's feedback follows A's as A saves

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_two_windows_keep_each_others_feedback_where_no_window_is_told_of_the_other(self):
        result, first = self.two_windows(quiet=True)
        self.assertEqual(result['b_sees']['sounds'][first]['note'], '')               # nothing told B, which still wrote over nothing

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_a_language_change_writes_only_the_language(self):
        result = self.stub('')
        self.assertTrue(result['language_writes'])
        self.assertTrue(all(key.endswith(':language') for key in result['language_writes']), result['language_writes'])

    def test_a_flac_of_another_rate_or_one_larger_than_the_wav_is_not_used(self):
        from unittest import mock
        from sfxlib import export

        def streaminfo(rate=48000, channels=1, bits=16, padding=0):
            packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36)
            return b'fLaC' + bytes([0x80, 0, 0, 34]) + bytes(10) + packed.to_bytes(8, 'big') + bytes(16) + bytes(padding)

        def ffmpeg(header):
            seen = {}

            def run(path, args, data=None, timeout=None):
                if args[:2] == ['-f', 'wav']:
                    seen['wav'] = data
                    i = wav._info_of(data, 'page')
                    seen['pcm'] = data[i['offset']:i['offset'] + i['frames'] * i['channels'] * 2]
                    seen['channels'] = i['channels']
                    return header(seen['channels'])
                return seen['pcm']
            return run
        for name, header in (('another rate', lambda ch: streaminfo(rate=24000, channels=ch)), ('larger than the WAV', lambda ch: streaminfo(channels=ch, padding=10 ** 6))):
            with self.subTest(name=name), mock.patch.object(export, '_run', side_effect=ffmpeg(header)):
                audition.build(self.out, ffmpeg='ffmpeg')
                page = (self.out / 'audition.html').read_text(encoding='utf-8')
                data = json.loads(re.search('<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
                self.assertEqual(base64.b64decode(data['sounds'][0]['variants'][0]['audio'])[:4], b'RIFF')
        audition.build(self.out)

    def test_every_language_has_every_label_with_the_same_blanks(self):
        blanks = lambda text: sorted(re.findall('[{]([a-z_]+)[}]', text))
        english = page_text.TEXT['en']
        self.assertEqual(list(page_text.TEXT), list(page_text.LANGUAGES))
        for code, table in page_text.TEXT.items():
            with self.subTest(language=code):
                self.assertEqual(sorted(table), sorted(english))
                for key, text in table.items():
                    self.assertEqual(blanks(text), blanks(english[key]), key)
        script = re.search('<script>(.*?)</script>', self.html, re.S).group(1)
        used = set(re.findall("[^a-zA-Z_]t[(]'([a-z_]+)'", script)) | set(re.findall('data-t(?:-placeholder)?="([a-z_]+)"', self.html))
        used |= {'keep', 'change', 'drop', 'music_stop', 'music_play', 'music_note', 'copied', 'copy_by_hand'}
        self.assertEqual(sorted(used - set(english)), [])

    def test_a_page_without_ffmpeg_holds_wav(self):
        for sound in self.data['sounds']:
            for v in sound['variants']:
                self.assertEqual(base64.b64decode(v['audio'])[:4], b'RIFF')

    def test_flac_that_is_not_whole_leaves_the_page_as_wav(self):
        from unittest import mock
        from sfxlib import export
        good_header = b'fLaC' + bytes([0x00, 0, 0, 34]) + bytes(34)

        def answer(encoded, decoded=b''):          # what a broken FFmpeg gives when asked to encode, and to decode
            return lambda ffmpeg, args, data=None, timeout=None: encoded if args[:2] == ['-f', 'wav'] else decoded

        def hangs(ffmpeg, args, data=None, timeout=None):
            raise export.ExportError('FFmpeg did not finish in 60 s')
        right_header = b'fLaC' + bytes([0x80, 0, 0, 34]) + bytes(10) + ((48000 << 44) | (15 << 36)).to_bytes(8, 'big') + bytes(16)
        for name, broken in (('just the marker', answer(b'fLaC')), ('a header and no audio', answer(good_header)), ('a timeout', hangs),
                             ('a right header that decodes to other samples', answer(right_header, b'\x01\x00' * 64))):
            with self.subTest(name=name), mock.patch.object(export, '_run', side_effect=broken):
                audition.build(self.out, ffmpeg='ffmpeg')
                page = (self.out / 'audition.html').read_text(encoding='utf-8')
                data = json.loads(re.search('<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
                self.assertEqual(base64.b64decode(data['sounds'][0]['variants'][0]['audio'])[:4], b'RIFF')
        audition.build(self.out)

    def test_a_stale_ffmpeg_setting_leaves_the_page_as_wav(self):
        import os
        import sys
        env = dict(helpers.child_env(), SFX_SKILL_FFMPEG=str(self.base / 'no-ffmpeg-here.exe'))
        done = subprocess.run([sys.executable, str(helpers.SCRIPTS / 'sfx.py'), 'audition', str(self.out)], capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('WAV', done.stdout)
        page = (self.out / 'audition.html').read_text(encoding='utf-8')
        self.assertIn('UklGR', page)                       # base64 of RIFF

    def test_an_ffmpeg_that_cannot_make_flac_leaves_the_page_as_wav(self):
        import sys
        for bad in (self.base / 'no-ffmpeg-here', sys.executable):
            with self.subTest(ffmpeg=str(bad)):
                audition.build(self.out, ffmpeg=bad)
                page = (self.out / 'audition.html').read_text(encoding='utf-8')
                data = json.loads(re.search('<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
                self.assertEqual({base64.b64decode(v['audio'])[:4] for s in data['sounds'] for v in s['variants']}, {b'RIFF'})
        audition.build(self.out)

    @unittest.skipIf(shutil.which('node') is None, 'node is not installed')
    def test_the_script_parses(self):
        scripts = re.findall(r'<script>(.*?)</script>', self.html, re.S)
        self.assertEqual(len(scripts), 1)
        js = self.base / 'page.js'
        js.write_text(scripts[0], encoding='utf-8')
        proc = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == '__main__':
    unittest.main()
