"""Phase 5 P3, LIVE: the short-lived credential against the WebRTC test PBX (pbx-webrtc-test).

The PBX reads PJSIP auths from this test database through its realtime backend (the view
pbx_ps_auths, migration 0056). A SIP REGISTER with digest authentication (Expires: 0, so it binds
nothing) proves what the PBX itself accepts:

* a freshly issued credential -> 200;  a wrong password -> 401;
* after a new issue the previous password -> 401 and the new one -> 200 (rotation);
* an expired, revoked or released credential -> 401 (the fallback "locked" auth answers).

Skipped unless the PBX is up AND reads this database:

    PBX_WEBRTC_DB_NAME=cawp_test_call3 docker compose --profile pbx-webrtc-test up -d pbx-webrtc-test
    docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call3 \\
        -e PBX_WEBRTC_TEST_DB=cawp_test_call3 api-test tests/test_calling_sip_live.py

Extension 7005 is used (the browser end-to-end run uses 7001/7002).
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import uuid

import pytest

from conftest import MAIN_DB
from test_calling_helpers import CL, cl, new_user, pgc, q1, run  # noqa: F401
from test_calling_sip import client, expire, hdr, issue, sip_app, sip_env  # noqa: F401

PBX_HOST = os.environ.get("PBX_WEBRTC_HOST", "pbx-webrtc-test")
EXT = "7005"
REALM = "cawp-webrtc-test"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()  # noqa: S324 - SIP digest


def _parse_challenge(value: str) -> dict:
    return {k.lower(): v for k, v in re.findall(r'(\w+)="?([^",]*)"?', value)}


def sip_register(extension: str, password: str, *, host: str = PBX_HOST, timeout: float = 3.0) -> int:
    """REGISTER (Expires: 0) over UDP, answering one digest challenge. Returns the final status."""
    addr = socket.gethostbyname(host)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.connect((addr, 5060))
    try:
        lip, lport = sock.getsockname()
        call_id, tag = uuid.uuid4().hex, uuid.uuid4().hex[:10]

        def send(cseq: int, auth: str | None = None) -> tuple[int, str]:
            lines = [f"REGISTER sip:{host} SIP/2.0",
                     f"Via: SIP/2.0/UDP {lip}:{lport};rport;branch=z9hG4bK{uuid.uuid4().hex}",
                     "Max-Forwards: 70", f"From: <sip:{extension}@{host}>;tag={tag}",
                     f"To: <sip:{extension}@{host}>", f"Call-ID: {call_id}", f"CSeq: {cseq} REGISTER",
                     f"Contact: <sip:{extension}@{lip}:{lport}>", "Expires: 0", "User-Agent: cawp-sip-probe"]
            if auth:
                lines.append(f"Authorization: {auth}")
            lines.append("Content-Length: 0")
            sock.send(("\r\n".join(lines) + "\r\n\r\n").encode())
            while True:
                data = sock.recv(65535).decode(errors="replace")
                status = int(data.split(" ", 2)[1])
                if status >= 200:
                    return status, data

        status, data = send(1)
        if status != 401:
            return status
        header = re.search(r"^WWW-Authenticate:\s*Digest\s+(.*)$", data, re.M | re.I).group(1)
        ch = _parse_challenge(header)
        uri = f"sip:{host}"
        ha1 = _md5(f"{extension}:{ch['realm']}:{password}")
        ha2 = _md5(f"REGISTER:{uri}")
        cnonce, nc = uuid.uuid4().hex[:16], "00000001"
        if "qop" in ch:
            response = _md5(f"{ha1}:{ch['nonce']}:{nc}:{cnonce}:auth:{ha2}")
            auth = (f'Digest username="{extension}", realm="{ch["realm"]}", nonce="{ch["nonce"]}", uri="{uri}", '
                    f'response="{response}", algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"')
        else:
            response = _md5(f"{ha1}:{ch['nonce']}:{ha2}")
            auth = (f'Digest username="{extension}", realm="{ch["realm"]}", nonce="{ch["nonce"]}", uri="{uri}", '
                    f'response="{response}", algorithm=MD5')
        if "opaque" in ch:
            auth += f', opaque="{ch["opaque"]}"'
        status, _ = send(2, auth)
        return status
    finally:
        sock.close()


@pytest.fixture(scope="module")
def pbx():
    if os.environ.get("PBX_WEBRTC_TEST_DB") != MAIN_DB:
        pytest.skip(f"the WebRTC test PBX must read this database: start it with PBX_WEBRTC_DB_NAME={MAIN_DB} "
                    f"and run with -e PBX_WEBRTC_TEST_DB={MAIN_DB}")
    try:
        status = sip_register(EXT, "probe-" + uuid.uuid4().hex)
    except (OSError, socket.timeout) as e:
        pytest.skip(f"WebRTC test PBX not reachable at {PBX_HOST}:5060 ({e}); "
                    f"docker compose --profile pbx-webrtc-test up -d pbx-webrtc-test")
    assert status == 401, status                 # nobody's credential: the locked fallback challenges
    return True


@pytest.fixture
def realtime_grant(pgc):
    """The PBX's read-only role (created by pbx-webrtc-test/e2e/setup_db.py) may read the view."""
    if q1(pgc, "SELECT 1 FROM pg_roles WHERE rolname = 'cawp_pbx_realtime'") is None:
        run(pgc, "CREATE ROLE cawp_pbx_realtime LOGIN PASSWORD 'pbx-realtime-test-only'")
    run(pgc, "GRANT SELECT ON pbx_ps_auths TO cawp_pbx_realtime")
    assert q1(pgc, "SELECT has_table_privilege('cawp_pbx_realtime', 'sip_credentials', 'SELECT')")[0] is False
    return True


@pytest.fixture
def owner_7005(pgc, monkeypatch):
    monkeypatch.setenv("SIP_REALM", REALM)
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'live test reset' "
             "WHERE extension = %s AND released_at IS NULL", CL.super, EXT)
    uid = new_user(pgc, name="Live 7005")
    xid = q1(pgc, "INSERT INTO user_extensions (extension, user_id, assigned_by, assign_reason) "
                  "VALUES (%s, %s, %s, 'live sip test') RETURNING id", EXT, uid, CL.super)[0]
    return uid, str(xid)


def test_pbx_accepts_only_the_current_unexpired_owned_credential(pbx, realtime_grant, owner_7005, client, hdr, pgc):
    uid, xid = owner_7005
    st, body, _ = issue(client, hdr, uid)
    assert st == 201, body
    first = body["data"]["password"]
    assert sip_register(EXT, first) == 200                          # the PBX accepts the fresh credential
    assert sip_register(EXT, first + "x") == 401                    # and nothing else

    st, body, _ = issue(client, hdr, uid)                           # rotation
    second, second_id = body["data"]["password"], body["data"]["credential_id"]
    assert sip_register(EXT, first) == 401                          # the superseded password is dead at once
    assert sip_register(EXT, second) == 200

    expire(pgc, second_id)
    assert sip_register(EXT, second) == 401                         # expiry: the PBX refuses it (DB clock)

    third = issue(client, hdr, uid)[1]["data"]["password"]
    assert sip_register(EXT, third) == 200
    assert client.post("/v2/me/sip-credentials/revoke", headers=hdr(uid)).status_code == 200
    assert sip_register(EXT, third) == 401                          # revoked

    fourth = issue(client, hdr, uid)[1]["data"]["password"]
    assert sip_register(EXT, fourth) == 200
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'live test' "
             "WHERE id = %s", CL.super, xid)
    assert sip_register(EXT, fourth) == 401                         # the extension was released
