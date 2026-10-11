"""Streaming export writers: CSV, XLSX and the Arabic RTL XLSX. Bounded memory: rows arrive
in batches (500) from a server-side cursor and are written straight to the file (XLSX: each
sheet streams to its own temporary XML file, inline strings, no shared-string table).

CSV (decision, documented in review/phase4-framework.md):
* UTF-8 **with a BOM** and CRLF line ends, like the Phase 2 bulk artifacts: the legacy
  users open exports in Excel, which shows Arabic text as mojibake without the BOM.
* Header = the column keys (stable, English, the same keys as the JSON preview rows).
* Values = exactly the preview's JSON values: money as exact decimal strings ("1234.50"),
  dates YYYY-MM-DD, instants ISO 8601 UTC with `Z`, enums as their codes, blank for null.
* Formula-safe: a text/enum cell starting with = + - @ TAB or CR gets a leading `'`
  (`core.bulk.artifacts.safe_cell`). Generated columns (money, dates, ids, numbers) are
  never escaped, so a value is never altered.

XLSX / Arabic XLSX (streaming writer, core/reports/xlsx.py; openpyxl's write-only mode is
not bounded without lxml, see that module):
* sheet 1 = the rows (header labels in English / Arabic, frozen header row); sheet 2 = the
  summary (report, generation cutoff, requester, filters, sort, row count, exact totals).
* Text cells are written as typed inline strings (never formulas, so `=1+1` stays text).
* Money = numbers with 2 decimals (`#,##0.00`); civil dates = Excel dates; instants =
  Asia/Riyadh local date-times (Excel has no time zone; the summary says so).
* Arabic variant: `rightToLeft` sheet views, Arabic headers, Arabic enum/boolean values.
"""
from __future__ import annotations

import csv
import datetime as _dt
from decimal import Decimal

from core import riyadh
from core.bulk.artifacts import safe_cell
from core.reports.definition import json_value

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsx_ar": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
EXTENSIONS = {"csv": "csv", "xlsx": "xlsx", "xlsx_ar": "xlsx"}
TEXTUAL = ("text", "enum", "json")

_AR_BOOL = {True: "نعم", False: "لا"}
_EN_BOOL = {True: "Yes", False: "No"}


def open_writer(fmt: str, path: str, report, columns, meta: dict):
    if fmt == "csv":
        return CsvWriter(path, report, columns, meta)
    if fmt in ("xlsx", "xlsx_ar"):
        return XlsxWriter(path, report, columns, meta, arabic=(fmt == "xlsx_ar"))
    raise ValueError(fmt)


class CsvWriter:
    def __init__(self, path, report, columns, meta):
        self.columns = columns
        self.f = open(path, "w", encoding="utf-8-sig", newline="")
        self.w = csv.writer(self.f, lineterminator="\r\n")
        self.w.writerow([c.key for c in columns])

    def cell(self, col, value) -> str:
        v = json_value(col, value)
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if col.type in TEXTUAL:
            return safe_cell(v)
        return str(v)

    def write_rows(self, rows):
        for values in rows:
            self.w.writerow([self.cell(c, v) for c, v in zip(self.columns, values)])

    def close(self, summary: dict):
        self.f.close()

    def abort(self):
        try:
            self.f.close()
        except Exception:  # noqa: BLE001
            pass


class XlsxWriter:
    """XLSX / Arabic RTL XLSX through the streaming writer in core/reports/xlsx.py."""

    def __init__(self, path, report, columns, meta, arabic: bool):
        from core.reports.xlsx import Workbook
        self.path, self.report, self.columns, self.meta, self.arabic = path, report, columns, meta, arabic
        self.lang = "ar" if arabic else "en"
        self.wb = Workbook(path, rtl=arabic)
        title = (report.sheet_title_ar or "البيانات") if arabic else "Report"
        self.ws = self.wb.add_sheet(title, freeze_header=True)
        self.ws.append([("s", c.labels[self.lang]) for c in columns], header=True)

    def cell(self, col, value):
        if value is None:
            return None
        t = col.type
        if t == "money":
            return ("money", value if isinstance(value, Decimal) else Decimal(str(value)))
        if t == "int":
            return ("int", int(value))
        if t == "date":
            return ("date", value if isinstance(value, _dt.date) else riyadh.parse_date(value))
        if t == "datetime":
            return ("datetime", riyadh.to_riyadh(value).replace(tzinfo=None, microsecond=0))
        if t == "bool":
            return ("s", (_AR_BOOL if self.arabic else _EN_BOOL)[bool(value)])
        if t == "enum" and col.enum and str(value) in col.enum:
            return ("s", col.enum[str(value)][self.lang])
        return ("s", json_value(col, value))

    def write_rows(self, rows):
        for values in rows:
            self.ws.append([self.cell(c, v) for c, v in zip(self.columns, values)])

    def close(self, summary: dict):
        ar = self.arabic
        L = (lambda en, a: a) if ar else (lambda en, a: en)
        s = self.wb.add_sheet("الملخص" if ar else "Summary")

        def local(ts):
            return ("datetime", riyadh.to_riyadh(ts).replace(tzinfo=None, microsecond=0))

        def row(k, v):
            if v is None:
                return
            s.append([("s", k), v if isinstance(v, tuple) else ("s", str(v))])

        title = self.report.label_ar if ar else self.report.label_en
        row(L("Report", "التقرير"), f"{self.report.id} — {title}")
        if ar:
            row("الجهة", "برنامج التحصيل الذكي")
        if summary.get("cutoff") is not None:
            row(L("Data as of (Asia/Riyadh)", "البيانات حتى (توقيت الرياض)"), local(summary["cutoff"]))
        if self.meta.get("requested_at") is not None:
            row(L("Requested at (Asia/Riyadh)", "وقت الطلب (توقيت الرياض)"), local(self.meta["requested_at"]))
        row(L("Requested by", "طلب بواسطة"), self.meta.get("requested_by_name") or self.meta.get("requested_by"))
        row(L("Export id", "رقم التصدير"), self.meta.get("export_id"))
        row(L("Times", "الأوقات"), L("date-times are Asia/Riyadh local time", "التواريخ والأوقات بتوقيت الرياض"))
        row(L("Sort", "الترتيب"), summary.get("sort"))
        for f in self.report.filters:
            value = (summary.get("filters") or {}).get(f.key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = " → ".join(str(value.get(k) or "…") for k in ("from", "to"))
            elif isinstance(value, list):
                sep = "، " if ar else ", "
                if getattr(f, "choices", None):
                    value = sep.join(f.choices[v][self.lang] for v in value)
                else:
                    value = sep.join(value)
            row(f.labels[self.lang], str(value))
        row(L("Rows", "عدد السجلات"), ("int", int(summary.get("row_count") or 0)))
        totals = summary.get("totals") or {}
        for t in self.report.totals:
            v = totals.get(t.key)
            label_ = t.label_ar if ar else t.label_en
            if v is None:
                if getattr(t, "nullable", False):       # unknown, shown as such (never 0)
                    row(label_, L("Unknown", "غير معروف"))
                continue
            row(label_, ("money", Decimal(str(v))) if t.type == "money" else ("int", int(v)))
        self.wb.save()

    def abort(self):
        self.wb.cleanup()
