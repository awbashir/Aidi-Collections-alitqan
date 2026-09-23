/* Sections of scripts/verify-phase2.cjs. */
const fs = require('fs');
const V = require('./verify-phase2.cjs');

const {
  FE, ADMIN, REVIEWER, COLLECTOR, PORTFOLIO, CONTRACT, CASES, OTHER_COLLECTOR, want,
  check, api, sleep, jobOf, waitJob, apiRun, caseOf,
  settle, shot, byTest, pickIn, openMenuEntry, clientsList, tick, waitStage, runJobId, countOf,
  confirmThroughUi, tmpFile, uploadFile, riyadhDate, login, setLang,
} = V;

const stamp = Date.now().toString(36);

/* Bring the test cases to a known state (unlocked, not archived, 1–3 owned by aidi.dev). */
async function normalise() {
  for (const n of [1, 2, 3, 4]) {
    const c = await caseOf(CASES[n].id);
    if (c.is_archive) await apiRun(ADMIN, 'restore', { source: 'selection', case_ids: [CASES[n].id], params: { reason: `verify-phase2 ${stamp}: reset` } });
    const c2 = await caseOf(CASES[n].id);
    if (c2.is_locked) await apiRun(ADMIN, 'unlock', { source: 'selection', case_ids: [CASES[n].id], params: { reason: `verify-phase2 ${stamp}: reset` } });
  }
  const c3 = await caseOf(CASES[3].id);
  if (c3.user_id !== 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf') {
    await apiRun(ADMIN, 'assign', { source: 'selection', case_ids: [CASES[3].id], params: { target_user_id: 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf', reason: `verify-phase2 ${stamp}: reset` } });
  }
}

async function run(h) {
  const { newPage } = h;
  await normalise();

  /* ------------------------------------------------ 1. screens, nav, both languages */
  if (want('screens')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    for (const lang of ['ar', 'en']) {
      await setLang(page, lang);
      await page.goto(`${FE}/jobs`, { waitUntil: 'networkidle' });
      await setLang(page, lang);
      await settle(page, 1500);
      const ok = (await byTest(page, 'p2-jobs').count()) === 1 && (await page.locator('[data-testid="p1-error"],[data-testid="p1-forbidden"]').count()) === 0;
      const dir = await page.evaluate(() => document.documentElement.dir);
      check(`/jobs renders for ${ADMIN} (${lang})`, ok && dir === (lang === 'ar' ? 'rtl' : 'ltr'), `dir=${dir}`);
      const hrefs = await page.$$eval('.side-nav a', (as) => as.map((a) => a.getAttribute('href')));
      check(`nav shows Bulk jobs under Intelligence & control (${lang})`, hrefs.includes('/jobs'));
      const group = await page.$$eval('.side-nav .menu-nav-group', (gs) => gs.map((g) => [...g.querySelectorAll('a')].map((a) => a.getAttribute('href'))));
      check(`Bulk jobs sits in the third group (${lang})`, (group[2] || []).includes('/jobs'), JSON.stringify(group.map((g) => g.length)));
      await shot(page, `01_jobs_${lang}`);
      for (const kind of ['lock', 'assign', 'notes', 'inquiry', 'attribute_corrections', 'round_robin', 'followup_closeout', 'archive', 'restore']) {
        await page.goto(`${FE}/jobs/new/${kind}`, { waitUntil: 'networkidle' });
        await settle(page, 700);
        const w = await byTest(page, 'p2-wizard').getAttribute('data-kind');
        const steps = await page.locator('[data-testid="p2-stepper"] li').count();
        check(`wizard /jobs/new/${kind} renders (${lang})`, w === kind && steps === 9, `kind=${w} steps=${steps}`);
      }
      await shot(page, `02_wizard_source_${lang}`);
    }
    check('no page errors on the Phase 2 screens', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 2. selection lock (ar) → unlock (en) */
  if (want('lockunlock')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    const steps = [
      { lang: 'ar', kind: 'lock', entry: 'p2-entry-lock', reason: `قفل اختبار ${stamp}`, locked: true },
      { lang: 'en', kind: 'unlock', entry: 'p2-entry-unlock', reason: `unlock test ${stamp}`, locked: false },
    ];
    for (const s of steps) {
      await setLang(page, s.lang);
      await clientsList(page);
      await tick(page, CASES[1].acc);
      await tick(page, CASES[2].acc);
      await page.waitForTimeout(300);
      check(`clients list shows the reviewed bulk menu (${s.lang})`, (await byTest(page, 'p2-clients-bulk-toggle').count()) === 1);
      check(`the legacy selected-row buttons stay, labelled classic (${s.lang})`, (await byTest(page, 'p2-classic-actions').count()) === 1);
      if (s.lang === 'ar') {
        await byTest(page, 'p2-clients-bulk-toggle').click();
        await page.waitForTimeout(300);
        await shot(page, '03_clients_bulk_menu_ar');
        await byTest(page, s.entry).click();
      } else {
        await openMenuEntry(page, 'p2-clients-bulk', s.entry);
      }
      await byTest(page, 'p2-wizard').waitFor();
      await settle(page, 500);
      const src = await page.locator('[data-testid="p2-source"] input:checked').evaluate((el) => el.closest('[data-source]').getAttribute('data-source'));
      const count = await byTest(page, 'p2-source-count-selection').innerText();
      check(`${s.kind} (${s.lang}): the wizard starts on the selection (2 cases)`, src === 'selection' && /2/.test(count), `${src} ${count}`);
      await shot(page, `04_${s.kind}_source_${s.lang}`);
      await byTest(page, 'p2-next').click();
      await byTest(page, 'p2-step-params').waitFor();
      const createDisabled = (await byTest(page, 'p2-create').getAttribute('aria-disabled')) === 'true';
      await byTest(page, 'p2-param-reason').fill(s.reason);
      check(`${s.kind} (${s.lang}): "validate" waits for the required reason`, createDisabled);
      await shot(page, `05_${s.kind}_params_${s.lang}`);
      await byTest(page, 'p2-create').click();
      await waitStage(page, 'preview');
      const elig = await countOf(page, 'p2-count-eligible');
      check(`${s.kind} (${s.lang}): preview has 2 eligible rows`, elig === 2, `eligible=${elig}`);
      const rows = await page.locator('[data-testid="p2-row"]').count();
      check(`${s.kind} (${s.lang}): the preview lists the 2 rows`, rows === 2, `rows=${rows}`);
      await shot(page, `06_${s.kind}_preview_${s.lang}`);
      const id = await runJobId(page);
      await confirmThroughUi(page, { shotName: `07_${s.kind}_confirm_${s.lang}` });
      const succeeded = await countOf(page, 'p2-count-succeeded');
      const j = await jobOf(ADMIN, id);
      check(`${s.kind} (${s.lang}): the job succeeded for both cases`, j.status === 'succeeded' && succeeded === 2, `${j.status} succeeded=${succeeded}`);
      const [c1, c2] = [await caseOf(CASES[1].id), await caseOf(CASES[2].id)];
      check(`${s.kind} (${s.lang}): cases 1 and 2 are ${s.locked ? 'locked' : 'unlocked'}`, !!c1.is_locked === s.locked && !!c2.is_locked === s.locked);
      await shot(page, `08_${s.kind}_results_${s.lang}`);
    }
    check('no page errors in lock → unlock', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 3. current-filter source = the list's count */
  if (want('filtercount')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'en');
    await clientsList(page);
    const listTotal = (await page.locator('tbody tr', { hasText: 'ZZTEST-P1-' }).count());
    await openMenuEntry(page, 'p2-clients-bulk', 'p2-entry-lock');
    await byTest(page, 'p2-wizard').waitFor();
    await settle(page, 500);
    const src = await page.locator('[data-testid="p2-source"] input:checked').evaluate((el) => el.closest('[data-source]').getAttribute('data-source'));
    check('with no selection the wizard offers the current filter', src === 'filter', src);
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-param-reason').fill(`filter count check ${stamp}`);
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    const cmp = byTest(page, 'p2-filter-compare');
    const listN = Number(await cmp.getAttribute('data-list'));
    const prevN = Number(await cmp.getAttribute('data-preview'));
    const id = await runJobId(page);
    const j = await jobOf(ADMIN, id);
    check('filter source: preview rows = the count the list shows', listN === prevN && prevN === listTotal && listTotal === 4, `list=${listN} preview=${prevN} rows=${listTotal}`);
    check('filter source: the stored query is the list query (portfolio filter, no paging)', /customeFilterPortfoliosValue/.test(j.source_summary.query || '') && !/pageSize=/.test(j.source_summary.query || ''), j.source_summary.query);
    await shot(page, '09_filter_source_preview_en');
    await byTest(page, 'p2-cancel-btn').first().click();
    await byTest(page, 'p2-cancel-dialog-confirm').click();
    await waitStage(page, 'cancelled_prepare');
    check('filter-source preview cancelled without running', (await jobOf(ADMIN, id)).status === 'cancelled');
    check('no page errors (filter source)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 4. spreadsheet assign (account → login), partial, then back */
  if (want('assign')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'ar');
    await clientsList(page);
    await openMenuEntry(page, 'p2-clients-bulk', 'p2-entry-file-assign');
    await byTest(page, 'p2-wizard').waitFor();
    const src = await page.locator('[data-testid="p2-source"] input:checked').evaluate((el) => el.closest('[data-source]').getAttribute('data-source'));
    check('assign from a spreadsheet starts on the spreadsheet source', src === 'spreadsheet', src);
    await byTest(page, 'p2-next').click();
    const csv = tmpFile('assign.csv', `Account,Login\n${CASES[3].acc},${OTHER_COLLECTOR.login}\nZZTEST-NOPE-${stamp},${OTHER_COLLECTOR.login}\n`);
    await uploadFile(page, csv);
    const scan = await byTest(page, 'p2-not-scanned').getAttribute('data-scan');
    const rowsN = Number(await byTest(page, 'p2-upload-rows').getAttribute('data-value'));
    check('upload inspection: "not scanned" warning, 2 rows', scan === 'not_scanned' && rowsN === 2, `${scan} ${rowsN}`);
    await shot(page, '10_assign_upload_ar');
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-step-mapping').waitFor();
    const mapped = await page.locator('[data-mapped-to]').count();
    check('mapping: account and login were matched from the headers', mapped === 2, `mapped=${mapped}`);
    await shot(page, '11_assign_mapping_ar');
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-step-params').waitFor();
    await byTest(page, 'p2-param-reason').fill(`اختبار تخصيص ${stamp}`);
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    const e = await countOf(page, 'p2-count-eligible');
    const er = await countOf(page, 'p2-count-error');
    const nf = await page.locator('[data-testid="p2-by-code"] li[data-code="not_found_or_not_permitted"]').count();
    check('assign preview: 1 eligible, 1 error (not found or not permitted)', e === 1 && er === 1 && nf === 1, `e=${e} err=${er}`);
    const target = await page.locator('[data-testid="p2-row"][data-eligibility="eligible"] [data-target]').getAttribute('data-target');
    check('assign preview row shows the target login', target === OTHER_COLLECTOR.login, target);
    const byTarget = await page.locator('[data-testid="p2-target-row"]').count();
    check('assign preview shows the per-collector summary (kind_summary)', byTarget === 1, `rows=${byTarget}`);
    await shot(page, '12_assign_preview_ar');
    const r = await confirmThroughUi(page, { partial: true, shotName: '13_assign_confirm_partial_ar' });
    check('accept-partial: confirm stays disabled until "run only the eligible rows" is ticked', r.disabledBefore === true);
    const id = await runJobId(page);
    const j = await jobOf(ADMIN, id);
    check('assign job confirmed with accept_partial and succeeded for 1 case', j.confirmation && j.confirmation.accept_partial === true && j.counts.succeeded === 1, JSON.stringify(j.counts));
    check('case 3 is now owned by the other collector', (await caseOf(CASES[3].id)).user_id === OTHER_COLLECTOR.id);
    await shot(page, '14_assign_results_ar');

    // assign back through the collector picker (selection source)
    await setLang(page, 'en');
    await clientsList(page);
    await tick(page, CASES[3].acc);
    await openMenuEntry(page, 'p2-clients-bulk', 'p2-entry-assign');
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    await pickIn(page, 'p2-param-collector', 'aidi.dev');
    await byTest(page, 'p2-param-reason').fill(`assign back ${stamp}`);
    await shot(page, '15_assign_back_params_en');
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    await confirmThroughUi(page);
    check('assign back: case 3 is owned by aidi.dev again', (await caseOf(CASES[3].id)).user_id === 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf');
    await shot(page, '16_assign_back_results_en');
    check('no page errors (assign)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 5. archive case 4, then restore it */
  if (want('archive')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'ar');
    await clientsList(page);
    await tick(page, CASES[4].acc);
    await openMenuEntry(page, 'p2-clients-bulk', 'p2-entry-archive');
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    await pickIn(page, 'p2-param-archive-reason');
    await byTest(page, 'p2-param-note').fill(`أرشفة اختبار ${stamp}`);
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    check('archive preview: case 4 eligible', (await countOf(page, 'p2-count-eligible')) === 1);
    await confirmThroughUi(page, { shotName: '17_archive_confirm_ar' });
    check('case 4 is archived', !!(await caseOf(CASES[4].id)).is_archive);
    await shot(page, '18_archive_results_ar');

    // restore from the archive screen: by selection when the case is on the first page, else from a spreadsheet
    await setLang(page, 'en');
    await page.goto(`${FE}/archive`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    check('archive screen shows the reviewed restore menu', (await byTest(page, 'p2-archive-bulk-toggle').count()) === 1);
    const onPage = await page.locator('tbody tr', { hasText: CASES[4].acc }).count();
    if (onPage > 0) {
      await tick(page, CASES[4].acc);
      await openMenuEntry(page, 'p2-archive-bulk', 'p2-entry-restore');
    } else {
      await openMenuEntry(page, 'p2-archive-bulk', 'p2-entry-file-restore');
    }
    await byTest(page, 'p2-wizard').waitFor();
    const how = onPage > 0 ? 'selection' : 'spreadsheet';
    await byTest(page, 'p2-next').click();
    if (how === 'spreadsheet') {
      await uploadFile(page, tmpFile('restore.csv', `Account\n${CASES[4].acc}\n`));
      await byTest(page, 'p2-next').click();
      await byTest(page, 'p2-next').click();
    }
    await byTest(page, 'p2-param-reason').fill(`restore test ${stamp}`);
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    await shot(page, '19_restore_preview_en');
    check(`restore (${how}) preview: case 4 eligible`, (await countOf(page, 'p2-count-eligible')) === 1);
    await confirmThroughUi(page);
    check(`case 4 restored from the archive screen (${how})`, !(await caseOf(CASES[4].id)).is_archive);
    await shot(page, '20_restore_results_en');
    check('no page errors (archive / restore)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 6. notes batch with a callback → follow-up */
  if (want('notes')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'en');
    await clientsList(page);
    await tick(page, CASES[1].acc);
    await openMenuEntry(page, 'p2-clients-bulk', 'p2-entry-notes');
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    await pickIn(page, 'p2-param-note-contract', 'أيجارة');
    await pickIn(page, 'p2-param-status');
    await pickIn(page, 'p2-param-method');
    await byTest(page, 'p2-param-comment').fill(`ZZ-TEST bulk note ${stamp}`);
    const date = riyadhDate(2);
    await byTest(page, 'p2-param-cb-date').fill(date);
    await byTest(page, 'p2-param-cb-time').fill('10:30');
    const instant = await byTest(page, 'p2-param-cb-instant').innerText();
    check('notes: the callback is sent with the +03:00 offset', instant === `${date}T10:30:00+03:00`, instant);
    await shot(page, '21_notes_params_en');
    let sent = null;
    page.on('request', (r) => {
      if (r.url().endsWith('/v2/bulk/notes') && r.method() === 'POST') sent = JSON.parse(r.postData() || '{}');
    });
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    check('notes: the create request carried next_contact_date with +03:00', sent && sent.params && sent.params.next_contact_date === `${date}T10:30:00+03:00`, JSON.stringify(sent && sent.params));
    check('notes preview: case 1 eligible', (await countOf(page, 'p2-count-eligible')) === 1);
    await confirmThroughUi(page);
    const fu = (await api(ADMIN, 'GET', `/v2/cases/${CASES[1].id}/follow-ups`)).body.data.current;
    const expectedZ = new Date(`${date}T10:30:00+03:00`).toISOString().replace('.000Z', 'Z');
    check('notes: an open follow-up was created at the callback time', fu && fu.status === 'open' && fu.source === 'note' && fu.due_at === expectedZ, JSON.stringify(fu && { due_at: fu.due_at, source: fu.source }));
    check('notes results link to the case follow-ups', (await byTest(page, 'p2-row-follow-up').count()) === 1);
    await shot(page, '22_notes_results_en');
    await byTest(page, 'p2-row-follow-up').click();
    await settle(page, 1500);
    await shot(page, '23_notes_case_follow_up_en');
    check('no page errors (notes)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 7. spreadsheet inquiry with an unknown account */
  if (want('inquiry')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'ar');
    await page.goto(`${FE}/client_query`, { waitUntil: 'networkidle' });
    await settle(page, 800);
    await byTest(page, 'p2-entry-inquiry').click();
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    await uploadFile(page, tmpFile('inquiry.csv', `Account,Ref\n${CASES[1].acc},r1\nZZ-UNKNOWN-${stamp},r2\n`));
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-step-mapping').waitFor();
    await byTest(page, 'p2-next').click();
    await shot(page, '24_inquiry_params_ar');
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'results');
    const matched = await page.locator('[data-testid="p2-inq-row"][data-eligibility="eligible"]').count();
    const nf = await page.locator('[data-testid="p2-inq-row"][data-code="not_found_or_not_permitted"]').count();
    check('inquiry: read-only, straight to results (1 match, 1 not_found_or_not_permitted)', matched === 1 && nf === 1, `matched=${matched} nf=${nf}`);
    check('inquiry: the match links to the case', (await byTest(page, 'p2-row-case-link').count()) >= 1);
    const [dl] = await Promise.all([page.waitForEvent('download'), byTest(page, 'p2-download-results').click()]);
    const text = fs.readFileSync(await dl.path(), 'utf8');
    check('inquiry: results CSV downloads (formula-safe CSV with the source columns)', /row_no/.test(text) && /source:Ref/.test(text) && /not_found_or_not_permitted/.test(text), dl.suggestedFilename());
    await shot(page, '25_inquiry_results_ar');
    check('no page errors (inquiry)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 8. attribute corrections → pending → rejected by the reviewer */
  if (want('attr')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'en');
    await page.goto(`${FE}/corrections`, { waitUntil: 'networkidle' });
    await settle(page, 800);
    await byTest(page, 'p2-entry-attr').click();
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    const key = `zz_p2_${stamp}`;
    await uploadFile(page, tmpFile('attr.csv', `Account,ZZ value\n${CASES[2].acc},v-${stamp}\n`));
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-attr-key').first().fill(key);
    await pickIn(page, 'p2-attr-column', 'ZZ value');
    await shot(page, '26_attr_mapping_en');
    await byTest(page, 'p2-next').click();
    await byTest(page, 'p2-param-reason').fill(`ZZ-TEST attribute file ${stamp}`);
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview');
    check('attribute corrections preview: 1 eligible with before → after', (await countOf(page, 'p2-count-eligible')) === 1 && (await page.locator(`[data-attr="${key}"]`).count()) === 1);
    await shot(page, '27_attr_preview_en');
    await confirmThroughUi(page);
    const corrId = await byTest(page, 'p2-row-correction').getAttribute('data-correction');
    const corr = (await api(ADMIN, 'GET', `/v2/corrections/${corrId}`)).body.data;
    check('attribute corrections: a pending attribute correction exists (not a direct edit)', corr && corr.status === 'pending' && corr.kind === 'attribute' && corr.changes && corr.changes[key], JSON.stringify(corr && { status: corr.status, kind: corr.kind }));
    const c2 = await caseOf(CASES[2].id);
    check('attribute corrections: the case itself is unchanged', !(c2.attribute || {})[key]);
    await shot(page, '28_attr_results_en');
    await page.context().close();

    // clean up: the reviewer rejects it in the Phase 1 UI
    const rv = await newPage();
    await login(rv.page, REVIEWER);
    await setLang(rv.page, 'en');
    await rv.page.goto(`${FE}/corrections#status=pending&sel=${corrId}`, { waitUntil: 'networkidle' });
    await settle(rv.page, 1500);
    await byTest(rv.page, 'p1-corr-reject').click();
    await byTest(rv.page, 'p1-corr-reject-dialog-reason').fill(`verify-phase2 cleanup ${stamp}`);
    await byTest(rv.page, 'p1-corr-reject-dialog-confirm').click();
    await settle(rv.page, 1500);
    const after = (await api(ADMIN, 'GET', `/v2/corrections/${corrId}`)).body.data;
    check('the reviewer rejected the bulk-requested correction (cleanup)', after.status === 'rejected', after.status);
    await shot(rv.page, '29_attr_rejected_by_reviewer_en');
    check('no page errors (attribute corrections)', errors.length === 0 && rv.errors.length === 0, [...errors, ...rv.errors].join(' | '));
    await rv.page.context().close();
  }

  /* ------------------------------------------------ 9. close-out: preview only, then cancel */
  if (want('closeout')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'ar');
    await page.goto(`${FE}/follow-ups`, { waitUntil: 'networkidle' });
    await settle(page, 800);
    await byTest(page, 'p2-entry-closeout').click();
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    await shot(page, '30_closeout_params_ar');
    await byTest(page, 'p2-create').click();
    await waitStage(page, 'preview', 120000);
    const id = await runJobId(page);
    const eligible = await countOf(page, 'p2-count-eligible');
    const excluded = await countOf(page, 'p2-count-excluded');
    check('close-out preview ready (no confirmation sent)', eligible > 0, `eligible=${eligible} excluded=${excluded}`);
    await shot(page, '31_closeout_preview_ar');
    await byTest(page, 'p2-cancel-btn').first().click();
    await byTest(page, 'p2-cancel-dialog-reason').fill('verify-phase2: preview only, decision pending');
    await byTest(page, 'p2-cancel-dialog-confirm').click();
    await waitStage(page, 'cancelled_prepare');
    const j = await jobOf(ADMIN, id);
    check('close-out job cancelled; never confirmed, nothing processed', j.status === 'cancelled' && !j.confirmation && j.counts.processed === 0, `${j.status} processed=${j.counts.processed}`);
    await shot(page, '32_closeout_cancelled_ar');
    check('no page errors (close-out)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 10. round-robin and portfolio archive: preview only */
  if (want('roundrobin')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'en');
    await page.goto(`${FE}/portfolios`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const toggle = byTest(page, `p2-portfolio-bulk-${PORTFOLIO}-toggle`);
    let launched = false;
    if (await toggle.count()) {
      await toggle.click();
      await byTest(page, 'p2-entry-rr-portfolio').click();
      launched = true;
    } else {
      await page.goto(`${FE}/jobs/new/round_robin`, { waitUntil: 'networkidle' });
    }
    check('portfolios screen offers the reviewed round-robin / archive entries', launched);
    await byTest(page, 'p2-wizard').waitFor();
    await byTest(page, 'p2-next').click();
    if (!launched) await pickIn(page, 'p2-param-portfolio', 'ZZ-TEST');
    await pickIn(page, 'p2-param-rr-add', OTHER_COLLECTOR.login);
    await pickIn(page, 'p2-param-rr-add', 'aidi.dev');
    const order = await page.locator('[data-testid="p2-param-rr-list"] li').evaluateAll((ls) => ls.map((l) => l.getAttribute('data-collector')));
    check('round-robin: collectors listed in the chosen order', order[0] === OTHER_COLLECTOR.id && order.length === 2, JSON.stringify(order));
    await page.locator('[data-testid="p2-param-rr-list"] li').nth(1).getByRole('button').first().click(); // move aidi.dev up
    const order2 = await page.locator('[data-testid="p2-param-rr-list"] li').evaluateAll((ls) => ls.map((l) => l.getAttribute('data-collector')));
    check('round-robin: reordering moves the collector up', order2[0] === 'd7dced3b-3a75-4f32-aea6-3b5dce0aeaaf', JSON.stringify(order2));
    await shot(page, '33_rr_params_en');
    await byTest(page, 'p2-create').click();
    await waitStage(page, ['preview', 'cancelled_prepare', 'failed_prepare']);
    const planned = await page.locator('[data-testid="p2-target-row"]').evaluateAll((rs) => rs.map((r) => [r.getAttribute('data-login'), Number(r.getAttribute('data-planned'))]));
    check('round-robin preview: per-collector plan in the given order', planned.length === 2 && planned[0][0] === 'aidi.dev', JSON.stringify(planned));
    await shot(page, '34_rr_preview_en');
    await byTest(page, 'p2-cancel-btn').first().click();
    await byTest(page, 'p2-cancel-dialog-confirm').click();
    await waitStage(page, 'cancelled_prepare');
    check('round-robin preview cancelled', true);

    if (launched) {
      await page.goto(`${FE}/portfolios`, { waitUntil: 'networkidle' });
      await settle(page, 1500);
      await byTest(page, `p2-portfolio-bulk-${PORTFOLIO}-toggle`).click();
      await byTest(page, 'p2-entry-archive-portfolio').click();
      await byTest(page, 'p2-wizard').waitFor();
      const src = await page.locator('[data-testid="p2-source"] input:checked').evaluate((el) => el.closest('[data-source]').getAttribute('data-source'));
      check('archive portfolio: the wizard starts on the portfolio source', src === 'portfolio', src);
      await byTest(page, 'p2-next').click();
      await pickIn(page, 'p2-param-archive-reason');
      await byTest(page, 'p2-create').click();
      await waitStage(page, 'preview');
      const total = await countOf(page, 'p2-count-total');
      check('archive portfolio preview covers the 4 ZZ-TEST cases', total === 4, `total=${total}`);
      await shot(page, '35_archive_portfolio_preview_en');
      await byTest(page, 'p2-cancel-btn').first().click();
      await byTest(page, 'p2-cancel-dialog-confirm').click();
      await waitStage(page, 'cancelled_prepare');
    }
    check('no page errors (round-robin / portfolio)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 11. jobs screen: list, detail, artifact download */
  if (want('jobs')) {
    const { page, errors } = await newPage();
    await login(page, ADMIN);
    await setLang(page, 'en');
    await page.goto(`${FE}/jobs#kind=lock`, { waitUntil: 'networkidle' });
    await settle(page, 1500);
    const rows = page.locator('[data-testid="p2-job-row"]');
    const n = await rows.count();
    const kinds = await rows.evaluateAll((rs) => [...new Set(rs.map((r) => r.getAttribute('data-kind')))]);
    check('jobs screen: the kind filter from the hash applies', n > 0 && kinds.length === 1 && kinds[0] === 'lock', `${n} ${kinds}`);
    const chipAll = Number(await page.locator('[data-testid="p2-jobs-chips"] [data-status="all"]').getAttribute('data-count'));
    const chipSum = await page.locator('[data-testid="p2-jobs-chips"] button:not([data-status="all"])').evaluateAll((bs) => bs.reduce((a, b) => a + Number(b.getAttribute('data-count') || 0), 0));
    check('jobs screen: counts_by_status chips add up to "All"', chipAll === chipSum && chipAll > 0, `${chipAll} vs ${chipSum}`);
    // a succeeded lock job with files
    const done = page.locator('[data-testid="p2-job-row"][data-status="succeeded"]').first();
    const jid = await done.getAttribute('data-id');
    await done.click();
    await byTest(page, 'p2-job-detail').waitFor();
    await settle(page, 800);
    check('jobs screen: the selected job is kept in the hash', (await page.evaluate(() => location.hash)).includes(`sel=${jid}`));
    const attempts = await page.locator('[data-testid="p2-attempt-row"]').count();
    check('job detail: attempts are listed (validation + execution)', attempts >= 2, `attempts=${attempts}`);
    const counts = await page.locator('[data-testid="p2-job-counts"] [data-count]').count();
    check('job detail: row counts shown', counts === 8);
    const [dl] = await Promise.all([page.waitForEvent('download'), byTest(page, 'p2-job-detail').locator('[data-testid="p2-download-results"]').click()]);
    const text = fs.readFileSync(await dl.path(), 'utf8');
    check('job detail: the results CSV downloads', /^﻿?row_no,/.test(text) && /succeeded/.test(text), dl.suggestedFilename());
    await shot(page, '36_jobs_detail_en');
    await page.reload({ waitUntil: 'networkidle' });
    await settle(page, 1200);
    check('jobs screen: reload keeps the filter and the open job', (await byTest(page, 'p2-job-detail').getAttribute('data-id')) === jid);
    await byTest(page, 'p2-job-resume').click();
    await waitStage(page, 'results');
    check('job detail "open results" resumes the wizard at the results step', (await page.locator('[data-testid="p2-stepper"] [data-step="results"]').getAttribute('data-state')) === 'current');
    await setLang(page, 'ar');
    await page.goto(`${FE}/jobs`, { waitUntil: 'networkidle' });
    await settle(page, 1200);
    await shot(page, '37_jobs_list_ar');
    // resume a ready job at the preview/confirm step
    const c = await api(ADMIN, 'POST', '/v2/bulk/lock', { source: 'selection', case_ids: [CASES[3].id], params: { reason: `resume check ${stamp}` } });
    const ready = await waitJob(ADMIN, c.body.data.id, ['ready']);
    await page.goto(`${FE}/jobs#sel=${ready.id}`, { waitUntil: 'networkidle' });
    await settle(page, 1200);
    await byTest(page, 'p2-job-resume').click();
    const stage = await waitStage(page, 'preview');
    check('a ready job resumes the wizard at the preview (review and confirm)', stage === 'preview' && (await byTest(page, 'p2-to-confirm').count()) === 1);
    await api(ADMIN, 'POST', `/v2/bulk/jobs/${ready.id}/cancel`, { reason: 'verify-phase2 resume check' });
    check('no page errors (jobs screen)', errors.length === 0, errors.join(' | '));
    await page.context().close();
  }

  /* ------------------------------------------------ 12. 409 conflicts */
  if (want('conflict')) {
    // (a) two tabs: B cancels while A is on the confirm step → A's confirm gets 409 job_not_ready
    const A = await newPage();
    const B = await newPage();
    await login(A.page, ADMIN);
    await login(B.page, ADMIN);
    await setLang(A.page, 'en');
    await setLang(B.page, 'en');
    const c = await api(ADMIN, 'POST', '/v2/bulk/lock', { source: 'selection', case_ids: [CASES[3].id, 'not-a-uuid'], params: { reason: `conflict test ${stamp}` } });
    const ready = await waitJob(ADMIN, c.body.data.id, ['ready']);
    const noPartial = await api(ADMIN, 'POST', `/v2/bulk/jobs/${ready.id}/confirm`, { expected_revision: ready.revision });
    check('API: confirm without accept_partial → 422 accept_partial_required', noPartial.status === 422 && noPartial.body.code === 'accept_partial_required', `${noPartial.status} ${noPartial.body.code}`);
    await A.page.goto(`${FE}/jobs/run/${ready.id}`, { waitUntil: 'networkidle' });
    await waitStage(A.page, 'preview');
    await byTest(A.page, 'p2-to-confirm').click();
    await waitStage(A.page, 'confirm');
    await byTest(A.page, 'p2-accept-partial').locator('input[type="checkbox"]').check();
    await B.page.goto(`${FE}/jobs/run/${ready.id}`, { waitUntil: 'networkidle' });
    await waitStage(B.page, 'preview');
    await byTest(B.page, 'p2-cancel-btn').first().click();
    await byTest(B.page, 'p2-cancel-dialog-confirm').click();
    await waitStage(B.page, 'cancelled_prepare');
    let status409 = null;
    A.page.on('response', (r) => {
      if (r.url().endsWith(`/v2/bulk/jobs/${ready.id}/confirm`)) status409 = r.status();
    });
    await byTest(A.page, 'p2-confirm-btn').click();
    await byTest(A.page, 'p1-conflict').waitFor({ timeout: 15000 });
    const txt = await byTest(A.page, 'p1-conflict').innerText();
    check('two tabs: the stale confirm gets HTTP 409 and shows "changed by someone else"', status409 === 409 && /someone else/i.test(txt), `${status409} ${txt.slice(0, 160)}`);
    await shot(A.page, '38_conflict_job_not_ready_en');
    await A.page.getByText('Reload the latest version').click();
    const st = await waitStage(A.page, 'cancelled_prepare');
    check('two tabs: reload shows the job as cancelled; nothing ran', st === 'cancelled_prepare' && !(await caseOf(CASES[3].id)).is_locked);
    await B.page.context().close();

    // (b) a stale preview version → 409 revision_conflict (the request is sent with the previous revision)
    const c2 = await api(ADMIN, 'POST', '/v2/bulk/lock', { source: 'selection', case_ids: [CASES[3].id], params: { reason: `revision conflict ${stamp}` } });
    const ready2 = await waitJob(ADMIN, c2.body.data.id, ['ready']);
    await A.page.goto(`${FE}/jobs/run/${ready2.id}#view=confirm`, { waitUntil: 'networkidle' });
    await waitStage(A.page, 'confirm');
    await A.page.route(`**/v2/bulk/jobs/${ready2.id}/confirm`, (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      body.expected_revision = Number(body.expected_revision) - 1;
      return route.fallback({ postData: JSON.stringify(body) });
    });
    await byTest(A.page, 'p2-confirm-btn').click();
    await byTest(A.page, 'p1-conflict').waitFor({ timeout: 15000 });
    const txt2 = await byTest(A.page, 'p1-conflict').innerText();
    check('stale version: 409 revision_conflict is shown with both versions, nothing ran', /version/i.test(txt2) && (await jobOf(ADMIN, ready2.id)).status === 'ready', txt2.slice(0, 200));
    await shot(A.page, '39_conflict_revision_en');
    await A.page.unroute(`**/v2/bulk/jobs/${ready2.id}/confirm`);
    await api(ADMIN, 'POST', `/v2/bulk/jobs/${ready2.id}/cancel`, { reason: 'verify-phase2 conflict check' });
    check('no page errors (conflicts)', A.errors.length === 0, A.errors.join(' | '));
    await A.page.context().close();
  }

  /* ------------------------------------------------ 13. collector: no bulk entry points, 403 / access denied */
  if (want('gating')) {
    const { page } = await newPage();
    await login(page, COLLECTOR);
    await setLang(page, 'en');
    await page.goto(`${FE}/clients`, { waitUntil: 'networkidle' });
    await settle(page, 2000);
    check(`${COLLECTOR}: no reviewed bulk menu on the clients list`, (await byTest(page, 'p2-clients-bulk-toggle').count()) === 0);
    await shot(page, '40_collector_clients_en');
    for (const p of ['/archive', '/portfolios', '/follow-ups', '/corrections']) {
      await page.goto(FE + p, { waitUntil: 'networkidle' });
      await settle(page, 800);
      check(`${COLLECTOR}: ${p} → access denied (so no bulk entry there)`, page.url().endsWith('/access-denied'), page.url());
    }
    for (const kind of ['lock', 'assign', 'archive', 'notes', 'followup_closeout', 'attribute_corrections']) {
      await page.goto(`${FE}/jobs/new/${kind}`, { waitUntil: 'networkidle' });
      await settle(page, 800);
      check(`${COLLECTOR}: /jobs/new/${kind} shows the 403 state`, (await byTest(page, 'p1-forbidden').count()) === 1 && (await byTest(page, 'p2-next').count()) === 0);
    }
    await shot(page, '41_collector_forbidden_wizard_en');
    const r = await api(COLLECTOR, 'POST', '/v2/bulk/lock', { source: 'selection', case_ids: [CASES[1].id], params: { reason: 'x' } });
    check(`${COLLECTOR}: POST /v2/bulk/lock → 403`, r.status === 403, `${r.status}`);
    /* The collector role holds client_query, so the spreadsheet inquiry and the jobs
     * screen are legitimately available (GET /v2/jobs answers 200 for it). */
    await page.goto(`${FE}/client_query`, { waitUntil: 'networkidle' });
    await settle(page, 1000);
    const inq = await byTest(page, 'p2-entry-inquiry').count();
    await page.goto(`${FE}/jobs`, { waitUntil: 'networkidle' });
    await settle(page, 1000);
    const jobsOk = (await byTest(page, 'p2-jobs').count()) === 1;
    const foreign = await page.locator('[data-testid="p2-job-row"]').count();
    check(`${COLLECTOR}: holds client_query → inquiry entry and jobs screen (own jobs only) are shown, mirroring the API`, inq === 1 && jobsOk && foreign === (await api(COLLECTOR, 'GET', '/v2/jobs?page_size=25')).body.data.items.length, `inquiry=${inq} jobs=${jobsOk} rows=${foreign}`);
    await shot(page, '42_collector_jobs_en');
    await page.context().close();
  }
}

module.exports = { run };
