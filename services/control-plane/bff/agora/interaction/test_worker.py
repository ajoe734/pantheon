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

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from services.control_plane.bff.agora.interaction.store import InteractionLifecycleStore
from services.control_plane.bff.agora.interaction.worker import AgoraInteractionWorker
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore


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


def _make_mock_client(
    return_values: Optional[Dict[str, Any]] = None,
    call_log: Optional[List[Dict[str, Any]]] = None,
    invoke_fn: Optional[Any] = None,
):
    import json
    call_log = call_log if call_log is not None else []
    return_values = return_values or {}

    class MockClient(OpenClawOpsClient):
        def ensure_persona_opinion_agent(self, admission: Dict[str, Any], persona_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {"execution_authority": "none", "agent_id": admission.get("agent_id", "mock-agent")}

        def invoke_assistant_provider(self, *, prompt: str, **kwargs) -> Dict[str, Any]:
            if invoke_fn is not None:
                return invoke_fn(prompt=prompt, **kwargs)
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
            for inv_id, inv_row in store._invocations.get(interaction_id, {}).items():
                if inv_row.get("status") == "running":
                    inv_row["lease_until"] = "2020-01-01T00:00:00Z"

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


def test_restart_preserves_terminal_readback(tmp_path):
    storage_file = str(tmp_path / "interactions_durable.json")
    durable_store = InteractionLifecycleStore(storage_filepath=storage_file)
    client_factory, _ = _make_mock_client()

    workshop_store = MemoryWorkshopStore()
    read_store = FakePersonaReadStore()
    session = workshop_store.create_session({
        "session_id": "ws-restart",
        "workshop_id": "ws-restart",
        "tenant_id": "pantheon-dev",
        "user_id": "interaction-user",
        "created_at": "2026-08-27T00:00:00Z",
    })

    binding = {
        "binding_id": "bind-restart-1",
        "tenant_id": "pantheon-dev",
        "workshop_id": session["workshop_id"],
        "context_digest": "cd-restart-1",
        "source_route": "/agora/workshops/ws-restart",
        "focused_object": {"kind": "strategy", "id": "strat-1", "version": "v1"},
        "context_refs": [{"kind": "strategy", "id": "strat-1", "version": "v1"}],
        "selected_persona_ids": ["risk-analyst"],
        "initial_mode": "consult",
        "return_route": "/agora/workshops/ws-restart",
        "captured_at": "2026-08-27T00:00:00Z",
        "evidence_cutoff": "2026-08-27T00:00:00Z",
    }
    durable_store.save_context_binding(binding, owner_user_id="interaction-user")

    interaction_id = "ix-restart-test-1"
    req_body = {
        "interaction_id": interaction_id,
        "tenant_id": "pantheon-dev",
        "owner_user_id": "interaction-user",
        "workshop_id": session["workshop_id"],
        "human_request": {
            "mode": "consult",
            "request_text": "Restart recovery test topic",
            "operator_id": "interaction-user",
            "submitted_at": "2026-08-27T00:00:00Z",
        },
        "context_snapshot": {
            "initial_mode": "consult",
            "selected_persona_ids": ["risk-analyst"],
            "context_refs": [{"kind": "strategy", "id": "strat-1", "version": "v1"}],
        },
        "created_at": "2026-08-27T00:00:00Z",
        "updated_at": "2026-08-27T00:00:00Z",
        "status": "queued",
        "_context_binding": binding,
    }
    durable_store.create_request(
        req_body,
        idempotency_scope="pantheon-dev:interaction-user",
        idempotency_key="key-restart-1",
        fingerprint="fp-restart-1",
        trace_id="tr-restart-1",
    )

    worker = AgoraInteractionWorker(
        lifecycle_store=durable_store,
        workshop_store=workshop_store,
        read_store=read_store,
        client_factory=client_factory,
        worker_id="worker-restart-test",
    )
    processed = worker.run_once()
    assert processed is not None

    first_read = durable_store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert first_read is not None
    assert first_read["status"] == "completed"
    assert len(first_read["opinions"]) == 1
    assert first_read["synthesis"] is not None

    # Simulate restart by constructing brand new store instance from disk
    restarted_store = InteractionLifecycleStore(storage_filepath=storage_file)
    second_read = restarted_store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert second_read is not None
    assert second_read["status"] == "completed"
    assert second_read["opinions"] == first_read["opinions"]
    assert second_read["synthesis"] == first_read["synthesis"]
    assert second_read["provider_invocations"] == first_read["provider_invocations"]


def test_claim_invocation_pre_expiry_rejects_second_claim():
    store = InteractionLifecycleStore()
    interaction_id = "ix-claim-pre-expiry-1"
    req_body = {
        "interaction_id": interaction_id,
        "tenant_id": "pantheon-dev",
        "owner_user_id": "interaction-user",
        "workshop_id": "ws-1",
        "human_request": {"operator_id": "interaction-user", "request_text": "Claim test"},
        "created_at": "2026-08-27T00:00:00Z",
        "updated_at": "2026-08-27T00:00:00Z",
        "status": "queued",
    }
    store.create_request(
        req_body,
        idempotency_scope="pantheon-dev:interaction-user",
        idempotency_key="key-claim-1",
        fingerprint="fp-claim-1",
        trace_id="tr-claim-1",
    )
    invocation = {
        "invocation_id": "inv-claim-1",
        "interaction_id": interaction_id,
        "participant": {"persona_id": "risk-analyst", "display_name": "Risk Analyst"},
        "provider_kind": "openclaw",
        "status": "running",
    }

    # First claim by worker-1 succeeds
    first_row, first_claimed = store.claim_invocation(
        interaction_id,
        invocation,
        lease_owner="worker-1",
        lease_duration_seconds=300,
    )
    assert first_claimed is True
    assert first_row["status"] == "running"
    assert first_row["attempt"] == 1
    assert first_row["lease_owner"] == "worker-1"

    # Second claim by worker-2 before lease expiry fails
    second_row, second_claimed = store.claim_invocation(
        interaction_id,
        invocation,
        lease_owner="worker-2",
        lease_duration_seconds=300,
    )
    assert second_claimed is False
    assert second_row["status"] == "running"
    assert second_row["attempt"] == 1
    assert second_row["lease_owner"] == "worker-1"


def test_concurrent_pre_expiry_workers_execute_provider_only_once(bff_client):
    import concurrent.futures
    import json

    call_log = []
    barrier = threading.Barrier(2)

    def slow_invoke(*args, **kwargs):
        call_log.append(kwargs)
        try:
            barrier.wait(timeout=1.0)
        except Exception:
            pass
        time.sleep(0.05)
        opinion_data = {
            "conclusion": "support",
            "rationale": "Concurrent invocation safety verified.",
            "confidence": 0.9,
            "uncertainty": [],
            "risks": [],
            "invalidation_conditions": [],
            "evidence_refs": [],
            "recommended_measures": [],
        }
        return {
            "status": "completed",
            "output": {
                "request_id": f"resp-risk-analyst-{uuid.uuid4().hex[:8]}",
                "agent_id": str(kwargs.get("agent_id") or "mock-agent"),
                "json_events": [{"item": {"text": json.dumps(opinion_data)}}],
            },
        }

    client_factory, _ = _make_mock_client(invoke_fn=slow_invoke)

    submit_resp, _ = _submit_interaction(bff_client, personas=("risk-analyst",))
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker_1 = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
        worker_id="concurrent-worker-1",
    )
    worker_2 = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory,
        worker_id="concurrent-worker-2",
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_1.run_once)
        f2 = executor.submit(worker_2.run_once)
        concurrent.futures.wait([f1, f2], timeout=5.0)

    # Exactly ONE provider invocation should have occurred
    assert len(call_log) == 1

    detail = bff_main.interaction_lifecycle.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail["status"] == "completed"
    assert len(detail["opinions"]) == 1
    assert len(detail["provider_invocations"]) == 1


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


def test_stale_lease_holder_finish_invocation_fenced_after_reclaim():
    store = InteractionLifecycleStore()
    interaction_id = "ix-stale-invoke-test-1"
    req_body = {
        "interaction_id": interaction_id,
        "tenant_id": "pantheon-dev",
        "owner_user_id": "interaction-user",
        "workshop_id": "ws-1",
        "human_request": {"operator_id": "interaction-user", "request_text": "Stale invoke test"},
        "created_at": "2026-08-27T00:00:00Z",
        "updated_at": "2026-08-27T00:00:00Z",
        "status": "queued",
    }
    store.create_request(
        req_body,
        idempotency_scope="pantheon-dev:interaction-user",
        idempotency_key="key-stale-invoke-1",
        fingerprint="fp-stale-invoke-1",
        trace_id="tr-stale-invoke-1",
    )
    invocation = {
        "invocation_id": "inv-stale-invoke-1",
        "interaction_id": interaction_id,
        "participant": {"persona_id": "risk-analyst", "display_name": "Risk Analyst"},
        "provider_kind": "openclaw",
        "status": "running",
    }

    # Worker A claims invocation with 1s lease
    first_row, first_claimed = store.claim_invocation(
        interaction_id, invocation, lease_owner="worker-A", lease_duration_seconds=1
    )
    assert first_claimed is True
    assert first_row["lease_owner"] == "worker-A"
    assert first_row["attempt"] == 1

    # Simulate lease expiry for Worker A
    with store._lock:
        store._invocations[interaction_id]["inv-stale-invoke-1"]["lease_until"] = "2020-01-01T00:00:00Z"

    # Worker B reclaims invocation
    second_row, second_claimed = store.claim_invocation(
        interaction_id, invocation, lease_owner="worker-B", lease_duration_seconds=300
    )
    assert second_claimed is True
    assert second_row["lease_owner"] == "worker-B"
    assert second_row["attempt"] == 2

    # Stale Worker A attempts to finish invocation with a failure
    failed_A = {
        **invocation,
        "status": "failed",
        "completed_at": "2026-08-27T00:00:05Z",
        "error": {"code": "stale_error", "retryable": False},
    }
    applied_A = store.finish_invocation(
        interaction_id,
        invocation=failed_A,
        opinion=None,
        error=failed_A["error"],
        outbox=[],
        lease_owner="worker-A",
    )
    assert applied_A is False

    # Invocation in store must still be running under Worker B
    with store._lock:
        row_mid = store._invocations[interaction_id]["inv-stale-invoke-1"]
        assert row_mid["status"] == "running"
        assert row_mid["lease_owner"] == "worker-B"
        assert row_mid["attempt"] == 2

    # Worker B finishes invocation successfully
    succeeded_B = {
        **invocation,
        "status": "succeeded",
        "completed_at": "2026-08-27T00:00:06Z",
    }
    opinion_B = {
        "opinion_id": "opn-B-1",
        "conclusion": "support",
        "rationale": "Worker B analysis succeeded",
        "confidence": 0.9,
    }
    applied_B = store.finish_invocation(
        interaction_id,
        invocation=succeeded_B,
        opinion=opinion_B,
        error=None,
        outbox=[],
        lease_owner="worker-B",
    )
    assert applied_B is True

    # State now reflects Worker B's work
    detail = store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail is not None
    assert detail["provider_invocations"][0]["status"] == "succeeded"
    assert detail["opinions"][0]["opinion_id"] == "opn-B-1"


def test_stale_lease_holder_finalize_fenced_after_reclaim():
    store = InteractionLifecycleStore()
    interaction_id = "ix-stale-finalize-test-1"
    req_body = {
        "interaction_id": interaction_id,
        "tenant_id": "pantheon-dev",
        "owner_user_id": "interaction-user",
        "workshop_id": "ws-1",
        "human_request": {"operator_id": "interaction-user", "request_text": "Stale finalize test"},
        "created_at": "2026-08-27T00:00:00Z",
        "updated_at": "2026-08-27T00:00:00Z",
        "status": "queued",
    }
    store.create_request(
        req_body,
        idempotency_scope="pantheon-dev:interaction-user",
        idempotency_key="key-stale-finalize-1",
        fingerprint="fp-stale-finalize-1",
        trace_id="tr-stale-finalize-1",
    )

    # Worker A claims interaction
    claimed_A = store.claim_interaction(
        lease_owner="worker-A",
        lease_duration_seconds=1,
        interaction_id=interaction_id,
    )
    assert claimed_A is not None
    assert claimed_A["lease_owner"] == "worker-A"
    assert claimed_A["status"] == "running"

    # Expire Worker A lease
    with store._lock:
        store._requests[interaction_id]["lease_until"] = "2020-01-01T00:00:00Z"

    # Worker B reclaims interaction
    claimed_B = store.claim_interaction(
        lease_owner="worker-B",
        lease_duration_seconds=300,
        interaction_id=interaction_id,
    )
    assert claimed_B is not None
    assert claimed_B["lease_owner"] == "worker-B"
    assert claimed_B["status"] == "running"

    # Stale Worker A attempts to finalize as failed
    applied_A = store.finalize(
        interaction_id,
        status="failed",
        synthesis=None,
        missing_participant_ids=["risk-analyst"],
        degraded_participant_ids=[],
        outbox=[],
        lease_owner="worker-A",
    )
    assert applied_A is False

    # Verify interaction is still running under Worker B
    detail_mid = store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail_mid is not None
    assert detail_mid["status"] == "running"
    assert detail_mid["lease_owner"] == "worker-B"

    # Worker B finalizes as completed
    synthesis_B = {
        "synthesis_id": "syn-B-1",
        "status": "recommendation",
        "summary": "Worker B completed synthesis",
        "opinion_ids": ["opn-B-1"],
    }
    applied_B = store.finalize(
        interaction_id,
        status="completed",
        synthesis=synthesis_B,
        missing_participant_ids=[],
        degraded_participant_ids=[],
        outbox=[],
        lease_owner="worker-B",
    )
    assert applied_B is True

    # Verify record is completed with Worker B synthesis and not failed
    final_detail = store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert final_detail is not None
    assert final_detail["status"] == "completed"
    assert final_detail["synthesis"]["summary"] == "Worker B completed synthesis"


def test_stale_worker_after_reclaim_preserves_new_owner_work(bff_client):
    """Full worker-level test reproducing A claiming a 1s lease, B reclaiming, and stale A finalizing."""
    barrier_A_started = threading.Event()
    barrier_B_done = threading.Event()

    calls_A = []
    calls_B = []

    def slow_invoke_A(*args, **kwargs):
        calls_A.append(kwargs)
        barrier_A_started.set()
        # Wait until Worker B has claimed and completed
        barrier_B_done.wait(timeout=5.0)
        # Return a failed or erroneous response from stale Worker A
        raise OpenClawOpsClientError("stale worker execution failed", status_code=500, error_code="STALE_WORKER_ERROR")

    def fast_invoke_B(*args, **kwargs):
        calls_B.append(kwargs)
        import json
        opinion_data = {
            "conclusion": "support",
            "rationale": "Worker B fast execution succeeded.",
            "confidence": 0.95,
            "uncertainty": [],
            "risks": [],
            "invalidation_conditions": [],
            "evidence_refs": [],
            "recommended_measures": [],
        }
        return {
            "status": "completed",
            "output": {
                "request_id": f"resp-worker-B-{uuid.uuid4().hex[:8]}",
                "agent_id": str(kwargs.get("agent_id") or "mock-agent"),
                "json_events": [{"item": {"text": json.dumps(opinion_data)}}],
            },
        }

    client_factory_A, _ = _make_mock_client(invoke_fn=slow_invoke_A)
    client_factory_B, _ = _make_mock_client(invoke_fn=fast_invoke_B)

    submit_resp, _ = _submit_interaction(bff_client, personas=("risk-analyst",))
    interaction_id = submit_resp.json()["data"]["interaction_id"]

    worker_A = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_A,
        worker_id="worker-A-stale",
        lease_duration_seconds=1,
    )
    worker_B = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=client_factory_B,
        worker_id="worker-B-reclaim",
        lease_duration_seconds=300,
    )

    thread_A_error = []
    def run_worker_A():
        try:
            worker_A.run_once(limit=1)
        except Exception as e:
            thread_A_error.append(e)

    thread_A = threading.Thread(target=run_worker_A, daemon=True)
    thread_A.start()

    # Wait until Worker A is inside its provider call
    assert barrier_A_started.wait(timeout=3.0)

    # Force Worker A lease to expire in the store
    store = bff_main.interaction_lifecycle
    with store._lock:
        store._requests[interaction_id]["lease_until"] = "2020-01-01T00:00:00Z"
        for inv_row in store._invocations.get(interaction_id, {}).values():
            inv_row["lease_until"] = "2020-01-01T00:00:00Z"

    # Worker B now claims and processes the interaction
    processed_B = worker_B.run_once(limit=1)
    assert processed_B == 1
    assert len(calls_B) == 1

    # Verify that Worker B set status to completed
    detail_B = store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert detail_B is not None
    assert detail_B["status"] == "completed"
    assert len(detail_B["opinions"]) == 1
    assert "Worker B fast execution succeeded." in detail_B["opinions"][0]["rationale"]

    # Release Worker A to resume and try to finalize
    barrier_B_done.set()
    thread_A.join(timeout=3.0)

    # Verify that record remains completed and was NOT overwritten to failed by stale Worker A
    final_detail = store.get(interaction_id, "pantheon-dev", "interaction-user")
    assert final_detail is not None
    assert final_detail["status"] == "completed"
    assert len(final_detail["opinions"]) == 1
    assert "Worker B fast execution succeeded." in final_detail["opinions"][0]["rationale"]
    assert final_detail["synthesis"]["status"] == "recommendation"
