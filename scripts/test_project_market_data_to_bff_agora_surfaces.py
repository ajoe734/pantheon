import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.project_market_data_to_bff_agora_surfaces import PROJECTOR, project, write_projection


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
