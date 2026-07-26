"""Acceptance tests for the L12-DEP-001 deployment dispatcher lease."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import MagicMock

import pytest

from services.deployment.outbox_lease import (
    DeploymentOutboxLeaseStore,
    OutboxLeaseError,
)
from services.deployment.runtime_manager_dispatch_adapter import (
    DispatchOutcome,
    dispatch_to_runtime_manager,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _outbox_record() -> dict:
    return {
        "owner_service": "deployment",
        "event": {
            "event_id": "event-binding-requested",
            "event_type": "runtime.binding.requested",
            "aggregate_type": "deployment_saga",
            "aggregate_id": "saga-plan-001",
            "sequence_no": 1,
            "trace_id": "trace-deployment-001",
            "idempotency_key": "deployment:saga-plan-001:1",
            "payload": {},
        },
        "status": "pending",
    }


def _authority_report() -> dict:
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": "plan-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "strategy_id": "strategy-001",
        "approval_decision_id": "approval-001",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "deployment_plan_current_stage": "none",
        "deployment_plan_binding_id": None,
        "deployment_plan_runtime_lifecycle": {},
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "deployment_plan_authority_sha256": "sha256:" + "a" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }


def _saga(*, binding_id: str | None = None) -> dict:
    return {
        "saga_id": "saga-plan-001",
        "plan_id": "plan-001",
        "approval_decision_id": "approval-001",
        "strategy_id": "strategy-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-001",
        "current_stage": "none",
        "target_stage": "paper",
        "runtime_action": "deploy_new_binding",
        "status": "awaiting_binding" if binding_id is None else "awaiting_runtime_load",
        "binding_id": binding_id,
    }


def _deploy_context() -> dict:
    return {
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "plan_status": "approved",
        "metadata": {
            "tenant_id": "tenant-a",
            "deployment_correlation_id": "correlation-deployment-001",
            "authoritative_loader_attestation": _authority_report(),
        },
    }


def _binding() -> dict:
    return {
        "binding_id": "binding-001",
        "plan_id": "plan-001",
        "runtime_id": "runtime-001",
        "capital_pool_id": "pool-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "deployment_mode": "paper",
        "execution_mode": "paper",
        "persona_capital_binding_id": "pcb-001",
        "status": "active",
        "metadata": {
            "strategy_id": "strategy-001",
            "tenant_id": "tenant-a",
            "deployment_correlation_id": "correlation-deployment-001",
            "authoritative_loader_attestation": _authority_report(),
        },
    }


def test_concurrent_consumers_cannot_claim_the_same_event(tmp_path) -> None:
    clock = MutableClock()
    storage_path = tmp_path / "deployment_outbox_leases.json"
    ready = Barrier(2)

    def claim(consumer_name: str) -> list[dict]:
        store = DeploymentOutboxLeaseStore(storage_path, clock=clock)
        ready.wait()
        return store.claim(
            [_outbox_record()],
            tenant_id="tenant-a",
            consumer_name=consumer_name,
            lease_seconds=30,
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("consumer-a", "consumer-b")))

    assert sorted(len(records) for records in claims) == [0, 1]
    winner = next(records[0] for records in claims if records)
    assert winner["status"] == "pending"
    assert winner["lease_status"] == "active"
    health = DeploymentOutboxLeaseStore(storage_path, clock=clock).health()
    assert health["active_claim_count"] == 1
    assert health["recovered_claim_count"] == 0


def test_idle_timeout_reclaims_lease_and_rejects_stale_ack(tmp_path) -> None:
    clock = MutableClock()
    store = DeploymentOutboxLeaseStore(
        tmp_path / "deployment_outbox_leases.json",
        clock=clock,
    )
    first = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-a",
        lease_seconds=30,
        limit=1,
    )[0]

    clock.advance(31)
    second = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
        lease_seconds=30,
        limit=1,
    )[0]

    assert second["claim_token"] != first["claim_token"]
    assert second["recovery_count"] == 1
    with pytest.raises(OutboxLeaseError, match="not owned by this caller"):
        store.acknowledge(
            event_id="event-binding-requested",
            claim_token=first["claim_token"],
            tenant_id="tenant-a",
            consumer_name="consumer-a",
        )
    store.acknowledge(
        event_id="event-binding-requested",
        claim_token=second["claim_token"],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
    )
    health = store.health()
    assert health["active_claim_count"] == 0
    assert health["acknowledged_claim_count"] == 1
    assert health["recovered_claim_count"] == 1


def test_crash_after_runtime_binding_side_effect_recovers_without_duplicate(
    tmp_path,
) -> None:
    clock = MutableClock()
    store = DeploymentOutboxLeaseStore(
        tmp_path / "deployment_outbox_leases.json",
        clock=clock,
    )
    first_claim = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-a",
        lease_seconds=30,
        limit=1,
    )[0]

    binding = _binding()
    client = MagicMock()
    client.deploy.return_value = binding
    client.get.return_value = binding

    first_dispatch = dispatch_to_runtime_manager(
        saga=_saga(),
        deploy_context=_deploy_context(),
        client=client,
    )
    assert first_dispatch.outcome == DispatchOutcome.SUCCESS
    assert first_dispatch.binding_id == "binding-001"
    assert client.deploy.call_count == 1

    # Simulate a process crash after RuntimeBinding and saga state committed,
    # but before the original outbox claim could be acknowledged.
    clock.advance(31)
    recovered_claim = store.claim(
        [_outbox_record()],
        tenant_id="tenant-a",
        consumer_name="consumer-b",
        lease_seconds=30,
        limit=1,
    )[0]
    assert recovered_claim["claim_token"] != first_claim["claim_token"]
    assert recovered_claim["recovery_count"] == 1

    replay = dispatch_to_runtime_manager(
        saga=_saga(binding_id="binding-001"),
        deploy_context=_deploy_context(),
        client=client,
    )

    assert replay.outcome == DispatchOutcome.SUCCESS
    assert replay.idempotent_replay is True
    assert client.deploy.call_count == 1
    assert client.get.call_count == 2
