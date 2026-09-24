"""PBX CDR and recordings of one call attempt (Phase 5 P4, read only). review/phase5-evidence.md.

    GET /v2/calls/<attempt_id>/pbx-evidence      (api/calling_evidence.py)

Visibility is the call detail's (services/calling/queries: the attempt's case is in the
caller's case scope, or the caller made the call); anything else is 404 `call_not_found`.

Returned:
* `cdr`: the live Issabel CDR records tagged with this attempt (asteriskcdrdb.cdr, read in a
  window around the attempt: userfield cawp:<id>, uniqueid cawp-<id> or its 32-character
  truncation). `status` is `available`, `unavailable` (the CDR could not be read: UNKNOWN, not
  "no records") or `disabled` (CDR_ENABLED=0).
* `imported`: `pbx_cdr_imported` evidence linked to the attempt by a reviewed import.
* `recordings`: the distinct recording files of those records. The playable link
  (GET /cdr/recording, scoped and audited there) is given only with a recording key; without
  one only `has_recording` is shown.
Numbers (src, dst) are shown only with `clints_details`, like the call detail. Nothing here
changes the attempt: a CDR duration is evidence next to the attempt's own (unknown) duration.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from core.api_response import ApiError
from core.auth import has_permission
from services.case_history_common import iso, me, s, scope_sql
from services.calling_cdr import RECORDING_KEYS, attempt_tag, resolve_path

WINDOW_BEFORE = _dt.timedelta(hours=1)
WINDOW_AFTER = _dt.timedelta(hours=3)


def _sess():
    from extensions import db
    return db.session


def visible_attempt(attempt_id) -> dict:
    cond, params = scope_sql("c")
    r = _sess().execute(text(f"""
        SELECT a.id::text AS id, a.case_id::text AS case_id, a.collector_user_id::text AS collector_user_id,
               a.state, a.mode, a.created_at, a.started_at, a.dispatched_at, a.ended_at, a.duration_seconds,
               a.pbx_channel_id
          FROM call_attempts a LEFT JOIN clints c ON c.id = a.case_id
         WHERE a.id = CAST(:id AS uuid)
           AND ((c.id IS NOT NULL AND {cond}) OR a.collector_user_id = CAST(:me AS uuid))"""),
        {**params, "id": str(attempt_id), "me": me()}).mappings().first()
    if r is None:
        raise ApiError(404, "call_not_found", "call not found", {"attempt_id": str(attempt_id)})
    return dict(r)


_LIVE_SQL = """
    SELECT calldate, src, dst, dcontext, channel, dstchannel, duration, billsec, disposition, uniqueid,
           userfield, recordingfile
      FROM asteriskcdrdb.cdr
     WHERE calldate >= %s AND calldate < %s
       AND (uniqueid IN (%s, %s) OR userfield LIKE %s)
     ORDER BY calldate, uniqueid
     LIMIT 200
"""


def live_cdr(attempt: dict) -> tuple[str, list[dict]]:
    """(status, CDR rows in the reports_v2_cdr shape tagged with exactly this attempt)."""
    from core.config import Config
    from services import reports_v2_cdr as cdrsrc
    if not Config.CDR_ENABLED:
        return "disabled", []
    zone = cdrsrc.pbx_zone()
    start = attempt["created_at"]
    end = attempt["ended_at"] or attempt["dispatched_at"] or attempt["started_at"] or start
    lo = (min(start, attempt["started_at"] or start) - WINDOW_BEFORE).astimezone(zone).replace(tzinfo=None)
    hi = (max(end, start) + WINDOW_AFTER).astimezone(zone).replace(tzinfo=None)
    tag = f"cawp-{attempt['id']}"
    try:
        conn = cdrsrc._connect()
    except cdrsrc.CdrUnavailable:
        return "unavailable", []
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(_LIVE_SQL, (lo, hi, tag, tag[:32], f"%cawp:{attempt['id']}%"))
            raw = cur.fetchall()
        finally:
            cur.close()
    except Exception:  # noqa: BLE001
        return "unavailable", []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    out = []
    for r in raw:
        t = attempt_tag(r.get("uniqueid"), r.get("userfield"), r.get("linkedid"))
        if t is None or (t["exact"] and t["value"] != attempt["id"]) or \
                (not t["exact"] and not attempt["id"].replace("-", "").startswith(t["value"])):
            continue
        row = cdrsrc._cdr_row(r, zone)
        row["attempt_link"] = t["via"]
        row["channel"], row["dstchannel"] = r.get("channel") or None, r.get("dstchannel") or None
        out.append(row)
    return "available", out


def imported(attempt_id) -> list[dict]:
    if not _sess().execute(text("SELECT to_regclass('public.pbx_cdr_imported') IS NOT NULL")).scalar():
        return []
    return [dict(r) for r in _sess().execute(text("""
        SELECT id::text AS id, source_name, bulk_job_id::text AS bulk_job_id, row_no, file_name, file_sha256, sheet,
               uniqueid, call_at, call_at_source, src, dst, direction, disposition, duration_seconds, billsec,
               recordingfile, attribution, attempt_link, leg_role, peer_leg_role, imported_by::text AS imported_by,
               imported_at
          FROM pbx_cdr_imported WHERE attempt_id = CAST(:a AS uuid) ORDER BY call_at, id"""),
        {"a": str(attempt_id)}).mappings()]


def attempt_evidence(attempt_id) -> dict:
    from services import reports_v2_cdr as cdrsrc
    attempt = visible_attempt(attempt_id)
    show_numbers = has_permission("clints_details")
    can_hear = any(has_permission(k) for k in RECORDING_KEYS)
    status, rows = live_cdr(attempt)

    def num(v):
        return v if show_numbers else None

    recs: dict[str, dict] = {}

    def recording(path, source):
        if not path:
            return None
        full = resolve_path(path)
        key = full or path
        entry = recs.setdefault(key, {"has_recording": True, "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if can_hear:
            entry["file"], entry["url"] = path, cdrsrc.recording_url(path)
        return {"has_recording": True, "file": path if can_hear else None,
                "url": cdrsrc.recording_url(path) if can_hear else None}

    cdr = [{"call_at": iso(r["call_at"]), "src": num(r["src"]), "dst": num(r["dst"]), "direction": r["direction"],
            "agent_extension": r["agent_ext"], "channel": r["channel"], "dstchannel": r["dstchannel"],
            "disposition": r["disposition"], "pbx_status": r["pbx_status"],
            "duration_seconds": r["duration"], "billsec": r["billsec"], "duration_known": r["duration"] is not None,
            "uniqueid": r["uniqueid"], "attempt_link": r["attempt_link"],
            "recording": recording(r["recording"], "cdr") or {"has_recording": False, "file": None, "url": None}}
           for r in rows]
    imp = [{"id": e["id"], "source_name": e["source_name"],
            "source": {"bulk_job_id": e["bulk_job_id"], "row_no": e["row_no"], "file_name": e["file_name"],
                       "file_sha256": e["file_sha256"], "sheet": e["sheet"]},
            "uniqueid": e["uniqueid"], "call_at": iso(e["call_at"]), "call_at_source": e["call_at_source"],
            "src": num(e["src"]), "dst": num(e["dst"]), "direction": e["direction"], "disposition": e["disposition"],
            "duration_seconds": e["duration_seconds"], "billsec": e["billsec"],
            "duration_known": e["duration_seconds"] is not None, "attribution": e["attribution"],
            "attempt_link": e["attempt_link"], "leg_role": e["leg_role"], "peer_leg_role": e["peer_leg_role"],
            "imported_by": s(e["imported_by"]), "imported_at": iso(e["imported_at"]),
            "recording": recording(e["recordingfile"], "imported") or {"has_recording": False, "file": None,
                                                                         "url": None}}
           for e in imported(attempt_id)]
    return {
        "attempt_id": attempt["id"], "state": attempt["state"],
        "duration_seconds": attempt["duration_seconds"], "duration_known": attempt["duration_seconds"] is not None,
        "cdr": {"status": status, "records": cdr},
        "imported": imp,
        "recordings": [{"has_recording": True, "file": v.get("file"), "url": v.get("url"), "sources": v["sources"]}
                       for v in recs.values()],
        "recording_access": can_hear,
        "note": ("PBX records are evidence next to the call attempt; they never change it. An unreadable CDR is "
                 "'unavailable' (unknown), not 'no records'."),
    }


__all__ = ["attempt_evidence", "imported", "live_cdr", "visible_attempt"]
