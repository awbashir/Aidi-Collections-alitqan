"""Contract note statuses ("حالات العقود"): general statuses, one level of sub-statuses, soft
delete, and the checks every note writer runs (migration 0068, customer request 2026-10).

Rules
-----
* `contract_id` NULL = a general status, usable on every contract.
* `parent_id` makes a status a sub-status. ONE level only: the parent must be a live main
  status (no parent itself), and a status that has sub-statuses cannot become one.
* A sub-status always carries its parent's `contract_id` and `CommentReachability_id`: they are
  copied on create/update, and a main status's change is propagated to its sub-statuses.
* DELETE is a soft delete of the status and its sub-statuses (`deleted_at`, `deleted_by`, one
  audit event each). Existing notes keep pointing at the row, so they keep showing its name.
* New notes refuse a deleted status (`status_deleted`) and a sub-status given as the main
  status (`is_sub_status`). The optional sub-status of a note must be a live child of the
  note's main status (`not_a_sub_status`). `"Comments".comment_status_id` stays the MAIN
  status, so reports and filters that group by it are unchanged.

The new columns are not mapped in models.py (their as_dict()/model_cols() are legacy wire
contracts), so they are read and written here with SQL.

Errors of the legacy settings routes keep their contract: HTTP 201 with
`{"message": <Arabic>, "status": false, "error": <code>}` (core/http.py).
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import text

from core import audit
from extensions import db

DEFAULT_COLOR = "#FFFFFF"
CACHE_KEYS = ("b1_comment_statuses",)

MESSAGES = {
    "not_found": "حالة التعليق غير موجودة",
    "status_deleted": "حالة التعليق محذوفة",
    "already_deleted": "حالة التعليق محذوفة مسبقاً",
    "invalid_parent_id": "معرّف الحالة الرئيسية غير صالح",
    "parent_not_found": "الحالة الرئيسية غير موجودة",
    "parent_deleted": "الحالة الرئيسية محذوفة",
    "parent_is_sub_status": "لا يمكن إضافة حالة فرعية تحت حالة فرعية (مستوى واحد فقط)",
    "parent_is_self": "لا يمكن أن تكون الحالة رئيسية لنفسها",
    "has_sub_statuses": "لهذه الحالة حالات فرعية، فلا يمكن جعلها حالة فرعية",
    "is_sub_status": "هذه حالة فرعية؛ اختر الحالة الرئيسية ثم الحالة الفرعية",
    "not_a_sub_status": "الحالة الفرعية لا تتبع الحالة المختارة",
    "unknown_sub_status": "الحالة الفرعية غير موجودة",
}


class StatusRuleError(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.message = message or MESSAGES.get(code, code)

    def body(self) -> dict:
        return {"message": self.message, "status": False, "error": self.code}


def _s():
    return db.session


def as_uuid(value) -> str | None:
    """A UUID string, or None for an empty or malformed value."""
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def invalidate_cache() -> None:
    from core import lookups
    lookups.invalidate(*CACHE_KEYS)


# --------------------------------------------------------------------------- reads
_ROW = """
    SELECT s.id::text AS id, s.name, s.parent_id::text AS parent_id, s.deleted_at,
           s.deleted_by::text AS deleted_by, s.contract_id::text AS contract_id,
           s."CommentReachability_id"::text AS reach
      FROM "CommentStatuses" s"""


def load(status_id, session=None, *, lock=False):
    """One status (with the 0068 columns) as a mapping, or None (also for a non-UUID id)."""
    sid = as_uuid(status_id)
    if sid is None:
        return None
    sql = _ROW + " WHERE s.id = CAST(:id AS uuid)" + (" FOR UPDATE" if lock else "")
    return (session or _s()).execute(text(sql), {"id": sid}).mappings().first()


def tree_columns(session=None) -> dict:
    """{status id: {"parent_id", "deleted_at", "deleted_by"}} for every status (a small table)."""
    rows = (session or _s()).execute(text("""
        SELECT id::text, parent_id::text, deleted_at, deleted_by::text FROM "CommentStatuses"
    """)).all()
    return {r[0]: {"parent_id": r[1], "deleted_at": r[2], "deleted_by": r[3]} for r in rows}


def with_tree(status_dict: dict, extra: dict | None, *, deleted=False) -> dict:
    """A legacy as_dict() row plus `parent_id` (and `deleted_at` when asked)."""
    extra = extra or {}
    status_dict["parent_id"] = extra.get("parent_id")
    if deleted:
        status_dict["deleted_at"] = extra.get("deleted_at")
    return status_dict


def tree_order(rows: list[dict]) -> list[dict]:
    """Main statuses in their current order, each followed by its own sub-statuses. A
    sub-status whose main status is not in `rows` stays where it is."""
    ids = {str(r["id"]) for r in rows}
    children: dict[str, list[dict]] = {}
    tops = []
    for r in rows:
        p = r.get("parent_id")
        if p and p in ids:
            children.setdefault(p, []).append(r)
        else:
            tops.append(r)
    out = []
    for r in tops:
        out.append(r)
        out.extend(children.get(str(r["id"]), []))
    return out


# --------------------------------------------------------------------------- note checks
def main_status_problem(row) -> str | None:
    """For a NEW note's main status (a `load()` mapping or None)."""
    if row is None:
        return "unknown"
    if row["deleted_at"] is not None:
        return "status_deleted"
    if row["parent_id"] is not None:
        return "is_sub_status"
    return None


def sub_status_problem(main_id, sub_id, session=None) -> str | None:
    """For a NEW note's optional sub-status: a live child of `main_id`."""
    sub = load(sub_id, session)
    if sub is None:
        return "unknown"
    if sub["deleted_at"] is not None:
        return "status_deleted"
    if sub["parent_id"] is None or sub["parent_id"] != as_uuid(main_id):
        return "not_a_sub_status"
    return None


def legacy_note_problem(status_id, sub_id) -> tuple[str, str] | None:
    """The legacy note writers (POST /comments, /commentsbyexcel): only what is new is refused
    (a deleted status, a sub-status given as the main one, a bad sub-status). An unknown or
    malformed main status keeps failing exactly as before (the database refuses it)."""
    row = load(status_id)
    if row is not None:
        problem = main_status_problem(row)
        if problem:
            return problem, MESSAGES[problem]
    if sub_id is not None:
        if row is None:
            return "not_a_sub_status", MESSAGES["not_a_sub_status"]
        problem = sub_status_problem(row["id"], sub_id)
        if problem:
            code = "unknown_sub_status" if problem == "unknown" else problem
            return code, MESSAGES[code]
    return None


def insert_legacy_note(comment, sub_id) -> dict:
    """POST /comments with a sub-status: the same row the ORM would insert plus
    comment_sub_status_id (unmapped), in ONE insert so the note-history UPDATE trigger never
    fires for it. Returns the legacy as_dict() plus `comment_sub_status_id`."""
    from models import Comments
    values = {}
    for c in Comments.__table__.columns:
        v = getattr(comment, c.key, None)
        if v is not None:
            values[c.name] = str(v) if isinstance(v, uuid.UUID) else v
    values.setdefault("id", str(uuid.uuid4()))
    values["comment_sub_status_id"] = as_uuid(sub_id)
    _s().execute(sa.insert(sa.table("Comments", *[sa.column(k) for k in values])).values(**values))
    _s().commit()
    row = _s().get(Comments, uuid.UUID(values["id"]))
    out = row.as_dict()
    out["comment_sub_status_id"] = values["comment_sub_status_id"]
    return out


# --------------------------------------------------------------------------- writes
def _parent(parent_id, self_id=None):
    pid = as_uuid(parent_id)
    if pid is None:
        raise StatusRuleError("invalid_parent_id")
    if self_id is not None and pid == str(self_id):
        raise StatusRuleError("parent_is_self")
    p = load(pid, lock=True)
    if p is None:
        raise StatusRuleError("parent_not_found")
    if p["deleted_at"] is not None:
        raise StatusRuleError("parent_deleted")
    if p["parent_id"] is not None:
        raise StatusRuleError("parent_is_sub_status")
    if self_id is not None and _s().execute(text("""
            SELECT 1 FROM "CommentStatuses" WHERE parent_id = CAST(:id AS uuid) AND deleted_at IS NULL LIMIT 1"""),
            {"id": str(self_id)}).first() is not None:
        raise StatusRuleError("has_sub_statuses")
    return p


def _blank_to_none(data: dict, *keys) -> None:
    for k in keys:
        if k in data and data[k] in ("", None):
            data[k] = None


def _set_parent(status_id, parent_id) -> None:
    _s().execute(text('UPDATE "CommentStatuses" SET parent_id = CAST(:p AS uuid) WHERE id = CAST(:id AS uuid)'),
                 {"p": parent_id, "id": str(status_id)})


def create(data: dict) -> dict:
    """POST /comment_status: the legacy ORM insert, plus parent_id and the default colour."""
    from models import CommentStatuses
    data = dict(data or {})
    parent_id = data.pop("parent_id", None)
    for k in ("deleted_at", "deleted_by", "parent"):
        data.pop(k, None)
    if not data.get("color"):
        data["color"] = DEFAULT_COLOR
    _blank_to_none(data, "contract_id", "CommentReachability_id")
    parent = None
    if parent_id not in (None, ""):
        parent = _parent(parent_id)
        data["contract_id"] = parent["contract_id"]
        data["CommentReachability_id"] = parent["reach"]
    status = CommentStatuses(**data)
    _s().add(status)
    _s().flush()
    if parent is not None:
        _set_parent(status.id, parent["id"])
    _s().commit()
    invalidate_cache()
    return with_tree(status.as_dict(), {"parent_id": parent["id"] if parent else None})


def update(status, data: dict) -> dict:
    """PUT /comment_status/<id>: the legacy field updates, plus parent_id and the
    contract/reachability inheritance in both directions."""
    data = dict(data or {})
    current = load(status.id, lock=True)
    if current["deleted_at"] is not None:
        raise StatusRuleError("status_deleted")
    _blank_to_none(data, "contract_id", "CommentReachability_id")
    status.name = data['name'] if 'name' in data else status.name
    status.contract_id = data['contract_id'] if 'contract_id' in data else status.contract_id
    status.CommentReachability_id = (data['CommentReachability_id'] if 'CommentReachability_id' in data
                                     else status.CommentReachability_id)
    status.is_active = data['is_active'] if 'is_active' in data else status.is_active
    if 'color' in data:
        status.color = data['color'] or DEFAULT_COLOR

    parent_id = current["parent_id"]
    if "parent_id" in data:
        parent_id = None if data["parent_id"] in (None, "") else data["parent_id"]
    parent = _parent(parent_id, self_id=status.id) if parent_id is not None else None
    if parent is not None:
        status.contract_id = parent["contract_id"]
        status.CommentReachability_id = parent["reach"]
    _s().flush()
    _set_parent(status.id, parent["id"] if parent else None)
    if parent is None:                                   # a main status: its sub-statuses follow it
        _s().execute(text("""
            UPDATE "CommentStatuses"
               SET contract_id = s.contract_id, "CommentReachability_id" = s."CommentReachability_id"
              FROM "CommentStatuses" s
             WHERE s.id = CAST(:id AS uuid) AND "CommentStatuses".parent_id = s.id
               AND ("CommentStatuses".contract_id IS DISTINCT FROM s.contract_id
                    OR "CommentStatuses"."CommentReachability_id" IS DISTINCT FROM s."CommentReachability_id")
        """), {"id": str(status.id)})
    _s().commit()
    invalidate_cache()
    return with_tree(status.as_dict(), {"parent_id": parent["id"] if parent else None})


def soft_delete(status_id) -> dict:
    """DELETE /comment_status/<id>: soft-delete the status and its live sub-statuses."""
    row = load(status_id, lock=True)
    if row is None:
        raise StatusRuleError("not_found")
    if row["deleted_at"] is not None:
        raise StatusRuleError("already_deleted")
    actor = as_uuid(_actor())
    children = _s().execute(text("""
        UPDATE "CommentStatuses" SET deleted_at = now(), deleted_by = CAST(:by AS uuid)
         WHERE parent_id = CAST(:id AS uuid) AND deleted_at IS NULL
        RETURNING id::text, name, deleted_at"""), {"id": row["id"], "by": actor}).all()
    deleted_at = _s().execute(text("""
        UPDATE "CommentStatuses" SET deleted_at = now(), deleted_by = CAST(:by AS uuid)
         WHERE id = CAST(:id AS uuid) RETURNING deleted_at"""), {"id": row["id"], "by": actor}).scalar()
    notes = _s().execute(text("""
        SELECT count(*) FROM "Comments"
         WHERE comment_status_id = ANY(CAST(:ids AS uuid[])) OR comment_sub_status_id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": [row["id"]] + [c[0] for c in children]}).scalar()
    audit.record("comment_status.delete", "comment_status", row["id"],
                 before=_audit_view(row), after={"deleted_at": deleted_at, "sub_statuses_deleted": [c[0] for c in children],
                                                 "notes_kept": int(notes or 0)},
                 reason="soft delete; existing notes keep the status")
    for c in children:
        audit.record("comment_status.delete", "comment_status", c[0],
                     before={"name": c[1], "parent_id": row["id"], "deleted_at": None},
                     after={"deleted_at": c[2]}, reason="its main status was deleted")
    _s().commit()
    invalidate_cache()
    return {"id": row["id"], "deleted_at": deleted_at, "sub_statuses_deleted": [c[0] for c in children]}


def _audit_view(row) -> dict:
    return {"name": row["name"], "parent_id": row["parent_id"], "contract_id": row["contract_id"],
            "CommentReachability_id": row["reach"], "deleted_at": None}


def _actor():
    try:
        from flask_jwt_extended import get_jwt_identity
        return get_jwt_identity()
    except Exception:  # noqa: BLE001 - no JWT (CLI)
        return None
