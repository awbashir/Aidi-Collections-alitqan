/* Phase 6 frontend checks: AI administration, AI drafts, the quality workspace, their nav and
 * permissions. Headless Chrome through the playwright-core install in ../tools/node_modules.
 * Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase6.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots (ar/en) and verify.json/verify.log go to outDir (default logs/screens/phase6).
 *
 * NO REAL PROVIDER IS EVER CONTACTED. There is no OpenAI key on this API: every provider action
 * is refused by the API itself (`ai_not_configured`, ...), and those live refusals are checked.
 * Provider-dependent success paths (a generated draft, a transcription, an AI review draft) are
 * checked with Playwright route stubs that answer exactly the API's documented success bodies
 * (review/phase6-drafts.md, review/phase6-quality.md). Any request to a host other than the app
 * and the API is aborted and counted (must stay 0).
 *
 * Drafts: the API writes an `ai_drafts` row only after a provider call, so a live "save as note"
 * needs a draft row. The `drafts` section seeds two rows straight into the test database
 * (`docker exec cawp-db psql`), labelled "ZZ-TEST seeded draft (no provider call)" with the model
 * `zz-test-seed-no-provider`; the stubbed generation hands back the first one exactly as
 * `GET /v2/ai/drafts/<id>` returns it, and every later step (open, edit, save as note, discard)
 * is LIVE against the real row.
 *
 * ONLY=<section,...>: screens, gating, perms, admin, drafts, quality, stubs, fromcall, finish.
 *
 * Test data it writes (all ZZ-TEST): AI data policy revisions (an approved test policy, then a
 * final SUSPENDED revision with 3650-day retention: no provider call is possible under it and
 * nothing is purged), audited refused provider-call rows, two seeded drafts on ZZTEST-P1-0001
 * (one saved as a note, which appears on the case timeline, one discarded), and two generated
 * WAV recordings in the QC library (one fully reviewed and approved).
 */
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const ROOT = path.resolve(__dirname, '../..');
const { chromium } = require(path.join(ROOT, 'tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const outDir = path.resolve(process.argv[2] || path.join(ROOT, 'logs/screens/phase6'));
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PASS = 'Aidi@2026';
const ADMIN = 'aidi.dev';
const ADMIN_ID = 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf';
const COLLECTOR = 'aidi.collector';
const CASE1 = '44f5da48-0cf4-4686-a538-380c14c06ae3'; // ZZTEST-P1-0001
const CASE3 = 'e1afce87-41d5-461a-bd9b-79beab1f15d3'; // ZZTEST-P1-0003 (Phase 5 test calls)
const REACHED = 'تم التوصل';
const ST_ACTIVE = 'جاري التفاوض'; // an ACTIVE status: AI save-as-note refuses inactive ones (see the report)
const RUN = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12);
const DB = ['exec', 'cawp-db', 'psql', '-U', 'postgres', '-d', 'mydata', '-v', 'ON_ERROR_STOP=1', '-tA', '-c'];

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
const sql = (q) => execFileSync('docker', [...DB, q], { encoding: 'utf8' }).trim();
const externalRequests = [];

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
async function api(user, method, p, body, { raw } = {}) {
  const tok = await apiLogin(user);
  const headers = { Authorization: 'Bearer ' + tok };
  if (body !== undefined && !raw) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = crypto.randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : raw ? body : JSON.stringify(body) });
  let j = null;
  try {
    j = await r.json();
  } catch {
    /* empty body */
  }
  return { status: r.status, body: j, data: j && j.data, code: j && j.code };
}

/* ------------------------------------------------------------ test audio */
/** A mono 16-bit PCM WAV of `seconds` with seeded noise (a new seed = a new sha256, no duplicate). */
function makeWav(seconds, seed) {
  const rate = 8000;
  const n = rate * seconds;
  const buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + n * 2, 4);
  buf.write('WAVE', 8);
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(n * 2, 40);
  let x = crypto.createHash('sha256').update(String(seed)).digest().readUInt32LE(0) || 1;
  for (let i = 0; i < n; i += 1) {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    x >>>= 0;
    const tone = Math.round(3000 * Math.sin((2 * Math.PI * 440 * i) / rate));
    buf.writeInt16LE(Math.max(-32768, Math.min(32767, tone + ((x % 2001) - 1000))), 44 + i * 2);
  }
  return buf;
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
const byTest = (page, id) => page.locator(`[data-testid="${id}"]`);
/** The design system's Button never sets `disabled`; it blocks the click and sets aria-disabled. */
const off = async (loc) => (await loc.getAttribute('aria-disabled')) === 'true';
async function pick(page, scope, text) {
  await scope.locator('.select__control').first().click();
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
async function sidebarCounts(page) {
  return page.$$eval('.side-nav .menu-nav-group', (gs) =>
    gs.map((g) => [...g.querySelectorAll('.menu-item, .menu-collapse-item')].filter((el) => !el.parentElement.closest('.menu-collapse > ul, .menu-collapse > div:not(.menu-collapse-item)')).length)
  );
}
const navLabels = (page) => page.$$eval('.side-nav .menu-item', (els) => els.map((e) => e.textContent.trim()));
/** Replace the JSON `data` of a live response (for "provider ready" stubs). */
async function patchJson(route, mutate) {
  const res = await route.fetch();
  const body = await res.json();
  mutate(body);
  await route.fulfill({ response: res, json: body });
}
const READY = { ready: true, reasons: [], required_categories: [] };
/* route matchers kept by reference so that page.unroute() finds them */
const DRAFTS_PATH = (u) => u.pathname === '/v2/ai/drafts';
const STATUS_PATH = (u) => u.pathname === '/v2/ai/status';
/** Status stub: the provider is "ready" (only the readiness answer is changed). */
async function stubAiReady(page) {
  await page.route(STATUS_PATH, (route) =>
    patchJson(route, (b) => {
      b.data.reasons = [];
      b.data.capabilities.generate_structured = { ...b.data.capabilities.generate_structured, ...READY };
    })
  );
}
async function stubQualityReady(page) {
  await page.route((u) => u.pathname === '/v2/quality', (route) =>
    patchJson(route, (b) => {
      b.data.ai.transcribe = { ...b.data.ai.transcribe, ...READY, required_categories: ['audio'] };
      b.data.ai.draft = { ...b.data.ai.draft, ...READY };
    })
  );
}

/* ------------------------------------------------------------ seeded drafts */
function seedDraft(label, policyRevision) {
  const id = crypto.randomUUID();
  const draft = {
    summary: `ZZ-TEST seeded draft (no provider call) ${label} ${RUN}: the case has an open balance.`,
    actions: [
      { action: 'Call about the open balance', reason: 'The balance is outstanding', priority: 'high', fact_ids: ['F1'] },
      { action: 'Offer a repayment plan', reason: 'No plan is recorded', priority: 'medium', fact_ids: [] },
    ],
    risks: ['No recent contact'],
    missing_information: [],
  };
  const content = { draft, checks: { unknown_fact_ids: [], unsupported: ['actions[1]'] }, citations: { F1: 'ZZ-TEST fact line F1 (seeded)' } };
  const text = [
    'Summary:',
    draft.summary,
    '',
    'Recommended actions:',
    '1. Call about the open balance (high)',
    '   Reason: The balance is outstanding',
    '2. Offer a repayment plan (medium) (no supporting case fact was cited)',
    '   Reason: No plan is recorded',
    '',
    'Risks:',
    '- No recent contact',
  ].join('\n');
  const hex = (s) => crypto.createHash('sha256').update(s).digest('hex');
  const q = `INSERT INTO ai_drafts (id, kind, case_id, requested_by, language, options, input_categories, input_hash,
      masked_input_fingerprint, input_summary, schema_name, schema_version, content, generated_text, text, provider_call_id,
      model, policy_revision, masked, masked_count, expires_at)
    VALUES ('${id}', 'case_guidance', '${CASE1}', '${ADMIN_ID}', 'en', '{}'::jsonb, ARRAY['case_facts','notes'],
      '${hex('in' + id)}', '${hex('mask' + id)}', '{"case_facts": 1, "calls": 0, "notes": 0}'::jsonb, 'case_guidance', 1,
      $j$${JSON.stringify(content)}$j$::jsonb, $t$${text}$t$, $t$${text}$t$, '${crypto.randomUUID()}',
      'zz-test-seed-no-provider', ${policyRevision}, true, 0, now() + interval '14 days') RETURNING id`;
  return sql(q).split('\n')[0].trim();
}

/* ============================================================ sections */
async function run(browser) {
  const newPage = async () => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
    page.on('response', (r) => {
      if (r.url().startsWith(API + '/v2/') && r.status() >= 500 && r.status() !== 503) errors.push(`HTTP ${r.status()} ${r.url()}`);
    });
    /* nothing but the app and the API: a provider host would be a bug */
    await ctx.route(
      (u) => !['localhost', '127.0.0.1', '[::1]'].includes(u.hostname),
      (route) => {
        externalRequests.push(route.request().url());
        return route.abort();
      }
    );
    return { ctx, page, errors };
  };

  /* ---------------------------------------------------------------- screens */
  if (want('screens')) {
    const { page, errors } = await newPage();
    await login(page);
    const screens = [
      ['01_ai_admin_readiness', '/ai-admin', 'p6-admin-readiness'],
      ['02_ai_admin_policy', '/ai-admin#tab=policy', 'p6-policy'],
      ['03_ai_admin_history', '/ai-admin#tab=history', 'p6-policy-history'],
      ['04_ai_admin_calls', '/ai-admin#tab=calls', 'p6-provider-calls'],
      ['05_quality_overview', '/quality', 'p6-qc-dashboard'],
      ['06_quality_library', '/quality#tab=library', 'p6-qc-library'],
      ['07_case_ai_drafts', `/clients/details/aiDrafts?id=${CASE1}`, 'p6-case-drafts'],
    ];
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      for (const [name, url, testId] of screens) {
        errors.length = 0;
        await page.goto(FE + url, { waitUntil: 'networkidle' });
        await page.waitForTimeout(1500);
        const ok = await byTest(page, testId).first().isVisible().catch(() => false);
        const errState = await page.locator(`[data-testid="${testId}"] [data-testid="p1-error"]`).count();
        const dir = await page.evaluate(() => document.documentElement.dir);
        check(`screens ${lang}: ${url} renders (${testId}), dir=${dir}`, ok && errState === 0 && dir === (lang === 'ar' ? 'rtl' : 'ltr') && errors.length === 0, `visible=${ok} errorStates=${errState} ${errors.join(' | ')}`);
        await shot(page, `${name}_${lang}`);
      }
      await page.goto(FE + '/home', { waitUntil: 'networkidle' });
      await settle(page, 800);
      const counts = await sidebarCounts(page);
      check(`screens ${lang}: aidi.dev sidebar items per group [9,13,14] (voice lab made it [9,13,12]; + AI administration, + Quality review)`, JSON.stringify(counts) === '[9,13,14]', JSON.stringify(counts));
      const labels = await navLabels(page);
      const expect = lang === 'ar' ? ['إدارة الذكاء الاصطناعي', 'مراجعة الجودة', 'مختبر التدريب الصوتي'] : ['AI administration', 'Quality review', 'Voice training lab'];
      check(`screens ${lang}: the Phase 6 nav items are present (Intelligence & control)`, expect.every((l) => labels.includes(l)), expect.filter((l) => !labels.includes(l)).join(', '));
    }
    await setLang(page, 'ar');
    await page.context().close();
  }

  /* ---------------------------------------------------------------- gating */
  if (want('gating')) {
    const { page } = await newPage();
    await login(page, COLLECTOR);
    await settle(page);
    const labels = await navLabels(page);
    const p6 = ['إدارة الذكاء الاصطناعي', 'مراجعة الجودة', 'مختبر التدريب الصوتي'];
    check('gating: aidi.collector (no AI / quality keys) sees none of the Phase 6 nav items', p6.every((l) => !labels.includes(l)), labels.filter((l) => p6.includes(l)).join(', '));
    for (const u of ['/ai-admin', '/ai-admin#tab=policy', '/quality', '/quality#tab=library']) {
      await page.goto(FE + u, { waitUntil: 'networkidle' });
      await page.waitForTimeout(600);
      check(`gating: aidi.collector ${u} -> access-denied`, page.url().includes('/access-denied'), page.url());
    }
    await page.goto(`${FE}/clients/details/details?id=${CASE1}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const body = await page.locator('body').innerText();
    check('gating: aidi.collector has no "AI drafts" tab on a case page', !body.includes('مسودات الذكاء الاصطناعي') && !body.includes('AI drafts'));
    await page.goto(`${FE}/clients/details/aiDrafts?id=${CASE1}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('gating: ... and the AI drafts panel does not render for aidi.collector even by URL', (await byTest(page, 'p6-case-drafts').count()) === 0);
    await shot(page, '10_collector_case_no_drafts_ar');
    const r1 = await api(COLLECTOR, 'GET', '/v2/ai/status');
    const r2 = await api(COLLECTOR, 'GET', '/v2/quality');
    const r3 = await api(COLLECTOR, 'GET', `/v2/ai/drafts?case=${CASE1}`);
    check('gating: the API agrees (aidi.collector: /v2/ai/status, /v2/quality, /v2/ai/drafts -> 403)', r1.status === 403 && r2.status === 403 && r3.status === 403, `${r1.status} ${r2.status} ${r3.status}`);
    await page.context().close();

    /* an approver-only role (UI stub: quality_approve without quality_review; the API decides) */
    const { page: p2 } = await newPage();
    await login(p2, ADMIN, { permissions: { dashbord: true, clints: true, quality_approve: true } });
    await settle(p2);
    const l2 = await navLabels(p2);
    check('gating: a quality_approve-only role sees Quality review but not AI administration', l2.includes('مراجعة الجودة') && !l2.includes('إدارة الذكاء الاصطناعي'), l2.join(' | '));
    await p2.goto(FE + '/quality#tab=library', { waitUntil: 'networkidle' });
    await p2.waitForTimeout(1500);
    check('gating: ... its library has no "Import a recording" (needs quality_review)', (await byTest(p2, 'p6-qc-library').count()) === 1 && (await byTest(p2, 'p6-qc-import-open').count()) === 0);
    await p2.goto(FE + '/ai-admin', { waitUntil: 'networkidle' });
    await p2.waitForTimeout(600);
    check('gating: ... /ai-admin -> access-denied', p2.url().includes('/access-denied'), p2.url());
    await shot(p2, '11_approver_only_library_ar');
    await p2.context().close();
  }

  /* ---------------------------------------------------------------- perms */
  if (want('perms')) {
    const py = fs.readFileSync(path.join(ROOT, 'CAWP-API/core/permissions.py'), 'utf8');
    const reg = [...py.matchAll(/PermissionKey\("([a-z_]+)"/g)].map((m) => m[1]);
    const ts = fs.readFileSync(path.join(ROOT, 'CAWP-frontend/src/constants/permissions.phase1.ts'), 'utf8');
    const mirror = [...ts.matchAll(/\{ key: '([a-z_]+)'/g)].map((m) => m[1]);
    check('perms: the frontend mirror equals core/permissions.py NEW_PERMISSIONS (same keys, same order)', JSON.stringify(reg) === JSON.stringify(mirror), `${reg.length} vs ${mirror.length}`);
    const { page } = await newPage();
    await login(page);
    await page.goto(FE + '/permissions/new', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
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
    const labels = ['مسودات الذكاء الاصطناعي (مراجعة قبل الحفظ)', 'حالة الذكاء الاصطناعي وسياسة البيانات', 'مراجعة الجودة', 'اعتماد مراجعات الجودة', 'مختبر التدريب الصوتي'];
    check('perms: the permissions screen shows the five Phase 6 keys (ar labels)', labels.every((l) => text.includes(l)), labels.filter((l) => !text.includes(l)).join(', '));
    check('perms: the workflow section is titled "Phases 1–6"', text.includes('المراحل 1–6'));
    await shot(page, '12_permissions_phase6_keys_ar');
    await page.context().close();
  }

  /* ---------------------------------------------------------------- AI admin (live) */
  if (want('admin')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await page.goto(FE + '/ai-admin', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const st = (await api(ADMIN, 'GET', '/v2/ai/status')).data;
    const capReasons = await page.$$eval('[data-testid="p6-admin-cap-reasons"]', (els) => els.map((e) => e.getAttribute('data-reasons')));
    const apiReasons = ['generate_structured', 'transcribe', 'realtime_session'].map((c) => st.capabilities[c].reasons.join(','));
    check('admin: readiness per capability shows exactly the API reasons (no key: ai_not_configured, model_not_set, ...)', JSON.stringify(capReasons) === JSON.stringify(apiReasons) && apiReasons.every((r) => r.includes('ai_not_configured') && r.includes('model_not_set')), JSON.stringify(capReasons));
    const keyShown = await byTest(page, 'p6-admin-key-configured').getAttribute('data-value');
    const bodyText = await page.locator('body').innerText();
    check('admin: "key configured" is "No" and no key text is shown anywhere', keyShown === '0' && !/sk-[A-Za-z0-9]/.test(bodyText), keyShown);
    check('admin: the quota block shows the API numbers', (await byTest(page, 'p6-admin-quota-user').innerText()).replace(/\s/g, '').endsWith(`${st.quota.user.used}/${st.quota.user.limit}`), await byTest(page, 'p6-admin-quota-user').innerText());
    /* "Check the connection": the honest refusal */
    const [checkRes] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/v2/ai/check') && r.request().method() === 'POST'),
      byTest(page, 'p6-admin-check-run').click(),
    ]);
    await page.waitForTimeout(600);
    const checkBody = await checkRes.json();
    const shownCode = await page.locator('[data-testid="p6-admin-check-error"] ul').getAttribute('data-code').catch(() => null);
    check('admin: "Check the connection" -> the API refuses (503 ai_not_configured) and the refusal is shown with every reason, nothing simulated', checkRes.status() === 503 && checkBody.code === 'ai_not_configured' && shownCode === 'ai_not_configured' && (await byTest(page, 'p6-admin-check-result').count()) === 0, `${checkRes.status()} ${checkBody.code} shown=${shownCode}`);
    await shot(page, '20_admin_check_refused_en');

    /* approve a test policy through the UI */
    const before = (await api(ADMIN, 'GET', '/v2/ai/policy')).data;
    await page.goto(FE + '/ai-admin#tab=policy', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('admin: the policy form shows the loaded revision it will replace', (await byTest(page, 'p6-policy-expected').getAttribute('data-expected')) === String(before.revision), before.revision);
    await page.click('[data-testid="p6-policy-state"] [data-value="approved"]');
    for (const c of ['case_facts', 'notes', 'transcripts', 'audio']) {
      const cb = byTest(page, `p6-policy-category-${c}`);
      if (!(await cb.isChecked())) await cb.check();
    }
    const emp = byTest(page, 'p6-policy-category-employee_names');
    if (await emp.isChecked()) await emp.uncheck();
    for (const k of ['name', 'identity_number', 'phone', 'account']) {
      const cb = byTest(page, `p6-policy-mask-${k}`);
      if (!(await cb.isChecked())) await cb.check();
    }
    for (const k of ['transcripts', 'audio', 'drafts', 'provider_logs']) await byTest(page, `p6-policy-days-${k}`).fill('3650');
    check('admin: "Record the policy" stays disabled without a reason and the confirmation', (await off(byTest(page, 'p6-policy-save'))));
    await byTest(page, 'p6-policy-reason').fill(`ZZ-TEST phase 6 frontend verification ${RUN}: test policy (no provider key is configured; 3650-day retention so nothing is purged)`);
    check('admin: ... still disabled until "I confirm" is ticked', (await off(byTest(page, 'p6-policy-save'))));
    await byTest(page, 'p6-policy-confirm').check();
    await byTest(page, 'p6-policy-mask-phone').uncheck();
    check('admin: unticking a masking class warns that identifiers would be sent in clear text', (await byTest(page, 'p6-policy-unmasked').count()) === 1);
    await byTest(page, 'p6-policy-mask-phone').check();
    await shot(page, '21_admin_policy_form_en');
    const [putRes] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/v2/ai/policy') && r.request().method() === 'PUT'),
      byTest(page, 'p6-policy-save').click(),
    ]);
    const putReq = JSON.parse(putRes.request().postData() || '{}');
    await page.waitForTimeout(1500);
    const after = (await api(ADMIN, 'GET', '/v2/ai/policy')).data;
    check('admin: the policy is recorded through the UI (PUT with expected_revision, confirmed: true, Idempotency-Key) -> revision + 1, approved, the four categories, all masked', putRes.status() === 200 && putReq.expected_revision === before.revision && putReq.confirmed === true && !!putRes.request().headers()['idempotency-key'] && after.revision === before.revision + 1 && after.policy.state === 'approved' && JSON.stringify(after.policy.allowed_categories) === JSON.stringify(['case_facts', 'notes', 'transcripts', 'audio']) && Object.values(after.policy.masking).every(Boolean), `${putRes.status()} rev ${before.revision}->${after.revision}`);
    check('admin: the current policy card shows the new revision', (await byTest(page, 'p6-policy-current').locator('[data-revision]').first().getAttribute('data-revision')) === String(after.revision));
    await shot(page, '22_admin_policy_recorded_en');
    /* a stale form: another revision recorded meanwhile -> 409 shown with a reload */
    await api(ADMIN, 'PUT', '/v2/ai/policy', { ...putReq, expected_revision: after.revision, reason: `ZZ-TEST phase 6 ${RUN}: concurrent revision (conflict check)` });
    await byTest(page, 'p6-policy-reason').fill(`ZZ-TEST phase 6 ${RUN}: stale form`);
    await byTest(page, 'p6-policy-confirm').check();
    await byTest(page, 'p6-policy-save').click();
    await page.waitForTimeout(1200);
    check('admin: a stale policy form is refused (409 revision_conflict) and offers "reload the latest"', (await byTest(page, 'p1-conflict').count()) === 1);
    /* history */
    await page.goto(FE + '/ai-admin#tab=history', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const hist = (await api(ADMIN, 'GET', '/v2/ai/policy/history?page_size=20')).data;
    const rows = await page.$$eval('[data-testid="p6-policy-history-row"]', (els) => els.map((e) => e.getAttribute('data-revision')));
    check('admin: the history lists every revision, newest first (equals the API)', JSON.stringify(rows) === JSON.stringify(hist.items.map((p) => String(p.revision))) && rows[0] === String(after.revision + 1), rows.slice(0, 5).join(','));
    await shot(page, '23_admin_policy_history_en');
    /* provider calls: the refused rows are audited, metadata only; filters in the hash */
    await page.goto(FE + '/ai-admin#tab=calls&outcome=refused', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const calls = (await api(ADMIN, 'GET', '/v2/ai/provider-calls?outcome=refused&page_size=25')).data;
    const callRows = await page.$$eval('[data-testid="p6-call-row"]', (els) => els.map((e) => [e.getAttribute('data-outcome'), e.getAttribute('data-purpose')]));
    check('admin: provider calls filtered by outcome=refused (from the URL hash) equal the API, including the connection check', callRows.length === calls.items.length && callRows.every((r) => r[0] === 'refused') && callRows.some((r) => r[1] === 'connectivity_check'), `${callRows.length} rows`);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    check('admin: the filter survives a reload (hash state)', (await byTest(page, 'p6-call-row').count()) === calls.items.length && page.url().includes('outcome=refused'));
    await shot(page, '24_admin_provider_calls_refused_en');
    check('admin: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- AI drafts */
  if (want('drafts')) {
    const pol = (await api(ADMIN, 'GET', '/v2/ai/policy')).data;
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    /* 1. the live state: not configured -> Generate disabled, the reasons shown */
    await page.goto(`${FE}/clients/details/aiDrafts?id=${CASE1}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    const liveReasons = await page.locator('[data-testid="p6-draft-not-ready"] ul').getAttribute('data-reasons').catch(() => '');
    check('drafts: live (no key): the readiness notice lists the API reasons and Generate is disabled', (liveReasons || '').includes('ai_not_configured') && (liveReasons || '').includes('model_not_set') && (await off(byTest(page, 'p6-draft-generate-btn'))), liveReasons);
    check('drafts: ... with a link to AI administration for an ai_admin holder', (await byTest(page, 'p6-ai-admin-link').count()) >= 1);
    await shot(page, '30_drafts_not_configured_en');
    /* 2. the real POST refusal (status stubbed "ready" only so the button can be pressed) */
    await stubAiReady(page);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const [genRes] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/v2/ai/drafts' && r.request().method() === 'POST'),
      byTest(page, 'p6-draft-generate-btn').click(),
    ]);
    await page.waitForTimeout(600);
    const genBody = await genRes.json();
    const shown = await page.locator('[data-testid="p6-draft-refusal"] ul').getAttribute('data-code').catch(() => null);
    const genReq = JSON.parse(genRes.request().postData() || '{}');
    check('drafts: live POST /v2/ai/drafts -> 503 ai_not_configured, shown as the refusal (no draft appears)', genRes.status() === 503 && genBody.code === 'ai_not_configured' && shown === 'ai_not_configured' && (await byTest(page, 'p6-draft').count()) === 0, `${genRes.status()} ${genBody.code}`);
    check('drafts: the request is {kind, case_id, language} with an Idempotency-Key', genReq.kind === 'call_script' && genReq.case_id === CASE1 && ['ar', 'en'].includes(genReq.language) && !!genRes.request().headers()['idempotency-key'], JSON.stringify(genReq));
    await shot(page, '31_drafts_live_refusal_en');
    /* 3. refusals only generation can reveal: category denied (409) and quota (429), exactly as documented */
    for (const [status, body, testName] of [
      [409, { message: 'the AI data policy does not allow sending this data', status: false, code: 'ai_policy_category_denied', error: { capability: 'generate_structured', reasons: ['ai_policy_category_denied'], denied_categories: ['notes'], call_id: crypto.randomUUID() } }, 'category denied (409): names the data category'],
      [429, { message: 'your daily limit of AI provider calls is reached', status: false, code: 'ai_quota_exceeded', error: { capability: 'generate_structured', reasons: ['ai_quota_exceeded'], scope: 'user', limit: 50, used: 50, resets_at: '2026-09-24T21:00:00+00:00', call_id: crypto.randomUUID() } }, 'quota (429): says whose limit and when it resets'],
    ]) {
      await page.route(DRAFTS_PATH, (route) => (route.request().method() === 'POST' ? route.fulfill({ status, json: body }) : route.fallback()));
      await byTest(page, 'p6-draft-generate-btn').click();
      await page.waitForTimeout(700);
      const txt = await byTest(page, 'p6-draft-refusal').innerText().catch(() => '');
      const ok = status === 409 ? /Notes/.test(txt) : /50 of 50/.test(txt) && /2026-09-25 00:00/.test(txt);
      check(`drafts: stubbed ${testName}`, ok, txt.slice(0, 160));
      await page.unroute(DRAFTS_PATH);
    }
    await shot(page, '32_drafts_quota_refusal_en');
    /* 4. seeded rows: the stubbed generation returns row #1 exactly as the API does; the rest is live */
    /* an aborted earlier run may have left seeded drafts open: discard them first (live, audited) */
    for (const line of sql(`SELECT id || ',' || revision FROM ai_drafts WHERE model = 'zz-test-seed-no-provider' AND status IN ('generated', 'edited')`).split('\n').filter(Boolean)) {
      const [lid, lrev] = line.split(',');
      await api(ADMIN, 'POST', `/v2/ai/drafts/${lid}/discard`, { expected_revision: Number(lrev) });
    }
    const id1 = seedDraft('A', pol.revision);
    const id2 = seedDraft('B', pol.revision);
    check('drafts: two ZZ-TEST draft rows seeded (the API only writes one after a provider call)', /^[0-9a-f-]{36}$/.test(id1) && /^[0-9a-f-]{36}$/.test(id2), `${id1} ${id2}`);
    const full1 = await api(ADMIN, 'GET', `/v2/ai/drafts/${id1}`);
    await page.route(DRAFTS_PATH, (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 201, json: { message: 'AI draft generated; it is a draft only and nothing was changed', status: true, data: full1.data } })
        : route.fallback()
    );
    await page.click('[data-testid="p6-draft-kinds"] [data-kind="case_guidance"]');
    await page.click('[data-testid="p6-draft-language"] [data-value="en"]');
    await byTest(page, 'p6-draft-generate-btn').click();
    await page.waitForTimeout(1800);
    await page.unroute(DRAFTS_PATH);
    const d = byTest(page, 'p6-draft');
    check('drafts: the generated draft opens (live GET) with the API notice "AI-generated draft. Review and edit it before saving"', (await d.getAttribute('data-draft')) === id1 && /AI-generated draft\. Review and edit it before saving/.test(await byTest(page, 'p6-ai-generated').first().innerText()), await d.getAttribute('data-draft'));
    check('drafts: the item without a cited case fact is flagged ("Offer a repayment plan")', /Offer a repayment plan/.test(await byTest(page, 'p6-draft-unsupported').innerText().catch(() => '')));
    check('drafts: the open draft is in the URL hash (#draft=<id>)', page.url().includes(`draft=${id1}`), page.url());
    await shot(page, '33_draft_generated_stub_en');
    /* edit (nothing auto-saved) -> Save edits (live PATCH) */
    const edited = `ZZ-TEST phase 6 ${RUN}: reviewed and edited by a person. Call about the open balance; no plan was offered.`;
    await byTest(page, 'p6-draft-text').fill(edited);
    check('drafts: an edit is only local ("Unsaved edits") until saved', (await byTest(page, 'p6-draft-dirty').count()) === 1 && (await api(ADMIN, 'GET', `/v2/ai/drafts/${id1}`)).data.status === 'generated');
    const [patchRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes(`/v2/ai/drafts/${id1}`) && r.request().method() === 'PATCH'),
      byTest(page, 'p6-draft-save-edits').click(),
    ]);
    await page.waitForTimeout(1200);
    const afterEdit = (await api(ADMIN, 'GET', `/v2/ai/drafts/${id1}`)).data;
    check('drafts: "Save edits" -> live PATCH (expected_revision 1) -> status edited, revision 2, the text saved', patchRes.status() === 200 && JSON.parse(patchRes.request().postData()).expected_revision === 1 && afterEdit.status === 'edited' && afterEdit.revision === 2 && afterEdit.text === edited, `${patchRes.status()} ${afterEdit.status} r${afterEdit.revision}`);
    /* save as note: explicit confirmation, the note form's status / method */
    await byTest(page, 'p6-draft-save-note').click();
    const dlg = byTest(page, 'p6-save-note');
    await dlg.waitFor({ timeout: 10000 });
    await pick(page, dlg.locator('[data-testid="p6-note-reach"]'), REACHED);
    await page.waitForTimeout(300);
    await pick(page, dlg.locator('[data-testid="p6-note-status"]'), ST_ACTIVE);
    check('drafts: "Save as note" stays disabled until "I reviewed this AI-generated text" is ticked', (await byTest(page, 'p6-save-note-confirm').getAttribute('aria-disabled')) === 'true' && !(await byTest(page, 'p6-save-note-reviewed').isChecked()));
    await byTest(page, 'p6-save-note-reviewed').check();
    await shot(page, '34_draft_save_as_note_dialog_en');
    const [saveRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes(`/v2/ai/drafts/${id1}/save-as-note`)),
      byTest(page, 'p6-save-note-confirm').click(),
    ]);
    const saveReq = JSON.parse(saveRes.request().postData() || '{}');
    if (saveRes.status() !== 201) log('save-as-note answer: ' + JSON.stringify(await saveRes.json().catch(() => null)) + ' request: ' + JSON.stringify(saveReq));
    await page.waitForTimeout(1500);
    const saved = (await api(ADMIN, 'GET', `/v2/ai/drafts/${id1}`)).data;
    check('drafts: live save-as-note -> 201, {confirmed: true, text, status, method, expected_revision 2}, no callback/promise field; the draft is saved_as_note with its note id', saveRes.status() === 201 && saveReq.confirmed === true && saveReq.expected_revision === 2 && saveReq.text === edited && !!saveReq.comment_status_id && !!saveReq.contact_method_id && !('callback_at' in saveReq) && !('promise' in saveReq) && saved.status === 'saved_as_note' && !!saved.saved_note_id, `${saveRes.status()} ${saved.status}`);
    check('drafts: the panel shows the draft as saved as a note (read-only)', (await byTest(page, 'p6-draft-saved-note').count()) === 1 && (await byTest(page, 'p6-draft-save-note').count()) === 0);
    await shot(page, '35_draft_saved_as_note_en');
    await page.goto(`${FE}/clients/details/comments?id=${CASE1}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    check('drafts: the saved note appears on the case timeline (notes tab)', (await page.locator('body').innerText()).includes(edited.slice(0, 60)));
    await shot(page, '36_case_timeline_ai_note_en');
    /* discard (live) */
    await page.goto(`${FE}/clients/details/aiDrafts?id=${CASE1}#draft=${id2}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    check('drafts: the previous drafts list shows both seeded drafts with their status', (await page.locator(`[data-testid="p6-draft-row"][data-draft="${id1}"][data-status="saved_as_note"]`).count()) === 1 && (await page.locator(`[data-testid="p6-draft-row"][data-draft="${id2}"][data-status="generated"]`).count()) === 1);
    await byTest(page, 'p6-draft-discard').click();
    await byTest(page, 'p6-draft-discard-dialog-confirm').click();
    await page.waitForTimeout(1500);
    const disc = (await api(ADMIN, 'GET', `/v2/ai/drafts/${id2}`)).data;
    check('drafts: Discard (confirmed) -> live: discarded, content cleared; the panel says so', disc.status === 'discarded' && disc.content_available === false && (await byTest(page, 'p6-draft-no-content').count()) === 1, disc.status);
    await shot(page, '37_draft_discarded_en');
    /* ar */
    await setLang(page, 'ar');
    await page.goto(`${FE}/clients/details/aiDrafts?id=${CASE1}#draft=${id1}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    await shot(page, '38_drafts_saved_ar');
    await page.unroute(STATUS_PATH);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await shot(page, '39_drafts_not_configured_ar');
    /* the call entries: call detail + wrap-up ("Draft summary / coaching") */
    await setLang(page, 'en');
    const callId = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calls?page_size=1`)).data?.items?.[0]?.id;
    if (callId) {
      await page.goto(`${FE}/calls#scope=team&call=${callId}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      await byTest(page, 'p6-call-ai-drafts').click();
      await page.waitForTimeout(1500);
      const kinds = await page.$$eval('[data-testid="p6-draft-dialog"] [data-testid="p6-draft-kinds"] [data-kind]', (els) => els.map((e) => e.getAttribute('data-kind')));
      check('drafts: call detail -> "Draft summary / coaching (AI)" opens the call drafts with the call kinds and the honest state', JSON.stringify(kinds) === '["call_summary","coaching"]' && (await page.locator('[data-testid="p6-draft-dialog"] [data-testid="p6-draft-not-ready"]').count()) === 1, JSON.stringify(kinds));
      await shot(page, '40_call_detail_ai_drafts_en');
      await byTest(page, 'p6-draft-dialog-close').click();
      /* the wrap-up entry: the call detail says a wrap-up is pending (stub) so "Wrap up" opens the dialog */
      const CALL_PATH = (u) => u.pathname === `/v2/calls/${callId}`;
      await page.route(CALL_PATH, (route) => patchJson(route, (b) => { b.data.wrapup_pending = true; }));
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      await byTest(page, 'p5-detail-wrapup').click();
      await page.waitForTimeout(2000);
      const entry = byTest(page, 'p6-wrapup-ai-drafts');
      const hasEntry = (await entry.count()) === 1;
      if (hasEntry) await entry.click();
      await page.waitForTimeout(1500);
      check('drafts: the wrap-up has the "Draft summary / coaching (AI)" entry, which opens the call drafts', hasEntry && (await byTest(page, 'p6-draft-dialog').count()) === 1);
      await shot(page, '41_wrapup_ai_entry_en');
      await page.unroute(CALL_PATH);
    } else {
      check('drafts: a Phase 5 test call exists on ZZTEST-P1-0003 for the call entries', false, 'no call');
    }
    check('drafts: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- quality (live) */
  let liveRec = null;
  if (want('quality')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const dashBefore = (await api(ADMIN, 'GET', '/v2/quality/dashboard')).data;
    await page.goto(FE + '/quality#tab=library', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    /* an inspection refusal: not a WAV */
    const junk = path.join(outDir, `zz-test-not-audio-${RUN}.wav`);
    fs.writeFileSync(junk, Buffer.from('this is not a wav file, only ZZ-TEST text'));
    await byTest(page, 'p6-qc-import-open').click();
    await byTest(page, 'p6-qc-import-file').setInputFiles(junk);
    const [badRes] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/v2/quality/recordings' && r.request().method() === 'POST'),
      byTest(page, 'p6-qc-import-confirm').click(),
    ]);
    await page.waitForTimeout(700);
    const badText = await page.locator('[data-testid="p6-qc-import"] [data-testid="p1-inline-error"]').innerText().catch(() => '');
    check('quality: import of a non-audio file -> the API inspection refusal is shown in words (dialog stays open)', [415, 422].includes(badRes.status()) && /supported|WAV|recording/i.test(badText), `${badRes.status()} ${badText.slice(0, 120)}`);
    await shot(page, '50_qc_import_refused_en');
    await page.locator('[data-testid="p6-qc-import"]').locator('button', { hasText: /^Cancel$/ }).click();
    fs.unlinkSync(junk);
    /* import a generated WAV */
    const wav = path.join(outDir, `zz-test-qc-${RUN}.wav`);
    fs.writeFileSync(wav, makeWav(8, `live-${RUN}`));
    await byTest(page, 'p6-qc-import-open').click();
    await byTest(page, 'p6-qc-import-file').setInputFiles(wav);
    await byTest(page, 'p6-qc-import-title').fill(`ZZ-TEST phase 6 ${RUN} live review`);
    const [impRes] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/v2/quality/recordings' && r.request().method() === 'POST'),
      byTest(page, 'p6-qc-import-confirm').click(),
    ]);
    const imp = await impRes.json();
    await page.waitForTimeout(1200);
    liveRec = imp.data?.recording?.id;
    check('quality: a generated 8 s WAV is imported through the UI (201, new), the notice links to it', impRes.status() === 201 && imp.data.duplicate === false && (await byTest(page, 'p6-qc-imported').count()) === 1, `${impRes.status()} ${liveRec}`);
    /* the same file again: a duplicate, not stored twice */
    await byTest(page, 'p6-qc-import-open').click();
    await byTest(page, 'p6-qc-import-file').setInputFiles(wav);
    const [dupRes] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/v2/quality/recordings' && r.request().method() === 'POST'),
      byTest(page, 'p6-qc-import-confirm').click(),
    ]);
    await page.waitForTimeout(800);
    check('quality: importing the same audio again -> 200 duplicate, the UI says it is already in the library', dupRes.status() === 200 && (await page.locator('[data-testid="p6-qc-imported"] [data-duplicate="1"]').count()) === 1);
    await byTest(page, 'p6-qc-imported-open').click();
    await page.waitForTimeout(2500);
    check('quality: the recording opens with #rec=<id> in the hash', page.url().includes(`rec=${liveRec}`) && (await byTest(page, 'p6-qc-detail').getAttribute('data-recording')) === liveRec, page.url());
    /* the player: byte ranges, seekable */
    await page.waitForSelector('[data-testid="p6-qc-player"][data-loaded="1"]', { timeout: 20000 });
    const req = await byTest(page, 'p6-qc-player').getAttribute('data-requests');
    const partial = await byTest(page, 'p6-qc-player').getAttribute('data-partial');
    const dur = await page.evaluate(async () => {
      const a = document.querySelector('[data-testid="p6-qc-audio"]');
      for (let i = 0; i < 50 && !(a.duration > 0); i += 1) await new Promise((r) => setTimeout(r, 100));
      a.currentTime = 3.2;
      await new Promise((r) => setTimeout(r, 300));
      return { duration: a.duration, current: a.currentTime };
    });
    check('quality: the player loaded the private audio with Range requests (206) and is seekable', Number(req) >= 1 && Number(partial) === Number(req) && Math.abs(dur.duration - 8) < 0.2 && Math.abs(dur.current - 3.2) < 0.2, `requests=${req} partial=${partial} ${JSON.stringify(dur)}`);
    /* evidence */
    await pick(page, byTest(page, 'p6-qc-ev-template'), /Collections/);
    await byTest(page, 'p6-qc-ev-employee').fill('ZZ-TEST agent 1');
    await byTest(page, 'p6-qc-ev-notes').fill('Customer confirmed identity and promised 500 riyals next Sunday.');
    await byTest(page, 'p6-qc-ev-action').fill('Follow-up call on Monday');
    await byTest(page, 'p6-qc-ev-save').click();
    await page.waitForTimeout(1500);
    let rec = (await api(ADMIN, 'GET', `/v2/quality/recordings/${liveRec}`)).data;
    check('quality: evidence saved (template collections-v1, employee label, notes, action; revision 2)', rec.evidence.template?.key === 'collections-v1' && rec.evidence.employee_label === 'ZZ-TEST agent 1' && rec.revision === 2, JSON.stringify(rec.evidence.template));
    await shot(page, '51_qc_evidence_en');
    /* transcript: manual, one line per segment */
    await page.click('[data-testid="p6-qc-sections"] [data-tab="transcript"]');
    await page.waitForTimeout(1200);
    check('quality: Transcribe is disabled with the honest reasons (no provider)', (await off(byTest(page, 'p6-qc-transcribe-run'))) && ((await page.locator('[data-testid="p6-qc-transcribe-not-ready"] ul').getAttribute('data-reasons')) || '').includes('ai_not_configured'));
    const LINES = [
      'Good morning, this is Sara from the collections team, calling about your account.',
      'Before we continue, can you confirm your date of birth?',
      'Yes, it is the first of May.',
      'Thank you. The outstanding balance is 1,500 riyals.',
      'I will pay 500 riyals next Sunday.',
      'Noted. I will call you on Monday to confirm the payment.',
    ];
    await page.click('[data-testid="p6-qc-transcript-mode"] [data-value="text"]');
    await page.click('[data-testid="p6-qc-transcript-lang"] [data-value="en"]');
    await byTest(page, 'p6-qc-transcript-text').fill(LINES.join('\n'));
    await byTest(page, 'p6-qc-transcript-save').click();
    await page.waitForTimeout(1500);
    rec = (await api(ADMIN, 'GET', `/v2/quality/recordings/${liveRec}`)).data;
    check('quality: manual transcript saved as version 1 with 6 segments (s1..s6); the view shows them', rec.transcript?.version === 1 && rec.transcript.segment_count === 6 && rec.status === 'transcribed' && (await page.locator('[data-testid="p6-qc-transcript-view"] [data-segment]').count()) === 6, `${rec.transcript?.version} ${rec.transcript?.segment_count}`);
    await shot(page, '52_qc_transcript_en');
    /* review: every criterion; a paraphrased quotation first (refused), then the verbatim one */
    await page.click('[data-testid="p6-qc-sections"] [data-tab="review"]');
    await page.waitForTimeout(1500);
    const crit = (id) => page.locator(`[data-testid="p6-qc-criterion"][data-criterion="${id}"]`);
    const setResult = async (id, result, reason) => {
      await crit(id).locator(`[data-testid="p6-qc-result"] [data-result="${result}"]`).click();
      await crit(id).locator('[data-testid="p6-qc-reason"]').fill(reason);
    };
    const quote = async (id, seg) => {
      await crit(id).locator('[data-testid="p6-qc-add-quote"]').click();
      await crit(id).locator(`[data-testid="p6-qc-pick-${seg}"]`).check();
      await crit(id).locator('[data-testid="p6-qc-pick-use"]').click();
    };
    await setResult('intro', 'met', 'The agent introduced herself, the team and the purpose.');
    await quote('intro', 's1');
    await crit('intro').locator('[data-testid="p6-qc-quote-text"]').fill('this is Sarah from collections');
    const clientSaysNo = await crit('intro').locator('[data-testid="p6-qc-quote"]').getAttribute('data-verbatim');
    await setResult('identity', 'met', 'Identity was verified before the debt was discussed.');
    await quote('identity', 's2');
    await setResult('debt', 'met', 'The balance was stated.');
    await quote('debt', 's4');
    await setResult('affordability', 'not_applicable', 'No payment difficulty was raised.');
    await setResult('listening', 'insufficient_evidence', 'Not enough dialogue to judge.');
    await setResult('alternatives', 'not_applicable', 'The customer did not refuse.');
    await setResult('next_steps', 'needs_improvement', 'The follow-up was stated but the promise date was not summarised.');
    await quote('next_steps', 's6');
    await setResult('notes', 'insufficient_evidence', 'Not compared in this test.');
    await setResult('action', 'insufficient_evidence', 'Not compared in this test.');
    await setResult('delivery', 'met', 'Clear and courteous tone (listened).');
    await crit('delivery').locator('[data-testid="p6-qc-add-stamp"]').click();
    await page.evaluate(() => {
      document.querySelector('[data-testid="p6-qc-audio"]').currentTime = 1.5;
    });
    await page.waitForTimeout(1200);
    await crit('delivery').locator('[data-testid="p6-qc-stamp-capture-start"]').click();
    await page.evaluate(() => {
      document.querySelector('[data-testid="p6-qc-audio"]').currentTime = 4.0;
    });
    await page.waitForTimeout(1200);
    await crit('delivery').locator('[data-testid="p6-qc-stamp-capture-end"]').click();
    await crit('delivery').locator('[data-testid="p6-qc-stamp-human"]').check();
    const stampStart = await crit('delivery').locator('[data-testid="p6-qc-stamp-start"]').inputValue();
    const stampEnd = await crit('delivery').locator('[data-testid="p6-qc-stamp-end"]').inputValue();
    check('quality: the client check flags the paraphrase before saving; the audio timestamp is captured from the player', clientSaysNo === '0' && stampStart === '1.5' && stampEnd === '4.0', `${clientSaysNo} ${stampStart}-${stampEnd}`);
    const [bad] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith(`/v2/quality/recordings/${liveRec}/review`) && r.request().method() === 'PUT'),
      byTest(page, 'p6-qc-review-save').click(),
    ]);
    const badBody = await bad.json();
    await page.waitForTimeout(800);
    const introErrors = await crit('intro').getAttribute('data-server-errors');
    check('quality: the save is refused (422 evidence_rule_failed) and the errors are shown at the "intro" criterion (the paraphrase is not in the cited segment s1: quote_not_in_segments, evidence_required)', bad.status() === 422 && badBody.code === 'evidence_rule_failed' && (introErrors || '').includes('quote_not_in_segments') && (introErrors || '').includes('evidence_required') && Object.keys(badBody.error.criteria).join() === 'intro' && (await byTest(page, 'p6-qc-review-failed').count()) === 1, `${bad.status()} ${introErrors}`);
    await shot(page, '53_qc_review_evidence_rule_failed_en');
    await crit('intro').locator('[data-testid="p6-qc-quote-text"]').fill('this is Sara from the collections team');
    check('quality: the verbatim quotation passes the client check', (await crit('intro').locator('[data-testid="p6-qc-quote"]').getAttribute('data-verbatim')) === '1');
    const [good] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith(`/v2/quality/recordings/${liveRec}/review`) && r.request().method() === 'PUT'),
      byTest(page, 'p6-qc-review-save').click(),
    ]);
    await page.waitForTimeout(1500);
    rec = (await api(ADMIN, 'GET', `/v2/quality/recordings/${liveRec}`)).data;
    check('quality: the corrected review saves (human revision 1): 4 met, 1 needs improvement -> compliance 80.0 %, coverage 50.0 %', good.status() === 200 && rec.review?.origin === 'human' && rec.review.metrics.compliance?.percent === 80 && rec.review.metrics.coverage?.percent === 50 && (await byTest(page, 'p6-qc-review-compliance').getAttribute('data-percent')) === '80', `${good.status()} ${JSON.stringify(rec.review?.metrics?.compliance)}`);
    const delivery = rec.review.results.find((r) => r.criterion_id === 'delivery');
    check('quality: the human-listening criterion kept its human-reviewed audio timestamp (1.5–4.0 s)', delivery.result === 'met' && delivery.evidence[0]?.type === 'audio' && delivery.evidence[0].start === 1.5 && delivery.evidence[0].end === 4 && delivery.evidence[0].human_reviewed === true, JSON.stringify(delivery.evidence));
    await shot(page, '54_qc_review_saved_en');
    /* approve (quality_approve, confirmed) */
    await byTest(page, 'p6-qc-approve').click();
    check('quality: Approve needs the explicit confirmation', (await page.locator('[data-testid="p6-qc-approve-dialog-confirm"]').getAttribute('aria-disabled')) === 'true');
    await byTest(page, 'p6-qc-approve-confirm').check();
    await shot(page, '55_qc_approve_dialog_en');
    const [apRes] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith(`/v2/quality/recordings/${liveRec}/approve`)),
      byTest(page, 'p6-qc-approve-dialog-confirm').click(),
    ]);
    const apReq = JSON.parse(apRes.request().postData() || '{}');
    await page.waitForTimeout(1500);
    rec = (await api(ADMIN, 'GET', `/v2/quality/recordings/${liveRec}`)).data;
    check('quality: approved (POST approve {expected_revision, confirmed: true}) -> status approved', apRes.status() === 200 && apReq.confirmed === true && typeof apReq.expected_revision === 'number' && rec.status === 'approved' && (await byTest(page, 'p6-qc-review-approved').count()) === 1, `${apRes.status()} ${rec.status}`);
    /* approval history and snapshot */
    await page.click('[data-testid="p6-qc-sections"] [data-tab="approvals"]');
    await page.waitForTimeout(1800);
    const ap = (await api(ADMIN, 'GET', `/v2/quality/approvals/${rec.approval.id}`)).data;
    check('quality: the approval history shows the current approval and its snapshot (sha256, findings, transcript)', (await page.locator(`[data-testid="p6-qc-approval-row"][data-approval="${rec.approval.id}"][data-current="1"]`).count()) === 1 && (await byTest(page, 'p6-qc-snapshot').getAttribute('data-sha')) === ap.snapshot_sha256 && (await page.locator('[data-testid="p6-qc-snapshot-results"] [data-criterion]').count()) === 10 && (await page.locator('[data-testid="p6-qc-snapshot"] [data-testid="p6-qc-transcript-view"]').count()) === 1);
    await shot(page, '56_qc_approval_snapshot_en');
    /* report download ar/en */
    for (const lang of ['ar', 'en']) {
      const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), page.locator(`[data-testid="p6-qc-report-${lang}"]`).first().click()]);
      const file = path.join(outDir, `qc-report-${lang}.html`);
      await dl.saveAs(file);
      const html = fs.readFileSync(file, 'utf8');
      const okLang = lang === 'ar' ? /lang="ar"/.test(html) && /dir="rtl"/.test(html) : /lang="en"/.test(html);
      check(`quality: report (${lang}) downloads as a file (${dl.suggestedFilename()}) stating the approval`, okLang && (lang === 'ar' ? html.includes('معتمد بعد مراجعة بشرية') : html.includes('APPROVED after human review')), dl.suggestedFilename());
    }
    /* hash state: reload keeps the recording and its section */
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    check('quality: reload keeps the open recording and section (#rec, #sec=approvals)', (await byTest(page, 'p6-qc-detail').getAttribute('data-recording')) === liveRec && (await byTest(page, 'p6-qc-approvals').count()) === 1, page.url());
    /* dashboard counts */
    const dashAfter = (await api(ADMIN, 'GET', '/v2/quality/dashboard')).data;
    await page.goto(FE + '/quality', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    const kpi = await page.locator('[data-testid="p6-qc-kpi-reviews"] [data-value]').getAttribute('data-value');
    const introRow = page.locator('[data-testid="p6-qc-dash-criterion-row"][data-criterion="intro"]');
    check('quality: the dashboard counts the approval (approved reviews +1; equals the API; intro met counted)', dashAfter.totals.reviews === dashBefore.totals.reviews + 1 && kpi === String(dashAfter.totals.reviews) && (await introRow.getAttribute('data-met')) === String(dashAfter.by_criterion.find((c) => c.criterion_id === 'intro').met), `${dashBefore.totals.reviews} -> ${dashAfter.totals.reviews}, kpi ${kpi}`);
    await shot(page, '57_qc_dashboard_en');
    /* filters in the hash: criterion + group, reload */
    await page.goto(FE + '/quality#crit=delivery&group=day', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const rowsNow = await page.$$eval('[data-testid="p6-qc-dash-criterion-row"]', (els) => els.map((e) => e.getAttribute('data-criterion')));
    check('quality: dashboard filters live in the hash (criterion=delivery, group=day)', JSON.stringify(rowsNow) === '["delivery"]' && (await page.locator('[data-testid="p6-qc-dash-group"] [data-value="day"]').getAttribute('aria-checked')) === 'true', JSON.stringify(rowsNow));
    /* a filter with no approved review: "—", never 0 % */
    await page.goto(FE + '/quality#from=2020-01-01&to=2020-01-02', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    check('quality: with nothing assessed the compliance is "—" with its explanation (never 0 %)', (await page.locator('[data-testid="p6-qc-kpi-compliance"] [data-percent]').getAttribute('data-percent')) === '' && (await page.locator('[data-testid="p6-qc-kpi-compliance"]').innerText()).includes('—'));
    await shot(page, '58_qc_dashboard_no_percentage_en');
    /* ar screenshots of the detail */
    await setLang(page, 'ar');
    for (const sec of ['evidence', 'transcript', 'review', 'approvals']) {
      await page.goto(`${FE}/quality#rec=${liveRec}&sec=${sec}`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(1800);
      await shot(page, `59_qc_${sec}_ar`);
    }
    await page.goto(FE + '/quality', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await shot(page, '60_qc_dashboard_ar');
    fs.unlinkSync(wav);
    check('quality: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- provider-dependent quality paths (stubs) */
  if (want('stubs')) {
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    const wav = makeWav(6, `stub-${RUN}`);
    const form = new FormData();
    form.append('file', new Blob([wav], { type: 'audio/wav' }), 'zz.wav');
    form.append('title', `ZZ-TEST phase 6 ${RUN} stubbed AI paths`);
    const imp = await api(ADMIN, 'POST', '/v2/quality/recordings', form, { raw: true });
    const sid = imp.data.recording.id;
    await api(ADMIN, 'PATCH', `/v2/quality/recordings/${sid}`, { expected_revision: 1, template: { key: 'collections-v1', version: 1 } });
    /* live refusal of the real POST (only the readiness is stubbed so the button can be pressed) */
    await stubQualityReady(page);
    await page.goto(`${FE}/quality#rec=${sid}&sec=transcript`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    const [tRes] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith(`/v2/quality/recordings/${sid}/transcribe`)),
      byTest(page, 'p6-qc-transcribe-run').click(),
    ]);
    await page.waitForTimeout(800);
    check('stubs: live POST transcribe -> the API refuses (503 ai_not_configured) and the refusal is shown', tRes.status() === 503 && (await page.locator('[data-testid="p6-qc-transcribe-error"] ul').getAttribute('data-code')) === 'ai_not_configured', tRes.status());
    await shot(page, '70_qc_transcribe_refused_en');
    /* stubbed transcription success: the documented body (the whole recording, a provider version, anonymous speakers) */
    const base = (await api(ADMIN, 'GET', `/v2/quality/recordings/${sid}`)).data;
    const segs = [
      { id: 's1', start: 0.0, end: 2.4, speaker: 'A', text: 'Good afternoon, this is the collections team calling about your account.' },
      { id: 's2', start: 2.4, end: 4.1, speaker: 'B', text: 'Yes, go ahead.' },
      { id: 's3', start: 4.1, end: 5.9, speaker: 'A', text: 'Can you confirm the last four digits of your identity number?' },
    ];
    const transcript = {
      id: crypto.randomUUID(), version: 1, source: 'provider', based_on_id: null, language: 'en', diarized: true, model: 'stub-transcribe-diarize',
      provider_call_id: crypto.randomUUID(), text: segs.map((s) => s.text).join('\n'), segments: segs, segment_count: 3, speakers: {}, speaker_labels: ['A', 'B'],
      text_sha256: crypto.createHash('sha256').update('x').digest('hex'), reason: null, purged: false, purged_at: null, created_by: { id: ADMIN_ID, name: 'Aidi Dev' }, created_at: new Date().toISOString(),
    };
    const transcribed = { ...base, revision: base.revision + 1, status: 'transcribed', transcript, history: { ...base.history, transcripts: 1 } };
    await page.route((u) => u.pathname === `/v2/quality/recordings/${sid}/transcribe`, (route) => route.fulfill({ status: 200, json: { message: 'transcribed', status: true, data: transcribed } }));
    await byTest(page, 'p6-qc-transcribe-run').click();
    await page.waitForTimeout(1500);
    const unverified = await page.locator('[data-testid="p6-qc-transcript-view"]').innerText();
    check('stubs: a (stubbed) diarized transcription is shown with anonymous speakers A/B marked "not verified"', (await page.locator('[data-testid="p6-qc-transcript-view"] [data-segment]').count()) === 3 && /A[\s\S]*not verified/.test(unverified), unverified.slice(0, 120));
    await shot(page, '71_qc_transcribed_stub_en');
    /* stubbed AI review draft: repaired items explained, not approvable */
    const tpl = (await api(ADMIN, 'GET', '/v2/quality/templates/collections-v1/1')).data;
    const results = tpl.criteria.map((c) => {
      if (c.id === 'intro') return { criterion_id: 'intro', result: 'met', reason: 'The agent introduced the team.', evidence: [{ type: 'quote', text: 'this is the collections team', segment_ids: ['s1'], start: 0, end: 2.4 }], ai_result: 'met', validation: [] };
      if (c.id === 'identity') return { criterion_id: 'identity', result: 'insufficient_evidence', reason: 'Verification was asked (The AI finding had no valid transcript quotation, so it is insufficient evidence.)', evidence: [], ai_result: 'met', validation: ['evidence_required', 'quote_not_found'] };
      if (c.id === 'delivery') return { criterion_id: 'delivery', result: 'insufficient_evidence', reason: 'Human listening only: a reviewer must listen and cite an audio timestamp.', evidence: [], ai_result: 'met', validation: ['human_listening_only'] };
      return { criterion_id: c.id, result: 'insufficient_evidence', reason: 'The AI draft did not return this criterion.', evidence: [], ai_result: null, validation: ['not_returned'] };
    });
    const m = { met: 1, needs_improvement: 0, not_applicable: 0, insufficient_evidence: 9, total: 10, assessed: 1, has_percentage: true, compliance: { numerator: 1, denominator: 1, ratio: 1, percent: 100 }, coverage: { numerator: 1, denominator: 10, ratio: 0.1, percent: 10 } };
    const review = {
      id: crypto.randomUUID(), version: 1, origin: 'ai', status: 'ai_draft', based_on_id: null, recording_revision: transcribed.revision + 1, template: { key: 'collections-v1', version: 1 }, transcript_id: transcript.id,
      results, summary: 'Stubbed AI draft summary.', coaching: null, language: 'en', metrics: m, model: 'stub-text-model', provider_call_id: crypto.randomUUID(),
      validation: results.filter((r) => r.validation.length).map((r) => ({ criterion_id: r.criterion_id, codes: r.validation })), created_by: { id: ADMIN_ID, name: 'Aidi Dev' }, created_at: new Date().toISOString(),
    };
    const drafted = { ...transcribed, revision: transcribed.revision + 1, status: 'ai_draft', review, history: { ...transcribed.history, reviews: 1 } };
    await page.route((u) => u.pathname === `/v2/quality/recordings/${sid}/draft`, (route) => route.fulfill({ status: 200, json: { message: 'AI draft saved; a human must review every criterion', status: true, data: drafted } }));
    await page.click('[data-testid="p6-qc-sections"] [data-tab="review"]');
    await page.waitForTimeout(1200);
    const [dReq] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith(`/v2/quality/recordings/${sid}/draft`)),
      byTest(page, 'p6-qc-ai-draft-run').click(),
    ]);
    await page.waitForTimeout(1500);
    const idItem = page.locator('[data-testid="p6-qc-criterion"][data-criterion="identity"] [data-testid="p6-qc-ai-item"]');
    check('stubs: the AI review draft request is {expected_revision, language}', typeof JSON.parse(dReq.postData()).expected_revision === 'number' && JSON.parse(dReq.postData()).language === 'en');
    check('stubs: a (stubbed) AI review draft: "insufficient evidence" items explain why (AI said met; no verbatim quotation), the delivery item says "human listening only"', /The AI draft said/.test(await idItem.innerText()) && ((await idItem.locator('ul').getAttribute('data-codes')) || '').includes('quote_not_found') && /Human listening only/.test(await page.locator('[data-testid="p6-qc-criterion"][data-criterion="delivery"] [data-testid="p6-qc-ai-item"]').innerText()));
    check('stubs: an AI draft cannot be approved (Approve disabled, "only a review saved by a person")', (await off(byTest(page, 'p6-qc-approve'))) && (await byTest(page, 'p6-qc-review-ai-notice').count()) === 1);
    await shot(page, '72_qc_ai_draft_stub_en');
    await setLang(page, 'ar');
    await page.waitForTimeout(800);
    await shot(page, '73_qc_ai_draft_stub_ar');
    check('stubs: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------------------------------------------------------- add call recording to QC */
  if (want('fromcall')) {
    const callId = (await api(ADMIN, 'GET', `/v2/cases/${CASE3}/calls?page_size=5`)).data?.items?.find((c) => c.mode !== 'manual')?.id;
    const FILE = '/var/spool/asterisk/monitor/2026/09/24/zz-test-phase6.wav';
    const stubEvidence = (page) =>
      page.route((u) => u.pathname === `/v2/calls/${callId}/pbx-evidence`, (route) =>
        patchJson(route, (b) => {
          b.data.recordings = [{ has_recording: true, file: FILE, url: null }];
        })
      );
    const r0 = await api(ADMIN, 'POST', '/v2/quality/recordings/from-call', { attempt_id: callId });
    const r1 = await api(ADMIN, 'POST', '/v2/quality/recordings/from-call', { attempt_id: crypto.randomUUID(), confirmed: true });
    const r2 = await api(ADMIN, 'POST', '/v2/quality/recordings/from-call', { attempt_id: callId, confirmed: true });
    check('fromcall: API refusals: no confirmation -> 422 confirmation_required; a call outside the scope (or unknown) -> 404 call_not_found; a call without a recording -> 404 recording_not_found', r0.status === 422 && r0.code === 'confirmation_required' && r1.status === 404 && r1.code === 'call_not_found' && r2.status === 404 && r2.code === 'recording_not_found', `${r0.status}/${r0.code} ${r1.status}/${r1.code} ${r2.status}/${r2.code}`);
    /* the UI: the recording listed (PBX evidence stubbed), the confirmed request goes to the real API */
    const { page, errors } = await newPage();
    await login(page);
    await setLang(page, 'en');
    await stubEvidence(page);
    await page.goto(`${FE}/calls#scope=team&call=${callId}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    const btn = byTest(page, 'p6-qc-add-call');
    check('fromcall: the call detail offers "Add to quality review" next to the recording (recording key + quality_review)', (await btn.count()) === 1 && (await btn.getAttribute('data-enabled')) === '1');
    await btn.click();
    check('fromcall: the dialog explains the copy and needs the explicit confirmation', (await page.locator('[data-testid="p6-qc-add-call-dialog-confirm"]').getAttribute('aria-disabled')) === 'true');
    await byTest(page, 'p6-qc-add-call-confirm').check();
    const [fc] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/v2/quality/recordings/from-call')),
      byTest(page, 'p6-qc-add-call-dialog-confirm').click(),
    ]);
    const fcReq = JSON.parse(fc.request().postData() || '{}');
    await page.waitForTimeout(800);
    const msg = await page.locator('[data-testid="p6-qc-add-call-dialog"] [data-testid="p1-inline-error"]').innerText().catch(() => '');
    check('fromcall: the confirmed request {attempt_id, recording_path, confirmed: true} is refused by the live API (404 recording_not_found: not a recording this call is known to have) and the dialog says so', fc.status() === 404 && fcReq.confirmed === true && fcReq.recording_path === FILE && fcReq.attempt_id === callId && /not available/.test(msg), `${fc.status()} ${msg}`);
    await shot(page, '80_fromcall_refused_en');
    /* a stubbed success: the documented 201 body -> a link into the quality workspace */
    const any = (await api(ADMIN, 'GET', '/v2/quality/recordings?page_size=1')).data.items[0];
    const recBody = (await api(ADMIN, 'GET', `/v2/quality/recordings/${any.id}`)).data;
    await page.route((u) => u.pathname === '/v2/quality/recordings/from-call', (route) => route.fulfill({ status: 201, json: { message: 'call recording added to QC', status: true, data: { duplicate: false, recording: recBody } } }));
    await byTest(page, 'p6-qc-add-call-dialog-confirm').click();
    await page.waitForTimeout(1000);
    check('fromcall: (stubbed 201) the button becomes a link to the recording in the quality workspace', (await page.locator(`[data-testid="p6-qc-added"][data-recording="${any.id}"] a[href*="/quality#rec=${any.id}"]`).count()) === 1);
    check('fromcall: no page errors', errors.length === 0, errors.join(' | '));
    await page.context().close();
    /* without a recording key: disabled with the reason (UI stub; the API also answers 403) */
    const { page: p2 } = await newPage();
    await login(p2, ADMIN, { permissions: { dashbord: true, clints: true, clints_details: true, calls: true, quality_review: true, clints_callsRecords: true } });
    await setLang(p2, 'en');
    await stubEvidence(p2);
    await p2.goto(`${FE}/calls#scope=team&call=${callId}`, { waitUntil: 'networkidle' });
    await p2.waitForTimeout(2500);
    check('fromcall: without a recording key the button is disabled and says why ("Needs a recording permission"); nothing is sent', (await p2.locator('[data-testid="p6-qc-add-call"][data-enabled="0"]').count()) === 1 && (await byTest(p2, 'p6-qc-add-call-nokey').count()) === 1);
    await shot(p2, '81_fromcall_no_recording_key_en');
    await p2.context().close();
  }

  /* ---------------------------------------------------------------- leave the test policy suspended */
  if (want('finish')) {
    const cur = (await api(ADMIN, 'GET', '/v2/ai/policy')).data;
    const r = await api(ADMIN, 'PUT', '/v2/ai/policy', {
      expected_revision: cur.revision,
      state: 'suspended',
      provider: 'openai',
      allowed_categories: ['case_facts', 'notes', 'transcripts', 'audio'],
      masking: { name: true, identity_number: true, phone: true, account: true },
      retention_days: { transcripts: 3650, audio: 3650, drafts: 3650, provider_logs: 3650 },
      reason: `ZZ-TEST phase 6 frontend verification ${RUN}: test policy SUSPENDED after the run (no provider key here; nothing may be sent; 3650-day retention so nothing is purged)`,
      confirmed: true,
    });
    const st = (await api(ADMIN, 'GET', '/v2/ai/status')).data;
    check('finish: the test policy is left SUSPENDED (a new revision): every provider call stays refused (ai_policy_required), retention 3650 days', r.status === 200 && r.data.policy.state === 'suspended' && st.reasons.includes('ai_policy_required'), `${r.status} rev ${r.data?.revision}`);
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
    check('no request left the machine (only the app and the API were contacted)', externalRequests.length === 0, externalRequests.slice(0, 5).join(' | '));
    const passed = results.filter((r) => r.ok).length;
    log(`\n${passed}/${results.length} checks passed`);
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify({ run: RUN, fe: FE, api: API, passed, total: results.length, results }, null, 2));
    fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
    process.exit(passed === results.length ? 0 : 1);
  }
})();
