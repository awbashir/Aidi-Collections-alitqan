"""Provider-call audit rows (table ai_provider_calls, migration 0060). Metadata only.

Each write runs in its OWN short transaction on a separate connection (`db.engine.begin()`), not
in the caller's session: the `started` row is durable before the first HTTP attempt (so a crash
leaves evidence, later marked `interrupted`), and the outcome is recorded even when the caller's
transaction rolls back.

There is no parameter for prompt, input, output, transcript, audio or secrets, on purpose.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text

log = logging.getLogger("cawp.ai.calls")

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PTYPE = re.compile(r"^[a-z0-9_.]{1,64}$")


def _engine():
    from extensions import db
    return db.engine


def _clean(value, pattern) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None


def _short(value, n=200) -> str | None:
    return value[:n] if isinstance(value, str) and value else None


def start(*, purpose, capability, provider, model, policy_revision, categories, masked, masked_count,
          actor_user_id, entity_type, entity_id, case_id, request_id, input_bytes=None) -> str:
    """Insert a `started` row, committed at once. Returns its id."""
    with _engine().begin() as conn:
        return str(conn.execute(text("""
            INSERT INTO ai_provider_calls
                (purpose, capability, provider, model, policy_revision, data_categories, masked, masked_count,
                 outcome, actor_user_id, entity_type, entity_id, case_id, request_id, input_bytes)
            VALUES (:purpose, :capability, :provider, :model, :policy_revision, CAST(:cats AS text[]), :masked,
                    :masked_count, 'started', CAST(:actor AS uuid), :entity_type, :entity_id,
                    CAST(:case_id AS uuid), :request_id, :input_bytes)
            RETURNING id"""), {
            "purpose": purpose, "capability": capability, "provider": provider, "model": _short(model),
            "policy_revision": policy_revision, "cats": sorted(categories), "masked": bool(masked),
            "masked_count": masked_count, "actor": actor_user_id, "entity_type": entity_type,
            "entity_id": _short(entity_id and str(entity_id), 100), "case_id": case_id,
            "request_id": _short(request_id), "input_bytes": input_bytes}).scalar())


def finish(call_id: str, *, outcome: str, model=None, error_code=None, provider_error_type=None, http_status=None,
           attempts=0, usage=None, latency_ms=None, provider_request_id=None, input_bytes=None) -> None:
    """Record the outcome of a `started` row (succeeded / failed). Never raises: the provider's
    answer must not be lost because the audit write failed; the row then stays `started` and the
    retention task marks it `interrupted`."""
    try:
        with _engine().begin() as conn:
            conn.execute(text("""
                UPDATE ai_provider_calls
                   SET outcome = :outcome, finished_at = now(), model = coalesce(:model, model),
                       error_code = :error_code, provider_error_type = :ptype, http_status = :http_status,
                       attempts = :attempts, input_tokens = :in_t, output_tokens = :out_t, total_tokens = :tot_t,
                       audio_seconds = :audio_s, latency_ms = :latency, provider_request_id = :prid,
                       input_bytes = coalesce(:input_bytes, input_bytes)
                 WHERE id = CAST(:id AS uuid) AND outcome = 'started'"""), {
                "id": call_id, "outcome": outcome, "model": _short(model),
                "error_code": _clean(error_code, _CODE), "ptype": _clean(provider_error_type, _PTYPE),
                "http_status": http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None,
                "attempts": max(0, min(int(attempts or 0), 20)),
                "in_t": getattr(usage, "input_tokens", None), "out_t": getattr(usage, "output_tokens", None),
                "tot_t": getattr(usage, "total_tokens", None), "audio_s": getattr(usage, "audio_seconds", None),
                "latency": latency_ms, "prid": _short(provider_request_id), "input_bytes": input_bytes})
    except Exception:  # noqa: BLE001
        log.exception("could not record the outcome of AI provider call %s", call_id)


def refused(*, purpose, capability, provider, model, policy_revision, categories, error_code,
            actor_user_id, entity_type, entity_id, case_id, request_id) -> str | None:
    """Insert a final `refused` row (not configured, no policy, category denied). Never raises."""
    try:
        with _engine().begin() as conn:
            return str(conn.execute(text("""
                INSERT INTO ai_provider_calls
                    (purpose, capability, provider, model, policy_revision, data_categories, outcome, finished_at,
                     error_code, actor_user_id, entity_type, entity_id, case_id, request_id)
                VALUES (:purpose, :capability, :provider, :model, :policy_revision, CAST(:cats AS text[]),
                        'refused', now(), :error_code, CAST(:actor AS uuid), :entity_type, :entity_id,
                        CAST(:case_id AS uuid), :request_id)
                RETURNING id"""), {
                "purpose": purpose, "capability": capability, "provider": provider or "unknown",
                "model": _short(model), "policy_revision": policy_revision, "cats": sorted(categories),
                "error_code": _clean(error_code, _CODE), "actor": actor_user_id, "entity_type": entity_type,
                "entity_id": _short(entity_id and str(entity_id), 100), "case_id": case_id,
                "request_id": _short(request_id)}).scalar())
    except Exception:  # noqa: BLE001
        log.exception("could not record a refused AI provider call (%s)", error_code)
        return None


_LIST_COLS = """c.id, c.started_at, c.finished_at, c.purpose, c.capability, c.provider, c.model, c.policy_revision,
                c.data_categories, c.masked, c.masked_count, c.outcome, c.error_code, c.provider_error_type,
                c.http_status, c.attempts, c.input_tokens, c.output_tokens, c.total_tokens, c.audio_seconds,
                c.latency_ms, c.input_bytes, c.actor_user_id, c.entity_type, c.entity_id, c.case_id,
                c.provider_request_id,
                nullif(btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')), '') AS actor_name"""


def as_dict(r) -> dict:
    from services.case_history_common import iso, s, user_ref
    return {"id": str(r["id"]), "started_at": iso(r["started_at"]), "finished_at": iso(r["finished_at"]),
            "purpose": r["purpose"], "capability": r["capability"], "provider": r["provider"], "model": r["model"],
            "policy_revision": r["policy_revision"], "data_categories": list(r["data_categories"] or []),
            "masked": r["masked"], "masked_count": r["masked_count"], "outcome": r["outcome"],
            "error_code": r["error_code"], "provider_error_type": r["provider_error_type"],
            "http_status": r["http_status"], "attempts": r["attempts"],
            "usage": {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                      "total_tokens": r["total_tokens"], "audio_seconds": s(r["audio_seconds"])},
            "latency_ms": r["latency_ms"], "input_bytes": r["input_bytes"],
            "actor": user_ref(r["actor_user_id"], r["actor_name"]), "entity_type": r["entity_type"],
            "entity_id": r["entity_id"], "case_id": s(r["case_id"]), "provider_request_id": r["provider_request_id"]}


def list_calls(*, page: int, size: int, filters: dict, session) -> tuple[list[dict], int]:
    where, params = ["TRUE"], {}
    for col in ("purpose", "capability", "outcome", "entity_type", "entity_id"):
        if filters.get(col):
            where.append(f"c.{col} = :{col}")
            params[col] = filters[col]
    if filters.get("actor_user_id"):
        where.append("c.actor_user_id = CAST(:actor AS uuid)")
        params["actor"] = filters["actor_user_id"]
    if filters.get("case_id"):
        where.append("c.case_id = CAST(:case_id AS uuid)")
        params["case_id"] = filters["case_id"]
    cond = " AND ".join(where)
    total = session.execute(text(f"SELECT count(*) FROM ai_provider_calls c WHERE {cond}"), params).scalar()
    rows = session.execute(text(f"""
        SELECT {_LIST_COLS} FROM ai_provider_calls c LEFT JOIN users u ON u.id = c.actor_user_id
         WHERE {cond} ORDER BY c.started_at DESC, c.id LIMIT :n OFFSET :o"""),
        {**params, "n": size, "o": (page - 1) * size}).mappings().all()
    return [as_dict(r) for r in rows], int(total)
