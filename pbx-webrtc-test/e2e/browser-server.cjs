/* Chromium for the softphone end-to-end run, inside the compose network (TEST ONLY).
 *
 * Runs in the official Playwright image (compose service `softphone-e2e-browser`) and exposes a
 * Playwright server on :3000 (published on 127.0.0.1:3310) that the host script
 * CAWP-frontend/scripts/verify-phase5-softphone.cjs connects to.
 *
 * Inside the container, two loopback forwarders make the app run on a secure origin
 * (http://localhost counts as secure: getUserMedia, Web Locks):
 *   localhost:FE_PORT  -> host.docker.internal:FE_PORT   (the e2e Vite dev server on the host)
 *   localhost:API_PORT -> API_HOST:API_PORT              (the e2e API container)
 * The PBX is reached by its service name (wss://pbx-webrtc-test:8089/ws), and media flows
 * between this container and Asterisk over the compose network (ICE host candidates).
 *
 * Chromium flags: fake microphone (a beep) and an auto-accepted permission prompt; the test
 * PBX's self-signed certificate is accepted (test only); local IPs are not hidden behind mDNS
 * names (Asterisk cannot resolve them).
 */
const net = require('net');
const { chromium } = require('/opt/pw/playwright-core');

const FE_PORT = Number(process.env.FE_PORT || 5178);
const API_PORT = Number(process.env.API_PORT || 9796);
const API_HOST = process.env.API_HOST || 'softphone-e2e-api';

function forward(localPort, host, port) {
  const server = net.createServer((client) => {
    const upstream = net.connect(port, host);
    client.pipe(upstream).pipe(client);
    const close = () => {
      client.destroy();
      upstream.destroy();
    };
    upstream.on('error', close);
    client.on('error', close);
  });
  server.listen(localPort, '127.0.0.1');
  server.on('error', (e) => console.error(`forward ${localPort}: ${e.message}`));
}

(async () => {
  forward(FE_PORT, 'host.docker.internal', FE_PORT);
  forward(API_PORT, API_HOST, API_PORT);
  const server = await chromium.launchServer({
    host: '0.0.0.0',
    port: 3000,
    wsPath: '/softphone',
    headless: true,
    args: [
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
      '--ignore-certificate-errors',
      '--disable-features=WebRtcHideLocalIpsWithMdns',
      '--autoplay-policy=no-user-gesture-required',
      '--disable-dev-shm-usage',
    ],
  });
  console.log(`softphone-e2e-browser: Playwright server ${server.wsEndpoint()} (forwarding :${FE_PORT} and :${API_PORT})`);
  const stop = async () => {
    await server.close().catch(() => undefined);
    process.exit(0);
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
