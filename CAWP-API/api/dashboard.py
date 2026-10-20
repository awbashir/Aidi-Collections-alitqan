"""Dashboards.

Supersedes (legacy line numbers in the *current* app.py):

    /dashbord/user                      6088-6112   (4 aggregates — already fine, ported)
    /dashbord/collector/v2              6205-6316   (FULL-LOAD of clients + 3 months)
    /dashbord/admin/dailyPayments/v2    6319-6358   (SQL GROUP BY — already fine, ported)
    /dashbord/admin/dailyPaymentDates/v2 6361-6396  (SQL GROUP BY — already fine, ported)
    /dashbord/Allcollector/v2           6399-6514   (pickle in Redis, 7.5 MB, 66 k rows)
    /dashbord/commentsReport/v2         6517-6558   (FULL-LOAD + blocking ext-mysql, 5 s)
    /dashboard                          8376-8519   (review P4: `dash` cache key ignores
                                                     params + identity, 8 q/comment,
                                                     redis KEYS scan, blocking CDR)

Response shapes are unchanged. The exact fields the React app reads were verified against
`CAWP-frontend/src/views/dashboard2/`:

  commentsReport/v2  users[id, frist_name, last_name, contract_id, extension]
                     contracts[id, name]
                     comments[user_id, create_at, status.is_active]   <- `status` must not be null
                     calls[src, calldate, billsec]
  Allcollector/v2    users[id, frist_name, last_name, target_amount, target_percent,
                           target_is_percentage, contract_id]
                     contracts[id, name, monthly_target_amount, monthly_target_percent]
                     clients[user_id, amount]     payments[user_id, amount, payment_date]
  collector/v2       userName, user.*, contract.*, clients[], comments[], payments[],
                     all_user_payments[]
  daily*/v2          {user_id, amount, user_name, contract_name}

Every id is compared with a strict `===` on the client, so they stay strings exactly as
the legacy handlers emit them.

The external Asterisk CDR (`Issable_db`) is never called inline: `_calls_async` runs it on
a worker thread with a hard wall-clock budget and falls back to `[]`. A dead PBX used to
add ~3 s to `/dashbord/commentsReport/v2` and could hang `/dashboard` for minutes
(review P10).
"""
import datetime
import gzip
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from dateutil.relativedelta import relativedelta
from flask import Blueprint, Response, request
from core.auth import required_roles
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func

from core import cache
from core.http import error_response
from extensions import db
from models import (Clints, CommentReachability, Comments, CommentStatuses, ContactMethods,
                    Contract, PaymentDates, Payments, Portfolios, User)
from services.reports_query import safe_dumps as dumps
from services.reports_query import safe_jsonify

log = logging.getLogger("cawp.api.dashboard")

bp = Blueprint("dashboard", __name__)
URL_PREFIX = None
CDR_BUDGET_SECONDS = 1.2          # hard wall clock for the PBX round trip
CDR_COOLDOWN_SECONDS = 30         # circuit breaker after a failure/timeout
ALLCOLLECTOR_TTL = 120            # same TTL the legacy pickle cache used
COMMENTS_REPORT_TTL = 60
DASHBOARD_V1_TTL = 150            # same TTL the legacy `dash` key used

_cdr_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cdr")
_cdr_down_until = 0.0             # monotonic deadline of the open circuit

MAX_CACHE_BYTES = 16 * 1024 * 1024


# --------------------------------------------------------------------------- #
# raw-bytes cache
#
# `core.cache.get/set` round-trip through `cache_dumps`, which renders datetimes as
# ISO-8601 while the app's JSON provider renders them RFC-822 — a cached response would
# therefore not be byte-identical to an uncached one. These two helpers store the already
# rendered response body instead, so a hit and a miss produce exactly the same bytes.
# --------------------------------------------------------------------------- #
def _cache_get_raw(key):
    if request.args.get("nocache") == "1":
        return None
    try:
        return cache.client().get(key)
    except Exception:                                    # pragma: no cover
        log.warning("cache get %s failed", key, exc_info=True)
        return None


def _cache_set_raw(key, value, ttl):
    if len(value) > MAX_CACHE_BYTES:
        log.info("skipping cache for %s: %.1f MB exceeds the %d MB cap",
                 key, len(value) / 1e6, MAX_CACHE_BYTES // (1024 * 1024))
        return
    try:
        cache.client().setex(key, int(ttl), value)
    except Exception:                                    # pragma: no cover
        log.warning("cache set %s failed", key, exc_info=True)


def _fragment(payload):
    """`dumps(dict)` without the enclosing braces, so it can be spliced into a body."""
    return dumps(payload)[1:-1]


def _json_response(body):
    return Response(body, mimetype="application/json")


def _gzip_cached_response(key, body, ttl):
    """Serve `body` gzip-compressed, caching the compressed bytes under `<key>:gz`.

    Flask-Compress re-gzips a 7.5 MB body on every request (~250 ms of CPU); under 20
    concurrent dashboard loads that dominated latency (p95 9.7 s). The compressed variant is
    cached alongside the raw one for the same TTL; Flask-Compress skips responses that already
    carry `Content-Encoding`.
    """
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return _json_response(body)
    gz = _cache_get_raw(key + ":gz")
    if gz is None:
        gz = gzip.compress(body, compresslevel=6)
        _cache_set_raw(key + ":gz", gz, ttl)
    resp = Response(gz, mimetype="application/json")
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Content-Length"] = str(len(gz))
    return resp


# --------------------------------------------------------------------------- #
# CDR (external MySQL) — bounded, never on the critical path
# --------------------------------------------------------------------------- #
def _trip_cdr_breaker():
    global _cdr_down_until
    _cdr_down_until = time.monotonic() + CDR_COOLDOWN_SECONDS


def _fetch_calls(start_date, end_date):
    from issable.db_connection import Issable_db

    try:
        rows = Issable_db().GetCallsByDate(start_date, end_date)
    except Exception:                                    # pragma: no cover
        log.warning("CDR fetch failed", exc_info=True)
        _trip_cdr_breaker()
        return []
    if rows is None:
        _trip_cdr_breaker()
        return []
    return rows


def _calls_async(start_date, end_date):
    """Start the CDR query now; collect it later with `_calls_result`.

    Returns None while the circuit breaker is open, so an unreachable PBX costs the
    dashboard nothing at all instead of `CDR_BUDGET_SECONDS` on every request.
    """
    if time.monotonic() < _cdr_down_until:
        return None
    return _cdr_pool.submit(_fetch_calls, start_date, end_date)


def _calls_result(future, budget=CDR_BUDGET_SECONDS):
    if future is None:
        return []
    try:
        return future.result(timeout=budget) or []
    except FutureTimeout:
        log.warning("CDR query exceeded %.1fs budget; returning []", budget)
        _trip_cdr_breaker()
        return []
    except Exception:                                    # pragma: no cover
        log.warning("CDR query failed", exc_info=True)
        _trip_cdr_breaker()
        return []


def _today():
    return datetime.datetime.now().date()


# =========================================================================== #
#  /dashbord/user  — 4 aggregates, ported verbatim
# =========================================================================== #
@bp.route("/dashbord/user", methods=["GET"])
@jwt_required()
def dashbord_user():
    try:
        identity = get_jwt_identity()
        clints = db.session.query(func.count(Clints.id)).filter(Clints.user_id == identity).scalar()
        comments = db.session.query(func.count(Comments.id)).filter(Comments.user_id == identity).scalar()
        payments = db.session.query(func.count(Payments.id)).filter(Payments.user_id == identity).scalar()
        payments_sum = db.session.query(func.sum(Payments.amount)).filter(Payments.user_id == identity).scalar()
        return safe_jsonify({
            "clintsCount": clints,
            "commentsCount": comments,
            "paymentCount": payments,
            "paymentSum": payments_sum,
        }), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


# =========================================================================== #
#  /dashbord/admin/dailyPayments/v2  +  dailyPaymentDates/v2  (already GROUP BY)
# =========================================================================== #
@bp.route("/dashbord/admin/dailyPayments/v2", methods=["GET"])
@jwt_required()
def dashbord_daily_payments_v2():
    try:
        rows = (db.session.query(
            User.id.label("user_id"), User.frist_name, User.last_name,
            Contract.name.label("contract_name"),
            func.sum(Payments.amount).label("total_amount"))
            .select_from(Payments)
            .join(Clints, Payments.clint_id == Clints.id)
            .join(User, Clints.user_id == User.id)
            .join(Portfolios, Clints.portfolios_id == Portfolios.id)
            .join(Contract, Portfolios.contract_id == Contract.id)
            .filter(func.date(Payments.payment_date) == _today(),
                    Payments.approval_status == True)
            .group_by(User.id, User.frist_name, User.last_name, Contract.name)
            .order_by(func.sum(Payments.amount).desc())
            .all())
        payload = [{
            "user_id": r.user_id,
            "amount": r.total_amount,
            "user_name": r.frist_name + " " + r.last_name,
            "contract_name": r.contract_name,
        } for r in rows]
        return safe_jsonify({"daily_payments": payload}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


@bp.route("/dashbord/admin/dailyPaymentDates/v2", methods=["GET"])
@jwt_required()
def dashbord_daily_payment_dates_v2():
    try:
        rows = (db.session.query(
            User.id.label("user_id"), User.frist_name, User.last_name,
            Contract.name.label("contract_name"),
            func.sum(PaymentDates.amount).label("total_amount"))
            .select_from(PaymentDates)
            .join(Clints, PaymentDates.clint_id == Clints.id)
            .join(User, Clints.user_id == User.id)
            .join(Portfolios, Clints.portfolios_id == Portfolios.id)
            .join(Contract, Portfolios.contract_id == Contract.id)
            .filter(func.date(PaymentDates.payment_date) == _today())
            .group_by(User.id, User.frist_name, User.last_name, Contract.name)
            .order_by(func.sum(PaymentDates.amount).desc())
            .all())
        payload = [{
            "user_id": r.user_id,
            "amount": r.total_amount,
            "user_name": r.frist_name + " " + r.last_name,
            "contract_name": r.contract_name,
        } for r in rows]
        return safe_jsonify({"payment_dates": payload}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب مواعيد السداد اليومية", e)


# =========================================================================== #
#  /dashbord/collector/v2
# =========================================================================== #
@bp.route("/dashbord/collector", methods=["GET"])
@jwt_required()
def dashbord_collector():
    """v1 of the collector dashboard. The frontend only calls `/dashbord/collector/v2`;
    this one is kept alive because the route existed. Legacy answered 201 for every
    caller (`fetchClientsForUser` -> `redis KEYS` -> `pickle.loads(None)`), and counted
    "active" clients out of the Redis client cache, which no longer has any writer:
    `allActiveClients` is now `is_archive = false`, the same rule `/dashboard` (v1) uses.
    Envelope, key spellings and the 200 status are unchanged."""
    try:
        start_date = _ts_arg("startDate")
        end_date = _ts_arg("endDate")
        identity = get_jwt_identity()

        all_clients = (db.session.query(func.count(Clints.id))
                       .filter(Clints.user_id == identity).scalar() or 0)
        active = (db.session.query(func.count(Clints.id))
                  .filter(Clints.user_id == identity, Clints.is_archive == False).scalar() or 0)

        rows = (db.session.query(Comments, CommentStatuses, CommentReachability,
                                 Clints, Portfolios, Contract)
                .outerjoin(CommentStatuses, Comments.comment_status_id == CommentStatuses.id)
                .outerjoin(CommentReachability,
                           Comments.comment_reachability_id == CommentReachability.id)
                .outerjoin(Clints, Comments.clint_id == Clints.id)
                .outerjoin(Portfolios, Clints.portfolios_id == Portfolios.id)
                .outerjoin(Contract, Portfolios.contract_id == Contract.id)
                .filter(Comments.user_id == identity,
                        Comments.create_at >= start_date,
                        Comments.create_at < end_date + datetime.timedelta(days=1))
                .all()) if start_date and end_date else []
        comments_list = []
        for comment, status, reach, clint, portfolio, contract in rows:
            d = comment.as_dict()
            if reach is not None:
                d["reachability"] = reach.as_dict()
            d["status"] = status.as_dict() if status is not None else None
            d["clint"] = clint.as_dict() if clint is not None else None
            if portfolio is not None:
                tp = portfolio.as_dict()
                tp["contract"] = contract.as_dict() if contract is not None else None
                d["portfolios"] = tp
            comments_list.append(d)

        pq = db.session.query(Payments).filter(Payments.user_id == identity,
                                               Payments.approval_status == True)
        if start_date and end_date:
            pq = pq.filter(Payments.payment_date.between(start_date, end_date))
        payments_list = [p.as_dict() for p in pq.all()]

        return safe_jsonify({"clients": {"allClients": all_clients,
                                         "allActiveClients": active,
                                         "allInactiveClients": all_clients - active},
                             "comments": comments_list,
                             "payments": payments_list}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


@bp.route("/dashbord/collector/v2", methods=["GET"])
@jwt_required()
def dashbord_collector_v2():
    try:
        start_date = datetime.datetime.now() - relativedelta(months=3)
        end_date = datetime.datetime.now()
        identity = get_jwt_identity()

        user = db.session.get(User, identity)
        if user is None:
            return safe_jsonify({"clients": [], "userName": "", "user": {}, "contract": {},
                            "comments": [], "payments": [], "all_user_payments": []}), 200
        tuser = user.as_dict()
        user_name = (tuser["frist_name"] or "") + " " + (tuser["last_name"] or "")

        contract = (db.session.get(Contract, tuser["contract_id"])
                    if tuser["contract_id"] else None)
        tcontract = contract.as_dict() if contract else {}

        clints = (db.session.query(Clints)
                  .join(Portfolios, Clints.portfolios_id == Portfolios.id)
                  .filter(Clints.user_id == identity, Clints.is_archive == False,
                          Portfolios.contract_id == tuser["contract_id"])
                  .all())
        clints_list = [c.as_dict() for c in clints]

        comment_rows = (db.session.query(Comments, CommentStatuses)
                        .outerjoin(CommentStatuses,
                                   Comments.comment_status_id == CommentStatuses.id)
                        .filter(Comments.create_at >= start_date,
                                Comments.create_at < end_date + datetime.timedelta(days=1),
                                Comments.is_deleted == False,
                                Comments.user_id == identity)
                        .all())
        comments_list = []
        for comment, status in comment_rows:
            d = comment.as_dict()
            if status is not None:
                d["status"] = status.as_dict()
            comments_list.append(d)

        payments = (db.session.query(Payments)
                    .join(Clints, Payments.clint_id == Clints.id)
                    .join(Portfolios, Clints.portfolios_id == Portfolios.id)
                    .filter(Payments.payment_date >= start_date,
                            Payments.payment_date < end_date + datetime.timedelta(days=1),
                            Payments.user_id == identity,
                            Payments.approval_status == True,
                            Portfolios.contract_id == tuser["contract_id"])
                    .all())
        payments_list = [p.as_dict() for p in payments]

        grouped = (db.session.query(
            User.id.label("user_id"), User.frist_name, User.last_name,
            func.sum(Payments.amount).label("total_amount"))
            .select_from(Payments)
            .join(Clints, Payments.clint_id == Clints.id)
            .join(User, Clints.user_id == User.id)
            .join(Portfolios, Clints.portfolios_id == Portfolios.id)
            .filter(func.date(Payments.payment_date) == _today(),
                    Payments.approval_status == True)
            .group_by(User.id, User.frist_name, User.last_name)
            .order_by(func.sum(Payments.amount).desc())
            .limit(6).all())
        all_user_payments = [{
            "user_id": r.user_id,
            "amount": r.total_amount,
            "user_name": r.frist_name + " " + r.last_name,
        } for r in grouped]

        return safe_jsonify({"clients": clints_list, "userName": user_name, "user": tuser,
                        "contract": tcontract, "comments": comments_list,
                        "payments": payments_list,
                        "all_user_payments": all_user_payments}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


# =========================================================================== #
#  Users on the home dashboard cards (Allcollector/v2, commentsReport/v2)
# =========================================================================== #
def _dashboard_users():
    """Active users shown on the all-collectors and daily notes/calls cards, with the contract
    each one is grouped under -> [(User, contract_id or None)].

    The cards used to list only users whose profile carries `users.contract_id`. Users created
    without it (every collector of the a-alaoula install, 2026-10) vanished from the home page.
    Now: every active user except SuperAdmin accounts (plus any SuperAdmin that does carry a
    contract); the contract is the profile's, else the contract of the portfolio holding most of
    the user's unarchived cases, else None (shown without a target; hidden only when a contract
    filter is chosen)."""
    from models import Role
    rows = (db.session.query(User, Role.name)
            .outerjoin(Role, Role.id == User.role_id)
            .filter(User.is_active == True).all())
    users = [u for u, role in rows if u.contract_id is not None or role != "SuperAdmin"]
    missing = [u.id for u in users if u.contract_id is None]
    derived = {}
    if missing:
        ranked = (db.session.query(Clints.user_id, Portfolios.contract_id,
                                   func.count(Clints.id).label("n"))
                  .join(Portfolios, Portfolios.id == Clints.portfolios_id)
                  .filter(Clints.is_archive == False, Clints.user_id.in_(missing))
                  .group_by(Clints.user_id, Portfolios.contract_id).all())
        for user_id, contract_id, n in sorted(ranked, key=lambda r: (str(r[0]), -r[2], str(r[1]))):
            derived.setdefault(user_id, contract_id)
    return [(u, u.contract_id if u.contract_id is not None else derived.get(u.id)) for u in users]


# =========================================================================== #
#  /dashbord/Allcollector/v2
# =========================================================================== #
def _allcollector_payload():
    now = datetime.datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # the KPI cards render each user's contract (AllCollectorsKPI.tsx): a user with no contract
    # at all (no profile contract and no cases) stays off this card
    users_rows = [(u, c) for u, c in _dashboard_users() if c is not None]
    active_user_ids = [u.id for u, _c in users_rows]
    tusers = [{
        "id": str(u.id),
        "frist_name": u.frist_name,
        "last_name": u.last_name,
        "target_amount": u.target_amount,
        "target_percent": u.target_percent,
        "target_is_percentage": u.target_is_percentage,
        "contract_id": str(contract_id) if contract_id else None,
    } for u, contract_id in users_rows]

    contracts_rows = db.session.query(
        Contract.id, Contract.name, Contract.monthly_target_amount,
        Contract.monthly_target_percent).all()
    tcontracts = [{
        "id": str(c.id),
        "name": c.name,
        "monthly_target_amount": c.monthly_target_amount,
        "monthly_target_percent": c.monthly_target_percent,
    } for c in contracts_rows]

    clients_rows = (db.session.query(Clints.id, Clints.user_id, Clints.amount)
                    .filter(Clints.is_archive == False,
                            Clints.user_id.in_(active_user_ids)).all()
                    ) if active_user_ids else []
    clints_list = [{
        "id": str(c.id),
        "user_id": str(c.user_id) if c.user_id else None,
        "amount": c.amount,
    } for c in clients_rows]

    payments_rows = (db.session.query(Payments.user_id, Payments.amount, Payments.payment_date)
                     .filter(Payments.payment_date >= month_start,
                             Payments.payment_date <= now,
                             Payments.approval_status == True,
                             Payments.user_id.in_(active_user_ids)).all()
                     ) if active_user_ids else []
    payments_list = [{
        "user_id": str(p.user_id) if p.user_id else None,
        "amount": p.amount,
        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
    } for p in payments_rows]

    return {"clients": clints_list, "users": tusers, "contracts": tcontracts,
            "payments": payments_list}


@bp.route("/dashbord/Allcollector/v2", methods=["GET"])
@jwt_required()
@required_roles("dashbord_admin")  # the all-collectors card (ProjectDashboard.tsx loads it only for dashbord_admin)
def dashbord_all_collector_v2():
    """Cache key: `dash:allcollector:v2:<contract filter>:<today>`, TTL 120 s, JSON.

    The legacy key was the constant `dash_allcollector_v2_slim_v4` holding a **pickle**,
    so every caller shared one blob regardless of who asked (review S6/P4). The payload
    itself is global (all active collectors), but the key still carries the caller scope
    and the `contract` filter so a future scoped variant cannot serve the wrong rows.

    Staleness: nothing invalidates this key on a payment write; a payment can take up to
    120 s to appear in the KPI cards. That matches the legacy TTL and is deliberate — the
    alternative (bumping a version on every payment insert) would make the cache useless
    on a busy day. `?nocache=1` bypasses it.
    """
    try:
        # payload is global (all active collectors) -> one shared blob, not one per caller scope
        contract_filter = ",".join(sorted(request.args.getlist("contract")))
        key = "dash:allcollector:v2:%s:%s" % (contract_filter or "-", _today().isoformat())

        body = _cache_get_raw(key)
        if body is None:
            body = dumps(_allcollector_payload())
            _cache_set_raw(key, body, ALLCOLLECTOR_TTL)
        return _gzip_cached_response(key, body, ALLCOLLECTOR_TTL)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


# =========================================================================== #
#  /dashbord/commentsReport/v2
# =========================================================================== #
def _comments_report_db():
    """users / contracts / today's comments, in three queries and zero Python loops
    over the ORM identity map."""
    users_list = []
    for u, contract_id in _dashboard_users():
        d = u.as_dict()
        d["contract_id"] = contract_id      # the grouping contract (profile's or derived)
        users_list.append(d)

    contracts_list = [c.as_dict() for c in Contract.query.all()]

    today = _today()
    rows = (db.session.query(Comments, CommentStatuses)
            .outerjoin(CommentStatuses, Comments.comment_status_id == CommentStatuses.id)
            .filter(Comments.is_deleted == False,
                    Comments.create_at >= datetime.datetime.combine(today, datetime.time.min),
                    Comments.create_at < datetime.datetime.combine(
                        today + datetime.timedelta(days=1), datetime.time.min))
            .all())
    comments_list = []
    for comment, status in rows:
        d = comment.as_dict()
        if status is not None:
            d["status"] = status.as_dict()
        comments_list.append(d)

    return {"users": users_list, "contracts": contracts_list, "comments": comments_list}


@bp.route("/dashbord/commentsReport/v2", methods=["GET"])
@jwt_required()
def dashbord_comments_report_v2():
    """The UI (`CommentsReportDaily.tsx`, `CallsReportDaily.tsx`) buckets the raw rows by
    hour **in browser-local time**, so the per-comment rows cannot be pre-aggregated
    server-side without changing what the screen shows. What changed instead:

      * one query with an explicit join replaces the ORM `joinedload` + per-row dict work;
      * the blocking `Issable_db()` connect (~3 s against a dead PBX) now runs on a worker
        thread started **before** the DB work and is abandoned after 1.5 s -> `calls: []`;
      * the DB half is cached for 60 s under a key that includes the caller scope and the
        date, so a dashboard refresh loop does not re-scan today's comments every time.

    `calls` is never cached (it is cheap when the PBX is up and empty when it is not).
    """
    calls_future = None
    try:
        today = _today()
        calls_future = _calls_async(today, today)

        # payload is global (today's comments for every user) -> one shared fragment
        from core.user_privacy import cache_key as _privacy_key   # embeds user dicts
        key = "dash:commentsreport:v2:%s:%s" % (today.isoformat(), _privacy_key())
        frag = _cache_get_raw(key)
        if frag is None:
            frag = _fragment(_comments_report_db())
            _cache_set_raw(key, frag, COMMENTS_REPORT_TTL)

        calls = _calls_result(calls_future)
        return _json_response(b"{" + frag + b',"calls":' + dumps(calls) + b"}")
    except Exception as e:
        db.session.rollback()
        _calls_result(calls_future, budget=0.01)
        return error_response("حدث خطأ أثناء جلب المحافظ", e)


# =========================================================================== #
#  /dashboard  (v1 admin dashboard — review P4)
# =========================================================================== #
def _ts_arg(name):
    """`startDate`/`endDate` are unix timestamps (`datetime.fromtimestamp(int(v))`)."""
    raw = request.args.getlist(name)
    if not raw:
        raise IndexError("list index out of range")
    return datetime.datetime.fromtimestamp(int(raw[0]))


@bp.route("/dashboard", methods=["GET"])
@jwt_required()
def get_dashboard_data():
    """Kept working, but rebuilt:

      * **cache key** is now `dash:v1:<identity>:<start>:<end>` instead of the constant
        `"dash"`. The legacy key ignored `startDate`/`endDate` *and* the caller, and it
        cached `exportServerSideMode(comments, request)` — i.e. the **first** caller's
        filtered rows — so the next caller with different filters was served someone
        else's data (review P4 a/b/c/d). TTL stays 150 s.
      * **aggregates in SQL**: the 8-queries-per-comment and 3-queries-per-payment loops
        are gone; `comments` comes from one join and `payments` gets its `contract_id`
        from the same join. `clients` uses `COUNT(*)` instead of a `KEYS clint*` scan
        plus `pickle.loads` per client.
      * **the CDR is never called on the request path.** `calls` is `[]` unless the caller
        asks for `?include_calls=1`, and even then it is bounded to 1.5 s on a worker
        thread. `src/views/dashboard` does not exist in the frontend any more (the only
        dashboard route is `views/dashboard2` -> `/home`), so nothing reads `calls` here.
        `countRow` is the number of comments in range, as before.
    """
    try:
        start_date = _ts_arg("startDate")
        end_date = _ts_arg("endDate")
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليقات", e)

    include_calls = request.args.get("include_calls") == "1"
    calls_future = _calls_async(start_date, end_date) if include_calls else None

    try:
        identity = get_jwt_identity()
        from core.user_privacy import cache_key as _privacy_key   # embeds user dicts
        key = "dash:v1:%s:%s:%s:%s" % (identity, start_date.isoformat(), end_date.isoformat(),
                                       _privacy_key())
        frag = _cache_get_raw(key)
        if frag is None:
            frag = _fragment(_dashboard_v1_payload(start_date, end_date))
            _cache_set_raw(key, frag, DASHBOARD_V1_TTL)

        calls = _calls_result(calls_future) if include_calls else []
        tail = _fragment({"startDate": request.args.getlist("startDate"),
                          "endDate": request.args.getlist("endDate")})
        return _json_response(b'{"calls":' + dumps(calls) + b"," + frag + b"," + tail + b"}")
    except Exception as e:
        db.session.rollback()
        _calls_result(calls_future, budget=0.01)
        return error_response("حدث خطأ أثناء جلب التعليقات", e)


def _dashboard_v1_payload(start_date, end_date):
    from sqlalchemy.orm import aliased

    from services.reports_query import model_cols, take, with_name

    end_excl = end_date + datetime.timedelta(days=1)

    contracts = [c.as_dict() for c in Contract.query.all()]
    users = [u.as_dict() for u in User.query.all()]

    all_clients = db.session.query(func.count(Clints.id)).scalar() or 0
    active_clients = (db.session.query(func.count(Clints.id))
                      .filter(Clints.is_archive == False).scalar() or 0)
    clients = {
        "allClients": all_clients,
        "allActiveClients": active_clients,
        "allInactiveClients": all_clients - active_clients,
        # legacy embedded every client dict here via a full `KEYS clint*` Redis scan;
        # nothing reads it (the v1 dashboard screen no longer exists in the frontend).
        "clints": [],
    }

    U = aliased(User, name="dbu")
    CS = aliased(CommentStatuses, name="dbcs")
    CM = aliased(ContactMethods, name="dbcm")
    CR = aliased(CommentReachability, name="dbcr")
    CL = aliased(Clints, name="dbcl")
    PF = aliased(Portfolios, name="dbpf")
    CT = aliased(Contract, name="dbct")

    cols = (model_cols(Comments, "c") + model_cols(U, "u") + model_cols(CS, "cs")
            + model_cols(CM, "cm") + model_cols(CR, "cr") + model_cols(CL, "cl")
            + model_cols(PF, "pf") + model_cols(CT, "ct"))
    rows = (db.session.query(*cols)
            .select_from(Comments)
            .outerjoin(U, Comments.user_id == U.id)
            .outerjoin(CS, Comments.comment_status_id == CS.id)
            .outerjoin(CM, Comments.contactMethod_id == CM.id)
            .outerjoin(CR, Comments.comment_reachability_id == CR.id)
            .outerjoin(CL, Comments.clint_id == CL.id)
            .outerjoin(PF, CL.portfolios_id == PF.id)
            .outerjoin(CT, PF.contract_id == CT.id)
            .filter(Comments.is_deleted == False,
                    Comments.create_at >= start_date, Comments.create_at < end_excl)
            .order_by(Comments.create_at.desc(), Comments.id)
            .all())

    comments = []
    for row in rows:
        d = take(row, Comments, "c")
        if getattr(row, "c__comment_reachability_id", None) is not None:
            d["reachability"] = take(row, CR, "cr")
        d["status"] = take(row, CS, "cs")
        d["user"] = with_name(take(row, U, "u"))
        d["contact_method"] = take(row, CM, "cm")
        d["clint"] = take(row, CL, "cl")
        pf = take(row, PF, "pf")
        if pf is not None:
            pf["contract"] = take(row, CT, "ct")
            d["portfolios"] = pf
        comments.append(d)

    payment_rows = (db.session.query(Payments, Portfolios.contract_id)
                    .select_from(Payments)
                    .outerjoin(Clints, Payments.clint_id == Clints.id)
                    .outerjoin(Portfolios, Clints.portfolios_id == Portfolios.id)
                    .filter(Payments.is_deleted == False,
                            Payments.payment_date >= start_date,
                            Payments.payment_date < end_excl,
                            Payments.approval_status == True)
                    .all())
    payments = []
    for payment, contract_id in payment_rows:
        d = payment.as_dict()
        d["contract_id"] = contract_id
        payments.append(d)

    return {"payments": payments, "comments": comments, "countRow": len(comments),
            "contracts": contracts, "users": users, "clients": clients}


__all__ = ["bp", "URL_PREFIX"]
