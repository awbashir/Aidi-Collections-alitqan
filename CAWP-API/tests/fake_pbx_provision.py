"""In-process fake of the PBX extension provisioning API (Issabel port 8092, cawp-ext-provision;
CAWP-API/calling/issabel/ext-provision/index.php) for the provisioning tests. Not a test module.

    with FakeProvision(existing={"5100": "Agent 5100"}) as fake:
        monkeypatch.setenv("PBX_PROVISION_URL", fake.url); monkeypatch.setenv("PBX_PROVISION_TOKEN", fake.token)

Same routes, statuses and JSON as the real endpoint. Knobs: `hidden` (numbers that exist on the PBX
but are missing from the list: a create answers 409 exists), `in_call`, `ring_group`, `fail_next`
(code for the next create), `calls` (the requests it saw).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeProvision:
    def __init__(self, existing=None, managed=(), registered=()):
        self.token = secrets.token_hex(16)
        self.ext = {e: {"name": n, "managed": e in managed} for e, n in (existing or {}).items()}
        self.registered = set(registered)
        self.hidden: set[str] = set()
        self.in_call: set[str] = set()
        self.ring_group: set[str] = set()
        self.fail_next: str | None = None
        self.calls: list[tuple[str, str, dict | None]] = []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 - quiet
                pass

            def _reply(self, status, body):
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                if self.headers.get("X-CAWP-Token") != fake.token:
                    raw = b"<html>403 Forbidden</html>"
                    self.send_response(403)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                body = None
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    body = json.loads(self.rfile.read(n).decode())
                with fake.lock:
                    fake.calls.append((method, self.path, body))
                    status, out = fake.route(method, self.path, body or {})
                self._reply(status, out)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------------ the API
    def item(self, e):
        x = self.ext[e]
        return {"extension": e, "name": x["name"], "tech": "pjsip", "managed": x["managed"], "endpoint": True,
                "state": "In use" if e in self.in_call else ("Not in use" if e in self.registered else "Unavailable"),
                "registered": e in self.registered, "in_call": e in self.in_call,
                "channels": 1 if e in self.in_call else 0}

    @staticmethod
    def err(status, code, message="refused"):
        return status, {"ok": False, "http": status, "code": code, "message": message}

    def route(self, method, path, body):
        if path == "/v1/extensions" and method == "GET":
            items = [self.item(e) for e in sorted(self.ext, key=lambda k: (len(k), k)) if e not in self.hidden]
            return 200, {"ok": True, "asterisk": True, "range": [100, 199], "include_installed": True, "items": items}
        if path == "/v1/extensions" and method == "POST":
            e, name = str(body.get("extension") or ""), str(body.get("name") or "")
            if not re.fullmatch(r"[0-9]{2,6}", e):
                return self.err(422, "invalid_extension")
            if not name.strip():
                return self.err(422, "invalid_name")
            if self.fail_next:
                code, self.fail_next = self.fail_next, None
                return self.err(500 if code != "busy" else 503, code)
            if e in self.ext:
                return self.err(409, "exists", f"extension {e} already exists on the PBX")
            self.ext[e] = {"name": name, "managed": True}
            return 201, {"ok": True, "created": True, "item": self.item(e), "endpoint_verified": True, "webrtc": True}
        m = re.fullmatch(r"/v1/extensions/([0-9]{2,6})(/rename)?", path)
        if not m:
            return self.err(404, "no_route")
        e = m.group(1)
        if e not in self.ext:
            return self.err(404, "not_found", f"extension {e} does not exist on the PBX")
        if m.group(2) and method == "POST":
            self.ext[e]["name"] = body.get("name")
            return 200, {"ok": True, "renamed": True, "item": self.item(e)}
        if method == "GET":
            return 200, {"ok": True, "item": self.item(e)}
        if method == "DELETE":
            if not self.ext[e]["managed"]:
                return self.err(409, "not_managed")
            if e in self.in_call:
                return self.err(409, "in_call")
            if e in self.ring_group:
                return 409, {"ok": False, "http": 409, "code": "in_ring_group", "message": "ring group",
                             "ring_groups": ["600"]}
            del self.ext[e]
            return 200, {"ok": True, "deleted": True, "extension": e, "endpoint_gone": True}
        return self.err(404, "no_route")


def closed_port_url() -> str:
    """A URL nothing listens on (the PBX is down)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/"
