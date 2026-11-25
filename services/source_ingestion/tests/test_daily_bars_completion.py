"""DAILY-BARS-COLLECTOR-VOLUME-VALUE-20261125: collector names map to volume and traded value; stored days complete."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion import controller_worker as cw
from services.source_ingestion.connectors import SourceRecord
from services.source_ingestion.daily_bars_completion import DailyBarsCompleter
from services.source_ingestion.daily_bars_store import DailyBarsStore, value_unit_problem

DAY = "2026-11-20"
OLD = "2026-11-19"


def collector(symbol: str, day: str, close: float, volume_shares=None, turnover_twd=None, market: str = "TW", **extra):
    """Shape of a collector price-history row: metadata carries volume_shares and turnover_twd, never volume/value."""
    meta = {"symbol": symbol, "trade_date": day, "dataset": "tw_price_daily", "market": market,
            "open": close, "high": close, "low": close, "close": close,
            "volume_shares": volume_shares, "turnover_twd": turnover_twd, **extra}
    return SourceRecord(source_id=f"s-{symbol}", connector_id="c", source_type="market", title="t",
                        content_ref=f"ref-{symbol}", metadata=meta)


def run_line(rec: SourceRecord) -> str:
    return json.dumps({"source_id": rec.source_id, "metadata": dict(rec.metadata)})


def write_run(root: Path, write_date: str, name: str, lines: list[str], gz: bool = False) -> None:
    folder = root / "normalized" / "tw_price_daily" / f"date={write_date}"
    folder.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if gz:
        with gzip.open(folder / f"{name}.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write(text)
    else:
        (folder / f"{name}.jsonl").write_text(text, encoding="utf-8")


def store_day_without_values(store: DailyBarsStore, day: str, symbols=("2330", "6488")):
    """What hosted holds today: prices stored, volume and traded value null (the mapping defect)."""
    recs = [collector(s, day, 2010.0) for s in symbols]
    store.write_records(recs, ingest_run_id="old", default_market="TW")


def values(store: DailyBarsStore, day: str) -> dict[str, tuple]:
    return {r["symbol"]: (r["volume"], r["traded_value"]) for r in store._read_day_rows(store.read_manifest(day)).values()}


def test_extract_rows_reads_the_collectors_own_names_for_twse_and_tpex(tmp_path):
    store = DailyBarsStore(tmp_path)
    recs = [
        collector("2330", DAY, 2010.0, 88179393, 177284650683),  # the official 2330 row of 2026-11-20
        collector("6488", DAY, 500.0, 1_000_000, 500_000_000, market="TPEx", exchange="TPEx"),
    ]
    rows, rejected, _ = store.extract_rows(recs, ingest_run_id="r", default_market="TW")
    got = {r["symbol"]: (r["volume"], r["traded_value"]) for r in rows}
    assert got == {"2330": (88179393.0, 177284650683.0), "6488": (1_000_000.0, 500_000_000.0)} and not rejected


def test_a_value_far_from_close_times_volume_is_refused_with_a_reason(tmp_path):
    store = DailyBarsStore(tmp_path)
    rows, rejected, _ = store.extract_rows(
        [collector("2330", DAY, 2010.0, 88179393, 177284650)], ingest_run_id="r", default_market="TW")  # thousands slip
    assert rows == [] and rejected == {"value_unit_mismatch": 1}
    assert value_unit_problem(10.0, 100.0, 1000.0) is None and value_unit_problem(10.0, 100.0, 1e6) == "value_unit_mismatch"


def test_zero_is_a_value_and_a_not_traded_row_stays_null(tmp_path):
    store = DailyBarsStore(tmp_path)
    zero = collector("1101", DAY, 40.0, 0, 0)
    idle = collector("1102", DAY, 0, None, None, trading_status="not_traded")
    for key in ("open", "high", "low", "close"):
        idle.metadata.pop(key)
    rows, rejected, _ = store.extract_rows([zero, idle], ingest_run_id="r", default_market="TW")
    got = {r["symbol"]: (r["volume"], r["traded_value"]) for r in rows}
    assert got == {"1101": (0.0, 0.0), "1102": (None, None)} and not rejected


def test_stored_days_are_completed_newest_first_with_a_new_generation_and_never_overwritten(tmp_path):
    store = DailyBarsStore(tmp_path)
    for day in (DAY, OLD):
        store_day_without_values(store, day)
    for day in (DAY, OLD):
        write_run(tmp_path, day, "run-a", [run_line(collector("2330", day, 2010.0, 88179393, 177284650683)),
                                           run_line(collector("6488", day, 2010.0, 1000, 2_010_000))])
    before = store.read_manifest(DAY)
    first = DailyBarsCompleter(store).run(max_days=1)
    assert first["completed_days"] == [DAY] and first["stopped"] == "days" and first["filled_rows"] == 2
    after = store.read_manifest(DAY)
    assert after["generation"] == before["generation"] + 1 and after["content_digest"] != before["content_digest"]
    assert values(store, DAY) == {"2330": (88179393.0, 177284650683.0), "6488": (1000.0, 2_010_000.0)}
    assert values(store, OLD) == {"2330": (None, None), "6488": (None, None)}  # resumable: older day waits
    second = DailyBarsCompleter(store).run(max_days=1)
    assert second["completed_days"] == [OLD]
    third = DailyBarsCompleter(store).run()
    assert third["completed_days"] == [] and third["filled_rows"] == 0 and third["pending_days"] == 0
    assert store.read_manifest(DAY)["generation"] == after["generation"]  # nothing rewritten


def test_an_existing_value_is_kept_and_a_disagreeing_source_is_refused_and_counted(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([collector("2330", DAY, 2010.0, 88179393, 177284650683),
                         collector("6488", DAY, 2010.0, None, None)], ingest_run_id="old", default_market="TW")
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 90000000, 180000000000)),
                                       run_line(collector("6488", DAY, 2010.0, 1000, 2_010_000))])
    report = DailyBarsCompleter(store).run()
    assert report["conflicts"] == 1 and report["filled_rows"] == 1
    assert values(store, DAY) == {"2330": (88179393.0, 177284650683.0), "6488": (1000.0, 2_010_000.0)}


def test_a_day_without_retained_source_rows_is_listed_not_guessed(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, OLD)
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000))], gz=True)
    report = DailyBarsCompleter(store).run()
    assert report["unretained_days"] == [OLD] and report["unretained_count"] == 1
    assert values(store, OLD) == {"2330": (None, None), "6488": (None, None)}


def test_the_call_stops_on_seconds_and_memory_and_the_state_resumes(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, DAY)
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000))])
    ticks = iter(range(0, 1000, 100))
    slow = DailyBarsCompleter(store, clock=lambda: next(ticks)).run(max_seconds=50)
    assert slow["stopped"] == "seconds" and slow["completed_days"] == []
    heavy = DailyBarsCompleter(store, rss=lambda: 5000.0).run(max_rss_mb=100)
    assert heavy["stopped"] == "memory_ceiling" and heavy["completed_days"] == [] and heavy["failed"] is True
    assert DailyBarsCompleter(store).run()["completed_days"] == [DAY]


def test_a_process_already_above_768_mb_still_completes_and_growth_is_what_is_bounded(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, DAY)
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000))])
    report = DailyBarsCompleter(store, rss=lambda: 2800.0).run()  # hosted-like: far above the old flat 768 MB cap
    assert report["completed_days"] == [DAY] and report["failed"] is False and report["baseline_rss_mb"] == 2800.0
    # growth beyond the declared increment stops the call
    readings = iter([2800.0] + [4000.0] * 50)
    grown = DailyBarsCompleter(store, rss=lambda: next(readings)).run(max_growth_mb=768)
    assert grown["stopped"] in (None, "memory_growth")
    # a first check already above the absolute ceiling is a visible failure in the health field the probe reads
    store2 = DailyBarsStore(tmp_path / "b")
    store_day_without_values(store2, DAY)
    write_run(tmp_path / "b", DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000))])
    DailyBarsCompleter(store2, rss=lambda: 5200.0).run()
    volume = store2.health()["volume"]
    assert volume["completion_failed"] is True and volume["last_run"]["stopped"] == "memory_ceiling"


def test_health_reports_days_over_five_percent_and_the_writer_completes_in_the_background(tmp_path, monkeypatch):
    import time

    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, OLD)
    write_run(tmp_path, OLD, "run-a", [run_line(collector("2330", OLD, 2010.0, 1000, 2_010_000)),
                                       run_line(collector("6488", OLD, 2010.0, 1000, 2_010_000))])
    assert store.volume_health()["unexamined_days"] == 1
    DailyBarsCompleter(store).run(max_days=0)  # examine nothing yet: state exists only after a day is looked at
    writer = MarketDataStorageWriter.__new__(MarketDataStorageWriter)
    import threading
    writer.root, writer.daily_bars_store = tmp_path, store
    writer._completion_guard, writer._completion_running, writer._completion_last = threading.Lock(), False, -1e9
    writer.last_daily_bars_completion = None
    monkeypatch.setenv("SOURCE_INGEST_DAILY_BARS_COMPLETION", "on")
    writer._start_daily_bars_completion()
    writer._start_daily_bars_completion()  # throttled and single flight: the second call starts nothing
    for _ in range(100):
        if writer.last_daily_bars_completion is not None:
            break
        time.sleep(0.05)
    assert writer.last_daily_bars_completion["completed_days"] == [OLD]
    assert values(store, OLD) == {"2330": (1000.0, 2_010_000.0), "6488": (1000.0, 2_010_000.0)}
    health = store.health()["volume"]
    assert health["days_over_threshold"] == 0 and health["examined_days"] == 1


def test_health_lists_a_stored_day_that_is_still_over_five_percent(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, OLD)  # no run files: unretained, values stay null
    DailyBarsCompleter(store).run()
    health = store.volume_health()
    assert health["days_over_threshold"] == 1 and health["unretained_days"] == 1
    assert health["days"][0]["trade_date"] == OLD and health["days"][0]["status"] == "unretained"


def test_the_writer_thread_keeps_calling_until_the_budget_stops_are_over_and_ends_on_memory(tmp_path, monkeypatch):
    import threading

    from services.source_ingestion import daily_bars_completion as mod
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    reports = iter([
        {"completed_days": ["a"], "unchanged_days": [], "indexed_partitions": 0, "stopped": "days"},
        {"completed_days": ["b"], "unchanged_days": [], "indexed_partitions": 0, "stopped": "days"},
        {"completed_days": [], "unchanged_days": [], "indexed_partitions": 0, "stopped": "memory_ceiling"},
        {"completed_days": ["never"], "unchanged_days": [], "indexed_partitions": 0, "stopped": None},
    ])
    calls = []

    class Fake:
        def __init__(self, *a, **k): ...
        def run(self):
            calls.append(1)
            return next(reports)

    monkeypatch.setattr(mod, "DailyBarsCompleter", Fake)
    writer = MarketDataStorageWriter.__new__(MarketDataStorageWriter)
    writer.root, writer.daily_bars_store = tmp_path, DailyBarsStore(tmp_path)
    writer._completion_guard, writer._completion_running = threading.Lock(), True
    writer.COMPLETION_CALL_PAUSE_SECONDS = 0.0
    writer._run_daily_bars_completion()
    assert len(calls) == 3 and writer.last_daily_bars_completion["stopped"] == "memory_ceiling"
    assert writer._completion_running is False
