"""Blueprint registry.

Each module in MODULES exposes `bp = Blueprint(...)` and an optional `URL_PREFIX`.
During development a module sets `URL_PREFIX = "/_new"` so its routes can be tested
next to the legacy implementation still living in app.py; at integration the prefix is
set to None and the legacy routes are deleted from app.py.
"""
import importlib
import logging

log = logging.getLogger("cawp.api")

MODULES = [
    "api.clients_reports",   # client lists/exports (B1)
    "api.reports",           # comments / payments / promise-to-pay reports (B2)
    "api.dashboard",         # dashboards (B2)
    "api.imports",           # Excel imports + bulk actions + async jobs (C)
    "api.money",             # Phase 1 A: /v2 payments, reversals, promises
    "api.worklist",          # Phase 1 B: /v2 follow-ups, attention queue
    "api.case_history",      # Phase 1 C: /v2 note/contact history, case corrections
    "api.bulk",              # Phase 2: /v2 uploads, reviewed bulk jobs, jobs list
    "api.requests_v2",       # Phase 3 A: internal requests
    "api.plans",             # Phase 3 B: repayment plans
    "api.admin_v2",          # Phase 3 C: targets, sessions, /v2/me
]


def register_blueprints(app):
    for name in MODULES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name == name:      # module not written yet
                continue
            raise
        bp = getattr(mod, "bp", None)
        if bp is None:
            continue
        prefix = getattr(mod, "URL_PREFIX", None)
        app.register_blueprint(bp, url_prefix=prefix)
        log.info("registered blueprint %s (prefix=%s, %d rules)", bp.name, prefix,
                 sum(1 for r in app.url_map.iter_rules() if r.endpoint.startswith(bp.name + ".")))
