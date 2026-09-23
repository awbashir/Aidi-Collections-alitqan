"""audit_events is append-only; core.audit.record writes in the caller's transaction."""
import uuid

import psycopg2
import pytest
from sqlalchemy import text

from conftest import Ids


def test_record_in_callers_transaction_commits_and_rolls_back(app):
    from core import audit
    from extensions import db

    marker = f"test.audit.{uuid.uuid4().hex[:8]}"
    with app.app_context():
        ev = audit.record(marker, "case", Ids.client1, case_id=Ids.client1,
                          before={"amount": 1000.0}, after={"amount": 900.0, "when": uuid.UUID(Ids.client2)},
                          reason="correction", actor_user_id=Ids.admin, source="pytest")
        assert isinstance(ev["id"], int)
        db.session.rollback()                      # caller aborts -> event gone
        n = db.session.execute(text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": marker}).scalar()
        assert n == 0

        audit.record(marker, "case", Ids.client1, case_id=Ids.client1, before={"amount": 1000.0},
                     after={"amount": 900.0}, reason="correction", actor_user_id=Ids.admin,
                     request_id="req-1", idempotency_key="key-1", source="pytest")
        db.session.commit()
        row = db.session.execute(text(
            "SELECT actor_user_id::text, entity_type, entity_id, case_id::text, before, after, reason, "
            "request_id, idempotency_key, source, occurred_at FROM audit_events WHERE action = :a"),
            {"a": marker}).one()
    assert row[:10] == (Ids.admin, "case", Ids.client1, Ids.client1, {"amount": 1000.0}, {"amount": 900.0},
                        "correction", "req-1", "key-1", "pytest")
    assert row[10].tzinfo is not None


def test_record_defaults_from_request(app, auth_headers):
    from core import audit
    from extensions import db
    from flask_jwt_extended import verify_jwt_in_request

    headers = auth_headers(Ids.collector, **{"X-Request-ID": "rid-42", "Idempotency-Key": "k-42"})
    with app.test_request_context("/x", method="POST", headers=headers):
        verify_jwt_in_request()
        ev = audit.record("test.defaults", "case", Ids.client2)
        row = db.session.execute(text("SELECT actor_user_id::text, request_id, idempotency_key, source "
                                      "FROM audit_events WHERE id = :i"), {"i": ev["id"]}).one()
        db.session.rollback()
    assert tuple(row) == (Ids.collector, "rid-42", "k-42", "api")


def test_record_validates_input(app):
    from core import audit
    with app.app_context():
        with pytest.raises(ValueError):
            audit.record("", "case")
        with pytest.raises(ValueError):
            audit.record("x", "case", case_id="not-a-uuid")


@pytest.mark.parametrize("stmt", [
    "UPDATE audit_events SET reason = 'tampered'",
    "DELETE FROM audit_events",
    "TRUNCATE audit_events",
])
def test_audit_events_are_append_only(pg, stmt):
    with pg.cursor() as cur:
        cur.execute("INSERT INTO audit_events (action, entity_type, source) VALUES ('test.append', 'x', 'pytest')")
        with pytest.raises(psycopg2.Error, match="append-only"):
            cur.execute(stmt)
        cur.execute("SELECT count(*) FROM audit_events WHERE action = 'test.append'")
        assert cur.fetchone()[0] >= 1


def test_audit_indexes_exist(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_events'")
        names = {r[0] for r in cur.fetchall()}
    assert {"ix_audit_events_case", "ix_audit_events_entity", "ix_audit_events_actor",
            "ix_audit_events_occurred"} <= names


@pytest.mark.no_db
def test_changed_fields():
    from core.audit import changed_fields
    assert changed_fields({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == ({"b": 2, "c": None}, {"b": 3, "c": 4})
