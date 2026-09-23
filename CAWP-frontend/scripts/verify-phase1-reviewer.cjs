/*
 * Case corrections reviewed by a second user (decision 2026-09-23).
 *
 *   node CAWP-frontend/scripts/verify-phase1-reviewer.cjs [outDir]      (from old/)
 *
 * aidi.dev requests two renames of the same test case. As the local-only reviewer
 * aidi.reviewer, in the UI: approve the first (the case is renamed), then the second is
 * stale: approving it shows the conflict with expected/current values, and it is
 * rejected with a note. The case name is restored at the end.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '../../tools/node_modules/playwright-core'));
const { check, api, login, setLang, settle, shot, FE, results } = require('./verify-phase1.cjs');

const REVIEWER = 'aidi.reviewer';
const CASE2 = '527b4757-aef0-455c-af8e-05ab84b746cb'; // ZZTEST-P1-0002
const outDir = process.argv[2] || 'logs/screens/phase1-reviewer';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
fs.mkdirSync(outDir, { recursive: true });

const caseName = async () => {
  const r = await api('aidi.dev', 'GET', `/clints/${CASE2}`);
  return (r.body.clint || r.body).name;
};
const request = async (name, reason) => {
  const r = await api('aidi.dev', 'POST', `/v2/cases/${CASE2}/corrections`, {
    kind: 'profile', changes: { name }, reason,
  });
  if (r.status !== 201) throw new Error('request failed ' + JSON.stringify(r));
  return r.body.data.id;
};
const status = async (id) => (await api(REVIEWER, 'GET', `/v2/corrections/${id}`)).body.data.status;

(async () => {
  const original = await caseName();
  const stamp = Date.now().toString(36);
  const first = await request(`${original} [${stamp}-a]`, `reviewer UI test ${stamp} a`);
  const second = await request(`${original} [${stamp}-b]`, `reviewer UI test ${stamp} b`);

  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  try {
    await login(page, REVIEWER);
    await setLang(page, 'en');

    // 1) approve the first request
    await page.goto(`${FE}/corrections#status=pending&sel=${first}`, { waitUntil: 'networkidle' });
    await settle(page);
    const panel = page.getByTestId('p1-corr-panel');
    check('reviewer: correction panel opens from the hash', await panel.isVisible());
    check('reviewer: approve is offered on someone else\'s request', await page.getByTestId('p1-corr-approve').isVisible());
    await page.getByTestId('p1-corr-approve').click();
    await page.getByTestId('p1-corr-approve-dialog-note').fill('checked against the contract');
    await page.getByTestId('p1-corr-approve-dialog-confirm').click();
    await settle(page);
    check('reviewer: first request approved', (await status(first)) === 'approved', await status(first));
    check('reviewer: the case was renamed', (await caseName()) === `${original} [${stamp}-a]`, await caseName());
    await shot(page, '01_reviewer_approved_en');

    // 2) the second request is now stale
    await page.goto(`${FE}/corrections#status=pending&sel=${second}`, { waitUntil: 'networkidle' });
    await settle(page);
    await shot(page, '02_reviewer_stale_live_en');
    await page.getByTestId('p1-corr-approve').click();
    await page.getByTestId('p1-corr-approve-dialog-confirm').click();
    await settle(page);
    const staleShown = await page.getByTestId('p1-corr-stale-fields').isVisible();
    check('reviewer: approving a stale request shows the conflict', staleShown);
    check('reviewer: the stale request stays pending', (await status(second)) === 'pending', await status(second));
    await shot(page, '03_reviewer_stale_conflict_en');

    // 3) reject it with a note
    await page.getByTestId('p1-corr-stale-reject').click();
    const reason = page.getByTestId('p1-corr-reject-dialog-reason');
    await reason.fill('stale: the case changed after this request');
    await page.getByTestId('p1-corr-reject-dialog-confirm').click();
    await settle(page);
    check('reviewer: stale request rejected with a note', (await status(second)) === 'rejected', await status(second));
    await shot(page, '04_reviewer_rejected_en');

    check('reviewer: no page errors', errors.length === 0, errors.join(' | '));
  } catch (e) {
    check('reviewer script finished without an exception', false, e.stack || String(e));
  } finally {
    const r = await api('aidi.dev', 'PUT', `/clints/${CASE2}`, { name: original });
    check('test case name restored', r.status === 200 && (await caseName()) === original);
    await browser.close();
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify(results, null, 1));
    const failed = results.filter((x) => !x.ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks pass`);
    process.exit(failed ? 1 : 0);
  }
})();
