"""Shared pieces of internal requests (Phase 3, stream A; docs/phase3-spec.md "Stream A").

* Tables `requests`, `request_events`, `request_attachments` (db/migrations/0028-0030) are
  not mapped in models.py; they are described here as SQLAlchemy Core tables.
* `Viewer` is the caller: JWT identity, case scope (services/clients_scope.py) and the four
  permission keys.
* `visibility()` is the one visibility rule, used by every read and write:

    reviewer    any `requests_<department>` key     -> requests they raised + their department(s)'
                                                       requests on cases in their case scope
                                                       (never widened by the supervisor rule)
    supervisor  `requests`, no department key       -> own + requests on cases in their scope +
                                                       requests raised by a supervised user
    SuperAdmin  `requests`, no department key       -> every request
    collector   `requests`, no department key       -> only the requests they raised

  A request the caller cannot see answers 404 everywhere (no existence leak).
"""
from __future__ import annotations

import decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from core import riyadh
from core.api_response import FORBIDDEN, VALIDATION_FAILED, ApiError
from services import worklist_common as wc
from services.clients_scope import COLLECTOR, SUPER, SUPERVISOR
from services.worklist_common import CLINTS, CONTRACTS, PORTFOLIOS, USERS

# --------------------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------------------

_meta = sa.MetaData()

REQUESTS = sa.Table(
    "requests", _meta,
    sa.Column("id", UUID(as_uuid=False), primary_key=True),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("department", sa.Text),
    sa.Column("subject", sa.Text),
    sa.Column("body", sa.Text),
    sa.Column("amount", sa.Numeric(14, 2)),
    sa.Column("related_date", sa.Date),
    sa.Column("status", sa.Text),
    sa.Column("requested_by", UUID(as_uuid=False)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("reviewer_id", UUID(as_uuid=False)),
    sa.Column("review_started_at", sa.DateTime(timezone=True)),
    sa.Column("resolved_by", UUID(as_uuid=False)),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
    sa.Column("resolution", sa.Text),
    sa.Column("cancelled_by", UUID(as_uuid=False)),
    sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    sa.Column("cancel_reason", sa.Text),
    sa.Column("reopen_count", sa.Integer),
    sa.Column("last_event_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("revision", sa.Integer),
)

EVENTS = sa.Table(
    "request_events", _meta,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("request_id", UUID(as_uuid=False)),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("event", sa.Text),
    sa.Column("actor_user_id", UUID(as_uuid=False)),
    sa.Column("occurred_at", sa.DateTime(timezone=True)),
    sa.Column("status_before", sa.Text),
    sa.Column("status_after", sa.Text),
    sa.Column("reason", sa.Text),
    sa.Column("body", sa.Text),
    sa.Column("details", JSONB),
    sa.Column("source", sa.Text),
    sa.Column("request_ref", sa.Text),
    sa.Column("idempotency_key", sa.Text),
)

ATTACHMENTS = sa.Table(
    "request_attachments", _meta,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("request_id", UUID(as_uuid=False)),
    sa.Column("document_id", UUID(as_uuid=False)),
    sa.Column("event_id", sa.BigInteger),
    sa.Column("added_by", UUID(as_uuid=False)),
    sa.Column("added_at", sa.DateTime(timezone=True)),
)

DOCUMENTS = sa.table(
    "clint_documents", sa.column("id"), sa.column("clint_id"), sa.column("display_name"),
    sa.column("original_name"), sa.column("file_type"), sa.column("is_deleted"), sa.column("create_at"),
    sa.column("uploaded_by"),
)

# --------------------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------------------

DEPARTMENTS = ("legal", "finance", "operations")
STATUSES = ("open", "in_review", "resolved", "cancelled")
UNRESOLVED = ("open", "in_review")

PERM_BASE = "requests"
PERM_DEPT = {"legal": "requests_legal", "finance": "requests_finance", "operations": "requests_operations"}
ALL_KEYS = (PERM_BASE, *PERM_DEPT.values())
REVIEW_KEYS = tuple(PERM_DEPT.values())

# action -> (allowed from, to)
TRANSITIONS = {
    "take": (("open",), "in_review"),
    "resolve": (("in_review",), "resolved"),
    "reopen": (("resolved",), "in_review"),
    "cancel": (("open",), "cancelled"),
}
EVENT_OF = {"take": "taken", "resolve": "resolved", "reopen": "reopened", "cancel": "cancelled"}

MAX_ATTACHMENTS = 20
MAX_AMOUNT = decimal.Decimal("999999999999.99")
SUBJECT_MAX, BODY_MAX, REASON_MAX = 200, 5000, 2000


# --------------------------------------------------------------------------------------
# the caller
# --------------------------------------------------------------------------------------

class Viewer:
    """Identity + scope + the four request keys, fixed once per request."""

    __slots__ = ("identity", "scope", "base", "departments", "_portfolios")

    def __init__(self, identity, scope, base: bool, departments):
        self.identity = str(identity)
        self.scope = scope
        self.base = bool(base)
        self.departments = tuple(d for d in DEPARTMENTS if d in set(departments or ()))
        self._portfolios = None

    @classmethod
    def from_request(cls, identity, scope, has_permission):
        return cls(identity, scope, has_permission(PERM_BASE),
                   [d for d, k in PERM_DEPT.items() if has_permission(k)])

    @property
    def any_key(self) -> bool:
        return self.base or bool(self.departments)

    def reviews(self, department) -> bool:
        return department in self.departments

    def as_dict(self) -> dict:
        return {"user_id": self.identity, "scope": self.scope.kind, "can_create": self.base,
                "reviewer_departments": list(self.departments)}

    # -- case scope, as a clause over clints (None = unrestricted) ----------------------
    def portfolio_ids(self, session) -> list:
        """The supervised contracts' portfolios, resolved once into a list: an IN list on
        clints.portfolios_id is planned as index probes, while `IN (SELECT ...)` let the
        planner hash-join a scan of clints (review/phase1-history.md §7: 556 ms -> 33 ms)."""
        if self._portfolios is None:
            cids = sorted(self.scope.contract_ids or ())
            self._portfolios = [] if not cids else [str(r[0]) for r in session.execute(
                sa.select(PORTFOLIOS.c.id).where(PORTFOLIOS.c.contract_id.in_(cids)))]
        return self._portfolios

    def case_scope(self, session):
        s = self.scope
        if s.kind == SUPER:
            return None
        if s.kind == COLLECTOR or (not s.user_ids and not s.contract_ids):
            return CLINTS.c.user_id == self.identity
        parts = []
        if s.user_ids:
            parts.append(CLINTS.c.user_id.in_(sorted(s.user_ids)))
        pids = self.portfolio_ids(session)
        if pids:
            parts.append(CLINTS.c.portfolios_id.in_(pids))
        if not parts:
            return CLINTS.c.user_id == self.identity
        return parts[0] if len(parts) == 1 else sa.or_(*parts)


class Visibility:
    """The visibility rule as a clause over `requests LEFT JOIN clints`.

    `clause` is None when the caller sees every request; `needs_case` says whether the
    clause reads clints (so count statements can skip the join when it does not)."""

    __slots__ = ("clause", "needs_case", "nothing")

    def __init__(self, clause, needs_case: bool, nothing: bool = False):
        self.clause, self.needs_case, self.nothing = clause, needs_case, nothing


def visibility(session, viewer: Viewer) -> Visibility:
    """Decision 2026-09-24 (review/phase3-requests.md §3.5):

    * a caller holding ANY department key is a reviewer and sees only the requests they
      raised plus their department(s)' requests on cases in their scope; the supervisor
      "team through scope" rule never widens that to other departments;
    * a caller with `requests` and no department key: own requests; a supervisor also sees
      requests on cases in their scope and requests raised by supervised users; SuperAdmin
      sees all;
    * a collector (no department key) sees only the requests they raised.
    """
    R = REQUESTS
    own = R.c.requested_by == viewer.identity
    if viewer.departments:
        case_scope = viewer.case_scope(session)
        dept = (R.c.department.in_(viewer.departments) if len(viewer.departments) < len(DEPARTMENTS)
                else None)
        if case_scope is None:                      # SuperAdmin scope
            if dept is None:
                return Visibility(None, False)
            return Visibility(sa.or_(own, dept), False)
        reviewed = case_scope if dept is None else sa.and_(dept, case_scope)
        return Visibility(sa.or_(own, reviewed), True)
    if viewer.base:
        if viewer.scope.kind == SUPER:
            return Visibility(None, False)
        if viewer.scope.kind == SUPERVISOR:
            conds = [own, viewer.case_scope(session)]
            others = sorted(u for u in (viewer.scope.user_ids or ()) if u != viewer.identity)
            if others:
                conds.append(R.c.requested_by.in_(others))
            return Visibility(sa.or_(*conds), True)
        return Visibility(own, False)
    return Visibility(sa.false(), False, nothing=True)


def case_in_scope(session, viewer: Viewer, case_id):
    """The case row (mapping) when it exists and is in the caller's case scope, else None."""
    stmt = sa.select(CLINTS.c.id, CLINTS.c.user_id, CLINTS.c.is_archive, CLINTS.c.is_locked,
                     CLINTS.c.is_active).where(CLINTS.c.id == str(case_id))
    cond = viewer.case_scope(session)
    if cond is not None:
        stmt = stmt.where(cond)
    return session.execute(stmt).mappings().first()


def require_any(viewer: Viewer, keys=ALL_KEYS):
    held = ({PERM_BASE} if viewer.base else set()) | {PERM_DEPT[d] for d in viewer.departments}
    if not held & set(keys):
        raise ApiError(403, FORBIDDEN, "You are not authorized to access this resource",
                       {"required_any": list(keys)})


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------

bad = wc.bad
parse_uuid = wc.parse_uuid
parse_text = wc.parse_text


def parse_choice(value, field, choices, required=False):
    if value is None or value == "":
        if required:
            raise bad(field, f"{field} is required", "required")
        return None
    if not isinstance(value, str) or value not in choices:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be one of {', '.join(choices)}",
                       {field: "invalid", "allowed": list(choices)})
    return value


def parse_multi(value, field, choices) -> tuple:
    """'a,b' or ['a', 'b'] -> tuple in canonical order; unknown values are a 422."""
    if value is None or value == "" or value == []:
        return ()
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    items = {str(i).strip() for i in items if str(i).strip()}
    unknown = sorted(items - set(choices))
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be among {', '.join(choices)}",
                       {field: "invalid", "unknown": unknown, "allowed": list(choices)})
    return tuple(c for c in choices if c in items)


def parse_amount(value):
    """Exact money: a string or number, > 0, at most 2 decimals, <= 999,999,999,999.99."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise bad("amount", "amount must be a decimal string such as \"1500.00\"")
    try:
        d = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        raise bad("amount", "amount must be a decimal string such as \"1500.00\"") from None
    if not d.is_finite() or d <= 0:
        raise bad("amount", "amount must be greater than 0", "not_positive")
    if d != d.quantize(decimal.Decimal("0.01")):
        raise bad("amount", "amount has more than 2 decimals", "too_many_decimals")
    if d > MAX_AMOUNT:
        raise bad("amount", "amount is too large", "too_large")
    return d.quantize(decimal.Decimal("0.01"))


def parse_civil_date(value, field):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) != 10:
        raise bad(field, f"{field} must be a civil date YYYY-MM-DD (Asia/Riyadh)")
    try:
        return riyadh.parse_date(value)
    except ValueError:
        raise bad(field, f"{field} must be a civil date YYYY-MM-DD (Asia/Riyadh)") from None


def parse_reason(body, field="reason"):
    return parse_text(body.get(field), field, required=True, max_len=REASON_MAX)


def parse_document_ids(value, field="document_ids") -> list:
    if value is None or value == []:
        return []
    if not isinstance(value, list):
        raise bad(field, f"{field} must be a list of document ids")
    out = []
    for v in value:
        u = parse_uuid(v, field)
        if u is None:
            raise bad(field, f"{field} must be a list of document ids")
        if u not in out:
            out.append(u)
    if len(out) > MAX_ATTACHMENTS:
        raise bad(field, f"at most {MAX_ATTACHMENTS} documents per request", "too_many")
    return out


def user_ref_or_me(value, viewer: Viewer, field):
    if value in (None, ""):
        return None
    if value == "me":
        return viewer.identity
    return parse_uuid(value, field)


# --------------------------------------------------------------------------------------
# display
# --------------------------------------------------------------------------------------

iso = wc.iso
sid = wc.sid
money = wc.money


def user_name_expr(alias):
    return sa.func.nullif(sa.func.trim(sa.func.concat(alias.c.frist_name, " ", alias.c.last_name)), "")


def user_ref(uid, name, user_name=None):
    if uid is None:
        return None
    d = {"id": sid(uid), "name": name}
    if user_name is not None:
        d["user_name"] = user_name
    return d


def civil(ts):
    return riyadh.civil_date(ts).isoformat() if ts is not None else None


def date_bounds(date_from, date_to):
    """Half-open UTC bounds for the inclusive Riyadh civil dates (either side optional)."""
    start = riyadh.day_bounds(date_from)[0] if date_from else None
    end = riyadh.day_bounds(date_to)[1] if date_to else None
    if date_from and date_to and date_to < date_from:
        raise ApiError(422, VALIDATION_FAILED, "date_to is before date_from",
                       {"date_to": "before_date_from"})
    return start, end


def as_of() -> dict:
    n = riyadh.now()
    return {"at": iso(n), "riyadh_date": n.date().isoformat()}


# case block (outer-joined: a request survives the legacy hard delete of its case)
CASE_OWNER = USERS.alias("case_owner_u")


def case_columns():
    return [
        CLINTS.c.id.label("case_row_id"),
        CLINTS.c.name.label("case_name"),
        CLINTS.c.accountNumber.label("account_number"),
        CLINTS.c.identityNumber.label("identity_number"),
        sa.func.round(sa.cast(CLINTS.c.amount, sa.Numeric), 2).label("original_amount"),
        CLINTS.c.is_locked.label("is_locked"),
        CLINTS.c.is_active.label("is_active"),
        CLINTS.c.is_archive.label("is_archived"),
        CLINTS.c.user_id.label("case_owner_id"),
        CLINTS.c.last_comment_at.label("last_comment_at"),
        PORTFOLIOS.c.id.label("portfolio_id"),
        PORTFOLIOS.c.name.label("portfolio_name"),
        CONTRACTS.c.id.label("contract_id"),
        CONTRACTS.c.name.label("contract_name"),
        user_name_expr(CASE_OWNER).label("case_owner_name"),
        CASE_OWNER.c.user_name.label("case_owner_user_name"),
    ]


def case_outer_join(left, on):
    return (left.outerjoin(CLINTS, on)
                .outerjoin(PORTFOLIOS, PORTFOLIOS.c.id == CLINTS.c.portfolios_id)
                .outerjoin(CONTRACTS, CONTRACTS.c.id == PORTFOLIOS.c.contract_id)
                .outerjoin(CASE_OWNER, CASE_OWNER.c.id == CLINTS.c.user_id))


def case_block(r, case_id=None):
    if r["case_row_id"] is None:
        return {"id": sid(case_id), "missing": True}
    return {
        "id": sid(r["case_row_id"]),
        "missing": False,
        "name": r["case_name"],
        "accountNumber": r["account_number"],
        "identityNumber": r["identity_number"],
        "original_amount": money(r["original_amount"]),
        "is_locked": bool(r["is_locked"]),
        "is_archived": bool(r["is_archived"]),
        "owner": user_ref(r["case_owner_id"], r["case_owner_name"], r["case_owner_user_name"]),
        "portfolio": ({"id": sid(r["portfolio_id"]), "name": r["portfolio_name"]}
                      if r["portfolio_id"] is not None else None),
        "contract": ({"id": sid(r["contract_id"]), "name": r["contract_name"]}
                     if r["contract_id"] is not None else None),
    }
