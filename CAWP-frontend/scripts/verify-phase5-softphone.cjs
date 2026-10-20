/* Phase 5 P3: the in-app softphone end to end, against the WebRTC test PBX (pbx-webrtc-test).
 *
 *   node CAWP-frontend/scripts/verify-phase5-softphone.cjs [outDir]        (from old/)
 *
 * What runs where (review/phase5-softphone.md §3):
 *   - Chromium runs INSIDE the compose network (service softphone-e2e-browser, the official
 *     Playwright image) with a fake microphone; this script drives it over the Playwright
 *     server on 127.0.0.1:3310. Media flows browser <-> Asterisk over the compose network.
 *   - The app is the Vite dev server of this checkout on 127.0.0.1:5178 (started here when it
 *     is not running), with VITE_API_URL=http://localhost:9796 = the e2e API container
 *     (softphone-e2e-api: the real app + api.calling_sip on the throwaway DB cawp_test_call3_e2e).
 *   - The PBX (pbx-webrtc-test) reads the digest credentials from that DB (realtime ps_auths).
 *   - The extension-first agent leg is originated over AMI (127.0.0.1:15038, production
 *     privileges) with exactly the attempt tag fields of services/calling/sip_tag.py.
 *
 * It starts test-db / builds the e2e DB / starts the three containers unless SKIP_SETUP=1, and
 * stops the three containers and its own Vite server at the end unless KEEP=1.
 * ONLY=<section,...>: register, autoanswer, controls, untagged, multiline, transfer, dialpad,
 * tabs, expiry, reconnect, devices, noext, storage (register always runs).
 * Screenshots softphone_*.png (ar/en) + softphone-verify.json/.log go to outDir (default logs/screens/phase5).
 */
const path = require('path');
const fs = require('fs');
const net = require('net');
const crypto = require('crypto');
const { execFileSync, spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '../..');
const outDir = path.resolve(process.argv[2] || path.join(ROOT, 'logs/screens/phase5'));
fs.mkdirSync(outDir, { recursive: true });
const { chromium } = require(path.join(ROOT, 'tools/node_modules/playwright-core'));

const FE_PORT = 5178;
const FE = `http://localhost:${FE_PORT}`; // the origin inside the browser container (forwarded)
const API = 'http://127.0.0.1:9796'; // the e2e API from the host
const PW = 'ws://127.0.0.1:3310/softphone';
const AMI = { host: '127.0.0.1', port: 15038, user: 'cawp-webrtc-test', secret: 'pbx-webrtc-ami-only' };
const PASS = 'Aidi@2026';
const PBX = 'cawp-pbx-webrtc-test';
const DB = 'cawp_test_call3_e2e';
const REALM = 'cawp-webrtc-test';
const E2E = {
  agent: '00000000-0000-4000-8000-0000000e3501',
  case: '00000000-0000-4000-8000-0000000e3601',
  contact: '00000000-0000-4000-8000-0000000e3801',
};
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);

/* ------------------------------------------------------------------ log + checks */
const logLines = [];
const results = [];
const evidence = {};
const log = (...a) => {
  const line = a.join(' ');
  logLines.push(line);
  console.log(line);
};
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 800) });
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== '' ? '  -- ' + String(detail).slice(0, 400) : ''}`);
  return !!ok;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ host helpers */
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { encoding: 'utf8', cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'], ...opts });
const compose = (...args) => sh('docker', ['compose', ...args], { timeout: 600000 });
const asterisk = (cmd) => sh('docker', ['exec', PBX, 'asterisk', '-rx', cmd]);
const psql = (sql) => sh('docker', ['exec', 'cawp-test-db', 'psql', '-U', 'postgres', '-d', DB, '-AtF', '|', '-c', sql]).trim();

function portOpen(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const s = net.connect(port, host);
    s.setTimeout(1500);
    s.on('connect', () => {
      s.destroy();
      resolve(true);
    });
    s.on('error', () => resolve(false));
    s.on('timeout', () => {
      s.destroy();
      resolve(false);
    });
  });
}
async function waitFor(fn, { timeout = 20000, every = 250, label = 'condition' } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      last = await fn();
      if (last) return last;
    } catch (e) {
      last = e;
    }
    await sleep(every);
  }
  throw new Error(`timeout waiting for ${label} (${last && last.message ? last.message : last})`);
}

/* ------------------------------------------------------------------ AMI (test PBX only) */
class Ami {
  constructor() {
    this.buf = '';
    this.frames = [];
    this.waiters = [];
    this.seq = 0;
  }
  connect() {
    return new Promise((resolve, reject) => {
      this.sock = net.connect(AMI.port, AMI.host, () => resolve());
      this.sock.on('error', reject);
      this.sock.on('data', (d) => this.onData(d.toString('utf8')));
    });
  }
  onData(text) {
    this.buf += text;
    let i;
    while ((i = this.buf.indexOf('\r\n\r\n')) >= 0) {
      const raw = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 4);
      const f = {};
      raw.split('\r\n').forEach((line) => {
        const k = line.indexOf(':');
        if (k > 0) f[line.slice(0, k).trim()] = line.slice(k + 1).trim();
      });
      this.frames.push(f);
      this.waiters = this.waiters.filter((w) => !(w.test(f) && (w.resolve(f), true)));
    }
  }
  wait(test, timeout = 15000) {
    const found = this.frames.find(test);
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      const w = { test, resolve };
      this.waiters.push(w);
      setTimeout(() => {
        this.waiters = this.waiters.filter((x) => x !== w);
        reject(new Error('AMI timeout'));
      }, timeout);
    });
  }
  send(fields) {
    const id = `sp:${++this.seq}`;
    const lines = [...fields, ['ActionID', id]].map(([k, v]) => {
      if (/[\r\n]/.test(v)) throw new Error('control character in AMI value');
      return `${k}: ${v}`;
    });
    this.sock.write(lines.join('\r\n') + '\r\n\r\n');
    return id;
  }
  async login() {
    const id = this.send([['Action', 'Login'], ['Username', AMI.user], ['Secret', AMI.secret], ['Events', 'call']]);
    const r = await this.wait((f) => f.ActionID === id && f.Response);
    if (r.Response !== 'Success') throw new Error('AMI login refused');
  }
  /** The extension-first agent leg: exactly services/calling/sip_tag.attempt_tag_fields for a tag. */
  originate({ extension = '7001', attemptId = null, exten = 'echo', channelId = null, timeoutMs = 25000 }) {
    const fields = [
      ['Action', 'Originate'],
      ['Channel', `PJSIP/${extension}`],
      ['Context', 'webrtc-test-answered'],
      ['Exten', exten],
      ['Priority', '1'],
      ['Async', 'true'],
      ['Timeout', String(timeoutMs)],
    ];
    if (channelId) fields.push(['ChannelId', channelId]);
    // untagged test calls look like an internal call; the extension-first leg carries no caller ID
    if (!attemptId) fields.push(['CallerID', '"PBX test" <7999>']);
    if (attemptId) {
      fields.push(['Variable', `__COLLECTIONS_ATTEMPT=${attemptId}`]);
      fields.push(['Variable', `PJSIP_HEADER(add,X-CAWP-Attempt)=${attemptId}`]); // sip_tag.attempt_tag_fields
    }
    return this.send(fields);
  }
  close() {
    try {
      this.sock.end();
    } catch {
      /* closed */
    }
  }
}

/* ------------------------------------------------------------------ Asterisk evidence */
function contacts7001() {
  return asterisk('pjsip show contacts')
    .split('\n')
    .filter((l) => /Contact:\s+7001\//.test(l));
}
function channelStats() {
  const out = asterisk('pjsip show channelstats');
  const rows = [];
  out.split('\n').forEach((l) => {
    const m = l.match(/(\S*7001-[0-9a-f]+)\s+(\d\d:\d\d:\d\d)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\d+)/);
    if (m) rows.push({ channel: m[1], uptime: m[2], codec: m[3], rxCount: Number(m[4]), txCount: Number(m[8]) });
  });
  return { rows, raw: out };
}
const activeChannels = () =>
  asterisk('core show channels concise')
    .split('\n')
    .filter((l) => l.startsWith('PJSIP/'));

/* ------------------------------------------------------------------ browser helpers */
const INIT = `(() => {
  const OrigPC = window.RTCPeerConnection;
  window.__pcs = [];
  function PC(...args) { const pc = new OrigPC(...args); window.__pcs.push(pc); return pc; }
  PC.prototype = OrigPC.prototype;
  Object.setPrototypeOf(PC, OrigPC);
  window.RTCPeerConnection = PC;
  window.__dtmf = []; window.__tones = [];
  if (window.RTCDTMFSender) {
    const ins = RTCDTMFSender.prototype.insertDTMF;
    RTCDTMFSender.prototype.insertDTMF = function (tones, ...rest) {
      window.__dtmf.push(tones);
      if (!this.__cawp) { this.__cawp = 1; this.addEventListener('tonechange', (e) => { if (e.tone) window.__tones.push(e.tone); }); }
      return ins.call(this, tones, ...rest);
    };
  }
  window.__bc = [];
  const OrigBC = window.BroadcastChannel;
  if (OrigBC) {
    window.BroadcastChannel = function (name) {
      const ch = new OrigBC(name);
      const post = ch.postMessage.bind(ch);
      ch.postMessage = (m) => { try { window.__bc.push(JSON.stringify(m)); } catch (e) {} return post(m); };
      ch.addEventListener('message', (e) => { try { window.__bc.push(JSON.stringify(e.data)); } catch (err) {} });
      return ch;
    };
  }
})();`;

async function pcStats(page) {
  return page.evaluate(async () => {
    const pcs = (window.__pcs || []).filter((pc) => pc.connectionState !== 'closed');
    const pc = pcs[pcs.length - 1];
    if (!pc) return null;
    const r = await pc.getStats();
    const out = { bytesSent: 0, bytesReceived: 0, packetsSent: 0, packetsReceived: 0, inEnergy: 0, srcEnergy: 0,
      inLevel: null, srcLevel: null, ice: pc.iceConnectionState, direction: null, codec: null, candidate: null };
    const byId = new Map();
    r.forEach((s) => byId.set(s.id, s));
    r.forEach((s) => {
      if (s.type === 'outbound-rtp' && s.kind === 'audio') { out.bytesSent += s.bytesSent || 0; out.packetsSent += s.packetsSent || 0; }
      if (s.type === 'inbound-rtp' && s.kind === 'audio') {
        out.bytesReceived += s.bytesReceived || 0; out.packetsReceived += s.packetsReceived || 0;
        out.inEnergy += s.totalAudioEnergy || 0; out.inLevel = s.audioLevel ?? null;
        const c = s.codecId && byId.get(s.codecId); if (c) out.codec = c.mimeType;
      }
      if (s.type === 'media-source' && s.kind === 'audio') { out.srcEnergy += s.totalAudioEnergy || 0; out.srcLevel = s.audioLevel ?? null; }
      if (s.type === 'transport' && s.selectedCandidatePairId) {
        const pair = byId.get(s.selectedCandidatePairId); const loc = pair && byId.get(pair.localCandidateId);
        const rem = pair && byId.get(pair.remoteCandidateId);
        if (loc) out.candidate = `${loc.candidateType} ${loc.address || loc.ip}:${loc.port} -> ${rem ? rem.candidateType + ' ' + (rem.address || rem.ip) + ':' + rem.port : '?'}`;
      }
    });
    const t = pc.getTransceivers()[0];
    out.direction = t ? t.currentDirection : null;
    return out;
  });
}
async function statsDelta(page, ms = 3000) {
  const a = await pcStats(page);
  await sleep(ms);
  const b = await pcStats(page);
  if (!a || !b) return null;
  return {
    sent: b.bytesSent - a.bytesSent,
    received: b.bytesReceived - a.bytesReceived,
    inEnergy: b.inEnergy - a.inEnergy,
    srcEnergy: b.srcEnergy - a.srcEnergy,
    direction: b.direction,
    ice: b.ice,
    codec: b.codec,
    candidate: b.candidate,
    totals: b,
  };
}

const byTest = (page, id) => page.locator(`[data-testid="${id}"]`);
const chipStatus = (page) => byTest(page, 'softphone-chip').getAttribute('data-status').catch(() => null);
const chipRole = (page) => byTest(page, 'softphone-chip').getAttribute('data-role').catch(() => null);
const lineSel = (state) => `[data-testid="softphone-line"]${state ? `[data-state="${state}"]` : ''}`;
async function openPanel(page) {
  if ((await byTest(page, 'softphone-panel').count()) === 0) await byTest(page, 'softphone-chip').click();
  await byTest(page, 'softphone-panel').waitFor({ timeout: 5000 });
}
async function tab(page, name) {
  await openPanel(page);
  await byTest(page, `sp-tab-${name}`).click();
}
async function shot(page, name) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(outDir, `${name}.png`) });
  log(`      screenshot ${name}.png`);
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
async function shotBoth(page, name) {
  await setLang(page, 'ar');
  await shot(page, `${name}_ar`);
  await setLang(page, 'en');
  await shot(page, `${name}_en`);
}

const issued = []; // every password this run saw in a credential response (to prove where it is NOT)
const credentialPosts = new Map(); // page label -> count
function watch(page, label) {
  credentialPosts.set(label, 0);
  page.on('pageerror', (e) => log(`      [${label}] pageerror ${e.message}`));
  page.on('response', async (res) => {
    const u = res.url();
    if (res.request().method() === 'POST' && /\/v2\/me\/sip-credentials$/.test(u)) {
      credentialPosts.set(label, (credentialPosts.get(label) || 0) + 1);
      try {
        const j = await res.json();
        if (j && j.data && j.data.password) issued.push({ at: Date.now(), label, password: j.data.password, id: j.data.credential_id, status: res.status() });
      } catch {
        /* not JSON */
      }
    }
  });
  const sockets = [];
  page.on('websocket', (ws) => {
    if (/pbx-webrtc-test/.test(ws.url())) sockets.push(ws.url());
  });
  return sockets;
}

async function login(page, user) {
  await page.goto(FE + '/sign-in', { waitUntil: 'networkidle', timeout: 90000 });
  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !/sign-in/.test(String(u)), { timeout: 30000 });
  // client-side navigation (a reload would start a new softphone engine)
  await page.evaluate(() => {
    window.history.pushState({}, '', '/clients');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
}

async function apiLogin(user) {
  const r = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: PASS }),
  });
  const j = await r.json();
  if (!j.access_token) throw new Error(`login ${user}: ${r.status}`);
  return j.access_token;
}
async function api(token, method, p, body) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = crypto.randomUUID();
  const r = await fetch(API + p, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let j = null;
  try {
    j = await r.json();
  } catch {
    /* empty */
  }
  return { status: r.status, body: j };
}

/* ------------------------------------------------------------------ setup */
let viteProc = null;
async function setup() {
  if (process.env.SKIP_SETUP !== '1') {
    log('setup: test-db, e2e database, PBX, e2e API, browser');
    compose('--profile', 'test', 'up', '-d', 'test-db');
    log(compose('--profile', 'pbx-webrtc-test', 'run', '--rm', '--no-deps', 'softphone-e2e-api', 'python', '/e2e/setup_db.py').trim().split('\n').pop());
    compose('--profile', 'pbx-webrtc-test', 'up', '-d', '--no-deps', 'pbx-webrtc-test', 'softphone-e2e-api', 'softphone-e2e-browser');
    // the PBX's realtime connection may have been cut by the DB rebuild: reconnect it
    await sleep(3000);
  }
  if (!(await portOpen(FE_PORT))) {
    log(`setup: starting the e2e Vite dev server on 127.0.0.1:${FE_PORT} (VITE_API_URL=http://localhost:9796)`);
    viteProc = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', String(FE_PORT), '--strictPort'], {
      cwd: path.join(ROOT, 'CAWP-frontend'),
      env: { ...process.env, VITE_API_URL: 'http://localhost:9796' },
      stdio: 'ignore',
      detached: true,
    });
  }
  await waitFor(() => portOpen(FE_PORT), { timeout: 60000, label: 'vite' });
  await waitFor(async () => (await fetch(API + '/health').catch(() => null))?.ok, { timeout: 90000, label: 'e2e API' });
  await waitFor(() => portOpen(3310), { timeout: 60000, label: 'playwright server' });
  await waitFor(() => /fully booted/.test(asterisk('core waitfullybooted')), { timeout: 60000, label: 'PBX' });
  await waitFor(() => /Connected to/.test(asterisk('realtime show pgsql status')), { timeout: 30000, label: 'PBX realtime' });
  log(`      PBX realtime: ${asterisk('realtime show pgsql status').trim()}`);
  asterisk('channel request hangup all'); // leftovers of an interrupted run (test PBX only)
}
function teardown() {
  if (viteProc) {
    try {
      process.kill(-viteProc.pid);
    } catch {
      /* gone */
    }
  }
  if (process.env.KEEP !== '1' && process.env.SKIP_SETUP !== '1') {
    log('teardown: stopping pbx-webrtc-test, softphone-e2e-api, softphone-e2e-browser');
    try {
      compose('--profile', 'pbx-webrtc-test', 'stop', 'pbx-webrtc-test', 'softphone-e2e-api', 'softphone-e2e-browser');
    } catch (e) {
      log(`teardown failed: ${e.message}`);
    }
  }
}

function insertAttempt() {
  // a rerun on the same database (SKIP_SETUP=1) reuses the attempt that still holds the reservation
  const held = psql(`SELECT r.attempt_id FROM call_reservations r JOIN call_attempts a ON a.id = r.attempt_id
                     WHERE r.collector_user_id = '${E2E.agent}' AND r.released_at IS NULL AND a.state = 'dispatched'`);
  if (held) return held;
  const id = crypto.randomUUID();
  psql(`INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, extension, contact_id, destination)
        VALUES ('${id}', 'click_to_call', 'prepared', '${E2E.case}', '${E2E.agent}', '${E2E.agent}', '7001', '${E2E.contact}', '+966500000001');
        UPDATE call_attempts SET state = 'authorized', authorized_at = now() WHERE id = '${id}';
        UPDATE call_attempts SET state = 'dispatched', dispatched_at = now() WHERE id = '${id}';
        INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension)
        VALUES ('${id}', '${E2E.agent}', '${E2E.case}', '7001');`);
  return id;
}

/* ------------------------------------------------------------------ main */
(async () => {
  const started = Date.now();
  await setup();
  const browser = await chromium.connect(PW, { timeout: 60000 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(INIT);
  const page = await ctx.newPage();
  const leaderSockets = watch(page, 'tab1');
  const ami = new Ami();
  await ami.connect();
  await ami.login();
  let attemptId = null;
  try {
    /* ---------------------------------------------------------------- register */
    log('\n== register');
    await login(page, 'e2e.agent');
    await waitFor(async () => (await chipStatus(page)) === 'registered', { timeout: 30000, label: 'registered' });
    check('register: the header chip says registered', (await chipStatus(page)) === 'registered');
    check('register: this tab holds the phone (leader)', ['leader', 'solo'].includes(await chipRole(page)), await chipRole(page));
    const c = await waitFor(() => contacts7001().length > 0 && contacts7001(), { timeout: 10000, label: 'contact' });
    check('register: Asterisk has exactly one contact for 7001', c.length === 1, c.join(' | '));
    evidence.contact = asterisk('pjsip show contacts').split('\n').filter((l) => /7001/.test(l)).join('\n');
    check('register: signalling over WSS to the PBX service name', leaderSockets.some((u) => u.startsWith('wss://pbx-webrtc-test:8089/ws')), leaderSockets.join(','));
    check('register: exactly one credential was issued', issued.length === 1, issued.length);
    const cred = issued[0];
    const row = psql(`SELECT extension, realm, ha1, ha1 = md5('7001:${REALM}:${cred.password}'), revoked_at IS NULL, round(extract(epoch from expires_at - issued_at)) FROM sip_credentials WHERE id = '${cred.id}'`).split('|');
    check('register: the DB holds only HA1 = md5(ext:realm:password), never the password', row[3] === 't' && row[2] !== cred.password && !row.includes(cred.password), row.slice(0, 2).join(','));
    check('register: short TTL (90 s in this run)', row[5] === '90', row[5]);
    await openPanel(page);
    check('register: panel shows extension 7001', /7001/.test(await byTest(page, 'softphone-extension').innerText()));
    await tab(page, 'diagnostics');
    const diag = {};
    for (const k of ['https', 'webrtc', 'mic', 'websocket', 'registration', 'credential', 'codecs', 'tab']) {
      diag[k] = await byTest(page, `sp-diag-${k}`).getAttribute('data-tone');
    }
    check('register: diagnostics all green (https, webrtc, mic, websocket, registration, credential, codecs, tab)', Object.values(diag).every((v) => v === 'success'), JSON.stringify(diag));
    await shotBoth(page, 'softphone_10_diagnostics');
    await tab(page, 'phone');
    await shotBoth(page, 'softphone_01_registered');

    /* ---------------------------------------------------------------- auto-answer + two-way audio */
    if (want('autoanswer') || want('controls') || want('multiline') || want('transfer')) {
      log('\n== auto-answer (attempt-tagged extension-first leg) and two-way audio');
      attemptId = insertAttempt();
      log(`      attempt ${attemptId} (dispatched, reserved for e2e.agent on 7001)`);
      const t0 = Date.now();
      const act = ami.originate({ attemptId, channelId: `cawp-${attemptId}` });
      const resp = await ami.wait((f) => f.ActionID === act && f.Response);
      check('autoanswer: AMI Originate accepted (= submitted, not connected)', resp.Response === 'Success', resp.Message);
      await page.locator(lineSel('active')).first().waitFor({ timeout: 15000 });
      const ms = Date.now() - t0;
      const lineId = await page.locator(lineSel('active')).first().getAttribute('data-line-id');
      const line = page.locator(`[data-line-id="${lineId}"]`);
      check('autoanswer: the tagged call was answered automatically', (await line.getAttribute('data-auto-answered')) === '1', `${ms} ms`);
      check('autoanswer: the line is linked to the attempt', (await line.getAttribute('data-attempt')) === attemptId);
      await byTest(page, 'sp-attempt-case').waitFor({ timeout: 8000 });
      check('autoanswer: the live call panel shows the case', /ZZ-E2E softphone case/.test(await byTest(page, 'sp-attempt-case').innerText()));
      check('autoanswer: the live call panel shows the attempt state from the API', (await byTest(page, 'sp-attempt').getAttribute('data-state')) === 'dispatched');
      const orig = await ami.wait((f) => f.Event === 'OriginateResponse' && f.ActionID === act, 15000).catch(() => null);
      check('autoanswer: AMI OriginateResponse Success (the agent leg answered)', orig && orig.Response === 'Success', orig && `${orig.Response} ${orig.Channel || ''} ${orig.Uniqueid || ''}`);
      await sleep(1500);
      const d1 = await statsDelta(page, 3000);
      const st1 = channelStats();
      await sleep(2000);
      const st2 = channelStats();
      evidence.twoWayBrowser = d1;
      evidence.twoWayAsterisk = { first: st1.rows, second: st2.rows, raw: st2.raw };
      check('audio: browser sends RTP (outbound bytes increase)', d1 && d1.sent > 10000, d1 && `+${d1.sent} B in 3 s`);
      check('audio: browser receives RTP (inbound bytes increase)', d1 && d1.received > 10000, d1 && `+${d1.received} B in 3 s`);
      check('audio: the received audio has energy (the fake microphone came back through the PBX echo)', d1 && d1.inEnergy > 0, d1 && `+${d1.inEnergy.toFixed(6)}`);
      check('audio: ICE connected over a host candidate on the compose network', d1 && ['connected', 'completed'].includes(d1.ice), d1 && `${d1.ice} ${d1.candidate} ${d1.codec}`);
      const a1 = st1.rows[0];
      const a2 = st2.rows.find((r) => a1 && r.channel === a1.channel);
      check('audio: Asterisk channel stats count RTP both ways and growing', a1 && a2 && a2.rxCount > a1.rxCount && a2.txCount > a1.txCount && a2.rxCount > 50 && a2.txCount > 50,
        a1 && a2 && `${a2.channel} ${a2.codec} rx ${a1.rxCount}->${a2.rxCount}, tx ${a1.txCount}->${a2.txCount}`);
      await shotBoth(page, 'softphone_02_auto_answered');

      /* ---------------------------------------------------------------- controls */
      if (want('controls')) {
        log('\n== mute, hold, DTMF, hang-up');
        await byTest(page, 'sp-mute').click();
        await waitFor(async () => (await line.getAttribute('data-muted')) === '1', { label: 'muted' });
        await sleep(1500);
        const dm = await statsDelta(page, 2500);
        check('mute: the line shows muted', (await line.getAttribute('data-muted')) === '1');
        check('mute: the microphone sends no audio energy while muted', dm && dm.srcEnergy === 0, dm && `src energy +${dm.srcEnergy}`);
        check('mute: the echo coming back is silent while muted', dm && dm.inEnergy < 1e-4, dm && `in energy +${dm.inEnergy}`);
        await shot(page, 'softphone_03_muted_en');
        await byTest(page, 'sp-mute').click();
        await waitFor(async () => (await line.getAttribute('data-muted')) === '0', { label: 'unmuted' });
        await sleep(1500);
        const du = await statsDelta(page, 2500);
        check('mute: after unmute the microphone and the echo carry audio again', du && du.srcEnergy > 0 && du.inEnergy > 0, du && `src +${du.srcEnergy.toFixed(6)}, in +${du.inEnergy.toFixed(6)}`);

        await byTest(page, 'sp-hold').click();
        await waitFor(async () => (await line.getAttribute('data-held')) === '1', { label: 'held' });
        await sleep(1500);
        const dh = await statsDelta(page, 2500);
        check('hold: the line shows on hold', (await line.getAttribute('data-state')) === 'held');
        check('hold: the media direction is sendonly/inactive (re-INVITE accepted by the PBX)', dh && ['sendonly', 'inactive'].includes(dh.direction), dh && dh.direction);
        check('hold: no audio flows back while held', dh && dh.inEnergy < 1e-4, dh && `in bytes +${dh.received}, energy +${dh.inEnergy}`);
        await shot(page, 'softphone_04_held_en');
        await byTest(page, 'sp-hold').click();
        await waitFor(async () => (await line.getAttribute('data-held')) === '0', { label: 'resumed' });
        await sleep(1500);
        const dr = await statsDelta(page, 2500);
        check('hold: resume restores sendrecv and two-way audio', dr && dr.direction === 'sendrecv' && dr.inEnergy > 0 && dr.sent > 5000 && dr.received > 5000,
          dr && `${dr.direction} +${dr.sent}/+${dr.received} B`);

        await byTest(page, 'sp-keypad').click();
        await byTest(page, 'sp-dtmf-5').click();
        await byTest(page, 'sp-dtmf-9').click();
        await sleep(1200);
        const dt = await page.evaluate(() => ({ sent: window.__dtmf, played: window.__tones }));
        check('dtmf: keypad sends RFC 4733 DTMF on the RTP sender (5, 9)', dt.sent.join('') === '59' && dt.played.join('') === '59', JSON.stringify(dt));

        await byTest(page, 'sp-hangup').click();
        await page.locator(lineSel('ended')).first().waitFor({ timeout: 8000 });
        await waitFor(() => activeChannels().length === 0, { timeout: 8000, label: 'channels gone' }).catch(() => null);
        check('hangup: the line ends and the PBX has no channel left', activeChannels().length === 0, activeChannels().join(' | '));
        await sleep(1000);
      }
    }

    /* ---------------------------------------------------------------- untagged: rings, not auto-answered */
    if (want('untagged')) {
      log('\n== untagged and foreign-tagged calls ring normally');
      const act = ami.originate({ timeoutMs: 30000 });
      await page.locator(lineSel('ringing')).first().waitFor({ timeout: 10000 });
      await sleep(4000);
      const ringing = page.locator(lineSel('ringing')).first();
      check('untagged: still ringing after 4 s (not auto-answered)', (await ringing.count()) === 1 && (await ringing.getAttribute('data-auto-answered')) === '0');
      check('untagged: decision says it is not a click-to-call leg', (await ringing.locator('[data-testid="sp-line-decision"]').getAttribute('data-decision')) === 'no_tag');
      check('untagged: answer and reject are offered', (await byTest(page, 'sp-answer').count()) === 1 && (await byTest(page, 'sp-reject').count()) === 1);
      check('untagged: the header chip signals the ringing call', (await byTest(page, 'softphone-chip-lines').innerText()) === '1');
      const ch = activeChannels();
      check('untagged: the PBX channel is ringing, not up', ch.length === 1 && /!Ringing!|!Ring!/.test(ch[0]), ch.join(' | '));
      await shotBoth(page, 'softphone_05_incoming_untagged');
      await byTest(page, 'sp-reject').click();
      const r = await ami.wait((f) => f.Event === 'OriginateResponse' && f.ActionID === act, 10000).catch(() => null);
      check('untagged: reject ends it; the PBX reports the agent leg was not answered', r && r.Response === 'Failure', r && `${r.Response} reason ${r.Reason}`);
      await sleep(1500);

      const foreign = crypto.randomUUID();
      const act2 = ami.originate({ attemptId: foreign, timeoutMs: 30000 });
      await page.locator(lineSel('ringing')).first().waitFor({ timeout: 10000 });
      await waitFor(async () => (await page.locator(lineSel('ringing')).first().locator('[data-testid="sp-line-decision"]').getAttribute('data-decision')) === 'not_my_active_attempt', { timeout: 6000, label: 'decision' }).catch(() => null);
      const fl = page.locator(lineSel('ringing')).first();
      check('tagged with someone else\'s attempt: rings, not auto-answered', (await fl.getAttribute('data-auto-answered')) === '0' && (await fl.locator('[data-testid="sp-line-decision"]').getAttribute('data-decision')) === 'not_my_active_attempt');
      // keyboard shortcuts: Alt+Shift+A answers, Alt+Shift+E ends (outside text fields)
      await page.locator('body').click({ position: { x: 600, y: 700 } });
      await page.keyboard.press('Alt+Shift+KeyA');
      await page.locator(lineSel('active')).first().waitFor({ timeout: 10000 });
      check('manual answer: the agent can still answer it (keyboard Alt+Shift+A)', (await page.locator(lineSel('active')).count()) === 1);
      await page.keyboard.press('Alt+Shift+KeyE');
      await page.locator(lineSel('ended')).first().waitFor({ timeout: 8000 }).catch(() => null);
      check('keyboard: Alt+Shift+E hangs up', (await page.locator(lineSel('active')).count()) === 0);
      await ami.wait((f) => f.Event === 'OriginateResponse' && f.ActionID === act2, 10000).catch(() => null);
      await sleep(1500);
    }

    /* ---------------------------------------------------------------- multi-line with auto-hold */
    if (want('multiline') && attemptId) {
      log('\n== multi-line with auto-hold');
      ami.originate({ attemptId, exten: 'echo' });
      await page.locator(lineSel('active')).first().waitFor({ timeout: 15000 });
      const first = await page.locator(lineSel('active')).first().getAttribute('data-line-id');
      ami.originate({ exten: 'tone', timeoutMs: 30000 });
      await page.locator(lineSel('ringing')).first().waitFor({ timeout: 10000 });
      const second = await page.locator(lineSel('ringing')).first().getAttribute('data-line-id');
      check('multiline: a second call rings while the first is active', !!second && second !== first);
      await page.locator(`[data-line-id="${second}"] [data-testid="sp-answer"]`).click();
      await waitFor(async () => (await page.locator(`[data-line-id="${first}"]`).getAttribute('data-held')) === '1', { timeout: 8000, label: 'auto-hold' });
      await page.locator(`[data-line-id="${second}"][data-state="active"]`).waitFor({ timeout: 8000 });
      check('multiline: answering line 2 auto-held line 1', (await page.locator(`[data-line-id="${first}"]`).getAttribute('data-state')) === 'held');
      await shotBoth(page, 'softphone_06_multiline');
      await page.locator(`[data-line-id="${first}"] [data-testid="sp-switch"]`).click();
      await waitFor(async () => (await page.locator(`[data-line-id="${second}"]`).getAttribute('data-held')) === '1', { timeout: 8000, label: 'switch' });
      await page.locator(`[data-line-id="${first}"][data-state="active"]`).waitFor({ timeout: 8000 });
      check('multiline: switching back holds line 2 and resumes line 1', true);

      /* ---------------------------------------------------------------- transfer: internal only */
      // 2026-10-20: transfer / consult / add a participant are server-controlled (AMI, CallControlBox):
      // the softphone sends no REFER and offers no free-text target, only app users' extensions.
      if (want('transfer')) {
        log('\n== transfer (server-controlled, app users only)');
        await page.locator(`[data-line-id="${first}"] [data-testid="sp-transfer-open"]`).click();
        const box = page.locator(`[data-line-id="${first}"] [data-testid="sp-callctl"]`);
        await box.waitFor({ timeout: 8000 });
        check('transfer: no free-text target (external numbers cannot be entered)',
          (await page.locator(`[data-line-id="${first}"] [data-testid="sp-transfer-input"]`).count()) === 0);
        check('transfer: blind / consult / add-to-call choices are offered',
          (await box.locator('[data-testid^="sp-callctl-"][role="radio"]').count()) === 3);
        await shot(page, 'softphone_07_call_control_en');
      }
      for (const id of [first, second]) {
        const b = page.locator(`[data-line-id="${id}"] [data-testid="sp-hangup"]`);
        if (await b.count()) await b.click();
      }
      await waitFor(() => activeChannels().length === 0, { timeout: 10000, label: 'channels gone' }).catch(() => null);
      check('multiline: both calls ended; no PBX channel left', activeChannels().length === 0, activeChannels().join(' | '));
      await sleep(1000);
    }

    /* ---------------------------------------------------------------- dialpad: internal only */
    if (want('dialpad')) {
      log('\n== dialpad: internal extensions only, echo test');
      await tab(page, 'phone');
      const dial = byTest(page, 'sp-dial-input');
      await dial.fill('0501234567');
      check('dialpad: an external number is refused (call disabled, click-to-call hint)',
        (await byTest(page, 'sp-dial-call').isDisabled()) && (await byTest(page, 'sp-dial-hint').getAttribute('data-external')) === '1');
      await shotBoth(page, 'softphone_08_dialpad_external_refused');
      await dial.fill('600');
      check('dialpad: an internal extension (the echo test 600) is allowed', !(await byTest(page, 'sp-dial-call').isDisabled()));
      await byTest(page, 'sp-dial-call').click();
      await page.locator(lineSel('active')).first().waitFor({ timeout: 15000 });
      await sleep(1500);
      const de = await statsDelta(page, 2500);
      check('dialpad: the outgoing echo-test call has two-way audio', de && de.sent > 5000 && de.received > 5000 && de.inEnergy > 0, de && `+${de.sent}/+${de.received} B, energy +${de.inEnergy.toFixed(6)}`);
      // Asterisk's Echo() ends on "#": the PBX hanging up proves it received the DTMF
      const echoLine = await page.locator(lineSel('active')).first().getAttribute('data-line-id');
      await page.locator(`[data-line-id="${echoLine}"] [data-testid="sp-keypad"]`).click();
      await page.locator(`[data-line-id="${echoLine}"] [data-testid="sp-dtmf-hash"]`).click();
      await page.locator(`[data-line-id="${echoLine}"][data-state="ended"]`).waitFor({ timeout: 8000 }).catch(() => null);
      check('dtmf: the PBX received "#" (the echo test ended by itself)', (await page.locator(`[data-line-id="${echoLine}"]`).getAttribute('data-state').catch(() => null)) === 'ended');
      await sleep(1000);
    }

    /* ---------------------------------------------------------------- devices */
    if (want('devices')) {
      log('\n== devices (saved per user through the API)');
      await tab(page, 'devices');
      await waitFor(async () => (await byTest(page, 'sp-device-microphone').locator('option').count()) > 1, { timeout: 8000, label: 'devices listed' }).catch(() => null);
      const mics = await byTest(page, 'sp-device-microphone').locator('option').allInnerTexts();
      check('devices: the fake microphone is listed', mics.some((m) => /fake/i.test(m)), mics.join(' | '));
      await byTest(page, 'sp-mic-test').click();
      await waitFor(async () => Number(await byTest(page, 'sp-mic-level').getAttribute('aria-valuenow')) > 0, { timeout: 6000, label: 'mic level' }).catch(() => null);
      const lvl = Number(await byTest(page, 'sp-mic-level').getAttribute('aria-valuenow'));
      check('devices: the microphone level meter moves', lvl > 0, lvl);
      await byTest(page, 'sp-mic-test').click();
      await byTest(page, 'sp-test-tone').click();
      const options = await byTest(page, 'sp-device-microphone').locator('option').evaluateAll((os) => os.map((o) => o.value).filter(Boolean));
      const chosen = options[0];
      await byTest(page, 'sp-device-microphone').selectOption(chosen);
      await byTest(page, 'sp-devices-save').click();
      await byTest(page, 'sp-devices-message').waitFor({ timeout: 8000 });
      const tok = await apiLogin('e2e.agent');
      const me = await api(tok, 'GET', '/v2/me/calling');
      check('devices: the choice is saved in the API (calling_preferences), not the browser', me.body.data.preferences.devices.microphone === chosen && me.body.data.preferences.saved === true,
        `revision ${me.body.data.preferences.revision}`);
      const store = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
      check('devices: no device choice in local/session storage', !store.includes(chosen));
      await shotBoth(page, 'softphone_09_devices');
      await tab(page, 'phone');
    }

    /* ---------------------------------------------------------------- second tab: status only */
    let page2 = null;
    if (want('tabs')) {
      log('\n== a second tab shows status only');
      page2 = await ctx.newPage();
      const sockets2 = watch(page2, 'tab2');
      await page2.goto(FE + '/clients', { waitUntil: 'domcontentloaded' });
      await waitFor(async () => (await chipRole(page2)) === 'follower' && (await chipStatus(page2)) === 'registered', { timeout: 20000, label: 'follower' });
      check('tabs: the second tab is status-only and mirrors "registered"', (await chipRole(page2)) === 'follower' && (await chipStatus(page2)) === 'registered');
      await openPanel(page2);
      check('tabs: the second tab says the phone is in another tab', (await byTest(page2, 'softphone-follower').count()) === 1);
      check('tabs: the second tab fetched no credential and opened no SIP WebSocket', credentialPosts.get('tab2') === 0 && sockets2.length === 0, `posts ${credentialPosts.get('tab2')}, ws ${sockets2.length}`);
      check('tabs: the PBX still has exactly one contact for 7001', contacts7001().length === 1);
      const act = ami.originate({ timeoutMs: 30000 });
      await page2.locator(lineSel('ringing')).first().waitFor({ timeout: 10000 });
      check('tabs: the status-only tab shows the ringing call without controls', (await byTest(page2, 'sp-answer').count()) === 0);
      await shotBoth(page2, 'softphone_11_second_tab_status_only');
      await byTest(page, 'sp-reject').click();
      await ami.wait((f) => f.Event === 'OriginateResponse' && f.ActionID === act, 10000).catch(() => null);
      const bcDump = await page2.evaluate(() => (window.__bc || []).join('\n'));
      check('tabs: the tab broadcast never carries a password', bcDump.length > 0 && !issued.some((c) => bcDump.includes(c.password)), `${bcDump.length} chars`);
      evidence.broadcastSample = bcDump.slice(0, 400);
    }

    /* ---------------------------------------------------------------- expiry: the PBX refuses, the phone re-fetches */
    if (want('expiry')) {
      log('\n== an expired credential forces a re-fetch');
      await tab(page, 'diagnostics');
      const pre = issued.length;
      await byTest(page, 'sp-reconnect').click();
      await waitFor(async () => issued.length > pre && (await chipStatus(page)) === 'registered', { timeout: 30000, label: 'fresh registration' });
      await tab(page, 'phone');
      const before = issued.length;
      const cur = issued[issued.length - 1];
      psql(`SET session_replication_role = replica; UPDATE sip_credentials SET issued_at = now() - interval '20 minutes', expires_at = now() - interval '1 second' WHERE id = '${cur.id}'`);
      check('expiry: the PBX view no longer returns the credential', psql(`SELECT count(*) FROM pbx_ps_auths WHERE id = '7001'`) === '0');
      const logFrom = new Date(Date.now() - 2000).toISOString();
      await waitFor(() => issued.length > before, { timeout: 75000, every: 1000, label: 're-fetch after the refused re-REGISTER' });
      const failed = sh('docker', ['logs', '--since', logFrom, PBX]).split('\n').filter((l) => /Request 'REGISTER' from '[^']*<sip:7001@/.test(l) && /Failed to authenticate/.test(l));
      check('expiry: the PBX refused the re-REGISTER with the expired credential', failed.length >= 1, failed[0] || 'no refusal logged');
      await waitFor(async () => (await chipStatus(page)) === 'registered', { timeout: 20000, label: 're-registered' });
      check('expiry: a new credential was fetched and the phone is registered again', issued.length === before + 1 && (await chipStatus(page)) === 'registered', `issues ${before} -> ${issued.length}`);
      check('expiry: the re-fetch was forced by the refusal (before the 63 s proactive refresh)', issued[before].at - cur.at < 62000, `${Math.round((issued[before].at - cur.at) / 1000)} s after the expired one was issued`);
      const n = issued[issued.length - 1];
      check('expiry: the old credential stays refused; the new one is current', psql(`SELECT md5_cred = md5('7001:${REALM}:${n.password}') FROM pbx_ps_auths WHERE id = '7001'`) === 't');
      // proactive refresh: before the new credential expires, the phone replaces it without a failure
      const t = Date.now();
      await waitFor(() => issued.length > before + 1, { timeout: 80000, every: 1000, label: 'proactive refresh' });
      check('expiry: the next credential is fetched before expiry (refresh_after_seconds = 63 of 90)', Date.now() - n.at < 90000 && (await chipStatus(page)) === 'registered',
        `${Math.round((issued[issued.length - 1].at - n.at) / 1000)} s after the previous issue (waited ${Math.round((Date.now() - t) / 1000)} s)`);
    }

    /* ---------------------------------------------------------------- reconnect: fetched again */
    if (want('reconnect')) {
      log('\n== WebSocket reconnect fetches the credential again');
      const before = issued.length;
      sh('docker', ['restart', PBX]);
      await waitFor(async () => (await chipStatus(page)) !== 'registered', { timeout: 20000, label: 'offline' }).catch(() => null);
      const seen = await chipStatus(page);
      await waitFor(async () => (await chipStatus(page)) === 'registered', { timeout: 90000, every: 1000, label: 'registered after PBX restart' });
      check('reconnect: the phone went offline and came back registered', seen !== 'registered', `saw ${seen}`);
      check('reconnect: a fresh credential was fetched for the new connection', issued.length > before, `issues ${before} -> ${issued.length}`);
    }

    /* ---------------------------------------------------------------- failover: close the leader tab */
    if (want('tabs') && page2) {
      log('\n== the status-only tab takes over when the leader tab closes');
      const before = credentialPosts.get('tab2');
      await page.close();
      await waitFor(async () => (await chipRole(page2)) === 'leader' && (await chipStatus(page2)) === 'registered', { timeout: 30000, label: 'failover' });
      check('tabs: the second tab took the lock, fetched its own credential and registered', (await chipRole(page2)) === 'leader' && credentialPosts.get('tab2') > before);
      check('tabs: still exactly one contact for 7001', contacts7001().length === 1);
    }
    const lastPage = page2 && !page2.isClosed() ? page2 : page.isClosed() ? null : page;

    /* ---------------------------------------------------------------- storage */
    if (want('storage') && lastPage) {
      log('\n== the password is never in browser storage');
      const dump = await lastPage.evaluate(async () => {
        const out = { local: JSON.stringify({ ...localStorage }), session: JSON.stringify({ ...sessionStorage }), idb: [] };
        if (indexedDB.databases) out.idb = (await indexedDB.databases()).map((d) => d.name);
        out.cookies = document.cookie;
        return out;
      });
      const leaked = issued.filter((c) => [dump.local, dump.session, dump.cookies].some((s) => s.includes(c.password)));
      check(`storage: none of the ${issued.length} issued passwords is in localStorage, sessionStorage or cookies`, issued.length > 0 && leaked.length === 0);
      check('storage: no IndexedDB database is used', dump.idb.length === 0, dump.idb.join(','));
      const dbLeak = issued.filter((c) => psql(`SELECT count(*) FROM sip_credentials WHERE ha1 = '${c.password}' OR id::text = '${c.password}'`) !== '0');
      check('storage: no issued password is stored in the database', dbLeak.length === 0);
      const cleared = psql(`SELECT count(*) FILTER (WHERE ha1 IS NULL), count(*) FROM sip_credentials WHERE extension = '7001'`).split('|');
      check('storage: every superseded/expired credential has had its HA1 cleared (only the current one keeps it)', Number(cleared[1]) - Number(cleared[0]) <= 1, `${cleared[0]} cleared of ${cleared[1]}`);
    }

    /* ---------------------------------------------------------------- no extension: a clear state */
    if (want('noext')) {
      log('\n== a collector without an extension');
      const ctx3 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const p3 = await ctx3.newPage();
      watch(p3, 'noext');
      await login(p3, 'e2e.noext');
      await waitFor(async () => (await chipStatus(p3)) === 'no_extension', { timeout: 20000, label: 'no_extension' });
      check('noext: the chip says "no extension"', (await chipStatus(p3)) === 'no_extension');
      await openPanel(p3);
      const blockedText = await byTest(p3, 'softphone-blocked').innerText();
      check('noext: the panel explains it and how to get one', (await byTest(p3, 'softphone-blocked').getAttribute('data-reason')) === 'no_extension' && /extension|تحويلة/i.test(blockedText), blockedText.replace(/\s+/g, ' '));
      check('noext: no dialpad, no registration', (await byTest(p3, 'sp-dial-input').count()) === 0);
      const tok = await apiLogin('e2e.noext');
      const r = await api(tok, 'POST', '/v2/me/sip-credentials');
      check('noext: the API refuses the credential with 409 no_extension', r.status === 409 && r.body.code === 'no_extension', `${r.status} ${r.body && r.body.code}`);
      await shotBoth(p3, 'softphone_12_no_extension');
      await ctx3.close();

      const ctx4 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const p4 = await ctx4.newPage();
      await login(p4, 'e2e.nocall');
      await sleep(1500);
      check('nocall: a user without calls_make has no softphone at all', (await byTest(p4, 'softphone-chip').count()) === 0);
      await ctx4.close();
    }
  } catch (e) {
    check('run completed without an exception', false, e && e.stack ? e.stack : e);
    try {
      if (!page.isClosed()) await shot(page, 'softphone_99_failure');
    } catch {
      /* no page */
    }
  } finally {
    ami.close();
    await browser.close().catch(() => undefined);
    const failed = results.filter((r) => !r.ok);
    log(`\n${results.length - failed.length}/${results.length} checks passed in ${Math.round((Date.now() - started) / 1000)} s`);
    fs.writeFileSync(path.join(outDir, 'softphone-verify.json'), JSON.stringify({ results, evidence, credentialsIssued: issued.length }, null, 2));
    fs.writeFileSync(path.join(outDir, 'softphone-verify.log'), logLines.join('\n') + '\n');
    teardown();
    process.exit(failed.length ? 1 : 0);
  }
})();
