# Issabel progressive dialer and OpenAI SIP

The application reserves one collector before originating each customer call. It never originates speculative extra calls. A collector stays reserved until the PBX reports a terminal customer event and a human completes wrap-up. Agent desktop availability must be kept in sync with actual extension availability. A paused, disconnected, or wrapping-up collector must not receive a new reservation.

This adapter requires a compatible Asterisk/PJSIP installation, the contexts below, an outbound trunk, and a reachable collector extension. `ISSABEL_DIALPLAN_READY=true` is an operator assertion after testing this contract; it is not automatic PBX discovery. Do not enable it for an unmodified Issabel installation. The adapter does not configure your PBX, trunk, firewall, codecs, TLS certificates, or OpenAI project.

## Environment

```dotenv
ISSABEL_ENABLED=true
ISSABEL_DIALER_ENABLED=true
ISSABEL_DIALPLAN_READY=false
ISSABEL_HOST=pbx.example.internal
ISSABEL_PORT=5039
ISSABEL_TLS=true
ISSABEL_USERNAME=collections_dialer
ISSABEL_SECRET=replace-in-server-secret-store
ISSABEL_DIALER_OUTBOUND_CONTEXT=collections-dialer-outbound
ISSABEL_DIALER_HUMAN_CONTEXT=collections-dialer-human
ISSABEL_DIALER_AI_CONTEXT=collections-dialer-ai
ISSABEL_DIALER_MAX_CALL_SECONDS=300

# Only enable after the SIP trunk and signed webhook are tested.
ISSABEL_AI_ENABLED=false
OPENAI_API_KEY=
OPENAI_WEBHOOK_SECRET=
OPENAI_PROJECT_ID=
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
```

Use a dedicated AMI account limited to the application host over TLS. It needs originate and call write access and call/user/system read access for lifecycle events and heartbeat. Do not grant command/configuration access. On a private legacy AMI connection, explicitly setting `ISSABEL_TLS=false` selects port 5038; prefer TLS or a protected tunnel. The application verifies the PBX TLS certificate; use a hostname matching its SAN and a trusted CA.

## Dialplan contract

Place custom contexts in the PBX's supported custom dialplan file (usually `extensions_custom.conf`). Replace `collections-outbound` with your configured **PJSIP outbound trunk endpoint**. These contexts dial that trunk directly, so configure required caller ID, authorized destinations, dial prefixes, maximum call duration, and trunk capacity there. The sample is deliberately limited to E.164-style numeric dialing; it does not expose a general dialplan expression from a browser.

```asterisk
[collections-dialer-outbound]
exten => _X.,1,Gosub(collections-customer-start,s,1(${EXTEN}))
 same => n,Hangup()
exten => _+X.,1,Gosub(collections-customer-start,s,1(${EXTEN}))
 same => n,Hangup()

[collections-customer-start]
exten => s,1,Set(CHANNEL(hangup_handler_push)=collections-customer-finished,s,1)
 same => n,Set(TIMEOUT(absolute)=${COLLECTIONS_MAX_SECONDS})
 same => n,Dial(PJSIP/${ARG1}@collections-outbound,30,gb(collections-customer-created^s^1)U(collections-customer-answered))
 same => n,Return()

; Runs on the actual outbound CUSTOMER channel before dialing.
[collections-customer-created]
exten => s,1,UserEvent(CollectionsCall,AttemptId:${COLLECTIONS_ATTEMPT},State:customer_created)
 same => n,Return()

; Runs on the actual outbound CUSTOMER channel only after answer.
[collections-customer-answered]
exten => s,1,UserEvent(CollectionsCall,AttemptId:${COLLECTIONS_ATTEMPT},State:answered)
 same => n,Return()

; Runs on the Local customer leg when it terminates, including failed attempts.
[collections-customer-finished]
exten => s,1,UserEvent(CollectionsCall,AttemptId:${COLLECTIONS_ATTEMPT},State:ended,DialStatus:${DIALSTATUS})
 same => n,Return()

; AMI starts this destination only when the CUSTOMER has answered.
[collections-dialer-human]
exten => _X.,1,Dial(PJSIP/${EXTEN},20)
 same => n,Hangup()

[collections-dialer-ai]
exten => _X.,1,Dial(PJSIP/openai/sip:${COLLECTIONS_PROJECT}@sip.api.openai.com\;transport=tls,20,b(collections-openai-header^s^1))
 same => n,Hangup()

[collections-openai-header]
exten => s,1,Set(PJSIP_HEADER(add,X-Collections-Attempt)=${COLLECTIONS_ATTEMPT})
 same => n,Return()
```

The AMI Originate creates a `Local/<phone>@collections-dialer-outbound/n` channel pair with independent random `ChannelId` and `OtherChannelId`. An inherited random `COLLECTIONS_ATTEMPT` token binds custom events and the OpenAI SIP header to a single in-memory attempt. Never expose that token to browser clients or logs. The `/n` retains the Local channel so hangup remains correlated. Custom events must preserve Asterisk's `Uniqueid` and `Channel` fields.

`customer_created` lets the adapter observe actual customer `Newstate: Ringing` events. `answered` comes from the customer's `U()` answer handler. Collector extension answers and Originate acceptance do not mark the customer answered. The customer Hangup or custom final event closes the attempt. AMI hangup acknowledgements do not free the collector until a terminal event arrives. If an AMI connection or acknowledgement is lost after submission, the outcome is **uncertain**, and the operator must check the PBX before proceeding. The adapter never retries an uncertain origination.

Customer-first dialing can leave a customer briefly waiting for the reserved extension to answer. Keep those endpoints ready and use suitable PBX announcements according to your operating policy; this sample does not implement answering machine detection or call-progress audio analysis. An unavailable extension is not an available collector. Asterisk/PBX restart or application restart requires reconciliation of in-flight calls; do not blindly resume the campaign.

Each attempt has a default 300-second maximum, configurable from 30 to 3,600 seconds. The PBX absolute timeout remains effective even when the application loses its AMI connection. The server also requests hangup at the limit while connected, but the timer never releases a collector without a PBX terminal event.

## OpenAI Realtime SIP

Create a PJSIP endpoint named `openai` using a TLS transport and a compatible media configuration. A starting point for a PBX administrator is:

```asterisk
; pjsip_custom.conf or the PBX-supported custom equivalent:
[collections-openai-tls]
type=transport
protocol=tls
bind=0.0.0.0:5061
method=tlsv1_2
verify_server=yes
ca_list_file=/etc/ssl/certs/ca-certificates.crt

[openai]
type=endpoint
transport=collections-openai-tls
context=collections-openai-inbound-denied
disallow=all
allow=ulaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

; extensions_custom.conf:
[collections-openai-inbound-denied]
exten => _.,1,Hangup()
```

The CA file location and media/NAT settings differ by Issabel distribution. Resolve port conflicts with an existing TLS transport instead of binding a second listener to the same port. Validate TLS, negotiated codecs, RTP, NAT, outbound caller ID, and the endpoint on your PBX version. The direct dial string above uses the OpenAI project URI; the SIP project must match `OPENAI_PROJECT_ID` on the application server. For a European residency project, use the appropriate OpenAI regional SIP hostname in the PBX context. These samples were reviewed against official APIs and tested with mocks, not on a live Issabel PBX.

Create a project webhook for `realtime.call.incoming`, pointing to your public HTTPS application endpoint `/api/webhooks/openai`. Set the webhook signing secret on the server. The route must verify the original raw request text before JSON processing. The bridge validates the Standard Webhooks HMAC and five-minute timestamp window, deduplicates webhook IDs, and accepts only a fresh, pending attempt with exactly one matching unpredictable `X-Collections-Attempt` header. Unmatched incoming calls are rejected. The endpoint is disabled in demo mode.

The server accepts the SIP session through OpenAI and opens an authenticated `ws` sideband connection. Customer/assistant transcripts and requests for human assistance, contact cessation, or a callback become review information. The voice agent clearly identifies itself as AI and acts as a **neutral callback assistant**: it receives no debtor name, balance, creditor, case notes, or identity evidence. It cannot verify identity, discuss or negotiate debt, create promises, confirm appointments, collect payment credentials, or change financial agreements. A stop-contact request immediately sets the case do-not-call flag and clears scheduled contact. There is no automatic human transfer in this version; requests are surfaced for the reserved collector. A human must verify identity through the organization's established process and review all case updates. AI sideband loss is not evidence that the telephone call ended; the PBX remains authoritative.

When the AI records a stop-contact request, the application holds future contact and the voice bridge asks for a short goodbye, then requests SIP hangup after playback finishes or after six seconds. It still waits for PBX confirmation before releasing the attempt.

## Commissioning checks

Use authorized test telephone numbers and dedicated test extensions. Before setting `ISSABEL_DIALPLAN_READY=true`, verify answer/busy/no-answer/customer-hangup/collector-hangup, wrap-up locking, pause/resume, parallel collector reservations, and AMI interruption. Check that arbitrary or stale AMI events cannot change another call. For AI, additionally verify signed webhook rejection, duplicate handling, wrong token rejection, bilingual AI disclosure, two-way audio, transcript delivery, callback/stop requests, and SIP teardown. Run `node --test tests/dialer-telephony.test.js` for isolated transport verification.

References: [Asterisk Originate](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Originate/), [UserEvent](https://docs.asterisk.org/Latest_API/API_Documentation/Dialplan_Applications/UserEvent/), [pre-bridge handlers](https://docs.asterisk.org/Configuration/Dialplan/Subroutines/Pre-Bridge-Handlers/), [OpenAI Realtime SIP](https://developers.openai.com/api/docs/guides/realtime-sip), [OpenAI server controls](https://developers.openai.com/api/docs/guides/realtime-server-controls), [OpenAI webhook verification](https://developers.openai.com/api/docs/guides/webhooks).


The separate manual submission helper `src/integrations.js:originate` now shares the bounded authenticated AMI session used by the human-calling rebuild. It still returns submission acknowledgement only and closes afterward; it does not use the customer-first campaign lifecycle above. For that manual helper, `ISSABEL_TLS=false` is accepted only with literal `127.0.0.1` or `::1` tunnel endpoints. Remote connections require verified TLS. Lifecycle events cannot substitute for the correlated action acknowledgement.

#### Native bridge destruction and merge journal

The authenticated human-call consumer retains typed BridgeDestroy/BridgeMerge facts in a shared profile/PBX-process-epoch journal. Names, raw frames and caller data are excluded. Events are applied only after a call is associated with an involved bridge; merge association also discovers the destination bridge. Replay uses immutable identifiers and the ordinary audited evidence transaction. A pending applicable event prevents wrap-up and resource reuse if fan-out fails.

A merge announcement does not itself move members in the application projection. Asterisk documents BridgeMerge before the corresponding BridgeLeave and BridgeEnter events; those explicit moves must establish the resulting membership. See the [AMI v2 bridge specification](https://docs.asterisk.org/Configuration/Interfaces/Asterisk-Manager-Interface-AMI/AMI-v2-Specification/), [BridgeDestroy fields](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/AMI_Events/BridgeDestroy/) and [BridgeMerge fields](https://docs.asterisk.org/Asterisk_24_Documentation/API_Documentation/AMI_Events/BridgeMerge/). These contracts do not verify the installed Issabel version or live behavior.

The journal currently has a fail-closed capacity of 32,768 events per profile/epoch. Maintenance/retention and operational recovery are still required before production readiness; never delete journal history manually to bypass a call safety hold.

The internal call-attempt service exposes `recoverBridgeEvidence({after, limit})` for a dedicated calling worker. It scans pending journal associations in attempt-ID order (default 25, maximum 50 attempts), tries the ordinary resource/attempt locks, and commits each attempt independently. Each transaction applies at most 256 lifecycle records. Results identify `busy`, `pending`, `replayed`, `retry_required` or `reconciliation_required` without raw database errors. Continue with `next_after` while `has_more` is true; then begin another sweep from null so busy, failed or partially processed attempts are revisited. Persisting or scheduling this cursor is the worker's responsibility. The service sends no PBX command, renews no lease, and does not clear sticky safety holds or create a topology-complete manifest. It is internal only; no browser endpoint exposes it. Executable worker scheduling and operational recovery monitoring remain unfinished.
