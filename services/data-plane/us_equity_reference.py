"""Helpers for normalizing US equities reference and market-data payloads."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .models.dataset_lineage import NormalizedDataset, RawDataset
    from .models.market_calendar_session import MarketCalendarSession
    from .models.security_master import SecurityMaster
except ImportError:  # pragma: no cover - supports direct file loading in tests
    module_root = Path(__file__).resolve().parent
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    from models.dataset_lineage import NormalizedDataset, RawDataset
    from models.market_calendar_session import MarketCalendarSession
    from models.security_master import SecurityMaster


_PRIMARY_EXCHANGE_SUFFIX = {
    "NASDAQ": "US",
    "NYSE": "US",
    "ARCA": "US",
    "BATS": "US",
}


def build_us_security_master(listing: dict[str, Any]) -> SecurityMaster:
    venue = str(listing["venue"]).upper()
    symbol = str(listing["symbol"]).upper()
    canonical_suffix = _PRIMARY_EXCHANGE_SUFFIX.get(venue, "US")
    metadata_json = {
        "reference_source": listing.get("governance_metadata", {}).get("source_key"),
        "isin": listing.get("isin"),
        "cusip": listing.get("cusip"),
        "figi": listing.get("figi"),
        "mic": listing.get("mic"),
        "company_name": listing.get("company_name"),
    }
    return SecurityMaster(
        security_id=f"SEC-US-{symbol}-{venue}",
        market="US",
        venue=venue,
        symbol_native=symbol,
        symbol_canonical=f"{symbol}.{canonical_suffix}",
        asset_type=listing.get("asset_type", "equity"),
        currency=listing.get("currency", "USD"),
        metadata_json=metadata_json,
    )


def build_us_calendar_session(
    trade_date: str,
    *,
    venue: str = "NYSE",
    holiday_flag: bool = False,
    early_close: bool = False,
    session_open: str = "09:30:00",
    session_close: str = "16:00:00",
) -> MarketCalendarSession:
    if holiday_flag:
        session_open = ""
        session_close = ""
    elif early_close:
        session_close = "13:00:00"
    return MarketCalendarSession(
        market="US",
        trade_date=trade_date,
        session_open=session_open,
        session_close=session_close,
        timezone="America/New_York",
        early_close_flag=early_close,
        holiday_flag=holiday_flag,
        metadata_json={"venue": venue.upper(), "early_close": early_close},
    )


def build_polygon_raw_dataset(
    *,
    dataset_id: str,
    instrument_scope: list[str],
    coverage_start: str,
    coverage_end: str,
    ingest_time: str,
    storage_ref: str,
    checksum: str,
    dataset_type: str,
    vendor: str = "Massive / Polygon",
) -> RawDataset:
    return RawDataset(
        dataset_id=dataset_id,
        source_class="research_grade",
        market="US",
        instrument_scope=list(instrument_scope),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        ingest_time=ingest_time,
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": vendor,
            "dataset_type": dataset_type,
            "governed_role": "us_research_grade_primary",
        },
    )


def build_us_normalized_dataset(
    *,
    dataset_id: str,
    parent_raw_dataset_id: str,
    storage_ref: str,
    checksum: str,
    symbol_mapping_version: str,
    corp_action_version: str,
    calendar_version: str,
    provider: str = "Massive / Polygon",
) -> NormalizedDataset:
    return NormalizedDataset(
        dataset_id=dataset_id,
        parent_raw_dataset_id=parent_raw_dataset_id,
        normalization_version="us-equity-v1",
        symbol_mapping_version=symbol_mapping_version,
        corp_action_version=corp_action_version,
        calendar_version=calendar_version,
        available_time_policy="at_ingest",
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": provider,
            "market_boundary": "US",
            "symbol_canonical_suffix": "US",
            "source_role": "primary_research_grade",
        },
    )


def build_us_dataset_lineage_source(
    *,
    dataset_name: str,
    source_key: str,
    source_class: str,
    frequency: str,
    symbol_universe: list[str],
) -> dict[str, Any]:
    return {
        "dataset_name": dataset_name,
        "market": "US",
        "source_key": source_key,
        "source_class": source_class,
        "frequency": frequency,
        "symbol_universe": list(symbol_universe),
    }
