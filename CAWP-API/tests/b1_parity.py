#!/usr/bin/env python3
"""B1 parity harness: legacy routes on :9797 vs the SQL rewrites on :9801 (`/_new/...`).

Run it from inside the API container (9801 is not published):

    docker exec -i cawp-api python /app/tests/b1_parity.py
    docker exec -i cawp-api python /app/tests/b1_parity.py --exports
    docker exec -i cawp-api python /app/tests/b1_parity.py --scope     # collector identity

Prints per request: HTTP status old->new, ms old->new, envelope keys equal, countRow
equal, first-row shape equal, page-1 id set equal.

Exports are compared **streamed**: the bodies reach 350 MB, so neither side is ever held
in memory -- rows are counted by scanning the chunks for the top-level `{"id":"<uuid>",`
marker and the first/last rows are recovered from a small rolling window.

Caveats that are not bugs:
  * the paged old/new calls run back to back, but sibling agents are importing clients into
    the same database, so `countRow` can legitimately drift by a few rows between the two
    requests. A difference of <1% on a 170k-row count is drift, not a parity failure.
  * the legacy `/clints/*/export` routes read the `clint:*` Redis keyspace, which is empty
    in this restored database, so they return 0 rows. The new ones return the real result
    set. That difference is the point of the rewrite, and is reported as `SCOPE/REDIS`.
"""
import argparse
import gzip
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

OLD = "http://localhost:9797"
NEW = "http://localhost:9801"
NEW_PREFIX = "/_new"

ADMIN = ("aidi.dev", "Aidi@2026")
COLLECTOR = ("aidi.collector", "Aidi@2026")

CONTRACT = "6f85326f-9dd7-46c1-8c85-bdfb86cc0c8c"
COLLECTOR_ID = "992c42ad-72aa-4f8c-9d89-c96e694cd38f"
PG = "page=1&pageSize=100"
DATES = "customeFilterDateValue[]=2026-01-01&customeFilterDateValue[]=2027-12-31"

ROW_RE = re.compile(rb'\{"id":"[0-9a-fA-F-]{36}",')


class TopLevelRowCounter(object):
    """Counts `{...}` objects directly inside the export's row array, streaming.

    Counting `{"id":` occurrences is wrong: it also matches the nested `portfolios` /
    `contract` / `user` objects, and the legacy Redis-cached row has a different number
    of them than the SQL row, so the two sides are not comparable that way.
    """

    def __init__(self):
        self.n = 0
        self.depth = 0
        self.in_array = False
        self.done = False
        self.instr = False
        self.esc = False

    def feed(self, chunk):
        for b in chunk:
            c = chr(b)
            if self.done:
                return
            if self.instr:
                if self.esc:
                    self.esc = False
                elif c == "\\":
                    self.esc = True
                elif c == '"':
                    self.instr = False
                continue
            if c == '"':
                self.instr = True
            elif not self.in_array:
                if c == "[":
                    self.in_array = True
            elif c == "{":
                self.depth += 1
                if self.depth == 1:
                    self.n += 1
            elif c == "}":
                self.depth -= 1
            elif c == "]" and self.depth == 0:
                self.done = True


# --------------------------------------------------------------------------- http

def login(base, creds):
    req = urllib.request.Request(
        base + "/login",
        data=json.dumps({"username": creds[0], "password": creds[1]}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["access_token"]


def _open(base, path, token, timeout, gzip_ok):
    headers = {"Authorization": "Bearer " + token,
               "Accept-Encoding": "gzip" if gzip_ok else "identity"}
    return urllib.request.urlopen(
        urllib.request.Request(base + path, headers=headers), timeout=timeout)


def get_json(base, path, token, timeout=600):
    t = time.perf_counter()
    try:
        with _open(base, path, token, timeout, gzip_ok=False) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    except Exception as e:
        return {"status": "ERR", "ms": int((time.perf_counter() - t) * 1000),
                "err": str(e)[:120]}
    ms = int((time.perf_counter() - t) * 1000)
    try:
        d = json.loads(body)
    except Exception:
        return {"status": status, "ms": ms, "err": "not json", "bytes": len(body)}
    rec = {"status": status, "ms": ms, "bytes": len(body), "keys": sorted(d.keys()),
           "countRow": d.get("countRow"), "msg": d.get("message")}
    lists = [(k, v) for k, v in d.items() if isinstance(v, list)]
    if lists:
        k, v = lists[0]
        rec["list_key"] = k
        rec["n"] = len(v)
        rec["shape"] = shape(v[0]) if v else None
        rec["ids"] = set(str(x.get("id")) for x in v if isinstance(x, dict))
    return rec


def stream_export(base, path, token, timeout=900):
    """Count rows + keep the first/last 3 without ever holding the body in memory."""
    t = time.perf_counter()
    try:
        r = _open(base, path, token, timeout, gzip_ok=True)
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.perf_counter() - t) * 1000),
                "err": e.read()[:120].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": "ERR", "ms": int((time.perf_counter() - t) * 1000),
                "err": str(e)[:120]}

    raw = r
    if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.GzipFile(fileobj=io.BufferedReader(r))  # incremental, not .read()

    counter = TopLevelRowCounter()
    total = 0
    head = b""
    tail = b""
    try:
        while True:
            chunk = raw.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            counter.feed(chunk)
            if len(head) < 8192:
                head += chunk[:8192]
            tail = (tail + chunk)[-8192:]
        n = counter.n
    except Exception as e:
        return {"status": r.status, "ms": int((time.perf_counter() - t) * 1000),
                "err": "stream broke: %s" % str(e)[:80],
                "rows": counter.n, "bytes": total}
    finally:
        r.close()

    ms = int((time.perf_counter() - t) * 1000)
    rec = {"status": r.status, "ms": ms, "rows": n, "bytes": total,
           "gzip": (r.headers.get("Content-Encoding") or "-")}
    rec["envelope"] = head[:40].decode("utf-8", "replace")
    rec["tail"] = tail[-70:].decode("utf-8", "replace")
    rec["first_shapes"] = [shape(o) for o in _rows_from(head)[:3]]
    rec["last_shapes"] = [shape(o) for o in _rows_from(tail)[-3:]]
    return rec


def _rows_from(blob):
    """Best-effort: pull whole `{...}` top-level row objects out of a byte window."""
    out = []
    for m in ROW_RE.finditer(blob):
        start = m.start()
        depth, i, instr, esc = 0, start, False, False
        while i < len(blob):
            c = blob[i:i + 1]
            if instr:
                if esc:
                    esc = False
                elif c == b"\\":
                    esc = True
                elif c == b'"':
                    instr = False
            elif c == b'"':
                instr = True
            elif c == b"{":
                depth += 1
            elif c == b"}":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(blob[start:i + 1].decode("utf-8")))
                    except Exception:
                        pass
                    break
            i += 1
    return out


def shape(v, depth=0):
    if isinstance(v, dict):
        return {k: shape(v[k], depth + 1) for k in sorted(v)} if depth < 3 else "dict"
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    if v is None:
        return "null"
    return type(v).__name__


# --------------------------------------------------------------------------- report

def verdict(o, n, note=""):
    def eq(key, fmt=str):
        a, b = o.get(key), n.get(key)
        if a is None and b is None:
            return "-"
        return "YES" if a == b else "no(%s->%s)" % (fmt(a)[:22], fmt(b)[:22])

    ids_v = "-"
    if o.get("ids") is not None and n.get("ids") is not None:
        if o["ids"] == n["ids"]:
            ids_v = "YES"
        else:
            ids_v = "no(%d|%d shared=%d)" % (
                len(o["ids"]), len(n["ids"]), len(o["ids"] & n["ids"]))
    count_v = eq("countRow")
    if count_v.startswith("no") and isinstance(o.get("countRow"), int) \
            and isinstance(n.get("countRow"), int) and o["countRow"]:
        drift = abs(o["countRow"] - n["countRow"]) / float(o["countRow"])
        if drift < 0.01:
            count_v += "~drift"
    return ("  status %s->%s  ms %s->%s  keys=%s countRow=%s n=%s->%s shape=%s ids=%s %s"
            % (o.get("status"), n.get("status"), o.get("ms"), n.get("ms"),
               eq("keys", lambda x: ",".join(x or [])), count_v,
               o.get("n"), n.get("n"), eq("shape", json.dumps), ids_v, note))


PAGED = [
    # NB: the default order is `create_at DESC`, and sibling agents are importing clients
    # into the same database while this runs, so the *default-sorted* page 1 is a moving
    # target. The deterministic variants (`field=...&sort=...`) are the meaningful id
    # comparison; a default-sorted `ids=no` next to a `countRow` that grew is import drift.
    ("/clints", [PG,
                 PG + "&field=create_at&sort=asc",
                 PG + "&field=identityNumber&sort=asc",
                 PG + "&field=amount&sort=desc",
                 PG + "&field=user_.frist_name&sort=asc",
                 "page=1&pageSize=50&customeFilterContractValue[]=" + CONTRACT,
                 "page=1&pageSize=50&filterOperator=contains&filterField=name"
                 "&filterFieldType=string&filterValue=%D9%85%D8%AD%D9%85%D8%AF"]),
    ("/clints/archive/", [PG,
                          PG + "&sort[key]=archive_create_at&sort[order]=desc",
                          PG + "&sort[key]=clint_amount&sort[order]=desc",
                          "page=1&pageSize=50&selectedUsers_Qkey[]=" + COLLECTOR_ID]),
    ("/clints/have_payments", [PG,
                               PG + "&field=create_at&sort=asc",
                               PG + "&field=amount&sort=desc",
                               PG + "&customeFilterContractValue[]=" + CONTRACT,
                               PG + "&filterOperator=>&filterField=payments"
                                    "&filterFieldType=number&filterValue=1"]),
    ("/clints/not_have_payments", [PG,
                                   PG + "&field=create_at&sort=asc",
                                   PG + "&field=amount&sort=desc",
                                   PG + "&customeFilterContractValue[]=" + CONTRACT,
                                   "page=1&pageSize=700"]),          # pageSize clamp
    ("/clints/neglect", [PG,
                         PG + "&field=neglect_days&sort=desc",
                         PG + "&customeFilterContractValue[]=" + CONTRACT]),
    ("/clints/currentUser/have_nextCalls", [PG, PG + "&" + DATES]),
    ("/clints/user/" + COLLECTOR_ID, ["page=1&pageSize=20",
                                      "page=1&pageSize=20&field=amount&sort=desc"]),
]

EXPORTS = [
    # narrowed so the legacy side cannot OOM the reference server (review, foundation §10)
    ("/clients/export", "customeFilterContractValue[]=" + CONTRACT),
    ("/clients/archive/export", "selectedUsers_Qkey[]=" + COLLECTOR_ID),
    ("/clints/have_payments/export", "customeFilterContractValue[]=" + CONTRACT),
    ("/clints/not_have_payments/export", "customeFilterContractValue[]=" + CONTRACT),
    ("/clints/neglect/export", "customeFilterContractValue[]=" + CONTRACT),
    ("/clints/currentUser/have_nextCalls/export", DATES),
]

SCOPE_ROUTES = [
    "/clints/have_payments?" + PG,
    "/clints/not_have_payments?" + PG,
    "/clints/neglect?" + PG,
    "/clints/currentUser/have_nextCalls?" + PG,
    "/clints/user/" + COLLECTOR_ID + "?page=1&pageSize=10",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exports", action="store_true", help="also diff the export twins")
    ap.add_argument("--scope", action="store_true",
                    help="also run the collector identity (scope) checks")
    ap.add_argument("--only", default="")
    a = ap.parse_args()

    told, tnew = login(OLD, ADMIN), login(NEW, ADMIN)
    print("logged in on both ports as %s\n" % ADMIN[0])

    print("=" * 100)
    print("PAGED ROUTES   old %s   vs   new %s%s" % (OLD, NEW, NEW_PREFIX))
    print("=" * 100)
    for path, variants in PAGED:
        if a.only and a.only not in path:
            continue
        print("\n" + path)
        for qs in variants:
            o = get_json(OLD, path + "?" + qs, told)
            n = get_json(NEW, NEW_PREFIX + path + "?" + qs, tnew)
            note = ""
            if o.get("status") == 201 and n.get("status") == 200:
                note = "<< legacy errored (201), new returns a valid result"
            print("  ?" + qs[:96])
            print(verdict(o, n, note))

    if a.exports:
        print("\n" + "=" * 100)
        print("EXPORTS (streamed on both sides, narrowed so :9797 cannot OOM)")
        print("=" * 100)
        for path, qs in EXPORTS:
            if a.only and a.only not in path:
                continue
            o = stream_export(OLD, path + "?" + qs, told)
            n = stream_export(NEW, NEW_PREFIX + path + "?" + qs, tnew)
            print("\n%s?%s" % (path, qs[:80]))
            print("  old: status=%s ms=%s rows=%s bytes=%s %s"
                  % (o.get("status"), o.get("ms"), o.get("rows"), o.get("bytes"),
                     o.get("err", "")))
            print("  new: status=%s ms=%s rows=%s bytes=%s enc=%s %s"
                  % (n.get("status"), n.get("ms"), n.get("rows"), n.get("bytes"),
                     n.get("gzip"), n.get("err", "")))
            print("  new envelope: %r ... %r" % (n.get("envelope"), n.get("tail")))
            same_rows = "YES" if o.get("rows") == n.get("rows") else \
                "no (%s vs %s)%s" % (o.get("rows"), n.get("rows"),
                                     "  [SCOPE/REDIS: legacy read an empty Redis keyspace]"
                                     if o.get("rows") == 0 and n.get("rows") else "")
            print("  rows equal: %s" % same_rows)
            if o.get("first_shapes") and n.get("first_shapes"):
                print("  first-row shape equal: %s" %
                      ("YES" if o["first_shapes"][:1] == n["first_shapes"][:1] else "no"))
                print("  last-row shape equal:  %s" %
                      ("YES" if o["last_shapes"][-1:] == n["last_shapes"][-1:] else "no"))

    if a.scope:
        print("\n" + "=" * 100)
        print("SCOPE -- same routes as the collector %s (legacy have/not_have were "
              "UNSCOPED)" % COLLECTOR[0])
        print("=" * 100)
        try:
            cold, cnew = login(OLD, COLLECTOR), login(NEW, COLLECTOR)
        except Exception as e:
            print("  collector login failed: %s" % e)
            return
        for path in SCOPE_ROUTES:
            o = get_json(OLD, path, cold)
            n = get_json(NEW, NEW_PREFIX + path, cnew)
            print("\n%s" % path[:96])
            print("  old countRow=%-8s n=%-5s status=%s" %
                  (o.get("countRow"), o.get("n"), o.get("status")))
            print("  new countRow=%-8s n=%-5s status=%s" %
                  (n.get("countRow"), n.get("n"), n.get("status")))


if __name__ == "__main__":
    sys.exit(main())
