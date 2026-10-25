"""Message campaigns (customer request 2026-10-25, migration 0088): the generated dialplan, the
audio conversion, the campaign fields, the dispatch (pace, exclusions, the one-shot token), the
FastAGI gate and the member pick, and the PBX events -> result -> note.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_message_campaigns.py
"""
from __future__ import annotations

import io
import math
import struct
import types
import uuid
import wave

import pytest

from test_calling_campaign_helpers import (CL, CP, act, auth, case_for, cl, client, cp, cp_app, dispatched,  # noqa: F401
                                           fences, in_app, key, make_case, pgc, q1, qall, ready, run, tick,
                                           transport, world)

POLICY = {"trunks": ["Mobily18", "Mobily19"], "issabel_trunk_ids": {"Mobily18": 2, "Mobily19": 3}, "ring_seconds": 30,
          "max_call_seconds": 600, "transfer_wait_seconds": 60, "member_ring_seconds": 20, "record": True,
          "calling_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}]}
CTX = types.SimpleNamespace(epoch="11111111-1111-4111-8111-111111111111@2026-10-25T10:41:56")


class Frame:
    def __init__(self, **kw):
        self.kw = {"Event": "UserEvent", "UserEvent": "CollectionsMessage", **kw}

    @property
    def event(self):
        return self.kw["Event"]

    def get(self, k, default=None):
        return self.kw.get(k, default)


def wav(seconds=1.0, rate=44100, channels=2, width=2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) * channels for i in range(int(rate * seconds)))
        w.writeframes(frames)
    return out.getvalue()


# --------------------------------------------------------------------------- dialplan
def test_message_dialplan_gate_one_dial_and_menu():
    from services.calling import message_dialplan as MD
    text = MD.render(POLICY)
    lines = text.splitlines()
    agi = lines.index(" same => n,AGI(${GLOBAL(COLLECTIONS_MSG_AGI)}/collections-message-authorize)")
    dials = [i for i, ln in enumerate(lines) if "Dial(SIP/${COLLECTIONS_TRUNK}/" in ln]
    assert len(dials) == 1 and agi < dials[0]                          # the gate before the ONE client Dial
    assert ' same => n,GotoIf($["${COLLECTIONS_MSG_GATE}" != "ALLOW"]?deny)' in lines
    assert ' same => n,GotoIf($["${COLLECTIONS_MSG_DESTINATION}" != "${EXTEN}"]?deny)' in lines
    assert [ln for ln in lines if "SIPPEER(" in ln] == [
        ' same => n,GotoIf($["${SIPPEER(Mobily18,status):0:2}" = "OK"]?trunk)',
        ' same => n,GotoIf($["${SIPPEER(Mobily19,status):0:2}" = "OK"]?trunk)']
    assert " same => n(trunk),Set(COLLECTIONS_CID=${GLOBAL(OUTCID_${COLLECTIONS_TRUNK_ID})})" in lines
    assert " same => n,Read(COLLECTIONS_MSG_KEY,${COLLECTIONS_MSG_SOUND}-message,1,,1,6)" in lines
    assert " same => n,AGI(${GLOBAL(COLLECTIONS_MSG_AGI)}/collections-message-agent)" in lines
    assert " same => n,Dial(SIP/${COLLECTIONS_MSG_AGENT},20,mU(collections-message-member-answered))" in lines
    assert " same => n,Set(COLLECTIONS_MSG_UNTIL=$[${EPOCH} + 60])" in lines
    assert "[collections-message-dial-end]" in text and "[collections-message-ivr-end]" in text
    assert "CollectionsCall" not in text                              # the click-to-call evidence never sees it
    for bad in ({**POLICY, "ring_seconds": 0}, {**POLICY, "issabel_trunk_ids": {"Mobily18": 2}},
                {**POLICY, "extra": 1}, {**POLICY, "record": "yes"}):
        with pytest.raises(MD.MessageDialplanError):
            MD.render(bad)


# --------------------------------------------------------------------------- audio
def test_audio_is_converted_to_8khz_mono_16bit_and_checked():
    from services.calling import message_audio as MA
    out, seconds = MA.convert(wav(1.0, 44100, 2, 2), "m.wav")
    with wave.open(io.BytesIO(out), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 8000)
    assert 0.9 <= seconds <= 1.1
    out8, _ = MA.convert(wav(0.5, 8000, 1, 1), "m.wav")                # 8-bit unsigned input
    assert out8[:4] == b"RIFF"
    for data, code in ((b"", "invalid_audio"), (b"not audio at all" * 10, "unsupported_format"),
                       (wav(121, 8000, 1, 2), "too_long")):
        with pytest.raises(MA.AudioError) as e:
            MA.convert(data, "x")
        assert e.value.code in (code, "invalid_audio")


# --------------------------------------------------------------------------- the campaign
def _status(pgc, name):
    sid = str(uuid.uuid4())
    run(pgc, """INSERT INTO "CommentStatuses" (id, name, "CommentReachability_id", is_active) VALUES (%s, %s, %s, true)""",
        sid, f"{name} {sid[:6]}", CL.reach)
    return sid


def _message_campaign(client, auth, w, pgc, *, members=None, start=True, max_concurrent=2, notes=None):
    body = {"name": "msg", "kind": "message", "pool": {"contract_ids": [w.contract]},
            "member_ids": members if members is not None else [w.c1, w.c2],
            "settings": {"max_attempts_per_day": 5, "cooldown_minutes": 0}, "max_concurrent": max_concurrent}
    if notes is not None:
        body["message_notes"] = notes
    r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()), json=body)
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["data"]["id"]
    w.fences.add(cid)
    if start:
        for kind in ("message", "callback"):
            run(pgc, """INSERT INTO campaign_sounds (campaign_id, kind, audio, duration_seconds, sha256, pbx_installed_at)
                        VALUES (%s, %s, '\\x52494646', 3.0, %s, now())""", cid, kind, "a" * 64)
        st, body = act(client, auth, w.supervisor, cid, "start")
        assert st == 200, body
    return cid


def load(cp_app, cid):
    from services.calling import campaign_dispatch as CD
    return in_app(cp_app, lambda s: CD.load_campaign(s, cid))


def test_message_campaign_fields_validation_and_start_needs_the_audio(cp_app, client, auth, world, pgc):
    w = world
    base = {"name": "m", "kind": "message", "pool": {"contract_ids": [w.contract]}, "member_ids": [w.c1]}
    for extra, field in (({"max_concurrent": 21}, "max_concurrent"), ({"fill_random": True}, "fill_random"),
                         ({"message_notes": {"closed": {"status_id": str(uuid.uuid4())}}}, "message_notes.closed"),
                         ({"kind": "robo"}, "kind")):
        r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()), json={**base, **extra})
        assert r.status_code == 422 and field in r.get_json()["error"], r.get_json()
    r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()),
                    json={"name": "a", "pool": {"contract_ids": [w.contract]}, "max_concurrent": 3})
    assert r.status_code == 422 and r.get_json()["error"]["max_concurrent"] == "not_applicable"
    cid = _message_campaign(client, auth, w, pgc, start=False, notes={"closed": {"status_id": _status(pgc, "closed")},
                                                                      "contact_method_id": CL.method})
    d = client.get(f"/v2/campaigns/{cid}", headers=auth(w.supervisor)).get_json()["data"]
    assert d["kind"] == "message" and d["message"]["max_concurrent"] == 2 and d["message"]["transfer"] is True
    assert set(d["message"]["notes"]) == {"closed", "contact_method_id"}
    st, body = act(client, auth, w.supervisor, cid, "start")
    assert st == 409 and body["code"] == "sounds_missing" and body["error"]["missing"] == ["callback", "message"]
    # an agent's "next" means nothing in a message campaign
    run(pgc, """INSERT INTO campaign_sounds (campaign_id, kind, audio, duration_seconds, sha256, pbx_installed_at)
                SELECT %s, k, '\\x52494646', 3.0, %s, now() FROM unnest(ARRAY['message', 'callback']) k""", cid, "b" * 64)
    assert act(client, auth, w.supervisor, cid, "start")[0] == 200
    r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(w.c1, key()), json={})
    assert r.status_code == 409 and r.get_json()["code"] == "message_campaign"


# --------------------------------------------------------------------------- dispatch
def test_dispatch_paces_tokens_and_never_two_calls_to_one_client(cp_app, client, auth, world, pgc):
    from services.calling import message_campaign as MC
    w = world
    a = case_for(pgc, w.c1, w.portfolio)[0]
    b = case_for(pgc, w.c2, w.portfolio)[0]
    c = case_for(pgc, w.c1, w.portfolio)[0]
    run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'opt-out', 'manual', %s)",
        c, w.supervisor)                                                  # do-not-call: never dialled
    cid = _message_campaign(client, auth, w, pgc, max_concurrent=2)
    camp = load(cp_app, cid)
    jobs = [in_app(cp_app, lambda s: MC.dispatch_one(s, camp, CTX)) for _ in range(3)]
    assert all(isinstance(j, MC.Job) for j in jobs[:2]) and jobs[2] is None       # the pace: 2 at once
    rows = qall(pgc, "SELECT case_id::text, state, token_hash, destination FROM message_calls WHERE campaign_id = %s", cid)
    assert {r[0] for r in rows} == {a, b} and all(r[1] == "sending" and len(r[2]) == 64 for r in rows)
    fields = dict((k, v) for k, v in jobs[0].fields if k != "Variable")
    variables = [v for k, v in jobs[0].fields if k == "Variable"]
    assert fields["Channel"].startswith("Local/+9665") and fields["Channel"].endswith("@collections-message-dial/n")
    assert fields["Context"] == "collections-message-ivr" and f"__COLLECTIONS_MSG_CAMPAIGN={cid}" in variables
    token = next(v.split("=", 1)[1] for v in variables if v.startswith("COLLECTIONS_MSG_TOKEN="))
    assert MC.token_hash(token) in {r[2] for r in rows}                  # only the hash is stored
    # a live message call blocks click-to-call on that client
    from services.calling import rules as R
    with pytest.raises(R.RuleRefused) as e:
        own = q1(pgc, 'SELECT id FROM "ClintContacts" WHERE "identityNumber" = (SELECT "identityNumber" FROM clints '
                      'WHERE id = %s) LIMIT 1', a)[0]
        in_app(cp_app, lambda s: R.evaluate(actor_id=w.c1, case_id=a, contact_id=own))
    assert e.value.code == "calling_reserved" and e.value.detail["reserved"] == "case"
    # the agent dispatcher never dials a message campaign
    ready(pgc, cid, w.c1)
    assert dispatched(tick(cp_app, cid, w)) == []


def test_agi_gate_consumes_the_token_once_and_checks_everything(cp_app, client, auth, world, pgc):
    from services.calling import message_campaign as MC
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = _message_campaign(client, auth, w, pgc, max_concurrent=1)
    camp = load(cp_app, cid)
    job = in_app(cp_app, lambda s: MC.dispatch_one(s, camp, CTX))
    token = next(v.split("=", 1)[1] for k, v in job.fields if k == "Variable" and v.startswith("COLLECTIONS_MSG_TOKEN="))
    dest = q1(pgc, "SELECT destination FROM message_calls WHERE id = %s", job.call_id)[0]
    ok = lambda **kw: in_app(cp_app, lambda s: MC.authorize(s, **{"call_id": job.call_id, "token": token,  # noqa: E731
                                                                     "destination": dest, "epoch_uuid": None, **kw}))
    assert ok(token="0" * 64) == (False, "token_mismatch")
    assert ok(destination="+966500000000") == (False, "destination_mismatch")   # consumed by that attempt
    assert ok() == (False, "token_used")                                         # never granted twice
    # the next call (a client never dialled comes first) is allowed once, with its own token
    run(pgc, "UPDATE message_calls SET state = 'ended' WHERE id = %s", job.call_id)
    case2 = case_for(pgc, w.c2, w.portfolio)[0]
    job2 = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    assert isinstance(job2, MC.Job)
    token2 = next(v.split("=", 1)[1] for k, v in job2.fields if k == "Variable" and v.startswith("COLLECTIONS_MSG_TOKEN="))
    dest2 = q1(pgc, "SELECT destination, case_id::text FROM message_calls WHERE id = %s", job2.call_id)
    assert dest2[1] == case2
    assert in_app(cp_app, lambda s: MC.authorize(s, call_id=job2.call_id, token=token2, destination=dest2[0],
                                                  epoch_uuid=None)) == (True, "allowed")


# --------------------------------------------------------------------------- events, member, note
def _answered_call(cp_app, client, auth, w, pgc, **kw):
    from services.calling import message_campaign as MC
    case = case_for(pgc, w.c1, w.portfolio)[0]
    cid = _message_campaign(client, auth, w, pgc, max_concurrent=1, **kw)
    job = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    in_app(cp_app, lambda s: MC.record_originate(s, job.call_id, "submitted"))
    ev = lambda **f: in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId=job.call_id, **f)))  # noqa: E731
    ev(State="dialing", Trunk="Mobily18")
    ev(State="answered", Recording=f"2026/10/25/cawp-msg-{job.call_id}.wav")
    return cid, case, job.call_id, ev


def test_unanswered_call_ends_with_the_dial_status(cp_app, client, auth, world, pgc):
    from services.calling import message_campaign as MC
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = _message_campaign(client, auth, w, pgc, max_concurrent=1)
    job = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    in_app(cp_app, lambda s: MC.record_originate(s, job.call_id, "submitted"))
    in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId=job.call_id, State="dial_end", DialStatus="CONGESTION", Fail="")))
    assert q1(pgc, "SELECT state, result, dial_status FROM message_calls WHERE id = %s", job.call_id) == (
        "ended", "congestion", "CONGESTION")
    # a denied gate is a failed dial, never an answer
    case_for(pgc, w.c2, w.portfolio)
    job2 = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId=job2.call_id, State="dial_end", DialStatus="", Fail="denied")))
    assert q1(pgc, "SELECT state, result FROM message_calls WHERE id = %s", job2.call_id) == ("failed", "denied")


def test_key_2_closes_records_the_note_and_the_client_is_never_called_again(cp_app, client, auth, world, pgc):
    from services.calling import message_campaign as MC
    w = world
    closed = _status(pgc, "msg closed")
    cid, case, call, ev = _answered_call(cp_app, client, auth, w, pgc,
                                         notes={"closed": {"status_id": closed}, "contact_method_id": CL.method})
    ev(State="key", Key="2")
    ev(State="result", Result="closed")
    ev(State="ended", Cause="16")
    row = q1(pgc, "SELECT state, result, ivr_key, note_id::text, recording FROM message_calls WHERE id = %s", call)
    assert row[:3] == ("ended", "closed", "2") and row[3] and row[4].endswith(f"cawp-msg-{call}.wav")
    note = q1(pgc, 'SELECT clint_id::text, comment_status_id::text, user_id::text FROM "Comments" WHERE id = %s', row[3])
    assert note == (case, closed, w.supervisor)                          # author = the campaign's creator
    # the same client (any account) is never dialled again by this campaign
    other = make_case(pgc, w.c2, w.portfolio)
    run(pgc, 'UPDATE clints SET "identityNumber" = (SELECT "identityNumber" FROM clints WHERE id = %s) WHERE id = %s',
        case, other)
    assert in_app(cp_app, lambda s: MC.candidates(s, load(cp_app, cid))) == []


def test_key_1_rings_a_free_member_and_a_missed_member_frees_them(cp_app, client, auth, world, pgc):
    from services.calling import message_campaign as MC
    w = world
    cid, case, call, ev = _answered_call(cp_app, client, auth, w, pgc)
    assert in_app(cp_app, lambda s: MC.pick_member(s, call_id=call)) is None       # nobody ready
    ready(pgc, cid, w.c1, lease_seconds=300)
    ev(State="key", Key="1")
    ext = in_app(cp_app, lambda s: MC.pick_member(s, call_id=call))
    assert ext == w.ext1
    assert q1(pgc, "SELECT state, member_user_id::text FROM message_calls WHERE id = %s", call) == ("transferring", w.c1)
    ev(State="member_missed", Extension=ext, DialStatus="NOANSWER")
    assert q1(pgc, "SELECT state, member_user_id FROM message_calls WHERE id = %s", call) == ("answered", None)
    assert in_app(cp_app, lambda s: MC.pick_member(s, call_id=call)) == w.ext1
    ev(State="member_answered")
    # the member may open that client while talking (the campaign grant)
    from services.calling.campaign_rules import campaign_grant
    assert in_app(cp_app, lambda s: campaign_grant(w.c1, case)) is True
    me = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert me["message_transfer"]["case"]["id"] == case and me["message_transfer"]["state"] == "talking"
    ev(State="ended", Cause="16")                                        # the client hung up while talking
    assert q1(pgc, "SELECT state, result FROM message_calls WHERE id = %s", call) == ("ended", "transferred")
    live = client.get(f"/v2/campaigns/{cid}/live", headers=auth(w.supervisor)).get_json()["data"]["messages"]
    assert live["results"] == {"transferred": 1} and live["clients_answered"] == 1
    calls = client.get(f"/v2/campaigns/{cid}/message-calls", headers=auth(w.supervisor)).get_json()["data"]
    assert calls["items"][0]["result"] == "transferred" and calls["items"][0]["member"]["id"] == w.c1


def test_no_agent_and_hang_up_during_the_message(cp_app, client, auth, world, pgc):
    w = world
    noagent = _status(pgc, "msg no agent")
    cid, case, call, ev = _answered_call(cp_app, client, auth, w, pgc,
                                         notes={"no_agent": {"status_id": noagent}, "contact_method_id": CL.method})
    ev(State="key", Key="1")
    ev(State="result", Result="no_agent")
    ev(State="ended")
    row = q1(pgc, "SELECT result, note_id IS NOT NULL FROM message_calls WHERE id = %s", call)
    assert row == ("no_agent", True)
    # "transferred" without a member who answered is not believed
    from services.calling import message_campaign as MC
    case2 = case_for(pgc, w.c2, w.portfolio)[0]
    job = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    assert q1(pgc, "SELECT case_id::text FROM message_calls WHERE id = %s", job.call_id) == (case2,)
    in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId=job.call_id, State="answered")))
    in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId=job.call_id, State="ended")))   # hung up mid-message
    assert q1(pgc, "SELECT state, result, reason_code FROM message_calls WHERE id = %s", job.call_id) == (
        "ended", "hung_up", "client_hung_up")
    # unknown or malformed frames change nothing
    assert in_app(cp_app, lambda s: MC.on_event(s, Frame(CallId="not-a-uuid", State="ended"))) is None
    assert in_app(cp_app, lambda s: MC.on_event(s, Frame(UserEvent="CollectionsCall", CallId=call, State="ended"))) is None


# --------------------------------------------------------------------------- the FastAGI protocol
@pytest.fixture
def msg_agi(cp_app):
    from services.calling.agi import FastAgiServer, PeerAllowlist
    from test_calling_agi import AGI_KEY
    srv = FastAgiServer(cp_app, host="127.0.0.1", port=0, secret=AGI_KEY,
                        allowed_peers=PeerAllowlist(["127.0.0.1/32"]), exchange_timeout=1.5)
    srv.start()
    yield srv
    srv.stop()


def test_agi_message_scripts_over_the_protocol(cp_app, client, auth, world, pgc, msg_agi):
    from services.calling import message_campaign as MC
    from test_calling_agi import FakeAsterisk
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = _message_campaign(client, auth, w, pgc, max_concurrent=1)
    job = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    token = next(v.split("=", 1)[1] for k, v in job.fields if k == "Variable" and v.startswith("COLLECTIONS_MSG_TOKEN="))
    dest = q1(pgc, "SELECT destination FROM message_calls WHERE id = %s", job.call_id)[0]
    env = {"agi_network_script": "collections-message-authorize", "agi_extension": dest,
           "agi_channel": "Local/+966@collections-message-dial-00000001;2", "agi_context": "collections-message-dial"}
    vars_ = {"COLLECTIONS_MSG_CALL": job.call_id, "COLLECTIONS_MSG_TOKEN": token}
    same_pbx = {"COLLECTIONS_HUMAN_PBX_EPOCH": CTX.epoch.split("@")[0]}
    fake = FakeAsterisk(msg_agi, attempt=job.call_id, token=token, env=env, channel_vars=vars_, globals_=same_pbx).run()
    sets = [c for c in fake.commands if c.startswith("SET VARIABLE")]
    assert sets[0] == 'SET VARIABLE COLLECTIONS_MSG_GATE ""' and sets[-1] == 'SET VARIABLE COLLECTIONS_MSG_GATE "ALLOW"'
    assert fake.vars["COLLECTIONS_MSG_DESTINATION"] == dest
    assert q1(pgc, "SELECT token_used_at IS NOT NULL FROM message_calls WHERE id = %s", job.call_id) == (True,)
    again = FakeAsterisk(msg_agi, attempt=job.call_id, token=token, env=env, channel_vars=vars_, globals_=same_pbx).run()
    assert again.vars.get("COLLECTIONS_MSG_GATE") == ""                    # one-shot: never twice
    assert q1(pgc, "SELECT reason_code FROM message_calls WHERE id = %s", job.call_id) == ("token_used",)
    wrong_key = FakeAsterisk(msg_agi, attempt=job.call_id, token=token, env=env, channel_vars=vars_,
                             globals_={"COLLECTIONS_AGI_KEY": "nope-nope-nope"}).run()
    assert wrong_key.vars.get("COLLECTIONS_MSG_GATE") == ""
    # another PBX process than the one the call was sent to: denied (the token is spent either way)
    case_for(pgc, w.c2, w.portfolio)
    run(pgc, "UPDATE message_calls SET state = 'ended' WHERE id = %s", job.call_id)
    job2 = in_app(cp_app, lambda s: MC.dispatch_one(s, load(cp_app, cid), CTX))
    tok2 = next(v.split("=", 1)[1] for k, v in job2.fields if k == "Variable" and v.startswith("COLLECTIONS_MSG_TOKEN="))
    dest2 = q1(pgc, "SELECT destination FROM message_calls WHERE id = %s", job2.call_id)[0]
    other = FakeAsterisk(msg_agi, attempt=job2.call_id, token=tok2, env={**env, "agi_extension": dest2},
                         channel_vars={"COLLECTIONS_MSG_CALL": job2.call_id, "COLLECTIONS_MSG_TOKEN": tok2}).run()
    assert other.vars.get("COLLECTIONS_MSG_GATE") == ""
    assert q1(pgc, "SELECT reason_code FROM message_calls WHERE id = %s", job2.call_id) == ("epoch_changed",)
    run(pgc, "UPDATE message_calls SET state = 'answered' WHERE id = %s", job.call_id)
    # key 1: the member's extension
    ready(pgc, cid, w.c2, lease_seconds=300)
    agent = FakeAsterisk(msg_agi, attempt=job.call_id, token="", channel_vars={"COLLECTIONS_MSG_CALL": job.call_id},
                         env={"agi_network_script": "collections-message-agent", "agi_context": "collections-message-ivr",
                              "agi_extension": "s"}).run()
    assert agent.vars["COLLECTIONS_MSG_AGENT"] == w.ext2
