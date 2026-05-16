"""SRC-001 SourceRecord schema and ingest API contract tests."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.connectors import SourceRecordStatus, SourceType

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional in local lean test images.
    jsonschema = None


SCHEMA_PATH = Path(__file__).with_name("source_record.schema.json")


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="src001_source_ingest_")
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
        yield TestClient(module.app), Path(tempdir)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _connector(**overrides: Any) -> dict[str, Any]:
    payload = {
        "connector_id": "conn-src001-openalex",
        "source_type": "paper",
        "provider": "OpenAlex",
        "license_scope": "open",
    }
    payload.update(overrides)
    return payload


def _record(**overrides: Any) -> dict[str, Any]:
    payload = {
        "source_id": "src-src001-paper",
        "connector_id": "conn-src001-openalex",
        "source_type": "paper",
        "title": "SRC-001 SourceRecord paper",
        "content_ref": "https://doi.org/10.5555/src001.paper",
        "trace_id": "trace-src001-record",
        "metadata": {
            "body": "SRC-001 source evidence for schema and ingest API verification.",
            "access_scope": ["research"],
            "keywords": ["source-record", "ingest"],
        },
    }
    payload.update(overrides)
    return payload


def test_source_record_schema_declares_canonical_contract() -> None:
    schema = _schema()

    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "SourceRecord"
    assert schema["$id"] == "https://pantheon/source-record/v1"
    assert set(schema["properties"]["source_type"]["enum"]) == {item.value for item in SourceType}
    assert set(schema["properties"]["status"]["enum"]) == {item.value for item in SourceRecordStatus}
    for field in (
        "source_id",
        "connector_id",
        "source_type",
        "title",
        "content_ref",
        "status",
        "metadata",
        "trace_id",
        "created_at",
    ):
        assert field in schema["required"]


def test_source_records_ingest_api_persists_schema_conformant_readback(client) -> None:
    test_client, data_dir = client

    schema_response = test_client.get("/api/source-ingest/schemas/source-record")
    assert schema_response.status_code == 200
    assert schema_response.json() == _schema()

    response = test_client.post(
        "/api/source-ingest/source-records",
        json={
            "connector": _connector(),
            "trace_id": "trace-src001-ingest",
            "trigger_type": "manual",
            "next_watermark": "2026-05-16T07:00:00Z",
            "records": [_record()],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["run"]["status"] == "completed"
    assert payload["run"]["trigger_type"] == "manual"
    assert payload["watermark"]["value"] == "2026-05-16T07:00:00Z"
    assert payload["evidence_refs"]["source_ids"] == ["src-src001-paper"]
    assert payload["evidence_refs"]["evidence_bundle_id"]
    assert (data_dir / "source_evidence.jsonl").exists()

    readback = test_client.get("/api/source-ingest/source-records/src-src001-paper")
    assert readback.status_code == 200
    source = readback.json()["source_record"]
    assert source["source_id"] == "src-src001-paper"
    assert source["status"] == "normalized"
    assert source["trace_id"] == "trace-src001-record"
    assert source["metadata"]["license_scope"] == "open"
    assert source["metadata"]["access_scope"] == ["research"]
    assert source["metadata"]["source_dedupe_key"] == "doi:10.5555/src001.paper"
    assert source["metadata"]["content_hash"].startswith("sha256:")

    if jsonschema is not None:
        jsonschema.validate(instance=source, schema=_schema())


def test_source_records_ingest_api_rejects_empty_or_mismatched_records(client) -> None:
    test_client, _ = client

    empty = test_client.post(
        "/api/source-ingest/source-records",
        json={
            "connector": _connector(),
            "trace_id": "trace-src001-empty",
            "records": [],
        },
    )
    assert empty.status_code == 400
    assert "records is required" in empty.json()["detail"]

    mismatched = test_client.post(
        "/api/source-ingest/source-records",
        json={
            "connector": _connector(),
            "trace_id": "trace-src001-mismatch",
            "records": [
                _record(
                    source_id="src-src001-repo-mismatch",
                    source_type="repo",
                    content_ref="https://github.com/pantheon/example",
                )
            ],
        },
    )
    assert mismatched.status_code == 400
    assert "record source_type must match job connector" in mismatched.json()["detail"]
