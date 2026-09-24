"""CDR lookup by attempt id, for reconciliation only (read-only; never the source of state).

The restricted dialplan tags every CDR of a click-to-call with `userfield = cawp:<attempt>`
(and the agent channel's `uniqueid = cawp-<attempt>`). Once a call is over, reconciliation
asks the CDR whether the customer was dialled and whether that dial was answered:

    {"source": "csv", "available": true, "found": 2, "customer_dialed": true,
     "customer_answered": false, "disposition": "BUSY", "billsec": 0, "matched_via": ["userfield"], ...}

Matching (the tag parser and resolver are P4's, services/calling_cdr.py, reused as they are):

* the `userfield` tag `cawp:<attempt>` first - the reliable one: Issabel's
  `asteriskcdrdb.cdr.uniqueid` is varchar(32), so `cawp-<uuid>` (41 characters) arrives
  TRUNCATED there. The commissioning runbook requires `userfield` to be logged;
* a full `uniqueid` / `linkedid` tag when the CDR store keeps it (the test PBX's CSV does);
* a truncated `uniqueid` only when it names exactly ONE call attempt (resolve_attempt_tags),
  and that attempt is this one;
* always inside a date window around the attempt (from 1 h before it was created to 12 h
  after), so the lookup never scans the whole CDR table.

A CDR ANSWERED is NOT a conversation (it carries no bridge membership): reconciliation uses it
only to say "the customer did not answer" (-> ended) or "the customer may have talked, but
the bridge was not observed" (-> kept uncertain for human review).

Sources (CALLING_CDR_SOURCE):
    none   (default) no lookup; reconciliation relies on evidence only
    csv    the test PBX's cdr_csv Master.csv (CALLING_CDR_CSV, mounted read-only)
    mysql  Issabel's asteriskcdrdb.cdr through the existing CDR_* settings (core/config.py).
           Refused for 10.10.0.220 (the production PBX) unless CALLING_PBX_COMMISSIONED=1.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
import os
import re

log = logging.getLogger("cawp.calling.cdr")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_CSV_BYTES = 8 * 1024 * 1024          # read at most the last 8 MiB of Master.csv
PRODUCTION_PBX = "10.10.0.220"
WINDOW_BEFORE = _dt.timedelta(hours=1)
WINDOW_AFTER = _dt.timedelta(hours=12)
ISSABEL_UNIQUEID_LEN = 32                # asteriskcdrdb.cdr.uniqueid varchar(32)
RIYADH = _dt.timezone(_dt.timedelta(hours=3))   # the PBX writes calldate in its local wall clock


def window(around: _dt.datetime | None) -> tuple[_dt.datetime, _dt.datetime]:
    """(start, end) in PBX local time (Asia/Riyadh, naive) around the attempt's creation."""
    at = around or _dt.datetime.now(_dt.timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=_dt.timezone.utc)
    local = at.astimezone(RIYADH).replace(tzinfo=None)
    return local - WINDOW_BEFORE, local + WINDOW_AFTER


def match(attempt_id: str, *, uniqueid=None, userfield=None, linkedid=None, session=None) -> str | None:
    """How a CDR record names `attempt_id` (userfield | uniqueid | linkedid | uniqueid_prefix), or None."""
    from services.calling_cdr import attempt_tag, resolve_attempt_tags
    tag = attempt_tag(uniqueid=uniqueid, userfield=userfield, linkedid=linkedid)
    if tag is None:
        return None
    if tag["exact"]:
        return tag["via"] if tag["value"] == attempt_id else None
    if not attempt_id.replace("-", "").startswith(tag["value"]) or session is None:
        return None
    row = resolve_attempt_tags(session, [tag]).get(tag["key"])     # exactly one attempt, or nothing
    return "uniqueid_prefix" if row is not None and row["id"] == attempt_id else None


def summarize(source: str, rows: list[dict]) -> dict:
    dials = [r for r in rows if r.get("dstchannel")]
    answered = [r for r in dials if (r.get("disposition") or "").upper() == "ANSWERED"]
    first = answered[0] if answered else (dials[0] if dials else None)
    return {
        "source": source, "available": True, "found": len(rows),
        "customer_dialed": bool(dials), "customer_answered": bool(answered),
        "disposition": (first or {}).get("disposition"),
        "billsec": (answered[0].get("billsec") if answered else None),
        "matched_via": sorted({r["matched_via"] for r in rows}),
        "rows": [{"disposition": r.get("disposition"), "billsec": r.get("billsec"), "lastapp": r.get("lastapp"),
                  "dialed": bool(r.get("dstchannel")), "start": r.get("start"), "end": r.get("end"),
                  "matched_via": r["matched_via"]} for r in rows[:10]],
    }


def unavailable(source: str, why: str) -> dict:
    return {"source": source, "available": False, "reason": why, "found": 0}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _local(value):
    try:
        return _dt.datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


class CsvCdrSource:
    """cdr_csv layout with loguniqueid + loguserfield (pbx-test/conf/cdr.conf):
    accountcode, src, dst, dcontext, clid, channel, dstchannel, lastapp, lastdata, start, answer,
    end, duration, billsec, disposition, amaflags, uniqueid, userfield."""

    name = "csv"

    def __init__(self, path: str):
        self.path = path

    def lookup(self, attempt_id: str, *, around=None, session=None) -> dict:
        attempt_id = str(attempt_id)
        if not UUID_RE.match(attempt_id):
            return unavailable(self.name, "bad_attempt_id")
        lo, hi = window(around)
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as f:
                if size > MAX_CSV_BYTES:
                    f.seek(size - MAX_CSV_BYTES)
                    f.readline()                               # skip a partial line
                data = f.read().decode("utf-8", "replace")
        except OSError:
            return unavailable(self.name, "cdr_file_unavailable")
        rows = []
        for rec in csv.reader(io.StringIO(data)):
            if len(rec) < 17:
                continue
            start = _local(rec[9])
            if start is None or not lo <= start <= hi:
                continue
            via = match(attempt_id, uniqueid=rec[16], userfield=rec[17] if len(rec) > 17 else None, session=session)
            if via is None:
                continue
            rows.append({"dst": rec[2], "channel": rec[5], "dstchannel": rec[6], "lastapp": rec[7],
                         "start": rec[9], "answer": rec[10], "end": rec[11], "duration": _int(rec[12]),
                         "billsec": _int(rec[13]), "disposition": rec[14], "matched_via": via})
        return summarize(self.name, rows)


class MysqlCdrSource:
    name = "mysql"

    def __init__(self, *, host, database, user, password, timeout=3, commissioned=False):
        if not commissioned and host in (PRODUCTION_PBX,):
            raise ValueError("refusing the production PBX CDR database before commissioning")
        self.params = dict(host=host, database=database, user=user, password=password, connection_timeout=timeout)

    def lookup(self, attempt_id: str, *, around=None, session=None) -> dict:
        attempt_id = str(attempt_id)
        if not UUID_RE.match(attempt_id):
            return unavailable(self.name, "bad_attempt_id")
        lo, hi = window(around)
        try:
            import mysql.connector
            conn = mysql.connector.connect(**self.params)
        except Exception:  # noqa: BLE001
            return unavailable(self.name, "cdr_database_unavailable")
        try:
            cur = conn.cursor(dictionary=True)
            # userfield first; a (possibly truncated) uniqueid as a candidate only - match() decides
            cur.execute("SELECT dst, channel, dstchannel, lastapp, calldate, duration, billsec, disposition, "
                        "uniqueid, userfield FROM cdr "
                        "WHERE calldate BETWEEN %s AND %s AND (userfield = %s OR userfield LIKE %s OR uniqueid = %s "
                        "OR uniqueid = %s) ORDER BY calldate LIMIT 200",
                        (lo, hi, f"cawp:{attempt_id}", f"%cawp:{attempt_id}%", f"cawp-{attempt_id}",
                         f"cawp-{attempt_id}"[:ISSABEL_UNIQUEID_LEN]))
            fetched = cur.fetchall()
        except Exception:  # noqa: BLE001
            return unavailable(self.name, "cdr_query_failed")
        finally:
            conn.close()
        rows = []
        for r in fetched:
            via = match(attempt_id, uniqueid=r.get("uniqueid"), userfield=r.get("userfield"), session=session)
            if via is not None:
                rows.append({**{k: r.get(k) for k in ("dst", "channel", "dstchannel", "lastapp", "duration", "billsec",
                                                       "disposition")},
                             "start": str(r.get("calldate")), "end": None, "matched_via": via})
        return summarize(self.name, rows)


def from_env(env=None):
    env = os.environ if env is None else env
    kind = (env.get("CALLING_CDR_SOURCE") or "none").strip().lower()
    if kind == "csv":
        return CsvCdrSource(env.get("CALLING_CDR_CSV") or "/pbx-test-cdr/Master.csv")
    if kind == "mysql":
        from core.config import Config
        return MysqlCdrSource(host=Config.CDR_HOST, database=Config.CDR_DB, user=Config.CDR_USER,
                              password=Config.CDR_PASSWORD, timeout=Config.CDR_CONNECT_TIMEOUT,
                              commissioned=(env.get("CALLING_PBX_COMMISSIONED") or "").strip() in ("1", "true"))
    return None
