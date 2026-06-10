"""FinMind Taiwan market-data adapters.

FinMind is modeled as the low-cost paid Taiwan research layer. Official
exchange and disclosure sources still own official-reference truth; FinMind
provides normalized API and bulk backfill surfaces for research and feature
generation.
"""

from __future__ import annotations

import hashlib
import json
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
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "FinMindTaiwanDatasetAdapter.records_from_data_payload",
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
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload",
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
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "FinMindTaiwanBrokerBulkBackfillAdapter.records_from_storage_objects_payload",
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
