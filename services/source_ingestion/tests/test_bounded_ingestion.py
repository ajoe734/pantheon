from __future__ import annotations

from pathlib import Path

import pytest

from services.foundation import DeadLetterQueue
from services.source_ingestion import IngestManager
from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import SourceConnector, SourceEvidenceError, SourceRecord
from services.source_ingestion.scheduler import IngestBatch, IngestionScheduler, JsonlIngestScheduleStore
from services.source_search_posture import validate_source_search_posture


def _connector(connector_id: str = "conn-bounded") -> SourceConnector:
    return SourceConnector(
        connector_id=connector_id,
        source_type="internal_note",
        provider="Pantheon bounded test",
        license_scope="internal",
    )


def test_static_records_fetcher_returns_bounded_source_records(tmp_path: Path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    store.upsert_config(
        _connector("conn-static"),
        {
            "mode": "static_records",
            "next_watermark": "2026-05-01T00:00:00Z",
            "records": [
                {
                    "source_id": "src-static-1",
                    "title": "Static record",
                    "content_ref": "memory://bounded/static-1",
                    "metadata": {"access_scope": ["operator"]},
                }
            ],
        },
    )

    batch = ConfiguredConnectorFetcher(store).fetch_batch("conn-static", watermark=None)

    assert batch.next_watermark == "2026-05-01T00:00:00Z"
    assert len(batch.records) == 1
    assert batch.records[0].source_id == "src-static-1"
    assert batch.records[0].metadata["license_scope"] == "internal"


def test_external_feed_config_requires_allowed_url_prefix(tmp_path: Path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")

    with pytest.raises(SourceEvidenceError, match="allowed_url_prefixes"):
        store.upsert_config(
            _connector("conn-feed"),
            {
                "mode": "external_feed",
                "url": "https://feeds.example.test/feed.json",
            },
        )

    with pytest.raises(SourceEvidenceError, match="outside allowed_url_prefixes"):
        store.upsert_config(
            _connector("conn-feed"),
            {
                "mode": "external_feed",
                "url": "https://feeds.example.test/feed.json",
                "allowed_url_prefixes": ["https://other.example.test/"],
            },
        )


def test_scheduled_failure_goes_to_shared_dlq_and_replay_completes(tmp_path: Path) -> None:
    manager = IngestManager()
    manager.register_connector(_connector("conn-replay"))
    queue = DeadLetterQueue(tmp_path / "dlq.jsonl")
    scheduler = IngestionScheduler(
        manager=manager,
        store=JsonlIngestScheduleStore(tmp_path / "schedule.jsonl"),
        dead_letter_queue=queue,
        max_attempts=1,
    )

    failed = scheduler.run_once(
        connector_id="conn-replay",
        trace_id="trace-replay-fail",
        fetch_batch=lambda _watermark: (_ for _ in ()).throw(SourceEvidenceError("bounded failure")),
    )

    assert failed.run.status.value == "failed"
    assert failed.dlq_entries[0].tags == ("source_ingestion", "scheduled_ingest", "retry_exhausted")
    assert queue.load_from_spill() == 1

    replayed = scheduler.run_once(
        connector_id="conn-replay",
        trace_id="trace-replay-success",
        trigger_type="dlq_replay",
        fetch_batch=lambda _watermark: IngestBatch(
            records=[
                SourceRecord(
                    source_id="src-replay-1",
                    connector_id="conn-replay",
                    source_type="internal_note",
                    title="Replay record",
                    content_ref="memory://bounded/replay-1",
                )
            ],
            next_watermark="2026-05-01T01:00:00Z",
        ),
    )

    assert replayed.run.status.value == "completed"
    assert replayed.watermark is not None
    assert replayed.watermark.value == "2026-05-01T01:00:00Z"


def test_source_ingest_production_posture_fails_closed_without_postgres_backend() -> None:
    check = validate_source_search_posture(
        "source-ingest",
        env={
            "PANTHEON_SOURCE_SEARCH_POSTURE": "production",
            "DATABASE_URL": "postgresql://pantheon:pantheon@postgres:5432/pantheon",
            "SOURCE_INGEST_EVIDENCE_BACKEND": "jsonl",
            "PANTHEON_S3_ENDPOINT": "http://minio:9000",
            "PANTHEON_ARTIFACT_BUCKET": "pantheon-artifacts",
            "PANTHEON_S3_ACCESS_KEY": "pantheon",
            "PANTHEON_S3_SECRET_KEY": "pantheonminio",
        },
    )

    assert check.status == "error"
    assert "SOURCE_INGEST_EVIDENCE_BACKEND must be postgres" in "; ".join(check.errors)
