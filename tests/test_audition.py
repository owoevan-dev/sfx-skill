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
from sfxlib import audition, project, render, wav


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
                    execCommand: () => true };
global.window = { localStorage: { getItem: () => null, setItem: () => {} }, devicePixelRatio: 1 };
global.getComputedStyle = () => ({ getPropertyValue: () => '#000' });
global.atob = s => Buffer.from(s, 'base64').toString('binary');
global.navigator = {};
(0, eval)(script);
function find(node, pred) {
  if (pred(node)) return node;
  for (const c of node.children || []) { const f = find(c, pred); if (f) return f; }
  return null;
}
const firstCard = ids['cards'].children[0];
const keep = find(firstCard, n => n.tag === 'button' && n.textContent === 'keep');
keep.listeners['click'].forEach(f => f());
const feedback = JSON.parse(ids['feedback'].value);
console.log(JSON.stringify({ cards: ids['cards'].children.length, flows: ids['flows'].children.length,
                             feedback_sounds: Object.keys(feedback.sounds),
                             verdict_after_click: feedback.sounds[Object.keys(feedback.sounds)[0]].verdict }));
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
                raw = base64.b64decode(v['wav'])
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
        self.assertEqual(len(re.findall(r'localStorage', script)), 2)         # the listener's choices, read and written

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
        proc = subprocess.run(['node', str(harness), str(page)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result['cards'], 2)
        self.assertEqual(result['flows'], 1)
        self.assertEqual(result['feedback_sounds'], ['beep', 'hand_made'])
        self.assertEqual(result['verdict_after_click'], 'keep')

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
