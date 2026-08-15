"""Contract tests for the durable Consultation-to-Governance handoff sink."""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.governance import main
from services.governance.record_store import JsonGovernanceRecordStore
TOKEN = "consultation-handoff-test-token"


def _headers(*, token: str | None = None, tenant: str = "tenant-a") -> dict[str, str]:
    headers = {
        "Idempotency-Key": "consultation-handoff:tenant-a:gh-001",
        "X-Pantheon-Service-Actor": "consultation-workflow-executor",
        "X-Pantheon-Tenant-Id": tenant,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _payload(*, tenant: str = "tenant-a", target_gate: str = "consultation.committee.risk.reviewed") -> dict:
    return {
        "tenant_id": tenant,
        "request_id": "cr-001",
        "handoff": {
            "handoff_id": "gh-001",
            "request_id": "cr-001",
            "target_gate": target_gate,
            "memo_ids": ["memo-001"],
            "evidence_refs": ["evidence-001"],
            "audit_refs": ["audit-001"],
            "trace_id": "trace-001",
        },
    }


def _client(tmp_path, monkeypatch) -> tuple[TestClient, JsonGovernanceRecordStore]:
    store = JsonGovernanceRecordStore(
        tmp_path / "consultation-handoffs.json",
        id_fields=("handoff_id",),
    )
    monkeypatch.setattr(main, "consultation_handoff_store", store)
    monkeypatch.setenv("CONSULTATION_HANDOFF_TOKEN", TOKEN)
    monkeypatch.setenv(
        "CONSULTATION_HANDOFF_ALLOWED_SERVICE_ACTOR",
        "consultation-workflow-executor",
    )
    monkeypatch.setenv("CONSULTATION_HANDOFF_ALLOWED_TENANTS", "tenant-a")
    return TestClient(main.app), store


def test_handoff_is_durable_exact_and_idempotent(tmp_path, monkeypatch) -> None:
    client, store = _client(tmp_path, monkeypatch)
    headers = _headers(token=TOKEN)

    first = client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=headers,
    )
    assert first.status_code == 201
    assert first.json() == {
        "acknowledged": True,
        "handoff_id": "gh-001",
        "consumer_ref": "governance-consultation-handoff",
        "idempotent": False,
    }
    assert store.get("gh-001")["target_gate"] == (
        "consultation.committee.risk.reviewed"
    )

    replay = client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True

    conflict = client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(target_gate="consultation.redteam.risk.reviewed"),
        headers=headers,
    )
    assert conflict.status_code == 409


def test_handoff_fails_closed_on_identity_and_tenant(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)

    assert client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=_headers(),
    ).status_code == 401
    assert client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=_headers(token="wrong-token"),
    ).status_code == 401
    assert client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(tenant="tenant-b"),
        headers=_headers(token=TOKEN, tenant="tenant-b"),
    ).status_code == 403

    wrong_actor = _headers(token=TOKEN)
    wrong_actor["X-Pantheon-Service-Actor"] = "operator-bff"
    assert client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=wrong_actor,
    ).status_code == 403


def test_handoff_rejects_wrong_idempotency_binding(tmp_path, monkeypatch) -> None:
    client, store = _client(tmp_path, monkeypatch)
    headers = _headers(token=TOKEN)
    headers["Idempotency-Key"] = "consultation-handoff:tenant-a:other"

    response = client.post(
        "/api/governance/consultation-handoffs",
        json=_payload(),
        headers=headers,
    )

    assert response.status_code == 422
    assert store.list_all() == []
