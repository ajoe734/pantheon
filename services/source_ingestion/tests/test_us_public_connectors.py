from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import (
    FINRA_SHORT_SALE_CONNECTOR_ID,
    FRED_CONNECTOR_ID,
    SEC_EDGAR_CONNECTOR_ID,
    STOOQ_DAILY_OHLCV_CONNECTOR_ID,
    YAHOO_US_DAILY_OHLCV_CONNECTOR_ID,
    FinraShortSaleAdapter,
    FredMacroSeriesAdapter,
    SecEdgarFilingAdapter,
    SourceEvidenceError,
    StooqDailyOhlcvAdapter,
    YahooUsEquityDailyAdapter,
)


SEC_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

SEC_SUBMISSIONS = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000001"],
            "filingDate": ["2026-01-30"],
            "reportDate": ["2025-12-31"],
            "acceptanceDateTime": ["2026-01-30T18:01:02Z"],
            "form": ["10-Q"],
            "primaryDocument": ["aapl-20251231.htm"],
            "items": [""],
            "fileNumber": ["001-36743"],
            "filmNumber": ["26570001"],
        }
    },
}

SEC_COMPANYFACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "description": "Revenue from contracts with customers",
                "units": {
                    "USD": [
                        {
                            "fy": 2025,
                            "fp": "Q1",
                            "form": "10-Q",
                            "filed": "2026-01-30",
                            "end": "2025-12-31",
                            "val": 124300000000,
                        }
                    ]
                },
            }
        }
    },
}

FRED_CSV = """observation_date,GDP
2025-07-01,30623.1
2025-10-01,30999.2
"""

FINRA_SHORT_VOLUME = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260610|AAPL|123456|120|987654|Q
20260610|MSFT|222222|0|1000000|Q
"""

STOOQ_DAILY = """Date,Open,High,Low,Close,Volume
2026-06-09,201.0,203.5,200.0,202.1,52000000
2026-06-10,202.1,205.0,201.2,204.8,61000000
"""

YAHOO_CHART = {
    "chart": {
        "result": [
            {
                "meta": {
                    "symbol": "AAPL",
                    "currency": "USD",
                    "exchangeTimezoneName": "America/New_York",
                },
                "timestamp": [int(datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp())],
                "indicators": {
                    "quote": [
                        {
                            "open": [202.1],
                            "high": [205.0],
                            "low": [201.2],
                            "close": [204.8],
                            "volume": [61000000],
                        }
                    ],
                    "adjclose": [{"adjclose": [204.6]}],
                },
            }
        ],
        "error": None,
    }
}


def _completed_result(*, connector_id: str, normalized_count: int = 1):
    run = SimpleNamespace(
        ingest_run_id=f"ingest-{connector_id}",
        status=SimpleNamespace(value="completed"),
        finished_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
        normalized_count=normalized_count,
        rejected_count=0,
    )
    return SimpleNamespace(run=run, watermark=SimpleNamespace(value="2026-06-10"))


def test_sec_edgar_adapter_normalizes_ticker_mapping_filings_and_company_facts() -> None:
    adapter = SecEdgarFilingAdapter(user_agent="Pantheon source-ingest ops@example.com", max_records=10)
    connector = adapter.connector()
    mapping = adapter.ticker_mapping_from_payload(SEC_TICKERS)
    filings = adapter.records_from_submissions_payload(SEC_SUBMISSIONS, symbol="AAPL", trace_id="trace-sec")
    facts = adapter.records_from_companyfacts_payload(SEC_COMPANYFACTS, symbol="AAPL", trace_id="trace-sec-facts")

    assert connector.connector_id == SEC_EDGAR_CONNECTOR_ID
    assert connector.metadata["requires_configured_user_agent"] is True
    assert mapping["AAPL"]["cik"] == "0000320193"
    assert filings[0].metadata["dataset"] == "sec_filing_event"
    assert filings[0].metadata["normalized_row"]["form_type"] == "10-Q"
    assert filings[0].metadata["normalized_row"]["primary_document_url"].endswith("/aapl-20251231.htm")
    assert facts[0].metadata["dataset"] == "sec_company_fact"
    assert facts[0].metadata["normalized_row"]["concept"] == "Revenues"
    assert facts[0].metadata["normalized_row"]["value"] == 124300000000


def test_sec_edgar_fetch_requires_configured_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    with pytest.raises(SourceEvidenceError, match="SEC_EDGAR_USER_AGENT"):
        SecEdgarFilingAdapter().configured_user_agent()
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Pantheon source-ingest ops@example.com")
    assert "ops@example.com" in SecEdgarFilingAdapter().configured_user_agent()


def test_fred_adapter_supports_public_csv_fallback_and_per_series_watermark() -> None:
    adapter = FredMacroSeriesAdapter(max_records=5)
    connector = adapter.connector()
    records = adapter.records_from_csv("GDP", FRED_CSV, trace_id="trace-fred")

    assert connector.connector_id == FRED_CONNECTOR_ID
    assert connector.auth_policy.secret_ref.secret_ref_id == "env://FRED_API_KEY"
    assert connector.metadata["required_secret_ref_id"] == "env://FRED_API_KEY"
    assert connector.metadata["symbol_scope"] == "global_no_symbol_filter"
    assert len(records) == 2
    assert records[0].metadata["dataset"] == "macro_fred_observation"
    assert records[0].metadata["fetch_mode"] == "public_csv_fallback"
    assert records[0].metadata["latest_watermark_key"] == "fred:GDP"
    assert records[0].metadata["staleness_seconds"] == 15552000


def test_fred_live_fetch_requires_keyed_api(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FredMacroSeriesAdapter(max_records=5)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(SourceEvidenceError, match="FRED_API_KEY"):
        adapter.fetch_api_observations("GDP")
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    captured = {}

    def fake_request(url: str, *, user_agent: str, timeout_seconds: float = 20.0):
        captured["url"] = url
        return {"observations": [{"date": "2026-01-01", "value": "1.2"}]}

    monkeypatch.setattr("services.source_ingestion.connectors.us_public._request_json", fake_request)
    payload = adapter.fetch_api_observations("GDP")

    assert payload["observations"][0]["value"] == "1.2"
    assert "api_key=test-fred-key" in captured["url"]


def test_finra_short_sale_adapter_normalizes_daily_file_and_publication_window() -> None:
    adapter = FinraShortSaleAdapter(max_records=10)
    records = adapter.records_from_short_volume_text(FINRA_SHORT_VOLUME, trace_id="trace-finra")

    assert records[0].connector_id == FINRA_SHORT_SALE_CONNECTOR_ID
    assert records[0].metadata["dataset"] == "us_short_volume_daily"
    assert records[0].metadata["normalized_row"]["symbol_canonical"] == "AAPL.US"
    assert records[0].metadata["normalized_row"]["short_volume"] == 123456
    assert records[0].metadata["normalized_row"]["short_volume_ratio"] == pytest.approx(123456 / 987654)

    pending = adapter.missing_file_health(
        "2026-06-10",
        now=datetime(2026, 6, 10, 20, tzinfo=timezone.utc),
    )
    late = adapter.missing_file_health(
        "2026-06-10",
        now=datetime(2026, 6, 12, 4, tzinfo=timezone.utc),
    )
    assert pending.status == "ok"
    assert pending.metadata["missing_file_reason"] == "expected_publication_pending"
    assert late.status == "degraded"
    assert late.metadata["missing_file_reason"] == "missing_after_publication_window"


def test_stooq_daily_ohlcv_adapter_is_disabled_until_runtime_endpoint_verified() -> None:
    adapter = StooqDailyOhlcvAdapter(max_records=5)
    connector = adapter.connector()
    records = adapter.records_from_csv("AAPL", STOOQ_DAILY, trace_id="trace-stooq")
    health = adapter.disabled_source_health()

    assert connector.connector_id == STOOQ_DAILY_OHLCV_CONNECTOR_ID
    assert connector.status.value == "disabled"
    assert connector.metadata["disabled_reason"] == "stooq_endpoint_unverified_2026-06-11"
    assert records[0].metadata["dataset"] == "us_price_daily"
    assert records[0].metadata["normalized_row"]["close"] == 202.1
    assert health.status == "disabled"
    assert health.metadata["disabled_reason"] == "stooq_endpoint_unverified_2026-06-11"


def test_yahoo_us_daily_ohlcv_adapter_normalizes_chart_payload() -> None:
    adapter = YahooUsEquityDailyAdapter(max_records=5, default_symbols=("AAPL",))
    connector = adapter.connector()
    records = adapter.records_from_chart_payload("AAPL", YAHOO_CHART, trace_id="trace-yahoo")

    assert connector.connector_id == YAHOO_US_DAILY_OHLCV_CONNECTOR_ID
    assert connector.metadata["replaces_connector_id"] == STOOQ_DAILY_OHLCV_CONNECTOR_ID
    assert records[0].metadata["dataset"] == "us_price_daily"
    assert records[0].metadata["normalized_row"]["provider"] == "Yahoo Finance"
    assert records[0].metadata["normalized_row"]["close"] == 204.8
    assert records[0].metadata["normalized_row"]["adjusted_close"] == 204.6


def test_provider_owned_dispatch_allowlists_us_public_adapters(tmp_path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    fred = FredMacroSeriesAdapter(max_records=5)
    store.upsert_config(
        fred.connector(),
        {
            "mode": "provider_owned_adapter",
            "adapter": "FredMacroSeriesAdapter.records_from_observations_payload",
            "adapter_config": {"max_records": 5},
            "request": {
                "series_id": "GDP",
                "csv_text": FRED_CSV,
                "dataset": "macro_fred_observation",
                "run_date": "2026-06-10",
            },
            "max_records": 5,
            "next_watermark": "fred:GDP:2025-10-01",
        },
    )

    batch = ConfiguredConnectorFetcher(store).fetch_batch(FRED_CONNECTOR_ID, None, trace_id="trace-dispatch")

    assert len(batch.records) == 2
    assert batch.next_watermark == "fred:GDP:2025-10-01"
    assert batch.records[0].metadata["provider_owned_adapter"] == (
        "FredMacroSeriesAdapter.records_from_observations_payload"
    )


def test_provider_owned_dispatch_fetches_yahoo_when_payload_absent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    adapter = YahooUsEquityDailyAdapter(max_records=5, default_symbols=("AAPL",))
    store.upsert_config(adapter.connector(), adapter.fetch_config())

    def fake_fetch_chart(self, symbol: str, *, range_value: str | None = None, interval: str | None = None):
        assert symbol == "AAPL"
        assert range_value == "1mo"
        assert interval == "1d"
        return YAHOO_CHART

    monkeypatch.setattr(YahooUsEquityDailyAdapter, "fetch_chart", fake_fetch_chart)
    batch = ConfiguredConnectorFetcher(store).fetch_batch(
        YAHOO_US_DAILY_OHLCV_CONNECTOR_ID,
        None,
        trace_id="trace-yahoo-live-driver",
    )

    assert len(batch.records) == 1
    assert batch.records[0].metadata["provider_owned_adapter"] == (
        "YahooUsEquityDailyAdapter.records_from_chart_payload"
    )
    assert batch.records[0].metadata["normalized_row"]["symbol"] == "AAPL"


def test_provider_owned_dispatch_fetches_sec_when_payload_absent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Pantheon source-ingest ops@example.com")
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    adapter = SecEdgarFilingAdapter(max_records=5)
    fetch = dict(adapter.fetch_config())
    fetch["request"] = {"dataset": "sec_filing_event", "symbols": ["AAPL"]}
    store.upsert_config(adapter.connector(), fetch)
    monkeypatch.setattr(SecEdgarFilingAdapter, "fetch_company_tickers", lambda self: SEC_TICKERS)
    monkeypatch.setattr(SecEdgarFilingAdapter, "fetch_submissions", lambda self, cik: SEC_SUBMISSIONS)

    batch = ConfiguredConnectorFetcher(store).fetch_batch(
        SEC_EDGAR_CONNECTOR_ID,
        None,
        trace_id="trace-sec-live-driver",
    )

    assert len(batch.records) == 1
    assert batch.records[0].metadata["dataset"] == "sec_filing_event"
    assert batch.records[0].metadata["provider_owned_adapter"] == "SecEdgarFilingAdapter.records_from_payload"


def test_provider_owned_dispatch_fetches_finra_when_payload_absent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    adapter = FinraShortSaleAdapter(max_records=5)
    store.upsert_config(adapter.connector(), adapter.fetch_config())
    monkeypatch.setattr(
        FinraShortSaleAdapter,
        "fetch_short_volume_text",
        lambda self, trade_date: FINRA_SHORT_VOLUME,
    )

    batch = ConfiguredConnectorFetcher(store).fetch_batch(
        FINRA_SHORT_SALE_CONNECTOR_ID,
        None,
        trace_id="trace-finra-live-driver",
    )

    assert len(batch.records) == 2
    assert batch.records[0].metadata["dataset"] == "us_short_volume_daily"
    assert batch.records[0].metadata["provider_owned_adapter"] == (
        "FinraShortSaleAdapter.records_from_short_volume_text"
    )


def test_provider_owned_dispatch_fetches_fred_keyed_api_when_payload_absent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    adapter = FredMacroSeriesAdapter(max_records=5)
    fetch = dict(adapter.fetch_config())
    fetch["request"] = {"series_ids": ["GDP"], "fetch_mode": "keyed_api"}
    store.upsert_config(adapter.connector(), fetch)
    monkeypatch.setattr(
        FredMacroSeriesAdapter,
        "fetch_api_observations",
        lambda self, series_id: {"observations": [{"date": "2026-01-01", "value": "1.2"}]},
    )

    batch = ConfiguredConnectorFetcher(store).fetch_batch(
        FRED_CONNECTOR_ID,
        None,
        trace_id="trace-fred-live-driver",
    )

    assert len(batch.records) == 1
    assert batch.records[0].metadata["fetch_mode"] == "keyed_api"
    assert batch.records[0].metadata["provider_owned_adapter"] == (
        "FredMacroSeriesAdapter.records_from_observations_payload"
    )


def test_us_public_adapters_build_source_health_snapshots() -> None:
    assert SecEdgarFilingAdapter().source_health_from_result(
        _completed_result(connector_id=SEC_EDGAR_CONNECTOR_ID)
    ).status == "ok"
    assert FredMacroSeriesAdapter().source_health_from_result(
        _completed_result(connector_id=FRED_CONNECTOR_ID),
        series_id="GDP",
    ).metadata["latest_watermark_key"] == "fred:GDP"
    assert FinraShortSaleAdapter().source_health_from_result(
        _completed_result(connector_id=FINRA_SHORT_SALE_CONNECTOR_ID)
    ).schema_hash == "us_short_volume_daily.v1"
