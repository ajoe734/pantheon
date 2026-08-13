"""Product V2 Research Alpha R3 Integration and Acceptance Suite.

Verifies end-to-end strategy-to-alpha replication execution:
1. Promoted StrategySpec consumption via real service boundaries
2. Durable replication & revalidation to terminal ExperimentRun
3. Terminal experiment & authority identifier readback by downstream surfaces
4. Idempotent duplicate submission and lease-retry behavior
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from services.research.alpha_replication.controller_state import (
    ControllerState,
    ControllerStateStore,
)
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.replication_controller import (
    ReplicationControllerConfig,
    run_controller_tick,
)
from services.research.alpha_replication.revalidation_worker import (
    AlphaRevalidationWorker,
)
from services.research.alpha_replication.test_replication_controller import (
    CaptureLoopWriter,
)
from services.research.alpha_replication.test_revalidation_worker import (
    FakeAuthority,
    FakeGateResponse,
    _queue_payload,
    _registry_entry,
)


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _setup_controller_env(tmp_path: Path, tenant_id: str = "tenant-prod-v2"):
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-prod-v2-r3-001"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state = ControllerState(
        controller_id="controller-r3-test",
        controller_name="alpha-replication-controller",
        environment="test",
        tenant_id=tenant_id,
        deployment={"git_sha": "git-sha-r3-test"},
    )
    state_store = ControllerStateStore(tmp_path / "state.json")
    state_store.save(state)

    config = ReplicationControllerConfig(
        database_url="postgresql://test",
        registry_url="http://registry.test",
        interval_seconds=10,
        max_ticks=1,
        state_path=tmp_path / "state.json",
        data_dir=tmp_path,
        seed_store_path=seed_path,
        authority=authority,
    )
    return config, state, state_store, authority


def test_product_v2_research_alpha_r3_end_to_end_flow(tmp_path: Path) -> None:
    tenant_id = "tenant-prod-v2"
    strategy_spec_id = "reg-strategy-spec-r3-alpha-1.0.0"
    strategy_id = "strat-prod-v2-r3-001"

    config, state, state_store, authority = _setup_controller_env(tmp_path, tenant_id=tenant_id)
    writer = CaptureLoopWriter()

    payload = _queue_payload(
        tenant_id=tenant_id,
        strategy_spec_id=strategy_spec_id,
        strategy_id=strategy_id,
    )
    registry_entry = _registry_entry(payload)

    # 1. Run controller tick to discover approved spec and reconcile state
    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[registry_entry],
    ), mock.patch(
        "services.research.alpha_replication.revalidation_worker.AlphaRevalidationWorker._fetch_strategy_spec_entry",
        return_value=registry_entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "alpha revalidation gate passed for R3"),
    ):
        result = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=writer,
        )

    # 2. Verify controller tick metrics and reconcile output
    assert result["total_successes"] == 1
    reconcile = result["reconcile"]
    assert reconcile["enqueued_new"] == 1
    assert reconcile["processed"] == 1
    assert len(reconcile["created_authority_task_ids"]) == 1
    assert len(reconcile["created_authority_run_ids"]) == 1

    auth_task_id = reconcile["created_authority_task_ids"][0]
    auth_run_id = reconcile["created_authority_run_ids"][0]
    exp_task_id = reconcile["created_experiment_task_ids"][0]
    exp_run_id = reconcile["created_experiment_run_ids"][0]

    # 3. Expose terminal experiment and authority identifiers to downstream surfaces via get_entry
    queue = AlphaReplicationQueue(tmp_path)
    entry = queue.get_entry(tenant_id, strategy_spec_id)
    assert entry is not None
    assert entry["status"] == "completed"
    assert entry["last_revalidation_status"] == "completed"
    assert entry["authority_task_id"] == auth_task_id
    assert auth_run_id in entry["authority_run_ids"]
    assert entry["experiment_task_id"] == exp_task_id
    assert exp_run_id in entry["experiment_run_ids"]

    # 4. Readback runs via AlphaRevalidationWorker.list_runs
    worker = AlphaRevalidationWorker(
        queue=queue,
        data_dir=tmp_path,
        authority=authority,
        registry_url="http://registry.test",
    )
    runs = worker.list_runs(tenant_id=tenant_id, strategy_spec_id=strategy_spec_id)
    assert len(runs) == 1
    assert runs[0]["run_id"] == exp_run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["tenant_id"] == tenant_id
    assert runs[0]["strategy_spec_id"] == strategy_spec_id


    # 5. Prove duplicate enqueuing is idempotent
    assert queue.enqueue(payload) is None

    # 6. Verify loop writer received evidence references
    assert len(writer.successes) == 1
    evidence_refs = writer.successes[0]["evidence_refs"]
    assert f"research-authority://experiment-tasks/{auth_task_id}" in evidence_refs
    assert f"research-authority://experiment-runs/{auth_run_id}" in evidence_refs


def test_idempotent_lease_recovery_and_fencing_token_protection(tmp_path: Path) -> None:
    tenant_id = "tenant-prod-v2"
    strategy_spec_id = "reg-strategy-spec-r3-lease-1.0.0"

    queue = AlphaReplicationQueue(tmp_path)
    payload = _queue_payload(
        tenant_id=tenant_id,
        strategy_spec_id=strategy_spec_id,
        strategy_id="strat-lease-001",
    )
    queue.enqueue(payload, now=NOW)

    # Claim with short lease at NOW
    claimed = queue.claim_next_pending(
        tenant_id,
        claimant="worker-stale",
        lease_seconds=10,
        now=NOW,
    )
    assert claimed is not None
    stale_token = claimed["claim_token"]

    # Reclaim expired claim at NOW + 20 seconds
    later = NOW + timedelta(seconds=20)
    recovered_count = queue.recover_expired_claims(tenant_id, now=later)
    assert recovered_count == 1

    # Stale worker tries to mark revalidated with stale token -> rejected
    stale_ok = queue.mark_revalidated(
        tenant_id,
        strategy_spec_id,
        claim_token=stale_token,
        authority_task_id="atask-stale",
        authority_run_id="arun-stale",
        experiment_task_id="etask-stale",
        experiment_run_id="erun-stale",
        now=later,
    )
    assert stale_ok is False

    # New worker claims re-opened pending work at NOW + 21 seconds
    even_later = NOW + timedelta(seconds=21)
    new_claim = queue.claim_next_pending(
        tenant_id,
        claimant="worker-fresh",
        lease_seconds=60,
        now=even_later,
    )
    assert new_claim is not None
    fresh_token = new_claim["claim_token"]
    assert fresh_token != stale_token

    # Fresh worker marks revalidated -> accepted
    fresh_ok = queue.mark_revalidated(
        tenant_id,
        strategy_spec_id,
        claim_token=fresh_token,
        authority_task_id="atask-fresh",
        authority_run_id="arun-fresh",
        experiment_task_id="etask-fresh",
        experiment_run_id="erun-fresh",
        now=even_later + timedelta(seconds=1),
    )
    assert fresh_ok is True

    final_entry = queue.get_entry(tenant_id, strategy_spec_id)
    assert final_entry is not None
    assert final_entry["status"] == "completed"
    assert final_entry["authority_task_id"] == "atask-fresh"
