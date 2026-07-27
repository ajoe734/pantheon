"""L12-EVO-001 integration proof against the real Research Orchestrator API.

The scripted receipt tests exercise retry and failure permutations in
isolation.  These tests additionally cross the actual service boundary:
Evolution writes a durable dispatch, the dispatch worker creates a real
Research Orchestrator task/run, and Evolution re-reads that run before it may
record either execution or compensation.
"""
from __future__ import annotations

import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.evolution import dispatch_worker
from services.evolution import main as evo_main
from services.evolution.dispatch_outbox import (
    CompensationLedger,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
    dispatch_identity,
)
from services.evolution.dispatch_receipts import build_adapter_registry


# Research Orchestrator still imports its sibling store module as ``store``.
# Make that service entrypoint importable without changing its ownership layer.
_RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from services.research import main as research_main  # noqa: E402
from services.research.store import ResearchOrchestratorStore  # noqa: E402


TENANT_ID = "tenant-real-research"
EVOLUTION_URL = "http://evolution.test"
RESEARCH_URL = "http://research.test"

evolution_client = TestClient(evo_main.app)
research_client = TestClient(research_main.app)


def _path(url: str) -> str:
    marker = "/api/"
    return url[url.index(marker) :] if marker in url else url


def _http_error(url: str, status_code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url,
        status_code,
        str(status_code),
        hdrs=None,
        fp=None,
    )


@pytest.fixture
def integrated_state(tmp_path, monkeypatch):
    """Give both services isolated durable stores and connect their HTTP edges."""
    decision_store = evo_main.EvolutionDecisionStore(
        storage_path=str(tmp_path / "evolution" / "decisions.json")
    )
    outbox = EvolutionDispatchOutbox(
        build_dispatch_outbox_store(data_dir=tmp_path / "evolution", backend="json")
    )
    compensations = CompensationLedger(data_dir=tmp_path / "evolution")
    research_store = ResearchOrchestratorStore(tmp_path / "research")

    monkeypatch.setattr(evo_main, "store", decision_store)
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", compensations)
    monkeypatch.setattr(research_main, "store", research_store)
    monkeypatch.setattr(research_main, "_trigger_retrain_execution", lambda *_args: None)

    def research_http_json(
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float,
    ) -> tuple[int, Any]:
        del timeout
        response = research_client.request(method, _path(url), json=payload)
        if response.status_code >= 400:
            raise _http_error(url, response.status_code)
        return response.status_code, response.json()

    registry = build_adapter_registry(
        research_api_url=RESEARCH_URL,
        http_json=research_http_json,
    )
    monkeypatch.setattr(evo_main, "receipt_registry", registry)

    def evolution_get(
        url: str,
        timeout_seconds: float,
        *,
        tenant_id: str | None = None,
        auth_token: str | None = None,
    ) -> Any:
        del timeout_seconds, auth_token
        headers = {"X-Tenant-Id": tenant_id} if tenant_id else {}
        response = evolution_client.get(_path(url), headers=headers)
        if response.status_code >= 400:
            raise _http_error(url, response.status_code)
        return response.json()

    def evolution_post(
        url: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        *,
        tenant_id: str | None = None,
        auth_token: str | None = None,
    ) -> Any:
        del timeout_seconds, auth_token
        headers = {"X-Tenant-Id": tenant_id} if tenant_id else {}
        response = evolution_client.post(_path(url), json=payload, headers=headers)
        if response.status_code >= 400:
            raise _http_error(url, response.status_code)
        return response.json()

    monkeypatch.setattr(dispatch_worker, "_http_get", evolution_get)
    monkeypatch.setattr(dispatch_worker, "_http_post", evolution_post)

    return {
        "outbox": outbox,
        "compensations": compensations,
        "registry": registry,
        "research_store": research_store,
        "data_dir": tmp_path / "evolution",
    }


def _approve_retrain(decision_id: str) -> dict[str, Any]:
    created = evolution_client.post(
        "/api/evolution/proposals",
        json={
            "decision_id": decision_id,
            "tenant_id": TENANT_ID,
            "target_type": "candidate_artifact",
            "target_id": f"artifact-{decision_id}",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Approved drawdown baseline was breached.",
            "created_by_id": "evolution-controller-01",
            "linked_incident_id": f"inc-{decision_id}",
        },
    )
    assert created.status_code == 201, created.text

    reviewed = evolution_client.post(
        f"/api/evolution/proposals/{decision_id}/review",
        json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "reviewer-01",
            "approval_decision_id": f"approval-{decision_id}",
            "tenant_id": TENANT_ID,
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    approved = evolution_client.post(
        f"/api/evolution/proposals/{decision_id}/approve",
        json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "approver-01",
            "tenant_id": TENANT_ID,
        },
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def _run_tick(state: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    return dispatch_worker.run_poll(
        api_url=EVOLUTION_URL,
        outbox=state["outbox"],
        registry=state["registry"],
        compensations=state["compensations"],
        timeout_seconds=5.0,
        now=now,
    )


def test_real_research_terminal_receipt_is_required_before_execution(integrated_state):
    decision_id = "evo-real-research-success"
    _approve_retrain(decision_id)
    first_tick_at = datetime.now(timezone.utc)

    pending = _run_tick(integrated_state, now=first_tick_at)
    assert pending["pending"] == 1, pending
    assert pending["executed"] == 0
    runs = integrated_state["research_store"].list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "queued"
    assert run["parameters"]["tenant_id"] == TENANT_ID
    assert run["parameters"]["decision_id"] == decision_id

    before_terminal = evolution_client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers={"X-Tenant-Id": TENANT_ID},
    ).json()
    assert before_terminal["decision_state"] == "approved"
    assert before_terminal["execution_result"] is None

    completed = research_client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/complete",
        json={
            "status": "completed",
            "summary": "Retrain evaluation completed.",
            "actor_id": "research-worker-01",
        },
    )
    assert completed.status_code == 200, completed.text

    converged = _run_tick(
        integrated_state,
        now=first_tick_at + timedelta(minutes=5),
    )
    assert converged["executed"] == 1, converged

    executed = evolution_client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers={"X-Tenant-Id": TENANT_ID},
    ).json()
    assert executed["decision_state"] == "executed"
    assert executed["execution_result"]["status"] == "succeeded"
    assert executed["execution_result"]["execution_ref_id"] == run["run_id"]
    assert "terminal status='completed'" in executed["execution_result"][
        "outcome_summary"
    ]

    outbox_id, _, _ = dispatch_identity(TENANT_ID, decision_id)
    assert integrated_state["outbox"].get_by_id(outbox_id).status.value == "published"


def test_real_research_failure_is_compensated_and_survives_ledger_restart(
    integrated_state,
):
    decision_id = "evo-real-research-failure"
    _approve_retrain(decision_id)
    first_tick_at = datetime.now(timezone.utc)
    pending = _run_tick(integrated_state, now=first_tick_at)
    assert pending["pending"] == 1, pending
    run = integrated_state["research_store"].list_runs()[0]

    failed = research_client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/complete",
        json={
            "status": "failed",
            "summary": "Retrain evaluation failed its safety checks.",
            "actor_id": "research-worker-01",
        },
    )
    assert failed.status_code == 200, failed.text

    compensated = _run_tick(
        integrated_state,
        now=first_tick_at + timedelta(minutes=5),
    )
    assert compensated["compensated"] == 1, compensated
    assert compensated["dead_lettered"] == 1
    assert compensated["executed"] == 0

    decision = evolution_client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers={"X-Tenant-Id": TENANT_ID},
    ).json()
    assert decision["decision_state"] == "approved"
    assert decision["execution_result"] is None

    reread = CompensationLedger(data_dir=integrated_state["data_dir"])
    obligation = reread.get(TENANT_ID, decision_id)
    assert obligation is not None
    assert obligation["downstream_ref_id"] == run["run_id"]
    assert obligation["resolved"] is False
