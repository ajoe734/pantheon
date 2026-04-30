from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_AUTH = "Bearer test-operator:operator"
EXPECTED_BACKENDS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}


def _capability_entries(actor_field: str, safe_dispatchers=None):
    safe_dispatchers = safe_dispatchers or ("stub", "handoff_only", "manual")
    safe = [
        {actor_field: dispatcher, "status": "available"}
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


def test_operator_research_oss_preactivation_aggregates_fail_closed_services() -> None:
    responses = _service_payloads()

    def fake_get(base_url: str, path: str, *, headers=None):
        return True, responses[(base_url, path)]

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
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            client = TestClient(bff_main.app)
            try:
                with mock.patch("read_store._http_json_get", side_effect=fake_get):
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

    assert data["safe_dispatch"]["research_orchestrator"] == ["handoff_only", "manual", "stub"]
    assert data["safe_dispatch"]["research_worker_gateway"] == ["handoff_only", "manual", "stub"]
    assert data["safe_dispatch"]["policy_learning"] == ["stub"]

    openclaw = inventory["openclaw"]["services"]["openclaw_gateway_adapter"]
    assert openclaw["activation_state"] == "facade_only"
    assert openclaw["broker_execution"] == "deferred"
    assert openclaw["capital_binding"] == "deferred"

    verification = data["rejection_verification"]
    assert verification["verification_state"] == "evidence_observed"
    assert verification["all_observed_rejections_fail_closed"] is True
    assert set(verification["observed_reasons"]) >= {
        "production_adapter_disabled",
        "registry_write_disabled",
        "governance_write_disabled",
    }
    assert {row["status"] for row in data["activity"]} >= {"queued", "rejected"}
    assert payload["meta"]["surfaces"]["research_oss_preactivation"]["status"] == "ok"


def test_operator_research_oss_preactivation_degrades_without_enabling_activation() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
