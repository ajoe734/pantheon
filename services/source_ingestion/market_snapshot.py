"""Market snapshot domain models and store facade.

Re-exports core market snapshot projection components from requirement_state
to provide a dedicated market snapshot interface for source ingestion.
"""

from __future__ import annotations

from .requirement_state import (
    MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION,
    MARKET_SNAPSHOT_CHECKSUM_ALGORITHM,
    MARKET_SNAPSHOT_SCHEMA_VERSION,
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotPoint,
    MarketSnapshotStateError,
)

__all__ = [
    "MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION",
    "MARKET_SNAPSHOT_CHECKSUM_ALGORITHM",
    "MARKET_SNAPSHOT_SCHEMA_VERSION",
    "LatestMarketSnapshot",
    "LatestMarketSnapshotStore",
    "MarketSnapshotPoint",
    "MarketSnapshotStateError",
]
