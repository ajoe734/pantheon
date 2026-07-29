"""Behavioral proof for authoritative Alpha revalidation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from services.research.experiment_orchestrator.authority import (
    AuthoritativeRunReceipt,
    AuthoritativeTaskReceipt,
)
from services.research.experiments.models import ExperimentRun, ExperimentTask

from .queue import AlphaReplicationQueue
from .revalidation_worker import AlphaRevalidationWorker, SAFE_DISPATCH_MODES


def _queue_payload(
    *,
    tenant_id: str = "tenant-a",
    strategy_spec_id: str = "reg-strategy-spec-alpha-1.0.0",
    strategy_id: str = "strat-alpha",
) -> dict:
    return {
        "tenant_id": tenant_id,
        "strategy_spec_id": strategy_spec_id,
        "strategy_id": strategy_id,
        "spec_version": "1.0.0",
        "artifact_state": "approved",
        "checksum": f"sha256:{tenant_id}-{strategy_spec_id}",
        "approval_decision_id": f"approval:{tenant_id}:{strategy_spec_id}",
        "approver": "research-reviewer",
        "approved_at": "2026-07-26T09:00:00Z",
    }


def _strategy_spec(
    *,
    tenant_id: str = "tenant-a",
    strategy_id: str = "strat-alpha",
) -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Approved alpha replication strategy",
        "hypothesis": "A governed daily signal remains reproducible.",
        "objective": "Revalidate schema and governance constraints.",
        "lifecycle_state": "approved",
        "market_scope": {
            "symbols": ["SPY"],
            "asset_classes": ["equity"],
            "frequency": "1d",
            "venues": ["NYSE"],
        },
        "data_dependencies": [{"ref": "dataset:alpha-v1", "kind": "dataset"}],
        "code_refs": [
            {
                "repo_ref": "ajoe734/pantheon",
                "path": "services/research/alpha_replication",
                "commit": "git:alpha123",
            }
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "rebalance_cadence": "1d",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio"],
            "candidate_gate": "All required replication checks pass.",
            "paper_gate": "Separate paper review required.",
            "live_gate": "Separate live review required.",
        },
        "governance": {
            "approval_required": True,
            "policy_id": "policy-alpha",
            "risk_profile": "research_only",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": "2026-07-26T08:00:00Z",
            "source_refs": ["source:alpha"],
            "created_by": "Codex",
        },
    }


def _registry_entry(payload: dict | None = None, spec: dict | None = None) -> dict:
    queue_payload = payload or _queue_payload()
    strategy_spec = spec or _strategy_spec(
        tenant_id=queue_payload["tenant_id"],
        strategy_id=queue_payload["strategy_id"],
    )
    return {
        "registry_id": queue_payload["strategy_spec_id"],
        "artifact_type": "strategy_spec",
        "strategy_id": queue_payload["strategy_id"],
        "version": queue_payload["spec_version"],
        "artifact_state": "approved",
        "checksum": queue_payload["checksum"],
        "approval_decision_id": queue_payload["approval_decision_id"],
        "approver": queue_payload["approver"],
        "approved_at": queue_payload["approved_at"],
        "metadata": {
            "tenant_id": queue_payload["tenant_id"],
            "strategy_spec": strategy_spec,
        },
    }


class FakeAuthority:
    def __init__(self) -> None:
        self.tasks: dict[str, ExperimentTask] = {}
        self.runs: dict[str, ExperimentRun] = {}
        self.ensure_task_calls = 0
        self.ensure_run_calls = 0
        self.fail_run_write = False

    def ensure_task(
        self,
        task: ExperimentTask,
        *,
        approval_decision_id: str,
        approver: str,
        approved_at: str,
        checksum: str,
    ) -> AuthoritativeTaskReceipt:
        self.ensure_task_calls += 1
        existing = self.tasks.setdefault(task.idempotency_key, task)
        return AuthoritativeTaskReceipt(
            authority_task_id=f"rtask:{existing.task_id}",
            task=existing,
            record={
                "approval_decision_id": approval_decision_id,
                "approver": approver,
                "approved_at": approved_at,
                "checksum": checksum,
            },
        )

    def ensure_run(
        self,
        authority_task_id: str,
        run: ExperimentRun,
        *,
        approval_decision_id: str,
    ) -> AuthoritativeRunReceipt:
        self.ensure_run_calls += 1
        if self.fail_run_write:
            raise RuntimeError("research authority unavailable")
        key = str(run.metadata["idempotency_key"])
        existing = self.runs.setdefault(key, run)
        return AuthoritativeRunReceipt(
            authority_run_id=f"rrun:{existing.run_id}",
            run=existing,
            record={
                "task_id": authority_task_id,
                "approval_decision_id": approval_decision_id,
                "production_activation": "disabled",
            },
        )

    def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        strategy_spec_id: str | None = None,
    ) -> list[ExperimentRun]:
        runs = list(self.runs.values())
        if tenant_id is not None:
            runs = [run for run in runs if run.tenant_id == tenant_id]
        if strategy_spec_id is not None:
            runs = [
                run for run in runs if run.strategy_spec_id == strategy_spec_id
            ]
        return runs


@dataclass
class FakeGateResponse:
    passed: bool
    summary: str

    def to_dict(self) -> dict:
        return {
            "admission_status": "admitted" if self.passed else "rejected",
            "replication_status": "passed" if self.passed else "failed",
            "summary": self.summary,
        }


def _worker(tmp_path, authority: FakeAuthority, *, mode: str = "authoritative"):
    queue = AlphaReplicationQueue(tmp_path)
    worker = AlphaRevalidationWorker(
        queue,
        tmp_path,
        dispatch_mode=mode,
        authority=authority,
        registry_url="http://registry.test",
        lease_seconds=300,
    )
    return queue, worker


def _run_with_registry(
    worker: AlphaRevalidationWorker,
    registry_entry: dict,
    *,
    tenant_id: str = "tenant-a",
    gate_passed: bool = True,
):
    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=registry_entry,
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(
            passed=gate_passed,
            summary="replication passed" if gate_passed else "replication rejected",
        ),
    ):
        return worker.run_once(tenant_id=tenant_id)


def test_worker_rejects_stub_manual_and_execution_activation_modes(tmp_path) -> None:
    authority = FakeAuthority()
    queue = AlphaReplicationQueue(tmp_path)
    assert SAFE_DISPATCH_MODES == {"authoritative", "handoff_only"}
    for mode in ("stub", "manual", "paper", "canary", "live", "production"):
        with pytest.raises(ValueError, match="not authoritative"):
            AlphaRevalidationWorker(
                queue,
                tmp_path,
                dispatch_mode=mode,
                authority=authority,
            )


def test_handoff_only_config_alias_executes_authoritative_path(tmp_path) -> None:
    authority = FakeAuthority()
    _, worker = _worker(tmp_path, authority, mode="handoff_only")
    assert worker._configured_mode == "handoff_only"
    assert worker._dispatch_mode == "authoritative"

    with mock.patch.dict(
        os.environ,
        {"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE": "stub"},
    ):
        with pytest.raises(ValueError, match="not authoritative"):
            AlphaRevalidationWorker(
                AlphaReplicationQueue(tmp_path / "other"),
                tmp_path / "other",
                authority=authority,
            )


def test_approved_spec_creates_authoritative_task_and_completed_run(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    result = _run_with_registry(worker, _registry_entry(payload))

    assert result["processed"] == 1
    assert result["dispatch_mode"] == "authoritative"
    assert result["errors"] == []
    assert len(result["created_run_ids"]) == 1
    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1

    task = next(iter(authority.tasks.values()))
    run = next(iter(authority.runs.values()))
    assert task.tenant_id == payload["tenant_id"]
    assert task.strategy_spec_id == payload["strategy_spec_id"]
    assert run.status == "completed"
    assert run.backend_id == "replication_gate"
    assert run.tenant_id == task.tenant_id
    assert run.strategy_spec_id == task.strategy_spec_id
    assert run.metadata["production_activation"] == "disabled"
    authority_task_id = f"rtask:{task.task_id}"
    authority_run_id = f"rrun:{run.run_id}"
    assert result["created_run_ids"] == [authority_run_id]
    assert result["created_authority_task_ids"] == [authority_task_id]
    assert result["created_authority_run_ids"] == [authority_run_id]
    assert result["created_experiment_task_ids"] == [task.task_id]
    assert result["created_experiment_run_ids"] == [run.run_id]
    assert result["authority_receipts"] == [
        {
            "authority_task_id": authority_task_id,
            "authority_run_id": authority_run_id,
            "experiment_task_id": task.task_id,
            "experiment_run_id": run.run_id,
        }
    ]
    assert worker.list_runs(
        tenant_id=payload["tenant_id"],
        strategy_spec_id=payload["strategy_spec_id"],
    ) == [run.to_dict()]

    queued = queue.list_all()[0]
    assert queued["status"] == "completed"
    assert queued["authority_task_id"] == authority_task_id
    assert queued["authority_run_ids"] == [authority_run_id]
    assert queued["experiment_task_id"] == task.task_id
    assert queued["experiment_run_ids"] == [run.run_id]


def test_real_replication_gate_accepts_the_approved_canonical_spec(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    registry_entry = _registry_entry(payload)
    queue.enqueue(payload)

    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=registry_entry,
    ):
        result = worker.run_once(tenant_id="tenant-a")

    assert result["errors"] == []
    run = next(iter(authority.runs.values()))
    assert run.status == "completed"
    gate = run.metadata["replication_gate"]
    assert gate["admission_status"] == "admitted"
    assert gate["replication_status"] == "passed"


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("artifact_state", "retired"),
        ("checksum", "sha256:changed"),
        ("approval_decision_id", "approval:changed"),
        ("approver", "other-reviewer"),
        ("approved_at", "2026-07-26T09:01:00Z"),
    ],
)
def test_registry_recheck_rejects_stale_or_changed_review(
    tmp_path,
    field_name,
    changed_value,
) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)
    registry_entry = _registry_entry(payload)
    registry_entry[field_name] = changed_value

    result = _run_with_registry(worker, registry_entry)

    assert result["created_run_ids"] == []
    assert len(result["errors"]) == 1
    assert field_name in result["errors"][0]["error"]
    assert authority.tasks == {}
    entry = queue.list_all()[0]
    assert entry["status"] == "pending"
    assert entry["attempt_count"] == 1


def test_tenant_collision_isolated_across_authority_and_queue(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload_a = _queue_payload(tenant_id="tenant-a")
    payload_b = _queue_payload(tenant_id="tenant-b")
    queue.enqueue(payload_a)
    queue.enqueue(payload_b)

    result_a = _run_with_registry(
        worker,
        _registry_entry(payload_a),
        tenant_id="tenant-a",
    )
    result_b = _run_with_registry(
        worker,
        _registry_entry(payload_b),
        tenant_id="tenant-b",
    )

    assert len(result_a["created_run_ids"]) == 1
    assert len(result_b["created_run_ids"]) == 1
    assert len(authority.tasks) == 2
    assert len(authority.runs) == 2
    assert {run.tenant_id for run in authority.runs.values()} == {
        "tenant-a",
        "tenant-b",
    }


def test_crash_after_authority_write_reclaims_same_attempt_without_duplicate(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)
    claimed = queue.claim_next_pending(
        "tenant-a",
        claimant="crashing-worker",
        lease_seconds=1,
    )
    assert claimed is not None

    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=_registry_entry(payload),
    ), mock.patch(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        return_value=FakeGateResponse(True, "replication passed"),
    ):
        worker._process_entry(claimed, tick_at="2026-07-26T10:00:00Z")

    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1
    assert queue.list_all()[0]["status"] == "claimed"

    future = datetime.now(timezone.utc) + timedelta(seconds=301)
    assert queue.recover_expired_claims("tenant-a", now=future) == 1
    result = _run_with_registry(worker, _registry_entry(payload))

    assert len(result["created_run_ids"]) == 1
    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1
    assert authority.ensure_task_calls == 2
    assert authority.ensure_run_calls == 2
    assert queue.list_all()[0]["status"] == "completed"


def test_failure_to_dlq_and_operator_replay_create_one_new_generation(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    for _ in range(3):
        result = _run_with_registry(
            worker,
            _registry_entry(payload),
            gate_passed=False,
        )
        assert len(result["errors"]) == 1

    entry = queue.list_all()[0]
    assert entry["status"] == "dlq"
    assert entry["attempt_count"] == 3
    assert len(authority.runs) == 3

    assert worker.replay_dlq(
        "tenant-a",
        payload["strategy_spec_id"],
        replay_id="replay-alpha-001",
        replayed_by="operator-a",
        reason="reviewed failure repaired",
    )
    replay_result = _run_with_registry(worker, _registry_entry(payload))
    assert len(replay_result["created_run_ids"]) == 1
    assert len(authority.runs) == 4
    replayed = queue.list_all()[0]
    assert replayed["status"] == "completed"
    assert replayed["replay_count"] == 1


def test_authority_failure_is_retryable_and_never_creates_local_run_truth(tmp_path) -> None:
    authority = FakeAuthority()
    authority.fail_run_write = True
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    failed = _run_with_registry(worker, _registry_entry(payload))
    assert failed["created_run_ids"] == []
    assert "research authority unavailable" in failed["errors"][0]["error"]
    assert not (tmp_path / "alpha_revalidation_runs.jsonl").exists()
    failed_entry = queue.list_all()[0]
    assert failed_entry["status"] == "pending"
    assert failed_entry["authority_task_id"].startswith("rtask:")
    assert failed_entry["authority_run_ids"] == []

    authority.fail_run_write = False
    recovered = _run_with_registry(worker, _registry_entry(payload))
    assert len(recovered["created_run_ids"]) == 1
    assert queue.list_all()[0]["status"] == "completed"


def test_metrics_persist_without_becoming_run_authority(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)
    _run_with_registry(worker, _registry_entry(payload))

    restarted = AlphaRevalidationWorker(
        AlphaReplicationQueue(tmp_path),
        tmp_path,
        authority=authority,
        registry_url="http://registry.test",
    )
    metrics = restarted.get_metrics()
    assert metrics["run_count"] == 1
    assert metrics["last_success_at"] is not None
    assert metrics["last_run_strategy_spec_ids"] == [payload["strategy_spec_id"]]
