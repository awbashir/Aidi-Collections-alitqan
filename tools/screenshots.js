// Baseline / after screenshots of the main screens. Uses the Playwright install from the Aidi-AI frontend.
//   NODE_PATH="/Users/awbashir/Development/Aidi AI/Aidi-AI/frontend/node_modules" node tools/screenshots.js logs/screens/before
const { chromium } = require('playwright-core');
const outDir = process.argv[2] || 'logs/screens/out';
const FE = process.env.FE_URL || 'http://localhost:5177';
const PAGES = [
  ['home', '/home'], ['clients', '/clients'], ['client_query', '/client_query'], ['archive', '/archive'],
  ['users', '/users'], ['portfolios', '/portfolios'], ['contracts', '/contracts'], ['payment', '/payment'],
  ['collections_users', '/user_collection'], ['report_comments', '/report/users/comments'],
  ['report_paymentDates', '/report/users/paymentDates'], ['report_neglect', '/report/quality/neglect'],
  ['permissions', '/permissions'], ['settings', '/settings'], ['supervisor', '/supervisor'],
];
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ar' });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('  pageerror:', String(e).slice(0, 160)));
  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
  await page.screenshot({ path: `${outDir}/00_sign-in.png`, fullPage: false });
  await page.fill('input[name="username"]', 'aidi.dev');
  await page.fill('input[name="password"]', 'Aidi@2026');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/home', { timeout: 30000 }).catch(() => console.log('  did not reach /home'));
  let i = 1;
  for (const [name, path] of PAGES) {
    const t = Date.now();
    try {
      await page.goto(FE + path, { waitUntil: 'networkidle', timeout: 90000 });
      await page.waitForTimeout(1200);
    } catch (e) { console.log(`  ${name}: ${String(e).slice(0, 100)}`); }
    const f = `${outDir}/${String(i++).padStart(2, '0')}_${name}.png`;
    await page.screenshot({ path: f, fullPage: false });
    console.log(`${name.padEnd(22)} ${path.padEnd(28)} ${Date.now() - t}ms -> ${f}`);
  }
  // client detail: first row link on /clients. The link is target="_blank", so
  // follow the popup instead of screenshotting the list again.
  try {
    await page.goto(FE + '/clients', { waitUntil: 'networkidle', timeout: 90000 });
    const link = page.locator('a[href*="/clients/details/"]').first();
    if (await link.count()) {
      const [popup] = await Promise.all([
        ctx.waitForEvent('page', { timeout: 15000 }).catch(() => null),
        link.click(),
      ]);
      const target = popup || page;
      await target.waitForLoadState('networkidle').catch(() => {});
      await target.waitForTimeout(3000);
      await target.screenshot({ path: `${outDir}/${String(i++).padStart(2, '0')}_client_detail.png` });
      console.log('client_detail ok', popup ? '(popup)' : '(same tab)');
    }
  } catch (e) { console.log('client_detail:', String(e).slice(0, 100)); }

  // Phase 0 extras: the clients list opened from a hash URL (filters, sort and
  // Arabic text in the fragment), and the side nav in Arabic and English.
  // HASH_URL overrides the fragment; the default uses the dev data set.
  const HASH = process.env.HASH_URL ||
    '#sort=name:asc&contract=bf69e026-5a52-446c-99a6-b596f886dbf0&attr=' + encodeURIComponent('الشمري');
  const shot = async (name, path, opts = {}) => {
    const f = `${outDir}/${String(i++).padStart(2, '0')}_${name}.png`;
    try {
      await page.goto(FE + path, { waitUntil: 'networkidle', timeout: 90000 });
      await page.waitForTimeout(1500);
      if (opts.element) await page.locator(opts.element).first().screenshot({ path: f });
      else await page.screenshot({ path: f, fullPage: false });
      console.log(`${name.padEnd(22)} ${path.slice(0, 60)} -> ${f}`);
    } catch (e) { console.log(`  ${name}: ${String(e).slice(0, 120)}`); }
  };
  try {
    // A fresh navigation to a URL that carries a hash: the list must open in
    // the state the hash describes (after-shots) or ignore it (before-shots).
    await page.goto('about:blank');
    await shot('clients_hash', '/clients' + HASH);
    await page.setViewportSize({ width: 1440, height: 1400 });
    await shot('sidebar_ar', '/home', { element: '.side-nav' });
    // Switch to English through the header language menu.
    await page.locator('.header img[src*="/img/countries/"]').first().click();
    await page.getByText('English', { exact: true }).first().click();
    await page.waitForTimeout(1000);
    await shot('sidebar_en', '/home', { element: '.side-nav' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await shot('home_en', '/home');
    await page.goto('about:blank');
    await shot('clients_hash_en', '/clients' + HASH);
  } catch (e) { console.log('phase0 extras:', String(e).slice(0, 160)); }
  await browser.close();
})();
