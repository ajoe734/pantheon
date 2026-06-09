from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _serve_json(payload: dict, *, robots_txt: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    robots_body = robots_txt.encode("utf-8") if robots_txt is not None else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path == "/robots.txt" and robots_body is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(robots_body)))
                self.end_headers()
                self.wfile.write(robots_body)
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

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/feed.json"


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


def test_source_evidence_normalization_sets_canonical_refs_and_dedupes_owner(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(connector_id="conn-doi-normalization", license_scope="open"),
            "trace_id": "trace-source-ingest-normalization",
            "trigger_type": "manual",
            "records": [
                _record(
                    source_id="src-paper-owner",
                    connector_id="conn-doi-normalization",
                    content_ref="https://doi.org/10.5555/Example.Paper",
                    metadata={
                        "body": "Canonical DOI evidence.",
                        "access_scope": ["research"],
                    },
                ),
                _record(
                    source_id="src-paper-duplicate",
                    connector_id="conn-doi-normalization",
                    content_ref="https://dx.doi.org/10.5555/example.paper",
                    metadata={
                        "body": "Canonical DOI evidence.",
                        "access_scope": ["research"],
                    },
                ),
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert set(body["evidence_refs"]["source_ids"]) == {"src-paper-owner"}

    sources = test_client.get("/api/source-ingest/source-records")
    assert sources.status_code == 200
    persisted_sources = sources.json()["source_records"]
    assert [source["source_id"] for source in persisted_sources] == ["src-paper-owner"]
    metadata = persisted_sources[0]["metadata"]
    assert metadata["canonical_doi"] == "10.5555/example.paper"
    assert metadata["source_dedupe_key"] == "doi:10.5555/example.paper"
    assert metadata["content_hash"].startswith("sha256:")
    assert metadata["license_scope"] == "open"
    assert metadata["access_scope"] == ["research"]

    items = test_client.get("/api/source-ingest/evidence/items")
    assert items.status_code == 200
    persisted_items = items.json()["items"]
    assert len(persisted_items) == 1
    assert persisted_items[0]["source_id"] == "src-paper-owner"
    assert persisted_items[0]["metadata"]["evidence_owner_id"] == persisted_items[0]["evidence_item_id"]


def test_registry_exposes_connector_status_policy_and_provider_examples(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-openalex-api",
                auth_type="api_key",
                secret_ref_id="env://OPENALEX_API_KEY",
                rate_limit_policy={
                    "requests_per_minute": 60,
                    "burst": 10,
                    "retry_after_seconds": 60,
                    "policy_ref": "source-ingest://policy/openalex",
                },
                license_policy={
                    "license_scope": "open",
                    "allowed_use": ["research", "search_index"],
                    "attribution_required": True,
                },
                source_metadata={
                    "display_name": "OpenAlex works",
                    "homepage_url": "https://openalex.org",
                    "tags": ["paper", "external_feed"],
                },
            ),
            "fetch": {
                "mode": "external_feed",
                "url": "https://api.openalex.org/works",
                "allowed_url_prefixes": ["https://api.openalex.org/"],
                "max_bytes": 4096,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.get("/api/source-ingest/registry")

    assert response.status_code == 200, response.text
    body = response.json()
    entry = next(item for item in body["connectors"] if item["connector_id"] == "conn-openalex-api")
    assert entry["status"] == "enabled"
    assert entry["policy"]["auth"]["auth_type"] == "api_key"
    assert entry["policy"]["auth"]["secret_ref"]["secret_ref_id"] == "env://OPENALEX_API_KEY"
    assert entry["policy"]["rate_limit"]["requests_per_minute"] == 60
    assert entry["policy"]["license"]["allowed_use"] == ["research", "search_index"]
    assert entry["policy"]["source_metadata"]["display_name"] == "OpenAlex works"
    assert entry["fetch_policy"]["mode"] == "external_feed"
    assert entry["fetch_policy"]["url_host"] == "api.openalex.org"
    assert body["provider_examples"]
    assert {example["fetch_policy"]["mode"] for example in body["provider_examples"]} == {
        "static_records",
        "external_feed",
    }
    catalog = body["financial_data_source_catalog"]
    assert catalog["schema_version"] == "financial_data_source_catalog.v1"
    assert catalog["summary"]["data_source_count"] == 6
    assert "FinMind" in catalog["summary"]["providers"]
    assert body["active_universe_policy"]["schema_version"] == "active_universe_scheduling_policy.v1"


def test_financial_data_source_catalog_endpoint_exposes_templates(client) -> None:
    test_client, _, _ = client

    response = test_client.get("/api/source-ingest/data-sources/financial-catalog")

    assert response.status_code == 200, response.text
    body = response.json()
    template_ids = {template["template_id"] for template in body["config_templates"]}
    assert body["catalog_status"] == "template_only_not_live_ingestion_claim"
    assert "template-tw-mops-official-disclosures" in template_ids
    assert "template-us-sec-edgar-filings" in template_ids
    assert body["active_universe_policy"]["summary"]["archive_baseline_rule_count"] >= 3


def test_external_http_feed_is_allowlisted_bounded_and_preserves_license_access_scope(client) -> None:
    test_client, _, _ = client
    feed_payload = {
        "next_watermark": "2026-04-28T22:00:00Z",
        "records": [
            {
                "source_id": "src-http-feed-note-1",
                "title": "HTTP feed note",
                "content_ref": "https://feeds.example.test/source/http-note-1",
                "metadata": {
                    "body": "HTTP feed evidence is bounded and allowlisted.",
                    "keywords": ["http", "feed"],
                },
            }
        ],
    }
    server, feed_url = _serve_json(feed_payload)
    try:
        configured = test_client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": _connector(
                    connector_id="conn-http-feed-notes",
                    source_type="internal_note",
                    license_scope="internal",
                ),
                "fetch": {
                    "mode": "external_feed",
                    "url": feed_url,
                    "allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"],
                    "timeout_seconds": 2,
                    "max_bytes": 4096,
                    "max_records": 3,
                    "default_access_scope": ["operator", "research"],
                },
            },
        )
        assert configured.status_code == 201, configured.text

        response = test_client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-http-feed-notes",
                "trace_id": "trace-source-ingest-http-feed",
                "trigger_type": "scheduled",
            },
        )
        assert response.status_code == 201, response.text
    finally:
        server.shutdown()
        server.server_close()

    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["watermark"]["value"] == "2026-04-28T22:00:00Z"
    metadata = body["records"][0]["metadata"]
    assert metadata["license_scope"] == "internal"
    assert metadata["access_scope"] == ["operator", "research"]
    assert metadata["source_feed_url"] == feed_url
    assert body["evidence_refs"]["knowledge_object_ids"]


def test_news_connector_ingest_preserves_entitlement_pit_on_bundle(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(
                connector_id="conn-news-vendor",
                source_type="news",
                license_scope="vendor",
                license_policy={
                    "license_scope": "vendor",
                    "allowed_use": ["research", "search_index"],
                    "policy_ref": "source-ingest://license/news-vendor",
                },
                metadata={
                    "entitlement_tags": ["news-vendor-research"],
                    "access_scope": ["research"],
                },
            ),
            "trace_id": "trace-source-ingest-news",
            "trigger_type": "manual",
            "records": [
                _record(
                    source_id="src-news-vendor-1",
                    connector_id="conn-news-vendor",
                    source_type="news",
                    title="ACME earnings surprise",
                    content_ref="https://news.example.test/acme-earnings",
                    metadata={
                        "publisher": "Example News",
                        "published_at": "2026-05-01T12:00:00Z",
                        "event_time": "2026-05-01T12:00:00Z",
                        "available_time": "2026-05-01T12:01:00Z",
                        "body": "ACME reported an earnings surprise.",
                        "keywords": ["ACME", "earnings"],
                    },
                )
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    source = test_client.get("/api/source-ingest/source-records/src-news-vendor-1")
    assert source.status_code == 200
    source_metadata = source.json()["source_record"]["metadata"]
    assert source_metadata["entitlement_tags"] == ["news-vendor-research"]
    assert source_metadata["available_time"] == "2026-05-01T12:01:00Z"
    assert source_metadata["pit"]["validated"] is True
    assert source_metadata["governance"]["direct_execution_allowed"] is False

    bundle = test_client.get(f"/api/source-ingest/evidence/bundles/{body['evidence_refs']['evidence_bundle_id']}")
    assert bundle.status_code == 200
    bundle_payload = bundle.json()["bundle"]
    assert bundle_payload["available_time"] == "2026-05-01T12:01:00Z"
    assert bundle_payload["entitlement_tags"] == ["news-vendor-research"]
    assert bundle_payload["license_scope"] == "vendor"


def test_external_file_feed_runs_under_same_allowlist_contract(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "allowlisted-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T22:30:00Z",
                "records": [
                    {
                        "source_id": "src-file-feed-note-1",
                        "title": "File feed note",
                        "content_ref": "file-feed://note-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    feed_url = feed_path.as_uri()

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-file-feed-notes", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_url,
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 4096,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-file-feed-notes",
            "trace_id": "trace-source-ingest-file-feed",
            "trigger_type": "scheduled",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["run"]["status"] == "completed"
    assert response.json()["watermark"]["value"] == "2026-04-28T22:30:00Z"


def test_external_feed_rejects_unallowlisted_url_at_configuration(client) -> None:
    test_client, _, _ = client

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-rejected-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": "https://not-allowed.example.test/feed.json",
                "allowed_url_prefixes": ["https://allowed.example.test/"],
                "max_records": 3,
            },
        },
    )

    assert configured.status_code == 400
    assert "outside allowed_url_prefixes" in configured.json()["detail"]


def test_external_http_feed_respects_robots_disallow_and_routes_to_dlq(client) -> None:
    test_client, _, _ = client
    feed_payload = {
        "next_watermark": "2026-04-28T22:45:00Z",
        "records": [
            {
                "source_id": "src-robots-denied-note-1",
                "title": "Robots denied note",
                "content_ref": "https://feeds.example.test/source/robots-denied",
            }
        ],
    }
    server, feed_url = _serve_json(
        feed_payload,
        robots_txt="User-agent: pantheon-source-ingest\nDisallow: /feed.json\n",
    )
    try:
        configured = test_client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": _connector(connector_id="conn-robots-denied", source_type="internal_note"),
                "fetch": {
                    "mode": "external_feed",
                    "url": feed_url,
                    "allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"],
                    "timeout_seconds": 2,
                    "max_bytes": 4096,
                    "max_records": 3,
                },
            },
        )
        assert configured.status_code == 201, configured.text

        failed = test_client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-robots-denied",
                "trace_id": "trace-source-ingest-robots-denied",
                "trigger_type": "scheduled",
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert "robots.txt disallows" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-robots-denied")
    assert watermark.status_code == 404


def test_external_feed_size_failure_routes_to_dlq_without_advancing_watermark(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "oversized-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T23:00:00Z",
                "records": [
                    {
                        "source_id": "src-oversized-feed-note-1",
                        "title": "Oversized feed note",
                        "content_ref": "file-feed://oversized-note-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-oversized-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_path.as_uri(),
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 16,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-oversized-feed",
            "trace_id": "trace-source-ingest-oversized-feed",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert body["watermark"] is None
    assert "exceeds fetch.max_bytes=16" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-oversized-feed")
    assert watermark.status_code == 404


def test_external_feed_record_count_failure_routes_to_dlq_without_advancing_watermark(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "too-many-records-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T23:10:00Z",
                "records": [
                    {
                        "source_id": "src-too-many-feed-note-1",
                        "title": "Too many feed note 1",
                        "content_ref": "file-feed://too-many-note-1",
                    },
                    {
                        "source_id": "src-too-many-feed-note-2",
                        "title": "Too many feed note 2",
                        "content_ref": "file-feed://too-many-note-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-too-many-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_path.as_uri(),
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 4096,
                "max_records": 1,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-too-many-feed",
            "trace_id": "trace-source-ingest-too-many-feed",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert "exceeds fetch.max_records=1" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-too-many-feed")
    assert watermark.status_code == 404


def test_configured_failure_dlq_audit_preserves_existing_watermark(client) -> None:
    test_client, _, _ = client
    connector = _connector(connector_id="conn-watermarked-failure", source_type="internal_note")
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": connector,
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:00:00Z",
                "records": [
                    {
                        "source_id": "src-watermark-baseline",
                        "title": "Watermark baseline",
                        "content_ref": "memory://watermark/baseline",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    completed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-watermarked-failure",
            "trace_id": "trace-watermark-baseline",
            "trigger_type": "scheduled",
        },
    )
    assert completed.status_code == 201, completed.text
    first_run_id = completed.json()["run"]["ingest_run_id"]

    reconfigured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": connector,
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T21:00:00Z",
                "fail_until_attempt": 3,
                "failure_reason": "bounded feed temporarily unavailable",
                "records": [
                    {
                        "source_id": "src-watermark-newer",
                        "title": "Watermark newer",
                        "content_ref": "memory://watermark/newer",
                    }
                ],
            },
        },
    )
    assert reconfigured.status_code == 201, reconfigured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-watermarked-failure",
            "trace_id": "trace-watermark-failure",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert body["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert body["watermark"]["last_ingest_run_id"] == first_run_id
    assert body["dlq_entries"][0]["event"]["payload"]["watermark"] == "2026-04-28T20:00:00Z"
    assert body["audit_actions"][0]["action_type"] == "source_ingestion.scheduled_run.dead_lettered"
    assert body["audit_actions"][0]["metadata"]["watermark"] == "2026-04-28T20:00:00Z"

    replayed_watermark = test_client.get("/api/source-ingest/watermarks/conn-watermarked-failure")
    assert replayed_watermark.status_code == 200
    assert replayed_watermark.json()["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert replayed_watermark.json()["watermark"]["last_ingest_run_id"] == first_run_id


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
