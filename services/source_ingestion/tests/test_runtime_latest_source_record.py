from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.source_ingestion.connectors import SourceRecord, SourceRecordStatus, SourceType
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.scheduler import IngestReceipt


def _make_record(
    source_id: str,
    connector_id: str = "tw-twse-tpex-official-market",
    *,
    dataset: str = "tw_price_daily",
    symbol: str = "2330",
    run_id: str = "run-001",
    available_time: str = "2026-08-28T05:30:00Z",
    created_at: str = "2026-08-30T01:00:00Z",
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id=connector_id,
        source_type=SourceType.MARKET,
        title=f"{symbol} market record",
        content_ref=f"ref://{source_id}",
        status=SourceRecordStatus.NORMALIZED,
        trace_id=f"trace-{source_id}",
        created_at=created_at,
        metadata={
            "dataset": dataset,
            "symbol": symbol,
            "source_ingest_run_id": run_id,
            "available_time": available_time,
            "normalized_row": {
                "dataset": dataset,
                "symbol": symbol,
                "available_time": available_time,
                "close": 940.0,
            },
        },
    )


def _make_receipt(
    connector_id: str = "tw-twse-tpex-official-market",
    *,
    run_id: str = "run-001",
    status: str = "completed",
    started_at: str = "2026-08-30T01:00:00Z",
    finished_at: str = "2026-08-30T01:01:00Z",
) -> IngestReceipt:
    return IngestReceipt(
        ingest_run_id=run_id,
        connector_id=connector_id,
        status=status,
        trigger_type="manual",
        trace_id=f"trace-{run_id}",
        started_at=started_at,
        finished_at=finished_at,
        raw_count=2,
        normalized_count=2,
        rejected_count=0,
        watermark=None,
        source_timestamp="2026-08-28T05:30:00Z",
        source_timestamp_status="valid",
    )


def test_latest_source_record_prioritizes_accepted_receipt_run(tmp_path: Path) -> None:
    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    connector_id = "tw-twse-tpex-official-market"

    old_record = _make_record(
        "tw-official:tw_price_daily:TWSE:2330:old",
        connector_id,
        run_id="run-old-100",
        created_at="2026-08-29T01:00:00Z",
    )
    new_record = _make_record(
        "tw-official:tw_price_daily:TWSE:2330:new",
        connector_id,
        run_id="run-new-200",
        created_at="2026-08-30T01:00:00Z",
    )
    runtime.evidence_repository.add_source_record(old_record)
    runtime.evidence_repository.add_source_record(new_record)

    receipt = _make_receipt(connector_id, run_id="run-new-200")

    latest = runtime._latest_source_record_by_connector({connector_id: [receipt]})
    assert connector_id in latest
    assert latest[connector_id].source_id == "tw-official:tw_price_daily:TWSE:2330:new"
    assert latest[connector_id].metadata["source_ingest_run_id"] == "run-new-200"


def test_latest_source_record_prioritizes_price_dataset_over_later_non_price(tmp_path: Path) -> None:
    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    connector_id = "tw-twse-tpex-official-market"

    price_record = _make_record(
        "tw-official:tw_price_daily:TWSE:2330:price",
        connector_id,
        dataset="tw_price_daily",
        symbol="2330",
        run_id="run-001",
        created_at="2026-08-30T01:00:00Z",
    )
    material_event = _make_record(
        "tw-official:tw_material_event:TWSE:2330:event",
        connector_id,
        dataset="tw_material_event",
        symbol="2330",
        run_id="run-001",
        created_at="2026-08-30T01:05:00Z",  # Created later than price record
    )
    runtime.evidence_repository.add_source_record(price_record)
    runtime.evidence_repository.add_source_record(material_event)

    receipt = _make_receipt(connector_id, run_id="run-001")

    latest = runtime._latest_source_record_by_connector({connector_id: [receipt]})
    assert latest[connector_id].source_id == "tw-official:tw_price_daily:TWSE:2330:price"
    assert latest[connector_id].metadata["dataset"] == "tw_price_daily"


def test_latest_source_record_prioritizes_active_paper_symbol_from_env(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2454.TW,3105.TWO")
    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    connector_id = "tw-twse-tpex-official-market"

    record_other = _make_record(
        "tw-official:tw_price_daily:TWSE:9999:other",
        connector_id,
        symbol="9999",
        run_id="run-001",
        created_at="2026-08-30T01:00:00Z",
    )
    record_active = _make_record(
        "tw-official:tw_price_daily:TWSE:2454:active",
        connector_id,
        symbol="2454",
        run_id="run-001",
        created_at="2026-08-30T01:00:00Z",
    )
    runtime.evidence_repository.add_source_record(record_other)
    runtime.evidence_repository.add_source_record(record_active)

    receipt = _make_receipt(connector_id, run_id="run-001")

    latest = runtime._latest_source_record_by_connector({connector_id: [receipt]})
    assert latest[connector_id].source_id == "tw-official:tw_price_daily:TWSE:2454:active"
    assert latest[connector_id].metadata["symbol"] == "2454"


def test_latest_source_record_prioritizes_newer_provider_event_time_over_later_created_historical_row(tmp_path: Path) -> None:
    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    connector_id = "tw-twse-tpex-official-market"
    run_id = "run-same-001"

    # Historical market record with older available_time/date but later created_at
    historical_record = _make_record(
        "tw-official:tw_price_daily:TWSE:2330:historical",
        connector_id,
        dataset="tw_price_daily",
        symbol="2330",
        run_id=run_id,
        available_time="2026-08-27T05:30:00Z",
        created_at="2026-08-30T02:00:00Z",
    )
    # Current market record with newer provider available_time/date but earlier created_at
    current_record = _make_record(
        "tw-official:tw_price_daily:TWSE:2330:current",
        connector_id,
        dataset="tw_price_daily",
        symbol="2330",
        run_id=run_id,
        available_time="2026-08-28T05:30:00Z",
        created_at="2026-08-30T01:00:00Z",
    )
    runtime.evidence_repository.add_source_record(historical_record)
    runtime.evidence_repository.add_source_record(current_record)

    receipt = _make_receipt(connector_id, run_id=run_id)

    latest = runtime._latest_source_record_by_connector({connector_id: [receipt]})
    assert latest[connector_id].source_id == "tw-official:tw_price_daily:TWSE:2330:current"
    assert latest[connector_id].metadata["available_time"] == "2026-08-28T05:30:00Z"
    assert latest[connector_id].metadata["source_ingest_run_id"] == run_id

