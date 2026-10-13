"""Post-call IVR survey (services/calling/survey.py, api/calling_survey.py, migration 0066).

The PBX side runs against the scripted fake Asterisk of test_calling_agi: the survey context
calls collections-survey-offer, then collections-survey-result with the digit pressed."""
from __future__ import annotations

import uuid

import pytest

from test_calling_agi import FakeAsterisk, agi_server  # noqa: F401
from test_calling_helpers import (CL, add_contact, cl, cl_app, insert_attempt, key,  # noqa: F401
                                  make_case, new_contract, new_user, pgc, q1, qall, run)


@pytest.fixture(scope="module")
def survey_client(cl):
    """The calling test app registers only api.calling: a small app of its own for the survey routes."""
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.calling_survey
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    from test_calling_helpers import MAIN_DB, db_url

    app = Flask("cawp_test_calling_survey")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.calling_survey.bp)
    return app


@pytest.fixture
def survey_hdr(survey_client):
    from flask_jwt_extended import create_access_token

    def make(user_id, key=None):
        with survey_client.app_context():
            token = create_access_token(identity=str(user_id))
        return {"Authorization": f"Bearer {token}", **({"Idempotency-Key": key} if key else {})}
    return make


def survey(server, script, channel, *, attempt="", globals_=None, **channel_vars):
    env = {"agi_network_script": script, "agi_request": f"agi://calling:4573/{script}", "agi_uniqueid": channel,
           "agi_channel": "SIP/ITC_SIP_Trunk-00000001", "agi_type": "SIP", "agi_context": "cawp-survey",
           "agi_extension": "s"}
    return FakeAsterisk(server, attempt=attempt, token="", env=env, channel_vars=channel_vars, globals_=globals_).run()


def world(pgc, *, enabled=True):
    contract, portfolio = new_contract(pgc, "survey")
    if enabled:
        run(pgc, "INSERT INTO contract_call_settings (contract_id, post_call_survey) VALUES (%s, true)", contract)
    user = new_user(pgc)
    return contract, user, make_case(pgc, user, portfolio)


def rows(pgc, **where):
    clause = " AND ".join(f"{k} = %s" for k in where)
    return qall(pgc, f"SELECT source, status, score, case_id::text, agent_extension FROM call_surveys WHERE {clause}",
                *where.values())


def test_click_to_call_survey_offered_once_and_answered(agi_server, pgc):
    _, user, case = world(pgc)
    a = insert_attempt(pgc, case, user, state="ended", bridged=True, extension="7201", channel=True)
    ch = f"survey-{uuid.uuid4()}"
    fake = survey(agi_server, "collections-survey-offer", ch, attempt=a)
    assert fake.vars["COLLECTIONS_SURVEY"] == "1"
    sid = fake.vars["COLLECTIONS_SURVEY_ID"]
    done = survey(agi_server, "collections-survey-result", ch, COLLECTIONS_SURVEY_ID=sid, CAWP_SURVEY_SCORE="4")
    assert done.commands                                   # the exchange happened
    assert rows(pgc, attempt_id=a) == [("click_to_call", "answered", 4, case, "7201")]
    again = survey(agi_server, "collections-survey-offer", f"survey-{uuid.uuid4()}", attempt=a)
    assert again.vars["COLLECTIONS_SURVEY"] == "0" and len(rows(pgc, attempt_id=a)) == 1


def test_click_to_call_needs_a_conversation_and_the_contract_switch(agi_server, pgc):
    _, user, case = world(pgc)
    unbridged = insert_attempt(pgc, case, user, state="ended", bridged=False, extension="7201", channel=True)
    assert survey(agi_server, "collections-survey-offer", "ch-a", attempt=unbridged).vars["COLLECTIONS_SURVEY"] == "0"
    _, user2, case2 = world(pgc, enabled=False)
    off = insert_attempt(pgc, case2, user2, state="ended", bridged=True, extension="7202", channel=True)
    assert survey(agi_server, "collections-survey-offer", "ch-b", attempt=off).vars["COLLECTIONS_SURVEY"] == "0"
    assert rows(pgc, case_id=case) == [] and rows(pgc, case_id=case2) == []


def test_incoming_caller_is_matched_to_a_case(agi_server, pgc):
    _, user, case = world(pgc)
    add_contact(pgc, case, "0551234567")
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES ('7311', %s, 'test')", user)
    ch = f"in-{uuid.uuid4()}"
    fake = survey(agi_server, "collections-survey-offer", ch, CAWP_SURVEY_CALLER="+966551234567",
                  CAWP_SURVEY_AGENT="7311", CAWP_LANG="en")
    assert fake.vars["COLLECTIONS_SURVEY"] == "1"
    row = q1(pgc, "SELECT source, case_id::text, agent_user_id::text, language FROM call_surveys WHERE id = %s",
             fake.vars["COLLECTIONS_SURVEY_ID"])
    assert row == ("inbound", case, user, "en")
    unknown = survey(agi_server, "collections-survey-offer", f"in-{uuid.uuid4()}", CAWP_SURVEY_CALLER="+966559999999")
    assert unknown.vars["COLLECTIONS_SURVEY"] == "0"


def test_result_only_for_the_offered_channel(agi_server, pgc):
    _, user, case = world(pgc)
    add_contact(pgc, case, "0557654321")
    ch = f"in-{uuid.uuid4()}"
    sid = survey(agi_server, "collections-survey-offer", ch, CAWP_SURVEY_CALLER="0557654321").vars["COLLECTIONS_SURVEY_ID"]
    survey(agi_server, "collections-survey-result", "another-channel", COLLECTIONS_SURVEY_ID=sid, CAWP_SURVEY_SCORE="5")
    assert q1(pgc, "SELECT status FROM call_surveys WHERE id = %s", sid) == ("offered",)
    survey(agi_server, "collections-survey-result", ch, COLLECTIONS_SURVEY_ID=sid)              # nothing pressed
    assert q1(pgc, "SELECT status, score FROM call_surveys WHERE id = %s", sid) == ("no_input", None)
    survey(agi_server, "collections-survey-result", ch, COLLECTIONS_SURVEY_ID=sid, CAWP_SURVEY_SCORE="5")
    assert q1(pgc, "SELECT status FROM call_surveys WHERE id = %s", sid) == ("no_input",)          # completed once


def test_wrong_key_offers_nothing(agi_server, pgc):
    _, user, case = world(pgc)
    a = insert_attempt(pgc, case, user, state="ended", bridged=True, extension="7203", channel=True)
    fake = survey(agi_server, "collections-survey-offer", "ch-k", attempt=a, globals_={"COLLECTIONS_AGI_KEY": "wrong-key"})
    assert fake.vars["COLLECTIONS_SURVEY"] == "0" and rows(pgc, attempt_id=a) == []


def test_settings_and_results_api(survey_client, survey_hdr, pgc):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    client, hdr = survey_client.test_client(), survey_hdr
    contract, user, case = world(pgc, enabled=False)
    r = client.get("/v2/calling/surveys/settings", headers=hdr(CL.super))
    assert r.status_code == 200
    item = next(i for i in r.get_json()["data"]["items"] if i["contract_id"] == contract)
    assert item["post_call_survey"] is False and item["revision"] == 0
    put = lambda body, k=None: client.put(f"/v2/calling/surveys/settings/{contract}", json=body,  # noqa: E731
                                             headers=hdr(CL.super, key=k or key()))
    r = put({"post_call_survey": True, "reason": "client asked", "expected_revision": 0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["post_call_survey"] is True and r.get_json()["data"]["revision"] == 1
    assert put({"post_call_survey": False, "expected_revision": 0}).status_code == 409          # stale revision
    assert put({"post_call_survey": "yes", "expected_revision": 1}).status_code == 422
    nobody = new_user(pgc, CL.collector_role)
    assert client.get("/v2/calling/surveys/settings", headers=hdr(nobody)).status_code == 403
    run(pgc, "INSERT INTO call_surveys (source, case_id, contract_id, channel_id, status, score, completed_at) "
             "VALUES ('inbound', %s, %s, 'api-test', 'answered', 5, now())", case, contract)
    r = client.get("/v2/calling/surveys", query_string={"contract_id": contract}, headers=hdr(CL.super))
    data = r.get_json()["data"]
    assert data["summary"]["answered"] == 1 and data["summary"]["avg_score"] == 5.0
    assert data["summary"]["distribution"]["5"] == 1 and data["by_contract"][0]["contract_id"] == contract
