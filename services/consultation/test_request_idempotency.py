from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.consultation import main as consultation_main
from services.consultation.client import ConsultationServiceClient
from services.consultation.models import ConsultRequestStatus
from services.consultation.store import ConsultationStore


def _payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "cr-workshop-001",
        "request_type": "strategy_review",
        "requested_by": {"actor_type": "operator", "actor_id": "operator-1"},
        "from_persona_id": "persona-alpha",
        "target_type": "strategy_workshop",
        "target_id": "workshop-001",
        "task": "Review the selected StrategySpec version",
        "consultation_type": "committee",
        "context_refs": ["workshop_version:wv-001"],
        "evidence_refs": ["evidence:ev-001"],
        "priority": "high",
        "metadata": {"workshop_version_id": "wv-001"},
        "trace_id": "trace-workshop-001",
    }
    payload.update(updates)
    return payload


@pytest.fixture
def consultation_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    test_store = ConsultationStore(str(tmp_path))
    monkeypatch.setattr(consultation_main, "store", test_store)
    return TestClient(consultation_main.app), test_store


def _line_count(path: Any) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line])


def test_create_with_caller_id_replays_exact_record_without_new_events(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, store = consultation_client

    created = client.post("/api/consult/requests", json=_payload())
    assert created.status_code == 201, created.text
    lifecycle_count = _line_count(store.lifecycle_log_path)
    audit_count = len(store.list_audit_for_request("cr-workshop-001"))

    replayed = client.post("/api/consult/requests", json=_payload())
    assert replayed.status_code == 201, replayed.text
    assert replayed.json() == created.json()
    assert _line_count(store.lifecycle_log_path) == lifecycle_count
    assert len(store.list_audit_for_request("cr-workshop-001")) == audit_count == 1
    assert store.list_audit_for_request("cr-workshop-001")[0].payload_hash.startswith("sha256:")


def test_create_with_same_id_and_different_body_conflicts_without_mutation(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, store = consultation_client
    assert client.post("/api/consult/requests", json=_payload()).status_code == 201
    lifecycle_count = _line_count(store.lifecycle_log_path)

    conflict = client.post(
        "/api/consult/requests",
        json=_payload(target_id="workshop-other"),
    )

    assert conflict.status_code == 409
    assert "different canonical request" in conflict.json()["detail"]
    assert _line_count(store.lifecycle_log_path) == lifecycle_count
    assert store.get_request("cr-workshop-001").target_id == "workshop-001"


def test_create_replay_survives_store_restart_and_returns_current_readback(
    consultation_client: tuple[TestClient, ConsultationStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = consultation_client
    assert client.post("/api/consult/requests", json=_payload()).status_code == 201
    assert client.post("/api/consult/requests/cr-workshop-001/submit").status_code == 200
    lifecycle_count = _line_count(store.lifecycle_log_path)
    audit_count = len(store.list_audit_for_request("cr-workshop-001"))

    restarted_store = ConsultationStore(str(store.data_dir))
    monkeypatch.setattr(consultation_main, "store", restarted_store)
    restarted_client = TestClient(consultation_main.app)

    readback = restarted_client.get("/api/consult/requests/cr-workshop-001")
    replayed = restarted_client.post("/api/consult/requests", json=_payload())

    assert readback.status_code == 200
    assert readback.json()["status"] == ConsultRequestStatus.SUBMITTED.value
    assert replayed.status_code == 201
    assert replayed.json() == readback.json()
    assert _line_count(restarted_store.lifecycle_log_path) == lifecycle_count
    assert len(restarted_store.list_audit_for_request("cr-workshop-001")) == audit_count


def test_create_replay_repairs_missing_audit_after_durable_write(
    consultation_client: tuple[TestClient, ConsultationStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = consultation_client
    append_audit = store.append_audit

    def fail_audit_once(_event: Any) -> None:
        raise RuntimeError("simulated audit append failure")

    monkeypatch.setattr(store, "append_audit", fail_audit_once)
    with pytest.raises(RuntimeError, match="simulated audit append failure"):
        client.post("/api/consult/requests", json=_payload())

    assert store.get_request("cr-workshop-001") is not None
    assert _line_count(store.lifecycle_log_path) == 1
    assert store.list_audit_for_request("cr-workshop-001") == []

    monkeypatch.setattr(store, "append_audit", append_audit)
    restarted_store = ConsultationStore(str(store.data_dir))
    monkeypatch.setattr(consultation_main, "store", restarted_store)
    restarted_client = TestClient(consultation_main.app)

    replayed = restarted_client.post("/api/consult/requests", json=_payload())

    assert replayed.status_code == 201
    assert _line_count(restarted_store.lifecycle_log_path) == 1
    assert [
        event.action
        for event in restarted_store.list_audit_for_request("cr-workshop-001")
    ] == ["request_created"]


def test_submit_replay_and_later_state_do_not_duplicate_events(
    consultation_client: tuple[TestClient, ConsultationStore],
) -> None:
    client, store = consultation_client
    assert client.post("/api/consult/requests", json=_payload()).status_code == 201

    submitted = client.post("/api/consult/requests/cr-workshop-001/submit")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == ConsultRequestStatus.SUBMITTED.value
    lifecycle_count = _line_count(store.lifecycle_log_path)
    audit_count = len(store.list_audit_for_request("cr-workshop-001"))

    replayed = client.post("/api/consult/requests/cr-workshop-001/submit")
    assert replayed.status_code == 200
    assert replayed.json() == submitted.json()
    assert _line_count(store.lifecycle_log_path) == lifecycle_count
    assert len(store.list_audit_for_request("cr-workshop-001")) == audit_count

    assigned = store.get_request("cr-workshop-001")
    assigned.status = ConsultRequestStatus.ASSIGNED
    store.put_request(assigned)
    assigned_lifecycle_count = _line_count(store.lifecycle_log_path)

    later_replay = client.post("/api/consult/requests/cr-workshop-001/submit")
    assert later_replay.status_code == 200
    assert later_replay.json()["status"] == ConsultRequestStatus.ASSIGNED.value
    assert _line_count(store.lifecycle_log_path) == assigned_lifecycle_count
    assert len(store.list_audit_for_request("cr-workshop-001")) == audit_count


def test_consultation_client_submit_request_calls_canonical_endpoint() -> None:
    client = ConsultationServiceClient(base_url="http://consultation-svc:8096")
    expected = {"request_id": "cr-workshop-001", "status": "submitted"}

    with mock.patch.object(client, "_request_json", return_value=expected) as request_json:
        assert client.submit_request("cr-workshop-001") == expected

    request_json.assert_called_once_with(
        "POST",
        "/api/consult/requests/cr-workshop-001/submit",
    )
