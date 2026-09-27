"""QuantLib-style vanilla option pricing adapter.

The public surface is intentionally narrow for OSS-QUANTLIB-001:
European Black-Scholes pricing and American CRR binomial pricing for
vanilla calls and puts backed by the official QuantLib numerical library.
Outputs are research-plane pricing snapshots only.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import QuantLib as ql

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
    dividend_yield: float = 0.0,
) -> dict[str, float]:
    """Price a vanilla European option with QuantLib Black-Scholes.

    ``tenor`` is expressed in years and ``vol`` is annualized volatility.
    Vega is returned per 1.0 volatility unit. Theta is returned per calendar day.
    """

    opt_type = _validate_inputs(spot, strike, rate, vol, tenor, option_type, dividend_yield)
    if tenor == 0.0:
        if opt_type == "call":
            price = max(0.0, spot - strike)
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            price = max(0.0, strike - spot)
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return {
            "price": float(price),
            "delta": float(delta),
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
        }

    forward = spot * math.exp((rate - dividend_yield) * tenor)
    std_dev = vol * math.sqrt(tenor)
    discount = math.exp(-rate * tenor)
    ql_type = ql.Option.Call if opt_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_type, strike)
    calc = ql.BlackCalculator(payoff, forward, std_dev, discount)

    return {
        "price": float(calc.value()),
        "delta": float(calc.delta(spot)),
        "gamma": float(calc.gamma(spot)),
        "vega": float(calc.vega(tenor)),
        "theta": float(calc.thetaPerDay(spot, tenor)),
        "rho": float(calc.rho(tenor)),
    }


def price_american_binomial(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType | str,
    *,
    steps: int = 512,
    dividend_yield: float = 0.0,
) -> dict[str, float]:
    """Price a vanilla American option with a QuantLib Cox-Ross-Rubinstein tree."""

    opt_type = _validate_inputs(spot, strike, rate, vol, tenor, option_type, dividend_yield)
    if steps < 3:
        raise ValueError("steps must be at least 3")

    if tenor == 0.0:
        if opt_type == "call":
            price = max(0.0, spot - strike)
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            price = max(0.0, strike - spot)
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return {
            "price": float(price),
            "delta": float(delta),
            "gamma": 0.0,
            "vega": 0.0,
        }

    price = _american_binomial_price_ql(
        spot, strike, rate, vol, tenor, opt_type, steps=steps, dividend_yield=dividend_yield
    )
    spot_bump = max(spot * 0.01, 0.01)
    vol_bump = 0.001
    price_up = _american_binomial_price_ql(
        spot + spot_bump, strike, rate, vol, tenor, opt_type, steps=steps, dividend_yield=dividend_yield
    )
    price_down = _american_binomial_price_ql(
        max(0.01, spot - spot_bump),
        strike,
        rate,
        vol,
        tenor,
        opt_type,
        steps=steps,
        dividend_yield=dividend_yield,
    )
    price_vol_up = _american_binomial_price_ql(
        spot, strike, rate, vol + vol_bump, tenor, opt_type, steps=steps, dividend_yield=dividend_yield
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
    dividend_yield: float = 0.0,
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
        "dividend_yield": dividend_yield,
    }.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if spot <= 0.0:
        raise ValueError("spot must be positive")
    if strike <= 0.0:
        raise ValueError("strike must be positive")
    if vol <= 0.0:
        raise ValueError("vol must be positive")
    if tenor < 0.0:
        raise ValueError("tenor must be non-negative")
    return normalized  # type: ignore[return-value]


def _american_binomial_price_ql(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: OptionType,
    *,
    steps: int,
    dividend_yield: float = 0.0,
) -> float:
    today = ql.Date(1, 1, 2026)
    ql.Settings.instance().evaluationDate = today
    day_count = ql.Actual365Fixed()
    calendar = ql.NullCalendar()
    days = 180
    maturity = today + days
    t_ql = day_count.yearFraction(today, maturity)
    scale = tenor / t_ql

    spot_handle = ql.QuoteHandle(ql.SimpleQuote(spot))
    rate_handle = ql.YieldTermStructureHandle(
        ql.FlatForward(today, rate * scale, day_count)
    )
    div_handle = ql.YieldTermStructureHandle(
        ql.FlatForward(today, dividend_yield * scale, day_count)
    )
    vol_handle = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(today, calendar, vol * math.sqrt(scale), day_count)
    )

    process = ql.BlackScholesMertonProcess(
        spot_handle, div_handle, rate_handle, vol_handle
    )
    ql_type = ql.Option.Call if option_type == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(ql_type, strike)
    exercise = ql.AmericanExercise(today, maturity)
    option = ql.VanillaOption(payoff, exercise)
    option.setPricingEngine(ql.BinomialVanillaEngine(process, "crr", steps))
    return float(option.NPV())


__all__ = ["price_european", "price_american_binomial"]
