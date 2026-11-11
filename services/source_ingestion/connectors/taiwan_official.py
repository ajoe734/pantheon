"""TWSE/TPEx official market-data source-ingest adapter.

The adapter keeps official exchange payload parsing behind the provider
boundary. Source-ingest can store the connector contract and replay normalized
records without learning each TWSE/TPEx field name.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
)
from .bounded_reader import (
    ResponseTruncated,
    read_bounded_response,
)
from .collector_bundle import (
    COLLECTOR_BUNDLE_ROOT_ENV,
    BundleTransport,
    CollectorBundleAdapter,
    LocalDirectoryBundleTransport,
)


TW_OFFICIAL_CONNECTOR_ID = "tw-twse-tpex-official-market"
TW_OFFICIAL_SCHEMA_HASH = "tw_taiwan_official_market.v1"
TW_PRICE_DAILY_SCHEMA_HASH = "tw_price_daily.v1"
TW_INSTITUTIONAL_FLOW_SCHEMA_HASH = "tw_institutional_flow.v1"
TW_MARGIN_SHORT_BALANCE_SCHEMA_HASH = "tw_margin_short_balance.v1"
TW_SECURITIES_LENDING_SCHEMA_HASH = "tw_securities_lending.v1"
TW_DAY_TRADING_SCHEMA_HASH = "tw_day_trading.v1"
TW_HOLIDAY_CALENDAR_SCHEMA_HASH = "tw_holiday_calendar.v1"
TDCC_SHAREHOLDING_CONNECTOR_ID = "tw-tdcc-shareholding-distribution"
TDCC_SHAREHOLDING_SCHEMA_HASH = "tw_tdcc_shareholding_distribution.v1"
TAIFEX_DERIVATIVES_CONNECTOR_ID = "tw-taifex-futures-options-chip"
TAIFEX_FUTURES_CHIP_SCHEMA_HASH = "tw_taifex_futures_chip.v1"
TAIFEX_OPTIONS_CHIP_SCHEMA_HASH = "tw_taifex_options_chip.v1"

TWSE_OPENAPI_BASE_URL = "https://openapi.twse.com.tw/v1"
TWSE_HOLIDAY_SCHEDULE_OPENAPI_ENDPOINT = f"{TWSE_OPENAPI_BASE_URL}/holidaySchedule/holidaySchedule"
TWSE_LEGACY_BASE_URL = "https://www.twse.com.tw/rwd/zh"
TPEX_OPENAPI_BASE_URL = "https://www.tpex.org.tw/openapi/v1"
TPEX_MARKET_BASE_URL = "https://www.tpex.org.tw/www/zh-tw"
TDCC_OPENAPI_BASE_URL = "https://openapi.tdcc.com.tw/v1"
TDCC_SMART_BASE_URL = "https://smart.tdcc.com.tw/opendata"
TAIFEX_OPENAPI_BASE_URL = "https://openapi.taifex.com.tw/v1"

COLLECTOR_TWSE_T86_SOURCE_ID = "collector-tw-twse-t86"
COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID = "collector-tw-twse-price-history"
COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID = "collector-tw-tpex-price-history"

TW_OFFICIAL_MIN_CLOSES_PER_ACTIVE_SYMBOL = 2
TW_OFFICIAL_HISTORY_MAX_MONTHS = 2
SOURCE_INGEST_TW_HISTORY_TRADING_DAYS_DEFAULT = 40
TW_PRICE_DAILY_DEFAULT_AVAILABLE_TIME_SUFFIX = "T14:30:00+08:00"


def resolve_tw_history_trading_days(val: Any = None) -> int:
    """Resolve the target trading-day depth for Taiwan official history."""
    if val is not None and str(val).strip():
        try:
            days = int(val)
            if days > 0:
                return days
        except (TypeError, ValueError):
            pass
    raw = os.getenv("SOURCE_INGEST_TW_HISTORY_TRADING_DAYS", "").strip()
    if raw:
        try:
            days = int(raw)
            if days > 0:
                return days
        except (TypeError, ValueError):
            pass
    return SOURCE_INGEST_TW_HISTORY_TRADING_DAYS_DEFAULT


def derive_tw_history_max_months(trading_days: int) -> int:
    """Derive bounded monthly fetch count for a target trading-day history depth."""
    depth = max(1, trading_days)
    months = max(2, math.ceil(depth / 15) + 1)
    return min(months, 12)


# Source-owned, immutable market-session evidence catalog.  The price adapter
# binds this evidence only to TWSE records whose trade date is inside the exact
# governed coverage window; it never infers trading status for an unlisted day
# or silently treats a year-wide range as open.  The consumer-side trust root
# independently pins the same canonical payload digest in
# services.execution.market_snapshot_admission.
TWSE_2026_LNY_CALENDAR_VERSION = "twse-2026-lny-v1"
TWSE_2026_LNY_CALENDAR_SHA256 = (
    "55b2e23b9bd30af666a99c98da2dbbfad568dcd655631b1c6347d12ee8381596"
)
TWSE_2026_LNY_CALENDAR_FETCHED_AT = "2026-02-23T01:00:00Z"
_TWSE_2026_LNY_CALENDAR_PAYLOAD: Mapping[str, Any] = {
    "authority": "Taiwan Stock Exchange 115 年市場開休市日期",
    "coverage_end": "2026-02-23",
    "coverage_start": "2026-02-11",
    "holidays": {
        "2026-02-12": {"name": "市場無交易，僅辦理結算交割作業"},
        "2026-02-13": {"name": "市場無交易，僅辦理結算交割作業"},
        "2026-02-16": {"name": "農曆除夕及春節"},
        "2026-02-17": {"name": "農曆除夕及春節"},
        "2026-02-18": {"name": "農曆除夕及春節"},
        "2026-02-19": {"name": "農曆除夕及春節"},
        "2026-02-20": {"name": "農曆除夕及春節"},
    },
    "market": "TW",
    "source_url": "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear=115",
    "timezone": "Asia/Taipei",
    "trading_days": ["2026-02-11", "2026-02-23"],
    "venue": "TWSE",
    "version": TWSE_2026_LNY_CALENDAR_VERSION,
}

TWSE_2026_SCHEDULE_CALENDAR_VERSION = "twse-2026-schedule-v1"
TWSE_2026_SCHEDULE_CALENDAR_SHA256 = (
    "69c1a9b5f8cd347faca498ea3ecbc63fc3fe7a7bbb49d1ddb2b50f4cef0c35eb"
)
TWSE_2026_SCHEDULE_CALENDAR_FETCHED_AT = "2026-10-08T00:00:00Z"
_TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD: Mapping[str, Any] = {
    "authority": "Taiwan Stock Exchange 115 年市場開休市日期",
    "coverage_end": "2026-12-31",
    "coverage_start": "2026-02-24",
    "holidays": {
        "2026-02-27": {"name": "和平紀念日"},
        "2026-02-28": {"name": "和平紀念日"},
        "2026-04-03": {"name": "兒童節及民族掃墓節"},
        "2026-04-04": {"name": "兒童節及民族掃墓節"},
        "2026-04-05": {"name": "兒童節及民族掃墓節"},
        "2026-04-06": {"name": "兒童節及民族掃墓節"},
        "2026-05-01": {"name": "勞動節"},
        "2026-06-19": {"name": "端午節"},
        "2026-09-25": {"name": "中秋節"},
        "2026-09-28": {"name": "孔子誕辰紀念日/ 教師節"},
        "2026-10-09": {"name": "國慶日"},
        "2026-10-10": {"name": "國慶日"},
        "2026-10-25": {"name": "臺灣光復暨金門古寧頭大捷紀念日"},
        "2026-10-26": {"name": "臺灣光復暨金門古寧頭大捷紀念日"},
        "2026-12-25": {"name": "行憲紀念日"},
    },
    "market": "TW",
    "source_url": "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
    "timezone": "Asia/Taipei",
    "trading_days": [
        "2026-02-24", "2026-02-25", "2026-02-26",
        "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13", "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20", "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27", "2026-03-30", "2026-03-31",
        "2026-04-01", "2026-04-02", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10", "2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17", "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
        "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29",
        "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30",
        "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
        "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31",
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-29", "2026-09-30",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
        "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30",
        "2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04", "2026-12-07", "2026-12-08", "2026-12-09", "2026-12-10", "2026-12-11", "2026-12-14", "2026-12-15", "2026-12-16", "2026-12-17", "2026-12-18", "2026-12-21", "2026-12-22", "2026-12-23", "2026-12-24", "2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31",
    ],
    "venue": "TWSE",
    "version": TWSE_2026_SCHEDULE_CALENDAR_VERSION,
}

# Bounded correction to twse-2026-schedule-v1: TWSE announced on 2026-10-22
# 20:10 (newsDetail zh id 8a8216d6a0fa351b01a1638507a10203 (JSON alternate-language id ...0205), attachment
# https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf)
# that the whole 2026-10-23 session is closed (typhoon).  v1 stays pinned and
# unchanged; v2 only supersedes it for the 2026-10-22..2026-10-28 window.
TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_VERSION = "twse-2026-emergency-closure-20261023-v2"
TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_SHA256 = "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99"
TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_FETCHED_AT = "2026-10-23T07:00:39Z"
_TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD: Mapping[str, Any] = {
    "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
    "coverage_end": "2026-10-28",
    "coverage_start": "2026-10-22",
    "holidays": {
        "2026-10-23": {"name": "颱風全日休市 (TWSE 臨時休市公告)"},
        "2026-10-25": {"name": "臺灣光復暨金門古寧頭大捷紀念日"},
        "2026-10-26": {"name": "臺灣光復暨金門古寧頭大捷紀念日"},
    },
    "market": "TW",
    "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
    "timezone": "Asia/Taipei",
    "trading_days": ["2026-10-22", "2026-10-27", "2026-10-28"],
    "venue": "TWSE",
    "version": TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_VERSION,
}

_DATASET_SCHEMA_HASHES = {
    "tw_price_daily": TW_PRICE_DAILY_SCHEMA_HASH,
    "tw_institutional_flow": TW_INSTITUTIONAL_FLOW_SCHEMA_HASH,
    "tw_margin_short_balance": TW_MARGIN_SHORT_BALANCE_SCHEMA_HASH,
    "tw_securities_lending": TW_SECURITIES_LENDING_SCHEMA_HASH,
    "tw_day_trading": TW_DAY_TRADING_SCHEMA_HASH,
    "tw_holiday_calendar": TW_HOLIDAY_CALENDAR_SCHEMA_HASH,
    "tdcc_shareholding_distribution": TDCC_SHAREHOLDING_SCHEMA_HASH,
    "taifex_futures_chip": TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
    "taifex_options_chip": TAIFEX_OPTIONS_CHIP_SCHEMA_HASH,
    "tw_monthly_revenue": "tw_monthly_revenue.v1",
    "tw_financial_statement": "tw_financial_statement.v1",
    "tw_valuation_bwibbu": "tw_valuation_bwibbu.v1",
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
        "endpoint": f"bundle://{COLLECTOR_TWSE_T86_SOURCE_ID}",
        "transport": "collector_bundle",
        "collector_source_id": COLLECTOR_TWSE_T86_SOURCE_ID,
        "upstream_website_endpoint": f"{TWSE_LEGACY_BASE_URL}/fund/T86?response=json&selectType=ALLBUT0999",
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
        "dataset": "tw_holiday_calendar",
        "venue": "TWSE",
        "source_dataset": "holidaySchedule",
        "endpoint": TWSE_HOLIDAY_SCHEDULE_OPENAPI_ENDPOINT,
        "transport": "twse_openapi",
        "cadence": "annual_and_on_demand",
        "tier_scope": ["core_universe", "candidate_universe", "archive_universe"],
        "status": "implemented",
    },
    {
        "dataset": "tdcc_shareholding_distribution",
        "venue": "TDCC",
        "source_dataset": "TDCC_OD_1-5",
        "endpoint": f"{TDCC_SMART_BASE_URL}/getOD.ashx?id=1-5",
        "transport": "tdcc_smart_opendata",
        "cadence": "weekly_after_tdcc_publication",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "taifex_futures_chip",
        "venue": "TAIFEX",
        "source_dataset": "Daily_Institutional_Trading_Futures",
        "endpoint": f"{TAIFEX_OPENAPI_BASE_URL}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
        "transport": "taifex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
    {
        "dataset": "taifex_options_chip",
        "venue": "TAIFEX",
        "source_dataset": "Daily_Institutional_Trading_Options",
        "endpoint": f"{TAIFEX_OPENAPI_BASE_URL}/PutCallRatio",
        "transport": "taifex_openapi",
        "cadence": "daily_after_close",
        "tier_scope": ["core_universe", "candidate_universe"],
        "status": "implemented",
    },
)

TAIWAN_OFFICIAL_PRICE_HISTORY_ENDPOINTS: Mapping[str, dict[str, Any]] = {
    "TWSE": {
        "dataset": "tw_price_daily",
        "venue": "TWSE",
        "source_dataset": "MI_INDEX_ALLBUT0999",
        "endpoint": f"bundle://{COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID}",
        "transport": "collector_bundle",
        "collector_source_id": COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID,
        "upstream_website_endpoint": f"{TWSE_LEGACY_BASE_URL}/afterTrading/MI_INDEX",
        "cadence": "daily_after_close_and_backfill",
        "status": "implemented_bounded_active_symbols",
    },
    "TPEx": {
        "dataset": "tw_price_daily",
        "venue": "TPEx",
        "source_dataset": "tpex_daily_quotes_all",
        "endpoint": f"bundle://{COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID}",
        "transport": "collector_bundle",
        "collector_source_id": COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID,
        "upstream_website_endpoint": f"{TPEX_MARKET_BASE_URL}/afterTrading/dailyQuotes",
        "cadence": "daily_after_close_and_backfill",
        "status": "implemented_bounded_active_symbols",
    },
}


OFFICIAL_FETCH_ATTEMPTS = 3
OFFICIAL_FETCH_BACKOFF_SECONDS = 1.0


def _assert_twse_tpex_openapi_only_url(url: str) -> None:
    """Fail closed if Pantheon attempts any non-OpenAPI request to TWSE or TPEx websites."""
    parsed = urllib.parse.urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host.endswith("twse.com.tw") and host != "mops.twse.com.tw":
        if host != "openapi.twse.com.tw" or not path.startswith("/v1/"):
            raise SourceEvidenceError(
                f"Pantheon forbids direct requests to non-OpenAPI TWSE website endpoint: {url} "
                "(only https://openapi.twse.com.tw/v1/... is permitted; read T86 and price history from collector bundles)"
            )
    if host.endswith("tpex.org.tw"):
        if host not in {"www.tpex.org.tw", "openapi.tpex.org.tw"} or not path.startswith("/openapi/v1/"):
            raise SourceEvidenceError(
                f"Pantheon forbids direct requests to non-OpenAPI TPEx website endpoint: {url} "
                "(only https://www.tpex.org.tw/openapi/v1/... is permitted; read price history from collector bundles)"
            )


def _fetch_official(
    request: urllib.request.Request,
    *,
    caller: str,
    timeout_seconds: float,
    max_bytes: int,
    parse: Any,
) -> Any:
    """Fetch and parse one official endpoint, retrying only transient body failures.

    Official open-data hosts intermittently close a response early or stall a
    read; the same request is retried a bounded number of times. HTTP errors,
    egress denial and the byte limit are raised immediately.
    """
    _assert_twse_tpex_openapi_only_url(request.full_url)
    failures: list[str] = []
    for attempt in range(1, OFFICIAL_FETCH_ATTEMPTS + 1):
        try:
            with open_external_url(request, caller=caller, timeout=timeout_seconds) as response:
                raw_bytes = read_bounded_response(response, max_bytes=max_bytes)
            return parse(raw_bytes)
        except (
            ResponseTruncated,
            TimeoutError,
            http.client.IncompleteRead,
            json.JSONDecodeError,
            BrokenPipeError,
            urllib.error.URLError,
        ) as exc:
            if isinstance(exc, urllib.error.HTTPError) or (
                isinstance(exc, urllib.error.URLError)
                and not isinstance(getattr(exc, "reason", None), BrokenPipeError)
            ):
                raise
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc)[:200]}")
            if attempt < OFFICIAL_FETCH_ATTEMPTS:
                time.sleep(OFFICIAL_FETCH_BACKOFF_SECONDS * attempt)
    raise SourceEvidenceError(
        f"official endpoint {request.full_url} failed after {OFFICIAL_FETCH_ATTEMPTS} attempts: "
        + "; ".join(failures)
    )


def _taifex_endpoint(dataset: str) -> str:
    if dataset == "taifex_options_chip":
        return f"{TAIFEX_OPENAPI_BASE_URL}/PutCallRatio"
    if dataset == "taifex_futures_chip":
        return f"{TAIFEX_OPENAPI_BASE_URL}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
    raise SourceEvidenceError(f"unsupported TAIFEX dataset: {dataset}")


def _validate_or_convert_rfc3339(val: Any, name: str = "available_time") -> str:
    s = _text(val)
    if not s:
        return _utc_now()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        try:
            iso_str = s.replace("Z", "+00:00")
            datetime.fromisoformat(iso_str)
            return s
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        iso_str = s.replace(" ", "T") + "+00:00"
        try:
            datetime.fromisoformat(iso_str)
            return s.replace(" ", "T") + "Z"
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        iso_str = s + "T00:00:00+00:00"
        try:
            datetime.fromisoformat(iso_str)
            return s + "T00:00:00Z"
        except Exception as err:
            raise SourceEvidenceError(
                f"{name} must be a valid RFC3339 timestamp with valid calendar date/time; got: {val!r}"
            ) from err
    raise SourceEvidenceError(f"{name} must be a valid RFC3339 timestamp; got: {val!r}")


def _to_rfc3339(val: Any) -> str:
    return _validate_or_convert_rfc3339(val, name="timestamp")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def governed_taiwan_calendar_evidence(
    *,
    venue: str,
    trade_date: str,
) -> dict[str, Any] | None:
    """Return exact governed Source evidence for one official price record.

    A missing catalog entry is represented by ``None`` so downstream admission
    remains fail-closed whenever a completed weekday requires evidence.  This
    writer is deliberately venue/date bounded: TWSE proof is never relabelled
    as TPEx proof, and coverage alone never manufactures a session record.
    """

    canonical_venue = _canonical_venue(venue)
    normalized_trade_date = _roc_date_to_iso(trade_date)
    if canonical_venue != "TWSE" or not normalized_trade_date:
        return None

    matched_payload: Mapping[str, Any] | None = None
    expected_digest: str | None = None
    fetched_at: str | None = None

    if (
        _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD["coverage_start"]
        <= normalized_trade_date
        <= _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD["coverage_end"]
    ):
        matched_payload = _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD
        expected_digest = TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_SHA256
        fetched_at = TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_FETCHED_AT
    elif (
        _TWSE_2026_LNY_CALENDAR_PAYLOAD["coverage_start"]
        <= normalized_trade_date
        <= _TWSE_2026_LNY_CALENDAR_PAYLOAD["coverage_end"]
    ):
        matched_payload = _TWSE_2026_LNY_CALENDAR_PAYLOAD
        expected_digest = TWSE_2026_LNY_CALENDAR_SHA256
        fetched_at = TWSE_2026_LNY_CALENDAR_FETCHED_AT
    elif (
        _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD["coverage_start"]
        <= normalized_trade_date
        <= _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD["coverage_end"]
    ):
        matched_payload = _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD
        expected_digest = TWSE_2026_SCHEDULE_CALENDAR_SHA256
        fetched_at = TWSE_2026_SCHEDULE_CALENDAR_FETCHED_AT
    else:
        return None

    canonical_payload = json.loads(
        json.dumps(
            matched_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    digest = hashlib.sha256(
        json.dumps(
            canonical_payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if digest != expected_digest:
        raise SourceEvidenceError(
            "governed TWSE calendar payload digest does not match its exact version pin"
        )
    return {
        **canonical_payload,
        "fetched_at": fetched_at,
        "checksum": digest,
    }


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


def _tw_price_available_time(val: Any, date: str) -> str:
    """Normalize available_time for tw_price_daily to an aware ISO 8601 string with offset.

    tw_price_daily rows must carry a timezone-aware available_time (ISO 8601 with offset)
    and never a bare date.
    """
    target_date = _roc_date_to_iso(date) or _text(date)
    s = _text(val)
    if s and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        if target_date and s.startswith(target_date):
            return s
        if not target_date:
            return s
    if s and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        iso_str = s.replace(" ", "T")
        if target_date and iso_str.startswith(target_date):
            return iso_str
        if not target_date:
            return iso_str
    if target_date and re.match(r"^\d{4}-\d{2}-\d{2}$", target_date):
        return f"{target_date}{TW_PRICE_DAILY_DEFAULT_AVAILABLE_TIME_SUFFIX}"
    if s and re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s}{TW_PRICE_DAILY_DEFAULT_AVAILABLE_TIME_SUFFIX}"
    if s and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", s):
        return s
    return _utc_now()



_TAIPEI_TZ = timezone(timedelta(hours=8))


def _taipei_today() -> str:
    return datetime.now(_TAIPEI_TZ).date().isoformat()


def _month_anchors(value: Any, *, max_months: int) -> tuple[str, ...]:
    """Return a bounded newest-first list of Gregorian month anchors."""

    normalized = _roc_date_to_iso(value)
    try:
        current = datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise SourceEvidenceError(f"Taiwan official history anchor date is invalid: {value!r}") from exc
    anchors: list[str] = []
    year = current.year
    month = current.month
    for _ in range(max_months):
        anchors.append(f"{year:04d}-{month:02d}-01")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return tuple(anchors)


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


def canonical_taiwan_equity_symbol(value: Any) -> str:
    """Return the explicit official-exchange identity for a TW equity symbol.

    ``.TW`` and ``.TWO`` are execution-facing aliases for TWSE and TPEx,
    respectively.  Keeping this mapping explicit prevents a read or bounded
    refresh from guessing a venue from a bare ticker.
    """

    text = _text(value).upper()
    if "." not in text:
        raise SourceEvidenceError(
            f"Taiwan priority symbol must include an explicit market suffix: {value!r}"
        )
    symbol, suffix = text.rsplit(".", 1)
    if not symbol:
        raise SourceEvidenceError(f"Taiwan priority symbol is invalid: {value!r}")
    if suffix in {"TW", "TWSE"}:
        return f"{symbol}.TWSE"
    if suffix in {"TWO", "TPEX"}:
        return f"{symbol}.TPEX"
    raise SourceEvidenceError(
        f"Taiwan priority symbol has an unsupported market suffix: {value!r}"
    )


def _priority_symbols_for_venue(
    priority_symbols: Sequence[str] | str | None,
    venue: str,
) -> tuple[str, ...]:
    if priority_symbols in (None, ""):
        return tuple()
    if isinstance(priority_symbols, str):
        values = [item.strip() for item in priority_symbols.split(",") if item.strip()]
    else:
        values = [str(item).strip() for item in priority_symbols if str(item).strip()]
    venue_suffix = ".TWSE" if _canonical_venue(venue) == "TWSE" else ".TPEX"
    canonical: list[str] = []
    for value in values:
        symbol = canonical_taiwan_equity_symbol(value)
        if symbol.endswith(venue_suffix) and symbol not in canonical:
            canonical.append(symbol)
    return tuple(canonical)


def _prioritize_normalized_rows(
    rows: Sequence[Mapping[str, Any]],
    priority_symbols: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    if not priority_symbols:
        return tuple(rows)
    priority_index = {symbol: index for index, symbol in enumerate(priority_symbols)}
    return tuple(
        row
        for _, row in sorted(
            enumerate(rows),
            key=lambda item: (
                priority_index.get(str(item[1].get("symbol_canonical") or ""), len(priority_index)),
                item[0],
            ),
        )
    )


def _rows_from_payload(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]] | str) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, str):
        if not payload.strip():
            return tuple()
        try:
            parsed = json.loads(payload)
            return _rows_from_payload(parsed)
        except json.JSONDecodeError:
            reader = csv.DictReader(io.StringIO(payload))
            return tuple(dict(r) for r in reader)
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
    fundamental = TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS.get(dataset)
    if fundamental is not None:
        if canonical not in fundamental:
            raise ValueError(f"unsupported Taiwan official endpoint: dataset={dataset} venue={venue}")
        return {
            "dataset": dataset,
            "venue": canonical,
            "source_dataset": fundamental[canonical]["source_dataset"],
            "endpoint": fundamental[canonical]["endpoint"],
            "cadence": "monthly_or_quarterly_filing" if dataset != "tw_valuation_bwibbu" else "daily_after_close",
            "tier_scope": [],
        }
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
    bundle_transport: BundleTransport | None = None
    bundle_root: str | Path | None = None

    def _resolve_bundle_transport(
        self,
        *,
        bundle_transport: BundleTransport | None = None,
        bundle_root: str | Path | None = None,
    ) -> BundleTransport | None:
        if bundle_transport is not None:
            return bundle_transport
        if self.bundle_transport is not None:
            return self.bundle_transport
        resolved_root = bundle_root or self.bundle_root or os.getenv(COLLECTOR_BUNDLE_ROOT_ENV, "").strip()
        if resolved_root:
            return LocalDirectoryBundleTransport(resolved_root)
        return None

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
                "holiday_calendar_endpoint": TWSE_HOLIDAY_SCHEDULE_OPENAPI_ENDPOINT,
                "collector_bundle_sources": [
                    COLLECTOR_TWSE_T86_SOURCE_ID,
                    COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID,
                    COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID,
                ],
                "endpoint_inventory": [dict(endpoint) for endpoint in TAIWAN_OFFICIAL_ENDPOINTS],
                "price_history_endpoint_inventory": [
                    dict(TAIWAN_OFFICIAL_PRICE_HISTORY_ENDPOINTS[venue])
                    for venue in ("TWSE", "TPEx")
                ],
                "active_symbol_history_policy": {
                    "minimum_distinct_closes": TW_OFFICIAL_MIN_CLOSES_PER_ACTIVE_SYMBOL,
                    "max_months_per_symbol": TW_OFFICIAL_HISTORY_MAX_MONTHS,
                },
                "governed_calendar_evidence": [
                    {
                        "venue": "TWSE",
                        "year": 2026,
                        "coverage_start": _TWSE_2026_LNY_CALENDAR_PAYLOAD["coverage_start"],
                        "coverage_end": _TWSE_2026_LNY_CALENDAR_PAYLOAD["coverage_end"],
                        "version": TWSE_2026_LNY_CALENDAR_VERSION,
                        "sha256": TWSE_2026_LNY_CALENDAR_SHA256,
                        "source_url": _TWSE_2026_LNY_CALENDAR_PAYLOAD["source_url"],
                    },
                    {
                        "venue": "TWSE",
                        "year": 2026,
                        "coverage_start": _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD["coverage_start"],
                        "coverage_end": _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD["coverage_end"],
                        "version": TWSE_2026_SCHEDULE_CALENDAR_VERSION,
                        "sha256": TWSE_2026_SCHEDULE_CALENDAR_SHA256,
                        "source_url": _TWSE_2026_SCHEDULE_CALENDAR_PAYLOAD["source_url"],
                    },
                    {
                        "venue": "TWSE",
                        "year": 2026,
                        "coverage_start": _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD["coverage_start"],
                        "coverage_end": _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD["coverage_end"],
                        "version": TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_VERSION,
                        "sha256": TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_SHA256,
                        "source_url": _TWSE_2026_EMERGENCY_CLOSURE_CALENDAR_PAYLOAD["source_url"],
                    },
                ],
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

    def holiday_calendar_endpoint(self) -> str:
        return TWSE_HOLIDAY_SCHEDULE_OPENAPI_ENDPOINT

    def fetch_holiday_calendar_payload(self, *, timeout_seconds: float = 20.0) -> Any:
        return self.fetch_payload("tw_holiday_calendar", "TWSE", timeout_seconds=timeout_seconds)

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
        if url.startswith("bundle://"):
            collector_source_id = str(
                endpoint.get("collector_source_id") or url[len("bundle://") :].split("?", 1)[0]
            )
            transport = self._resolve_bundle_transport()
            if transport is None:
                raise SourceEvidenceError(
                    f"Taiwan official website dataset {dataset} ({venue}) is fetched via collector bundle "
                    f"{collector_source_id}; direct exchange website requests from Pantheon are forbidden"
                )
            bundle = CollectorBundleAdapter(
                connector_id=collector_source_id,
                source_id=collector_source_id,
                source_type="market",
                transport=transport,
                max_records=max(self.max_records, 5000),
            ).fetch_bundle()
            rows = [
                dict(record.metadata.get("normalized_row") or record.metadata.get("metrics") or record.metadata)
                for record in bundle.records
            ]
            manifest_info = (
                dict(bundle.records[0].metadata.get("collector") or {})
                if bundle.records
                else {}
            )
            return {
                "stat": "OK",
                "data": rows,
                "bundle_manifest": manifest_info,
            }
        def _get_json(target: str) -> Any:
            request = urllib.request.Request(
                target,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "pantheon-source-ingest/0.1",
                },
            )
            return _fetch_official(
                request,
                caller="source_ingest.taiwan_official",
                timeout_seconds=timeout_seconds,
                max_bytes=10485760,
                parse=lambda raw_bytes: json.loads(raw_bytes.decode("utf-8")),
            )

        if dataset == "tw_financial_statement":
            # Quarterly statements join the income (t187ap06) and balance (t187ap07)
            # OpenAPI responses; both are fetched here so the payload matches
            # ``normalized_rows_from_payload``'s {"income", "balance"} contract.
            balance_url = str(
                TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS[dataset][_canonical_venue(venue)]["balance_endpoint"]
            )
            _assert_twse_tpex_openapi_only_url(url)
            _assert_twse_tpex_openapi_only_url(balance_url)
            return {"income": _get_json(url), "balance": _get_json(balance_url)}
        return _get_json(url)

    def price_history_endpoint(
        self,
        symbol: str,
        venue: str,
        *,
        anchor_date: str,
    ) -> str:
        canonical_venue = _canonical_venue(venue)
        canonical_symbol = canonical_taiwan_equity_symbol(symbol)
        expected_suffix = ".TWSE" if canonical_venue == "TWSE" else ".TPEX"
        if not canonical_symbol.endswith(expected_suffix):
            raise SourceEvidenceError(
                f"Taiwan official history symbol {canonical_symbol} does not belong to {canonical_venue}"
            )
        ticker = canonical_symbol.rsplit(".", 1)[0]
        [month_anchor] = _month_anchors(anchor_date, max_months=1)
        descriptor = TAIWAN_OFFICIAL_PRICE_HISTORY_ENDPOINTS[canonical_venue]
        parameters = {
            "date": month_anchor.replace("-", ""),
            "symbol": ticker,
        }
        return str(descriptor["endpoint"]) + "?" + urllib.parse.urlencode(parameters)

    def fetch_price_history_payload(
        self,
        symbol: str,
        venue: str,
        *,
        anchor_date: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[Mapping[str, Any], str]:
        url = self.price_history_endpoint(symbol, venue, anchor_date=anchor_date)
        canonical_venue = _canonical_venue(venue)
        canonical_symbol = canonical_taiwan_equity_symbol(symbol)
        ticker = canonical_symbol.rsplit(".", 1)[0]
        [month_anchor] = _month_anchors(anchor_date, max_months=1)
        month_prefix = month_anchor[:7]
        if url.startswith("bundle://"):
            descriptor = TAIWAN_OFFICIAL_PRICE_HISTORY_ENDPOINTS[canonical_venue]
            collector_source_id = str(
                descriptor.get("collector_source_id") or url[len("bundle://") :].split("?", 1)[0]
            )
            transport = self._resolve_bundle_transport()
            if transport is None:
                raise SourceEvidenceError(
                    f"Taiwan official price history for {canonical_symbol} is fetched via collector bundle "
                    f"{collector_source_id}; direct exchange website requests from Pantheon are forbidden"
                )
            bundle = CollectorBundleAdapter(
                connector_id=collector_source_id,
                source_id=collector_source_id,
                source_type="market",
                transport=transport,
                max_records=max(self.max_records, 5000),
            ).fetch_bundle()
            all_rows = [
                dict(record.metadata.get("normalized_row") or record.metadata.get("metrics") or record.metadata)
                for record in bundle.records
            ]
            symbol_rows = [
                row
                for row in all_rows
                if _text(row.get("symbol")).upper() == ticker
                or _text(row.get("symbol_canonical")).upper() == canonical_symbol
            ]
            month_rows = [
                row
                for row in symbol_rows
                if str(row.get("date") or row.get("trade_date") or "").startswith(month_prefix)
            ]
            selected = month_rows if month_rows else symbol_rows
            if not selected:
                raise SourceEvidenceError(
                    f"Taiwan official collector bundle {collector_source_id} contains no price history rows for {canonical_symbol}"
                )
            manifest_info = (
                dict(bundle.records[0].metadata.get("collector") or {})
                if bundle.records
                else {}
            )
            return {
                "stat": "OK",
                "bundle_records": selected,
                "bundle_manifest": manifest_info,
            }, url
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        payload = _fetch_official(
            request,
            caller="source_ingest.taiwan_official",
            timeout_seconds=timeout_seconds,
            max_bytes=10485760,
            parse=lambda raw_bytes: json.loads(raw_bytes.decode("utf-8")),
        )
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError("Taiwan official price history payload must be an object")
        return payload, url

    def records_from_collector_bundle(
        self,
        dataset: str,
        venue: str,
        *,
        bundle_transport: BundleTransport | None = None,
        bundle_root: str | Path | None = None,
        symbol: str | None = None,
        priority_symbols: Sequence[str] | str | None = None,
        universe_tier: str = "core_universe",
        max_records: int | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        canonical_venue = _canonical_venue(venue)
        transport = self._resolve_bundle_transport(
            bundle_transport=bundle_transport,
            bundle_root=bundle_root,
        )
        if transport is None:
            raise SourceEvidenceError(
                f"collector bundle transport is required to read {dataset} ({canonical_venue})"
            )
        if dataset == "tw_institutional_flow" and canonical_venue == "TWSE":
            collector_source_id = COLLECTOR_TWSE_T86_SOURCE_ID
            endpoint_uri = f"bundle://{COLLECTOR_TWSE_T86_SOURCE_ID}"
            source_dataset = "T86"
        elif dataset == "tw_price_daily" and canonical_venue == "TWSE":
            collector_source_id = COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID
            endpoint_uri = f"bundle://{COLLECTOR_TWSE_PRICE_HISTORY_SOURCE_ID}"
            source_dataset = "MI_INDEX_ALLBUT0999"
        elif dataset == "tw_price_daily" and canonical_venue == "TPEx":
            collector_source_id = COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID
            endpoint_uri = f"bundle://{COLLECTOR_TPEX_PRICE_HISTORY_SOURCE_ID}"
            source_dataset = "tpex_daily_quotes_all"
        else:
            raise ValueError(
                f"dataset={dataset} venue={canonical_venue} is not backed by a collector bundle"
            )

        limit = max_records if max_records is not None else self.max_records
        bundle = CollectorBundleAdapter(
            connector_id=collector_source_id,
            source_id=collector_source_id,
            source_type="market",
            transport=transport,
            max_records=max(limit, 5000),
        ).fetch_bundle(trace_id=trace_id)
        rows = [
            dict(record.metadata.get("normalized_row") or record.metadata.get("metrics") or record.metadata)
            for record in bundle.records
        ]
        if symbol:
            canonical_symbol = canonical_taiwan_equity_symbol(symbol)
            ticker = canonical_symbol.rsplit(".", 1)[0]
            rows = [
                row
                for row in rows
                if _text(row.get("symbol")).upper() == ticker
                or _text(row.get("symbol_canonical")).upper() == canonical_symbol
            ]
        return self.records_from_payload(
            dataset,
            canonical_venue,
            rows,
            source_dataset=source_dataset,
            api_endpoint=endpoint_uri,
            universe_tier=universe_tier,
            priority_symbols=priority_symbols,
            max_records=limit,
            trace_id=trace_id,
        )

    def fetch_price_history_records(
        self,
        symbol: str,
        venue: str,
        *,
        anchor_date: str,
        history_trading_days: int | None = None,
        max_months: int | None = None,
        timeout_seconds: float = 20.0,
        trace_id: str = "",
        today_provider: Callable[[], str] | None = None,
    ) -> tuple[SourceRecord, ...]:
        """Fetch official monthly windows for one active symbol bounded by history depth.

        The anchor date can lag the Asia/Taipei calendar (openapi snapshot rows trail
        the official monthly window), so the current Taipei month is requested first
        when it is later than the anchor month. Months after it are never requested.
        """

        raw_env = os.getenv("SOURCE_INGEST_TW_HISTORY_TRADING_DAYS", "").strip()
        if history_trading_days is not None:
            target_days = max(1, int(history_trading_days))
        elif raw_env:
            target_days = max(1, int(raw_env))
        else:
            target_days = TW_OFFICIAL_MIN_CLOSES_PER_ACTIVE_SYMBOL

        if max_months is not None:
            resolved_max_months = max(1, int(max_months))
        elif history_trading_days is not None or raw_env:
            resolved_max_months = derive_tw_history_max_months(target_days)
        else:
            resolved_max_months = TW_OFFICIAL_HISTORY_MAX_MONTHS

        records: list[SourceRecord] = []
        month_anchors = _month_anchors(anchor_date, max_months=resolved_max_months)
        current_month = _month_anchors((today_provider or _taipei_today)(), max_months=1)[0]
        extra_current_month = current_month > month_anchors[0]
        if extra_current_month:
            month_anchors = (current_month, *month_anchors)
        for month_anchor in month_anchors:
            try:
                payload, api_endpoint = self.fetch_price_history_payload(
                    symbol,
                    venue,
                    anchor_date=month_anchor,
                    timeout_seconds=timeout_seconds,
                )
                month_records = self.records_from_price_history_payload(
                    symbol,
                    venue,
                    payload,
                    api_endpoint=api_endpoint,
                    available_time=anchor_date,
                    max_records=max(self.max_records, target_days),
                    trace_id=trace_id,
                )
            except SourceEvidenceError:
                # The current month window may not be published yet (e.g. before the close).
                if extra_current_month and month_anchor == current_month:
                    continue
                raise
            records.extend(month_records)
            distinct_dates = {
                str(record.metadata.get("event_time") or "")
                for record in records
                if record.metadata.get("event_time")
            }
            if len(distinct_dates) >= target_days:
                break
        return tuple(records)

    def records_from_price_history_payload(
        self,
        symbol: str,
        venue: str,
        payload: Mapping[str, Any],
        *,
        api_endpoint: str | None = None,
        available_time: str | None = None,
        max_records: int | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        canonical_venue = _canonical_venue(venue)
        canonical_symbol = canonical_taiwan_equity_symbol(symbol)
        expected_suffix = ".TWSE" if canonical_venue == "TWSE" else ".TPEX"
        if not canonical_symbol.endswith(expected_suffix):
            raise SourceEvidenceError(
                f"Taiwan official history symbol {canonical_symbol} does not belong to {canonical_venue}"
            )
        ticker = canonical_symbol.rsplit(".", 1)[0]
        descriptor = TAIWAN_OFFICIAL_PRICE_HISTORY_ENDPOINTS[canonical_venue]
        source_dataset = str(descriptor["source_dataset"])

        normalized_rows: list[dict[str, Any]] = []
        bundle_records = payload.get("bundle_records") if isinstance(payload, Mapping) else None
        if isinstance(bundle_records, list):
            for raw_item in bundle_records:
                if not isinstance(raw_item, Mapping):
                    continue
                row_symbol = _text(_first(raw_item, "symbol", "Code", "證券代號", "代號")).upper()
                if row_symbol and row_symbol != ticker:
                    continue
                date = _roc_date_to_iso(_first(raw_item, "date", "trade_date", "日期", "日 期", "Date"))
                close = _float(_first(raw_item, "close", "收盤價", "收盤", "Close", "ClosingPrice"))
                if not date or close is None or not math.isfinite(close) or close <= 0:
                    continue
                normalized_rows.append(
                    {
                        "dataset": "tw_price_daily",
                        "date": date,
                        "symbol": ticker,
                        "symbol_canonical": canonical_symbol,
                        "market": "TW",
                        "venue": canonical_venue,
                        "name": _text(_first(raw_item, "name", "security_name", "證券名稱", "名稱", "Name")),
                        "open": _float(_first(raw_item, "open", "開盤價", "開盤", "Open", "OpeningPrice")),
                        "high": _float(_first(raw_item, "high", "最高價", "最高", "High", "HighestPrice")),
                        "low": _float(_first(raw_item, "low", "最低價", "最低", "Low", "LowestPrice")),
                        "close": close,
                        "change": _float(_first(raw_item, "change", "漲跌價差", "漲跌", "Change")),
                        "volume": _int(_first(raw_item, "volume", "volume_shares", "成交股數", "TradeVolume")),
                        "volume_lots": _int(_first(raw_item, "volume_lots", "成交張數")),
                        "value": _int(_first(raw_item, "value", "turnover_twd", "成交金額", "成交金額(元)", "TradeValue")),
                        "value_thousands": _int(_first(raw_item, "value_thousands", "成交仟元")),
                        "transactions": _int(_first(raw_item, "transactions", "成交筆數", "筆數", "Transaction")),
                        "source_dataset": str(raw_item.get("source_dataset") or source_dataset),
                        "api_endpoint": api_endpoint or str(descriptor["endpoint"]),
                        "available_time": _tw_price_available_time(
                            available_time or _first(raw_item, "available_time", "AvailableTime"),
                            date,
                        ),
                        "history_window": str(raw_item.get("history_window") or "collector_bundle"),
                        "raw_row": dict(raw_item.get("raw_row") or raw_item),
                    }
                )
        else:
            if canonical_venue == "TWSE":
                if str(payload.get("stat") or "").upper() != "OK":
                    raise SourceEvidenceError("TWSE official price history response is not OK")
                fields = payload.get("fields")
                data = payload.get("data")
                name = _text(payload.get("title"))
                if (not isinstance(fields, list) or not isinstance(data, list)) and isinstance(payload.get("tables"), list):
                    for candidate_table in payload["tables"]:
                        if (
                            isinstance(candidate_table, Mapping)
                            and isinstance(candidate_table.get("fields"), list)
                            and isinstance(candidate_table.get("data"), list)
                            and any(k in candidate_table["fields"] for k in ("證券代號", "日期"))
                        ):
                            fields = candidate_table["fields"]
                            data = candidate_table["data"]
                            name = _text(candidate_table.get("title") or name)
                            break
            else:
                if str(payload.get("stat") or "").lower() != "ok":
                    raise SourceEvidenceError("TPEx official price history response is not OK")
                tables = payload.get("tables")
                if not isinstance(tables, list):
                    raise SourceEvidenceError("TPEx official price history tables are missing")
                table = next(
                    (
                        item
                        for item in tables
                        if isinstance(item, Mapping)
                        and isinstance(item.get("fields"), list)
                        and isinstance(item.get("data"), list)
                    ),
                    None,
                )
                if table is None:
                    raise SourceEvidenceError("TPEx official price history table is missing")
                fields = table.get("fields")
                data = table.get("data")
                name = _text(payload.get("name") or table.get("subtitle") or table.get("title"))

            if not isinstance(fields, list) or not isinstance(data, list):
                raise SourceEvidenceError("Taiwan official price history fields/data are invalid")
            raw_rows = tuple(
                dict(zip((str(field) for field in fields), item))
                for item in data
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray))
            )
            payload_trade_date = _roc_date_to_iso(_first(payload, "date", "Date", "資料日期"))
            for raw_row in raw_rows:
                row_code = _text(_first(raw_row, "證券代號", "代號", "Code", "symbol")).upper()
                if row_code and row_code != ticker:
                    continue
                date = _roc_date_to_iso(_first(raw_row, "日期", "日 期", "Date")) or payload_trade_date
                close = _float(_first(raw_row, "收盤價", "收盤", "Close", "ClosingPrice"))
                if not date or close is None or not math.isfinite(close) or close <= 0:
                    continue
                row_name = _text(_first(raw_row, "證券名稱", "名稱", "Name")) or name
                normalized_rows.append(
                    {
                        "dataset": "tw_price_daily",
                        "date": date,
                        "symbol": ticker,
                        "symbol_canonical": canonical_symbol,
                        "market": "TW",
                        "venue": canonical_venue,
                        "name": row_name,
                        "open": _float(_first(raw_row, "開盤價", "開盤", "Open", "OpeningPrice")),
                        "high": _float(_first(raw_row, "最高價", "最高", "High", "HighestPrice")),
                        "low": _float(_first(raw_row, "最低價", "最低", "Low", "LowestPrice")),
                        "close": close,
                        "change": _float(_first(raw_row, "漲跌價差", "漲跌", "Change")),
                        "volume": _int(_first(raw_row, "成交股數", "TradeVolume")),
                        "volume_lots": _int(_first(raw_row, "成交張數")),
                        "value": _int(_first(raw_row, "成交金額", "成交金額(元)", "TradeValue")),
                        "value_thousands": _int(_first(raw_row, "成交仟元")),
                        "transactions": _int(_first(raw_row, "成交筆數", "筆數", "Transaction")),
                        "source_dataset": source_dataset,
                        "api_endpoint": api_endpoint or str(descriptor["endpoint"]),
                        "available_time": _tw_price_available_time(
                            available_time or _first(raw_row, "available_time", "AvailableTime"),
                            date,
                        ),
                        "history_window": "per_date_all_stocks" if row_code else "official_monthly",
                        "raw_row": raw_row,
                    }
                )

        limit = max_records if max_records is not None else self.max_records
        return self._records_from_normalized_rows(
            dataset="tw_price_daily",
            venue=canonical_venue,
            normalized_rows=sorted(
                normalized_rows,
                key=lambda row: str(row.get("date") or ""),
            )[-limit :],
            source_dataset=source_dataset,
            api_endpoint=api_endpoint or str(descriptor["endpoint"]),
            available_time=available_time,
            universe_tier="core_universe",
            max_records=limit,
            trace_id=trace_id,
        )

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
        priority_symbols: Sequence[str] | str | None = None,
        max_records: int | None = None,
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
        normalized_rows = _prioritize_normalized_rows(
            normalized_rows,
            _priority_symbols_for_venue(priority_symbols, venue),
        )
        return self._records_from_normalized_rows(
            dataset=dataset,
            venue=venue,
            normalized_rows=normalized_rows,
            source_dataset=source_dataset,
            api_endpoint=api_endpoint,
            available_time=available_time,
            universe_tier=tier,
            max_records=max_records,
            trace_id=trace_id,
        )

    def _records_from_normalized_rows(
        self,
        *,
        dataset: str,
        venue: str,
        normalized_rows: Sequence[Mapping[str, Any]],
        source_dataset: str,
        api_endpoint: str,
        available_time: str | None,
        universe_tier: str,
        max_records: int | None = None,
        trace_id: str,
    ) -> tuple[SourceRecord, ...]:
        tier = _tier_name(universe_tier)
        endpoint = dict(_endpoint_for(dataset, venue))
        limit = max_records if max_records is not None else self.max_records
        records: list[SourceRecord] = []
        for row in normalized_rows[: limit]:
            normalized_row = dict(row)
            calendar_evidence = None
            if dataset == "tw_price_daily":
                calendar_evidence = governed_taiwan_calendar_evidence(
                    venue=str(row["venue"]),
                    trade_date=str(row.get("date") or ""),
                )
                row_available_time = _tw_price_available_time(
                    row.get("available_time") or available_time,
                    str(row.get("date") or ""),
                )
                normalized_row["available_time"] = row_available_time
            elif dataset in _GOVERNED_DAILY_FLOW_DATASETS and normalized_row.get("date"):
                event_date = str(normalized_row["date"])[:10]
                lag = _governed_daily_lag_days(dataset)
                supplied = _text(row.get("available_time") or available_time)
                if re.match(r"^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$", supplied):
                    row_available_time = supplied
                else:
                    row_available_time = _fundamental_lag_available(event_date, lag)
                normalized_row["event_time"] = event_date
                normalized_row["available_time"] = row_available_time
                normalized_row["ingest_time"] = _text(row.get("ingest_time")) or _utc_now()
                normalized_row["availability_lag_days"] = lag
                row = {**row, "ingest_time": normalized_row["ingest_time"], "availability_lag_days": lag}
            else:
                row_available_time = row.get("available_time") or available_time or _utc_now()
            row_hash = _stable_hash(
                {"dataset": dataset, "venue": normalized_row["venue"], "row": normalized_row}
            )
            symbol = _text(normalized_row.get("symbol"), "market")
            as_of_date = _text(normalized_row.get("date"), row_hash)
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
                        "available_time": row_available_time,
                        **(
                            {
                                "ingest_time": row["ingest_time"],
                                "availability_lag_days": row.get("availability_lag_days"),
                            }
                            if row.get("ingest_time")
                            else {}
                        ),
                        "api_endpoint": api_endpoint,
                        "cadence": endpoint.get("cadence"),
                        "tier_scope": list(endpoint.get("tier_scope") or []),
                        "universe_tier": tier,
                        "normalized_row": normalized_row,
                        **(
                            {"calendar_evidence": calendar_evidence}
                            if calendar_evidence is not None
                            else {}
                        ),
                        "raw_row": dict(normalized_row.get("raw_row") or {}),
                        **(
                            {"history_window": row["history_window"]}
                            if row.get("history_window")
                            else {}
                        ),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
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
        if dataset in TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS:
            common = {"source_dataset": source_dataset, "api_endpoint": api_endpoint}
            if dataset == "tw_monthly_revenue":
                return normalize_monthly_revenue_rows(canonical, payload, **common)
            if dataset == "tw_valuation_bwibbu":
                return normalize_valuation_rows(canonical, payload, **common)
            if not isinstance(payload, Mapping) or "income" not in payload:
                raise ValueError(
                    "tw_financial_statement payload must map 'income' (and optional 'balance') responses"
                )
            return normalize_financial_statement_rows(
                canonical, payload["income"], payload.get("balance"), **common
            )
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
        if dataset == "tw_holiday_calendar":
            return tuple(
                row
                for row in (
                    self._normalize_holiday_calendar_row(canonical, raw, source_dataset, api_endpoint, available_time)
                    for raw in rows
                )
                if row is not None
            )
        raise ValueError(f"unsupported Taiwan official dataset: {dataset}")

    def fundamental_records(
        self,
        dataset: str,
        venue: str,
        *,
        fetch: Callable[[str], Any] | None = None,
        max_records: int | None = None,
        ingest_time: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        """Fetch one governed fundamentals dataset and wrap it for the storage writer."""
        rows = fetch_fundamental_rows(dataset, venue, fetch=fetch, ingest_time=ingest_time)
        limit = max_records if max_records is not None else self.max_records
        return fundamental_rows_to_records(
            rows[:limit] if limit else rows, connector_id=self.connector_id, trace_id=trace_id
        )

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
        symbol = _text(_first(row, "Code", "SecuritiesCompanyCode", "證券代號", "股票代號", "代號", "symbol")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date", "trade_date", "日期", "日 期"))
        if not symbol or not date:
            return None
        return {
            "dataset": "tw_price_daily",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "Name", "CompanyName", "證券名稱", "股票名稱", "名稱", "name", "security_name")),
            "open": _float(_first(row, "OpeningPrice", "Open", "開盤價", "開盤", "open")),
            "high": _float(_first(row, "HighestPrice", "High", "最高價", "最高", "high")),
            "low": _float(_first(row, "LowestPrice", "Low", "最低價", "最低", "low")),
            "close": _float(_first(row, "ClosingPrice", "Close", "收盤價", "收盤", "close")),
            "change": _float(_first(row, "Change", "漲跌價差", "漲跌", "change")),
            "volume": _int(_first(row, "TradeVolume", "TradingShares", "成交股數", "volume", "volume_shares")),
            "value": _int(_first(row, "TradeValue", "TransactionAmount", "成交金額", "成交金額(元)", "value", "turnover_twd")),
            "transactions": _int(_first(row, "Transaction", "TransactionNumber", "成交筆數", "筆數", "transactions")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": _tw_price_available_time(
                available_time or _first(row, "available_time", "AvailableTime"),
                date,
            ),
            "raw_row": dict(row.get("raw_row") if isinstance(row.get("raw_row"), Mapping) else row),
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
        symbol = _text(_first(row, "證券代號", "SecuritiesCompanyCode", "Code", "股票代號", "symbol")).upper()
        date = _roc_date_to_iso(_first(row, "Date", "資料日期", "date", "trade_date") or trade_date)
        if not symbol or not date:
            return None
        foreign_buy = _int(_first(row, "外陸資買進股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy", "foreign_buy"))
        foreign_sell = _int(_first(row, "外陸資賣出股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell", "foreign_sell"))
        foreign_net = _int(_first(row, "外陸資買賣超股數(不含外資自營商)", "ForeignInvestorsIncludeMainlandAreaInvestors-Difference", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference", "foreign_net", "foreign_net_shares"))
        trust_buy = _int(_first(row, "投信買進股數", "SecuritiesInvestmentTrustCompanies-TotalBuy", "investment_trust_buy"))
        trust_sell = _int(_first(row, "投信賣出股數", "SecuritiesInvestmentTrustCompanies-TotalSell", "investment_trust_sell"))
        trust_net = _int(_first(row, "投信買賣超股數", "SecuritiesInvestmentTrustCompanies-Difference", "investment_trust_net", "investment_trust_net_shares"))
        dealer_buy = _int(_first(row, "自營商買進股數(自行買賣)", "Dealers-TotalBuy", "自營商買進股數", "dealer_buy"))
        dealer_sell = _int(_first(row, "自營商賣出股數(自行買賣)", "Dealers-TotalSell", "Dealers -TotalSell", "自營商賣出股數", "dealer_sell"))
        dealer_net = _int(_first(row, "自營商買賣超股數", "Dealers-Difference", "dealer_net", "dealer_net_shares"))
        return {
            "dataset": "tw_institutional_flow",
            "date": date,
            "symbol": symbol,
            "symbol_canonical": _symbol_canonical(symbol, venue),
            "market": "TW",
            "venue": venue,
            "name": _text(_first(row, "證券名稱", "CompanyName", "Name", "股票名稱", "name", "security_name")),
            "foreign_buy": foreign_buy,
            "foreign_sell": foreign_sell,
            "foreign_net": foreign_net,
            "investment_trust_buy": trust_buy,
            "investment_trust_sell": trust_sell,
            "investment_trust_net": trust_net,
            "dealer_buy": dealer_buy,
            "dealer_sell": dealer_sell,
            "dealer_net": dealer_net,
            "total_net": _int(_first(row, "三大法人買賣超股數", "TotalDifference", "total_net", "total_institutional_net_shares")),
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint,
            "available_time": available_time or _first(row, "available_time") or date,
            "raw_row": dict(row.get("raw_row") if isinstance(row.get("raw_row"), Mapping) else row),
        }

    def _normalize_holiday_calendar_row(
        self,
        venue: str,
        row: Mapping[str, Any],
        source_dataset: str,
        api_endpoint: str,
        available_time: str | None,
    ) -> dict[str, Any] | None:
        date = _roc_date_to_iso(_first(row, "Date", "日期", "date"))
        if not date:
            return None
        name = _text(_first(row, "Name", "名稱", "HolidayName", "name"), "Holiday")
        weekday = _text(_first(row, "Weekday", "星期", "weekday"))
        description = _text(_first(row, "Description", "說明", "description"))
        return {
            "dataset": "tw_holiday_calendar",
            "date": date,
            "symbol": "TWSE",
            "symbol_canonical": "TWSE",
            "market": "TW",
            "venue": venue,
            "name": name,
            "weekday": weekday,
            "description": description,
            "source_dataset": source_dataset,
            "api_endpoint": api_endpoint or TWSE_HOLIDAY_SCHEDULE_OPENAPI_ENDPOINT,
            "available_time": available_time or f"{date}T00:00:00Z",
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
    symbols: Sequence[str] | None = None
    source_dataset: str = "TDCC_OD_1-5"
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
                "allowed_host_patterns": ["openapi.tdcc.com.tw", "smart.tdcc.com.tw", "www.tdcc.com.tw"],
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
                "symbols": list(self.symbols) if self.symbols else None,
                "source_dataset": self.source_dataset,
                "max_records": self.max_records,
            },
            "request": {
                "dataset": "tdcc_shareholding_distribution",
                "symbols": list(self.symbols) if self.symbols else ["2330"],
                "source_dataset": self.source_dataset,
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        *,
        source_dataset: str | None = None,
        symbols: Sequence[str] | None = None,
        max_records: int | None = None,
        timeout_seconds: float = 45.0,
    ) -> Any:
        """Fetch live open data payload from TDCC open data endpoint."""
        resolved_ds = str(source_dataset or self.source_dataset or "TDCC_OD_1-5")
        clean_id = resolved_ds.replace("TDCC_OD_", "")
        url = f"{TDCC_SMART_BASE_URL}/getOD.ashx?id={clean_id}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/csv, application/json, */*",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        text = _fetch_official(
            request,
            caller="source_ingest.tdcc_shareholding",
            timeout_seconds=timeout_seconds,
            max_bytes=5242880,
            parse=lambda raw_bytes: raw_bytes.decode("utf-8-sig"),
        )
        reader = csv.DictReader(io.StringIO(text))
        resolved_symbols = symbols if symbols is not None else self.symbols
        target_symbols = {str(s).strip().upper() for s in (resolved_symbols or ())} if resolved_symbols else None
        limit = max_records or self.max_records
        rows: list[dict[str, Any]] = []
        for r in reader:
            row_dict = dict(r)
            if target_symbols:
                sym = _text(_first(row_dict, "證券代號", "Code", "SecuritiesCompanyCode", "Symbol", "股票代號")).upper()
                if sym not in target_symbols:
                    continue
            rows.append(row_dict)
            if limit and len(rows) >= limit:
                break
        return rows

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
        source_dataset: str | None = None,
        api_endpoint: str | None = None,
        trade_date: str | None = None,
        available_time: str | None = None,
        universe_tier: str = "core_universe",
        symbols: Sequence[str] | None = None,
        max_records: int | None = None,
        is_correction: bool = False,
        correction_reason: str = "",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        resolved_ds = str(source_dataset or self.source_dataset or "TDCC_OD_1-5")
        clean_id = resolved_ds.replace("TDCC_OD_", "")
        resolved_endpoint = api_endpoint or f"{TDCC_SMART_BASE_URL}/getOD.ashx?id={clean_id}"
        resolved_symbols = symbols if symbols is not None else self.symbols
        normalized_rows = self.normalized_rows_from_payload(
            payload,
            source_dataset=resolved_ds,
            api_endpoint=resolved_endpoint,
            trade_date=trade_date,
            available_time=available_time,
            symbols=resolved_symbols,
            max_records=max_records or self.max_records,
            is_correction=is_correction,
            correction_reason=correction_reason,
        )
        records: list[SourceRecord] = []
        limit = max_records or self.max_records
        for row in normalized_rows[:limit]:
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
                        "source_dataset": resolved_ds,
                        "venue": row["venue"],
                        "symbol": row.get("symbol"),
                        "symbol_canonical": row.get("symbol_canonical"),
                        "date": row.get("date"),
                        "event_time": row.get("event_time") or (f"{row['date']}T00:00:00Z" if row.get("date") else _utc_now()),
                        "available_time": row.get("available_time") or _validate_or_convert_rfc3339(available_time or (f"{row['date']}T19:00:00Z" if row.get("date") else _utc_now())),
                        "ingest_time": row.get("ingest_time") or _validate_or_convert_rfc3339(_utc_now()),
                        "api_endpoint": resolved_endpoint,
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
        symbols: Sequence[str] | None = None,
        max_records: int | None = None,
        is_correction: bool = False,
        correction_reason: str = "",
    ) -> tuple[dict[str, Any], ...]:
        rows = _rows_from_payload(payload)
        resolved_symbols = symbols if symbols is not None else self.symbols
        target_symbols = {str(s).strip().upper() for s in (resolved_symbols or ())} if resolved_symbols else None
        limit = max_records or self.max_records
        resolved_ds = str(source_dataset or self.source_dataset or "TDCC_OD_1-5")
        clean_id = resolved_ds.replace("TDCC_OD_", "")
        resolved_endpoint = api_endpoint or f"{TDCC_SMART_BASE_URL}/getOD.ashx?id={clean_id}"
        results: list[dict[str, Any]] = []
        for raw in rows:
            symbol = _text(_first(raw, "證券代號", "Code", "SecuritiesCompanyCode", "Symbol", "股票代號")).upper()
            if target_symbols and symbol not in target_symbols:
                continue
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

            event_time_raw = _first(raw, "event_time", "EventTime") or f"{date}T00:00:00Z"
            avail_time_raw = _first(raw, "available_time", "AvailableTime") or available_time or f"{date}T19:00:00Z"
            event_time_rfc = _validate_or_convert_rfc3339(event_time_raw, name="event_time")
            avail_time_rfc = _validate_or_convert_rfc3339(avail_time_raw, name="available_time")
            ingest_time_rfc = _validate_or_convert_rfc3339(_first(raw, "ingest_time", "IngestTime") or _utc_now(), name="ingest_time")

            results.append({
                "dataset": "tdcc_shareholding_distribution",
                "date": date,
                "event_time": event_time_rfc,
                "available_time": avail_time_rfc,
                "ingest_time": ingest_time_rfc,
                "symbol": symbol,
                "symbol_canonical": _symbol_canonical(symbol, venue),
                "market": "TW",
                "venue": venue,
                "holder_level": level,
                "holding_range": holding_range,
                "people_count": people_count,
                "shares": shares,
                "percentage": percentage,
                "source_dataset": resolved_ds,
                "api_endpoint": resolved_endpoint,
                "is_correction": row_is_correction,
                "correction_reason": correction_reason or ("TDCC weekly correction" if row_is_correction else ""),
                "raw_row": dict(raw),
            })
            if limit and len(results) >= limit:
                break
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


_TAIFEX_CONTRACT_ALIASES: dict[str, str] = {
    "TX": "TX",
    "TXF": "TX",
    "臺股期貨": "TX",
    "MTX": "MTX",
    "小型臺指期貨": "MTX",
    "TE": "TE",
    "電子期貨": "TE",
    "TF": "TF",
    "金融期貨": "TF",
    "TXO": "TXO",
    "臺指選擇權": "TXO",
    "TEO": "TEO",
    "電子選擇權": "TEO",
    "TFO": "TFO",
    "金融選擇權": "TFO",
}


def _matches_taifex_contract(raw_contract: str, target_contracts: Sequence[str] | None) -> bool:
    if not target_contracts:
        return True
    raw_clean = raw_contract.strip()
    raw_upper = raw_clean.upper()
    canonical_upper = _TAIFEX_CONTRACT_ALIASES.get(raw_clean, _TAIFEX_CONTRACT_ALIASES.get(raw_upper, raw_upper)).upper()
    for t in target_contracts:
        t_clean = str(t).strip()
        t_upper = t_clean.upper()
        t_canon = _TAIFEX_CONTRACT_ALIASES.get(t_clean, _TAIFEX_CONTRACT_ALIASES.get(t_upper, t_upper)).upper()
        if raw_upper == t_upper or canonical_upper == t_canon or raw_clean == t_clean:
            return True
    return False


TAIFEX_SSF_DATASET = "taifex_ssf_contracts"
TAIFEX_SSF_SCHEMA_HASH = "tw_taifex_ssf_contracts.v1"
TAIFEX_SSF_OPENAPI_ENDPOINTS: Mapping[str, str] = {
    "SingleStockFuturesMargining": f"{TAIFEX_OPENAPI_BASE_URL}/SingleStockFuturesMargining",
    "SingleStockFuturesETFMargining": f"{TAIFEX_OPENAPI_BASE_URL}/SingleStockFuturesETFMargining",
    "DailyMarketReportFut": f"{TAIFEX_OPENAPI_BASE_URL}/DailyMarketReportFut",
    "FinalSettlementPriceSSF": f"{TAIFEX_OPENAPI_BASE_URL}/FinalSettlementPriceSSF",
}
TAIFEX_SSF_STOCK_LISTS_URL = "https://www.taifex.com.tw/cht/2/stockLists"
TAIFEX_SSF_MAX_RECORDS = 5000
_SSF_CONTRACT_CODE = re.compile(r"^[A-Z]{2}F$")
_SSF_CONTRACT_MONTH = re.compile(r"^\d{6}$")
_SSF_UNDERLYING_VENUE_SUFFIXES = {"TW", "TWSE", "TSE", "TWO", "TPEX", "OTC"}


def _normalize_ssf_underlying_code(value: Any) -> str:
    """Normalize ``2330``, ``2330.TW``, ``2330.TWSE`` or ``eq:2330`` to ``2330``."""
    text = _text(value).upper()
    if text.startswith("EQ:"):
        text = text[3:].strip()
    if "." in text:
        base, suffix = text.rsplit(".", 1)
        if suffix in _SSF_UNDERLYING_VENUE_SUFFIXES and base:
            return base
    return text


def _decode_taifex_bytes(raw_bytes: bytes) -> str:
    """Decode a TAIFEX body; ``utf-8-sig`` drops the BOM the CSV responses carry."""
    return raw_bytes.decode("utf-8-sig")


def _taifex_text_rows(value: Any) -> tuple[dict[str, Any], ...]:
    """Rows of one TAIFEX response given as rows, JSON text, or CSV text (BOM tolerated)."""
    if value is None:
        return tuple()
    if isinstance(value, (bytes, bytearray)):
        value = _decode_taifex_bytes(bytes(value))
    if isinstance(value, str):
        text = value.lstrip("﻿")
        if not text.strip():
            return tuple()
        try:
            return _taifex_text_rows(json.loads(text))
        except json.JSONDecodeError:
            try:
                reader = csv.DictReader(io.StringIO(text))
                return tuple({str(k).lstrip("﻿"): v for k, v in row.items() if k is not None} for row in reader)
            except csv.Error:
                return tuple()
    if isinstance(value, Mapping):
        return (dict(value),)
    if not hasattr(value, "__iter__"):
        return tuple()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def parse_taifex_stock_lists_units(page: str) -> dict[str, dict[str, Any]]:
    """Map contract family (``QF``) to underlying code, name, unit and ETF flag.

    The unit is the last numeric column of the ``stockLists`` table (standard
    shares or beneficiary units per contract).
    """
    families: dict[str, dict[str, Any]] = {}
    for row_html in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
        ]
        if len(cells) < 14 or not re.fullmatch(r"[A-Z]{2}", cells[0]):
            continue
        unit = _int(cells[11])
        if unit is None or unit <= 0:
            continue
        families[cells[0]] = {
            "family": cells[0],
            "underlying_name": cells[1],
            "underlying_code": cells[2],
            "unit": unit,
            "is_etf": "ETF" in (cells[9] + cells[10]),
        }
    return families


def _ssf_percent_to_fraction(value: Any) -> float | None:
    number = _float(value)
    return None if number is None else round(number / 100.0, 10)


_LAST_SSF_INGEST_US = 0


def _ssf_ingest_now() -> str:
    global _LAST_SSF_INGEST_US
    now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    if now_us <= _LAST_SSF_INGEST_US:
        now_us = _LAST_SSF_INGEST_US + 1
    _LAST_SSF_INGEST_US = now_us
    dt = datetime.fromtimestamp(now_us / 1_000_000, tz=timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _third_wednesday_iso(contract_month: str) -> str:
    year, month = int(contract_month[:4]), int(contract_month[4:6])
    first = datetime(year, month, 1)
    return (first + timedelta(days=(2 - first.weekday()) % 7 + 14)).date().isoformat()


def _has_any_key(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in row:
            return True
    squashed = {_squash_key(str(key)) for key in row}
    return any(_squash_key(key) in squashed for key in keys)


def taifex_ssf_normalized_rows(
    payload: Mapping[str, Any],
    *,
    underlying_codes: Sequence[str] | None = None,
    api_endpoint: str = "",
    trade_date: str | None = None,
    available_time: str | None = None,
    max_records: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """Join the five official SSF sources into one row per contract and month.

    Rows exist only for contracts present in a margin table, whose family is in
    ``stockLists``, and whose code matches ``^[A-Z]{2}F$``. Regular-session
    market rows with a numeric month are preserved (with ``settlement_price``
    set to ``None`` when ``SettlementPrice`` is non-numeric or non-positive so
    downstream month selection never falls through past an unpriced front month);
    after-hours and spread rows are not. Each emitted row carries scope-aware
    snapshot metadata (and a tombstone row is emitted when a completed snapshot
    produces no in-scope contract rows) so downstream consumers never resurrect
    contracts removed in a newer snapshot.
    """
    scope = {
        _normalize_ssf_underlying_code(code)
        for code in (underlying_codes or ())
        if _normalize_ssf_underlying_code(code)
    }
    scope_mode = "scoped" if scope else "all"
    scope_list = sorted(scope)
    margins: dict[str, dict[str, Any]] = {}
    latest_margin_date = ""
    for row in _taifex_text_rows(payload.get("SingleStockFuturesMargining")):
        margin_date = _roc_date_to_iso(row.get("Date"))
        if margin_date and margin_date > latest_margin_date:
            latest_margin_date = margin_date
        code = _text(row.get("Contract"))
        if _SSF_CONTRACT_CODE.match(code):
            margins[code] = {
                "underlying_code": _normalize_ssf_underlying_code(row.get("UnderlyingSecurityCode")),
                "contract_name": _text(row.get("ContractName")),
                "margin_mode": "rate",
                "margin_tier": _text(row.get("GroupLevel")),
                "clearing_margin_rate": _ssf_percent_to_fraction(row.get("ClearingMarginRate")),
                "maintenance_margin_rate": _ssf_percent_to_fraction(row.get("MaintenanceMarginRate")),
                "initial_margin_rate": _ssf_percent_to_fraction(row.get("InitialMarginRate")),
                "margin_date": margin_date,
                "raw_margin_row": dict(row),
            }
    for row in _taifex_text_rows(payload.get("SingleStockFuturesETFMargining")):
        margin_date = _roc_date_to_iso(row.get("Date"))
        if margin_date and margin_date > latest_margin_date:
            latest_margin_date = margin_date
        code = _text(row.get("Contract"))
        if _SSF_CONTRACT_CODE.match(code):
            margins[code] = {
                "underlying_code": _normalize_ssf_underlying_code(row.get("UnderlyingSecurityCode")),
                "contract_name": _text(row.get("ContractName")),
                "margin_mode": "fixed",
                "margin_tier": "",
                "clearing_margin_amount": _float(row.get("ClearingMargin")),
                "maintenance_margin_amount": _float(row.get("MaintenanceMargin")),
                "initial_margin_amount": _float(row.get("InitialMargin")),
                "margin_date": margin_date,
                "raw_margin_row": dict(row),
            }
    stock_lists = payload.get("stockLists")
    units = parse_taifex_stock_lists_units(stock_lists) if isinstance(stock_lists, str) else dict(stock_lists or {})
    last_final: dict[str, dict[str, Any]] = {}
    for row in _taifex_text_rows(payload.get("FinalSettlementPriceSSF")):
        code = _text(row.get("Contract"))
        day = _roc_date_to_iso(row.get("TheFinalSettlementDay"))
        if code and day and (code not in last_final or day > last_final[code]["day"]):
            last_final[code] = {
                "day": day,
                "contract_month": _text(row.get("ContractDeliveryMonth")),
                "price": _float(row.get("TheFinalSettlementPrice")),
            }
    stamp = available_time or ""
    ingest_time = _ssf_ingest_now()
    results: list[dict[str, Any]] = []
    absent_contracts: set[str] = set()
    absent_underlyings: set[str] = set()
    latest_market_date = ""
    limit = max_records or TAIFEX_SSF_MAX_RECORDS
    market_rows = _taifex_text_rows(payload.get("DailyMarketReportFut"))
    parsed_ssf_regular_rows = 0
    for row in market_rows:
        price_date = _roc_date_to_iso(_first(row, "Date", "日期"))
        valid_price_date = bool(price_date and re.match(r"^\d{4}-\d{2}-\d{2}$", price_date))
        if valid_price_date and price_date > latest_market_date:
            latest_market_date = price_date
        code = _text(_first(row, "Contract", "契約代號"))
        month = _text(_first(row, "ContractMonth(Week)", "ContractMonth", "到期月份(週別)"))
        session = _text(_first(row, "TradingSession", "交易時段"))
        if (
            not valid_price_date
            or not _SSF_CONTRACT_CODE.match(code)
            or not _SSF_CONTRACT_MONTH.match(month)
            or session != "一般"
            or not _has_any_key(row, "SettlementPrice", "結算價")
        ):
            continue
        parsed_ssf_regular_rows += 1
        settlement = _float(_first(row, "SettlementPrice", "結算價"))
        if settlement is not None and (not math.isfinite(settlement) or settlement <= 0):
            settlement = None
        unit_row = units.get(code[:2])
        if unit_row is None:
            continue
        unit_underlying = _normalize_ssf_underlying_code(unit_row.get("underlying_code"))
        if code not in margins:
            if not scope or (unit_underlying and unit_underlying in scope):
                absent_contracts.add(code)
                if unit_underlying:
                    absent_underlyings.add(unit_underlying)
            continue
        margin = margins[code]
        if scope and margin["underlying_code"] not in scope:
            continue
        normalized = {
            "dataset": TAIFEX_SSF_DATASET,
            "date": price_date,
            "event_time": f"{price_date}T00:00:00Z",
            "available_time": _validate_or_convert_rfc3339(stamp or f"{price_date}T16:30:00Z", name="available_time"),
            "ingest_time": ingest_time,
            "contract": code,
            "contract_family": code[:2],
            "contract_month": month,
            "contract_id": f"ssf:{code}:{month}",
            "underlying_code": margin["underlying_code"],
            "contract_name": margin["contract_name"],
            "kind": "stock_future",
            "underlying_kind": "etf" if unit_row.get("is_etf") else "stock",
            "unit": int(unit_row["unit"]),
            "market": "TW",
            "venue": "TAIFEX",
            "settlement_price": settlement,
            "price_date": price_date,
            "volume": _int(_first(row, "Volume", "合計成交量")) or 0,
            "open_interest": _int(_first(row, "OpenInterest", "未沖銷契約數")) or 0,
            "last_trading_day": _third_wednesday_iso(month),
            "last_trading_day_basis": "third_wednesday_no_holiday_shift",
            "last_final_settlement": last_final.get(code),
            "source_dataset": TAIFEX_SSF_DATASET,
            "api_endpoint": api_endpoint or TAIFEX_SSF_STOCK_LISTS_URL,
            "raw_market_row": dict(row),
        }
        normalized.update({k: v for k, v in margin.items() if k != "underlying_code" and k != "contract_name"})
        results.append(normalized)
        if len(results) >= limit:
            break
    if not market_rows or parsed_ssf_regular_rows == 0:
        raise SourceEvidenceError(
            "DailyMarketReportFut parse failure: no single-stock futures regular-session row could be read"
        )
    snapshot_contracts = sorted({r["contract"] for r in results if r.get("contract")})
    snapshot_contract_ids = sorted({r["contract_id"] for r in results if r.get("contract_id")})
    snapshot_underlyings = sorted(
        scope
        if scope
        else (
            {
                _normalize_ssf_underlying_code(u.get("underlying_code"))
                for u in units.values()
                if isinstance(u, Mapping) and _normalize_ssf_underlying_code(u.get("underlying_code"))
            }
            | {m["underlying_code"] for m in margins.values() if m.get("underlying_code")}
            | {r["underlying_code"] for r in results if r.get("underlying_code")}
            | absent_underlyings
        )
    )
    snapshot_absent = sorted(absent_contracts)
    if results:
        for item in results:
            item["snapshot_scope_mode"] = scope_mode
            item["snapshot_scope"] = scope_list
            item["snapshot_underlyings"] = snapshot_underlyings
            item["snapshot_contracts"] = snapshot_contracts
            item["snapshot_contract_ids"] = snapshot_contract_ids
            item["snapshot_absent_contracts"] = snapshot_absent
    elif scope or absent_contracts or units:
        stamp_rfc3339 = _validate_or_convert_rfc3339(stamp, name="available_time") if stamp else ""
        tombstone_date = (
            latest_market_date
            or latest_margin_date
            or (_roc_date_to_iso(trade_date) if trade_date else "")
            or (stamp_rfc3339[:10] if stamp_rfc3339 else "")
            or ingest_time[:10]
        )
        tombstone_avail = stamp_rfc3339 or _validate_or_convert_rfc3339(
            f"{tombstone_date}T16:30:00Z",
            name="available_time",
        )
        results.append(
            {
                "dataset": TAIFEX_SSF_DATASET,
                "date": tombstone_date,
                "price_date": tombstone_date,
                "margin_date": latest_margin_date or tombstone_date,
                "event_time": f"{tombstone_date}T00:00:00Z",
                "available_time": tombstone_avail,
                "ingest_time": ingest_time,
                "contract": "",
                "contract_family": "",
                "contract_month": "",
                "contract_id": "",
                "underlying_code": scope_list[0] if len(scope_list) == 1 else "",
                "kind": "stock_future_tombstone",
                "market": "TW",
                "venue": "TAIFEX",
                "tombstone": True,
                "source_dataset": TAIFEX_SSF_DATASET,
                "api_endpoint": api_endpoint or TAIFEX_SSF_STOCK_LISTS_URL,
                "snapshot_scope_mode": scope_mode,
                "snapshot_scope": scope_list,
                "snapshot_underlyings": snapshot_underlyings,
                "snapshot_contracts": [],
                "snapshot_contract_ids": [],
                "snapshot_absent_contracts": snapshot_absent,
            }
        )
    return tuple(results)


@dataclass(frozen=True)
class TaifexDerivativesChipAdapter(SourceConnectorProvider):
    """TAIFEX official daily futures and options chip-context adapter (SD-SRCM-05 §7.3)."""

    connector_id: str = TAIFEX_DERIVATIVES_CONNECTOR_ID
    dataset: str = "taifex_futures_chip"
    contracts: Sequence[str] | None = None
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
                "normalized_datasets": ["taifex_futures_chip", "taifex_options_chip", TAIFEX_SSF_DATASET],
                "tier_policy": {
                    "core_universe": ["taifex_futures_chip", "taifex_options_chip", TAIFEX_SSF_DATASET],
                    "candidate_universe": ["taifex_futures_chip", "taifex_options_chip"],
                },
                # Code-reviewed public official reference source (auth none, no secret):
                # satisfies the persona reconciler's require_connector_approved gate, which
                # reads this marker for registered custom connectors. Other gates are unaffected.
                "approval_status": "approved",
                "approval_authority": "code_reviewed_public_official_reference",
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        default_contracts = None if self.dataset == TAIFEX_SSF_DATASET else ["TX", "MTX"]
        resolved_contracts = (
            [_normalize_ssf_underlying_code(c) for c in self.contracts if _normalize_ssf_underlying_code(c)]
            if self.dataset == TAIFEX_SSF_DATASET and self.contracts
            else (list(self.contracts) if self.contracts else None)
        )
        return {
            "mode": "provider_owned_adapter",
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "adapter_config": {
                "dataset": self.dataset,
                "contracts": resolved_contracts,
                "max_records": self.max_records,
            },
            "request": {
                "dataset": self.dataset,
                "contracts": resolved_contracts if resolved_contracts is not None else default_contracts,
            },
            "next_watermark": None,
            "max_records": self.max_records,
        }

    def fetch_payload(
        self,
        dataset: str | None = None,
        *,
        max_records: int | None = None,
        timeout_seconds: float = 20.0,
    ) -> Any:
        """Fetch live open data payload from TAIFEX OpenAPI endpoint."""
        resolved_ds = str(dataset or self.dataset or "taifex_futures_chip")
        if resolved_ds == TAIFEX_SSF_DATASET:
            return self._fetch_ssf_payload(timeout_seconds=timeout_seconds)
        if resolved_ds not in ("taifex_futures_chip", "taifex_options_chip"):
            raise SourceEvidenceError(f"unsupported TAIFEX dataset: {resolved_ds}")
        url = _taifex_endpoint(resolved_ds)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "pantheon-source-ingest/0.1",
            },
        )
        parsed = _fetch_official(
            request,
            caller="source_ingest.taifex_derivatives",
            timeout_seconds=timeout_seconds,
            max_bytes=2097152,
            parse=lambda raw_bytes: json.loads(raw_bytes.decode("utf-8-sig")),
        )
        if isinstance(parsed, list) and (max_records or self.max_records):
            limit = max_records or self.max_records
            return parsed[:limit]
        return parsed

    def _fetch_ssf_payload(self, *, timeout_seconds: float = 20.0) -> dict[str, Any]:
        """Fetch the four OpenAPI tables and the ``stockLists`` page for taifex_ssf_contracts."""
        payload: dict[str, Any] = {}
        for name, url in TAIFEX_SSF_OPENAPI_ENDPOINTS.items():
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "pantheon-source-ingest/0.1"},
            )
            payload[name] = _fetch_official(
                request,
                caller="source_ingest.taifex_ssf_contracts",
                timeout_seconds=timeout_seconds,
                max_bytes=2097152,
                # The ETF margin table is served as CSV with a BOM on some reads.
                parse=lambda raw_bytes: _taifex_text_rows(_decode_taifex_bytes(raw_bytes)),
            )
        request = urllib.request.Request(
            TAIFEX_SSF_STOCK_LISTS_URL,
            headers={"Accept": "text/html", "User-Agent": "pantheon-source-ingest/0.1"},
        )
        payload["stockLists"] = _fetch_official(
            request,
            caller="source_ingest.taifex_ssf_contracts",
            timeout_seconds=timeout_seconds,
            max_bytes=2097152,
            parse=_decode_taifex_bytes,
        )
        return payload

    @staticmethod
    def is_contract_roll_day(trade_date: str) -> bool:
        """Check if trade_date (YYYY-MM-DD) is the 3rd Wednesday of the month (TAIFEX settlement/roll day)."""
        from datetime import date
        try:
            d = date.fromisoformat(trade_date)
            if d.weekday() != 2:  # Wednesday is 2
                return False
            first_day = date(d.year, d.month, 1)
            offset = (2 - first_day.weekday()) % 7
            first_wednesday = 1 + offset
            third_wednesday = first_wednesday + 14
            return d.day == third_wednesday
        except Exception:
            return False

    @staticmethod
    def resolve_front_month_contract(trade_date: str, contract_prefix: str = "TX") -> str:
        """Resolve active front month contract code based on settlement calendar."""
        from datetime import date
        try:
            d = date.fromisoformat(trade_date)
            first_day = date(d.year, d.month, 1)
            offset = (2 - first_day.weekday()) % 7
            third_wednesday = 1 + offset + 14
            if d.day <= third_wednesday:
                return f"{contract_prefix}{d.year}{d.month:02d}"
            next_month = 1 if d.month == 12 else d.month + 1
            next_year = d.year + 1 if d.month == 12 else d.year
            return f"{contract_prefix}{next_year}{next_month:02d}"
        except Exception:
            return f"{contract_prefix}FRONT"

    def records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        dataset: str | None = None,
        source_dataset: str | None = None,
        api_endpoint: str | None = None,
        trade_date: str | None = None,
        available_time: str | None = None,
        universe_tier: str = "core_universe",
        contracts: Sequence[str] | None = None,
        max_records: int | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        resolved_ds = str(dataset or self.dataset or "taifex_futures_chip")
        if resolved_ds == TAIFEX_SSF_DATASET:
            return self._ssf_records_from_payload(
                payload,
                source_dataset=source_dataset,
                api_endpoint=api_endpoint,
                trade_date=trade_date,
                available_time=available_time,
                universe_tier=universe_tier,
                contracts=contracts,
                max_records=max_records,
                trace_id=trace_id,
            )
        if resolved_ds not in ("taifex_futures_chip", "taifex_options_chip"):
            raise SourceEvidenceError(f"unsupported TAIFEX dataset: {resolved_ds}")
        resolved_endpoint = api_endpoint or _taifex_endpoint(resolved_ds)
        resolved_contracts = contracts if contracts is not None else self.contracts
        normalized_rows = self.normalized_rows_from_payload(
            payload,
            dataset=resolved_ds,
            source_dataset=source_dataset or resolved_ds,
            api_endpoint=resolved_endpoint,
            trade_date=trade_date,
            available_time=available_time,
            contracts=resolved_contracts,
            max_records=max_records or self.max_records,
        )
        records: list[SourceRecord] = []
        limit = max_records or self.max_records
        schema_hash = TAIFEX_OPTIONS_CHIP_SCHEMA_HASH if resolved_ds == "taifex_options_chip" else TAIFEX_FUTURES_CHIP_SCHEMA_HASH
        for row in normalized_rows[:limit]:
            row_hash = _stable_hash({"dataset": resolved_ds, "row": row})
            contract = _text(row.get("contract"), "MARKET")
            group = _text(row.get("participant_group"), "ALL")
            as_of_date = _text(row.get("date"), row_hash)
            content_ref = f"tw-taifex://{resolved_ds}/{contract}/{as_of_date}/{group}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"tw-taifex:{resolved_ds}:{contract}:{group}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TAIFEX {resolved_ds} {contract} {group} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "taiwan_chip",
                        "provider": "TAIFEX",
                        "dataset": resolved_ds,
                        "source_dataset": source_dataset or resolved_ds,
                        "venue": "TAIFEX",
                        "contract": contract,
                        "participant_group": group,
                        "date": row.get("date"),
                        "event_time": row.get("event_time"),
                        "available_time": row.get("available_time"),
                        "ingest_time": row.get("ingest_time"),
                        "api_endpoint": resolved_endpoint,
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

    def _ssf_records_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        source_dataset: str | None,
        api_endpoint: str | None,
        trade_date: str | None = None,
        available_time: str | None,
        universe_tier: str,
        contracts: Sequence[str] | None,
        max_records: int | None,
        trace_id: str,
    ) -> tuple[SourceRecord, ...]:
        rows = self.normalized_rows_from_payload(
            payload,
            dataset=TAIFEX_SSF_DATASET,
            api_endpoint=api_endpoint or "",
            trade_date=trade_date,
            available_time=available_time,
            contracts=contracts,
            max_records=max_records,
        )
        records: list[SourceRecord] = []
        for row in rows:
            row_hash = _stable_hash({"dataset": TAIFEX_SSF_DATASET, "row": row})
            contract = _text(row.get("contract"))
            month = _text(row.get("contract_month"))
            as_of_date = _text(row.get("date"), row_hash)
            records.append(
                SourceRecord(
                    source_id=f"tw-taifex:{TAIFEX_SSF_DATASET}:{contract}:{month}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TAIFEX {TAIFEX_SSF_DATASET} {contract} {month} {as_of_date}",
                    content_ref=f"tw-taifex://{TAIFEX_SSF_DATASET}/{contract}/{month}/{as_of_date}/{row_hash}",
                    metadata={
                        "source_class": "taiwan_chip",
                        "provider": "TAIFEX",
                        "dataset": TAIFEX_SSF_DATASET,
                        "source_dataset": source_dataset or TAIFEX_SSF_DATASET,
                        "venue": "TAIFEX",
                        "contract": contract,
                        "contract_month": month,
                        "underlying_code": row.get("underlying_code"),
                        "date": row.get("date"),
                        "event_time": row.get("event_time"),
                        "available_time": row.get("available_time"),
                        "ingest_time": row.get("ingest_time"),
                        "api_endpoint": row.get("api_endpoint"),
                        "cadence": "daily_after_taifex_publication",
                        "universe_tier": _tier_name(universe_tier),
                        "normalized_row": dict(row),
                        "raw_row": {
                            "margin": dict(row.get("raw_margin_row") or {}),
                            "market": dict(row.get("raw_market_row") or {}),
                        },
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "official_reference",
                        "schema_hash": TAIFEX_SSF_SCHEMA_HASH,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def normalized_rows_from_payload(
        self,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        dataset: str | None = None,
        source_dataset: str | None = None,
        api_endpoint: str = "",
        trade_date: str | None = None,
        available_time: str | None = None,
        contracts: Sequence[str] | None = None,
        max_records: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        resolved_ds = str(dataset or self.dataset or "taifex_futures_chip")
        if resolved_ds == TAIFEX_SSF_DATASET:
            if not isinstance(payload, Mapping):
                raise SourceEvidenceError("taifex_ssf_contracts payload must map source names to responses")
            # ``contracts`` carries underlying security codes for this dataset (symbol scope).
            resolved_codes = contracts if contracts is not None else self.contracts
            return taifex_ssf_normalized_rows(
                payload,
                underlying_codes=resolved_codes,
                api_endpoint=api_endpoint,
                trade_date=trade_date,
                available_time=available_time,
                max_records=max_records,
            )
        if resolved_ds not in ("taifex_futures_chip", "taifex_options_chip"):
            raise SourceEvidenceError(f"unsupported TAIFEX dataset: {resolved_ds}")
        rows = _rows_from_payload(payload)
        limit = max_records or self.max_records
        resolved_endpoint = api_endpoint or _taifex_endpoint(resolved_ds)
        resolved_contracts = contracts if contracts is not None else self.contracts
        results: list[dict[str, Any]] = []
        for raw in rows:
            if limit is not None and len(results) >= limit:
                break
            date = _roc_date_to_iso(_first(raw, "Date", "日期", "date", "TradingDate") or trade_date)
            if not date:
                continue

            roll_day = self.is_contract_roll_day(date) if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else False
            front_month = self.resolve_front_month_contract(date) if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else ""

            event_time_raw = _first(raw, "event_time", "EventTime") or f"{date}T00:00:00Z"
            avail_time_raw = _first(raw, "available_time", "AvailableTime") or available_time or f"{date}T16:30:00Z"
            event_time_rfc = _validate_or_convert_rfc3339(event_time_raw, name="event_time")
            avail_time_rfc = _validate_or_convert_rfc3339(avail_time_raw, name="available_time")
            ingest_time_rfc = _validate_or_convert_rfc3339(_first(raw, "ingest_time", "IngestTime") or _utc_now(), name="ingest_time")

            if resolved_ds == "taifex_options_chip":
                raw_contract = _text(_first(raw, "Contract", "契約名稱", "商品代號", "ContractCode"), "TXO")
                if not _matches_taifex_contract(raw_contract, resolved_contracts):
                    continue
                contract = _TAIFEX_CONTRACT_ALIASES.get(raw_contract, raw_contract)
                call_volume = _int(_first(raw, "買權成交量", "CallVolume", "call_volume")) or 0
                put_volume = _int(_first(raw, "賣權成交量", "PutVolume", "put_volume")) or 0
                call_oi = _int(_first(raw, "買權未平倉量", "CallOpenInterest", "CallOI", "call_open_interest")) or 0
                put_oi = _int(_first(raw, "賣權未平倉量", "PutOpenInterest", "PutOI", "put_open_interest")) or 0
                pc_ratio = _float(_first(raw, "買賣權未平倉量比率%", "PutCallRatio", "PutCallOIRatio%", "PutCallVolumeRatio%", "put_call_ratio", "Ratio"))
                if pc_ratio is None and call_oi > 0:
                    pc_ratio = round((put_oi / call_oi) * 100.0, 2)

                results.append({
                    "dataset": "taifex_options_chip",
                    "date": date,
                    "event_time": event_time_rfc,
                    "available_time": avail_time_rfc,
                    "ingest_time": ingest_time_rfc,
                    "contract": contract,
                    "market": "TW",
                    "venue": "TAIFEX",
                    "call_volume": call_volume,
                    "put_volume": put_volume,
                    "call_open_interest": call_oi,
                    "put_open_interest": put_oi,
                    "put_call_ratio": pc_ratio or 0.0,
                    "contract_roll_day": roll_day,
                    "front_month_contract": front_month,
                    "source_dataset": source_dataset or resolved_ds,
                    "api_endpoint": resolved_endpoint,
                    "raw_row": dict(raw),
                })
            else:
                raw_contract = _text(_first(raw, "Contract", "契約名稱", "商品代號", "contract", "ContractCode"), "TX")
                if not _matches_taifex_contract(raw_contract, resolved_contracts):
                    continue
                contract = _TAIFEX_CONTRACT_ALIASES.get(raw_contract, raw_contract)
                participant = _text(_first(raw, "身分別", "身份別", "ParticipantGroup", "participant_group", "Identity", "Item"), "foreign_investors")
                long_vol = _int(_first(raw, "多方交易口數", "LongVolume", "buy_volume", "買進口數", "TradingVolume(Long)")) or 0
                short_vol = _int(_first(raw, "空方交易口數", "ShortVolume", "sell_volume", "賣出口數", "TradingVolume(Short)")) or 0
                net_vol = _int(_first(raw, "多空交易口數淨額", "NetVolume", "net_volume", "買賣超口數", "TradingVolume(Net)"))
                if net_vol is None:
                    net_vol = long_vol - short_vol

                long_oi = _int(_first(raw, "多方未平倉口數", "LongOpenInterest", "long_oi", "OpenInterest(Long)")) or 0
                short_oi = _int(_first(raw, "空方未平倉口數", "ShortOpenInterest", "short_oi", "OpenInterest(Short)")) or 0
                net_oi = _int(_first(raw, "多空未平倉口數淨額", "NetOpenInterest", "net_oi", "OpenInterest(Net)"))
                if net_oi is None:
                    net_oi = long_oi - short_oi

                results.append({
                    "dataset": "taifex_futures_chip",
                    "date": date,
                    "event_time": event_time_rfc,
                    "available_time": avail_time_rfc,
                    "ingest_time": ingest_time_rfc,
                    "contract": contract,
                    "participant_group": participant,
                    "market": "TW",
                    "venue": "TAIFEX",
                    "long_volume": long_vol,
                    "short_volume": short_vol,
                    "net_volume": net_vol,
                    "long_open_interest": long_oi,
                    "short_open_interest": short_oi,
                    "net_open_interest": net_oi,
                    "contract_roll_day": roll_day,
                    "front_month_contract": front_month,
                    "source_dataset": source_dataset or resolved_ds,
                    "api_endpoint": resolved_endpoint,
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


# ---------------------------------------------------------------------------
# Governed point-in-time fundamentals (task GOVERNED-FEATURE-DATASETS-20261110)
#
# Monthly revenue, quarterly statements and daily valuation come from the
# TWSE/TPEx OpenAPI only (never the exchange websites). Every normalized row
# carries ``event_time`` (the period the figure describes), ``available_time``
# (the earliest time the figure may be used: the declared filing deadline or
# the same-day close) and ``ingest_time`` (when Pantheon read it), plus the
# declared ``availability_lag_days`` from the governed feature catalogue.
# ---------------------------------------------------------------------------

TW_MONTHLY_REVENUE_SCHEMA_HASH = "tw_monthly_revenue.v1"
TW_FINANCIAL_STATEMENT_SCHEMA_HASH = "tw_financial_statement.v1"
TW_VALUATION_SCHEMA_HASH = "tw_valuation_bwibbu.v1"
_TWSE_OPENDATA_BASE_URL = f"{TWSE_OPENAPI_BASE_URL}/opendata"
_TWSE_EXCHANGE_REPORT_BASE_URL = f"{TWSE_OPENAPI_BASE_URL}/exchangeReport"

TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS: Mapping[str, Mapping[str, Mapping[str, str]]] = {
    "tw_monthly_revenue": {
        "TWSE": {"source_dataset": "t187ap05_L", "endpoint": f"{_TWSE_OPENDATA_BASE_URL}/t187ap05_L"},
        "TPEx": {"source_dataset": "mopsfin_t187ap05_O", "endpoint": f"{TPEX_OPENAPI_BASE_URL}/mopsfin_t187ap05_O"},
    },
    "tw_financial_statement": {
        "TWSE": {
            "source_dataset": "t187ap06_L_ci",
            "endpoint": f"{_TWSE_OPENDATA_BASE_URL}/t187ap06_L_ci",
            "balance_source_dataset": "t187ap07_L_ci",
            "balance_endpoint": f"{_TWSE_OPENDATA_BASE_URL}/t187ap07_L_ci",
        },
        "TPEx": {
            "source_dataset": "mopsfin_t187ap06_O_ci",
            "endpoint": f"{TPEX_OPENAPI_BASE_URL}/mopsfin_t187ap06_O_ci",
            "balance_source_dataset": "mopsfin_t187ap07_O_ci",
            "balance_endpoint": f"{TPEX_OPENAPI_BASE_URL}/mopsfin_t187ap07_O_ci",
        },
    },
    "tw_valuation_bwibbu": {
        "TWSE": {"source_dataset": "BWIBBU_ALL", "endpoint": f"{_TWSE_EXCHANGE_REPORT_BASE_URL}/BWIBBU_ALL"},
        "TPEx": {
            "source_dataset": "tpex_mainboard_peratio_analysis",
            "endpoint": f"{TPEX_OPENAPI_BASE_URL}/tpex_mainboard_peratio_analysis",
        },
    },
}

_QUARTER_END_MONTH_DAY = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _roc_year_to_ad(value: Any) -> int | None:
    digits = re.sub(r"\D", "", _text(value))
    if not digits:
        return None
    year = int(digits)
    return year + 1911 if year < 1911 else year


def _month_end_iso(year: int, month: int) -> str:
    first_next = datetime(year + (month == 12), (month % 12) + 1, 1)
    return (first_next - timedelta(days=1)).date().isoformat()


def _fundamental_base(
    dataset: str,
    venue: str,
    symbol: str,
    row: Mapping[str, Any],
    *,
    source_dataset: str,
    api_endpoint: str,
    event_time: str,
    lag_days: int,
    available_time: str,
    ingest_time: str,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "symbol": symbol,
        "symbol_canonical": _symbol_canonical(symbol, venue),
        "market": "TW",
        "venue": venue,
        "name": _text(_first(row, "公司名稱", "CompanyName", "Name")),
        "event_time": event_time,
        "date": event_time,
        "available_time": available_time,
        "ingest_time": ingest_time,
        "availability_lag_days": lag_days,
        "source_dataset": source_dataset,
        "api_endpoint": api_endpoint,
        "raw_row": dict(row),
    }


_GOVERNED_DAILY_FLOW_DATASETS = {
    "tw_margin_short_balance": "tw_margin_transactions",
    "tw_institutional_flow": "tw_institutional_flows",
}


def _governed_daily_lag_days(dataset: str) -> int:
    from ..feature_catalogue import get_dataset_spec

    return get_dataset_spec(_GOVERNED_DAILY_FLOW_DATASETS[dataset]).availability_lag_days


def _fundamental_lag_available(event_time: str, lag_days: int, *, hour: int = 18) -> str:
    from ..feature_catalogue import default_available_time

    return default_available_time(event_time, lag_days, hour=hour)


def _version_available_time(
    row: Mapping[str, Any], event_time: str, lag_days: int, ingest_time: str, *, hour: int = 18
) -> str:
    """Per-version availability: never earlier than the lag floor nor than first knowledge.

    Uses the version's own publication date (``出表日期`` / ``PublishDate``) when the
    source states one; otherwise the conservative first-seen time (``ingest_time``).
    A later corrected version therefore never back-dates to the original deadline.
    """
    from ..feature_catalogue import parse_time, iso_utc

    floor = parse_time(_fundamental_lag_available(event_time, lag_days, hour=hour))
    known = None
    published = _roc_date_to_iso(_first(row, "出表日期", "PublishDate", "PublicationDate", "published_time", "publish_date"))
    if re.match(r"^\d{4}-\d{2}-\d{2}$", published):
        known = parse_time(_fundamental_lag_available(published, 0, hour=hour))
    else:
        try:
            known = parse_time(ingest_time)
        except Exception:
            known = None
    return iso_utc(max(floor, known) if known else floor)


def normalize_monthly_revenue_rows(
    venue: str,
    payload: Any,
    *,
    source_dataset: str,
    api_endpoint: str,
    ingest_time: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Normalize TWSE ``t187ap05_L`` / TPEx ``mopsfin_t187ap05_O`` monthly revenue rows."""
    from ..feature_catalogue import lag_days_for

    canonical = _canonical_venue(venue)
    ingested = ingest_time or _utc_now()
    lag = lag_days_for("tw_monthly_revenue", "revenue")
    out: list[dict[str, Any]] = []
    for row in _rows_from_payload(payload):
        symbol = _text(_first(row, "公司代號", "Code", "SecuritiesCompanyCode")).upper()
        ym = re.sub(r"\D", "", _text(_first(row, "資料年月", "DataYearMonth", "ReportYearMonth")))
        if not symbol or len(ym) < 5:
            continue
        year, month = _roc_year_to_ad(ym[:-2]), int(ym[-2:])
        if year is None or not 1 <= month <= 12:
            continue
        event_time = _month_end_iso(year, month)
        revenue = _float(_first(row, "營業收入-當月營收", "Revenue-CurrentMonth", "CurrentMonthRevenue"))
        if revenue is None:
            continue
        item = _fundamental_base(
            "tw_monthly_revenue", canonical, symbol, row,
            source_dataset=source_dataset, api_endpoint=api_endpoint, event_time=event_time,
            lag_days=lag, available_time=_version_available_time(row, event_time, lag, ingested), ingest_time=ingested,
        )
        item.update(
            revenue=revenue,
            revenue_previous_month=_float(_first(row, "營業收入-上月營收", "Revenue-PreviousMonth")),
            revenue_previous_year_month=_float(_first(row, "營業收入-去年當月營收", "Revenue-SameMonthLastYear")),
            mom=_float(_first(row, "營業收入-上月比較增減(%)", "Revenue-PercentageChangeFromPreviousMonth")),
            yoy=_float(_first(row, "營業收入-去年同月增減(%)", "Revenue-PercentageChangeFromSameMonthLastYear")),
            industry=_text(_first(row, "產業別", "Industry")),
        )
        out.append(item)
    return tuple(out)


def normalize_financial_statement_rows(
    venue: str,
    income_payload: Any,
    balance_payload: Any = None,
    *,
    source_dataset: str,
    api_endpoint: str,
    ingest_time: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Normalize quarterly income statements, joining parent equity for ROE.

    Figures are the period-to-date amounts as reported by MOPS; margins and ROE are
    ratios of those reported amounts and are not annualised. Q1-Q3 are available
    after the 45-day filing deadline, Q4 after the 90-day annual-report deadline.
    """
    from ..feature_catalogue import lag_days_for

    canonical = _canonical_venue(venue)
    ingested = ingest_time or _utc_now()
    base_lag = lag_days_for("tw_quarterly_statements", "eps")
    equity: dict[tuple[str, int, int], float] = {}
    for row in _rows_from_payload(balance_payload) if balance_payload is not None else ():
        symbol = _text(_first(row, "公司代號", "Code", "SecuritiesCompanyCode")).upper()
        year = _roc_year_to_ad(_first(row, "年度", "Year"))
        quarter = _int(_first(row, "季別", "Season", "Quarter"))
        value = _float(_first(row, "歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益", "權益總額", "權益總計"))
        if symbol and year and quarter and value:
            equity[(symbol, year, quarter)] = value
    out: list[dict[str, Any]] = []
    for row in _rows_from_payload(income_payload):
        symbol = _text(_first(row, "公司代號", "Code", "SecuritiesCompanyCode")).upper()
        year = _roc_year_to_ad(_first(row, "年度", "Year"))
        quarter = _int(_first(row, "季別", "Season", "Quarter"))
        if not symbol or not year or quarter not in _QUARTER_END_MONTH_DAY:
            continue
        month, day = _QUARTER_END_MONTH_DAY[quarter]
        event_time = f"{year:04d}-{month:02d}-{day:02d}"
        lag = 90 if quarter == 4 else base_lag
        revenue = _float(_first(row, "營業收入", "Revenue", "OperatingRevenue"))
        gross = _float(_first(row, "營業毛利（毛損）淨額", "營業毛利（毛損）", "GrossProfit"))
        operating = _float(_first(row, "營業利益（損失）", "OperatingIncome"))
        net_parent = _float(_first(row, "歸屬於母公司業主之淨利（損）", "NetIncomeAttributableToParent"))
        eps = _float(_first(row, "基本每股盈餘（元）", "EarningsPerShare", "BasicEarningsPerShare"))
        parent_equity = equity.get((symbol, year, quarter))
        item = _fundamental_base(
            "tw_financial_statement", canonical, symbol, row,
            source_dataset=source_dataset, api_endpoint=api_endpoint, event_time=event_time,
            lag_days=lag, available_time=_version_available_time(row, event_time, lag, ingested), ingest_time=ingested,
        )
        item.update(
            fiscal_year=year,
            fiscal_quarter=quarter,
            revenue=revenue,
            eps=eps,
            gross_margin=(gross / revenue) if gross is not None and revenue else None,
            operating_margin=(operating / revenue) if operating is not None and revenue else None,
            roe=(net_parent / parent_equity) if net_parent is not None and parent_equity else None,
        )
        if all(item[k] is None for k in ("eps", "gross_margin", "operating_margin", "roe")):
            continue
        out.append(item)
    return tuple(out)


def normalize_valuation_rows(
    venue: str,
    payload: Any,
    *,
    source_dataset: str,
    api_endpoint: str,
    ingest_time: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Normalize TWSE ``BWIBBU_ALL`` / TPEx P/E analysis rows (same-day close availability)."""
    canonical = _canonical_venue(venue)
    ingested = ingest_time or _utc_now()
    out: list[dict[str, Any]] = []
    for row in _rows_from_payload(payload):
        symbol = _text(_first(row, "Code", "SecuritiesCompanyCode", "股票代號", "證券代號")).upper()
        event_time = _roc_date_to_iso(_first(row, "Date", "資料日期", "date"))
        if not symbol or not re.match(r"^\d{4}-\d{2}-\d{2}$", event_time):
            continue
        pe = _float(_first(row, "PEratio", "PriceEarningRatio", "本益比"))
        pb = _float(_first(row, "PBratio", "PriceBookRatio", "股價淨值比"))
        dy = _float(_first(row, "DividendYield", "YieldRatio", "殖利率(%)"))
        if pe is None and pb is None and dy is None:
            continue
        item = _fundamental_base(
            "tw_valuation_bwibbu", canonical, symbol, row,
            source_dataset=source_dataset, api_endpoint=api_endpoint, event_time=event_time,
            lag_days=0, available_time=_version_available_time(row, event_time, 0, ingested), ingest_time=ingested,
        )
        item.update(pe_ratio=pe, pb_ratio=pb, dividend_yield=dy)
        out.append(item)
    return tuple(out)


def fetch_fundamental_rows(
    dataset: str,
    venue: str,
    *,
    fetch: Callable[[str], Any] | None = None,
    timeout_seconds: float = 30.0,
    ingest_time: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Fetch one governed fundamentals dataset from the official OpenAPI and normalize it.

    ``fetch`` is an injectable ``url -> parsed JSON`` transport; the default goes
    through the egress-guarded, OpenAPI-only official fetcher.
    """
    canonical = _canonical_venue(venue)
    try:
        spec = TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS[dataset][canonical]
    except KeyError as exc:
        raise ValueError(f"unsupported fundamentals dataset: dataset={dataset} venue={venue}") from exc

    def _get(url: str) -> Any:
        if fetch is not None:
            _assert_twse_tpex_openapi_only_url(url)
            return fetch(url)
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "pantheon-source-ingest/0.1"}
        )
        return _fetch_official(
            request,
            caller="source_ingest.taiwan_official",
            timeout_seconds=timeout_seconds,
            max_bytes=20971520,
            parse=lambda raw_bytes: json.loads(raw_bytes.decode("utf-8-sig")),
        )

    common = {
        "source_dataset": spec["source_dataset"],
        "api_endpoint": spec["endpoint"],
        "ingest_time": ingest_time,
    }
    payload = _get(spec["endpoint"])
    if dataset == "tw_monthly_revenue":
        return normalize_monthly_revenue_rows(canonical, payload, **common)
    if dataset == "tw_valuation_bwibbu":
        return normalize_valuation_rows(canonical, payload, **common)
    return normalize_financial_statement_rows(
        canonical, payload, _get(spec["balance_endpoint"]), **common
    )


def fundamental_rows_to_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    connector_id: str = TW_OFFICIAL_CONNECTOR_ID,
    trace_id: str = "",
) -> tuple[SourceRecord, ...]:
    """Wrap normalized fundamentals rows as SourceRecords the storage writer can persist."""
    records: list[SourceRecord] = []
    for row in rows:
        dataset = str(row["dataset"])
        row_hash = _stable_hash({"dataset": dataset, "venue": row["venue"], "row": dict(row)})
        symbol = str(row["symbol"])
        records.append(
            SourceRecord(
                source_id=f"tw-official:{dataset}:{row['venue']}:{symbol}:{row_hash}",
                connector_id=connector_id,
                source_type="market",
                title=f"TW official {dataset} {row['venue']} {symbol} {row['event_time']}",
                content_ref=f"tw-official://{dataset}/{row['venue']}/{symbol}/{row['event_time']}/{row_hash}",
                metadata={
                    "source_class": "official_reference",
                    "provider": "TWSE/TPEx",
                    "dataset": dataset,
                    "source_dataset": row.get("source_dataset"),
                    "venue": row["venue"],
                    "symbol": symbol,
                    "symbol_canonical": row.get("symbol_canonical"),
                    "date": row["event_time"],
                    "event_time": row["event_time"],
                    "available_time": row["available_time"],
                    "ingest_time": row["ingest_time"],
                    "availability_lag_days": row["availability_lag_days"],
                    "api_endpoint": row.get("api_endpoint"),
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
