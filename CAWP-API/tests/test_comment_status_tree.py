"""Contract note statuses (migration 0068, services/comment_status_tree.py): general statuses,
one level of sub-statuses with inherited contract/reachability, soft delete, and the note-time
checks of the legacy note form (real app.py) and the calling wrap-up / manual log."""
from __future__ import annotations

import datetime as dt
import uuid

from test_admin_support import A, FORBIDDEN, admin_db, db, make_case, make_user, q1, run_app  # noqa: F401
from test_calling_helpers import CL, cl, cl_app, cl_client, hdr, key, note_body, pgc, team  # noqa: F401
from test_calling_helpers import q1 as cq1
from test_calling_helpers import run as crun

CONTACT_METHOD = "00000000-0000-4000-8000-000000000701"     # tests/fixtures/seed.sql
SETTINGS = {"settings_comments_status": True, "settings_comments_status_add": True,
            "settings_comments_status_edit": True}


def one(conn, sql, params=None):
    return q1(conn, sql, params)[0]


def new_reach(conn, name="T reach"):
    return one(conn, "INSERT INTO comment_reachability (name) VALUES (%s) RETURNING id::text", (name,))


def new_status(conn, name, *, contract=None, reach=None, parent=None, deleted=False):
    return one(conn, 'INSERT INTO "CommentStatuses" (name, contract_id, "CommentReachability_id", parent_id, deleted_at) '
                     "VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END) RETURNING id::text",
               (name, contract, reach, parent, deleted))


def status_row(conn, sid):
    return q1(conn, 'SELECT contract_id::text, "CommentReachability_id"::text, parent_id::text, deleted_at, '
                    'deleted_by::text, color FROM "CommentStatuses" WHERE id = %s', (sid,))


def calls_as(user, calls):
    steps = [{"op": "mint", "user": user, "as": "T"}]
    steps += [{"op": "call", "token": "T", "method": c[0], "path": c[1], "json": c[2] if len(c) > 2 else None}
              for c in calls]
    return [r for r in run_app(steps) if r["op"] == "call"]


def legacy_note(case, status, user, sub=None, text="T note"):
    body = {"clint_id": case, "user_id": user, "contactMethod_id": CONTACT_METHOD, "comment": text,
            "comment_status_id": status}
    if sub is not None:
        body["comment_sub_status_id"] = sub
    return body


# --------------------------------------------------------------------------- settings API
def test_general_status_sub_status_one_level_and_inheritance(db):
    admin = make_user(db, SETTINGS)
    reach_a, reach_b = new_reach(db, "T reach a"), new_reach(db, "T reach b")
    main = new_status(db, "T main", contract=A.contract_a, reach=reach_a)
    other_main = new_status(db, "T other main", contract=A.contract_b, reach=reach_b)
    general = new_status(db, "T general existing")
    tag = uuid.uuid4().hex[:6]
    res = calls_as(admin, [
        ("POST", "/comment_status", {"name": "T general " + tag}),                                     # 0
        ("POST", "/comment_status", {"name": "T sub " + tag, "parent_id": main, "contract_id": A.contract_b,
                                     "CommentReachability_id": reach_b, "color": "#FF0000"}),          # 1
        ("POST", "/comment_status", {"name": "T bad parent", "parent_id": str(uuid.uuid4())}),         # 2
        ("POST", "/comment_status", {"name": "T bad parent", "parent_id": "nope"}),                    # 3
        ("PUT", f"/comment_status/{main}", {"contract_id": A.contract_b, "CommentReachability_id": reach_b}),  # 4
        ("PUT", f"/comment_status/{main}", {"parent_id": general}),                                    # 5 has children
        ("PUT", f"/comment_status/{main}", {"parent_id": main}),                                       # 6 itself
        ("PUT", f"/comment_status/{general}", {"parent_id": other_main, "contract_id": None}),         # 7 ok
        ("PUT", f"/comment_status/{general}", {"name": "T moved"}),                                    # 8 still a child
        ("PUT", f"/comment_status/{general}", {"parent_id": None, "contract_id": ""}),                 # 9 back to main
        ("GET", f"/comment_status/{main}"),                                                            # 10
        ("GET", "/comment_status"),                                                                    # 11
        ("GET", "/comment_status?page=1&pageSize=1000&tree=1&field=name&sort=asc"),                    # 12
    ])
    assert [r["status"] for r in res] == [200, 200, 201, 201, 200, 201, 201, 200, 200, 200, 200, 200, 200], res
    gen = res[0]["body"]
    assert gen["contract_id"] is None and gen["color"] == "#FFFFFF" and gen["parent_id"] is None
    sub = res[1]["body"]
    # a sub-status takes its parent's contract and reachability, whatever was sent
    assert (sub["parent_id"], sub["contract_id"], sub["CommentReachability_id"], sub["color"]) == \
        (main, A.contract_a, reach_a, "#FF0000")
    assert res[2]["body"]["error"] == "parent_not_found" and res[2]["body"]["status"] is False
    assert res[3]["body"]["error"] == "invalid_parent_id"
    # a main status's new contract/reachability is propagated to its sub-statuses
    assert status_row(db, sub["id"])[:3] == (A.contract_b, reach_b, main)
    assert res[5]["body"]["error"] == "has_sub_statuses" and res[6]["body"]["error"] == "parent_is_self"
    # a sub-status of a sub-status is refused (one level)
    res2 = calls_as(admin, [("POST", "/comment_status", {"name": "T grandchild", "parent_id": sub["id"]})])
    assert res2[0]["body"]["error"] == "parent_is_sub_status"
    assert res[7]["body"]["parent_id"] == other_main and res[7]["body"]["contract_id"] == A.contract_b
    assert res[8]["body"]["parent_id"] == other_main and res[8]["body"]["CommentReachability_id"] == reach_b
    assert res[9]["body"]["parent_id"] is None and res[9]["body"]["contract_id"] is None
    assert res[10]["body"]["parent_id"] is None and res[10]["body"]["deleted_at"] is None
    rows = {r["id"]: r for r in res[11]["body"]["comment_statuses"]}
    assert rows[sub["id"]]["parent"] == {"id": main, "name": "T main"} and rows[main]["parent_id"] is None
    assert "parent" not in rows[main] and "deleted_at" not in rows[main]
    order = [r["id"] for r in res[12]["body"]["comment_statuses"]]
    assert order.index(sub["id"]) == order.index(main) + 1          # children right under their parent


def test_delete_is_soft_cascades_keeps_notes_and_needs_the_key(db):
    editor = make_user(db, SETTINGS)
    deleter = make_user(db, {"settings_comments_status_delete": True, "clints": True})
    collector = make_user(db, {"clints": True})
    reach = new_reach(db)
    main = new_status(db, "T del main", contract=A.contract_a, reach=reach)
    child = new_status(db, "T del child", contract=A.contract_a, reach=reach, parent=main)
    case = make_case(db, 100, owner=collector)
    note = one(db, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
                   "comment_sub_status_id) VALUES (%s, %s, %s, 'old note', %s, %s) RETURNING id::text",
               (case, collector, CONTACT_METHOD, main, child))

    denied = calls_as(editor, [("DELETE", f"/comment_status/{main}")])
    assert (denied[0]["status"], denied[0]["body"]) == (403, FORBIDDEN)
    assert status_row(db, main)[3] is None

    res = calls_as(deleter, [
        ("DELETE", f"/comment_status/{main}"),                                     # 0
        ("DELETE", f"/comment_status/{main}"),                                     # 1 again
        ("DELETE", f"/comment_status/{uuid.uuid4()}"),                             # 2 unknown
        ("GET", "/comment_status"),                                                # 3
        ("GET", "/comment_status?include_deleted=1"),                              # 4
        ("GET", f"/comments/clint/{case}"),                                        # 5
        ("POST", "/comments", legacy_note(case, main, collector)),                 # 6 deleted status
    ])
    assert res[0]["status"] == 200 and res[0]["body"]["status"] is True, res[0]
    assert res[0]["body"]["sub_statuses_deleted"] == [child]
    assert res[1]["body"]["error"] == "already_deleted" and res[2]["body"]["error"] == "not_found"
    for sid in (main, child):
        row = status_row(db, sid)
        assert row[3] is not None and row[4] == deleter
    assert one(db, "SELECT count(*) FROM audit_events WHERE action = 'comment_status.delete' AND entity_id IN (%s, %s)",
               (main, child)) == 2
    live = {r["id"] for r in res[3]["body"]["comment_statuses"]}
    assert main not in live and child not in live
    every = {r["id"]: r for r in res[4]["body"]["comment_statuses"]}
    assert every[main]["deleted_at"] and every[child]["deleted_at"]
    # the old note is untouched and still shows both names
    assert q1(db, 'SELECT comment_status_id::text, comment_sub_status_id::text FROM "Comments" WHERE id = %s',
              (note,)) == (main, child)
    shown = {n["id"]: n for n in res[5]["body"]["comments"]}[note]
    assert shown["status"]["name"] == "T del main" and shown["sub_status"]["name"] == "T del child"
    # ... but a new note on the deleted status is refused
    assert res[6]["status"] == 201 and res[6]["body"]["error"] == "status_deleted"
    assert one(db, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', (case,)) == 1
    # and the deleted status cannot be edited any more
    res3 = calls_as(editor, [("PUT", f"/comment_status/{main}", {"name": "T revived"})])
    assert res3[0]["body"]["error"] == "status_deleted"


def test_legacy_note_form_with_a_sub_status(db):
    collector = make_user(db, {"clints": True})
    reach = new_reach(db)
    main = new_status(db, "T note main", reach=reach)                    # a general status
    child = new_status(db, "T note child", reach=reach, parent=main)
    other_main = new_status(db, "T note other", reach=reach)
    other_child = new_status(db, "T note other child", reach=reach, parent=other_main)
    gone_child = new_status(db, "T note gone child", reach=reach, parent=main, deleted=True)
    case = make_case(db, 100, owner=collector)
    res = calls_as(collector, [
        ("POST", "/comments", legacy_note(case, main, collector, sub=child, text="with sub")),     # 0
        ("POST", "/comments", legacy_note(case, main, collector, text="plain")),                   # 1
        ("POST", "/comments", legacy_note(case, child, collector)),                                # 2 sub as main
        ("POST", "/comments", legacy_note(case, main, collector, sub=other_child)),                # 3 wrong parent
        ("POST", "/comments", legacy_note(case, main, collector, sub=gone_child)),                 # 4 deleted sub
        ("POST", "/comments", legacy_note(case, main, collector, sub=str(uuid.uuid4()))),          # 5 unknown sub
        ("GET", f"/comments/clint/{case}"),                                                        # 6
    ])
    assert res[0]["status"] == 200 and res[0]["body"]["comment_sub_status_id"] == child, res[0]
    assert res[1]["status"] == 200 and "comment_sub_status_id" not in res[1]["body"]
    assert set(res[0]["body"]) - set(res[1]["body"]) == {"comment_sub_status_id"}
    assert [r["body"]["error"] for r in res[2:6]] == ["is_sub_status", "not_a_sub_status", "status_deleted",
                                                     "unknown_sub_status"]
    assert all(r["status"] == 201 and r["body"]["status"] is False for r in res[2:6])
    with_sub = res[0]["body"]["id"]
    assert q1(db, 'SELECT comment_sub_status_id::text FROM "Comments" WHERE id = %s', (with_sub,))[0] == child
    # one INSERT: the note-history UPDATE trigger never fired for it
    assert one(db, "SELECT count(*) FROM audit_events WHERE entity_type = 'comment' AND entity_id = %s",
               (with_sub,)) == 0
    assert one(db, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', (case,)) == 2
    timeline = {n["id"]: n for n in res[6]["body"]["comments"]}
    assert timeline[with_sub]["sub_status"]["name"] == "T note child"
    assert "sub_status" not in timeline[res[1]["body"]["id"]]


# --------------------------------------------------------------------------- calling wrap-up
def now_iso():
    return (dt.datetime.now(dt.timezone(dt.timedelta(hours=3))) - dt.timedelta(minutes=5)).isoformat(timespec="seconds")


def manual(client, hdr, user, case, note):
    r = client.post("/v2/calls/manual", headers=hdr(user, key=key()),
                    json={"case_id": case, "started_at": now_iso(), "outcome": "no_answer", "note": note})
    return r.status_code, r.get_json()


def test_manual_log_with_a_sub_status(cl_client, hdr, team, pgc):
    def status(name, parent=None, deleted=False):
        sid = str(uuid.uuid4())
        crun(pgc, 'INSERT INTO "CommentStatuses" (id, name, "CommentReachability_id", parent_id, deleted_at) '
                  "VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)", sid, name, CL.reach, parent, deleted)
        return sid
    main = status("T wrap main")
    child = status("T wrap child", main)
    other = status("T wrap other child", status("T wrap other"))
    gone = status("T wrap gone", deleted=True)
    c = (cl_client, hdr, team["c1"], team["case1"])

    st, body = manual(*c, note_body(comment_status_id=main, comment_sub_status_id=child))
    assert st == 201, body
    note = body["data"]["wrapup"]["note_id"]
    assert cq1(pgc, 'SELECT comment_status_id::text, comment_sub_status_id::text FROM "Comments" WHERE id = %s',
               note) == (main, child)
    for over, field, problem in (
            ({"comment_status_id": main, "comment_sub_status_id": other}, "note.comment_sub_status_id",
             "not_a_sub_status"),
            ({"comment_status_id": child}, "note.comment_status_id", "is_sub_status"),
            ({"comment_status_id": gone}, "note.comment_status_id", "status_deleted"),
            ({"comment_status_id": None, "comment_sub_status_id": child}, "note.comment_sub_status_id",
             "needs_note_status")):
        st, body = manual(*c, note_body(**over))
        assert st == 422 and body["error"][field] == problem, (over, body)


# --------------------------------------------------------------------------- cache
def test_writes_invalidate_the_status_cache(cl_app, pgc):
    from core import lookups
    from services import clients_query
    from services import comment_status_tree as tree
    from models import CommentStatuses
    from extensions import db as sdb

    def primed():
        clients_query.comment_statuses_map()
        assert "b1_comment_statuses" in lookups._store

    with cl_app.app_context():
        primed()
        created = tree.create({"name": "T cache " + uuid.uuid4().hex[:6]})
        assert "b1_comment_statuses" not in lookups._store
        assert created["id"] is not None and str(created["id"]) in clients_query.comment_statuses_map()
        primed()
        tree.update(sdb.session.get(CommentStatuses, created["id"]), {"name": "T cache renamed"})
        assert "b1_comment_statuses" not in lookups._store
        assert clients_query.comment_statuses_map()[str(created["id"])]["name"] == "T cache renamed"
        primed()
        tree.soft_delete(created["id"])
        assert "b1_comment_statuses" not in lookups._store
