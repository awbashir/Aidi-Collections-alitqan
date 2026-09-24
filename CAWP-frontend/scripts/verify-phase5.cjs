/* Phase 5 frontend checks (calling screens), driven by headless Chrome through the
 * playwright-core install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase5.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots (ar/en) and verify.json/verify.log go to outDir (default logs/screens/phase5-ui).
 *
 * LIVE CALLS: they run against the LOCAL test PBX only (compose profile pbx-test) and the
 * `calling` service pointed at the app database (review/phase5-click-to-call.md). The script
 * starts both when they are not running and stops them at the end (KEEP=1 keeps them). It
 * only ever dials the ZZ-TEST loopback test numbers it creates on the ZZ-TEST cases
 * (9665000099xx; the test PBX decides the behaviour by the last digit and never leaves the
 * container). The real PBX is never contacted: the calling service refuses any other PBX.
 *
 * ONLY=<section,...>: screens, gating, perms, admin, refusal, c2c, busy, locked, manual, calls,
 * cdr, campaign, auto, uncertain, release.
 *
 * Test data it writes (all ZZ-TEST): contacts 9665000099xx on ZZTEST-P1-0003/0004, calls
 * and wrap-up notes on those cases, a review flag it resolves, a campaign it drains, a PBX
 * CDR import job, the 24/7 calling-hours policy, and the extension 7101 assignment it
 * releases at the end.
 */
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const ROOT = path.resolve(__dirname, '../..');
const { chromium } = require(path.join(ROOT, 'tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const outDir = path.resolve(process.argv[2] || path.join(ROOT, 'logs/screens/phase5-ui'));
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PASS = 'Aidi@2026';
const ADMIN = process.env.P5_USER || 'aidi.dev';
const COLLECTOR = 'aidi.collector';
const ADMIN_ID = process.env.P5_USER_ID || 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf';
const ADMIN_LABEL = new RegExp(process.env.P5_USER_LABEL || 'Aidi Dev');
/* E2E_DB=1: the campaign section against the throwaway e2e database (see the report) */
const E2E_DB = process.env.E2E_DB === '1';
const CASE3 = 'e1afce87-41d5-461a-bd9b-79beab1f15d3';
const ID3 = '1999000003';
const CASE4 = process.env.P5_CAMPAIGN_CASE || 'a206f02b-2ccc-4e06-8ed6-9f06ef796732';
const ID4 = process.env.P5_CAMPAIGN_IDENTITY || '1999000004';
const ACC4 = process.env.P5_CAMPAIGN_ACCOUNT || 'ZZTEST-P1-0004';
const ZZ_PORTFOLIO = process.env.P5_POOL_PORTFOLIO || 'ZZ-TEST Phase1 frontend 2026-09-23';
const MOBILE_TYPE = process.env.P5_MOBILE_TYPE || 'cddd230b-ce12-4019-b6d7-f151aad6bcdb';
const EXT = '7101'; // the pjsua auto-answer agent registered on pbx-test
/* Loopback test numbers: the LAST digit decides the simulated customer (pbx-test). */
const NUM = {
  connected: '966500009916', // rings 3 s, answers, 20 s tone, hangs up
  busy: '966500009912', // busy
  short: '966500009914', // answers, hangs up after 3 s
  echo: '966500009911', // answers, echo up to 120 s
  blocked: '966500009918', // blocked in the setup (refusal check); never dialled
  campaign: '966500009924', // case 4 (campaign pool): answers, hangs up after 3 s
};
const RUN = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12);

fs.mkdirSync(outDir, { recursive: true });
const results = [];
const logLines = [];
const log = (...a) => {
  const line = a.join(' ');
  logLines.push(line);
  console.log(line);
};
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 600) });
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== '' ? '  -- ' + String(detail).slice(0, 300) : ''}`);
  return !!ok;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { encoding: 'utf8', cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'], ...opts });

/* ------------------------------------------------------------ API helpers */
const tokens = {};
async function apiLogin(user) {
  if (tokens[user]) return tokens[user];
  const r = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
  if (method !== 'GET') headers['Idempotency-Key'] = crypto.randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let j = null;
  try {
    j = await r.json();
  } catch {
    /* empty body */
  }
  return { status: r.status, body: j, data: j && j.data };
}

/* ------------------------------------------------------------ UI helpers */
async function login(page, user = ADMIN, { permissions } = {}) {
  if (permissions) {
    await page.route('**/login', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (body?.user?.role) body.user.role.permission = permissions;
      await route.fulfill({ response: res, json: body });
    });
    await page.route('**/v2/me', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (body?.data) body.data.permissions = permissions;
      await route.fulfill({ response: res, json: body });
    });
  }
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
  await page.waitForTimeout(700);
}
const settle = async (page, ms = 1200) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
};
const shot = (page, name, full = true) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: full });
/** react-select: open the control inside `scope` and click the option matching `text` (string = exact). */
async function pick(page, scope, text) {
  await scope.locator('.select__control').first().click();
  /* type to filter (long menus in a dialog can run past the viewport) */
  if (typeof text === 'string') await page.keyboard.type(text, { delay: 15 });
  const opts = page.locator('.select__menu .select-option');
  await opts.first().waitFor({ timeout: 10000 });
  if (!text) return opts.first().click();
  const re = typeof text === 'string' ? new RegExp('^\\s*' + text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*$') : text;
  const n = await opts.count();
  for (let i = 0; i < n; i += 1) {
    const t = (await opts.nth(i).innerText()).trim();
    if (re.test(t)) {
      await opts.nth(i).scrollIntoViewIfNeeded().catch(() => {});
      return opts.nth(i).click();
    }
  }
  throw new Error(`option ${text} not found`);
}
/** Top-level items per sidebar group (nested sub-items excluded), as verify-phase0 counts them. */
async function sidebarCounts(page) {
  return page.$$eval('.side-nav .menu-nav-group', (gs) =>
    gs.map((g) => [...g.querySelectorAll('.menu-item, .menu-collapse-item')].filter((el) => !el.parentElement.closest('.menu-collapse > ul, .menu-collapse > div:not(.menu-collapse-item)')).length)
  );
}

/* ------------------------------------------------------------ infrastructure */
function containerUp(name) {
  try {
    return sh('docker', ['inspect', '-f', '{{.State.Running}}', name]).trim() === 'true';
  } catch {
    return false;
  }
}
let startedHere = false;
async function ensureCalling() {
  if (!containerUp('cawp-pbx-test')) {
    log('starting pbx-test');
    sh('docker', ['compose', '--profile', 'pbx-test', 'up', '-d', 'pbx-test'], { timeout: 300000 });
    startedHere = true;
    await sleep(15000);
  }
  if (!containerUp('cawp-calling')) {
    log('starting the calling service (CALLING_ENABLED=1, the app database)');
    sh('docker', ['compose', '--profile', 'pbx-test', 'up', '-d', 'calling'], { timeout: 300000, env: { ...process.env, CALLING_ENABLED: '1' } });
    startedHere = true;
  }
  for (let i = 0; i < 40; i += 1) {
    const st = await api(ADMIN, 'GET', '/v2/calling/status');
    if (st.data?.pbx?.connected) {
      const me = await api(ADMIN, 'GET', '/v2/me/calling');
      if (me.data && me.data.reason !== 'transport_not_connected') return st.data.pbx;
    }
    await sleep(1500);
  }
  return null;
}
function stopCalling() {
  try {
    sh('docker', ['stop', 'cawp-calling'], { timeout: 120000 });
  } catch (e) {
    log('stop calling failed: ' + String(e).slice(0, 200));
  }
}
function startCalling() {
  sh('docker', ['compose', '--profile', 'pbx-test', 'up', '-d', 'calling'], { timeout: 300000, env: { ...process.env, CALLING_ENABLED: '1' } });
}

/* ------------------------------------------------------------ test data */
async function contactsOf(identity) {
  return (await api(ADMIN, 'GET', `/v2/debtors/${identity}/contacts`)).data.contacts;
}
async function ensureContact(identity, number, reason) {
  let c = (await contactsOf(identity)).find((x) => x.contact === number);
  if (!c) {
    const r = await api(ADMIN, 'POST', `/v2/debtors/${identity}/contacts`, { contact_type_id: MOBILE_TYPE, contact: number, reason });
    c = r.data?.contact || (await contactsOf(identity)).find((x) => x.contact === number);
  }
  return c;
}
async function setupData() {
  const reason = 'ZZ-TEST phase 5 frontend: loopback test number (local test PBX only)';
  const out = {};
  if (E2E_DB) {
    out.campaign = await ensureContact(ID4, NUM.campaign, reason);
    const c = (await contactsOf(ID4)).find((x) => x.contact === NUM.campaign);
    if (!c.is_default) await api(ADMIN, 'POST', `/v2/contacts/${c.id}/default`, { expected_revision: c.revision, reason: 'the campaign dials only this test number' });
    return out;
  }
  for (const k of ['connected', 'busy', 'short', 'echo', 'blocked']) out[k] = await ensureContact(ID3, NUM[k], reason);
  if (!out.blocked.is_blocked) {
    await api(ADMIN, 'POST', `/v2/contacts/${out.blocked.id}/block`, { reason: 'ZZ-TEST phase 5: blocked for the refusal check', expected_revision: out.blocked.revision });
  }
  out.campaign = await ensureContact(ID4, NUM.campaign, reason);
  if (!out.campaign.is_default) {
    const c = (await contactsOf(ID4)).find((x) => x.contact === NUM.campaign);
    await api(ADMIN, 'POST', `/v2/contacts/${c.id}/default`, { expected_revision: c.revision, reason: 'ZZ-TEST phase 5: the campaign dials only this test number' });
  }
  /* case 3 unlocked (a previous interrupted run may have left it locked) */
  const kase = (await api(ADMIN, 'GET', `/clints/${CASE3}`)).body;
  if (kase?.is_locked) await api(ADMIN, 'PUT', `/clint/unlock/${CASE3}`, { reason: 'ZZ-TEST phase 5: unlock after an interrupted run' });
  return out;
}
/** Leave no reservation behind: wrap up / resolve whatever still holds aidi.dev. */
async function clearActive(label) {
  for (let i = 0; i < 30; i += 1) {
    const a = (await api(ADMIN, 'GET', '/v2/me/calls/active')).data?.attempt;
    if (!a) return true;
    if (a.state === 'uncertain') {
      await api(ADMIN, 'POST', `/v2/calls/${a.id}/resolve`, { expected_revision: a.revision, resolution: 'ended', reason: `ZZ-TEST phase 5 cleanup (${label})`, release_reservation: true });
    } else if (a.actions?.can_wrapup) {
      await api(ADMIN, 'POST', `/v2/calls/${a.id}/wrapup`, {
        expected_revision: a.revision,
        outcome: a.actions.wrapup_outcomes.includes('no_answer') ? 'no_answer' : a.actions.wrapup_outcomes[0],
        note: { text: `ZZ-TEST phase 5 cleanup wrap-up (${label})`, comment_status_id: '06be2107-ac9b-46c0-910f-9c92a079cefb', contact_method_id: 'ff081c7e-4500-4a75-ae36-580f2ae90834', comment_reachability_id: '81a4180b-876b-443e-a6fd-62a693887370' },
      });
    } else if (a.actions?.can_cancel) {
      await api(ADMIN, 'POST', `/v2/calls/${a.id}/cancel`, { expected_revision: a.revision, reason: 'ZZ-TEST cleanup' });
    }
    await sleep(2000);
  }
  return false;
}
async function ensureExtension() {
  const me = (await api(ADMIN, 'GET', '/v2/me/calling')).data;
  if (me?.extension?.extension === EXT) return true;
  const hist = (await api(ADMIN, 'GET', `/v2/calling/extensions/${EXT}/history`)).data;
  const owner = hist?.items?.find((i) => i.active)?.user?.id || null;
  const r = await api(ADMIN, 'POST', `/v2/calling/extensions/${EXT}/assign`, { user_id: ADMIN_ID, reason: 'ZZ-TEST phase 5 frontend verification (API fallback)', expected_owner_id: owner, replace: true });
  return r.status === 200;
}
async function ensureWindows() {
  const w = (await api(ADMIN, 'GET', '/v2/calling/windows')).data;
  if (w?.status?.open) return true;
  const r = await api(ADMIN, 'PUT', '/v2/calling/windows', {
    expected_revision: w?.policy?.revision || 0,
    reason: 'ZZ-TEST phase 5 frontend verification: 24/7 on the local test PBX only',
    windows: [{ days: [0, 1, 2, 3, 4, 5, 6], start: '00:00', end: '24:00' }],
    holidays: [],
  });
  return r.status === 200;
}

/* ------------------------------------------------------------ call helpers (UI) */
/** Click "Call" on the contact row with `number` and follow the dock until `until` states. */
async function placeCall(page, number) {
  await page.goto(`${FE}/clients/details/details?id=${CASE3}`, { waitUntil: 'networkidle' });
  const row = page.locator('[data-testid="p1-contact-row"]', { hasText: number });
  await row.waitFor({ timeout: 20000 });
  const btn = row.locator('[data-testid="p5-call-contact"]');
  for (let i = 0; i < 20; i += 1) {
    if ((await btn.getAttribute('aria-disabled')) === 'false') break;
    await page.waitForTimeout(500);
  }
  await btn.click();
  await page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').waitFor({ timeout: 20000 });
  return page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').getAttribute('data-attempt');
}
/** Record the dock's states until one of `until` shows (or the timeout). */
async function followDock(page, until, timeoutMs = 60000) {
  const seen = [];
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const st = await page.locator('[data-testid="p5-dock"]').getAttribute('data-state').catch(() => null);
    if (st && seen[seen.length - 1] !== st) seen.push(st);
    if (st && until.includes(st)) break;
    await page.waitForTimeout(150);
  }
  return seen;
}
async function fillNote(page, dialog, text, reach, status) {
  await dialog.locator('[data-testid$="note-text"]').fill(text);
  await pick(page, dialog.locator('[data-testid$="note-reach"]'), reach);
  await page.waitForTimeout(300);
  await pick(page, dialog.locator('[data-testid$="note-status"]'), status);
  if (METHOD) await pick(page, dialog.locator('[data-testid$="note-method"]'), METHOD);
}
const REACHED = process.env.P5_REACH || 'تم التوصل';
const NOT_REACHED = 'لم يتم التوصل';
const ST_FOLLOW = process.env.P5_STATUS || 'جاري متابعة العميل';
const METHOD = process.env.P5_METHOD || null; // the legacy default "mobile" method is picked by the form
const ST_BUSY = 'مشغول-مفصول';
async function closeDialogs(page) {
  for (let i = 0; i < 3; i += 1) {
    const cancel = page.locator('.dialog-content button', { hasText: /^(إلغاء|Cancel)$/ });
    if (!(await cancel.count())) return;
    await cancel.first().click().catch(() => {});
    await page.waitForTimeout(400);
  }
}

/* ============================================================ sections */
async function run(browser) {
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
  const campaignsLive = (await api(ADMIN, 'GET', '/v2/campaigns?page_size=1')).status === 200;
  log(`campaign API on ${API}: ${campaignsLive ? 'available' : 'MISSING (GET /v2/campaigns is 404: the running API has not registered api.calling_campaigns)'}`);

  /* ---------------------------------------------------------------- screens */
  if (want('screens')) {
    const { page, errors } = await newPage();
    await login(page);
    const screens = [
      ['01_calls', '/calls', 'p5-calls'],
      ['02_calling', '/calling', 'p5-workspace'],
      ['03_campaigns', '/campaigns', 'p5-campaigns'],
      ['04_review', '/call-review', 'p5-review'],
      ['05_review_flags', '/call-review#tab=flags', 'p5-review-flags'],
      ['06_admin_status', '/calling-admin', 'p5-admin'],
      ['07_admin_extensions', '/calling-admin#tab=extensions', 'p5-admin-extensions'],
      ['08_admin_drift', '/calling-admin#tab=drift', 'p5-admin-drift'],
      ['09_admin_windows', '/calling-admin#tab=windows', 'p5-admin-windows'],
      ['10_admin_blocks', '/calling-admin#tab=blocks', 'p5-admin-blocks'],
      ['11_case_calls', `/clients/details/calls?id=${CASE3}`, 'p5-case-calls'],
      ['12_case_contacts', `/clients/details/details?id=${CASE3}`, 'p1-contacts'],
    ];
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      for (const [name, url, testId] of screens) {
        errors.length = 0;
        await page.goto(FE + url, { waitUntil: 'networkidle' });
        await page.waitForTimeout(1800);
        const ok = await page.locator(`[data-testid="${testId}"]`).first().isVisible().catch(() => false);
        const errState = await page.locator(`[data-testid="${testId}"] [data-testid="p1-error"]`).count();
        const dir = await page.evaluate(() => document.documentElement.dir);
        /* the campaigns API may be missing on the running server (reported): its error state is expected then */
        const allowErr = !campaignsLive && ['02_calling', '03_campaigns'].includes(name);
        check(`screens ${lang}: ${url} renders (${testId}), dir=${dir}${allowErr ? ' [campaign API missing: error state tolerated]' : ''}`, ok && (allowErr || errState === 0) && dir === (lang === 'ar' ? 'rtl' : 'ltr') && errors.length === 0, `visible=${ok} errorStates=${errState} ${errors.join(' | ')}`);
        await shot(page, `${name}_${lang}`);
      }
      await page.goto(FE + '/home', { waitUntil: 'networkidle' });
      await settle(page, 800);
      const counts = await sidebarCounts(page);
      check(`screens ${lang}: aidi.dev sidebar items per group [9,13,11] (Phase 4 was [6,13,9]; + calls, calling, campaigns; + call review, calling admin)`, JSON.stringify(counts) === '[9,13,11]', JSON.stringify(counts));
      const labels = await page.$$eval('.side-nav .menu-item', (els) => els.map((e) => e.textContent.trim()));
      const expect = lang === 'ar' ? ['المكالمات', 'مساحة الاتصال', 'الحملات', 'مراجعة المكالمات', 'إدارة الاتصال'] : ['Calls', 'Calling workspace', 'Campaigns', 'Call review', 'Calling admin'];
      check(`screens ${lang}: the five nav items are present`, expect.every((l) => labels.includes(l)), expect.filter((l) => !labels.includes(l)).join(', '));
    }
    await setLang(page, 'ar');
    await page.context().close();
  }

  /* ---------------------------------------------------------------- gating */
  if (want('gating')) {
    const { page } = await newPage();
    await login(page, COLLECTOR);
    await settle(page);
    const labels = await page.$$eval('.side-nav .menu-item', (els) => els.map((e) => e.textContent.trim()));
    const p5 = ['المكالمات', 'مساحة الاتصال', 'الحملات', 'مراجعة المكالمات', 'إدارة الاتصال'];
    check('gating: aidi.collector (no calling keys) sees none of the five nav items', p5.every((l) => !labels.includes(l)), labels.filter((l) => p5.includes(l)).join(', '));
    for (const u of ['/calls', '/calling', '/campaigns', '/call-review', '/calling-admin', `/campaigns/00000000-0000-4000-8000-000000000000`]) {
      await page.goto(FE + u, { waitUntil: 'networkidle' });
      await page.waitForTimeout(600);
      check(`gating: aidi.collector ${u} -> access-denied`, page.url().includes('/access-denied'), page.url());
    }
    check('gating: aidi.collector has no docked live-call panel', (await page.locator('[data-testid="p5-dock"]').count()) === 0);
    const r = await api(COLLECTOR, 'GET', '/v2/calls/mine');
    check('gating: the API agrees (GET /v2/calls/mine as aidi.collector -> 403)', r.status === 403, r.status);
    await page.context().close();

    /* a collector-like role holding calls + calls_make + campaigns (UI stub, the API still decides) */
    const { page: p2 } = await newPage();
    const perms = { dashbord: true, clints: true, clints_details: true, calls: true, calls_make: true, campaigns: true };
    await login(p2, ADMIN, { permissions: perms });
    await settle(p2);
    const l2 = await p2.$$eval('.side-nav .menu-item', (els) => els.map((e) => e.textContent.trim()));
    check('gating: a role with calls + calls_make + campaigns sees Calls, Calling workspace and Campaigns', ['المكالمات', 'مساحة الاتصال', 'الحملات'].every((l) => l2.includes(l)), l2.join(' | '));
    check('gating: ... but not Call review / Calling admin (calls_manage)', !l2.includes('مراجعة المكالمات') && !l2.includes('إدارة الاتصال'), l2.join(' | '));
    await p2.goto(FE + '/calling-admin', { waitUntil: 'networkidle' });
    check('gating: ... /calling-admin -> access-denied', p2.url().includes('/access-denied'), p2.url());
    await p2.goto(FE + '/calls', { waitUntil: 'networkidle' });
    await p2.waitForTimeout(1200);
    /* (the PBX import button follows the API's GET /v2/bulk/kinds `allowed`, which is the real role's) */
    check('gating: ... /calls renders without the call-review link (calls_manage)', (await p2.locator('[data-testid="p5-calls"]').count()) === 1 && (await p2.locator('[data-testid="p5-calls-review-link"]').count()) === 0);
    await shot(p2, '13_collector_like_calls_ar');
    await p2.context().close();
  }

  /* ---------------------------------------------------------------- perms */
  if (want('perms')) {
    const py = fs.readFileSync(path.join(ROOT, 'CAWP-API/core/permissions.py'), 'utf8');
    const reg = [...py.matchAll(/PermissionKey\("([a-z_]+)"/g)].map((m) => m[1]);
    const ts = fs.readFileSync(path.join(ROOT, 'CAWP-frontend/src/constants/permissions.phase1.ts'), 'utf8');
    const mirror = [...ts.matchAll(/\{ key: '([a-z_]+)'/g)].map((m) => m[1]);
    check('perms: the frontend mirror equals core/permissions.py NEW_PERMISSIONS (same keys, same order)', JSON.stringify(reg) === JSON.stringify(mirror), `${reg.length} vs ${mirror.length}: ${reg.filter((k) => !mirror.includes(k)).join(',')}`);
    const { page } = await newPage();
    await login(page);
    await page.goto(FE + '/permissions/new', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const body = await page.locator('body').innerText();
    const labels = ['المكالمات وسجلها', 'إجراء المكالمات', 'إدارة الاتصال', 'حملات الاتصال', 'تشغيل وإيقاف الحملات'];
    /* the tree is collapsed: expand the workflow node and its groups */
    const tree = page.locator('.react-checkbox-tree');
    const expandAll = tree.locator('button.rct-collapse-btn');
    for (let i = 0; i < 6; i += 1) {
      const n = await expandAll.count();
      for (let j = 0; j < n; j += 1) {
        const b = expandAll.nth(j);
        if ((await b.getAttribute('aria-label'))?.toLowerCase().includes('expand')) await b.click().catch(() => {});
      }
      await page.waitForTimeout(200);
    }
    const text = await page.locator('body').innerText();
    check('perms: the permissions screen shows the five calling keys (ar labels)', labels.every((l) => text.includes(l)), labels.filter((l) => !text.includes(l)).join(', ') + (body ? '' : ''));
    check('perms: the workflow section is titled "Phases 1–5"', text.includes('المراحل 1–5'));
    await shot(page, '14_permissions_calling_keys_ar');
    await page.context().close();
  }

  /* ---------------------------------------------------------------- infrastructure + data */
  const needsLive = ['admin', 'refusal', 'c2c', 'busy', 'locked', 'manual', 'calls', 'cdr', 'campaign', 'auto', 'uncertain', 'release'].some(want);
  let data = null;
  if (needsLive) {
    const pbx = await ensureCalling();
    check('live: the calling service is connected to the LOCAL test PBX (pbx-test) and writes into the app database', !!pbx && pbx.host === 'pbx-test', JSON.stringify(pbx));
    data = await setupData();
    check('live: ZZ-TEST loopback test contacts exist (case 3: connected/busy/short/echo/blocked; case 4: campaign default)', !!((E2E_DB || (data.connected && data.busy && data.blocked)) && data.campaign), Object.keys(data).join(','));
    check('live: no reservation left from an earlier run', await clearActive('setup'));
  }

  /* ---------------------------------------------------------------- admin */
  if (want('admin')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    /* calling hours: every day, all day (the local test PBX only) */
    await page.goto(FE + '/calling-admin#tab=windows', { waitUntil: 'networkidle' });
    await page.locator('[data-testid="p5-admin-windows"]').waitFor({ timeout: 15000 });
    const rev0 = Number(await page.locator('[data-testid="p5-admin-windows"]').getAttribute('data-revision'));
    await page.click('[data-testid="p5-admin-window-allday"]');
    check('admin: save is disabled until a reason is typed', (await page.locator('[data-testid="p5-admin-windows-save"]').getAttribute('aria-disabled')) === 'true');
    await page.fill('[data-testid="p5-admin-windows-reason"]', `ZZ-TEST phase 5 frontend verification ${RUN}: 24/7 on the local test PBX only`);
    await page.click('[data-testid="p5-admin-windows-save"]');
    await page.waitForTimeout(1500);
    const rev1 = Number(await page.locator('[data-testid="p5-admin-windows"]').getAttribute('data-revision'));
    const w = (await api(ADMIN, 'GET', '/v2/calling/windows')).data;
    check('admin: calling hours saved through the UI (revision +1, open now, 24/7)', rev1 === rev0 + 1 && w.status.open && w.policy.windows.length === 1 && w.policy.windows[0].end === '24:00', `rev ${rev0}->${rev1} open=${w.status.open}`);
    await shot(page, '20_admin_windows_saved_en');

    /* extension 7101 -> aidi.dev (release a previous assignment first, through the UI) */
    await page.goto(FE + '/calling-admin#tab=extensions', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const existing = page.locator(`[data-testid="p5-admin-ext-row"][data-ext="${EXT}"][data-active="1"]`);
    if (await existing.count()) {
      await existing.locator('[data-testid="p5-admin-ext-release"]').click();
      await page.fill('[data-testid="p5-admin-release-reason"]', 'ZZ-TEST phase 5: re-assign in the verification run');
      await page.click('[data-testid="p5-admin-release-confirm"]');
      await page.waitForTimeout(1500);
    }
    await page.click('[data-testid="p5-admin-assign-open"]');
    const dlg = page.locator('[data-testid="p5-admin-assign"]');
    await dlg.waitFor();
    await page.fill('[data-testid="p5-admin-assign-ext"]', EXT);
    await page.fill('[data-testid="p5-admin-assign-search"]', ADMIN);
    await page.waitForTimeout(900);
    await pick(page, dlg.locator('[data-testid="p5-admin-assign-user"]'), new RegExp(ADMIN.replace(/\./g, '\\.')));
    await page.fill('[data-testid="p5-admin-assign-reason"]', `ZZ-TEST phase 5 frontend verification ${RUN}: pjsua test agent 7101`);
    await page.waitForTimeout(600);
    await shot(page, '21_admin_assign_dialog_en', false);
    await page.click('[data-testid="p5-admin-assign-confirm"]');
    await page.waitForTimeout(1800);
    const row = page.locator(`[data-testid="p5-admin-ext-row"][data-ext="${EXT}"][data-active="1"]`);
    check('admin: extension 7101 assigned to aidi.dev through the UI (active row, owner aidi.dev)', (await row.count()) === 1 && (await row.getAttribute('data-user')) === ADMIN_ID);
    const me = (await api(ADMIN, 'GET', '/v2/me/calling')).data;
    check('admin: GET /v2/me/calling now shows 7101 and can_call true', me.extension?.extension === EXT && me.can_call === true, `${me.extension?.extension} can_call=${me.can_call} reason=${me.reason}`);
    await shot(page, '22_admin_extensions_en');
    /* history dialog */
    await row.locator('[data-testid="p5-admin-ext-history"]').click();
    await page.locator('[data-testid="p5-admin-history"]').waitFor();
    await page.locator('[data-testid="p5-admin-history"]', { hasText: `verification ${RUN}` }).waitFor({ timeout: 10000 }).catch(() => {});
    check('admin: the extension history lists the assignment with its reason', (await page.locator('[data-testid="p5-admin-history"]').innerText()).includes(`verification ${RUN}`));
    await shot(page, '24_admin_extension_history_en', false);
    await closeDialogs(page);
    /* service status */
    await page.goto(FE + '/calling-admin', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const status = page.locator('[data-testid="p5-admin-status"]');
    const epoch = await page.locator('[data-testid="p5-admin-epoch"]').innerText();
    check('admin: service status shows connected with the PBX epoch', (await status.getAttribute('data-connected')) === '1' && /@/.test(epoch), epoch);
    await shot(page, '23_admin_status_connected_en');
    await setLang(page, 'ar');
    await shot(page, '23_admin_status_connected_ar');
    /* legacy drift list (read only: adopting would change real users) */
    await page.goto(FE + '/calling-admin#tab=drift', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const drift = await page.locator('[data-testid="p5-admin-drift-row"]').count();
    const apiDrift = (await api(ADMIN, 'GET', '/v2/calling/extensions/legacy-drift?page_size=25')).data;
    check('admin: the legacy drift list equals the API page (adopt offered only on "adoptable" rows)', drift === apiDrift.items.length && (await page.locator('[data-testid="p5-admin-drift-row"]:not([data-status="adoptable"]) [data-testid="p5-admin-adopt"]').count()) === 0, `${drift} vs ${apiDrift.items.length}`);
    check('admin: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  } else if (needsLive) {
    check(`live: extension 7101 on ${ADMIN} (API fallback)`, await ensureExtension());
    check('live: calling hours open (API fallback)', await ensureWindows());
  }

  /* ---------------------------------------------------------------- refusal */
  if (want('refusal')) {
    const { page } = await newPage();
    await login(page);
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      await page.goto(`${FE}/clients/details/details?id=${CASE3}`, { waitUntil: 'networkidle' });
      const row = page.locator('[data-testid="p1-contact-row"]', { hasText: NUM.blocked });
      await row.waitFor({ timeout: 20000 });
      await page.waitForTimeout(1500);
      const line = row.locator('[data-testid="p5-call-refusal"]');
      const code = await line.getAttribute('data-code').catch(() => null);
      const text = await line.innerText().catch(() => '');
      const btn = row.locator('[data-testid="p5-call-contact"]');
      check(`refusal ${lang}: the blocked contact shows why it cannot be called (contact_blocked) and its Call is disabled`, code === 'contact_blocked' && (await btn.getAttribute('aria-disabled')) === 'true' && (lang === 'ar' ? text.includes('محظورة') : /blocked/i.test(text)), `${code}: ${text}`);
      const okRow = page.locator('[data-testid="p1-contact-row"]', { hasText: NUM.connected });
      check(`refusal ${lang}: a callable test contact has an enabled Call and no refusal line`, (await okRow.locator('[data-testid="p5-call-contact"]').getAttribute('aria-disabled')) === 'false' && (await okRow.locator('[data-testid="p5-call-refusal"]').count()) === 0);
      await row.scrollIntoViewIfNeeded();
      await shot(page, `30_refusal_blocked_${lang}`);
    }
    /* the API's own refusal, mapped: a direct POST for the blocked contact */
    const r = await api(ADMIN, 'POST', '/v2/calls', { case_id: CASE3, contact_id: data.blocked.id });
    check('refusal: the API refuses the blocked contact the same way (409 contact_blocked)', r.status === 409 && r.body.code === 'contact_blocked', `${r.status} ${r.body?.code}`);
    /* an API-only refusal shown under the row: stub POST /v2/calls to answer the rule callback_scheduled */
    await page.route('**/v2/calls', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ message: 'a callback is scheduled later', status: false, code: 'callback_scheduled', error: { rule: 'callback_scheduled', due_at: '2026-09-30T06:00:00+00:00', callable_from: '2026-09-30T05:45:00+00:00' } }) });
    });
    await setLang(page, 'en');
    await page.goto(`${FE}/clients/details/details?id=${CASE3}`, { waitUntil: 'networkidle' });
    const okRow = page.locator('[data-testid="p1-contact-row"]', { hasText: NUM.connected });
    await okRow.waitFor();
    await page.waitForTimeout(1500);
    await okRow.locator('[data-testid="p5-call-contact"]').click();
    await page.waitForTimeout(1200);
    const apiLine = await okRow.locator('[data-testid="p5-call-refusal"]').innerText().catch(() => '');
    check('refusal: an API refusal code (stubbed callback_scheduled) is shown under the row in words, with the Riyadh times', /callback is scheduled for 2026-09-30 09:00.*08:45/.test(apiLine), apiLine);
    await shot(page, '31_refusal_api_callback_en');
    await page.unroute('**/v2/calls');
    await setLang(page, 'ar');
    await page.context().close();
  }

  /* ---------------------------------------------------------------- click-to-call: connected */
  let connectedId = null;
  let connectedNote = null;
  if (want('c2c')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const lives = { r200: 0, r304: 0 };
    page.on('response', (r) => {
      if (/\/v2\/calls\/[0-9a-f-]+\/live$/.test(r.url().split('?')[0])) {
        if (r.status() === 304) lives.r304 += 1;
        else if (r.status() === 200) lives.r200 += 1;
      }
    });
    const sentIfNoneMatch = [];
    page.on('request', (r) => {
      if (/\/live$/.test(r.url()) && r.headers()['if-none-match']) sentIfNoneMatch.push(r.headers()['if-none-match']);
    });
    connectedId = await placeCall(page, NUM.connected);
    check('c2c: "Call" placed a click-to-call and the docked live panel opened', !!connectedId, connectedId);
    const firstLine = await page.locator('[data-testid="p5-live-line"]').innerText();
    check('c2c: the first state is honest (authorized / queued or submitted, never "connected")', !/connected/i.test(firstLine) || /not a connection|not connected/i.test(firstLine), firstLine);
    await shot(page, '40_c2c_dock_start_en', false);
    let seen = await followDock(page, ['bridged', 'ended', 'failed', 'uncertain'], 45000);
    await shot(page, '41_c2c_dock_bridged_en', false);
    const bridgedStep = await page.locator('[data-testid="p5-dock"] [data-step="bridged"]').getAttribute('data-status');
    const seen2 = await followDock(page, ['ended', 'failed', 'uncertain'], 60000);
    seen = seen.concat(seen2.filter((s) => s !== seen[seen.length - 1]));
    check('c2c: the timeline went through agent/customer steps to bridged, then ended', seen.includes('bridged') && seen[seen.length - 1] === 'ended' && bridgedStep === 'done', seen.join(' > '));
    check('c2c: ETag polling (If-None-Match sent, 304s answered)', sentIfNoneMatch.length > 0 && lives.r304 > 0, `If-None-Match x${sentIfNoneMatch.length}, 200 x${lives.r200}, 304 x${lives.r304}`);
    const reason = await page.locator('[data-testid="p5-live-final"] [data-reason]').getAttribute('data-reason').catch(() => null);
    check('c2c: ended by the PBX evidence (customer_hung_up) with a known duration', reason === 'customer_hung_up', reason);
    /* the wrap-up opened by itself */
    const dlg = page.locator('[data-testid="p5-wrapup"]');
    await dlg.waitFor({ timeout: 15000 });
    await page.waitForTimeout(800);
    check('c2c: the wrap-up opened by itself when the call ended; every outcome offered (bridge evidence)', (await dlg.locator('button[data-outcome][data-enabled="1"]').count()) === 7 && (await dlg.locator('[data-testid="p5-wrapup-evidence"]').getAttribute('data-evidenced')) === '1');
    await dlg.locator('button[data-outcome="promise_discussed"]').click();
    check('c2c: "promise discussed" says it never creates a promise and offers no promise fields', (await dlg.locator('[data-testid="p5-promise-discussed"]').count()) === 1 && (await dlg.locator('[data-testid="p5-promise-agreed"]').count()) === 0);
    await dlg.locator('button[data-outcome="connected"]').click();
    check('c2c: "connected" offers an explicitly agreed promise (checkbox)', (await dlg.locator('[data-testid="p5-promise"]').count()) === 1);
    connectedNote = `ZZ-TEST P5 ${RUN} connected via click-to-call (loopback test PBX)`;
    await fillNote(page, dlg, connectedNote, REACHED, ST_FOLLOW);
    await shot(page, '42_wrapup_connected_en', false);
    await page.click('[data-testid="p5-wrapup-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const detail = (await api(ADMIN, 'GET', `/v2/calls/${connectedId}`)).data;
    check('c2c: wrap-up saved: outcome connected, a case note linked, the reservation released', detail.wrapup?.outcome === 'connected' && !!detail.wrapup.note_id && detail.reservation && detail.reservation.active === false && detail.reservation.release_source === 'wrapup', JSON.stringify(detail.wrapup && { o: detail.wrapup.outcome, n: detail.wrapup.note_id, r: detail.reservation }));
    check('c2c: the dock shows the wrapped call', (await page.locator('[data-testid="p5-dock"] [data-testid="p5-live-wrapped"]').count()) === 1);
    await shot(page, '43_dock_wrapped_en', false);
    /* the note on the case timeline */
    await page.goto(`${FE}/clients/details/comments?id=${CASE3}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('c2c: the wrap-up note appears on the case timeline (notes tab)', (await page.locator('body').innerText()).includes(connectedNote));
    await shot(page, '44_case_timeline_note_en');
    await page.goto(`${FE}/clients/details/calls?id=${CASE3}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const crow = page.locator(`[data-testid="p5-case-call-row"][data-call="${connectedId}"]`);
    check('c2c: the case Calls tab lists the call', (await crow.count()) === 1);
    await crow.click();
    await page.locator('[data-testid="p5-case-call-panel"] [data-testid="p5-wrapup-note"]').waitFor({ timeout: 10000 });
    await shot(page, '45_case_calls_tab_detail_en');
    await setLang(page, 'ar');
    await shot(page, '45_case_calls_tab_detail_ar');
    check('c2c: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    await clearActive('c2c');
  }

  /* ---------------------------------------------------------------- busy: "connected" refused */
  if (want('busy')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const id = await placeCall(page, NUM.busy);
    const seen = await followDock(page, ['ended', 'failed', 'uncertain'], 60000);
    const reason = await page.locator('[data-testid="p5-live-final"] [data-reason]').getAttribute('data-reason').catch(() => null);
    check('busy: the call ended as customer_busy with no bridge (no conversation)', seen[seen.length - 1] === 'ended' && reason === 'customer_busy' && (await page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').getAttribute('data-evidenced')) === '0', `${seen.join(' > ')} ${reason}`);
    const dlg = page.locator('[data-testid="p5-wrapup"]');
    await dlg.waitFor({ timeout: 15000 });
    await page.waitForTimeout(800);
    const enabled = await dlg.locator('button[data-outcome][data-enabled="1"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-outcome')));
    check('busy: only no answer / busy / do not call are offered; "connected" explains it needs bridge evidence', JSON.stringify(enabled.sort()) === JSON.stringify(['busy', 'do_not_call', 'no_answer']) && (await dlg.locator('button[data-outcome="connected"]').isDisabled()) && (await dlg.locator('[data-testid="p5-wrapup-bridge-rule"]').count()) === 1, enabled.join(','));
    await shot(page, '50_wrapup_busy_connected_disabled_en', false);
    await closeDialogs(page);
    /* force the UI to offer "connected" (stubbed live view) so the API's own refusal shows */
    await page.route(`**/v2/calls/${id}/live`, async (route) => {
      const res = await route.fetch();
      if (res.status() !== 200) return route.fulfill({ response: res });
      const body = await res.json();
      body.data.actions.wrapup_outcomes = ['connected', 'no_answer', 'busy', 'promise_discussed', 'dispute', 'wrong_number', 'do_not_call'];
      await route.fulfill({ response: res, json: body });
    });
    await page.click('[data-testid="p5-dock"] [data-testid="p5-live-wrapup"]');
    await dlg.waitFor();
    await page.waitForTimeout(800);
    await dlg.locator('button[data-outcome="connected"]').click();
    await fillNote(page, dlg, `ZZ-TEST P5 ${RUN} busy (connected attempted)`, NOT_REACHED, ST_BUSY);
    await page.click('[data-testid="p5-wrapup-confirm"]');
    const err = dlg.locator('[data-testid="p1-inline-error"]');
    await err.waitFor({ timeout: 10000 });
    const errText = await err.innerText();
    check('busy: "connected" is refused by the API (409 conversation_not_evidenced) and the refusal is shown in words; the draft stays', /no bridged conversation/i.test(errText) && (await dlg.locator('[data-testid="p5-note-text"]').inputValue()).includes('busy'), errText);
    await shot(page, '51_wrapup_connected_refused_en', false);
    await page.unroute(`**/v2/calls/${id}/live`);
    await dlg.locator('button[data-outcome="busy"]').click();
    await page.click('[data-testid="p5-wrapup-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const detail = (await api(ADMIN, 'GET', `/v2/calls/${id}`)).data;
    check('busy: wrap-up busy saved (same dialog, same Idempotency-Key action)', detail.wrapup?.outcome === 'busy', detail.wrapup?.outcome);
    check('busy: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    await clearActive('busy');
  }

  /* ---------------------------------------------------------------- locked case: wrap-up recorded for review */
  if (want('locked')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const id = await placeCall(page, NUM.short);
    const seen = await followDock(page, ['ended', 'failed', 'uncertain'], 60000);
    check('locked: the short call ended by evidence', seen[seen.length - 1] === 'ended', seen.join(' > '));
    await page.locator('[data-testid="p5-wrapup"]').waitFor({ timeout: 15000 });
    await closeDialogs(page);
    const lock = await api(ADMIN, 'PUT', `/clint/lock/${CASE3}`, { reason: 'ZZ-TEST phase 5: locked for the locked-case wrap-up check' });
    check('locked: the case was locked before the wrap-up (legacy lock route)', lock.status === 200, lock.status);
    await page.click('[data-testid="p5-dock"] [data-testid="p5-live-wrapup"]');
    const dlg = page.locator('[data-testid="p5-wrapup"]');
    await dlg.waitFor();
    await dlg.locator('[data-testid="p5-wrapup-locked"]').waitFor({ timeout: 10000 });
    check('locked: the dialog explains the wrap-up is recorded and the case changes wait for a supervisor', /recorded/i.test(await dlg.locator('[data-testid="p5-wrapup-locked"]').innerText()));
    await dlg.locator('button[data-outcome="connected"]').click();
    const lockedNote = `ZZ-TEST P5 ${RUN} wrap-up on a locked case`;
    await fillNote(page, dlg, lockedNote, REACHED, ST_FOLLOW);
    await shot(page, '52_wrapup_locked_case_en', false);
    await page.click('[data-testid="p5-wrapup-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const detail = (await api(ADMIN, 'GET', `/v2/calls/${id}`)).data;
    check('locked: the API recorded the wrap-up for review (needs_review, no case note, reservation released)', detail.wrapup?.needs_review === true && detail.wrapup.review_reason === 'case_locked' && !detail.wrapup.note_id && detail.reservation?.active === false, JSON.stringify(detail.wrapup && { nr: detail.wrapup.needs_review, rr: detail.wrapup.review_reason, note: detail.wrapup.note_id }));
    /* supervisor: resolve the review flag */
    await page.goto(FE + '/call-review#tab=flags', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const frow = page.locator(`[data-testid="p5-review-flag-row"][data-call="${id}"][data-kind="wrapup_needs_review"]`);
    check('locked: the review flags list shows the wrap-up on the locked case', (await frow.count()) === 1);
    await shot(page, '53_review_flags_open_en');
    await frow.locator('[data-testid="p5-review-flag-resolve"]').click();
    await page.fill('[data-testid="p5-review-flag-dialog-reason"]', 'ZZ-TEST phase 5: reviewed; the case is unlocked by the verification script');
    await page.click('[data-testid="p5-review-flag-dialog-confirm"]');
    await page.waitForTimeout(1500);
    const flags = (await api(ADMIN, 'GET', '/v2/calling/review?state=all&kind=wrapup_needs_review&page_size=50')).data;
    const f = flags.items.find((x) => x.call.id === id);
    check('locked: the flag is resolved with the reason (review-flag resolve)', f && f.open === false && /reviewed/.test(f.resolution_reason || ''), JSON.stringify(f && { open: f.open, r: f.resolution_reason }));
    await page.goto(FE + '/call-review#tab=flags&flags=resolved', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await shot(page, '54_review_flags_resolved_en');
    await setLang(page, 'ar');
    await shot(page, '54_review_flags_resolved_ar');
    const un = await api(ADMIN, 'PUT', `/clint/unlock/${CASE3}`, { reason: 'ZZ-TEST phase 5: unlock after the locked-case check' });
    check('locked: the case is unlocked again', un.status === 200, un.status);
    check('locked: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    await clearActive('locked');
  }

  /* ---------------------------------------------------------------- manual call log + do-not-call block */
  if (want('manual')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await page.goto(`${FE}/clients/details/details?id=${CASE3}`, { waitUntil: 'networkidle' });
    await page.click('[data-testid="p5-log-call"]');
    const dlg = page.locator('[data-testid="p5-log"]');
    await dlg.waitFor();
    await page.fill('[data-testid="p5-log-minutes"]', '2');
    await page.fill('[data-testid="p5-log-seconds"]', '5');
    await pick(page, dlg.locator('[data-testid="p5-log-contact"]'), new RegExp('^' + NUM.connected));
    await dlg.locator('[data-testid="p5-log-outcome"] button[data-outcome="connected"]').click();
    await dlg.locator('[data-testid="p5-log-promise-agreed"]').check();
    const tomorrow = new Date(Date.now() + 3 * 3600 * 1000 + 86400000).toISOString().slice(0, 10);
    await page.fill('[data-testid="p5-log-promise-amount"]', '10.00');
    await page.fill('[data-testid="p5-log-promise-due"]', tomorrow);
    const manualNote = `ZZ-TEST P5 ${RUN} manual log (desk phone), agreed promise 10.00`;
    await fillNote(page, dlg, manualNote, REACHED, ST_FOLLOW);
    await page.fill('[data-testid="p5-log-transcript"]', `ZZ-TEST transcript ${RUN}: debtor agreed to pay 10.00 tomorrow.`);
    await shot(page, '46_log_call_dialog_en', false);
    await page.click('[data-testid="p5-log-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const list = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calls?page_size=5`)).data;
    const m = list.items.find((c) => c.mode === 'manual' && c.wrapup && c.wrapup.outcome === 'connected');
    const det = m && (await api(ADMIN, 'GET', `/v2/calls/${m.id}`)).data;
    check('manual: "Log a call" saved a manual call with its wrap-up (duration 125 s reported, transcript, connected)', det && det.duration_seconds === 125 && det.duration_source === 'reported' && det.has_transcript && det.wrapup.note_text === manualNote, JSON.stringify(det && { d: det.duration_seconds, s: det.duration_source, t: det.has_transcript }));
    check('manual: the explicitly agreed promise was created through the promise rules', !!det?.wrapup?.promise_id, det?.wrapup?.promise_id);
    if (det?.wrapup?.promise_id) {
      const pr = (await api(ADMIN, 'GET', `/v2/promises/${det.wrapup.promise_id}`)).data;
      check('manual: the promise is 10.00, due tomorrow, open', pr && pr.amount === '10.00' && pr.due_date === tomorrow && pr.state === 'open', JSON.stringify(pr && { a: pr.amount, d: pr.due_date, s: pr.state }));
      const c = await api(ADMIN, 'POST', `/v2/promises/${det.wrapup.promise_id}/cancel`, { reason: 'ZZ-TEST phase 5 verification: test promise', expected_revision: pr.revision });
      check('manual: the test promise is cancelled again (no open ZZ-TEST promise left)', c.status === 200, c.status);
    }
    /* a do-not-call manual log with an unknown duration -> the case block, then lift it */
    await page.click('[data-testid="p5-log-call"]');
    await dlg.waitFor();
    await dlg.locator('[data-testid="p5-log-outcome"] button[data-outcome="do_not_call"]').click();
    await page.fill('[data-testid="p5-dnc-reason"]', 'ZZ-TEST phase 5: debtor asked not to be called (test)');
    await fillNote(page, dlg, `ZZ-TEST P5 ${RUN} do-not-call requested (manual log, duration unknown)`, REACHED, ST_FOLLOW);
    check('manual: a blank duration is announced as unknown (never 0)', (await dlg.innerText()).includes('saved as unknown'));
    await page.click('[data-testid="p5-log-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const blocks = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calling-blocks`)).data;
    const dncCall = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calls?mode=manual&outcome=do_not_call&page_size=1`)).data.items[0];
    check('manual: do-not-call blocks the case (active calling block); the logged duration stays unknown', blocks.do_not_call === true && dncCall.duration_seconds === null && dncCall.duration_known === false, JSON.stringify({ dnc: blocks.do_not_call, d: dncCall.duration_seconds }));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    const banner = page.locator('[data-testid="p5-call-panel-refusal"] [data-code]');
    check('manual: the contacts panel now says the case cannot be called (do_not_call)', (await banner.getAttribute('data-code').catch(() => null)) === 'do_not_call', await banner.innerText().catch(() => ''));
    await shot(page, '47_case_do_not_call_en');
    await page.goto(`${FE}/clients/details/calls?id=${CASE3}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('manual: the case Calls tab shows the do-not-call block', (await page.locator('[data-testid="p5-case-dnc"]').count()) === 1);
    /* calling admin -> do-not-call -> lift with a reason */
    await page.goto(FE + '/calling-admin#tab=blocks', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    const brow = page.locator(`[data-testid="p5-admin-block-row"][data-case="${CASE3}"]`);
    check('manual: calling admin lists the case with an active do-not-call block', (await brow.getAttribute('data-active')) === '1');
    await shot(page, '48_admin_dnc_blocks_en');
    await brow.locator('[data-testid="p5-admin-block-lift"]').click();
    check('manual: lifting needs a reason', (await page.locator('[data-testid="p5-admin-lift-confirm"]').getAttribute('aria-disabled')) === 'true');
    await page.fill('[data-testid="p5-admin-lift-reason"]', 'ZZ-TEST phase 5 verification: lift the test block');
    await page.click('[data-testid="p5-admin-lift-confirm"]');
    await page.waitForTimeout(2000);
    const after = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calling-blocks`)).data;
    check('manual: the block is lifted with the reason (history kept)', after.do_not_call === false && after.items.some((b) => !b.active && /lift the test block/.test(b.release_reason || '')));
    check('manual: the admin row shows it lifted', (await brow.getAttribute('data-active')) === '0');
    check('manual: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- calls screen */
  if (want('calls')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await page.goto(FE + '/calls', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const mine = (await api(ADMIN, 'GET', '/v2/calls/mine?page_size=25')).data;
    const rows = await page.locator('[data-testid="p5-call-row"]').count();
    check('calls: "My calls" equals GET /v2/calls/mine (page 1)', rows === Math.min(25, mine.total) && mine.total > 0, `${rows} rows, api total ${mine.total}`);
    const allTab = await page.locator('[data-testid="p5-calls-state-tabs"] [data-tab="all"] [data-count]').getAttribute('data-count');
    check('calls: the "All" tab count equals the API total', Number(allTab) === mine.total, `${allTab} vs ${mine.total}`);
    /* filters in the hash */
    await page.locator('[data-testid="p5-calls-state-tabs"] [data-tab="ended"]').click();
    await pick(page, page.locator('[data-testid="p5-calls-outcome"]'), 'Connected');
    await page.waitForTimeout(1500);
    const hash = await page.evaluate(() => location.hash);
    const filtered = (await api(ADMIN, 'GET', '/v2/calls/mine?state=ended&outcome=connected&page_size=25')).data;
    const rows2 = await page.locator('[data-testid="p5-call-row"]').count();
    check('calls: state + outcome filters are in the hash and match the API', hash.includes('state=ended') && hash.includes('outcome=connected') && rows2 === Math.min(25, filtered.total), `${hash} rows=${rows2} api=${filtered.total}`);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('calls: a reload keeps the filters', (await page.locator('[data-testid="p5-call-row"]').count()) === rows2 && (await page.evaluate(() => location.hash)) === hash);
    const target = connectedId || filtered.items[0]?.id;
    if (target) {
      await page.goto(`${FE}/calls#call=${target}`, { waitUntil: 'networkidle' });
      const panel = page.locator('[data-testid="p5-call-panel"]');
      await panel.locator('[data-testid="p5-call-detail"]').waitFor({ timeout: 15000 });
      await panel.locator('[data-testid="p5-pbx-evidence"]').waitFor({ timeout: 20000 });
      const cdr = await panel.locator('[data-testid="p5-pbx-evidence"]').getAttribute('data-cdr-status');
      check('calls: the detail panel shows the timeline, the wrap-up, the evidence and the PBX records (CDR status is stated, unknown is not "none")', (await panel.locator('[data-testid="p5-live-steps"]').count()) === 1 && (await panel.locator('[data-testid="p5-wrapup-view"]').count()) === 1 && (await panel.locator('[data-testid="p5-evidence-legs"]').count()) === 1 && ['available', 'unavailable', 'disabled'].includes(cdr), `cdr=${cdr}`);
      await shot(page, '60_calls_detail_en');
      await setLang(page, 'ar');
      await shot(page, '60_calls_detail_ar');
      await setLang(page, 'en');
    }
    await page.goto(FE + '/calls#scope=team', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const team = (await api(ADMIN, 'GET', '/v2/calls?page_size=25')).data;
    check('calls: "All calls in my scope" equals GET /v2/calls (the supervisor scope)', (await page.locator('[data-testid="p5-call-row"]').count()) === Math.min(25, team.total), `api ${team.total}`);
    check('calls: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- PBX CDR import (bulk kind) */
  if (want('cdr')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await page.goto(FE + '/calls', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.click('[data-testid="p5-calls-import"]');
    await page.waitForURL('**/jobs/new/pbx_cdr_import', { timeout: 15000 });
    await page.waitForTimeout(1500);
    const txt = await page.locator('body').innerText();
    check('cdr: "Import PBX call records" opens the reviewed wizard for pbx_cdr_import (from GET /v2/bulk/kinds)', txt.includes('Import PBX call records'), txt.slice(0, 200));
    await shot(page, '70_cdr_wizard_source_en');
    /* a one-row CSV tagged with the connected call (cawp:<attempt>) */
    const attempt = connectedId || (await api(ADMIN, 'GET', '/v2/calls/mine?outcome=connected&mode=click_to_call&page_size=1')).data.items[0]?.id;
    const csvPath = path.join(outDir, `pbx_cdr_${RUN}.csv`);
    const when = new Date(Date.now() - 60000);
    const riyadh = new Date(when.getTime() + 3 * 3600 * 1000).toISOString().replace('T', ' ').slice(0, 19);
    fs.writeFileSync(csvPath, `calldate,src,dst,uniqueid,userfield,duration,billsec,disposition\n${riyadh},${EXT},+${NUM.connected},zztest-p5-${RUN},cawp:${attempt},25,20,ANSWERED\n`);
    const byTest = (id) => page.locator(`[data-testid="${id}"]`);
    await byTest('p2-wizard').waitFor();
    check('cdr: the wizard is the pbx_cdr_import kind', (await byTest('p2-wizard').getAttribute('data-kind')) === 'pbx_cdr_import');
    await byTest('p2-next').click();
    await byTest('p2-upload-input').setInputFiles(csvPath);
    await byTest('p2-inspection').waitFor({ timeout: 30000 });
    await page.waitForTimeout(500);
    await shot(page, '71_cdr_wizard_upload_en');
    await byTest('p2-next').click();
    await byTest('p2-step-mapping').waitFor();
    const mapped = await page.locator('[data-mapped-to]').count();
    check('cdr: the mapping step matched the PBX columns from the headers (calldate, src, dst, uniqueid, userfield, duration, billsec, disposition)', mapped >= 8, `mapped=${mapped}`);
    await byTest('p2-next').click();
    await byTest('p2-step-params').waitFor();
    check('cdr: the params step shows the PBX import fields (reason, source, time zone, match window)', (await byTest('p5-cdr-source').count()) === 1 && (await byTest('p5-cdr-timezone').count()) === 1 && (await byTest('p5-cdr-window').count()) === 1);
    check('cdr: "validate" stays disabled until the reason is filled', (await byTest('p2-create').getAttribute('aria-disabled')) === 'true');
    await byTest('p2-param-reason').fill(`ZZ-TEST phase 5 frontend verification ${RUN}: one tagged record`);
    await shot(page, '72_cdr_wizard_params_en');
    await byTest('p2-create').click();
    await page.waitForURL('**/jobs/run/**', { timeout: 20000 }).catch(() => {});
    const jobUrl = page.url();
    check('cdr: the job was created and the run page opened', /\/jobs\/run\//.test(jobUrl), jobUrl);
    if (/\/jobs\/run\//.test(jobUrl)) {
      const jobId = jobUrl.split('/jobs/run/')[1].split(/[?#]/)[0];
      await page.waitForFunction(() => {
        const el = document.querySelector('[data-testid="p2-run"]');
        return !!el && ['preview', 'failed', 'cancelled'].includes(el.getAttribute('data-stage'));
      }, null, { timeout: 90000 }).catch(() => {});
      await page.waitForTimeout(1500);
      const rows = (await api(ADMIN, 'GET', `/v2/bulk/jobs/${jobId}/rows?page_size=10`)).data;
      const row = rows?.items?.[0];
      check('cdr: the preview attributes the record to the call by its CRM tag (attempt_tag, eligible)', row && row.eligibility === 'eligible' && row.details?.attribution === 'attempt_tag' && row.details?.attempt_id === attempt, JSON.stringify(row && { e: row.eligibility, c: row.eligibility_code, a: row.details?.attribution, id: row.details?.attempt_id }));
      const body = await page.locator('body').innerText();
      check('cdr: the preview row shows the attribution in words ("CRM tag") and a link to the call', body.includes('CRM tag') && (await page.locator('[data-testid="p5-cdr-attempt-link"]').count()) >= 1);
      await shot(page, '73_cdr_preview_en');
      /* cancel: nothing is imported by the verification (the flow up to the reviewed preview works) */
      const c = await api(ADMIN, 'POST', `/v2/bulk/jobs/${jobId}/cancel`, { reason: 'ZZ-TEST phase 5 verification: preview checked' });
      check('cdr: the verification job was cancelled after the preview (nothing imported)', [200, 202].includes(c.status), c.status);
    }
    check('cdr: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- campaign */
  if (want('campaign')) {
    if (!campaignsLive) {
      check('campaign: the campaigns API is available on the running API (GET /v2/campaigns)', false, 'GET /v2/campaigns -> 404: cawp-api was started before api.calling_campaigns was registered; restart it, then run ONLY=campaign');
    } else {
      const { page, errors } = await newPage();
      await login(page);
      await setLang(page, 'en');
      await page.goto(FE + '/campaigns', { waitUntil: 'networkidle' });
      await page.click('[data-testid="p5-campaigns-new"]');
      const dlg = page.locator('[data-testid="p5-cf"]');
      await dlg.waitFor();
      const name = `ZZ-TEST P5 campaign ${RUN}`;
      await page.fill('[data-testid="p5-cf-name"]', name);
      await pick(page, dlg.locator('[data-testid="p5-cf-portfolios"]'), ZZ_PORTFOLIO);
      await pick(page, dlg.locator('[data-testid="p5-cf-grid-field"]'), 'Account number');
      await pick(page, dlg.locator('[data-testid="p5-cf-grid-op"]'), 'equals');
      await page.fill('[data-testid="p5-cf-grid-value"]', ACC4);
      await dlg.locator('[data-testid="p5-cf-mode"] [data-value="manual"]').click();
      for (const [k, v] of [['wrapup_delay_seconds', '0'], ['max_attempts_per_day', '50'], ['cooldown_minutes', '0'], ['lease_seconds', '30']]) await page.fill(`[data-testid="p5-cf-${k}"]`, v);
      await pick(page, dlg.locator('[data-testid="p5-cf-members"]'), ADMIN_LABEL);
      await shot(page, '80_campaign_form_en', false);
      await page.click('[data-testid="p5-cf-confirm"]');
      await page.waitForURL('**/campaigns/*', { timeout: 20000 });
      await page.waitForTimeout(1500);
      const cid = page.url().split('/campaigns/')[1].split(/[?#]/)[0];
      const c0 = (await api(ADMIN, 'GET', `/v2/campaigns/${cid}`)).data;
      check('campaign: created as a draft with the pool (portfolio + account-number column filter), manual mode, the signed-in user the only member', c0.status === 'draft' && c0.settings.next_mode === 'manual' && c0.pool.filter.filterField === 'accountNumber' && c0.members.active === 1, JSON.stringify({ s: c0.status, pool: c0.pool, m: c0.members }));
      await page.click('[data-testid="p5-cd-start"]');
      await page.locator('[data-testid="p5-cd-confirm-start"]').waitFor();
      await shot(page, '81_campaign_start_confirm_en', false);
      await page.click('[data-testid="p5-cd-confirm-start-confirm"]');
      await page.waitForTimeout(2000);
      check('campaign: started (running) through the confirmation', (await api(ADMIN, 'GET', `/v2/campaigns/${cid}`)).data.status === 'running');
      /* the collector side: ready -> next */
      await page.goto(`${FE}/calling#campaign=${cid}`, { waitUntil: 'networkidle' });
      await page.locator('[data-testid="p5-part"]').waitFor({ timeout: 15000 });
      await page.locator('[data-testid="p5-part-readiness"] [data-value="ready"]').click();
      await page.waitForTimeout(1500);
      const part = page.locator('[data-testid="p5-part"]');
      check('campaign: ready declared (lease live; manual mode waits for "next")', (await part.getAttribute('data-declared')) === 'ready' && (await part.getAttribute('data-state')) === 'waiting_next', `${await part.getAttribute('data-declared')} / ${await part.getAttribute('data-state')}`);
      await shot(page, '82_workspace_ready_en');
      await page.click('[data-testid="p5-part-next"]');
      await page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').waitFor({ timeout: 20000 });
      const aid = await page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').getAttribute('data-attempt');
      const call = (await api(ADMIN, 'GET', `/v2/calls/${aid}`)).data;
      check('campaign: "next" placed exactly one campaign call on the pool case (the test number)', call.mode === 'campaign' && call.case.id === CASE4 && call.campaign_id === cid && call.destination === '+' + NUM.campaign, JSON.stringify({ m: call.mode, c: call.case.id, d: call.destination }));
      const seen = await followDock(page, ['ended', 'failed', 'uncertain'], 60000);
      check('campaign: the campaign call went through bridged to ended', seen.includes('bridged') && seen[seen.length - 1] === 'ended', seen.join(' > '));
      const dlg2 = page.locator('[data-testid="p5-wrapup"]');
      await dlg2.waitFor({ timeout: 15000 });
      await page.waitForTimeout(800);
      await dlg2.locator('button[data-outcome="connected"]').click();
      await fillNote(page, dlg2, `ZZ-TEST P5 ${RUN} campaign call wrap-up`, REACHED, ST_FOLLOW);
      await page.click('[data-testid="p5-wrapup-confirm"]');
      await dlg2.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(2500);
      check('campaign: wrap-up saved (connected)', (await api(ADMIN, 'GET', `/v2/calls/${aid}`)).data.wrapup?.outcome === 'connected');
      await shot(page, '83_workspace_after_call_en');
      /* the supervisor's live view */
      await page.goto(`${FE}/campaigns/${cid}`, { waitUntil: 'networkidle' });
      await page.locator('[data-testid="p5-cd-live"]').waitFor({ timeout: 15000 });
      await page.waitForTimeout(2500);
      const live = (await api(ADMIN, 'GET', `/v2/campaigns/${cid}/live`)).data;
      const kpi = await page.locator('[data-testid="p5-cd-kpi-attempts"]').innerText();
      const connectedOutcome = await page.locator('[data-testid="p5-cd-outcomes"] [data-outcome="connected"][data-count]').first().getAttribute('data-count').catch(() => null);
      const member = await page.locator(`[data-testid="p5-cd-member"][data-user="${ADMIN_ID}"]`).count();
      const pool = (await page.locator('[data-testid="p5-cd-pool-size"]').innerText()).trim().split('\n').pop().trim();
      check('campaign: the live view reflects the call (attempts, conversations, outcome connected, the member, the pool summary)', live.today.attempts >= 1 && live.today.conversations >= 1 && kpi.includes(String(live.today.attempts)) && Number(connectedOutcome) >= 1 && member === 1 && pool === String(live.pool.size), JSON.stringify({ kpi, connectedOutcome, member, pool, today: live.today, size: live.pool.size }));
      await shot(page, '84_campaign_live_en');
      await setLang(page, 'ar');
      await shot(page, '84_campaign_live_ar');
      await setLang(page, 'en');
      /* pool and roster tabs */
      await page.goto(`${FE}/campaigns/${cid}#tab=pool`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      check('campaign: the pool tab lists case 4 with its reason', (await page.locator('[data-testid="p5-cd-pool-row"]').count()) >= 1);
      await shot(page, '85_campaign_pool_en');
      await page.goto(`${FE}/campaigns/${cid}#tab=roster`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(1500);
      check('campaign: the roster tab lists the member (active)', (await page.locator(`[data-testid="p5-cd-roster-row"][data-user="${ADMIN_ID}"][data-active="1"]`).count()) === 1);
      /* drain: nothing outstanding -> stopped at once */
      await page.goto(`${FE}/campaigns/${cid}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(1200);
      await page.click('[data-testid="p5-cd-drain"]');
      await page.locator('[data-testid="p5-cd-confirm-drain"]').waitFor();
      await page.click('[data-testid="p5-cd-confirm-drain-confirm"]');
      await page.waitForTimeout(2500);
      const c2 = (await api(ADMIN, 'GET', `/v2/campaigns/${cid}`)).data;
      check('campaign: drain with nothing outstanding stopped it at once (stopped / drained)', c2.status === 'stopped' && c2.stop_reason === 'drained', `${c2.status} ${c2.stop_reason}`);
      await shot(page, '86_campaign_stopped_en');
      check('campaign: no page errors', errors.length === 0, errors.join(' | '));
      await page.context().close();
      await clearActive('campaign');
    }
  }

  /* ---------------------------------------------------------------- campaign, auto mode: heartbeat + wrap-up delay */
  if (want('auto') && campaignsLive) {
    const { page, errors } = await newPage();
    const beats = [];
    page.on('request', (r) => {
      if (/\/heartbeat$/.test(r.url())) beats.push({ t: Date.now(), body: r.postData() || '' });
    });
    await login(page);
    await setLang(page, 'en');
    const pf = (await api(ADMIN, 'GET', `/v2/cases/${CASE4}/calls?page_size=1`)).status;
    const created = await api(ADMIN, 'POST', '/v2/campaigns', {
      name: `ZZ-TEST P5 auto campaign ${RUN}`,
      pool: { portfolio_ids: [], contract_ids: [], filter: { filterField: 'accountNumber', filterOperator: 'equals', filterFieldType: 'string', filterValue: ACC4 } },
      settings: { next_mode: 'auto', wrapup_delay_seconds: 45, max_attempts_per_day: 50, cooldown_minutes: 0, lease_seconds: 30 },
      member_ids: [ADMIN_ID],
    });
    const cid = created.data?.id;
    const st = cid && (await api(ADMIN, 'POST', `/v2/campaigns/${cid}/start`, { expected_revision: created.data.revision }));
    check('auto: an automatic-mode campaign (45 s wrap-up delay) created and started', !!cid && st.status === 200, `${created.status} ${pf}`);
    await page.goto(`${FE}/calling#campaign=${cid}`, { waitUntil: 'networkidle' });
    await page.locator('[data-testid="p5-part"]').waitFor({ timeout: 15000 });
    await page.locator('[data-testid="p5-part-readiness"] [data-value="ready"]').click();
    /* the calling service dispatches by itself (no "next" in auto mode) */
    await page.locator('[data-testid="p5-dock"] [data-testid="p5-live"]').waitFor({ timeout: 30000 });
    const seen = await followDock(page, ['ended', 'failed', 'uncertain'], 60000);
    check('auto: ready -> the calling service placed the call by itself; it ended by evidence', seen[seen.length - 1] === 'ended', seen.join(' > '));
    const dlg = page.locator('[data-testid="p5-wrapup"]');
    await dlg.waitFor({ timeout: 15000 });
    await page.waitForTimeout(800);
    await dlg.locator('button[data-outcome="connected"]').click();
    await fillNote(page, dlg, `ZZ-TEST P5 ${RUN} auto campaign wrap-up`, REACHED, ST_FOLLOW);
    await page.click('[data-testid="p5-wrapup-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    const cd = page.locator('[data-testid="p5-part-countdown"]');
    await cd.waitFor({ timeout: 15000 }).catch(() => {});
    const s1 = Number(await cd.getAttribute('data-seconds').catch(() => -1));
    await page.waitForTimeout(3000);
    const s2 = Number(await cd.getAttribute('data-seconds').catch(() => -1));
    check('auto: after the wrap-up the wrap-up delay counts down to the next call', s1 > 0 && s2 < s1 && (await page.locator('[data-testid="p5-part"]').getAttribute('data-state')) === 'wrap_up', `${s1} -> ${s2}`);
    await shot(page, '87_workspace_auto_countdown_en');
    const renewals = beats.filter((b) => b.body === '{}' || b.body === '');
    check('auto: the heartbeat renews the lease on an interval while the page is open (no Idempotency-Key needed)', renewals.length >= 1, `${beats.length} heartbeats, ${renewals.length} renewals`);
    await page.locator('[data-testid="p5-part-readiness"] [data-value="offline"]').click();
    await page.waitForTimeout(1500);
    check('auto: going offline ends the lease at once', (await page.locator('[data-testid="p5-part"]').getAttribute('data-declared')) === 'offline');
    await setLang(page, 'ar');
    await shot(page, '88_workspace_offline_ar');
    const c = (await api(ADMIN, 'GET', `/v2/campaigns/${cid}`)).data;
    const stop = await api(ADMIN, 'POST', `/v2/campaigns/${cid}/stop`, { expected_revision: c.revision, reason: 'ZZ-TEST phase 5 verification done' });
    check('auto: the test campaign is stopped', stop.status === 200 && stop.data.status === 'stopped', stop.status);
    check('auto: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    await clearActive('auto');
  }

  /* ---------------------------------------------------------------- uncertain: resolve */
  if (want('uncertain')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const id = await placeCall(page, NUM.echo);
    const seen = await followDock(page, ['bridged', 'ended', 'failed', 'uncertain'], 45000);
    check('uncertain: the echo call is bridged', seen[seen.length - 1] === 'bridged', seen.join(' > '));
    log('stopping the calling service while the call is bridged (a lost PBX session)');
    stopCalling();
    const seen2 = await followDock(page, ['uncertain'], 30000);
    check('uncertain: the call became uncertain (calling_service_stopped): nothing assumed', seen2[seen2.length - 1] === 'uncertain' && (await page.locator('[data-testid="p5-dock"] [data-testid="p5-live-uncertain"]').count()) === 1, seen2.join(' > '));
    const unc = await page.locator('[data-testid="p5-live-uncertain"]').innerText();
    check('uncertain: the panel explains it (unknown, the reservation stays, the wrap-up waits)', /unknown/i.test(unc) && /reservation stays/i.test(unc), unc);
    await shot(page, '90_dock_uncertain_en', false);
    await page.goto(FE + '/call-review', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const urow = page.locator(`[data-testid="p5-review-uncertain-row"][data-call="${id}"]`);
    check('uncertain: the supervisor list shows the call with its reason', (await urow.count()) === 1 && /calling service stopped/i.test(await urow.innerText()), await urow.innerText().catch(() => ''));
    await shot(page, '91_review_uncertain_en');
    await urow.locator('[data-testid="p5-review-resolve-open"]').click();
    const rd = page.locator('[data-testid="p5-review-resolve"]');
    await rd.waitFor();
    check('uncertain: resolve needs a reason (confirm disabled while empty)', (await page.locator('[data-testid="p5-review-resolve-confirm"]').getAttribute('aria-disabled')) === 'true');
    await page.fill('[data-testid="p5-review-resolve-reason"]', 'ZZ-TEST phase 5: the calling service was stopped on purpose; the call was bridged on the test PBX');
    await shot(page, '92_review_resolve_dialog_en', false);
    await page.click('[data-testid="p5-review-resolve-confirm"]');
    await page.waitForTimeout(2000);
    const d = (await api(ADMIN, 'GET', `/v2/calls/${id}`)).data;
    check('uncertain: resolved as ended with the reservation released (source reconciled)', d.state === 'ended' && d.reservation?.release_source === 'reconciled', `${d.state} ${d.reservation?.release_source}`);
    check('uncertain: the list is empty of that call now', (await page.locator(`[data-testid="p5-review-uncertain-row"][data-call="${id}"]`).count()) === 0);
    /* the collector wraps it up (bridge evidence exists: connected is allowed) */
    await page.goto(`${FE}/calls#call=${id}`, { waitUntil: 'networkidle' });
    await page.locator('[data-testid="p5-detail-wrapup"]').click({ timeout: 15000 });
    const dlg = page.locator('[data-testid="p5-wrapup"]');
    await dlg.waitFor();
    await page.waitForTimeout(800);
    await dlg.locator('button[data-outcome="connected"]').click();
    await fillNote(page, dlg, `ZZ-TEST P5 ${RUN} wrap-up after a supervisor resolved the uncertain call`, REACHED, ST_FOLLOW);
    await page.click('[data-testid="p5-wrapup-confirm"]');
    await dlg.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200);
    check('uncertain: wrapped up from the calls screen after the resolution', (await api(ADMIN, 'GET', `/v2/calls/${id}`)).data.wrapup?.outcome === 'connected');
    log('restarting the calling service');
    startCalling();
    await ensureCalling();
    check('uncertain: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    await clearActive('uncertain');
  }

  /* ---------------------------------------------------------------- release the test extension */
  if (want('release')) {
    await clearActive('release');
    const { page } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await page.goto(FE + '/calling-admin#tab=extensions', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const row = page.locator(`[data-testid="p5-admin-ext-row"][data-ext="${EXT}"][data-active="1"]`);
    if (await row.count()) {
      await row.locator('[data-testid="p5-admin-ext-release"]').click();
      check('release: the reason is required', (await page.locator('[data-testid="p5-admin-release-confirm"]').getAttribute('aria-disabled')) === 'true');
      await page.fill('[data-testid="p5-admin-release-reason"]', `ZZ-TEST phase 5 frontend verification ${RUN}: done`);
      await page.click('[data-testid="p5-admin-release-confirm"]');
      await page.waitForTimeout(1800);
    }
    const me = (await api(ADMIN, 'GET', '/v2/me/calling')).data;
    check('release: extension 7101 released through the UI (aidi.dev has no extension; history kept)', !me.extension && (await page.locator(`[data-testid="p5-admin-ext-row"][data-ext="${EXT}"][data-active="1"]`).count()) === 0, JSON.stringify(me.extension));
    await page.goto(FE + '/calling-admin#tab=extensions&ext=all', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await shot(page, '95_admin_extension_released_en');
    await page.context().close();
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    await run(browser);
  } catch (e) {
    check('script finished without an exception', false, e.stack || String(e));
  } finally {
    await browser.close();
    try {
      await clearActive('final');
    } catch {
      /* the API may be down */
    }
    if (process.env.KEEP !== '1' && ONLY.length === 0) {
      log('stopping the test PBX and the calling service');
      try {
        sh('docker', ['stop', 'cawp-calling', 'cawp-pbx-test'], { timeout: 120000 });
      } catch (e) {
        log('stop failed: ' + String(e).slice(0, 200));
      }
    }
    const passed = results.filter((r) => r.ok).length;
    log(`\n${passed}/${results.length} checks passed`);
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify({ run: RUN, fe: FE, api: API, passed, total: results.length, results }, null, 2));
    fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
    process.exit(passed === results.length ? 0 : 1);
  }
})();
