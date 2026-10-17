"""Excel imports, batched.

Every function here is the body of one legacy route, rewritten so that

* the affected clients are fetched with **one** ``IN (...)`` query (chunked at
  `IN_CHUNK`) instead of ``Clints.query.filter_by(...).first()`` per row;
* ``db.session.commit()`` runs once per `BATCH_SIZE` rows instead of once per row
  (review P9: a 10k-row import was 20k+ commits, each a round trip + fsync);
* ``updateClintInRedis()`` is **not** called (review P2 - ~9 queries + a ``KEYS`` scan
  per row for a cache whose only remaining readers are being deleted);
* progress is reported through `core.jobs` when the caller passes a `job_id`.

Each ``run_*`` returns the **exact** JSON body its legacy route returned - same keys,
same Arabic strings, same counts - so the route layer is a `jsonify` and nothing else,
and a background job's ``result`` is indistinguishable from the synchronous body.
Where the body is an error body the returned dict also carries the HTTP status the
legacy route used, via `Result.http_status` (a plain dict everywhere else).
"""
import datetime
import json
import logging

from sqlalchemy import func

from extensions import db
from models import (Clints, ClintContacts, ClintContactTypes, Comments,
                    Payments, Portfolios, SepcifyUsers, User)
from services import excel

log = logging.getLogger("cawp.imports")

BATCH_SIZE = 500     # rows per commit
IN_CHUNK = 1000      # ids per IN (...) predicate

__all__ = [
    "Result", "BATCH_SIZE", "IN_CHUNK", "chunked", "fetch_clints_by", "progress",
    "run_add_clints_by_excel", "run_update_by_excel", "run_attribute_by_excel",
    "run_sepcify_users_by_excel", "run_comments_by_excel",
]


class Result(dict):
    """The legacy response body, plus the HTTP status the legacy route returned.

    A plain ``dict`` subclass: `jsonify` renders it identically and orjson stores it in
    the job record as the bare body, so `GET /jobs/<id>` -> ``result`` is exactly what the
    synchronous call would have returned.
    """

    http_status = 200

    def __init__(self, body, http_status=200):
        super().__init__(body)
        self.http_status = http_status


def chunked(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def progress(job_id, pct, message=None):
    """No-op when running synchronously (`job_id` is None)."""
    if job_id is None:
        return
    from core import jobs
    jobs.progress(job_id, pct, message)


def fetch_clints_by(column, values, chunk=IN_CHUNK):
    """One ``IN (...)`` per `chunk` distinct values instead of one query per row."""
    seen = list(dict.fromkeys(v for v in values if v is not None))
    out = []
    for part in chunked(seen, chunk):
        out.extend(Clints.query.filter(column.in_(part)).all())
    return out


def paid_totals(clint_ids, chunk=IN_CHUNK):
    """``{clint_id: sum(approved payments)}`` - replaces the per-row loop in `check_amount`."""
    totals = {}
    for part in chunked(clint_ids, chunk):
        rows = (db.session.query(Payments.clint_id, func.sum(Payments.amount))
                .filter(Payments.clint_id.in_(part), Payments.approval_status == True)  # noqa: E712
                .group_by(Payments.clint_id).all())
        for clint_id, total in rows:
            totals[clint_id] = float(total or 0)
    return totals


def _json_default(o):
    """`default` of the legacy `attribute` serializer (app.py:873-880).

    Only dates are handled; anything else still raises TypeError, exactly as before.
    """
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def check_amount(value, comparison_value, paid_total):
    """`app.py:7983-8005` check_amount, with the per-row payments query hoisted out.

    Note the deliberate asymmetry kept from legacy: only `ValueError` is caught, so a
    ``None`` amount still raises `TypeError` and aborts the whole request the way it did.
    """
    try:
        value = float(value)
    except ValueError:
        return False, "المبلغ غير صحيح"
    if value == comparison_value:
        return False, "المبلغ الحالي مطابق للقيمة المدخلة"
    if value < paid_total:
        return False, "المبلغ أقل من المبلغ المسدد "
    return True, None


# --------------------------------------------------------------------------- #
# POST /clint/addClintsByExcel   (legacy app.py:746-778 + insertClints 801-870)
# --------------------------------------------------------------------------- #

_CONTACT_PREFIXES = ("email", "fax", "mobile", "phone")


def _split_row(row, contact_type_ids):
    """Legacy `insertClints` row split: (contact dicts, Clints kwargs).

    Kept bug-for-bug:
      * the contact columns are whatever starts with email/fax/mobile/phone (plus
        `identityNumber`), and the contact type is looked up by the part before the
        first ``_`` - an unknown prefix raised `AttributeError` and aborted the import,
        which is still what happens (the caller lets it propagate);
      * `attribute` is the **whole** row (all Excel headers, dates isoformatted) built
        before the row is narrowed to the four real columns.  `attribute` is JSONB now;
        the value is still a dict.
    """
    contacts = {k: v for k, v in row.items()
                if k == "identityNumber" or k.startswith(_CONTACT_PREFIXES)}
    contacts = {k: v for k, v in contacts.items() if v is not None}

    out_contacts = []
    for k, v in contacts.items():
        if k == "identityNumber":
            continue
        type_name = k.split("_")[0]
        contact_type_id = contact_type_ids[type_name]     # KeyError == legacy AttributeError
        out_contacts.append({
            "identityNumber": contacts["identityNumber"],
            "contact": v,
            "contact_type_id": contact_type_id,
            "contact_status": "new",
        })

    attribute = json.loads(json.dumps(row, default=_json_default))
    clint = {k: v for k, v in row.items()
             if k in ("name", "identityNumber", "accountNumber", "amount")}
    clint["attribute"] = attribute
    return out_contacts, clint


def _flush_batch(objects):
    """Commit a batch; on failure retry it row by row so only the bad rows are dropped.

    Legacy committed once per row inside a ``try/except: rollback(); log``, i.e. a single
    duplicate `accountNumber` or a NULL `name` skipped that row and kept going.  A batched
    commit would lose the whole batch, so the slow path only runs when a batch actually
    fails.  Returns the number of rows that could not be inserted.
    """
    if not objects:
        return 0
    try:
        db.session.add_all(objects)
        db.session.commit()
        return 0
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.warning("batch of %d failed (%s); retrying row by row", len(objects), e)
    failed = 0
    for obj in objects:
        try:
            db.session.add(obj)
            db.session.commit()
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            failed += 1
            log.exception("insertClints row failed: %s", e)
    return failed


def run_add_clints_by_excel(job_id, identity, contract_id, portfolio_name, data_excel):
    """Create the portfolio, then bulk-insert its clients + contacts.

    Legacy response: ``{"message": "تم رفع الملف بنجاح", "status": True}`` (200).
    Legacy swallowed per-row insert failures, so this is a partial-success import with no
    error list; the same is true here.  Anything raised outside a row (a bad contact
    column, an unserialisable cell) still aborts the whole call, as before.
    """
    portfolio = Portfolios(name=portfolio_name, contract_id=contract_id)
    db.session.add(portfolio)
    db.session.commit()
    portfolios_id = portfolio.id

    contact_type_ids = {t.name: str(t.id) for t in ClintContactTypes.query.all()}

    total = len(data_excel) or 1
    done = 0
    for batch in chunked(data_excel, BATCH_SIZE):
        contacts, clints = [], []
        for row in batch:
            row_contacts, clint_kwargs = _split_row(dict(row), contact_type_ids)
            contacts.extend(ClintContacts(**c) for c in row_contacts)
            clint_kwargs["portfolios_id"] = portfolios_id
            clints.append(Clints(**clint_kwargs))
        # contacts first: legacy inserted (and committed) them before the client row,
        # so a rejected client still left its contacts behind.
        _flush_batch(contacts)
        _flush_batch(clints)
        done += len(batch)
        progress(job_id, int(done * 100 / total), f"{done}/{len(data_excel)}")

    return Result({"message": "تم رفع الملف بنجاح", "status": True}, 200)


# --------------------------------------------------------------------------- #
# POST /clints/update/byexcel   (legacy app.py:1517-1602)
# --------------------------------------------------------------------------- #

def _commit_every(counter, total, job_id, label):
    if counter % BATCH_SIZE == 0:
        db.session.commit()
        progress(job_id, int(counter * 100 / (total or 1)), f"{label} {counter}/{total}")


def run_update_by_excel(job_id, identity, data1, data2, update_type, update_side,
                        file_data_row):
    """`update_type` 1..5.

    Legacy semantics preserved:
      * a DB error anywhere aborts the **whole** request (legacy committed inside the
        loop but inside the outer `try`, so the exception escaped the route) - we let it
        propagate to the route, which returns the same 201 body;
      * business errors are collected per row in `messages` (a list of
        ``[identifier, reason]`` pairs) and the request still returns **200**;
      * the response key is ``messages`` (not `errorMessages`), and
        `total_accounts` is ``len(fileDataRow)`` regardless of type;
      * the identifier is matched with a plain ``str(value)`` - *not* the
        `_normalize_excel_key` used by `/clints/attribute/byexcel`.

    Two fixes:
      * **review 3.3** - the ``for ... else`` on type 5 appended "الحساب غير موجود" for
        every row that succeeded.  Now only rows with no matching client are reported.
      * **review 3.25** - type 3 had no branch at all, so the route reported success
        having done nothing.  The dialog (`UpdateDataByExcelButton.tsx`) labels 3
        "رقم الهوية" with `data1` = account-number column, so it now updates
        `identityNumber` by `accountNumber`; an unknown `update_type` returns the
        legacy-style per-row error instead of a silent success.
    """
    error_messages = []
    total_accounts = len(file_data_row)

    if update_type == "1":
        contact_type = (db.session.query(ClintContactTypes)
                        .filter(ClintContactTypes.name == 'mobile').first())
        contact_type_id = str(contact_type.as_dict()['id'])
        n = 0
        for row in file_data_row:
            db.session.add(ClintContacts(
                contact_type_id=contact_type_id,
                contact_status='new',
                identityNumber=row[data1],
                contact=row[data2],
            ))
            n += 1
            _commit_every(n, total_accounts, job_id, "contacts")
        db.session.commit()
        progress(job_id, 100, f"contacts {n}/{total_accounts}")

    elif update_type in ("2", "3", "4"):
        keys = [str(row[data1]) for row in file_data_row]
        by_account = {}
        for clint in fetch_clints_by(Clints.accountNumber, keys):
            by_account.setdefault(clint.accountNumber, clint)

        totals = paid_totals([c.id for c in by_account.values()]) if update_type == "2" else {}

        n = 0
        for row in file_data_row:
            key = str(row[data1])
            clint = by_account.get(key)
            if clint is None:
                error_messages.append([row[data1], "الحساب غير موجود"])
            elif update_type == "2":
                ok, message = check_amount(row[data2], clint.amount, totals.get(clint.id, 0))
                if ok:
                    # legacy assigned the raw cell and let Postgres coerce it into the
                    # float8 column; `float()` stores the identical value and keeps the
                    # in-memory attribute numeric for a repeated account number in the
                    # same batch (legacy got that from the per-row commit expiring it).
                    clint.amount = float(row[data2])
                else:
                    error_messages.append([row[data1], message])
            elif update_type == "3":
                clint.identityNumber = row[data2]
            else:                                   # "4"
                clint.name = row[data2]
            n += 1
            _commit_every(n, total_accounts, job_id, "rows")
        db.session.commit()
        progress(job_id, 100, f"rows {n}/{total_accounts}")

    elif update_type == "5":
        keys = [str(row[data1]) for row in file_data_row]
        by_identity = {}
        for clint in fetch_clints_by(Clints.identityNumber, keys):
            by_identity.setdefault(clint.identityNumber, []).append(clint)

        n = 0
        for row in file_data_row:
            clints = by_identity.get(str(row[data1]))
            if clints:
                for clint in clints:
                    clint.name = row[data2]
            else:
                error_messages.append([row[data1], "الحساب غير موجود"])
            n += 1
            _commit_every(n, total_accounts, job_id, "rows")
        db.session.commit()
        progress(job_id, 100, f"rows {n}/{total_accounts}")

    else:
        # review 3.25: legacy fell through to `len(errorMessages) == 0` -> 200 "success"
        # without touching a single row.  Report it instead.
        for row in file_data_row:
            error_messages.append([row.get(data1), "نوع التحديث غير موجود"])

    total_error = len(error_messages)
    updated_accounts = total_accounts - total_error

    if total_error == 0:
        return Result({"messages": error_messages,
                       "message": "تم تحديث الحسابات بنجاح",
                       "status": True}, 200)
    return Result({"messages": error_messages,
                   "message": "تم تحديث " + str(updated_accounts) + " من اصل " + str(total_accounts),
                   "status": False}, 200)


# --------------------------------------------------------------------------- #
# POST /clints/attribute/byexcel   (legacy app.py:1617-1697 - already batched)
# --------------------------------------------------------------------------- #

def normalize_excel_key(value):
    """app.py:1605-1614 `_normalize_excel_key`: openpyxl returns floats, and `str(2211200272.0)`
    is `'2211200272.0'`, which matches nothing."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def run_attribute_by_excel(job_id, identity, identifier_column, identifier_type,
                           value_column, attribute_key, file_data_row):
    """Set one `attribute` key on every client named by the file.

    This route was already the in-repo template (single ``in_()`` + single commit); the
    only change is that the per-row ``updateClintInRedis`` loop is gone and the fetch is
    chunked at 1,000 ids.  Validation stays in the route so the async path can reject
    early; here we only handle the "no valid identifier" case, which needs the parsed rows.
    """
    error_messages = []

    wanted = {}
    for row in file_data_row:
        key = normalize_excel_key(row.get(identifier_column))
        if key == '':
            continue
        wanted[key] = row

    if not wanted:
        return Result({"message": "لا يوجد معرّف صالح في العمود المحدد", "status": False}, 201)

    id_col = Clints.accountNumber if identifier_type == 'accountNumber' else Clints.identityNumber
    found = {}
    for clint in fetch_clints_by(id_col, list(wanted.keys())):
        raw = clint.accountNumber if identifier_type == 'accountNumber' else clint.identityNumber
        found.setdefault(normalize_excel_key(raw), []).append(clint)

    total = len(wanted) or 1
    n = 0
    for key, row in wanted.items():
        targets = found.get(key)
        if not targets:
            error_messages.append([key, "الحساب غير موجود"])
        else:
            value = row.get(value_column)
            for clint in targets:
                # JSON column: SQLAlchemy does not track in-place mutation, so rebuild.
                new_attribute = dict(clint.attribute or {})
                new_attribute[attribute_key] = value
                clint.attribute = new_attribute
        n += 1
        if n % BATCH_SIZE == 0:
            db.session.commit()
            progress(job_id, int(n * 100 / total), f"{n}/{len(wanted)}")
    db.session.commit()
    progress(job_id, 100, f"{n}/{len(wanted)}")

    total_accounts = len(wanted)
    total_error = len(error_messages)
    updated_accounts = total_accounts - total_error

    if total_error == 0:
        return Result({"messages": error_messages,
                       "message": "تم تحديث " + str(updated_accounts) + " حساب بنجاح",
                       "status": True}, 200)
    return Result({"messages": error_messages,
                   "message": "تم تحديث " + str(updated_accounts) + " من اصل " + str(total_accounts),
                   "status": False}, 200)


# --------------------------------------------------------------------------- #
# POST /clint/SepcifyUsersByExcel   (legacy app.py:696-745)
# --------------------------------------------------------------------------- #

def run_sepcify_users_by_excel(job_id, identity, user_name_column, account_number_column,
                               data_excel):
    """Assign a collector to every account in the file (a `SepcifyUsers` row + `user_id`).

    **Legacy aborts** on the first unknown user name or account number and returns 201 -
    but it had already committed every row before it, so the import is partially applied.
    That is reproduced exactly: rows before the offending one are committed, then the same
    201 body is returned.  `create_by` stays the JWT identity.
    """
    names = [row[user_name_column] for row in data_excel]
    users = {}
    for part in chunked(list(dict.fromkeys(names)), IN_CHUNK):
        for user in User.query.filter(User.user_name.in_(part)).all():
            users.setdefault(user.user_name, user)

    accounts = [str(row[account_number_column]) for row in data_excel]
    clints = {}
    for clint in fetch_clints_by(Clints.accountNumber, accounts):
        clints.setdefault(clint.accountNumber, clint)

    # A case with an open call keeps its owner (services/calling/guards.py): its row is
    # skipped, and the success message says how many were skipped.
    from services.calling import guards as call_guards
    held = call_guards.open_cases([c.id for c in clints.values()])
    skipped = set()

    n = 0
    for row in data_excel:
        user_name = row[user_name_column]
        account_number = row[account_number_column]
        user = users.get(user_name)
        if user is None:
            db.session.commit()      # legacy had committed everything up to this row
            return Result({"message": str(user_name) + " المستخدم غير موجود",
                           "status": False}, 201)
        clint = clints.get(str(account_number))
        if clint is None:
            db.session.commit()
            return Result({"message": str(account_number) + " الحساب غير موجود",
                           "status": False}, 201)
        if str(clint.id) in held and str(clint.user_id) != str(user.id):
            skipped.add(str(clint.id))
            continue

        db.session.add(SepcifyUsers(user_id=user.id, clint_id=clint.id, create_by=identity))
        clint.user_id = user.id
        n += 1
        _commit_every(n, len(data_excel), job_id, "rows")
    db.session.commit()
    progress(job_id, 100, f"rows {n}/{len(data_excel)}")

    if skipped:
        return Result({"message": "تم تحديد المستخدمين بنجاح"
                                  + call_guards.ARABIC_REASSIGN_SKIPPED.format(n=len(skipped)),
                       "status": True, "skipped_open_calls": len(skipped)}, 200)
    return Result({"message": "تم تحديد المستخدمين بنجاح", "status": True}, 200)


# --------------------------------------------------------------------------- #
# POST /commentsbyexcel   (legacy app.py:3409-3482)
# --------------------------------------------------------------------------- #

def run_comments_by_excel(job_id, identity, comment_data, file_path, index_column):
    """One comment per account number in column `index_column` of the uploaded workbook.

    Legacy read the column with pandas, then ran one ``filter_by(accountNumber=...)`` +
    one ``INSERT`` + one ``COMMIT`` + one `updateClintInRedis` per row, and **aborted**
    with 201 "العميل غير موجود" at the first unknown account (again leaving the earlier
    comments committed).  Same contract here, minus the per-row query/commit/redis.

    Duplicated account numbers in the column produce duplicated comments, as before.
    """
    if not file_path:
        return Result({"message": "تم إنشاء التعليق بنجاح", "status": True}, 200)

    selected = excel.column_values(file_path, index_column)

    clints = {}
    for clint in fetch_clints_by(Clints.accountNumber, selected):
        clints.setdefault(clint.accountNumber, clint)

    n = 0
    for account_number in selected:
        clint = clints.get(account_number)
        if clint is None:
            db.session.commit()
            return Result({"message": "العميل غير موجود", "status": False,
                           "error": "clint not found"}, 201)
        row = dict(comment_data)
        row['clint_id'] = clint.id
        db.session.add(Comments(**row))
        n += 1
        _commit_every(n, len(selected), job_id, "comments")
    db.session.commit()
    progress(job_id, 100, f"comments {n}/{len(selected)}")

    return Result({"message": "تم إنشاء التعليق بنجاح", "status": True}, 200)
