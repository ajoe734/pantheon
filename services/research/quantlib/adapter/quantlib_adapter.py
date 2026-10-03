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


def _get_core_adapter():
    import importlib.util
    from pathlib import Path

    adapter_file = Path(__file__).resolve().parents[1] / "adapter.py"
    spec = importlib.util.spec_from_file_location("_quantlib_core_adapter", adapter_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load QuantLib option adapter from {adapter_file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bs_metrics(option: GovernedOptionSpec) -> dict[str, float]:
    """Compute option metrics via the unified QuantLib numerical backend."""
    adapter_mod = _get_core_adapter()
    t = option.maturity_days / 365.0
    res = adapter_mod.price_european(
        spot=option.spot,
        strike=option.strike,
        rate=option.risk_free_rate,
        vol=option.volatility,
        tenor=t,
        option_type=option.option_type,
        dividend_yield=option.dividend_yield,
    )
    return {
        "npv": round(res["price"] * abs(option.quantity), 6),
        "delta": round(res["delta"] * option.quantity, 6),
        "gamma": round(res["gamma"] * abs(option.quantity), 6),
        "vega": round(res["vega"] * abs(option.quantity) / 100.0, 6),
        "theta": round(res["theta"] * option.quantity, 6),
        "rho": round(res["rho"] * option.quantity / 100.0, 6),
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
        adapter_mod = _get_core_adapter()
        import QuantLib as ql

        settings = ql.Settings.instance()
        prev_date = settings.evaluationDate
        try:
            valuation_dt = dt.date.fromisoformat(snapshot.valuation_date)
            settings.evaluationDate = ql.Date(
                valuation_dt.day, valuation_dt.month, valuation_dt.year
            )

            priced: dict[str, Any] = {}
            for option in snapshot.option_specs:
                if option.style == "european":
                    metrics = _bs_metrics(option)
                    result = {
                        **metrics,
                        "model": "analytic_european",
                        "style": option.style,
                        "option_type": option.option_type,
                    }
                else:
                    t = option.maturity_days / 365.0
                    m = adapter_mod.american_binomial_metrics(
                        spot=option.spot,
                        strike=option.strike,
                        rate=option.risk_free_rate,
                        vol=option.volatility,
                        tenor=t,
                        option_type=option.option_type,
                        steps=200,
                        dividend_yield=option.dividend_yield,
                    )
                    result = {
                        "npv": round(m["price"] * abs(option.quantity), 6),
                        "delta": round(m["delta"] * option.quantity, 6),
                        "gamma": round(m["gamma"] * abs(option.quantity), 6),
                        "vega": round(m["vega"] * abs(option.quantity) / 100.0, 6),
                        "theta": round(m["theta"] * option.quantity, 6),
                        "rho": round(m["rho"] * option.quantity / 100.0, 6),
                        "model": "binomial_crr",
                        "style": option.style,
                        "option_type": option.option_type,
                    }

                priced[option.option_id] = result
            return priced
        finally:
            settings.evaluationDate = prev_date

    def analyze_fixed_income(self, snapshot: GovernedMarketSnapshot) -> dict[str, Any]:
        import QuantLib as ql

        settings = ql.Settings.instance()
        prev_date = settings.evaluationDate
        try:
            valuation_dt = dt.date.fromisoformat(snapshot.valuation_date)
            settings.evaluationDate = ql.Date(
                valuation_dt.day, valuation_dt.month, valuation_dt.year
            )

            results: dict[str, Any] = {}
            for bond in snapshot.bond_specs:
                schedule = ql.Schedule(
                    settings.evaluationDate,
                    settings.evaluationDate + ql.Period(bond.maturity_years, ql.Years),
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
        finally:
            settings.evaluationDate = prev_date


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
