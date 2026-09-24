/* Phase 1 frontend checks, driven by headless Chrome through the playwright-core
 * install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase1.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Writes ar/en screenshots into outDir (default logs/screens/phase1) and a JSON
 * summary to outDir/verify.json. Exits non-zero when a check fails.
 *
 * The end-to-end flows write to the synthetic test case ZZTEST-P1-0001 (portfolio
 * "ZZ-TEST Phase1 frontend 2026-09-23"). ONLY=<section,...> runs a subset:
 * screens, gating, payment, promise, followup, note, contact, correction, conflict.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '../../tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const outDir = process.argv[2] || 'logs/screens/phase1';
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PASS = 'Aidi@2026';
const ADMIN = 'aidi.dev';
const COLLECTOR = 'aidi.collector';
const CASE1 = '44f5da48-0cf4-4686-a538-380c14c06ae3';
const IDENTITY1 = '1999000001';

fs.mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + String(detail).slice(0, 300) : ''}`);
};

/* ------------------------------------------------------------ API helpers */
const tokens = {};
async function apiLogin(user) {
  if (tokens[user]) return tokens[user];
  const r = await fetch(API + '/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: PASS }),
  });
  const j = await r.json();
  tokens[user] = j.access_token;
  return tokens[user];
}
async function api(user, method, p, body) {
  const tok = await apiLogin(user);
  const headers = { Authorization: 'Bearer ' + tok };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = require('crypto').randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let j = null;
  try { j = await r.json(); } catch { /* empty */ }
  return { status: r.status, body: j };
}

/* ------------------------------------------------------------ UI helpers */
async function login(page, user = ADMIN) {
  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/home', { timeout: 30000 });
}
async function setLang(page, lang) {
  // wait for the app to finish restoring its persisted state first: a switch made before
  // rehydration is overwritten by the saved language (a test race, not a product path)
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.locator('.header img[src*="/img/countries/"]').first().waitFor({ timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(400);
  const dir = await page.evaluate(() => document.documentElement.dir);
  if ((lang === 'en' && dir === 'ltr') || (lang === 'ar' && dir === 'rtl')) return;
  await page.locator('.header img[src*="/img/countries/"]').first().click();
  await page.getByText(lang === 'en' ? 'English' : 'العربية', { exact: true }).first().click();
  await page.waitForTimeout(600);
}
const settle = async (page, ms = 1200) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
};
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: true });

module.exports = { check, api, login, setLang, settle, shot, FE, API, CASE1, IDENTITY1, ADMIN, COLLECTOR, results, outDir, want };

if (require.main === module) {
  (async () => {
    const browser = await chromium.launch({ executablePath: CHROME, headless: true });
    const newPage = async () => {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
      page.on('response', (r) => {
        if (r.url().startsWith(API + '/v2/') && r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
      });
      return { ctx, page, errors };
    };
    const sections = require('./verify-phase1-flows.cjs');
    try {
      await sections.run({ browser, newPage, check, api, login, setLang, settle, shot, want, FE_URL: FE, API_URL: API });
    } catch (e) {
      check('script finished without an exception', false, e.stack || String(e));
    }
    await browser.close();
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify(results, null, 1));
    const failed = results.filter((r) => !r.ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks pass`);
    process.exit(failed ? 1 : 0);
  })();
}
