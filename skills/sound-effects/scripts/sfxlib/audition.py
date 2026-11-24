"""The audition page: one HTML file with every variant embedded, for the game's developer to listen to and leave feedback
on. No server and no network: it opens from disk in any current browser and can be sent as one file.

Each variant is embedded as its master's samples at 16 bits, dithered: as FLAC when the render was given an FFmpeg that
makes it (the same samples in about half the bytes), and as WAV without one or whenever FFmpeg fails.

On the page: each variant on its own; "random" plays the variants in random order without repeating the last one (the
default mode of Godot's AudioStreamRandomizer); "rapid" fires eight random variants at an adjustable interval, to hear
repetition; the project's flows play a short sequence of sounds; a gain slider per sound, a keep / change / drop choice
and a note make up the feedback, which the page shows as JSON to copy back to the agent. Choices are kept in the
browser's local storage where the browser allows it.

A loop's variants play looping (the Web Audio buffer source's own loop, which joins the end to the start with no gap)
until "stop"; "hear the join" starts two seconds before the end, so the join comes at once.

"Music" takes a file from the listener's own computer and plays it, looping if asked, under the sounds at a level of its
own. The file is decoded in the page and goes nowhere: it is not embedded, kept or sent, and no master holds any of it.

The page speaks English, Traditional or Simplified Chinese, Japanese or Korean (page_text.py), as the browser's languages
ask, and its language menu changes it. Only labels change: the feedback keeps its keys and its verdicts keep, change and
drop."""
import base64
import html
import json
from pathlib import Path

import numpy as np

from . import dsp, licences, page_text, record, wav

FLAC_LEVEL = '8'                         # FFmpeg's highest FLAC compression: smaller, and decoded as fast
FFMPEG_TIMEOUT_S = 60                    # for one variant: a page is not held up by an FFmpeg that hangs

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg: #f7f6f2; --card: #ffffff; --ink: #1d1d1f; --muted: #6b6b70; --line: #dddcd6; --accent: #2f6f5e;
        --warn: #9a5b00; --on: #e3efe9; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17181a; --card: #222326; --ink: #ececee; --muted: #a0a0a8; --line: #36373b; --accent: #7cc4ad;
          --warn: #e0a64a; --on: #1f3530; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 system-ui, -apple-system, "Segoe UI",
       sans-serif; }
header, main, footer { max-width: 1100px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
.top { display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; justify-content: space-between; }
.muted { color: var(--muted); }
.bar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 12px 0; }
button, select { font: inherit; border: 1px solid var(--line); background: var(--card); color: var(--ink); border-radius: 6px;
         padding: 4px 10px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button.on { background: var(--on); border-color: var(--accent); }
button.playing { outline: 2px solid var(--accent); }
button:disabled { opacity: 0.5; cursor: default; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
.card h2 { font-size: 1.05rem; margin: 0; }
.card .row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; }
canvas { width: 100%; height: 48px; display: block; margin-top: 8px; border-radius: 4px; background: var(--bg); }
textarea { width: 100%; font: inherit; border: 1px solid var(--line); border-radius: 6px; background: var(--bg);
           color: var(--ink); padding: 6px; }
.flag { color: var(--warn); font-size: 0.9rem; margin-top: 6px; }
.small { font-size: 0.85rem; }
input[type=range] { width: 140px; }
</style>
</head>
<body>
<header>
  <div class="top">
    <h1 id="title"></h1>
    <label class="small"><span data-t="language">Language</span> <select id="lang"></select></label>
  </div>
  <div class="muted small" id="about"></div>
  <p class="small" data-t="intro">Press any play button to start sound. Listen at a comfortable, fixed volume; every sound is already
  levelled to the set. For each sound choose keep, change or drop, add a note if you like, then copy the feedback at
  the bottom of the page.</p>
  <div class="bar">
    <label><span data-t="volume">Volume</span> <input type="range" id="master" min="-30" max="0" step="1" value="-6"> <span id="masterval"></span></label>
    <label><input type="checkbox" id="phone"> <span data-t="phone">As on a phone speaker (cuts below 400 Hz)</span></label>
    <label><span data-t="interval">Rapid interval</span> <input type="number" id="interval" min="40" max="1000" step="10" value="150"> <span data-t="ms">ms</span></label>
    <button id="all" data-t="all">Play every sound once</button>
  </div>
  <div class="bar">
    <label><span data-t="music">Music</span> <input type="file" id="musicfile" accept="audio/*,.mp3,.ogg,.oga,.opus,.flac,.wav,.m4a,.aac"></label>
    <button id="musicplay" disabled>play music</button>
    <label><input type="checkbox" id="musicloop" checked> <span data-t="loop">loop</span></label>
    <label><span data-t="music_level">Music level</span> <input type="range" id="musiclevel" min="-40" max="0" step="1" value="-12"> <span id="musicval"></span></label>
    <span class="muted small" id="musicnote"></span>
  </div>
  <div class="bar" id="flows"></div>
</header>
<main><div class="grid" id="cards"></div></main>
<footer>
  <h2 data-t="feedback">Feedback</h2>
  <textarea id="general" rows="3" data-t-placeholder="general" placeholder="Anything about the set as a whole"></textarea>
  <div class="bar"><button id="copy" data-t="copy">Copy feedback</button><span class="muted small" id="copied"></span></div>
  <textarea id="feedback" rows="10" readonly></textarea>
  <p class="muted small" id="recordings"></p>
</footer>
<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const STORE = 'sound-effects-audition:' + DATA.title + ':' + DATA.render;
const LANGUAGE_STORE = STORE + ':language';      // apart from the feedback, so a language change in one window writes over no notes
// keyed by sound ids, which may be any name ("constructor" too): no prototype to find them in
const buffers = Object.create(null);
const last = Object.create(null);
const views = Object.create(null);      // each card's way to show the feedback as it is now
let showGeneral = () => {};
let ctx = null;
let master = null;
let state = loadState();
let LANG = pickLanguage();

function own(object, key) { return Object.prototype.hasOwnProperty.call(object, key); }
function loadState() {
  const sounds = Object.create(null);
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORE));
    if (saved && saved.sounds && typeof saved.sounds === 'object') {
      for (const id of Object.keys(saved.sounds)) sounds[id] = saved.sounds[id];
      return { sounds: sounds, general: typeof saved.general === 'string' ? saved.general : '' };
    }
  } catch (e) { /* storage may be unavailable */ }
  return { sounds: sounds, general: '' };
}
// This window's changes that storage has not kept yet (a full storage refuses them): each stays here, on top of what is saved,
// until a write goes through. `id` null is the general feedback.
const unsaved = new Map();
function change(base, u) {
  if (u.id === null) { base.general = u.value; return; }
  const was = own(base.sounds, u.id) && base.sounds[u.id] && typeof base.sounds[u.id] === 'object' ? base.sounds[u.id] : {};
  const now = { verdict: was.verdict || '', note: was.note || '', gain_db: was.gain_db || 0 };
  now[u.field] = u.value;
  base.sounds[u.id] = now;
}
// One change, written into the feedback as it was saved last, by this window or another open on this page, so that what the
// other window wrote stays; where nothing can be read back, into this window's own.
function saveState(id, field, value) {
  unsaved.set(JSON.stringify([id, field]), { id: id, field: field, value: value });
  let base = state;
  try { if (window.localStorage.getItem(STORE) !== null) base = loadState(); } catch (e) { /* storage may be unavailable */ }
  for (const u of unsaved.values()) change(base, u);
  state = base;
  try { window.localStorage.setItem(STORE, JSON.stringify(state)); unsaved.clear(); } catch (e) { /* kept here until a write goes through */ }
  showFeedback();
}
// another window on this page saved: this one's feedback and controls show that now, with its own unkept changes on top, so that
// what is typed here next starts from what was saved there
window.addEventListener('storage', e => {
  if (e.key !== STORE && e.key !== null) return;
  state = loadState();
  for (const u of unsaved.values()) change(state, u);
  for (const id in views) views[id]();
  showGeneral();
  showFeedback();
});
// the language chosen before on this page, else the first of the browser's languages the page speaks, else English
function pickLanguage() {
  let chosen = null;
  try { chosen = window.localStorage.getItem(LANGUAGE_STORE); } catch (e) { /* storage may be unavailable */ }
  if (typeof chosen === 'string' && own(DATA.text, chosen)) return chosen;
  const asked = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language || 'en'];
  for (const tag of asked) {
    const code = String(tag).toLowerCase();
    if (/^zh-(tw|hk|mo|hant)/.test(code)) return 'zh-Hant';
    if (code.startsWith('zh')) return 'zh-Hans';
    if (code.startsWith('ja')) return 'ja';
    if (code.startsWith('ko')) return 'ko';
    if (code.startsWith('en')) return 'en';
  }
  return 'en';
}
function t(key, values) {
  const table = own(DATA.text, LANG) ? DATA.text[LANG] : {};
  let text = key in table ? table[key] : DATA.text.en[key];
  for (const name in (values || {})) text = text.split('{' + name + '}').join(String(values[name]));
  return text;
}
function entry(id) {
  if (!state.sounds[id]) state.sounds[id] = { verdict: '', note: '', gain_db: 0 };
  return state.sounds[id];
}
function dbToGain(db) { return Math.pow(10, db / 20); }
function byId(id) { return DATA.sounds.find(s => s.id === id); }
function bytesOf(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}
let phone = null;
function routeMaster() {
  if (!master) return;
  master.disconnect();
  master.connect(document.getElementById('phone').checked ? phone : ctx.destination);
}
document.getElementById('phone').addEventListener('change', routeMaster);
let decoding = null;
async function ensureAudio() {
  // not awaited: a sound started now waits for the context, and on a machine with no sound device it may never resume
  if (ctx) { if (ctx.state === 'suspended') ctx.resume().catch(() => {}); return decoding; }
  // the files' own rate: a context at another one resamples each file as a clip, and a loop then jumps at its join
  const Context = window.AudioContext || window.webkitAudioContext;
  try { ctx = new Context({ sampleRate: 48000 }); } catch (e) { ctx = new Context(); }
  master = ctx.createGain();
  master.gain.value = dbToGain(Number(document.getElementById('master').value));
  phone = ctx.createBiquadFilter();
  phone.type = 'highpass';
  phone.frequency.value = 400;
  phone.connect(ctx.destination);
  routeMaster();
  const jobs = [];
  for (const s of DATA.sounds) {
    for (const v of s.variants) {
      jobs.push(ctx.decodeAudioData(bytesOf(v.audio)).then(buf => { buffers[s.id + '/' + v.n] = buf; }));
    }
  }
  const all = Promise.all(jobs);
  decoding = all.catch(() => {});                  // a press while the files decode waits for them
  await all;
}
function play(sound, n, when) {
  const buf = buffers[sound.id + '/' + n];
  if (!buf) return 0;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const g = ctx.createGain();
  g.gain.value = dbToGain(entry(sound.id).gain_db || 0);
  src.connect(g);
  g.connect(master);
  src.start(when || 0);
  drawWave(sound.id, buf);
  flash(sound.id, n);
  return buf.duration;
}
const loops = Object.create(null);
function stopLoop(id) {
  const l = loops[id];
  if (!l) return;
  try { l.src.stop(); } catch (e) { /* already stopped */ }
  delete loops[id];
}
function playLoop(sound, n, fromEnd) {
  stopLoop(sound.id);
  const buf = buffers[sound.id + '/' + n];
  if (!buf) return;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.loop = true;
  const g = ctx.createGain();
  g.gain.value = dbToGain(entry(sound.id).gain_db || 0);
  src.connect(g);
  g.connect(master);
  src.start(0, fromEnd ? Math.max(0, buf.duration - fromEnd) : 0);
  loops[sound.id] = { src: src, gain: g, n: n };
  drawWave(sound.id, buf);
  flash(sound.id, n);
}
function pickRandom(sound) {
  const count = sound.variants.length;
  if (count === 1) return 1;
  let n;
  do { n = 1 + Math.floor(Math.random() * count); } while (n === last[sound.id]);
  last[sound.id] = n;
  return n;
}
function flash(id, n) {
  const b = document.getElementById('v-' + id + '-' + n);
  if (!b) return;
  b.classList.add('playing');
  setTimeout(() => b.classList.remove('playing'), 250);
}
function drawWave(id, buf) {
  const canvas = document.getElementById('w-' + id);
  if (!canvas) return;
  const w = canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
  const h = canvas.height = canvas.clientHeight * (window.devicePixelRatio || 1);
  const c = canvas.getContext('2d');
  c.clearRect(0, 0, w, h);
  c.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent');
  const data = buf.getChannelData(0);
  const step = Math.max(1, Math.floor(data.length / w));
  for (let x = 0; x < w; x++) {
    let lo = 1, hi = -1;
    for (let i = x * step; i < Math.min(data.length, (x + 1) * step); i++) {
      if (data[i] < lo) lo = data[i];
      if (data[i] > hi) hi = data[i];
    }
    if (hi < lo) continue;
    c.fillRect(x, (1 - hi) * h / 2, 1, Math.max(1, (hi - lo) * h / 2));
  }
}
function el(tag, attrs, text) {
  const e = document.createElement(tag);
  for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}
function fmt(v, digits) { return (v === null || v === undefined) ? '-' : Number(v).toFixed(digits); }
function signed(v) { return (v > 0 ? '+' : '') + fmt(v, 1); }
function buildCard(s) {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', {}, s.id));
  card.appendChild(el('div', { class: 'muted small' }, [s.recipe || t('layers'), s.notes].filter(Boolean).join(' - ')));
  const m = s.variants[0];
  if (s.use === 'loop') {
    card.appendChild(el('div', { class: 'muted small' }, t('loop_info', { seconds: fmt(m.duration_ms / 1000, 1), trim: fmt(s.trim_db, 1) })));
    for (const v of s.variants) {
      card.appendChild(el('div', { class: 'muted small' }, t('loop_variant', { n: v.n, peak: fmt(v.true_peak_db, 1), lufs: fmt(v.integrated_lufs, 1),
        range: fmt(v.loudness_range_lu, 1), halves: signed(v.halves_db) })));
    }
  } else {
    card.appendChild(el('div', { class: 'muted small' }, t('shot_info', { ms: fmt(m.duration_ms, 0), peak: fmt(m.true_peak_db, 1),
      lufs: fmt(m.brief_lufs, 1), trim: fmt(s.trim_db, 1) })));
  }
  const row = el('div', { class: 'row' });
  for (const v of s.variants) {
    const b = el('button', { id: 'v-' + s.id + '-' + v.n, title: v.file }, String(v.n));
    if (s.use === 'loop') {
      b.addEventListener('click', async () => { await ensureAudio(); playLoop(s, v.n, 0); });
    } else {
      b.addEventListener('click', async () => { await ensureAudio(); play(s, v.n); });
    }
    row.appendChild(b);
  }
  if (s.use === 'loop') {
    const j = el('button', {}, t('join'));
    j.addEventListener('click', async () => {
      await ensureAudio();
      playLoop(s, loops[s.id] ? loops[s.id].n : 1, 2.0);
    });
    row.appendChild(j);
    const stop = el('button', {}, t('stop'));
    stop.addEventListener('click', () => stopLoop(s.id));
    row.appendChild(stop);
  } else {
    const r = el('button', {}, t('random'));
    r.addEventListener('click', async () => { await ensureAudio(); play(s, pickRandom(s)); });
    row.appendChild(r);
    const f = el('button', {}, t('rapid'));
    f.addEventListener('click', async () => {
      await ensureAudio();
      const ms = Number(document.getElementById('interval').value) || 150;
      const t0 = ctx.currentTime + 0.05;
      for (let k = 0; k < 8; k++) play(s, pickRandom(s), t0 + k * ms / 1000);
    });
    row.appendChild(f);
  }
  card.appendChild(row);
  card.appendChild(el('canvas', { id: 'w-' + s.id }));
  const st = entry(s.id);                   // as the card opens: its handlers read entry(s.id), which another window's save can replace
  const gainRow = el('div', { class: 'row small' });
  const label = el('span', {}, '');
  const slider = el('input', { type: 'range', min: '-12', max: '6', step: '0.5', value: String(st.gain_db || 0) });
  const showGain = () => { label.textContent = t('level', { db: signed(entry(s.id).gain_db || 0) }); };
  slider.addEventListener('input', () => {
    const db = Number(slider.value);
    if (loops[s.id]) loops[s.id].gain.gain.value = dbToGain(db);
    saveState(s.id, 'gain_db', db);
    showGain();
  });
  showGain();
  gainRow.appendChild(slider);
  gainRow.appendChild(label);
  card.appendChild(gainRow);
  const verdicts = el('div', { class: 'row' });
  const buttons = {};
  for (const name of ['keep', 'change', 'drop']) {
    // the verdict stays the word in the feedback; another language shows its translation beside it
    const b = el('button', {}, LANG === 'en' ? name : name + ' · ' + t(name));
    buttons[name] = b;
    b.addEventListener('click', () => {
      const verdict = entry(s.id).verdict === name ? '' : name;
      for (const k in buttons) buttons[k].classList.toggle('on', verdict === k);
      saveState(s.id, 'verdict', verdict);
    });
    if (st.verdict === name) b.classList.add('on');
    verdicts.appendChild(b);
  }
  card.appendChild(verdicts);
  const note = el('textarea', { rows: '2', placeholder: t('note') });
  note.value = st.note || '';
  note.addEventListener('input', () => { saveState(s.id, 'note', note.value); });
  card.appendChild(note);
  views[s.id] = () => {
    const now = entry(s.id);
    slider.value = String(now.gain_db || 0);
    if (loops[s.id]) loops[s.id].gain.gain.value = dbToGain(now.gain_db || 0);
    showGain();
    for (const k in buttons) buttons[k].classList.toggle('on', now.verdict === k);
    if (document.activeElement !== note) note.value = now.note || '';     // not under the cursor of someone typing here
  };
  for (const flag of s.flags) card.appendChild(el('div', { class: 'flag' }, flag));
  return card;
}
function showFeedback() {
  const out = { project: DATA.title, render: DATA.render, made_by: DATA.made_by, general: state.general || '',
                sounds: {} };
  for (const s of DATA.sounds) {
    const st = entry(s.id);
    out.sounds[s.id] = { verdict: st.verdict || '', gain_db: st.gain_db || 0, note: st.note || '' };
  }
  document.getElementById('feedback').value = JSON.stringify(out, null, 1);
}
// Music to hear the sounds over: a file from this computer, decoded here. It is not kept or sent, and reaches no master.
const MUSIC_LIMIT_BYTES = 256 * 1024 * 1024;
const music = { buffer: null, src: null, gain: null, turn: 0, note: ['music_note', {}] };
function musicLevel() { return dbToGain(Number(document.getElementById('musiclevel').value)); }
function showMusic() { document.getElementById('musicplay').textContent = t(music.src ? 'music_stop' : 'music_play'); }
function musicNote(key, values) { music.note = [key, values || {}]; document.getElementById('musicnote').textContent = t(key, values); }
function stopMusic() {
  const src = music.src;
  music.src = null;
  if (src) {
    src.onended = null;
    try { src.stop(); } catch (e) { /* already stopped */ }
  }
  showMusic();
}
function startMusic() {
  stopMusic();
  if (!music.buffer) return;
  if (!music.gain) { music.gain = ctx.createGain(); music.gain.connect(master); }
  music.gain.gain.value = musicLevel();
  const src = ctx.createBufferSource();
  src.buffer = music.buffer;
  src.loop = document.getElementById('musicloop').checked;
  src.connect(music.gain);
  src.onended = () => { if (music.src === src) { music.src = null; showMusic(); } };
  src.start();
  music.src = src;
  showMusic();
}
async function loadMusic(file) {
  const turn = ++music.turn;
  const button = document.getElementById('musicplay');
  stopMusic();
  music.buffer = null;
  button.disabled = true;
  if (file.size > MUSIC_LIMIT_BYTES) {
    musicNote('music_big', { name: file.name, mb: MUSIC_LIMIT_BYTES / 1048576 });
    return;
  }
  musicNote('music_reading', { name: file.name });
  try {
    ensureAudio().catch(() => {});                  // the sounds' own decoding goes on beside this
    const buffer = await ctx.decodeAudioData(await file.arrayBuffer());
    if (turn !== music.turn) return;                // another file was chosen meanwhile
    music.buffer = buffer;
    button.disabled = false;
    musicNote('music_ready', { name: file.name, seconds: fmt(buffer.duration, 1) });
  } catch (e) {
    if (turn === music.turn) musicNote('music_bad', { name: file.name });
  }
}
function setupMusic() {
  document.getElementById('musicfile').addEventListener('change', e => {
    const file = e.target.files && e.target.files[0];
    if (file) loadMusic(file);
  });
  document.getElementById('musicplay').addEventListener('click', async () => {
    if (music.src) { stopMusic(); return; }
    try { await ensureAudio(); } catch (e) { /* the music does not wait on the sounds */ }
    startMusic();
  });
  const loop = document.getElementById('musicloop');
  loop.addEventListener('change', () => { if (music.src) music.src.loop = loop.checked; });
  const level = document.getElementById('musiclevel');
  const showLevel = () => {
    document.getElementById('musicval').textContent = level.value + ' dB';
    if (music.gain) music.gain.gain.value = musicLevel();
  };
  level.addEventListener('input', showLevel);
  showLevel();
}
// everything the page says, in LANG: the marked labels, then the cards and flows, built again with their state
function showLanguage() {
  document.documentElement.lang = LANG;
  for (const node of document.querySelectorAll('[data-t]')) node.textContent = t(node.getAttribute('data-t'));
  for (const node of document.querySelectorAll('[data-t-placeholder]')) node.setAttribute('placeholder', t(node.getAttribute('data-t-placeholder')));
  document.getElementById('about').textContent = t('about', { count: DATA.sounds.length, render: DATA.render, made_by: DATA.made_by }) +
    (DATA.level.target_lufs === null ? '' : t('about_level', { lufs: fmt(DATA.level.target_lufs, 1) }));
  showMusic();
  musicNote(music.note[0], music.note[1]);
  const cards = document.getElementById('cards');
  const built = DATA.sounds.map(buildCard);
  if (cards.replaceChildren) cards.replaceChildren(...built); else built.forEach(c => cards.appendChild(c));
  const flows = document.getElementById('flows');
  const buttons = DATA.flows.map(flow => {
    const b = el('button', {}, t('flow', { name: flow.name }));
    b.addEventListener('click', async () => {
      await ensureAudio();
      let at = ctx.currentTime + 0.05;
      for (const id of flow.sounds) { const s = byId(id); at += play(s, pickRandom(s), at) + flow.gap; }
    });
    return b;
  });
  if (flows.replaceChildren) flows.replaceChildren(...buttons); else buttons.forEach(b => flows.appendChild(b));
}
function setup() {
  document.getElementById('title').textContent = DATA.title;
  const packs = {};
  for (const r of DATA.recordings) (packs[r.title + ' by ' + r.author] = packs[r.title + ' by ' + r.author] || []).push(r.file);
  document.getElementById('recordings').textContent = DATA.recordings.length ? DATA.recordings_note +
    ' Recordings layered in these sounds: ' + Object.entries(packs).map(([pack, files]) => pack + ' (' + files.join(', ') +
    ')').join('; ') + '.' : '';
  const select = document.getElementById('lang');
  for (const code of DATA.languages) select.appendChild(el('option', { value: code }, DATA.text[code].name));
  select.value = LANG;
  select.addEventListener('change', () => {
    LANG = own(DATA.text, select.value) ? select.value : 'en';
    try { window.localStorage.setItem(LANGUAGE_STORE, LANG); } catch (e) { /* not kept */ }
    showLanguage();
  });
  showLanguage();
  document.getElementById('all').addEventListener('click', async () => {
    await ensureAudio();
    let at = ctx.currentTime + 0.05;
    for (const s of DATA.sounds) if (s.use !== 'loop') at += play(s, 1, at) + 0.4;
  });
  const masterInput = document.getElementById('master');
  const masterVal = document.getElementById('masterval');
  const showMaster = () => { masterVal.textContent = masterInput.value + ' dB'; if (master) master.gain.value = dbToGain(Number(masterInput.value)); };
  masterInput.addEventListener('input', showMaster);
  showMaster();
  const general = document.getElementById('general');
  general.value = state.general || '';
  general.addEventListener('input', () => { saveState(null, 'general', general.value); });
  showGeneral = () => { if (document.activeElement !== general) general.value = state.general || ''; };
  document.getElementById('copy').addEventListener('click', async () => {
    const text = document.getElementById('feedback');
    let done = false;
    try { await navigator.clipboard.writeText(text.value); done = true; } catch (e) { /* fall back */ }
    if (!done) { text.select(); try { done = document.execCommand('copy'); } catch (e) { done = false; } }
    document.getElementById('copied').textContent = t(done ? 'copied' : 'copy_by_hand');
  });
  setupMusic();
  showFeedback();
}
setup();
</script>
</body>
</html>
"""


def flac(ffmpeg, blob, frames):
    """The 16-bit WAV `blob` as FLAC, by FFmpeg through pipes. Writing to a pipe, FFmpeg cannot go back to put the stream's
    length into its header, so it is written here (the MD5 stays zero, which the format reads as not given)."""
    from . import export                 # imported here: export imports render, which imports this module
    data = bytearray(export._run(ffmpeg, ['-f', 'wav', '-i', 'pipe:0', '-c:a', 'flac', '-compression_level', FLAC_LEVEL, '-f', 'flac', 'pipe:1'],
                                 data=blob, timeout=FFMPEG_TIMEOUT_S))
    if len(data) < 42 or data[:4] != b'fLaC' or data[4] & 0x7F != 0:
        raise export.ExportError('FFmpeg did not write a FLAC stream that begins with its STREAMINFO')
    # STREAMINFO from byte 8: block and frame sizes (10 bytes), then 20 bits of rate, 3 of channels, 5 of bits a sample
    # and 36 of the length in frames
    packed = int.from_bytes(data[18:26], 'big')
    i = wav._info_of(blob, 'page')
    if (packed >> 44, ((packed >> 41) & 7) + 1, ((packed >> 36) & 31) + 1) != (i['rate'], i['channels'], 16):
        raise export.ExportError('the FLAC is not at the rate, channels and bits of the WAV')
    data[18:26] = ((packed >> 36 << 36) | (frames & (2 ** 36 - 1))).to_bytes(8, 'big')
    # it is kept only if it decodes to the very samples of the WAV it replaces
    back = export._run(ffmpeg, ['-f', 'flac', '-i', 'pipe:0', '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1'], data=bytes(data), timeout=FFMPEG_TIMEOUT_S)
    if back != blob[i['offset']:i['offset'] + i['frames'] * i['channels'] * 2]:
        raise export.ExportError('the FLAC does not decode to the samples of the WAV')
    return bytes(data)


def _page_audio(folder, rel, seed, ffmpeg):
    """A variant for the page and its length in frames: its master at 16 bits, dithered, as FLAC if `ffmpeg` makes it, else WAV."""
    x = record.master(folder, rel).astype(np.float64)
    blob = wav.encode(x, bits=16, dither=True, seed=seed)
    if ffmpeg:
        from . import export
        try:
            smaller = flac(ffmpeg, blob, len(x))
            if len(smaller) < len(blob):     # a click of a few milliseconds can come out larger as FLAC
                return smaller, len(x)
        except (OSError, ValueError, export.ExportError):
            pass                         # the page is the same with WAV, only larger
    return blob, len(x)


def build(render_dir, ffmpeg=None):
    """Write audition.html into the render folder (replacing an earlier page there) and return its path. With `ffmpeg`,
    the variants are embedded as FLAC where it can make them."""
    folder = Path(render_dir)
    resolved = record.load(folder)
    check = json.loads((folder / 'check.json').read_text(encoding='utf-8'))
    measured = {f['file']: f for f in check['files']}
    flags = {}
    for f in check['flags']:
        flags.setdefault(f['sound'], []).append(f"{Path(f['file']).stem}: {f['message']}")
    sounds = []
    for s in resolved['sounds']:
        variants = []
        for v in s['variants']:
            m = measured.get(v['file'], {})
            blob, frames = _page_audio(folder, v['file'], dsp.seed_for(resolved['seed'], v['file'], 'page'), ffmpeg)
            variants.append({'n': v['n'], 'file': v['file'], 'audio': base64.b64encode(blob).decode('ascii'), 'frames': frames,
                             'duration_ms': 1000 * m.get('duration_s', 0.0), 'true_peak_db': m.get('true_peak_db'),
                             'brief_lufs': m.get('brief_lufs'), 'onset_ms': m.get('onset_ms'),
                             'integrated_lufs': m.get('integrated_lufs'),
                             'loudness_range_lu': m.get('loudness_range_lu'), 'halves_db': m.get('halves_db')})
        sounds.append({'id': s['id'], 'category': s['category'], 'use': s.get('use', 'one_shot'),
                       'recipe': s.get('recipe', ''), 'notes': s['notes'], 'trim_db': s['trim_db'],
                       'flags': flags.get(s['id'], []), 'variants': variants})
    data = {'title': resolved['title'], 'render': folder.name, 'made_by': resolved['made_by'],
            'level': resolved['level'], 'flows': resolved['flows'], 'sounds': sounds,
            'recordings': resolved.get('recordings', []),
            'recordings_note': licences.note(resolved.get('recordings', []), resolved['sounds']),
            'languages': list(page_text.LANGUAGES), 'text': page_text.TEXT}
    # "<" is written as its JSON escape so that no text inside the data can close the script element
    text = json.dumps(data, ensure_ascii=False).replace('<', chr(92) + 'u003c')
    page = TEMPLATE.replace('__TITLE__', html.escape(f"{resolved['title']}: audition")).replace('__DATA__', text)
    path = folder / 'audition.html'
    path.write_text(page, encoding='utf-8', newline='\n')
    return path
