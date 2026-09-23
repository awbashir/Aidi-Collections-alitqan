"""Fast JSON for API responses and cache payloads (orjson, with Flask fallback types)."""
import dataclasses
import datetime as _dt
import decimal
import json
import uuid

import orjson
from flask.json.provider import JSONProvider


def _default(o):
    if isinstance(o, (uuid.UUID,)):
        return str(o)
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return list(o)
    if isinstance(o, _dt.date) and not isinstance(o, _dt.datetime):
        return o.isoformat()
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if hasattr(o, "as_dict"):
        return o.as_dict()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# Flask's default provider renders datetimes as RFC 822 ("Tue, 16 Sep 2026 00:12:00 GMT").
# The React frontend parses those with dayjs/moment, which also accept ISO-8601, but to keep
# the wire format byte-identical we reproduce the RFC 822 rendering for datetimes.
def _http_date(o: _dt.datetime) -> str:
    from werkzeug.http import http_date

    return http_date(o)


_BASE_OPTS = orjson.OPT_PASSTHROUGH_DATETIME | orjson.OPT_NON_STR_KEYS


def _response_default(o):
    if isinstance(o, _dt.datetime):
        return _http_date(o)
    return _default(o)


def _stdlib_dumps(obj, default) -> bytes:
    # Slow path, only for payloads orjson rejects (integers beyond 64 bits - a handful of
    # `clints.attribute` JSONB values hold 19+ digit account numbers stored as numbers).
    return json.dumps(obj, default=default, ensure_ascii=False, separators=(",", ":")).encode()


def dumps(obj, *, option=0) -> bytes:
    try:
        return orjson.dumps(obj, default=_response_default, option=option | _BASE_OPTS)
    except TypeError as e:
        if "64-bit" in str(e) or "Integer exceeds" in str(e):
            return _stdlib_dumps(obj, _response_default)
        raise


def loads(s):
    return orjson.loads(s)


class ORJSONProvider(JSONProvider):
    """Drop-in Flask JSON provider: ~5-10x faster than the stdlib encoder on big lists."""

    def dumps(self, obj, **kwargs):
        return dumps(obj).decode()

    def loads(self, s, **kwargs):
        return loads(s)

    def response(self, *args, **kwargs):
        obj = self._prepare_response_obj(args, kwargs)
        return self._app.response_class(dumps(obj) + b"\n", mimetype="application/json")


# Cache payloads: datetimes as ISO-8601 (round-trippable), never pickle.
def _cache_default(o):
    if isinstance(o, _dt.datetime):
        return o.isoformat()
    return _default(o)


def cache_dumps(obj) -> bytes:
    try:
        return orjson.dumps(obj, default=_default, option=orjson.OPT_NAIVE_UTC | orjson.OPT_NON_STR_KEYS)
    except TypeError as e:
        if "64-bit" in str(e) or "Integer exceeds" in str(e):
            return _stdlib_dumps(obj, _cache_default)
        raise


def cache_loads(b):
    return orjson.loads(b)
