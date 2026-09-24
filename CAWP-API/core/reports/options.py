"""Scoped selector options: `GET /v2/report-options/<field>?report=<id>`.

Candidates are the distinct values of the report's `Ids` filter column over the report's
own base (current case scope + fixed rules), narrowed by the filters the field depends on
(`depends_on`, e.g. the contract for portfolios). So a selector only ever lists entities
attached to a record the caller may see in that report; inactive/historical entities stay.

* 50 per page, ordered by label then id.
* `q`: literal, case-insensitive search over the entity's label fields (no wildcards).
* `selected=<id>[,<id>]`: those ids are resolved within the same candidate set,
  independently of `q` and the page, so a choice beyond page one survives a reload.
  An id outside the candidates is simply not returned (no existence leak).

Streams can add entity kinds with `register_entity(Entity(...))`.
"""
from __future__ import annotations

import dataclasses
import math
import uuid

from sqlalchemy import text

from core.api_response import ApiError
from core.reports import engine
from core.reports.definition import Args, FieldError, Ids

PAGE_SIZE = 50


@dataclasses.dataclass(frozen=True)
class Entity:
    key: str
    table_sql: str            # "<table> e"
    label_sql: str            # display label
    secondary_sql: str = "NULL"
    active_sql: str = "TRUE"
    search: tuple = ()        # text expressions searched (literal contains)


_ENTITIES: dict[str, Entity] = {}


def register_entity(entity: Entity) -> Entity:
    _ENTITIES[entity.key] = entity
    return entity


register_entity(Entity("user", "users e",
                       "btrim(concat_ws(' ', e.frist_name, e.last_name))", "e.user_name", "e.is_active",
                       ("btrim(concat_ws(' ', e.frist_name, e.last_name))", "e.user_name")))
register_entity(Entity("branch", "branches e", "e.name", "NULL", "e.is_active", ("e.name",)))
register_entity(Entity("contract", "contracts e", "e.name", "NULL", "e.is_active", ("e.name",)))
register_entity(Entity("portfolio", "portfolios e",
                       "e.name", "(SELECT oc.name FROM contracts oc WHERE oc.id = e.contract_id)",
                       "NOT e.is_deleted", ("e.name",)))


def entity(key: str) -> Entity:
    return _ENTITIES[key]


def options(report, field: str, args, actor_id, scope) -> dict:
    f = report.filter(field)
    if not isinstance(f, Ids):
        raise ApiError(404, "unknown_option_field", f"report {report.id} has no selector {field!r}",
                       {"fields": [x.key for x in report.filters if isinstance(x, Ids)]})
    ent = entity(f.entity)
    a = Args(args)
    try:
        q = a.text("q")
        selected = a.many("selected")
    except FieldError as e:
        raise engine.validation_error(e.errors) from None
    if q is not None and len(q) > 100:
        raise engine.validation_error({"q": "too_long"})
    try:
        page = int(a.text("page") or 1)
    except (TypeError, ValueError):
        raise engine.validation_error({"page": "invalid"}) from None
    if page < 1:
        raise engine.validation_error({"page": "invalid"})
    sel_ids = []
    for s in selected:
        try:
            sel_ids.append(str(uuid.UUID(s)))
        except ValueError:
            raise engine.validation_error({"selected": "invalid"}) from None
    if len(sel_ids) > f.max_ids:
        raise engine.validation_error({"selected": "too_many"})

    # dependency filters only; the rest of the report's parameters are accepted and ignored
    filters = engine.normalize_filters(report, a, strict=True, extra_allowed=("report", "q", "selected"))
    deps = {k: filters.get(k) for k in f.depends_on}
    ctx = engine.make_context(report, actor_id, scope, {**{k.key: None for k in report.filters}, **deps},
                              purpose="options")
    from_sql, where_sql, params = engine.compose(report, ctx, only=tuple(f.depends_on))
    cand = f"SELECT DISTINCT {f.column} AS id FROM {from_sql} WHERE {where_sql} AND {f.column} IS NOT NULL"
    base = (f"WITH cand AS MATERIALIZED ({cand}) "
            f"SELECT e.id::text AS id, {ent.label_sql} AS label, {ent.secondary_sql} AS secondary, "
            f"({ent.active_sql}) AS active, count(*) OVER () AS total_count "
            f"FROM cand JOIN {ent.table_sql} ON e.id = cand.id")
    where, p = [], dict(params)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(" + " OR ".join(f"{s} ILIKE :rq_q ESCAPE '\\'" for s in ent.search) + ")")
        p["rq_q"] = pattern
    where_q = (" WHERE " + " AND ".join(where)) if where else ""
    with engine.snapshot(engine.preview_timeout_ms()) as conn:
        rows = conn.execute(text(f"{base}{where_q} ORDER BY 2, 1 LIMIT :rq_lim OFFSET :rq_off"),
                            {**p, "rq_lim": PAGE_SIZE, "rq_off": (page - 1) * PAGE_SIZE}).mappings().all()
        if rows:
            total = rows[0]["total_count"]
        else:                                # beyond the last page: count separately
            total = conn.execute(text(f"SELECT count(*) FROM ({base}{where_q}) x"), p).scalar()
        sel = []
        if sel_ids:
            sel = conn.execute(text(f"{base} WHERE e.id = ANY(CAST(:rq_sel AS uuid[])) ORDER BY 2, 1"),
                               {**params, "rq_sel": sel_ids}).mappings().all()

    def item(r):
        return {"id": r["id"], "label": r["label"], "secondary": r["secondary"], "active": bool(r["active"])}

    return {"report": report.id, "field": field, "entity": f.entity, "q": q,
            "depends_on": {k: deps.get(k) for k in f.depends_on},
            "items": [item(r) for r in rows], "total": int(total), "page": page, "page_size": PAGE_SIZE,
            "pages": math.ceil(total / PAGE_SIZE) if total else 0,
            "selected": [item(r) for r in sel]}
