"""Bulk archive / assign / lock / delete / notify - set-based.

Every function here replaced a ``for id in ids:`` loop that ran
``Clints.query.get(id)`` + ``db.session.commit()`` + ``redis_obj.keys("clint:<id>*")``
per element (review P2/P9/P11).  They now issue one ``UPDATE ... WHERE id IN (...)``
(or one ``INSERT ... SELECT``) per chunk of 1,000 and commit once.

`send_notification` additionally separates the DB work from the socket work: all
`Notification` rows are inserted and committed first, and only then does
``socketio.emit('alarm', ...)`` run - with ``async_mode='threading'`` an emit is a
blocking write to every socket in the room, so interleaving it with commits held a
transaction open across N network writes.

Response bodies, Arabic strings and HTTP statuses are the legacy ones, to the space.
"""
import datetime
import logging
import uuid as _uuid

from sqlalchemy import delete, insert, update

from extensions import db, socketio
from models import (ClientLockHistory, Clints, ClintArchive, Notification,
                    SepcifyUsers, User)
from services.excel import column_values
from services.imports import IN_CHUNK, Result, chunked, fetch_clints_by, progress

log = logging.getLogger("cawp.bulk")

_NO_SYNC = {"synchronize_session": False}


def _uuid_key(value):
    """Canonical key for an id that may arrive as str, UUID, or an odd spelling.

    `Clints.query.get(id)` coerced whatever the JSON body carried; a dict keyed on
    `str(row.id)` would not, so ids are normalised through `uuid.UUID` when possible.
    """
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return str(value)


__all__ = [
    "run_archive_by_excel", "run_unarchive_by_excel", "run_archive_by_ids",
    "run_archive_portfolio", "run_sepcify_users_by_ids", "run_bulk_lock",
    "run_bulk_unlock", "run_delete_clints", "run_send_notification",
]


def _archive_clints(clint_ids, archive_reason_id, user_id, job_id=None, label="archive"):
    """Archive `clint_ids`: one `clintArchive` row each, then point the client at it.

    Legacy did ``is_archive=True; commit; add(ClintArchive); commit; clintArchive_id=...;
    commit`` **per client** (3 commits per row).  Here it is, per chunk:
        INSERT INTO "clintArchive" (clint_id, archive_reason_id, user_id) SELECT ...
        UPDATE clints SET is_archive=true, "clintArchive_id" = <the new row>
    A client archived twice keeps the *newest* `clintArchive` row, which is what the
    legacy sequence produced too.
    """
    total = len(clint_ids) or 1
    done = 0
    for part in chunked(clint_ids, IN_CHUNK):
        new_ids = [row[0] for row in db.session.execute(
            insert(ClintArchive).returning(ClintArchive.id),
            [{"clint_id": cid, "archive_reason_id": archive_reason_id, "user_id": user_id}
             for cid in part],
        ).all()]
        # One statement instead of one UPDATE per row: the pointer is the archive row we
        # just created for that client (restricted to `new_ids`, so pre-existing archive
        # rows can never be selected; LIMIT 1 covers an id repeated inside one request,
        # which legacy also left pointing at the last row it inserted).
        newest = (db.session.query(ClintArchive.id)
                  .filter(ClintArchive.clint_id == Clints.id, ClintArchive.id.in_(new_ids))
                  .order_by(ClintArchive.create_at.desc(), ClintArchive.id.desc())
                  .limit(1).scalar_subquery())
        db.session.execute(
            update(Clints).where(Clints.id.in_(part))
            .values(is_archive=True, clintArchive_id=newest)
            .execution_options(**_NO_SYNC)
        )
        done += len(part)
        progress(job_id, int(done * 100 / total), f"{label} {done}/{len(clint_ids)}")
    db.session.commit()


# --------------------------------------------------------------------------- #
# PUT /clint/archivebyexcel   (legacy app.py:5253-5293)
# --------------------------------------------------------------------------- #

def run_archive_by_excel(job_id, identity, account_number_column, data_excel,
                         archive_reason_id, user_id):
    """Archive every account listed in the posted rows.

    Legacy **aborts** at the first unknown account with
    ``{"<acc> الحساب غير موجود", status: false}`` 201 - and everything before it is
    already committed.  Reproduced: the accounts are validated in file order first, and
    the prefix that legacy would have committed is committed before returning the error.
    """
    accounts = [str(row[account_number_column]) for row in data_excel]
    found = {}
    for clint in fetch_clints_by(Clints.accountNumber, accounts):
        found.setdefault(clint.accountNumber, clint)

    ok_ids = []
    for account_number in accounts:
        clint = found.get(account_number)
        if clint is None:
            if ok_ids:
                _archive_clints(ok_ids, archive_reason_id, user_id, job_id)
            return Result({"message": str(account_number) + " الحساب غير موجود",
                           "status": False}, 201)
        ok_ids.append(clint.id)

    _archive_clints(ok_ids, archive_reason_id, user_id, job_id)
    return Result({"message": "تم تحديد المستخدمين بنجاح", "status": True}, 200)


# --------------------------------------------------------------------------- #
# PUT /clint/unarchivebyexcel   (legacy app.py:5295-5349)
# --------------------------------------------------------------------------- #

def run_unarchive_by_excel(job_id, identity, file_path, index_column):
    """Un-archive every account in column `index_column` of the uploaded workbook.

    Legacy: missing/unreadable file -> nothing happens and the call still reports success
    (review 3.9 made `selected_clients` default to None instead of NameError-ing); first
    unknown account -> 201 with that account in the message, earlier rows committed.
    """
    if not file_path:
        return Result({"message": "تم تحديد المستخدمين بنجاح", "status": True}, 200)

    selected = column_values(file_path, index_column)
    found = {}
    for clint in fetch_clints_by(Clints.accountNumber, selected):
        found.setdefault(clint.accountNumber, clint)

    ok_ids = []
    for account_number in selected:
        clint = found.get(account_number)
        if clint is None:
            _unarchive(ok_ids, job_id)
            return Result({"message": str(account_number) + " الحساب غير موجود",
                           "status": False}, 201)
        ok_ids.append(clint.id)

    _unarchive(ok_ids, job_id)
    return Result({"message": "تم تحديد المستخدمين بنجاح", "status": True}, 200)


def _unarchive(clint_ids, job_id=None):
    total = len(clint_ids) or 1
    done = 0
    for part in chunked(clint_ids, IN_CHUNK):
        db.session.execute(
            update(Clints).where(Clints.id.in_(part))
            .values(is_archive=False, clintArchive_id=None)
            .execution_options(**_NO_SYNC)
        )
        done += len(part)
        progress(job_id, int(done * 100 / total), f"unarchive {done}/{len(clint_ids)}")
    db.session.commit()


# --------------------------------------------------------------------------- #
# PUT /clint/archive/ids   (legacy app.py:5372-5404)
# --------------------------------------------------------------------------- #

def run_archive_by_ids(job_id, identity, clint_ids, archive_reason_id, user_id):
    """Archive by client id.

    Legacy aborts on the first id that does not exist with
    ``{"العميل غير موجود", status:false, error:"role not found"}`` 201 (the "role not
    found" string is a copy/paste from the branches route - it is on the wire, so it stays).
    Note the success message keeps its stray leading/trailing spaces.
    """
    existing = {_uuid_key(c.id): c for c in fetch_clints_by(Clints.id, clint_ids)}
    ok_ids = []
    for clint_id in clint_ids:
        clint = existing.get(_uuid_key(clint_id))
        if clint is None:
            if ok_ids:
                _archive_clints(ok_ids, archive_reason_id, user_id, job_id)
            return Result({"message": "العميل غير موجود", "status": False,
                           "error": "role not found"}, 201)
        ok_ids.append(clint.id)

    _archive_clints(ok_ids, archive_reason_id, user_id, job_id)
    return Result({"message": "  تم نقل المحقظة للأرشيف بنجاح ", "status": True}, 200)


# --------------------------------------------------------------------------- #
# PUT /clint/archive/portofolio/<id>   (legacy app.py:5939-5989)
# --------------------------------------------------------------------------- #

def run_archive_portfolio(job_id, identity, portfolio_id, archive_reason_id):
    """Archive every non-archived client of one portfolio. `user_id` is the JWT identity.

    404 (not 201) when the portfolio has no un-archived clients - that status is on the
    wire (review section 6).
    """
    clint_ids = [row[0] for row in db.session.query(Clints.id)
                 .filter(Clints.portfolios_id == portfolio_id, Clints.is_archive == False)  # noqa: E712
                 .all()]
    if not clint_ids:
        return Result({"message": "لم يتم العثور على أي عملاء غير مؤرشفين في المحفظة",
                       "status": False}, 404)

    _archive_clints(clint_ids, archive_reason_id, identity, job_id, label="portfolio")
    return Result({"message": "تم نقل جميع العملاء في المحفظة للأرشيف بنجاح",
                   "status": True}, 200)


# --------------------------------------------------------------------------- #
# POST /sepcifyusers/ids   (legacy app.py:6047-6087)
# --------------------------------------------------------------------------- #

def run_sepcify_users_by_ids(job_id, identity, clint_ids, user_id):
    """Assign one collector to a list of client ids.

    Legacy checked the user once (201 ``"<user_id> المستخدم غير موجود"``) and then each
    client (201 ``"<id> الحساب غير موجود"``, earlier rows committed).  `create_by` is the
    JWT identity, as before.  The `SepcifyUsers` rows go in with a single
    ``INSERT ... VALUES (...), (...)`` per chunk and the collector is set with one UPDATE.
    """
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        return Result({"message": str(user_id) + " المستخدم غير موجود", "status": False}, 201)

    existing = {_uuid_key(c.id): c for c in fetch_clints_by(Clints.id, clint_ids)}
    ok_ids = []
    for clint_id in clint_ids:
        clint = existing.get(_uuid_key(clint_id))
        if clint is None:
            _assign(ok_ids, user.id, identity, job_id)
            return Result({"message": str(clint_id) + " الحساب غير موجود",
                           "status": False}, 201)
        ok_ids.append(clint.id)

    _assign(ok_ids, user.id, identity, job_id)
    return Result({"message": "تم تحديد المستخدمين بنجاح", "status": True}, 200)


def _assign(clint_ids, user_id, created_by, job_id=None):
    if not clint_ids:
        return
    total = len(clint_ids)
    done = 0
    for part in chunked(clint_ids, IN_CHUNK):
        db.session.execute(
            insert(SepcifyUsers),
            [{"user_id": user_id, "clint_id": cid, "create_by": created_by} for cid in part],
        )
        db.session.execute(update(Clints).where(Clints.id.in_(part))
                           .values(user_id=user_id).execution_options(**_NO_SYNC))
        done += len(part)
        progress(job_id, int(done * 100 / total), f"assign {done}/{total}")
    db.session.commit()


# --------------------------------------------------------------------------- #
# PUT /clints/bulk-lock  &  /clints/bulk-unlock   (legacy app.py:442-489 / 490-535)
# --------------------------------------------------------------------------- #

def _bulk_set_lock(job_id, identity, client_ids, reason, locked):
    """Shared body: only the rows whose `is_locked` differs are touched, and each of
    those gets one `client_lock_history` row - exactly the legacy rule, set-based."""
    changed, unchanged = [], 0
    for part in chunked(client_ids, IN_CHUNK):
        rows = (db.session.query(Clints.id, Clints.is_locked)
                .filter(Clints.id.in_(part)).all())
        for clint_id, is_locked in rows:
            if bool(is_locked) != locked:
                changed.append(clint_id)
            else:
                unchanged += 1

    for part in chunked(changed, IN_CHUNK):
        db.session.execute(update(Clints).where(Clints.id.in_(part))
                           .values(is_locked=locked).execution_options(**_NO_SYNC))
        db.session.execute(
            insert(ClientLockHistory),
            [{"clint_id": cid, "user_id": identity,
              "action": "lock" if locked else "unlock", "reason": reason} for cid in part],
        )
        progress(job_id, 90, f"{'lock' if locked else 'unlock'} {len(changed)}")
    db.session.commit()
    return len(changed), unchanged


def run_bulk_lock(job_id, identity, client_ids, reason):
    locked_count, already = _bulk_set_lock(job_id, identity, client_ids, reason, True)
    message = f"تم قفل {locked_count} عميل"
    if already > 0:
        message += f" و {already} عميل مقفل بالفعل"
    return Result({"message": message, "status": True}, 200)


def run_bulk_unlock(job_id, identity, client_ids, reason):
    unlocked_count, already = _bulk_set_lock(job_id, identity, client_ids, reason, False)
    message = f"تم إلغاء قفل {unlocked_count} عميل"
    if already > 0:
        message += f" و {already} عميل غير مقفل بالفعل"
    return Result({"message": message, "status": True}, 200)


# --------------------------------------------------------------------------- #
# DELETE /clints   (legacy app.py:6017-6046)
# --------------------------------------------------------------------------- #

def run_delete_clints(job_id, identity, clints_ids):
    """Delete clients by id.

    Legacy aborted on the first missing id with 201 ``"العميل غير موجود"`` /
    ``"clint not found"``, having already deleted the ones before it.  Same here.

    Order matters: `clints."clintArchive_id"` -> `clintArchive.id` is the one FK into a
    client row that is **not** ``ON DELETE CASCADE`` (verified in `pg_constraint`), and
    `clintArchive.clint_id` points back, so the pointer is nulled, the archive rows are
    deleted, and only then the clients.  Comments / Payments / paymentDates /
    SepcifyUsers / client_lock_history / clint_documents all cascade in the database.
    """
    existing = {_uuid_key(c.id): c for c in fetch_clints_by(Clints.id, clints_ids)}
    ok_ids = []
    for clint_id in clints_ids:
        clint = existing.get(_uuid_key(clint_id))
        if clint is None:
            _delete(ok_ids, job_id)
            return Result({"message": "العميل غير موجود", "status": False,
                           "error": "clint not found"}, 201)
        ok_ids.append(clint.id)

    _delete(ok_ids, job_id)
    return Result({"message": "تم حذف العملاء بنجاح", "status": True}, 200)


def _delete(clint_ids, job_id=None):
    if not clint_ids:
        return
    total = len(clint_ids)
    done = 0
    for part in chunked(clint_ids, IN_CHUNK):
        db.session.execute(update(Clints).where(Clints.id.in_(part))
                           .values(clintArchive_id=None).execution_options(**_NO_SYNC))
        db.session.execute(delete(ClintArchive).where(ClintArchive.clint_id.in_(part))
                           .execution_options(**_NO_SYNC))
        db.session.execute(delete(Clints).where(Clints.id.in_(part))
                           .execution_options(**_NO_SYNC))
        done += len(part)
        progress(job_id, int(done * 100 / total), f"delete {done}/{total}")
    db.session.commit()


# --------------------------------------------------------------------------- #
# POST /send/notification   (legacy app.py:6631-6674 + send_notification_to_user 6591-6630)
# --------------------------------------------------------------------------- #

def run_send_notification(job_id, identity, file_path, index_column, message):
    """Insert one `Notification` per assigned client, commit, **then** emit.

    Legacy (`send_notification_to_user`, review P11) did ``User.query.get`` +
    ``INSERT`` + ``COMMIT`` + a blocking ``socketio.emit`` per row, inside the request.
    A 1,000-row file was 1,000 commits and 1,000 synchronous socket writes.

    Unchanged on the wire: the event name `alarm`, the room (``str(user_id)``), and the
    payload keys ``sender_user_id, client_id, accountNumber, message, type, id``.
    Clients with no collector are skipped, and a missing/unreadable file is still a
    success with nothing sent.
    """
    if not file_path:
        return Result({"message": "تم إرسال الإشعارات بنجاح", "status": True}, 200)

    selected = column_values(file_path, index_column)
    clints = {}
    for clint in fetch_clints_by(Clints.accountNumber, selected):
        clints.setdefault(clint.accountNumber, clint)

    # legacy resolved the receiver with User.query.get and skipped the row when it was
    # missing (an orphan clints.user_id); one IN query instead of one get per row.
    user_ids = {c.user_id for c in clints.values() if c.user_id is not None}
    known_users = set()
    for part in chunked(user_ids, IN_CHUNK):
        known_users.update(u.id for u in User.query.filter(User.id.in_(part)).all())

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pending = []          # (notification, receiver_id, client)
    for account_number in selected:
        clint = clints.get(account_number)
        if clint is None or clint.user_id is None or clint.user_id not in known_users:
            continue
        notification = Notification(
            sender_user_id=str(identity),
            receiver_user_id=str(clint.user_id),
            event="alarm",
            data={
                "sender_user_id": str(identity),
                "accountNumber": str(clint.accountNumber),
                "client_id": str(clint.id),
                "create_by": str(identity),
                "message": message,
                "date": now,
                "type": 3,
            },
        )
        pending.append((notification, clint.user_id, clint))

    total = len(pending) or 1
    done = 0
    for part in chunked(pending, 500):
        db.session.add_all([n for n, _u, _c in part])
        db.session.commit()
        done += len(part)
        progress(job_id, int(done * 100 / total), f"notifications {done}/{len(pending)}")

    # emit only after the rows are durable, and outside any open transaction
    for notification, receiver_id, clint in pending:
        try:
            socketio.emit('alarm', {
                'sender_user_id': str(identity),
                'client_id': str(clint.id),
                'accountNumber': str(clint.accountNumber),
                'message': message,
                'type': 3,
                'id': str(notification.id),
            }, room=str(receiver_id))
        except Exception as e:  # noqa: BLE001 - legacy swallowed emit errors per row
            log.warning("alarm emit to %s failed: %s", receiver_id, e)

    return Result({"message": "تم إرسال الإشعارات بنجاح", "status": True}, 200)
