"""AI assistance drafts (Phase 6 Q1; table ai_drafts, migration 0061; review/phase6-drafts.md).

A draft is ONLY a draft. Generating one reads the case (scope checked), sends the declared data
categories through services.ai.gateway (readiness, policy, daily caps, masking, audit), validates
the answer, renders an editable text and stores it. It never changes a case, books a payment,
creates a promise or follow-up, authorizes a call or sends a message: the one follow-up action is
`save_as_note()`, an explicit human action that writes a legacy "Comments" row (the note
triggers fire: last comment, note history) with the status and method the user chose, and no
callback.

    generated --edit--> edited --edit--> edited
        |                  |
        +------------------+--save_as_note--> saved_as_note   (final)
        +------------------+--discard-------> discarded       (final; the content is cleared)

Every change checks `expected_revision` (409 revision_conflict) and only the user who generated
the draft may change it. Drafts are visible to the ai_assist users whose scope contains the case.

What is stored: the kind, case/attempt, requester, language, options, the categories sent, a
sha256 of the unmasked input, a sha256 of the masked text actually sent (the raw prompt is never
stored), the draft JSON and text as shown to the user, the provider-call id, model and policy
revision. The AI retention task deletes unsaved drafts and clears saved drafts' content after
the policy's `draft_days` (services/ai/retention_hooks/drafts.py).

Idempotency of POST /v2/ai/drafts is kept on the draft row itself (requester + Idempotency-Key +
request fingerprint), not in `idempotency_keys`, so the draft's content is never copied into a
table the draft retention does not govern. The other mutations use @idempotent with content-free
responses.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import uuid

from flask import request
from sqlalchemy import text

from core import audit
from core.api_response import (FORBIDDEN, IDEMPOTENCY_IN_PROGRESS, IDEMPOTENCY_KEY_INVALID,
                               IDEMPOTENCY_KEY_REQUIRED, IDEMPOTENCY_KEY_REUSED, VALIDATION_FAILED, ApiError)
from core.revisions import RevisionConflict
from services import comment_status_tree as status_tree
from services.ai import drafts_inputs as I
from services.ai import drafts_kinds as K
from services.case_history_common import (USER_NAME_SQL, case_in_scope, iso, me, parse_uuid, required_text, s,
                                          user_ref)

STATUSES = ("generated", "edited", "saved_as_note", "discarded")
LIVE = ("generated", "edited")
TEXT_MAX = 20_000          # an edited draft
NOTE_MAX = 10_000          # the note saved from a draft (the v2 note limit)
ENTITY = "ai_draft"


def _s():
    from extensions import db
    return db.session


def _sha256(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                         separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def masked_fingerprint(instructions: str, input_messages: list[dict]) -> str:
    """sha256 over exactly what the provider receives (Responses API `instructions` + `input`)."""
    return _sha256({"instructions": instructions or None, "input": input_messages})


# ------------------------------------------------------------------------------ serialization
_COLS = f"""d.*, {USER_NAME_SQL.format(a='ru')} AS requested_by_name, {USER_NAME_SQL.format(a='eu')} AS edited_by_name,
            {USER_NAME_SQL.format(a='su')} AS saved_by_name, {USER_NAME_SQL.format(a='xu')} AS discarded_by_name"""
_FROM = """ai_drafts d
           LEFT JOIN users ru ON ru.id = d.requested_by LEFT JOIN users eu ON eu.id = d.edited_by
           LEFT JOIN users su ON su.id = d.saved_by LEFT JOIN users xu ON xu.id = d.discarded_by"""


def as_dict(r, *, level: str = "full") -> dict:
    """level: full (content + texts), list (current text only), meta (no content at all)."""
    out = {
        "id": s(r["id"]), "kind": r["kind"], "case_id": s(r["case_id"]), "attempt_id": s(r["attempt_id"]),
        "language": r["language"], "options": r["options"] or {}, "status": r["status"],
        "revision": int(r["revision"]), "ai_generated": True, "notice": K.NOTICE[r["language"]],
        "edited": int(r["edit_count"]) > 0, "edit_count": int(r["edit_count"]),
        "input": {"categories": list(r["input_categories"] or []), "hash": r["input_hash"],
                  "masked_fingerprint": r["masked_input_fingerprint"], "summary": r["input_summary"] or {}},
        "provenance": {"provider_call_id": s(r["provider_call_id"]), "model": r["model"],
                       "policy_revision": r["policy_revision"], "masked": r["masked"], "masked_count": r["masked_count"],
                       "schema": f"{r['schema_name']}.v{r['schema_version']}"},
        "requested_by": user_ref(r["requested_by"], r["requested_by_name"]),
        "created_at": iso(r["created_at"]), "updated_at": iso(r["updated_at"]), "expires_at": iso(r["expires_at"]),
        "edited_at": iso(r["edited_at"]), "edited_by": user_ref(r["edited_by"], r["edited_by_name"]),
        "saved_note_id": s(r["saved_note_id"]), "saved_at": iso(r["saved_at"]),
        "saved_by": user_ref(r["saved_by"], r["saved_by_name"]),
        "discarded_at": iso(r["discarded_at"]), "discarded_by": user_ref(r["discarded_by"], r["discarded_by_name"]),
        "purged_at": iso(r["purged_at"]),
        "content_available": r["content"] is not None or r["text"] is not None,
    }
    if level in ("full", "list"):
        out["text"] = r["text"]
    if level == "full":
        content = r["content"] or {}
        out["content"] = content.get("draft")
        out["checks"] = content.get("checks")
        out["citations"] = content.get("citations")
        out["generated_text"] = r["generated_text"]
    return out


def _row(draft_id, *, lock: bool = False):
    sql = f"SELECT {_COLS} FROM {_FROM} WHERE d.id = CAST(:id AS uuid)"
    if lock:
        sql += " FOR UPDATE OF d"
    return _s().execute(text(sql), {"id": str(draft_id)}).mappings().first()


def _visible(draft_id, *, lock: bool = False):
    """The draft when its case is in the caller's scope, else 404 (an id never reveals a case)."""
    not_found = ApiError(404, "draft_not_found", "draft not found", {"draft_id": str(draft_id)})
    r = _row(draft_id, lock=lock)
    if r is None:
        raise not_found
    try:
        case_in_scope(r["case_id"])
    except ApiError:
        raise not_found from None
    return r


# ------------------------------------------------------------------------------ reads
def get(draft_id) -> dict:
    return as_dict(_visible(draft_id))


def list_drafts(args, page: int, size: int) -> dict:
    case_arg = args.get("case") or args.get("case_id")
    attempt_arg = args.get("attempt") or args.get("attempt_id")
    if not case_arg and not attempt_arg:
        raise ApiError(422, VALIDATION_FAILED, "case (or attempt) is required", {"case": "required"})
    where, params = [], {}
    if case_arg:
        case_id = parse_uuid(case_arg, "case")
        case_in_scope(case_id)
        where.append("d.case_id = CAST(:case AS uuid)")
        params["case"] = case_id
    if attempt_arg:
        attempt_id = parse_uuid(attempt_arg, "attempt")
        I.load_attempt(attempt_id)                       # 404 outside the scope
        where.append("d.attempt_id = CAST(:attempt AS uuid)")
        params["attempt"] = attempt_id
    if args.get("kind"):
        if args["kind"] not in K.KINDS:
            raise ApiError(422, VALIDATION_FAILED, f"kind must be one of {', '.join(K.KINDS)}", {"kind": "invalid"})
        where.append("d.kind = :kind")
        params["kind"] = args["kind"]
    if args.get("status"):
        if args["status"] not in STATUSES:
            raise ApiError(422, VALIDATION_FAILED, f"status must be one of {', '.join(STATUSES)}",
                           {"status": "invalid"})
        where.append("d.status = :status")
        params["status"] = args["status"]
    if args.get("mine") in ("1", "true"):
        where.append("d.requested_by = CAST(:me AS uuid)")
        params["me"] = me()
    cond = " AND ".join(where)
    total = _s().execute(text(f"SELECT count(*) FROM ai_drafts d WHERE {cond}"), params).scalar()
    rows = _s().execute(text(f"SELECT {_COLS} FROM {_FROM} WHERE {cond} ORDER BY d.created_at DESC, d.id DESC "
                             "LIMIT :n OFFSET :o"), {**params, "n": size, "o": (page - 1) * size}).mappings().all()
    return {"items": [as_dict(r, level="list") for r in rows], "total": int(total), "page": page, "page_size": size}


# ------------------------------------------------------------------------------ generate
def parse_create(body: dict) -> dict:
    unknown = set(body) - {"kind", "case_id", "attempt_id", "language", "options"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    kind = body.get("kind")
    if kind not in K.KINDS:
        raise ApiError(422, VALIDATION_FAILED, f"kind must be one of {', '.join(K.KINDS)}",
                       {"kind": "required" if kind is None else "invalid"})
    language = body.get("language")
    if language not in K.LANGUAGES:
        raise ApiError(422, VALIDATION_FAILED, "language must be ar or en",
                       {"language": "required" if language is None else "invalid"})
    case_id = parse_uuid(body["case_id"], "case_id") if body.get("case_id") not in (None, "") else None
    attempt_id = parse_uuid(body["attempt_id"], "attempt_id") if body.get("attempt_id") not in (None, "") else None
    if kind in K.ATTEMPT_KINDS and attempt_id is None:
        raise ApiError(422, VALIDATION_FAILED, f"{kind} is drafted from a call: attempt_id is required",
                       {"attempt_id": "required"})
    if kind not in K.ATTEMPT_KINDS and case_id is None:
        raise ApiError(422, VALIDATION_FAILED, f"{kind} is drafted for a case: case_id is required",
                       {"case_id": "required"})
    if kind not in K.ATTEMPT_KINDS and attempt_id is not None:
        raise ApiError(422, VALIDATION_FAILED, f"{kind} is drafted for a case, not a call", {"attempt_id": "not_allowed"})
    return {"kind": kind, "language": language, "case_id": case_id, "attempt_id": attempt_id,
            "options": K.parse_options(kind, body)}


def _idempotency_key() -> str:
    from core.idempotency import valid_key
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise ApiError(400, IDEMPOTENCY_KEY_REQUIRED, "the Idempotency-Key header is required")
    if not valid_key(key):
        raise ApiError(400, IDEMPOTENCY_KEY_INVALID, "Idempotency-Key must be 1-255 visible ASCII characters")
    return key


def _by_key(user_id, key):
    return _s().execute(text(f"SELECT {_COLS} FROM {_FROM} WHERE d.requested_by = CAST(:u AS uuid) "
                             "AND d.idempotency_key = :k"), {"u": user_id, "k": key}).mappings().first()


def _replay(existing, fingerprint):
    if existing["request_fingerprint"] != fingerprint:
        raise ApiError(422, IDEMPOTENCY_KEY_REUSED, "this Idempotency-Key was already used for a different request")
    return as_dict(existing)


@contextlib.contextmanager
def _key_lock(user_id, key):
    """A session-level advisory lock on (user, key) on a dedicated connection, held across the
    provider call: a concurrent duplicate gets 409 instead of a second provider call."""
    from extensions import db
    conn = db.engine.connect()
    lock_key = f"ai_draft:{user_id}:{key}"
    try:
        if not conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": lock_key}).scalar():
            raise ApiError(409, IDEMPOTENCY_IN_PROGRESS, "a request with this Idempotency-Key is still being processed")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": lock_key})
    finally:
        conn.close()


def _may_coach(attempt) -> None:
    from core.auth import has_permission
    if str(attempt["collector_user_id"]) == me() or has_permission("calls_manage") or has_permission("quality_review"):
        return
    raise ApiError(403, FORBIDDEN, "coaching on another collector's call needs calls_manage or quality_review",
                   {"required_any": ["calls_manage", "quality_review"]})


def _policy_masking(revision: int, categories) -> set:
    from services.ai import masking
    from services.ai import policy as policy_mod
    r = _s().execute(text("SELECT mask_names, mask_identity_numbers, mask_phones, mask_accounts FROM ai_data_policies "
                          "WHERE revision = :r"), {"r": revision}).mappings().first()
    flags = {k: bool(r[col]) for k, col in policy_mod.MASKING_FIELDS.items()} if r else {}
    return masking.classes_for_policy(flags, employee_names_allowed="employee_names" in categories)


def create(body: dict) -> tuple[dict, bool]:
    """-> (draft, replayed). Raises the gateway's honest refusals unchanged."""
    from core.idempotency import request_fingerprint
    from services.ai import gateway, masking
    from services.ai import schema as schema_mod
    from services.ai.provider import Message

    key = _idempotency_key()
    v = parse_create(body)
    actor = me()
    fingerprint = request_fingerprint()
    existing = _by_key(actor, key)
    if existing is not None:
        return _replay(existing, fingerprint), True

    with _key_lock(actor, key):
        existing = _by_key(actor, key)
        if existing is not None:
            return _replay(existing, fingerprint), True
        attempt = None
        case_id = v["case_id"]
        if v["attempt_id"]:
            attempt = I.load_attempt(v["attempt_id"])            # 404 outside the scope
            case_id = str(attempt["case_id"])
            if v["kind"] == "coaching":
                _may_coach(attempt)
        else:
            case_in_scope(case_id)                               # 404 outside the scope
        kind, lang = v["kind"], v["language"]
        built = K.build(kind, language=lang, options=v["options"], case_id=case_id, attempt=attempt)
        instructions = K.instructions(kind, lang, v["options"], with_transcript=built.with_transcript)
        message = K.payload_text(built)
        input_hash = _sha256({"kind": kind, "language": lang, "options": v["options"],
                              "schema_version": K.SCHEMA_VERSION, "instructions": instructions, "payload": built.payload})
        _s().commit()          # end the read transaction: no locks or open snapshot across the provider call

        out = gateway.generate_structured(
            purpose=f"ai_draft_{kind}", categories=built.categories, schema=K.SCHEMAS[kind], schema_name=kind,
            instructions=instructions, messages=[Message("user", message)], identifiers=built.identifiers,
            max_output_tokens=K.MAX_OUTPUT_TOKENS[kind],
            entity_type="call_attempt" if attempt else "case", entity_id=v["attempt_id"] or case_id, case_id=case_id)

        draft = out.data
        schema_mod.validate(draft, K.SCHEMAS[kind])              # the provider validated the raw answer too
        checks = K.check(kind, draft, built)
        rendered = K.render(kind, draft, checks, lang)
        cited = {i for f in ("talking_points", "actions", "timeline") for item in draft.get(f) or []
                 for i in item.get("fact_ids") or []}
        content = {"draft": draft, "checks": checks,
                   "citations": {k: t for k, t in built.citations.items() if k in cited}}
        masker = masking.Masker(_policy_masking(out.policy_revision, built.categories)).add(built.identifiers)
        masked = masker.mask_all([instructions, message])
        fp = masked_fingerprint(masked[0], [{"role": "user", "content": masked[1]}])

        draft_id = _s().execute(text("""
            INSERT INTO ai_drafts (kind, case_id, attempt_id, requested_by, language, options, input_categories,
                                   input_hash, masked_input_fingerprint, input_summary, schema_name, schema_version,
                                   content, generated_text, text, provider_call_id, model, policy_revision, masked,
                                   masked_count, expires_at, request_id, idempotency_key, request_fingerprint)
            VALUES (:kind, CAST(:case AS uuid), CAST(:attempt AS uuid), CAST(:me AS uuid), :lang,
                    CAST(:options AS jsonb), CAST(:cats AS text[]), :ihash, :fp, CAST(:summary AS jsonb), :schema,
                    :sv, CAST(:content AS jsonb), :text, :text, CAST(:call AS uuid), :model, :rev, :masked,
                    :masked_count,
                    now() + make_interval(days => (SELECT draft_days FROM ai_data_policies WHERE revision = :rev)),
                    :req, :key, :rfp)
            RETURNING id"""), {
            "kind": kind, "case": case_id, "attempt": v["attempt_id"], "me": actor, "lang": lang,
            "options": json.dumps(v["options"]), "cats": sorted(built.categories), "ihash": input_hash, "fp": fp,
            "summary": json.dumps(built.summary), "schema": kind, "sv": K.SCHEMA_VERSION,
            "content": json.dumps(content, ensure_ascii=False), "text": rendered, "call": out.call_id,
            "model": out.model, "rev": out.policy_revision, "masked": out.masked, "masked_count": out.masked_count,
            "req": request.headers.get("X-Request-ID"), "key": key, "rfp": fingerprint}).scalar()
        audit.record("ai.draft.generate", ENTITY, draft_id, case_id=case_id, after={
            "kind": kind, "attempt_id": v["attempt_id"], "language": lang, "options": v["options"],
            "categories": sorted(built.categories), "input_hash": input_hash, "input_summary": built.summary,
            "provider_call_id": out.call_id, "model": out.model, "policy_revision": out.policy_revision,
            "masked": out.masked, "masked_count": out.masked_count, "schema": f"{kind}.v{K.SCHEMA_VERSION}",
            "checks": {k: len(v_) if isinstance(v_, list) else v_ for k, v_ in checks.items()}})
        _s().commit()
    return as_dict(_row(draft_id)), False


# ------------------------------------------------------------------------------ changes
def _changeable(draft_id, expected: int):
    """Lock the draft; it must be the caller's, still live, at the expected revision."""
    r = _visible(draft_id, lock=True)
    if str(r["requested_by"]) != me():
        raise ApiError(403, FORBIDDEN, "only the user who generated the draft can change it",
                       {"requested_by": s(r["requested_by"])})
    if r["status"] not in LIVE:
        code = "draft_saved" if r["status"] == "saved_as_note" else "draft_discarded"
        raise ApiError(409, code, f"the draft is {r['status'].replace('_', ' ')}; it can no longer change",
                       {"status": r["status"], "saved_note_id": s(r["saved_note_id"])})
    if int(r["revision"]) != expected:
        raise RevisionConflict(ENTITY, draft_id, expected, int(r["revision"]))
    return r


def _only(body: dict, allowed: set) -> None:
    unknown = set(body) - allowed
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})


def edit(draft_id, body: dict, expected: int) -> dict:
    _only(body, {"text", "expected_revision"})
    new_text = required_text(body, "text", max_len=TEXT_MAX)
    r = _changeable(draft_id, expected)
    if new_text == r["text"]:
        return as_dict(r, level="meta")
    _s().execute(text("""
        UPDATE ai_drafts SET text = :t, status = 'edited', edit_count = edit_count + 1, edited_at = now(),
                             edited_by = CAST(:me AS uuid)
         WHERE id = CAST(:id AS uuid)"""), {"t": new_text, "me": me(), "id": str(draft_id)})
    after = _row(draft_id)
    audit.record("ai.draft.edit", ENTITY, draft_id, case_id=r["case_id"], after={
        "kind": r["kind"], "revision": int(after["revision"]), "edit_count": int(after["edit_count"]),
        "text_chars": len(new_text)})
    return as_dict(after, level="meta")


def discard(draft_id, body: dict, expected: int) -> dict:
    _only(body, {"expected_revision"})
    r = _changeable(draft_id, expected)
    _s().execute(text("""
        UPDATE ai_drafts SET status = 'discarded', discarded_at = now(), discarded_by = CAST(:me AS uuid),
                             content = NULL, generated_text = NULL, text = NULL
         WHERE id = CAST(:id AS uuid)"""), {"me": me(), "id": str(draft_id)})
    after = _row(draft_id)
    audit.record("ai.draft.discard", ENTITY, draft_id, case_id=r["case_id"], after={
        "kind": r["kind"], "revision": int(after["revision"]), "content_cleared": True})
    return as_dict(after, level="meta")


_NOTE_FIELDS = {"text", "comment_status_id", "comment_sub_status_id", "contact_method_id", "comment_reachability_id",
                "expected_revision", "confirmed"}
_NEVER = {"callback_at", "next_contact_date", "promise", "payment", "call", "send", "follow_up", "outcome"}


def _note_fields(body: dict, contract_id) -> dict:
    status_id = parse_uuid(body.get("comment_status_id"), "comment_status_id")
    method_id = parse_uuid(body.get("contact_method_id"), "contact_method_id")
    reach = body.get("comment_reachability_id")
    reach = parse_uuid(reach, "comment_reachability_id") if reach not in (None, "") else None
    raw_sub = body.get("comment_sub_status_id")
    sub_id = parse_uuid(raw_sub, "comment_sub_status_id") if raw_sub not in (None, "") else None
    st = status_tree.load(status_id, _s())
    # `is_active` is ignored, as in the legacy note form, the Phase 5 wrap-up and Phase 2 bulk
    # notes (most statuses in daily use are flagged inactive; decision 2026-09-24)
    if st is None:
        raise ApiError(422, VALIDATION_FAILED, "unknown comment status", {"comment_status_id": "unknown"})
    problem = status_tree.main_status_problem(st)              # 0068: deleted, or a sub-status
    if problem:
        raise ApiError(422, VALIDATION_FAILED, "the note status was deleted" if problem == "status_deleted"
                       else "this is a sub-status: send its main status and the sub-status separately",
                       {"comment_status_id": problem})
    if sub_id is not None:
        problem = status_tree.sub_status_problem(status_id, sub_id, _s())
        if problem:
            raise ApiError(422, VALIDATION_FAILED, "the sub-status is not a live sub-status of the note status",
                           {"comment_sub_status_id": problem})
    if st["contract_id"] is not None and st["contract_id"] != contract_id:
        raise ApiError(422, VALIDATION_FAILED, "the note status belongs to another contract",
                       {"comment_status_id": "status_not_for_contract"})
    if _s().execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:id AS uuid)'), {"id": method_id}).first() is None:
        raise ApiError(422, VALIDATION_FAILED, "unknown contact method", {"contact_method_id": "unknown"})
    if reach is not None:
        rr = _s().execute(text("SELECT is_deleted FROM comment_reachability WHERE id = CAST(:id AS uuid)"),
                          {"id": reach}).first()
        if rr is None or rr[0]:
            raise ApiError(422, VALIDATION_FAILED, "unknown or inactive reachability",
                           {"comment_reachability_id": "unknown"})
        if st["reach"] is not None and st["reach"] != reach:
            raise ApiError(422, VALIDATION_FAILED, "the reachability does not match the status",
                           {"comment_reachability_id": "status_mismatch"})
    else:
        reach = st["reach"]
    return {"status_id": status_id, "sub_status_id": sub_id, "method_id": method_id, "reach_id": reach}


def save_as_note(draft_id, body: dict, expected: int) -> dict:
    """The one follow-up action: the human-edited text becomes a legacy "Comments" note."""
    never = set(body) & _NEVER
    if never:
        raise ApiError(422, "draft_note_only",
                       "saving a draft only records a note: it never schedules a callback, creates a promise or "
                       "payment, authorizes a call or sends a message", {k: "not_allowed" for k in never})
    _only(body, _NOTE_FIELDS)
    if body.get("confirmed") is not True:
        raise ApiError(422, VALIDATION_FAILED, "confirmed must be true: you reviewed the AI-generated text",
                       {"confirmed": "required"})
    note_text = required_text(body, "text", max_len=NOTE_MAX)
    r = _changeable(draft_id, expected)
    case_id = str(r["case_id"])
    contract_id = I.case_row(case_id).get("contract_id")
    v = _note_fields(body, contract_id)
    # 0079: a promise-to-pay status needs a promise (amount + due date), and a draft never
    # creates one: the collector saves such a note from the note form or the call result.
    from services import promise_required
    if promise_required.status_requires_promise(v["status_id"], v["sub_status_id"], _s()):
        raise ApiError(422, "promise_required",
                       "this note status records a promise to pay, which needs the promise amount and due date: "
                       "copy the text and save the note from the case's note form or call result",
                       {"comment_status_id": "requires_promise"})
    state = _s().execute(text("SELECT is_locked, is_archive FROM clints WHERE id = CAST(:id AS uuid) FOR NO KEY UPDATE"),
                         {"id": case_id}).mappings().first()
    if state["is_locked"]:
        raise ApiError(409, "case_locked", "the case is locked; notes cannot be recorded", {"case_id": case_id})
    if state["is_archive"]:
        raise ApiError(409, "case_archived", "the case is archived; notes cannot be recorded", {"case_id": case_id})

    actor, note_id = me(), str(uuid.uuid4())
    _s().execute(text("""
        INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                comment_reachability_id, comment_sub_status_id)
        VALUES (CAST(:id AS uuid), CAST(:case AS uuid), CAST(:actor AS uuid), CAST(:method AS uuid), :text,
                CAST(:status AS uuid), CAST(:reach AS uuid), CAST(:sub AS uuid))"""),
        {"id": note_id, "case": case_id, "actor": actor, "method": v["method_id"], "text": note_text,
         "status": v["status_id"], "reach": v["reach_id"], "sub": v["sub_status_id"]})
    audit.record("note.create", "comment", note_id, case_id=case_id,
                 after={"comment": note_text, "comment_status_id": v["status_id"],
                        "comment_sub_status_id": v["sub_status_id"],
                        "contactMethod_id": v["method_id"], "comment_reachability_id": v["reach_id"],
                        "next_contact_date": None, "ai_draft_id": str(draft_id)},
                 reason="saved from an AI draft")
    edited = note_text != r["text"]
    _s().execute(text("""
        UPDATE ai_drafts
           SET status = 'saved_as_note', text = :t, saved_note_id = CAST(:note AS uuid), saved_at = now(),
               saved_by = CAST(:me AS uuid),
               edit_count = edit_count + CASE WHEN :edited THEN 1 ELSE 0 END,
               edited_at = CASE WHEN :edited THEN now() ELSE edited_at END,
               edited_by = CASE WHEN :edited THEN CAST(:me AS uuid) ELSE edited_by END
         WHERE id = CAST(:id AS uuid)"""),
        {"t": note_text, "note": note_id, "me": actor, "edited": edited, "id": str(draft_id)})
    after = _row(draft_id)
    audit.record("ai.draft.save_as_note", ENTITY, draft_id, case_id=case_id, after={
        "kind": r["kind"], "note_id": note_id, "revision": int(after["revision"]),
        "edited_before_save": int(after["edit_count"]) > 0, "text_chars": len(note_text)})
    return {"draft": as_dict(after, level="meta"),
            "note": {"id": note_id, "case_id": case_id, "comment_status_id": v["status_id"],
                     "contact_method_id": v["method_id"], "comment_reachability_id": v["reach_id"],
                     "next_contact_date": None}}
