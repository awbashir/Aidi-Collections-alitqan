"""Reviewed bulk assignment (Phase 2 stream A, docs/phase2-spec.md, review/phase2-assign.md).

Two kinds, both on the legacy key `clints_sepcifyUser` (the key the legacy clients screen
checks before it shows its assign buttons, so existing grants keep working):

* `assign`       sources `selection`, `filter` (params `{target_user_id, reason?}`) and
                 `spreadsheet` (mapping `account_number` -> case, `login` -> the collector's
                 `users.user_name`; params `{reason?}`).
* `round_robin`  source `query` (params `{portfolio_id | contract_id, collector_ids[], reason?,
                 limit?}`): the unassigned, active, unarchived, unlocked cases of the portfolio or
                 contract that are in the creator's scope, ordered by `(create_at, id)`, dealt
                 to the collectors in the given order, cycled. The planned collector is stored
                 on every row (`details.target_user_id`, `details.position`) at preview and
                 execution applies exactly that plan.

The change is exactly what the legacy assignment writes (`services/bulk_actions._assign`,
`services/imports.run_sepcify_users_by_excel`): `clints.user_id` is set and one
`"SepcifyUsers"` row (`user_id` = the new owner, `clint_id`, `create_by` = the acting user,
i.e. the confirmer) is inserted per changed case, so the legacy assignment-history screen
(`GET /sepcifyusers/clint/<id>`) shows bulk jobs as before. The Phase 1 trigger
`clints_follow_up_owner_trg` (0010) moves the case's open follow-up to the new owner.
On top of legacy: one `audit_events` row per case (`case.assign`, before/after owner,
source `bulk_job`, request_id = the job id), a reviewed preview, and the framework rechecks
(permission, scope, revision, call reservations) plus the kind's own (target still eligible,
case state, round-robin case still unassigned).

**Collector eligibility** (who may receive cases), derived from cawp-db (see the report):
an existing, active user whose role grants one of `COLLECTOR_KEYS` (`collections_currentUser`,
the "my collections" work key held by every role that owns an open case today), and who is
inside the caller's scope (`target_in_scope`): SuperAdmin -> anyone; supervisor -> a
supervised user, the supervisor themself, a user whose `users.contract_id` is a supervised
contract, or a user who owns a case in a supervised contract (the `can_view_user` rule);
anyone else -> only themself.
"""
from __future__ import annotations

from sqlalchemy import text

from core.bulk.common import dumps, scope_sql, valid_uuid
from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome, ParamError
from services.bulk_common import REASON_MAX, reject_unknown
from services.clients_scope import SUPER

ASSIGN_KEYS = ("clints_sepcifyUser",)
COLLECTOR_KEYS = ("collections_currentUser",)
MAX_COLLECTORS = 200

# target problems: code -> message. At preview they are row errors; at execution
# target_out_of_scope is failed (a scope recheck) and the others are skipped/target_not_eligible.
TARGET_PROBLEMS = {
    "unknown_user": "the collector does not exist",
    "unknown_login": "no user has this login",
    "target_inactive": "the collector's account is inactive",
    "target_not_collector": "the collector's role cannot hold cases (no collections_currentUser)",
    "target_out_of_scope": "the collector is outside your scope",
}


# =========================================================================== targets
def load_users(session, ids=None, logins=None) -> list[dict]:
    """Users by id and/or exact login, with the collector flag (role grants a COLLECTOR_KEY)."""
    ids = sorted({str(i) for i in (ids or ()) if i})
    logins = sorted({str(x) for x in (logins or ()) if x is not None})
    if not ids and not logins:
        return []
    rows = session.execute(text("""
        SELECT u.id::text AS id, u.user_name, btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')) AS name,
               u.is_active, u.contract_id::text AS contract_id,
               EXISTS (SELECT 1 FROM unnest(CAST(:ck AS text[])) k
                        WHERE (r.permission::jsonb) -> k = 'true'::jsonb) AS is_collector
          FROM users u LEFT JOIN roles r ON r.id = u.role_id
         WHERE u.id = ANY(CAST(:ids AS uuid[])) OR u.user_name = ANY(CAST(:logins AS text[]))
    """), {"ck": list(COLLECTOR_KEYS), "ids": ids, "logins": logins}).mappings().all()
    return [dict(r) for r in rows]


def target_in_scope(session, scope, user_ids) -> set[str]:
    """The subset of `user_ids` the caller's scope covers (see the module docstring)."""
    user_ids = {str(u) for u in user_ids if u}
    if not user_ids:
        return set()
    if scope.kind == SUPER:
        return set(user_ids)
    own = set(scope.user_ids or ()) | {scope.identity}
    ok = user_ids & own
    rest = sorted(user_ids - ok)
    if rest and scope.contract_ids:
        ok |= {r[0] for r in session.execute(text("""
            WITH sp AS (SELECT id FROM portfolios WHERE contract_id = ANY(CAST(:cids AS uuid[])))
            SELECT u.id::text FROM users u
             WHERE u.id = ANY(CAST(:ids AS uuid[]))
               AND (u.contract_id = ANY(CAST(:cids AS uuid[]))
                    OR EXISTS (SELECT 1 FROM clints c
                                WHERE c.user_id = u.id AND c.portfolios_id IN (SELECT id FROM sp)))
        """), {"cids": sorted(scope.contract_ids), "ids": rest})}
    return ok


def classify_targets(session, scope, users: list[dict]) -> dict[str, str | None]:
    """{user_id: None (eligible) | problem code} for loaded users."""
    in_scope = target_in_scope(session, scope, [u["id"] for u in users])
    out = {}
    for u in users:
        if not u["is_active"]:
            out[u["id"]] = "target_inactive"
        elif not u["is_collector"]:
            out[u["id"]] = "target_not_collector"
        elif u["id"] not in in_scope:
            out[u["id"]] = "target_out_of_scope"
        else:
            out[u["id"]] = None
    return out


def eligible_collectors(session, scope) -> list[dict]:
    """Active collectors (role grants a COLLECTOR_KEY) the caller may assign to, by name."""
    rows = [dict(r) for r in session.execute(text("""
        SELECT u.id::text AS id, u.user_name, btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')) AS name
          FROM users u JOIN roles r ON r.id = u.role_id
         WHERE u.is_active
           AND EXISTS (SELECT 1 FROM unnest(CAST(:ck AS text[])) k WHERE (r.permission::jsonb) -> k = 'true'::jsonb)
         ORDER BY 3, 2
    """), {"ck": list(COLLECTOR_KEYS)}).mappings()]
    allowed = target_in_scope(session, scope, [r["id"] for r in rows])
    return [r for r in rows if r["id"] in allowed]


def _target_details(u: dict) -> dict:
    return {"target_user_id": u["id"], "target_login": u["user_name"], "target_name": u["name"] or None}


def optional_reason(params: dict, field: str = "reason"):
    value = params.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ParamError({field: "invalid"}, f"{field} must be text")
    value = value.strip()
    if len(value) > REASON_MAX:
        raise ParamError({field: "too_long"}, f"{field} is longer than {REASON_MAX} characters")
    return value


def _owner_logins(session, ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    return {r[0]: r[1] for r in session.execute(text(
        "SELECT id::text, user_name FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})}


def _case_state_problem(case: dict, *, need_active: bool) -> tuple[str, str] | None:
    if case["is_archive"]:
        return "case_archived", "the case is archived"
    if case["is_locked"]:
        return "case_locked", "the case is locked"
    if need_active and not case["is_active"]:
        return "case_inactive", "the case is not active"
    return None


# =========================================================================== shared base
class _AssignBase(BulkKind):
    permissions = ASSIGN_KEYS
    need_unassigned = False     # round-robin: only cases that have no owner
    need_active = False         # round-robin: only active cases

    def _apply(self, rows, ctx, targets: dict[int, str]) -> dict[int, Outcome]:
        """rows: locked current snapshots (framework rechecks passed); targets: row.id -> user id.
        Rechecks the target and the case state, then writes clints.user_id + "SepcifyUsers"
        + audit exactly once per changed case."""
        session = ctx.session
        users = {u["id"]: u for u in load_users(session, ids=set(targets.values()))}
        problems = classify_targets(session, ctx.scope, list(users.values()))
        out, todo = {}, []
        for r in rows:
            t = targets.get(r.id)
            u = users.get(t)
            problem = "unknown_user" if u is None else problems.get(t)
            state = _case_state_problem(r.case, need_active=self.need_active)
            if problem == "target_out_of_scope":
                out[r.id] = Outcome.failed(problem, "the collector is outside the confirming user's scope",
                                           {"target_user_id": t})
            elif problem is not None:
                out[r.id] = Outcome.skipped("target_not_eligible", TARGET_PROBLEMS[problem],
                                            {"target_user_id": t, "target_problem": problem})
            elif state is not None:
                out[r.id] = Outcome.skipped(*state)
            elif self.need_unassigned and r.case["user_id"] is not None:
                out[r.id] = Outcome.skipped("already_assigned", "the case already has a collector")
            elif r.case["user_id"] == t:
                out[r.id] = Outcome.skipped("already_assigned", "the case is already assigned to this collector")
            else:
                todo.append(r)
        if not todo:
            return out
        data = [{"id": r.case_id, "t": targets[r.id]} for r in todo]
        only_unassigned = "AND c.user_id IS NULL" if self.need_unassigned else ""
        changed = {row[0] for row in session.execute(text(f"""
            UPDATE clints c SET user_id = d.t
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, t uuid)
             WHERE c.id = d.id AND c.user_id IS DISTINCT FROM d.t {only_unassigned}
            RETURNING c.id::text
        """), {"rows": dumps(data)})}
        if changed:
            session.execute(text("""
                INSERT INTO "SepcifyUsers" (user_id, clint_id, create_by)
                SELECT d.t, d.id, CAST(:actor AS uuid)
                  FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, t uuid)
                 WHERE d.id = ANY(CAST(:changed AS uuid[]))
            """), {"rows": dumps(data), "actor": ctx.actor_id, "changed": sorted(changed)})
        before_logins = _owner_logins(session, [r.case["user_id"] for r in todo])
        reason = (ctx.params or {}).get("reason")
        for r in todo:
            t = targets[r.id]
            if r.case_id not in changed:
                out[r.id] = Outcome.skipped("already_assigned", "the owner changed concurrently")
                continue
            before = r.case["user_id"]
            after = {"user_id": t, "user_name": users[t]["user_name"]}
            if self.need_unassigned:
                after["round_robin_position"] = (r.details or {}).get("position")
            ctx.audit(r, "case.assign", before={"user_id": before, "user_name": before_logins.get(before)},
                      after=after, reason=reason)
            out[r.id] = Outcome.succeeded({"owner_after_login": users[t]["user_name"]})
        return out

    # ---- per-target totals (see review/phase2-assign.md: preview_summary hook) -----------
    def summarize(self, session, job: dict, rows_pred: str = "TRUE", params: dict | None = None) -> dict:
        """{"by_target": [{user_id, user_name, name, planned, succeeded, skipped, failed}]}.
        `planned` = eligible rows. `rows_pred`/`params`: the framework's row-visibility filter
        over `bulk_job_rows r LEFT JOIN clints c`."""
        rows = session.execute(text(f"""
            SELECT r.details->>'target_user_id' AS uid,
                   min(r.details->>'target_login') AS login, min(r.details->>'target_name') AS name,
                   count(*) FILTER (WHERE r.eligibility = 'eligible') AS planned,
                   count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'skipped') AS skipped,
                   count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'failed') AS failed
              FROM bulk_job_rows r LEFT JOIN clints c ON c.id = r.case_id
             WHERE r.job_id = CAST(:j AS uuid) AND r.details ? 'target_user_id' AND {rows_pred}
             GROUP BY 1
        """), {"j": str(job["id"]), **(params or {})}).mappings().all()
        found = {r["uid"]: dict(r) for r in rows}
        order = list((job.get("params") or {}).get("collector_ids") or [])
        order += sorted((u for u in found if u not in order), key=lambda u: (found[u]["login"] or "", u))
        names = {u["id"]: u for u in load_users(session, ids=[u for u in order if u not in found])}
        out = []
        for u in order:
            r = found.get(u)
            if r is None:
                n = names.get(u, {})
                r = {"login": n.get("user_name"), "name": n.get("name"), "planned": 0, "succeeded": 0,
                     "skipped": 0, "failed": 0}
            out.append({"user_id": u, "user_name": r["login"], "name": r["name"], "planned": r["planned"],
                        "succeeded": r["succeeded"], "skipped": r["skipped"], "failed": r["failed"]})
        return {"by_target": out}


# =========================================================================== assign
class AssignKind(_AssignBase):
    key = "assign"
    label_en, label_ar = "Assign cases to a collector", "تخصيص العملاء لمحصل"
    sources = ("selection", "filter", "spreadsheet")
    filter_lists = ("clients",)
    mapping_fields = (
        MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True, max_length=200),
        MappingField("login", "Collector login (user name)", "اسم المستخدم للمحصل", required=True, max_length=200,
                     description="users.user_name of the collector who receives the case (exact match)"),
    )
    match_by = "account_number"
    result_columns = ("owner_before_login", "target_login", "target_name", "target_user_id", "owner_after_login")

    def validate_params(self, params, ctx):
        if ctx.source == "spreadsheet":
            reject_unknown(params, {"reason"})
            reason = optional_reason(params)
            return {"reason": reason} if reason else {}
        reject_unknown(params, {"target_user_id", "reason"})
        raw = params.get("target_user_id")
        if raw in (None, ""):
            raise ParamError({"target_user_id": "required"}, "target_user_id is required")
        tid = valid_uuid(raw) if isinstance(raw, str) else None
        if tid is None:
            raise ParamError({"target_user_id": "invalid"}, "target_user_id must be a user id")
        users = load_users(ctx.session, ids=[tid])
        if not users:
            raise ParamError({"target_user_id": "unknown_user"}, TARGET_PROBLEMS["unknown_user"])
        problem = classify_targets(ctx.session, ctx.scope, users)[tid]
        if problem:
            raise ParamError({"target_user_id": problem}, TARGET_PROBLEMS[problem])
        reason = optional_reason(params)
        out = {"target_user_id": tid}
        if reason:
            out["reason"] = reason
        return out

    def _row_targets(self, rows, ctx) -> tuple[dict[int, dict | str], dict[str, dict]]:
        """row.id -> user dict, or a problem code; plus the loaded users by id."""
        session = ctx.session
        if ctx.job["source"] != "spreadsheet":
            users = load_users(session, ids=[ctx.params["target_user_id"]])
            by_id = {u["id"]: u for u in users}
            u = by_id.get(ctx.params["target_user_id"])
            return {r.id: (u if u is not None else "unknown_user") for r in rows}, by_id
        logins = {(r.mapped or {}).get("login") for r in rows}
        users = load_users(session, logins=[x for x in logins if x])
        by_login = {u["user_name"]: u for u in users}
        return {r.id: by_login.get((r.mapped or {}).get("login"), "unknown_login") for r in rows}, \
            {u["id"]: u for u in users}

    def _conflicts(self, rows, ctx) -> dict[str, dict]:
        """Spreadsheet: case_id -> {rows, logins} when a later duplicate row names another login."""
        if ctx.job["source"] != "spreadsheet":
            return {}
        res = ctx.session.execute(text("""
            SELECT r.case_id::text, array_agg(r.row_no ORDER BY r.row_no),
                   array_agg(DISTINCT coalesce(r.mapped->>'login', ''))
              FROM bulk_job_rows r
             WHERE r.job_id = CAST(:j AS uuid) AND r.eligibility = 'excluded'
               AND r.eligibility_code = 'duplicate_case' AND r.case_id = ANY(CAST(:ids AS uuid[]))
             GROUP BY 1
        """), {"j": str(ctx.job["id"]), "ids": [r.case_id for r in rows]}).all()
        return {c: {"rows": list(nos), "logins": list(logins)} for c, nos, logins in res}

    def prepare(self, rows, ctx):
        if not rows:
            return {}
        targets, users = self._row_targets(rows, ctx)
        problems = classify_targets(ctx.session, ctx.scope, list(users.values()))
        dups = self._conflicts(rows, ctx)
        owners = _owner_logins(ctx.session, [r.case["user_id"] for r in rows])
        out = {}
        for r in rows:
            t = targets[r.id]
            owner = r.case["user_id"]
            base = {"owner_before_id": owner, "owner_before_login": owners.get(owner)}
            if isinstance(t, str):                        # unknown_user / unknown_login
                out[r.id] = Eligibility.error(t, TARGET_PROBLEMS[t], {**base, "login": (r.mapped or {}).get("login")})
                continue
            base.update(_target_details(t))
            problem = problems.get(t["id"])
            dup = dups.get(r.case_id)
            state = _case_state_problem(r.case, need_active=self.need_active)
            if problem:
                out[r.id] = Eligibility.error(problem, TARGET_PROBLEMS[problem], base)
            elif dup and any(x != t["user_name"] for x in dup["logins"]):
                out[r.id] = Eligibility.error(
                    "conflicting_logins", "the same case is on later rows with a different login",
                    {**base, "duplicate_rows": dup["rows"], "duplicate_logins": dup["logins"]})
            elif state is not None:
                out[r.id] = Eligibility.excluded(*state, base)
            elif owner == t["id"]:
                out[r.id] = Eligibility.excluded("already_assigned", "the case is already assigned to this collector",
                                                 base)
            else:
                out[r.id] = Eligibility.eligible(base)
        return out

    def execute_batch(self, rows, ctx):
        if ctx.job["source"] == "spreadsheet":
            targets = {r.id: (r.details or {}).get("target_user_id") for r in rows}
        else:
            targets = {r.id: ctx.params["target_user_id"] for r in rows}
        return self._apply(rows, ctx, targets)


# =========================================================================== round-robin
class RoundRobinKind(_AssignBase):
    key = "round_robin"
    label_en, label_ar = "Round-robin assignment of unassigned cases", "توزيع العملاء غير المخصصين بالتناوب"
    sources = ("query",)
    match_by = None
    need_unassigned = True
    need_active = True
    result_columns = ("position", "target_login", "target_name", "target_user_id", "owner_after_login")

    def validate_params(self, params, ctx):
        from core.bulk import settings as _settings

        reject_unknown(params, {"portfolio_id", "contract_id", "collector_ids", "reason", "limit"})
        session, scope = ctx.session, ctx.scope
        has_p = params.get("portfolio_id") not in (None, "")
        has_c = params.get("contract_id") not in (None, "")
        if has_p == has_c:
            code = "exclusive" if has_p else "required"
            raise ParamError({"portfolio_id": code, "contract_id": code},
                             "give exactly one of portfolio_id or contract_id")
        out = {}
        field = "portfolio_id" if has_p else "contract_id"
        ref = valid_uuid(params[field]) if isinstance(params[field], str) else None
        if ref is None:
            raise ParamError({field: "invalid"}, f"{field} must be an id")
        if has_p:
            contract = session.execute(text("SELECT contract_id::text FROM portfolios WHERE id = CAST(:p AS uuid)"),
                                       {"p": ref}).first()
            contract_id = contract[0] if contract else None
        else:
            contract = session.execute(text("SELECT id::text FROM contracts WHERE id = CAST(:c AS uuid)"),
                                       {"c": ref}).first()
            contract_id = contract[0] if contract else None
        # unassigned cases are only in a scope through a supervised contract (or SuperAdmin):
        # a portfolio/contract outside it reads as not found
        if contract is None or (scope.kind != SUPER and contract_id not in (scope.contract_ids or ())):
            raise ParamError({field: "not_found"}, f"{field.split('_')[0]} not found or not in your scope")
        out[field] = ref

        ids = params.get("collector_ids")
        if ids is None or ids == []:
            raise ParamError({"collector_ids": "required"}, "collector_ids is required")
        if not isinstance(ids, list) or len(ids) > MAX_COLLECTORS \
                or not all(isinstance(i, str) and valid_uuid(i) for i in ids):
            raise ParamError({"collector_ids": "invalid"},
                             f"collector_ids must be a list of 1-{MAX_COLLECTORS} user ids")
        ids = [valid_uuid(i) for i in ids]
        if len(set(ids)) != len(ids):
            raise ParamError({"collector_ids": "duplicate"}, "a collector is listed twice")
        users = {u["id"]: u for u in load_users(session, ids=ids)}
        problems = classify_targets(session, scope, list(users.values()))
        bad = {i: ("unknown_user" if i not in users else problems[i]) for i in ids
               if i not in users or problems[i]}
        if bad:
            raise ParamError({"collector_ids": bad}, "some collectors cannot receive cases")
        out["collector_ids"] = ids

        limit = params.get("limit")
        if limit is not None:
            max_rows = _settings.get().max_rows
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_rows:
                raise ParamError({"limit": "invalid"}, f"limit must be a whole number from 1 to {max_rows}")
            out["limit"] = limit
        reason = optional_reason(params)
        if reason:
            out["reason"] = reason
        return out

    def resolve_query(self, params, ctx):
        pred, bind = scope_sql(ctx.session, ctx.scope, "c", prefix="rrsc")
        if params.get("portfolio_id"):
            where, bind["rr_ref"] = "c.portfolios_id = CAST(:rr_ref AS uuid)", params["portfolio_id"]
        else:
            where = "c.portfolios_id IN (SELECT p.id FROM portfolios p WHERE p.contract_id = CAST(:rr_ref AS uuid))"
            bind["rr_ref"] = params["contract_id"]
        limit = ""
        if params.get("limit"):
            limit, bind["rr_limit"] = "LIMIT :rr_limit", int(params["limit"])
        sql = f"""
            SELECT c.id AS case_id FROM clints c
             WHERE {where} AND c.user_id IS NULL AND c.is_active AND NOT c.is_archive AND NOT c.is_locked
               AND {pred}
             ORDER BY c.create_at, c.id {limit}
        """
        return sql, bind

    def _positions(self, ctx) -> dict[int, int]:
        """row.id -> 1-based position of its case in (create_at, id) order over ALL the job's
        rows. Independent of row_no and of later eligibility, so the plan is the same however
        the rows were numbered or chunked. Cached on the validation context."""
        cached = getattr(ctx, "_rr_positions", None)
        if cached is not None:
            return cached
        res = ctx.session.execute(text("""
            SELECT r.id, row_number() OVER (ORDER BY c.create_at, c.id)
              FROM bulk_job_rows r JOIN clints c ON c.id = r.case_id
             WHERE r.job_id = CAST(:j AS uuid)
        """), {"j": str(ctx.job["id"])}).all()
        cached = {rid: int(pos) for rid, pos in res}
        setattr(ctx, "_rr_positions", cached)
        return cached

    def plan(self, positions: dict[int, int], collectors: list[str]) -> dict[int, str]:
        n = len(collectors)
        return {rid: collectors[(pos - 1) % n] for rid, pos in positions.items()}

    def prepare(self, rows, ctx):
        if not rows:
            return {}
        collectors = ctx.params["collector_ids"]
        positions = self._positions(ctx)
        users = {u["id"]: u for u in load_users(ctx.session, ids=collectors)}
        problems = classify_targets(ctx.session, ctx.scope, list(users.values()))
        out = {}
        for r in rows:
            pos = positions.get(r.id)
            if pos is None:                                   # case vanished meanwhile
                out[r.id] = Eligibility.excluded("case_changed", "the case changed during validation")
                continue
            t = collectors[(pos - 1) % len(collectors)]
            u = users.get(t)
            details = {"position": pos, **(_target_details(u) if u else {"target_user_id": t})}
            problem = "unknown_user" if u is None else problems.get(t)
            state = _case_state_problem(r.case, need_active=True)
            if problem:
                out[r.id] = Eligibility.error(problem, TARGET_PROBLEMS[problem], details)
            elif r.case["user_id"] is not None:
                out[r.id] = Eligibility.excluded("already_assigned", "the case already has a collector",
                                                 {**details, "owner_before_id": r.case["user_id"]})
            elif state is not None:
                out[r.id] = Eligibility.excluded(*state, details)
            else:
                out[r.id] = Eligibility.eligible(details)
        return out

    def execute_batch(self, rows, ctx):
        return self._apply(rows, ctx, {r.id: (r.details or {}).get("target_user_id") for r in rows})


KINDS = (AssignKind(), RoundRobinKind())
