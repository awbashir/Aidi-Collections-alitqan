/* Phase 6 Q3 frontend checks: the voice training lab (/voice-lab), driven by headless Chrome
 * through the playwright-core install in ../tools/node_modules. Start the dev server first.
 *
 *   node CAWP-frontend/scripts/verify-phase6-voicelab.cjs [outDir]      (from old/)
 *
 * FE_URL (default http://localhost:5177), API_URL (default http://localhost:9797).
 * Screenshots (ar/en) and verify.json/verify.log go to outDir (default logs/screens/phase6-voicelab).
 *
 * NO REAL PROVIDER IS EVER CONTACTED:
 *   - `live`: the real API (:9797) and its real readiness (`GET /v2/ai/status`); the page must show
 *     the honest not-configured state and never ask for the microphone. Any request to a host other
 *     than the app and the API fails the check.
 *   - the other sections stub the voice lab API (`/v2/ai/status`, `/v2/ai/voice-lab/*`) and the
 *     provider's SDP endpoint (https://realtime.stub.invalid/...) with Playwright route interception.
 *     The "provider" answer is produced by a second RTCPeerConnection inside the same page (a
 *     local loopback peer with a test tone), so the WebRTC path is real but nothing leaves the
 *     machine. Requests to *.openai.com are aborted and counted (must stay 0).
 *   - the microphone is Chromium's fake device (--use-fake-device-for-media-stream). On macOS a
 *     host Chrome cannot open even the fake device without the OS microphone permission (the
 *     call hangs), so the browser runs in Docker: a one-off container from the same Playwright
 *     image the softphone e2e uses (mcr.microsoft.com/playwright:v1.63.0-noble), exposing a
 *     Playwright server on 127.0.0.1:3311. Inside it, localhost:5177 and localhost:9797 are
 *     forwarded to the host (a small forwarder here bridges host.docker.internal:15177 to the
 *     Vite server, which listens on [::1] only), so the app keeps its secure localhost origin.
 *     The container is removed at the end (KEEP=1 keeps it; PW_WS=<ws url> uses another server).
 *
 * ONLY=<section,...>: live, gating, session, limit, navigate, failures.
 * Writes nothing to the database (the live section only reads).
 */
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { execFileSync } = require('child_process');
const ROOT = path.resolve(__dirname, '../..');
const { chromium } = require(path.join(ROOT, 'tools/node_modules/playwright-core'));

const FE = process.env.FE_URL || 'http://localhost:5177';
const API = process.env.API_URL || 'http://localhost:9797';
const outDir = path.resolve(process.argv[2] || path.join(ROOT, 'logs/screens/phase6-voicelab'));
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (s) => ONLY.length === 0 || ONLY.includes(s);
const PASS = 'Aidi@2026';
const ADMIN = process.env.P6_USER || 'aidi.dev';
const COLLECTOR = 'aidi.collector';
const STUB_RTC = 'https://realtime.stub.invalid/v1/realtime/calls';
const SECRET = 'ek_STUB_ephemeral_secret_for_tests';
const USER_ID = '00000000-0000-4000-8000-00000000a3b1';
const PW_IMAGE = 'mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27';
const PW_CONTAINER = 'cawp-voicelab-e2e-browser';
const PW_PORT = 3311;
const BRIDGE_PORT = 15177;
const API_PROXY_PORT = 19797;
/* The API as the container browser sees it goes through a small recording proxy on the host
 * (pass-through to API_URL). Only while `proxyState.stubEnd` is on does it answer the voice lab
 * end route itself: a report sent with fetch(keepalive) while the page unloads cannot be seen by
 * Playwright's route interception, so the proxy records it instead. */
const proxyState = { stubEnd: false, ends: [] };
const END_RE = /\/v2\/ai\/voice-lab\/sessions\/[^/]+\/end$/;
const BROWSER_ARGS = [
  '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream',
  '--autoplay-policy=no-user-gesture-required',
  '--disable-features=WebRtcHideLocalIpsWithMdns',
  '--disable-dev-shm-usage',
];
/* runs inside the container: loopback forwarders + a Playwright server */
const SERVER_JS = `
const net = require('net');
const { chromium } = require('/opt/pw/playwright-core');
function fwd(lp, host, port) {
  net.createServer((c) => {
    const u = net.connect(port, host);
    c.pipe(u).pipe(c);
    const x = () => { c.destroy(); u.destroy(); };
    u.on('error', x); c.on('error', x);
  }).listen(lp, '127.0.0.1');
}
(async () => {
  fwd(5177, 'host.docker.internal', ${BRIDGE_PORT});
  fwd(9797, 'host.docker.internal', ${API_PROXY_PORT});
  const s = await chromium.launchServer({ host: '0.0.0.0', port: 3000, wsPath: '/voicelab', headless: true,
    args: ${JSON.stringify(BROWSER_ARGS)} });
  console.log('voicelab browser ready ' + s.wsEndpoint());
  process.on('SIGTERM', async () => { await s.close().catch(() => {}); process.exit(0); });
})();
`;

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
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: true });

/* ------------------------------------------------------------ API (read-only) */
async function apiLogin(user) {
  const r = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: PASS }),
  });
  return (await r.json()).access_token;
}
async function apiGet(user, p) {
  const tok = await apiLogin(user);
  const r = await fetch(API + p, { headers: { Authorization: 'Bearer ' + tok } });
  let j = null;
  try {
    j = await r.json();
  } catch {
    /* empty */
  }
  return { status: r.status, body: j, data: j && j.data };
}

/* ------------------------------------------------------------ UI helpers */
async function login(page, user = ADMIN) {
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
const attr = (page, sel, name) => page.getAttribute(sel, name).catch(() => null);
const phase = (page) => attr(page, '[data-testid="p6vl-phase"]', 'data-phase');
async function waitPhase(page, want, timeout = 20000) {
  const t0 = Date.now();
  let p = null;
  while (Date.now() - t0 < timeout) {
    p = await phase(page);
    if ((Array.isArray(want) ? want : [want]).includes(p)) return p;
    await sleep(150);
  }
  return p;
}

/* Instrumentation (before any page script): counts getUserMedia calls, keeps the streams it
 * returned, and the app's RTCPeerConnections. `window.__vl.OrigPC` is used by the fake provider. */
const INSTRUMENT = () => {
  const vl = { gum: 0, streams: [], pcs: [], deny: false, providerEvents: [] };
  window.__vl = vl;
  const md = navigator.mediaDevices;
  if (md && md.getUserMedia) {
    const orig = md.getUserMedia.bind(md);
    md.getUserMedia = async (c) => {
      vl.gum += 1;
      if (vl.deny) throw new DOMException('denied by the test', 'NotAllowedError');
      const s = await orig(c);
      vl.streams.push(s);
      return s;
    };
  }
  const OrigPC = window.RTCPeerConnection;
  vl.OrigPC = OrigPC;
  class TrackedPC extends OrigPC {
    constructor(...a) {
      super(...a);
      vl.pcs.push(this);
    }
  }
  window.RTCPeerConnection = TrackedPC;
};
const micState = (page) =>
  page.evaluate(() => {
    const tracks = window.__vl.streams.flatMap((s) => s.getTracks());
    return {
      gum: window.__vl.gum,
      tracks: tracks.map((t) => ({ state: t.readyState, enabled: t.enabled, kind: t.kind })),
      pcs: window.__vl.pcs.map((p) => ({ conn: p.connectionState, sig: p.signalingState })),
      providerEvents: window.__vl.providerEvents.slice(),
    };
  });

/* The "provider": answer the app's offer from a second peer in the same page (loopback). */
const FAKE_PROVIDER = async (offerSdp) => {
  const vl = window.__vl;
  const pc = new vl.OrigPC();
  vl.fakePeers = (vl.fakePeers || []).concat([pc]);
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const dst = ctx.createMediaStreamDestination();
    osc.connect(dst);
    osc.start();
    dst.stream.getTracks().forEach((t) => pc.addTrack(t, dst.stream));
  } catch {
    pc.addTransceiver('audio', { direction: 'sendonly' });
  }
  pc.ondatachannel = (e) => {
    const ch = e.channel;
    ch.onmessage = (m) => vl.providerEvents.push(String(m.data));
    ch.onopen = () => ch.send(JSON.stringify({ type: 'session.created', session: { id: 'sess_stub' } }));
  };
  await pc.setRemoteDescription({ type: 'offer', sdp: offerSdp });
  const answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);
  await new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve();
    pc.addEventListener('icegatheringstatechange', () => pc.iceGatheringState === 'complete' && resolve());
    setTimeout(resolve, 3000);
  });
  return pc.localDescription.sdp;
};

/* ------------------------------------------------------------ stubs */
const CATALOGUE = {
  scenarios: [
    {
      id: 'cooperative', version: 1, difficulty: 'easy', persona_name: { ar: 'خالد', en: 'Khalid' },
      title: { ar: 'عميل متعاون', en: 'Cooperative customer' },
      summary: { ar: 'عميل نسي فاتورته ويرغب في السداد.', en: 'A customer who forgot a bill and wants to pay.' },
      brief: { ar: 'تتصل بخالد بخصوص فاتورة افتراضية. كل البيانات افتراضية.', en: 'You are calling Khalid about a fictional bill. Every detail is fictional.' },
      goals: { ar: ['عرّف بنفسك', 'تحقق من الهوية', 'اتفق على خطوة'], en: ['Introduce yourself', 'Confirm identity', 'Agree a step'] },
      fictional: true,
    },
    {
      id: 'angry_dispute', version: 1, difficulty: 'hard', persona_name: { ar: 'فهد', en: 'Fahad' },
      title: { ar: 'اعتراض غاضب', en: 'Angry dispute' },
      summary: { ar: 'عميل غاضب يعترض.', en: 'An angry customer disputes the amount.' },
      brief: { ar: 'تتصل بفهد. كل البيانات افتراضية.', en: 'You are calling Fahad. Every detail is fictional.' },
      goals: { ar: ['حافظ على هدوئك', 'لا تجادل', 'اعرض تسجيل اعتراض'], en: ['Stay calm', 'Do not argue', 'Offer a dispute review'] },
      fictional: true,
    },
  ],
  languages: ['ar', 'en'],
  limits: { max_seconds: 300, sessions_per_hour: 6, sessions_per_day: 20 },
  readiness: { ready: true, reasons: [] },
  fictional: true,
};
const READY_STATUS = {
  provider: 'openai',
  capabilities: {
    generate_structured: { ready: true, reasons: [], required_categories: [] },
    transcribe: { ready: true, reasons: [], required_categories: ['audio'] },
    realtime_session: { ready: true, reasons: [], required_categories: [] },
  },
  reasons: [],
};
const CORS = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
const record = (id, over = {}) => ({
  id, user_id: USER_ID, scenario_id: 'cooperative', scenario_version: 1,
  scenario_title: { ar: 'عميل متعاون', en: 'Cooperative customer' }, language: 'ar', state: 'active', outcome: null,
  error_code: null, max_seconds: 300, started_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 360000).toISOString(), activated_at: new Date().toISOString(), ended_at: null,
  duration_seconds: null, connected_seconds: null, provider_call_id: '11111111-2222-4333-8444-555555555555',
  model: 'gpt-realtime-stub', policy_revision: 1, ...over,
});

async function stubbedPage(browser, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['microphone'] });
  const page = await ctx.newPage();
  const s = { starts: [], ends: [], sdp: [], openai: 0, foreign: [], mode: opts.mode || 'ok', seq: 0, endSeen: [] };
  page.on('pageerror', (e) => s.foreign.push('pageerror: ' + String(e).slice(0, 200)));
  await page.addInitScript(INSTRUMENT);
  if (opts.clock) await page.clock.install();
  const json = (route, status, body) =>
    route.request().method() === 'OPTIONS'
      ? route.fulfill({ status: 204, headers: CORS })
      : route.fulfill({ status, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) });
  await ctx.route(/\/v2\/ai\/status(\?|$)/, (route) => json(route, 200, { status: true, message: 'ok', data: opts.status || READY_STATUS }));
  await ctx.route(/\/v2\/ai\/voice-lab\/scenarios(\?|$)/, (route) => json(route, 200, { status: true, message: 'ok', data: CATALOGUE }));
  await ctx.route(/\/v2\/ai\/voice-lab\/sessions(\?.*)?$/, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return json(route, 204, {});
    if (req.method() === 'GET') {
      return json(route, 200, { status: true, message: 'ok', data: { items: [], total: 0, page: 1, page_size: 10, active: null, usage: { last_hour: s.starts.length, today: s.starts.length, sessions_per_hour: 6, sessions_per_day: 20 } } });
    }
    s.starts.push({ body: JSON.parse(req.postData() || '{}'), headers: await req.allHeaders() });
    if (s.mode === 'active409') {
      return json(route, 409, { status: false, code: 'voice_lab_session_active', message: 'open', error: { session_id: 'x', state: 'active' } });
    }
    const id = `00000000-0000-4000-8000-${String(++s.seq).padStart(12, '0')}`;
    const sc = CATALOGUE.scenarios.find((x) => x.id === s.starts[s.starts.length - 1].body.scenario_id) || CATALOGUE.scenarios[0];
    const lang = s.starts[s.starts.length - 1].body.language === 'en' ? 'en' : 'ar';
    return json(route, 201, {
      status: true, message: 'voice lab session started',
      data: {
        session: record(id, { scenario_id: sc.id, scenario_title: sc.title, language: lang }), client_secret: SECRET, expires_at: new Date(Date.now() + 300000).toISOString(),
        model: 'gpt-realtime-stub', max_seconds: 300, webrtc_url: STUB_RTC,
        persona_name: sc.persona_name,
        instructions_summary: lang === 'ar' ? `يؤدي الذكاء الاصطناعي دور ${sc.persona_name.ar}، شخصية افتراضية. (رد تجريبي من سكربت الاختبار)` : `The AI plays ${sc.persona_name.en}, a fictional customer. (test stub answer)`,
      },
    });
  });
  await ctx.route(END_RE, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return json(route, 204, {});
    const body = JSON.parse(req.postData() || '{}');
    const id = req.url().split('/sessions/')[1].split('/')[0];
    s.ends.push({ id, body });
    const conn = body.connected_seconds ?? null;
    return json(route, 200, { status: true, message: 'ended', data: { changed: true, session: record(id, { state: 'ended', outcome: body.outcome, ended_at: new Date().toISOString(), duration_seconds: conn ?? 0, connected_seconds: conn }) } });
  });
  await ctx.route('https://realtime.stub.invalid/**', async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS });
    const headers = await req.allHeaders();
    s.sdp.push({ method: req.method(), auth: headers.authorization, ctype: headers['content-type'], offer: req.postData() || '', cookie: headers.cookie || null });
    if (s.mode === 'sdp500') return route.fulfill({ status: 500, headers: CORS, body: 'stub failure' });
    const answer = await page.evaluate(FAKE_PROVIDER, req.postData());
    return route.fulfill({ status: 201, headers: CORS, contentType: 'application/sdp', body: answer });
  });
  await ctx.route(/openai\.com/, (route) => {
    s.openai += 1;
    return route.abort();
  });
  ctx.on('request', (r) => {
    if (/\/end$/.test(r.url())) s.endSeen.push(`${r.method()} ${r.url()}`);
  });
  page.on('request', (r) => {
    const u = r.url();
    if (!u.startsWith(FE) && !u.startsWith(API) && !u.startsWith(STUB_RTC) && !u.startsWith('data:') && !u.startsWith('blob:')) s.foreign.push(u);
  });
  await login(page);
  return { ctx, page, s };
}

async function openLab(page) {
  await page.goto(FE + '/voice-lab', { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="p6vl-page"]', { timeout: 20000 });
  await page.waitForTimeout(500);
}

/* ------------------------------------------------------------ sections */
async function live(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const foreign = [];
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  page.on('request', (r) => {
    const u = r.url();
    if (!u.startsWith(FE) && !u.startsWith(API) && !u.startsWith('data:') && !u.startsWith('blob:')) foreign.push(u);
  });
  await page.addInitScript(INSTRUMENT);
  await login(page);
  const st = await apiGet(ADMIN, '/v2/ai/status');
  const cap = st.data?.capabilities?.realtime_session || { reasons: [] };
  const apiReasons = [...cap.reasons];
  if (st.data?.quota?.exceeded && !apiReasons.includes('ai_quota_exceeded')) apiReasons.push('ai_quota_exceeded');
  const scen = await apiGet(ADMIN, '/v2/ai/voice-lab/scenarios');
  log(`live API: /v2/ai/status realtime_session = ${JSON.stringify(cap)}; /v2/ai/voice-lab/scenarios -> ${scen.status}`);
  for (const lang of ['ar', 'en']) {
    await setLang(page, lang);
    await openLab(page);
    const banner = await page.textContent('[data-testid="p6vl-banner"]').catch(() => '');
    check(`live ${lang}: the "fictional training, not a real debt call" banner`, lang === 'en' ? /Fictional training/.test(banner) && /not a real debt call/.test(banner) : /تدريب افتراضي/.test(banner) && /ليست مكالمة تحصيل حقيقية/.test(banner), banner.slice(0, 120));
    if (apiReasons.length) {
      const shown = (await attr(page, '[data-testid="p6vl-unconfigured"] ul', 'data-reasons')) || '';
      const order = ['ai_not_configured', 'model_not_set', 'ai_policy_required', 'ai_policy_category_denied', 'ai_quota_exceeded'];
      const expected = order.filter((r) => apiReasons.includes(r)).concat(apiReasons.filter((r) => !order.includes(r)));
      check(`live ${lang}: the honest not-configured state equals the API's reasons`, shown === expected.join(','), `page=${shown} api=${expected.join(',')}`);
      const txt = await page.textContent('[data-testid="p6vl-unconfigured"]').catch(() => '');
      check(`live ${lang}: the reasons are in words and say there is no simulation`, lang === 'en' ? /no demo or simulated conversation/.test(txt) : /لا توجد محادثة تجريبية أو محاكاة/.test(txt), txt.slice(0, 160));
      const start = page.locator('[data-testid="p6vl-start"]');
      check(`live ${lang}: Start is disabled`, (await start.count()) === 1 && (await start.getAttribute('aria-disabled')) === 'true', await start.getAttribute('aria-disabled'));
      await start.click({ force: true, timeout: 2000 }).catch(() => {});
      await page.waitForTimeout(500);
    } else {
      check(`live ${lang}: the API reports realtime ready (unexpected on this stack; no key expected)`, false, JSON.stringify(cap));
    }
    if (scen.status === 404) {
      const c = await page.textContent('[data-testid="p6vl-catalogue-error"]').catch(() => '');
      check(`live ${lang}: the running API has no voice lab routes yet (needs a restart) and the page says so`, lang === 'en' ? /not available on this server/.test(c) : /غير متاحة على هذا الخادم/.test(c), c);
    } else {
      const n = await page.locator('[data-testid="p6vl-scenario"]').count();
      check(`live ${lang}: the scenario catalogue from the API is listed`, n === (scen.data?.scenarios || []).length && n >= 5, n);
    }
    const m = await micState(page);
    check(`live ${lang}: the microphone was never requested, no peer connection`, m.gum === 0 && m.pcs.length === 0, JSON.stringify(m));
    await shot(page, `01_live_unconfigured_${lang}`);
  }
  const nav = await page.$$eval('.side-nav a[href="/voice-lab"]', (els) => els.map((e) => e.textContent.trim()));
  check('live: the sidebar has the voice lab item (Intelligence & control) for aidi.dev', nav.length === 1, JSON.stringify(nav));
  check('live: no request to any host other than the app and the API', foreign.length === 0, foreign.join(' '));
  check('live: no page error', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

async function gating(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  await login(page, COLLECTOR);
  const nav = await page.$$eval('.side-nav a[href="/voice-lab"]', (els) => els.length);
  check('gating: aidi.collector (no voice_lab) has no voice lab item', nav === 0, nav);
  await page.goto(FE + '/voice-lab', { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  check('gating: aidi.collector /voice-lab -> access-denied', page.url().includes('/access-denied'), page.url());
  const st = await apiGet(COLLECTOR, '/v2/ai/status');
  check('gating: the API agrees (GET /v2/ai/status 403 for aidi.collector)', st.status === 403, st.status);
  await ctx.close();
}

async function session(browser) {
  const { ctx, page, s } = await stubbedPage(browser);
  for (const lang of ['en', 'ar']) {
    await setLang(page, lang);
    await openLab(page);
    let m = await micState(page);
    check(`session ${lang}: nothing asks for the microphone before Start`, m.gum === 0, m.gum);
    const start = page.locator('[data-testid="p6vl-start"]');
    check(`session ${lang}: Start is enabled when the API says ready`, (await start.getAttribute('aria-disabled')) === 'false', await start.getAttribute('aria-disabled'));
    if (lang === 'en') {
      await page.click('[data-testid="p6vl-scenario"][data-id="angry_dispute"]');
      await page.click('[data-testid="p6vl-lang"] [data-value="en"]');
    }
    const startsBefore = s.starts.length;
    const sdpBefore = s.sdp.length;
    await start.click();
    const p = await waitPhase(page, 'live', 25000);
    check(`session ${lang}: Start -> microphone -> minted secret -> WebRTC -> live`, p === 'live', p);
    m = await micState(page);
    check(`session ${lang}: the microphone was requested once, after Start`, m.gum === 1 && m.tracks.length >= 1 && m.tracks.every((t) => t.state === 'live'), JSON.stringify(m.tracks));
    const st = s.starts[startsBefore];
    const wantBody = lang === 'en' ? { scenario_id: 'angry_dispute', language: 'en' } : { scenario_id: 'cooperative', language: 'ar' };
    check(`session ${lang}: POST /v2/ai/voice-lab/sessions sent only {scenario_id, language}`, st && JSON.stringify(st.body) === JSON.stringify(wantBody), st && JSON.stringify(st.body));
    const sdp = s.sdp[sdpBefore];
    check(`session ${lang}: the SDP offer went to the provider endpoint the server named, with the EPHEMERAL secret as bearer`, sdp && sdp.method === 'POST' && sdp.auth === `Bearer ${SECRET}` && /application\/sdp/.test(sdp.ctype || '') && sdp.offer.startsWith('v=0') && /m=audio/.test(sdp.offer) && /webrtc-datachannel|m=application/.test(sdp.offer), sdp && `${sdp.method} ${sdp.auth} ${sdp.ctype} ${sdp.offer.slice(0, 20)}`);
    check(`session ${lang}: no cookie is sent to the provider`, sdp && !sdp.cookie, sdp && sdp.cookie);
    const mic = await attr(page, '[data-testid="p6vl-mic"]', 'data-live');
    check(`session ${lang}: the microphone indicator is on`, mic === 'true', mic);
    const e0 = Number(await attr(page, '[data-testid="p6vl-timer"]', 'data-elapsed'));
    await page.waitForTimeout(2600);
    const e1 = Number(await attr(page, '[data-testid="p6vl-timer"]', 'data-elapsed'));
    const lim = Number(await attr(page, '[data-testid="p6vl-timer"]', 'data-limit'));
    check(`session ${lang}: the live timer runs (limit 5:00)`, e1 >= e0 + 2 && lim === 300, `${e0} -> ${e1}, limit ${lim}`);
    const pcs = (await micState(page)).pcs;
    check(`session ${lang}: the peer connection to the (stub) provider is connected`, pcs.length === 1 && pcs[0].conn === 'connected', JSON.stringify(pcs));
    await page.waitForTimeout(400);
    const ev = (await micState(page)).providerEvents;
    check(`session ${lang}: the browser only asks the persona to answer (response.create), never changes the session`, ev.length >= 1 && ev.every((x) => JSON.parse(x).type === 'response.create'), JSON.stringify(ev));
    // mute / unmute
    await page.click('[data-testid="p6vl-mute"]');
    await page.waitForTimeout(200);
    m = await micState(page);
    check(`session ${lang}: Mute disables the microphone track (indicator "muted")`, m.tracks.every((t) => !t.enabled) && (await attr(page, '[data-testid="p6vl-mic"]', 'data-muted')) === 'true', JSON.stringify(m.tracks));
    await page.click('[data-testid="p6vl-mute"]');
    await page.waitForTimeout(200);
    m = await micState(page);
    check(`session ${lang}: Unmute enables it again`, m.tracks.every((t) => t.enabled), JSON.stringify(m.tracks));
    await shot(page, `10_session_live_${lang}`);
    // end
    const endsBefore = s.ends.length;
    await page.click('[data-testid="p6vl-end"]');
    const pe = await waitPhase(page, 'ended', 8000);
    await page.waitForTimeout(600);
    m = await micState(page);
    check(`session ${lang}: End stops every microphone track`, pe === 'ended' && m.tracks.length >= 1 && m.tracks.every((t) => t.state === 'ended'), JSON.stringify(m.tracks));
    check(`session ${lang}: End closes the peer connection`, m.pcs.every((x) => x.sig === 'closed'), JSON.stringify(m.pcs));
    const end = s.ends[endsBefore];
    check(`session ${lang}: the end is reported with outcome + connected seconds only`, end && end.body.outcome === 'user_ended' && Number.isInteger(end.body.connected_seconds) && Object.keys(end.body).sort().join() === 'connected_seconds,outcome', end && JSON.stringify(end.body));
    check(`session ${lang}: the indicator shows the microphone off; outcome shown`, (await attr(page, '[data-testid="p6vl-mic"]', 'data-live')) === 'false' && (await attr(page, '[data-testid="p6vl-outcome"] [data-outcome]', 'data-outcome')) === 'user_ended');
    await shot(page, `11_session_ended_${lang}`);
    // reset the counters for the next language
    await page.evaluate(() => {
      window.__vl.gum = 0;
      window.__vl.streams = [];
      window.__vl.pcs = [];
      window.__vl.providerEvents = [];
    });
  }
  check('session: no request to *.openai.com', s.openai === 0, s.openai);
  check('session: no request to an unexpected host; no page error', s.foreign.length === 0, s.foreign.join(' '));
  await ctx.close();
}

async function limit(browser) {
  const { ctx, page, s } = await stubbedPage(browser, { clock: true });
  await setLang(page, 'en');
  await openLab(page);
  await page.click('[data-testid="p6vl-start"]');
  const p = await waitPhase(page, 'live', 25000);
  check('limit: session live under the test clock', p === 'live', p);
  await page.clock.fastForward('04:50');
  await page.waitForTimeout(700);
  const el = Number(await attr(page, '[data-testid="p6vl-timer"]', 'data-elapsed'));
  const rem = Number(await attr(page, '[data-testid="p6vl-timer"]', 'data-remaining'));
  check('limit: at 4:50 the session is still live and the timer shows it', (await phase(page)) === 'live' && el >= 289 && el <= 295 && rem <= 11, `phase=${await phase(page)} elapsed=${el} remaining=${rem}`);
  await shot(page, '20_limit_near_en');
  await page.clock.fastForward('00:12');
  const pe = await waitPhase(page, 'ended', 8000);
  await page.waitForTimeout(600);
  const m = await micState(page);
  check('limit: the 5-minute limit ends the session by itself', pe === 'ended' && (await attr(page, '[data-testid="p6vl-outcome"] [data-outcome]', 'data-outcome')) === 'time_limit', pe);
  check('limit: every microphone track is stopped and the connection closed', m.tracks.length >= 1 && m.tracks.every((t) => t.state === 'ended') && m.pcs.every((x) => x.sig === 'closed'), JSON.stringify(m));
  const end = s.ends[s.ends.length - 1];
  check('limit: the end is reported as time_limit, connected seconds capped at 300', end && end.body.outcome === 'time_limit' && end.body.connected_seconds >= 290 && end.body.connected_seconds <= 300, end && JSON.stringify(end.body));
  await shot(page, '21_limit_reached_en');
  await ctx.close();
}

async function navigate(browser) {
  const { ctx, page, s } = await stubbedPage(browser);
  await setLang(page, 'en');
  await openLab(page);
  await page.click('[data-testid="p6vl-start"]');
  check('navigate: session live', (await waitPhase(page, 'live', 25000)) === 'live');
  // leave through the app's own navigation (SPA): the page unmounts
  await page.click('.side-nav a[href="/home"]').catch(async () => page.evaluate(() => window.history.pushState({}, '', '/home')));
  await page.waitForURL('**/home', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(1200);
  let m = await micState(page);
  check('navigate: leaving the page stops every microphone track and closes the connection', m.tracks.length >= 1 && m.tracks.every((t) => t.state === 'ended') && m.pcs.every((x) => x.sig === 'closed'), JSON.stringify(m));
  let end = s.ends[s.ends.length - 1];
  check('navigate: the end is reported as navigated_away', end && end.body.outcome === 'navigated_away', end && JSON.stringify(end.body));
  // closing / reloading the tab: pagehide -> keepalive end report
  await openLab(page);
  await page.click('[data-testid="p6vl-start"]');
  check('navigate: a second session live', (await waitPhase(page, 'live', 25000)) === 'live');
  const sid = s.seq;
  proxyState.stubEnd = true;
  proxyState.ends = [];
  // a request sent by an unloading page is not delivered to Playwright route handlers (it is lost
  // while interception is on), so interception is switched off for this step and the recording
  // proxy answers the end route instead
  await ctx.unrouteAll({ behavior: 'ignoreErrors' });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  proxyState.stubEnd = false;
  const posted = proxyState.ends.find((e) => e.method === 'POST' && e.url.endsWith(`${String(sid).padStart(12, '0')}/end`));
  const pre = proxyState.ends.find((e) => e.method === 'OPTIONS');
  let pb = null;
  try {
    pb = posted && JSON.parse(posted.body);
  } catch {
    /* not JSON */
  }
  check('navigate: a reload (pagehide) sends the end report with fetch(keepalive): preflight + POST {outcome: navigated_away}, with the bearer token',
    !!pre && !!posted && posted.auth && pb && pb.outcome === 'navigated_away', JSON.stringify(proxyState.ends.map((e) => `${e.method} ${e.body}`)));
  m = await micState(page);
  check('navigate: after the reload nothing holds the microphone (fresh page, no request)', m.gum === 0, JSON.stringify(m));
  await ctx.close();
}

async function failures(browser) {
  // the provider refuses the SDP offer
  {
    const { ctx, page, s } = await stubbedPage(browser, { mode: 'sdp500' });
    await setLang(page, 'en');
    await openLab(page);
    await page.click('[data-testid="p6vl-start"]');
    const p = await waitPhase(page, 'failed', 20000);
    await page.waitForTimeout(500);
    const m = await micState(page);
    const err = await page.textContent('[data-testid="p6vl-error"]').catch(() => '');
    check('failures: a provider SDP refusal fails honestly (HTTP 500 shown)', p === 'failed' && /refused the connection \(HTTP 500\)/.test(err), `${p} ${err}`);
    check('failures: ... and releases the microphone', m.tracks.length >= 1 && m.tracks.every((t) => t.state === 'ended'), JSON.stringify(m.tracks));
    const end = s.ends[s.ends.length - 1];
    check('failures: ... and reports setup_failed', end && end.body.outcome === 'setup_failed', end && JSON.stringify(end.body));
    await shot(page, '30_failure_sdp_en');
    await ctx.close();
  }
  // microphone denied: no session is requested
  {
    const { ctx, page, s } = await stubbedPage(browser);
    await setLang(page, 'ar');
    await openLab(page);
    await page.evaluate(() => {
      window.__vl.deny = true;
    });
    await page.click('[data-testid="p6vl-start"]');
    const p = await waitPhase(page, 'failed', 10000);
    const err = await page.textContent('[data-testid="p6vl-error"]').catch(() => '');
    check('failures: microphone denied -> failed with the reason in words (ar), no session minted', p === 'failed' && /تم حظر الوصول إلى الميكروفون/.test(err) && s.starts.length === 0 && s.sdp.length === 0, `${p} ${err} starts=${s.starts.length}`);
    await shot(page, '31_failure_mic_denied_ar');
    await ctx.close();
  }
  // the one-open-session rule (409) is explained and the microphone released
  {
    const { ctx, page, s } = await stubbedPage(browser, { mode: 'active409' });
    await setLang(page, 'en');
    await openLab(page);
    await page.click('[data-testid="p6vl-start"]');
    const p = await waitPhase(page, 'failed', 10000);
    await page.waitForTimeout(400);
    const m = await micState(page);
    const err = await page.textContent('[data-testid="p6vl-error"]').catch(() => '');
    check('failures: 409 voice_lab_session_active is explained; microphone released; no provider call', p === 'failed' && /already have an open session/.test(err) && m.tracks.every((t) => t.state === 'ended') && s.sdp.length === 0, `${p} ${err}`);
    await ctx.close();
  }
  // stubbed "not configured" + quota: Start disabled, no microphone
  {
    const status = {
      ...READY_STATUS,
      capabilities: { ...READY_STATUS.capabilities, realtime_session: { ready: false, reasons: ['model_not_set', 'ai_policy_required'], required_categories: [] } },
      reasons: ['model_not_set', 'ai_policy_required'],
      quota: { day: '2026-09-24', resets_at: '2026-09-24T21:00:00+00:00', user: { limit: 50, used: 50, remaining: 0 }, total: { limit: 1000, used: 60, remaining: 940 }, exceeded: 'user' },
    };
    const { ctx, page } = await stubbedPage(browser, { status });
    await setLang(page, 'en');
    await openLab(page);
    const shown = await attr(page, '[data-testid="p6vl-unconfigured"] ul', 'data-reasons');
    const dis = (await page.locator('[data-testid="p6vl-start"]').getAttribute('aria-disabled')) === 'true';
    await page.locator('[data-testid="p6vl-start"]').click({ force: true, timeout: 2000 }).catch(() => {});
    const m = await micState(page);
    check('failures: model_not_set + no policy + quota used up are listed; Start disabled; no microphone', shown === 'model_not_set,ai_policy_required,ai_quota_exceeded' && dis && m.gum === 0, `${shown} disabled=${dis} gum=${m.gum}`);
    await shot(page, '32_not_ready_stub_en');
    await ctx.close();
  }
}

/* host side: host.docker.internal:BRIDGE_PORT -> the Vite server ([::1]:5177 or 127.0.0.1:5177) */
function bridge() {
  const fe = new URL(FE);
  const server = net.createServer((c) => {
    const u = net.connect(Number(fe.port || 80), fe.hostname === 'localhost' ? '::1' : fe.hostname);
    u.on('error', () => {
      const v = net.connect(Number(fe.port || 80), '127.0.0.1');
      c.pipe(v).pipe(c);
      v.on('error', () => c.destroy());
    });
    u.on('connect', () => c.pipe(u).pipe(c));
    c.on('error', () => u.destroy());
  });
  server.listen(BRIDGE_PORT, '127.0.0.1');
  return server;
}

function apiProxy() {
  const up = new URL(API);
  const server = http.createServer((req, res) => {
    const origin = req.headers.origin || '*';
    if (proxyState.stubEnd && /\/v2\/ai\/voice-lab\/sessions\/[^/]+\/end$/.test(req.url)) {
      let body = '';
      req.on('data', (d) => (body += d));
      req.on('end', () => {
        proxyState.ends.push({ method: req.method, url: req.url, body, auth: !!req.headers.authorization });
        const cors = { 'access-control-allow-origin': origin, 'access-control-allow-headers': 'authorization, content-type, idempotency-key', 'access-control-allow-methods': 'POST, OPTIONS' };
        if (req.method === 'OPTIONS') {
          res.writeHead(204, cors);
          return res.end();
        }
        res.writeHead(200, { ...cors, 'content-type': 'application/json' });
        return res.end(JSON.stringify({ status: true, message: 'ended', data: { changed: true, session: null } }));
      });
      return;
    }
    const p = http.request({ host: up.hostname === 'localhost' ? '127.0.0.1' : up.hostname, port: Number(up.port || 80), method: req.method, path: req.url, headers: req.headers }, (r) => {
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    });
    p.on('error', () => {
      res.writeHead(502);
      res.end();
    });
    req.pipe(p);
  });
  server.listen(API_PROXY_PORT, '127.0.0.1');
  return server;
}

async function dockerBrowser() {
  try {
    execFileSync('docker', ['rm', '-f', PW_CONTAINER], { stdio: 'ignore' });
  } catch {
    /* not running */
  }
  execFileSync('docker', ['run', '-d', '--rm', '--init', '--name', PW_CONTAINER, '--shm-size', '256m',
    '--add-host', 'host.docker.internal:host-gateway', '-p', `127.0.0.1:${PW_PORT}:3000`,
    '-v', `${path.join(ROOT, 'tools/node_modules/playwright-core')}:/opt/pw/playwright-core:ro`,
    PW_IMAGE, 'node', '-e', SERVER_JS], { stdio: 'ignore' });
  const ws = `ws://127.0.0.1:${PW_PORT}/voicelab`;
  for (let i = 0; i < 60; i += 1) {
    try {
      return await chromium.connect(ws, { timeout: 5000 });
    } catch {
      await sleep(1000);
    }
  }
  throw new Error('the Playwright server in Docker did not start');
}

(async () => {
  let browser;
  let fwd = null;
  let proxy = null;
  let started = false;
  try {
    if (process.env.PW_WS) {
      browser = await chromium.connect(process.env.PW_WS);
    } else {
      fwd = bridge();
      proxy = apiProxy();
      browser = await dockerBrowser();
      started = true;
    }
    log(`browser: ${process.env.PW_WS || 'docker ' + PW_CONTAINER} (${browser.version()})`);
    if (want('live')) await live(browser);
    if (want('gating')) await gating(browser);
    if (want('session')) await session(browser);
    if (want('limit')) await limit(browser);
    if (want('navigate')) await navigate(browser);
    if (want('failures')) await failures(browser);
  } catch (e) {
    check('script finished without an exception', false, e.stack || String(e));
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (started && process.env.KEEP !== '1') {
      try {
        execFileSync('docker', ['rm', '-f', PW_CONTAINER], { stdio: 'ignore' });
      } catch {
        /* already gone */
      }
    }
    if (fwd) fwd.close();
    if (proxy) proxy.close();
    const passed = results.filter((r) => r.ok).length;
    log(`\n${passed}/${results.length} checks passed`);
    fs.writeFileSync(path.join(outDir, 'verify.json'), JSON.stringify({ fe: FE, api: API, passed, total: results.length, results }, null, 2));
    fs.writeFileSync(path.join(outDir, 'verify.log'), logLines.join('\n') + '\n');
    process.exit(passed === results.length ? 0 : 1);
  }
})();
