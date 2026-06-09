"""Helpers for normalizing Taiwan reference data into canonical data-plane models."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .models.dataset_lineage import FeatureDataset, NormalizedDataset, RawDataset
    from .models.security_master import SecurityMaster
    from .models.market_calendar_session import MarketCalendarSession
except ImportError:  # pragma: no cover - supports direct file loading in tests
    module_root = Path(__file__).resolve().parent
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    from models.dataset_lineage import FeatureDataset, NormalizedDataset, RawDataset
    from models.security_master import SecurityMaster
    from models.market_calendar_session import MarketCalendarSession


_TW_VENUE_ALIASES = {
    "TW": "TWSE",
    "TWSE": "TWSE",
    "TSE": "TWSE",
    "TPEX": "TPEx",
    "TWO": "TPEx",
    "OTC": "TPEx",
    "TPEX": "TPEx",
    "TPEx": "TPEx",
}

_TW_BROKER_VENUE_ALIASES = {
    "TSE": "TWSE",
    "OTC": "TPEx",
}


def normalize_tw_venue(venue: str) -> str:
    normalized = str(venue or "").strip()
    if not normalized:
        raise ValueError("venue is required")
    resolved = _TW_VENUE_ALIASES.get(normalized.upper())
    if resolved is None:
        raise ValueError(f"unsupported Taiwan venue: {venue}")
    return resolved


def build_tw_symbol_mapping_record(
    *,
    symbol: str,
    venue: str,
    market_segment: str | None = None,
    tej_symbol: str | None = None,
    source_keys: list[str] | None = None,
) -> dict[str, Any]:
    canonical_venue = normalize_tw_venue(venue)
    native_symbol = str(symbol).strip().upper()
    if not native_symbol:
        raise ValueError("symbol is required")
    resolved_segment = _infer_market_segment(
        {
            "venue": canonical_venue,
            "market_segment": market_segment,
        }
    )
    return {
        "symbol_native": native_symbol,
        "symbol_canonical": f"{native_symbol}.{_tw_canonical_suffix(canonical_venue)}",
        "market": "TW",
        "venue": canonical_venue,
        "market_segment": resolved_segment,
        "tej_symbol": str(tej_symbol).strip().upper() if tej_symbol else native_symbol,
        "source_keys": list(source_keys or []),
    }


def build_tw_security_master(listing: dict[str, Any]) -> SecurityMaster:
    venue = normalize_tw_venue(str(listing["venue"]))
    suffix = _tw_canonical_suffix(venue)
    symbol = str(listing["symbol"]).strip().upper()
    market_segment = _infer_market_segment(listing)
    metadata_json = {
        "reference_source": listing.get("governance_metadata", {}).get("source_key"),
        "isin": listing.get("isin"),
        "industry": listing.get("industry"),
        "listing_date": listing.get("listing_date"),
        "company_name": listing.get("company_name"),
        "market_segment": market_segment,
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


def build_shioaji_raw_dataset(
    *,
    dataset_id: str,
    instrument_scope: list[str],
    coverage_start: str,
    coverage_end: str,
    ingest_time: str,
    storage_ref: str,
    checksum: str,
    frequency: str = "tick",
) -> RawDataset:
    return RawDataset(
        dataset_id=dataset_id,
        source_class="broker_execution",
        market="TW",
        instrument_scope=list(instrument_scope),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        ingest_time=ingest_time,
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": "Shioaji",
            "dataset_type": "quote_snapshot",
            "frequency": frequency,
            "governed_role": "tw_broker_quote_boundary",
        },
    )


def build_mops_raw_dataset(
    *,
    dataset_id: str,
    instrument_scope: list[str],
    coverage_start: str,
    coverage_end: str,
    ingest_time: str,
    storage_ref: str,
    checksum: str,
    route_ids: list[str],
) -> RawDataset:
    return RawDataset(
        dataset_id=dataset_id,
        source_class="official_reference",
        market="TW",
        instrument_scope=list(instrument_scope),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        ingest_time=ingest_time,
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": "MOPS",
            "dataset_type": "official_disclosure",
            "frequency": "event",
            "route_ids": list(route_ids),
            "governed_role": "tw_official_disclosure_truth",
        },
    )


def build_tej_raw_dataset(
    *,
    dataset_id: str,
    instrument_scope: list[str],
    coverage_start: str,
    coverage_end: str,
    ingest_time: str,
    storage_ref: str,
    checksum: str,
    dataset_codes: list[str],
    frequency: str = "daily",
) -> RawDataset:
    return RawDataset(
        dataset_id=dataset_id,
        source_class="research_grade",
        market="TW",
        instrument_scope=list(instrument_scope),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        ingest_time=ingest_time,
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": "TEJ API",
            "dataset_type": "vendor_research_dataset",
            "frequency": frequency,
            "dataset_codes": list(dataset_codes),
            "governed_role": "tw_research_grade_fundamentals_ownership_market_data",
            "does_not_replace_official_disclosure_truth": True,
        },
    )


def build_tw_normalized_dataset(
    *,
    dataset_id: str,
    parent_raw_dataset_id: str,
    storage_ref: str,
    checksum: str,
    symbol_mapping_version: str,
    calendar_version: str,
    disclosure_join_version: str,
    fundamentals_join_version: str,
    source_keys: list[str] | None = None,
) -> NormalizedDataset:
    return NormalizedDataset(
        dataset_id=dataset_id,
        parent_raw_dataset_id=parent_raw_dataset_id,
        normalization_version="tw-equity-v1",
        symbol_mapping_version=symbol_mapping_version,
        corp_action_version=disclosure_join_version,
        calendar_version=calendar_version,
        available_time_policy="at_ingest",
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "market_boundary": "TW",
            "provider": "Shioaji + TWSE/TPEx/MOPS/TEJ",
            "source_role": "broker_quote_plus_official_reference_join",
            "market_segments": ["listed", "otc"],
            "disclosure_join_version": disclosure_join_version,
            "fundamentals_join_version": fundamentals_join_version,
            "source_keys": list(source_keys or ["shioaji", "twse", "tpex", "mops", "tej"]),
        },
    )


def build_tw_broker_top_row(
    *,
    date: str,
    symbol: str,
    source: str,
    side: str,
    rank: int,
    broker: str,
    buy_qty: int,
    sell_qty: int,
    net_qty: int | None = None,
    venue: str = "TWSE",
    available_time: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    native_symbol = str(symbol or "").strip().upper()
    if not native_symbol:
        raise ValueError("symbol is required")
    source_text = str(source or "").strip()
    if not source_text:
        raise ValueError("source is required")
    broker_text = str(broker or "").strip()
    if not broker_text:
        raise ValueError("broker is required")
    side_text = str(side or "").strip().lower()
    if side_text not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    rank_value = int(rank)
    if rank_value <= 0:
        raise ValueError("rank must be > 0")
    buy_value = int(buy_qty)
    sell_value = int(sell_qty)
    canonical_venue = normalize_tw_venue(venue)
    resolved_net = int(net_qty) if net_qty is not None else buy_value - sell_value
    return {
        "date": str(date),
        "symbol": native_symbol,
        "symbol_canonical": f"{native_symbol}.{_tw_canonical_suffix(canonical_venue)}",
        "market": "TW",
        "venue": canonical_venue,
        "source": source_text,
        "side": side_text,
        "rank": rank_value,
        "broker": broker_text,
        "buy_qty": buy_value,
        "sell_qty": sell_value,
        "net_qty": resolved_net,
        "available_time": available_time,
        "source_url": source_url,
    }


def build_tw_broker_top_normalized_dataset(
    *,
    dataset_id: str,
    parent_raw_dataset_id: str,
    storage_ref: str,
    checksum: str,
    symbol_mapping_version: str,
    calendar_version: str,
    source_keys: list[str] | None = None,
    top_n: int = 15,
) -> NormalizedDataset:
    return NormalizedDataset(
        dataset_id=dataset_id,
        parent_raw_dataset_id=parent_raw_dataset_id,
        normalization_version="tw-broker-top-v1",
        symbol_mapping_version=symbol_mapping_version,
        calendar_version=calendar_version,
        available_time_policy="at_ingest",
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "market_boundary": "TW",
            "provider": "Yahoo Taiwan + TEJ",
            "source_role": "active_universe_broker_top_summary",
            "table_name": "tw_broker_top",
            "top_n": int(top_n),
            "row_contract": "date,symbol,source,side,rank,broker,buy_qty,sell_qty,net_qty",
            "source_keys": list(source_keys or ["yahoo_tw_broker_top15", "tej_twn_absr20", "tej_twn_amtop1"]),
            "archive_behavior": "skip_detail_updates",
        },
    )


def build_tw_broker_top_feature_dataset(
    *,
    dataset_id: str,
    parent_normalized_dataset_id: str,
    storage_ref: str,
    checksum: str,
    feature_spec_version: str = "tw-broker-top-features-v1",
    label_spec_version: str = "none",
) -> FeatureDataset:
    return FeatureDataset(
        dataset_id=dataset_id,
        parent_normalized_dataset_id=parent_normalized_dataset_id,
        feature_spec_version=feature_spec_version,
        label_spec_version=label_spec_version,
        point_in_time_rule="use only rows with available_time <= feature_as_of_time",
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "market_boundary": "TW",
            "parent_table": "tw_broker_top",
            "features": [
                "top_broker_net_qty",
                "top_broker_concentration",
                "main_broker_consecutive_buy_days",
                "broker_flow_reversal",
            ],
        },
    )


def join_tw_quote_with_reference(
    quote_snapshots: list[dict[str, Any]],
    listings: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    tej_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    listing_index = {}
    for listing in listings:
        mapping = build_tw_symbol_mapping_record(
            symbol=listing["symbol"],
            venue=listing["venue"],
            market_segment=listing.get("market_segment"),
            source_keys=[listing.get("governance_metadata", {}).get("source_key", "")],
        )
        listing_index[(mapping["symbol_native"], mapping["venue"])] = {
            "listing": dict(listing),
            "mapping": mapping,
        }

    disclosure_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for disclosure in disclosures:
        disclosure_index[str(disclosure["symbol"]).strip().upper()].append(dict(disclosure))

    tej_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in tej_records:
        tej_index[str(record["symbol"]).strip().upper()].append(dict(record))

    joined_rows = []
    for quote in quote_snapshots:
        native_symbol = str(quote["symbol"]).strip().upper()
        quote_venue = _normalize_quote_venue(quote.get("venue"))
        key = (native_symbol, quote_venue)
        listing_entry = listing_index.get(key)
        if listing_entry is None:
            raise KeyError(f"missing Taiwan listing for {native_symbol}.{_tw_canonical_suffix(quote_venue)}")

        mapping = listing_entry["mapping"]
        symbol_disclosures = sorted(
            disclosure_index.get(native_symbol, []),
            key=lambda item: str(item.get("disclosure_date", "")),
        )
        symbol_tej = sorted(
            tej_index.get(native_symbol, []),
            key=lambda item: str(item.get("as_of_date", "")),
        )
        latest_tej = symbol_tej[-1] if symbol_tej else None
        joined_rows.append(
            {
                "security_id": f"SEC-TW-{native_symbol}-{_tw_canonical_suffix(quote_venue)}",
                "symbol_native": native_symbol,
                "symbol_canonical": mapping["symbol_canonical"],
                "market": "TW",
                "venue": quote_venue,
                "market_segment": mapping["market_segment"],
                "quote_timestamp": quote.get("ts"),
                "quote_close": quote.get("close"),
                "quote_bid": quote.get("bid"),
                "quote_ask": quote.get("ask"),
                "quote_day_volume": quote.get("day_volume"),
                "official_listing_source": listing_entry["listing"].get("governance_metadata", {}).get("source_key"),
                "disclosure_count": len(symbol_disclosures),
                "latest_disclosure_date": symbol_disclosures[-1]["disclosure_date"] if symbol_disclosures else None,
                "disclosure_filing_codes": [item["filing_code"] for item in symbol_disclosures],
                "tej_dataset_codes": [item["dataset_code"] for item in symbol_tej],
                "tej_latest_as_of_date": latest_tej["as_of_date"] if latest_tej else None,
                "tej_latest_values": latest_tej.get("values", {}) if latest_tej else {},
            }
        )
    return joined_rows


def _tw_canonical_suffix(venue: str) -> str:
    return "TWSE" if venue == "TWSE" else "TPEX"


def _normalize_quote_venue(venue: Any) -> str:
    normalized = str(venue or "").strip().upper()
    if normalized in _TW_BROKER_VENUE_ALIASES:
        return _TW_BROKER_VENUE_ALIASES[normalized]
    return normalize_tw_venue(normalized)


def _infer_market_segment(record: dict[str, Any]) -> str:
    explicit = record.get("market_segment") or record.get("segment") or record.get("board")
    if explicit not in (None, ""):
        normalized = str(explicit).strip().lower()
        if normalized in {"listed", "primary", "上市"}:
            return "listed"
        if normalized in {"otc", "tpex", "otc_board", "上櫃"}:
            return "otc"
    return "listed" if normalize_tw_venue(str(record.get("venue", "TWSE"))) == "TWSE" else "otc"
