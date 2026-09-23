/* End-to-end check of the async Excel-import flow.
 *
 *   cd /Users/awbashir/Development/Aidi-Collections/old/tools && node e2e_import.js
 *
 * Builds a two-row .xlsx with account numbers that do not exist, drives the
 * "إضافة/تحديث خاصية بملف اكسل" dialog on /clients, and captures the queued /
 * running progress view and the final result. The first few `GET /jobs/<id>`
 * polls are rewritten to a "running" payload so the progress state is
 * screenshot-able even though a 2-row job finishes in milliseconds — every
 * later poll goes through to the real API untouched.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright-core');
const ExcelJS = require(path.join(__dirname, '..', 'CAWP-frontend', 'node_modules', 'exceljs'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const OUT = process.env.OUT_DIR || path.join(__dirname, '..', 'logs', 'screens', 'final');
const XLSX = path.join(__dirname, '..', 'logs', 'e2e_import.xlsx');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const shot = async (page, name) => {
    const file = path.join(OUT, `e2e_${name}.png`);
    await page.screenshot({ path: file });
    console.log('  shot ->', file);
};

async function makeWorkbook() {
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet('rows');
    ws.addRow(['acc', 'val']);
    ws.addRow(['E2E-NO-SUCH-ACCOUNT-1', '111']);
    ws.addRow(['E2E-NO-SUCH-ACCOUNT-2', '222']);
    fs.mkdirSync(path.dirname(XLSX), { recursive: true });
    await wb.xlsx.writeFile(XLSX);
    console.log('xlsx ->', XLSX);
}

/** react-select: click the control, then the option with this exact label.
 *  Scoped to the dialog — the DataTable footer has a page-size select too. */
async function pickOption(page, controlIndex, optionLabel) {
    const control = page.locator('.dialog .select__control').nth(controlIndex);
    await control.click();
    const option = page
        .locator('.select-option')
        .filter({ hasText: new RegExp(`^${optionLabel}$`) })
        .first();
    await option.waitFor({ state: 'visible', timeout: 10000 });
    await option.click();
}

(async () => {
    fs.mkdirSync(OUT, { recursive: true });
    await makeWorkbook();

    const browser = await chromium.launch({ executablePath: CHROME, headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ar' });
    const page = await ctx.newPage();

    const consoleErrors = [];
    page.on('console', (m) => {
        if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200));
    });
    page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 200)));
    page.on('response', async (r) => {
        const u = r.url();
        if (/upload|byexcel|jobs\//i.test(u)) {
            let body = '';
            try { body = (await r.text()).slice(0, 300); } catch (e) { /* stream */ }
            console.log(`  << ${r.status()} ${u.replace(/^https?:\/\/[^/]+/, '')} ${body}`);
        }
    });

    // Hold the job at "running" for the first few polls so the progress view is
    // observable; then stop intercepting and let the real result through.
    let fakePolls = 3;
    let jobId = null;
    await page.route('**/jobs/**', async (route) => {
        const url = route.request().url();
        jobId = jobId || url.split('/jobs/')[1];
        if (fakePolls-- > 0) {
            const progress = [0, 35, 78][2 - fakePolls] ?? 50;
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    id: jobId, name: 'attribute-by-excel', owner: 'e2e',
                    status: fakePolls === 2 ? 'queued' : 'running',
                    progress, message: fakePolls === 2 ? null : `${progress * 80}/8000`,
                    result: null, error: null,
                    created_at: Date.now() / 1000, started_at: Date.now() / 1000, finished_at: null,
                }),
            });
        }
        return route.continue();
    });

    console.log('sign in');
    await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
    await page.fill('input[name="username"]', 'aidi.dev');
    await page.fill('input[name="password"]', 'Aidi@2026');
    await page.click('button[type="submit"]');
    await page.waitForURL('**/home', { timeout: 30000 });

    console.log('open /clients');
    await page.goto(FE + '/clients', { waitUntil: 'networkidle', timeout: 90000 });
    await page.waitForTimeout(1500);

    console.log('open the Excel menu');
    await page.getByRole('button', { name: /عمليات ملف إكسل/ }).click();
    await page.waitForTimeout(400);
    await shot(page, '00_excel_menu');

    await page.locator('li,div').filter({ hasText: /^إضافة\/تحديث خاصية بملف اكسل$/ }).first().click();
    await page.waitForTimeout(800);

    console.log('upload the workbook');
    await page.setInputFiles('.dialog input[type="file"]', XLSX);
    await page.waitForTimeout(3000);
    await shot(page, '00b_uploaded');

    console.log('fill the form');
    await pickOption(page, 1, 'acc');   // identifier column
    await pickOption(page, 2, 'val');   // value column
    await page.locator('input[name="attribute_key"]').fill('e2e_phase_c');
    await page.waitForTimeout(300);
    await shot(page, '01_dialog_ready');

    console.log('submit');
    await page.getByRole('button', { name: /^حفظ$|^Save$/ }).click();

    // The 202 lands in well under a second; the faked polls hold "running".
    await page.waitForSelector('text=يعمل في الخلفية', { timeout: 15000 });
    await page.waitForTimeout(1700);
    await shot(page, '02_progress');
    await page.waitForTimeout(1600);
    await shot(page, '03_progress_running');

    console.log('wait for the real result');
    await page.waitForTimeout(5000);
    await shot(page, '04_result');

    const toast = await page.locator('.notification').allInnerTexts().catch(() => []);
    console.log('toasts:', JSON.stringify(toast));
    console.log('job id:', jobId);

    /* Second pass: force the job to report `failed` and confirm the dialog
       stays open on the danger Alert. */
    console.log('failed-job pass');
    await page.unroute('**/jobs/**');
    await page.route('**/jobs/**', (route) =>
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                id: 'forced', name: 'attribute-by-excel', owner: 'e2e',
                status: 'failed', progress: 60, message: '1200/2000', result: null,
                error: 'ProgrammingError: relation "clints" does not exist',
                created_at: Date.now() / 1000, started_at: Date.now() / 1000,
                finished_at: Date.now() / 1000,
            }),
        })
    );
    /* The dialog is still open on the business-failure result (that is the
       designed behaviour), so the same form can simply be re-submitted. */
    await page.locator('input[name="attribute_key"]').fill('e2e_phase_c_fail');
    await page.getByRole('button', { name: /^حفظ$|^Save$/ }).click();
    await page.waitForTimeout(4000);
    await shot(page, '05_failed');
    console.log('dialog still open:', await page.locator('.dialog').isVisible());

    console.log('console errors:', consoleErrors.length ? consoleErrors : 'none');

    await browser.close();
})().catch(async (e) => {
    console.error('FAILED:', e);
    process.exit(1);
});
