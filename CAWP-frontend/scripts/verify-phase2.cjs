/* Phase 2 frontend checks (reviewed bulk jobs), driven by headless Chrome through the
 * playwright-core install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase2.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots go to outDir (default logs/screens/phase2), plus verify.json and a log.
 * ONLY=<section,...> runs a subset: screens, lockunlock, filtercount, assign, archive,
 * notes, inquiry, attr, closeout, roundrobin, jobs, conflict, gating.
 *
 * Every job runs on the synthetic cases ZZTEST-P1-0001…0004 (portfolio "ZZ-TEST Phase1
 * frontend 2026-09-23") or on files this script generates. The follow-up close-out is
 * previewed and cancelled, never confirmed: a network guard aborts (and fails the run
 * on) any confirm request for a followup_closeout job.
 *
 * Reuses the login / API / language helpers of verify-phase1.cjs.
 */
const path = require('path');
const fs = require('fs');
const os = require('os');

const outDir = process.argv[2] || 'logs/screens/phase2';
// verify-phase1.cjs reads process.argv[2] as its outDir at load; it is the same directory.
const H = require('./verify-phase1.cjs');
const { chromium } = require(path.join(__dirname, '../../tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const ADMIN = 'aidi.dev';
const REVIEWER = 'aidi.reviewer';
const COLLECTOR = 'aidi.collector';
const PORTFOLIO = 'de606dd9-f0bf-4f6d-98eb-4e26d96f41f5'; // ZZ-TEST Phase1 frontend 2026-09-23
const CONTRACT = 'bf69e026-5a52-446c-99a6-b596f886dbf0'; // its contract
const CASES = {
  1: { id: '44f5da48-0cf4-4686-a538-380c14c06ae3', acc: 'ZZTEST-P1-0001' },
  2: { id: '527b4757-aef0-455c-af8e-05ab84b746cb', acc: 'ZZTEST-P1-0002' },
  3: { id: 'e1afce87-41d5-461a-bd9b-79beab1f15d3', acc: 'ZZTEST-P1-0003' },
  4: { id: 'a206f02b-2ccc-4e06-8ed6-9f06ef796732', acc: 'ZZTEST-P1-0004' },
};
// The in-scope collector the lead's live tests used for ZZ-TEST assignments.
const OTHER_COLLECTOR = { id: 'ad0d801b-78ba-42d3-a57a-6e7f8b3f86b2', login: 'rasha.s' };

fs.mkdirSync(outDir, { recursive: true });
const logLines = [];
const origLog = console.log;
console.log = (...a) => {
  logLines.push(a.join(' '));
  origLog(...a);
};
const { check, api, results } = H;

/* ------------------------------------------------------------------ API helpers */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TERMINAL = ['succeeded', 'partially_succeeded', 'failed', 'cancelled', 'expired'];

async function jobOf(user, id) {
  return (await api(user, 'GET', `/v2/bulk/jobs/${id}`)).body.data;
}
async function waitJob(user, id, statuses, timeout = 60000) {
  const t0 = Date.now();
  for (;;) {
    const j = await jobOf(user, id);
    if (statuses.includes(j.status)) return j;
    if (Date.now() - t0 > timeout) throw new Error(`job ${id} stuck in ${j.status}`);
    await sleep(800);
  }
}
/** Create → ready → confirm (accept_partial) → terminal, all through the API (setup / cleanup only). */
async function apiRun(user, kind, body) {
  const c = await api(user, 'POST', `/v2/bulk/${kind}`, body);
  if (c.status !== 201) throw new Error(`create ${kind}: ${c.status} ${JSON.stringify(c.body)}`);
  if (kind === 'followup_closeout') throw new Error('refusing to confirm a close-out');
  const ready = await waitJob(user, c.body.data.id, ['ready', ...TERMINAL]);
  if (ready.status !== 'ready') return ready;
  if (ready.counts.eligible === 0) {
    await api(user, 'POST', `/v2/bulk/jobs/${ready.id}/cancel`, { reason: 'verify-phase2 setup: nothing to do' });
    return ready;
  }
  const cf = await api(user, 'POST', `/v2/bulk/jobs/${ready.id}/confirm`, { expected_revision: ready.revision, accept_partial: true });
  if (cf.status !== 200) throw new Error(`confirm ${kind}: ${cf.status} ${JSON.stringify(cf.body)}`);
  return waitJob(user, ready.id, TERMINAL);
}
async function caseOf(id) {
  const r = await api(ADMIN, 'GET', `/clints/${id}`);
  return r.body.clint || r.body;
}

/* ------------------------------------------------------------------- UI helpers */
const settle = async (page, ms = 900) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
};
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: true });
const byTest = (page, id) => page.locator(`[data-testid="${id}"]`);

/** Open a react-select inside the element with data-testid=`testId` and pick an option (by text, or the first). */
async function pickIn(page, testId, text) {
  const scope = byTest(page, testId).first();
  await scope.locator('.select__control').first().click();
  const opts = page.locator('.select__menu .select-option');
  await opts.first().waitFor({ timeout: 15000 });
  if (text) await opts.filter({ hasText: text }).first().click();
  else await opts.first().click();
}
async function openMenuEntry(page, menuTestId, entryTestId) {
  await byTest(page, `${menuTestId}-toggle`).first().click();
  await byTest(page, entryTestId).first().click();
}
async function clientsList(page) {
  await page.goto(`${FE}/clients#portfolio=${PORTFOLIO}`, { waitUntil: 'networkidle' });
  await page.locator('tr', { hasText: CASES[1].acc }).first().waitFor({ timeout: 30000 });
  await settle(page, 600);
}
async function tick(page, acc) {
  const row = page.locator('tbody tr', { hasText: acc }).first();
  await row.locator('input[type="checkbox"]').first().check();
}
async function waitStage(page, stages, timeout = 60000) {
  const list = Array.isArray(stages) ? stages : [stages];
  await page.waitForFunction(
    (s) => {
      const el = document.querySelector('[data-testid="p2-run"]');
      return !!el && s.includes(el.getAttribute('data-stage'));
    },
    list,
    { timeout }
  );
  await page.waitForTimeout(700);
  return page.locator('[data-testid="p2-run"]').getAttribute('data-stage');
}
const runJobId = (page) => page.locator('[data-testid="p2-run"]').getAttribute('data-job');
async function countOf(page, testId) {
  const v = await byTest(page, testId).first().getAttribute('data-value');
  return v === null || v === '' ? null : Number(v);
}
/** From the run page's preview: open the confirm step, tick "accept partial" when shown, confirm, wait for results. */
async function confirmThroughUi(page, { partial = false, shotName } = {}) {
  await byTest(page, 'p2-to-confirm').click();
  await waitStage(page, 'confirm');
  const btn = byTest(page, 'p2-confirm-btn');
  let disabledBefore = null;
  if (partial) {
    disabledBefore = (await btn.getAttribute('aria-disabled')) === 'true';
    await byTest(page, 'p2-accept-partial').locator('input[type="checkbox"]').check();
  }
  if (shotName) await shot(page, shotName);
  await btn.click();
  await waitStage(page, 'results', 90000);
  return { disabledBefore };
}
function tmpFile(name, content) {
  const p = path.join(os.tmpdir(), `p2-${Date.now().toString(36)}-${name}`);
  fs.writeFileSync(p, content);
  return p;
}
async function uploadFile(page, file) {
  await byTest(page, 'p2-upload-input').setInputFiles(file);
  await byTest(page, 'p2-inspection').waitFor({ timeout: 30000 });
  await page.waitForTimeout(400);
}
const riyadhDate = (plusDays = 0) => new Date(Date.now() + 3 * 3600 * 1000 + plusDays * 86400000).toISOString().slice(0, 10);

module.exports = {
  FE, API, ADMIN, REVIEWER, COLLECTOR, PORTFOLIO, CONTRACT, CASES, OTHER_COLLECTOR, outDir, want,
  check, api, results, sleep, jobOf, waitJob, apiRun, caseOf,
  settle, shot, byTest, pickIn, openMenuEntry, clientsList, tick, waitStage, runJobId, countOf,
  confirmThroughUi, tmpFile, uploadFile, riyadhDate, login: H.login, setLang: H.setLang,
};

if (require.main === module) {
  (async () => {
    const browser = await chromium.launch({ executablePath: CHROME, headless: true });
    const closeoutJobs = new Set();
    const guard = { violations: [] };
    const newPage = async (opts = {}) => {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true, ...opts });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
      page.on('response', (r) => {
        if (r.url().startsWith(API + '/v2/') && r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
        // remember every close-out job this browser creates
        if (r.url() === `${API}/v2/bulk/followup_closeout` && r.status() === 201) {
          r.json().then((j) => closeoutJobs.add(j.data.id)).catch(() => {});
        }
      });
      /* Safety: the close-out must never be confirmed from this script. */
      await page.route('**/v2/bulk/jobs/*/confirm', async (route) => {
        const id = route.request().url().split('/v2/bulk/jobs/')[1].split('/')[0];
        let kind = null;
        try {
          kind = (await jobOf(ADMIN, id)).kind;
        } catch {
          /* not visible to the admin: not a close-out created here */
        }
        if (closeoutJobs.has(id) || kind === 'followup_closeout') {
          guard.violations.push(id);
          return route.abort();
        }
        return route.fallback();
      });
      return { ctx, page, errors };
    };
    const flows = require('./verify-phase2-flows.cjs');
    try {
      await flows.run({ browser, newPage, guard });
    } catch (e) {
      check('script finished without an exception', false, e.stack || String(e));
    }
    check('no close-out confirm was ever sent', guard.violations.length === 0, guard.violations.join(','));
    await browser.close();
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify(results, null, 1));
    const failed = results.filter((r) => !r.ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks pass`);
    fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
    process.exit(failed ? 1 : 0);
  })();
}
