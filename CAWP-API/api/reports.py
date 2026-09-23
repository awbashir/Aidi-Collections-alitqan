"""Comments / payments / promise-to-pay reports — SQL-side paging, sorting and filtering.

Supersedes (legacy line numbers in the *current* app.py):

    /comments                          3537-3607      (FULL-LOAD -> python)
    /comments/export                   3611-3684      (FULL-LOAD + N+1 reachability)
    /comments/current_user             3689-3745      (FULL-LOAD + 7 q/row)
    /comments/current_user/export      3749-3803      (FULL-LOAD + 7 q/row)
    /comments/clint/<id>               3821-3846      (4 q/row)
    /payments                          3970-4083      (FULL-LOAD, batched maps)
    /payments/user                     4087-4137      (FULL-LOAD + 6 q/row)
    /payments/user/export              4140-4191      (FULL-LOAD + 6 q/row)
    /payments/export                   4194-4288      (FULL-LOAD + 6 q/row)
    /payments/approval_status          4343-4458      (FULL-LOAD, batched maps)
    /payments/approval_status/export   4461-4571      (FULL-LOAD, batched maps)
    /payments/clint/<id>               4669-4687      (3 q/row)
    /payments/clint/<id>/total         4690-4703      (python SUM)
    /payment_date                      4774-4818      (FULL-LOAD + 8 q/row, one run twice)
    /payment_date/export               4821-4864      (same)
    /payment_date/currentUser          4867-4916      (same)
    /payment_date/currentUser/export   4919-4963      (same)
    /payment_date/user                 4966-4989      (1 q/client + 2 q/PTP)
    /payment_date/clint/<id>           4754-4771      (2 q/row)
    /contracts                         1764-1800      (2 aggregate q per contract)
    /uesrs/export                      1083-1166      (get_supervisor 1+2N, counts per user)
    /supervisors                       5405-5440      (1 + S*(...))
    /comment_status                    3290-3320      (contract + reachability per row)

Row shapes are copies of the legacy enrichment loops, *including* their inconsistencies:
`/comments` nests `portfolios` inside `clint` and repeats it at the top level while
`/comments/export` only emits the top-level one; `/comments/clint/<id>` emits a `user`
without the `name` concatenation; `/payments` runs every row through the
`_wp_normalize` defaults while `/payments/export` does not. Those differences are the
wire contract, so they are reproduced rather than unified.
"""
import datetime
import logging

from flask import Blueprint, Response, request, stream_with_context
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func, select, true
from sqlalchemy.orm import aliased

from core.http import error_response
from extensions import db
from models import (Branch, Clints, CollectionMethods, CollectionTypes, CommentReachability,
                    Comments, CommentStatuses, ContactMethods, Contract, Nationality,
                    PaymentDates, Payments, Portfolios, Role, Supervisor, SupervisorContract,
                    SupervisorUser, User)
from services.report_scope import payments_scope_clause, supervisor_scope
from services.reports_query import (apply_all_filters, apply_branch2_filter, apply_sort,
                                    between_range, count_rows, date_range,
                                    inclusive_day_range, model_cols, page_slice,
                                    col_pairs, parse_paging, safe_jsonify, stream_json,
                                    sum_column, take, with_name)

log = logging.getLogger("cawp.api.reports")

bp = Blueprint("reports", __name__)
URL_PREFIX = None
EXPORT_YIELD = 2000
MSG_EXPORT = "تم تحميل الملف بنجاح"
MSG_EXPORT_SPACED = " تم تحميل الملف بنجاح"   # /payment_date/currentUser/export, app.py:4963


# --------------------------------------------------------------------------- #
# `_wp_*` defaults, copied from app.py:3482-3533 (patch_white_page_v1)
# --------------------------------------------------------------------------- #
def _empty_contract():
    return {"id": None, "name": "", "contract_name": ""}


def _empty_portfolio():
    p = {"id": None, "name": "", "portfolios_name": ""}
    p["contract"] = _empty_contract()
    return p


def _empty_user():
    return {"id": None, "name": "", "frist_name": "", "last_name": "", "branch_id": None}


def _empty_clint():
    c = {"id": None, "name": "", "clint_name": "", "portfolios_id": None}
    c["portfolios"] = _empty_portfolio()
    return c


def _normalize(row, kind):
    if not isinstance(row.get("user"), dict):
        row["user"] = _empty_user()
    if not isinstance(row.get("clint"), dict):
        row["clint"] = _empty_clint()
    if not isinstance(row["clint"].get("portfolios"), dict):
        row["clint"]["portfolios"] = _empty_portfolio()
    if not isinstance(row["clint"]["portfolios"].get("contract"), dict):
        row["clint"]["portfolios"]["contract"] = _empty_contract()
    if not isinstance(row.get("portfolios"), dict):
        row["portfolios"] = row["clint"]["portfolios"]
    if not isinstance(row["portfolios"].get("contract"), dict):
        row["portfolios"]["contract"] = _empty_contract()
    if kind == "comment":
        if not isinstance(row.get("status"), dict):
            row["status"] = {"id": None, "name": ""}
        if not isinstance(row.get("contact_method"), dict):
            row["contact_method"] = {"id": None, "name": ""}
        if not isinstance(row.get("reachability"), dict):
            row["reachability"] = {"id": None, "name": ""}
    else:
        if not isinstance(row.get("collection_type"), dict):
            row["collection_type"] = {"id": None, "name": ""}
        if not isinstance(row.get("collection_method"), dict):
            row["collection_method"] = {"id": None, "name": ""}
        if not isinstance(row.get("approval_by"), dict):
            row["approval_by"] = _empty_user()
    return row


# --------------------------------------------------------------------------- #
# column-map helper
# --------------------------------------------------------------------------- #
def _paths(col_map, prefixes):
    """Register `<path>.<column>` for every (path, entity) pair."""
    for path, entity in prefixes:
        for attr_key, name in col_pairs(entity):
            key = "%s.%s" % (path, name) if path else name
            col_map.setdefault(key, getattr(entity, attr_key))
    return col_map


def _full_name(u, strip=False):
    expr = func.concat(func.coalesce(u.frist_name, ""), " ", func.coalesce(u.last_name, ""))
    return func.btrim(expr) if strip else expr


# =========================================================================== #
#  COMMENTS
# =========================================================================== #
C_U = aliased(User, name="cu")
C_CS = aliased(CommentStatuses, name="ccs")
C_CM = aliased(ContactMethods, name="ccm")
C_CR = aliased(CommentReachability, name="ccr")
C_CL = aliased(Clints, name="ccl")
C_PF = aliased(Portfolios, name="cpf")
C_CT = aliased(Contract, name="cct")

COMMENT_COLS = (model_cols(Comments, "c") + model_cols(C_U, "u") + model_cols(C_CS, "cs")
                + model_cols(C_CM, "cm") + model_cols(C_CR, "cr") + model_cols(C_CL, "cl")
                + model_cols(C_PF, "pf") + model_cols(C_CT, "ct"))


def _comment_map():
    m = {}
    _paths(m, [("", Comments), ("user", C_U), ("status", C_CS), ("contact_method", C_CM),
               ("reachability", C_CR), ("clint", C_CL),
               ("clint.portfolios", C_PF), ("portfolios", C_PF),
               ("clint.portfolios.contract", C_CT), ("portfolios.contract", C_CT)])
    m["user.name"] = _full_name(C_U, strip=True)
    # legacy also exposes `comment_status`/`commentStatuses` spellings through getKey
    _paths(m, [("comment_status", C_CS), ("commentStatuses", C_CS)])
    return m


COMMENT_MAP = _comment_map()
COMMENT_DEFAULT_ORDER = (Comments.create_at.desc(), Comments.id)


def _comments_query():
    return (db.session.query(*COMMENT_COLS)
            .select_from(Comments)
            .outerjoin(C_U, Comments.user_id == C_U.id)
            .outerjoin(C_CS, Comments.comment_status_id == C_CS.id)
            .outerjoin(C_CM, Comments.contactMethod_id == C_CM.id)
            .outerjoin(C_CR, Comments.comment_reachability_id == C_CR.id)
            .outerjoin(C_CL, Comments.clint_id == C_CL.id)
            .outerjoin(C_PF, C_CL.portfolios_id == C_PF.id)
            .outerjoin(C_CT, C_PF.contract_id == C_CT.id))


def _serialize_comment_list(row):
    """app.py:3569-3598 — `/comments` (includes the `_wp_normalize` defaults)."""
    d = take(row, Comments, "c")
    d["user"] = with_name(take(row, C_U, "u"), strip=True) or _empty_user()
    cs = take(row, C_CS, "cs")
    if cs:
        d["status"] = cs
    cm = take(row, C_CM, "cm")
    if cm:
        d["contact_method"] = cm
    cr = take(row, C_CR, "cr")
    if cr:
        d["reachability"] = cr
    clint = take(row, C_CL, "cl")
    if clint:
        pf = take(row, C_PF, "pf")
        if pf:
            ct = take(row, C_CT, "ct")
            if ct:
                pf["contract"] = ct
            clint["portfolios"] = pf
            d["portfolios"] = pf          # legacy stores the *same* dict in both places
        d["clint"] = clint
    return _normalize(d, "comment")


def _serialize_comment_export(row):
    """app.py:3636-3672 — `/comments/export`: no `_wp_normalize`, and `clint` carries
    no nested `portfolios` (only the top-level key does)."""
    d = take(row, Comments, "c")
    u = take(row, C_U, "u")
    if u:
        d["user"] = with_name(u)
    cs = take(row, C_CS, "cs")
    if cs:
        d["status"] = cs
    cm = take(row, C_CM, "cm")
    if cm:
        d["contact_method"] = cm
    if getattr(row, "c__comment_reachability_id", None):
        cr = take(row, C_CR, "cr")
        if cr:
            d["reachability"] = cr
    clint = take(row, C_CL, "cl")
    if clint:
        d["clint"] = clint
        pf = take(row, C_PF, "pf")
        if pf:
            ct = take(row, C_CT, "ct")
            if ct:
                pf["contract"] = ct
            d["portfolios"] = pf
    return d


def _serialize_comment_current_user(row):
    """app.py:3704-3736 — `/comments/current_user` (+ its export). Key order matches."""
    d = take(row, Comments, "c")
    if getattr(row, "c__comment_reachability_id", None) is not None:
        d["reachability"] = take(row, C_CR, "cr")
    d["status"] = take(row, C_CS, "cs")
    d["user"] = with_name(take(row, C_U, "u"))
    d["contact_method"] = take(row, C_CM, "cm")
    d["clint"] = take(row, C_CL, "cl")
    pf = take(row, C_PF, "pf")
    if pf:
        pf["contract"] = take(row, C_CT, "ct")
        d["portfolios"] = pf
    return d


def _comments_range_filter(dates):
    return inclusive_day_range(Comments.create_at, dates[0], dates[1])


@bp.route("/comments", methods=["GET"])
@jwt_required()
def get_comments():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب التعليقات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)

        q = _comments_query().filter(Comments.is_deleted == False,
                                     _comments_range_filter(dates))
        q = apply_all_filters(q, request, COMMENT_MAP)
        count = count_rows(q)
        q = apply_sort(q, request, COMMENT_MAP, COMMENT_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_comment_list(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)
    return safe_jsonify({"comments": payload, "countRow": count}), 200


@bp.route("/comments/export", methods=["GET"])
@jwt_required()
def export_comments():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب التعليقات", IndexError("list index out of range"))
        q = _comments_query().filter(Comments.is_deleted == False,
                                     _comments_range_filter(dates))
        q = apply_all_filters(q, request, COMMENT_MAP, export=True)
        q = apply_sort(q, request, COMMENT_MAP, COMMENT_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)

    def gen():
        for chunk in stream_json(
            "comments",
            (_serialize_comment_export(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/comments/current_user", methods=["GET"])
@jwt_required()
def get_comments_current_user():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب التعليقات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        identity = get_jwt_identity()

        q = (_comments_query()
             .filter(Comments.is_deleted == False,
                     _comments_range_filter(dates),
                     Comments.user_id == identity))
        q = apply_all_filters(q, request, COMMENT_MAP)
        count = count_rows(q)
        total_amount = sum_column(q, C_CL.amount) if count else 0
        q = apply_sort(q, request, COMMENT_MAP, COMMENT_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_comment_current_user(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)
    return safe_jsonify({"comments": payload, "countRow": count, "totalAmount": total_amount}), 200


@bp.route("/comments/current_user/export", methods=["GET"])
@jwt_required()
def export_comments_current_user():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب التعليقات", IndexError("list index out of range"))
        identity = get_jwt_identity()
        q = (_comments_query()
             .filter(Comments.is_deleted == False,
                     _comments_range_filter(dates),
                     Comments.user_id == identity))
        q = apply_all_filters(q, request, COMMENT_MAP, export=True)
        q = apply_sort(q, request, COMMENT_MAP, COMMENT_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)

    def gen():
        for chunk in stream_json(
            "comments",
            (_serialize_comment_current_user(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/comments/clint/<id>", methods=["GET"])
@jwt_required()
def get_comments_by_clint(id):
    """Client timeline. jwt-only in the legacy route (no `check_client_lock`, which is a
    write-path decorator returning 423); review S5 flags the missing ownership check but
    tightening it here would be a behaviour change, so it is reported, not applied."""
    try:
        cols = (model_cols(Comments, "c") + model_cols(C_U, "u") + model_cols(C_CS, "cs")
                + model_cols(C_CM, "cm") + model_cols(C_CR, "cr"))
        rows = (db.session.query(*cols)
                .select_from(Comments)
                .outerjoin(C_U, Comments.user_id == C_U.id)
                .outerjoin(C_CS, Comments.comment_status_id == C_CS.id)
                .outerjoin(C_CM, Comments.contactMethod_id == C_CM.id)
                .outerjoin(C_CR, Comments.comment_reachability_id == C_CR.id)
                .filter(Comments.clint_id == id)
                .order_by(Comments.create_at.desc(), Comments.id)
                .all())
        payload = []
        for row in rows:
            d = take(row, Comments, "c")
            cr = take(row, C_CR, "cr")
            if cr is not None:
                d["reachability"] = cr
            d["status"] = take(row, C_CS, "cs")
            d["user"] = take(row, C_U, "u")          # legacy adds no `name` here
            d["contact_method"] = take(row, C_CM, "cm")
            payload.append(d)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)
    return safe_jsonify({"comments": payload}), 200


# =========================================================================== #
#  PAYMENTS
# =========================================================================== #
P_U = aliased(User, name="pu")
P_AB = aliased(User, name="pab")
P_KT = aliased(CollectionTypes, name="pkt")
P_KM = aliased(CollectionMethods, name="pkm")
P_CL = aliased(Clints, name="pcl")
P_PF = aliased(Portfolios, name="ppf")
P_CT = aliased(Contract, name="pct")

PAYMENT_COLS = (model_cols(Payments, "p") + model_cols(P_U, "u") + model_cols(P_AB, "ab")
                + model_cols(P_KT, "kt") + model_cols(P_KM, "km") + model_cols(P_CL, "cl")
                + model_cols(P_PF, "pf") + model_cols(P_CT, "ct"))


def _payment_map():
    m = {}
    _paths(m, [("", Payments), ("user", P_U), ("approval_by", P_AB),
               ("collection_type", P_KT), ("collection_method", P_KM), ("clint", P_CL),
               ("clint.portfolios", P_PF), ("portfolios", P_PF),
               ("clint.portfolios.contract", P_CT), ("portfolios.contract", P_CT)])
    m["user.name"] = _full_name(P_U, strip=True)
    m["approval_by.name"] = _full_name(P_AB, strip=True)
    return m


PAYMENT_MAP = _payment_map()
PAYMENT_DEFAULT_ORDER = (Payments.create_at.desc(), Payments.id)


def _payments_query():
    return (db.session.query(*PAYMENT_COLS)
            .select_from(Payments)
            .outerjoin(P_U, Payments.user_id == P_U.id)
            .outerjoin(P_AB, Payments.approval_by == P_AB.id)
            .outerjoin(P_KT, Payments.collection_type_id == P_KT.id)
            .outerjoin(P_KM, Payments.collection_method_id == P_KM.id)
            .outerjoin(P_CL, Payments.clint_id == P_CL.id)
            .outerjoin(P_PF, P_CL.portfolios_id == P_PF.id)
            .outerjoin(P_CT, P_PF.contract_id == P_CT.id))


def _serialize_payment_list(row):
    """app.py:4046-4073 — `/payments` (with `_wp_normalize`)."""
    d = take(row, Payments, "p")
    kt = take(row, P_KT, "kt")
    if kt:
        d["collection_type"] = kt
    km = take(row, P_KM, "km")
    if km:
        d["collection_method"] = km
    clint = take(row, P_CL, "cl")
    if clint:
        pf = take(row, P_PF, "pf")
        if pf:
            ct = take(row, P_CT, "ct")
            if ct:
                pf["contract"] = ct
            clint["portfolios"] = pf
        d["clint"] = clint
    d["user"] = with_name(take(row, P_U, "u"), strip=True) or _empty_user()
    ab = take(row, P_AB, "ab")
    if ab:
        d["approval_by"] = with_name(ab, strip=True)
    return _normalize(d, "payment")


def _serialize_payment_plain(row):
    """app.py:4100-4135 — `/payments/user`, `/payments/export`,
    `/payments/approval_status` and their exports. No `_wp_normalize`, no top-level
    `portfolios`; `approval_by` stays the raw UUID when the payment is unapproved."""
    d = take(row, Payments, "p")
    d["collection_type"] = take(row, P_KT, "kt")
    d["collection_method"] = take(row, P_KM, "km")
    clint = take(row, P_CL, "cl")
    d["clint"] = clint
    if clint is not None and clint.get("portfolios_id") is not None:
        pf = take(row, P_PF, "pf")
        if pf is not None:
            pf["contract"] = take(row, P_CT, "ct")
            clint["portfolios"] = pf
    d["user"] = with_name(take(row, P_U, "u"))
    if d.get("approval_by") is not None:
        ab = with_name(take(row, P_AB, "ab"))
        if ab is not None:
            d["approval_by"] = ab
    return d


def _payments_base(scope, dates, approved_only=False):
    q = _payments_query().filter(Payments.is_deleted == False,
                                 between_range(Payments.payment_date, dates[0], dates[1]))
    if approved_only:
        q = q.filter(Payments.approval_status == True)
    q = q.filter(payments_scope_clause(scope, Payments, P_PF))
    return q


@bp.route("/payments", methods=["GET"])
@jwt_required()
def get_payments():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        scope = supervisor_scope()

        q = _payments_base(scope, dates)
        q = apply_branch2_filter(q, request, PAYMENT_MAP)
        q = apply_all_filters(q, request, PAYMENT_MAP)
        count = count_rows(q)
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_payment_list(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)
    return safe_jsonify({"payments": payload, "countRow": count}), 200


@bp.route("/payments/export", methods=["GET"])
@jwt_required()
def export_payments():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        scope = supervisor_scope()
        q = _payments_base(scope, dates)
        q = apply_branch2_filter(q, request, PAYMENT_MAP)
        q = apply_all_filters(q, request, PAYMENT_MAP, export=True)
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)

    def gen():
        for chunk in stream_json(
            "payments",
            (_serialize_payment_plain(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/payments/user", methods=["GET"])
@jwt_required()
def get_payments_user():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        identity = get_jwt_identity()

        q = (_payments_query()
             .filter(Payments.is_deleted == False,
                     between_range(Payments.payment_date, dates[0], dates[1]),
                     Payments.user_id == identity,
                     Payments.approval_status == True))
        q = apply_all_filters(q, request, PAYMENT_MAP)
        count = count_rows(q)
        total_amount = sum_column(q, Payments.amount) if count else 0
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_payment_plain(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)
    return safe_jsonify({"payments": payload, "countRow": count, "totalAmount": total_amount}), 200


@bp.route("/payments/user/export", methods=["GET"])
@jwt_required()
def export_payments_user():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        identity = get_jwt_identity()
        q = (_payments_query()
             .filter(Payments.is_deleted == False,
                     between_range(Payments.payment_date, dates[0], dates[1]),
                     Payments.user_id == identity,
                     Payments.approval_status == True))
        q = apply_all_filters(q, request, PAYMENT_MAP, export=True)
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)

    def gen():
        for chunk in stream_json(
            "payments",
            (_serialize_payment_plain(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/payments/approval_status", methods=["GET"])
@jwt_required()
def get_payments_approval_status():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        scope = supervisor_scope()

        q = _payments_base(scope, dates, approved_only=True)
        q = apply_branch2_filter(q, request, PAYMENT_MAP)
        q = apply_all_filters(q, request, PAYMENT_MAP)
        count = count_rows(q)
        total_amount = sum_column(q, Payments.amount) if count else 0
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_payment_plain(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)
    return safe_jsonify({"payments": payload, "countRow": count, "totalAmount": total_amount}), 200


@bp.route("/payments/approval_status/export", methods=["GET"])
@jwt_required()
def export_payments_approval_status():
    try:
        dates = date_range(request, "customeFilterMoveDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب الدفعات", IndexError("list index out of range"))
        scope = supervisor_scope()
        q = _payments_base(scope, dates, approved_only=True)
        q = apply_branch2_filter(q, request, PAYMENT_MAP)
        q = apply_all_filters(q, request, PAYMENT_MAP, export=True)
        q = apply_sort(q, request, PAYMENT_MAP, PAYMENT_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)

    def gen():
        for chunk in stream_json(
            "payments",
            (_serialize_payment_plain(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/payments/clint/<id>", methods=["GET"])
@jwt_required()
def get_payments_by_clint(id):
    try:
        cols = (model_cols(Payments, "p") + model_cols(P_U, "u")
                + model_cols(P_KT, "kt") + model_cols(P_KM, "km"))
        rows = (db.session.query(*cols)
                .select_from(Payments)
                .outerjoin(P_U, Payments.user_id == P_U.id)
                .outerjoin(P_KT, Payments.collection_type_id == P_KT.id)
                .outerjoin(P_KM, Payments.collection_method_id == P_KM.id)
                .filter(Payments.clint_id == id)
                .order_by(Payments.create_at.desc(), Payments.id)
                .all())
        payload = []
        for row in rows:
            d = take(row, Payments, "p")
            d["collection_type"] = take(row, P_KT, "kt")
            d["collection_method"] = take(row, P_KM, "km")
            d["user"] = take(row, P_U, "u")          # legacy adds no `name` here
            payload.append(d)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)
    return safe_jsonify({"payments": payload}), 200


@bp.route("/payments/clint/<id>/total", methods=["GET"])
@jwt_required()
def get_payments_total_by_clint(id):
    try:
        total = (db.session.query(func.sum(Payments.amount))
                 .filter(Payments.clint_id == id, Payments.approval_status == True)
                 .scalar())
        if total is None:
            total = 0                                # legacy starts the loop at int 0
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعات", e)
    return safe_jsonify({"total": total}), 200


# =========================================================================== #
#  PROMISE-TO-PAY  (paymentDates)
# =========================================================================== #
D_U = aliased(User, name="du")
D_CL = aliased(Clints, name="dcl")
D_PF = aliased(Portfolios, name="dpf")
D_CT = aliased(Contract, name="dct")
D_LCS = aliased(CommentStatuses, name="dlcs")

# one latest comment per client, as a LATERAL join instead of the legacy
# `Comments...order_by(create_at.desc()).first()` run **twice** per row (review bug 18)
_latest_comment_sq = (
    select(Comments)
    .where(Comments.clint_id == D_CL.id, Comments.is_deleted == False)
    .order_by(Comments.create_at.desc())
    .limit(1)
    .subquery()
    .lateral(name="dlc")
)
D_LC = aliased(Comments, _latest_comment_sq, name="dlc")

PD_COLS = (model_cols(PaymentDates, "pd") + model_cols(D_U, "u") + model_cols(D_CL, "cl")
           + model_cols(D_PF, "pf") + model_cols(D_CT, "ct") + model_cols(D_LC, "lc")
           + model_cols(D_LCS, "ls"))


def _pd_map():
    m = {}
    _paths(m, [("", PaymentDates), ("user", D_U), ("clint", D_CL), ("portfolios", D_PF),
               ("portfolios.contract", D_CT), ("comment", D_LC), ("comment_status", D_LCS),
               ("clint.portfolios", D_PF), ("clint.portfolios.contract", D_CT)])
    m["user.name"] = _full_name(D_U)
    return m


PD_MAP = _pd_map()
PD_DEFAULT_ORDER = (PaymentDates.create_at.desc(), PaymentDates.id)


def _payment_dates_query():
    return (db.session.query(*PD_COLS)
            .select_from(PaymentDates)
            .outerjoin(D_U, PaymentDates.user_id == D_U.id)
            .outerjoin(D_CL, PaymentDates.clint_id == D_CL.id)
            .outerjoin(D_PF, D_CL.portfolios_id == D_PF.id)
            .outerjoin(D_CT, D_PF.contract_id == D_CT.id)
            .outerjoin(D_LC, true())
            .outerjoin(D_LCS, D_LC.comment_status_id == D_LCS.id))


def _serialize_payment_date(row):
    """app.py:4784-4812 — shared by /payment_date, its export and the currentUser pair."""
    d = take(row, PaymentDates, "pd")
    lc = take(row, D_LC, "lc")
    d["comment"] = lc
    d["comment_status"] = take(row, D_LCS, "ls") if lc is not None else None
    d["user"] = with_name(take(row, D_U, "u"))
    d["clint"] = take(row, D_CL, "cl")
    pf = take(row, D_PF, "pf")
    if pf is not None:
        pf["contract"] = take(row, D_CT, "ct")
        d["portfolios"] = pf
    return d


def _pd_base(dates, identity=None):
    q = _payment_dates_query().filter(
        PaymentDates.is_deleted == False,
        inclusive_day_range(PaymentDates.payment_date, dates[0], dates[1]),
    )
    if identity is not None:
        q = q.filter(PaymentDates.user_id == identity)
    return q


@bp.route("/payment_date", methods=["GET"])
@jwt_required()
def get_payment_dates():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        q = apply_all_filters(_pd_base(dates), request, PD_MAP)
        count = count_rows(q)
        q = apply_sort(q, request, PD_MAP, PD_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_payment_date(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)
    return safe_jsonify({"payment_dates": payload, "countRow": count}), 200


@bp.route("/payment_date/export", methods=["GET"])
@jwt_required()
def export_payment_dates():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", IndexError("list index out of range"))
        q = apply_all_filters(_pd_base(dates), request, PD_MAP, export=True)
        q = apply_sort(q, request, PD_MAP, PD_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)

    def gen():
        for chunk in stream_json(
            "payment_dates",
            (_serialize_payment_date(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/payment_date/currentUser", methods=["GET"])
@jwt_required()
def get_payment_dates_current_user():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", IndexError("list index out of range"))
        page, page_size, offset = parse_paging(request)
        q = apply_all_filters(_pd_base(dates, get_jwt_identity()), request, PD_MAP)
        count = count_rows(q)
        total_amount = sum_column(q, D_CL.amount) if count else 0
        q = apply_sort(q, request, PD_MAP, PD_DEFAULT_ORDER)
        rows = page_slice(q, page_size, offset).all()
        payload = [_serialize_payment_date(r) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)
    return safe_jsonify({"payment_dates": payload, "countRow": count, "totalAmount": total_amount}), 200


@bp.route("/payment_date/currentUser/export", methods=["GET"])
@jwt_required()
def export_payment_dates_current_user():
    try:
        dates = date_range(request, "customeFilterDateValue[]")
        if dates is None:
            return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", IndexError("list index out of range"))
        q = apply_all_filters(_pd_base(dates, get_jwt_identity()), request, PD_MAP, export=True)
        q = apply_sort(q, request, PD_MAP, PD_DEFAULT_ORDER)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)

    def gen():
        # NOTE: legacy emits a leading space in this message only (app.py:4963)
        for chunk in stream_json(
            "payment_dates",
            (_serialize_payment_date(r) for r in q.yield_per(EXPORT_YIELD)),
            MSG_EXPORT_SPACED,
        ):
            yield chunk

    return Response(stream_with_context(gen()), mimetype="application/json")


@bp.route("/payment_date/user", methods=["GET"])
@jwt_required()
def get_payment_dates_by_user():
    """PTPs of the current user's *clients* (not of the current user). No date filter,
    no paging — same as app.py:4966."""
    try:
        identity = get_jwt_identity()
        cols = model_cols(PaymentDates, "pd") + model_cols(D_U, "u") + model_cols(D_CL, "cl")
        rows = (db.session.query(*cols)
                .select_from(PaymentDates)
                .join(D_CL, PaymentDates.clint_id == D_CL.id)
                .outerjoin(D_U, PaymentDates.user_id == D_U.id)
                .filter(D_CL.user_id == identity, D_CL.is_archive == False)
                .order_by(PaymentDates.create_at.desc(), PaymentDates.id)
                .all())
        payload = []
        for row in rows:
            d = take(row, PaymentDates, "pd")
            d["user"] = with_name(take(row, D_U, "u"))
            d["clint"] = take(row, D_CL, "cl")
            payload.append(d)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)
    return safe_jsonify({"payment_dates": payload}), 200


@bp.route("/payment_date/clint/<id>", methods=["GET"])
@jwt_required()
def get_payment_dates_by_clint(id):
    try:
        cols = model_cols(PaymentDates, "pd") + model_cols(D_U, "u") + model_cols(D_CL, "cl")
        rows = (db.session.query(*cols)
                .select_from(PaymentDates)
                .outerjoin(D_U, PaymentDates.user_id == D_U.id)
                .outerjoin(D_CL, PaymentDates.clint_id == D_CL.id)
                .filter(PaymentDates.clint_id == id)
                .order_by(PaymentDates.create_at.desc(), PaymentDates.id)
                .all())
        payload = []
        for row in rows:
            d = take(row, PaymentDates, "pd")
            d["user"] = take(row, D_U, "u")          # legacy adds no `name` here
            d["clint"] = take(row, D_CL, "cl")
            payload.append(d)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تواريخ الدفعات", e)
    return safe_jsonify({"payment_dates": payload}), 200


# =========================================================================== #
#  small N+1 fixes:  /contracts  /uesrs/export  /supervisors  /comment_status
# =========================================================================== #
def _archiving_filter_values():
    """`customeFilterArchivingStatusesValue[]` -> [True] / [False] / [] (review §3.8)."""
    out = []
    for v in request.args.getlist("customeFilterArchivingStatusesValue[]"):
        lv = str(v).lower()
        if lv == "true":
            out.append(True)
        elif lv == "false":
            out.append(False)
    return out


@bp.route("/contracts", methods=["GET"])
@jwt_required()
def get_contracts():
    """P7: `get_countClints()`/`get_totalAmount()` were 2 aggregate queries per contract.
    One GROUP BY now covers every contract."""
    try:
        page, page_size, offset = parse_paging(request)
        values = _archiving_filter_values()

        agg = db.session.query(
            Portfolios.contract_id.label("contract_id"),
            func.count(Clints.id).label("cnt"),
            func.sum(Clints.amount).label("total"),
        ).select_from(Clints).join(Portfolios, Clints.portfolios_id == Portfolios.id)
        if values:
            agg = agg.filter(Clints.is_archive.is_(values[0]))
        counts = {r.contract_id: r for r in agg.group_by(Portfolios.contract_id).all()}

        contracts = Contract.query.order_by(Contract.created_at).all()
        rows = []
        for contract in contracts:
            d = contract.as_dict()
            hit = counts.get(contract.id)
            d["count_clints"] = hit.cnt if hit else 0
            d["total_amount"] = float(hit.total) if (hit and hit.total) else 0
            rows.append(d)

        rows = _py_custom_filter_rows(_py_filter_rows(rows))
        count = len(rows)
        rows = _py_sort_rows(rows)
        page_rows = rows[offset:offset + page_size] if page_size > 0 else rows
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العقود", e)
    return safe_jsonify({"contracts": page_rows, "countRow": count}), 200


@bp.route("/supervisors", methods=["GET"])
@jwt_required()
def get_supervisors():
    """P8: was `1 + S x (1 user + 1 contracts q + C gets + 1 users q + U gets)`.
    Now 5 queries total, regardless of how many supervisors exist."""
    try:
        supervisors = Supervisor.query.all()
        sup_ids = [s.id for s in supervisors]

        contracts_by_sup = {}
        users_by_sup = {}
        if sup_ids:
            for sc, contract in (db.session.query(SupervisorContract, Contract)
                                 .join(Contract, SupervisorContract.contract_id == Contract.id)
                                 .filter(SupervisorContract.supervisor_id.in_(sup_ids)).all()):
                contracts_by_sup.setdefault(sc.supervisor_id, []).append(contract.as_dict())
            for su, user in (db.session.query(SupervisorUser, User)
                             .join(User, SupervisorUser.user_id == User.id)
                             .filter(SupervisorUser.supervisor_id.in_(sup_ids)).all()):
                users_by_sup.setdefault(su.supervisor_id, []).append(with_name(user.as_dict()))

        owner_ids = list({s.user_id for s in supervisors})
        owners = ({u.id: u for u in User.query.filter(User.id.in_(owner_ids)).all()}
                  if owner_ids else {})

        payload = []
        for s in supervisors:
            d = s.as_dict()
            owner = owners.get(s.user_id)
            d["user"] = with_name(owner.as_dict()) if owner else None
            d["contracts"] = contracts_by_sup.get(s.id, [])
            d["users"] = users_by_sup.get(s.id, [])
            payload.append(d)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المشرفين", e)
    return safe_jsonify({"supervisors": payload}), 200


@bp.route("/comment_status", methods=["GET"])
@jwt_required()
def get_comment_status():
    """Per-row `Contract.query.get` + `CommentReachability.query.get` -> two small maps."""
    try:
        statuses = CommentStatuses.query.all()
        contracts = {c.id: c.as_dict() for c in Contract.query.all()}
        reach = {r.id: r.as_dict() for r in CommentReachability.query.all()}

        rows = []
        for s in statuses:
            d = s.as_dict()
            if s.contract_id is not None and s.contract_id in contracts:
                d["contract"] = contracts[s.contract_id]
            if s.CommentReachability_id is not None and s.CommentReachability_id in reach:
                d["CommentReachability"] = reach[s.CommentReachability_id]
            rows.append(d)

        # app.py:3313 — no `page` param at all means "the whole list" (the follow-up picker)
        if "page" not in request.args:
            return safe_jsonify({"comment_statuses": rows, "countRow": len(rows)}), 200

        page, page_size, offset = parse_paging(request)
        rows = _py_custom_filter_rows(_py_filter_rows(rows))
        count = len(rows)
        rows = _py_sort_rows(rows)
        page_rows = rows[offset:offset + page_size] if page_size > 0 else rows
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العملاء", e)
    return safe_jsonify({"comment_statuses": page_rows, "countRow": count}), 200


@bp.route("/uesrs/export", methods=["GET"])
@jwt_required()
def export_users():
    """P7: `get_supervisor()` (1 + 2N queries) plus `get_countClints*`/`get_totalAmount*`
    per user. Replaced by the batched maps `/uesrs` (app.py:992) already uses."""
    try:
        users = User.query.order_by(User.created_at).all()
        user_ids = [u.id for u in users]

        roles = {r.id: r.as_dict() for r in Role.query.all()}
        nationalities = {n.id: n.as_dict() for n in Nationality.query.all()}
        branches = {b.id: b.as_dict() for b in Branch.query.all()}

        supervisors = {s.id: s for s in Supervisor.query.all()}
        links = (SupervisorUser.query.filter(SupervisorUser.user_id.in_(user_ids)).all()
                 if user_ids else [])
        owner_ids = list({supervisors[l.supervisor_id].user_id for l in links
                          if l.supervisor_id in supervisors})
        owners = ({u.id: u for u in User.query.filter(User.id.in_(owner_ids)).all()}
                  if owner_ids else {})
        links_by_user = {}
        for l in links:
            links_by_user.setdefault(l.user_id, []).append(l)

        values = _archiving_filter_values()
        agg = db.session.query(
            Clints.user_id,
            func.count(Clints.id).label("cnt"),
            func.sum(Clints.amount).label("total"),
        )
        if values:
            agg = agg.filter(Clints.is_archive.is_(values[0]))
        counts = {r.user_id: r for r in agg.group_by(Clints.user_id).all()}

        rows = []
        for user in users:
            d = with_name(user.as_dict())
            d["role"] = roles.get(user.role_id)
            d["branch"] = branches.get(user.branch_id)
            d["nationality"] = nationalities.get(user.nationality_id)

            sup_list = []
            for l in links_by_user.get(user.id, []):
                tl = l.as_dict()
                sup = supervisors.get(l.supervisor_id)
                if sup:
                    ts = sup.as_dict()
                    owner = owners.get(sup.user_id)
                    if owner:
                        ts["user"] = with_name(owner.as_dict())
                    tl["supervisor"] = ts
                sup_list.append(tl)
            d["supervisoruser"] = sup_list

            hit = counts.get(user.id)
            d["count_clints"] = hit.cnt if hit else 0
            d["total_amount"] = float(hit.total) if (hit and hit.total) else 0
            rows.append(d)

        rows = _py_sort_rows(_py_custom_filter_rows(_py_filter_rows(rows)))
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المستخدمين", e)
    return safe_jsonify({"users": rows, "message": MSG_EXPORT, "status": True}), 200


# --------------------------------------------------------------------------- #
# /contracts, /uesrs/export, /supervisors and /comment_status page over a list
# that is *derived* (count_clints / total_amount / supervisoruser are not columns),
# so their sort/filter stays in Python — but over tens of rows, not tens of
# thousands, and without any per-row query. Same operators as `filter_mui`.
# --------------------------------------------------------------------------- #
def _nested(row, path):
    value = row
    for key in path.split("."):
        if key.endswith("_"):
            key = key[:-1]
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _py_sort_rows(rows):
    field = request.args.get("field")
    direction = request.args.get("sort")
    if not field or not direction:
        return rows

    def key(row):
        v = _nested(row, field)
        if v is None:
            return ""
        if isinstance(v, str):
            return v.lower()
        if isinstance(v, datetime.datetime):
            return v.replace(tzinfo=None)
        return v

    try:
        return sorted(rows, key=key, reverse=(direction == "desc"))
    except TypeError:                       # mixed types, exactly what review §3.15 hits
        return rows


def _py_custom_filter_rows(rows):
    """`customeFilterBranchesValue[]` -> `branch_id`, `customeFilterStatusesValue[]` ->
    `is_active` — the two `my_custom_filter` calls inside srverSideMode/exportServerSideMode."""
    branches = [v for v in request.args.getlist("customeFilterBranchesValue[]") if v != ""]
    if branches:
        rows = [r for r in rows if str(_nested(r, "branch_id")) in branches]
    statuses = [str(v).lower() for v in request.args.getlist("customeFilterStatusesValue[]")]
    bools = [s == "true" for s in statuses if s in ("true", "false")]
    if bools:
        rows = [r for r in rows if _nested(r, "is_active") in bools]
    return rows


def _py_filter_rows(rows):
    operator = request.args.get("filterOperator")
    field = request.args.get("filterField")
    ftype = request.args.get("filterFieldType")
    value = request.args.get("filterValue")
    values = request.args.getlist("filterValue[]")
    if not operator or not field:
        return rows

    def keep(row):
        v = _nested(row, field)
        try:
            if operator == "isEmpty":
                return v is None or v == ""
            if operator == "isNotEmpty":
                return v is not None and v != ""
            if operator == "isAnyOf":
                return str(v) in [str(x) for x in values]
            if value is None:
                return True
            if ftype == "number":
                fv = float(value)
                return {"=": v == fv, "!=": v != fv, ">": v > fv, ">=": v >= fv,
                        "<": v < fv, "<=": v <= fv}.get(operator, True)
            if ftype == "date":
                dv = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                if v is None:
                    return operator == "not"
                d = v.date() if isinstance(v, datetime.datetime) else v
                return {"is": d == dv, "not": d != dv, "after": d > dv, "onOrAfter": d >= dv,
                        "before": d < dv, "onOrBefore": d <= dv}.get(operator, True)
            sv, rv = str(value), str(v)
            return {"contains": sv.lower() in rv.lower(), "equals": sv == rv,
                    "startsWith": rv.startswith(sv), "endsWith": rv.endswith(sv),
                    "is": rv == sv, "not": rv != sv}.get(operator, True)
        except (TypeError, ValueError):
            return True

    return [r for r in rows if keep(r)]


__all__ = ["bp", "URL_PREFIX"]
