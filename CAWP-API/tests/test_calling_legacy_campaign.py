"""Progressive campaigns on Asterisk 11 (CALLING_PBX_EVENTS=legacy): the dispatcher records the
Originate's ActionID, the OriginateResponse Failure of the agent's extension fails the attempt with
the agent reason (by ActionID: Asterisk 11 names no channel), and the member is auto-paused - the
same housekeeping as on Asterisk 12+, with no new PBX dependency."""
from __future__ import annotations

import types

from services.calling import evidence as E
from services.calling.ami import PbxIdentity
from test_calling_campaign_helpers import (auth, case_for, cl, client, cp, cp_app, dispatched, fences, new_campaign,  # noqa: F401
                                           pgc, q1, ready, reservation, tick, transport, world)
from test_calling_legacy_pbx import LegacySession, originate_response


def test_member_is_paused_after_a_legacy_originate_failure(cp_app, client, auth, world, pgc, monkeypatch):
    from services.calling import dispatch as D
    monkeypatch.setenv("CALLING_PBX_EVENTS", "legacy")
    monkeypatch.setenv("CALLING_DRIVER", "SIP")
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    session = LegacySession()
    ctx = types.SimpleNamespace(session=session, previous=None, pbx_restarted=False,
                                identity=PbxIdentity(w.transport["epoch_uuid"], "2026-09-24T05:00:00", None,
                                                     "Asterisk Call Manager/1.3"))
    ctx.epoch = ctx.identity.epoch
    d = D.Dispatcher(cp_app, lambda: ctx, "test-worker")
    for _ in range(100):
        if not d.run_once():
            break
    sent = [s for s in session.sent if f"__COLLECTIONS_ATTEMPT={a1}" in [v for k, v in s[1] if k == "Variable"]]
    assert len(sent) == 1
    _action, fields, action_id = sent[0]
    assert dict(fields)["Channel"] == f"SIP/{w.ext1}"
    assert q1(pgc, "SELECT ami_action_id FROM call_dispatch_queue WHERE attempt_id = %s", a1)[0] == action_id
    cons = E.EvidenceConsumer(cp_app, host_label="issabel-test", worker_id="t")
    cons.on_session_start(ctx)
    dest = q1(pgc, "SELECT destination FROM call_attempts WHERE id = %s", a1)[0]
    cons.on_event(originate_response(action_id, w.ext1, None, ok=False, reason="3", exten=dest), ctx)   # no answer
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a1) == ("failed", "agent_no_answer")
    assert reservation(pgc, a1) == (False, "no_customer_contact")
    assert dispatched(tick(cp_app, cid, w)) == []
    assert q1(pgc, "SELECT state, state_reason FROM campaign_readiness WHERE campaign_id = %s AND user_id = %s",
              cid, w.c1) == ("paused", "agent_did_not_answer")
