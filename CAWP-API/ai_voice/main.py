"""`python -m ai_voice`: the AI voice service of the AI calling agent test (0092,
docs/alitqan-ai-calling-agent-plan.md; systemd unit `alitqan-ai-voice`, compose service `ai-voice`).

Off unless AI_VOICE_ENABLED=1 (and AI_VOICE_PBX_HOST, AI_VOICE_LOCAL_IP, AI_VOICE_SIP_USER,
AI_VOICE_SIP_SECRET are set). It then:

* at start, ends the rows a previous run left open (`failed` / `service_stopped`: unknown stays unknown);
* claims queued test calls (FOR UPDATE SKIP LOCKED) every AI_VOICE_POLL_SECONDS and runs each one
  (services/ai_voice/call.py): the Realtime session through the AI gateway (approved policy, quota,
  audit row; the permanent key never leaves the gateway), the one-shot token, the SIP INVITE to the PBX,
  the audio both ways, the transcript, the end;
* writes a heartbeat (ai_voice_status) every 5 s and sends a keep-alive OPTIONS to the PBX every 25 s;
* SIGTERM: ends the live calls (BYE), marks them `service_stopped`, exits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import threading

log = logging.getLogger("cawp.ai_voice")
MAX_CALLS = 4


def create_app():
    from flask import Flask

    from core.config import Config
    from extensions import db
    app = Flask("cawp-ai-voice")
    app.config.from_object(Config)
    opts = dict(Config.SQLALCHEMY_ENGINE_OPTIONS)
    opts.update(pool_size=3, max_overflow=3, pool_timeout=5)
    connect_args = dict(opts.get("connect_args") or {})
    connect_args.update(application_name="cawp-ai-voice", connect_timeout=3)
    opts["connect_args"] = connect_args
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts
    db.init_app(app)
    return app


class Db:
    """The synchronous database / gateway side of a call (run in worker threads)."""

    def __init__(self, app):
        self.app = app

    def _tx(self, fn):
        from extensions import db
        with self.app.app_context():
            try:
                out = fn(db.session)
                db.session.commit()
                return out
            except Exception:
                db.session.rollback()
                raise
            finally:
                db.session.remove()

    def claim(self):
        from services.ai import test_calls as TC
        return self._tx(TC.claim_next)

    def abandon(self):
        from services.ai import test_calls as TC
        return self._tx(TC.abandon_open)

    def mint(self, row):
        """-> (secret, model, ai_call_id, live). A Live API model (gpt-live-*) opens its own audited
        session with the server key; any other model gets an ephemeral Realtime secret."""
        from services.ai import config as ai_config
        from services.ai import gateway
        from services.ai import test_calls as TC
        with self.app.app_context():
            from extensions import db
            model = ai_config.load().model(ai_config.REALTIME_SESSION)
            try:
                if gateway.is_live_model(model):
                    out = gateway.open_live_session(
                        purpose="ai_test_call", categories=TC.CATEGORIES, entity_type="ai_test_call",
                        entity_id=str(row["id"]), actor_user_id=str(row["created_by"]))
                    db.session.commit()
                    return out.api_key, out.model, out.call_id, True
                out = gateway.mint_realtime_session(
                    purpose="ai_test_call", instructions=TC.full_instructions(row["instructions"]), voice=row["voice"],
                    expires_in_seconds=120, categories=TC.CATEGORIES, entity_type="ai_test_call",
                    entity_id=str(row["id"]), actor_user_id=str(row["created_by"]))
                db.session.commit()
                return out.client_secret, out.model, out.call_id, False
            except Exception:
                db.session.commit()                 # keep the gateway's refused/failed audit row
                raise
            finally:
                db.session.remove()

    def issue_token(self, call_id):
        from services.ai import test_calls as TC
        return self._tx(lambda s: TC.issue_token(s, call_id))

    def mark(self, call_id, **kw):
        from services.ai import test_calls as TC
        return self._tx(lambda s: TC.mark(s, call_id, **kw))

    def transcript(self, call_id, role, text_):
        from services.ai import test_calls as TC
        return self._tx(lambda s: TC.add_transcript(s, call_id, role, text_))

    def stop_requested(self, call_id):
        from services.ai import test_calls as TC
        return self._tx(lambda s: TC.stop_requested(s, call_id))

    def heartbeat(self, detail: dict):
        from sqlalchemy import text
        return self._tx(lambda s: s.execute(text("""
            INSERT INTO ai_voice_status (id, updated_at, detail) VALUES (1, now(), CAST(:d AS jsonb))
            ON CONFLICT (id) DO UPDATE SET updated_at = now(), detail = EXCLUDED.detail"""), {"d": json.dumps(detail)}))


def realtime_factory(*, secret, model, live=False, **kw):
    from services.ai import config as ai_config
    from services.ai_voice.realtime import LiveLink, RealtimeLink, live_url, ws_url
    base = ai_config.load().base_url
    if live:
        kw.pop("transcribe_model", None)
        return LiveLink(url=live_url(base), api_key=secret, model=model, **kw)
    return RealtimeLink(url=ws_url(base, model), secret=secret, **kw)


class Service:
    def __init__(self, cfg, db, factory=realtime_factory, poll_seconds: float = 1.0):
        from services.ai_voice.call import SipTransport
        self.cfg, self.db, self.factory, self.poll = cfg, db, factory, poll_seconds
        self.sip = SipTransport(cfg)
        self.calls: dict = {}
        self.ports = list(range(cfg.rtp_min - cfg.rtp_min % 2, cfg.rtp_max, 2))
        self.stopping = asyncio.Event()

    async def run(self):
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self.sip, local_addr=(self.cfg.local_ip, self.cfg.sip_port))
        n = await asyncio.to_thread(self.db.abandon)
        if n:
            log.info("ended %d test call(s) left open by a previous run", n)
        tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._beat()),
                 asyncio.create_task(self._keepalive())]
        await self.stopping.wait()
        for c in list(self.calls.values()):
            c.shutdown = True                        # an answered call says goodbye with a BYE
        if self.calls:
            await asyncio.wait([asyncio.ensure_future(t) for t in self._call_tasks()], timeout=10)
        for t in tasks:
            t.cancel()
        await asyncio.to_thread(self.db.abandon)

    def _call_tasks(self):
        return [c._task for c in self.calls.values() if getattr(c, "_task", None)]

    async def _poll(self):
        from services.ai_voice.call import AiCall
        while not self.stopping.is_set():
            try:
                if len(self.calls) < MAX_CALLS and self.ports:
                    row = await asyncio.to_thread(self.db.claim)
                    if row is not None:
                        port = self.ports.pop(0)
                        call = AiCall(row, self.cfg, self.sip, self.db, self.factory, port)
                        self.calls[call.call_id] = call
                        call._task = asyncio.create_task(self._run_call(call, port))
                        continue
            except Exception:  # noqa: BLE001
                log.exception("poll failed")
            await asyncio.sleep(self.poll)

    async def _run_call(self, call, port):
        try:
            await call.run()
        finally:
            self.calls.pop(call.call_id, None)
            self.ports.append(port)

    async def _beat(self):
        while True:
            try:
                await asyncio.to_thread(self.db.heartbeat, {"calls": len(self.calls), "host": socket.gethostname()})
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")
            await asyncio.sleep(5)

    async def _keepalive(self):
        while True:
            self.sip.keepalive()
            await asyncio.sleep(25)


def main(argv=None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    from services.ai_voice.call import VoiceConfig
    cfg = VoiceConfig.from_env()
    if os.getenv("AI_VOICE_ENABLED", "0") != "1" or cfg.missing():
        # off or not configured: stay up and idle (no socket), so systemd does not restart-loop
        log.info("AI voice service idle: %s", "AI_VOICE_ENABLED is not 1" if os.getenv("AI_VOICE_ENABLED", "0") != "1"
                 else "missing " + ", ".join(cfg.missing()))
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_a: stop.set())
        while not stop.wait(3600):
            pass
        return 0
    app = create_app()
    svc = Service(cfg, Db(app), poll_seconds=float(os.getenv("AI_VOICE_POLL_SECONDS", "1.0")))

    async def runner():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, svc.stopping.set)
        await svc.run()
    log.info("AI voice service: PBX %s:%s as %s, SIP %s:%s", cfg.pbx_host, cfg.pbx_port, cfg.sip_user, cfg.local_ip,
             cfg.sip_port)
    asyncio.run(runner())
    return 0
