"""B1 -- client lists and client exports, rewritten in SQL.

Every route here supersedes one in app.py that either materialised the whole result set in
Python or read it back out of Redis with `KEYS` + `pickle` and then ran one query per row
(review P1/P2/P6). While the prefix below is `/_new` both implementations are live side by
side so they can be diffed; at integration `URL_PREFIX` becomes `None` and the legacy
routes are deleted.

Wire contract (review §6) is unchanged: same envelopes (`clints` / `clints_archive` /
`clients`), same key spellings, same `pageSize` defaults per endpoint, success 200, errors
201 with `"status": false`, `"message": "تم تحميل الملف بنجاح"` on every export.

Exports **stream**. `/clients/export` used to build 171,452 dicts in memory and the worker
was OOM-killed (foundation report §10); it now yields rows straight off a server-side
cursor with `yield_per`, so peak RSS is flat regardless of the row count. Because
Flask-Compress buffers the *entire* body to gzip it -- which would reintroduce exactly the
problem we are fixing -- these responses compress themselves incrementally and set
`Content-Encoding` so `flask_compress.after_request` skips them.
"""
import logging
import zlib

from flask import Blueprint, Response, request, stream_with_context
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.http import error_response
from core.serialization import dumps
from extensions import db
from models import Clints
from services import clients_query as q
from services.clients_scope import can_view_user, resolve_scope

bp = Blueprint("clients_reports", __name__)
URL_PREFIX = None
log = logging.getLogger("cawp.api.clients_reports")

EXPORT_MESSAGE = "تم تحميل الملف بنجاح"
ERR_FETCH = "حدث خطأ أثناء جلب العملاء"
YIELD_PER = 2000


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _dump(obj):
    """`core.serialization.dumps` -- which falls back to the stdlib encoder for the
    handful of `clints.attribute` values that hold an integer beyond the signed 64-bit
    range (orjson refuses those; the stdlib prints them verbatim, like the pre-orjson
    Flask provider did)."""
    return dumps(obj)


def _json(payload, status=200):
    """`jsonify` equivalent (same body + trailing newline) that cannot 500 on big ints."""
    return Response(_dump(payload) + b"\n", status=status, mimetype="application/json")


# ---------------------------------------------------------------------------
# streaming helpers
# ---------------------------------------------------------------------------


def _row_chunks(query, serialize, maps, envelope_key="clints"):
    """Yield the raw bytes of `{"<key>":[ ...rows... ],"message":...,"status":true}`."""
    yield b'{"' + envelope_key.encode() + b'":['
    first = True
    for row in query.yield_per(YIELD_PER):
        if first:
            first = False
        else:
            yield b","
        yield _dump(serialize(row, maps))
    yield b'],"message":' + dumps(EXPORT_MESSAGE) + b',"status":true}'


def _stream(chunks):
    """Wrap a byte generator in a streaming Response that Flask-Compress will not buffer.

    `flask_compress.after_request` calls `response.get_data()` on *streamed* responses
    (`COMPRESS_STREAMS` defaults to True), which would materialise the whole export in
    memory. Setting `Content-Encoding` makes it return early, so we gzip incrementally
    ourselves and the wire format (gzip + `Vary: Accept-Encoding`) is unchanged.
    """
    accept = (request.headers.get("Accept-Encoding") or "").lower()

    if "gzip" in accept or "*" in accept:
        def gzipped():
            co = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
            try:
                for chunk in chunks:
                    out = co.compress(chunk)
                    if out:
                        yield out
                out = co.flush()
            except Exception:                                  # pragma: no cover
                log.exception("export stream failed mid-body")
                raise
            if out:
                yield out

        resp = Response(stream_with_context(gzipped()), mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = Response(stream_with_context(chunks), mimetype="application/json")
        if "br" in accept or "deflate" in accept:
            # the only case where flask_compress would still buffer us
            resp.headers["Content-Encoding"] = "identity"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


def _fetch_page(query, p, serialize, maps):
    """Two-phase ("deferred join") paging.

    `ORDER BY ... LIMIT 100` over a 175k-row result makes PostgreSQL sort the *whole*
    projection, and these rows carry the `attribute` JSONB blob (~900 B each), so the sort
    spilled ~50 MB to disk on every page of `/clints/neglect` and
    `/clints/not_have_payments` (`Sort Method: external merge Disk: 48600kB`).

    Phase 1 re-projects the very same query down to `clints.id` -- same joins, same
    filters, same ORDER BY -- so the sort is a narrow top-N heapsort in memory. Phase 2
    fetches the wide columns for those <=pageSize ids through the primary key. Safe here
    because every one of these builders yields exactly one row per client (no client has
    two non-deleted comments with the same `create_at`; checked).
    """
    ids = [r[0] for r in query.with_entities(Clints.id)
           .offset(p.startIndex).limit(p.pageSize)]
    if not ids:
        return []
    position = {str(i): n for n, i in enumerate(ids)}
    rows = query.filter(Clints.id.in_(ids)).all()
    rows.sort(key=lambda r: position.get(str(r.k_id), len(position)))
    return [serialize(r, maps) for r in rows]


def _forbidden(message):
    """The existing-style business error: HTTP 201 with `status: false` (review §6 --
    never 401/403 for a business rule, the frontend force-logs-out on 401)."""
    return _json({"message": message, "status": False, "error": "forbidden"}, 201)


# ---------------------------------------------------------------------------
# 1. main grid + export   (supersedes app.py:2506-2513 and 2654-2663)
# ---------------------------------------------------------------------------


@bp.route("/clints", methods=["GET"])
@jwt_required()
def get_clints():
    """Parity twin of the legacy `/clints`; proves `build_clients_query` matches
    `_build_clints_payload` before `/clients/export` is switched over to it."""
    try:
        query, count_query, (p, _scope) = q.build_clients_query(
            request.args, get_jwt_identity())
        agg = count_query.first()
        count = (agg[0] if agg else 0) or 0
        total_amount = (agg[1] if agg else 0) or 0
        maps = q.all_maps()
        clints = _fetch_page(query, p, q.serialize_client_row, maps)
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints": clints, "countRow": count, "totalAmount": total_amount}, 200)


@bp.route("/clients/export", methods=["GET"])
@jwt_required()
def export_clients():
    """Streamed replacement for `_build_clints_payload(export_mode=True)` + `jsonify`,
    which built 171k dicts and a ~250 MB body in RAM before Flask could send a byte."""
    try:
        query, _count_query, (_p, _scope) = q.build_clients_query(
            request.args, get_jwt_identity())
        maps = q.all_maps()
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _stream(_row_chunks(query, q.serialize_client_row, maps, "clients"))


# ---------------------------------------------------------------------------
# 2. archive grid + export   (supersedes app.py:2957-3130 and 2665-2830)
# ---------------------------------------------------------------------------


@bp.route("/clints/archive/", methods=["GET"])
@jwt_required()
def get_archive_clints():
    try:
        query, count_query, p = q.build_archive_query(request.args, get_jwt_identity())
        count = count_query.scalar() or 0
        maps = q.all_maps()
        clints = _fetch_page(query, p, q.serialize_archive_row, maps)
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints_archive": clints, "countRow": count}, 200)


@bp.route("/clients/archive/export", methods=["GET"])
@jwt_required()
def export_archive_clients():
    try:
        query, _count_query, _p = q.build_archive_query(request.args, get_jwt_identity())
        maps = q.all_maps()
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _stream(_row_chunks(query, q.serialize_archive_row, maps, "clients"))


# ---------------------------------------------------------------------------
# 3. have_payments / not_have_payments (+ exports)
#    supersedes app.py:6769-7020, 7023-7053, 7056-7307, 7310-7339
# ---------------------------------------------------------------------------


def _payments_flag_page(have_payments):
    try:
        query, agg_query, p = q.build_payments_flag_query(
            request.args, get_jwt_identity(), have_payments)
        agg = agg_query.one()
        count = agg[0] or 0
        total_amount = float(agg[1] or 0)
        maps = q.all_maps()
        clints = _fetch_page(query, p, q.serialize_payments_flag_row, maps)
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints": clints, "countRow": count, "totalAmount": total_amount}, 200)


def _payments_flag_export(have_payments):
    try:
        query, _agg_query, _p = q.build_payments_flag_query(
            request.args, get_jwt_identity(), have_payments)
        maps = q.all_maps()
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _stream(_row_chunks(query, q.serialize_payments_flag_row, maps, "clints"))


@bp.route("/clints/have_payments", methods=["GET"])
@jwt_required()
def get_clints_have_payments():
    return _payments_flag_page(True)


@bp.route("/clints/not_have_payments", methods=["GET"])
@jwt_required()
def get_clints_not_have_payments():
    return _payments_flag_page(False)


@bp.route("/clints/have_payments/export", methods=["GET"])
@jwt_required()
def export_clints_have_payments():
    return _payments_flag_export(True)


@bp.route("/clints/not_have_payments/export", methods=["GET"])
@jwt_required()
def export_clints_not_have_payments():
    return _payments_flag_export(False)


# ---------------------------------------------------------------------------
# 4. neglect report   (paged = parity twin of app.py:7342-7627;
#                      export supersedes app.py:7630-7648)
# ---------------------------------------------------------------------------


@bp.route("/clints/neglect", methods=["GET"])
@jwt_required()
def get_clints_neglect():
    try:
        query, agg_query, p = q.build_neglect_query(request.args, get_jwt_identity())
        agg = agg_query.one()
        count = agg[0] or 0
        total_amount = float(agg[1] or 0)
        maps = q.all_maps()
        clints = _fetch_page(query, p, q.serialize_neglect_row, maps)
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints": clints, "countRow": count, "totalAmount": total_amount}, 200)


@bp.route("/clints/neglect/export", methods=["GET"])
@jwt_required()
def export_clints_neglect():
    try:
        query, _agg_query, _p = q.build_neglect_query(request.args, get_jwt_identity())
        maps = q.all_maps()
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _stream(_row_chunks(query, q.serialize_neglect_row, maps, "clints"))


# ---------------------------------------------------------------------------
# 5. my next calls (+ export)   supersedes app.py:7650-7705 / 7708-7755
# ---------------------------------------------------------------------------


@bp.route("/clints/currentUser/have_nextCalls", methods=["GET"])
@jwt_required()
def get_current_user_next_calls():
    try:
        query, agg_query, p = q.build_next_calls_query(
            request.args, get_jwt_identity(), latest_only=False)
        agg = agg_query.one()
        count = agg[0] or 0
        total_amount = float(agg[1] or 0)
        maps = q.all_maps()
        rows = query.offset(p.startIndex).limit(p.pageSize).all()
        clints = [q.serialize_next_call_row(r, maps) for r in rows]
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints": clints, "countRow": count, "totalAmount": total_amount}, 200)


@bp.route("/clints/currentUser/have_nextCalls/export", methods=["GET"])
@jwt_required()
def export_current_user_next_calls():
    try:
        # the legacy export looked at the client's *latest* comment only, unlike the
        # paged route above which emitted one row per matching comment -- kept as is.
        query, _agg_query, _p = q.build_next_calls_query(
            request.args, get_jwt_identity(), latest_only=True)
        maps = q.all_maps()
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _stream(_row_chunks(query, q.serialize_next_call_row, maps, "clints"))


# ---------------------------------------------------------------------------
# 6. one collector's clients   supersedes app.py:2941-2954
# ---------------------------------------------------------------------------


@bp.route("/clints/user/<id>", methods=["GET"])
@jwt_required()
def get_clints_by_user(id):
    """The legacy route did `redis KEYS *<id>*` and had **no access check at all** -- any
    authenticated user could read any collector's book. Caller must now be that user, a
    supervisor of that user, or SuperAdmin."""
    try:
        identity = get_jwt_identity()
        scope = resolve_scope(identity)
        if not can_view_user(scope, id):
            return _forbidden("لا تملك صلاحية عرض عملاء هذا المستخدم")

        query, count_query, (p, _scope) = q.build_clients_query(
            request.args, identity, force_user_id=id)
        agg = count_query.first()
        count = (agg[0] if agg else 0) or 0
        maps = q.all_maps()
        clints = _fetch_page(query, p, q.serialize_client_row, maps)
    except Exception as e:
        db.session.rollback()
        return error_response(ERR_FETCH, e)
    return _json({"clints": clints, "countRow": count}, 200)
