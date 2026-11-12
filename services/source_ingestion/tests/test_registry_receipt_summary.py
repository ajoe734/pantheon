"""Registry read projection against real connectors and snapshot writer output."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.connectors import (
    AnueTaiwanRssAdapter,
    FinMindTaiwanDatasetAdapter,
    TaifexDerivativesChipAdapter,
    TaiwanOfficialMarketDatasetAdapter,
    TdccShareholdingDistributionAdapter,
    YahooTaiwanBrokerTopAdapter,
    YahooTaiwanRssAdapter,
)
from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence
from services.source_ingestion.routers.catalog_controller import create_catalog_controller_router
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.runtime import SourceIngestionRuntime, _receipt_summary
from services.source_ingestion.scheduler import IngestReceipt


@pytest.fixture(scope="module")
def registry_fixture(tmp_path_factory):
    """Seven real adapter definitions; a 100-record run updates 96 snapshots.

    Seed 23 historical closes per symbol through the production writer, then
    refresh 96 symbols plus four corrections to prior closes. Each snapshot
    has 24 closes and their real lineage, with the governed TWSE calendar.
    No providers or hosted infrastructure are contacted.
    """
    root = tmp_path_factory.mktemp("registry_receipt")
    runtime = SourceIngestionRuntime(data_dir=root)
    adapters = (
        TaiwanOfficialMarketDatasetAdapter(),
        FinMindTaiwanDatasetAdapter(),
        TdccShareholdingDistributionAdapter(),
        TaifexDerivativesChipAdapter(),
        YahooTaiwanBrokerTopAdapter(),
        YahooTaiwanRssAdapter(),
        AnueTaiwanRssAdapter(),
    )
    for adapter in adapters:
        runtime.manager.register_connector(adapter.connector())
    connector = adapters[0].connector()
    calendar = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-11-11")
    assert calendar and calendar["holidays"] and calendar["trading_days"]
    days = []
    day = date(2026, 11, 11)
    while len(days) < 24:
        if day.isoformat() in calendar["trading_days"]:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.reverse()

    def record(index, trade_date, *, correction=False):
        symbol = f"{2300 + index}.TWSE"
        source_id = f"tw-official:tw_price_daily:TWSE:{2300 + index}:{trade_date}"
        if correction:
            source_id += ":correction"
        return SimpleNamespace(
            source_id=source_id,
            connector_id=connector.connector_id,
            content_ref=f"tw-official://tw_price_daily/TWSE/{2300 + index}/{trade_date}/{source_id}",
            is_rejected=False,
            metadata={"normalized_row": {
                "symbol_canonical": symbol,
                "trade_date": trade_date,
                "close": 100.0 + index,
                "calendar_evidence": calendar,
            }},
        )

    writer = runtime.latest_market_snapshot_store
    writer.append_normalized_records(
        [record(i, day) for i in range(96) for day in days[:-1]],
        ingest_run_id="ingest-historical-24-close-window",
        observed_at="2026-11-10T23:47:00Z",
        connector=connector,
    )
    records = [record(i, days[-1]) for i in range(96)]
    records += [record(i, days[-2], correction=True) for i in range(4)]
    batch = writer.append_normalized_records(
        records,
        ingest_run_id="ingest-faf143f61509",
        observed_at="2026-11-11T23:47:00Z",
        connector=connector,
    )
    assert batch["accepted_record_count"] == 100
    assert batch["updated_snapshot_count"] == len(batch["snapshots"]) == 96
    for snapshot in batch["snapshots"]:
        assert snapshot["calendar_evidence"] == calendar
        assert len(snapshot["closes"]) == len(snapshot["lineage"]["content_refs"]) == 24
    refs = {
        "source_ids": [r.source_id for r in records],
        "evidence_item_ids": [f"evi-{i:020d}" for i in range(100)],
        "evidence_bundle_id": "evbundle-ingest-faf143f61509",
        "knowledge_object_ids": [f"ko-{i:036d}" for i in range(100)],
        "distillation_admissions": {r.source_id: {
            "status": "admitted", "job_id": f"distill-{i:024d}", "source_digest": "a" * 64,
        } for i, r in enumerate(records)},
        "market_snapshots": batch,
    }
    receipt = IngestReceipt(
        ingest_run_id=batch["ingest_run_id"], connector_id=connector.connector_id,
        status="completed", trigger_type="scheduled", trace_id="registry-receipt-test",
        started_at="2026-11-11T23:46:00Z", finished_at="2026-11-11T23:47:00Z",
        created_at="2026-11-11T23:47:00Z", raw_count=100, normalized_count=100,
        rejected_count=0, watermark=days[-1], source_timestamp="2026-11-11T06:00:00Z",
        source_timestamp_status="valid", evidence_refs=refs,
    )
    runtime.store.upsert_receipt(receipt)
    app = FastAPI()
    app.include_router(create_catalog_controller_router(runtime))
    app.include_router(create_ingest_operations_router(runtime))
    with TestClient(app) as client:
        yield runtime, client, receipt


def _registry_receipt(payload, connector_id):
    assert len(payload["connectors"]) == 7
    return next(row["freshness"]["latest_receipt"] for row in payload["connectors"]
                if row["connector_id"] == connector_id)


def _measure(client):
    client.get("/api/source-ingest/registry").raise_for_status()  # Warm up.
    elapsed = []
    for _ in range(5):
        start = perf_counter()
        response = client.get("/api/source-ingest/registry")
        elapsed.append((perf_counter() - start) * 1000)
        assert response.status_code == 200
    return response, {"bytes": len(response.content), "median_ms": median(elapsed),
                      "samples_ms": elapsed}


def test_real_registry_size_and_local_before_after_timing(registry_fixture, monkeypatch):
    _, client, receipt = registry_fixture
    # Reproduce the original projection on exactly the same persisted fixture.
    with monkeypatch.context() as legacy:
        legacy.setattr("services.source_ingestion.runtime._receipt_summary", IngestReceipt.to_dict)
        before, before_metrics = _measure(client)
    after, after_metrics = _measure(client)
    print("REGISTRY_BENCHMARK=" + json.dumps({"before": before_metrics, "after": after_metrics}))
    assert _registry_receipt(before.json(), receipt.connector_id) == receipt.to_dict()
    assert _registry_receipt(after.json(), receipt.connector_id) == _receipt_summary(receipt)
    assert before_metrics["bytes"] > 700_000
    assert after_metrics["bytes"] < 150_000
    assert after_metrics["bytes"] < before_metrics["bytes"] / 4


def test_registry_and_freshness_are_bounded_and_full_receipt_unchanged(registry_fixture):
    runtime, client, receipt = registry_fixture
    persisted_before = runtime.SCHEDULE_STORE_PATH.read_bytes()
    snapshots_before = runtime.LATEST_MARKET_SNAPSHOT_PATH.read_bytes()
    full_before = receipt.to_dict()
    registry = client.get("/api/source-ingest/registry").json()
    summary = _registry_receipt(registry, receipt.connector_id)
    freshness = runtime._connector_freshness_summary(receipt.connector_id)
    assert freshness["latest_receipt"] == summary
    assert summary["schema_version"] == "source_ingest_receipt_summary.v1"
    for key in ("ingest_run_id", "connector_id", "status", "trigger_type", "trace_id",
                "started_at", "finished_at", "created_at", "source_timestamp",
                "source_timestamp_status", "raw_count", "normalized_count", "rejected_count", "watermark"):
        assert summary[key] == full_before[key]
    assert summary["evidence_refs"] == {
        "evidence_bundle_id": full_before["evidence_refs"]["evidence_bundle_id"],
        "source_id_count": 100, "evidence_item_id_count": 100,
        "knowledge_object_id_count": 100, "distillation_admission_count": 100,
        "market_snapshots": {"accepted_record_count": 100, "updated_snapshot_count": 96,
                             "snapshot_count": 96},
    }
    assert len(json.dumps(summary)) < 1500
    response = client.get(f"/api/source-ingest/receipts/{receipt.ingest_run_id}")
    assert response.status_code == 200
    assert response.json()["receipt"] == full_before
    assert runtime.store.get_receipt(receipt.ingest_run_id).to_dict() == full_before
    assert runtime.SCHEDULE_STORE_PATH.read_bytes() == persisted_before
    assert runtime.LATEST_MARKET_SNAPSHOT_PATH.read_bytes() == snapshots_before


def test_snapshot_and_evidence_list_growth_changes_only_counts(registry_fixture):
    runtime, client, receipt = registry_fixture
    lengths = []
    try:
        for count in (0, 4, 96, 960):
            refs = dict(receipt.evidence_refs)
            batch = dict(refs["market_snapshots"])
            batch.update(snapshots=(batch["snapshots"] * 10)[:count],
                         accepted_record_count=count, updated_snapshot_count=count)
            refs["market_snapshots"] = batch
            refs["source_ids"] = [receipt.evidence_refs["source_ids"][0]] * count
            runtime.store.upsert_receipt(replace(receipt, evidence_refs=refs))
            response = client.get("/api/source-ingest/registry")
            assert response.status_code == 200
            summary = _registry_receipt(response.json(), receipt.connector_id)
            assert summary["evidence_refs"]["market_snapshots"]["snapshot_count"] == count
            assert summary["evidence_refs"]["source_id_count"] == count
            lengths.append(len(response.content))
    finally:
        runtime.store.upsert_receipt(receipt)
    # Only the decimal widths of four counters can change, never the bodies.
    assert max(lengths) - min(lengths) <= 8
    assert max(lengths) < 150_000


@pytest.mark.parametrize("status", ["processing", "failed", "rejected", "completed"])
def test_empty_evidence_receipts_keep_identity_and_counts(registry_fixture, status):
    _, _, receipt = registry_fixture
    summary = _receipt_summary(replace(receipt, status=status, evidence_refs={},
                                       finished_at=None, source_timestamp=None))
    assert summary["status"] == status
    assert summary["ingest_run_id"] == receipt.ingest_run_id
    assert summary["finished_at"] is summary["source_timestamp"] is None
    assert summary["evidence_refs"]["evidence_bundle_id"] is None
    assert summary["evidence_refs"]["source_id_count"] == 0
    assert summary["evidence_refs"]["market_snapshots"]["snapshot_count"] == 0
