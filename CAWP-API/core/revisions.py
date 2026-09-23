"""Optimistic concurrency on the `revision` columns (db/migrations/0006_revision_columns.sql).

The columns are not mapped in models.py (so they never leak into legacy responses); this
module reads and checks them with plain SQL on the caller's session/transaction.

A trigger bumps `revision` on every real change, so the pattern for a new mutating route
is:

    expected = expected_revision_from_request()        # body "expected_revision" or If-Match
    check_revision("case", case_id, expected)          # SELECT ... FOR UPDATE; raises on mismatch
    ... UPDATE clints ... (ORM or SQL, same transaction)
    new_rev = current_revision("case", case_id)        # after flush, to return to the client

`RevisionConflict` becomes `409 {"code": "revision_conflict", "error": {"expected", "current"}}`
through `core.api_response.register_api_errors`.
"""
from __future__ import annotations

import re

from sqlalchemy import text

# entity name -> quoted table. Only these tables carry a revision column + trigger.
REVISIONED = {
    "case": 'clints',
    "comment": '"Comments"',
    "payment": '"Payments"',
    "contact": '"ClintContacts"',
    "promise": '"paymentDates"',
}


class RevisionConflict(Exception):
    def __init__(self, entity: str, entity_id, expected: int, current: int):
        super().__init__(f"{entity} {entity_id} was changed by someone else "
                         f"(expected revision {expected}, current {current})")
        self.entity, self.entity_id, self.expected, self.current = entity, entity_id, expected, current

    def as_error(self) -> dict:
        return {"entity": self.entity, "id": str(self.entity_id),
                "expected_revision": self.expected, "current_revision": self.current}


class RevisionNotFound(LookupError):
    def __init__(self, entity: str, entity_id):
        super().__init__(f"{entity} {entity_id} not found")
        self.entity, self.entity_id = entity, entity_id


def _table(entity: str) -> str:
    try:
        return REVISIONED[entity]
    except KeyError:
        raise ValueError(f"no revision column for entity {entity!r}; known: {sorted(REVISIONED)}") from None


def _session(session):
    if session is not None:
        return session
    from extensions import db
    return db.session


_ETAG = re.compile(r'^\s*(?:W/)?"?(\d+)"?\s*$')


def parse_revision(value) -> int:
    """int from 3, "3", '"3"' or 'W/"3"'. ValueError for anything else (incl. bool/negative)."""
    if isinstance(value, bool) or value is None:
        raise ValueError("revision must be a non-negative integer")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("revision must be a non-negative integer")
        return value
    m = _ETAG.match(str(value))
    if not m:
        raise ValueError("revision must be a non-negative integer")
    return int(m.group(1))


def expected_revision_from_request(field: str = "expected_revision", required: bool = True):
    """Expected revision from the JSON body field, else the If-Match header.

    Raises core.api_response.ApiError(422) when missing (and required) or malformed.
    """
    from flask import request

    from core.api_response import VALIDATION_FAILED, ApiError

    body = request.get_json(silent=True)
    raw = body.get(field) if isinstance(body, dict) and field in body else request.headers.get("If-Match")
    if raw is None:
        if required:
            raise ApiError(422, VALIDATION_FAILED, f"{field} is required", {field: "required"})
        return None
    try:
        return parse_revision(raw)
    except ValueError as e:
        raise ApiError(422, VALIDATION_FAILED, str(e), {field: "invalid"}) from None


def current_revision(entity: str, entity_id, *, lock: bool = False, session=None) -> int:
    """The row's revision (optionally `FOR UPDATE`); RevisionNotFound when absent."""
    sql = f"SELECT revision FROM {_table(entity)} WHERE id = :id" + (" FOR UPDATE" if lock else "")
    row = _session(session).execute(text(sql), {"id": str(entity_id)}).first()
    if row is None:
        raise RevisionNotFound(entity, entity_id)
    return int(row[0])


def check_revision(entity: str, entity_id, expected, *, lock: bool = True, session=None) -> int:
    """Lock the row (default) and compare. Returns the current revision.

    Holding the row lock until the caller commits means no other writer can slip in
    between this check and the caller's UPDATE.
    """
    expected = parse_revision(expected)
    current = current_revision(entity, entity_id, lock=lock, session=session)
    if current != expected:
        raise RevisionConflict(entity, entity_id, expected, current)
    return current


def guarded_update(entity: str, entity_id, expected, values: dict, *, session=None) -> int:
    """Single-statement compare-and-set: UPDATE ... WHERE id = :id AND revision = :expected.

    `values` maps *column names* (quoted here) to values; the column names must come from
    code, never from the request. Returns the new revision; raises RevisionConflict or
    RevisionNotFound when nothing was updated.
    """
    if not values:
        raise ValueError("values must not be empty")
    expected = parse_revision(expected)
    sets, params = [], {"id": str(entity_id), "expected": expected}
    for i, (col, val) in enumerate(values.items()):
        if col == "revision" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", col):
            raise ValueError(f"invalid column name {col!r}")
        sets.append(f'"{col}" = :v{i}')
        params[f"v{i}"] = val
    sql = (f"UPDATE {_table(entity)} SET {', '.join(sets)} "
           f"WHERE id = :id AND revision = :expected RETURNING revision")
    s = _session(session)
    row = s.execute(text(sql), params).first()
    if row is not None:
        return int(row[0])
    current = current_revision(entity, entity_id, session=s)  # raises RevisionNotFound
    raise RevisionConflict(entity, entity_id, expected, current)
