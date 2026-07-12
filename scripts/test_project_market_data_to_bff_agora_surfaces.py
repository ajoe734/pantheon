import json

from scripts.project_market_data_to_bff_agora_surfaces import PROJECTOR, project, write_projection


def _record(source_id: str, symbol: str, date: str, close: float) -> dict:
    return {
        "source_id": source_id,
        "connector_id": "tw-twse-tpex-official-market",
        "metadata": {
            "ingest_run_id": "ingest-live-001",
            "normalized_row": {
                "dataset": "tw_price_daily", "date": date, "available_time": date,
                "symbol": symbol, "market": "TW", "venue": "TWSE", "name": "TSMC",
                "close": close, "change": 5.0, "volume": 1000,
            },
        },
    }


def test_projects_latest_real_row_with_provenance() -> None:
    stores = project([_record("src-old", "2330", "2026-07-06", 900), _record("src-new", "2330", "2026-07-07", 905)])
    market = stores["agora_watchlist"]["market-2330"]
    signal = stores["agora_signals"]["market-signal-2330-2026-07-07"]
    assert market["close"] == 905
    assert market["source_ref"] == "source_ingest:src-new"
    assert market["ingestRunId"] == "ingest-live-001"
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
