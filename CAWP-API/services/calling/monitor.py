"""Call monitor (customer request 2026-10-13): live calls and calls by user over time.

Scope, decided here for every request (never by the page); the page's user filter
(`extension`) can only narrow it (403 `not_in_scope` outside it):

* a user whose role is `SuperAdmin` sees every extension and calls not yet at an agent (IVR);
* a supervisor (an active `supervisors` row) sees the active extensions of their team
  (`supervisor_users`) and their own;
* anyone else with access sees only their own active extension.

Live calls come from the calling service's channel snapshot (`pbx_live_channels`, refreshed
every few seconds; services/calling/live_monitor.py), grouped into calls by linkedid. A
snapshot that is old or failed is reported as such, never as "no calls".

Calls by user come from the PBX call records (asteriskcdrdb.cdr, read-only), classified per
agent extension: outbound (PJSIP/<ext> -> trunk), inbound (-> PJSIP/<ext>), internal (agent to
agent). A ring-group call rings several extensions: each rung extension that did not answer
counts one missed inbound. Extensions map to users through their CURRENT owner
(`user_extensions`): calls are attributed to today's owner of the extension.

Downloads (customer request 2026-10-15), same scope, period and user filter as the page:
* the summary (export_summary): the per-user table, its totals and the calls over time;
* the call detail (export_calls): one row per call and agent (calls(): the CDR rows of one
  linkedid merged, as the statistics count them) with the time, the other party's number, the
  line (caller ID) used, the result, ring and talk seconds and whether it was recorded. The
  number is matched to a client ONLY among the cases the downloader may see (money.scope_sql),
  through the normalised-mobile index; otherwise the client columns stay empty.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling.common import USER_NAME_SQL, iso, me, sess

AGENT_RE = re.compile(r"^PJSIP/([0-9]{2,6})-")
AGENT_EXT_RE = re.compile(r"^[0-9]{2,6}$")
MAX_DAYS = 31
MAX_CDR_ROWS = 200_000


# --------------------------------------------------------------------------- scope
def scope(user_id=None) -> dict:
    uid = str(user_id or me())
    role = sess().execute(text("SELECT r.name FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = CAST(:u AS uuid)"),
                          {"u": uid}).scalar()
    owners = sess().execute(text(f"""
        SELECT x.extension, x.user_id::text AS user_id, {USER_NAME_SQL.format(a='u')} AS name
          FROM user_extensions x LEFT JOIN users u ON u.id = x.user_id
         WHERE x.released_at IS NULL""")).mappings().all()
    names = {o["extension"]: {"id": o["user_id"], "name": o["name"]} for o in owners}
    if role == "SuperAdmin":
        return {"kind": "all", "extensions": None, "owners": names}
    team = {r[0] for r in sess().execute(text("""
        SELECT su.user_id::text FROM supervisors s
          JOIN supervisor_users su ON su.supervisor_id = s.id AND su.is_active
         WHERE s.user_id = CAST(:u AS uuid) AND s.is_active"""), {"u": uid})}
    kind = "team" if team else "own"
    team.add(uid)
    exts = {ext for ext, o in names.items() if o["id"] in team}
    return {"kind": kind, "extensions": exts, "owners": names}


def narrow(sc: dict, extension) -> dict:
    """The scope narrowed to one extension (the page's user filter); 403 outside the scope."""
    if extension in (None, ""):
        return sc
    ext = str(extension).strip()
    if not AGENT_EXT_RE.match(ext) or (sc["extensions"] is not None and ext not in sc["extensions"]):
        raise ApiError(403, "not_in_scope", "this extension is not in your scope", {"extension": "not_in_scope"})
    return {**sc, "extensions": {ext}, "filtered": ext}


def options(sc: dict) -> list[dict]:
    """Every extension of the scope (before any filter), for the page's user filter."""
    exts = sc["extensions"] if sc["extensions"] is not None else sc["owners"].keys()
    return [_agent(e, sc["owners"]) for e in sorted(exts, key=lambda e: (len(e), e))]


def _agent(ext, owners):
    o = owners.get(ext) or {}
    return {"extension": ext, "user_id": o.get("id"), "name": o.get("name")}


# --------------------------------------------------------------------------- live
def live(user_id=None, extension=None) -> dict:
    full = scope(user_id)
    sc = narrow(full, extension)
    snap = sess().execute(text("""
        SELECT captured_at, ok, error, channel_count, extract(epoch FROM now() - captured_at) AS age
          FROM pbx_live_snapshot WHERE id = 1""")).mappings().first()
    rows = [dict(r) for r in sess().execute(text("SELECT * FROM pbx_live_channels ORDER BY linkedid, uniqueid")).mappings()]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["linkedid"] or r["uniqueid"], []).append(r)
    allowed = sc["extensions"]
    calls = []
    for linkedid, legs in groups.items():
        agents = [r for r in legs if r["extension"]]
        trunks = [r for r in legs if (r["channel"] or "").startswith("SIP/")]
        if allowed is not None and not any(r["extension"] in allowed for r in agents):
            continue                                   # IVR-only calls are SuperAdmin's
        first = next((r for r in legs if r["uniqueid"] == linkedid), legs[0])
        if (first["channel"] or "").startswith("SIP/"):
            direction = "inbound"
        elif trunks:
            direction = "outbound"
        elif len(agents) >= 2:
            direction = "internal"
        else:
            direction = "outbound"
        bridged = [r for r in legs if r["bridge_id"]]
        ringing = [r for r in agents if (r["state"] or "") in ("Ring", "Ringing")]
        if len(bridged) >= 2:
            state = "talking"
        elif ringing:
            state = "ringing"
        elif direction == "inbound" and not agents:
            state = "ivr"
        else:
            state = "connecting"
        if direction == "inbound":
            external = first["caller_num"]
        elif trunks:
            external = trunks[0]["connected_num"] or trunks[0]["exten"] or trunks[0]["caller_num"]
        else:
            external = None
        on_call = [r for r in agents if r["bridge_id"]] or agents
        visible = [r for r in on_call if allowed is None or r["extension"] in allowed]
        calls.append({
            "id": linkedid, "direction": direction, "state": state, "external_number": external,
            "agents": [_agent(r["extension"], sc["owners"]) for r in visible],
            "ringing": [r["extension"] for r in ringing if allowed is None or r["extension"] in allowed],
            "duration_seconds": max((r["duration_s"] or 0) for r in legs),
            "where": first["application"] if state == "ivr" else None,
        })
    calls.sort(key=lambda c: -c["duration_seconds"])
    busy = {a["extension"] for c in calls if c["state"] == "talking" for a in c["agents"]}
    ringing_exts = {e for c in calls for e in c["ringing"]}
    exts = sorted(allowed if allowed is not None else sc["owners"].keys(), key=lambda e: (len(e), e))
    agents = [{**_agent(e, sc["owners"]), "status": "on_call" if e in busy else "ringing" if e in ringing_exts else "free"}
              for e in exts]
    age = float(snap["age"]) if snap else None
    return {
        "scope": sc["kind"], "extension": sc.get("filtered"), "options": options(full),
        "snapshot": {"captured_at": iso(snap["captured_at"]) if snap else None, "ok": bool(snap["ok"]) if snap else False,
                     "error": snap["error"] if snap else "no_snapshot", "age_seconds": round(age, 1) if age is not None else None,
                     "stale": age is None or age > 15 or not snap["ok"]},
        "summary": {"active_calls": len(calls), "talking": sum(c["state"] == "talking" for c in calls),
                    "ringing": sum(c["state"] == "ringing" for c in calls), "in_ivr": sum(c["state"] == "ivr" for c in calls),
                    "agents_on_call": len(busy), "agents": len(agents)},
        "calls": calls, "agents": agents,
    }


# --------------------------------------------------------------------------- calls by user over time
def classify(row) -> list[tuple[str, str, bool]]:
    """One CDR row -> [(extension, direction, answered)] for the agent extensions it involves."""
    src = AGENT_RE.match(row.get("channel") or "")
    dst = AGENT_RE.match(row.get("dstchannel") or "")
    answered = (row.get("disposition") or "").upper() == "ANSWERED"
    if src and dst:
        return [(src.group(1), "internal", answered)]
    if src:
        return [(src.group(1), "outbound", answered)]
    if dst:
        return [(dst.group(1), "inbound", answered)]
    return []


def _date(value, field, default):
    if value in (None, ""):
        return default
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM-DD", {field: "invalid"}) from None


def _cdr_rows(lo, hi):
    from services import reports_v2_cdr as cdrsrc
    try:
        conn = cdrsrc._connect()
    except cdrsrc.CdrUnavailable as e:
        raise ApiError(503, "cdr_unavailable", "the PBX call records are unavailable", {"reason": str(e)}) from None
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT calldate, channel, dstchannel, disposition, billsec, duration, linkedid, uniqueid,
                       src, dst, userfield, recordingfile
                  FROM asteriskcdrdb.cdr
                 WHERE calldate >= %s AND calldate < %s AND (channel LIKE 'PJSIP/%%' OR dstchannel LIKE 'PJSIP/%%')
                 ORDER BY calldate LIMIT %s""", (lo, hi, MAX_CDR_ROWS + 1))
            return cur.fetchall()
        finally:
            cur.close()
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ApiError(503, "cdr_unavailable", "the PBX call records are unavailable",
                       {"reason": type(e).__name__}) from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _range(args):
    """(from, to, lo, hi) of the page's period: Asia/Riyadh days, default today, 31 days max."""
    today = riyadh.now().date()
    start = _date(args.get("from"), "from", today)
    end = _date(args.get("to"), "to", start)
    if end < start:
        raise ApiError(422, VALIDATION_FAILED, "to is before from", {"to": "before_from"})
    if (end - start).days >= MAX_DAYS:
        raise ApiError(422, VALIDATION_FAILED, f"at most {MAX_DAYS} days", {"to": "range_too_long"})
    lo = _dt.datetime.combine(start, _dt.time.min)                      # the PBX writes Riyadh wall clock
    hi = _dt.datetime.combine(end + _dt.timedelta(days=1), _dt.time.min)
    return start, end, lo, hi


def stats(args, user_id=None, rows=None) -> dict:
    full = scope(user_id)
    sc = narrow(full, args.get("extension"))
    start, end, lo, hi = _range(args)
    gran = args.get("granularity") or ("hour" if start == end else "day")
    if gran not in ("hour", "day"):
        raise ApiError(422, VALIDATION_FAILED, "granularity must be hour or day", {"granularity": "invalid"})
    cdr = rows if rows is not None else _cdr_rows(lo, hi)
    allowed = sc["extensions"]
    calls: dict[tuple, dict] = {}
    for r in cdr[:MAX_CDR_ROWS]:
        for ext, direction, answered in classify(r):
            if allowed is not None and ext not in allowed:
                continue
            key = (r.get("linkedid") or r.get("uniqueid"), ext, direction)
            c = calls.get(key)
            if c is None:
                calls[key] = {"ext": ext, "direction": direction, "answered": answered,
                              "billsec": int(r.get("billsec") or 0), "at": r["calldate"]}
            else:
                c["answered"] = c["answered"] or answered
                c["billsec"] = max(c["billsec"], int(r.get("billsec") or 0))
    fmt = "%Y-%m-%d %H:00" if gran == "hour" else "%Y-%m-%d"
    buckets = []
    t = lo
    while t < hi:
        buckets.append(t.strftime(fmt))
        t += _dt.timedelta(hours=1) if gran == "hour" else _dt.timedelta(days=1)
    per_user: dict[str, dict] = {}
    series = {b: {"bucket": b, "total": 0, "by_extension": {}} for b in buckets}
    for c in calls.values():
        u = per_user.setdefault(c["ext"], {**_agent(c["ext"], sc["owners"]), "outbound": 0, "outbound_answered": 0,
                                           "inbound_answered": 0, "inbound_missed": 0, "internal": 0, "talk_seconds": 0})
        if c["direction"] == "outbound":
            u["outbound"] += 1
            u["outbound_answered"] += c["answered"]
        elif c["direction"] == "inbound":
            u["inbound_answered" if c["answered"] else "inbound_missed"] += 1
        else:
            u["internal"] += 1
        u["talk_seconds"] += c["billsec"] if c["answered"] else 0
        b = series.get(c["at"].strftime(fmt))
        if b is not None and (c["direction"] != "inbound" or c["answered"]):   # calls handled, not every ring
            b["total"] += 1
            b["by_extension"][c["ext"]] = b["by_extension"].get(c["ext"], 0) + 1
    users = sorted(per_user.values(), key=lambda u: -(u["outbound"] + u["inbound_answered"] + u["internal"]))
    for u in users:
        u["handled"] = u["outbound"] + u["inbound_answered"] + u["internal"]
    return {"scope": sc["kind"], "extension": sc.get("filtered"), "options": options(full),
            "from": start.isoformat(), "to": end.isoformat(), "granularity": gran,
            "truncated": len(cdr) > MAX_CDR_ROWS, "users": users, "series": list(series.values()),
            "totals": {"handled": sum(u["handled"] for u in users), "outbound": sum(u["outbound"] for u in users),
                       "outbound_answered": sum(u["outbound_answered"] for u in users),
                       "inbound_answered": sum(u["inbound_answered"] for u in users),
                       "inbound_missed": sum(u["inbound_missed"] for u in users),
                       "talk_seconds": sum(u["talk_seconds"] for u in users)}}


# --------------------------------------------------------------------------- call detail + downloads
DIRECTION_AR = {"outbound": "صادرة", "inbound": "واردة", "internal": "داخلية"}
RESULT_AR = {"ANSWERED": "تم الرد", "NO ANSWER": "لم يتم الرد", "BUSY": "مشغول", "FAILED": "فشل",
             "CONGESTION": "تعذر الاتصال"}
SCOPE_AR = {"all": "كل التحويلات", "team": "الفريق", "own": "تحويلتي"}


def _other_party(r, direction):
    """(number, line) of one CDR row: the customer's number and the PBX line (caller ID / DID)."""
    src, dst = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
    if direction == "outbound":
        return dst, src
    if direction == "inbound":
        uf = (r.get("userfield") or "").strip()
        did = uf.split(":")[1] if uf.startswith("ivr:") and uf.count(":") >= 1 else ""
        return src, did
    peer = AGENT_RE.match(r.get("dstchannel") or "")
    return (peer.group(1) if peer else dst), ""


def calls(args, user_id=None, rows=None) -> dict:
    """One row per call and agent (the CDR rows of one linkedid merged), oldest first."""
    full = scope(user_id)
    sc = narrow(full, args.get("extension"))
    start, end, lo, hi = _range(args)
    cdr = rows if rows is not None else _cdr_rows(lo, hi)
    allowed = sc["extensions"]
    merged: dict[tuple, dict] = {}
    for r in cdr[:MAX_CDR_ROWS]:
        for ext, direction, answered in classify(r):
            if allowed is not None and ext not in allowed:
                continue
            key = (r.get("linkedid") or r.get("uniqueid"), ext, direction)
            number, line = _other_party(r, direction)
            disp = (r.get("disposition") or "").upper()
            rank = 3 if answered else 2 if (r.get("dstchannel") or "") else 1    # the Dial row tells the result
            billsec, duration = int(r.get("billsec") or 0), int(r.get("duration") or 0)
            c = merged.get(key)
            if c is None:
                merged[key] = {"at": r["calldate"], "extension": ext, "direction": direction, "answered": answered,
                               "result": disp, "_rank": rank, "number": number, "line": line,
                               "talk_seconds": billsec if answered else 0, "ring_seconds": max(duration - billsec, 0),
                               "recorded": bool((r.get("recordingfile") or "").strip()), "call_id": key[0]}
                continue
            c["at"] = min(c["at"], r["calldate"])
            if rank > c["_rank"]:
                c["result"], c["_rank"] = disp, rank
            c["answered"] = c["answered"] or answered
            if answered:
                c["talk_seconds"] = max(c["talk_seconds"], billsec)
            c["ring_seconds"] = max(c["ring_seconds"], duration - billsec)
            c["number"], c["line"] = c["number"] or number, c["line"] or line
            c["recorded"] = c["recorded"] or bool((r.get("recordingfile") or "").strip())
    items = sorted(merged.values(), key=lambda c: (c["at"], c["extension"]))
    for c in items:
        c.pop("_rank")
        c.update({k: v for k, v in _agent(c["extension"], sc["owners"]).items() if k != "extension"})
    clients = _clients_for([c["number"] for c in items if c["direction"] != "internal"], user_id)
    for c in items:
        c["client"] = clients.get(c["number"]) if c["direction"] != "internal" else None
    return {"scope": sc["kind"], "extension": sc.get("filtered"), "from": start.isoformat(), "to": end.isoformat(),
            "truncated": len(cdr) > MAX_CDR_ROWS, "calls": items}


def _clients_for(numbers, user_id=None) -> dict:
    """number -> {name, account_number} for the numbers that are a contact of a case the caller
    may see (one client per number: active before archived, the latest updated)."""
    from services import money
    nums = sorted({n for n in numbers if n and len(re.sub(r"[^0-9]", "", n)) >= 9})
    if not nums:
        return {}
    pred, params = money.scope_sql(money.caller_scope(str(user_id or me())), alias="k")
    rows = sess().execute(text(f"""
        SELECT DISTINCT ON (m.n) m.n, k.name, k."accountNumber"
          FROM unnest(CAST(:nums AS text[])) AS m(n)
          JOIN "ClintContacts" cc ON cc.is_deleted = false
                                 AND cawp_norm_mobile(cc.contact) = cawp_norm_mobile(m.n)
          JOIN clints k ON k."identityNumber" = cc."identityNumber"
         WHERE cawp_norm_mobile(m.n) IS NOT NULL AND {pred}
         ORDER BY m.n, k.is_archive ASC, k.update_at DESC NULLS LAST"""), {"nums": nums, **params}).all()
    return {r[0]: {"name": r[1], "account_number": r[2]} for r in rows}


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _who(sc_kind, ext, owners) -> str:
    if not ext:
        return SCOPE_AR.get(sc_kind, sc_kind)
    a = _agent(ext, owners)
    return f"{a['name'] or '—'} ({ext})"


def _info_sheet(wb, data, owners, kind_ar):
    sh = wb.add_sheet("معلومات")
    sh.append([("s", "البند"), ("s", "القيمة")], header=True)
    for label, value in (("التقرير", kind_ar), ("من", data["from"]), ("إلى", data["to"]),
                         ("الموظف", _who(data["scope"], data.get("extension"), owners)),
                         ("وقت الإنشاء (الرياض)", riyadh.now().strftime("%Y-%m-%d %H:%M")),
                         ("ملاحظة", "المكالمات تُنسب للمالك الحالي للتحويلة؛ الأوقات بتوقيت الرياض")):
        sh.append([("s", label), ("s", value)])
    if data.get("truncated"):
        sh.append([("s", "تنبيه"), ("s", f"الفترة فيها أكثر من {MAX_CDR_ROWS} سجل؛ قصّر الفترة")])


def export_summary(args, dest_dir: str, user_id=None, rows=None) -> tuple[str, str, int]:
    """XLSX: the per-user table with totals, the calls over time, and the period. -> (path, name, users)."""
    from core.reports.xlsx import Workbook
    data = stats({**args, "granularity": args.get("granularity") or None}, user_id=user_id, rows=rows)
    owners = scope(user_id)["owners"]
    name = f"call-monitor-summary-{data['from']}_{data['to']}{'-' + data['extension'] if data['extension'] else ''}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("ملخص الموظفين", freeze_header=True)
    sh.append([("s", h) for h in ("الموظف", "التحويلة", "المكالمات المنجزة", "صادرة", "صادرة تم الرد عليها",
                                  "واردة تم الرد عليها", "واردة فائتة", "داخلية", "مدة المحادثة",
                                  "مدة المحادثة (ثانية)")], header=True)
    for u in data["users"]:
        sh.append([("s", u["name"] or "—"), ("s", u["extension"]), ("int", u["handled"]), ("int", u["outbound"]),
                   ("int", u["outbound_answered"]), ("int", u["inbound_answered"]), ("int", u["inbound_missed"]),
                   ("int", u["internal"]), ("s", _clock(u["talk_seconds"])), ("int", u["talk_seconds"])])
    tot = data["totals"]
    sh.append([("s", "الإجمالي"), None, ("int", tot["handled"]), ("int", tot["outbound"]),
               ("int", tot["outbound_answered"]), ("int", tot["inbound_answered"]), ("int", tot["inbound_missed"]),
               ("int", sum(u["internal"] for u in data["users"])), ("s", _clock(tot["talk_seconds"])),
               ("int", tot["talk_seconds"])], header=True)
    ts = wb.add_sheet("حسب الوقت", freeze_header=True)
    exts = [u["extension"] for u in data["users"]]
    ts.append([("s", "الساعة" if data["granularity"] == "hour" else "اليوم"), ("s", "الإجمالي")]
              + [("s", f"{u['name'] or '—'} ({u['extension']})") for u in data["users"]], header=True)
    for b in data["series"]:
        ts.append([("s", b["bucket"]), ("int", b["total"])] + [("int", b["by_extension"].get(e, 0)) for e in exts])
    _info_sheet(wb, data, owners, "ملخص مكالمات الموظفين")
    wb.save()
    return path, name, len(data["users"])


def export_calls(args, dest_dir: str, user_id=None, rows=None) -> tuple[str, str, int]:
    """XLSX: one row per call (the call detail). -> (path, name, calls)."""
    from core.reports.xlsx import Workbook
    data = calls(args, user_id=user_id, rows=rows)
    owners = scope(user_id)["owners"]
    name = f"call-monitor-calls-{data['from']}_{data['to']}{'-' + data['extension'] if data['extension'] else ''}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("تفاصيل المكالمات", freeze_header=True)
    sh.append([("s", h) for h in ("التاريخ والوقت", "الموظف", "التحويلة", "الاتجاه", "الرقم", "العميل",
                                  "رقم الحساب", "الخط", "النتيجة", "مدة الرنين (ثانية)", "مدة المحادثة",
                                  "مدة المحادثة (ثانية)", "مسجلة", "معرف المكالمة")], header=True)
    for c in data["calls"]:
        client = c["client"] or {}
        sh.append([("datetime", c["at"]), ("s", c["name"] or "—"), ("s", c["extension"]),
                   ("s", DIRECTION_AR[c["direction"]]), ("s", c["number"] or None), ("s", client.get("name")),
                   ("s", client.get("account_number")), ("s", c["line"] or None),
                   ("s", RESULT_AR.get(c["result"], c["result"] or "—")), ("int", c["ring_seconds"]),
                   ("s", _clock(c["talk_seconds"])), ("int", c["talk_seconds"]), ("s", "نعم" if c["recorded"] else "لا"),
                   ("s", c["call_id"])])
    _info_sheet(wb, data, owners, "تفاصيل المكالمات")
    wb.save()
    return path, name, len(data["calls"])
