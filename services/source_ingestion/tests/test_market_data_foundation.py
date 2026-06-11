from __future__ import annotations

import gzip
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.source_health import SourceHealth, SourceHealthStore


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Yahoo Taiwan Stock</title>
    <item>
      <title>2330 TSMC broker flow update</title>
      <link>https://tw.stock.yahoo.com/news/2330-test</link>
      <guid>2330-test-guid</guid>
      <pubDate>Mon, 08 Jun 2026 06:30:00 GMT</pubDate>
      <description>Market metadata summary.</description>
    </item>
  </channel>
</rss>
"""


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_market_foundation_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
        "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": os.environ.get("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"),
        "SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT": os.environ.get("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "20"
    os.environ["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "2"
    os.environ["SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT"] = str(Path(tempdir) / "market-data-store")

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


def _news_connector(connector_id: str = "tw-yahoo-stock-rss") -> dict:
    return {
        "connector_id": connector_id,
        "source_type": "news",
        "provider": "Yahoo Taiwan Stock RSS",
        "license_scope": "rss_metadata",
        "license_policy": {
            "license_scope": "rss_metadata",
            "allowed_use": ["research", "search_index", "audit_evidence"],
            "attribution_required": True,
            "redistribution_allowed": False,
        },
        "metadata": {
            "dataset": "tw_news_metadata",
            "entitlement_tags": ["yahoo-tw-rss-public-metadata"],
            "access_scope": ["public", "research"],
            "feature_targets": ["features/tw_news_event_flags"],
            "schema_hash": "tw_yahoo_rss_news.v1",
        },
    }


def _market_connector(connector_id: str = "conn-market-foundation", *, metadata: dict | None = None) -> dict:
    connector_metadata = {
        "dataset": "tw_price_daily",
        "feature_targets": ["features/tw_returns"],
        "schema_hash": "tw_price_daily.test.v1",
    }
    connector_metadata.update(metadata or {})
    return {
        "connector_id": connector_id,
        "source_type": "market",
        "provider": "Foundation Test Market",
        "license_scope": "internal",
        "metadata": connector_metadata,
    }


def test_provider_owned_adapter_run_writes_storage_health_and_usage(client) -> None:
    test_client, _, _ = client

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _news_connector(),
            "fetch": {
                "mode": "provider_owned_adapter",
                "adapter": "YahooTaiwanRssAdapter.records_from_rss",
                "adapter_config": {
                    "feed_url": "https://tw.stock.yahoo.com/rss",
                    "max_records": 5,
                },
                "request": {
                    "rss_xml": RSS_XML,
                    "feed_url": "https://tw.stock.yahoo.com/rss",
                    "dataset": "tw_news_metadata",
                    "run_date": "2026-06-08",
                },
                "max_records": 5,
                "next_watermark": "2026-06-08T06:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "tw-yahoo-stock-rss", "trace_id": "trace-provider-owned-rss"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["records"][0]["metadata"]["provider_owned_adapter"] == "YahooTaiwanRssAdapter.records_from_rss"

    storage_refs = body["storage_refs"]
    assert storage_refs["raw_refs"][0]["row_count"] == 1
    assert storage_refs["normalized_refs"][0]["dataset"] == "tw_news_metadata"
    assert storage_refs["feature_refs"][0]["dataset"] == "features/tw_news_event_flags"
    assert Path(storage_refs["raw_refs"][0]["uri"]).exists()

    health = test_client.get("/api/source-ingest/health/tw-yahoo-stock-rss")
    assert health.status_code == 200, health.text
    health_body = health.json()
    assert health_body["status"] == "ok"
    assert health_body["last_success_at"]
    assert health_body["latest_watermark"] == "2026-06-08T06:30:00Z"
    assert health_body["row_count_last_run"] == 1
    assert health_body["metadata"]["storage_refs"]["normalized_refs"]

    usage = test_client.get("/api/source-ingest/usage?source_id=tw-yahoo-stock-rss")
    assert usage.status_code == 200, usage.text
    assert usage.json()["usage_records"][0]["ingest_run_count"] == 1


def test_market_data_storage_refs_include_raw_retention_and_gzip_policy(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(
                "conn-high-volume-market",
                metadata={
                    "dataset": "tw_broker_top",
                    "feature_targets": ["features/broker_top_concentration"],
                    "schema_hash": "tw_broker_top.test.v1",
                    "raw_storage_policy": {
                        "compression": "gzip",
                        "retention_days": 2555,
                        "retention_policy_ref": "market-data://raw-retention/tw-chip-7y",
                        "storage_class": "warm",
                    },
                },
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    {
                        "source_id": "src-high-volume-market-1",
                        "title": "Broker top row",
                        "content_ref": "market://tw_broker_top/2330/2026-06-08",
                        "metadata": {
                            "dataset": "tw_broker_top",
                            "date": "2026-06-08",
                            "symbol": "2330",
                            "body": "bulk market row",
                            "raw_row": {"broker": "test", "buy_qty": 100},
                        },
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "conn-high-volume-market", "trace_id": "trace-high-volume-market"},
    )

    assert response.status_code == 201, response.text
    raw_ref = response.json()["storage_refs"]["raw_refs"][0]
    assert raw_ref["compression"] == "gzip"
    assert raw_ref["retention_days"] == 2555
    assert raw_ref["retention_policy_ref"] == "market-data://raw-retention/tw-chip-7y"
    assert raw_ref["storage_class"] == "warm"
    assert raw_ref["uri"].endswith(".jsonl.gz")
    with gzip.open(raw_ref["uri"], "rt", encoding="utf-8") as handle:
        raw_line = json.loads(handle.readline())
    assert raw_line["source_id"] == "src-high-volume-market-1"


def test_zero_row_runs_fail_unless_provider_marks_no_new_data(client) -> None:
    test_client, _, _ = client
    connector = _market_connector("conn-zero-row")
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={"connector": connector, "fetch": {"mode": "static_records", "records": []}},
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "conn-zero-row", "trace_id": "trace-zero-row-fail"},
    )
    assert failed.status_code == 201, failed.text
    assert failed.json()["run"]["status"] == "failed"
    health = test_client.get("/api/source-ingest/health/conn-zero-row").json()
    assert health["status"] == "failed"
    assert health["last_failure_at"]

    configured_ok = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector("conn-zero-row-ok"),
            "fetch": {
                "mode": "static_records",
                "records": [],
                "allow_empty": True,
                "empty_reason": "no_new_data",
            },
        },
    )
    assert configured_ok.status_code == 201, configured_ok.text
    ok = test_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "conn-zero-row-ok", "trace_id": "trace-zero-row-ok"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["run"]["status"] == "completed"
    assert test_client.get("/api/source-ingest/health/conn-zero-row-ok").json()["status"] == "ok"


def test_active_universe_schedule_fans_out_symbol_batches(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector("conn-active-universe"),
            "fetch": {
                "mode": "static_records",
                "records": [],
                "allow_empty": True,
                "empty_reason": "no_new_data",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/active-universe/schedule",
        json={
            "run_date": "2026-06-08",
            "members": [
                {"symbol": "2330", "tier": "core_universe"},
                {"symbol": "2317", "tier": "candidate_universe"},
                {"symbol": "2454", "tier": "candidate_universe"},
                {"symbol": "6488", "tier": "archive_universe"},
            ],
            "rules": [
                {
                    "connector_id": "conn-active-universe",
                    "dataset": "tw_price_daily",
                    "eligible_tiers": ["core_universe", "candidate_universe"],
                    "cadence": "daily_after_close",
                    "max_symbols_per_run": 2,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["job_count"] == 2
    assert [job["symbols"] for job in body["jobs"]] == [["2330", "2317"], ["2454"]]
    assert body["summary"]["enqueued_count"] == 2
    assert any(item["symbol"] == "6488" and item["reason"] == "not-in-universe" for item in body["skipped"])

    frontier = test_client.get("/api/source-ingest/frontier")
    assert frontier.status_code == 200
    assert [item["job_parameters"]["dataset"] for item in frontier.json()["frontier"]] == [
        "tw_price_daily",
        "tw_price_daily",
    ]


def test_gap_report_cli_classifies_credential_and_universe_gaps(tmp_path) -> None:
    members_path = tmp_path / "members.json"
    health_path = tmp_path / "health.jsonl"
    output_path = tmp_path / "gap-report.md"
    json_path = tmp_path / "gap-report.json"
    members_path.write_text(
        json.dumps(
            [
                {"symbol": "2330", "tier": "core_universe", "market": "TW"},
                {"symbol": "6488", "tier": "archive_universe", "market": "TW"},
            ]
        ),
        encoding="utf-8",
    )
    store = SourceHealthStore.from_jsonl(health_path)
    store.upsert(
        SourceHealth(
            source_id="tw-finmind-broker-daily-report",
            source_kind="data_source",
            status="failed",
            metadata={"source_error": "credential_unavailable: FINMIND_API_TOKEN"},
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/source_ingest_gap_report.py",
            "--members-json",
            str(members_path),
            "--health-store",
            str(health_path),
            "--run-date",
            "2026-06-08",
            "--output",
            str(output_path),
            "--json-output",
            str(json_path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["gap_summary"]["credential"] >= 1
    assert report["gap_summary"]["not-in-universe"] >= 1
    gap_connectors = {gap["connector_id"] for gap in report["gaps"]}
    assert "tw-tdcc-shareholding-distribution" in gap_connectors
    assert "tw-taifex-futures-options-chip" in gap_connectors
    assert "credential" in output_path.read_text(encoding="utf-8")
