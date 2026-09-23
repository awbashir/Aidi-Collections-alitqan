#!/usr/bin/env python
"""CAWP schema migration runner: plain, reviewable SQL files, recorded in `schema_migrations`.

    python manage_db.py status                 # applied / pending / checksum state
    python manage_db.py baseline [--dry-run]   # record db/01..03 as applied when their objects exist
    python manage_db.py up [--dry-run] [--target 0006]
    python manage_db.py verify                 # exit 1 on a changed or unknown applied file
    python manage_db.py sync-permissions [--dry-run]
    python manage_db.py load-schema db/schema/baseline.sql   # empty databases only (tests / CI)

In the Docker stack (the compose file mounts ./db at /db in the api container):

    docker exec cawp-api python manage_db.py status

Rules (see db/README.md):

* Migrations live in `db/migrations/NNNN_description.sql`, are applied in version order
  and are never edited once applied: the runner stores a sha256 of each file and refuses
  to run while an applied file's checksum differs.
* Each file runs in one transaction, together with its `schema_migrations` row, unless its
  header carries `-- migrate:no-transaction` (needed for `CREATE INDEX CONCURRENTLY`). Such
  a file is split into statements and run in autocommit mode; it is recorded only after
  every statement succeeded, so it must be idempotent (`IF NOT EXISTS`) to be re-runnable.
* Transactional files run with `lock_timeout = 5s` by default (`--lock-timeout`, or a
  `-- migrate:lock-timeout 30s` header) so an `ALTER TABLE` waiting behind a long query
  fails fast instead of queueing every other request behind its lock.
  `statement_timeout` is always 0 for migrations.
* psql meta-commands (`\\set`, `\\timing`, ...) are not supported in migration files.
* The three historical scripts `db/01..03_*.sql` predate the runner. They are *legacy*
  versions 0001-0003: `baseline` detects their objects in the database and records them
  (it never runs them); `up` refuses to run until they are recorded.
* One runner at a time: a session advisory lock guards `baseline` / `up`.

The runner deliberately does not import the Flask app: it only needs psycopg2 and
DATABASE_URL (or --database-url), so it also runs from a bare `docker run` of the image
with `db/` mounted.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

ADVISORY_LOCK_KEY = 7_236_401_001  # arbitrary constant, "CAWP schema"
DEFAULT_LOCK_TIMEOUT = "5s"
MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9][a-z0-9_]*)\.sql$")
HEADER_NO_TX = re.compile(r"^--\s*migrate:no-transaction\s*$", re.M)
HEADER_LOCK_TIMEOUT = re.compile(r"^--\s*migrate:lock-timeout[ =]+(\S+)\s*$", re.M)

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      text PRIMARY KEY,
    name         text        NOT NULL,
    checksum     text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    duration_ms  integer     NOT NULL DEFAULT 0,
    execution    text        NOT NULL DEFAULT 'applied'
                 CHECK (execution IN ('applied', 'baselined')),
    applied_by   text        NOT NULL DEFAULT current_user
)
"""


class MigrationError(RuntimeError):
    pass


class ChecksumMismatch(MigrationError):
    pass


# --------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------

def default_db_dir() -> Path:
    """`db/` next to CAWP-API in a checkout, `/db` in the container (/app/../db)."""
    env = os.getenv("CAWP_DB_DIR")
    if env:
        return Path(env)
    return (Path(__file__).resolve().parent.parent / "db").resolve()


def file_checksum(path: Path) -> str:
    """sha256 of the file with line endings normalised (a CRLF checkout is not a change)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


@dataclasses.dataclass
class Migration:
    version: str
    name: str
    path: Path
    legacy: bool = False

    @property
    def checksum(self) -> str:
        return file_checksum(self.path)

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def transactional(self) -> bool:
        return not HEADER_NO_TX.search(self.sql)

    @property
    def lock_timeout(self) -> str | None:
        m = HEADER_LOCK_TIMEOUT.search(self.sql)
        return m.group(1) if m else None


# Legacy scripts: never executed by the runner, only detected + recorded by `baseline`.
LEGACY = [
    ("0001", "01_performance_indexes.sql"),
    ("0002", "02_jsonb_attribute.sql"),
    ("0003", "03_last_comment.sql"),
]


def discover(db_dir: Path) -> list[Migration]:
    """Legacy entries + db/migrations/*.sql, sorted, with duplicate/format checks."""
    out: list[Migration] = []
    for version, fname in LEGACY:
        p = db_dir / fname
        if not p.is_file():
            raise MigrationError(f"legacy script {p} is missing")
        out.append(Migration(version, fname[:-4], p, legacy=True))
    mdir = db_dir / "migrations"
    seen = {m.version for m in out}
    if mdir.is_dir():
        for p in sorted(mdir.iterdir()):
            if p.suffix != ".sql":
                continue
            m = MIGRATION_RE.match(p.name)
            if not m:
                raise MigrationError(f"{p.name}: migration files must be named NNNN_lower_snake.sql")
            if m.group(1) in seen:
                raise MigrationError(f"{p.name}: version {m.group(1)} is used twice")
            seen.add(m.group(1))
            out.append(Migration(m.group(1), p.stem, p))
    out.sort(key=lambda m: m.version)
    return out


# --------------------------------------------------------------------------------------
# SQL splitting (only for -- migrate:no-transaction files)
# --------------------------------------------------------------------------------------

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def split_sql(sql: str) -> list[str]:
    """Split a script into top-level statements.

    Understands -- and /* */ comments (nested), '...' strings ('' escapes), E'...' strings
    (backslash escapes), "..." identifiers and $tag$...$tag$ bodies. Comment-only chunks
    are dropped. Raises on psql meta-commands and on unterminated literals.
    """
    stmts, buf, i, n = [], [], 0, len(sql)
    line_start = True
    while i < n:
        c = sql[i]
        if line_start and c == "\\":
            raise MigrationError("psql meta-commands (\\...) are not supported in migrations")
        if c in " \t":
            buf.append(c); i += 1
            continue
        line_start = c == "\n"
        if c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j]); i = j
            continue
        if c == "/" and sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1; j += 2
                elif sql.startswith("*/", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            if depth:
                raise MigrationError("unterminated /* comment")
            buf.append(sql[i:j]); i = j
            continue
        if c == "'":
            escape = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            j = i + 1
            while True:
                if j >= n:
                    raise MigrationError("unterminated string literal")
                if escape and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1]); i = j + 1
            continue
        if c == '"':
            j = i + 1
            while True:
                j = sql.find('"', j)
                if j < 0:
                    raise MigrationError("unterminated quoted identifier")
                if j + 1 < n and sql[j + 1] == '"':
                    j += 2
                    continue
                break
            buf.append(sql[i:j + 1]); i = j + 1
            continue
        if c == "$":
            m = _DOLLAR_TAG.match(sql, i)
            if m and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                tag = m.group(0)
                j = sql.find(tag, m.end())
                if j < 0:
                    raise MigrationError(f"unterminated dollar-quoted string {tag}")
                buf.append(sql[i:j + len(tag)]); i = j + len(tag)
                continue
        if c == ";":
            stmts.append("".join(buf)); buf = []; i += 1
            continue
        buf.append(c); i += 1
    stmts.append("".join(buf))
    return [s.strip() for s in stmts if _has_code(s)]


def _strip_comments(chunk: str) -> str:
    stripped = re.sub(r"/\*.*?\*/", "", chunk, flags=re.S)
    return re.sub(r"--[^\n]*", "", stripped).strip()


def _has_code(chunk: str) -> bool:
    return bool(_strip_comments(chunk))


_TX_CONTROL = re.compile(r"^(BEGIN|COMMIT|ROLLBACK|END|START\s+TRANSACTION|ABORT)\b", re.I)


# --------------------------------------------------------------------------------------
# Legacy detection
# --------------------------------------------------------------------------------------

_INDEX_NAME = re.compile(r"CREATE\s+INDEX\s+CONCURRENTLY\s+IF\s+NOT\s+EXISTS\s+(\"?[A-Za-z0-9_]+\"?)", re.I)


def _script_indexes(path: Path) -> list[str]:
    return [n.strip('"') for n in _INDEX_NAME.findall(path.read_text(encoding="utf-8"))]


def _missing_valid_indexes(cur, names: list[str]) -> list[str]:
    cur.execute(
        """SELECT c.relname FROM pg_class c
             JOIN pg_index i ON i.indexrelid = c.oid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = ANY(%s) AND i.indisvalid""",
        (names,))
    have = {r[0] for r in cur.fetchall()}
    return [n for n in names if n not in have]


def detect_legacy(cur, mig: Migration) -> list[str]:
    """Return what is missing for a legacy script (empty list = fully present)."""
    missing: list[str] = []
    if mig.version == "0001":
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        if not cur.fetchone():
            missing.append("extension pg_trgm")
        missing += [f"index {n}" for n in _missing_valid_indexes(cur, _script_indexes(mig.path))]
    elif mig.version == "0002":
        cur.execute("""SELECT data_type FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = 'clints'
                          AND column_name = 'attribute'""")
        row = cur.fetchone()
        if not row or row[0] != "jsonb":
            missing.append(f"clints.attribute is {row[0] if row else 'absent'}, not jsonb")
    elif mig.version == "0003":
        cur.execute("""SELECT column_name FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = 'clints'
                          AND column_name IN ('last_comment_at', 'last_comment_id')""")
        have = {r[0] for r in cur.fetchall()}
        missing += [f"column clints.{c}" for c in ("last_comment_at", "last_comment_id") if c not in have]
        for fn in ("clints_refresh_last_comment", "trg_comments_last_comment"):
            cur.execute("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                        "WHERE n.nspname = current_schema() AND p.proname = %s", (fn,))
            if not cur.fetchone():
                missing.append(f"function {fn}")
        cur.execute("""SELECT 1 FROM pg_trigger WHERE tgname = 'comments_last_comment_trg'
                          AND tgrelid = to_regclass('"Comments"') AND NOT tgisinternal""")
        if not cur.fetchone():
            missing.append('trigger comments_last_comment_trg on "Comments"')
        missing += [f"index {n}" for n in _missing_valid_indexes(cur, _script_indexes(mig.path))]
    else:  # pragma: no cover - LEGACY is fixed
        raise MigrationError(f"no detector for legacy version {mig.version}")
    return missing


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------

def libpq_dsn(url: str) -> str:
    """postgresql+psycopg2://... (SQLAlchemy form) -> postgresql://... (libpq form)."""
    return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql://", url)


def _log(msg: str = "") -> None:
    print(msg, flush=True)


class Runner:
    def __init__(self, dsn: str, db_dir: Path | str | None = None,
                 lock_timeout: str = DEFAULT_LOCK_TIMEOUT, log=_log):
        self.dsn = libpq_dsn(dsn)
        self.db_dir = Path(db_dir) if db_dir else default_db_dir()
        self.lock_timeout = lock_timeout
        self.log = log

    # -- connection helpers ---------------------------------------------------------
    def connect(self):
        conn = psycopg2.connect(self.dsn, application_name="cawp-migrate")
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
        return conn

    def _ensure_table(self, conn):
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MIGRATIONS_DDL)

    def _applied(self, conn) -> dict[str, dict]:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS ok")
            if not cur.fetchone()["ok"]:
                return {}
            cur.execute("SELECT version, name, checksum, applied_at, duration_ms, execution "
                        "FROM schema_migrations ORDER BY version")
            return {r["version"]: dict(r) for r in cur.fetchall()}

    def _lock(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            if not cur.fetchone()[0]:
                raise MigrationError("another migration run holds the advisory lock; try again later")

    def _unlock(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))

    # -- state ------------------------------------------------------------------------
    def plan(self, conn=None) -> dict:
        """{'migrations': [...], 'applied': {...}, 'pending': [...], 'mismatched': [...], 'unknown': [...]}"""
        own = conn is None
        conn = conn or self.connect()
        try:
            migs = discover(self.db_dir)
            applied = self._applied(conn)
        finally:
            if own:
                conn.close()
        known = {m.version: m for m in migs}
        mismatched = [m for m in migs if m.version in applied and applied[m.version]["checksum"] != m.checksum]
        unknown = sorted(v for v in applied if v not in known)
        pending = [m for m in migs if m.version not in applied]
        return {"migrations": migs, "applied": applied, "pending": pending,
                "mismatched": mismatched, "unknown": unknown}

    def status(self) -> dict:
        p = self.plan()
        for m in p["migrations"]:
            a = p["applied"].get(m.version)
            if a is None:
                state = "pending (legacy: run `baseline`)" if m.legacy else "pending"
            elif a["checksum"] != m.checksum:
                state = "CHECKSUM MISMATCH"
            else:
                state = f"{a['execution']} {a['applied_at']:%Y-%m-%d %H:%M:%S%z} ({a['duration_ms']} ms)"
            mode = "legacy" if m.legacy else ("tx" if m.transactional else "no-tx")
            self.log(f"  {m.version}  {m.name:<40} {mode:<7} {state}")
        for v in p["unknown"]:
            self.log(f"  {v}  {p['applied'][v]['name']:<40} ??      applied but the file is missing")
        self.log(f"{len(p['applied'])} applied, {len(p['pending'])} pending, "
                 f"{len(p['mismatched'])} checksum mismatch(es), {len(p['unknown'])} unknown")
        return p

    def verify(self, p: dict | None = None) -> None:
        p = p or self.plan()
        problems = [f"{m.version} {m.name}: file changed after it was applied "
                    f"(recorded {p['applied'][m.version]['checksum'][:12]}, file {m.checksum[:12]})"
                    for m in p["mismatched"]]
        problems += [f"{v}: recorded in schema_migrations but no such file" for v in p["unknown"]]
        if problems:
            raise ChecksumMismatch("refusing to continue:\n  " + "\n  ".join(problems))

    # -- baseline ---------------------------------------------------------------------
    def baseline(self, dry_run: bool = False) -> list[str]:
        """Record legacy scripts whose objects are all present. Returns recorded versions."""
        conn = self.connect()
        recorded = []
        try:
            if not dry_run:
                self._ensure_table(conn)
                self._lock(conn)
            p = self.plan(conn)
            self.verify(p)
            with conn.cursor() as cur:
                for m in (m for m in p["migrations"] if m.legacy):
                    if m.version in p["applied"]:
                        self.log(f"  {m.version} {m.name}: already recorded")
                        continue
                    missing = detect_legacy(cur, m)
                    if missing:
                        self.log(f"  {m.version} {m.name}: NOT present, not recorded; missing: "
                                 + ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else ""))
                        continue
                    if dry_run:
                        self.log(f"  {m.version} {m.name}: present, would be recorded")
                    else:
                        cur.execute("INSERT INTO schema_migrations (version, name, checksum, duration_ms, execution) "
                                    "VALUES (%s, %s, %s, 0, 'baselined')", (m.version, m.name, m.checksum))
                        self.log(f"  {m.version} {m.name}: present, recorded as baselined")
                    recorded.append(m.version)
        finally:
            if not dry_run:
                self._unlock(conn)
            conn.close()
        return recorded

    # -- up ---------------------------------------------------------------------------
    def up(self, dry_run: bool = False, target: str | None = None) -> list[str]:
        conn = self.connect()
        done = []
        try:
            if not dry_run:
                self._ensure_table(conn)
                self._lock(conn)
            p = self.plan(conn)
            self.verify(p)
            legacy_missing = [m for m in p["pending"] if m.legacy]
            if legacy_missing:
                raise MigrationError(
                    "legacy scripts not recorded: " + ", ".join(f"{m.version} {m.name}" for m in legacy_missing)
                    + ". Apply them per DEPLOY.md, then run `manage_db.py baseline`.")
            todo = [m for m in p["pending"] if target is None or m.version <= target]
            if not todo:
                self.log("nothing to apply; schema is up to date")
                return []
            for m in todo:
                mode = "transaction" if m.transactional else "no-transaction"
                lt = m.lock_timeout or (self.lock_timeout if m.transactional else None)
                if dry_run:
                    n = len(split_sql(m.sql))
                    self.log(f"  would apply {m.version} {m.name} ({mode}, {n} statement(s), "
                             f"lock_timeout={lt or 'none'}, sha256 {m.checksum[:12]})")
                    done.append(m.version)
                    continue
                self.log(f"  applying {m.version} {m.name} ({mode}) ...")
                ms = self._apply(conn, m, lt)
                self.log(f"  applied  {m.version} {m.name} in {ms} ms")
                done.append(m.version)
        finally:
            if not dry_run:
                self._unlock(conn)
            conn.close()
        return done

    def _apply(self, conn, m: Migration, lock_timeout: str | None) -> int:
        sql, checksum = m.sql, m.checksum
        split = split_sql(sql)  # validates (no psql meta-commands, balanced quoting)
        for stmt in split:
            if _TX_CONTROL.match(_strip_comments(stmt)):
                raise MigrationError(f"{m.version} {m.name}: remove '{stmt.split()[0]}' - the runner owns "
                                     f"transactions (use `-- migrate:no-transaction` for CONCURRENTLY)")
        t0 = time.monotonic()
        rec = ("INSERT INTO schema_migrations (version, name, checksum, duration_ms, execution) "
               "VALUES (%s, %s, %s, %s, 'applied')")
        if m.transactional:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = %s", (lock_timeout or "0",))
                    cur.execute(sql)
                    ms = int((time.monotonic() - t0) * 1000)
                    cur.execute(rec, (m.version, m.name, checksum, ms))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise MigrationError(f"{m.version} {m.name} failed and was rolled back: {e}") from e
            finally:
                conn.autocommit = True
            return ms
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = %s", (lock_timeout or "0",))
            try:
                for i, stmt in enumerate(split, 1):
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        raise MigrationError(
                            f"{m.version} {m.name}: statement {i}/{len(split)} failed: {e}\n"
                            f"Earlier statements are committed; the file is not recorded. Fix the "
                            f"cause (drop any INVALID index it left) and re-run `up`.") from e
            finally:
                cur.execute("RESET lock_timeout")
            ms = int((time.monotonic() - t0) * 1000)
            cur.execute(rec, (m.version, m.name, checksum, ms))
        return ms

    # -- load-schema (tests / CI) -------------------------------------------------------
    def load_schema(self, path: Path | str) -> None:
        """Load a schema-only dump into an EMPTY database (refuses otherwise)."""
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')")
                if cur.fetchone()[0]:
                    raise MigrationError("load-schema refuses to run: the target database already has tables")
            text = Path(path).read_text(encoding="utf-8")
            text = "\n".join(l for l in text.splitlines() if not l.startswith("\\"))
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(text)
            conn.commit()
        finally:
            conn.close()


# --------------------------------------------------------------------------------------
# Permissions (core/permissions.py registry -> roles.permission)
# --------------------------------------------------------------------------------------

def sync_permissions(dsn: str, dry_run: bool = False, log=_log) -> dict:
    from sqlalchemy import create_engine
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from core.permissions import sync_role_permissions

    url = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg2://", dsn)
    engine = create_engine(url, connect_args={"application_name": "cawp-migrate"})
    try:
        with engine.begin() as conn:
            report = sync_role_permissions(conn, dry_run=dry_run, source="manage_db")
    finally:
        engine.dispose()
    if not report:
        log("permissions: every role already has every registered key")
    for role, keys in report.items():
        log(f"permissions: {'would add' if dry_run else 'added'} {len(keys)} key(s) to role {role}: {', '.join(keys)}")
    return report


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _database_url(arg: str | None) -> str:
    if arg:
        return arg
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env")
    except Exception:  # noqa: BLE001 - dotenv is optional here
        pass
    url = os.getenv("MIGRATE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("set DATABASE_URL (or MIGRATE_DATABASE_URL, or pass --database-url)")
    return url


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--database-url", help="default: $MIGRATE_DATABASE_URL, then $DATABASE_URL")
    ap.add_argument("--dir", help="db directory holding 01..03 and migrations/ (default: ../db or $CAWP_DB_DIR)")
    ap.add_argument("--lock-timeout", default=DEFAULT_LOCK_TIMEOUT,
                    help="lock_timeout for transactional migrations (default 5s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("verify")
    b = sub.add_parser("baseline"); b.add_argument("--dry-run", action="store_true")
    u = sub.add_parser("up"); u.add_argument("--dry-run", action="store_true"); u.add_argument("--target")
    u.add_argument("--no-permissions", action="store_true", help="skip the roles.permission merge after migrating")
    s = sub.add_parser("sync-permissions"); s.add_argument("--dry-run", action="store_true")
    ls = sub.add_parser("load-schema"); ls.add_argument("file")
    a = ap.parse_args(argv)

    url = _database_url(a.database_url)
    r = Runner(url, a.dir, lock_timeout=a.lock_timeout)
    try:
        if a.cmd == "status":
            p = r.status()
            return 1 if (p["mismatched"] or p["unknown"]) else 0
        if a.cmd == "verify":
            r.verify()
            _log("ok: every applied migration matches its file")
            return 0
        if a.cmd == "baseline":
            r.baseline(dry_run=a.dry_run)
            return 0
        if a.cmd == "up":
            r.up(dry_run=a.dry_run, target=a.target)
            if not a.no_permissions:
                sync_permissions(url, dry_run=a.dry_run)
            return 0
        if a.cmd == "sync-permissions":
            sync_permissions(url, dry_run=a.dry_run)
            return 0
        if a.cmd == "load-schema":
            r.load_schema(a.file)
            _log(f"loaded {a.file}")
            return 0
    except MigrationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
