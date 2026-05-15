"""Helpers for normalizing crypto venue and reference data into canonical models."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .models.dataset_lineage import NormalizedDataset, RawDataset
    from .models.security_master import SecurityMaster
    from services.execution.crypto_symbol_utils import extract_kraken_base_asset
except ImportError:  # pragma: no cover - supports direct file loading in tests
    module_root = Path(__file__).resolve().parent
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    repo_root = module_root.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from models.dataset_lineage import NormalizedDataset, RawDataset
    from models.security_master import SecurityMaster
    from services.execution.crypto_symbol_utils import extract_kraken_base_asset


def build_crypto_security_master(listing: dict[str, Any]) -> SecurityMaster:
    venue = _normalize_crypto_venue(str(listing.get("venue", "KRAKEN")))
    base_asset = str(listing["base_asset"]).strip().upper()
    quote_asset = str(listing["quote_asset"]).strip().upper()
    instrument_type = str(listing.get("instrument_type", "spot")).strip().lower()
    native_symbol = f"{base_asset}/{quote_asset}"
    altname = f"{base_asset}{quote_asset}"
    metadata_json = {
        "reference_source": listing.get("governance_metadata", {}).get("source_key"),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "instrument_type": instrument_type,
        "pair_status": listing.get("pair_status", "online"),
        "coingecko_id": listing.get("coingecko_id"),
    }
    return SecurityMaster(
        security_id=f"SEC-CRYPTO-{altname}-{venue}",
        market="CRYPTO",
        venue=venue,
        symbol_native=native_symbol,
        symbol_canonical=f"{altname}.{venue}",
        asset_type="crypto",
        currency=quote_asset,
        metadata_json=metadata_json,
    )


def build_kraken_raw_dataset(
    *,
    dataset_id: str,
    instrument_scope: list[str],
    coverage_start: str,
    coverage_end: str,
    ingest_time: str,
    storage_ref: str,
    checksum: str,
    dataset_type: str,
    source_class: str = "broker_execution",
) -> RawDataset:
    return RawDataset(
        dataset_id=dataset_id,
        source_class=source_class,
        market="CRYPTO",
        instrument_scope=list(instrument_scope),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        ingest_time=ingest_time,
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": "Kraken",
            "dataset_type": dataset_type,
            "governed_role": "crypto_venue_canonical",
        },
    )


def build_crypto_normalized_dataset(
    *,
    dataset_id: str,
    parent_raw_dataset_id: str,
    storage_ref: str,
    checksum: str,
    symbol_mapping_version: str,
    reference_join_version: str,
    provider: str = "Kraken + CoinGecko",
) -> NormalizedDataset:
    return NormalizedDataset(
        dataset_id=dataset_id,
        parent_raw_dataset_id=parent_raw_dataset_id,
        normalization_version="crypto-v1",
        symbol_mapping_version=symbol_mapping_version,
        corp_action_version=reference_join_version,
        calendar_version=None,
        available_time_policy="at_ingest",
        storage_ref=storage_ref,
        checksum=checksum,
        metadata_json={
            "provider": provider,
            "market_boundary": "CRYPTO",
            "execution_truth_provider": "Kraken",
            "reference_provider": "CoinGecko",
            "source_role": "venue_canonical_plus_reference_join",
        },
    )


def build_crypto_dataset_lineage_source(
    *,
    dataset_name: str,
    source_key: str,
    source_class: str,
    frequency: str,
    symbol_universe: list[str],
) -> dict[str, Any]:
    return {
        "dataset_name": dataset_name,
        "market": "CRYPTO",
        "source_key": source_key,
        "source_class": source_class,
        "frequency": frequency,
        "symbol_universe": list(symbol_universe),
    }


def join_kraken_quote_with_reference(
    quote_snapshots: list[dict[str, Any]],
    asset_metadata: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metadata_index = _build_coingecko_asset_index(asset_metadata)
    joined_rows = []
    for quote in quote_snapshots:
        symbol = str(quote["symbol"]).strip().upper()
        base_asset = extract_kraken_base_asset(symbol)
        metadata = metadata_index.get(base_asset) or metadata_index.get(symbol)
        if metadata is None:
            raise KeyError(f"missing CoinGecko metadata for {symbol}")
        joined_rows.append(
            {
                "security_id": f"SEC-CRYPTO-{symbol}-KRAKEN",
                "symbol_canonical": f"{symbol}.KRAKEN",
                "market": "CRYPTO",
                "venue": "KRAKEN",
                "quote_timestamp": quote.get("ts"),
                "quote_last": quote.get("last"),
                "quote_close": quote.get("close"),
                "quote_bid": quote.get("bid"),
                "quote_ask": quote.get("ask"),
                "quote_volume": quote.get("volume"),
                "execution_provider": "Kraken",
                "quote_transport": quote.get("transport", "rest"),
                "quote_channel": quote.get("channel", "rest_snapshot"),
                "replay_source": quote.get("replay_source", "rest_snapshot"),
                "runtime_replay_ready": bool(quote.get("is_replayable", False)),
                "reference_provider": "CoinGecko",
                "coingecko_id": metadata.get("coingecko_id"),
                "asset_name": metadata.get("asset_name"),
                "market_cap_rank": metadata.get("market_cap_rank"),
                "categories": list(metadata.get("categories", [])),
            }
        )
    return joined_rows


def _build_coingecko_asset_index(asset_metadata: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metadata_index: dict[str, dict[str, Any]] = {}
    for record in asset_metadata:
        normalized = dict(record)
        symbol = str(normalized["symbol"]).strip().upper()
        metadata_index[symbol] = normalized
    return metadata_index
def _normalize_crypto_venue(venue: str) -> str:
    normalized = str(venue or "").strip().upper()
    if normalized in {"KRAKEN", "CRYPTO"}:
        return "KRAKEN"
    raise ValueError(f"unsupported crypto venue: {venue}")
