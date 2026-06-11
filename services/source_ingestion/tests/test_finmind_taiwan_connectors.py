from __future__ import annotations

import http.client
import io
import json
import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FINMIND_BASE_URL,
    FINMIND_BROKER_REPORT_DATASET,
    FinMindCredentialError,
    FinMindFetchError,
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
    FinMindTaiwanDatasetAdapter,
    _extract_quota_meta,
    build_finmind_fetch_plan,
)


BROKER_DAILY_REPORT_PAYLOAD = {
    "msg": "success",
    "status": 200,
    "data": [
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "9200",
            "securities_trader": "國泰-敦南",
            "price": 950.0,
            "buy": 4000,
            "sell": 300,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "9200",
            "securities_trader": "國泰-敦南",
            "price": 951.0,
            "buy": 500,
            "sell": 100,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "1020",
            "securities_trader": "合庫-台中",
            "price": 950.0,
            "buy": 1400,
            "sell": 200,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "1440",
            "securities_trader": "美林",
            "price": 949.0,
            "buy": 100,
            "sell": 2100,
        },
    ],
}


def test_finmind_broker_daily_report_aggregates_to_top_buy_sell_rows() -> None:
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=2)
    rows = adapter.top_rows_from_payload(
        BROKER_DAILY_REPORT_PAYLOAD,
        source_url="https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report?data_id=2330&date=2026-06-08",
        available_time="2026-06-08T13:00:00Z",
    )

    assert len(rows) == 3
    assert rows[0]["side"] == "buy"
    assert rows[0]["rank"] == 1
    assert rows[0]["broker"] == "國泰-敦南"
    assert rows[0]["broker_id"] == "9200"
    assert rows[0]["buy_qty"] == 4500
    assert rows[0]["sell_qty"] == 400
    assert rows[0]["net_qty"] == 4100
    assert rows[2]["side"] == "sell"
    assert rows[2]["rank"] == 1
    assert rows[2]["net_qty"] == -2000


def test_finmind_broker_daily_report_adapter_emits_tw_broker_top_records() -> None:
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=2)
    connector = adapter.connector()
    records = adapter.records_from_daily_report_payload(
        BROKER_DAILY_REPORT_PAYLOAD,
        available_time="2026-06-08T13:00:00Z",
        trace_id="trace-finmind-broker",
    )

    assert connector.provider == "FinMind"
    assert connector.auth_policy.secret_ref.secret_ref_id == "env://FINMIND_API_TOKEN"
    assert connector.metadata["source_plan"] == "finmind_first_low_cost_paid_layer"
    assert connector.metadata["fallback_connector_id"] == "tw-yahoo-broker-top15"
    assert len(records) == 3
    assert records[0].connector_id == "tw-finmind-broker-daily-report"
    assert records[0].metadata["dataset"] == "tw_broker_top"
    assert records[0].metadata["source_dataset"] == FINMIND_BROKER_REPORT_DATASET
    assert records[0].metadata["schema_hash"] == "tw_broker_top.v1"


def test_finmind_generic_dataset_adapter_emits_research_records_without_raw_token() -> None:
    adapter = FinMindTaiwanDatasetAdapter(secret_ref_id="env://FINMIND_API_TOKEN")
    records = adapter.records_from_data_payload(
        "TaiwanStockInstitutionalInvestorsBuySell",
        {"data": [{"date": "2026-06-08", "stock_id": "2330", "buy": 1000, "sell": 250}]},
        trace_id="trace-finmind-dataset",
    )

    encoded = json.dumps(records[0].to_dict(), ensure_ascii=False)
    assert records[0].metadata["provider"] == "FinMind"
    assert records[0].metadata["dataset"] == "TaiwanStockInstitutionalInvestorsBuySell"
    assert "FINMIND_API_TOKEN" not in encoded


def test_finmind_sponsorpro_storage_object_records_redact_signed_url() -> None:
    adapter = FinMindTaiwanBrokerBulkBackfillAdapter()
    records = adapter.records_from_storage_objects_payload(
        {
            "data": [
                {
                    "object_name": "TaiwanStockTradingDailyReport/2026-06-08.parquet",
                    "url": "https://signed.example.test/download?token=raw-signed-token",
                    "expires_at": "2026-06-09T00:00:00Z",
                }
            ]
        },
        date="2026-06-08",
        trace_id="trace-finmind-storage",
    )

    encoded = json.dumps(records[0].to_dict(), ensure_ascii=False)
    assert records[0].metadata["signed_url_present"] is True
    assert records[0].metadata["signed_url_redacted"] is True
    assert records[0].metadata["raw_storage_partition"] == (
        "raw/finmind/TaiwanStockTradingDailyReport/date=2026-06-08/"
    )
    assert "raw-signed-token" not in encoded
    assert "https://signed.example.test" not in encoded


# ---------------------------------------------------------------------------
# FinMindLiveFetcher: credential resolution
# ---------------------------------------------------------------------------


def test_live_fetcher_credential_unavailable_when_no_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    fetcher = FinMindLiveFetcher()
    assert fetcher.resolve_token() is None
    status = fetcher.credential_status()
    assert status["available"] is False
    assert status["status"] == "credential_unavailable"
    assert "FINMIND_API_TOKEN" in status["note"]
    assert "token" not in str(status).lower().replace("token", "TOKEN")


def test_live_fetcher_credential_ok_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "test-api-token-value")
    fetcher = FinMindLiveFetcher()
    assert fetcher.resolve_token() == "test-api-token-value"
    status = fetcher.credential_status()
    assert status["available"] is True
    assert status["status"] == "credential_ok"
    # Token value must not appear in the status dict
    assert "test-api-token-value" not in json.dumps(status)


def test_live_fetcher_raises_credential_error_on_fetch_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    fetcher = FinMindLiveFetcher()
    with pytest.raises(FinMindCredentialError, match="FINMIND_API_TOKEN"):
        fetcher.fetch_dataset("TaiwanStockPrice", symbol="2330", start_date="2026-06-08")


def test_live_fetcher_raises_credential_error_on_broker_fetch_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    fetcher = FinMindLiveFetcher()
    with pytest.raises(FinMindCredentialError):
        fetcher.fetch_broker_report("2330", "2026-06-08")


# ---------------------------------------------------------------------------
# FinMindLiveFetcher: HTTP fetch with mocked responses
# ---------------------------------------------------------------------------


def _mock_http_response(body: dict, *, status: int = 200, headers: dict | None = None) -> MagicMock:
    """Build a mock urllib response context manager."""
    raw_bytes = json.dumps(body).encode("utf-8")
    msg = MagicMock(spec=http.client.HTTPMessage)
    header_items = list((headers or {}).items())
    msg.items.return_value = header_items

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = status
    mock_resp.headers = msg
    mock_resp.read = MagicMock(return_value=raw_bytes)
    mock_resp.geturl = MagicMock(return_value="https://api.finmindtrade.com/api/v4/data")
    return mock_resp


def test_live_fetcher_dataset_returns_payload_and_quota_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    price_body = {
        "msg": "success",
        "status": 200,
        "data": [{"date": "2026-06-08", "stock_id": "2330", "open": 950.0, "close": 955.0}],
    }
    rate_headers = {"X-RateLimit-Limit": "600", "X-RateLimit-Remaining": "597"}
    mock_resp = _mock_http_response(price_body, headers=rate_headers)

    fetcher = FinMindLiveFetcher()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        payload, quota_meta = fetcher.fetch_dataset(
            "TaiwanStockPrice", symbol="2330", start_date="2026-06-08", end_date="2026-06-08"
        )

    assert payload["status"] == 200
    assert len(payload["data"]) == 1
    assert quota_meta["ratelimit_limit"] == 600
    assert quota_meta["ratelimit_remaining"] == 597
    assert quota_meta["http_status"] == 200
    assert "api_status" in quota_meta
    # Token must not appear in quota_meta
    assert "valid-token" not in json.dumps(quota_meta)


def test_live_fetcher_redacts_token_from_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "secret-token-value")
    price_body = {"msg": "success", "status": 200, "data": []}
    mock_resp = _mock_http_response(price_body)

    captured_urls: list[str] = []

    def capture_urlopen(request: urllib.request.Request, *, timeout: float) -> MagicMock:
        captured_urls.append(request.full_url)
        return mock_resp

    fetcher = FinMindLiveFetcher()
    with patch("urllib.request.urlopen", side_effect=capture_urlopen):
        payload, quota_meta = fetcher.fetch_dataset("TaiwanStockPrice", symbol="2330")

    # The live URL carries the token, but the stored quota_meta URL must not
    assert any("secret-token-value" in u for u in captured_urls), "live URL should contain token"
    assert "secret-token-value" not in quota_meta["request_url"]
    assert "secret-token-value" not in json.dumps(quota_meta)


def test_live_fetcher_raises_credential_error_on_http_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "bad-token")
    fetcher = FinMindLiveFetcher()
    exc = urllib.error.HTTPError(
        url="https://api.finmindtrade.com/api/v4/data",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(FinMindCredentialError, match="HTTP 401"):
            fetcher.fetch_dataset("TaiwanStockPrice", symbol="2330")


def test_live_fetcher_raises_quota_error_on_http_402(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    fetcher = FinMindLiveFetcher()
    exc = urllib.error.HTTPError(
        url="https://api.finmindtrade.com/api/v4/data",
        code=402,
        msg="Payment Required",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(FinMindQuotaError, match="HTTP 402"):
            fetcher.fetch_dataset("TaiwanStockPrice", symbol="2330")


def test_live_fetcher_raises_fetch_error_on_app_level_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    error_body = {"msg": "Internal Server Error", "status": 500, "data": []}
    mock_resp = _mock_http_response(error_body)
    fetcher = FinMindLiveFetcher()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(FinMindFetchError, match="500"):
            fetcher.fetch_dataset("TaiwanStockPrice", symbol="2330")


def test_live_fetcher_broker_report_returns_payload_and_quota_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    broker_body = {"msg": "success", "status": 200, "data": [
        {"date": "2026-06-08", "stock_id": "2330", "securities_trader": "元大-南京",
         "securities_trader_id": "9A00", "buy": 2000, "sell": 100},
    ]}
    mock_resp = _mock_http_response(broker_body)
    fetcher = FinMindLiveFetcher()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        payload, quota_meta = fetcher.fetch_broker_report("2330", "2026-06-08")

    assert payload["status"] == 200
    assert quota_meta["http_status"] == 200
    assert "valid-token" not in json.dumps(quota_meta)


def test_live_fetcher_storage_objects_returns_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "valid-token")
    manifest_body = {"msg": "success", "status": 200, "data": [
        {"object_name": "TaiwanStockTradingDailyReport/2026-06-08.parquet",
         "url": "https://signed.storage.example/download?token=SHOULD-NOT-BE-STORED"},
    ]}
    mock_resp = _mock_http_response(manifest_body)
    fetcher = FinMindLiveFetcher()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        payload, quota_meta = fetcher.fetch_storage_objects(
            FINMIND_BROKER_REPORT_DATASET, "2026-06-08"
        )

    assert len(payload["data"]) == 1
    assert "valid-token" not in json.dumps(quota_meta)


# ---------------------------------------------------------------------------
# _extract_quota_meta
# ---------------------------------------------------------------------------


def test_extract_quota_meta_from_dict_headers() -> None:
    headers = {
        "X-RateLimit-Limit": "600",
        "X-RateLimit-Remaining": "590",
        "X-RateLimit-Reset": "1749340800",
        "Content-Type": "application/json",
    }
    meta = _extract_quota_meta(headers)
    assert meta["ratelimit_limit"] == 600
    assert meta["ratelimit_remaining"] == 590
    assert meta["ratelimit_reset"] == 1749340800
    assert "Content-Type" not in meta


def test_extract_quota_meta_returns_empty_dict_for_none() -> None:
    assert _extract_quota_meta(None) == {}


def test_extract_quota_meta_handles_retry_after() -> None:
    headers = {"retry-after": "30"}
    meta = _extract_quota_meta(headers)
    assert meta.get("retry_after_seconds") == 30


# ---------------------------------------------------------------------------
# build_finmind_fetch_plan: active-universe fanout
# ---------------------------------------------------------------------------


def test_fetch_plan_archive_gets_only_price_baseline() -> None:
    plan = build_finmind_fetch_plan(
        {"core_universe": ["2330"], "candidate_universe": ["6505"],
         "archive_universe": ["2002", "1234"]},
        "2026-06-08",
    )
    archive_specs = [s for s in plan if s["universe_tier"] == "archive_universe"]
    archive_datasets = {s["dataset"] for s in archive_specs}
    assert archive_datasets == {"TaiwanStockPrice"}, (
        "archive symbols must only receive TaiwanStockPrice baseline"
    )


def test_fetch_plan_core_and_candidate_get_chip_and_broker() -> None:
    plan = build_finmind_fetch_plan(
        {"core_universe": ["2330"], "candidate_universe": ["6505"]},
        "2026-06-08",
    )
    core_datasets = {s["dataset"] for s in plan if s["symbol"] == "2330"}
    candidate_datasets = {s["dataset"] for s in plan if s["symbol"] == "6505"}
    assert "TaiwanStockPrice" in core_datasets
    assert "TaiwanStockInstitutionalInvestorsBuySell" in core_datasets
    assert FINMIND_BROKER_REPORT_DATASET in core_datasets
    assert "TaiwanStockNews" in core_datasets
    assert "TaiwanStockPrice" in candidate_datasets
    assert FINMIND_BROKER_REPORT_DATASET in candidate_datasets


def test_fetch_plan_broker_detail_skipped_when_disabled() -> None:
    plan = build_finmind_fetch_plan(
        {"core_universe": ["2330", "2317"]},
        "2026-06-08",
        include_broker_detail=False,
    )
    assert all(s["dataset"] != FINMIND_BROKER_REPORT_DATASET for s in plan)


def test_fetch_plan_broker_specs_use_broker_report_fetcher() -> None:
    plan = build_finmind_fetch_plan({"core_universe": ["2330"]}, "2026-06-08")
    broker_specs = [s for s in plan if s["dataset"] == FINMIND_BROKER_REPORT_DATASET]
    assert all(s["fetcher"] == "broker_report" for s in broker_specs)


def test_fetch_plan_dataset_specs_use_dataset_fetcher() -> None:
    plan = build_finmind_fetch_plan({"core_universe": ["2330"]}, "2026-06-08")
    dataset_specs = [s for s in plan if s["dataset"] != FINMIND_BROKER_REPORT_DATASET]
    assert all(s["fetcher"] == "dataset" for s in dataset_specs)


def test_fetch_plan_symbols_are_uppercased() -> None:
    plan = build_finmind_fetch_plan(
        {"core_universe": ["2330"], "archive_universe": ["tsm"]},
        "2026-06-08",
    )
    assert all(s["symbol"] == s["symbol"].upper() for s in plan)


def test_fetch_plan_empty_tiers_produce_no_specs() -> None:
    plan = build_finmind_fetch_plan({}, "2026-06-08")
    assert plan == []

