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
from .revalidation_worker import AlphaRevalidationWorker, RevalidationAttemptError, SAFE_DISPATCH_MODES


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


def _tw_registry_entry(payload: dict) -> dict:
    spec = _strategy_spec(strategy_id=payload["strategy_id"])
    spec["market_scope"] = {
        "symbols": ["2330", "2317"],
        "asset_classes": ["equity"],
        "frequency": "1d",
        "venues": ["TWSE"],
    }
    _attach_real_inputs(spec, symbols=["2330", "2317"])
    return _registry_entry(payload, spec)


def test_real_replication_gate_accepts_the_approved_canonical_spec(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    registry_entry = _tw_registry_entry(payload)
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
    assert "actor_id" not in post_body
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


def test_alpha_teaching_invocation_with_real_training_inbound_and_issued_principal(
    tmp_path, monkeypatch
) -> None:
    import importlib.util
    import io
    import sys
    import urllib.error
    import urllib.parse
    from pathlib import Path
    from fastapi.testclient import TestClient
    from scripts.issue_dev_paper_principals import issue_environment
    from scripts.test_issue_dev_paper_principals import configured, KEY
    from services.research.experiment_orchestrator.authority import ResearchAuthorityHttpClient

    # 1. Load real Training session service module and initialize isolated test store
    training_main_path = (
        Path(__file__).resolve().parents[3] / "services/training-session/main.py"
    )
    sys.path.insert(0, str(training_main_path.parent))
    spec = importlib.util.spec_from_file_location(
        "training_session_main", training_main_path
    )
    training_mod = importlib.util.module_from_spec(spec)
    sys.modules["training_session_main"] = training_mod
    spec.loader.exec_module(training_mod)

    training_store_dir = tmp_path / "training_store"
    training_mod.store = training_mod.TrainingSessionStore(training_store_dir)
    client = TestClient(training_mod.app)

    # 2. Configure Training service inbound authority in strict auth mode
    monkeypatch.setenv("TRAINING_SESSION_AUTH_MODE", "strict")
    monkeypatch.delenv("TRAINING_SESSION_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("TRAINING_SESSION_JWT_SECRET", KEY)
    monkeypatch.setenv("TRAINING_SESSION_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("TRAINING_SESSION_JWT_AUDIENCE", "isolated-audience")
    monkeypatch.setenv(
        "TRAINING_SESSION_ALLOWED_CALLER_SERVICES",
        "training-session-preview-worker",
    )

    # 3. Issue genuine token with issue_environment and place in token file
    token = issue_environment(configured())["TRAINING_SESSION_WORKER_TOKEN"]
    token_file = tmp_path / "TRAINING_SESSION_WORKER_TOKEN"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.delenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE", raising=False
    )
    monkeypatch.delenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN", raising=False
    )
    monkeypatch.setenv(
        "TRAINING_SESSION_WORKER_TOKEN_FILE", str(token_file)
    )

    # 4. Wire urlopen adapter that forwards real urllib requests to TestClient(training_mod.app)
    class TestClientHTTPResponse(io.BytesIO):
        def __init__(self, resp):
            super().__init__(resp.content)
            self.status = resp.status_code
            self.headers = resp.headers

        def getcode(self):
            return self.status

        def info(self):
            return self.headers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def app_urlopen(req, timeout=10):
        url = req.full_url
        method = req.get_method()
        headers = {k: v for k, v in req.headers.items()}
        path = urllib.parse.urlparse(url).path
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        resp = client.request(method, path, headers=headers, json=body)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url,
                resp.status_code,
                resp.text,
                resp.headers,
                io.BytesIO(resp.content),
            )
        return TestClientHTTPResponse(resp)

    monkeypatch.setattr("urllib.request.urlopen", app_urlopen)

    # 5. Setup AlphaRevalidationWorker with ResearchAuthorityHttpClient stub
    research_auth = ResearchAuthorityHttpClient(
        "http://research-orchestrator.test:8101"
    )
    queue = AlphaReplicationQueue(tmp_path / "alpha_queue")
    worker = AlphaRevalidationWorker(
        queue,
        tmp_path / "alpha_worker",
        dispatch_mode="authoritative",
        authority=research_auth,
        registry_url="http://registry.test:8087",
        training_session_url="http://training-session.test:8099",
    )

    payload = _queue_payload()
    payload["tenant_id"] = "tenant-dev"
    entry = _registry_entry(payload)

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

    # Enqueue and execute run_once
    queue.enqueue(payload)
    result = worker.run_once(tenant_id="tenant-dev")

    assert result["errors"] == []
    assert len(result["created_teaching_session_ids"]) == 1
    session_id = result["created_teaching_session_ids"][0]
    assert session_id.startswith("trn-")

    # Verify loop record and readback
    loop_rec = worker.get_loop_record("tenant-dev", payload["strategy_spec_id"])
    assert loop_rec is not None
    assert loop_rec["next_consumer_receipt_id"] == session_id
    assert loop_rec["owner_worker_identity"] == "alpha-revalidation-worker"
    assert (
        loop_rec["durable_reload_readback"]["opened_by"]
        == "training-session-preview-worker"
    )
    assert loop_rec["durable_reload_readback"]["session_id"] == session_id

    # 6. Negative contracts:
    # a. Caller-declared mismatched actor is rejected with HTTP 403 by Training _trusted_actor_id
    mismatched_payload = {
        "persona_id": "persona-test",
        "objective": "test mismatched actor",
        "actor_id": "alpha-revalidation-worker",
        "context_refs": [],
    }
    with pytest.raises(RevalidationAttemptError) as exc_info:
        worker._post_teaching_session(
            mismatched_payload, tenant_id="tenant-dev"
        )
    assert "HTTP 403" in str(exc_info.value)
    assert "declared actor does not match" in str(exc_info.value)

    # b. Missing token sends request without Authorization and is rejected with HTTP 401 by Training
    monkeypatch.delenv("ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("TRAINING_SESSION_WORKER_TOKEN_FILE", raising=False)
    monkeypatch.delenv("TRAINING_SESSION_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("PANTHEON_L12_TRAINING_TOKEN", raising=False)
    monkeypatch.delenv("LOCAL_TRAINING_WORKER_TOKEN", raising=False)
    with pytest.raises(RevalidationAttemptError) as exc_info:
        worker._post_teaching_session(
            {"persona_id": "p-1"}, tenant_id="tenant-dev"
        )
    assert "HTTP 401" in str(exc_info.value)
    assert "missing Bearer token" in str(exc_info.value)

    # c. Foreign tenant is rejected with HTTP 403
    monkeypatch.delenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN_FILE", raising=False
    )
    monkeypatch.delenv(
        "ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN", raising=False
    )
    monkeypatch.setenv(
        "TRAINING_SESSION_WORKER_TOKEN_FILE", str(token_file)
    )
    with pytest.raises(RevalidationAttemptError) as exc_info:
        worker._post_teaching_session(
            {"persona_id": "p-1"}, tenant_id="tenant-foreign"
        )
    assert "HTTP 403" in str(exc_info.value)



# --- Baseline Teaching evaluation queued when Alpha opens a session ---------


class _BaselineOwner:
    """In-memory Training owner preview-jobs API keyed by idempotency key."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.fail = False
        self.sessions: dict[str, dict[str, Any]] = {}
        self.job_states: dict[str, dict[str, Any]] = {}

    def urlopen(self, req, timeout=10):
        from unittest.mock import MagicMock

        method, url = req.get_method(), req.full_url
        resp = MagicMock()
        resp.__enter__.return_value = resp
        if method == "POST" and url.endswith("/preview-jobs"):
            self.requests.append(
                {
                    "url": url,
                    "headers": dict(req.header_items()),
                    "body": json.loads(req.data.decode("utf-8")),
                }
            )
            if self.fail:
                raise OSError("training owner unreachable")
            key = req.get_header("Idempotency-key")
            job = self.jobs.setdefault(
                key, {"job_id": f"job-{len(self.jobs) + 1}", "eval_id": "teval-1"}
            )
            resp.status = 201
            resp.read.return_value = json.dumps(job).encode("utf-8")
        elif method == "GET" and "/preview-jobs/" in url:
            job_id = url.rsplit("/", 1)[-1]
            resp.status = 200
            state = self.job_states.get(job_id, {"status": "queued"})
            resp.read.return_value = json.dumps({"job_id": job_id, **state}).encode(
                "utf-8"
            )
        elif method == "GET":
            session_id = url.rsplit("/", 1)[-1]
            resp.status = 200
            resp.read.return_value = json.dumps(self.sessions[session_id]).encode(
                "utf-8"
            )
        else:
            raise AssertionError(f"unexpected request {method} {url}")
        resp.getcode.return_value = resp.status
        return resp


@pytest.fixture
def baseline_owner(monkeypatch):
    owner = _BaselineOwner()
    monkeypatch.setattr("urllib.request.urlopen", owner.urlopen)
    return owner


def _open_session(tmp_path):
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)
    result = _run_with_registry(worker, _registry_entry(payload))
    return authority, worker, payload, result


def test_baseline_queued_once_on_first_open(tmp_path, baseline_owner) -> None:
    authority, worker, payload, result = _open_session(tmp_path)

    assert result["errors"] == []
    assert authority.create_teaching_session_calls == 1
    assert len(baseline_owner.requests) == 1
    request = baseline_owner.requests[0]
    assert request["url"].endswith("/api/training/sessions/trn-0001/preview-jobs")
    assert request["body"] == {"mode": "baseline", "terminalize_session": False}
    assert request["headers"]["Idempotency-key"] == "teaching-baseline-eval-trn-0001"
    assert request["headers"]["X-tenant-id"] == payload["tenant_id"]
    baseline = result["loop_records"][0]["teaching_baseline"]
    assert baseline["status"] == "queued"
    assert baseline["job_id"] == "job-1"


def test_baseline_replay_never_creates_second_job_or_session(
    tmp_path, baseline_owner
) -> None:
    authority, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}

    first = worker._ensure_teaching_baseline(
        "trn-0001",
        tenant_id=payload["tenant_id"],
        readback={"status": "active"},
    )
    assert first["job_id"] == "job-1"
    assert len(baseline_owner.jobs) == 1
    worker.run_once(tenant_id=payload["tenant_id"])
    assert len(baseline_owner.jobs) == 1
    assert authority.create_teaching_session_calls == 1


def test_baseline_skipped_when_session_already_evaluated(
    tmp_path, baseline_owner
) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.requests.clear()
    state = worker._ensure_teaching_baseline(
        "trn-0009",
        tenant_id=payload["tenant_id"],
        readback={"status": "active", "evaluation_result": {"eval_identity": "e1"}},
    )
    assert state == {"status": "skipped", "reason": "evaluation_present"}
    assert baseline_owner.requests == []


def test_baseline_skipped_when_session_not_active(tmp_path, baseline_owner) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.requests.clear()
    state = worker._ensure_teaching_baseline(
        "trn-0009",
        tenant_id=payload["tenant_id"],
        readback={"status": "completed"},
    )
    assert state == {"status": "skipped", "reason": "session_not_active"}
    assert baseline_owner.requests == []


def test_baseline_queue_failure_is_recorded_and_retried_later(
    tmp_path, baseline_owner
) -> None:
    baseline_owner.fail = True
    authority, worker, payload, result = _open_session(tmp_path)

    # Revalidation still succeeded, one session only, failure named on record.
    assert result["errors"] == []
    assert result["created_teaching_session_ids"] == ["trn-0001"]
    assert authority.create_teaching_session_calls == 1
    record = worker.list_loop_records()[0]
    assert record["teaching_baseline"]["status"] == "failed"
    assert record["teaching_baseline"]["failure"] == "teaching_baseline_queue_failed"
    assert worker.get_metrics()["error_count"] == 0

    # Later tick: owner recovers, same session gets exactly one baseline job.
    baseline_owner.fail = False
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    worker.run_once(tenant_id=payload["tenant_id"])
    record = worker.list_loop_records()[0]
    assert record["teaching_baseline"]["status"] == "queued"
    assert list(baseline_owner.jobs) == ["teaching-baseline-eval-trn-0001"]
    assert authority.create_teaching_session_calls == 1


def _legacy_record(worker):
    record = dict(worker.list_loop_records()[0])
    record.pop("teaching_baseline", None)
    worker._save_loop_record(record)


def test_legacy_record_without_baseline_is_reensured_once(
    tmp_path, baseline_owner
) -> None:
    baseline_owner.fail = True
    authority, worker, payload, _ = _open_session(tmp_path)
    _legacy_record(worker)
    baseline_owner.fail = False
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    baseline_owner.requests.clear()

    worker.run_once(tenant_id=payload["tenant_id"])
    assert worker.list_loop_records()[0]["teaching_baseline"]["status"] == "queued"
    assert len(baseline_owner.requests) == 1

    worker.run_once(tenant_id=payload["tenant_id"])
    assert len(baseline_owner.requests) == 1
    assert list(baseline_owner.jobs) == ["teaching-baseline-eval-trn-0001"]
    assert authority.create_teaching_session_calls == 1


def test_prior_queued_without_baseline_mode_is_reensured(
    tmp_path, baseline_owner
) -> None:
    authority, worker, payload, _ = _open_session(tmp_path)
    record = dict(worker.list_loop_records()[0])
    record["teaching_baseline"] = {
        "status": "queued",
        "job_id": "pvjob-old",
        "idempotency_key": "teaching-baseline-trn-0001",
    }
    worker._save_loop_record(record)
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    baseline_owner.requests.clear()

    worker.run_once(tenant_id=payload["tenant_id"])
    baseline = worker.list_loop_records()[0]["teaching_baseline"]
    assert baseline["status"] == "queued"
    assert baseline["mode"] == "baseline"
    assert baseline["idempotency_key"] == "teaching-baseline-eval-trn-0001"
    assert len(baseline_owner.requests) == 1

    worker.run_once(tenant_id=payload["tenant_id"])
    assert len(baseline_owner.requests) == 1


def test_baseline_skipped_when_controls_already_edited(
    tmp_path, baseline_owner
) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.requests.clear()
    state = worker._ensure_teaching_baseline(
        "trn-0009",
        tenant_id=payload["tenant_id"],
        readback={
            "status": "active",
            "events": [
                {"event_type": "preview_requested"},
                {"event_type": "control_patch"},
            ],
        },
    )
    assert state == {"status": "skipped", "reason": "controls_already_edited"}
    assert baseline_owner.requests == []


def test_reensure_skips_session_with_evaluation_result(
    tmp_path, baseline_owner
) -> None:
    baseline_owner.fail = True
    _, worker, payload, _ = _open_session(tmp_path)
    _legacy_record(worker)
    baseline_owner.fail = False
    baseline_owner.sessions["trn-0001"] = {
        "session_id": "trn-0001",
        "status": "active",
        "evaluation_result": {"eval_identity": "e1"},
    }
    baseline_owner.requests.clear()
    worker.run_once(tenant_id=payload["tenant_id"])
    assert baseline_owner.requests == []
    assert worker.list_loop_records()[0]["teaching_baseline"] == {
        "status": "skipped",
        "reason": "evaluation_present",
    }


# --- Failed baseline job is retried with backoff ----------------------------

_FRESHNESS_REASON = (
    "source DatasetVersion authority rejected: connector freshness is not terminal fresh"
)


def _age_baseline(worker, hours: float) -> None:
    from datetime import datetime, timedelta, timezone

    record = dict(worker.list_loop_records()[0])
    baseline = dict(record["teaching_baseline"])
    baseline["queued_at"] = (
        (datetime.now(timezone.utc) - timedelta(hours=hours))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    record["teaching_baseline"] = baseline
    worker._save_loop_record(record)


def _tick(worker, payload):
    worker.run_once(tenant_id=payload["tenant_id"])
    return worker.list_loop_records()[0]["teaching_baseline"]


def test_queued_baseline_completed_job_becomes_final(tmp_path, baseline_owner) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.job_states["job-1"] = {"status": "completed"}
    baseline = _tick(worker, payload)
    assert baseline["status"] == "completed"
    baseline_owner.requests.clear()
    assert _tick(worker, payload)["status"] == "completed"
    assert baseline_owner.requests == []


def test_queued_baseline_running_job_is_left_alone(tmp_path, baseline_owner) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    before = worker.list_loop_records()[0]["teaching_baseline"]
    baseline_owner.job_states["job-1"] = {"status": "running"}
    _age_baseline(worker, 5)
    before = worker.list_loop_records()[0]["teaching_baseline"]
    baseline_owner.requests.clear()
    assert _tick(worker, payload) == before
    assert baseline_owner.requests == []


def test_failed_baseline_retried_with_new_key_after_backoff(
    tmp_path, baseline_owner
) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    baseline_owner.job_states["job-1"] = {
        "status": "failed",
        "error_code": "evaluation_admission_rejected",
        "failure_reason": _FRESHNESS_REASON,
    }
    _age_baseline(worker, 1.5)
    baseline_owner.requests.clear()

    baseline = _tick(worker, payload)
    assert len(baseline_owner.requests) == 1
    assert (
        baseline_owner.requests[0]["headers"]["Idempotency-key"]
        == "teaching-baseline-eval-trn-0001-r2"
    )
    assert baseline["status"] == "queued"
    assert baseline["attempt"] == 2
    assert baseline["job_id"] == "job-2"
    assert baseline["last_error_code"] == "evaluation_admission_rejected"
    assert baseline["last_failure_reason"] == _FRESHNESS_REASON

    # The second attempt is fresh, so a still-failed job waits for its own hour.
    baseline_owner.job_states["job-2"] = dict(baseline_owner.job_states["job-1"])
    baseline_owner.requests.clear()
    assert _tick(worker, payload)["attempt"] == 2
    assert baseline_owner.requests == []


def test_failed_baseline_not_retried_before_backoff(tmp_path, baseline_owner) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    baseline_owner.job_states["job-1"] = {
        "status": "failed",
        "error_code": "evaluation_admission_rejected",
        "failure_reason": _FRESHNESS_REASON,
    }
    _age_baseline(worker, 0.25)
    baseline_owner.requests.clear()
    baseline = _tick(worker, payload)
    assert baseline_owner.requests == []
    assert baseline["status"] == "queued"
    assert baseline["attempt"] == 1
    assert baseline["last_error_code"] == "evaluation_admission_rejected"
    assert baseline["last_failure_reason"] == _FRESHNESS_REASON


@pytest.mark.parametrize(
    "reason",
    [
        "baseline evaluation requires controls that match their baseline values",
        "baseline evaluation already completed for this session",
    ],
)
def test_failed_baseline_with_final_reason_is_never_retried(
    tmp_path, baseline_owner, reason
) -> None:
    _, worker, payload, _ = _open_session(tmp_path)
    baseline_owner.sessions["trn-0001"] = {"session_id": "trn-0001", "status": "active"}
    baseline_owner.job_states["job-1"] = {
        "status": "failed",
        "error_code": "evaluation_admission_rejected",
        "failure_reason": reason,
    }
    _age_baseline(worker, 5)
    baseline_owner.requests.clear()
    baseline = _tick(worker, payload)
    assert baseline["status"] == "failed_final"
    assert baseline["last_failure_reason"] == reason
    _tick(worker, payload)
    assert baseline_owner.requests == []


# --- REPLICATION-GATE-REAL-BACKTEST-20261110 tests --------------------------


def _attach_real_inputs(spec: dict, *, symbols: list[str], trading_days: int = 756) -> None:
    """Supply the candidate's own approved artifact and governed dataset version."""
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        build_canonical_tw_strategy_artifact,
        build_governed_tw_replication_dataset,
    )

    dataset_ref = next(
        d["ref"] for d in spec["data_dependencies"] if d.get("kind") == "dataset"
    )
    artifact = build_canonical_tw_strategy_artifact(
        strategy_id=spec["strategy_id"], symbols=symbols
    )
    spec["strategy_artifact"] = artifact
    spec["backtest_dataset"] = build_governed_tw_replication_dataset(
        dataset_id=dataset_ref,
        symbols=list(artifact["parameters"]["symbols"]),
        trading_days=trading_days,
    )


def test_worker_does_not_write_self_attested_governance_values(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload()
    queue.enqueue(payload)

    captured_requests = []
    from services.research.replication.gate import ReplicationGate

    real_evaluate = ReplicationGate.evaluate_candidate

    def spy_evaluate(self, request):
        captured_requests.append(request)
        return real_evaluate(self, request)

    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=_tw_registry_entry(payload),
    ), mock.patch.object(
        ReplicationGate,
        "evaluate_candidate",
        autospec=True,
        side_effect=spy_evaluate,
    ):
        result = worker.run_once(tenant_id="tenant-a")

    assert result["errors"] == []
    assert len(captured_requests) == 1
    handoff = captured_requests[0].research_handoff
    assert "grok_processing_notes" not in handoff


def test_worker_end_to_end_real_backtest_promotion_gate_and_research_finding_outbox(
    tmp_path,
) -> None:
    from services.research.research_memory_outbox import ResearchMemoryOutboxStore

    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload(strategy_spec_id="reg-strategy-spec-tw-alpha-1.0.0")
    spec = _strategy_spec(strategy_id=payload["strategy_id"])
    spec["market_scope"] = {
        "symbols": ["2330", "2317"],
        "asset_classes": ["equity"],
        "frequency": "1d",
        "venues": ["TWSE"],
    }
    _attach_real_inputs(spec, symbols=["2330", "2317"])
    registry_entry = _registry_entry(payload, spec)
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
    bt_run_id = run.metadata["backtest_run_id"]
    assert bt_run_id == f"bt-{run.run_id}"
    assert list(run.artifact_refs) == [f"backtest://{bt_run_id}"]

    promo_entry = run.metadata["promotion_gate_entry"]
    assert promo_entry["replication_success"] is True
    eval_summary = promo_entry["evaluation_summary"]
    assert eval_summary["sharpe_ratio"] > 0.0
    assert eval_summary["out_of_sample_sharpe_ratio"] == eval_summary["sharpe_ratio"]
    assert eval_summary["deflated_sharpe_threshold"] > 0.0
    assert eval_summary["sharpe_ratio"] >= eval_summary["deflated_sharpe_threshold"]
    assert eval_summary["trial_count"] >= 1
    assert len(eval_summary["walk_forward_fold_sharpes"]) == 3
    assert promo_entry["lineage"]["source_run_ids"] == [bt_run_id]

    outbox = ResearchMemoryOutboxStore(tmp_path)
    records = outbox.list_records(run_id=run.run_id)
    assert len(records) == 1
    finding = records[0].to_dict()
    assert finding["source_event_type"] == "research_finding_published"
    assert finding["run_id"] == run.run_id
    assert finding["metadata"]["artifact_refs"] == [
        {"artifact_type": "backtest_run", "uri": f"backtest://{bt_run_id}"}
    ]
    metrics = finding["metadata"]["metrics"]
    assert metrics["sharpe_ratio"] == eval_summary["sharpe_ratio"]
    assert metrics["commission_rate"] == 0.001425
    assert metrics["equity_transaction_tax_rate"] == 0.003
    assert metrics["etf_transaction_tax_rate"] == 0.001


def test_worker_records_insufficient_history_when_pit_history_under_two_years(
    tmp_path,
) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload(strategy_spec_id="reg-strategy-spec-tw-short-1.0.0")
    spec = _strategy_spec(strategy_id=payload["strategy_id"])
    spec["market_scope"] = {
        "symbols": ["2330"],
        "asset_classes": ["equity"],
        "frequency": "1d",
        "venues": ["TWSE"],
    }
    spec["data_dependencies"] = [{"ref": "dataset:tw-one-year", "kind": "dataset"}]
    _attach_real_inputs(spec, symbols=["2330"], trading_days=252)
    registry_entry = _registry_entry(payload, spec)
    queue.enqueue(payload)

    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=registry_entry,
    ):
        result = worker.run_once(tenant_id="tenant-a")

    assert result["errors"] == []
    assert result["insufficient_history_strategy_spec_ids"] == [
        payload["strategy_spec_id"]
    ]
    queued = queue.list_all()[0]
    assert queued["status"] == "insufficient_history"
    assert queued["last_revalidation_status"] == "insufficient_history"
    assert queue.get_metrics()["insufficient_history"] == 1

    run = next(iter(authority.runs.values()))
    assert run.metadata["candidate_status"] == "insufficient_history"
    assert run.metadata["replication_status"] == "insufficient_history"


def test_worker_refuses_admission_without_real_backtest(tmp_path) -> None:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload(strategy_spec_id="reg-strategy-spec-no-bt-1.0.0")
    spec = _strategy_spec(strategy_id=payload["strategy_id"])
    spec["skip_backtest"] = True
    registry_entry = _registry_entry(payload, spec)
    queue.enqueue(payload)

    with mock.patch.object(
        worker,
        "_fetch_strategy_spec_entry",
        return_value=registry_entry,
    ):
        result = worker.run_once(tenant_id="tenant-a")

    assert len(result["errors"]) == 1
    assert "real_backtest_validation" in result["errors"][0]["error"]
    run = next(iter(authority.runs.values()))
    assert run.status == "failed"



def _run_with_inputs(tmp_path, mutate) -> tuple[dict, FakeAuthority]:
    authority = FakeAuthority()
    queue, worker = _worker(tmp_path, authority)
    payload = _queue_payload(strategy_spec_id="reg-strategy-spec-unbound-1.0.0")
    spec = _strategy_spec(strategy_id=payload["strategy_id"])
    spec["market_scope"] = {
        "symbols": ["2330"],
        "asset_classes": ["equity"],
        "frequency": "1d",
        "venues": ["TWSE"],
    }
    _attach_real_inputs(spec, symbols=["2330"])
    mutate(spec)
    registry_entry = _registry_entry(payload, spec)
    queue.enqueue(payload)
    with mock.patch.object(
        worker, "_fetch_strategy_spec_entry", return_value=registry_entry
    ):
        return worker.run_once(tenant_id="tenant-a"), authority


def test_worker_refuses_when_candidate_inputs_are_absent_instead_of_generating_them(
    tmp_path,
) -> None:
    def drop_inputs(spec):
        spec.pop("strategy_artifact")
        spec.pop("backtest_dataset")

    result, authority = _run_with_inputs(tmp_path, drop_inputs)
    assert "real_backtest_validation" in result["errors"][0]["error"]
    run = next(iter(authority.runs.values()))
    assert run.status == "failed"
    assert not run.metadata.get("backtest_run_id")


def test_worker_refuses_dataset_that_is_not_the_requested_version(tmp_path) -> None:
    def other_dataset(spec):
        spec["backtest_dataset"]["dataset_id"] = "dataset:tw-governed-daily-v1"

    result, authority = _run_with_inputs(tmp_path, other_dataset)
    assert "real_backtest_validation" in result["errors"][0]["error"]
    assert not next(iter(authority.runs.values())).metadata.get("backtest_run_id")


def test_worker_refuses_artifact_not_matching_approved_checksum(tmp_path) -> None:
    def wrong_checksum(spec):
        spec["strategy_artifact_checksum"] = "sha256:not-the-approved-artifact"

    result, authority = _run_with_inputs(tmp_path, wrong_checksum)
    assert "real_backtest_validation" in result["errors"][0]["error"]
    assert not next(iter(authority.runs.values())).metadata.get("backtest_run_id")


def test_worker_never_reuses_cached_results_across_candidates() -> None:
    from services.research.alpha_replication.revalidation_worker import (
        AlphaRevalidationWorker,
    )

    assert not hasattr(AlphaRevalidationWorker, "_CACHED_DEFAULT_TW_BACKTEST")


def test_backtest_run_store_records_gate_dataset_universe_beside_the_digest(tmp_path) -> None:
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore, dataset_symbol_universe

    dataset = {"records": [{"instrument": "2330"}, {"instrument": "2317"}, {"instrument": "2330"}, {"date": "x"}]}
    assert dataset_symbol_universe(dataset) == ["2317", "2330"]
    assert dataset_symbol_universe({}) == []
    store = SharedBacktestRunStore(tmp_path / "runs.json")
    evaluation = {"backtest_run_id": "bt-1", "status": "completed", "dataset_id": "d"}
    digest = store.evaluation_digest(evaluation)
    store.record(evaluation, dataset_symbols=dataset_symbol_universe(dataset))
    row = SharedBacktestRunStore(tmp_path / "runs.json").get("bt-1")
    assert row["dataset_symbols"] == ["2317", "2330"] and row["evaluation_digest"] == digest
    store.record({"backtest_run_id": "bt-2", "status": "completed"})  # no dataset: no universe is invented
    assert "dataset_symbols" not in store.get("bt-2")
    assert store.get("bt-1")["dataset_symbols"] == ["2317", "2330"]
