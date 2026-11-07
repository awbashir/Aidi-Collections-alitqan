"""PBX (Issabel) CDR source for the v2 call reports (Phase 4, stream R-C): RPT-23 calls mode
and RPT-25. The report definitions are in services/reports_v2_c.py; this module reads the
CDR, attributes it and builds the SQL relation they select from.

Source (read-only, the same databases and credentials as the legacy `/cdr*` routes,
`core.config.Config.CDR_*`; issable/db_connection.py itself is not changed):

    asteriskcdrdb.cdr          one row per Asterisk CDR record (legs are separate records)
    call_center.calls          Issabel call-center campaign calls (+ agent, + recording)

Rows are read for one time window per request (`calldate >= lo AND calldate < hi`, the
index-friendly form of the legacy `DATE(calldate) BETWEEN`). `calldate`/`start_time` are the
PBX's local wall clock; `CDR_TIMEZONE` (default Asia/Riyadh, which is what the legacy
dashboards assume) turns them into instants. Unknown stays unknown: a call-center row with
no duration has `duration = NULL`, and call-center rows never carry a billable duration
(`billsec = NULL`); nothing is substituted with 0 (docs/PROJECT-NOTES.md §0.7).

The rows are attributed once per request (`attribute()`: two indexed lookups in a short
read-only transaction, just before the report's own snapshot) and bound into PostgreSQL as
arrays (`unnest(...)`), so the report's statements only join the attributed case/users by
primary key. The CDR itself is an external system and shares no snapshot with PostgreSQL.

Attribution (the reviewed rules; every row says which applied):

* **CDR row -> case**: the legacy matching (`_mobile_like_clause`: the call's src/dst
  contains the last 9 digits of one of the case debtor's contacts) done in the other
  direction: the last 9 digits of src and of dst (after Arabic-Indic digits -> ASCII) are
  looked up among the last 9 digits of every live contact ("ClintContacts".is_deleted =
  false; index 0046). A debtor's contacts belong to all of the debtor's cases
  (clints."identityNumber"). Archived cases count.
      attribution = case        exactly one matching case in the viewer's current scope
                    ambiguous   two or more in scope: no case, owner or contract is shown
                    unmatched   no case at all matches the number
  Difference to legacy: legacy is "contains"; a number that contains the 9 digits but does
  not END with them (e.g. an extra trailing digit) is not matched.
* **CDR row -> agent**: the extension-looking party (2-6 digits) is compared with the
  CURRENT `users.extension` of active users (the legacy dashboard rule, `call.src ===
  user.extension`). One user -> that user; several users with the same extension ->
  `agent_basis = ambiguous` and no agent (never counted for several people).
  Call-center rows use the agent's number (`call_center.agent.number`).
* **case -> collector**: the attributed case's CURRENT owner (clints.user_id). The agent and
  the owner are different roles and are reported separately.
* **CDR row -> call attempt** (Phase 5 P4, services/calling_cdr.attempt_tag): a record whose
  userfield carries `cawp:<attempt>` or whose uniqueid/linkedid is `cawp-<attempt>` (or a
  truncated uniqueid naming exactly one attempt) is linked to that `call_attempts` row. The
  link is exact, so it replaces the phone match for that record: case = the attempt's case
  (attribution = attempt when it is in the viewer's scope), agent = the attempt's collector
  (agent_basis = attempt). Untagged records are attributed exactly as before.

Visibility (never reveal a call on an out-of-scope case): a record is visible when
  (a) it matches at least one case in the viewer's current case scope, or
  (b) it matches no case at all, and the viewer has unrestricted scope (SuperAdmin) or its
      agent is in the viewer's user scope (supervised user / supervised contract / self).
A record whose number matches only out-of-scope cases is never listed or counted.

Settings (read at call time):

| Variable | Default | Meaning |
|---|---|---|
| `CDR_TIMEZONE` | Asia/Riyadh | time zone of the PBX wall clock (calldate, start_time) |
| `CDR_REPORT_MAX_ROWS` | 50000 | CDR rows one report window may read; more -> 422 `cdr_window_too_large` |
| `CDR_REPORT_CACHE_SECONDS` | 30 | a window read for a preview/selector is reused for this long (never by exports) |
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import re
import threading
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

log = logging.getLogger("cawp.reports.cdr")

# The phone key, textually identical to the expression of index ix_clintcontacts_phone_key
# (db/migrations/0046) so that PostgreSQL uses it.
KEY_SQL = ("right(regexp_replace(translate({col}, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'), "
           "'[^0-9]', '', 'g'), 9)")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EXT_RE = re.compile(r"^[0-9]{2,6}$")
EXTENSION_SQL = "'^[0-9]{2,6}$'"

SOURCES = {"cdr": ("Asterisk CDR", "سجل المقسم"), "call_center": ("Call center", "مركز الاتصال")}
DIRECTIONS = {"outbound": ("Outbound", "صادرة"), "inbound": ("Inbound", "واردة"),
              "internal": ("Internal", "داخلية"), "unknown": ("Unknown", "غير معروف")}
PBX_STATUS = {"answered": ("PBX answered (not proof of a conversation)", "رد المقسم (ليس دليلاً على محادثة)"),
              "no_answer": ("No answer", "لم يتم الرد"), "busy": ("Busy", "مشغول"),
              "failed": ("Failed", "فشل"), "other": ("Other", "أخرى"), "unknown": ("Unknown", "غير معروف")}
ATTRIBUTION = {"attempt": ("Call attempt tag (exact)", "وسم محاولة الاتصال (مطابق)"),
               "case": ("One case in your scope", "عميل واحد ضمن نطاقك"),
               "ambiguous": ("Several cases in your scope", "أكثر من عميل ضمن نطاقك"),
               "unmatched": ("No matching case", "لا يوجد عميل مطابق")}
AGENT_BASIS = {"attempt": ("Collector of the call attempt", "محصل محاولة الاتصال"),
               "extension": ("Current extension of one user", "التحويلة الحالية لمستخدم واحد"),
               "ambiguous": ("Extension shared by several users", "تحويلة مشتركة بين عدة مستخدمين"),
               "unknown_extension": ("Extension of no active user", "تحويلة لا تخص مستخدماً نشطاً"),
               "none": ("No extension on the call", "لا توجد تحويلة في المكالمة")}


class CdrUnavailable(Exception):
    """The CDR database is disabled (CDR_ENABLED=0) or cannot be reached."""


class CdrWindowTooLarge(Exception):
    def __init__(self, rows: int, limit: int):
        super().__init__(f"{rows} CDR rows exceed the report limit of {limit}")
        self.rows, self.limit = rows, limit


# --------------------------------------------------------------------------- settings
def pbx_zone() -> ZoneInfo:
    return ZoneInfo(os.getenv("CDR_TIMEZONE") or "Asia/Riyadh")


def max_rows() -> int:
    try:
        return max(1, int(os.getenv("CDR_REPORT_MAX_ROWS", "50000")))
    except ValueError:
        return 50000


def cache_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CDR_REPORT_CACHE_SECONDS", "30")))
    except ValueError:
        return 30.0


# --------------------------------------------------------------------------- phone keys
def phone_key(value) -> str | None:
    """Python twin of KEY_SQL for a CDR number: the last 9 digits, or None under 9 digits
    (legacy `_mobile_like_clause` skips numbers with fewer than 9 digits)."""
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value).translate(_ARABIC_DIGITS))
    return digits[-9:] if len(digits) >= 9 else None


def is_extension(value) -> bool:
    return bool(value) and bool(_EXT_RE.match(str(value).strip()))


def recording_url(path: str | None) -> str | None:
    """The legacy download/playback route (unchanged): GET /cdr/recording?recordingPath=."""
    return f"/cdr/recording?recordingPath={quote(path, safe='')}" if path else None


# --------------------------------------------------------------------------- reading
def _connect():
    from core.config import Config
    if not Config.CDR_ENABLED:
        raise CdrUnavailable("the CDR source is disabled (CDR_ENABLED=0)")
    import mysql.connector
    try:
        return mysql.connector.connect(host=Config.CDR_HOST, database=Config.CDR_DB, user=Config.CDR_USER,
                                       password=Config.CDR_PASSWORD, connection_timeout=Config.CDR_CONNECT_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - any driver error means "unavailable"
        log.warning("CDR database unavailable (%s): %s", Config.CDR_HOST, e)
        raise CdrUnavailable(f"the CDR database cannot be reached: {type(e).__name__}") from None


_CDR_SQL = """
    SELECT calldate, src, dst, dcontext, channel, dstchannel, duration, billsec, disposition, uniqueid, recordingfile,
           userfield
      FROM asteriskcdrdb.cdr
     WHERE {bound}calldate >= %s AND calldate < %s{extra}
     ORDER BY calldate, uniqueid
     LIMIT %s
"""
_CC_SQL = """
    SELECT c.id, c.start_time, c.phone, c.duration, c.status, c.uniqueid, a.name AS agent_name,
           a.number AS agent_number, cr.recordingfile
      FROM call_center.calls c
      LEFT JOIN call_center.agent a ON a.id = c.id_agent
      LEFT JOIN call_center.call_recording cr ON cr.id_call_outgoing = c.id AND cr.uniqueid = c.uniqueid
     WHERE c.start_time >= %s AND c.start_time < %s{extra}
     ORDER BY c.start_time, c.id, cr.id
     LIMIT %s
"""


# 2026-10-25 (PBX CPU): Issabel's asteriskcdrdb.cdr has no calldate index; on an Asterisk <= 11 CDR
# (no linkedid) the uniqueid "<epoch>.<seq>" is indexed, so the window is also bounded by uniqueid
# (an hour wider each side; the calldate predicate keeps the rows exact), as services/calling/
# monitor.py does. call_center.call_recording has no uniqueid index: the join goes through its
# indexed id_call_outgoing and keeps the uniqueid equality (a retried dialer call keeps one row
# whose uniqueid is the LAST attempt's; older attempts' recordings stay out, as before).
_EPOCH_UNIQUEID: dict = {}


def _uniqueid_bound(cur, lo, hi) -> tuple[str, list]:
    if "cdr" not in _EPOCH_UNIQUEID:
        cur.execute("SHOW COLUMNS FROM asteriskcdrdb.cdr LIKE %s", ("linkedid",))
        _EPOCH_UNIQUEID["cdr"] = not cur.fetchall()
    if not _EPOCH_UNIQUEID["cdr"]:
        return "", []
    zone = pbx_zone()
    epoch = lambda v: int(v.replace(tzinfo=zone).timestamp())  # noqa: E731 - naive PBX wall clock
    return "uniqueid >= %s AND uniqueid < %s AND ", [str(epoch(lo) - 3600), str(epoch(hi) + 3600)]


def _like(text_value: str) -> str:
    return "%" + text_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _read(lo_utc: _dt.datetime, hi_utc: _dt.datetime, sources: tuple, number: str | None) -> list[dict]:
    zone = pbx_zone()
    lo = lo_utc.astimezone(zone).replace(tzinfo=None)
    hi = hi_utc.astimezone(zone).replace(tzinfo=None)
    limit = max_rows()
    conn = _connect()
    rows: list[dict] = []
    try:
        cur = conn.cursor(dictionary=True)
        try:
            if "cdr" in sources:
                bound, params = _uniqueid_bound(cur, lo, hi)
                extra = ""
                params += [lo, hi]
                if number:
                    extra = " AND (src LIKE %s OR dst LIKE %s)"
                    params += [_like(number), _like(number)]
                cur.execute(_CDR_SQL.format(bound=bound, extra=extra), (*params, limit + 1))
                rows.extend(_cdr_row(r, zone) for r in cur.fetchall())
                _dedupe_ids(rows)
            from core.config import Config
            if "call_center" in sources and Config.CDR_CALL_CENTER:
                extra, params = "", [lo, hi]
                if number:
                    extra = " AND c.phone LIKE %s"
                    params.append(_like(number))
                cur.execute(_CC_SQL.format(extra=extra), (*params, limit + 1))
                rows.extend(_cc_rows(cur.fetchall(), zone))
        finally:
            cur.close()
    except CdrUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("CDR query failed: %s", e)
        raise CdrUnavailable(f"the CDR query failed: {type(e).__name__}") from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if len(rows) > limit:
        raise CdrWindowTooLarge(len(rows), limit)
    return rows


def _instant(naive, zone) -> _dt.datetime | None:
    if naive is None:
        return None
    if isinstance(naive, _dt.date) and not isinstance(naive, _dt.datetime):
        naive = _dt.datetime.combine(naive, _dt.time.min)
    return naive.replace(tzinfo=zone).astimezone(_dt.timezone.utc)


def _int_or_none(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _direction(src, dst):
    s, d = is_extension(src), is_extension(dst)
    if s and not d:
        return "outbound", str(src).strip()
    if d and not s:
        return "inbound", str(dst).strip()
    if s and d:
        return "internal", str(src).strip()
    return "unknown", None


def _cdr_status(disposition):
    d = (disposition or "").strip().upper()
    if not d:
        return "unknown"
    return {"ANSWERED": "answered", "NO ANSWER": "no_answer", "BUSY": "busy", "FAILED": "failed",
            "CONGESTION": "failed"}.get(d, "other")


def _cc_status(status):
    s = (status or "").strip().lower()
    if not s:
        return "unknown"
    return {"success": "answered", "noanswer": "no_answer", "no answer": "no_answer", "busy": "busy",
            "failure": "failed", "failed": "failed"}.get(s, "other")


def _attempt_tag(r: dict):
    from services.calling_cdr import attempt_tag
    return attempt_tag(r.get("uniqueid"), r.get("userfield"), r.get("linkedid"))


def _cdr_row(r: dict, zone) -> dict:
    src, dst = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
    direction, ext = _direction(src, dst)
    content = "|".join(str(r.get(k)) for k in ("calldate", "src", "dst", "channel", "dstchannel", "disposition",
                                                "duration", "billsec", "recordingfile"))
    return {
        "row_id": f"cdr:{r.get('uniqueid') or '-'}:{hashlib.sha1(content.encode()).hexdigest()[:12]}",
        "source": "cdr",
        "call_at": _instant(r.get("calldate"), zone),
        "src": src or None, "dst": dst or None,
        "direction": direction, "agent_ext": ext, "agent_label": None,
        "external": dst if direction == "outbound" else src if direction == "inbound" else (dst or src or None),
        "key_src": phone_key(src), "key_dst": phone_key(dst),
        "disposition": r.get("disposition") or None, "pbx_status": _cdr_status(r.get("disposition")),
        # Asterisk writes both as NOT NULL integers: known values (0 is a real 0)
        "duration": _int_or_none(r.get("duration")), "billsec": _int_or_none(r.get("billsec")),
        "uniqueid": r.get("uniqueid") or None,
        "recording": (r.get("recordingfile") or "").strip() or None, "recording_count": None,
        "recording_url": recording_url((r.get("recordingfile") or "").strip() or None),
        "attempt_tag": _attempt_tag(r), "attempt_id": None, "attempt_link": None,
    }


def _dedupe_ids(rows):
    seen = {}
    for r in rows:
        n = seen.get(r["row_id"], 0)
        seen[r["row_id"]] = n + 1
        if n:
            r["row_id"] = f"{r['row_id']}:{n}"


def _cc_rows(raw: list, zone) -> list[dict]:
    """One row per call-center call (the legacy join repeats a call per recording row and
    drops calls without one); the first recording row is linked, the count is kept."""
    out, by_id = [], {}
    for r in raw:
        cid = r.get("id")
        if cid in by_id:
            if r.get("recordingfile"):
                by_id[cid]["recording_count"] += 1
            continue
        phone = (r.get("phone") or "").strip()
        agent_number = (r.get("agent_number") or "").strip()
        rec = (r.get("recordingfile") or "").strip() or None
        row = {
            "row_id": f"cc:{cid}", "source": "call_center",
            "call_at": _instant(r.get("start_time"), zone),
            "src": agent_number or None, "dst": phone or None,
            "direction": "outbound", "agent_ext": agent_number if is_extension(agent_number) else None,
            "agent_label": r.get("agent_name") or None,
            "external": phone or None, "key_src": None, "key_dst": phone_key(phone),
            "disposition": r.get("status") or None, "pbx_status": _cc_status(r.get("status")),
            "duration": _int_or_none(r.get("duration")),     # NULL stays unknown
            "billsec": None,                                   # the call center has no billable duration
            "uniqueid": r.get("uniqueid") or None,
            "recording": rec, "recording_count": 1 if rec else 0, "recording_url": recording_url(rec),
            "attempt_tag": None, "attempt_id": None, "attempt_link": None,
        }
        by_id[cid] = row
        out.append(row)
    return out


_cache: dict = {}
_cache_lock = threading.Lock()


def fetch(lo_utc, hi_utc, *, sources=("cdr", "call_center"), number: str | None = None,
          fresh: bool = False) -> list[dict]:
    """CDR rows with lo <= call time < hi (UTC instants). `fresh` (exports) bypasses the
    short in-process cache used by previews and selectors."""
    key = (lo_utc.isoformat(), hi_utc.isoformat(), tuple(sorted(sources)), number or "")
    ttl = cache_seconds()
    now = time.monotonic()
    if not fresh and ttl:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
    rows = _read(lo_utc, hi_utc, tuple(sources), number)
    if ttl:
        with _cache_lock:
            for k in [k for k, v in _cache.items() if now - v[0] >= ttl]:
                _cache.pop(k, None)
            if len(_cache) > 16:
                _cache.clear()
            _cache[key] = (now, rows)
    return rows


def clear_cache():
    with _cache_lock:
        _cache.clear()


# --------------------------------------------------------------------------- attribution + relation
_COLS = (  # (array name, SQL type, row key)
    ("row_id", "text", "row_id"), ("source", "text", "source"), ("call_at", "timestamptz", "call_at"),
    ("src", "text", "src"), ("dst", "text", "dst"), ("direction", "text", "direction"),
    ("agent_ext", "text", "agent_ext"), ("agent_label", "text", "agent_label"), ("external_number", "text", "external"),
    ("disposition", "text", "disposition"), ("pbx_status", "text", "pbx_status"), ("duration", "int", "duration"),
    ("billsec", "int", "billsec"), ("uniqueid", "text", "uniqueid"), ("recording_file", "text", "recording"),
    ("recording_count", "int", "recording_count"), ("recording_url", "text", "recording_url"),
    # attribution (computed by attribute(), per viewer)
    ("n_all", "int", "n_all"), ("n_scope", "int", "n_scope"), ("case_id", "uuid", "case_id"),
    ("attribution", "text", "attribution"), ("agent_user_id", "uuid", "agent_user_id"),
    ("agent_basis", "text", "agent_basis"), ("agent_in_scope", "boolean", "agent_in_scope"),
    # call-attempt link (Phase 5 P4)
    ("attempt_id", "uuid", "attempt_id"), ("attempt_link", "text", "attempt_link"),
)


def user_scope_sql(scope, alias: str, prefix: str) -> tuple[str, dict]:
    """Users visible to `scope` (the employee side of services.clients_scope): SuperAdmin all;
    supervisor = supervised users + self OR users configured on a supervised contract;
    collector = self."""
    from services.clients_scope import COLLECTOR, SUPER
    if scope.kind == SUPER:
        return "TRUE", {}
    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return f"{alias}.id = CAST(:{prefix}_me AS uuid)", {f"{prefix}_me": str(scope.identity)}
    parts, params = [], {}
    if scope.user_ids:
        parts.append(f"{alias}.id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(str(u) for u in scope.user_ids)
    if scope.contract_ids:
        parts.append(f"{alias}.contract_id = ANY(CAST(:{prefix}_cids AS uuid[]))")
        params[f"{prefix}_cids"] = sorted(str(c) for c in scope.contract_ids)
    return "(" + " OR ".join(parts) + ")", params


_INDEX = {"at": None, "ok": False}


def phone_index_ready(conn) -> bool:
    """True when index ix_clintcontacts_phone_key (migration 0046) exists and is valid.
    Checked at most every 5 minutes per process."""
    from sqlalchemy import text
    now = time.monotonic()
    if _INDEX["at"] is not None and now - _INDEX["at"] < 300:
        return _INDEX["ok"]
    ok = conn.execute(text("""SELECT coalesce((SELECT indisvalid FROM pg_index
                                               WHERE indexrelid = to_regclass('ix_clintcontacts_phone_key')), false)""")).scalar()
    _INDEX.update(at=now, ok=bool(ok))
    return bool(ok)


def attribute(ctx, rows: list[dict], conn=None) -> list[dict]:
    """Adds the per-viewer attribution to `rows` (in place; returns them):

    n_all        cases (anywhere) whose live contacts match the src or dst phone key
    n_scope      of those, the cases in the viewer's current case scope
    case_id      the in-scope case when n_scope == 1, else None
    attribution  attempt | case | ambiguous | unmatched (see the module docstring)
    agent_*      the active user holding the agent extension (unique), basis, in the viewer's user scope
    attempt_*    the call attempt named by the record's cawp tag (exact), and how it was linked

    Two indexed lookups in one short read-only transaction (index 0046 for the keys), run
    once per report request, just before the report's own snapshot."""
    from sqlalchemy import text
    keys = sorted({k for r in rows for k in (r["key_src"], r["key_dst"]) if k})
    exts = sorted({r["agent_ext"] for r in rows if r["agent_ext"]})
    case_scope, cp = ctx.scope_sql("c", prefix="cdrsc")
    user_scope, up = user_scope_sql(ctx.scope, "u", "cdrus")
    key_sql = KEY_SQL.format(col="cc.contact")
    hits: dict[str, list] = {}
    agents: dict[str, list] = {}
    tagged: dict[str, dict] = {}         # tag key -> attempt row + case/user scope flags

    def run(c):
        if keys:
            if phone_index_ready(c):
                # index 0046: one btree probe per key (OFFSET 0 keeps the planner from turning this
                # into a join it cannot estimate, which it answers with a full index scan)
                ck = f"""SELECT DISTINCT kk.k, x.ident FROM unnest(CAST(:cdr_keys AS text[])) AS kk(k)
                           CROSS JOIN LATERAL (SELECT cc."identityNumber" AS ident FROM "ClintContacts" cc
                                                WHERE {key_sql} = kk.k AND cc.is_deleted = false OFFSET 0) x"""
            else:
                # no index yet: one sequential scan with a hashed `= ANY(<literal array>)` (PostgreSQL 14+)
                ck = f"""SELECT DISTINCT {key_sql} AS k, cc."identityNumber" AS ident FROM "ClintContacts" cc
                          WHERE cc.is_deleted = false AND {key_sql} = ANY(CAST(:cdr_keys AS text[]))"""
            for k, case_id, in_scope in c.execute(text(f"""
                    WITH ck AS MATERIALIZED ({ck})
                    SELECT DISTINCT ck.k, c.id::text, ({case_scope})
                      FROM ck CROSS JOIN LATERAL (SELECT * FROM clints c0 WHERE c0."identityNumber" = ck.ident OFFSET 0) c
                    """), {"cdr_keys": keys, **cp}):
                hits.setdefault(k, []).append((case_id, bool(in_scope)))
        if exts:
            for ext, uid, in_scope in c.execute(text(f"""
                    SELECT u.extension, u.id::text, ({user_scope}) FROM users u
                     WHERE u.is_active AND u.extension = ANY(CAST(:cdr_exts AS text[]))"""), {"cdr_exts": exts, **up}):
                agents.setdefault(ext, []).append((uid, bool(in_scope)))
        tags = [r.get("attempt_tag") for r in rows if r.get("attempt_tag")]
        if tags:
            from services.calling_cdr import resolve_attempt_tags
            found = resolve_attempt_tags(c, tags)
            if found:
                case_ids = sorted({a["case_id"] for a in found.values()})
                user_ids = sorted({a["collector_user_id"] for a in found.values()})
                cin = dict(c.execute(text(f"""SELECT c.id::text, ({case_scope}) FROM clints c
                                               WHERE c.id = ANY(CAST(:cdr_acases AS uuid[]))"""),
                                     {"cdr_acases": case_ids, **cp}).all())
                uin = dict(c.execute(text(f"""SELECT u.id::text, ({user_scope}) FROM users u
                                               WHERE u.id = ANY(CAST(:cdr_ausers AS uuid[]))"""),
                                     {"cdr_ausers": user_ids, **up}).all())
                for k, a in found.items():
                    if a["case_id"] in cin:          # a hard-deleted case: fall back to the phone match
                        tagged[k] = {**a, "case_in_scope": bool(cin[a["case_id"]]),
                                     "user_in_scope": bool(uin.get(a["collector_user_id"]))}

    if conn is not None:
        run(conn)
    else:
        from extensions import db
        with db.engine.connect() as c:
            c = c.execution_options(postgresql_readonly=True)
            with c.begin():
                run(c)
    for r in rows:
        cases = {}
        for k in (r["key_src"], r["key_dst"]):
            for case_id, in_scope in hits.get(k, ()) if k else ():
                cases[case_id] = in_scope
        in_scope = sorted(cid for cid, ok in cases.items() if ok)
        r["n_all"], r["n_scope"] = len(cases), len(in_scope)
        r["case_id"] = in_scope[0] if len(in_scope) == 1 else None
        r["attribution"] = "case" if len(in_scope) == 1 else "ambiguous" if in_scope else "unmatched"
        holders = agents.get(r["agent_ext"], []) if r["agent_ext"] else []
        if not r["agent_ext"]:
            basis = "none"
        elif not holders:
            basis = "unknown_extension"
        elif len(holders) == 1:
            basis = "extension"
        else:
            basis = "ambiguous"
        r["agent_basis"] = basis
        r["agent_user_id"] = holders[0][0] if basis == "extension" else None
        r["agent_in_scope"] = holders[0][1] if basis == "extension" else False
        tag = r.get("attempt_tag")
        a = tagged.get(tag["key"]) if tag else None
        r["attempt_id"] = a["id"] if a else None
        r["attempt_link"] = tag["via"] if a else None
        if a:                                   # the exact tag replaces the phone match
            r["n_all"] = 1
            r["n_scope"] = 1 if a["case_in_scope"] else 0
            r["case_id"] = a["case_id"] if a["case_in_scope"] else None
            r["attribution"] = "attempt" if a["case_in_scope"] else "unmatched"
            r["agent_basis"] = "attempt"
            r["agent_user_id"] = a["collector_user_id"]
            r["agent_in_scope"] = a["user_in_scope"]
    return rows


def relation(ctx, rows: list[dict], prefix: str = "cdr") -> tuple[str, str, dict]:
    """(relation SQL aliased `cdr`, visibility predicate, params) for `rows`, attributed for
    the viewer (attribute()). Columns: every _COLS name plus call_day / call_hour (Riyadh)."""
    from services.clients_scope import SUPER
    rows = attribute(ctx, [dict(r) for r in rows])      # copies: the fetched rows are shared through the cache
    params = {f"{prefix}_{name}": [r[key] for r in rows] for name, _t, key in _COLS}
    arrays = ", ".join(f"CAST(:{prefix}_{name} AS {t}[])" for name, t, _k in _COLS)
    names = ", ".join(name for name, _t, _k in _COLS)
    rel = f"""(
        SELECT t.*, (t.call_at AT TIME ZONE 'Asia/Riyadh')::date AS call_day,
               extract(hour FROM t.call_at AT TIME ZONE 'Asia/Riyadh')::int AS call_hour
          FROM unnest({arrays}) AS t({names})
    ) cdr"""
    unrestricted = ctx.scope.kind == SUPER
    visible = ("(cdr.n_scope > 0 OR cdr.n_all = 0)" if unrestricted
               else "(cdr.n_scope > 0 OR (cdr.n_all = 0 AND cdr.agent_in_scope))")
    return rel, visible, params


__all__ = ["KEY_SQL", "SOURCES", "DIRECTIONS", "PBX_STATUS", "ATTRIBUTION", "AGENT_BASIS", "CdrUnavailable",
           "CdrWindowTooLarge", "phone_key", "is_extension", "recording_url", "fetch", "clear_cache", "attribute",
           "relation", "user_scope_sql", "pbx_zone"]
