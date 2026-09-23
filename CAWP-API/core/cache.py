"""Redis cache helpers.

- JSON payloads (orjson) instead of pickle: safe to deserialize, cross-process, inspectable.
- `delete_pattern` uses SCAN + UNLINK in batches; the old code used KEYS, which is O(N) and
  blocks Redis for every other client (the previous developer's comment at the neglect report:
  "never use redis_obj.keys() — it froze the system for all employees").
- Every operation is best-effort: if Redis is down the API keeps working (just slower).
"""
import functools
import logging
import time

import redis

from core.config import Config
from core.serialization import cache_dumps, cache_loads

log = logging.getLogger("cawp.cache")

_client = redis.Redis.from_url(
    Config.REDIS_URL,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
    decode_responses=False,
)


def client() -> redis.Redis:
    return _client


def get(key, default=None):
    try:
        raw = _client.get(key)
    except redis.RedisError as e:  # pragma: no cover
        log.warning("redis get %s failed: %s", key, e)
        return default
    if raw is None:
        return default
    try:
        return cache_loads(raw)
    except Exception:
        # legacy pickle / foreign payload: drop it
        try:
            _client.unlink(key)
        except redis.RedisError:
            pass
        return default


def set(key, value, ttl=None):
    ttl = Config.CACHE_DEFAULT_TTL if ttl is None else ttl
    try:
        payload = cache_dumps(value)
        if ttl and ttl > 0:
            _client.setex(key, int(ttl), payload)
        else:
            _client.set(key, payload)
        return True
    except (redis.RedisError, TypeError) as e:
        log.warning("redis set %s failed: %s", key, e)
        return False


def delete(*keys):
    if not keys:
        return 0
    try:
        return _client.unlink(*keys)
    except redis.RedisError as e:
        log.warning("redis unlink failed: %s", e)
        return 0


def delete_pattern(pattern, batch=500):
    """Non-blocking replacement for KEYS pattern + DELETE."""
    removed = 0
    try:
        pipe = _client.pipeline(transaction=False)
        n = 0
        for key in _client.scan_iter(match=pattern, count=batch):
            pipe.unlink(key)
            n += 1
            if n >= batch:
                removed += sum(pipe.execute())
                n = 0
        if n:
            removed += sum(pipe.execute())
    except redis.RedisError as e:
        log.warning("redis delete_pattern %s failed: %s", pattern, e)
    return removed


def bump_version(namespace):
    """Version-tagged namespaces: invalidation is one INCR instead of a scan.
    Keys are built as f"{namespace}:v{version}:..." via `versioned_key`."""
    try:
        return _client.incr(f"ver:{namespace}")
    except redis.RedisError:
        return None


def versioned_key(namespace, *parts):
    try:
        v = _client.get(f"ver:{namespace}") or b"0"
        v = v.decode() if isinstance(v, bytes) else str(v)
    except redis.RedisError:
        v = "0"
    return ":".join([namespace, f"v{v}", *[str(p) for p in parts]])


def cached(key_builder, ttl=None):
    """Decorator: cache the (JSON-able) return value of a view helper.

    key_builder(*args, **kwargs) -> str | None (None = bypass cache).
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_builder(*args, **kwargs)
            if key is None:
                return fn(*args, **kwargs)
            hit = get(key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            set(key, value, ttl)
            return value

        return wrapper

    return deco


def ping():
    try:
        return bool(_client.ping())
    except redis.RedisError:
        return False


class Timer:
    """with Timer() as t: ...; t.ms"""

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._t) * 1000
