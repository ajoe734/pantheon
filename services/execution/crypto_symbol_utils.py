"""Shared helpers for parsing venue-scoped crypto symbols."""

from __future__ import annotations

from typing import Final


_KNOWN_KRAKEN_QUOTES: Final[tuple[str, ...]] = (
    "PYUSD",
    "USDT",
    "USDC",
    "EURT",
    "DAI",
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "CHF",
    "JPY",
    "TRY",
    "BTC",
    "ETH",
)


def split_kraken_compact_pair(pair: str) -> tuple[str, str]:
    normalized = str(pair).strip().upper().replace("/", "")
    if not normalized:
        raise ValueError("unsupported Kraken pair: ")

    for quote in _KNOWN_KRAKEN_QUOTES:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)], quote

    # Fallback for unlisted fiat/crypto quote assets while keeping a non-empty base.
    for quote_length in (4, 3):
        if len(normalized) <= quote_length:
            continue
        base = normalized[:-quote_length]
        quote = normalized[-quote_length:]
        if base.isalpha() and quote.isalpha():
            return base, quote

    raise ValueError(f"unsupported Kraken pair: {pair}")


def parse_kraken_symbol_components(symbol: str) -> tuple[str, str]:
    normalized = str(symbol).strip().upper()
    if not normalized:
        raise ValueError("symbol is required")

    if "/" in normalized:
        base, quote = normalized.split("/", 1)
        if base and quote:
            return base, quote
        raise ValueError(f"unsupported Kraken pair: {symbol}")

    return split_kraken_compact_pair(normalized)


def extract_kraken_base_asset(symbol: str) -> str:
    base, _quote = parse_kraken_symbol_components(symbol)
    return base
