/* Phase 4 frontend checks: the reports hub and the generic report viewer, driven by
 * headless Chrome through the playwright-core install in ../tools/node_modules.
 * Start the dev server first (npx vite --port 5177).
 *
 *   node CAWP-frontend/scripts/verify-phase4.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots (ar and en) go to outDir (default logs/screens/phase4), plus verify.json
 * and verify.log. ONLY=<section,...> runs a subset:
 *   hub, open, filters, totals, export, exports, hidden, recording, screens
 *
 * Writes: report exports only (private to aidi.dev, expire after 7 days). The
 * `hidden`/`recording` sections change the local test role "ZZ-TEST corrections reviewer
 * (local)" (aidi.reviewer) for about two minutes: + dashbord_admin, − the two recording
 * keys; the role is restored exactly afterwards (also on failure). No business data is
 * written. The recording download itself is never clicked: /cdr/recording would reach
 * the configured PBX over SSH.
 */
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const outDir = process.argv[2] || 'logs/screens/phase4';
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
const REVIEWER_ROLE = '09c4d34d-c67c-4866-93ac-320515cb548d'; // "ZZ-TEST corrections reviewer (local)"
const CDR_DAY = '2026-09-20'; // the local CDR fixture's day (cawp-cdr-test-db)

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
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== '' ? '  -- ' + String(detail).slice(0, 300) : ''}`);
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
  if (!j.access_token) throw new Error(`login ${user}: ${r.status}`);
  tokens[user] = j.access_token;
  return tokens[user];
}
async function api(user, method, p, body, retry = true) {
  const tok = await apiLogin(user);
  const headers = { Authorization: 'Bearer ' + tok };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = crypto.randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401 && retry) {
    await apiLogin(user, true);
    return api(user, method, p, body, false);
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
const monthBounds = () => {
  const [y, m] = riyadhToday().split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const mm = String(m).padStart(2, '0');
  return { from: `${y}-${mm}-01`, to: `${y}-${mm}-${String(last).padStart(2, '0')}` };
};
/** The frontend's money text: re-grouped decimal string, never a float. */
function formatMoney(value) {
  if (value === null || value === undefined || value === '') return '—';
  const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(String(value).trim());
  if (!m) return String(value);
  const sign = m[1] === '-' ? '-' : '';
  const intPart = (m[2] || '0').replace(/^0+(?=\d)/, '');
  let frac = m[3] || '';
  if (frac.length < 2) frac = frac.padEnd(2, '0');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const isZero = /^0*$/.test(intPart) && /^0*$/.test(frac);
  return `${isZero ? '' : sign}${grouped}.${frac}`;
}
const qsOf = (url) => Object.fromEntries(new URL(url).searchParams.entries());

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
const navHrefs = (page) => page.$$eval('.side-nav a[href]', (as) => as.map((a) => a.getAttribute('href')));
const groupItemCounts = (page) =>
  page.$$eval('.side-nav .menu-nav-group', (gs) =>
    gs.map(
      (g) =>
        [...g.querySelectorAll('.menu-item, .menu-collapse-item')].filter(
          (el) => !el.parentElement.closest('.menu-collapse > ul, .menu-collapse > div:not(.menu-collapse-item)')
        ).length
    )
  );

/** The table's rows request of a report (not the chart's 200-row loads unless asked). */
const isRows = (id, { chart = false } = {}) => (r) => {
  const u = r.url();
  if (!u.startsWith(`${API}/v2/reports/${id}/rows`) || r.request().method() !== 'GET') return false;
  const size = new URL(u).searchParams.get('page_size');
  return chart ? true : size !== '200' || !['RPT-20', 'RPT-21', 'RPT-23', 'RPT-24'].includes(id);
};
/** Navigate (or run `action`) and return the rows response that follows. */
async function rowsAfter(page, id, action, opts = {}) {
  const wait = page.waitForResponse(isRows(id, opts), { timeout: 60000 });
  await action();
  const resp = await wait;
  let body = null;
  try {
    body = await resp.json();
  } catch {
    /* empty */
  }
  await settle(page, 500);
  return { resp, url: resp.url(), qs: qsOf(resp.url()), status: resp.status(), body: body && body.data };
}
const tableReady = (page) =>
  page.waitForFunction(
    () => document.querySelector('[data-testid="p4-row"], [data-testid="p1-empty"], [data-testid="p1-error"], [data-testid="p1-forbidden"]'),
    null,
    { timeout: 60000 }
  );

/** Minimal RFC 4180 CSV parser (quoted fields may hold commas, quotes and newlines). */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let q = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else q = false;
      } else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else field += c;
  }
  if (field !== '' || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/** Reads an XLSX with python3's zipfile: sheet names, RTL flags and data-row counts. */
function inspectXlsx(file) {
  const py = `
import json, re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
wb = z.read('xl/workbook.xml').decode('utf-8')
names = re.findall(r'<sheet [^>]*name="([^"]+)"', wb)
out = []
for i, n in enumerate(names, 1):
    x = z.read('xl/worksheets/sheet%d.xml' % i).decode('utf-8')
    out.append({'name': n, 'rtl': 'rightToLeft="1"' in x or "rightToLeft='1'" in x or 'rightToLeft="true"' in x,
                'rows': len(re.findall(r'<row[ >]', x)), 'formulas': x.count('<f>') + x.count('<f ')})
print(json.dumps(out, ensure_ascii=False))
`;
  return JSON.parse(execFileSync('python3', ['-c', py, file]).toString());
}

/* ================================================================== sections */

/* ------------------------------------------------ hub: exactly what GET /v2/reports returns */
async function hub({ newPage }) {
  for (const user of [ADMIN, COLLECTOR, REVIEWER]) {
    const apiList = data(await api(user, 'GET', '/v2/reports'));
    const ids = apiList.items.map((i) => i.id);
    const { page, ctx } = await newPage();
    await login(page, user);
    await setLang(page, 'en');
    await page.goto(`${FE}/reports`, { waitUntil: 'networkidle' });
    await byTest(page, 'p4-report-card').first().waitFor({ timeout: 30000 });
    const cards = await page.$$eval('[data-testid="p4-report-card"]', (els) => els.map((e) => e.getAttribute('data-report')));
    const grouped = apiList.families.flatMap((f) => f.reports);
    check(`hub ${user}: the cards are exactly GET /v2/reports (${ids.length}), grouped by family`, JSON.stringify([...cards].sort()) === JSON.stringify([...ids].sort()) && JSON.stringify(cards) === JSON.stringify(grouped), `${cards.join(',')} vs ${ids.join(',')}`);
    const fams = await page.$$eval('[data-testid="p4-family"]', (els) => els.map((e) => e.getAttribute('data-family')));
    check(`hub ${user}: families in the API order`, JSON.stringify(fams) === JSON.stringify(apiList.families.map((f) => f.family)), fams.join(','));
    const count = await byTest(page, 'p4-hub-tabs').locator('[data-tab="reports"] [data-count]').getAttribute('data-count');
    check(`hub ${user}: the tab count equals total`, Number(count) === apiList.total, count);
    const hrefs = await navHrefs(page);
    check(`hub ${user}: the "Reports hub" nav item is present`, hrefs.includes('/reports'), '');
    if (user === ADMIN) {
      // labels in both languages and descriptions
      const first = apiList.items[0];
      const card = page.locator(`[data-testid="p4-report-card"][data-report="${first.id}"]`);
      const text = await card.innerText();
      check('hub: a card shows the English label, the Arabic label and the description', text.includes(first.labels.en) && text.includes(first.labels.ar) && text.includes(first.description.en.slice(0, 30)), text.slice(0, 120));
      // search, hash, reload, Back
      await byTest(page, 'p4-hub-search').fill('collections');
      await page.waitForTimeout(700);
      let shown = await page.$$eval('[data-testid="p4-report-card"]', (els) => els.map((e) => e.getAttribute('data-report')));
      const expect = apiList.items
        .filter((r) => [r.id, r.labels.en, r.labels.ar, r.description.en, r.description.ar].some((s) => (s || '').toLowerCase().includes('collections')))
        .map((r) => r.id);
      check('hub: search "collections" shows the matching reports', JSON.stringify(shown) === JSON.stringify(expect) && shown.length > 0, shown.join(','));
      check('hub: the search is in the URL hash', /#.*q=collections/.test(page.url()), page.url());
      await page.reload({ waitUntil: 'networkidle' });
      await byTest(page, 'p4-report-card').first().waitFor({ timeout: 30000 });
      shown = await page.$$eval('[data-testid="p4-report-card"]', (els) => els.map((e) => e.getAttribute('data-report')));
      check('hub: the search survives a reload', JSON.stringify(shown) === JSON.stringify(expect) && (await byTest(page, 'p4-hub-search').inputValue()) === 'collections', shown.join(','));
      await byTest(page, 'p4-hub-search').fill('RPT-25');
      await page.waitForTimeout(700);
      shown = await page.$$eval('[data-testid="p4-report-card"]', (els) => els.map((e) => e.getAttribute('data-report')));
      check('hub: search by id', JSON.stringify(shown) === '["RPT-25"]', shown.join(','));
      await byTest(page, 'p4-hub-search').fill('تحصيلاتي');
      await page.waitForTimeout(700);
      shown = await page.$$eval('[data-testid="p4-report-card"]', (els) => els.map((e) => e.getAttribute('data-report')));
      check('hub: Arabic search in the English UI', shown.includes('RPT-07'), shown.join(','));
      await byTest(page, 'p4-hub-search').fill('zzzz-no-such-report');
      await page.waitForTimeout(700);
      check('hub: no match shows the empty state', (await byTest(page, 'p1-empty').count()) === 1 && (await byTest(page, 'p4-report-card').count()) === 0);
      await byTest(page, 'p4-hub-search').fill('');
      await page.waitForTimeout(700);
      // classic links: every card's classic link is a route the user may open
      const classic = await page.$$eval('[data-testid="p4-classic-link"]', (as) => as.map((a) => a.getAttribute('href')));
      check('hub: classic links to the legacy screens', classic.includes('/user_collection') && classic.includes('/report/users/comments') && classic.includes('/report/collectors/nextCalls'), [...new Set(classic)].join(','));
      await page.locator('[data-testid="p4-classic-all"] a[href="/report/quality/neglect"]').click();
      await settle(page, 1200);
      check('hub: a classic link opens the legacy screen, which still works', /\/report\/quality\/neglect/.test(page.url()) && !/access-denied/.test(page.url()), page.url());
      // sidebar: the new item leads the reports in Recovery & team
      await page.goto(`${FE}/reports`, { waitUntil: 'networkidle' });
      const counts = await groupItemCounts(page);
      check('hub: aidi.dev sidebar items per group are [6,13,9] (Phase 3 was [6,12,9]; + Reports hub)', JSON.stringify(counts) === '[6,13,9]', JSON.stringify(counts));
      // the card link opens the viewer
      await page.locator('[data-testid="p4-report-card"][data-report="RPT-12"] [data-testid="p4-report-open"]').click();
      await byTest(page, 'p4-viewer').waitFor({ timeout: 30000 });
      check('hub: a card opens /reports/RPT-12', /\/reports\/RPT-12$/.test(page.url().split('#')[0]), page.url());
    }
    if (user === COLLECTOR) {
      check('hub aidi.collector: sees exactly RPT-02/05/07/10/11/16/17/18/19', ids.join(',') === 'RPT-02,RPT-05,RPT-07,RPT-10,RPT-11,RPT-16,RPT-17,RPT-18,RPT-19', ids.join(','));
      await page.goto(`${FE}/reports/RPT-06`, { waitUntil: 'networkidle' });
      await settle(page, 1000);
      check('hub aidi.collector: /reports/RPT-06 (no key) shows the unauthorized state', (await byTest(page, 'p1-forbidden').count()) === 1, page.url());
    }
    await ctx.close();
  }
  // the derived key mirrors the registry: every report's any-of keys are in REPORT_KEYS
  const src = fs.readFileSync(path.join(__dirname, '../src/utils/v2/derivedPermissions.ts'), 'utf8');
  const block = src.slice(src.indexOf('export const REPORT_KEYS'), src.indexOf('] as const', src.indexOf('export const REPORT_KEYS')));
  const listed = new Set([...block.matchAll(/'([a-zA-Z_]+)'/g)].map((m) => m[1]));
  const keys = new Set();
  for (const user of [ADMIN, COLLECTOR]) {
    const l = data(await api(user, 'GET', '/v2/reports'));
    l.items.forEach((i) => i.permissions.forEach((k) => keys.add(k)));
  }
  const missing = [...keys].filter((k) => !listed.has(k));
  check('hub: the derived reports_any keys cover every report key the API uses', missing.length === 0, `missing: ${missing.join(',')} (of ${keys.size})`);
}

/* ------------------------------------------------ every report opens with its defaults */
async function open({ newPage }) {
  const month = monthBounds();
  const today = riyadhToday();
  for (const user of [ADMIN, COLLECTOR]) {
    const ids = data(await api(user, 'GET', '/v2/reports')).items.map((i) => i.id);
    const { page, ctx, errors } = await newPage();
    await login(page, user);
    await setLang(page, user === ADMIN ? 'en' : 'ar');
    let ok = 0;
    for (const id of ids) {
      const before = errors.length;
      const r = await rowsAfter(page, id, () => page.goto(`${FE}/reports/${id}`, { waitUntil: 'domcontentloaded' }));
      await tableReady(page);
      await settle(page, 400);
      const uiTotal = await byTest(page, 'p4-row-count').getAttribute('data-total').catch(() => null);
      const countTile = await page.locator('[data-testid="p4-total"][data-key="count"]').getAttribute('data-value').catch(() => null);
      const rows = await byTest(page, 'p4-row').count();
      const bad = (await byTest(page, 'p1-error').count()) + (await byTest(page, 'p4-validation').count());
      const good =
        r.status === 200 &&
        bad === 0 &&
        errors.length === before &&
        Number(uiTotal) === r.body.total &&
        Number(countTile) === r.body.totals.count &&
        rows === r.body.items.length;
      if (good) ok += 1;
      check(`open ${user} ${id}: 200, ${r.body ? r.body.total : '?'} rows, totals shown, no page error`, good, `status ${r.status} ui ${uiTotal} tile ${countTile} rows ${rows}/${r.body && r.body.items.length} bad ${bad} errs ${errors.slice(before).join(' | ')}`);
      // default filters the report notes ask for
      if (id === 'RPT-16') check(`open ${user} RPT-16: defaults to the current Riyadh month`, r.qs.callback_from === month.from && r.qs.callback_to === month.to, JSON.stringify(r.qs));
      if (id === 'RPT-04' || id === 'RPT-05')
        check(`open ${user} ${id}: the required created range defaults to the current Riyadh month`, r.qs.created_from === month.from && r.qs.created_to === month.to, JSON.stringify(r.qs));
      if (id === 'RPT-04' || id === 'RPT-05') check(`open ${user} ${id}: created is marked required`, (await page.locator('[data-testid="p4-filter-created"][data-required="true"] [data-testid="p4-required"]').count()) === 1);
      if (id === 'RPT-25') check(`open ${user} RPT-25: call date defaults to today (Riyadh)`, r.qs.call_date_from === today && r.qs.call_date_to === today, JSON.stringify(r.qs));
      if (id === 'RPT-19') check(`open ${user} RPT-19: month defaults to the current Riyadh month`, r.qs.month === today.slice(0, 7), JSON.stringify(r.qs));
      if (id === 'RPT-23') check(`open ${user} RPT-23: hours 8–17 and mode notes`, r.qs.from_hour === '8' && r.qs.to_hour === '17' && r.qs.mode === 'notes', JSON.stringify(r.qs));
      if (id === 'RPT-24') check(`open ${user} RPT-24: month to date, summary section first`, r.qs.activity_date_from === month.from && r.qs.activity_date_to === today && r.qs.section === 'summary' && (await byTest(page, 'p4-section-row').first().getAttribute('data-section')) === 'summary', JSON.stringify(r.qs));
      if (id === 'RPT-18') check(`open ${user} RPT-18: today, rows grouped by section`, r.qs.activity_date_from === today && (await byTest(page, 'p4-section-row').count()) >= 1, await byTest(page, 'p4-section-row').count());
      if (id === 'RPT-20' || id === 'RPT-21') check(`open ${user} ${id}: defaults to today`, Object.values(r.qs).filter((v) => v === today).length === 2, JSON.stringify(r.qs));
      // the request carries the default sort and page size of the definition
      if (r.qs.sort === undefined || r.qs.page !== '1') check(`open ${user} ${id}: sort and page sent`, false, JSON.stringify(r.qs));
    }
    check(`open ${user}: all ${ids.length} reports load with default filters`, ok === ids.length, `${ok}/${ids.length}`);
    await ctx.close();
  }
}

/* ------------------------------------------------ filters, sort, hash, Back, 422 */
async function filters({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  const id = 'RPT-06';
  let r = await rowsAfter(page, id, () => page.goto(`${FE}/reports/${id}`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  check('filters: the first request has only sort, page, page_size', JSON.stringify(Object.keys(r.qs).sort()) === '["page","page_size","sort"]' && r.qs.sort === '-created_at' && r.qs.page_size === '50', JSON.stringify(r.qs));
  // enum
  r = await rowsAfter(page, id, () => byTest(page, 'p4-enum-status-approved').click());
  check('filters: status chip -> status=approved', r.qs.status === 'approved' && r.qs.page === '1', JSON.stringify(r.qs));
  r = await rowsAfter(page, id, () => byTest(page, 'p4-enum-status-reversed').click());
  check('filters: a second chip -> status=approved,reversed', r.qs.status === 'approved,reversed', JSON.stringify(r.qs));
  // date range (inclusive civil dates)
  r = await rowsAfter(page, id, () => byTest(page, 'p4-in-payment_date_from').fill('2026-09-01'));
  check('filters: payment date from', r.qs.payment_date_from === '2026-09-01', JSON.stringify(r.qs));
  r = await rowsAfter(page, id, () => byTest(page, 'p4-in-payment_date_to').fill('2026-09-30'));
  check('filters: payment date to', r.qs.payment_date_to === '2026-09-30', JSON.stringify(r.qs));
  // ids selector: contract, then the portfolio selector depends on it
  await byTest(page, 'p4-ids-trigger-contract').click();
  const optResp = await page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-options/contract`), { timeout: 30000 });
  check('filters: the contract selector calls /v2/report-options/contract?report=RPT-06', qsOf(optResp.url()).report === 'RPT-06', optResp.url());
  const optBody = (await optResp.json()).data;
  await page.locator('[data-testid="p4-ids-panel-contract"] [data-testid="p4-option"]').first().waitFor({ timeout: 15000 });
  const firstOpt = await page.locator('[data-testid="p4-ids-panel-contract"] [data-testid="p4-option"]').first().getAttribute('data-id');
  check('filters: the contract options are the API page', firstOpt === optBody.items[0].id && (await page.locator('[data-testid="p4-ids-panel-contract"] [data-testid="p4-option"]').count()) === optBody.items.length, `${optBody.total} options`);
  // search inside the selector
  const searchResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-options/contract`) && !!qsOf(x.url()).q, { timeout: 30000 });
  await page.locator('[data-testid="p4-ids-panel-contract"] [data-testid="p4-ids-search"]').fill(optBody.items[0].label.slice(0, 3));
  const sr = await searchResp;
  check('filters: typing searches the selector (q=…)', qsOf(sr.url()).q === optBody.items[0].label.slice(0, 3), sr.url());
  r = await rowsAfter(page, id, () => page.locator(`[data-testid="p4-ids-panel-contract"] [data-testid="p4-option"][data-id="${optBody.items[0].id}"] input`).click());
  check('filters: choosing a contract -> contract=<id>', r.qs.contract === optBody.items[0].id, JSON.stringify(r.qs));
  await page.keyboard.press('Escape');
  const pfResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-options/portfolio`), { timeout: 30000 });
  await byTest(page, 'p4-ids-trigger-portfolio').click();
  const pf = await pfResp;
  check('filters: the portfolio selector sends its depends_on (contract)', qsOf(pf.url()).contract === optBody.items[0].id, pf.url());
  await page.keyboard.press('Escape');
  // text + amount (commit on Enter)
  r = await rowsAfter(page, id, async () => {
    await byTest(page, 'p4-in-amount_from').fill('100');
    await byTest(page, 'p4-in-amount_from').press('Enter');
  });
  check('filters: amount from on Enter', r.qs.amount_from === '100', JSON.stringify(r.qs));
  // sort by header
  r = await rowsAfter(page, id, () => byTest(page, 'p4-sort-amount').click());
  check('filters: clicking the Amount header -> sort=-amount, page 1', r.qs.sort === '-amount' && r.qs.page === '1', JSON.stringify(r.qs));
  r = await rowsAfter(page, id, () => byTest(page, 'p4-sort-amount').click());
  check('filters: clicking again -> sort=amount', r.qs.sort === 'amount', JSON.stringify(r.qs));
  const beforeReload = r.qs;
  const hash = new URL(page.url()).hash;
  check('filters: everything is in the hash', /status=approved,reversed/.test(hash) && /contract=/.test(hash) && /sort=amount/.test(hash) && /amount_from=100/.test(hash), hash);
  // reload: the same request, the labels resolved
  const selResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-options/contract`) && !!qsOf(x.url()).selected, { timeout: 30000 });
  r = await rowsAfter(page, id, () => page.reload({ waitUntil: 'domcontentloaded' }));
  check('filters: reload sends exactly the same rows request', JSON.stringify(r.qs) === JSON.stringify(beforeReload), `${JSON.stringify(r.qs)} vs ${JSON.stringify(beforeReload)}`);
  const sel = await selResp;
  check('filters: reload resolves the chosen contract label with selected=', qsOf(sel.url()).selected === optBody.items[0].id, sel.url());
  await page.waitForTimeout(600);
  const trig = await byTest(page, 'p4-ids-trigger-contract').innerText();
  check('filters: the selector shows the label after reload', trig.includes(optBody.items[0].label), trig);
  check('filters: the restored sort shows on the header', (await page.locator('th[data-col="amount"]').getAttribute('aria-sort')) === 'ascending');
  check('filters: the restored inputs', (await byTest(page, 'p4-in-payment_date_from').inputValue()) === '2026-09-01' && (await byTest(page, 'p4-enum-status-approved').getAttribute('aria-pressed')) === 'true');
  // page 2 then Back
  if (r.body.pages > 1) {
    r = await rowsAfter(page, id, () => page.locator('.pagination .pagination-pager').filter({ hasText: /^2$/ }).first().click());
    check('filters: page 2', r.qs.page === '2', JSON.stringify(r.qs));
    r = await rowsAfter(page, id, () => page.goBack());
    check('filters: Back returns to page 1 with the same filters', r.qs.page === '1' && r.qs.sort === 'amount', JSON.stringify(r.qs));
  }
  r = await rowsAfter(page, id, () => page.goBack());
  check('filters: Back again undoes the sort change', r.qs.sort === '-amount', JSON.stringify(r.qs));
  r = await rowsAfter(page, id, () => page.goForward());
  check('filters: Forward re-applies it', r.qs.sort === 'amount', JSON.stringify(r.qs));
  // 422 mapped to bilingual field errors
  r = await rowsAfter(page, id, async () => {
    await byTest(page, 'p4-in-amount_from').fill('10.555');
    await byTest(page, 'p4-in-amount_from').press('Enter');
  });
  check('filters: an over-precise amount -> 422 validation_failed', r.status === 422, r.status);
  const fe = page.locator('[data-testid="p4-field-error"][data-param="amount_from"]');
  await fe.waitFor({ timeout: 10000 });
  check('filters: the field error sits under Amount (en)', (await fe.getAttribute('data-code')) === 'too_precise' && /At most 2 decimals/.test(await fe.innerText()), await fe.innerText());
  check('filters: the summary line names the filter', /Payment amount \(From\): At most 2 decimals/.test(await byTest(page, 'p4-validation').innerText()), await byTest(page, 'p4-validation').innerText());
  await shot(page, '20_filter_error_en');
  await setLang(page, 'ar');
  check('filters: the same field error in Arabic', /منزلتان عشريتان/.test(await fe.innerText()), await fe.innerText());
  await shot(page, '20_filter_error_ar');
  await setLang(page, 'en');
  r = await rowsAfter(page, id, async () => {
    await byTest(page, 'p4-in-payment_date_from').fill('2026-10-05');
  });
  const codes = await page.$$eval('[data-testid="p4-field-error"]', (els) => els.map((e) => `${e.getAttribute('data-param')}:${e.getAttribute('data-code')}`));
  check('filters: every bad parameter at once (reversed range + precision)', codes.includes('payment_date_to:reversed_range') && codes.includes('amount_from:too_precise'), codes.join(','));
  // reset
  r = await rowsAfter(page, id, () => byTest(page, 'p4-filters-reset').click());
  check('filters: reset -> the default request again', JSON.stringify(Object.keys(r.qs).sort()) === '["page","page_size","sort"]' && r.status === 200, JSON.stringify(r.qs));

  // RPT-16: clearing the default month survives a reload (sends no window)
  let r16 = await rowsAfter(page, 'RPT-16', () => page.goto(`${FE}/reports/RPT-16`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  r16 = await rowsAfter(page, 'RPT-16', () => byTest(page, 'p4-in-callback_from').fill(''));
  r16 = await rowsAfter(page, 'RPT-16', () => byTest(page, 'p4-in-callback_to').fill(''));
  check('filters: RPT-16 cleared window -> no callback params', !('callback_from' in r16.qs) && !('callback_to' in r16.qs), JSON.stringify(r16.qs));
  r16 = await rowsAfter(page, 'RPT-16', () => page.reload({ waitUntil: 'domcontentloaded' }));
  check('filters: RPT-16 the cleared window survives a reload (hash ~)', !('callback_from' in r16.qs) && /callback_from=~/.test(page.url()), page.url());

  // RPT-04: selectors carry the required created window (depends_on)
  await rowsAfter(page, 'RPT-04', () => page.goto(`${FE}/reports/RPT-04`, { waitUntil: 'domcontentloaded' }));
  const recResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-options/recorder`), { timeout: 30000 });
  await byTest(page, 'p4-ids-trigger-recorder').click();
  const rec = await recResp;
  const m = monthBounds();
  check('filters: RPT-04 recorder selector sends created_from/created_to', qsOf(rec.url()).created_from === m.from && qsOf(rec.url()).created_to === m.to && rec.status() === 200, rec.url());
  await page.keyboard.press('Escape');
  // single-choice enum
  const r4 = await rowsAfter(page, 'RPT-04', () => byTest(page, 'p4-enum-note_selection-latest_per_case').click());
  check('filters: RPT-04 note selection (single choice) -> note_selection=latest_per_case', r4.qs.note_selection === 'latest_per_case', JSON.stringify(r4.qs));
  // RPT-23 hour range and mode
  await rowsAfter(page, 'RPT-23', () => page.goto(`${FE}/reports/RPT-23#day=${CDR_DAY}`, { waitUntil: 'domcontentloaded' }));
  let r23 = await rowsAfter(page, 'RPT-23', () => byTest(page, 'p4-in-from_hour').selectOption('0'));
  check('filters: RPT-23 from hour 0', r23.qs.from_hour === '0' && r23.qs.day === CDR_DAY, JSON.stringify(r23.qs));
  r23 = await rowsAfter(page, 'RPT-23', () => byTest(page, 'p4-enum-mode-calls').click());
  check('filters: RPT-23 mode calls', r23.qs.mode === 'calls' && r23.status === 200, JSON.stringify(r23.qs));
  await ctx.close();
}

/* ------------------------------------------------ exact totals as returned */
async function totals({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  for (const [id, hash] of [
    ['RPT-06', '#payment_date_from=2026-07-01&payment_date_to=2026-07-31'],
    ['RPT-12', ''],
    ['RPT-02', ''],
    ['RPT-09', '#due_date_from=2026-09-01&due_date_to=2026-09-30'],
    ['RPT-25', `#call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}`],
  ]) {
    const r = await rowsAfter(page, id, () => page.goto(`${FE}/reports/${id}${hash}`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    const tiles = await page.$$eval('[data-testid="p4-total"]', (els) =>
      els.map((e) => ({ key: e.getAttribute('data-key'), value: e.getAttribute('data-value'), text: e.querySelector('div[dir="ltr"]').innerText.trim() }))
    );
    const def = data(await api(ADMIN, 'GET', `/v2/reports/${id}`));
    const bad = [];
    for (const tdef of def.totals) {
      const t = tiles.find((x) => x.key === tdef.key);
      const v = r.body.totals[tdef.key];
      if (!t) {
        bad.push(`${tdef.key}: missing`);
        continue;
      }
      if (t.value !== String(v)) bad.push(`${tdef.key}: ${t.value} vs ${v}`);
      const want = tdef.type === 'money' ? formatMoney(v) : String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      if (t.text !== want) bad.push(`${tdef.key}: text ${t.text} vs ${want}`);
    }
    check(`totals ${id}: every total tile equals the API exactly (${def.totals.length})`, bad.length === 0 && tiles.length === def.totals.length, bad.join(' | ') || tiles.map((x) => `${x.key}=${x.value}`).slice(0, 6).join(' '));
    if (id === 'RPT-06') {
      // money cells exactly as returned
      const firstAmt = r.body.items[0].amount;
      const cell = await page.locator('[data-testid="p4-row"]').first().locator('td[data-col="amount"]').innerText();
      check('totals RPT-06: money cells are the API string re-grouped', cell.trim() === formatMoney(firstAmt), `${cell} vs ${firstAmt}`);
      const iso = r.body.items[0].created_at;
      const riy = new Date(new Date(iso).getTime() + 3 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ');
      const tcell = await page.locator('[data-testid="p4-row"]').first().locator('td[data-col="created_at"]').innerText();
      check('totals RPT-06: instants are shown in Asia/Riyadh', tcell.trim() === riy, `${tcell} vs ${riy} (${iso})`);
      await shot(page, '10_viewer_rpt06_en');
    }
  }
  // "Unknown", never 0, for a nullable total that is null (no live report has one yet: stubbed)
  await page.route(`${API}/v2/reports/RPT-25`, async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    j.data.totals = j.data.totals.map((t) => (t.key === 'duration_known_seconds' ? { ...t, nullable: true } : t));
    await route.fulfill({ response: res, json: j });
  });
  await page.route(`${API}/v2/reports/RPT-25/rows**`, async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    if (j.data) j.data.totals.duration_known_seconds = null;
    await route.fulfill({ response: res, json: j });
  });
  await page.goto(`${FE}/reports`, { waitUntil: 'networkidle' });
  await rowsAfter(page, 'RPT-25', () => page.goto(`${FE}/reports/RPT-25#call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  const unk = page.locator('[data-testid="p4-total"][data-key="duration_known_seconds"]');
  check('totals: a nullable total returned as null shows "Unknown", never 0 (stubbed response)', (await unk.getAttribute('data-value')) === 'null' && /Unknown/.test(await unk.innerText()), await unk.innerText());
  await page.unroute(`${API}/v2/reports/RPT-25`);
  await page.unroute(`${API}/v2/reports/RPT-25/rows**`);
  await ctx.close();
}

/* ------------------------------------------------ export CSV + Arabic XLSX end to end */
const created = { exports: [] };
async function waitExportDone(page, notId = null) {
  await page.waitForFunction(
    (prev) => {
      const el = document.querySelector('[data-testid="p4-export-current"]');
      return el && el.getAttribute('data-export') !== prev && ['succeeded', 'failed', 'expired'].includes(el.getAttribute('data-status'));
    },
    notId,
    { timeout: 180000 }
  );
  const el = byTest(page, 'p4-export-current');
  return { id: await el.getAttribute('data-export'), status: await el.getAttribute('data-status') };
}
async function exportFlow({ newPage }) {
  const { page, ctx } = await newPage({ acceptDownloads: true });
  await login(page, ADMIN);
  await setLang(page, 'en');
  const dl = fs.mkdtempSync(path.join(os.tmpdir(), 'p4-exports-'));

  // CSV: RPT-06, reversed payments (text with commas, Arabic names)
  const csvHash = '#status=reversed&sort=-amount';
  let r = await rowsAfter(page, 'RPT-06', () => page.goto(`${FE}/reports/RPT-06${csvHash}`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  const previewCsv = r.body.total;
  check('export: the note "exports use a fresh snapshot…" is shown', /fresh snapshot; they may differ from the preview if data changed/.test(await byTest(page, 'p4-export-note').innerText()));
  check('export: RPT-06 offers CSV, XLSX and Arabic XLSX', (await byTest(page, 'p4-export-csv').count()) === 1 && (await byTest(page, 'p4-export-xlsx').count()) === 1 && (await byTest(page, 'p4-export-xlsx_ar').count()) === 1);
  const postWait = page.waitForRequest((q) => q.url() === `${API}/v2/reports/RPT-06/exports` && q.method() === 'POST');
  await byTest(page, 'p4-export-csv').click();
  const post = await postWait;
  const body = JSON.parse(post.postData());
  check('export: POST body = the preview params and sort', body.format === 'csv' && body.sort === '-amount' && JSON.stringify(body.params) === '{"status":"reversed"}' && !!post.headers()['idempotency-key'], post.postData());
  const polled = page.waitForResponse((x) => /\/v2\/report-exports\/[0-9a-f-]{36}$/.test(x.url()), { timeout: 30000 });
  await byTest(page, 'p4-export-current').waitFor({ timeout: 15000 });
  const firstStatus = await byTest(page, 'p4-export-current').getAttribute('data-status');
  check('export: the job shows queued/running first', ['queued', 'running', 'retrying', 'succeeded'].includes(firstStatus), firstStatus);
  await polled;
  let done = await waitExportDone(page);
  created.exports.push(done.id);
  check('export CSV: the job succeeded (progress polled)', done.status === 'succeeded', JSON.stringify(done));
  const rowsAttr = await byTest(page, 'p4-export-rows').getAttribute('data-rows');
  check('export CSV: the job row count equals the preview total', Number(rowsAttr) === previewCsv, `${rowsAttr} vs ${previewCsv}`);
  let dlWait = page.waitForEvent('download', { timeout: 60000 });
  await byTest(page, 'p4-export-current').locator('[data-testid="p4-export-download"]').click();
  let d = await dlWait;
  const csvPath = path.join(dl, d.suggestedFilename());
  await d.saveAs(csvPath);
  const buf = fs.readFileSync(csvPath);
  const text = buf.toString('utf8');
  const bom = text.charCodeAt(0) === 0xfeff;
  const rows = parseCsv(text.replace(/^﻿/, '')).filter((x) => !(x.length === 1 && x[0] === ''));
  const def06 = data(await api(ADMIN, 'GET', '/v2/reports/RPT-06'));
  check('export CSV: downloaded under the server file name', /^RPT-06-\d{8}-\d{4}-[0-9a-f]{8}\.csv$/.test(d.suggestedFilename()), d.suggestedFilename());
  check('export CSV: UTF-8 with BOM, header = the column keys', bom && JSON.stringify(rows[0]) === JSON.stringify(def06.columns.filter((c) => c.export !== false).map((c) => c.key)), rows[0].slice(0, 5).join(','));
  check('export CSV: data rows = the preview total', rows.length - 1 === previewCsv, `${rows.length - 1} vs ${previewCsv}`);
  const amtIdx = rows[0].indexOf('amount');
  const statusIdx = rows[0].indexOf('status');
  check('export CSV: every row is reversed and amounts are exact decimals', rows.slice(1).every((x) => x[statusIdx] === 'reversed' && /^\d+\.\d{2}$/.test(x[amtIdx])), rows[1] && rows[1][amtIdx]);
  const sum = rows.slice(1).reduce((s, x) => s + BigInt(x[amtIdx].replace('.', '')), 0n);
  const sumStr = `${sum / 100n}.${String(sum % 100n).padStart(2, '0')}`;
  check('export CSV: the file sums to the preview total exactly', sumStr === r.body.totals.reversed_amount, `${sumStr} vs ${r.body.totals.reversed_amount}`);

  // Arabic XLSX: RPT-06, September
  r = await rowsAfter(page, 'RPT-06', () => page.goto(`${FE}/reports/RPT-06#payment_date_from=2026-09-01&payment_date_to=2026-09-30`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  const previewX = r.body.total;
  check('export: a new filter clears the previous export card', (await byTest(page, 'p4-export-current').count()) === 0);
  await byTest(page, 'p4-export-xlsx_ar').click();
  await byTest(page, 'p4-export-current').waitFor({ timeout: 15000 });
  done = await waitExportDone(page, done.id);
  created.exports.push(done.id);
  check('export Arabic XLSX: the job succeeded', done.status === 'succeeded', JSON.stringify(done));
  check('export Arabic XLSX: the job row count equals the preview total', Number(await byTest(page, 'p4-export-rows').getAttribute('data-rows')) === previewX, previewX);
  await shot(page, '30_export_done_en');
  dlWait = page.waitForEvent('download', { timeout: 60000 });
  await byTest(page, 'p4-export-current').locator('[data-testid="p4-export-download"]').click();
  d = await dlWait;
  const xPath = path.join(dl, d.suggestedFilename());
  await d.saveAs(xPath);
  const sheets = inspectXlsx(xPath);
  const dataSheet = sheets.find((s) => s.name !== 'الملخص') || sheets[0];
  check('export Arabic XLSX: file name ends -ar.xlsx', /-ar\.xlsx$/.test(d.suggestedFilename()), d.suggestedFilename());
  check('export Arabic XLSX: every sheet is right-to-left', sheets.length >= 1 && sheets.every((s) => s.rtl), JSON.stringify(sheets));
  check('export Arabic XLSX: Arabic sheet names (تحصيلات المستخدمين, الملخص)', sheets.some((s) => s.name === 'تحصيلات المستخدمين') && sheets.some((s) => s.name === 'الملخص'), sheets.map((s) => s.name).join(','));
  check('export Arabic XLSX: data rows (minus the header) = the preview total', dataSheet.rows - 1 === previewX, `${dataSheet.rows - 1} vs ${previewX}`);
  check('export Arabic XLSX: no formulas in the sheet', sheets.every((s) => s.formulas === 0));
  // RPT-24 (no legacy Arabic export): the Arabic XLSX is not offered
  await rowsAfter(page, 'RPT-24', () => page.goto(`${FE}/reports/RPT-24`, { waitUntil: 'domcontentloaded' }));
  check('export: RPT-24 offers no Arabic XLSX (definition formats csv, xlsx)', (await byTest(page, 'p4-export-xlsx_ar').count()) === 0 && (await byTest(page, 'p4-export-no-ar').count()) === 1);
  await ctx.close();
}

/* ------------------------------------------------ My exports */
async function exportsList({ newPage }) {
  const { page, ctx } = await newPage({ acceptDownloads: true });
  await login(page, ADMIN);
  await setLang(page, 'en');
  const listResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-exports?`), { timeout: 30000 });
  await page.goto(`${FE}/reports#tab=exports`, { waitUntil: 'domcontentloaded' });
  const lr = await listResp;
  const apiList = (await lr.json()).data;
  await byTest(page, 'p4-export-row').first().waitFor({ timeout: 30000 });
  const uiRows = await page.$$eval('[data-testid="p4-export-row"]', (els) => els.map((e) => ({ id: e.getAttribute('data-export'), status: e.getAttribute('data-status') })));
  check('exports: "My exports" lists the API page in order (newest first)', JSON.stringify(uiRows.map((x) => x.id)) === JSON.stringify(apiList.items.map((x) => x.id)), `${uiRows.length} rows`);
  check('exports: the total equals the API', Number(await byTest(page, 'p4-exports-total').getAttribute('data-total')) === apiList.total, apiList.total);
  for (const id of created.exports) {
    const row = page.locator(`[data-testid="p4-export-row"][data-export="${id}"]`);
    const ok = (await row.count()) === 1 && (await row.getAttribute('data-status')) === 'succeeded';
    const exp = (await row.locator('[data-testid="p4-export-expiry"]').innerText().catch(() => '')).trim();
    const apiExp = data(await api(ADMIN, 'GET', `/v2/report-exports/${id}`));
    const riy = new Date(new Date(apiExp.artifact.expires_at).getTime() + 3 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ');
    check(`exports: this run's export ${id.slice(0, 8)} is listed as succeeded with its expiry (Riyadh)`, ok && exp === riy, `${exp} vs ${riy}`);
  }
  // status filter in the hash
  const f = await (async () => {
    const w = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/report-exports?`) && qsOf(x.url()).status === 'succeeded', { timeout: 30000 });
    await byTest(page, 'p4-exports-status').selectOption('succeeded');
    return w;
  })();
  check('exports: the status filter -> status=succeeded, kept in the hash', !!f && /xstatus=succeeded/.test(page.url()), page.url());
  await shot(page, '31_my_exports_en');
  await setLang(page, 'ar');
  await shot(page, '31_my_exports_ar');
  // another user never sees them (creator-private)
  const other = data(await api(REVIEWER, 'GET', '/v2/report-exports?page_size=100'));
  check('exports: another user does not see these exports', !other.items.some((x) => created.exports.includes(x.id)), `${other.total} own`);
  await ctx.close();
}

/* ------------------------------------------------ protected field "Hidden" */
async function roleSwap(fn) {
  const role = await api(ADMIN, 'GET', `/roles/${REVIEWER_ROLE}`);
  const before = [...role.body.permission_arr].sort();
  const next = before.filter((k) => k !== 'clints_callsRecords_download' && k !== 'clints_callsRecords_play');
  if (!next.includes('dashbord_admin')) next.push('dashbord_admin');
  try {
    const put = await api(ADMIN, 'PUT', `/roles/${REVIEWER_ROLE}`, { id: REVIEWER_ROLE, name: role.body.name, permission: next });
    check('role: the reviewer test role gets dashbord_admin and loses the recording keys (temporary)', put.status === 200, put.status);
    // the API caches permissions for up to 60 s: wait until the reviewer's calls reflect it
    const t0 = Date.now();
    let ready = false;
    while (Date.now() - t0 < 90000) {
      await apiLogin(REVIEWER, true);
      const a = await api(REVIEWER, 'GET', '/v2/reports/RPT-22');
      const b = data(await api(REVIEWER, 'GET', `/v2/reports/RPT-25/rows?call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}&page_size=50`));
      if (a.status === 200 && b && b.items.every((x) => x.recording_url === null)) {
        ready = true;
        break;
      }
      await sleep(5000);
    }
    check('role: the API applies the change (permission cache)', ready, `${Math.round((Date.now() - t0) / 1000)} s`);
    await fn();
  } finally {
    const back = await api(ADMIN, 'PUT', `/roles/${REVIEWER_ROLE}`, { id: REVIEWER_ROLE, name: role.body.name, permission: before });
    const after = (await api(ADMIN, 'GET', `/roles/${REVIEWER_ROLE}`)).body.permission_arr.sort();
    check('role: the reviewer role is restored exactly', back.status === 200 && JSON.stringify(after) === JSON.stringify(before), `${after.length} keys`);
  }
}

async function hidden({ newPage }) {
  /* 1. aidi.collector (a real collector role, never changed). A collector only ever gets its
   * own RPT-19 row, which is never redacted, so another collector's row is stubbed exactly as
   * the API redacts it (protected target settings null). */
  const def19 = data(await api(COLLECTOR, 'GET', '/v2/reports/RPT-19'));
  const protectedCols = def19.columns.filter((c) => c.protected).map((c) => c.key);
  check('hidden: RPT-19 marks the personal target settings as protected (API, collector)', protectedCols.join(',') === 'user_target_kind,user_target_value,user_target_revision,user_target_from_month,user_target_record', protectedCols.join(','));
  const { page, ctx } = await newPage();
  await login(page, COLLECTOR);
  await setLang(page, 'en');
  await page.route(`${API}/v2/reports/RPT-19/rows**`, async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    const row = {};
    def19.columns.forEach((c) => {
      row[c.key] = null;
    });
    Object.assign(row, {
      rank: 1, contract_rank: 1, collector_name: 'ZZ-TEST another collector', user_name: 'zz.other', contract_name: 'ZZ contract',
      target_known: true, target_source: 'user', target_amount: '1000.00', contract_target_kind: 'amount', contract_target_value: '5000.00',
      contract_target_revision: 1, contract_target_from_month: '2026-09', contract_target_record: 'imported_baseline',
      actual: '250.50', payments_count: 2, actual_recorded: '250.50', recorded_count: 2, achievement_percent: '25.05', remaining: '749.50',
    });
    j.data.items = [row];
    j.data.total = 1;
    j.data.totals.count = 1;
    await route.fulfill({ response: res, json: j });
  });
  await rowsAfter(page, 'RPT-19', () => page.goto(`${FE}/reports/RPT-19`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  const row = page.locator('[data-testid="p4-row"]').first();
  const hiddenCols = await row.locator('[data-testid="p4-hidden"]').evaluateAll((els) => els.map((e) => e.closest('td').getAttribute('data-col')));
  check('hidden: collector — the protected columns show "Hidden"', JSON.stringify(hiddenCols) === JSON.stringify(protectedCols), hiddenCols.join(','));
  const pb = await row.locator('td[data-col="percent_base"]').innerText();
  check('hidden: collector — a non-protected null shows "—", not Hidden', pb.trim() === '—', pb);
  check('hidden: collector — the protected column headers carry the lock', (await page.locator('th[data-col="user_target_kind"] svg').count()) === 1);
  await shot(page, '40_hidden_collector_en');
  await setLang(page, 'ar');
  check('hidden: collector — "مخفي" in Arabic', /مخفي/.test(await row.locator('[data-testid="p4-hidden"]').first().innerText()));
  await shot(page, '40_hidden_collector_ar');
  await ctx.close();
}

/* 2. Live: aidi.reviewer (a supervisor role without user keys) with dashbord_admin sees other
 * collectors in RPT-22, with the personal target settings null -> "Hidden". Same role swap
 * also drops the recording keys for the RPT-25 check. */
async function liveRole({ newPage }) {
  await roleSwap(async () => {
    const r22 = data(await api(REVIEWER, 'GET', '/v2/reports/RPT-22/rows?page_size=50'));
    const def22 = data(await api(REVIEWER, 'GET', '/v2/reports/RPT-22'));
    const prot = def22.columns.filter((c) => c.protected).map((c) => c.key);
    const apiNull = r22.items.length > 0 && r22.items.every((it) => prot.every((k) => it[k] === null));
    check('hidden (live): RPT-22 for aidi.reviewer — the API nulls every protected target setting', apiNull, `${r22.items.length} rows`);
    const { page, ctx } = await newPage();
    await login(page, REVIEWER);
    await setLang(page, 'en');
    await rowsAfter(page, 'RPT-22', () => page.goto(`${FE}/reports/RPT-22`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    const n = await page.locator('[data-testid="p4-row"]').count();
    const hiddenN = await page.locator('[data-testid="p4-hidden"]').count();
    check('hidden (live): every protected cell of every row shows "Hidden"', n > 0 && hiddenN === n * prot.length, `${hiddenN} = ${n} × ${prot.length}`);
    const known = await page.locator('[data-testid="p4-row"]').first().locator('td[data-col="target_amount"]').innerText();
    check('hidden (live): the effective target stays visible', known.trim() !== '' && !/Hidden/.test(known), known);
    await shot(page, '41_hidden_reviewer_rpt22_en');

    // RPT-25: recording links hidden without the recording keys
    const r25 = await rowsAfter(page, 'RPT-25', () => page.goto(`${FE}/reports/RPT-25#call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    const withRec = r25.body.items.filter((x) => x.has_recording).length;
    check('recording: without the keys the API returns no recording link', withRec > 0 && r25.body.items.every((x) => x.recording_url === null && x.recording_file === null), `${withRec} with recording`);
    check('recording: no download/play control, "No access" on the recorded calls', (await byTest(page, 'p4-rec-link').count()) === 0 && (await byTest(page, 'p4-rec-nokey').count()) === withRec, `${await byTest(page, 'p4-rec-nokey').count()} / ${withRec}`);
    await page.locator('td[data-col="recording_url"]').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(outDir, '51_rpt25_no_recording_key_en.png'), fullPage: false });
    await ctx.close();
  });
}

async function recording({ newPage }) {
  // with the keys (aidi.dev): the links show, pointing at the existing /cdr/recording route
  const r = data(await api(ADMIN, 'GET', `/v2/reports/RPT-25/rows?call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}&page_size=50`));
  const rec = r.items.filter((x) => x.recording_file);
  check('recording: with the keys the API links /cdr/recording', rec.length > 0 && rec.every((x) => x.recording_url.startsWith('/cdr/recording?recordingPath=')), rec.length);
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  await rowsAfter(page, 'RPT-25', () => page.goto(`${FE}/reports/RPT-25#call_date_from=${CDR_DAY}&call_date_to=${CDR_DAY}`, { waitUntil: 'domcontentloaded' }));
  await tableReady(page);
  const links = await page.$$eval('[data-testid="p4-rec-link"]', (els) => els.map((e) => e.getAttribute('data-file')));
  check('recording: aidi.dev (download + play keys) gets a control per recorded call', links.length === rec.length && links.every((f) => rec.some((x) => x.recording_file === f)), `${links.length} / ${rec.length}`);
  check('recording: download and play buttons', (await byTest(page, 'p4-rec-link').first().locator('button').count()) === 2);
  await page.locator('[data-testid="p4-rec-link"]').first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outDir, '50_rpt25_recordings_en.png'), fullPage: false });
  await ctx.close();
}

/* ------------------------------------------------ screenshots (ar and en) and charts */
async function screens({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  for (const lang of ['ar', 'en']) {
    await setLang(page, lang);
    await page.goto(`${FE}/reports`, { waitUntil: 'networkidle' });
    await byTest(page, 'p4-report-card').first().waitFor();
    check(`screens ${lang}: dir`, (await page.evaluate(() => document.documentElement.dir)) === (lang === 'ar' ? 'rtl' : 'ltr'));
    await shot(page, `01_hub_${lang}`);
    await rowsAfter(page, 'RPT-06', () => page.goto(`${FE}/reports/RPT-06`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    await shot(page, `02_viewer_rpt06_${lang}`);
    // charts
    for (const [id, hash, name] of [
      ['RPT-20', '#payment_date_from=2026-07-01&payment_date_to=2026-07-31', '03_chart_rpt20'],
      ['RPT-21', '#due_date_from=2026-09-01&due_date_to=2026-09-30', '03b_chart_rpt21'],
      ['RPT-24', '#section=trend&activity_date_from=2026-08-01', '04_chart_rpt24_trend'],
      ['RPT-23', `#day=${CDR_DAY}&mode=calls`, '05_chart_rpt23_calls'],
    ]) {
      const chartResp = page.waitForResponse((x) => x.url().startsWith(`${API}/v2/reports/${id}/rows`) && qsOf(x.url()).page_size === '200', { timeout: 60000 });
      await page.goto(`${FE}/reports/${id}${hash}`, { waitUntil: 'domcontentloaded' });
      const cr = (await (await chartResp).json()).data;
      await page.waitForFunction(() => document.querySelector('[data-testid="p4-chart-svg"], [data-testid="p4-chart-mismatch"], [data-testid="p4-chart-too-many"]'), null, { timeout: 60000 });
      await settle(page, 600);
      const svg = byTest(page, 'p4-chart-svg');
      check(`charts ${lang} ${id}: drawn, and its values add up to the report's exact total`, (await svg.count()) === 1 && (await byTest(page, 'p4-chart-mismatch').count()) === 0, `${await byTest(page, 'p4-chart-sub').innerText().catch(() => '')}`);
      if (lang === 'en') {
        const sub = await byTest(page, 'p4-chart-sub').innerText();
        const key = id === 'RPT-21' ? 'promised_amount' : id === 'RPT-23' ? 'total' : 'approved_amount';
        const want = id === 'RPT-23' ? String(cr.totals.total) : formatMoney(cr.totals[key]);
        check(`charts ${id}: the sub-line total is the exact API total (${want})`, sub.includes(`Total ${want}`), sub);
        check(`charts ${id}: the time axis stays left-to-right`, (await page.locator('[data-testid="p4-chart"] div[dir="ltr"]').count()) >= 1);
        await byTest(page, 'p4-chart-values-toggle').click();
        const cells = await page.locator('[data-testid="p4-chart-values"] td[data-x]').count();
        check(`charts ${id}: the exact value list is one click away`, cells === Number(await svg.getAttribute('data-points')), cells);
      }
      await shot(page, `${name}_${lang}`);
    }
    await rowsAfter(page, 'RPT-24', () => page.goto(`${FE}/reports/RPT-24`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    await shot(page, `06_rpt24_summary_${lang}`);
    await rowsAfter(page, 'RPT-01', () => page.goto(`${FE}/reports/RPT-01`, { waitUntil: 'domcontentloaded' }));
    await tableReady(page);
    await shot(page, `08_rpt01_${lang}`);
  }
  await ctx.close();
  const c = await newPage();
  await login(c.page, COLLECTOR);
  for (const lang of ['ar', 'en']) {
    await setLang(c.page, lang);
    await c.page.goto(`${FE}/reports`, { waitUntil: 'networkidle' });
    await byTest(c.page, 'p4-report-card').first().waitFor();
    await shot(c.page, `09_hub_collector_${lang}`);
    // RPT-18 (dashbord_user: the collector's own dated dashboard), grouped by section
    await rowsAfter(c.page, 'RPT-18', () => c.page.goto(`${FE}/reports/RPT-18#activity_date_from=2026-09-01&activity_date_to=2026-09-24`, { waitUntil: 'domcontentloaded' }));
    await tableReady(c.page);
    check(`screens ${lang}: RPT-18 renders its sections as groups`, (await byTest(c.page, 'p4-section-row').count()) >= 1, await byTest(c.page, 'p4-section-row').count());
    await shot(c.page, `07_rpt18_sections_collector_${lang}`);
  }
  await c.ctx.close();
}

/* ================================================================== runner */
(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const pageErrors = [];
  const newPage = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...opts });
    const page = await ctx.newPage();
    const errors = [];
    if (process.env.DEBUG) page.on('request', (q) => q.url().includes('/v2/reports') && log('  req', q.url()));
    page.on('pageerror', (e) => {
      const msg = `${String(e).slice(0, 160)} @ ${page.url()}`;
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
  const sections = [
    ['hub', hub],
    ['open', open],
    ['filters', filters],
    ['totals', totals],
    ['export', exportFlow],
    ['exports', exportsList],
    ['hidden', hidden],
    ['recording', recording],
    ['hidden', liveRole],
    ['screens', screens],
  ];
  for (const [name, fn] of sections) {
    if (!want(name) && !(name === 'hidden' && fn === liveRole && want('recording'))) continue;
    log(`\n=== ${name}${fn === liveRole ? ' (live role: Hidden + recording keys)' : ''}`);
    try {
      await fn({ browser, newPage });
    } catch (e) {
      check(`section ${name} finished without an exception`, false, e.stack || String(e));
    }
  }
  check('no page errors or 5xx from /v2 in any browser context', pageErrors.length === 0, pageErrors.slice(0, 5).join(' | '));
  await browser.close();
  fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok).length;
  log(`\n${results.length - failed}/${results.length} checks pass`);
  fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
  process.exit(failed ? 1 : 0);
})();
