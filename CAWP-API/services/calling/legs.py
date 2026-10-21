"""Which PBX channel is an attempt's AGENT leg (both event models).

Asterisk 12+ (CALLING_PBX_EVENTS=modern): the Originate's ChannelId, `cawp-<attempt>`, IS the agent
channel's Uniqueid. Asterisk <= 11 (legacy) ignores ChannelId; the agent channel has an ordinary
"<epoch>.<seq>" Uniqueid, bound to the attempt by its OriginateResponse (ActionID) or by the FastAGI
hook and recorded as the attempt's `call_legs` row with role `agent`
(services/calling/legacy_correlation.py). These helpers read that row and fall back to
`cawp-<attempt>`, so a modern attempt resolves exactly as before. `call_attempts.pbx_channel_id`
stays `cawp-<attempt>` either way: it is the app's own id of the dispatch, not a PBX fact.
"""
from __future__ import annotations

import re

from sqlalchemy import text

APP_CHANNEL_RE = re.compile(r"^cawp-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def app_channel(attempt_id) -> str:
    return f"cawp-{attempt_id}"


def agent_uids(session, attempt_id, epoch=None) -> list[str]:
    """The attempt's bound agent legs on its PBX epoch (normally none or one)."""
    rows = session.execute(text("""
        SELECT l.uniqueid FROM call_legs l
         WHERE l.attempt_id = CAST(:a AS uuid) AND l.role = 'agent'
           AND l.pbx_epoch = coalesce(CAST(:ep AS text),
                                      (SELECT a.pbx_epoch FROM call_attempts a WHERE a.id = CAST(:a AS uuid)))
         ORDER BY l.first_event_id NULLS LAST, l.uniqueid"""), {"a": str(attempt_id), "ep": epoch}).all()
    return [r[0] for r in rows]


def agent_uid(session, attempt_id, epoch=None) -> str:
    """The agent leg's Uniqueid: the bound leg, else the app-issued `cawp-<attempt>` channel."""
    uids = agent_uids(session, attempt_id, epoch)
    return uids[0] if uids else app_channel(attempt_id)


def attempt_of_agent(session, uniqueid) -> str | None:
    """The attempt whose agent leg is `uniqueid` (`cawp-<attempt>`, or a bound Asterisk 11 leg)."""
    m = APP_CHANNEL_RE.match(uniqueid or "")
    if m:
        return m.group(1)
    if not uniqueid:
        return None
    r = session.execute(text("""
        SELECT a.id::text FROM call_legs l JOIN call_attempts a ON a.id = l.attempt_id AND a.pbx_epoch = l.pbx_epoch
         WHERE l.uniqueid = :u AND l.role = 'agent' ORDER BY a.created_at DESC LIMIT 1"""), {"u": uniqueid}).first()
    return r[0] if r else None
