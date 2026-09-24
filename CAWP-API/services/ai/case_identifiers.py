"""The customer identifiers of a case, for masking (services/ai/masking.py).

    from services.ai.case_identifiers import for_case
    ids = for_case(case_id)          # Identifiers(names, identity_numbers, phones, accounts)

Reads the case's debtor name, identity number and account number, and every contact number of the
debtor (blocked and inactive ones included: masking more is the safe side). The caller must have
checked scope already; this helper does not.
"""
from __future__ import annotations

from sqlalchemy import text

from services.ai.masking import Identifiers


def for_case(case_id, session=None) -> Identifiers:
    if session is None:
        from extensions import db
        session = db.session
    row = session.execute(text("""
        SELECT c.name, c."identityNumber" AS identity_number, c."accountNumber" AS account_number
          FROM clints c WHERE c.id = CAST(:id AS uuid)"""), {"id": str(case_id)}).mappings().first()
    if row is None:
        return Identifiers()
    phones = []
    if row["identity_number"]:
        phones = [r[0] for r in session.execute(text("""
            SELECT DISTINCT k.contact FROM "ClintContacts" k
             WHERE k."identityNumber" = :idn AND k.contact IS NOT NULL AND btrim(k.contact) <> ''"""),
            {"idn": row["identity_number"]}).all()]
    return Identifiers(
        names=tuple(v for v in (row["name"],) if v),
        identity_numbers=tuple(v.strip() for v in (row["identity_number"],) if v and v.strip()),
        phones=tuple(phones),
        accounts=tuple(v.strip() for v in (row["account_number"],) if v and v.strip()),
    )
