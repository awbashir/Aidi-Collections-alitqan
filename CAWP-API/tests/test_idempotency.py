"""core.idempotency: replay, conflict, in-progress, release on failure, expiry."""
import uuid

from sqlalchemy import text

import routes_for_tests
from conftest import Ids

URL = f"/t/cases/{Ids.client1}/notes"


def audit_count(app, text_value):
    from extensions import db
    with app.app_context():
        return db.session.execute(text("SELECT count(*) FROM audit_events WHERE action = 'test.note' "
                                       "AND after->>'text' = :t"), {"t": text_value}).scalar()


def key_row(app, key, user=Ids.collector):
    from extensions import db
    with app.app_context():
        return db.session.execute(text("SELECT state, response_status, request_hash, method, route "
                                       "FROM idempotency_keys WHERE user_id = :u AND key = :k"),
                                  {"u": user, "k": key}).first()


def test_missing_and_invalid_key(client, auth_headers):
    r = client.post(URL, json={"text": "x"}, headers=auth_headers())
    assert r.status_code == 400
    assert r.get_json() == {"message": "the Idempotency-Key header is required", "status": False,
                            "error": None, "code": "idempotency_key_required"}
    r = client.post(URL, json={"text": "x"}, headers=auth_headers(**{"Idempotency-Key": "has space"}))
    assert r.status_code == 400 and r.get_json()["code"] == "idempotency_key_invalid"


def test_same_key_same_body_is_replayed(app, client, auth_headers):
    key, body = str(uuid.uuid4()), {"text": f"note-{uuid.uuid4().hex[:6]}"}
    h = auth_headers(**{"Idempotency-Key": key})
    first = client.post(URL, json=body, headers=h)
    assert first.status_code == 201, first.get_json()
    calls = routes_for_tests.CALLS["notes"]
    # same JSON, different key order / whitespace -> same canonical hash
    second = client.post(URL, data='{ "text" : "%s" }' % body["text"], content_type="application/json", headers=h)
    assert second.status_code == 201
    assert second.data == first.data
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.headers.get("Idempotent-Replayed") is None
    assert routes_for_tests.CALLS["notes"] == calls        # handler did not run again
    assert audit_count(app, body["text"]) == 1               # the write happened once
    state, status, *_ = key_row(app, key)
    assert (state, status) == ("completed", 201)


def test_same_key_different_body_is_rejected(client, auth_headers):
    h = auth_headers(**{"Idempotency-Key": str(uuid.uuid4())})
    assert client.post(URL, json={"text": "a"}, headers=h).status_code == 201
    r = client.post(URL, json={"text": "b"}, headers=h)
    assert r.status_code == 422 and r.get_json()["code"] == "idempotency_key_reused"
    # same key on another case (another path) is also a different request
    r = client.post(f"/t/cases/{Ids.client2}/notes", json={"text": "a"}, headers=h)
    assert r.status_code == 422


def test_keys_are_per_user(client, auth_headers):
    key = str(uuid.uuid4())
    assert client.post(URL, json={"text": "u1"}, headers=auth_headers(Ids.collector, **{"Idempotency-Key": key})).status_code == 201
    r = client.post(URL, json={"text": "u2"}, headers=auth_headers(Ids.admin, **{"Idempotency-Key": key}))
    assert r.status_code == 201 and r.headers.get("Idempotent-Replayed") is None


def test_in_progress_returns_409(app, client, auth_headers):
    from core.idempotency import request_fingerprint
    from extensions import db
    key, body = str(uuid.uuid4()), {"text": "slow"}
    with app.test_request_context(URL, method="POST", json=body):
        digest = request_fingerprint()
    with app.app_context():
        db.session.execute(text(
            "INSERT INTO idempotency_keys (user_id, key, method, route, request_hash, expires_at) "
            "VALUES (:u, :k, 'POST', '/t/cases/<uuid:case_id>/notes', :h, now() + interval '1 hour')"),
            {"u": Ids.collector, "k": key, "h": digest})
        db.session.commit()
    r = client.post(URL, json=body, headers=auth_headers(**{"Idempotency-Key": key}))
    assert r.status_code == 409 and r.get_json()["code"] == "idempotency_request_in_progress"


def test_stale_in_progress_reservation_is_reclaimed(app, client, auth_headers):
    from extensions import db
    key = str(uuid.uuid4())
    with app.app_context():
        db.session.execute(text(
            "INSERT INTO idempotency_keys (user_id, key, method, route, request_hash, expires_at, created_at) "
            "VALUES (:u, :k, 'POST', 'x', 'crashed-worker', now() + interval '1 hour', now() - interval '1 day')"),
            {"u": Ids.collector, "k": key})
        db.session.commit()
    r = client.post(URL, json={"text": "after crash"}, headers=auth_headers(**{"Idempotency-Key": key}))
    assert r.status_code == 201


def test_error_response_releases_the_key(app, client, auth_headers):
    key = str(uuid.uuid4())
    h = auth_headers(**{"Idempotency-Key": key})
    r = client.post(URL, json={"text": ""}, headers=h)             # 422 from the handler
    assert r.status_code == 422 and r.get_json()["code"] == "validation_failed"
    assert key_row(app, key) is None
    fixed = client.post(URL, json={"text": "fixed"}, headers=h)    # same key, corrected body
    assert fixed.status_code == 201


def test_exception_rolls_back_and_releases(app, client, auth_headers):
    import pytest
    from extensions import db
    key = str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        client.post("/t/boom", json={}, headers=auth_headers(**{"Idempotency-Key": key}))
    assert key_row(app, key) is None
    with app.app_context():
        n = db.session.execute(text("SELECT count(*) FROM audit_events WHERE action = 'test.boom'")).scalar()
    assert n == 0


def test_expired_key_can_be_reused_and_is_purged(app, client, auth_headers):
    from core.idempotency import purge_expired
    from extensions import db
    key = str(uuid.uuid4())
    h = auth_headers(**{"Idempotency-Key": key})
    assert client.post(URL, json={"text": "old"}, headers=h).status_code == 201
    with app.app_context():
        db.session.execute(text("UPDATE idempotency_keys SET expires_at = now() - interval '1 second' "
                                "WHERE key = :k"), {"k": key})
        db.session.commit()
    # a different body with the expired key is a new request, not a reuse error
    r = client.post(URL, json={"text": "new"}, headers=h)
    assert r.status_code == 201 and r.headers.get("Idempotent-Replayed") is None

    other = str(uuid.uuid4())
    assert client.post(URL, json={"text": "p"}, headers=auth_headers(**{"Idempotency-Key": other})).status_code == 201
    with app.app_context():
        db.session.execute(text("UPDATE idempotency_keys SET expires_at = now() - interval '1 second' "
                                "WHERE key = :k"), {"k": other})
        db.session.commit()
        assert purge_expired() >= 1
    assert key_row(app, other) is None


def test_optional_key_passes_through(client, auth_headers):
    r = client.post("/t/optional", json={"a": 1}, headers=auth_headers())
    assert r.status_code == 200 and r.get_json()["data"] == {"echo": {"a": 1}}


def test_fingerprint_covers_query_and_form_files(app):
    import io
    from core.idempotency import request_fingerprint
    with app.test_request_context("/x?a=1", method="POST", json={"b": 2}):
        h1 = request_fingerprint()
    with app.test_request_context("/x?a=2", method="POST", json={"b": 2}):
        h2 = request_fingerprint()
    assert h1 != h2

    def upload(content):
        with app.test_request_context("/u", method="POST",
                                      data={"f": (io.BytesIO(content), "a.xlsx"), "sheet": "1"},
                                      content_type="multipart/form-data"):
            from flask import request
            h = request_fingerprint()
            assert request.files["f"].read() == content    # stream rewound for the handler
            return h
    assert upload(b"same") == upload(b"same")
    assert upload(b"same") != upload(b"different")
