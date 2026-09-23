#!/usr/bin/env python
"""Drop everything `c_parity.py` created — safe to run at any time.

`c_parity.py` calls this logic in its own `finally`; this standalone entry point exists so
an interrupted run (a killed process, a gunicorn worker timeout) can still be swept up:

    docker exec cawp-api sh -c 'cd /app && python tests/c_cleanup.py'

It only ever touches portfolios named `C-TEST-%` and contacts whose `contact` ends in
`@test.local`, i.e. rows this harness created. Production data is never matched.
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, "/app")

from app import app                      # noqa: E402
from extensions import db                # noqa: E402

STEPS = (
    'UPDATE clints SET "clintArchive_id" = NULL WHERE portfolios_id = :p',
    "DELETE FROM notifications WHERE (data->>'client_id') IN "
    "(SELECT id::text FROM clints WHERE portfolios_id = :p)",
    'DELETE FROM "ClintContacts" WHERE "identityNumber" IN '
    '(SELECT "identityNumber" FROM clints WHERE portfolios_id = :p)',
    'DELETE FROM "clintArchive" WHERE clint_id IN '
    '(SELECT id FROM clints WHERE portfolios_id = :p)',
    'DELETE FROM clints WHERE portfolios_id = :p',
    'DELETE FROM portfolios WHERE id = :p',
)


def main():
    with app.app_context():
        rows = db.session.execute(
            text("SELECT id, name FROM portfolios WHERE name LIKE 'C-TEST-%'")).all()
        for pid, name in rows:
            for q in STEPS:
                db.session.execute(text(q), {"p": pid})
            print("dropped portfolio", name, pid, flush=True)
        db.session.execute(
            text("DELETE FROM \"ClintContacts\" WHERE contact LIKE '%@test.local'"))
        # notifications survive their client (no FK), so sweep them by the harness's own
        # message text as well as via the portfolio above
        db.session.execute(text(
            "DELETE FROM notifications WHERE data->>'message' LIKE '%تنبيه اختبار%'"))
        db.session.commit()
        left = db.session.execute(text(
            "SELECT (SELECT count(*) FROM portfolios WHERE name LIKE 'C-TEST-%'),"
            " (SELECT count(*) FROM \"ClintContacts\" WHERE contact LIKE '%@test.local'),"
            " (SELECT count(*) FROM notifications WHERE data->>'message' LIKE '%تنبيه اختبار%')"
        )).one()
        db.session.remove()
    temp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "uploads", "temp")
    removed = 0
    for f in os.listdir(temp) if os.path.isdir(temp) else []:
        if f.startswith("c_") and f.endswith(".xlsx"):
            os.remove(os.path.join(temp, f))
            removed += 1
    print(f"remaining: portfolios={left[0]} test_contacts={left[1]} "
          f"test_notifications={left[2]}; temp workbooks removed={removed}", flush=True)


if __name__ == "__main__":
    main()
