from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def test_external_feed_provenance_url_is_not_treated_as_execution_route() -> None:
    connector = _connector("news")
    record = _news_record(source_feed_url="https://feeds.example.test/live/news.json")

    validated = validate_external_source_record(record, connector=connector)

    assert validated.metadata["source_feed_url"] == "https://feeds.example.test/live/news.json"
    assert validated.metadata["governance"]["direct_execution_allowed"] is False

    with pytest.raises(SourceEvidenceError, match="cannot target Lean"):
        validate_external_source_record(
            _news_record(routes=["lean"]),
            connector=connector,
        )


def _serve_live_feed(records: list[dict]) -> tuple[ThreadingHTTPServer, str]:
    body = json.dumps({"records": records}).encode("utf-8")
    robots = b"User-agent: pantheon-source-ingest\nAllow: /\n"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/robots.txt":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(robots)))
                self.end_headers()
                self.wfile.write(robots)
                return
            if self.path != "/feed.json":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/feed.json"


@pytest.fixture()
def _source_ingest_client(tmp_path):
    env_backup = {key: os.environ.get(key) for key in (
        "SOURCE_INGEST_DATA_DIR",
        "SOURCE_INGEST_STORE_PATH",
        "SOURCE_INGEST_CONNECTOR_STORE_PATH",
        "SOURCE_INGEST_EVIDENCE_STORE_PATH",
        "SOURCE_INGEST_DLQ_PATH",
        "SOURCE_INGEST_AUDIT_PATH",
        "SOURCE_INGEST_MAX_RECORDS",
    )}
    os.environ["SOURCE_INGEST_DATA_DIR"] = str(tmp_path)
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "5"
    sys.modules.pop("services.source_ingestion.main", None)
    try:
        from fastapi.testclient import TestClient
        module = importlib.import_module("services.source_ingestion.main")
        module = importlib.reload(module)
        yield TestClient(module.app), tmp_path
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_live_connector_smoke_path_enforces_governance_and_preserves_pit(
    _source_ingest_client,
) -> None:
    client, _ = _source_ingest_client

    feed_record = {
        "source_id": "src-live-smoke-news-001",
        "connector_id": "conn-live-news-smoke",
        "source_type": "news",
        "title": "Pantheon live smoke governance test",
        "content_ref": "https://feeds.test.local/live/news/001",
        "metadata": {
            "publisher": "smoke-news-vendor",
            "published_at": "2026-05-02T10:00:00Z",
            "event_time": "2026-05-02T10:00:00Z",
            "available_time": "2026-05-02T10:01:00Z",
            "body": "Bounded live connector smoke governance evidence.",
            "symbols": ["SMOKE"],
        },
    }
    server, feed_url = _serve_live_feed([feed_record])
    feed_prefix = feed_url.rsplit("/", 1)[0] + "/"
    try:
        resp = client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": {
                    "connector_id": "conn-live-news-smoke",
                    "source_type": "news",
                    "provider": "Pantheon smoke news vendor",
                    "license_scope": "vendor",
                    "license_policy": {
                        "license_scope": "vendor",
                        "allowed_use": ["research", "search_index"],
                        "policy_ref": "source-ingest://license/smoke",
                    },
                    "metadata": {
                        "entitlement_tags": ["news-smoke-research"],
                        "access_scope": ["research"],
                        "direct_execution_allowed": False,
                        "canonical_sink": "SourceRecord/EvidenceBundle",
                    },
                },
                    "fetch": {
                        "mode": "external_feed",
                        "network_scope": "internal_service",
                        "url": feed_url,
                    "allowed_url_prefixes": [feed_prefix],
                    "timeout_seconds": 5,
                    "max_bytes": 32768,
                    "max_records": 3,
                    "default_access_scope": ["research"],
                },
            },
        )
        assert resp.status_code == 201, resp.text

        run_resp = client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-live-news-smoke",
                "trace_id": "trace-live-smoke-001",
                "trigger_type": "live_connector_smoke",
            },
        )
        assert run_resp.status_code == 201, run_resp.text
        run_body = run_resp.json()
        assert run_body["run"]["status"] == "completed"

        evidence_refs = run_body.get("evidence_refs") or {}
        source_ids = evidence_refs.get("source_ids") or []
        assert "src-live-smoke-news-001" in source_ids

        src_resp = client.get("/api/source-ingest/source-records/src-live-smoke-news-001")
        assert src_resp.status_code == 200, src_resp.text
        rec = src_resp.json()["source_record"]
        md = rec["metadata"]

        assert md["license_scope"] == "vendor"
        assert "news-smoke-research" in md["entitlement_tags"]
        assert md["available_time"] == "2026-05-02T10:01:00Z"
        assert md["pit"]["validated"] is True
        assert md["governance"]["direct_execution_allowed"] is False
        assert md["governance"]["lean_consumption"] == "research_only_not_direct_action"
        assert md["governance"]["broker_consumption"] == "not_direct_action"
        assert md["governance"]["canonical_sink"] == "SourceRecord/EvidenceBundle"

        bundle_id = evidence_refs.get("evidence_bundle_id")
        assert bundle_id
        bundle_resp = client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}")
        assert bundle_resp.status_code == 200, bundle_resp.text
        bundle = bundle_resp.json()["bundle"]
        assert "src-live-smoke-news-001" in bundle["source_ids"]
        assert bundle["license_scope"] == "vendor"
        assert "research" in bundle["access_scope"]
        assert bundle["available_time"] == "2026-05-02T10:01:00Z"
        assert "news-smoke-research" in bundle["entitlement_tags"]
    finally:
        server.shutdown()
        server.server_close()


def test_live_connector_smoke_rejects_forbidden_execution_routes() -> None:
    connector = _connector("news")

    with pytest.raises(SourceEvidenceError, match="cannot target Lean"):
        validate_external_source_record(_news_record(routes=["broker"]), connector=connector)

    with pytest.raises(SourceEvidenceError, match="cannot target Lean"):
        validate_external_source_record(_news_record(routes=["live_execution"]), connector=connector)

    with pytest.raises(SourceEvidenceError, match="cannot target Lean"):
        validate_external_source_record(_news_record(routes=["order_router"]), connector=connector)

    with pytest.raises(SourceEvidenceError, match="cannot enable direct Lean"):
        validate_external_source_record(_news_record(direct_lean_feed=True), connector=connector)


def test_source_search_end_to_end_durable_readback(
    _source_ingest_client,
) -> None:
    """Prove the full source→search durable path: bounded live connector → SourceRecord/EvidenceBundle
    → durable evidence store → search index refresh → SearchGateway query with citation refs.
    No caller-supplied documents used; adapter_state must be 'durable'.
    Acceptance: P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 criteria 1+2.
    """
    from pathlib import Path

    from services.search.main import create_app as create_search_app

    client, tmp_path = _source_ingest_client

    feed_record = {
        "source_id": "src-e2e-search-smoke-001",
        "connector_id": "conn-e2e-news-search-smoke",
        "source_type": "news",
        "title": "Pantheon end-to-end source search smoke governance",
        "content_ref": "https://feeds.test.local/e2e/news/001",
        "metadata": {
            "publisher": "smoke-news-vendor",
            "published_at": "2026-05-02T10:00:00Z",
            "event_time": "2026-05-02T10:00:00Z",
            "available_time": "2026-05-02T10:01:00Z",
            "body": "End-to-end durable evidence search readback governance proof.",
            "symbols": ["SMOKE"],
        },
    }
    server, feed_url = _serve_live_feed([feed_record])
    feed_prefix = feed_url.rsplit("/", 1)[0] + "/"
    try:
        resp = client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": {
                    "connector_id": "conn-e2e-news-search-smoke",
                    "source_type": "news",
                    "provider": "Pantheon e2e search smoke vendor",
                    "license_scope": "vendor",
                    "license_policy": {
                        "license_scope": "vendor",
                        "allowed_use": ["research", "search_index"],
                        "policy_ref": "source-ingest://license/e2e-search-smoke",
                    },
                    "metadata": {
                        "entitlement_tags": ["news-e2e-search-smoke-research"],
                        "access_scope": ["research"],
                        "direct_execution_allowed": False,
                        "canonical_sink": "SourceRecord/EvidenceBundle",
                    },
                },
                    "fetch": {
                        "mode": "external_feed",
                        "network_scope": "internal_service",
                        "url": feed_url,
                    "allowed_url_prefixes": [feed_prefix],
                    "timeout_seconds": 5,
                    "max_bytes": 32768,
                    "max_records": 3,
                    "default_access_scope": ["research"],
                },
            },
        )
        assert resp.status_code == 201, resp.text

        run_resp = client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-e2e-news-search-smoke",
                "trace_id": "trace-e2e-search-smoke-001",
                "trigger_type": "live_connector_smoke",
            },
        )
        assert run_resp.status_code == 201, run_resp.text
        run_body = run_resp.json()
        assert run_body["run"]["status"] == "completed"
        evidence_refs = run_body.get("evidence_refs") or {}
        source_ids = evidence_refs.get("source_ids") or []
        assert "src-e2e-search-smoke-001" in source_ids, f"source_ids={source_ids}"

        # Verify PIT/governance on the source record
        src_resp = client.get("/api/source-ingest/source-records/src-e2e-search-smoke-001")
        assert src_resp.status_code == 200, src_resp.text
        src_md = src_resp.json()["source_record"]["metadata"]
        assert src_md["pit"]["validated"] is True
        assert src_md["available_time"] == "2026-05-02T10:01:00Z"
        assert src_md["governance"]["direct_execution_allowed"] is False
        assert src_md["governance"]["lean_consumption"] == "research_only_not_direct_action"
        assert src_md["governance"]["broker_consumption"] == "not_direct_action"

        # Verify EvidenceBundle was persisted
        bundle_id = evidence_refs.get("evidence_bundle_id")
        assert bundle_id, "expected evidence_bundle_id in evidence_refs"
        bundle_resp = client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}")
        assert bundle_resp.status_code == 200, bundle_resp.text
        bundle = bundle_resp.json()["bundle"]
        assert "src-e2e-search-smoke-001" in bundle["source_ids"]
        assert bundle["license_scope"] == "vendor"
        assert "research" in bundle["access_scope"]
        assert "news-e2e-search-smoke-research" in bundle["entitlement_tags"]

        # Prove durable search readback via the shared evidence store (no caller-supplied documents)
        evidence_path = Path(tmp_path) / "source_evidence.jsonl"
        assert evidence_path.exists(), f"evidence store not written: {evidence_path}"

        search_app = create_search_app(
            index_store_path=Path(tmp_path) / "search-index.jsonl",
            evidence_store_path=evidence_path,
            materialize_store_path=Path(tmp_path) / "search-materialize.jsonl",
            pipeline_store_path=Path(tmp_path) / "search-pipeline.jsonl",
            freshness_sla_seconds=60,
        )
        from fastapi.testclient import TestClient as _TestClient

        search_client = _TestClient(search_app)

        # Trigger index refresh — pipeline reloads repository from disk
        refresh_resp = search_client.post(
            "/api/search/index/refresh",
            json={
                "triggered_by": "live_connector_smoke",
                "trigger_ref": run_body["run"]["ingest_run_id"],
            },
        )
        assert refresh_resp.status_code == 200, refresh_resp.text
        pipeline_snap = refresh_resp.json()["pipeline_snapshot"]
        assert pipeline_snap["indexed_count"] >= 1, f"no objects indexed: {pipeline_snap}"

        # Search query against durable index (adapter_state must be 'durable')
        query_resp = search_client.post(
            "/api/search/query",
            json={
                "request_id": "req-e2e-search-smoke-001",
                "trace_id": "trace-e2e-search-smoke-001",
                "query": "durable evidence search readback governance",
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "source_types": ["news"],
                "access_context": {
                    "persona_id": "operator-workbench",
                    "workspace_id": "research-workbench",
                    "environment": "paper",
                    "access_scopes": ["research"],
                    "license_scopes": ["vendor"],
                },
                "top_k": 5,
            },
        )
        assert query_resp.status_code == 200, query_resp.text
        query_body = query_resp.json()

        assert query_body["index_adapter"]["adapter_state"] == "durable"

        results = query_body.get("results") or []
        assert results, f"expected durable search results but got none: {query_body}"

        matched_source_ids = {
            item["source_id"]
            for result in results
            for item in (result.get("matched_items") or [])
        }
        assert "src-e2e-search-smoke-001" in matched_source_ids, (
            f"source_id not in search results; matched_source_ids={matched_source_ids}"
        )

        first_result = results[0]
        assert first_result.get("evidence_bundle_id"), "search result must have evidence_bundle_id"
        assert first_result.get("citations"), "search result must have citation refs"
    finally:
        server.shutdown()
        server.server_close()
