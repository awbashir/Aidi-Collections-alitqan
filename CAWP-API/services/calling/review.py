"""Supervisor review of calls (Phase 5 P1): uncertain attempts, their resolution, review flags.

* `GET /v2/calling/uncertain`   uncertain attempts in the caller's case scope, with their reason,
  the latest reconciliation decision and the open flags.
* `POST /v2/calls/<id>/resolve` a supervisor resolves an uncertain attempt to `ended` or `failed`
  with a REQUIRED reason; optionally releases its reservation (source `reconciled`, who + why).
  Refused (409 call_still_live) while the service's latest observation saw live channels of
  the call (within the last 2 minutes). The decision is appended to call_reconciliations.
* `GET /v2/calling/review`      review flags in scope: wrap-ups saved on a locked/archived case
  (their deferred case changes), late evidence after a wrap-up, evidence conflicts, and
  uncertain attempts the service could not resolve.
* `POST /v2/calling/review-flags/<id>/resolve` closes a flag with a REQUIRED reason (the
  supervisor applies any deferred change through the normal screens first). A flag that
  blocks calling (a deferred do-not-call / wrong number) stops blocking once resolved.
"""
from __future__ import annotations

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict, parse_revision
from services.calling import attempts as A
from services.calling import projection as P
from services.calling import queries
from services.calling import reconcile as RC
from services.calling.common import USER_NAME_SQL, bool_arg, iso, me, required_text, s, scope_sql, sess, user_ref

FLAG_KINDS = ("wrapup_needs_review", "late_evidence", "evidence_conflict", "reconciliation_unresolved")


def _reconciliations(attempt_id, limit=20) -> list[dict]:
    rows = sess().execute(text(f"""
        SELECT r.id, r.occurred_at, r.source, r.actor_user_id, {USER_NAME_SQL.format(a='u')} AS actor_name,
               r.prior_state, r.prior_reason, r.decision, r.code, r.reason, r.inventory, r.cdr,
               r.reservation_released
          FROM call_reconciliations r LEFT JOIN users u ON u.id = r.actor_user_id
         WHERE r.attempt_id = CAST(:a AS uuid) ORDER BY r.id DESC LIMIT :lim"""),
        {"a": str(attempt_id), "lim": limit}).mappings().all()
    out = []
    for r in rows:
        inv = r["inventory"] or {}
        cdr = r["cdr"] or {}
        out.append({"id": r["id"], "at": iso(r["occurred_at"]), "source": r["source"],
                    "actor": user_ref(r["actor_user_id"], r["actor_name"]), "prior_state": r["prior_state"],
                    "prior_reason": r["prior_reason"], "decision": r["decision"], "code": r["code"],
                    "reason": r["reason"], "reservation_released": r["reservation_released"],
                    "inventory": {k: inv.get(k) for k in ("same_epoch", "related_channels", "total_channels")
                                  if k in inv},
                    "cdr": {k: cdr.get(k) for k in ("source", "available", "found", "customer_dialed",
                                                    "customer_answered", "disposition", "billsec") if k in cdr}})
    return out


def flags_of(attempt_id, *, open_only=False) -> list[dict]:
    rows = sess().execute(text(f"""
        SELECT f.id, f.kind, f.reason, f.detail, f.event_id, f.blocks_calling, f.created_at, f.resolved_at,
               f.resolved_by, {USER_NAME_SQL.format(a='u')} AS resolved_by_name, f.resolution_reason
          FROM call_review_flags f LEFT JOIN users u ON u.id = f.resolved_by
         WHERE f.attempt_id = CAST(:a AS uuid) {"AND f.resolved_at IS NULL" if open_only else ""}
         ORDER BY f.created_at DESC"""), {"a": str(attempt_id)}).mappings().all()
    return [_flag_json(r) for r in rows]


def _flag_json(r) -> dict:
    return {"id": s(r["id"]), "kind": r["kind"], "reason": r["reason"], "detail": r["detail"],
            "event_id": r["event_id"], "blocks_calling": r["blocks_calling"], "created_at": iso(r["created_at"]),
            "open": r["resolved_at"] is None, "resolved_at": iso(r["resolved_at"]),
            "resolved_by": user_ref(r["resolved_by"], r["resolved_by_name"]),
            "resolution_reason": r["resolution_reason"]}


# --------------------------------------------------------------------------- uncertain
def list_uncertain(args, page, size, offset) -> dict:
    cond, params = scope_sql("c")
    where = f"a.state = 'uncertain' AND c.id IS NOT NULL AND {cond}"
    total = sess().execute(text(f"SELECT count(*) FROM call_attempts a LEFT JOIN clints c ON c.id = a.case_id "
                                f"WHERE {where}"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT a.id FROM call_attempts a LEFT JOIN clints c ON c.id = a.case_id
         WHERE {where} ORDER BY a.updated_at, a.id LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).all()
    items = []
    for (aid,) in rows:
        d = queries.call_detail(aid, with_evidence=False)
        d["reconciliations"] = _reconciliations(aid, limit=3)
        d["flags"] = flags_of(aid, open_only=True)
        items.append(d)
    return {"items": items, "total": int(total), "page": page, "page_size": size}


def resolve(attempt_id, body: dict, expected_revision) -> str:
    unknown = set(body) - {"expected_revision", "resolution", "reason", "release_reservation"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    resolution = body.get("resolution")
    if resolution not in ("ended", "failed"):
        raise ApiError(422, VALIDATION_FAILED, "resolution must be ended or failed", {"resolution": "invalid"})
    reason = required_text(body, "reason", max_len=1000)
    release = bool_arg(body, "release_reservation", False)
    queries.visible_or_404(attempt_id)
    a = A.lock_attempt(attempt_id)
    expected = parse_revision(expected_revision)
    if int(a["revision"]) != expected:
        raise RevisionConflict("call_attempt", attempt_id, expected, int(a["revision"]))
    if a["state"] != "uncertain":
        raise ApiError(409, "not_uncertain", "only an uncertain call can be resolved", {"state": a["state"]})
    live = sess().execute(text("""
        SELECT decision, code, occurred_at > now() - interval '2 minutes' AS recent
          FROM call_reconciliations WHERE attempt_id = CAST(:a AS uuid) AND source = 'service'
         ORDER BY id DESC LIMIT 1"""), {"a": str(attempt_id)}).mappings().first()
    if live and live["code"] == "channels_present" and live["recent"]:
        raise ApiError(409, "call_still_live",
                       "the PBX still showed channels of this call at the last check; wait for it to end",
                       {"attempt_id": str(attempt_id)})
    a = dict(a)
    facts = P.facts_for(sess(), attempt_id, a["pbx_epoch"]) if a["pbx_epoch"] else P.Facts(agent="")
    sets = RC.resolve_to(sess(), a, facts, resolution, None)
    released = False
    if release:
        released = A.release_reservation(attempt_id, "reconciled", by=me(), reason=reason)
    RC.record(sess(), a, resolution, "supervisor_resolution", source="supervisor", actor=me(), reason=reason,
              evidence=facts.summary() if a["pbx_epoch"] else None, released=released)
    sess().execute(text("""
        UPDATE call_review_flags SET resolved_at = now(), resolved_by = CAST(:by AS uuid), resolution_reason = :why
         WHERE attempt_id = CAST(:a AS uuid) AND kind = 'reconciliation_unresolved' AND resolved_at IS NULL"""),
        {"by": me(), "why": reason, "a": str(attempt_id)})
    audit.record("call.resolve", "call_attempt", attempt_id, case_id=str(a["case_id"]),
                 before={"state": "uncertain", "reason_code": a["reason_code"]},
                 after={"state": resolution, "reservation_released": released,
                        "set": {k: v for k, v in sets.items() if k != "state"}}, reason=reason)
    return str(attempt_id)


# --------------------------------------------------------------------------- flags
def list_review(args, page, size, offset) -> dict:
    cond, params = scope_sql("c")
    where = [f"c.id IS NOT NULL AND {cond}"]
    state = args.get("state") or "open"
    if state not in ("open", "resolved", "all"):
        raise ApiError(422, VALIDATION_FAILED, "state must be open, resolved or all", {"state": "invalid"})
    if state != "all":
        where.append("f.resolved_at IS NULL" if state == "open" else "f.resolved_at IS NOT NULL")
    kind = args.get("kind")
    if kind:
        kinds = [k.strip() for k in kind.split(",") if k.strip()]
        if any(k not in FLAG_KINDS for k in kinds):
            raise ApiError(422, VALIDATION_FAILED, f"kind must be in {', '.join(FLAG_KINDS)}", {"kind": "invalid"})
        where.append("f.kind = ANY(CAST(:kinds AS text[]))")
        params["kinds"] = kinds
    w = " AND ".join(where)
    base = "FROM call_review_flags f LEFT JOIN clints c ON c.id = f.case_id"
    total = sess().execute(text(f"SELECT count(*) {base} WHERE {w}"), params).scalar()
    counts = {r[0]: int(r[1]) for r in sess().execute(text(f"SELECT f.kind, count(*) {base} WHERE {w} GROUP BY f.kind"),
                                                      params)}
    rows = sess().execute(text(f"""
        SELECT f.id, f.attempt_id, f.kind, f.reason, f.detail, f.event_id, f.blocks_calling, f.created_at,
               f.resolved_at, f.resolved_by, {USER_NAME_SQL.format(a='u')} AS resolved_by_name, f.resolution_reason
          {base} LEFT JOIN users u ON u.id = f.resolved_by
         WHERE {w} ORDER BY f.created_at DESC, f.id LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        item = _flag_json(r)
        item["call"] = queries.call_detail(r["attempt_id"], with_evidence=False)
        items.append(item)
    return {"items": items, "total": int(total), "counts_by_kind": counts, "page": page, "page_size": size,
            "state": state}


def resolve_flag(flag_id, body: dict) -> dict:
    unknown = set(body) - {"reason"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    reason = required_text(body, "reason", max_len=1000)
    cond, params = scope_sql("c")
    row = sess().execute(text(f"""
        SELECT f.id, f.attempt_id, f.case_id, f.kind, f.resolved_at FROM call_review_flags f
          LEFT JOIN clints c ON c.id = f.case_id
         WHERE f.id = CAST(:id AS uuid) AND c.id IS NOT NULL AND {cond}
           FOR UPDATE OF f"""), {**params, "id": str(flag_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "flag_not_found", "review flag not found", {"flag_id": str(flag_id)})
    if row["resolved_at"] is not None:
        raise ApiError(409, "flag_already_resolved", "the review flag is already resolved", {"flag_id": str(flag_id)})
    sess().execute(text("""
        UPDATE call_review_flags SET resolved_at = now(), resolved_by = CAST(:by AS uuid), resolution_reason = :why
         WHERE id = CAST(:id AS uuid)"""), {"by": me(), "why": reason, "id": str(flag_id)})
    audit.record("call.review_flag.resolve", "call_review_flag", str(flag_id), case_id=str(row["case_id"]),
                 before={"open": True, "kind": row["kind"]}, after={"open": False}, reason=reason)
    return {"id": str(flag_id), "attempt_id": str(row["attempt_id"])}
