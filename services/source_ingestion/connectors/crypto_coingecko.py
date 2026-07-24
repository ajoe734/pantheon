"""CoinGecko public crypto spot market-data adapter."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.external_egress import open_external_url

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


COINGECKO_SPOT_CONNECTOR_ID = "crypto-coingecko-spot"
COINGECKO_API_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_SPOT_OHLC_SCHEMA_HASH = "crypto_spot_ohlc.v1"
COINGECKO_SPOT_PRICE_SCHEMA_HASH = "crypto_spot_price.v1"

DEFAULT_COINGECKO_SYMBOL_TO_ID: Mapping[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "ATOM": "cosmos",
    "NEAR": "near",
    "ARB": "arbitrum",
    "OP": "optimism",
}

DEFAULT_COINGECKO_ID_TO_SYMBOL: Mapping[str, str] = {
    coin_id: symbol for symbol, coin_id in DEFAULT_COINGECKO_SYMBOL_TO_ID.items()
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    try:
        return int(float(str(value).strip().replace(",", "")))
    except ValueError:
        return None


def _epoch_ms_to_iso(value: Any) -> str:
    try:
        seconds = int(value) / 1000.0
    except (TypeError, ValueError) as exc:
        raise SourceEvidenceError("CoinGecko OHLC timestamp must be epoch milliseconds") from exc
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch_s_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coin_id(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise SourceEvidenceError("CoinGecko coin_id is required")
    return normalized


def _vs_currency(value: Any) -> str:
    normalized = str(value or "usd").strip().lower()
    if not normalized:
        raise SourceEvidenceError("CoinGecko vs_currency is required")
    return normalized


def _coingecko_symbol(coin_id: str, explicit_symbol: str | None = None) -> str:
    symbol = str(explicit_symbol or "").strip().upper()
    if symbol:
        return symbol
    return str(DEFAULT_COINGECKO_ID_TO_SYMBOL.get(coin_id, coin_id)).upper()


def _coin_ids_from_symbols(symbols: Sequence[str] | str | None) -> tuple[str, ...]:
    if symbols in (None, "", []):
        return tuple()
    raw_symbols: Sequence[str]
    if isinstance(symbols, str):
        raw_symbols = [symbols]
    else:
        raw_symbols = symbols
    coin_ids: list[str] = []
    for symbol in raw_symbols:
        token = str(symbol or "").strip()
        if not token:
            continue
        mapped = DEFAULT_COINGECKO_SYMBOL_TO_ID.get(token.upper(), token.lower())
        if mapped not in coin_ids:
            coin_ids.append(mapped)
    return tuple(coin_ids)


def _request_json(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    user_agent: str = "pantheon-source-ingest/0.1",
) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with open_external_url(
        request,
        caller="source_ingest.crypto_coingecko",
        timeout=timeout_seconds,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def _finished_at(result: Any) -> str | None:
    run = getattr(result, "run", None)
    finished_at = getattr(run, "finished_at", None)
    if hasattr(finished_at, "isoformat"):
        return finished_at.isoformat().replace("+00:00", "Z")
    return finished_at


def _health_from_result(*, connector_id: str, result: Any, metadata: Mapping[str, Any] | None = None) -> SourceHealth:
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
        schema_hash=COINGECKO_SPOT_PRICE_SCHEMA_HASH,
        metadata={
            "connector_id": connector_id,
            "ingest_run_id": getattr(run, "ingest_run_id", None),
            "normalized_datasets": ["crypto_spot_ohlc", "crypto_spot_price"],
            **dict(metadata or {}),
        },
    )


@dataclass(frozen=True)
class CoinGeckoSpotMarketAdapter(SourceConnectorProvider):
    """CoinGecko keyless public API adapter for crypto spot OHLC and price data."""

    connector_id: str = COINGECKO_SPOT_CONNECTOR_ID
    api_base_url: str = COINGECKO_API_BASE_URL
    vs_currency: str = "usd"
    ohlc_days: str = "1"
    max_records: int = 100
    timeout_seconds: float = 20.0
    user_agent: str = "pantheon-source-ingest/0.1"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="CoinGecko",
            license_scope="public_crypto_market_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_crypto_market_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/coingecko-public-crypto-market-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=10,
                burst=2,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/coingecko-public-keyless-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="CoinGecko crypto spot market data",
                homepage_url="https://www.coingecko.com/",
                docs_url="https://docs.coingecko.com/reference/introduction",
                owner="CoinGecko",
                tags=("crypto", "coingecko", "ohlc", "spot_price", "public_market_data"),
            ),
            metadata={
                "source_class": "public_crypto_market_reference",
                "market": "CRYPTO",
                "venue": "COINGECKO",
                "normalized_datasets": ["crypto_spot_ohlc", "crypto_spot_price"],
                "schema_hashes": [COINGECKO_SPOT_OHLC_SCHEMA_HASH, COINGECKO_SPOT_PRICE_SCHEMA_HASH],
                "api_base_url": self.api_base_url.rstrip("/"),
                "auth": "keyless_public_api",
                "order_capable_provider": False,
                "direct_execution_allowed": False,
                "symbol_id_mapping": dict(DEFAULT_COINGECKO_SYMBOL_TO_ID),
                "feature_targets": ["features/crypto_returns", "features/crypto_liquidity"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "CoinGeckoSpotMarketAdapter.records_from_payload",
            "adapter_config": {
                "api_base_url": self.api_base_url.rstrip("/"),
                "vs_currency": self.vs_currency,
                "ohlc_days": self.ohlc_days,
                "max_records": self.max_records,
                "timeout_seconds": self.timeout_seconds,
                "user_agent": self.user_agent,
            },
            "request": {
                "dataset": "crypto_spot_ohlc_and_price",
                "coin_ids": ["bitcoin", "ethereum", "solana"],
                "vs_currency": self.vs_currency,
                "ohlc_days": self.ohlc_days,
            },
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "crypto_spot_ohlc_and_price",
        }

    def ohlc_url(self, coin_id: str, *, vs_currency: str | None = None, days: str | int | None = None) -> str:
        params = {
            "vs_currency": _vs_currency(vs_currency or self.vs_currency),
            "days": str(days or self.ohlc_days),
        }
        return f"{self.api_base_url.rstrip('/')}/coins/{_coin_id(coin_id)}/ohlc?{urllib.parse.urlencode(params)}"

    def simple_price_url(self, coin_ids: Sequence[str], *, vs_currency: str | None = None) -> str:
        ids = ",".join(_coin_id(coin_id) for coin_id in coin_ids)
        if not ids:
            raise SourceEvidenceError("CoinGecko simple price requires at least one coin_id")
        params = {
            "ids": ids,
            "vs_currencies": _vs_currency(vs_currency or self.vs_currency),
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        return f"{self.api_base_url.rstrip('/')}/simple/price?{urllib.parse.urlencode(params)}"

    def fetch_ohlc(self, coin_id: str, *, vs_currency: str | None = None, days: str | int | None = None) -> Any:
        return _request_json(
            self.ohlc_url(coin_id, vs_currency=vs_currency, days=days),
            timeout_seconds=self.timeout_seconds,
            user_agent=self.user_agent,
        )

    def fetch_simple_price(self, coin_ids: Sequence[str], *, vs_currency: str | None = None) -> Any:
        return _request_json(
            self.simple_price_url(coin_ids, vs_currency=vs_currency),
            timeout_seconds=self.timeout_seconds,
            user_agent=self.user_agent,
        )

    def records_from_ohlc_payload(
        self,
        coin_id: str,
        payload: Sequence[Sequence[Any]],
        *,
        vs_currency: str | None = None,
        days: str | int | None = None,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        normalized_coin_id = _coin_id(coin_id)
        currency = _vs_currency(vs_currency or self.vs_currency)
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
            raise SourceEvidenceError("CoinGecko OHLC payload must be a list")
        records: list[SourceRecord] = []
        for item in payload[: self.max_records]:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)) or len(item) < 5:
                continue
            candle_time = _epoch_ms_to_iso(item[0])
            symbol = _coingecko_symbol(normalized_coin_id)
            raw_row = {
                "timestamp": item[0],
                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],
            }
            normalized_row = {
                "schema_version": COINGECKO_SPOT_OHLC_SCHEMA_HASH,
                "target_table": "crypto_spot_ohlc",
                "provider": "CoinGecko",
                "market": "CRYPTO",
                "venue": "COINGECKO",
                "coin_id": normalized_coin_id,
                "symbol": symbol,
                "symbol_canonical": f"{symbol}.CRYPTO",
                "vs_currency": currency,
                "candle_time": candle_time,
                "trade_date": candle_time[:10],
                "open": _float_or_none(item[1]),
                "high": _float_or_none(item[2]),
                "low": _float_or_none(item[3]),
                "close": _float_or_none(item[4]),
                "available_time": candle_time,
                "coingecko_days": str(days or self.ohlc_days),
                "source_granularity": "coingecko_auto_interval",
                "raw_row": raw_row,
            }
            row_hash = _stable_hash({"dataset": "crypto_spot_ohlc", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"coingecko:ohlc:{normalized_coin_id}:{currency}:{candle_time}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"CoinGecko OHLC {symbol}/{currency.upper()} {candle_time}",
                    content_ref=source_url or self.ohlc_url(normalized_coin_id, vs_currency=currency, days=days),
                    metadata={
                        "source_class": "public_crypto_market_reference",
                        "provider": "CoinGecko",
                        "dataset": "crypto_spot_ohlc",
                        "market": "CRYPTO",
                        "venue": "COINGECKO",
                        "coin_id": normalized_coin_id,
                        "symbol": symbol,
                        "symbol_canonical": f"{symbol}.CRYPTO",
                        "vs_currency": currency,
                        "trade_date": candle_time[:10],
                        "event_time": candle_time,
                        "available_time": candle_time,
                        "source_url": source_url or self.ohlc_url(normalized_coin_id, vs_currency=currency, days=days),
                        "normalized_row": normalized_row,
                        "raw_row": raw_row,
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_crypto_market_reference",
                        "schema_hash": COINGECKO_SPOT_OHLC_SCHEMA_HASH,
                        "feature_targets": ["features/crypto_returns"],
                        "governance": {
                            "canonical_sink": "SourceRecord/EvidenceBundle",
                            "direct_execution_allowed": False,
                            "lean_consumption": "research_only_not_direct_action",
                            "broker_consumption": "not_direct_action",
                        },
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def records_from_simple_price_payload(
        self,
        payload: Mapping[str, Any],
        *,
        coin_ids: Sequence[str] | None = None,
        vs_currency: str | None = None,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("CoinGecko simple price payload must be a JSON object")
        currency = _vs_currency(vs_currency or self.vs_currency)
        requested_ids = tuple(_coin_id(coin_id) for coin_id in (coin_ids or payload.keys()))
        records: list[SourceRecord] = []
        for coin_id in requested_ids:
            price_row = payload.get(coin_id)
            if not isinstance(price_row, Mapping):
                continue
            price = _float_or_none(price_row.get(currency))
            if price is None:
                continue
            last_updated_at = _epoch_s_to_iso(price_row.get("last_updated_at")) or _utc_now()
            symbol = _coingecko_symbol(coin_id)
            raw_row = dict(price_row)
            normalized_row = {
                "schema_version": COINGECKO_SPOT_PRICE_SCHEMA_HASH,
                "target_table": "crypto_spot_price",
                "provider": "CoinGecko",
                "market": "CRYPTO",
                "venue": "COINGECKO",
                "coin_id": coin_id,
                "symbol": symbol,
                "symbol_canonical": f"{symbol}.CRYPTO",
                "vs_currency": currency,
                "price": price,
                "market_cap": _float_or_none(price_row.get(f"{currency}_market_cap")),
                "volume_24h": _float_or_none(price_row.get(f"{currency}_24h_vol")),
                "change_24h_pct": _float_or_none(price_row.get(f"{currency}_24h_change")),
                "as_of_time": last_updated_at,
                "available_time": last_updated_at,
                "raw_row": raw_row,
            }
            row_hash = _stable_hash({"dataset": "crypto_spot_price", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"coingecko:price:{coin_id}:{currency}:{last_updated_at}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"CoinGecko spot price {symbol}/{currency.upper()} {last_updated_at}",
                    content_ref=source_url or self.simple_price_url([coin_id], vs_currency=currency),
                    metadata={
                        "source_class": "public_crypto_market_reference",
                        "provider": "CoinGecko",
                        "dataset": "crypto_spot_price",
                        "market": "CRYPTO",
                        "venue": "COINGECKO",
                        "coin_id": coin_id,
                        "symbol": symbol,
                        "symbol_canonical": f"{symbol}.CRYPTO",
                        "vs_currency": currency,
                        "event_time": last_updated_at,
                        "available_time": last_updated_at,
                        "source_url": source_url or self.simple_price_url([coin_id], vs_currency=currency),
                        "normalized_row": normalized_row,
                        "raw_row": raw_row,
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_crypto_market_reference",
                        "schema_hash": COINGECKO_SPOT_PRICE_SCHEMA_HASH,
                        "feature_targets": ["features/crypto_returns", "features/crypto_liquidity"],
                        "governance": {
                            "canonical_sink": "SourceRecord/EvidenceBundle",
                            "direct_execution_allowed": False,
                            "lean_consumption": "research_only_not_direct_action",
                            "broker_consumption": "not_direct_action",
                        },
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def evidence_packet_from_records(self, records: Sequence[SourceRecord]) -> dict[str, Any]:
        datasets = sorted({str(record.metadata.get("dataset") or "") for record in records if record.metadata})
        source_ids = [record.source_id for record in records]
        latest_event_time = max(
            (str(record.metadata.get("event_time") or "") for record in records if record.metadata.get("event_time")),
            default=None,
        )
        return {
            "schema_version": "coingecko_source_evidence_packet.v1",
            "connector_id": self.connector_id,
            "provider": "CoinGecko",
            "record_count": len(records),
            "datasets": datasets,
            "source_ids": source_ids,
            "latest_event_time": latest_event_time,
            "governance": {
                "canonical_sink": "SourceRecord/EvidenceBundle",
                "direct_execution_allowed": False,
                "broker_consumption": "not_direct_action",
            },
        }

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            result=result,
            metadata={"provider": "CoinGecko", "market": "CRYPTO", "venue": "COINGECKO"},
        )
