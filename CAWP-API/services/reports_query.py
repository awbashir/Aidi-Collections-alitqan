"""SQL-side replacement for the in-Python paging/sort/filter engine in app.py.

The legacy pipeline is:

    rows = <full .all() of the date range, enriched with N+1 queries>
    rows = my_sort(request, rows)          # getKey(field) over the nested dict
    rows = my_filter(request, rows)        # filter_mui  (MUI DataGrid operators)
    rows = my_custom_filter(...)           # customeFilterBranchesValue[] / StatusesValue[]
                                           # (+ BranchesValue2[] / MoveDateValue[] on exports)
    rows = FilterServerSide(request, rows) # the *_Qkey[]/_Jkey/_Type/_Operator quad
    page = rows[start:end]                 # my_pagination, pageSize default 200

Everything here translates that contract to SQL. The wire contract is unchanged:
same parameter names, same operators, same per-endpoint `pageSize` default (200 for
every route this module serves), same dotted `field` paths — including the `x_.y`
spelling, because legacy `getKey` strips a trailing `_` from every path segment.

Nothing in this module touches the ORM identity map: callers select **labelled
columns** (see `model_cols` / `take`) so an export can stream with `yield_per` and
flat memory.
"""
import datetime
import logging

from sqlalchemy import Text, and_, cast, func, inspect as sa_inspect, or_

from extensions import db

log = logging.getLogger("cawp.reports")

DEFAULT_PAGE_SIZE = 200          # my_pagination(), app.py:8193
EXPORT_MESSAGE = "تم تحميل الملف بنجاح"


# --------------------------------------------------------------------------- #
# labelled-column helpers (no ORM entities -> no identity map -> flat memory)
# --------------------------------------------------------------------------- #
_COL_PAIRS = {}


def col_pairs(entity):
    """[(mapper attribute key, column name)] for a model class *or* an alias of one.

    `entity.__table__` is useless for an `aliased()` entity (it returns the base table),
    so the mapper is inspected instead and the expression is read back off the alias.
    """
    cached = _COL_PAIRS.get(entity)
    if cached is None:
        mapper = sa_inspect(entity).mapper
        secret = getattr(mapper.class_, "SECRET_COLUMNS", frozenset())  # User.password
        cached = tuple((prop.key, prop.columns[0].name) for prop in mapper.column_attrs
                       if prop.columns[0].name not in secret)
        _COL_PAIRS[entity] = cached
    return cached


def model_cols(entity, prefix):
    """Every column of `entity`, labelled `<prefix>__<column name>`."""
    return [getattr(entity, key).label("%s__%s" % (prefix, name))
            for key, name in col_pairs(entity)]


_IS_USER = {}


def _is_user(entity):
    hit = _IS_USER.get(entity)
    if hit is None:
        hit = _IS_USER[entity] = getattr(sa_inspect(entity).mapper.class_, "__tablename__", None) == "users"
    return hit


def take(row, entity, prefix):
    """Rebuild `entity.as_dict()` for `row`; None when the outer join found nothing.
    User rows (or aliases) get the same protected-field redaction as `User.as_dict()`."""
    if getattr(row, "%s__id" % prefix, None) is None:
        return None
    d = {name: getattr(row, "%s__%s" % (prefix, name))
         for _, name in col_pairs(entity)}
    if _is_user(entity):
        from core.user_privacy import redact
        redact(d)
    return d


def with_name(d, strip=False):
    """`frist_name + ' ' + last_name` -> `name`, exactly as the legacy loops do.

    `strip=True` is the `_wp_user_dict` variant (app.py:3499) used by `/comments` and
    `/payments`; every other handler concatenates without `.strip()`, so a user whose
    `last_name` is empty gets a trailing space there. Both spellings are preserved.
    """
    if d is None:
        return None
    name = (d.get("frist_name") or "") + " " + (d.get("last_name") or "")
    d["name"] = name.strip() if strip else name
    return d


# --------------------------------------------------------------------------- #
# request parsing
# --------------------------------------------------------------------------- #
def parse_paging(request, default_page_size=DEFAULT_PAGE_SIZE):
    args = request.args
    try:
        page = int(args["page"]) if "page" in args else 1
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(args["pageSize"]) if "pageSize" in args else default_page_size
    except (TypeError, ValueError):
        page_size = default_page_size
    if page < 1:
        page = 1
    if page_size < 0:
        page_size = default_page_size
    return page, page_size, (page - 1) * page_size


def date_range(request, key):
    """`customeFilterDateValue[]` / `customeFilterMoveDateValue[]` -> ('YYYY-MM-DD', 'YYYY-MM-DD').

    Legacy indexes [0]/[1] blindly; returns None here when the pair is absent so the
    caller can decide (the legacy routes raise -> 201, which we reproduce explicitly).
    """
    values = request.args.getlist(key)
    if len(values) < 2:
        return None
    return values[0], values[1]


def _day_start(value):
    return datetime.datetime.strptime(value, "%Y-%m-%d")


def _day_after(value):
    return _day_start(value) + datetime.timedelta(days=1)


def inclusive_day_range(col, start, end):
    """`func.date(col) >= start AND func.date(col) <= end` rewritten as a sargable range.

    The database session runs in UTC (`SHOW timezone` = UTC) and every timestamp column
    is `timestamptz`, so `date(col)` and the half-open range agree exactly — but only the
    range form can use `ix_comments_create_at` / `ix_paymentdates_payment_date`.
    """
    return and_(col >= _day_start(start), col < _day_after(end))


def between_range(col, start, end):
    """`between(col, '<start>', '<end>')` — the *payments* spelling.

    Note this is NOT the same as `inclusive_day_range`: the bare date string is coerced
    to midnight, so the end day is excluded except for exactly 00:00:00. That quirk is
    part of the wire contract of /payments, /payments/user, /payments/export and
    /payments/approval_status, so it is reproduced verbatim.
    """
    return col.between(_day_start(start), _day_start(end))


# --------------------------------------------------------------------------- #
# dotted-path resolution
# --------------------------------------------------------------------------- #
def normalize_path(path):
    """`user_.frist_name` -> `user.frist_name` (legacy getKey strips a trailing `_`)."""
    if not path:
        return path
    return ".".join(seg[:-1] if seg.endswith("_") else seg for seg in path.split("."))


def resolve(col_map, path):
    if not path:
        return None
    if path in col_map:
        return col_map[path]
    return col_map.get(normalize_path(path))


def _is_text(col):
    try:
        return isinstance(col.type, Text) or col.type.python_type is str
    except (NotImplementedError, AttributeError):
        return False


def _as_text(col):
    return col if _is_text(col) else cast(col, Text)


# --------------------------------------------------------------------------- #
# MUI DataGrid single-filter  (filterOperator / filterField / filterFieldType /
#                              filterValue / filterValue[])
# --------------------------------------------------------------------------- #
def mui_clause(col, operator, value, values, field_type):
    """Translate one `filter_mui` branch (app.py:8223) to a SQL clause, or None."""
    if col is None or not operator:
        return None

    # ---- no-value operators (legacy checks these after the typed blocks, but they
    #      are unreachable there because those all require `value is not None`)
    if operator == "isEmpty" and value is None:
        return or_(col.is_(None), _as_text(col) == "")
    if operator == "isNotEmpty" and value is None:
        return and_(col.isnot(None), _as_text(col) != "")

    if field_type == "number" and value is not None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if operator == "=":
            return col == v
        if operator == "!=":                      # Python `None != 5.0` is True
            return or_(col != v, col.is_(None))
        if operator == ">":
            return col > v
        if operator == ">=":
            return col >= v
        if operator == "<":
            return col < v
        if operator == "<=":
            return col <= v

    if field_type == "string" and value is not None:
        sv = str(value)
        if operator == "contains":
            return _as_text(col).ilike("%" + _esc(sv) + "%", escape="\\")
        if operator == "equals":
            return _as_text(col) == sv
        if operator == "startsWith":
            return _as_text(col).ilike(_esc(sv) + "%", escape="\\")
        if operator == "endsWith":
            return _as_text(col).ilike("%" + _esc(sv), escape="\\")

    if field_type == "date" and value is not None:
        try:
            d = datetime.datetime.strptime(str(value), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
        dc = func.date(col)
        if operator == "is":
            return dc == d
        if operator == "not":                     # legacy keeps NULL rows
            return or_(dc != d, col.is_(None))
        if operator == "after":
            return dc > d
        if operator == "onOrAfter":
            return dc >= d
        if operator == "before":
            return dc < d
        if operator == "onOrBefore":
            return dc <= d

    if field_type == "singleSelect" and value is not None:
        if operator == "is":
            return _as_text(col) == str(value)
        if operator == "not":
            return or_(_as_text(col) != str(value), col.is_(None))

    if values:
        if operator == "isAnyOf" and field_type == "singleSelect":
            return _as_text(col).in_([str(v) for v in values])
        if operator == "isAnyOf" and field_type == "number":
            try:
                return col.in_([float(v) for v in values])
            except (TypeError, ValueError):
                return None
        if operator == "isAnyOf" and field_type == "string":
            return _as_text(col).in_([str(v) for v in values])

    return None


def _esc(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_mui_filter(query, request, col_map):
    args = request.args
    operator = args.get("filterOperator")
    field = args.get("filterField")
    field_type = args.get("filterFieldType")
    value = args.get("filterValue")
    values = args.getlist("filterValue[]")
    if not operator or not field:
        return query
    col = resolve(col_map, field)
    if col is None:
        # unmapped column: ignore rather than 500 (legacy would silently return [])
        log.debug("mui filter on unmapped field %r ignored", field)
        return query
    clause = mui_clause(col, operator, value, values, field_type)
    return query.filter(clause) if clause is not None else query


# --------------------------------------------------------------------------- #
# generic `*_Qkey[]` + `*_Jkey` + `*_Type` (+ `*_Operator`, unused by legacy too)
# classes/FilterServerSide.py
# --------------------------------------------------------------------------- #
def apply_qkey_filters(query, request, col_map):
    for key in list(request.args.keys()):
        if key.endswith("_Qkey"):
            base = key[: -len("_Qkey")]
        elif key.endswith("_Qkey[]"):
            base = key[: -len("_Qkey[]")]
        else:
            continue
        values = [v for v in request.args.getlist(key) if v != ""]
        if not values:
            continue
        path = request.args.get(base + "_Jkey")
        vtype = request.args.get(base + "_Type")
        col = resolve(col_map, path)
        if col is None:
            log.debug("Qkey filter %s -> %r unmapped, ignored", key, path)
            continue
        try:
            if vtype == "string":
                query = query.filter(_as_text(col).in_([str(v) for v in values]))
            elif vtype == "bool":
                bools = [v.lower() == "true" for v in values if v.lower() in ("true", "false")]
                if bools:
                    query = query.filter(col.in_(bools))
            elif vtype == "number":
                nums = [int(v) for v in values]
                if len(nums) >= 2:
                    query = query.filter(col.between(nums[0], nums[1]))
                else:
                    query = query.filter(col == nums[0])
            elif vtype == "date":
                dates = [datetime.datetime.strptime(v, "%Y-%m-%d").date() for v in values]
                query = query.filter(func.date(col).in_(dates))
        except (TypeError, ValueError) as e:
            log.debug("Qkey filter %s ignored: %s", key, e)
    return query


# --------------------------------------------------------------------------- #
# custom filters  (my_custom_filter, app.py:8353)
# --------------------------------------------------------------------------- #
def apply_custom_filters(query, request, col_map, export=False):
    """`customeFilterBranchesValue[]`, `customeFilterStatusesValue[]` and — on exports —
    `customeFilterBranchesValue2[]` and `customeFilterMoveDateValue[]`.

    Each one is only applied when the endpoint's `col_map` actually exposes the path the
    legacy code reads out of the row dict; otherwise it is ignored. Legacy instead
    evaluated `str(None) in values` (-> an empty grid) or, for `customeFilterMoveDateValue[]`
    on a row without `payment_date`, raised `AttributeError` -> HTTP 500. That 500 is the
    one documented deviation here: `/comments/export?...customeFilterMoveDateValue[]=...`
    now returns rows instead of crashing.
    """
    branches = [v for v in request.args.getlist("customeFilterBranchesValue[]") if v != ""]
    if branches:
        col = resolve(col_map, "branch_id")
        if col is not None:
            query = query.filter(_as_text(col).in_(branches))

    statuses = [v.lower() for v in request.args.getlist("customeFilterStatusesValue[]")]
    bools = [s == "true" for s in statuses if s in ("true", "false")]
    if bools:
        col = resolve(col_map, "is_active")
        if col is not None:
            query = query.filter(col.in_(bools))

    if export:
        branches2 = [v for v in request.args.getlist("customeFilterBranchesValue2[]") if v != ""]
        if branches2:
            col = resolve(col_map, "user.branch_id")
            if col is not None:
                query = query.filter(_as_text(col).in_(branches2))

        move = date_range(request, "customeFilterMoveDateValue[]")
        if move:
            col = resolve(col_map, "payment_date")
            if col is not None:
                try:
                    query = query.filter(inclusive_day_range(col, move[0], move[1]))
                except ValueError:
                    pass
    return query


def apply_branch2_filter(query, request, col_map):
    """`customeFilterBranchesValue2[]` — the *payment user's* branch (app.py:4549).

    On the list routes legacy applies this in Python after enrichment, not through
    `my_custom_filter`, so it is a separate call site.
    """
    branches = [v for v in request.args.getlist("customeFilterBranchesValue2[]") if v != ""]
    if not branches:
        return query
    col = resolve(col_map, "user.branch_id")
    if col is None:
        return query
    return query.filter(_as_text(col).in_(branches))


# --------------------------------------------------------------------------- #
# sorting
# --------------------------------------------------------------------------- #
def apply_sort(query, request, sort_map, default_order):
    """`field` (dotted, `x.y` or `x_.y`) + `sort` (asc|desc).

    Legacy lowercases strings before comparing and substitutes `""` for a missing key, so
    ascending puts empty/NULL first and descending puts it last; `func.lower()` +
    explicit NULL placement reproduces both.
    """
    field = request.args.get("field")
    direction = request.args.get("sort")
    if not field or not direction:
        return query.order_by(*default_order)
    col = resolve(sort_map, field)
    if col is None:
        log.debug("sort on unmapped field %r ignored", field)
        return query.order_by(*default_order)
    # `COLLATE "C"` makes Postgres order text by UTF-8 byte value, which is exactly what
    # Python's `sorted()` did over the lowercased strings. The database default here is
    # en_US.utf8, whose linguistic rules put Arabic names in a different order.
    expr = func.lower(col).collate("C") if _is_text(col) else col
    if str(direction).lower() == "desc":
        order = expr.desc().nullslast()
    else:
        order = expr.asc().nullsfirst()
    return query.order_by(order, *default_order)


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def apply_all_filters(query, request, col_map, export=False):
    query = apply_mui_filter(query, request, col_map)
    query = apply_custom_filters(query, request, col_map, export=export)
    query = apply_qkey_filters(query, request, col_map)
    return query


def count_rows(query):
    """Light COUNT over the same FROM/WHERE, without ORDER BY or the column list."""
    return query.order_by(None).with_entities(func.count()).scalar() or 0


def sum_column(query, col):
    return query.order_by(None).with_entities(func.coalesce(func.sum(col), 0.0)).scalar() or 0


def page_slice(query, page_size, offset):
    if page_size <= 0:
        return query
    return query.offset(offset).limit(page_size)


def safe_dumps(obj):
    """`core.serialization.dumps`.

    Kept as a named helper because every export/row serializer in this module calls it.
    The out-of-64-bit-integer guard that used to live here (`Clints.attribute` is
    free-form JSONB and a few rows hold 25-digit reference numbers, which orjson
    refuses) now lives in `core.serialization.dumps` itself, where it falls back to the
    stdlib encoder -- that renders the big integer verbatim, exactly like the pre-orjson
    Flask provider did, instead of stringifying it.
    """
    from core.serialization import dumps

    return dumps(obj)


def safe_jsonify(payload):
    """`jsonify(payload)` -- the app's JSON provider is `core.serialization`, so the
    same big-integer fallback applies."""
    from flask import jsonify

    return jsonify(payload)


def stream_json(list_key, rows_iter, message=EXPORT_MESSAGE):
    """Export body generator: `{"<list_key>": [...], "message": "...", "status": true}`.

    Rows are serialized one at a time with orjson (same datetime rendering as the app's
    JSON provider) so a 134 MB export never exists in memory at once.
    """
    yield b'{"' + list_key.encode() + b'": ['
    first = True
    for row in rows_iter:
        if first:
            first = False
        else:
            yield b","
        yield safe_dumps(row)
    tail = safe_dumps({"message": message, "status": True})
    yield b"]," + tail[1:]


__all__ = [
    "DEFAULT_PAGE_SIZE", "EXPORT_MESSAGE", "model_cols", "take", "with_name",
    "parse_paging", "date_range", "inclusive_day_range", "between_range",
    "normalize_path", "resolve", "mui_clause", "apply_mui_filter",
    "apply_qkey_filters", "apply_custom_filters", "apply_branch2_filter",
    "apply_sort", "apply_all_filters", "count_rows", "sum_column", "page_slice",
    "stream_json", "safe_dumps", "safe_jsonify", "col_pairs", "db",
]
