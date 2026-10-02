from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from services.research import main as research_main
from services.research.write_owner import ResearchWriteOwner


class _InMemoryStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    def put(self, key: str, payload: Dict[str, Any]) -> None:
        self._data[key] = json.loads(json.dumps(payload))

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        val = self._data.get(key)
        return json.loads(json.dumps(val)) if val else None

    def list_all(self, *, conn: Optional[Any] = None) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(v)) for v in self._data.values()]


@pytest.fixture
def mock_write_owner() -> ResearchWriteOwner:
    return ResearchWriteOwner(
        tickets_store=_InMemoryStore(),
        experiments_store=_InMemoryStore(),
        notes_store=_InMemoryStore(),
    )


@pytest.fixture
def client(mock_write_owner: ResearchWriteOwner) -> TestClient:
    research_main.set_write_owner(mock_write_owner)
    try:
        yield TestClient(research_main.app)
    finally:
        research_main.set_write_owner(None)


def test_research_tickets_lifecycle(client: TestClient) -> None:
    # 1. Create ticket
    res = client.post(
        "/api/research/tickets",
        json={
            "title": "Alpha Research Ticket",
            "description": "Exploration of momentum signal",
            "priority": "high",
            "owner": "quant_dev",
            "actor_id": "operator",
        },
    )
    assert res.status_code == 200, res.text
    ticket = res.json()
    ticket_id = ticket["ticket_id"]
    assert ticket["title"] == "Alpha Research Ticket"
    assert ticket["status"] == "open"
    assert ticket["priority"] == "high"

    # 2. Get ticket
    res_get = client.get(f"/api/research/tickets/{ticket_id}")
    assert res_get.status_code == 200
    assert res_get.json()["ticket_id"] == ticket_id

    # 3. List tickets
    res_list = client.get("/api/research/tickets?status=open")
    assert res_list.status_code == 200
    assert any(t["ticket_id"] == ticket_id for t in res_list.json())

    # 4. Patch ticket to in_progress
    res_patch = client.patch(
        f"/api/research/tickets/{ticket_id}",
        json={"status": "in_progress", "actor_id": "operator"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["status"] == "in_progress"

    # 5. Patch ticket to closed
    res_close = client.patch(
        f"/api/research/tickets/{ticket_id}",
        json={"status": "closed", "actor_id": "operator"},
    )
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "closed"


def test_research_experiments_lifecycle(client: TestClient) -> None:
    # 1. Create experiment
    res = client.post(
        "/api/research/experiments",
        json={
            "ticket_id": "rt-2026-001",
            "experiment_name": "Momentum Backtest 1",
            "strategy_selector": {"strategy_id": "strat-mom-01"},
            "parameter_set": {"lookback": 20},
            "run_config": {"stage": "backtest", "backend": "vectorbt"},
            "launch_context": {},
        },
    )
    assert res.status_code == 200, res.text
    exp = res.json()
    exp_id = exp["experiment_id"]
    assert exp["status"] == "queued"
    assert exp["allowedActions"]["canCancel"] is True

    # 2. Get experiment
    res_get = client.get(f"/api/research/experiments/{exp_id}")
    assert res_get.status_code == 200
    assert res_get.json()["experiment_id"] == exp_id

    # 3. List experiments
    res_list = client.get(f"/api/research/experiments?ticket_id=rt-2026-001")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 4. Cancel experiment
    res_cancel = client.post(
        f"/api/research/experiments/{exp_id}/cancel",
        json={"reason": "Operator test cancel", "actor_id": "tester"},
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "canceled"
    assert res_cancel.json()["allowedActions"]["canRetry"] is True

    # 5. Retry experiment
    res_retry = client.post(
        f"/api/research/experiments/{exp_id}/retry",
        json={"actor_id": "tester"},
    )
    assert res_retry.status_code == 200
    retried_exp = res_retry.json()
    assert retried_exp["attempt_number"] == 2
    assert retried_exp["status"] == "queued"

    # 6. Archive experiment
    res_archive = client.post(
        f"/api/research/experiments/{exp_id}/archive",
        json={"actor_id": "tester"},
    )
    assert res_archive.status_code == 200
    assert res_archive.json()["is_archived"] is True


def test_research_notes_lifecycle(client: TestClient) -> None:
    # 1. Create note
    res = client.post(
        "/api/research/notes",
        json={
            "note_id": "note-test-001",
            "title": "Findings on Volatility",
            "content": {"text": "Mean reversion observed in short tenor."},
            "tags": ["volatility", "options"],
        },
    )
    assert res.status_code == 200, res.text
    note = res.json()
    assert note["note_id"] == "note-test-001"

    # 2. Get note
    res_get = client.get("/api/research/notes/note-test-001")
    assert res_get.status_code == 200
    assert res_get.json()["title"] == "Findings on Volatility"

    # 3. List notes
    res_list = client.get("/api/research/notes")
    assert res_list.status_code == 200
    assert any(n.get("note_id") == "note-test-001" for n in res_list.json())


def test_research_write_owner_unavailable_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    research_main.set_write_owner(None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESEARCH_STORE_DSN", raising=False)
    client = TestClient(research_main.app)

    res = client.post("/api/research/tickets", json={"title": "Test Ticket"})
    assert res.status_code == 503
    assert "Research write owner unavailable" in res.json()["detail"]
