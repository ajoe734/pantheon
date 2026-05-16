"""ASK-004: committee session memo publish to registry / review contract tests.

Covers:
  GET  /bff/agora/committee/sessions/{id}/memos              — list session memos
  POST /bff/agora/committee/sessions/{id}/memos              — submit draft memo
  GET  /bff/agora/committee/sessions/{id}/memos/{memoId}     — get memo for review
  POST /bff/agora/committee/sessions/{id}/memos/{memoId}/publish — publish to registry

Canonical basis:
  AI_COLLABORATION_GUIDE.md (ASK-004 scope)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore

AUTH = {"Authorization": "Bearer ask-test-op:operator"}

_SEED_SESSIONS = {
    "committee-memo-001": {
        "id": "committee-memo-001",
        "sessionId": "committee-memo-001",
        "title": "Strategy Alpha committee review",
        "mode": "committee",
        "status": "open",
        "participants": [{"type": "persona", "id": "persona-alpha"}],
        "quorumState": "quorum",
        "consensusState": "open",
        "participantRoster": [],
        "messages": [],
        "createdAt": "2026-05-16T09:00:00Z",
        "updatedAt": "2026-05-16T09:00:00Z",
    },
    "ask-session-001": {
        "id": "ask-session-001",
        "sessionId": "ask-session-001",
        "title": "Quick ask — not a committee session",
        "mode": "quick_ask",
        "status": "active",
        "participants": [{"type": "operator", "id": "ask-test-op"}],
        "messages": [],
        "createdAt": "2026-05-16T08:00:00Z",
        "updatedAt": "2026-05-16T08:00:00Z",
    },
}


def _idem() -> str:
    return f"idem-{uuid.uuid4().hex[:16]}"


@contextmanager
def _client(*, seeded: bool = False) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        if seeded:
            with open(store_path, "w") as f:
                json.dump({"agora_sessions": _SEED_SESSIONS}, f)
        original_store = bff_main.read_store
        original_cmd = bff_main.command_store
        bff_main.read_store = ReadSurfaceStore(
            store_path,
            allow_local_snapshot_fallback=seeded,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_cmd
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()


# --------------------------------------------------------------------------- #
# GET /bff/agora/committee/sessions/{id}/memos  (list)
# --------------------------------------------------------------------------- #


def test_ask_004_list_memos_returns_envelope() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert "agora_committee_session_memos" in body["meta"]["surfaces"]


def test_ask_004_list_memos_empty_for_new_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
        assert resp.json()["page_info"]["total"] == 0


def test_ask_004_list_memos_404_for_missing_session() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/committee/sessions/nonexistent-999/memos", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_004_list_memos_404_for_ask_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/ask-session-001/memos", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_004_list_memos_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos")
        assert resp.status_code == 401, resp.text


def test_ask_004_list_memos_shows_submitted_memos() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-list-test-001", "summary": "Committee analysis"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["memo_id"] == "memo-list-test-001"


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{id}/memos  (submit)
# --------------------------------------------------------------------------- #


def test_ask_004_submit_memo_returns_201() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-submit-001", "summary": "Risk analysis complete"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text


def test_ask_004_submit_memo_response_shape() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={
                "memoId": "memo-shape-001",
                "memoType": "committee_summary",
                "summary": "All signals reviewed",
                "recommendations": ["Approve deployment"],
            },
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        data = body["data"]
        assert data["memo_id"] == "memo-shape-001"
        assert data["memo_type"] == "committee_summary"
        assert data["status"] == "draft"
        assert data["linked_session_id"] == "committee-memo-001"
        assert data["recommendations"] == ["Approve deployment"]
        assert data["session_to_memo_mapping"]["source_session_id"] == "committee-memo-001"
        assert data["session_to_memo_mapping"]["memo_id"] == "memo-shape-001"
        assert data["session_to_memo_mapping"]["mapping_status"] == "draft"


def test_ask_004_submit_memo_autogenerates_memo_id() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "No explicit memo id"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        memo_id = resp.json()["data"]["memo_id"]
        assert memo_id.startswith("memo-")


def test_ask_004_submit_memo_404_for_missing_session() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions/nonexistent-999/memos",
            json={"summary": "Should fail"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_submit_memo_404_for_ask_session() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/ask-session-001/memos",
            json={"summary": "Should fail — not a committee session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_submit_memo_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "No auth"},
        )
        assert resp.status_code == 401, resp.text


def test_ask_004_submit_memo_idempotency_replays() -> None:
    with _client(seeded=True) as client:
        key = _idem()
        payload = {"memoId": "memo-idem-001", "summary": "Idempotent"}
        headers = {**AUTH, "Idempotency-Key": key}
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers=headers,
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers=headers,
        )
        assert r1.status_code == 201, r1.text
        assert r2.status_code in {200, 201}, r2.text
        assert r1.json()["data"]["memo_id"] == r2.json()["data"]["memo_id"]


def test_ask_004_submit_memo_conflicts_on_duplicate_memo_id_with_new_key() -> None:
    with _client(seeded=True) as client:
        payload = {"memoId": "memo-duplicate-001", "summary": "First draft"}
        first = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        second = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={**payload, "summary": "Different draft"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 409, second.text


def test_ask_004_submit_memo_rejects_body_idempotency_key() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "bad", "idempotency_key": "should-reject"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# GET /bff/agora/committee/sessions/{id}/memos/{memoId}  (review)
# --------------------------------------------------------------------------- #


def test_ask_004_memo_detail_returns_200() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-detail-001", "summary": "Detail test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-detail-001",
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text


def test_ask_004_memo_detail_shape() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={
                "memoId": "memo-shape-detail-001",
                "memoType": "committee_summary",
                "summary": "Full detail shape",
                "recommendations": ["rec-1", "rec-2"],
            },
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-shape-detail-001",
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        data = body["data"]
        assert data["memo_id"] == "memo-shape-detail-001"
        assert data["memo_type"] == "committee_summary"
        assert data["status"] == "draft"
        assert data["linked_session_id"] == "committee-memo-001"
        assert data["summary"] == "Full detail shape"
        assert data["recommendations"] == ["rec-1", "rec-2"]
        assert "agora_committee_memo_detail" in body["meta"]["surfaces"]


def test_ask_004_memo_detail_404_for_missing_memo() -> None:
    with _client(seeded=True) as client:
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-does-not-exist",
            headers=AUTH,
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_memo_detail_404_for_wrong_session() -> None:
    with _client(seeded=True) as client:
        # Create a second committee session
        client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-other-001", "title": "Other session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Submit memo to first session
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-wrong-session-001", "summary": "Belongs to first session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Fetch from second session should 404
        resp = client.get(
            "/bff/agora/committee/sessions/committee-other-001/memos/memo-wrong-session-001",
            headers=AUTH,
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_memo_detail_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-any",
        )
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{id}/memos/{memoId}/publish
# --------------------------------------------------------------------------- #


def test_ask_004_publish_memo_returns_200() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-001", "summary": "Ready to publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text


def test_ask_004_publish_memo_sets_status_published() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-status-001", "summary": "Publish status test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-status-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "published"
        assert data["lifecycle_state"] == "published"
        assert data["published_at"] is not None
        assert data["session_to_memo_mapping"]["mapping_status"] == "active"


def test_ask_004_publish_memo_visible_in_registry() -> None:
    """Published memo should appear via GET /api/v1/consult/memos."""
    with _client(seeded=True) as client:
        memo_id = f"memo-registry-{uuid.uuid4().hex[:8]}"
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": memo_id, "summary": "Registry visible"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            f"/bff/agora/committee/sessions/committee-memo-001/memos/{memo_id}/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Verify the memo is now in the /api/v1/consult/memos registry
        list_resp = client.get(
            "/api/v1/consult/memos?status=published",
            headers=AUTH,
        )
        assert list_resp.status_code == 200, list_resp.text
        memo_ids = [item["memo_id"] for item in list_resp.json().get("items", [])]
        assert memo_id in memo_ids


def test_ask_004_publish_memo_detail_still_accessible() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-detail-001", "summary": "Post-publish detail"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-detail-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        detail_resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-detail-001",
            headers=AUTH,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["data"]["status"] == "published"


def test_ask_004_publish_memo_appears_in_session_memo_list() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-list-001", "summary": "In list after publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-list-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        list_resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            headers=AUTH,
        )
        items = list_resp.json()["items"]
        memo = next((m for m in items if m["memo_id"] == "memo-pub-list-001"), None)
        assert memo is not None
        assert memo["status"] == "published"


def test_ask_004_publish_memo_404_for_missing_memo() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-nonexistent/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_publish_memo_404_for_wrong_session() -> None:
    with _client(seeded=True) as client:
        # Create second committee session
        client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-other-pub-001", "title": "Other session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-wrong-pub-001", "summary": "Belongs to first session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-other-pub-001/memos/memo-wrong-pub-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_publish_memo_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-any/publish",
            json={},
        )
        assert resp.status_code == 401, resp.text


def test_ask_004_publish_memo_idempotency_replays() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-idem-001", "summary": "Idempotent publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        key = _idem()
        headers = {**AUTH, "Idempotency-Key": key}
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-idem-001/publish",
            json={},
            headers=headers,
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-idem-001/publish",
            json={},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["data"]["memo_id"] == r2.json()["data"]["memo_id"]


def test_ask_004_publish_memo_already_published_is_stable_with_new_key() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-stable-001", "summary": "Stable publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-stable-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-stable-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["data"]["published_at"] == r2.json()["data"]["published_at"]


def test_ask_004_publish_memo_rejects_body_idempotency_key() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-body-idem-001", "summary": "Body idem test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-body-idem-001/publish",
            json={"idempotency_key": "reject-me"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text
