"""Phase 6 Q0: the AI foundation (docs/phase6-spec.md, review/phase6-foundation.md).

Modules (import the one you need; this package imports nothing at load time):

    config          server-side configuration from the environment (read at call time)
    transport       the HTTP transport (urllib); tests inject a fake one
    provider        the provider interface, result types and ProviderError
    openai_provider the OpenAI implementation (Responses structured outputs, transcription,
                    Realtime client secrets)
    schema          strict JSON-schema checks for structured outputs
    masking         per-request placeholders for customer identifiers ([PHONE_1], ...)
    policy          the revisioned AI data policy (ai_data_policies, migration 0059)
    calls           the provider-call audit rows (ai_provider_calls, migration 0060)
    gateway         THE entry point for Q1-Q3: readiness, policy, masking, audit, provider call
    retention       retention task + hook registry (drafts, transcripts, audio, provider logs)
    permissions     permission keys and decorators for the AI/quality/voice-lab routes
    case_identifiers  the identifiers of a case to mask (name, identity, account, contacts)

Rules: generated text is a draft; not configured means not configured (no fabricated or
simulated output in production code); keys live in the server environment only and are never
logged, returned or stored.
"""
