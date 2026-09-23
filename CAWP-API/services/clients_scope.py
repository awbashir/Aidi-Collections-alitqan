"""Caller scope for every client-list endpoint.

Extracted verbatim (semantics, not text) from `_build_clints_payload` and
`getClintsNneglect` in app.py so the report routes stop each re-deriving it:

  * **SuperAdmin** -- `Role.name == "SuperAdmin"` for the JWT identity's `role_id`.
    Exactly the test the two legacy builders use (`db.session.get(User, id)` ->
    `db.session.get(Role, user.role_id)` -> `role.name == "SuperAdmin"`). Unrestricted.
  * **supervisor** -- a row in `supervisors` with `user_id == identity`. Sees the clients of
    every supervised user (plus their own) *or* in any supervised contract.
  * **collector** -- everyone else: only `Clints.user_id == identity`.

The resolved scope is cached per user for 60 s (`core.lookups.get_map`, process-local TTL
dict, thread-safe). Nothing invalidates it: adding/removing a supervised user or contract
therefore takes up to a minute to take effect on the report screens. That is deliberate --
the alternative is 3 extra queries on every request -- and is noted in the B1 report.
"""
import logging

from sqlalchemy import or_

from core import lookups
from extensions import db
from models import (Clints, Portfolios, Role, Supervisor, SupervisorContract,
                    SupervisorUser, User)

log = logging.getLogger("cawp.scope")

SCOPE_TTL = 60.0

SUPER = "super"
SUPERVISOR = "supervisor"
COLLECTOR = "collector"


class Scope(object):
    """kind: 'super' | 'supervisor' | 'collector'.

    `user_ids` / `contract_ids` are None for 'super' (no restriction). For 'supervisor'
    they are the supervised sets (`user_ids` always contains the caller). For 'collector'
    `user_ids` is `{identity}` and `contract_ids` is None.
    """

    __slots__ = ("kind", "user_ids", "contract_ids", "identity", "supervisor_id")

    def __init__(self, kind, identity, user_ids=None, contract_ids=None, supervisor_id=None):
        self.kind = kind
        self.identity = str(identity)
        self.user_ids = user_ids
        self.contract_ids = contract_ids
        self.supervisor_id = supervisor_id

    @property
    def is_super(self):
        return self.kind == SUPER

    def as_dict(self):
        return {
            "kind": self.kind,
            "identity": self.identity,
            "user_ids": sorted(self.user_ids) if self.user_ids else None,
            "contract_ids": sorted(self.contract_ids) if self.contract_ids else None,
            "supervisor_id": self.supervisor_id,
        }

    def __repr__(self):
        return "<Scope %s u=%s c=%s>" % (
            self.kind,
            len(self.user_ids) if self.user_ids else "-",
            len(self.contract_ids) if self.contract_ids else "-",
        )


def _load_scope(identity):
    identity = str(identity)
    user_obj = db.session.get(User, identity)
    role_obj = db.session.get(Role, user_obj.role_id) if user_obj is not None else None
    # Same test as _build_clints_payload / getClintsNneglect / fetchClients.isSuperAdmin.
    if role_obj is not None and role_obj.name == "SuperAdmin":
        return {"id": identity, "kind": SUPER, "user_ids": None,
                "contract_ids": None, "supervisor_id": None}

    is_super = Supervisor.query.filter_by(user_id=identity).first()
    if is_super is None:
        return {"id": identity, "kind": COLLECTOR, "user_ids": [identity],
                "contract_ids": None, "supervisor_id": None}

    contract_ids = [
        str(r[0]) for r in db.session.query(SupervisorContract.contract_id)
        .filter(SupervisorContract.supervisor_id == is_super.id).all()
    ]
    user_ids = [
        str(r[0]) for r in db.session.query(SupervisorUser.user_id)
        .filter(SupervisorUser.supervisor_id == is_super.id).all()
    ]
    # the legacy builders always append the supervisor themselves
    user_ids.append(identity)
    return {"id": identity, "kind": SUPERVISOR, "user_ids": user_ids,
            "contract_ids": contract_ids, "supervisor_id": str(is_super.id)}


def resolve_scope(identity):
    """-> Scope. Cached per user for SCOPE_TTL seconds."""
    identity = str(identity)
    row = lookups.get_map(
        "b1_scope:" + identity, lambda: [_load_scope(identity)], ttl=SCOPE_TTL
    ).get(identity)
    if row is None:                                   # pragma: no cover - defensive
        row = _load_scope(identity)
    return Scope(
        row["kind"], identity,
        user_ids=set(row["user_ids"]) if row["user_ids"] else None,
        contract_ids=set(row["contract_ids"]) if row["contract_ids"] else None,
        supervisor_id=row["supervisor_id"],
    )


def invalidate_scope(identity=None):
    """Drop the cached scope (call after supervisor membership changes)."""
    if identity is None:
        lookups.invalidate()
    else:
        lookups.invalidate("b1_scope:" + str(identity))


def scope_condition(scope, request_contract_ids=None):
    """-> a SQLAlchemy boolean clause over `Clints` only, or None for SuperAdmin.

    Only same-table predicates are produced (`Clints.user_id`, `Clints.portfolios_id`) so
    PostgreSQL can BitmapOr `ix_clints_user_notarch` with `ix_clints_portfolios_notarch`
    instead of scanning, and so the clause can be reused on a count query that does not
    join `portfolios`.
    """
    if scope.kind == SUPER:
        return None

    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return Clints.user_id == scope.identity

    contract_ids = scope.contract_ids
    # If the request already narrows contracts, intersect with the supervisor scope: the
    # OR can then be dropped completely (same optimisation as _build_clints_payload).
    if request_contract_ids and contract_ids:
        req = set(str(c) for c in request_contract_ids)
        allowed = [c for c in contract_ids if str(c) in req]
        if allowed:
            contract_ids = allowed

    conds = []
    if scope.user_ids:
        conds.append(Clints.user_id.in_(list(scope.user_ids)))
    if contract_ids:
        allowed_portfolios = (
            db.session.query(Portfolios.id)
            .filter(Portfolios.contract_id.in_(list(contract_ids)))
            .subquery()
        )
        conds.append(Clints.portfolios_id.in_(db.session.query(allowed_portfolios.c.id)))

    if not conds:
        return Clints.user_id == scope.identity
    if len(conds) == 1:
        return conds[0]
    return or_(*conds)


def apply_client_scope(query, scope, request_contract_ids=None):
    """Filter `query` (which must select from/join `Clints`) down to `scope`."""
    cond = scope_condition(scope, request_contract_ids)
    if cond is None:
        return query
    return query.filter(cond)


def can_view_user(scope, user_id):
    """May `scope`'s caller read the client list of `user_id`?

    Self, a supervisor of that user, or SuperAdmin. Used by `/clints/user/<id>`, which the
    legacy implementation left completely unguarded (it only did a Redis key scan, so any
    authenticated account could read any collector's whole book).

    "Supervisor of that user" covers both ways a supervisor is linked: an explicit
    `supervisor_users` row **or** a `supervisor_contracts` row whose contract the target
    user actually works clients in -- the ADMIN test identity, for example, supervises all
    15 contracts but has no `supervisor_users` rows at all. The row set returned by the
    route is additionally intersected with the caller's scope, so a supervisor never sees
    a client outside their contracts even when this returns True.
    """
    user_id = str(user_id)
    if scope.kind == SUPER:
        return True
    if user_id == scope.identity:
        return True
    if scope.kind != SUPERVISOR:
        return False
    if scope.user_ids and user_id in scope.user_ids:
        return True
    if not scope.contract_ids:
        return False
    # cheap EXISTS on ix_clints_user_notarch, not a count
    return db.session.query(
        db.session.query(Clints.id)
        .join(Portfolios, Clints.portfolios_id == Portfolios.id)
        .filter(Clints.user_id == user_id,
                Portfolios.contract_id.in_(list(scope.contract_ids)))
        .exists()
    ).scalar() is True


__all__ = [
    "Scope", "SUPER", "SUPERVISOR", "COLLECTOR", "SCOPE_TTL",
    "resolve_scope", "invalidate_scope", "scope_condition", "apply_client_scope",
    "can_view_user",
]
