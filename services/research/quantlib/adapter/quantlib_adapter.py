"""Governed QuantLib adapter for Pantheon Research Plane.

Governance invariants:
- All input must pass GovernedQuantLibInputAdapter before reaching a backend.
- CI and default local verification use StubQuantLibBackend.
- Real backend requires PANTHEON_QUANTLIB_BACKEND=real.
- Outputs are always non-executable research artifacts at artifact_state=draft.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import uuid
from dataclasses import dataclass, field
from typing import Any


class QuantLibWorkflowError(ValueError):
    """Raised when a governed QuantLib workflow cannot run safely."""


@dataclass(frozen=True)
class GovernedOptionSpec:
    option_id: str
    style: str
    option_type: str
    spot: float
    strike: float
    volatility: float
    risk_free_rate: float
    dividend_yield: float
    maturity_days: int
    quantity: int = 1


@dataclass(frozen=True)
class GovernedBondSpec:
    instrument_id: str
    face_value: float
    coupon_rate: float
    market_rate: float
    maturity_years: int
    payment_frequency: int = 2


@dataclass(frozen=True)
class GovernedMarketSnapshot:
    dataset_id: str
    source_dataset_refs: tuple[str, ...]
    valuation_date: str
    option_specs: tuple[GovernedOptionSpec, ...]
    bond_specs: tuple[GovernedBondSpec, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


class GovernedQuantLibInputAdapter:
    """Validates governed pricing/risk inputs before they reach any backend."""

    ALLOWED_STYLES = {"european", "american"}
    ALLOWED_OPTION_TYPES = {"call", "put"}

    def validate(self, snapshot: GovernedMarketSnapshot) -> GovernedMarketSnapshot:
        if not isinstance(snapshot, GovernedMarketSnapshot):
            raise QuantLibWorkflowError(
                "Input must be a GovernedMarketSnapshot; raw dicts are not a governed interface."
            )

        if not snapshot.dataset_id.strip():
            raise QuantLibWorkflowError("dataset_id must be a non-empty string")
        if not snapshot.source_dataset_refs:
            raise QuantLibWorkflowError("source_dataset_refs must include at least one lineage ref")
        if not snapshot.option_specs:
            raise QuantLibWorkflowError("At least one governed option spec is required")
        if not snapshot.bond_specs:
            raise QuantLibWorkflowError("At least one governed bond spec is required")

        for ref in snapshot.source_dataset_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise QuantLibWorkflowError("source_dataset_refs must contain only non-empty strings")

        for option in snapshot.option_specs:
            self._validate_option(option)
        for bond in snapshot.bond_specs:
            self._validate_bond(bond)

        return snapshot

    def _validate_option(self, option: GovernedOptionSpec) -> None:
        if option.style not in self.ALLOWED_STYLES:
            raise QuantLibWorkflowError(f"Unsupported option style '{option.style}'")
        if option.option_type not in self.ALLOWED_OPTION_TYPES:
            raise QuantLibWorkflowError(f"Unsupported option type '{option.option_type}'")
        for name in ("spot", "strike", "volatility"):
            if getattr(option, name) <= 0:
                raise QuantLibWorkflowError(f"Option field '{name}' must be positive")
        if option.maturity_days <= 0:
            raise QuantLibWorkflowError("Option maturity_days must be positive")
        if option.quantity == 0:
            raise QuantLibWorkflowError("Option quantity must be non-zero")

    def _validate_bond(self, bond: GovernedBondSpec) -> None:
        for name in ("face_value", "coupon_rate", "market_rate"):
            if getattr(bond, name) < 0:
                raise QuantLibWorkflowError(f"Bond field '{name}' must be non-negative")
        if bond.face_value <= 0:
            raise QuantLibWorkflowError("Bond field 'face_value' must be positive")
        if bond.maturity_years <= 0:
            raise QuantLibWorkflowError("Bond maturity_years must be positive")
        if bond.payment_frequency <= 0:
            raise QuantLibWorkflowError("Bond payment_frequency must be positive")


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_metrics(option: GovernedOptionSpec) -> dict[str, float]:
    t = option.maturity_days / 365.0
    s = option.spot
    k = option.strike
    sigma = option.volatility
    r = option.risk_free_rate
    q = option.dividend_yield
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option.option_type == "call":
        price = s * math.exp(-q * t) * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
        delta = math.exp(-q * t) * _norm_cdf(d1)
        theta = (
            -(s * _norm_pdf(d1) * sigma * math.exp(-q * t)) / (2.0 * sqrt_t)
            - r * k * math.exp(-r * t) * _norm_cdf(d2)
            + q * s * math.exp(-q * t) * _norm_cdf(d1)
        )
        rho = k * t * math.exp(-r * t) * _norm_cdf(d2)
    else:
        price = k * math.exp(-r * t) * _norm_cdf(-d2) - s * math.exp(-q * t) * _norm_cdf(-d1)
        delta = math.exp(-q * t) * (_norm_cdf(d1) - 1.0)
        theta = (
            -(s * _norm_pdf(d1) * sigma * math.exp(-q * t)) / (2.0 * sqrt_t)
            + r * k * math.exp(-r * t) * _norm_cdf(-d2)
            - q * s * math.exp(-q * t) * _norm_cdf(-d1)
        )
        rho = -k * t * math.exp(-r * t) * _norm_cdf(-d2)

    gamma = math.exp(-q * t) * _norm_pdf(d1) / (s * sigma * sqrt_t)
    vega = s * math.exp(-q * t) * _norm_pdf(d1) * sqrt_t
    return {
        "npv": round(price * abs(option.quantity), 6),
        "delta": round(delta * option.quantity, 6),
        "gamma": round(gamma * abs(option.quantity), 6),
        "vega": round(vega * abs(option.quantity) / 100.0, 6),
        "theta": round(theta * option.quantity / 365.0, 6),
        "rho": round(rho * option.quantity / 100.0, 6),
    }


def _bond_metrics(bond: GovernedBondSpec) -> dict[str, float]:
    periods = bond.maturity_years * bond.payment_frequency
    coupon_cashflow = bond.face_value * bond.coupon_rate / bond.payment_frequency
    per_period_rate = bond.market_rate / bond.payment_frequency
    cashflows = [coupon_cashflow] * periods
    cashflows[-1] += bond.face_value

    discounted: list[float] = []
    weighted: list[float] = []
    for idx, cf in enumerate(cashflows, start=1):
        t = idx / bond.payment_frequency
        pv = cf / ((1.0 + per_period_rate) ** idx)
        discounted.append(pv)
        weighted.append(t * pv)

    clean_price = sum(discounted)
    macaulay_duration = sum(weighted) / clean_price
    modified_duration = macaulay_duration / (1.0 + per_period_rate)
    convexity = sum(
        pv * t * (t + 1.0 / bond.payment_frequency) for pv, t in zip(discounted, [i / bond.payment_frequency for i in range(1, periods + 1)])
    ) / (clean_price * (1.0 + per_period_rate) ** 2)
    dv01 = modified_duration * clean_price * 0.0001
    return {
        "clean_price": round(clean_price, 6),
        "duration": round(modified_duration, 6),
        "convexity": round(convexity, 6),
        "dv01": round(dv01, 6),
    }


class StubQuantLibBackend:
    """Deterministic CI-safe backend using local analytic formulas only."""

    def price_options(self, snapshot: GovernedMarketSnapshot) -> dict[str, Any]:
        return {
            option.option_id: {
                **_bs_metrics(option),
                "model": "black_scholes_stub" if option.style == "european" else "american_stub_proxy",
                "style": option.style,
                "option_type": option.option_type,
                "stub": True,
            }
            for option in snapshot.option_specs
        }

    def analyze_fixed_income(self, snapshot: GovernedMarketSnapshot) -> dict[str, Any]:
        return {
            bond.instrument_id: {
                **_bond_metrics(bond),
                "curve_points": [
                    {"tenor_years": 0.5, "zero_rate": round(max(0.0001, bond.market_rate - 0.0025), 6)},
                    {"tenor_years": float(bond.maturity_years), "zero_rate": round(bond.market_rate, 6)},
                ],
                "stub": True,
            }
            for bond in snapshot.bond_specs
        }


class QuantLibBackend:
    """Real backend wrapping QuantLib-Python for governed research use."""

    def price_options(self, snapshot: GovernedMarketSnapshot) -> dict[str, Any]:
        import QuantLib as ql

        valuation_dt = dt.date.fromisoformat(snapshot.valuation_date)
        self._set_evaluation_date(valuation_dt)

        priced: dict[str, Any] = {}
        for option in snapshot.option_specs:
            spot = ql.SimpleQuote(option.spot)
            spot_handle = ql.QuoteHandle(spot)
            day_count = ql.Actual365Fixed()
            calendar = ql.NullCalendar()
            maturity_date = ql.Settings.instance().evaluationDate + int(option.maturity_days)

            risk_ts = ql.YieldTermStructureHandle(
                ql.FlatForward(0, calendar, option.risk_free_rate, day_count)
            )
            div_ts = ql.YieldTermStructureHandle(
                ql.FlatForward(0, calendar, option.dividend_yield, day_count)
            )
            vol_ts = ql.BlackVolTermStructureHandle(
                ql.BlackConstantVol(0, calendar, option.volatility, day_count)
            )
            process = ql.BlackScholesMertonProcess(spot_handle, div_ts, risk_ts, vol_ts)
            payoff = ql.PlainVanillaPayoff(
                ql.Option.Call if option.option_type == "call" else ql.Option.Put,
                option.strike,
            )

            if option.style == "european":
                exercise = ql.EuropeanExercise(maturity_date)
                instrument = ql.VanillaOption(payoff, exercise)
                instrument.setPricingEngine(ql.AnalyticEuropeanEngine(process))
                result = {
                    "npv": round(instrument.NPV() * abs(option.quantity), 6),
                    "delta": round(instrument.delta() * option.quantity, 6),
                    "gamma": round(instrument.gamma() * abs(option.quantity), 6),
                    "vega": round(instrument.vega() * abs(option.quantity) / 100.0, 6),
                    "theta": round(instrument.thetaPerDay() * option.quantity, 6),
                    "rho": round(instrument.rho() * option.quantity / 100.0, 6),
                    "model": "analytic_european",
                    "style": option.style,
                    "option_type": option.option_type,
                }
            else:
                base_npv = self._american_npv(option, valuation_date=valuation_dt)
                result = {
                    "npv": round(base_npv * abs(option.quantity), 6),
                    **self._finite_difference_greeks(option, valuation_date=valuation_dt, base_npv=base_npv),
                    "model": "binomial_crr",
                    "style": option.style,
                    "option_type": option.option_type,
                }

            priced[option.option_id] = result
        return priced

    def _set_evaluation_date(self, valuation_date: dt.date) -> None:
        import QuantLib as ql

        ql.Settings.instance().evaluationDate = ql.Date(
            valuation_date.day, valuation_date.month, valuation_date.year
        )

    def _american_npv(
        self,
        option: GovernedOptionSpec,
        *,
        valuation_date: dt.date,
        maturity_days: int | None = None,
        spot: float | None = None,
        volatility: float | None = None,
        risk_free_rate: float | None = None,
    ) -> float:
        import QuantLib as ql

        self._set_evaluation_date(valuation_date)
        day_count = ql.Actual365Fixed()
        calendar = ql.NullCalendar()
        maturity = max(1, maturity_days if maturity_days is not None else option.maturity_days)
        evaluation_date = ql.Settings.instance().evaluationDate
        maturity_date = evaluation_date + int(maturity)
        payoff = ql.PlainVanillaPayoff(
            ql.Option.Call if option.option_type == "call" else ql.Option.Put,
            option.strike,
        )
        exercise = ql.AmericanExercise(evaluation_date, maturity_date)
        process = ql.BlackScholesMertonProcess(
            ql.QuoteHandle(ql.SimpleQuote(spot if spot is not None else option.spot)),
            ql.YieldTermStructureHandle(
                ql.FlatForward(0, calendar, option.dividend_yield, day_count)
            ),
            ql.YieldTermStructureHandle(
                ql.FlatForward(
                    0,
                    calendar,
                    risk_free_rate if risk_free_rate is not None else option.risk_free_rate,
                    day_count,
                )
            ),
            ql.BlackVolTermStructureHandle(
                ql.BlackConstantVol(
                    0,
                    calendar,
                    volatility if volatility is not None else option.volatility,
                    day_count,
                )
            ),
        )
        instrument = ql.VanillaOption(payoff, exercise)
        instrument.setPricingEngine(ql.BinomialVanillaEngine(process, "crr", 200))
        return instrument.NPV()

    def _finite_difference_greeks(
        self,
        option: GovernedOptionSpec,
        *,
        valuation_date: dt.date,
        base_npv: float | None = None,
    ) -> dict[str, float]:
        base = base_npv if base_npv is not None else self._american_npv(
            option, valuation_date=valuation_date
        )
        spot_bump = max(option.spot * 0.01, 0.01)
        vol_bump = 0.01
        rate_bump = 0.0001

        up = self._american_npv(
            option,
            valuation_date=valuation_date,
            spot=option.spot + spot_bump,
        )
        down = self._american_npv(
            option,
            valuation_date=valuation_date,
            spot=max(0.01, option.spot - spot_bump),
        )
        vol_up = self._american_npv(
            option,
            valuation_date=valuation_date,
            volatility=option.volatility + vol_bump,
        )
        rate_up = self._american_npv(
            option,
            valuation_date=valuation_date,
            risk_free_rate=option.risk_free_rate + rate_bump,
        )
        next_day = self._american_npv(
            option,
            valuation_date=valuation_date + dt.timedelta(days=1),
            maturity_days=max(1, option.maturity_days - 1),
        )
        return {
            "delta": round(((up - down) / (2.0 * spot_bump)) * option.quantity, 6),
            "gamma": round(((up - 2.0 * base + down) / (spot_bump**2)) * abs(option.quantity), 6),
            "vega": round((vol_up - base) * abs(option.quantity), 6),
            "theta": round((next_day - base) * option.quantity, 6),
            "rho": round((((rate_up - base) / rate_bump) / 100.0) * option.quantity, 6),
        }

    def analyze_fixed_income(self, snapshot: GovernedMarketSnapshot) -> dict[str, Any]:
        import QuantLib as ql

        valuation_dt = dt.date.fromisoformat(snapshot.valuation_date)
        ql.Settings.instance().evaluationDate = ql.Date(
            valuation_dt.day, valuation_dt.month, valuation_dt.year
        )

        results: dict[str, Any] = {}
        for bond in snapshot.bond_specs:
            schedule = ql.Schedule(
                ql.Settings.instance().evaluationDate,
                ql.Settings.instance().evaluationDate + ql.Period(bond.maturity_years, ql.Years),
                ql.Period(int(12 / bond.payment_frequency), ql.Months),
                ql.NullCalendar(),
                ql.Unadjusted,
                ql.Unadjusted,
                ql.DateGeneration.Forward,
                False,
            )
            instrument = ql.FixedRateBond(0, bond.face_value, schedule, [bond.coupon_rate], ql.ActualActual(ql.ActualActual.ISDA))
            discount_curve = ql.YieldTermStructureHandle(
                ql.FlatForward(0, ql.NullCalendar(), bond.market_rate, ql.ActualActual(ql.ActualActual.ISDA))
            )
            instrument.setPricingEngine(ql.DiscountingBondEngine(discount_curve))
            clean_price = instrument.cleanPrice()
            duration = ql.BondFunctions.duration(
                instrument,
                ql.InterestRate(bond.market_rate, ql.ActualActual(ql.ActualActual.ISDA), ql.Compounded, ql.Semiannual),
                ql.Duration.Modified,
            )
            convexity = ql.BondFunctions.convexity(
                instrument,
                ql.InterestRate(bond.market_rate, ql.ActualActual(ql.ActualActual.ISDA), ql.Compounded, ql.Semiannual),
            )
            results[bond.instrument_id] = {
                "clean_price": round(clean_price, 6),
                "duration": round(duration, 6),
                "convexity": round(convexity, 6),
                "dv01": round(duration * clean_price * 0.0001, 6),
                "curve_points": [
                    {"tenor_years": 0.5, "zero_rate": round(max(0.0001, bond.market_rate - 0.0025), 6)},
                    {"tenor_years": float(bond.maturity_years), "zero_rate": round(bond.market_rate, 6)},
                ],
            }
        return results


def _build_artifact_bundle(*, analysis_path: str, results: dict[str, Any]) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "artifact_id": str(uuid.uuid4()),
        "artifact_family": "pricing_report",
        "framework": "quantlib",
        "analysis_path": analysis_path,
        "produced_at": now,
        "results_summary": results,
        "governance": {
            "direct_live_influence": False,
            "lean_consumption": "research_only_not_direct_action",
            "write_boundary": "research_plane_only",
        },
        "registry_entry": {
            "artifact_type": "research_report",
            "artifact_state": "draft",
            "deployment_summary": {
                "current_stage": "none",
            },
        },
    }


def run_quantlib_workflow(
    snapshot: GovernedMarketSnapshot,
    *,
    analysis_paths: list[str] | None = None,
    backend: StubQuantLibBackend | QuantLibBackend | None = None,
) -> dict[str, Any]:
    use_real = os.environ.get("PANTHEON_QUANTLIB_BACKEND", "stub").lower() == "real"
    if backend is None:
        backend = QuantLibBackend() if use_real else StubQuantLibBackend()

    validated = GovernedQuantLibInputAdapter().validate(snapshot)
    paths = analysis_paths or ["options_pricing", "fixed_income"]
    results: dict[str, Any] = {}

    if "options_pricing" in paths:
        results["options_pricing"] = backend.price_options(validated)
    if "fixed_income" in paths:
        results["fixed_income"] = backend.analyze_fixed_income(validated)

    return _build_artifact_bundle(analysis_path="+".join(paths), results=results)
