"""Helpers for normalizing Taiwan reference data into canonical data-plane models."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .models.security_master import SecurityMaster
    from .models.market_calendar_session import MarketCalendarSession
except ImportError:  # pragma: no cover - supports direct file loading in tests
    module_root = Path(__file__).resolve().parent
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    from models.security_master import SecurityMaster
    from models.market_calendar_session import MarketCalendarSession


def build_tw_security_master(listing: dict[str, Any]) -> SecurityMaster:
    venue = str(listing["venue"]).upper()
    suffix = "TWSE" if venue == "TWSE" else "TPEX"
    symbol = str(listing["symbol"])
    metadata_json = {
        "reference_source": listing.get("governance_metadata", {}).get("source_key"),
        "isin": listing.get("isin"),
        "industry": listing.get("industry"),
        "listing_date": listing.get("listing_date"),
        "company_name": listing.get("company_name"),
    }
    return SecurityMaster(
        security_id=f"SEC-TW-{symbol}-{suffix}",
        market="TW",
        venue=venue,
        symbol_native=symbol,
        symbol_canonical=f"{symbol}.{suffix}",
        asset_type="equity",
        currency="TWD",
        metadata_json=metadata_json,
    )


def build_tw_calendar_session(
    trade_date: str,
    *,
    venue: str = "TWSE",
    holiday_flag: bool = False,
    early_close: bool = False,
    session_open: str = "09:00:00",
    session_close: str = "13:30:00",
) -> MarketCalendarSession:
    if holiday_flag:
        session_open = ""
        session_close = ""
    elif early_close:
        session_close = "12:30:00"
    return MarketCalendarSession(
        market="TW",
        trade_date=trade_date,
        session_open=session_open,
        session_close=session_close,
        timezone="Asia/Taipei",
        early_close_flag=early_close,
        holiday_flag=holiday_flag,
        metadata_json={"venue": venue.upper(), "early_close": early_close},
    )


def build_tw_dataset_lineage_source(
    *,
    dataset_name: str,
    source_key: str,
    source_class: str,
    frequency: str,
    symbol_universe: list[str],
) -> dict[str, Any]:
    return {
        "dataset_name": dataset_name,
        "market": "TW",
        "source_key": source_key,
        "source_class": source_class,
        "frequency": frequency,
        "symbol_universe": list(symbol_universe),
    }
