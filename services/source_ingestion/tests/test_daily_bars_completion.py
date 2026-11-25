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
    assert heavy["stopped"] == "memory" and heavy["completed_days"] == []
    assert DailyBarsCompleter(store).run()["completed_days"] == [DAY]


def test_the_controller_runs_the_completion_and_a_failure_never_fails_the_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(cw, "_request_json", lambda url, **kw: calls.append(url) or {"completed_days": [DAY], "filled_rows": 3})
    ok = cw.run_daily_bars_completion(api_url="http://x/", controller_token="t")
    assert ok["ok"] and ok["filled_rows"] == 3 and calls == ["http://x/api/source-ingest/daily-bars/complete"]

    def boom(url, **kw):
        raise cw.ControllerTickError("controller", "HTTPError: 503")
    monkeypatch.setattr(cw, "_request_json", boom)
    bad = cw.run_daily_bars_completion(api_url="http://x", controller_token="t")
    assert bad["ok"] is False and "503" in bad["reason"]


def test_volume_report_counts_days_over_five_percent_and_separates_days_newer_than_the_fix(tmp_path):
    store = DailyBarsStore(tmp_path)
    store_day_without_values(store, OLD)  # defective stored day
    store.write_records([collector("2330", DAY, 2010.0, 1000, 2_010_000)], ingest_run_id="ok", default_market="TW")
    report = DailyBarsCompleter(store).volume_report(since="2026-11-19")
    assert report["days_over_threshold"] == 1 and report["days_over_threshold_newer_than_fix"] == 0
    store_day_without_values(store, "2026-11-23")
    report = DailyBarsCompleter(store).volume_report(since="2026-11-19")
    assert report["days_over_threshold"] == 2 and report["days_over_threshold_newer_than_fix"] == 1

