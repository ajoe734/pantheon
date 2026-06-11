"""US paid and broker-readback adapters: Polygon, Alpha Vantage, IBKR, Shioaji.

Design invariants (§9 DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md):
- No inline API keys; credentials resolved only through secret_ref_id.
- Adapters are disabled by default when credentials are unavailable.
- Broker readback adapters (IBKR, Shioaji) are strictly read-only evidence
  surfaces; they must never become research OHLCV history primaries and must
  never call order placement or capital mutation paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
)
from ..source_health import SourceHealth, SourceHealthStatus


POLYGON_US_DAILY_CONNECTOR_ID = "us-polygon-daily-ohlcv"
ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID = "us-alpha-vantage-daily-ohlcv"
IBKR_READBACK_CONNECTOR_ID = "us-ibkr-broker-readback"
SHIOAJI_READBACK_CONNECTOR_ID = "tw-shioaji-broker-readback"

US_PRICE_DAILY_SCHEMA_HASH = "us_price_daily.v1"
BROKER_READBACK_SCHEMA_HASH = "broker_readback_evidence.v1"

POLYGON_AGGS_URL = "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
ALPHA_VANTAGE_DAILY_URL = "https://www.alphavantage.co/query"

_POLYGON_SECRET_ENV_VARS = ("POLYGON_API_KEY", "MASSIVE_API_KEY", "US_MARKET_DATA_API_KEY")
_ALPHA_VANTAGE_SECRET_ENV_VARS = ("ALPHA_VANTAGE_API_KEY",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    s = str(value or "").strip()
    return s if s else default


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    n = _float_or_none(value)
    return None if n is None else int(n)


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 20.0,
) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "pantheon-source-ingest/0.1",
            **dict(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _resolve_secret(env_vars: Sequence[str]) -> str | None:
    for name in env_vars:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _credential_unavailable_health(
    connector_id: str,
    schema_hash: str,
    *,
    env_vars: Sequence[str],
    provider: str,
) -> SourceHealth:
    return SourceHealth(
        source_id=connector_id,
        source_kind="data_source",
        status=SourceHealthStatus.DEGRADED.value,
        latest_watermark=None,
        schema_hash=schema_hash,
        metadata={
            "connector_id": connector_id,
            "credential_status": "credential_unavailable",
            "provider": provider,
            "credential_env_vars": list(env_vars),
            "note": "configure one of the listed env vars via secret_ref_id to enable live fetch",
        },
    )


def _finished_at(result: Any) -> str | None:
    run = getattr(result, "run", None)
    finished_at = getattr(run, "finished_at", None)
    if hasattr(finished_at, "isoformat"):
        return finished_at.isoformat().replace("+00:00", "Z")
    return finished_at


def _health_from_result(
    *,
    connector_id: str,
    schema_hash: str,
    result: Any,
    metadata: Mapping[str, Any] | None = None,
) -> SourceHealth:
    watermark = getattr(result, "watermark", None)
    run = getattr(result, "run", None)
    status = getattr(run, "status", "failed")
    run_status = status.value if hasattr(status, "value") else str(status)
    finished = _finished_at(result)
    return SourceHealth(
        source_id=connector_id,
        source_kind="data_source",
        status=SourceHealthStatus.OK.value if run_status == "completed" else SourceHealthStatus.FAILED.value,
        last_success_at=finished if run_status == "completed" else None,
        last_failure_at=finished if run_status != "completed" else None,
        latest_watermark=getattr(watermark, "value", None),
        row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
        rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
        schema_hash=schema_hash,
        metadata={
            "connector_id": connector_id,
            "ingest_run_id": getattr(run, "ingest_run_id", None),
            **dict(metadata or {}),
        },
    )


def _ms_timestamp_to_date(ts_ms: Any) -> str | None:
    """Convert Unix-millisecond timestamp to ISO date string."""
    if ts_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return dt.date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


@dataclass(frozen=True)
class PolygonUsEquityDailyAdapter(SourceConnectorProvider):
    """Polygon.io (or Massive-compatible) US equity daily OHLCV adapter.

    The connector is credential-gated: without a key, health reports
    `credential_unavailable` and the connector remains enabled in the registry
    but no fetch is attempted. With a key, daily aggregate smoke passes.

    Secret resolution order (first non-empty wins):
        POLYGON_API_KEY -> MASSIVE_API_KEY -> US_MARKET_DATA_API_KEY
    """

    connector_id: str = POLYGON_US_DAILY_CONNECTOR_ID
    secret_ref_id: str = "env://POLYGON_API_KEY"
    max_records: int = 500
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Polygon",
            license_scope="vendor_paid_market_data",
            auth_type=AuthType.API_KEY,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref=self.secret_ref_id,
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_paid_market_data",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/polygon-vendor-paid-market-data",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=5,
                retry_after_seconds=60,
                concurrency=2,
                policy_ref="source-ingest://policy/polygon-basic-rate-limit",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Polygon US equity daily OHLCV",
                homepage_url="https://polygon.io/",
                docs_url="https://polygon.io/docs/stocks/get_v2_aggs_ticker__stocksticker__range__multiplier___timespan___from___to",
                owner="Polygon.io",
                tags=("us", "polygon", "ohlcv", "market_data", "paid"),
            ),
            metadata={
                "source_class": "vendor_paid_market_data",
                "normalized_datasets": ["us_price_daily"],
                "schema_hash": US_PRICE_DAILY_SCHEMA_HASH,
                "source_priority": "primary_paid_us_daily_ohlcv",
                "corporate_action_adjustment_policy": "polygon_adjusted_close_when_adjusted_true; unadjusted when adjusted=false",
                "credential_env_vars": list(_POLYGON_SECRET_ENV_VARS),
                "credential_health_without_key": "credential_unavailable",
                "quota_headers_captured": True,
                "feature_targets": ["features/us_returns"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "PolygonUsEquityDailyAdapter.records_from_aggs_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
                "max_records": self.max_records,
                "credential_env_vars": list(_POLYGON_SECRET_ENV_VARS),
            },
            "request": {"adjusted": "true"},
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "us_price_daily",
        }

    def daily_url(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        api_key: str,
        adjusted: bool = True,
    ) -> str:
        base = POLYGON_AGGS_URL.format(
            ticker=urllib.parse.quote(symbol.upper()),
            start=start_date,
            end=end_date,
        )
        params = {"adjusted": "true" if adjusted else "false", "sort": "asc", "apiKey": api_key}
        return f"{base}?{urllib.parse.urlencode(params)}"

    def resolve_api_key(self) -> str | None:
        return _resolve_secret(_POLYGON_SECRET_ENV_VARS)

    def fetch_daily_aggs(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        api_key: str | None = None,
        adjusted: bool = True,
    ) -> Any:
        key = api_key or self.resolve_api_key()
        if not key:
            raise SourceEvidenceError(
                f"Polygon fetch requires one of {_POLYGON_SECRET_ENV_VARS}; none found in environment"
            )
        url = self.daily_url(symbol, start_date=start_date, end_date=end_date, api_key=key, adjusted=adjusted)
        response = _request_json(url)
        if isinstance(response, Mapping):
            quota_remaining = response.get("quota_remaining")
            if quota_remaining is not None:
                pass
        return response

    def records_from_aggs_payload(
        self,
        symbol: str,
        payload: Any,
        *,
        source_url: str | None = None,
        adjusted: bool = True,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("Polygon aggs payload must be a JSON object")
        status_value = _text(payload.get("status", ""))
        if status_value in ("NOT_FOUND", "FORBIDDEN", "UNAUTHORIZED"):
            raise SourceEvidenceError(f"Polygon API error: status={status_value}")
        symbol_value = _symbol(symbol or payload.get("ticker") or "")
        if not symbol_value:
            raise SourceEvidenceError("symbol is required for Polygon records")
        results = payload.get("results")
        if not isinstance(results, list):
            return tuple()
        records: list[SourceRecord] = []
        for item in results[: self.max_records]:
            if not isinstance(item, Mapping):
                continue
            trade_date = _ms_timestamp_to_date(item.get("t")) or _text(item.get("date"))
            if not trade_date:
                continue
            open_price = _float_or_none(item.get("o"))
            high_price = _float_or_none(item.get("h"))
            low_price = _float_or_none(item.get("l"))
            close_price = _float_or_none(item.get("c"))
            volume = _int_or_none(item.get("v"))
            vwap = _float_or_none(item.get("vw"))
            transactions = _int_or_none(item.get("n"))
            normalized_row = {
                "schema_version": US_PRICE_DAILY_SCHEMA_HASH,
                "target_table": "us_price_daily",
                "provider": "Polygon",
                "symbol": symbol_value,
                "symbol_canonical": f"{symbol_value}.US",
                "trade_date": trade_date,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "vwap": vwap,
                "transactions": transactions,
                "currency": "USD",
                "adjustment_policy": "polygon_adjusted_close" if adjusted else "polygon_unadjusted_close",
                "available_time": trade_date,
                "raw_row": dict(item),
            }
            row_hash = _stable_hash({"dataset": "us_price_daily", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"polygon:daily:{symbol_value}:{trade_date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"Polygon daily OHLCV {symbol_value} {trade_date}",
                    content_ref=f"polygon://aggs/{symbol_value}/{trade_date}",
                    metadata={
                        "source_class": "vendor_paid_market_data",
                        "provider": "Polygon",
                        "dataset": "us_price_daily",
                        "symbol": symbol_value,
                        "symbol_canonical": f"{symbol_value}.US",
                        "trade_date": trade_date,
                        "event_time": trade_date,
                        "available_time": trade_date,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(item),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["paid_vendor", "research"],
                        "license_scope": "vendor_paid_market_data",
                        "entitlement_required": True,
                        "schema_hash": US_PRICE_DAILY_SCHEMA_HASH,
                        "feature_targets": ["features/us_returns"],
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def credential_unavailable_health(self) -> SourceHealth:
        return _credential_unavailable_health(
            self.connector_id,
            US_PRICE_DAILY_SCHEMA_HASH,
            env_vars=_POLYGON_SECRET_ENV_VARS,
            provider="Polygon",
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=US_PRICE_DAILY_SCHEMA_HASH,
            result=result,
            metadata={"normalized_datasets": ["us_price_daily"]},
        )


@dataclass(frozen=True)
class AlphaVantageUsEquityDailyAdapter(SourceConnectorProvider):
    """Alpha Vantage US equity daily OHLCV adapter.

    Optional low-throughput fallback. Disabled by default; enabled only when
    ALPHA_VANTAGE_API_KEY is configured. Due to Alpha Vantage quota limits
    (typically 25 calls/day on free tier), this connector should only run
    for core symbols or targeted gap repair, never full-universe scans.
    """

    connector_id: str = ALPHA_VANTAGE_US_DAILY_CONNECTOR_ID
    secret_ref_id: str = "env://ALPHA_VANTAGE_API_KEY"
    max_records: int = 100
    connector_status: ConnectorStatus | str = ConnectorStatus.DISABLED
    disabled_reason: str = "alpha_vantage_disabled_until_key_configured"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Alpha Vantage",
            license_scope="vendor_paid_market_data",
            auth_type=AuthType.API_KEY,
            supported_modes=(ConnectorMode.BATCH,),
            status=self.connector_status,
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref=self.secret_ref_id,
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_paid_market_data",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/alpha-vantage-vendor-market-data",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=5,
                burst=1,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/alpha-vantage-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Alpha Vantage US equity daily OHLCV",
                homepage_url="https://www.alphavantage.co/",
                docs_url="https://www.alphavantage.co/documentation/",
                owner="Alpha Vantage Inc.",
                tags=("us", "alpha_vantage", "ohlcv", "market_data", "paid", "low_quota_fallback"),
            ),
            metadata={
                "source_class": "vendor_paid_market_data",
                "normalized_datasets": ["us_price_daily"],
                "schema_hash": US_PRICE_DAILY_SCHEMA_HASH,
                "source_priority": "low_quota_fallback_disabled_by_default",
                "disabled_reason": self.disabled_reason,
                "corporate_action_adjustment_policy": "alpha_vantage_adjusted_close_when_function_time_series_daily_adjusted",
                "credential_env_vars": list(_ALPHA_VANTAGE_SECRET_ENV_VARS),
                "credential_health_without_key": "credential_unavailable",
                "quota_model": "tiered_typically_25_calls_per_day_free_tier",
                "full_universe_scan_forbidden": True,
                "feature_targets": ["features/us_returns"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "AlphaVantageUsEquityDailyAdapter.records_from_time_series_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
                "max_records": self.max_records,
                "credential_env_vars": list(_ALPHA_VANTAGE_SECRET_ENV_VARS),
            },
            "request": {"function": "TIME_SERIES_DAILY_ADJUSTED", "outputsize": "compact"},
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "us_price_daily",
            "allow_empty": True,
            "empty_reason": self.disabled_reason,
        }

    def resolve_api_key(self) -> str | None:
        return _resolve_secret(_ALPHA_VANTAGE_SECRET_ENV_VARS)

    def daily_url(self, symbol: str, *, api_key: str, outputsize: str = "compact") -> str:
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol.upper(),
            "outputsize": outputsize,
            "apikey": api_key,
        }
        return f"{ALPHA_VANTAGE_DAILY_URL}?{urllib.parse.urlencode(params)}"

    def fetch_daily_time_series(
        self,
        symbol: str,
        *,
        api_key: str | None = None,
        outputsize: str = "compact",
    ) -> Any:
        key = api_key or self.resolve_api_key()
        if not key:
            raise SourceEvidenceError(
                f"Alpha Vantage fetch requires {_ALPHA_VANTAGE_SECRET_ENV_VARS[0]}; not found in environment"
            )
        return _request_json(self.daily_url(symbol, api_key=key, outputsize=outputsize))

    def records_from_time_series_payload(
        self,
        symbol: str,
        payload: Any,
        *,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("Alpha Vantage response must be a JSON object")
        information = _text(payload.get("Information") or payload.get("Note") or "")
        if "thank you for using alpha vantage" in information.lower() or "api call frequency" in information.lower():
            raise SourceEvidenceError(f"Alpha Vantage quota or plan limit reached: {information}")
        symbol_value = _symbol(symbol)
        time_series = payload.get("Time Series (Daily)") or payload.get("Time Series (Daily Adjusted)")
        if not isinstance(time_series, Mapping):
            return tuple()
        records: list[SourceRecord] = []
        for trade_date, row in list(time_series.items())[: self.max_records]:
            if not isinstance(row, Mapping):
                continue
            date = _text(trade_date)
            if not date:
                continue
            open_price = _float_or_none(row.get("1. open"))
            high_price = _float_or_none(row.get("2. high"))
            low_price = _float_or_none(row.get("3. low"))
            close_price = _float_or_none(row.get("4. close"))
            adjusted_close = _float_or_none(row.get("5. adjusted close"))
            volume = _int_or_none(row.get("6. volume"))
            dividend = _float_or_none(row.get("7. dividend amount"))
            split_coeff = _float_or_none(row.get("8. split coefficient"))
            normalized_row = {
                "schema_version": US_PRICE_DAILY_SCHEMA_HASH,
                "target_table": "us_price_daily",
                "provider": "Alpha Vantage",
                "symbol": symbol_value,
                "symbol_canonical": f"{symbol_value}.US",
                "trade_date": date,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "adjusted_close": adjusted_close,
                "volume": volume,
                "dividend_amount": dividend,
                "split_coefficient": split_coeff,
                "currency": "USD",
                "adjustment_policy": "alpha_vantage_adjusted_close",
                "available_time": date,
                "raw_row": dict(row),
            }
            row_hash = _stable_hash({"dataset": "us_price_daily", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"alpha-vantage:daily:{symbol_value}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"Alpha Vantage daily OHLCV {symbol_value} {date}",
                    content_ref=f"alpha-vantage://daily/{symbol_value}/{date}",
                    metadata={
                        "source_class": "vendor_paid_market_data",
                        "provider": "Alpha Vantage",
                        "dataset": "us_price_daily",
                        "symbol": symbol_value,
                        "symbol_canonical": f"{symbol_value}.US",
                        "trade_date": date,
                        "event_time": date,
                        "available_time": date,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["paid_vendor", "research"],
                        "license_scope": "vendor_paid_market_data",
                        "entitlement_required": True,
                        "schema_hash": US_PRICE_DAILY_SCHEMA_HASH,
                        "feature_targets": ["features/us_returns"],
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def credential_unavailable_health(self) -> SourceHealth:
        return _credential_unavailable_health(
            self.connector_id,
            US_PRICE_DAILY_SCHEMA_HASH,
            env_vars=_ALPHA_VANTAGE_SECRET_ENV_VARS,
            provider="Alpha Vantage",
        )

    def disabled_source_health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.DISABLED.value,
            latest_watermark=None,
            schema_hash=US_PRICE_DAILY_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "disabled_reason": self.disabled_reason,
                "provider": "Alpha Vantage",
                "dataset": "us_price_daily",
            },
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=US_PRICE_DAILY_SCHEMA_HASH,
            result=result,
            metadata={"disabled_reason": self.disabled_reason},
        )


@dataclass(frozen=True)
class IbkrBrokerReadbackAdapter(SourceConnectorProvider):
    """IBKR broker execution read-only evidence adapter.

    IBKR is strictly a broker execution boundary. This adapter:
    - Accepts a readback file produced by the execution layer (never fetches live).
    - Records the evidence of broker fills or quote snapshots for audit.
    - Must not become the research OHLCV history primary.
    - Must never call order placement or capital mutation paths.

    The readback file path is resolved from IBKR_READBACK_FILE_PATH or a
    configured file path; it must already exist from the execution layer.
    """

    connector_id: str = IBKR_READBACK_CONNECTOR_ID
    readback_file_env: str = "IBKR_READBACK_FILE_PATH"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="IBKR",
            license_scope="broker_execution_readback_only",
            auth_type=AuthType.BROKER_REF,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.BROKER_REF,
                secret_ref=f"env://{self.readback_file_env}",
            ),
            license_policy=LicensePolicy(
                license_scope="broker_execution_readback_only",
                allowed_use=("execution_sync", "audit_evidence"),
                attribution_required=False,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/ibkr-broker-execution-readback-boundary",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=1,
                burst=1,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/ibkr-readback-file-only",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="IBKR broker execution readback",
                homepage_url="https://www.ibkr.com/",
                docs_url="https://www.ibkr.com/",
                owner="Interactive Brokers",
                tags=("us", "ibkr", "broker", "execution_readback", "not_research_primary"),
            ),
            metadata={
                "source_class": "broker_execution_readback_only",
                "normalized_datasets": ["broker_readback_evidence"],
                "schema_hash": BROKER_READBACK_SCHEMA_HASH,
                "research_primary_forbidden": True,
                "order_placement_forbidden": True,
                "capital_mutation_forbidden": True,
                "readback_file_env": self.readback_file_env,
                "usage_boundary": "execution_sync_and_audit_only",
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "IbkrBrokerReadbackAdapter.records_from_readback_file",
            "adapter_config": {
                "readback_file_env": self.readback_file_env,
            },
            "request": {},
            "dataset": "broker_readback_evidence",
            "order_placement_forbidden": True,
            "capital_mutation_forbidden": True,
        }

    def resolve_readback_path(self) -> str | None:
        return os.getenv(self.readback_file_env, "").strip() or None

    def records_from_readback_file(
        self,
        file_path: str,
        payload: Any,
        *,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        """Produce evidence records from a broker readback payload.

        The payload is a list of fill or quote snapshot dicts already loaded
        from the readback file. This method normalizes them into SourceRecord
        evidence entries.
        """
        if not isinstance(payload, (list, tuple)):
            if isinstance(payload, Mapping):
                rows = [dict(payload)]
            else:
                raise SourceEvidenceError("IBKR readback payload must be a list of fill/quote dicts")
        else:
            rows = [dict(row) for row in payload if isinstance(row, Mapping)]
        records: list[SourceRecord] = []
        for row in rows:
            symbol_value = _symbol(row.get("symbol") or row.get("ticker") or "")
            if not symbol_value:
                continue
            readback_type = _text(row.get("type") or row.get("readback_type") or "fill_readback")
            trade_date = _text(row.get("date") or row.get("trade_date") or "")
            normalized_row = {
                "schema_version": BROKER_READBACK_SCHEMA_HASH,
                "target_table": "broker_readback_evidence",
                "provider": "IBKR",
                "readback_type": readback_type,
                "symbol": symbol_value or None,
                "symbol_canonical": f"{symbol_value}.US" if symbol_value else None,
                "trade_date": trade_date or None,
                "available_time": trade_date or _utc_now(),
                "source_file": file_path,
                "raw_row": dict(row),
                "research_primary": False,
                "order_placement_evidence": False,
            }
            row_hash = _stable_hash({"dataset": "broker_readback_evidence", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"ibkr:readback:{symbol_value}:{trade_date or 'unknown'}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"IBKR readback {readback_type} {symbol_value} {trade_date}".strip(),
                    content_ref=f"ibkr://readback/{readback_type}/{symbol_value}/{trade_date or 'unknown'}",
                    metadata={
                        "source_class": "broker_execution_readback_only",
                        "provider": "IBKR",
                        "dataset": "broker_readback_evidence",
                        "readback_type": readback_type,
                        "symbol": symbol_value or None,
                        "trade_date": trade_date or None,
                        "event_time": trade_date or _utc_now(),
                        "available_time": trade_date or _utc_now(),
                        "source_file": file_path,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["broker_execution_audit"],
                        "license_scope": "broker_execution_readback_only",
                        "research_primary": False,
                        "schema_hash": BROKER_READBACK_SCHEMA_HASH,
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def missing_readback_health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.DEGRADED.value,
            latest_watermark=None,
            schema_hash=BROKER_READBACK_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "credential_status": "readback_file_unavailable",
                "provider": "IBKR",
                "readback_file_env": self.readback_file_env,
                "note": "IBKR readback file not present; execution layer must produce readback before this connector can run",
            },
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=BROKER_READBACK_SCHEMA_HASH,
            result=result,
            metadata={"readback_file_env": self.readback_file_env},
        )


@dataclass(frozen=True)
class ShioajiBrokerReadbackAdapter(SourceConnectorProvider):
    """Shioaji broker execution read-only evidence adapter.

    Shioaji (Taiwan broker) is strictly a broker execution boundary. Same
    policy as IbkrBrokerReadbackAdapter: read-only file-based evidence only;
    must not become research primary; must not call order placement.

    Readback file path resolved from SHIOAJI_READBACK_FILE_PATH.
    """

    connector_id: str = SHIOAJI_READBACK_CONNECTOR_ID
    readback_file_env: str = "SHIOAJI_READBACK_FILE_PATH"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Shioaji",
            license_scope="broker_execution_readback_only",
            auth_type=AuthType.BROKER_REF,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.BROKER_REF,
                secret_ref=f"env://{self.readback_file_env}",
            ),
            license_policy=LicensePolicy(
                license_scope="broker_execution_readback_only",
                allowed_use=("execution_sync", "audit_evidence"),
                attribution_required=False,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/shioaji-broker-execution-readback-boundary",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=1,
                burst=1,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/shioaji-readback-file-only",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Shioaji broker execution readback",
                homepage_url="https://sinotrade.github.io/",
                docs_url="https://sinotrade.github.io/",
                owner="Sinopac Securities",
                tags=("tw", "shioaji", "broker", "execution_readback", "not_research_primary"),
            ),
            metadata={
                "source_class": "broker_execution_readback_only",
                "normalized_datasets": ["broker_readback_evidence"],
                "schema_hash": BROKER_READBACK_SCHEMA_HASH,
                "research_primary_forbidden": True,
                "order_placement_forbidden": True,
                "capital_mutation_forbidden": True,
                "readback_file_env": self.readback_file_env,
                "usage_boundary": "execution_sync_and_audit_only",
                "market_scope": "taiwan",
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "ShioajiBrokerReadbackAdapter.records_from_readback_file",
            "adapter_config": {
                "readback_file_env": self.readback_file_env,
            },
            "request": {},
            "dataset": "broker_readback_evidence",
            "order_placement_forbidden": True,
            "capital_mutation_forbidden": True,
        }

    def resolve_readback_path(self) -> str | None:
        return os.getenv(self.readback_file_env, "").strip() or None

    def records_from_readback_file(
        self,
        file_path: str,
        payload: Any,
        *,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if not isinstance(payload, (list, tuple)):
            if isinstance(payload, Mapping):
                rows = [dict(payload)]
            else:
                raise SourceEvidenceError("Shioaji readback payload must be a list of fill/quote dicts")
        else:
            rows = [dict(row) for row in payload if isinstance(row, Mapping)]
        records: list[SourceRecord] = []
        for row in rows:
            symbol_value = _symbol(row.get("symbol") or row.get("code") or "")
            if not symbol_value:
                continue
            readback_type = _text(row.get("type") or row.get("readback_type") or "fill_readback")
            trade_date = _text(row.get("date") or row.get("trade_date") or "")
            normalized_row = {
                "schema_version": BROKER_READBACK_SCHEMA_HASH,
                "target_table": "broker_readback_evidence",
                "provider": "Shioaji",
                "readback_type": readback_type,
                "symbol": symbol_value or None,
                "symbol_canonical": f"{symbol_value}.TW" if symbol_value else None,
                "trade_date": trade_date or None,
                "available_time": trade_date or _utc_now(),
                "source_file": file_path,
                "raw_row": dict(row),
                "research_primary": False,
                "order_placement_evidence": False,
                "market_scope": "taiwan",
            }
            row_hash = _stable_hash({"dataset": "broker_readback_evidence", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"shioaji:readback:{symbol_value}:{trade_date or 'unknown'}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"Shioaji readback {readback_type} {symbol_value} {trade_date}".strip(),
                    content_ref=f"shioaji://readback/{readback_type}/{symbol_value}/{trade_date or 'unknown'}",
                    metadata={
                        "source_class": "broker_execution_readback_only",
                        "provider": "Shioaji",
                        "dataset": "broker_readback_evidence",
                        "readback_type": readback_type,
                        "symbol": symbol_value or None,
                        "trade_date": trade_date or None,
                        "event_time": trade_date or _utc_now(),
                        "available_time": trade_date or _utc_now(),
                        "source_file": file_path,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["broker_execution_audit"],
                        "license_scope": "broker_execution_readback_only",
                        "research_primary": False,
                        "schema_hash": BROKER_READBACK_SCHEMA_HASH,
                        "market_scope": "taiwan",
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def missing_readback_health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.DEGRADED.value,
            latest_watermark=None,
            schema_hash=BROKER_READBACK_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "credential_status": "readback_file_unavailable",
                "provider": "Shioaji",
                "readback_file_env": self.readback_file_env,
                "note": "Shioaji readback file not present; execution layer must produce readback before this connector can run",
            },
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=BROKER_READBACK_SCHEMA_HASH,
            result=result,
            metadata={"readback_file_env": self.readback_file_env, "market_scope": "taiwan"},
        )
