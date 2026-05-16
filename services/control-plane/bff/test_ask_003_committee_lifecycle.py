"""ASK-003: /bff/agora/committee/sessions committee session lifecycle contract tests.

Covers:
  GET  /bff/agora/committee/sessions                  — list (committee mode filter)
  POST /bff/agora/committee/sessions                  — create a committee session
  GET  /bff/agora/committee/sessions/{id}             — detail (also SSE resync route)
  POST /bff/agora/committee/sessions/{id}/open        — open a pending committee session
  POST /bff/agora/committee/sessions/{id}/close       — close with optional outcome/memoIds

Canonical basis:
  AI_COLLABORATION_GUIDE.md (ASK-003 scope)
  BFF_API_CONTRACT.md §11.4 (SSE resync routes)
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
    "committee-seeded-001": {
        "id": "committee-seeded-001",
        "sessionId": "committee-seeded-001",
        "title": "Strategy Alpha committee review",
        "mode": "committee",
        "status": "pending",
        "participants": [{"type": "persona", "id": "persona-alpha"}],
        "quorumState": "pending",
        "consensusState": "open",
        "participantRoster": [],
        "messages": [],
        "createdAt": "2026-05-16T09:00:00Z",
        "updatedAt": "2026-05-16T09:00:00Z",
    },
    "ask-seeded-001": {
        "id": "ask-seeded-001",
        "sessionId": "ask-seeded-001",
        "title": "Quick ask — must not appear in committee list",
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
# GET /bff/agora/committee/sessions  (list)
# --------------------------------------------------------------------------- #


def test_ask_003_list_returns_envelope() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert "agora_committee_sessions" in body["meta"]["surfaces"]


def test_ask_003_list_filters_to_committee_mode() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions", headers=AUTH)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        ids = [item.get("sessionId") or item.get("id") for item in items]
        assert "committee-seeded-001" in ids, "committee session must appear"
        assert "ask-seeded-001" not in ids, "quick_ask session must be filtered out"


def test_ask_003_list_requires_auth() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/committee/sessions")
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions  (create)
# --------------------------------------------------------------------------- #


def test_ask_003_create_returns_committee_session() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions",
            json={"title": "Strategy Beta review committee"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        session = body["data"]
        assert "sessionId" in session
        assert session["mode"] == "committee"
        assert session["status"] == "pending"
        assert "createdAt" in session


def test_ask_003_create_sets_committee_fields() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions",
            json={"title": "Capital pool committee", "linkedRequestId": "req-001"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        session = resp.json()["data"]
        assert session["mode"] == "committee"
        assert session.get("linkedRequestId") == "req-001"
        assert "quorumState" in session
        assert "consensusState" in session


def test_ask_003_create_accepts_explicit_session_id() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-explicit-001", "title": "Explicit committee"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["sessionId"] == "committee-explicit-001"


def test_ask_003_create_idempotent() -> None:
    with _client() as client:
        key = _idem()
        payload = {"sessionId": "committee-idem-001", "title": "Idempotent committee"}
        first = client.post(
            "/bff/agora/committee/sessions",
            json=payload,
            headers={**AUTH, "Idempotency-Key": key},
        )
        second = client.post(
            "/bff/agora/committee/sessions",
            json=payload,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["data"]["sessionId"] == second.json()["data"]["sessionId"]


def test_ask_003_create_requires_auth() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions",
            json={"title": "No auth"},
            headers={"Idempotency-Key": _idem()},
        )
        assert resp.status_code == 401, resp.text


def test_ask_003_create_requires_idempotency_key() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions",
            json={"title": "Missing key"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# GET /bff/agora/committee/sessions/{sessionId}  (detail — SSE resync route)
# --------------------------------------------------------------------------- #


def test_ask_003_detail_returns_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-seeded-001", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert body["data"]["sessionId"] == "committee-seeded-001"
        assert "meta" in body
        assert "snapshot_at" in body["meta"]


def test_ask_003_detail_404_for_unknown() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/committee/sessions/nonexistent-999", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_003_detail_serves_as_sse_resync_route() -> None:
    """SSE resync route must return canonical session shape with data + meta."""
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-seeded-001", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body, "SSE resync must include data key"
        assert "meta" in body, "SSE resync must include meta key"
        assert "snapshot_at" in body["meta"]


def test_ask_003_detail_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-seeded-001")
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{sessionId}/open
# --------------------------------------------------------------------------- #


def test_ask_003_open_transitions_to_open() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/open",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["status"] == "open"
        assert "openedAt" in body["data"]


def test_ask_003_open_404_for_unknown() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions/nonexistent-999/open",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_003_open_idempotent() -> None:
    with _client(seeded=True) as client:
        key = _idem()
        first = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/open",
            json={},
            headers={**AUTH, "Idempotency-Key": key},
        )
        second = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/open",
            json={},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["data"]["status"] == second.json()["data"]["status"]


def test_ask_003_open_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/open",
            json={},
            headers={"Idempotency-Key": _idem()},
        )
        assert resp.status_code == 401, resp.text


def test_ask_003_open_requires_idempotency_key() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/open",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{sessionId}/close
# --------------------------------------------------------------------------- #


def test_ask_003_close_transitions_to_closed() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["status"] == "closed"
        assert "closedAt" in body["data"]


def test_ask_003_close_records_outcome() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={"outcome": "consensus_reached"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["outcome"] == "consensus_reached"


def test_ask_003_close_records_memo_ids() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={"outcome": "consensus_reached", "memoIds": ["memo-001", "memo-002"]},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "closed"
        assert data.get("memoIds") == ["memo-001", "memo-002"]


def test_ask_003_close_404_for_unknown() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions/nonexistent-999/close",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_003_close_idempotent() -> None:
    with _client(seeded=True) as client:
        key = _idem()
        first = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={"outcome": "consensus_reached"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        second = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={"outcome": "consensus_reached"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["data"]["status"] == second.json()["data"]["status"]


def test_ask_003_close_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={},
            headers={"Idempotency-Key": _idem()},
        )
        assert resp.status_code == 401, resp.text


def test_ask_003_close_requires_idempotency_key() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-seeded-001/close",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# Lifecycle roundtrip: create → detail → open → close → verify
# --------------------------------------------------------------------------- #


def test_ask_003_full_lifecycle_create_open_close() -> None:
    with _client() as client:
        create_resp = client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-lifecycle-001", "title": "Lifecycle committee test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert create_resp.status_code == 201, create_resp.text
        session_id = create_resp.json()["data"]["sessionId"]
        assert session_id == "committee-lifecycle-001"
        assert create_resp.json()["data"]["status"] == "pending"

        detail_resp = client.get(f"/bff/agora/committee/sessions/{session_id}", headers=AUTH)
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["data"]["status"] == "pending"

        list_resp = client.get("/bff/agora/committee/sessions", headers=AUTH)
        assert list_resp.status_code == 200, list_resp.text
        list_ids = [item.get("sessionId") or item.get("id") for item in list_resp.json()["items"]]
        assert session_id in list_ids, "newly created committee session must appear in list"

        open_resp = client.post(
            f"/bff/agora/committee/sessions/{session_id}/open",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert open_resp.status_code == 200, open_resp.text
        assert open_resp.json()["data"]["status"] == "open"
        assert "openedAt" in open_resp.json()["data"]

        detail_after_open = client.get(f"/bff/agora/committee/sessions/{session_id}", headers=AUTH)
        assert detail_after_open.json()["data"]["status"] == "open"

        close_resp = client.post(
            f"/bff/agora/committee/sessions/{session_id}/close",
            json={"outcome": "consensus_reached", "memoIds": ["memo-final-001"]},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert close_resp.status_code == 200, close_resp.text
        closed_data = close_resp.json()["data"]
        assert closed_data["status"] == "closed"
        assert closed_data["outcome"] == "consensus_reached"
        assert closed_data.get("memoIds") == ["memo-final-001"]

        detail_after_close = client.get(f"/bff/agora/committee/sessions/{session_id}", headers=AUTH)
        assert detail_after_close.json()["data"]["status"] == "closed"
