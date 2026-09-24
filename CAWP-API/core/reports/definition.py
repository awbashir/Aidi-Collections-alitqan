"""The report definition interface (Phase 4, stream R0).

A report is a `ReportDefinition` instance registered by a `services/reports_v2_*.py` module
(`REPORTS = (MyReport(), ...)`, discovered by `core.reports.registry`). The framework does
everything else: filter normalization and validation, the preview (page + exact totals in
one REPEATABLE READ snapshot), scoped selector options, and the private exports (CSV, XLSX,
Arabic RTL XLSX) run by the worker. See review/phase4-framework.md for a worked example.

SQL conventions
---------------
* `build(ctx)` returns a `Built(from_sql, where, params)`: the FROM clause (joins included)
  and the base predicates, which MUST contain the caller's current case scope
  (`ctx.scope_sql("c")`) and the report's fixed rules (e.g. approved only). Filters are
  added by the framework from their own `sql()`; they only ever narrow the result.
* Unused LEFT JOINs to unique keys are removed by the PostgreSQL planner, so one FROM
  clause serves the totals, the page and the export.
* Bind parameters are named (`:name`). Framework parameters start with `f_`, `rq_` or `sc_`;
  use another prefix in a definition.
* Money is `numeric` rounded to 2 places in SQL (`round(x::numeric, 2)`); it is a string in
  JSON (`"1234.50"`), an exact decimal string in CSV and a 2-decimal number in XLSX.
* Every sort gets the definition's `id_sql` as the last key (same direction): stable paging.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from core import riyadh

COLUMN_TYPES = ("text", "int", "money", "date", "datetime", "enum", "bool", "uuid", "json")
TOTAL_TYPES = ("int", "money")
MAX_TEXT = 100
MAX_IDS = 200
MONEY_MAX = Decimal("999999999999.99")
CENT = Decimal("0.01")


class FieldError(Exception):
    """A filter value is invalid; `errors` = {param name: code}."""

    def __init__(self, errors: dict):
        super().__init__(errors)
        self.errors = errors


def label(en: str, ar: str) -> dict:
    return {"en": en, "ar": ar}


# =========================================================================== results of build()
@dataclasses.dataclass
class Built:
    from_sql: str
    where: list
    params: dict


# =========================================================================== argument access
class Args:
    """Uniform read access to filter parameters from a query string (MultiDict) or a JSON
    object (the export body). A list value may be repeated params or one comma list."""

    def __init__(self, source):
        self.source = source or {}

    def keys(self):
        return list(self.source.keys())

    def get(self, name):
        src = self.source
        if hasattr(src, "getlist"):
            vals = src.getlist(name)
            if not vals:
                return None
            return vals[-1] if len(vals) == 1 else vals
        return src.get(name)

    def text(self, name):
        v = self.get(name)
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            v = v[-1] if v else None
            if v is None:
                return None
        if isinstance(v, bool) or not isinstance(v, (str, int, float, Decimal)):
            raise FieldError({name: "invalid"})
        s = str(v).strip()
        return s or None

    def many(self, name) -> list[str]:
        v = self.get(name)
        if v is None:
            return []
        items = v if isinstance(v, (list, tuple)) else [v]
        out = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise FieldError({name: "invalid"})
            out.extend(p.strip() for p in str(item).split(",") if p.strip())
        return list(dict.fromkeys(out))


# =========================================================================== filters
class FilterSpec:
    """Base class. Subclasses define `type`, `params()`, `normalize()`, `sql()`, `describe()`.

    `key` is the filter's name in the normalized definition; `params()` are its query
    parameter names. `normalize()` returns the canonical value or None (not set)."""

    type = "base"

    def __init__(self, key: str, label_en: str, label_ar: str, *, required: bool = False,
                 help_en: str | None = None, help_ar: str | None = None):
        self.key = key
        self.labels = label(label_en, label_ar)
        self.required = required
        self.help = label(help_en, help_ar) if help_en or help_ar else None

    def params(self) -> tuple[str, ...]:
        return (self.key,)

    def normalize(self, args: Args):
        raise NotImplementedError

    def sql(self, value, n: int) -> tuple[str, dict]:
        raise NotImplementedError

    def to_params(self, value) -> dict:
        """Normalized value -> flat query parameters (what the rows endpoint accepts)."""
        return {} if value is None else {self.key: value}

    def describe(self) -> dict:
        return {"key": self.key, "type": self.type, "labels": self.labels, "params": list(self.params()),
                "required": self.required, "help": self.help}


class DateRange(FilterSpec):
    """Inclusive civil dates `<key>_from` / `<key>_to` (YYYY-MM-DD, Asia/Riyadh).

    `column_type="timestamptz"`: the column stores the business date as an instant (the
    legacy `payment_date` / promise dates); the range becomes the half-open UTC range from
    Riyadh midnight of `from` to Riyadh midnight after `to` (`core.riyadh.range_bounds`), so
    the btree index stays usable and the last day is fully included. `column_type="date"`:
    `from <= col <= to`."""

    type = "date_range"

    def __init__(self, key, label_en, label_ar, column: str, *, column_type: str = "timestamptz",
                 max_days: int | None = None, **kw):
        super().__init__(key, label_en, label_ar, **kw)
        if column_type not in ("timestamptz", "date"):
            raise ValueError(column_type)
        self.column, self.column_type, self.max_days = column, column_type, max_days

    def params(self):
        return (f"{self.key}_from", f"{self.key}_to")

    def normalize(self, args):
        p_from, p_to = self.params()
        errors, out = {}, {}
        for p, k in ((p_from, "from"), (p_to, "to")):
            raw = args.text(p)
            if raw is None:
                out[k] = None
                continue
            try:
                out[k] = riyadh.parse_date(raw)
            except ValueError:
                errors[p] = "invalid"
        if errors:
            raise FieldError(errors)
        if out["from"] is None and out["to"] is None:
            if self.required:
                raise FieldError({p_from: "required"})
            return None
        if self.required and (out["from"] is None or out["to"] is None):
            raise FieldError({p_from if out["from"] is None else p_to: "required"})
        if out["from"] and out["to"] and out["to"] < out["from"]:
            raise FieldError({p_to: "reversed_range"})
        if self.max_days and out["from"] and out["to"] and (out["to"] - out["from"]).days + 1 > self.max_days:
            raise FieldError({p_to: "range_too_long"})
        return {"from": out["from"].isoformat() if out["from"] else None,
                "to": out["to"].isoformat() if out["to"] else None}

    def sql(self, value, n):
        parts, params = [], {}
        lo, hi = value.get("from"), value.get("to")
        if self.column_type == "timestamptz":
            if lo:
                parts.append(f"{self.column} >= :f{n}_lo")
                params[f"f{n}_lo"] = riyadh.day_bounds(lo)[0]
            if hi:
                parts.append(f"{self.column} < :f{n}_hi")
                params[f"f{n}_hi"] = riyadh.day_bounds(hi)[1]
        else:
            if lo:
                parts.append(f"{self.column} >= CAST(:f{n}_lo AS date)")
                params[f"f{n}_lo"] = lo
            if hi:
                parts.append(f"{self.column} <= CAST(:f{n}_hi AS date)")
                params[f"f{n}_hi"] = hi
        return " AND ".join(parts), params

    def to_params(self, value):
        if value is None:
            return {}
        p_from, p_to = self.params()
        out = {}
        if value.get("from"):
            out[p_from] = value["from"]
        if value.get("to"):
            out[p_to] = value["to"]
        return out

    def describe(self):
        d = super().describe()
        d.update({"semantics": "inclusive_civil_dates", "timezone": "Asia/Riyadh", "max_days": self.max_days})
        return d


class TimestampRange(DateRange):
    """Riyadh civil days over a real event timestamp (note created_at, CDR start): the same
    half-open `[Riyadh 00:00 of from, Riyadh 00:00 after to)` UTC range; never `<= 23:59:59`."""

    type = "timestamp_range"

    def __init__(self, key, label_en, label_ar, column: str, **kw):
        kw.pop("column_type", None)
        super().__init__(key, label_en, label_ar, column, column_type="timestamptz", **kw)

    def describe(self):
        d = super().describe()
        d["semantics"] = "riyadh_day_bounds"
        return d


class Enum(FilterSpec):
    """One or more of fixed `choices` ({code: (en, ar)}). `column` (SQL) is compared with
    `= ANY(...)`, or `predicates` ({code: SQL}) are OR'ed for codes that are not a column value."""

    type = "enum"

    def __init__(self, key, label_en, label_ar, choices: dict, *, column: str | None = None,
                 predicates: dict | None = None, multi: bool = True, default: list | None = None, **kw):
        super().__init__(key, label_en, label_ar, **kw)
        if not column and not predicates:
            raise ValueError("Enum needs a column or predicates")
        self.choices = {k: label(*v) if isinstance(v, (tuple, list)) else v for k, v in choices.items()}
        self.column, self.predicates, self.multi, self.default = column, predicates, multi, default

    def normalize(self, args):
        values = args.many(self.key)
        if not values:
            if self.default:
                return list(self.default)
            if self.required:
                raise FieldError({self.key: "required"})
            return None
        if "all" in values and len(values) == 1:
            return None
        bad = [v for v in values if v not in self.choices]
        if bad:
            raise FieldError({self.key: "unknown_value"})
        if not self.multi and len(values) > 1:
            raise FieldError({self.key: "too_many"})
        order = list(self.choices)
        return sorted(values, key=order.index)

    def sql(self, value, n):
        if self.predicates:
            return "(" + " OR ".join(f"({self.predicates[v]})" for v in value) + ")", {}
        return f"{self.column} = ANY(CAST(:f{n} AS text[]))", {f"f{n}": list(value)}

    def to_params(self, value):
        return {} if value is None else {self.key: ",".join(value)}

    def describe(self):
        d = super().describe()
        d.update({"choices": [{"value": k, "labels": v} for k, v in self.choices.items()],
                  "multi": self.multi, "default": self.default})
        return d


class Ids(FilterSpec):
    """Entity ids chosen with a scoped selector (`GET /v2/report-options/<key>?report=<id>`).

    `column` is the SQL value compared with the ids; `sql_template` (with `{ids}` for the
    bound uuid[] parameter) replaces the default `column = ANY(ids)` when the predicate needs
    a subquery (e.g. the recorder's branch). `entity` names the option source in
    `core.reports.options` (user, branch, contract, portfolio, ...). `depends_on` lists filter
    keys that narrow the selector's candidates (a portfolio list narrowed by the contract)."""

    type = "ids"

    def __init__(self, key, label_en, label_ar, entity: str, column: str, *,
                 sql_template: str | None = None, depends_on: tuple = (), max_ids: int = MAX_IDS, **kw):
        super().__init__(key, label_en, label_ar, **kw)
        self.entity, self.column, self.sql_template = entity, column, sql_template
        self.depends_on, self.max_ids = tuple(depends_on), max_ids

    def normalize(self, args):
        values = args.many(self.key)
        if not values:
            if self.required:
                raise FieldError({self.key: "required"})
            return None
        if len(values) > self.max_ids:
            raise FieldError({self.key: "too_many"})
        out = []
        for v in values:
            try:
                out.append(str(uuid.UUID(v)))
            except (ValueError, AttributeError):
                raise FieldError({self.key: "invalid"}) from None
        return sorted(set(out))

    def sql(self, value, n):
        ids = f"CAST(:f{n} AS uuid[])"
        if self.sql_template:
            return self.sql_template.format(ids=ids), {f"f{n}": list(value)}
        return f"{self.column} = ANY({ids})", {f"f{n}": list(value)}

    def to_params(self, value):
        return {} if value is None else {self.key: ",".join(value)}

    def describe(self):
        d = super().describe()
        d.update({"selector": {"field": self.key, "entity": self.entity,
                               "endpoint": f"/v2/report-options/{self.key}"},
                  "depends_on": list(self.depends_on), "max_ids": self.max_ids})
        return d


class Text(FilterSpec):
    """Literal, case-insensitive "contains" over one or more text columns (OR). `%`, `_` and
    `\\` are matched literally (escaped LIKE, so pg_trgm indexes stay usable)."""

    type = "text"

    def __init__(self, key, label_en, label_ar, columns: tuple, *, max_len: int = MAX_TEXT, **kw):
        super().__init__(key, label_en, label_ar, **kw)
        self.columns, self.max_len = tuple(columns), max_len

    def normalize(self, args):
        v = args.text(self.key)
        if v is None:
            if self.required:
                raise FieldError({self.key: "required"})
            return None
        if len(v) > self.max_len:
            raise FieldError({self.key: "too_long"})
        return v

    def sql(self, value, n):
        pattern = "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        ors = " OR ".join(f"{c} ILIKE :f{n} ESCAPE '\\'" for c in self.columns)
        return f"({ors})", {f"f{n}": pattern}

    def describe(self):
        d = super().describe()
        d.update({"semantics": "literal_contains_case_insensitive", "max_len": self.max_len})
        return d


class AmountRange(FilterSpec):
    """Inclusive exact SAR bounds `<key>_from` / `<key>_to` (up to 2 decimals, >= 0)."""

    type = "amount_range"

    def __init__(self, key, label_en, label_ar, column: str, **kw):
        super().__init__(key, label_en, label_ar, **kw)
        self.column = column

    def params(self):
        return (f"{self.key}_from", f"{self.key}_to")

    @staticmethod
    def _parse(raw, name):
        try:
            d = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise FieldError({name: "invalid"}) from None
        if not d.is_finite():
            raise FieldError({name: "invalid"})
        if d < 0:
            raise FieldError({name: "negative"})
        if d != d.quantize(CENT):
            raise FieldError({name: "too_precise"})
        if d > MONEY_MAX:
            raise FieldError({name: "too_large"})
        return d.quantize(CENT)

    def normalize(self, args):
        p_from, p_to = self.params()
        lo_raw, hi_raw = args.text(p_from), args.text(p_to)
        if lo_raw is None and hi_raw is None:
            return None
        lo = self._parse(lo_raw, p_from) if lo_raw is not None else None
        hi = self._parse(hi_raw, p_to) if hi_raw is not None else None
        if lo is not None and hi is not None and hi < lo:
            raise FieldError({p_to: "reversed_range"})
        return {"from": str(lo) if lo is not None else None, "to": str(hi) if hi is not None else None}

    def sql(self, value, n):
        parts, params = [], {}
        if value.get("from") is not None:
            parts.append(f"{self.column} >= CAST(:f{n}_lo AS numeric)")
            params[f"f{n}_lo"] = value["from"]
        if value.get("to") is not None:
            parts.append(f"{self.column} <= CAST(:f{n}_hi AS numeric)")
            params[f"f{n}_hi"] = value["to"]
        return " AND ".join(parts), params

    def to_params(self, value):
        if value is None:
            return {}
        p_from, p_to = self.params()
        out = {}
        if value.get("from") is not None:
            out[p_from] = value["from"]
        if value.get("to") is not None:
            out[p_to] = value["to"]
        return out

    def describe(self):
        d = super().describe()
        d.update({"semantics": "inclusive_exact_sar"})
        return d


# =========================================================================== columns, sorts, totals
@dataclasses.dataclass(frozen=True)
class Column:
    """One output column.

    type       text | int | money | date | datetime | enum | bool | uuid | json
    sql        SQL expression (selected AS the key)
    enum       {code: {"en", "ar"}} labels for `enum` columns (the XLSX variants show labels)
    protected  None | "identity" | "target": an employee field (core/user_privacy.py); the
               value is null unless the viewer may see it or `subject` (a column key holding
               the employee's user id) is the viewer
    hidden     selected (e.g. a `subject` id) but never returned or exported
    export     False: shown in the preview only
    """
    key: str
    label_en: str
    label_ar: str
    type: str
    sql: str
    enum: dict | None = None
    protected: str | None = None
    subject: str | None = None
    hidden: bool = False
    export: bool = True
    role: str | None = None          # documentation: owner | recorder | approver | historical_owner

    def __post_init__(self):
        if self.type not in COLUMN_TYPES:
            raise ValueError(f"column {self.key}: unknown type {self.type!r}")
        if self.protected not in (None, "identity", "target"):
            raise ValueError(f"column {self.key}: protected must be identity|target")

    @property
    def labels(self):
        return label(self.label_en, self.label_ar)

    @property
    def money(self) -> bool:
        return self.type == "money"

    def describe(self) -> dict:
        return {"key": self.key, "labels": self.labels, "type": self.type, "money": self.money,
                "currency": "SAR" if self.money else None, "protected": self.protected,
                "role": self.role, "export": self.export,
                "enum": ([{"value": k, "labels": v} for k, v in self.enum.items()] if self.enum else None)}


@dataclasses.dataclass(frozen=True)
class Sort:
    key: str
    label_en: str
    label_ar: str
    sql: str
    nulls_last: bool = True

    def describe(self):
        return {"key": self.key, "labels": label(self.label_en, self.label_ar)}


@dataclasses.dataclass(frozen=True)
class Total:
    """An aggregate over the full filtered result (never the page)."""
    key: str
    label_en: str
    label_ar: str
    sql: str
    type: str = "int"
    # True for aggregates where "no value" means unknown (averages, min/max, sums over
    # nullable measures such as CDR durations): a NULL stays null instead of becoming 0.
    nullable: bool = False

    def __post_init__(self):
        if self.type not in TOTAL_TYPES:
            raise ValueError(f"total {self.key}: type must be int|money")

    def describe(self):
        return {"key": self.key, "labels": label(self.label_en, self.label_ar), "type": self.type,
                "currency": "SAR" if self.type == "money" else None, "nullable": self.nullable}


# =========================================================================== the definition
class ReportDefinition:
    """Subclass (or instantiate with attributes set) and register through `REPORTS`."""

    id: str = ""                     # "RPT-06"
    family: str = ""                 # "collections"
    label_en: str = ""
    label_ar: str = ""
    description_en: str = ""
    description_ar: str = ""
    permissions: tuple = ()          # any-of; the legacy screen's key (screen-key rule)
    filters: tuple = ()              # FilterSpec instances
    columns: tuple = ()              # Column instances, in display order
    sorts: tuple = ()                # Sort instances
    default_sort: str = ""           # "-created_at"
    totals: tuple = ()               # Total instances; `count` is always added
    id_sql: str = ""                 # unique row id expression: the stable tie-breaker
    xlsx_ar: bool = False            # offer the Arabic RTL XLSX export
    fixed: tuple = ()                # documentation: [{"en", "ar"}] rules the caller cannot change
    legacy: dict | None = None       # documentation: {"endpoint", "screen", "key"}
    sheet_title_ar: str | None = None  # Arabic XLSX sheet title (<= 31 chars)

    def build(self, ctx) -> Built:
        """FROM + base predicates (scope + fixed rules) for `ctx` (a ReportContext)."""
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers
    @property
    def labels(self):
        return label(self.label_en, self.label_ar)

    def filter(self, key) -> FilterSpec | None:
        for f in self.filters:
            if f.key == key:
                return f
        return None

    def sort(self, key) -> Sort | None:
        for s in self.sorts:
            if s.key == key:
                return s
        return None

    def visible_columns(self, for_export: bool = False) -> list[Column]:
        return [c for c in self.columns if not c.hidden and (c.export or not for_export)]

    def formats(self) -> list[str]:
        return ["csv", "xlsx"] + (["xlsx_ar"] if self.xlsx_ar else [])

    def validate(self) -> None:
        """Raise ValueError for an inconsistent definition (called at registration)."""
        if not self.id or not self.family or not self.permissions:
            raise ValueError(f"report {self!r}: id, family and permissions are required")
        if not self.id_sql:
            raise ValueError(f"report {self.id}: id_sql (the tie-breaker) is required")
        keys = [c.key for c in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError(f"report {self.id}: duplicate column keys")
        for c in self.columns:
            if c.subject and c.subject not in keys:
                raise ValueError(f"report {self.id}: column {c.key} subject {c.subject} is not a column")
        params = [p for f in self.filters for p in f.params()]
        reserved = {"page", "page_size", "sort", "report", "q_option", "selected", "format"}
        if len(params) != len(set(params)) or reserved & set(params):
            raise ValueError(f"report {self.id}: duplicate or reserved filter parameter")
        for f in self.filters:
            for dep in getattr(f, "depends_on", ()):
                if self.filter(dep) is None:
                    raise ValueError(f"report {self.id}: {f.key} depends on unknown filter {dep}")
        default = self.default_sort.lstrip("-")
        if self.sort(default) is None:
            raise ValueError(f"report {self.id}: default sort {self.default_sort!r} is not a sort")
        tkeys = [t.key for t in self.totals]
        if "count" in tkeys or len(tkeys) != len(set(tkeys)):
            raise ValueError(f"report {self.id}: totals must be unique and not 'count'")

    def describe(self, full: bool = True) -> dict:
        d = {"id": self.id, "family": self.family, "labels": self.labels,
             "description": label(self.description_en, self.description_ar),
             "permissions": list(self.permissions), "formats": self.formats()}
        if full:
            d.update({
                "filters": [f.describe() for f in self.filters],
                "columns": [c.describe() for c in self.visible_columns()],
                "sorts": [s.describe() for s in self.sorts],
                "default_sort": self.default_sort,
                "totals": [{"key": "count", "labels": label("Rows", "عدد السجلات"), "type": "int",
                            "currency": None}] + [t.describe() for t in self.totals],
                "fixed": list(self.fixed),
                "legacy": self.legacy,
                "paging": {"default_page_size": 50, "max_page_size": 200},
            })
        return d

    def __repr__(self):
        return f"<Report {self.id or type(self).__name__}>"


# =========================================================================== value formatting
def money_str(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(CENT))


def iso_instant(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def json_value(col: Column, value) -> Any:
    """Preview / CSV representation of one cell (CSV uses str() of this, blank for None)."""
    if value is None:
        return None
    t = col.type
    if t == "money":
        return money_str(value)
    if t == "datetime":
        return iso_instant(value)
    if t == "date":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if t == "int":
        return int(value)
    if t == "bool":
        return bool(value)
    if t in ("uuid", "text", "enum"):
        return str(value)
    return value


Normalizer = Callable[[Args], Any]
