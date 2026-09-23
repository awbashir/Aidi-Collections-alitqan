"""Upload inspection and streaming readers for XLSX / CSV sources.

Rules (docs/phase2-spec.md "Uploads"):

* Only a **real XLSX** (a zip with the OOXML workbook parts, no macros) and **UTF-8 CSV**
  are accepted. The extension must agree with the content: a text file named `.xlsx`, a
  zip named `.csv`, `.xls`, `.xlsm`, `.ods` ... are refused (415).
* A size limit applies while the upload is received (413), and a zip-bomb guard limits the
  total uncompressed size of a workbook.
* **Formula cells are counted and never evaluated.** Workbooks are opened with
  `read_only=True, data_only=False`: a formula cell yields its formula text (`=A1+1`), never a
  computed or cached value. For CSV, a cell starting with `=` is what a spreadsheet program
  would evaluate, so it is counted the same way. A formula in a *mapped* column makes that
  source row an error (`formula_not_allowed`); elsewhere the text is kept as raw data.
* Reading is streaming (openpyxl read-only / csv module), so memory stays bounded whatever
  the row count.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import os
import zipfile
from typing import Iterator

from core.bulk import settings as _settings

XLSX_EXTENSIONS = (".xlsx",)
CSV_EXTENSIONS = (".csv", ".txt")
ZIP_MAGIC = b"PK\x03\x04"
SAMPLE_ROWS = 5
SAMPLE_CELL_MAX = 200
HEADER_SEARCH_ROWS = 20
RAW_CELL_MAX = 2000
csv.field_size_limit(1024 * 1024)


class InspectionError(Exception):
    """Refused upload: `status` is the HTTP status (413/415/422), `code` stable."""

    def __init__(self, status: int, code: str, message: str, error=None):
        super().__init__(message)
        self.status, self.code, self.message, self.error = status, code, message, error


# --------------------------------------------------------------------------- receiving
def receive(stream, dest_path: str, max_bytes: int | None = None) -> tuple[int, str]:
    """Copy `stream` to `dest_path` in chunks, enforcing the size limit. -> (size, sha256)."""
    limit = max_bytes if max_bytes is not None else _settings.get().upload_max_bytes
    digest, size = hashlib.sha256(), 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise InspectionError(413, "file_too_large",
                                          f"the file is larger than {limit} bytes", {"max_bytes": limit})
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    if size == 0:
        os.remove(dest_path)
        raise InspectionError(422, "empty_file", "the file is empty")
    return size, digest.hexdigest()


# --------------------------------------------------------------------------- type detection
def detect_type(path: str, original_name: str) -> str:
    """'xlsx' or 'csv' from the CONTENT, which must agree with the extension."""
    ext = os.path.splitext(original_name or "")[1].lower()
    with open(path, "rb") as f:
        head = f.read(8)
    is_zip = head.startswith(ZIP_MAGIC)
    if ext in XLSX_EXTENSIONS:
        if not is_zip:
            raise InspectionError(415, "not_a_workbook",
                                  "the file is named .xlsx but is not an Excel workbook")
        _check_workbook_zip(path)
        return "xlsx"
    if ext in CSV_EXTENSIONS:
        if is_zip or head.startswith(b"\xd0\xcf\x11\xe0"):
            raise InspectionError(415, "type_mismatch", "the file is named .csv but is not a text file")
        return "csv"
    raise InspectionError(415, "unsupported_file_type",
                          "only .xlsx workbooks and UTF-8 .csv files are accepted",
                          {"extension": ext or None, "accepted": [".xlsx", ".csv"]})


def _check_workbook_zip(path: str) -> None:
    limit = _settings.get().xlsx_max_uncompressed_bytes
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise InspectionError(415, "not_a_workbook", "the file is not a readable Excel workbook") from None
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise InspectionError(415, "not_a_workbook", "the zip file is not an Excel workbook")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise InspectionError(415, "macro_not_allowed", "workbooks with macros are not accepted")
    if any(i.flag_bits & 0x1 for i in infos):
        raise InspectionError(415, "encrypted_workbook", "encrypted workbooks are not accepted")
    total = sum(i.file_size for i in infos)
    if total > limit:
        raise InspectionError(413, "workbook_too_large",
                              "the workbook expands beyond the allowed size", {"max_uncompressed_bytes": limit})


def check_utf8(path: str) -> str:
    """'utf-8-sig' when the file starts with a BOM, else 'utf-8'. 422 on NUL / invalid UTF-8."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    with open(path, "rb") as f:
        first = True
        bom = False
        while True:
            chunk = f.read(1024 * 1024)
            if first:
                bom = chunk.startswith(codecs.BOM_UTF8)
                first = False
            if not chunk:
                break
            if b"\x00" in chunk:
                raise InspectionError(415, "not_text", "the CSV file contains binary data")
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                raise InspectionError(422, "not_utf8", "the CSV file must be UTF-8 encoded") from None
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise InspectionError(422, "not_utf8", "the CSV file must be UTF-8 encoded") from None
    return "utf-8-sig" if bom else "utf-8"


def sniff_delimiter(path: str, encoding: str) -> str:
    with open(path, "r", encoding=encoding, newline="") as f:
        sample = f.read(64 * 1024)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# --------------------------------------------------------------------------- cells
def cell_text(value) -> str | None:
    """Text of a cell as the user sees it (integral floats lose '.0'); None for empty."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        try:
            as_int = int(value)
        except (ValueError, OverflowError):
            return str(value)
        return str(as_int) if as_int == value else repr(value)
    if hasattr(value, "text") and not isinstance(value, str):     # openpyxl ArrayFormula
        return str(value.text)
    text = str(value)
    return text if text != "" else None


def is_formula_text(text: str | None) -> bool:
    return bool(text) and text.startswith("=") and len(text) > 1


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out, n = "", index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def normalise_headers(values: list, width: int) -> list[str]:
    """Header texts, trimmed; blank -> "Column C"; repeated -> "Name (2)"."""
    headers, used = [], set()
    for i in range(width):
        v = values[i] if i < len(values) else None
        base = (cell_text(v) or "").strip() or f"Column {column_letter(i)}"
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base} ({n})"
        used.add(name)
        headers.append(name)
    return headers


# --------------------------------------------------------------------------- streaming readers
def iter_raw_rows(path: str, detected_type: str, sheet: str | None = None, *,
                  encoding: str | None = None, delimiter: str | None = None,
                  max_columns: int | None = None) -> Iterator[tuple[int, list[str | None], set[int]]]:
    """Yield (row_no, [cell text], {formula column indexes}) for every row, blank rows too.
    row_no is the sheet row (XLSX) or the CSV record number, 1-based."""
    max_columns = max_columns or _settings.get().max_columns
    if detected_type == "xlsx":
        import openpyxl
        fh = open(path, "rb")          # a file object: openpyxl would refuse the .part temp name
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=False)
        try:
            ws = wb[sheet] if sheet is not None else wb.worksheets[0]
            ws.reset_dimensions()      # never trust the stored dimension: read every cell
            for row_no, cells in enumerate(ws.iter_rows(min_row=1), start=1):
                values, formulas = [], set()
                for i, c in enumerate(cells[:max_columns]):
                    if c.data_type == "f":
                        formulas.add(i)
                    values.append(cell_text(c.value))
                yield row_no, values, formulas
        finally:
            wb.close()
            fh.close()
        return
    with open(path, "r", encoding=encoding or "utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter or ",")
        for row_no, record in enumerate(reader, start=1):
            values, formulas = [], set()
            for i, v in enumerate(record[:max_columns]):
                t = v if v != "" else None
                if is_formula_text(t):
                    formulas.add(i)
                values.append(t)
            yield row_no, values, formulas


def sheet_names(path: str, detected_type: str) -> list[str]:
    if detected_type != "xlsx":
        return ["csv"]
    import openpyxl
    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=False)
        try:
            return [ws.title for ws in wb.worksheets]
        finally:
            wb.close()


def iter_data_rows(path: str, detected_type: str, sheet: str | None, header_row: int, headers: list[str], *,
                   encoding=None, delimiter=None) -> Iterator[tuple[int, dict, set[str]]]:
    """Data rows after `header_row` as (row_no, {header: text}, {formula headers}); blank rows
    are skipped. Values are truncated to RAW_CELL_MAX characters."""
    width = len(headers)
    xl_sheet = sheet if detected_type == "xlsx" else None
    for row_no, values, formulas in iter_raw_rows(path, detected_type, xl_sheet, encoding=encoding,
                                                  delimiter=delimiter, max_columns=width):
        if row_no <= header_row:
            continue
        if not any(v is not None and v.strip() != "" for v in values):
            continue
        row = {}
        for i in range(width):
            v = values[i] if i < len(values) else None
            row[headers[i]] = v[:RAW_CELL_MAX] if isinstance(v, str) else v
        yield row_no, row, {headers[i] for i in formulas if i < width}


# --------------------------------------------------------------------------- inspection
def inspect_file(path: str, detected_type: str) -> dict:
    """Full streaming pass: sheets, header row, headers, data rows, formula cells, sample."""
    s = _settings.get()
    encoding = delimiter = None
    if detected_type == "csv":
        encoding = check_utf8(path)
        delimiter = sniff_delimiter(path, encoding)
    names = sheet_names(path, detected_type)
    sheets, total_formulas = [], 0
    for name in names:
        info = _inspect_sheet(path, detected_type, None if detected_type == "csv" else name,
                              encoding, delimiter, s.max_rows, s.max_columns)
        info["name"] = name
        total_formulas += info["formula_cells"]
        sheets.append(info)
    first = next((sh for sh in sheets if sh["rows"] > 0), sheets[0] if sheets else None)
    if first is None or not first["headers"]:
        raise InspectionError(422, "no_data", "the file has no header row")
    return {
        "detected_type": detected_type, "encoding": encoding, "delimiter": delimiter,
        "sheets": sheets, "row_count": first["rows"], "header_row": first["header_row"],
        "formula_cells": total_formulas, "default_sheet": first["name"],
    }


def _clip(v):
    return v[:SAMPLE_CELL_MAX] if isinstance(v, str) else v


def _inspect_sheet(path, detected_type, sheet, encoding, delimiter, max_rows, max_columns) -> dict:
    header_row, headers, width = None, [], 0
    rows = formulas = 0
    widest = 0
    sample = []
    complete = True
    for row_no, values, formula_cols in iter_raw_rows(path, detected_type, sheet, encoding=encoding,
                                                      delimiter=delimiter, max_columns=max_columns):
        formulas += len(formula_cols)
        nonblank = any(v is not None and v.strip() != "" for v in values)
        if header_row is None:
            if nonblank:
                last = max(i for i, v in enumerate(values) if v is not None and v.strip() != "")
                width = last + 1
                header_row, headers = row_no, normalise_headers(values, width)
            elif row_no >= HEADER_SEARCH_ROWS:
                break
            continue
        if not nonblank:
            continue
        rows += 1
        used = [i for i, v in enumerate(values) if v is not None and v.strip() != ""]
        widest = max(widest, (used[-1] + 1) if used else 0)
        if len(sample) < SAMPLE_ROWS:
            sample.append([_clip(values[i]) if i < len(values) else None for i in range(width)])
        if rows > max_rows:
            complete = False
            break
    return {"header_row": header_row or 1, "headers": headers, "columns": width,
            "unnamed_data_columns": max(0, widest - width), "rows": rows,
            "formula_cells": formulas, "sample": sample, "complete": complete}
