"""WSGI entry of the softphone end-to-end API (compose service `softphone-e2e-api`, TEST ONLY).

The real application (app.py, every registered blueprint) plus `api.calling_sip`, which the lead
registers in api/__init__.py MODULES at integration. Until then this registers it here, once.
It serves the throwaway database built by setup_db.py; it is never pointed at cawp-db.
"""
import os

if "cawp_test" not in (os.environ.get("DATABASE_URL") or ""):
    raise SystemExit("softphone-e2e-api refuses a database that is not a cawp_test* test database")

from app import app  # noqa: E402

import api.calling_sip  # noqa: E402

if "calling_sip" not in app.blueprints:
    app.register_blueprint(api.calling_sip.bp, url_prefix=api.calling_sip.URL_PREFIX)
