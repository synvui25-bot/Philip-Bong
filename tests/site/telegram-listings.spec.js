// Standalone browser checks: NODE_PATH must expose the bundled playwright package.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const fixturePath = path.join(__dirname, 'fixtures/telegram-listings.json');
const tests = [];
const test = (name, run) => tests.push({ name, run });
let browser, siteUrl, fixture;

async function withPage(width, data, run, status = 200) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(3000);
  const requests = [];
  const errors = [];
  page.on('request', request => requests.push(request.url()));
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(siteUrl).origin) return route.abort();
    if (url.pathname.endsWith('/data/telegram-listings.json')) {
      return route.fulfill({ status, contentType: 'application/json', body: typeof data === 'string' ? data : JSON.stringify(data) });
    }
    if (/\/assets\/telegram\/\d+-\d+\.webp$/.test(url.pathname)) {
      return route.fulfill({ contentType: 'image/webp', body: await fs.readFile(path.join(root, 'wozo-listing-1.webp')) });
    }
    return route.continue();
  });
  try {
    await page.goto(siteUrl);
    await run(page, requests);
    assert.deepEqual(errors, [], 'no uncaught page errors');
  } finally { await page.close(); }
}

for (const width of [1280, 375]) {
  test(width + ': renders 9, then 18, then remaining; preserves curated cards', () => withPage(width, fixture, async page => {
    await page.waitForFunction(() => document.querySelectorAll('#telegramListings .listing-card').length === 9);
    assert.equal(await page.locator('#listings .listing-card').count(), 3);
    assert.equal(await page.locator('#listings + #telegram-listings').count(), 1);
    const more = page.getByRole('button', { name: 'Load More', exact: true });
    await more.click();
    assert.equal(await page.locator('#telegramListings .listing-card').count(), 18);
    await more.click();
    assert.equal(await page.locator('#telegramListings .listing-card').count(), 19);
    assert.equal(await more.isVisible(), false);
    assert.equal(await page.locator('#telegramListings h3').last().textContent(), 'Property 19');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  }));
  test(width + ': renders hostile text literally and encodes enquiry', () => withPage(width, fixture, async page => {
    const first = page.locator('#telegramListings .listing-card').first();
    await first.waitFor();
    assert.match(await first.textContent(), /<script>/);
    assert.match(await first.textContent(), /<img src=x onerror=/);
    assert.equal(await first.locator('script, [onerror]').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    const source = first.getByRole('link', { name: 'View on Telegram' });
    assert.equal(await source.getAttribute('href'), 'https://t.me/sarawakpropertyguru/100');
    const enquiry = new URL(await first.getByRole('link', { name: 'Enquire on WhatsApp' }).getAttribute('href'));
    assert.equal(enquiry.origin + enquiry.pathname, 'https://wa.me/60128810895');
    assert.match(enquiry.searchParams.get('text'), /Kuching <script>window.injected=true<\/script> & Home/);
    assert.match(enquiry.searchParams.get('text'), /https:\/\/t.me\/sarawakpropertyguru\/100/);
    assert.equal([...enquiry.searchParams.keys()].join(','), 'text');
    const second = page.locator('#telegramListings .listing-card').nth(1);
    assert.equal(await second.locator('.listing-highlights, .listing-code, .gallery-trigger').count(), 0);
    assert.match(await second.locator('img').getAttribute('alt'), /unavailable/i);
    assert.equal(await first.locator('img').getAttribute('loading'), 'lazy');
    assert.ok(Number(await first.locator('img').getAttribute('width')) > 0);
    assert.ok(Number(await first.locator('img').getAttribute('height')) > 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  }));
  test(width + ': dynamic gallery shares controls, wraps, traps focus and restores it', () => withPage(width, fixture, async (page, requests) => {
    const trigger = page.locator('#telegramListings .gallery-trigger').first();
    await trigger.waitFor();
    assert.equal(requests.some(url => /100-[12]\.webp/.test(url)), false, 'non-cover photos remain unloaded');
    await trigger.click();
    assert.equal(await page.getByRole('dialog').count(), 1);
    assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 3');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).overflow), 'hidden');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#galleryNext').evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#galleryClose').evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('ArrowLeft');
    assert.equal(await page.locator('#galleryCounter').textContent(), '3 / 3');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 3');
    await page.getByRole('button', { name: 'Next photo', exact: true }).click();
    assert.equal(await page.locator('#galleryCounter').textContent(), '2 / 3');
    await page.getByRole('button', { name: 'Previous photo', exact: true }).click();
    assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 3');
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').isVisible(), false);
    assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
    assert.notEqual(await page.evaluate(() => getComputedStyle(document.body).overflow), 'hidden');
    await trigger.click();
    await page.getByRole('button', { name: 'Close photo viewer' }).click();
    assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
    await page.locator('#listings .gallery-trigger').first().click();
    assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 3');
    await page.keyboard.press('Escape');
  }));
}

test('empty feed shows an honest empty state', () => withPage(375, [], async page => {
  await page.getByRole('status').filter({ hasText: /No.*listings/i }).waitFor();
  assert.equal(await page.locator('#telegramListings .listing-card').count(), 0);
  assert.equal(await page.locator('#loadMoreListings').isVisible(), false);
  const link = page.locator('#telegramListingsStatus a');
  assert.equal(await link.count(), 1);
  assert.equal(await link.getAttribute('href'), 'https://t.me/sarawakpropertyguru');
  assert.equal(await link.getAttribute('target'), '_blank');
  assert.match(await link.getAttribute('rel'), /noopener/);
}));

test('renders explicit structured fields safely and omits missing fields', () => withPage(375, [
  { ...fixture[0], location: 'Kuching <img src=x onerror=alert(1)>', property_type: 'Semi-detached house',
    bedrooms: 4, bathrooms: 3, parking: 2, negotiable: false, edited_at: 1789092600 },
  { ...fixture[1], status: undefined, facts: [] }
], async page => {
  const cards = page.locator('#telegramListings .listing-card');
  await cards.first().waitFor();
  const highlights = await cards.first().locator('.listing-highlights').textContent();
  assert.ok(highlights.includes('Kuching <img src=x onerror=alert(1)>'));
  assert.equal(await cards.first().locator('.listing-highlights img').count(), 0);
  assert.equal(await cards.first().locator('.listing-updated time').getAttribute('datetime'), '2026-09-11T02:10:00.000Z');
  assert.equal(await cards.nth(1).locator('.listing-location,.listing-highlights,.listing-updated').count(), 0);
}));
test('preserves Telegram copy formatting and shows exactly three prioritized highlights', () => withPage(375, [{
  ...fixture[0],
  source_text: 'First line\n\n  Indented detail\nRefer code: JRL',
  price: 'RM 1,500/month',
  facts: ['1,200 sq ft', '4 acres'],
  location: 'Jalan Uplands',
  reference: 'JRL',
  bedrooms: 3,
}], async page => {
  const card = page.locator('#telegramListings .listing-card').first();
  await card.waitFor();
  assert.equal(await card.locator('.listing-description').textContent(), 'First line\n\n  Indented detail\nRefer code: JRL');
  assert.equal(await card.locator('.listing-description').evaluate(node => getComputedStyle(node).whiteSpace), 'pre-wrap');
  assert.deepEqual(await card.locator('.listing-highlights span').allTextContents(), ['RM 1,500/month', '1,200 sq ft', 'Jalan Uplands']);
  assert.equal((await card.locator('.listing-highlights').textContent()).includes('JRL'), false);
  assert.equal(await card.locator('.listing-code').count(), 0);
}));
for (const [name, data, status] of [['HTTP failure', [], 503], ['invalid JSON', '{', 200], ['invalid shape', {}, 200]]) {
  test(name + ' shows an error state', () => withPage(375, data, async page => {
    await page.getByRole('status').filter({ hasText: /unable|could not/i }).waitFor();
    assert.equal(await page.locator('#telegramListings .listing-card').count(), 0);
    assert.equal(await page.locator('#loadMoreListings').isVisible(), false);
  }, status));
}
test('rejects unsafe Telegram links and image paths without inventing facts', () => {
  const urls = ['javascript:alert(1)', 'https://t.me.evil.test/sarawakpropertyguru/1', 'https://t.me/other/1', 'https://t.me/sarawakpropertyguru/1?next=evil', 'https://evil@t.me/sarawakpropertyguru/1', 'https://t.me/sarawakpropertyguru/1#evil'];
  const rows = urls.map((telegram_url, i) => ({ ...fixture[1], id: 'unsafe-' + i, telegram_url, images: ['https://evil.test/pixel.webp', 'assets/telegram/../../secret.webp'] }));
  return withPage(375, rows, async (page, requests) => {
    await page.locator('#telegramListings .listing-card').first().waitFor();
    assert.equal(await page.locator('#telegramListings a[href*="t.me"]').count(), 0);
    for (const link of await page.locator('#telegramListings a').all()) assert.equal((await link.getAttribute('href')).includes('evil'), false);
    assert.equal(requests.some(url => url.includes('evil.test')), false);
    assert.equal(await page.locator('#telegramListings .gallery-trigger').count(), 0);
  });
});
test('broken cover and gallery images use the local placeholder', () => withPage(375, fixture, async page => {
  await page.route('**/assets/telegram/100-*.webp', route => route.fulfill({ status: 404, body: '' }));
  const trigger = page.locator('#telegramListings .gallery-trigger').first();
  await trigger.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector('#telegramListings img')?.getAttribute('src') === 'assets/listing-placeholder.svg');
  assert.ok(await trigger.locator('img').evaluate(img => img.complete && img.naturalWidth > 0));
  await trigger.click();
  await page.waitForFunction(() => document.querySelector('#galleryImage').getAttribute('src') === 'assets/listing-placeholder.svg');
  assert.match(await page.locator('#galleryImage').getAttribute('alt'), /unavailable/i);
}));
test('a long title stays within the mobile gallery', () => withPage(375, [{ ...fixture[0], title: 'Property' + 'x'.repeat(350) }], async page => {
  await page.locator('#telegramListings .gallery-trigger').click();
  assert.equal(await page.locator('#galleryDialog').evaluate(node => node.scrollWidth <= innerWidth), true);
}));
test('newly loaded cards open their registered gallery and wrap a single image', () => withPage(375, fixture, async page => {
  await page.getByRole('button', { name: 'Load More', exact: true }).click();
  const trigger = page.locator('#telegramListings .listing-card').nth(9).locator('.gallery-trigger');
  await trigger.click();
  assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 1');
  assert.match(await page.locator('#galleryImage').getAttribute('src'), /91-0\.webp$/);
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#galleryCounter').textContent(), '1 / 1');
  await page.keyboard.press('Escape');
  assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
}));

(async () => {
  fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
  const server = http.createServer(async (req, res) => {
    try {
      const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      const filename = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
      if (!filename.startsWith(root + path.sep)) throw new Error('outside root');
      const body = await fs.readFile(filename);
      res.setHeader('Content-Type', ({ '.html': 'text/html', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.json': 'application/json' })[path.extname(filename)] || 'application/octet-stream');
      res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  siteUrl = 'http://127.0.0.1:' + server.address().port + '/';
  let failures = 0;
  try {
    browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    for (const { name, run } of tests) {
      try { await run(); console.log('PASS ' + name); }
      catch (error) { failures++; console.error('FAIL ' + name + '\n' + error.message); }
    }
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
  console.log((tests.length - failures) + ' passed, ' + failures + ' failed');
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });

