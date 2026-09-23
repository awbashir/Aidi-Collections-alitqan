"""Uploads: real XLSX / UTF-8 CSV only, size limit, formula cells counted and never evaluated,
private storage, inspection result, permissions, idempotent replay."""
import io
import os
import zipfile

import pytest

from test_bulk_framework_support import *  # noqa: F401,F403


def test_xlsx_upload_is_inspected_and_stored_privately(bc, db, bulk_storage):
    content = make_xlsx([["Account No", "Name", None], ["A-1", "x", None], [1220401916.0, "y", None],
                         [None, None, None], ["A-3", "z", None]],
                        extra_sheets={"Other": [[], ["h1", "h2"], ["v", "w"]]})
    r = bc.upload(B.col1, content, "accounts.xlsx")
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["data"]
    assert up["detected_type"] == "xlsx" and up["purpose"] == "lock"
    assert up["sha256"] == sha(content) and up["size_bytes"] == len(content)
    assert up["scan_status"] == "not_scanned"
    assert any(w["code"] == "not_scanned" for w in up["warnings"])
    first, other = up["sheets"]
    assert first["name"] == "Sheet1" and first["headers"] == ["Account No", "Name"]
    assert first["rows"] == 3 and first["header_row"] == 1          # the blank row is not a data row
    assert first["sample"][1] == ["1220401916", "y"]                # integral float loses ".0"
    assert other["header_row"] == 2 and other["headers"] == ["h1", "h2"] and other["rows"] == 1
    assert up["row_count"] == 3 and up["formula_cells"] == 0
    stored = q1(db, "SELECT stored_path, uploaded_by::text, expires_at > uploaded_at + interval '6 days' "
                    "FROM bulk_uploads WHERE id = %s", (up["id"],))
    assert stored[1] == B.col1 and stored[2] is True
    path = os.path.join(str(bulk_storage), stored[0])
    assert stored[0].startswith("uploads/") and os.path.isfile(path)
    assert open(path, "rb").read() == content
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    # the uploader can read it back; another user cannot (404, no existence leak)
    assert bc.get(f"/v2/uploads/{up['id']}", B.col1).status_code == 200
    assert bc.get(f"/v2/uploads/{up['id']}", B.col2).status_code == 404
    audit = q1(db, "SELECT action, actor_user_id::text FROM audit_events WHERE entity_type = 'bulk_upload' "
                   "AND entity_id = %s", (up["id"],))
    assert audit == ("bulk_upload.create", B.col1)


def test_csv_upload_utf8_with_bom_and_semicolons(bc):
    content = make_csv([["رقم الحساب", "ملاحظة"], ["A-1", "مرحبا"], ["A-2", "=1+2"]], delimiter=";", bom=True)
    r = bc.upload(B.col1, content, "accounts.csv")
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["data"]
    assert up["detected_type"] == "csv" and up["encoding"] == "utf-8-sig" and up["delimiter"] == ";"
    assert up["sheets"][0]["headers"] == ["رقم الحساب", "ملاحظة"] and up["row_count"] == 2
    assert up["formula_cells"] == 1                                 # "=1+2" would be a formula in Excel


def test_fake_xlsx_and_other_types_are_rejected(bc, db, bulk_storage):
    before = q1(db, "SELECT count(*) FROM bulk_uploads")[0]
    r = bc.upload(B.col1, b"Account No\nA-1\n", "fake.xlsx")
    assert r.status_code == 415 and r.get_json()["code"] == "not_a_workbook"
    # a zip that is not a workbook
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    r = bc.upload(B.col1, buf.getvalue(), "zip.xlsx")
    assert r.status_code == 415 and r.get_json()["code"] == "not_a_workbook"
    # a workbook named .csv, a legacy .xls, a macro workbook
    xlsx = make_xlsx([["a"], ["b"]])
    assert bc.upload(B.col1, xlsx, "sheet.csv").get_json()["code"] == "type_mismatch"
    assert bc.upload(B.col1, xlsx, "sheet.xls").get_json()["code"] == "unsupported_file_type"
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("xl/vbaProject.bin", b"\x00macro")
    r = bc.upload(B.col1, buf.getvalue(), "macro.xlsx")
    assert r.status_code == 415 and r.get_json()["code"] == "macro_not_allowed"
    # not UTF-8, binary
    r = bc.upload(B.col1, "Account;Name\nA-1;café\n".encode("latin-1"), "latin.csv")
    assert r.status_code == 422 and r.get_json()["code"] == "not_utf8"
    r = bc.upload(B.col1, b"a,b\n\x00\x01,2\n", "bin.csv")
    assert r.status_code == 415 and r.get_json()["code"] == "not_text"
    assert bc.upload(B.col1, b"", "empty.csv").get_json()["code"] == "empty_file"
    # nothing was recorded or left behind
    assert q1(db, "SELECT count(*) FROM bulk_uploads")[0] == before
    tmp = os.path.join(str(bulk_storage), "tmp")
    assert not os.path.isdir(tmp) or os.listdir(tmp) == []


def test_formula_cells_are_counted_and_never_evaluated(bc, db, bulk_storage):
    cid, acc = make_case(db)
    content = make_xlsx([["Account No", "Note"], [acc, "=HYPERLINK(\"http://evil\",\"x\")"],
                         ["=A2", "plain"], [acc + "-other", "=1+1"]])
    r = bc.upload(B.col1, content, "formulas.xlsx")
    up = r.get_json()["data"]
    assert r.status_code == 201 and up["formula_cells"] == 3
    assert any(w["code"] == "formula_cells" and w["count"] == 3 for w in up["warnings"])
    assert up["sheets"][0]["sample"][1][0] == "=A2"                   # the formula text, not a value
    job = bc.create(B.col1, "lock", {"source": "spreadsheet", "upload_id": up["id"],
                                     "mapping": {"account_number": "Account No"}, "params": {"reason": "r"}})
    assert run(bc.app, job["id"]) == "ready"
    rows = {r["row_no"]: r for r in rows_of(db, job["id"])}
    assert rows[2]["eligibility"] == "eligible"                        # formula only in an unmapped column
    assert rows[2]["raw"]["Note"] == '=HYPERLINK("http://evil","x")'  # kept as text
    assert rows[3]["eligibility"] == "error" and rows[3]["eligibility_code"] == "formula_not_allowed"
    assert rows[3]["mapped"]["account_number"] == "=A2" and rows[3]["case_id"] is None
    assert rows[4]["eligibility"] == "error" and rows[4]["eligibility_code"] == "not_found_or_not_permitted"


def test_size_limit(bc, monkeypatch):
    monkeypatch.setenv("BULK_UPLOAD_MAX_BYTES", "200")
    r = bc.upload(B.col1, make_csv([["Account No"]] + [[f"A-{i:05d}"] for i in range(50)]), "big.csv")
    assert r.status_code == 413 and r.get_json()["code"] == "file_too_large"
    assert r.get_json()["error"] == {"max_bytes": 200}


def test_zip_bomb_guard(bc, monkeypatch):
    monkeypatch.setenv("BULK_XLSX_MAX_UNCOMPRESSED_MB", "1")
    rows = [["Account No", "Pad"]] + [[f"A-{i}", f"{i}-" + "x" * 200] for i in range(8000)]
    r = bc.upload(B.col1, make_xlsx(rows), "wide.xlsx")
    assert r.status_code == 413 and r.get_json()["code"] == "workbook_too_large"


def test_upload_permissions_and_purpose(bc):
    content = make_csv([["Account No"], ["A-1"]])
    r = bc.upload(B.none, content, "a.csv")
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"
    assert bc.upload(B.none, content, "a.csv", purpose="bulk").status_code == 403
    r = bc.upload(B.col1, content, "a.csv", purpose="no_such_kind")
    assert r.status_code == 422 and "lock" in r.get_json()["error"]["allowed"]
    assert bc.upload(B.col1, content, "a.csv", purpose="bulk").status_code == 201
    r = bc.client.post("/v2/uploads", data={"purpose": "lock"}, content_type="multipart/form-data",
                       headers=bc.headers(B.col1))
    assert r.status_code == 422 and r.get_json()["error"] == {"file": "required"}


def test_upload_idempotent_replay(bc, db):
    content = make_csv([["Account No"], ["A-1"]])
    before = q1(db, "SELECT count(*) FROM bulk_uploads")[0]
    r1 = bc.upload(B.col1, content, "same.csv", key="upload-replay-1")
    r2 = bc.upload(B.col1, content, "same.csv", key="upload-replay-1")
    assert r1.status_code == r2.status_code == 201
    assert r2.headers.get("Idempotent-Replayed") == "true" and r1.data == r2.data
    assert q1(db, "SELECT count(*) FROM bulk_uploads")[0] == before + 1
    r3 = bc.upload(B.col1, content + b"A-2\r\n", "same.csv", key="upload-replay-1")
    assert r3.status_code == 422 and r3.get_json()["code"] == "idempotency_key_reused"
    assert bc.client.post("/v2/uploads", data={"file": (io.BytesIO(content), "x.csv")},
                          headers=bc.headers(B.col1, key=False)).status_code == 400


@pytest.mark.no_db
def test_inspect_helpers():
    from core.bulk import inspect
    assert inspect.cell_text(3.0) == "3" and inspect.cell_text(3.5) == "3.5" and inspect.cell_text(True) == "TRUE"
    assert inspect.cell_text("") is None and inspect.cell_text(None) is None
    assert inspect.normalise_headers(["a", None, "a", " a ", "b"], 6) == \
        ["a", "Column B", "a (2)", "a (3)", "b", "Column F"]
    assert [inspect.column_letter(i) for i in (0, 25, 26, 701, 702)] == ["A", "Z", "AA", "ZZ", "AAA"]
    assert inspect.is_formula_text("=1") and not inspect.is_formula_text("=") and not inspect.is_formula_text("a=1")
