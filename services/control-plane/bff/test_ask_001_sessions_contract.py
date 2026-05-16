"""ASK-001: /bff/agora/ask/sessions session lifecycle contract tests.

Covers:
  GET  /bff/agora/ask/sessions           — list (quick_ask mode filter)
  POST /bff/agora/ask/sessions           — create a new ask session
  GET  /bff/agora/ask/sessions/{id}      — detail (also serves as SSE resync route)
  POST /bff/agora/ask/sessions/{id}/close — close a session

Canonical basis:
  BFF_API_CONTRACT.md §11.4 (SSE resync route: ask -> /bff/agora/ask/sessions/{id})
  AI_COLLABORATION_GUIDE.md (ASK-001 scope)
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
    "ask-seeded-001": {
        "id": "ask-seeded-001",
        "sessionId": "ask-seeded-001",
        "title": "Why did signal sig-001 fire?",
        "mode": "quick_ask",
        "status": "active",
        "participants": [{"type": "operator", "id": "ask-test-op"}],
        "messages": [],
        "createdAt": "2026-05-16T08:00:00Z",
        "updatedAt": "2026-05-16T08:00:00Z",
    },
    "committee-seeded-001": {
        "id": "committee-seeded-001",
        "sessionId": "committee-seeded-001",
        "title": "Committee session — must not appear in ask list",
        "mode": "committee",
        "status": "active",
        "participants": [{"type": "persona", "id": "persona-alpha"}],
        "messages": [],
        "createdAt": "2026-05-16T08:01:00Z",
        "updatedAt": "2026-05-16T08:01:00Z",
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
        bff_main._ASK_SESSIONS_IDEMPOTENCY.clear()
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_cmd
            bff_main._ASK_SESSIONS_IDEMPOTENCY.clear()
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()


# --------------------------------------------------------------------------- #
# GET /bff/agora/ask/sessions  (list)
# --------------------------------------------------------------------------- #


def test_ask_001_list_returns_envelope() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/ask/sessions", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert "agora_ask_sessions" in body["meta"]["surfaces"]


def test_ask_001_list_filters_to_quick_ask_mode() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/ask/sessions", headers=AUTH)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        ids = [item.get("sessionId") or item.get("id") for item in items]
        assert "ask-seeded-001" in ids, "quick_ask session must appear"
        assert "committee-seeded-001" not in ids, "committee session must be filtered out"


def test_ask_001_list_requires_auth() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/ask/sessions")
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/ask/sessions  (create)
# --------------------------------------------------------------------------- #


def test_ask_001_create_returns_session_with_required_fields() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/ask/sessions",
            json={"title": "Ask about strategy alpha"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        session = body["data"]
        assert "sessionId" in session
        assert session["mode"] == "quick_ask"
        assert session["status"] == "active"
        assert "createdAt" in session


def test_ask_001_create_accepts_explicit_session_id() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/ask/sessions",
            json={"sessionId": "ask-explicit-001", "title": "Explicit session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        session = resp.json()["data"]
        assert session["sessionId"] == "ask-explicit-001"


def test_ask_001_create_idempotent() -> None:
    with _client() as client:
        key = _idem()
        payload = {"sessionId": "ask-idem-001", "title": "Idempotent session"}
        first = client.post(
            "/bff/agora/ask/sessions",
            json=payload,
            headers={**AUTH, "Idempotency-Key": key},
        )
        second = client.post(
            "/bff/agora/ask/sessions",
            json=payload,
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["data"]["sessionId"] == second.json()["data"]["sessionId"]


def test_ask_001_create_requires_auth() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/ask/sessions",
            json={"title": "No auth"},
            headers={"Idempotency-Key": _idem()},
        )
        assert resp.status_code == 401, resp.text


def test_ask_001_create_requires_idempotency_key() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/ask/sessions",
            json={"title": "Missing key"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# GET /bff/agora/ask/sessions/{sessionId}  (detail — SSE resync route)
# --------------------------------------------------------------------------- #


def test_ask_001_detail_returns_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/ask/sessions/ask-seeded-001", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert body["data"]["sessionId"] == "ask-seeded-001"
        assert "meta" in body
        assert "snapshot_at" in body["meta"]


def test_ask_001_detail_404_for_unknown() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/ask/sessions/nonexistent-999", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_001_detail_serves_as_sse_resync_route() -> None:
    """SSE resync route must return the canonical session shape with data + meta."""
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/ask/sessions/ask-seeded-001", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body, "SSE resync must include data key"
        assert "meta" in body, "SSE resync must include meta key"
        assert "snapshot_at" in body["meta"]


def test_ask_001_detail_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/ask/sessions/ask-seeded-001")
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/ask/sessions/{sessionId}/close
# --------------------------------------------------------------------------- #


def test_ask_001_close_transitions_status_to_closed() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["status"] == "closed"
        assert "closedAt" in body["data"]


def test_ask_001_close_records_outcome() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={"outcome": "resolved"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["outcome"] == "resolved"


def test_ask_001_close_404_for_unknown() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/ask/sessions/nonexistent-999/close",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_001_close_idempotent() -> None:
    with _client(seeded=True) as client:
        key = _idem()
        first = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={"outcome": "resolved"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        second = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={"outcome": "resolved"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["data"]["status"] == second.json()["data"]["status"]


def test_ask_001_close_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={},
            headers={"Idempotency-Key": _idem()},
        )
        assert resp.status_code == 401, resp.text


def test_ask_001_close_requires_idempotency_key() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/ask/sessions/ask-seeded-001/close",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# Lifecycle roundtrip
# --------------------------------------------------------------------------- #


def test_ask_001_full_lifecycle_create_detail_close() -> None:
    with _client() as client:
        create_resp = client.post(
            "/bff/agora/ask/sessions",
            json={"sessionId": "ask-lifecycle-001", "title": "Full lifecycle test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert create_resp.status_code == 201, create_resp.text
        session_id = create_resp.json()["data"]["sessionId"]
        assert session_id == "ask-lifecycle-001"

        detail_resp = client.get(f"/bff/agora/ask/sessions/{session_id}", headers=AUTH)
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["data"]["status"] == "active"

        list_resp = client.get("/bff/agora/ask/sessions", headers=AUTH)
        assert list_resp.status_code == 200, list_resp.text
        list_ids = [item.get("sessionId") or item.get("id") for item in list_resp.json()["items"]]
        assert session_id in list_ids, "newly created session must appear in list"

        close_resp = client.post(
            f"/bff/agora/ask/sessions/{session_id}/close",
            json={"outcome": "answered"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert close_resp.status_code == 200, close_resp.text
        assert close_resp.json()["data"]["status"] == "closed"

        detail_after = client.get(f"/bff/agora/ask/sessions/{session_id}", headers=AUTH)
        assert detail_after.status_code == 200, detail_after.text
        assert detail_after.json()["data"]["status"] == "closed"
