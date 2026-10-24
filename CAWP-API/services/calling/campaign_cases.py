"""Campaign customer lists, hand removals and the called-customers view (customer feedback
2026-10-24, migration 0086). Request/response examples: review/phase5-campaigns.md §13.

"When creating a campaign we need more control, like selecting customers, including customer ids
 from Excel. Before the campaign starts the user can see the list of customers and remove.
 Show in campaigns which customers got called and how many are still in the queue."

* A CUSTOMER-LIST campaign has the pool key `case_list: true` (services/calling/campaign_pool.py):
  its pool is the cases of `campaign_list_cases`, ANDed with any other pool criterion, bounded by
  the creator's scope and every calling rule, like any pool.
* `import_cases`: values from an Excel sheet (.xlsx / .csv, one column, default the first) or
  pasted (JSON `values`), and `match_by` (required, the customer's choice 2026-10-24: one OR the
  other, never both): `identity` = the NATIONAL ID (the identity key of
  services/same_identity.py: every account of that customer is added) or `account` = the
  ACCOUNT NUMBER (that account only). Each value is normalised (Arabic-Indic digits, spaces and
  direction marks removed, upper case) and matched within the EDITOR's current scope. A value that matches nothing visible is
  reported as not found (whether or not it exists outside the scope). A first row without any
  digit is taken as a header. At most MAX_VALUES values per import. Importing a case that was
  removed by hand restores it. The audit keeps the counts, not the values.
* `remove_cases` / `restore_cases`: any campaign (filter or list); a removed case is the pool
  reason `removed_by_user` and is never dialled. At most MAX_IDS per request.
* Every write needs the campaign's expected_revision, refuses a stopped campaign and bumps the
  revision (campaigns.cases_changed_at).
* `called`: the customers this campaign called, newest first: attempts, whether the customer ever
  answered (customer leg answered or bridged), the last call's state / reason / wrap-up outcome /
  agent. Filter `answered=1|0`.
"""
from __future__ import annotations

import csv
import io

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling import campaign_pool as P
from services.calling.common import USER_NAME_SQL, iso, me, sess, user_ref
from services.same_identity import IDENTITY_KEY_SQL

MATCH_BY = ("identity", "account")
MAX_VALUES = 20000
MAX_IDS = 500
NOT_FOUND_SAMPLE = 100
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SPACES = {ord(ch): None for ch in " \t\r\n  　﻿"}
_SPACES.update({cp: None for cp in range(0x2000, 0x2010)})
_SPACES.update({cp: None for cp in range(0x2028, 0x2030)})
_SPACES.update({cp: None for cp in range(0x205f, 0x2070)})


def _err(status, code, message, error=None):
    return ApiError(status, code, message, error)


def normalize(value) -> str:
    """The identity-key normalisation of services/same_identity.IDENTITY_KEY_SQL, in Python."""
    from services.excel import cell_str
    raw = cell_str(value) if not isinstance(value, str) else value
    if raw == "nan":
        return ""
    return raw.translate(_DIGITS).translate(_SPACES).upper()


def values_from_file(storage, column: int = 1) -> list[str]:
    """The values of one column (1-based) of an uploaded .xlsx or .csv. A first row without any
    digit is a header and is dropped."""
    name = (getattr(storage, "filename", "") or "").lower()
    if column < 1 or column > 50:
        raise _err(422, VALIDATION_FAILED, "column must be 1..50", {"column": "invalid"})
    idx = column - 1
    if name.endswith(".csv"):
        data = storage.read().decode("utf-8-sig", errors="replace")
        rows = [r for r in csv.reader(io.StringIO(data))]
        cells = [r[idx] if idx < len(r) else None for r in rows]
    elif name.endswith((".xlsx", ".xlsm")):
        from services.excel import read_sheet
        try:
            headers, rows = read_sheet(storage)
        except Exception:  # noqa: BLE001 - any unreadable workbook is the client's error
            raise _err(422, VALIDATION_FAILED, "the file is not a readable Excel workbook", {"file": "invalid"}) from None
        cells = [headers[idx] if idx < len(headers) else None] + [r[idx] if idx < len(r) else None for r in rows]
    else:
        raise _err(422, VALIDATION_FAILED, "upload an .xlsx or .csv file", {"file": "invalid_type"})
    values = [normalize(c) for c in cells]
    if values and values[0] and not any(ch.isdigit() for ch in values[0]):
        values = values[1:]
    return values


def _clean(values) -> list[str]:
    if not isinstance(values, list):
        raise _err(422, VALIDATION_FAILED, "values must be a list of national ids or account numbers",
                   {"values": "invalid"})
    out, seen = [], set()
    for v in values:
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            raise _err(422, VALIDATION_FAILED, "values must be text or numbers", {"values": "invalid"})
        n = normalize(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    if not out:
        raise _err(422, VALIDATION_FAILED, "no national id or account number was given", {"values": "required"})
    if len(out) > MAX_VALUES:
        raise _err(422, VALIDATION_FAILED, f"at most {MAX_VALUES} values per import", {"values": "too_many",
                                                                                      "count": len(out)})
    return out


def _editable(campaign_id, expected) -> dict:
    from services.calling.campaign import _check_revision, load
    camp = load(campaign_id, lock="update", manage=True)
    _check_revision(camp, expected)
    if camp["status"] == "stopped":
        raise _err(409, "campaign_stopped", "a stopped campaign cannot be changed", {"status": camp["status"]})
    return camp


def _touch(campaign_id) -> int:
    return int(sess().execute(text("""
        UPDATE campaigns SET cases_changed_at = clock_timestamp() WHERE id = CAST(:c AS uuid) RETURNING revision"""),
        {"c": str(campaign_id)}).scalar())


def import_cases(campaign_id, *, values, source: str, match_by, expected) -> dict:
    if match_by not in MATCH_BY:
        raise _err(422, VALIDATION_FAILED, "match_by must be identity (national id) or account (account number)",
                   {"match_by": "required" if match_by in (None, "") else "invalid"})
    camp = _editable(campaign_id, expected)
    if not (camp["pool"] or {}).get(P.CASE_LIST_KEY):
        raise _err(409, "not_a_list_campaign", "this campaign selects its customers by filter, not by a list",
                   {"pool": "case_list"})
    vals = _clean(values)
    scope_sql, scope_params = P.scope_clause(me(), "ed")
    column = (IDENTITY_KEY_SQL.format(col='c."identityNumber"') if match_by == "identity"
              else IDENTITY_KEY_SQL.format(col='c."accountNumber"'))
    rows = sess().execute(text(f"""
        WITH v AS (SELECT unnest(CAST(:vals AS text[])) AS val)
        SELECT DISTINCT v.val, c.id::text AS case_id
          FROM v JOIN clints c ON {column} = v.val
          JOIN portfolios p ON p.id = c.portfolios_id
         WHERE {scope_sql}"""), {"vals": vals, **scope_params}).mappings().all()
    matched_vals = {r["val"] for r in rows}
    by_case = {r["case_id"]: match_by for r in rows}
    added = restored = 0
    if by_case:
        ids = sorted(by_case)
        added = sess().execute(text("""
            INSERT INTO campaign_list_cases (campaign_id, case_id, source, matched_by, added_by)
            SELECT CAST(:c AS uuid), x.case_id, :src, x.how, CAST(:by AS uuid)
              FROM unnest(CAST(:ids AS uuid[]), CAST(:hows AS text[])) AS x(case_id, how)
            ON CONFLICT (campaign_id, case_id) DO NOTHING"""),
            {"c": str(campaign_id), "src": source, "by": me(), "ids": ids,
             "hows": [by_case[i] for i in ids]}).rowcount
        restored = sess().execute(text("""
            DELETE FROM campaign_removed_cases WHERE campaign_id = CAST(:c AS uuid) AND case_id = ANY(CAST(:ids AS uuid[]))"""),
            {"c": str(campaign_id), "ids": ids}).rowcount
    not_found = [v for v in vals if v not in matched_vals]
    out = {"match_by": match_by, "values": len(vals), "matched_values": len(matched_vals), "not_found_count": len(not_found),
           "not_found": not_found[:NOT_FOUND_SAMPLE], "cases_matched": len(by_case), "cases_added": int(added),
           "cases_restored": int(restored)}
    out["revision"] = _touch(campaign_id) if (added or restored) else int(camp["revision"])
    audit.record("campaign.cases.import", "campaign", str(campaign_id),
                 after={k: out[k] for k in ("values", "matched_values", "not_found_count", "cases_matched",
                                            "cases_added", "cases_restored")} | {"source": source,
                                                                                  "match_by": match_by})
    return out


def _case_ids(body) -> list[str]:
    from services.calling.common import valid_uuid
    raw = body.get("case_ids")
    if not isinstance(raw, list) or not raw:
        raise _err(422, VALIDATION_FAILED, "case_ids is required", {"case_ids": "required"})
    if len(raw) > MAX_IDS:
        raise _err(422, VALIDATION_FAILED, f"at most {MAX_IDS} cases per request", {"case_ids": "too_many"})
    ids = [valid_uuid(v) for v in raw]
    if any(i is None for i in ids):
        raise _err(422, VALIDATION_FAILED, "case_ids contains an invalid id", {"case_ids": "invalid"})
    return sorted(set(ids))


def remove_cases(campaign_id, body: dict, expected) -> dict:
    from services.calling.campaign import _reason, _unknown
    _unknown(body, {"expected_revision", "case_ids", "reason"})
    reason = _reason(body)
    camp = _editable(campaign_id, expected)
    ids = _case_ids(body)
    known = [r[0] for r in sess().execute(text("SELECT id::text FROM clints WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                          {"ids": ids}).all()]
    removed = sess().execute(text("""
        INSERT INTO campaign_removed_cases (campaign_id, case_id, removed_by, reason)
        SELECT CAST(:c AS uuid), x, CAST(:by AS uuid), :why FROM unnest(CAST(:ids AS uuid[])) AS x
        ON CONFLICT (campaign_id, case_id) DO NOTHING RETURNING case_id::text"""),
        {"c": str(campaign_id), "by": me(), "why": reason, "ids": known}).scalars().all() if known else []
    rev = _touch(campaign_id) if removed else int(camp["revision"])
    if removed:
        audit.record("campaign.cases.remove", "campaign", str(campaign_id), after={"case_ids": sorted(removed)},
                     reason=reason)
    return {"removed": sorted(removed), "revision": rev}


def restore_cases(campaign_id, body: dict, expected) -> dict:
    from services.calling.campaign import _reason, _unknown
    _unknown(body, {"expected_revision", "case_ids", "reason"})
    reason = _reason(body)
    camp = _editable(campaign_id, expected)
    ids = _case_ids(body)
    restored = sess().execute(text("""
        DELETE FROM campaign_removed_cases WHERE campaign_id = CAST(:c AS uuid) AND case_id = ANY(CAST(:ids AS uuid[]))
        RETURNING case_id::text"""), {"c": str(campaign_id), "ids": ids}).scalars().all()
    rev = _touch(campaign_id) if restored else int(camp["revision"])
    if restored:
        audit.record("campaign.cases.restore", "campaign", str(campaign_id), after={"case_ids": sorted(restored)},
                     reason=reason)
    return {"restored": sorted(restored), "revision": rev}


def list_counts(campaign_id) -> dict:
    """{"listed", "removed"}: the campaign's customer list size and its hand removals."""
    r = sess().execute(text("""
        SELECT (SELECT count(*) FROM campaign_list_cases WHERE campaign_id = CAST(:c AS uuid)) AS listed,
               (SELECT count(*) FROM campaign_removed_cases WHERE campaign_id = CAST(:c AS uuid)) AS removed"""),
        {"c": str(campaign_id)}).mappings().first()
    return {"listed": int(r["listed"]), "removed": int(r["removed"])}


# --------------------------------------------------------------------------- called customers
_REACHED = "(r.release_source IS NULL OR r.release_source NOT IN ('no_customer_contact', 'cancel_unsent'))"


def called_counts(campaign_id) -> dict:
    """{"called": customers (cases) dialled by this campaign, "answered": of them, ever answered}."""
    r = sess().execute(text(f"""
        SELECT count(DISTINCT ca.case_id) AS called,
               count(DISTINCT ca.case_id) FILTER (WHERE {P.ANSWERED_SQL}) AS answered
          FROM campaign_attempts ca
          JOIN call_attempts a ON a.id = ca.attempt_id
          LEFT JOIN call_reservations r ON r.attempt_id = a.id
         WHERE ca.campaign_id = CAST(:c AS uuid) AND {_REACHED}"""), {"c": str(campaign_id)}).mappings().first()
    return {"called": int(r["called"]), "answered": int(r["answered"])}


def called(campaign_id, args, page, size, offset) -> dict:
    from services.calling.campaign import load
    load(campaign_id, manage=True)
    answered = args.get("answered")
    if answered not in (None, "", "0", "1"):
        raise _err(422, VALIDATION_FAILED, "answered must be 0 or 1", {"answered": "invalid"})
    having = {"1": "HAVING bool_or(x.answered)", "0": "HAVING NOT bool_or(x.answered)"}.get(answered or "", "")
    base = f"""
        SELECT ca.case_id, ca.created_at, ca.user_id, a.state, a.reason_code, w.outcome,
               {P.ANSWERED_SQL} AS answered
          FROM campaign_attempts ca
          JOIN call_attempts a ON a.id = ca.attempt_id
          LEFT JOIN call_reservations r ON r.attempt_id = a.id
          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
         WHERE ca.campaign_id = CAST(:c AS uuid) AND {_REACHED}"""
    grouped = f"""
        SELECT x.case_id, count(*) AS attempts, bool_or(x.answered) AS answered, max(x.created_at) AS last_at,
               (array_agg(x.state ORDER BY x.created_at DESC))[1] AS last_state,
               (array_agg(x.reason_code ORDER BY x.created_at DESC))[1] AS last_reason,
               (array_agg(x.outcome ORDER BY x.created_at DESC))[1] AS last_outcome,
               (array_agg(x.user_id ORDER BY x.created_at DESC))[1] AS last_agent
          FROM ({base}) x GROUP BY x.case_id {having}"""
    params = {"c": str(campaign_id)}
    total = sess().execute(text(f"SELECT count(*) FROM ({grouped}) g"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT g.*, c.name, c."accountNumber" AS account_number, {USER_NAME_SQL.format(a='u')} AS agent_name
          FROM ({grouped}) g
          LEFT JOIN clints c ON c.id = g.case_id
          LEFT JOIN users u ON u.id = g.last_agent
         ORDER BY g.last_at DESC, g.case_id
         LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    items = [{"case": {"id": str(r["case_id"]), "name": r["name"], "account_number": r["account_number"]},
              "attempts": int(r["attempts"]), "answered": bool(r["answered"]), "last_call_at": iso(r["last_at"]),
              "last_state": r["last_state"], "last_reason": r["last_reason"], "last_outcome": r["last_outcome"],
              "last_agent": user_ref(r["last_agent"], r["agent_name"])} for r in rows]
    return {"items": items, "total": int(total), "page": page, "page_size": size,
            "counts": called_counts(campaign_id)}
