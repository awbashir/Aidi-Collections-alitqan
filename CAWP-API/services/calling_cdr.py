"""Phase 5 P4: scoped legacy CDR and recordings, and the attempt <-> CDR link.
docs/phase5-spec.md (hard rule 7, stream P4), review/phase5-evidence.md.

The legacy routes keep their wire contracts; only what they return is narrowed:

    GET /cdr                    today's Asterisk CDR (Issable_db.GetSubscription)
    GET /cdr_by_date            Asterisk + call-center rows for the case numbers (GetCombinedCalls)
    GET /cdr/recording          the recording file (SFTP under RECORDINGS_ROOT)

Visibility of one CDR record for a viewer (`visible()`), using the Phase 4 attribution
(services/reports_v2_cdr.attribute: the last 9 digits of src/dst against the live contacts of
every case, the agent by the current users.extension) plus the exact call-attempt tag:

    SuperAdmin                                   everything (the legacy answer, unchanged)
    the record attributes to a case in scope     visible   (n_scope > 0)
    the record's agent is the viewer             visible   (own extension, or the attempt's collector)
    the record matches NO case and its agent     visible   (a team extension; the Phase 4 rule
      is in the viewer's user scope                          for unmatched calls)
    anything else                                hidden    (a call on an out-of-scope case is never
                                                            revealed through a team extension)

Attempt tag (`attempt_tag()`): the calling dialplan sets CDR(userfield)=cawp:<attempt> and the
agent channel's uniqueid is cawp-<attempt> (pbx-test/conf/extensions_collections.conf). The
Issabel/FreePBX asteriskcdrdb.cdr.uniqueid column is varchar(32), so there the uniqueid arrives
TRUNCATED to `cawp-` + 27 characters; a truncated uniqueid links only when its uuid prefix names
exactly one call attempt. Priority: userfield, uniqueid, linkedid, uniqueid prefix; two exact
tags that disagree link nothing.

Recording access (`scoped_recording()`): the requested path must resolve (legacy
`genrate_call_path` + `_validate_remote_path`, unchanged) to the recording of at least one CDR
record (asteriskcdrdb.cdr.recordingfile or call_center.call_recording.recordingfile) that the
caller may see. There is no path-guess access, not even for SuperAdmin. If the CDR database
cannot be read the answer is refused (fail closed). Every request, served or refused, writes one
`audit_events` row (`cdr.recording.served` / `cdr.recording.refused`) in its own transaction.

Reading the file: `RECORDINGS_READER` = `sftp` (default: the legacy
Issable_db.download_call_recording, unchanged) or `local` (the path below RECORDINGS_ROOT is read
from RECORDINGS_LOCAL_DIR, e.g. the test PBX's recordings volume mounted read-only; used by the
tests instead of SFTP). Settings are read at call time.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import re

from sqlalchemy import text

log = logging.getLogger("cawp.calling.cdr")

_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_USERFIELD_TAG = re.compile(r"(?:^|[^0-9a-z])cawp:(" + _UUID_RE + r")(?![0-9a-f-])")
_EXACT_TAG = re.compile(r"^cawp-(" + _UUID_RE + r")$")
_PREFIX_TAG = re.compile(r"^cawp-([0-9a-f-]{8,35})$")
_UUID_SHAPE = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
MIN_PREFIX_HEX = 16            # a truncated uniqueid must still carry 64 bits of the attempt id

ATTEMPT_LINKS = {
    "userfield": ("CDR userfield cawp:<attempt>", "حقل المستخدم cawp:<المحاولة>"),
    "uniqueid": ("PBX uniqueid cawp-<attempt>", "المعرف الفريد cawp-<المحاولة>"),
    "linkedid": ("PBX linkedid cawp-<attempt>", "المعرف المرتبط cawp-<المحاولة>"),
    "uniqueid_prefix": ("Truncated uniqueid naming one attempt", "معرف فريد مقتطع يطابق محاولة واحدة"),
}

RECORDING_KEYS = ("clints_callsRecords_download", "clints_callsRecords_play")
NOT_FOUND_BODY = {"message": "تسجيل المكالمة غير موجود", "status": False, "error": "recording not found"}


# =========================================================================== attempt tags
def _prefix_hex(value: str) -> str | None:
    """The hex digits of a uuid PREFIX in canonical 8-4-4-4-12 shape, or None."""
    if len(value) >= len(_UUID_SHAPE):
        return None
    for i, ch in enumerate(value):
        if _UUID_SHAPE[i] == "-":
            if ch != "-":
                return None
        elif ch not in "0123456789abcdef":
            return None
    digits = value.replace("-", "")
    return digits if len(digits) >= MIN_PREFIX_HEX else None


def attempt_tag(uniqueid=None, userfield=None, linkedid=None) -> dict | None:
    """The call-attempt tag a CDR record carries, or None.

    -> {"key": "id:<uuid>" | "prefix:<hex>", "value": uuid | hex prefix, "exact": bool,
        "via": userfield | uniqueid | linkedid | uniqueid_prefix}"""
    exact = []
    uf = str(userfield or "").strip().lower()
    m = _USERFIELD_TAG.search(uf) if uf else None
    if m:
        exact.append(("userfield", m.group(1)))
    uid = str(uniqueid or "").strip().lower()
    m = _EXACT_TAG.match(uid) if uid else None
    if m:
        exact.append(("uniqueid", m.group(1)))
    lid = str(linkedid or "").strip().lower()
    m = _EXACT_TAG.match(lid) if lid else None
    if m:
        exact.append(("linkedid", m.group(1)))
    if exact:
        if len({v for _via, v in exact}) > 1:          # two exact tags that disagree: link nothing
            return None
        via, value = exact[0]
        return {"key": f"id:{value}", "value": value, "exact": True, "via": via}
    m = _PREFIX_TAG.match(uid) if uid else None
    if m:
        hexs = _prefix_hex(m.group(1))
        if hexs:
            return {"key": f"prefix:{hexs}", "value": hexs, "exact": False, "via": "uniqueid_prefix"}
    return None


def _uuid_bounds(hexs: str) -> tuple[str, str]:
    def fmt(h):
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return fmt(hexs + "0" * (32 - len(hexs))), fmt(hexs + "f" * (32 - len(hexs)))


_ATTEMPT_COLS = """a.id::text AS id, a.case_id::text AS case_id, a.collector_user_id::text AS collector_user_id,
                   a.extension, a.destination, a.state, a.mode, a.created_at, a.started_at, a.dispatched_at,
                   a.ended_at, a.pbx_channel_id"""


def attempts_available(conn) -> bool:
    return bool(conn.execute(text("SELECT to_regclass('public.call_attempts') IS NOT NULL")).scalar())


def resolve_attempt_tags(conn, tags) -> dict[str, dict]:
    """{tag key: attempt row} for the tags that name exactly one existing call attempt.
    Exact tags by primary key; uuid prefixes (truncated uniqueids) by a primary-key range,
    accepted only when the range holds exactly one attempt."""
    tags = [t for t in (tags or ()) if t]
    if not tags or not attempts_available(conn):
        return {}
    out: dict[str, dict] = {}
    exact = sorted({t["value"] for t in tags if t["exact"]})
    if exact:
        for r in conn.execute(text(f"SELECT {_ATTEMPT_COLS} FROM call_attempts a WHERE a.id = ANY(CAST(:ids AS uuid[]))"),
                              {"ids": exact}).mappings():
            out[f"id:{r['id']}"] = dict(r)
    prefixes = sorted({t["value"] for t in tags if not t["exact"]})
    if prefixes:
        bounds = [_uuid_bounds(p) for p in prefixes]
        found: dict[str, list] = {}
        for r in conn.execute(text(f"""
                SELECT p.k, x.* FROM unnest(CAST(:ks AS text[]), CAST(:los AS uuid[]), CAST(:his AS uuid[])) AS p(k, lo, hi)
                 CROSS JOIN LATERAL (SELECT {_ATTEMPT_COLS} FROM call_attempts a
                                      WHERE a.id BETWEEN p.lo AND p.hi ORDER BY a.id LIMIT 2) x"""),
                {"ks": prefixes, "los": [b[0] for b in bounds], "his": [b[1] for b in bounds]}).mappings():
            row = dict(r)
            found.setdefault(row.pop("k"), []).append(row)
        for p, rows in found.items():
            if len(rows) == 1:
                out[f"prefix:{p}"] = rows[0]
    return out


# =========================================================================== viewer + attribution
class Viewer:
    """The minimal report-context shape services.reports_v2_cdr.attribute() needs."""

    def __init__(self, user_id, scope=None):
        from services.clients_scope import resolve_scope
        self.actor_id = str(user_id)
        self.scope = scope if scope is not None else resolve_scope(self.actor_id)

    @property
    def unrestricted(self) -> bool:
        from services.clients_scope import SUPER
        return self.scope.kind == SUPER

    def scope_sql(self, alias: str = "c", prefix: str = "sc"):
        from core.reports.engine import scope_sql
        return scope_sql(self.scope, alias, prefix)


def is_legacy_call_center(row: dict) -> bool:
    """GetCallsFromCallCenter rows have exactly the aliased call-center columns; Asterisk CDR
    rows come from `SELECT *` (dcontext, channel, ...)."""
    return "dcontext" not in row and "channel" not in row and "clid" not in row


def attribution_row(row: dict) -> dict:
    """A legacy /cdr* row -> the row shape of services.reports_v2_cdr (for attribute())."""
    from services import reports_v2_cdr as cdrsrc
    zone = cdrsrc.pbx_zone()
    if is_legacy_call_center(row):
        phone = (row.get("dst") or "").strip()
        return {"source": "call_center", "src": None, "dst": phone or None, "key_src": None,
                "key_dst": cdrsrc.phone_key(phone), "agent_ext": None, "direction": "outbound",
                "attempt_tag": None, "attempt_id": None, "attempt_link": None,
                "call_at": cdrsrc._instant(row.get("calldate"), zone), "uniqueid": row.get("uniqueid")}
    a = cdrsrc._cdr_row(row, zone)
    a["attempt_tag"] = attempt_tag(row.get("uniqueid"), row.get("userfield"), row.get("linkedid"))
    return a


def visible(viewer: Viewer, a: dict) -> bool:
    if viewer.unrestricted:
        return True
    if (a.get("n_scope") or 0) > 0:
        return True
    if a.get("agent_user_id") and str(a["agent_user_id"]) == viewer.actor_id:
        return True
    return (a.get("n_all") or 0) == 0 and bool(a.get("agent_in_scope"))


def scope_legacy_rows(user_id, rows) -> list:
    """The legacy rows the caller may see, unchanged and in their original order.
    SuperAdmin: the list itself (nothing is attributed, nothing changes)."""
    rows = list(rows or [])
    viewer = Viewer(user_id)
    if viewer.unrestricted or not rows:
        return rows
    from services import reports_v2_cdr as cdrsrc
    arows = [attribution_row(r) for r in rows]
    cdrsrc.attribute(viewer, arows)
    return [r for r, a in zip(rows, arows) if visible(viewer, a)]


# =========================================================================== recordings
def _legacy_helper():
    """Issable_db without its constructor: the path helpers need no MySQL connection."""
    from issable.db_connection import Issable_db
    return Issable_db.__new__(Issable_db)


def _root() -> str:
    from core.config import Config
    root = Config.RECORDINGS_ROOT
    return root if root.endswith("/") else root + "/"


def resolve_path(path) -> str | None:
    """The full remote path the legacy download would read (genrate_call_path +
    _validate_remote_path, unchanged), or None when the legacy code would refuse it."""
    if not path or not isinstance(path, str):
        return None
    from issable.db_connection import Issable_db
    try:
        full = _legacy_helper().genrate_call_path(path)
    except Exception:  # noqa: BLE001 - e.g. an empty or odd value: the legacy code would fail too
        return None
    return Issable_db._validate_remote_path(full)


_DATE_DIR = re.compile(r"^([0-9]{4})/([0-9]{2})/([0-9]{2})/")


def _date_hint(full: str) -> _dt.date | None:
    """The PBX day of a recording from its YYYY/MM/DD directory (every Issabel layout the legacy
    path builder produces), used to keep the CDR lookup on the calldate index."""
    m = _DATE_DIR.match(full[len(_root()):])
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_REC_CDR_SQL = """
    SELECT calldate, src, dst, dcontext, channel, dstchannel, duration, billsec, disposition, uniqueid,
           userfield, recordingfile
      FROM asteriskcdrdb.cdr
     WHERE recordingfile IN ({marks}){window}
     LIMIT 200
"""
_REC_CC_SQL = """
    SELECT c.id, c.start_time, c.phone, c.duration, c.status, c.uniqueid, a.name AS agent_name,
           a.number AS agent_number, cr.recordingfile
      FROM call_center.call_recording cr
      JOIN call_center.calls c ON c.uniqueid = cr.uniqueid
      LEFT JOIN call_center.agent a ON a.id = c.id_agent
     WHERE cr.recordingfile IN ({marks}){window}
     ORDER BY c.id, cr.id
     LIMIT 200
"""


def recording_records(path: str, full: str) -> list[dict]:
    """CDR records (reports_v2_cdr row shape) whose recording resolves to `full`.
    Raises reports_v2_cdr.CdrUnavailable when the CDR cannot be read."""
    from services import reports_v2_cdr as cdrsrc
    root = _root()
    candidates = sorted({path, os.path.basename(path), full, full[len(root):]} - {""})
    marks = ", ".join(["%s"] * len(candidates))
    day = _date_hint(full)
    window, wparams = "", []
    if day is not None:
        lo = _dt.datetime.combine(day - _dt.timedelta(days=1), _dt.time.min)
        hi = _dt.datetime.combine(day + _dt.timedelta(days=2), _dt.time.min)
        window, wparams = " AND {col} >= %s AND {col} < %s", [lo, hi]
    zone = cdrsrc.pbx_zone()
    conn = cdrsrc._connect()
    out: list[dict] = []
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(_REC_CDR_SQL.format(marks=marks, window=window.format(col="calldate")),
                        (*candidates, *wparams))
            for r in cur.fetchall():
                if resolve_path((r.get("recordingfile") or "").strip()) != full:
                    continue
                a = cdrsrc._cdr_row(r, zone)
                a["attempt_tag"] = attempt_tag(r.get("uniqueid"), r.get("userfield"), r.get("linkedid"))
                out.append(a)
            cur.execute(_REC_CC_SQL.format(marks=marks, window=window.format(col="cr.datetime_entry")),
                        (*candidates, *wparams))
            cc = [r for r in cur.fetchall() if resolve_path((r.get("recordingfile") or "").strip()) == full]
            for a in cdrsrc._cc_rows(cc, zone):
                a["attempt_tag"] = None
                out.append(a)
        finally:
            cur.close()
    except cdrsrc.CdrUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("recording CDR lookup failed: %s", e)
        raise cdrsrc.CdrUnavailable(f"the CDR query failed: {type(e).__name__}") from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def authorize_recording(user_id, path) -> dict:
    """{"allowed", "reason", "full_path", "records": [attributed rows], "visible": [...]}.
    reasons: invalid_path | cdr_unavailable | no_cdr_record | not_in_scope | allowed"""
    from services import reports_v2_cdr as cdrsrc
    full = resolve_path(path)
    res = {"allowed": False, "reason": "invalid_path", "full_path": full, "records": [], "visible": []}
    if full is None:
        return res
    try:
        records = recording_records(path, full)
    except cdrsrc.CdrUnavailable:
        res["reason"] = "cdr_unavailable"
        return res
    res["records"] = records
    if not records:
        res["reason"] = "no_cdr_record"
        return res
    viewer = Viewer(user_id)
    cdrsrc.attribute(viewer, records)
    res["visible"] = [a for a in records if visible(viewer, a)]
    res["allowed"] = bool(res["visible"])
    res["reason"] = "allowed" if res["allowed"] else "not_in_scope"
    return res


def read_recording(path: str) -> bytes | None:
    """The file behind a legacy recording path: SFTP (legacy, default) or a local directory."""
    mode = (os.getenv("RECORDINGS_READER") or "sftp").strip().lower()
    if mode == "local":
        return _read_local(path)
    return _legacy_helper().download_call_recording(path)


def _read_local(path: str) -> bytes | None:
    base = os.getenv("RECORDINGS_LOCAL_DIR")
    full = resolve_path(path)
    if not base or full is None:
        return None
    rel = full[len(_root()):]
    base_real = os.path.realpath(base)
    local = os.path.realpath(os.path.join(base_real, rel))
    if not local.startswith(base_real + os.sep) or not os.path.isfile(local):
        return None
    if os.path.getsize(local) == 0:          # the legacy reader treats an empty file as missing
        return None
    with open(local, "rb") as fh:
        return fh.read() or None


def _audit(user_id, path, decision: dict, outcome: str, data: bytes | None) -> None:
    from core import audit
    from extensions import db
    vis = decision.get("visible") or []
    cases = sorted({a["case_id"] for a in vis if a.get("case_id")})
    attempts = sorted({a["attempt_id"] for a in vis if a.get("attempt_id")})
    after = {
        "outcome": outcome, "reason": decision.get("reason"),
        "path": (path or "")[:500] if isinstance(path, str) else None,
        "resolved_path": decision.get("full_path"),
        "records": len(decision.get("records") or []), "visible_records": len(vis),
        "uniqueids": sorted({str(a["uniqueid"]) for a in vis if a.get("uniqueid")})[:20],
        "case_ids": cases[:20], "attempt_ids": attempts[:20],
        "bytes": len(data) if data else None,
        "sha256": hashlib.sha256(data).hexdigest() if data else None,
    }
    try:
        with db.engine.begin() as conn:
            audit.record("cdr.recording.served" if outcome == "served" else "cdr.recording.refused",
                         "cdr_recording", ((path or "")[:500] if isinstance(path, str) else None) or "-",
                         case_id=cases[0] if len(cases) == 1 else None, after=after,
                         actor_user_id=str(user_id), source="api", session=conn)
    except Exception:  # noqa: BLE001 - the audit must not turn a refusal into a 500
        log.exception("could not audit a recording access")
        if outcome == "served":
            raise


def scoped_recording(user_id, recording_path) -> bytes | None:
    """The recording bytes when the caller may hear this path, else None (the legacy
    'recording not found' answer). Always audited."""
    if recording_path is None:
        # legacy: download_call_recording(None) raised (AttributeError) -> the route's 500 answer
        _audit(user_id, None, {"reason": "invalid_path"}, "invalid_path", None)
        raise ValueError("recordingPath is required")
    decision = authorize_recording(user_id, recording_path)
    data = read_recording(recording_path) if decision["allowed"] else None
    if decision["allowed"]:
        outcome = "served" if data else "file_not_found"
    else:
        outcome = decision["reason"]
    _audit(user_id, recording_path, decision, outcome, data)
    return data


__all__ = ["ATTEMPT_LINKS", "NOT_FOUND_BODY", "RECORDING_KEYS", "Viewer", "attempt_tag", "attempts_available",
           "attribution_row", "authorize_recording", "read_recording", "recording_records", "resolve_attempt_tags",
           "resolve_path", "scope_legacy_rows", "scoped_recording", "visible"]
