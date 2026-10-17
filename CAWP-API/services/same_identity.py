"""Other accounts of the same debtor (customer request 2026-10-17).

On a case page, the other `clints` rows whose identity number is the same as this case's,
after normalisation (`IDENTITY_KEY_SQL`: Arabic-Indic digits folded, every space and bidi /
zero-width mark removed, upper case, NULL when empty; indexed by db/migrations/0072 as
ix_clints_identity_key, which spells the same expression out).

Visibility: the caller has already been authorized for the OPENED case (money.require_case).
Every account of the same identity is then listed read-only, whatever its owner or contract;
`can_open` says whether the account is inside the caller's own case scope (the same predicate
as money.scope_sql / clients_scope.scope_condition), and only those rows are linked in the UI.

Money is the case balance rule of services/money.py (`_BALANCE_SQL`): `amount` is the original
debt `round(clints.amount::numeric, 2)`, `paid` the approved, non-deleted, non-reversed
payments, `remaining` = amount - paid (negative when overpaid, exactly like the balance card's
`outstanding`). Pending, rejected and reversed money never counts. The summary is summed in
SQL numeric over the same rows that are listed.

Archived accounts (is_archive, including the `<account>-أرشيف<n>` records a portfolio upload
renamed) are left out unless `include_archived`. Read-only: nothing is written or audited.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from services.money import iso, money, scope_sql

# The identity key of an "identityNumber" column. MUST stay character-for-character the
# expression of ix_clints_identity_key (db/migrations/0072), or the lookup scans clints;
# tests/test_same_identity.py checks both. Raw string: SQL receives E'[\\s...]' as written.
IDENTITY_KEY_SQL = (r"""nullif(upper(regexp_replace(translate({col}, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', """
                    r"""'01234567890123456789'), E'[\\s\u00a0\u1680\u2000-\u200f\u2028-\u202f\u205f-\u206f\u3000\ufeff]', """
                    r"""'', 'g')), '')""")

# A junk identity shared by thousands of rows must not produce a huge page; the summary still
# counts every matching account.
MAX_ACCOUNTS = 500


def _s():
    from extensions import db
    return db.session


def parse_flag(raw) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


_ACCOUNTS_SQL = """
    WITH acc AS (
        SELECT c.id, c.name, c."accountNumber" AS account_number, c.is_archive, c.create_at,
               round(c.amount::numeric, 2) AS original,
               coalesce(pay.paid, 0) AS paid,
               ({scope}) AS can_open,
               pf.id::text AS portfolio_id, pf.name AS portfolio_name,
               ct.id::text AS contract_id, ct.name AS contract_name,
               c.user_id::text AS owner_id,
               btrim(coalesce(ou.frist_name, '') || ' ' || coalesce(ou.last_name, '')) AS owner_name,
               lc.create_at AS note_at, lcs.name AS note_status, lcs.color AS note_color,
               lss.name AS note_sub_status
          FROM clints c
          LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
          LEFT JOIN contracts ct ON ct.id = pf.contract_id
          LEFT JOIN users ou ON ou.id = c.user_id
          LEFT JOIN "Comments" lc ON lc.id = c.last_comment_id AND lc.is_deleted = false
          LEFT JOIN "CommentStatuses" lcs ON lcs.id = lc.comment_status_id
          LEFT JOIN "CommentStatuses" lss ON lss.id = lc.comment_sub_status_id
          LEFT JOIN LATERAL (
              SELECT sum(round(p.amount::numeric, 2)) AS paid
                FROM "Payments" p
               WHERE p.clint_id = c.id
                 AND p.approval_status = true AND p.is_deleted = false
                 AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
          ) pay ON true
         WHERE {key} = :norm
           AND c.id <> CAST(:cid AS uuid)
           AND (CAST(:archived AS boolean) OR c.is_archive = false)
    )
    SELECT acc.*,
           count(*) OVER () AS total_count,
           sum(original) OVER () AS total_amount,
           sum(paid) OVER () AS total_paid
      FROM acc
     ORDER BY is_archive, create_at DESC, id
     LIMIT :lim
"""


def same_identity(case_id, scope, include_archived: bool = False) -> dict:
    """{identity, summary, accounts, truncated} for the case `case_id` (already authorized)."""
    s = _s()
    me = s.execute(text("""
        SELECT "identityNumber" AS identity, {key} AS norm
          FROM clints WHERE id = CAST(:cid AS uuid)
    """.format(key=IDENTITY_KEY_SQL.format(col='"identityNumber"'))), {"cid": str(case_id)}).mappings().first()
    identity = me["identity"] if me else None
    norm = me["norm"] if me else None
    rows = []
    if norm:
        pred, params = scope_sql(scope, "c", "sid")
        sql = _ACCOUNTS_SQL.format(scope=pred, key=IDENTITY_KEY_SQL.format(col='c."identityNumber"'))
        rows = s.execute(text(sql), {"norm": norm, "cid": str(case_id), "archived": bool(include_archived),
                                     "lim": MAX_ACCOUNTS, **params}).mappings().all()

    count = int(rows[0]["total_count"]) if rows else 0
    amount = rows[0]["total_amount"] if rows else Decimal("0")
    paid = rows[0]["total_paid"] if rows else Decimal("0")
    accounts = []
    for r in rows:
        note = None
        if r["note_at"] is not None:
            note = {"status": r["note_status"], "sub_status": r["note_sub_status"],
                    "color": r["note_color"], "at": iso(r["note_at"])}
        accounts.append({
            "id": str(r["id"]),
            "account_number": r["account_number"],
            "name": r["name"],
            "contract": None if r["contract_id"] is None else {"id": r["contract_id"], "name": r["contract_name"]},
            "portfolio": None if r["portfolio_id"] is None else {"id": r["portfolio_id"], "name": r["portfolio_name"]},
            "amount": money(r["original"]),
            "paid": money(r["paid"]),
            "remaining": money(r["original"] - r["paid"]),
            "is_archive": bool(r["is_archive"]),
            "last_note": note,
            "owner": None if r["owner_id"] is None else {"id": r["owner_id"], "name": r["owner_name"] or None},
            "can_open": bool(r["can_open"]),
        })
    return {
        "case_id": str(case_id),
        "identity": identity,
        "include_archived": bool(include_archived),
        "summary": {"count": count, "amount": money(amount), "paid": money(paid),
                    "remaining": money(amount - paid)},
        "accounts": accounts,
        "truncated": count > len(accounts),
    }
