"""The attempt tag on the extension-first agent leg (Phase 5 P3), pure.

The softphone auto-answers ONLY a call that carries this SIP header AND whose value is the
caller's own active attempt (GET /v2/me/calls/active). Every other call rings normally.

    X-CAWP-Attempt: <attempt uuid>

It is added to the INVITE that the PBX sends to the agent's extension by the Originate itself,
before the channel is called (the variables of an Originate are set on the new channel between
the request and the call, so the PJSIP header datastore is in place when the INVITE is built):

    PJSIP      Variable: PJSIP_HEADER(add,X-CAWP-Attempt)=<attempt id>
    chan_sip   Variable: SIPADDHEADER51=X-CAWP-Attempt: <attempt id>

Nothing else sets it: agent-to-agent calls in `cawp-agents` do not copy inbound headers, and a
forged header still needs the victim's current attempt id to be auto-answered.

Integration (P1/P2 own the Originate): append `attempt_tag_fields(attempt_id, driver)` to the
fields from `services.calling.originate.extension_first_fields(...)`. Proven live against the
WebRTC test PBX by CAWP-frontend/scripts/verify-phase5-softphone.cjs (review/phase5-softphone.md).
"""
from __future__ import annotations

import re

ATTEMPT_HEADER = "X-CAWP-Attempt"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def attempt_tag_fields(attempt_id: str, driver: str = "PJSIP") -> list[tuple[str, str]]:
    """The Originate `Variable` field(s) that put the attempt tag on the agent leg's INVITE."""
    if not isinstance(attempt_id, str) or not _UUID_RE.match(attempt_id):
        raise ValueError("attempt_id must be a lowercase UUID")
    if driver == "PJSIP":
        return [("Variable", f"PJSIP_HEADER(add,{ATTEMPT_HEADER})={attempt_id}")]
    if driver == "SIP":
        return [("Variable", f"SIPADDHEADER51={ATTEMPT_HEADER}: {attempt_id}")]
    raise ValueError("driver must be PJSIP or SIP")


def parse_attempt_header(value) -> str | None:
    """The attempt id of a received header value, or None (never guessed)."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _UUID_RE.match(value) else None
