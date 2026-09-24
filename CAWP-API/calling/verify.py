"""Commissioning checks the operator runs from the calling host: `python -m calling.verify`.

Read-only by default. It connects with the dedicated AMI account (settings from the same
CALLING_* environment as the service, or flags) and checks, printing PASS / WARN / FAIL:

  tls            AMI over TLS with a verified certificate (or an explicit loopback tunnel)
  login          the dedicated account logs in; the greeting / AMI version
  events         PBX events carry the `Timestamp` header (manager.conf timestampevents=yes)
  epoch          global COLLECTIONS_HUMAN_PBX_EPOCH is a UUIDv4 (the pre-start hook ran)
  startup        CoreStatus gives the process start time (the second half of the epoch)
  privileges     ListCommands: Originate, Getvar, CoreStatus, CoreShowChannels allowed;
                 Command, Setvar, Redirect, Reload, ModuleLoad, Atxfer, Bridge NOT allowed
  refusals       the PBX itself refuses Command, Setvar and application Originate (harmless probes
                 that would fail safely even on an over-privileged account)
  dialplan       ShowDialPlan of the restricted context: AGI via GLOBAL(COLLECTIONS_AGI_URL),
                 the ALLOW check, TIMEOUT(absolute), the fixed trunk Dial with only i/b/U/L options
  channels       CoreShowChannels (the inventory P1 reconciliation uses)
  agi            the FastAGI port is reachable from THIS host (the PBX side is pbx-verify.sh)

Optional, and only with --place-deny-test EXTENSION --i-am-at-the-extension: originates to that
TEST extension with NO token. Expected: the phone rings; after you answer, the call is refused by
the AGI hook and hangs up; UserEvents agent_answered -> denied; no customer leg is ever created.
It never dials a customer number (the destination is a reserved +999 test number that the gate
refuses anyway).

Exit code 0 when nothing FAILed. Secrets are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import uuid

from services.calling.ami import (AmiConfig, AmiDecoder, AmiError, AmiSession, encode_action, read_identity)

MUST_ALLOW = ("Originate", "Getvar", "CoreStatus", "CoreShowChannels", "Ping")
MUST_DENY = ("Command", "Setvar", "Redirect", "Reload", "ModuleLoad", "Atxfer", "Bridge", "UpdateConfig",
             "GetConfig", "Filter", "DBPut")
DENY_TEST_NUMBER = "+99900000000"                       # never routable; the gate refuses it too


class Report:
    def __init__(self):
        self.items = []

    def add(self, status, check, detail=""):
        self.items.append({"status": status, "check": check, "detail": detail})
        print(f"{status:<5} {check:<12} {detail}", flush=True)

    @property
    def failed(self):
        return any(i["status"] == "FAIL" for i in self.items)


def _raw_probe(cfg: AmiConfig, actions):
    """Login on a raw socket and send each action; returns {ActionID: (Response, Message)}."""
    import ssl
    sock = socket.create_connection((cfg.host, cfg.port), timeout=5)
    if cfg.tls:
        ctx = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=cfg.server_name or cfg.host)
    dec, frames, out = AmiDecoder(), [], {}
    try:
        def pump(until):
            end = time.monotonic() + 5
            while time.monotonic() < end:
                for fr in frames:
                    if hasattr(fr, "fields") and fr.event is None and fr.get("ActionID") == until:
                        return fr
                frames.extend(dec.feed(sock.recv(65536)))
            return None
        frames.extend(dec.feed(sock.recv(4096)))
        sock.sendall(encode_action([("Action", "Login"), ("ActionID", "v-login"), ("Username", cfg.username),
                                    ("Secret", cfg.secret), ("Events", "off")]))
        login = pump("v-login")
        if login is None or login.response != "Success":
            return None
        for aid, fields in actions:
            sock.sendall(encode_action([*fields[:1], ("ActionID", aid), *fields[1:]]))
            fr = pump(aid)
            out[aid] = (fr.response, fr.get("Message")) if fr else (None, "no response")
        return out
    finally:
        sock.close()


def run_checks(cfg: AmiConfig, *, context: str, trunk: str | None, agi_host: str | None, agi_port: int,
               deny_extension: str | None) -> Report:
    rep = Report()
    events, lock = [], threading.Lock()

    def on_event(frame, _s):
        with lock:
            events.append(frame)

    try:
        s = AmiSession(cfg, on_event, heartbeat=0).connect()
    except AmiError as e:
        rep.add("FAIL", "login", f"cannot open the AMI session ({e.code})")
        return rep
    rep.add("PASS", "tls", "verified TLS" if cfg.tls else "plaintext (loopback tunnel only)")
    rep.add("PASS", "login", f"{cfg.username} on {cfg.host}:{cfg.port} ({s.banner})")
    try:
        ident = read_identity(s)
        if ident.epoch_uuid:
            rep.add("PASS", "epoch", f"COLLECTIONS_HUMAN_PBX_EPOCH = {ident.epoch_uuid}")
        else:
            rep.add("FAIL", "epoch", "global COLLECTIONS_HUMAN_PBX_EPOCH missing or not a UUIDv4 (install the "
                                      "pre-start hook, see the runbook)")
        rep.add("PASS" if ident.startup else "FAIL", "startup",
                f"process started {ident.startup} (epoch {ident.epoch})" if ident.startup else "CoreStatus refused")

        allowed = set(_list_commands(cfg))       # ListCommands: the actions this account may run
        if not allowed:
            rep.add("FAIL", "privileges", "ListCommands returned nothing")
        else:
            missing = [x for x in MUST_ALLOW if x not in allowed]
            extra = [x for x in MUST_DENY if x in allowed]
            if missing:
                rep.add("FAIL", "privileges", f"not allowed but needed: {', '.join(missing)}")
            if extra:
                rep.add("FAIL", "privileges", f"too much privilege, remove: {', '.join(extra)} "
                                              "(write should be originate,reporting)")
            if not missing and not extra:
                rep.add("PASS", "privileges", f"{len(allowed)} actions; only originate + reporting")

        refusals = _raw_probe(cfg, [
            ("v-cmd", [("Action", "Command"), ("Command", "core show uptime")]),
            ("v-set", [("Action", "Setvar"), ("Channel", "PJSIP/cawp-verify-no-such-channel"),
                       ("Variable", "CAWP_VERIFY"), ("Value", "1")]),
            ("v-app", [("Action", "Originate"), ("Channel", "PJSIP/cawp-verify-no-such-endpoint"),
                       ("Application", "System"), ("Data", "true"), ("Async", "false"), ("Timeout", "1000")]),
        ]) or {}
        bad = [k for k, (resp, msg) in refusals.items()
               if not (resp == "Error" and re.search(r"permission denied|forbidden", msg or "", re.I))]
        rep.add("FAIL" if bad or not refusals else "PASS", "refusals",
                f"not refused: {', '.join(bad)}" if bad else "Command, Setvar and application Originate refused")

        _check_dialplan(s, cfg, rep, context, trunk)

        try:
            rows = s.list_channels()
            rep.add("PASS", "channels", f"CoreShowChannels: {len(rows)} active channel(s)")
        except AmiError as e:
            rep.add("FAIL", "channels", f"CoreShowChannels failed ({e.code})")

        if agi_host:
            try:
                socket.create_connection((agi_host, agi_port), timeout=3).close()
                rep.add("PASS", "agi", f"FastAGI {agi_host}:{agi_port} reachable from this host "
                                       "(check from the PBX with pbx-verify.sh)")
            except OSError:
                rep.add("FAIL", "agi", f"FastAGI {agi_host}:{agi_port} not reachable")

        if deny_extension:
            _deny_test(s, events, lock, rep, context, deny_extension)

        # timestampevents: the FullyBooted greeting event never carries it, so look at real events
        time.sleep(0.5 if deny_extension else 3)
        with lock:
            real = [f for f in events if f.event and f.event != "FullyBooted"]
        if not real:
            rep.add("WARN", "events", "no PBX event seen yet; re-run during traffic or with --place-deny-test "
                                      "to check timestampevents=yes")
        elif all(f.get("Timestamp") for f in real):
            rep.add("PASS", "events", f"{len(real)} event(s), all with the PBX Timestamp (timestampevents=yes)")
        else:
            rep.add("FAIL", "events", "events carry no Timestamp header: set timestampevents=yes in manager.conf")
    finally:
        s.close()
    return rep


def _list_commands(cfg: AmiConfig) -> list[str]:
    import ssl
    sock = socket.create_connection((cfg.host, cfg.port), timeout=5)
    if cfg.tls:
        ctx = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=cfg.server_name or cfg.host)
    dec, frames = AmiDecoder(), []
    try:
        frames.extend(dec.feed(sock.recv(4096)))
        sock.sendall(encode_action([("Action", "Login"), ("ActionID", "l"), ("Username", cfg.username),
                                    ("Secret", cfg.secret), ("Events", "off")]))
        sock.sendall(encode_action([("Action", "ListCommands"), ("ActionID", "lc")]))
        end = time.monotonic() + 5
        while time.monotonic() < end:
            for fr in frames:
                if hasattr(fr, "fields") and fr.get("ActionID") == "lc" and fr.event is None:
                    return [k for k, _v in fr.fields if k not in ("Response", "ActionID", "Message")]
            frames.extend(dec.feed(sock.recv(65536)))
        return []
    finally:
        sock.close()


def _check_dialplan(s: AmiSession, cfg: AmiConfig, rep: Report, context: str, trunk: str | None):
    """ShowDialPlan needs `reporting`: collect the restricted context's priorities."""
    import ssl
    sock = socket.create_connection((cfg.host, cfg.port), timeout=5)
    if cfg.tls:
        ctx = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=cfg.server_name or cfg.host)
    dec, frames, apps = AmiDecoder(), [], []
    try:
        frames.extend(dec.feed(sock.recv(4096)))
        sock.sendall(encode_action([("Action", "Login"), ("ActionID", "l"), ("Username", cfg.username),
                                    ("Secret", cfg.secret), ("Events", "off")]))
        sock.sendall(encode_action([("Action", "ShowDialPlan"), ("ActionID", "dp"), ("Context", context)]))
        end, done = time.monotonic() + 8, False
        while time.monotonic() < end and not done:
            frames.extend(dec.feed(sock.recv(65536)))
            for fr in frames:
                if not hasattr(fr, "fields") or fr.get("ActionID") != "dp":
                    continue
                if fr.event is None and fr.response == "Error":
                    done = True
                if fr.event == "ListDialplan" and fr.get("Context") == context:
                    apps.append((fr.get("Application") or "", fr.get("AppData") or ""))
                if fr.event == "ShowDialPlanComplete":
                    done = True
            frames = [f for f in frames if not (hasattr(f, "fields") and f.get("ActionID") == "dp")]
    finally:
        sock.close()
    if not apps:
        rep.add("FAIL", "dialplan", f"context {context!r} not found (install the generated dialplan)")
        return
    text = [f"{a}({d})" for a, d in apps]
    problems = []
    if "AGI(${GLOBAL(COLLECTIONS_AGI_URL)})" not in text:
        problems.append("AGI hook via GLOBAL(COLLECTIONS_AGI_URL) missing")
    if not any('"${COLLECTIONS_GATE}" != "ALLOW"' in d for _a, d in apps):
        problems.append("ALLOW check missing")
    if not any(a == "Set" and d.startswith("TIMEOUT(absolute)=") for a, d in apps):
        problems.append("TIMEOUT(absolute) missing")
    dials = [d for a, d in apps if a == "Dial"]
    if len(dials) != 1:
        problems.append(f"expected exactly one Dial, found {len(dials)}")
    else:
        target, _ring, options = (dials[0].split(",", 2) + ["", ""])[:3]
        if trunk and not (target.endswith("@" + trunk) or target.startswith(f"SIP/{trunk}/")):
            problems.append(f"Dial target {target!r} is not the fixed trunk {trunk!r}")
        stripped = options
        while re.search(r"\([^()]*\)", stripped):
            stripped = re.sub(r"\([^()]*\)", "", stripped)
        if set(stripped) - set("ibUL"):
            problems.append(f"Dial has extra options {stripped!r} (only i, b, U, L are generated)")
    rep.add("FAIL" if problems else "PASS", "dialplan",
            "; ".join(problems) if problems else f"{context}: {len(apps)} priorities, gate + fixed trunk Dial")


def _deny_test(s: AmiSession, events, lock, rep: Report, context: str, extension: str):
    attempt = str(uuid.uuid4())
    chan = f"cawp-{attempt}"
    ack = s.send("Originate", [("Channel", f"PJSIP/{extension}"), ("Context", context), ("Exten", DENY_TEST_NUMBER),
                               ("Priority", "1"), ("Async", "true"), ("Timeout", "30000"), ("ChannelId", chan),
                               ("Variable", f"__COLLECTIONS_ATTEMPT={attempt}")])
    if ack.response != "Success":
        rep.add("FAIL", "deny-test", f"Originate refused: {ack.get('Message')}")
        return
    print(f"      ringing test extension {extension}: answer it within 30 s", flush=True)
    end = time.monotonic() + 45
    while time.monotonic() < end:
        with lock:
            mine = [f for f in events if f.get("Linkedid") == chan or f.get("Uniqueid") == chan]
        if any(f.event == "Hangup" and f.get("Uniqueid") == chan for f in mine):
            break
        time.sleep(0.3)
    states = [f.get("State") for f in mine if f.event == "UserEvent"]
    dialled = [f for f in mine if f.event == "DialBegin" and f.get("Uniqueid") == chan]
    if "agent_answered" not in states:
        rep.add("WARN", "deny-test", "the test extension did not answer; nothing was checked")
    elif "denied" in states and not dialled and "customer_dialing" not in states:
        rep.add("PASS", "deny-test", "answered, refused by the AGI hook, no customer leg")
    else:
        rep.add("FAIL", "deny-test", f"unexpected flow: states={states} customer_legs={len(dialled)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m calling.verify", description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default=os.environ.get("CALLING_AMI_HOST"))
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--no-tls", action="store_true", help="plaintext (only for a loopback tunnel)")
    p.add_argument("--ca-file", default=os.environ.get("CALLING_AMI_CA_FILE") or None)
    p.add_argument("--server-name", default=os.environ.get("CALLING_AMI_SERVER_NAME") or None)
    p.add_argument("--username", default=os.environ.get("CALLING_AMI_USERNAME"))
    p.add_argument("--context", default="collections-human-outbound")
    p.add_argument("--trunk", default=None, help="the outbound trunk the generated dialplan must dial")
    p.add_argument("--agi-host", default=None, help="the calling host as the PBX reaches it (reachability only)")
    p.add_argument("--agi-port", type=int, default=4573)
    p.add_argument("--place-deny-test", metavar="EXTENSION", default=None)
    p.add_argument("--i-am-at-the-extension", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    secret = os.environ.get("CALLING_AMI_SECRET")
    if not a.host or not a.username or not secret:
        print("set CALLING_AMI_HOST, CALLING_AMI_USERNAME and CALLING_AMI_SECRET (or --host/--username)",
              file=sys.stderr)
        return 2
    if a.place_deny_test and not a.i_am_at_the_extension:
        print("--place-deny-test rings a real extension: add --i-am-at-the-extension to confirm", file=sys.stderr)
        return 2
    tls = not a.no_tls
    cfg = AmiConfig(a.host, a.port or (5039 if tls else 5038), a.username, secret, tls=tls, ca_file=a.ca_file,
                    server_name=a.server_name)
    try:
        cfg.validate()
    except AmiError as e:
        print(f"configuration refused: {e.code}", file=sys.stderr)
        return 2
    rep = run_checks(cfg, context=a.context, trunk=a.trunk, agi_host=a.agi_host, agi_port=a.agi_port,
                     deny_extension=a.place_deny_test)
    if a.json:
        print(json.dumps(rep.items, indent=1))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
