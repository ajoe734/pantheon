from __future__ import annotations

import pytest

from services.source_ingestion import IngestManager
from services.source_ingestion.connectors import (
    ConnectorMode,
    IngestRunStatus,
    SourceConnector,
    SourceEvidenceError,
)


def test_connector_requires_source_type_provider_and_license_scope() -> None:
    connector = SourceConnector(
        connector_id="conn-openalex",
        source_type="paper",
        provider="OpenAlex",
        license_scope="open",
        supported_modes=[ConnectorMode.BATCH],
    )

    assert connector.to_dict()["provider"] == "OpenAlex"
    assert connector.to_dict()["source_type"] == "paper"

    with pytest.raises(SourceEvidenceError, match="provider is required"):
        SourceConnector(
            connector_id="bad",
            source_type="paper",
            provider="",
            license_scope="open",
        )


def test_ingest_run_lifecycle_emits_start_and_completion_events() -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-notes",
            source_type="internal_note",
            provider="Pantheon Notes",
            license_scope="internal",
        )
    )

    run = manager.start_ingest_run(
        connector_id="conn-notes",
        trigger_type="manual",
        trace_id="trace-sd03-001",
    )
    assert run.status == IngestRunStatus.FETCHING
    assert [event.event_type for event in run.events][-1] == "IngestRunStarted"

    manager.complete_run(run.ingest_run_id, raw_count=3, normalized_count=2, rejected_count=1)

    assert run.status == IngestRunStatus.COMPLETED
    assert run.finished_at is not None
    assert [event.event_type for event in run.events][-3:] == [
        "SourceNormalizingStarted",
        "EvidenceIndexingStarted",
        "IngestRunCompleted",
    ]
    assert run.to_dict()["trace_id"] == "trace-sd03-001"


def test_ingest_run_rejects_invalid_transition() -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-repo",
            source_type="repo",
            provider="GitHub",
            license_scope="internal",
        )
    )
    run = manager.start_ingest_run(
        connector_id="conn-repo",
        trigger_type="manual",
        trace_id="trace-sd03-002",
    )

    with pytest.raises(SourceEvidenceError, match="Cannot transition"):
        manager.advance_run(run.ingest_run_id, IngestRunStatus.COMPLETED)
