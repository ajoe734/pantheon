"""FinMind fundamentals: statutory availability, point-in-time derived fields, revision label."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FINMIND_FUNDAMENTALS_VALUE_REVISION,
    FinMindTaiwanFundamentalsAdapter,
    finmind_fundamental_venues,
    finmind_fundamental_window,
    finmind_fundamental_windows,
)
from services.source_ingestion.feature_catalogue import parse_time, statutory_available_date

FIXTURE = json.loads((Path(__file__).parent / "fixtures/tw_fundamentals_official/tsmc_2330_finmind_shape.json").read_text())
INGEST = "2024-06-01T00:00:00Z"
ADAPTER = FinMindTaiwanFundamentalsAdapter()


def rows(dataset):
    return ADAPTER.rows_from_payloads(dataset, FIXTURE, symbols=["2330.TW"], ingest_time=INGEST)


def by_event(items):
    return {r["event_time"]: r for r in items}


def test_monthly_revenue_deadline_is_tenth_of_next_month():
    out = by_event(rows("tw_monthly_revenue"))
    assert out["2024-01-31"]["available_time"].startswith("2024-02-10")
    assert out["2023-12-31"]["available_time"].startswith("2024-01-10")
    for r in out.values():
        assert parse_time(r["available_time"]) > parse_time(r["event_time"])


def test_revenue_yoy_mom_use_published_amounts_and_only_available_months():
    jan = by_event(rows("tw_monthly_revenue"))["2024-01-31"]
    assert jan["yoy"] == pytest.approx((215785 / 200050 - 1) * 100)  # published +7.9%
    assert jan["mom"] == pytest.approx((215785 / 176299 - 1) * 100)  # published +22.4%
    assert jan["revenue"] == 215785000.0
    assert jan["value_revision"] == FINMIND_FUNDAMENTALS_VALUE_REVISION == "provider_latest_revision_not_first_release"
    # The earliest month has no earlier available comparison: derived fields are absent, not guessed.
    first = by_event(rows("tw_monthly_revenue"))["2023-01-31"]
    assert first["yoy"] is None and first["mom"] is None


@pytest.mark.parametrize("event,deadline", [
    ("2024-03-31", "2024-05-15"), ("2024-06-30", "2024-08-14"),
    ("2024-09-30", "2024-11-14"), ("2023-12-31", "2024-03-31"), ("2024-12-31", "2025-03-31"),
])
def test_quarter_statutory_deadlines_including_leap_year_annual(event, deadline):
    assert statutory_available_date("tw_quarterly_statements", event).isoformat() == deadline


def test_statements_available_no_earlier_than_deadline_and_ratios():
    out = by_event(rows("tw_financial_statement"))
    assert out["2024-03-31"]["available_time"].startswith("2024-05-15")
    assert out["2023-12-31"]["available_time"].startswith("2024-03-31")
    assert out["2023-06-30"]["available_time"].startswith("2023-08-14")
    q1 = out["2024-03-31"]
    assert q1["gross_margin"] == pytest.approx(314700 / 592644)
    assert q1["operating_margin"] == pytest.approx(249000 / 592644)
    assert q1["roe"] == pytest.approx(225490000000 / 2900000000000)
    assert q1["eps"] == 8.7
    assert q1["value_revision"] == FINMIND_FUNDAMENTALS_VALUE_REVISION


def test_valuation_row_and_symbol_suffix_required():
    pe = rows("tw_valuation_bwibbu")[0]
    assert (pe["pe_ratio"], pe["pb_ratio"], pe["dividend_yield"]) == (26.5, 6.6, 1.4)
    with pytest.raises(Exception):
        finmind_fundamental_venues(["2330"])


def test_history_windows_cover_two_years():
    start, end = finmind_fundamental_window("tw_financial_statement", "2024-06-03")
    assert (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days >= 730
    start, _ = finmind_fundamental_window("tw_monthly_revenue", "2024-06-03")
    assert (datetime.fromisoformat("2024-06-03") - datetime.fromisoformat(start)).days >= 730 + 366
    # Daily valuation reaches two years of history through rotating daily pages.
    covered = set()
    for offset in range(14):
        s, e = finmind_fundamental_window("tw_valuation_bwibbu", f"2024-06-{3 + offset:02d}" if offset < 27 else "2024-06-30")
        covered.add(s)
    assert len(covered) > 1


def test_valuation_windows_are_latest_plus_one_page_not_the_span_between():
    for day in range(1, 29):
        latest, page = finmind_fundamental_windows("tw_valuation_bwibbu", f"2024-06-{day:02d}")
        total = sum((datetime.fromisoformat(e) - datetime.fromisoformat(s)).days for s, e in (latest, page))
        assert total <= 65
        assert datetime.fromisoformat(page[1]) <= datetime.fromisoformat(latest[0])


def test_records_carry_point_in_time_metadata():
    records = ADAPTER.records_from_rows(rows("tw_monthly_revenue"), trace_id="t")
    for rec in records:
        meta = rec.metadata
        assert meta["available_time"] and meta["ingest_time"] and meta["event_time"]
        assert meta["value_revision"] == "provider_latest_revision_not_first_release"
