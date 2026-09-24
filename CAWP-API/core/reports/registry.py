"""Report registry. Streams add `services/reports_v2_<name>.py` exposing `REPORTS = (...)`;
discovery imports every such module on first use, so no framework file changes."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import threading

from core.reports.definition import ReportDefinition

log = logging.getLogger("cawp.reports.registry")

_REPORTS: dict[str, ReportDefinition] = {}
_lock = threading.RLock()
_discovered = False


def register(report: ReportDefinition) -> ReportDefinition:
    report.validate()
    with _lock:
        existing = _REPORTS.get(report.id)
        if existing is not None and existing is not report and type(existing) is not type(report):
            raise ValueError(f"report {report.id} is registered twice")
        _REPORTS[report.id] = report
    return report


def discover() -> None:
    global _discovered
    if _discovered:
        return
    with _lock:
        if _discovered:
            return
        import services
        for mod in pkgutil.iter_modules(services.__path__):
            if not mod.name.startswith("reports_v2_"):
                continue
            try:
                m = importlib.import_module(f"services.{mod.name}")
            except Exception:  # noqa: BLE001 - one broken stream must not hide the others
                log.exception("report module services.%s failed to import", mod.name)
                continue
            for r in getattr(m, "REPORTS", ()):
                register(r)
        _discovered = True


def get(report_id: str) -> ReportDefinition | None:
    discover()
    if not report_id:
        return None
    return _REPORTS.get(report_id) or _REPORTS.get(str(report_id).upper())


def all_reports() -> list[ReportDefinition]:
    discover()
    def order(r):
        num = "".join(ch for ch in r.id if ch.isdigit())
        return (int(num) if num else 10_000, r.id)
    return sorted(_REPORTS.values(), key=order)


def reset_for_tests(reports=None) -> None:
    """Tests: register extra definitions without rediscovery side effects."""
    discover()
    for r in reports or ():
        register(r)
