"""Live channel snapshot for the call monitor (migration 0067).

A thread of the calling service: every CALLING_LIVE_SNAPSHOT_SECONDS (default 3, 0 = off) it
lists the PBX's channels over the existing AMI session (CoreShowChannels, a `reporting`
action) and replaces `pbx_live_channels` with them in one transaction, then stamps
`pbx_live_snapshot`. Without a connected session it records ok=false and no channels, so the
API can say "unknown" instead of "no calls". It never writes call evidence and never acts on
a channel.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from sqlalchemy import text

log = logging.getLogger("cawp.calling.live")

AGENT_RE = re.compile(r"^PJSIP/([0-9]{2,6})-")
FIELDS = ("uniqueid", "linkedid", "channel", "extension", "state", "caller_num", "caller_name", "connected_num",
          "connected_name", "context", "exten", "application", "bridge_id", "duration_s")


def _clip(value, n=200):
    if value in (None, "", "<unknown>"):
        return None
    return str(value)[:n]


def _seconds(value) -> int | None:
    """CoreShowChannel Duration is HH:MM:SS."""
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", (value or "").strip())
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def parse(frames) -> list[dict]:
    """CoreShowChannel frames (or plain dicts) -> pbx_live_channels rows."""
    rows, seen = [], set()
    for f in frames or ():
        get = f.get
        uid = _clip(get("Uniqueid"), 150)
        channel = _clip(get("Channel"))
        if not uid or not channel or uid in seen:
            continue
        seen.add(uid)
        m = AGENT_RE.match(channel)
        rows.append({
            "uniqueid": uid, "linkedid": _clip(get("Linkedid"), 150) or uid, "channel": channel,
            "extension": m.group(1) if m else None, "state": _clip(get("ChannelStateDesc"), 40),
            "caller_num": _clip(get("CallerIDNum"), 40), "caller_name": _clip(get("CallerIDName"), 80),
            "connected_num": _clip(get("ConnectedLineNum"), 40), "connected_name": _clip(get("ConnectedLineName"), 80),
            "context": _clip(get("Context"), 80), "exten": _clip(get("Exten"), 80),
            "application": _clip(get("Application"), 40), "bridge_id": _clip(get("BridgeId"), 80),
            "duration_s": _seconds(get("Duration"))})
    return rows


def write(session, rows: list[dict] | None, error: str | None = None):
    """Replace the snapshot (rows None = the PBX could not be listed)."""
    session.execute(text("DELETE FROM pbx_live_channels"))
    if rows:
        session.execute(text(f"""
            INSERT INTO pbx_live_channels ({', '.join(FIELDS)}, captured_at)
            VALUES ({', '.join(':' + k for k in FIELDS)}, now())"""), rows)
    session.execute(text("""
        INSERT INTO pbx_live_snapshot (id, captured_at, ok, error, channel_count)
        VALUES (1, now(), :ok, :err, :n)
        ON CONFLICT (id) DO UPDATE SET captured_at = now(), ok = EXCLUDED.ok, error = EXCLUDED.error,
                                       channel_count = EXCLUDED.channel_count"""),
        {"ok": rows is not None, "err": error, "n": len(rows or [])})


class LiveSnapshot(threading.Thread):
    def __init__(self, app, current_ctx, *, interval: float = 3.0):
        super().__init__(name="live-snapshot", daemon=True)
        self.app, self.current_ctx, self.interval = app, current_ctx, interval
        self.stopping = threading.Event()
        self.stats = {"passes": 0, "errors": 0}

    def stop(self, timeout=5.0):
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def run_once(self):
        from extensions import db
        from services.calling.ami import AmiError
        ctx = self.current_ctx()
        rows, error = None, None
        if ctx is None or not ctx.session.ready:
            error = "ami_disconnected"
        else:
            try:
                rows = parse(ctx.session.list_channels())
            except AmiError as e:
                error = e.code
        with self.app.app_context():
            try:
                write(db.session, rows, error)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            finally:
                db.session.remove()
        self.stats["passes"] += 1

    def run(self):
        last_log = None
        while not self.stopping.wait(self.interval):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the database may be down: next pass, log once a minute
                self.stats["errors"] += 1
                if last_log is None or time.monotonic() - last_log > 60:
                    log.exception("live channel snapshot failed (retrying)")
                    last_log = time.monotonic()
