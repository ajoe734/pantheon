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
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

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


FINMIND_BASE_URL = "https://api.finmindtrade.com/api/v4"
FINMIND_DATA_ENDPOINT = "/data"
FINMIND_BROKER_DAILY_REPORT_ENDPOINT = "/taiwan_stock_trading_daily_report"
FINMIND_STORAGE_OBJECTS_ENDPOINT = "/storage_objects"
FINMIND_BROKER_REPORT_DATASET = "TaiwanStockTradingDailyReport"
FINMIND_BROKER_REPORT_HISTORY_START = "2021-06-30"
BROKER_TOP_SCHEMA_HASH = "tw_broker_top.v1"
FINMIND_DATASET_SCHEMA_HASH = "finmind_taiwan_dataset.v1"
FINMIND_STORAGE_OBJECT_SCHEMA_HASH = "finmind_storage_object_manifest.v1"

FINMIND_TAIWAN_DATASETS: tuple[dict[str, Any], ...] = (
    {
        "dataset": "TaiwanStockPrice",
        "normalized_dataset": "tw_price_daily",
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
                    "tw_price_daily",
                    "tw_institutional_flow",
                    "tw_margin_short_balance",
                    "tw_securities_lending",
                    "tw_shareholding",
                    "tw_news_metadata",
                ],
                "entitlement_tier": self.entitlement_tier,
                "dataset_catalog": [dict(item) for item in FINMIND_TAIWAN_DATASETS],
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
                "fallback_connector_id": "tw-yahoo-broker-top15",
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
                        "entitlement_tier": self.entitlement_tier,
                        "active_universe_tiers": ["core_universe", "candidate_universe"],
                        "schema_hash": BROKER_TOP_SCHEMA_HASH,
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
# FinMind live HTTP fetcher
# ---------------------------------------------------------------------------

_FINMIND_FETCH_TIMEOUT_SECONDS: float = 30.0
_FINMIND_MAX_RESPONSE_BYTES: int = 20_000_000


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
        params: dict[str, str] = {"dataset": dataset, "token": token}
        if symbol:
            params["data_id"] = symbol
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        redacted_params = {k: v for k, v in params.items() if k != "token"}
        url = _finmind_url(FINMIND_DATA_ENDPOINT, params)
        redacted_url = _finmind_url(FINMIND_DATA_ENDPOINT, redacted_params)
        return self._get(url, redacted_url=redacted_url)

    def fetch_broker_report(
        self,
        symbol: str,
        date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch FinMind ``TaiwanStockTradingDailyReport`` for one symbol/date.

        Returns ``(payload, quota_meta)``.
        """
        token = self._require_token()
        params: dict[str, str] = {"data_id": symbol, "date": date, "token": token}
        url = _finmind_url(FINMIND_BROKER_DAILY_REPORT_ENDPOINT, params)
        redacted_url = _finmind_url(
            FINMIND_BROKER_DAILY_REPORT_ENDPOINT, {"data_id": symbol, "date": date}
        )
        return self._get(url, redacted_url=redacted_url)

    def fetch_storage_objects(
        self,
        dataset: str,
        date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch FinMind SponsorPro storage-object manifest for bulk backfill.

        Returns ``(payload, quota_meta)``.
        """
        token = self._require_token()
        params: dict[str, str] = {"dataset": dataset, "date": date, "token": token}
        url = _finmind_url(FINMIND_STORAGE_OBJECTS_ENDPOINT, params)
        redacted_url = _finmind_url(
            FINMIND_STORAGE_OBJECTS_ENDPOINT, {"dataset": dataset, "date": date}
        )
        return self._get(url, redacted_url=redacted_url)

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
        url_with_token: str,
        *,
        redacted_url: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """HTTP GET against FinMind API.  Token is not stored; only redacted_url is evidence-safe."""
        request = urllib.request.Request(
            url_with_token,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        quota_meta: dict[str, Any] = {"request_url": redacted_url}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                quota_meta.update(_extract_quota_meta(response.headers))
                quota_meta["http_status"] = response.status
                raw = response.read(_FINMIND_MAX_RESPONSE_BYTES)
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
