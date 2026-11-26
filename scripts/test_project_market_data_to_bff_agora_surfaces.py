import http.server
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.project_market_data_to_bff_agora_surfaces import (
    PROJECTOR,
    _fetch_source_ingest_json,
    _get_connector_freshness,
    _get_connector_readback,
    _get_source_records,
    project,
    write_projection,
)
from scripts.run_isolated_l12_runtime_e2e import _mint_projector_service_jwt
from services.source_ingestion.routers.ingest_operations import _source_read_tenant


REPO_ROOT = Path(__file__).resolve().parents[1]


def _record(source_id: str, symbol: str, date: str, close: float) -> dict:
    return {
        "source_id": source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": "ingest-live-001",
            "normalized_row": {
                "dataset": "tw_price_daily", "date": date, "available_time": date,
                "symbol": symbol, "market": "TW", "venue": "TWSE", "name": "TSMC",
                "close": close, "change": 5.0, "volume": 1000,
            },
        },
    }


def test_projects_latest_real_row_with_provenance() -> None:
    stores = project(
        [
            _record("tw-official:tw_price_daily:TWSE:2330:old", "2330", "2026-07-06", 900),
            _record("tw-official:tw_price_daily:TWSE:2330:new", "2330", "2026-07-07", 905),
        ],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-07-07T06:00:00Z",
            "next_run_at": "2026-07-08T06:00:00Z",
            "stale_threshold_seconds": 172800,
            "last_typed_failure": None,
        },
        now=datetime(2026, 7, 7, 6, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    signal = stores["agora_signals"]["market-signal-2330-2026-07-07"]
    assert market["close"] == 905
    assert market["source_ref"] == "source_ingest:tw-official:tw_price_daily:TWSE:2330:new"
    assert market["ingestRunId"] == "ingest-live-001"
    assert market["freshnessStatus"] == "fresh"
    assert market["stale"] is False
    assert market["source_proof_receipt_id"] == "spr-tw-twse-tpex-official-market:ingest-live-001:tw-official:tw_price_daily:TWSE:2330:new"
    assert market["sourceProofReceiptId"] == "spr-tw-twse-tpex-official-market:ingest-live-001:tw-official:tw_price_daily:TWSE:2330:new"
    assert market["freshness"] == {
        "schemaVersion": "agora_source_freshness.v1",
        "status": "fresh",
        "stale": False,
        "lastSuccessAt": "2026-07-07T06:00:00Z",
        "sourceTimestamp": "2026-07-07",
        "sourceTimeStatus": "valid",
        "ageSeconds": 21600,
        "staleThresholdSeconds": 172800,
        "nextRunAt": "2026-07-08T06:00:00Z",
        "lastTypedFailure": None,
        "source_proof_receipt_id": "spr-tw-twse-tpex-official-market:ingest-live-001:tw-official:tw_price_daily:TWSE:2330:new",
        "sourceProofReceiptId": "spr-tw-twse-tpex-official-market:ingest-live-001:tw-official:tw_price_daily:TWSE:2330:new",
    }
    assert signal["projectionOwner"] == PROJECTOR


def test_projects_weekend_friday_close_as_fresh() -> None:
    # Friday 2026-08-28 official close projected on Sunday 2026-08-30 with fresh receipt
    stores = project(
        [_record("tw-official:tw_price_daily:TWSE:2330:fri", "2330", "2026-08-28", 920)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-08-30T01:00:00Z",
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["close"] == 920
    assert market["freshnessStatus"] == "fresh"
    assert market["stale"] is False
    assert market["freshness"]["sourceTimeStatus"] == "valid"


def test_projects_weekend_non_official_lineage_as_stale() -> None:
    # Friday 2026-08-28 close from non-official lineage must fail closed on Sunday 2026-08-30
    stores = project(
        [_record("mock-vendor:tw_price_daily:TWSE:2330:mock", "2330", "2026-08-28", 920)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-08-30T01:00:00Z",
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True


def test_rejects_missing_source_id_as_non_official_lineage() -> None:
    # A record with empty/missing source_id fails closed as non-official lineage
    stores = project(
        [_record("", "2330", "2026-08-28", 920)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-08-30T01:00:00Z",
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["lastTypedFailure"]["category"] == "lineage"
    assert market["freshness"]["lastTypedFailure"]["code"] == "market_input_non_official_lineage"


def test_projects_weekend_missing_refresh_receipt_as_stale() -> None:
    # Friday 2026-08-28 close on Sunday 2026-08-30 without refresh receipt must fail closed
    stores = project(
        [_record("tw-official:tw_price_daily:TWSE:2330:fri", "2330", "2026-08-28", 920)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": None,
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True


def test_projects_weekend_unparsable_refresh_receipt_as_stale() -> None:
    # Friday 2026-08-28 close on Sunday 2026-08-30 with unparsable receipt must fail closed
    stores = project(
        [_record("tw-official:tw_price_daily:TWSE:2330:fri", "2330", "2026-08-28", 920)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "not-a-timestamp",
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True


def test_ignores_non_market_records_and_preserves_other_projectors(tmp_path) -> None:
    path = tmp_path / "agora_signals.json"
    path.write_text(json.dumps({"consult-sig": {"id": "consult-sig"}, "old": {"projectionOwner": PROJECTOR}}))
    invalid = _record("src-mops", "2330", "2026-07-07", 905)
    invalid["metadata"]["normalized_row"]["dataset"] = "tw_material_event"
    stores = project([invalid])
    write_projection(stores, tmp_path)
    payload = json.loads(path.read_text())
    assert payload == {"consult-sig": {"id": "consult-sig"}}


def test_projects_stale_persisted_market_with_typed_failure_truth() -> None:
    stores = project(
        [_record("tw-official:tw_price_daily:TWSE:2330:stale", "2330", "2026-07-01", 880)],
        connector_freshness={
            "status": "stale",
            "last_success_at": "2026-07-01T01:00:00Z",
            "next_run_at": "2026-07-22T19:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": {
                "category": "external_egress",
                "code": "host_not_allowlisted",
                "retryable": False,
            },
        },
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    market = stores["agora_watchlist"]["market-2330"]
    assert market["close"] == 880
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["ageSeconds"] > market["freshness"]["staleThresholdSeconds"]
    assert market["freshness"]["lastTypedFailure"]["code"] == "host_not_allowlisted"


def test_missing_source_time_never_falls_back_to_record_creation_time() -> None:
    record = _record("src-missing-time", "2330", "2026-07-07", 905)
    record["created_at"] = "2026-07-22T00:00:00Z"
    del record["metadata"]["normalized_row"]["available_time"]
    del record["metadata"]["normalized_row"]["date"]

    stores = project(
        [record],
        connector_freshness={"status": "fresh", "stale_threshold_seconds": 86400},
        now=datetime(2026, 7, 22, 1, tzinfo=timezone.utc),
    )

    market = stores["agora_watchlist"]["market-2330"]
    assert market["asOf"] is None
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["sourceTimestamp"] is None
    assert market["freshness"]["sourceTimeStatus"] == "missing"
    assert market["freshness"]["ageSeconds"] is None


def test_materially_future_source_time_is_explicitly_stale() -> None:
    stores = project(
        [_record("src-future-time", "2330", "2099-01-01", 905)],
        connector_freshness={"status": "fresh", "stale_threshold_seconds": 86400},
        now=datetime(2026, 7, 22, 1, tzinfo=timezone.utc),
    )

    market = stores["agora_watchlist"]["market-2330"]
    assert market["asOf"] == "2099-01-01"
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["sourceTimeStatus"] == "future"
    assert market["freshness"]["ageSeconds"] is None


def test_compose_wires_both_projected_agora_stores() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "PANTHEON_BFF_AGORA_SIGNAL_STORE: ${PANTHEON_BFF_AGORA_SIGNAL_STORE:-/data/bff/agora_signals.json}" in compose
    assert "PANTHEON_BFF_AGORA_WATCHLIST_STORE: ${PANTHEON_BFF_AGORA_WATCHLIST_STORE:-/data/bff/agora_watchlist.json}" in compose
    assert "source-ingest-agora-projector:" in compose
    assert "source-ingest-scheduler:" in compose


def test_reproduces_and_resolves_run_33287442245_projection_binding() -> None:
    run_id = "33287442245"
    source_id = f"tw-official:tw_price_daily:TWSE:2330:{run_id}"
    record = {
        "source_id": source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": run_id,
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
                "change": 10.0,
                "volume": 25000,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": run_id,
                "status": "completed",
                "source_timestamp": "2026-08-28T05:30:00Z",
                "source_timestamp_status": "valid",
                "finished_at": "2026-08-30T01:00:00Z",
            },
            "latest_run": {
                "ingest_run_id": run_id,
                "status": "completed",
            },
        },
        "latest_source_record": {
            "source_id": source_id,
            "connector_id": "tw-twse-tpex-official-market",
            "provenance": {
                "source_ingest_run_id": run_id,
                "dataset": "tw_price_daily",
                "available_time": "2026-08-28T05:30:00Z",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    signal = stores["agora_signals"]["market-signal-2330-2026-08-28"]

    assert market["connectorId"] == "tw-twse-tpex-official-market"
    assert market["ingestRunId"] == run_id
    assert market["sourceId"] == source_id
    assert market["close"] == 940.0
    assert market["freshnessStatus"] == "fresh"
    assert market["stale"] is False
    assert market["freshness"]["sourceTimeStatus"] == "valid"
    assert market["freshness"]["lastTypedFailure"] is None

    assert signal["connectorId"] == "tw-twse-tpex-official-market"
    assert signal["ingestRunId"] == run_id
    assert signal["sourceId"] == source_id


def test_rejects_mismatched_run_id_between_receipt_and_record() -> None:
    receipt_run_id = "run-receipt-999"
    record_run_id = "run-old-111"
    record = {
        "source_id": f"tw-official:tw_price_daily:TWSE:2330:{record_run_id}",
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": record_run_id,
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": receipt_run_id,
                "status": "completed",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["ingestRunId"] == record_run_id
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["lastTypedFailure"]["code"] == "mismatched_run"


def test_rejects_missing_record_run_id_when_accepted_run_expected() -> None:
    receipt_run_id = "run-receipt-999"
    record = {
        "source_id": "tw-official:tw_price_daily:TWSE:2330:norun",
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": receipt_run_id,
                "status": "completed",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["ingestRunId"] is None
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["lastTypedFailure"]["code"] == "missing_record_run"


def test_rejects_unverifiable_calendar_evidence() -> None:
    record = {
        "source_id": "tw-official:tw_price_daily:TWSE:2330:badcal",
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": "run-001",
            "calendar_evidence": {
                "schema_hash": "invalid-schema",
                "settlement_session": "invalid",
            },
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": "run-001",
                "status": "completed",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["lastTypedFailure"]["category"] == "market_session"
    assert market["freshness"]["lastTypedFailure"]["code"] == "market_input_calendar_unverifiable"


def test_projects_non_hardcoded_symbol_with_exact_binding() -> None:
    run_id = "run-2454-001"
    source_id = f"tw-official:tw_price_daily:TWSE:2454:{run_id}"
    record = {
        "source_id": source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": run_id,
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2454",
                "market": "TW",
                "venue": "TWSE",
                "name": "MediaTek",
                "close": 1200.0,
                "change": 25.0,
                "volume": 8000,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": run_id,
                "status": "completed",
            },
        },
        "latest_source_record": {
            "source_id": source_id,
            "connector_id": "tw-twse-tpex-official-market",
            "provenance": {
                "source_ingest_run_id": run_id,
                "dataset": "tw_price_daily",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2454"]
    assert market["connectorId"] == "tw-twse-tpex-official-market"
    assert market["ingestRunId"] == run_id
    assert market["sourceId"] == source_id
    assert market["close"] == 1200.0
    assert market["freshnessStatus"] == "fresh"
    assert market["stale"] is False


def test_rejects_mismatched_source_id_between_readback_and_record_with_same_run() -> None:
    run_id = "run-same-888"
    accepted_source_id = f"tw-official:tw_price_daily:TWSE:2330:{run_id}:accepted"
    record_source_id = f"tw-official:tw_price_daily:TWSE:2330:{run_id}:other"
    record = {
        "source_id": record_source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": run_id,
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "latest_receipt": {
                "ingest_run_id": run_id,
                "status": "completed",
                "source_timestamp": "2026-08-28T05:30:00Z",
                "source_timestamp_status": "valid",
                "finished_at": "2026-08-30T01:00:00Z",
            },
            "latest_run": {
                "ingest_run_id": run_id,
                "status": "completed",
            },
        },
        "latest_source_record": {
            "source_id": accepted_source_id,
            "connector_id": "tw-twse-tpex-official-market",
            "provenance": {
                "symbol": "2330",
                "source_ingest_run_id": run_id,
                "dataset": "tw_price_daily",
                "available_time": "2026-08-28T05:30:00Z",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    # Assert authentic record source identity is preserved, not overwritten
    assert market["sourceId"] == record_source_id
    assert market["ingestRunId"] == run_id
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    assert market["freshness"]["lastTypedFailure"]["category"] == "receipt_binding"
    assert market["freshness"]["lastTypedFailure"]["code"] == "mismatched_source"


def test_rejects_mismatched_source_id_with_preexisting_freshness_typed_failure() -> None:
    run_id = "run-same-999"
    accepted_source_id = f"tw-official:tw_price_daily:TWSE:2330:{run_id}:accepted"
    record_source_id = f"tw-official:tw_price_daily:TWSE:2330:{run_id}:other"
    record = {
        "source_id": record_source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "source_ingest_run_id": run_id,
            "normalized_row": {
                "dataset": "tw_price_daily",
                "date": "2026-08-28",
                "available_time": "2026-08-28T05:30:00Z",
                "symbol": "2330",
                "market": "TW",
                "venue": "TWSE",
                "name": "TSMC",
                "close": 940.0,
            },
        },
    }
    connector_readback = {
        "connector_id": "tw-twse-tpex-official-market",
        "freshness": {
            "status": "fresh",
            "stale": False,
            "last_success_at": "2026-08-30T01:00:00Z",
            "source_timestamp": "2026-08-28T05:30:00Z",
            "source_timestamp_status": "valid",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": {
                "category": "freshness",
                "code": "historic_failure",
                "retryable": False,
            },
            "latest_receipt": {
                "ingest_run_id": run_id,
                "status": "completed",
                "source_timestamp": "2026-08-28T05:30:00Z",
                "source_timestamp_status": "valid",
                "finished_at": "2026-08-30T01:00:00Z",
            },
            "latest_run": {
                "ingest_run_id": run_id,
                "status": "completed",
            },
        },
        "latest_source_record": {
            "source_id": accepted_source_id,
            "connector_id": "tw-twse-tpex-official-market",
            "provenance": {
                "symbol": "2330",
                "source_ingest_run_id": run_id,
                "dataset": "tw_price_daily",
                "available_time": "2026-08-28T05:30:00Z",
            },
        },
    }
    stores = project(
        [record],
        connector_readback=connector_readback,
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["sourceId"] == record_source_id
    assert market["ingestRunId"] == run_id
    assert market["freshnessStatus"] == "stale"
    assert market["stale"] is True
    # Binding mismatch must take precedence over pre-existing historic failure
    assert market["freshness"]["lastTypedFailure"]["category"] == "receipt_binding"
    assert market["freshness"]["lastTypedFailure"]["code"] == "mismatched_source"


def test_binds_explicit_source_proof_receipt_id() -> None:
    rec = _record("tw-official:tw_price_daily:TWSE:2330:receipt-proof", "2330", "2026-08-28", 950)
    rec["metadata"]["source_proof_receipt_id"] = "spr-custom-receipt-999"
    rec["metadata"]["snapshot_id"] = "snap-market-123"
    stores = project(
        [rec],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-08-30T01:00:00Z",
            "next_run_at": "2026-08-31T01:00:00Z",
            "stale_threshold_seconds": 86400,
            "last_typed_failure": None,
        },
        now=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    assert market["source_proof_receipt_id"] == "spr-custom-receipt-999"
    assert market["sourceProofReceiptId"] == "spr-custom-receipt-999"
    assert market["snapshot_id"] == "snap-market-123"
    assert market["snapshotId"] == "snap-market-123"
    assert market["freshness"]["source_proof_receipt_id"] == "spr-custom-receipt-999"
    assert market["freshness"]["sourceProofReceiptId"] == "spr-custom-receipt-999"


class _MockSourceIngestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        auth = self.headers.get("Authorization")
        tenant = self.headers.get("X-Tenant-Id")
        try:
            resolved_tenant = _source_read_tenant(auth, tenant)
        except Exception as exc:
            status = getattr(exc, "status_code", 401)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": "auth failed"}')
            return

        if self.path.startswith("/api/source-ingest/source-records"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "source_records": [
                            {
                                "source_id": "tw-official:tw_price_daily:TWSE:2330:auth-test",
                                "connector_id": "tw-twse-tpex-official-market",
                                "tenant": resolved_tenant,
                                "metadata": {
                                    "source_ingest_run_id": "run-auth-01",
                                    "normalized_row": {
                                        "dataset": "tw_price_daily",
                                        "date": "2026-10-06",
                                        "available_time": "2026-10-06T06:00:00Z",
                                        "symbol": "2330",
                                        "market": "TW",
                                        "venue": "TWSE",
                                        "close": 1000.0,
                                        "change": 10.0,
                                        "volume": 50000,
                                    },
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            )
        elif self.path.startswith("/api/source-ingest/controller/readback"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "connectors": [
                            {
                                "connector_id": "tw-twse-tpex-official-market",
                                "freshness": {
                                    "status": "fresh",
                                    "last_success_at": "2026-10-06T06:00:00Z",
                                    "stale_threshold_seconds": 86400,
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args, **kwargs) -> None:
        pass


def test_projector_authorized_read_with_service_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-projector-auth-test"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        token = _mint_projector_service_jwt(secret, tenant_id="tenant-dev")
        monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", token)
        monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
        records = _get_source_records(base_url)
        assert len(records) == 1
        assert records[0]["source_id"] == "tw-official:tw_price_daily:TWSE:2330:auth-test"
        readback = _get_connector_readback(base_url)
        assert readback["connector_id"] == "tw-twse-tpex-official-market"
        freshness = _get_connector_freshness(base_url)
        assert freshness["status"] == "fresh"
    finally:
        server.shutdown()
        server.server_close()


def test_projector_missing_credential_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGORA_PROJECTOR_SERVICE_JWT", raising=False)
    monkeypatch.delenv("PANTHEON_AGORA_PROJECTOR_SERVICE_JWT", raising=False)
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_connector_readback("http://127.0.0.1:9999")


def test_projector_missing_tenant_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", "dummy-projector-token")
    monkeypatch.delenv("PANTHEON_TENANT_ID", raising=False)
    with pytest.raises(RuntimeError, match="PANTHEON_TENANT_ID is required"):
        _get_source_records("http://127.0.0.1:9999")
    with pytest.raises(RuntimeError, match="PANTHEON_TENANT_ID is required"):
        _get_connector_readback("http://127.0.0.1:9999")


def test_projector_wrong_role_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-projector-wrong-role"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        import base64
        import hashlib
        import hmac

        header = {"alg": "HS256", "typ": "JWT"}
        # "viewer" became an admitted source-ingest reader in 721eedcfa
        # (EVALUATOR-EXISTING-OWNER-READ-CONTRACT-20261012); a role outside
        # _SOURCE_INGEST_READ_ROLES keeps this fail-closed assertion meaningful.
        claims = {"sub": "test", "roles": ["guest"], "tenant_id": "tenant-dev"}

        def b64url(d: bytes) -> str:
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode("ascii")

        h = b64url(json.dumps(header).encode("utf-8"))
        c = b64url(json.dumps(claims).encode("utf-8"))
        sig = hmac.new(secret.encode("utf-8"), f"{h}.{c}".encode("ascii"), hashlib.sha256).digest()
        token = f"{h}.{c}.{b64url(sig)}"

        monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", token)
        monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
        with pytest.raises(RuntimeError, match=r"AGORA_PROJECTOR_SERVICE_JWT was rejected \(403\)"):
            _get_source_records(base_url)
    finally:
        server.shutdown()
        server.server_close()


def test_projector_foreign_tenant_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-projector-foreign-tenant"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        token = _mint_projector_service_jwt(secret, tenant_id="foreign-tenant")
        monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", token)
        monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
        with pytest.raises(RuntimeError, match=r"AGORA_PROJECTOR_SERVICE_JWT was rejected \(403\)"):
            _get_source_records(base_url)
    finally:
        server.shutdown()
        server.server_close()


def test_projector_never_falls_back_to_unauthenticated_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGORA_PROJECTOR_SERVICE_JWT", raising=False)
    monkeypatch.delenv("PANTHEON_AGORA_PROJECTOR_SERVICE_JWT", raising=False)
    monkeypatch.delenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", raising=False)
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _fetch_source_ingest_json("http://127.0.0.1:9999")


def test_projector_reads_credential_from_configured_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-projector-file-test"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    token_file = tmp_path / "AGORA_PROJECTOR_SERVICE_JWT"
    token_file.write_text(_mint_projector_service_jwt(secret, tenant_id="tenant-dev"), encoding="utf-8")
    token_file.chmod(0o600)
    try:
        monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(token_file))
        monkeypatch.delenv("AGORA_PROJECTOR_SERVICE_JWT", raising=False)
        monkeypatch.delenv("PANTHEON_AGORA_PROJECTOR_SERVICE_JWT", raising=False)
        monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
        records = _get_source_records(base_url)
        assert len(records) == 1

        # Dynamic rotation: file content updates and next fetch reads the renewed token without process restart
        renewed_token = _mint_projector_service_jwt(secret, tenant_id="tenant-dev", subject="renewed-projector")
        token_file.write_text(renewed_token, encoding="utf-8")
        token_file.chmod(0o600)
        records_after_rotation = _get_source_records(base_url)
        assert len(records_after_rotation) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_projector_missing_or_unsafe_credential_file_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
    missing_file = tmp_path / "missing_token"
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(missing_file))
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")

    unsafe_file = tmp_path / "unsafe_token"
    unsafe_file.write_text("dummy-token", encoding="utf-8")
    unsafe_file.chmod(0o666)
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(unsafe_file))
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")

    empty_file = tmp_path / "empty_token"
    empty_file.write_text("", encoding="utf-8")
    empty_file.chmod(0o600)
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(empty_file))
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")

    whitespace_file = tmp_path / "whitespace_token"
    whitespace_file.write_text("bad token with spaces", encoding="utf-8")
    whitespace_file.chmod(0o600)
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(whitespace_file))
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")


def test_projector_never_falls_back_to_env_when_file_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", "fallback-token-should-not-be-used")
    missing_file = tmp_path / "missing_token"
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT_FILE", str(missing_file))
    with pytest.raises(RuntimeError, match="AGORA_PROJECTOR_SERVICE_JWT is required"):
        _get_source_records("http://127.0.0.1:9999")



# --- AGORA-PROJECTOR-SOURCE-RECORDS-BOUNDED-READ-20261126 -------------------
# Real PostgreSQL owner store at hosted-like volume: the projector reads only
# its connector's records in bounded pages and projects exactly what the old
# tenant-wide read projected.

_VOLUME_TENANT = "tenant-dev"
_VOLUME_ROWS = 120_000
_PROJECTOR_SYMBOLS = 150
_PROJECTOR_DAYS = 12
_OTHER_CONNECTORS = (
    "tw-finmind-broker-daily-report",
    "tw-finmind-valuation-daily",
    "rss-news-public",
    "sec-edgar-filings",
    "tw-twse-tpex-official-market-margin",
    "internal-research-notes",
)
_VOLUME_PROJECTOR_FRESHNESS = {
    "status": "fresh",
    "last_success_at": "2026-11-20T08:00:00Z",
    "stale_threshold_seconds": 86400,
    "last_typed_failure": None,
}
_VOLUME_NOW = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)


def _volume_source_payload(i: int, connector: str, tenant: str, *, symbol: str, day: int, run: str) -> dict:
    date = f"2026-11-{day:02d}"
    return {
        "source_id": f"tw-official:tw_price_daily:TWSE:{symbol}:{connector[:6]}{i:07d}"
        if connector == "tw-twse-tpex-official-market"
        else f"{connector}:{i:07d}",
        "connector_id": connector,
        "source_type": "market" if connector.startswith("tw-") else "news",
        "title": f"volume row {i}",
        "content_ref": f"volume://{connector}/{i}",
        "status": "normalized",
        "trace_id": f"trace-{i}",
        "created_at": f"{date}T08:00:00+00:00",
        "metadata": {
            "tenant_id": tenant,
            "source_ingest_run_id": run,
            "dataset": "tw_price_daily",
            "normalized_row": {
                "dataset": "tw_price_daily", "date": date, "available_time": f"{date}T08:00:00Z",
                "symbol": symbol, "market": "TW", "venue": "TWSE", "name": f"name-{symbol}",
                "close": 100.0 + (i % 997) / 10, "change": (i % 11) - 5.0, "volume": 1000 + i,
            },
            # Hosted rows average ~3.3 KB (422 MB / ~128k rows).
            "raw_excerpt": "x" * 2600,
        },
    }


@pytest.fixture(scope="module")
def volume_store():
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("real PostgreSQL required; set SOURCE_INGEST_TEST_POSTGRES_DSN")
    import uuid

    import psycopg

    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    schema = f"agora_proj_{uuid.uuid4().hex[:12]}"
    repo = PostgresSourceEvidenceRepository(dsn, table=f"{schema}.source_evidence", max_connections=4)
    projector_rows = []
    i = 0
    # Interleave the projector connector's daily runs with other connectors so
    # its rows are spread across the whole append range, as on hosted.
    with psycopg.connect(dsn) as conn:
        with conn.cursor().copy(f"COPY {repo.table} (record_type, record_id, payload) FROM STDIN") as copy:
            per_day_other = (_VOLUME_ROWS - _PROJECTOR_SYMBOLS * _PROJECTOR_DAYS * 2) // _PROJECTOR_DAYS
            for day in range(1, _PROJECTOR_DAYS + 1):
                for _ in range(per_day_other):
                    connector = _OTHER_CONNECTORS[i % len(_OTHER_CONNECTORS)]
                    payload = _volume_source_payload(i, connector, _VOLUME_TENANT, symbol=str(2000 + i % 300), day=day, run=f"other-{day}")
                    copy.write_row(("source_record", f"{_VOLUME_TENANT}:{payload['source_id']}", json.dumps(payload)))
                    i += 1
                for tenant in (_VOLUME_TENANT, "tenant-other"):
                    for n in range(_PROJECTOR_SYMBOLS):
                        payload = _volume_source_payload(
                            i, "tw-twse-tpex-official-market", tenant, symbol=str(1100 + n), day=day, run=f"run-{tenant}-{day}"
                        )
                        copy.write_row(("source_record", f"{tenant}:{payload['source_id']}", json.dumps(payload)))
                        if tenant == _VOLUME_TENANT:
                            projector_rows.append(payload)
                        i += 1
        conn.commit()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"VACUUM ANALYZE {repo.table}")
    repo.projector_rows = projector_rows
    repo.total_rows = i
    try:
        yield repo
    finally:
        repo.close()
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _record_volume_measurement(key: str, value) -> None:
    """Optional evidence sink; normal test runs never write repository files."""
    import os

    output = os.getenv("AGORA_PROJECTOR_READ_MEASUREMENTS")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({key: value}) + "\n")


def _owner_client(repo):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(evidence_repository=repo)))
    return TestClient(app)


def test_real_pg_projector_reads_only_its_connector_bounded_and_projects_identically(
    volume_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import psycopg

    import scripts.project_market_data_to_bff_agora_surfaces as projector

    secret = "secret-projector-volume"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    token = _mint_projector_service_jwt(secret, tenant_id=_VOLUME_TENANT)
    monkeypatch.setenv("AGORA_PROJECTOR_SERVICE_JWT", token)
    monkeypatch.setenv("PANTHEON_TENANT_ID", _VOLUME_TENANT)
    client = _owner_client(volume_store)
    requested: list[str] = []
    response_bytes = 0

    def fetch_via_owner(url: str):
        nonlocal response_bytes
        path = url.split("://", 1)[1].split("/", 1)[1]
        requested.append("/" + path)
        res = client.get("/" + path, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": _VOLUME_TENANT})
        assert res.status_code == 200, res.text
        response_bytes += len(res.content)
        return res.json()

    monkeypatch.setattr(projector, "_fetch_source_ingest_json", fetch_via_owner)
    started = time.perf_counter()
    records = projector._get_source_records("http://source-ingest:8097", page_size=500)
    elapsed = time.perf_counter() - started

    expected = volume_store.projector_rows
    assert volume_store.total_rows >= 100_000
    # Only this tenant's projector-connector records, newest append first, none missing.
    assert len(records) == len(expected) == _PROJECTOR_SYMBOLS * _PROJECTOR_DAYS
    assert {r["connector_id"] for r in records} == {projector.CONNECTOR_ID}
    assert {r["metadata"]["tenant_id"] for r in records} == {_VOLUME_TENANT}
    assert [r["source_id"] for r in records] == [r["source_id"] for r in reversed(expected)]
    # Every request was a bounded connector page; the unfiltered list was never requested.
    assert requested and all("connector_id=tw-twse-tpex-official-market" in p and "page_size=500" in p for p in requested)
    assert len(requested) == -(-len(expected) // 500)
    # Stated budget: the whole bounded read finishes well inside the 30 s client timeout.
    assert elapsed < 5.0, elapsed

    # Same Agora output as the previous tenant-wide read for the same input.
    full_list = [r.to_dict() for r in volume_store.list_source_records(tenant_id=_VOLUME_TENANT)]
    assert len(full_list) > 10 * len(records)
    kwargs = {"connector_freshness": _VOLUME_PROJECTOR_FRESHNESS, "now": _VOLUME_NOW}
    assert project(records, **kwargs) == project(full_list, **kwargs)
    assert len(project(records, **kwargs)["agora_watchlist"]) == _PROJECTOR_SYMBOLS

    with psycopg.connect(volume_store.dsn) as conn:
        def scalar(sql, params=()):
            row = conn.execute(sql, params).fetchone()
            return row[0]

        before_rows, before_bytes = conn.execute(
            f"SELECT COUNT(*), SUM(pg_column_size(payload)) FROM {volume_store.table} "
            "WHERE record_type = 'source_record' AND evidence_tenant_id = %s",
            (_VOLUME_TENANT,),
        ).fetchone()
        after_rows, after_bytes = conn.execute(
            f"SELECT COUNT(*), SUM(pg_column_size(payload)) FROM {volume_store.table} "
            "WHERE record_type = 'source_record' AND evidence_tenant_id = %s AND payload->>'connector_id' = %s",
            (_VOLUME_TENANT, projector.CONNECTOR_ID),
        ).fetchone()
        plan = "\n".join(
            r[0]
            for r in conn.execute(
                f"EXPLAIN SELECT append_id, payload FROM {volume_store.table} "
                "WHERE record_type = 'source_record' AND evidence_tenant_id = %s "
                "AND payload->>'connector_id' = %s AND append_id < %s ORDER BY append_id DESC LIMIT 501",
                (_VOLUME_TENANT, projector.CONNECTOR_ID, 10**9),
            ).fetchall()
        )
        table_bytes = scalar("SELECT pg_total_relation_size(%s::regclass)", (volume_store.table,))
    assert "source_evidence_source_connector_page_idx" in plan and "Seq Scan" not in plan, plan
    for key, value in {
        "seeded_rows": volume_store.total_rows,
        "table_total_bytes": table_bytes,
        "before_rows_read_per_refresh": before_rows,
        "before_payload_bytes_per_refresh": before_bytes,
        "after_rows_read_per_refresh": after_rows,
        "after_payload_bytes_per_refresh": after_bytes,
        "after_http_response_bytes": response_bytes,
        "after_requests": len(requested),
        "after_read_seconds": round(elapsed, 3),
        "page_plan": plan,
    }.items():
        _record_volume_measurement(key, value)


def test_real_pg_connector_page_cap_cursor_and_tenant_scope(volume_store) -> None:
    from services.source_ingestion.pg_store import SOURCE_RECORD_PAGE_MAX

    client = _owner_client(volume_store)
    import os

    secret = "secret-projector-volume-cap"
    os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = secret
    try:
        token = _mint_projector_service_jwt(secret, tenant_id=_VOLUME_TENANT)
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": _VOLUME_TENANT}
        over = client.get(
            "/api/source-ingest/source-records",
            params={"connector_id": "tw-twse-tpex-official-market", "page_size": SOURCE_RECORD_PAGE_MAX + 1},
            headers=headers,
        )
        assert over.status_code == 422
        no_connector = client.get("/api/source-ingest/source-records", params={"page_size": 10}, headers=headers)
        assert no_connector.status_code == 400
        bad_token = client.get(
            "/api/source-ingest/source-records",
            params={"connector_id": "tw-twse-tpex-official-market", "page_token": "abc"},
            headers=headers,
        )
        assert bad_token.status_code == 400
        first = client.get(
            "/api/source-ingest/source-records",
            params={"connector_id": "tw-twse-tpex-official-market", "page_size": 7},
            headers=headers,
        ).json()
        second = client.get(
            "/api/source-ingest/source-records",
            params={"connector_id": "tw-twse-tpex-official-market", "page_size": 7, "page_token": first["next_page_token"]},
            headers=headers,
        ).json()
        newest = [r["source_id"] for r in reversed(volume_store.projector_rows)][:14]
        assert [r["source_id"] for r in first["source_records"] + second["source_records"]] == newest
        assert first["page_size"] == 7 and first["connector_id"] == "tw-twse-tpex-official-market"
        unknown = client.get(
            "/api/source-ingest/source-records", params={"connector_id": "no-such-connector"}, headers=headers
        ).json()
        assert unknown["source_records"] == [] and unknown["next_page_token"] is None
    finally:
        os.environ.pop("PANTHEON_RUNTIME_JWT_SECRET", None)


def test_in_memory_owner_page_matches_postgres_contract() -> None:
    from services.knowledge.evidence.repository import InMemoryEvidenceRepository
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import read_source_records_page

    repo = InMemoryEvidenceRepository()
    for i in range(5):
        for connector, tenant in (("tw-twse-tpex-official-market", "t1"), ("other", "t1"), ("tw-twse-tpex-official-market", "t2")):
            repo.add_source_record(SourceRecord(
                source_id=f"{connector}-{tenant}-{i}", connector_id=connector, source_type="market",
                title="t", content_ref="c", metadata={"tenant_id": tenant},
            ))
    page, cursor = read_source_records_page(repo, "t1", connector_id="tw-twse-tpex-official-market", limit=3)
    rest, end = read_source_records_page(repo, "t1", connector_id="tw-twse-tpex-official-market", limit=3, cursor=cursor)
    assert [r.source_id for r in page + rest] == [f"tw-twse-tpex-official-market-t1-{i}" for i in (4, 3, 2, 1, 0)]
    assert cursor == 3 and end is None
    with pytest.raises(ValueError):
        read_source_records_page(repo, "*", connector_id="tw-twse-tpex-official-market", limit=3)


def test_projector_pages_until_last_token_and_stops_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.project_market_data_to_bff_agora_surfaces as projector

    urls: list[str] = []

    def fake_fetch(url: str):
        urls.append(url)
        n = len(urls)
        return {
            "source_records": [{"source_id": f"s{n}-{k}", "connector_id": projector.CONNECTOR_ID} for k in range(2)],
            "next_page_token": str(n * 2) if n < 10 else None,
        }

    monkeypatch.setattr(projector, "_fetch_source_ingest_json", fake_fetch)
    assert len(projector._get_source_records("http://x", page_size=2, max_records=100)) == 20
    assert len(urls) == 10 and "page_token" not in urls[0] and "page_token=2" in urls[1]
    assert all("connector_id=tw-twse-tpex-official-market" in u for u in urls)
    urls.clear()
    assert len(projector._get_source_records("http://x", page_size=2, max_records=6)) == 6
    assert len(urls) == 3
