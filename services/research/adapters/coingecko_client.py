"""Governed CoinGecko adapter for crypto reference and research metadata."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CoinGeckoSourceSpec:
    key: str
    base_url: str
    source_class: str
    governance_context: str


@dataclass
class CoinGeckoGovernanceMetadata:
    source_key: str
    source_class: str
    api_endpoint: str
    retrieved_at: str
    governance_context: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoinGeckoAssetRecord:
    coingecko_id: str
    symbol: str
    asset_name: str
    market_cap_rank: Optional[int]
    categories: list[str]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoinGeckoClient:
    """Thin client for governed CoinGecko reference metadata."""

    SOURCE_SPEC = CoinGeckoSourceSpec(
        key="coingecko",
        base_url="https://api.coingecko.com/api/v3",
        source_class="research_grade",
        governance_context="Reference / metadata source for crypto research only; does not replace Kraken execution truth",
    )

    def __init__(self, rate_limit_delay: float = 0.25):
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0.0

    def get_json(
        self,
        resource_path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        url = f"{self.SOURCE_SPEC.base_url.rstrip('/')}/{resource_path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)

        request = Request(url, headers=headers or {})
        self._last_request_time = time.time()
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def build_metadata(self, api_endpoint: str) -> CoinGeckoGovernanceMetadata:
        return CoinGeckoGovernanceMetadata(
            source_key=self.SOURCE_SPEC.key,
            source_class=self.SOURCE_SPEC.source_class,
            api_endpoint=api_endpoint,
            retrieved_at=_utc_now_iso(),
            governance_context=self.SOURCE_SPEC.governance_context,
        )

    def normalize_asset(self, record: dict[str, Any], *, api_endpoint: str = "coingecko://coins/markets") -> CoinGeckoAssetRecord:
        coingecko_id = str(record.get("id") or "")
        symbol = str(record.get("symbol") or "").upper()
        asset_name = str(record.get("name") or "")
        if not coingecko_id or not symbol or not asset_name:
            raise ValueError("CoinGecko asset requires id, symbol, and name")
        metadata = self.build_metadata(api_endpoint)
        categories = record.get("categories") or []
        return CoinGeckoAssetRecord(
            coingecko_id=coingecko_id,
            symbol=symbol,
            asset_name=asset_name,
            market_cap_rank=_to_int(record.get("market_cap_rank")),
            categories=[str(item) for item in categories],
            source_metadata={"raw_record": dict(record)},
            governance_metadata=metadata.to_dict(),
        )


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)
