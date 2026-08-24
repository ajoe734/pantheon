"""TWSE/TPEx official market-data source-ingest adapter.

The adapter keeps official exchange payload parsing behind the provider
boundary. Source-ingest can store the connector contract and replay normalized
records without learning each TWSE/TPEx field name.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.external_egress import open_external_url
from services.source_ingestion.source_health import SourceHealth

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceMetadata,
    SourceRecord,
)


TW_OFFICIAL_CONNECTOR_ID = "tw-twse-tpex-official-market"
TW_OFFICIAL_SCHEMA_HASH = "tw_taiwan_official_market.v1"
TW_PRICE_DAILY_SCHEMA_HASH = "tw_price_daily.v1"
TW_INSTITUTIONAL_FLOW_SCHEMA_HASH = "tw_institutional_flow.v1"
TW_MARGIN_SHORT_BALANCE_SCHEMA_HASH = "tw_margin_short_balance.v1"
TW_SECURITIES_LENDING_SCHEMA_HASH = "tw_securities_lending.v1"
TW_DAY_TRADING_SCHEMA_HASH = "tw_day_trading.v1"
TDCC_SHAREHOLDING_CONNECTOR_ID = "tw-tdcc-shareholding-distribution"
TDCC_SHAREHOLDING_SCHEMA_HASH = "tw_tdcc_shareholding_distribution.v1"
TAIFEX_DERIVATIVES_CONNECTOR_ID = "tw-taifex-futures-options-chip"
TAIFEX_FUTURES_CHIP_SCHEMA_HASH = "tw_taifex_futures_chip.v1"
TAIFEX_OPTIONS_CHIP_SCHEMA_HASH = "tw_taifex_options_chip.v1"

TWSE_OPENAPI_BASE_URL = "https://openapi.twse.com.tw/v1"
TWSE_LEGACY_BASE_URL = "https://www.twse.com.tw/rwd/zh"
TPEX_OPENAPI_BASE_URL = "https://www.tpex.org.tw/openapi/v1"
TDCC_OPENAPI_BASE_URL = "https://openapi.tdcc.com.tw/v1"
TAIFEX_OPENAPI_BASE_URL = "https://openapi.taifex.com.tw/v1"

_DATASET_SCHEMA_HASHES = {
    "tw_price_daily": TW_PRICE_DAILY_SCHEMA_HASH,
    "tw_institutional_flow": TW_INSTITUTIONAL_FLOW_SCHEMA_HASH,
    "tw_margin_short_balance": TW_MARGIN_SHORT_BALANCE_SCHEMA_HASH,
    "tw_securities_lending": TW_SECURITIES_LENDING_SCHEMA_HASH,
    "tw_day_trading": TW_DAY_TRADING_SCHEMA_HASH,
    "tdcc_shareholding_distribution": TDCC_SHAREHOLDING_SCHEMA_HASH,
    "taifex_futures_chip": TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
    "taifex_options_chip": TAIFEX_OPTIONS_CHIP_SCHEMA_HASH,
}


TAIWAN_OFFICIAL_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {
        "dataset": "tw_price_daily",
        "venue": "TWSE",
        "source_dataset": "STOCK_DAY_ALL",
        "endpoint": f"{TWSE_OPENAPI_BASE_URL}/exchangeReport/STOCK_DAY_ALL",
        "transport": "twse_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe", "archive_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_price_daily",
        "venue": "TPEx",
        "source_dataset": "tpex_mainboard_daily_close_quotes",
        "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_mainboard_daily_close_quotes",
        "transport": "tpex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe", "archive_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_institutional_flow",
        "venue": "TWSE",
        "source_dataset": "T86",
        "endpoint": f"{TWSE_LEGACY_BASE_URL}/fund/T86?response=json&selectType=ALLBUT0999",
        "transport": "twse_legacy_json",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_institutional_flow",
        "venue": "TPEx",
        "source_dataset": "tpex_3insti_daily_trading",
        "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_3insti_daily_trading",
        "transport": "tpex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_margin_short_balance",
        "venue": "TWSE",
        "source_dataset": "MI_MARGN",
        "endpoint": f"{TWSE_OPENAPI_BASE_URL}/exchangeReport/MI_MARGN",
        "transport": "twse_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_margin_short_balance",
        "venue": "TPEx",
        "source_dataset": "tpex_mainboard_margin_balance",
        "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_mainboard_margin_balance",
        "transport": "tpex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_securities_lending",
        "venue": "TWSE",
        "source_dataset": "TWT96U",
        "endpoint": f"{TWSE_OPENAPI_BASE_URL}/SBL/TWT96U",
        "transport": "twse_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented_available_volume",
    },
    {
        "dataset": "tw_securities_lending",
        "venue": "TPEx",
        "source_dataset": "tpex_margin_sbl",
        "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_margin_sbl",
        "transport": "tpex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tw_day_trading",
        "venue": "TWSE",
        "source_dataset": "TWTB4U",
        "endpoint": f"{TWSE_OPENAPI_BASE_URL}/exchangeReport/TWTB4U",
        "transport": "twse_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented_symbol_eligibility",
    },
    {
        "dataset": "tw_day_trading",
        "venue": "TPEx",
        "source_dataset": "tpex_securities",
        "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_securities",
        "transport": "tpex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented_symbol_eligibility",
    },
    {
        "dataset": "tdcc_shareholding_distribution",
        "venue": "TDCC",
        "source_dataset": "TDCC_OD_1-5",
        "endpoint": f"{TDCC_OPENAPI_BASE_URL}/opendata/TDCC_OD_1-5",
        "transport": "tdcc_openapi",
        "cadence": "weekly_after_tdcc_publication",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "taifex_futures_chip",
        "venue": "TAIFEX",
        "source_dataset": "Daily_Institutional_Trading_Futures",
        "endpoint": f"{TAIFEX_OPENAPI_BASE_URL}/Daily_Institutional_Trading_Futures",
        "transport": "taifex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "taifex_options_chip",
        "venue": "TAIFEX",
        "source_dataset": "Daily_Institutional_Trading_Options",
        "endpoint": f"{TAIFEX_OPENAPI_BASE_URL}/Daily_Institutional_Trading_Options",
        "transport": "taifex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _squash_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value).lower()


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    squashed = {_squash_key(str(key)): value for key, value in row.items()}
    for key in keys:
        value = squashed.get(_squash_key(key))
        if value not in (None, ""):
            return value
    return None


def _int(value: Any) -> int | None:
    if value in (None, "", "-", "--", "---", "N/A", "X"):
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "---", "N/A", "X"):
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _roc_date_to_iso(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) == 7:
        year = int(digits[:3]) + 1911
        return f"{year:04d}-{int(digits[3:5]):02d}-{int(digits[5:7]):02d}"
    if len(digits) == 8:
        return f"{int(digits[:4]):04d}-{int(digits[4:6]):02d}-{int(digits[6:8]):02d}"
    return text


def _canonical_venue(venue: str) -> str:
    normalized = str(venue or "").strip().upper()
    if normalized in {"TPEX", "TWO", "OTC", "GRETAI", "GTSM"}:
        return "TPEx"
    if normalized in {"TWSE", "TSE", "TW"}:
        return "TWSE"
    if normalized == "TDCC":
        return "TDCC"
    if normalized == "TAIFEX":
        return "TAIFEX"
    raise ValueError(f"unsupported Taiwan venue: {venue}")


def _symbol_canonical(symbol: str, venue: str) -> str:
    v = _canonical_venue(venue)
    if v == "TWSE":
        return f"{symbol}.TWSE"
    if v == "TPEx":
        return f"{symbol}.TPEX"
    if v == "TDCC":
        return f"{symbol}.TW"
    if v == "TAIFEX":
        return f"{symbol}.TX"
    return f"{symbol}.{v}"


def _rows_from_payload(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping):
        fields = payload.get("fields")
        data = payload.get("data")
        if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)) and isinstance(data, list):
            field_names = [str(field) for field in fields]
            rows = []
            for item in data:
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                    rows.append(dict(zip(field_names, item)))
                elif isinstance(item, Mapping):
                    rows.append(dict(item))
            return tuple(rows)
        for key in ("data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return tuple(dict(item) for item in value if isinstance(item, Mapping))
        return (dict(payload),)
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(dict(item) for item in payload if isinstance(item, Mapping))
    return tuple()


def _endpoint_for(dataset: str, venue: str) -> Mapping[str, Any]:
    canonical = _canonical_venue(venue)
    for endpoint in TAIWAN_OFFICIAL_ENDPOINTS:
        if endpoint.get("dataset") == dataset and endpoint.get("venue") == canonical:
            return endpoint
    raise ValueError(f"unsupported Taiwan official endpoint: dataset={dataset} venue={venue}")


def _tier_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"core", "core_universe"}:
        return "core_universe"
    if normalized in {"candidate", "candidate_universe"}:
        return "candidate_universe"
    if normalized in {"archive", "archive_universe"}:
        return "archive_universe"
    return normalized or "core_universe"


@dataclass(frozen=True)
class TaiwanOfficialMarketDatasetAdapter(SourceConnectorProvider):
    """TWSE/TPEx official daily price and chip-summary adapter."""

    connector_id: str = TW_OFFICIAL_CONNECTOR_ID
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="TWSE/TPEx",
            license_scope="official_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="official_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/twse-tpex-official-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/twse-tpex-official-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="TWSE/TPEx official market data",
                homepage_url="https://openapi.twse.com.tw/",
                docs_url="https://openapi.twse.com.tw/v1/swagger.json",
                owner="TWSE and TPEx",
                tags=("taiwan", "twse", "tpex", "official_reference", "market_data", "chip"),
            ),
            metadata={
                "source_class": "official_reference",
                "official_reference_truth": True,
                "dataset_schema_hash": TW_OFFICIAL_SCHEMA_HASH,
                "normalized_datasets": sorted(_DATASET_SCHEMA_HASHES),
                "endpoint_inventory": [dict(endpoint) for endpoint in TAIWAN_OFFICIAL_ENDPOINTS],
                "tier_policy": {
                    "core_universe": ["tw_price_daily", "tw_institutional_flow", "tw_margin_short_balance", "tw_securities_lending", "tw_day_trading"],
                    "candidate_universe": ["tw_price_daily", "tw_institutional_flow", "tw_margin_short_balance", "tw_securities_lending", "tw_day_trading"],
                    "archive_universe": ["tw_price_daily"],
                },
                "excluded_followups": [
                    {
                        "dataset": endpoint["dataset"],
                        "venue": endpoint["venue"],
                        "followup_task": endpoint["followup_task"],
                    }
                    for endpoint in TAIWAN_OFFICIAL_ENDPOINTS
                    if endpoint.get("status") == "excluded_followup"
                ],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
            "adapter_config": {
                "max_records": self.max_records,
            },
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE", "TPEx"],
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        dataset: str,
        venue: str,
        *,
        timeout_seconds: float = 20.0,
    ) -> Any:
        endpoint = dict(_endpoint_for(dataset, venue))
        url = str(endpoint.get("endpoint") or "")
        if not url:
            raise ValueError(f"Taiwan official endpoint is not fetchable: dataset={dataset} venue={venue}")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        with open_external_url(
            request,
            caller="source_ingest.taiwan_official",
            timeout=timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def records_from_payload(
        self,
        dataset: str,
        venue: str,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        source_dataset: str | None = None,
        api_endpoint: str | None = None,
        trade_date: str | None = None,
        available_time: str | None = None,
        universe_tier: str = "core_universe",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        tier = _tier_name(universe_tier)
        if tier == "archive_universe" and dataset != "tw_price_daily":
            return tuple()

        endpoint = dict(_endpoint_for(dataset, venue))
        source_dataset = source_dataset or str(endpoint.get("source_dataset") or dataset)
        api_endpoint = api_endpoint or str(endpoint.get("endpoint") or "")
        if trade_date is None and isinstance(payload, Mapping):
            trade_date = _roc_date_to_iso(payload.get("date") or payload.get("Date") or payload.get("資料日期"))
        normalized_rows = self.normalized_rows_from_payload(
            dataset,
            venue,
            payload,
            source_dataset=source_dataset,
            api_endpoint=api_endpoint,
            trade_date=trade_date,
            available_time=available_time,
        )
        records: list[SourceRecord] = []
        for row in normalized_rows[: self.max_records]:
            row_hash = _stable_hash({"dataset": dataset, "venue": row["venue"], "row": row})
            symbol = _text(row.get("symbol"), "market")
            as_of_date = _text(row.get("date"), row_hash)
            content_ref = f"tw-official://{dataset}/{row['venue']}/{symbol}/{as_of_date}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"tw-official:{dataset}:{row['venue']}:{symbol}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TW official {dataset} {row['venue']} {symbol} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "official_reference",
                        "provider": "TWSE/TPEx",
                        "dataset": dataset,
                        "source_dataset": source_dataset,
                        "venue": row["venue"],
                        "symbol": row.get("symbol"),
                        "symbol_canonical": row.get("symbol_canonical"),
                        "date": row.get("date"),
                        "event_time": row.get("date"),
                        "available_time": row.get("available_time") or available_time or _utc_now(),
                        "api_endpoint": api_endpoint,
                        "cadence": endpoint.get("cadence"),
                        "tier_scope": list(endpoint.get("tier_scope") or []),
                        "universe_tier": tier,
                        "normalized_row": dict(row),
                        "raw_row": dict(row.get("raw_row") or {}),
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "official_reference",
                        "schema_hash": _DATASET_SCHEMA_HASHES[dataset],
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def normalized_rows_from_payload(
        self,
        dataset: str,
        venue: str,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        source_dataset: str,
        api_endpoint: str,
        trade_date: str | None = None,
        available_time: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        canonical = _canonical_venue(venue)
        rows = _rows_from_payload(payload)
        if dataset == "tw_price_daily":
            return tuple(
                row
                for row in (
                    self._normalize_price_row(canonical, raw, source_dataset, api_endpoint, available_time)
                    for raw in rows
                )
                if row is not None
            )
        if dataset == "tw_institutional_flow":
            return tuple(
                row
                for row in (
                    self._normalize_institutional_row(canonical, raw, source_dataset, api_endpoint, trade_date, available_time)
                    for raw in rows
                )
                if row is not None
            )
        if dataset == "tw_margin_short_balance":
            return tuple(
                row
                for row in (
                    self._normalize_margin_row(canonical, raw, source_dataset, api_endpoint, trade_date, available_time)
                    for raw in rows
                )
                if row is not None
            )
        if dataset == "tw_securities_lending":
            return tuple(
                row
                for row in (
                    self._normalize_lending_row(canonical, raw, source_dataset, api_endpoint, trade_date, available_time)
                    for raw in rows
                )
                if row is not None
            )
        if dataset == "tw_day_trading":
            return tuple(
                row
                for row in (
                    self._normalize_day_trading_row(canonical, raw, source_dataset, api_endpoint, trade_date, available_time)
                    for raw in rows
                )
                if row is not None
            )
        raise ValueError(f"unsupported Taiwan official dataset: {dataset}")

    def source_health_from_result(self, result: Any) -> SourceHealth:
        watermark = getattr(result, "watermark", None)
        run = getattr(result, "run", None)
        status = getattr(run, "status", "failed")
        run_status = status.value if hasattr(status, "value") else str(status)
        finished_at = getattr(run, "finished_at", None)
        finished = finished_at.isoformat().replace("+00:00", "Z") if hasattr(finished_at, "isoformat") else finished_at
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status="ok" if run_status == "completed" else "failed",
            last_success_at=finished if run_status == "completed" else None,
            last_failure_at=finished if run_status != "completed" else None,
            latest_watermark=getattr(watermark, "value", None),
            row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
            rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
            schema_hash=TW_OFFICIAL_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "source_type": getattr(getattr(run, "source_type", None), "value", "market"),
            },
        )

    def _normalize_price_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        symbol = _text(_first(row, "Code", "SecuritiesCompanyCode", "證券代號", "股票代號")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date"))
        if not symbol or not date:
            return None
        return {
            "dataset": "tw_price_daily",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "Name", "CompanyName", "證券名稱", "股票名稱")),
            "open": _float(_first(row, "OpeningPrice", "Open", "開盤價")),
            "high": _float(_first(row, "HighestPrice", "High", "最高價")),
            "low": _float(_first(row, "LowestPrice", "Low", "最低價")),
            "close": _float(_first(row, "ClosingPrice", "Close", "收盤價")),
            "change": _float(_first(row, "Change", "漲跌價差", "漲跌")),
            "volume": _int(_first(row, "TradeVolume", "TradingShares", "成交股數")),
            "value": _int(_first(row, "TradeValue", "TransactionAmount", "成交金額")),
            "transactions": _int(_first(row, "Transaction", "TransactionNumber", "成交筆數")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or date,
            "raw_row": dict(row),
        }

    def _normalize_institutional_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        trade_date: str | None,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        symbol = _text(_first(row, "證券代號", "SecuritiesCompanyCode", "Code", "股票代號")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date") or trade_date)
        if not symbol or not date:
            return None
        foreign_buy = _int(_first(row, "外陸資買進股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy"))
        foreign_sell = _int(_first(row, "外陸資賣出股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell"))
        foreign_net = _int(_first(row, "外陸資買賣超股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-Difference", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"))
        trust_buy = _int(_first(row, "投信買進股數", "SecuritiesInvestmentTrustCompanies-TotalBuy"))
        trust_sell = _int(_first(row, "投信賣出股數", "SecuritiesInvestmentTrustCompanies-TotalSell"))
        trust_net = _int(_first(row, "投信買賣超股數", "SecuritiesInvestmentTrustCompanies-Difference"))
        dealer_buy = _int(_first(row, "自營商買進股數(自行買賣)", "Dealers-TotalBuy", "自營商買進股數"))
        dealer_sell = _int(_first(row, "自營商賣出股數(自行買賣)", "Dealers-TotalSell", "Dealers -TotalSell", "自營商賣出股數"))
        dealer_net = _int(_first(row, "自營商買賣超股數", "Dealers-Difference"))
        return {
            "dataset": "tw_institutional_flow",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "證券名稱", "CompanyName", "Name", "股票名稱")),
            "foreign_buy": foreign_buy,
            "foreign_sell": foreign_sell,
            "foreign_net": foreign_net,
            "investment_trust_buy": trust_buy,
            "investment_trust_sell": trust_sell,
            "investment_trust_net": trust_net,
            "dealer_buy": dealer_buy,
            "dealer_sell": dealer_sell,
            "dealer_net": dealer_net,
            "total_net": _int(_first(row, "三大法人買賣超股數", "TotalDifference")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or date,
            "raw_row": dict(row),
        }

    def _normalize_margin_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        trade_date: str | None,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        symbol = _text(_first(row, "股票代號", "SecuritiesCompanyCode", "Code", "證券代號")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date") or trade_date)
        if not symbol:
            return None
        return {
            "dataset": "tw_margin_short_balance",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "股票名稱", "CompanyName", "Name", "證券名稱")),
            "margin_buy": _int(_first(row, "融資買進", "MarginPurchase")),
            "margin_sell": _int(_first(row, "融資賣出", "MarginSales")),
            "margin_cash_redemption": _int(_first(row, "融資現金償還", "CashRedemption")),
            "margin_previous_balance": _int(_first(row, "融資前日餘額", "MarginPurchaseBalancePreviousDay")),
            "margin_balance": _int(_first(row, "融資今日餘額", "MarginPurchaseBalance")),
            "margin_quota": _int(_first(row, "融資限額", "MarginPurchaseQuota")),
            "short_buy": _int(_first(row, "融券買進", "ShortConvering")),
            "short_sell": _int(_first(row, "融券賣出", "ShortSale")),
            "short_stock_redemption": _int(_first(row, "融券現券償還", "StockRedemption")),
            "short_previous_balance": _int(_first(row, "融券前日餘額", "ShortSaleBalancePreviousDay")),
            "short_balance": _int(_first(row, "融券今日餘額", "ShortSaleBalance")),
            "short_quota": _int(_first(row, "融券限額", "ShortSaleQuota")),
            "offsetting": _int(_first(row, "資券互抵", "Offsetting")),
            "note": _text(_first(row, "註記", "Note")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or date,
            "raw_row": dict(row),
        }

    def _normalize_lending_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        trade_date: str | None,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        if source_dataset == "TWT96U":
            symbol_key = "TWSECode" if venue == "TWSE" else "GRETAICode"
            volume_key = "TWSEAvailableVolume" if venue == "TWSE" else "GRETAIAvailableVolume"
            symbol = _text(_first(row, symbol_key)).upper()
            available_volume = _int(_first(row, volume_key))
        else:
            symbol = _text(_first(row, "SecuritiesCompanyCode", "證券代號", "Code")).upper()
            available_volume = _int(_first(row, "AvailableVolumesForSBLShortSale"))
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date") or trade_date)
        if not symbol:
            return None
        return {
            "dataset": "tw_securities_lending",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "CompanyName", "Name", "證券名稱")),
            "sbl_short_available_volume": available_volume,
            "securities_borrowing_balance_previous_day": _int(_first(row, "SecuritiesBorrowingBalancePreviousDay")),
            "securities_borrowing_sale": _int(_first(row, "SecuritiesBorrowingSale")),
            "securities_borrowing_return": _int(_first(row, "SecuritiesBorrowingReturn")),
            "securities_borrowing_balance": _int(_first(row, "SecuritiesBorrowingBalanceOfTheMarketDay")),
            "sale_balance": _int(_first(row, "SaleBalanceOfTheMarketDay")),
            "note": _text(_first(row, "Note", "註記")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or date or _utc_now(),
            "raw_row": dict(row),
        }

    def _normalize_day_trading_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        trade_date: str | None,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        symbol = _text(_first(row, "Code", "證券代號", "SecuritiesCompanyCode")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date") or trade_date)
        suspension = _text(_first(row, "Suspension", "暫停現股賣出後現款買進當沖註記", "Note"))
        if not symbol:
            return None
        return {
            "dataset": "tw_day_trading",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "Name", "證券名稱", "CompanyName")),
            "short_then_buy_suspended": bool(suspension),
            "suspension_note": suspension,
            "day_trading_volume": _int(_first(row, "DayTradingVolume")),
            "day_trading_volume_market_pct": _float(_first(row, "DayTradingVolumeOfTheMarket")),
            "day_trading_buy_value": _int(_first(row, "DayTradingValueOfBuys")),
            "day_trading_sell_value": _int(_first(row, "DayTradingValueOfSells")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or date or _utc_now(),
            "raw_row": dict(row),
        }


@dataclass(frozen=True)
class TdccShareholdingDistributionAdapter(SourceConnectorProvider):
    """TDCC official weekly shareholding distribution adapter (SD-SRCM-05 §7.2)."""

    connector_id: str = TDCC_SHAREHOLDING_CONNECTOR_ID
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="TDCC",
            license_scope="official_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="official_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/tdcc-official-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=20,
                burst=2,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/tdcc-official-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="TDCC official weekly shareholding distribution",
                homepage_url="https://www.tdcc.com.tw/",
                docs_url="https://openapi.tdcc.com.tw/",
                owner="Taiwan Depository & Clearing Corporation (TDCC)",
                tags=("taiwan", "tdcc", "official_reference", "shareholding", "chip"),
            ),
            metadata={
                "source_class": "taiwan_chip",
                "official_reference_truth": True,
                "dataset_schema_hash": TDCC_SHAREHOLDING_SCHEMA_HASH,
                "normalized_datasets": ["tdcc_shareholding_distribution"],
                "tier_policy": {
                    "core_universe": ["tdcc_shareholding_distribution"],
                    "candidate_universe": ["tdcc_shareholding_distribution"],
                },
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "TdccShareholdingDistributionAdapter.records_from_payload",
            "adapter_config": {
                "max_records": self.max_records,
            },
            "request": {
                "dataset": "tdcc_shareholding_distribution",
                "symbols": ["2330"],
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        *,
        source_dataset: str = "TDCC_OD_1-5",
        timeout_seconds: float = 20.0,
    ) -> Any:
        """Fetch live open data payload from TDCC OpenAPI endpoint."""
        url = f"{TDCC_OPENAPI_BASE_URL}/opendata/{source_dataset}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        with open_external_url(
            request,
            caller="source_ingest.tdcc_shareholding",
            timeout=timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def generate_backfill_weeks(start_date: str, end_date: str) -> list[str]:
        """Generate weekly publication date windows between start_date and end_date."""
        from datetime import date, timedelta
        d_start = date.fromisoformat(start_date)
        d_end = date.fromisoformat(end_date)
        weeks: list[str] = []
        curr = d_start
        while curr <= d_end:
            # Friday is weekday 4
            if curr.weekday() == 4:
                weeks.append(curr.isoformat())
            curr += timedelta(days=1)
        return weeks

    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        source_dataset: str = "TDCC_OD_1-5",
        api_endpoint: str | None = None,
        trade_date: str | None = None,
        available_time: str | None = None,
        universe_tier: str = "core_universe",
        is_correction: bool = False,
        correction_reason: str = "",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        normalized_rows = self.normalized_rows_from_payload(
            payload,
            source_dataset=source_dataset,
            api_endpoint=api_endpoint or f"{TDCC_OPENAPI_BASE_URL}/opendata/{source_dataset}",
            trade_date=trade_date,
            available_time=available_time,
            is_correction=is_correction,
            correction_reason=correction_reason,
        )
        records: list[SourceRecord] = []
        for row in normalized_rows[: self.max_records]:
            row_hash = _stable_hash({"dataset": "tdcc_shareholding_distribution", "row": row})
            symbol = _text(row.get("symbol"), "market")
            as_of_date = _text(row.get("date"), row_hash)
            level = str(row.get("holder_level", "0"))
            content_ref = f"tw-tdcc://shareholding/{row['venue']}/{symbol}/{as_of_date}/{level}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"tw-tdcc:shareholding:{row['venue']}:{symbol}:{level}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TDCC shareholding {row['venue']} {symbol} level {level} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "taiwan_chip",
                        "provider": "TDCC",
                        "dataset": "tdcc_shareholding_distribution",
                        "source_dataset": source_dataset,
                        "venue": row["venue"],
                        "symbol": row.get("symbol"),
                        "symbol_canonical": row.get("symbol_canonical"),
                        "date": row.get("date"),
                        "event_time": row.get("date"),
                        "available_time": row.get("available_time") or available_time or _utc_now(),
                        "api_endpoint": api_endpoint or f"{TDCC_OPENAPI_BASE_URL}/opendata/{source_dataset}",
                        "cadence": "weekly_after_tdcc_publication",
                        "universe_tier": _tier_name(universe_tier),
                        "is_correction": row.get("is_correction", False),
                        "correction_reason": row.get("correction_reason", ""),
                        "normalized_row": dict(row),
                        "raw_row": dict(row.get("raw_row") or {}),
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "official_reference",
                        "schema_hash": TDCC_SHAREHOLDING_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def normalized_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        source_dataset: str = "TDCC_OD_1-5",
        api_endpoint: str = "",
        trade_date: str | None = None,
        available_time: str | None = None,
        is_correction: bool = False,
        correction_reason: str = "",
    ) -> tuple[dict[str, Any], ...]:
        rows = _rows_from_payload(payload)
        results: list[dict[str, Any]] = []
        for raw in rows:
            symbol = _text(_first(raw, "證券代號", "Code", "SecuritiesCompanyCode", "Symbol", "股票代號")).upper()
            date = _roc_date_to_iso(_first(raw, "資料日期", "Date", "date", "PublicationDate") or trade_date)
            if not symbol or not date:
                continue

            level = _int(_first(raw, "持股分級", "HoldLevel", "level", "HolderLevel")) or 0
            holding_range = _text(_first(raw, "持股分級說明", "HoldingRange", "range", "LevelDescription"))
            people_count = _int(_first(raw, "人數", "PeopleCount", "people_count", "NumberOfHolders")) or 0
            shares = _int(_first(raw, "股數", "Shares", "shares", "NumberOfShares")) or 0
            percentage = _float(_first(raw, "占集保庫存數比例%", "Percentage", "percentage", "Ratio", "Percent")) or 0.0
            venue = _text(_first(raw, "venue", "Venue"), "TWSE")
            row_is_correction = bool(is_correction or _first(raw, "is_correction", "is_restated", "更正註記"))

            results.append({
                "dataset": "tdcc_shareholding_distribution",
                "date": date,
                "symbol": symbol,
                "symbol_canonical": _symbol_canonical(symbol, venue),
                "market": "TW",
                "venue": venue,
                "holder_level": level,
                "holding_range": holding_range,
                "people_count": people_count,
                "shares": shares,
                "percentage": percentage,
                "source_dataset": source_dataset,
                "api_endpoint": api_endpoint,
                "available_time": available_time or f"{date}T19:00:00Z",
                "is_correction": row_is_correction,
                "correction_reason": correction_reason or ("TDCC weekly correction" if row_is_correction else ""),
                "raw_row": dict(raw),
            })
        return tuple(results)

    def source_health_from_result(self, result: Any) -> SourceHealth:
        watermark = getattr(result, "watermark", None)
        run = getattr(result, "run", None)
        status = getattr(run, "status", "failed")
        run_status = status.value if hasattr(status, "value") else str(status)
        finished_at = getattr(run, "finished_at", None)
        finished = finished_at.isoformat().replace("+00:00", "Z") if hasattr(finished_at, "isoformat") else finished_at
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status="ok" if run_status == "completed" else "failed",
            last_success_at=finished if run_status == "completed" else None,
            last_failure_at=finished if run_status != "completed" else None,
            latest_watermark=getattr(watermark, "value", None),
            row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
            rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
            schema_hash=TDCC_SHAREHOLDING_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "source_type": "market",
            },
        )


@dataclass(frozen=True)
class TaifexDerivativesChipAdapter(SourceConnectorProvider):
    """TAIFEX official daily futures and options chip-context adapter (SD-SRCM-05 §7.3)."""

    connector_id: str = TAIFEX_DERIVATIVES_CONNECTOR_ID
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="TAIFEX",
            license_scope="official_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="official_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/taifex-official-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/taifex-official-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="TAIFEX official futures and options chip context",
                homepage_url="https://www.taifex.com.tw/",
                docs_url="https://openapi.taifex.com.tw/",
                owner="Taiwan Futures Exchange (TAIFEX)",
                tags=("taiwan", "taifex", "official_reference", "derivatives", "futures", "options", "chip"),
            ),
            metadata={
                "source_class": "taiwan_chip",
                "official_reference_truth": True,
                "dataset_schema_hash": TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
                "normalized_datasets": ["taifex_futures_chip", "taifex_options_chip"],
                "tier_policy": {
                    "core_universe": ["taifex_futures_chip", "taifex_options_chip"],
                    "candidate_universe": ["taifex_futures_chip", "taifex_options_chip"],
                },
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "adapter_config": {
                "max_records": self.max_records,
            },
            "request": {
                "dataset": "taifex_futures_chip",
                "contracts": ["TX", "MTX"],
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        dataset: str = "taifex_futures_chip",
        *,
        timeout_seconds: float = 20.0,
    ) -> Any:
        """Fetch live open data payload from TAIFEX OpenAPI endpoint."""
        source_name = "Daily_Institutional_Trading_Options" if dataset == "taifex_options_chip" else "Daily_Institutional_Trading_Futures"
        url = f"{TAIFEX_OPENAPI_BASE_URL}/{source_name}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        with open_external_url(
            request,
            caller="source_ingest.taifex_derivatives",
            timeout=timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def is_contract_roll_day(trade_date: str) -> bool:
        """Check if trade_date (YYYY-MM-DD) is the 3rd Wednesday of the month (TAIFEX settlement/roll day)."""
        from datetime import date
        d = date.fromisoformat(trade_date)
        if d.weekday() != 2:  # Wednesday is 2
            return False
        first_day = date(d.year, d.month, 1)
        offset = (2 - first_day.weekday()) % 7
        first_wednesday = 1 + offset
        third_wednesday = first_wednesday + 14
        return d.day == third_wednesday

    @staticmethod
    def resolve_front_month_contract(trade_date: str, contract_prefix: str = "TX") -> str:
        """Resolve active front month contract code based on settlement calendar."""
        from datetime import date
        d = date.fromisoformat(trade_date)
        first_day = date(d.year, d.month, 1)
        offset = (2 - first_day.weekday()) % 7
        third_wednesday = 1 + offset + 14
        if d.day <= third_wednesday:
            return f"{contract_prefix}{d.year}{d.month:02d}"
        next_month = 1 if d.month == 12 else d.month + 1
        next_year = d.year + 1 if d.month == 12 else d.year
        return f"{contract_prefix}{next_year}{next_month:02d}"

    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        dataset: str = "taifex_futures_chip",
        source_dataset: str | None = None,
        api_endpoint: str | None = None,
        trade_date: str | None = None,
        available_time: str | None = None,
        universe_tier: str = "core_universe",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        normalized_rows = self.normalized_rows_from_payload(
            payload,
            dataset=dataset,
            source_dataset=source_dataset or dataset,
            api_endpoint=api_endpoint or f"{TAIFEX_OPENAPI_BASE_URL}/{dataset}",
            trade_date=trade_date,
            available_time=available_time,
        )
        records: list[SourceRecord] = []
        schema_hash = TAIFEX_OPTIONS_CHIP_SCHEMA_HASH if dataset == "taifex_options_chip" else TAIFEX_FUTURES_CHIP_SCHEMA_HASH
        for row in normalized_rows[: self.max_records]:
            row_hash = _stable_hash({"dataset": dataset, "row": row})
            contract = _text(row.get("contract"), "MARKET")
            group = _text(row.get("participant_group"), "ALL")
            as_of_date = _text(row.get("date"), row_hash)
            content_ref = f"tw-taifex://{dataset}/{contract}/{as_of_date}/{group}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"tw-taifex:{dataset}:{contract}:{group}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TAIFEX {dataset} {contract} {group} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "taiwan_chip",
                        "provider": "TAIFEX",
                        "dataset": dataset,
                        "source_dataset": source_dataset or dataset,
                        "venue": "TAIFEX",
                        "contract": contract,
                        "participant_group": group,
                        "date": row.get("date"),
                        "event_time": row.get("date"),
                        "available_time": row.get("available_time") or available_time or _utc_now(),
                        "api_endpoint": api_endpoint or f"{TAIFEX_OPENAPI_BASE_URL}/{dataset}",
                        "cadence": "daily_after_taifex_publication",
                        "universe_tier": _tier_name(universe_tier),
                        "contract_roll_day": row.get("contract_roll_day", False),
                        "front_month_contract": row.get("front_month_contract"),
                        "normalized_row": dict(row),
                        "raw_row": dict(row.get("raw_row") or {}),
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "official_reference",
                        "schema_hash": schema_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def normalized_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        dataset: str = "taifex_futures_chip",
        source_dataset: str = "taifex_futures_chip",
        api_endpoint: str = "",
        trade_date: str | None = None,
        available_time: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        rows = _rows_from_payload(payload)
        results: list[dict[str, Any]] = []
        for raw in rows:
            date = _roc_date_to_iso(_first(raw, "Date", "日期", "date", "TradingDate") or trade_date)
            if not date:
                continue

            roll_day = self.is_contract_roll_day(date) if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else False
            front_month = self.resolve_front_month_contract(date) if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else ""

            if dataset == "taifex_options_chip":
                call_volume = _int(_first(raw, "買權成交量", "CallVolume", "call_volume")) or 0
                put_volume = _int(_first(raw, "賣權成交量", "PutVolume", "put_volume")) or 0
                call_oi = _int(_first(raw, "買權未平倉量", "CallOpenInterest", "call_open_interest")) or 0
                put_oi = _int(_first(raw, "賣權未平倉量", "PutOpenInterest", "put_open_interest")) or 0
                pc_ratio = _float(_first(raw, "買賣權未平倉量比率%", "PutCallRatio", "put_call_ratio", "Ratio"))
                if pc_ratio is None and call_oi > 0:
                    pc_ratio = round((put_oi / call_oi) * 100.0, 2)

                results.append({
                    "dataset": "taifex_options_chip",
                    "date": date,
                    "contract": _text(_first(raw, "Contract", "契約名稱", "商品代號"), "TXO"),
                    "market": "TW",
                    "venue": "TAIFEX",
                    "call_volume": call_volume,
                    "put_volume": put_volume,
                    "call_open_interest": call_oi,
                    "put_open_interest": put_oi,
                    "put_call_ratio": pc_ratio or 0.0,
                    "contract_roll_day": roll_day,
                    "front_month_contract": front_month,
                    "source_dataset": source_dataset,
                    "api_endpoint": api_endpoint,
                    "available_time": available_time or f"{date}T16:30:00Z",
                    "raw_row": dict(raw),
                })
            else:
                contract = _text(_first(raw, "Contract", "契約名稱", "商品代號", "contract"), "TX")
                participant = _text(_first(raw, "身分別", "身份別", "ParticipantGroup", "participant_group", "Identity"), "foreign_investors")
                long_vol = _int(_first(raw, "多方交易口數", "LongVolume", "buy_volume", "買進口數")) or 0
                short_vol = _int(_first(raw, "空方交易口數", "ShortVolume", "sell_volume", "賣出口數")) or 0
                net_vol = _int(_first(raw, "多空交易口數淨額", "NetVolume", "net_volume", "買賣超口數"))
                if net_vol is None:
                    net_vol = long_vol - short_vol

                long_oi = _int(_first(raw, "多方未平倉口數", "LongOpenInterest", "long_oi")) or 0
                short_oi = _int(_first(raw, "空方未平倉口數", "ShortOpenInterest", "short_oi")) or 0
                net_oi = _int(_first(raw, "多空未平倉口數淨額", "NetOpenInterest", "net_oi"))
                if net_oi is None:
                    net_oi = long_oi - short_oi

                results.append({
                    "dataset": "taifex_futures_chip",
                    "date": date,
                    "contract": contract,
                    "market": "TW",
                    "venue": "TAIFEX",
                    "participant_group": participant,
                    "long_volume": long_vol,
                    "short_volume": short_vol,
                    "net_volume": net_vol,
                    "long_open_interest": long_oi,
                    "short_open_interest": short_oi,
                    "net_open_interest": net_oi,
                    "contract_roll_day": roll_day,
                    "front_month_contract": front_month,
                    "source_dataset": source_dataset,
                    "api_endpoint": api_endpoint,
                    "available_time": available_time or f"{date}T16:30:00Z",
                    "raw_row": dict(raw),
                })
        return tuple(results)

    def source_health_from_result(self, result: Any) -> SourceHealth:
        watermark = getattr(result, "watermark", None)
        run = getattr(result, "run", None)
        status = getattr(run, "status", "failed")
        run_status = status.value if hasattr(status, "value") else str(status)
        finished_at = getattr(run, "finished_at", None)
        finished = finished_at.isoformat().replace("+00:00", "Z") if hasattr(finished_at, "isoformat") else finished_at
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status="ok" if run_status == "completed" else "failed",
            last_success_at=finished if run_status == "completed" else None,
            last_failure_at=finished if run_status != "completed" else None,
            latest_watermark=getattr(watermark, "value", None),
            row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
            rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
            schema_hash=TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "ingest_run_id": getattr(run, "ingest_run_id", None),
                "source_type": "market",
            },
        )
