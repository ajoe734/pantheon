"""SecurityMaster model.

Canonical definition of a tradable security identity.
Based on Pantheon_Market_Data_Scope_and_Source_Plan_v1 §6.1
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timezone
from enum import Enum
import json


class AssetType(str, Enum):
    """Supported asset types."""
    EQUITY = "equity"
    ETF = "etf"
    FUTURE = "future"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"
    BOND = "bond"
    INDEX = "index"
    WARRANT = "warrant"
    RIGHT = "right"


class ListingStatus(str, Enum):
    """Security listing lifecycle."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"
    PENDING = "pending"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SecurityMaster:
    """Canonical security identity.

    Attributes:
        security_id: Globally unique identifier (e.g., "SEC-US0378331005").
        market: Market code (e.g., "US", "TW", "CRYPTO").
        venue: Primary venue / exchange code (e.g., "NASDAQ", "TWSE").
        symbol_native: Native symbol as used by the venue (e.g., "AAPL", "2330").
        symbol_canonical: Normalized cross-venue symbol (e.g., ISIN or internal canonical form).
        asset_type: One of the AssetType enum values.
        currency: ISO 4217 currency code (e.g., "USD", "TWD").
        underlying_id: For derivatives, references the underlying security_id; null for spot.
        listing_status: Current lifecycle status.
        metadata_json: Free-form venue or market-specific metadata.
        created_at: ISO 8601 timestamp of record creation.
        updated_at: ISO 8601 timestamp of last update.
    """
    security_id: str
    market: str
    venue: str
    symbol_native: str
    symbol_canonical: str
    asset_type: str
    currency: str
    underlying_id: Optional[str] = None
    listing_status: str = ListingStatus.ACTIVE.value
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "market": self.market,
            "venue": self.venue,
            "symbol_native": self.symbol_native,
            "symbol_canonical": self.symbol_canonical,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "underlying_id": self.underlying_id,
            "listing_status": self.listing_status,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecurityMaster":
        return cls(
            security_id=data["security_id"],
            market=data["market"],
            venue=data["venue"],
            symbol_native=data["symbol_native"],
            symbol_canonical=data["symbol_canonical"],
            asset_type=data["asset_type"],
            currency=data["currency"],
            underlying_id=data.get("underlying_id"),
            listing_status=data.get("listing_status", ListingStatus.ACTIVE.value),
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
            updated_at=data.get("updated_at") or _utc_now_iso(),
        )

    @staticmethod
    def validate(security: "SecurityMaster") -> tuple[bool, list[str]]:
        errors = []
        if not security.security_id:
            errors.append("Missing security_id")
        if not security.market:
            errors.append("Missing market")
        if not security.venue:
            errors.append("Missing venue")
        if not security.symbol_native:
            errors.append("Missing symbol_native")
        if not security.symbol_canonical:
            errors.append("Missing symbol_canonical")
        if not security.asset_type:
            errors.append("Missing asset_type")
        elif security.asset_type not in {item.value for item in AssetType}:
            errors.append(f"Invalid asset_type: {security.asset_type}")
        if not security.currency:
            errors.append("Missing currency")
        if security.listing_status not in {item.value for item in ListingStatus}:
            errors.append(f"Invalid listing_status: {security.listing_status}")
        return len(errors) == 0, errors
