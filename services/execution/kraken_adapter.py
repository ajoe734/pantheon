"""Kraken execution and venue market-data boundary for crypto instruments.

The adapter stays at the contract boundary so Pantheon can construct
venue-scoped crypto payloads without importing Kraken SDK dependencies into
core execution code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

try:
    from .crypto_symbol_utils import parse_kraken_symbol_components
except ImportError:  # pragma: no cover - supports direct script imports
    from crypto_symbol_utils import parse_kraken_symbol_components

_KRAKEN_VENUE_ALIASES = {
    "KRAKEN": "KRAKEN",
    "CRYPTO": "KRAKEN",
}


def normalize_kraken_symbol(symbol: str, venue: Optional[str] = None) -> tuple[str, str, str]:
    """Return Kraken-native pair, altname, and venue code.

    Accepted formats:
    - ``BTCUSD``
    - ``BTC/USD``
    - ``ETHUSDT.KRAKEN``
    - ``ETH/USDT.KRAKEN``
    """

    raw = symbol.strip().upper()
    if not raw:
        raise ValueError("symbol is required")

    resolved_venue = _KRAKEN_VENUE_ALIASES.get((venue or "KRAKEN").strip().upper())
    if resolved_venue is None:
        raise ValueError(f"unsupported Kraken venue: {venue}")

    pair = raw
    if "." in raw:
        pair, suffix = raw.rsplit(".", 1)
        suffix_venue = _KRAKEN_VENUE_ALIASES.get(suffix)
        if suffix_venue is None:
            raise ValueError(f"unsupported Kraken venue suffix: {suffix}")
        resolved_venue = suffix_venue

    base, quote = parse_kraken_symbol_components(pair)
    if not base or not quote:
        raise ValueError(f"unsupported Kraken pair: {symbol}")

    altname = f"{base}{quote}"
    return f"{base}/{quote}", altname, resolved_venue


@dataclass
class KrakenConfig:
    api_key: str
    api_secret: str
    account_type: str = "cash"
    validate_only: bool = False


@dataclass
class KrakenAssetPair:
    pair: str
    altname: str
    venue: str = "KRAKEN"

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "altname": self.altname,
            "venue": self.venue,
        }


@dataclass
class KrakenOrderIntent:
    symbol: str
    side: str
    quantity: float
    order_type: str = "market"
    price: Optional[float] = None
    validate: Optional[bool] = None
    leverage: Optional[str] = None
    oflags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        side = self.side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported order side: {self.side}")
        order_type = self.order_type.lower()
        payload: dict[str, Any] = {
            "type": side,
            "ordertype": order_type,
            "volume": str(self.quantity),
        }
        if order_type != "market":
            if self.price is None:
                raise ValueError("price is required for non-market orders")
            payload["price"] = str(self.price)
        if self.validate is not None:
            payload["validate"] = self.validate
        if self.leverage:
            payload["leverage"] = self.leverage
        if self.oflags:
            payload["oflags"] = ",".join(self.oflags)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class KrakenMarketDataRequest:
    pair: str
    interval: int = 1
    since: Optional[int] = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "pair": self.pair,
            "interval": self.interval,
        }
        if self.since is not None:
            payload["since"] = self.since
        return payload


@dataclass
class KrakenWebSocketSubscription:
    pair: str
    channel: str = "ticker"
    snapshot: bool = True
    token: Optional[str] = None
    req_id: Optional[int] = None

    def to_payload(self) -> dict[str, Any]:
        subscription: dict[str, Any] = {
            "name": self.channel,
            "snapshot": self.snapshot,
        }
        if self.token:
            subscription["token"] = self.token
        payload: dict[str, Any] = {
            "event": "subscribe",
            "pair": [self.pair],
            "subscription": subscription,
        }
        if self.req_id is not None:
            payload["reqid"] = self.req_id
        return payload


@dataclass
class KrakenQuoteSnapshot:
    symbol: str
    venue: str
    base_asset: str
    quote_asset: str
    ts: str
    last: Optional[float] = None
    close: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    vwap: Optional[float] = None
    volume: Optional[float] = None
    funding_rate: Optional[float] = None
    provider: str = "Kraken"
    source_class: str = "broker_execution"
    boundary_role: str = "venue_canonical"
    transport: str = "rest"
    channel: str = "rest_snapshot"
    replay_source: str = "rest_snapshot"
    is_replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KrakenExecutionSyncState:
    symbol: str
    venue: str
    base_asset: str
    quote_asset: str
    ts: str
    rest_ts: str
    websocket_ts: str
    last: Optional[float] = None
    close: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    vwap: Optional[float] = None
    volume: Optional[float] = None
    funding_rate: Optional[float] = None
    provider: str = "Kraken"
    source_class: str = "broker_execution"
    boundary_role: str = "venue_canonical"
    transport: str = "websocket"
    channel: str = "ticker"
    sync_source: str = "rest_plus_websocket"
    replay_source: str = "websocket_backed_sync"
    is_replayable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KrakenAdapter:
    """Build Kraken pair, order, and market-data payloads."""

    def __init__(self, config: KrakenConfig):
        self.config = config

    def build_contract(self, symbol: str, venue: Optional[str] = None) -> KrakenAssetPair:
        pair, altname, resolved_venue = normalize_kraken_symbol(symbol, venue=venue)
        return KrakenAssetPair(pair=pair, altname=altname, venue=resolved_venue)

    def build_order(self, intent: KrakenOrderIntent, venue: Optional[str] = None) -> dict[str, Any]:
        contract = self.build_contract(intent.symbol, venue=venue)
        payload = intent.to_payload()
        payload["pair"] = contract.pair
        payload["venue"] = contract.venue
        payload["provider"] = "Kraken"
        payload["validate"] = payload.get("validate", self.config.validate_only)
        return payload

    def build_market_data_request(
        self,
        symbol: str,
        venue: Optional[str] = None,
        *,
        interval: int = 1,
        since: Optional[int] = None,
    ) -> dict[str, Any]:
        contract = self.build_contract(symbol, venue=venue)
        request = KrakenMarketDataRequest(pair=contract.pair, interval=interval, since=since)
        payload = request.to_payload()
        payload["venue"] = contract.venue
        payload["provider"] = "Kraken"
        payload["sourceClass"] = "broker_execution"
        payload["boundaryRole"] = "venue_canonical"
        return payload

    def build_websocket_subscription(
        self,
        symbol: str,
        venue: Optional[str] = None,
        *,
        channel: str = "ticker",
        snapshot: bool = True,
        token: Optional[str] = None,
        req_id: Optional[int] = None,
    ) -> dict[str, Any]:
        contract = self.build_contract(symbol, venue=venue)
        subscription = KrakenWebSocketSubscription(
            pair=contract.pair,
            channel=channel,
            snapshot=snapshot,
            token=token,
            req_id=req_id,
        )
        payload = subscription.to_payload()
        payload["venue"] = contract.venue
        payload["provider"] = "Kraken"
        payload["sourceClass"] = "broker_execution"
        payload["boundaryRole"] = "venue_canonical"
        payload["transport"] = "websocket"
        return payload

    def normalize_quote(self, payload: dict[str, Any], symbol: str, venue: Optional[str] = None) -> KrakenQuoteSnapshot:
        pair, altname, resolved_venue = normalize_kraken_symbol(symbol, venue=venue)
        base_asset, quote_asset = pair.split("/", 1)
        raw_last = _to_float(payload.get("last"))
        raw_close = _to_float(payload.get("close"))
        fallback_price = _to_float(payload.get("price"))
        last_price = raw_last if raw_last is not None else raw_close if raw_close is not None else fallback_price
        close_price = raw_close if raw_close is not None else raw_last if raw_last is not None else fallback_price
        return KrakenQuoteSnapshot(
            symbol=altname,
            venue=resolved_venue,
            base_asset=base_asset,
            quote_asset=quote_asset,
            ts=str(payload.get("ts") or payload.get("time") or payload.get("timestamp") or ""),
            last=last_price,
            close=close_price,
            bid=_to_float(payload.get("bid")),
            ask=_to_float(payload.get("ask")),
            vwap=_to_float(payload.get("vwap")),
            volume=_to_float(payload.get("volume")),
            funding_rate=_to_float(payload.get("funding_rate")),
            provider=str(payload.get("provider") or "Kraken"),
            source_class="broker_execution",
            boundary_role="venue_canonical",
            transport=str(payload.get("transport") or "rest"),
            channel=str(payload.get("channel") or "rest_snapshot"),
            replay_source=str(payload.get("replay_source") or "rest_snapshot"),
            is_replayable=bool(payload.get("is_replayable", False)),
        )

    def normalize_websocket_ticker(
        self,
        payload: dict[str, Any],
        symbol: str,
        venue: Optional[str] = None,
    ) -> KrakenQuoteSnapshot:
        normalized_payload = {
            "ts": payload.get("ts") or payload.get("time") or payload.get("timestamp"),
            "bid": _extract_kraken_ws_price(payload.get("b") or payload.get("bid")),
            "ask": _extract_kraken_ws_price(payload.get("a") or payload.get("ask")),
            "last": _extract_kraken_ws_price(payload.get("c") or payload.get("last")),
            "close": _extract_kraken_ws_price(payload.get("c") or payload.get("close")),
            "vwap": _extract_kraken_ws_price(payload.get("p") or payload.get("vwap")),
            "volume": _extract_kraken_ws_volume(payload.get("v") or payload.get("volume")),
            "provider": payload.get("provider") or "Kraken",
            "transport": "websocket",
            "channel": payload.get("channel") or "ticker",
            "replay_source": "websocket_ticker",
            "is_replayable": True,
        }
        return self.normalize_quote(normalized_payload, symbol, venue=venue)

    def reconcile_execution_sync(
        self,
        *,
        symbol: str,
        rest_payload: dict[str, Any],
        websocket_payload: dict[str, Any],
        venue: Optional[str] = None,
    ) -> KrakenExecutionSyncState:
        rest_quote = self.normalize_quote(rest_payload, symbol, venue=venue)
        websocket_quote = self.normalize_websocket_ticker(websocket_payload, symbol, venue=venue)
        pair, altname, resolved_venue = normalize_kraken_symbol(symbol, venue=venue)
        base_asset, quote_asset = pair.split("/", 1)
        return KrakenExecutionSyncState(
            symbol=altname,
            venue=resolved_venue,
            base_asset=base_asset,
            quote_asset=quote_asset,
            ts=websocket_quote.ts or rest_quote.ts,
            rest_ts=rest_quote.ts,
            websocket_ts=websocket_quote.ts,
            last=websocket_quote.last if websocket_quote.last is not None else rest_quote.last,
            close=rest_quote.close if rest_quote.close is not None else websocket_quote.close,
            bid=websocket_quote.bid if websocket_quote.bid is not None else rest_quote.bid,
            ask=websocket_quote.ask if websocket_quote.ask is not None else rest_quote.ask,
            vwap=websocket_quote.vwap if websocket_quote.vwap is not None else rest_quote.vwap,
            volume=websocket_quote.volume if websocket_quote.volume is not None else rest_quote.volume,
            funding_rate=rest_quote.funding_rate if rest_quote.funding_rate is not None else websocket_quote.funding_rate,
            transport=websocket_quote.transport,
            channel=websocket_quote.channel,
        )


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _extract_kraken_ws_price(value: Any) -> Optional[float]:
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    return _to_float(value)


def _extract_kraken_ws_volume(value: Any) -> Optional[float]:
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[-1]
    return _to_float(value)
