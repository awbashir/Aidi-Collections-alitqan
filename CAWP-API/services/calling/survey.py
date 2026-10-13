"""Post-call IVR survey (customer request 2026-10-13; migration 0066).

When the agent hangs up first, the PBX sends the customer to the `cawp-survey` dialplan
context: click-to-call through the Dial `F()` option of the restricted dialplan (policy
`post_call_context`), the toll-free IVR after its Dial returns. That context asks the calling
service's FastAGI server:

    collections-survey-offer   eligible? -> COLLECTIONS_SURVEY "1" + COLLECTIONS_SURVEY_ID
    collections-survey-result  the digit pressed (CAWP_SURVEY_SCORE) for that survey id

A call is eligible only when its case is known and that case's contract has the survey on
(`contract_call_settings.post_call_survey`):

* click-to-call: the attempt (COLLECTIONS_ATTEMPT, inherited by the customer leg) must have
  bridge evidence (`bridged_at`): no conversation, no survey. One survey per attempt.
* incoming: the caller's number (CAWP_SURVEY_CALLER) is matched to a case through the debtor's
  contacts (Saudi mobile, `cawp_norm_mobile`); an unmatched caller gets no survey. The agent is
  the extension that answered (CAWP_SURVEY_AGENT) and its current owner in `user_extensions`.

A result is accepted only for a survey still `offered`, offered to the same PBX channel, within
SURVEY_TTL_MINUTES. Nothing here talks to the PBX.
"""
from __future__ import annotations

import datetime as _dt
import re

from sqlalchemy import text

from core import audit, riyadh
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict
from services.calling.common import USER_NAME_SQL, iso, me, optional_text, parse_uuid, sess, user_ref

OFFER_SCRIPT = "collections-survey-offer"
RESULT_SCRIPT = "collections-survey-result"
SCRIPTS = (OFFER_SCRIPT, RESULT_SCRIPT)
SURVEY_TTL_MINUTES = 30
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXT_RE = re.compile(r"^[0-9]{2,6}$")


# --------------------------------------------------------------------------- settings (app)
def settings_view() -> dict:
    rows = sess().execute(text(f"""
        SELECT c.id::text AS contract_id, c.name, c.is_active,
               COALESCE(s.post_call_survey, false) AS enabled, COALESCE(s.revision, 0) AS revision,
               s.updated_at, s.updated_by, {USER_NAME_SQL.format(a='u')} AS updated_by_name, s.reason
          FROM contracts c
          LEFT JOIN contract_call_settings s ON s.contract_id = c.id
          LEFT JOIN users u ON u.id = s.updated_by
         ORDER BY c.is_active DESC, c.name""")).mappings().all()
    return {"items": [{
        "contract_id": r["contract_id"], "name": r["name"], "is_active": bool(r["is_active"]),
        "post_call_survey": bool(r["enabled"]), "revision": int(r["revision"]),
        "updated_at": iso(r["updated_at"]), "updated_by": user_ref(r["updated_by"], r["updated_by_name"]) if r["updated_by"] else None,
        "reason": r["reason"]} for r in rows]}


def save_setting(contract_id, body: dict, expected_revision) -> dict:
    cid = parse_uuid(contract_id, "contract_id")
    unknown = set(body) - {"post_call_survey", "reason", "expected_revision"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}", {k: "unknown" for k in unknown})
    enabled = body.get("post_call_survey")
    if not isinstance(enabled, bool):
        raise ApiError(422, VALIDATION_FAILED, "post_call_survey must be true or false", {"post_call_survey": "required"})
    reason = optional_text(body, "reason", max_len=1000)
    contract = sess().execute(text("SELECT id, name FROM contracts WHERE id = CAST(:id AS uuid)"), {"id": cid}).first()
    if contract is None:
        raise ApiError(404, NOT_FOUND, "contract not found", {"contract_id": cid})
    cur = sess().execute(text("""
        SELECT post_call_survey, revision FROM contract_call_settings WHERE contract_id = CAST(:id AS uuid) FOR UPDATE"""),
        {"id": cid}).first()
    current_rev = cur[1] if cur else 0
    if expected_revision is not None and expected_revision != current_rev:
        raise RevisionConflict("contract_call_settings", cid, expected_revision, current_rev)
    before = bool(cur[0]) if cur else False
    params = {"id": cid, "on": enabled, "by": me(), "why": reason}
    if cur is None:
        sess().execute(text("""
            INSERT INTO contract_call_settings (contract_id, post_call_survey, revision, updated_by, reason)
            VALUES (CAST(:id AS uuid), :on, 1, CAST(:by AS uuid), :why)"""), params)
    else:
        sess().execute(text("""
            UPDATE contract_call_settings
               SET post_call_survey = :on, revision = revision + 1, updated_at = now(),
                   updated_by = CAST(:by AS uuid), reason = :why
             WHERE contract_id = CAST(:id AS uuid)"""), params)
    audit.record("calling.survey.setting", "contract", cid, before={"post_call_survey": before},
                 after={"post_call_survey": enabled}, reason=reason or "post-call survey setting")
    return next(i for i in settings_view()["items"] if i["contract_id"] == cid)


# --------------------------------------------------------------------------- results (app)
def _date(value, field, default):
    if value in (None, ""):
        return default
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM-DD", {field: "invalid"}) from None


def results(args) -> dict:
    today = riyadh.now().date()
    start = _date(args.get("from"), "from", today - _dt.timedelta(days=29))
    end = _date(args.get("to"), "to", today)
    if end < start:
        raise ApiError(422, VALIDATION_FAILED, "to is before from", {"to": "before_from"})
    contract = parse_uuid(args["contract_id"], "contract_id") if args.get("contract_id") else None
    where = ("(s.offered_at AT TIME ZONE 'Asia/Riyadh')::date BETWEEN :start AND :end"
             + (" AND s.contract_id = CAST(:contract AS uuid)" if contract else ""))
    p = {"start": start, "end": end, "contract": contract}
    summary = sess().execute(text(f"""
        SELECT count(*) AS offered,
               count(*) FILTER (WHERE s.status = 'answered') AS answered,
               count(*) FILTER (WHERE s.status = 'no_input') AS no_input,
               count(*) FILTER (WHERE s.status = 'invalid') AS invalid,
               round(avg(s.score)::numeric, 2) AS avg_score,
               count(*) FILTER (WHERE s.score = 1) AS s1, count(*) FILTER (WHERE s.score = 2) AS s2,
               count(*) FILTER (WHERE s.score = 3) AS s3, count(*) FILTER (WHERE s.score = 4) AS s4,
               count(*) FILTER (WHERE s.score = 5) AS s5
          FROM call_surveys s WHERE {where}"""), p).mappings().one()
    def group(key, label):
        return sess().execute(text(f"""
        SELECT {key} AS id, {label} AS label, count(*) AS offered,
               count(*) FILTER (WHERE s.status = 'answered') AS answered, round(avg(s.score)::numeric, 2) AS avg_score
          FROM call_surveys s
          LEFT JOIN users u ON u.id = s.agent_user_id
          LEFT JOIN contracts c ON c.id = s.contract_id
         WHERE {where}
         GROUP BY 1, 2 ORDER BY avg_score DESC NULLS LAST, offered DESC LIMIT 200"""), p).mappings().all()

    by_agent = group("s.agent_user_id::text", USER_NAME_SQL.format(a="u"))
    by_contract = group("s.contract_id::text", "c.name")
    recent = sess().execute(text(f"""
        SELECT s.id::text AS id, s.source, s.status, s.score, s.language, s.offered_at, s.completed_at,
               s.case_id::text AS case_id, s.attempt_id::text AS attempt_id, s.agent_extension,
               s.agent_user_id, {USER_NAME_SQL.format(a='u')} AS agent_name, c.name AS contract_name
          FROM call_surveys s
          LEFT JOIN users u ON u.id = s.agent_user_id
          LEFT JOIN contracts c ON c.id = s.contract_id
         WHERE {where}
         ORDER BY s.offered_at DESC LIMIT 100"""), p).mappings().all()
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return {
        "from": start.isoformat(), "to": end.isoformat(), "contract_id": contract,
        "summary": {"offered": summary["offered"], "answered": summary["answered"], "no_input": summary["no_input"],
                    "invalid": summary["invalid"], "avg_score": num(summary["avg_score"]),
                    "distribution": {str(k): summary[f"s{k}"] for k in range(1, 6)}},
        "by_agent": [{"user_id": r["id"], "name": r["label"], "offered": r["offered"], "answered": r["answered"],
                      "avg_score": num(r["avg_score"])} for r in by_agent],
        "by_contract": [{"contract_id": r["id"], "name": r["label"], "offered": r["offered"], "answered": r["answered"],
                         "avg_score": num(r["avg_score"])} for r in by_contract],
        "items": [{"id": r["id"], "source": r["source"], "status": r["status"], "score": r["score"],
                   "language": r["language"], "offered_at": iso(r["offered_at"]), "completed_at": iso(r["completed_at"]),
                   "case_id": r["case_id"], "attempt_id": r["attempt_id"], "agent_extension": r["agent_extension"],
                   "agent": user_ref(r["agent_user_id"], r["agent_name"]) if r["agent_user_id"] else None,
                   "contract_name": r["contract_name"]} for r in recent],
    }


# --------------------------------------------------------------------------- PBX side (FastAGI)
def _enabled(session, contract_id) -> bool:
    return bool(session.execute(text("""
        SELECT post_call_survey FROM contract_call_settings WHERE contract_id = CAST(:c AS uuid)"""),
        {"c": contract_id}).scalar())


def offer(session, *, channel_id: str, attempt_id: str | None, caller: str | None, agent_extension: str | None,
          language: str | None) -> tuple[str | None, str]:
    """-> (survey id, reason). A survey id only when the call is eligible (module docstring)."""
    if not channel_id or len(channel_id) > 150:
        return None, "bad_channel"
    lang = "en" if (language or "").strip().lower() == "en" else "ar"
    if attempt_id:
        if not UUID_RE.match(attempt_id):
            return None, "bad_attempt"
        a = session.execute(text("""
            SELECT a.id::text AS id, a.case_id::text AS case_id, a.collector_user_id::text AS agent, a.extension,
                   a.bridged_at, p.contract_id::text AS contract_id
              FROM call_attempts a JOIN clints k ON k.id = a.case_id JOIN portfolios p ON p.id = k.portfolios_id
             WHERE a.id = CAST(:a AS uuid)"""), {"a": attempt_id}).mappings().first()
        if a is None:
            return None, "unknown_attempt"
        if a["bridged_at"] is None:
            return None, "no_conversation"
        if not _enabled(session, a["contract_id"]):
            return None, "survey_off"
        sid = session.execute(text("""
            INSERT INTO call_surveys (source, attempt_id, case_id, contract_id, agent_user_id, agent_extension,
                                      channel_id, language)
            VALUES ('click_to_call', CAST(:a AS uuid), CAST(:c AS uuid), CAST(:k AS uuid), CAST(:u AS uuid), :ext, :ch, :lang)
            ON CONFLICT (attempt_id) WHERE attempt_id IS NOT NULL DO NOTHING
            RETURNING id::text"""), {"a": a["id"], "c": a["case_id"], "k": a["contract_id"], "u": a["agent"],
                                     "ext": a["extension"], "ch": channel_id, "lang": lang}).scalar()
        return (sid, "offered") if sid else (None, "already_offered")
    digits = re.sub(r"[^0-9]", "", caller or "")
    if len(digits) < 9:
        return None, "no_caller"
    match = session.execute(text("""
        SELECT k.id::text AS case_id, p.contract_id::text AS contract_id
          FROM "ClintContacts" cc
          JOIN clints k ON k."identityNumber" = cc."identityNumber"
          JOIN portfolios p ON p.id = k.portfolios_id
         WHERE cc.is_deleted = false
           AND right(regexp_replace(translate(cc.contact, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'),
                                    '[^0-9]', '', 'g'), 9) = right(:digits, 9)
           AND cawp_norm_mobile(cc.contact) IS NOT DISTINCT FROM cawp_norm_mobile(:caller)
         ORDER BY k.is_archive ASC, k.update_at DESC
         LIMIT 1"""), {"digits": digits, "caller": caller}).mappings().first()
    if match is None:
        return None, "caller_unmatched"
    if not _enabled(session, match["contract_id"]):
        return None, "survey_off"
    ext = agent_extension if agent_extension and EXT_RE.match(agent_extension) else None
    agent = session.execute(text("""
        SELECT user_id::text FROM user_extensions WHERE extension = :e AND released_at IS NULL"""),
        {"e": ext}).scalar() if ext else None
    sid = session.execute(text("""
        INSERT INTO call_surveys (source, case_id, contract_id, agent_user_id, agent_extension, caller_number,
                                  channel_id, language)
        VALUES ('inbound', CAST(:c AS uuid), CAST(:k AS uuid), CAST(:u AS uuid), :ext, :caller, :ch, :lang)
        RETURNING id::text"""), {"c": match["case_id"], "k": match["contract_id"], "u": agent, "ext": ext,
                                 "caller": digits[-32:], "ch": channel_id, "lang": lang}).scalar()
    return sid, "offered"


def record_result(session, *, channel_id: str, survey_id: str | None, digits: str | None) -> str:
    """-> the stored status, or a refusal reason (nothing written)."""
    if not survey_id or not UUID_RE.match(survey_id):
        return "bad_survey"
    row = session.execute(text("""
        SELECT id, channel_id, status, (offered_at < now() - make_interval(mins => :ttl)) AS expired
          FROM call_surveys WHERE id = CAST(:id AS uuid) FOR UPDATE"""),
        {"id": survey_id, "ttl": SURVEY_TTL_MINUTES}).mappings().first()
    if row is None:
        return "unknown_survey"
    if row["channel_id"] != channel_id:
        return "wrong_channel"
    if row["status"] != "offered":
        return "already_completed"
    if row["expired"]:
        return "expired"
    d = (digits or "").strip()
    status, score = ("answered", int(d)) if d in ("1", "2", "3", "4", "5") else (("no_input", None) if d == "" else ("invalid", None))
    session.execute(text("""
        UPDATE call_surveys SET status = :st, score = :sc, completed_at = now() WHERE id = CAST(:id AS uuid)"""),
        {"st": status, "sc": score, "id": survey_id})
    return status
