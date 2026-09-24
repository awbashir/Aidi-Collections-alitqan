"""A minimal streaming XLSX (SpreadsheetML) writer with bounded memory.

Why not openpyxl's write-only mode: without `lxml` (not in this image), openpyxl falls back
to `et_xmlfile`, whose "incremental" writer keeps the whole `<sheetData>` element tree in
memory until the sheet is closed. Measured on cawp-db: 355 MB of Python heap and 32 s for
23,090 rows x 25 columns. This writer streams every row straight to a temporary file
(one XML string per row) and zips the parts at the end: constant memory, ~1 s for the same
export. The files open in Excel/LibreOffice and are read back with openpyxl in the tests.

Cells are typed:
    ("s", text)              inline string; never a formula (no <f> element is ever written)
    ("money", Decimal)       number, format #,##0.00 (the exact decimal text is written)
    ("int", int)             number, format 0
    ("date", date)           Excel date serial, format yyyy-mm-dd
    ("datetime", datetime)   naive local date-time serial, format yyyy-mm-dd hh:mm:ss
    None                     empty cell
Header rows use a bold font. Text is XML-escaped; a CR is written as `&#13;` (a literal CR
would be normalized to LF by the XML parser); control characters XML 1.0 cannot carry at all
are written as Excel's `_xHHHH_` escapes, and a literal `_xHHHH_` in the text is protected
with `_x005F_`, so Excel shows the exact text (openpyxl does not decode `_xHHHH_`).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import tempfile
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape as _xml_escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_EPOCH = _dt.datetime(1899, 12, 30)
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")     # not XML 1.0 characters
_ESCAPE_LIKE = re.compile(r"_x[0-9A-Fa-f]{4}_")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")

STYLE = {"text": 0, "money": 1, "date": 2, "datetime": 3, "header": 4, "int": 5}


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def text_xml(value: str) -> str:
    value = _ESCAPE_LIKE.sub(lambda m: "_x005F" + m.group(0), value)
    value = _BAD.sub(lambda m: "_x%04X_" % ord(m.group(0)), value)
    # a literal CR would be normalized to LF by every XML parser; a character reference is kept
    return _xml_escape(value).replace("\r", "&#13;")


def _serial(value) -> str:
    if isinstance(value, _dt.datetime):
        delta = value - _EPOCH
        return repr(delta.days + delta.seconds / 86400.0)
    return str((value - _EPOCH.date()).days)


class Sheet:
    def __init__(self, workbook, name: str, rtl: bool, freeze_header: bool):
        self.wb, self.name, self.rtl, self.freeze = workbook, name, rtl, freeze_header
        self.rows = 0
        fd, self.tmp = tempfile.mkstemp(prefix="xlsx-", suffix=".xml", dir=workbook.tmp_dir)
        self.f = os.fdopen(fd, "w", encoding="utf-8", newline="")
        view = '<sheetView workbookViewId="0"' + (' rightToLeft="1"' if rtl else "")
        if freeze_header:
            view += '><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' \
                    '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView>'
        else:
            view += "/>"
        self.f.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}" '
                     f'xmlns:r="{NS_R}"><sheetViews>{view}</sheetViews><sheetFormatPr defaultRowHeight="15"/>'
                     "<sheetData>")

    def append(self, cells, header: bool = False):
        self.rows += 1
        r = self.rows
        out = [f'<row r="{r}">']
        for i, cell in enumerate(cells, 1):
            if cell is None:
                continue
            kind, value = cell
            if value is None:
                continue
            ref = f"{col_letter(i)}{r}"
            if kind == "s":
                s = STYLE["header"] if header else STYLE["text"]
                out.append(f'<c r="{ref}" s="{s}" t="inlineStr"><is><t xml:space="preserve">'
                           f"{text_xml(str(value))}</t></is></c>")
            elif kind == "money":
                v = value if isinstance(value, Decimal) else Decimal(str(value))
                out.append(f'<c r="{ref}" s="{STYLE["money"]}"><v>{v}</v></c>')
            elif kind == "int":
                out.append(f'<c r="{ref}" s="{STYLE["int"]}"><v>{int(value)}</v></c>')
            elif kind in ("date", "datetime"):
                out.append(f'<c r="{ref}" s="{STYLE[kind]}"><v>{_serial(value)}</v></c>')
            else:
                raise ValueError(kind)
        out.append("</row>")
        self.f.write("".join(out))

    def close(self):
        if self.f is not None:
            self.f.write("</sheetData><pageMargins left=\"0.7\" right=\"0.7\" top=\"0.75\" bottom=\"0.75\" "
                         "header=\"0.3\" footer=\"0.3\"/></worksheet>")
            self.f.close()
            self.f = None


class Workbook:
    def __init__(self, path: str, rtl: bool = False):
        self.path, self.rtl = path, rtl
        self.tmp_dir = os.path.dirname(os.path.abspath(path)) or None
        self.sheets: list[Sheet] = []

    def add_sheet(self, name: str, freeze_header: bool = False) -> Sheet:
        name = _SHEET_BAD.sub(" ", name)[:31] or f"Sheet{len(self.sheets) + 1}"
        sh = Sheet(self, name, self.rtl, freeze_header)
        self.sheets.append(sh)
        return sh

    def _static(self) -> dict:
        n = len(self.sheets)
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, n + 1))
        quote = {'"': "&quot;"}
        sheets = "".join('<sheet name="' + _xml_escape(s.name, quote) + f'" sheetId="{i}" r:id="rId{i}"/>'
                         for i, s in enumerate(self.sheets, 1))
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="{NS_R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, n + 1))
        head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        return {
            "[Content_Types].xml": head + (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                f"{overrides}</Types>"),
            "_rels/.rels": head + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="xl/workbook.xml"/>'
                "</Relationships>"),
            "xl/workbook.xml": head + (
                f'<workbook xmlns="{NS}" xmlns:r="{NS_R}"><bookViews><workbookView activeTab="0"/></bookViews>'
                f"<sheets>{sheets}</sheets></workbook>"),
            "xl/_rels/workbook.xml.rels": head + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{rels}<Relationship Id="rId{n + 1}" Type="{NS_R}/styles" Target="styles.xml"/>'
                "</Relationships>"),
            "xl/styles.xml": head + (
                f'<styleSheet xmlns="{NS}">'
                '<numFmts count="3"><numFmt numFmtId="164" formatCode="#,##0.00"/>'
                '<numFmt numFmtId="165" formatCode="yyyy-mm-dd"/>'
                '<numFmt numFmtId="166" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="6">'
                '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                "</styleSheet>"),
        }

    def save(self):
        for s in self.sheets:
            s.close()
        try:
            with zipfile.ZipFile(self.path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for name, body in self._static().items():
                    z.writestr(name, body)
                for i, s in enumerate(self.sheets, 1):
                    z.write(s.tmp, f"xl/worksheets/sheet{i}.xml")      # streamed in chunks
        finally:
            self.cleanup()

    def cleanup(self):
        for s in self.sheets:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                os.remove(s.tmp)
            except OSError:
                pass
