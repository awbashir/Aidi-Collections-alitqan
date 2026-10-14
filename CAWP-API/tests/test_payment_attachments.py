"""Payment attachments (receipts): /v2/payments/<id>/attachments (db/migrations/0069).

Upload (stored like a client document + clint_documents row + link + audit), magic-byte type
check, size and count limits, scope / permission matrix, private download headers, list, the
removal rules, and the `attachments` field on the v2 payment payloads.
"""
from __future__ import annotations

import io
import json
import os
import uuid

import pytest

from test_money_support import *  # noqa: F401,F403

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

DOCS_ONLY_ROLE = "aaaaaaaa-0000-4000-8000-0000000002a1"
DOCS_ONLY_USER = "aaaaaaaa-0000-4000-8000-0000000005a1"


@pytest.fixture(scope="module")
def pa_app(money_db):
    """Minimal Flask app with the money and payment-attachment blueprints."""
    import psycopg2
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from extensions import db, jwt

    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    q(conn, "INSERT INTO roles (id, name, permission) VALUES (%s, 'PayAttDocsOnly', %s) ON CONFLICT (id) DO NOTHING",
      (DOCS_ONLY_ROLE, json.dumps({"clints": True, "clints_documents": True})))
    q(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
            "nationality_id, branch_id) SELECT %s, 'payatt.docs@test.invalid', 'Docs', 'Only', mobile, password, "
            "'payatt.docs', %s, nationality_id, branch_id FROM users WHERE id = %s ON CONFLICT (id) DO NOTHING",
      (DOCS_ONLY_USER, DOCS_ONLY_ROLE, M.col))
    conn.close()

    app = Flask("cawp_payatt_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.money
    import api.payment_attachments
    app.register_blueprint(api.money.bp)
    app.register_blueprint(api.payment_attachments.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    from core import files
    d = tmp_path / "documents"
    monkeypatch.setattr(files, "DOCUMENTS_DIR", str(d))
    return d


@pytest.fixture
def pc(pa_app, docs_dir):
    return PaClient(pa_app)


class PaClient(MoneyClient):
    def upload(self, pid, user, content=PDF, filename="receipt.pdf", name=None, key=None):
        data = {"file": (io.BytesIO(content), filename)}
        if name is not None:
            data["name"] = name
        return self.client.post(f"/v2/payments/{pid}/attachments", data=data,
                                content_type="multipart/form-data", headers=self.headers(user, key))

    def delete(self, url, user, key=None):
        return self.client.delete(url, headers=self.headers(user, key))


def stored_files(docs_dir):
    return sorted(os.listdir(docs_dir)) if docs_dir.exists() else []


def att_rows(db, pid):
    return q(db, "SELECT document_id::text, size_bytes, sha256, added_by::text, removed_at IS NOT NULL "
                 "FROM payment_attachments WHERE payment_id=%s ORDER BY id", (pid,))


# --------------------------------------------------------------------------- upload
def test_upload_stores_document_links_it_and_audits(pc, db, docs_dir):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    key = str(uuid.uuid4())
    r = pc.upload(pid, M.col, key=key)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    att = data["attachment"]
    assert att["file_type"] == "pdf" and att["size_bytes"] == len(PDF)
    assert att["name"] == "إيصال سداد 2026-09-21 100.00"          # Riyadh civil date of the payment
    assert att["original_name"] == "receipt.pdf"
    assert att["added_by"]["id"] == M.col
    assert att["download_url"] == f"/v2/payments/{pid}/attachments/{att['document_id']}/download"
    assert [i["document_id"] for i in data["items"]] == [att["document_id"]]

    doc = q1(db, "SELECT clint_id::text, file_name, original_name, display_name, file_type, uploaded_by::text, "
                 "is_deleted FROM clint_documents WHERE id=%s", (att["document_id"],))
    assert doc[0] == case and doc[2:] == ("receipt.pdf", att["name"], "pdf", M.col, False)
    assert stored_files(docs_dir) == [doc[1]]                      # <uuid>.pdf, the client-document layout
    assert (docs_dir / doc[1]).read_bytes() == PDF
    import hashlib
    assert att_rows(db, pid) == [(att["document_id"], len(PDF), hashlib.sha256(PDF).hexdigest(), M.col, False)]
    au = q1(db, "SELECT action, entity_type, actor_user_id::text, case_id::text, idempotency_key, after "
                "FROM audit_events WHERE entity_id=%s ORDER BY id DESC LIMIT 1", (pid,))
    assert au[:5] == ("payment.attachment.add", "payment", M.col, case, key)
    assert au[5]["document_id"] == att["document_id"] and au[5]["payment_status"] == "pending"

    # the same key again is a replay: no second file, row or link
    r2 = pc.upload(pid, M.col, key=key)
    assert r2.status_code == 201 and r2.headers.get("Idempotent-Replayed") == "true"
    assert len(att_rows(db, pid)) == 1 and len(stored_files(docs_dir)) == 1


def test_upload_custom_name_png_and_jpeg(pc, db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 50)
    r = pc.upload(pid, M.col, content=PNG, filename="slip.PNG", name="  bank slip  ")
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["attachment"]["name"] == "bank slip"
    assert r.get_json()["data"]["attachment"]["file_type"] == "png"
    r = pc.upload(pid, M.col, content=JPG, filename="photo.jpeg")
    assert r.status_code == 201, r.get_json()
    assert len(r.get_json()["data"]["items"]) == 2


def test_wrong_type_is_refused_by_content_and_extension(pc, db, docs_dir):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    r = pc.upload(pid, M.col, content=PNG, filename="receipt.pdf")         # PNG bytes named .pdf
    assert r.status_code == 415 and r.get_json()["code"] == "type_mismatch"
    r = pc.upload(pid, M.col, content=b"<html><script>x</script>", filename="receipt.jpg")
    assert r.status_code == 415 and r.get_json()["code"] == "type_mismatch"
    r = pc.upload(pid, M.col, content=b"hello", filename="notes.txt")
    assert r.status_code == 415 and r.get_json()["code"] == "unsupported_file_type"
    r = pc.client.post(f"/v2/payments/{pid}/attachments", data={}, content_type="multipart/form-data",
                       headers=pc.headers(M.col))
    assert r.status_code == 422 and r.get_json()["error"] == {"file": "required"}
    r = pc.upload(pid, M.col, content=b"", filename="empty.pdf")
    assert r.status_code == 422 and r.get_json()["code"] == "empty_file"
    assert stored_files(docs_dir) == [] and att_rows(db, pid) == []
    assert q1(db, "SELECT count(*) FROM clint_documents WHERE clint_id=%s", (case,))[0] == 0


def test_too_large_and_too_many(pc, db, docs_dir, monkeypatch):
    from services import payment_attachments as svc
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    monkeypatch.setattr(svc, "MAX_BYTES", 1000)
    r = pc.upload(pid, M.col, content=PDF + b"0" * 2000)
    assert r.status_code == 413 and r.get_json()["code"] == "file_too_large"
    assert stored_files(docs_dir) == [] and att_rows(db, pid) == []
    # a body far beyond the limit is refused before it is read
    monkeypatch.setattr(svc, "MAX_BYTES", 10)
    r = pc.upload(pid, M.col, content=PDF + b"0" * (300 * 1024))
    assert r.status_code == 413 and r.get_json()["code"] == "file_too_large"
    monkeypatch.setattr(svc, "MAX_BYTES", 10 * 1024 * 1024)

    monkeypatch.setattr(svc, "MAX_PER_PAYMENT", 2)
    assert pc.upload(pid, M.col).status_code == 201
    assert pc.upload(pid, M.col).status_code == 201
    r = pc.upload(pid, M.col)
    assert r.status_code == 409 and r.get_json()["code"] == "too_many_attachments"
    assert len(stored_files(docs_dir)) == 2


def test_deleted_reversed_and_locked(pc, db):
    case = make_case(db, 1000)
    deleted = make_payment(db, case, 100)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id=%s', (deleted,))
    r = pc.upload(deleted, M.col)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_deleted"

    reversed_ = make_payment(db, case, 100)
    ok = pc.post(f"/v2/payments/{reversed_}/approve", M.super,
                 {"expected_revision": revision(db, "Payments", reversed_)})
    assert ok.status_code == 200, ok.get_json()
    ok = pc.post(f"/v2/payments/{reversed_}/reverse", M.super,
                 {"expected_revision": revision(db, "Payments", reversed_), "reason": "recalled"})
    assert ok.status_code == 200, ok.get_json()
    r = pc.upload(reversed_, M.col)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_reversed"

    locked = make_case(db, 1000, locked=True)
    pid = make_payment(db, locked, 100)
    r = pc.upload(pid, M.col)
    assert r.status_code == 423 and r.get_json()["code"] == "case_locked"

    # an approved (or rejected) payment can still get its receipt
    approved = make_payment(db, case, 100, status=True, approver=M.super)
    assert pc.upload(approved, M.sup).status_code == 201


# --------------------------------------------------------------------------- scope / permissions
def test_permission_and_scope_matrix(pc, db):
    case = make_case(db, 1000)                      # owner m.col, "Money contract"
    pid = make_payment(db, case, 100)
    assert pc.upload(pid, M.col).status_code == 201
    doc = att_rows(db, pid)[0][0]
    urls = {
        "list": f"/v2/payments/{pid}/attachments",
        "download": f"/v2/payments/{pid}/attachments/{doc}/download",
        "case": f"/v2/cases/{case}/payment-attachments",
    }
    # no key at all / only the documents key -> 403 everywhere
    for user in (M.none, DOCS_ONLY_USER):
        assert pc.upload(pid, user).status_code == 403
        for url in urls.values():
            r = pc.get(url, user)
            assert r.status_code == 403 and r.get_json()["code"] == "forbidden", (user, url)
        assert pc.delete(f"/v2/payments/{pid}/attachments/{doc}", user).status_code == 403
    # a key but the case is outside the caller's scope -> 404, as if missing
    for user in (M.col2, M.approver):
        r = pc.upload(pid, user)
        assert r.status_code == 404 and r.get_json()["code"] == "payment_not_found", user
        for url in urls.values():
            assert pc.get(url, user).status_code == 404, (user, url)
        assert pc.delete(f"/v2/payments/{pid}/attachments/{doc}", user).status_code == 404
    # in scope: collector (owner), supervisor of the contract, super
    for user in (M.col, M.sup, M.super):
        for url in urls.values():
            assert pc.get(url, user).status_code == 200, (user, url)
    # an unknown payment is 404 too
    r = pc.upload(str(uuid.uuid4()), M.super)
    assert r.status_code == 404 and r.get_json()["code"] == "payment_not_found"
    assert len(att_rows(db, pid)) == 1


# --------------------------------------------------------------------------- reads
def test_list_download_and_case_map(pc, db):
    case = make_case(db, 1000)
    p1 = make_payment(db, case, 100)
    p2 = make_payment(db, case, 200)
    make_payment(db, case, 300)                     # no attachment
    a1 = pc.upload(p1, M.col).get_json()["data"]["attachment"]
    a2 = pc.upload(p2, M.col, content=PNG, filename="b.png").get_json()["data"]["attachment"]

    r = pc.get(f"/v2/payments/{p1}/attachments", M.col)
    d = r.get_json()["data"]
    assert d["payment_status"] == "pending" and d["max_attachments"] == 5
    assert [i["document_id"] for i in d["items"]] == [a1["document_id"]]

    r = pc.get(a1["download_url"], M.sup)
    assert r.status_code == 200
    assert r.data == PDF
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.headers["Cache-Control"] == "private, no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Content-Disposition"].startswith("inline")
    r = pc.get(a2["download_url"], M.col)
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png" and r.data == PNG

    # a document of the payment that is not linked / another payment's document -> 404
    r = pc.get(f"/v2/payments/{p1}/attachments/{a2['document_id']}/download", M.col)
    assert r.status_code == 404 and r.get_json()["code"] == "attachment_not_found"

    r = pc.get(f"/v2/cases/{case}/payment-attachments", M.col)
    m = r.get_json()["data"]["payments"]
    assert set(m) == {p1, p2}
    assert m[p2][0]["document_id"] == a2["document_id"]

    # deleted from the Documents tab -> no longer listed or downloadable
    q(db, "UPDATE clint_documents SET is_deleted = true WHERE id=%s", (a2["document_id"],))
    assert pc.get(f"/v2/payments/{p2}/attachments", M.col).get_json()["data"]["items"] == []
    assert pc.get(a2["download_url"], M.col).status_code == 404


def test_v2_payment_payloads_carry_attachments(pc, db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    bare = make_payment(db, case, 10)
    att = pc.upload(pid, M.col).get_json()["data"]["attachment"]

    p = pc.get(f"/v2/payments/{pid}", M.super).get_json()["data"]
    assert [a["document_id"] for a in p["attachments"]] == [att["document_id"]]
    assert p["attachments"][0]["download_url"] == att["download_url"]
    assert p["attachments"][0]["added_by"] == {"id": M.col, "name": "Money Collector"}

    items = pc.get(f"/v2/cases/{case}/payments", M.col).get_json()["data"]["items"]
    by_id = {i["id"]: i for i in items}
    assert len(by_id[pid]["attachments"]) == 1 and by_id[bare]["attachments"] == []

    desk = pc.get(f"/v2/payments?case_id={case}", M.super).get_json()["data"]["items"]
    assert {i["id"]: len(i["attachments"]) for i in desk} == {pid: 1, bare: 0}

    # the approve response's payment carries it too
    r = pc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": revision(db, "Payments", pid)})
    assert r.status_code == 200 and len(r.get_json()["data"]["payment"]["attachments"]) == 1


# --------------------------------------------------------------------------- removal
def test_remove_rules(pc, db, docs_dir):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    by_col = pc.upload(pid, M.col).get_json()["data"]["attachment"]
    by_sup = pc.upload(pid, M.sup).get_json()["data"]["attachment"]
    url = lambda a: f"/v2/payments/{pid}/attachments/{a['document_id']}"  # noqa: E731

    # the collector may not remove what someone else attached (no accounts_payments)
    r = pc.delete(url(by_sup), M.col)
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"
    # the uploader may
    key = str(uuid.uuid4())
    r = pc.delete(url(by_col), M.col, key=key)
    assert r.status_code == 200, r.get_json()
    assert [i["document_id"] for i in r.get_json()["data"]["items"]] == [by_sup["document_id"]]
    assert q1(db, "SELECT is_deleted FROM clint_documents WHERE id=%s", (by_col["document_id"],))[0] is True
    rows = {d: removed for d, _s, _h, _a, removed in att_rows(db, pid)}
    assert rows == {by_col["document_id"]: True, by_sup["document_id"]: False}
    au = q1(db, "SELECT action, actor_user_id::text, idempotency_key FROM audit_events WHERE entity_id=%s "
                "ORDER BY id DESC LIMIT 1", (pid,))
    assert au == ("payment.attachment.remove", M.col, key)
    # gone: 404 on a second removal and on download
    assert pc.delete(url(by_col), M.col).status_code == 404
    assert pc.get(by_col["download_url"], M.col).status_code == 404

    # once decided, nothing can be removed, not even by accounts
    r = pc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": revision(db, "Payments", pid)})
    assert r.status_code == 200
    r = pc.delete(url(by_sup), M.sup)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_not_pending"
    assert r.get_json()["error"] == {"status": "approved"}

    # accounts_payments may remove another person's attachment on a pending payment
    pid2 = make_payment(db, case, 50)
    a = pc.upload(pid2, M.col).get_json()["data"]["attachment"]
    r = pc.delete(f"/v2/payments/{pid2}/attachments/{a['document_id']}", M.sup)
    assert r.status_code == 200, r.get_json()

    # mutations need an Idempotency-Key
    r = pc.client.delete(url(by_sup), headers=pc.headers(M.sup, key=False))
    assert r.status_code == 400 and r.get_json()["code"] == "idempotency_key_required"
