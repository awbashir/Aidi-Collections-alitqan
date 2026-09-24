/* Phase 0 shell checks, driven by headless Chrome through the playwright-core
 * install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase0.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177). Writes screenshots into outDir
 * (default logs/screens/phase0-after) and a JSON summary to outDir/verify.json.
 * Exits non-zero when a check fails.
 *
 * MODE=parity only records the /clints requests of a fixed interaction script
 * (plain load, page 2, sort, filter panel search) so two builds can be diffed.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '../../tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const outDir = process.argv[2] || 'logs/screens/phase0-after';
const MODE = process.env.MODE || 'full';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const USER = 'aidi.dev';
const PASS = 'Aidi@2026';
const CONTRACT = 'bf69e026-5a52-446c-99a6-b596f886dbf0';
const ARABIC = 'الشمري';

fs.mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
};

/** Records GET /clints (the clients list request) as decoded query pairs. */
function recordClints(page) {
  const log = [];
  page.on('request', (req) => {
    const u = new URL(req.url());
    if (req.method() === 'GET' && u.pathname === '/clints') {
      log.push([...u.searchParams.entries()].map(([k, v]) => `${k}=${v}`).join('&'));
    }
  });
  return log;
}

async function login(page, { permissions } = {}) {
  if (permissions) {
    // Replace the role's permission map in the login response (UI-only test).
    await page.route('**/login', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (body?.user?.role) body.user.role.permission = permissions;
      await route.fulfill({ response: res, json: body });
    });
    /* Phase 3: the app re-reads the permissions from GET /v2/me after sign-in (and on
     * focus), so the fake role must be served there too or the real one replaces it. */
    await page.route('**/v2/me', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      if (body?.data) body.data.permissions = permissions;
      await route.fulfill({ response: res, json: body });
    });
  }
  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/home', { timeout: 30000 });
}

const settle = async (page, ms = 1200) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
};

const hashOf = (page) => page.evaluate(() => decodeURIComponent(location.hash));
const histLen = (page) => page.evaluate(() => history.length);
const groupLabels = (page, scope = '.side-nav') =>
  page.$$eval(`${scope} .menu-nav-group > .menu-title`, (els) => els.map((e) => e.textContent.trim()));

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const newPage = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ar', ...opts });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
    return { ctx, page, errors };
  };

  /* ---------------- request parity script ---------------- */
  if (MODE === 'parity') {
    const { page } = await newPage();
    await login(page);
    const log = recordClints(page);
    await page.goto(FE + '/clients', { waitUntil: 'networkidle' });
    await settle(page);
    await page.locator('.pagination li', { hasText: /^2$/ }).first().click();
    await settle(page);
    await page.locator('th div.cursor-pointer', { hasText: 'رقم الحساب' }).first().click();
    await settle(page);
    await page.getByRole('button', { name: 'بحث' }).first().click();
    await page.locator('input[name="TextField"][type="text"]').first().fill(ARABIC);
    await page.waitForTimeout(300);
    await page.locator('button', { hasText: /^بحث$/ }).first().click();
    await settle(page);
    fs.writeFileSync(path.join(outDir, 'parity.json'), JSON.stringify(log, null, 1));
    console.log(JSON.stringify(log, null, 1));
    await browser.close();
    return;
  }

  /* ---------------- 1. sidebar groups, AR and EN ---------------- */
  {
    const { page, errors } = await newPage();
    await login(page);
    await settle(page);
    const ar = await groupLabels(page);
    check('sidebar AR: three groups in order', JSON.stringify(ar) === JSON.stringify(['العمليات اليومية', 'التحصيل والفريق', 'الذكاء والتحكم']), JSON.stringify(ar));
    const arItems = await page.$$eval('.side-nav .menu-nav-group', (gs) => gs.map((g) => [...g.querySelectorAll('.menu-item, .menu-collapse-item')].filter((el) => !el.parentElement.closest('.menu-collapse > ul, .menu-collapse > div:not(.menu-collapse-item)')).length));
    check('sidebar AR: item count per group', JSON.stringify(arItems) === JSON.stringify([9, 13, 11]), JSON.stringify(arItems)); // Phase 1 added attention + follow-ups, promises + approvals, corrections; Phase 2 added Bulk jobs (/jobs); Phase 3 added plans, team, requests; Phase 4 added the Reports hub (/reports); Phase 5 added calls, calling workspace, campaigns (Daily operations) and call review, calling admin (Intelligence & control)
    const dir = await page.evaluate(() => document.documentElement.dir);
    check('document is RTL in Arabic', dir === 'rtl', dir);

    await page.locator('.header img[src*="/img/countries/"]').first().click();
    await page.getByText('English', { exact: true }).first().click();
    await settle(page, 800);
    const en = await groupLabels(page);
    check('sidebar EN: three groups in order', JSON.stringify(en.map((s) => s.toLowerCase())) === JSON.stringify(['daily operations', 'recovery & team', 'intelligence & control']), JSON.stringify(en));
    const dirEn = await page.evaluate(() => document.documentElement.dir);
    check('document is LTR in English', dirEn === 'ltr', dirEn);
    const tt = await page.$eval('.side-nav .menu-nav-group > .menu-title', (e) => getComputedStyle(e).textTransform);
    check('EN group label is micro caps', tt === 'uppercase', tt);

    // Collapsed rail (LTR): no labels, hairline dividers between groups, items still link.
    await page.locator('.header-action-start .header-action-item').last().click();
    await settle(page, 600);
    const collapsedLabels = await groupLabels(page);
    const dividers = await page.$$eval('.side-nav .menu-nav-group-divider', (d) => d.length);
    check('collapsed LTR: no group labels, 2 dividers', collapsedLabels.length === 0 && dividers === 2, `labels=${collapsedLabels.length} dividers=${dividers}`);
    await page.screenshot({ path: path.join(outDir, 'v01_sidebar_collapsed_en.png') }); // viewport: the collapsed rail spans the full page height
    await page.locator('.side-nav a[href="/clients"]').first().click();
    await page.waitForURL('**/clients**');
    check('collapsed LTR: clients link navigates', page.url().includes('/clients'), page.url());

    // Back to Arabic, still collapsed.
    await page.locator('.header img[src*="/img/countries/"]').first().click();
    await page.getByText('العربية', { exact: true }).first().click();
    await settle(page, 800);
    const dividersAr = await page.$$eval('.side-nav .menu-nav-group-divider', (d) => d.length);
    check('collapsed RTL: 2 dividers', dividersAr === 2, `dividers=${dividersAr}`);
    await page.screenshot({ path: path.join(outDir, 'v02_sidebar_collapsed_ar.png') }); // viewport: the collapsed rail spans the full page height
    await page.locator('.header-action-start .header-action-item').last().click();
    await settle(page, 400);
    check('no page errors (sidebar run)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 2. mobile drawer, RTL and LTR ---------------- */
  {
    const { page, errors } = await newPage({ viewport: { width: 390, height: 844 } });
    await login(page);
    await settle(page);
    await page.locator('.header-action-start .header-action-item').first().click();
    await page.waitForSelector('.drawer-content .menu-nav-group');
    await page.waitForTimeout(500);
    const labels = await groupLabels(page, '.drawer-content');
    const box = await page.locator('.drawer-content').boundingBox();
    check('mobile RTL: drawer shows 3 groups', labels.length === 3, JSON.stringify(labels));
    check('mobile RTL: drawer opens from the right', box && box.x + box.width >= 389, JSON.stringify(box));
    await page.screenshot({ path: path.join(outDir, 'v03_mobile_drawer_ar.png') });
    await page.locator('.drawer-content a[href="/clients"]').first().click();
    await page.waitForURL('**/clients**');
    await page.waitForTimeout(600);
    const stillOpen = await page.locator('.drawer-content').isVisible().catch(() => false);
    check('mobile RTL: navigating closes the drawer', !stillOpen);

    await page.locator('.header img[src*="/img/countries/"]').first().click();
    await page.getByText('English', { exact: true }).first().click();
    await settle(page, 800);
    await page.locator('.header-action-start .header-action-item').first().click();
    await page.waitForSelector('.drawer-content .menu-nav-group');
    await page.waitForTimeout(500);
    const labelsEn = await groupLabels(page, '.drawer-content');
    const boxEn = await page.locator('.drawer-content').boundingBox();
    check('mobile LTR: drawer shows 3 groups', labelsEn.length === 3, JSON.stringify(labelsEn));
    check('mobile LTR: drawer opens from the left', boxEn && boxEn.x <= 1, JSON.stringify(boxEn));
    await page.screenshot({ path: path.join(outDir, 'v04_mobile_drawer_en.png') });
    check('no page errors (mobile run)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 3. permission-hidden groups ---------------- */
  for (const [name, perms, expected] of [
    /* Phase 2: client_query is the spreadsheet-inquiry bulk key, so this role also sees
     * Bulk jobs (/jobs, which mirrors GET /v2/jobs access) under Intelligence & control. */
    ['collector-like role', { dashbord: true, clints: true, client_query: true, clints_archive: true, report_collectors: true, report_collectors_currentUser_comments: true }, ['العمليات اليومية', 'التحصيل والفريق', 'الذكاء والتحكم']],
    ['home-only role', { dashbord: true }, ['العمليات اليومية']],
    /* Phase 4: `users` is also RPT-01's key (users roster), so this role now sees the
     * Reports hub (`reports_any`, mirroring GET /v2/reports) under Recovery & team. */
    ['admin-only role', { users: true, roles: true }, ['التحصيل والفريق', 'الذكاء والتحكم']],
  ]) {
    const { page } = await newPage();
    await login(page, { permissions: perms });
    await settle(page);
    const labels = await groupLabels(page);
    const emptyGroups = await page.$$eval('.side-nav .menu-nav-group', (gs) => gs.filter((g) => !g.querySelector('.menu-item, .menu-collapse-item')).length);
    check(`permissions (${name}): only non-empty groups render`, JSON.stringify(labels) === JSON.stringify(expected) && emptyGroups === 0, JSON.stringify(labels));
    if (name === 'collector-like role') await page.locator('.side-nav').screenshot({ path: path.join(outDir, 'v05_sidebar_collector_role.png') });
    await page.context().close();
  }

  /* ---------------- 4. clients list: hash state ---------------- */
  {
    const { page, errors } = await newPage();
    await login(page);
    const log = recordClints(page);

    await page.goto(FE + '/clients', { waitUntil: 'networkidle' });
    await settle(page);
    check('plain /clients: one list request', log.length === 1, JSON.stringify(log));
    check('plain /clients: empty hash', (await hashOf(page)) === '', await hashOf(page));

    const h0 = await histLen(page);
    await page.locator('.pagination li', { hasText: /^2$/ }).first().click();
    await settle(page);
    check('page 2 -> hash page=2 (push)', (await hashOf(page)) === '#page=2' && (await histLen(page)) === h0 + 1, `${await hashOf(page)} len ${h0}->${await histLen(page)}`);

    await page.locator('th div.cursor-pointer', { hasText: 'رقم الحساب' }).first().click();
    await settle(page);
    const hSort = await hashOf(page);
    check('sort click -> hash sort=accountNumber:asc', hSort === '#page=2&sort=accountNumber:asc', hSort);
    const sortedHeader = await page.locator('th', { hasText: 'رقم الحساب' }).first().locator('.text-azure-500').count();

    // Filter panel: typing must not touch history; searching pushes once.
    await page.getByRole('button', { name: 'بحث' }).first().click();
    const hBeforeTyping = await histLen(page);
    await page.locator('input[name="TextField"][type="text"]').first().pressSequentially(ARABIC, { delay: 40 });
    await page.waitForTimeout(700);
    check('typing in the filter field adds no history entries', (await histLen(page)) === hBeforeTyping && (await hashOf(page)) === hSort, `len ${hBeforeTyping}->${await histLen(page)}`);
    // The panel renders above the toolbar, so its search button comes first.
    await page.locator('button', { hasText: /^بحث$/ }).first().click();
    await settle(page);
    const hAttr = await hashOf(page);
    check('search -> attr in hash, Arabic intact, one push', hAttr.includes('attr=' + ARABIC) && (await histLen(page)) === hBeforeTyping + 1, hAttr);
    const rawHash = await page.evaluate(() => location.hash);
    check('Arabic is percent-encoded UTF-8 in the URL', rawHash.includes(encodeURIComponent(ARABIC)), rawHash);

    // Reload restores everything, with one request carrying the state.
    log.length = 0;
    await page.reload({ waitUntil: 'networkidle' });
    await settle(page);
    const r = log[0] || '';
    check('reload: exactly one list request', log.length === 1, JSON.stringify(log));
    check('reload: request carries page 2, sort, Arabic filter', r.includes('page=2') && r.includes('field=accountNumber') && r.includes('sort=asc') && r.includes('customeFilterAttributeValue=' + ARABIC), r);
    const activePager = await page.locator('.pagination li.pagination-pager:not(.pagination-pager-inactive)').first().textContent().catch(() => '');
    check('reload: pager shows page 2', (activePager || '').trim() === '2', activePager);
    const sortedAfterReload = await page.locator('th', { hasText: 'رقم الحساب' }).first().locator('.text-azure-500').count();
    check('reload: sort arrow shown on the restored column', sortedAfterReload === 1 && sortedHeader === 1, `${sortedHeader}/${sortedAfterReload}`);
    const chip = await page.getByText('فلاتر مطبقة').count();
    check('reload: "filters applied" chip visible', chip === 1);
    await page.screenshot({ path: path.join(outDir, 'v06_clients_after_reload.png') });

    // Back steps through the pushes: filter -> sort -> page -> plain.
    log.length = 0;
    await page.goBack();
    await settle(page);
    check('Back #1: filter removed, page 2 + sort kept', (await hashOf(page)) === hSort && log.length === 1 && !log[0].includes('customeFilterAttributeValue') && log[0].includes('page=2') && log[0].includes('field=accountNumber'), `${await hashOf(page)} ${JSON.stringify(log)}`);
    log.length = 0;
    await page.goBack();
    await settle(page);
    check('Back #2: sort removed, page 2 kept', (await hashOf(page)) === '#page=2' && log.length === 1 && log[0].includes('page=2') && log[0].includes('field=&'), `${await hashOf(page)} ${JSON.stringify(log)}`);
    log.length = 0;
    await page.goBack();
    await settle(page);
    check('Back #3: plain list, page 1', (await hashOf(page)) === '' && log.length === 1 && log[0].includes('page=1'), `${await hashOf(page)} ${JSON.stringify(log)}`);
    log.length = 0;
    await page.goForward();
    await settle(page);
    check('Forward: page 2 again', (await hashOf(page)) === '#page=2' && log.length === 1 && log[0].includes('page=2'), `${await hashOf(page)} ${JSON.stringify(log)}`);
    const activeAfterFwd = await page.locator('.pagination li.pagination-pager:not(.pagination-pager-inactive)').first().textContent().catch(() => '');
    check('Forward: pager shows page 2', (activeAfterFwd || '').trim() === '2', activeAfterFwd);

    // Selected case: opening a case writes case=<id> with replace, row is marked.
    const hl = await histLen(page);
    const detail = page.locator('a[href*="/clients/details/"]').first();
    const href = await detail.getAttribute('href');
    const caseId = new URL(FE + href).searchParams.get('id');
    const [popup] = await Promise.all([page.context().waitForEvent('page', { timeout: 15000 }).catch(() => null), detail.click()]);
    if (popup) await popup.close();
    await page.waitForTimeout(800);
    check('open case -> hash case=<id> via replace', (await hashOf(page)) === `#page=2&case=${caseId}` && (await histLen(page)) === hl, `${await hashOf(page)} len ${hl}->${await histLen(page)}`);
    log.length = 0;
    await page.reload({ waitUntil: 'networkidle' });
    await settle(page);
    const marked = await page.$$eval('tr[aria-current="true"]', (rows) => rows.length);
    check('reload: opened case row is marked', marked === 1, `marked=${marked}`);
    await page.screenshot({ path: path.join(outDir, 'v07_clients_case_marked.png') });
    check('no page errors (clients run)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 5. shared link (fresh session, signed in) ---------------- */
  const sharedHash = `#size=500&sort=name:desc&contract=${CONTRACT}&attr=${encodeURIComponent(ARABIC)}`;
  {
    const { page, errors } = await newPage();
    await login(page);
    const log = recordClints(page);
    await page.goto('about:blank');
    await page.goto(FE + '/clients' + sharedHash, { waitUntil: 'networkidle' });
    await settle(page);
    const r = log[0] || '';
    check('shared link: exactly one list request', log.length === 1, JSON.stringify(log));
    check('shared link: request has size 500, sort, contract, Arabic attr', r.includes('pageSize=500') && r.includes('field=name') && r.includes('sort=desc') && r.includes(`customeFilterContractValue[]=${CONTRACT}`) && r.includes('customeFilterAttributeValue=' + ARABIC), r);
    await page.screenshot({ path: path.join(outDir, 'v08_clients_shared_link.png') });
    check('no page errors (shared link)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 6. shared link through sign-in ---------------- */
  {
    const { page, errors } = await newPage();
    const log = recordClints(page);
    await page.goto(FE + '/clients' + sharedHash, { waitUntil: 'networkidle' });
    await page.waitForURL('**/sign-in**');
    await page.fill('input[name="username"]', USER);
    await page.fill('input[name="password"]', PASS);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/clients**', { timeout: 30000 }).catch(() => {});
    await settle(page);
    const r = log[log.length - 1] || '';
    check('signed-out shared link: lands on /clients with the hash', page.url().includes('/clients#') && r.includes('customeFilterAttributeValue=' + ARABIC), page.url());
    check('no page errors (sign-in redirect)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 7. malformed hash ---------------- */
  {
    const { page, errors } = await newPage();
    await login(page);
    const log = recordClints(page);
    await page.goto('about:blank');
    await page.goto(FE + '/clients#page=abc&size=7&sort=%3Cscript%3E:up&contract=%%%&from=2026-02-30&case=../x&attr=%E0%A4%A', { waitUntil: 'networkidle' });
    await settle(page);
    const r = log[0] || '';
    check('bad hash: screen renders, no page error', errors.length === 0 && (await page.locator('table').count()) > 0, errors.join(' | '));
    check('bad hash: defaults used (page 1, size 100, no filters)', log.length === 1 && r.includes('page=1') && r.includes('pageSize=100') && !r.includes('customeFilterContractValue') && !r.includes('customeFilterAttributeValue'), r);
    check('bad hash: URL cleaned', (await page.evaluate(() => location.hash)) === '', await page.evaluate(() => location.hash));
    await page.context().close();
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify({ at: new Date().toISOString(), fe: FE, passed: results.length - failed.length, failed: failed.length, results }, null, 1));
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(2);
});
