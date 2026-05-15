"""Shioaji execution and quote boundary for Taiwan venues.

The adapter stays at the contract boundary so Pantheon can construct
broker intents without importing the vendor SDK in core execution code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


_VENUE_ALIASES = {
    "TW": "TSE",
    "TWSE": "TSE",
    "TSE": "TSE",
    "TPEX": "OTC",
    "TWO": "OTC",
    "OTC": "OTC",
    "TAIFEX": "TAIFEX",
}

_MARKET_SUFFIX_TO_VENUE = {
    "TW": "TSE",
    "TWSE": "TSE",
    "TPEX": "OTC",
    "TWO": "OTC",
    "OTC": "OTC",
    "TAIFEX": "TAIFEX",
}


def normalize_taiwan_symbol(symbol: str, venue: Optional[str] = None) -> tuple[str, str]:
    """Return native Taiwan symbol and Shioaji exchange code.

    Accepted formats:
    - ``2330``
    - ``2330.TW``
    - ``2330.TWSE``
    - ``6488.TWO``
    - ``TXF202604.TAIFEX``
    """

    raw = symbol.strip().upper()
    if not raw:
        raise ValueError("symbol is required")

    if "." in raw:
        native_symbol, suffix = raw.rsplit(".", 1)
        resolved_venue = _MARKET_SUFFIX_TO_VENUE.get(suffix)
        if resolved_venue is None:
            raise ValueError(f"unsupported Taiwan venue suffix: {suffix}")
        return native_symbol, resolved_venue

    resolved_venue = _VENUE_ALIASES.get((venue or "TWSE").strip().upper())
    if resolved_venue is None:
        raise ValueError(f"unsupported Taiwan venue: {venue}")
    return raw, resolved_venue


@dataclass
class ShioajiConfig:
    api_key: str
    secret_key: str
    simulation: bool = False
    account_name: Optional[str] = None
    quote_bind: bool = True


@dataclass
class ShioajiContractSpec:
    symbol: str
    venue: str
    contract_type: str = "stock"
    delivery_month: Optional[str] = None

    def to_lookup_payload(self) -> dict[str, Any]:
        payload = {
            "exchange": self.venue,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
        }
        if self.delivery_month:
            payload["delivery_month"] = self.delivery_month
        return payload


@dataclass
class ShioajiOrderIntent:
    symbol: str
    side: str
    quantity: int
    price: Optional[float] = None
    order_type: str = "ROD"
    price_type: str = "LMT"
    account: Optional[str] = None
    odd_lot: bool = False
    day_trade_short: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        side = self.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported order side: {self.side}")
        payload = {
            "symbol": self.symbol,
            "side": side,
            "quantity": self.quantity,
            "order_type": self.order_type.upper(),
            "price_type": self.price_type.upper(),
            "odd_lot": self.odd_lot,
            "day_trade_short": self.day_trade_short,
        }
        if self.account:
            payload["account"] = self.account
        if self.price is not None:
            payload["price"] = self.price
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class ShioajiQuoteSubscription:
    symbol: str
    venue: str
    quote_type: str = "tick"
    version: str = "v1"
    intraday_odd: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "exchange": self.venue,
            "symbol": self.symbol,
            "quote_type": self.quote_type,
            "version": self.version,
            "intraday_odd": self.intraday_odd,
        }


@dataclass
class ShioajiQuoteSnapshot:
    symbol: str
    venue: str
    ts: str
    close: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_volume: Optional[int] = None
    ask_volume: Optional[int] = None
    reference_price: Optional[float] = None
    day_volume: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShioajiAdapter:
    """Build Shioaji contract, order, and quote payloads."""

    def __init__(self, config: ShioajiConfig):
        self.config = config

    def build_contract(self, symbol: str, venue: Optional[str] = None) -> ShioajiContractSpec:
        native_symbol, exchange = normalize_taiwan_symbol(symbol, venue=venue)
        contract_type = "future" if exchange == "TAIFEX" else "stock"
        delivery_month = None
        if contract_type == "future":
            delivery_month = native_symbol[-6:] if len(native_symbol) >= 6 else None
        return ShioajiContractSpec(
            symbol=native_symbol,
            venue=exchange,
            contract_type=contract_type,
            delivery_month=delivery_month,
        )

    def build_order(self, intent: ShioajiOrderIntent, venue: Optional[str] = None) -> dict[str, Any]:
        native_symbol, _ = normalize_taiwan_symbol(intent.symbol, venue=venue)
        payload = intent.to_payload()
        payload["symbol"] = native_symbol
        payload["simulation"] = self.config.simulation
        if self.config.account_name and "account" not in payload:
            payload["account"] = self.config.account_name
        return payload

    def build_quote_subscription(
        self,
        symbol: str,
        venue: Optional[str] = None,
        *,
        quote_type: str = "tick",
        version: str = "v1",
        intraday_odd: bool = False,
    ) -> dict[str, Any]:
        native_symbol, exchange = normalize_taiwan_symbol(symbol, venue=venue)
        subscription = ShioajiQuoteSubscription(
            symbol=native_symbol,
            venue=exchange,
            quote_type=quote_type,
            version=version,
            intraday_odd=intraday_odd,
        )
        payload = subscription.to_payload()
        payload["bind"] = self.config.quote_bind
        return payload

    def normalize_quote(self, payload: dict[str, Any], symbol: str, venue: Optional[str] = None) -> ShioajiQuoteSnapshot:
        native_symbol, exchange = normalize_taiwan_symbol(symbol, venue=venue)
        return ShioajiQuoteSnapshot(
            symbol=native_symbol,
            venue=exchange,
            ts=str(payload.get("ts") or payload.get("datetime") or ""),
            close=_to_float(payload.get("close") or payload.get("price")),
            bid=_to_float(payload.get("bid_price") or payload.get("bid")),
            ask=_to_float(payload.get("ask_price") or payload.get("ask")),
            bid_volume=_to_int(payload.get("bid_volume")),
            ask_volume=_to_int(payload.get("ask_volume")),
            reference_price=_to_float(payload.get("reference")),
            day_volume=_to_int(payload.get("volume") or payload.get("total_volume")),
        )


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)
