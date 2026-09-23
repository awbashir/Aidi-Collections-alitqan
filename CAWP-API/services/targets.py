"""Targets with history and target-vs-actual (Phase 3, stream C; migrations 0034/0035).

Legacy definition (the only place legacy computes "achievement" is the React dashboard;
the API just ships the raw columns, api/dashboard.py `_allcollector_payload` and
`dashbord_collector_v2`):

  CAWP-frontend/src/views/dashboard2/components/AllCollectorsKPI.tsx:195-237 (all collectors,
  shown with `dashbord_admin`, ProjectDashboard.tsx:116) and KPICollector.tsx:81-122 (own
  KPI, shown with `dashbord_user`, ProjectDashboard.tsx:88):

    base   = sum(clints.amount) of the collector's CURRENT, unarchived cases
             (original debt, not outstanding; AllCollectors: every contract, KPICollector:
             only cases in the user's own contract)
    target = user's own target when target_amount > 0 or target_percent > 0:
               target_is_percentage and target_percent > 0 -> Math.round(base * pct / 100)
               not target_is_percentage and target_amount > 0 -> target_amount
             else (or when neither branch applies) the user's contract target:
               monthly_target_percent > 0 -> Math.round(base * pct / 100)   (flag ignored)
               monthly_target_amount > 0  -> monthly_target_amount
             else 0
    achieved = sum("Payments".amount) with approval_status = true and payment_date in the
               current month, where "Payments".user_id (the RECORDER) is the collector
               (KPICollector also requires the case to be in the user's contract). The
               month is the server's naive month start (UTC in Docker) on the API side and
               the browser month on the client side.
    achievement % = achieved / target * 100 (0 when target is 0); remaining = target - achieved.
    The "all collectors" totals add every collector's target, so a contract amount target
    is counted once per collector of that contract that has no own target.

So "percent of what": percent of the original debt (clints.amount) of the collector's
currently assigned, unarchived cases - evaluated at view time, not at month start.

v2 target-vs-actual (`achievement`), used by the Team screen and RPT-19/22:

  * month M = Asia/Riyadh civil month; a payment counts in M when the Riyadh civil date of
    "Payments".payment_date is in M;
  * approved recovery = approval_status = true AND is_deleted = false AND no
    payment_reversals row; each payment is round(amount::numeric, 2), sums are exact numeric;
  * `actual` = by the case's CURRENT owner (clints.user_id), per the Phase 3 spec;
    `actual_recorded` = by the payment recorder ("Payments".user_id), the legacy KPI rule,
    for reconciliation with the legacy dashboard;
  * per contract: approved recovery on cases of the contract's portfolios;
  * the target in force for M comes from `targets` (see effective_targets) and is turned into
    SAR with the legacy rule above: percent targets use the CURRENT unarchived case totals as
    the base (legacy keeps no history of it) and are rounded to whole riyals half-up
    (= JavaScript Math.round for positive values). The user's CURRENT contract supplies the
    fallback contract target.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError

SUBJECTS = ("user", "contract")
KINDS = ("amount", "percent", "none")
MAX_AMOUNT = Decimal("999999999999.99")
MAX_PERCENT = Decimal("100")
CENT = Decimal("0.01")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})(?:-01)?$")

_SUBJECT_SQL = {
    "user": ("users", "target_amount", "target_percent", "target_is_percentage"),
    "contract": ("contracts", "monthly_target_amount", "monthly_target_percent",
                 "monthly_target_is_percentage"),
}


# --------------------------------------------------------------------------- basics
def money(v) -> str | None:
    return None if v is None else str(Decimal(v).quantize(CENT))


def pct_str(v) -> str | None:
    return None if v is None else format(Decimal(v).normalize(), "f")


def month_str(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def current_month(at: dt.datetime | None = None) -> dt.date:
    return riyadh.today(at).replace(day=1)


def parse_month(value, field: str = "month") -> dt.date:
    m = _MONTH_RE.match(str(value or "").strip())
    if not m:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM", {field: "invalid"})
    year, mon = int(m.group(1)), int(m.group(2))
    if not (1 <= mon <= 12 and 2000 <= year <= 2100):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM", {field: "invalid"})
    return dt.date(year, mon, 1)


def next_month(d: dt.date) -> dt.date:
    return dt.date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def legacy_kind(subject: str, amount, percent, is_pct) -> tuple[str, Decimal]:
    """Python twin of SQL cawp_target_kind (0034): the legacy dashboard's reading."""
    amount = Decimal(str(amount or 0))
    percent = Decimal(str(percent or 0))
    if subject == "user":
        if is_pct and percent > 0:
            return "percent", percent
        if not is_pct and amount > 0:
            return "amount", amount.quantize(CENT, ROUND_HALF_UP)
        return "none", Decimal(0)
    if percent > 0:
        return "percent", percent
    if amount > 0:
        return "amount", amount.quantize(CENT, ROUND_HALF_UP)
    return "none", Decimal(0)


def validate_value(kind, value) -> Decimal:
    if kind not in KINDS:
        raise ApiError(422, VALIDATION_FAILED, "kind must be amount, percent or none", {"kind": "invalid"})
    if kind == "none":
        if value is not None and not isinstance(value, bool):
            try:
                zero = Decimal(str(value).strip()) == 0
            except (InvalidOperation, ValueError):
                zero = False
            if not zero:
                raise ApiError(422, VALIDATION_FAILED, "a 'none' target has no value",
                               {"value": "must_be_empty"})
        elif isinstance(value, bool):
            raise ApiError(422, VALIDATION_FAILED, "a 'none' target has no value", {"value": "must_be_empty"})
        return Decimal(0)
    if value is None or isinstance(value, bool):
        raise ApiError(422, VALIDATION_FAILED, "value is required", {"value": "required"})
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ApiError(422, VALIDATION_FAILED, "value must be a number", {"value": "invalid"}) from None
    if not d.is_finite() or d <= 0:
        raise ApiError(422, VALIDATION_FAILED, "value must be greater than 0 (use kind 'none' for no target)",
                       {"value": "not_positive"})
    if kind == "amount":
        if d > MAX_AMOUNT or d != d.quantize(CENT):
            raise ApiError(422, VALIDATION_FAILED, "amount must have at most 2 decimals and 12 digits",
                           {"value": "invalid_amount"})
        return d.quantize(CENT)
    if d > MAX_PERCENT or d != d.quantize(Decimal("0.0001")):
        raise ApiError(422, VALIDATION_FAILED, "percent must be at most 100 with at most 4 decimals",
                       {"value": "invalid_percent"})
    return d


# --------------------------------------------------------------------------- reads
_ROW_COLS = """id, subject_type, subject_id::text AS subject_id, month, revision, kind, value,
               raw_amount, raw_percent, raw_is_percentage, source, set_by::text AS set_by,
               set_at, reason"""


def row_dict(r) -> dict:
    m = r["month"]
    return {
        "id": r["id"],
        "subject_type": r["subject_type"],
        "subject_id": r["subject_id"],
        "month": month_str(m),
        "revision": r["revision"],
        "kind": r["kind"],
        "value": money(r["value"]) if r["kind"] == "amount" else pct_str(r["value"]),
        "legacy": ({"amount": r["raw_amount"], "percent": r["raw_percent"],
                    "is_percentage": r["raw_is_percentage"]}
                   if r["source"] != "v2" else None),
        "source": r["source"],
        "imported_baseline": r["source"] == "imported_baseline",
        "set_by": r["set_by"],
        "set_at": r["set_at"].isoformat() if r["set_at"] else None,
        "reason": r["reason"],
    }


def subject_exists(session, subject: str, subject_id) -> bool:
    table = _SUBJECT_SQL[subject][0]
    return session.execute(text(f"SELECT 1 FROM {table} WHERE id = CAST(:id AS uuid)"),
                           {"id": str(subject_id)}).first() is not None


def legacy_current(session, subject: str, subject_id) -> dict | None:
    table, a, p, f = _SUBJECT_SQL[subject]
    r = session.execute(text(f"SELECT {a} AS amount, {p} AS percent, {f} AS is_pct FROM {table} "
                             f"WHERE id = CAST(:id AS uuid)"), {"id": str(subject_id)}).mappings().first()
    if r is None:
        return None
    kind, value = legacy_kind(subject, r["amount"], r["percent"], r["is_pct"])
    return {"amount": r["amount"], "percent": r["percent"], "is_percentage": r["is_pct"],
            "kind": kind, "value": money(value) if kind == "amount" else pct_str(value)}


def history(session, subject: str, subject_id) -> list[dict]:
    rows = session.execute(text(f"""
        SELECT {_ROW_COLS} FROM targets
         WHERE subject_type = :s AND subject_id = CAST(:id AS uuid)
         ORDER BY month DESC, revision DESC"""), {"s": subject, "id": str(subject_id)}).mappings().all()
    return [row_dict(r) for r in rows]


def month_revision(session, subject: str, subject_id, month: dt.date) -> int:
    r = session.execute(text("""SELECT coalesce(max(revision), 0) FROM targets
                                 WHERE subject_type = :s AND subject_id = CAST(:id AS uuid) AND month = :m"""),
                        {"s": subject, "id": str(subject_id), "m": month}).first()
    return int(r[0])


def effective_targets(session, subject: str, ids, month: dt.date) -> dict:
    """{subject_id: {"kind", "value": Decimal, "revision", "month", "source", "known": True}
    or {"known": False}} for the target in force in `month`.

    In force = highest revision of the latest month <= `month` that has rows. With no row:
    the legacy columns when `month` is the current month or later (they are the current
    value), otherwise unknown (history starts at migration 0035)."""
    ids = [str(i) for i in ids]
    if not ids:
        return {}
    rows = session.execute(text("""
        SELECT DISTINCT ON (subject_id) subject_id::text AS sid, kind, value, revision, month, source
          FROM targets
         WHERE subject_type = :s AND subject_id = ANY(CAST(:ids AS uuid[])) AND month <= :m
         ORDER BY subject_id, month DESC, revision DESC"""),
        {"s": subject, "ids": ids, "m": month}).mappings().all()
    out = {r["sid"]: {"kind": r["kind"], "value": Decimal(r["value"]), "revision": r["revision"],
                      "month": month_str(r["month"]), "source": r["source"], "known": True}
           for r in rows}
    missing = [i for i in ids if i not in out]
    if missing:
        if month >= current_month():
            table, a, p, f = _SUBJECT_SQL[subject]
            for r in session.execute(text(f"""SELECT id::text AS sid, {a} AS amount, {p} AS percent,
                                                     {f} AS is_pct FROM {table}
                                               WHERE id = ANY(CAST(:ids AS uuid[]))"""),
                                     {"ids": missing}).mappings():
                kind, value = legacy_kind(subject, r["amount"], r["percent"], r["is_pct"])
                out[r["sid"]] = {"kind": kind, "value": value, "revision": 0, "month": None,
                                 "source": "legacy_columns", "known": True}
        for i in missing:
            out.setdefault(i, {"known": False})
    return out


def target_view(t: dict | None) -> dict | None:
    if not t or not t.get("known"):
        return {"known": False}
    return {"known": True, "kind": t["kind"],
            "value": money(t["value"]) if t["kind"] == "amount" else pct_str(t["value"]),
            "revision": t["revision"], "from_month": t["month"], "source": t["source"]}


# --------------------------------------------------------------------------- writes
def _lock_subject(session, subject, subject_id):
    table = _SUBJECT_SQL[subject][0]
    return session.execute(text(f"SELECT id FROM {table} WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                           {"id": str(subject_id)}).first()


def set_target(session, subject: str, subject_id, month: dt.date, kind: str, value, *,
               reason: str, expected_revision: int, actor, request_id=None,
               idempotency_key=None, today_month: dt.date | None = None) -> dict:
    """Append a v2 revision for (subject, month). Past months are locked. For the current
    month the legacy columns are updated too (without a second, captured row). Does not
    commit. Raises ApiError / core.revisions.RevisionConflict."""
    from core import audit
    from core.revisions import RevisionConflict, RevisionNotFound

    if subject not in SUBJECTS:
        raise ValueError(subject)
    reason = (reason or "").strip()
    if not reason:
        raise ApiError(422, VALIDATION_FAILED, "reason is required", {"reason": "required"})
    if len(reason) > 1000:
        raise ApiError(422, VALIDATION_FAILED, "reason is too long", {"reason": "too_long"})
    value = validate_value(kind, value)
    now_month = today_month or current_month()
    if month < now_month:
        raise ApiError(409, "month_locked", "targets of past months are locked",
                       {"month": month_str(month), "current_month": month_str(now_month)})
    if _lock_subject(session, subject, subject_id) is None:
        raise RevisionNotFound(subject, subject_id)
    current = month_revision(session, subject, subject_id, month)
    if current != expected_revision:
        raise RevisionConflict("target", f"{subject}:{subject_id}:{month_str(month)}",
                               expected_revision, current)
    before = effective_targets(session, subject, [subject_id], month).get(str(subject_id))
    row = session.execute(text(f"""
        INSERT INTO targets (subject_type, subject_id, month, revision, kind, value, source,
                             set_by, reason, request_id, idempotency_key)
        VALUES (:s, CAST(:id AS uuid), :m, :rev, :k, :v, 'v2', CAST(:actor AS uuid), :reason, :rid, :ikey)
        RETURNING {_ROW_COLS}"""),
        {"s": subject, "id": str(subject_id), "m": month, "rev": current + 1, "k": kind, "v": value,
         "actor": str(actor), "reason": reason, "rid": request_id, "ikey": idempotency_key}).mappings().one()
    if month == now_month:
        mirror_legacy(session, subject, subject_id, kind, value)
    audit.record("target.set", subject, subject_id,
                 before=target_view(before),
                 after={"month": month_str(month), "revision": current + 1, "kind": kind,
                        "value": money(value) if kind == "amount" else pct_str(value),
                        "mirrored_to_legacy": month == now_month},
                 reason=reason)
    return row_dict(row)


def mirror_legacy(session, subject, subject_id, kind, value):
    """Write the legacy columns so the legacy screens show the same current target."""
    table, a, p, f = _SUBJECT_SQL[subject]
    amount = value if kind == "amount" else Decimal(0)
    percent = value if kind == "percent" else Decimal(0)
    session.execute(text("SELECT set_config('cawp_targets.skip_capture', 'on', true)"))
    session.execute(text(f"UPDATE {table} SET {a} = :a, {p} = :p, {f} = :f WHERE id = CAST(:id AS uuid)"),
                    {"a": float(amount), "p": float(percent), "f": kind == "percent", "id": str(subject_id)})
    session.execute(text("SELECT set_config('cawp_targets.skip_capture', 'off', true)"))


def sync_legacy_columns(session, month: dt.date | None = None) -> list[dict]:
    """Month rollover: a target set in advance for `month` (default: the current month) is
    copied into the legacy columns when they still hold something else, so the legacy
    dashboard shows it. Idempotent; does not commit. Returns what changed. Meant for a
    daily/monthly job (see review/phase3-admin.md)."""
    month = month or current_month()
    changed = []
    for subject in SUBJECTS:
        table, a, p, f = _SUBJECT_SQL[subject]
        rows = session.execute(text(f"""
            SELECT t.subject_id::text AS sid, t.kind, t.value, s.{a} AS amount, s.{p} AS percent,
                   s.{f} AS is_pct
              FROM v_target_months t
              JOIN {table} s ON s.id = t.subject_id
             WHERE t.subject_type = :s AND t.month = :m AND t.source = 'v2'
             FOR UPDATE OF s"""), {"s": subject, "m": month}).mappings().all()
        for r in rows:
            kind, value = legacy_kind(subject, r["amount"], r["percent"], r["is_pct"])
            if kind == r["kind"] and value == Decimal(r["value"]):
                continue
            mirror_legacy(session, subject, r["sid"], r["kind"], Decimal(r["value"]))
            changed.append({"subject_type": subject, "subject_id": r["sid"], "kind": r["kind"],
                            "value": str(r["value"])})
    return changed


# --------------------------------------------------------------------------- target vs actual
_PAYMENTS_CTE = """
    pay AS (
        SELECT round(p.amount::numeric, 2) AS amount, p.user_id AS recorder, c.user_id AS owner,
               po.contract_id
          FROM "Payments" p
          JOIN clints c ON c.id = p.clint_id
          LEFT JOIN portfolios po ON po.id = c.portfolios_id
         WHERE p.approval_status = true AND p.is_deleted = false
           AND p.payment_date >= :t0 AND p.payment_date < :t1
           AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
    )"""


def _round_riyal(d: Decimal) -> Decimal:
    return d.quantize(Decimal(1), ROUND_HALF_UP)


def _target_amount(t, base):
    """SAR target for one effective target (None when unknown)."""
    if not t or not t.get("known"):
        return None
    if t["kind"] == "amount":
        return Decimal(t["value"])
    if t["kind"] == "percent":
        return _round_riyal(Decimal(base or 0) * Decimal(t["value"]) / 100)
    return Decimal(0)


def _achievement(actual, target):
    if target is None or target <= 0:
        return None
    return (Decimal(actual) * 100 / target).quantize(CENT, ROUND_HALF_UP)


def _rank(rows):
    """Competition ranking by achievement % (unknown last), then actual."""
    def key(r):
        a = r["_ach"]
        return (0 if a is not None else 1, -(a or 0), -r["_actual"])
    ordered = sorted(rows, key=key)
    prev, rank = None, 0
    for i, r in enumerate(ordered, 1):
        k = key(r)
        if k != prev:
            rank, prev = i, k
        r["rank"] = rank
    return ordered


def achievement(session, month: dt.date, *, user_ids=None, contract_ids=None,
                include_contracts: bool = True) -> dict:
    """Target vs actual for `month` (see the module docstring for the definition).

    user_ids: restrict the collector rows (None = the legacy set: every active user with a
    contract). contract_ids: restrict to collectors of / rows for these contracts."""
    t0, t1 = riyadh.range_bounds(month, next_month(month) - dt.timedelta(days=1))
    params = {"t0": t0, "t1": t1}

    where, uparams = ["u.is_active = true", "u.contract_id IS NOT NULL"], {}
    if user_ids is not None:
        where.append("u.id = ANY(CAST(:uids AS uuid[]))")
        uparams["uids"] = [str(u) for u in user_ids]
    if contract_ids is not None:
        where.append("u.contract_id = ANY(CAST(:cids AS uuid[]))")
        uparams["cids"] = [str(c) for c in contract_ids]
    users = session.execute(text(f"""
        SELECT u.id::text AS id, u.user_name, u.frist_name, u.last_name, u.contract_id::text AS contract_id,
               c.name AS contract_name
          FROM users u LEFT JOIN contracts c ON c.id = u.contract_id
         WHERE {' AND '.join(where)}
         ORDER BY u.frist_name, u.last_name, u.user_name"""), uparams).mappings().all()
    uids = [u["id"] for u in users]

    by_owner, by_recorder = {}, {}
    if uids:
        for r in session.execute(text(f"""
                WITH {_PAYMENTS_CTE}
                SELECT who, kind, sum(amount) AS total, count(*) AS n FROM (
                    SELECT owner::text AS who, 'owner' AS kind, amount FROM pay WHERE owner = ANY(CAST(:uids AS uuid[]))
                    UNION ALL
                    SELECT recorder::text, 'recorder', amount FROM pay WHERE recorder = ANY(CAST(:uids AS uuid[]))
                ) x GROUP BY who, kind"""), {**params, "uids": uids}).mappings():
            (by_owner if r["kind"] == "owner" else by_recorder)[r["who"]] = (Decimal(r["total"]), r["n"])

    user_t = effective_targets(session, "user", uids, month)
    user_contracts = sorted({u["contract_id"] for u in users if u["contract_id"]})
    contract_rows = []
    if include_contracts:
        cwhere = "true" if contract_ids is None else "c.id = ANY(CAST(:cids AS uuid[]))"
        contract_rows = session.execute(text(f"""
            SELECT c.id::text AS id, c.name, c.is_active FROM contracts c WHERE {cwhere}
             ORDER BY c.name"""), {"cids": [str(c) for c in contract_ids or []]}).mappings().all()
    all_cids = sorted(set(user_contracts) | {c["id"] for c in contract_rows})
    contract_t = effective_targets(session, "contract", all_cids, month)

    # percent bases only where a percent target is in force (legacy: current unarchived debt)
    need_user_base = [u["id"] for u in users
                      if (user_t.get(u["id"]) or {}).get("kind") == "percent"
                      or ((user_t.get(u["id"]) or {}).get("kind") == "none"
                          and (contract_t.get(u["contract_id"]) or {}).get("kind") == "percent")]
    user_base = {}
    if need_user_base:
        for r in session.execute(text("""
                SELECT user_id::text AS uid, sum(round(amount::numeric, 2)) AS base FROM clints
                 WHERE is_archive = false AND user_id = ANY(CAST(:ids AS uuid[])) GROUP BY user_id"""),
                {"ids": need_user_base}).mappings():
            user_base[r["uid"]] = Decimal(r["base"])

    collectors, collector_target_by_contract = [], {}
    for u in users:
        ut = user_t.get(u["id"]) or {"known": False}
        ct = contract_t.get(u["contract_id"]) if u["contract_id"] else None
        base = user_base.get(u["id"], Decimal(0))
        if ut.get("known") and ut["kind"] != "none":
            source, target = "user", _target_amount(ut, base)
        elif ut.get("known") and ct is not None and ct.get("known") and ct["kind"] != "none":
            source, target = "contract", _target_amount(ct, base)
        elif ut.get("known") and (ct is None or ct.get("known")):
            source, target = None, Decimal(0)
        else:
            source, target = None, None          # unknown: month before history
        actual, n = by_owner.get(u["id"], (Decimal(0), 0))
        recorded, rn = by_recorder.get(u["id"], (Decimal(0), 0))
        ach = _achievement(actual, target)
        if target is not None and u["contract_id"]:
            collector_target_by_contract[u["contract_id"]] = \
                collector_target_by_contract.get(u["contract_id"], Decimal(0)) + target
        collectors.append({
            "user_id": u["id"], "user_name": u["user_name"],
            "name": f"{u['frist_name'] or ''} {u['last_name'] or ''}".strip(),
            "contract_id": u["contract_id"], "contract_name": u["contract_name"],
            "target": {
                "known": target is not None,
                "amount": money(target),
                "source": source,
                "user_target": target_view(ut),
                "contract_target": target_view(ct) if ct is not None else None,
                "percent_base": money(base) if u["id"] in need_user_base else None,
            },
            "actual": money(actual), "payments_count": n,
            "actual_recorded": money(recorded), "recorded_count": rn,
            "achievement_percent": None if ach is None else str(ach),
            "remaining": money(target - actual) if target is not None else None,
            "_ach": ach, "_actual": actual,
        })
    collectors = _rank(collectors)
    for c in collectors:
        c.pop("_ach"), c.pop("_actual")

    contracts_out, unassigned, all_actual = [], None, None
    if include_contracts:
        cid_list = [c["id"] for c in contract_rows]
        by_contract = {}
        if cid_list:
            for r in session.execute(text(f"""
                    WITH {_PAYMENTS_CTE}
                    SELECT contract_id::text AS cid, sum(amount) AS total, count(*) AS n FROM pay
                     WHERE contract_id = ANY(CAST(:cids AS uuid[])) GROUP BY contract_id"""),
                    {**params, "cids": cid_list}).mappings():
                by_contract[r["cid"]] = (Decimal(r["total"]), r["n"])
        need_c_base = [c for c in cid_list if (contract_t.get(c) or {}).get("kind") == "percent"]
        c_base = {}
        if need_c_base:
            for r in session.execute(text("""
                    SELECT po.contract_id::text AS cid, sum(round(c.amount::numeric, 2)) AS base
                      FROM clints c JOIN portfolios po ON po.id = c.portfolios_id
                     WHERE c.is_archive = false AND po.contract_id = ANY(CAST(:ids AS uuid[]))
                     GROUP BY po.contract_id"""), {"ids": need_c_base}).mappings():
                c_base[r["cid"]] = Decimal(r["base"])
        for c in contract_rows:
            ct = contract_t.get(c["id"]) or {"known": False}
            target = _target_amount(ct, c_base.get(c["id"], Decimal(0)))
            actual, n = by_contract.get(c["id"], (Decimal(0), 0))
            ach = _achievement(actual, target)
            contracts_out.append({
                "contract_id": c["id"], "name": c["name"], "is_active": c["is_active"],
                "target": {"known": target is not None, "amount": money(target),
                           "contract_target": target_view(ct),
                           "percent_base": money(c_base.get(c["id"])) if c["id"] in need_c_base else None},
                "collectors_target_sum": money(collector_target_by_contract.get(c["id"], Decimal(0))),
                "actual": money(actual), "payments_count": n,
                "achievement_percent": None if ach is None else str(ach),
                "remaining": money(target - actual) if target is not None else None,
            })
        if contract_ids is None and user_ids is None:
            r = session.execute(text(f"""
                WITH {_PAYMENTS_CTE}
                SELECT coalesce(sum(amount), 0) AS total,
                       coalesce(sum(amount) FILTER (WHERE owner IS NULL), 0) AS unassigned
                  FROM pay"""), params).mappings().one()
            all_actual, unassigned = Decimal(r["total"]), Decimal(r["unassigned"])

    known_targets = [Decimal(c["target"]["amount"]) for c in collectors if c["target"]["known"]]
    return {
        "month": month_str(month),
        "period": {"first_day": month.isoformat(),
                   "last_day": (next_month(month) - dt.timedelta(days=1)).isoformat(),
                   "timezone": "Asia/Riyadh", "locked": month < current_month()},
        "collectors": collectors,
        "contracts": contracts_out,
        "totals": {
            "collectors": len(collectors),
            "target": money(sum(known_targets, Decimal(0))),
            "targets_unknown": sum(1 for c in collectors if not c["target"]["known"]),
            "actual": money(sum((Decimal(c["actual"]) for c in collectors), Decimal(0))),
            "actual_recorded": money(sum((Decimal(c["actual_recorded"]) for c in collectors), Decimal(0))),
            "all_approved_recovery": money(all_actual),
            "unassigned_cases_recovery": money(unassigned),
        },
    }
