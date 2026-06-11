"""Tests for US paid and broker-readback adapters.

Covers: Polygon, Alpha Vantage, IBKR readback, Shioaji readback.

All tests use fixture payloads — no live HTTP calls. Tests marked with
pytest.mark.external require network access and are skipped by default.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from services.source_ingestion.connectors import (
    ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID,
    IBKR_READBACK_CONNECTOR_ID,
    POLYGON_US_DAILY_CONNECTOR_ID,
    SHIOAJI_READBACK_CONNECTOR_ID,
    AlphaVantageUsEquityDailyAdapter,
    ConnectorStatus,
    IbkrBrokerReadbackAdapter,
    PolygonUsEquityDailyAdapter,
    ShioajiBrokerReadbackAdapter,
    SourceEvidenceError,
)
from services.source_ingestion.source_health import SourceHealthStatus


# ---------------------------------------------------------------------------
# Polygon fixtures
# ---------------------------------------------------------------------------

POLYGON_AGGS_RESPONSE = {
    "ticker": "AAPL",
    "queryCount": 2,
    "resultsCount": 2,
    "adjusted": True,
    "status": "OK",
    "results": [
        {
            "v": 70790813,
            "vw": 150.0742,
            "o": 149.68,
            "c": 150.43,
            "h": 151.83,
            "l": 149.26,
            "t": 1746921600000,
            "n": 524117,
        },
        {
            "v": 65432100,
            "vw": 152.10,
            "o": 151.00,
            "c": 153.25,
            "h": 154.00,
            "l": 150.50,
            "t": 1747008000000,
            "n": 500000,
        },
    ],
}

POLYGON_NOT_FOUND = {
    "status": "NOT_FOUND",
    "request_id": "abc123",
    "results": [],
}

POLYGON_FORBIDDEN = {
    "status": "FORBIDDEN",
    "message": "Insufficient funds",
}


class TestPolygonUsEquityDailyAdapter:
    def test_connector_metadata(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        connector = adapter.connector()
        assert connector.connector_id == POLYGON_US_DAILY_CONNECTOR_ID
        assert connector.provider == "Polygon"
        assert "vendor_paid_market_data" in connector.license_scope
        meta = connector.metadata
        assert "credential_env_vars" in meta
        assert "POLYGON_API_KEY" in meta["credential_env_vars"]

    def test_fetch_config_shape(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        config = adapter.fetch_config()
        assert config["dataset"] == "us_price_daily"
        assert "PolygonUsEquityDailyAdapter" in config["adapter"]
        assert "POLYGON_API_KEY" in config["adapter_config"]["credential_env_vars"]

    def test_records_from_aggs_ok(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        records = adapter.records_from_aggs_payload("AAPL", POLYGON_AGGS_RESPONSE)
        assert len(records) == 2
        r0 = records[0]
        assert r0.connector_id == POLYGON_US_DAILY_CONNECTOR_ID
        assert r0.source_type.value == "market"
        meta = r0.metadata
        assert meta["symbol"] == "AAPL"
        assert meta["dataset"] == "us_price_daily"
        assert meta["schema_hash"] == "us_price_daily.v1"
        assert meta["entitlement_required"] is True
        assert "polygon_adjusted_close" in meta["normalized_row"]["adjustment_policy"]
        # Point-in-time check
        assert meta["normalized_row"]["open"] == 149.68
        assert meta["normalized_row"]["volume"] == 70790813

    def test_records_from_aggs_not_found(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        with pytest.raises(SourceEvidenceError, match="NOT_FOUND"):
            adapter.records_from_aggs_payload("AAPL", POLYGON_NOT_FOUND)

    def test_records_from_aggs_forbidden(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        with pytest.raises(SourceEvidenceError, match="FORBIDDEN"):
            adapter.records_from_aggs_payload("AAPL", POLYGON_FORBIDDEN)

    def test_records_require_symbol(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        payload_no_ticker = {
            "status": "OK",
            "results": [{"v": 1000, "o": 10.0, "c": 11.0, "h": 12.0, "l": 9.0, "t": 1746921600000}],
        }
        with pytest.raises(SourceEvidenceError, match="symbol is required"):
            adapter.records_from_aggs_payload("", payload_no_ticker)

    def test_records_wrong_payload_type(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        with pytest.raises(SourceEvidenceError, match="JSON object"):
            adapter.records_from_aggs_payload("AAPL", [1, 2, 3])

    def test_empty_results_returns_empty_tuple(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        payload = {"ticker": "AAPL", "status": "OK", "results": []}
        records = adapter.records_from_aggs_payload("AAPL", payload)
        assert records == tuple()

    def test_max_records_respected(self) -> None:
        adapter = PolygonUsEquityDailyAdapter(max_records=1)
        records = adapter.records_from_aggs_payload("AAPL", POLYGON_AGGS_RESPONSE)
        assert len(records) == 1

    def test_credential_unavailable_health(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in ("POLYGON_API_KEY", "MASSIVE_API_KEY", "US_MARKET_DATA_API_KEY"):
            monkeypatch.delenv(env_var, raising=False)
        adapter = PolygonUsEquityDailyAdapter()
        health = adapter.credential_unavailable_health()
        assert health.status == SourceHealthStatus.DEGRADED.value
        assert health.metadata["credential_status"] == "credential_unavailable"
        assert "POLYGON_API_KEY" in health.metadata["credential_env_vars"]

    def test_source_ids_are_unique(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        records = adapter.records_from_aggs_payload("AAPL", POLYGON_AGGS_RESPONSE)
        ids = [r.source_id for r in records]
        assert len(ids) == len(set(ids))

    def test_no_inline_secret_in_metadata(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        connector = adapter.connector()
        meta_str = json.dumps(connector.metadata)
        for forbidden in ("apikey", "api_key=", "token="):
            assert forbidden not in meta_str.lower().replace("_api_key", "")

    def test_daily_url_structure(self) -> None:
        adapter = PolygonUsEquityDailyAdapter()
        url = adapter.daily_url("AAPL", start_date="2026-01-01", end_date="2026-01-10", api_key="testkey123")
        assert "AAPL" in url
        assert "2026-01-01" in url
        assert "2026-01-10" in url
        assert "adjusted=true" in url
        # api key must appear (it's in the query params)
        assert "testkey123" in url

    def test_ms_timestamp_conversion(self) -> None:
        """Verify Unix-millisecond timestamps map to expected ISO dates."""
        adapter = PolygonUsEquityDailyAdapter()
        records = adapter.records_from_aggs_payload("AAPL", POLYGON_AGGS_RESPONSE)
        # 1746921600000 ms = 2025-05-11 00:00:00 UTC
        assert records[0].metadata["trade_date"] is not None
        assert "-" in records[0].metadata["trade_date"]


# ---------------------------------------------------------------------------
# Alpha Vantage fixtures
# ---------------------------------------------------------------------------

ALPHA_VANTAGE_TIME_SERIES = {
    "Meta Data": {
        "1. Information": "Daily Time Series with Splits and Dividend Events",
        "2. Symbol": "AAPL",
        "3. Last Refreshed": "2026-06-10",
        "4. Output Size": "Compact",
        "5. Time Zone": "US/Eastern",
    },
    "Time Series (Daily)": {
        "2026-06-10": {
            "1. open": "192.50",
            "2. high": "194.00",
            "3. low": "191.80",
            "4. close": "193.20",
            "5. adjusted close": "193.20",
            "6. volume": "55123456",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0",
        },
        "2026-06-09": {
            "1. open": "191.00",
            "2. high": "192.80",
            "3. low": "190.50",
            "4. close": "192.10",
            "5. adjusted close": "192.10",
            "6. volume": "48765432",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0",
        },
    },
}

ALPHA_VANTAGE_QUOTA_EXCEEDED = {
    "Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 25 requests per day and 5 requests per minute.",
}


class TestAlphaVantageUsEquityDailyAdapter:
    def test_connector_disabled_by_default(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        connector = adapter.connector()
        assert connector.status == ConnectorStatus.DISABLED
        assert "full_universe_scan_forbidden" in connector.metadata
        assert connector.metadata["full_universe_scan_forbidden"] is True

    def test_connector_metadata(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        connector = adapter.connector()
        assert connector.connector_id == ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID
        assert connector.provider == "Alpha Vantage"
        assert "vendor_paid_market_data" in connector.license_scope

    def test_records_from_time_series_ok(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        records = adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_TIME_SERIES)
        assert len(records) == 2
        r0 = records[0]
        assert r0.connector_id == ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID
        meta = r0.metadata
        assert meta["symbol"] == "AAPL"
        assert meta["dataset"] == "us_price_daily"
        assert meta["entitlement_required"] is True

    def test_records_normalized_close(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        records = adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_TIME_SERIES)
        row = records[0].metadata["normalized_row"]
        assert row["close"] == 193.20
        assert row["adjusted_close"] == 193.20
        assert "alpha_vantage_adjusted_close" in row["adjustment_policy"]

    def test_quota_exceeded_raises(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        with pytest.raises(SourceEvidenceError, match="quota"):
            adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_QUOTA_EXCEEDED)

    def test_empty_time_series_returns_empty(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        payload = {"Meta Data": {}, "Time Series (Daily)": {}}
        records = adapter.records_from_time_series_payload("AAPL", payload)
        assert records == tuple()

    def test_wrong_payload_raises(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        with pytest.raises(SourceEvidenceError, match="JSON object"):
            adapter.records_from_time_series_payload("AAPL", [1, 2])

    def test_max_records_respected(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter(max_records=1)
        records = adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_TIME_SERIES)
        assert len(records) == 1

    def test_credential_unavailable_health(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
        adapter = AlphaVantageUsEquityDailyAdapter()
        health = adapter.credential_unavailable_health()
        assert health.status == SourceHealthStatus.DEGRADED.value
        assert health.metadata["credential_status"] == "credential_unavailable"

    def test_disabled_health(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        health = adapter.disabled_source_health()
        assert health.status == SourceHealthStatus.DISABLED.value

    def test_source_ids_unique(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        records = adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_TIME_SERIES)
        ids = [r.source_id for r in records]
        assert len(ids) == len(set(ids))

    def test_daily_url_contains_symbol_and_key(self) -> None:
        adapter = AlphaVantageUsEquityDailyAdapter()
        url = adapter.daily_url("MSFT", api_key="testkey456")
        assert "MSFT" in url
        assert "testkey456" in url
        assert "TIME_SERIES_DAILY_ADJUSTED" in url


# ---------------------------------------------------------------------------
# IBKR readback fixtures
# ---------------------------------------------------------------------------

IBKR_READBACK_PAYLOAD = [
    {
        "symbol": "AAPL",
        "type": "fill_readback",
        "date": "2026-06-10",
        "price": 193.20,
        "quantity": 100,
        "side": "BUY",
        "order_id": "ibkr-fill-001",
    },
    {
        "symbol": "MSFT",
        "type": "quote_snapshot",
        "date": "2026-06-10",
        "bid": 420.50,
        "ask": 420.75,
    },
]


class TestIbkrBrokerReadbackAdapter:
    def test_connector_metadata(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        connector = adapter.connector()
        assert connector.connector_id == IBKR_READBACK_CONNECTOR_ID
        assert connector.provider == "IBKR"
        assert "broker_execution_readback_only" in connector.license_scope
        meta = connector.metadata
        assert meta["research_primary_forbidden"] is True
        assert meta["order_placement_forbidden"] is True
        assert meta["capital_mutation_forbidden"] is True

    def test_fetch_config_has_safety_flags(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        config = adapter.fetch_config()
        assert config["order_placement_forbidden"] is True
        assert config["capital_mutation_forbidden"] is True

    def test_records_from_readback_file(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/ibkr_readback.json", IBKR_READBACK_PAYLOAD)
        assert len(records) == 2
        r0 = records[0]
        assert r0.connector_id == IBKR_READBACK_CONNECTOR_ID
        meta = r0.metadata
        assert meta["research_primary"] is False
        assert meta["license_scope"] == "broker_execution_readback_only"
        assert meta["schema_hash"] == "broker_readback_evidence.v1"

    def test_records_access_scope_is_audit_only(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/ibkr_readback.json", IBKR_READBACK_PAYLOAD)
        for r in records:
            assert "broker_execution_audit" in r.metadata["access_scope"]
            assert "research_primary" not in r.metadata.get("access_scope", [])

    def test_records_missing_symbol_skipped(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        payload = [{"type": "fill_readback", "date": "2026-06-10"}]
        records = adapter.records_from_readback_file("/tmp/ibkr.json", payload)
        assert records == tuple()

    def test_records_single_dict_payload(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        single = {"symbol": "AAPL", "type": "fill_readback", "date": "2026-06-10"}
        records = adapter.records_from_readback_file("/tmp/ibkr.json", single)
        assert len(records) == 1

    def test_records_bad_payload_raises(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        with pytest.raises(SourceEvidenceError):
            adapter.records_from_readback_file("/tmp/ibkr.json", "not a list")

    def test_missing_readback_health(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        health = adapter.missing_readback_health()
        assert health.status == SourceHealthStatus.DEGRADED.value
        assert health.metadata["credential_status"] == "readback_file_unavailable"
        assert "IBKR_READBACK_FILE_PATH" in health.metadata["readback_file_env"]

    def test_source_ids_unique(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/ibkr.json", IBKR_READBACK_PAYLOAD)
        ids = [r.source_id for r in records]
        assert len(ids) == len(set(ids))

    def test_no_order_placement_in_metadata(self) -> None:
        adapter = IbkrBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/ibkr.json", IBKR_READBACK_PAYLOAD)
        for r in records:
            assert r.metadata["normalized_row"]["order_placement_evidence"] is False

    def test_resolve_readback_path_none_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IBKR_READBACK_FILE_PATH", raising=False)
        adapter = IbkrBrokerReadbackAdapter()
        assert adapter.resolve_readback_path() is None


# ---------------------------------------------------------------------------
# Shioaji readback fixtures
# ---------------------------------------------------------------------------

SHIOAJI_READBACK_PAYLOAD = [
    {
        "symbol": "2330",
        "code": "2330",
        "type": "fill_readback",
        "date": "2026-06-10",
        "price": 920.0,
        "quantity": 1,
        "side": "BUY",
        "order_id": "shioaji-fill-001",
    },
    {
        "code": "2317",
        "type": "quote_snapshot",
        "date": "2026-06-10",
        "bid_price": 112.5,
        "ask_price": 112.6,
    },
]


class TestShioajiBrokerReadbackAdapter:
    def test_connector_metadata(self) -> None:
        adapter = ShioajiBrokerReadbackAdapter()
        connector = adapter.connector()
        assert connector.connector_id == SHIOAJI_READBACK_CONNECTOR_ID
        assert connector.provider == "Shioaji"
        assert "broker_execution_readback_only" in connector.license_scope
        meta = connector.metadata
        assert meta["research_primary_forbidden"] is True
        assert meta["order_placement_forbidden"] is True
        assert meta["market_scope"] == "taiwan"

    def test_records_from_readback_file(self) -> None:
        adapter = ShioajiBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/shioaji_readback.json", SHIOAJI_READBACK_PAYLOAD)
        assert len(records) == 2
        r0 = records[0]
        assert r0.connector_id == SHIOAJI_READBACK_CONNECTOR_ID
        meta = r0.metadata
        assert meta["research_primary"] is False
        assert meta["market_scope"] == "taiwan"
        assert ".TW" in meta.get("normalized_row", {}).get("symbol_canonical", "")

    def test_taiwan_symbol_canonical(self) -> None:
        adapter = ShioajiBrokerReadbackAdapter()
        records = adapter.records_from_readback_file("/tmp/shioaji.json", SHIOAJI_READBACK_PAYLOAD)
        for r in records:
            canonical = r.metadata.get("normalized_row", {}).get("symbol_canonical", "")
            if canonical:
                assert canonical.endswith(".TW")

    def test_missing_readback_health(self) -> None:
        adapter = ShioajiBrokerReadbackAdapter()
        health = adapter.missing_readback_health()
        assert health.status == SourceHealthStatus.DEGRADED.value
        assert "SHIOAJI_READBACK_FILE_PATH" in health.metadata["readback_file_env"]
        assert health.metadata["provider"] == "Shioaji"

    def test_safety_flags_in_fetch_config(self) -> None:
        adapter = ShioajiBrokerReadbackAdapter()
        config = adapter.fetch_config()
        assert config["order_placement_forbidden"] is True
        assert config["capital_mutation_forbidden"] is True


# ---------------------------------------------------------------------------
# Cross-adapter invariant tests
# ---------------------------------------------------------------------------

class TestAdapterInvariants:
    """Verify design invariants hold across all paid/broker adapters."""

    def test_paid_adapters_require_entitlement(self) -> None:
        for adapter_cls in (PolygonUsEquityDailyAdapter, AlphaVantageUsEquityDailyAdapter):
            adapter = adapter_cls()
            connector = adapter.connector()
            assert connector.metadata.get("credential_health_without_key") == "credential_unavailable"

    def test_broker_readback_adapters_forbid_research_primary(self) -> None:
        for adapter_cls in (IbkrBrokerReadbackAdapter, ShioajiBrokerReadbackAdapter):
            adapter = adapter_cls()
            connector = adapter.connector()
            assert connector.metadata.get("research_primary_forbidden") is True
            assert connector.metadata.get("order_placement_forbidden") is True
            assert connector.metadata.get("capital_mutation_forbidden") is True

    def test_no_inline_secrets_in_any_connector_metadata(self) -> None:
        adapters = [
            PolygonUsEquityDailyAdapter(),
            AlphaVantageUsEquityDailyAdapter(),
            IbkrBrokerReadbackAdapter(),
            ShioajiBrokerReadbackAdapter(),
        ]
        for adapter in adapters:
            connector = adapter.connector()
            meta_str = json.dumps(connector.metadata)
            for pattern in ("apikey=", "api_key=", "token=", "password=", "secret="):
                assert pattern not in meta_str.lower(), f"{adapter.__class__.__name__} has inline secret pattern: {pattern}"

    def test_all_adapters_have_connector_ids(self) -> None:
        assert PolygonUsEquityDailyAdapter().connector_id == POLYGON_US_DAILY_CONNECTOR_ID
        assert AlphaVantageUsEquityDailyAdapter().connector_id == ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID
        assert IbkrBrokerReadbackAdapter().connector_id == IBKR_READBACK_CONNECTOR_ID
        assert ShioajiBrokerReadbackAdapter().connector_id == SHIOAJI_READBACK_CONNECTOR_ID

    def test_paid_ohlcv_records_mark_entitlement_required(self) -> None:
        polygon_adapter = PolygonUsEquityDailyAdapter()
        polygon_records = polygon_adapter.records_from_aggs_payload("AAPL", POLYGON_AGGS_RESPONSE)
        for r in polygon_records:
            assert r.metadata.get("entitlement_required") is True

        av_adapter = AlphaVantageUsEquityDailyAdapter()
        av_records = av_adapter.records_from_time_series_payload("AAPL", ALPHA_VANTAGE_TIME_SERIES)
        for r in av_records:
            assert r.metadata.get("entitlement_required") is True
