# Voice integration status

The app now includes a durable progressive dialer, personal calling workspace and supervisor team campaign page. Human operators have independent readiness leases, one active reservation each, real call-state tracking, required wrap-up and automatic next-case progression. Demo simulation is isolated from real telephone numbers and providers.

## Issabel and OpenAI

The original Issabel click-to-call path remains available. The progressive dialer uses its own retained AMI connection and correlated customer events, with explicit dialplan configuration. A queue acknowledgment or a collector answering their extension does not prove the customer answered. Interrupted event tracking leaves a call uncertain and requires review.

The AI route connects Issabel to OpenAI Realtime SIP. A signed incoming-call webhook must match a backend-created pending attempt before it is accepted. A server-side WebSocket handles the initial greeting, conversation transcript and requests for human help. The AI identifies itself, uses a neutral callback conversation and receives no debt amounts, names or account details. Payment promises and callbacks are saved through the collector's reviewed wrap-up. This is not an authenticated autonomous debt negotiation agent.

Use [Issabel dialer setup](issabel-dialer.md) for the exact environment flags, dialplan and webhook configuration. The public webhook path is `/api/webhooks/openai`; application cookie authentication is replaced there by provider signature verification. Demo mode rejects live webhooks.

## Deployment validation still needed

- Confirm the installed Issabel/Asterisk version, channel driver, outbound route, AMI privileges, TLS and codecs against the supplied configuration.
- Exercise answer, busy, no answer, transfer, hangup, disconnect and restart behavior with isolated test extensions.
- Configure business calling windows and contact-attempt policy for the operating jurisdiction. The application uses a one-pass campaign pool and checks do-not-call, future callbacks, closed/paid cases and existing call reservations before dispatch.
- Add a real identity verification mechanism before allowing AI to access debt facts. Current AI conversations deliberately do not receive debt details.
- Validate webhook delivery, provider billing, transcript retention and access policies. Binary recording ingestion and automatic human-call transcription remain separate work.

Official implementation references:

- [Asterisk AMI Originate](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Originate/)
- [Asterisk AMI DialEnd](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/DialEnd/)
- [OpenAI Realtime SIP](https://developers.openai.com/api/docs/guides/realtime-sip)

Tests use simulated transport and mock provider responses. Real OpenAI telephone calls and Issabel routing have not been exercised with customer credentials or destinations.
