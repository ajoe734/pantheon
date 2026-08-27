from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import main as bff_main
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from agora.interaction.store import InteractionLifecycleStore
from agora.interaction.worker import AgoraInteractionWorker
from agora.strategy_workshop.store import MemoryWorkshopStore


class FakePersonaReadStore:
    def __init__(self, personas: Optional[List[Dict[str, Any]]] = None):
        self._personas = personas or [
            {"persona_id": "risk-analyst", "tenant_id": "pantheon-dev", "display_name": "Risk Analyst", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "macro-quant", "tenant_id": "pantheon-dev", "display_name": "Macro Quant", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "execution-guard", "tenant_id": "pantheon-dev", "display_name": "Execution Guard", "lifecycle_state": "active", "environment_ceiling": "paper"},
        ]

    def list_personas(self, **kwargs):
        return list(self._personas)

    def get_capability_snapshot_for_persona(self, persona_id):
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}

    def get_capability_snapshot(self, snapshot_id):
        return {"snapshot_id": snapshot_id, "capabilities": ["persona_opinion"]}

    def get_strategy_spec_detail(self, strategy_id, *, version_selector=None):
        return {"strategy_id": strategy_id, "strategy_spec_registry_id": version_selector or "v1"}

    def list_approval_decisions(self):
        return []

    def list_decision_journal_entries(self):
        return []


def _make_mock_client(return_values: Optional[Dict[str, Any]] = None, call_log: Optional[List[Dict[str, Any]]] = None):
    import json
    call_log = call_log if call_log is not None else []
    return_values = return_values or {}

    class MockClient(OpenClawOpsClient):
        def ensure_persona_opinion_agent(self, admission: Dict[str, Any], persona_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {"execution_authority": "none", "agent_id": admission.get("agent_id", "mock-agent")}

        def invoke_assistant_provider(self, *, prompt: str, **kwargs) -> Dict[str, Any]:
            persona_adm = kwargs.get("persona_admission") or {}
            pid = str(persona_adm.get("persona_id") or "")
            agent_id = str(kwargs.get("agent_id") or "mock-agent")
            call_log.append({"persona_id": pid, "agent_id": agent_id, "prompt": prompt, "kwargs": kwargs})
            if pid in return_values:
                val = return_values[pid]
                if isinstance(val, BaseException):
                    raise val
                return val
            opinion_data = {
                "conclusion": "support",
                "rationale": f"Analysis by {pid} confirms valid setup.",
                "confidence": 0.85,
                "uncertainty": [],
                "risks": ["Tail volatility risk"],
                "invalidation_conditions": ["Break below 200 SMA"],
                "evidence_refs": [],
                "recommended_measures": [],
            }
            return {
                "status": "completed",
                "output": {
                    "request_id": f"resp-{pid}-{uuid.uuid4().hex[:8]}",
                    "agent_id": agent_id,
                    "json_events": [
                        {
                            "item": {
                                "text": json.dumps(opinion_data)
                            }
                        }
                    ],
                },
            }

    return lambda: MockClient(), call_log


@pytest.fixture(autouse=True)
def clean_stores(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    read_store = FakePersonaReadStore()
    monkeypatch.setattr(bff_main, "read_store", read_store)

    if bff_main.interaction_lifecycle.backend == "memory":
        with bff_main.interaction_lifecycle._lock:
            bff_main.interaction_lifecycle._requests.clear()
            bff_main.interaction_lifecycle._idempotency.clear()
            bff_main.interaction_lifecycle._invocations.clear()
            bff_main.interaction_lifecycle._syntheses.clear()
            bff_main.interaction_lifecycle._outbox.clear()
            bff_main.interaction_lifecycle._candidate_links.clear()
            bff_main.interaction_lifecycle._audits.clear()
            bff_main.interaction_lifecycle._retry_commands.clear()
            bff_main.interaction_lifecycle._context_bindings.clear()
            bff_main.interaction_lifecycle._context_binding_latest.clear()
    if hasattr(bff_main.workshop_store, "_sessions"):
        bff_main.workshop_store._sessions.clear()
        bff_main.workshop_store._events.clear()
        bff_main.workshop_store._cards.clear()


@pytest.fixture
def bff_client():
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _submit_interaction(client, *, tenant_id="pantheon-dev", user_id="interaction-user", personas=("risk-analyst",), key=None):
    auth_header = {"Authorization": f"Bearer {user_id}:operator", "Idempotency-Key": key or f"test-key-{uuid.uuid4().hex}"}
    resolve_resp = client.post(
        "/bff/agora/interactions/context:resolve",
        headers=auth_header,
        json={
            "environment": "paper",
            "context_refs": [
                {"type": "strategy", "id": "strat-1", "version_id": "v1"},
                {"type": "decision_event", "id": "dec-1"},
            ],
        },
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    workshop_id = resolve_resp.json()["data"]["workshop_id"]

    submit_resp = client.post(
        "/bff/agora/interactions",
        headers=auth_header,
        json={
            "workshop_id": workshop_id,
            "mode": "consult",
            "environment": "paper",
            "topic": "Assess risk for allocation expansion",
            "participant_persona_ids": list(personas),
            "context_refs": [{"type": "strategy", "id": "strat-1", "version_id": "v1"}],
        },
    )
    return submit_resp, workshop_id


def test_admission_returns_before_provider_completion(bff_client, monkeypatch):
    calls = []
    client_factory, call_log = _make_mock_client(call_log=calls)

    submit_resp, workshop_id = _submit_interaction(bff_client, personas=("risk-analyst",))
    assert submit_resp.status_code == 202, submit_resp.text
    data = submit_resp.json()["data"]
    assert data["status"] == "queued"
    interaction_id = data["interaction_id"]

    # Provider must NOT have been called during HTTP submission
    assert len(calls) == 0

    # Consult result card must not exist yet
    cards = bff_client.get(f"/bff/agora/workshops/{workshop_id}/cards", headers={"Authorization": "Bearer interaction-user:operator"}).json()["data"]
    assert not any(card.get("card_type") == "consult_result" for card in cards)

    # Worker now processes the queued interaction
    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
        worker_id="test-worker-1",
    )
    processed = worker.run_once(limit=10)
    assert processed >= 1
    assert len(calls) == 1
    assert calls[0]["persona_id"] == "risk-analyst"

    # Detail now shows completed
    detail = bff_client.get(f"/bff/agora/interactions/{interaction_id}", headers={"Authorization": "Bearer interaction-user:operator"}).json()["data"]
    assert detail["status"] == "completed"
    assert len(detail["opinions"]) == 1
    assert detail["synthesis"] is not None

    # Card is now visible in workshop
    cards = bff_client.get(f"/bff/agora/workshops/{workshop_id}/cards", headers={"Authorization": "Bearer interaction-user:operator"}).json()["data"]
    assert any(card.get("card_type") == "consult_result" for card in cards)


def test_durable_lease_recovery_prevents_duplicate_invocation(bff_client):
    class WorkerCrash(BaseException):
        """Simulate unhandled process crash or hard kill."""

    # Worker 1 runs and succeeds for risk-analyst, but crashes before macro-quant completes
    client_factory_crashed, _ = _make_mock_client(
        return_values={"macro-quant": WorkerCrash("worker process killed mid-run")},
    )

    submit_resp, _ = _submit_interaction(bff_client, personas=("risk-analyst", "macro-quant"))
    assert submit_resp.status_code == 202
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker_1 = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_crashed,
        worker_id="crashed-worker-1",
        lease_duration_seconds=1,
    )
    with pytest.raises(WorkerCrash):
        worker_1.run_once(limit=1)

    # Expire worker 1 lease
    store = bff_main.interaction_lifecycle
    if store.backend == "memory":
        with store._lock:
            store._requests[interaction_id]["lease_until"] = "2020-01-01T00:00:00Z"
            store._requests[interaction_id]["status"] = "running"

    # Recovery worker starts up
    recovery_calls = []
    client_factory_recovery, _ = _make_mock_client(call_log=recovery_calls)

    recovery_worker = AgoraInteractionWorker(
        lifecycle_store=store,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_recovery,
        worker_id="recovery-worker-2",
    )
    processed = recovery_worker.run_once(limit=10)
    assert processed >= 1

    # Macro-quant should be called, but risk-analyst must NOT be re-invoked
    called_personas = [c["persona_id"] for c in recovery_calls]
    assert "macro-quant" in called_personas
    assert "risk-analyst" not in called_personas

    detail = bff_main.interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail["status"] == "completed"
    assert len(detail["opinions"]) == 2


def test_tenant_isolation_covers_every_interaction_route(bff_client, monkeypatch):
    client_factory, _ = _make_mock_client()
    submit_resp, _ = _submit_interaction(bff_client, tenant_id="pantheon-dev", user_id="user-a")
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    # Foreign tenant / user requests
    foreign_auth = {"Authorization": "Bearer foreign-user:operator", "X-Tenant-Id": "other-tenant"}

    # Foreign read
    assert bff_client.get(f"/bff/agora/interactions/{interaction_id}", headers=foreign_auth).status_code in {403, 404}

    # Foreign timeline
    assert bff_client.get(f"/bff/agora/interactions/{interaction_id}/timeline", headers=foreign_auth).status_code in {403, 404}

    # Foreign stream
    assert bff_client.get(f"/bff/agora/interactions/{interaction_id}/stream", headers=foreign_auth).status_code in {403, 404}

    # Foreign retry
    assert bff_client.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={**foreign_auth, "Idempotency-Key": "foreign-retry-key"},
        json={"reason": "Attacking foreign interaction"},
    ).status_code in {403, 404}

    # Foreign list does not contain tenant A interaction
    listed = bff_client.get("/bff/agora/interactions", headers=foreign_auth).json().get("data", [])
    assert not any(item["interaction_id"] == interaction_id for item in listed)

    # Scoped worker for foreign tenant does not claim Tenant A interaction
    foreign_worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
        worker_id="foreign-worker",
    )
    assert foreign_worker.claim_and_process_one(tenant_id="other-tenant") is None


def test_restart_preserves_terminal_readback(bff_client):
    client_factory, _ = _make_mock_client()
    submit_resp, _ = _submit_interaction(bff_client)
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
        worker_id="worker-restart-test",
    )
    worker.run_once()

    first_read = bff_client.get(f"/bff/agora/interactions/{interaction_id}", headers={"Authorization": "Bearer interaction-user:operator"}).json()["data"]
    assert first_read["status"] == "completed"

    # Simulate restart by reading through newly constructed lifecycle store instance
    if bff_main.interaction_lifecycle.backend == "postgres":
        restarted_store = InteractionLifecycleStore(backend="postgres", dsn=bff_main.interaction_lifecycle.dsn, schema=bff_main.interaction_lifecycle.schema)
        second_read = restarted_store.get(interaction_id, "pantheon-dev", "interaction-user")
        assert second_read["status"] == "completed"
        assert second_read["opinions"] == first_read["opinions"]
        assert second_read["synthesis"] == first_read["synthesis"]


def test_retry_and_recover_routes_do_not_execute_inline(bff_client):
    calls = []
    # Initially fail the provider so interaction becomes failed and eligible for retry
    client_factory_fail, _ = _make_mock_client(
        return_values={"risk-analyst": OpenClawOpsClientError("temporary provider error", status_code=503, error_code="SERVICE_UNAVAILABLE")},
    )

    submit_resp, _ = _submit_interaction(bff_client)
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker_fail = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_fail,
    )
    worker_fail.run_once()
    detail_fail = bff_main.interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail_fail["status"] == "failed"

    # Retry route
    client_factory_success, calls = _make_mock_client(call_log=calls)
    retry_resp = bff_client.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "retry-no-exec-key"},
        json={"reason": "Testing decoupled retry"},
    )
    assert retry_resp.status_code == 202
    assert retry_resp.json()["data"]["status"] == "queued"
    assert len(calls) == 0

    # Recover route
    recover_resp = bff_client.post(
        "/bff/agora/interactions:recover",
        headers={"Authorization": "Bearer interaction-user:operator"},
    )
    assert recover_resp.status_code == 202
    assert len(calls) == 0

    # Worker now executes retry
    worker_retry = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_success,
    )
    processed = worker_retry.run_once()
    assert processed >= 1
    assert len(calls) >= 1
    detail_retry = bff_main.interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail_retry["status"] == "completed"


def test_degraded_status_on_partial_failure(bff_client):
    calls = []
    # risk-analyst succeeds, macro-quant fails
    client_factory, _ = _make_mock_client(
        return_values={"macro-quant": RuntimeError("provider quota exceeded")},
        call_log=calls,
    )

    submit_resp, _ = _submit_interaction(bff_client, personas=("risk-analyst", "macro-quant"))
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
    )
    worker.run_once()

    detail = bff_main.interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail["status"] == "degraded"
    assert "macro-quant" in detail["missing_participant_ids"]
    assert len(detail["opinions"]) == 1
    assert detail["synthesis"]["status"] == "degraded"
    assert worker.metrics["degraded_count"] == 1
