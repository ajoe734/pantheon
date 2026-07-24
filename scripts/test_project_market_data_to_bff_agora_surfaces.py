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
        [_record("src-old", "2330", "2026-07-06", 900), _record("src-new", "2330", "2026-07-07", 905)],
        connector_freshness={
            "status": "fresh",
            "last_success_at": "2026-07-07T01:00:00Z",
            "next_run_at": "2026-07-08T01:00:00Z",
            "stale_threshold_seconds": 172800,
            "last_typed_failure": None,
        },
        now=datetime(2026, 7, 7, 2, tzinfo=timezone.utc),
    )
    market = stores["agora_watchlist"]["market-2330"]
    signal = stores["agora_signals"]["market-signal-2330-2026-07-07"]
    assert market["close"] == 905
    assert market["source_ref"] == "source_ingest:src-new"
    assert market["ingestRunId"] == "ingest-live-001"
    assert market["freshnessStatus"] == "fresh"
    assert market["stale"] is False
    assert market["freshness"] == {
        "schemaVersion": "agora_source_freshness.v1",
        "status": "fresh",
        "stale": False,
        "lastSuccessAt": "2026-07-07T01:00:00Z",
        "sourceTimestamp": "2026-07-07",
        "sourceTimeStatus": "valid",
        "ageSeconds": 7200,
        "staleThresholdSeconds": 172800,
        "nextRunAt": "2026-07-08T01:00:00Z",
        "lastTypedFailure": None,
    }
    assert signal["projectionOwner"] == PROJECTOR


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
        [_record("src-stale", "2330", "2026-07-01", 880)],
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
