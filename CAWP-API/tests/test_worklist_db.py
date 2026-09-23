"""Stream B, database level: schema, the one-open invariant, the note trigger, the owner
trigger, the backfill (0011) and coexistence with the legacy triggers on "Comments"."""
from __future__ import annotations

import threading
import time

import psycopg2
import pytest

from test_worklist_helpers import (W, add_note, connect, events, follow_ups, make_case, new_contract, new_user,
                                   open_follow_up, pgw, q1, qall, run, utc, wl)  # noqa: F401 (fixtures)


@pytest.fixture
def team(pgw):
    cid, pid = new_contract(pgw)
    return {"contract": cid, "portfolio": pid, "c1": new_user(pgw), "c2": new_user(pgw)}


def test_schema_objects(pgw):
    versions = {r[0] for r in qall(pgw, "SELECT version FROM schema_migrations")}
    assert {"0010", "0011", "0012"} <= versions
    idx = dict(qall(pgw, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('follow_ups', 'clints')"))
    assert "UNIQUE" in idx["ux_follow_ups_one_open"] and "WHERE (status = 'open'::text)" in idx["ux_follow_ups_one_open"]
    assert "last_comment_at NULLS FIRST, id" in idx["ix_clints_worklist_neglect"]
    assert "user_id, last_comment_at NULLS FIRST, id" in idx["ix_clints_worklist_user_neglect"]
    valid = qall(pgw, "SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                      "WHERE c.relname LIKE 'ix_clints_worklist_%'")
    assert sorted(valid) == [("ix_clints_worklist_neglect", True), ("ix_clints_worklist_user_neglect", True)]
    trg = {r[0]: r[1] for r in qall(pgw, "SELECT tgname, tgenabled FROM pg_trigger WHERE NOT tgisinternal AND "
                                         "tgrelid IN ('\"Comments\"'::regclass, 'clints'::regclass, "
                                         "'follow_ups'::regclass, 'follow_up_events'::regclass)")}
    for name in ("comments_follow_up_trg", "comments_last_comment_trg", "clints_follow_up_owner_trg",
                 "follow_ups_bump_revision", "follow_up_events_no_update"):
        assert trg.get(name) == "O", name


def test_one_open_follow_up_per_case(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    ins = ("INSERT INTO follow_ups (case_id, due_at, original_due_at, source, status, closed_at) "
           "VALUES (%s, now(), now(), 'manual', %s, %s)")
    run(pgw, ins, case, "open", None)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        run(pgw, ins, case, "open", None)
    run(pgw, ins, case, "cancelled", utc(2026, 1, 1))          # closed ones are unlimited
    run(pgw, ins, case, "superseded", utc(2026, 1, 1))
    assert [r["status"] for r in follow_ups(pgw, case)].count("open") == 1


def test_check_constraints(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    base = "INSERT INTO follow_ups (case_id, due_at, original_due_at, source, status"
    with pytest.raises(psycopg2.errors.CheckViolation):        # completed without outcome / next / reason
        run(pgw, base + ", closed_at, completed_at, completed_by) VALUES (%s, now(), now(), 'manual', "
                        "'completed', now(), now(), %s)", case, team["c1"])
    with pytest.raises(psycopg2.errors.CheckViolation):        # note source needs the note
        run(pgw, base + ") VALUES (%s, now(), now(), 'note', 'open')", case)
    with pytest.raises(psycopg2.errors.CheckViolation):        # blank no-further-action reason
        run(pgw, base + ", closed_at, completed_at, completed_by, outcome_comment_id, no_further_action_reason) "
                        "VALUES (%s, now(), now(), 'manual', 'completed', now(), now(), %s, gen_random_uuid(), '  ')",
            case, team["c1"])


def test_callback_note_opens_follow_up(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    note = add_note(pgw, case, team["c2"], utc(2026, 9, 30, 6))   # written by someone else than the owner
    fu = open_follow_up(pgw, case)
    assert fu is not None
    assert (fu["source"], str(fu["source_comment_id"]), fu["due_at"], fu["original_due_at"]) == \
        ("note", note, utc(2026, 9, 30, 6), utc(2026, 9, 30, 6))
    assert str(fu["owner_user_id"]) == team["c1"]                 # owner = case owner
    assert str(fu["created_by"]) == team["c2"]                    # creator = note author
    ev = events(pgw, case)
    assert [(e["event"], e["source"], str(e["comment_id"])) for e in ev] == [("created", "trigger", note)]
    # the legacy trigger still ran: last_comment_* point at the note
    assert q1(pgw, "SELECT last_comment_id::text FROM clints WHERE id = %s", case)[0] == note


def test_newer_callback_supersedes(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    n1 = add_note(pgw, case, team["c1"], utc(2026, 9, 1, 6))
    first = open_follow_up(pgw, case)
    n2 = add_note(pgw, case, team["c1"], utc(2026, 10, 5, 6))
    rows = follow_ups(pgw, case)
    assert [r["status"] for r in rows] == ["superseded", "open"]
    old, new = rows
    assert old["id"] == first["id"] and old["superseded_by"] == new["id"] and old["closed_at"] is not None
    assert old["due_at"] == utc(2026, 9, 1, 6)                    # the missed callback keeps its due time
    assert str(new["source_comment_id"]) == n2
    ev = [(e["event"], str(e["follow_up_id"])) for e in events(pgw, case)]
    assert ev == [("created", str(first["id"])), ("superseded", str(first["id"])), ("created", str(new["id"]))]
    sup = [e for e in events(pgw, case) if e["event"] == "superseded"][0]
    assert str(sup["comment_id"]) == n2 and sup["due_at_before"] == utc(2026, 9, 1, 6) and sup["was_late"] is True
    assert n1 != n2


def test_note_without_callback_changes_nothing(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], utc(2026, 9, 30, 6))
    before = follow_ups(pgw, case)
    add_note(pgw, case, team["c1"], None, text="called, no answer")
    assert follow_ups(pgw, case) == before
    assert len(events(pgw, case)) == 1


def test_deleted_callback_note_is_ignored(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], utc(2026, 9, 30, 6), deleted=True)
    assert follow_ups(pgw, case) == []
    # retiring / hard-deleting the source note later does not touch the follow-up
    note = add_note(pgw, case, team["c1"], utc(2026, 10, 1, 6))
    run(pgw, 'UPDATE "Comments" SET is_deleted = true WHERE id = %s', note)
    run(pgw, 'DELETE FROM "Comments" WHERE id = %s', note)
    fu = open_follow_up(pgw, case)
    assert fu is not None and str(fu["source_comment_id"]) == note


def test_skip_setting_is_transaction_local(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    pgw.autocommit = False
    try:
        run(pgw, "SELECT set_config('cawp.skip_note_follow_up', 'on', true)")
        add_note(pgw, case, team["c1"], utc(2026, 9, 30, 6))
        pgw.commit()
    finally:
        pgw.autocommit = True
    assert follow_ups(pgw, case) == []
    add_note(pgw, case, team["c1"], utc(2026, 9, 30, 6))          # next transaction: trigger active again
    assert open_follow_up(pgw, case) is not None


def test_owner_follows_case_reassignment(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], utc(2026, 9, 1, 6))
    add_note(pgw, case, team["c1"], utc(2026, 9, 2, 6))           # first one is now superseded
    rev = open_follow_up(pgw, case)["revision"]
    run(pgw, "UPDATE clints SET amount = amount + 1 WHERE id = %s", case)       # not an owner change
    assert open_follow_up(pgw, case)["revision"] == rev
    run(pgw, "UPDATE clints SET user_id = %s WHERE id = %s", team["c2"], case)
    rows = follow_ups(pgw, case)
    assert str(rows[0]["owner_user_id"]) == team["c1"]            # closed row keeps its historical owner
    assert str(rows[1]["owner_user_id"]) == team["c2"] and rows[1]["revision"] == rev + 1
    ev = [e for e in events(pgw, case) if e["event"] == "owner_changed"]
    assert len(ev) == 1 and ev[0]["details"] == {"before": team["c1"], "after": team["c2"]}
    run(pgw, "UPDATE clints SET user_id = NULL WHERE id = %s", case)             # unassign
    assert open_follow_up(pgw, case)["owner_user_id"] is None


def test_legacy_case_delete_cascades(pgw, team):
    """legacy DELETE /clints hard-deletes the case; follow-ups and events go with it."""
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], utc(2026, 9, 1, 6))
    add_note(pgw, case, team["c1"], utc(2026, 9, 2, 6))
    assert run(pgw, "DELETE FROM clints WHERE id = %s", case) == 1
    assert follow_ups(pgw, case) == [] and events(pgw, case) == []


def test_events_are_append_only(pgw, team):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], utc(2026, 9, 1, 6))
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgw, "UPDATE follow_up_events SET reason = 'x' WHERE case_id = %s", case)


def test_concurrent_callback_notes_serialise(pgw, team):
    """Two sessions save a callback note on the same case at once: both notes are kept, the
    later commit supersedes the earlier one, and there is still exactly one open follow-up
    (without the case-row lock the second insert failed on the unique index)."""
    case = make_case(pgw, team["c1"], team["portfolio"])
    a, b = connect(), connect()
    a.autocommit = b.autocommit = False
    try:
        na = add_note(a, case, team["c1"], utc(2026, 9, 10, 6))      # holds the case row lock
        result = {}

        def second():
            try:
                result["note"] = add_note(b, case, team["c2"], utc(2026, 9, 11, 6))
                b.commit()
            except Exception as e:  # noqa: BLE001
                result["error"] = e
                b.rollback()

        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.5)
        assert t.is_alive(), "the second note should wait for the first transaction"
        a.commit()
        t.join(10)
        assert "error" not in result, result.get("error")
    finally:
        a.close()
        b.close()
    rows = follow_ups(pgw, case)
    assert [r["status"] for r in rows] == ["superseded", "open"]
    assert str(rows[0]["source_comment_id"]) == na and str(rows[1]["source_comment_id"]) == result["note"]


def test_backfill_rule(pgw, team):
    """0011: latest callback note (create_at DESC) of each live case, skipping archived
    cases, deleted notes and cases that already have a follow-up; idempotent."""
    c, p = team["c1"], team["portfolio"]
    live = make_case(pgw, c, p)
    archived = make_case(pgw, c, p, archived=True)
    deleted_only = make_case(pgw, c, p)
    plain = make_case(pgw, c, p)
    has_fu = make_case(pgw, c, p)
    unassigned = make_case(pgw, None, p)
    add_note(pgw, has_fu, c, utc(2026, 9, 3, 6))                   # trigger-made follow-up exists
    pgw.autocommit = False                                          # legacy history, recorded before 0010
    try:
        run(pgw, "SELECT set_config('cawp.skip_note_follow_up', 'on', true)")
        add_note(pgw, live, c, utc(2026, 11, 1, 6), create_at=utc(2026, 8, 1, 9))
        latest = add_note(pgw, live, c, utc(2026, 8, 20, 6), create_at=utc(2026, 8, 10, 9))
        add_note(pgw, live, c, None, create_at=utc(2026, 8, 15, 9))       # later plain note: no effect
        add_note(pgw, archived, c, utc(2026, 9, 1, 6))
        add_note(pgw, deleted_only, c, utc(2026, 9, 1, 6), deleted=True)
        add_note(pgw, plain, c, None)
        add_note(pgw, has_fu, c, utc(2026, 12, 1, 6), create_at=utc(2026, 9, 20, 9))
        un_note = add_note(pgw, unassigned, c, utc(2026, 9, 5, 6))
        pgw.commit()
    finally:
        pgw.autocommit = True
    before_has_fu = follow_ups(pgw, has_fu)

    n = q1(pgw, "SELECT worklist_backfill_follow_ups()")[0]
    assert n >= 2
    fu = open_follow_up(pgw, live)
    assert (fu["source"], str(fu["source_comment_id"]), fu["due_at"]) == ("imported", latest, utc(2026, 8, 20, 6))
    assert fu["created_by"] is None and str(fu["owner_user_id"]) == c
    ev = events(pgw, live)
    assert [(e["event"], e["source"], e["was_late"]) for e in ev] == [("imported", "migration", True)]
    assert follow_ups(pgw, archived) == [] and follow_ups(pgw, deleted_only) == [] and follow_ups(pgw, plain) == []
    assert follow_ups(pgw, has_fu) == before_has_fu
    un = open_follow_up(pgw, unassigned)
    assert str(un["source_comment_id"]) == un_note and un["owner_user_id"] is None
    assert q1(pgw, "SELECT worklist_backfill_follow_ups()")[0] == 0      # idempotent


def test_bulk_callback_notes_same_case_in_one_transaction(pgw, team):
    """/commentsbyexcel inserts one note per row in one transaction; a repeated account
    number means several callback notes on one case: the last one wins, one open."""
    case = make_case(pgw, team["c1"], team["portfolio"])
    pgw.autocommit = False
    try:
        for i in range(5):
            add_note(pgw, case, team["c1"], utc(2026, 10, 1 + i, 6))
        pgw.commit()
    finally:
        pgw.autocommit = True
    rows = follow_ups(pgw, case)
    assert [r["status"] for r in rows].count("open") == 1 and len(rows) == 5
    assert open_follow_up(pgw, case)["due_at"] == utc(2026, 10, 5, 6)
