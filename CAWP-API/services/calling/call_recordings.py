"""The PBX records of a list of call attempts, in ONE CDR query (customer request 2026-10-19:
listen to a call's recording from the call list, and the line / recorded columns of its download).

Since the PBX CDR fix of 2026-10-17 a click-to-call (or campaign) call writes CDR rows tagged with
its attempt (userfield `cawp:<attempt>`, uniqueid `cawp-<attempt>`, or its 32-character
truncation on an Issabel varchar(32) column) and its recording `YYYY/MM/DD/cawp-<attempt>.wav`.
The rows are read inside a calldate window around the attempts (1 h before the first one was
created, 3 h after the last one ended), never by scanning the table.

`lookup(rows)` -> (status, {attempt_id: {"file", "line", "records"}}):
* status `available`, `unavailable` (the CDR could not be read: UNKNOWN, never "not recorded")
  or `disabled` (CDR_ENABLED=0);
* file: the first non-empty recordingfile of the attempt's rows (played/downloaded through the
  legacy GET /cdr/recording, which authorizes it again; services/calling_cdr.scoped_recording);
* line: the caller ID used (src of the Dial row);
* attempts with no tagged row are absent (manual calls are never looked up).
A truncated uniqueid links only when it names exactly one attempt of the list.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger("cawp.calling.recordings")

WINDOW_BEFORE = _dt.timedelta(hours=1)
WINDOW_AFTER = _dt.timedelta(hours=3)
CHUNK = 400
MAX_ROWS = 20_000

_SQL = """
    SELECT calldate, src, dst, channel, dstchannel, disposition, uniqueid, userfield, recordingfile
      FROM asteriskcdrdb.cdr
     WHERE calldate >= %s AND calldate < %s AND (uniqueid IN ({u}) OR userfield IN ({f}))
     ORDER BY calldate, uniqueid
     LIMIT %s
"""


def _window(rows, zone):
    starts = [r["created_at"] for r in rows if r.get("created_at")]
    ends = [r.get("ended_at") or r.get("dispatched_at") or r.get("started_at") or r["created_at"] for r in rows
            if r.get("created_at")]
    lo = (min(starts) - WINDOW_BEFORE).astimezone(zone).replace(tzinfo=None)
    hi = (max(ends + starts) + WINDOW_AFTER).astimezone(zone).replace(tzinfo=None)
    return lo, hi


def lookup(rows) -> tuple[str, dict]:
    from core.config import Config
    from services import reports_v2_cdr as cdrsrc
    from services.calling_cdr import attempt_tag
    rows = [r for r in rows if r.get("mode") != "manual" and r.get("created_at")]
    if not Config.CDR_ENABLED:
        return "disabled", {}
    if not rows:
        return "available", {}
    try:
        conn = cdrsrc._connect()
    except cdrsrc.CdrUnavailable:
        return "unavailable", {}
    zone = cdrsrc.pbx_zone()
    out: dict[str, dict] = {}
    try:
        cur = conn.cursor(dictionary=True)
        try:
            rows = sorted(rows, key=lambda r: r["created_at"])
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                ids = [str(r["id"]) for r in chunk]
                full = [f"cawp-{a}" for a in ids]
                trunc: dict[str, list[str]] = {}
                for a in ids:
                    trunc.setdefault(f"cawp-{a}"[:32], []).append(a)
                uniq = sorted(set(full) | set(trunc))
                tags = [f"cawp:{a}" for a in ids]
                lo, hi = _window(chunk, zone)
                cur.execute(_SQL.format(u=", ".join(["%s"] * len(uniq)), f=", ".join(["%s"] * len(tags))),
                            (lo, hi, *uniq, *tags, MAX_ROWS))
                wanted = set(ids)
                for r in cur.fetchall():
                    t = attempt_tag(r.get("uniqueid"), r.get("userfield"))
                    if t is None:
                        continue
                    if t["exact"]:
                        aid = t["value"] if t["value"] in wanted else None
                    else:
                        hits = trunc.get((r.get("uniqueid") or "").strip().lower()) or []
                        aid = hits[0] if len(hits) == 1 else None
                    if aid is None:
                        continue
                    e = out.setdefault(aid, {"file": None, "line": None, "records": 0})
                    e["records"] += 1
                    e["file"] = e["file"] or (r.get("recordingfile") or "").strip() or None
                    src = (r.get("src") or "").strip() or None
                    if (r.get("dstchannel") or "").strip() and src:
                        e["line"] = e["line"] or src
        finally:
            cur.close()
    except Exception as e:  # noqa: BLE001 - any driver error: unknown, not "no recording"
        log.warning("call recordings CDR lookup failed: %s", type(e).__name__)
        return "unavailable", {}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return "available", out
