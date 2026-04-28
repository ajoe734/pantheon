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
