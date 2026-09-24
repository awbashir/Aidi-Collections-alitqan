"""Fixtures for the Phase 6 Q1 AI draft tests (not a test module by itself).

* `ai1` (session): loads tests/fixtures/seed_ai_drafts.sql once (users, cases, notes, money, a plan,
  a follow-up, calls with wrap-ups and transcripts) after the Q0 seed.
* `dclient`: the Q0 test app client with a clean AI state (no policy, no provider-call rows, no drafts).
* `echo`: a provider fake that answers from a function of the request it received (so an answer can
  reuse the placeholders the masker chose); it records every request. No real provider is contacted.
* `ready()`: key + models + an approved policy (all categories unless told otherwise).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai1 api-test tests/test_ai_*.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_ai_helpers import (AI, ai, ai_app, ai_env, configure, connect, hdr, http, insert_policy, key,  # noqa: F401
                             pgc, q1, qall, reset_ai_state, responses_ok, run)

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_ai_drafts.sql"


class D:
    sara = "00000000-0000-4000-8000-0000000af501"
    omar = "00000000-0000-4000-8000-0000000af502"
    huda = "00000000-0000-4000-8000-0000000af503"
    case = "00000000-0000-4000-8000-0000000af601"
    case_other = "00000000-0000-4000-8000-0000000af602"
    case_locked = "00000000-0000-4000-8000-0000000af603"
    case_archived = "00000000-0000-4000-8000-0000000af604"
    call = "00000000-0000-4000-8000-0000000af801"          # sara, wrap-up + transcript
    call_omar = "00000000-0000-4000-8000-0000000af802"     # omar's call on sara's case, transcript, no wrap-up
    call_out = "00000000-0000-4000-8000-0000000af803"      # out of sara's scope
    call_no_transcript = "00000000-0000-4000-8000-0000000af804"
    status = "00000000-0000-4000-8000-000000000703"
    status_other_contract = "00000000-0000-4000-8000-0000000af703"
    method = "00000000-0000-4000-8000-000000000701"
    reach = "00000000-0000-4000-8000-000000000702"


# Values that must never reach the provider (or the AI audit) in clear text.
IDENTIFIERS = ("محمد عبد الله السبيعي", "السبيعي", "1098765432", "0551234567", "551234567", "966501112233",
               "0501112233", "ACC-5566778899", "5566778899", "سارة", "القحطاني", "Omar Nasser", "Nasser")
TRANSCRIPT_BIT = "I can pay 2000 after salary on the 27th"
NOTE_MARKER = "NOTE-MARKER-2"
FOLLOWUP_MARKER = "FOLLOWUP-MARKER"


class Echo:
    """Provider transport fake: `answers` holds dicts (answered as a Responses API success),
    callables (request JSON -> dict or HttpResponse) or HttpResponse objects."""

    def __init__(self):
        self.answers, self.requests = [], []

    def send(self, request):
        self.requests.append(request)
        if not self.answers:
            raise AssertionError("Echo: unexpected extra request")
        item = self.answers.pop(0)
        if callable(item):
            item = item(json.loads(request.body.decode("utf-8")))
        if isinstance(item, dict):
            return responses_ok(item)
        return item

    def json(self, i=-1):
        return json.loads(self.requests[i].body.decode("utf-8"))

    def payload(self, i=-1) -> dict:
        """The user message (the masked JSON payload) of request i."""
        return json.loads(self.json(i)["input"][0]["content"])

    def sent_text(self) -> str:
        return "\n".join(r.body.decode("utf-8") for r in self.requests)


def placeholder(body: dict, label: str = "NAME") -> str:
    """The first placeholder of a class in what was sent (so an answer can use it)."""
    m = re.search(rf"\[{label}_\d+\]", body["input"][0]["content"])
    return m.group(0) if m else "the debtor"


def answer(kind: str, **over):
    """A valid answer for each kind, using the debtor-name placeholder the masker chose."""
    def make(body):
        name = placeholder(body)
        base = {
            "call_script": {"opening": f"السلام عليكم، هل أتحدث مع {name}؟", "verification": ["Confirm the name"],
                            "talking_points": [{"point": "Outstanding balance 11500.00 SAR", "fact_ids": ["F5"]}],
                            "if_refused": [{"objection": "I cannot pay now", "response": "Offer the plan"}],
                            "next_step": "Agree a payment date", "cautions": ["No threats"]},
            "case_guidance": {"summary": f"{name} owes 11500.00 SAR",
                              "actions": [{"action": "Call about the overdue promise", "reason": "overdue",
                                           "priority": "high", "fact_ids": ["F3", "N1"]}],
                              "risks": ["Broken plan"], "missing_information": []},
            "narrative": {"title": "Case history", "summary": f"Debt of {name}",
                          "timeline": [{"date": "2026-08-15", "event": "Payment approved", "fact_ids": ["F7"]}],
                          "current_position": "Outstanding 11500.00 SAR", "open_issues": ["Plan overdue"],
                          "recommendation": "Supervisor call"},
            "call_summary": {"summary": f"Spoke with {name}", "debtor_position": "Will pay after salary",
                             "commitments_mentioned": ["2000 after salary (mentioned, not recorded)"],
                             "follow_up": "Call on the 27th", "suggested_note": f"{name} said he will pay 2000"},
            "coaching": {"overall": f"Clear call with {name}", "strengths": [], "improvements": [], "compliance_concerns": [],
                         "practice_tips": ["Summarise the agreement"]},
            "followup_message": {"message": f"عزيزي {name}، نرجو التواصل معنا بخصوص الرصيد المستحق.",
                                 "cautions": ["Do not send before verifying the number"]},
        }[kind]
        return {**base, **over}
    return make


@pytest.fixture(scope="session")
def ai1(ai):
    conn = connect()
    try:
        if q1(conn, "SELECT 1 FROM users WHERE id = %s", D.sara) is None:
            with conn.cursor() as cur:
                cur.execute(SEED.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


def reset_drafts(conn):
    with conn.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        cur.execute("DELETE FROM ai_drafts")
        cur.execute("SET session_replication_role = DEFAULT")


@pytest.fixture
def dclient(ai_app, pgc, ai1):
    from core.auth import invalidate_permission_maps
    from services.clients_scope import invalidate_scope
    invalidate_permission_maps()
    invalidate_scope()
    reset_ai_state(pgc)
    reset_drafts(pgc)
    return ai_app.test_client()


@pytest.fixture
def echo(ai_app):
    t = Echo()
    ai_app.config["AI_PROVIDER_TRANSPORT"] = t
    ai_app.config["AI_PROVIDER_SLEEP"] = lambda s: None
    yield t
    ai_app.config.pop("AI_PROVIDER_TRANSPORT", None)
    ai_app.config.pop("AI_PROVIDER_SLEEP", None)


def ready(monkeypatch, pgc, **policy):
    configure(monkeypatch)
    return insert_policy(pgc, **policy)


def generate(client, hdr, user=D.sara, idem=None, **body):
    body.setdefault("language", "en")
    r = client.post("/v2/ai/drafts", json=body, headers=hdr(user, idem or key()))
    return r.status_code, r.get_json(), r


def save_body(draft, **over):
    return {"expected_revision": draft["revision"], "text": draft["text"], "comment_status_id": D.status,
            "contact_method_id": D.method, "confirmed": True, **over}
