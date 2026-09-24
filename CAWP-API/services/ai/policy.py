"""The revisioned AI data policy (table ai_data_policies, migration 0059).

One global policy; the current one is the highest revision. A change is a new revision written by
`save()` in the caller's transaction, with an `ai.policy.update` audit event (before/after, reason).
Writers are serialised by a transaction-scoped advisory lock and must name the revision they saw
(`expected_revision`, 0 when there is none yet): a stale writer gets RevisionConflict (409).

Wire shape (GET/PUT /v2/ai/policy):

    {"revision": 3, "state": "approved", "provider": "openai",
     "allowed_categories": ["case_facts", "notes", "transcripts"],
     "masking": {"name": true, "identity_number": true, "phone": true, "account": true},
     "retention_days": {"transcripts": 90, "audio": 30, "drafts": 30, "provider_logs": 365},
     "approved_by": {"id": "...", "name": "..."}, "approved_at": "...", "reason": "..."}
"""
from __future__ import annotations

import dataclasses

from sqlalchemy import text

from core.api_response import VALIDATION_FAILED, ApiError

CATEGORIES = ("case_facts", "notes", "transcripts", "audio", "employee_names")
STATES = ("approved", "suspended")
PROVIDERS = ("openai",)
MASKING_FIELDS = {"name": "mask_names", "identity_number": "mask_identity_numbers",
                  "phone": "mask_phones", "account": "mask_accounts"}
RETENTION_FIELDS = {"transcripts": ("transcript_days", 1, 3650), "audio": ("audio_days", 1, 3650),
                    "drafts": ("draft_days", 1, 3650), "provider_logs": ("provider_log_days", 30, 3650)}
LOCK_KEY = "ai_data_policy"


@dataclasses.dataclass(frozen=True)
class Policy:
    revision: int
    state: str
    provider: str
    allowed_categories: tuple
    masking: dict
    retention_days: dict
    approved_by: str | None
    approved_by_name: str | None
    approved_at: object
    reason: str

    @property
    def approved(self) -> bool:
        return self.state == "approved"

    def allows(self, categories) -> bool:
        return set(categories) <= set(self.allowed_categories)

    def denied(self, categories) -> list[str]:
        return sorted(set(categories) - set(self.allowed_categories))

    def as_dict(self) -> dict:
        from services.case_history_common import iso, user_ref
        return {"revision": self.revision, "state": self.state, "provider": self.provider,
                "allowed_categories": list(self.allowed_categories), "masking": dict(self.masking),
                "retention_days": dict(self.retention_days),
                "approved_by": user_ref(self.approved_by, self.approved_by_name),
                "approved_at": iso(self.approved_at), "reason": self.reason}

    def snapshot(self) -> dict:
        """For the audit trail: the policy content without the actor/time (they are on the event)."""
        return {"revision": self.revision, "state": self.state, "provider": self.provider,
                "allowed_categories": list(self.allowed_categories), "masking": dict(self.masking),
                "retention_days": dict(self.retention_days)}


def options() -> dict:
    """The accepted values, for the admin form."""
    return {"states": list(STATES), "providers": list(PROVIDERS), "categories": list(CATEGORIES),
            "masking": list(MASKING_FIELDS),
            "retention_days": {k: {"min": lo, "max": hi} for k, (_c, lo, hi) in RETENTION_FIELDS.items()}}


_COLS = """p.revision, p.state, p.provider, p.allowed_categories, p.mask_names, p.mask_identity_numbers,
           p.mask_phones, p.mask_accounts, p.transcript_days, p.audio_days, p.draft_days,
           p.provider_log_days, p.approved_by, p.approved_at, p.reason,
           nullif(btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')), '') AS approved_by_name"""


def _row_to_policy(r) -> Policy:
    return Policy(
        revision=int(r["revision"]), state=r["state"], provider=r["provider"],
        allowed_categories=tuple(c for c in CATEGORIES if c in (r["allowed_categories"] or [])),
        masking={k: bool(r[col]) for k, col in MASKING_FIELDS.items()},
        retention_days={k: int(r[col]) for k, (col, _lo, _hi) in RETENTION_FIELDS.items()},
        approved_by=str(r["approved_by"]) if r["approved_by"] else None,
        approved_by_name=r["approved_by_name"], approved_at=r["approved_at"], reason=r["reason"])


def _session(session):
    if session is not None:
        return session
    from extensions import db
    return db.session


def current(session=None) -> Policy | None:
    """The current policy, or None when no revision was ever recorded."""
    r = _session(session).execute(text(f"""
        SELECT {_COLS} FROM ai_data_policies p LEFT JOIN users u ON u.id = p.approved_by
         ORDER BY p.revision DESC LIMIT 1""")).mappings().first()
    return _row_to_policy(r) if r else None


def history(page: int, size: int, session=None) -> tuple[list[dict], int]:
    s = _session(session)
    total = s.execute(text("SELECT count(*) FROM ai_data_policies")).scalar()
    rows = s.execute(text(f"""
        SELECT {_COLS} FROM ai_data_policies p LEFT JOIN users u ON u.id = p.approved_by
         ORDER BY p.revision DESC LIMIT :n OFFSET :o"""), {"n": size, "o": (page - 1) * size}).mappings().all()
    return [_row_to_policy(r).as_dict() for r in rows], int(total)


def parse_body(body: dict) -> dict:
    """Validate a PUT body (a full replacement; every field is required). -> column values."""
    errors = {}
    state = body.get("state")
    if state not in STATES:
        errors["state"] = f"one of {', '.join(STATES)}"
    provider = body.get("provider")
    if provider not in PROVIDERS:
        errors["provider"] = f"one of {', '.join(PROVIDERS)}"
    cats = body.get("allowed_categories")
    if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
        errors["allowed_categories"] = "a list of categories"
    elif set(cats) - set(CATEGORIES):
        errors["allowed_categories"] = f"unknown: {', '.join(sorted(set(cats) - set(CATEGORIES)))}"
    masking = body.get("masking")
    if not isinstance(masking, dict) or set(masking) != set(MASKING_FIELDS) \
            or not all(isinstance(v, bool) for v in masking.values()):
        errors["masking"] = f"true/false for each of {', '.join(MASKING_FIELDS)}"
    retention = body.get("retention_days")
    if not isinstance(retention, dict) or set(retention) != set(RETENTION_FIELDS):
        errors["retention_days"] = f"days for each of {', '.join(RETENTION_FIELDS)}"
    else:
        for k, (_col, lo, hi) in RETENTION_FIELDS.items():
            v = retention[k]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                errors[f"retention_days.{k}"] = f"a whole number of days, {lo}-{hi}"
    if body.get("confirmed") is not True:
        errors["confirmed"] = "must be true: recording the policy is an explicit approval"
    if errors:
        raise ApiError(422, VALIDATION_FAILED, "the AI data policy is not valid", errors)
    values = {"state": state, "provider": provider,
              "allowed_categories": [c for c in CATEGORIES if c in set(cats)]}
    values.update({col: masking[k] for k, col in MASKING_FIELDS.items()})
    values.update({col: retention[k] for k, (col, _lo, _hi) in RETENTION_FIELDS.items()})
    return values


def save(values: dict, *, expected_revision: int, reason: str, actor_user_id: str,
         request_id: str | None = None, idempotency_key: str | None = None, session=None) -> Policy:
    """Record a new revision in the caller's transaction (commit is the caller's job)."""
    from core import audit
    from core.revisions import RevisionConflict
    s = _session(session)
    s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": LOCK_KEY})
    before = current(s)
    current_rev = before.revision if before else 0
    if expected_revision != current_rev:
        raise RevisionConflict("ai_policy", "current", expected_revision, current_rev)
    params = dict(values, revision=current_rev + 1, approved_by=actor_user_id, reason=reason,
                  request_id=request_id, idempotency_key=idempotency_key)
    s.execute(text("""
        INSERT INTO ai_data_policies
            (revision, state, provider, allowed_categories, mask_names, mask_identity_numbers, mask_phones,
             mask_accounts, transcript_days, audio_days, draft_days, provider_log_days, approved_by, reason,
             request_id, idempotency_key)
        VALUES (:revision, :state, :provider, CAST(:allowed_categories AS text[]), :mask_names,
                :mask_identity_numbers, :mask_phones, :mask_accounts, :transcript_days, :audio_days,
                :draft_days, :provider_log_days, CAST(:approved_by AS uuid), :reason, :request_id,
                :idempotency_key)"""), params)
    after = current(s)
    audit.record("ai.policy.update", "ai_policy", str(after.revision),
                 before=before.snapshot() if before else None, after=after.snapshot(), reason=reason,
                 actor_user_id=actor_user_id, session=s)
    return after
