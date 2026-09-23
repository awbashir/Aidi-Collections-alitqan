"""/v2 notes: case note list, history, correction (original kept, author/time unchanged),
retirement, permissions, scope, idempotent replay and revision conflicts."""
import uuid

from test_history_support import *  # noqa: F401,F403


def correct(hc, as_user, note, text, reason="typo", rev=0, user=H.col1, key=True):
    return hc.post(f"/v2/notes/{note}/correct", json={"text": text, "reason": reason, "expected_revision": rev},
                   headers=as_user(user, key))


def test_correction_keeps_original_and_author(hc, as_user, hpg):
    note = make_note(hpg, text="Called, promised 500 SAR", create_at="2026-08-01T07:30:00Z")
    before = q1(hpg, 'SELECT user_id::text, create_at, "contactMethod_id"::text FROM "Comments" WHERE id = %s', note)

    r = correct(hc, as_user, note, "Called, promised 50 SAR", reason="amount typo", user=H.reviewer)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["text"] == "Called, promised 50 SAR"
    assert data["original_text"] == "Called, promised 500 SAR"
    assert data["corrected"] is True and data["correction_count"] == 1 and data["revision"] == 1
    assert data["author"]["id"] == H.col1                                  # the author, not the corrector
    assert data["created_at"].startswith("2026-08-01T07:30:00")

    # the legacy column now holds the latest text, author and time untouched
    assert q1(hpg, 'SELECT comment FROM "Comments" WHERE id = %s', note)[0] == "Called, promised 50 SAR"
    assert q1(hpg, 'SELECT user_id::text, create_at, "contactMethod_id"::text FROM "Comments" WHERE id = %s', note) == before
    revs = q(hpg, "SELECT revision_no, kind, text, reason, corrected_by::text FROM note_revisions "
                  "WHERE comment_id = %s ORDER BY revision_no", note)
    assert revs == [(0, "original", "Called, promised 500 SAR", None, None),
                    (1, "correction", "Called, promised 50 SAR", "amount typo", H.reviewer)]
    (action, actor, source, reason, b, a), = audit_rows(hpg, "comment", note)
    assert (action, actor, source, reason) == ("note.correct", H.reviewer, "api", "amount typo")
    assert b["comment"] == "Called, promised 500 SAR" and a["comment"] == "Called, promised 50 SAR"

    # a second correction appends; the original stays revision 0
    r = correct(hc, as_user, note, "Called, promised 55 SAR", reason="again", rev=1)
    assert r.status_code == 200 and r.get_json()["data"]["original_text"] == "Called, promised 500 SAR"
    assert [x[0] for x in q(hpg, "SELECT revision_no FROM note_revisions WHERE comment_id = %s ORDER BY 1", note)] == [0, 1, 2]

    h = hc.get(f"/v2/notes/{note}/history", headers=as_user(H.col1, key=None)).get_json()["data"]
    assert [x["kind"] for x in h["revisions"]] == ["original", "correction", "correction"]
    assert h["revisions"][1]["corrected_by"]["id"] == H.reviewer
    assert [e["action"] for e in h["events"]] == ["note.correct", "note.correct"]
    assert h["events"][0]["changes"]["comment"] == {"before": "Called, promised 50 SAR", "after": "Called, promised 55 SAR"}
    assert "revision" not in h["events"][0]["changes"] and h["hard_deleted"] is False


def test_correction_validation(hc, as_user, hpg):
    note = make_note(hpg, text="same")
    assert correct(hc, as_user, note, "same").status_code == 422                          # no change
    r = hc.post(f"/v2/notes/{note}/correct", json={"text": "new", "expected_revision": 0}, headers=as_user(H.col1))
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    r = hc.post(f"/v2/notes/{note}/correct", json={"text": " ", "reason": "x", "expected_revision": 0},
                headers=as_user(H.col1))
    assert r.status_code == 422 and r.get_json()["error"] == {"text": "required"}
    r = hc.post(f"/v2/notes/{note}/correct", json={"text": "new", "reason": "x"}, headers=as_user(H.col1))
    assert r.status_code == 422 and r.get_json()["error"] == {"expected_revision": "required"}


def test_revision_conflict(hc, as_user, hpg):
    note = make_note(hpg)
    q(hpg, 'UPDATE "Comments" SET comment = %s WHERE id = %s', "changed by legacy", note)   # revision -> 1
    r = correct(hc, as_user, note, "mine", rev=0)
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "revision_conflict" and body["error"]["current_revision"] == 1
    assert q(hpg, "SELECT 1 FROM note_revisions WHERE comment_id = %s", note) == []        # rolled back


def test_idempotent_replay(hc, as_user, hpg):
    note = make_note(hpg)
    key = str(uuid.uuid4())
    first = correct(hc, as_user, note, "replayed text", key=key)
    second = correct(hc, as_user, note, "replayed text", key=key)
    assert first.status_code == second.status_code == 200
    assert second.data == first.data and second.headers.get("Idempotent-Replayed") == "true"
    assert q1(hpg, "SELECT count(*) FROM note_revisions WHERE comment_id = %s", note)[0] == 2
    assert len(audit_rows(hpg, "comment", note)) == 1
    other = correct(hc, as_user, note, "different", key=key)
    assert other.status_code == 422 and other.get_json()["code"] == "idempotency_key_reused"
    no_key = hc.post(f"/v2/notes/{note}/correct", json={"text": "x", "reason": "y", "expected_revision": 1},
                     headers=as_user(H.col1, key=None))
    assert no_key.status_code == 400 and no_key.get_json()["code"] == "idempotency_key_required"


def test_retire_is_soft_with_reason(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    older = make_note(hpg, case_id=case_id, text="older", create_at="2026-09-01T08:00:00Z")
    newer = make_note(hpg, case_id=case_id, text="newer", create_at="2026-09-02T08:00:00Z")
    assert q1(hpg, "SELECT last_comment_id::text FROM clints WHERE id = %s", case_id)[0] == newer

    r = hc.post(f"/v2/notes/{newer}/retire", json={"reason": "wrong case", "expected_revision": 0},
                headers=as_user(H.col1))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["retired"] is True
    row = q1(hpg, 'SELECT is_deleted, comment, user_id::text FROM "Comments" WHERE id = %s', newer)
    assert row == (True, "newer", H.col1)                                  # soft: row and text kept
    assert q1(hpg, "SELECT last_comment_id::text FROM clints WHERE id = %s", case_id)[0] == older
    (action, actor, _s, reason, _b, a), = audit_rows(hpg, "comment", newer)
    assert (action, actor, reason, a["is_deleted"]) == ("note.retire", H.col1, "wrong case", True)

    # list hides it by default, shows it on request
    lst = hc.get(f"/v2/cases/{case_id}/notes", headers=as_user(H.col1, key=None)).get_json()["data"]
    assert [n["id"] for n in lst["items"]] == [older] and lst["total"] == 1
    lst = hc.get(f"/v2/cases/{case_id}/notes?include_retired=1", headers=as_user(H.col1, key=None)).get_json()["data"]
    assert [n["id"] for n in lst["items"]] == [newer, older] and lst["items"][0]["retired"] is True

    again = hc.post(f"/v2/notes/{newer}/retire", json={"reason": "x", "expected_revision": 1}, headers=as_user(H.col1))
    assert again.status_code == 409
    fix = correct(hc, as_user, newer, "fixing a retired note", rev=1)
    assert fix.status_code == 409 and fix.get_json()["code"] == "note_retired"
    missing_reason = hc.post(f"/v2/notes/{older}/retire", json={"expected_revision": 0}, headers=as_user(H.col1))
    assert missing_reason.status_code == 422


def test_locked_case_blocks_note_changes(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    note = make_note(hpg, case_id=case_id)
    q(hpg, "UPDATE clints SET is_locked = true WHERE id = %s", case_id)
    r = correct(hc, as_user, note, "new text")
    assert r.status_code == 409 and r.get_json()["code"] == "case_locked"


def test_permissions(hc, as_user, hpg):
    note = make_note(hpg, case_id=H.case4, user_id=H.ro)
    for method, url, body in [
        ("post", f"/v2/notes/{note}/correct", {"text": "a", "reason": "b", "expected_revision": 0}),
        ("post", f"/v2/notes/{note}/retire", {"reason": "b", "expected_revision": 0}),
        ("get", f"/v2/notes/{note}/history", None),
    ]:
        r = getattr(hc, method)(url, json=body, headers=as_user(H.ro))
        assert r.status_code == 403 and r.get_json()["code"] == "forbidden", url
    # read-only may list notes (clints_details)
    assert hc.get(f"/v2/cases/{H.case4}/notes", headers=as_user(H.ro, key=None)).status_code == 200


def test_scope(hc, as_user, hpg):
    """col2 owns case3 only; the reviewer (supervisor of contract 1) cannot see case3."""
    note1 = make_note(hpg, case_id=H.case1)
    note3 = make_note(hpg, case_id=H.case3, user_id=H.col2)
    assert correct(hc, as_user, note1, "x", user=H.col2).status_code == 404
    assert hc.get(f"/v2/notes/{note1}/history", headers=as_user(H.col2, key=None)).status_code == 404
    assert hc.get(f"/v2/cases/{H.case1}/notes", headers=as_user(H.col2, key=None)).status_code == 404
    assert hc.get(f"/v2/notes/{note3}/history", headers=as_user(H.reviewer, key=None)).status_code == 404
    assert hc.get(f"/v2/notes/{note3}/history", headers=as_user(H.super, key=None)).status_code == 200
    assert hc.get(f"/v2/notes/{note1}/history", headers=as_user(H.reviewer, key=None)).status_code == 200
    assert hc.get(f"/v2/notes/{uuid.uuid4()}/history", headers=as_user(H.super, key=None)).status_code == 404


def test_history_of_hard_deleted_note(hc, as_user, hpg):
    note = make_note(hpg, text="gone soon")
    q(hpg, 'DELETE FROM "Comments" WHERE id = %s', note)                   # legacy DELETE /comments/<id>
    h = hc.get(f"/v2/notes/{note}/history", headers=as_user(H.col1, key=None))
    assert h.status_code == 200
    data = h.get_json()["data"]
    assert data["note"] is None and data["hard_deleted"] is True and data["case_id"] == H.case1
    (ev,) = data["events"]
    assert ev["action"] == "comment.delete" and ev["actor"] is None and ev["source"] == "db_trigger"
    assert ev["deleted_row"]["comment"] == "gone soon"


def test_case_notes_paging(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    ids = [make_note(hpg, case_id=case_id, text=f"n{i}", create_at=f"2026-09-0{i + 1}T08:00:00Z") for i in range(5)]
    page2 = hc.get(f"/v2/cases/{case_id}/notes?page=2&page_size=2", headers=as_user(H.col1, key=None)).get_json()["data"]
    assert page2["total"] == 5 and page2["page"] == 2 and page2["page_size"] == 2
    assert [n["id"] for n in page2["items"]] == [ids[2], ids[1]]
    item = page2["items"][0]
    assert item["author"] == {"id": H.col1, "name": "Hist Collector1"}
    assert item["status"]["id"] == Ids.comment_status and item["corrected"] is False
    assert item["original_text"] == item["text"]
