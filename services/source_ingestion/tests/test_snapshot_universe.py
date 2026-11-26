"""Active-universe latest snapshot projection (SOURCE-SNAPSHOT-ACTIVE-UNIVERSE-20261116).

Rows use the hosted shapes: official daily points and collector-relayed provisional closes
(``collector_close`` lineage), stored by the real MarketDataStorageWriter.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.connectors.base import SourceConnector, SourceRecord, SourceType
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.pipeline import persist_latest_market_snapshots
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.snapshot_universe import SnapshotUniverse, static_member_symbols

CONNECTOR = "collector-tw-twse-price-history"
BASE = date(2026, 8, 24)  # a Monday
TARGET = "3008.TWSE"
LINEAGE = {
    "collector_source_id": CONNECTOR,
    "exchange_endpoint": "https://www.twse.com.tw/exchangeReport/MI_INDEX",
    "bundle_sha256": "a" * 64,
}


def _days(n: int = 60) -> list[str]:
    out, day = [], BASE
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _record(symbol: str, day: str, index: int, *, provisional: bool) -> SourceRecord:
    metadata = {
        "dataset": "tw_price_daily", "symbol": symbol, "close": 100.0 + index / 4,
        "event_time": f"{day}T05:30:00Z", "market": "TW", "trade_date": day,
    }
    if provisional:
        metadata["collector_close"] = {**LINEAGE, "trade_date": day, "provisional": True, "official": False}
    return SourceRecord(
        source_id=f"{CONNECTOR}-{day}" if provisional else f"official-{day}", connector_id=CONNECTOR,
        source_type="market", title=f"{symbol} {day}", content_ref=f"ref://{symbol}/{day}", metadata=metadata,
        created_at=f"{day}T09:00:00Z",
    )


def _result(records, run_id, finished):
    run = SimpleNamespace(status=SimpleNamespace(value="completed"), ingest_run_id=run_id,
                          to_dict=lambda: {"finished_at": finished})
    return SimpleNamespace(run=run, records=tuple(records))


def _connector() -> SourceConnector:
    return SourceConnector(connector_id=CONNECTOR, source_type=SourceType.MARKET, provider="p",
                           license_scope="official_reference", metadata={"market": "TW"})


def _ingest(root: Path, store: LatestMarketSnapshotStore | None, symbols, day: str, index: int, *, noise: int = 0):
    """One ingest run: partitions written by the real writer, then the live projection."""
    records = [_record(s, day, index, provisional=index >= 50) for s in symbols]
    records += [_record(f"{9000 + n}.TWSE", day, index, provisional=False) for n in range(noise)]
    finished = f"{day}T09:00:00Z"
    result = _result(records, f"ingest-{day}", finished)
    MarketDataStorageWriter(root).write_run(result=result, connector=_connector())
    for path in (root / "raw").rglob(f"ingest-{day}.jsonl*"):
        stamp = datetime.fromisoformat(finished.replace("Z", "+00:00")).timestamp()
        os.utime(path, (stamp, stamp))
    if store is not None:
        persist_latest_market_snapshots(store, result, connector=_connector())


@pytest.fixture()
def clock():
    state = {"now": datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)}

    def now() -> datetime:
        return state["now"]

    now.state = state  # type: ignore[attr-defined]
    return now


def _universe(tmp_path, clock, *, name="cand", activate=True):
    store = LatestMarketSnapshotStore(tmp_path / f"{name}.jsonl")
    uni = SnapshotUniverse(store, state_path=tmp_path / f"{name}.universe.json", storage_root=tmp_path / "store",
                           connector_market=lambda _c: "TW", clock=clock)
    if activate:
        uni.initialize()
    return store, uni


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", raising=False)


def _build(tmp_path, *, noise=0, n=60, candidate_store=None):
    ref = LatestMarketSnapshotStore(tmp_path / "ref.jsonl")
    for i, day in enumerate(_days(n)):
        _ingest(tmp_path / "store", ref, [TARGET], day, i, noise=noise)
    return ref


def test_first_request_seeds_byte_identical_snapshot_within_two_seconds(tmp_path, clock):
    ref = _build(tmp_path, noise=25)
    store, uni = _universe(tmp_path, clock)
    assert store.get(TARGET) is None
    started = time.monotonic()
    got = uni.snapshot_for_request("3008.TW")
    elapsed = time.monotonic() - started
    want = ref.get(TARGET)
    assert got is not None and elapsed < 2.0
    assert got.snapshot_id == want.snapshot_id and got.points == want.points
    assert len(got.points) == 60
    assert json.dumps(got.to_public_dict(), sort_keys=True) == json.dumps(want.to_public_dict(), sort_keys=True)
    assert any(p.collector_close for p in got.points)  # real shaped: provisional collector closes present
    assert uni.is_member(TARGET)


def test_partitions_identical_and_projection_only_keeps_members(tmp_path, clock):
    plain, uni_root = tmp_path / "plain", tmp_path / "uni"
    plain.mkdir(), uni_root.mkdir()
    day = _days(1)[0]
    _ingest(plain / "store", LatestMarketSnapshotStore(plain / "s.jsonl"), [TARGET, "2330.TWSE"], day, 0, noise=5)
    store = LatestMarketSnapshotStore(uni_root / "s.jsonl")
    SnapshotUniverse(store, state_path=uni_root / "u.json", storage_root=uni_root / "store", clock=clock).initialize()
    _ingest(uni_root / "store", store, [TARGET, "2330.TWSE"], day, 0, noise=5)

    def tree(root: Path):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted((root / "store").rglob("*")) if p.is_file()}

    assert tree(plain) == tree(uni_root) and tree(plain)
    assert store.live_symbols() == ["2330.TWSE"]  # governed member only; 3008 and noise stay in partitions


def test_membership_survives_restart_and_request_refreshes_it(tmp_path, clock):
    _build(tmp_path, n=3)
    store, uni = _universe(tmp_path, clock)
    assert uni.snapshot_for_request(TARGET) is not None
    store2 = LatestMarketSnapshotStore(tmp_path / "cand.jsonl")
    uni2 = SnapshotUniverse(store2, state_path=tmp_path / "cand.universe.json", storage_root=tmp_path / "store", clock=clock)
    assert uni2.ready and uni2.is_member("3008") and store2.get(TARGET) is not None
    _ingest(tmp_path / "store", store2, [TARGET], "2026-11-13", 70)  # later live runs keep the member current
    assert store2.get(TARGET).points[-1].event_time.startswith("2026-11-13")


def test_idle_member_leaves_with_transition_and_reseeds_identically(tmp_path, clock):
    ref = _build(tmp_path, n=20)
    store, uni = _universe(tmp_path, clock)
    first = uni.snapshot_for_request(TARGET)
    clock.state["now"] += timedelta(days=9)  # 7 weekdays: still inside the 10 trading day window
    assert uni.sweep()["withdrawn"] == 0 and store.get(TARGET) is not None
    clock.state["now"] += timedelta(days=6)  # 12 weekdays since the request
    result = uni.sweep()
    assert result["withdrawn"] == 1 and store.get(TARGET) is None and not uni.is_member(TARGET)
    transition = json.loads((tmp_path / "cand.universe.json").read_text())["transitions"][-1]
    assert transition["symbol"] == TARGET and transition["to_tier"] == "archive_universe"
    assert transition["reason"] == "not_requested_10_trading_days" and transition["triggered_by"] == "snapshot_universe"
    store.compact(force=True)
    again = uni.snapshot_for_request(TARGET)
    assert again.snapshot_id == first.snapshot_id == ref.get(TARGET).snapshot_id
    assert LatestMarketSnapshotStore(tmp_path / "cand.jsonl").get(TARGET).snapshot_id == first.snapshot_id


def test_missing_or_unreadable_state_never_drops_snapshots_and_reports_reason(tmp_path, clock):
    _build(tmp_path, n=5)
    store, uni = _universe(tmp_path, clock, activate=False)
    _ingest(tmp_path / "store", store, [TARGET], "2026-11-13", 70)
    assert store.get(TARGET) is not None
    stats = store.journal_stats()["snapshot_universe"]
    assert stats["state"] == "missing" and stats["reason"] == "membership_state_missing"
    assert uni.sweep()["swept"] is False and store.get(TARGET) is not None
    assert uni.snapshot_for_request(TARGET) is not None  # served as before
    _ingest(tmp_path / "store", store, ["2330.TWSE"], "2026-11-13", 70)
    uni.initialize()
    assert store.get("2330.TWSE") is not None
    (tmp_path / "cand.universe.json").write_text("{not json")
    store2 = LatestMarketSnapshotStore(tmp_path / "cand.jsonl")
    uni2 = SnapshotUniverse(store2, state_path=tmp_path / "cand.universe.json", storage_root=tmp_path / "store", clock=clock)
    live_before = store2.live_symbols()
    assert uni2.sweep()["swept"] is False and store2.live_symbols() == live_before
    stats = store2.journal_stats()["snapshot_universe"]
    assert stats["state"] == "unreadable" and stats["reason"] == "membership_state_unreadable"
    assert uni2.admits("ANYTHING.TWSE")  # writes still admitted
    with pytest.raises(Exception):
        uni2.initialize()  # never overwrites an unreadable state


def test_governed_and_env_symbols_are_always_members(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2454.TWSE")
    statics = static_member_symbols()
    assert {"2330", "0050", "2317", "2454"} <= set(statics)  # hosted probe / agora readiness / persona symbols
    for i, day in enumerate(_days(5)):
        _ingest(tmp_path / "store", None, ["2330.TWSE", "2454.TWSE", TARGET], day, i)
    store, uni = _universe(tmp_path, clock)
    assert {"2330.TWSE", "2454.TWSE"} <= set(store.live_symbols())  # seeded at activation
    clock.state["now"] += timedelta(days=60)
    uni.sweep()
    for symbol in ("2330.TW", "2330.TWSE", "2454.TWSE", "0050.TWSE", "2317.TWSE"):
        assert uni.is_member(symbol)
    assert store.get("2330.TWSE") is not None and store.get(TARGET) is None


def test_unknown_symbol_is_not_a_member_and_misses_are_cached(tmp_path, clock):
    _build(tmp_path, n=3)
    _store, uni = _universe(tmp_path, clock)
    assert uni.snapshot_for_request("1101.TWSE") is None and not uni.is_member("1101.TWSE")
    assert uni._last_seed["records"] == 0
    uni._last_seed = None
    assert uni.snapshot_for_request("1101.TWSE") is None and uni._last_seed is None  # cached miss


def test_snapshot_route_seeds_through_runtime_universe(tmp_path, clock):
    _build(tmp_path, n=4)
    store, uni = _universe(tmp_path, clock)
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(latest_market_snapshot_store=store, snapshot_universe=uni)))
    client = TestClient(app)
    response = client.get("/api/source-ingest/snapshots/latest", params={"symbol": "3008.TW"})
    assert response.status_code == 200 and response.json()["snapshot_id"].startswith("mss-")
    assert client.get("/api/source-ingest/snapshots/latest", params={"symbol": "1101.TWSE"}).status_code == 404
    assert client.post("/api/source-ingest/snapshots/universe/sweep").json()["swept"] is True


# ----- SOURCE-UNIVERSE-SEED-READS-DAILY-BARS-20261118: the seed reads daily bars behind the named setting -----
def _bars_universe(tmp_path, clock, monkeypatch, mode="daily_bars"):
    pytest.importorskip("pyarrow")  # the daily bars store needs it
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", mode)
    return _universe(tmp_path, clock)


def _spy_partitions(monkeypatch):
    from services.source_ingestion.daily_bars_reader import DailyBarsReader

    opened: list[str] = []
    original = DailyBarsReader._read_one_date

    def spy(self, trade_date, wanted, columns=None):
        opened.append(trade_date)
        return original(self, trade_date, wanted, columns)

    monkeypatch.setattr(DailyBarsReader, "_read_one_date", spy)
    return opened


def test_daily_bars_seed_equals_run_file_seed_except_named_differences(tmp_path, clock, monkeypatch):
    pytest.importorskip("pyarrow")
    _build(tmp_path, noise=5, n=65)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "run_files")
    _s0, uni0 = _universe(tmp_path, clock, name="old")
    old = uni0.snapshot_for_request(TARGET)
    assert uni0._last_seed["path"] == "run_files" and uni0._last_seed["files_read"] >= 60
    opened = _spy_partitions(monkeypatch)
    _s1, uni1 = _bars_universe(tmp_path, clock, monkeypatch)
    opened.clear()  # activation seeded the governed static members; count the request alone
    new = uni1.snapshot_for_request("3008.TW")
    assert uni1._last_seed["path"] == "daily_bars", str(uni1._last_seed)
    assert uni1._last_seed["files_read"] == 0
    assert len(opened) <= 60
    assert uni1._last_seed["partitions_read"] == 60
    assert new.closes == old.closes and len(new.points) == 60
    assert [p.open for p in new.points] == [p.open for p in old.points]
    assert [p.ingest_run_id for p in new.points] == [p.ingest_run_id for p in old.points]
    assert [p.source_id for p in new.points] == [p.source_id for p in old.points]
    assert [p.market for p in new.points] == [p.market for p in old.points]
    assert [p.event_time[:10] for p in new.points] == [p.event_time[:10] for p in old.points]
    assert new.calendar_evidence == old.calendar_evidence and new.market == old.market
    assert uni1.is_member(TARGET)


def test_daily_bars_seed_falls_back_when_fewer_dates_or_closes_than_needed(tmp_path, clock, monkeypatch):
    pytest.importorskip("pyarrow")
    _build(tmp_path, n=30)  # 30 stored dates < 60 needed
    _store, uni = _bars_universe(tmp_path, clock, monkeypatch)
    snap = uni.snapshot_for_request(TARGET)
    assert snap is not None and len(snap.points) == 30
    assert uni._last_seed["path"] == "run_files" and "30 trade dates" in uni._last_seed["fallback_reason"]

    other = tmp_path / "second"
    other.mkdir()
    for i, day in enumerate(_days(65)):  # the symbol trades on only 10 of the 65 stored dates
        symbols = [TARGET] if i % 6 == 0 else ["2330.TWSE"]
        _ingest(other / "store", None, symbols, day, i)
    store2 = LatestMarketSnapshotStore(other / "c.jsonl")
    uni2 = SnapshotUniverse(store2, state_path=other / "c.json", storage_root=other / "store",
                            connector_market=lambda _c: "TW", clock=clock)
    uni2.initialize()
    snap2 = uni2.snapshot_for_request(TARGET)
    assert snap2 is not None and len(snap2.points) == 11
    assert uni2._last_seed["path"] == "run_files" and "holds 10 closes" in uni2._last_seed["fallback_reason"]


def test_run_files_setting_changes_nothing(tmp_path, clock, monkeypatch):
    _build(tmp_path, n=65)
    opened = _spy_partitions(monkeypatch)
    _store, uni = _bars_universe(tmp_path, clock, monkeypatch, mode="run_files")
    assert uni.snapshot_for_request(TARGET) is not None
    assert uni._last_seed["path"] == "run_files" and "fallback_reason" not in uni._last_seed and not opened
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "bogus")
    with pytest.raises(ValueError, match="PANTHEON_DAILY_PRICE_READER"):
        uni.seed("3008.TWSE")  # an unknown value fails closed with a readable reason


def test_daily_bars_seed_miss_is_bounded_and_cached(tmp_path, clock, monkeypatch):
    pytest.importorskip("pyarrow")
    _build(tmp_path, n=65)
    opened = _spy_partitions(monkeypatch)
    _store, uni = _bars_universe(tmp_path, clock, monkeypatch)
    opened.clear()
    monkeypatch.setattr(SnapshotUniverse, "_partition_dates", lambda self: pytest.fail("run files scanned"))
    assert uni.snapshot_for_request("1101.TWSE") is None and not uni.is_member("1101.TWSE")
    assert uni._last_seed["path"] == "daily_bars" and uni._last_seed["records"] == 0 and len(opened) <= 60
    uni._last_seed = None
    assert uni.snapshot_for_request("1101.TWSE") is None and uni._last_seed is None  # cached miss
