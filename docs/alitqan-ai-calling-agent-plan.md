# Alitqan: AI calling agent test page — plan for approval

Status (2026-11-04): **approved, built and live-tested** (migration 0092, `ai_voice/`, `services/ai_voice/`,
`services/ai/test_calls.py`, `services/calling/ai_dialplan.py`; PBX extension 498 "AI test agent",
`/etc/asterisk/collections-ai.conf` from `docs/reference/alitqan-ai-dialplan-policy.json`). First live call to the
test mobile: answered, 29 s, both sides transcribed, ended `completed`. Customer request: "add this OpenAI key and add a testing AI calling agent
where I can add the number". The key is stored on the server only (`OPENAI_API_KEY` in
`/etc/alitqan/api.env`, root:cawp 0640, never in git, never logged or returned) and was verified against
OpenAI (HTTP 200, the account has `gpt-realtime` models).

## 1. What the user gets

A page "تجربة وكيل الاتصال الذكي" (AI section, permission `ai_admin`):

* enter a Saudi mobile number, pick the outbound number (the customer's list, 0090) and a voice;
* the agent's script in a text box, pre-filled with an Arabic collections script that opens by saying it
  is an automated assistant calling for Alitqan; edit it per test;
* Start: the PBX calls the number; when it is answered the AI talks with the person live; Stop at any time;
* after the call: status (no answer / busy / answered / ended), duration, and the transcript of both sides.

It is a test tool: no case is linked, nothing is written to a client, no promise or note. The transcript is
a record of the test only (the project rule "AI output is a draft a human saves" holds: it saves nothing).

## 2. How a call works

The network allows the app server to open UDP to the PBX and get replies on that flow; the PBX cannot open
connections to the app server (measured 2026-11-04). So the app starts the call:

1. The app inserts the test call (one-shot token, SHA-256 stored) and the **AI voice service** (new,
   `alitqan-ai-voice`, on the app server) sends a SIP INVITE to the PBX as a dedicated extension, with the
   token in a SIP header.
2. PBX dialplan (generated, like the message campaigns): the FastAGI hook `collections-ai-authorize`
   checks the token (bound to this call and number, one use), then the same trunk and caller-ID rules as
   message campaigns, ONE Dial of the number, 30-60 s ring, 3-minute limit.
3. The client answers → the PBX answers our leg → audio flows as G.711 µ-law (RTP, the app sends first
   so the reply path is open). The service streams it to OpenAI Realtime over a server-side WebSocket
   (`audio/pcmu` in and out, no transcoding) and plays the AI's voice back.
4. Either side hangs up, Stop is pressed, or 3 minutes pass → BYE; the transcript and the result are saved.

## 3. PBX changes (reversible, no restart)

| # | Change | Reload |
|---|---|---|
| 1 | Backup of `/etc/asterisk` | none |
| 2 | One SIP extension for the agent, created through the existing provisioning connection (`nat=force_rport,comedia`, `directmedia=no`) | the provisioning script's normal SIP reload |
| 3 | Generated `/etc/asterisk/collections-ai.conf` + one `#include`, reachable only from that extension with a valid token | **`dialplan reload`** |

## 4. App changes

* Migration: `ai_test_calls` (number, outbound number, voice, script, state, result, times, transcript,
  created_by; token hash only).
* The AI voice service: a small SIP user agent (INVITE with digest auth, ACK, BYE, CANCEL), RTP, and the
  OpenAI Realtime bridge; Python asyncio, one new dependency (`websockets`). systemd unit + compose entry.
* API `POST/GET /v2/ai/test-calls`, `POST .../<id>/stop`; the page; tests with a fake PBX and a fake
  Realtime server; a live test to your mobile.
* Limits: one test call at a time per user, 3 minutes each; every call audited. The daily limit per user
  (`AI_TEST_CALLS_PER_DAY`) is off since 2026-11-04 (customer request); the AI gateway's daily quota still applies. Cost: OpenAI
  Realtime is billed per minute of audio; the 3-minute cap bounds each test.

## 5. Risks

* Speaking to people with an AI voice: the script must say it is automated; use it on your own numbers
  while testing.
* Audio quality depends on the network between the app server and the PBX (measured working, not yet
  under load).
* The key was sent in a chat message: rotate it in the OpenAI dashboard when convenient and send the new
  one the same way it was stored.
