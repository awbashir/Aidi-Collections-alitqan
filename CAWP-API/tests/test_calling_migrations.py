"""Phase 5 P0: migrations 0048-0051 - tables exist and the database guards hold on their own
(one owner per extension, one extension per user, lifecycle, set-once evidence, append-only
history, reservations that no restart or timer can release, one wrap-up per attempt)."""
from __future__ import annotations

import uuid

import psycopg2
import pytest

from test_calling_helpers import (cl, insert_attempt, make_case, new_contract, new_user, pgc, q1, reserve,  # noqa: F401
                                  run)

TABLES = ("user_extensions", "calling_preferences", "calling_window_policy", "calling_window_policy_history",
          "call_attempts", "call_reservations", "call_dispatch_tokens", "pbx_sessions", "call_events", "call_legs",
          "call_agi_decisions", "call_wrapups", "calling_blocks")


@pytest.fixture
def world(pgc):
    _cid, pid = new_contract(pgc, "mig")
    u1, u2 = new_user(pgc), new_user(pgc)
    return {"pid": pid, "u1": u1, "u2": u2, "case1": make_case(pgc, u1, pid), "case2": make_case(pgc, u2, pid)}


def err(pgc, sql, *params):
    with pytest.raises(psycopg2.Error) as e:
        run(pgc, sql, *params)
    return e.value.pgcode, str(e.value)


def test_migrations_recorded_and_tables_exist(pgc):
    versions = {r[0] for r in [q1(pgc, "SELECT version FROM schema_migrations WHERE version = %s", v)
                                for v in ("0048", "0049", "0050", "0051")] if r}
    assert versions == {"0048", "0049", "0050", "0051"}
    for t in TABLES:
        assert q1(pgc, "SELECT to_regclass(%s)", t)[0] is not None, t


def test_users_extension_column_untouched(pgc):
    # legacy column still there and unchanged in type (the mirror)
    assert q1(pgc, "SELECT data_type FROM information_schema.columns WHERE table_name = 'users' "
                   "AND column_name = 'extension'")[0] == "text"


def test_extension_ownership_constraints(pgc, world):
    ext = str(6000 + uuid.uuid4().int % 900)
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'r')", ext, world["u1"])
    code, _ = err(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'r')",
                  ext, world["u2"])
    assert code == "23505"                                    # one active owner per extension
    code, _ = err(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'r')",
                  str(int(ext) + 1), world["u1"])
    assert code == "23505"                                    # one active extension per user
    code, _ = err(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES ('7a', %s, 'r')",
                  world["u2"])
    assert code == "23514"
    # release once, with a reason; then immutable; never deleted
    code, _ = err(pgc, "UPDATE user_extensions SET released_at = now() WHERE extension = %s", ext)
    assert code == "23514"
    run(pgc, "UPDATE user_extensions SET released_at = now(), release_reason = 'left' WHERE extension = %s", ext)
    code, _ = err(pgc, "UPDATE user_extensions SET release_reason = 'other' WHERE extension = %s", ext)
    assert code == "CL001"
    code, _ = err(pgc, "DELETE FROM user_extensions WHERE extension = %s", ext)
    assert code == "CL001"
    # history allows the next owner
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'r')", ext, world["u2"])


def test_attempt_lifecycle_guard(pgc, world):
    a = insert_attempt(pgc, world["case1"], world["u1"], state="prepared")
    code, msg = err(pgc, "UPDATE call_attempts SET state = 'bridged' WHERE id = %s", a)
    assert code == "CL003" and "prepared -> bridged" in msg
    run(pgc, "UPDATE call_attempts SET state = 'authorized', authorized_at = now() WHERE id = %s", a)
    run(pgc, "UPDATE call_attempts SET state = 'dispatched', dispatched_at = now() WHERE id = %s", a)
    code, _ = err(pgc, "UPDATE call_attempts SET state = 'authorized' WHERE id = %s", a)        # never backwards
    assert code == "CL003"
    code, _ = err(pgc, "UPDATE call_attempts SET dispatched_at = now() - interval '1 hour' WHERE id = %s", a)
    assert code == "CL002"                                                                     # set once
    code, _ = err(pgc, "UPDATE call_attempts SET case_id = %s WHERE id = %s", world["case2"], a)
    assert code == "CL002"                                                                     # identity immutable
    code, _ = err(pgc, "UPDATE call_attempts SET state = 'uncertain' WHERE id = %s", a)
    assert code == "23514"                                                                     # needs a reason
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'ami_connection_lost' WHERE id = %s", a)
    run(pgc, "UPDATE call_attempts SET state = 'ended', ended_at = now() WHERE id = %s", a)
    code, _ = err(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'x' WHERE id = %s", a)
    assert code == "CL003"                                                                     # terminal
    code, _ = err(pgc, "DELETE FROM call_attempts WHERE id = %s", a)
    assert code == "CL001"
    r0 = q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", a)[0]
    assert r0 >= 4


def test_manual_attempt_shape(pgc, world):
    code, _ = err(pgc, "INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by) "
                       "VALUES ('manual', 'prepared', %s, %s, %s)", world["case1"], world["u1"], world["u1"])
    assert code == "23514"
    code, _ = err(pgc, "INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, "
                       "duration_seconds) VALUES ('manual', 'ended', %s, %s, %s, 10)",
                  world["case1"], world["u1"], world["u1"])
    assert code == "23514"                                          # a duration needs its source


def test_reservations_one_active_and_never_auto_released(pgc, world):
    ext = str(6900 + uuid.uuid4().int % 90)
    a1 = insert_attempt(pgc, world["case1"], world["u1"], state="dispatched", extension=ext)
    reserve(pgc, a1)
    a2 = insert_attempt(pgc, world["case2"], world["u1"], state="prepared")
    code, msg = err(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id) VALUES (%s, %s, %s)",
                    a2, world["u1"], world["case2"])
    assert code == "23505" and "collector" in msg
    a3 = insert_attempt(pgc, world["case1"], world["u2"], state="prepared")
    code, msg = err(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id) VALUES (%s, %s, %s)",
                    a3, world["u2"], world["case1"])
    assert code == "23505" and "case" in msg
    # no timeout / restart release source exists
    code, _ = err(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'timeout' "
                       "WHERE attempt_id = %s", a1)
    assert code == "23514"
    # wrap-up release only after a saved wrap-up; cancel only for a never-dispatched attempt
    code, _ = err(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'wrapup' "
                       "WHERE attempt_id = %s", a1)
    assert code == "CL004"
    code, _ = err(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'cancel_unsent' "
                       "WHERE attempt_id = %s", a1)
    assert code == "CL004"
    code, _ = err(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled' "
                       "WHERE attempt_id = %s", a1)
    assert code == "23514"                                     # reconciliation names a person and a reason
    code, _ = err(pgc, "DELETE FROM call_reservations WHERE attempt_id = %s", a1)
    assert code == "CL001"
    assert q1(pgc, "SELECT released_at FROM call_reservations WHERE attempt_id = %s", a1)[0] is None
    # a cancelled, never-dispatched attempt can release
    run(pgc, "UPDATE call_attempts SET state = 'cancelled' WHERE id = %s", a2)


def test_cancel_unsent_release(pgc, world):
    a = insert_attempt(pgc, world["case2"], world["u2"], state="prepared")
    reserve(pgc, a)
    run(pgc, "UPDATE call_attempts SET state = 'cancelled' WHERE id = %s", a)
    run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'cancel_unsent' "
             "WHERE attempt_id = %s", a)
    code, _ = err(pgc, "UPDATE call_reservations SET release_reason = 'x' WHERE attempt_id = %s", a)
    assert code == "CL001"


def test_dispatch_tokens_one_per_attempt_consumed_once(pgc, world):
    a = insert_attempt(pgc, world["case1"], world["u1"], state="authorized")
    ins = ("INSERT INTO call_dispatch_tokens (attempt_id, token_sha256, channel_id, context, destination, extension, "
           "max_call_seconds, expires_at) VALUES (%s, %s, %s, 'collections-human-outbound', '+966500000001', '7201', "
           "600, now() + interval '30 seconds')")
    run(pgc, ins, a, "a" * 64, f"cawp-{a}")
    code, _ = err(pgc, ins, a, "b" * 64, f"cawp-{a}")
    assert code == "23505"
    code, _ = err(pgc, ins.replace("30 seconds", "1 hour"), insert_attempt(pgc, world["case2"], world["u2"]),
                  "c" * 64, f"cawp-{a}")
    assert code == "23514"                                     # short-lived only
    run(pgc, "UPDATE call_dispatch_tokens SET consumed_at = now() WHERE attempt_id = %s", a)
    code, _ = err(pgc, "UPDATE call_dispatch_tokens SET consumed_at = now() WHERE attempt_id = %s", a)
    assert code == "CL001"


def test_evidence_is_append_only_and_legs_set_once(pgc, world):
    run(pgc, "INSERT INTO call_events (source, event, dedupe_key) VALUES ('ami', 'FullyBooted', %s)", "t:" + str(uuid.uuid4()))
    code, _ = err(pgc, "UPDATE call_events SET event = 'Shutdown' WHERE event = 'FullyBooted'")
    assert code == "CL001"
    code, _ = err(pgc, "DELETE FROM call_events")
    assert code == "CL001"
    code, _ = err(pgc, "TRUNCATE call_events")
    assert code == "CL001"
    uid = "cawp-" + str(uuid.uuid4())
    run(pgc, "INSERT INTO call_legs (pbx_epoch, uniqueid, role, answered_pbx_ts) VALUES ('e1', %s, 'agent', 1.5)", uid)
    code, _ = err(pgc, "UPDATE call_legs SET answered_pbx_ts = 2.5 WHERE uniqueid = %s", uid)
    assert code == "CL002"
    code, _ = err(pgc, "UPDATE call_legs SET role = 'customer' WHERE uniqueid = %s", uid)
    assert code == "CL002"
    run(pgc, "UPDATE call_legs SET hungup_pbx_ts = 3.0 WHERE uniqueid = %s", uid)


def test_wrapups_and_blocks(pgc, world):
    a = insert_attempt(pgc, world["case1"], world["u1"], state="ended")
    note = str(uuid.uuid4())
    ins = ("INSERT INTO call_wrapups (attempt_id, case_id, outcome, note_id, reviewed_by, promise_id) "
           "VALUES (%s, %s, %s, %s, %s, %s)")
    code, _ = err(pgc, ins, a, world["case1"], "busy", note, world["u1"], str(uuid.uuid4()))
    assert code == "23514"                     # a promise only with connected / promise_discussed (0079)
    code, _ = err(pgc, ins, a, world["case1"], "do_not_call", note, world["u1"], None)
    assert code == "23514"                                     # DNC must link its block
    run(pgc, ins, a, world["case1"], "busy", note, world["u1"], None)
    code, _ = err(pgc, ins, a, world["case1"], "busy", note, world["u1"], None)
    assert code == "23505"                                     # one wrap-up per attempt
    code, _ = err(pgc, "UPDATE call_wrapups SET outcome = 'connected' WHERE attempt_id = %s", a)
    assert code == "CL001"
    run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'opt-out', 'manual', %s)",
        world["case2"], world["u2"])
    code, _ = err(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) "
                       "VALUES (%s, 'again', 'manual', %s)", world["case2"], world["u2"])
    assert code == "23505"                                     # one active DNC per case
    code, _ = err(pgc, "UPDATE calling_blocks SET released_at = now() WHERE case_id = %s", world["case2"])
    assert code == "23514"                                     # release needs who and why
