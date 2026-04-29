from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_service_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_STORE_PATH": os.environ.get("SOURCE_INGEST_STORE_PATH"),
        "SOURCE_INGEST_CONNECTOR_STORE_PATH": os.environ.get("SOURCE_INGEST_CONNECTOR_STORE_PATH"),
        "SOURCE_INGEST_EVIDENCE_STORE_PATH": os.environ.get("SOURCE_INGEST_EVIDENCE_STORE_PATH"),
        "SOURCE_INGEST_DLQ_PATH": os.environ.get("SOURCE_INGEST_DLQ_PATH"),
        "SOURCE_INGEST_AUDIT_PATH": os.environ.get("SOURCE_INGEST_AUDIT_PATH"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "3"

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), Path(tempdir), module
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _connector(**overrides):
    payload = {
        "connector_id": "conn-openalex",
        "source_type": "paper",
        "provider": "OpenAlex",
        "license_scope": "open",
    }
    payload.update(overrides)
    return payload


def _record(**overrides):
    payload = {
        "source_id": "src-paper-1",
        "connector_id": "conn-openalex",
        "source_type": "paper",
        "title": "Paper 1",
        "content_ref": "https://example.test/paper-1",
    }
    payload.update(overrides)
    return payload


def test_health_exposes_storage_contract(client) -> None:
    test_client, data_dir, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "pantheon-source-ingest"
    assert body["store_path"] == str(data_dir / "ingest_schedule.jsonl")
    assert body["connector_store_path"] == str(data_dir / "connector_config.jsonl")
    assert body["source_evidence_path"] == str(data_dir / "source_evidence.jsonl")
    assert body["dlq_path"] == str(data_dir / "source_ingest_dlq.jsonl")
    assert body["audit_path"] == str(data_dir / "source_ingest_audit.jsonl")


def test_trigger_success_persists_run_and_watermark_for_replay(client) -> None:
    test_client, data_dir, module = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-success",
            "trigger_type": "manual",
            "next_watermark": "2026-04-28T18:00:00Z",
            "records": [_record()],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    run_id = body["run"]["ingest_run_id"]
    assert body["run"]["status"] == "completed"
    assert body["watermark"]["value"] == "2026-04-28T18:00:00Z"
    assert (data_dir / "ingest_schedule.jsonl").exists()

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_run = replay_client.get(f"/api/source-ingest/jobs/{run_id}")
    assert replayed_run.status_code == 200
    assert replayed_run.json()["run"]["status"] == "completed"
    replayed_watermark = replay_client.get("/api/source-ingest/watermarks/conn-openalex")
    assert replayed_watermark.status_code == 200
    assert replayed_watermark.json()["watermark"]["last_ingest_run_id"] == run_id


def test_configured_connector_fetch_runs_without_inline_records_and_persists_evidence_refs(client) -> None:
    test_client, data_dir, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-autonomous-notes", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:00:00Z",
                "records": [
                    {
                        "source_id": "src-autonomous-note-1",
                        "title": "Autonomous note",
                        "content_ref": "memory://autonomous/note-1",
                        "metadata": {
                            "body": "Autonomous source evidence persisted for downstream consumers.",
                            "access_scope": ["operator", "research"],
                            "keywords": ["autonomous", "evidence"],
                        },
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-autonomous-notes",
            "trace_id": "trace-source-ingest-autonomous",
            "trigger_type": "scheduled",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["records"][0]["source_id"] == "src-autonomous-note-1"
    assert body["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert body["evidence_refs"]["source_ids"] == ["src-autonomous-note-1"]
    evidence_item_id = body["evidence_refs"]["evidence_item_ids"][0]
    evidence_bundle_id = body["evidence_refs"]["evidence_bundle_id"]
    assert evidence_bundle_id
    assert (data_dir / "connector_config.jsonl").exists()
    assert (data_dir / "source_evidence.jsonl").exists()

    source = test_client.get("/api/source-ingest/source-records/src-autonomous-note-1")
    assert source.status_code == 200
    item = test_client.get(f"/api/source-ingest/evidence/items/{evidence_item_id}")
    assert item.status_code == 200
    assert item.json()["item"]["source_id"] == "src-autonomous-note-1"
    bundle = test_client.get(f"/api/source-ingest/evidence/bundles/{evidence_bundle_id}")
    assert bundle.status_code == 200
    assert bundle.json()["bundle"]["source_ids"] == ["src-autonomous-note-1"]

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_source = replay_client.get("/api/source-ingest/source-records/src-autonomous-note-1")
    assert replayed_source.status_code == 200
    replayed_item = replay_client.get(f"/api/source-ingest/evidence/items/{evidence_item_id}")
    assert replayed_item.status_code == 200


def test_configured_connector_preserves_per_record_access_scope_for_search_index(client) -> None:
    test_client, data_dir, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-mixed-scope-notes",
                source_type="internal_note",
                license_scope="internal",
            ),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:30:00Z",
                "records": [
                    {
                        "source_id": "src-public-momentum-note",
                        "title": "Public momentum note",
                        "content_ref": "memory://autonomous/public-momentum-note",
                        "metadata": {
                            "body": "Momentum volatility evidence visible to operator research.",
                            "access_scope": ["operator", "research"],
                            "keywords": ["momentum", "volatility"],
                        },
                    },
                    {
                        "source_id": "src-private-momentum-note",
                        "title": "Private momentum note",
                        "content_ref": "memory://autonomous/private-momentum-note",
                        "metadata": {
                            "body": "Momentum volatility evidence limited to risk committee.",
                            "access_scope": ["risk-committee"],
                            "keywords": ["momentum", "volatility"],
                        },
                    },
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-mixed-scope-notes",
            "trace_id": "trace-source-ingest-mixed-scope",
            "trigger_type": "scheduled",
        },
    )
    assert response.status_code == 201, response.text
    knowledge_object_ids = response.json()["evidence_refs"]["knowledge_object_ids"]
    assert len(knowledge_object_ids) == 2

    listed = test_client.get("/api/source-ingest/evidence/knowledge-objects")
    assert listed.status_code == 200
    by_id = {item["knowledge_object_id"]: item for item in listed.json()["knowledge_objects"]}
    assert by_id[knowledge_object_ids[0]]["access_scope"] == ["operator", "research"]
    assert by_id[knowledge_object_ids[1]]["access_scope"] == ["risk-committee"]

    from services.search.main import create_app

    search_client = TestClient(create_app(data_dir / "search-index.jsonl", data_dir / "source_evidence.jsonl"))
    search_response = search_client.post(
        "/api/search/query",
        json={
            "request_id": "search-mixed-scope",
            "trace_id": "trace-search-mixed-scope",
            "query": "momentum volatility",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["internal_note"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )
    assert search_response.status_code == 200, search_response.text
    search_payload = search_response.json()
    assert [item["result_id"] for item in search_payload["results"]] == [knowledge_object_ids[0]]
    assert search_payload["results"][0]["citations"] == ["Public momentum note"]
    assert search_payload["rejected_items_count"] == 1


def test_dlq_replay_retries_configured_failure_and_persists_status(client) -> None:
    test_client, _, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-replay-notes", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T21:00:00Z",
                "fail_until_attempt": 2,
                "failure_reason": "upstream fixture unavailable",
                "records": [
                    {
                        "source_id": "src-replayed-note-1",
                        "title": "Replayed note",
                        "content_ref": "memory://autonomous/replayed-note-1",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-replay-notes",
            "trace_id": "trace-source-ingest-replay",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    assert failed.json()["run"]["status"] == "failed"
    assert failed.json()["dlq_entries"][0]["status"] == "pending"

    replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={"tag": "retry_exhausted", "reason": "test replay after configured recovery"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["summary"]["applied"] == 1

    watermark = test_client.get("/api/source-ingest/watermarks/conn-replay-notes")
    assert watermark.status_code == 200
    assert watermark.json()["watermark"]["value"] == "2026-04-28T21:00:00Z"
    source = test_client.get("/api/source-ingest/source-records/src-replayed-note-1")
    assert source.status_code == 200
    replayed_dlq = test_client.get("/api/source-ingest/dlq?status=replayed")
    assert replayed_dlq.status_code == 200
    assert len(replayed_dlq.json()["entries"]) == 1

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    durable_replayed_dlq = replay_client.get("/api/source-ingest/dlq?status=replayed")
    assert durable_replayed_dlq.status_code == 200
    assert len(durable_replayed_dlq.json()["entries"]) == 1


def test_rejected_records_route_to_replayable_dlq_and_audit(client) -> None:
    test_client, data_dir, module = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-rejected",
            "trigger_type": "manual",
            "next_watermark": "2026-04-28T19:00:00Z",
            "records": [
                _record(
                    source_id="src-rejected-paper",
                    status="rejected",
                    metadata={"reject_reason": "license scope denied"},
                )
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "rejected"
    assert body["watermark"] is None
    assert body["dlq_entries"][0]["reason"] == "license scope denied"
    assert body["audit_actions"][0]["action_type"] == "source_ingestion.source_record.dead_lettered"
    assert (data_dir / "source_ingest_dlq.jsonl").exists()
    assert (data_dir / "source_ingest_audit.jsonl").exists()

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_dlq = replay_client.get("/api/source-ingest/dlq")
    assert replayed_dlq.status_code == 200
    assert replayed_dlq.json()["entries"][0]["reason"] == "license scope denied"
    replayed_audit = replay_client.get("/api/source-ingest/audit")
    assert replayed_audit.status_code == 200
    assert replayed_audit.json()["actions"][0]["action_type"] == "source_ingestion.source_record.dead_lettered"


def test_trigger_enforces_bounded_batch_size(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-too-large",
            "records": [
                _record(source_id="src-1"),
                _record(source_id="src-2"),
                _record(source_id="src-3"),
                _record(source_id="src-4"),
            ],
        },
    )

    assert response.status_code == 413
    assert "SOURCE_INGEST_MAX_RECORDS=3" in response.json()["detail"]
