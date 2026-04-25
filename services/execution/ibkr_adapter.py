"""IBKR execution and market-data boundary for US instruments.

The adapter stays at the contract boundary so Pantheon can construct
broker-aligned payloads without importing ``ib_insync`` or the native IBKR SDK
into core execution code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


_US_VENUE_ALIASES = {
    "US": "SMART",
    "USA": "SMART",
    "SMART": "SMART",
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "ARCA": "ARCA",
    "BATS": "BATS",
}

_MARKET_SUFFIX_TO_VENUE = {
    "US": "SMART",
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "ARCA": "ARCA",
    "BATS": "BATS",
}


def normalize_us_symbol(symbol: str, venue: Optional[str] = None) -> tuple[str, str]:
    """Return native US ticker and IBKR exchange route.

    Accepted formats:
    - ``AAPL``
    - ``AAPL.US``
    - ``MSFT.NASDAQ``
    - ``SPY.ARCA``
    """

    raw = symbol.strip().upper()
    if not raw:
        raise ValueError("symbol is required")

    if "." in raw:
        native_symbol, suffix = raw.rsplit(".", 1)
        resolved_venue = _MARKET_SUFFIX_TO_VENUE.get(suffix)
        if resolved_venue is None:
            raise ValueError(f"unsupported US venue suffix: {suffix}")
        return native_symbol, resolved_venue

    resolved_venue = _US_VENUE_ALIASES.get((venue or "SMART").strip().upper())
    if resolved_venue is None:
        raise ValueError(f"unsupported US venue: {venue}")
    return raw, resolved_venue


@dataclass
class IBKRConfig:
    host: str
    port: int
    client_id: int
    account_id: Optional[str] = None
    readonly_market_data: bool = False
    market_data_type: int = 1


@dataclass
class IBKRContractSpec:
    symbol: str
    exchange: str
    primary_exchange: Optional[str] = None
    currency: str = "USD"
    sec_type: str = "STK"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "currency": self.currency,
            "secType": self.sec_type,
        }
        if self.primary_exchange:
            payload["primaryExchange"] = self.primary_exchange
        return payload


@dataclass
class IBKROrderIntent:
    symbol: str
    side: str
    quantity: int
    order_type: str = "MKT"
    price: Optional[float] = None
    tif: str = "DAY"
    account: Optional[str] = None
    outside_rth: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        side = self.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported order side: {self.side}")

        order_type = self.order_type.upper()
        payload = {
            "action": side,
            "totalQuantity": self.quantity,
            "orderType": order_type,
            "tif": self.tif.upper(),
            "outsideRth": self.outside_rth,
        }
        if order_type != "MKT":
            if self.price is None:
                raise ValueError("price is required for non-market orders")
            payload["lmtPrice"] = self.price
        if self.account:
            payload["account"] = self.account
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class IBKRMarketDataRequest:
    symbol: str
    exchange: str
    primary_exchange: Optional[str] = None
    currency: str = "USD"
    sec_type: str = "STK"
    generic_ticks: str = ""
    snapshot: bool = False
    regulatory_snapshot: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "contract": {
                "symbol": self.symbol,
                "exchange": self.exchange,
                "currency": self.currency,
                "secType": self.sec_type,
            },
            "genericTicks": self.generic_ticks,
            "snapshot": self.snapshot,
            "regulatorySnapshot": self.regulatory_snapshot,
        }
        if self.primary_exchange:
            payload["contract"]["primaryExchange"] = self.primary_exchange
        return payload


@dataclass
class IBKRQuoteSnapshot:
    symbol: str
    exchange: str
    ts: str
    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    market_data_type: Optional[int] = None
    provider: str = "IBKR market data"
    source_class: str = "broker_execution"
    boundary_role: str = "execution_sync_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IBKRAdapter:
    """Build IBKR contract, order, and market-data payloads."""

    def __init__(self, config: IBKRConfig):
        self.config = config

    def build_contract(self, symbol: str, venue: Optional[str] = None) -> IBKRContractSpec:
        native_symbol, exchange = normalize_us_symbol(symbol, venue=venue)
        primary_exchange = None if exchange == "SMART" else exchange
        return IBKRContractSpec(
            symbol=native_symbol,
            exchange=exchange,
            primary_exchange=primary_exchange,
        )

    def build_order(self, intent: IBKROrderIntent, venue: Optional[str] = None) -> dict[str, Any]:
        native_symbol, exchange = normalize_us_symbol(intent.symbol, venue=venue)
        contract = self.build_contract(native_symbol, venue=exchange)
        payload = {
            "contract": contract.to_payload(),
            "order": intent.to_payload(),
            "broker": "IBKR",
        }
        if self.config.account_id and "account" not in payload["order"]:
            payload["order"]["account"] = self.config.account_id
        return payload

    def build_market_data_request(
        self,
        symbol: str,
        venue: Optional[str] = None,
        *,
        snapshot: bool = False,
        generic_ticks: str = "",
        regulatory_snapshot: bool = False,
    ) -> dict[str, Any]:
        native_symbol, exchange = normalize_us_symbol(symbol, venue=venue)
        request = IBKRMarketDataRequest(
            symbol=native_symbol,
            exchange=exchange,
            primary_exchange=None if exchange == "SMART" else exchange,
            generic_ticks=generic_ticks,
            snapshot=snapshot,
            regulatory_snapshot=regulatory_snapshot,
        )
        payload = request.to_payload()
        payload["marketDataType"] = self.config.market_data_type
        payload["readonly"] = self.config.readonly_market_data
        payload["provider"] = "IBKR market data"
        payload["sourceClass"] = "broker_execution"
        payload["boundaryRole"] = "execution_sync_fallback"
        return payload

    def normalize_quote(self, payload: dict[str, Any], symbol: str, venue: Optional[str] = None) -> IBKRQuoteSnapshot:
        native_symbol, exchange = normalize_us_symbol(symbol, venue=venue)
        contract = payload.get("contract") or {}
        resolved_exchange = str(contract.get("exchange") or exchange)
        return IBKRQuoteSnapshot(
            symbol=native_symbol,
            exchange=resolved_exchange,
            ts=str(payload.get("ts") or payload.get("time") or ""),
            last=_to_float(payload.get("last") or payload.get("lastPrice")),
            bid=_to_float(payload.get("bid") or payload.get("bidPrice")),
            ask=_to_float(payload.get("ask") or payload.get("askPrice")),
            bid_size=_to_int(payload.get("bidSize")),
            ask_size=_to_int(payload.get("askSize")),
            close=_to_float(payload.get("close")),
            volume=_to_int(payload.get("volume")),
            market_data_type=_to_int(payload.get("marketDataType") or self.config.market_data_type),
            provider=str(payload.get("provider") or "IBKR market data"),
            source_class="broker_execution",
            boundary_role="execution_sync_fallback",
        )


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)
