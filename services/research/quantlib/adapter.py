"""QuantLib-style vanilla option pricing adapter.

The public surface is intentionally narrow for OSS-QUANTLIB-001:
European Black-Scholes pricing and American CRR binomial pricing for
vanilla calls and puts. Outputs are research-plane pricing snapshots only.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

# Keep the pre-existing ``adapter/`` package importable for older governed
# QuantLib tests that use ``from adapter.quantlib_adapter import ...``.
_LEGACY_PACKAGE_DIR = Path(__file__).with_suffix("")
if _LEGACY_PACKAGE_DIR.is_dir():
    __path__ = [str(_LEGACY_PACKAGE_DIR)]  # type: ignore[var-annotated]

OptionType = Literal["call", "put"]


def price_european(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType | str,
) -> dict[str, float]:
    """Price a vanilla European option with Black-Scholes.

    ``tenor`` is expressed in years and ``vol`` is annualized volatility.
    Vega is returned per 1.0 volatility unit.
    """

    option_type = _validate_inputs(spot, strike, rate, vol, tenor, option_type)
    return _black_scholes(spot, strike, rate, vol, tenor, option_type)


def price_american_binomial(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType | str,
    *,
    steps: int = 512,
) -> dict[str, float]:
    """Price a vanilla American option with a Cox-Ross-Rubinstein tree."""

    option_type = _validate_inputs(spot, strike, rate, vol, tenor, option_type)
    if steps < 3:
        raise ValueError("steps must be at least 3")

    price = _american_binomial_price(
        spot, strike, rate, vol, tenor, option_type, steps=steps
    )
    spot_bump = max(spot * 0.01, 0.01)
    vol_bump = 0.001
    price_up = _american_binomial_price(
        spot + spot_bump, strike, rate, vol, tenor, option_type, steps=steps
    )
    price_down = _american_binomial_price(
        max(0.01, spot - spot_bump),
        strike,
        rate,
        vol,
        tenor,
        option_type,
        steps=steps,
    )
    price_vol_up = _american_binomial_price(
        spot, strike, rate, vol + vol_bump, tenor, option_type, steps=steps
    )

    delta = (price_up - price_down) / (2.0 * spot_bump)
    gamma = (price_up - 2.0 * price + price_down) / (spot_bump**2)
    vega = (price_vol_up - price) / vol_bump
    return {
        "price": float(price),
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
    }


def _validate_inputs(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType | str,
) -> OptionType:
    normalized = str(option_type).lower()
    if normalized not in {"call", "put"}:
        raise ValueError("option_type must be 'call' or 'put'")
    for name, value in {
        "spot": spot,
        "strike": strike,
        "rate": rate,
        "vol": vol,
        "tenor": tenor,
    }.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if spot <= 0.0:
        raise ValueError("spot must be positive")
    if strike <= 0.0:
        raise ValueError("strike must be positive")
    if vol <= 0.0:
        raise ValueError("vol must be positive")
    if tenor <= 0.0:
        raise ValueError("tenor must be positive")
    return normalized  # type: ignore[return-value]


def _black_scholes(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType,
) -> dict[str, float]:
    sqrt_t = math.sqrt(tenor)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * tenor) / (
        vol * sqrt_t
    )
    d2 = d1 - vol * sqrt_t

    if option_type == "call":
        price = spot * _norm_cdf(d1) - strike * math.exp(-rate * tenor) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
    else:
        price = strike * math.exp(-rate * tenor) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0

    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t
    return {
        "price": float(price),
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
    }


def _american_binomial_price(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType,
    *,
    steps: int,
) -> float:
    dt = tenor / steps
    up = math.exp(vol * math.sqrt(dt))
    down = 1.0 / up
    discount = math.exp(-rate * dt)
    probability = (math.exp(rate * dt) - down) / (up - down)
    if not 0.0 < probability < 1.0:
        raise ValueError("CRR tree produced invalid risk-neutral probability")

    values = [
        _payoff(spot * (up**j) * (down ** (steps - j)), strike, option_type)
        for j in range(steps + 1)
    ]
    for level in range(steps - 1, -1, -1):
        next_values: list[float] = []
        for j in range(level + 1):
            continuation = discount * (
                probability * values[j + 1] + (1.0 - probability) * values[j]
            )
            node_spot = spot * (up**j) * (down ** (level - j))
            exercise = _payoff(node_spot, strike, option_type)
            next_values.append(max(continuation, exercise))
        values = next_values
    return values[0]


def _payoff(spot: float, strike: float, option_type: OptionType) -> float:
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


__all__ = ["price_european", "price_american_binomial"]
