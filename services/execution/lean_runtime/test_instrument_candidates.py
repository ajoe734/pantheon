"""Candidate generator tests over the captured TAIFEX Date=20261106 fixtures."""

from __future__ import annotations

import json
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from services.execution.lean_runtime import instrument_candidates as ic
from services.source_ingestion.connectors.taiwan_official import taifex_ssf_normalized_rows

FIXTURES = Path(__file__).parent / "fixtures" / "taifex_20261106"
# Friday 2026-11-06 is the last regular session captured; Monday 2026-11-09 decides.
DECISION_DAY = "2026-11-09"
BUDGET = {"available_cash": 1_000_000.0, "gross_limit": 2_000_000.0, "gross_used": 0.0}


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return {
        "SingleStockFuturesMargining": _load("SingleStockFuturesMargining"),
        "SingleStockFuturesETFMargining": _load("SingleStockFuturesETFMargining"),
        "DailyMarketReportFut": _load("DailyMarketReportFut"),
        "FinalSettlementPriceSSF": _load("FinalSettlementPriceSSF"),
        "stockLists": (FIXTURES / "stockLists.html").read_text(encoding="utf-8"),
    }


@pytest.fixture(scope="module")
def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in taifex_ssf_normalized_rows(payload)]


def _build(rows: list[dict[str, Any]], underlying: str, day: str = DECISION_DAY, **kwargs: Any) -> list[dict[str, Any]]:
    kwargs.setdefault("cash_price", 2200.0)
    kwargs.setdefault("cash_price_date", "2026-11-06")
    return ic.build_candidates(underlying, day, {}, BUDGET, rows_reader=lambda: rows, **kwargs)


def _by_id(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["instrument_id"]: c for c in candidates}


def test_2330_candidates_reproduce_the_fixture_numbers(rows: list[dict[str, Any]]) -> None:
    candidates = _by_id(_build(rows, "2330"))
    assert set(candidates) == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}
    assert candidates["eq:2330"]["side_capability"] == ["long"]
    qff, cdf = candidates["ssf:QFF:202611"], candidates["ssf:CDF:202611"]
    for future in (qff, cdf):
        assert future["side_capability"] == ["long", "short"]
        assert future["price"] == 2133.0
        assert future["price_date"] == "2026-11-06"
        assert future["price_basis"] == "futures_daily_settlement_prev_session"
        assert future["kind"] == "stock_future"
        assert future["underlying"] == "2330"
        assert future["expiry"] == future["last_trading_day"] == "2026-11-18"
    assert (qff["unit"], qff["multiplier"], qff["initial_margin_per_unit"]) == (100, 100, 28795.5)
    assert (cdf["unit"], cdf["multiplier"], cdf["initial_margin_per_unit"]) == (2000, 2000, 575910.0)
    assert qff["maintenance_margin_per_unit"] == 22076.55
    assert qff["liquidity"] == {"volume": 126386, "open_interest": 65034}


def test_2317_candidates(rows: list[dict[str, Any]]) -> None:
    candidates = _by_id(_build(rows, "2317", cash_price=230.0))
    assert set(candidates) == {"eq:2317", "ssf:DHF:202611"}
    dhf = candidates["ssf:DHF:202611"]
    assert (dhf["unit"], dhf["price"], dhf["initial_margin_per_unit"]) == (2000, 225.0, 60750.0)
    assert dhf["liquidity"]["volume"] == 14260


def test_0050_candidates_use_fixed_margins(rows: list[dict[str, Any]]) -> None:
    candidates = _by_id(_build(rows, "0050", cash_price=92.0))
    assert set(candidates) == {"eq:0050", "ssf:SRF:202611", "ssf:NYF:202611"}
    srf, nyf = candidates["ssf:SRF:202611"], candidates["ssf:NYF:202611"]
    assert (srf["unit"], srf["initial_margin_per_unit"], srf["maintenance_margin_per_unit"]) == (1000, 8700.0, 6700.0)
    assert (nyf["unit"], nyf["initial_margin_per_unit"], nyf["maintenance_margin_per_unit"]) == (10000, 87000.0, 67000.0)
    assert srf["price"] == 92.35
    assert srf["fee_per_unit_per_side"] == 8.0 and nyf["fee_per_unit_per_side"] == 12.5
    assert srf["tax_rate_per_side"] == nyf["tax_rate_per_side"] == 0.00002


def test_fee_and_required_fields_on_every_candidate(rows: list[dict[str, Any]]) -> None:
    required = {
        "instrument_id", "kind", "underlying", "side_capability", "unit", "multiplier", "price", "price_date",
        "price_basis", "initial_margin_per_unit", "maintenance_margin_per_unit", "fee_per_unit_per_side",
        "tax_rate_per_side", "expiry", "last_trading_day", "liquidity", "source_refs", "max_units_by_risk",
    }
    for candidate in _build(rows, "2330", decision_id="d-1"):
        assert required <= set(candidate)
        assert candidate["decision_id"] == "d-1"
    qff = _by_id(_build(rows, "2330"))["ssf:QFF:202611"]
    assert qff["fee_per_unit_per_side"] == 5.0
    assert qff["source_refs"][0]["price_date"] == "2026-11-06"


@pytest.mark.parametrize("symbol", ["2330.TW", "2330.TWSE", " 2330 "])
def test_underlying_is_normalised(rows: list[dict[str, Any]], symbol: str) -> None:
    assert {c["instrument_id"] for c in _build(rows, symbol)} == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}


def test_unknown_underlying_gets_only_the_cash_share(rows: list[dict[str, Any]]) -> None:
    assert [c["instrument_id"] for c in _build(rows, "9999")] == ["eq:9999"]


def test_stale_data_drops_futures_but_keeps_cash(rows: list[dict[str, Any]]) -> None:
    # Decision on Tuesday: the previous trading day is Monday 11-09, newer than the Date 20261106 rows.
    assert [c["instrument_id"] for c in _build(rows, "2330", day="2026-11-10")] == ["eq:2330"]
    # Monday: previous trading day is Friday 11-06, so the same rows are fresh.
    assert len(_build(rows, "2330", day="2026-11-09")) == 3


def test_holiday_moves_the_previous_trading_day(rows: list[dict[str, Any]]) -> None:
    holidays = {date(2026, 11, 6)}
    # With 11-06 a holiday, the previous trading day of 11-09 is 11-05 and the rows are still fresh.
    assert len(_build(rows, "2330", holidays=holidays)) == 3
    # With 11-09 a holiday, a decision on 11-10 looks back to 11-06 and the rows are fresh.
    assert len(_build(rows, "2330", day="2026-11-10", holidays={date(2026, 11, 9)})) == 3


def test_missing_margin_row_is_not_a_candidate(rows: list[dict[str, Any]]) -> None:
    broken = [dict(row) for row in rows]
    for row in broken:
        if row["contract"] == "QFF":
            row["initial_margin_rate"] = None
    ids = {c["instrument_id"] for c in _build(broken, "2330")}
    assert ids == {"eq:2330", "ssf:CDF:202611"}
    for row in broken:
        if row["contract"] == "CDF":
            row["margin_date"] = ""
    assert {c["instrument_id"] for c in _build(broken, "2330")} == {"eq:2330"}


def test_normalizer_emits_no_row_for_a_contract_without_a_margin_table_entry(payload: dict[str, Any]) -> None:
    trimmed = dict(payload)
    trimmed["SingleStockFuturesMargining"] = [r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "QFF"]
    assert "QFF" not in {row["contract"] for row in taifex_ssf_normalized_rows(trimmed)}


def test_zero_volume_and_open_interest_is_not_a_candidate(rows: list[dict[str, Any]]) -> None:
    quiet = [dict(row) for row in rows]
    for row in quiet:
        if row["contract"] == "QFF":
            row["volume"], row["open_interest"] = 0, 0
        if row["contract"] == "CDF":
            row["volume"], row["open_interest"] = 0, 5
    assert {c["instrument_id"] for c in _build(quiet, "2330")} == {"eq:2330", "ssf:CDF:202611"}


def _shifted(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    out = [dict(row) for row in rows]
    for row in out:
        row["price_date"] = row["margin_date"] = day
    return out


def test_only_the_nearest_month_whose_last_trading_day_is_after_the_decision_day(rows: list[dict[str, Any]]) -> None:
    shifted = _shifted(rows, "2026-11-16")
    before = _by_id(_build(shifted, "2330", day="2026-11-17"))
    assert {"ssf:QFF:202611", "ssf:CDF:202611"} <= set(before)
    assert not any(i.endswith(("202612", "202703")) for i in before)
    # 2026-11-18 is the last trading day itself: 202611 is no longer strictly after, so 202612 is nearest.
    on_expiry = _by_id(_build(_shifted(rows, "2026-11-17"), "2330", day="2026-11-18"))
    assert set(on_expiry) == {"eq:2330", "ssf:QFF:202612", "ssf:CDF:202612"}
    assert on_expiry["ssf:QFF:202612"]["price"] == 2139.0
    assert on_expiry["ssf:QFF:202612"]["last_trading_day"] == "2026-12-16"


def test_nearest_month_failing_a_rule_does_not_fall_through_to_the_next_month(rows: list[dict[str, Any]]) -> None:
    quiet = [dict(row) for row in rows]
    for row in quiet:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["volume"], row["open_interest"] = 0, 0
    assert {c["instrument_id"] for c in _build(quiet, "2330")} == {"eq:2330", "ssf:CDF:202611"}


def test_last_trading_day_shifts_past_a_holiday() -> None:
    assert ic.last_trading_day("202611") == date(2026, 11, 18)
    assert ic.last_trading_day("202611", {date(2026, 11, 18)}) == date(2026, 11, 19)
    assert ic.last_trading_day("202611", {date(2026, 11, 18), date(2026, 11, 19)}) == date(2026, 11, 20)


def test_adjusted_codes_and_spread_rows_are_never_candidates(rows: list[dict[str, Any]]) -> None:
    extra = []
    for contract, month in (("CDA", "202611"), ("CD1", "202611"), ("CDF", "202611/202612"), ("QFFX", "202611")):
        row = dict(next(r for r in rows if r["contract"] == "QFF" and r["contract_month"] == "202611"))
        row["contract"], row["contract_month"] = contract, month
        extra.append(row)
    assert {c["instrument_id"] for c in _build([*rows, *extra], "2330")} == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}


def test_normalizer_skips_after_hours_and_spread_rows(rows: list[dict[str, Any]]) -> None:
    assert all("/" not in row["contract_month"] for row in rows)
    assert all(row["raw_market_row"]["TradingSession"] == "一般" for row in rows)
    assert {"QFF", "CDF", "DHF", "SRF", "NYF", "CAF"} == {row["contract"] for row in rows}


def test_missing_dataset_yields_only_the_cash_share_with_reason(tmp_path: Path) -> None:
    for kwargs in (
        {"rows_reader": lambda: None},
        {"rows_reader": lambda: []},
        {"normalized_root": tmp_path},
        {},
    ):
        candidates = ic.build_candidates(
            "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", **kwargs
        )
        assert [c["instrument_id"] for c in candidates] == ["eq:2330"]
        assert {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"} in candidates[0]["source_refs"]


def test_cash_candidate_without_a_price_is_flagged(rows: list[dict[str, Any]]) -> None:
    [cash, *_] = ic.build_candidates("2330", DECISION_DAY, {}, BUDGET, rows_reader=lambda: rows)
    assert cash["max_units_by_risk"] == 0 and cash["price"] is None
    assert {"reason": "cash_price_unavailable"} in cash["source_refs"]


def test_max_units_by_risk(rows: list[dict[str, Any]]) -> None:
    budget = {"available_cash": 100_000.0, "gross_limit": 500_000.0}
    candidates = _by_id(
        ic.build_candidates("2330", DECISION_DAY, {"gross_notional": 100_000.0}, budget, cash_price=2000.0,
                            cash_price_date="2026-11-06", rows_reader=lambda: rows)
    )
    # Remaining gross 400,000. Cash share: min(100,000, 400,000) / 2000 = 50.
    assert candidates["eq:2330"]["max_units_by_risk"] == 50
    # QFF: margin 28,795.5 -> 3 units by cash; notional 213,300 -> 1 unit by gross; min is 1.
    assert candidates["ssf:QFF:202611"]["max_units_by_risk"] == 1
    # CDF: margin 575,910 > cash -> 0.
    assert candidates["ssf:CDF:202611"]["max_units_by_risk"] == 0


def test_missing_budget_fails_closed(rows: list[dict[str, Any]]) -> None:
    candidates = ic.build_candidates("2330", DECISION_DAY, {}, {}, cash_price=2200.0, rows_reader=lambda: rows)
    assert {c["max_units_by_risk"] for c in candidates} == {0}


def _write_partition(root: Path, day: str, run: str, rows: list[dict[str, Any]]) -> None:
    path = root / "normalized" / "taifex_ssf_contracts" / f"date={day}"
    path.mkdir(parents=True, exist_ok=True)
    with (path / f"{run}.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"source_id": "x", "metadata": {"normalized_row": row}}) + "\n")


def test_candidates_from_the_normalized_dataset_on_disk(rows: list[dict[str, Any]], tmp_path: Path) -> None:
    _write_partition(tmp_path, "2026-11-06", "run1", rows)
    candidates = ic.build_candidates(
        "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in candidates} == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}


def test_on_disk_multi_run_deduplicates_to_newest_snapshot_without_fallback(
    rows: list[dict[str, Any]], tmp_path: Path
) -> None:
    newer_quiet = [dict(row) for row in rows]
    for row in newer_quiet:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["volume"], row["open_interest"] = 0, 0
    _write_partition(tmp_path, "2026-11-06", "a-old", rows)
    _write_partition(tmp_path, "2026-11-06", "b-new", newer_quiet)

    candidates = _by_id(
        ic.build_candidates(
            "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
        )
    )
    assert set(candidates) == {"eq:2330", "ssf:CDF:202611"}

    newer_repriced = [dict(row) for row in rows]
    for row in newer_repriced:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["settlement_price"] = 2150.0
            row["volume"], row["open_interest"] = 12, 34
    _write_partition(tmp_path, "2026-11-06", "c-newest", newer_repriced)
    repriced = _by_id(
        ic.build_candidates(
            "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
        )
    )
    assert set(repriced) == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}
    assert repriced["ssf:QFF:202611"]["price"] == 2150.0
    assert repriced["ssf:QFF:202611"]["liquidity"] == {"volume": 12, "open_interest": 34}


def test_snapshot_deduplication_is_order_independent(rows: list[dict[str, Any]]) -> None:
    stale = _shifted(rows, "2026-11-05")
    for row in stale:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["settlement_price"] = 1999.0
    for combined in ([*stale, *rows], [*rows, *stale]):
        built = _by_id(_build(combined, "2330"))
        assert set(built) == {"eq:2330", "ssf:QFF:202611", "ssf:CDF:202611"}
        assert built["ssf:QFF:202611"]["price"] == 2133.0

    older_avail = [dict(row, available_time="2026-11-06T16:30:00Z", ingest_time="2026-11-06T16:31:00Z") for row in rows]
    newer_avail_quiet = [
        dict(row, available_time="2026-11-06T17:00:00Z", ingest_time="2026-11-06T16:31:00Z") for row in rows
    ]
    for row in newer_avail_quiet:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["volume"], row["open_interest"] = 0, 0
    for combined in ([*older_avail, *newer_avail_quiet], [*newer_avail_quiet, *older_avail]):
        assert {c["instrument_id"] for c in _build(combined, "2330")} == {"eq:2330", "ssf:CDF:202611"}

    older_ingest = [dict(row, available_time="2026-11-06T16:30:00Z", ingest_time="2026-11-07T01:00:00Z") for row in rows]
    newer_ingest_no_margin = [
        dict(row, available_time="2026-11-06T16:30:00Z", ingest_time="2026-11-07T02:00:00Z") for row in rows
    ]
    for row in newer_ingest_no_margin:
        if row["contract"] == "QFF" and row["contract_month"] == "202611":
            row["margin_date"] = ""
    for combined in ([*older_ingest, *newer_ingest_no_margin], [*newer_ingest_no_margin, *older_ingest]):
        assert {c["instrument_id"] for c in _build(combined, "2330")} == {"eq:2330", "ssf:CDF:202611"}


def test_reader_is_bounded(rows: list[dict[str, Any]], tmp_path: Path) -> None:
    for index in range(5):
        _write_partition(tmp_path, f"2026-11-0{index + 1}", "run", rows)
    assert len(ic.read_normalized_ssf_rows(tmp_path, max_rows=10)) == 10
    assert len(ic.read_normalized_ssf_rows(tmp_path, max_partitions=2)) == 2 * len(rows)
    assert len(ic.read_normalized_ssf_rows(tmp_path, max_files=1)) == len(rows)
    assert ic.read_normalized_ssf_rows(tmp_path / "absent") is None
    (tmp_path / "normalized" / "taifex_ssf_contracts" / "date=2026-11-09").mkdir()
    big = tmp_path / "normalized" / "taifex_ssf_contracts" / "date=2026-11-09" / "big.jsonl"
    big.write_text("x" * 100, encoding="utf-8")
    assert ic.read_normalized_ssf_rows(tmp_path, max_file_bytes=10, max_partitions=1) == []


def test_generator_makes_no_network_call(rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    assert len(_build(rows, "2330")) == 3


def test_normalization_to_disk_snapshot_removal_does_not_resurrect_removed_contract(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from services.source_ingestion.connectors.taiwan_official import TaifexDerivativesChipAdapter
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    adapter = TaifexDerivativesChipAdapter()
    writer = MarketDataStorageWriter(tmp_path)

    old_records = adapter.records_from_payload(
        payload,
        dataset="taifex_ssf_contracts",
        available_time="2026-11-06T16:30:00Z",
    )
    writer.write_run(
        result=SimpleNamespace(records=old_records, run=SimpleNamespace(ingest_run_id="run-1630")),
        connector=adapter.connector(),
    )

    trimmed = dict(
        payload,
        SingleStockFuturesMargining=[r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "QFF"],
    )
    new_records = adapter.records_from_payload(
        trimmed,
        dataset="taifex_ssf_contracts",
        available_time="2026-11-06T17:00:00Z",
    )
    assert "QFF" not in {r.metadata["contract"] for r in new_records}
    writer.write_run(
        result=SimpleNamespace(records=new_records, run=SimpleNamespace(ingest_run_id="run-1700")),
        connector=adapter.connector(),
    )

    candidates = ic.build_candidates(
        "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in candidates} == {"eq:2330", "ssf:CDF:202611"}


def test_scoped_normalization_to_disk_removes_absent_margin_without_clobbering_other_underlyings(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    old_rows = list(taifex_ssf_normalized_rows(payload, available_time="2026-11-06T16:30:00Z"))
    _write_partition(tmp_path, "2026-11-06", "01-unscoped-1630", old_rows)

    # Scoped 17:00Z run for 2330 with QFF removed from SingleStockFuturesMargining.
    trimmed_2330 = dict(
        payload,
        SingleStockFuturesMargining=[r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "QFF"],
    )
    scoped_2330_rows = list(
        taifex_ssf_normalized_rows(
            trimmed_2330,
            underlying_codes=["2330"],
            available_time="2026-11-06T17:00:00Z",
        )
    )
    assert {r["contract"] for r in scoped_2330_rows} == {"CDF"}
    _write_partition(tmp_path, "2026-11-06", "02-scoped-2330-1700", scoped_2330_rows)

    # 2330 drops QFF (not resurrected from 16:30Z) while keeping CDF.
    candidates_2330 = ic.build_candidates(
        "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in candidates_2330} == {"eq:2330", "ssf:CDF:202611"}

    # Other underlyings (2317, 0050) were outside the 17:00Z scope and remain intact from 16:30Z.
    candidates_2317 = ic.build_candidates(
        "2317", DECISION_DAY, {}, BUDGET, cash_price=230.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in candidates_2317} == {"eq:2317", "ssf:DHF:202611"}
    candidates_0050 = ic.build_candidates(
        "0050", DECISION_DAY, {}, BUDGET, cash_price=92.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in candidates_0050} == {"eq:0050", "ssf:SRF:202611", "ssf:NYF:202611"}

    # Scoped 17:30Z run for 2317 where DHF (the only 2317 contract) is removed from margins emits a tombstone.
    trimmed_2317 = dict(
        payload,
        SingleStockFuturesMargining=[r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "DHF"],
    )
    scoped_2317_rows = list(
        taifex_ssf_normalized_rows(
            trimmed_2317,
            underlying_codes=["2317"],
            available_time="2026-11-06T17:30:00Z",
        )
    )
    assert len(scoped_2317_rows) == 1 and scoped_2317_rows[0].get("tombstone") is True
    _write_partition(tmp_path, "2026-11-06", "03-scoped-2317-1730", scoped_2317_rows)

    after_2317_removal = ic.build_candidates(
        "2317", DECISION_DAY, {}, BUDGET, cash_price=230.0, cash_price_date="2026-11-06", normalized_root=tmp_path
    )
    assert {c["instrument_id"] for c in after_2317_removal} == {"eq:2317"}
    # 2330 and 0050 remain unaffected by the 2317 tombstone.
    assert {
        c["instrument_id"]
        for c in ic.build_candidates(
            "2330", DECISION_DAY, {}, BUDGET, cash_price=2200.0, cash_price_date="2026-11-06", normalized_root=tmp_path
        )
    } == {"eq:2330", "ssf:CDF:202611"}


def test_missing_nearest_settlement_does_not_fall_back_to_farther_month_or_older_snapshot(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from services.source_ingestion.connectors.taiwan_official import TaifexDerivativesChipAdapter
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    adapter = TaifexDerivativesChipAdapter()
    writer = MarketDataStorageWriter(tmp_path)

    # First verify single-snapshot normalization -> build_candidates rejects QFF 202611 without selecting QFF 202612.
    unpriced_front = dict(
        payload,
        DailyMarketReportFut=[
            {**row, "SettlementPrice": "-"}
            if (
                row.get("Contract") == "QFF"
                and str(row.get("ContractMonth(Week)")).strip() == "202611"
                and row.get("TradingSession") == "一般"
            )
            else row
            for row in payload["DailyMarketReportFut"]
        ],
    )
    in_memory_rows = adapter.normalized_rows_from_payload(
        unpriced_front,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T16:30:00Z",
    )
    in_memory_candidates = ic.build_candidates(
        "2330",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=2200.0,
        cash_price_date="2026-11-06",
        rows_reader=lambda: in_memory_rows,
    )
    assert [c["instrument_id"] for c in in_memory_candidates] == ["eq:2330", "ssf:CDF:202611"]

    # Now verify normalization -> storage -> reader when an earlier 16:30 snapshot had priced QFF 202611.
    old_records = adapter.records_from_payload(
        payload,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T16:30:00Z",
    )
    writer.write_run(
        result=SimpleNamespace(records=old_records, run=SimpleNamespace(ingest_run_id="run-1630")),
        connector=adapter.connector(),
    )
    new_records = adapter.records_from_payload(
        unpriced_front,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T17:00:00Z",
    )
    writer.write_run(
        result=SimpleNamespace(records=new_records, run=SimpleNamespace(ingest_run_id="run-1700")),
        connector=adapter.connector(),
    )

    disk_candidates = ic.build_candidates(
        "2330",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=2200.0,
        cash_price_date="2026-11-06",
        normalized_root=tmp_path,
    )
    assert [c["instrument_id"] for c in disk_candidates] == ["eq:2330", "ssf:CDF:202611"]


def test_empty_scoped_snapshot_when_contract_removed_from_margin_and_market_tables_evicts_old_snapshot(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from services.source_ingestion.connectors.taiwan_official import TaifexDerivativesChipAdapter
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    adapter = TaifexDerivativesChipAdapter()
    writer = MarketDataStorageWriter(tmp_path)

    old_2330 = adapter.records_from_payload(
        payload,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T16:20:00Z",
    )
    writer.write_run(
        result=SimpleNamespace(records=old_2330, run=SimpleNamespace(ingest_run_id="run-1620")),
        connector=adapter.connector(),
    )
    old_2317 = adapter.records_from_payload(
        payload,
        dataset="taifex_ssf_contracts",
        contracts=["2317"],
        available_time="2026-11-06T16:30:00Z",
    )
    writer.write_run(
        result=SimpleNamespace(records=old_2317, run=SimpleNamespace(ingest_run_id="run-1630")),
        connector=adapter.connector(),
    )

    no_dhf = {
        **payload,
        "SingleStockFuturesMargining": [
            row for row in payload["SingleStockFuturesMargining"] if row["Contract"] != "DHF"
        ],
        "DailyMarketReportFut": [
            row for row in payload["DailyMarketReportFut"] if row["Contract"] != "DHF"
        ],
    }
    new_2317 = adapter.records_from_payload(
        no_dhf,
        dataset="taifex_ssf_contracts",
        contracts=["2317"],
        available_time="2026-11-06T17:00:00Z",
    )
    assert len(new_2317) == 1
    assert new_2317[0].metadata["normalized_row"]["tombstone"] is True
    writer.write_run(
        result=SimpleNamespace(records=new_2317, run=SimpleNamespace(ingest_run_id="run-1700")),
        connector=adapter.connector(),
    )

    candidates_2317 = ic.build_candidates(
        "2317",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=230.0,
        cash_price_date="2026-11-06",
        normalized_root=tmp_path,
    )
    assert [c["instrument_id"] for c in candidates_2317] == ["eq:2317"]

    candidates_2330 = ic.build_candidates(
        "2330",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=2200.0,
        cash_price_date="2026-11-06",
        normalized_root=tmp_path,
    )
    assert [c["instrument_id"] for c in candidates_2330] == ["eq:2330", "ssf:CDF:202611", "ssf:QFF:202611"]


def test_more_than_six_random_ingest_runs_preserve_latest_removal_and_unpriced_snapshots(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from services.source_ingestion.connectors.taiwan_official import TaifexDerivativesChipAdapter
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    assert ic.MAX_FILES == 6
    adapter = TaifexDerivativesChipAdapter()
    old_run_ids = (
        "ingest-ffffffffffff",
        "ingest-eeeeeeeeeeee",
        "ingest-dddddddddddd",
        "ingest-cccccccccccc",
        "ingest-bbbbbbbbbbbb",
        "ingest-aaaaaaaaaaaa",
        "ingest-999999999999",
    )

    # 1. Margin removal in newest snapshot named ingest-000000000000 after >6 lexicographically larger runs.
    removal_root = tmp_path / "margin_removal"
    removal_writer = MarketDataStorageWriter(removal_root)
    for run_id in old_run_ids:
        old_records = adapter.records_from_payload(
            payload,
            dataset="taifex_ssf_contracts",
            contracts=["2330"],
            available_time="2026-11-06T16:30:00Z",
        )
        removal_writer.write_run(
            result=SimpleNamespace(records=old_records, run=SimpleNamespace(ingest_run_id=run_id)),
            connector=adapter.connector(),
        )
    trimmed_qff = {
        **payload,
        "SingleStockFuturesMargining": [
            row for row in payload["SingleStockFuturesMargining"] if row["Contract"] != "QFF"
        ],
    }
    newest_removed = adapter.records_from_payload(
        trimmed_qff,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T17:00:00Z",
    )
    removal_writer.write_run(
        result=SimpleNamespace(records=newest_removed, run=SimpleNamespace(ingest_run_id="ingest-000000000000")),
        connector=adapter.connector(),
    )
    assert [
        c["instrument_id"]
        for c in ic.build_candidates(
            "2330",
            DECISION_DAY,
            {},
            BUDGET,
            cash_price=2200.0,
            cash_price_date="2026-11-06",
            normalized_root=removal_root,
        )
    ] == ["eq:2330", "ssf:CDF:202611"]
    default_disk_rows = ic.read_normalized_ssf_rows(removal_root)
    assert default_disk_rows is not None
    assert [
        c["instrument_id"]
        for c in ic.build_candidates(
            "2330",
            DECISION_DAY,
            {},
            BUDGET,
            cash_price=2200.0,
            cash_price_date="2026-11-06",
            rows_reader=lambda: default_disk_rows,
        )
    ] == ["eq:2330", "ssf:CDF:202611"]

    # 2. Unpriced front-month in newest snapshot named ingest-000000000000 (even with same available_time)
    #    plus >6 scoped 2317 runs with tombstone in ingest-000000000001.
    mixed_root = tmp_path / "unpriced_and_tombstone"
    mixed_writer = MarketDataStorageWriter(mixed_root)
    for run_id in old_run_ids:
        old_2330 = adapter.records_from_payload(
            payload,
            dataset="taifex_ssf_contracts",
            contracts=["2330"],
            available_time="2026-11-06T16:30:00Z",
        )
        mixed_writer.write_run(
            result=SimpleNamespace(records=old_2330, run=SimpleNamespace(ingest_run_id=f"{run_id[:15]}a")),
            connector=adapter.connector(),
        )
        old_2317 = adapter.records_from_payload(
            payload,
            dataset="taifex_ssf_contracts",
            contracts=["2317"],
            available_time="2026-11-06T16:30:00Z",
        )
        mixed_writer.write_run(
            result=SimpleNamespace(records=old_2317, run=SimpleNamespace(ingest_run_id=f"{run_id[:15]}b")),
            connector=adapter.connector(),
        )

    unpriced_qff_front = {
        **payload,
        "DailyMarketReportFut": [
            {**row, "SettlementPrice": "-"}
            if (
                row.get("Contract") == "QFF"
                and str(row.get("ContractMonth(Week)")).strip() == "202611"
                and row.get("TradingSession") == "一般"
            )
            else row
            for row in payload["DailyMarketReportFut"]
        ],
    }
    newest_unpriced_2330 = adapter.records_from_payload(
        unpriced_qff_front,
        dataset="taifex_ssf_contracts",
        contracts=["2330"],
        available_time="2026-11-06T16:30:00Z",
    )
    mixed_writer.write_run(
        result=SimpleNamespace(records=newest_unpriced_2330, run=SimpleNamespace(ingest_run_id="ingest-000000000000")),
        connector=adapter.connector(),
    )

    no_dhf = {
        **payload,
        "SingleStockFuturesMargining": [
            row for row in payload["SingleStockFuturesMargining"] if row["Contract"] != "DHF"
        ],
        "DailyMarketReportFut": [
            row for row in payload["DailyMarketReportFut"] if row["Contract"] != "DHF"
        ],
    }
    newest_tombstone_2317 = adapter.records_from_payload(
        no_dhf,
        dataset="taifex_ssf_contracts",
        contracts=["2317"],
        available_time="2026-11-06T17:05:00Z",
    )
    mixed_writer.write_run(
        result=SimpleNamespace(records=newest_tombstone_2317, run=SimpleNamespace(ingest_run_id="ingest-000000000001")),
        connector=adapter.connector(),
    )

    assert [
        c["instrument_id"]
        for c in ic.build_candidates(
            "2330",
            DECISION_DAY,
            {},
            BUDGET,
            cash_price=2200.0,
            cash_price_date="2026-11-06",
            normalized_root=mixed_root,
        )
    ] == ["eq:2330", "ssf:CDF:202611"]
    assert [
        c["instrument_id"]
        for c in ic.build_candidates(
            "2317",
            DECISION_DAY,
            {},
            BUDGET,
            cash_price=230.0,
            cash_price_date="2026-11-06",
            normalized_root=mixed_root,
        )
    ] == ["eq:2317"]


def test_bounded_reader_fails_closed_when_latest_coverage_cannot_be_established(
    rows: list[dict[str, Any]], tmp_path: Path
) -> None:
    # 1. Row-truncated newest snapshot fails closed in build_candidates.
    trunc_root = tmp_path / "truncated"
    _write_partition(trunc_root, "2026-11-06", "ingest-aaaaaaaaaaaa", rows)
    partial_rows = ic.read_normalized_ssf_rows(trunc_root, max_rows=2)
    assert partial_rows is not None and len(partial_rows) == 2
    candidates_trunc = ic.build_candidates(
        "2330",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=2200.0,
        cash_price_date="2026-11-06",
        rows_reader=lambda: partial_rows,
    )
    assert [c["instrument_id"] for c in candidates_trunc] == ["eq:2330"]
    assert {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"} in candidates_trunc[0]["source_refs"]

    # 2. Ambiguous tied random-UUID snapshots with identical timestamps and conflicting rows fail closed.
    ambig_root = tmp_path / "ambiguous"
    same_stamp_full = [
        dict(r, available_time="2026-11-06T17:00:00Z", ingest_time="2026-11-06T17:00:01Z")
        for r in rows
        if r["underlying_code"] == "2330"
    ]
    same_stamp_removed = [
        dict(r, snapshot_contracts=["CDF"], snapshot_absent_contracts=["QFF"])
        for r in same_stamp_full
        if r["contract"] != "QFF"
    ]
    _write_partition(ambig_root, "2026-11-06", "ingest-ffffffffffff", same_stamp_full)
    _write_partition(ambig_root, "2026-11-06", "ingest-000000000000", same_stamp_removed)
    candidates_ambig = ic.build_candidates(
        "2330",
        DECISION_DAY,
        {},
        BUDGET,
        cash_price=2200.0,
        cash_price_date="2026-11-06",
        normalized_root=ambig_root,
    )
    assert [c["instrument_id"] for c in candidates_ambig] == ["eq:2330"]
    assert {"reason": "candidates_unavailable", "dataset": "taifex_ssf_contracts"} in candidates_ambig[0]["source_refs"]

    # 3. Scan limit exceeded fails closed.
    assert ic.read_normalized_ssf_rows(ambig_root, max_scan_files=1) == []


def test_older_market_date_replay_with_newer_available_time_aligns_contract_master_and_candidates(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    import importlib.util
    import sys
    from types import SimpleNamespace

    from services.source_ingestion.connectors.taiwan_official import (
        TaifexDerivativesChipAdapter,
        taifex_ssf_normalized_rows,
    )
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    spec = importlib.util.spec_from_file_location(
        "contract_master",
        Path(__file__).resolve().parents[2] / "data-plane" / "models" / "contract_master.py",
    )
    assert spec and spec.loader
    cm_mod = importlib.util.module_from_spec(spec)
    sys.modules["contract_master"] = cm_mod
    spec.loader.exec_module(cm_mod)
    ContractMaster = cm_mod.ContractMaster

    payload_20261109 = {
        **payload,
        "SingleStockFuturesMargining": [
            {**r, "Date": "20261109"} for r in payload["SingleStockFuturesMargining"]
        ],
        "SingleStockFuturesETFMargining": [
            {**r, "Date": "20261109"} for r in payload["SingleStockFuturesETFMargining"]
        ],
        "DailyMarketReportFut": [
            {
                **r,
                "Date": "20261109",
                "SettlementPrice": "2150"
                if (
                    r.get("Contract") == "QFF"
                    and str(r.get("ContractMonth(Week)")).strip() == "202611"
                    and r.get("TradingSession") == "一般"
                )
                else r.get("SettlementPrice"),
            }
            for r in payload["DailyMarketReportFut"]
        ],
    }

    adapter = TaifexDerivativesChipAdapter()
    replay_root = tmp_path / "replay_priced"
    writer = MarketDataStorageWriter(replay_root)
    rec_20261109 = adapter.records_from_payload(
        payload_20261109,
        dataset="taifex_ssf_contracts",
        contracts=["2330", "2317"],
        available_time="2026-11-09T16:30:00Z",
    )
    for r in rec_20261109:
        r.metadata["ingest_time"] = "2026-11-09T16:30:01Z"
        r.metadata["normalized_row"]["ingest_time"] = "2026-11-09T16:30:01Z"
    rec_replay_20261106 = adapter.records_from_payload(
        payload,
        dataset="taifex_ssf_contracts",
        contracts=["2330", "2317"],
        available_time="2026-11-10T06:00:00Z",
    )
    for r in rec_replay_20261106:
        r.metadata["ingest_time"] = "2026-11-10T06:00:01Z"
        r.metadata["normalized_row"]["ingest_time"] = "2026-11-10T06:00:01Z"
    writer.write_run(
        result=SimpleNamespace(records=rec_20261109, run=SimpleNamespace(ingest_run_id="ingest-000000000000")),
        connector=adapter.connector(),
    )
    writer.write_run(
        result=SimpleNamespace(records=rec_replay_20261106, run=SimpleNamespace(ingest_run_id="ingest-ffffffffffff")),
        connector=adapter.connector(),
    )

    disk_rows = ic.read_normalized_ssf_rows(replay_root)
    masters = {m.contract_id: m for m in ContractMaster.from_ssf_normalized_rows(disk_rows)}
    assert masters["ssf:QFF:202611"].metadata_json["settlement_price"] == 2150.0
    assert masters["ssf:QFF:202611"].metadata_json["settlement_price_date"] == "2026-11-09"
    assert masters["ssf:QFF:202611"].metadata_json["margin_date"] == "2026-11-09"

    cands = {
        c["instrument_id"]: c
        for c in ic.build_candidates(
            "2330",
            date(2026, 11, 10),
            {},
            BUDGET,
            cash_price=2200.0,
            cash_price_date="2026-11-09",
            normalized_root=replay_root,
        )
    }
    assert cands["ssf:QFF:202611"]["price"] == 2150.0
    assert cands["ssf:QFF:202611"]["price_date"] == "2026-11-09"
    assert cands["ssf:QFF:202611"]["source_refs"][0]["margin_date"] == "2026-11-09"

    # Older unpriced/tombstone replayed with newer available_time must NOT evict newer 2026-11-09 snapshot.
    unpriced_20261106 = {
        **payload,
        "DailyMarketReportFut": [
            {**r, "SettlementPrice": "-"}
            if (
                r.get("Contract") == "QFF"
                and str(r.get("ContractMonth(Week)")).strip() == "202611"
                and r.get("TradingSession") == "一般"
            )
            else r
            for r in payload["DailyMarketReportFut"]
        ],
    }
    no_dhf_20261106 = {
        **payload,
        "SingleStockFuturesMargining": [
            r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "DHF"
        ],
        "DailyMarketReportFut": [
            r for r in payload["DailyMarketReportFut"] if r["Contract"] != "DHF"
        ],
    }
    rows_20261109 = [dict(r.metadata["normalized_row"]) for r in rec_20261109]
    replayed_old_unpriced = [
        dict(r, ingest_time="2026-11-10T06:00:01Z")
        for r in taifex_ssf_normalized_rows(
            unpriced_20261106,
            underlying_codes=["2330"],
            available_time="2026-11-10T06:00:00Z",
        )
    ]
    replayed_old_tombstone = [
        dict(r, ingest_time="2026-11-10T06:00:01Z")
        for r in taifex_ssf_normalized_rows(
            no_dhf_20261106,
            underlying_codes=["2317"],
            available_time="2026-11-10T06:00:00Z",
        )
    ]
    for ordered in (
        [*rows_20261109, *replayed_old_unpriced, *replayed_old_tombstone],
        [*replayed_old_unpriced, *replayed_old_tombstone, *rows_20261109],
    ):
        m_map = {m.contract_id: m for m in ContractMaster.from_ssf_normalized_rows(ordered)}
        assert m_map["ssf:QFF:202611"].metadata_json["settlement_price"] == 2150.0
        assert "ssf:DHF:202611" in m_map
        c_2330 = {
            c["instrument_id"]: c
            for c in ic.build_candidates(
                "2330",
                date(2026, 11, 10),
                {},
                BUDGET,
                cash_price=2200.0,
                cash_price_date="2026-11-09",
                rows_reader=lambda ordered=ordered: ordered,
            )
        }
        assert c_2330["ssf:QFF:202611"]["price"] == 2150.0
        c_2317 = {
            c["instrument_id"]: c
            for c in ic.build_candidates(
                "2317",
                date(2026, 11, 10),
                {},
                BUDGET,
                cash_price=230.0,
                cash_price_date="2026-11-09",
                rows_reader=lambda ordered=ordered: ordered,
            )
        }
        assert "ssf:DHF:202611" in c_2317

