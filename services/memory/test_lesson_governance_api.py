"""API contract tests for Trade Lesson Candidates memory governance endpoints."""

from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("services.memory.main")
from services.persona.lesson_governance import utc_now


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Setup temporary store files
    candidate_store_path = tmp_path / "trade_lesson_candidates.json"
    persona_store_path = tmp_path / "persona_memory_entries.json"
    institutional_store_path = tmp_path / "institutional_memory_entries.json"

    # Use monkeypatch to redirect environment variables
    monkeypatch.setenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", str(candidate_store_path))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(persona_store_path))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(institutional_store_path))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")

    # Reload store singletons for the test
    main._candidate_store.cache_clear() if hasattr(main._candidate_store, "cache_clear") else None
    main._persona_store.cache_clear() if hasattr(main._persona_store, "cache_clear") else None
    main._store.cache_clear() if hasattr(main._store, "cache_clear") else None

    return TestClient(main.app)


def make_valid_candidate_payload(overrides: dict | None = None) -> dict:
    base = {
        "lesson_candidate_id": str(uuid.uuid4()),
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4())],
        "persona_id": "persona-macro",
        "scope": "strategy",
        "proposed_change": "Reduce holding period target by 2 hours",
        "confidence": 0.75,
        "review_state": "proposed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "expiry": utc_now(),
    }
    if overrides:
        base.update(overrides)
    return base


def test_api_create_and_get_lesson_candidate(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    resp = client.post("/api/memory/trade-lessons", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["lesson_candidate_id"] == payload["lesson_candidate_id"]
    assert created["review_state"] == "proposed"

    resp = client.get(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}")
    assert resp.status_code == 200
    retrieved = resp.json()
    assert retrieved["lesson_candidate_id"] == payload["lesson_candidate_id"]

    resp = client.get("/api/memory/trade-lessons")
    assert resp.status_code == 200
    listed = resp.json()["candidates"]
    assert len(listed) == 1
    assert listed[0]["lesson_candidate_id"] == payload["lesson_candidate_id"]


def test_api_submit_review(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    client.post("/api/memory/trade-lessons", json=payload)

    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/submit-review")
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["review_state"] == "pending_review"


def test_api_decide_gate_check_failure(client: TestClient) -> None:
    # Pattern candidate (scope=strategy) but only 1 episode -> endorsement blocked
    payload = make_valid_candidate_payload({"scope": "strategy", "trade_episode_ids": [str(uuid.uuid4())]})
    client.post("/api/memory/trade-lessons", json=payload)

    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "episodes": []
    }
    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide", json=decide_payload)
    assert resp.status_code == 422
    assert "Endorsement blocked by evaluation gates" in resp.json()["detail"]["message"]


def test_api_decide_and_merge_success(client: TestClient) -> None:
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bear_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    payload = make_valid_candidate_payload({
        "scope": "strategy",
        "trade_episode_ids": ["ep1", "ep2", "ep3"],
    })
    client.post("/api/memory/trade-lessons", json=payload)

    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "episodes": episodes,
    }
    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide", json=decide_payload)
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "endorsed"

    # Merge to memory
    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge")
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "merged"

    # Query persona memory entries to confirm it exists
    resp = client.get("/api/memory/retrieve", params={
        "actor_id": "op-alice",
        "actor_roles": "operator",
        "session_id": "session-123",
        "persona_id": "persona-macro",
        "scope": "persona",
    })
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["entry"]["memory_id"] == f"pmem-lesson-{payload['lesson_candidate_id']}"
