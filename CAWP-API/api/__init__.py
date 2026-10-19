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
    "api.payment_attachments",  # payment receipts: attach / list / download / remove (0069)
    "api.worklist",          # Phase 1 B: /v2 follow-ups, attention queue
    "api.case_history",      # Phase 1 C: /v2 note/contact history, case corrections
    "api.bulk",              # Phase 2: /v2 uploads, reviewed bulk jobs, jobs list
    "api.requests_v2",       # Phase 3 A: internal requests
    "api.plans",             # Phase 3 B: repayment plans
    "api.admin_v2",          # Phase 3 C: targets, sessions, /v2/me
    "api.reports_v2",        # Phase 4: /v2/reports, report options, report exports
    "api.calling",           # Phase 5: calls, attempts, wrap-up, extensions, campaigns, SIP credentials
    "api.calling_evidence",  # Phase 5 P4: attempt PBX evidence (CDR, recordings, imported CDR)
    "api.calling_campaigns", # Phase 5 P2: progressive calling campaigns
    "api.calling_sip",       # Phase 5 P3: short-lived SIP credentials for the in-app softphone
    "api.calling_survey",    # post-call IVR survey: per-contract switch and results (0066)
    "api.calling_monitor",   # call monitor: live calls and calls by user over time (0067)
    "api.ai",                # Phase 6 Q0/Q1: AI status, data policy, drafts
    "api.quality",           # Phase 6 Q2: quality workspace
    "api.voice_lab",         # Phase 6 Q3: voice training lab sessions
    "api.portfolio_import",  # portfolio upload: mapping, preview, one-transaction import, lineage (0070)
    "api.comment_status",    # note statuses: soft DELETE (0068); POST/PUT stay in app.py
    "api.same_identity",     # case page: other accounts of the same debtor identity (0072)
    "api.previous_status_import",  # previous statuses import from the old system (0074)
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
