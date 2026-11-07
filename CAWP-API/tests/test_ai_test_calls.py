"""AI calling agent test (customer request 2026-11-04, migration 0092): the generated dialplan, the API
(readiness, validation, one call at a time, stop), the FastAGI gate's token, and the AI voice service
end to end against a fake PBX (SIP digest challenge, ringing, answer, RTP both ways, BYE) and a fake
Realtime link. No real PBX or provider is ever contacted.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai4 api-test tests/test_ai_test_calls.py
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import struct

import pytest

from test_ai_helpers import (AI, ai, ai_env, configure, connect, insert_policy, key, pgc, q1, reset_ai_state,  # noqa: F401
                             run)

POLICY = {"trunks": ["Mobily18", "Mobily19"], "issabel_trunk_ids": {"Mobily18": 2, "Mobily19": 3}, "ring_seconds": 45,
          "max_call_seconds": 180, "agent_extension": "9990", "record": True,
          "calling_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}]}


# --------------------------------------------------------------------------- dialplan
def test_ai_dialplan_gate_peer_one_dial_and_causes():
    from services.calling import ai_dialplan as AD
    lines = AD.render(POLICY).splitlines()
    assert "exten => cawp-ai-agent,1,Goto(collections-ai-dial,s,1)" in lines
    peer = lines.index(' same => n,GotoIf($["${CHANNEL(peername)}" != "9990"]?deny)')
    agi = lines.index(" same => n,AGI(${GLOBAL(COLLECTIONS_MSG_AGI)}/collections-ai-authorize)")
    dials = [i for i, ln in enumerate(lines) if "Dial(SIP/${COLLECTIONS_TRUNK}/" in ln]
    assert len(dials) == 1 and peer < agi < dials[0]                  # who, then the token, then ONE Dial
    assert ' same => n,GotoIf($["${COLLECTIONS_AI_GATE}" != "ALLOW"]?deny)' in lines
    assert lines.index(" same => n,Set(COLLECTIONS_AI_DESTINATION=)") < agi   # only the hook sets the number
    assert "L(180000)" in lines[dials[0]] and ",45," in lines[dials[0]]
    assert {" same => n(busy),Hangup(17)", " same => n(noanswer),Hangup(19)", " same => n(deny),Hangup(21)"} <= set(lines)
    for bad in ({**POLICY, "agent_extension": "x"}, {**POLICY, "max_call_seconds": 5}, {**POLICY, "extra": 1}):
        with pytest.raises(AD.AiDialplanError):
            AD.render(bad)


# --------------------------------------------------------------------------- API
@pytest.fixture(scope="module")
def tc_app(ai):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.ai_test_calls
    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    app = Flask("cawp_test_ai_test_calls")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.ai_test_calls.bp)
    return app


@pytest.fixture
def client(tc_app, pgc):
    from core.auth import invalidate_permission_maps
    invalidate_permission_maps()
    reset_ai_state(pgc)
    run(pgc, "DELETE FROM ai_test_calls")
    return tc_app.test_client()


@pytest.fixture
def hdr(tc_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, idem=None):
        with tc_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h
    return make


def test_api_readiness_validation_one_at_a_time_and_stop(client, hdr, pgc, monkeypatch):
    body = {"destination": "0563287135", "instructions": "اختبار", "voice": "marin"}
    assert client.get("/v2/ai/test-calls/options", headers=hdr(AI.assist)).status_code == 403
    o = client.get("/v2/ai/test-calls/options", headers=hdr(AI.admin)).get_json()["data"]
    assert o["ready"] is False and "ai_policy_required" in o["not_ready_reasons"] and o["default_instructions"]
    r = client.post("/v2/ai/test-calls", json=body, headers=hdr(AI.admin, key()))
    assert r.status_code == 409 and r.get_json()["code"] == "ai_not_ready", r.get_json()
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=["audio"])                  # the transcripts category is missing
    r = client.post("/v2/ai/test-calls", json=body, headers=hdr(AI.admin, key()))
    assert r.status_code == 409 and "ai_policy_category_denied" in r.get_json()["error"]["reasons"]
    insert_policy(pgc, allowed_categories=["audio", "transcripts"])
    for bad, field in (({"destination": "12345"}, "destination"), ({"voice": "robot"}, "voice"),
                       ({"instructions": " "}, "instructions"), ({"caller_id": "+966500000000"}, "message_caller_ids")):
        r = client.post("/v2/ai/test-calls", json={**body, **bad}, headers=hdr(AI.admin, key()))
        assert r.status_code == 422 and field in r.get_json()["error"], r.get_json()
    r = client.post("/v2/ai/test-calls", json={**body, "caller_id": "+966125786530"}, headers=hdr(AI.admin, key()))
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]
    assert d["destination"] == "+966563287135" and d["state"] == "queued" and d["caller_id"] == "+966125786530"
    r = client.post("/v2/ai/test-calls", json=body, headers=hdr(AI.admin, key()))
    assert r.status_code == 409 and r.get_json()["code"] == "ai_test_call_active"
    r = client.post(f"/v2/ai/test-calls/{d['id']}/stop", headers=hdr(AI.admin))
    assert r.status_code == 200 and r.get_json()["data"]["state"] == "ended" and r.get_json()["data"]["result"] == "cancelled"
    items = client.get("/v2/ai/test-calls", headers=hdr(AI.admin)).get_json()["data"]["items"]
    assert [i["id"] for i in items] == [d["id"]]
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE action = 'ai_test_call.create'")[0] >= 1


def test_gate_token_is_one_shot_bound_and_short_lived(tc_app, client, hdr, pgc, monkeypatch):
    from extensions import db
    from services.ai import test_calls as TC
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=["audio", "transcripts"])
    d = client.post("/v2/ai/test-calls", json={"destination": "0563287135", "instructions": "x"},
                    headers=hdr(AI.admin, key())).get_json()["data"]
    with tc_app.app_context():
        row = TC.claim_next(db.session)
        token = TC.issue_token(db.session, row["id"] and str(row["id"]))
        db.session.commit()
        assert TC.authorize(db.session, call_id=d["id"], token="0" * 64)[1] == "token_mismatch"
        db.session.commit()
    run(pgc, "UPDATE ai_test_calls SET token_used_at = NULL WHERE id = %s", d["id"])
    with tc_app.app_context():
        ok_, reason, out = TC.authorize(db.session, call_id=d["id"], token=token)
        db.session.commit()
        assert (ok_, reason, out) == (True, "allowed", {"destination": "+966563287135", "caller_id": ""})
        assert TC.authorize(db.session, call_id=d["id"], token=token)[1] == "token_used"
        db.session.rollback()
    assert q1(pgc, "SELECT token_hash = %s FROM ai_test_calls WHERE id = %s",
              hashlib.sha256(token.encode()).hexdigest(), d["id"]) == (True,)


# --------------------------------------------------------------------------- the voice service end to end
class FakePbx(asyncio.DatagramProtocol):
    """INVITE -> 401 (digest) -> INVITE with credentials -> 100, 180, 200 + SDP; RTP both ways; BYE."""

    def __init__(self, secret, final=200):
        self.secret, self.final = secret, final
        self.transport = None
        self.invites = []
        self.acks = 0
        self.byes = 0
        self.headers = {}
        self.client = None
        self.nonce = "abc123"

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        from services.ai_voice import sip
        m = sip.parse(data)
        self.client = addr
        if m.is_response:
            return
        if m.method == "INVITE":
            self.invites.append(m)
            self.headers = {k: v for k, v in m.headers}
            auth = m.get("Authorization")
            if not auth:
                self.transport.sendto(sip.response_to(m, 401, "Unauthorized", to_tag="pbx1", extra=[
                    ("WWW-Authenticate", f'Digest realm="asterisk", nonce="{self.nonce}"')]).to_bytes(), addr)
                return
            ch = sip.parse_challenge(auth)
            ha1 = hashlib.md5(f"9990:asterisk:{self.secret}".encode()).hexdigest()
            ha2 = hashlib.md5(f"INVITE:{ch['uri']}".encode()).hexdigest()
            assert ch["response"] == hashlib.md5(f"{ha1}:{self.nonce}:{ha2}".encode()).hexdigest()
            self.transport.sendto(sip.response_to(m, 100, "Trying").to_bytes(), addr)
            self.transport.sendto(sip.response_to(m, 180, "Ringing", to_tag="pbx2").to_bytes(), addr)
            if self.final != 200:
                self.transport.sendto(sip.response_to(m, self.final, "Busy Here", to_tag="pbx2").to_bytes(), addr)
                return
            body = sip.sdp_offer("127.0.0.1", self.rtp_port, 1)
            self.transport.sendto(sip.response_to(m, 200, "OK", to_tag="pbx2", extra=[
                ("Contact", "<sip:cawp-ai-agent@127.0.0.1:%d>" % self.port), ("Content-Type", "application/sdp")],
                body=body).to_bytes(), addr)
        elif m.method == "ACK":
            self.acks += 1
        elif m.method == "BYE":
            self.byes += 1
            self.transport.sendto(sip.response_to(m, 200, "OK").to_bytes(), addr)
        elif m.method == "OPTIONS":
            self.transport.sendto(sip.response_to(m, 200, "OK").to_bytes(), addr)


class FakeRtp(asyncio.DatagramProtocol):
    def __init__(self):
        self.got = 0
        self.payloads = bytearray()
        self.transport = None
        self.peer = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.got += 1
        self.peer = addr
        self.payloads.extend(data[12:])


class FakeLink:
    def __init__(self, **kw):
        self.kw = kw
        self.appended = bytearray()
        self.greeted = False
        self.closed = asyncio.Event()

    async def open(self):
        pass

    async def greet(self):
        self.greeted = True
        self.kw["on_audio"](b"\x11" * 800)                      # the agent says something (100 ms)
        self.kw["on_transcript"]("agent", "السلام عليكم، معك مساعد آلي")

    async def append_audio(self, data):
        self.appended.extend(data)
        if len(self.appended) >= 1600 and not getattr(self, "_said", False):
            self._said = True
            self.kw["on_transcript"]("client", "مين معي؟")

    async def close(self):
        self.closed.set()


class FakeDb:
    def __init__(self):
        self.marks, self.transcripts, self.stop = [], [], False
        self.minted = None

    def mint(self, row):
        self.minted = row
        return "ek_fake", "gpt-test-realtime", "00000000-0000-4000-8000-0000000000aa"

    def issue_token(self, call_id):
        return "f" * 64

    def mark(self, call_id, **kw):
        self.marks.append(kw)

    def transcript(self, call_id, role, text_):
        self.transcripts.append((role, text_))

    def stop_requested(self, call_id):
        return self.stop


def _free_port(kind=socket.SOCK_DGRAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _scenario(final=200, client_hangs_up=True):
    from services.ai_voice.call import AiCall, SipTransport, VoiceConfig

    async def main():
        loop = asyncio.get_running_loop()
        pbx_port, sip_port, rtp_port, pbx_rtp = _free_port(), _free_port(), _free_port(), _free_port()
        pbx = FakePbx("s3cret", final)
        pbx.port, pbx.rtp_port = pbx_port, pbx_rtp
        await loop.create_datagram_endpoint(lambda: pbx, local_addr=("127.0.0.1", pbx_port))
        rtp = FakeRtp()
        await loop.create_datagram_endpoint(lambda: rtp, local_addr=("127.0.0.1", pbx_rtp))
        cfg = VoiceConfig(pbx_host="127.0.0.1", pbx_port=pbx_port, local_ip="127.0.0.1", sip_port=sip_port,
                          sip_user="9990", sip_secret="s3cret", rtp_min=rtp_port, rtp_max=rtp_port + 2,
                          transcribe_model="gpt-test-transcribe", max_seconds=20)
        sipt = SipTransport(cfg)
        await loop.create_datagram_endpoint(lambda: sipt, local_addr=("127.0.0.1", sip_port))
        db = FakeDb()
        links = []

        def factory(**kw):
            links.append(FakeLink(**kw))
            return links[-1]
        row = {"id": "11111111-1111-4111-8111-111111111111", "instructions": "قل مرحبا", "voice": "marin",
               "created_by": AI.admin}
        call = AiCall(row, cfg, sipt, db, factory, rtp_port)
        task = asyncio.create_task(call.run())
        if final == 200:
            for _ in range(100):                                # the PBX sends the client's voice
                await asyncio.sleep(0.02)
                if rtp.peer:
                    rtp.transport.sendto(struct.pack("!BBHII", 0x80, 0, _, _ * 160, 7) + b"\x22" * 160, rtp.peer)
            if client_hangs_up:
                from services.ai_voice import sip
                bye = sip.Message(f"BYE sip:9990@127.0.0.1:{sip_port} SIP/2.0", [
                    ("Via", f"SIP/2.0/UDP 127.0.0.1:{pbx_port};branch=z9hG4bKbye1"), ("From", "<sip:x@pbx>;tag=pbx2"),
                    ("To", f"<sip:9990@pbx>;tag={call.local_tag}"), ("Call-ID", call.sip_call_id), ("CSeq", "1 BYE")])
                pbx.transport.sendto(bye.to_bytes(), ("127.0.0.1", sip_port))
            else:
                db.stop = True
        await asyncio.wait_for(task, 15)
        return pbx, rtp, db, links, call
    return asyncio.run(main())


def test_voice_service_dials_with_digest_talks_both_ways_and_ends_on_the_clients_bye():
    pbx, rtp, db, links, call = _scenario()
    assert len(pbx.invites) == 2 and pbx.headers["X-CAWP-Call"] == "11111111-1111-4111-8111-111111111111"
    assert re.fullmatch(r"[0-9a-f]{64}", pbx.headers["X-CAWP-Token"]) and pbx.acks >= 2   # the 401 and the 200
    link = links[0]
    assert link.kw["secret"] == "ek_fake" and "end_call" in link.kw["instructions"]     # the fixed rules are added
    assert link.greeted and len(link.appended) >= 1600                                  # the client's voice reached the AI
    assert rtp.got > 10 and b"\x11" * 160 in bytes(rtp.payloads)                        # the AI's voice reached the PBX
    states = [m.get("state") for m in db.marks if m.get("state")]
    assert states[:2] == ["ringing", "answered"] and db.marks[-1]["result"] == "completed" and pbx.byes == 0
    assert ("agent", "السلام عليكم، معك مساعد آلي") in db.transcripts and ("client", "مين معي؟") in db.transcripts


def test_voice_service_stop_sends_bye_and_busy_ends_busy():
    pbx, rtp, db, links, call = _scenario(client_hangs_up=False)
    assert pbx.byes >= 1 and db.marks[-1]["result"] == "completed" and db.marks[-1]["state"] == "ended"
    pbx, rtp, db, links, call = _scenario(final=486)
    last = db.marks[-1]
    assert last["result"] == "busy" and last["sip_status"] == 486 and last["state"] == "ended"
    assert not any(m.get("state") == "answered" for m in db.marks)


def test_realtime_link_over_a_real_websocket():
    """The link against a local fake Realtime server: the bearer secret, the session (µ-law both ways,
    server VAD, Arabic transcription, the end_call tool), audio both ways, transcripts, barge-in, end_call."""
    import base64
    import json

    from websockets.asyncio.server import serve

    from services.ai_voice.realtime import RealtimeLink, ws_url
    seen = {"auth": None, "events": []}

    async def handler(ws):
        seen["auth"] = ws.request.headers.get("Authorization")
        seen["path"] = ws.request.path
        async for raw in ws:
            ev = json.loads(raw)
            seen["events"].append(ev)
            if ev["type"] == "response.create":
                await ws.send(json.dumps({"type": "response.output_audio.delta",
                                          "delta": base64.b64encode(b"\x33" * 320).decode()}))
                await ws.send(json.dumps({"type": "response.output_audio_transcript.done", "transcript": "أهلاً"}))
            elif ev["type"] == "input_audio_buffer.append":
                await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))
                await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                          "transcript": "تمام"}))
                await ws.send(json.dumps({"type": "response.done", "response": {"output": [
                    {"type": "function_call", "name": "end_call", "arguments": "{}"}]}}))

    async def main():
        got = {"audio": b"", "barge": 0, "tr": [], "end": 0, "err": []}
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            link = RealtimeLink(url=ws_url(f"http://127.0.0.1:{port}/v1", "gpt-test-realtime"), secret="ek_abc",
                                instructions="قل مرحبا", voice="marin", transcribe_model="gpt-test-transcribe",
                                on_audio=lambda b: got.__setitem__("audio", got["audio"] + b),
                                on_barge=lambda: got.__setitem__("barge", got["barge"] + 1),
                                on_transcript=lambda r, t: got["tr"].append((r, t)),
                                on_end_call=lambda: got.__setitem__("end", got["end"] + 1),
                                on_error=got["err"].append)
            await link.open()
            await link.greet()
            await link.append_audio(b"\x44" * 800)
            for _ in range(50):
                await asyncio.sleep(0.02)
                if got["end"]:
                    break
            await link.close()
        return got
    got = asyncio.run(main())
    assert seen["auth"] == "Bearer ek_abc" and seen["path"] == "/v1/realtime?model=gpt-test-realtime"
    s = seen["events"][0]["session"]
    assert seen["events"][0]["type"] == "session.update" and s["tools"][0]["name"] == "end_call"
    assert s["audio"]["input"]["format"] == {"type": "audio/pcmu"} and s["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    assert s["audio"]["input"]["transcription"] == {"model": "gpt-test-transcribe", "language": "ar"}
    sent = next(e for e in seen["events"] if e["type"] == "input_audio_buffer.append")
    assert base64.b64decode(sent["audio"]) == b"\x44" * 800
    assert got["audio"] == b"\x33" * 320 and got["barge"] == 1 and got["end"] == 1 and not got["err"]
    assert ("agent", "أهلاً") in got["tr"] and ("client", "تمام") in got["tr"]


def test_daily_limit_is_off_by_default_and_optional(client, hdr, pgc, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=["audio", "transcripts"])
    body = {"destination": "0563287135", "instructions": "اختبار"}

    def place():
        r = client.post("/v2/ai/test-calls", json=body, headers=hdr(AI.admin, key()))
        if r.status_code == 201:
            run(pgc, "UPDATE ai_test_calls SET state = 'ended', result = 'completed', ended_at = now() WHERE id = %s",
                r.get_json()["data"]["id"])
        return r
    monkeypatch.delenv("AI_TEST_CALLS_PER_DAY", raising=False)
    assert client.get("/v2/ai/test-calls/options", headers=hdr(AI.admin)).get_json()["data"]["limits"]["per_day"] == 0
    assert [place().status_code for _ in range(25)] == [201] * 25           # no daily limit
    monkeypatch.setenv("AI_TEST_CALLS_PER_DAY", "25")
    r = place()
    assert r.status_code == 429 and r.get_json()["code"] == "ai_test_call_limit"


def test_connect_failures_get_a_reason_the_page_explains():
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    from services.ai_voice.realtime import connect_failure_code
    quota = ConnectionClosedError(Close(1013, "insufficient_quota.credit_balance_exhausted"), None)
    assert connect_failure_code(quota) == "ai_credit_exhausted"
    assert connect_failure_code(ConnectionClosedError(Close(1008, "rate_limit_exceeded"), None)) == "ai_rate_limited"
    assert connect_failure_code(TimeoutError()) == "realtime_connect_failed"


def test_live_link_speaks_the_live_api_protocol():
    """gpt-live-1 (2026-11-05): session.start with µ-law 8 kHz and the voice, waits for session.started,
    audio both ways, transcript deltas flushed per side, session.close at the end; no end_call rule."""
    import base64
    import json

    from websockets.asyncio.server import serve

    from services.ai import gateway
    from services.ai import test_calls as TC
    from services.ai_voice.realtime import LiveLink, live_url
    assert gateway.is_live_model("gpt-live-1") and not gateway.is_live_model("gpt-realtime")
    assert "end_call" not in TC.full_instructions("x", live=True) and "end_call" in TC.full_instructions("x")
    seen = {"events": []}

    async def handler(ws):
        seen["auth"], seen["path"] = ws.request.headers.get("Authorization"), ws.request.path
        async for raw in ws:
            ev = json.loads(raw)
            seen["events"].append(ev)
            if ev["type"] == "session.start":
                await ws.send(json.dumps({"type": "session.started"}))
            elif ev["type"] == "session.commentary.append":
                assert ev["content"] and "delegation_id" in ev
                await ws.send(json.dumps({"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x55" * 160).decode()}))
                for part in ("السلام ", "عليكم"):
                    await ws.send(json.dumps({"type": "session.output_transcript.delta", "delta": part}))
            elif ev["type"] == "session.input_audio.append":
                await ws.send(json.dumps({"type": "session.input_transcript.delta", "delta": "ألو"}))
                await ws.send(json.dumps({"type": "session.output_transcript.delta", "delta": " أهلا"}))  # overlap
                await ws.send(json.dumps({"type": "session.input_transcript.delta", "delta": " نعم"}))
            elif ev["type"] == "session.close":
                await ws.send(json.dumps({"type": "session.closed", "usage": {}}))
                await ws.close()

    async def main():
        got = {"audio": b"", "tr": []}
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            link = LiveLink(url=live_url(f"http://127.0.0.1:{port}/v1"), api_key="sk-test", model="gpt-live-1",
                            instructions="قل مرحبا", voice="marin",
                            on_audio=lambda b: got.__setitem__("audio", got["audio"] + b), on_barge=lambda: None,
                            on_transcript=lambda r, t: got["tr"].append((r, t)), on_end_call=lambda: None,
                            on_error=lambda c: None)
            await link.open()
            await link.greet()
            await asyncio.sleep(0.1)
            await link.append_audio(b"\x44" * 800)
            await asyncio.sleep(1.5)
            await link.close()
        return got
    got = asyncio.run(main())
    assert seen["auth"] == "Bearer sk-test" and seen["path"] == "/v1/live/sessions"
    start = seen["events"][0]
    assert start["type"] == "session.start" and start["session"]["model"] == "gpt-live-1"
    assert start["session"]["audio"] == {"format": {"type": "audio/pcmu", "rate": 8000}, "output": {"voice": "marin"}}
    assert any(e["type"] == "session.close" for e in seen["events"])
    assert got["audio"] == b"\x55" * 160
    # a short overlap does not split a turn: the agent's line flushes on its pause, the client's on theirs
    assert got["tr"] == [("agent", "السلام عليكم أهلا"), ("client", "ألو نعم")] or \
        got["tr"] == [("agent", "السلام عليكم"), ("client", "ألو نعم"), ("agent", "أهلا")], got["tr"]


def test_open_live_session_is_policy_checked_and_audited(tc_app, client, pgc, monkeypatch):
    from extensions import db
    from services.ai import gateway
    configure(monkeypatch, realtime="gpt-live-1")
    with tc_app.app_context():
        with pytest.raises(gateway.AIRefused):                    # no approved policy yet
            gateway.open_live_session(purpose="ai_test_call", categories=("audio", "transcripts"), actor_user_id=AI.admin)
        db.session.commit()
    insert_policy(pgc, allowed_categories=["audio", "transcripts"])
    with tc_app.app_context():
        out = gateway.open_live_session(purpose="ai_test_call", categories=("audio", "transcripts"),
                                        entity_type="ai_test_call", entity_id="11111111-1111-4111-8111-111111111111",
                                        actor_user_id=AI.admin)
        db.session.commit()
    assert out.model == "gpt-live-1" and out.api_key and "api_key" not in repr(out)
    row = q1(pgc, "SELECT outcome, model, purpose FROM ai_provider_calls WHERE id = %s", out.call_id)
    assert row == ("succeeded", "gpt-live-1", "ai_test_call")
