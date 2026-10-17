"""Call log reads: my calls, a case's calls, the scoped call log, one call's detail.

Visibility: a call is visible when its case is in the caller's case scope (the Phase 1 rules,
services/case_history_common.scope_sql) or when the caller made it. Everything else answers
404. The dialled number (contact value / destination) is shown only with `clints_details`,
the key the case contact screens use. `duration_seconds: null` means unknown, never 0.
"""
from __future__ import annotations

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from core.auth import has_permission
from services.calling.common import (MODES, OUTCOMES, STATES, TERMINAL, USER_NAME_SQL, case_in_scope, iso, me,
                                     s, scope_sql, sess, user_ref, valid_uuid)

_COLS = f"""
    a.id, a.mode, a.direction, a.state, a.case_id, a.contact_id, a.destination, a.collector_user_id,
    {USER_NAME_SQL.format(a='cu')} AS collector_name, a.extension, a.campaign_id, a.created_at, a.started_at,
    a.authorized_at, a.dispatched_at, a.agent_ringing_at, a.agent_answered_at, a.customer_dialing_at,
    a.customer_answered_at, a.bridged_at, a.ended_at, a.duration_seconds, a.duration_source, a.pbx_epoch,
    a.pbx_channel_id, a.reason_code, a.hangup_cause, (a.transcript IS NOT NULL) AS has_transcript,
    a.wrapped_up_at, a.revision,
    c.name AS case_name, c."accountNumber" AS account_number, cp.contract_id AS case_contract_id,
    w.id AS wrapup_id, w.outcome, w.note_id, w.follow_up_id, w.callback_at, w.promise_id, w.contact_blocked,
    w.calling_block_id, w.reviewed_by, {USER_NAME_SQL.format(a='ru')} AS reviewed_by_name, w.reviewed_at,
    w.needs_review, w.review_reason, w.note_text AS wrapup_note_text, w.deferred_actions,
    k.contact AS contact_value,
    (SELECT r.release_source FROM call_reservations r WHERE r.attempt_id = a.id) AS release_source
"""
_JOINS = """
    LEFT JOIN clints c ON c.id = a.case_id
    LEFT JOIN portfolios cp ON cp.id = c.portfolios_id
    LEFT JOIN users cu ON cu.id = a.collector_user_id
    LEFT JOIN call_wrapups w ON w.attempt_id = a.id
    LEFT JOIN users ru ON ru.id = w.reviewed_by
    LEFT JOIN "ClintContacts" k ON k.id = a.contact_id
"""
_TIME = "coalesce(a.started_at, a.created_at)"
_SORTS = {"-time": f"{_TIME} DESC, a.id DESC", "time": f"{_TIME} ASC, a.id ASC"}


def serialize(r, *, show_numbers: bool) -> dict:
    wrap = None
    if r["wrapup_id"] is not None:
        wrap = {"id": s(r["wrapup_id"]), "outcome": r["outcome"], "note_id": s(r["note_id"]),
                "follow_up_id": s(r["follow_up_id"]), "callback_at": iso(r["callback_at"]),
                "promise_id": s(r["promise_id"]), "contact_blocked": r["contact_blocked"],
                "calling_block_id": s(r["calling_block_id"]),
                "reviewed_by": user_ref(r["reviewed_by"], r["reviewed_by_name"]), "reviewed_at": iso(r["reviewed_at"]),
                "needs_review": bool(r["needs_review"]), "review_reason": r["review_reason"],
                "deferred_actions": r["deferred_actions"]}
    return {
        "id": s(r["id"]), "mode": r["mode"], "direction": r["direction"], "state": r["state"],
        # contract_id (2026-10-17): the wrap-up form loads the contract's note statuses even when
        # the caller does not own the case (campaign calls); null when the case was deleted
        "case": {"id": s(r["case_id"]), "name": r["case_name"], "account_number": r["account_number"],
                 "contract_id": s(r["case_contract_id"]), "missing": r["case_name"] is None},
        "contact": ({"id": s(r["contact_id"]), "value": r["contact_value"] if show_numbers else None}
                    if r["contact_id"] else None),
        "destination": r["destination"] if show_numbers else None,
        "collector": user_ref(r["collector_user_id"], r["collector_name"]),
        "extension": r["extension"], "campaign_id": s(r["campaign_id"]),
        "created_at": iso(r["created_at"]), "started_at": iso(r["started_at"]),
        "timeline": {k: iso(r[k]) for k in ("authorized_at", "dispatched_at", "agent_ringing_at", "agent_answered_at",
                                             "customer_dialing_at", "customer_answered_at", "bridged_at", "ended_at")},
        "duration_seconds": r["duration_seconds"], "duration_source": r["duration_source"],
        "duration_known": r["duration_seconds"] is not None,
        "pbx": {"epoch": r["pbx_epoch"], "channel_id": r["pbx_channel_id"]} if r["pbx_channel_id"] else None,
        "reason_code": r["reason_code"], "hangup_cause": r["hangup_cause"],
        "has_transcript": r["has_transcript"],
        "wrapup": wrap,
        "wrapup_pending": (wrap is None and r["mode"] != "manual" and r["state"] in ("ended", "failed")
                           and r["release_source"] != "no_customer_contact"),
        "revision": int(r["revision"]),
    }


def _filters(args, where, params):
    for name, col, allowed in (("state", "a.state", STATES), ("mode", "a.mode", MODES), ("outcome", "w.outcome", OUTCOMES)):
        raw = args.get(name)
        if raw:
            vals = [v.strip() for v in raw.split(",") if v.strip()]
            bad = [v for v in vals if v not in allowed]
            if bad:
                raise ApiError(422, VALIDATION_FAILED, f"{name} must be in {', '.join(allowed)}", {name: "invalid"})
            where.append(f"{col} = ANY(CAST(:{name} AS text[]))")
            params[name] = vals
    for name, col in (("collector_id", "a.collector_user_id"), ("case_id", "a.case_id"),
                      ("campaign_id", "a.campaign_id")):
        raw = args.get(name)
        if raw:
            v = valid_uuid(raw)
            if v is None:
                raise ApiError(422, VALIDATION_FAILED, f"{name} must be a UUID", {name: "invalid"})
            where.append(f"{col} = CAST(:{name} AS uuid)")
            params[name] = v
    wr = args.get("wrapup")
    if wr:
        if wr not in ("pending", "done"):
            raise ApiError(422, VALIDATION_FAILED, "wrapup must be pending or done", {"wrapup": "invalid"})
        where.append("w.id IS NOT NULL" if wr == "done" else
                     "(w.id IS NULL AND a.mode <> 'manual' AND a.state IN ('ended', 'failed') AND NOT EXISTS ("
                     "SELECT 1 FROM call_reservations rr WHERE rr.attempt_id = a.id "
                     "AND rr.release_source = 'no_customer_contact'))")
    first, last = args.get("from"), args.get("to")
    try:
        if first:
            params["t_lo"] = riyadh.day_bounds(riyadh.parse_date(first))[0]
            where.append(f"{_TIME} >= :t_lo")
        if last:
            params["t_hi"] = riyadh.day_bounds(riyadh.parse_date(last))[1]
            where.append(f"{_TIME} < :t_hi")
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, "from/to must be YYYY-MM-DD (Asia/Riyadh)", {"from": "invalid"}) from None
    if first and last and params["t_hi"] <= params["t_lo"]:
        raise ApiError(422, VALIDATION_FAILED, "to is before from", {"to": "invalid"})


def _list(where, params, args, page, size, offset) -> dict:
    sort = args.get("sort") or "-time"
    if sort not in _SORTS:
        raise ApiError(422, VALIDATION_FAILED, f"sort must be one of {', '.join(_SORTS)}", {"sort": "invalid"})
    _filters(args, where, params)
    w = " AND ".join(where)
    total = sess().execute(text(f"SELECT count(*) FROM call_attempts a {_JOINS} WHERE {w}"), params).scalar()
    counts = {r[0]: int(r[1]) for r in sess().execute(text(
        f"SELECT a.state, count(*) FROM call_attempts a {_JOINS} WHERE {w} GROUP BY a.state"), params)}
    rows = sess().execute(text(f"""
        SELECT {_COLS} FROM call_attempts a {_JOINS} WHERE {w}
         ORDER BY {_SORTS[sort]} LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    show = has_permission("clints_details")
    return {"items": [serialize(r, show_numbers=show) for r in rows], "total": int(total), "page": page,
            "page_size": size, "sort": sort, "counts_by_state": counts}


def my_calls(args, page, size, offset) -> dict:
    return _list(["a.collector_user_id = CAST(:me AS uuid)"], {"me": me()}, args, page, size, offset)


def case_calls(case_id, args, page, size, offset) -> dict:
    case_in_scope(case_id)
    return _list(["a.case_id = CAST(:cid AS uuid)"], {"cid": str(case_id)}, args, page, size, offset)


def call_log(args, page, size, offset) -> dict:
    cond, params = scope_sql("c")
    where = [f"((c.id IS NOT NULL AND {cond}) OR a.collector_user_id = CAST(:me AS uuid))"]
    return _list(where, {**params, "me": me()}, args, page, size, offset)


def my_active_attempt_id(user_id) -> str | None:
    r = sess().execute(text("""SELECT attempt_id FROM call_reservations WHERE collector_user_id = CAST(:u AS uuid)
                                  AND released_at IS NULL ORDER BY reserved_at DESC LIMIT 1"""),
                       {"u": str(user_id)}).first()
    return s(r[0]) if r else None


def visible_or_404(attempt_id):
    """404 unless the caller may see the call (case scope, or the caller made it)."""
    return _visible(attempt_id)


def visibility_sql(alias_case: str = "c") -> tuple[str, dict]:
    """Calls the caller may see: case in scope, or made by the caller, or - with calls_supervise /
    calls_manage -
    an ORPHANED call whose case was deleted (legacy hard delete; 2026-10-17: a supervisor must
    be able to see and release it)."""
    cond, params = scope_sql(alias_case)
    from services.calling.common import can_supervise
    orphan = f" OR {alias_case}.id IS NULL" if can_supervise() else ""
    return (f"(({alias_case}.id IS NOT NULL AND {cond}) OR a.collector_user_id = CAST(:me AS uuid){orphan})",
            {**params, "me": me()})


def _visible(attempt_id):
    cond, params = visibility_sql("c")
    r = sess().execute(text(f"""
        SELECT {_COLS} FROM call_attempts a {_JOINS}
         WHERE a.id = CAST(:id AS uuid) AND {cond}"""),
        {**params, "id": str(attempt_id)}).mappings().first()
    if r is None:
        raise ApiError(404, "call_not_found", "call not found", {"attempt_id": str(attempt_id)})
    return r


def call_detail(attempt_id, *, with_evidence: bool = True) -> dict:
    r = _visible(attempt_id)
    show = has_permission("clints_details")
    d = serialize(r, show_numbers=show)
    d["transcript"] = sess().execute(text("SELECT transcript FROM call_attempts WHERE id = CAST(:id AS uuid)"),
                                     {"id": str(attempt_id)}).scalar()
    if d["wrapup"] is not None:
        note = None
        if d["wrapup"]["note_id"] is not None:
            note = sess().execute(text('SELECT comment FROM "Comments" WHERE id = CAST(:id AS uuid)'),
                                  {"id": d["wrapup"]["note_id"]}).scalar()
        d["wrapup"]["note_text"] = note if note is not None else r["wrapup_note_text"]
    d["reservation"] = _reservation(attempt_id)
    if with_evidence:
        d["evidence"] = evidence(attempt_id, show_numbers=show)
    return d


def _reservation(attempt_id):
    r = sess().execute(text("""
        SELECT id, reserved_at, released_at, release_source FROM call_reservations
         WHERE attempt_id = CAST(:id AS uuid)"""), {"id": str(attempt_id)}).mappings().first()
    if r is None:
        return None
    return {"id": s(r["id"]), "reserved_at": iso(r["reserved_at"]), "released_at": iso(r["released_at"]),
            "release_source": r["release_source"], "active": r["released_at"] is None}


_NUMBER_FIELDS = ("Exten", "DestExten", "DialString", "DestChannel")


def evidence(attempt_id, *, show_numbers: bool) -> dict:
    events = sess().execute(text("""
        SELECT id, received_at, source, pbx_epoch, event, state, pbx_ts, uniqueid, linkedid, channel, payload
          FROM call_events WHERE attempt_id = CAST(:id AS uuid) ORDER BY pbx_ts NULLS LAST, id LIMIT 500"""),
        {"id": str(attempt_id)}).mappings().all()
    legs = sess().execute(text("""
        SELECT pbx_epoch, uniqueid, role, channel, created_pbx_ts, ringing_pbx_ts, answered_pbx_ts, bridged_pbx_ts,
               bridge_id, hungup_pbx_ts, hangup_cause
          FROM call_legs WHERE attempt_id = CAST(:id AS uuid) ORDER BY created_pbx_ts NULLS LAST, uniqueid"""),
        {"id": str(attempt_id)}).mappings().all()
    agi = sess().execute(text("""
        SELECT occurred_at, decision, reason FROM call_agi_decisions
         WHERE attempt_id = CAST(:id AS uuid) ORDER BY id"""), {"id": str(attempt_id)}).mappings().all()

    def payload(p):
        p = dict(p or {})
        if not show_numbers:
            for k in _NUMBER_FIELDS:
                p.pop(k, None)
        return p

    def ts(v):
        return None if v is None else format(v, "f")

    return {
        "note": "PBX facts only. Two answered legs are not a conversation; bridge membership of both is required.",
        "events": [{"id": e["id"], "received_at": iso(e["received_at"]), "source": e["source"],
                    "pbx_epoch": e["pbx_epoch"], "event": e["event"], "state": e["state"], "pbx_ts": ts(e["pbx_ts"]),
                    "uniqueid": e["uniqueid"], "payload": payload(e["payload"])} for e in events],
        "legs": [{"pbx_epoch": g["pbx_epoch"], "uniqueid": g["uniqueid"], "role": g["role"],
                  "created_pbx_ts": ts(g["created_pbx_ts"]), "ringing_pbx_ts": ts(g["ringing_pbx_ts"]),
                  "answered_pbx_ts": ts(g["answered_pbx_ts"]), "bridged_pbx_ts": ts(g["bridged_pbx_ts"]),
                  "bridge_id": g["bridge_id"], "hungup_pbx_ts": ts(g["hungup_pbx_ts"]),
                  "hangup_cause": g["hangup_cause"]} for g in legs],
        "agi_decisions": [{"at": iso(a["occurred_at"]), "decision": a["decision"], "reason": a["reason"]}
                          for a in agi],
    }


def case_blocks(case_id) -> dict:
    case_in_scope(case_id)
    rows = sess().execute(text(f"""
        SELECT b.id, b.kind, b.reason, b.source, b.attempt_id, b.created_by, {USER_NAME_SQL.format(a='cu')} AS cname,
               b.created_at, b.released_at, b.released_by, {USER_NAME_SQL.format(a='ru')} AS rname, b.release_reason
          FROM calling_blocks b LEFT JOIN users cu ON cu.id = b.created_by LEFT JOIN users ru ON ru.id = b.released_by
         WHERE b.case_id = CAST(:c AS uuid) ORDER BY b.created_at DESC, b.id"""), {"c": str(case_id)}).mappings().all()
    items = [{"id": s(r["id"]), "kind": r["kind"], "reason": r["reason"], "source": r["source"],
              "attempt_id": s(r["attempt_id"]), "created_by": user_ref(r["created_by"], r["cname"]),
              "created_at": iso(r["created_at"]), "active": r["released_at"] is None,
              "released_at": iso(r["released_at"]), "released_by": user_ref(r["released_by"], r["rname"]),
              "release_reason": r["release_reason"]} for r in rows]
    blocked_contacts = sess().execute(text("""
        SELECT count(DISTINCT k.contact) FROM "ClintContacts" k JOIN clints c ON c."identityNumber" = k."identityNumber"
         WHERE c.id = CAST(:c AS uuid) AND k.is_blocked AND NOT k.is_deleted"""), {"c": str(case_id)}).scalar()
    return {"case_id": str(case_id), "do_not_call": any(i["active"] and i["kind"] == "do_not_call" for i in items),
            "blocked_contacts": int(blocked_contacts or 0), "items": items}


def list_blocks(args, page, size, offset) -> dict:
    """Do-not-call blocks on cases in the caller's scope (the supervisor screen "blocked clients").

    Filters: `active=true|false|all` (default true), `contract_id`, `owner_id` (the case's
    collector), `source=wrapup|manual`, `q` (literal name / account / identity number)."""
    cond, params = scope_sql("c")
    where = [cond]
    active = (args.get("active") or "true").lower()
    if active not in ("true", "false", "all"):
        raise ApiError(422, VALIDATION_FAILED, "active must be true, false or all", {"active": "invalid"})
    if active == "true":
        where.append("b.released_at IS NULL")
    elif active == "false":
        where.append("b.released_at IS NOT NULL")
    for name, col in (("contract_id", "p.contract_id"), ("owner_id", "c.user_id")):
        raw = args.get(name)
        if raw:
            v = valid_uuid(raw)
            if v is None:
                raise ApiError(422, VALIDATION_FAILED, f"{name} must be a UUID", {name: "invalid"})
            where.append(f"{col} = CAST(:{name} AS uuid)")
            params[name] = v
    source = args.get("source")
    if source:
        if source not in ("wrapup", "manual"):
            raise ApiError(422, VALIDATION_FAILED, "source must be wrapup or manual", {"source": "invalid"})
        where.append("b.source = :source")
        params["source"] = source
    q = (args.get("q") or "").strip()
    if q:
        where.append("""(c.name ILIKE :q ESCAPE '\\' OR c."accountNumber" ILIKE :q ESCAPE '\\'
                         OR c."identityNumber" ILIKE :q ESCAPE '\\')""")
        params["q"] = "%" + q[:100].replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    base = f"""FROM calling_blocks b JOIN clints c ON c.id = b.case_id
               LEFT JOIN portfolios p ON p.id = c.portfolios_id LEFT JOIN contracts k ON k.id = p.contract_id
               LEFT JOIN users ou ON ou.id = c.user_id
               LEFT JOIN users cu ON cu.id = b.created_by LEFT JOIN users ru ON ru.id = b.released_by
              WHERE {' AND '.join(where)}"""
    total = sess().execute(text(f"SELECT count(*) {base}"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT b.id, b.case_id, c.name AS case_name, c."accountNumber" AS account,
               c."identityNumber" AS identity_number, c.user_id AS owner_id,
               {USER_NAME_SQL.format(a='ou')} AS owner_name, p.contract_id, k.name AS contract_name,
               p.name AS portfolio_name, b.kind, b.reason, b.source,
               b.attempt_id, b.created_by, {USER_NAME_SQL.format(a='cu')} AS cname, b.created_at,
               b.released_at, b.released_by, {USER_NAME_SQL.format(a='ru')} AS rname, b.release_reason
          {base} ORDER BY b.created_at DESC, b.id LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).mappings().all()
    items = [{"id": s(r["id"]), "case": {"id": s(r["case_id"]), "name": r["case_name"], "accountNumber": r["account"],
                                         "identityNumber": r["identity_number"]},
              "owner": user_ref(r["owner_id"], r["owner_name"]),
              "contract": {"id": s(r["contract_id"]), "name": r["contract_name"]} if r["contract_id"] else None,
              "portfolio_name": r["portfolio_name"],
              "kind": r["kind"], "reason": r["reason"], "source": r["source"], "attempt_id": s(r["attempt_id"]),
              "created_by": user_ref(r["created_by"], r["cname"]), "created_at": iso(r["created_at"]),
              "active": r["released_at"] is None, "released_at": iso(r["released_at"]),
              "released_by": user_ref(r["released_by"], r["rname"]), "release_reason": r["release_reason"]}
             for r in rows]
    return {"items": items, "total": int(total or 0), "page": page, "page_size": size, "active": active}


def service_status() -> dict:
    """What the API can see of the calling service: its latest PBX session (from the DB)."""
    last = sess().execute(text("""
        SELECT host, epoch, epoch_strength, pbx_restarted, connected_at, disconnected_at, disconnect_code
          FROM pbx_sessions ORDER BY connected_at DESC LIMIT 1""")).mappings().first()
    counts = {r[0]: int(r[1]) for r in sess().execute(text("""
        SELECT state, count(*) FROM call_attempts WHERE state <> ALL(CAST(:t AS text[])) GROUP BY state"""),
        {"t": list(TERMINAL)})}
    reservations = sess().execute(text("SELECT count(*) FROM call_reservations WHERE released_at IS NULL")).scalar()
    agi = {r[0]: int(r[1]) for r in sess().execute(text("""
        SELECT decision, count(*) FROM call_agi_decisions WHERE occurred_at > now() - interval '24 hours'
         GROUP BY decision"""))}
    return {
        "pbx": ({"host": last["host"], "epoch": last["epoch"], "epoch_strength": last["epoch_strength"],
                 "pbx_restarted": last["pbx_restarted"], "connected_at": iso(last["connected_at"]),
                 "connected": last["disconnected_at"] is None, "disconnected_at": iso(last["disconnected_at"]),
                 "disconnect_code": last["disconnect_code"]} if last else None),
        "note": "connected = the calling service's AMI session was open at its last report; not a call guarantee",
        "in_flight_by_state": counts, "active_reservations": int(reservations),
        "agi_decisions_24h": {"allow": agi.get("allow", 0), "deny": agi.get("deny", 0)},
    }


def user_picker(args, page, size, offset) -> dict:
    q = (args.get("q") or "").strip()
    where, params = ["u.is_active"], {}
    if q:
        where.append(f"({USER_NAME_SQL.format(a='u')} ILIKE :q OR u.user_name ILIKE :q)")
        params["q"] = "%" + q.replace("%", r"\%").replace("_", r"\_")[:100] + "%"
    w = " AND ".join(where)
    total = sess().execute(text(f"SELECT count(*) FROM users u WHERE {w}"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.user_name, u.extension AS legacy_extension,
               x.extension AS owned_extension
          FROM users u LEFT JOIN user_extensions x ON x.user_id = u.id AND x.released_at IS NULL
         WHERE {w} ORDER BY name, u.id LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    return {"items": [{**user_ref(r["id"], r["name"]), "user_name": r["user_name"],
                       "legacy_extension": r["legacy_extension"], "owned_extension": r["owned_extension"]}
                      for r in rows], "total": int(total), "page": page, "page_size": size}
