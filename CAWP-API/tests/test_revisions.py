"""revision columns + cawp_bump_revision trigger + core.revisions helpers, and proof that
the columns are invisible to the legacy ORM serializers."""
import uuid

import pytest
from sqlalchemy import text

from conftest import Ids

TABLES = {  # table -> (trigger, a harmless update)
    "clints": ("clints_bump_revision", "amount = amount + 1"),
    "Comments": ("comments_bump_revision", "comment = comment || '.'"),
    "Payments": ("payments_bump_revision", "amount = amount + 1"),
    "ClintContacts": ("clintcontacts_bump_revision", "contact_status = contact_status || '.'"),
    "paymentDates": ("paymentdates_bump_revision", "amount = amount + 1"),
}
ROW = {"clints": Ids.client2, "Comments": Ids.comment, "Payments": Ids.payment,
       "ClintContacts": Ids.contact, "paymentDates": Ids.promise}


def rev(cur, table, row_id):
    cur.execute(f'SELECT revision FROM "{table}" WHERE id = %s', (row_id,))
    return cur.fetchone()[0]


def test_columns_and_triggers_exist(pg):
    with pg.cursor() as cur:
        for table, (trigger, _upd) in TABLES.items():
            cur.execute("SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                        "WHERE table_name = %s AND column_name = 'revision'", (table,))
            assert cur.fetchone() == ("integer", "NO", "0"), table
            cur.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                        (trigger, f'"{table}"'))
            assert cur.fetchone() == ("O",), trigger


@pytest.mark.parametrize("table", list(TABLES))
def test_trigger_bumps_on_change_only(pg, table):
    _trigger, change = TABLES[table]
    row_id = ROW[table]
    with pg.cursor() as cur:
        r0 = rev(cur, table, row_id)
        cur.execute(f'UPDATE "{table}" SET {change} WHERE id = %s', (row_id,))
        assert rev(cur, table, row_id) == r0 + 1
        cur.execute(f'UPDATE "{table}" SET id = id WHERE id = %s', (row_id,))          # no-op update
        assert rev(cur, table, row_id) == r0 + 1
        cur.execute(f'UPDATE "{table}" SET revision = 999 WHERE id = %s', (row_id,))   # client cannot set it
        assert rev(cur, table, row_id) == r0 + 1


def test_new_note_does_not_bump_case_revision(pg):
    """last_comment_at/id are rewritten by comments_last_comment_trg; they are excluded."""
    with pg.cursor() as cur:
        before = rev(cur, "clints", Ids.client3)
        cur.execute('INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id) '
                    "VALUES (%s, %s, %s, 'note', %s) RETURNING id, revision",
                    (Ids.client3, Ids.collector, Ids.contact_method, Ids.comment_status))
        comment_id, comment_rev = cur.fetchone()
        assert comment_rev == 0
        cur.execute("SELECT last_comment_id::text FROM clints WHERE id = %s", (Ids.client3,))
        assert cur.fetchone()[0] == str(comment_id)                  # denormalisation still works
        assert rev(cur, "clints", Ids.client3) == before             # ... without bumping the case
        cur.execute("UPDATE clints SET is_locked = NOT is_locked WHERE id = %s", (Ids.client3,))
        assert rev(cur, "clints", Ids.client3) == before + 1


def test_orm_models_do_not_map_revision(app):
    """The legacy serializers iterate mapped columns only; revision must never be mapped."""
    import models
    from extensions import db
    for model in (models.Clints, models.Comments, models.Payments, models.ClintContacts, models.PaymentDates):
        assert "revision" not in model.__table__.c, model.__name__
    with app.app_context():
        case = db.session.get(models.Clints, uuid.UUID(Ids.client1))
        d = case.as_dict()
        assert "revision" not in d
        assert list(d) == [c.name for c in models.Clints.__table__.columns]
        pay = db.session.get(models.Payments, uuid.UUID(Ids.payment)).as_dict()
        assert "revision" not in pay


def test_check_revision_and_guarded_update(app):
    from core.revisions import (RevisionConflict, RevisionNotFound, check_revision, current_revision,
                                guarded_update)
    from extensions import db
    with app.app_context():
        cur = current_revision("case", Ids.client1)
        assert check_revision("case", Ids.client1, cur) == cur
        with pytest.raises(RevisionConflict) as e:
            check_revision("case", Ids.client1, cur + 5)
        assert e.value.as_error() == {"entity": "case", "id": Ids.client1,
                                      "expected_revision": cur + 5, "current_revision": cur}
        with pytest.raises(RevisionNotFound):
            check_revision("case", str(uuid.uuid4()), 0)
        db.session.rollback()

        new = guarded_update("case", Ids.client1, cur, {"amount": 1234.5})
        assert new == cur + 1
        with pytest.raises(RevisionConflict):
            guarded_update("case", Ids.client1, cur, {"amount": 1.0})        # stale expected revision
        db.session.commit()
        assert current_revision("case", Ids.client1) == cur + 1
        with pytest.raises(ValueError):
            guarded_update("case", Ids.client1, cur + 1, {"amount; DROP TABLE x": 1})
        with pytest.raises(ValueError):
            current_revision("users", Ids.admin)


@pytest.mark.no_db
def test_parse_revision():
    from core.revisions import parse_revision
    assert parse_revision(3) == 3
    assert parse_revision("4") == 4
    assert parse_revision('W/"5"') == 5
    assert parse_revision('"6"') == 6
    for bad in (-1, True, None, "abc", "1.5"):
        with pytest.raises(ValueError):
            parse_revision(bad)


def test_revision_conflict_maps_to_409(app, client, auth_headers):
    from core.revisions import current_revision
    with app.app_context():
        cur = current_revision("case", Ids.client1)
    ok = client.put(f"/t/cases/{Ids.client1}", json={"expected_revision": cur}, headers=auth_headers())
    assert ok.status_code == 200 and ok.get_json() == {"message": "ok", "status": True, "data": {"revision": cur}}
    stale = client.put(f"/t/cases/{Ids.client1}", json={"expected_revision": cur - 1}, headers=auth_headers())
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["status"] is False and body["code"] == "revision_conflict"
    assert body["error"]["current_revision"] == cur
    via_header = client.put(f"/t/cases/{Ids.client1}", headers=auth_headers(**{"If-Match": f'W/"{cur}"'}))
    assert via_header.status_code == 200
    missing = client.put(f"/t/cases/{Ids.client1}", json={}, headers=auth_headers())
    assert missing.status_code == 422 and missing.get_json()["code"] == "validation_failed"
    gone = client.put(f"/t/cases/{uuid.uuid4()}", json={"expected_revision": 0}, headers=auth_headers())
    assert gone.status_code == 404 and gone.get_json()["code"] == "not_found"


def test_legacy_style_route_is_untouched(client):
    r = client.get("/t/legacy-style")
    assert r.status_code == 201 and r.get_json() == {"status": False, "message": "legacy"}
