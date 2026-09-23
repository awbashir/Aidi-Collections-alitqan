"""SQL query builders + row serializers for every client-list / client-export endpoint.

This is `_build_clints_payload` (app.py) refactored so the grid and the export share one
builder, plus SQL rewrites of the five report queries that still went through Redis
`KEYS` + `pickle` + a query per row.

Design rules (review P1/P2/P6, §6):

* **Never** `redis_obj.keys()`, never `pickle`, never a query per row, never `.all()` on a
  client-sized table. Every builder returns a `Query` that pages/sorts/filters in SQL.
* Every builder selects **labelled columns**, not ORM entities. A `Query` over entities
  keeps every loaded object alive in the session identity map, which is exactly what made
  `/clients/export` OOM the worker; plain `Row` tuples are dropped as soon as the
  serializer is done with them, so `yield_per()` keeps memory flat.
* Small reference tables (users, portfolios, contracts, comment statuses, delinquency and
  archive reasons) are read once per request through `core.lookups.get_map` (process-local
  60 s TTL dict) instead of being joined or -- worse -- lazily loaded per row.
* The serialized row dicts are **byte-for-byte** the legacy shapes, including the key
  order, the `{"comment": null, "create_at": null}` placeholder, the `str()`-ed nested ids
  and the conditional `specifyUser` / `clintArchive` keys.
"""
import datetime
import logging
import os

from sqlalchemy import String, and_, cast, func, text as sa_text
from sqlalchemy.orm import aliased

from core import lookups
from core.files import resolve_temp_file
from extensions import db
from models import (ArchiveReason, ClintArchive, Clints, CommentStatuses, Comments,
                    Contract, DelinquencyReason, Payments, Portfolios, SepcifyUsers, User)
from services.clients_scope import resolve_scope, scope_condition

log = logging.getLogger("cawp.clients_query")

# ---------------------------------------------------------------------------
# reference-table maps (batched, never a query per row)
# ---------------------------------------------------------------------------


def users_map():
    def load():
        rows = db.session.query(
            User.id, User.frist_name, User.last_name, User.branch_id
        ).all()
        return [{
            "id": str(r[0]),
            "frist_name": r[1],
            "last_name": r[2],
            "name": (r[1] or "") + " " + (r[2] or ""),
            "branch_id": str(r[3]) if r[3] else None,
        } for r in rows]
    return lookups.get_map("b1_users", load)


def portfolios_map():
    def load():
        rows = db.session.query(
            Portfolios.id, Portfolios.name, Portfolios.create_at, Portfolios.contract_id
        ).all()
        return [{"id": str(r[0]), "name": r[1], "create_at": r[2],
                 "contract_id": str(r[3])} for r in rows]
    return lookups.get_map("b1_portfolios", load)


def contracts_map():
    def load():
        rows = db.session.query(Contract.id, Contract.name).all()
        return [{"id": str(r[0]), "name": r[1]} for r in rows]
    return lookups.get_map("b1_contracts", load)


def comment_statuses_map():
    def load():
        rows = db.session.query(
            CommentStatuses.id, CommentStatuses.name, CommentStatuses.color,
            CommentStatuses.is_active, CommentStatuses.CommentReachability_id
        ).all()
        return [{"id": str(r[0]), "name": r[1], "color": r[2], "is_active": r[3],
                 "CommentReachability_id": str(r[4]) if r[4] else None} for r in rows]
    return lookups.get_map("b1_comment_statuses", load)


def delinquency_reasons_map():
    def load():
        rows = db.session.query(DelinquencyReason.id, DelinquencyReason.name).all()
        return [{"id": str(r[0]), "name": r[1]} for r in rows]
    return lookups.get_map("b1_delinquency_reasons", load)


def archive_reasons_map():
    def load():
        rows = db.session.query(ArchiveReason.id, ArchiveReason.name).all()
        return [{"id": str(r[0]), "name": r[1]} for r in rows]
    return lookups.get_map("b1_archive_reasons", load)


def all_maps():
    """One dict of every reference map, built once per request."""
    return {
        "users": users_map(),
        "portfolios": portfolios_map(),
        "contracts": contracts_map(),
        "comment_statuses": comment_statuses_map(),
        "delinquency_reasons": delinquency_reasons_map(),
        "archive_reasons": archive_reasons_map(),
    }


def invalidate_reference_maps():
    lookups.invalidate("b1_users", "b1_portfolios", "b1_contracts",
                       "b1_comment_statuses", "b1_delinquency_reasons",
                       "b1_archive_reasons")


# ---------------------------------------------------------------------------
# param parsing (review §P12 -- exact names and defaults)
# ---------------------------------------------------------------------------


def _list(args, key):
    """`parse_qs(...)[key]` equivalent: repeated key -> list, blanks dropped."""
    return [v for v in args.getlist(key) if v != ""]


def _int(args, key, default):
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _excel_column(args, file_key, index_key):
    """`customeFilterInquiryClintByExcelValue[file_name]` / `selectedClients_Qkey[...]`."""
    file_name = args.get(file_key)
    if not file_name:
        return None
    try:
        path = resolve_temp_file(file_name)
    except ValueError as e:
        log.warning("rejected temp file name %r: %s", file_name, e)
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        from services.excel import column_values   # same string coercion as the old pandas read
        return column_values(path, int(args.get(index_key)))
    except Exception as e:                                     # pragma: no cover
        log.warning("excel filter %s failed: %s", file_name, e)
        return None


def _latest_comment_lateral(name="latest_comment", filter_deleted=True):
    """`LATERAL (SELECT ... FROM "Comments" WHERE clint_id = clints.id [AND NOT is_deleted]
    ORDER BY create_at DESC LIMIT 1) ON true`.

    Replaces the legacy `GROUP BY clint_id -> max(create_at)` subquery + a second join on
    `create_at = max(create_at)`. The group-by form aggregated all 1.27M comments on every
    request and then spilled a 50 MB sort to disk; the LATERAL probes
    `ix_comments_clint_created` once per row the plan actually keeps.

    Equivalent, not merely similar: there is no client with two non-deleted comments
    sharing the same `create_at` (checked -- 0 rows), so the legacy join could never
    return more than one comment per client either.
    """
    q = db.session.query(
        Comments.id.label("id"),
        Comments.comment.label("comment"),
        Comments.comment_status_id.label("comment_status_id"),
        Comments.next_contact_date.label("next_contact_date"),
        Comments.create_at.label("create_at"),
    ).filter(Comments.clint_id == Clints.id)
    if filter_deleted:
        q = q.filter(Comments.is_deleted == False)               # noqa: E712
    return q.order_by(Comments.create_at.desc()).limit(1).subquery().lateral(name)


def _latest_specify_lateral(name="latest_specify"):
    """Latest non-deleted `SepcifyUsers` row per client, via ix_specify_clint_created."""
    return (db.session.query(
                SepcifyUsers.id.label("id"),
                SepcifyUsers.user_id.label("user_id"),
                SepcifyUsers.create_at.label("create_at"),
            )
            .filter(SepcifyUsers.clint_id == Clints.id,
                    SepcifyUsers.is_deleted == False)            # noqa: E712
            .order_by(SepcifyUsers.create_at.desc())
            .limit(1).subquery().lateral(name))


class ListParams(object):
    """Everything the grids send, parsed once. Unknown params are ignored, never fatal."""

    def __init__(self, args, page_size_default=200, page_size_max=None):
        self.args = args
        self.page = _int(args, "page", 1)
        self.pageSize = _int(args, "pageSize", page_size_default)
        if page_size_max is not None and (self.pageSize <= 0 or self.pageSize > page_size_max):
            self.pageSize = page_size_default
        self.startIndex = (self.page - 1) * self.pageSize

        self.field = args.get("field")
        self.sort = args.get("sort")
        # the archive screens + /v2/clints use the sort[key]/sort[order] form instead
        self.sort_key = args.get("sort[key]")
        self.sort_order = args.get("sort[order]")

        self.filterOperator = args.get("filterOperator")
        self.filterField = args.get("filterField")
        self.filterFieldType = args.get("filterFieldType")
        self.filterValue = args.get("filterValue")
        self.filter_values = _list(args, "filterValue[]")

        self.contracts = _list(args, "customeFilterContractValue[]")
        self.users = _list(args, "customeFilterUserValue[]")
        raw_status = (_list(args, "customeFilterCommentStatusValue[]")
                      or _list(args, "customeFilterStatusesValue"))
        self.comment_statuses = [v for v in raw_status if v and v != "0"]
        self.portfolios = _list(args, "customeFilterPortfoliosValue[]")
        self.branches = _list(args, "customeFilterBranchesValue[]")
        self.attribute = args.get("customeFilterAttributeValue")

        single = args.get("customeFilterPortfolioCreateAtValue")
        self.portfolio_from = (_parse_date(args.get("customeFilterPortfolioCreateAtFromValue"))
                               or _parse_date(single))
        self.portfolio_to = (_parse_date(args.get("customeFilterPortfolioCreateAtToValue"))
                             or _parse_date(single))
        if (self.portfolio_from is not None and self.portfolio_to is not None
                and self.portfolio_from > self.portfolio_to):
            self.portfolio_from, self.portfolio_to = self.portfolio_to, self.portfolio_from

        self.excel_account_numbers = _excel_column(
            args, "customeFilterInquiryClintByExcelValue[file_name]",
            "customeFilterInquiryClintByExcelValue[index]")

        dates = _list(args, "customeFilterDateValue[]")
        self.date_from = _parse_date(dates[0]) if len(dates) > 0 else None
        self.date_to = _parse_date(dates[1]) if len(dates) > 1 else None


def apply_mui_filter(query, col_map, p, mode="clients"):
    """The generic MUI `filterOperator/filterField/filterFieldType/filterValue[/[]]` quad.

    The three legacy copies of this block are *not* identical, and the differences are
    observable, so each is reproduced exactly:

    | mode        | legacy source        | string ops apply when | column cast | isAnyOf          |
    |-------------|----------------------|-----------------------|-------------|------------------|
    | `clients`   | `_build_clints_payload` | `filterFieldType == 'string'` | none | `float()` for numbers |
    | `neglect`   | `getClintsNneglect`  | any non-`number` type | none        | raw values       |
    | `payments`  | `getClints*HavePayments` | any non-`number` type | `cast(col, String)` | raw values |

    Unknown `filterField`s (and anything unparsable) are ignored, never fatal.
    """
    if not (p.filterOperator and p.filterField):
        return query
    db_col = col_map.get(p.filterField)
    if db_col is None:
        return query

    strings_for_any_type = mode in ("neglect", "payments")
    do_cast = mode == "payments"

    if p.filterOperator == "isEmpty":
        query = query.filter(db_col.is_(None))
    elif p.filterOperator == "isNotEmpty":
        query = query.filter(db_col.isnot(None))
    elif p.filterValue is not None:
        if p.filterFieldType == "number":
            try:
                val = float(p.filterValue)
                num_ops = {
                    "=": db_col == val, "!=": db_col != val,
                    ">": db_col > val, ">=": db_col >= val,
                    "<": db_col < val, "<=": db_col <= val,
                }
                if p.filterOperator in num_ops:
                    query = query.filter(num_ops[p.filterOperator])
            except (ValueError, TypeError):
                pass
        elif strings_for_any_type or p.filterFieldType == "string":
            col = cast(db_col, String) if do_cast else db_col
            val = str(p.filterValue)
            str_ops = {
                # `contains` on `name` hits ix_clints_name_trgm (GIN trigram)
                "contains": col.ilike("%" + val + "%"),
                "equals": (col == val) if do_cast else (db_col == p.filterValue),
                "startsWith": col.ilike(val + "%"),
                "endsWith": col.ilike("%" + val),
            }
            if p.filterOperator in str_ops:
                query = query.filter(str_ops[p.filterOperator])

    if p.filter_values and p.filterOperator == "isAnyOf":
        if mode == "clients" and p.filterFieldType == "number":
            try:
                query = query.filter(db_col.in_([float(v) for v in p.filter_values]))
            except (ValueError, TypeError):
                pass
        else:
            query = query.filter(db_col.in_(p.filter_values))
    return query


# ---------------------------------------------------------------------------
# shared column lists
# ---------------------------------------------------------------------------

# `as_dict()` iterates `__table__.columns`; reproducing that order keeps the JSON key
# order identical to the legacy body, not just the key *set*.
CLINT_COLUMN_NAMES = [c.name for c in Clints.__table__.columns]
_CLINT_COLUMNS = [Clints.__table__.c[n].label("k_" + n) for n in CLINT_COLUMN_NAMES]


def _clint_dict(row):
    return {n: getattr(row, "k_" + n) for n in CLINT_COLUMN_NAMES}


def _user_block(maps, user_id):
    if not user_id:
        return None
    u = maps["users"].get(str(user_id))
    if u is None:
        return None
    return {"id": u["id"], "frist_name": u["frist_name"], "last_name": u["last_name"],
            "name": u["name"], "branch_id": u["branch_id"]}


# ---------------------------------------------------------------------------
# 1. the main client grid  (/clints, /clients/export, /clints/user/<id>)
# ---------------------------------------------------------------------------


def build_clients_query(args, identity, force_user_id=None, page_size_default=200):
    """-> (query, count_query, scope_info)

    `query` yields one row per client with every column the grid needs; `count_query`
    is the light `COUNT(*), SUM(amount)` twin (`.first()` -> `(count, total)`).
    `scope_info` is `(params, scope)` -- the caller needs `params.page/pageSize`.

    Line-for-line equivalent of `app.py:_build_clints_payload` (1972-2506).
    """
    p = ListParams(args, page_size_default=page_size_default)
    scope = resolve_scope(identity)

    # The latest comment comes from the denormalised `clints.last_comment_id`
    # (db/03_last_comment.sql, maintained by `comments_last_comment_trg`) instead of a
    # correlated `max(create_at)` + a join on that value: one PK probe per page row.
    last_specify_at = (
        db.session.query(func.max(SepcifyUsers.create_at))
        .filter(SepcifyUsers.clint_id == Clints.id, SepcifyUsers.is_deleted == False)  # noqa: E712
        .correlate(Clints).scalar_subquery()
    )

    ClientUser = aliased(User, name="client_user")
    LatestSpecify = aliased(SepcifyUsers, name="latest_specify")

    query = (
        db.session.query(
            *_CLINT_COLUMNS,
            Portfolios.id.label("p_id"),
            Portfolios.name.label("p_name"),
            Portfolios.create_at.label("p_create_at"),
            Contract.id.label("c_id"),
            Contract.name.label("c_name"),
            Comments.id.label("cm_id"),
            Comments.comment_status_id.label("cm_status_id"),
            Comments.next_contact_date.label("cm_next_contact"),
            Comments.create_at.label("cm_create_at"),
            LatestSpecify.id.label("sp_id"),
            LatestSpecify.user_id.label("sp_user_id"),
            LatestSpecify.create_at.label("sp_create_at"),
            ClintArchive.id.label("ca_id"),
            ClintArchive.user_id.label("ca_user_id"),
            ClintArchive.archive_reason_id.label("ca_reason_id"),
            ClintArchive.create_at.label("ca_create_at"),
        )
        .select_from(Clints)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .join(Contract, Portfolios.contract_id == Contract.id)
        .outerjoin(ClientUser, Clints.user_id == ClientUser.id)
        .outerjoin(Comments, Comments.id == Clints.last_comment_id)
        .outerjoin(CommentStatuses, Comments.comment_status_id == CommentStatuses.id)
        .outerjoin(LatestSpecify, and_(
            LatestSpecify.clint_id == Clints.id,
            LatestSpecify.is_deleted == False,                   # noqa: E712
            LatestSpecify.create_at == last_specify_at))
        .outerjoin(ClintArchive, Clints.clintArchive_id == ClintArchive.id)
        .filter(Clints.is_archive == False)                      # noqa: E712
    )

    scope_cond = scope_condition(scope, p.contracts)
    if scope_cond is not None:
        query = query.filter(scope_cond)
    if force_user_id is not None:
        query = query.filter(Clints.user_id == str(force_user_id))

    def custom_filters(q, user_joined=True):
        if p.contracts:
            q = q.filter(Portfolios.contract_id.in_(p.contracts))
        if p.users:
            q = q.filter(Clints.user_id.in_(p.users))
        if p.comment_statuses:
            q = q.filter(Comments.comment_status_id.in_(p.comment_statuses))
        if p.portfolios:
            q = q.filter(Clints.portfolios_id.in_(p.portfolios))
        if p.branches and user_joined:
            q = q.filter(ClientUser.branch_id.in_(p.branches))
        if p.attribute:
            q = q.filter(cast(Clints.attribute, String).contains(p.attribute))
        if p.excel_account_numbers:
            q = q.filter(Clints.accountNumber.in_(p.excel_account_numbers))
        if p.portfolio_from is not None:
            q = q.filter(func.date(Portfolios.create_at) >= p.portfolio_from)
        if p.portfolio_to is not None:
            q = q.filter(func.date(Portfolios.create_at) <= p.portfolio_to)
        return q

    query = custom_filters(query)

    col_map = {
        "amount": Clints.amount,
        "name": Clints.name,
        "accountNumber": Clints.accountNumber,
        "identityNumber": Clints.identityNumber,
        "portfolios.name": Portfolios.name,
        "contract.name": Contract.name,
        # identical value to `Comments.create_at` of `last_comment_id`, but on `clints`
        # it is index-backed (ix_clints_last_comment_live) and needs no join
        "comment.create_at": Clints.last_comment_at,
    }
    query = apply_mui_filter(query, col_map, p)

    # البروفجن مخزّن داخل حقل attribute بصيغة JSON، وقد يكون نصاً أو رقماً.
    provision_text = func.btrim(Clints.attribute[u"البروفجن"].as_string())
    provision_sort = db.case(
        (provision_text.op("~")(r"^-?[0-9]+(\.[0-9]+)?$"), cast(provision_text, db.Numeric)),
        else_=None,
    )
    sort_map = {
        "amount": Clints.amount,
        "name": Clints.name,
        "accountNumber": Clints.accountNumber,
        "identityNumber": Clints.identityNumber,
        "create_at": Clints.create_at,
        "portfolios.name": Portfolios.name,
        "contract.name": Contract.name,
        "user.frist_name": ClientUser.frist_name,
        "user.last_name": ClientUser.last_name,
        "user.name": ClientUser.frist_name,
        "comment.create_at": Clints.last_comment_at,
        "comment.next_contact_date": Comments.next_contact_date,
        "next_contact_date": Comments.next_contact_date,
        "comment_status.name": CommentStatuses.name,
        "commentStatus.name": CommentStatuses.name,
        "specifyUser.create_at": LatestSpecify.create_at,
        "specify_user.create_at": LatestSpecify.create_at,
        "provision": provision_sort,
        "provision.name": provision_sort,
    }
    # `getKey` (app.py:8342) strips a trailing "_" from every path segment, so the grids
    # send both `user.frist_name` and `user_.frist_name`. Accept both spellings.
    sort_map = _with_underscore_variants(sort_map)

    field = p.field
    if field and p.sort and field in sort_map:
        ordering = sort_map[field].desc() if p.sort == "desc" else sort_map[field].asc()
        if field in ("provision", "provision.name", "provision_.name"):
            ordering = ordering.nullslast()
        query = query.order_by(ordering)
    else:
        query = query.order_by(Clints.create_at.desc())

    # ---- count + total: light twin, only the joins the active filters need ----
    # `comment.create_at` now lives on `clints`, so only a comment-status filter still
    # needs the join.
    needs_comment = bool(p.comment_statuses)
    needs_user = bool(p.branches)

    count_query = (
        db.session.query(func.count(Clints.id), func.sum(Clints.amount))
        .select_from(Clints)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .join(Contract, Portfolios.contract_id == Contract.id)
        .filter(Clints.is_archive == False)                      # noqa: E712
    )
    if needs_user:
        count_query = count_query.outerjoin(ClientUser, Clints.user_id == ClientUser.id)
    if needs_comment:
        count_query = count_query.outerjoin(Comments, Comments.id == Clints.last_comment_id)
    if scope_cond is not None:
        count_query = count_query.filter(scope_cond)
    if force_user_id is not None:
        count_query = count_query.filter(Clints.user_id == str(force_user_id))
    count_query = custom_filters(count_query, user_joined=needs_user)
    count_query = apply_mui_filter(count_query, col_map, p)

    return query, count_query, (p, scope)


def _with_underscore_variants(mapping):
    out = dict(mapping)
    for key, col in list(mapping.items()):
        if "." in key:
            head, tail = key.split(".", 1)
            out.setdefault(head + "_." + tail, col)
    return out


def serialize_client_row(row, maps):
    """The legacy `/clints` row, key-for-key (app.py:2455-2506)."""
    d = _clint_dict(row)

    d["portfolios"] = {
        "id": str(row.p_id),
        "name": row.p_name,
        "create_at": row.p_create_at,
        "contract_id": str(row.c_id),
    }
    d["contract"] = {"id": str(row.c_id), "name": row.c_name}
    d["user"] = _user_block(maps, row.k_user_id)

    if row.cm_id:
        cs_id = str(row.cm_status_id) if row.cm_status_id else None
        d["comment"] = {
            "id": str(row.cm_id),
            "comment_status_id": cs_id,
            "next_contact_date": row.cm_next_contact,
            "create_at": row.cm_create_at,
        }
        cs = maps["comment_statuses"].get(cs_id) if cs_id else None
        d["comment_status"] = {
            "name": cs["name"] if cs else None,
            "color": cs["color"] if cs else None,
            "is_active": cs["is_active"] if cs else None,
            "CommentReachability_id": cs["CommentReachability_id"] if cs else None,
        }
    else:
        # NEVER null -- review §6: the grid reads `comment.create_at` unconditionally.
        d["comment"] = {"comment": None, "create_at": None}
        d["comment_status"] = None

    dr = maps["delinquency_reasons"].get(str(row.k_delinquencyReason_id)) \
        if row.k_delinquencyReason_id else None
    d["delinquencyReason"] = {"name": dr["name"]} if dr else None

    if row.sp_id:
        sp_user = maps["users"].get(str(row.sp_user_id)) if row.sp_user_id else None
        d["specifyUser"] = {
            "id": str(row.sp_id),
            "user_id": str(row.sp_user_id),
            "create_at": row.sp_create_at,
            "user": {
                "id": str(row.sp_user_id),
                "frist_name": sp_user["frist_name"] if sp_user else None,
                "last_name": sp_user["last_name"] if sp_user else None,
                "name": sp_user["name"] if sp_user else None,
            },
        }

    if row.ca_id:
        d["clintArchive"] = {
            "id": str(row.ca_id),
            "user_id": str(row.ca_user_id),
            "archive_reason_id": str(row.ca_reason_id),
            "create_at": row.ca_create_at,
        }
    return d


# ---------------------------------------------------------------------------
# 2. archive grid + archive export  (/clints/archive/, /clients/archive/export)
# ---------------------------------------------------------------------------


def build_archive_query(args, identity):
    """-> (query, count_query, params)

    Replaces app.py:2957-3130 / 2665-2830. Two fixes that change behaviour:

    * **bug 5** -- the legacy rows came from `clint.comments[-1]`, and the `comments`
      relationship has no `order_by`, so "latest comment" was an arbitrary comment (and it
      lazily loaded *all* of them, review P6). The subquery here is
      `DISTINCT ON (clint_id) ... ORDER BY clint_id, create_at DESC`.
    * **bug 4** -- `latest_comments.comment_status_id` never existed (it was neither
      selected nor reached through `.c`), so `SelectedCommentStatus_Qkey[]` always raised
      `AttributeError` -> 201 -> an empty grid. `comment_status_id` is now selected.
    """
    p = ListParams(args, page_size_default=1000)

    portfolio_id = args.get("SelectedPortfolios_Qkey[]")
    contract_id = args.get("selectedContracts_Qkey[]")
    account_number = args.get("selectedAccountNumber_Qkey")
    user_ids = _list(args, "selectedUsers_Qkey[]")
    archiveby = args.get("selectedArchiveBy_Qkey[]")
    archiveby_reason = args.get("SelectedResons_Qkey[]")
    comment_statuses = _list(args, "SelectedCommentStatus_Qkey[]")
    selected_clients = _excel_column(args, "selectedClients_Qkey[file_name]",
                                     "selectedClients_Qkey[index]")

    # The latest comment is `clints.last_comment_id` (db/03_last_comment.sql): a PK probe
    # per page row instead of a LATERAL descent into ix_comments_clint_created.
    def base(q, with_comment=True, with_portfolios=True, with_archive=True):
        """`with_*` let the COUNT twin drop joins no active filter needs.

        `clints.portfolios_id` / `clints.clintArchive_id` are FK columns, so the two inner
        joins can only ever drop rows whose FK is NULL -- `clintArchive_id IS NOT NULL`
        reproduces that exactly (there are 2 such rows) at 75 ms instead of 344 ms.
        """
        q = q.select_from(Clints).filter(Clints.is_archive.is_(True))
        if with_portfolios:
            q = q.join(Portfolios, Clints.portfolios_id == Portfolios.id)
        if with_archive:
            q = q.join(ClintArchive, Clints.clintArchive_id == ClintArchive.id)
        else:
            q = q.filter(Clints.clintArchive_id.isnot(None))
        if with_comment:
            q = q.outerjoin(Comments, Comments.id == Clints.last_comment_id)
        if portfolio_id:
            q = q.filter(Clints.portfolios_id == portfolio_id)
        if contract_id:
            q = q.filter(Portfolios.contract_id == contract_id)
        if account_number:
            q = q.filter(Clints.accountNumber == account_number)
        if user_ids:
            q = q.filter(Clints.user_id.in_(user_ids))
        if archiveby:
            q = q.filter(ClintArchive.user_id == archiveby)
        if archiveby_reason:
            q = q.filter(ClintArchive.archive_reason_id == archiveby_reason)
        if selected_clients:
            q = q.filter(Clints.accountNumber.in_(selected_clients))
        if with_comment and comment_statuses:
            q = q.filter(Comments.comment_status_id.in_(comment_statuses))
        return q

    query = base(db.session.query(
        Clints.id.label("k_id"),
        Clints.accountNumber.label("k_accountNumber"),
        Clints.name.label("k_name"),
        Clints.identityNumber.label("k_identityNumber"),
        Clints.amount.label("k_amount"),
        Clints.user_id.label("k_user_id"),
        Clints.portfolios_id.label("k_portfolios_id"),
        Portfolios.contract_id.label("c_id"),
        ClintArchive.create_at.label("ca_create_at"),
        ClintArchive.user_id.label("ca_user_id"),
        ClintArchive.archive_reason_id.label("ca_reason_id"),
        Comments.comment_status_id.label("cm_status_id"),
    ))
    count_query = base(db.session.query(func.count(Clints.id)),
                       with_comment=bool(comment_statuses),
                       with_portfolios=bool(contract_id),
                       with_archive=bool(archiveby or archiveby_reason))

    field = p.sort_key
    order = p.sort_order
    if field and order:
        if field == "clint_amount":
            query = query.order_by(Clints.amount.desc() if order == "desc" else Clints.amount.asc())
        elif field == "archive_create_at":
            query = query.order_by(ClintArchive.create_at.desc() if order == "desc"
                                   else ClintArchive.create_at.asc())
    # The legacy query has NO ORDER BY at all when sort[key]/sort[order] are absent (or
    # name something else), so page 1 was whatever the plan returned. A stable order is
    # required for correct paging -> `create_at DESC, id`.
    query = query.order_by(Clints.create_at.desc(), Clints.id)
    return query, count_query, p


def serialize_archive_row(row, maps):
    """The legacy archive row (app.py:3089-3128), with the relationship walk replaced by
    the batched reference maps."""
    archiver = maps["users"].get(str(row.ca_user_id)) if row.ca_user_id else None
    reason = maps["archive_reasons"].get(str(row.ca_reason_id)) if row.ca_reason_id else None
    portfolio = maps["portfolios"].get(str(row.k_portfolios_id))
    contract = maps["contracts"].get(str(row.c_id))
    owner = maps["users"].get(str(row.k_user_id)) if row.k_user_id else None
    cs = maps["comment_statuses"].get(str(row.cm_status_id)) if row.cm_status_id else None

    return {
        "portfolios": {"id": row.k_portfolios_id,
                       "name": portfolio["name"] if portfolio else None},
        "contract": {"id": row.c_id, "name": contract["name"] if contract else None},
        "clintArchive": {
            "create_at": row.ca_create_at,
            "user": {"id": row.ca_user_id,
                     "name": archiver["name"] if archiver else None},
            "archive_reason": {"id": row.ca_reason_id,
                               "name": reason["name"] if reason else None},
        },
        "comment_status": {"name": cs["name"] if cs else None,
                           "color": cs["color"] if cs else None},
        "user": {"id": row.k_user_id, "name": owner["name"] if owner else None},
        "id": row.k_id,
        "accountNumber": row.k_accountNumber,
        "name": row.k_name,
        "identityNumber": row.k_identityNumber,
        "amount": row.k_amount,
        "user_id": row.k_user_id,
    }


# ---------------------------------------------------------------------------
# 3. have_payments / not_have_payments  (+ exports)
# ---------------------------------------------------------------------------


def build_payments_flag_query(args, identity, have_payments):
    """-> (query, agg_query, params). One builder for both routes (they were 246
    identical lines apart from `HAVE_PAYMENTS`).

    **Scope change (review S5):** both legacy routes were completely unscoped -- any
    collector could page the whole 171k-client table. The caller scope from
    `/clints/neglect` is applied here. SuperAdmin results are unchanged.
    """
    p = ListParams(args, page_size_default=10, page_size_max=500)
    scope = resolve_scope(identity)

    payments_sq = (
        db.session.query(
            Payments.clint_id.label("clint_id"),
            func.count(Payments.id).label("payments_count"),
            func.coalesce(func.sum(Payments.amount), 0).label("payments_amount"),
        ).group_by(Payments.clint_id).subquery()
    )
    latest_comment = _latest_comment_lateral()
    ClientUser = aliased(User, name="client_user")
    payments_count_col = func.coalesce(payments_sq.c.payments_count, 0)

    scope_cond = scope_condition(scope, p.contracts)

    def apply_filters(q, with_comment=False):
        q = q.filter(Clints.is_archive == False)                 # noqa: E712
        if scope_cond is not None:
            q = q.filter(scope_cond)
        q = q.filter(payments_count_col > 0) if have_payments else q.filter(payments_count_col == 0)
        if p.contracts:
            q = q.filter(Portfolios.contract_id.in_(p.contracts))
        if p.users:
            q = q.filter(Clints.user_id.in_(p.users))
        if p.portfolios:
            q = q.filter(Clints.portfolios_id.in_(p.portfolios))
        if p.branches:
            q = q.filter(ClientUser.branch_id.in_(p.branches))
        if p.attribute:
            q = q.filter(cast(Clints.attribute, String).contains(p.attribute))
        if with_comment and p.comment_statuses:
            q = q.filter(latest_comment.c.comment_status_id.in_(p.comment_statuses))
        return q

    agg_query = apply_filters(
        db.session.query(func.count(Clints.id), func.coalesce(func.sum(Clints.amount), 0))
        .select_from(Clints)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .outerjoin(ClientUser, Clints.user_id == ClientUser.id)
        .outerjoin(payments_sq, Clints.id == payments_sq.c.clint_id),
        with_comment=False)

    query = apply_filters(
        db.session.query(
            *_CLINT_COLUMNS,
            Portfolios.id.label("p_id"),
            Portfolios.name.label("p_name"),
            Contract.id.label("c_id"),
            Contract.name.label("c_name"),
            latest_comment.c.id.label("cm_id"),
            latest_comment.c.comment.label("cm_text"),
            latest_comment.c.comment_status_id.label("cm_status_id"),
            latest_comment.c.next_contact_date.label("cm_next_contact"),
            latest_comment.c.create_at.label("cm_create_at"),
            payments_count_col.label("payments_count"),
            func.coalesce(payments_sq.c.payments_amount, 0).label("payments_amount"),
        )
        .select_from(Clints)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .join(Contract, Portfolios.contract_id == Contract.id)
        .outerjoin(ClientUser, Clints.user_id == ClientUser.id)
        .outerjoin(payments_sq, Clints.id == payments_sq.c.clint_id)
        .outerjoin(latest_comment, sa_text("true"))
        .outerjoin(CommentStatuses,
                   latest_comment.c.comment_status_id == CommentStatuses.id),
        with_comment=True)

    col_map = {
        "amount": Clints.amount,
        "name": Clints.name,
        "accountNumber": Clints.accountNumber,
        "identityNumber": Clints.identityNumber,
        "portfolios.name": Portfolios.name,
        "contract.name": Contract.name,
        "comment.create_at": latest_comment.c.create_at,
        "comment_status.name": CommentStatuses.name,
        "payments": payments_count_col,
    }
    query = apply_mui_filter(query, col_map, p, mode="payments")

    sort_map = _with_underscore_variants(col_map)
    if p.field in sort_map and p.sort:
        col = sort_map[p.field]
        query = query.order_by(col.desc() if p.sort == "desc" else col.asc())
    else:
        query = query.order_by(Clints.create_at.desc())
    return query, agg_query, p


def serialize_payments_flag_row(row, maps):
    """app.py:6955-7018 row, key-for-key."""
    d = _clint_dict(row)
    d["payments"] = int(row.payments_count or 0)
    d["payments_amount"] = float(row.payments_amount or 0)
    d["portfolios"] = {"id": str(row.p_id), "name": row.p_name, "contract_id": str(row.c_id)}
    d["contract"] = {"id": str(row.c_id), "name": row.c_name}
    d["user"] = _user_block(maps, row.k_user_id)

    if row.cm_id:
        cs_id = str(row.cm_status_id) if row.cm_status_id else None
        d["comment"] = {
            "id": str(row.cm_id),
            "comment": row.cm_text,
            "comment_status_id": cs_id,
            "next_contact_date": row.cm_next_contact,
            "create_at": row.cm_create_at,
        }
        cs = maps["comment_statuses"].get(cs_id) if cs_id else None
        d["comment_status"] = {
            "id": cs_id,
            "name": cs["name"] if cs else None,
            "color": cs["color"] if cs else None,
        }
    else:
        d["comment"] = {"comment": None, "create_at": None}
        d["comment_status"] = None
    return d


# ---------------------------------------------------------------------------
# 4. neglect report  (/clints/neglect + /clints/neglect/export)
# ---------------------------------------------------------------------------


def build_neglect_query(args, identity):
    """-> (query, agg_query, params). Copy of `getClintsNneglect` (app.py:7342-7627), so
    the paged route and the export share one definition instead of the export going
    through `fetchClients()` -> `redis KEYS` -> `exportServerSideMode`."""
    p = ListParams(args, page_size_default=100, page_size_max=500)
    scope = resolve_scope(identity)

    # The neglect report *orders every client in scope* by their last follow-up, so the
    # ordering key is needed for all ~175k rows. It is now the denormalised
    # `clints.last_comment_at` (db/03_last_comment.sql) instead of a `GROUP BY clint_id ->
    # max(create_at)` over 1.27M comments (300-800 ms) plus a second join on that value.
    ClientUser = aliased(User, name="client_user")
    scope_cond = scope_condition(scope, p.contracts)

    def common(q, with_user_join):
        if scope_cond is not None:
            q = q.filter(scope_cond)
        if p.contracts:
            q = q.filter(Portfolios.contract_id.in_(p.contracts))
        if p.users:
            q = q.filter(Clints.user_id.in_(p.users))
        if p.portfolios:
            q = q.filter(Clints.portfolios_id.in_(p.portfolios))
        if p.attribute:
            q = q.filter(cast(Clints.attribute, String).contains(p.attribute))
        if with_user_join and p.branches:
            q = q.filter(ClientUser.branch_id.in_(p.branches))
        return q

    # `clints.portfolios_id` is NOT NULL + FK, so the inner join to portfolios cannot
    # drop a row; it is only added when a filter actually reads a portfolios column.
    # Same for the user outer join (an outer join never drops rows).
    agg_base = (db.session.query(func.count(Clints.id),
                                 func.coalesce(func.sum(Clints.amount), 0))
                .select_from(Clints))
    if p.contracts:
        agg_base = agg_base.join(Portfolios, Clints.portfolios_id == Portfolios.id)
    if p.branches:
        agg_base = agg_base.outerjoin(ClientUser, Clints.user_id == ClientUser.id)
    agg_query = common(agg_base.filter(Clints.is_archive == False),  # noqa: E712
                       bool(p.branches))

    query = common(
        db.session.query(
            *_CLINT_COLUMNS,
            Portfolios.id.label("p_id"),
            Portfolios.name.label("p_name"),
            Contract.id.label("c_id"),
            Contract.name.label("c_name"),
            Comments.id.label("cm_id"),
            Comments.comment.label("cm_text"),
            Comments.comment_status_id.label("cm_status_id"),
            Comments.next_contact_date.label("cm_next_contact"),
            Comments.create_at.label("cm_create_at"),
            Clints.last_comment_at.label("last_follow_up"),
        )
        .select_from(Clints)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .join(Contract, Portfolios.contract_id == Contract.id)
        .outerjoin(ClientUser, Clints.user_id == ClientUser.id)
        .outerjoin(Comments, Comments.id == Clints.last_comment_id)
        .outerjoin(CommentStatuses, Comments.comment_status_id == CommentStatuses.id)
        .filter(Clints.is_archive == False),                     # noqa: E712
        True)
    if p.comment_statuses:
        query = query.filter(Comments.comment_status_id.in_(p.comment_statuses))

    col_map = {
        "amount": Clints.amount,
        "name": Clints.name,
        "accountNumber": Clints.accountNumber,
        "identityNumber": Clints.identityNumber,
        "portfolios.name": Portfolios.name,
        "contract.name": Contract.name,
        "comment.create_at": Clints.last_comment_at,
    }
    query = apply_mui_filter(query, col_map, p, mode="neglect")

    sort_map = dict(col_map)
    sort_map["last_follow_up"] = Clints.last_comment_at
    sort_map["neglect_days"] = Clints.last_comment_at
    sort_map["comment_status.name"] = CommentStatuses.name
    sort_map = _with_underscore_variants(sort_map)

    if p.field in sort_map and p.sort:
        col = sort_map[p.field]
        if p.field in ("neglect_days", "neglect_days_"):
            # اكثر اهمالا = اقدم تاريخ متابعة
            query = query.order_by(col.asc() if p.sort == "desc" else col.desc())
        else:
            query = query.order_by(col.desc() if p.sort == "desc" else col.asc())
    else:
        query = query.order_by(Clints.last_comment_at.asc())
    return query, agg_query, p


def serialize_neglect_row(row, maps, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    d = _clint_dict(row)
    d["portfolios"] = {"id": str(row.p_id), "name": row.p_name, "contract_id": str(row.c_id)}
    d["contract"] = {"id": str(row.c_id), "name": row.c_name}
    d["user"] = _user_block(maps, row.k_user_id)

    if row.cm_id:
        cs_id = str(row.cm_status_id) if row.cm_status_id else None
        d["comment"] = {
            "id": str(row.cm_id),
            "comment": row.cm_text,
            "comment_status_id": cs_id,
            "next_contact_date": row.cm_next_contact,
            "create_at": row.cm_create_at,
        }
        cs = maps["comment_statuses"].get(cs_id) if cs_id else None
        d["comment_status"] = {"id": cs_id,
                               "name": cs["name"] if cs else None,
                               "color": cs["color"] if cs else None}
    else:
        d["comment"] = {"comment": None, "create_at": None}
        d["comment_status"] = None

    last_fu = row.last_follow_up
    d["last_follow_up"] = last_fu
    if last_fu is not None:
        lf = last_fu if last_fu.tzinfo is not None else last_fu.replace(tzinfo=datetime.timezone.utc)
        d["neglect_days"] = (now - lf).days
    else:
        d["neglect_days"] = None
    return d


# ---------------------------------------------------------------------------
# 5. my next calls  (/clints/currentUser/have_nextCalls + export)
# ---------------------------------------------------------------------------


def build_next_calls_query(args, identity, latest_only=False):
    """-> (query, agg_query, params).

    Legacy (app.py:7650-7705 paged / 7708-7755 export) pulled the caller's clients out of
    Redis (`KEYS *<user_id>*`) and then ran one `Comments` query per client. The two
    legacy variants disagree and both are reproduced:

    * paged  -> **one row per comment** whose `next_contact_date` falls in the range
      (a client with three upcoming calls appeared three times);
    * export -> only the client's **latest** comment, and only if *its* next call is in
      range (`latest_only=True`).

    `customeFilterDateValue[]=<from>&customeFilterDateValue[]=<to>` bounds the range;
    legacy did `dates[0]`/`dates[1]` unguarded, so calling the endpoint without them
    raised `IndexError` -> 201 (that is the 201 in `logs/golden_before.log` for the ADMIN
    user, who simply has no clients of their own). Missing/unparsable dates now just drop
    that half of the range.
    """
    p = ListParams(args, page_size_default=200)

    if latest_only:
        # one LATERAL per client -> index-only walk of ix_comments_clint_created; a plain
        # DISTINCT ON subquery would sort all 1.27M comments for the ~50 rows we need.
        # NB: legacy did not filter `is_deleted` here, so neither do we.
        latest = (
            db.session.query(
                Comments.id.label("id"),
                Comments.comment.label("comment"),
                Comments.comment_status_id.label("comment_status_id"),
                Comments.next_contact_date.label("next_contact_date"),
                Comments.create_at.label("create_at"),
            )
            .filter(Comments.clint_id == Clints.id)
            .order_by(Comments.create_at.desc())
            .limit(1)
            .subquery()
            .lateral("latest_comment")
        )
        cmt = latest.c
        target, join_cond = latest, sa_text("true")
    else:
        cmt = Comments
        target, join_cond = Comments, Comments.clint_id == Clints.id

    date_conds = [cmt.next_contact_date.isnot(None)]
    if p.date_from is not None:
        date_conds.append(func.date(cmt.next_contact_date) >= p.date_from)
    if p.date_to is not None:
        date_conds.append(func.date(cmt.next_contact_date) <= p.date_to)

    # "ايام الاسناد" in the export workbook is computed from `specifyUser.create_at`
    # (CAWP-frontend/src/components/shared/myComponents/exportCurrentUserNextCalls.tsx:26),
    # which the legacy rows carried because they came out of `updateClintInRedis`.
    latest_specify = _latest_specify_lateral()

    def base(q, with_specify=False):
        q = (q.select_from(Clints)
              .join(Portfolios, Clints.portfolios_id == Portfolios.id)
              .join(Contract, Portfolios.contract_id == Contract.id)
              .join(target, join_cond)
              .filter(Clints.is_archive == False)                # noqa: E712
              .filter(Clints.user_id == str(identity)))
        if with_specify:
            q = q.outerjoin(latest_specify, sa_text("true"))
        for c in date_conds:
            q = q.filter(c)
        return q

    query = base(db.session.query(
        *_CLINT_COLUMNS,
        Portfolios.id.label("p_id"),
        Portfolios.name.label("p_name"),
        Portfolios.create_at.label("p_create_at"),
        Contract.id.label("c_id"),
        Contract.name.label("c_name"),
        cmt.id.label("cm_id"),
        cmt.comment.label("cm_text"),
        cmt.comment_status_id.label("cm_status_id"),
        cmt.next_contact_date.label("cm_next_contact"),
        cmt.create_at.label("cm_create_at"),
        cmt.next_contact_date.label("next_call"),
        latest_specify.c.id.label("sp_id"),
        latest_specify.c.user_id.label("sp_user_id"),
        latest_specify.c.create_at.label("sp_create_at"),
    ), with_specify=True)
    agg_query = base(db.session.query(func.count(Clints.id),
                                      func.coalesce(func.sum(Clints.amount), 0)))

    sort_map = _with_underscore_variants({
        "amount": Clints.amount,
        "name": Clints.name,
        "accountNumber": Clints.accountNumber,
        "identityNumber": Clints.identityNumber,
        "portfolios.name": Portfolios.name,
        "contract.name": Contract.name,
        "next_call": cmt.next_contact_date,
        "comment.next_contact_date": cmt.next_contact_date,
        "comment.create_at": cmt.create_at,
    })
    if p.field in sort_map and p.sort:
        col = sort_map[p.field]
        query = query.order_by(col.desc() if p.sort == "desc" else col.asc())
    # The legacy list came out of a Redis `mget` in key order, i.e. no defined order at
    # all. `create_at DESC, id` is the stable default used everywhere else here.
    query = query.order_by(Clints.create_at.desc(), Clints.id)
    return query, agg_query, p


def serialize_next_call_row(row, maps):
    d = _clint_dict(row)
    d["portfolios"] = {"id": str(row.p_id), "name": row.p_name,
                       "create_at": row.p_create_at, "contract_id": str(row.c_id)}
    d["contract"] = {"id": str(row.c_id), "name": row.c_name}
    d["user"] = _user_block(maps, row.k_user_id)
    cs_id = str(row.cm_status_id) if row.cm_status_id else None
    d["comment"] = {
        "id": str(row.cm_id),
        "comment": row.cm_text,
        "comment_status_id": cs_id,
        "next_contact_date": row.cm_next_contact,
        "create_at": row.cm_create_at,
    }
    cs = maps["comment_statuses"].get(cs_id) if cs_id else None
    d["comment_status"] = ({"name": cs["name"], "color": cs["color"],
                            "is_active": cs["is_active"],
                            "CommentReachability_id": cs["CommentReachability_id"]}
                           if cs else None)
    dr = maps["delinquency_reasons"].get(str(row.k_delinquencyReason_id)) \
        if row.k_delinquencyReason_id else None
    d["delinquencyReason"] = {"name": dr["name"]} if dr else None
    if row.sp_id:
        sp_user = maps["users"].get(str(row.sp_user_id)) if row.sp_user_id else None
        d["specifyUser"] = {
            "id": str(row.sp_id),
            "user_id": str(row.sp_user_id),
            "create_at": row.sp_create_at,
            "user": {
                "id": str(row.sp_user_id),
                "frist_name": sp_user["frist_name"] if sp_user else None,
                "last_name": sp_user["last_name"] if sp_user else None,
                "name": sp_user["name"] if sp_user else None,
            },
        }
    d["next_call"] = row.next_call
    return d


# ---------------------------------------------------------------------------
# 6. Redis client cache -- placeholder
# ---------------------------------------------------------------------------


def invalidate_client_cache(clint_id):
    """No-op placeholder for `app.py:updateClintInRedis` (review P2).

    Once the legacy routes this module supersedes are deleted at integration, **nothing**
    reads the `clint:<id>:<user_id>:<portfolios_id>:<contract_id>` Redis keys any more:
    every remaining reader (`/clints/*/export`, `/clints/user/<id>`,
    `/clints/currentUser/have_nextCalls*`, `fetchClients*`, `getClintsBySupervisor`,
    `fetchClientsForUser`) is replaced by a SQL builder in this module.

    `updateClintInRedis` costs ~9 queries + a `KEYS clint:<id>*` scan + an untTL'd `SET`
    per client and is called from 18 places (listed in review/backend-b1-report.md),
    including once per row of every Excel import. The integrator should replace those 18
    calls with this function and delete `updateClintInRedis`; it is kept as a hook rather
    than removed outright so the call sites stay greppable and a future write-through
    cache has somewhere to live.
    """
    return None


__all__ = [
    "ListParams", "apply_mui_filter", "all_maps", "invalidate_reference_maps",
    "build_clients_query", "serialize_client_row",
    "build_archive_query", "serialize_archive_row",
    "build_payments_flag_query", "serialize_payments_flag_row",
    "build_neglect_query", "serialize_neglect_row",
    "build_next_calls_query", "serialize_next_call_row",
    "invalidate_client_cache",
]
