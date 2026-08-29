from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import (
    CompositeOperationsConsultationPort,
    DomainOpenClawOperationsPort,
    InMemoryOperationsConsultationPort,
    create_read_surface_ports,
)


OPERATOR_AUTH = "Bearer test-operator:operator"
EXPECTED_BACKENDS = {"dsp", "dspy", "qlib", "finrl", "imitation", "mlflow", "openclaw"}


class _DormantOperationsDouble(DomainOpenClawOperationsPort):
    """Typed service-boundary double for dormant OSS capability reads."""

    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        super().__init__()
        self._responses = responses

    def _fetch_dormant_json(self, service: str, path_key: str):
        legacy_keys = {
            ("research_worker_gateway", "capabilities_path"): (
                "http://worker-gateway:8103",
                "/api/research-worker-gateway/capabilities",
            ),
            ("research_worker_gateway", "activity_path"): (
                "http://worker-gateway:8103",
                "/api/research-worker-gateway/jobs",
            ),
            ("research_operator_bridge", "capabilities_path"): (
                "http://research-orchestrator:8101",
                "/api/research-orchestrator/capabilities",
            ),
            ("research_operator_bridge", "activity_path"): (
                "http://research-orchestrator:8101",
                "/api/research-orchestrator/runs",
            ),
            ("openclaw_gateway_adapter", "capabilities_path"): (
                "http://openclaw-adapter:8104",
                "/api/openclaw-adapter/capabilities",
            ),
            ("openclaw_gateway_adapter", "upstream_status_path"): (
                "http://openclaw-adapter:8104",
                "/api/openclaw-adapter/upstream/status",
            ),
        }
        key = legacy_keys.get((service, path_key))
        if key is None or key not in self._responses:
            return {
                "status": "unavailable",
                "source": "service_client",
                "reason": "service_unavailable",
            }, None
        payload = json.loads(json.dumps(self._responses[key]))
        if service == "research_worker_gateway":
            records = payload.get("capabilities") if isinstance(payload, dict) else payload
            for record in records or []:
                if not isinstance(record, dict):
                    continue
                if "worker" in record:
                    record["backend"] = record.pop("worker")
                if "job_id" in record:
                    record["id"] = record.pop("job_id")
                artifact_refs = list(record.get("artifact_refs") or [])
                stdout = str(record.get("stdout") or "")
                if stdout:
                    try:
                        stdout_payload = json.loads(stdout)
                    except json.JSONDecodeError:
                        stdout_payload = None
                    files = (
                        stdout_payload.get("artifact_manifest", {}).get("files", {})
                        if isinstance(stdout_payload, dict)
                        else {}
                    )
                    artifact_refs.extend(
                        {
                            "artifact_name": name,
                            "artifact_path": path,
                            "source_field": "stdout.artifact_manifest.files",
                        }
                        for name, path in files.items()
                    )
                record["artifact_refs"] = artifact_refs
                record["logs"] = [
                    {"source": source, "message": record[source]}
                    for source in ("stdout", "stderr")
                    if record.get(source)
                ]
        if service == "research_operator_bridge":
            records = payload.get("capabilities") if isinstance(payload, dict) else payload
            for record in records or []:
                if isinstance(record, dict) and "adapter" in record:
                    record["framework"] = record.pop("adapter")
        return {"status": "ok", "source": "service_client", "path": key[1]}, payload


def _oss_ports(responses: dict[tuple[str, str], object] | None = None):
    in_memory = InMemoryOperationsConsultationPort()
    operations = CompositeOperationsConsultationPort(
        workflow_port=in_memory,
        openclaw_port=_DormantOperationsDouble(responses or {}),
        consultation_port=in_memory,
    )
    return create_read_surface_ports(operations_consultation=operations)


def _capability_entries(actor_field: str, safe_dispatchers=None):
    safe_dispatchers = safe_dispatchers or ("finrl", "imitation")
    safe = [
        {
            actor_field: dispatcher,
            "status": "available",
            "gate_state": "fail_closed",
            "allowed_scope": "capability_metadata_read_only",
        }
        for dispatcher in safe_dispatchers
    ]
    dormant = [
        {
            actor_field: backend,
            "status": "deferred",
            "gate_state": "fail_closed",
            "allowed_scope": "capability_metadata_read_only",
            "activation_gate": f"{backend}_gate",
        }
        for backend in sorted(EXPECTED_BACKENDS)
    ]
    return safe + dormant


def _activation_ready_capability_entries(actor_field: str, safe_dispatchers=None):
    safe_dispatchers = safe_dispatchers or ("finrl", "imitation")
    offline_ready = EXPECTED_BACKENDS - {"openclaw"}
    safe = [
        {actor_field: dispatcher, "status": "available"}
        for dispatcher in safe_dispatchers
    ]
    dormant = []
    for backend in sorted(EXPECTED_BACKENDS):
        entry = {
            actor_field: backend,
            "status": "deferred",
            "gate_state": "fail_closed",
            "allowed_scope": "capability_metadata_read_only",
            "activation_gate": f"{backend}_gate",
        }
        if backend in offline_ready:
            entry["gate_state"] = "activation_ready"
            entry["allowed_scope"] = "offline_worker_dispatch_enabled"
            if actor_field == "worker":
                entry["offline_dispatch"] = "enabled"
            else:
                entry["gateway_routing"] = "enabled"
        dormant.append(entry)
    return safe + dormant


def _service_payloads():
    return {
        ("http://research-orchestrator:8101", "/api/research-orchestrator/capabilities"): {
            "service": "research-orchestrator",
            "production_activation": "disabled",
            "capabilities": _capability_entries("adapter"),
        },
        ("http://research-orchestrator:8101", "/api/research-orchestrator/runs"): [
            {
                "run_id": "rrun-denied-001",
                "adapter": "qlib",
                "requested_mode": "production",
                "dispatch_mode": "stub",
                "status": "rejected",
                "production_activation": "disabled",
                "rejection": {"reason": "production_adapter_disabled"},
                "updated_at": "2026-04-29T01:00:00Z",
            },
            {
                "run_id": "rrun-safe-001",
                "adapter": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "status": "queued",
                "production_activation": "disabled",
                "updated_at": "2026-04-29T00:59:00Z",
            },
        ],
        ("http://policy-learning:8100", "/api/policy-learning/capabilities"): {
            "service": "policy-learning",
            "production_activation": "disabled",
            "capabilities": _capability_entries("adapter", safe_dispatchers=("stub",)),
        },
        ("http://policy-learning:8100", "/api/policy-learning/jobs"): [
            {
                "job_id": "plj-denied-001",
                "adapter": "stub",
                "requested_mode": "stub",
                "status": "rejected",
                "production_activation": "disabled",
                "rejection": {"reason": "governance_write_disabled"},
                "updated_at": "2026-04-29T01:02:00Z",
            }
        ],
        ("http://worker-gateway:8103", "/api/research-worker-gateway/capabilities"): {
            "service": "research-worker-gateway",
            "production_activation": "disabled",
            "capabilities": _capability_entries("worker"),
        },
        ("http://worker-gateway:8103", "/api/research-worker-gateway/jobs"): [
            {
                "job_id": "wjob-denied-001",
                "worker": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "status": "rejected",
                "production_activation": "disabled",
                "rejection": {"reason": "registry_write_disabled"},
                "updated_at": "2026-04-29T01:01:00Z",
            },
            {
                "job_id": "wjob-safe-001",
                "worker": "handoff_only",
                "requested_mode": "stub",
                "dispatch_mode": "handoff_only",
                "status": "queued",
                "production_activation": "disabled",
                "updated_at": "2026-04-29T00:58:00Z",
            },
        ],
        ("http://openclaw-adapter:8104", "/api/openclaw-adapter/capabilities"): {
            "activation_state": "facade_only",
            "broker_execution": "deferred",
            "paper_adapter": "deferred",
            "live_adapter": "deferred",
            "capital_binding": "deferred",
            "fail_closed": True,
        },
        ("http://openclaw-adapter:8104", "/api/openclaw-adapter/upstream/status"): {
            "reachable": False,
            "details": {"reason": "OPENCLAW_GATEWAY_URL not configured"},
        },
    }


def _activation_ready_service_payloads():
    return {
        ("http://research-orchestrator:8101", "/api/research-orchestrator/capabilities"): {
            "service": "research-orchestrator",
            "production_activation": "disabled",
            "offline_gate": "enabled",
            "capabilities": _activation_ready_capability_entries("adapter"),
        },
        ("http://research-orchestrator:8101", "/api/research-orchestrator/runs"): [
            {
                "run_id": "rrun-qlib-001",
                "task_id": "rtask-qlib-001",
                "adapter": "qlib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "status": "dispatched",
                "production_activation": "disabled",
                "gateway_ref": {"gateway_job_id": "wjob-qlib-001", "gateway": "research-worker-gateway"},
                "artifact_refs": [{"artifact_id": "rart-qlib-001", "artifact_type": "model_artifact"}],
                "proposal_refs": [{"proposal_id": "rprop-qlib-001", "proposal_type": "registry_candidate"}],
                "events": [
                    {
                        "event_type": "run_dispatched",
                        "summary": "Offline-gated qlib dispatch recorded.",
                        "emitted_at": "2026-04-30T06:00:00Z",
                        "sequence_number": 1,
                    }
                ],
                "updated_at": "2026-04-30T06:00:00Z",
            }
        ],
        ("http://policy-learning:8100", "/api/policy-learning/capabilities"): {
            "service": "policy-learning",
            "production_activation": "disabled",
            "offline_gate": "enabled",
            "capabilities": _activation_ready_capability_entries("adapter", safe_dispatchers=("stub",)),
        },
        ("http://policy-learning:8100", "/api/policy-learning/jobs"): [
            {
                "job_id": "plj-qlib-001",
                "policy_id": "policy-qlib-001",
                "adapter": "qlib",
                "requested_mode": "offline",
                "status": "dispatched",
                "production_activation": "disabled",
                "gateway_ref": {"gateway_job_id": "wjob-qlib-001", "gateway": "research-worker-gateway"},
                "events": [
                    {
                        "event_type": "proposal_dispatched",
                        "summary": "Offline-gated policy learning proposal routed to gateway.",
                        "emitted_at": "2026-04-30T06:01:00Z",
                        "sequence_number": 1,
                    }
                ],
                "updated_at": "2026-04-30T06:01:00Z",
            }
        ],
        ("http://worker-gateway:8103", "/api/research-worker-gateway/capabilities"): {
            "service": "research-worker-gateway",
            "production_activation": "disabled",
            "offline_gate": "enabled",
            "capabilities": _activation_ready_capability_entries("worker"),
        },
        ("http://worker-gateway:8103", "/api/research-worker-gateway/jobs"): [
            {
                "job_id": "wjob-qlib-001",
                "worker": "qlib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "status": "completed",
                "production_activation": "disabled",
                "output_refs": [{"output_id": "wgout-qlib-001", "output_type": "offline_execution_output"}],
                "stdout": json.dumps(
                    {
                        "backend": "stub_lgbm",
                        "artifact_state": "draft",
                        "deployment_stage": "none",
                        "checksum": "sha256:qlib-artifact",
                        "artifact_manifest": {
                            "files": {
                                "artifact_bundle": "/tmp/pantheon/qlib/artifact_bundle.json",
                                "registry_entry": "/tmp/pantheon/qlib/registry_entry.json",
                            }
                        },
                    }
                ),
                "stderr": "",
                "exit_code": 0,
                "events": [
                    {
                        "event_type": "job_completed",
                        "summary": "Worker exit_code=0",
                        "emitted_at": "2026-04-30T06:02:00Z",
                        "sequence_number": 2,
                    }
                ],
                "updated_at": "2026-04-30T06:02:00Z",
            },
            {
                "job_id": "wjob-rllib-001",
                "worker": "rllib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "status": "failed",
                "production_activation": "disabled",
                "output_refs": [{"output_id": "wgout-rllib-001", "output_type": "offline_execution_output"}],
                "stdout": "",
                "stderr": "RLlib worker failed before artifact persistence",
                "exit_code": 1,
                "events": [
                    {
                        "event_type": "job_failed",
                        "summary": "Worker exit_code=1",
                        "emitted_at": "2026-04-30T06:03:00Z",
                        "sequence_number": 2,
                    }
                ],
                "updated_at": "2026-04-30T06:03:00Z",
            },
        ],
        ("http://openclaw-adapter:8104", "/api/openclaw-adapter/capabilities"): {
            "activation_state": "facade_only",
            "broker_execution": "deferred",
            "paper_adapter": "deferred",
            "live_adapter": "deferred",
            "capital_binding": "deferred",
            "fail_closed": True,
        },
        ("http://openclaw-adapter:8104", "/api/openclaw-adapter/upstream/status"): {
            "reachable": False,
            "details": {"reason": "OPENCLAW_GATEWAY_URL not configured"},
        },
    }


def test_operator_research_oss_preactivation_aggregates_fail_closed_services() -> None:
    responses = _service_payloads()

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL": "http://research-orchestrator:8101",
                "PANTHEON_POLICY_LEARNING_API_URL": "http://policy-learning:8100",
                "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL": "http://worker-gateway:8103",
                "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-adapter:8104",
            },
            clear=False,
        ):
            bff_main.read_store = _oss_ports(responses)
            client = TestClient(bff_main.app)
            try:
                response = client.get(
                    "/api/v1/operator/research/oss-preactivation",
                    headers={"Authorization": OPERATOR_AUTH},
                )
            finally:
                bff_main.read_store = original_store

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["production_activation"] == "disabled"
    assert data["activated"] is False
    assert data["write_paths"]["paper_canary_live"] == "disabled"
    assert data["write_paths"]["registry_writes"] == "disabled"
    assert data["write_paths"]["governance_writes"] == "disabled"

    inventory = {row["backend"]: row for row in data["backend_inventory"]}
    assert set(inventory) == EXPECTED_BACKENDS
    for backend in EXPECTED_BACKENDS:
        assert inventory[backend]["activated"] is False
        assert inventory[backend]["production_activation"] == "disabled"
        assert inventory[backend]["gate_state"] == "fail_closed"
        assert inventory[backend]["allowed_scope"] == "capability_metadata_read_only"

    assert data["safe_dispatch"]["research_operator_bridge"] == ["finrl", "imitation"]
    assert data["safe_dispatch"]["research_worker_gateway"] == ["finrl", "imitation"]

    openclaw = inventory["openclaw"]["services"]["openclaw_gateway_adapter"]
    assert openclaw["activation_state"] == "facade_only"
    assert openclaw["broker_execution"] == "deferred"
    assert openclaw["capital_binding"] == "deferred"

    rejected_reasons = {
        row["rejection_reason"]
        for row in data["activity"]
        if row["status"] == "rejected"
    }
    assert rejected_reasons >= {
        "production_adapter_disabled",
        "registry_write_disabled",
    }
    assert {row["status"] for row in data["activity"]} >= {"queued", "rejected"}
    assert payload["meta"]["surfaces"]["research_oss_preactivation"]["status"] == "ok"


def test_operator_research_oss_activation_ready_reports_offline_artifacts_logs_and_errors() -> None:
    responses = _activation_ready_service_payloads()

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL": "http://research-orchestrator:8101",
                "PANTHEON_POLICY_LEARNING_API_URL": "http://policy-learning:8100",
                "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL": "http://worker-gateway:8103",
                "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-adapter:8104",
            },
            clear=False,
        ):
            bff_main.read_store = _oss_ports(responses)
            client = TestClient(bff_main.app)
            try:
                response = client.get(
                    "/api/v1/operator/research/oss-activation-ready?activity_limit=10",
                    headers={"Authorization": OPERATOR_AUTH},
                )
            finally:
                bff_main.read_store = original_store

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]

    assert data["production_activation"] == "disabled"
    assert data["activated"] is False
    assert data["activation_state"] == "offline_activation_ready"
    assert data["offline_gate"] == "enabled"
    assert data["write_paths"]["registry_writes"] == "disabled"
    assert data["write_paths"]["governance_writes"] == "disabled"
    assert data["allowed_scope"] == "offline_training_preactivation"

    inventory = {row["backend"]: row for row in data["backend_inventory"]}
    assert inventory["qlib"]["activated"] is False
    assert inventory["qlib"]["production_activation"] == "disabled"
    assert inventory["qlib"]["gate_state"] == "activation_ready"
    assert inventory["qlib"]["allowed_scope"] == "offline_training_preactivation"
    assert inventory["openclaw"]["gate_state"] == "fail_closed"

    run_history = {row["object_id"]: row for row in data["run_history"]}
    qlib_job = run_history["wjob-qlib-001"]
    assert qlib_job["status"] == "completed"
    assert qlib_job["exit_code"] == 0
    assert any(ref.get("source_field") == "stdout.artifact_manifest.files" for ref in qlib_job["artifact_refs"])
    assert any(log.get("source") == "stdout" for log in qlib_job["logs"])

    failed_job = run_history["wjob-rllib-001"]
    assert failed_job["error_summary"]["has_error"] is True
    assert any("RLlib worker failed" in str(log) for log in failed_job["logs"])

    assert any(ref.get("artifact_name") == "artifact_bundle" for ref in qlib_job["artifact_refs"])
    assert sum(len(row["logs"]) for row in data["run_history"]) >= 2
    assert data["error_summary"]["failed_count"] == 1
    assert payload["meta"]["surfaces"]["research_oss_activation_ready"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["research_oss_preactivation"]["status"] == "ok"


def test_operator_research_oss_preactivation_degrades_without_enabling_activation() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _oss_ports()
        client = TestClient(bff_main.app)
        try:
            response = client.get(
                "/api/v1/operator/research/oss-preactivation",
                headers={"Authorization": OPERATOR_AUTH},
            )
        finally:
            bff_main.read_store = original_store

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["production_activation"] == "disabled"
    assert payload["data"]["activated"] is False
    assert payload["data"]["activity"] == []
    assert payload["meta"]["surfaces"]["research_oss_preactivation"]["status"] == "unavailable"
    for backend in payload["data"]["backend_inventory"]:
        assert backend["activated"] is False
        assert backend["production_activation"] == "disabled"
