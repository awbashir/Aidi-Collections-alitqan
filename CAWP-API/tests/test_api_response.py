"""core.api_response: the new-route convention (real HTTP codes, {message, status, error, code})."""
import pytest
from flask import Blueprint, Flask

from core import api_response as R

pytestmark = pytest.mark.no_db


@pytest.fixture
def mini_app():
    app = Flask("resp_test")
    app.config["TESTING"] = True
    new = Blueprint("new_style", __name__)
    R.register_api_errors(new)

    @new.post("/new/validate")
    def validate():
        body = R.json_body()
        if "name" not in body:
            raise R.ApiError(422, R.VALIDATION_FAILED, "name is required", {"name": "required"})
        return R.created({"name": body["name"]})

    @new.get("/new/gone")
    def gone():
        return R.not_found("case not found")

    old = Blueprint("old_style", __name__)

    @old.post("/old/validate")
    def old_validate():
        raise R.ApiError(422, R.VALIDATION_FAILED, "boom")    # no handler on this blueprint

    app.register_blueprint(new)
    app.register_blueprint(old)
    return app


def test_success_and_failure_shapes(mini_app):
    c = mini_app.test_client()
    r = c.post("/new/validate", json={"name": "x"})
    assert r.status_code == 201 and r.get_json() == {"message": "created", "status": True, "data": {"name": "x"}}
    r = c.post("/new/validate", json={})
    assert r.status_code == 422
    assert r.get_json() == {"message": "name is required", "status": False,
                            "error": {"name": "required"}, "code": "validation_failed"}
    r = c.post("/new/validate", data="not json", content_type="application/json")
    assert r.status_code == 400 and r.get_json()["code"] == "invalid_json"
    r = c.post("/new/validate", json=[1, 2])
    assert r.status_code == 400 and r.get_json()["code"] == "invalid_json"
    r = c.get("/new/gone")
    assert r.status_code == 404 and r.get_json() == {"message": "case not found", "status": False,
                                                    "error": None, "code": "not_found"}


def test_handlers_are_blueprint_scoped(mini_app):
    mini_app.config["TESTING"] = False   # let Flask turn the unhandled error into a 500
    r = mini_app.test_client().post("/old/validate", json={})
    assert r.status_code == 500


def test_401_is_refused():
    with pytest.raises(ValueError):
        R.ApiError(401, "x", "no")
    app = Flask("t")
    with app.app_context():
        with pytest.raises(ValueError):
            R.fail(401, "x", "no")
        with pytest.raises(ValueError):
            R.fail(200, "x", "no")
        body, status = R.conflict("stale", {"current_revision": 3}, code=R.REVISION_CONFLICT)
        assert status == 409 and body.get_json()["code"] == "revision_conflict"
        assert R.forbidden()[1] == 403 and R.unprocessable()[1] == 422 and R.bad_request()[1] == 400
        assert R.ok([1])[0].get_json() == {"message": "ok", "status": True, "data": [1]}
