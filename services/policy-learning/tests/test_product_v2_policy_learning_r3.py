"""Tests for PRODUCT-V2-POLICY-LEARNING-R3-20260813.

Verifies:
1. Consumption of durable Agora DatasetVersion handoffs through the production consumer boundary (/api/policy-learning/agora-handoff).
2. Acknowledgment returned only after durable candidate store ingestion succeeds.
3. Production of terminal shadow candidate linked to DatasetVersion with dataset lineage, without seed fallback or live execution authority.
4. Idempotency under duplicate delivery and consumer persistence reload/restart.
5. Fail-closed tenant boundaries, authorization checks, and invalid payload handling.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import main as main_module
from agora_dataset_authority import (
    AgoraDatasetAuthority,
    AgoraDatasetVersion,
    build_agora_dataset_authority,
)
from inbound_authority import (
    LOCAL_DEV_SERVICE_TOKEN,
    TENANT_HEADER,
)
from main import (
    STATUS_PROCESSED,
    STATUS_PROPOSED,
    app,
    store,
)
from store import PolicyLearningStore


TEST_TENANT = "tenant-a"
TEST_USER = "user-r3-ops"


@pytest.fixture
def auth_headers() -> Dict[str, str]:
    return {
        TENANT_HEADER: TEST_TENANT,
        "Authorization": f"Bearer {LOCAL_DEV_SERVICE_TOKEN}",
    }


@pytest.fixture
def test_app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Fixture providing a fresh store and app client per test."""

    data_dir = tmp_path / "policy_learning_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    test_store = PolicyLearningStore(data_dir=data_dir)
    monkeypatch.setattr(main_module, "store", test_store)

    test_authority = AgoraDatasetAuthority(backend="memory", records=[])
    monkeypatch.setattr(main_module, "DATASET_AUTHORITY", test_authority)

    monkeypatch.setenv("POLICY_LEARNING_DATASET_MODE", "product")
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TOKEN", LOCAL_DEV_SERVICE_TOKEN)
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TENANTS", "tenant-a,tenant-b,tenant-c,tenant-z,tenant-other-scope")

    client = TestClient(app)
    return client


def _sample_agora_dataset_version_record(
    version_id: str = "dsv-r3-agora-001",
    tenant_id: str = TEST_TENANT,
) -> Dict[str, Any]:
    return {
        "dataset_version_id": version_id,
        "tenant_id": tenant_id,
        "user_id": TEST_USER,
        "dataset_kind": "learn",
        "interaction_kind": "operator_action",
        "persona_id": "persona-alpha",
        "evidence_id": f"evt-evidence-{version_id}",
        "source_refs": [f"agora://dataset-version/{tenant_id}/{version_id}"],
        "content": {
            "strategy_id": "alpha-mean-reversion",
            "source_strategy_spec_id": "strat-alpha-v2",
            "sessions": [
                {
                    "trajectory_id": "traj-r3-001",
                    "actor_id": "trader-r3-01",
                    "actor_role": "operator",
                    "decision": "approve",
                    "steps": [
                        {
                            "observation": [0.85, 0.15, -0.1],
                            "action": "buy_small",
                            "reward": 0.45,
                            "feedback_event_id": "evt-fb-001",
                        },
                        {
                            "observation": [0.75, 0.25, -0.2],
                            "action": "reduce_risk",
                            "reward": 0.35,
                            "feedback_event_id": "evt-fb-002",
                        },
                    ],
                }
            ],
        },
        "learning_eligible": True,
        "captured_at": "2026-08-13T09:00:00Z",
        "extracted_at": "2026-08-13T09:05:00Z",
        "version": 1,
    }


def test_durable_agora_dataset_version_handoff_consumption_and_acknowledgment(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Requirement 1 & 2: Consume Agora DatasetVersion handoff and acknowledge after durable ingestion."""

    record = _sample_agora_dataset_version_record("dsv-r3-001")
    payload = {
        "handoff_id": "handoff-r3-001",
        "dataset_version": record,
        "eval_type": "shadow",
        "actor_id": "agora-pipeline-worker",
        "process_immediately": True,
    }

    response = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 201
    data = response.json()

    assert data["status"] == "acknowledged"
    assert data["handoff_id"] == "handoff-r3-001"
    assert data["dataset_version_id"] == "dsv-r3-001"
    assert data["tenant_id"] == TEST_TENANT
    assert data["created"] is True
    assert data["candidate_status"] == STATUS_PROCESSED
    assert data["seed_fallback_used"] is False
    assert data["authoritative"] is True
    assert data["production_training"] == "fail_closed"

    lineage = data["dataset_lineage"]
    assert "dsv-r3-001" in lineage.get("dataset_version_ids", [])
    assert lineage.get("tenant_id") == TEST_TENANT
    assert lineage.get("seed_fallback_used") is False


def test_terminal_shadow_candidate_properties(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Requirement 3: Terminal shadow candidate is linked to DatasetVersion without seed fallback or live execution authority."""

    record = _sample_agora_dataset_version_record("dsv-r3-002")
    payload = {
        "handoff_id": "handoff-r3-002",
        "dataset_version": record,
        "process_immediately": True,
    }

    res_ack = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )
    candidate_id = res_ack.json()["candidate_id"]

    res_cand = test_app_client.get(
        f"/api/policy-learning/candidates/{candidate_id}",
        headers=auth_headers,
    )
    assert res_cand.status_code == 200
    cand = res_cand.json()

    assert cand["status"] == STATUS_PROCESSED
    assert cand["dataset_version_id"] == "dsv-r3-002"
    assert cand["seed_fallback_used"] is False
    assert cand["authoritative"] is True
    assert cand["production_training"] == "fail_closed"
    assert cand["experiment_approval_gate"] == "required"
    assert cand["runtime_effect"] == "none"

    # Verify metrics & evaluation summary
    assert "action_match_rate" in cand["evaluation_summary"]
    assert "kl_divergence" in cand["evaluation_summary"]
    assert "return_gap" in cand["evaluation_summary"]
    assert "policy_weights" in cand
    assert "artifact_checksum" in cand

    # Verify lineage retains DatasetVersion linkage
    dataset_lineage = cand["dataset_lineage"]
    assert "dsv-r3-002" in dataset_lineage["dataset_version_ids"]
    assert dataset_lineage["product_mode"] is True


def test_duplicate_delivery_idempotency(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Requirement 4: Re-delivering same handoff returns existing candidate without duplicating backlog state."""

    record = _sample_agora_dataset_version_record("dsv-r3-003")
    payload = {
        "handoff_id": "handoff-r3-003",
        "dataset_version": record,
        "process_immediately": True,
    }

    # First delivery
    res1 = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )
    assert res1.status_code == 201
    d1 = res1.json()
    assert d1["created"] is True
    candidate_id = d1["candidate_id"]

    # Duplicate delivery
    res2 = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )
    assert res2.status_code == 201
    d2 = res2.json()
    assert d2["created"] is False
    assert d2["candidate_id"] == candidate_id
    assert d2["status"] == "acknowledged"
    assert d2["candidate_status"] == STATUS_PROCESSED

    # Verify candidate count in backlog is still 1
    res_list = test_app_client.get(
        "/api/policy-learning/candidates",
        headers=auth_headers,
    )
    assert res_list.status_code == 200
    candidates = res_list.json()
    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == candidate_id


def test_consumer_restart_and_persistence_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 4 & 5: Persisted candidates reload cleanly upon store restart without data loss."""

    data_dir = tmp_path / "persistent_policy_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Store instance 1: ingest handoff and process
    store1 = PolicyLearningStore(data_dir=data_dir)
    monkeypatch.setattr(main_module, "store", store1)
    test_auth1 = AgoraDatasetAuthority(backend="memory", records=[])
    monkeypatch.setattr(main_module, "DATASET_AUTHORITY", test_auth1)
    monkeypatch.setenv("POLICY_LEARNING_DATASET_MODE", "product")
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TOKEN", LOCAL_DEV_SERVICE_TOKEN)
    monkeypatch.setenv("POLICY_LEARNING_SERVICE_TENANTS", "tenant-a,tenant-b,tenant-c,tenant-z,tenant-other-scope")

    headers = {
        TENANT_HEADER: TEST_TENANT,
        "Authorization": f"Bearer {LOCAL_DEV_SERVICE_TOKEN}",
    }
    client1 = TestClient(app)

    record = _sample_agora_dataset_version_record("dsv-r3-restart-001")
    res1 = client1.post(
        "/api/policy-learning/agora-handoff",
        json={"handoff_id": "handoff-restart-1", "dataset_version": record},
        headers=headers,
    )
    assert res1.status_code == 201
    candidate_id = res1.json()["candidate_id"]

    # Store instance 2: simulate service restart by reading from the same data_dir
    store2 = PolicyLearningStore(data_dir=data_dir)
    monkeypatch.setattr(main_module, "store", store2)
    client2 = TestClient(app)

    res2 = client2.get(
        f"/api/policy-learning/candidates/{candidate_id}",
        headers=headers,
    )
    assert res2.status_code == 200
    reloaded = res2.json()
    assert reloaded["candidate_id"] == candidate_id
    assert reloaded["status"] == STATUS_PROCESSED
    assert reloaded["dataset_version_id"] == "dsv-r3-restart-001"
    assert reloaded["seed_fallback_used"] is False


def test_cross_tenant_mismatch_rejection(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Fail closed when dataset record tenant mismatches header tenant."""

    record = _sample_agora_dataset_version_record("dsv-r3-tenant-mismatch", tenant_id="tenant-other-scope")
    payload = {
        "handoff_id": "handoff-mismatch",
        "dataset_version": record,
    }

    response = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 400
    err = response.json()["detail"]
    assert err["code"] == "dataset_ref_tenant_mismatch"


def test_invalid_agora_dataset_version_rejection(
    test_app_client: TestClient, auth_headers: Dict[str, str]
) -> None:
    """Fail closed when DatasetVersion record lacks required lineage identity."""

    invalid_record = {
        "dataset_version_id": "dsv-invalid",
        "tenant_id": TEST_TENANT,
        # missing evidence_id!
    }
    payload = {
        "handoff_id": "handoff-invalid",
        "dataset_version": invalid_record,
    }

    response = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 400
    err = response.json()["detail"]
    assert err["code"] == "dataset_version_invalid"


def test_unauthenticated_handoff_rejection(
    test_app_client: TestClient
) -> None:
    """Unauthenticated handoff requests are rejected."""

    record = _sample_agora_dataset_version_record("dsv-noauth")
    response = test_app_client.post(
        "/api/policy-learning/agora-handoff",
        json={"dataset_version": record},
        headers={TENANT_HEADER: TEST_TENANT},
    )
    assert response.status_code in (401, 403)
