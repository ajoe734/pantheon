"""Helpers for normalizing US equities reference and market-data payloads."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

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


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


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


def build_us_price_daily_row(
    *,
    symbol: str,
    trade_date: str,
    open: float | int | str | None,
    high: float | int | str | None,
    low: float | int | str | None,
    close: float | int | str | None,
    volume: int | str | None,
    provider: str,
    available_time: str | None = None,
    currency: str = "USD",
    adjustment_policy: str = "provider_close_unadjusted_until_corporate_action_policy_verified",
    raw_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_value = _symbol(symbol)
    return {
        "schema_version": "us_price_daily.v1",
        "target_table": "us_price_daily",
        "provider": provider,
        "symbol": symbol_value,
        "symbol_canonical": f"{symbol_value}.US",
        "trade_date": trade_date,
        "open": _float_or_none(open),
        "high": _float_or_none(high),
        "low": _float_or_none(low),
        "close": _float_or_none(close),
        "volume": _int_or_none(volume),
        "currency": currency,
        "adjustment_policy": adjustment_policy,
        "available_time": available_time or trade_date,
        "raw_row": dict(raw_row or {}),
    }


def build_sec_filing_event_row(
    *,
    cik: str,
    accession_number: str,
    form_type: str,
    filing_date: str,
    symbol: str | None = None,
    report_date: str | None = None,
    accepted_at: str | None = None,
    primary_document_url: str | None = None,
    company_name: str | None = None,
    raw_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_value = _symbol(symbol) if symbol else ""
    return {
        "schema_version": "sec_filing_event.v1",
        "target_table": "sec_filing_event",
        "provider": "SEC EDGAR",
        "cik": str(cik).zfill(10),
        "symbol": symbol_value or None,
        "symbol_canonical": f"{symbol_value}.US" if symbol_value else None,
        "company_name": company_name,
        "accession_number": accession_number,
        "form_type": form_type,
        "filing_date": filing_date,
        "report_date": report_date,
        "accepted_at": accepted_at,
        "available_time": accepted_at or filing_date,
        "primary_document_url": primary_document_url,
        "raw_row": dict(raw_row or {}),
    }


def build_sec_company_fact_row(
    *,
    cik: str,
    taxonomy: str,
    concept: str,
    unit: str,
    value: Any,
    filed_date: str,
    symbol: str | None = None,
    end_date: str | None = None,
    fiscal_year: int | str | None = None,
    fiscal_period: str | None = None,
    form_type: str | None = None,
    company_name: str | None = None,
    raw_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_value = _symbol(symbol) if symbol else ""
    return {
        "schema_version": "sec_company_fact.v1",
        "target_table": "sec_company_fact",
        "provider": "SEC EDGAR",
        "cik": str(cik).zfill(10),
        "symbol": symbol_value or None,
        "symbol_canonical": f"{symbol_value}.US" if symbol_value else None,
        "company_name": company_name,
        "taxonomy": taxonomy,
        "concept": concept,
        "unit": unit,
        "value": value,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "form_type": form_type,
        "filed_date": filed_date,
        "end_date": end_date,
        "available_time": filed_date,
        "raw_row": dict(raw_row or {}),
    }


def build_macro_fred_observation_row(
    *,
    series_id: str,
    observation_date: str,
    value: float | int | str,
    frequency: str,
    release_lag_days: int | None = None,
    realtime_start: str | None = None,
    realtime_end: str | None = None,
    raw_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "macro_fred_observation.v1",
        "target_table": "macro_fred_observation",
        "provider": "FRED",
        "series_id": str(series_id).strip().upper(),
        "observation_date": observation_date,
        "value": _float_or_none(value),
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        "frequency": frequency,
        "release_lag_days": release_lag_days,
        "available_time": realtime_start or observation_date,
        "raw_row": dict(raw_row or {}),
    }


def build_us_short_volume_daily_row(
    *,
    symbol: str,
    trade_date: str,
    short_volume: int | str | None,
    short_exempt_volume: int | str | None,
    total_volume: int | str | None,
    market: str | None = None,
    raw_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_value = _symbol(symbol)
    short = _int_or_none(short_volume)
    total = _int_or_none(total_volume)
    return {
        "schema_version": "us_short_volume_daily.v1",
        "target_table": "us_short_volume_daily",
        "provider": "FINRA",
        "symbol": symbol_value,
        "symbol_canonical": f"{symbol_value}.US",
        "trade_date": trade_date,
        "market": market,
        "short_volume": short,
        "short_exempt_volume": _int_or_none(short_exempt_volume),
        "total_volume": total,
        "short_volume_ratio": _ratio(short, total),
        "available_time": trade_date,
        "raw_row": dict(raw_row or {}),
    }
