/* Phase 3 frontend checks (requests, repayment plans, targets and team, sessions,
 * permission refresh, attention labels, gating), driven by headless Chrome through the
 * playwright-core install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase3.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots (ar and en) go to outDir (default logs/screens/phase3), plus verify.json
 * and verify.log. ONLY=<section,...> runs a subset: screens, requests, conflict, plans,
 * targets, team, sessions, refresh, attention, gating.
 *
 * Test data: only the synthetic cases ZZTEST-P1-0001…0004, the local test users
 * aidi.dev / aidi.reviewer / aidi.collector, and targets for **future** months on
 * aidi.collector and the ZZ-TEST contract (targets are append-only). Everything created
 * is labelled "ZZ-TEST phase3 …" and left closed: requests resolved or cancelled, plans
 * cancelled, a linked test document deleted, aidi.collector active, the reviewer role's
 * key restored.
 */
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const outDir = process.argv[2] || 'logs/screens/phase3';
fs.mkdirSync(outDir, { recursive: true });
const { chromium } = require(path.join(__dirname, '../../tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PASS = 'Aidi@2026';

const ADMIN = 'aidi.dev';
const REVIEWER = 'aidi.reviewer';
const COLLECTOR = 'aidi.collector';
const COLLECTOR_ID = '66c2dfbd-615f-4e99-bb0a-01228ca3e8b5';
const REVIEWER_ROLE = '09c4d34d-c67c-4866-93ac-320515cb548d'; // "ZZ-TEST corrections reviewer (local)"
const CONTRACT = 'bf69e026-5a52-446c-99a6-b596f886dbf0'; // ZZ-TEST portfolio's contract
const CASES = {
  1: { id: '44f5da48-0cf4-4686-a538-380c14c06ae3', acc: 'ZZTEST-P1-0001' },
  2: { id: '527b4757-aef0-455c-af8e-05ab84b746cb', acc: 'ZZTEST-P1-0002' },
  3: { id: 'e1afce87-41d5-461a-bd9b-79beab1f15d3', acc: 'ZZTEST-P1-0003' },
  4: { id: 'a206f02b-2ccc-4e06-8ed6-9f06ef796732', acc: 'ZZTEST-P1-0004' },
};
const STAMP = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');

/* ------------------------------------------------------------------ log + checks */
const logLines = [];
const results = [];
const log = (...a) => {
  const line = a.join(' ');
  logLines.push(line);
  console.log(line);
};
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 500) });
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + String(detail).slice(0, 300) : ''}`);
};

/* ------------------------------------------------------------------ API helpers */
const tokens = {};
async function apiLogin(user, fresh = false) {
  if (tokens[user] && !fresh) return tokens[user];
  const r = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: PASS }),
  });
  const j = await r.json();
  if (!j.access_token) throw new Error(`login ${user}: ${r.status} ${JSON.stringify(j).slice(0, 200)}`);
  tokens[user] = j.access_token;
  return tokens[user];
}
/** One API call as `user`; re-logs in once when the cached token was revoked. */
async function api(user, method, p, body, { retry = true, headers: extra = {} } = {}) {
  const tok = await apiLogin(user);
  const headers = { Authorization: 'Bearer ' + tok, ...extra };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = crypto.randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401 && retry) {
    await apiLogin(user, true);
    return api(user, method, p, body, { retry: false, headers: extra });
  }
  let j = null;
  try {
    j = await r.json();
  } catch {
    /* empty */
  }
  return { status: r.status, body: j };
}
const data = (r) => (r && r.body && 'data' in r.body ? r.body.data : r.body);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const riyadhToday = () => new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
const riyadhMonth = (plus = 0) => {
  const [y, m] = riyadhToday().split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + plus, 1));
  return d.toISOString().slice(0, 7);
};

/* ------------------------------------------------------------------- UI helpers */
async function login(page, user = ADMIN) {
  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !/sign-in/.test(String(u)), { timeout: 30000 });
  await settle(page, 600);
}
async function setLang(page, lang) {
  const dir = await page.evaluate(() => document.documentElement.dir);
  if ((lang === 'en' && dir === 'ltr') || (lang === 'ar' && dir === 'rtl')) return;
  await page.locator('.header img[src*="/img/countries/"]').first().click();
  await page.getByText(lang === 'en' ? 'English' : 'العربية', { exact: true }).first().click();
  await page.waitForTimeout(600);
}
const settle = async (page, ms = 900) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
};
const shot = async (page, name) => {
  await page.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
  await page.waitForTimeout(150);
  return page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: true });
};
const byTest = (page, id) => page.locator(`[data-testid="${id}"]`);
const navHrefs = (page) =>
  page.$$eval('.side-nav a[href]', (as) => as.map((a) => a.getAttribute('href')));
/** Items per sidebar group (top-level only), like verify-phase0. */
const groupItemCounts = (page) =>
  page.$$eval('.side-nav .menu-nav-group', (gs) =>
    gs.map(
      (g) =>
        [...g.querySelectorAll('.menu-item, .menu-collapse-item')].filter(
          (el) => !el.parentElement.closest('.menu-collapse > ul, .menu-collapse > div:not(.menu-collapse-item)')
        ).length
    )
  );
async function pickIn(page, testId, text) {
  const scope = byTest(page, testId).first();
  await scope.locator('.select__control').first().click();
  const opts = page.locator('.select__menu .select-option');
  await opts.first().waitFor({ timeout: 15000 });
  if (text) await opts.filter({ hasText: text }).first().click();
  else await opts.first().click();
}
const countOfTab = async (page, tabsTestId, value) => {
  const el = page.locator(`[data-testid="${tabsTestId}"] [data-tab="${value}"] [data-count]`).first();
  if (!(await el.count())) return null;
  const v = await el.getAttribute('data-count');
  return v === '' || v === null ? null : Number(v);
};

module.exports = {
  FE, API, ADMIN, REVIEWER, COLLECTOR, COLLECTOR_ID, REVIEWER_ROLE, CONTRACT, CASES, STAMP, outDir, want,
  log, check, results, api, apiLogin, data, sleep, riyadhToday, riyadhMonth,
  login, setLang, settle, shot, byTest, navHrefs, groupItemCounts, pickIn, countOfTab,
};

if (require.main === module) {
  (async () => {
    const browser = await chromium.launch({ executablePath: CHROME, headless: true });
    const pageErrors = [];
    /* `revoked: true` marks a context whose session this script ends on purpose: legacy
     * screens then leave unhandled 401 rejections (AxiosError), which are logged, not failed. */
    const newPage = async ({ revoked = false, ...opts } = {}) => {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...opts });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => {
        const where = (e.stack || '').split('\n').slice(0, 4).join(' | ');
        const msg = `${String(e).slice(0, 120)} @ ${page.url()} :: ${where.slice(0, 300)}`;
        if (revoked && /AxiosError/.test(String(e))) {
          log(`note: expected after the revocation (legacy screen, unhandled 401): ${msg.slice(0, 160)}`);
          return;
        }
        errors.push(msg);
        pageErrors.push(msg);
      });
      page.on('response', (r) => {
        if (r.url().startsWith(API + '/v2/') && r.status() >= 500) {
          errors.push(`HTTP ${r.status()} ${r.url()}`);
          pageErrors.push(`HTTP ${r.status()} ${r.url()}`);
        }
      });
      return { ctx, page, errors };
    };
    const flows = require('./verify-phase3-flows.cjs');
    try {
      await flows.run({ browser, newPage });
    } catch (e) {
      check('script finished without an exception', false, e.stack || String(e));
    }
    try {
      await flows.cleanup();
    } catch (e) {
      check('cleanup finished without an exception', false, e.stack || String(e));
    }
    check('no page errors or 5xx from /v2 in any browser context', pageErrors.length === 0, pageErrors.slice(0, 5).join(' | '));
    await browser.close();
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify(results, null, 1));
    const failed = results.filter((r) => !r.ok).length;
    log(`\n${results.length - failed}/${results.length} checks pass`);
    fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
    process.exit(failed ? 1 : 0);
  })();
}
