"""Controller tests for approved registry discovery and tenant binding."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.research.alpha_replication.admission import (
    ReplicationAdmissionStore,
)
from services.research.alpha_replication.controller_state import (
    ControllerState,
    ControllerStateStore,
)
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.replication_controller import (
    ReplicationControllerConfig,
    _queue_payload_from_registry_entry,
    run_controller_tick,
)
from services.research.experiment_orchestrator.authority import (
    ResearchAuthorityHttpClient,
)
from services.research.experiment_orchestrator.test_authority import (
    FastApiTransport,
    _load_research_service_module,
)
from services.research.alpha_replication.test_revalidation_worker import (
    FakeAuthority,
    FakeGateResponse,
    _queue_payload,
    _registry_entry,
)


class CaptureLoopWriter:
    def __init__(self) -> None:
        self.successes: list[dict] = []
        self.failures: list[dict] = []

    async def record_success(self, **payload) -> None:
        self.successes.append(payload)

    async def record_failure(self, **payload) -> None:
        self.failures.append(payload)


def _state(path: Path, *, tenant_id: str = "tenant-a"):
    state = ControllerState(
        controller_id="test-controller",
        controller_name="alpha-replication-controller",
        environment="test",
        tenant_id=tenant_id,
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(path)
    store.save(state)
    return state, store


def _config(
    tmp_path: Path,
    *,
    seed_store_path: Path,
    authority: FakeAuthority,
    admission_store: ReplicationAdmissionStore | None = None,
) -> ReplicationControllerConfig:
    store = admission_store or ReplicationAdmissionStore(tmp_path)
    return ReplicationControllerConfig(
        database_url="postgresql://test",
        registry_url="http://registry.test",
        interval_seconds=10,
        max_ticks=1,
        state_path=tmp_path / "state.json",
        data_dir=tmp_path,
        seed_store_path=seed_store_path,
        authority=authority,
        admission_store=store,
    )


def test_registry_entry_normalization_requires_review_and_matching_tenant(
    tmp_path,
) -> None:
    payload = _queue_payload()
    entry = _registry_entry(payload)
    normalized = _queue_payload_from_registry_entry(entry, tenant_id="tenant-a")
    assert normalized == payload

    unapproved = dict(entry)
    unapproved["artifact_state"] = "candidate"
    with pytest.raises(ValueError, match="only approved"):
        _queue_payload_from_registry_entry(unapproved, tenant_id="tenant-a")

    missing_decision = dict(entry)
    missing_decision["approval_decision_id"] = None
    # Normalization preserves the missing value; queue admission is the final
    # shared guard and rejects it.
    queue = AlphaReplicationQueue(tmp_path)
    with pytest.raises(ValueError, match="approval_decision_id"):
        queue.enqueue(
            _queue_payload_from_registry_entry(
                missing_decision,
                tenant_id="tenant-a",
            )
        )

    with pytest.raises(ValueError, match="does not match controller"):
        _queue_payload_from_registry_entry(entry, tenant_id="tenant-b")


def test_controller_discovers_canonical_id_and_reads_authority_before_success(
    tmp_path,
) -> None:
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-alpha"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state, state_store = _state(tmp_path / "state.json")
    payload = _queue_payload()
    entry = _registry_entry(payload)
    admission_store = ReplicationAdmissionStore(tmp_path)
    admission_store.create_admission(
        {
            "tenant_id": "tenant-a",
            "strategy_spec_id": payload["strategy_spec_id"],
            "strategy_id": payload["strategy_id"],
            "spec_version": payload["spec_version"],
            "checksum": payload["checksum"],
            "approval_decision_id": payload["approval_decision_id"],
            "approver": payload["approver"],
        }
    )
    config = _config(tmp_path, seed_store_path=seed_path, authority=authority, admission_store=admission_store)
    writer = CaptureLoopWriter()
    stale_local_run_path = tmp_path / "alpha_revalidation_runs.jsonl"
    stale_local_run_path.write_text('{"legacy": true}\n', encoding="utf-8")

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[entry],
    ), mock.patch(
        "services.research.alpha_replication.revalidation_worker.AlphaRevalidationWorker._fetch_strategy_spec_entry",
        return_value=entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "replication passed"),
    ):
        result = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=writer,
        )

    assert result["total_successes"] == 1
    assert result["desired_state"] == {
        "approved_spec_count": 1,
        "strategy_spec_ids": [payload["strategy_spec_id"]],
        "tenant_id": "tenant-a",
        "seed_lineage_count": 1,
    }
    assert result["reconcile"]["enqueued_new"] == 1
    assert len(result["reconcile"]["created_run_ids"]) == 1
    assert result["actual_readback"]["queue_revalidated"] == 1
    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1
    task = next(iter(authority.tasks.values()))
    run = next(iter(authority.runs.values()))
    authority_task_id = f"rtask:{task.task_id}"
    authority_run_id = f"rrun:{run.run_id}"
    assert result["reconcile"]["created_authority_task_ids"] == [
        authority_task_id
    ]
    assert result["reconcile"]["created_authority_run_ids"] == [
        authority_run_id
    ]
    assert result["reconcile"]["created_run_ids"] == [authority_run_id]
    assert writer.failures == []
    assert len(writer.successes) == 1
    assert writer.successes[0]["evidence_refs"] == [
        f"research-authority://experiment-tasks/{authority_task_id}",
        f"research-authority://experiment-runs/{authority_run_id}",
    ]
    assert str(stale_local_run_path) not in writer.successes[0]["evidence_refs"]

    queued = AlphaReplicationQueue(tmp_path).list_all()[0]
    assert queued["tenant_id"] == "tenant-a"
    assert queued["strategy_spec_id"] == payload["strategy_spec_id"]
    assert queued["status"] == "completed"
    assert queued["authority_task_id"] == authority_task_id
    assert queued["authority_run_ids"] == [authority_run_id]
    assert queued["experiment_task_id"] == task.task_id
    assert queued["experiment_run_ids"] == [run.run_id]


def test_controller_evidence_refs_dereference_real_fastapi_authority(
    tmp_path,
) -> None:
    service_module = _load_research_service_module()
    service_client = TestClient(service_module.app)
    authority = ResearchAuthorityHttpClient(
        "http://research-orchestrator.test",
        transport=FastApiTransport(service_client),
    )
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-alpha"}) + "\n",
        encoding="utf-8",
    )
    state, state_store = _state(tmp_path / "state.json")
    payload = _queue_payload()
    entry = _registry_entry(payload)
    admission_store = ReplicationAdmissionStore(tmp_path)
    admission_store.create_admission(
        {
            "tenant_id": "tenant-a",
            "strategy_spec_id": payload["strategy_spec_id"],
            "strategy_id": payload["strategy_id"],
            "spec_version": payload["spec_version"],
            "checksum": payload["checksum"],
            "approval_decision_id": payload["approval_decision_id"],
            "approver": payload["approver"],
        }
    )
    config = _config(tmp_path, seed_store_path=seed_path, authority=authority, admission_store=admission_store)
    writer = CaptureLoopWriter()

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[entry],
    ), mock.patch(
        "services.research.alpha_replication.revalidation_worker.AlphaRevalidationWorker._fetch_strategy_spec_entry",
        return_value=entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "replication passed"),
    ):
        result = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=writer,
        )

    [receipt] = result["reconcile"]["authority_receipts"]
    evidence_refs = writer.successes[0]["evidence_refs"]
    assert evidence_refs == [
        (
            "research-authority://experiment-tasks/"
            f"{receipt['authority_task_id']}"
        ),
        (
            "research-authority://experiment-runs/"
            f"{receipt['authority_run_id']}"
        ),
    ]
    assert receipt["authority_task_id"] != receipt["experiment_task_id"]
    assert receipt["authority_run_id"] != receipt["experiment_run_id"]

    ref_routes = {
        "research-authority://experiment-tasks/": (
            "/api/research-orchestrator/tasks/",
            "task_id",
        ),
        "research-authority://experiment-runs/": (
            "/api/research-orchestrator/runs/",
            "run_id",
        ),
    }
    for evidence_ref in evidence_refs:
        prefix = next(
            candidate
            for candidate in ref_routes
            if evidence_ref.startswith(candidate)
        )
        route_prefix, identity_field = ref_routes[prefix]
        authority_id = evidence_ref.removeprefix(prefix)
        response = service_client.get(f"{route_prefix}{authority_id}")
        assert response.status_code == 200
        assert response.json()[identity_field] == authority_id

    queued = AlphaReplicationQueue(tmp_path).list_all()[0]
    assert queued["authority_task_id"] == receipt["authority_task_id"]
    assert queued["authority_run_ids"] == [receipt["authority_run_id"]]


def test_controller_duplicate_discovery_and_restart_converge_once(tmp_path) -> None:
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-alpha"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state, state_store = _state(tmp_path / "state.json")
    entry = _registry_entry()
    admission_store = ReplicationAdmissionStore(tmp_path)
    admission_store.create_admission(
        {
            "tenant_id": "tenant-a",
            "strategy_spec_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "spec_version": entry["version"],
            "checksum": entry["checksum"],
            "approval_decision_id": entry["approval_decision_id"],
            "approver": entry["approver"],
        }
    )
    config = _config(tmp_path, seed_store_path=seed_path, authority=authority, admission_store=admission_store)

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        return_value=[entry],
    ), mock.patch(
        "services.research.alpha_replication.revalidation_worker.AlphaRevalidationWorker._fetch_strategy_spec_entry",
        return_value=entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "replication passed"),
    ):
        first = run_controller_tick(
            config=config,
            state=state,
            store=state_store,
            writer=None,
        )
        restarted_state = state_store.load()
        assert restarted_state is not None
        second = run_controller_tick(
            config=config,
            state=restarted_state,
            store=state_store,
            writer=None,
        )

    assert first["reconcile"]["enqueued_new"] == 1
    assert second["reconcile"]["enqueued_new"] == 0
    assert second["reconcile"]["processed"] == 0
    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1
    assert len(AlphaReplicationQueue(tmp_path).list_all()) == 1


def test_controller_registry_failure_is_durable_and_fail_closed(tmp_path) -> None:
    seed_path = tmp_path / "distill_seeds.jsonl"
    seed_path.write_text(
        json.dumps({"source_id": "strat-alpha"}) + "\n",
        encoding="utf-8",
    )
    authority = FakeAuthority()
    state, state_store = _state(tmp_path / "state.json")
    admission_store = ReplicationAdmissionStore(tmp_path)
    admission_store.create_admission(
        {
            "tenant_id": "tenant-a",
            "strategy_spec_id": "spec-fail-001",
            "strategy_id": "strat-alpha",
            "spec_version": "1.0.0",
            "checksum": "sha256:fail",
            "approval_decision_id": "app-fail",
            "approver": "approver-fail",
        }
    )
    config = _config(tmp_path, seed_store_path=seed_path, authority=authority, admission_store=admission_store)

    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_approved_specs_for_strategy",
        side_effect=RuntimeError("registry unavailable"),
    ):
        with pytest.raises(RuntimeError, match="Registry lookup failed"):
            run_controller_tick(
                config=config,
                state=state,
                store=state_store,
                writer=None,
            )

    persisted = state_store.load()
    assert persisted is not None
    assert persisted.total_failures == 1
    assert persisted.consecutive_failures == 1
    assert "registry unavailable" in str(persisted.last_failure_reason)
    assert authority.tasks == {}
    assert authority.runs == {}
