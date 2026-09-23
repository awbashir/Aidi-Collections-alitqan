"""Shared pieces of the Phase 1 worklist (stream B): follow-ups and the attention queue.

* Tables: `follow_ups` / `follow_up_events` (db/migrations/0010) are not mapped in
  models.py; they are described here as lightweight SQLAlchemy Core tables. The legacy
  tables are used through their ORM `__table__`, so the caller scope from
  `services.clients_scope.scope_condition` (a clause over `clints`) plugs in unchanged.
* `v_promise_state` is stream A's view (columns fixed in docs/phase1-spec.md).
* Time: every "late / today / overdue" decision compares Asia/Riyadh civil dates. `now()`
  is the single clock of this package (tests monkeypatch it); SQL gets the UTC instants of
  the Riyadh day bounds as parameters, so the comparisons stay index-friendly
  (`due_at < :today_start`) instead of wrapping the column in a function.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from models import Clints, Contract, Payments, Portfolios, User
from services.clients_scope import scope_condition

# --------------------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------------------

_meta = sa.MetaData()

FOLLOW_UPS = sa.Table(
    "follow_ups", _meta,
    sa.Column("id", UUID(as_uuid=False), primary_key=True),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("owner_user_id", UUID(as_uuid=False)),
    sa.Column("due_at", sa.DateTime(timezone=True)),
    sa.Column("original_due_at", sa.DateTime(timezone=True)),
    sa.Column("next_action", sa.Text),
    sa.Column("status", sa.Text),
    sa.Column("source", sa.Text),
    sa.Column("source_comment_id", UUID(as_uuid=False)),
    sa.Column("created_by", UUID(as_uuid=False)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("completed_by", UUID(as_uuid=False)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("completed_late", sa.Boolean),
    sa.Column("outcome_comment_id", UUID(as_uuid=False)),
    sa.Column("no_further_action_reason", sa.Text),
    sa.Column("next_follow_up_id", UUID(as_uuid=False)),
    sa.Column("closed_at", sa.DateTime(timezone=True)),
    sa.Column("closed_by", UUID(as_uuid=False)),
    sa.Column("superseded_by", UUID(as_uuid=False)),
    sa.Column("close_reason", sa.Text),
    sa.Column("reschedule_count", sa.Integer),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("revision", sa.Integer),
)

FOLLOW_UP_EVENTS = sa.Table(
    "follow_up_events", _meta,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("follow_up_id", UUID(as_uuid=False)),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("event", sa.Text),
    sa.Column("actor_user_id", UUID(as_uuid=False)),
    sa.Column("occurred_at", sa.DateTime(timezone=True)),
    sa.Column("due_at_before", sa.DateTime(timezone=True)),
    sa.Column("due_at_after", sa.DateTime(timezone=True)),
    sa.Column("was_late", sa.Boolean),
    sa.Column("reason", sa.Text),
    sa.Column("comment_id", UUID(as_uuid=False)),
    sa.Column("related_follow_up_id", UUID(as_uuid=False)),
    sa.Column("source", sa.Text),
    sa.Column("request_id", sa.Text),
    sa.Column("details", sa.JSON),
)

# stream A's contract (docs/phase1-spec.md): promise_id, case_id, recorder_user_id,
# due_date, amount, allocated, state, cancelled_at, fulfilled_at
PROMISE_STATE = sa.Table(
    "v_promise_state", _meta,
    sa.Column("promise_id", UUID(as_uuid=False)),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("recorder_user_id", UUID(as_uuid=False)),
    sa.Column("due_date", sa.Date),
    sa.Column("amount", sa.Numeric(14, 2)),
    sa.Column("allocated", sa.Numeric(14, 2)),
    sa.Column("state", sa.Text),
    sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    sa.Column("fulfilled_at", sa.DateTime(timezone=True)),
)

# Planner note: `state` is a CASE expression inside the view, so PostgreSQL has no
# statistics for it and estimates `state = 'x'` at 0.5 % of the rows (118 of 11.8k on the
# real data). With that estimate it picked a materialised nested-loop anti-join for the
# unscheduled page: 78 s. The same predicates written as NOT IN over the complementary
# states of the (closed) contract set are estimated at ~99 % and get hash joins (0.2 s).
# states: 'open' | 'overdue' | 'fulfilled' | 'cancelled'  (never NULL)
PROMISE_ACTIVE = PROMISE_STATE.c.state.notin_(("fulfilled", "cancelled"))          # open or overdue
PROMISE_OVERDUE = PROMISE_STATE.c.state.notin_(("open", "fulfilled", "cancelled"))  # overdue

CLINTS = Clints.__table__
PORTFOLIOS = Portfolios.__table__
CONTRACTS = Contract.__table__
PAYMENTS = Payments.__table__
USERS = User.__table__

PAYMENT_PENDING = sa.and_(PAYMENTS.c.approval_status.is_(None), PAYMENTS.c.is_deleted == sa.false())

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


# --------------------------------------------------------------------------------------
# clock / Riyadh day
# --------------------------------------------------------------------------------------

def now() -> _dt.datetime:
    """The worklist clock (aware). Tests monkeypatch this."""
    return riyadh.now()


class Day:
    """Riyadh civil 'today' and its UTC bounds, fixed once per request."""

    __slots__ = ("at", "today", "start", "end")

    def __init__(self, at: _dt.datetime | None = None):
        self.at = at or now()
        self.today = riyadh.civil_date(self.at)
        self.start, self.end = riyadh.day_bounds(self.today)

    def bucket(self, due_at) -> str:
        if due_at < self.start:
            return "late"
        if due_at < self.end:
            return "today"
        return "upcoming"

    def days_late(self, due_at) -> int:
        return max(0, (self.today - riyadh.civil_date(due_at)).days)

    def as_dict(self) -> dict:
        return {"at": iso(self.at), "riyadh_date": self.today.isoformat(),
                "day_start": iso(self.start), "day_end": iso(self.end)}


# --------------------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------------------

def bad(field: str, message: str, problem: str = "invalid"):
    return ApiError(422, VALIDATION_FAILED, message, {field: problem})


def parse_uuid(value, field: str, required: bool = False):
    if value is None or value == "":
        if required:
            raise bad(field, f"{field} is required", "required")
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise bad(field, f"{field} must be a UUID") from None


def parse_text(value, field: str, required: bool = False, max_len: int = 2000):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise bad(field, f"{field} is required", "required")
        return None
    if not isinstance(value, str):
        raise bad(field, f"{field} must be a string")
    value = value.strip()
    if len(value) > max_len:
        raise bad(field, f"{field} is longer than {max_len} characters", "too_long")
    return value


def parse_due(value, field: str = "due_at") -> _dt.datetime:
    """An instant for a follow-up due time.

    Accepts an ISO-8601 datetime **with** an offset ("2026-09-24T10:00:00+03:00",
    "...Z"), or a civil date "YYYY-MM-DD" meaning 09:00 Asia/Riyadh that day. A naive
    datetime is rejected: it is ambiguous (the legacy UI stored UTC midnights).
    """
    if value is None or value == "":
        raise bad(field, f"{field} is required", "required")
    if not isinstance(value, str):
        raise bad(field, f"{field} must be an ISO-8601 string")
    s = value.strip()
    if len(s) == 10:
        try:
            d = _dt.date.fromisoformat(s)
        except ValueError:
            raise bad(field, f"{field} must be YYYY-MM-DD or an ISO-8601 datetime with offset") from None
        return _dt.datetime.combine(d, _dt.time(9, 0), tzinfo=riyadh.RIYADH).astimezone(riyadh.UTC)
    try:
        ts = _dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise bad(field, f"{field} must be YYYY-MM-DD or an ISO-8601 datetime with offset") from None
    if ts.tzinfo is None:
        raise bad(field, f"{field} needs a UTC offset (e.g. +03:00 or Z)", "naive_datetime")
    return ts.astimezone(riyadh.UTC)


def not_before_today(due: _dt.datetime, day: Day, field: str = "due_at"):
    if riyadh.civil_date(due) < day.today:
        raise ApiError(422, "due_in_past",
                       f"{field} is before today ({day.today.isoformat()}, Asia/Riyadh)",
                       {field: "before_today", "riyadh_date": day.today.isoformat()})


def paging(args) -> tuple[int, int]:
    try:
        page = int(args.get("page", 1))
        size = int(args.get("pageSize", args.get("page_size", DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        raise ApiError(422, VALIDATION_FAILED, "page and pageSize must be integers",
                       {"page": "invalid"}) from None
    if page < 1 or size < 1:
        raise ApiError(422, VALIDATION_FAILED, "page and pageSize must be >= 1", {"page": "invalid"})
    return page, min(size, MAX_PAGE_SIZE)


# --------------------------------------------------------------------------------------
# scope + common case filters
# --------------------------------------------------------------------------------------

class CaseFilter:
    """Caller scope plus the optional owner / contract / portfolio filters, as clauses over
    `clints`. Live cases only (not archived). Never widens the scope."""

    def __init__(self, scope, owner=None, contract_id=None, portfolio_id=None):
        self.scope = scope
        self.owner = owner
        self.contract_id = contract_id
        self.portfolio_id = portfolio_id

    @classmethod
    def from_args(cls, scope, args):
        return cls(scope,
                   owner=parse_uuid(args.get("owner") or args.get("owner_user_id"), "owner"),
                   contract_id=parse_uuid(args.get("contract_id"), "contract_id"),
                   portfolio_id=parse_uuid(args.get("portfolio_id"), "portfolio_id"))

    def clauses(self) -> list:
        out = [CLINTS.c.is_archive == sa.false()]
        cond = scope_condition(self.scope, [self.contract_id] if self.contract_id else None)
        if cond is not None:
            out.append(cond)
        if self.owner:
            out.append(CLINTS.c.user_id == self.owner)
        if self.portfolio_id:
            out.append(CLINTS.c.portfolios_id == self.portfolio_id)
        if self.contract_id:
            out.append(CLINTS.c.portfolios_id.in_(
                sa.select(PORTFOLIOS.c.id).where(PORTFOLIOS.c.contract_id == self.contract_id)))
        return out

    def as_dict(self):
        return {"owner": self.owner, "contract_id": self.contract_id, "portfolio_id": self.portfolio_id}


def case_in_scope(session, scope, case_id, *, lock: bool = False):
    """The case row (mapping) if it exists **and** is in the caller's scope, else None.
    Archived cases are returned too (callers decide). `lock` takes FOR NO KEY UPDATE, the
    same lock the note trigger takes, so writers on one case serialise in one order."""
    cond = scope_condition(scope)
    stmt = sa.select(CLINTS.c.id, CLINTS.c.user_id, CLINTS.c.is_archive, CLINTS.c.is_active,
                     CLINTS.c.is_locked).where(CLINTS.c.id == case_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if lock:
        stmt = stmt.with_for_update(key_share=True)      # PostgreSQL: FOR NO KEY UPDATE
    return session.execute(stmt).mappings().first()


# --------------------------------------------------------------------------------------
# display helpers
# --------------------------------------------------------------------------------------

def iso(ts):
    if ts is None:
        return None
    if isinstance(ts, _dt.datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=riyadh.UTC)
        return ts.astimezone(riyadh.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(ts, _dt.date):
        return ts.isoformat()
    return str(ts)


def civil(ts):
    return riyadh.civil_date(ts).isoformat() if ts is not None else None


def money(value):
    """Exact decimal string ("1234.50") for numeric values, None stays None."""
    if value is None:
        return None
    if not isinstance(value, decimal.Decimal):
        value = decimal.Decimal(str(value))
    return str(value.quantize(decimal.Decimal("0.01")))


def sid(value):
    return None if value is None else str(value)


USER_NAME = sa.func.trim(sa.func.concat(USERS.c.frist_name, " ", USERS.c.last_name))


def case_columns(owner_alias):
    """Columns for the compact case block used by both lists."""
    return [
        CLINTS.c.id.label("case_row_id"),
        CLINTS.c.name.label("case_name"),
        CLINTS.c.accountNumber.label("account_number"),
        CLINTS.c.identityNumber.label("identity_number"),
        sa.func.round(sa.cast(CLINTS.c.amount, sa.Numeric), 2).label("original_amount"),
        CLINTS.c.is_locked.label("is_locked"),
        CLINTS.c.is_active.label("is_active"),
        CLINTS.c.user_id.label("case_owner_id"),
        CLINTS.c.last_comment_at.label("last_comment_at"),
        PORTFOLIOS.c.id.label("portfolio_id"),
        PORTFOLIOS.c.name.label("portfolio_name"),
        CONTRACTS.c.id.label("contract_id"),
        CONTRACTS.c.name.label("contract_name"),
        sa.func.trim(sa.func.concat(owner_alias.c.frist_name, " ", owner_alias.c.last_name)).label("case_owner_name"),
        owner_alias.c.user_name.label("case_owner_user_name"),
    ]


def case_block(r) -> dict:
    return {
        "id": sid(r["case_row_id"]),
        "name": r["case_name"],
        "accountNumber": r["account_number"],
        "identityNumber": r["identity_number"],
        "original_amount": money(r["original_amount"]),
        "is_locked": bool(r["is_locked"]),
        "last_comment_at": iso(r["last_comment_at"]),
        "owner": ({"id": sid(r["case_owner_id"]), "name": r["case_owner_name"],
                   "user_name": r["case_owner_user_name"]} if r["case_owner_id"] else None),
        "portfolio": {"id": sid(r["portfolio_id"]), "name": r["portfolio_name"]},
        "contract": {"id": sid(r["contract_id"]), "name": r["contract_name"]},
    }


def case_join(owner_alias):
    return (CLINTS.join(PORTFOLIOS, PORTFOLIOS.c.id == CLINTS.c.portfolios_id)
                  .join(CONTRACTS, CONTRACTS.c.id == PORTFOLIOS.c.contract_id)
                  .outerjoin(owner_alias, owner_alias.c.id == CLINTS.c.user_id))
