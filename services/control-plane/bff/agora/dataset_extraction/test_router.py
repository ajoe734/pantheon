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


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_client(store: AgoraDatasetStore | None = None) -> TestClient:
    if store is None:
        store = AgoraDatasetStore()  # fresh store per test for isolation
    app = FastAPI()

    def _extract_identity(_auth: str | None) -> dict:
        return {"user_id": "user-test", "tenant_id": "tenant-test"}

    def _require_read_role(_identity: dict) -> None:
        pass

    def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
        return HTTPException(status_code=status_code, detail={"code": str(code), "message": message, "reason": reason})

    _now = "2026-06-27T10:00:00Z"

    def _utc_now() -> str:
        return _now

    router = create_dataset_extraction_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
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
