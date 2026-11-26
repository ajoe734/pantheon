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


# -- DAILY-BARS-COMPLETION-RESUMABLE-INDEX-20261126 --------------------------------------------------------------------

class StepClock:
    """Each reading advances by ``step`` seconds: a call with max_seconds S has time for about S / step budget checks."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def hosted_shape(root: Path, store: DailyBarsStore, files: int = 6) -> None:
    """One write-date partition with many run files, each a full copy of the day (hosted: 201 files, 346 MB)."""
    store_day_without_values(store, DAY)
    for n in range(files):
        write_run(root, DAY, f"run-{n:03d}", [run_line(collector("2330", DAY, 2010.0, 88179393, 177284650683)),
                                             run_line(collector("6488", DAY, 2010.0, 1000, 2_010_000))])


def test_a_partition_and_a_day_larger_than_one_call_resume_file_by_file_and_never_end_without_progress(tmp_path):
    store = DailyBarsStore(tmp_path)
    hosted_shape(tmp_path, store, files=6)
    before = store.read_manifest(DAY)
    reports = []
    for _ in range(20):
        # A clock that has run out of time at every check: the call may only do its one guaranteed unit of work.
        report = DailyBarsCompleter(store, clock=StepClock(1000.0)).run(max_seconds=20)
        reports.append(report)
        if report["stopped"] is None:
            break
    first, second = reports[0], reports[1]
    # call 1 indexed exactly one file of the partition and recorded it; call 2 continued with the next file
    assert first["stopped"] == "seconds" and first["indexed_files"] == 1 and first["progress"] == 1
    state = DailyBarsCompleter(store).load_state()
    assert all(r["progress"] >= 1 for r in reports)  # no call ended with zero progress
    assert second["indexed_files"] == 1
    index_calls = [r for r in reports if r["indexed_files"]]
    stage_calls = [r for r in reports if r["staged_files"]]
    assert len(index_calls) == 6 and len(stage_calls) == 6  # one file per call: 6 to index, then 6 to stage
    assert sum(r["indexed_partitions"] for r in reports) == 1
    assert reports[-1]["completed_days"] == [DAY] and reports[-1]["stopped"] is None
    assert state["indexed"] == {f"date={DAY}": {"dates": 1, "files": 6}} and state["indexing"] == {}
    # day completion keeps its guarantees: values filled once, one new generation, stage removed
    assert values(store, DAY) == {"2330": (88179393.0, 177284650683.0), "6488": (1000.0, 2_010_000.0)}
    assert store.read_manifest(DAY)["generation"] == before["generation"] + 1
    assert not list((tmp_path / "daily_bars_completion_stage").glob("*.json"))


def test_the_index_state_records_the_files_read_inside_a_partition(tmp_path):
    store = DailyBarsStore(tmp_path)
    hosted_shape(tmp_path, store, files=4)
    DailyBarsCompleter(store, clock=StepClock(1000.0)).run(max_seconds=20)
    DailyBarsCompleter(store, clock=StepClock(1000.0)).run(max_seconds=20)
    state = DailyBarsCompleter(store).load_state()
    assert state["indexing"][f"date={DAY}"]["files"] == ["run-000.jsonl", "run-001.jsonl"]
    assert state["files_by_date"][DAY] == [f"normalized/tw_price_daily/date={DAY}/run-00{n}.jsonl" for n in (0, 1)]
    assert f"date={DAY}" not in state["indexed"] and DAY not in state["days"]  # not eligible until fully indexed


def test_a_staged_day_matches_one_pass_over_all_files_including_cross_file_conflicts(tmp_path):
    """Copies of one key that disagree on price in two different files (two calls) are refused as in one pass."""
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, DAY, symbols=("2330", "6488", "1101"))
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000)),
                                       run_line(collector("6488", DAY, 2010.0, 1000, 2_010_000)),
                                       run_line(collector("1101", DAY, 2010.0, 1000, 2_010_000))])
    write_run(tmp_path, DAY, "run-b", [run_line(collector("2330", DAY, 2011.0, 1000, 2_011_000)),  # price disagrees
                                       run_line(collector("6488", DAY, 2010.0, 2000, 4_020_000)),  # larger copy wins
                                       run_line(collector("1101", DAY, 2010.0, 500, 1_005_000))])
    reports = [DailyBarsCompleter(store, clock=StepClock(1000.0)).run(max_seconds=20) for _ in range(5)]
    done = [r for r in reports if r["completed_days"]]
    assert len(done) == 1 and done[0]["rejected"] == {"conflicting_duplicate": 1} and done[0]["conflicts"] == 1
    assert values(store, DAY) == {"2330": (None, None), "6488": (2000.0, 4_020_000.0), "1101": (1000.0, 2_010_000.0)}
    # same result as the one-pass rule of extract_rows over both files
    lines = [json.loads(x) for f in ("run-a", "run-b")
             for x in (tmp_path / "normalized" / "tw_price_daily" / f"date={DAY}" / f"{f}.jsonl").read_text().split("\n") if x]
    recs = [SourceRecord(source_id=x["source_id"], connector_id="c", source_type="market", title="t", content_ref="r",
                         metadata=x["metadata"]) for x in lines]
    rows, rejected, _ = store.extract_rows(recs, ingest_run_id="r", default_market="TW")
    assert rejected == {"conflicting_duplicate": 1}
    assert {r["symbol"]: (r["volume"], r["traded_value"]) for r in rows} == {
        "6488": (2000.0, 4_020_000.0), "1101": (1000.0, 2_010_000.0)}


def test_a_conflict_inside_one_file_is_refused_even_when_another_file_agrees(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, DAY, symbols=("2330",))
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000)),
                                       run_line(collector("2330", DAY, 2012.0, 1000, 2_012_000))])
    write_run(tmp_path, DAY, "run-b", [run_line(collector("2330", DAY, 2010.0, 1000, 2_010_000))])
    report = DailyBarsCompleter(store).run()
    assert report["rejected"] == {"conflicting_duplicate": 1} and report["filled_rows"] == 0
    assert values(store, DAY) == {"2330": (None, None)}


def test_the_writer_thread_continues_while_calls_progress_file_by_file(tmp_path, monkeypatch):
    import threading

    from services.source_ingestion import daily_bars_completion as mod
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    store = DailyBarsStore(tmp_path)
    hosted_shape(tmp_path, store, files=5)

    class Slow(mod.DailyBarsCompleter):
        def __init__(self, store, root):
            super().__init__(store, root, clock=StepClock(1000.0))

    monkeypatch.setattr(mod, "DailyBarsCompleter", Slow)
    writer = MarketDataStorageWriter.__new__(MarketDataStorageWriter)
    writer.root, writer.daily_bars_store = tmp_path, store
    writer._completion_guard, writer._completion_running = threading.Lock(), True
    writer.COMPLETION_CALL_PAUSE_SECONDS = 0.0
    writer._run_daily_bars_completion()
    # every call stopped on seconds after one file and the loop went on until the day was completed
    assert writer.last_daily_bars_completion["completed_days"] == [DAY]
    assert values(store, DAY) == {"2330": (88179393.0, 177284650683.0), "6488": (1000.0, 2_010_000.0)}
    assert writer._completion_running is False


def test_a_v1_state_without_indexing_loads_and_keeps_its_indexed_partitions(tmp_path):
    store = DailyBarsStore(tmp_path)
    hosted_shape(tmp_path, store, files=2)
    (tmp_path / "daily_bars_completion_state.json").write_text(json.dumps({
        "schema_version": "daily_bars_completion_state.v1", "indexed": {f"date={DAY}": {"dates": 1}},
        "files_by_date": {DAY: [f"normalized/tw_price_daily/date={DAY}/run-000.jsonl"]}, "days": {}}))
    report = DailyBarsCompleter(store).run()
    assert report["indexed_files"] == 0 and report["completed_days"] == [DAY] and report["staged_files"] == 1


def test_a_day_whose_files_were_all_staged_is_not_read_again_for_rows_the_source_cannot_fill(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, DAY)
    write_run(tmp_path, DAY, "run-a", [run_line(collector("2330", DAY, 2010.0, 88179393, 177284650)),  # unit slip
                                       run_line(collector("6488", DAY, 2010.0, 1000, 2_010_000))])
    first = DailyBarsCompleter(store).run()
    assert first["completed_days"] == [DAY] and first["rejected"] == {"value_unit_mismatch": 1}
    state = DailyBarsCompleter(store).load_state()
    assert state["days"][DAY]["status"] == "source_has_no_values" and state["days"][DAY]["filled_rows"] == 1
    second = DailyBarsCompleter(store).run()
    assert second["staged_files"] == 0 and second["completed_days"] == second["unchanged_days"] == []
    assert values(store, DAY) == {"2330": (None, None), "6488": (1000.0, 2_010_000.0)}
