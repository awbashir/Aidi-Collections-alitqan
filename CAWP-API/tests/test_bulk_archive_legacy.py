"""Stream B vs legacy: the archive / restore kinds write what the legacy code writes, and the
legacy archive / restore / lock routes are permission-checked with the keys their screens use
(403 without the key, unchanged behaviour with it). The route tests run the REAL app.py in a
subprocess (tests/test_money_support.run_legacy)."""
import uuid

from test_bulk_framework_support import *  # noqa: F401,F403
from test_bulk_archive_support import *  # noqa: F401,F403
from test_money_support import run_legacy

FORBIDDEN = {"message": "You are not authorized to access this resource"}
DONE_EXCEL = {"message": "تم تحديد المستخدمين بنجاح", "status": True}


# --------------------------------------------------------------------------- same writes as legacy
def test_archive_kind_writes_what_the_legacy_bulk_archive_writes(ac, db):
    legacy = [new_case(db)[0] for _ in range(2)]
    v2 = [new_case(db)[0] for _ in range(2)]
    before = {c: row_json(db, c) for c in legacy + v2}
    with ac.app.app_context():
        from extensions import db as _db
        from services.bulk_actions import run_archive_by_ids
        body = run_archive_by_ids(None, A.arch, legacy, A.reason1, A.arch)
        _db.session.remove()
    assert body == {"message": "  تم نقل المحقظة للأرشيف بنجاح ", "status": True}
    job = ac.create(A.arch, "archive", {"source": "selection", "case_ids": v2,
                                        "params": {"archive_reason_id": A.reason1}})
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.arch)
    assert run(ac.app, job["id"]) == "succeeded"
    for lg, nw in zip(legacy, v2):
        assert changed_columns(db, lg, before[lg]) == changed_columns(db, nw, before[nw]) == \
            ["clintArchive_id", "is_archive", "revision"]                 # owner, lock, update_at untouched
        [(lg_id, *lg_rest)] = history(db, lg)
        [(nw_id, *nw_rest)] = history(db, nw)
        assert lg_rest == nw_rest == [A.reason1, A.arch, False]
        assert arch_state(db, lg)[:2] == (True, lg_id) and arch_state(db, nw)[:2] == (True, nw_id)
        lg_row = q1(db, 'SELECT create_at = update_at FROM "clintArchive" WHERE id = %s', (lg_id,))
        nw_row = q1(db, 'SELECT create_at = update_at FROM "clintArchive" WHERE id = %s', (nw_id,))
        assert lg_row == nw_row == (True,)
    # on top of legacy: the audit row
    assert audit_of(db, legacy[0], "case.archive") == [] and len(audit_of(db, v2[0], "case.archive")) == 1


def test_restore_kind_writes_what_the_legacy_unarchive_writes(ac, db):
    legacy = [new_case(db, archived=True)[0] for _ in range(2)]
    v2 = [new_case(db, archived=True)[0] for _ in range(2)]
    before = {c: row_json(db, c) for c in legacy + v2}
    hist = {c: history(db, c) for c in legacy + v2}
    with ac.app.app_context():
        from extensions import db as _db
        from services.bulk_actions import _unarchive
        _unarchive(legacy)
        _db.session.remove()
    job = ac.create(A.col, "restore", {"source": "selection", "case_ids": v2, "params": {"reason": "r"}})
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.col)
    assert run(ac.app, job["id"]) == "succeeded"
    for lg, nw in zip(legacy, v2):
        assert changed_columns(db, lg, before[lg]) == changed_columns(db, nw, before[nw]) == \
            ["clintArchive_id", "is_archive", "revision"]
        assert arch_state(db, lg)[:2] == arch_state(db, nw)[:2] == (False, None)
        assert history(db, lg) == hist[lg] and history(db, nw) == hist[nw]        # both keep the history rows


# --------------------------------------------------------------------------- legacy route gates
def _calls(case, locked_case, archived_case, pid, acc, user):
    return [
        {"method": "PUT", "path": f"/clint/archive/{case}",
         "json": {"archive_reason_id": A.reason1, "user_id": user, "clint_id": case, "id": case}},
        {"method": "PUT", "path": "/clint/archive/ids",
         "json": {"archive_reason_id": A.reason1, "user_id": user, "clint_ids": [case]}},
        {"method": "PUT", "path": "/clint/archivebyexcel",
         "json": {"account_number": "acc", "data": [{"acc": acc}], "archive_reason_id": A.reason1, "user_id": user}},
        {"method": "PUT", "path": f"/clint/archive/portofolio/{pid}", "json": {"archive_reason_id": A.reason1}},
        {"method": "PUT", "path": f"/clint/unarchive/{archived_case}"},
        {"method": "PUT", "path": "/clint/unarchivebyexcel", "json": {"file_name": None, "index": 0}},
        {"method": "PUT", "path": f"/clint/lock/{case}", "json": {"reason": "gate"}},
        {"method": "PUT", "path": f"/clint/unlock/{locked_case}", "json": {"reason": "gate"}},
        {"method": "PUT", "path": "/clints/bulk-lock", "json": {"client_ids": [case], "reason": "gate"}},
        {"method": "PUT", "path": "/clints/bulk-unlock", "json": {"client_ids": [locked_case], "reason": "gate"}},
    ]


ROUTES = ["archive/<id>", "archive/ids", "archivebyexcel", "archive/portofolio", "unarchive/<id>",
          "unarchivebyexcel", "lock/<id>", "unlock/<id>", "bulk-lock", "bulk-unlock"]


def _fixture(db):
    pid = new_portfolio(db)
    case, acc = new_case(db, portfolio=pid)
    locked = new_case(db, portfolio=pid, locked=True)[0]
    archived = new_case(db, portfolio=pid, archived=True)[0]
    return pid, case, acc, locked, archived


def _statuses(db, perms):
    user = gate_user(db, perms)
    pid, case, acc, locked, archived = _fixture(db)
    res = run_legacy(_calls(case, locked, archived, pid, acc, user), user=user)
    return {route: r["status"] for route, r in zip(ROUTES, res)}, res, (pid, case, locked, archived)


def test_legacy_routes_refuse_a_user_without_the_keys(ac, db):
    user = gate_user(db, {"clints": True, "clints_details": True, "clints_archive_": True})
    pid, case, acc, locked, archived = _fixture(db)
    snap = business_snapshot(db, [case, locked, archived])
    res = run_legacy(_calls(case, locked, archived, pid, acc, user), user=user)
    assert [(r["status"], r["body"]) for r in res] == [(403, FORBIDDEN)] * len(ROUTES)
    assert business_snapshot(db, [case, locked, archived]) == snap
    assert q1(db, 'SELECT count(*) FROM "clintArchive" c JOIN clints k ON k.id = c.clint_id '
                  'WHERE k.portfolios_id = %s', (pid,)) == (1,)


def test_archive_key_opens_the_archive_routes_only(ac, db):
    st, res, (pid, case, locked, archived) = _statuses(db, {"clints_move_archive": True})
    assert st == {"archive/<id>": 200, "archive/ids": 200, "archivebyexcel": 200, "archive/portofolio": 200,
                  "unarchive/<id>": 403, "unarchivebyexcel": 403, "lock/<id>": 403, "unlock/<id>": 403,
                  "bulk-lock": 403, "bulk-unlock": 403}
    # unchanged legacy behaviour with the key (the same case archived four times, as legacy allows)
    assert res[0]["body"]["is_archive"] is True
    assert res[1]["body"] == {"message": "  تم نقل المحقظة للأرشيف بنجاح ", "status": True}
    assert res[2]["body"] == DONE_EXCEL
    assert res[3]["body"] == {"message": "تم نقل جميع العملاء في المحفظة للأرشيف بنجاح", "status": True}
    # the single-case route no longer uses the frontend's `id` (the case id) as the history
    # row id, which broke re-archiving after a restore (tests/test_legacy_archive_rearchive.py)
    rows = history(db, case)
    assert len(rows) == 3 and case not in [r[0] for r in rows]
    assert arch_state(db, locked)[0] is True and arch_state(db, archived)[0] is True     # portfolio archived all


def test_screen_keys_open_their_own_routes(ac, db):
    st, _res, _ids = _statuses(db, {"clints_UpdateByExcel": True})           # clients list Excel menu
    assert [k for k, v in st.items() if v != 403] == ["archivebyexcel"]
    st, _res, (pid, case, *_rest) = _statuses(db, {"portfolios": True})       # portfolios screen
    assert [k for k, v in st.items() if v != 403] == ["archive/portofolio"]
    assert arch_state(db, case)[0] is True
    for key in ("clints_archive", "clints_archive_move"):                       # archive screen / dedicated key
        st, res, (_p, _c, _l, archived) = _statuses(db, {key: True})
        assert [k for k, v in st.items() if v != 403] == ["unarchive/<id>", "unarchivebyexcel"], key
        assert res[4]["body"]["is_archive"] is False and res[5]["body"] == DONE_EXCEL
        assert arch_state(db, archived)[:2] == (False, None) and len(history(db, archived)) == 1


def test_lock_keys_open_the_lock_routes(ac, db):
    st, res, (_p, case, locked, _a) = _statuses(db, {"clints_lock": True})    # clients list lock buttons
    assert [k for k, v in st.items() if v != 403] == ["lock/<id>", "unlock/<id>", "bulk-lock", "bulk-unlock"]
    assert res[6]["body"] == {"message": "تم قفل العميل بنجاح", "status": True}
    assert res[7]["body"] == {"message": "تم إلغاء قفل العميل بنجاح", "status": True}
    assert res[8]["body"] == {"message": "تم قفل 0 عميل و 1 عميل مقفل بالفعل", "status": True}
    assert res[9]["body"] == {"message": "تم إلغاء قفل 0 عميل و 1 عميل غير مقفل بالفعل", "status": True}
    st, _res, _ids = _statuses(db, {"clints_bulk_lock": True})                  # the spec's key, held by nobody
    assert [k for k, v in st.items() if v != 403] == ["lock/<id>", "unlock/<id>", "bulk-lock", "bulk-unlock"]
