"""CDR_CALL_CENTER=0: a PBX without Issabel's call-center module (no `call_center` database).

Every CDR read then uses asteriskcdrdb.cdr only; before, the call-center query failed and the
whole report export failed with cdr_unavailable (RPT-25 on the a-alaoula PBX)."""
from __future__ import annotations

import datetime as _dt

from core.config import Config
from services import reports_v2_cdr as cdr


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(sql)

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self, dictionary=False):
        return FakeCursor(self.log)

    def close(self):
        pass


def _read(monkeypatch, call_center: bool):
    executed = []
    monkeypatch.setattr(Config, "CDR_CALL_CENTER", call_center)
    monkeypatch.setattr(cdr, "_connect", lambda: FakeConn(executed))
    lo = _dt.datetime(2026, 10, 1, tzinfo=_dt.timezone.utc)
    cdr.fetch(lo, lo + _dt.timedelta(days=1), fresh=True)
    return executed


def test_call_center_query_skipped_when_off(monkeypatch):
    sql = _read(monkeypatch, call_center=False)
    assert len(sql) == 1 and "asteriskcdrdb.cdr" in sql[0] and "call_center" not in sql[0]


def test_call_center_query_kept_by_default(monkeypatch):
    sql = _read(monkeypatch, call_center=True)
    assert len(sql) == 2 and "call_center.calls" in sql[1]
