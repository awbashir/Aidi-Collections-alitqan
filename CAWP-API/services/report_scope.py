"""Per-route visibility rules for the report endpoints.

These reproduce the legacy `is_super` blocks **exactly** — neither stricter nor looser.
Two distinct rules are in play across the routes this package serves:

  * `supervisor_scope()` — /payments, /payments/export, /payments/approval_status(+export).
    `Supervisor.query.filter_by(user_id=<identity>).first()`:
      - no supervisor row  -> `Payments.user_id == identity` (own payments only)
      - supervisor row     -> supervised `SupervisorUser.user_id` list + self,
                              OR (when the supervisor has contracts)
                              `Portfolios.contract_id IN <supervised contracts>`
    A user whose role is `SuperAdmin` sees every payment (as in services/clients_scope.py);
    before 2026-10-20 there was no role short-circuit here and an admin saw everything only
    if they were also a supervisor over every contract, so the a-alaoula SuperAdmins (no
    supervisor rows) saw empty payment pages.

  * identity scope — /comments/current_user(+export), /payments/user(+export),
    /payment_date/currentUser(+export): a plain `user_id == identity`.

  * no scope at all — /comments(+export), /payment_date(+export),
    /comments/clint/<id>, /payments/clint/<id>, /payment_date/clint/<id>.
    Those are jwt-only in the legacy code (review S5 flags them as IDOR, but widening or
    narrowing them here would be a wire change, so they are left as they are and the
    finding is carried forward in the report).
"""
import logging

from flask_jwt_extended import get_jwt_identity

from extensions import db
from models import Supervisor, SupervisorContract, SupervisorUser

log = logging.getLogger("cawp.scope")


class Scope:
    """Resolved supervisor scope for the current identity."""

    __slots__ = ("identity", "supervisor_id", "user_ids", "contract_ids", "is_all")

    def __init__(self, identity, supervisor_id=None, user_ids=None, contract_ids=None, is_all=False):
        self.identity = identity
        self.supervisor_id = supervisor_id
        self.user_ids = user_ids or []
        self.contract_ids = contract_ids or []
        self.is_all = is_all            # SuperAdmin: every payment

    @property
    def is_supervisor(self):
        return self.supervisor_id is not None

    def cache_tag(self):
        """Stable, short identity tag for a cache key."""
        if self.is_all:
            return "all"
        if not self.is_supervisor:
            return "u:%s" % self.identity
        return "s:%s" % self.supervisor_id


def current_identity():
    return get_jwt_identity()


def supervisor_scope(identity=None):
    """Two small queries, matching app.py:3983-4015 / 4194-4245 / 4356-4396."""
    identity = identity or current_identity()
    from models import Role, User
    role = (db.session.query(Role.name).join(User, User.role_id == Role.id)
            .filter(User.id == identity).scalar())
    if role == "SuperAdmin":
        return Scope(identity, is_all=True)
    is_super = Supervisor.query.filter_by(user_id=identity).first()
    if is_super is None:
        return Scope(identity)
    user_ids = [
        r.user_id
        for r in db.session.query(SupervisorUser.user_id)
        .filter(SupervisorUser.supervisor_id == is_super.id)
        .all()
    ]
    user_ids.append(identity)
    contract_ids = [
        r.contract_id
        for r in db.session.query(SupervisorContract.contract_id)
        .filter(SupervisorContract.supervisor_id == is_super.id)
        .all()
    ]
    return Scope(identity, is_super.id, user_ids, contract_ids)


def payments_scope_clause(scope, payments_model, portfolios_model):
    """The `or_(user_id IN ..., contract_id IN ...)` filter used by the payments routes.

    Returns a SQLAlchemy clause. The caller must already have joined
    Payments -> Clints -> Portfolios when `scope.contract_ids` is non-empty.
    """
    if scope.is_all:
        return db.true()
    if not scope.is_supervisor:
        return payments_model.user_id == scope.identity
    if scope.contract_ids:
        return db.or_(
            payments_model.user_id.in_(scope.user_ids),
            portfolios_model.contract_id.in_(scope.contract_ids),
        )
    return payments_model.user_id.in_(scope.user_ids)


def needs_contract_join(scope):
    return scope.is_supervisor and bool(scope.contract_ids) and not scope.is_all


__all__ = [
    "Scope", "current_identity", "supervisor_scope",
    "payments_scope_clause", "needs_contract_join",
]
