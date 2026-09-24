"""AI retention: purge AI drafts, transcripts and audio per the AI data policy, finish provider
calls a restart interrupted, and delete old provider-call audit rows.

Q0 does not know the Q1/Q2 tables. They register **hooks**:

    # services/ai/retention_hooks/q1_drafts.py  (any module in that package is imported by discover())
    from services.ai.retention import register

    def purge_drafts(session, *, cutoff, limit):
        # delete or clear at most `limit` items whose retention clock started before `cutoff`;
        # use `session` and do NOT commit; return how many were purged; already purged items are
        # not counted again (idempotent)
        return session.execute(text("DELETE FROM ai_drafts WHERE id IN (SELECT id FROM ai_drafts "
                                    "WHERE created_at < :c AND NOT saved LIMIT :n)"), {"c": cutoff, "n": limit}).rowcount

    register("q1.ai_drafts", "drafts", purge_drafts, "unsaved AI drafts")

Categories and the policy field that sets their period: `drafts` (draft_days), `transcripts`
(transcript_days), `audio` (audio_days). A hook that removes files must not unlink inside the
database transaction: record a durable purge intent first and remove the file after the commit
(the Phase 2 retention pattern), so a crash never loses track of a file.

One run (`run()`):

1. provider-call rows still `started` after AI_CALL_STALE_SECONDS become `interrupted` (the work is
   never replayed); one audit event per run that changed any;
2. **no policy recorded -> nothing is purged** (there is no default retention period); a
   suspended policy still applies its retention periods;
3. each hook, in batches: a transaction-scoped advisory lock (a concurrent runner skips), the
   hook's purge, and an `ai_retention.purge` audit event with the hook, category, days, cutoff,
   policy revision and count, committed together. A failing hook is logged and does not stop the
   others;
4. ai_provider_calls rows finished before now - provider_log_days are deleted the same way
   (`SET LOCAL cawp.retention_purge = 'ai_provider_calls'`, which the 0060 guard requires).

Running it: from the worker loop (`worker_tick()`, self-throttled by AI_RETENTION_INTERVAL_SECONDS),
or once with `python -m services.ai.retention`.

| Setting | Default | Accepted |
|---|---|---|
| `AI_RETENTION_INTERVAL_SECONDS` | 3600 | 60-86400 |
| `AI_RETENTION_BATCH` | 1000 | 10-50000 |
| `AI_RETENTION_MAX_BATCHES` | 20 | 1-1000 (per hook per run; the rest waits for the next run) |
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import importlib
import json
import logging
import pkgutil
import time
from typing import Callable

from sqlalchemy import text

log = logging.getLogger("cawp.ai.retention")

CATEGORY_DAYS = {"drafts": "drafts", "transcripts": "transcripts", "audio": "audio"}   # hook category -> policy key
ADVISORY_KEY = 7_236_405_060
SOURCE = "ai_retention"


@dataclasses.dataclass(frozen=True)
class RetentionHook:
    name: str
    category: str
    purge: Callable
    description: str = ""


_HOOKS: dict[str, RetentionHook] = {}
_discovered = {"done": False}
_warned = {"migrations": False}


def register(name: str, category: str, purge: Callable, description: str = "") -> RetentionHook:
    """Register (or replace, so a re-import is harmless) a purge hook."""
    if category not in CATEGORY_DAYS:
        raise ValueError(f"category must be one of {sorted(CATEGORY_DAYS)}")
    if not name or not isinstance(name, str) or len(name) > 100:
        raise ValueError("a hook needs a name (at most 100 characters)")
    if not callable(purge):
        raise ValueError("purge must be callable")
    hook = RetentionHook(name, category, purge, description)
    _HOOKS[name] = hook
    return hook


def unregister(name: str) -> None:
    _HOOKS.pop(name, None)


def hooks() -> list[RetentionHook]:
    return [_HOOKS[k] for k in sorted(_HOOKS)]


def discover() -> list[str]:
    """Import every module of services/ai/retention_hooks (they call register())."""
    from services.ai import retention_hooks
    names = []
    for mod in pkgutil.iter_modules(retention_hooks.__path__):
        full = f"{retention_hooks.__name__}.{mod.name}"
        try:
            importlib.import_module(full)
            names.append(full)
        except Exception:  # noqa: BLE001 - one broken hook module must not stop the others
            log.exception("AI retention hook module %s failed to import", full)
    _discovered["done"] = True
    return names


def _int_env(name, default, lo, hi):
    from services.ai.config import _num_env
    return _num_env(name, default, lo, hi, int)


def settings() -> dict:
    return {"interval": _int_env("AI_RETENTION_INTERVAL_SECONDS", 3600, 60, 86400),
            "batch": _int_env("AI_RETENTION_BATCH", 1000, 10, 50000),
            "max_batches": _int_env("AI_RETENTION_MAX_BATCHES", 20, 1, 1000)}


def _audit(session, action, entity_id, after, reason):
    from core import audit
    audit.record(action, "ai_retention", entity_id, actor_user_id=None, request_id=None, idempotency_key=None,
                 source=SOURCE, session=session, after=after, reason=reason)


def mark_interrupted(session, *, stale_seconds: int) -> int:
    """Finish `started` provider-call rows older than stale_seconds as `interrupted`."""
    n = session.execute(text("""
        UPDATE ai_provider_calls SET outcome = 'interrupted', finished_at = now(), error_code = 'interrupted'
         WHERE outcome = 'started' AND started_at < now() - make_interval(secs => :s)"""),
        {"s": stale_seconds}).rowcount
    if n:
        _audit(session, "ai_retention.interrupted", "ai_provider_calls",
               {"table": "ai_provider_calls", "interrupted": n, "stale_seconds": stale_seconds},
               "provider work interrupted by a restart or crash; never replayed")
    session.commit()
    return n


def _batches(session, cfg, report_entry, work) -> bool:
    """Run `work(session) -> n` in batches, one transaction each. False when another runner holds the lock."""
    for _ in range(cfg["max_batches"]):
        if not session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY}).scalar():
            session.rollback()
            return False
        n = work(session)
        report_entry["purged"] += n
        session.commit()
        if n < cfg["batch"]:
            break
    return True


def run(session=None, *, now: _dt.datetime | None = None) -> dict:
    """One retention pass. Returns a JSON-able report (counts, cutoffs; never content)."""
    from extensions import db
    from services.ai import config as config_mod
    from services.ai import policy as policy_mod
    session = session or db.session
    cfg = settings()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if not _discovered["done"]:
        discover()
    report: dict = {"skipped": False, "policy_revision": None, "interrupted": 0, "hooks": {},
                    "provider_calls": None}
    ready = session.execute(text("SELECT to_regclass('public.ai_provider_calls') IS NOT NULL "
                                 "AND to_regclass('public.ai_data_policies') IS NOT NULL")).scalar()
    session.rollback()
    if not ready:
        if not _warned["migrations"]:
            log.warning("AI retention skipped: migrations 0059/0060 are not applied")
            _warned["migrations"] = True
        report.update(skipped=True, reason="migrations_not_applied")
        return report
    report["interrupted"] = mark_interrupted(session, stale_seconds=config_mod.load().call_stale_seconds)
    pol = policy_mod.current(session)
    session.rollback()
    if pol is None:
        report["policy"] = "missing"
        return report
    report["policy_revision"] = pol.revision
    for hook in hooks():
        days = pol.retention_days[CATEGORY_DAYS[hook.category]]
        cutoff = now - _dt.timedelta(days=days)
        entry = {"category": hook.category, "days": days, "cutoff": cutoff.isoformat(), "purged": 0}
        report["hooks"][hook.name] = entry

        def work(s, hook=hook, cutoff=cutoff, days=days, entry=entry):
            n = int(hook.purge(s, cutoff=cutoff, limit=cfg["batch"]) or 0)
            if n:
                _audit(s, "ai_retention.purge", hook.name,
                       {"hook": hook.name, "category": hook.category, "days": days, "cutoff": entry["cutoff"],
                        "policy_revision": pol.revision, "purged": n},
                       f"AI data policy revision {pol.revision}: {hook.category} kept {days} days")
            return n
        try:
            if not _batches(session, cfg, entry, work):
                report["skipped"] = True
                return report
        except Exception as e:  # noqa: BLE001 - one hook must not stop the others
            session.rollback()
            entry["error"] = type(e).__name__
            log.exception("AI retention hook %s failed", hook.name)

    days = pol.retention_days["provider_logs"]
    cutoff = now - _dt.timedelta(days=days)
    entry = {"days": days, "cutoff": cutoff.isoformat(), "purged": 0}
    report["provider_calls"] = entry

    def purge_calls(s):
        s.execute(text("SELECT set_config('cawp.retention_purge', 'ai_provider_calls', true)"))
        n = s.execute(text("""
            DELETE FROM ai_provider_calls WHERE id IN (
                SELECT id FROM ai_provider_calls
                 WHERE outcome <> 'started' AND coalesce(finished_at, started_at) < :cutoff
                 ORDER BY started_at, id LIMIT :n)"""), {"cutoff": cutoff, "n": cfg["batch"]}).rowcount
        if n:
            _audit(s, "ai_retention.purge", "ai_provider_calls",
                   {"table": "ai_provider_calls", "days": days, "cutoff": entry["cutoff"],
                    "policy_revision": pol.revision, "purged": n},
                   f"AI data policy revision {pol.revision}: provider logs kept {days} days")
        return n
    try:
        if not _batches(session, cfg, entry, purge_calls):
            report["skipped"] = True
    except Exception as e:  # noqa: BLE001
        session.rollback()
        entry["error"] = type(e).__name__
        log.exception("AI provider-call retention failed")
    if any(h["purged"] for h in report["hooks"].values()) or entry["purged"] or report["interrupted"]:
        summary = {k: v for k, v in report.items() if k != "hooks"}
        summary["hooks"] = {k: v["purged"] for k, v in report["hooks"].items()}
        log.info("AI retention: %s", json.dumps(summary, default=str))
    return report


_next_run = {"at": 0.0}


def worker_tick(session=None) -> dict | None:
    """For the worker loop: `run()` at most every AI_RETENTION_INTERVAL_SECONDS; never raises."""
    now = time.monotonic()
    if now < _next_run["at"]:
        return None
    _next_run["at"] = now + settings()["interval"]
    try:
        return run(session)
    except Exception:  # noqa: BLE001 - e.g. migrations 0059/0060 not applied yet
        log.exception("AI retention failed")
        try:
            from extensions import db
            (session or db.session).rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def main(argv=None) -> int:
    import argparse
    argparse.ArgumentParser(prog="python -m services.ai.retention",
                            description="Run the AI retention task once").parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    from worker.main import create_app
    app = create_app()
    with app.app_context():
        print(json.dumps(run(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
