"""Governed whole-market dataset from the daily_bars store (DISCOVERY-GOVERNED-MARKET-DATASET-20261125)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from services.research import governed_market_dataset as gmd
from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.daily_bars_store import DailyBarsStore

END = "2026-11-24"
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
SMALL = gmd.MarketPolicy(window_days=30, min_history_days=10, liquidity_days=5, min_liquidity_observations=3,
                         min_median_traded_value=1_000_000.0)


def trading_days(n: int, end: str = END) -> list[str]:
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def row(symbol: str, day: str, close: float, *, traded_value: float | None = 9_000_000.0,
        available: datetime | None = None) -> dict:
    ohlcv = {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0, "adjusted": False,
             "traded_value": traded_value,
             "available_time": available or datetime.fromisoformat(day + "T11:00:00+00:00"),
             "trading_status": "traded"}
    return {"market": "TW", "symbol": symbol, "trade_date": day, **ohlcv, "source_id": "s", "ingest_run_id": "r",
            "lineage_ref": "r", "content_hash": dbs._sha({k: (v.isoformat() if isinstance(v, datetime) else v)
                                                         for k, v in ohlcv.items()})}


def build_store(root: Path, days: int = 30, *, symbols=("2330.TW", "2317.TW"), skip: dict | None = None,
                thin: tuple = (), short: tuple = (), low_tv: tuple = ()) -> list[str]:
    store = DailyBarsStore(root)
    ds = trading_days(days)
    for i, d in enumerate(ds):
        rows = []
        for s in list(symbols) + list(thin) + list(short) + list(low_tv):
            if s in (skip or {}) and d >= skip[s]:
                continue
            if s in short and i < days - 4:
                continue
            tv = 100.0 if s in low_tv else 9_000_000.0
            rows.append(row(s, d, 100.0 + i, traded_value=tv))
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    return ds


def test_boundary_includes_market_and_names_every_exclusion(tmp_path):
    build_store(tmp_path, symbols=("2330.TW", "2317.TW", "2412.TW"), skip={"2412.TW": trading_days(30)[-3]},
                short=("9999.TW",), low_tv=("1101.TW",))
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert boundary.symbols == ("2317.TW", "2330.TW")
    assert boundary.exclusions["2412.TW"].startswith("suspended:")
    assert boundary.exclusions["9999.TW"].startswith("too_short_history:")
    assert boundary.exclusions["1101.TW"].startswith("illiquid:")
    assert boundary.store_symbol_count == 5 and boundary.trade_days == 30


def test_empty_authority_directory_reaches_a_dataset(tmp_path, monkeypatch):
    """No persona candidate output, no policy file: the dataset comes from the store alone."""
    store_root = tmp_path / "market_data_store"
    build_store(store_root)
    empty_authority = tmp_path / "persona-pool-dataset-authority"
    empty_authority.mkdir()
    monkeypatch.setenv(gmd.STORE_ROOT_ENV, str(store_root))
    for name in ("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH", "RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"):
        monkeypatch.delenv(name, raising=False)
    dataset = gmd.resolve_governed_dataset(symbols=["2330"], now=NOW, policy=SMALL)
    assert list(empty_authority.iterdir()) == []
    assert dataset["data_frequency"] == "daily" and dataset["dataset_id"].startswith("gmd-" + END)
    assert {r["instrument"] for r in dataset["records"]} == {"2330.TW"}
    first = dataset["records"][0]
    assert set(first) == {"instrument", "date", "open", "high", "low", "close", "volume", "traded_value", "available_time"}
    assert first["available_time"].endswith("Z")


def test_point_in_time_by_stored_available_time_and_nothing_imputed(tmp_path):
    store = DailyBarsStore(tmp_path)
    ds = trading_days(30)
    for i, d in enumerate(ds):
        late = datetime(2026, 11, 26, tzinfo=timezone.utc) if d == ds[-1] else None
        store.write_day(d, [row("2330.TW", d, 100.0 + i, available=late), row("2317.TW", d, 50.0 + i)], ingest_run_id=d)
    dataset = gmd.resolve_governed_dataset(symbols=["2330.TW"], root=tmp_path, now=NOW, policy=SMALL)
    assert [r["date"] for r in dataset["records"]] == ds[:-1]  # the bar not yet available is left out, not filled
    assert dataset["bars_not_point_in_time"] == 1


def test_snapshot_id_reverifies_and_changes_with_the_store(tmp_path):
    ds = build_store(tmp_path)
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert gmd.verify_snapshot(boundary.snapshot_id, tmp_path, policy=SMALL)
    pinned = gmd.resolve_governed_dataset(snapshot_id=boundary.snapshot_id, symbols=["2330"], root=tmp_path, now=NOW, policy=SMALL)
    assert pinned["dataset_id"] == boundary.snapshot_id
    DailyBarsStore(tmp_path).write_day(ds[-5], [row("2330.TW", ds[-5], 777.0)], ingest_run_id="rewrite")
    assert not gmd.verify_snapshot(boundary.snapshot_id, tmp_path, policy=SMALL)
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.resolve_governed_dataset(snapshot_id=boundary.snapshot_id, root=tmp_path, now=NOW, policy=SMALL)
    assert exc.value.code == "snapshot_not_reproducible"
    assert not gmd.verify_snapshot("gmd-2026-11-24-" + "0" * 40, tmp_path, policy=SMALL)
    assert not gmd.verify_snapshot("not-ours", tmp_path, policy=SMALL)


def test_missing_and_stale_store_fail_closed_with_a_reason(tmp_path):
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.build_market_boundary(tmp_path / "absent", policy=SMALL, now=NOW)
    assert exc.value.code == "daily_bars_store_missing" and "no stored trade day" in str(exc.value)
    build_store(tmp_path)
    with pytest.raises(gmd.GovernedMarketDatasetError) as stale:
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW + timedelta(days=9))
    assert stale.value.code == "daily_bars_store_stale" and END in str(stale.value)


@pytest.mark.parametrize("damage,code", [
    ("truncate", "daily_bars_day_incomplete"),
    ("delete", "daily_bars_day_incomplete"),
    ("bad_json", "daily_bars_day_corrupt"),
    ("no_digest", "daily_bars_day_unverifiable"),
])
def test_corrupt_incomplete_or_unverifiable_day_blocks(tmp_path, damage, code):
    ds = build_store(tmp_path)
    manifest_path = tmp_path / "daily_bars" / "_manifest" / f"date={ds[-3]}.json"
    manifest = json.loads(manifest_path.read_text())
    part = tmp_path / "daily_bars" / manifest["files"][0]["path"]
    if damage == "truncate":
        part.write_bytes(part.read_bytes()[:-10])
    elif damage == "delete":
        part.unlink()
    elif damage == "bad_json":
        manifest_path.write_text("{not json")
    else:
        manifest.pop("content_digest")
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    assert exc.value.code == code and ds[-3] in str(exc.value)


def test_dead_letters_of_other_connectors_do_not_block(tmp_path, monkeypatch):
    """Hosted shape: 4,742 pending dead letters on other connectors, 0 on the price connector. The builder
    reads the store only and never calls the source API, so the count cannot reach it."""
    build_store(tmp_path)
    import urllib.request

    def refuse(*args, **kwargs):
        raise AssertionError("the builder must not call any HTTP API")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("SOURCE_INGEST_API_URL", "http://source-ingest:8097")
    hosted = {"pending_dlq_count_other_connectors": 4742, "pending_dlq_count_price_connector": 0}
    assert hosted["pending_dlq_count_price_connector"] == 0
    assert gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW).symbols
    source = Path(gmd.__file__).read_text()
    assert "urllib" not in source and "requests" not in source and "source-ingest/candidate-pool-market" not in source


def test_whole_market_is_not_materialised_and_symbol_limit_fails_closed(tmp_path):
    build_store(tmp_path)
    boundary = gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)
    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        gmd.dataset_for(boundary, ["2330.TW"] * 1 + [f"S{i}" for i in range(gmd.MAX_DATASET_SYMBOLS)], root=tmp_path)
    assert exc.value.code == "dataset_symbols_over_limit"
    with pytest.raises(gmd.GovernedMarketDatasetError) as outside:
        gmd.dataset_for(boundary, ["0000.TW"], root=tmp_path)
    assert outside.value.code == "symbols_outside_market_boundary"


def test_refuses_to_run_on_an_event_loop(tmp_path):
    build_store(tmp_path)

    async def call():
        gmd.build_market_boundary(tmp_path, policy=SMALL, now=NOW)

    with pytest.raises(gmd.GovernedMarketDatasetError) as exc:
        asyncio.run(call())
    assert exc.value.code == "governed_dataset_must_run_in_worker"
