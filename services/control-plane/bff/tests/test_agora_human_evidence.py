"""Production route -> Agora owner -> durable inbox -> L5 -> L6 proof.

Set AGORA_EVIDENCE_TEST_DSN for the PostgreSQL/restart/scheduler boundary test.
Only HTTP transports are replaced in that test; all payloads come from the
human evidence POSTs and the real owner, extraction and scheduler functions.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.agora.service import AgoraEvidenceWriteService
from services.agora.store import AgoraEvidenceStore
from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from services.control_plane.bff.agora.dataset_extraction.router import create_dataset_extraction_router
from services.control_plane.bff.agora.evidence.router import create_human_evidence_router
from services.control_plane.bff.agora.evidence.worker import install_evidence_worker, run_extraction_batch
from services.control_plane.bff.auth.policy import require_operator_role, require_read_role
from services.control_plane.bff.models import OperatorIdentity, utc_now


SURFACES = {"signal-feedback": "signal_feedback", "notes": "note", "insights": "insight", "training-examples": "training_example"}


def identity(user="human-a", tenant="tenant-a", roles=None):
    return OperatorIdentity(operator_id=user, roles=roles or ["operator"], token_kind="structured",
                            claims={"sub": user, "tenant_id": tenant, "allowed_tenants": [tenant]})


def make_app(store, *, actor=None, ephemeral=True):
    actor = actor or identity()
    app = FastAPI()
    kwargs = dict(extract_identity=lambda auth: actor, require_read_role=require_read_role,
                  require_write_role=require_operator_role, utc_now=utc_now)
    app.include_router(create_human_evidence_router(dataset_store=store, allow_ephemeral=ephemeral, **kwargs))
    app.include_router(create_dataset_extraction_router(
        dataset_store=store, bff_error=lambda status, code, message, reason, **kw: HTTPException(status, detail={"reason": reason}), **kwargs))
    return app


def body(kind, **overrides):
    return {"evidence_id": f"human-{kind}", "persona_id": "persona-a", "subject_ref": "signal://actual-human-subject",
            "content": {"rationale": "Human correction: reduce exposure"}, "consent_granted": True,
            "captured_at": datetime.now(timezone.utc).isoformat(), **overrides}


def submit(client, path, payload):
    return client.post(f"/bff/agora/{path}", json=payload, headers={"Idempotency-Key": payload["evidence_id"]})


def test_each_surface_owner_roundtrip_idempotency_and_new_observe():
    store = AgoraDatasetStore(backend="memory")
    client = TestClient(make_app(store))
    since = datetime.now(timezone.utc).isoformat()
    for path, kind in SURFACES.items():
        payload = body(kind)
        response = submit(client, path, payload)
        assert response.status_code == 201, response.text
        assert submit(client, path, payload).json()["idempotent"] is True
        assert submit(client, path, {**payload, "content": {"rationale": "changed"}}).status_code == 409
        owned = AgoraEvidenceWriteService(AgoraEvidenceStore(store)).get(payload["evidence_id"], tenant_id="tenant-a", user_id="human-a")
        assert owned == response.json()["data"]
        assert owned["content"]["agora_human_evidence"]["type"] == kind
        assert owned["source_refs"] == [payload["subject_ref"]]
        assert client.get(f"/bff/agora/human-evidence/{payload['evidence_id']}").json()["data"] == owned
    observe = client.get("/bff/agora/personas/persona-a/human-evidence", params={"since": since}).json()
    assert observe["status"] == "ok" and observe["total"] == 4
    assert observe["execution_authority"] == "none"
    assert client.get("/bff/agora/personas/persona-b/human-evidence").json()["reason"] == "no_new_human_evidence"
    assert client.get("/bff/agora/personas/persona-a/human-evidence", params={"since": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}).json()["status"] == "unavailable"
    assert run_extraction_batch(store, worker_id="automatic-batch")["processed"] == 4
    assert len(store.list_by_dataset("learn", tenant_id="tenant-a", user_id="human-a")) == 2
    assert len(store.list_by_dataset("observe", tenant_id="tenant-a", user_id="human-a")) == 2
    assert run_extraction_batch(store, worker_id="retry")["processed"] == 0


@pytest.mark.parametrize("actor", [identity(user="human-b"), identity(tenant="tenant-b")])
def test_no_cross_user_or_tenant_reads(actor):
    store = AgoraDatasetStore(backend="memory")
    assert submit(TestClient(make_app(store)), "notes", body("note")).status_code == 201
    client = TestClient(make_app(store, actor=actor))
    assert client.get("/bff/agora/human-evidence/human-note").status_code == 404
    assert client.get("/bff/agora/personas/persona-a/human-evidence").json()["total"] == 0
    assert client.get("/bff/agora/personas/persona-a/human-evidence", headers={"X-Tenant-Id": "tenant-unauthorized"}).status_code == 403


def test_fail_closed_missing_owner_consent_roles_payload_and_storage(monkeypatch):
    store = AgoraDatasetStore(backend="memory")
    client = TestClient(make_app(store))
    assert client.get("/bff/agora/human-evidence/missing").json()["detail"]["reason"] == "agora_human_evidence_missing"
    assert submit(TestClient(make_app(store, ephemeral=False)), "notes", body("note")).status_code == 503
    assert submit(TestClient(make_app(store, actor=identity(roles=["viewer"]))), "notes", body("note")).status_code == 403
    for override, code in (({"consent_granted": False}, 400), ({"subject_ref": " "}, 422),
                           ({"environment_ceiling": "live"}, 422), ({"tenant_id": "other"}, 422),
                           ({"content": {"raw_transcript": "private"}}, 400),
                           ({"content": {"agora_human_evidence": {}}}, 422)):
        assert submit(client, "notes", body("note", **override)).status_code == code
    assert store.get_backlog("tenant-a", "human-a") == []
    forged = client.post("/bff/agora/interaction-evidence", json={
        "evidence_id": "forged-provenance", "interaction_kind": "note", "persona_id": "persona-a",
        "captured_at": utc_now(), "content": {"agora_human_evidence": {"actor_id": "someone-else"}},
    }, headers={"Idempotency-Key": "forged-provenance"})
    assert forged.status_code == 422
    assert store.get_backlog("tenant-a", "human-a") == []
    monkeypatch.setattr(store, "get_inbox_entry", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db down")))
    assert client.get("/bff/agora/human-evidence/missing").json()["detail"]["reason"] == "agora_evidence_owner_unreachable"


def test_redaction_retention_and_failed_extraction_reason():
    store = AgoraDatasetStore(backend="memory")
    client = TestClient(make_app(store))
    response = submit(client, "notes", body("note", content={"api_key": "test-only-secret", "rationale": "human"}))
    assert response.json()["data"]["content"]["api_key"] == "[REDACTED]"
    key = ("tenant-a", "human-a", "human-note")
    store._inbox[key]["created_at"] = (datetime.now(timezone.utc)-timedelta(days=31)).isoformat()
    assert client.get("/bff/agora/personas/persona-a/human-evidence").json()["status"] == "unavailable"
    assert client.get("/bff/agora/human-evidence/human-note").status_code == 404
    # A revoked consent is an explicit durable extraction failure, with no
    # dataset or downstream handoff. It must not become an empty success.
    assert submit(client, "training-examples", body("training_example")).status_code == 201
    store._inbox[("tenant-a", "human-a", "human-training_example")]["consent_granted"] = False
    result = run_extraction_batch(store, worker_id="fail-closed")
    assert result["failed"] >= 1
    failed = store.get_inbox_entry("human-training_example", tenant_id="tenant-a", user_id="human-a")
    assert failed["status"] == "failed" and failed["error_message"]
    assert client.get("/bff/agora/human-evidence/human-training_example").status_code == 404


def test_missing_owner_is_explicit_in_lifespan_and_route():
    app = make_app(None, ephemeral=False)
    install_evidence_worker(app, None)
    with TestClient(app) as client:
        assert app.state.agora_evidence_worker == {
            "status": "unavailable", "reason": "agora_evidence_durable_owner_unconfigured",
        }
        assert submit(client, "notes", body("note")).status_code == 503


def load_policy_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_postgres_restart_automatic_lifespan_and_real_l6_scheduler(tmp_path, monkeypatch):
    dsn = os.getenv("AGORA_EVIDENCE_TEST_DSN")
    if not dsn:
        pytest.skip("AGORA_EVIDENCE_TEST_DSN required for durable service boundary proof")
    schema = f"human_evidence_{uuid4().hex[:12]}"
    store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
    app = make_app(store, ephemeral=False)
    client = TestClient(app)
    for path, kind in SURFACES.items():
        response = submit(client, path, body(kind))
        assert response.status_code == 201, response.text
    # Simulate a process dying after claim; recovery must use the durable
    # expired lease rather than an in-process notification or manual replay.
    assert len(store.claim_inbox(
        tenant_id="tenant-a", user_id="human-a", worker_id="crashed-process", batch_size=1,
        lease_seconds=1, now=datetime.now(timezone.utc)-timedelta(minutes=1),
    )) == 1
    # A new owner/process sees the durable backlog; the app lifespan, not a
    # manual dataset endpoint, extracts and publishes it automatically.
    restarted = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
    owner = AgoraEvidenceWriteService(AgoraEvidenceStore(restarted))
    for kind in SURFACES.values():
        assert owner.get(f"human-{kind}", tenant_id="tenant-a", user_id="human-a")["content"]["agora_human_evidence"]["type"] == kind
    app = make_app(restarted, ephemeral=False)
    install_evidence_worker(app, restarted, interval_seconds=0.02)
    with TestClient(app) as bff:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            handoffs = restarted.list_handoffs(tenant_id="tenant-a", user_id="human-a")
            if len(handoffs) == 4:
                break
            time.sleep(0.02)
        assert len(handoffs) == 4
        assert bff.get("/bff/agora/personas/persona-a/human-evidence").json()["total"] == 4

        policy_dir = Path(__file__).resolve().parents[3] / "policy-learning"
        monkeypatch.syspath_prepend(str(policy_dir))
        for name, value in {
            "POLICY_LEARNING_DATA_DIR": str(tmp_path), "POLICY_LEARNING_STORE_BACKEND": "json",
            "PERSISTENCE_POSTURE": "lenient", "POLICY_LEARNING_DATASET_MODE": "product",
            "POLICY_LEARNING_SERVICE_TOKEN": "human-evidence-test-policy-token",
            "POLICY_LEARNING_SERVICE_TENANTS": "tenant-a", "AGORA_HANDOFF_SERVICE_TOKEN": "human-evidence-test-agora-token",
            "AGORA_HANDOFF_SERVICE_TENANTS": "tenant-a", "AGORA_DATASET_STORE_BACKEND": "postgres",
            "AGORA_DATASET_STORE_DSN": dsn, "AGORA_DATASET_STORE_SCHEMA": schema,
        }.items():
            monkeypatch.setenv(name, value)
        policy_store_module = load_policy_module("human_evidence_policy_store", policy_dir / "store.py")
        monkeypatch.setitem(sys.modules, "store", policy_store_module)
        policy = load_policy_module("human_evidence_policy_main", policy_dir / "main.py")
        scheduler = load_policy_module("human_evidence_scheduler", policy_dir / "scheduler_worker.py")
        policy_client = TestClient(policy.app)

        def transport(url, *, method="GET", body=None, headers=None, timeout_seconds=None):
            from urllib.parse import urlsplit
            target = bff if url.startswith("http://agora/") else policy_client
            response = target.request(method, urlsplit(url).path, json=body, headers=headers)
            assert response.status_code < 300, response.text
            return response.json()

        monkeypatch.setattr(scheduler.agora_handoff_drainer, "_http_request", transport)
        outcome = scheduler.run_intake_cycle(
            agora_url="http://agora", policy_learning_url="http://policy", tenant_id="tenant-a",
            agora_token="human-evidence-test-agora-token", policy_learning_token="human-evidence-test-policy-token", batch_size=25,
        )
        assert outcome["acked_count"] == 4, outcome
        candidates = policy.store.list_candidates()
        assert len(candidates) == 4
        assert {c["dataset_version_id"] for c in candidates} == {h["dataset_version_id"] for h in handoffs}
        assert all(c["runtime_effect"] == "none" and c["status"] == "proposed" for c in candidates)
        assert all(h["ack_status"] == "acknowledged" for h in restarted.list_handoffs(tenant_id="tenant-a", user_id="human-a"))
        # Text corrections reach L6 automatically. They contain no trajectory;
        # the real policy worker must record degradation rather than invent
        # observations/actions/rewards or train on a seed dataset.
        processed = policy_client.post(
            "/api/policy-learning/worker/process", json={"worker_id": "human-evidence-test", "batch_size": 25},
            headers={"Authorization": "Bearer human-evidence-test-policy-token", "X-Tenant-Id": "tenant-a"},
        )
        assert processed.status_code == 200, processed.text
        settled = policy.store.list_candidates()
        assert len(settled) == 4
        assert all(c["status"] == "degraded" and c["degradation"] and c["seed_fallback_used"] is False for c in settled)
