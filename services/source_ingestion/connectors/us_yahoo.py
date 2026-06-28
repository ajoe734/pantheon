"""Yahoo Finance chart API adapter for US daily OHLCV."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
)
from ..source_health import SourceHealth, SourceHealthStatus


YAHOO_US_DAILY_OHLCV_CONNECTOR_ID = "us-yahoo-daily-ohlcv"
YAHOO_US_DAILY_OHLCV_SCHEMA_HASH = "us_price_daily.v1"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_DEFAULT_SYMBOLS: tuple[str, ...] = ("SPY", "AAPL", "MSFT")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    value = _float_or_none(value)
    return None if value is None else int(value)


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _timestamp_to_trade_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _request_json(
    url: str,
    *,
    user_agent: str = "pantheon-source-ingest/0.1",
    timeout_seconds: float = 20.0,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise SourceEvidenceError("Yahoo chart response must be a JSON object")
    return payload


def _finished_at(result: Any) -> str | None:
    run = getattr(result, "run", None)
    finished_at = getattr(run, "finished_at", None)
    if hasattr(finished_at, "isoformat"):
        return finished_at.isoformat().replace("+00:00", "Z")
    return finished_at


@dataclass(frozen=True)
class YahooUsEquityDailyAdapter(SourceConnectorProvider):
    """Keyless Yahoo chart API daily OHLCV adapter for US research reads."""

    connector_id: str = YAHOO_US_DAILY_OHLCV_CONNECTOR_ID
    max_records: int = 100
    default_symbols: Sequence[str] = field(default_factory=lambda: YAHOO_DEFAULT_SYMBOLS)
    range_value: str = "1mo"
    interval: str = "1d"
    timeout_seconds: float = 20.0
    user_agent: str = "pantheon-source-ingest/0.1"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Yahoo Finance",
            license_scope="public_market_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_market_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/yahoo-finance-chart-public-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/yahoo-finance-chart-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Yahoo Finance US daily OHLCV",
                homepage_url="https://finance.yahoo.com/",
                docs_url="https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=5d",
                owner="Yahoo Finance",
                tags=("us", "yahoo", "ohlcv", "market_data", "public_fallback"),
            ),
            metadata={
                "source_class": "public_market_reference",
                "normalized_datasets": ["us_price_daily"],
                "schema_hash": YAHOO_US_DAILY_OHLCV_SCHEMA_HASH,
                "source_priority": "keyless_public_us_daily_ohlcv_after_stooq_blocked",
                "replaces_connector_id": "us-stooq-daily-ohlcv",
                "endpoint": YAHOO_CHART_URL,
                "default_symbols": [_symbol(symbol) for symbol in self.default_symbols],
                "corporate_action_adjustment_policy": "yahoo_adjclose_captured_when_present; close remains provider close",
                "feature_targets": ["features/us_returns"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "YahooUsEquityDailyAdapter.records_from_chart_payload",
            "adapter_config": {
                "max_records": self.max_records,
                "default_symbols": [_symbol(symbol) for symbol in self.default_symbols],
                "range_value": self.range_value,
                "interval": self.interval,
                "timeout_seconds": self.timeout_seconds,
                "user_agent": self.user_agent,
            },
            "request": {
                "symbols": [_symbol(symbol) for symbol in self.default_symbols],
                "range": self.range_value,
                "interval": self.interval,
            },
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "us_price_daily",
        }

    def daily_url(
        self,
        symbol: str,
        *,
        range_value: str | None = None,
        interval: str | None = None,
    ) -> str:
        symbol_value = _symbol(symbol)
        if not symbol_value:
            raise SourceEvidenceError("symbol is required for Yahoo chart fetch")
        params = {
            "interval": interval or self.interval,
            "range": range_value or self.range_value,
            "includePrePost": "false",
            "events": "div,splits",
        }
        return f"{YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol_value))}?{urllib.parse.urlencode(params)}"

    def fetch_chart(
        self,
        symbol: str,
        *,
        range_value: str | None = None,
        interval: str | None = None,
    ) -> Mapping[str, Any]:
        return _request_json(
            self.daily_url(symbol, range_value=range_value, interval=interval),
            user_agent=self.user_agent,
            timeout_seconds=self.timeout_seconds,
        )

    def records_from_chart_payload(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("Yahoo chart payload must be a JSON object")
        chart = payload.get("chart") if isinstance(payload.get("chart"), Mapping) else {}
        errors = chart.get("error") if isinstance(chart, Mapping) else None
        if errors:
            raise SourceEvidenceError(f"Yahoo chart API error: {errors}")
        results = chart.get("result") if isinstance(chart, Mapping) else None
        if not isinstance(results, list) or not results:
            return tuple()
        result = results[0]
        if not isinstance(result, Mapping):
            return tuple()

        symbol_value = _symbol(symbol or (result.get("meta") or {}).get("symbol"))
        if not symbol_value:
            raise SourceEvidenceError("symbol is required for Yahoo chart records")
        timestamps = result.get("timestamp") if isinstance(result.get("timestamp"), list) else []
        indicators = result.get("indicators") if isinstance(result.get("indicators"), Mapping) else {}
        quotes = indicators.get("quote") if isinstance(indicators.get("quote"), list) else []
        quote = quotes[0] if quotes and isinstance(quotes[0], Mapping) else {}
        adjclose_rows = indicators.get("adjclose") if isinstance(indicators.get("adjclose"), list) else []
        adjclose = adjclose_rows[0].get("adjclose") if adjclose_rows and isinstance(adjclose_rows[0], Mapping) else []
        meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
        currency = _text(meta.get("currency"), "USD")
        exchange_timezone = _text(meta.get("exchangeTimezoneName")) or None
        records: list[SourceRecord] = []
        for index, ts in enumerate(timestamps[: self.max_records]):
            trade_date = _timestamp_to_trade_date(ts)
            if not trade_date:
                continue
            row = {
                "open": quote.get("open", [None])[index] if index < len(quote.get("open", [])) else None,
                "high": quote.get("high", [None])[index] if index < len(quote.get("high", [])) else None,
                "low": quote.get("low", [None])[index] if index < len(quote.get("low", [])) else None,
                "close": quote.get("close", [None])[index] if index < len(quote.get("close", [])) else None,
                "volume": quote.get("volume", [None])[index] if index < len(quote.get("volume", [])) else None,
                "adjclose": adjclose[index] if isinstance(adjclose, list) and index < len(adjclose) else None,
            }
            close_price = _float_or_none(row["close"])
            if close_price is None:
                continue
            normalized_row = {
                "schema_version": YAHOO_US_DAILY_OHLCV_SCHEMA_HASH,
                "target_table": "us_price_daily",
                "provider": "Yahoo Finance",
                "symbol": symbol_value,
                "symbol_canonical": f"{symbol_value}.US",
                "trade_date": trade_date,
                "open": _float_or_none(row["open"]),
                "high": _float_or_none(row["high"]),
                "low": _float_or_none(row["low"]),
                "close": close_price,
                "adjusted_close": _float_or_none(row["adjclose"]),
                "volume": _int_or_none(row["volume"]),
                "currency": currency,
                "exchange_timezone": exchange_timezone,
                "adjustment_policy": "yahoo_adjclose_when_present",
                "available_time": trade_date,
                "raw_row": dict(row),
            }
            row_hash = _stable_hash({"dataset": "us_price_daily", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"yahoo:daily:{symbol_value}:{trade_date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"Yahoo Finance daily OHLCV {symbol_value} {trade_date}",
                    content_ref=f"yahoo://chart/{symbol_value}/{trade_date}",
                    metadata={
                        "source_class": "public_market_reference",
                        "provider": "Yahoo Finance",
                        "dataset": "us_price_daily",
                        "symbol": symbol_value,
                        "symbol_canonical": f"{symbol_value}.US",
                        "trade_date": trade_date,
                        "event_time": trade_date,
                        "available_time": trade_date,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_market_reference",
                        "schema_hash": YAHOO_US_DAILY_OHLCV_SCHEMA_HASH,
                        "feature_targets": ["features/us_returns"],
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def source_health_from_result(self, result: Any) -> SourceHealth:
        run = getattr(result, "run", None)
        status = getattr(run, "status", "failed")
        run_status = status.value if hasattr(status, "value") else str(status)
        finished = _finished_at(result)
        watermark = getattr(result, "watermark", None)
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.OK.value if run_status == "completed" else SourceHealthStatus.FAILED.value,
            last_success_at=finished if run_status == "completed" else None,
            last_failure_at=finished if run_status != "completed" else None,
            latest_watermark=getattr(watermark, "value", None),
            row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
            rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
            schema_hash=YAHOO_US_DAILY_OHLCV_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "normalized_datasets": ["us_price_daily"],
                "provider": "Yahoo Finance",
            },
        )
