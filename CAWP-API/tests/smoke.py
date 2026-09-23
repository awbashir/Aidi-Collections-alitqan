#!/usr/bin/env python
"""Post-integration smoke test - single target, stdlib only.

    docker exec cawp-api sh -c 'cd /app && PYTHONPATH=/app python tests/smoke.py'
    BASE=http://localhost:9797 ... python tests/smoke.py      # override the target

`tests/c_parity.py` and `tests/b{1,2}_parity.py` compare the legacy handlers against the
`/_new/...` blueprint twins; the legacy handlers were deleted at integration, so those
harnesses can no longer run. This one exercises the surviving routes against a single
server:

  1. every Excel-import / bulk route, synchronously **and** with `?async=1` + polling
     `GET /jobs/<id>` (the job `result` must equal the synchronous body);
  2. bulk lock / unlock / archive / unarchive on the clients it just created;
  3. `DELETE /clints` on them;
  4. `POST /logout` -> the revoked token must yield 401;
  5. `required_roles`: a collector calling `DELETE /portfolios/<id>` must get 403.

Everything it creates lives in portfolios named `SMOKE-<ts>-*` (the same `C-TEST`-style
synthetic account/identity ranges), and the `finally` block deletes them. Nothing
pre-existing is read-modified or deleted.
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import openpyxl
from sqlalchemy import text

sys.path.insert(0, "/app")

from app import app                       # noqa: E402
from extensions import db                 # noqa: E402

BASE = os.environ.get("BASE", "http://localhost:9797")
ADMIN = {"username": "aidi.dev", "password": "Aidi@2026"}
COLLECTOR = {"username": os.environ.get("SMOKE_COLLECTOR", "aidi.collector"),
             "password": os.environ.get("SMOKE_COLLECTOR_PW", "Aidi@2026")}

TMP = "/app/uploads/temp"
TS = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
TAG = f"SMOKE-{TS}"

CONTRACT_ID = "6f85326f-9dd7-46c1-8c85-bdfb86cc0c8c"
ARCHIVE_REASON_ID = "f2184113-d08d-4438-a1fe-a4db32789464"
CONTACT_METHOD_ID = "b55aa031-9349-4a3a-ad14-1f9d6821a342"
COMMENT_STATUS_ID = "1c0466b4-c3f5-4841-bad4-1b111ac8f821"
REACHABILITY_ID = "9d45dcb5-25fa-4b9b-aa03-3488e85ad81a"
COLLECTOR_USER = "rasha.s"
COLLECTOR_ID = "ad0d801b-78ba-42d3-a57a-6e7f8b3f86b2"
NOTIFY_MESSAGE = f"تنبيه اختبار {TAG}"

PASS, FAIL = [], []


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)
    return ok


def call(method, path, tok=None, body=None, timeout=600):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method.upper())
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    ms = round((time.perf_counter() - t) * 1000)
    try:
        payload = json.loads(raw)
    except Exception:                      # noqa: BLE001
        payload = {"_raw": raw[:200].decode("utf-8", "replace")}
    return status, payload, ms


def token(creds):
    status, body, _ = call("post", "/login", body=creds)
    if status != 200 or "access_token" not in body:
        raise SystemExit(f"login failed for {creds['username']}: {status} {body}")
    return body["access_token"]


def sql(q, **params):
    with app.app_context():
        rows = db.session.execute(text(q), params).all()
        db.session.remove()
    return rows


def exec_sql(*statements, **params):
    with app.app_context():
        for q in statements:
            db.session.execute(text(q), params)
        db.session.commit()
        db.session.remove()


def await_job(tok, job_id, timeout=300):
    """Poll `GET /jobs/<id>` until it leaves queued/running."""
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        status, job, _ = call("get", f"/jobs/{job_id}", tok)
        if status != 200:
            return {"status": "http-" + str(status), "result": job}
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.2)
    return job or {"status": "timeout"}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def gen_rows(n, offset=0):
    return [{
        "name": f"عميل {TAG} {i}",
        "identityNumber": int(f"7{TS[-8:]}{i:05d}") + offset,
        "accountNumber": int(f"9{TS[-8:]}{i:05d}") + offset,
        "amount": 1000.0 + i,
        "mobile_1": 500000000 + i,
        "email_1": f"s{i}@smoke.local",
        "البروفجن": f"P{i % 3}",
    } for i in range(n)]


def column_file(values, name):
    os.makedirs(TMP, exist_ok=True)
    path = os.path.join(TMP, name)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["accountNumber"])
    for v in values:
        ws.append([v])
    wb.save(path)
    return os.path.basename(path)


def clints_of(portfolio_name):
    return [(str(r[0]), r[1], r[2]) for r in sql(
        'SELECT c.id, c."accountNumber", c."identityNumber" FROM clints c '
        'JOIN portfolios p ON p.id = c.portfolios_id WHERE p.name = :n '
        'ORDER BY c."accountNumber"', n=portfolio_name)]


def portfolio_id(name):
    rows = sql("SELECT id FROM portfolios WHERE name = :n", n=name)
    return str(rows[0][0]) if rows else None


# --------------------------------------------------------------------------- #
# the tests
# --------------------------------------------------------------------------- #

def t_add_clints(tok, n=6):
    print(f"\n== POST /clint/addClintsByExcel (sync, {n} rows) ==")
    name = f"{TAG}-A"
    st, body, ms = call("post", "/clint/addClintsByExcel", tok,
                        {"contract_id": CONTRACT_ID, "name": name, "data": gen_rows(n)})
    check("addClintsByExcel: 200", st == 200, f"{st} {body} {ms}ms")
    rows = clints_of(name)
    check(f"addClintsByExcel: {n} clients inserted", len(rows) == n, f"got {len(rows)}")

    print(f"\n== POST /clint/addClintsByExcel?async=1 ({n} rows) ==")
    name2 = f"{TAG}-B"
    st, body, _ = call("post", "/clint/addClintsByExcel?async=1", tok,
                       {"contract_id": CONTRACT_ID, "name": name2,
                        "data": gen_rows(n, offset=5_000_000)})
    check("addClintsByExcel(async): 202 + job_id",
          st == 202 and body.get("job_id") and body.get("async") is True, f"{st} {body}")
    job = await_job(tok, body.get("job_id", ""))
    check("addClintsByExcel(async): job done", job.get("status") == "done",
          json.dumps(job, ensure_ascii=False)[:220])
    check("addClintsByExcel(async): job result is the sync body shape",
          isinstance(job.get("result"), dict) and "status" in job["result"],
          json.dumps(job.get("result"), ensure_ascii=False)[:160])
    rows2 = clints_of(name2)
    check(f"addClintsByExcel(async): {n} clients inserted", len(rows2) == n,
          f"got {len(rows2)}")
    return name, name2


def t_update_byexcel(tok, name):
    print("\n== POST /clints/update/byexcel ==")
    rows = clints_of(name)
    body = {"data1": "acc", "data2": "v", "update_type": "2",
            "update_side": None, "fileDataRow":
            [{"acc": a, "v": 7000.0 + i} for i, (_c, a, _i) in enumerate(rows)]}
    st, resp, _ = call("post", "/clints/update/byexcel", tok, body)
    check("update/byexcel type=2: 200 + status true",
          st == 200 and resp.get("status") is True, f"{st} {resp}")
    amounts = [float(r[0]) for r in sql(
        'SELECT c.amount FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
        'WHERE p.name=:n ORDER BY c."accountNumber"', n=name)]
    check("update/byexcel type=2: amounts written",
          amounts == [7000.0 + i for i in range(len(rows))], str(amounts[:3]))

    st, resp, _ = call("post", "/clints/update/byexcel?async=1", tok,
                       dict(body, fileDataRow=[{"acc": a, "v": 8000.0 + i}
                                               for i, (_c, a, _i) in enumerate(rows)]))
    check("update/byexcel(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    job = await_job(tok, resp.get("job_id", ""))
    check("update/byexcel(async): job done + result.status", job.get("status") == "done"
          and (job.get("result") or {}).get("status") is True,
          json.dumps(job.get("result"), ensure_ascii=False)[:160])


def t_attribute(tok, name):
    print("\n== POST /clints/attribute/byexcel ==")
    rows = clints_of(name)
    body = {"identifier_column": "acc", "identifier_type": "accountNumber",
            "value_column": "val", "attribute_key": "الخاصية",
            "fileDataRow": [{"acc": a, "val": f"V{i}"}
                            for i, (_c, a, _i) in enumerate(rows)]}
    st, resp, _ = call("post", "/clints/attribute/byexcel", tok, body)
    check("attribute/byexcel: 200", st == 200, f"{st} {resp}")
    vals = [json.loads(r[0]).get("الخاصية") for r in sql(
        'SELECT c.attribute::text FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
        'WHERE p.name=:n ORDER BY c."accountNumber"', n=name)]
    check("attribute/byexcel: values written",
          vals == [f"V{i}" for i in range(len(rows))], str(vals[:3]))

    st, resp, _ = call("post", "/clints/attribute/byexcel", tok,
                       dict(body, identifier_column=None))
    check("attribute/byexcel: validation error is synchronous 201",
          st == 201 and resp.get("status") is False, f"{st} {resp}")

    st, resp, _ = call("post", "/clints/attribute/byexcel?async=1", tok, body)
    check("attribute/byexcel(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    job = await_job(tok, resp.get("job_id", ""))
    check("attribute/byexcel(async): job done", job.get("status") == "done",
          json.dumps(job.get("result"), ensure_ascii=False)[:160])


def t_sepcify_by_excel(tok, name):
    print("\n== POST /clint/SepcifyUsersByExcel ==")
    rows = clints_of(name)
    body = {"user_name": "u", "account_number": "acc",
            "data": [{"acc": a, "u": COLLECTOR_USER} for _c, a, _i in rows]}
    st, resp, _ = call("post", "/clint/SepcifyUsersByExcel", tok, body)
    check("SepcifyUsersByExcel: 200", st == 200, f"{st} {resp}")
    n = sql('SELECT count(*) FROM "SepcifyUsers" s JOIN clints c ON c.id=s.clint_id '
            'JOIN portfolios p ON p.id=c.portfolios_id WHERE p.name=:n', n=name)[0][0]
    check("SepcifyUsersByExcel: SepcifyUsers rows created", n >= len(rows), f"{n} rows")

    st, resp, _ = call("post", "/clint/SepcifyUsersByExcel?async=1", tok, body)
    check("SepcifyUsersByExcel(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    check("SepcifyUsersByExcel(async): job done",
          await_job(tok, resp.get("job_id", "")).get("status") == "done")


def t_comments(tok, name):
    print("\n== POST /commentsbyexcel ==")
    rows = clints_of(name)
    f = column_file([a for _c, a, _i in rows], f"c_{TAG}_comments.xlsx")
    body = {"call_id": "", "clint_id": "", "contactNumber": "0500000000",
            "contract_id": CONTRACT_ID, "contactMethod_id": CONTACT_METHOD_ID,
            "comment_reachability_id": REACHABILITY_ID,
            "comment_status_id": COMMENT_STATUS_ID, "comment": f"تعليق {TAG}",
            "data1": "accountNumber", "index": 0, "excel_data": f}
    st, resp, _ = call("post", "/commentsbyexcel", tok, body)
    check("commentsbyexcel: 200", st == 200, f"{st} {resp}")
    n = sql('SELECT count(*) FROM "Comments" cm JOIN clints c ON c.id=cm.clint_id '
            'JOIN portfolios p ON p.id=c.portfolios_id WHERE p.name=:n', n=name)[0][0]
    check("commentsbyexcel: comments created", n >= len(rows), f"{n} rows")
    # the trigger from db/03_last_comment.sql must have filled the denormalised columns
    filled = sql('SELECT count(*) FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
                 'WHERE p.name=:n AND c.last_comment_id IS NOT NULL', n=name)[0][0]
    check("commentsbyexcel: clints.last_comment_id maintained by the trigger",
          filled == len(rows), f"{filled}/{len(rows)}")

    f2 = column_file([a for _c, a, _i in rows], f"c_{TAG}_comments2.xlsx")
    st, resp, _ = call("post", "/commentsbyexcel?async=1", tok, dict(body, excel_data=f2))
    check("commentsbyexcel(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    check("commentsbyexcel(async): job done",
          await_job(tok, resp.get("job_id", "")).get("status") == "done")


def t_archive_cycle(tok, name):
    print("\n== archive / unarchive (byexcel, ids, portfolio) ==")
    rows = clints_of(name)
    ids = [c for c, _a, _i in rows]
    accs = [a for _c, a, _i in rows]

    def archived():
        return sql('SELECT count(*) FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
                   'WHERE p.name=:n AND c.is_archive', n=name)[0][0]

    st, resp, _ = call("put", "/clint/archivebyexcel", tok,
                       {"account_number": "acc", "archive_reason_id": ARCHIVE_REASON_ID,
                        "user_id": COLLECTOR_ID, "data": [{"acc": a} for a in accs]})
    check("archivebyexcel: 200", st == 200, f"{st} {resp}")
    check("archivebyexcel: all archived", archived() == len(rows), f"{archived()}")

    f = column_file(accs, f"c_{TAG}_unarch.xlsx")
    st, resp, _ = call("put", "/clint/unarchivebyexcel", tok, {"file_name": f, "index": 0})
    check("unarchivebyexcel: 200", st == 200, f"{st} {resp}")
    check("unarchivebyexcel: none archived", archived() == 0, f"{archived()}")

    st, resp, _ = call("put", "/clint/archive/ids", tok,
                       {"clint_ids": ids, "archive_reason_id": ARCHIVE_REASON_ID,
                        "user_id": COLLECTOR_ID})
    check("archive/ids: 200", st == 200, f"{st} {resp}")
    check("archive/ids: all archived", archived() == len(rows), f"{archived()}")

    f = column_file(accs, f"c_{TAG}_unarch2.xlsx")
    st, resp, _ = call("put", "/clint/unarchivebyexcel?async=1", tok,
                       {"file_name": f, "index": 0})
    check("unarchivebyexcel(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    job = await_job(tok, resp.get("job_id", ""))
    check("unarchivebyexcel(async): job done + un-archived",
          job.get("status") == "done" and archived() == 0, f"{archived()} archived")

    pid = portfolio_id(name)
    st, resp, _ = call("put", f"/clint/archive/portofolio/{pid}", tok,
                       {"archive_reason_id": ARCHIVE_REASON_ID})
    check("archive/portofolio/<id>: 200", st == 200, f"{st} {resp}")
    st, resp, _ = call("put", f"/clint/archive/portofolio/{pid}", tok,
                       {"archive_reason_id": ARCHIVE_REASON_ID})
    check("archive/portofolio/<id>: second call 404", st == 404, f"{st} {resp}")
    f = column_file(accs, f"c_{TAG}_unarch3.xlsx")
    call("put", "/clint/unarchivebyexcel", tok, {"file_name": f, "index": 0})


def t_sepcify_ids_and_locks(tok, name):
    print("\n== /sepcifyusers/ids + bulk lock/unlock ==")
    ids = [c for c, _a, _i in clints_of(name)]
    st, resp, _ = call("post", "/sepcifyusers/ids", tok,
                       {"clint_ids": ids, "user_id": COLLECTOR_ID})
    check("sepcifyusers/ids: 200", st == 200, f"{st} {resp}")

    st, resp, _ = call("put", "/clints/bulk-lock", tok,
                       {"client_ids": ids, "reason": f"قفل {TAG}"})
    locked = sql('SELECT count(*) FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
                 'WHERE p.name=:n AND c.is_locked', n=name)[0][0]
    check("bulk-lock: 200 + all locked", st == 200 and locked == len(ids),
          f"{st} locked={locked}")
    st, resp, _ = call("put", "/clints/bulk-lock", tok, {"client_ids": ids})
    check("bulk-lock: already-locked branch is still 200", st == 200, f"{st} {resp}")
    st, resp, _ = call("put", "/clints/bulk-lock", tok, {"client_ids": []})
    check("bulk-lock: empty client_ids -> 400", st == 400, f"{st} {resp}")

    st, resp, _ = call("put", "/clints/bulk-unlock?async=1", tok,
                       {"client_ids": ids, "reason": f"فتح {TAG}"})
    check("bulk-unlock(async): 202", st == 202 and resp.get("job_id"), f"{st} {resp}")
    job = await_job(tok, resp.get("job_id", ""))
    locked = sql('SELECT count(*) FROM clints c JOIN portfolios p ON p.id=c.portfolios_id '
                 'WHERE p.name=:n AND c.is_locked', n=name)[0][0]
    check("bulk-unlock(async): job done + all unlocked",
          job.get("status") == "done" and locked == 0, f"locked={locked}")


def t_notification(tok, name):
    print("\n== POST /send/notification ==")
    accs = [a for _c, a, _i in clints_of(name)]
    f = column_file(accs, f"c_{TAG}_notif.xlsx")
    st, resp, _ = call("post", "/send/notification", tok,
                       {"file_name": f, "index": 0, "message": NOTIFY_MESSAGE})
    check("send/notification: 200", st == 200, f"{st} {resp}")
    n = sql("SELECT count(*) FROM notifications WHERE data->>'message' = :m",
            m=NOTIFY_MESSAGE)[0][0]
    check("send/notification: notification rows written", n > 0, f"{n} rows")


def t_delete_clints(tok, name):
    print("\n== DELETE /clints ==")
    ids = [c for c, _a, _i in clints_of(name)]
    st, resp, _ = call("delete", "/clints", tok, {"clints_ids": ids})
    left = len(clints_of(name))
    check("DELETE /clints: 200 + rows gone (incl. rows with lock history)",
          st == 200 and left == 0, f"{st} {resp} left={left}")
    st, resp, _ = call("delete", "/clints", tok, {"clints_ids": [str(uuid.uuid4())]})
    check("DELETE /clints: unknown id is not fatal", st in (200, 201), f"{st} {resp}")


def t_logout_revokes(creds=ADMIN):
    print("\n== POST /logout -> 401 on the revoked token ==")
    tok = token(creds)
    st, _b, _ = call("get", "/roles", tok)
    check("logout: token works before logout", st == 200, str(st))
    st, body, _ = call("post", "/logout", tok)
    check("logout: 200", st == 200 and body.get("status") is True, f"{st} {body}")
    st, body, _ = call("get", "/roles", tok)
    check("logout: revoked token -> 401", st == 401, f"{st} {body}")


def t_required_roles():
    print("\n== required_roles: collector must not delete a portfolio ==")
    try:
        ctok = token(COLLECTOR)
    except SystemExit as e:
        check("required_roles: collector login", False, str(e))
        return
    ghost = str(uuid.uuid4())
    st, body, _ = call("delete", f"/portfolios/{ghost}", ctok)
    check("required_roles: collector DELETE /portfolios/<id> -> 403",
          st == 403 and "not authorized" in json.dumps(body), f"{st} {body}")
    atok = token(ADMIN)
    st, body, _ = call("delete", f"/portfolios/{ghost}", atok)
    check("required_roles: admin passes the gate (404 on a ghost id, not 403)",
          st == 404, f"{st} {body}")


def cleanup():
    print("\n== cleanup ==")
    with app.app_context():
        rows = db.session.execute(text(
            "SELECT id, name FROM portfolios WHERE name LIKE :p"), {"p": TAG + "%"}).all()
        for pid, name in rows:
            for q in (
                'UPDATE clints SET "clintArchive_id" = NULL WHERE portfolios_id = :p',
                "DELETE FROM notifications WHERE (data->>'client_id') IN "
                "(SELECT id::text FROM clints WHERE portfolios_id = :p)",
                'DELETE FROM "ClintContacts" WHERE "identityNumber" IN '
                '(SELECT "identityNumber" FROM clints WHERE portfolios_id = :p)',
                'DELETE FROM "clintArchive" WHERE clint_id IN '
                '(SELECT id FROM clints WHERE portfolios_id = :p)',
                'DELETE FROM clints WHERE portfolios_id = :p',
                'DELETE FROM portfolios WHERE id = :p',
            ):
                db.session.execute(text(q), {"p": pid})
            print("  dropped portfolio", name, flush=True)
        db.session.execute(text(
            "DELETE FROM \"ClintContacts\" WHERE contact LIKE '%@smoke.local'"))
        db.session.execute(text(
            "DELETE FROM notifications WHERE data->>'message' = :m"), {"m": NOTIFY_MESSAGE})
        db.session.commit()
        left = db.session.execute(text(
            "SELECT (SELECT count(*) FROM portfolios WHERE name LIKE :p),"
            " (SELECT count(*) FROM \"ClintContacts\" WHERE contact LIKE '%@smoke.local'),"
            " (SELECT count(*) FROM notifications WHERE data->>'message' = :m)"),
            {"p": TAG + "%", "m": NOTIFY_MESSAGE}).one()
        db.session.remove()
    removed = 0
    for f in os.listdir(TMP) if os.path.isdir(TMP) else []:
        if f.startswith(f"c_{TAG}"):
            os.remove(os.path.join(TMP, f))
            removed += 1
    print(f"  remaining: portfolios={left[0]} contacts={left[1]} notifications={left[2]};"
          f" temp workbooks removed={removed}", flush=True)
    return left == (0, 0, 0)


def main():
    print(f"target={BASE}  tag={TAG}")
    st, body, _ = call("get", "/health")
    check("/health -> 200 ok", st == 200 and body.get("status") == "ok", f"{st} {body}")
    tok = token(ADMIN)
    try:
        name, name_async = t_add_clints(tok)
        t_update_byexcel(tok, name)
        t_attribute(tok, name)
        t_sepcify_by_excel(tok, name)
        t_comments(tok, name)
        t_archive_cycle(tok, name)
        t_sepcify_ids_and_locks(tok, name)
        t_notification(tok, name)
        t_delete_clints(tok, name_async)
        t_required_roles()
        t_logout_revokes()
    finally:
        check("cleanup: nothing left behind", cleanup())

    print("\n" + "=" * 78)
    print(f"RESULT: {len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
    for f in FAIL:
        print("  FAILED:", f)
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
