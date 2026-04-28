from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


def test_governance_runtime_and_evidence_reads_use_http_service_clients_without_snapshot_fallback() -> None:
    responses = {
        ("http://deployment:8095", "/api/deployment/plans"): [
            {
                "plan_id": "plan-svc-001",
                "approval_decision_id": "approval-svc-001",
                "target_stage": "paper",
                "status": "approved",
                "artifact_id": "artifact-svc-001",
                "capital_pool_id": "pool-svc-001",
            }
        ],
        ("http://governance:8082", "/api/governance/approvals"): [
            {
                "decision_id": "approval-svc-001",
                "outcome": "approved",
                "decision_state": "decided",
                "actor_id": "risk-committee",
                "risk_level": "medium",
            }
        ],
        ("http://capital:8092", "/api/capital-pools"): [
            {"pool_id": "pool-svc-001", "name": "Service Pool", "status": "active"}
        ],
        ("http://capital:8092", "/api/bindings"): [
            {
                "binding_id": "pcb-svc-001",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-svc-001",
                "status": "active",
            }
        ],
        ("http://runtime-manager:8081", "/api/runtime-bindings"): {
            "bindings": [
                {
                    "binding_id": "rb-svc-001",
                    "runtime_id": "runtime-svc-001",
                    "plan_id": "plan-svc-001",
                    "deployment_mode": "paper",
                    "status": "active",
                }
            ]
        },
        ("http://lineage-read:8094", "/api/v1/lineage"): [
            {
                "id": "edge-svc-001",
                "source_type": "StrategySpec",
                "source_id": "strat-svc-001",
                "target_type": "CandidateArtifact",
                "target_id": "artifact-svc-001",
                "artifact_id": "artifact-svc-001",
                "created_at": "2026-04-28T00:00:00Z",
            }
        ],
    }

    def fake_get(base_url: str, path: str, *, headers=None):
        if base_url == "http://runtime-manager:8081":
            assert headers == {"Authorization": "Bearer runtime-control-internal"}
        return True, responses[(base_url, path)]

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_DEPLOYMENT_API_URL": "http://deployment:8095",
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "http://governance:8082",
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_LINEAGE_READ_URL": "http://lineage-read:8094",
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "PANTHEON_RUNTIME_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )

                plan = store.get_deployment_plan("plan-svc-001")
                assert plan is not None
                assert plan["runtime_binding_id"] == "rb-svc-001"

                decision = store.get_approval_decision("approval-svc-001")
                assert decision is not None
                assert decision["reviewer"] == "risk-committee"

                assert store.get_capital_pool("pool-svc-001")["name"] == "Service Pool"
                assert store.get_runtime_binding("rb-svc-001")["runtime_id"] == "runtime-svc-001"
                assert store.list_lineage_edges("artifact-svc-001")[0]["id"] == "edge-svc-001"
                assert store.dataset_source("deployment_plans") == "service_client"
                assert store.dataset_source("lineage_edges") == "service_client"


def test_snapshot_payload_does_not_mask_missing_service_client_data_when_fallback_disabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_DEPLOYMENT_API_URL": "",
                "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )

            assert store.list_deployment_plans() == []
            assert store.dataset_source("deployment_plans") == "missing"
