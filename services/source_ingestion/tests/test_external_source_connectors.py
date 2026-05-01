from __future__ import annotations

from pathlib import Path

import pytest

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, InMemoryEvidenceRepository
from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import SourceConnector, SourceEvidenceError, SourceRecord
from services.source_ingestion.external_sources import validate_external_source_connector, validate_external_source_record


def _connector(source_type: str = "news", **overrides) -> SourceConnector:
    payload = {
        "connector_id": f"conn-{source_type}",
        "source_type": source_type,
        "provider": "Example Vendor",
        "license_scope": "vendor",
        "license_policy": {
            "license_scope": "vendor",
            "allowed_use": ["research", "search_index"],
            "policy_ref": f"source-ingest://license/{source_type}",
        },
        "metadata": {
            "entitlement_tags": [f"{source_type}-research"],
            "access_scope": ["research"],
        },
    }
    payload.update(overrides)
    return SourceConnector(**payload)


def _news_record(**metadata_overrides) -> SourceRecord:
    metadata = {
        "publisher": "Example News",
        "published_at": "2026-05-01T12:00:00Z",
        "event_time": "2026-05-01T12:00:00Z",
        "available_time": "2026-05-01T12:01:00Z",
        "body": "News evidence about ACME earnings.",
        "symbols": ["ACME"],
    }
    metadata.update(metadata_overrides)
    return SourceRecord(
        source_id="src-news-1",
        connector_id="conn-news",
        source_type="news",
        title="ACME earnings surprise",
        content_ref="https://news.example.test/acme-earnings",
        metadata=metadata,
    )


def test_news_static_connector_emits_pit_source_record_with_entitlement(tmp_path: Path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl")
    connector = _connector("news")
    store.upsert_config(
        connector,
        {
            "mode": "static_records",
            "records": [_news_record().to_dict()],
            "next_watermark": "2026-05-01T12:05:00Z",
        },
    )

    batch = ConfiguredConnectorFetcher(store).fetch_batch("conn-news", watermark=None)

    assert batch.next_watermark == "2026-05-01T12:05:00Z"
    record = batch.records[0]
    assert record.source_type.value == "news"
    assert record.metadata["license_scope"] == "vendor"
    assert record.metadata["entitlement_tags"] == ["news-research"]
    assert record.metadata["access_scope"] == ["research"]
    assert record.metadata["available_time"] == "2026-05-01T12:01:00Z"
    assert record.metadata["pit"]["validated"] is True
    assert record.metadata["content_hash"].startswith("sha256:")
    assert record.metadata["governance"]["canonical_sink"] == "SourceRecord/EvidenceBundle"
    assert record.metadata["governance"]["direct_execution_allowed"] is False


def test_external_source_record_builds_entitled_evidence_bundle() -> None:
    connector = _connector("news")
    source = validate_external_source_record(_news_record(), connector=connector)
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)
    item = EvidenceItem(
        evidence_item_id="evi-news-1",
        source_id=source.source_id,
        item_type="news_article",
        content_ref=source.content_ref,
        citation_label="Example News, 2026-05-01",
        body=str(source.metadata["body"]),
        event_time=source.metadata["event_time"],
        available_time=source.metadata["available_time"],
        confidence=0.83,
        access_scope=source.metadata["access_scope"],
        metadata={"entitlement_tags": source.metadata["entitlement_tags"]},
    )

    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Governed news evidence for ACME earnings.",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-news-1",
    )

    assert repository.get_source_record(source.source_id) == source
    assert repository.get_bundle("evbundle-news-1") == bundle
    assert bundle.license_scope == "vendor"
    assert bundle.access_scope == ("research",)
    assert bundle.available_time == "2026-05-01T12:01:00Z"
    assert bundle.entitlement_tags == ("news-research",)


def test_social_connector_requires_trust_score_and_platform_policy() -> None:
    connector = _connector("social")
    record = SourceRecord(
        source_id="src-social-1",
        connector_id="conn-social",
        source_type="social",
        title="Social mention",
        content_ref="social://example/post-1",
        metadata={
            "platform": "example-social",
            "author_id_hash": "sha256:author",
            "post_id": "post-1",
            "published_at": "2026-05-01T12:00:00Z",
            "event_time": "2026-05-01T12:00:00Z",
            "available_time": "2026-05-01T12:00:30Z",
            "platform_policy_ref": "source-ingest://policy/example-social",
            "body": "Social evidence.",
        },
    )

    with pytest.raises(SourceEvidenceError, match="trust_score"):
        validate_external_source_record(record, connector=connector)

    validated = validate_external_source_record(
        SourceRecord(
            **{
                **record.to_dict(),
                "metadata": {**dict(record.metadata), "trust_score": 0.71},
            }
        ),
        connector=connector,
    )
    assert validated.metadata["trust_score"] == 0.71
    assert validated.metadata["entitlement_tags"] == ["social-research"]


def test_alpha_db_requires_entitlement_pit_and_blocks_execution_targets() -> None:
    with pytest.raises(SourceEvidenceError, match="entitlement"):
        validate_external_source_connector(
            _connector("alpha_db", metadata={"access_scope": ["research"]})
        )

    with pytest.raises(SourceEvidenceError, match="cannot target Lean"):
        validate_external_source_connector(
            _connector("alpha_db", metadata={"entitlement_tags": ["alpha-research"], "targets": ["lean"]})
        )

    connector = _connector("alpha_db")
    valid_metadata = {
        "alpha_vendor_id": "example-alpha",
        "signal_id": "quality-score",
        "signal_version": "v1",
        "field_schema": {"score": "float"},
        "universe": ["US_EQUITY"],
        "as_of_time": "2026-05-01T12:00:00Z",
        "event_time": "2026-05-01T12:00:00Z",
        "available_time": "2026-05-01T12:10:00Z",
        "allowed_use": ["research", "experiment"],
        "body": "Alpha vendor quality score.",
    }
    with pytest.raises(SourceEvidenceError, match="signal_version"):
        validate_external_source_record(
            SourceRecord(
                source_id="src-alpha-bad",
                connector_id="conn-alpha_db",
                source_type="alpha_db",
                title="Alpha signal",
                content_ref="alpha-db://example/quality-score/v1",
                metadata={key: value for key, value in valid_metadata.items() if key != "signal_version"},
            ),
            connector=connector,
        )

    with pytest.raises(SourceEvidenceError, match="forbidden direct execution"):
        validate_external_source_record(
            SourceRecord(
                source_id="src-alpha-live",
                connector_id="conn-alpha_db",
                source_type="alpha_db",
                title="Alpha signal",
                content_ref="alpha-db://example/quality-score/v1",
                metadata={**valid_metadata, "allowed_use": ["live_trading"]},
            ),
            connector=connector,
        )

    validated = validate_external_source_record(
        SourceRecord(
            source_id="src-alpha-ok",
            connector_id="conn-alpha_db",
            source_type="alpha_db",
            title="Alpha signal",
            content_ref="alpha-db://example/quality-score/v1",
            metadata=valid_metadata,
        ),
        connector=connector,
    )
    assert validated.metadata["pit"]["validated"] is True
    assert validated.metadata["entitlement_tags"] == ["alpha_db-research"]
    assert validated.metadata["governance"]["broker_consumption"] == "not_direct_action"
