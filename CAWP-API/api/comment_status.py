"""DELETE /comment_status/<id> (migration 0068): soft delete of a note status and its
sub-statuses. POST/PUT/GET-one stay in app.py (they call services/comment_status_tree.py) and
the list in api/reports.py.

Gate: `settings_comments_status_delete` (the role editor already offers the key; nothing used it
until now). Responses keep the legacy settings contract: 200 `{"message", "status": true, ...}`,
or 201 `{"message": <Arabic>, "status": false, "error": <code>}` (core/http.py).
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from core.auth import required_roles
from core.http import error_response
from extensions import db
from services import comment_status_tree as tree

bp = Blueprint("comment_status", __name__)
URL_PREFIX = None


@bp.route("/comment_status/<id>", methods=["DELETE"])
@jwt_required()
@required_roles("settings_comments_status_delete")
def delete_comment_status(id):
    try:
        out = tree.soft_delete(id)
    except tree.StatusRuleError as e:
        db.session.rollback()
        return jsonify(e.body()), 201
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return error_response("حدث خطأ أثناء حذف حالة التعليق", e)
    return jsonify({"message": "تم حذف حالة التعليق", "status": True, **out}), 200
