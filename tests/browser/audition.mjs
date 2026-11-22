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

async function check(name) {
  const browser = await ENGINES[name].launch();
  const page = await (await browser.newContext()).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  const expected = await page.evaluate(() => ({
    sounds: DATA.sounds.length,
    files: DATA.sounds.reduce((n, s) => n + s.variants.length, 0),
    loop: (DATA.sounds.find(s => s.use === 'loop') || {}).id,
  }));
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

  if (expected.loop) {
    await page.locator(`button[id="v-${expected.loop}-1"]`).click();
    // the click's handler waits for the audio context first
    await page.waitForFunction(id => Boolean(loops[id] && loops[id].src.loop), expected.loop, { timeout: 10000 })
      .catch(() => { throw new Error('the loop does not play looping'); });
    await page.evaluate(id => stopLoop(id), expected.loop);
  }

  await page.getByRole('button', { name: 'keep', exact: true }).first().click();
  const feedback = JSON.parse(await page.locator('#feedback').inputValue());
  const first = Object.keys(feedback.sounds)[0];
  if (feedback.sounds[first].verdict !== 'keep') throw new Error('the feedback does not hold the choice');
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
