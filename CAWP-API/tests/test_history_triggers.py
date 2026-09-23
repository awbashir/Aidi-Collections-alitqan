"""Stream C migrations 0013-0015: schema, and the audit triggers that capture legacy writes
on "Comments" (UPDATE / hard DELETE) and "ClintContacts" (INSERT / UPDATE / DELETE).

The legacy routes are simulated with the exact statements they issue (plain SQL, no v2
audit context), so the rows must be recorded with actor NULL and source 'db_trigger'.
"""
import psycopg2
import pytest

from test_history_support import *  # noqa: F401,F403


def test_schema_objects(hpg):
    assert q1(hpg, "SELECT to_regclass('note_revisions') IS NOT NULL, to_regclass('case_corrections') IS NOT NULL") \
        == (True, True)
    triggers = {r[0] for r in q(hpg, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN "
                                     "('\"Comments\"'::regclass, '\"ClintContacts\"'::regclass)")}
    assert {"comments_hist_audit_upd", "comments_hist_audit_del", "clintcontacts_hist_audit_ins",
            "clintcontacts_hist_audit_upd", "clintcontacts_hist_audit_del"} <= triggers
    cols = {r[0]: r[1:] for r in q(hpg, "SELECT column_name, data_type, is_nullable, column_default "
                                        "FROM information_schema.columns WHERE table_name = 'ClintContacts' "
                                        "AND column_name IN ('is_blocked','blocked_at','blocked_by','block_reason')")}
    assert cols["is_blocked"] == ("boolean", "NO", "false")
    assert cols["blocked_at"][0] == "timestamp with time zone" and cols["blocked_by"][0] == "uuid"
    assert q1(hpg, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_audit_events_contact_identity'") == (1,)


def test_stream_c_migrations_are_transactional_and_rerunnable(migrated_scratch_db):
    """0013-0015 run in one transaction each and can be re-run by hand (IF NOT EXISTS /
    OR REPLACE / DROP TRIGGER IF EXISTS), on top of baseline + every other migration."""
    import manage_db
    from conftest import DB_DIR
    _name, url = migrated_scratch_db
    migs = {m.version: m for m in manage_db.discover(DB_DIR)}
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations WHERE version IN ('0013','0014','0015') ORDER BY 1")
            assert [r[0] for r in cur.fetchall()] == ["0013", "0014", "0015"]
        for v in ("0013", "0014", "0015"):
            assert migs[v].transactional
            with conn, conn.cursor() as cur:
                cur.execute(migs[v].sql)
    finally:
        conn.close()


def test_new_contact_columns_are_not_mapped(hist_app, hpg):
    """The block columns must never reach a legacy response (as_dict iterates mapped columns)."""
    from models import ClintContacts, Comments
    assert not {"is_blocked", "blocked_at", "blocked_by", "block_reason"} & set(ClintContacts.__table__.columns.keys())
    cid = add_contact_row(hpg, "1999000001", "966511111111")
    with hist_app.app_context():
        from extensions import db
        row = db.session.get(ClintContacts, cid)
        assert "is_blocked" not in row.as_dict() and "revision" not in row.as_dict()
        assert "revision" not in Comments.__table__.columns.keys()


# ------------------------------------------------------------------------ notes --

def test_legacy_note_update_is_audited_with_before_and_after(hpg):
    note = make_note(hpg, text="legacy text")
    q(hpg, 'UPDATE "Comments" SET comment = %s WHERE id = %s', "legacy edited", note)
    rows = audit_rows(hpg, "comment", note)
    assert len(rows) == 1
    action, actor, source, reason, before, after = rows[0]
    assert (action, actor, source, reason) == ("comment.update", None, "db_trigger", None)
    assert before["comment"] == "legacy text" and after["comment"] == "legacy edited"
    assert before["id"] == after["id"] == note
    assert q1(hpg, "SELECT case_id::text FROM audit_events WHERE entity_type='comment' AND entity_id=%s", note)[0] == H.case1


def test_noop_note_update_is_not_audited(hpg):
    note = make_note(hpg)
    q(hpg, 'UPDATE "Comments" SET comment = comment WHERE id = %s', note)
    assert audit_rows(hpg, "comment", note) == []


def test_note_insert_is_not_audited_by_stream_c(hpg):
    note = make_note(hpg)
    assert audit_rows(hpg, "comment", note) == []


def test_legacy_hard_delete_leaves_a_record(hpg):
    """DELETE /comments/<id> (db.session.delete) -> the whole row survives in audit_events."""
    note = make_note(hpg, text="to be deleted")
    q(hpg, 'DELETE FROM "Comments" WHERE id = %s', note)
    rows = audit_rows(hpg, "comment", note)
    assert [r[0] for r in rows] == ["comment.delete"]
    assert rows[0][4]["comment"] == "to be deleted" and rows[0][5] is None
    assert rows[0][4]["user_id"] == H.col1


def test_case_hard_delete_cascade_records_every_note(hpg):
    """DELETE /clints cascades to "Comments": every cascaded note is recorded."""
    case_id, _ = make_case(hpg)
    notes = [make_note(hpg, case_id=case_id, text=f"n{i}") for i in range(3)]
    q(hpg, "DELETE FROM clints WHERE id = %s", case_id)
    got = q(hpg, "SELECT entity_id FROM audit_events WHERE entity_type='comment' AND action='comment.delete' "
                 "AND case_id = %s", case_id)
    assert sorted(r[0] for r in got) == sorted(notes)


def test_bulk_update_audits_only_changed_rows(hpg):
    case_id, _ = make_case(hpg)
    a, b = make_note(hpg, case_id=case_id, text="x"), make_note(hpg, case_id=case_id, text="y")
    q(hpg, 'UPDATE "Comments" SET comment = %s WHERE clint_id = %s', "x", case_id)   # only b changes
    assert audit_rows(hpg, "comment", a) == []
    assert len(audit_rows(hpg, "comment", b)) == 1


def test_trigger_uses_the_v2_context_when_set(hpg):
    note = make_note(hpg)
    hpg.autocommit = False
    try:
        with hpg.cursor() as cur:
            cur.execute("SELECT set_config('cawp_hist.actor', %s, true), set_config('cawp_hist.action', 'note.test', true),"
                        " set_config('cawp_hist.reason', 'because', true), set_config('cawp_hist.source', 'api', true)",
                        (H.reviewer,))
            cur.execute('UPDATE "Comments" SET comment = %s WHERE id = %s', ("ctx", note))
        hpg.commit()
    finally:
        hpg.autocommit = True
    (action, actor, source, reason, _b, _a), = audit_rows(hpg, "comment", note)
    assert (action, actor, source, reason) == ("note.test", H.reviewer, "api", "because")
    # the context is transaction-local: a later legacy write is anonymous again
    q(hpg, 'UPDATE "Comments" SET comment = %s WHERE id = %s', "after", note)
    assert audit_rows(hpg, "comment", note)[-1][:4] == ("comment.update", None, "db_trigger", None)


def test_note_revisions_is_append_only(hpg):
    q(hpg, "INSERT INTO note_revisions (comment_id, case_id, revision_no, kind, text) "
           "VALUES (gen_random_uuid(), %s, 0, 'original', 't')", H.case1)
    with pytest.raises(psycopg2.Error):
        q(hpg, "UPDATE note_revisions SET text = 'x'")
    with pytest.raises(psycopg2.Error):
        q(hpg, "DELETE FROM note_revisions")
    with pytest.raises(psycopg2.Error):          # a correction needs reason + corrector
        q(hpg, "INSERT INTO note_revisions (comment_id, case_id, revision_no, kind, text) "
               "VALUES (gen_random_uuid(), %s, 1, 'correction', 't')", H.case1)


# --------------------------------------------------------------------- contacts --

def test_contact_insert_update_delete_are_audited(hpg):
    idn = "1999000002"
    cid = add_contact_row(hpg, idn, "966522222222")
    q(hpg, 'UPDATE "ClintContacts" SET is_default = true WHERE id = %s', cid)
    q(hpg, 'DELETE FROM "ClintContacts" WHERE id = %s', cid)
    rows = audit_rows(hpg, "contact", cid)
    assert [r[0] for r in rows] == ["contact.insert", "contact.update", "contact.delete"]
    assert all(r[1] is None and r[2] == "db_trigger" for r in rows)
    ins, upd, dele = rows
    assert ins[4] is None and ins[5]["contact"] == "966522222222" and ins[5]["identityNumber"] == idn
    assert upd[4]["is_default"] is False and upd[5]["is_default"] is True
    assert dele[4]["id"] == cid and dele[5] is None
    # findable by debtor through the expression index
    n = q1(hpg, "SELECT count(*) FROM audit_events WHERE entity_type = 'contact' AND "
                "coalesce(after ->> 'identityNumber', before ->> 'identityNumber') = %s", idn)[0]
    assert n == 3


def test_legacy_set_default_audits_only_flipped_rows(hpg):
    """/clint_contact/default/<id>: two UPDATEs over every row of the debtor."""
    idn = "1999000003"
    a1 = add_contact_row(hpg, idn, "966533333331", default=True)
    a2 = add_contact_row(hpg, idn, "966533333331", default=True)
    b1 = add_contact_row(hpg, idn, "966533333332")
    q(hpg, 'UPDATE "ClintContacts" SET is_default = false WHERE "identityNumber" = %s', idn)
    q(hpg, 'UPDATE "ClintContacts" SET is_default = true WHERE "identityNumber" = %s AND contact = %s',
      idn, "966533333332")
    assert [r[0] for r in audit_rows(hpg, "contact", a1)] == ["contact.insert", "contact.update"]
    assert [r[0] for r in audit_rows(hpg, "contact", a2)] == ["contact.insert", "contact.update"]
    assert [r[0] for r in audit_rows(hpg, "contact", b1)] == ["contact.insert", "contact.update"]


def test_multi_row_insert_audits_every_row(hpg):
    idn = "1999000004"
    q(hpg, 'INSERT INTO "ClintContacts" (contact_type_id, "identityNumber", contact_status, contact) '
           "SELECT %s, %s, 'new', '9665' || lpad(g::text, 8, '0') FROM generate_series(1, 25) g", H.t_mobile, idn)
    assert q1(hpg, "SELECT count(*) FROM audit_events WHERE entity_type='contact' AND action='contact.insert' "
                   "AND after->>'identityNumber' = %s", idn)[0] == 25


def test_block_check_constraint(hpg):
    cid = add_contact_row(hpg, "1999000005", "966544444444")
    with pytest.raises(psycopg2.Error):
        q(hpg, 'UPDATE "ClintContacts" SET is_blocked = true WHERE id = %s', cid)


# ------------------------------------------------------------------ corrections --

def test_case_corrections_constraints(hpg):
    base = ("INSERT INTO case_corrections (case_id, kind, changes, reason, requested_by, status, reviewed_by, "
            "reviewed_at, applied_revision, review_note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
    ok = (H.case1, "profile", '{"name": {"before": "a", "after": "b"}}', "r", H.col1)
    q(hpg, base, *ok, "pending", None, None, None, None)
    bad = [
        (*ok[:2], "{}", "r", H.col1, "pending", None, None, None, None),                       # empty changes
        (H.case1, "nope", ok[2], "r", H.col1, "pending", None, None, None, None),              # kind
        (*ok[:3], " ", H.col1, "pending", None, None, None, None),                             # reason
        (*ok, "approved", H.col1, "2026-09-01", 1, None),                                      # self approval
        (*ok, "approved", H.reviewer, "2026-09-01", None, None),                               # no applied_revision
        (*ok, "rejected", H.reviewer, "2026-09-01", None, None),                               # reject w/o note
        (*ok, "cancelled", H.reviewer, "2026-09-01", None, None),                              # cancel by other
        (*ok, "pending", H.reviewer, "2026-09-01", None, None),                                # pending w/ reviewer
    ]
    for params in bad:
        with pytest.raises(psycopg2.Error):
            q(hpg, base, *params)
