"""Workbook reading, done once.

Two things used to be repeated all over `app.py`:

* the four upload routes called ``openpyxl.load_workbook(file)`` **twice** per request
  (review P9) - once to validate the header row, once to read the data;
* seven call sites read a single column with pandas
  (``ps.read_excel(path, dtype=str).iloc[:, idx].astype(str).values.tolist()`` -
  review section 5, "the pandas Excel-column read appears 7x"), which spins up a whole
  DataFrame, a second parser stack and ~10x the memory of the one column it wants.

`read_sheet` loads the workbook once in read-only/values-only mode.
`column_values` reproduces the pandas result **exactly** - see `_cell_str` for the
string-coercion rules, which matter because the values are matched against
``clints."accountNumber"`` (a text column) and a ``'1234.0'`` would never match.
"""
import openpyxl

__all__ = ["read_sheet", "column_values", "rows_as_dicts", "cell_str"]


def _trim_trailing_blank(rows):
    """pandas drops trailing rows whose cells are all empty; interior blanks are kept.

    (`OpenpyxlReader._convert_cell` maps None -> "" and `_parse_sheet` then pops trailing
    `[""] * width` rows.)  Reproduced here so `column_values` returns the same length.
    """
    while rows and all(c is None for c in rows[-1]):
        rows.pop()
    return rows


def read_sheet(path_or_file, sheet=None, trim_blank_rows=True):
    """Load a workbook **once** and return ``(headers, rows)``.

    headers : list of the first row's cell values (may contain None - the upload routes
              are the ones that reject that).
    rows    : list of lists, one per data row, raw openpyxl values (int / float / str /
              datetime / bool / None).
    """
    wb = openpyxl.load_workbook(path_or_file, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet is not None else wb.active
        it = ws.iter_rows(values_only=True)
        headers = list(next(it, ()) or ())
        rows = [list(r) for r in it]
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001 - read_only workbooks hold a zip handle
            pass
    if trim_blank_rows:
        _trim_trailing_blank(rows)
    return headers, rows


def rows_as_dicts(headers, rows):
    """`{header: value}` per row - the shape `getdataasdict()` produced in app.py."""
    keys = list(headers)
    return [dict(zip(keys, row)) for row in rows]


def cell_str(value):
    """`str()` of a cell the way ``read_excel(dtype=str).astype(str)`` renders it.

    Verified against pandas 2.1.0 / openpyxl 3.1.2 inside the API container:

    ==========================  ==================  ==========
    cell                        openpyxl value      result
    ==========================  ==================  ==========
    1220401916                  int 1220401916      '1220401916'
    1220401916.0 (saved)        int 1220401916      '1220401916'   <- no '.0'
    1234.5                      float 1234.5        '1234.5'
    1e20                        float 1e+20         '100000000000000000000'
    '1220401917'                str                 '1220401917'
    '  102 '                    str (kept)          '  102 '       <- not stripped
    empty / None                None                'nan'          <- the literal string
    2020-01-02 03:04:05         datetime            '2020-01-02 03:04:05'
    TRUE                        bool                'True'
    ==========================  ==================  ==========

    Two rules are load-bearing:

    * integral floats lose the ``.0``.  pandas' `OpenpyxlReader._convert_cell` does
      ``val = int(cell.value); return val if val == cell.value else float(cell.value)``,
      so a numeric account number never reaches the database query as ``'1220401916.0'``
      (which would match no row in the text column ``clints."accountNumber"``).
    * empty cells become the literal string ``'nan'``.  That is not a quirk we chose:
      ``astype(str)`` on a column holding NaN yields ``'nan'``, and the legacy code fed
      exactly that into ``Clints.query.filter_by(accountNumber='nan')``.
    """
    if value is None:
        return "nan"
    if isinstance(value, bool):          # bool is an int subclass - test it first
        return str(value)
    if isinstance(value, float):
        try:
            as_int = int(value)
        except (ValueError, OverflowError):   # nan / inf
            return str(value)
        if as_int == value:
            return str(as_int)
    return str(value)


def column_values(path_or_file, index, sheet=None):
    """Replacement for ``ps.read_excel(p, dtype=str).iloc[:, i].astype(str).values.tolist()``.

    Returns the values of column `index` for every data row (header row excluded),
    as strings, with trailing blank rows dropped - byte-identical to the pandas call
    it replaces, at a fraction of the cost.
    """
    idx = int(index)
    _headers, rows = read_sheet(path_or_file, sheet=sheet)
    return [cell_str(row[idx]) if idx < len(row) else "nan" for row in rows]
