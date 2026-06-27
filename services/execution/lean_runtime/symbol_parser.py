"""
Symbol Parser
Converts the schema.json flat symbol string ("AAPL.US", "BTCUSDT")
into a LEAN Symbol object via Symbol.Create().

Format contract (from signal_schema_v1.md §3.3, pending P2-002 full doc):
    "{TICKER}.{MARKET_CODE}"   — equities, forex, options
    "{TICKER}{QUOTE}"          — crypto (no dot separator, e.g. BTCUSDT on Binance)

Taiwan venue symbols are excluded from LEAN Symbol.Create() because they
execute through the Shioaji broker boundary. executor._execute_taiwan() routes
TW signals to algo.SubmitTaiwanBrokerOrder before this parser is reached, so a
TW market code only surfaces here as a hard error if that routing is bypassed.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Market code mapping
# Aligns with the LEAN-backed subset of schema.json symbol descriptions.
# Keys are uppercase suffixes after the last dot (or heuristic for crypto).
# ---------------------------------------------------------------------------
_MARKET_MAP: dict[str, tuple[str, str]] = {
    # code          : (LEAN Market constant,  LEAN SecurityType constant)
    "US":           ("Market.USA",            "SecurityType.Equity"),
    "USA":          ("Market.USA",            "SecurityType.Equity"),
    "FOREX":        ("Market.Oanda",          "SecurityType.Forex"),
    "FX":           ("Market.Oanda",          "SecurityType.Forex"),
    "COINBASE":     ("Market.Coinbase",       "SecurityType.Crypto"),
    "BINANCE":      ("Market.Binance",        "SecurityType.Crypto"),
    "KRAKEN":       ("Market.Kraken",         "SecurityType.Crypto"),
    "CRYPTO":       ("Market.Kraken",         "SecurityType.Crypto"),   # canonical crypto venue default
}

# Crypto pairs that lack a dot separator — identified by known quote currencies
_CRYPTO_QUOTES = ("USDT", "USD", "BTC", "ETH", "BNB", "USDC")


@dataclass
class ParsedSymbol:
    ticker: str
    lean_market: str        # e.g. "Market.USA"
    lean_security_type: str # e.g. "SecurityType.Equity"
    raw: str                # original string from signal


class SymbolParseError(ValueError):
    pass


def parse(symbol_str: str) -> ParsedSymbol:
    """
    Parse a flat symbol string from signal payload into LEAN Symbol components.

    Raises SymbolParseError if the format is unrecognised — caller must log
    and discard the signal (do not crash the runtime).

    Examples:
        "AAPL.US"       → ticker=AAPL, Market.USA, SecurityType.Equity
        "EURUSD.FX"     → ticker=EURUSD, Market.Oanda, SecurityType.Forex
        "BTCUSD.KRAKEN" → ticker=BTCUSD, Market.Kraken, SecurityType.Crypto
        "BTCUSDT"       → ticker=BTCUSDT, Market.Kraken, SecurityType.Crypto
        Taiwan venue codes are intentionally excluded here. Taiwan execution
        uses the Shioaji adapter boundary rather than LEAN Symbol.Create().
    """
    s = symbol_str.strip().upper()

    # --- dotted format: TICKER.MARKET_CODE ---
    if "." in s:
        parts = s.rsplit(".", 1)
        ticker, code = parts[0], parts[1]
        if code not in _MARKET_MAP:
            raise SymbolParseError(
                f"Unknown market code '{code}' in symbol '{symbol_str}'. "
                f"Supported codes: {sorted(_MARKET_MAP.keys())}"
            )
        market, sec_type = _MARKET_MAP[code]
        return ParsedSymbol(ticker=ticker, lean_market=market,
                            lean_security_type=sec_type, raw=symbol_str)

    # --- no-dot format: try crypto heuristic ---
    for quote in _CRYPTO_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            # Preserve original casing for LEAN (e.g. "BTCUSDT" not "BTCUSD T")
            return ParsedSymbol(
                ticker=s,
                lean_market="Market.Kraken",
                lean_security_type="SecurityType.Crypto",
                raw=symbol_str,
            )

    raise SymbolParseError(
        f"Cannot parse symbol '{symbol_str}': no dot separator and no known crypto quote suffix. "
        "Expected format: 'TICKER.MARKET_CODE' or a crypto pair like 'BTCUSDT'."
    )


def to_lean_create_call(parsed: ParsedSymbol) -> str:
    """Return the Python source string for Symbol.Create() — useful for logging."""
    return (
        f"Symbol.Create('{parsed.ticker}', "
        f"{parsed.lean_security_type}, "
        f"{parsed.lean_market})"
    )
