#!/usr/bin/env python3
"""Capture / compare page-1 bodies of the three client grids across the
LATERAL -> clints.last_comment_at switch."""
import json, sys, time, urllib.request, urllib.error

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9798"
CS = "dabf3179-e4e7-4a6b-8675-d15de65f5632"

def cases(cs):
    PG = "page=1&pageSize=100"
    return {
        "clints/default":      f"/clints?{PG}",
        "clints/comment-desc": f"/clints?{PG}&field=comment.create_at&sort=desc",
        "clints/cs-filter":    f"/clints?{PG}&customeFilterCommentStatusValue[]={cs}",
        "neglect/default":     f"/clints/neglect?{PG}",
        "neglect/comment-desc":f"/clints/neglect?{PG}&field=comment.create_at&sort=desc",
        "neglect/neglect-days":f"/clints/neglect?{PG}&field=neglect_days&sort=desc",
        "neglect/cs-filter":   f"/clints/neglect?{PG}&customeFilterCommentStatusValue[]={cs}",
        "archive/default":     f"/clints/archive/?{PG}",
        "archive/cs-filter":   f"/clints/archive/?{PG}&SelectedCommentStatus_Qkey[]={cs}",
        "archive/sort-create": f"/clints/archive/?{PG}&sort[key]=archive_create_at&sort[order]=desc",
    }

def login(base):
    req = urllib.request.Request(base + "/login",
        data=json.dumps({"username": "aidi.dev", "password": "Aidi@2026"}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))["access_token"]

def get(base, ep, tok):
    req = urllib.request.Request(base + ep, headers={"Authorization": "Bearer " + tok,
                                                     "Accept-Encoding": "identity"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, st = e.read(), e.code
    ms = round((time.perf_counter() - t) * 1000)
    try:
        d = json.loads(body)
    except Exception:
        d = {"_raw": body[:200].decode("utf-8", "replace")}
    return {"status": st, "ms": ms, "body": d}

def rows(d):
    for k in ("clints", "clints_archive"):
        if isinstance(d.get(k), list):
            return d[k]
    return []

def capture(out):
    tok = login(BASE)
    res = {}
    for name, ep in cases(CS).items():
        r = get(BASE, ep, tok)
        res[name] = {"ep": ep, **r}
        print(f"{r['status']} {r['ms']:>6}ms  n={len(rows(r['body'])):>4} "
              f"countRow={r['body'].get('countRow')}  {name}")
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False, default=str)

def compare(before, after):
    b = json.load(open(before)); a = json.load(open(after)); bad = 0
    for name in b:
        rb, ra = b[name], a.get(name)
        if ra is None:
            print("  MISSING", name); bad += 1; continue
        d = []
        if rb["status"] != ra["status"]: d.append(f"status {rb['status']}->{ra['status']}")
        if sorted(rb["body"].keys()) != sorted(ra["body"].keys()):
            d.append(f"keys {sorted(rb['body'])}->{sorted(ra['body'])}")
        for k in ("countRow", "totalAmount"):
            if rb["body"].get(k) != ra["body"].get(k):
                d.append(f"{k} {rb['body'].get(k)}->{ra['body'].get(k)}")
        RB, RA = rows(rb["body"]), rows(ra["body"])
        if len(RB) != len(RA): d.append(f"n {len(RB)}->{len(RA)}")
        ib = [str(x.get("id")) for x in RB]; ia = [str(x.get("id")) for x in RA]
        if ib != ia:
            sym = set(ib) ^ set(ia)
            d.append(f"ids: order-differs' if not sym else ''  symdiff={len(sym)}"
                     if sym else "ids: same set, different order")
        # row-by-row dict comparison on the common ids
        mb = {str(x.get("id")): x for x in RB}; ma = {str(x.get("id")): x for x in RA}
        diffrows = 0; sample = None
        for i in set(mb) & set(ma):
            if json.dumps(mb[i], sort_keys=True, default=str) != json.dumps(ma[i], sort_keys=True, default=str):
                diffrows += 1
                if sample is None:
                    sample = [k for k in set(mb[i]) | set(ma[i])
                              if json.dumps(mb[i].get(k), sort_keys=True, default=str)
                              != json.dumps(ma[i].get(k), sort_keys=True, default=str)]
        if diffrows: d.append(f"rows_with_diffs={diffrows} keys={sample}")
        flag = "  " if not d else "!!"
        bad += bool(d)
        print(f"{flag} {rb['ms']:>6}ms -> {ra['ms']:>6}ms  common={len(set(mb)&set(ma)):>4}  {name}")
        for x in d: print("      ", x)
    print(f"\n{bad} case(s) with differences")

if sys.argv[1] == "capture":
    capture(sys.argv[3] if len(sys.argv) > 3 else "logs/parity/lc_before.json")
else:
    compare(sys.argv[2], sys.argv[3])
