"""Phase 6 Q0: the AI retention task (services/ai/retention.py) and its hook registry.

Hooks are registered here against scratch tables standing in for the Q1 drafts and Q2 transcripts /
audio references. Checks: no policy -> nothing purged; each category uses its own policy period;
purges are audited and idempotent; a failing hook does not stop the others; interrupted provider
calls are marked (never replayed); old provider-call rows are deleted, unfinished ones never; the
worker calls the task and survives its failure.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_retention.py
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text

from test_ai_helpers import ai, ai_app, ai_env, insert_policy, pgc, q1, qall, reset_ai_state, run  # noqa: F401

TABLES = ("ai_test_drafts", "ai_test_transcripts", "ai_test_audio")


@pytest.fixture
def scratch(pgc):
    """Three stand-in tables and hooks on them; everything removed afterwards."""
    from services.ai import retention
    reset_ai_state(pgc)
    _T0["at"] = q1(pgc, "SELECT clock_timestamp()")[0]
    for t in TABLES:
        run(pgc, f"DROP TABLE IF EXISTS {t}")
        run(pgc, f"CREATE TABLE {t} (id serial PRIMARY KEY, created_at timestamptz NOT NULL, body text)")
    seen = {}

    def hook_for(table):
        def purge(session, *, cutoff, limit):
            seen.setdefault(table, []).append(cutoff)
            return session.execute(text(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} "
                                        "WHERE created_at < :c ORDER BY id LIMIT :n)"), {"c": cutoff, "n": limit}).rowcount
        return purge

    retention.register("test.drafts", "drafts", hook_for("ai_test_drafts"), "stand-in for Q1 drafts")
    retention.register("test.transcripts", "transcripts", hook_for("ai_test_transcripts"))
    retention.register("test.audio", "audio", hook_for("ai_test_audio"))
    retention._discovered["done"] = True
    yield seen
    for name in ("test.drafts", "test.transcripts", "test.audio", "test.broken"):
        retention.unregister(name)
    for t in TABLES:
        run(pgc, f"DROP TABLE IF EXISTS {t}")


def add_rows(pgc, table, *ages_days):
    for age in ages_days:
        run(pgc, f"INSERT INTO {table} (created_at, body) VALUES (now() - make_interval(days => %s), 'content')", age)


def count(pgc, table):
    return q1(pgc, f"SELECT count(*) FROM {table}")[0]


def run_once(ai_app, **kw):
    from services.ai import retention
    with ai_app.app_context():
        return retention.run(**kw)


def purge_events(pgc, entity_id):
    """ai_retention.purge events of this test only (after the `scratch` fixture started)."""
    return qall(pgc, "SELECT after, reason, source, actor_user_id FROM audit_events WHERE action = 'ai_retention.purge' "
                     "AND entity_id = %s AND occurred_at >= %s ORDER BY occurred_at, id", entity_id, _T0["at"])


_T0 = {"at": None}


def test_no_policy_means_nothing_is_purged(ai_app, pgc, scratch):
    add_rows(pgc, "ai_test_drafts", 400, 1)
    report = run_once(ai_app)
    assert report["policy"] == "missing" and report["hooks"] == {}
    assert count(pgc, "ai_test_drafts") == 2 and scratch == {}


def test_each_category_uses_its_policy_period_audited_and_idempotent(ai_app, pgc, scratch):
    rev = insert_policy(pgc, retention_days={"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365})
    add_rows(pgc, "ai_test_drafts", 20, 13)            # drafts: 14 days
    add_rows(pgc, "ai_test_transcripts", 100, 60)      # transcripts: 90 days
    add_rows(pgc, "ai_test_audio", 60, 31, 29)         # audio: 30 days
    now = dt.datetime.now(dt.timezone.utc)
    report = run_once(ai_app, now=now)
    assert report["policy_revision"] == rev
    assert {k: (v["category"], v["days"], v["purged"]) for k, v in report["hooks"].items()} == {
        "test.drafts": ("drafts", 14, 1), "test.transcripts": ("transcripts", 90, 1), "test.audio": ("audio", 30, 2)}
    assert (count(pgc, "ai_test_drafts"), count(pgc, "ai_test_transcripts"), count(pgc, "ai_test_audio")) == (1, 1, 1)
    assert scratch["ai_test_audio"][0] == now - dt.timedelta(days=30)
    ev = purge_events(pgc, "test.audio")
    assert len(ev) == 1
    after, reason, source, actor = ev[0]
    assert after == {"hook": "test.audio", "category": "audio", "days": 30, "cutoff": report["hooks"]["test.audio"]["cutoff"],
                     "policy_revision": rev, "purged": 2}
    assert source == "ai_retention" and actor is None and f"revision {rev}" in reason
    assert "content" not in str(after)
    # a second run purges nothing and writes no new event
    again = run_once(ai_app, now=now)
    assert all(v["purged"] == 0 for v in again["hooks"].values())
    assert len(purge_events(pgc, "test.audio")) == 1


def test_batches_are_bounded(ai_app, pgc, scratch, monkeypatch):
    insert_policy(pgc)
    add_rows(pgc, "ai_test_drafts", *([30] * 25))
    monkeypatch.setenv("AI_RETENTION_BATCH", "10")
    monkeypatch.setenv("AI_RETENTION_MAX_BATCHES", "2")
    assert run_once(ai_app)["hooks"]["test.drafts"]["purged"] == 20
    assert count(pgc, "ai_test_drafts") == 5
    assert [e[0]["purged"] for e in purge_events(pgc, "test.drafts")] == [10, 10]
    assert run_once(ai_app)["hooks"]["test.drafts"]["purged"] == 5


def test_a_failing_hook_does_not_stop_the_others(ai_app, pgc, scratch):
    from services.ai import retention
    insert_policy(pgc)
    add_rows(pgc, "ai_test_audio", 45)

    def broken(session, *, cutoff, limit):
        raise RuntimeError("boom")
    retention.register("test.broken", "drafts", broken)
    report = run_once(ai_app)
    assert report["hooks"]["test.broken"]["error"] == "RuntimeError"
    assert report["hooks"]["test.audio"]["purged"] == 1 and count(pgc, "ai_test_audio") == 0


def test_a_suspended_policy_still_applies_retention(ai_app, pgc, scratch):
    insert_policy(pgc, state="suspended")
    add_rows(pgc, "ai_test_drafts", 30)
    assert run_once(ai_app)["hooks"]["test.drafts"]["purged"] == 1


def _call(pgc, outcome, age_days, **cols):
    extra = ", finished_at" if outcome != "started" else ""
    extra_v = ", now() - make_interval(days => %s)" if outcome != "started" else ""
    params = [outcome, age_days] + ([age_days] if outcome != "started" else [])
    err = ", error_code" if outcome in ("failed", "refused") else ""
    err_v = ", 'provider_timeout'" if outcome in ("failed", "refused") else ""
    return q1(pgc, f"""INSERT INTO ai_provider_calls (purpose, capability, provider, outcome, started_at{extra}{err})
                        VALUES ('test_retention', 'generate_structured', 'openai', %s,
                                now() - make_interval(days => %s){extra_v}{err_v}) RETURNING id::text""", *params)[0]


def test_old_provider_call_rows_are_deleted_unfinished_never(ai_app, pgc, scratch):
    insert_policy(pgc, retention_days={"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 30})
    with pgc.cursor() as cur:        # an old `succeeded` row must go through `started` first (guard)
        cur.execute("SET session_replication_role = replica")
    old_ok = _call(pgc, "succeeded", 40)
    old_refused = _call(pgc, "refused", 31)
    recent = _call(pgc, "succeeded", 5)
    old_started = _call(pgc, "started", 40)
    with pgc.cursor() as cur:
        cur.execute("SET session_replication_role = DEFAULT")
    report = run_once(ai_app)
    assert report["provider_calls"]["days"] == 30 and report["provider_calls"]["purged"] == 2
    left = dict(qall(pgc, "SELECT id::text, outcome FROM ai_provider_calls"))
    assert recent in left and old_ok not in left and old_refused not in left
    # an unfinished row is never deleted: it is first marked interrupted, and its retention clock
    # starts when it was finished
    assert left[old_started] == "interrupted" and report["interrupted"] == 1
    ev = purge_events(pgc, "ai_provider_calls")
    assert ev[-1][0]["purged"] == 2 and ev[-1][0]["table"] == "ai_provider_calls"


def test_interrupted_provider_work_is_marked_never_replayed(ai_app, pgc, scratch, monkeypatch):
    from test_ai_helpers import FakeTransport
    ai_app.config["AI_PROVIDER_TRANSPORT"] = fake = FakeTransport()
    try:
        stale = _call(pgc, "started", 1)                 # a crash left it `started` a day ago
        fresh = q1(pgc, "INSERT INTO ai_provider_calls (purpose, capability, provider, outcome) "
                        "VALUES ('test_retention', 'transcribe', 'openai', 'started') RETURNING id::text")[0]
        report = run_once(ai_app)                        # no policy: interruption is still recorded
        assert report["interrupted"] == 1 and report["policy"] == "missing"
        rows = dict(qall(pgc, "SELECT id::text, outcome || '/' || coalesce(error_code, '') FROM ai_provider_calls"))
        assert rows == {stale: "interrupted/interrupted", fresh: "started/"}
        assert fake.requests == []                       # nothing was sent again
        ev = q1(pgc, "SELECT after FROM audit_events WHERE action = 'ai_retention.interrupted' "
                     "ORDER BY occurred_at DESC LIMIT 1")[0]
        assert ev["interrupted"] == 1
        # the stale threshold is configurable
        monkeypatch.setenv("AI_CALL_STALE_SECONDS", "300")
        run(pgc, "UPDATE ai_provider_calls SET outcome = 'failed', error_code = 'provider_timeout', "
                 "finished_at = now() WHERE id = %s", fresh)
        assert run_once(ai_app)["interrupted"] == 0
    finally:
        ai_app.config.pop("AI_PROVIDER_TRANSPORT", None)


def test_registry_validation_and_discovery():
    from services.ai import retention
    with pytest.raises(ValueError):
        retention.register("x", "emails", lambda s, **k: 0)
    with pytest.raises(ValueError):
        retention.register("", "drafts", lambda s, **k: 0)
    with pytest.raises(ValueError):
        retention.register("x", "drafts", "not callable")
    h1 = retention.register("test.replace", "drafts", lambda s, **k: 0)
    h2 = retention.register("test.replace", "audio", lambda s, **k: 0)     # a re-import replaces
    assert [h for h in retention.hooks() if h.name == "test.replace"] == [h2] and h1 != h2
    retention.unregister("test.replace")
    assert isinstance(retention.discover(), list)        # Q0 ships no hook module; Q1/Q2 add theirs


def test_worker_tick_is_throttled_and_never_raises(ai_app, monkeypatch):
    from services.ai import retention
    calls = []

    def boom(session=None):
        calls.append(1)
        raise RuntimeError("database gone")
    monkeypatch.setattr(retention, "run", boom)
    monkeypatch.setattr(retention, "_next_run", {"at": 0.0})
    with ai_app.app_context():
        assert retention.worker_tick() is None           # failed, swallowed
        assert retention.worker_tick() is None           # throttled: not run again
    assert calls == [1]


def test_the_worker_loop_calls_the_ai_retention_tick(monkeypatch):
    from services.ai import retention
    from worker.main import Worker
    ticks = []
    monkeypatch.setattr(retention, "worker_tick", lambda session=None: ticks.append(1))
    w = Worker.__new__(Worker)
    w._ai_retention()
    assert ticks == [1]
    monkeypatch.setattr(retention, "worker_tick", lambda session=None: (_ for _ in ()).throw(RuntimeError("x")))
    w._ai_retention()                                    # never raises into the loop


def test_missing_migrations_skip_quietly(ai_app, monkeypatch):
    """Code deployed before 0059/0060 are applied: the tick skips instead of failing every hour."""
    from services.ai import retention

    class NoTables:
        def execute(self, *a, **k):
            class R:
                def scalar(self):
                    return False
            return R()

        def rollback(self):
            pass
    with ai_app.app_context():
        report = retention.run(NoTables())
    assert report == {"skipped": True, "reason": "migrations_not_applied", "policy_revision": None,
                      "interrupted": 0, "hooks": {}, "provider_calls": None}
