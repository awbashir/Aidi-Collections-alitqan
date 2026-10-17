"""Note sub-statuses in reports and lists (customer 2026-10-17, migration 0068).

Every report/list that shows a note status also shows the note's optional sub-status and can be
filtered by it; the main-status filter of the with/without payments, neglect and next-calls
screens (sent as `selectedCommentStatus_Qkey[]`, ignored before) now works too.

Covered: legacy team/my notes (+ exports), promise dates (+ export), the clients list, archive,
with/without payments, neglect, next calls (+ export), the v2 case notes and follow-up source
note, the campaign pool filter, and one v2 report column + filter + selector (RPT-04, RPT-02).
"""
from __future__ import annotations

import datetime as dt
import uuid

from test_admin_support import admin_db, db, make_case, make_payment, make_user, q, q1, run_app  # noqa: F401
from test_reports_v2_support import R, rc, rep_app, rep_db, rep_storage  # noqa: F401
from test_reports_v2_support import make_case as rep_case
from test_reports_v2_support import make_portfolio as rep_portfolio
from test_reports_v2_support import set_perm

CONTACT_METHOD = "00000000-0000-4000-8000-000000000701"     # tests/fixtures/seed.sql
PERMS = {"clints": True, "clints_details": True, "follow_ups": True}


def one(conn, sql, params=None):
    return q1(conn, sql, params)[0]


def new_status(conn, name, parent=None):
    return one(conn, 'INSERT INTO "CommentStatuses" (name, parent_id) VALUES (%s, %s) RETURNING id::text',
               (name, parent))


def note(conn, case, user, status, sub=None, at=None, callback=None, text="T note"):
    return one(conn, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
                     "comment_sub_status_id, create_at, next_contact_date) "
                     "VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()), %s) RETURNING id::text",
               (case, user, CONTACT_METHOD, text, status, sub, at, callback))


def promise(conn, case, user, when):
    return one(conn, 'INSERT INTO "paymentDates" (clint_id, user_id, payment_date, amount) '
                     "VALUES (%s, %s, %s, 500) RETURNING id::text", (case, user, when))


def calls_as(user, calls):
    steps = [{"op": "mint", "user": user, "as": "T"}]
    steps += [{"op": "call", "token": "T", "method": "GET", "path": p} for p in calls]
    return [r for r in run_app(steps) if r["op"] == "call"]


def qs(*pairs):
    return "&".join(f"{k}={v}" for k, v in pairs)


def ids(rows, key="id"):
    return {r[key] for r in rows}


def test_legacy_reports_and_client_lists_show_and_filter_the_sub_status(db):
    tag = uuid.uuid4().hex[:6]
    col = make_user(db, PERMS)
    main = new_status(db, "T rpt main " + tag)
    sub1 = new_status(db, "T rpt sub one " + tag, parent=main)
    sub2 = new_status(db, "T rpt sub two " + tag, parent=main)
    other = new_status(db, "T rpt other " + tag)

    now = dt.datetime.now(dt.timezone.utc)
    soon = (now + dt.timedelta(days=1)).isoformat()
    c1, c2, c3 = (make_case(db, 100 * (i + 1), owner=col) for i in range(3))
    old1 = note(db, c1, col, main, at=(now - dt.timedelta(hours=3)).isoformat(), text="older")
    n1 = note(db, c1, col, main, sub1, callback=soon)          # latest of c1: main + sub1, a callback
    n2 = note(db, c2, col, main, sub2)                         # latest of c2: main + sub2
    n3 = note(db, c3, col, other, callback=soon)               # latest of c3: other, no sub-status
    make_payment(db, c1, 50, now.isoformat(), recorder=col)    # c1 has payments, c2/c3 have none
    p1, p3 = promise(db, c1, col, soon), promise(db, c3, col, soon)
    # an archived case whose latest note carries sub1
    c4 = make_case(db, 400, owner=col, archived=True)
    n4 = note(db, c4, col, main, sub1)                         # (notes reports include archived cases)
    reason = one(db, 'INSERT INTO "archiveReason" (name) VALUES (%s) RETURNING id::text', ("T reason " + tag,))
    arch = one(db, 'INSERT INTO "clintArchive" (clint_id, user_id, archive_reason_id) VALUES (%s, %s, %s) '
                   "RETURNING id::text", (c4, col, reason))
    q(db, 'UPDATE clints SET "clintArchive_id" = %s WHERE id = %s', (arch, c4))

    d0, d1 = (now - dt.timedelta(days=2)).date(), (now + dt.timedelta(days=3)).date()
    dates = qs(("customeFilterDateValue[]", d0), ("customeFilterDateValue[]", d1))
    pg = "page=1&pageSize=100"
    me = qs(("selectedUsers_Qkey[]", col), ("selectedUsers_Jkey", "user_id"), ("selectedUsers_Type", "string"))

    def sub_q(sid, jkey):
        return qs(("selectedCommentSubStatus_Qkey[]", sid), ("selectedCommentSubStatus_Jkey", jkey),
                  ("selectedCommentSubStatus_Type", "string"))

    res = calls_as(col, [
        f"/comments?{pg}&{dates}&{me}",                                                         # 0
        f"/comments?{pg}&{dates}&{me}&{sub_q(sub1, 'comment_sub_status_id')}",                  # 1
        f"/comments/export?{dates}&{me}&{sub_q(sub2, 'comment_sub_status_id')}",                # 2
        f"/comments/current_user?{pg}&{dates}&field=sub_status_.name&sort=desc",                # 3
        f"/comments/current_user/export?{dates}&{sub_q(sub1, 'comment_sub_status_id')}",        # 4
        f"/payment_date/currentUser?{pg}&{dates}",                                              # 5
        f"/payment_date/currentUser?{pg}&{dates}&{sub_q(sub1, 'comment.comment_sub_status_id')}",  # 6
        f"/payment_date/export?{dates}&{sub_q(sub1, 'comment.comment_sub_status_id')}",         # 7
        f"/clints?{pg}",                                                                        # 8
        f"/clints?{pg}&customeFilterCommentSubStatusValue[]={sub1}",                            # 9
        f"/clients/export?customeFilterCommentStatusValue[]={main}"
        f"&customeFilterCommentSubStatusValue[]={sub2}",                                        # 10
        f"/clints/archive/?page=1&pageSize=100&SelectedCommentSubStatus_Qkey[]={sub1}",         # 11
        f"/clints/have_payments?{pg}&selectedCommentStatus_Qkey[]={other}",                     # 12
        f"/clints/not_have_payments?{pg}&selectedCommentStatus_Qkey[]={main}",                  # 13
        f"/clints/not_have_payments/export?selectedCommentSubStatus_Qkey[]={sub2}",             # 14
        f"/clints/neglect?{pg}&selectedCommentStatus_Qkey[]={other}",                           # 15
        f"/clints/neglect/export?selectedCommentSubStatus_Qkey[]={sub1}",                       # 16
        f"/clints/currentUser/have_nextCalls?{pg}&{dates}&selectedCommentStatus_Qkey[]={main}",  # 17
        f"/clints/currentUser/have_nextCalls/export?{dates}&selectedCommentSubStatus_Qkey[]={sub1}",  # 18
        f"/clints/currentUser/have_nextCalls?{pg}&{dates}&selectedCommentStatus_Qkey[]={other}",  # 19
        f"/clints/neglect?{pg}&selectedCommentStatus_Qkey[]=not-a-uuid",                        # 20
        f"/v2/cases/{c1}/notes",                                                                # 21
        "/v2/follow-ups?page_size=100",                                                         # 22
    ])
    assert all(r["status"] == 200 for r in res), [(i, r["status"], str(r["body"])[:300])
                                                  for i, r in enumerate(res) if r["status"] != 200]
    b = [r["body"] for r in res]

    # ---- team notes: the sub-status next to the (unchanged) main status; filter by sub-status
    rows = {r["id"]: r for r in b[0]["comments"]}
    assert set(rows) == {old1, n1, n2, n3, n4} and b[0]["countRow"] == 5
    assert rows[n1]["status"]["id"] == main
    assert (rows[n1]["sub_status"]["id"], rows[n1]["sub_status"]["name"]) == (sub1, "T rpt sub one " + tag)
    assert rows[n2]["sub_status"]["name"] == "T rpt sub two " + tag
    assert rows[old1]["sub_status"] is None and rows[n3]["sub_status"] is None
    assert ids(b[1]["comments"]) == {n1, n4} and b[1]["countRow"] == 2
    assert ids(b[2]["comments"]) == {n2} and b[2]["comments"][0]["sub_status"]["id"] == sub2
    # ---- my notes: sortable by the sub-status name (desc: "two" before "one", notes without one last)
    mine = [r["id"] for r in b[3]["comments"]]
    assert mine.index(n2) < mine.index(n1) < mine.index(n3)
    assert ids(b[4]["comments"]) == {n1, n4}

    # ---- promise dates: the sub-status of the case's latest note
    pd = {r["id"]: r for r in b[5]["payment_dates"]}
    assert set(pd) == {p1, p3}
    assert pd[p1]["comment_sub_status"]["id"] == sub1 and pd[p1]["comment_status"]["id"] == main
    assert pd[p3]["comment_sub_status"] is None
    assert ids(b[6]["payment_dates"]) == {p1} and b[6]["countRow"] == 1
    assert ids(b[7]["payment_dates"]) == {p1}

    # ---- clients list (+ export) and archive
    cl = {r["id"]: r for r in b[8]["clints"]}
    assert set(cl) == {c1, c2, c3}
    assert cl[c1]["comment_sub_status"]["name"] == "T rpt sub one " + tag
    assert cl[c2]["comment_sub_status"]["id"] == sub2 and cl[c3]["comment_sub_status"] is None
    assert ids(b[9]["clints"]) == {c1} and b[9]["countRow"] == 1
    assert ids(b[10]["clients"]) == {c2}
    assert ids(b[11]["clints_archive"]) == {c4} and b[11]["countRow"] == 1
    assert b[11]["clints_archive"][0]["comment_sub_status"]["id"] == sub1

    # ---- the three screens whose status filter used to be ignored: rows AND counts follow it
    assert b[12]["clints"] == [] and b[12]["countRow"] == 0                    # c1 (paid) has `main`
    assert ids(b[13]["clints"]) == {c2} and b[13]["countRow"] == 1             # c3 has `other`
    assert b[13]["clints"][0]["comment_sub_status"]["id"] == sub2
    assert ids(b[14]["clints"]) == {c2}
    assert ids(b[15]["clints"]) == {c3} and b[15]["countRow"] == 1
    assert ids(b[16]["clints"]) == {c1} and b[16]["clints"][0]["comment_sub_status"]["id"] == sub1
    nxt = b[17]["clints"]
    assert ids(nxt) == {c1} and b[17]["countRow"] == 1 and nxt[0]["comment"]["id"] == n1
    assert nxt[0]["comment_sub_status"]["id"] == sub1
    assert ids(b[18]["clints"]) == {c1}
    assert ids(b[19]["clints"]) == {c3} and b[19]["clints"][0]["comment_sub_status"] is None
    assert b[20]["clints"] == [] and b[20]["countRow"] == 0                    # a malformed id: no match, no 500

    # ---- v2 case notes and the follow-up's source note
    notes = {n["id"]: n for n in b[21]["data"]["items"]}
    assert notes[n1]["sub_status"] == {"id": sub1, "name": "T rpt sub one " + tag}
    assert notes[old1]["sub_status"] is None and notes[n1]["status"]["id"] == main
    fus = [f for f in b[22]["data"]["items"] if (f.get("source_note") or {}).get("id") in (n1, n3)]
    by_note = {f["source_note"]["id"]: f["source_note"] for f in fus}
    assert by_note[n1]["sub_status"] == "T rpt sub one " + tag and by_note[n1]["status"] == "T rpt main " + tag
    assert by_note[n3]["sub_status"] is None


def test_campaign_pool_accepts_the_sub_status_filter():
    from services.calling import campaign_pool as cp
    sub = str(uuid.uuid4())
    pool = cp.validate_pool({"filter": {"customeFilterCommentSubStatusValue[]": [sub]}})
    assert pool["filter"] == {"customeFilterCommentSubStatusValue[]": [sub]}
    joins, where, params = cp.definition_sql(pool)
    assert "lc.comment_sub_status_id = ANY" in where and joins.count('"Comments" lc') == 1
    assert sub in [v for p in params.values() if isinstance(p, list) for v in p]


# --------------------------------------------------------------------------- v2 reports
def test_v2_note_report_sub_status_column_filter_and_selector(rc, db):
    saved = one(db, "SELECT permission::text FROM roles WHERE id = %s", (R.role_super,))
    try:
        set_perm(db, R.role_super, "report_users_comments", True)
        set_perm(db, R.role_super, "clints", True)
        _v2_checks(rc, db)
    finally:
        q(db, "UPDATE roles SET permission = %s::json WHERE id = %s", (saved, R.role_super))
        from core import lookups
        lookups.invalidate()


def _v2_checks(rc, db):
    tag = uuid.uuid4().hex[:6]
    main = new_status(db, "T v2 main " + tag)
    sub1 = new_status(db, "T v2 sub one " + tag, parent=main)
    sub2 = new_status(db, "T v2 sub two " + tag, parent=main)
    pf = rep_portfolio(db, R.contract_a)
    c1, c2 = rep_case(db, pf, owner=R.col1), rep_case(db, pf, owner=R.col1)
    n1 = note(db, c1, R.col1, main, sub1, at="2026-05-10T09:00:00Z")
    n2 = note(db, c2, R.col1, main, at="2026-05-11T09:00:00Z")
    n3 = note(db, c2, R.col1, main, sub2, at="2026-05-12T09:00:00Z")
    may = {"created_from": "2026-05-01", "created_to": "2026-05-31", "portfolio": pf}

    d = rc.rows("RPT-04", R.super, may)
    by = {i["note_id"]: i for i in d["items"]}
    assert set(by) == {n1, n2, n3}
    assert by[n1]["sub_status"] == "T v2 sub one " + tag and by[n2]["sub_status"] is None
    assert by[n1]["status"] == "T v2 main " + tag
    only = rc.rows("RPT-04", R.super, {**may, "sub_status": sub2})
    assert [i["note_id"] for i in only["items"]] == [n3] and only["total"] == 1
    asc = [i["note_id"] for i in rc.rows("RPT-04", R.super, {**may, "sort": "sub_status"})["items"]]
    assert asc.index(n1) < asc.index(n3)

    meta = rc.get("/v2/reports/RPT-04", R.super).get_json()["data"]
    cols = [c["key"] for c in meta["columns"]]
    assert cols.index("sub_status") == cols.index("status") + 1
    flt = {f["key"]: f for f in meta["filters"]}
    assert flt["sub_status"]["selector"]["entity"] == "comment_sub_status"
    assert "status" in flt["sub_status"]["depends_on"]

    # RPT-02: latest note's sub-status column and filter; the selector lists the sub-statuses
    # found under the chosen main status, with the main status as the secondary label
    cases = rc.rows("RPT-02", R.super, {"portfolio": pf})
    latest = {i["case_id"]: i["last_note_sub_status"] for i in cases["items"]}
    assert latest == {c1: "T v2 sub one " + tag, c2: "T v2 sub two " + tag}
    assert [i["case_id"] for i in rc.rows("RPT-02", R.super, {"portfolio": pf, "note_sub_status": sub1})["items"]] == [c1]
    opts = rc.get("/v2/report-options/note_sub_status", R.super,
                  {"report": "RPT-02", "note_status": main}).get_json()["data"]
    assert {i["id"] for i in opts["items"]} >= {sub1, sub2}
    assert all(i["secondary"] == "T v2 main " + tag for i in opts["items"] if i["id"] in (sub1, sub2))
    assert opts["entity"] == "comment_sub_status"
