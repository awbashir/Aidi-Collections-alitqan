"""manage_db.py: fresh apply, idempotent re-run, checksum mismatch, baseline detection,
no-transaction files, rollback on failure, dry-run, locking and the SQL splitter."""
import shutil
from pathlib import Path

import psycopg2
import pytest

import manage_db
from conftest import DB_DIR, quiet

NEW_VERSIONS = sorted(p.name[:4] for p in (DB_DIR / "migrations").glob("*.sql"))


def rows(url, sql, params=None):
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def copy_db_dir(tmp_path: Path, with_migrations: bool = True) -> Path:
    d = tmp_path / "db"
    (d / "migrations").mkdir(parents=True)
    for _v, fname in manage_db.LEGACY:
        shutil.copy(DB_DIR / fname, d / fname)
    if with_migrations:
        for p in (DB_DIR / "migrations").glob("*.sql"):
            shutil.copy(p, d / "migrations" / p.name)
    return d


# --------------------------------------------------------------------------- real files

def test_migration_files_are_well_formed():
    migs = manage_db.discover(DB_DIR)
    assert [m.version for m in migs][:3] == ["0001", "0002", "0003"]
    assert all(m.legacy for m in migs[:3])
    assert NEW_VERSIONS and NEW_VERSIONS[0] == "0004"
    for m in migs[3:]:
        stmts = manage_db.split_sql(m.sql)  # parses: no meta-commands, balanced quoting
        assert stmts, m.name
        assert m.transactional, f"{m.name}: foundation migrations are transactional"


def test_fresh_apply_then_idempotent_rerun(scratch_db):
    _name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    assert r.baseline() == ["0001", "0002", "0003"]
    assert r.up() == NEW_VERSIONS
    recorded = rows(url, "SELECT version, execution, checksum, duration_ms FROM schema_migrations ORDER BY version")
    assert [v for v, *_ in recorded] == ["0001", "0002", "0003"] + NEW_VERSIONS
    assert {v: e for v, e, *_ in recorded}["0001"] == "baselined"
    assert all(e == "applied" for v, e, *_ in recorded if v in NEW_VERSIONS)
    for v, _e, checksum, ms in recorded:
        m = next(m for m in manage_db.discover(DB_DIR) if m.version == v)
        assert checksum == m.checksum and ms >= 0
    # objects exist
    assert rows(url, "SELECT to_regclass('audit_events') IS NOT NULL, to_regclass('idempotency_keys') IS NOT NULL") == [(True, True)]
    # second run: nothing to do, nothing re-recorded
    assert r.up() == []
    assert r.baseline() == []
    p = r.plan()
    assert not p["pending"] and not p["mismatched"] and not p["unknown"]
    assert rows(url, "SELECT count(*) FROM schema_migrations") == [(3 + len(NEW_VERSIONS),)]


def test_migration_sql_is_rerunnable_by_hand(migrated_scratch_db):
    """Each file is idempotent (IF NOT EXISTS / OR REPLACE), so a manual re-run is harmless."""
    _name, url = migrated_scratch_db
    conn = psycopg2.connect(url)
    try:
        for v in NEW_VERSIONS:
            m = next(m for m in manage_db.discover(DB_DIR) if m.version == v)
            with conn, conn.cursor() as cur:
                cur.execute(m.sql)
    finally:
        conn.close()


def test_checksum_mismatch_refuses(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path)
    r = manage_db.Runner(url, d, log=quiet)
    r.baseline()
    r.up()
    target = sorted((d / "migrations").glob("*.sql"))[0]
    target.write_text(target.read_text() + "\n-- edited after it was applied\n")
    p = r.plan()
    assert [m.version for m in p["mismatched"]] == [target.name[:4]]
    with pytest.raises(manage_db.ChecksumMismatch):
        r.verify()
    with pytest.raises(manage_db.ChecksumMismatch):
        r.up()
    assert manage_db.main(["--database-url", url, "--dir", str(d), "status"]) == 1
    assert manage_db.main(["--database-url", url, "--dir", str(d), "up"]) == 1


def test_checksum_ignores_crlf(tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    b.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    assert manage_db.file_checksum(a) == manage_db.file_checksum(b)


def test_applied_file_missing_is_reported(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path)
    r = manage_db.Runner(url, d, log=quiet)
    r.baseline()
    r.up()
    last = sorted((d / "migrations").glob("*.sql"))[-1]
    last.unlink()
    assert r.plan()["unknown"] == [last.name[:4]]
    with pytest.raises(manage_db.ChecksumMismatch):
        r.up()


def test_baseline_detects_missing_legacy_objects(scratch_db):
    _name, url = scratch_db
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE clints ALTER COLUMN attribute TYPE json USING attribute::json")  # undo 02
        cur.execute("DROP INDEX ix_clintcontacts_identity")                                       # part of 01
    conn.close()
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    assert r.baseline(dry_run=True) == ["0003"]
    assert rows(url, "SELECT to_regclass('schema_migrations')") == [(None,)]  # dry-run wrote nothing
    assert r.baseline() == ["0003"]
    assert rows(url, "SELECT version FROM schema_migrations") == [("0003",)]
    with pytest.raises(manage_db.MigrationError, match="legacy scripts not recorded"):
        r.up()


def test_baseline_detects_missing_trigger(scratch_db):
    _name, url = scratch_db
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute('DROP TRIGGER comments_last_comment_trg ON "Comments"')
    conn.close()
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    assert r.baseline() == ["0001", "0002"]


def test_baseline_ignores_invalid_index(scratch_db):
    """An interrupted CREATE INDEX CONCURRENTLY leaves an INVALID index: not 'present'."""
    _name, url = scratch_db
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'ix_clints_last_comment_id'::regclass")
    conn.close()
    assert manage_db.Runner(url, DB_DIR, log=quiet).baseline() == ["0001", "0002"]


def test_dry_run_changes_nothing(scratch_db):
    _name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    r.baseline()
    assert r.up(dry_run=True) == NEW_VERSIONS
    assert rows(url, "SELECT count(*) FROM schema_migrations") == [(3,)]
    assert rows(url, "SELECT to_regclass('audit_events')") == [(None,)]


def test_target_applies_a_prefix(scratch_db):
    _name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    r.baseline()
    assert r.up(target=NEW_VERSIONS[0]) == NEW_VERSIONS[:1]
    assert [m.version for m in r.plan()["pending"]] == NEW_VERSIONS[1:]


def test_no_transaction_file_runs_concurrently(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path, with_migrations=False)
    (d / "migrations" / "0004_concurrent_index.sql").write_text(
        "-- migrate:no-transaction\n"
        "-- comment; with a semicolon\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_payments_amount ON \"Payments\" (amount);\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_clints_amount ON clints (amount);\n")
    r = manage_db.Runner(url, d, log=quiet)
    r.baseline()
    assert r.up() == ["0004"]
    valid = rows(url, "SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                      "WHERE c.relname LIKE 'ix_test_%' ORDER BY 1")
    assert valid == [("ix_test_clints_amount", True), ("ix_test_payments_amount", True)]


def test_failed_transactional_migration_rolls_back(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path, with_migrations=False)
    (d / "migrations" / "0004_broken.sql").write_text(
        "CREATE TABLE phase0_half_done (id int);\nSELECT * FROM table_that_does_not_exist;\n")
    r = manage_db.Runner(url, d, log=quiet)
    r.baseline()
    with pytest.raises(manage_db.MigrationError, match="rolled back"):
        r.up()
    assert rows(url, "SELECT to_regclass('phase0_half_done')") == [(None,)]
    assert rows(url, "SELECT count(*) FROM schema_migrations WHERE version = '0004'") == [(0,)]


def test_transaction_control_in_file_is_rejected(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path, with_migrations=False)
    (d / "migrations" / "0004_own_tx.sql").write_text("BEGIN;\nCREATE TABLE phase0_x (id int);\nCOMMIT;\n")
    r = manage_db.Runner(url, d, log=quiet)
    r.baseline()
    with pytest.raises(manage_db.MigrationError, match="runner owns"):
        r.up()


def test_lock_timeout_makes_a_blocked_migration_fail_fast(scratch_db, tmp_path):
    _name, url = scratch_db
    d = copy_db_dir(tmp_path, with_migrations=False)
    (d / "migrations" / "0004_alter.sql").write_text("ALTER TABLE clints ADD COLUMN phase0_tmp int;\n")
    r = manage_db.Runner(url, d, lock_timeout="200ms", log=quiet)
    r.baseline()
    blocker = psycopg2.connect(url)
    try:
        with blocker.cursor() as cur:
            cur.execute("SELECT id FROM clints LIMIT 1")  # holds ACCESS SHARE until rollback
            with pytest.raises(manage_db.MigrationError, match="lock timeout"):
                r.up()
    finally:
        blocker.rollback()
        blocker.close()
    assert r.up() == ["0004"]


def test_concurrent_runner_is_refused(scratch_db):
    _name, url = scratch_db
    holder = psycopg2.connect(url)
    holder.autocommit = True
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (manage_db.ADVISORY_LOCK_KEY,))
        with pytest.raises(manage_db.MigrationError, match="advisory lock"):
            manage_db.Runner(url, DB_DIR, log=quiet).baseline()
    finally:
        holder.close()


def test_bad_file_name_is_rejected(tmp_path):
    d = copy_db_dir(tmp_path, with_migrations=False)
    (d / "migrations" / "4_Bad Name.sql").write_text("SELECT 1;")
    with pytest.raises(manage_db.MigrationError, match="NNNN_lower_snake"):
        manage_db.discover(d)


def test_load_schema_refuses_a_database_with_tables(databases):
    from conftest import MAIN_DB, db_url
    with pytest.raises(manage_db.MigrationError, match="already has tables"):
        manage_db.Runner(db_url(MAIN_DB), DB_DIR, log=quiet).load_schema(DB_DIR / "schema" / "baseline.sql")


def test_libpq_dsn():
    assert manage_db.libpq_dsn("postgresql+psycopg2://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    assert manage_db.libpq_dsn("postgres://u@h/d") == "postgresql://u@h/d"


# --------------------------------------------------------------------------- splitter

@pytest.mark.no_db
def test_split_sql_handles_quotes_comments_and_dollar_bodies():
    sql = """
    -- leading comment; with semicolon
    CREATE TABLE a (t text DEFAULT 'x;y', "we;ird" int);
    /* block; /* nested; */ still comment */
    INSERT INTO a VALUES ('it''s; fine', 1);
    SELECT E'back\\'slash;';
    CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $body$
    BEGIN
      RETURN 1; -- inner ;
    END $body$;
    DO $$ BEGIN PERFORM 1; END $$;
    SELECT $1::int
    """
    stmts = manage_db.split_sql(sql)
    assert len(stmts) == 6
    assert stmts[0].startswith("-- leading comment") and stmts[0].endswith('"we;ird" int)')
    assert "'it''s; fine'" in stmts[1]
    assert "E'back\\'slash;'" in stmts[2]
    assert "RETURN 1; -- inner ;" in stmts[3]
    assert stmts[4].startswith("DO $$")
    assert stmts[5] == "SELECT $1::int"


@pytest.mark.no_db
def test_split_sql_rejects_meta_commands_and_unterminated_literals():
    with pytest.raises(manage_db.MigrationError, match="meta-commands"):
        manage_db.split_sql("\\set ON_ERROR_STOP on\nSELECT 1;")
    with pytest.raises(manage_db.MigrationError, match="unterminated"):
        manage_db.split_sql("SELECT 'abc;")
    with pytest.raises(manage_db.MigrationError, match="unterminated"):
        manage_db.split_sql("SELECT $x$ abc;")
    assert manage_db.split_sql("-- only a comment\n/* and this */\n") == []
