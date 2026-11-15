"""One definition of Taiwan cash-share statutory costs, shared by the paper ledger
and the member virtual book.

Rates are carried over from the member virtual book (previous single source) and
are MARKED FOR REVIEWER CONFIRMATION against the statutory source:
  * broker commission 0.1425% of notional on each side (no minimum fee modelled);
  * securities transaction tax on the SELL side only: 0.3% for stocks,
    0.1% for ETFs, where an ETF is a code starting with "00" (the existing rule).
No rounding is applied, so ledger equity and the virtual book stay comparable.
"""
from __future__ import annotations

import math

COMMISSION_RATE = 0.001425
EQUITY_TAX_RATE = 0.003
ETF_TAX_RATE = 0.001


def _code(symbol) -> str:
    return str(symbol).split(".")[0].strip()


def transaction_tax_rate(symbol) -> float:
    return ETF_TAX_RATE if _code(symbol).startswith("00") else EQUITY_TAX_RATE


def cash_share_costs(symbol, side, quantity, price) -> tuple[float, float]:
    """Return ``(commission, transaction_tax)`` for one cash-share fill."""
    side = str(side).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, got {side!r}")
    notional = float(quantity) * float(price)
    if not math.isfinite(notional) or notional < 0:
        raise ValueError("cash-share notional must be finite and non-negative")
    commission = notional * COMMISSION_RATE
    tax = notional * transaction_tax_rate(symbol) if side == "sell" else 0.0
    return commission, tax


def buy_cash_required(symbol, quantity, price) -> float:
    commission, _ = cash_share_costs(symbol, "buy", quantity, price)
    return float(quantity) * float(price) + commission
