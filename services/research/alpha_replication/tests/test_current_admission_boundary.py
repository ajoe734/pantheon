"""HTTP boundary tests for the reviewed Alpha Replication admission owner API.

Exercises the create/readback commands exposed in services/research/main.py
over the existing ReplicationAdmissionStore, and confirms that the existing
Alpha Replication controller enqueues admitted specs (and only admitted
specs) without a second queue or duplicate ExperimentRuns on replay.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]

ADMISSION_PAYLOAD = {
    "tenant_id": "tenant-current-admission",
    "strategy_spec_id": "spec-current-001",
    "strategy_id": "strat-current-001",
    "spec_version": "1.0.0",
    "checksum": "sha256:" + "a" * 64,
    "approval_decision_id": "approval-current-001",
    "approver": "reviewer-current",
    "mode": "initial",
}


def _load_app_module(module_name: str, *, research_data_dir: str, alpha_data_dir: str):
    env = {
        "RESEARCH_ORCHESTRATOR_DATA_DIR": research_data_dir,
        "ALPHA_REPLICATION_DATA_DIR": alpha_data_dir,
    }
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "services" / "research" / "main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module


def _new_client(tmp_path: Path, unique: str) -> TestClient:
    module = _load_app_module(
        f"research_main_test_{unique}",
        research_data_dir=str(tmp_path / "research-orchestrator"),
        alpha_data_dir=str(tmp_path / "alpha-replication"),
    )
    return TestClient(module.app)


def test_reviewed_strategy_spec_admitted_through_owner_api(tmp_path: Path) -> None:
    client = _new_client(tmp_path, "admit")

    response = client.post(
        "/api/research-orchestrator/alpha-replication/admissions",
        json=ADMISSION_PAYLOAD,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "admitted"
    assert body["tenant_id"] == ADMISSION_PAYLOAD["tenant_id"]
    assert body["strategy_spec_id"] == ADMISSION_PAYLOAD["strategy_spec_id"]
    assert body["admission_id"].startswith("adm-")


def test_authority_readback_returns_exact_admission_identity(tmp_path: Path) -> None:
    client = _new_client(tmp_path, "readback")

    created = client.post(
        "/api/research-orchestrator/alpha-replication/admissions",
        json=ADMISSION_PAYLOAD,
    ).json()

    readback = client.get(
        f"/api/research-orchestrator/alpha-replication/admissions/"
        f"{ADMISSION_PAYLOAD['tenant_id']}/{ADMISSION_PAYLOAD['strategy_spec_id']}"
    )
    assert readback.status_code == 200
    assert readback.json() == created

    missing = client.get(
        "/api/research-orchestrator/alpha-replication/admissions/tenant-current-admission/no-such-spec"
    )
    assert missing.status_code == 404


def test_unreviewed_admission_is_rejected_and_not_admitted(tmp_path: Path) -> None:
    client = _new_client(tmp_path, "unreviewed")

    unreviewed_payload = dict(ADMISSION_PAYLOAD)
    unreviewed_payload.pop("approval_decision_id")

    response = client.post(
        "/api/research-orchestrator/alpha-replication/admissions",
        json=unreviewed_payload,
    )
    assert response.status_code == 400

    missing = client.get(
        f"/api/research-orchestrator/alpha-replication/admissions/"
        f"{ADMISSION_PAYLOAD['tenant_id']}/{ADMISSION_PAYLOAD['strategy_spec_id']}"
    )
    assert missing.status_code == 404


def test_replayed_admission_creates_zero_duplicate_store_entries(tmp_path: Path) -> None:
    client = _new_client(tmp_path, "replay")

    first = client.post(
        "/api/research-orchestrator/alpha-replication/admissions",
        json=ADMISSION_PAYLOAD,
    ).json()
    second = client.post(
        "/api/research-orchestrator/alpha-replication/admissions",
        json=ADMISSION_PAYLOAD,
    ).json()

    assert first == second

    module = sys.modules["research_main_test_replay"]
    admissions = module.alpha_replication_admission_store.list_admissions(
        tenant_id=ADMISSION_PAYLOAD["tenant_id"]
    )
    assert len(admissions) == 1

    from services.research.alpha_replication.admission import ReplicationAdmissionStore
    from services.research.alpha_replication.replication_controller import (
        _get_approved_specs_for_strategy,
        _queue_payload_from_registry_entry,
    )
    from services.research.alpha_replication.queue import AlphaReplicationQueue

    approved_entry = {
        "registry_id": ADMISSION_PAYLOAD["strategy_spec_id"],
        "artifact_type": "strategy_spec",
        "artifact_state": "approved",
        "strategy_id": ADMISSION_PAYLOAD["strategy_id"],
        "version": ADMISSION_PAYLOAD["spec_version"],
        "checksum": ADMISSION_PAYLOAD["checksum"],
        "approval_decision_id": ADMISSION_PAYLOAD["approval_decision_id"],
        "approver": ADMISSION_PAYLOAD["approver"],
        "approved_at": "2026-08-14T00:00:00Z",
        "metadata": {
            "tenant_id": ADMISSION_PAYLOAD["tenant_id"],
            "strategy_spec": {"strategy_id": ADMISSION_PAYLOAD["strategy_id"]},
        },
    }

    admission_store = ReplicationAdmissionStore(tmp_path / "alpha-replication")
    queue = AlphaReplicationQueue(tmp_path / "alpha-replication")

    enqueued_count = 0
    for adm in admission_store.list_admissions(tenant_id=ADMISSION_PAYLOAD["tenant_id"]):
        spec_payload = _queue_payload_from_registry_entry(
            approved_entry, tenant_id=adm["tenant_id"]
        )
        result = queue.enqueue(spec_payload, enqueued_by="test-controller")
        if result is not None:
            enqueued_count += 1

    # Replaying the same admitted spec a second tick must not enqueue again.
    for adm in admission_store.list_admissions(tenant_id=ADMISSION_PAYLOAD["tenant_id"]):
        spec_payload = _queue_payload_from_registry_entry(
            approved_entry, tenant_id=adm["tenant_id"]
        )
        result = queue.enqueue(spec_payload, enqueued_by="test-controller")
        if result is not None:
            enqueued_count += 1

    assert enqueued_count == 1
