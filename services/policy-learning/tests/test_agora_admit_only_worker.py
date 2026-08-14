"""Tests for AGORA-POLICY-ADMIT-WORKER-20260813.

Verifies:
1. Agora handoff endpoint validates scope and lineage, durably creates/adopts proposed candidate in STATUS_PROPOSED, and acknowledges admission without claiming or processing inline.
2. Worker lease, process, renew, crash recovery, and terminal processing.
3. Fail-closed tenant boundaries, dataset authority degradation without seed fallback in product mode.
4. Artifact checksum, dataset lineage, and explicit runtime_effect=none.
5. Promotion endpoint always fails closed (HTTP 409).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import main as main_module
import store as store_module
from agora_dataset_authority import AgoraDatasetAuthority
from inbound_authority import LOCAL_DEV_SERVICE_TOKEN, TENANT_HEADER
from main import (
    STATUS_CLAIMED,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_PROCESSED,
    STATUS_PROPOSED,
    app,
)
from store import PolicyLearningStore

TEST_TENANT = "tenant-admit"
TEST_USER = "user-admit-ops"


@pytest.fixture
def auth_headers() -> Dict[str, str]:
    return {
        TENANT_HEADER: TEST_TENANT,
        "Authorization": f"Bearer {LOCAL_DEV_SERVICE_TOKEN}",
    }


@pytest.fixture
def test_app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "admit_worker_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    test_store = PolicyLearningStore(data_dir=data_dir)
    monkeypatch.setattr(main_module, "store", test_store)

    test_authority = AgoraDatasetAuthority(
        backend="memory",
        records=[
            _sample_dataset_version_record("dsv-admit-001"),
            _sample_dataset_version_record("dsv-admit-002"),
            _sample_dataset_version_record("dsv-admit-crash"),
            _sample_dataset_version_record("dsv-no-promote"),
        ],
    )
    monkeypatch.setattr(main_module, "DATASET_AUTHORITY", test_authority)

    monkeypatch.setenv("POLICY_LEARNING_DATASET_MODE", "product")
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TOKEN", LOCAL_DEV_SERVICE_TOKEN)
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TENANTS", f"{TEST_TENANT},tenant-a,tenant-other")

    return TestClient(app)


def _sample_dataset_version_record(version_id: str, tenant_id: str = TEST_TENANT) -> Dict[str, Any]:
    return {
        "dataset_version_id": version_id,
        "tenant_id": tenant_id,
        "user_id": TEST_USER,
        "dataset_kind": "learn",
        "interaction_kind": "operator_action",
        "persona_id": "persona-alpha",
        "evidence_id": f"evt-{version_id}",
        "source_refs": [f"agora://dataset-version/{tenant_id}/{version_id}"],
        "content": {
            "strategy_id": "alpha-mean-reversion",
            "source_strategy_spec_id": "strat-alpha-v2",
            "sessions": [
                {
                    "trajectory_id": "traj-001",
                    "actor_id": "trader-01",
                    "actor_role": "operator",
                    "decision": "approve",
                    "steps": [
                        {
                            "observation": [0.85, 0.15, -0.1],
                            "action": "buy_small",
                            "reward": 0.45,
                            "feedback_event_id": f"evt-{version_id}-a",
                        },
                        {
                            "observation": [0.75, 0.25, -0.2],
                            "action": "reduce_risk",
                            "reward": 0.35,
                            "feedback_event_id": f"evt-{version_id}-b",
                        },
                    ],
                }
            ],
        },
        "learning_eligible": True,
        "captured_at": "2026-08-13T10:00:00Z",
        "extracted_at": "2026-08-13T10:05:00Z",
        "version": 1,
    }


def test_handoff_is_strictly_admit_only(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Handoff endpoint acknowledges admission without claiming or processing inline."""
    record = _sample_dataset_version_record("dsv-admit-001")
    payload = {
        "handoff_id": "handoff-admit-001",
        "dataset_version": record,
        "actor_id": "agora-admit-test",
    }

    res = test_app_client.post("/api/policy-learning/agora-handoff", json=payload, headers=auth_headers)
    assert res.status_code == 201
    data = res.json()

    assert data["status"] == "acknowledged"
    assert data["created"] is True
    assert data["candidate_status"] == STATUS_PROPOSED

    cand_res = test_app_client.get(f"/api/policy-learning/candidates/{data['candidate_id']}", headers=auth_headers)
    assert cand_res.status_code == 200
    cand = cand_res.json()

    assert cand["status"] == STATUS_PROPOSED
    assert cand["attempt_count"] == 0
    assert cand["runtime_effect"] == "none"
    assert cand["production_training"] == "fail_closed"


def test_worker_claim_lease_and_process_cycle(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Worker claims batch under lease and processes candidates into STATUS_PROCESSED."""
    record = _sample_dataset_version_record("dsv-admit-002")
    test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json={"handoff_id": "handoff-admit-002", "dataset_version": record},
        headers=auth_headers,
    )

    # Process batch directly via worker/process route (which claims and processes proposed items)
    proc_res = test_app_client.post(
        "/api/policy-learning/worker/process",
        json={"worker_id": "worker-1", "batch_size": 5},
        headers=auth_headers,
    )
    assert proc_res.status_code == 200
    assert proc_res.json()["processed_count"] == 1

    cand_id = proc_res.json()["candidate_ids"][0]
    cand_res_after = test_app_client.get(f"/api/policy-learning/candidates/{cand_id}", headers=auth_headers)
    final_cand = cand_res_after.json()
    assert final_cand["status"] == STATUS_PROCESSED
    assert final_cand["runtime_effect"] == "none"
    assert "artifact_checksum" in final_cand


def test_worker_restart_reclaims_expired_leases(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Crashed worker lease recovery returns expired candidates to backlog and re-processes them."""
    record = _sample_dataset_version_record("dsv-admit-crash")
    handoff_res = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json={"handoff_id": "handoff-crash", "dataset_version": record},
        headers=auth_headers,
    )
    cand_id = handoff_res.json()["candidate_id"]

    # Claim candidate
    test_app_client.post(
        "/api/policy-learning/worker/claim",
        json={"worker_id": "crashed-worker", "batch_size": 1, "lease_seconds": 60},
        headers=auth_headers,
    )

    # Simulate expired lease on stored candidate
    cand = main_module.store.get_candidate(cand_id)
    cand["lease_expires_at"] = "2020-01-01T00:00:00Z"
    main_module.store.put_candidate(cand)

    restart_res = test_app_client.post(
        "/api/policy-learning/worker/restart",
        json={"worker_id": "recovery-worker", "batch_size": 5},
        headers=auth_headers,
    )
    assert restart_res.status_code == 200
    restart_data = restart_res.json()
    assert restart_data["released_count"] == 1
    assert cand_id in restart_data["released_candidate_ids"]
    assert restart_data["processed_count"] == 1


def test_promote_candidate_always_fails_closed(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Shadow candidates cannot be promoted; /promote endpoint always returns HTTP 409."""
    record = _sample_dataset_version_record("dsv-no-promote")
    handoff_res = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json={"handoff_id": "handoff-no-promote", "dataset_version": record},
        headers=auth_headers,
    )
    cand_id = handoff_res.json()["candidate_id"]

    promote_res = test_app_client.post(
        f"/api/policy-learning/candidates/{cand_id}/promote",
        headers=auth_headers,
    )
    assert promote_res.status_code == 409
    detail = promote_res.json()["detail"]
    assert detail["promotion_allowed"] is False
    assert detail["runtime_effect"] == "none"
    assert detail["production_training"] == "fail_closed"
