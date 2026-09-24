"""Extension-first Originate action (pure; P1's dispatcher sends it on the AMI session).

The agent's own extension is rung first; only when it answers does the agent channel run the
restricted context, whose FastAGI hook must consume the one-shot token before the customer is
dialled. The action carries no application, no Local channel and no caller-supplied options:

    Channel   PJSIP/<extension> (or SIP/<extension>)
    Context   the restricted context (collections-human-*)
    Exten     the customer's E.164 number, Priority 1
    ChannelId cawp-<attempt id>   (= the agent channel's Uniqueid; evidence correlation key)
    Async     true; Timeout = agent ring timeout (ms)
    Variable  __COLLECTIONS_ATTEMPT=<attempt id>  (inherited by the customer channel)
    Variable  COLLECTIONS_TOKEN=<one-shot token>  (agent channel only; cleared after the AGI)

A `Response: Success` to it means *queued* - never ringing, answered or connected.
"""
from __future__ import annotations

import re

from services.calling.dialplan import CONTEXT_RE

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def extension_first_fields(*, attempt_id: str, extension: str, destination: str, token: str, context: str,
                           driver: str = "PJSIP", agent_ring_seconds: int = 30) -> list[tuple[str, str]]:
    if not UUID_RE.match(attempt_id or ""):
        raise ValueError("attempt_id must be a lowercase UUID")
    if not re.fullmatch(r"[0-9]{2,6}", extension or ""):
        raise ValueError("extension must be 2-6 digits")
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", destination or ""):
        raise ValueError("destination must be E.164")
    if not re.fullmatch(r"[0-9a-f]{64}", token or ""):
        raise ValueError("token must be 64 hex characters")
    if not CONTEXT_RE.match(context or ""):
        raise ValueError("context must be a collections-human-* context")
    if driver not in ("PJSIP", "SIP"):
        raise ValueError("driver must be PJSIP or SIP")
    if not 5 <= int(agent_ring_seconds) <= 60:
        raise ValueError("agent_ring_seconds must be 5..60")
    return [
        ("Channel", f"{driver}/{extension}"),
        ("Context", context),
        ("Exten", destination),
        ("Priority", "1"),
        ("Async", "true"),
        ("Timeout", str(int(agent_ring_seconds) * 1000)),
        ("ChannelId", f"cawp-{attempt_id}"),
        ("Variable", f"__COLLECTIONS_ATTEMPT={attempt_id}"),
        ("Variable", f"COLLECTIONS_TOKEN={token}"),
    ]
