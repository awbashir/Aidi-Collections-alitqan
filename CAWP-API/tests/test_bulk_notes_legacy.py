"""Server-side permission checks on the stream C legacy routes (phase2-spec: "each stream
also adds server-side permission checks to the legacy bulk routes in its area, using the
keys their screens use"). Runs the REAL app.py (subprocess, test_money_support.run_legacy).

* POST /commentsbyexcel   -> `clints_UpdateByExcel`: the clients list shows "إضافة متابعات
  بالإكسل" only with it (CAWP-frontend/src/views/clients/list/ClientsTable.tsx, excelActions
  key "comments" and the mounted <CommentByExcelButton>).
* POST /send/notification -> `clints_sepcifyUser`: the same screen shows "ارسال تنبيه" only
  with it (excelActions key "notify" and the mounted <SendNotification>).

The handlers themselves are unchanged (same bodies and status codes).
"""
from test_bulk_notes_support import *  # noqa: F401,F403
from test_money_support import run_legacy

FORBIDDEN = {"message": "You are not authorized to access this resource"}


def _comments_body(file_name="no-such-file.xlsx"):
    return {"excel_data": file_name, "index": 0, "contract_id": N.contract_a, "data1": "Account",
            "comment": "legacy excel note", "comment_status_id": N.s_a, "contactMethod_id": N.method,
            "comment_reachability_id": N.r_ok, "contactNumber": "", "call_id": "", "clint_id": ""}


def _call(method, path, user, body):
    return run_legacy([{"method": method, "path": path, "json": body}], user=user)[0]


def test_comments_by_excel_needs_the_update_by_excel_key(nc, db):
    for user in (N.none, N.notify, N.mgr):
        r = _call("POST", "/commentsbyexcel", user, _comments_body())
        assert (r["status"], r["body"]) == (403, FORBIDDEN), user
    r = _call("POST", "/commentsbyexcel", N.col1, _comments_body())      # the key: legacy behaviour unchanged
    assert (r["status"], r["body"]) == (200, {"message": "تم إنشاء التعليق بنجاح", "status": True})


def test_comments_by_excel_still_writes_notes_with_the_key(nc, db, tmp_path, monkeypatch):
    c, acc = make_case(db)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "notes-legacy.xlsx").write_bytes(make_xlsx([["Account"], [acc]]))
    denied = _call("POST", "/commentsbyexcel", N.none, _comments_body("notes-legacy.xlsx"))
    assert denied["status"] == 403 and notes_of(db, c) == []
    r = _call("POST", "/commentsbyexcel", N.col1, _comments_body("notes-legacy.xlsx"))
    assert r["status"] == 200 and r["body"]["status"] is True
    [note] = notes_of(db, c)
    assert note["user_id"] == N.col1 and note["comment"] == "legacy excel note"


def test_send_notification_needs_the_specify_users_key(nc, db):
    body = {"file_name": "no-such-file.xlsx", "index": 0, "message": "call the debtor"}
    for user in (N.none, N.col1, N.mgr):                  # n.col1 has clints_UpdateByExcel, not the key
        r = _call("POST", "/send/notification", user, body)
        assert (r["status"], r["body"]) == (403, FORBIDDEN), user
    for user in (N.notify, N.sup):
        r = _call("POST", "/send/notification", user, body)
        assert (r["status"], r["body"]) == (200, {"message": "تم إرسال الإشعارات بنجاح", "status": True}), user
