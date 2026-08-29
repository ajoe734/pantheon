"""Small, dependency-free market-symbol identity helpers.

The Source data plane stores Taiwan listings with venue-specific canonical
suffixes (``.TWSE`` and ``.TPEX``).  Existing execution bindings may use the
broker-facing aliases ``.TW`` and ``.TWO``.  These helpers keep that one
explicit compatibility rule at the boundary, without changing either stored
canonical records or configured execution symbols.
"""
from __future__ import annotations

from typing import Any


_TAIWAN_SUFFIX_ALIASES = {
    "TW": ("TW", "TWSE"),
    "TWSE": ("TW", "TWSE"),
    "TWO": ("TWO", "TPEX"),
    "TPEX": ("TWO", "TPEX"),
}


def normalized_market_symbol(value: Any) -> str:
    """Return a case-normalized symbol without assigning a venue."""

    return str(value or "").strip().upper()


def market_symbol_aliases(value: Any) -> tuple[str, ...]:
    """Return known equivalent spellings, with unrelated symbols unchanged."""

    symbol = normalized_market_symbol(value)
    root, separator, suffix = symbol.rpartition(".")
    aliases = _TAIWAN_SUFFIX_ALIASES.get(suffix) if separator and root else None
    if aliases is None:
        return (symbol,) if symbol else ()
    return tuple(f"{root}.{alias}" for alias in aliases)


def market_symbols_equivalent(left: Any, right: Any) -> bool:
    """Whether two configured/stored symbols identify the same market listing."""

    left_aliases = market_symbol_aliases(left)
    right_aliases = market_symbol_aliases(right)
    return bool(left_aliases and right_aliases and set(left_aliases).intersection(right_aliases))
