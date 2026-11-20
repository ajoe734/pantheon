"""TW-OFFICIAL-CHIP-FULL-MARKET-AND-SESSION-DATE-20261120: whole-session storage with provider session dates."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services.source_ingestion.chip_publication_schedule import (
    ChipSessionUndecidable,
    FULL_MARKET_CHIP_CONNECTORS,
    TAIPEI_TZ,
    evaluate_chip_row_coverage,
    resolve_chip_row_session,
)
from services.source_ingestion.configured import PROVIDER_OWNED_ADAPTER_MAX_RECORDS
from services.source_ingestion.connector_definitions import (
    TW_CHIP_REFRESH_PROFILES,
    TW_OFFICIAL_CHIP_FULL_MARKET_MAX_RECORDS,
    get_connector_definition,
)
from services.source_ingestion.connectors import SourceEvidenceError
from services.source_ingestion.connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter
from services.source_ingestion.persona_source_reconciler import chip_profile_provider

TWSE_MARGIN = "tw-official-twse-margin-balances"
TPEX_MARGIN = "tw-official-tpex-margin-balances"
TPEX_INST = "tw-official-tpex-institutional-flow"
# Fetch at 18:50 Taipei on 2026-11-19: MI_MARGN for 11/19 publishes next morning, so the rows are the 11/18 session.
FETCH_1850 = datetime(2026, 11, 19, 18, 50, tzinfo=TAIPEI_TZ)


def _margin_rows(count: int) -> list[dict]:
    return [
        {"股票代號": f"{1000 + i}", "股票名稱": f"S{i}", "融資買進": "1", "融資今日餘額": "10"}
        for i in range(count)
    ]


def _inst_rows(count: int, day: str = "1151119") -> list[dict]:
    return [{"Date": day, "SecuritiesCompanyCode": f"{3000 + i}", "CompanyName": f"T{i}"} for i in range(count)]


def _adapter(connector_id: str) -> TaiwanOfficialMarketDatasetAdapter:
    # chip_profile_provider builds the adapter with max_records=500; the full-market profile lifts it to 3000.
    return chip_profile_provider(connector_id)


def test_three_profiles_are_full_market_with_budget_above_provider_rows() -> None:
    assert FULL_MARKET_CHIP_CONNECTORS == {TWSE_MARGIN, TPEX_MARGIN, TPEX_INST}
    for cid in FULL_MARKET_CHIP_CONNECTORS:
        assert TW_CHIP_REFRESH_PROFILES[cid]["max_records"] == TW_OFFICIAL_CHIP_FULL_MARKET_MAX_RECORDS
        assert get_connector_definition(cid).default_limits["max_records"] == 3000
        assert _adapter(cid).fetch_config()["max_records"] == 3000
    # recorded provider response: TWSE MI_MARGN returned 1310 rows (chip_publication_schedule probes) > old 500
    assert 1310 <= TW_OFFICIAL_CHIP_FULL_MARKET_MAX_RECORDS <= PROVIDER_OWNED_ADAPTER_MAX_RECORDS


def test_every_row_stored_when_provider_returns_more_than_500() -> None:
    records = _adapter(TWSE_MARGIN).records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(1310), fetch_time=FETCH_1850, trace_id="t"
    )
    assert len(records) == 1310
    assert len({r.metadata["symbol"] for r in records}) == 1310


def test_twse_margin_rows_without_date_get_published_session_not_fetch_date() -> None:
    records = _adapter(TWSE_MARGIN).records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(3), fetch_time=FETCH_1850, trace_id="t"
    )
    for record in records:
        assert record.metadata["date"] == "2026-11-18"
        assert record.metadata["event_time"] == "2026-11-18"
        assert record.metadata["normalized_row"]["date"] == "2026-11-18"
    # after the next-morning rebuild the 11/19 session is the published one
    later = datetime(2026, 11, 20, 6, 0, tzinfo=TAIPEI_TZ)
    records = _adapter(TWSE_MARGIN).records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(1), fetch_time=later, trace_id="t"
    )
    assert records[0].metadata["date"] == "2026-11-19"


def test_run_date_is_not_used_as_row_session_for_full_market_profiles() -> None:
    adapter = _adapter(TWSE_MARGIN)
    direct = adapter.records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(1), trade_date="2026-11-19", fetch_time=FETCH_1850, trace_id="t"
    )
    assert direct[0].metadata["date"] == "2026-11-18"
    request = {
        "dataset": "tw_margin_short_balance",
        "venues": ["TWSE"],
        "payload": _margin_rows(2),
        "run_date": "2026-11-19",  # fetch date: must not become the session
    }
    connector = adapter.connector()
    fetch = {
        "adapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
        "adapter_config": {"max_records": 3000},
        "request": request,
        "max_records": 3000,
    }
    records = execute_provider_owned_adapter(connector=connector, fetch=fetch, trace_id="t")
    assert len(records) == 2
    expected = resolve_chip_row_session(TWSE_MARGIN, "", datetime.now(timezone.utc)).isoformat()
    assert {r.metadata["date"] for r in records} == {expected}


def test_provider_supplied_date_is_kept() -> None:
    records = _adapter(TPEX_INST).records_from_payload(
        "tw_institutional_flow", "TPEx", _inst_rows(2, "1151118"), fetch_time=FETCH_1850, trace_id="t"
    )
    assert {r.metadata["date"] for r in records} == {"2026-11-18"}


def test_undecidable_session_fails_run_naming_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.source_ingestion.chip_publication_schedule as sched

    def _boom(*a, **k):
        raise ValueError("no published chip session found")

    monkeypatch.setattr(sched, "latest_published_chip_session", _boom)
    with pytest.raises(SourceEvidenceError, match=TWSE_MARGIN):
        _adapter(TWSE_MARGIN).records_from_payload(
            "tw_margin_short_balance", "TWSE", _margin_rows(2), fetch_time=FETCH_1850, trace_id="t"
        )
    with pytest.raises(ChipSessionUndecidable, match="invalid row date"):
        resolve_chip_row_session(TPEX_INST, "not-a-date", FETCH_1850)


def test_over_budget_response_fails_instead_of_truncating() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(connector_id=TWSE_MARGIN, max_records=100)
    assert adapter.max_records == 3000  # a smaller caller budget is lifted to the profile budget
    with pytest.raises(SourceEvidenceError, match="refusing to truncate"):
        adapter.records_from_payload(
            "tw_margin_short_balance", "TWSE", _margin_rows(3001), fetch_time=FETCH_1850, trace_id="t"
        )
    # the run through the provider-owned path fails too (adapter raises before any generic slice)
    connector = _adapter(TWSE_MARGIN).connector()
    fetch = {
        "adapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
        "adapter_config": {}, "max_records": 3000,
        "request": {"dataset": "tw_margin_short_balance", "venues": ["TWSE"], "payload": _margin_rows(3001)},
    }
    with pytest.raises(SourceEvidenceError):
        execute_provider_owned_adapter(connector=connector, fetch=fetch, trace_id="t")


def test_other_official_connectors_keep_their_cap() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=5)
    records = adapter.records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(9), trade_date="2026-11-18", trace_id="t"
    )
    assert len(records) == 5


def test_coverage_full_partial_and_session_mismatch() -> None:
    full = evaluate_chip_row_coverage(
        TWSE_MARGIN, provider_listed_count=1310, stored_count=1310,
        stored_session="2026-11-18", expected_session=date(2026, 11, 18),
    )
    assert full["status"] == "complete" and not full["is_failure"] and full["missing_count"] == 0
    partial = evaluate_chip_row_coverage(
        TWSE_MARGIN, provider_listed_count=1310, stored_count=500,
        stored_session="2026-11-18", expected_session="2026-11-18",
    )
    assert partial["status"] == "partial" and partial["is_failure"] and partial["missing_count"] == 810
    mismatch = evaluate_chip_row_coverage(
        TWSE_MARGIN, provider_listed_count=1310, stored_count=1310,
        stored_session="2026-11-19", expected_session="2026-11-18",
    )
    assert mismatch["status"] == "session_mismatch" and mismatch["is_failure"]


def test_rollback_reader_reads_new_rows() -> None:
    """The new rows keep the previous release's row shape: same keys, same schema hash, string ISO date."""
    records = _adapter(TWSE_MARGIN).records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(1), fetch_time=FETCH_1850, trace_id="t"
    )
    meta = records[0].metadata
    assert meta["schema_hash"] == "tw_margin_short_balance.v1"
    assert {"date", "event_time", "available_time", "normalized_row", "symbol", "venue"} <= set(meta)
    assert date.fromisoformat(meta["date"]).isoformat() == meta["date"]
    assert datetime.fromisoformat(meta["available_time"].replace("Z", "+00:00")).tzinfo is not None


def test_records_carry_provider_listed_count_for_readback() -> None:
    records = _adapter(TWSE_MARGIN).records_from_payload(
        "tw_margin_short_balance", "TWSE", _margin_rows(7), fetch_time=FETCH_1850, trace_id="t"
    )
    assert {r.metadata["provider_listed_count"] for r in records} == {7}


def test_deploy_script_lifts_caps_and_wires_coverage_readback() -> None:
    from pathlib import Path

    script = (Path(__file__).parents[3] / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert "SOURCE_INGEST_MAX_RECORDS must be between 1 and 5000" in script
    assert "evaluate_chip_row_coverage" in script
    assert "tw-official-twse-margin-balances|tw-official-tpex-margin-balances|tw-official-tpex-institutional-flow)" in script
