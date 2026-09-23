"""CAWP collections API.

Layout:
    extensions.py   db / migrate / jwt / socketio / cors / compress singletons
    models.py       the 29 db.Model classes + `defult_permission`
    core/           config, auth (permission gate), cache (Redis+JSON), serialization
                    (orjson), lookups, jobs (background thread pool), http
                    (error_response), files (safe paths)
    services/       SQL builders + row serializers, no Flask (clients_query,
                    clients_scope, reports_query, report_scope, excel, imports,
                    bulk_actions)
    api/            blueprints, registered by `api.register_blueprints(app)`:
                      api/clients_reports  14 routes - client lists + client exports
                      api/reports          23 routes - comments / payments / PTP /
                                                       contracts / supervisors /
                                                       comment_status / uesrs export
                      api/dashboard         8 routes - /dashbord/* + the v1 /dashboard
                      api/imports          14 routes - Excel imports, bulk actions,
                                                       `?async=1` -> /jobs/<id>
    app.py          the Flask app + the remaining 105 routes (CRUD, lookups, auth,
                    uploads, documents, portfolios, CDR, notifications, health, jobs)

Wire-compatibility rules that this file deliberately preserves (see review section 6):
  * error responses keep HTTP 201 - the frontend treats 2xx as success and reads
    `status: false` from the body; 401 is never returned for a business error;
  * the spelling of every path and response key is frozen, typos included
    (`clints`, `uesrs`, `dashbord`, `sepcify`, `reson`, `frist_name`, ...).
"""
import datetime
import logging
import os
import time
import uuid
from functools import wraps
from urllib.parse import parse_qs

import openpyxl
import redis as redis_lib
import sentry_sdk
from flask import Flask, jsonify, redirect, request, send_file, url_for
from flask_jwt_extended import (create_access_token, current_user, get_jwt,
                                get_jwt_identity, jwt_required)
from flask_socketio import join_room
from sentry_sdk.integrations.flask import FlaskIntegration
from sqlalchemy import cast, text
from sqlalchemy.sql import func
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from classes.FilterServerSide import FilterServerSide
from core import cache, files, jobs
from core.auth import (has_permission, invalidate_permission_maps,
                       is_admin_identity, required_any, required_roles)
from core.config import Config
from core.permissions import role_permission_template
from core.files import (document_path, ensure_dirs, resolve_temp_file,
                        temp_file_path)
from core.http import error_response
from core.serialization import ORJSONProvider
from extensions import db, jwt, socketIO
from extensions import init_app as init_extensions
from models import *  # noqa: F401,F403 - the 29 models + defult_permission
from services import excel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
)

# Sentry was previously initialised unconditionally with the dsn line commented out:
# no error reporting at all, but the full instrumentation cost on every request.
if Config.SENTRY_DSN:
    sentry_sdk.init(
        dsn=Config.SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        enable_tracing=True,
        integrations=[FlaskIntegration(transaction_style="endpoint")],
    )

app = Flask(__name__)
app.config.from_object(Config)
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = Config.JWT_ACCESS_TOKEN_EXPIRES
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH
# SQLALCHEMY_DATABASE_URI + SQLALCHEMY_ENGINE_OPTIONS (pool_size 20, pre_ping,
# statement_timeout) come from core/config.py.

# orjson for responses: same RFC-822 datetime rendering as Flask's default provider,
# ~5-10x faster on the 5-7 MB dashboard payloads.
app.json = ORJSONProvider(app)

init_extensions(app)   # db, migrate, jwt, cors, compress, socketio
jobs.init_app(app)
ensure_dirs()          # <UPLOAD_DIR>/{temp,documents,exports}

log = app.logger

@socketIO.on('join')
def on_join(user_id):
    try:
        log.info("User joined room: %s", user_id)
        join_room(str(user_id))
    except Exception as e:
        log.exception("Error joining room: %s", e)


@app.teardown_request
def _rollback_failed_transaction(exc):
    """Safety net: never hand a connection with a failed transaction back to the pool.

    Flask-SQLAlchemy removes the session on teardown_appcontext, but an exception raised
    mid-request leaves the transaction in an aborted state that the next statement on the
    same connection inherits ("current transaction is aborted").
    """
    if exc is not None:
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- auth
# `has_permission` / `required_roles` / `invalidate_permission_maps` live in core/auth.py
# so the blueprints can import them directly (no `from app import ...` cycle).


def check_client_lock(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get client ID from various sources
        clint_id = None

        # Check if it's in the URL path
        if 'id' in kwargs:
            clint_id = kwargs['id']

        # Check if it's in the request data
        if request.method in ['POST', 'PUT'] and request.is_json:
            data = request.get_json()
            if 'clint_id' in data:
                clint_id = data['clint_id']

        if clint_id:
            clint = Clints.query.get(clint_id)
            if clint and clint.is_locked:
                return jsonify({
                    "message": "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
                    "status": False,
                    "error": "client_locked"
                }), 423  # HTTP 423 Locked status code

        return f(*args, **kwargs)
    return decorated_function


@jwt.user_identity_loader
def user_identity_lookup(user):
    return user['id']


@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    identity = jwt_data["sub"]
    return User.query.filter_by(id=identity).one_or_none()


@jwt.token_in_blocklist_loader
def check_if_token_revoked(_jwt_header, jwt_payload):
    """Revocation list in Redis (`jwt:block:<jti>`), TTL = the token's remaining lifetime.

    The old code set JWT_BLACKLIST_ENABLED and kept an in-memory `set()` that no loader
    ever consulted, so a stolen 24 h token could not be revoked. Best effort: if Redis is
    unreachable the token is treated as valid rather than logging everybody out.
    """
    jti = jwt_payload.get("jti")
    if not jti:
        return False
    try:
        return bool(cache.client().exists(f"jwt:block:{jti}"))
    except redis_lib.RedisError as e:
        log.warning("blocklist check failed (redis): %s", e)
        return False


# --------------------------------------------------------------- ops / health / jobs
@app.route('/health', methods=['GET'])
def health():
    """Liveness + dependency probe used by the Dockerfile HEALTHCHECK. No auth."""
    db_ok = True
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        db_ok = False
        log.warning("health: database check failed: %s", e)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
    payload = {
        "status": "ok" if db_ok else "error",
        "db": db_ok,
        "redis": cache.ping(),
        "version": "2.0",
    }
    return jsonify(payload), 200 if db_ok else 503


def _job_or_error(job_id):
    job = jobs.get(job_id)
    if not job:
        return None, (jsonify({"message": "job not found", "status": False}), 404)
    owner = job.get("owner")
    if owner is not None and owner != str(get_jwt_identity()) and not is_admin_identity():
        return None, (jsonify({"message": "You are not authorized to access this resource"}), 403)
    return job, None


@app.route('/jobs/<job_id>', methods=['GET'])
@jwt_required()
def get_job(job_id):
    job, err = _job_or_error(job_id)
    if err:
        return err
    return jsonify(job), 200


@app.route('/jobs/<job_id>/download', methods=['GET'])
@jwt_required()
def download_job_result(job_id):
    job, err = _job_or_error(job_id)
    if err:
        return err
    result = job.get("result")
    path = result.get("file") if isinstance(result, dict) else None
    if not path or not files.is_under_exports(path) or not os.path.exists(path):
        return jsonify({"message": "job not found", "status": False}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    try:
        token = get_jwt()
        jti = token.get("jti")
        exp = token.get("exp")
        ttl = int(exp - time.time()) if exp else int(Config.JWT_ACCESS_TOKEN_EXPIRES.total_seconds())
        if jti and ttl > 0:
            cache.client().setex(f"jwt:block:{jti}", ttl, b"1")
        return jsonify({"message": "تم تسجيل الخروج بنجاح", "status": True}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تسجيل الخروج", e)


@app.route("/")
def index():
    return redirect(url_for('login'))


# Add these routes after the existing client routes

# Lock client
@app.route('/clint/lock/<id>', methods=['PUT'])
@jwt_required()
@required_any('clints_lock', 'clints_bulk_lock')  # clients list row lock button (clints_lock)
def lock_client(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify({"message": "العميل غير موجود", "status": False, "error": "client not found"}), 404
        
        if clint.is_locked:
            return jsonify({"message": "العميل مقفل بالفعل", "status": False}), 400
        
        data = request.get_json()
        reason = data.get('reason', 'لا يوجد سبب محدد')
        current_user = get_jwt_identity()
        
        # Lock the client
        clint.is_locked = True
        
        # Create lock history record
        lock_history = ClientLockHistory(
            clint_id=clint.id,
            user_id=current_user,
            action='lock',
            reason=reason
        )
        
        db.session.add(lock_history)
        db.session.commit()
        
        
        return jsonify({"message": "تم قفل العميل بنجاح", "status": True}), 200
        
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء قفل العميل", e, 500)

# Unlock client
@app.route('/clint/unlock/<id>', methods=['PUT'])
@jwt_required()
@required_any('clints_lock', 'clints_bulk_lock')  # clients list row unlock button (clints_lock)
def unlock_client(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify({"message": "العميل غير موجود", "status": False, "error": "client not found"}), 404
        
        if not clint.is_locked:
            return jsonify({"message": "العميل غير مقفل", "status": False}), 400
        
        data = request.get_json()
        reason = data.get('reason', 'لا يوجد سبب محدد')
        current_user = get_jwt_identity()
        
        # Unlock the client
        clint.is_locked = False
        
        # Create unlock history record
        unlock_history = ClientLockHistory(
            clint_id=clint.id,
            user_id=current_user,
            action='unlock',
            reason=reason
        )
        
        db.session.add(unlock_history)
        db.session.commit()
        
        
        return jsonify({"message": "تم إلغاء قفل العميل بنجاح", "status": True}), 200
        
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إلغاء قفل العميل", e, 500)

# Get client lock history
@app.route('/clint/lock-history/<id>', methods=['GET'])
@jwt_required()
def get_client_lock_history(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify({"message": "العميل غير موجود", "status": False, "error": "client not found"}), 404
        
        history = (ClientLockHistory.query
                  .filter_by(clint_id=id)
                  .order_by(ClientLockHistory.created_at.desc())
                  .all())
        
        history_list = []
        for record in history:
            history_dict = record.as_dict()
            user = User.query.get(record.user_id)
            if user:
                history_dict['user'] = {
                    'id': user.id,
                    'name': f"{user.frist_name} {user.last_name}"
                }
            history_list.append(history_dict)
        
        return jsonify({"history": history_list, "status": True}), 200
        
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب تاريخ القفل", e, 500)


def _temp_excel_path(file_name):
    """Resolve an uploaded temp file by basename, or None when the name is unsafe.

    Replaces `os.path.join(app.root_path, "static/temp", file_name)` at seven call sites:
    `file_name` came straight from the query string / JSON body, so `../../` escaped the
    directory and pandas then returned a column of an arbitrary file (review S11).
    Falls back to the legacy `static/temp` so files uploaded before the refactor still work.
    """
    try:
        return resolve_temp_file(file_name)
    except ValueError as e:
        app.logger.warning("rejected temp file name %r: %s", file_name, e)
        return None


def _clint_document_path(file_name):
    """Absolute path of a stored client document, new location first, `static/` second.

    Rows created before the refactor point at <app_root>/static/clint_documents.
    """
    try:
        new_path = document_path(file_name)
    except ValueError as e:
        app.logger.warning("rejected document name %r: %s", file_name, e)
        return None
    if os.path.exists(new_path):
        return new_path
    legacy = os.path.join(files.LEGACY_DOCUMENTS_DIR, os.path.basename(str(file_name)))
    if os.path.exists(legacy):
        return legacy
    return new_path

def _save_uploaded_workbook(file):
    """Load an uploaded .xlsx **once** and persist it under <UPLOAD_DIR>/temp.

    The four upload routes used to call `openpyxl.load_workbook(file)` twice (2x parse,
    2x memory) and return the absolute server path in `excel_file` (path disclosure).
    Returns (worksheet, stored_basename); the basename is what the frontend echoes back
    as `file_name` on the follow-up request.
    """
    accounts = openpyxl.load_workbook(file)
    safe = secure_filename(file.filename or "") or "upload.xlsx"
    if not safe.lower().endswith((".xlsx", ".xlsm", ".xls")):
        safe += ".xlsx"
    stored_name = f"{uuid.uuid4().hex}_{safe}"
    accounts.save(temp_file_path(stored_name))
    return accounts.active, stored_name


def _no_file_response():
    """`request.files['file']` used to raise KeyError -> 500 before the try block."""
    return jsonify({"message": 'يرجى اختيار ملف', "file_name": '', 'status': False}), 201


@app.route('/api/file/upload', methods=['GET','POST'])
@jwt_required()
def file_upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            return _no_file_response()
        file = request.files['file']

        try:
            accounts_xls, stored_name = _save_uploaded_workbook(file)

            if not checkheader(accounts_xls):
                return jsonify({"message": "يرجى إضافة عنوان لكل عمود","file_name":'','status':False}),201
            headres =  getheaders(accounts_xls)

            excel = getdataasdict(accounts_xls,headres)

            # Return a JSON response with a success message
            return jsonify({"message": "تم رفع الملف بنجاح","file_name":file.filename,"excel_file":stored_name,"excel_data" : excel,"data":headres,'status':True}),200
        except Exception as e:
            app.logger.exception("file_upload failed: %s", e)
            return jsonify({"message": 'تأكد من صيغة الملف انه اكسل',"file_name":file.filename,'status':False}),201
    return jsonify({"message": 'Get',})


# get uploaf file for SepcifyUsersByExcel
@app.route('/api/file/upload/SepcifyUsersByExcel', methods=['POST'])
@jwt_required()
def SepcifyUsersByExcel():
    if request.method == 'POST':
        if 'file' not in request.files:
            return _no_file_response()
        file = request.files['file']

        try:
            accounts_xls, stored_name = _save_uploaded_workbook(file)

            headres =  getheaders(accounts_xls)
            #delete header none
            headres = [header for header in headres if header['header'] is not None]

            excel = getdataasdict(accounts_xls,headres)

            # Return a JSON response with a success message
            return jsonify({"message": "تم رفع الملف بنجاح","file_name":file.filename,"excel_file":stored_name,"excel_data" : excel,"data":headres,'status':True}),200
        except Exception as e:
            app.logger.exception("SepcifyUsersByExcel failed: %s", e)
            return jsonify({"message": 'تأكد من صيغة الملف انه اكسل',"file_name":file.filename,'status':False}),201
    return jsonify({"message": 'Get',})


# get uploaf file for SepcifyUsersByExcel
@app.route('/api/file/upload/ByExcel', methods=['POST'])
@jwt_required()
def ByExcel():
    if request.method == 'POST':
        if 'file' not in request.files:
            return _no_file_response()
        file = request.files['file']

        try:
            accounts_xls, stored_name = _save_uploaded_workbook(file)

            headres =  getheaders(accounts_xls)
            #delete header none
            headres = [header for header in headres if header['header'] is not None]

            excel = getdataasdict(accounts_xls,headres)

            # Return a JSON response with a success message
            return jsonify({"message": "تم رفع الملف بنجاح","file_name":file.filename,"excel_file":stored_name,"excel_data" : excel,"data":headres,'status':True}),200
        except Exception as e:
            app.logger.exception("ByExcel failed: %s", e)
            return jsonify({"message": 'تأكد من صيغة الملف انه اكسل',"file_name":file.filename,'status':False}),201
    return jsonify({"message": 'Get',})


# get uploaf file for SepcifyUsersByExcel
@app.route('/api/v2/file/upload/ByExcel', methods=['POST'])
@jwt_required()
def ByExcel_v2():
    if request.method == 'POST':
        if 'file' not in request.files:
            return _no_file_response()
        file = request.files['file']

        try:
            accounts_xls, stored_name = _save_uploaded_workbook(file)

            headres =  getheaders(accounts_xls)
            #delete header none
            headres = [header for header in headres if header['header'] is not None]

            # `excel_data` is the stored basename here: the frontend posts it straight back
            # as `file_name` (modelForm_V2.tsx), and the filter routes resolve it under
            # <UPLOAD_DIR>/temp (falling back to the legacy static/temp).
            return jsonify({"message": "تم رفع الملف بنجاح","file_name":file.filename,"excel_file":stored_name,"excel_data" : stored_name,"data":headres,'status':True}),200
        except Exception as e:
            app.logger.exception("ByExcel_v2 failed: %s", e)
            return jsonify({"message": 'تأكد من صيغة الملف انه اكسل',"file_name":file.filename,'status':False}),201
    return jsonify({"message": 'Get',})

    
    


     

    
        


       
    
#-------------------------------------------- functions


def checkheader(excel_cheet):
    headers = getheaders(excel_cheet)
    for header in headers:
        if header['header'] is None or header == "": # check if any value is missing
            return False
    return True

def getheaders(excel_cheet):
    headers = [{'header': cell.value} for cell in next(excel_cheet.rows)] # get the values of the first
    return headers


def getdataasdict(excel_cheet,headres):
    data = []
    for row in range(2, excel_cheet.max_row + 1):
        data.append(dict(zip([header['header'] for header in headres], [cell.value for cell in excel_cheet[row]])))
   
    return data
 


#--------------------------------------------- end function

# 404 error
@app.errorhandler(404)
def not_found(error=None):
    message = {
        'status': 404,
        'message': 'Not Found: ' + request.url,
    }
    resp = jsonify(message)
    resp.status_code = 404
    return resp

# 500 error
@app.errorhandler(500) 
def internal_error(error=None):
    message = {
        'status': 500,
        'message': 'Internal Error: ' + request.url,
    }
    resp = jsonify(message)
    resp.status_code = 500
    return resp

# 405 error
@app.errorhandler(405)
def method_not_allowed(error=None):
    message = {
        'status': 405,
        'message': 'Method Not Allowed: ' + request.url,
    }
    resp = jsonify(message)
    resp.status_code = 405
    return resp

@app.route('/login', methods=['POST'])
def login():
    ip_address = request.remote_addr
    try:
        data = request.get_json()
        username = data['username']
        password = data['password']
        user = User.query.filter_by(user_name=username).first()

        if user is None or not check_password_hash (user.password, password):
            log = LoginLog(username=username, ip_address=ip_address, outcome="failure")
            db.session.add(log)
            db.session.commit()
            return jsonify( {"message": "خطأ في اسم المستخدم أو كلمة المرور","status":False , "error": "user not found"}),401
        if not user.is_active:
            log = LoginLog(username=username, ip_address=ip_address, outcome="user not active")
            db.session.add(log)
            db.session.commit()
            return jsonify( {"message": "المستخدم غير نشط راجع مسؤول النظام","status":False , "error": "user not active"}),401
        
        tuser = user.as_dict()
        role = db.session.get(Role,tuser['role_id'])
        tuser['role'] = role.as_dict()
        branch = db.session.get(Branch,tuser['branch_id'])
        tuser['branch'] = branch.as_dict()
        access_token = create_access_token(identity=user.as_dict())
        log = LoginLog(username=username, ip_address=ip_address, outcome="success")
        db.session.add(log)
        db.session.commit()
        return jsonify( {"access_token": access_token,"status":True ,"user" : tuser}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تسجيل الدخول", e)

@app.route('/refresh', methods=['POST'])
@jwt_required()
def refresh():
        #SLEEP 10 SEC
      

        current_user = get_jwt_identity()
        user = User.query.filter_by(id=current_user).first()
        tuser = user.as_dict()
        role = db.session.get(Role,tuser['role_id'])
        tuser['role'] = role.as_dict()
        branch = db.session.get(Branch,tuser['branch_id'])
        tuser['branch'] = branch.as_dict()
        
        access_token = create_access_token(identity=user.as_dict())
 
        return jsonify( {"access_token": access_token,"status":True ,"user" : tuser }),200


# get LoginLog by user_id
@app.route('/loginLog/user/<id>', methods=['GET'])
@jwt_required()
def getLoginLog(id):
    try:
        loginLogList = []
        #get username by id from user 
        user = User.query.get(id)
        username = user.user_name
        # order before limiting: `.limit(10)` used to run before the Python sort at the
        # bottom, so the endpoint returned the *oldest* 10 logins (review 3.6).
        loginLog = LoginLog.query.filter_by(username=username).order_by(LoginLog.timestamp.desc()).limit(10).all()

        for log in loginLog:
            loginLogList.append(log.as_dict())

        #sort reverse
        loginLogList = sorted(loginLogList, key=lambda x: x['timestamp'], reverse=True)
        

        return jsonify( {"loginLog": loginLogList,"status":True }),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب بيانات تسجيل الدخول", e)
    

@app.route('/uesrs', methods=['GET'])
@jwt_required()
def getusers():
    """# === PATCH_DASHBOARD_FAST_V6 APPLIED === -- نفس شكل الاستجابة، بدون N+1، + (نشط فقط/بدون حد أقصى) إذا الطلب بدون فلتر حالة إطلاقًا (Select context)"""
    try:
        # لو الطلب ما فيه هذا الباراميتر أصلًا (بغض النظر عن قيمته)، نعتبره طلب Select
        # (زي /supervisor/add) ونرجع النشطين فقط بدون حد أقصى. لو موجود (حتى فارغ/0)،
        # نعتبره صفحة المستخدمين الرئيسية ونحافظ على نفس السلوك القديم تمامًا.
        _is_select_context = 'customeFilterStatusesValue[]' not in request.args

        users = User.query.order_by(User.created_at).all()
        user_ids = [u.id for u in users]

        roles_map = {r.id: r for r in Role.query.all()}
        nationalities_map = {n.id: n for n in Nationality.query.all()}
        branches_map = {b.id: b for b in Branch.query.all()}

        supervisor_users_all = (
            SupervisorUser.query.filter(SupervisorUser.user_id.in_(user_ids)).all()
            if user_ids else []
        )
        supervisors_map = {s.id: s for s in Supervisor.query.all()}
        supervisor_owner_ids = list({
            supervisors_map[su.supervisor_id].user_id
            for su in supervisor_users_all
            if su.supervisor_id in supervisors_map
        })
        supervisor_owner_users_map = (
            {u.id: u for u in User.query.filter(User.id.in_(supervisor_owner_ids)).all()}
            if supervisor_owner_ids else {}
        )
        supervisor_users_by_user_id = {}
        for su in supervisor_users_all:
            supervisor_users_by_user_id.setdefault(su.user_id, []).append(su)

        archiving_values = my_get_custom_filter_values(request, 'customeFilterArchivingStatusesValue[]', True)
        counts_query = db.session.query(
            Clints.user_id,
            func.count(Clints.id).label("cnt"),
            func.sum(Clints.amount).label("total"),
        )
        if archiving_values and len(archiving_values) != 0:
            counts_query = counts_query.filter(Clints.is_archive == archiving_values[0])
        counts_map = {row.user_id: row for row in counts_query.group_by(Clints.user_id).all()}

        user_list = []
        for user in users:
            tuser = user.as_dict()
            role = roles_map.get(tuser['role_id'])
            tuser['role'] = role.as_dict() if role else None
            tuser['name'] = tuser['frist_name'] + " " + tuser['last_name']
            nationality = nationalities_map.get(tuser['nationality_id'])
            branch = branches_map.get(tuser['branch_id'])
            tuser['branch'] = branch.as_dict() if branch else None
            tuser['nationality'] = nationality.as_dict() if nationality else None

            sup_list = []
            for su in supervisor_users_by_user_id.get(user.id, []):
                tsu = su.as_dict()
                sup = supervisors_map.get(su.supervisor_id)
                if sup:
                    tsup = sup.as_dict()
                    sup_owner = supervisor_owner_users_map.get(sup.user_id)
                    if sup_owner:
                        towner = sup_owner.as_dict()
                        towner['name'] = towner['frist_name'] + " " + towner['last_name']
                        tsup['user'] = towner
                    tsu['supervisor'] = tsup
                sup_list.append(tsu)
            tuser['supervisoruser'] = sup_list

            row = counts_map.get(user.id)
            tuser['count_clints'] = row.cnt if row else 0
            tuser['total_amount'] = float(row.total) if (row and row.total) else 0

            user_list.append(tuser)

        if _is_select_context:
            active_only = [u for u in user_list if u.get('is_active') is True]
            active_only = sorted(active_only, key=lambda u: (u.get('frist_name') or '') + (u.get('last_name') or ''))
            return jsonify({"users": active_only, "countRow": len(active_only)}), 200

    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المستخدمين", e)


    rows , count,_rows =srverSideMode(user_list,request)
    return jsonify( {"users": rows  ,"countRow" :count}),200


@app.route('/users/<id>', methods=['GET'])
@jwt_required()
def getuser(id):
    try:
        user = User.query.get(id)
        tuser = user.as_dict()
        role = db.session.get(Role,tuser['role_id'])
        tuser['role'] = role.as_dict()
        branch = db.session.get(Branch,tuser['branch_id'])
        tuser['branch'] = branch.as_dict()
        nationality = db.session.get(Nationality,tuser['nationality_id'])
        tuser['nationality'] = nationality.as_dict()
        if user is None:
            return jsonify( {"message": "المستخدم غير موجود","status":False , "error": "user not found"}),201
        
        return jsonify( tuser),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المستخدم", e)

# insert user 
@app.route('/uesrs', methods=['POST'])
@jwt_required()
@required_roles('users_add')
def create_user():
   
   
    try:
        data = request.get_json()
        #hash password
        data['password'] = generate_password_hash(data['password'], method='pbkdf2:sha256')
        user = User(**data)
        db.session.add(user)
        db.session.commit()
        invalidate_permission_maps()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء المستخدم", e)
      
    
    return jsonify( user.as_dict()),200

# update user
@app.route('/uesrs/<id>', methods=['PUT'])
@jwt_required()
def update_user(id):
     
    try:
        user = User.query.get(id)
        if user is None:
            return jsonify( {"message": "المستخدم غير موجود","status":False , "error": "user not found"}),201
        
        data = request.get_json()
        user.email = data['email'] if 'email' in data else user.email  
        user.frist_name = data['frist_name'] if 'frist_name' in data else user.frist_name
        user.last_name = data['last_name'] if 'last_name' in data else user.last_name
        
        # only change the password when a non-empty one was actually sent: every other
        # field is guarded with `'x' in data`, and a PATCH-style payload without
        # `password` used to raise KeyError -> rollback -> 201 (review 3.2).
        if data.get('password') and user.password != data['password']:
            user.password = generate_password_hash(data['password'], method='pbkdf2:sha256')
        

        user.mobile = data['mobile'] if 'mobile' in data else user.mobile
        user.user_name = data['user_name'] if 'user_name' in data else user.user_name
        user.role_id = data['role_id'] if 'role_id' in data else user.role_id
        user.nationality_id = data['nationality_id'] if 'nationality_id' in data else user.nationality_id
        user.branch_id = data['branch_id'] if 'branch_id' in data else user.branch_id
        user.contract_id = data['contract_id'] if 'contract_id' in data else user.contract_id
        user.extension = data['extension'] if 'extension' in data else user.extension
        user.target_amount = data['target_amount'] if 'target_amount' in data else user.target_amount
        user.target_percent = data['target_percent'] if 'target_percent' in data else user.target_percent
        user.target_is_percentage = data['target_is_percentage'] if 'target_is_percentage' in data else user.target_is_percentage
        user.is_active = data['is_active'] if 'is_active' in data else user.is_active
        user.identityNumber = data['identityNumber'] if 'identityNumber' in data else user.identityNumber

        db.session.commit()
        invalidate_permission_maps()
        return jsonify( user.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل المستخدم", e)

#inactive user
@app.route('/user/inactive/<id>' ,methods=['PUT'])
@jwt_required()
def inactive_user(id):
    try:
        user = User.query.get(id)
        if user is  None:
             return jsonify( {"message": "المستخدم غير موجود","status":False , "error": "user not found"}),201
        
        user.is_active = False
        db.session.commit()
        invalidate_permission_maps()
        return jsonify( {"status":True , "message": "تم تعطيل الحساب بنجاح"}  ),200
    except Exception as e:
         db.session.rollback()
         return error_response("حدث خطأ أثناء تعديل المستخدم", e)
    
#active user 
@app.route('/user/active/<id>',methods=['PUT'])
@jwt_required()
def active_user(id):
    try:
        user = User.query.get(id)
        if user is  None:
             return jsonify( {"message": "المستخدم غير موجود","status":False , "error": "user not found"}),201
        
        user.is_active = True
        db.session.commit()
        invalidate_permission_maps()
        return jsonify( {"status":True , "message": "تم تعطيل الحساب بنجاح"}  ),200
    except Exception as e:
         db.session.rollback()
         return error_response("حدث خطأ أثناء تعديل المستخدم", e)


@app.route('/roles', methods=['GET'])
@jwt_required()
def getroles():
    try:
        role_list = []
        roles = Role.query.all()
        for role in roles:
            role_list.append( role.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الأدوار", e)
    
    #----------- pagination ---------------
    rows , count,_rows =srverSideMode(role_list,request)
    #-------------------------------------

    return jsonify( {"roles": rows  ,"countRow" :count}),200
# insert role
@app.route('/roles', methods=['POST'])
@jwt_required()
@required_roles('roles_add')
def create_role():
   
   
    try:
        data = request.get_json()
        role = Role(**data)

        copy_defult_permission = role_permission_template()  # legacy + registered new keys
        permission = data['permission'] 
        if isinstance(permission, dict):
            # an object used to grant *every* key it contained, even `false` ones
            permission = [k for k, v in permission.items() if v is True]

        if permission is not None:
            for key in permission:
                if key in copy_defult_permission:
                        copy_defult_permission[key] = True

        
        role.permission = copy_defult_permission
       

        db.session.add(role)
        db.session.commit()
        invalidate_permission_maps()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء الدور", e)
      
    
    return jsonify( role.as_dict()),200

# update role
@app.route('/roles/<id>', methods=['PUT'])
@jwt_required()
@required_roles('roles_edit')
def update_role(id):
     
    try:
        role = Role.query.get(id)
        if role is None:
            return jsonify( {"message": "الدور غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()

        role.name = data['name'] if 'name' in data else role.name  
        role.permission = data['permission'] if 'permission' in data else role.permission
        copy_defult_permission = role_permission_template()  # legacy + registered new keys
        permission = data['permission'] 
        if isinstance(permission, dict):
            # an object used to grant *every* key it contained, even `false` ones
            permission = [k for k, v in permission.items() if v is True]

        if permission is not None:
            for key in permission:
                if key in copy_defult_permission:
                        copy_defult_permission[key] = True
            role.permission = copy_defult_permission

        else:
            role.permission = role.permission
        

        
        db.session.commit()
        invalidate_permission_maps()
        return jsonify( role.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الدور", e)

# get role by id
@app.route('/roles/<id>', methods=['GET'])
@jwt_required()
def getrole(id):
    try:
        role = Role.query.get(id)
        if role is None:
            return jsonify( {"message": "الدور غير موجود","status":False , "error": "role not found"}),201
        
        # get role permission
        trole =role.as_dict()
        permission = trole['permission']
        permission_list = []
        # get premission list keys if value ture
        for key in permission:
            if permission[key] == True:
                permission_list.append(key)
        trole['permission_arr'] = permission_list
        return jsonify( trole),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدور", e)
    
# get nationalities    
@app.route('/nationalities', methods=['GET'])
@jwt_required()
def getnationalities():
    try:
        nationalies_list = []
        nationalies =  Nationality.query.all()
        for nation in nationalies:
            nationalies_list.append( nation.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الجنسيات", e)
    
    #----------- pagination ---------------
    rows , count,_rows =srverSideMode(nationalies_list,request)
    #-------------------------------------

    return jsonify( {"nationalities": rows  ,"countRow" :count}),200

# get nationalities by id
@app.route('/nationalities/<id>', methods=['GET'])
@jwt_required()
def getnationality(id):
    try:
        nationality = Nationality.query.get(id)
        if nationality is None:
               return jsonify( {"message": "الجنسية غير موجود","status":False , "error": "role not found"}),201
        return  jsonify(nationality.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الجنسية", e)

# insert nationality
@app.route('/nationalities', methods=['POST'])
@jwt_required()
def create_nationality():
   
   
    try:
        data = request.get_json()
        nationality = Nationality(**data)
        db.session.add(nationality)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء الجنسية", e)
      
    
    return jsonify( nationality.as_dict()),200

# update nationality
@app.route('/nationalities/<id>', methods=['PUT'])
@jwt_required()
def update_nationality(id):
    try:
        nationality = Nationality.query.get(id)
        if nationality is None:
            return jsonify( {"message": "الجنسية غير موجودة","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        nationality.name = data['name'] if 'name' in data else nationality.name  
        db.session.commit()
        return jsonify( nationality.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الجنسية", e)


# get updateSides
@app.route('/updateSides', methods=['GET'])
@jwt_required()
def getupdateSides():
    try:
        updateSides_list = []
        updateSides =  UpdateSides.query.all()
        for updateSide in updateSides:
            updateSides_list.append( updateSide.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الجهات المحدثة", e)
    
    #----------- pagination ---------------
    rows , count,_rows =srverSideMode(updateSides_list,request)
    #-------------------------------------
    return jsonify( {"updateSides": rows  ,"countRow" :count}),200


# get updateSide by id
@app.route('/updateSides/<id>', methods=['GET'])
@jwt_required()
def getupdateSide(id):
    try:
        updateSide = UpdateSides.query.get(id)
        if updateSide is None:
               return jsonify( {"message": "الجهة المحدثة غير موجودة","status":False , "error": "role not found"}),201
        return  jsonify(updateSide.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الجهة المحدثة", e)  

# insert updateSide
@app.route('/updateSides', methods=['POST'])
@jwt_required()
def create_updateSide():
       
   
    try:
        data = request.get_json()
        updateSide = UpdateSides(**data)
        db.session.add(updateSide)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء الجهة المحدثة", e)
      
    
    return jsonify( updateSide.as_dict()),200

# update updateSide
@app.route('/updateSides/<id>', methods=['PUT'])
@jwt_required()
def update_updateSide(id):
    try:
        updateSide = UpdateSides.query.get(id)
        if updateSide is None:
            return jsonify( {"message": "الجهة المحدثة غير موجودة","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        updateSide.name = data['name'] if 'name' in data else updateSide.name  
        db.session.commit()
        return jsonify( updateSide.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الجهة المحدثة", e)


# get branches
@app.route('/branches', methods=['GET'])
@jwt_required()
def getbranches():
    try:
        branches_list = []
        branches =  Branch.query.all()
        for branch in branches:
            branches_list.append( branch.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الفروع", e)
    return jsonify( {"branches": branches_list}),200

# get branch by id
@app.route('/branches/<id>', methods=['GET'])
@jwt_required()
@required_roles('branches')
def getbranch(id):
    try:
        branch = Branch.query.get(id)
        if branch is None:
               return jsonify( {"message": "الفرع غير موجود","status":False , "error": "role not found"}),201
        return  jsonify(branch.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الفرع", e)
    
# insert branch
@app.route('/branches', methods=['POST'])
@jwt_required()
@required_roles('branches_add')
def create_branch():
   
   
    try:
        data = request.get_json()
        branch = Branch(**data)
        db.session.add(branch)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء الفرع", e)
      
    
    return jsonify( branch.as_dict()),200

# update branch
@app.route('/branches/<id>', methods=['PUT'])
@jwt_required()
@required_roles('branches_edit')
def update_branch(id):
    try:
        branch = Branch.query.get(id)
        if branch is None:
            return jsonify( {"message": "الفرع غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        branch.name = data['name'] if 'name' in data else branch.name  
        db.session.commit()
        return jsonify( branch.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الفرع", e)

 # get contracts


# get contract by id
@app.route('/contracts/<id>', methods=['GET'])
@jwt_required()
@required_roles('contracts')
def getcontract(id):
    try:
        contract = Contract.query.get(id)
        if contract is None:
               return jsonify( {"message": "العقد غير موجود","status":False , "error": "role not found"}),201
        return  jsonify(contract.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العقد", e)
    
# insert contract
@app.route('/contracts', methods=['POST'])
@jwt_required()
@required_roles('contracts_add')
def create_contract():
   
   
    try:
        data = request.get_json()
        contract = Contract(**data)
        db.session.add(contract)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء العقد", e)
      
    
    return jsonify( contract.as_dict()),200

# update contract
@app.route('/contracts/<id>', methods=['PUT'])
@jwt_required()
@required_roles('contracts_edit')
def update_contract(id):
    try:
        contract = Contract.query.get(id)
        if contract is None:
            return jsonify( {"message": "العقد غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        contract.name = data['name'] if 'name' in data else contract.name
        contract.monthly_target_percent = data['monthly_target_percent'] if 'monthly_target_percent' in data else contract.monthly_target_percent
        contract.monthly_target_amount = data['monthly_target_amount'] if 'monthly_target_amount' in data else contract.monthly_target_amount
        contract.monthly_target_is_percentage = data['monthly_target_is_percentage'] if 'monthly_target_is_percentage' in data else contract.monthly_target_is_percentage
        db.session.commit()
        return jsonify( contract.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العقد", e)

# deactivate contract
@app.route('/contract/deactivate/<id>' ,methods=['PUT'])
@jwt_required()
def deactivate_contract(id):
    try:
        contract = Contract.query.get(id)
        if contract is  None:
             return jsonify( {"message": "العقد غير موجود","status":False , "error": "contract not found"}),201
        
        contract.is_active = False
        db.session.commit()
        return jsonify( {"status":True , "message": "تم تعطيل العقد بنجاح"}  ),200
    except Exception as e:
         db.session.rollback()
         return error_response("حدث خطأ أثناء تعديل العقد", e)
    
#activate user 
@app.route('/contract/activate/<id>',methods=['PUT'])
@jwt_required()
def activate_user(id):
    try:
        contract = Contract.query.get(id)
        if contract is  None:
             return jsonify( {"message": "المستخدم غير موجود","status":False , "error": "user not found"}),201
        
        contract.is_active = True
        db.session.commit()
        return jsonify( {"status":True , "message": "تم إلغاء تعطيل العقد بنجاح"}  ),200
    except Exception as e:
         db.session.rollback()
         return error_response("حدث خطأ أثناء تعديل العقد", e)

  
from issable.db_connection import Issable_db


@app.route('/cdr', methods=['GET']) 
@jwt_required()
def getcdr():
    try:
      db_1 = Issable_db()
      dd = db_1.GetSubscription()
      return jsonify(dd ),200
        
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب السجلات", e)

@app.route('/cdr_by_date', methods=['GET'])
@jwt_required()
def getcdr_by_date():
    try:
        db_1 = Issable_db()
        # Get all query parameters
        args = request.args
        
        # Get start and end dates
        start_date = args.get('startDate')
        end_date = args.get('endDate')
        
        # Get numbers array - handle multiple values with same key
        numbers = args.getlist('numbers[]')  # This will get all values for 'numbers[]'
        
        if not start_date or not end_date:
            return jsonify({"message": "تاريخ غير موجود", "status": False, "error": "date not found"}), 400
        
        print("start_date", start_date)
        print("end_date", end_date)
        print("numbers", numbers)
        
        # Pass the dates and numbers to GetCallsByDate
        dd = db_1.GetCombinedCalls(start_date, end_date, numbers)
        return jsonify(dd), 200

    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب السجلات", e, 500)

@app.route('/cdr/recording', methods=['GET'])
@jwt_required()
def download_recording():
    # Get the recording path from query parameters
    recording_path = request.args.get('recordingPath')
    try:
        from io import BytesIO

        db_1 = Issable_db()
        # Get the recording file data as bytes
        file_data = db_1.download_call_recording(recording_path)
        if not file_data:
            return jsonify({"message": "تسجيل المكالمة غير موجود", "status": False, "error": "recording not found"}), 404
        
        # Create a BytesIO object from the file data
        file_buffer = BytesIO(file_data)
        file_buffer.seek(0)  # Reset buffer position to start
        
        # Extract just the filename from the path
        filename = os.path.basename(recording_path)
        print("Original recording_path:", recording_path)
        print("Extracted filename:", filename)
        
        # Return the file as a response with proper headers for download
        return send_file(
            file_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/octet-stream'
        )
    
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تحميل تسجيل المكالمة", e, 500)


@app.route('/v2/clints', methods=['GET'])
@jwt_required()
@required_roles('client_query')  # only the inquiry screen (/client_query) calls it
def getclints_v2():
    try:
        clints_list = []

        # Get parameters
        data = request.args
        
        # Check if any filters are set
        account_number = data.get('selectedAccountNumber_Qkey')
        customeFilrerAccountNumberValue = data.get('customeFilrerAccountNumberValue')
        customeFilterAttributeValue = data.get('customeFilterAttributeValue')
        file_name = data.get('selectedClients_Qkey[file_name]')
        #customeFilterMobileNumberValue
        customeFilterMobileNumberValue = data.get('customeFilterMobileNumberValue')


    # Return empty result if no filters are applied
        if not any([account_number, customeFilrerAccountNumberValue, customeFilterAttributeValue, file_name, customeFilterMobileNumberValue]):
            return jsonify({"clints": [], "countRow": 0, "totalAmount": 0}), 200

        # Get page number
        page = int(data['page']) if 'page' in data else 1
        # Get limit number
        pageSize = int(data['pageSize']) if 'pageSize' in data else 1000
        # Get index column 
        index_column = data.get('selectedClients_Qkey[index]')

        selected_clients = None

        # Handle Excel file filter
        if file_name is not None:
            file_path = _temp_excel_path(file_name)
            if file_path and os.path.exists(file_path):
                try:
                    # same strings pandas produced (services/excel.cell_str), without
                    # building a DataFrame for one column
                    selected_clients = excel.column_values(file_path, index_column)
                except Exception as e:
                    app.logger.exception("getclints_v2 failed: %s", e)

        # Get sort parameters
        field = data.get('sort[key]')
        sort = data.get('sort[order]')

        query = (
            db.session.query(
                Clints.id,
                Clints.accountNumber,
                Clints.amount,
                Clints.name,
                Clints.identityNumber,
                Clints.user_id,
                Clints.attribute,
                Clints.is_archive,
                Portfolios.id.label("portfolio_id"),
                Portfolios.name.label("portfolio_name"),
                Contract.id.label("contract_id"),
                Contract.name.label("contract_name"),
            )
            .join(Portfolios, Clints.portfolios_id == Portfolios.id)
            .join(Contract, Portfolios.contract_id == Contract.id)
            # explicit outer join so that ordering by ClintArchive.create_at below does not
            # make SQLAlchemy add `clintArchive` to the FROM list as a cross join, which
            # inflated both count() and paginate() (review P5)
            .outerjoin(ClintArchive, Clints.clintArchive_id == ClintArchive.id)
        )

        # Apply filters only if they are set
       
        if account_number:
            query = query.filter(Clints.accountNumber == account_number)

      
        if selected_clients:
            query = query.filter(Clints.accountNumber.in_(selected_clients))

        if customeFilrerAccountNumberValue:
            query = query.filter(Clints.accountNumber == customeFilrerAccountNumberValue)

        if customeFilterAttributeValue:
            from sqlalchemy import String
            query = query.filter(cast(Clints.attribute, String).contains(customeFilterAttributeValue))
       
        if customeFilterMobileNumberValue:
            # subquery instead of round-tripping every matching identityNumber into an
            # IN (...) list of literals (review P5)
            query = query.filter(Clints.identityNumber.in_(
                db.session.query(ClintContacts.identityNumber).filter(
                    ClintContacts.contact.like(f'%{customeFilterMobileNumberValue}%')
                )
            ))
       
        # Apply order by
        if field and sort:
            if field == 'clint_amount':
                query = query.order_by(Clints.amount.desc() if sort == 'desc' else Clints.amount.asc())
            if field == 'archive_create_at':
                query = query.order_by(ClintArchive.create_at.desc() if sort == 'desc' else ClintArchive.create_at.asc())

        count = query.count()
        # Paginate the results
        pagination = query.paginate(page=page, per_page=pageSize, error_out=False)
        clints = pagination.items

        clints_list = [
            {
                "id": str(row.id),
                "accountNumber": row.accountNumber,
                "amount": float(row.amount),
                "name": row.name,
                "identityNumber": row.identityNumber,
                "user_id": str(row.user_id) if row.user_id else None,
                "is_archive": row.is_archive,
                "portfolios": {
                    "id": str(row.portfolio_id),
                    "name": row.portfolio_name
                },
                "contract": {
                    "id": str(row.contract_id),
                    "name": row.contract_name
                },
            }
            for row in clints
        ]

    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العملاء", e)
    
    return jsonify({"clints": clints_list, "countRow": count, "totalAmount": 0}), 200


@app.route('/clints/<id>', methods=['GET']) 
@jwt_required()
def getclint(id):
    try:
        #get current user
        current_user = get_jwt_identity()
        # A SuperAdmin without a Supervisor row used to fall into the
        # `user_id == current_user` branch and always get "العميل غير موجود", which
        # contradicts _build_clints_payload (same test, full scope) - review 3.13.
        user_obj = db.session.get(User, current_user)
        role_obj = db.session.get(Role, user_obj.role_id) if user_obj is not None else None
        is_super_admin = (role_obj is not None and role_obj.name == "SuperAdmin")
        #check if user Supervisor table
        is_super = Supervisor.query.filter_by(user_id=current_user).first()
        if is_super_admin:
            #unrestricted scope
            clint = Clints.query.filter_by(id=id).first()
        elif is_super is  None:
            #get clints by user_id
            clint = Clints.query.filter_by(id=id,user_id=current_user).first()
        else:
            #get all clints
            clint = getClintBySupervisor(clint_id=id,supervisor_id=is_super.id)

        if clint is None:
               return jsonify( {"message": "العميل غير موجود","status":False , "error": "role not found"}),201
      
        tclint = clint.as_dict()
        if clint.portfolios_id is not None:
            portfolios = Portfolios.query.get(clint.portfolios_id)
            contract = Contract.query.get(portfolios.contract_id)
            tcontract = contract.as_dict()
            tclint['portfolios'] = portfolios.as_dict()
            tclint['contract'] = tcontract
            user = User.query.get(clint.user_id) if clint.user_id is not None else None
            if user is not None:
                tuser = user.as_dict()
                tuser['name'] = tuser['frist_name'] + " " + tuser['last_name']
                tclint['user'] = tuser
            else:
                tclint['user'] = None

        
        
        return  jsonify(tclint),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العميل", e)
    
 
 #update clint
@app.route('/clints/<id>', methods=['PUT'])
@jwt_required()
@required_roles('clints_details')  # the case page's update-data tab
@check_client_lock
def update_clint(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify( {"message": "العميل غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        clint.name = data['name'] if 'name' in data else clint.name  
        clint.identityNumber = data['identityNumber'] if 'identityNumber' in data else clint.identityNumber
        clint.accountNumber = data['accountNumber'] if 'accountNumber' in data else clint.accountNumber
        clint.amount = data['amount'] if 'amount' in data else clint.amount
        clint.user_id = data['user_id'] if 'user_id' in data else clint.user_id
        clint.attribute = data['attribute'] if 'attribute' in data else clint.attribute
        db.session.commit()
    
        

        return jsonify( clint.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العميل", e)

 #get ClintContactTypes 

#update just DelinquencyReason_id for clint
@app.route('/clints/delinquency/<id>', methods=['PUT'])
@jwt_required()
@required_roles('clints_details')  # set from the case page's notes tab
def update_clint_delinquency(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify( {"message": "العميل غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        clint.delinquencyReason_id = data['delinquencyReason_id'] if 'delinquencyReason_id' in data else clint.delinquencyReason_id
        db.session.commit()
    
        

        return jsonify( clint.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العميل", e)
    


#get reson archive
@app.route('/clints/archive/reson', methods=['GET'])
@jwt_required()
def getArchiveReason():
    try:
        reson_archive_list = []
        reson_archives = ArchiveReason.query.all()
        for reson_archive in reson_archives:
            reson_archive_list.append( reson_archive.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب أسباب الأرشفة", e)
    
    
    #pagination
    reson_archive_list ,countRow = pagination_filter_clients(request,reson_archive_list)
    return jsonify( {"reson_archive": reson_archive_list ,"countRow":countRow}),200

@app.route('/clints/archive/reson', methods=['POST'])
@jwt_required()
def createArchiveReason():
    try:
        data = request.get_json()
        archive_reason = ArchiveReason(**data)
        db.session.add(archive_reason)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء سبب الأرشفة", e)
      
    
    return jsonify( archive_reason.as_dict()),200

@app.route('/clints/archive/reson/<id>', methods=['GET'])
@jwt_required()
def getArchiveReasonById(id):
    try:
        archive_reason = ArchiveReason.query.get(id)
        if archive_reason is None:
               return jsonify( {"message": "سبب الأرشفة غير موجود","status":False , "error": "role not found"}),201
        return  jsonify(archive_reason.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب سبب الأرشفة", e)
    
@app.route('/clints/archive/reson/<id>', methods=['PUT'])
@jwt_required()
def updateArchiveReason(id):
    try:
        archive_reason = ArchiveReason.query.get(id)
        if archive_reason is None:
            return jsonify( {"message": "سبب الأرشفة غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        archive_reason.name = data['name'] if 'name' in data else archive_reason.name  
        db.session.commit()
        return jsonify( archive_reason.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل سبب الأرشفة", e)

#DelinquencyReasons
#get DelinquencyReasons
@app.route('/clints/delinquency_reasons', methods=['GET'])
@jwt_required()
def getDelinquencyReasons():
    try:
        delinquency_reasons_list = []
        delinquency_reasons = DelinquencyReason.query.all()
        for delinquency_reason in delinquency_reasons:
            delinquency_reasons_list.append( delinquency_reason.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب أسباب التأخير", e)
    return jsonify( {"delinquency_reasons": delinquency_reasons_list}),200

#create DelinquencyReasons
@app.route('/clints/delinquency_reasons', methods=['POST'])
@jwt_required()
def createDelinquencyReasons():
    try:
        data = request.get_json()
        delinquency_reason = DelinquencyReason(**data)
        db.session.add(delinquency_reason)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء سبب التأخير", e)
      
    
    return jsonify( delinquency_reason.as_dict()),200

#get  DelinquencyReasons by id
@app.route('/clints/delinquency_reasons/<id>', methods=['GET'])
@jwt_required()
def getDelinquencyReasonsById(id):
    try:
        delinquency_reason = DelinquencyReason.query.get(id)
        if delinquency_reason is None:
               return jsonify( {"message": "سبب التأخير غير موجود","status":False , "error": "role not found"}),201
        return  jsonify(delinquency_reason.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب سبب التأخير", e)

#update DelinquencyReasons
@app.route('/clints/delinquency_reasons/<id>', methods=['PUT'])
@jwt_required()
def updateDelinquencyReasons(id):   
    try:
        delinquency_reason = DelinquencyReason.query.get(id)
        if delinquency_reason is None:
            return jsonify( {"message": "سبب التأخير غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        delinquency_reason.name = data['name'] if 'name' in data else delinquency_reason.name  
        db.session.commit()
        return jsonify( delinquency_reason.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل سبب التأخير", e)


@app.route('/clint_contact_types', methods=['GET'])
@jwt_required()
def getClintContactTypes():
    try:
        clint_contact_types_list = []
        clint_contact_types =  ClintContactTypes.query.all()
        for clint_contact_type in clint_contact_types:
            clint_contact_types_list.append( clint_contact_type.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب أنواع الاتصال", e)
    return jsonify( {"clint_contact_types": clint_contact_types_list}),200

#create clint contacts
@app.route('/clint_contacts', methods=['POST'])
@jwt_required()
def createClintContacts():
    try:
        data = request.get_json()
        clint_contact = ClintContacts(**data)
        db.session.add(clint_contact)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء جهة الاتصال", e)
      
    tclintContact =clint_contact.as_dict()
    contactType = ClintContactTypes.query.get(clint_contact.contact_type_id)
    tclintContact['contact_type'] = contactType.as_dict()
    
    return jsonify( tclintContact),200


#add comment status
@app.route('/comment_status' , methods =['POST'])
@jwt_required()
def createCommentStatus():
    try:
        data = request.get_json()
        comment_status = CommentStatuses(**data)
        db.session.add(comment_status)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء حالة التعليق", e)
      
    
    return jsonify( comment_status.as_dict()),200

#get comment status by id
@app.route('/comment_status/<id>' , methods =['GET'])
@jwt_required()
def getCommentStatus(id):
    try:
        comment_status = CommentStatuses.query.get(id)
        if comment_status is None:
               return jsonify( {"message": "حالة التعليق غير موجودة","status":False , "error": "comment_status not found"}),201
        return  jsonify(comment_status.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب حالة التعليق", e)
    

#update comment status
@app.route('/comment_status/<id>' , methods =['PUT'])
@jwt_required()
def updateCommentStatus(id):
    try:
        comment_status = CommentStatuses.query.get(id)
        if comment_status is None:
            return jsonify( {"message": "حالة التعليق غير موجودة","status":False , "error": "comment_status not found"}),201
        
        data = request.get_json()
        comment_status.name = data['name'] if 'name' in data else comment_status.name  
        comment_status.contract_id = data['contract_id'] if 'contract_id' in data else comment_status.contract_id
        comment_status.CommentReachability_id = data['CommentReachability_id'] if 'CommentReachability_id' in data else comment_status.CommentReachability_id
        comment_status.is_active = data['is_active'] if 'is_active' in data else comment_status.is_active   
        comment_status.color = data['color'] if 'color' in data else comment_status.color
        db.session.commit()
        return jsonify( comment_status.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل حالة التعليق", e)
    
@app.route('/comment_reachability' , methods =['GET'])
@jwt_required()
def commentReachability():
    try:
        comment_reachability_list = []
        comment_reachabilitys =  CommentReachability.query.all()
        for comment_reachability in comment_reachabilitys:
            comment_reachability_list.append( comment_reachability.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب العملاء", e)
    return jsonify( {"comment_reachabilitys": comment_reachability_list}),200


@app.route('/comments' , methods =['POST'])
@jwt_required()
@check_client_lock  # Add this line
def createComment():
    try:
        #time.sleep(2)
       
        data = request.get_json()
        comment = Comments(**data)
        db.session.add(comment)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء التعليق", e)
      
    
    return jsonify( comment.as_dict()),200


    

    
      
    


# ---- helpers added by patch_white_page_v1 (safe defaults for nested objects) ----
def _wp_empty_contract():
    return {"id": None, "name": "", "contract_name": ""}

def _wp_empty_portfolio():
    p = {"id": None, "name": "", "portfolios_name": ""}
    p["contract"] = _wp_empty_contract()
    return p

def _wp_empty_user():
    return {"id": None, "name": "", "frist_name": "", "last_name": "", "branch_id": None}

def _wp_empty_clint():
    c = {"id": None, "name": "", "clint_name": "", "portfolios_id": None}
    c["portfolios"] = _wp_empty_portfolio()
    return c

def _wp_user_dict(u):
    if u is None:
        return _wp_empty_user()
    d = u.as_dict()
    d["name"] = ((d.get("frist_name") or "") + " " + (d.get("last_name") or "")).strip()
    return d

def _wp_normalize(row, kind):
    """kind: 'comment' | 'payment'"""
    if not isinstance(row.get("user"), dict):
        row["user"] = _wp_empty_user()
    if not isinstance(row.get("clint"), dict):
        row["clint"] = _wp_empty_clint()
    if not isinstance(row["clint"].get("portfolios"), dict):
        row["clint"]["portfolios"] = _wp_empty_portfolio()
    if not isinstance(row["clint"]["portfolios"].get("contract"), dict):
        row["clint"]["portfolios"]["contract"] = _wp_empty_contract()
    if not isinstance(row.get("portfolios"), dict):
        row["portfolios"] = row["clint"]["portfolios"]
    if not isinstance(row["portfolios"].get("contract"), dict):
        row["portfolios"]["contract"] = _wp_empty_contract()
    if kind == "comment":
        if not isinstance(row.get("status"), dict):
            row["status"] = {"id": None, "name": ""}
        if not isinstance(row.get("contact_method"), dict):
            row["contact_method"] = {"id": None, "name": ""}
        if not isinstance(row.get("reachability"), dict):
            row["reachability"] = {"id": None, "name": ""}
    else:
        if not isinstance(row.get("collection_type"), dict):
            row["collection_type"] = {"id": None, "name": ""}
        if not isinstance(row.get("collection_method"), dict):
            row["collection_method"] = {"id": None, "name": ""}
        if not isinstance(row.get("approval_by"), dict):
            row["approval_by"] = _wp_empty_user()
    return row
# ---- end helpers ----


@app.route('/comments/<id>' , methods =['GET'])
@jwt_required()
def getComment(id):
    try:
        comment = Comments.query.get(id)
        if comment is None:
               return jsonify( {"message": "التعليق غير موجود","status":False , "error": "comment not found"}),201
        return  jsonify(comment.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب التعليق", e)
    


#delete comments
@app.route('/comments/<id>' , methods =['DELETE'])
@jwt_required()
def deleteComment(id):
    try:
        comment = Comments.query.get(id)
        if comment is None:
            return jsonify( {"message": "التعليق غير موجود","status":False , "error": "comment not found"}),201
        # Same rule as the case timeline: `clints_comments_delete`, or the author within
        # 60 s of writing it (+15 s grace for network and clock skew).
        if not has_permission('clints_comments_delete'):
            age = (datetime.datetime.now(datetime.timezone.utc) - comment.create_at).total_seconds()
            if str(comment.user_id) != str(get_jwt_identity()) or age > 75:
                return jsonify({'message': 'You are not authorized to access this resource'}), 403
        db.session.delete(comment)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف التعليق", e)
    return jsonify( {"message": "تم حذف التعليق بنجاح","status":True}),200

#get contatcs by identityNumber
@app.route('/contacts/identityNumber/<identityNumber>' , methods =['GET'])
@jwt_required()
def getContactsByIdentityNumber(identityNumber):
    try:
        contacts_list = []
        contacts =  ClintContacts.query.filter_by(identityNumber=identityNumber).order_by(ClintContacts.create_at.desc())
        for contact in contacts:
            tcontact = contact.as_dict()
            contactType = ClintContactTypes.query.get(contact.contact_type_id)
            tcontact['contact_type'] = contactType.as_dict()

            #check if tcontact contact is reapet in contacts_list
            for contact in contacts_list:
                if contact['contact'] == tcontact['contact']:
                    break
            else:
                contacts_list.append(tcontact)
            

          
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب جهات الاتصال", e)
    return jsonify( {"contacts": contacts_list}),200

#get collection types
@app.route('/collection_types' , methods =['GET'])
@jwt_required()
def getCollectionTypes():
    try:
        collection_types_list = []
        collection_types =  CollectionTypes.query.all()
        for collection_type in collection_types:
            collection_types_list.append( collection_type.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب انواع التحصيل", e)
    return jsonify( {"collection_types": collection_types_list}),200

#get collecton Methods
@app.route('/collection_methods' , methods =['GET'])
@jwt_required()
def getCollectionMethods():
    try:
        collection_methods_list = []
        collection_methods =  CollectionMethods.query.all()
        for collection_method in collection_methods:
            collection_methods_list.append( collection_method.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب طرق التحصيل", e)
    
    #pagination
    collection_methods_list ,countRow = pagination_filter_clients(request,collection_methods_list)
    return jsonify( {"collection_methods": collection_methods_list ,"countRow":countRow}),200

#add collection method
@app.route('/collection_methods' , methods =['POST'])
@jwt_required()
def createCollectionMethod():
    try:
        data = request.get_json()
        collection_method = CollectionMethods(**data)
        db.session.add(collection_method)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء طريقة التحصيل", e)
      
    
    return jsonify( collection_method.as_dict()),200

#get collecton Methods by id
@app.route('/collection_methods/<id>' , methods =['GET'])
@jwt_required()
def getCollectionMethodsById(id):
    try:
        collection_method = CollectionMethods.query.get(id)
        if collection_method is None:
               return jsonify( {"message": "طريقة التحصيل غير موجودة","status":False , "error": "collection_method not found"}),201
        return  jsonify(collection_method.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب طريقة التحصيل", e)
    
#update collection method
@app.route('/collection_methods/<id>' , methods =['PUT'])
@jwt_required()
def updateCollectionMethod(id):
    try:
        collection_method = CollectionMethods.query.get(id)
        if collection_method is None:
            return jsonify( {"message": "طريقة التحصيل غير موجودة","status":False , "error": "collection_method not found"}),201
        
        data = request.get_json()
        collection_method.name = data['name'] if 'name' in data else collection_method.name  
        db.session.commit()
        return jsonify( collection_method.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل طريقة التحصيل", e)
    


    


#get payements by id
@app.route('/payment/<id>' , methods =['GET'])
@jwt_required()
def getPaymentsById(id):
    try:
        payment = Payments.query.get(id)
        if payment is None:
               return jsonify( {"message": "الدفعة غير موجودة","status":False , "error": "payment not found"}),201
        
        return  jsonify(payment.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الدفعة", e)

#update payment
@app.route('/payment/<id>' , methods =['PUT'])
@jwt_required()
@required_any('accounts_payments', 'collections_users')  # edit button on /payment and /user_collection
def updatePayment(id):
    try:
        payment = Payments.query.get(id)
        if payment is None:
            return jsonify( {"message": "الدفعة غير موجودة","status":False , "error": "payment not found"}),201
        
        data = request.get_json()

        #check if total amount is equal to payments amount or less than
        payments = Payments.query.filter_by(clint_id=payment.clint_id,approval_status=True )
        payments = payments.filter(Payments.id != id)

        clint_amount = Clints.query.get(payment.clint_id)
        total_amount = 0

        for payment_ in payments:
            total_amount = total_amount + payment_.amount

   
        data_amount = int(data['amount'])

        if total_amount + data_amount > clint_amount.amount:
            return jsonify( {"message": "المبلغ المدفوع أكبر من المبلغ المطلوب","status":False , "error": "amount not valid"}),201

        payment.amount = data['amount'] if 'amount' in data else payment.amount  
        payment.collection_type_id = data['collection_type_id'] if 'collection_type_id' in data else payment.collection_type_id
        payment.collection_method_id = data['collection_method_id'] if 'collection_method_id' in data else payment.collection_method_id
        payment.user_id = data['user_id'] if 'user_id' in data else payment.user_id
        payment.payment_date = data['payment_date'] if 'payment_date' in data else payment.payment_date
        db.session.commit()
        return jsonify( payment.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الدفعة", e)  


#delete payment by id
@app.route('/payments/<id>' , methods =['DELETE'])
@jwt_required()
@required_roles('accounts_payments')  # delete button on /payment
def deletePayment(id):
    try:
        payment = Payments.query.get(id)
        if payment is None:
            return jsonify( {"message": "الدفعة غير موجودة","status":False , "error": "payment not found"}),201
        
        db.session.delete(payment)
        db.session.commit()
        return jsonify( {"message": "تم حذف الدفعة بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف الدفعة", e)

#create payment
@app.route('/payment' , methods =['POST'])
@jwt_required()
@check_client_lock
def createPayment():
    try:
        #time.sleep(2)
        data = request.get_json()
        #check if total amount is equal to payments amount or less than
        payments = Payments.query.filter_by(clint_id=data['clint_id'],approval_status=True)
        clint_amount = Clints.query.get(data['clint_id'])
        total_amount = 0
        for payment in payments:
            total_amount = total_amount + payment.amount
            
        if total_amount + data['amount'] > clint_amount.amount:
            return jsonify( {"message": "المبلغ المدفوع أكبر من المبلغ المطلوب","status":False , "error": "amount not valid"}),201
        

        payment = Payments(**data)
        db.session.add(payment)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء الدفعة", e)
      
    
    return jsonify( payment.as_dict()),200

#approve payment by id
@app.route('/payment/approve/<id>' , methods =['PUT'])
@jwt_required()
@required_roles('collections_users')  # supervisors only (decision 2026-09-23); collectors' "my collections" hides approve
def approvePayment(id):
    try:
        payment = db.session.get(Payments, id)
        if payment is None:
            return jsonify( {"message": "الدفعة غير موجودة","status":False , "error": "payment not found"}),201

        # Phase 1 fix (review/phase1-money.md): lock the case row, re-read the payment under
        # its row lock, refuse an already decided payment, and compare the approved total
        # + this payment with the debt exactly in numeric. Two concurrent approvals on one
        # case now serialize and can no longer exceed the debt (the old float loop ran
        # without a lock). Same responses as before, plus one "already decided" answer.
        from services.money import legacy_approval_verdict
        verdict = legacy_approval_verdict(payment)
        if verdict == "decided":
            db.session.rollback()
            return jsonify( {"message": "تم البت في هذه الدفعة مسبقاً","status":False , "error": "payment already decided"}),201
        if verdict == "amount":
            db.session.rollback()
            return jsonify( {"message": "المبلغ المدفوع أكبر من المبلغ المطلوب","status":False , "error": "amount not valid"}),201

        payment.approval_status = True
        payment.approval_by = current_user.id
        payment.approval_at = datetime.datetime.now()
        db.session.commit()
        return jsonify( payment.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الدفعة", e)


#reject payment by id
@app.route('/payment/reject/<id>' , methods =['PUT'])
@jwt_required()
@required_roles('collections_users')  # supervisors only (decision 2026-09-23); collectors' "my collections" hides reject
def rejectPayment(id):
    try:
        payment = Payments.query.get(id)
        if payment is None:
            return jsonify( {"message": "الدفعة غير موجودة","status":False , "error": "payment not found"}),201
        
       
        payment.approval_status = False
        payment.approval_by = current_user.id
        payment.approval_at = datetime.datetime.now()
        payment.approval_comment = request.get_json()['approval_comment']
        db.session.commit()
        return jsonify( payment.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل الدفعة", e)


#get contact methods
@app.route('/contact_methods' , methods =['GET'])
@jwt_required()
def getContactMethods():
    try:
        contact_methods_list = []
        contact_methods =  ContactMethods.query.all()
        for contact_method in contact_methods:
            contact_methods_list.append( contact_method.as_dict())
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب طرق التواصل", e)
    return jsonify( {"contact_methods": contact_methods_list}),200

#create payment date 
@app.route('/payment_date' , methods =['POST'])
@jwt_required()
@check_client_lock
def createPaymentDate():
    try:
        #time.sleep(2)
        data = request.get_json()
        #check if total amount is equal to payments amount or less than
        payments = Payments.query.filter_by(clint_id=data['clint_id'], approval_status=True)
        clint_amount = Clints.query.get(data['clint_id'])
        total_amount = 0
        for payment in payments:
            total_amount = total_amount + payment.amount
            
        if total_amount + data['amount'] > clint_amount.amount:
            return jsonify( {"message": "المبلغ السداد أكبر من المبلغ المطلوب","status":False , "error": "amount not valid"}),201
        
       #check if payment date is future date
        # compare dates, not date-at-midnight against `now()`: today was always rejected
        # with "لا يمكن إضافة تاريخ سداد سابق" (review 3.7).
        if datetime.datetime.strptime(data['payment_date'], '%Y-%m-%d').date() < datetime.datetime.now().date():
            return jsonify( {"message": "لا يمكن إضافة تاريخ سداد سابق","status":False , "error": "payment date not valid"}),201
        
        payment_date = PaymentDates(**data)
        db.session.add(payment_date)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء موعد سداد", e)
      
    
    return jsonify( payment_date.as_dict()),200


# create clint contact
@app.route('/clint_contact' , methods =['POST'])
@jwt_required()
def createClintContact():
    try:
        #time.sleep(2)
        data = request.get_json()
        clint_contact = ClintContacts(**data)
        db.session.add(clint_contact)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء جهة اتصال", e)
      
    
    return jsonify( clint_contact.as_dict()),200

#delete clint contact by id
@app.route('/clint_contact/<id>' , methods =['DELETE'])
@jwt_required()
def deleteClintContact(id):
    try:
        clint_contact = ClintContacts.query.get(id)
        if clint_contact is None:
            return jsonify( {"message": "جهة الاتصال غير موجودة","status":False , "error": "clint contact not found"}),201
        
        # Get the identityNumber and contact from the current contact
        identity_number = clint_contact.identityNumber
        contact_value = clint_contact.contact
        
        # Set is_deleted = True for all contacts with same identityNumber and contact
        ClintContacts.query.filter_by(
            identityNumber=identity_number,
            contact=contact_value
        ).update({"is_deleted": True})
        
        db.session.commit()
        return jsonify( {"message": "تم حذف جهة الاتصال بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف جهة الاتصال", e)   

#update clint contact is_default by id
@app.route('/clint_contact/default/<id>' , methods =['PUT'])
@jwt_required()
def updateClintContactDefault(id):
    try:
        clint_contact = ClintContacts.query.get(id)
        if clint_contact is None:
            return jsonify( {"message": "جهة الاتصال غير موجودة","status":False , "error": "clint contact not found"}),201
        
        # Get the identityNumber from the current contact
        identity_number = clint_contact.identityNumber
        # Get the contact from the current contact
        contact_value = clint_contact.contact
        
        # Set is_default = False for all contacts with same identityNumber
        ClintContacts.query.filter_by(
            identityNumber=identity_number
        ).update({"is_default": False})

        # Set is_default = True for all contacts with same identityNumber and contact
        ClintContacts.query.filter_by(
            identityNumber=identity_number,
            contact=contact_value
        ).update({"is_default": True})

        db.session.commit()
        return jsonify( {"message": "١تم تعديل جهة الاتصال بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل جهة الاتصال", e)
    
#update clint contact  is_default false by id
@app.route('/clint_contact/unset_default/<id>' , methods =['PUT'])
@jwt_required()
def unsetClintContactDefault(id):
    try:
        clint_contact = ClintContacts.query.get(id)
        if clint_contact is None:
            return jsonify( {"message": "جهة الاتصال غير موجودة","status":False , "error": "clint contact not found"}),201

        # Get the identityNumber from the current contact
        identity_number = clint_contact.identityNumber
        # Get the contact from the current contact
        contact_value = clint_contact.contact

        # Set is_archive = False for all contacts with same identityNumber and contact
        ClintContacts.query.filter_by(
            identityNumber=identity_number,
            contact=contact_value
        ).update({"is_default": False})

        db.session.commit()
        return jsonify( {"message": "تم تعديل جهة الاتصال بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل جهة الاتصال", e)
    
#---------------------------- clint documents ----------------------------
_ALLOWED_CLINT_DOC_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
_ALLOWED_CLINT_DOC_MIMETYPES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
}
_MAX_CLINT_DOC_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

# upload a document for a clint (pdf or image)
@app.route('/clint/<id>/documents', methods=['POST'])
@jwt_required()
def uploadClintDocument(id):
    try:
        current_user_id = get_jwt_identity()

        clint = Clints.query.get(id)
        if clint is None:
            return jsonify({"message": "العميل غير موجود", "status": False, "error": "clint not found"}), 201

        if 'file' not in request.files:
            return jsonify({"message": "لم يتم إرفاق ملف", "status": False, "error": "no file part"}), 201

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({"message": "لم يتم اختيار ملف", "status": False, "error": "no selected file"}), 201

        display_name = request.form.get('name')
        if display_name is not None:
            display_name = display_name.strip() or None

        _, ext = os.path.splitext(file.filename)
        ext = ext.lower()
        if ext not in _ALLOWED_CLINT_DOC_EXTENSIONS:
            return jsonify({"message": "نوع الملف غير مسموح به، يسمح فقط بـ PDF أو صور (jpg, jpeg, png)", "status": False, "error": "file type not allowed"}), 201

        if file.mimetype not in _ALLOWED_CLINT_DOC_MIMETYPES:
            return jsonify({"message": "نوع الملف غير مسموح به", "status": False, "error": "mimetype not allowed"}), 201

        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > _MAX_CLINT_DOC_SIZE_BYTES:
            return jsonify({"message": "حجم الملف أكبر من الحد المسموح به (10 ميجابايت)", "status": False, "error": "file too large"}), 201

        # New uploads go to <UPLOAD_DIR>/documents, outside Flask's `static/` folder:
        # `static/clint_documents/<uuid>.pdf` was served without a JWT to anyone who
        # learned the name (review S12). The DB row keeps just the basename; the download
        # route resolves it in the new directory and falls back to the legacy one.
        stored_name = str(uuid.uuid4()) + ext
        file_path = document_path(stored_name)
        file.save(file_path)

        doc = ClintDocuments(
            clint_id=id,
            file_name=stored_name,
            original_name=file.filename,
            display_name=display_name,
            file_type=ext.lstrip('.'),
            uploaded_by=current_user_id,
        )
        db.session.add(doc)
        db.session.commit()

        return jsonify({"message": "تم رفع المستند بنجاح", "status": True, "document": doc.as_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء رفع المستند", e)


# list documents for a clint
@app.route('/clint/<id>/documents', methods=['GET'])
@jwt_required()
def getClintDocuments(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify({"message": "العميل غير موجود", "status": False, "error": "clint not found"}), 201

        docs = ClintDocuments.query.filter_by(clint_id=id, is_deleted=False).order_by(ClintDocuments.create_at.desc()).all()
        docs_list = [doc.as_dict() for doc in docs]

        return jsonify({"documents": docs_list, "countRow": len(docs_list)}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المستندات", e)


# download / view a single document
@app.route('/clint/document/<doc_id>/download', methods=['GET'])
@jwt_required()
def downloadClintDocument(doc_id):
    try:
        doc = ClintDocuments.query.filter_by(id=doc_id, is_deleted=False).first()
        if doc is None:
            return jsonify({"message": "المستند غير موجود", "status": False, "error": "document not found"}), 201

        file_path = _clint_document_path(doc.file_name)
        if not file_path or not os.path.exists(file_path):
            return jsonify({"message": "الملف غير موجود على الخادم", "status": False, "error": "file missing on disk"}), 201

        return send_file(
            file_path,
            as_attachment=False,
            download_name=doc.original_name,
        )
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تحميل المستند", e)


# delete a document (soft delete)
@app.route('/clint/document/<doc_id>', methods=['DELETE'])
@jwt_required()
def deleteClintDocument(doc_id):
    try:
        doc = ClintDocuments.query.filter_by(id=doc_id, is_deleted=False).first()
        if doc is None:
            return jsonify({"message": "المستند غير موجود", "status": False, "error": "document not found"}), 201

        doc.is_deleted = True
        db.session.commit()

        return jsonify({"message": "تم حذف المستند بنجاح", "status": True}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف المستند", e)  
#update clint to archive true
@app.route('/clint/archive/<id>' , methods =['PUT'])
@jwt_required()
@required_roles('clints_move_archive')  # clients list row archive button
def updateClintToArchive(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify( {"message": "العميل غير موجود","status":False , "error": "role not found"}),201
        
        # The UI sends the case id as `id`; it used to become the history row's id, so
        # archiving the same case again after a restore failed on a duplicate key after
        # `is_archive` was already committed. The history row now gets its own id, and
        # the row and both flags are written in one transaction.
        data = dict(request.get_json() or {})
        data.pop('id', None)
        archive_reason = ClintArchive(**data)
        db.session.add(archive_reason)
        db.session.flush()
        clint.is_archive = True
        clint.clintArchive_id = archive_reason.id
        db.session.commit()

        
        return jsonify( clint.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العميل", e)


 #update clint to archive false


 #update clint to archive false


@app.route('/clint/unarchive/<id>' , methods =['PUT'])   
@jwt_required()
@required_any('clints_archive_move', 'clints_archive')  # archive screen row restore (route key clints_archive)
def updateClintToUnArchive(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify( {"message": "العميل غير موجود","status":False , "error": "role not found"}),201
        
        clint.is_archive = False
        clint.clintArchive_id = None
        db.session.commit()
        clint = db.session.get(Clints, id) 
        return jsonify( clint.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل العميل", e)

    


# get supervisor by id
@app.route('/supervisor/<id>', methods=['GET'])
@jwt_required()
@required_roles('supervisors')
def getsupervisor(id):
    try:
        supervisor = Supervisor.query.get(id)
        if supervisor is None:
               return jsonify( {"message": "المشرف غير موجود","status":False , "error": "role not found"}),201
        #get supervisor details
        tsupervisor = supervisor.as_dict()
        user = User.query.get(supervisor.user_id)
        tuser = user.as_dict()
        tuser['name'] = tuser['frist_name'] + " " + tuser['last_name']
        tsupervisor['user'] = tuser
        #get supervisor contracts
        supervisor_contracts = SupervisorContract.query.filter_by(supervisor_id=supervisor.id)
        contracts_list = []
        for supervisor_contract in supervisor_contracts:
            contract = Contract.query.get(supervisor_contract.contract_id)
            contracts_list.append(contract.as_dict())
        tsupervisor['contracts'] = contracts_list
        #get supervisor users
        supervisor_users = SupervisorUser.query.filter_by(supervisor_id=supervisor.id)
        users_list = []
        for supervisor_user in supervisor_users:
            user = User.query.get(supervisor_user.user_id)
            tuser = user.as_dict()
            tuser['name'] = tuser['frist_name'] + " " + tuser['last_name']
            users_list.append(tuser)
        tsupervisor['users'] = users_list
        return  jsonify(tsupervisor),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المشرف", e)   
    
# insert supervisor
@app.route('/supervisors', methods=['POST'])
@jwt_required()
@required_roles('supervisors_add')
def create_supervisor():
   
   
    try:
       
        data = request.get_json()
        #check if user ready supervisor
        is_supervisor = Supervisor.query.filter_by(user_id=data['user_id']).first()
        if is_supervisor is not None:
            return jsonify( {"message": "المستخدم مشرف بالفعل","status":False , "error": "user is supervisor"}),201
        
        supervisor = Supervisor(**data)
        db.session.add(supervisor)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء المشرف", e)
      
    
    return jsonify( supervisor.as_dict()),200

# update supervisor
@app.route('/supervisor/<id>', methods=['PUT'])
@jwt_required()
@required_roles('supervisors_edit')
def update_supervisor(id):
    try:
        supervisor = Supervisor.query.get(id)
        if supervisor is None:
            return jsonify( {"message": "المشرف غير موجود","status":False , "error": "role not found"}),201
        
        data = request.get_json()
        # Replace the link rows. The delete loop only removed links that are *not* in the
        # payload, but the insert loop then added a row for **every** id in the payload -
        # so saving the same supervisor twice doubled the join rows, double-counting in
        # /supervisors and widening the or_() scope filter (review 3.22). Only insert the
        # links that do not exist yet, and commit once instead of per row.
        wanted_contracts = {str(c) for c in (data['contracts_ids'] or [])}
        wanted_users = {str(u) for u in (data['users_ids'] or [])}

        existing_contracts = set()
        for supervisor_contract in SupervisorContract.query.filter_by(supervisor_id=supervisor.id).all():
            if str(supervisor_contract.contract_id) not in wanted_contracts:
                db.session.delete(supervisor_contract)
            else:
                existing_contracts.add(str(supervisor_contract.contract_id))

        existing_users = set()
        for supervisor_user in SupervisorUser.query.filter_by(supervisor_id=supervisor.id).all():
            if str(supervisor_user.user_id) not in wanted_users:
                db.session.delete(supervisor_user)
            else:
                existing_users.add(str(supervisor_user.user_id))

        #insert new contracts and users
        for contract_id in data['contracts_ids']:
            if str(contract_id) in existing_contracts:
                continue
            db.session.add(SupervisorContract(supervisor_id=supervisor.id,contract_id=contract_id))
        for user_id in data['users_ids']:
            if str(user_id) in existing_users:
                continue
            db.session.add(SupervisorUser(supervisor_id=supervisor.id,user_id=user_id))

        db.session.commit()

        return jsonify( supervisor.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل المشرف", e)


# delete supervisor
@app.route('/supervisor/<id>', methods=['DELETE'])
@jwt_required()
@required_roles('supervisors_delete')
def delete_supervisor(id):
    try:
        supervisor = Supervisor.query.get(id)
        if supervisor is None:
            return jsonify( {"message": "المشرف غير موجود","status":False , "error": "role not found"}),201
        
        db.session.delete(supervisor)
        db.session.commit()
        return jsonify( {"message": "تم حذف المشرف بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف المشرف", e)
    


#create supervisor-contract 
@app.route('/supervisor_contract' , methods =['POST'])
@jwt_required()
def createSupervisorContract():
    try:
        data = request.get_json()
        #get supervisor id
        supervisor_id = data['supervisor_id']
        #get contract id array 
        contract_ids = data['contract_id']
        #get supervisor id with each contract id
       
        for contract_id in contract_ids:
            supervisor_contract = SupervisorContract(supervisor_id=supervisor_id,contract_id=contract_id)
            db.session.add(supervisor_contract)
            db.session.commit()
       
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء عقد المشرف", e)
      
    
    return jsonify( supervisor_contract.as_dict()),200

#create supervisor-users
@app.route('/supervisor_user' , methods =['POST'])
@jwt_required()
def createSupervisorUsers():
    try:
        data = request.get_json()
        #get supervisor id
        supervisor_id = data['supervisor_id']
        #get user id array
        user_ids = data['users']
        #get supervisor id with each user id
       
        for user_id in user_ids:
            supervisor_users = SupervisorUser(supervisor_id=supervisor_id,user_id=user_id)
            db.session.add(supervisor_users)
            db.session.commit()

 
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء إنشاء مستخدم المشرف", e)
      
    
    return jsonify( supervisor_users.as_dict()),200


#--------------------------------------------------------------------------------
#get portfolios

@app.route('/portfolios' , methods =['GET'])
@jwt_required()
def getPortfolios():
    try:

        # Get parameters
        data = request.args
        # Get page number
        page = int(data['page']) if 'page' in data else 1
        # Get limit number
        pageSize = int(data['pageSize']) if 'pageSize' in data else 1000
        # Get ArchivingStatus
        # repeated query key -> getlist. `data.get(...)` returned a *str*, so
        # `ArchivingStatus[0]` took the first character ("true" -> "t") and an empty
        # value raised IndexError -> 201 (review 3.8).
        ArchivingStatus = [v for v in data.getlist('customeFilterArchivingStatusesValue[]') if v not in (None, '')]
        def _parse_filter_date(raw):
            if not raw:
                return None
            try:
                return datetime.datetime.strptime(raw, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return None

        # تاريخ المحفظة: من / الى. القيمة المفردة القديمة تعامل كـ "من" و "الى" لنفس اليوم
        customeFilterPortfolioCreateAtValue = data.get('customeFilterPortfolioCreateAtValue')
        _portfolio_create_at_from = _parse_filter_date(
            data.get('customeFilterPortfolioCreateAtFromValue')
        ) or _parse_filter_date(customeFilterPortfolioCreateAtValue)
        _portfolio_create_at_to = _parse_filter_date(
            data.get('customeFilterPortfolioCreateAtToValue')
        ) or _parse_filter_date(customeFilterPortfolioCreateAtValue)

        # لو أدخل المستخدم المدى بالعكس نصححه بدل ما ترجع نتيجة فاضية
        if (
            _portfolio_create_at_from is not None
            and _portfolio_create_at_to is not None
            and _portfolio_create_at_from > _portfolio_create_at_to
        ):
            _portfolio_create_at_from, _portfolio_create_at_to = (
                _portfolio_create_at_to,
                _portfolio_create_at_from,
            )
        
        client_subq = (
            db.session.query(
                Clints.portfolios_id.label("portfolio_id"),
                func.count(Clints.id).label("client_count"),
                func.sum(Clints.amount).label("total_client_amount")
            )
            .group_by(Clints.portfolios_id)
        )

        payment_subq = (
            db.session.query(
                Clints.portfolios_id.label("portfolio_id"),
                func.sum(Payments.amount).label("total_payment_amount")
            )
            .join(Payments, Payments.clint_id == Clints.id)
            .group_by(Clints.portfolios_id)
        ) 

        # Apply filter for ArchivingStatus only if it is not empty or None
        if ArchivingStatus and ArchivingStatus[0] != '0':
            payment_subq = payment_subq.filter(Clints.is_archive == ArchivingStatus[0])  # Apply filter directly in the subquery
            client_subq = client_subq.filter(Clints.is_archive == ArchivingStatus[0])  # Apply filter directly in the subquery
        
        payment_subq = payment_subq.subquery()
        client_subq = client_subq.subquery()

        
            # Main query joining Portfolios with Contracts and the aggregated subqueries.
        portfolios_query = (
            db.session.query(
                Portfolios.id.label("portfolio_id"),
                Portfolios.name.label("portfolio_name"),
                Portfolios.create_at.label("create_at"),
                Contract.id.label("contract_id"),
                Contract.name.label("contract_name"),
                func.coalesce(client_subq.c.client_count, 0).label("count_clints"),
                func.coalesce(client_subq.c.total_client_amount, 0).label("count_clints_amount"),
                func.coalesce(payment_subq.c.total_payment_amount, 0).label("payments")
            )
            .join(Contract, Contract.id == Portfolios.contract_id)  # Every portfolio has a related contract.
            .outerjoin(client_subq, client_subq.c.portfolio_id == Portfolios.id)
            .outerjoin(payment_subq, payment_subq.c.portfolio_id == Portfolios.id)
             .order_by(Portfolios.create_at.desc())
        )

        if _portfolio_create_at_from is not None:
            portfolios_query = portfolios_query.filter(func.date(Portfolios.create_at) >= _portfolio_create_at_from)
        if _portfolio_create_at_to is not None:
            portfolios_query = portfolios_query.filter(func.date(Portfolios.create_at) <= _portfolio_create_at_to)
      

    
        # Convert the results into a list of dictionaries
        portfolios_list = []


        


     
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)
    
   

    count = portfolios_query.count()
    # Paginate the results
    pagination = portfolios_query.paginate(page=page, per_page=pageSize, error_out=False)
    portfolios = pagination.items

    portfolios_list = [{
        "contracts": {
            "id": portfolio.contract_id,
            "name": portfolio.contract_name,
        },
        "id": portfolio.portfolio_id,
        "name": portfolio.portfolio_name,
        "countClints": portfolio.count_clints,
        "countClintsAmount": portfolio.count_clints_amount,
        "payments": portfolio.payments,
        "create_at": portfolio.create_at # Fixed the assignment operator and added a missing comma
    } for portfolio in portfolios]


    return jsonify( {"portfolios": portfolios_list ,"countRow" :count }),200


#export
@app.route('/portfolios/export' , methods =['GET'])
@jwt_required()
def exportPortfolios():
    try:

        # Get parameters
        data = request.args
    
        # Get ArchivingStatus
        # repeated query key -> getlist. `data.get(...)` returned a *str*, so
        # `ArchivingStatus[0]` took the first character ("true" -> "t") and an empty
        # value raised IndexError -> 201 (review 3.8).
        ArchivingStatus = [v for v in data.getlist('customeFilterArchivingStatusesValue[]') if v not in (None, '')]
        
        client_subq = (
            db.session.query(
                Clints.portfolios_id.label("portfolio_id"),
                func.count(Clints.id).label("client_count"),
                func.sum(Clints.amount).label("total_client_amount")
            )
            .group_by(Clints.portfolios_id)
        )

        payment_subq = (
            db.session.query(
                Clints.portfolios_id.label("portfolio_id"),
                func.sum(Payments.amount).label("total_payment_amount")
            )
            .join(Payments, Payments.clint_id == Clints.id)
            .group_by(Clints.portfolios_id)
        ) 

        # Apply filter for ArchivingStatus only if it is not empty or None
        if ArchivingStatus and ArchivingStatus[0] != '0':
            payment_subq = payment_subq.filter(Clints.is_archive == ArchivingStatus[0])  # Apply filter directly in the subquery
            client_subq = client_subq.filter(Clints.is_archive == ArchivingStatus[0])  # Apply filter directly in the subquery
        
        payment_subq = payment_subq.subquery()
        client_subq = client_subq.subquery()

        
            # Main query joining Portfolios with Contracts and the aggregated subqueries.
        portfolios_query = (
            db.session.query(
                Portfolios.id.label("portfolio_id"),
                Portfolios.name.label("portfolio_name"),
                Portfolios.create_at.label("create_at"),
                Contract.id.label("contract_id"),
                Contract.name.label("contract_name"),
                func.coalesce(client_subq.c.client_count, 0).label("count_clints"),
                func.coalesce(client_subq.c.total_client_amount, 0).label("count_clints_amount"),
                func.coalesce(payment_subq.c.total_payment_amount, 0).label("payments")
            )
            .join(Contract, Contract.id == Portfolios.contract_id)  # Every portfolio has a related contract.
            .outerjoin(client_subq, client_subq.c.portfolio_id == Portfolios.id)
            .outerjoin(payment_subq, payment_subq.c.portfolio_id == Portfolios.id)
             .order_by(Portfolios.create_at.desc())
        )
      

    
        # Convert the results into a list of dictionaries
        portfolios_list = []


        


     
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحافظ", e)
    
   

    count = portfolios_query.count()
    portfolios = portfolios_query.all()

    portfolios_list = [{
        "contracts": {
            "id": portfolio.contract_id,
            "name": portfolio.contract_name,
        },
        "id": portfolio.portfolio_id,
        "name": portfolio.portfolio_name,
        "countClints": portfolio.count_clints,
        "countClintsAmount": portfolio.count_clints_amount,
        "payments": portfolio.payments,
        "create_at": portfolio.create_at # Fixed the assignment operator and added a missing comma
    } for portfolio in portfolios]


    return jsonify( {"portfolios": portfolios_list ,"countRow" :count }),200


#get portfolio by id
@app.route('/portfolio/<id>' , methods =['GET'])
@jwt_required()
def getPortfolioById(id):
    try:
        portfolio =  Portfolios.query.get(id)
        if portfolio is None:
            return jsonify( {"message": "المحفظة غير موجودة","status":False , "error": "portfolio not found"}),201
        tportfolio = portfolio.as_dict()
        contracts = Contract.query.get(portfolio.contract_id)   
        tportfolio['contracts'] = contracts.as_dict()
        # count clints in portfolio
        clints = Clints.query.filter_by(portfolios_id=portfolio.id).count()
        tportfolio['countClints'] = clints
        # count clints amount in portfolio
        clints_amount = Clints.query.filter_by(portfolios_id=portfolio.id).with_entities(func.sum(Clints.amount)).scalar()
        tportfolio['countClintsAmount'] = clints_amount
        # get all clints payments in portfolio then sum it 3 tables payments , clint, portfolio
        payments = db.session.query(func.sum(Payments.amount)).join(Clints, Payments.clint_id == Clints.id).filter(Clints.portfolios_id == portfolio.id).scalar()
        tportfolio['payments'] = payments
        return jsonify( tportfolio),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المحفظة", e)

#update portfolio by id
@app.route('/portfolio/<id>' , methods =['PUT'])
@jwt_required()
def updatePortfolio(id):
    try:
        portfolio = Portfolios.query.get(id)
        if portfolio is None:
            return jsonify( {"message": "المحفظة غير موجودة","status":False , "error": "portfolio not found"}),201
        
        data = request.get_json()
        portfolio.name = data['name'] if 'name' in data else portfolio.name
        db.session.commit()
        return jsonify( portfolio.as_dict()),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء تعديل المحفظة", e)
    
#delete portfolio by id
@app.route('/portfolios/<id>' , methods =['DELETE'])
@jwt_required()
@required_roles('portfolios_delete')
def deletePortfolio(id):
    try:
        portfolio = Portfolios.query.get(id)
        if portfolio is None:
            return jsonify( {"message": "المحفظة غير موجودة","status":False , "error": "portfolio not found"}),404


        # Set clintArchive_id to None before deleting the records
        db.session.query(Clints).filter(Clints.portfolios_id == portfolio.id).update(
        {Clints.clintArchive_id: None}, synchronize_session=False)

        # Delete ClintArchive records associated with those clients
        db.session.query(ClintArchive).filter(ClintArchive.clint_id.in_(
        db.session.query(Clints.id).filter(Clints.portfolios_id == portfolio.id)
           )).delete(synchronize_session=False)


        # Bulk delete clients associated with the portfolio
        db.session.query(Clints).filter(Clints.portfolios_id == portfolio.id).delete(synchronize_session=False)

        # Delete the portfolio itself
        db.session.delete(portfolio)

        # Commit once, ensuring atomicity
        db.session.commit()

        

        return jsonify( {"message": "تم حذف المحفظة بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف المحفظة", e)

  
#--------------------------------------------------------------------------------

#delete clint by id
@app.route('/clint/<id>' , methods =['DELETE'])
@jwt_required()
@required_roles('clints_delete')
def deleteClint(id):
    try:
        clint = Clints.query.get(id)
        if clint is None:
            return jsonify( {"message": "العميل غير موجود","status":False , "error": "clint not found"}),201
        #delete clintArchive 
        clintArchive = ClintArchive.query.filter_by(clint_id=clint.id).all()
        for clintArchive_ in clintArchive:
            db.session.delete(clintArchive_)
            db.session.commit()
        #delete clint
       
        db.session.delete(clint)
        db.session.commit()
        
        return jsonify( {"message": "تم حذف العميل بنجاح","status":True}),200
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف العميل", e)

    


#get 


    

#get SepcifyUsers by clint id
@app.route('/sepcifyusers/clint/<id>' , methods =['GET'])
@jwt_required()
def getSepcifyUsers(id):
    #get from SepcifyUsers table by clint id
    try:
        sepcifyusers_list = []
        sepcifyusers =  SepcifyUsers.query.filter_by(clint_id=id)
       
        for sepcifyuser in sepcifyusers:
            tsepcifyuser = sepcifyuser.as_dict()
            user = User.query.get(sepcifyuser.user_id)
            tuser = user.as_dict()
            tuser['name'] = tuser['frist_name'] + " " + tuser['last_name']
            tsepcifyuser['user'] = tuser
            creatby = User.query.get(sepcifyuser.create_by)
            tcreatby = creatby.as_dict()
            tcreatby['name'] = tcreatby['frist_name'] + " " + tcreatby['last_name']
            tsepcifyuser['create_by'] = tcreatby
           
            sepcifyusers_list.append(tsepcifyuser)

        #sprt sepcifyusers_list by create_at  (once, after the loop - review P8)
        sepcifyusers_list.sort(key=lambda x: x['create_at'], reverse=True)
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب المستخدمين المحددين", e)
    return jsonify( {"sepcifyusers": sepcifyusers_list}),200


#get notifications for user
@app.route('/notifications', methods=['GET'])
@jwt_required()
def get_notifications():
    try:
        current_user = get_jwt_identity()
        notifications = Notification.query.filter_by(receiver_user_id=current_user).order_by(Notification.created_at.desc()).all()
        # NB: a bare JSON array, not an object - the frontend reads it directly.
        return jsonify([n.as_dict() for n in notifications])
    except Exception as e:
        db.session.rollback()
        return error_response("حدث خطأ أثناء جلب الإشعارات", e)


#set notification as read
@app.route('/notifications/read/<id>', methods=['PUT'])
@jwt_required()
def set_notification_read(id):
    try:
        notification = Notification.query.get(id)
        if notification is None:
            return jsonify({"message": "Notification not found", "status": False}), 404

        notification.is_read = True
        db.session.commit()
        return jsonify({"message": "Notification marked as read", "status": True}), 200
    except Exception as e:
        db.session.rollback()
        return error_response("Error marking notification as read", e, 500) 


       


 


 


def getClintBySupervisor(clint_id,supervisor_id):
    try:
        #get clint from dictlist_redis that supervisor_contract.contract_id = clint.portfolios.contract_id
        supervisor_contracts = SupervisorContract.query.filter_by(supervisor_id=supervisor_id)
        supervisor_users = SupervisorUser.query.filter_by(supervisor_id=supervisor_id)
        #get clint from dictlist_redis that supervisor_contract.contract_id = clint.portfolios.contract_id
        clint = Clints.query.filter_by(id=clint_id).first()
        portfolios = Portfolios.query.get(clint.portfolios_id)
        #set current user id in listContractsAndUsersIds
        current_user = get_jwt_identity()

        if clint is None:
            return None
        
        if str(clint.user_id) == str(current_user):
            return clint
        #check if clint not in clints_list
        if clint.user_id in [supervisor_user.user_id for supervisor_user in supervisor_users]:
            return clint
        for supervisor_contract in supervisor_contracts:
            if portfolios.contract_id == supervisor_contract.contract_id:
                return clint
        return None
    except Exception as e:
        app.logger.exception("getClintBySupervisor failed: %s", e)
        return None
        
 #-------------------- filter and sort clints by server side ---------------------#
def sortClints(clients, field, sort):
    if field is not None and sort is not None:
        clients = sorted(clients, key=getKey(field), reverse=(sort == "desc"))
    return clients
def getKey(key):
    import datetime
    keys = key.split(".")
    
    
    def nested_get(client):
        item = client
        for k in keys:
            if k.endswith("_"):
                k = k[:-1]

            item = item.get(k, None)
            #9966
            if item is None and k != "create_at":
                return ""  # return an empty string when key doesn't exist
            if item is None and k == "create_at":
                return datetime.datetime(1,1,1)
            
            # if string convrt to lower
            if isinstance(item, str):
                item = item.lower()
        if isinstance(item, datetime.datetime):
            item = item.replace(tzinfo=None)  # remove timezone information
        return item  # convert all items to strings
    return nested_get
# write function by mui filter model filter by server side 

def filterClints(clients,operator,field,value,values,filterFieldType):
 

    # filter with one value and field type number
    if operator and field and value is not None and filterFieldType == "number":
        if operator == "=":
            return list(filter(lambda client: get_nested_value(client, field) ==  float(value), clients))
        elif operator == "!=":
            return list(filter(lambda client: get_nested_value(client, field) != float(value), clients))
        elif operator == ">":
            return list(filter(lambda client: get_nested_value(client, field)  > float(value), clients))
        elif operator == ">=":
            return list(filter(lambda client:get_nested_value(client, field) >= float(value), clients))
        elif operator == "<":
            return list(filter(lambda client: get_nested_value(client, field)  < float(value), clients))
        elif operator == "<=":
            return list(filter(lambda client: get_nested_value(client, field) <= float(value), clients))
    # filter with one value and field type string
    if operator and field and value is not None and filterFieldType == "string":
        if operator == "contains":
            return list(filter(lambda client: str(value) in str(get_nested_value(client, field)), clients))
        elif operator == "equals":
            return list(filter(lambda client: str(value) == str(get_nested_value(client, field)), clients))
        elif operator == "startsWith":
            return list(filter(lambda client: str(get_nested_value(client, field)).startswith(str(value)), clients))
        elif operator == "endsWith":
            return list(filter(lambda client: str(get_nested_value(client, field)).endswith(str(value)), clients))
    
    #filter with one value and field type date
    if operator and field and value is not None and filterFieldType == "date":
        import datetime
        if operator == "is":
            return list(filter(lambda client: get_nested_value(client, field) is not None and (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) == datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        elif operator == "not":
            return list(filter(lambda client: get_nested_value(client, field) is None or (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) != datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        elif operator == "after":
            return list(filter(lambda client: get_nested_value(client, field) is not None and (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) > datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        elif operator == "onOrAfter":
            return list(filter(lambda client: get_nested_value(client, field) is not None and (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) >= datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        elif operator == "before":
            return list(filter(lambda client: get_nested_value(client, field) is not None and (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) < datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        elif operator == "onOrBefore":
            return list(filter(lambda client: get_nested_value(client, field) is not None and (get_nested_value(client, field).date() if isinstance(get_nested_value(client, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(client, field), '%Y-%m-%d').date()) <= datetime.datetime.strptime(value, '%Y-%m-%d').date(), clients))
        
    # filter without value   
    if operator and field  is not None:
        if operator == "isEmpty":
                return list(filter(lambda client: get_nested_value(client, field) is None or  get_nested_value(client, field) == "", clients))
        elif operator == "isNotEmpty":
                return list(filter(lambda client: get_nested_value(client, field) is not None and  get_nested_value(client, field) != "", clients))
        
    # filter with one value and filed single select
    if operator and field and value is not None and filterFieldType == "singleSelect":
        if operator == "is":
            return list(filter(lambda client: str(get_nested_value(client, field)) == str(value), clients))
        elif operator == "not":
            return list(filter(lambda client: str(get_nested_value(client, field)) != str(value), clients))

    # filter wigh more than one value and filed single select
    if operator and field and values  is not None and filterFieldType == "singleSelect":
        if operator == "isAnyOf":
            return list(filter(lambda client: str(get_nested_value(client, field)) in values, clients))


    # filter with more than one values   
    if operator and field and values  is not None and filterFieldType == "number":
        if operator == "isAnyOf":
            values = [float(value) for value in values]
            return list(filter(lambda client: get_nested_value(client, field) in values, clients))
    
    # filter with more than one values
    if operator and field and values  is not None and filterFieldType == "string":
        if operator == "isAnyOf":
            return list(filter(lambda client: get_nested_value(client, field) in values, clients))
        

    return clients

def customFilter(clints,customeFilterContractValue,customeFilterUserValue,customeFilterCommentStatusValue ,customeFilterPortfoliosValue,customeFilterBranchesValue,customeFilterAttributeValue,customeFilterInquiryClintByExcelValue,customeFilterClintArchiveByValue,customeFilterClintArchiveReasonValue ):
    if customeFilterContractValue is not None and len(customeFilterContractValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "portfolios.contract_id")) in customeFilterContractValue, clints))

    if customeFilterUserValue is not None and len(customeFilterUserValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "user_id")) in customeFilterUserValue, clints))
    
    if customeFilterCommentStatusValue is not None and len(customeFilterCommentStatusValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "comment.comment_status_id")) in customeFilterCommentStatusValue, clints))

    if customeFilterPortfoliosValue is not None and len(customeFilterPortfoliosValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "portfolios_id")) in customeFilterPortfoliosValue, clints))

    if customeFilterBranchesValue is not None and len(customeFilterBranchesValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "user.branch_id")) in customeFilterBranchesValue, clints))

    if customeFilterAttributeValue is not None and len(customeFilterAttributeValue) > 0:
        clints = list(filter(lambda client: str(customeFilterAttributeValue) in map(str, client.get('attribute', {}).values()), clints))

    if customeFilterInquiryClintByExcelValue is not None and len(customeFilterInquiryClintByExcelValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "accountNumber")) in customeFilterInquiryClintByExcelValue, clints))

    if customeFilterClintArchiveByValue is not None and len(customeFilterClintArchiveByValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "clintArchive.user_id")) in customeFilterClintArchiveByValue, clints))

    if customeFilterClintArchiveReasonValue is not None and len(customeFilterClintArchiveReasonValue) > 0:
        clints = list(filter(lambda client: str(get_nested_value(client, "clintArchive.archive_reason_id")) in customeFilterClintArchiveReasonValue, clints))
    

    

    
    
    return clints

def get_nested_value(client, field):
    keys = field.split('.')
    value = client
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value
#--------------------------- 2024/02/04 --------------------------


def pagination_filter_clients(request,clints_list):
     #get parma 
    data = request.args
    #get page number
    page = int(data['page']) if 'page' in data else 1
    #get limit number
    pageSize = int(data['pageSize']) if 'pageSize' in data else 200
    #get start index
    startIndex = (page - 1) * pageSize
    #get end index
    endIndex = page * pageSize
    #get clints by page number
   

    #sort parma filed , sortType
    field = data['field'] if 'field' in data else None
    sort = data['sort'] if 'sort' in data else None


    #get parma filter
    filterField = data['filterField'] if 'filterField' in data else None
    filterFieldType = data['filterFieldType'] if 'filterFieldType' in data else None
    filterValue = data['filterValue'] if 'filterValue' in data else None
    #add any filtervaluse[] to list
    query_string = request.query_string.decode()
    parsed_query = parse_qs(query_string)
    filter_values = parsed_query.get('filterValue[]', [])
    customeFilterContractValue = parsed_query.get('customeFilterContractValue[]', [])
    customeFilterUserValue = parsed_query.get('customeFilterUserValue[]', [])
    customeFilterCommentStatusValue = parsed_query.get('customeFilterCommentStatusValue[]', [])
    customeFilterPortfoliosValue = parsed_query.get('customeFilterPortfoliosValue[]', [])
    customeFilterBranchesValue = parsed_query.get('customeFilterBranchesValue[]', [])
    customeFilterAttributeValue = data['customeFilterAttributeValue'] if 'customeFilterAttributeValue' in data else None
    customeFilterInquiryClintByExcelValue =  parse_qs(query_string).get('customeFilterInquiryClintByExcelValue[]', [])
    customFilterClintArchiveByValue =  parsed_query.get('customeFilterClintArchiveByValue[]', [])
    customeFilterClintArchiveReasonValue =  parse_qs(query_string).get('customeFilterClintArchiveReasonValue[]', [])

    
    filterOperator = data['filterOperator'] if 'filterOperator' in data else None


  

    c = clints_list
    clintsSorted =  sortClints(c,field,sort)
    clintsFilterd = filterClints(clintsSorted,filterOperator,filterField,filterValue,filter_values,filterFieldType)
    clintsCustomFilterd = customFilter(clintsFilterd,
                                       customeFilterContractValue,
                                       customeFilterUserValue,
                                       customeFilterCommentStatusValue,
                                       customeFilterPortfoliosValue,
                                       customeFilterBranchesValue,
                                       customeFilterAttributeValue,
                                       customeFilterInquiryClintByExcelValue,
                                       customFilterClintArchiveByValue,
                                       customeFilterClintArchiveReasonValue)
    clints_list = clintsCustomFilterd[startIndex:endIndex]
    return clints_list,len(clintsCustomFilterd)
   

def my_pagination(request,rows):
    #get parma 
    data = request.args
    #get page number
    page = int(data['page']) if 'page' in data else 1
    #get limit number
    pageSize = int(data['pageSize']) if 'pageSize' in data else 200
    #get start index
    startIndex = (page - 1) * pageSize
    #get end index
    endIndex = page * pageSize
    #get clints by page number
    return rows[startIndex:endIndex]

def my_sort(request,rows):
    data = request.args
    field = data['field'] if 'field' in data else None
    sort = data['sort'] if 'sort' in data else None

    if field is not None and sort is not None:
        rows = sorted(rows, key=getKey(field), reverse=(sort == "desc"))
    return rows

def my_filter(request,rows):
     data = request.args
     filterOperator = data['filterOperator'] if 'filterOperator' in data else None
     filterField = data['filterField'] if 'filterField' in data else None
     filterFieldType = data['filterFieldType'] if 'filterFieldType' in data else None
     filterValue = data['filterValue'] if 'filterValue' in data else None
     query_string = request.query_string.decode()
     parsed_query = parse_qs(query_string)
     filter_values = parsed_query.get('filterValue[]', [])

     result_filter = filter_mui(rows,filterOperator,filterField,filterValue,filter_values,filterFieldType)
     return result_filter

def filter_mui(rows,operator,field,value,values,filterFieldType):
 

    # filter with one value and field type number
    if operator and field and value is not None and filterFieldType == "number":
        if operator == "=":
            return list(filter(lambda row: get_nested_value(row, field) ==  float(value), rows))
        elif operator == "!=":
            return list(filter(lambda row: get_nested_value(row, field) != float(value), rows))
        elif operator == ">":
            return list(filter(lambda row: get_nested_value(row, field)  > float(value), rows))
        elif operator == ">=":
            return list(filter(lambda row:get_nested_value(row, field) >= float(value), rows))
        elif operator == "<":
            return list(filter(lambda row: get_nested_value(row, field)  < float(value), rows))
        elif operator == "<=":
            return list(filter(lambda row: get_nested_value(row, field) <= float(value), rows))
    # filter with one value and field type string
    if operator and field and value is not None and filterFieldType == "string":
        if operator == "contains":
            return list(filter(lambda row: str(value) in str(get_nested_value(row, field)), rows))
        elif operator == "equals":
            return list(filter(lambda row: str(value) == str(get_nested_value(row, field)), rows))
        elif operator == "startsWith":
            return list(filter(lambda row: str(get_nested_value(row, field)).startswith(str(value)), rows))
        elif operator == "endsWith":
            return list(filter(lambda row: str(get_nested_value(row, field)).endswith(str(value)), rows))
    
    #filter with one value and field type date
    if operator and field and value is not None and filterFieldType == "date":
        import datetime
        if operator == "is":
            return list(filter(lambda row: get_nested_value(row, field) is not None and (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) == datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        elif operator == "not":
            return list(filter(lambda row: get_nested_value(row, field) is None or (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) != datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        elif operator == "after":
            return list(filter(lambda row: get_nested_value(row, field) is not None and (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) > datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        elif operator == "onOrAfter":
            return list(filter(lambda row: get_nested_value(row, field) is not None and (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) >= datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        elif operator == "before":
            return list(filter(lambda row: get_nested_value(row, field) is not None and (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) < datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        elif operator == "onOrBefore":
            return list(filter(lambda row: get_nested_value(row, field) is not None and (get_nested_value(row, field).date() if isinstance(get_nested_value(row, field), datetime.datetime) else datetime.datetime.strptime(get_nested_value(row, field), '%Y-%m-%d').date()) <= datetime.datetime.strptime(value, '%Y-%m-%d').date(), rows))
        
    # filter without value   
    if operator and field  is not None:
        if operator == "isEmpty":
                return list(filter(lambda row: get_nested_value(row, field) is None or  get_nested_value(row, field) == "", rows))
        elif operator == "isNotEmpty":
                return list(filter(lambda row: get_nested_value(row, field) is not None and  get_nested_value(row, field) != "", rows))
        
    # filter with one value and filed single select
    if operator and field and value is not None and filterFieldType == "singleSelect":
        if operator == "is":
            return list(filter(lambda row: str(get_nested_value(row, field)) == str(value), rows))
        elif operator == "not":
            return list(filter(lambda row: str(get_nested_value(row, field)) != str(value), rows))

    # filter wigh more than one value and filed single select
    if operator and field and values  is not None and filterFieldType == "singleSelect":
        if operator == "isAnyOf":
            return list(filter(lambda row: str(get_nested_value(row, field)) in values, rows))


    # filter with more than one values   
    if operator and field and values  is not None and filterFieldType == "number":
        if operator == "isAnyOf":
            values = [float(value) for value in values]
            return list(filter(lambda row: get_nested_value(row, field) in values, rows))
    
    # filter with more than one values
    if operator and field and values  is not None and filterFieldType == "string":
        if operator == "isAnyOf":
            return list(filter(lambda row: get_nested_value(row, field) in values, rows))
        

    return rows

def srverSideMode(rows,request):
    rows_copy = rows
    rowSorted =  my_sort(request,rows_copy)
    rowFiltered = my_filter(request,rowSorted)
    rowCustom_filterd = my_custom_filter(request,rowFiltered,'customeFilterBranchesValue[]','branch_id')
    rowCustom_filterd = my_custom_filter(request,rowCustom_filterd,'customeFilterStatusesValue[]','is_active',True)
    rowCustom_filterd = FilterServerSide(request, rowCustom_filterd).list
    
     #9966

    return my_pagination(request,rowCustom_filterd),len(rowCustom_filterd),rowCustom_filterd


def my_get_custom_filter_values(request,custom_filter_key,is_boolen=False,is_date=False):
    query_string = request.query_string.decode()
    parsed_query = parse_qs(query_string)
    values = parsed_query.get(custom_filter_key, [])
    
    if is_boolen:
        # Convert values to boolean
        bool_values = []
        for value in values:
            if value.lower() == 'true':
                bool_values.append(True)
            elif value.lower() == 'false':
                bool_values.append(False)
        return bool_values
    print(is_date) 
    if is_date:
        date_values = []
        for value in values:
            date_values.append(datetime.datetime.strptime(value, '%Y-%m-%d').date())
        return date_values
    

    return values
    


def my_custom_filter(request,rows,custom_filter_key,nested_key,is_boolen=False ,is_date=False):
   
  

    values =  my_get_custom_filter_values(request,custom_filter_key,is_boolen,is_date)

    if values is not None and len(values) > 0 and  not is_boolen and not is_date:

        rows = list(filter(lambda row: str(get_nested_value(row, nested_key)) in values, rows))

    if values is not None and len(values) > 0 and  is_boolen:
        rows = list(filter(lambda row: get_nested_value(row, nested_key) in values, rows))
    
    if is_date:
        if values is not None and len(values) > 0:
            rows = list(filter(lambda row: values[0] <= get_nested_value(row, nested_key).date() <= values[1], rows))
            


    return rows


# --- modular API: blueprints under api/ (see api/__init__.py) ---
from api import register_blueprints  # noqa: E402
register_blueprints(app)

if __name__ == "__main__":
    socketIO.run(app, port=Config.PORT, debug=Config.DEBUG, host="0.0.0.0", allow_unsafe_werkzeug=True)
