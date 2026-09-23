/* End-to-end check of the "نقل العملاء للارشيف بملف اكسل" dialog (archive by Excel).
 *   cd /Users/awbashir/Development/Aidi-Collections/old && node tools/e2e_archive_excel.js
 * Uploads a 2-row workbook with non-existent accounts, picks an archive reason and the
 * account column, submits, and expects PUT /clint/archivebyexcel -> 202 and a finished job
 * whose result carries the legacy "<acc> الحساب غير موجود" message (nothing is archived).
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright-core');
const ExcelJS = require(path.join(__dirname, '..', 'CAWP-frontend', 'node_modules', 'exceljs'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const OUT = process.env.OUT_DIR || path.join(__dirname, '..', 'logs', 'screens', 'final');
const XLSX = path.join(__dirname, '..', 'logs', 'e2e_archive.xlsx');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const shot = async (page, name) => { const f = path.join(OUT, `e2e_arch_${name}.png`); await page.screenshot({ path: f }); console.log('  shot ->', f); };

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const wb = new ExcelJS.Workbook(); const ws = wb.addWorksheet('rows');
  ws.addRow(['acc', 'note']); ws.addRow(['E2E-ARCH-NO-SUCH-1', 'x']); ws.addRow(['E2E-ARCH-NO-SUCH-2', 'y']);
  await wb.xlsx.writeFile(XLSX);

  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ar' })).newPage();
  const seen = []; const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)); });
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 160)));
  page.on('response', async (r) => {
    const u = r.url().replace(/^https?:\/\/[^/]+/, '');
    if (/archivebyexcel|update\/byexcel|jobs\//.test(u)) { let b = ''; try { b = (await r.text()).slice(0, 220); } catch (e) {} seen.push(`${r.request().method()} ${r.status()} ${u} ${b}`); console.log('  <<', seen[seen.length - 1]); }
  });

  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle' });
  await page.fill('input[name="username"]', 'aidi.dev'); await page.fill('input[name="password"]', 'Aidi@2026');
  await page.click('button[type="submit"]'); await page.waitForURL('**/home', { timeout: 30000 });
  await page.goto(FE + '/clients', { waitUntil: 'networkidle', timeout: 90000 }); await page.waitForTimeout(1200);

  await page.getByRole('button', { name: /عمليات ملف إكسل/ }).click(); await page.waitForTimeout(400);
  await page.locator('li,div').filter({ hasText: /^نقل العملاء للارشيف بملف اكسل$/ }).first().click(); await page.waitForTimeout(800);
  await page.setInputFiles('.dialog input[type="file"]', XLSX); await page.waitForTimeout(2500);

  // reason: first option of the first select; column: 'acc' in the second select
  await page.locator('.dialog .select__control').nth(0).click();
  await page.locator('.select-option').first().waitFor({ state: 'visible', timeout: 10000 });
  const reason = await page.locator('.select-option').first().innerText(); await page.locator('.select-option').first().click();
  await page.locator('.dialog .select__control').nth(1).click();
  await page.locator('.select-option').filter({ hasText: /^acc$/ }).first().click();
  await shot(page, '01_ready'); console.log('reason picked:', reason);

  await page.locator('.dialog button[type="submit"]').click();
  await page.waitForTimeout(5000); await shot(page, '02_result');
  const toasts = await page.locator('.notification').allInnerTexts().catch(() => []);
  console.log('toasts:', JSON.stringify(toasts));
  const put = seen.find((s) => s.startsWith('PUT') && /archivebyexcel/.test(s));
  const wrong = seen.find((s) => /update\/byexcel/.test(s));
  const done = seen.find((s) => /jobs\//.test(s) && /"status":"done"/.test(s));
  console.log('PUT /clint/archivebyexcel ->', put ? put.split(' ')[1] : 'NOT CALLED');
  console.log('legacy /clints/update/byexcel called:', wrong ? 'YES (bug)' : 'no');
  console.log('job reached done:', !!done, '| result mentions الحساب غير موجود:', /الحساب غير موجود/.test(done || ''));
  console.log('console errors:', consoleErrors.length ? consoleErrors : 'none');
  await browser.close();
  if (!put || !put.includes(' 202 ') || wrong) process.exit(1);
})().catch((e) => { console.error('FAILED:', e); process.exit(1); });
