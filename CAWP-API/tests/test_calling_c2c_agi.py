"""Phase 5 P1: the FastAGI gate rechecks the case/contact safety rules right before the customer
is dialled (services/calling/rules.py agi_recheck), with P0's scripted fake Asterisk."""
from __future__ import annotations

import pytest

from test_calling_agi import EPOCH, FakeAsterisk, agi_server, decisions, token_for  # noqa: F401
from test_calling_helpers import (add_contact, cl, cl_app, insert_attempt, make_case, new_contract, new_user,  # noqa: F401
                                  pgc, q1, run)


@pytest.fixture
def gate(pgc):
    _c, pid = new_contract(pgc, "agi p1")
    u, sup = new_user(pgc), new_user(pgc)
    case = make_case(pgc, u, pid)
    contact = add_contact(pgc, case, "966500000001", default=True)

    def attempt():
        a = insert_attempt(pgc, case, u, state="agent_answered", extension="7201", contact_id=contact,
                           destination="+966500000001")
        return a, token_for(pgc, a, epoch=EPOCH)
    return {"case": case, "contact": contact, "attempt": attempt, "sup": sup, "user": u}


@pytest.mark.parametrize("change,reason", [
    ("UPDATE clints SET is_locked = true WHERE id = %(case)s", "case_locked"),
    ("UPDATE clints SET is_archive = true WHERE id = %(case)s", "case_archived"),
    ("INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%(case)s, 'x', 'manual', %(sup)s)",
     "do_not_call"),
    ("UPDATE \"ClintContacts\" SET is_blocked = true, is_default = false, blocked_at = now(), "
     "block_reason = 'wrong number' WHERE id = %(contact)s", "contact_blocked"),
])
def test_gate_refuses_when_a_safety_rule_changed_while_the_agent_rang(agi_server, gate, pgc, change, reason):
    a, token = gate["attempt"]()
    with pgc.cursor() as cur:
        cur.execute(change, gate)
    fake = FakeAsterisk(agi_server, attempt=a, token=token).run()
    assert not fake.allowed
    assert decisions(pgc, a) == [("deny", reason)]
    assert q1(pgc, "SELECT consumed_at FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] is None


def test_gate_refuses_a_case_with_a_blocking_review(agi_server, gate, pgc):
    a, token = gate["attempt"]()
    other = insert_attempt(pgc, gate["case"], gate["user"], state="prepared")
    run(pgc, "INSERT INTO call_review_flags (attempt_id, case_id, kind, reason, blocks_calling) "
             "VALUES (%s, %s, 'wrapup_needs_review', 'case_locked', true)", other, gate["case"])
    fake = FakeAsterisk(agi_server, attempt=a, token=token).run()
    assert not fake.allowed and decisions(pgc, a) == [("deny", "calling_review_pending")]


def test_gate_allows_when_nothing_changed(agi_server, gate, pgc):
    a, token = gate["attempt"]()
    fake = FakeAsterisk(agi_server, attempt=a, token=token).run()
    assert fake.allowed and decisions(pgc, a) == [("allow", "allowed")]
