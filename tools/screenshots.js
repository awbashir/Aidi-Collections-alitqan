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
  await browser.close();
})();
