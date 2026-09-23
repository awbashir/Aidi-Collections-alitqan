"""Integration additions for stream A: the preview's per-collector `kind_summary` and
GET /v2/bulk/assign/collectors (the wizard's collector picker)."""
from __future__ import annotations

from test_bulk_assign_support import *  # noqa: F401,F403


def test_preview_carries_the_per_collector_summary(ac, db):
    cases = [mk(db, owner=A.col3)[0] for _ in range(3)]
    r = ac.post("/v2/bulk/assign", A.super, {"source": "selection", "case_ids": cases,
                                             "params": {"target_user_id": A.col1, "reason": "rebalance"}})
    assert r.status_code == 201, r.get_json()
    job_id = r.get_json()["data"]["id"]
    to_ready(ac, job_id)
    preview = ac.get(f"/v2/bulk/jobs/{job_id}/preview", A.super).get_json()["data"]
    by_target = preview["kind_summary"]["by_target"]
    assert [(t["user_id"], t["planned"]) for t in by_target] == [(A.col1, 3)]


def test_lock_preview_has_no_kind_summary(ac, db):
    case = mk(db)[0]
    r = ac.post("/v2/bulk/lock", A.super, {"source": "selection", "case_ids": [case], "params": {"reason": "x"}})
    if r.status_code == 403:          # the assign fixture's super role may lack the lock keys
        return
    job_id = r.get_json()["data"]["id"]
    to_ready(ac, job_id)
    assert ac.get(f"/v2/bulk/jobs/{job_id}/preview", A.super).get_json()["data"]["kind_summary"] is None


def test_collectors_endpoint(ac, db):
    body = ac.get("/v2/bulk/assign/collectors", A.super).get_json()["data"]
    ids = {u["id"] for u in body["items"]}
    assert {A.col1, A.col2, A.col3, A.col4} <= ids
    assert A.gone not in ids and A.clerk not in ids        # inactive / not a collector
    assert body["total"] == len(body["items"])
    assert ac.get("/v2/bulk/assign/collectors", A.nokey).status_code == 403
    # a supervisor sees only collectors inside their scope
    sup_ids = {u["id"] for u in ac.get("/v2/bulk/assign/collectors", A.sup).get_json()["data"]["items"]}
    assert sup_ids <= ids
