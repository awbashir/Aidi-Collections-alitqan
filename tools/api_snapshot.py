#!/usr/bin/env python3
"""Golden snapshot of every GET endpoint: status, timing, envelope keys, countRow, first-row shape.

  python3 tools/api_snapshot.py --base http://localhost:9797 --out logs/golden_before.json
  python3 tools/api_snapshot.py --base http://localhost:9798 --out logs/golden_after.json --compare logs/golden_before.json
"""
import argparse, json, sys, time, urllib.request, urllib.parse, urllib.error

IDS = dict(l.strip().split('=', 1) for l in open('logs/sample_ids.txt') if '=' in l and not l.startswith('client_id'))
first = [l for l in open('logs/sample_ids.txt') if l.startswith('client_id')][0].split()
IDS.update(kv.split('=', 1) for kv in first)
D0, D1 = '2026-09-09', '2026-09-15'
DATES = f"customeFilterDateValue[]={D0}&customeFilterDateValue[]={D1}&customeFilterMoveDateValue[]={D0}&customeFilterMoveDateValue[]={D1}"
PG = "page=1&pageSize=100"

ENDPOINTS = [
    "/uesrs?" + PG, "/uesrs/export", f"/users/{IDS['me_id']}", "/roles", f"/roles/{IDS['role_id']}",
    "/nationalities", "/updateSides", "/branches", "/contracts", f"/contracts/{IDS['contract_id']}",
    "/clints?" + PG, "/clints?" + PG + "&field=amount&sort=desc", "/clints?" + PG + "&field=create_at&sort=asc",
    f"/clints?{PG}&customeFilterContractValue[]={IDS['contract_id']}",
    f"/clints?{PG}&customeFilterUserValue[]={IDS['collector']}",
    f"/clints?{PG}&filterOperator=contains&filterField=name&filterFieldType=string&filterValue=%D9%85%D8%AD%D9%85%D8%AF",
    f"/v2/clints?{PG}&customeFilrerAccountNumberValue={IDS['account']}",
    f"/clints/{IDS['client_id']}", f"/clints/{IDS['archived_client_id']}", f"/clints/user/{IDS['collector']}",
    "/clints/archive/?" + PG, "/clints/archive/?" + PG + "&sort[key]=archive_create_at&sort[order]=desc",
    "/clints/archive/reson", "/clints/delinquency_reasons", "/clint_contact_types", "/comment_status",
    "/comment_reachability", "/collection_types", "/collection_methods", "/contact_methods",
    f"/comments?{PG}&{DATES}", f"/comments/export?{DATES}", f"/comments/current_user?{PG}&{DATES}",
    f"/comments/current_user/export?{DATES}", f"/comments/{IDS['comment_id']}", f"/comments/clint/{IDS['client_id']}",
    f"/contacts/identityNumber/{IDS['identity']}",
    f"/payments?{PG}&{DATES}", f"/payments/user?{PG}&{DATES}", f"/payments/user/export?{DATES}", f"/payments/export?{DATES}",
    f"/payments/approval_status?{PG}&{DATES}", f"/payments/approval_status/export?{DATES}",
    f"/payment/{IDS['payment_id']}", f"/payments/clint/{IDS['client_id']}", f"/payments/clint/{IDS['client_id']}/total",
    f"/payment_date?{PG}&{DATES}", f"/payment_date/export?{DATES}", f"/payment_date/currentUser?{PG}&{DATES}",
    f"/payment_date/currentUser/export?{DATES}", "/payment_date/user", f"/payment_date/clint/{IDS['client_id']}",
    f"/clint/{IDS['client_id']}/documents", f"/clint/lock-history/{IDS['client_id']}",
    "/supervisors", f"/supervisor/{IDS['supervisor_id']}",
    "/portfolios?" + PG, "/portfolios/export", f"/portfolio/{IDS['portfolio_id']}",
    "/dashbord/user", "/dashbord/collector", "/dashbord/collector/v2", "/dashbord/admin/dailyPayments/v2",
    "/dashbord/admin/dailyPaymentDates/v2", "/dashbord/Allcollector/v2", "/dashbord/commentsReport/v2",
    f"/dashboard?{DATES}&startDate={D0}&endDate={D1}",
    f"/sepcifyusers/clint/{IDS['client_id']}", "/notifications", f"/loginLog/user/{IDS['me_id']}",
    "/clints/not_have_payments?" + PG, "/clints/not_have_payments/export", "/clints/have_payments?" + PG,
    "/clints/have_payments/export", "/clints/neglect?" + PG, "/clints/neglect/export",
    "/clints/currentUser/have_nextCalls?" + PG, "/clints/currentUser/have_nextCalls/export",
    "/cdr", f"/cdr_by_date?start_date={D0}&end_date={D1}",
]

def shape(v, depth=0):
    if isinstance(v, dict):
        return {k: shape(v[k], depth + 1) for k in sorted(v)} if depth < 3 else 'dict'
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    if v is None: return 'null'
    return type(v).__name__

def login(base):
    req = urllib.request.Request(base + '/login', data=json.dumps({"username": "aidi.dev", "password": "Aidi@2026"}).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=30))['access_token']

def fetch(base, ep, token, timeout):
    req = urllib.request.Request(base + ep, headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(); status = r.status
    except urllib.error.HTTPError as e:
        body = e.read(); status = e.code
    except Exception as e:
        return {"status": "ERR", "ms": round((time.perf_counter() - t) * 1000), "error": str(e)[:200]}
    ms = round((time.perf_counter() - t) * 1000)
    try:
        d = json.loads(body)
    except Exception:
        return {"status": status, "ms": ms, "bytes": len(body), "json": False}
    rec = {"status": status, "ms": ms, "bytes": len(body)}
    if isinstance(d, dict):
        rec["keys"] = sorted(d.keys())
        rec["countRow"] = d.get("countRow")
        rec["totalAmount"] = d.get("totalAmount")
        rec["status_field"] = d.get("status")
        rec["error"] = d.get("error")
        lists = [(k, v) for k, v in d.items() if isinstance(v, list)]
        rec["lists"] = {k: {"n": len(v), "shape": shape(v[0]) if v else None} for k, v in lists}
        if lists:
            lists.sort(key=lambda kv: -len(kv[1]))   # largest list first (order-independent)
            k, v = lists[0]
            rec["list_key"] = k; rec["n_rows"] = len(v)
            rec["row_shape"] = shape(v[0]) if v else None
            if v and isinstance(v[0], dict) and 'id' in v[0]:
                rec["ids"] = sorted(str(x.get('id')) for x in v)[:100]
        else:
            rec["row_shape"] = shape(d)
    elif isinstance(d, list):
        rec["list_key"] = "<array>"; rec["n_rows"] = len(d); rec["row_shape"] = shape(d[0]) if d else None
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://localhost:9797'); ap.add_argument('--out', required=True)
    ap.add_argument('--compare'); ap.add_argument('--timeout', type=int, default=300); ap.add_argument('--only', default='')
    a = ap.parse_args()
    token = login(a.base)
    res = {}
    for ep in ENDPOINTS:
        if a.only and a.only not in ep: continue
        res[ep] = fetch(a.base, ep, token, a.timeout)
        r = res[ep]
        print(f"{r.get('status')!s:>4} {r.get('ms', 0):>7}ms {r.get('bytes', 0):>9}B  {ep[:90]}  n={r.get('n_rows')} count={r.get('countRow')}", flush=True)
    json.dump(res, open(a.out, 'w'), indent=1, ensure_ascii=False)
    if a.compare:
        base = json.load(open(a.compare)); bad = 0
        print("\n=== COMPARE (before -> after) ===")
        for ep, r in res.items():
            b = base.get(ep)
            if not b: print("  NEW", ep); continue
            diffs = []
            for k in ("status", "keys", "countRow", "status_field"):
                if b.get(k) != r.get(k): diffs.append(f"{k}: {json.dumps(b.get(k), ensure_ascii=False)[:160]} -> {json.dumps(r.get(k), ensure_ascii=False)[:160]}")
            # list comparison by KEY (order-independent); old snapshots only carry the first list
            bl = b.get("lists") or ({b["list_key"]: {"n": b.get("n_rows"), "shape": b.get("row_shape")}} if b.get("list_key") else {})
            rl = r.get("lists") or ({r["list_key"]: {"n": r.get("n_rows"), "shape": r.get("row_shape")}} if r.get("list_key") else {})
            for lk in sorted(set(bl) | set(rl)):
                if lk == "<array>" and (lk not in bl or lk not in rl): continue
                if lk not in bl or lk not in rl:
                    if lk in bl: diffs.append(f"list '{lk}' missing in new")
                    elif b.get("lists") is not None: diffs.append(f"new list '{lk}'")
                    continue
                if bl[lk]["n"] != rl[lk]["n"]: diffs.append(f"{lk}: n {bl[lk]['n']} -> {rl[lk]['n']}")
                if bl[lk]["shape"] != rl[lk]["shape"]: diffs.append(f"{lk}: shape {json.dumps(bl[lk]['shape'], ensure_ascii=False)[:150]} -> {json.dumps(rl[lk]['shape'], ensure_ascii=False)[:150]}")
            if b.get("ids") and r.get("ids") and set(b["ids"]) != set(r["ids"]):
                diffs.append(f"ids: {len(set(b['ids']) ^ set(r['ids']))} differ (ordering/scope)")
            sp = (b.get('ms') or 1) / max(r.get('ms') or 1, 1)
            flag = "  " if not diffs else "!!"; bad += bool(diffs)
            print(f"{flag} {b.get('ms',0):>7}ms -> {r.get('ms',0):>7}ms ({sp:5.1f}x)  {ep[:80]}")
            for d in diffs: print("        ", d)
        print(f"\n{bad} endpoint(s) with shape/count differences")
main()
