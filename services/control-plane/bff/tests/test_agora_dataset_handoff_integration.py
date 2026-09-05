"""Service-boundary integration proof for PRODUCT-V2-AGORA-DATASET-R3-20260813.

Acceptance Criteria:
  1. Persist one tenant-scoped Agora interaction and its evidence through the production store
  2. Create a DatasetVersion and durable downstream handoff with correlated identifiers
  3. Prove a second tenant cannot read acknowledge replay or correlate the first tenant records
  4. Keep retry and duplicate publication idempotent while preserving backlog truth
  5. Implement any missing current source edge before evidence
  6. Land one independently reviewed PR with executable service-boundary proof
"""
from __future__ import annotations

import os
import sys
import uuid


import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from services.control_plane.bff.agora.dataset_extraction.router import create_dataset_extraction_router
try:
    from services.control_plane.bff.models import OperatorIdentity
except ImportError:
    from bff.models import OperatorIdentity



def _identity(
    *,
    user_id: str = "user-alpha",
    tenant_id: str = "tenant-alpha",
    roles: list[str] | None = None,
    allowed_tenants: list[str] | None = None,
) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id=user_id,
        roles=roles or ["operator"],
        claims={
            "sub": user_id,
            "tenant_id": tenant_id,
            "allowed_tenants": allowed_tenants or [tenant_id],
        },
        token_kind="structured",
    )


def _make_client(
    store: AgoraDatasetStore | None = None,
    *,
    identity: OperatorIdentity | None = None,
) -> TestClient:
    if store is None:
        store = AgoraDatasetStore()
    app = FastAPI()

    resolved_identity = identity or _identity()

    def _extract_identity(_auth: str | None) -> OperatorIdentity:
        return resolved_identity

    def _require_read_role(current: OperatorIdentity) -> None:
        if not {"viewer", "operator", "reviewer", "approver", "admin"}.intersection(current.roles):
            raise HTTPException(status_code=403, detail="read role required")

    def _require_write_role(current: OperatorIdentity) -> None:
        if not {"operator", "reviewer", "approver", "admin"}.intersection(current.roles):
            raise HTTPException(status_code=403, detail="write role required")

    def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={"code": str(code), "message": message, "reason": reason},
        )

    _now = "2026-08-13T10:00:00Z"

    def _utc_now() -> str:
        return _now

    router = create_dataset_extraction_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_write_role,
        bff_error=_bff_error,
        utc_now=_utc_now,
        dataset_store=store,
    )
    app.include_router(router)
    return TestClient(app)


def test_ac1_persist_tenant_scoped_interaction_and_evidence() -> None:
    """Criterion 1: Persist tenant-scoped Agora interaction & evidence through store."""
    store = AgoraDatasetStore()
    client = _make_client(store, identity=_identity(user_id="user-a1", tenant_id="tenant-alpha"))

    evidence_id = "ev-ac1-001"
    ikey = f"idem-ac1-{uuid.uuid4().hex}"
    body = {
        "evidence_id": evidence_id,
        "interaction_kind": "ask",
        "persona_id": "persona-advisor",
        "session_id": "session-101",
        "content": {"question": "What is the current drawdown ceiling?"},
        "source_refs": ["workshop:ws-101", "session:session-101"],
        "learning_eligible": True,
        "captured_at": "2026-08-13T09:30:00Z",
    }

    response = client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert response.status_code == 201, response.text
    res_data = response.json()
    assert res_data["status"] == "created"
    assert res_data["idempotent"] is False
    assert res_data["data"]["tenant_id"] == "tenant-alpha"
    assert res_data["data"]["user_id"] == "user-a1"
    assert res_data["data"]["governance_boundary"] == "observe_or_learn_only"
    assert res_data["data"]["no_promote_proof"] == "agora_observe_learn_only"
    assert res_data["data"]["no_runtime_mutation_proof"] == "agora_evidence_extract_only"

    # Readback via GET endpoint
    get_resp = client.get(
        f"/bff/agora/interaction-evidence/{evidence_id}",
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert get_resp.status_code == 200
    rec = get_resp.json()["data"]
    assert rec["evidence_id"] == evidence_id
    assert rec["dataset_kind"] == "observe"


def test_ac2_create_dataset_version_and_durable_handoff() -> None:
    """Criterion 2: Create DatasetVersion & durable downstream handoff with correlated IDs."""
    store = AgoraDatasetStore()
    client = _make_client(store, identity=_identity(user_id="user-a2", tenant_id="tenant-alpha"))

    evidence_id = "ev-ac2-002"
    ikey = f"idem-ac2-{uuid.uuid4().hex}"
    body = {
        "evidence_id": evidence_id,
        "interaction_kind": "feedback",
        "persona_id": "persona-trader",
        "session_id": "session-202",
        "content": {"verdict": "useful", "rating": 5},
        "source_refs": ["proposal:prop-202"],
        "learning_eligible": True,
        "captured_at": "2026-08-13T09:40:00Z",
    }

    sub_resp = client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert sub_resp.status_code == 201
    dsv_id = sub_resp.json()["data"]["dataset_version_id"]
    assert dsv_id.startswith("dsv-"), f"Expected dsv- prefix, got {dsv_id}"

    # Run leased worker to process inbox into DatasetVersion and handoff
    proc_resp = client.post(
        "/bff/agora/dataset-worker/process",
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert proc_resp.status_code == 200
    assert proc_resp.json()["data"]["processed"] == 1

    # Get handoffs
    h_resp = client.get(
        "/bff/agora/dataset-worker/handoffs",
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert h_resp.status_code == 200
    handoffs = h_resp.json()["items"]
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert handoff["dataset_version_id"] == dsv_id
    assert handoff["handoff_id"].startswith("gh-")
    assert handoff["evidence_ids"] == [evidence_id]
    assert handoff["authority_limit"] == "Observe/Learn"
    assert handoff["ack_status"] == "pending"


def test_ac3_tenant_isolation_prevents_read_acknowledge_and_replay() -> None:
    """Criterion 3: Prove a second tenant cannot read, acknowledge, replay or correlate records."""
    store = AgoraDatasetStore()

    tenant_a_client = _make_client(
        store,
        identity=_identity(user_id="user-a", tenant_id="tenant-alpha", allowed_tenants=["tenant-alpha"]),
    )
    tenant_b_client = _make_client(
        store,
        identity=_identity(user_id="user-b", tenant_id="tenant-beta", allowed_tenants=["tenant-beta"]),
    )

    evidence_id = "ev-tenant-a-secret"
    ikey = f"idem-iso-{uuid.uuid4().hex}"
    body = {
        "evidence_id": evidence_id,
        "interaction_kind": "insight",
        "persona_id": "persona-secret",
        "content": {"secret_analysis": "Alpha tenant private signal"},
        "captured_at": "2026-08-13T09:50:00Z",
    }

    sub_resp = tenant_a_client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert sub_resp.status_code == 201
    dsv_id = sub_resp.json()["data"]["dataset_version_id"]

    # Run worker for Tenant A
    proc_resp = tenant_a_client.post(
        "/bff/agora/dataset-worker/process",
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert proc_resp.status_code == 200
    assert proc_resp.json()["data"]["processed"] == 1

    handoffs_a = tenant_a_client.get(
        "/bff/agora/dataset-worker/handoffs",
        headers={"X-Tenant-Id": "tenant-alpha"},
    ).json()["items"]
    handoff_id_a = handoffs_a[0]["handoff_id"]

    # --- Tenant B isolation assertions ---

    # 1. Tenant B cannot GET Tenant A's evidence
    get_b = tenant_b_client.get(
        f"/bff/agora/interaction-evidence/{evidence_id}",
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert get_b.status_code == 404, "Tenant B must get 404 when reading Tenant A evidence"

    # 2. Tenant B dataset projection lists 0 items
    list_b = tenant_b_client.get(
        "/bff/agora/datasets/observe",
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert list_b.status_code == 200
    assert list_b.json()["total"] == 0

    # 3. Tenant B handoffs list returns 0 items
    handoffs_b = tenant_b_client.get(
        "/bff/agora/dataset-worker/handoffs",
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert handoffs_b.status_code == 200
    assert handoffs_b.json()["total"] == 0

    # 4. Tenant B cannot acknowledge Tenant A's handoff
    ack_b = tenant_b_client.post(
        f"/bff/agora/dataset-worker/handoffs/{handoff_id_a}/ack",
        json={
            "acknowledgement_id": "ack-b-unauthorized",
            "dataset_version_id": dsv_id,
            "downstream_ref": "ds-b://observe",
        },
        headers={"Idempotency-Key": "ack-key-b", "X-Tenant-Id": "tenant-beta"},
    )
    assert ack_b.status_code == 404, "Tenant B must get 404 attempting to ack Tenant A handoff"

    # 5. Tenant B cannot replay DLQ item of Tenant A
    store._inbox[("tenant-alpha", "user-a", "ev-dlq-a")] = {
        "evidence_id": "ev-dlq-a", "tenant_id": "tenant-alpha", "user_id": "user-a",
        "idempotency_key": "fail-key-a", "request_digest": "fail-dig-a",
        "interaction_kind": "ask", "persona_id": "p", "session_id": None,
        "content": {}, "source_refs": [], "learning_eligible": True,
        "captured_at": "2026", "status": "failed", "extracted_at": "2026",
        "error_message": "test error", "created_at": "2026", "processed_at": "2026",
        "lease_owner": None, "lease_token": None, "lease_expires_at": None, "attempt_count": 1,
    }

    replay_b = tenant_b_client.post(
        "/bff/agora/dataset-worker/dlq/ev-dlq-a/replay",
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert replay_b.status_code == 404, "Tenant B must get 404 attempting to replay Tenant A DLQ"


def test_ac4_idempotent_retry_duplicate_publication_and_ack() -> None:
    """Criterion 4: Keep retry & duplicate publication idempotent while preserving backlog truth."""
    store = AgoraDatasetStore()
    client = _make_client(store, identity=_identity(user_id="user-a4", tenant_id="tenant-alpha"))

    evidence_id = "ev-ac4-004"
    ikey = f"idem-ac4-{uuid.uuid4().hex}"
    body = {
        "evidence_id": evidence_id,
        "interaction_kind": "training_example",
        "persona_id": "persona-learner",
        "content": {"example_id": "ex-101", "label": "positive"},
        "captured_at": "2026-08-13T10:00:00Z",
    }

    # First submission
    first_resp = client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert first_resp.status_code == 201
    first_data = first_resp.json()
    assert first_data["status"] == "created"
    assert first_data["idempotent"] is False
    dsv_id = first_data["data"]["dataset_version_id"]

    # Duplicate submission (same payload & same Idempotency-Key)
    second_resp = client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert second_resp.status_code == 201
    second_data = second_resp.json()
    assert second_data["status"] == "exists"
    assert second_data["idempotent"] is True
    assert second_data["data"]["dataset_version_id"] == dsv_id

    # Same Idempotency-Key with different payload -> 409 conflict
    conflicting_body = {**body, "content": {"example_id": "ex-101", "label": "negative"}}
    conflict_resp = client.post(
        "/bff/agora/interaction-evidence",
        json=conflicting_body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert conflict_resp.status_code == 409, "Different payload with same Idempotency-Key must return 409"

    # Process inbox with leased worker
    proc_resp = client.post(
        "/bff/agora/dataset-worker/process",
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert proc_resp.status_code == 200
    assert proc_resp.json()["data"]["processed"] == 1

    # Acknowledge handoff
    handoff = client.get(
        "/bff/agora/dataset-worker/handoffs",
        headers={"X-Tenant-Id": "tenant-alpha"},
    ).json()["items"][0]
    handoff_id = handoff["handoff_id"]

    ack_payload = {
        "acknowledgement_id": "ack-ac4-001",
        "dataset_version_id": dsv_id,
        "downstream_ref": "learn-pipeline://dataset/learn/v1",
    }
    ack_key = f"idem-ack-{uuid.uuid4().hex}"

    ack_1 = client.post(
        f"/bff/agora/dataset-worker/handoffs/{handoff_id}/ack",
        json=ack_payload,
        headers={"Idempotency-Key": ack_key, "X-Tenant-Id": "tenant-alpha"},
    )
    assert ack_1.status_code == 200
    assert ack_1.json()["status"] == "acknowledged"

    # Idempotent re-acknowledgement
    ack_2 = client.post(
        f"/bff/agora/dataset-worker/handoffs/{handoff_id}/ack",
        json=ack_payload,
        headers={"Idempotency-Key": ack_key, "X-Tenant-Id": "tenant-alpha"},
    )
    assert ack_2.status_code == 200
    assert ack_2.json()["status"] == "exists"
    assert ack_2.json()["idempotent"] is True

    # Acknowledgement with mismatched dataset_version_id -> 409
    bad_ack = client.post(
        f"/bff/agora/dataset-worker/handoffs/{handoff_id}/ack",
        json={**ack_payload, "dataset_version_id": "dsv-mismatched-version"},
        headers={"Idempotency-Key": f"idem-bad-{uuid.uuid4().hex}", "X-Tenant-Id": "tenant-alpha"},
    )
    assert bad_ack.status_code == 409
    assert bad_ack.json()["detail"]["reason"] == "AGORA_DATASET_VERSION_MISMATCH"


def test_ac5_source_edge_and_context_preserved() -> None:
    """Criterion 5: Implement any missing current source edge before evidence."""
    store = AgoraDatasetStore()
    client = _make_client(store, identity=_identity(user_id="user-a5", tenant_id="tenant-alpha"))

    evidence_id = "ev-ac5-edges"
    ikey = f"idem-ac5-{uuid.uuid4().hex}"
    source_refs = [
        "workshop:ws-999",
        "strategy_spec:strategy-alpha:v1",
        "decision_event:evt-555",
    ]
    body = {
        "evidence_id": evidence_id,
        "interaction_kind": "journal",
        "persona_id": "persona-researcher",
        "session_id": "session-edge-999",
        "content": {"entry": "Journal note on momentum shift"},
        "source_refs": source_refs,
        "learning_eligible": True,
        "captured_at": "2026-08-13T10:15:00Z",
    }

    sub_resp = client.post(
        "/bff/agora/interaction-evidence",
        json=body,
        headers={"Idempotency-Key": ikey, "X-Tenant-Id": "tenant-alpha"},
    )
    assert sub_resp.status_code == 201
    rec = sub_resp.json()["data"]
    assert rec["source_refs"] == source_refs
    assert rec["persona_id"] == "persona-researcher"
    assert rec["session_id"] == "session-edge-999"
    assert rec["learning_eligible"] is True
    assert rec["status"] == "pending"

    # Process and verify record in dataset
    client.post("/bff/agora/dataset-worker/process", headers={"X-Tenant-Id": "tenant-alpha"})
    obs_resp = client.get("/bff/agora/datasets/observe", headers={"X-Tenant-Id": "tenant-alpha"})
    assert obs_resp.status_code == 200
    assert obs_resp.json()["total"] == 1

