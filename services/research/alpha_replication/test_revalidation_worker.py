"""Behavioral proof for authoritative Alpha revalidation."""

from __future__ import annotations

import json
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
        self.teaching_sessions: dict[str, dict[str, Any]] = {}
        self.ensure_task_calls = 0
        self.ensure_run_calls = 0
        self.create_teaching_session_calls = 0
        self.get_teaching_session_calls = 0
        self.fail_run_write = False
        self.fail_teaching_write = False
        self.fail_teaching_readback = False

    def create_teaching_session(
        self,
        payload: dict[str, Any],
        *,
        tenant_id: str,
    ) -> dict[str, Any]:
        self.create_teaching_session_calls += 1
        if self.fail_teaching_write:
            raise RuntimeError("teaching service write failure")
        session_id = f"trn-{len(self.teaching_sessions) + 1:04d}"
        session = {
            "session_id": session_id,
            "id": session_id,
            "persona_id": payload.get("persona_id", "persona-test"),
            "tenant_id": tenant_id,
            "objective": payload.get("objective", ""),
            "mode": payload.get("mode", "evaluation"),
            "status": "active",
            "context_refs": list(payload.get("context_refs") or []),
            "trace_id": payload.get("trace_id", f"trace-{session_id}"),
            "actor_id": payload.get("actor_id", "test-actor"),
        }
        self.teaching_sessions[session_id] = session
        return dict(session)

    def get_teaching_session(
        self,
        session_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any]:
        self.get_teaching_session_calls += 1
        if self.fail_teaching_readback:
            raise RuntimeError(f"teaching service readback failed for {session_id}")
        session = self.teaching_sessions.get(session_id)
        if not session:
            raise RuntimeError(f"teaching session not found: {session_id}")
        return dict(session)

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
    session_id = "trn-0001"
    trigger_id = str(
        payload.get("admission_id")
        or payload.get("trigger_id")
        or payload.get("approval_decision_id")
        or payload["strategy_spec_id"]
    )
    assert result["created_run_ids"] == [authority_run_id]
    assert result["created_authority_task_ids"] == [authority_task_id]
    assert result["created_authority_run_ids"] == [authority_run_id]
    assert result["created_experiment_task_ids"] == [task.task_id]
    assert result["created_experiment_run_ids"] == [run.run_id]
    assert result["created_teaching_session_ids"] == [session_id]
    assert result["next_consumer_receipt_ids"] == [session_id]
    assert result["authority_receipts"] == [
        {
            "authority_task_id": authority_task_id,
            "authority_run_id": authority_run_id,
            "experiment_task_id": task.task_id,
            "experiment_run_id": run.run_id,
            "next_consumer_receipt_id": session_id,
            "teaching_session_id": session_id,
            "trigger_id": trigger_id,
            "terminal_output_id": run.run_id,
            "owner_worker_identity": "alpha-revalidation-worker",
        }
    ]
    loop_record = worker.get_loop_record(
        payload["tenant_id"], payload["strategy_spec_id"]
    )
    assert loop_record is not None
    assert loop_record["loop_id"] == "alpha_replication"
    assert loop_record["loop_index"] == 3
    assert loop_record["trigger_id"] == trigger_id
    assert loop_record["terminal_output_id"] == run.run_id
    assert loop_record["next_consumer_receipt_id"] == session_id
    assert loop_record["owner_worker_identity"] == "alpha-revalidation-worker"
    assert loop_record["durable_reload_readback"]["session_id"] == session_id
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


def test_alpha_revalidation_fetch_strategy_spec_authorization_and_rotation(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)

    token_file = tmp_path / "reval_token"
    token_file.write_text("token-reval-1\n", encoding="utf-8")
    token_file.chmod(0o600)

    monkeypatch.setenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN", "stale-env-token")

    captured_requests = []

    def fake_urlopen(req, timeout=10):
        captured_requests.append(req)
        resp = MagicMock()
        resp.read.return_value = b'{"entry": {"strategy_spec_id": "spec-1"}}'
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # 1. Exact GET request with token-reval-1
    res1 = worker._fetch_strategy_spec_entry("spec-1")
    assert res1 == {"strategy_spec_id": "spec-1"}
    req1 = captured_requests[-1]
    assert req1.get_method() == "GET"
    assert req1.get_header("Authorization") == "Bearer token-reval-1"
    assert req1.full_url == f"{worker._registry_url}/api/registry/strategy-specs/spec-1"

    # 2. Rotate token in file: next call immediately sees rotated token
    token_file.write_text("token-reval-2\n", encoding="utf-8")

    res2 = worker._fetch_strategy_spec_entry("spec-1")
    assert res2 == {"strategy_spec_id": "spec-1"}
    req2 = captured_requests[-1]
    assert req2.get_header("Authorization") == "Bearer token-reval-2"


def test_alpha_revalidation_fetch_strategy_spec_missing_blank_and_file_error(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock
    from services.research.alpha_replication.revalidation_worker import RevalidationAttemptError

    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)

    captured_requests = []

    def fake_urlopen(req, timeout=10):
        captured_requests.append(req)
        resp = MagicMock()
        resp.read.return_value = b'{"entry": {"strategy_spec_id": "spec-1"}}'
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # 1. Missing / unconfigured: preserves unauthenticated test/local behavior
    monkeypatch.delenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN", raising=False)

    worker._fetch_strategy_spec_entry("spec-1")
    assert captured_requests[-1].get_header("Authorization") is None

    # 2. Blank env token: no header
    monkeypatch.setenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN", "   ")
    worker._fetch_strategy_spec_entry("spec-1")
    assert captured_requests[-1].get_header("Authorization") is None

    # 3. File error: raises RevalidationAttemptError and does not log credentials
    bad_token_file = tmp_path / "bad_token"
    bad_token_file.write_text("secret-reval-token", encoding="utf-8")
    bad_token_file.chmod(0o644)

    monkeypatch.setenv("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN_FILE", str(bad_token_file))

    with pytest.raises(RevalidationAttemptError) as exc_info:
        worker._fetch_strategy_spec_entry("spec-1")
    assert "Configured service credential unavailable" in str(exc_info.value)
    assert "secret-reval-token" not in str(exc_info.value)


def test_gate_rejection_does_not_invoke_persona_teaching(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    result = _run_with_registry(
        worker,
        _registry_entry(payload),
        gate_passed=False,
    )

    assert len(result["errors"]) == 1
    assert "replication rejected" in result["errors"][0]["error"]
    assert authority.create_teaching_session_calls == 0
    assert authority.get_teaching_session_calls == 0
    assert result["created_teaching_session_ids"] == []
    assert result["next_consumer_receipt_ids"] == []
    assert result["authority_receipts"] == []
    assert result["loop_records"] == []
    assert worker.get_loop_record(payload["tenant_id"], payload["strategy_spec_id"]) is None


def test_teaching_call_failure_surfaces_real_error_and_no_fabricated_receipt(tmp_path) -> None:
    authority = FakeAuthority()
    authority.fail_teaching_write = True
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    result = _run_with_registry(worker, _registry_entry(payload))

    assert len(result["errors"]) == 1
    assert "teaching service write failure" in result["errors"][0]["error"]
    assert authority.create_teaching_session_calls == 1
    assert result["created_teaching_session_ids"] == []
    assert result["next_consumer_receipt_ids"] == []
    assert result["authority_receipts"] == []
    assert result["loop_records"] == []
    assert worker.get_loop_record(payload["tenant_id"], payload["strategy_spec_id"]) is None

    # Queue must be marked failed and NOT revalidated
    queued = queue.list_all()[0]
    assert queued["last_revalidation_status"] == "failed"
    assert queued.get("revalidated_at") is None


def test_teaching_readback_failure_surfaces_real_error(tmp_path) -> None:
    authority = FakeAuthority()
    authority.fail_teaching_readback = True
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    result = _run_with_registry(worker, _registry_entry(payload))

    assert len(result["errors"]) == 1
    assert "teaching service readback failed" in result["errors"][0]["error"]
    assert authority.create_teaching_session_calls == 1
    assert authority.get_teaching_session_calls == 1
    assert result["created_teaching_session_ids"] == []
    assert result["next_consumer_receipt_ids"] == []
    assert result["authority_receipts"] == []
    assert result["loop_records"] == []
    assert worker.get_loop_record(payload["tenant_id"], payload["strategy_spec_id"]) is None

    queued = queue.list_all()[0]
    assert queued["last_revalidation_status"] == "failed"
    assert queued.get("revalidated_at") is None


def test_durable_loop_record_persisted_and_reloaded_across_worker_restart(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    result = _run_with_registry(worker, _registry_entry(payload))
    assert result["errors"] == []

    # 1. Verify all 5 canonical loop fields in loop record
    loop_rec = worker.get_loop_record(payload["tenant_id"], payload["strategy_spec_id"])
    assert loop_rec is not None
    trigger_id = str(
        payload.get("admission_id")
        or payload.get("trigger_id")
        or payload.get("approval_decision_id")
        or payload["strategy_spec_id"]
    )
    assert loop_rec["trigger_id"] == trigger_id
    assert loop_rec["terminal_output_id"] == result["created_experiment_run_ids"][0]
    assert loop_rec["next_consumer_receipt_id"] == "trn-0001"
    assert loop_rec["owner_worker_identity"] == "alpha-revalidation-worker"
    assert loop_rec["durable_reload_readback"]["session_id"] == "trn-0001"

    # 2. Simulate worker process restart with fresh instance on same data_dir
    restarted_queue = AlphaReplicationQueue(tmp_path)
    restarted_worker = AlphaRevalidationWorker(
        restarted_queue,
        tmp_path,
        dispatch_mode="authoritative",
        authority=authority,
        registry_url="http://registry.test",
    )

    reloaded = restarted_worker.get_loop_record(payload["tenant_id"], payload["strategy_spec_id"])
    assert reloaded == loop_rec
    assert reloaded["trigger_id"] == loop_rec["trigger_id"]
    assert reloaded["terminal_output_id"] == loop_rec["terminal_output_id"]
    assert reloaded["next_consumer_receipt_id"] == loop_rec["next_consumer_receipt_id"]
    assert reloaded["owner_worker_identity"] == loop_rec["owner_worker_identity"]
    assert reloaded["durable_reload_readback"] == loop_rec["durable_reload_readback"]

    all_records = restarted_worker.list_loop_records(payload["tenant_id"])
    assert len(all_records) == 1
    assert all_records[0]["next_consumer_receipt_id"] == "trn-0001"


def test_real_http_teaching_invocation_and_headers(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock
    from services.research.experiment_orchestrator.authority import ResearchAuthorityHttpClient

    # Use a ResearchAuthorityHttpClient stub so worker defaults to real HTTP transport
    research_auth = ResearchAuthorityHttpClient("http://research-orchestrator.test:8101")
    queue = AlphaReplicationQueue(tmp_path)
    worker = AlphaRevalidationWorker(
        queue,
        tmp_path,
        dispatch_mode="authoritative",
        authority=research_auth,
        registry_url="http://registry.test:8087",
        training_session_url="http://training-svc.test:8099",
    )

    payload = _queue_payload()
    entry = _registry_entry(payload)

    # Prepare token file
    token_file = tmp_path / "training_token"
    token_file.write_text("bearer-teaching-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE", str(token_file))

    captured_http: list[tuple[str, str, dict, Any]] = []

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        method = req.get_method()
        headers = {k: v for k, v in req.headers.items()}
        data = req.data
        body = json.loads(data.decode("utf-8")) if data else None
        captured_http.append((method, url, headers, body))

        resp = MagicMock()
        if method == "POST" and "/api/training/sessions" in url:
            resp.status = 201
            resp.read.return_value = json.dumps({
                "session_id": "trn-http-0042",
                "id": "trn-http-0042",
                "persona_id": body["persona_id"],
                "status": "active",
                "objective": body["objective"],
                "context_refs": body["context_refs"],
            }).encode("utf-8")
        elif method == "GET" and "/api/training/sessions/trn-http-0042" in url:
            resp.status = 200
            resp.read.return_value = json.dumps({
                "session_id": "trn-http-0042",
                "persona_id": "persona-test",
                "status": "active",
                "context_refs": [{"type": "experiment_run", "id": "rrun-1"}],
            }).encode("utf-8")
        else:
            resp.status = 404
            resp.read.return_value = b'{"error": "not found"}'
        resp.getcode.return_value = resp.status
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # Mock _fetch_strategy_spec_entry and gate evaluation
    monkeypatch.setattr(worker, "_fetch_strategy_spec_entry", lambda spec_id: entry)
    monkeypatch.setattr(
        "services.research.replication.gate.ReplicationGate.evaluate_candidate",
        lambda self, task: FakeGateResponse(passed=True, summary="gate passed"),
    )

    monkeypatch.setattr(
        research_auth,
        "ensure_task",
        lambda task, **kwargs: AuthoritativeTaskReceipt(
            authority_task_id="rtask:auth-001", task=task, record={}
        ),
    )
    monkeypatch.setattr(
        research_auth,
        "ensure_run",
        lambda authority_task_id, run, **kwargs: AuthoritativeRunReceipt(
            authority_run_id="rrun:auth-001", run=run, record={}
        ),
    )

    queue.enqueue(payload)
    result = worker.run_once(tenant_id=payload["tenant_id"])

    assert result["errors"] == []
    assert result["created_teaching_session_ids"] == ["trn-http-0042"]
    assert result["next_consumer_receipt_ids"] == ["trn-http-0042"]

    # Verify captured POST request to /api/training/sessions
    post_method, post_url, post_headers, post_body = captured_http[0]
    assert post_method == "POST"
    assert post_url == "http://training-svc.test:8099/api/training/sessions"
    assert post_headers["X-tenant-id"] == payload["tenant_id"]
    assert post_headers["X-pantheon-service"] == "training-session-preview-worker"
    assert post_headers["Authorization"] == "Bearer bearer-teaching-secret"
    exp_run_id = result["created_experiment_run_ids"][0]
    assert post_body["context_refs"] == [
        {
            "type": "experiment_run",
            "id": "rrun:auth-001",
            "domain_run_id": exp_run_id,
            "strategy_spec_id": payload["strategy_spec_id"],
        }
    ]

    # Verify captured GET request to /api/training/sessions/trn-http-0042
    get_method, get_url, get_headers, _ = captured_http[1]
    assert get_method == "GET"
    assert get_url == "http://training-svc.test:8099/api/training/sessions/trn-http-0042"
    assert get_headers["X-tenant-id"] == payload["tenant_id"]
    assert get_headers["Authorization"] == "Bearer bearer-teaching-secret"


def test_alpha_revalidation_teaching_token_file_rotation(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)

    token_file = tmp_path / "teaching_token_rotate"
    token_file.write_text("token-teaching-1\n", encoding="utf-8")
    token_file.chmod(0o600)

    monkeypatch.setenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE", str(token_file)
    )
    monkeypatch.setenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN", "stale-teaching-env-token"
    )

    captured_requests = []

    def fake_urlopen(req, timeout=10):
        captured_requests.append(req)
        resp = MagicMock()
        resp.status = 201
        resp.getcode.return_value = 201
        resp.read.return_value = b'{"session_id": "trn-rot-1"}'
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # 1. Initial POST request with token-teaching-1
    worker._post_teaching_session(
        {"persona_id": "p-1"}, tenant_id="tenant-rot"
    )
    req1 = captured_requests[-1]
    assert req1.get_method() == "POST"
    assert req1.get_header("Authorization") == "Bearer token-teaching-1"

    # 2. Rotate token in file: next call immediately sees rotated token
    token_file.write_text("token-teaching-2\n", encoding="utf-8")

    worker._post_teaching_session(
        {"persona_id": "p-1"}, tenant_id="tenant-rot"
    )
    req2 = captured_requests[-1]
    assert req2.get_header("Authorization") == "Bearer token-teaching-2"
