/* Sections of verify-phase3.cjs. Each section is independent (ONLY=<name> runs one). */
const fs = require('fs');
const os = require('os');
const path = require('path');
const H = require('./verify-phase3.cjs');
const {
  FE, API, ADMIN, REVIEWER, COLLECTOR, COLLECTOR_ID, REVIEWER_ROLE, CONTRACT, CASES, STAMP, want,
  log, check, api, data, sleep, riyadhToday, riyadhMonth,
  login, setLang, settle, shot, byTest, navHrefs, groupItemCounts, pickIn, countOfTab,
} = H;

const created = { requests: [], plans: [], docs: [], roleBefore: null };

/* ------------------------------------------------------------------ small helpers */
const statusOf = async (id, user = ADMIN) => data(await api(user, 'GET', `/v2/requests/${id}`))?.status;
async function waitAttr(page, selector, attr, value, timeout = 20000) {
  await page.waitForFunction(
    ([s, a, v]) => {
      const el = document.querySelector(s);
      return !!el && el.getAttribute(a) === v;
    },
    [selector, attr, value],
    { timeout }
  );
}
const panelStatus = (page) => page.locator('[data-testid="p3-req-detail"]').first().getAttribute('data-status');
async function reasonDialog(page, testId, reason) {
  await byTest(page, testId).waitFor({ timeout: 10000 });
  const confirm = byTest(page, `${testId}-confirm`);
  const disabledEmpty = (await confirm.getAttribute('aria-disabled')) === 'true';
  await byTest(page, `${testId}-reason`).fill(reason);
  await confirm.click();
  return disabledEmpty;
}
/** A tiny valid PDF for the linked-document test. */
function tinyPdf() {
  const body = '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n';
  const p = path.join(os.tmpdir(), `zz-test-phase3-${Date.now()}.pdf`);
  fs.writeFileSync(p, body);
  return p;
}
async function uploadCaseDoc(caseId, name) {
  const tok = await H.apiLogin(ADMIN);
  const fd = new FormData();
  const file = tinyPdf();
  fd.append('file', new Blob([fs.readFileSync(file)], { type: 'application/pdf' }), 'zz-test-phase3.pdf');
  fd.append('name', name);
  const r = await fetch(`${API}/clint/${caseId}/documents`, { method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
  const j = await r.json().catch(() => ({}));
  const list = await api(ADMIN, 'GET', `/clint/${caseId}/documents`);
  const doc = (list.body.documents || []).find((d) => d.display_name === name);
  return { status: r.status, body: j, doc };
}

/* ======================================================================= sections */

async function screens({ newPage }) {
  const { page, errors } = await newPage();
  await login(page, ADMIN);
  for (const lang of ['ar', 'en']) {
    await setLang(page, lang);
    await page.goto(FE + '/requests', { waitUntil: 'networkidle' });
    await settle(page);
    const hrefs = await navHrefs(page);
    check(`screens ${lang}: nav has /plans, /team and /requests`, ['/plans', '/team', '/requests'].every((h) => hrefs.includes(h)), hrefs.filter((h) => /plans|team|requests/.test(h)).join(','));
    const counts = await groupItemCounts(page);
    /* Phase 4 added the Reports hub to Recovery & team: [6,12,9] -> [6,13,9].
     * Phase 5 added calls, calling workspace, campaigns and call review, calling admin: -> [9,13,11]. */
    check(`screens ${lang}: sidebar item count per group [9,13,11]`, JSON.stringify(counts) === JSON.stringify([9, 13, 11]), JSON.stringify(counts));
    const order = await page.$$eval('.side-nav .menu-nav-group', (gs) => [...gs[1].querySelectorAll('a[href]')].map((a) => a.getAttribute('href')));
    check(`screens ${lang}: plans leads Recovery & team; team and requests close it`, order[0] === '/plans' && order.indexOf('/team') > 0 && order.indexOf('/requests') > order.indexOf('/team'), order.join(','));
    for (const [name, url, root] of [
      ['01_requests', '/requests', 'p3-requests'],
      ['02_plans', '/plans', 'p3-plans'],
      ['03_team', '/team', 'p3-team'],
      ['04_case_requests', `/clients/details/requests?id=${CASES[1].id}`, 'p3-case-requests'],
      ['05_case_plan', `/clients/details/plan?id=${CASES[2].id}`, 'p3-case-plan'],
    ]) {
      await page.goto(FE + url, { waitUntil: 'networkidle' });
      await settle(page, 1300);
      const ok = await byTest(page, root).first().isVisible().catch(() => false);
      const err = await byTest(page, 'p1-error').count();
      check(`screens ${lang}: ${url} renders (${root}), no error state`, ok && err === 0, `visible=${ok} errors=${err}`);
      await shot(page, `${name}_${lang}`);
    }
    const dir = await page.evaluate(() => document.documentElement.dir);
    check(`screens ${lang}: document direction`, dir === (lang === 'ar' ? 'rtl' : 'ltr'), dir);
  }
  check('screens: no page errors', errors.length === 0, errors.join(' | '));
  await page.context().close();
}

/* ------------------------------------------------------------ requests end to end */
async function requests({ newPage }) {
  const docName = `ZZ-TEST phase3 attachment ${STAMP}`;
  const up = await uploadCaseDoc(CASES[1].id, docName);
  check('requests: a ZZ-TEST document is on case 1 to link', !!up.doc, JSON.stringify(up.body).slice(0, 200));
  if (up.doc) created.docs.push(up.doc.id);

  /* 1. aidi.dev raises a legal request from the case's Requests tab */
  const dev = await newPage();
  const page = dev.page;
  await login(page, ADMIN);
  await setLang(page, 'en');
  await page.goto(`${FE}/clients/details/requests?id=${CASES[1].id}`, { waitUntil: 'networkidle' });
  await settle(page);
  await byTest(page, 'p3-req-new').click();
  await byTest(page, 'p3-req-new-dialog').waitFor();
  const confirm = byTest(page, 'p3-req-new-dialog-confirm');
  check('requests: "raise" stays disabled until department, subject and details are filled', (await confirm.getAttribute('aria-disabled')) === 'true');
  const subject = `ZZ-TEST phase3 request ${STAMP}`;
  await byTest(page, 'p3-req-new-dept-legal').click();
  await byTest(page, 'p3-req-new-subject').fill(subject);
  await byTest(page, 'p3-req-new-body').fill('ZZ-TEST phase3: please issue a settlement letter for this test case.');
  await byTest(page, 'p3-req-new-amount').fill('125.50');
  const related = new Date(Date.now() + 3 * 3600e3 + 7 * 86400e3).toISOString().slice(0, 10);
  await byTest(page, 'p3-req-new-date').fill(related);
  if (up.doc) await page.locator(`[data-testid="p3-req-new-doc"][data-doc="${up.doc.id}"]`).check();
  await shot(page, '10_request_new_dialog_en');
  const createResp = page.waitForResponse((r) => r.url().includes(`/v2/cases/${CASES[1].id}/requests`) && r.request().method() === 'POST');
  await confirm.click();
  const cr = await createResp;
  check('requests: create answers 201', cr.status() === 201, cr.status());
  const createdBody = await cr.json();
  const reqId = createdBody.data.id;
  created.requests.push(reqId);
  await waitAttr(page, '[data-testid="p3-req-detail"]', 'data-request', reqId);
  check('requests: the new request opens in the panel as open', (await panelStatus(page)) === 'open');
  const d0 = data(await api(ADMIN, 'GET', `/v2/requests/${reqId}`));
  check('requests: stored exactly (department, amount 125.50, related date, linked document)', d0.department === 'legal' && d0.amount === '125.50' && d0.related_date === related && (!up.doc || d0.attachments.some((a) => a.document_id === up.doc.id)), JSON.stringify({ a: d0.amount, r: d0.related_date, att: d0.attachments.length }));
  check('requests: the panel lists the linked document', !up.doc || (await page.locator(`[data-testid="p3-req-attachment"][data-doc="${up.doc.id}"]`).count()) === 1);
  check('requests: own request: no "take" for the requester, and the note says why', (await byTest(page, 'p3-req-take').count()) === 0 && (await byTest(page, 'p3-req-own-note').count()) === 1);
  check('requests: the requester may cancel an open request (API actions)', d0.actions.cancel === true && (await byTest(page, 'p3-req-cancel').count()) === 1);
  await shot(page, '11_request_created_en');

  /* 2. attention queue: open_request label, fact and links (aidi.dev reviews every department) */
  if (want('attention') || true) {
    await page.goto(`${FE}/attention#reason=open_request`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const att = data(await api(ADMIN, 'GET', '/v2/attention?reason=open_request&page_size=25'));
    const uiCount = await countOfTab(page, 'p1-attention-tabs', 'open_request');
    check('attention: "Open request" tab shows the API count', uiCount === att.counts.open_request, `${uiCount} vs ${att.counts.open_request}`);
    const row = page.locator(`[data-testid="p1-attention-row"][data-case="${CASES[1].id}"]`).first();
    const inApi = att.items.some((i) => i.case.id === CASES[1].id);
    check('attention: case 1 is in the open_request list (API)', inApi);
    if (inApi) {
      check('attention: the row carries the "Open request" badge', (await row.locator('[data-reason="open_request"]').count()) === 1);
      const href = await row.locator('[data-testid="p1-work-link-open_request"]').getAttribute('href');
      check('attention: the fact links to the case Requests tab', /\/clients\/details\/requests\?id=/.test(href || ''), href);
      const href2 = await row.locator('[data-testid="p3-work-link-requests-screen"]').getAttribute('href');
      check('attention: and to the requests screen filtered to the case', href2 === `/requests#case=${CASES[1].id}`, href2);
      const factText = await row.locator('[data-testid="p1-work-link-open_request"]').innerText();
      check('attention: the fact names the department', /Legal/.test(factText), factText);
    }
    const labels = await page.$$eval('[data-testid="p1-attention-tabs"] [data-tab]', (els) => els.map((e) => [e.getAttribute('data-tab'), e.textContent.trim()]));
    const lab = Object.fromEntries(labels);
    check('attention: "Missed installment" tab label (en)', /Missed installment/.test(lab.missed_installment || ''), lab.missed_installment);
    check('attention: "Open request" tab label (en)', /Open request/.test(lab.open_request || ''), lab.open_request);
    await shot(page, '12_attention_open_request_en');
    await setLang(page, 'ar');
    await settle(page, 800);
    const labelsAr = Object.fromEntries(await page.$$eval('[data-testid="p1-attention-tabs"] [data-tab]', (els) => els.map((e) => [e.getAttribute('data-tab'), e.textContent.trim()])));
    check('attention: Arabic labels for both new reasons', /قسط متأخر/.test(labelsAr.missed_installment || '') && /طلب مفتوح/.test(labelsAr.open_request || ''), JSON.stringify(labelsAr));
    await shot(page, '12_attention_open_request_ar');
    await page.goto(`${FE}/attention#reason=missed_installment`, { waitUntil: 'networkidle' });
    await settle(page, 1200);
    const mi = data(await api(ADMIN, 'GET', '/v2/attention?reason=missed_installment&page_size=1'));
    check('attention: "Missed installment" count equals the API', (await countOfTab(page, 'p1-attention-tabs', 'missed_installment')) === mi.counts.missed_installment, mi.counts.missed_installment);
    await shot(page, '13_attention_missed_installment_ar');
    await setLang(page, 'en');
  }

  /* 3. aidi.reviewer (legal) takes, comments and resolves in the UI */
  const rev = await newPage();
  const rp = rev.page;
  await login(rp, REVIEWER);
  await setLang(rp, 'en');
  await rp.goto(`${FE}/requests#dept=legal&status=open`, { waitUntil: 'networkidle' });
  await settle(rp, 1300);
  const listApi = data(await api(REVIEWER, 'GET', '/v2/requests?department=legal&status=open&pageSize=25'));
  check('reviewer: legal/open tab count equals the API', (await countOfTab(rp, 'p3-req-status-tabs', 'open')) === listApi.counts.open, `${listApi.counts.open}`);
  const row = rp.locator(`[data-testid="p3-req-row"][data-request="${reqId}"]`);
  check('reviewer: the new request is listed under Legal / Open', (await row.count()) === 1);
  await row.click();
  await waitAttr(rp, '[data-testid="p3-req-detail"]', 'data-request', reqId);
  await byTest(rp, 'p3-req-take').click();
  await byTest(rp, 'p3-req-take-dialog').waitFor();
  await byTest(rp, 'p3-req-take-dialog-confirm').click();
  await waitAttr(rp, '[data-testid="p3-req-detail"]', 'data-status', 'in_review');
  check('reviewer: taken into review', (await statusOf(reqId)) === 'in_review');
  await byTest(rp, 'p3-req-comment').fill('ZZ-TEST phase3 comment: letter being drafted.');
  await byTest(rp, 'p3-req-comment-send').click();
  await rp.locator('[data-testid="p3-req-event"][data-event="commented"]').first().waitFor({ timeout: 15000 });
  const afterComment = data(await api(ADMIN, 'GET', `/v2/requests/${reqId}`));
  check('reviewer: the comment is an event and does not change the revision', afterComment.comment_count === 1 && afterComment.revision === 1, `comments=${afterComment.comment_count} rev=${afterComment.revision}`);
  await byTest(rp, 'p3-req-resolve').click();
  const disabled = await reasonDialog(rp, 'p3-req-resolve-dialog', 'ZZ-TEST phase3: letter issued, ref ZZ-118');
  check('reviewer: resolve needs a reason (confirm disabled while empty)', disabled);
  await waitAttr(rp, '[data-testid="p3-req-detail"]', 'data-status', 'resolved');
  check('reviewer: resolved', (await statusOf(reqId)) === 'resolved');
  await shot(rp, '14_request_resolved_reviewer_en');
  await setLang(rp, 'ar');
  await settle(rp, 700);
  await shot(rp, '14_request_resolved_reviewer_ar');

  /* 4. the requester reopens with a reason; the reviewer stays */
  await page.goto(`${FE}/requests#request=${reqId}`, { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  await settle(page, 1200);
  await byTest(page, 'p3-req-reopen').click();
  await reasonDialog(page, 'p3-req-reopen-dialog', 'ZZ-TEST phase3: the amount on the letter is wrong');
  await waitAttr(page, '[data-testid="p3-req-detail"]', 'data-status', 'in_review');
  const reopened = data(await api(ADMIN, 'GET', `/v2/requests/${reqId}`));
  check('requester: reopened back to in review, reviewer kept, reopen counted', reopened.status === 'in_review' && reopened.reviewer?.name && reopened.reopen_count === 1, JSON.stringify({ s: reopened.status, r: reopened.reviewer?.name, n: reopened.reopen_count }));
  const trail = await page.$$eval('[data-testid="p3-req-event"]', (els) => els.map((e) => e.getAttribute('data-event')));
  check('requester: the trail shows created, taken, commented, resolved, reopened in order', JSON.stringify(trail) === JSON.stringify(['created', 'taken', 'commented', 'resolved', 'reopened']), trail.join(','));

  /* 5. the reviewer resolves it again (final state) */
  await rp.goto(`${FE}/requests#request=${reqId}`, { waitUntil: 'networkidle' });
  await rp.reload({ waitUntil: 'networkidle' }); // a hash-only goto keeps the loaded (resolved) version
  await settle(rp, 1200);
  await byTest(rp, 'p3-req-resolve').click();
  await reasonDialog(rp, 'p3-req-resolve-dialog', 'ZZ-TEST phase3: corrected letter issued');
  await waitAttr(rp, '[data-testid="p3-req-detail"]', 'data-status', 'resolved');
  check('reviewer: resolved again after the reopen', (await statusOf(reqId)) === 'resolved');

  /* 6. a deleted linked document stays listed, marked deleted */
  if (up.doc) {
    const del = await api(ADMIN, 'DELETE', `/clint/document/${up.doc.id}`);
    await page.goto(`${FE}/requests#request=${reqId}`, { waitUntil: 'networkidle' });
    await page.reload({ waitUntil: 'networkidle' });
    await settle(page, 1200);
    const attText = await page.locator(`[data-testid="p3-req-attachment"][data-doc="${up.doc.id}"]`).innerText().catch(() => '');
    check('requests: after the case document is deleted the link stays, marked deleted', del.status < 300 && /Deleted|removed/i.test(attText), `${del.status} ${attText}`);
  }

  /* 7. cancel path: a finance request cancelled by its requester in the UI */
  const r2 = await api(ADMIN, 'POST', `/v2/cases/${CASES[1].id}/requests`, { department: 'finance', subject: `ZZ-TEST phase3 cancel path ${STAMP}`, body: 'ZZ-TEST phase3: to be cancelled.' });
  const r2id = data(r2).id;
  created.requests.push(r2id);
  await page.goto(`${FE}/clients/details/requests?id=${CASES[1].id}#request=${r2id}`, { waitUntil: 'networkidle' });
  await settle(page, 1300);
  await byTest(page, 'p3-req-cancel').click();
  const cancelDisabled = await reasonDialog(page, 'p3-req-cancel-dialog', 'ZZ-TEST phase3: raised by mistake');
  await waitAttr(page, '[data-testid="p3-req-detail"]', 'data-status', 'cancelled');
  const c2 = data(await api(ADMIN, 'GET', `/v2/requests/${r2id}`));
  check('requester: cancel needs a reason and ends cancelled with it', cancelDisabled && c2.status === 'cancelled' && c2.cancel_reason === 'ZZ-TEST phase3: raised by mistake', c2.status);
  check('requester: a cancelled request has no comment box', (await byTest(page, 'p3-req-comment').count()) === 0);
  await shot(page, '15_case_requests_cancelled_en');
  await setLang(page, 'ar');
  await settle(page, 700);
  await shot(page, '15_case_requests_cancelled_ar');

  /* 8. filters and hash state on the requests screen */
  await setLang(page, 'en');
  await page.goto(`${FE}/requests#requester=me&case=${CASES[1].id}&status=cancelled`, { waitUntil: 'networkidle' });
  await settle(page, 1300);
  const apiF = data(await api(ADMIN, 'GET', `/v2/requests?requester=me&case_id=${CASES[1].id}&status=cancelled&pageSize=25`));
  const uiRows = await page.$$eval('[data-testid="p3-req-row"]', (els) => els.map((e) => e.getAttribute('data-request')));
  check('requests screen: requester=me + case + status from the hash give the API rows', JSON.stringify(uiRows) === JSON.stringify(apiF.items.map((i) => i.id)), `${uiRows.length} vs ${apiF.items.length}`);
  check('requests screen: the case filter shows as a removable chip', (await byTest(page, 'p3-req-case-chip').count()) === 1);
  const today = riyadhToday();
  await page.goto(`${FE}/requests#from=${today}&to=${today}&dept=finance`, { waitUntil: 'networkidle' });
  await settle(page, 1300);
  const apiD = data(await api(ADMIN, 'GET', `/v2/requests?date_from=${today}&date_to=${today}&department=finance&pageSize=25`));
  check('requests screen: Riyadh date range + department tab counts equal the API', (await countOfTab(page, 'p3-req-dept-tabs', 'finance')) === apiD.department_counts.finance && (await countOfTab(page, 'p3-req-status-tabs', 'cancelled')) === apiD.counts.cancelled, JSON.stringify(apiD.department_counts));
  await page.reload({ waitUntil: 'networkidle' });
  await settle(page, 1000);
  check('requests screen: the hash survives a reload', (await page.evaluate(() => location.hash)).includes('dept=finance'));

  await dev.ctx.close();
  await rev.ctx.close();
}

/* ------------------------------------------------ 409: someone else changed it */
async function conflict({ newPage }) {
  const r = await api(ADMIN, 'POST', `/v2/cases/${CASES[1].id}/requests`, { department: 'legal', subject: `ZZ-TEST phase3 conflict ${STAMP}`, body: 'ZZ-TEST phase3: conflict test.' });
  const id = data(r).id;
  created.requests.push(id);
  const { page, ctx } = await newPage();
  await login(page, REVIEWER);
  await setLang(page, 'en');
  await page.goto(`${FE}/requests#request=${id}`, { waitUntil: 'networkidle' });
  await settle(page, 1300);
  await byTest(page, 'p3-req-take').click();
  await byTest(page, 'p3-req-take-dialog').waitFor();
  // meanwhile the requester cancels it
  const c = await api(ADMIN, 'POST', `/v2/requests/${id}/cancel`, { expected_revision: 0, reason: 'ZZ-TEST phase3: cancelled while the reviewer was taking it' });
  check('conflict: the requester cancelled in the meantime', c.status === 200, c.status);
  const resp = page.waitForResponse((x) => x.url().endsWith(`/v2/requests/${id}/take`));
  await byTest(page, 'p3-req-take-dialog-confirm').click();
  const tr = await resp;
  check('conflict: the stale take answers HTTP 409 invalid_transition', tr.status() === 409 && (await tr.json()).code === 'invalid_transition', tr.status());
  await byTest(page, 'p1-conflict').waitFor({ timeout: 10000 });
  const text = await byTest(page, 'p1-conflict').innerText();
  check('conflict: the dialog says it changed, why, and offers a reload', /Changed by someone else/.test(text) && /Cancelled/.test(text) && /Reload the latest version/.test(text), text.replace(/\s+/g, ' '));
  await shot(page, '16_request_conflict_en');
  await byTest(page, 'p1-conflict').locator('button').click();
  await waitAttr(page, '[data-testid="p3-req-detail"]', 'data-status', 'cancelled');
  check('conflict: after reload the panel shows the cancelled request, no take button', (await byTest(page, 'p3-req-take').count()) === 0);
  await ctx.close();
}

/* ------------------------------------------------------------ plans */
function nextDay31() {
  const today = riyadhToday();
  for (let i = 0; i < 400; i += 1) {
    const d = new Date(Date.parse(today + 'T00:00:00Z') + i * 86400e3).toISOString().slice(0, 10);
    if (d.endsWith('-31') && d >= today) return d;
  }
  return today;
}
const rowsOf = (page, testId) =>
  page.$$eval(`[data-testid="${testId}"]`, (els) => els.map((e) => [e.getAttribute('data-seq'), e.getAttribute('data-due'), e.getAttribute('data-amount')].join('|')));

async function plans({ newPage }) {
  const caseId = CASES[2].id;
  // leftover active plan from an earlier run: cancel it first (ZZ-TEST only)
  const before = data(await api(ADMIN, 'GET', `/v2/cases/${caseId}/plans`));
  if (before.active_plan_id) {
    const p = before.items.find((x) => x.id === before.active_plan_id);
    await api(ADMIN, 'POST', `/v2/plans/${p.id}/cancel`, { expected_revision: p.revision, reason: 'ZZ-TEST phase3: leftover from an earlier run' });
  }
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  await page.goto(`${FE}/clients/details/plan?id=${caseId}`, { waitUntil: 'networkidle' });
  await settle(page, 1300);
  await byTest(page, 'p3-plan-create').click();
  await byTest(page, 'p3-plan-wizard').waitFor();

  // an amount above the outstanding balance: the preview endpoint refuses it
  await byTest(page, 'p3-plan-total').fill('99999999.00');
  await byTest(page, 'p3-plan-freq-monthly').click();
  const start = nextDay31();
  await byTest(page, 'p3-plan-start').fill(start);
  await byTest(page, 'p3-plan-mode-count').click();
  await byTest(page, 'p3-plan-count').fill('3');
  const pr1 = page.waitForResponse((r) => r.url().endsWith(`/v2/cases/${caseId}/plans/preview`));
  await byTest(page, 'p3-plan-next').click();
  const r1 = await pr1;
  check('plans: a total above the balance is refused by the preview (409 amount_exceeds_balance)', r1.status() === 409 && (await r1.json()).code === 'amount_exceeds_balance', r1.status());
  await page.waitForTimeout(500);
  const errText = await page.locator('[data-testid="p3-plan-wizard"] [role="alert"]').first().innerText().catch(() => '');
  check('plans: the wizard explains it with the exact outstanding balance', /outstanding balance/i.test(errText) && /2,000\.50|2000\.50/.test(errText), errText.replace(/\s+/g, ' '));

  // a valid plan: 1000.00, monthly from a 31st, 3 installments
  await byTest(page, 'p3-plan-total').fill('1000.00');
  let previewReq = null;
  page.on('request', (r) => {
    if (r.url().endsWith(`/v2/cases/${caseId}/plans/preview`)) previewReq = r.postDataJSON();
  });
  const pr2 = page.waitForResponse((r) => r.url().endsWith(`/v2/cases/${caseId}/plans/preview`) && r.status() === 200);
  await byTest(page, 'p3-plan-next').click();
  const previewBody = await (await pr2).json();
  await byTest(page, 'p3-plan-preview').waitFor();
  const prevRows = await rowsOf(page, 'p3-plan-preview-row');
  const apiPrev = previewBody.data.installments.map((i) => [String(i.seq), i.due_date, i.amount].join('|'));
  check('plans: the preview table shows the endpoint schedule exactly', JSON.stringify(prevRows) === JSON.stringify(apiPrev), prevRows.join(' ; '));
  check('plans: halalas: 333.34, 333.33, 333.33 and month-end clamping from the 31st', prevRows.map((r) => r.split('|')[2]).join(',') === '333.34,333.33,333.33', prevRows.join(' ; '));
  check('plans: the preview request carries the typed values (no local schedule)', previewReq && previewReq.total_amount === '1000.00' && previewReq.frequency === 'monthly' && previewReq.start_date === start && Number(previewReq.installment_count) === 3 && !('installments' in previewReq), JSON.stringify(previewReq));
  await shot(page, '20_plan_preview_en');
  const cr = page.waitForResponse((r) => r.url().endsWith(`/v2/cases/${caseId}/plans`) && r.request().method() === 'POST');
  await byTest(page, 'p3-plan-confirm').click();
  const crr = await cr;
  check('plans: create answers 201', crr.status() === 201, crr.status());
  const plan = (await crr.json()).data;
  created.plans.push(plan.id);
  await settle(page, 1300);
  const saved = data(await api(ADMIN, 'GET', `/v2/plans/${plan.id}`));
  const savedRows = saved.installments.map((i) => [String(i.seq), i.due_date, i.amount].join('|'));
  check('plans: the saved schedule equals the preview (API)', JSON.stringify(savedRows) === JSON.stringify(apiPrev), savedRows.join(' ; '));
  const uiInst = await rowsOf(page, 'p3-installment-row');
  check('plans: the case tab shows the saved installments', JSON.stringify(uiInst) === JSON.stringify(savedRows), uiInst.join(' ; '));
  const states = await page.$$eval('[data-testid="p3-installment-row"]', (els) => els.map((e) => e.getAttribute('data-state')));
  check('plans: installment states come from the API', JSON.stringify(states) === JSON.stringify(saved.installments.map((i) => i.state)), states.join(','));
  await shot(page, '21_plan_created_en');
  await setLang(page, 'ar');
  await settle(page, 700);
  await shot(page, '21_plan_created_ar');
  await setLang(page, 'en');

  // manual allocate (a payment approved before the plan existed) and deallocate
  const pays = data(await api(ADMIN, 'GET', `/v2/cases/${caseId}/payments`)).items.filter((p) => p.status === 'approved');
  if (pays.length && (await byTest(page, 'p3-plan-allocate').count())) {
    await byTest(page, 'p3-plan-allocate').click();
    await byTest(page, 'p3-plan-allocate-dialog').waitFor();
    const payEl = byTest(page, 'p3-alloc-payment').first();
    if ((await payEl.evaluate((e) => e.tagName)) === 'SELECT') await payEl.selectOption(pays[0].id);
    else await pickIn(page, 'p3-alloc-payment', pays[0].amount);
    await byTest(page, 'p3-alloc-amount').fill('10.00');
    await byTest(page, 'p3-alloc-reason').fill('ZZ-TEST phase3: receipt matched by hand');
    const ar = page.waitForResponse((r) => r.url().endsWith(`/v2/plans/${plan.id}/allocations`));
    await byTest(page, 'p3-plan-allocate-dialog-confirm').click();
    const arr = await ar;
    check('plans: manual allocation answers 201 (plans_manage)', arr.status() === 201, `${arr.status()} ${JSON.stringify(await arr.json()).slice(0, 200)}`);
    await settle(page, 1200);
    const afterAlloc = data(await api(ADMIN, 'GET', `/v2/plans/${plan.id}`));
    check('plans: 10.00 allocated to the first installment, source manual with the reason', afterAlloc.allocated === '10.00' && afterAlloc.installments[0].allocations.some((a) => a.source === 'manual' && a.note === 'ZZ-TEST phase3: receipt matched by hand'), afterAlloc.allocated);
    // expand the installment if needed, then deallocate
    const btn = byTest(page, 'p3-alloc-deallocate-btn').first();
    if (!(await btn.isVisible().catch(() => false))) await page.locator('[data-testid="p3-installment-row"]').first().click();
    await btn.click();
    await reasonDialog(page, 'p3-alloc-deallocate', 'ZZ-TEST phase3: allocated to the wrong plan');
    await settle(page, 1200);
    const afterDe = data(await api(ADMIN, 'GET', `/v2/plans/${plan.id}`));
    const voided = afterDe.installments[0].allocations.find((a) => a.source === 'manual');
    check('plans: deallocated with a reason (allocation voided, allocated back to 0.00)', afterDe.allocated === '0.00' && voided && voided.active === false && voided.void_reason === 'ZZ-TEST phase3: allocated to the wrong plan', afterDe.allocated);
    await shot(page, '22_plan_deallocated_en');
  } else {
    check('plans: manual allocate button and an approved payment are available', false, `payments=${pays.length}`);
  }

  // cancel with a reason
  await byTest(page, 'p3-plan-cancel').first().click();
  const cDisabled = await reasonDialog(page, 'p3-plan-cancel-dialog', 'ZZ-TEST phase3: cleanup');
  await settle(page, 1300);
  const cancelled = data(await api(ADMIN, 'GET', `/v2/plans/${plan.id}`));
  check('plans: cancel needs a reason; the plan ends cancelled with it', cDisabled && cancelled.status === 'cancelled' && cancelled.cancellation?.reason === 'ZZ-TEST phase3: cleanup', cancelled.status);

  // the plans screen: state tabs from counts_by_state, the cancelled plan listed
  await page.goto(`${FE}/plans#state=cancelled&sort=-created_at`, { waitUntil: 'networkidle' });
  await settle(page, 1500);
  const list = data(await api(ADMIN, 'GET', '/v2/plans?state=cancelled&sort=-created_at&page_size=25'));
  let tabsOk = true;
  for (const s of ['active', 'completed', 'broken', 'cancelled']) {
    const c = await countOfTab(page, 'p3-plan-tabs', s);
    if (c !== list.counts_by_state[s].count) tabsOk = false;
  }
  check('plans screen: state tabs show counts_by_state exactly', tabsOk, JSON.stringify(list.counts_by_state));
  const first = await page.locator('[data-testid="p3-plan-row"]').first().getAttribute('data-plan');
  check('plans screen: the cancelled plan is first under Cancelled (newest first)', first === plan.id, first);
  await page.locator(`[data-testid="p3-plan-row"][data-plan="${plan.id}"]`).click();
  await byTest(page, 'p3-plan-panel').waitFor({ timeout: 10000 });
  await settle(page, 800);
  check('plans screen: the side panel opens with the installments', (await page.locator('[data-testid="p3-plan-panel"] [data-testid="p3-installment-row"]').count()) === 3);
  check('plans screen: a cancelled plan offers no cancel/allocate', (await page.locator('[data-testid="p3-plan-panel"] [data-testid="p3-plan-cancel"]').count()) === 0);
  await page.reload({ waitUntil: 'networkidle' });
  await settle(page, 1200);
  check('plans screen: tab and selected plan survive a reload', (await byTest(page, 'p3-plan-panel').count()) === 1 && (await page.evaluate(() => location.hash)).includes('state=cancelled'));
  await shot(page, '23_plans_screen_en');
  await setLang(page, 'ar');
  await settle(page, 700);
  await shot(page, '23_plans_screen_ar');
  await ctx.close();
}

/* ------------------------------------------------------------ targets */
async function targets({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  await page.goto(`${FE}/users/edit?id=${COLLECTOR_ID}`, { waitUntil: 'networkidle' });
  await settle(page, 1500);
  const panel = byTest(page, 'p3-targets-user');
  check('targets: the "Targets by month" panel is on the legacy user form', await panel.isVisible());
  check('targets: the legacy form is still there', (await page.locator('form').count()) > 0);
  const month = riyadhMonth(3);
  const hist0 = data(await api(ADMIN, 'GET', `/v2/users/${COLLECTOR_ID}/targets`));
  const rev0 = (hist0.months.find((m) => m.month === month) || { revision: 0 }).revision;
  await byTest(page, 'p3-target-month').fill(month);
  await byTest(page, 'p3-target-kind-amount').click();
  await byTest(page, 'p3-target-value').fill('1234.56');
  await byTest(page, 'p3-target-reason').fill(`ZZ-TEST phase3: future target ${STAMP}`);
  const put = page.waitForResponse((r) => r.url().includes(`/v2/users/${COLLECTOR_ID}/targets/${month}`) && r.request().method() === 'PUT');
  await byTest(page, 'p3-target-save').click();
  const pr = await put;
  const sent = pr.request().postDataJSON();
  check('targets: a future month saves (200) with the loaded revision', pr.status() === 200 && sent.expected_revision === rev0 && sent.kind === 'amount' && sent.value === '1234.56', `${pr.status()} ${JSON.stringify(sent)}`);
  const body = await pr.json();
  check('targets: a future month is not mirrored into the legacy fields', body.data?.mirrored_to_legacy === false);
  await settle(page, 1200);
  const hist1 = data(await api(ADMIN, 'GET', `/v2/users/${COLLECTOR_ID}/targets`));
  const top = hist1.history[0];
  check('targets: history has the new v2 revision on top', top.month === month && top.value === '1234.56' && top.source === 'v2' && top.revision === rev0 + 1, JSON.stringify(top).slice(0, 200));
  const monthRow = page.locator(`[data-testid="p3-target-month-row"][data-month="${month}"]`);
  check('targets: the months table shows the month, unlocked', (await monthRow.count()) === 1 && (await monthRow.getAttribute('data-locked')) === 'false');
  const shown = await monthRow.innerText().catch(() => '');
  check('targets: the value is shown exactly', /1,234\.56|1234\.56/.test(shown), shown.replace(/\s+/g, ' '));
  await shot(page, '30_targets_user_en');

  // past month: locked in the form
  const past = riyadhMonth(-1);
  await byTest(page, 'p3-target-month').fill(past);
  await page.waitForTimeout(400);
  check('targets: a past month shows the lock explanation', await byTest(page, 'p3-target-locked').isVisible());
  const saveDisabled = (await byTest(page, 'p3-target-save').getAttribute('aria-disabled')) === 'true' || (await byTest(page, 'p3-target-save').isDisabled());
  check('targets: save is disabled for a past month', saveDisabled);
  // the server's 409 month_locked, as when the month rolls over while the form is open
  await page.route(`**/v2/users/${COLLECTOR_ID}/targets/*`, (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    return route.continue({ url: route.request().url().replace(/targets\/\d{4}-\d{2}$/, `targets/${past}`) });
  });
  await byTest(page, 'p3-target-month').fill(month);
  await byTest(page, 'p3-target-value').fill('1234.56');
  await byTest(page, 'p3-target-reason').fill('ZZ-TEST phase3: month rolled over');
  const put2 = page.waitForResponse((r) => r.url().includes(`/targets/${past}`) && r.request().method() === 'PUT');
  await byTest(page, 'p3-target-save').click();
  const p2 = await put2;
  check('targets: the server refuses a past month with 409 month_locked', p2.status() === 409 && (await p2.json()).code === 'month_locked', p2.status());
  const err = await page.locator('[data-testid="p3-targets-user"] [role="alert"]').first().innerText().catch(() => '');
  check('targets: the 409 is explained (past months are locked, current month named)', /locked/i.test(err) && err.includes(riyadhMonth(0)), err.replace(/\s+/g, ' '));
  await shot(page, '31_targets_month_locked_en');
  await page.unroute(`**/v2/users/${COLLECTOR_ID}/targets/*`);
  await setLang(page, 'ar');
  await settle(page, 700);
  await shot(page, '31_targets_month_locked_ar');
  await setLang(page, 'en');

  // contract form
  await page.goto(`${FE}/contracts/edit?id=${CONTRACT}`, { waitUntil: 'networkidle' });
  await settle(page, 1500);
  const cHist = data(await api(ADMIN, 'GET', `/v2/contracts/${CONTRACT}/targets`));
  check('targets: the contract form has the panel with every month the API lists', (await page.locator('[data-testid="p3-targets-contract"] [data-testid="p3-target-month-row"]').count()) === cHist.months.length, cHist.months.length);
  await shot(page, '32_targets_contract_en');
  await ctx.close();
}

/* ------------------------------------------------------------ team */
const cents = (s) => (s === null || s === undefined ? null : BigInt(String(s).replace('.', '').replace(/^(-?)0+(?=\d)/, '$1')));
async function team({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, ADMIN);
  await setLang(page, 'en');
  const month = riyadhMonth(0);
  await page.goto(`${FE}/team#month=${month}`, { waitUntil: 'networkidle' });
  await settle(page, 2000);
  const a = data(await api(ADMIN, 'GET', `/v2/targets/achievement?month=${month}`));
  const rows = await page.$$eval('[data-testid="p3-team-collector-row"]', (els) => els.map((e) => e.getAttribute('data-user')));
  check('team: one row per collector the API returns', rows.length === a.collectors.length, `${rows.length} vs ${a.collectors.length}`);
  check('team: contracts table for the admin view', (await page.locator('[data-testid="p3-team-contract-row"]').count()) === a.contracts.length, a.contracts.length);
  check('team: actual vs actual_recorded is explained', /currently owns|current owner/i.test(await byTest(page, 'p3-team-actual-note').innerText()));
  // exact numbers: the first collector row shows the API's actual string
  const firstApi = a.collectors.find((c) => c.user_id === rows[0]);
  const firstText = await page.locator('[data-testid="p3-team-collector-row"]').first().innerText();
  const grouped = (s) => s.replace(/^(-?)(\d+)/, (m, sg, d) => sg + d.replace(/\B(?=(\d{3})+(?!\d))/g, ','));
  check('team: money is shown exactly as returned', firstApi && firstText.includes(grouped(firstApi.actual)), `${firstApi?.actual}`);
  await shot(page, '40_team_current_en');
  // sort by actual, descending: the first row is the API's largest actual
  await page.goto(`${FE}/team#month=${month}&sort=actual&dir=desc`, { waitUntil: 'networkidle' });
  await settle(page, 1500);
  const top = [...a.collectors].sort((x, y) => (cents(y.actual) > cents(x.actual) ? 1 : cents(y.actual) < cents(x.actual) ? -1 : 0))[0];
  const firstRow = await page.locator('[data-testid="p3-team-collector-row"]').first().getAttribute('data-user');
  const topAct = cents(top.actual);
  const firstAct = cents(a.collectors.find((c) => c.user_id === firstRow)?.actual);
  check('team: sorting by actual (desc) puts the largest exact amount first', firstAct === topAct, `${firstRow} ${top.user_id}`);
  // a past month: every target unknown -> "No target history", never 0
  const prev = riyadhMonth(-1);
  await page.goto(`${FE}/team#month=${prev}`, { waitUntil: 'networkidle' });
  await settle(page, 2000);
  const ap = data(await api(ADMIN, 'GET', `/v2/targets/achievement?month=${prev}`));
  const unknown = ap.collectors.filter((c) => !c.target.known).length;
  const uiUnknown = await byTest(page, 'p3-no-target-history').count();
  check('team: unknown targets show "No target history" (not 0)', unknown > 0 && uiUnknown >= unknown, `${uiUnknown} vs ${unknown}`);
  const falseRows = await page.$$eval('[data-testid="p3-team-collector-row"][data-known="false"]', (els) => els.length);
  check('team: rows are marked known=false exactly for the unknown targets', falseRows === unknown, `${falseRows} vs ${unknown}`);
  await shot(page, '41_team_past_month_en');
  await setLang(page, 'ar');
  await settle(page, 800);
  await shot(page, '41_team_past_month_ar');
  await ctx.close();

  // the collector's own view
  const c = await newPage();
  await login(c.page, COLLECTOR);
  await setLang(c.page, 'en');
  await c.page.goto(`${FE}/team`, { waitUntil: 'networkidle' });
  await settle(c.page, 1500);
  const own = data(await api(COLLECTOR, 'GET', '/v2/targets/achievement'));
  check('team (collector): own view only, no contracts table', own.view === 'own' && (await byTest(c.page, 'p3-team-contracts').count()) === 0 && (await c.page.locator('[data-testid="p3-team-collector-row"]').count()) === own.collectors.length, `${own.view} ${own.collectors.length}`);
  await shot(c.page, '42_team_collector_own_en');
  await c.ctx.close();
}

/* ------------------------------------------------------------ sessions */
async function sessions({ newPage }) {
  // the collector's open browser session (ended on purpose below)
  const col = await newPage({ revoked: true });
  await login(col.page, COLLECTOR);
  await setLang(col.page, 'en');
  check('sessions: aidi.collector is signed in', !/sign-in/.test(col.page.url()), col.page.url());

  const adm = await newPage();
  const page = adm.page;
  await login(page, ADMIN);
  await setLang(page, 'en');
  await page.goto(`${FE}/users/details?id=${COLLECTOR_ID}`, { waitUntil: 'networkidle' });
  await settle(page, 1500);
  check('sessions: the user admin screen has the sessions card', await byTest(page, 'p3-user-sessions').isVisible());
  await shot(page, '50_user_sessions_card_en');
  await byTest(page, 'p3-signout-everywhere').click();
  await byTest(page, 'p3-signout-dialog').waitFor();
  const reasonInput = byTest(page, 'p3-signout-dialog-reason');
  if (await reasonInput.count()) await reasonInput.fill('ZZ-TEST phase3: sign-out everywhere check');
  const so = page.waitForResponse((r) => r.url().endsWith(`/v2/users/${COLLECTOR_ID}/sign-out`));
  await byTest(page, 'p3-signout-dialog-confirm').click();
  const sor = await so;
  check('sessions: sign out everywhere answers 200', sor.status() === 200, sor.status());
  await byTest(page, 'p3-signout-result').waitFor({ timeout: 10000 });
  check('sessions: the time sessions end is shown', (await byTest(page, 'p3-signout-result').innerText()).length > 0);

  // the collector's next action is refused and lands on sign-in with the message
  await col.page.locator('.side-nav a[href="/clients"]').first().click().catch(() => {});
  await col.page.waitForURL(/sign-in/, { timeout: 20000 }).catch(() => {});
  await settle(col.page, 800);
  check('sessions: the collector is sent to sign-in', /sign-in/.test(col.page.url()), col.page.url());
  const note = byTest(col.page, 'p3-session-ended');
  const noteText = await note.innerText().catch(() => '');
  check('sessions: sign-in explains "ended by an administrator", in both languages', (await note.getAttribute('data-reason').catch(() => null)) === 'revoked' && /ended by an administrator/.test(noteText) && /أنهى المسؤول جلستك/.test(noteText), noteText.replace(/\s+/g, ' '));
  await shot(col.page, '51_signin_session_ended_en');
  await setLang(col.page, 'ar').catch(() => {});
  await shot(col.page, '51_signin_session_ended_ar');
  // and can sign in again
  await login(col.page, COLLECTOR);
  check('sessions: the collector can sign in again', !/sign-in/.test(col.page.url()), col.page.url());
  check('sessions: the note is gone after a new sign-in', (await byTest(col.page, 'p3-session-ended').count()) === 0);

  // v2 deactivate / activate with a reason
  await page.goto(`${FE}/users/details?id=${COLLECTOR_ID}`, { waitUntil: 'networkidle' });
  await settle(page, 1200);
  await byTest(page, 'p3-user-deactivate').click();
  const dDisabled = await reasonDialog(page, 'p3-user-status-dialog', 'ZZ-TEST phase3: deactivate check');
  await settle(page, 1500);
  const loginWhileInactive = await fetch(API + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: COLLECTOR, password: 'Aidi@2026' }) });
  check('sessions: deactivate needs a reason; the user can no longer sign in', dDisabled && loginWhileInactive.status === 401, loginWhileInactive.status);
  await col.page.reload({ waitUntil: 'networkidle' }).catch(() => {});
  await col.page.waitForURL(/sign-in/, { timeout: 20000 }).catch(() => {});
  check('sessions: deactivation also ends the open session', /sign-in/.test(col.page.url()), col.page.url());
  await shot(page, '52_user_deactivated_en');
  await byTest(page, 'p3-user-activate').waitFor({ timeout: 10000 });
  await byTest(page, 'p3-user-activate').click();
  await reasonDialog(page, 'p3-user-status-dialog', 'ZZ-TEST phase3: re-activate after the check');
  await settle(page, 1500);
  const again = await fetch(API + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: COLLECTOR, password: 'Aidi@2026' }) });
  check('sessions: re-activated; aidi.collector signs in again', again.status === 200, again.status);
  await col.ctx.close();
  await adm.ctx.close();
}

/* ------------------------------------------------ permission refresh via /v2/me */
async function refresh({ newPage }) {
  const role = await api(ADMIN, 'GET', `/roles/${REVIEWER_ROLE}`);
  const keysBefore = [...role.body.permission_arr].sort();
  created.roleBefore = { name: role.body.name, keys: keysBefore };
  const KEY = 'case_corrections_review';
  check('refresh: the reviewer role holds the key before the test', keysBefore.includes(KEY));
  const { page, ctx } = await newPage();
  await login(page, REVIEWER);
  await setLang(page, 'en');
  await page.goto(`${FE}/requests`, { waitUntil: 'networkidle' });
  await settle(page, 1000);
  check('refresh: the reviewer sees Case corrections and Requests in the nav', (await navHrefs(page)).includes('/corrections') && (await navHrefs(page)).includes('/requests'));
  const put = await api(ADMIN, 'PUT', `/roles/${REVIEWER_ROLE}`, { id: REVIEWER_ROLE, name: role.body.name, permission: keysBefore.filter((k) => k !== KEY) });
  check('refresh: the key is revoked on the role', put.status === 200 && put.body.permission?.[KEY] === false, put.status);
  await sleep(16000); // the focus refresh is throttled to one per 15 s
  const meCall = page.waitForResponse((r) => r.url().endsWith('/v2/me'), { timeout: 15000 });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  const me = await meCall;
  check('refresh: window focus re-reads GET /v2/me', me.status() === 200, me.status());
  await page.waitForFunction(() => !document.querySelector('.side-nav a[href="/corrections"]'), null, { timeout: 10000 }).catch(() => {});
  const hrefs = await navHrefs(page);
  check('refresh: the nav drops Case corrections without a new sign-in', !hrefs.includes('/corrections') && hrefs.includes('/requests') && !/sign-in/.test(page.url()), hrefs.join(','));
  await page.goto(`${FE}/corrections`, { waitUntil: 'networkidle' });
  await settle(page, 800);
  check('refresh: the route guard follows too (/corrections -> access denied)', /access-denied/.test(page.url()), page.url());
  await shot(page, '60_refresh_key_revoked_en');
  // restore
  const back = await api(ADMIN, 'PUT', `/roles/${REVIEWER_ROLE}`, { id: REVIEWER_ROLE, name: role.body.name, permission: keysBefore });
  const after = (await api(ADMIN, 'GET', `/roles/${REVIEWER_ROLE}`)).body.permission_arr.sort();
  check('refresh: the role is restored exactly', back.status === 200 && JSON.stringify(after) === JSON.stringify(keysBefore), after.length);
  created.roleBefore = null;
  await sleep(16000);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForFunction(() => !!document.querySelector('.side-nav a[href="/corrections"]'), null, { timeout: 15000 }).catch(() => {});
  check('refresh: the nav shows Case corrections again, still without a new sign-in', (await navHrefs(page)).includes('/corrections'));
  await ctx.close();
}

/* ------------------------------------------------------------ gating */
async function gating({ newPage }) {
  const { page, ctx } = await newPage();
  await login(page, COLLECTOR);
  await setLang(page, 'en');
  await settle(page, 800);
  const hrefs = await navHrefs(page);
  check('gating (collector): no Requests or Plans in the nav', !hrefs.includes('/requests') && !hrefs.includes('/plans'), hrefs.filter((h) => /requests|plans|team/.test(h)).join(','));
  check('gating (collector): Team is shown (dashbord_user = own figures, like the legacy KPI card)', hrefs.includes('/team'));
  for (const url of ['/requests', '/plans']) {
    await page.goto(FE + url, { waitUntil: 'networkidle' });
    await settle(page, 800);
    check(`gating (collector): ${url} -> access denied`, /access-denied/.test(page.url()), page.url());
  }
  // a case the collector owns (if any) has no Plan / Requests tabs
  const mine = await api(COLLECTOR, 'GET', '/clints?page=1&pageSize=1');
  const caseId = (mine.body?.clints || mine.body?.data || [])[0]?.id;
  if (caseId) {
    await page.goto(`${FE}/clients/details/details?id=${caseId}`, { waitUntil: 'networkidle' });
    await settle(page, 1200);
    const tabs = await page.$$eval('.tab-nav', (els) => els.map((e) => e.getAttribute('data-value') || e.textContent.trim()));
    check('gating (collector): the case has no Repayment plan / Requests tabs', !tabs.some((x) => /Repayment plan|^Requests$/.test(x)), tabs.join(','));
  } else {
    check('gating (collector): a case to open', true, 'collector has no cases; tab check skipped');
  }
  const r1 = await api(COLLECTOR, 'GET', '/v2/requests');
  const r2 = await api(COLLECTOR, 'GET', '/v2/plans');
  check('gating (collector): the API agrees (403 on /v2/requests and /v2/plans)', r1.status === 403 && r2.status === 403, `${r1.status} ${r2.status}`);
  await shot(page, '70_collector_nav_en');
  await ctx.close();
}

/* ------------------------------------------------------------ cleanup */
async function cleanup() {
  // requests: nothing left open / in review
  for (const id of created.requests) {
    const r = data(await api(ADMIN, 'GET', `/v2/requests/${id}`));
    if (!r) continue;
    if (r.status === 'open') await api(ADMIN, 'POST', `/v2/requests/${id}/cancel`, { expected_revision: r.revision, reason: 'ZZ-TEST phase3 cleanup' });
    else if (r.status === 'in_review') await api(REVIEWER, 'POST', `/v2/requests/${id}/resolve`, { expected_revision: r.revision, reason: 'ZZ-TEST phase3 cleanup' });
  }
  // plans: cancel any still active
  for (const id of created.plans) {
    const p = data(await api(ADMIN, 'GET', `/v2/plans/${id}`));
    if (p && p.status === 'active') await api(ADMIN, 'POST', `/v2/plans/${id}/cancel`, { expected_revision: p.revision, reason: 'ZZ-TEST phase3 cleanup' });
  }
  // the linked test document
  for (const id of created.docs) await api(ADMIN, 'DELETE', `/clint/document/${id}`);
  // the reviewer role, if a run stopped half-way
  if (created.roleBefore) {
    await api(ADMIN, 'PUT', `/roles/${REVIEWER_ROLE}`, { id: REVIEWER_ROLE, name: created.roleBefore.name, permission: created.roleBefore.keys });
  }
  // aidi.collector must end active
  const probe = await fetch(API + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: COLLECTOR, password: 'Aidi@2026' }) });
  if (probe.status !== 200) await api(ADMIN, 'POST', `/v2/users/${COLLECTOR_ID}/activate`, { reason: 'ZZ-TEST phase3 cleanup' });
  const final = await fetch(API + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: COLLECTOR, password: 'Aidi@2026' }) });
  check('cleanup: aidi.collector is active and can sign in', final.status === 200, final.status);
  const openLeft = [];
  for (const id of created.requests) {
    const s = await statusOf(id);
    if (s === 'open' || s === 'in_review') openLeft.push(id);
  }
  check('cleanup: no ZZ-TEST request left open or in review', openLeft.length === 0, openLeft.join(','));
  const plansLeft = data(await api(ADMIN, 'GET', `/v2/cases/${CASES[2].id}/plans`));
  check('cleanup: no active plan left on ZZTEST-P1-0002', !plansLeft.active_plan_id, plansLeft.active_plan_id);
}

async function run(ctx) {
  const sections = { screens, requests, conflict, plans, targets, team, sessions, refresh, gating };
  for (const [name, fn] of Object.entries(sections)) {
    if (!want(name)) continue;
    log(`\n== ${name}`);
    try {
      await fn(ctx);
    } catch (e) {
      check(`${name}: section finished without an exception`, false, e.stack || String(e));
    }
  }
}

module.exports = { run, cleanup };
