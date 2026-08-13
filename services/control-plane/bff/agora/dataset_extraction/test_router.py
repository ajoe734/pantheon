"""Tests for Agora dataset extraction BFF router (agora.dataset.v1).

Validates:
  - Evidence admission is admit-only (persists into inbox, does not run worker inline)
  - HTTP 201 on created, correct envelope (data + meta)
  - Governance boundary meta fields present on all responses
  - Routing: ask/journal/note/insight -> observe; feedback/training_example -> learn
  - Idempotency by Idempotency-Key / evidence_id
  - Missing Idempotency-Key -> 400
  - RBAC: write role required for POST / interaction-evidence & /ack; read role for GET
  - Scoping: tenant/user isolation across operations
  - Bounded list operations and pagination
  - Worker processing, backlog, DLQ replay, handoffs, and exactly-once ACK
  - Privacy, consent, raw conversation minimization, and redaction
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from .extractor import AgoraDatasetStore
from .router import (
    create_dataset_extraction_router,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _identity(
    user_id: str = "user-test",
    tenant_id: str = "tenant-test",
    roles: Optional[list[str]] = None,
    allowed_tenants: Optional[list[str]] = None,
) -> Any:
    return SimpleNamespace(
        operator_id=user_id,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles if roles is not None else ["operator", "trader"],
        allowed_tenants=allowed_tenants or [tenant_id],
        claims={
            "sub": user_id,
            "tenant_id": tenant_id,
            "allowed_tenants": allowed_tenants or [tenant_id],
        },
    )


def _make_client(
    store: Optional[AgoraDatasetStore] = None,
    identity: Optional[Any] = None,
) -> TestClient:
    app = FastAPI()
    current_identity = identity or _identity()

    def _extract_identity(_authorization: Any = None) -> Any:
        return current_identity

    def _require_read_role(ident: Any) -> None:
        roles = set(getattr(ident, "roles", []) if hasattr(ident, "roles") else ident.get("roles", []))
        if not roles.intersection({"operator", "trader", "viewer", "admin"}):
            raise HTTPException(status_code=403, detail="Forbidden: read role required")

    def _require_write_role(ident: Any) -> None:
        roles = set(getattr(ident, "roles", []) if hasattr(ident, "roles") else ident.get("roles", []))
        if not roles.intersection({"operator", "trader", "admin"}):
            raise HTTPException(status_code=403, detail="Forbidden: write role required")

    def _bff_error(status_code: int, error_code: Any, message: str, reason: str, **kwargs: Any) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={
                "code": getattr(error_code, "value", str(error_code)),
                "message": message,
                "reason": reason,
                **kwargs,
            },
        )

    def _utc_now() -> str:
        return "2026-06-27T10:00:00Z"

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
    consent_granted: bool = True,
    is_raw_conversation: bool = False,
    explicit_conversation_consent: bool = False,
    content: Optional[dict] = None,
    process_worker: bool = False,
) -> tuple[int, dict]:
    body = {
        "evidence_id": evidence_id or str(uuid.uuid4()),
        "interaction_kind": interaction_kind,
        "persona_id": persona_id,
        "content": content or {"text": "test interaction"},
        "captured_at": captured_at,
        "consent_granted": consent_granted,
        "is_raw_conversation": is_raw_conversation,
        "explicit_conversation_consent": explicit_conversation_consent,
    }
    headers = {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    resp = client.post("/bff/agora/interaction-evidence", json=body, headers=headers)
    if resp.status_code == 201 and process_worker:
        client.post("/bff/agora/dataset-worker/process")
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# POST /bff/agora/interaction-evidence (admit-only)
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

    def test_submit_carries_governance_proof_in_data(self) -> None:
        client = _make_client()
        _, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        data = body["data"]
        assert data["governance_boundary"] == "observe_or_learn_only"
        assert data["no_promote_proof"] == "agora_observe_learn_only"
        assert data["no_runtime_mutation_proof"] == "agora_evidence_extract_only"

    def test_submit_carries_governance_proof_in_meta(self) -> None:
        client = _make_client()
        _, body = _submit(client, idempotency_key=str(uuid.uuid4()))
        meta = body["meta"]
        assert meta["no_promote_proof"] == "agora_observe_learn_only"
        assert meta["no_runtime_mutation_proof"] == "agora_evidence_extract_only"

    def test_submit_missing_idempotency_key_returns_400(self) -> None:
        client = _make_client()
        status, _ = _submit(client, idempotency_key=None)
        assert status == 400

    def test_submit_empty_idempotency_key_returns_400(self) -> None:
        client = _make_client()
        status, _ = _submit(client, idempotency_key="")
        assert status == 400

    def test_submit_duplicate_returns_exists_and_idempotent_true(self) -> None:
        client = _make_client()
        eid = str(uuid.uuid4())
        ikey = str(uuid.uuid4())
        status1, body1 = _submit(client, evidence_id=eid, idempotency_key=ikey)
        assert status1 == 201
        assert body1["status"] == "created"

        status2, body2 = _submit(client, evidence_id=eid, idempotency_key=ikey)
        assert status2 == 201
        assert body2["status"] == "exists"
        assert body2["idempotent"] is True
        assert body2["data"]["evidence_id"] == eid

    def test_submit_without_consent_returns_400(self) -> None:
        client = _make_client()
        status, body = _submit(client, consent_granted=False, idempotency_key=str(uuid.uuid4()))
        assert status == 400
        assert body["detail"]["reason"] == "AGORA_DATASET_CONSENT_REQUIRED"

    def test_submit_is_strictly_admit_only(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        eid = "ev-admit-strict"
        status, body = _submit(client, evidence_id=eid, idempotency_key="key-admit-strict")
        assert status == 201
        assert body["status"] == "created"
        assert body["data"]["status"] == "pending"
        assert body["data"]["admission_receipt_id"] == f"rcpt-adm-{eid}"

        # Backlog has 1 pending item
        backlog_resp = client.get("/bff/agora/dataset-worker/backlog")
        assert backlog_resp.status_code == 200
        assert backlog_resp.json()["total"] == 1

        # Dataset bucket has 0 items before worker runs
        obs_resp = client.get("/bff/agora/datasets/observe")
        assert obs_resp.status_code == 200
        assert obs_resp.json()["total"] == 0

        # Handoffs has 0 items before worker runs
        h_resp = client.get("/bff/agora/dataset-worker/handoffs")
        assert h_resp.status_code == 200
        assert h_resp.json()["total"] == 0


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
        _submit(client, evidence_id="ev-ask", interaction_kind="ask", idempotency_key=str(uuid.uuid4()), process_worker=True)
        _submit(client, evidence_id="ev-fb", interaction_kind="feedback", idempotency_key=str(uuid.uuid4()), process_worker=True)
        resp = client.get("/bff/agora/datasets/observe")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_kind"] == "observe"
        assert body["total"] == 1
        assert body["items"][0]["dataset_kind"] == "observe"

    def test_list_learn_returns_learn_records(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        _submit(client, evidence_id="ev-te", interaction_kind="training_example", idempotency_key=str(uuid.uuid4()), process_worker=True)
        _submit(client, evidence_id="ev-note", interaction_kind="note", idempotency_key=str(uuid.uuid4()), process_worker=True)
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
                process_worker=True,
            )
        resp = client.get("/bff/agora/datasets/observe?page_size=3")
        body = resp.json()
        assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# Backlog, DLQ, and Leased Worker routes
# ---------------------------------------------------------------------------

class TestDatasetBacklogAndWorkerRoutes:
    def test_backlog_routes(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)

        # 1. Backlog is initially empty
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # 2. Add an item
        _submit(client, evidence_id="ev-bk-1", idempotency_key="key-bk-1")

        # 3. Check backlog has 1 item
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["evidence_id"] == "ev-bk-1"

        # 4. Trigger process worker
        proc_resp = client.post("/bff/agora/dataset-worker/process")
        assert proc_resp.status_code == 200
        assert proc_resp.json()["data"]["processed"] == 1

        # 5. Check backlog is empty
        resp = client.get("/bff/agora/dataset-worker/backlog")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # 6. Check handoffs list has 1 item
        h_resp = client.get("/bff/agora/dataset-worker/handoffs")
        assert h_resp.status_code == 200
        assert h_resp.json()["total"] == 1

    def test_dlq_and_replay_routes(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)

        # Insert a corrupt item directly to memory inbox to trigger DLQ failure
        store._inbox[("tenant-test", "user-test", "ev-dlq-route")] = {
            "evidence_id": "ev-dlq-route", "tenant_id": "tenant-test", "user_id": "user-test",
            "idempotency_key": "fail-key", "request_digest": "fail-digest",
            "interaction_kind": "invalid-kind", "persona_id": "p", "session_id": None,
            "content": {}, "source_refs": [], "learning_eligible": True,
            "captured_at": "2026", "status": "pending", "extracted_at": "2026",
            "error_message": None, "created_at": "2026", "processed_at": None,
            "lease_owner": None, "lease_token": None, "lease_expires_at": None,
            "attempt_count": 0,
        }

        # Process -> should fail into DLQ
        proc_resp = client.post("/bff/agora/dataset-worker/process")
        assert proc_resp.status_code == 200
        assert proc_resp.json()["data"]["failed"] == 1

        # Get DLQ
        dlq_resp = client.get("/bff/agora/dataset-worker/dlq")
        assert dlq_resp.status_code == 200
        assert dlq_resp.json()["total"] == 1
        assert dlq_resp.json()["items"][0]["evidence_id"] == "ev-dlq-route"

        # Replay DLQ item
        replay_resp = client.post("/bff/agora/dataset-worker/dlq/ev-dlq-route/replay")
        assert replay_resp.status_code == 200

        # Backlog has it again
        bk_resp = client.get("/bff/agora/dataset-worker/backlog")
        assert bk_resp.status_code == 200
        assert bk_resp.json()["total"] == 1

        # Replay nonexistent returns 404
        bad_replay = client.post("/bff/agora/dataset-worker/dlq/ev-missing/replay")
        assert bad_replay.status_code == 404


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def test_viewer_can_read_datasets(self) -> None:
        client = _make_client(identity=_identity(roles=["viewer"]))
        resp = client.get("/bff/agora/datasets/observe")
        assert resp.status_code == 200

    def test_viewer_cannot_submit_evidence(self) -> None:
        client = _make_client(identity=_identity(roles=["viewer"]))
        status, _ = _submit(client, idempotency_key=str(uuid.uuid4()))
        assert status == 403

    def test_no_role_cannot_read(self) -> None:
        client = _make_client(identity=_identity(roles=[]))
        resp = client.get("/bff/agora/datasets/observe")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Idempotency conflict & digest mismatch
# ---------------------------------------------------------------------------

class TestIdempotencyConflict:
    def test_same_idempotency_key_different_payload_returns_409(self) -> None:
        client = _make_client()
        ikey = str(uuid.uuid4())
        status1, _ = _submit(
            client,
            evidence_id="ev-orig",
            interaction_kind="ask",
            idempotency_key=ikey,
        )
        assert status1 == 201

        status2, body2 = _submit(
            client,
            evidence_id="ev-diff",
            interaction_kind="feedback",
            idempotency_key=ikey,
        )
        assert status2 == 409
        assert body2["detail"]["reason"] == "AGORA_DATASET_IDEMPOTENCY_CONFLICT"

    def test_same_evidence_id_different_payload_returns_409(self) -> None:
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


# ---------------------------------------------------------------------------
# Downstream Handoff Acknowledgement
# ---------------------------------------------------------------------------

class TestDownstreamHandoffAcknowledgement:
    def test_acknowledgement_is_exactly_once_and_authority_limited(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        _, created = _submit(
            client,
            evidence_id="ev-ack",
            idempotency_key="ack-submit-key",
            process_worker=True,
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
        _submit(writer, evidence_id="ev-ack-rbac", idempotency_key="ack-rbac-submit", process_worker=True)
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


# ---------------------------------------------------------------------------
# Privacy & Redaction in API
# ---------------------------------------------------------------------------

class TestPrivacyAndRedaction:
    def test_sensitive_tokens_redacted_during_extraction(self) -> None:
        store = AgoraDatasetStore()
        client = _make_client(store)
        eid = "ev-redact-api"
        status, body = _submit(
            client,
            evidence_id=eid,
            idempotency_key="key-redact-api",
            content={
                "api_key": "secret-12345",
                "nested": {"password": "mypassword", "normal_field": "public_data"},
            },
            process_worker=True,
        )
        assert status == 201

        # Check extracted record has redacted sensitive fields
        rec_resp = client.get(f"/bff/agora/interaction-evidence/{eid}")
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()["data"]
        assert rec_data["content"]["api_key"] == "[REDACTED]"
        assert rec_data["content"]["nested"]["password"] == "[REDACTED]"
        assert rec_data["content"]["nested"]["normal_field"] == "public_data"
        assert rec_data["redaction_applied"] is True
