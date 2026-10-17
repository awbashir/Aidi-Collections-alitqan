"""Other accounts of the same debtor: GET /v2/cases/<id>/same-identity (db/migrations/0072,
services/same_identity.py). Uses the money fixtures (tests/fixtures/seed_money.sql):

  m.super  SuperAdmin -> every case
  m.sup    supervisor of "Money contract" only
  m.col    collector -> own cases only
  m.col2   collector, owns the cases in "Other contract"
  m.none   no keys at all
"""
from __future__ import annotations

import os
import random
import uuid

import pytest

from test_money_support import *  # noqa: F401,F403
from conftest import MAIN_DB, db_url


@pytest.fixture(scope="session")
def sid_app(money_db):
    """Minimal Flask app with the money blueprint (balance, approve, reverse) and ours."""
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db as sa_db, jwt

    app = Flask("cawp_same_identity_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    sa_db.init_app(app)
    jwt.init_app(app)
    import api.money
    import api.same_identity
    app.register_blueprint(api.money.bp)
    app.register_blueprint(api.same_identity.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def sc(sid_app):
    return MoneyClient(sid_app)


def new_identity() -> str:
    return "".join(random.choice("0123456789") for _ in range(10))


def case_with(conn, identity, amount=1000, owner=M.col, portfolio=M.portfolio, archived=False, account=None):
    cid = str(uuid.uuid4())
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_archive)
               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s)""",
      (cid, "Sid " + cid[:8], identity, account or "SID-" + cid, amount, owner, portfolio, archived))
    return cid


def get(sc, case_id, user=M.super, archived=None):
    url = f"/v2/cases/{case_id}/same-identity"
    if archived is not None:
        url += f"?include_archived={archived}"
    return sc.get(url, user)


def ids(data):
    return {a["id"] for a in data["accounts"]}


AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


# --------------------------------------------------------------------------- matching
def test_identity_is_normalised_and_the_opened_case_is_excluded(sc, db):
    ident = new_identity()
    opened = case_with(db, ident)
    spaced = case_with(db, f"  {ident[:4]} {ident[4:]} ")
    arabic = case_with(db, ident.translate(AR))
    nbsp_bidi = case_with(db, "‏" + ident + " ")
    longer = case_with(db, ident + "1")                     # a different identity
    shorter = case_with(db, ident[1:])
    r = get(sc, opened)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["identity"] == ident and data["case_id"] == opened
    assert ids(data) == {spaced, arabic, nbsp_bidi}
    assert opened not in ids(data) and longer not in ids(data) and shorter not in ids(data)
    assert data["summary"]["count"] == 3 and data["truncated"] is False
    # from the Arabic-Indic side the ASCII ones match too
    assert ids(get(sc, arabic).get_json()["data"]) == {opened, spaced, nbsp_bidi}


def test_letters_match_in_any_case_and_blank_identity_matches_nothing(sc, db):
    ident = "a" + new_identity()
    opened = case_with(db, ident)
    upper = case_with(db, ident.upper())
    assert ids(get(sc, opened).get_json()["data"]) == {upper}
    blank = case_with(db, "   ")
    case_with(db, " ")
    data = get(sc, blank).get_json()["data"]
    assert data["accounts"] == [] and data["summary"] == {"count": 0, "amount": "0.00", "paid": "0.00",
                                                          "remaining": "0.00"}


def test_lookup_expression_is_the_index_expression_and_uses_it(db):
    from conftest import DB_DIR
    from services.same_identity import IDENTITY_KEY_SQL
    mig = (DB_DIR / "migrations" / "0072_identity_lookup_index.sql").read_text(encoding="utf-8")
    assert "ON clints ((" + IDENTITY_KEY_SQL.format(col='"identityNumber"') + "));" in mig
    expr = IDENTITY_KEY_SQL.format(col='c."identityNumber"')
    with db.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        cur.execute(f"EXPLAIN SELECT c.id FROM clints c WHERE {expr} = '123'")
        plan = "\n".join(r[0] for r in cur.fetchall())
        cur.execute("RESET enable_seqscan")
        # the key itself, evaluated by PostgreSQL
        cur.execute(f"""SELECT {IDENTITY_KEY_SQL.format(col='v')} FROM (VALUES
                          (E' \u200f١٢٣ 4\u00a05\t'), ('ab-1'), (E' \u200b '), ('')) t(v)""")
        keys = [r[0] for r in cur.fetchall()]
    assert "ix_clints_identity_key" in plan, plan
    assert keys == ["12345", "AB-1", None, None]


# --------------------------------------------------------------------------- archived
def test_archived_accounts_only_with_the_toggle(sc, db):
    ident = new_identity()
    opened = case_with(db, ident)
    live = case_with(db, ident)
    renamed = case_with(db, ident, archived=True, account="ACC-77-أرشيف1")
    plain_archived = case_with(db, ident, archived=True)
    d0 = get(sc, opened, archived=0).get_json()["data"]
    assert ids(d0) == {live} and d0["include_archived"] is False
    assert ids(get(sc, opened).get_json()["data"]) == {live}           # default off
    d1 = get(sc, opened, archived=1).get_json()["data"]
    assert ids(d1) == {live, renamed, plain_archived} and d1["include_archived"] is True
    by_id = {a["id"]: a for a in d1["accounts"]}
    assert by_id[renamed]["is_archive"] is True and by_id[renamed]["account_number"] == "ACC-77-أرشيف1"
    assert by_id[live]["is_archive"] is False
    assert d1["accounts"][0]["id"] == live                              # live accounts first
    assert d1["summary"]["count"] == 3
    # an archived case opened by the user still lists its live siblings
    assert ids(get(sc, renamed).get_json()["data"]) == {opened, live}


# --------------------------------------------------------------------------- scope
def _family(db):
    ident = new_identity()
    opened = case_with(db, ident, owner=M.col)                              # Money contract, m.col
    other_contract = case_with(db, ident, owner=M.col2, portfolio=M.other_portfolio)
    own = case_with(db, ident, owner=M.col)
    unassigned = case_with(db, ident, owner=None)                           # Money contract
    return opened, other_contract, own, unassigned


def test_can_open_follows_the_viewer_scope(sc, db):
    opened, other_contract, own, unassigned = _family(db)
    expect = {
        M.col: {other_contract: False, own: True, unassigned: False},
        M.sup: {other_contract: False, own: True, unassigned: True},
        M.super: {other_contract: True, own: True, unassigned: True},
    }
    for user, want in expect.items():
        r = get(sc, opened, user)
        assert r.status_code == 200, (user, r.get_json())
        data = r.get_json()["data"]
        # every account of the identity is listed, read-only, whatever the viewer's scope
        assert {a["id"]: a["can_open"] for a in data["accounts"]} == want, user
        assert data["summary"]["count"] == 3


def test_out_of_scope_rows_are_listed_with_their_details(sc, db):
    opened, other_contract, _own, _un = _family(db)
    data = get(sc, opened, M.col).get_json()["data"]
    row = next(a for a in data["accounts"] if a["id"] == other_contract)
    assert row["can_open"] is False
    assert row["contract"] == {"id": M.other_contract, "name": "Other contract"}
    assert row["portfolio"] == {"id": M.other_portfolio, "name": "Other portfolio"}
    assert row["owner"] == {"id": M.col2, "name": "Money Collector2"}
    assert row["amount"] == "1000.00" and row["remaining"] == "1000.00"


def test_case_outside_scope_is_404_and_no_key_is_403(sc, db):
    opened, other_contract, _own, unassigned = _family(db)
    r = get(sc, other_contract, M.col)                     # m.col2's case, other contract
    assert r.status_code == 404 and r.get_json()["code"] == "case_not_found"
    assert get(sc, other_contract, M.sup).status_code == 404
    assert get(sc, unassigned, M.col).status_code == 404
    assert get(sc, str(uuid.uuid4()), M.super).status_code == 404
    r = get(sc, opened, M.none)
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"
    assert get(sc, other_contract, M.col2).status_code == 200


# --------------------------------------------------------------------------- money
def test_paid_counts_only_approved_non_reversed_payments(sc, db):
    ident = new_identity()
    opened = case_with(db, ident)
    other = case_with(db, ident, amount=1000.005)          # float8; read as round(::numeric, 2) = 1000.01
    third = case_with(db, ident, amount=50)
    make_payment(db, other, 100.1, status=True, approver=M.super)
    make_payment(db, other, 0.2, status=True, approver=M.super)
    make_payment(db, other, 70, status=None)                                # pending
    make_payment(db, other, 30, status=False, approver=M.super)             # rejected
    deleted = make_payment(db, other, 20, status=True, approver=M.super)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id = %s', (deleted,))
    rev = make_payment(db, other, 40)
    r = sc.post(f"/v2/payments/{rev}/approve", M.super, {"expected_revision": revision(db, "Payments", rev)})
    assert r.status_code == 200, r.get_json()
    r = sc.post(f"/v2/payments/{rev}/reverse", M.super,
                {"expected_revision": revision(db, "Payments", rev), "reason": "bank recalled"})
    assert r.status_code == 200, r.get_json()
    make_payment(db, third, 60, status=True, approver=M.super)              # overpaid by 10

    data = get(sc, opened).get_json()["data"]
    row = {a["id"]: a for a in data["accounts"]}
    assert (row[other]["amount"], row[other]["paid"], row[other]["remaining"]) == ("1000.01", "100.30", "899.71")
    assert (row[third]["amount"], row[third]["paid"], row[third]["remaining"]) == ("50.00", "60.00", "-10.00")
    # same numbers as the case balance card
    bal = sc.get(f"/v2/cases/{other}/balance", M.super).get_json()["data"]
    assert (bal["original"], bal["approved"], bal["outstanding"]) == ("1000.01", "100.30", "899.71")
    assert data["summary"] == {"count": 2, "amount": "1050.01", "paid": "160.30", "remaining": "889.71"}


def test_last_note_status_sub_status_and_owner(sc, db):
    ident = new_identity()
    opened = case_with(db, ident)
    other = case_with(db, ident)
    silent = case_with(db, ident)
    parent, child = str(uuid.uuid4()), str(uuid.uuid4())
    q(db, """INSERT INTO "CommentStatuses" (id, name, contract_id, color) VALUES (%s, 'Sid promise', %s, '#00aa00')""",
      (parent, M.contract))
    q(db, """INSERT INTO "CommentStatuses" (id, name, contract_id, parent_id) VALUES (%s, 'Sid next week', %s, %s)""",
      (child, M.contract, parent))
    q(db, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                     comment_sub_status_id, create_at)
             VALUES (%s, %s, '00000000-0000-4000-8000-000000000701', 'older', %s, NULL, '2026-10-01T08:00:00Z'),
                    (%s, %s, '00000000-0000-4000-8000-000000000701', 'newer', %s, %s, '2026-10-02T08:00:00Z')""",
      (other, M.col, parent, other, M.col, parent, child))
    data = get(sc, opened).get_json()["data"]
    row = {a["id"]: a for a in data["accounts"]}
    note = row[other]["last_note"]
    assert note["status"] == "Sid promise" and note["sub_status"] == "Sid next week"
    assert note["color"] == "#00aa00" and note["at"].startswith("2026-10-02T08:00:00")
    assert row[silent]["last_note"] is None
    assert row[other]["owner"] == {"id": M.col, "name": "Money Collector"}
    assert row[other]["name"] == "Sid " + other[:8]
