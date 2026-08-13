from __future__ import annotations

from typing import Any
import pytest
from fastapi.testclient import TestClient

from services.consultation import main as consultation_main
from services.consultation.models import MemoStatus, ConsultRequestStatus
from services.consultation.store import ConsultationStore


@pytest.fixture
def consultation_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    test_store = ConsultationStore(str(tmp_path))
    monkeypatch.setattr(consultation_main, "store", test_store)
    return TestClient(consultation_main.app), test_store


def test_intake_terminal_policy_learning_candidate_success(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, store = consultation_client
    candidate_id = "cand-test-intake-001"
    dataset_version_id = "dv-agora-20260813-999"
    payload = {
        "candidate_id": candidate_id,
        "status": "processed",
        "dataset_version_id": dataset_version_id,
        "dataset_lineage": {
            "dataset_id": "ds-agora-999",
            "dataset_version_ids": [dataset_version_id],
            "source_dataset_refs": ["src-001"],
            "authoritative": True,
        },
        "evaluation_summary": {
            "action_match_rate": 0.88,
            "return_gap": 0.015,
            "evaluator_id": "shadow-evaluator-01",
        },
        "artifact_checksum": "sha256:1234567890abcdef",
        "from_persona_id": "persona-policy-learner",
        "priority": "normal",
        "trace_id": "tr-intake-test-001",
    }

    response = client.post("/api/consult/intake/policy-learning-candidate", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()

    assert data["status"] == "created"
    assert data["candidate_id"] == candidate_id
    assert data["dataset_version_id"] == dataset_version_id
    assert data["replayed"] is False

    # Check persisted request in store
    req = data["request"]
    assert req["request_id"] == f"cr-cand-{candidate_id}"
    assert req["target_type"] == "policy_learning_candidate"
    assert req["target_id"] == candidate_id
    assert req["status"] == ConsultRequestStatus.PUBLISHED.value
    assert req["metadata"]["dataset_version_id"] == dataset_version_id
    assert req["metadata"]["dataset_lineage"]["dataset_version_ids"] == [dataset_version_id]

    # Check published memo
    memo = data["memo"]
    assert memo["memo_id"] == f"memo-cand-{candidate_id}"
    assert memo["request_id"] == f"cr-cand-{candidate_id}"
    assert memo["status"] == MemoStatus.PUBLISHED.value
    assert memo["recommendation"] == "approve"
    assert len(memo["findings"]) == 2
    assert memo["target_id"] == candidate_id

    # Check sponsor decision proposal
    proposal = data["proposal"]
    assert proposal["proposal_type"] == "approval_decision"
    assert proposal["metadata"]["sponsor_persona_id"] == "persona-policy-learner"
    assert proposal["target_version"] == dataset_version_id
    assert proposal["target_id"] == candidate_id
    assert proposal["decision_state"] == "proposed"


def test_intake_replay_returns_existing_terminal_decision(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, store = consultation_client
    candidate_id = "cand-test-replay-002"
    dataset_version_id = "dv-agora-replay-002"
    payload = {
        "candidate_id": candidate_id,
        "status": "processed",
        "dataset_version_id": dataset_version_id,
        "dataset_lineage": {
            "dataset_id": "ds-replay-002",
            "dataset_version_ids": [dataset_version_id],
        },
        "evaluation_summary": {
            "action_match_rate": 0.92,
        },
        "from_persona_id": "persona-policy-learner",
        "trace_id": "tr-replay-test-002",
    }

    # First call creates request and memo
    resp1 = client.post("/api/consult/intake/policy-learning-candidate", json=payload)
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["status"] == "created"
    assert data1["replayed"] is False

    # Second call (replay) must detect existing and return without creating a second decision
    resp2 = client.post("/api/consult/intake/policy-learning-candidate", json=payload)
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["status"] == "existing"
    assert data2["replayed"] is True
    assert data2["request_id"] == data1["request_id"]
    assert data2["memo"]["memo_id"] == data1["memo"]["memo_id"]


def test_intake_rejects_non_terminal_candidate_status(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, _ = consultation_client
    payload = {
        "candidate_id": "cand-proposed-003",
        "status": "proposed",  # Non-terminal!
        "dataset_version_id": "dv-003",
        "trace_id": "tr-003",
    }
    response = client.post("/api/consult/intake/policy-learning-candidate", json=payload)
    assert response.status_code == 400
    assert "Intake requires a terminal policy-learning candidate status ('processed')" in response.json()["detail"]


def test_intake_rejects_missing_dataset_version_id(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, _ = consultation_client
    payload = {
        "candidate_id": "cand-nodv-004",
        "status": "processed",
        "dataset_version_id": "",  # Empty string violates min_length=1!
        "trace_id": "tr-004",
    }
    response = client.post("/api/consult/intake/policy-learning-candidate", json=payload)
    assert response.status_code in (400, 422)
