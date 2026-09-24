"""Fixtures and helpers for the Phase 5 P1 click-to-call tests (not a test module by itself).

Builds on tests/test_calling_helpers.py (P0). Everything is created per test (users, extension
ownership, cases, contacts); the singleton calling-window policy is set explicitly by the
tests that depend on it. `transport` fakes a connected calling service for the API (a unique
PBX epoch per test, so the real calling service, when it runs against this database, never
claims these rows: it only sends rows enqueued for its own epoch).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call1 api-test tests/test_calling_c2c_*.py
"""
from __future__ import annotations

import json
import random
import types
import uuid

import pytest

from test_calling_helpers import (CL, add_contact, cl, cl_app, cl_client, hdr, key, make_case, new_contract,  # noqa: F401
                                  new_supervisor, new_user, note_body, pgc, q1, qall, run)

OPEN = [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}]


def set_windows(conn, windows=OPEN, holidays=()):
    run(conn, """
        INSERT INTO calling_window_policy (id, windows, holidays, revision, reason)
        VALUES (1, %s::jsonb, %s::jsonb, 1, 'c2c test')
        ON CONFLICT (id) DO UPDATE SET windows = EXCLUDED.windows, holidays = EXCLUDED.holidays,
               revision = calling_window_policy.revision + 1, updated_at = now()""",
        json.dumps(windows), json.dumps(list(holidays)))


def free_extension(conn) -> str:
    while True:
        ext = str(random.randint(10000, 99999))
        if q1(conn, "SELECT 1 FROM user_extensions WHERE extension = %s AND released_at IS NULL", ext) is None:
            return ext


def own_extension(conn, user, ext=None) -> str:
    ext = ext or free_extension(conn)
    run(conn, "UPDATE user_extensions SET released_at = now(), release_reason = 'c2c test reuse' "
              "WHERE (extension = %s OR user_id = %s) AND released_at IS NULL", ext, user)
    run(conn, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'c2c test')", ext, user)
    return ext


def number(last_digit=4) -> str:
    """A unique Saudi mobile in the stored Phase 1 form (9665XXXXXXXX) ending in `last_digit`
    (the test PBX's customer behaviour: 1 answers, 2 busy, 3 no answer, 4 answers + hangs up)."""
    return "9665" + f"{random.randint(0, 9999999):07d}" + str(last_digit)


@pytest.fixture
def transport(monkeypatch):
    """A connected calling service as the API sees it (unique epoch per test)."""
    from services.calling import rules
    ep_uuid = str(uuid.uuid4())
    fake = {"worker_id": f"test-{uuid.uuid4().hex[:8]}", "epoch": f"{ep_uuid}@2026-09-24T05:00:00",
            "epoch_uuid": ep_uuid, "ami_connected": True, "dispatcher": True}
    state = {"up": True}
    monkeypatch.setattr(rules, "transport_status", lambda: dict(fake) if state["up"] else None)
    fake["down"] = lambda: state.update(up=False)
    return fake


@pytest.fixture
def c2c(pgc, transport):
    """One collector with an extension, a callable case (1000 due) with a default mobile,
    a second collector (same team), their supervisor and an outsider. Windows open 24/7."""
    set_windows(pgc)
    cid, pid = new_contract(pgc, "c2c")
    _oc, other_pid = new_contract(pgc, "c2c other")
    c1, c2, outsider = new_user(pgc, name="C2C One"), new_user(pgc, name="C2C Two"), new_user(pgc, name="C2C Out")
    sup = new_supervisor(pgc, users=[c1, c2])
    case = make_case(pgc, c1, pid)
    w = types.SimpleNamespace(
        contract=cid, portfolio=pid, other_portfolio=other_pid, c1=c1, c2=c2, outsider=outsider, supervisor=sup,
        case=case, contact=add_contact(pgc, case, number(4), default=True),
        ext1=own_extension(pgc, c1), ext2=own_extension(pgc, c2), transport=transport)
    return w


def place(client, hdr, user, case, contact, *, k=None):
    r = client.post("/v2/calls", headers=hdr(user, key=k or key()), json={"case_id": case, "contact_id": contact})
    return r.status_code, r.get_json()


def get(client, h, url, **params):
    r = client.get(url, headers=h, query_string=params)
    return r.status_code, r.get_json()


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json()


def rev(conn, attempt):
    return q1(conn, "SELECT revision FROM call_attempts WHERE id = %s", attempt)[0]


def state(conn, attempt):
    return q1(conn, "SELECT state, reason_code FROM call_attempts WHERE id = %s", attempt)


def reservation(conn, attempt):
    return q1(conn, "SELECT released_at IS NULL, release_source FROM call_reservations WHERE attempt_id = %s", attempt)


def queue(conn, attempt):
    return q1(conn, "SELECT state, result_code, expected_epoch FROM call_dispatch_queue WHERE attempt_id = %s", attempt)


class FakeSession:
    """The part of services.calling.ami.AmiSession the dispatcher and reconciler use."""

    def __init__(self, response="Success", error=None, channels=()):
        self.ready = True
        self.sent = []
        self.response, self.error, self.channels = response, error, list(channels)

    def send(self, action, fields, timeout=None):
        from services.calling.ami import Frame
        self.sent.append((action, list(fields)))
        if self.error:
            from services.calling.ami import AmiError
            raise AmiError(self.error)
        return Frame([("Response", self.response), ("ActionID", "x:1")])

    def list_channels(self, max_channels=4096):
        from services.calling.ami import Frame
        return [Frame(list(c.items())) for c in self.channels]


def fake_ctx(epoch=None, epoch_uuid=None, session=None):
    from services.calling.ami import PbxIdentity
    if epoch is None:
        epoch_uuid = epoch_uuid or str(uuid.uuid4())
        ident = PbxIdentity(epoch_uuid, "2026-09-24T05:00:00")
    else:
        u, _, startup = epoch.partition("@")
        ident = PbxIdentity(u if u != "none" else None, startup or None)
    c = types.SimpleNamespace(session=session or FakeSession(), identity=ident)
    c.epoch = ident.epoch
    return c


def connect_run(sql, *params):
    from test_calling_helpers import connect
    conn = connect()
    try:
        return run(conn, sql, *params)
    finally:
        conn.close()


def in_app(app, fn):
    """Run fn(db.session) in an app context and commit (like the calling service)."""
    from extensions import db
    with app.app_context():
        try:
            out = fn(db.session)
            db.session.commit()
            return out
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()
