"""Report export settings (read at call time).

| Variable | Default | Meaning |
|---|---|---|
| `REPORT_EXPORT_TTL_DAYS` | 7 | artifact lifetime; the retention task then deletes the file (row kept, status `expired`) |
| `REPORT_EXPORT_BATCH` | 500 | rows fetched from the server-side cursor and written per batch |
| `REPORT_EXPORT_MAX_ROWS` | 500000 | an export with more rows fails `too_many_rows` (narrow the filters) |
| `REPORT_EXPORT_MAX_MB` | 200 | a larger file fails `artifact_too_large` and is deleted |
| `REPORT_EXPORT_MAX_ATTEMPTS` | 3 | crashed/errored attempts before the export fails |
| `REPORT_EXPORT_TIMEOUT_MS` | 1800000 | statement timeout of the export snapshot (30 min) |
| `REPORT_PREVIEW_TIMEOUT_MS` | 60000 | statement timeout of a preview / selector request |

Storage (`BULK_STORAGE_DIR`, private) and the worker lease (`BULK_LEASE_SECONDS`) are shared
with the Phase 2 bulk jobs.
"""
from __future__ import annotations

import dataclasses
import os


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


@dataclasses.dataclass(frozen=True)
class ReportSettings:
    ttl_days: int
    batch: int
    max_rows: int
    max_bytes: int
    max_attempts: int
    timeout_ms: int
    lease_seconds: int


def get() -> ReportSettings:
    from core.bulk import settings as bulk
    return ReportSettings(
        ttl_days=_int("REPORT_EXPORT_TTL_DAYS", 7),
        batch=_int("REPORT_EXPORT_BATCH", 500),
        max_rows=_int("REPORT_EXPORT_MAX_ROWS", 500_000),
        max_bytes=_int("REPORT_EXPORT_MAX_MB", 200) * 1024 * 1024,
        max_attempts=_int("REPORT_EXPORT_MAX_ATTEMPTS", 3),
        timeout_ms=_int("REPORT_EXPORT_TIMEOUT_MS", 1_800_000),
        lease_seconds=bulk.get().lease_seconds,
    )
