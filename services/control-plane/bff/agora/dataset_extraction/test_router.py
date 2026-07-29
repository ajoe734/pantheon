"""Route-level tests for the Agora dataset extraction BFF router.

Covers:
  - POST /bff/agora/interaction-evidence: submit, idempotent repeat
  - GET  /bff/agora/interaction-evidence/{evidence_id}: found, not-found
  - GET  /bff/agora/datasets/{dataset_kind}: list observe/learn, invalid kind
  - Governance: response always carries no_promote_proof and no_runtime_mutation_proof
  - Idempotency-Key header required on POST
  - 422 on invalid interaction_kind
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from bff.agora.dataset_extraction.router import create_dataset_extraction_router
from bff.models import OperatorIdentity


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _identity(
    *,
    user_id: str = "user-test",
    tenant_id: str = "tenant-test",
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
        store = AgoraDatasetStore()  # fresh store per test for isolation
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
        return HTTPException(status_code=status_code, detail={"code": str(code), "message": message, "reason": reason})

    _now = "2026-06-27T10:00:00Z"

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


def _submit(
    client: TestClient,
    *,
    evidence_id: str | None = None,
    interaction_kind: str = "ask",
    persona_id: str = "persona-a",
    captured_at: str = "2026-06-27T09:00:00Z",
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    body = {
        "evidence_id": evidence_id or str(uuid.uuid4()),
        "interaction_kind": interaction_kind,
        "persona_id": persona_id,
        "content": {"text": "test interaction"},
        "captured_at": captured_at,
    }
    headers = {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    resp = client.post("/bff/agora/interaction-evidence", json=body, headers=headers)
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# POST /bff/agora/interaction-evidence
# ---------------------------------------------------------------------------

class TestSubmitInteractionEvidence:
    def test_submit_returns_201(self) -> None:
        client = _make_client()
        status, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        assert status == 201

    def test_submit_response_has_status_created(self) -> None:
        client = _make_client()
        _, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        assert body["status"] == "created"

    def test_submit_not_idempotent_on_first_call(self) -> None:
        client = _make_client()
        _, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        assert body["idempotent"] is False

    def test_submit_routes_ask_to_observe(self) -> None:
        client = _make_client()
        _, body = _submit(client, interaction_kind="ask", idempotency_key=str(uuid.uuid4()))
        assert body["data"]["dataset_kind"] == "observe"

    def test_submit_routes_feedback_to_learn(self) -> None:
        client = _make_client()
        _, body = _submit(client, interaction_kind="feedback", idempotency_key=str(uuid.uuid4()))
        assert body["data"]["dataset_kind"] == "learn"

    def test_submit_routes_training_example_to_learn(self) -> None:
        client = _make_client()
        _, body = _submit(client, interaction_kind="training_example", idempotency_key=str(uuid.uuid4()))
        assert body["data"]["dataset_kind"] == "learn"

    def test_submit_response_carries_governance_proof(self) -> None:
        client = _make_client()
        _, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        meta = body["meta"]
        assert meta["no_promote_proof"] == "agora_observe_learn_only"
        assert meta["no_runtime_mutation_proof"] == "agora_evidence_extract_only"
        data = body["data"]
        assert data["governance_boundary"] == "observe_or_learn_only"
        assert data["no_promote_proof"] == "agora_observe_learn_only"
        assert data["no_runtime_mutation_proof"] == "agora_evidence_extract_only"

    def test_duplicate_evidence_id_returns_201_with_idempotent_true(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        eid = "ev-dup-001"
        ikey = str(uuid.uuid4())
        _submit(client, evidence_id=eid, idempotency_key=ikey)
        status, body = _submit(client, evidence_id=eid, idempotency_key=ikey)
        assert status == 201
        assert body["idempotent"] is True
        assert body["status"] == "exists"
        assert body["data"]["evidence_id"] == eid

    def test_missing_idempotency_key_returns_400(self) -> None:
        client = _make_client()
        # No Idempotency-Key header
        status, body = _submit(client)  # idempotency_key=None
        assert status == 400

    def test_invalid_interaction_kind_returns_422(self) -> None:
        client = _make_client()
        resp = client.post(
            "/bff/agora/interaction-evidence",
            json={
                "evidence_id": "ev-bad",
                "interaction_kind": "not_a_valid_kind",
                "persona_id": "p",
                "captured_at": "2026-06-27T00:00:00Z",
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 422

    def test_all_valid_interaction_kinds_are_accepted(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        for kind in ("ask", "feedback", "journal", "note", "insight", "training_example"):
            status, _ = _submit(
                client,
                interaction_kind=kind,
                evidence_id=f"ev-{kind}",
                idempotency_key=str(uuid.uuid4()),
            )
            assert status == 201, f"Expected 201 for kind={kind}, got {status}"


# ---------------------------------------------------------------------------
# GET /bff/agora/interaction-evidence/{evidence_id}
# ---------------------------------------------------------------------------

class TestGetInteractionEvidence:
    def test_get_returns_submitted_record(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        eid = "ev-get-001"
        _submit(client, evidence_id=eid, idempotency_key=str(uuid.uuid4()))
        resp = client.get(f"/bff/agora/interaction-evidence/{eid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["evidence_id"] == eid

    def test_get_missing_returns_404(self) -> None:
        client = _make_client()
        resp = client.get("/bff/agora/interaction-evidence/nonexistent")
        assert resp.status_code == 404

    def test_get_response_has_governance_proof_in_meta(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        eid = "ev-gov-001"
        _submit(client, evidence_id=eid, idempotency_key=str(uuid.uuid4()))
        resp = client.get(f"/bff/agora/interaction-evidence/{eid}")
        meta = resp.json()["meta"]
        assert meta["no_promote_proof"] == "agora_observe_learn_only"
        assert meta["no_runtime_mutation_proof"] == "agora_evidence_extract_only"


# ---------------------------------------------------------------------------
# GET /bff/agora/datasets/{dataset_kind}
# ---------------------------------------------------------------------------

class TestListDatasetRecords:
    def test_list_observe_returns_observe_records(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        _submit(client, evidence_id="ev-ask", interaction_kind="ask", idempotency_key=str(uuid.uuid4()))
        _submit(client, evidence_id="ev-fb", interaction_kind="feedback", idempotency_key=str(uuid.uuid4()))
        resp = client.get("/bff/agora/datasets/observe")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_kind"] == "observe"
        assert body["total"] == 1
        assert body["items"][0]["dataset_kind"] == "observe"

    def test_list_learn_returns_learn_records(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        _submit(client, evidence_id="ev-te", interaction_kind="training_example", idempotency_key=str(uuid.uuid4()))
        _submit(client, evidence_id="ev-note", interaction_kind="note", idempotency_key=str(uuid.uuid4()))
        resp = client.get("/bff/agora/datasets/learn")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_kind"] == "learn"
        assert body["total"] == 1
        assert body["items"][0]["dataset_kind"] == "learn"

    def test_list_invalid_kind_returns_422(self) -> None:
        client = _make_client()
        resp = client.get("/bff/agora/datasets/not_a_dataset")
        assert resp.status_code == 422

    def test_list_response_carries_governance_proof_in_meta(self) -> None:
        client = _make_client()
        resp = client.get("/bff/agora/datasets/observe")
        meta = resp.json()["meta"]
        assert meta["no_promote_proof"] == "agora_observe_learn_only"
        assert meta["no_runtime_mutation_proof"] == "agora_evidence_extract_only"

    def test_list_empty_dataset_returns_zero_total(self) -> None:
        client = _make_client()
        resp = client.get("/bff/agora/datasets/learn")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_page_size_limits_results(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        for i in range(10):
            _submit(
                client,
                evidence_id=f"ev-pg-{i}",
                interaction_kind="ask",
                idempotency_key=str(uuid.uuid4()),
            )
        resp = client.get("/bff/agora/datasets/observe?page_size=3")
        body = resp.json()
        assert len(body["items"]) == 3


class TestDatasetBacklogAndWorkerRoutes:
    def test_backlog_routes(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)

        # 1. Backlog is initially empty
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # 2. Add an item directly to the store inbox (or through POST /bff/agora/interaction-evidence)
        # Note: POST triggers synchronous worker in extract_evidence, so to test backlog/DLQ we can manually insert to store._inbox
        store._inbox[("tenant-test", "user-test", "ev-pending-1")] = {
            "evidence_id": "ev-pending-1", "tenant_id": "tenant-test", "user_id": "user-test",
            "idempotency_key": "pending-key", "request_digest": "pending-digest",
            "interaction_kind": "ask", "persona_id": "p", "session_id": None,
            "content": {}, "source_refs": [], "learning_eligible": True,
            "captured_at": "2026", "status": "pending", "extracted_at": "2026",
            "error_message": None, "created_at": "2026", "processed_at": None,
            "lease_owner": None, "lease_token": None, "lease_expires_at": None, "attempt_count": 0,
        }

        # Verify backlog endpoint now returns 1 item
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["evidence_id"] == "ev-pending-1"

        # 3. Manually run worker
        resp = client.post("/bff/agora/dataset-worker/process")
        assert resp.status_code == 200
        assert resp.json()["data"]["processed"] == 1

        # Backlog is empty again
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.json()["total"] == 0

        # Verify handoffs lists the new handoff
        resp = client.get("/bff/agora/dataset-worker/handoffs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["authority_limit"] == "Observe/Learn"

    def test_dlq_and_replay_routes(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)

        # 1. Add failed item to DLQ
        store._inbox[("tenant-test", "user-test", "ev-fail-1")] = {
            "evidence_id": "ev-fail-1", "tenant_id": "tenant-test", "user_id": "user-test",
            "idempotency_key": "fail-key", "request_digest": "fail-digest",
            "interaction_kind": "invalid-kind", "persona_id": "p", "session_id": None,
            "content": {}, "source_refs": [], "learning_eligible": True,
            "captured_at": "2026", "status": "failed", "extracted_at": "2026",
            "error_message": "Some validation error",
            "created_at": "2026", "processed_at": "2026",
            "lease_owner": None, "lease_token": None, "lease_expires_at": None, "attempt_count": 1,
        }

        # Verify DLQ returns it
        resp = client.get("/bff/agora/dataset-worker/dlq")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["evidence_id"] == "ev-fail-1"

        # 2. Replay DLQ item
        resp = client.post("/bff/agora/dataset-worker/dlq/ev-fail-1/replay")
        assert resp.status_code == 200

        # Now DLQ is empty, backlog has it
        resp = client.get("/bff/agora/dataset-worker/dlq")
        assert resp.json()["total"] == 0

        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["evidence_id"] == "ev-fail-1"


class TestGovernedIdentityAndTenantBoundaries:
    def test_real_operator_identity_attribute_path_succeeds(self) -> None:
        client = _make_client(identity=_identity())
        status, body = _submit(
            client,
            evidence_id="ev-real-identity",
            idempotency_key="real-identity-key",
        )
        assert status == 201
        assert body["data"]["tenant_id"] == "tenant-test"
        assert body["data"]["user_id"] == "user-test"

    def test_viewer_can_read_but_cannot_submit_process_or_replay(self) -> None:
        store = AgoraDatasetStore()
        writer = _make_client(store)
        _submit(writer, evidence_id="ev-rbac", idempotency_key="rbac-writer-key")

        viewer = _make_client(store, identity=_identity(roles=["viewer"]))
        assert viewer.get("/bff/agora/interaction-evidence/ev-rbac").status_code == 200
        assert _submit(
            viewer,
            evidence_id="ev-rbac-denied",
            idempotency_key="rbac-viewer-key",
        )[0] == 403
        assert viewer.post("/bff/agora/dataset-worker/process").status_code == 403
        assert (
            viewer.post("/bff/agora/dataset-worker/dlq/ev-rbac/replay").status_code
            == 403
        )

    def test_scoped_get_does_not_disclose_other_tenant_or_user(self) -> None:
        store = AgoraDatasetStore()
        tenant_a = _make_client(
            store,
            identity=_identity(
                user_id="user-a",
                tenant_id="tenant-a",
                allowed_tenants=["tenant-a"],
            ),
        )
        tenant_b = _make_client(
            store,
            identity=_identity(
                user_id="user-b",
                tenant_id="tenant-b",
                allowed_tenants=["tenant-b"],
            ),
        )
        assert _submit(
            tenant_a,
            evidence_id="ev-private",
            idempotency_key="tenant-a-key",
        )[0] == 201
        assert tenant_b.get("/bff/agora/interaction-evidence/ev-private").status_code == 404
        assert tenant_b.get("/bff/agora/datasets/observe").json()["total"] == 0
        assert tenant_b.get("/bff/agora/dataset-worker/handoffs").json()["total"] == 0

    def test_requested_tenant_must_be_bound_to_identity(self) -> None:
        client = _make_client(
            identity=_identity(tenant_id="tenant-a", allowed_tenants=["tenant-a"])
        )
        response = client.get(
            "/bff/agora/datasets/observe",
            headers={"X-Tenant-Id": "tenant-b"},
        )
        assert response.status_code == 403

    def test_same_idempotency_key_with_different_payload_returns_409(self) -> None:
        client = _make_client()
        assert _submit(
            client,
            evidence_id="ev-idem-a",
            captured_at="2026-06-27T01:00:00Z",
            idempotency_key="stable-key",
        )[0] == 201
        status, body = _submit(
            client,
            evidence_id="ev-idem-b",
            captured_at="2026-06-27T02:00:00Z",
            idempotency_key="stable-key",
        )
        assert status == 409
        assert "different" in str(body).lower()

    def test_same_evidence_id_with_changed_payload_returns_409(self) -> None:
        client = _make_client()
        assert _submit(
            client,
            evidence_id="ev-digest",
            captured_at="2026-06-27T01:00:00Z",
            idempotency_key="digest-key-a",
        )[0] == 201
        assert _submit(
            client,
            evidence_id="ev-digest",
            captured_at="2026-06-27T02:00:00Z",
            idempotency_key="digest-key-b",
        )[0] == 409


class TestDownstreamHandoffAcknowledgement:
    def test_acknowledgement_is_exactly_once_and_authority_limited(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        _, created = _submit(
            client,
            evidence_id="ev-ack",
            idempotency_key="ack-submit-key",
        )
        handoff = client.get("/bff/agora/dataset-worker/handoffs").json()["items"][0]
        payload = {
            "acknowledgement_id": "ack-001",
            "dataset_version_id": created["data"]["dataset_version_id"],
            "downstream_ref": "imitation-dataset://observe/42",
        }
        first = client.post(
            f"/bff/agora/dataset-worker/handoffs/{handoff['handoff_id']}/ack",
            json=payload,
            headers={"Idempotency-Key": "ack-key"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "acknowledged"
        assert first.json()["data"]["authority_limit"] == "Observe/Learn"

        second = client.post(
            f"/bff/agora/dataset-worker/handoffs/{handoff['handoff_id']}/ack",
            json=payload,
            headers={"Idempotency-Key": "ack-key"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "exists"
        assert second.json()["idempotent"] is True

        conflict = client.post(
            f"/bff/agora/dataset-worker/handoffs/{handoff['handoff_id']}/ack",
            json={**payload, "acknowledgement_id": "ack-002"},
            headers={"Idempotency-Key": "ack-key-2"},
        )
        assert conflict.status_code == 409

    def test_ack_requires_matching_dataset_version_and_write_role(self) -> None:
        store = AgoraDatasetStore()
        writer = _make_client(store)
        _submit(writer, evidence_id="ev-ack-rbac", idempotency_key="ack-rbac-submit")
        handoff = writer.get("/bff/agora/dataset-worker/handoffs").json()["items"][0]
        payload = {
            "acknowledgement_id": "ack-rbac",
            "dataset_version_id": "dsv-wrong",
            "downstream_ref": "imitation-dataset://observe/43",
        }
        mismatch = writer.post(
            f"/bff/agora/dataset-worker/handoffs/{handoff['handoff_id']}/ack",
            json=payload,
            headers={"Idempotency-Key": "ack-rbac-key"},
        )
        assert mismatch.status_code == 409

        viewer = _make_client(store, identity=_identity(roles=["viewer"]))
        denied = viewer.post(
            f"/bff/agora/dataset-worker/handoffs/{handoff['handoff_id']}/ack",
            json={**payload, "dataset_version_id": handoff["dataset_version_id"]},
            headers={"Idempotency-Key": "ack-viewer-key"},
        )
        assert denied.status_code == 403
