// The audition page in real browsers: it builds its cards, decodes every embedded file on the first press, plays, loops,
// and writes the feedback, with nothing reported as an error.
//
//   npm ci && npx playwright install --with-deps chromium firefox webkit
//   node audition.mjs PATH/TO/audition.html [chromium firefox webkit]
import { pathToFileURL } from 'node:url';
import { chromium, firefox, webkit } from 'playwright';

const ENGINES = { chromium, firefox, webkit };
const [page_path, ...names] = process.argv.slice(2);
if (!page_path) throw new Error('usage: node audition.mjs PAGE [engines]');
const url = pathToFileURL(page_path).href;
let failed = 0;

// A short tone as a WAV file, to stand for a track the listener picks.
function trackBytes(seconds = 0.4, rate = 48000) {
  const frames = Math.floor(seconds * rate);
  const data = Buffer.alloc(44 + frames * 2);
  data.write('RIFF', 0);
  data.writeUInt32LE(36 + frames * 2, 4);
  data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24);
  data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write('data', 36);
  data.writeUInt32LE(frames * 2, 40);
  for (let i = 0; i < frames; i++) data.writeInt16LE(Math.round(9000 * Math.sin(2 * Math.PI * 330 * i / rate)), 44 + i * 2);
  return data;
}

async function check(name) {
  const browser = await ENGINES[name].launch();
  const page = await (await browser.newContext({ locale: 'en-US' })).newPage();      // the page in English; another language is tried below
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  const expected = await page.evaluate(() => ({
    sounds: DATA.sounds.length,
    files: DATA.sounds.reduce((n, s) => n + s.variants.length, 0),
    loop: (DATA.sounds.find(s => s.use === 'loop') || {}).id,
    flac: DATA.sounds.every(s => s.variants.every(v => v.audio.startsWith('ZkxhQw'))),
  }));
  if (process.env.SFX_PAGE_FLAC === '1' && !expected.flac) throw new Error('the page was to hold FLAC and does not');
  const cards = await page.locator('#cards > .card').count();
  if (cards !== expected.sounds) throw new Error(`${cards} cards for ${expected.sounds} sounds`);

  // the first press makes the audio context and decodes every embedded file; a second press meanwhile waits for them
  await page.evaluate(() => {
    window.played = [];
    const real = play;
    play = (s, n, when) => { const seconds = real(s, n, when); window.played.push(seconds > 0); return seconds; };
    const ids = DATA.sounds.filter(s => s.use !== 'loop').flatMap(s => s.variants.map(v => `v-${s.id}-${v.n}`)).slice(0, 2);
    ids.forEach(id => document.getElementById(id).click());
  });
  await page.waitForFunction(n => Object.keys(buffers).length === n, expected.files, { timeout: 60000 });
  await page.waitForFunction(() => window.played.length === 2 && window.played.every(Boolean), null, { timeout: 10000 })
    .catch(() => { throw new Error('a press made while the files were decoding did not play'); });
  const loudest = await page.evaluate(() => Object.values(buffers).map(b => {
    let peak = 0;
    for (let c = 0; c < b.numberOfChannels; c++) {
      const data = b.getChannelData(c);
      for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i]));
    }
    return peak;
  }));
  if (loudest.some(p => !(p > 0.01))) throw new Error('a decoded file is silent');
  // each file decodes to its master's length exactly, so a loop's join stays where it was made
  const lengths = await page.evaluate(() => DATA.sounds.flatMap(s => s.variants.map(v => [buffers[s.id + '/' + v.n].length, Math.round(v.frames * ctx.sampleRate / 48000)])));
  const slack = await page.evaluate(() => ctx.sampleRate === 48000 ? 0 : 1);     // a context at another rate resamples
  if (lengths.some(([got, want]) => Math.abs(got - want) > slack)) throw new Error(`a decoded file is not as long as its master: ${JSON.stringify(lengths.slice(0, 4))}`);

  if (expected.loop) {
    await page.locator(`button[id="v-${expected.loop}-1"]`).click();
    // the click's handler waits for the audio context first
    await page.waitForFunction(id => Boolean(loops[id] && loops[id].src.loop), expected.loop, { timeout: 10000 })
      .catch(() => { throw new Error('the loop does not play looping'); });
    await page.evaluate(id => stopLoop(id), expected.loop);
  }

  // music from the listener's disk plays under the sounds, and a file it cannot read leaves them playing
  const oneShot = await page.evaluate(() => { const s = DATA.sounds.find(x => x.use !== 'loop'); return s ? `v-${s.id}-${s.variants[0].n}` : null; });
  const pressSound = async () => {
    if (!oneShot) return;
    const before = await page.evaluate(() => window.played.length);
    await page.locator(`button[id="${oneShot}"]`).click();
    await page.waitForFunction(n => window.played.length > n && window.played[window.played.length - 1], before, { timeout: 10000 })
      .catch(() => { throw new Error('a sound did not play with the music control in use'); });
  };
  await page.locator('#musicfile').setInputFiles({ name: 'track.wav', mimeType: 'audio/wav', buffer: trackBytes() });
  await page.waitForFunction(() => Boolean(music.buffer) && !document.getElementById('musicplay').disabled, null, { timeout: 20000 })
    .catch(() => { throw new Error('a WAV track was not decoded'); });
  await page.evaluate(() => {
    window.routed = [];
    const connect = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function (target, ...rest) { window.routed.push([this, target]); return connect.call(this, target, ...rest); };
  });
  await page.locator('#musicplay').click();
  await page.waitForFunction(() => Boolean(music.src && music.src.loop), null, { timeout: 10000 })
    .catch(() => { throw new Error('the music does not play looping'); });
  if (!await page.evaluate(() => window.routed.some(([from, to]) => from === music.gain && to === master))) {
    throw new Error("the music does not go through the page's volume");
  }
  await pressSound();
  const levelSet = await page.evaluate(() => {
    const level = document.getElementById('musiclevel');
    level.value = '-20';
    level.dispatchEvent(new Event('input'));
    return music.gain.gain.value;
  });
  if (Math.abs(levelSet - 0.1) > 0.001) throw new Error(`the music's level is ${levelSet}`);
  await page.locator('#musicplay').click();
  await page.waitForFunction(() => music.src === null, null, { timeout: 10000 })
    .catch(() => { throw new Error('the music does not stop'); });
  const seen = errors.length;
  await page.locator('#musicfile').setInputFiles({ name: 'broken.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('this is no audio at all') });
  await page.waitForFunction(() => music.buffer === null && document.getElementById('musicnote').textContent.includes('could not read'), null, { timeout: 20000 })
    .catch(() => { throw new Error('a file it cannot decode is not reported'); });
  errors.splice(seen, errors.length - seen, ...errors.slice(seen).filter(text => !/decod|audio/i.test(text)));   // the browser's own note on that file
  await pressSound();

  await page.getByRole('button', { name: 'keep', exact: true }).first().click();
  const feedback = JSON.parse(await page.locator('#feedback').inputValue());
  const first = Object.keys(feedback.sounds)[0];
  if (feedback.sounds[first].verdict !== 'keep') throw new Error('the feedback does not hold the choice');
  // the page in the browser's language, and in another chosen from its menu; the feedback is the same in all
  const local = await (await browser.newContext({ locale: 'zh-TW' })).newPage();
  local.on('pageerror', e => errors.push(String(e)));
  await local.goto(url);
  const about = await local.locator('#about').textContent();
  if (!about.includes('個音效')) throw new Error(`the page did not start in the browser's language: ${about}`);
  await local.getByRole('button', { name: 'keep · 保留', exact: true }).first().click();
  const verdict = page => page.locator('#feedback').inputValue().then(text => { const f = JSON.parse(text); return f.sounds[Object.keys(f.sounds)[0]].verdict; });
  if (await verdict(local) !== 'keep') throw new Error('a page in another language writes another verdict');
  await local.locator('#lang').selectOption('ja');
  if ((await local.locator('#copy').textContent()) !== 'フィードバックをコピー') throw new Error('the language menu does not change the page');
  if (await local.getByRole('button', { name: 'keep · 採用', exact: true }).first().getAttribute('class') !== 'on') throw new Error('a choice is lost when the language changes');
  if (await verdict(local) !== 'keep') throw new Error('the feedback changed with the language');

  if (errors.length) throw new Error(errors.join(' | '));
  await browser.close();
  return `${cards} cards, ${expected.files} files decoded`;
}

for (const name of names.length ? names : Object.keys(ENGINES)) {
  try {
    console.log(`${name}: ${await check(name)}`);
  } catch (e) {
    failed += 1;
    console.log(`${name}: FAILED: ${e.message}`);
  }
}
process.exit(failed ? 1 : 0);
