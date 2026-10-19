"""Campaign case pool (Phase 5 P2): the pool definition, its SQL, candidate order, exclusions.

A campaign's pool is a DEFINITION, evaluated at every dispatch (never a frozen list):

    {"contract_ids": [...], "portfolio_ids": [...],
     "filter": {<a saved clients-list filter: the query parameters of GET /clints>}}

At least one criterion is required. The filter keys are the clients list's own parameter names
(services/clients_query.ListParams), so the frontend can save the list's current filter as it is:

    customeFilterContractValue[]    customeFilterPortfoliosValue[]   customeFilterUserValue[]
    customeFilterCommentStatusValue[] (or customeFilterStatusesValue)  customeFilterBranchesValue[]
    customeFilterCommentSubStatusValue[] (the latest note's sub-status, 0068)
    customeFilterAttributeValue     customeFilterPortfolioCreateAt{,From,To}Value
    filterField / filterOperator / filterFieldType / filterValue / filterValue[]   (the grid filter)

A note status filter is the case's LATEST note (clints.last_comment_id): its main status and its
sub-status, ANDed when both are given, like the clients list. On create/update the statuses are
checked against the campaign's contracts within the editor's scope (check_pool_statuses).

They mean exactly what the clients list means (tested for parity against build_clients_query),
except the spreadsheet filter (a temporary upload) which a campaign cannot keep, and grid
filters the list itself cannot evaluate (a number operator on a text column, ...): both 422.
Archived cases stay in the pool but are excluded (`case_archived`), so the supervisor sees them.

The pool is bounded by the scope of the campaign's creator (evaluated now, not at creation), and
a case is only ever offered to a member whose own scope contains it (the click-to-call rule).

Dispatch order for a member (deterministic):
    1. priority: `callback_due` (an open follow-up that is callable now: due, late, or within the
       15-minute grace) before `normal`;
    2. oldest last contact first: greatest(last note, last time this campaign tried the case);
       never contacted first (any recent outbound call is covered by the cooldown);
    3. case id.

The SQL pre-filter here only avoids offering cases that would be refused; the dispatch rules
(services/calling/rules.evaluate) are always the authority and run on the chosen case.
"""
from __future__ import annotations

import datetime as _dt
import re
import uuid

from sqlalchemy import text

from core.api_response import VALIDATION_FAILED, ApiError
from services.calling.common import e164_from_contact, sess

CALLBACK_GRACE_MINUTES = 15          # = rules.CALLBACK_GRACE
MAX_LIST = 500

UUID_LIST_KEYS = {
    "customeFilterContractValue[]": "contracts",
    "customeFilterPortfoliosValue[]": "portfolios",
    "customeFilterUserValue[]": "users",
    "customeFilterCommentStatusValue[]": "comment_statuses",
    "customeFilterStatusesValue": "comment_statuses_alt",
    "customeFilterCommentSubStatusValue[]": "comment_sub_statuses",
    "customeFilterBranchesValue[]": "branches",
}
DATE_KEYS = ("customeFilterPortfolioCreateAtValue", "customeFilterPortfolioCreateAtFromValue",
             "customeFilterPortfolioCreateAtToValue")
GRID_KEYS = ("filterField", "filterOperator", "filterFieldType", "filterValue", "filterValue[]")
REFUSED_KEYS = ("customeFilterInquiryClintByExcelValue[file_name]", "customeFilterInquiryClintByExcelValue[index]")
# paging / sorting keys of the list are accepted and ignored (the campaign has its own order)
IGNORED_KEYS = ("page", "pageSize", "field", "sort", "sort[key]", "sort[order]", "customeFilterDateValue[]",
                # the list's archive-state filter: a campaign never calls archived cases (the
                # hard pool reason `case_archived`), so it is accepted and has no further effect
                "customeFilterArchivingStatusesValue", "customeFilterArchivingStatusesValue[]")

# grid columns: SQL expression and kind (clients mode of services/clients_query.apply_mui_filter)
GRID_COLUMNS = {
    "amount": ("c.amount", "number"),
    "name": ("c.name", "text"),
    "accountNumber": ('c."accountNumber"', "text"),
    "identityNumber": ('c."identityNumber"', "text"),
    "portfolios.name": ("p.name", "text"),
    "contract.name": ("k.name", "text"),
    "comment.create_at": ("c.last_comment_at", "time"),
}
NUMBER_OPS = {"=": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
STRING_OPS = ("contains", "equals", "startsWith", "endsWith")

# The SQL twin of common.e164_from_contact (used only to pre-filter and to count; the chosen
# contact is always validated by the Python function and then by the dispatch rules).
CALLABLE_SQL = ("regexp_replace(regexp_replace(regexp_replace(btrim({col}), '[[:space:]().-]', '', 'g'), "
                "'^00', ''), '^[+]+', '') ~ '^(0?5[0-9]{{8}}|[1-9][0-9]{{7,14}})$'")
CONTACT_ELIGIBLE = ("NOT {k}.is_deleted AND NOT {k}.is_blocked AND {k}.is_active AND NOT {k}.is_archive")

# pool classification, in reporting order (the first matching reason wins)
REASONS = ("case_archived", "case_locked", "do_not_call", "calling_review_pending", "in_call",
           "no_member_in_scope", "case_paid", "no_callable_contact", "callback_scheduled",
           "max_attempts_today", "cooldown", "recheck_later", "eligible")
# exclusions that do not go away by themselves today (the rest may: a callback becomes due,
# the cooldown passes, a call ends, a recheck time passes)
HARD_REASONS = ("case_archived", "case_locked", "do_not_call", "calling_review_pending", "no_member_in_scope",
                "case_paid", "no_callable_contact", "max_attempts_today")


def _err(field, message, code="invalid"):
    return ApiError(422, VALIDATION_FAILED, message, {field: code})


def _uuid_list(value, field) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise _err(field, f"{field} must be a list of at most {MAX_LIST} ids")
    out = []
    for v in value:
        if v in ("", None):
            continue
        try:
            out.append(str(uuid.UUID(str(v))))
        except (ValueError, TypeError, AttributeError):
            raise _err(field, f"{field} contains an invalid id") from None
    return sorted(set(out))


def _date(value, field):
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise _err(field, f"{field} must be a date (YYYY-MM-DD)") from None


def validate_pool(pool) -> dict:
    """-> the normalized pool definition, or 422. Unknown keys are refused (a typo must not
    silently widen the pool)."""
    if not isinstance(pool, dict):
        raise _err("pool", "pool must be an object with contract_ids, portfolio_ids and/or filter")
    unknown = set(pool) - {"contract_ids", "portfolio_ids", "filter"}
    if unknown:
        raise _err("pool", f"unknown pool keys: {', '.join(sorted(unknown))}", "unknown")
    out = {"contract_ids": _uuid_list(pool.get("contract_ids") or [], "pool.contract_ids"),
           "portfolio_ids": _uuid_list(pool.get("portfolio_ids") or [], "pool.portfolio_ids"),
           "filter": {}}
    flt = pool.get("filter") or {}
    if not isinstance(flt, dict):
        raise _err("pool.filter", "pool.filter must be an object of clients-list filter parameters")
    for k, v in flt.items():
        if k in REFUSED_KEYS:
            raise _err(f"pool.filter.{k}", "a spreadsheet filter is a temporary upload; a campaign cannot keep it",
                       "not_supported")
        if k in IGNORED_KEYS:
            continue
        if k in UUID_LIST_KEYS:
            if k in ("customeFilterCommentStatusValue[]", "customeFilterStatusesValue"):
                v = [x for x in ([v] if isinstance(v, str) else (v or [])) if x not in ("", "0")]  # as ListParams
            ids = _uuid_list(v, f"pool.filter.{k}")
            if ids:
                out["filter"][k] = ids
        elif k == "customeFilterAttributeValue":
            if v not in (None, ""):
                if not isinstance(v, str) or len(v) > 500:
                    raise _err(f"pool.filter.{k}", "the attribute filter must be text (at most 500 characters)")
                out["filter"][k] = v
        elif k in DATE_KEYS:
            d = _date(v, f"pool.filter.{k}")
            if d:
                out["filter"][k] = d
        elif k in GRID_KEYS:
            if v not in (None, "", []):
                out["filter"][k] = v
        else:
            raise _err(f"pool.filter.{k}", f"unknown clients-list filter parameter {k!r}", "unknown")
    _validate_grid(out["filter"])
    f = out["filter"]
    if not (out["contract_ids"] or out["portfolio_ids"] or f):
        raise _err("pool", "the pool needs at least one criterion (contracts, portfolios or a filter)", "required")
    return out


def _grid(f: dict):
    """-> (column sql, kind, operator, field type, value, values) or None (as ListParams)."""
    op, field = f.get("filterOperator"), f.get("filterField")
    if not (op and field):
        return None
    values = f.get("filterValue[]")
    if isinstance(values, str):
        values = [values]
    return field, op, f.get("filterFieldType"), f.get("filterValue"), [v for v in (values or []) if v != ""]


def _validate_grid(f: dict):
    g = _grid(f)
    if g is None:
        for k in GRID_KEYS:
            f.pop(k, None)
        return
    field, op, ftype, value, values = g
    if field not in GRID_COLUMNS:
        raise _err("pool.filter.filterField", f"the grid filter on {field!r} is not supported for campaigns",
                   "not_supported")
    _col, kind = GRID_COLUMNS[field]
    if op in ("isEmpty", "isNotEmpty"):
        return
    if value is not None and not isinstance(value, (str, int, float)):
        raise _err("pool.filter.filterValue", "filterValue must be text or a number")
    if value is not None:
        if ftype == "number":
            if kind != "number" or op not in NUMBER_OPS:
                raise _err("pool.filter.filterOperator", "this number filter is not supported for campaigns",
                           "not_supported")
            try:
                float(value)
            except (TypeError, ValueError):
                raise _err("pool.filter.filterValue", "filterValue must be a number") from None
        elif ftype == "string":
            if kind != "text" or op not in STRING_OPS:
                raise _err("pool.filter.filterOperator", "this text filter is not supported for campaigns",
                           "not_supported")
    if values and op == "isAnyOf":
        if not isinstance(f.get("filterValue[]"), (list, str)) or len(values) > MAX_LIST:
            raise _err("pool.filter.filterValue[]", "filterValue[] must be a list")
        if ftype == "number":
            if kind != "number":
                raise _err("pool.filter.filterFieldType", "isAnyOf number needs a number column", "not_supported")
            try:
                [float(v) for v in values]
            except (TypeError, ValueError):
                raise _err("pool.filter.filterValue[]", "filterValue[] must be numbers") from None
        elif kind != "text":
            raise _err("pool.filter.filterField", "isAnyOf needs a text column", "not_supported")


# --------------------------------------------------------------------------- note statuses (2026-10-19)
STATUS_KEYS = ("customeFilterCommentStatusValue[]", "customeFilterStatusesValue")
SUB_STATUS_KEY = "customeFilterCommentSubStatusValue[]"


def pool_contract_ids(pool: dict) -> set[str] | None:
    """The contracts the pool can reach by its own definition: the intersection of its contract
    and portfolio criteria (a portfolio stands for its contract). None = it names none."""
    f = pool.get("filter") or {}
    sets = [set(ids) for ids in (pool.get("contract_ids"), f.get("customeFilterContractValue[]")) if ids]
    for ids in (pool.get("portfolio_ids"), f.get("customeFilterPortfoliosValue[]")):
        if ids:
            sets.append({r[0] for r in sess().execute(text(
                "SELECT DISTINCT contract_id::text FROM portfolios WHERE id = ANY(CAST(:ids AS uuid[])) "
                "AND contract_id IS NOT NULL"), {"ids": list(ids)}).all()})
    return set.intersection(*sets) if sets else None


def scope_contract_ids(user_id) -> list[str] | None:
    """Contracts with at least one case in `user_id`'s current scope (scope_clause), sorted.
    None = every contract (SuperAdmin)."""
    sql, params = scope_clause(user_id, "sc")
    if sql == "TRUE":
        return None
    return [r[0] for r in sess().execute(text(f"""
        SELECT k.id::text FROM contracts k
         WHERE EXISTS (SELECT 1 FROM clints c JOIN portfolios p ON p.id = c.portfolios_id
                        WHERE p.contract_id = k.id AND {sql})
         ORDER BY k.id"""), params).all()]


def status_contracts(pool: dict, user_id) -> set[str] | None:
    """The contracts whose own note statuses the pool may filter on: the pool's contracts within
    `user_id`'s scope (None = any contract). General statuses (contract NULL) always fit."""
    own, scope = pool_contract_ids(pool), scope_contract_ids(user_id)
    if scope is None:
        return own
    return set(scope) if own is None else own & set(scope)


def check_pool_statuses(pool: dict, user_id, keep=()) -> None:
    """422 unless every note status of the (normalized) pool filter can be chosen by `user_id`:
    an existing, live status; a MAIN status under the status keys and a sub-status under the
    sub-status key; general or of a contract in status_contracts(); and, when main statuses
    are chosen too, each sub-status under one of them (the two filters are ANDed, exactly as
    the clients list ANDs them, so another parent's sub-status would only empty the pool).

    `keep`: ids the campaign already had (an edit keeps a status deleted since, or one of a
    contract that left the scope; the pool keeps evaluating it as it did)."""
    f = pool.get("filter") or {}
    mains = {x for k in STATUS_KEYS for x in (f.get(k) or [])}
    subs = set(f.get(SUB_STATUS_KEY) or [])
    if not (mains or subs):
        return
    rows = {r[0]: r for r in sess().execute(text("""
        SELECT id::text, contract_id::text, parent_id::text, deleted_at IS NOT NULL FROM "CommentStatuses"
         WHERE id = ANY(CAST(:ids AS uuid[]))"""), {"ids": sorted(mains | subs)}).all()}
    keep = {str(x) for x in keep}
    allowed = None
    for key in STATUS_KEYS + (SUB_STATUS_KEY,):
        field = f"pool.filter.{key}"
        for sid in f.get(key) or []:
            if sid in keep:
                continue
            row = rows.get(sid)
            if row is None:
                raise _err(field, "unknown note status", "unknown")
            _id, contract, parent, deleted = row
            if deleted:
                raise _err(field, "this note status was deleted", "status_deleted")
            if key == SUB_STATUS_KEY and parent is None:
                raise _err(field, "a main status is not a sub-status", "not_a_sub_status")
            if key != SUB_STATUS_KEY and parent is not None:
                raise _err(field, "a sub-status is not a main status; choose its main status", "is_sub_status")
            if key == SUB_STATUS_KEY and mains and parent not in mains:
                raise _err(field, "the sub-status does not belong to a chosen main status", "not_a_sub_status")
            if contract is not None:
                if allowed is None:
                    allowed = (status_contracts(pool, user_id),)
                if allowed[0] is not None and contract not in allowed[0]:
                    raise _err(field, "the note status belongs to a contract outside this campaign's pool or "
                                      "your scope", "status_not_for_contract")


def statuses_of(pool) -> set[str]:
    f = (pool or {}).get("filter") or {}
    return {str(x) for k in STATUS_KEYS + (SUB_STATUS_KEY,) for x in (f.get(k) or [])}


# --------------------------------------------------------------------------- SQL
def definition_sql(pool: dict, prefix: str = "pd") -> tuple[str, str, dict]:
    """(extra joins, WHERE fragment over c (clints), p (portfolios), k (contracts), params)."""
    joins, where, params = [], [], {}
    f = pool.get("filter") or {}

    def ids(key, values):
        params[f"{prefix}_{key}"] = list(values)
        return f"CAST(:{prefix}_{key} AS uuid[])"

    if pool.get("contract_ids"):
        where.append(f"p.contract_id = ANY({ids('contracts', pool['contract_ids'])})")
    if pool.get("portfolio_ids"):
        where.append(f"c.portfolios_id = ANY({ids('portfolios', pool['portfolio_ids'])})")
    if f.get("customeFilterContractValue[]"):
        where.append(f"p.contract_id = ANY({ids('f_contracts', f['customeFilterContractValue[]'])})")
    if f.get("customeFilterUserValue[]"):
        where.append(f"c.user_id = ANY({ids('f_users', f['customeFilterUserValue[]'])})")
    statuses = [v for v in (f.get("customeFilterCommentStatusValue[]") or f.get("customeFilterStatusesValue") or [])
                if v and v != "0"]
    subs = f.get("customeFilterCommentSubStatusValue[]") or []
    if statuses or subs:
        joins.append('LEFT JOIN "Comments" lc ON lc.id = c.last_comment_id')
    if statuses:
        where.append(f"lc.comment_status_id = ANY({ids('f_statuses', statuses)})")
    if subs:
        where.append(f"lc.comment_sub_status_id = ANY({ids('f_sub_statuses', subs)})")
    if f.get("customeFilterPortfoliosValue[]"):
        where.append(f"c.portfolios_id = ANY({ids('f_portfolios', f['customeFilterPortfoliosValue[]'])})")
    if f.get("customeFilterBranchesValue[]"):
        joins.append("LEFT JOIN users pu ON pu.id = c.user_id")
        where.append(f"pu.branch_id = ANY({ids('f_branches', f['customeFilterBranchesValue[]'])})")
    if f.get("customeFilterAttributeValue"):
        params[f"{prefix}_attr"] = f["customeFilterAttributeValue"]
        # the list's cast(attribute, String).contains(v): LIKE, so % and _ in v are wildcards there too
        where.append(f"CAST(c.attribute AS varchar) LIKE '%' || :{prefix}_attr || '%'")
    single = f.get("customeFilterPortfolioCreateAtValue")
    frm = f.get("customeFilterPortfolioCreateAtFromValue") or single
    to = f.get("customeFilterPortfolioCreateAtToValue") or single
    if frm and to and frm > to:
        frm, to = to, frm
    if frm:
        params[f"{prefix}_pfrom"] = frm
        where.append(f"date(p.create_at) >= CAST(:{prefix}_pfrom AS date)")
    if to:
        params[f"{prefix}_pto"] = to
        where.append(f"date(p.create_at) <= CAST(:{prefix}_pto AS date)")
    g = _grid(f)
    if g is not None:
        field, op, ftype, value, values = g
        col, kind = GRID_COLUMNS[field]
        if op == "isEmpty":
            where.append(f"{col} IS NULL")
        elif op == "isNotEmpty":
            where.append(f"{col} IS NOT NULL")
        elif value is not None:
            params[f"{prefix}_gv"] = value
            if ftype == "number" and op in NUMBER_OPS:
                params[f"{prefix}_gv"] = float(value)
                where.append(f"{col} {NUMBER_OPS[op]} :{prefix}_gv")
            elif ftype == "string" and op in STRING_OPS:
                params[f"{prefix}_gv"] = str(value)
                where.append({
                    "contains": f"{col} ILIKE '%' || :{prefix}_gv || '%'",
                    "equals": f"{col} = :{prefix}_gv",
                    "startsWith": f"{col} ILIKE :{prefix}_gv || '%'",
                    "endsWith": f"{col} ILIKE '%' || :{prefix}_gv",
                }[op])
        if values and op == "isAnyOf":
            if ftype == "number":
                params[f"{prefix}_gvs"] = [float(v) for v in values]
                where.append(f"{col} = ANY(CAST(:{prefix}_gvs AS double precision[]))")
            else:
                params[f"{prefix}_gvs"] = [str(v) for v in values]
                where.append(f"{col} = ANY(CAST(:{prefix}_gvs AS text[]))")
    return " ".join(joins), " AND ".join(where) or "TRUE", params


def scope_clause(user_id, prefix: str) -> tuple[str, dict]:
    """(SQL boolean over c/p, params): `user_id`'s current case scope, uncached (the same rules
    as rules.case_in_scope_of)."""
    from services.clients_scope import COLLECTOR, SUPER, _load_scope
    scope = _load_scope(str(user_id))
    if scope["kind"] == SUPER:
        return "TRUE", {}
    users = scope.get("user_ids") or []
    contracts = scope.get("contract_ids") or []
    if scope["kind"] == COLLECTOR or (not users and not contracts):
        return f"c.user_id = CAST(:{prefix}_me AS uuid)", {f"{prefix}_me": str(user_id)}
    parts, params = [], {}
    if users:
        parts.append(f"c.user_id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(set(users))
    if contracts:
        parts.append(f"p.contract_id = ANY(CAST(:{prefix}_cids AS uuid[]))")
        params[f"{prefix}_cids"] = sorted(set(contracts))
    return "(" + " OR ".join(parts) + ")", params


def union_scope_clause(user_ids, prefix: str = "ms") -> tuple[str, dict]:
    """Union of several users' scopes (a case some active member could be offered)."""
    from services.clients_scope import COLLECTOR, SUPER, _load_scope
    owners, uids, cids = set(), set(), set()
    for u in user_ids:
        scope = _load_scope(str(u))
        if scope["kind"] == SUPER:
            return "TRUE", {}
        users = scope.get("user_ids") or []
        contracts = scope.get("contract_ids") or []
        if scope["kind"] == COLLECTOR or (not users and not contracts):
            owners.add(str(u))
        else:
            uids |= set(users)
            cids |= set(contracts)
    uids |= owners
    parts, params = [], {}
    if uids:
        parts.append(f"c.user_id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(uids)
    if cids:
        parts.append(f"p.contract_id = ANY(CAST(:{prefix}_cids AS uuid[]))")
        params[f"{prefix}_cids"] = sorted(cids)
    if not parts:
        return "FALSE", {}
    return "(" + " OR ".join(parts) + ")", params


def _pool_cte(campaign: dict, member_scope: tuple[str, dict] | None, extra_select: str = "") -> tuple[str, dict]:
    joins, where, params = definition_sql(campaign["pool"])
    own_sql, own_params = scope_clause(campaign["created_by"], "os")
    params.update(own_params)
    extra = ""
    if member_scope is not None:
        extra = f" AND {member_scope[0]}"
        params.update(member_scope[1])
    return (f"""
        SELECT c.id, c."identityNumber" AS idn, c.is_archive, c.is_locked, c.last_comment_at, c.amount,
               c.name, c."accountNumber" AS account_number, c.user_id{extra_select}
          FROM clints c
          JOIN portfolios p ON p.id = c.portfolios_id
          JOIN contracts k ON k.id = p.contract_id
          {joins}
         WHERE {where} AND {own_sql}{extra}"""), params


def _clock(now=None) -> dict:
    """Database-time anchors: now, the Riyadh civil day start."""
    if now is None:
        now = sess().execute(text("SELECT clock_timestamp()")).scalar()
    day_start = sess().execute(text(
        "SELECT (date_trunc('day', CAST(:n AS timestamptz) AT TIME ZONE 'Asia/Riyadh')) AT TIME ZONE 'Asia/Riyadh'"),
        {"n": now}).scalar()
    return {"now": now, "day_start": day_start}


_FACTS = """
    LEFT JOIN campaign_pool_state ps ON ps.campaign_id = CAST(:campaign AS uuid) AND ps.case_id = pool.id
    LEFT JOIN LATERAL (SELECT f.due_at FROM follow_ups f
                        WHERE f.case_id = pool.id AND f.status = 'open' ORDER BY f.due_at LIMIT 1) fu ON true
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE a.created_at >= :day_start) AS today, max(a.created_at) AS last_at
          FROM call_attempts a
          LEFT JOIN call_reservations r ON r.attempt_id = a.id
         WHERE a.case_id = pool.id AND a.direction = 'outbound' AND a.created_at >= :since
           AND (r.release_source IS NULL OR r.release_source NOT IN ('no_customer_contact', 'cancel_unsent'))
    ) att ON true"""

_BALANCE = """
    LEFT JOIN LATERAL (
        SELECT coalesce(sum(round(CAST(pay.amount AS numeric), 2)) FILTER (
                   WHERE pay.approval_status = true AND pay.is_deleted = false AND rv.id IS NULL), 0) AS approved
          FROM "Payments" pay LEFT JOIN payment_reversals rv ON rv.payment_id = pay.id
         WHERE pay.clint_id = pool.id) bal ON true"""

_DNC = """EXISTS (SELECT 1 FROM calling_blocks b WHERE b.case_id = pool.id AND b.kind = 'do_not_call'
                     AND b.released_at IS NULL)"""
_REVIEW = """EXISTS (SELECT 1 FROM call_review_flags rf WHERE rf.case_id = pool.id AND rf.blocks_calling
                        AND rf.resolved_at IS NULL)"""
_RESERVED = "EXISTS (SELECT 1 FROM call_reservations x WHERE x.case_id = pool.id AND x.released_at IS NULL)"
_CONTACT = ("EXISTS (SELECT 1 FROM \"ClintContacts\" k2 WHERE k2.\"identityNumber\" = pool.idn AND "
            + CONTACT_ELIGIBLE.format(k="k2") + " AND " + CALLABLE_SQL.format(col="k2.contact") + ")")
_FUTURE_CALLBACK = "(fu.due_at IS NOT NULL AND fu.due_at - make_interval(mins => :grace) > :now)"
_MAXED = "(coalesce(att.today, 0) >= :max_per_day)"
_COOLING = "(att.last_at IS NOT NULL AND att.last_at > :now - make_interval(mins => :cooldown))"
_RECHECK = "(ps.recheck_after IS NOT NULL AND ps.recheck_after > :now)"
_PAID = "(round(CAST(pool.amount AS numeric), 2) - bal.approved <= 0)"

_LAST_CONTACT = "greatest(pool.last_comment_at, ps.last_tried_at)"
_PRIORITY = "(fu.due_at IS NOT NULL)"          # the callback is callable now (future ones are excluded)


def _fact_params(campaign: dict, clock: dict) -> dict:
    cooldown = int(campaign["cooldown_minutes"])
    since = min(clock["day_start"], clock["now"] - _dt.timedelta(minutes=cooldown))
    return {"campaign": str(campaign["id"]), "now": clock["now"], "day_start": clock["day_start"], "since": since,
            "grace": CALLBACK_GRACE_MINUTES, "max_per_day": int(campaign["max_attempts_per_day"]),
            "cooldown": cooldown}


def candidates(campaign: dict, member_id, *, limit: int = 20, window: int = 200, clock=None, exclude=()) -> list[dict]:
    """The member's next cases in dispatch order, pre-filtered by every SQL-checkable exclusion.

    Two stages so a large pool stays cheap: (1) the member's pool in dispatch order with the cheap
    exclusions (archived, locked, a future callback, a remembered refusal), one WINDOW of rows at
    a time; (2) on that window only, the expensive ones (do-not-call, review flag, a call on the
    case, paid, attempts today, cooldown, no callable contact). Windows are read until `limit`
    candidates are found or the pool is exhausted. Measured on the full local copy: 330 ms for a
    collector with 64k cases in one contract (one window), versus 1.2 s in one pass."""
    clock = clock or _clock()
    cte, params = _pool_cte(campaign, scope_clause(member_id, "mb"))
    params.update(_fact_params(campaign, clock))
    params["skip"] = [str(x) for x in exclude]
    params["win"] = int(window)
    out, offset = [], 0
    while len(out) < limit:
        params.update(lim=int(limit - len(out)), off=offset)
        rows = sess().execute(text(f"""
            WITH pool AS ({cte}),
            ordered AS MATERIALIZED (
                SELECT pool.*, ps.last_tried_at, {_LAST_CONTACT} AS last_contact, {_PRIORITY} AS callback_due
                  FROM pool
                  LEFT JOIN campaign_pool_state ps ON ps.campaign_id = CAST(:campaign AS uuid) AND ps.case_id = pool.id
                  LEFT JOIN LATERAL (SELECT f.due_at FROM follow_ups f
                                      WHERE f.case_id = pool.id AND f.status = 'open' ORDER BY f.due_at LIMIT 1) fu ON true
                 WHERE NOT pool.is_archive AND NOT pool.is_locked AND NOT {_FUTURE_CALLBACK} AND NOT {_RECHECK}
                 ORDER BY {_PRIORITY} DESC, {_LAST_CONTACT} ASC NULLS FIRST, pool.id
                 LIMIT :win OFFSET :off),
            cand AS (
                SELECT pool.id AS case_id, pool.idn, pool.callback_due, pool.last_contact,
                       coalesce(att.today, 0) AS attempts_today
                  FROM ordered pool
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE a.created_at >= :day_start) AS today, max(a.created_at) AS last_at
                        FROM call_attempts a
                        LEFT JOIN call_reservations r ON r.attempt_id = a.id
                       WHERE a.case_id = pool.id AND a.direction = 'outbound' AND a.created_at >= :since
                         AND (r.release_source IS NULL OR r.release_source NOT IN ('no_customer_contact', 'cancel_unsent'))
                  ) att ON true {_BALANCE}
                 WHERE NOT {_DNC} AND NOT {_REVIEW} AND NOT {_RESERVED} AND NOT {_PAID}
                   AND NOT {_MAXED} AND NOT {_COOLING} AND {_CONTACT}
                   AND NOT (pool.id = ANY(CAST(:skip AS uuid[])))
                 ORDER BY pool.callback_due DESC, pool.last_contact ASC NULLS FIRST, pool.id
                 LIMIT :lim)
            SELECT (SELECT count(*) FROM ordered) AS window_rows, cand.*
              FROM (SELECT 1) one LEFT JOIN cand ON true
             ORDER BY cand.callback_due DESC, cand.last_contact ASC NULLS FIRST, cand.case_id"""),
            params).mappings().all()
        out.extend(dict(r) for r in rows if r["case_id"] is not None)
        if rows[0]["window_rows"] < window:
            break                                             # the pool is exhausted
        offset += window
    return out


def choose_contact(identity_number) -> tuple[dict | None, str | None]:
    """The debtor's contact to call -> (contact row with `destination`, 'default' | 'fallback').

    1. the DEFAULT contact, when it is callable (active, not archived, not deleted, not blocked,
       a plausible E.164 number);
    2. otherwise (no default, or the default is not callable) the most recently added callable
       contact of the debtor, then by id (deterministic);
    3. none -> the case is excluded (`no_callable_contact`).
    Nothing is guessed: a number that is not plausibly E.164 is never used."""
    rows = sess().execute(text(f"""
        SELECT k.id, k.contact, k.is_default, k.create_at FROM "ClintContacts" k
         WHERE k."identityNumber" = :idn AND {CONTACT_ELIGIBLE.format(k='k')}
         ORDER BY k.is_default DESC, k.create_at DESC, k.id"""), {"idn": identity_number}).mappings().all()
    for r in rows:
        dest = e164_from_contact(r["contact"])
        if dest is not None:
            return {"id": str(r["id"]), "destination": dest}, ("default" if r["is_default"] else "fallback")
    return None, None


_CLASSIFY = f"""CASE
    WHEN pool.is_archive THEN 'case_archived'
    WHEN pool.is_locked THEN 'case_locked'
    WHEN {_DNC} THEN 'do_not_call'
    WHEN {_REVIEW} THEN 'calling_review_pending'
    WHEN {_RESERVED} THEN 'in_call'
    WHEN NOT pool.in_member_scope THEN 'no_member_in_scope'
    WHEN {_PAID} THEN 'case_paid'
    WHEN NOT {_CONTACT} THEN 'no_callable_contact'
    WHEN {_FUTURE_CALLBACK} THEN 'callback_scheduled'
    WHEN {_MAXED} THEN 'max_attempts_today'
    WHEN {_COOLING} THEN 'cooldown'
    WHEN {_RECHECK} THEN 'recheck_later'
    ELSE 'eligible' END"""




def _classified(campaign: dict, member_ids, clock) -> tuple[str, dict]:
    ms_sql, ms_params = union_scope_clause(member_ids)
    cte, params = _pool_cte(campaign, None, f", {ms_sql} AS in_member_scope")
    params.update(ms_params)
    params.update(_fact_params(campaign, clock))
    return (f"""
        WITH pool AS ({cte})
        SELECT pool.id AS case_id, pool.name, pool.account_number, pool.user_id,
               {_CLASSIFY} AS reason, {_PRIORITY} AS callback_due, {_LAST_CONTACT} AS last_contact,
               coalesce(att.today, 0) AS attempts_today, fu.due_at AS callback_at,
               ps.excluded_reason AS last_excluded_reason, ps.last_tried_at
          FROM pool {_FACTS} {_BALANCE}"""), params


def summary(campaign: dict, member_ids, clock=None) -> dict:
    """Pool size, eligible now, remaining today and the exclusion reasons (one per case)."""
    clock = clock or _clock()
    sql, params = _classified(campaign, member_ids, clock)
    rows = sess().execute(text(f"SELECT reason, count(*) AS n FROM ({sql}) x GROUP BY reason"), params).all()
    counts = {r[0]: int(r[1]) for r in rows}
    size = sum(counts.values())
    hard = sum(counts.get(r, 0) for r in HARD_REASONS)
    return {"size": size, "eligible_now": counts.get("eligible", 0), "remaining_today": size - hard,
            "excluded": {r: counts[r] for r in REASONS if r in counts and r != "eligible"},
            "evaluated_at": clock["now"].isoformat()}


def listing(campaign: dict, member_ids, *, reason=None, page=1, size=50, offset=0, clock=None) -> dict:
    """The pool in dispatch order with each case's current reason (supervisor preview)."""
    clock = clock or _clock()
    sql, params = _classified(campaign, member_ids, clock)
    where = ""
    if reason:
        bad = [r for r in reason if r not in REASONS]
        if bad:
            raise _err("reason", f"unknown reason(s): {', '.join(bad)}")
        where = "WHERE reason = ANY(CAST(:reasons AS text[]))"
        params["reasons"] = list(reason)
    total = sess().execute(text(f"SELECT count(*) FROM ({sql}) x {where}"), params).scalar()
    params.update({"lim": size, "off": offset})
    rows = sess().execute(text(f"""
        SELECT * FROM ({sql}) x {where}
         ORDER BY (reason = 'eligible') DESC, callback_due DESC, last_contact ASC NULLS FIRST, case_id
         LIMIT :lim OFFSET :off"""), params).mappings().all()
    items = [{"case": {"id": str(r["case_id"]), "name": r["name"], "account_number": r["account_number"],
                       "owner_id": str(r["user_id"]) if r["user_id"] else None},
              "reason": r["reason"], "priority": "callback_due" if r["callback_due"] else "normal",
              "last_contact_at": r["last_contact"].isoformat() if r["last_contact"] else None,
              "callback_at": r["callback_at"].isoformat() if r["callback_at"] else None,
              "attempts_today": int(r["attempts_today"]), "last_tried_at":
                  r["last_tried_at"].isoformat() if r["last_tried_at"] else None,
              "last_excluded_reason": r["last_excluded_reason"]} for r in rows]
    return {"items": items, "total": int(total), "page": page, "page_size": size}


def reason_code_ok(code: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code or ""))
