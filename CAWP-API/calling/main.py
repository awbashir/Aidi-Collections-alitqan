"""`python -m calling`: the long-lived calling service (docker-compose service `calling`).

What it does when CALLING_ENABLED=1 (config: calling/config.py):

* owns ONE persistent AMI session to the PBX (services/calling/ami.py AmiSupervisor):
  login, Ping heartbeat, reconnect with backoff, and the PBX epoch after every login;
* consumes the event stream into evidence rows (services/calling/evidence.py): call_events,
  call_legs, pbx_sessions. Lost observation (session end, PBX restart, this service
  stopping or restarting) marks in-flight attempts `uncertain`; it NEVER releases a
  reservation;
* answers the FastAGI authorization hook (services/calling/agi.py) that the restricted
  dialplan calls before any customer leg is dialled;
* P1: dispatches click-to-calls (services/calling/dispatch.py Dispatcher): claims queued
  rows FOR UPDATE SKIP LOCKED, rechecks every rule, issues the one-shot token and sends ONE
  extension-first Originate on the live session (AMI Success = submitted);
* P1: reconciles (services/calling/reconcile.py Reconciler): re-projects evidence, marks
  attempts with missing evidence uncertain, and resolves uncertain attempts from the channel
  inventory (CoreShowChannels) and a CDR lookup, or leaves them for human review;
* P1: reports a heartbeat into calling_service_status every 2 s (AMI state, PBX epoch); the
  API dispatches only while a fresh heartbeat shows a connected session.
* P2: runs progressive campaigns (services/calling/campaign_dispatch.py CampaignDispatcher):
  one call per ready campaign member, authorized by the same code as POST /v2/calls and sent by
  the dispatcher above; only while this process has a connected AMI session and a running
  dispatcher. It keeps no state in memory: a restart neither releases a reservation nor
  duplicates a call.

With CALLING_ENABLED=0 (the default) it opens no socket at all and stays healthy, so the
container can be part of the stack without ever touching a PBX.

Health: the loop writes CALLING_HEALTH_FILE every few seconds; `python -m calling
--healthcheck` exits 0 while it is fresh and (when enabled) the AMI session has not been down
longer than CALLING_HEALTH_AMI_GRACE, and the database answers. An internal JSON status is
served on CALLING_STATUS_PORT (/status, /healthz) inside the compose network only.

SIGTERM/SIGINT: stop accepting AGI connections and wait (bounded) for in-flight answers, close
the AMI session (recorded as `calling_service_stopped`), write the final health file, exit 0.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

log = logging.getLogger("cawp.calling")


def _apply_database_override():
    url = os.environ.get("CALLING_DATABASE_URL")
    if url:
        os.environ["DATABASE_URL"] = url


def create_app():
    """A minimal Flask app (config + SQLAlchemy), like the worker; not app.py."""
    _apply_database_override()
    from flask import Flask

    from core.config import Config
    from extensions import db

    app = Flask("cawp-calling")
    app.config.from_object(Config)
    opts = dict(Config.SQLALCHEMY_ENGINE_OPTIONS)
    opts["pool_size"] = int(os.getenv("CALLING_DB_POOL_SIZE", "4"))
    opts["max_overflow"] = 4
    opts["pool_timeout"] = 5
    connect_args = dict(opts.get("connect_args") or {})
    connect_args["application_name"] = "cawp-calling"
    connect_args["connect_timeout"] = 3
    opts["connect_args"] = connect_args
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts
    db.init_app(app)
    return app


def database_label() -> str:
    """host/dbname of the database in use (never the credentials)."""
    try:
        parts = urlsplit(os.environ.get("DATABASE_URL", ""))
        return f"{parts.hostname}/{parts.path.lstrip('/')}"
    except ValueError:
        return "unknown"


class CallingService:
    def __init__(self, cfg, app=None):
        self.cfg = cfg
        self.app = app
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.stop_event = threading.Event()
        self.started_at = time.time()
        self.supervisor = None
        self.agi = None
        self.consumer = None
        self.status_server = None
        self.ami_down_since = time.time()
        self.recovered_on_start = None
        self.dispatcher = None
        self.reconciler = None
        self.campaigns = None
        self._status_failing = False

    # ---- lifecycle ------------------------------------------------------------------
    def start(self):
        if not self.cfg.enabled:
            log.info("calling service disabled (CALLING_ENABLED=0): no PBX connection, no AGI listener")
            self._start_status()
            return
        from services.calling.agi import FastAgiServer, PeerAllowlist
        from services.calling.ami import AmiSupervisor
        from services.calling.evidence import EvidenceConsumer

        self.recovered_on_start = self.startup_recovery()
        self.consumer = EvidenceConsumer(self.app, host_label=self.cfg.ami_host, worker_id=self.worker_id)
        if self.cfg.agi_enabled:
            self.agi = FastAgiServer(self.app, host=self.cfg.agi_bind, port=self.cfg.agi_port,
                                     secret=self.cfg.agi_secret,
                                     allowed_peers=PeerAllowlist(self.cfg.agi_allowed_peers))
            self.agi.start()
            log.info("FastAGI hook listening on %s:%s", *self.agi.address[:2])
        self.supervisor = AmiSupervisor(
            self.cfg.ami_config(), on_event=self.consumer.on_event,
            on_session_start=self._on_session_start, on_session_end=self._on_session_end,
            on_heartbeat=self.consumer.on_heartbeat,
            session_options={"heartbeat": self.cfg.heartbeat_seconds})
        self.supervisor.start()
        if self.cfg.dispatch_enabled:
            from services.calling.dispatch import Dispatcher
            self.dispatcher = Dispatcher(self.app, self.current_ctx, self.worker_id)
            self.dispatcher.start()
        if self.cfg.reconcile_seconds:
            from services.calling import cdr_lookup
            from services.calling.reconcile import Reconciler
            self.reconciler = Reconciler(self.app, self.current_ctx, cdr_lookup.from_env(),
                                         interval=self.cfg.reconcile_seconds)
            self.reconciler.start()
        if self.cfg.dispatch_enabled and self.cfg.campaigns_enabled:
            from services.calling.campaign_dispatch import CampaignDispatcher
            self.campaigns = CampaignDispatcher(self.app, self.campaign_transport, self.worker_id,
                                                poll=self.cfg.campaign_poll_ms / 1000.0)
            self.campaigns.start()
        self._start_status()
        log.info("calling service started (%s, PBX %s:%s, TLS %s, database %s)", self.worker_id,
                 self.cfg.ami_host, self.cfg.ami_port, "on" if self.cfg.ami_tls else "off", database_label())

    def current_ctx(self):
        """The live AMI session context (None while disconnected)."""
        sup = self.supervisor
        return sup.current if sup is not None else None

    def campaign_transport(self):
        """{"epoch": ...} while this process can send a call (connected session, dispatcher
        running), else None: campaign calls are then not authorized at all."""
        ctx = self.current_ctx()
        if ctx is None or not ctx.session.ready or self.dispatcher is None or not self.dispatcher.is_alive():
            return None
        return {"epoch": ctx.epoch, "epoch_uuid": ctx.identity.epoch_uuid, "worker_id": self.worker_id}

    def _on_session_start(self, ctx):
        self.consumer.on_session_start(ctx)
        self.ami_down_since = None

    def _on_session_end(self, ctx, code):
        self.ami_down_since = time.time()
        self.consumer.on_session_end(ctx, code)
        self.report_status()                 # the API must stop dispatching on this epoch at once

    def startup_recovery(self) -> dict:
        """A new process observed nothing of what an earlier one dispatched: in-flight attempts
        become `uncertain` (reason calling_service_restarted). Reservations stay exactly as
        they are - a restart never releases one."""
        from extensions import db
        from services.calling.attempts import active_reservations, mark_in_flight_uncertain
        with self.app.app_context():
            try:
                changed = mark_in_flight_uncertain("calling_service_restarted")
                held = active_reservations()
                # a dispatch that was being sent when the previous process died: its outcome is unknown
                from sqlalchemy import text
                db.session.execute(text("""
                    UPDATE call_dispatch_queue SET state = 'unacknowledged', finished_at = now(),
                           result_code = 'calling_service_restarted'
                     WHERE state = 'sending'"""))
                db.session.execute(text("""
                    UPDATE calling_service_status SET stopped_at = coalesce(stopped_at, now()), ami_connected = false
                     WHERE stopped_at IS NULL AND heartbeat_at < now() - interval '30 seconds'"""))
                db.session.commit()
            except Exception:  # noqa: BLE001 - e.g. migrations 0048-0051 not applied yet
                db.session.rollback()
                log.exception("startup recovery could not run (is the database migrated?)")
                return {"ok": False}
            finally:
                db.session.remove()
        if changed or held:
            log.warning("startup: %d in-flight attempt(s) marked uncertain; %d reservation(s) kept active",
                        len(changed), len(held))
        return {"ok": True, "uncertain": len(changed), "reservations_kept": len(held)}

    def shutdown(self):
        log.info("calling service stopping")
        if self.consumer is not None:
            self.consumer.stopping = True
        if self.campaigns is not None:
            self.campaigns.stop()
        if self.dispatcher is not None:
            self.dispatcher.stop()
        if self.reconciler is not None:
            self.reconciler.stop()
        if self.agi is not None:
            self.agi.stop()
        if self.supervisor is not None:
            self.supervisor.stop()
        if self.status_server is not None:
            self.status_server.shutdown()
        self.touch(final=True)
        log.info("calling service stopped")

    def run_forever(self) -> int:
        self.start()
        while not self.stop_event.wait(2):
            self.touch()
        self.shutdown()
        return 0

    def install_signals(self):
        def handler(signum, _frame):
            log.info("signal %s: shutting down", signum)
            self.stop_event.set()
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    # ---- status / health ------------------------------------------------------------
    def status(self) -> dict:
        ami = self.supervisor.status() if self.supervisor else None
        return {
            "enabled": self.cfg.enabled, "worker_id": self.worker_id, "started_at": self.started_at,
            "pbx_host": self.cfg.ami_host if self.cfg.enabled else None, "test_pbx": self.cfg.is_test_pbx,
            "database": database_label(), "ami": ami,
            "ami_down_since": self.ami_down_since if self.cfg.enabled else None,
            "agi": ({"listening": self.agi.is_alive(), "port": self.cfg.agi_port, **self.agi.stats}
                    if self.agi else None),
            "evidence": dict(self.consumer.stats) if self.consumer else None,
            "buffered_events": len(self.consumer.buffer) if self.consumer else 0,
            "startup_recovery": self.recovered_on_start,
            "dispatcher": ({"running": self.dispatcher.is_alive(), **self.dispatcher.stats}
                           if self.dispatcher else None),
            "reconciler": ({"running": self.reconciler.is_alive(), "last_pass_at": self.reconciler.last_pass_at,
                            **self.reconciler.stats} if self.reconciler else None),
            "campaigns": ({"running": self.campaigns.is_alive(), "last_tick_at": self.campaigns.last_tick_at,
                           **self.campaigns.stats} if self.campaigns else None),
        }

    def report_status(self, final: bool = False):
        """Upsert this process's heartbeat row (calling_service_status). Best effort."""
        if not self.cfg.enabled or self.app is None:
            return
        import json as _json

        from sqlalchemy import text

        from extensions import db
        ctx = self.current_ctx()
        connected = bool(ctx is not None and ctx.session.ready and not final)
        detail = {"ami_down_since": self.ami_down_since,
                  "dispatcher": dict(self.dispatcher.stats) if self.dispatcher else None,
                  "reconciler": dict(self.reconciler.stats) if self.reconciler else None,
                  "campaigns": dict(self.campaigns.stats) if self.campaigns else None}
        with self.app.app_context():
            try:
                db.session.execute(text("""
                    INSERT INTO calling_service_status (worker_id, host, heartbeat_at, ami_connected, epoch, epoch_uuid,
                                                        dispatcher, reconciler, stopped_at, detail)
                    VALUES (:w, :h, now(), :c, :ep, :eu, :d, :r, CASE WHEN :final THEN now() END, CAST(:det AS jsonb))
                    ON CONFLICT (worker_id) DO UPDATE
                       SET heartbeat_at = now(), ami_connected = EXCLUDED.ami_connected, epoch = EXCLUDED.epoch,
                           epoch_uuid = EXCLUDED.epoch_uuid, dispatcher = EXCLUDED.dispatcher,
                           reconciler = EXCLUDED.reconciler, stopped_at = EXCLUDED.stopped_at,
                           detail = EXCLUDED.detail"""), {
                    "w": self.worker_id, "h": self.cfg.ami_host, "c": connected,
                    "ep": ctx.epoch if ctx else None, "eu": ctx.identity.epoch_uuid if ctx else None,
                    "d": bool(self.dispatcher and self.dispatcher.is_alive() and not final),
                    "r": bool(self.reconciler and self.reconciler.is_alive() and not final), "final": final,
                    "det": _json.dumps(detail, default=str)})
                db.session.commit()
                if self._status_failing:
                    log.info("status heartbeat writes recovered")
                self._status_failing = False
            except Exception:  # noqa: BLE001 - e.g. the database (or migration 0052) is not there yet
                db.session.rollback()
                if not self._status_failing:
                    log.warning("could not write the status heartbeat (is migration 0052 applied?)", exc_info=True)
                self._status_failing = True
            finally:
                db.session.remove()

    def touch(self, final: bool = False):
        self.report_status(final)
        data = {"pid": os.getpid(), "at": time.time(), "final": final, **self.status()}
        try:
            tmp = self.cfg.health_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, default=str)
            os.replace(tmp, self.cfg.health_file)
        except OSError:
            log.exception("cannot write the health file %s", self.cfg.health_file)

    def _start_status(self):
        if not self.cfg.status_port:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path not in ("/status", "/healthz"):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = service.status()
                code = 200
                if self.path == "/healthz" and not healthy_status(body, service.cfg)[0]:
                    code = 503
                raw = json.dumps(body, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_a):
                pass

        try:
            self.status_server = ThreadingHTTPServer(("0.0.0.0", self.cfg.status_port), Handler)
        except OSError:
            log.exception("status port %s unavailable", self.cfg.status_port)
            return
        threading.Thread(target=self.status_server.serve_forever, name="calling-status", daemon=True).start()


def healthy_status(data: dict, cfg) -> tuple[bool, str]:
    if not data.get("enabled"):
        return True, "disabled (idle)"
    down = data.get("ami_down_since")
    if down and time.time() - float(down) > cfg.health_ami_grace:
        return False, f"AMI session down for {time.time() - float(down):.0f}s"
    agi = data.get("agi")
    if cfg.agi_enabled and (not agi or not agi.get("listening")):
        return False, "FastAGI listener is not running"
    disp = data.get("dispatcher")
    if getattr(cfg, "dispatch_enabled", False) and disp is not None and not disp.get("running"):
        return False, "click-to-call dispatcher is not running"
    camp = data.get("campaigns")
    if camp is not None and not camp.get("running"):
        return False, "campaign dispatcher is not running"
    return True, "ok"


def healthcheck(check_db: bool = True) -> int:
    from calling.config import load
    try:
        cfg = load()
    except Exception as e:  # noqa: BLE001
        print(f"unhealthy: configuration ({e})")
        return 1
    try:
        with open(cfg.health_file) as f:
            data = json.load(f)
        age = time.time() - float(data["at"])
    except (OSError, ValueError, KeyError) as e:
        print(f"unhealthy: no health file ({e})")
        return 1
    if age > cfg.health_max_age:
        print(f"unhealthy: the service loop last reported {age:.0f}s ago")
        return 1
    ok, why = healthy_status(data, cfg)
    if not ok:
        print(f"unhealthy: {why}")
        return 1
    if check_db and cfg.enabled:
        try:
            import psycopg2

            from manage_db import libpq_dsn
            _apply_database_override()
            conn = psycopg2.connect(libpq_dsn(os.environ["DATABASE_URL"]), connect_timeout=3)
            conn.cursor().execute("SELECT 1")
            conn.close()
        except Exception as e:  # noqa: BLE001
            print(f"unhealthy: database unreachable ({type(e).__name__})")
            return 1
    print(f"healthy: {why}; epoch {((data.get('ami') or {}) or {}).get('epoch')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m calling", description="CAWP calling service (AMI owner + FastAGI)")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 when the running service is healthy")
    parser.add_argument("--no-db", action="store_true", help="healthcheck without the database ping")
    parser.add_argument("--check-config", action="store_true", help="validate the configuration and exit")
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck(check_db=not args.no_db)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    from calling.config import ConfigError, load
    try:
        cfg = load()
    except ConfigError as e:
        log.error("invalid calling configuration: %s", e)
        return 2
    if args.check_config:
        print(json.dumps({"enabled": cfg.enabled, "pbx_host": cfg.ami_host, "test_pbx": cfg.is_test_pbx,
                          "tls": cfg.ami_tls, "agi": cfg.agi_enabled}))
        return 0
    app = create_app() if cfg.enabled else None
    service = CallingService(cfg, app)
    service.install_signals()
    return service.run_forever()


if __name__ == "__main__":
    sys.exit(main())
