"""Excel imports, bulk actions and notifications.

Routes are byte-compatible with the legacy ones in `app.py` - same paths (typos frozen:
`SepcifyUsersByExcel`, `commentsbyexcel`, `archivebyexcel`, `unarchivebyexcel`,
`portofolio`, `sepcifyusers`), same methods, same request parsing, same response keys and
Arabic strings, same status codes (success 200, business errors 201, and the handful of
400/404/500 the review pinned).  Only the implementation behind them changed - see
`services/imports.py` and `services/bulk_actions.py`.

Every heavy route additionally accepts **`?async=1`** (or ``{"async": true}`` in the body):
the request is validated synchronously - a bad file/portfolio/user still comes back
immediately with the legacy 201 body - and the work is then handed to `core.jobs`:

    HTTP 202 {"status": true, "job_id": "<hex>", "message": "تم بدء المعالجة في الخلفية",
              "async": true}

`GET /jobs/<job_id>` (already in app.py) then returns
``{"status": "queued|running|done|failed", "progress": 0-100, "message": "...",
"result": <the body the synchronous call would have returned>, ...}``, so the dialogs can
show the same summary they show today without changing how they read it.
"""
import logging
import os

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core import jobs
from core.auth import required_any, required_roles
from core.files import resolve_temp_file
from core.http import error_response
from extensions import db
from models import Clints, Contract
from services import bulk_actions, imports

bp = Blueprint("imports", __name__)
URL_PREFIX = None
log = logging.getLogger("cawp.api.imports")

ASYNC_ACCEPTED = "تم بدء المعالجة في الخلفية"
_TRUTHY = ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _wants_async(data=None):
    value = request.args.get("async")
    if value is not None:
        return str(value).strip().lower() in _TRUTHY
    if isinstance(data, dict):
        value = data.get("async")
        if isinstance(value, bool):
            return value
        if value is not None:
            return str(value).strip().lower() in _TRUTHY
    return False


def _accepted(job_id):
    return jsonify({"status": True, "job_id": job_id,
                    "message": ASYNC_ACCEPTED, "async": True}), 202


def _job(job_id, fn, *args):
    """Worker entry point: `core.jobs` pushes the app context, we own the session."""
    try:
        return fn(job_id, *args)
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.session.remove()


def _dispatch(name, fn, identity, *args, want_async=False):
    """Run `fn` now and return its legacy body, or queue it and return 202."""
    if want_async:
        return _accepted(jobs.submit(name, _job, fn, identity, *args, owner=identity))
    result = fn(None, identity, *args)
    return jsonify(result), getattr(result, "http_status", 200)


def _temp_path(file_name):
    """Resolve an uploaded workbook by basename, or None (== "file not present")."""
    if file_name is None:
        return None
    try:
        path = resolve_temp_file(file_name)
    except ValueError as e:
        log.warning("rejected temp file name %r: %s", file_name, e)
        return None
    return path if os.path.exists(path) else None


# --------------------------------------------------------------------------- #
# POST /clint/SepcifyUsersByExcel        legacy app.py:696-745  (supersedes)
# --------------------------------------------------------------------------- #

@bp.route('/clint/SepcifyUsersByExcel', methods=['POST'])
@jwt_required()
@required_roles('clints_sepcifyUser')
def sepcifyUsers():
    data = request.get_json()
    user_name = data['user_name']
    account_number = data['account_number']
    data_excel = data['data']
    identity = get_jwt_identity()
    try:
        return _dispatch("sepcify-users-by-excel", imports.run_sepcify_users_by_excel,
                         identity, user_name, account_number, data_excel,
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001 - legacy inner handler
        db.session.rollback()
        log.exception("sepcifyUsers failed: %s", e)
        return jsonify({"message": "حدث خطأ", "status": False}), 201


# --------------------------------------------------------------------------- #
# POST /clint/addClintsByExcel           legacy app.py:746-778 + 801-870 + 873-880
# --------------------------------------------------------------------------- #

@bp.route('/clint/addClintsByExcel', methods=['POST'])
@jwt_required()
def addUsers():
    try:
        data = request.get_json()
        contract_id = data['contract_id']
        portfolioName = data['name']
        data_excel = data['data']
        identity = get_jwt_identity()

        want_async = _wants_async(data)
        if want_async:
            # validate up front so a bad contract still fails with the legacy body
            if Contract.query.get(contract_id) is None:
                return jsonify({"message": "حدث خطأ", "status": False}), 201

        return _dispatch("add-clints-by-excel", imports.run_add_clints_by_excel,
                         identity, contract_id, portfolioName, data_excel,
                         want_async=want_async)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.exception("addUsers failed: %s", e)
        return jsonify({"message": "حدث خطأ", "status": False}), 201


# --------------------------------------------------------------------------- #
# POST /clints/update/byexcel            legacy app.py:1517-1602 + check_amount 7983-8005
# --------------------------------------------------------------------------- #

@bp.route('/clints/update/byexcel', methods=['POST'])
@jwt_required()
@required_roles('clints_UpdateByExcel')
def updateClintByExcel():
    try:
        data = request.get_json()
        data1 = data['data1']
        data2 = data['data2']
        update_type = data['update_type']
        update_side = data['update_side']
        fileDataRow = data['fileDataRow']
        identity = get_jwt_identity()

        return _dispatch("update-clints-by-excel", imports.run_update_by_excel,
                         identity, data1, data2, update_type, update_side, fileDataRow,
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.exception("updateClintByExcel failed: %s", e)
        return jsonify({"message": "حدث خطاء اثناء تحديث المستخدمين", "status": False}), 201


# --------------------------------------------------------------------------- #
# POST /clints/attribute/byexcel         legacy app.py:1617-1697 + 1605-1614
# --------------------------------------------------------------------------- #

@bp.route('/clints/attribute/byexcel', methods=['POST'])
@jwt_required()
@required_roles('clints_UpdateByExcel')
def updateClintAttributeByExcel():
    try:
        data = request.get_json() or {}

        identifier_column = data.get('identifier_column')
        identifier_type = data.get('identifier_type') or 'accountNumber'
        value_column = data.get('value_column')
        attribute_key = (data.get('attribute_key') or value_column or '').strip()
        fileDataRow = data.get('fileDataRow') or []

        if not identifier_column or not value_column:
            return jsonify({"message": "يرجى تحديد عمود التعريف وعمود القيمة", "status": False}), 201
        if identifier_type not in ('accountNumber', 'identityNumber'):
            return jsonify({"message": "نوع التعريف غير صالح", "status": False}), 201
        if not attribute_key:
            return jsonify({"message": "يرجى إدخال اسم الخاصية", "status": False}), 201
        if not fileDataRow:
            return jsonify({"message": "الملف لا يحتوي على بيانات", "status": False}), 201

        return _dispatch("attribute-by-excel", imports.run_attribute_by_excel,
                         get_jwt_identity(), identifier_column, identifier_type,
                         value_column, attribute_key, fileDataRow,
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ اثناء تحديث الخصائص", e)


# --------------------------------------------------------------------------- #
# POST /commentsbyexcel                  legacy app.py:3409-3482
# --------------------------------------------------------------------------- #

@bp.route('/commentsbyexcel', methods=['POST'])
@jwt_required()
@required_roles('clints_UpdateByExcel')
def createCommentByExcel():
    try:
        data = request.get_json()
        identity = get_jwt_identity()
        data['user_id'] = identity
        file_name = data['excel_data']
        index_column = data['index']
        want_async = _wants_async(data)

        # the remaining keys are splatted into Comments(**data), exactly as before
        for key in ('contract_id', 'data1', 'index', 'excel_data', 'async'):
            data.pop(key, None)

        file_path = _temp_path(file_name)
        return _dispatch("comments-by-excel", imports.run_comments_by_excel,
                         identity, data, file_path, index_column,
                         want_async=want_async and file_path is not None)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء التعليق", e)


# --------------------------------------------------------------------------- #
# PUT /clint/archivebyexcel              legacy app.py:5253-5293
# --------------------------------------------------------------------------- #

@bp.route('/clint/archivebyexcel', methods=['PUT'])
@jwt_required()
@required_any('clints_UpdateByExcel', 'clints_move_archive')  # clients list: Excel menu (UpdateByExcel) / archive key
def updateClintToArchiveByExcel():
    data = request.get_json()
    account_number = data['account_number']
    data_excel = data['data']
    identity = get_jwt_identity()
    try:
        return _dispatch("archive-by-excel", bulk_actions.run_archive_by_excel,
                         identity, account_number, data_excel,
                         data['archive_reason_id'], data['user_id'],
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.exception("updateClintToArchiveByExcel failed: %s", e)
        return jsonify({"message": "حدث خطأ", "status": False}), 201


# --------------------------------------------------------------------------- #
# PUT /clint/unarchivebyexcel            legacy app.py:5295-5349
# --------------------------------------------------------------------------- #

@bp.route('/clint/unarchivebyexcel', methods=['PUT'])
@jwt_required()
@required_any('clints_archive_move', 'clints_archive')  # archive screen (route key clints_archive)
def updateClintToUnArchiveByExcel():
    data = request.get_json()
    file_name = data['file_name']
    index_column = data['index']
    identity = get_jwt_identity()
    file_path = _temp_path(file_name)
    try:
        return _dispatch("unarchive-by-excel", bulk_actions.run_unarchive_by_excel,
                         identity, file_path, index_column,
                         want_async=_wants_async(data) and file_path is not None)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.exception("updateClintToUnArchiveByExcel failed: %s", e)
        return jsonify({"message": "حدث خطأ", "status": False}), 201


# --------------------------------------------------------------------------- #
# PUT /clint/archive/ids                 legacy app.py:5372-5404
# --------------------------------------------------------------------------- #

@bp.route('/clint/archive/ids', methods=['PUT'])
@jwt_required()
@required_roles('clints_move_archive')  # clients list: archive selected
def updateClintToArchiveByIds():
    try:
        data = request.get_json()
        clints_ids = data['clint_ids']
        return _dispatch("archive-by-ids", bulk_actions.run_archive_by_ids,
                         get_jwt_identity(), clints_ids,
                         data['archive_reason_id'], data['user_id'],
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العميل", e)


# --------------------------------------------------------------------------- #
# PUT /clint/archive/portofolio/<id>     legacy app.py:5939-5989
# --------------------------------------------------------------------------- #

@bp.route('/clint/archive/portofolio/<id>', methods=['PUT'])
@jwt_required()
@required_roles('clints_move_archive')  # decision 2026-09-24: portfolio-wide archive needs the archive key
def updateClintToArchiveByPortofolioId(id):
    try:
        data = request.get_json()
        identity = get_jwt_identity()
        want_async = _wants_async(data)

        if want_async:
            # the 404 is part of the wire contract, so it must not be deferred into a job
            exists = db.session.query(Clints.id).filter(
                Clints.portfolios_id == id, Clints.is_archive == False).first()  # noqa: E712
            if exists is None:
                return jsonify({"message": "لم يتم العثور على أي عملاء غير مؤرشفين في المحفظة",
                                "status": False}), 404

        return _dispatch("archive-portfolio", bulk_actions.run_archive_portfolio,
                         identity, id, data['archive_reason_id'], want_async=want_async)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء نقل عملاء المحفظة للأرشيف ", e, 500)


# --------------------------------------------------------------------------- #
# POST /sepcifyusers/ids                 legacy app.py:6047-6087
# --------------------------------------------------------------------------- #

@bp.route('/sepcifyusers/ids', methods=['POST'])
@jwt_required()
@required_roles('clints_sepcifyUser')
def sepcifyusers_ids():
    data = request.get_json()
    ids = data['clint_ids']
    user_id = data['user_id']
    identity = get_jwt_identity()
    try:
        return _dispatch("sepcify-users-by-ids", bulk_actions.run_sepcify_users_by_ids,
                         identity, ids, user_id, want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        log.exception("sepcifyusers_ids failed: %s", e)
        return jsonify({"message": "حدث خطأ", "status": False}), 201


# --------------------------------------------------------------------------- #
# PUT /clints/bulk-lock  /  bulk-unlock  legacy app.py:442-489 / 490-535
# --------------------------------------------------------------------------- #

@bp.route('/clints/bulk-lock', methods=['PUT'])
@jwt_required()
@required_any('clints_bulk_lock', 'clints_lock')  # clients list shows bulk lock on clints_lock
def bulk_lock_clients():
    try:
        data = request.get_json()
        client_ids = data.get('client_ids', [])
        reason = data.get('reason', 'قفل جماعي')
        if not client_ids:
            return jsonify({"message": "لم يتم تحديد أي عملاء", "status": False}), 400
        return _dispatch("bulk-lock", bulk_actions.run_bulk_lock,
                         get_jwt_identity(), client_ids, reason,
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء القفل الجماعي", e, 500)


@bp.route('/clints/bulk-unlock', methods=['PUT'])
@jwt_required()
@required_any('clints_bulk_lock', 'clints_lock')  # clients list shows bulk unlock on clints_lock
def bulk_unlock_clients():
    try:
        data = request.get_json()
        client_ids = data.get('client_ids', [])
        reason = data.get('reason', 'إلغاء قفل جماعي')
        if not client_ids:
            return jsonify({"message": "لم يتم تحديد أي عملاء", "status": False}), 400
        return _dispatch("bulk-unlock", bulk_actions.run_bulk_unlock,
                         get_jwt_identity(), client_ids, reason,
                         want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء إلغاء القفل الجماعي", e, 500)


# --------------------------------------------------------------------------- #
# DELETE /clints                         legacy app.py:6017-6046
# --------------------------------------------------------------------------- #

@bp.route('/clints', methods=['DELETE'])
@jwt_required()
@required_roles('clints_delete')
def deleteClints():
    try:
        data = request.get_json()
        clints_ids = data['clints_ids']
        return _dispatch("delete-clints", bulk_actions.run_delete_clints,
                         get_jwt_identity(), clints_ids, want_async=_wants_async(data))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف العملاء", e)


# --------------------------------------------------------------------------- #
# POST /send/notification                legacy app.py:6631-6674 + 6591-6630
# --------------------------------------------------------------------------- #

@bp.route('/send/notification', methods=['POST'])
@jwt_required()
@required_roles('clints_sepcifyUser')
def sendNotification():
    try:
        data = request.get_json()
        file_name = data.get("file_name")
        index_column = data.get("index")
        message = data.get("message")
        file_path = _temp_path(file_name)

        return _dispatch("send-notification", bulk_actions.run_send_notification,
                         get_jwt_identity(), file_path, index_column, message,
                         want_async=_wants_async(data) and file_path is not None)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء إرسال الإشعارات", e)
