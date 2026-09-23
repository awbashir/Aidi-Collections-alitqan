"""Process-local TTL cache for small reference tables (users, roles, contracts, portfolios,
branches, statuses...). These are read on almost every request to enrich rows and were the
source of most N+1 queries (`User.query.get(row.user_id)` inside loops).

One dict per table, refreshed at most every TTL seconds, invalidated explicitly on writes.
Cheaper than Redis (no network hop, no deserialization) and correct across gunicorn threads.
"""
import threading
import time

_TTL = 60.0
_lock = threading.RLock()
_store = {}  # name -> (expires_at, {id_str: dict})


def get_map(name, loader, ttl=_TTL):
    """Return {str(id): row_dict} for `name`, loading via `loader()` (-> iterable of dicts)."""
    now = time.monotonic()
    with _lock:
        hit = _store.get(name)
        if hit and hit[0] > now:
            return hit[1]
    rows = loader()
    mapping = {str(r["id"]): r for r in rows}
    with _lock:
        _store[name] = (now + ttl, mapping)
    return mapping


def invalidate(*names):
    with _lock:
        if not names:
            _store.clear()
        for n in names:
            _store.pop(n, None)


def stats():
    with _lock:
        return {k: len(v[1]) for k, v in _store.items()}
