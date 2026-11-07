"""Employee activity report ("تقرير الموظف"; customer request 2026-11-05): one collector's daily attendance to the
system and answered calls, with charts, the daily table and the weekly totals, as a print-ready Arabic
page (the browser saves it as PDF).

Sources, read-only:
* attendance: a day counts when the collector logged in (`login_log`, successful) OR has any activity
  that day (a call or a note), because a session can stay open from the day before. "Login" is the
  first successful login recorded that day (Asia/Riyadh).
* calls: the PBX call records of the collector's extensions (services/calling/monitor.py: the indexed
  CDR read and the per-call classification), so the app, the campaigns and the desk phone all count.
  Extensions: every `user_extensions` row of the user that overlaps the period, plus `users.extension`
  (the history starts when the app took over the extensions, 2026-10-21). "Answered" = outbound calls
  the client answered + inbound calls the collector answered; internal calls are left out; inbound
  calls that rang unanswered (ring groups) are reported apart, never counted.
* notes: the notes the collector wrote; the automatic copies of "apply to all the client's accounts"
  (`applied_from_comment_id`) are counted apart.

Scope: the team dashboard's (services/team_dashboard.py): SuperAdmin everyone, a supervisor the team
and the users of the supervised contracts, anyone else themselves. Period: Asia/Riyadh days, at most
MAX_DAYS; weeks run Sunday to Saturday.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import uuid

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling.common import USER_NAME_SQL, me, sess

MAX_DAYS = 92
CDR_CHUNK_DAYS = 31                       # the call monitor reads at most 31 days at once
DAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MONTHS_AR = ["", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر",
             "نوفمبر", "ديسمبر"]
NAVY, STEEL, MIST, SLATE, WASH = "#070A4B", "#5B93C9", "#B8C4CE", "#6B7482", "#F2F6F8"


# --------------------------------------------------------------------------- arguments + scope
def _date(raw, field, default):
    if raw in (None, ""):
        return default
    try:
        return _dt.date.fromisoformat(str(raw))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM-DD", {field: "invalid"}) from None


def period(args) -> tuple[_dt.date, _dt.date]:
    today = riyadh.today()
    end = _date(args.get("to"), "to", today)
    start = _date(args.get("from"), "from", end - _dt.timedelta(days=29))
    if end < start:
        raise ApiError(422, VALIDATION_FAILED, "to is before from", {"to": "before_from"})
    if end > today:
        raise ApiError(422, VALIDATION_FAILED, "to is in the future", {"to": "future"})
    if (end - start).days >= MAX_DAYS:
        raise ApiError(422, VALIDATION_FAILED, f"at most {MAX_DAYS} days", {"to": "range_too_long"})
    return start, end


def _scope_users(user_id=None):
    from services import team_dashboard as TD
    return TD.scope(user_id)["users"]                     # None = every user


def options(user_id=None) -> dict:
    users = _scope_users(user_id)
    cond, params = ("TRUE", {}) if users is None else ("u.id = ANY(CAST(:ids AS uuid[]))", {"ids": sorted(users)})
    rows = sess().execute(text(f"""
        SELECT u.id::text, {USER_NAME_SQL.format(a='u')} AS name, u.user_name, u.is_active, r.name AS role
          FROM users u LEFT JOIN roles r ON r.id = u.role_id
         WHERE {cond} ORDER BY u.is_active DESC, name"""), params).mappings().all()
    return {"users": [dict(r) for r in rows], "max_days": MAX_DAYS}


def _target(args, user_id=None) -> dict:
    raw = args.get("user_id")
    try:
        uid = str(uuid.UUID(str(raw)))
    except (ValueError, TypeError):
        raise ApiError(422, VALIDATION_FAILED, "user_id is required", {"user_id": "required"}) from None
    users = _scope_users(user_id)
    if users is not None and uid not in users:
        raise ApiError(403, "not_in_scope", "this user is outside your scope", {"user_id": uid})
    row = sess().execute(text(f"""
        SELECT u.id::text, {USER_NAME_SQL.format(a='u')} AS name, u.user_name, u.extension
          FROM users u WHERE u.id = CAST(:u AS uuid)"""), {"u": uid}).mappings().first()
    if row is None:
        raise ApiError(404, "not_found", "user not found", {"user_id": uid})
    return dict(row)


# --------------------------------------------------------------------------- facts
def _extensions(user: dict, start, end) -> list[str]:
    lo, hi = riyadh.range_bounds(start, end)
    exts = {r[0] for r in sess().execute(text("""
        SELECT extension FROM user_extensions WHERE user_id = CAST(:u AS uuid)
           AND assigned_at < :hi AND (released_at IS NULL OR released_at > :lo)"""),
        {"u": user["id"], "lo": lo, "hi": hi})}
    if (user.get("extension") or "").strip():
        exts.add(user["extension"].strip())
    return sorted(e for e in exts if e.isdigit())


def _calls(exts, start, end) -> dict:
    """{date: {out, out_answered, in_answered, in_missed, talk, first, last}} from the PBX."""
    from services.calling import monitor as M
    days: dict = {}
    if not exts:
        return days
    chunk = start
    while chunk <= end:
        last = min(chunk + _dt.timedelta(days=CDR_CHUNK_DAYS - 1), end)
        _s, _e, lo, hi = M._range({"from": chunk.isoformat(), "to": last.isoformat()})
        rows = []
        for ext in exts:
            rows += M._cdr_rows(lo, hi, ext)
        for c in M._merge(rows, set(exts)):
            if c["direction"] == "internal":
                continue
            at = str(c.get("at") or "")
            x = days.setdefault(at[:10], {"out": 0, "out_answered": 0, "in_answered": 0, "in_missed": 0, "talk": 0,
                                          "first": None, "last": None})
            if c["direction"] == "outbound":
                x["out"] += 1
                x["out_answered"] += 1 if c["answered"] else 0
            elif c["answered"]:
                x["in_answered"] += 1
            else:
                x["in_missed"] += 1
                continue
            if c["answered"]:
                x["talk"] += int(c.get("talk_seconds") or 0)
            hm = at[11:16]
            x["first"] = min(x["first"] or hm, hm)
            x["last"] = max(x["last"] or hm, hm)
        chunk = last + _dt.timedelta(days=1)
    return days


def _logins(user, start, end) -> dict:
    rows = sess().execute(text("""
        SELECT (l."timestamp" AT TIME ZONE 'Asia/Riyadh')::date AS d,
               to_char(min(l."timestamp" AT TIME ZONE 'Asia/Riyadh'), 'HH24:MI') AS first_at
          FROM login_log l WHERE l.username = :un AND l.outcome = 'success'
           AND (l."timestamp" AT TIME ZONE 'Asia/Riyadh')::date BETWEEN :f AND :t GROUP BY 1"""),
        {"un": user["user_name"], "f": start, "t": end}).all()
    return {r[0].isoformat(): r[1] for r in rows}


def _notes(user, start, end) -> dict:
    lo, hi = riyadh.range_bounds(start, end)
    rows = sess().execute(text("""
        SELECT (create_at AT TIME ZONE 'Asia/Riyadh')::date AS d,
               count(*) FILTER (WHERE applied_from_comment_id IS NULL), count(*) FILTER (WHERE applied_from_comment_id IS NOT NULL)
          FROM "Comments" WHERE user_id = CAST(:u AS uuid) AND is_deleted = false AND create_at >= :lo AND create_at < :hi
         GROUP BY 1"""), {"u": user["id"], "lo": lo, "hi": hi}).all()
    return {r[0].isoformat(): (int(r[1]), int(r[2])) for r in rows}


def report(args, user_id=None) -> dict:
    user = _target(args, user_id)
    start, end = period(args)
    exts = _extensions(user, start, end)
    calls, logins, notes = _calls(exts, start, end), _logins(user, start, end), _notes(user, start, end)
    days = []
    d = start
    while d <= end:
        k = d.isoformat()
        c = calls.get(k, {})
        own, copies = notes.get(k, (0, 0))
        login = logins.get(k)
        out_, out_ans, in_ans = c.get("out", 0), c.get("out_answered", 0), c.get("in_answered", 0)
        starts = [t for t in (login, c.get("first")) if t]
        days.append({"date": k, "weekday": DAYS_AR[d.weekday()], "weekend": d.weekday() in (4, 5),
                     "present": bool(login) or out_ > 0 or in_ans > 0 or own > 0, "login_at": login,
                     "start": min(starts) if starts else None, "first_call": c.get("first"), "last_call": c.get("last"),
                     "out": out_, "out_answered": out_ans, "in_answered": in_ans, "answered": out_ans + in_ans,
                     "in_missed": c.get("in_missed", 0), "talk_seconds": c.get("talk", 0), "notes": own,
                     "note_copies": copies})
        d += _dt.timedelta(days=1)
    weeks = []
    w = start - _dt.timedelta(days=(start.weekday() + 1) % 7)       # the Sunday of the first week
    while w <= end:
        we = w + _dt.timedelta(days=6)
        ds = [x for x in days if w.isoformat() <= x["date"] <= we.isoformat()]
        weeks.append({"from": max(w, start).isoformat(), "to": min(we, end).isoformat(),
                      "partial": w < start or we > end, **_totals(ds)})
        w += _dt.timedelta(days=7)
    return {"user": {"id": user["id"], "name": user["name"], "user_name": user["user_name"]}, "extensions": exts,
            "from": start.isoformat(), "to": end.isoformat(), "generated_at": riyadh.now().isoformat(),
            "totals": _totals(days), "weeks": weeks, "days": days}


def _totals(ds) -> dict:
    out = {"days": len(ds), "work_days": sum(not x["weekend"] for x in ds), "present": sum(x["present"] for x in ds),
           "present_work_days": sum(x["present"] and not x["weekend"] for x in ds)}
    for k in ("out", "out_answered", "in_answered", "answered", "in_missed", "talk_seconds", "notes", "note_copies"):
        out[k] = sum(x[k] for x in ds)
    call_days = sum(1 for x in ds if x["out"])
    out["answer_rate"] = round(out["out_answered"] * 100 / out["out"], 1) if out["out"] else None
    out["answered_per_call_day"] = round(out["answered"] / call_days, 1) if call_days else None
    return out


# --------------------------------------------------------------------------- the print-ready page
def _talk(s) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _mins(hm) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _short(iso) -> str:
    d = _dt.date.fromisoformat(iso)
    return f"{d.day} {MONTHS_AR[d.month]}"


def _num(v) -> str:
    return f'<span class="num">{v:,}</span>' if isinstance(v, int) else f'<span class="num">{_html.escape(str(v))}</span>'


def _axis_labels(g, rows, x, cw, y_text, month_y):
    for i, r in enumerate(rows):
        day = int(r["date"][8:])
        g.append(f'<text x="{x(i) + cw / 2:.1f}" y="{y_text}" font-size="9.5" fill="{SLATE}" text-anchor="middle" direction="ltr">{day}</text>')
        if i == 0 or day == 1:
            g.append(f'<text x="{x(i) + cw / 2:.1f}" y="{month_y}" font-size="10.5" fill="{NAVY}" font-weight="700" '
                     f'text-anchor="end">{MONTHS_AR[int(r["date"][5:7])]}</text>')


def _calls_chart(rows) -> str:
    W, H, L, R, TOP, B = 760, 300, 46, 16, 18, 64
    cw = (W - L - R) / len(rows)
    vmax = max(max(r["out"] for r in rows), max(r["answered"] for r in rows), 1)
    step = 100 if vmax > 300 else (50 if vmax > 100 else 10)
    top = ((vmax + step - 1) // step) * step
    y = lambda v: TOP + (H - TOP - B) * (1 - v / top)                     # noqa: E731
    x = lambda i: W - R - (i + 1) * cw                                    # noqa: E731
    g = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    for v in range(0, top + 1, step):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#E7EEF2"/>')
        g.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" font-size="10" fill="{SLATE}" text-anchor="end" direction="ltr">{v}</text>')
    for i, r in enumerate(rows):
        bx, bw = x(i) + cw * 0.12, cw * 0.76
        if r["weekend"]:
            g.append(f'<rect x="{x(i):.1f}" y="{TOP}" width="{cw:.1f}" height="{H - TOP - B}" fill="{WASH}"/>')
        for v, col in ((r["out"], MIST), (r["answered"], NAVY)):
            if v:
                g.append(f'<rect x="{bx:.1f}" y="{y(v):.1f}" width="{bw:.1f}" height="{y(0) - y(v):.1f}" rx="1.5" fill="{col}"/>')
        if r["answered"] and (r["answered"] >= top / 15) and cw > 14:
            g.append(f'<text x="{bx + bw / 2:.1f}" y="{y(r["answered"]) - 3:.1f}" font-size="8.5" fill="{NAVY}" '
                     f'text-anchor="middle" direction="ltr" font-weight="700">{r["answered"]}</text>')
    _axis_labels(g, rows, x, cw, H - B + 14, H - B + 30)
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="{MIST}"/></svg>')
    return "".join(g)


def _attendance_chart(rows) -> str:
    W, H, L, R, TOP, B = 760, 270, 46, 16, 14, 44
    H0, H1 = 7, 23
    cw = (W - L - R) / len(rows)
    y = lambda m: TOP + (H - TOP - B) * ((max(min(m, H1 * 60), H0 * 60) - H0 * 60) / ((H1 - H0) * 60))  # noqa: E731
    x = lambda i: W - R - (i + 1) * cw                                    # noqa: E731
    g = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    for h in range(H0, H1 + 1, 2):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(h * 60):.1f}" y2="{y(h * 60):.1f}" stroke="#E7EEF2"/>')
        g.append(f'<text x="{L - 6}" y="{y(h * 60) + 4:.1f}" font-size="10" fill="{SLATE}" text-anchor="end" direction="ltr">{h:02d}:00</text>')
    for i, r in enumerate(rows):
        cx = x(i) + cw / 2
        if r["weekend"]:
            g.append(f'<rect x="{x(i):.1f}" y="{TOP}" width="{cw:.1f}" height="{H - TOP - B}" fill="{WASH}"/>')
        s, e = r["start"], r["last_call"]
        if s and e and _mins(e) > _mins(s):
            g.append(f'<rect x="{cx - cw * 0.3:.1f}" y="{y(_mins(s)):.1f}" width="{cw * 0.6:.1f}" '
                     f'height="{y(_mins(e)) - y(_mins(s)):.1f}" rx="2" fill="{STEEL}"/>')
        if r["login_at"]:
            g.append(f'<circle cx="{cx:.1f}" cy="{y(_mins(r["login_at"])):.1f}" r="3.2" fill="{NAVY}"/>')
        if not r["present"]:
            g.append(f'<text x="{cx:.1f}" y="{y(14 * 60):.1f}" font-size="9" fill="{SLATE}" text-anchor="middle">—</text>')
    _axis_labels(g, rows, x, cw, H - B + 14, H - B + 30)
    g.append("</svg>")
    return "".join(g)


def _weekly_chart(weeks) -> str:
    W, H, L, R, TOP, B = 760, 230, 46, 16, 22, 40
    cw = (W - L - R) / len(weeks)
    vmax = max(max(wk["out"] for wk in weeks), 1)
    step = 500 if vmax > 1500 else (100 if vmax > 300 else 50)
    top = ((vmax + step - 1) // step) * step
    y = lambda v: TOP + (H - TOP - B) * (1 - v / top)                     # noqa: E731
    x = lambda i: W - R - (i + 1) * cw                                    # noqa: E731
    g = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    for v in range(0, top + 1, step):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#E7EEF2"/>')
        g.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" font-size="10" fill="{SLATE}" text-anchor="end" direction="ltr">{v}</text>')
    for i, wk in enumerate(weeks):
        bw, cx = min(cw * 0.28, 60), x(i) + cw / 2
        for j, (v, col) in enumerate(((wk["out"], MIST), (wk["answered"], NAVY))):
            bx = cx + (0.04 * cw if j == 0 else -bw - 0.04 * cw)
            g.append(f'<rect x="{bx:.1f}" y="{y(v):.1f}" width="{bw:.1f}" height="{y(0) - y(v):.1f}" rx="2" fill="{col}"/>')
            g.append(f'<text x="{bx + bw / 2:.1f}" y="{y(v) - 4:.1f}" font-size="10" fill="{NAVY if col == NAVY else SLATE}" '
                     f'text-anchor="middle" direction="ltr" font-weight="700">{v}</text>')
        g.append(f'<text x="{cx:.1f}" y="{H - B + 16}" font-size="10" fill="{NAVY}" text-anchor="middle">'
                 f'{_html.escape(_short(wk["from"]) + " – " + _short(wk["to"]))}</text>')
        if wk["partial"]:
            g.append(f'<text x="{cx:.1f}" y="{H - B + 30}" font-size="9" fill="{SLATE}" text-anchor="middle">(أسبوع غير مكتمل)</text>')
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="{MIST}"/></svg>')
    return "".join(g)


def _legend(items) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{c}{";border-radius:50%" if dot else ""}"></i>{t}</span>' for t, c, dot in items) + "</div>"


def _kpi(label, value, sub="") -> str:
    return f'<div class="kpi"><div class="kl">{label}</div><div class="kv">{value}</div><div class="ks">{sub}</div></div>'


CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans Arabic", "SF Arabic", "Geeza Pro", "Segoe UI", Tahoma, sans-serif; color: #2E3450;
       font-size: 11px; line-height: 1.55; margin: 0; letter-spacing: 0; background: #fff; }
.sheet { max-width: 800px; margin: 0 auto; padding: 16px; }
@media print { .sheet { padding: 0; max-width: none; } }
.num { font-variant-numeric: tabular-nums; direction: ltr; unicode-bidi: isolate; }
h1 { font-size: 22px; color: #070A4B; margin: 0 0 2px; }
h2 { font-size: 15px; color: #070A4B; margin: 18px 0 6px; padding-bottom: 4px; border-bottom: 2px solid #070A4B; }
.head { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1px solid #D7E1E8; padding-bottom: 8px; }
.meta { color: #6B7482; font-size: 11px; }
.brand { text-align: left; color: #070A4B; font-weight: 700; font-size: 12px; }
.brand small { display: block; color: #6B7482; font-weight: 400; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 12px; }
.kpi { border: 1px solid #D7E1E8; border-radius: 8px; padding: 8px 10px; }
.kl { color: #6B7482; font-size: 10.5px; }
.kv { color: #070A4B; font-size: 19px; font-weight: 800; font-variant-numeric: tabular-nums; }
.ks { color: #8A929F; font-size: 10px; min-height: 13px; }
.card { border: 1px solid #D7E1E8; border-radius: 8px; padding: 10px 12px 6px; margin-top: 6px; break-inside: avoid; }
.card .t { font-weight: 700; color: #070A4B; font-size: 12px; }
.card .s { color: #6B7482; font-size: 10.5px; }
.legend { display: flex; gap: 14px; font-size: 10.5px; color: #3B4250; margin: 4px 0; flex-wrap: wrap; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-inline-end: 5px; vertical-align: -1px; }
table { width: 100%; border-collapse: collapse; font-size: 10px; margin-top: 6px; }
th { background: #DFEEF2; color: #070A4B; font-weight: 700; padding: 5px 4px; text-align: right; border-bottom: 1px solid #B8C4CE; }
td { padding: 3.5px 4px; border-bottom: 1px solid #E7EEF2; white-space: nowrap; }
tr.wkend td { background: #FAFCFD; color: #6B7482; }
tr.total td { font-weight: 800; color: #070A4B; border-top: 1.5px solid #070A4B; background: #F2F6F8; }
.yes { color: #070A4B; font-weight: 700; } .no { color: #B25A00; font-weight: 700; }
.muted { color: #8A929F; font-weight: 400; }
.notes { color: #515966; font-size: 10px; margin-top: 10px; border-top: 1px solid #E7EEF2; padding-top: 6px; }
.notes li { margin-bottom: 2px; }
.page { break-before: page; }
"""


def render_html(data: dict) -> str:
    rows, weeks, T = data["days"], data["weeks"], data["totals"]
    name = _html.escape(data["user"]["name"] or data["user"]["user_name"] or "—")
    exts = "، ".join(data["extensions"]) or "—"
    period_ = f'{_short(data["from"])} – {_short(data["to"])} {data["to"][:4]}'
    gen = _dt.datetime.fromisoformat(data["generated_at"])
    generated = f"{gen.day} {MONTHS_AR[gen.month]} {gen.year} {gen:%H:%M}"
    rate = f'{T["answer_rate"]:.1f}%' if T["answer_rate"] is not None else "—"
    per_day = f'{T["answered_per_call_day"]:.0f}' if T["answered_per_call_day"] is not None else "—"

    daily = ['<table><thead><tr><th>التاريخ</th><th>اليوم</th><th>الحضور</th><th>تسجيل دخول</th><th>أول مكالمة</th>'
             '<th>آخر مكالمة</th><th>صادرة</th><th>تم الرد (صادرة)</th><th>واردة تم الرد</th><th>إجمالي تم الرد</th>'
             '<th>وقت الحديث</th><th>ملاحظات</th></tr></thead><tbody>']
    for r in rows:
        pres = '<span class="yes">حاضر</span>' if r["present"] else '<span class="no">غائب</span>'
        daily.append(f'<tr{" class=wkend" if r["weekend"] else ""}><td class="num">{r["date"]}</td><td>{r["weekday"]}</td>'
                     f'<td>{pres}</td><td class="num">{r["login_at"] or "—"}</td><td class="num">{r["first_call"] or "—"}</td>'
                     f'<td class="num">{r["last_call"] or "—"}</td><td>{_num(r["out"])}</td><td>{_num(r["out_answered"])}</td>'
                     f'<td>{_num(r["in_answered"])}</td><td><b>{_num(r["answered"])}</b></td>'
                     f'<td class="num">{_talk(r["talk_seconds"])}</td><td>{_num(r["notes"])}</td></tr>')
    daily.append(f'<tr class="total"><td colspan="2">الإجمالي</td><td>{T["present"]} يوم</td><td colspan="3"></td>'
                 f'<td>{_num(T["out"])}</td><td>{_num(T["out_answered"])}</td><td>{_num(T["in_answered"])}</td>'
                 f'<td>{_num(T["answered"])}</td><td class="num">{_talk(T["talk_seconds"])}</td><td>{_num(T["notes"])}</td></tr>'
                 '</tbody></table>')

    weekly = ['<table><thead><tr><th>الأسبوع</th><th>أيام الحضور</th><th>صادرة</th><th>تم الرد (صادرة)</th>'
              '<th>واردة تم الرد</th><th>إجمالي تم الرد</th><th>نسبة الرد</th><th>وقت الحديث</th><th>ملاحظات</th></tr></thead><tbody>']
    for i, wk in enumerate(weeks, 1):
        wr = f'{wk["answer_rate"]:.1f}%' if wk["answer_rate"] is not None else "—"
        part = ' <span class="muted">(غير مكتمل)</span>' if wk["partial"] else ""
        weekly.append(f'<tr><td>الأسبوع {i}: {_short(wk["from"])} – {_short(wk["to"])}{part}</td>'
                      f'<td>{wk["present"]} من {wk["days"]} <span class="muted">(أيام العمل {wk["work_days"]})</span></td>'
                      f'<td>{_num(wk["out"])}</td><td>{_num(wk["out_answered"])}</td><td>{_num(wk["in_answered"])}</td>'
                      f'<td><b>{_num(wk["answered"])}</b></td><td class="num">{wr}</td>'
                      f'<td class="num">{_talk(wk["talk_seconds"])}</td><td>{_num(wk["notes"])}</td></tr>')
    weekly.append(f'<tr class="total"><td>الإجمالي</td><td>{T["present"]} من {T["days"]}</td><td>{_num(T["out"])}</td>'
                  f'<td>{_num(T["out_answered"])}</td><td>{_num(T["in_answered"])}</td><td>{_num(T["answered"])}</td>'
                  f'<td class="num">{rate}</td><td class="num">{_talk(T["talk_seconds"])}</td><td>{_num(T["notes"])}</td></tr>'
                  '</tbody></table>')

    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>تقرير الموظف {name}</title><style>{CSS}</style></head>
<body><div class="sheet">
<div class="head">
  <div><h1>تقرير الموظف: {name}</h1>
    <div class="meta">الحضور اليومي إلى النظام والمكالمات · الفترة: {period_} · اسم المستخدم
      <span class="num">{_html.escape(data["user"]["user_name"] or "")}</span> · التحويلة <span class="num">{exts}</span></div></div>
  <div class="brand"><small>تاريخ الإصدار: {generated}</small></div>
</div>
<div class="kpis">
  {_kpi("أيام الحضور", _num(T["present"]), f'من {T["days"]} يوماً · أيام العمل {T["present_work_days"]} من {T["work_days"]}')}
  {_kpi("المكالمات التي تم الرد عليها", _num(T["answered"]), f'صادرة {T["out_answered"]:,} · واردة {T["in_answered"]:,}')}
  {_kpi("المكالمات الصادرة", _num(T["out"]), f"نسبة الرد {rate}")}
  {_kpi("وقت الحديث", _num(_talk(T["talk_seconds"])), f"متوسط تم الرد يومياً {per_day} مكالمة")}
</div>
<h2>الحضور اليومي إلى النظام</h2>
<div class="card"><div class="t">ساعات العمل الفعلية لكل يوم</div>
  <div class="s">النقطة: أول تسجيل دخول مسجّل في اليوم (قد تكون الجلسة مفتوحة قبلها). العمود: من أول نشاط إلى آخر مكالمة.</div>
  {_legend([("تسجيل دخول مسجّل في اليوم", NAVY, True), ("مدة العمل (أول نشاط ← آخر مكالمة)", STEEL, False), ("الجمعة والسبت", WASH, False)])}
  {_attendance_chart(rows)}</div>
<h2>مكالمات</h2>
<div class="card"><div class="t">المكالمات اليومية</div>
  <div class="s">العمود الفاتح: كل المكالمات الصادرة. العمود الداكن: المكالمات التي تم الرد عليها (صادرة + واردة).</div>
  {_legend([("المكالمات الصادرة", MIST, False), ("تم الرد عليها", NAVY, False), ("الجمعة والسبت", WASH, False)])}
  {_calls_chart(rows)}</div>
<div class="card"><div class="t">المجموع الأسبوعي</div><div class="s">الأسبوع من الأحد إلى السبت.</div>
  {_legend([("المكالمات الصادرة", MIST, False), ("تم الرد عليها", NAVY, False)])}
  {_weekly_chart(weeks)}</div>
<h2 class="page">المجموع الأسبوعي</h2>
{"".join(weekly)}
<h2>التفاصيل اليومية</h2>
{"".join(daily)}
<div class="notes"><b>طريقة الاحتساب</b><ul>
<li>الحضور: يُعدّ اليوم حضوراً إذا سجّل الموظف الدخول إلى النظام في ذلك اليوم أو كان له نشاط فيه (مكالمة أو ملاحظة)، لأن الجلسة قد تبقى مفتوحة من اليوم السابق. «تسجيل دخول» هو أول تسجيل دخول مسجّل في ذلك اليوم (توقيت الرياض)، وقد يأتي بعد أول مكالمة.</li>
<li>المكالمات: من سجلات المقسم لتحويلة الموظف (<span class="num">{exts}</span>)، وتشمل المكالمات من التطبيق والحملات والهاتف المكتبي. «تم الرد» = المكالمات الصادرة التي رد عليها العميل + المكالمات الواردة التي رد عليها الموظف. المكالمات الداخلية بين التحويلات غير محسوبة.</li>
<li>المكالمات الواردة التي لم يُرد عليها (رنين التحويلة ضمن مجموعات الرد) غير محسوبة؛ مجموعها في الفترة <span class="num">{T["in_missed"]:,}</span>.</li>
<li>نسبة الرد = المكالمات الصادرة التي تم الرد عليها ÷ كل المكالمات الصادرة. وقت الحديث للمكالمات التي تم الرد عليها فقط.</li>
<li>الملاحظات: التي كتبها الموظف بنفسه؛ النسخ التلقائية التي يضيفها «تطبيق على جميع حسابات العميل» غير محسوبة (مجموعها <span class="num">{T["note_copies"]:,}</span>).</li>
</ul></div>
</div></body></html>"""


# --------------------------------------------------------------------------- the Word file
def _png(fig) -> bytes:
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return buf.getvalue()


def _chart_images(data) -> dict:
    """The three charts as PNG (numbers only inside the image: the Arabic captions are in the document).
    The x axis runs right to left like the page: the first day on the right."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows, weeks = data["days"], data["weeks"]
    labels = [str(int(r["date"][8:])) for r in rows]
    xs = list(range(len(rows)))
    out = {}

    def base(height=2.6):
        fig, ax = plt.subplots(figsize=(7.2, height))
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(MIST)
        ax.spines["bottom"].set_color(MIST)
        ax.tick_params(colors=SLATE, labelsize=7)
        ax.grid(axis="y", color="#E7EEF2", linewidth=0.6)
        ax.set_axisbelow(True)
        return fig, ax

    fig, ax = base()
    for i, r in enumerate(rows):
        if r["weekend"]:
            ax.axvspan(i - 0.5, i + 0.5, color=WASH, zorder=0)
    ax.bar(xs, [r["out"] for r in rows], width=0.75, color=MIST, zorder=2)
    ax.bar(xs, [r["answered"] for r in rows], width=0.75, color=NAVY, zorder=3)
    ax.set_xticks(xs, labels)
    ax.set_xlim(len(rows) - 0.5, -0.5)
    out["calls"] = _png(fig)

    fig, ax = base()
    for i, r in enumerate(rows):
        if r["weekend"]:
            ax.axvspan(i - 0.5, i + 0.5, color=WASH, zorder=0)
        if r["start"] and r["last_call"] and _mins(r["last_call"]) > _mins(r["start"]):
            s, e = _mins(r["start"]) / 60, _mins(r["last_call"]) / 60
            ax.bar(i, e - s, bottom=s, width=0.6, color=STEEL, zorder=2)
        if r["login_at"]:
            ax.plot(i, _mins(r["login_at"]) / 60, "o", color=NAVY, markersize=3.5, zorder=3)
    ax.set_ylim(23, 7)
    ax.set_yticks(range(7, 24, 2), [f"{h:02d}:00" for h in range(7, 24, 2)])
    ax.set_xticks(xs, labels)
    ax.set_xlim(len(rows) - 0.5, -0.5)
    out["attendance"] = _png(fig)

    fig, ax = base(2.3)
    wx = list(range(len(weeks)))
    ax.bar([i - 0.18 for i in wx], [w["answered"] for w in weeks], width=0.34, color=NAVY, zorder=3)
    ax.bar([i + 0.18 for i in wx], [w["out"] for w in weeks], width=0.34, color=MIST, zorder=2)
    for i, w in enumerate(weeks):
        ax.text(i - 0.18, w["answered"], f'{w["answered"]:,}', ha="center", va="bottom", fontsize=7, color=NAVY)
        ax.text(i + 0.18, w["out"], f'{w["out"]:,}', ha="center", va="bottom", fontsize=7, color=SLATE)
    ax.set_xticks(wx, [f'{w["from"][5:].replace("-", "/")} - {w["to"][5:].replace("-", "/")}' for w in weeks])
    ax.set_xlim(len(weeks) - 0.5, -0.5)
    out["weekly"] = _png(fig)
    return out


def file_name(data: dict, ext: str) -> str:
    who = (data["user"]["name"] or data["user"]["user_name"] or "employee").replace(" ", "-")
    return f"تقرير-الموظف-{who}-{data['from']}-{data['to']}.{ext}"


def render_docx(data: dict) -> bytes:
    """The same report as a Word file (right to left), for editing or forwarding."""
    import io

    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    navy = RGBColor(0x07, 0x0A, 0x4B)
    slate = RGBColor(0x6B, 0x74, 0x82)
    rows, weeks, T = data["days"], data["weeks"], data["totals"]
    name = data["user"]["name"] or data["user"]["user_name"] or "—"
    exts = "، ".join(data["extensions"]) or "—"
    gen = _dt.datetime.fromisoformat(data["generated_at"])
    rate = f'{T["answer_rate"]:.1f}%' if T["answer_rate"] is not None else "—"
    per_day = f'{T["answered_per_call_day"]:.0f}' if T["answered_per_call_day"] is not None else "—"

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.6)
    sec.top_margin = sec.bottom_margin = Cm(1.5)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Arial", Pt(9.5)
    style.element.rPr.rFonts.set(qn("w:cs"), "Arial")
    sec_pr = sec._sectPr
    sec_pr.append(OxmlElement("w:bidi"))

    def rtl(par, align=WD_ALIGN_PARAGRAPH.RIGHT):
        ppr = par._p.get_or_add_pPr()
        ppr.append(OxmlElement("w:bidi"))
        par.alignment = align
        return par

    def run(par, txt, *, bold=False, size=None, color=None):
        r = par.add_run(txt)
        rpr = r._r.get_or_add_rPr()
        rpr.append(OxmlElement("w:rtl"))
        r.bold = bold
        if bold:
            rpr.append(OxmlElement("w:bCs"))
        if size:
            r.font.size = Pt(size)
            szcs = OxmlElement("w:szCs")
            szcs.set(qn("w:val"), str(int(size * 2)))
            rpr.append(szcs)
        if color is not None:
            r.font.color.rgb = color
        return r

    def para(txt="", **kw):
        p = rtl(doc.add_paragraph())
        p.paragraph_format.space_after = Pt(kw.pop("after", 3))
        if txt:
            run(p, txt, **kw)
        return p

    def heading(txt):
        p = para(txt, bold=True, size=13, color=navy, after=4)
        p.paragraph_format.space_before = Pt(10)
        return p

    def table(head, body, total=None, widths=None):
        t = doc.add_table(rows=1, cols=len(head))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t._tbl.tblPr.append(OxmlElement("w:bidiVisual"))
        for i, h in enumerate(head):
            c = t.rows[0].cells[i]
            c.paragraphs[0].text = ""
            run(rtl(c.paragraphs[0]), h, bold=True, size=8, color=navy)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "DFEEF2")
            c._tc.get_or_add_tcPr().append(shd)
        for vals in body + ([total] if total else []):
            cells = t.add_row().cells
            for i, v in enumerate(vals):
                run(rtl(cells[i].paragraphs[0]), str(v), size=8, bold=vals is total)
        if widths:
            t.autofit = False
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Cm(w)
        return t

    para(f"تقرير الموظف: {name}", bold=True, size=18, color=navy, after=1)
    para(f"الحضور اليومي إلى النظام والمكالمات · الفترة: {_short(data['from'])} – {_short(data['to'])} {data['to'][:4]} · "
         f"اسم المستخدم {data['user']['user_name'] or ''} · التحويلة {exts}", size=9, color=slate, after=1)
    para(f"تاريخ الإصدار: {gen.day} {MONTHS_AR[gen.month]} {gen.year} {gen:%H:%M}", size=9, color=slate, after=6)

    for label, value, sub in (
            ("أيام الحضور", f'{T["present"]}', f'من {T["days"]} يوماً · أيام العمل {T["present_work_days"]} من {T["work_days"]}'),
            ("المكالمات التي تم الرد عليها", f'{T["answered"]:,}', f'صادرة {T["out_answered"]:,} · واردة {T["in_answered"]:,}'),
            ("المكالمات الصادرة", f'{T["out"]:,}', f"نسبة الرد {rate}"),
            ("وقت الحديث", _talk(T["talk_seconds"]), f"متوسط تم الرد يومياً {per_day} مكالمة")):
        p = para(after=1)
        run(p, f"{label}: ", bold=True, size=10.5, color=navy)
        run(p, value, bold=True, size=12, color=navy)
        run(p, f"  ({sub})", size=8.5, color=slate)

    imgs = _chart_images(data)
    heading("الحضور اليومي إلى النظام")
    para("ساعات العمل الفعلية لكل يوم: النقطة أول تسجيل دخول مسجّل في اليوم، والعمود من أول نشاط إلى آخر مكالمة. "
         "الأيام من اليمين إلى اليسار، والمظلل الجمعة والسبت.", size=8.5, color=slate)
    rtl(doc.add_paragraph(), WD_ALIGN_PARAGRAPH.CENTER).add_run().add_picture(io.BytesIO(imgs["attendance"]), width=Cm(17.5))
    heading("مكالمات")
    para("المكالمات اليومية: الرمادي كل المكالمات الصادرة، والداكن المكالمات التي تم الرد عليها (صادرة + واردة).",
         size=8.5, color=slate)
    rtl(doc.add_paragraph(), WD_ALIGN_PARAGRAPH.CENTER).add_run().add_picture(io.BytesIO(imgs["calls"]), width=Cm(17.5))
    para("المجموع الأسبوعي (الأسبوع من الأحد إلى السبت): الداكن تم الرد عليها، والرمادي الصادرة.", size=8.5, color=slate)
    rtl(doc.add_paragraph(), WD_ALIGN_PARAGRAPH.CENTER).add_run().add_picture(io.BytesIO(imgs["weekly"]), width=Cm(17.5))

    doc.add_page_break()
    heading("المجموع الأسبوعي")
    body = []
    for i, wk in enumerate(weeks, 1):
        body.append([f'الأسبوع {i}: {_short(wk["from"])} – {_short(wk["to"])}' + (" (غير مكتمل)" if wk["partial"] else ""),
                     f'{wk["present"]} من {wk["days"]}', f'{wk["out"]:,}', f'{wk["out_answered"]:,}', f'{wk["in_answered"]:,}',
                     f'{wk["answered"]:,}', f'{wk["answer_rate"]:.1f}%' if wk["answer_rate"] is not None else "—",
                     _talk(wk["talk_seconds"]), f'{wk["notes"]:,}'])
    table(["الأسبوع", "أيام الحضور", "صادرة", "تم الرد (صادرة)", "واردة تم الرد", "إجمالي تم الرد", "نسبة الرد", "وقت الحديث",
           "ملاحظات"], body,
          ["الإجمالي", f'{T["present"]} من {T["days"]}', f'{T["out"]:,}', f'{T["out_answered"]:,}', f'{T["in_answered"]:,}',
           f'{T["answered"]:,}', rate, _talk(T["talk_seconds"]), f'{T["notes"]:,}'])

    heading("التفاصيل اليومية")
    body = [[r["date"], r["weekday"], "حاضر" if r["present"] else "غائب", r["login_at"] or "—", r["first_call"] or "—",
             r["last_call"] or "—", f'{r["out"]:,}', f'{r["out_answered"]:,}', f'{r["in_answered"]:,}', f'{r["answered"]:,}',
             _talk(r["talk_seconds"]), f'{r["notes"]:,}'] for r in rows]
    table(["التاريخ", "اليوم", "الحضور", "تسجيل دخول", "أول مكالمة", "آخر مكالمة", "صادرة", "تم الرد (صادرة)", "واردة تم الرد",
           "إجمالي تم الرد", "وقت الحديث", "ملاحظات"], body,
          ["الإجمالي", "", f'{T["present"]} يوم', "", "", "", f'{T["out"]:,}', f'{T["out_answered"]:,}',
           f'{T["in_answered"]:,}', f'{T["answered"]:,}', _talk(T["talk_seconds"]), f'{T["notes"]:,}'])

    heading("طريقة الاحتساب")
    for line in (
        "الحضور: يُعدّ اليوم حضوراً إذا سجّل الموظف الدخول إلى النظام في ذلك اليوم أو كان له نشاط فيه (مكالمة أو ملاحظة)، لأن "
        "الجلسة قد تبقى مفتوحة من اليوم السابق. «تسجيل دخول» هو أول تسجيل دخول مسجّل في ذلك اليوم (توقيت الرياض)، وقد يأتي بعد أول مكالمة.",
        f"المكالمات: من سجلات المقسم لتحويلة الموظف ({exts})، وتشمل المكالمات من التطبيق والحملات والهاتف المكتبي. «تم الرد» = "
        "المكالمات الصادرة التي رد عليها العميل + المكالمات الواردة التي رد عليها الموظف. المكالمات الداخلية غير محسوبة.",
        f"المكالمات الواردة التي لم يُرد عليها (رنين ضمن مجموعات الرد) غير محسوبة؛ مجموعها في الفترة {T['in_missed']:,}.",
        "نسبة الرد = المكالمات الصادرة التي تم الرد عليها ÷ كل المكالمات الصادرة. وقت الحديث للمكالمات التي تم الرد عليها فقط.",
        f"الملاحظات: التي كتبها الموظف بنفسه؛ النسخ التلقائية من «تطبيق على جميع حسابات العميل» غير محسوبة (مجموعها {T['note_copies']:,}).",
    ):
        para("• " + line, size=8.5, color=slate)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
