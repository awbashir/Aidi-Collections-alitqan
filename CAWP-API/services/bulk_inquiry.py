"""Spreadsheet inquiry (Phase 2, stream D; docs/phase2-spec.md, review/phase2-inquiry.md).

A **read-only** bulk kind: the preview is the result. The worker validates the job and it
goes straight to `succeeded` with a formula-safe results CSV (framework). Nothing is
written to a business table.

Mapping (one or more of the match fields; every other column is echoed back as
`source:<column>` in the CSV files and as `raw` in the rows API):

    account_number   exact  clints."accountNumber"
    identity_number  exact  clints."identityNumber"          (a debtor usually has several cases)
    contact_number   Saudi mobile, normalised on both sides (cawp_norm_mobile, 0026/0027),
                     matched against "ClintContacts".contact (not deleted) and resolved to the
                     cases of that contact's identityNumber

Matching rules (row level):

* Values are trimmed and Arabic-Indic digits are folded to ASCII; otherwise account and
  identity are compared **literally** (case-sensitive, no wildcard, no partial match).
* When a row carries several identifiers, `params.match_mode` decides: `all` (default) = a
  case must match every non-blank identifier of the row; `any` = a case matching one of them.
* Only cases inside the job creator's scope (services/clients_scope) are ever considered.
  A row with no match in scope is `error / not_found_or_not_permitted` whether the
  identifier is unknown or belongs to a case outside the scope: the same code, message and
  columns, so an identifier never reveals that a case exists.
* Archived cases are included (in scope only) and flagged `is_archived`.
* **One result row per matched case.** The first match (live cases first, then by account
  number) stays on the source row; each further match k of source row n gets its own row
  numbered 1,000,000,000 + n * 1000 + k (after every sheet row: the framework keys rows by
  row number), with the same source values plus `source_row_no`, `match_no` and
  `match_count` in its details. Sort by (`source_row_no`, `match_no`) for sheet order.
* The same case reached from two source rows: the later row is `excluded / duplicate_case`
  with `first_row_no` (framework rule).
* Row errors that are not about existence: `missing_value` (no identifier on the row),
  `invalid_contact_number` (the value is not a Saudi mobile), `too_many_matches` (more than
  MAX_MATCHES_PER_ROW cases in scope).

Result details (rows API `details`, CSV `detail:<key>`): identity number, owner, portfolio,
contract, archived/locked/active flags, the exact balance (same SQL as
services/money.case_balance: original = round(amount, 2); approved = approved, not deleted,
not reversed; outstanding = original - approved), the last note time
(clints.last_comment_at, which excludes deleted/retired notes) and the open follow-up.

Permission: `client_query`, the key of the legacy inquiry screen (`/client_query`).
"""
from __future__ import annotations

import decimal
import re

from sqlalchemy import text

from core.bulk.common import NOT_FOUND_OR_NOT_PERMITTED, dumps, iso, scope_sql
from core.bulk.registry import BulkKind, Eligibility, MappingField, ParamError
from services.bulk_common import reject_unknown

MAX_MATCHES_PER_ROW = 999
# Row number of the k-th match (k >= 2) of source row n: EXTRA_ROW_BASE + n * 1000 + k.
# Deterministic (restart-safe), after every sheet row (an XLSX sheet has at most 1,048,576
# rows, so the largest value, 2,048,576,999, fits the integer column), grouped by source row.
EXTRA_ROW_BASE = 1_000_000_000
MATCH_MODES = ("all", "any")
FIELDS = ("account_number", "identity_number", "contact_number")

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PHONE_CHARS = re.compile(r"^[0-9+()./ -]+$")
_WS = re.compile(r"[\t\r\n ]")


def fold(value) -> str | None:
    """Trimmed text with Arabic-Indic digits folded to ASCII; None for blank."""
    if value is None:
        return None
    s = str(value).translate(_DIGITS).strip(" \t\r\n ")
    return s or None


def normalize_mobile(value) -> str | None:
    """Python twin of the SQL `cawp_norm_mobile` (db/migrations/0026): `05XXXXXXXX` or None."""
    if value is None:
        return None
    t = _WS.sub(" ", str(value).translate(_DIGITS).strip(" \t\r\n "))
    if not t or not _PHONE_CHARS.match(t):
        return None
    d = re.sub(r"[^0-9]", "", t)
    if re.fullmatch(r"05[0-9]{8}", d):
        return d
    if re.fullmatch(r"5[0-9]{8}", d):
        return "0" + d
    if re.fullmatch(r"(00)?9665[0-9]{8}", d):
        return "0" + d[-9:]
    if re.fullmatch(r"(00)?96605[0-9]{8}", d):
        return d[-10:]
    return None


# --------------------------------------------------------------------------- SQL
_CASE_COLS = 'c.id::text AS id, c."accountNumber" AS account_number, c.is_archive'

_DETAILS_SQL = """
    SELECT c.id::text AS id, c."identityNumber" AS identity_number,
           c.user_id::text AS owner_id, u.user_name AS owner_user_name,
           nullif(btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')), '') AS owner_name,
           p.id::text AS portfolio_id, p.name AS portfolio_name,
           ct.id::text AS contract_id, ct.name AS contract_name,
           c.is_archive, c.is_locked, c.is_active,
           round(c.amount::numeric, 2) AS original,
           coalesce(pay.approved, 0) AS approved,
           coalesce(pay.pending, 0) AS pending,
           c.last_comment_at,
           f.due_at AS follow_up_due_at,
           (f.due_at AT TIME ZONE 'Asia/Riyadh')::date < (now() AT TIME ZONE 'Asia/Riyadh')::date AS follow_up_overdue
      FROM clints c
      LEFT JOIN users u ON u.id = c.user_id
      LEFT JOIN portfolios p ON p.id = c.portfolios_id
      LEFT JOIN contracts ct ON ct.id = p.contract_id
      LEFT JOIN follow_ups f ON f.case_id = c.id AND f.status = 'open'
      LEFT JOIN LATERAL (
           SELECT sum(round(py.amount::numeric, 2)) FILTER (
                      WHERE py.approval_status = true AND py.is_deleted = false AND r.id IS NULL) AS approved,
                  sum(round(py.amount::numeric, 2)) FILTER (
                      WHERE py.approval_status IS NULL AND py.is_deleted = false) AS pending
             FROM "Payments" py
             LEFT JOIN payment_reversals r ON r.payment_id = py.id
            WHERE py.clint_id = c.id) pay ON true
     WHERE c.id = ANY(CAST(:ids AS uuid[]))
"""


_CENT = decimal.Decimal("0.01")


def _money(v) -> str | None:
    return None if v is None else format(v.quantize(_CENT), "f")


class InquiryKind(BulkKind):
    key = "inquiry"
    call_reservation_guard = False   # read-only lookup
    label_en, label_ar = "Spreadsheet inquiry", "استعلام بملف"
    permissions = ("client_query",)
    sources = ("spreadsheet",)
    read_only = True
    mapping_fields = (
        MappingField("account_number", "Account number", "رقم الحساب", match=True, max_length=200),
        MappingField("identity_number", "Identity number", "رقم الهوية", match=True, max_length=200),
        MappingField("contact_number", "Mobile number", "رقم الجوال", match=True, max_length=200,
                     description="Saudi mobile in any common format (05…, 5…, 9665…, +966…)"),
    )
    match_by = None
    result_columns = ("source_row_no", "match_no", "match_count", "matched_by", "identity_number",
                      "owner_user_name", "owner_name", "portfolio_name", "contract_name",
                      "is_archived", "is_locked", "is_active", "original_debt", "approved_total",
                      "pending_total", "outstanding_balance", "last_note_at", "follow_up_due_at",
                      "follow_up_overdue")

    # ------------------------------------------------------------------ params
    def validate_params(self, params, ctx):
        reject_unknown(params, {"match_mode"})
        mode = params.get("match_mode", "all")
        if mode not in MATCH_MODES:
            raise ParamError({"match_mode": "invalid"}, "match_mode must be 'all' or 'any'")
        return {"match_mode": mode}

    # ------------------------------------------------------------------ matching
    def match_rows(self, rows, ctx):
        session = ctx.session
        mode = (ctx.params or {}).get("match_mode", "all")
        out: dict = {}
        wanted: dict[int, dict[str, str]] = {}
        accs, idns, mobs = set(), set(), set()
        for r in rows:
            m = r.mapped or {}
            vals = {}
            acc, idn, mob_raw = fold(m.get("account_number")), fold(m.get("identity_number")), m.get("contact_number")
            if acc:
                vals["account_number"] = acc
            if idn:
                vals["identity_number"] = idn
            if fold(mob_raw):
                mob = normalize_mobile(mob_raw)
                if mob is None:
                    out[r.id] = Eligibility.error("invalid_contact_number",
                                                  "the contact number is not a Saudi mobile number",
                                                  {"field": "contact_number"})
                    continue
                vals["contact_number"] = mob
            if not vals:
                out[r.id] = Eligibility.error("missing_value", "no account, identity or mobile number on this row",
                                              {"fields": list(FIELDS)})
                continue
            wanted[r.id] = vals
            accs.update([vals["account_number"]] if "account_number" in vals else [])
            idns.update([vals["identity_number"]] if "identity_number" in vals else [])
            mobs.update([vals["contact_number"]] if "contact_number" in vals else [])

        pred, sp = scope_sql(session, ctx.scope, "c", prefix="iq")
        info: dict[str, tuple] = {}          # case id -> (is_archive, account number)
        by_acc: dict[str, set] = {}
        by_idn: dict[str, set] = {}
        by_mob: dict[str, set] = {}
        if accs:
            for c in session.execute(text(f"""
                SELECT {_CASE_COLS}, c."accountNumber" AS k FROM clints c
                 WHERE c."accountNumber" = ANY(CAST(:v AS text[])) AND {pred}"""),
                    {"v": sorted(accs), **sp}).mappings():
                info[c["id"]] = (c["is_archive"], c["account_number"])
                by_acc.setdefault(c["k"], set()).add(c["id"])
        if idns:
            for c in session.execute(text(f"""
                SELECT {_CASE_COLS}, c."identityNumber" AS k FROM clints c
                 WHERE c."identityNumber" = ANY(CAST(:v AS text[])) AND {pred}"""),
                    {"v": sorted(idns), **sp}).mappings():
                info[c["id"]] = (c["is_archive"], c["account_number"])
                by_idn.setdefault(c["k"], set()).add(c["id"])
        if mobs:
            _require_normaliser(session)
            # one index probe per value (LATERAL): with `= ANY(:array)` the planner misjudges the
            # expression index and scans all 1.8M contacts (14 s vs 0.12 s per 2,000 on cawp-db)
            for c in session.execute(text(f"""
                SELECT DISTINCT {_CASE_COLS}, m.k
                  FROM unnest(CAST(:v AS text[])) AS m(k)
                 CROSS JOIN LATERAL (
                       SELECT DISTINCT cc."identityNumber" AS idn FROM "ClintContacts" cc
                        WHERE cawp_norm_mobile(cc.contact) = m.k AND cc.is_deleted = false) x
                  JOIN clints c ON c."identityNumber" = x.idn
                 WHERE {pred}"""), {"v": sorted(mobs), **sp}).mappings():
                info[c["id"]] = (c["is_archive"], c["account_number"])
                by_mob.setdefault(c["k"], set()).add(c["id"])

        index = {"account_number": by_acc, "identity_number": by_idn, "contact_number": by_mob}
        meta, extra = [], []
        for r in rows:
            vals = wanted.get(r.id)
            if vals is None:
                continue
            sets = {f: index[f].get(v, set()) for f, v in vals.items()}
            if mode == "any":
                found = set().union(*sets.values())
            else:
                found = set.intersection(*sets.values())
            match_value = vals.get("account_number") or vals.get("identity_number") or vals.get("contact_number")
            if not found:
                meta.append({"id": r.id, "mv": match_value, "d": None})
                continue            # -> not_found_or_not_permitted (framework)
            if len(found) > MAX_MATCHES_PER_ROW:
                out[r.id] = Eligibility.error("too_many_matches",
                                              f"more than {MAX_MATCHES_PER_ROW} cases match this row; "
                                              "narrow it with another identifier",
                                              {"matches": len(found), "max": MAX_MATCHES_PER_ROW})
                meta.append({"id": r.id, "mv": match_value, "d": None})
                continue
            ordered = sorted(found, key=lambda cid: (bool(info[cid][0]), info[cid][1] or "", cid))
            n = len(ordered)
            for i, cid in enumerate(ordered, start=1):
                d = {"source_row_no": r.row_no, "match_no": i, "match_count": n,
                     "matched_by": sorted(f for f, s in sets.items() if cid in s)}
                if i == 1:
                    out[r.id] = cid
                    meta.append({"id": r.id, "mv": match_value, "d": d})
                else:
                    extra.append({"raw": r.raw, "mapped": r.mapped, "mv": match_value, "case_id": cid, "d": d})

        if meta:
            session.execute(text("""
                UPDATE bulk_job_rows r SET match_value = d.mv, details = coalesce(d.d, r.details)
                  FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id bigint, mv text, d jsonb)
                 WHERE r.id = d.id
            """), {"rows": dumps(meta)})
        if extra:
            _append_rows(session, ctx.job, extra)
        return out

    # ------------------------------------------------------------------ preview = result
    def prepare(self, rows, ctx):
        ids = sorted({r.case_id for r in rows})
        facts = {m["id"]: m for m in ctx.session.execute(text(_DETAILS_SQL), {"ids": ids}).mappings()}
        out = {}
        for r in rows:
            f = facts.get(r.case_id)
            if f is None:           # deleted since matching: same answer as any unknown case
                out[r.id] = Eligibility.error(NOT_FOUND_OR_NOT_PERMITTED, "case not found or not in your scope")
                continue
            base = dict(r.details or {})
            base.setdefault("source_row_no", r.row_no)
            base.setdefault("match_no", 1)
            base.setdefault("match_count", 1)
            original, approved = f["original"], f["approved"]
            base.update({
                "identity_number": f["identity_number"],
                "owner_id": f["owner_id"], "owner_user_name": f["owner_user_name"], "owner_name": f["owner_name"],
                "portfolio_id": f["portfolio_id"], "portfolio_name": f["portfolio_name"],
                "contract_id": f["contract_id"], "contract_name": f["contract_name"],
                "is_archived": bool(f["is_archive"]), "is_locked": bool(f["is_locked"]),
                "is_active": bool(f["is_active"]),
                "original_debt": _money(original), "approved_total": _money(approved),
                "pending_total": _money(f["pending"]), "outstanding_balance": _money(original - approved),
                "last_note_at": iso(f["last_comment_at"]),
                "follow_up_due_at": iso(f["follow_up_due_at"]),
                "follow_up_overdue": f["follow_up_overdue"],
            })
            out[r.id] = Eligibility.eligible(base)
        return out


def _require_normaliser(session) -> None:
    from core.bulk.sources import JobFailure
    ok = session.execute(text("""
        SELECT to_regprocedure('cawp_norm_mobile(text)') IS NOT NULL
           AND EXISTS (SELECT 1 FROM pg_index i
                        WHERE i.indexrelid = to_regclass('ix_clintcontacts_mobile_norm') AND i.indisvalid)
    """)).scalar()
    if not ok:      # without the index every mobile would scan all contacts
        raise JobFailure("migration_pending",
                         "mobile matching needs database migrations 0026-0027 "
                         "(cawp_norm_mobile and a valid ix_clintcontacts_mobile_norm)")


def _append_rows(session, job: dict, extra: list[dict]) -> None:
    """Extra matches of a row: new job rows after every sheet row (one per case)."""
    from core.bulk import settings as _settings
    from core.bulk.sources import JobFailure
    job_id = str(job["id"])
    total = session.execute(text("SELECT count(*) FROM bulk_job_rows WHERE job_id = CAST(:j AS uuid)"),
                            {"j": job_id}).scalar()
    limit = _settings.get().max_rows
    if total + len(extra) > limit:
        raise JobFailure("too_many_matches",
                         f"the rows match more than {limit} cases in total; split the file",
                         {"max_rows": limit})
    data = [{"row_no": EXTRA_ROW_BASE + e["d"]["source_row_no"] * 1000 + e["d"]["match_no"],
             "raw": e["raw"], "mapped": e["mapped"], "mv": e["mv"], "case_id": e["case_id"], "d": e["d"]}
            for e in extra]
    session.execute(text("""
        INSERT INTO bulk_job_rows (job_id, row_no, raw, mapped, match_value, case_id, eligibility, details)
        SELECT CAST(:j AS uuid), d.row_no, coalesce(d.raw, '{}'::jsonb), coalesce(d.mapped, '{}'::jsonb),
               d.mv, d.case_id, 'pending', d.d
          FROM jsonb_to_recordset(CAST(:rows AS jsonb))
               AS d(row_no int, raw jsonb, mapped jsonb, mv text, case_id uuid, d jsonb)
    """), {"j": job_id, "rows": dumps(data)})


KINDS = (InquiryKind(),)
