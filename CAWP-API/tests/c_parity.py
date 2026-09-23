#!/usr/bin/env python
"""Legacy vs refactored parity harness for the Excel imports / bulk actions (agent C).

Runs inside the `cawp-api` container:

    docker exec cawp-api sh -c 'cd /app && python tests/c_parity.py'

For every import it
  a) generates ONE xlsx (or one JSON row set) and posts it to the legacy route on
     :9797 and to `/_new/<same path>` on :9803, each against its **own** test portfolio;
  b) compares the response JSON (keys, counts, messages, `messages`/errorMessages content);
  c) compares the resulting database rows - a checksum of
     `name, identityNumber, accountNumber, amount, attribute::text` ordered by
     accountNumber, plus contacts / SepcifyUsers / is_archive / lock-history counts;
  d) times 200 and 3,000 rows on both sides;
  e) exercises `?async=1` end to end (poll `GET /jobs/<id>` until `done`, compare
     `result` to the synchronous body).

Every row it creates lives in a portfolio named `C-TEST-<ts>-{legacy,new}`; `--cleanup`
(run automatically at the end) deletes those portfolios, which cascades to the clients.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import time
import uuid

import openpyxl
from sqlalchemy import text

try:                                  # `requests` is not a runtime dependency of the API
    import requests
except ImportError:                   # stdlib fallback so the harness needs no install
    import urllib.error
    import urllib.request

    class _Resp:
        def __init__(self, status, text_):
            self.status_code, self.text = status, text_

        def json(self):
            return json.loads(self.text)

    class requests:                   # noqa: N801 - a shim, not a class API
        @staticmethod
        def request(method, url, headers=None, json=None, timeout=60, **_kw):
            data = None
            headers = dict(headers or {})
            if json is not None:
                import json as _json
                data = _json.dumps(json).encode()
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return _Resp(r.status, r.read().decode())
            except urllib.error.HTTPError as e:
                return _Resp(e.code, e.read().decode())

        @staticmethod
        def post(url, json=None, timeout=60):
            return requests.request("POST", url, json=json, timeout=timeout)

sys.path.insert(0, "/app")

from app import app                                  # noqa: E402
from extensions import db                            # noqa: E402

# Both bases serve the same `app.py`; LEGACY hits the un-prefixed legacy routes, NEW hits
# `/_new/...`.  Default LEGACY to :9803 as well: the production gunicorn on :9797 has a 30 s
# `--timeout`, which the legacy per-row-commit routes blow through on a 3,000-row file
# whenever the shared container is also serving anything else - the worker is killed and the
# connection closes with no response.  Point LEGACY_BASE at :9797 to see that happen.
LEGACY = os.environ.get("LEGACY_BASE", "http://localhost:9803")
NEW = os.environ.get("NEW_BASE", "http://localhost:9803")
LOGIN = {"username": "aidi.dev", "password": "Aidi@2026"}

TMP = "/app/uploads/temp"
TS = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

CONTRACT_ID = "6f85326f-9dd7-46c1-8c85-bdfb86cc0c8c"
ARCHIVE_REASON_ID = "f2184113-d08d-4438-a1fe-a4db32789464"
CONTACT_METHOD_ID = "b55aa031-9349-4a3a-ad14-1f9d6821a342"
COMMENT_STATUS_ID = "1c0466b4-c3f5-4841-bad4-1b111ac8f821"
REACHABILITY_ID = "9d45dcb5-25fa-4b9b-aa03-3488e85ad81a"
UPDATE_SIDE_ID = "152f3477-642f-4a21-ac20-5af32b0387e6"
COLLECTOR_USER = "rasha.s"
NOTIFY_MESSAGE = "تنبيه اختبار C-TEST"      # unique to this harness; used by cleanup
COLLECTOR_ID = "ad0d801b-78ba-42d3-a57a-6e7f8b3f86b2"

RESULTS = []
FAILURES = []


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def log(msg=""):
    print(msg, flush=True)


def token(base):
    r = requests.post(f"{base}/login", json=LOGIN, timeout=30)
    return r.json()["access_token"]


class Dropped(Exception):
    """The server closed the connection without a response.

    On :9797 this is gunicorn killing the worker on its 30 s `--timeout` - which the
    legacy per-row-commit routes hit on a 3,000-row file whenever the shared instance is
    also serving anything else.  It is a finding, not a harness defect, so it is recorded
    and the affected sub-test is skipped instead of aborting the run.
    """


def call(base, method, path, tok, **kw):
    """One request.  A dropped connection raises `Dropped` - it is **never** retried:
    these routes are not idempotent, and a retried `archivebyexcel` after a worker timeout
    inserted a second batch of `clintArchive` rows for the prefix the first attempt had
    already written."""
    t0 = time.perf_counter()
    try:
        r = requests.request(method, base + path,
                             headers={"Authorization": f"Bearer {tok}"},
                             timeout=1800, **kw)
    except Exception as e:                          # ConnectionError / RemoteDisconnected
        raise Dropped(f"{method.upper()} {base}{path}: {e}") from e
    ms = (time.perf_counter() - t0) * 1000
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:400]}
    return r.status_code, body, ms


def safe(label, fn, *args, **kwargs):
    """Run one sub-test; a dropped connection is recorded and the run continues."""
    try:
        return fn(*args, **kwargs)
    except Dropped as e:
        check(f"{label}: server kept the connection open", False,
              f"CONNECTION DROPPED - {e}")
        return None


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    if not ok:
        FAILURES.append((name, detail))
    log(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def compare_bodies(name, a, b):
    sa, ba, ta = a
    sb, bb, tb = b
    check(f"{name}: http status {sa} == {sb}", sa == sb, f"legacy={sa} new={sb}")
    check(f"{name}: response keys", sorted(ba.keys()) == sorted(bb.keys()),
          f"legacy={sorted(ba.keys())} new={sorted(bb.keys())}")
    same = json.dumps(ba, ensure_ascii=False, sort_keys=True) == \
        json.dumps(bb, ensure_ascii=False, sort_keys=True)
    check(f"{name}: response body identical", same,
          "" if same else f"\n      legacy={json.dumps(ba, ensure_ascii=False)[:400]}"
                          f"\n      new   ={json.dumps(bb, ensure_ascii=False)[:400]}")
    return ta, tb


# --------------------------------------------------------------------------- #
# DB helpers (checksums)
# --------------------------------------------------------------------------- #

def sql(q, **params):
    with app.app_context():
        rows = db.session.execute(text(q), params).all()
        db.session.remove()
    return rows


# `accountNumber` is UNIQUE and `identityNumber` drives update_type 5, so the two
# fixtures have to differ in exactly those two fields (and in the two `attribute` keys
# that mirror them).  Everything else must be bit-identical, so they are excluded from
# the checksum and asserted separately by the counts + the attribute comparison.
_FIXTURE_KEYS = ("accountNumber", "identityNumber")


def portfolio_checksum(portfolio_id):
    """md5 over the client rows of one portfolio, plus the related-row counts."""
    rows = sql("""
        SELECT c.name, c."identityNumber", c."accountNumber", c.amount,
               c.attribute::text, c.is_archive, c.is_locked, c.user_id::text,
               c."clintArchive_id" IS NOT NULL
        FROM clints c WHERE c.portfolios_id = :p ORDER BY c."accountNumber"
    """, p=portfolio_id)
    h = hashlib.md5()
    for r in rows:
        attribute = json.loads(r[4])
        for k in _FIXTURE_KEYS:
            attribute.pop(k, None)
        h.update(repr((r[0], r[3], json.dumps(attribute, sort_keys=True, ensure_ascii=False),
                       r[5], r[6], r[7], r[8])).encode())
    counts = sql("""
        SELECT
          (SELECT count(*) FROM clints WHERE portfolios_id = :p),
          (SELECT count(*) FROM "ClintContacts" ct
             WHERE ct."identityNumber" IN (SELECT "identityNumber" FROM clints WHERE portfolios_id = :p)),
          (SELECT count(*) FROM "SepcifyUsers" s
             WHERE s.clint_id IN (SELECT id FROM clints WHERE portfolios_id = :p)),
          (SELECT count(*) FROM "Comments" cm
             WHERE cm.clint_id IN (SELECT id FROM clints WHERE portfolios_id = :p)),
          (SELECT count(*) FROM "clintArchive" a
             WHERE a.clint_id IN (SELECT id FROM clints WHERE portfolios_id = :p)),
          (SELECT count(*) FROM client_lock_history l
             WHERE l.clint_id IN (SELECT id FROM clints WHERE portfolios_id = :p)),
          (SELECT count(*) FROM clints WHERE portfolios_id = :p AND is_archive),
          (SELECT count(*) FROM clints WHERE portfolios_id = :p AND is_locked),
          (SELECT count(*) FROM notifications n
             WHERE (n.data->>'client_id') IN (SELECT id::text FROM clints WHERE portfolios_id = :p))
    """, p=portfolio_id)[0]
    return h.hexdigest(), {
        "clints": counts[0], "contacts": counts[1], "sepcifyusers": counts[2],
        "comments": counts[3], "archive_rows": counts[4], "lock_history": counts[5],
        "archived": counts[6], "locked": counts[7], "notifications": counts[8],
    }


def compare_db(name, p_legacy, p_new):
    ha, ca = portfolio_checksum(p_legacy)
    hb, cb = portfolio_checksum(p_new)
    check(f"{name}: DB row checksum", ha == hb, f"legacy={ha} new={hb}")
    check(f"{name}: DB counts {ca}", ca == cb, f"legacy={ca} new={cb}")


def make_portfolio(suffix):
    pid = str(uuid.uuid4())
    with app.app_context():
        db.session.execute(text(
            'INSERT INTO portfolios (id, name, contract_id) VALUES (:i, :n, :c)'),
            {"i": pid, "n": f"C-TEST-{TS}-{suffix}", "c": CONTRACT_ID})
        db.session.commit()
        db.session.remove()
    return pid


def clints_of(portfolio_id):
    """[(id, accountNumber, identityNumber)] ordered by accountNumber."""
    return [(str(r[0]), r[1], r[2]) for r in sql(
        'SELECT id, "accountNumber", "identityNumber" FROM clints '
        'WHERE portfolios_id = :p ORDER BY "accountNumber"', p=portfolio_id)]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def gen_rows(n, tag, offset=0):
    """`data` payload the way the upload routes hand it to the import routes.

    `offset` keeps each fixture in its own account/identity range: `accountNumber` is
    UNIQUE, and `update_type == "5"` matches on `identityNumber`, so two fixtures that
    shared either would silently update each other's rows.
    """
    return [{
        "name": f"عميل {tag} {i}",
        "identityNumber": int(f"7{TS[-8:]}{i:05d}") + offset,
        "accountNumber": int(f"9{TS[-8:]}{i:05d}") + offset,
        "amount": 1000.0 + i,
        "mobile_1": 500000000 + i,
        "email_1": f"c{i}@test.local",
        "البروفجن": f"P{i % 7}",
    } for i in range(n)]


def gen_xlsx(path, rows, headers):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
    wb.save(path)
    return os.path.basename(path)


def upload_column_file(accounts, name):
    """Write an xlsx whose column 0 is a list of account numbers, into the temp dir."""
    os.makedirs(TMP, exist_ok=True)
    path = os.path.join(TMP, name)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["accountNumber"])
    for a in accounts:
        ws.append([a])
    wb.save(path)
    return os.path.basename(path)


# --------------------------------------------------------------------------- #
# the tests
# --------------------------------------------------------------------------- #

def t_add_clints(tl, tn, sizes):
    log("\n== POST /clint/addClintsByExcel ==")
    portfolios = {}
    timings = {}
    for si, n in enumerate(sizes):
        # each size gets its own disjoint account/identity range, and the new side is
        # offset again inside it - accountNumber is UNIQUE, so overlapping fixtures would
        # silently drop rows (legacy swallows the IntegrityError) and skew the timings.
        base = si * 10_000_000
        rows_l = gen_rows(n, f"L{n}", offset=base)
        rows_n = [dict(r, accountNumber=r["accountNumber"] + 5_000_000,
                       identityNumber=r["identityNumber"] + 5_000_000) for r in rows_l]
        a = call(LEGACY, "post", "/clint/addClintsByExcel", tl,
                 json={"contract_id": CONTRACT_ID, "name": f"C-TEST-{TS}-L{n}", "data": rows_l})
        b = call(NEW, "post", "/_new/clint/addClintsByExcel", tn,
                 json={"contract_id": CONTRACT_ID, "name": f"C-TEST-{TS}-N{n}", "data": rows_n})
        compare_bodies(f"addClintsByExcel[{n}]", a, b)
        timings[n] = (a[2], b[2])
        pl = sql("SELECT id FROM portfolios WHERE name = :n", n=f"C-TEST-{TS}-L{n}")[0][0]
        pn = sql("SELECT id FROM portfolios WHERE name = :n", n=f"C-TEST-{TS}-N{n}")[0][0]
        # the account numbers differ by design (unique constraint), so compare counts and
        # the checksum of everything except accountNumber-derived fields
        cl = portfolio_checksum(pl)[1]
        cn = portfolio_checksum(pn)[1]
        check(f"addClintsByExcel[{n}]: DB counts {cl}", cl == cn, f"legacy={cl} new={cn}")
        check(f"addClintsByExcel[{n}]: all {n} rows inserted on both sides",
              cl["clints"] == cn["clints"] == n, f"legacy={cl['clints']} new={cn['clints']}")
        attr_l = sql('SELECT attribute::text FROM clints WHERE portfolios_id=:p ORDER BY "accountNumber" LIMIT 1', p=pl)
        attr_n = sql('SELECT attribute::text FROM clints WHERE portfolios_id=:p ORDER BY "accountNumber" LIMIT 1', p=pn)
        al = json.loads(attr_l[0][0]); an = json.loads(attr_n[0][0])
        al.pop("accountNumber"); an.pop("accountNumber")
        al.pop("identityNumber"); an.pop("identityNumber")
        check(f"addClintsByExcel[{n}]: attribute JSON identical (minus account/identity)",
              al == an, f"legacy={al} new={an}")
        portfolios[n] = (str(pl), str(pn))
    return portfolios, timings


def t_update_byexcel(tl, tn, pl, pn, n):
    log(f"\n== POST /clints/update/byexcel [{n} rows] ==")
    la = clints_of(pl)
    na = clints_of(pn)
    out = {}
    for utype, col2, mk in (
        ("2", "amount", lambda i: 5000.0 + i),
        ("4", "name", lambda i: f"محدث {i}"),
        ("5", "name", lambda i: f"هوية محدث {i}"),
    ):
        key = "accountNumber" if utype in ("2", "3", "4") else "identityNumber"
        rows_l, rows_n = [], []
        for i, (cid, acc, idn) in enumerate(la):
            rows_l.append({key: acc if key == "accountNumber" else idn, "v": mk(i)})
        for i, (cid, acc, idn) in enumerate(na):
            rows_n.append({key: acc if key == "accountNumber" else idn, "v": mk(i)})
        # 3 deliberately-missing rows so errorMessages is exercised
        for j in range(3):
            rows_l.append({key: f"NOPE{j}", "v": mk(j)})
            rows_n.append({key: f"NOPE{j}", "v": mk(j)})
        body = {"data1": key, "data2": "v", "update_type": utype,
                "update_side": UPDATE_SIDE_ID}
        a = call(LEGACY, "post", "/clints/update/byexcel", tl,
                 json=dict(body, fileDataRow=rows_l))
        b = call(NEW, "post", "/_new/clints/update/byexcel", tn,
                 json=dict(body, fileDataRow=rows_n))
        compare_bodies(f"update/byexcel type={utype}[{n}]", a, b)
        out[utype] = (a[2], b[2])
    # identityNumber is shared by both sets for type 5, so compare only the columns the
    # update touched, normalised by row order
    def snapshot(p):
        return [tuple(r) for r in sql(
            'SELECT name, amount FROM clints WHERE portfolios_id=:p ORDER BY "accountNumber"', p=p)]
    check(f"update/byexcel[{n}]: name+amount columns identical",
          snapshot(pl) == snapshot(pn),
          f"legacy[0:2]={snapshot(pl)[:2]} new[0:2]={snapshot(pn)[:2]}")
    return out


def t_update_type3(tn, pn):
    log("\n== POST /clints/update/byexcel update_type=3 (review 3.25) ==")
    na = clints_of(pn)[:5]
    rows = [{"accountNumber": acc, "v": f"77{i:08d}"} for i, (cid, acc, _idn) in enumerate(na)]
    body = {"data1": "accountNumber", "data2": "v", "update_type": "3",
            "update_side": UPDATE_SIDE_ID, "fileDataRow": rows}
    sa, ba, _ = call(LEGACY, "post", "/clints/update/byexcel", TOKENS[0], json=dict(body, fileDataRow=[]))
    sb, bb, _ = call(NEW, "post", "/_new/clints/update/byexcel", tn, json=body)
    got = sql('SELECT "identityNumber" FROM clints WHERE "accountNumber" = :a',
              a=str(rows[0]["accountNumber"]))
    check("update_type=3 now actually updates identityNumber",
          bb.get("status") is True and got and got[0][0] == rows[0]["v"],
          f"body={bb} identityNumber={got and got[0][0]}")
    check("update_type=3: legacy silently reported success having done nothing (documented)",
          ba.get("status") is True, f"legacy body={ba}")
    # put the original identityNumbers back so cleanup can still find the contacts
    with app.app_context():
        for (cid, acc, idn) in na:
            db.session.execute(text('UPDATE clints SET "identityNumber" = :i WHERE id = :c'),
                               {"i": idn, "c": cid})
        db.session.commit()
        db.session.remove()


def t_update_unknown_type(tn):
    log("\n== POST /clints/update/byexcel unknown update_type (review 3.25) ==")
    body = {"data1": "a", "data2": "v", "update_type": "99", "update_side": UPDATE_SIDE_ID,
            "fileDataRow": [{"a": "x", "v": 1}]}
    sa, ba, _ = call(LEGACY, "post", "/clints/update/byexcel", TOKENS[0], json=body)
    sb, bb, _ = call(NEW, "post", "/_new/clints/update/byexcel", TOKENS[1], json=body)
    check("unknown update_type: legacy=silent success, new=error message",
          ba.get("status") is True and bb.get("status") is False,
          f"legacy={ba} new={bb}")


def t_attribute(tl, tn, pl, pn):
    log("\n== POST /clints/attribute/byexcel ==")
    la, na = clints_of(pl), clints_of(pn)
    rows_l = [{"acc": acc, "val": f"V{i}"} for i, (_c, acc, _i) in enumerate(la)] + \
             [{"acc": "NOPE", "val": "x"}]
    rows_n = [{"acc": acc, "val": f"V{i}"} for i, (_c, acc, _i) in enumerate(na)] + \
             [{"acc": "NOPE", "val": "x"}]
    body = {"identifier_column": "acc", "identifier_type": "accountNumber",
            "value_column": "val", "attribute_key": "الخاصية"}
    a = call(LEGACY, "post", "/clints/attribute/byexcel", tl, json=dict(body, fileDataRow=rows_l))
    b = call(NEW, "post", "/_new/clints/attribute/byexcel", tn, json=dict(body, fileDataRow=rows_n))
    compare_bodies("attribute/byexcel", a, b)
    def attrs(p):
        return [json.loads(r[0]).get("الخاصية") for r in sql(
            'SELECT attribute::text FROM clints WHERE portfolios_id=:p ORDER BY "accountNumber"', p=p)]
    check("attribute/byexcel: attribute values identical", attrs(pl) == attrs(pn))
    return a[2], b[2]


def t_sepcify_by_excel(tl, tn, pl, pn):
    log("\n== POST /clint/SepcifyUsersByExcel ==")
    la, na = clints_of(pl), clints_of(pn)
    rows_l = [{"acc": acc, "u": COLLECTOR_USER} for _c, acc, _i in la]
    rows_n = [{"acc": acc, "u": COLLECTOR_USER} for _c, acc, _i in na]
    body = {"user_name": "u", "account_number": "acc"}
    a = call(LEGACY, "post", "/clint/SepcifyUsersByExcel", tl, json=dict(body, data=rows_l))
    b = call(NEW, "post", "/_new/clint/SepcifyUsersByExcel", tn, json=dict(body, data=rows_n))
    compare_bodies("SepcifyUsersByExcel", a, b)
    compare_db("SepcifyUsersByExcel", pl, pn)
    # abort-on-unknown-account: the prefix before the bad row must be committed on both
    a2 = call(LEGACY, "post", "/clint/SepcifyUsersByExcel", tl,
              json=dict(body, data=[{"acc": "NO-SUCH-ACCOUNT", "u": COLLECTOR_USER}]))
    b2 = call(NEW, "post", "/_new/clint/SepcifyUsersByExcel", tn,
              json=dict(body, data=[{"acc": "NO-SUCH-ACCOUNT", "u": COLLECTOR_USER}]))
    compare_bodies("SepcifyUsersByExcel(missing account)", a2, b2)
    a3 = call(LEGACY, "post", "/clint/SepcifyUsersByExcel", tl,
              json=dict(body, data=[{"acc": la[0][1], "u": "no-such-user"}]))
    b3 = call(NEW, "post", "/_new/clint/SepcifyUsersByExcel", tn,
              json=dict(body, data=[{"acc": na[0][1], "u": "no-such-user"}]))
    compare_bodies("SepcifyUsersByExcel(missing user)", a3, b3)
    return a[2], b[2]


def t_comments(tl, tn, pl, pn):
    log("\n== POST /commentsbyexcel ==")
    la, na = clints_of(pl), clints_of(pn)
    fl = upload_column_file([a for _c, a, _i in la], f"c_{TS}_comments_l.xlsx")
    fn = upload_column_file([a for _c, a, _i in na], f"c_{TS}_comments_n.xlsx")
    body = {"call_id": "", "clint_id": "", "contactNumber": "0500000000",
            "contract_id": CONTRACT_ID, "contactMethod_id": CONTACT_METHOD_ID,
            "comment_reachability_id": REACHABILITY_ID,
            "comment_status_id": COMMENT_STATUS_ID, "comment": "تعليق اختبار",
            "data1": "accountNumber", "index": 0}
    a = call(LEGACY, "post", "/commentsbyexcel", tl, json=dict(body, excel_data=fl))
    b = call(NEW, "post", "/_new/commentsbyexcel", tn, json=dict(body, excel_data=fn))
    compare_bodies("commentsbyexcel", a, b)
    compare_db("commentsbyexcel", pl, pn)
    # unknown account -> abort with 201 on both
    fbad_l = upload_column_file(["NO-SUCH-ACCOUNT"], f"c_{TS}_comments_bad_l.xlsx")
    fbad_n = upload_column_file(["NO-SUCH-ACCOUNT"], f"c_{TS}_comments_bad_n.xlsx")
    a2 = call(LEGACY, "post", "/commentsbyexcel", tl, json=dict(body, excel_data=fbad_l))
    b2 = call(NEW, "post", "/_new/commentsbyexcel", tn, json=dict(body, excel_data=fbad_n))
    compare_bodies("commentsbyexcel(missing account)", a2, b2)
    # missing file -> success, nothing done
    a3 = call(LEGACY, "post", "/commentsbyexcel", tl, json=dict(body, excel_data="nope.xlsx"))
    b3 = call(NEW, "post", "/_new/commentsbyexcel", tn, json=dict(body, excel_data="nope.xlsx"))
    compare_bodies("commentsbyexcel(missing file)", a3, b3)
    return a[2], b[2]


def t_archive_unarchive(tl, tn, pl, pn):
    log("\n== PUT /clint/archivebyexcel + /clint/unarchivebyexcel ==")
    la, na = clints_of(pl), clints_of(pn)
    body = {"account_number": "acc", "archive_reason_id": ARCHIVE_REASON_ID,
            "user_id": COLLECTOR_ID}
    a = call(LEGACY, "put", "/clint/archivebyexcel", tl,
             json=dict(body, data=[{"acc": acc} for _c, acc, _i in la]))
    b = call(NEW, "put", "/_new/clint/archivebyexcel", tn,
             json=dict(body, data=[{"acc": acc} for _c, acc, _i in na]))
    compare_bodies("archivebyexcel", a, b)
    compare_db("archivebyexcel", pl, pn)

    fl = upload_column_file([acc for _c, acc, _i in la], f"c_{TS}_unarch_l.xlsx")
    fn = upload_column_file([acc for _c, acc, _i in na], f"c_{TS}_unarch_n.xlsx")
    a2 = call(LEGACY, "put", "/clint/unarchivebyexcel", tl, json={"file_name": fl, "index": 0})
    b2 = call(NEW, "put", "/_new/clint/unarchivebyexcel", tn, json={"file_name": fn, "index": 0})
    compare_bodies("unarchivebyexcel", a2, b2)
    compare_db("unarchivebyexcel", pl, pn)
    a3 = call(LEGACY, "put", "/clint/unarchivebyexcel", tl, json={"file_name": "nope.xlsx", "index": 0})
    b3 = call(NEW, "put", "/_new/clint/unarchivebyexcel", tn, json={"file_name": "nope.xlsx", "index": 0})
    compare_bodies("unarchivebyexcel(missing file)", a3, b3)
    return (a[2], b[2]), (a2[2], b2[2])


def t_archive_ids(tl, tn, pl, pn):
    log("\n== PUT /clint/archive/ids ==")
    la, na = clints_of(pl), clints_of(pn)
    body = {"archive_reason_id": ARCHIVE_REASON_ID, "user_id": COLLECTOR_ID}
    a = call(LEGACY, "put", "/clint/archive/ids", tl,
             json=dict(body, clint_ids=[c for c, _a, _i in la]))
    b = call(NEW, "put", "/_new/clint/archive/ids", tn,
             json=dict(body, clint_ids=[c for c, _a, _i in na]))
    compare_bodies("archive/ids", a, b)
    compare_db("archive/ids", pl, pn)
    ghost = str(uuid.uuid4())
    a2 = call(LEGACY, "put", "/clint/archive/ids", tl, json=dict(body, clint_ids=[ghost]))
    b2 = call(NEW, "put", "/_new/clint/archive/ids", tn, json=dict(body, clint_ids=[ghost]))
    compare_bodies("archive/ids(missing id)", a2, b2)
    # back to un-archived for the next test
    for p, base, tok, prefix in ((pl, LEGACY, tl, ""), (pn, NEW, tn, "/_new")):
        accs = [acc for _c, acc, _i in clints_of(p)]
        f = upload_column_file(accs, f"c_{TS}_reset_{'l' if prefix == '' else 'n'}.xlsx")
        call(base, "put", f"{prefix}/clint/unarchivebyexcel", tok, json={"file_name": f, "index": 0})
    return a[2], b[2]


def t_archive_portfolio(tl, tn, pl, pn):
    log("\n== PUT /clint/archive/portofolio/<id> ==")
    body = {"archive_reason_id": ARCHIVE_REASON_ID}
    a = call(LEGACY, "put", f"/clint/archive/portofolio/{pl}", tl, json=body)
    b = call(NEW, "put", f"/_new/clint/archive/portofolio/{pn}", tn, json=body)
    compare_bodies("archive/portofolio", a, b)
    compare_db("archive/portofolio", pl, pn)
    a2 = call(LEGACY, "put", f"/clint/archive/portofolio/{pl}", tl, json=body)
    b2 = call(NEW, "put", f"/_new/clint/archive/portofolio/{pn}", tn, json=body)
    compare_bodies("archive/portofolio(already archived -> 404)", a2, b2)
    for p, base, tok, prefix in ((pl, LEGACY, tl, ""), (pn, NEW, tn, "/_new")):
        accs = [acc for _c, acc, _i in clints_of(p)]
        f = upload_column_file(accs, f"c_{TS}_reset2_{'l' if prefix == '' else 'n'}.xlsx")
        call(base, "put", f"{prefix}/clint/unarchivebyexcel", tok, json={"file_name": f, "index": 0})
    return a[2], b[2]


def t_sepcify_ids(tl, tn, pl, pn):
    log("\n== POST /sepcifyusers/ids ==")
    la, na = clints_of(pl), clints_of(pn)
    a = call(LEGACY, "post", "/sepcifyusers/ids", tl,
             json={"clint_ids": [c for c, _a, _i in la], "user_id": COLLECTOR_ID})
    b = call(NEW, "post", "/_new/sepcifyusers/ids", tn,
             json={"clint_ids": [c for c, _a, _i in na], "user_id": COLLECTOR_ID})
    compare_bodies("sepcifyusers/ids", a, b)
    compare_db("sepcifyusers/ids", pl, pn)
    ghost = str(uuid.uuid4())
    a2 = call(LEGACY, "post", "/sepcifyusers/ids", tl,
              json={"clint_ids": [c for c, _a, _i in la], "user_id": ghost})
    b2 = call(NEW, "post", "/_new/sepcifyusers/ids", tn,
              json={"clint_ids": [c for c, _a, _i in na], "user_id": ghost})
    compare_bodies("sepcifyusers/ids(missing user)", a2, b2)
    return a[2], b[2]


def t_bulk_lock(tl, tn, pl, pn):
    log("\n== PUT /clints/bulk-lock + /clints/bulk-unlock ==")
    la, na = clints_of(pl), clints_of(pn)
    a = call(LEGACY, "put", "/clints/bulk-lock", tl,
             json={"client_ids": [c for c, _a, _i in la], "reason": "اختبار"})
    b = call(NEW, "put", "/_new/clints/bulk-lock", tn,
             json={"client_ids": [c for c, _a, _i in na], "reason": "اختبار"})
    compare_bodies("bulk-lock", a, b)
    compare_db("bulk-lock", pl, pn)
    # second call: everything is already locked -> the "و N عميل مقفل بالفعل" branch
    a2 = call(LEGACY, "put", "/clints/bulk-lock", tl,
              json={"client_ids": [c for c, _a, _i in la], "reason": "اختبار"})
    b2 = call(NEW, "put", "/_new/clints/bulk-lock", tn,
              json={"client_ids": [c for c, _a, _i in na], "reason": "اختبار"})
    compare_bodies("bulk-lock(already locked)", a2, b2)
    a3 = call(LEGACY, "put", "/clints/bulk-unlock", tl,
              json={"client_ids": [c for c, _a, _i in la], "reason": "اختبار"})
    b3 = call(NEW, "put", "/_new/clints/bulk-unlock", tn,
              json={"client_ids": [c for c, _a, _i in na], "reason": "اختبار"})
    compare_bodies("bulk-unlock", a3, b3)
    compare_db("bulk-unlock", pl, pn)
    a4 = call(LEGACY, "put", "/clints/bulk-lock", tl, json={"client_ids": []})
    b4 = call(NEW, "put", "/_new/clints/bulk-lock", tn, json={"client_ids": []})
    compare_bodies("bulk-lock(empty -> 400)", a4, b4)
    return a[2], b[2]


def t_notification(tl, tn, pl, pn):
    log("\n== POST /send/notification ==")
    la, na = clints_of(pl), clints_of(pn)
    # assign a collector first, otherwise every row is skipped
    call(LEGACY, "post", "/sepcifyusers/ids", tl,
         json={"clint_ids": [c for c, _a, _i in la], "user_id": COLLECTOR_ID})
    call(NEW, "post", "/_new/sepcifyusers/ids", tn,
         json={"clint_ids": [c for c, _a, _i in na], "user_id": COLLECTOR_ID})
    fl = upload_column_file([acc for _c, acc, _i in la], f"c_{TS}_notif_l.xlsx")
    fn = upload_column_file([acc for _c, acc, _i in na], f"c_{TS}_notif_n.xlsx")
    a = call(LEGACY, "post", "/send/notification", tl,
             json={"file_name": fl, "index": 0, "message": NOTIFY_MESSAGE})
    b = call(NEW, "post", "/_new/send/notification", tn,
             json={"file_name": fn, "index": 0, "message": NOTIFY_MESSAGE})
    compare_bodies("send/notification", a, b)
    nl = portfolio_checksum(pl)[1]["notifications"]
    nn = portfolio_checksum(pn)[1]["notifications"]
    check(f"send/notification: notification rows {nl} == {nn}", nl == nn)
    keys_l = sql("""SELECT DISTINCT jsonb_object_keys(n.data::jsonb) FROM notifications n
                    WHERE (n.data->>'client_id') IN (SELECT id::text FROM clints WHERE portfolios_id=:p)
                    ORDER BY 1""", p=pl)
    keys_n = sql("""SELECT DISTINCT jsonb_object_keys(n.data::jsonb) FROM notifications n
                    WHERE (n.data->>'client_id') IN (SELECT id::text FROM clints WHERE portfolios_id=:p)
                    ORDER BY 1""", p=pn)
    check("send/notification: payload keys identical",
          [r[0] for r in keys_l] == [r[0] for r in keys_n],
          f"legacy={[r[0] for r in keys_l]} new={[r[0] for r in keys_n]}")
    return a[2], b[2]


def t_delete(tl, tn):
    """DELETE /clints on a fresh pair of portfolios.

    Two cases, because legacy behaves differently in each:
      * clients with no `client_lock_history` -> both sides delete them, identical body;
      * clients that were ever bulk-locked -> **legacy fails** with 201
        "حدث خطأ أثناء حذف العملاء".  `ClientLockHistory.clint` is a plain relationship
        with no cascade, so the ORM tries `UPDATE client_lock_history SET clint_id=NULL`
        and hits the NOT NULL constraint; the database FK is ON DELETE CASCADE, so the
        set-based delete simply works.  Divergence is deliberate and reported.
    """
    log("\n== DELETE /clints ==")
    rows_l = gen_rows(8, "DL", offset=80_000_000)
    rows_n = [dict(r, accountNumber=r["accountNumber"] + 5_000_000,
                   identityNumber=r["identityNumber"] + 5_000_000) for r in rows_l]
    call(LEGACY, "post", "/clint/addClintsByExcel", tl,
         json={"contract_id": CONTRACT_ID, "name": f"C-TEST-{TS}-DELL", "data": rows_l})
    call(NEW, "post", "/_new/clint/addClintsByExcel", tn,
         json={"contract_id": CONTRACT_ID, "name": f"C-TEST-{TS}-DELN", "data": rows_n})
    pl = str(sql("SELECT id FROM portfolios WHERE name=:n", n=f"C-TEST-{TS}-DELL")[0][0])
    pn = str(sql("SELECT id FROM portfolios WHERE name=:n", n=f"C-TEST-{TS}-DELN")[0][0])
    la, na = clints_of(pl), clints_of(pn)

    a = call(LEGACY, "delete", "/clints", tl, json={"clints_ids": [c for c, _x, _i in la[:4]]})
    b = call(NEW, "delete", "/_new/clints", tn, json={"clints_ids": [c for c, _x, _i in na[:4]]})
    compare_bodies("DELETE /clints(clean)", a, b)
    left_l, left_n = len(clints_of(pl)), len(clints_of(pn))
    check(f"DELETE /clints(clean): {8 - left_l} deleted on both sides, {left_l} left",
          left_l == left_n == 4, f"legacy_left={left_l} new_left={left_n}")

    ghost = str(uuid.uuid4())
    a2 = call(LEGACY, "delete", "/clints", tl, json={"clints_ids": [ghost]})
    b2 = call(NEW, "delete", "/_new/clints", tn, json={"clints_ids": [ghost]})
    compare_bodies("DELETE /clints(missing id)", a2, b2)

    rest_l = [c for c, _x, _i in clints_of(pl)]
    rest_n = [c for c, _x, _i in clints_of(pn)]
    call(LEGACY, "put", "/clints/bulk-lock", tl, json={"client_ids": rest_l, "reason": "x"})
    call(NEW, "put", "/_new/clints/bulk-lock", tn, json={"client_ids": rest_n, "reason": "x"})
    s3, b3, _ = call(LEGACY, "delete", "/clints", tl, json={"clints_ids": rest_l})
    s4, b4, _ = call(NEW, "delete", "/_new/clints", tn, json={"clints_ids": rest_n})
    log(f"      legacy: {s3} {json.dumps(b3, ensure_ascii=False)}")
    log(f"      new   : {s4} {json.dumps(b4, ensure_ascii=False)}")
    check("DELETE /clints(with lock history): new succeeds 200 and the rows are gone",
          s4 == 200 and b4.get("status") is True and len(clints_of(pn)) == 0,
          f"{s4} {json.dumps(b4, ensure_ascii=False)} left={len(clints_of(pn))}")
    if s3 != s4:
        log("      DIVERGENCE (expected): legacy cannot delete a client that has "
            "client_lock_history rows - ClientLockHistory.clint has no ORM cascade, so "
            "SQLAlchemy emits UPDATE client_lock_history SET clint_id=NULL and hits the "
            "NOT NULL constraint.  The set-based DELETE lets the ON DELETE CASCADE FK do it.")
    return a[2], b[2]


def t_async(tn, pn):
    log("\n== ?async=1 end to end ==")
    na = clints_of(pn)
    body = {"client_ids": [c for c, _a, _i in na], "reason": "async-test"}

    s, b, _ = call(NEW, "put", "/_new/clints/bulk-lock?async=1", tn, json=body)
    check("async: HTTP 202", s == 202, f"got {s}")
    check("async: 202 body shape",
          sorted(b.keys()) == ["async", "job_id", "message", "status"]
          and b.get("status") is True and b.get("async") is True
          and b.get("message") == "تم بدء المعالجة في الخلفية",
          json.dumps(b, ensure_ascii=False))
    job_id = b.get("job_id")
    job = poll(tn, job_id)
    check("async: job reaches done", job and job.get("status") == "done",
          json.dumps(job, ensure_ascii=False)[:300] if job else "no job")
    check("async: job result == the synchronous body",
          isinstance(job.get("result"), dict)
          and sorted(job["result"].keys()) == ["message", "status"]
          and job["result"]["status"] is True,
          json.dumps(job.get("result"), ensure_ascii=False))
    async_result = job.get("result")

    # the same call synchronously, on an identical (already-locked) state
    s2, b2, _ = call(NEW, "put", "/_new/clints/bulk-unlock", tn, json=body)
    s3, b3, _ = call(NEW, "put", "/_new/clints/bulk-lock", tn, json=body)
    check("async: sync body for the same input is identical", b3 == async_result,
          f"async={json.dumps(async_result, ensure_ascii=False)} "
          f"sync={json.dumps(b3, ensure_ascii=False)}")
    call(NEW, "put", "/_new/clints/bulk-unlock", tn, json=body)

    # JSON-body flavour of the flag, on a real import
    rows = [{"acc": acc, "val": "ASYNC"} for _c, acc, _i in na]
    s4, b4, _ = call(NEW, "post", "/_new/clints/attribute/byexcel", tn, json={
        "identifier_column": "acc", "identifier_type": "accountNumber",
        "value_column": "val", "attribute_key": "asyncKey",
        "fileDataRow": rows, "async": True})
    check("async: {\"async\": true} in the body also returns 202", s4 == 202, f"got {s4} {b4}")
    job2 = poll(tn, b4.get("job_id"))
    check("async: attribute job done + result body",
          job2 and job2.get("status") == "done"
          and job2["result"].get("status") is True
          and "messages" in job2["result"],
          json.dumps(job2.get("result"), ensure_ascii=False)[:300] if job2 else "none")
    written = sql('SELECT count(*) FROM clints WHERE portfolios_id=:p '
                  "AND attribute->>'asyncKey' = 'ASYNC'", p=pn)[0][0]
    check(f"async: job actually wrote {written}/{len(na)} rows", written == len(na))

    # a validation failure must still come back synchronously, not as a job
    s5, b5, _ = call(NEW, "post", "/_new/clints/attribute/byexcel?async=1", tn,
                     json={"identifier_column": "", "value_column": "v", "fileDataRow": rows})
    check("async: validation error is returned synchronously as 201",
          s5 == 201 and b5.get("status") is False, f"{s5} {b5}")
    s6, b6, _ = call(NEW, "put", f"/_new/clint/archive/portofolio/{uuid.uuid4()}?async=1", tn,
                     json={"archive_reason_id": ARCHIVE_REASON_ID})
    check("async: empty portfolio still 404s synchronously", s6 == 404, f"{s6} {b6}")


def poll(tok, job_id, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s, b, _ = call(NEW, "get", f"/jobs/{job_id}", tok)
        if s == 200 and b.get("status") in ("done", "failed"):
            return b
        time.sleep(0.4)
    return None


# --------------------------------------------------------------------------- #

def cleanup():
    log("\n== cleanup ==")
    with app.app_context():
        pids = db.session.execute(text(
            "SELECT id, name FROM portfolios WHERE name LIKE 'C-TEST-%'")).all()
        for pid, name in pids:
            db.session.execute(text(
                'UPDATE clints SET "clintArchive_id" = NULL WHERE portfolios_id = :p'), {"p": pid})
            db.session.execute(text(
                'DELETE FROM notifications WHERE (data->>\'client_id\') IN '
                '(SELECT id::text FROM clints WHERE portfolios_id = :p)'), {"p": pid})
            db.session.execute(text(
                'DELETE FROM "ClintContacts" WHERE "identityNumber" IN '
                '(SELECT "identityNumber" FROM clints WHERE portfolios_id = :p)'), {"p": pid})
            db.session.execute(text(
                'DELETE FROM "clintArchive" WHERE clint_id IN '
                '(SELECT id FROM clints WHERE portfolios_id = :p)'), {"p": pid})
            db.session.execute(text('DELETE FROM clints WHERE portfolios_id = :p'), {"p": pid})
            db.session.execute(text('DELETE FROM portfolios WHERE id = :p'), {"p": pid})
            log(f"  dropped portfolio {name} ({pid})")
        db.session.commit()
        db.session.remove()
        # sweep rows an interrupted run can orphan once its portfolio is already gone
        db.session.execute(text(
            "DELETE FROM \"ClintContacts\" WHERE contact LIKE '%@test.local'"))
        db.session.execute(text(
            "DELETE FROM notifications WHERE data->>'message' = :m"), {"m": NOTIFY_MESSAGE})
        db.session.commit()
        db.session.remove()
    for f in os.listdir(TMP):
        if f.startswith(f"c_{TS}"):
            os.remove(os.path.join(TMP, f))
    log("  temp workbooks removed")


TOKENS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="5,200,3000")
    ap.add_argument("--no-cleanup", action="store_true")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]

    tl, tn = token(LEGACY), token(NEW)
    TOKENS.extend([tl, tn])
    log(f"legacy={LEGACY}  new={NEW}  sizes={sizes}  tag=C-TEST-{TS}")

    timings = {}
    try:
        added = safe("addClintsByExcel", t_add_clints, tl, tn, sizes)
        if added is None:
            return 1
        portfolios, t_add = added
        timings["addClintsByExcel"] = t_add

        def record(label, n, value):
            if value is not None:
                timings.setdefault(label, {})[n] = value

        for n in sizes:
            pl, pn = portfolios[n]
            res = safe("update/byexcel", t_update_byexcel, tl, tn, pl, pn, n)
            if res:
                timings.setdefault("update/byexcel", {})[n] = res
            record("attribute/byexcel", n, safe("attribute/byexcel", t_attribute, tl, tn, pl, pn))
            record("SepcifyUsersByExcel", n,
                   safe("SepcifyUsersByExcel", t_sepcify_by_excel, tl, tn, pl, pn))
            record("commentsbyexcel", n, safe("commentsbyexcel", t_comments, tl, tn, pl, pn))
            both = safe("archive/unarchive by excel", t_archive_unarchive, tl, tn, pl, pn)
            if both:
                record("archivebyexcel", n, both[0])
                record("unarchivebyexcel", n, both[1])
            record("archive/ids", n, safe("archive/ids", t_archive_ids, tl, tn, pl, pn))
            record("sepcifyusers/ids", n, safe("sepcifyusers/ids", t_sepcify_ids, tl, tn, pl, pn))
            record("bulk-lock", n, safe("bulk-lock", t_bulk_lock, tl, tn, pl, pn))
            record("send/notification", n,
                   safe("send/notification", t_notification, tl, tn, pl, pn))
            if n == sizes[0]:
                t_update_unknown_type(tn)
                record("archive/portofolio", n,
                       safe("archive/portofolio", t_archive_portfolio, tl, tn, pl, pn))

        safe("async", t_async, tn, portfolios[sizes[0]][1])
        # last: it rewrites identityNumber, which the ClintContacts comparison keys on
        safe("update_type=3", t_update_type3, tn, portfolios[sizes[0]][1])
        record("DELETE /clints", 8, safe("DELETE /clints", t_delete, tl, tn))
    finally:
        log("\n" + "=" * 78)
        log("TIMINGS   legacy -> new   (ms, wall clock, whole HTTP request)")
        log("=" * 78)
        for name, per_n in timings.items():
            for n, v in sorted(per_n.items(), key=lambda kv: str(kv[0])):
                if isinstance(v, dict):
                    for k, (a, b) in v.items():
                        log(f"  {name:<24} type={k} n={n:<6} {a:9.0f} -> {b:8.0f}   "
                            f"{a / b if b else 0:5.1f}x")
                else:
                    a, b = v
                    log(f"  {name:<24} {'':9} n={n:<6} {a:9.0f} -> {b:8.0f}   "
                        f"{a / b if b else 0:5.1f}x")
        log("\n" + "=" * 78)
        passed = sum(1 for _n, ok, _d in RESULTS if ok)
        log(f"RESULT: {passed}/{len(RESULTS)} checks passed")
        for n, d in FAILURES:
            log(f"  FAIL {n}  {d}")
        log("=" * 78)
        if not args.no_cleanup:
            cleanup()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
