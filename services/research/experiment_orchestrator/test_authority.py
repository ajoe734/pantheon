"""Contract tests for the research-orchestrator authority adapter."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.research.experiment_orchestrator.authority import (
    ResearchAuthorityError,
    ResearchAuthorityHttpClient,
)
from services.research.experiments.models import ExperimentRun, ExperimentTask


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _task() -> ExperimentTask:
    return ExperimentTask(
        task_id="etask-alpha-tenant-a-spec-a",
        tenant_id="tenant-a",
        strategy_spec_id="reg-strategy-spec-a",
        strategy_id="strat-a",
        strategy_spec_version="1.0.0",
        requested_by="alpha-replication-worker",
        task_type="rapid_eval",
        backend_id="replication_gate",
        backend_selection_policy_id="alpha-replication-authoritative-v1",
        dataset_version_id="dataset-a",
        code_version="git:abc123",
        priority="normal",
        status="ready",
        idempotency_key="alpha-task:tenant-a:reg-strategy-spec-a",
        trace_id="trace-alpha-task-a",
        created_at="2026-07-26T10:00:00Z",
    )


def _run(task: ExperimentTask) -> ExperimentRun:
    return ExperimentRun(
        run_id="erun-alpha-tenant-a-spec-a-attempt-1",
        task_id=task.task_id,
        tenant_id=task.tenant_id,
        strategy_spec_id=task.strategy_spec_id,
        strategy_id=task.strategy_id,
        strategy_spec_version=task.strategy_spec_version,
        backend_id="replication_gate",
        runtime_env="research",
        status="completed",
        dataset_version_id=task.dataset_version_id,
        code_version=task.code_version,
        input_manifest_ref="registry://reg-strategy-spec-a",
        output_manifest_ref="research-authority://erun-alpha-tenant-a-spec-a-attempt-1",
        artifact_refs=[],
        trace_id="trace-alpha-run-a",
        created_at="2026-07-26T10:01:00Z",
        started_at="2026-07-26T10:01:00Z",
        finished_at="2026-07-26T10:02:00Z",
        metadata={
            "idempotency_key": "alpha-run:tenant-a:reg-strategy-spec-a:1",
            "production_activation": "disabled",
        },
    )


class FakeResearchServiceTransport:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.task_ids_by_key: dict[str, str] = {}
        self.run_ids_by_key: dict[str, str] = {}

    def __call__(self, method: str, path: str, body: dict | None):
        if method == "POST" and path == "/api/research-orchestrator/tasks":
            assert body is not None
            key = body["idempotency_key"]
            task_id = self.task_ids_by_key.get(key)
            if task_id:
                return deepcopy(self.tasks[task_id])
            task_id = f"rtask-{len(self.tasks) + 1}"
            record = {
                **deepcopy(body),
                "id": task_id,
                "task_id": task_id,
                "status": "ready",
            }
            self.tasks[task_id] = record
            self.task_ids_by_key[key] = task_id
            return deepcopy(record)

        if method == "GET" and path.startswith("/api/research-orchestrator/tasks/"):
            return deepcopy(self.tasks[path.rsplit("/", 1)[-1]])

        if method == "POST" and path.endswith("/runs") and "/tasks/" in path:
            assert body is not None
            key = body["idempotency_key"]
            run_id = self.run_ids_by_key.get(key)
            if run_id:
                return deepcopy(self.runs[run_id])
            run_id = f"rrun-{len(self.runs) + 1}"
            record = {
                **deepcopy(body),
                "id": run_id,
                "run_id": run_id,
                "task_id": path.split("/tasks/", 1)[1].split("/", 1)[0],
                "status": "queued",
                "production_activation": "disabled",
            }
            self.runs[run_id] = record
            self.run_ids_by_key[key] = run_id
            return deepcopy(record)

        if method == "POST" and path.endswith("/complete"):
            assert body is not None
            run_id = path.split("/runs/", 1)[1].split("/", 1)[0]
            self.runs[run_id]["status"] = body["status"]
            return deepcopy(self.runs[run_id])

        if method == "GET" and path == "/api/research-orchestrator/runs":
            return [deepcopy(record) for record in self.runs.values()]

        if method == "GET" and path.startswith("/api/research-orchestrator/runs/"):
            return deepcopy(self.runs[path.rsplit("/", 1)[-1]])

        raise AssertionError(f"unexpected transport request: {method} {path}")


def _load_research_service_module():
    with mock.patch.dict(
        os.environ,
        {
            "RESEARCH_ORCHESTRATOR_DATA_DIR": tempfile.mkdtemp(),
            "RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS": "8",
            "RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS": "false",
            "PANTHEON_OFFLINE_GATE_ENABLED": "false",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location(
                "l12_alpha_research_authority_main",
                SERVICE_DIR / "main.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["l12_alpha_research_authority_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


class FastApiTransport:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def __call__(self, method: str, path: str, body: dict | None):
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        return response.json()


def test_authority_records_and_reads_back_non_stub_domain_task_and_run() -> None:
    transport = FakeResearchServiceTransport()
    client = ResearchAuthorityHttpClient(
        "http://research-authority",
        transport=transport,
    )
    task = _task()
    task_receipt = client.ensure_task(
        task,
        approval_decision_id="approval-a",
        approver="reviewer-a",
        approved_at="2026-07-26T09:00:00Z",
        checksum="sha256:spec-a",
    )
    run_receipt = client.ensure_run(
        task_receipt.authority_task_id,
        _run(task),
        approval_decision_id="approval-a",
    )

    assert task_receipt.task == task
    assert run_receipt.run.backend_id == "replication_gate"
    assert run_receipt.record["adapter"] == "manual"
    assert run_receipt.record["status"] == "completed"
    assert run_receipt.record["production_activation"] == "disabled"
    assert client.list_runs(
        tenant_id="tenant-a",
        strategy_spec_id="reg-strategy-spec-a",
    ) == [run_receipt.run]


def test_real_research_service_boundary_persists_domain_payload_and_readback() -> None:
    module = _load_research_service_module()
    service_client = TestClient(module.app)
    client = ResearchAuthorityHttpClient(
        "http://research-orchestrator.test",
        transport=FastApiTransport(service_client),
    )
    task = _task()

    task_receipt = client.ensure_task(
        task,
        approval_decision_id="approval-a",
        approver="reviewer-a",
        approved_at="2026-07-26T09:00:00Z",
        checksum="sha256:spec-a",
    )
    run_receipt = client.ensure_run(
        task_receipt.authority_task_id,
        _run(task),
        approval_decision_id="approval-a",
    )

    stored_task = module.store.get_task(task_receipt.authority_task_id)
    stored_run = module.store.get_run(run_receipt.authority_run_id)
    assert stored_task["constraints"]["experiment_task"] == task.to_dict()
    assert stored_run["parameters"]["experiment_run"] == run_receipt.run.to_dict()
    assert stored_run["adapter"] == "manual"
    assert stored_run["status"] == "completed"
    assert stored_run["production_activation"] == "disabled"
    assert len(module.store.list_tasks()) == 1
    assert len(module.store.list_runs()) == 1


def test_authority_idempotency_converges_duplicate_restart_once() -> None:
    transport = FakeResearchServiceTransport()
    client = ResearchAuthorityHttpClient(
        "http://research-authority",
        transport=transport,
    )
    task = _task()

    first_task = client.ensure_task(
        task,
        approval_decision_id="approval-a",
        approver="reviewer-a",
        approved_at="2026-07-26T09:00:00Z",
        checksum="sha256:spec-a",
    )
    second_task = client.ensure_task(
        task,
        approval_decision_id="approval-a",
        approver="reviewer-a",
        approved_at="2026-07-26T09:00:00Z",
        checksum="sha256:spec-a",
    )
    first_run = client.ensure_run(
        first_task.authority_task_id,
        _run(task),
        approval_decision_id="approval-a",
    )
    second_run = client.ensure_run(
        second_task.authority_task_id,
        _run(task),
        approval_decision_id="approval-a",
    )

    assert first_task.authority_task_id == second_task.authority_task_id
    assert first_run.authority_run_id == second_run.authority_run_id
    assert len(transport.tasks) == 1
    assert len(transport.runs) == 1


def test_authority_rejects_stub_or_tampered_readback() -> None:
    transport = FakeResearchServiceTransport()
    client = ResearchAuthorityHttpClient(
        "http://research-authority",
        transport=transport,
    )
    task = _task()
    receipt = client.ensure_task(
        task,
        approval_decision_id="approval-a",
        approver="reviewer-a",
        approved_at="2026-07-26T09:00:00Z",
        checksum="sha256:spec-a",
    )
    run = _run(task)
    transport.run_ids_by_key[run.metadata["idempotency_key"]] = "rrun-stub"
    transport.runs["rrun-stub"] = {
        "run_id": "rrun-stub",
        "task_id": receipt.authority_task_id,
        "adapter": "stub",
        "status": "completed",
        "production_activation": "disabled",
        "parameters": {
            "record_type": "ExperimentRun",
            "approval_decision_id": "approval-a",
            "producer_backend": "stub",
            "experiment_run": run.to_dict(),
        },
    }

    with pytest.raises(ResearchAuthorityError, match="stub authority runs"):
        client.ensure_run(
            receipt.authority_task_id,
            run,
            approval_decision_id="approval-a",
        )
