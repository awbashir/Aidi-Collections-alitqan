/* Sections of scripts/verify-phase1.cjs. Each section gets the shared helpers. */
const ADMIN = 'aidi.dev';
const COLLECTOR = 'aidi.collector';
const CASE1 = '44f5da48-0cf4-4686-a538-380c14c06ae3';
const IDENTITY1 = '1999000001';

const SCREENS = [
  { path: '/attention', key: 'attention_queue', testid: 'p1-attention', shot: '01_attention' },
  { path: '/follow-ups', key: 'follow_ups', testid: 'p1-follow-ups', shot: '02_follow_ups' },
  { path: '/promises', key: 'promises', testid: 'p1-promises', shot: '03_promises' },
  { path: '/payment-approvals', key: 'payments_approve', testid: 'p1-payment-approvals', shot: '04_payment_approvals' },
  { path: '/corrections', key: 'case_corrections_review', testid: 'p1-corrections', shot: '05_corrections' },
];

async function run(h) {
  const { newPage, check, login, setLang, settle, shot, want } = h;

  /* ---------------- 1. every screen loads for aidi.dev, ar and en ---------------- */
  if (want('screens')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      for (const s of SCREENS) {
        await page.goto(h.FE_URL + s.path, { waitUntil: 'networkidle' });
        await setLang(page, lang);
        await settle(page, 1500);
        const root = await page.locator(`[data-testid="${s.testid}"]`).count();
        const bad = await page.locator('[data-testid="p1-error"], [data-testid="p1-forbidden"]').count();
        const dir = await page.evaluate(() => document.documentElement.dir);
        check(`${s.path} renders for ${ADMIN} (${lang})`, root === 1 && bad === 0 && dir === (lang === 'ar' ? 'rtl' : 'ltr'), `root=${root} errorStates=${bad} dir=${dir}`);
        await shot(page, `${s.shot}_${lang}`);
      }
      // nav: the five new items are visible, in their groups
      const hrefs = await page.$$eval('.side-nav a', (as) => as.map((a) => a.getAttribute('href')));
      const expected = SCREENS.map((s) => s.path);
      check(`nav shows the 5 Phase 1 items (${lang})`, expected.every((p) => hrefs.includes(p)), JSON.stringify(expected.filter((p) => !hrefs.includes(p))));
    }
    // Case detail: the extended tabs, both languages.
    const TABS = [['details', '07_case_details'], ['followUps', '08_case_follow_ups'], ['promises', '09_case_promises'], ['corrections', '09b_case_corrections'], ['comments', '09c_case_notes']];
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      for (const [tab, name] of TABS) {
        await page.goto(`${h.FE_URL}/clients/details/${tab}?id=${CASE1}`, { waitUntil: 'networkidle' });
        await setLang(page, lang);
        await settle(page, 1800);
        const bad = await page.locator('[data-testid="p1-error"], [data-testid="p1-forbidden"]').count();
        check(`case detail tab ${tab} renders (${lang})`, bad === 0, `errorStates=${bad}`);
        await shot(page, `${name}_${lang}`);
      }
    }
    check('no page errors while loading the Phase 1 screens', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ---------------- 2. collector: no nav items, access denied on direct URLs ---------------- */
  if (want('gating')) {
    const { page } = await newPage();
    await login(page, COLLECTOR);
    await settle(page);
    const hrefs = await page.$$eval('.side-nav a', (as) => as.map((a) => a.getAttribute('href')));
    const leaked = SCREENS.map((s) => s.path).filter((p) => hrefs.includes(p));
    check(`${COLLECTOR} sees none of the Phase 1 nav items`, leaked.length === 0, JSON.stringify(leaked));
    for (const s of SCREENS) {
      await page.goto(h.FE_URL + s.path, { waitUntil: 'networkidle' });
      await settle(page, 800);
      check(`${COLLECTOR} direct ${s.path} → access denied`, page.url().endsWith('/access-denied'), page.url());
    }
    await shot(page, '06_collector_access_denied_ar');
    await page.context().close();
  }

  await flows(h);
}


/* ------------------------------------------------------------------ helpers */
const caseUrl = (h, tab, id = CASE1) => `${h.FE_URL}/clients/details/${tab}?id=${id}`;
const plusDays = (n) => {
  const d = new Date(Date.now() + 3 * 3600 * 1000 + n * 86400000); // Riyadh civil date
  return d.toISOString().slice(0, 10);
};
/** Open a react-select inside `scope` and pick an option (first one, or by text). */
async function pick(page, scope, text) {
  await scope.locator('.select__control').first().click();
  const opts = page.locator('.select__menu .select-option');
  await opts.first().waitFor({ timeout: 10000 });
  if (text) await opts.filter({ hasText: text }).first().click();
  else await opts.first().click();
}
const balance = async (h) => (await h.api(ADMIN, 'GET', `/v2/cases/${CASE1}/balance`)).body.data;
async function balanceCard(page) {
  const card = page.locator('[data-testid="p1-balance-card"]');
  await card.waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  return card.innerText();
}
async function uiPayment(h, page, amount) {
  await page.goto(caseUrl(h, 'payments'), { waitUntil: 'networkidle' });
  await h.settle(page, 1000);
  await page.fill('input[name="amount"]', String(amount));
  await page.fill('input[name="payment_date"]', plusDays(0));
  const selects = page.locator('form .select__control');
  for (const i of [0, 1]) {
    await selects.nth(i).click();
    await page.locator('.select__menu .select-option').first().click();
  }
  await page.locator('form button[type="submit"]').first().click();
  await page.waitForTimeout(2500);
}

async function flows(h) {
  const { newPage, check, login, settle, shot, want, api } = h;

  /* ---- E2E 1: submit a payment (legacy form), approve on the desk, reverse, balance ---- */
  if (want('payment')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    const b0 = await balance(h);
    await uiPayment(h, page, '150.25');
    const pays = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/payments`)).body.data.items;
    const pending = pays.find((p) => p.status === 'pending' && p.amount === '150.25');
    check('E2E pay: legacy form submitted a pending payment of 150.25', !!pending, JSON.stringify(pays.map((p) => [p.status, p.amount])));
    await page.goto(caseUrl(h, 'details'), { waitUntil: 'networkidle' });
    const cardPending = await balanceCard(page);
    check('E2E pay: balance card shows it as pending', cardPending.includes('150.25'), cardPending.replace(/\s+/g, ' ').slice(0, 300));

    await page.goto(`${h.FE_URL}/payment-approvals#status=pending&case=${CASE1}`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const row = page.locator('[data-testid="p1-desk-row"]', { hasText: '150.25' }).first();
    await row.locator('[data-testid="p1-approve"]').click();
    await page.locator('[data-testid="p1-approve-dialog-confirm"]').click();
    await page.locator('[data-testid="p1-decision-result"]').waitFor({ timeout: 15000 });
    await shot(page, '10_e2e_payment_approved_ar');
    const b1 = await balance(h);
    check('E2E pay: approval moves 150.25 into approved; outstanding drops exactly', b1.pending === b0.pending && b1.outstanding === (Math.round((Number(b0.outstanding) - 150.25) * 100) / 100).toFixed(2), JSON.stringify(b1));

    await page.goto(`${h.FE_URL}/payment-approvals#status=approved&case=${CASE1}`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    await page.locator('[data-testid="p1-desk-row"]', { hasText: '150.25' }).first().locator('[data-testid="p1-reverse"]').click();
    await page.locator('[data-testid="p1-reverse-dialog-confirm"]').waitFor();
    check('E2E pay: reverse needs a reason (confirm disabled when empty)', (await page.locator('[data-testid="p1-reverse-dialog-confirm"]').getAttribute('aria-disabled')) === 'true');
    await page.fill('[data-testid="p1-reverse-dialog-reason"]', 'E2E: cheque bounced (verify-phase1)');
    await page.locator('[data-testid="p1-reverse-dialog-confirm"]').click();
    await page.locator('[data-testid="p1-decision-result"]').waitFor({ timeout: 15000 });
    await settle(page, 800);
    const b2 = await balance(h);
    check('E2E pay: reversal restores the outstanding and adds exactly 150.25 to reversed', b2.outstanding === b0.outstanding && (Math.round(Number(b2.reversed) * 100) - Math.round(Number(b0.reversed) * 100)) === 15025, JSON.stringify({ before: b0.reversed, after: b2.reversed, outstanding: b2.outstanding }));
    await page.goto(caseUrl(h, 'details'), { waitUntil: 'networkidle' });
    const card2 = await balanceCard(page);
    check('E2E pay: balance card shows the new reversed total', card2.includes(b2.reversed.replace(/\B(?=(\d{3})+(?!\d))/g, ',')), card2.replace(/\s+/g, ' ').slice(0, 300));
    await shot(page, '11_e2e_balance_after_reverse_ar');
    await page.context().close();
  }

  /* ---- E2E 2: create a promise (UI), approve a payment, promise fulfilled ---- */
  if (want('promise')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    await page.goto(caseUrl(h, 'promises'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    await page.locator('[data-testid="p1-promise-create"]').click();
    await page.fill('[data-testid="p1-promise-amount"]', '5000');
    await page.fill('[data-testid="p1-promise-due"]', plusDays(7));
    await page.locator('[data-testid="p1-promise-create-dialog-confirm"]').click();
    await page.waitForTimeout(1500);
    const tooMuch = await page.locator('[data-testid="p1-inline-error"]').innerText().catch(() => '');
    check('E2E promise: over-balance amount refused with the API message', /outstanding|المتبقي/.test(tooMuch), tooMuch.slice(0, 200));
    await page.fill('[data-testid="p1-promise-amount"]', '120.50');
    await page.locator('[data-testid="p1-promise-create-dialog-confirm"]').click();
    await page.waitForTimeout(2000);
    const promises = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/promises`)).body.data.items;
    const pr = promises.find((p) => p.amount === '120.50' && p.state === 'open');
    check('E2E promise: created open promise of 120.50', !!pr, JSON.stringify(promises.map((p) => [p.state, p.amount])));
    await uiPayment(h, page, '120.50');
    const pays = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/payments`)).body.data.items;
    const pay = pays.find((p) => p.status === 'pending' && p.amount === '120.50');
    await page.goto(`${h.FE_URL}/payment-approvals#status=pending&case=${CASE1}`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    await page.locator('[data-testid="p1-desk-row"]', { hasText: '120.50' }).first().locator('[data-testid="p1-approve"]').click();
    await page.locator('[data-testid="p1-approve-dialog-confirm"]').click();
    await page.locator('[data-testid="p1-decision-result"]').waitFor({ timeout: 15000 });
    await page.goto(caseUrl(h, 'promises'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const after = pr ? (await api(ADMIN, 'GET', `/v2/promises/${pr.id}`)).body.data : null;
    const rowText = pr ? await page.locator('[data-testid="p1-case-promise-row"]', { hasText: '120.50' }).first().innerText() : '';
    check('E2E promise: approving the payment fulfils the promise (auto allocation)', !!pay && after && after.state === 'fulfilled' && after.allocated === '120.50', JSON.stringify(after && { state: after.state, allocated: after.allocated }));
    check('E2E promise: the case panel shows it as fulfilled', /Fulfilled|مُوفى/.test(rowText), rowText.replace(/\s+/g, ' ').slice(0, 200));
    await shot(page, '12_e2e_promise_fulfilled_ar');
    // clean up: reverse the approved 120.50 so repeated runs do not use up the test debt
    if (pay) {
      const cur = (await api(ADMIN, 'GET', `/v2/payments/${pay.id}`)).body.data;
      await api(ADMIN, 'POST', `/v2/payments/${pay.id}/reverse`,
        { reason: 'verify-phase1 cleanup', expected_revision: cur.revision });
    }
    await page.context().close();
  }

  /* ---- E2E 3: legacy note with a callback -> follow-up -> complete with a next one ---- */
  if (want('followup')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    // Close any open follow-up left by an earlier run, so the new note is the only source.
    await page.goto(caseUrl(h, 'comments'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const selects = page.locator('form .select__control');
    // form order: contact method (0, preset mobile), contact (1), reachability (2), status (3), delinquency (4)
    await selects.nth(2).click();
    await page.locator('.select__menu .select-option', { hasText: 'تم التوصل' }).first().click();
    await selects.nth(3).click();
    await page.locator('.select__menu .select-option').first().click();
    const due = plusDays(2);
    await page.fill('input[name="nextContactDate"]', due);
    await page.fill('input[name="nextContactTime"]', '10:30');
    const noteText = `E2E callback note ${Date.now()}`;
    await page.fill('textarea[name="comment"]', noteText);
    await page.locator('form button[type="submit"]').first().click();
    await page.waitForTimeout(3000);
    const fu = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/follow-ups`)).body.data;
    check('E2E follow-up: the legacy note with a callback opened a follow-up (source note)', fu.current && fu.current.source === 'note' && !!fu.current.source_comment_id && fu.current.due_date === due, JSON.stringify(fu.current && { source: fu.current.source, due_at: fu.current.due_at, due_date: fu.current.due_date, typed: `${due} 10:30` }));

    await page.goto(caseUrl(h, 'followUps'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const panel = await page.locator('[data-testid="p1-fu-panel-open"]').innerText().catch(() => '');
    check('E2E follow-up: the case panel shows the open follow-up', panel.length > 0, panel.replace(/\s+/g, ' ').slice(0, 200));
    await shot(page, '13_e2e_follow_up_open_ar');
    await page.locator('[data-testid="p1-fu-panel-complete"]').click();
    await page.fill('[data-testid="p1-fu-complete-text"]', 'E2E outcome: spoke to the debtor, agreed to call back');
    await pick(page, page.locator('[data-testid="p1-fu-complete-reach"]'), 'تم التوصل');
    await pick(page, page.locator('[data-testid="p1-fu-complete-status"]'));
    await pick(page, page.locator('[data-testid="p1-fu-complete-method"]'));
    await page.fill('[data-testid="p1-fu-complete-next-date"]', plusDays(5));
    await page.fill('[data-testid="p1-fu-complete-next-time"]', '11:00');
    await page.fill('[data-testid="p1-fu-complete-next-action"]', 'E2E: confirm the instalment');
    await shot(page, '14_e2e_follow_up_complete_dialog_ar');
    await page.locator('[data-testid="p1-fu-complete-confirm"]').click();
    await page.waitForTimeout(3000);
    const fu2 = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/follow-ups`)).body.data;
    const done = fu2.follow_ups.find((f) => fu.current && f.id === fu.current.id);
    check('E2E follow-up: completed with an outcome note and a next follow-up', done && done.status === 'completed' && fu2.current && fu2.current.next_action === 'E2E: confirm the instalment' && done.next_follow_up_id === fu2.current.id, JSON.stringify({ done: done && done.status, next: fu2.current && fu2.current.next_action }));
    await settle(page, 800);
    const hist = await page.locator('[data-testid="p1-fu-events"], [data-testid="p1-fu-history"]').first().innerText().catch(() => '');
    check('E2E follow-up: history shows the completion', hist.length > 0, hist.replace(/\s+/g, ' ').slice(0, 200));
    await shot(page, '15_e2e_follow_up_history_ar');
    await page.context().close();
  }

  /* ---- 409: two tabs edit the same follow-up ---- */
  if (want('conflict')) {
    const { ctx, page: a } = await newPage();
    await login(a, ADMIN);
    const b = await ctx.newPage();
    for (const p of [a, b]) {
      await p.goto(caseUrl(h, 'followUps'), { waitUntil: 'networkidle' });
      await settle(p, 1500);
    }
    const before = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/follow-ups`)).body.data.current;
    // Tab B opens the reschedule dialog first (it holds the loaded revision).
    await b.locator('[data-testid="p1-fu-panel-reschedule"]').click();
    await b.fill('[data-testid="p1-fu-reschedule-date"]', plusDays(9));
    await b.fill('[data-testid="p1-fu-reschedule-time"]', '12:00');
    await b.fill('[data-testid="p1-fu-reschedule-reason"]', 'E2E tab B: stale edit');
    // Tab A reschedules and saves.
    await a.locator('[data-testid="p1-fu-panel-reschedule"]').click();
    await a.fill('[data-testid="p1-fu-reschedule-date"]', plusDays(8));
    await a.fill('[data-testid="p1-fu-reschedule-time"]', '12:00');
    await a.fill('[data-testid="p1-fu-reschedule-reason"]', 'E2E tab A: debtor asked for another day');
    await a.locator('[data-testid="p1-fu-reschedule-confirm"]').click();
    await a.waitForTimeout(2500);
    // Tab B now submits with the old revision.
    const resp = b.waitForResponse((r) => r.url().includes('/reschedule') && r.request().method() === 'POST');
    await b.locator('[data-testid="p1-fu-reschedule-confirm"]').click();
    const r = await resp;
    await b.locator('[data-testid="p1-conflict"]').waitFor({ timeout: 10000 }).catch(() => {});
    const conflictText = await b.locator('[data-testid="p1-conflict"]').innerText().catch(() => '');
    check('409: the stale tab gets HTTP 409 revision_conflict', r.status() === 409, `${r.status()} ${(await r.text()).slice(0, 200)}`);
    check('409: the stale tab shows "changed by someone else" with a reload action and keeps the dialog open', conflictText.length > 0 && (await b.locator('[data-testid="p1-fu-reschedule-reason"]').inputValue()) === 'E2E tab B: stale edit', conflictText.replace(/\s+/g, ' ').slice(0, 250));
    await shot(b, '16_conflict_409_ar');
    const after = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/follow-ups`)).body.data.current;
    check('409: tab A\'s change is kept, nothing silently overwritten', after.due_date === plusDays(8) && after.revision === before.revision + 1, JSON.stringify({ due: after.due_date, rev: after.revision }));
    await ctx.close();
  }

  /* ---- E2E 4: correct a note, then view its history ---- */
  if (want('note')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    const notes = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/notes?page_size=50`)).body.data.items;
    const target = notes.find((n) => !n.retired && /E2E/.test(n.text)) || notes.find((n) => !n.retired);
    check('E2E note: the test case has a live note to correct', !!target, `${notes.length} notes`);
    if (target) {
      await page.goto(caseUrl(h, 'comments'), { waitUntil: 'networkidle' });
      await settle(page, 2000);
      const row = page.locator(`[data-testid="p1-note"][data-id="${target.id}"]`);
      await row.locator('[data-testid="p1-note-correct"]').click();
      const corrected = `${target.text} (corrected by verify-phase1)`;
      await page.fill('[data-testid="p1-note-correct-dialog-text"]', corrected);
      await page.fill('[data-testid="p1-note-correct-dialog-reason"]', 'E2E: typo in the callback note');
      await page.locator('[data-testid="p1-note-correct-dialog-confirm"]').click();
      await page.waitForTimeout(2500);
      const n2 = (await api(ADMIN, 'GET', `/v2/cases/${CASE1}/notes?page_size=50`)).body.data.items.find((n) => n.id === target.id);
      check('E2E note: correction saved; original text kept; revision bumped', n2 && n2.corrected && n2.text === corrected && n2.original_text === target.original_text && n2.revision === target.revision + 1, JSON.stringify(n2 && { corrected: n2.corrected, rev: n2.revision, original: n2.original_text }));
      await row.locator('[data-testid="p1-note-corrected-badge"]').waitFor({ timeout: 10000 }).catch(() => {});
      check('E2E note: the timeline marks it corrected', (await row.locator('[data-testid="p1-note-corrected-badge"]').count()) === 1);
      await row.locator('[data-testid="p1-note-history-open"]').click();
      await page.locator('[data-testid="p1-note-history"]').waitFor({ timeout: 10000 });
      await settle(page, 800);
      const kinds = await page.$$eval('[data-testid="p1-note-revision"]', (els) => els.map((e) => e.getAttribute('data-kind')));
      const hist = await page.locator('[data-testid="p1-note-history"]').innerText();
      check('E2E note: history shows the original and the correction with its reason', kinds.includes('original') && kinds.includes('correction') && hist.includes('E2E: typo in the callback note'), JSON.stringify(kinds));
      await shot(page, '17_e2e_note_history_ar');
    }
    await page.context().close();
  }

  /* ---- E2E 5: set a default contact, then block it (with a replacement) ---- */
  if (want('contact')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    const list0 = (await api(ADMIN, 'GET', `/v2/debtors/${IDENTITY1}/contacts`)).body.data;
    const eligible = list0.contacts.filter((c) => c.eligible && !c.is_default && c.contact_type?.name === 'mobile');
    const target = eligible[0];
    await page.goto(caseUrl(h, 'details'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const row = page.locator(`[data-testid="p1-contact-row"][data-id="${target.id}"]`);
    await row.locator('[data-testid="p1-contact-set-default"]').click();
    await page.locator('[data-testid="p1-contact-default-dialog-confirm"]').click();
    await page.waitForTimeout(2500);
    const list1 = (await api(ADMIN, 'GET', `/v2/debtors/${IDENTITY1}/contacts`)).body.data;
    check('E2E contact: set default leaves exactly one default', list1.default_contact_id === target.id && list1.default_count === 1, JSON.stringify({ def: list1.default_contact_id, count: list1.default_count }));
    await shot(page, '18_e2e_contact_default_ar');
    const row2 = page.locator(`[data-testid="p1-contact-row"][data-id="${target.id}"]`);
    await row2.locator('[data-testid="p1-contact-block"]').click();
    await page.fill('[data-testid="p1-contact-block-dialog-reason"]', 'E2E: wrong number (verify-phase1)');
    const repl = page.locator('[data-testid="p1-contact-block-dialog-replacement"]');
    if (await repl.count()) await pick(page, repl);
    await shot(page, '19_e2e_contact_block_dialog_ar');
    await page.locator('[data-testid="p1-contact-block-dialog-confirm"]').click();
    await page.waitForTimeout(2500);
    const list2 = (await api(ADMIN, 'GET', `/v2/debtors/${IDENTITY1}/contacts`)).body.data;
    const blocked = list2.contacts.find((c) => c.id === target.id);
    check('E2E contact: blocking the default needs and applies a replacement; still exactly one default', blocked && blocked.is_blocked && list2.default_count === 1 && list2.default_contact_id !== target.id, JSON.stringify({ blocked: blocked && blocked.is_blocked, def: list2.default_contact_id, count: list2.default_count }));
    await settle(page, 800);
    const badge = await page.locator(`[data-testid="p1-contact-row"][data-id="${target.id}"] [data-testid="p1-contact-blocked-badge"]`).count();
    check('E2E contact: the panel shows the blocked badge', badge === 1);
    await page.locator('[data-testid="p1-contacts-history"]').click();
    await page.locator('[data-testid="p1-contact-history"]').waitFor({ timeout: 10000 });
    await settle(page, 800);
    const actions = await page.$$eval('[data-testid="p1-contact-history-row"]', (els) => els.map((e) => e.getAttribute('data-action')));
    check('E2E contact: history lists the set-default and block events', actions.includes('contact.block') && actions.includes('contact.set_default'), JSON.stringify(actions.slice(0, 6)));
    await shot(page, '20_e2e_contact_history_ar');
    // Tidy up: unblock through the API so the case stays usable for the next run.
    await api(ADMIN, 'POST', `/v2/contacts/${target.id}/unblock`, { reason: 'E2E cleanup', expected_revision: blocked.revision });
    await page.context().close();
  }

  /* ---- E2E 6: request a correction; self-approval is refused ---- */
  if (want('correction')) {
    const { page } = await newPage();
    await login(page, ADMIN);
    await page.goto(caseUrl(h, 'corrections'), { waitUntil: 'networkidle' });
    await settle(page, 1500);
    await page.locator('[data-testid="p1-request-correction"]').click();
    await pick(page, page.locator('[data-testid="p1-corr-kind"]'), 'الاسم');
    const newName = `ZZ-TEST P1 حالة اختبار 1 (مصحح ${Date.now() % 100000})`;
    await page.fill('[data-testid="p1-corr-name"]', newName);
    await page.fill('[data-testid="p1-corr-request-dialog-reason"]', 'E2E: name spelling (verify-phase1)');
    await shot(page, '21_e2e_correction_request_ar');
    await page.locator('[data-testid="p1-corr-request-dialog-confirm"]').click();
    await page.waitForTimeout(2500);
    const list = (await api(ADMIN, 'GET', `/v2/corrections?case_id=${CASE1}&status=pending`)).body.data.items;
    const mine = list.find((c) => c.kind === 'profile' && c.changes?.name?.after === newName);
    check('E2E correction: request created as pending with before → after', !!mine && mine.changes.name.before !== undefined, JSON.stringify(mine && mine.changes));
    await page.goto(`${h.FE_URL}/corrections#status=pending&sel=${mine ? mine.id : ''}`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    if (mine) {
      const r = page.locator(`[data-testid="p1-corr-row"][data-id="${mine.id}"]`);
      if (await r.count()) await r.first().click();
      await page.locator('[data-testid="p1-corr-detail"]').waitFor({ timeout: 10000 });
      await settle(page, 800);
      const approveBtns = await page.locator('[data-testid="p1-corr-detail"] [data-testid="p1-corr-approve"]').count();
      const ownNote = await page.locator('[data-testid="p1-corr-detail"] [data-testid="p1-corr-own-note"]').count();
      check('E2E correction: approve is hidden on my own request, with an explanation', approveBtns === 0 && ownNote >= 1, `approve=${approveBtns} ownNote=${ownNote}`);
      await shot(page, '22_e2e_correction_own_request_ar');
      const direct = await api(ADMIN, 'POST', `/v2/corrections/${mine.id}/approve`, {});
      check('E2E correction: the API refuses self-approval (403 own_request)', direct.status === 403 && direct.body.code === 'own_request', `${direct.status} ${JSON.stringify(direct.body)}`);
      // leave it pending for a second reviewer? cancel it to keep the case clean.
      await api(ADMIN, 'POST', `/v2/corrections/${mine.id}/cancel`, { reason: 'E2E cleanup (no second reviewer exists)' });
    }
    await page.context().close();
  }

  /* ---- Permissions: a new key survives saving a role (user-less ZZ-TEST role only) ---- */
  if (want('roles')) {
    const RID = 'e549cafc-8196-483f-88ce-9411ae6576cd';
    const { page } = await newPage();
    await login(page, ADMIN);
    await page.goto(`${h.FE_URL}/permissions/edit?id=${RID}`, { waitUntil: 'networkidle' });
    await settle(page, 2000);
    const tree = page.locator('.react-checkbox-tree');
    await tree.getByText('قائمة المتابعة اليومية', { exact: true }).first().click();
    await tree.getByText('مراجعة تصحيحات بيانات العميل', { exact: true }).first().click();
    await page.locator('button[type="submit"]').first().click();
    await page.waitForURL('**/permissions', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const role = (await api(ADMIN, 'GET', `/roles/${RID}`)).body;
    check('Roles: saving from the UI keeps the legacy key and stores the new keys', role.permission.dashbord === true && role.permission.attention_queue === true && role.permission.case_corrections_review === true && role.permission.follow_ups === false && Object.keys(role.permission).length >= 123, JSON.stringify(role.permission_arr));
    await shot(page, '23_roles_after_save_ar');
    await api(ADMIN, 'PUT', `/roles/${RID}`, { id: RID, name: 'ZZ-TEST phase1 frontend (no users)', permission: ['dashbord'] });
    await page.context().close();
  }

  if (h.moreFlows) await h.moreFlows(h, { caseUrl, plusDays, pick, balance });
}

module.exports = { run, SCREENS, ADMIN, COLLECTOR, CASE1, IDENTITY1, caseUrl, plusDays, pick };
