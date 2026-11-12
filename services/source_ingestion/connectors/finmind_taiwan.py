"""FinMind Taiwan market-data adapters.

FinMind is modeled as the low-cost paid Taiwan research layer. Official
exchange and disclosure sources still own official-reference truth; FinMind
provides normalized API and bulk backfill surfaces for research and feature
generation.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date as _date_cls, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from services.external_egress import open_external_url

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceMetadata,
    SourceEvidenceError,
    SourceRecord,
)


FINMIND_BASE_URL = "https://api.finmindtrade.com/api/v4"
FINMIND_DATA_ENDPOINT = "/data"
FINMIND_BROKER_DAILY_REPORT_ENDPOINT = "/taiwan_stock_trading_daily_report"
FINMIND_STORAGE_OBJECTS_ENDPOINT = "/storage_objects"
FINMIND_BROKER_REPORT_DATASET = "TaiwanStockTradingDailyReport"
FINMIND_BROKER_REPORT_HISTORY_START = "2021-06-30"
BROKER_TOP_SCHEMA_HASH = "tw_broker_top.v1"
FINMIND_DATASET_SCHEMA_HASH = "finmind_taiwan_dataset.v1"
FINMIND_STORAGE_OBJECT_SCHEMA_HASH = "finmind_storage_object_manifest.v1"
BROKER_BRANCH_DETAIL_DATASET = "tw_broker_branch_detail"
BROKER_BRANCH_DETAIL_SCHEMA_HASH = "tw_broker_branch_detail.v1"
# Taiwan daily OHLCV truth is TWSE/TPEx official; FinMind price rows are a
# research reference only and must never land in normalized/tw_price_daily.
FINMIND_PRICE_REFERENCE_DATASET = "finmind_tw_price_reference"

# SD_INTRADAY_MINUTE_BARS §3: KBar is a post-close (~15:50 Asia/Taipei) 1-minute
# feed written ONLY to tw_price_1m, never to tw_price_daily (official truth).
FINMIND_KBAR_DATASET = "TaiwanStockKBar"
FINMIND_KBAR_NORMALIZED_DATASET = "tw_price_1m"
FINMIND_KBAR_SCHEMA_HASH = "tw_price_1m.v1"
FINMIND_KBAR_DOCUMENTED_PUBLICATION_LOCAL = "15:50"
FINMIND_KBAR_HOURLY_SELF_LIMIT = 4500  # 6,000/h SponsorYear minus 25 % headroom
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
KBAR_SESSION_OPEN = (9, 0)
KBAR_SESSION_LAST_BAR_START = (13, 30)  # inclusive; may be the closing-auction bar

FINMIND_TAIWAN_DATASETS: tuple[dict[str, Any], ...] = (
    {
        "dataset": "TaiwanStockPrice",
        "normalized_dataset": FINMIND_PRICE_REFERENCE_DATASET,
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "1994-10-01",
        "cadence": "daily_after_close",
    },
    {
        "dataset": "TaiwanStockDayTrading",
        "normalized_dataset": "tw_day_trading",
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "2014-01-01",
        "cadence": "daily_after_close",
    },
    {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "normalized_dataset": "tw_institutional_flow",
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "2005-01-01",
        "cadence": "daily_after_close",
    },
    {
        "dataset": "TaiwanStockMarginPurchaseShortSale",
        "normalized_dataset": "tw_margin_short_balance",
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "2001-01-01",
        "cadence": "daily_after_close",
    },
    {
        "dataset": "TaiwanStockSecuritiesLending",
        "normalized_dataset": "tw_securities_lending",
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "2001-05-01",
        "cadence": "daily_after_close",
    },
    {
        "dataset": "TaiwanStockShareholding",
        "normalized_dataset": "tw_shareholding",
        "minimum_tier": "free_by_symbol_backer_all_market",
        "history_start": "2004-02-01",
        "cadence": "weekly",
    },
    {
        "dataset": "TaiwanStockNews",
        "normalized_dataset": "tw_news_metadata",
        "minimum_tier": "free_or_paid_by_current_entitlement",
        "history_start": None,
        "cadence": "10m_to_30m",
    },
    {
        "dataset": FINMIND_BROKER_REPORT_DATASET,
        "normalized_dataset": "tw_broker_top",
        "minimum_tier": "sponsor",
        "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
        "cadence": "daily_after_close",
    },
    {
        "dataset": FINMIND_KBAR_DATASET,
        "normalized_dataset": FINMIND_KBAR_NORMALIZED_DATASET,
        "minimum_tier": "sponsor",
        "history_start": None,  # real availability is probed (evidence), not assumed
        "cadence": "daily_after_close",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _int(value: Any) -> int:
    if value in (None, "", "-", "--"):
        return 0
    return int(float(str(value).replace(",", "").strip()))


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _rows_from_payload(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, list):
            return tuple(dict(row) for row in data if isinstance(row, Mapping))
        if isinstance(data, Mapping):
            nested = data.get("data") or data.get("results") or data.get("items")
            if isinstance(nested, list):
                return tuple(dict(row) for row in nested if isinstance(row, Mapping))
            return (dict(data),)
        for key in ("results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return tuple(dict(row) for row in value if isinstance(row, Mapping))
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(dict(row) for row in payload if isinstance(row, Mapping))
    return tuple()


def _finmind_url(endpoint: str, params: Mapping[str, Any] | None = None) -> str:
    base = f"{FINMIND_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    if not params:
        return base
    query = urlencode(params)
    return f"{base}?{query}"


@dataclass(frozen=True)
class FinMindTaiwanDatasetAdapter(SourceConnectorProvider):
    """Generic FinMind /data adapter for Taiwan price, chip, holding, and news datasets."""

    connector_id: str = "tw-finmind-datasets"
    secret_ref_id: str = "env://FINMIND_API_TOKEN"
    max_records: int = 100
    entitlement_tier: str = "sponsor"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="FinMind",
            license_scope="vendor_research",
            auth_type=AuthType.API_KEY,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref={"secret_ref_id": self.secret_ref_id},
                auth_scope=("finmind:read", "source_ingest:read"),
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_research",
                allowed_use=("research", "backtest", "search_index", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/finmind-vendor-research",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=100,
                burst=10,
                retry_after_seconds=60,
                concurrency=2,
                policy_ref=f"source-ingest://policy/finmind-{self.entitlement_tier}-api",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FinMind Taiwan datasets",
                homepage_url="https://finmindtrade.com/",
                docs_url="https://finmind.github.io/",
                owner="FinMind",
                tags=("taiwan", "finmind", "research_grade", "market_data", "chip"),
            ),
            metadata={
                "source_class": "research_grade",
                "source_plan": "finmind_first_low_cost_paid_layer",
                "does_not_replace_official_reference_truth": True,
                "primary_for": [
                    "tw_institutional_flow",
                    "tw_margin_short_balance",
                    "tw_securities_lending",
                    "tw_shareholding",
                    "tw_news_metadata",
                ],
                "entitlement_tier": self.entitlement_tier,
                "dataset_catalog": [dict(item) for item in FINMIND_TAIWAN_DATASETS],
                "active_universe_tiers": ["core_universe", "candidate_universe"],
                "archive_behavior": "price_baseline_only_elsewhere",
                "raw_storage_policy": {
                    "compression": "gzip",
                    "retention_days": 2555,
                    "retention_policy_ref": "market-data://raw-retention/tw-finmind-7y",
                    "dataset_overrides": {
                        "TaiwanStockNews": {
                            "retention_days": 730,
                            "retention_policy_ref": "market-data://raw-retention/tw-news-metadata-2y",
                        }
                    },
                },
                "schema_hash": FINMIND_DATASET_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "next_watermark": None,
            "adapter": "FinMindTaiwanDatasetAdapter.records_from_data_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
                "max_records": self.max_records,
                "entitlement_tier": self.entitlement_tier,
            },
            "request": {},
            "base_url": FINMIND_BASE_URL,
            "endpoint": FINMIND_DATA_ENDPOINT,
            "secret_ref_id": self.secret_ref_id,
            "datasets": [dict(item) for item in FINMIND_TAIWAN_DATASETS],
            "max_records": self.max_records,
        }

    def records_from_data_payload(
        self,
        dataset: str,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        api_endpoint: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        records: list[SourceRecord] = []
        endpoint = api_endpoint or _finmind_url(FINMIND_DATA_ENDPOINT, {"dataset": dataset})
        for row in _rows_from_payload(payload)[: self.max_records]:
            symbol = _text(_first(row, "stock_id", "data_id", "coid", "symbol", "stock_code"))
            as_of_date = _text(_first(row, "date", "mdate", "資料日", "published_at"))
            row_hash = _stable_hash({"dataset": dataset, "row": row})
            content_ref = f"finmind://data/{dataset}/{symbol or 'market'}/{as_of_date or row_hash}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"finmind:{dataset}:{symbol or 'market'}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"FinMind {dataset} {symbol} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "research_grade",
                        "source_plan": "finmind_first_low_cost_paid_layer",
                        "provider": "FinMind",
                        "dataset": dataset,
                        "source_dataset": dataset,
                        "symbol": symbol or None,
                        "as_of_date": as_of_date or None,
                        "event_time": as_of_date or None,
                        "available_time": as_of_date or _utc_now(),
                        "api_endpoint": endpoint,
                        "raw_row": dict(row),
                        "normalized_target": (
                            FINMIND_PRICE_REFERENCE_DATASET if dataset == "TaiwanStockPrice" else None
                        ),
                        "redistribution_allowed": False,
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                        "entitlement_tier": self.entitlement_tier,
                        "schema_hash": FINMIND_DATASET_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)


@dataclass(frozen=True)
class FinMindTaiwanBrokerDailyReportAdapter(SourceConnectorProvider):
    """FinMind Sponsor branch-trading report normalized into top broker rows."""

    connector_id: str = "tw-finmind-broker-daily-report"
    secret_ref_id: str = "env://FINMIND_API_TOKEN"
    max_rank: int = 20
    entitlement_tier: str = "sponsor"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="FinMind",
            license_scope="vendor_research",
            auth_type=AuthType.API_KEY,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref={"secret_ref_id": self.secret_ref_id},
                auth_scope=("finmind:read", "source_ingest:read"),
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_research",
                allowed_use=("research", "backtest", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/finmind-vendor-research",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=100,
                burst=10,
                retry_after_seconds=60,
                concurrency=2,
                policy_ref="source-ingest://policy/finmind-sponsor-api",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FinMind Taiwan broker trading daily report",
                homepage_url="https://finmindtrade.com/",
                docs_url="https://finmind.github.io/tutor/TaiwanMarket/Chip/",
                owner="FinMind",
                tags=("taiwan", "finmind", "broker_top", "branch_trading", "chip"),
            ),
            metadata={
                "source_class": "research_grade",
                "source_plan": "finmind_first_low_cost_paid_layer",
                "dataset": "tw_broker_top",
                "source_dataset": FINMIND_BROKER_REPORT_DATASET,
                "source_profile": "broker_top20_from_full_daily_report",
                "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
                "history_depth": f"{FINMIND_BROKER_REPORT_HISTORY_START}_to_now",
                "update_cadence": "daily_after_close_21_00_taipei",
                "completeness": "top20_buy_sell_aggregated_from_full_branch_daily_report",
                "entitlement_tier": self.entitlement_tier,
                "active_universe_tiers": ["core_universe", "candidate_universe"],
                "archive_behavior": "skip",
                "max_rank_policy": {
                    "default_max_rank": self.max_rank,
                    "stored_rank_scope": "top_buy_and_top_sell_only",
                    "full_branch_storage_allowed_by_default": False,
                },
                "raw_storage_policy": {
                    "compression": "gzip",
                    "retention_days": 2555,
                    "retention_policy_ref": "market-data://raw-retention/tw-broker-top-7y",
                },
                "expected_rows_per_symbol": self.max_rank * 2,
                "schema_hash": BROKER_TOP_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "next_watermark": None,
            "adapter": "FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
                "max_rank": self.max_rank,
                "entitlement_tier": self.entitlement_tier,
            },
            "request": {},
            "base_url": FINMIND_BASE_URL,
            "endpoint": FINMIND_BROKER_DAILY_REPORT_ENDPOINT,
            "secret_ref_id": self.secret_ref_id,
            "dataset": FINMIND_BROKER_REPORT_DATASET,
            "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
            "max_rank": self.max_rank,
        }

    def top_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        symbol: str | None = None,
        trade_date: str | None = None,
        source_url: str | None = None,
        available_time: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in _rows_from_payload(payload):
            row_symbol = _text(_first(row, "stock_id", "data_id", "symbol", "stock_code"), symbol or "").upper()
            row_date = _text(_first(row, "date", "mdate"), trade_date or "")
            broker = _text(_first(row, "securities_trader", "broker", "券商"))
            broker_id = _text(_first(row, "securities_trader_id", "broker_id", "券商代號"))
            if not row_symbol or not row_date or not broker:
                continue
            key = (row_date, row_symbol, broker_id, broker)
            bucket = grouped.setdefault(
                key,
                {
                    "date": row_date,
                    "symbol": row_symbol,
                    "source": "FinMind TaiwanStockTradingDailyReport",
                    "broker": broker,
                    "broker_id": broker_id or None,
                    "buy_qty": 0,
                    "sell_qty": 0,
                    "available_time": available_time or _utc_now(),
                    "source_url": source_url
                    or _finmind_url(
                        FINMIND_BROKER_DAILY_REPORT_ENDPOINT,
                        {"data_id": row_symbol, "date": row_date},
                    ),
                    "source_dataset": FINMIND_BROKER_REPORT_DATASET,
                    "raw_rows": [],
                },
            )
            bucket["buy_qty"] += _int(_first(row, "buy", "buy_qty", "買進"))
            bucket["sell_qty"] += _int(_first(row, "sell", "sell_qty", "賣出"))
            bucket["raw_rows"].append(dict(row))

        aggregates = []
        for row in grouped.values():
            row["net_qty"] = int(row["buy_qty"]) - int(row["sell_qty"])
            aggregates.append(row)

        buy_rows = sorted((row for row in aggregates if int(row["net_qty"]) > 0), key=lambda item: (-int(item["net_qty"]), item["broker"]))
        sell_rows = sorted((row for row in aggregates if int(row["net_qty"]) < 0), key=lambda item: (int(item["net_qty"]), item["broker"]))
        result: list[dict[str, Any]] = []
        for side, rows in (("buy", buy_rows[: self.max_rank]), ("sell", sell_rows[: self.max_rank])):
            for rank, row in enumerate(rows, start=1):
                normalized = dict(row)
                normalized["side"] = side
                normalized["rank"] = rank
                result.append(normalized)
        return tuple(result)

    def records_from_daily_report_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        symbol: str | None = None,
        trade_date: str | None = None,
        source_url: str | None = None,
        available_time: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        records: list[SourceRecord] = []
        for row in self.top_rows_from_payload(
            payload,
            symbol=symbol,
            trade_date=trade_date,
            source_url=source_url,
            available_time=available_time,
        ):
            key_payload = {
                "symbol": row["symbol"],
                "date": row["date"],
                "side": row["side"],
                "rank": row["rank"],
                "broker_id": row.get("broker_id"),
                "broker": row["broker"],
            }
            row_hash = _stable_hash(key_payload)
            content_ref = f"finmind://broker-top/{row['symbol']}/{row['date']}/{row['side']}/{row['rank']}"
            records.append(
                SourceRecord(
                    source_id=f"finmind-broker-top:{row['symbol']}:{row['date']}:{row['side']}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=(
                        f"FinMind broker top {row['side']} "
                        f"{row['symbol']} {row['date']} #{row['rank']} {row['broker']}"
                    ),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "research_grade",
                        "source_plan": "finmind_first_low_cost_paid_layer",
                        "provider": "FinMind",
                        "dataset": "tw_broker_top",
                        "source_dataset": FINMIND_BROKER_REPORT_DATASET,
                        "source_profile": "broker_top20_from_full_daily_report",
                        "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
                        "completeness": "top20_buy_sell_aggregated_from_full_branch_daily_report",
                        "symbol": row["symbol"],
                        "trade_date": row["date"],
                        "event_time": row["date"],
                        "available_time": row["available_time"],
                        "side": row["side"],
                        "rank": row["rank"],
                        "broker": row["broker"],
                        "broker_id": row.get("broker_id"),
                        "buy_qty": row["buy_qty"],
                        "sell_qty": row["sell_qty"],
                        "net_qty": row["net_qty"],
                        "source_url": row["source_url"],
                        "raw_rows": [dict(raw) for raw in row.get("raw_rows", [])],
                        "body": json.dumps(row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                        "redistribution_allowed": False,
                        "entitlement_tier": self.entitlement_tier,
                        "active_universe_tiers": ["core_universe", "candidate_universe"],
                        "schema_hash": BROKER_TOP_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def branch_detail_records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        available_time: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        """Emit one governed ``tw_broker_branch_detail`` record per branch/symbol/date."""
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in _rows_from_payload(payload):
            symbol = _text(_first(row, "stock_id", "data_id", "symbol", "stock_code")).upper()
            date = _text(_first(row, "date", "mdate"))
            broker = _text(_first(row, "securities_trader", "broker", "券商"))
            broker_id = _text(_first(row, "securities_trader_id", "broker_id", "券商代號"))
            if not symbol or not date or not (broker or broker_id):
                continue
            bucket = grouped.setdefault(
                (date, symbol, broker_id, broker),
                {"date": date, "symbol": symbol, "broker": broker, "broker_id": broker_id or None,
                 "buy_qty": 0, "sell_qty": 0, "price_levels": 0},
            )
            bucket["buy_qty"] += _int(_first(row, "buy", "buy_qty", "買進"))
            bucket["sell_qty"] += _int(_first(row, "sell", "sell_qty", "賣出"))
            bucket["price_levels"] += 1
        stamp = available_time or _utc_now()
        records: list[SourceRecord] = []
        for (date, symbol, broker_id, broker), row in sorted(grouped.items()):
            row["net_qty"] = row["buy_qty"] - row["sell_qty"]
            row_hash = _stable_hash({"symbol": symbol, "date": date, "broker_id": broker_id, "broker": broker})
            records.append(
                SourceRecord(
                    source_id=f"finmind-broker-branch:{symbol}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"FinMind broker branch {symbol} {date} {broker or broker_id}",
                    content_ref=f"finmind://broker-branch/{symbol}/{date}/{row_hash}",
                    metadata={
                        "source_class": "research_grade",
                        "provider": "FinMind",
                        "dataset": BROKER_BRANCH_DETAIL_DATASET,
                        "source_dataset": FINMIND_BROKER_REPORT_DATASET,
                        "symbol": symbol,
                        "trade_date": date,
                        "event_time": date,
                        "available_time": stamp,
                        **row,
                        "body": json.dumps(row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                        "redistribution_allowed": False,
                        "entitlement_tier": self.entitlement_tier,
                        "schema_hash": BROKER_BRANCH_DETAIL_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)


@dataclass(frozen=True)
class FinMindTaiwanBrokerBulkBackfillAdapter(SourceConnectorProvider):
    """FinMind SponsorPro storage-object manifest adapter for one-time backfill."""

    connector_id: str = "tw-finmind-broker-bulk-parquet"
    secret_ref_id: str = "env://FINMIND_API_TOKEN"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="FinMind",
            license_scope="vendor_research",
            auth_type=AuthType.API_KEY,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref={"secret_ref_id": self.secret_ref_id},
                auth_scope=("finmind:read", "source_ingest:read"),
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_research",
                allowed_use=("research", "backtest", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/finmind-vendor-research",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=300,
                burst=20,
                retry_after_seconds=60,
                concurrency=2,
                policy_ref="source-ingest://policy/finmind-sponsorpro-storage-objects",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FinMind SponsorPro Taiwan broker bulk parquet",
                homepage_url="https://finmindtrade.com/",
                docs_url="https://finmind.github.io/tutor/TaiwanMarket/Chip/",
                owner="FinMind",
                tags=("taiwan", "finmind", "sponsorpro", "broker_backfill", "parquet"),
            ),
            metadata={
                "source_class": "research_grade",
                "source_plan": "finmind_first_low_cost_paid_layer",
                "dataset": FINMIND_BROKER_REPORT_DATASET,
                "normalized_target": "tw_broker_top",
                "source_profile": "sponsorpro_daily_parquet_backfill",
                "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
                "entitlement_tier": "sponsorpro",
                "bulk_download": True,
                "signed_url_redaction_required": True,
                "raw_storage_partition": "raw/finmind/TaiwanStockTradingDailyReport/date=YYYY-MM-DD/",
                "raw_storage_policy": {
                    "compression": "gzip",
                    "retention_days": 2555,
                    "retention_policy_ref": "market-data://raw-retention/tw-broker-bulk-7y",
                },
                "schema_hash": FINMIND_STORAGE_OBJECT_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "next_watermark": None,
            "adapter": "FinMindTaiwanBrokerBulkBackfillAdapter.records_from_storage_objects_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
            },
            "request": {},
            "base_url": FINMIND_BASE_URL,
            "endpoint": FINMIND_STORAGE_OBJECTS_ENDPOINT,
            "secret_ref_id": self.secret_ref_id,
            "dataset": FINMIND_BROKER_REPORT_DATASET,
            "history_start": FINMIND_BROKER_REPORT_HISTORY_START,
        }

    def records_from_storage_objects_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        dataset: str = FINMIND_BROKER_REPORT_DATASET,
        date: str,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        rows = _rows_from_payload(payload)
        if not rows and isinstance(payload, Mapping):
            rows = (dict(payload),)
        records: list[SourceRecord] = []
        for index, row in enumerate(rows, start=1):
            signed_url = _text(_first(row, "url", "signed_url", "download_url"))
            object_name = _text(
                _first(row, "object_name", "object_key", "name", "path"),
                f"{dataset}/{date}/part-{index}.parquet",
            )
            row_hash = _stable_hash({"dataset": dataset, "date": date, "object_name": object_name, "index": index})
            signed_url_hash = _content_hash(signed_url)[:16] if signed_url else None
            records.append(
                SourceRecord(
                    source_id=f"finmind-storage-object:{dataset}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"FinMind storage object {dataset} {date} #{index}",
                    content_ref=f"finmind://storage_objects/{dataset}/{date}/{row_hash}",
                    metadata={
                        "source_class": "research_grade",
                        "source_plan": "finmind_first_low_cost_paid_layer",
                        "provider": "FinMind",
                        "dataset": dataset,
                        "source_dataset": dataset,
                        "date": date,
                        "event_time": date,
                        "available_time": _utc_now(),
                        "object_name": object_name,
                        "signed_url_present": bool(signed_url),
                        "signed_url_hash": signed_url_hash,
                        "signed_url_redacted": True,
                        "raw_storage_partition": f"raw/finmind/{dataset}/date={date}/",
                        "raw_row_without_signed_url": {
                            key: value
                            for key, value in dict(row).items()
                            if key not in {"url", "signed_url", "download_url"}
                        },
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                        "entitlement_tier": "sponsorpro",
                        "schema_hash": FINMIND_STORAGE_OBJECT_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)


# ---------------------------------------------------------------------------
# Live fetch errors
# ---------------------------------------------------------------------------


class FinMindFetchError(RuntimeError):
    """Raised when a FinMind live fetch fails for a non-credential reason."""


class FinMindCredentialError(FinMindFetchError):
    """Raised when no FINMIND_API_TOKEN is available or the token is rejected."""


class FinMindQuotaError(FinMindFetchError):
    """Raised when the FinMind API quota is exhausted."""


# ---------------------------------------------------------------------------
# Rate-limit / quota header extraction
# ---------------------------------------------------------------------------

_RATELIMIT_HEADER_MAP: tuple[tuple[str, str], ...] = (
    ("x-ratelimit-limit", "ratelimit_limit"),
    ("x-ratelimit-remaining", "ratelimit_remaining"),
    ("x-ratelimit-reset", "ratelimit_reset"),
    ("x-rate-limit-limit", "ratelimit_limit"),
    ("x-rate-limit-remaining", "ratelimit_remaining"),
    ("x-rate-limit-reset", "ratelimit_reset"),
    ("retry-after", "retry_after_seconds"),
    ("x-quota-limit", "quota_limit"),
    ("x-quota-remaining", "quota_remaining"),
    ("x-quota-reset", "quota_reset"),
)


def _extract_quota_meta(headers: Any) -> dict[str, Any]:
    """Extract rate-limit and quota metadata from HTTP response headers."""
    meta: dict[str, Any] = {}
    if headers is None:
        return meta
    try:
        items = list(headers.items())
    except AttributeError:
        return meta
    for header_key, meta_key in _RATELIMIT_HEADER_MAP:
        for raw_key, raw_value in items:
            if str(raw_key).lower().strip() == header_key and raw_value not in (None, ""):
                if meta_key in meta:
                    break
                try:
                    meta[meta_key] = int(str(raw_value).strip())
                except (ValueError, TypeError):
                    meta[meta_key] = str(raw_value).strip()
                break
    return meta


# ---------------------------------------------------------------------------
# KBar hourly self-limit (shared token: broker + daily jobs keep 25 % headroom)
# ---------------------------------------------------------------------------


class KBarHourlyBudget:
    """Fixed-window per-hour request budget (default 4,500) with 402 stop.

    ``acquire`` returns True when a request may be sent. After ``mark_quota_exhausted``
    (HTTP 402) or when the hour's budget is spent, it returns False until the next
    clock hour; callers stop the run and resume then. Nothing falls back to another
    source and nothing retries a 402 within the same hour.
    """

    def __init__(self, limit: int = FINMIND_KBAR_HOURLY_SELF_LIMIT, *, clock: Any = None) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = int(limit)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._window = self._current_window()
        self._used = 0
        self._blocked_window: int | None = None

    def _current_window(self) -> int:
        return int(self._clock() // 3600)

    def _roll(self) -> None:
        now = self._current_window()
        if now != self._window:
            self._window, self._used = now, 0
        if self._blocked_window is not None and self._blocked_window != self._window:
            self._blocked_window = None

    def acquire(self) -> bool:
        with self._lock:
            self._roll()
            if self._blocked_window == self._window or self._used >= self.limit:
                return False
            self._used += 1
            return True

    def mark_quota_exhausted(self) -> None:
        with self._lock:
            self._roll()
            self._blocked_window = self._window

    def seconds_until_next_window(self) -> int:
        with self._lock:
            self._roll()
            return int((self._window + 1) * 3600 - self._clock()) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._roll()
            return {
                "limit_per_hour": self.limit,
                "used_this_hour": self._used,
                "blocked_by_402": self._blocked_window == self._window,
                "window_start_epoch": self._window * 3600,
            }


_SHARED_KBAR_BUDGET = KBarHourlyBudget()


def shared_kbar_budget() -> KBarHourlyBudget:
    """Process-wide 4,500/h budget used by the production KBar provider path."""
    return _SHARED_KBAR_BUDGET


@dataclass(frozen=True)
class KBarFetchResult:
    status: str  # "ok" | "budget_exhausted" | "quota_402"
    payload: Mapping[str, Any] | None = None
    quota_meta: Mapping[str, Any] = field(default_factory=dict)
    budget: Mapping[str, Any] = field(default_factory=dict)
    resume_after_seconds: int = 0


def fetch_kbar(
    fetcher: "FinMindLiveFetcher",
    symbol: str,
    date: str,
    *,
    budget: KBarHourlyBudget,
) -> KBarFetchResult:
    """Budgeted single symbol-day KBar fetch.

    402 -> ``quota_402`` (stop, resume next hour, no retry). Credential errors
    propagate (``FinMindCredentialError``) so the task reports blocked; no
    placeholder data is ever produced.
    """
    if not budget.acquire():
        return KBarFetchResult("budget_exhausted", budget=budget.snapshot(),
                               resume_after_seconds=budget.seconds_until_next_window())
    try:
        payload, quota_meta = fetcher.fetch_kbar(symbol, date)
    except FinMindQuotaError:
        budget.mark_quota_exhausted()
        return KBarFetchResult("quota_402", budget=budget.snapshot(),
                               resume_after_seconds=budget.seconds_until_next_window())
    return KBarFetchResult("ok", payload=payload, quota_meta=quota_meta, budget=budget.snapshot())


# ---------------------------------------------------------------------------
# FinMind live HTTP fetcher
# ---------------------------------------------------------------------------

_FINMIND_FETCH_TIMEOUT_SECONDS: float = 30.0
_FINMIND_MAX_RESPONSE_BYTES: int = 20_000_000
# Bulk storage_objects parquet bodies legitimately exceed the JSON cap.
_FINMIND_MAX_BULK_RESPONSE_BYTES: int = 512_000_000
_FINMIND_READ_CHUNK_BYTES: int = 1 << 20


@dataclass
class FinMindLiveFetcher:
    """Live HTTP fetcher for the FinMind Taiwan market-data API.

    Resolves the API token at fetch time from ``secret_ref_id``
    (default: ``env://FINMIND_API_TOKEN``).  The token is never stored in
    evidence records or logged; only a redacted URL is included in quota_meta.

    When no token is available, ``credential_status()`` describes the gap so a
    ``SourceHealth`` record can surface ``credential_unavailable`` without
    making a live request.
    """

    secret_ref_id: str = "env://FINMIND_API_TOKEN"
    timeout_seconds: float = _FINMIND_FETCH_TIMEOUT_SECONDS

    def resolve_token(self) -> str | None:
        """Return the raw token string, or None when unavailable.

        Token is resolved at call time and must never be stored by callers.
        """
        if self.secret_ref_id.startswith("env://"):
            env_var = self.secret_ref_id[len("env://"):]
            return os.environ.get(env_var) or None
        return None

    def credential_status(self) -> dict[str, Any]:
        """Return a credential availability summary safe for logging and health records.

        Returns ``{"available": True, ...}`` when a token is present, or
        ``{"available": False, "status": "credential_unavailable", ...}`` when not.
        Never includes the token value.
        """
        token = self.resolve_token()
        if not token:
            return {
                "available": False,
                "status": "credential_unavailable",
                "secret_ref_id": self.secret_ref_id,
                "note": (
                    f"No API token found at {self.secret_ref_id}. "
                    "Set the environment variable to enable live FinMind fetching."
                ),
            }
        return {
            "available": True,
            "status": "credential_ok",
            "secret_ref_id": self.secret_ref_id,
        }

    def fetch_dataset(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch one FinMind ``/data`` dataset.

        Returns ``(payload, quota_meta)`` where ``quota_meta`` contains
        rate-limit headers and a redacted request URL (no token).

        Raises:
            FinMindCredentialError: no token, or token rejected by provider.
            FinMindQuotaError: provider returned quota-exceeded response.
            FinMindFetchError: any other provider or network error.
        """
        token = self._require_token()
        params: dict[str, str] = {"dataset": dataset}
        if symbol:
            params["data_id"] = symbol
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        url = _finmind_url(FINMIND_DATA_ENDPOINT, params)
        return self._get(url, token=token)

    def fetch_kbar(
        self,
        symbol: str,
        date: str,
        *,
        end_date: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch FinMind ``TaiwanStockKBar`` for one ``(stock_id, date)``.

        ``end_date`` is only passed by the bounded range probe; production
        requests are always one symbol-day. Returns ``(payload, quota_meta)``
        and adds ``fetched_at`` (UTC) to ``quota_meta`` taken right after the
        response arrived. Same credential/402 handling as ``fetch_dataset``.
        """
        payload, quota_meta = self.fetch_dataset(
            FINMIND_KBAR_DATASET, symbol=symbol, start_date=date, end_date=end_date or None
        )
        quota_meta["fetched_at"] = _utc_now()
        return payload, quota_meta

    def fetch_broker_report(
        self,
        symbol: str,
        date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch FinMind ``TaiwanStockTradingDailyReport`` for one symbol/date.

        Returns ``(payload, quota_meta)``.
        """
        token = self._require_token()
        params: dict[str, str] = {"data_id": symbol, "date": date}
        url = _finmind_url(FINMIND_BROKER_DAILY_REPORT_ENDPOINT, params)
        return self._get(url, token=token)

    def fetch_storage_objects(
        self,
        dataset: str,
        date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch FinMind SponsorPro storage-object manifest for bulk backfill.

        Returns ``(payload, quota_meta)``.
        """
        token = self._require_token()
        params: dict[str, str] = {"dataset": dataset, "date": date}
        url = _finmind_url(FINMIND_STORAGE_OBJECTS_ENDPOINT, params)
        return self._get(url, token=token)

    def fetch_storage_objects_raw(
        self,
        dataset: str,
        date: str,
    ) -> tuple[bytes, dict[str, Any]]:
        """Fetch storage_objects response bytes (parquet body or JSON), undecoded.

        FinMind serves TaiwanStockTradingDailyReport storage_objects as a parquet
        body; errors/manifests arrive as JSON. Same credential, quota, egress
        and redaction handling as ``_get`` (token only in Authorization header).
        """
        token = self._require_token()
        url = _finmind_url(FINMIND_STORAGE_OBJECTS_ENDPOINT, {"dataset": dataset, "date": date})
        return self._get_raw(url, token=token, max_bytes=_FINMIND_MAX_BULK_RESPONSE_BYTES)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _require_token(self) -> str:
        token = self.resolve_token()
        if not token:
            raise FinMindCredentialError(
                f"FINMIND_API_TOKEN not set; secret_ref_id={self.secret_ref_id!r}. "
                "Configure the environment variable to enable live FinMind fetching."
            )
        return token

    def _get(
        self,
        url: str,
        *,
        token: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """HTTP GET against FinMind API. Token is sent only as Authorization header."""
        raw, quota_meta = self._get_raw(url, token=token)
        return self.parse_json_payload(raw, quota_meta)

    def _get_raw(
        self,
        url: str,
        *,
        token: str,
        max_bytes: int = _FINMIND_MAX_RESPONSE_BYTES,
    ) -> tuple[bytes, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/octet-stream",
                "Authorization": f"Bearer {token}",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        quota_meta: dict[str, Any] = {"request_url": url}
        try:
            with open_external_url(
                request,
                caller="source_ingest.finmind_taiwan",
                timeout=self.timeout_seconds,
            ) as response:
                quota_meta.update(_extract_quota_meta(response.headers))
                quota_meta["http_status"] = response.status
                if max_bytes <= _FINMIND_MAX_RESPONSE_BYTES:
                    raw = response.read(max_bytes)
                else:
                    # read(n) may return short chunks; loop to the full body,
                    # bounded by max_bytes (fail loudly instead of truncating).
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(_FINMIND_READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise FinMindFetchError(
                                f"FinMind response exceeds {max_bytes} bytes bulk limit"
                            )
                        chunks.append(chunk)
                    raw = b"".join(chunks)
        except urllib.error.HTTPError as exc:
            quota_meta.update(_extract_quota_meta(exc.headers))
            quota_meta["http_status"] = exc.code
            if exc.code in (401, 403):
                raise FinMindCredentialError(
                    f"FinMind API rejected credentials (HTTP {exc.code}); "
                    f"secret_ref_id={self.secret_ref_id!r}"
                ) from exc
            if exc.code == 402:
                raise FinMindQuotaError(
                    f"FinMind API quota exceeded (HTTP 402); "
                    f"secret_ref_id={self.secret_ref_id!r}"
                ) from exc
            raise FinMindFetchError(f"FinMind API error HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise FinMindFetchError(f"FinMind connection error: {exc.reason}") from exc
        return raw, quota_meta

    def parse_json_payload(
        self,
        raw: bytes,
        quota_meta: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinMindFetchError("FinMind response is not valid UTF-8 JSON") from exc

        if not isinstance(payload, Mapping):
            raise FinMindFetchError("FinMind response must be a JSON object")

        # FinMind wraps application errors inside a 200 HTTP response body
        app_status = payload.get("status")
        if app_status is not None:
            try:
                app_code = int(app_status)
            except (ValueError, TypeError):
                app_code = 0
            if app_code not in (200, 201):
                msg = str(payload.get("msg") or f"FinMind API returned status {app_code}")
                if app_code in (401, 403):
                    raise FinMindCredentialError(f"FinMind auth error: {msg}")
                if app_code == 402:
                    raise FinMindQuotaError(f"FinMind quota error: {msg}")
                raise FinMindFetchError(f"FinMind API status {app_code}: {msg}")
            quota_meta["api_status"] = app_code

        return dict(payload), quota_meta


# ---------------------------------------------------------------------------
# KBar (1-minute) adapter -> tw_price_1m
# ---------------------------------------------------------------------------


def _parse_utc(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value!r}")
    return dt.astimezone(timezone.utc)


def _iso_utc(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_bar_availability_ts(
    bar_end: datetime,
    fetched_at: datetime | None,
    reconciled_at: datetime | None,
) -> datetime | None:
    """``max(bar_end, fetched_at, reconciled_at)``; None until reconciled.

    After-close data is never stamped ``bar_end``: it is obtainable only once it
    was really fetched AND reconciled. Without ``reconciled_at`` the bar is
    ``pending_reconciliation`` and invisible to loaders (SD §3, §6.3, §8).
    """
    if fetched_at is None or reconciled_at is None:
        return None
    return max(bar_end.astimezone(timezone.utc), fetched_at, reconciled_at)


@dataclass(frozen=True)
class KBarNormalization:
    records: tuple[SourceRecord, ...]
    raw_row_count: int
    kept_bars: int
    dropped_outside_session: int
    dropped_malformed: int
    conflicting_duplicates: tuple[str, ...]  # bar_start ISO values -> caller quarantines the day
    identical_duplicates: int
    rejected_non_trading_day: bool


@dataclass(frozen=True)
class FinMindTaiwanKBarAdapter(SourceConnectorProvider):
    """FinMind ``TaiwanStockKBar`` 1-minute bars normalized for ``tw_price_1m``.

    Post-close feed. Keeps raw rows and a source hash; never writes
    ``tw_price_daily``; ``bar_availability_ts`` is derived from real fetch and
    reconciliation times only.
    """

    connector_id: str = "tw-finmind-kbar-1m"
    secret_ref_id: str = "env://FINMIND_API_TOKEN"
    entitlement_tier: str = "sponsor"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="FinMind",
            license_scope="vendor_research",
            auth_type=AuthType.API_KEY,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref={"secret_ref_id": self.secret_ref_id},
                auth_scope=("finmind:read", "source_ingest:read"),
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_research",
                allowed_use=("research", "backtest", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/finmind-vendor-research",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=75,  # 4,500 / hour self-limit
                burst=5,
                retry_after_seconds=3600,
                concurrency=1,
                policy_ref=f"source-ingest://policy/finmind-{self.entitlement_tier}-kbar-4500ph",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FinMind Taiwan 1-minute KBar",
                homepage_url="https://finmindtrade.com/",
                docs_url="https://finmind.github.io/tutor/TaiwanMarket/Technical/#taiwanstockkbar-sponsor",
                owner="FinMind",
                tags=("taiwan", "finmind", "research_grade", "intraday", "post_close"),
            ),
            metadata={
                "source_class": "research_grade",
                "does_not_replace_official_reference_truth": True,
                "daily_truth_dataset": "tw_price_daily",
                "normalized_dataset": FINMIND_KBAR_NORMALIZED_DATASET,
                "feed_kind": "post_close_historical",
                "documented_publication_local": FINMIND_KBAR_DOCUMENTED_PUBLICATION_LOCAL,
                "hourly_self_limit": FINMIND_KBAR_HOURLY_SELF_LIMIT,
                "schema_hash": FINMIND_KBAR_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "next_watermark": None,
            "adapter": "FinMindTaiwanKBarAdapter.records_from_kbar_payload",
            "adapter_config": {"secret_ref_id": self.secret_ref_id},
            "request": {},
            "base_url": FINMIND_BASE_URL,
            "endpoint": FINMIND_DATA_ENDPOINT,
            "secret_ref_id": self.secret_ref_id,
            "datasets": [d for d in FINMIND_TAIWAN_DATASETS if d["dataset"] == FINMIND_KBAR_DATASET],
            "hourly_self_limit": FINMIND_KBAR_HOURLY_SELF_LIMIT,
        }

    def records_from_kbar_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        symbol: str,
        session_date: str,
        fetched_at: str | datetime,
        reconciled_at: str | datetime | None = None,
        provider_published_at: str | datetime | None = None,
        quota_meta: Mapping[str, Any] | None = None,
        is_trading_day: Any = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        result = self.normalize_kbar_payload(
            payload,
            symbol=symbol,
            session_date=session_date,
            fetched_at=fetched_at,
            reconciled_at=reconciled_at,
            provider_published_at=provider_published_at,
            quota_meta=quota_meta,
            is_trading_day=is_trading_day,
            trace_id=trace_id,
        )
        if result.rejected_non_trading_day:
            raise SourceEvidenceError(
                f"FinMind KBar {symbol} {session_date}: non-trading day; payload rejected"
            )
        if result.conflicting_duplicates:
            # Fail closed: first-wins would silently drop a conflicting raw row.
            raise SourceEvidenceError(
                f"FinMind KBar {symbol} {session_date}: conflicting duplicate minute bars "
                f"{list(result.conflicting_duplicates)}; symbol-day quarantined, no records emitted"
            )
        return result.records

    def normalize_kbar_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        symbol: str,
        session_date: str,
        fetched_at: str | datetime,
        reconciled_at: str | datetime | None = None,
        provider_published_at: str | datetime | None = None,
        quota_meta: Mapping[str, Any] | None = None,
        is_trading_day: Any = None,
        trace_id: str = "",
    ) -> KBarNormalization:
        """Normalize one symbol-day. ``fetched_at`` must be the real fetch time.

        ``provider_published_at`` is only recorded when the provider (or an
        observation probe) supplied it; it is never invented. ``reconciled_at``
        is None until K3 reconciles the day, which keeps ``bar_availability_ts``
        None (pending_reconciliation).
        """
        symbol = _text(symbol).upper()
        fetched = _parse_utc(fetched_at)
        if fetched is None:
            raise ValueError("fetched_at is required (real fetch time)")
        reconciled = _parse_utc(reconciled_at)
        published = _parse_utc(provider_published_at)
        day = _date_cls.fromisoformat(session_date)
        rows = _rows_from_payload(payload)

        if is_trading_day is not None and not is_trading_day(day):
            return KBarNormalization((), len(rows), 0, 0, 0, (), 0, True)

        open_t = KBAR_SESSION_OPEN
        last_t = KBAR_SESSION_LAST_BAR_START
        outside = malformed = identical = 0
        by_start: dict[str, tuple[dict[str, Any], Mapping[str, Any]]] = {}
        conflicts: list[str] = []
        quota = {k: v for k, v in dict(quota_meta or {}).items() if k != "request_url"}
        request_id = _stable_hash({"dataset": FINMIND_KBAR_DATASET, "symbol": symbol, "date": session_date})
        raw_hash_total = _stable_hash({"rows": [dict(r) for r in rows]})

        for row in rows:
            row_symbol = _text(_first(row, "stock_id", "data_id", "symbol")).upper()
            if row_symbol and row_symbol != symbol:
                malformed += 1
                continue
            if _text(row.get("date")) and _text(row.get("date"))[:10] != session_date:
                malformed += 1
                continue
            minute = _text(row.get("minute"))
            try:
                hh, mm = int(minute[0:2]), int(minute[3:5])
                start_local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TAIPEI_TZ)
            except (ValueError, IndexError):
                malformed += 1
                continue
            if (hh, mm) < open_t or (hh, mm) > last_t:
                outside += 1
                continue
            prices = [_number(row.get(k)) for k in ("open", "high", "low", "close")]
            volume = _number(row.get("volume"))
            if any(p is None for p in prices) or volume is None:
                malformed += 1
                continue
            start_iso = start_local.isoformat()
            normalized = {
                "symbol": symbol,
                "session_date": session_date,
                "bar_start": start_iso,
                "bar_end": (start_local + timedelta(minutes=1)).isoformat(),
                "open": prices[0], "high": prices[1], "low": prices[2], "close": prices[3],
                "volume": volume,
            }
            if start_iso in by_start:
                prev = by_start[start_iso][0]
                if all(prev[k] == normalized[k] for k in ("open", "high", "low", "close", "volume")):
                    identical += 1
                else:
                    conflicts.append(start_iso)
                continue
            by_start[start_iso] = (normalized, row)

        records: list[SourceRecord] = []
        for start_iso in sorted(by_start):
            normalized, raw = by_start[start_iso]
            bar_end = datetime.fromisoformat(normalized["bar_end"])
            availability = compute_bar_availability_ts(bar_end, fetched, reconciled)
            row_hash = _stable_hash({"dataset": FINMIND_KBAR_DATASET, "row": dict(raw)})
            normalized = {
                **normalized,
                "provider_published_at": _iso_utc(published),
                "fetched_at": _iso_utc(fetched),
                "reconciled_at": _iso_utc(reconciled),
                "bar_availability_ts": _iso_utc(availability),
            }
            records.append(
                SourceRecord(
                    source_id=f"finmind:{FINMIND_KBAR_DATASET}:{symbol}:{start_iso}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"FinMind {FINMIND_KBAR_DATASET} {symbol} {start_iso}",
                    content_ref=f"finmind://data/{FINMIND_KBAR_DATASET}/{symbol}/{start_iso}/{row_hash}",
                    metadata={
                        "source_class": "research_grade",
                        "provider": "FinMind",
                        "source": "finmind",
                        "dataset": FINMIND_KBAR_NORMALIZED_DATASET,
                        "source_dataset": FINMIND_KBAR_DATASET,
                        "normalized_target": FINMIND_KBAR_NORMALIZED_DATASET,
                        "symbol": symbol,
                        "as_of_date": session_date,
                        "event_time": normalized["bar_end"],
                        # PIT: obtainable only when really fetched AND reconciled.
                        "available_time": normalized["bar_availability_ts"],
                        "availability_status": "available" if availability else "pending_reconciliation",
                        "request_id": request_id,
                        "source_hash": row_hash,
                        "response_content_hash": raw_hash_total,
                        "raw_row": dict(raw),
                        "normalized_row": normalized,
                        "quota_meta": quota,
                        "redistribution_allowed": False,
                        "body": json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                        "entitlement_tier": self.entitlement_tier,
                        "schema_hash": FINMIND_KBAR_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return KBarNormalization(
            records=tuple(records),
            raw_row_count=len(rows),
            kept_bars=len(records),
            dropped_outside_session=outside,
            dropped_malformed=malformed,
            conflicting_duplicates=tuple(conflicts),
            identical_duplicates=identical,
            rejected_non_trading_day=False,
        )


# --- bounded real-source probes (pure analysis; the HTTP calls are made by the caller) ---


def classify_range_probe(
    single_day_rows: Sequence[Mapping[str, Any]],
    range_rows: Sequence[Mapping[str, Any]] | None,
    *,
    start_date: str,
    end_date: str,
    range_error: str | None = None,
) -> dict[str, Any]:
    """Decide whether KBar accepts ``start_date..end_date`` from one probe request."""
    if range_error is not None:
        return {"range_supported": False, "reason": f"range_request_failed: {range_error}"}
    dates = sorted({_text(r.get("date"))[:10] for r in (range_rows or []) if _text(r.get("date"))})
    multi = len(dates) > 1 and start_date <= dates[0] and dates[-1] <= end_date
    return {
        "range_supported": multi,
        "distinct_dates_returned": dates,
        "single_day_row_count": len(single_day_rows),
        "range_row_count": len(range_rows or []),
        "reason": "multiple_dates_returned" if multi else "range_returned_at_most_one_date",
    }


def measure_volume_unit(
    kbar_rows: Sequence[Mapping[str, Any]],
    official_daily_volume_shares: float,
    *,
    tolerance: float = 0.15,
) -> dict[str, Any]:
    """Compare summed KBar volume with official daily volume; infer the unit factor.

    ``unit_factor`` = kbar_sum * factor ≈ official shares; 1 = shares, 1000 = lots.
    Returns ``unit_factor=None`` (undetermined) when no candidate is within tolerance.
    Real probe (2026-11-12): factor 1000 (lots) with a 1.7-8.5 % unexplained residual of
    official volume absent from KBar, hence the 15 % tolerance (units differ by 1000x, so
    this cannot confuse them); the residual is reported, never assumed explained.
    """
    total = sum(v for v in (_number(r.get("volume")) for r in kbar_rows) if v is not None)
    result: dict[str, Any] = {"kbar_volume_sum": total, "official_volume_shares": official_daily_volume_shares}
    if total <= 0 or official_daily_volume_shares <= 0:
        return {**result, "unit_factor": None, "relative_error": None, "reason": "no_volume"}
    best: tuple[float, int] | None = None
    for factor in (1, 1000):
        err = abs(total * factor - official_daily_volume_shares) / official_daily_volume_shares
        if best is None or err < best[0]:
            best = (err, factor)
    assert best is not None
    if best[0] <= tolerance:
        return {
            **result, "unit_factor": best[1], "relative_error": best[0],
            "unexplained_residual_ratio": best[0], "reason": "within_tolerance",
        }
    return {**result, "unit_factor": None, "relative_error": best[0], "reason": "outside_tolerance"}


def observe_publication_time(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise real polling observations of when a session's KBar first appeared.

    Each observation: ``{"session_date", "polled_at"(UTC), "row_count"}``. The first
    poll with rows per session bounds publication from above; the last empty poll
    bounds it from below. Local (Asia/Taipei) times are reported.
    """
    by_date: dict[str, list[tuple[datetime, int]]] = {}
    for obs in observations:
        polled = _parse_utc(obs["polled_at"])
        if polled is None:
            continue
        by_date.setdefault(str(obs["session_date"]), []).append((polled, int(obs.get("row_count") or 0)))
    out: dict[str, Any] = {}
    for session, polls in sorted(by_date.items()):
        polls.sort()
        first_with_rows = next((p for p, n in polls if n > 0), None)
        empties_before = [p for p, n in polls if n == 0 and (first_with_rows is None or p < first_with_rows)]
        last_empty = max(empties_before) if empties_before else None
        out[session] = {
            "first_seen_with_rows_local": first_with_rows.astimezone(TAIPEI_TZ).isoformat() if first_with_rows else None,
            "last_empty_poll_local": last_empty.astimezone(TAIPEI_TZ).isoformat() if last_empty else None,
            "published_between_local": [
                last_empty.astimezone(TAIPEI_TZ).strftime("%H:%M") if last_empty else None,
                first_with_rows.astimezone(TAIPEI_TZ).strftime("%H:%M") if first_with_rows else None,
            ],
            "polls": len(polls),
        }
    return {"sessions": out, "documented_publication_local": FINMIND_KBAR_DOCUMENTED_PUBLICATION_LOCAL}


# ---------------------------------------------------------------------------
# Active-universe fanout plan for FinMind
# ---------------------------------------------------------------------------

FINMIND_ARCHIVE_DATASETS: frozenset[str] = frozenset({"TaiwanStockPrice"})
FINMIND_CHIP_DATASETS: tuple[str, ...] = (
    "TaiwanStockDayTrading",
    "TaiwanStockInstitutionalInvestorsBuySell",
    "TaiwanStockMarginPurchaseShortSale",
    "TaiwanStockSecuritiesLending",
    "TaiwanStockShareholding",
)
FINMIND_NEWS_DATASET = "TaiwanStockNews"


def build_finmind_fetch_plan(
    symbols_by_tier: Mapping[str, Sequence[str]],
    date: str,
    *,
    include_broker_detail: bool = True,
) -> list[dict[str, Any]]:
    """Build an active-universe-aware fetch plan for FinMind Taiwan datasets.

    ``symbols_by_tier`` maps tier name (``"core_universe"``,
    ``"candidate_universe"``, ``"archive_universe"``) to a list of stock symbols.

    Archive symbols receive only price baseline; chip detail, news metadata, and
    broker top20 are skipped to avoid unnecessary quota consumption.

    Returns a list of fetch-spec dicts with:
    - ``dataset``: FinMind dataset name
    - ``symbol``: stock symbol (uppercase)
    - ``date``: target date string
    - ``universe_tier``: tier string for scheduling context
    - ``fetcher``: ``"dataset"`` (for /data endpoint) or ``"broker_report"``
    """
    _CORE = "core_universe"
    _CANDIDATE = "candidate_universe"
    _ARCHIVE = "archive_universe"

    core: list[str] = [str(s).strip().upper() for s in symbols_by_tier.get(_CORE, [])]
    candidate: list[str] = [str(s).strip().upper() for s in symbols_by_tier.get(_CANDIDATE, [])]
    archive: list[str] = [str(s).strip().upper() for s in symbols_by_tier.get(_ARCHIVE, [])]
    core_set = set(core)
    candidate_set = set(candidate)

    def _tier(sym: str) -> str:
        if sym in core_set:
            return _CORE
        if sym in candidate_set:
            return _CANDIDATE
        return _ARCHIVE

    plan: list[dict[str, Any]] = []

    # Price baseline: all tiers
    for sym in core + candidate + archive:
        plan.append({"dataset": "TaiwanStockPrice", "symbol": sym, "date": date,
                     "universe_tier": _tier(sym), "fetcher": "dataset"})

    # Chip detail: core + candidate only (skip archive)
    for dataset in FINMIND_CHIP_DATASETS:
        for sym in core + candidate:
            plan.append({"dataset": dataset, "symbol": sym, "date": date,
                         "universe_tier": _tier(sym), "fetcher": "dataset"})

    # News metadata: core + candidate only (skip archive)
    for sym in core + candidate:
        plan.append({"dataset": FINMIND_NEWS_DATASET, "symbol": sym, "date": date,
                     "universe_tier": _tier(sym), "fetcher": "dataset"})

    # Broker top20: core + candidate only (entitlement: sponsor; skip archive)
    if include_broker_detail:
        for sym in core + candidate:
            plan.append({"dataset": FINMIND_BROKER_REPORT_DATASET, "symbol": sym, "date": date,
                         "universe_tier": _tier(sym), "fetcher": "broker_report"})

    return plan
