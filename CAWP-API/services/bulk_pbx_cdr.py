"""Bulk kind `pbx_cdr_import`: reviewed import of an Issabel CDR export (Phase 5 P4).
docs/phase5-spec.md (stream P4), docs/PROJECT-NOTES.md §2.7 ("PBX CDR review") and §2.9,
review/phase5-evidence.md.

Flow (the Phase 2 framework, core/bulk): private upload of a CSV/XLSX export -> explicit column
mapping -> validation in the worker -> preview (every row with its attribution and confidence)
-> explicit confirmation (partial allowed) -> execution in batches -> per-row outcomes, results
file, run history. The preview writes nothing but the job's own rows.

What it writes, and only on confirmation: one `pbx_cdr_imported` row (migration 0057) per
eligible record, with its source identity (job, sheet row, upload, file name and sha256, the raw
row), the parsed record, and the attribution decided in the preview. It NEVER changes
`call_attempts`, `call_legs` or a case: a linked attempt keeps its own state and its unknown
duration; the CDR's duration/billsec are evidence next to it. An unknown (blank) duration or
billsec stays NULL, never 0.

Mapping (source column per field; `calldate`, `dst` and `uniqueid` are required):

    calldate       PBX wall-clock start "YYYY-MM-DD HH:MM[:SS]" (or "/" separators, or ISO with
                   an offset/Z); a naive time is read in params.timezone (default CDR_TIMEZONE,
                   Asia/Riyadh: the PBX clock). Anything else is a row error (invalid_calldate).
    src, dst       caller / receiver as the PBX wrote them
    uniqueid       Asterisk uniqueid (the duplicate key and the agent-leg correlation)
    userfield, linkedid        the calling tags cawp:<attempt> / cawp-<attempt>
    duration, billsec          whole seconds, blank = unknown; billsec > duration is a row error
    disposition, channel, dstchannel, recordingfile

Attribution (in the job creator's case scope; the same answer for "no such case" and "a case
you may not see", so a file never reveals out-of-scope cases):

    attempt_tag   exact tag (services/calling_cdr.attempt_tag: userfield cawp:<id>, uniqueid or
                  linkedid cawp-<id>, or a truncated uniqueid naming exactly one attempt) of an
                  attempt whose case is in scope -> that case and attempt
    phone_time    the external number's last 9 digits match live contacts of case(s) in scope,
                  and exactly ONE call attempt on those cases to the same number started within
                  params.match_window_seconds (default 300) of the call -> that attempt's case
    phone         exactly one case in scope matches the number; no attempt is linked
    ambiguous     several cases in scope match and no single attempt decides -> excluded
    unmatched     no case in scope matches -> excluded (kept in the preview/error file)
Legs: for a linked attempt, the call_legs row whose uniqueid (or channel) is the record's is
`leg_*`, and the one whose channel is the record's dstchannel is `peer_leg_*`.

Duplicates (source key = sha256(uniqueid | call instant | src | dst), per params.source_name):
    duplicate_in_file   the same record earlier in the file (first_row_no)
    already_imported    the same record is already evidence (a previous job)
    source_conflict     the same key with different values (in the file or in the evidence)
    Several records of ONE case are all imported: the framework's one-row-per-case rule
    (`duplicate_case`) does not fit evidence, so prepare() re-decides those rows (see
    _adopt_same_case_rows). Execution inserts with ON CONFLICT, so two jobs racing on the
    same records import each once (the loser's row is skipped/already_imported).

Permission: `calls_manage`. The legacy call-records keys (`clints_callsRecords*`) are the
case tab's viewing and listening keys; there is no legacy screen that imports PBX records, so
by the screen-key rule this new admin action uses the new calling-admin key.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from core.bulk.common import dumps, scope_sql
from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome, ParamError
from services.bulk_common import reject_unknown, required_reason

IMPORT_KEYS = ("calls_manage",)
MAX_SECONDS = 604800
DEFAULT_WINDOW = 300
SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$")
_CALLDATE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?"
                       r"\s*(Z|[+-]\d{2}:?\d{2})?$")
_SECONDS = re.compile(r"^\d{1,6}$")
_CHANNEL_EXT = re.compile(r"^(?:PJSIP|SIP|IAX2|DAHDI|Local)/(\d{2,6})(?:[-@;].*)?$", re.I)
TEXT_FIELDS = ("src", "dst", "uniqueid", "userfield", "linkedid", "disposition", "channel", "dstchannel",
               "recordingfile")
RECORD_FIELDS = ("uniqueid", "call_at", "src", "dst", "userfield", "linkedid", "duration", "billsec",
                 "disposition", "channel", "dstchannel", "recordingfile")


# =========================================================================== parsing
def parse_calldate(value, zone) -> _dt.datetime | None:
    """PBX start time -> aware UTC datetime, or None when it is not an unambiguous timestamp."""
    if value is None:
        return None
    m = _CALLDATE.match(str(value).strip())
    if not m:
        return None
    y, mo, d, h, mi, sec, frac, off = m.groups()
    try:
        naive = _dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0),
                             int((frac or "0").ljust(6, "0")))
    except ValueError:
        return None
    if off:
        if off == "Z":
            tz = _dt.timezone.utc
        else:
            sign = 1 if off[0] == "+" else -1
            digits = off[1:].replace(":", "")
            tz = _dt.timezone(sign * _dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
        aware = naive.replace(tzinfo=tz)
    else:
        aware = naive.replace(tzinfo=zone)
    return aware.astimezone(_dt.timezone.utc)


def parse_seconds(value):
    """-> (int | None, ok). Blank is unknown (None, True); never substituted with 0."""
    if value is None or str(value).strip() == "":
        return None, True
    s = str(value).strip()
    if not _SECONDS.match(s) or int(s) > MAX_SECONDS:
        return None, False
    return int(s), True


def _txt(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _iso(value: _dt.datetime | None) -> str | None:
    return None if value is None else value.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def source_key(rec: dict) -> str:
    raw = "\x1f".join([rec["uniqueid"] or "", rec["call_at"] or "", rec["src"] or "", rec["dst"] or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def payload_hash(rec: dict) -> str:
    body = json.dumps({k: rec.get(k) for k in RECORD_FIELDS}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def conflicting_fields(a: dict, b: dict) -> list[str]:
    """Fields both records carry (non-null) with different values. A field mapped in only one
    of two exports is not a conflict."""
    return sorted(k for k in RECORD_FIELDS if a.get(k) is not None and b.get(k) is not None and a[k] != b[k])


def parse_row(mapped: dict, zone) -> tuple[dict | None, Eligibility | None]:
    """-> (record, None) or (None, Eligibility.error)."""
    rec = {k: _txt(mapped.get(k)) for k in TEXT_FIELDS}
    raw_at = _txt(mapped.get("calldate"))
    at = parse_calldate(raw_at, zone)
    if at is None:
        return None, Eligibility.error("invalid_calldate",
                                       "the call date is not a timestamp like 2026-09-20 08:15:00",
                                       {"field": "calldate", "value": raw_at})
    rec["call_at"], rec["call_at_source"] = _iso(at), raw_at
    for f in ("duration", "billsec"):
        v, ok = parse_seconds(mapped.get(f))
        if not ok:
            return None, Eligibility.error(f"invalid_{f}", f"{f} must be whole seconds (blank = unknown)",
                                           {"field": f, "value": _txt(mapped.get(f))})
        rec[f] = v
    if rec["duration"] is not None and rec["billsec"] is not None and rec["billsec"] > rec["duration"]:
        return None, Eligibility.error("billsec_exceeds_duration", "the billed seconds exceed the duration",
                                       {"duration": rec["duration"], "billsec": rec["billsec"]})
    if not rec["uniqueid"]:
        return None, Eligibility.error("missing_value", "uniqueid is empty", {"field": "uniqueid"})
    from services import reports_v2_cdr as cdrsrc
    direction, ext = cdrsrc._direction(rec["src"] or "", rec["dst"] or "")
    if ext is None and rec["channel"]:
        m = _CHANNEL_EXT.match(rec["channel"])
        ext = m.group(1) if m else None
    rec["direction"] = direction
    rec["agent_extension"] = ext
    rec["external_number"] = (rec["dst"] if direction == "outbound" else rec["src"] if direction == "inbound"
                              else (rec["dst"] or rec["src"]))
    rec["key_src"], rec["key_dst"] = cdrsrc.phone_key(rec["src"]), cdrsrc.phone_key(rec["dst"])
    rec["source_key"] = source_key(rec)
    rec["payload_hash"] = payload_hash(rec)
    return rec, None


# =========================================================================== attribution
def _phone_cases(session, keys: list[str], pred: str, params: dict) -> dict[str, set]:
    """{phone key: {in-scope case ids}} through the live contacts (the Phase 4 phone key)."""
    if not keys:
        return {}
    from services import reports_v2_cdr as cdrsrc
    key_sql = cdrsrc.KEY_SQL.format(col="cc.contact")
    if cdrsrc.phone_index_ready(session):
        ck = f"""SELECT DISTINCT kk.k, x.ident FROM unnest(CAST(:pk_keys AS text[])) AS kk(k)
                   CROSS JOIN LATERAL (SELECT cc."identityNumber" AS ident FROM "ClintContacts" cc
                                        WHERE {key_sql} = kk.k AND cc.is_deleted = false OFFSET 0) x"""
    else:
        ck = f"""SELECT DISTINCT {key_sql} AS k, cc."identityNumber" AS ident FROM "ClintContacts" cc
                  WHERE cc.is_deleted = false AND {key_sql} = ANY(CAST(:pk_keys AS text[]))"""
    out: dict[str, set] = {}
    for k, cid in session.execute(text(f"""
            WITH ck AS MATERIALIZED ({ck})
            SELECT DISTINCT ck.k, c.id::text FROM ck
              JOIN clints c ON c."identityNumber" = ck.ident
             WHERE {pred}"""), {"pk_keys": keys, **params}):
        out.setdefault(k, set()).add(cid)
    return out


def _attempts_near(session, case_ids: list[str], lo: str, hi: str) -> list[dict]:
    from services.calling_cdr import attempts_available
    if not case_ids or not attempts_available(session):
        return []
    rows = session.execute(text("""
        SELECT a.id::text AS id, a.case_id::text AS case_id, a.collector_user_id::text AS collector_user_id,
               a.destination, coalesce(a.dispatched_at, a.started_at, a.created_at) AS t
          FROM call_attempts a
         WHERE a.case_id = ANY(CAST(:cids AS uuid[]))
           AND coalesce(a.dispatched_at, a.started_at, a.created_at) BETWEEN CAST(:lo AS timestamptz)
                                                                       AND CAST(:hi AS timestamptz)
    """), {"cids": case_ids, "lo": lo, "hi": hi}).mappings().all()
    from services import reports_v2_cdr as cdrsrc
    return [{**dict(r), "key": cdrsrc.phone_key(r["destination"])} for r in rows]


def _legs(session, attempt_ids: list[str]) -> dict[str, list[dict]]:
    if not attempt_ids or not session.execute(text("SELECT to_regclass('public.call_legs') IS NOT NULL")).scalar():
        return {}
    out: dict[str, list] = {}
    for r in session.execute(text("""
            SELECT id::text AS id, attempt_id::text AS attempt_id, uniqueid, channel, role FROM call_legs
             WHERE attempt_id = ANY(CAST(:ids AS uuid[]))"""), {"ids": attempt_ids}).mappings():
        out.setdefault(r["attempt_id"], []).append(dict(r))
    return out


def _agents(session, exts: list[str]) -> dict[str, list[str]]:
    if not exts:
        return {}
    out: dict[str, list] = {}
    for ext, uid in session.execute(text("""
            SELECT u.extension, u.id::text FROM users u
             WHERE u.is_active AND u.extension = ANY(CAST(:exts AS text[]))"""), {"exts": exts}):
        out.setdefault(ext, []).append(uid)
    return out


def analyse(session, scope, params: dict, mapped_rows: list[tuple[int, dict]]) -> dict[int, dict]:
    """Parse and attribute rows. `mapped_rows` = [(row id, mapped values)]. Returns
    {row id: {"record": dict | None, "error": Eligibility | None, "case_id": str | None,
              "decision": attribution | 'ambiguous' | 'unmatched'}} (record carries the details)."""
    from services.calling_cdr import attempt_tag, resolve_attempt_tags
    zone = ZoneInfo(params.get("timezone") or "Asia/Riyadh")
    window = int(params.get("match_window_seconds", DEFAULT_WINDOW))
    res: dict[int, dict] = {}
    recs: dict[int, dict] = {}
    for rid, mapped in mapped_rows:
        rec, err = parse_row(mapped or {}, zone)
        res[rid] = {"record": rec, "error": err, "case_id": None, "decision": None}
        if rec is not None:
            recs[rid] = rec
    if not recs:
        return res
    pred, sp = scope_sql(session, scope, "c", prefix="pbx")

    # exact tags
    tags = {rid: attempt_tag(r["uniqueid"], r["userfield"], r["linkedid"]) for rid, r in recs.items()}
    found = resolve_attempt_tags(session, [t for t in tags.values() if t])
    tagged_cases = sorted({a["case_id"] for a in found.values()})
    in_scope = set()
    if tagged_cases:
        in_scope = {r[0] for r in session.execute(text(
            f"SELECT c.id::text FROM clints c WHERE c.id = ANY(CAST(:tc AS uuid[])) AND {pred}"),
            {"tc": tagged_cases, **sp})}

    # phone + time
    keys = sorted({k for r in recs.values() for k in (r["key_src"], r["key_dst"]) if k})
    by_key = _phone_cases(session, keys, pred, sp)
    cand_cases = sorted({c for s in by_key.values() for c in s})
    ats = [_dt.datetime.fromisoformat(r["call_at"].replace("Z", "+00:00")) for r in recs.values()]
    pad = _dt.timedelta(seconds=window)
    near = _attempts_near(session, cand_cases, _iso(min(ats) - pad), _iso(max(ats) + pad)) if cand_cases else []

    linked: dict[int, tuple[dict, str]] = {}
    for rid, rec in recs.items():
        tag = tags[rid]
        a = found.get(tag["key"]) if tag else None
        rec["tag"] = None if tag is None else ("resolved" if a and a["case_id"] in in_scope else "unresolved")
        if a is not None and a["case_id"] in in_scope:
            rec["attribution"], rec["attempt_link"] = "attempt_tag", tag["via"]
            rec["matched_cases"] = 1
            linked[rid] = (a, tag["via"])
            res[rid].update(case_id=a["case_id"], decision="attempt_tag")
            continue
        rkeys = {k for k in (rec["key_src"], rec["key_dst"]) if k}
        cases = set().union(*(by_key.get(k, set()) for k in rkeys)) if rkeys else set()
        rec["matched_cases"] = len(cases)
        at = _dt.datetime.fromisoformat(rec["call_at"].replace("Z", "+00:00"))
        close = [x for x in near if x["case_id"] in cases and x["key"] in rkeys
                 and abs((x["t"] - at).total_seconds()) <= window]
        if len(close) == 1:
            x = close[0]
            rec["attribution"], rec["attempt_link"] = "phone_time", "phone_time"
            linked[rid] = (x, "phone_time")
            res[rid].update(case_id=x["case_id"], decision="phone_time")
        elif len(cases) == 1:
            rec["attribution"], rec["attempt_link"] = "phone", None
            res[rid].update(case_id=next(iter(cases)), decision="phone")
        else:
            rec["attribution"], rec["attempt_link"] = ("ambiguous" if cases else "unmatched"), None
            res[rid]["decision"] = rec["attribution"]

    # legs and agents
    legs = _legs(session, sorted({a["id"] for a, _via in linked.values()}))
    agents = _agents(session, sorted({r["agent_extension"] for r in recs.values() if r["agent_extension"]}))
    for rid, rec in recs.items():
        a = linked.get(rid, (None, None))[0]
        rec["attempt_id"] = a["id"] if a else None
        rec.update(leg_id=None, leg_role=None, peer_leg_id=None, peer_leg_role=None)
        for g in legs.get(rec["attempt_id"], []) if a else []:
            if (g["uniqueid"] and g["uniqueid"] == rec["uniqueid"]) or (g["channel"] and g["channel"] == rec["channel"]):
                rec["leg_id"], rec["leg_role"] = g["id"], g["role"]
            elif g["channel"] and g["channel"] == rec["dstchannel"]:
                rec["peer_leg_id"], rec["peer_leg_role"] = g["id"], g["role"]
        if a:
            rec["agent_user_id"], rec["agent_basis"] = a["collector_user_id"], "attempt"
        elif not rec["agent_extension"]:
            rec["agent_user_id"], rec["agent_basis"] = None, "none"
        else:
            holders = agents.get(rec["agent_extension"], [])
            basis = "extension" if len(holders) == 1 else ("ambiguous" if holders else "unknown_extension")
            rec["agent_user_id"], rec["agent_basis"] = (holders[0] if basis == "extension" else None), basis
        rec["case_id"] = res[rid]["case_id"]
    return res


def _details(rec: dict) -> dict:
    d = {k: rec.get(k) for k in (
        "call_at", "call_at_source", "src", "dst", "direction", "external_number", "agent_extension", "uniqueid",
        "userfield", "linkedid", "channel", "dstchannel", "disposition", "recordingfile", "duration", "billsec",
        "attribution", "attempt_id", "attempt_link", "tag", "leg_id", "leg_role", "peer_leg_id", "peer_leg_role",
        "agent_user_id", "agent_basis", "matched_cases", "source_key", "payload_hash")}
    d["duration_known"] = rec.get("duration") is not None
    return d


# =========================================================================== the kind
class PbxCdrImportKind(BulkKind):
    key = "pbx_cdr_import"
    label_en, label_ar = "Import PBX call records (reviewed)", "استيراد سجلات مكالمات المقسم (مراجَع)"
    permissions = IMPORT_KEYS
    sources = ("spreadsheet",)
    mapping_fields = (
        MappingField("calldate", "Call date and time (PBX clock)", "تاريخ ووقت المكالمة (ساعة المقسم)",
                     required=True, max_length=50, description="YYYY-MM-DD HH:MM:SS; naive = params.timezone"),
        MappingField("src", "Source (caller)", "المصدر (المتصل)", max_length=80),
        MappingField("dst", "Destination", "الوجهة", required=True, max_length=80),
        MappingField("uniqueid", "PBX unique ID", "المعرف الفريد في المقسم", required=True, match=True,
                     max_length=150, description="duplicate detection and leg correlation"),
        MappingField("userfield", "User field", "حقل المستخدم", max_length=255,
                     description="carries cawp:<attempt> for calls made from the CRM"),
        MappingField("linkedid", "Linked ID", "المعرف المرتبط", max_length=150),
        MappingField("duration", "Duration (s)", "المدة (ث)", max_length=20, description="blank = unknown"),
        MappingField("billsec", "Billed duration (s)", "المدة المحتسبة (ث)", max_length=20,
                     description="blank = unknown"),
        MappingField("disposition", "Disposition", "الحالة", max_length=45),
        MappingField("channel", "Source channel", "قناة المصدر", max_length=200),
        MappingField("dstchannel", "Destination channel", "قناة الوجهة", max_length=200),
        MappingField("recordingfile", "Recording file", "ملف التسجيل", max_length=255),
    )
    match_by = None
    result_columns = ("attribution", "attempt_id", "attempt_link", "tag", "leg_role", "peer_leg_role",
                      "matched_cases", "call_at", "call_at_source", "direction", "external_number", "duration",
                      "billsec", "disposition", "agent_basis", "first_row_no", "conflict_fields", "evidence_id",
                      "source_key")

    # ------------------------------------------------------------------ params
    def validate_params(self, params, ctx):
        reject_unknown(params, {"reason", "source_name", "timezone", "match_window_seconds"})
        out = {"reason": required_reason(params)}
        name = params.get("source_name", "issabel")
        if not isinstance(name, str) or not SOURCE_NAME_RE.match(name.strip()):
            raise ParamError({"source_name": "invalid"}, "source_name: letters, digits, space . _ - (max 100)")
        out["source_name"] = name.strip()
        tz = params.get("timezone") or os.getenv("CDR_TIMEZONE") or "Asia/Riyadh"
        try:
            ZoneInfo(str(tz))
        except (ZoneInfoNotFoundError, ValueError):
            raise ParamError({"timezone": "invalid"}, "timezone must be an IANA name such as Asia/Riyadh") from None
        out["timezone"] = str(tz)
        w = params.get("match_window_seconds", DEFAULT_WINDOW)
        if isinstance(w, bool) or not isinstance(w, int) or not 0 <= w <= 3600:
            raise ParamError({"match_window_seconds": "invalid"}, "match_window_seconds: whole seconds 0-3600")
        out["match_window_seconds"] = w
        return out

    # ------------------------------------------------------------------ matching (= attribution)
    def match_rows(self, rows, ctx):
        session = ctx.session
        params = ctx.params or {}
        job_id = str(ctx.job["id"])
        res = analyse(session, ctx.scope, params, [(r.id, r.mapped) for r in rows])
        out: dict = {}
        meta = []
        recs = {rid: v["record"] for rid, v in res.items() if v["record"] is not None}
        keys = sorted({rec["source_key"] for rec in recs.values()})

        # duplicates: earlier chunks of this file, earlier rows of this chunk, existing evidence
        earlier: dict[str, tuple] = {}
        if keys:
            for mv, first_row, ph in session.execute(text("""
                    SELECT match_value, min(row_no), (array_agg(details->>'payload_hash' ORDER BY row_no))[1]
                      FROM bulk_job_rows
                     WHERE job_id = CAST(:j AS uuid) AND match_value = ANY(CAST(:k AS text[]))
                     GROUP BY match_value"""), {"j": job_id, "k": keys}):
                earlier[mv] = (first_row, ph)
        existing: dict[str, dict] = {}
        if keys:
            for m in session.execute(text("""
                    SELECT source_key, payload_hash, uniqueid, to_char(call_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS call_at_us, src, dst, userfield, linkedid,
                           duration_seconds AS duration, billsec, disposition, channel, dstchannel, recordingfile,
                           call_at
                      FROM pbx_cdr_imported
                     WHERE source_name = :s AND source_key = ANY(CAST(:k AS text[]))"""),
                    {"s": params.get("source_name", "issabel"), "k": keys}).mappings():
                existing[m["source_key"]] = dict(m)

        for r in rows:
            v = res[r.id]
            rec = v["record"]
            if rec is None:
                out[r.id] = v["error"]
                meta.append({"id": r.id, "mv": None, "d": v["error"].details})
                continue
            d = _details(rec)
            k = rec["source_key"]
            elig = None
            if k in earlier:
                first_row, ph = earlier[k]
                d["first_row_no"] = first_row
                if ph == rec["payload_hash"]:
                    elig = Eligibility.excluded("duplicate_in_file", "the same PBX record is on an earlier row", d)
                else:
                    elig = Eligibility.excluded("source_conflict",
                                                "an earlier row has the same PBX record with different values", d)
            elif k in existing:
                ex = existing[k]
                ex_rec = {f: ex.get(f) for f in RECORD_FIELDS}
                ex_rec["call_at"] = _iso(ex["call_at"])
                diff = conflicting_fields(rec, ex_rec)
                if diff:
                    d["conflict_fields"] = ",".join(diff)
                    elig = Eligibility.excluded("source_conflict",
                                                "this PBX record was already imported with different values", d)
                else:
                    elig = Eligibility.excluded("already_imported", "this PBX record is already imported evidence", d)
            elif v["decision"] == "unmatched":
                elig = Eligibility.excluded("unmatched", "no case in your scope matches this call", d)
            elif v["decision"] == "ambiguous":
                elig = Eligibility.excluded("ambiguous",
                                            "several cases in your scope match this number and no call attempt decides",
                                            d)
            earlier.setdefault(k, (r.row_no, rec["payload_hash"]))
            meta.append({"id": r.id, "mv": k, "d": d})
            out[r.id] = elig if elig is not None else v["case_id"]

        if meta:
            session.execute(text("""
                UPDATE bulk_job_rows r SET match_value = d.mv, details = coalesce(d.d, r.details)
                  FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id bigint, mv text, d jsonb)
                 WHERE r.id = d.id
            """), {"rows": dumps(meta)})
        return out

    # ------------------------------------------------------------------ preview
    def prepare(self, rows, ctx):
        _adopt_same_case_rows(ctx, rows)
        return {r.id: Eligibility.eligible() for r in rows}

    # ------------------------------------------------------------------ execution
    def execute_batch(self, rows, ctx):
        session, job = ctx.session, ctx.job
        up = session.execute(text("""
            SELECT id::text AS id, original_name, sha256 FROM bulk_uploads WHERE id = CAST(:u AS uuid)
        """), {"u": str(job["upload_id"])}).mappings().first()
        params = ctx.params or {}
        data = []
        for r in rows:
            d = r.details or {}
            data.append({
                "source_name": params.get("source_name", "issabel"), "source_key": d["source_key"],
                "payload_hash": d["payload_hash"], "bulk_job_id": str(job["id"]), "row_no": r.row_no,
                "upload_id": up["id"], "file_name": up["original_name"][:500], "file_sha256": up["sha256"],
                "sheet": job.get("sheet"), "raw": r.raw or {},
                "uniqueid": d["uniqueid"], "linkedid": d.get("linkedid"), "userfield": d.get("userfield"),
                "call_at": d["call_at"], "call_at_source": d["call_at_source"],
                "source_timezone": params.get("timezone", "Asia/Riyadh"),
                "src": d.get("src"), "dst": d.get("dst"), "direction": d["direction"],
                "external_number": d.get("external_number"), "agent_extension": d.get("agent_extension"),
                "channel": d.get("channel"), "dstchannel": d.get("dstchannel"), "disposition": d.get("disposition"),
                "recordingfile": d.get("recordingfile"), "duration_seconds": d.get("duration"),
                "billsec": d.get("billsec"), "case_id": r.case_id, "attribution": d["attribution"],
                "attempt_id": d.get("attempt_id"), "attempt_link": d.get("attempt_link"),
                "leg_id": d.get("leg_id"), "leg_role": d.get("leg_role"), "peer_leg_id": d.get("peer_leg_id"),
                "peer_leg_role": d.get("peer_leg_role"), "agent_user_id": d.get("agent_user_id"),
                "agent_basis": d.get("agent_basis"), "matched_cases": d.get("matched_cases"),
                "details": {"tag": d.get("tag"), "reason": params.get("reason")},
                "imported_by": ctx.actor_id,
            })
        inserted = {row_no: eid for eid, row_no in session.execute(text("""
            INSERT INTO pbx_cdr_imported (
                source_name, source_key, payload_hash, bulk_job_id, row_no, upload_id, file_name, file_sha256, sheet,
                raw, uniqueid, linkedid, userfield, call_at, call_at_source, source_timezone, src, dst, direction,
                external_number, agent_extension, channel, dstchannel, disposition, recordingfile, duration_seconds,
                billsec, case_id, attribution, attempt_id, attempt_link, leg_id, leg_role, peer_leg_id, peer_leg_role,
                agent_user_id, agent_basis, matched_cases, details, imported_by)
            SELECT d.source_name, d.source_key, d.payload_hash, d.bulk_job_id, d.row_no, d.upload_id, d.file_name,
                   d.file_sha256, d.sheet, d.raw, d.uniqueid, d.linkedid, d.userfield, d.call_at, d.call_at_source,
                   d.source_timezone, d.src, d.dst, d.direction, d.external_number, d.agent_extension, d.channel,
                   d.dstchannel, d.disposition, d.recordingfile, d.duration_seconds, d.billsec, d.case_id,
                   d.attribution, d.attempt_id, d.attempt_link, d.leg_id, d.leg_role, d.peer_leg_id,
                   d.peer_leg_role, d.agent_user_id, d.agent_basis, d.matched_cases, d.details, d.imported_by
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(
                   source_name text, source_key text, payload_hash text, bulk_job_id uuid, row_no int, upload_id uuid,
                   file_name text, file_sha256 text, sheet text, raw jsonb, uniqueid text, linkedid text,
                   userfield text, call_at timestamptz, call_at_source text, source_timezone text, src text, dst text,
                   direction text, external_number text, agent_extension text, channel text, dstchannel text,
                   disposition text, recordingfile text, duration_seconds int, billsec int, case_id uuid,
                   attribution text, attempt_id uuid, attempt_link text, leg_id uuid, leg_role text,
                   peer_leg_id uuid, peer_leg_role text, agent_user_id uuid, agent_basis text, matched_cases int,
                   details jsonb, imported_by uuid)
            ON CONFLICT DO NOTHING
            RETURNING id::text, row_no
        """), {"rows": dumps(data)})}
        out = {}
        missing = [r for r in rows if r.row_no not in inserted]
        taken = {}
        if missing:
            taken = {k: ph for k, ph in session.execute(text("""
                SELECT source_key, payload_hash FROM pbx_cdr_imported
                 WHERE source_name = :s AND source_key = ANY(CAST(:k AS text[]))"""),
                {"s": params.get("source_name", "issabel"), "k": [(r.details or {})["source_key"] for r in missing]})}
        for r in rows:
            d = r.details or {}
            eid = inserted.get(r.row_no)
            if eid is None:
                if taken.get(d["source_key"]) == d["payload_hash"]:
                    out[r.id] = Outcome.skipped("already_imported", "imported meanwhile by another job")
                else:
                    out[r.id] = Outcome.skipped("source_conflict",
                                                "the same PBX record was imported meanwhile with different values")
                continue
            ctx.audit(r, "pbx_cdr.import", entity_type="pbx_cdr_imported", entity_id=eid,
                      after={"evidence_id": eid, "case_id": r.case_id, "attempt_id": d.get("attempt_id"),
                             "attribution": d["attribution"], "uniqueid": d["uniqueid"], "call_at": d["call_at"],
                             "source_key": d["source_key"]},
                      reason=params.get("reason"))
            out[r.id] = Outcome.succeeded({"evidence_id": eid})
        return out


def _adopt_same_case_rows(ctx, rows) -> None:
    """The framework excludes every later row of an already-seen case as `duplicate_case` (one
    change per case) and replaces its details with {first_row_no}. For evidence each record is
    its own row, so the rows of this chunk's cases are analysed again and become eligible with
    the case revision of their first row (the framework rechecks it at execution). A row whose
    attribution now names another case stays excluded (`attribution_changed`)."""
    if not rows:
        return
    session = ctx.session
    first = {r.row_no: r for r in rows}
    dups = session.execute(text("""
        SELECT id, row_no, mapped, case_id::text AS case_id, (details->>'first_row_no')::int AS first_row_no
          FROM bulk_job_rows
         WHERE job_id = CAST(:j AS uuid) AND eligibility = 'excluded' AND eligibility_code = 'duplicate_case'
           AND (details->>'first_row_no')::int = ANY(CAST(:rn AS int[]))
    """), {"j": str(ctx.job["id"]), "rn": sorted(first)}).mappings().all()
    if not dups:
        return
    res = analyse(session, ctx.scope, ctx.params or {}, [(m["id"], m["mapped"]) for m in dups])
    upd = []
    for m in dups:
        v = res[m["id"]]
        head = first[m["first_row_no"]]
        rec = v["record"]
        if rec is not None and v["case_id"] == m["case_id"] == head.case_id:
            upd.append({"id": m["id"], "e": "eligible", "c": None, "m": None, "d": _details(rec),
                        "rev": head.case["revision"] if head.case else None})
        else:
            d = _details(rec) if rec is not None else {}
            d["first_row_no"] = m["first_row_no"]
            upd.append({"id": m["id"], "e": "excluded", "c": "attribution_changed",
                        "m": "the record no longer attributes to the same case; reopen the job", "d": d, "rev": None})
    session.execute(text("""
        UPDATE bulk_job_rows r SET eligibility = d.e, eligibility_code = d.c, eligibility_message = d.m,
               details = d.d, expected_revision = d.rev
          FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id bigint, e text, c text, m text, d jsonb, rev int)
         WHERE r.id = d.id
    """), {"rows": dumps(upd)})


KINDS = (PbxCdrImportKind(),)
