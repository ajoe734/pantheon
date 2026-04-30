"""Tests for autonomous scheduled connector execution path."""

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
    tempdir = tempfile.mkdtemp(prefix="source_ingest_scheduled_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "10"

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
        "connector_id": "conn-sched-notes",
        "source_type": "internal_note",
        "provider": "Test scheduled",
        "license_scope": "internal",
    }
    payload.update(overrides)
    return payload


def _configure_with_records(test_client, connector_id="conn-sched-notes"):
    return test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id=connector_id),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-30T01:00:00Z",
                "records": [
                    {
                        "source_id": f"src-{connector_id}-note-1",
                        "title": "Scheduled note",
                        "content_ref": f"memory://scheduled/{connector_id}/note-1",
                        "metadata": {
                            "body": "Autonomous scheduled evidence",
                            "access_scope": ["operator"],
                        },
                    }
                ],
            },
        },
    )


def test_set_and_get_connector_schedule(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )

    response = test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 300, "enabled": True},
    )
    assert response.status_code == 200, response.text
    schedule = response.json()["schedule"]
    assert schedule["connector_id"] == "conn-sched-notes"
    assert schedule["interval_seconds"] == 300
    assert schedule["enabled"] is True

    get_response = test_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["schedule"]["interval_seconds"] == 300


def test_schedule_replays_after_reload(client) -> None:
    test_client, _, module = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    get_response = replay_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert get_response.status_code == 200
    assert get_response.json()["schedule"]["interval_seconds"] == 60


def test_run_scheduled_runs_due_connector_and_persists_evidence(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text

    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 1, "enabled": True},
    )

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 1
    assert body["summary"]["total_failed"] == 0
    assert body["ran"][0]["connector_id"] == "conn-sched-notes"
    assert body["ran"][0]["run"]["status"] == "completed"
    assert body["ran"][0]["evidence_refs"]["knowledge_object_ids"]

    source = test_client.get("/api/source-ingest/source-records/src-conn-sched-notes-note-1")
    assert source.status_code == 200


def test_run_scheduled_skips_disabled_connector(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 1, "enabled": False},
    )

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 1


def test_run_scheduled_skips_not_due_connector(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text

    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )
    # First run makes the connector not due for another 86400 seconds.
    first_run = test_client.post("/api/source-ingest/run-scheduled")
    assert first_run.status_code == 200, first_run.text
    assert first_run.json()["summary"]["total_ran"] == 1

    # Second run immediately - interval not elapsed.
    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 1


def test_set_schedule_returns_404_for_unknown_connector(client) -> None:
    test_client, _, _ = client

    response = test_client.put(
        "/api/source-ingest/connectors/conn-unknown/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert response.status_code == 404


def test_get_schedule_returns_404_when_not_configured(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )

    response = test_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert response.status_code == 404


def test_run_scheduled_returns_empty_when_no_schedules_configured(client) -> None:
    test_client, _, _ = client

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 0
    assert body["summary"]["total_failed"] == 0
    assert body["ran"] == []
