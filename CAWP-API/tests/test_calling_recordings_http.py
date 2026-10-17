"""Recordings read over HTTP from the PBX file server (RECORDINGS_READER=http, services/calling_cdr.py
_read_http; deploy/README.md) and the recording-name resolver (issable/db_connection.py
genrate_call_path: bare in-ivr-<epoch>, names with '+', click-to-call cawp-<attempt>).

A local http.server stands in for the PBX (Apache on 10.0.40.7:8091 with Require ip + X-CAWP-Token):
the real PBX is never contacted."""
from __future__ import annotations

import datetime as dt
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.config import Config

ROOT = "/var/spool/asterisk/monitor/"
TOKEN = "t0ken-for-tests"
FILES = {
    "/2026/10/15/out-0500399975-115-20261015-100050-1792047650.140587.wav": b"RIFF....WAVEout",
    "/2026/10/15/in-ivr-1792072292.176850.wav": b"RIFF....WAVEivr",
    "/2026/10/15/out-%2B966512345678-110-20261015-104212-1791963732.98138.wav": b"RIFF....WAVEplus",
    "/2026/10/15/big.wav": b"x" * 5000,
    "/2026/10/15/empty.wav": b"",
}


class _Pbx(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):  # noqa: D401 - quiet
        pass

    def do_GET(self):  # noqa: N802
        _Pbx.hits.append((self.path, self.headers.get("X-CAWP-Token")))
        if self.headers.get("X-CAWP-Token") != TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/2026/10/15/moved.wav":
            self.send_response(302)
            self.send_header("Location", "/2026/10/15/elsewhere.wav")
            self.end_headers()
            return
        if self.path == "/2026/10/15/broken.wav":
            self.send_response(500)
            self.end_headers()
            return
        body = FILES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-wav")
        if self.path != "/2026/10/15/big.wav":              # the cap must hold without a Content-Length too
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def pbx():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Pbx)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def http_env(pbx, monkeypatch):
    monkeypatch.setattr(Config, "RECORDINGS_ROOT", ROOT)
    monkeypatch.setenv("RECORDINGS_READER", "http")
    monkeypatch.setenv("RECORDINGS_HTTP_BASE", pbx)
    monkeypatch.setenv("RECORDINGS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("RECORDINGS_HTTP_MAX_MB", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")          # never used: the reader bypasses proxies
    _Pbx.hits = []
    return pbx


@pytest.mark.no_db
def test_http_reader_reads_with_the_token(http_env):
    from services.calling_cdr import read_recording
    assert read_recording("out-0500399975-115-20261015-100050-1792047650.140587.wav") == b"RIFF....WAVEout"
    assert read_recording(ROOT + "2026/10/15/in-ivr-1792072292.176850.wav") == b"RIFF....WAVEivr"
    assert read_recording("2026/10/15/in-ivr-1792072292.176850.wav") == b"RIFF....WAVEivr"
    assert read_recording("out-+966512345678-110-20261015-104212-1791963732.98138.wav") == b"RIFF....WAVEplus"
    assert all(tok == TOKEN for _p, tok in _Pbx.hits)
    assert _Pbx.hits[-1][0].endswith("out-%2B966512345678-110-20261015-104212-1791963732.98138.wav")


@pytest.mark.no_db
def test_http_reader_missing_refused_and_capped(http_env, monkeypatch):
    from services.calling_cdr import read_recording
    assert read_recording("2026/10/15/nothere.wav") is None                            # 404 -> missing
    assert read_recording("2026/10/15/empty.wav") is None                              # empty = missing (legacy)
    assert read_recording("2026/10/15/broken.wav") is None
    n = len(_Pbx.hits)
    assert read_recording("2026/10/15/moved.wav") is None                              # a redirect is not followed
    assert [p for p, _t in _Pbx.hits[n:]] == ["/2026/10/15/moved.wav"]
    monkeypatch.setenv("RECORDINGS_HTTP_MAX_MB", "0.001")                               # ~1 kB
    assert read_recording("2026/10/15/big.wav") is None                                # streamed past the cap
    monkeypatch.setenv("RECORDINGS_HTTP_MAX_MB", "1")
    assert read_recording("2026/10/15/big.wav") == b"x" * 5000
    # paths the resolver refuses are never requested
    n = len(_Pbx.hits)
    for bad in ("2026/10/15/../../../etc/passwd", "/etc/passwd", "x; cat /etc/passwd", ""):
        assert read_recording(bad) is None
    assert len(_Pbx.hits) == n
    monkeypatch.setenv("RECORDINGS_HTTP_TOKEN", "wrong")
    assert read_recording("2026/10/15/in-ivr-1792072292.176850.wav") is None            # 403
    monkeypatch.setenv("RECORDINGS_HTTP_TOKEN", "")
    monkeypatch.setattr(Config, "RECORDINGS_HTTP_TOKEN", "")
    n = len(_Pbx.hits)
    assert read_recording("2026/10/15/in-ivr-1792072292.176850.wav") is None            # no token: not even asked
    assert len(_Pbx.hits) == n


@pytest.mark.no_db
def test_http_reader_unreachable_is_missing(monkeypatch):
    from services.calling_cdr import read_recording
    monkeypatch.setattr(Config, "RECORDINGS_ROOT", ROOT)
    monkeypatch.setenv("RECORDINGS_READER", "http")
    monkeypatch.setenv("RECORDINGS_HTTP_BASE", "http://127.0.0.1:9/")
    monkeypatch.setenv("RECORDINGS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("RECORDINGS_HTTP_TIMEOUT", "2")
    assert read_recording("2026/10/15/in-ivr-1792072292.176850.wav") is None


@pytest.mark.no_db
def test_name_resolver(monkeypatch):
    from issable.db_connection import Issable_db
    from services.calling_cdr import resolve_path
    monkeypatch.setattr(Config, "RECORDINGS_ROOT", ROOT)
    # unchanged: full paths, dated paths, out-/exten- names with their YYYYMMDD part
    assert resolve_path(ROOT + "2026/10/15/a.wav") == ROOT + "2026/10/15/a.wav"
    assert resolve_path("2026/10/14/in-ivr-1791959392.90145.wav") == ROOT + "2026/10/14/in-ivr-1791959392.90145.wav"
    assert resolve_path("out-0500399975-115-20261015-100050-1792047650.140587.wav") == \
        ROOT + "2026/10/15/out-0500399975-115-20261015-100050-1792047650.140587.wav"
    assert resolve_path("exten-100-134-20261013-150533-1791893133.78893.wav") == \
        ROOT + "2026/10/13/exten-100-134-20261013-150533-1791893133.78893.wav"
    # bare in-ivr-<uniqueid>: the day of the epoch in Asia/Riyadh (21:30 UTC = 00:30 the next day)
    assert resolve_path("in-ivr-1792072292.176850.wav") == ROOT + "2026/10/15/in-ivr-1792072292.176850.wav"
    late = int(dt.datetime(2026, 10, 15, 21, 30, tzinfo=dt.timezone.utc).timestamp())
    assert resolve_path(f"in-ivr-{late}.12.wav") == ROOT + f"2026/10/16/in-ivr-{late}.12.wav"
    # '+' in a name (an E.164 number) is accepted; shell characters still are not
    assert resolve_path("out-+966512345678-110-20261014-104212-1791963732.98138.wav") == \
        ROOT + "2026/10/14/out-+966512345678-110-20261014-104212-1791963732.98138.wav"
    assert Issable_db._validate_remote_path(ROOT + "2026/10/14/a b.wav") is None
    assert Issable_db._validate_remote_path(ROOT + "2026/10/14/a;b.wav") is None
    # click-to-call cawp-<attempt>: dated by the dialplan; a bare one needs the CDR calldate
    cawp = "cawp-00e6de54-476e-4f6b-910a-e385da1fae87.wav"
    assert resolve_path(f"2026/10/15/{cawp}") == ROOT + f"2026/10/15/{cawp}"
    assert resolve_path(cawp) is None
    assert resolve_path(cawp, dt.datetime(2026, 10, 15, 23, 59)) == ROOT + f"2026/10/15/{cawp}"
    assert resolve_path("odd.wav") is None and resolve_path("../x.wav", dt.date(2026, 10, 15)) is None


@pytest.mark.no_db
def test_bare_cawp_name_is_authorized_by_its_cdr_calldate(monkeypatch):
    from services import calling_cdr
    monkeypatch.setattr(Config, "RECORDINGS_ROOT", ROOT)
    cawp = "cawp-00e6de54-476e-4f6b-910a-e385da1fae87.wav"
    asked = []
    monkeypatch.setattr(calling_cdr, "_calldate_of", lambda name: asked.append(name) or dt.datetime(2026, 10, 15, 14, 22))
    monkeypatch.setattr(calling_cdr, "recording_records", lambda path, full: asked.append(full) or [])
    res = calling_cdr.authorize_recording("00000000-0000-4000-8000-000000000001", cawp)
    assert asked == [cawp, ROOT + f"2026/10/15/{cawp}"]
    assert res["full_path"] == ROOT + f"2026/10/15/{cawp}" and res["reason"] == "no_cdr_record"
    # a name the CDR does not know stays an invalid path; shell-ish input never reaches the CDR
    asked.clear()
    monkeypatch.setattr(calling_cdr, "_calldate_of", lambda name: asked.append(name) or None)
    assert calling_cdr.authorize_recording("u", "cawp-unknown.wav")["reason"] == "invalid_path"
    assert calling_cdr.authorize_recording("u", "x; cat /etc/passwd")["reason"] == "invalid_path"
    assert asked == ["cawp-unknown.wav"]
