#!/usr/bin/env python3
"""Old (:9797, legacy handlers in app.py) vs new (:9802, /_new blueprints) parity.

    docker exec cawp-api python /app/tests/b2_parity.py
    docker exec cawp-api python /app/tests/b2_parity.py --only comments

For every route it prints: HTTP status on both sides, wall time old -> new, and whether
the envelope keys, `countRow`, `totalAmount`, the row count, the recursive first-row key
shape and the page's id set agree. Dashboards compare key sets and list lengths only.

stdlib only, runs inside the API container (:9802 is not published).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

OLD = "http://localhost:9797"
NEW = "http://localhost:9802"
NEW_PREFIX = "/_new"

ADMIN = ("aidi.dev", "Aidi@2026")
COLLECTOR = ("aidi.collector2", "Aidi@2026")   # created by this pass; see the B2 report

CLIENT = "c087a00c-a43b-41b6-babb-6895cda9b843"
D0, D1 = "2026-09-09", "2026-09-15"
DATE = "customeFilterDateValue[]=%s&customeFilterDateValue[]=%s" % (D0, D1)
MOVE = "customeFilterMoveDateValue[]=%s&customeFilterMoveDateValue[]=%s" % (D0, D1)
PG = "page=1&pageSize=100"

# (label, path+query, which token: "admin" | "collector")
CASES = [
    # ---------------- comments -------------------------------------------------
    ("comments/base",        "/comments?%s&%s" % (PG, DATE), "admin"),
    ("comments/sort",        "/comments?%s&%s&field=clint_.amount&sort=desc" % (PG, DATE), "admin"),
    ("comments/sort-user",   "/comments?%s&%s&field=user_.name&sort=asc" % (PG, DATE), "admin"),
    ("comments/mui-contains", "/comments?%s&%s&filterOperator=contains&filterField=comment"
                              "&filterFieldType=string&filterValue=%%D9%%85%%D8%%BA%%D9%%84%%D9%%82" % (PG, DATE), "admin"),
    ("comments/qkey-contract", "/comments?%s&%s&selectedContracts_Qkey[]=6f85326f-9dd7-46c1-8c85-bdfb86cc0c8c"
                               "&selectedContracts_Jkey=portfolios.contract.id&selectedContracts_Type=string" % (PG, DATE), "admin"),
    ("comments/qkey-user",   "/comments?%s&%s&selectedUsers_Qkey[]=992c42ad-72aa-4f8c-9d89-c96e694cd38f"
                             "&selectedUsers_Jkey=user_id&selectedUsers_Type=string" % (PG, DATE), "admin"),
    ("comments/movedate-too", "/comments?%s&%s&%s" % (PG, DATE, MOVE), "admin"),
    ("comments/clint",       "/comments/clint/%s" % CLIENT, "admin"),
    ("comments/current",     "/comments/current_user?%s&%s" % (PG, DATE), "collector"),
    ("comments/current/sort", "/comments/current_user?%s&%s&field=create_at&sort=asc" % (PG, DATE), "collector"),

    # ---------------- exports (1-day window so the bodies stay comparable) -----
    ("comments/export",      "/comments/export?customeFilterDateValue[]=%s&customeFilterDateValue[]=%s" % (D1, D1), "admin"),
    ("comments/export+move", "/comments/export?customeFilterDateValue[]=%s&customeFilterDateValue[]=%s&%s" % (D1, D1, MOVE), "admin"),
    ("comments/current/export", "/comments/current_user/export?%s" % DATE, "collector"),

    # ---------------- payments -------------------------------------------------
    ("payments/base",        "/payments?%s&%s" % (PG, MOVE), "admin"),
    ("payments/sort-amount", "/payments?%s&%s&field=amount&sort=desc" % (PG, MOVE), "admin"),
    ("payments/sort-clint",  "/payments?%s&%s&field=clint_.name&sort=asc" % (PG, MOVE), "admin"),
    ("payments/mui-gt",      "/payments?%s&%s&filterOperator=>&filterField=amount"
                             "&filterFieldType=number&filterValue=1000" % (PG, MOVE), "admin"),
    ("payments/qkey-acct",   "/payments?%s&%s&selectedAccountNumber_Qkey=1220401916"
                             "&selectedAccountNumber_Jkey=clint.accountNumber&selectedAccountNumber_Type=string" % (PG, MOVE), "admin"),
    ("payments/export",      "/payments/export?%s" % MOVE, "admin"),
    ("payments/user",        "/payments/user?%s&%s" % (PG, MOVE), "collector"),
    ("payments/user/export", "/payments/user/export?%s" % MOVE, "collector"),
    ("payments/approval",    "/payments/approval_status?%s&%s" % (PG, MOVE), "admin"),
    ("payments/approval/sort", "/payments/approval_status?%s&%s&field=payment_date&sort=asc" % (PG, MOVE), "admin"),
    ("payments/approval/export", "/payments/approval_status/export?%s" % MOVE, "admin"),
    ("payments/clint",       "/payments/clint/%s" % CLIENT, "admin"),
    ("payments/clint/total", "/payments/clint/%s/total" % CLIENT, "admin"),

    # ---------------- promise-to-pay ------------------------------------------
    ("payment_date/base",    "/payment_date?%s&%s" % (PG, DATE), "admin"),
    ("payment_date/sort",    "/payment_date?%s&%s&field=amount&sort=desc" % (PG, DATE), "admin"),
    ("payment_date/sort-cs", "/payment_date?%s&%s&field=comment_status_.name&sort=asc" % (PG, DATE), "admin"),
    ("payment_date/mui-date", "/payment_date?%s&%s&filterOperator=onOrAfter&filterField=payment_date"
                              "&filterFieldType=date&filterValue=2026-09-12" % (PG, DATE), "admin"),
    ("payment_date/qkey",    "/payment_date?%s&%s&selectedContracts_Qkey[]=6f85326f-9dd7-46c1-8c85-bdfb86cc0c8c"
                             "&selectedContracts_Jkey=portfolios.contract.id&selectedContracts_Type=string" % (PG, DATE), "admin"),
    ("payment_date/export",  "/payment_date/export?%s" % DATE, "admin"),
    ("payment_date/current", "/payment_date/currentUser?%s&%s" % (PG, DATE), "collector"),
    ("payment_date/current/export", "/payment_date/currentUser/export?%s" % DATE, "collector"),
    ("payment_date/user",    "/payment_date/user", "collector"),
    ("payment_date/clint",   "/payment_date/clint/%s" % CLIENT, "admin"),

    # ---------------- small N+1 fixes -----------------------------------------
    ("contracts",            "/contracts", "admin"),
    ("contracts/sort",       "/contracts?field=count_clints&sort=desc", "admin"),
    ("contracts/archived",   "/contracts?customeFilterArchivingStatusesValue[]=true", "admin"),
    ("supervisors",          "/supervisors", "admin"),
    ("comment_status",       "/comment_status", "admin"),
    ("comment_status/page",  "/comment_status?page=1&pageSize=10", "admin"),
    ("uesrs/export",         "/uesrs/export", "admin"),

    # ---------------- dashboards ----------------------------------------------
    ("dash/user",            "/dashbord/user", "collector"),
    ("dash/collector/v2",    "/dashbord/collector/v2", "collector"),
    ("dash/dailyPayments",   "/dashbord/admin/dailyPayments/v2", "admin"),
    ("dash/dailyPaymentDates", "/dashbord/admin/dailyPaymentDates/v2", "admin"),
    ("dash/Allcollector",    "/dashbord/Allcollector/v2", "admin"),
    ("dash/commentsReport",  "/dashbord/commentsReport/v2", "admin"),
    ("dashboard/v1",         "/dashboard?startDate=1757376000&endDate=1757894400", "admin"),
]

DASHBOARDS = {"dash/user", "dash/collector/v2", "dash/dailyPayments", "dash/dailyPaymentDates",
              "dash/Allcollector", "dash/commentsReport", "dashboard/v1"}


def login(base, creds):
    body = json.dumps({"username": creds[0], "password": creds[1]}).encode()
    req = urllib.request.Request(base + "/login", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["access_token"]


def fetch(base, path, token, timeout=600):
    req = urllib.request.Request(base + path, headers={
        "Authorization": "Bearer " + token, "Accept-Encoding": "identity"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:
        return {"status": "ERR", "ms": int((time.perf_counter() - t) * 1000), "err": str(e)[:120]}
    ms = int((time.perf_counter() - t) * 1000)
    out = {"status": status, "ms": ms, "bytes": len(raw)}
    try:
        d = json.loads(raw)
    except Exception as e:
        out["err"] = "bad json: %s" % str(e)[:80]
        return out
    out["body"] = d
    if isinstance(d, dict):
        out["keys"] = sorted(d)
        out["countRow"] = d.get("countRow")
        out["totalAmount"] = d.get("totalAmount")
        lists = [(k, v) for k, v in d.items() if isinstance(v, list)]
        out["lists"] = {k: len(v) for k, v in lists}
        if lists:
            k, v = lists[0]
            out["list_key"] = k
            out["n"] = len(v)
            out["shape"] = shape(v[0]) if v else None
            out["ids"] = sorted(str(x.get("id")) for x in v if isinstance(x, dict) and "id" in x)
    return out


# `Clints.attribute` is free-form JSONB built from arbitrary Excel headers, so its key
# set is per-portfolio data, not API shape; comparing it would flag every row whose first
# client happens to come from a different portfolio.
OPAQUE = {"attribute"}


def shape(v, depth=0):
    if isinstance(v, dict):
        return ({k: ("opaque" if k in OPAQUE else shape(v[k], depth + 1)) for k in sorted(v)}
                if depth < 3 else "dict")
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    if v is None:
        return "null"
    return type(v).__name__


def diff_shape(a, b, path=""):
    """Report only *missing* / *added* keys and type flips, recursively."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in b:
                out.append("-%s%s" % (path, k))
            elif k not in a:
                out.append("+%s%s" % (path, k))
            else:
                out += diff_shape(a[k], b[k], path + k + ".")
    elif isinstance(a, list) and isinstance(b, list):
        if a and b:
            out += diff_shape(a[0], b[0], path + "[].")
        elif bool(a) != bool(b):
            out.append("~%s(list %s vs %s)" % (path, len(a), len(b)))
    elif type(a) is not type(b) or a != b:
        # `null` on one side only is a value difference, not a shape one
        if a != "null" and b != "null":
            out.append("~%s(%s->%s)" % (path.rstrip("."), _t(a), _t(b)))
    return out


def _t(v):
    if isinstance(v, dict):
        return "{%s}" % ",".join(sorted(v)[:6])
    if isinstance(v, list):
        return "[]"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-recheck", action="store_true",
                    help="skip the second read of the old side (faster, but a concurrent "
                         "writer can then show up as an id-set difference)")
    ap.add_argument("--old", default=OLD)
    ap.add_argument("--new", default=NEW)
    a = ap.parse_args()

    tokens = {}
    for name, creds in (("admin", ADMIN), ("collector", COLLECTOR)):
        try:
            tokens[name] = (login(a.old, creds), login(a.new, creds))
        except Exception as e:
            print("!! cannot log in as %s (%s) - those cases will be skipped" % (name, e))
            tokens[name] = None

    print("%-30s %-11s %-18s %s" % ("case", "status o/n", "ms  old -> new", "result"))
    print("-" * 110)
    ok = warn = bad = 0
    for label, path, who in CASES:
        if a.only and a.only not in label:
            continue
        tok = tokens.get(who)
        if tok is None:
            print("%-30s %-11s %-18s SKIP (no %s token)" % (label, "-", "-", who))
            continue
        o = fetch(a.old, path, tok[0])
        n = fetch(a.new, NEW_PREFIX + path, tok[1])
        # The database is live (a sibling agent imports rows while this runs). Re-read the
        # old side afterwards: if it moved, row-identity comparisons are meaningless for
        # this case and are reported as VOLATILE instead of a false failure.
        o2 = fetch(a.old, path, tok[0]) if not a.no_recheck else o
        volatile = (o.get("countRow") != o2.get("countRow")
                    or o.get("lists") != o2.get("lists")
                    or (o.get("ids") is not None and o.get("ids") != o2.get("ids")))

        notes = []
        if o.get("status") != n.get("status"):
            notes.append("STATUS %s->%s" % (o.get("status"), n.get("status")))
        if o.get("err") or n.get("err"):
            notes.append("err old=%s new=%s" % (o.get("err"), n.get("err")))

        if "body" in o and "body" in n:
            if o.get("keys") != n.get("keys"):
                notes.append("keys %s -> %s" % (o.get("keys"), n.get("keys")))
            if label in DASHBOARDS:
                if o.get("lists") != n.get("lists") and not volatile:
                    notes.append("lists %s -> %s" % (o.get("lists"), n.get("lists")))
            elif volatile:
                sd = diff_shape(o.get("shape"), n.get("shape"))
                if sd:
                    notes.append("shape %s" % (sd[:8],))
            else:
                if o.get("countRow") != n.get("countRow"):
                    notes.append("countRow %s -> %s" % (o.get("countRow"), n.get("countRow")))
                if o.get("n") != n.get("n"):
                    notes.append("n_rows %s -> %s" % (o.get("n"), n.get("n")))
                ta_o, ta_n = o.get("totalAmount"), n.get("totalAmount")
                if ta_o is not None or ta_n is not None:
                    if ta_o is None or ta_n is None or abs(float(ta_o) - float(ta_n)) > 0.5:
                        notes.append("totalAmount %s -> %s" % (ta_o, ta_n))
                sd = diff_shape(o.get("shape"), n.get("shape"))
                if sd:
                    notes.append("shape %s" % (sd[:8],))
                if o.get("ids") and n.get("ids") and set(o["ids"]) != set(n["ids"]):
                    d = len(set(o["ids"]) ^ set(n["ids"]))
                    notes.append("ids %d/%d differ" % (d, max(len(o["ids"]), len(n["ids"]))))

        if not notes:
            res, ok = ("OK (volatile: rows changed mid-test, id set not compared)"
                       if volatile else "OK"), ok + 1
        elif all(x.startswith(("ids", "shape")) for x in notes):
            res, warn = "WARN " + " | ".join(notes), warn + 1
        else:
            res, bad = "DIFF " + " | ".join(notes), bad + 1

        print("%-30s %-11s %-18s %s" % (
            label, "%s/%s" % (o.get("status"), n.get("status")),
            "%6d -> %-6d" % (o.get("ms", 0), n.get("ms", 0)), res[:400]))

    print("-" * 110)
    print("OK=%d  WARN=%d  DIFF=%d" % (ok, warn, bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
