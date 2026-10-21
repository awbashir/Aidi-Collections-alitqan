"""Team performance dashboard ("لوحة أداء الفريق", customer request 2026-10-20): the calls and
the client status updates (notes) of each user over a period, for supervisors and admins.

Scope, decided here for every request (the page's filters can only narrow it; a user outside it
is refused with 403 `not_in_scope`), the same rule as the call monitor (services/calling/monitor.py
scope()):

* a user whose role is `SuperAdmin` sees every user, every extension and every case;
* a supervisor (an active `supervisors` row) sees the users of their team (active
  `supervisor_users` rows, and since 2026-10-21 the users of their contracts) and themselves, and only the notes those users wrote on cases in the
  supervisor's own case scope (money.scope_sql: the cases of the team, or of the supervisor's
  contracts);
* anyone else with access sees only themselves (their own notes on their own cases).

Period: Asia/Riyadh civil dates `from`..`to` (inclusive, default today, 31 days at most, like the
call monitor whose PBX call records it reads); `granularity` hour (default for one day) | day.

Calls come from the call monitor's own classification (monitor._rows_for + monitor._merge: the
PBX call records merged per call and agent, click-to-call answered from the app's call_attempts
evidence), attributed to the CURRENT owner of each extension. Per user: outbound calls placed
(`calls_made`), of them answered / not answered, inbound answered / missed, internal, talk time
of answered calls. When the PBX call records cannot be read, the call figures are null
(`calls.available` false): unknown stays unknown, the notes are still shown. The contract filter
does not apply to calls (a PBX record carries no contract): `calls.contract_filtered` is false.

Status updates are "Comments" rows written by the user in the period, aggregated in SQL (one
scan, GROUPING SETS; index ix_comments_user_created / ix_comments_create_at):
* deleted notes (is_deleted) are excluded;
* notes imported from the old system (text starting with previous_status_import.MARKER,
  "حالة سابقة (من النظام السابق)") are excluded: they are history, not work done in the period;
* `group=status` (default) groups by the main status (comment_status_id); `group=sub_status`
  by the sub-status ("Comments".comment_sub_status_id, unmapped in models.py), a note without one
  under `none:<main status id>` (its main status, flagged `no_sub`);
* `clients` = distinct cases the user updated; `top_status` = the user's most frequent main status.
Promises created: "paymentDates" rows created in the period (not deleted), same scope and
filters; a promise is not money recovered.

Download (export): the per-user table with totals, statuses per user, status updates over time
and the period, as XLSX (core.reports.xlsx); the route writes an audit event.
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid
from decimal import Decimal

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling import monitor
from services.calling.common import USER_NAME_SQL, iso, me, sess

MAX_DAYS = monitor.MAX_DAYS
GROUPS = ("status", "sub_status")
MAX_USERS_FILTER = 200
NONE_PREFIX = "none:"
CALL_KEYS = ("calls_made", "answered", "not_answered", "inbound_answered", "inbound_missed", "internal",
             "talk_seconds", "handled")


def _marker() -> str:
    from services.previous_status_import import MARKER
    return MARKER


# --------------------------------------------------------------------------- scope + filters
def scope(user_id=None) -> dict:
    """{kind: all|team|own, users: set of user ids | None (every user), calls: the call
    monitor's scope (extensions and their current owners)}."""
    uid = str(user_id or me())
    calls = monitor.scope(uid)
    if calls["kind"] == "all":
        return {"kind": "all", "me": uid, "users": None, "calls": calls}
    team = {r[0] for r in sess().execute(text("""
        SELECT su.user_id::text FROM supervisors s
          JOIN supervisor_users su ON su.supervisor_id = s.id AND su.is_active
         WHERE s.user_id = CAST(:u AS uuid) AND s.is_active
        UNION
        -- 2026-10-21: and the users of the supervisor's contracts (users.contract_id), as the case
        -- scope already counts supervised contracts (services/clients_scope.py); the old system's
        -- supervisors are linked to contracts, hardly to users
        SELECT tu.id::text FROM supervisors s
          JOIN supervisor_contracts sc ON sc.supervisor_id = s.id AND sc.is_active
          JOIN users tu ON tu.contract_id = sc.contract_id AND tu.is_active
         WHERE s.user_id = CAST(:u AS uuid) AND s.is_active"""), {"u": uid})}
    team.add(uid)
    return {"kind": calls["kind"], "me": uid, "users": team, "calls": calls}


def _uuid(value, field) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be a UUID", {field: "invalid"}) from None


def _list_arg(args, name) -> list[str]:
    """`?user_id=a&user_id=b` or `?user_id=a,b` -> ['a', 'b']."""
    raw = args.getlist(name) if hasattr(args, "getlist") else args.get(name)
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [p.strip() for v in raw for p in str(v).split(",") if p.strip()]


def _filters(args, sc) -> dict:
    users = []
    for v in _list_arg(args, "user_id"):
        u = _uuid(v, "user_id")
        if u not in users:
            users.append(u)
    if len(users) > MAX_USERS_FILTER:
        raise ApiError(422, VALIDATION_FAILED, f"at most {MAX_USERS_FILTER} users", {"user_id": "too_many"})
    if sc["users"] is not None and any(u not in sc["users"] for u in users):
        raise ApiError(403, "not_in_scope", "this user is not in your scope", {"user_id": "not_in_scope"})
    contract = args.get("contract_id")
    contract = _uuid(contract, "contract_id") if contract not in (None, "") else None
    if contract and sess().execute(text("SELECT 1 FROM contracts WHERE id = CAST(:c AS uuid)"),
                                   {"c": contract}).first() is None:
        raise ApiError(422, VALIDATION_FAILED, "unknown contract", {"contract_id": "not_found"})
    group = args.get("group") or "status"
    if group not in GROUPS:
        raise ApiError(422, VALIDATION_FAILED, "group must be status or sub_status", {"group": "invalid"})
    return {"users": users, "contract": contract, "group": group}


def _period(args) -> dict:
    start, end, lo, hi = monitor._range(args)             # Riyadh days, default today, 31 days max
    gran = args.get("granularity") or ("hour" if start == end else "day")
    if gran not in ("hour", "day"):
        raise ApiError(422, VALIDATION_FAILED, "granularity must be hour or day", {"granularity": "invalid"})
    utc_lo, utc_hi = riyadh.range_bounds(start, end)
    fmt = "%Y-%m-%d %H:00" if gran == "hour" else "%Y-%m-%d"
    buckets, t = [], lo
    while t < hi:
        buckets.append(t.strftime(fmt))
        t += _dt.timedelta(hours=1) if gran == "hour" else _dt.timedelta(days=1)
    return {"from": start, "to": end, "cdr_lo": lo, "cdr_hi": hi, "lo": utc_lo, "hi": utc_hi,
            "granularity": gran, "buckets": buckets}


def _note_where(sc, flt, alias="m", case="c") -> tuple[str, dict]:
    """The user + case scope + contract filter over `<alias>` (a note or promise) and `<case>` (clints)."""
    from services import money
    parts, params = [], {}
    users = flt["users"] or (sorted(sc["users"]) if sc["users"] is not None else None)
    if users is not None:
        parts.append(f"{alias}.user_id = ANY(CAST(:td_users AS uuid[]))")
        params["td_users"] = users
    if sc["kind"] != "all":
        pred, p = money.scope_sql(money.caller_scope(sc["me"]), alias=case, prefix="td_scope")
        parts.append(pred)
        params.update(p)
    if flt["contract"]:
        parts.append(f"{case}.portfolios_id IN (SELECT tpf.id FROM portfolios tpf "
                     f"WHERE tpf.contract_id = CAST(:td_contract AS uuid))")
        params["td_contract"] = flt["contract"]
    return (" AND ".join(parts) or "TRUE"), params


# --------------------------------------------------------------------------- notes
def _notes(sc, flt, per) -> list[dict]:
    """One scan of the period's notes, grouped (user, status, sub-status, bucket), (user) and ()."""
    where, params = _note_where(sc, flt)
    fmt = "YYYY-MM-DD HH24:00" if per["granularity"] == "hour" else "YYYY-MM-DD"
    marker = _marker()
    rows = sess().execute(text(f"""
        WITH n AS (
            SELECT m.user_id, m.clint_id, m.comment_status_id, m.comment_sub_status_id,
                   to_char(m.create_at AT TIME ZONE 'Asia/Riyadh', :bucket_fmt) AS bucket
              FROM "Comments" m
              JOIN clints c ON c.id = m.clint_id
             WHERE m.is_deleted = false
               AND m.create_at >= :lo AND m.create_at < :hi
               AND left(m.comment, :mlen) <> :marker
               AND {where}
        )
        SELECT user_id::text AS user_id, comment_status_id::text AS status_id,
               comment_sub_status_id::text AS sub_status_id, bucket,
               count(*) AS notes, count(DISTINCT clint_id) AS clients,
               GROUPING(user_id, comment_status_id, comment_sub_status_id, bucket) AS g
          FROM n
         GROUP BY GROUPING SETS ((user_id, comment_status_id, comment_sub_status_id, bucket), (user_id), ())
    """), {"lo": per["lo"], "hi": per["hi"], "mlen": len(marker), "marker": marker, "bucket_fmt": fmt,
          **params}).mappings().all()
    return [dict(r) for r in rows]


def _promises(sc, flt, per) -> dict:
    """user id -> (count, amount) of the promises created in the period."""
    where, params = _note_where(sc, flt, alias="pd")
    rows = sess().execute(text(f"""
        SELECT pd.user_id::text, count(*), round(coalesce(sum(pd.amount), 0)::numeric, 2)
          FROM "paymentDates" pd
          JOIN clints c ON c.id = pd.clint_id
         WHERE pd.is_deleted = false AND pd.create_at >= :lo AND pd.create_at < :hi AND {where}
         GROUP BY pd.user_id"""), {"lo": per["lo"], "hi": per["hi"], **params}).all()
    return {r[0]: (int(r[1]), Decimal(r[2])) for r in rows}


def _statuses(ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = sess().execute(text("""
        SELECT s.id::text AS id, s.name, s.color, s.parent_id::text AS parent_id, p.name AS parent_name
          FROM "CommentStatuses" s LEFT JOIN "CommentStatuses" p ON p.id = s.parent_id
         WHERE s.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings()
    return {r["id"]: dict(r) for r in rows}


def _status_key(group, status_id, sub_id) -> str:
    if group == "status":
        return status_id
    return sub_id or f"{NONE_PREFIX}{status_id}"


def _legend(key, names) -> dict:
    if key.startswith(NONE_PREFIX):
        main = names.get(key[len(NONE_PREFIX):]) or {}
        return {"key": key, "id": None, "name": main.get("name"), "color": main.get("color"),
                "parent_id": main.get("id"), "parent_name": main.get("name"), "no_sub": True}
    s = names.get(key) or {}
    return {"key": key, "id": key, "name": s.get("name"), "color": s.get("color"),
            "parent_id": s.get("parent_id"), "parent_name": s.get("parent_name"), "no_sub": False}


# --------------------------------------------------------------------------- calls
def _calls(sc, flt, per, rows=None) -> tuple[dict, dict]:
    """(meta, per-user call figures keyed by user id, or `ext:<ext>` for an extension nobody owns)."""
    csc = sc["calls"]
    owners = csc["owners"]
    if flt["users"]:
        wanted = set(flt["users"])
        exts = {e for e, o in owners.items() if o["id"] in wanted}
        csc = {**csc, "extensions": exts}
    meta = {"available": True, "error": None, "truncated": False, "contract_filtered": False}
    try:
        cdr = monitor._rows_for(csc, per["cdr_lo"], per["cdr_hi"], rows)
    except ApiError as e:
        if e.status != 503:
            raise
        return {**meta, "available": False, "error": e.code}, {}
    meta["truncated"] = len(cdr) > monitor.MAX_CDR_ROWS
    out: dict[str, dict] = {}
    for c in monitor._merge(cdr, csc["extensions"]):
        ext = c["extension"]
        owner = (owners.get(ext) or {}).get("id")
        u = out.setdefault(owner or f"ext:{ext}", {"extensions": set(), **{k: 0 for k in CALL_KEYS}})
        u["extensions"].add(ext)
        if c["direction"] == "outbound":
            u["calls_made"] += 1
            u["answered" if c["answered"] else "not_answered"] += 1
        elif c["direction"] == "inbound":
            u["inbound_answered" if c["answered"] else "inbound_missed"] += 1
        else:
            u["internal"] += 1
        u["talk_seconds"] += c["talk_seconds"] if c["answered"] else 0
    for u in out.values():
        u["handled"] = u["calls_made"] + u["inbound_answered"] + u["internal"]
    return meta, out


# --------------------------------------------------------------------------- the dashboard
def _user_rows(ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = sess().execute(text(f"""
        SELECT u.id::text, {USER_NAME_SQL.format(a='u')}, u.is_active FROM users u
         WHERE u.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).all()
    return {r[0]: {"name": r[1] or None, "is_active": bool(r[2])} for r in rows}


def _options(sc) -> dict:
    owners = sc["calls"]["owners"]
    exts: dict[str, list] = {}
    for e, o in owners.items():
        if o["id"]:
            exts.setdefault(o["id"], []).append(e)
    if sc["users"] is None:
        rows = sess().execute(text(f"""
            SELECT u.id::text, {USER_NAME_SQL.format(a='u')}, u.is_active FROM users u
             WHERE u.is_active ORDER BY 2, 1""")).all()
    else:
        rows = sess().execute(text(f"""
            SELECT u.id::text, {USER_NAME_SQL.format(a='u')}, u.is_active FROM users u
             WHERE u.id = ANY(CAST(:ids AS uuid[])) ORDER BY 2, 1"""), {"ids": sorted(sc["users"])}).all()
    contracts = sess().execute(text("SELECT id::text, name, is_active FROM contracts ORDER BY is_active DESC, name")).all()
    return {"users": [{"id": r[0], "name": r[1] or None, "is_active": bool(r[2]),
                       "extensions": sorted(exts.get(r[0], []), key=lambda e: (len(e), e))} for r in rows],
            "contracts": [{"id": r[0], "name": r[1], "is_active": bool(r[2])} for r in contracts]}


def _rate(answered, made):
    return round(answered / made, 4) if made else None


def dashboard(args, user_id=None, rows=None) -> dict:
    sc = scope(user_id)
    flt = _filters(args, sc)
    per = _period(args)
    group = flt["group"]
    call_meta, calls = _calls(sc, flt, per, rows)
    raw = _notes(sc, flt, per)
    names = _statuses([r["status_id"] for r in raw] + [r["sub_status_id"] for r in raw])
    promises = _promises(sc, flt, per)

    per_user: dict[str, dict] = {}
    series = {b: {"bucket": b, "total": 0, "by_status": {}} for b in per["buckets"]}
    team_by: dict[str, int] = {}
    totals_notes = totals_clients = 0

    def user(key):
        return per_user.setdefault(key, {"notes": 0, "clients": 0, "by_status": {}, "by_main": {}})

    for r in raw:
        if r["g"] == 15:                                   # () : the whole team
            totals_notes, totals_clients = int(r["notes"]), int(r["clients"])
        elif r["g"] == 7:                                  # (user)
            u = user(r["user_id"])
            u["notes"], u["clients"] = int(r["notes"]), int(r["clients"])
        else:
            n = int(r["notes"])
            key = _status_key(group, r["status_id"], r["sub_status_id"])
            u = user(r["user_id"])
            u["by_status"][key] = u["by_status"].get(key, 0) + n
            u["by_main"][r["status_id"]] = u["by_main"].get(r["status_id"], 0) + n
            team_by[key] = team_by.get(key, 0) + n
            b = series.get(r["bucket"])
            if b is not None:
                b["total"] += n
                b["by_status"][key] = b["by_status"].get(key, 0) + n

    keys = set(per_user) | set(calls) | set(promises) | set(flt["users"])
    if sc["users"] is not None and not flt["users"]:
        keys |= sc["users"]                                # a team member with no activity is a row too
    people = _user_rows([k for k in keys if not k.startswith("ext:")])
    out = []
    for k in keys:
        n = per_user.get(k) or {"notes": 0, "clients": 0, "by_status": {}, "by_main": {}}
        c = calls.get(k)
        p = promises.get(k, (0, Decimal(0)))
        top = max(n["by_main"].items(), key=lambda kv: (kv[1], kv[0]), default=None)
        is_ext = k.startswith("ext:")
        row = {"user_id": None if is_ext else k,
               "name": None if is_ext else (people.get(k) or {}).get("name"),
               "is_active": None if is_ext else (people.get(k) or {}).get("is_active"),
               "extensions": sorted(c["extensions"] if c else
                                    [e for e, o in sc["calls"]["owners"].items() if o["id"] == k],
                                    key=lambda e: (len(e), e))}
        for f in CALL_KEYS:
            row[f] = (c or {}).get(f, 0) if call_meta["available"] else None
        row["answer_rate"] = _rate(row["answered"], row["calls_made"]) if call_meta["available"] else None
        row.update({"notes": n["notes"], "clients": n["clients"], "by_status": n["by_status"],
                    "top_status": ({"id": top[0], "name": (names.get(top[0]) or {}).get("name"),
                                    "color": (names.get(top[0]) or {}).get("color"), "count": top[1]}
                                   if top else None),
                    "promises": p[0], "promises_amount": str(p[1].quantize(Decimal("0.01")))})
        out.append(row)
    out.sort(key=lambda u: (-(u["notes"] + (u["handled"] or 0)), u["name"] or "~", u["user_id"] or ""))

    legend = sorted((dict(_legend(k, names), count=v) for k, v in team_by.items()),
                    key=lambda s: (-s["count"], s["name"] or ""))
    avail = call_meta["available"]
    tot = {f: (sum(u[f] for u in out) if avail else None) for f in CALL_KEYS}
    tot.update({"answer_rate": _rate(tot["answered"], tot["calls_made"]) if avail else None,
                "notes": totals_notes, "clients": totals_clients,
                "promises": sum(u["promises"] for u in out),
                "promises_amount": str(sum((Decimal(u["promises_amount"]) for u in out), Decimal(0)).quantize(Decimal("0.01")))})
    return {"scope": sc["kind"], "from": per["from"].isoformat(), "to": per["to"].isoformat(),
            "granularity": per["granularity"], "group": group, "contract_id": flt["contract"],
            "user_ids": flt["users"], "max_days": MAX_DAYS, "options": _options(sc),
            "calls": call_meta, "totals": tot, "users": out, "statuses": legend,
            "series": list(series.values())}


# --------------------------------------------------------------------------- download
# --------------------------------------------------------------------------- one user's client updates
UPDATES_PAGE_SIZE = 50
UPDATES_MAX_PAGE_SIZE = 200
CALL_BEFORE = _dt.timedelta(hours=3)          # the call an update reports is at most 3 h before it
CALL_AFTER = _dt.timedelta(minutes=10)        # (or a few minutes after: the note saved during the call)


def _page(args, name, default, hi) -> int:
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        n = int(str(raw))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{name} must be a whole number", {name: "invalid"}) from None
    if n < 1 or n > hi:
        raise ApiError(422, VALIDATION_FAILED, f"{name} must be 1..{hi}", {name: "out_of_range"})
    return n


def user_updates(args, user_id=None, rows=None) -> dict:
    """GET /v2/dashboards/team/user-updates?user_id=<one>&from&to&contract_id&page&page_size
    (customer request 2026-10-21): the clients one user updated in the period, newest first, one
    row per update (note) with the status it set and the call it reports: that user's call to one of
    the client's numbers (the note's contacted number first, else the client's contacts) at most
    3 h before the update (or 10 min after), the latest such call. No such call = `call: null`
    (unknown, never guessed); PBX records unavailable = `calls.available: false`."""
    sc = scope(user_id)
    ids = _list_arg(args, "user_id")
    if len(ids) != 1:
        raise ApiError(422, VALIDATION_FAILED, "exactly one user_id is required", {"user_id": "required"})
    flt = _filters(args, sc)
    per = _period(args)
    target = flt["users"][0]
    page = _page(args, "page", 1, 1_000_000)
    size = _page(args, "page_size", UPDATES_PAGE_SIZE, UPDATES_MAX_PAGE_SIZE)
    where, params = _note_where(sc, flt)
    marker = _marker()
    base = f"""
        FROM "Comments" m
        JOIN clints c ON c.id = m.clint_id
       WHERE m.is_deleted = false AND m.user_id = CAST(:target AS uuid)
         AND m.create_at >= :lo AND m.create_at < :hi
         AND left(m.comment, :mlen) <> :marker
         AND {where}"""
    qp = {"target": target, "lo": per["lo"], "hi": per["hi"], "mlen": len(marker), "marker": marker, **params}
    agg = sess().execute(text(f"SELECT count(*), count(DISTINCT m.clint_id) {base}"), qp).one()
    notes = sess().execute(text(f"""
        SELECT m.id::text AS id, m.create_at, m.clint_id::text AS case_id, c.name AS client_name,
               c."accountNumber" AS account_number, c."identityNumber" AS identity_number,
               m.comment_status_id::text AS status_id, m.comment_sub_status_id::text AS sub_status_id,
               m."contactNumber" AS contact_number, left(m.comment, 300) AS comment
        {base}
        ORDER BY m.create_at DESC, m.id DESC LIMIT :lim OFFSET :off"""),
        {**qp, "lim": size, "off": (page - 1) * size}).mappings().all()
    names = _statuses([n["status_id"] for n in notes] + [n["sub_status_id"] for n in notes])

    # the user's calls of the period (their current extensions), one item per call
    owners = sc["calls"]["owners"]
    exts = {e for e, o in owners.items() if o["id"] == target}
    calls_meta = {"available": True, "error": None, "truncated": False, "extensions": sorted(exts)}
    items = []
    if exts and notes:
        csc = {**sc["calls"], "extensions": exts}
        try:
            cdr = monitor._rows_for(csc, per["cdr_lo"] - CALL_BEFORE, per["cdr_hi"] + CALL_AFTER, rows)
            calls_meta["truncated"] = len(cdr) > monitor.MAX_CDR_ROWS
            items = [c for c in monitor._merge(cdr, exts) if c["direction"] != "internal" and c["number"]]
        except ApiError as e:
            if e.status != 503:
                raise
            calls_meta.update(available=False, error=e.code)
    # every number of the page's clients (and the notes' contacted numbers), normalised by the DB
    keys_of_case: dict[str, set] = {}
    if items:
        idents = sorted({n["identity_number"] for n in notes if n["identity_number"]})
        crow = sess().execute(text("""
            SELECT cc."identityNumber", cawp_norm_mobile(cc.contact) FROM "ClintContacts" cc
             WHERE cc.is_deleted = false AND cc."identityNumber" = ANY(CAST(:ids AS text[]))
               AND cawp_norm_mobile(cc.contact) IS NOT NULL"""), {"ids": idents}).all() if idents else []
        by_ident: dict[str, set] = {}
        for ident, key in crow:
            by_ident.setdefault(ident, set()).add(key)
        nums = sorted({c["number"] for c in items} | {n["contact_number"] for n in notes if n["contact_number"]})
        norm = dict(sess().execute(text("""SELECT m.n, cawp_norm_mobile(m.n) FROM unnest(CAST(:nums AS text[])) AS m(n)"""),
                                   {"nums": nums}).all()) if nums else {}
        for n in notes:
            keys_of_case[n["id"]] = set(by_ident.get(n["identity_number"], set()))
            own = norm.get(n["contact_number"]) if n["contact_number"] else None
            if own:
                keys_of_case[n["id"]].add(own)
        for c in items:
            c["key"] = norm.get(c["number"])
            c["at_local"] = c["at"] if c["at"].tzinfo is None else c["at"].astimezone(riyadh.RIYADH).replace(tzinfo=None)

    out = []
    for n in notes:
        at_local = riyadh.to_riyadh(n["create_at"]).replace(tzinfo=None)       # the PBX writes Riyadh wall clock
        wanted = keys_of_case.get(n["id"]) or set()
        best = None
        for c in items:
            if c.get("key") in wanted and at_local - CALL_BEFORE <= c["at_local"] <= at_local + CALL_AFTER:
                if best is None or c["at_local"] > best["at_local"]:
                    best = c
        st, sub = names.get(n["status_id"]) or {}, names.get(n["sub_status_id"]) or {}
        out.append({
            "note_id": n["id"], "at": iso(n["create_at"]),
            "client": {"case_id": n["case_id"], "name": n["client_name"], "account_number": n["account_number"]},
            "status": {"id": n["status_id"], "name": st.get("name"), "color": st.get("color")} if n["status_id"] else None,
            "sub_status": {"id": n["sub_status_id"], "name": sub.get("name"), "color": sub.get("color")}
                          if n["sub_status_id"] else None,
            "comment": n["comment"],
            "call": None if best is None else {
                "at": best["at"].replace(tzinfo=riyadh.RIYADH).isoformat() if best["at"].tzinfo is None else best["at"].isoformat(),
                "direction": best["direction"], "answered": bool(best["answered"]), "result": best["result"] or None,
                "talk_seconds": int(best["talk_seconds"] or 0), "ring_seconds": int(best["ring_seconds"] or 0),
                "extension": best["extension"], "recorded": bool(best["recorded"])},
        })
    who = _user_rows([target]).get(target) or {}
    return {"scope": sc["kind"], "user": {"id": target, "name": who.get("name"), "extensions": sorted(exts)},
            "from": per["from"].isoformat(), "to": per["to"].isoformat(), "contract_id": flt["contract"],
            "calls": calls_meta, "updates": int(agg[0]), "clients": int(agg[1]),
            "items": out, "total": int(agg[0]), "page": page, "page_size": size}


SCOPE_AR = {"all": "كل المستخدمين", "team": "الفريق", "own": "أنا فقط"}


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _num(v):
    return ("int", v) if v is not None else ("s", "غير معروف")


def _status_label(s) -> str:
    name = s["name"] or "—"
    if s["no_sub"]:
        return f"{name} (بدون حالة فرعية)"
    if s["parent_name"]:
        return f"{s['parent_name']} / {name}"
    return name


def export(args, dest_dir: str, user_id=None, rows=None) -> tuple[str, str, int]:
    """XLSX: per-user table + totals, statuses per user, updates over time, the period."""
    from core.reports.xlsx import Workbook
    data = dashboard(args, user_id=user_id, rows=rows)
    name = f"team-dashboard-{data['from']}_{data['to']}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("أداء الموظفين", freeze_header=True)
    sh.append([("s", h) for h in ("الموظف", "التحويلة", "المكالمات الصادرة", "تم الرد", "لم يتم الرد",
                                  "نسبة الرد %", "واردة تم الرد عليها", "واردة فائتة", "مدة المحادثة",
                                  "مدة المحادثة (ثانية)", "تحديثات الحالة", "عملاء تم تحديثهم",
                                  "الحالة الأكثر", "وعود السداد", "مبلغ الوعود")], header=True)

    def rate(v):
        return ("money", Decimal(str(round(v * 100, 2)))) if v is not None else None

    for u in data["users"]:
        top = u["top_status"]
        sh.append([("s", u["name"] or (f"تحويلة {u['extensions'][0]}" if u["extensions"] else "—")),
                   ("s", "، ".join(u["extensions"]) or None), _num(u["calls_made"]), _num(u["answered"]),
                   _num(u["not_answered"]), rate(u["answer_rate"]), _num(u["inbound_answered"]),
                   _num(u["inbound_missed"]), ("s", _clock(u["talk_seconds"]) if u["talk_seconds"] is not None else "غير معروف"),
                   _num(u["talk_seconds"]), ("int", u["notes"]), ("int", u["clients"]),
                   ("s", f"{top['name'] or '—'} ({top['count']})" if top else None), ("int", u["promises"]),
                   ("money", Decimal(u["promises_amount"]))])
    t = data["totals"]
    sh.append([("s", "الإجمالي"), None, _num(t["calls_made"]), _num(t["answered"]), _num(t["not_answered"]),
               rate(t["answer_rate"]), _num(t["inbound_answered"]), _num(t["inbound_missed"]),
               ("s", _clock(t["talk_seconds"]) if t["talk_seconds"] is not None else "غير معروف"),
               _num(t["talk_seconds"]), ("int", t["notes"]), ("int", t["clients"]), None, ("int", t["promises"]),
               ("money", Decimal(t["promises_amount"]))], header=True)

    st = wb.add_sheet("الحالات حسب الموظف", freeze_header=True)
    legend = data["statuses"]
    st.append([("s", "الموظف"), ("s", "الإجمالي")] + [("s", _status_label(s)) for s in legend], header=True)
    for u in data["users"]:
        if not u["notes"]:
            continue
        st.append([("s", u["name"] or "—"), ("int", u["notes"])]
                  + [("int", u["by_status"].get(s["key"], 0)) for s in legend])
    st.append([("s", "الإجمالي"), ("int", t["notes"])] + [("int", s["count"]) for s in legend], header=True)

    ts = wb.add_sheet("التحديثات حسب الوقت", freeze_header=True)
    ts.append([("s", "الساعة" if data["granularity"] == "hour" else "اليوم"), ("s", "الإجمالي")]
              + [("s", _status_label(s)) for s in legend], header=True)
    for b in data["series"]:
        ts.append([("s", b["bucket"]), ("int", b["total"])] + [("int", b["by_status"].get(s["key"], 0)) for s in legend])

    info = wb.add_sheet("معلومات")
    info.append([("s", "البند"), ("s", "القيمة")], header=True)
    contract = next((c["name"] for c in data["options"]["contracts"] if c["id"] == data["contract_id"]), None)
    names = {o["id"]: o["name"] for o in data["options"]["users"]}
    for label, value in (("التقرير", "لوحة أداء الفريق"), ("من", data["from"]), ("إلى", data["to"]),
                         ("النطاق", SCOPE_AR.get(data["scope"], data["scope"])),
                         ("العقد", contract or "كل العقود"),
                         ("الموظفون", "، ".join(names.get(u) or u for u in data["user_ids"]) or "الكل"),
                         ("التجميع", "الحالة الفرعية" if data["group"] == "sub_status" else "الحالة الرئيسية"),
                         ("وقت الإنشاء (الرياض)", riyadh.now().strftime("%Y-%m-%d %H:%M")),
                         ("ملاحظة", "تحديثات الحالة لا تشمل الملاحظات المحذوفة ولا الحالات المستوردة من النظام السابق؛ "
                                    "المكالمات تُنسب للمالك الحالي للتحويلة ولا تتأثر بفلتر العقد؛ الأوقات بتوقيت الرياض")):
        info.append([("s", label), ("s", value)])
    if not data["calls"]["available"]:
        info.append([("s", "تنبيه"), ("s", "سجلات مكالمات السنترال غير متاحة الآن؛ أرقام المكالمات غير معروفة")])
    if data["calls"]["truncated"]:
        info.append([("s", "تنبيه"), ("s", f"الفترة فيها أكثر من {monitor.MAX_CDR_ROWS} سجل مكالمة؛ قصّر الفترة")])
    wb.save()
    return path, name, len(data["users"])
