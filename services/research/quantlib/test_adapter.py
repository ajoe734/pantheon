"""Unit tests for the governed QuantLib adapter."""

from __future__ import annotations

import copy
import datetime as dt
import math
import subprocess
import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.quantlib_adapter import (
    GovernedBondSpec,
    GovernedMarketSnapshot,
    GovernedOptionSpec,
    GovernedQuantLibInputAdapter,
    QuantLibBackend,
    QuantLibWorkflowError,
    StubQuantLibBackend,
    _bs_metrics,
    run_quantlib_workflow,
)


def _snapshot() -> GovernedMarketSnapshot:
    return GovernedMarketSnapshot(
        dataset_id="dataset:test-quantlib",
        source_dataset_refs=("dataset:test-source",),
        valuation_date="2026-04-17",
        option_specs=(
            GovernedOptionSpec(
                option_id="opt-001",
                style="european",
                option_type="call",
                spot=100.0,
                strike=95.0,
                volatility=0.25,
                risk_free_rate=0.03,
                dividend_yield=0.01,
                maturity_days=120,
                quantity=5,
            ),
        ),
        bond_specs=(
            GovernedBondSpec(
                instrument_id="bond-001",
                face_value=1000.0,
                coupon_rate=0.04,
                market_rate=0.035,
                maturity_years=3,
                payment_frequency=2,
            ),
        ),
    )


def _american_snapshot() -> GovernedMarketSnapshot:
    return GovernedMarketSnapshot(
        dataset_id="dataset:test-quantlib-american",
        source_dataset_refs=("dataset:test-source-american",),
        valuation_date="2026-04-17",
        option_specs=(
            GovernedOptionSpec(
                option_id="opt-am-put-001",
                style="american",
                option_type="put",
                spot=100.0,
                strike=105.0,
                volatility=0.2,
                risk_free_rate=0.03,
                dividend_yield=0.01,
                maturity_days=180,
                quantity=2,
            ),
        ),
        bond_specs=_snapshot().bond_specs,
    )


class TestGovernedQuantLibInputAdapter(unittest.TestCase):
    def test_reject_non_governed_input(self) -> None:
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate({"dataset_id": "bad"})  # type: ignore[arg-type]

    def test_reject_missing_refs(self) -> None:
        bad = copy.deepcopy(_snapshot())
        bad = GovernedMarketSnapshot(
            dataset_id=bad.dataset_id,
            source_dataset_refs=(),
            valuation_date=bad.valuation_date,
            option_specs=bad.option_specs,
            bond_specs=bad.bond_specs,
        )
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate(bad)

    def test_reject_missing_option_specs(self) -> None:
        snap = _snapshot()
        bad = GovernedMarketSnapshot(
            dataset_id=snap.dataset_id,
            source_dataset_refs=snap.source_dataset_refs,
            valuation_date=snap.valuation_date,
            option_specs=(),
            bond_specs=snap.bond_specs,
        )
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate(bad)

    def test_reject_missing_bond_specs(self) -> None:
        snap = _snapshot()
        bad = GovernedMarketSnapshot(
            dataset_id=snap.dataset_id,
            source_dataset_refs=snap.source_dataset_refs,
            valuation_date=snap.valuation_date,
            option_specs=snap.option_specs,
            bond_specs=(),
        )
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate(bad)

    def test_reject_invalid_option_style(self) -> None:
        snap = _snapshot()
        bad_option = GovernedOptionSpec(**{**snap.option_specs[0].__dict__, "style": "asian"})
        bad = GovernedMarketSnapshot(
            dataset_id=snap.dataset_id,
            source_dataset_refs=snap.source_dataset_refs,
            valuation_date=snap.valuation_date,
            option_specs=(bad_option,),
            bond_specs=snap.bond_specs,
        )
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate(bad)

    def test_reject_non_positive_maturity(self) -> None:
        snap = _snapshot()
        bad_option = GovernedOptionSpec(**{**snap.option_specs[0].__dict__, "maturity_days": 0})
        bad = GovernedMarketSnapshot(
            dataset_id=snap.dataset_id,
            source_dataset_refs=snap.source_dataset_refs,
            valuation_date=snap.valuation_date,
            option_specs=(bad_option,),
            bond_specs=snap.bond_specs,
        )
        with self.assertRaises(QuantLibWorkflowError):
            GovernedQuantLibInputAdapter().validate(bad)

    def test_accept_valid_snapshot(self) -> None:
        snapshot = _snapshot()
        validated = GovernedQuantLibInputAdapter().validate(snapshot)
        self.assertIs(validated, snapshot)


class TestStubQuantLibBackend(unittest.TestCase):
    def test_stub_option_pricing_is_deterministic(self) -> None:
        snapshot = _snapshot()
        backend = StubQuantLibBackend()
        self.assertEqual(backend.price_options(snapshot), backend.price_options(snapshot))

    def test_stub_option_result_contains_greeks(self) -> None:
        snapshot = _snapshot()
        result = StubQuantLibBackend().price_options(snapshot)["opt-001"]
        for key in ("npv", "delta", "gamma", "vega", "theta", "rho"):
            self.assertIn(key, result)

    def test_stub_fixed_income_contains_risk_metrics(self) -> None:
        snapshot = _snapshot()
        result = StubQuantLibBackend().analyze_fixed_income(snapshot)["bond-001"]
        for key in ("clean_price", "duration", "convexity", "dv01"):
            self.assertIn(key, result)


@unittest.skipUnless(__import__("importlib").util.find_spec("QuantLib"), "QuantLib not installed")
class TestQuantLibBackend(unittest.TestCase):
    @staticmethod
    def _american_npv(
        option: GovernedOptionSpec,
        *,
        valuation_date: dt.date,
        maturity_days: int | None = None,
        spot: float | None = None,
        volatility: float | None = None,
        risk_free_rate: float | None = None,
    ) -> float:
        import QuantLib as ql

        ql.Settings.instance().evaluationDate = ql.Date(
            valuation_date.day, valuation_date.month, valuation_date.year
        )
        day_count = ql.Actual365Fixed()
        calendar = ql.NullCalendar()
        evaluation_date = ql.Settings.instance().evaluationDate
        maturity_date = evaluation_date + int(maturity_days if maturity_days is not None else option.maturity_days)
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

    @classmethod
    def _expected_american_greeks(
        cls, option: GovernedOptionSpec, valuation_date: dt.date
    ) -> dict[str, float]:
        base = cls._american_npv(option, valuation_date=valuation_date)
        spot_bump = max(option.spot * 0.01, 0.01)
        vol_bump = 0.01
        rate_bump = 0.0001
        up = cls._american_npv(
            option,
            valuation_date=valuation_date,
            spot=option.spot + spot_bump,
        )
        down = cls._american_npv(
            option,
            valuation_date=valuation_date,
            spot=max(0.01, option.spot - spot_bump),
        )
        vol_up = cls._american_npv(
            option,
            valuation_date=valuation_date,
            volatility=option.volatility + vol_bump,
        )
        rate_up = cls._american_npv(
            option,
            valuation_date=valuation_date,
            risk_free_rate=option.risk_free_rate + rate_bump,
        )
        next_day = cls._american_npv(
            option,
            valuation_date=valuation_date + dt.timedelta(days=1),
            maturity_days=max(1, option.maturity_days - 1),
        )
        return {
            "npv": round(base * abs(option.quantity), 6),
            "delta": round(((up - down) / (2.0 * spot_bump)) * option.quantity, 6),
            "gamma": round(((up - 2.0 * base + down) / (spot_bump**2)) * abs(option.quantity), 6),
            "vega": round((vol_up - base) * abs(option.quantity), 6),
            "theta": round((next_day - base) * option.quantity, 6),
            "rho": round((((rate_up - base) / rate_bump) / 100.0) * option.quantity, 6),
        }

    def test_american_option_greeks_follow_quantlib_bumped_engine(self) -> None:
        snapshot = _american_snapshot()
        option = snapshot.option_specs[0]
        valuation_date = dt.date.fromisoformat(snapshot.valuation_date)

        result = QuantLibBackend().price_options(snapshot)[option.option_id]
        expected = self._expected_american_greeks(option, valuation_date)
        baseline = _bs_metrics(option)

        self.assertEqual(result["model"], "binomial_crr")
        self.assertEqual(result["style"], "american")
        for key in ("npv", "delta", "gamma", "vega", "theta", "rho"):
            self.assertAlmostEqual(result[key], expected[key], places=3)

        divergences = {
            key: abs(expected[key] - baseline[key]) for key in ("delta", "gamma", "vega", "theta", "rho")
        }
        self.assertGreater(
            max(divergences.values()),
            0.01,
            msg=f"Expected at least one American Greek to diverge materially from the BS proxy: {divergences}",
        )
        for key in ("vega", "rho"):
            self.assertNotAlmostEqual(
                expected[key],
                baseline[key],
                places=3,
                msg=f"{key} unexpectedly collapsed back to the BS proxy: {divergences}",
            )


class TestRunQuantLibWorkflow(unittest.TestCase):
    def test_artifact_family(self) -> None:
        bundle = run_quantlib_workflow(_snapshot())
        self.assertEqual(bundle["artifact_family"], "pricing_report")

    def test_framework(self) -> None:
        bundle = run_quantlib_workflow(_snapshot())
        self.assertEqual(bundle["framework"], "quantlib")

    def test_governance_flags(self) -> None:
        bundle = run_quantlib_workflow(_snapshot())
        self.assertFalse(bundle["governance"]["direct_live_influence"])
        self.assertEqual(
            bundle["governance"]["lean_consumption"],
            "research_only_not_direct_action",
        )

    def test_registry_entry_defaults(self) -> None:
        bundle = run_quantlib_workflow(_snapshot())
        self.assertEqual(bundle["registry_entry"]["artifact_type"], "research_report")
        self.assertEqual(bundle["registry_entry"]["artifact_state"], "draft")
        self.assertEqual(bundle["registry_entry"]["deployment_summary"]["current_stage"], "none")

    def test_selective_option_only_path(self) -> None:
        bundle = run_quantlib_workflow(_snapshot(), analysis_paths=["options_pricing"])
        self.assertIn("options_pricing", bundle["results_summary"])
        self.assertNotIn("fixed_income", bundle["results_summary"])

    def test_selective_fixed_income_only_path(self) -> None:
        bundle = run_quantlib_workflow(_snapshot(), analysis_paths=["fixed_income"])
        self.assertIn("fixed_income", bundle["results_summary"])
        self.assertNotIn("options_pricing", bundle["results_summary"])

    def test_artifact_id_unique_per_run(self) -> None:
        b1 = run_quantlib_workflow(_snapshot())
        b2 = run_quantlib_workflow(_snapshot())
        self.assertNotEqual(b1["artifact_id"], b2["artifact_id"])


# ---------------------------------------------------------------------------
# Analytical Reference Fixtures (retained for verification of numerical core)
# ---------------------------------------------------------------------------

FROZEN_PRICE_TOLERANCE = 1e-4
FROZEN_DELTA_TOLERANCE = 1e-4
FROZEN_GAMMA_TOLERANCE = 1e-4
FROZEN_VEGA_TOLERANCE = 1e-3
FROZEN_THETA_TOLERANCE = 1e-3


def _ref_norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _ref_norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def reference_bsm_analytical(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: str,
    dividend_yield: float = 0.0,
) -> dict[str, float]:
    """Analytical reference BSM formula retained only as test fixture."""
    sqrt_t = math.sqrt(tenor)
    discount_rate = math.exp(-rate * tenor)
    discount_div = math.exp(-dividend_yield * tenor)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * tenor) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    pdf_d1 = _ref_norm_pdf(d1)

    if option_type == "call":
        price = spot * discount_div * _ref_norm_cdf(d1) - strike * discount_rate * _ref_norm_cdf(d2)
        delta = discount_div * _ref_norm_cdf(d1)
        theta_annual = (
            -(spot * discount_div * pdf_d1 * vol) / (2.0 * sqrt_t)
            - rate * strike * discount_rate * _ref_norm_cdf(d2)
            + dividend_yield * spot * discount_div * _ref_norm_cdf(d1)
        )
        rho = strike * tenor * discount_rate * _ref_norm_cdf(d2)
    else:
        price = strike * discount_rate * _ref_norm_cdf(-d2) - spot * discount_div * _ref_norm_cdf(-d1)
        delta = discount_div * (_ref_norm_cdf(d1) - 1.0)
        theta_annual = (
            -(spot * discount_div * pdf_d1 * vol) / (2.0 * sqrt_t)
            + rate * strike * discount_rate * _ref_norm_cdf(-d2)
            - dividend_yield * spot * discount_div * _ref_norm_cdf(-d1)
        )
        rho = -strike * tenor * discount_rate * _ref_norm_cdf(-d2)

    gamma = discount_div * pdf_d1 / (spot * vol * sqrt_t)
    vega = spot * discount_div * pdf_d1 * sqrt_t
    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta_annual / 365.0,
        "rho": rho,
    }


def reference_crr_american_analytical(
    spot: float,
    strike: float,
    rate: float,
    vol: float,
    tenor: float,
    option_type: str,
    steps: int = 512,
    dividend_yield: float = 0.0,
) -> float:
    """Analytical CRR tree reference formula retained only as test fixture."""
    dt = tenor / steps
    up = math.exp(vol * math.sqrt(dt))
    down = 1.0 / up
    discount = math.exp(-rate * dt)
    probability = (math.exp((rate - dividend_yield) * dt) - down) / (up - down)

    def payoff(s: float) -> float:
        return max(s - strike, 0.0) if option_type == "call" else max(strike - s, 0.0)

    values = [payoff(spot * (up**j) * (down ** (steps - j))) for j in range(steps + 1)]
    for level in range(steps - 1, -1, -1):
        values = [
            max(
                discount * (probability * values[j + 1] + (1.0 - probability) * values[j]),
                payoff(spot * (up**j) * (down ** (level - j))),
            )
            for j in range(level + 1)
        ]
    return values[0]


class TestQuantLibNumericalEngineVersusReferenceFixtures(unittest.TestCase):
    """Verify numerical QuantLib backend against analytical reference fixtures with frozen tolerances."""

    def test_european_call_and_put_match_reference_fixtures(self) -> None:
        from adapter import price_european

        test_cases = [
            (100.0, 100.0, 0.05, 0.20, 1.0, 0.0),
            (105.0, 100.0, 0.03, 0.25, 0.5, 0.01),
            (95.0, 100.0, 0.02, 0.18, 0.75, 0.03),
            (21000.0, 20400.0, 0.015, 0.238, 31 / 365.0, 0.012),
            (21000.0, 21600.0, 0.015, 0.242, 60 / 365.0, 0.012),
        ]
        for spot, strike, rate, vol, tenor, div in test_cases:
            for opt_type in ("call", "put"):
                ql_result = price_european(spot, strike, rate, vol, tenor, opt_type, dividend_yield=div)
                ref_result = reference_bsm_analytical(spot, strike, rate, vol, tenor, opt_type, dividend_yield=div)

                self.assertAlmostEqual(
                    ql_result["price"], ref_result["price"], delta=FROZEN_PRICE_TOLERANCE,
                    msg=f"Price mismatch for {opt_type} at S={spot}, K={strike}"
                )
                self.assertAlmostEqual(
                    ql_result["delta"], ref_result["delta"], delta=FROZEN_DELTA_TOLERANCE,
                    msg=f"Delta mismatch for {opt_type} at S={spot}, K={strike}"
                )
                self.assertAlmostEqual(
                    ql_result["gamma"], ref_result["gamma"], delta=FROZEN_GAMMA_TOLERANCE,
                    msg=f"Gamma mismatch for {opt_type} at S={spot}, K={strike}"
                )
                self.assertAlmostEqual(
                    ql_result["vega"], ref_result["vega"], delta=FROZEN_VEGA_TOLERANCE,
                    msg=f"Vega mismatch for {opt_type} at S={spot}, K={strike}"
                )
                self.assertAlmostEqual(
                    ql_result["theta"], ref_result["theta"], delta=FROZEN_THETA_TOLERANCE,
                    msg=f"Theta mismatch for {opt_type} at S={spot}, K={strike}"
                )

    def test_american_binomial_crr_matches_reference_fixtures(self) -> None:
        from adapter import price_american_binomial, price_european

        spot, strike, rate, vol, tenor = 105.0, 100.0, 0.03, 0.20, 0.5
        ql_call = price_american_binomial(spot, strike, rate, vol, tenor, "call", steps=512)
        ref_call = reference_crr_american_analytical(spot, strike, rate, vol, tenor, "call", steps=512)
        self.assertAlmostEqual(ql_call["price"], ref_call, delta=1e-3)

        # Non-dividend American call converges to European call
        euro_call = price_european(spot, strike, rate, vol, tenor, "call")
        self.assertAlmostEqual(ql_call["price"], euro_call["price"], delta=1e-3)

        # American put reflects early exercise premium
        ql_put = price_american_binomial(spot, 95.0, rate, vol, tenor, "put", steps=512)
        euro_put = price_european(spot, 95.0, rate, vol, tenor, "put")
        self.assertGreaterEqual(ql_put["price"], euro_put["price"] - 1e-4)

    def test_evaluation_date_preservation_and_moving_curve_regression(self) -> None:
        """P1 regression: ensure caller Settings.evaluationDate and moving curves are not mutated."""
        import QuantLib as ql
        from adapter import price_american_binomial

        settings = ql.Settings.instance()
        target_date = ql.Date(27, 9, 2026)
        settings.evaluationDate = target_date
        curve = ql.FlatForward(0, ql.NullCalendar(), 0.03, ql.Actual365Fixed())

        self.assertEqual(settings.evaluationDate, target_date)
        self.assertEqual(curve.referenceDate(), target_date)

        # Call price_american_binomial
        res = price_american_binomial(100.0, 100.0, 0.03, 0.2, 0.5, "put")
        self.assertGreater(res["price"], 0.0)

        # Verify evaluationDate and moving curve referenceDate remain untouched
        self.assertEqual(settings.evaluationDate, target_date)
        self.assertEqual(curve.referenceDate(), target_date)

        # Verify preservation on error
        with self.assertRaises(ValueError):
            price_american_binomial(-10.0, 100.0, 0.03, 0.2, 0.5, "put")

        self.assertEqual(settings.evaluationDate, target_date)
        self.assertEqual(curve.referenceDate(), target_date)

    def test_american_binomial_crr_matches_reference_fixtures_with_dividends(self) -> None:
        """Verify American CRR pricing with continuous dividend yield against independent analytical tree."""
        from adapter import price_american_binomial

        test_cases = [
            (100.0, 100.0, 0.05, 0.20, 0.5, 0.02),
            (105.0, 100.0, 0.03, 0.25, 0.75, 0.04),
            (95.0, 100.0, 0.04, 0.18, 0.5, 0.03),
        ]
        for spot, strike, rate, vol, tenor, div in test_cases:
            for opt_type in ("call", "put"):
                ql_res = price_american_binomial(spot, strike, rate, vol, tenor, opt_type, steps=512, dividend_yield=div)
                ref_res = reference_crr_american_analytical(spot, strike, rate, vol, tenor, opt_type, steps=512, dividend_yield=div)
                self.assertAlmostEqual(
                    ql_res["price"], ref_res, delta=2e-3,
                    msg=f"American dividend CRR mismatch for {opt_type} at S={spot}, K={strike}, div={div}"
                )

    def test_american_option_early_exercise_premium_and_greeks_with_dividends(self) -> None:
        """Verify early exercise premium and Greeks behavior on dividend-paying options."""
        from adapter import price_american_binomial, price_european

        spot, strike, rate, vol, tenor, div = 100.0, 80.0, 0.02, 0.20, 0.5, 0.08
        am_call = price_american_binomial(
            spot, strike, rate, vol, tenor, "call", steps=512, dividend_yield=div, include_extended_greeks=True
        )
        eu_call = price_european(spot, strike, rate, vol, tenor, "call", dividend_yield=div)

        # Early exercise premium exists for deep ITM dividend-paying call: American > European
        self.assertGreater(am_call["price"], eu_call["price"] + 0.1)

        # Verify Greeks boundaries
        self.assertGreater(am_call["delta"], 0.8)
        self.assertLessEqual(am_call["delta"], 1.0)
        self.assertGreaterEqual(am_call["gamma"], 0.0)
        self.assertGreaterEqual(am_call["vega"], 0.0)

        # Put with dividends: higher dividend increases put price compared to zero dividend
        am_put_div = price_american_binomial(
            spot, strike, rate, vol, tenor, "put", steps=512, dividend_yield=div, include_extended_greeks=True
        )
        am_put_nodiv = price_american_binomial(
            spot, strike, rate, vol, tenor, "put", steps=512, dividend_yield=0.0, include_extended_greeks=True
        )
        self.assertGreater(am_put_div["price"], am_put_nodiv["price"])
        self.assertLess(am_put_div["delta"], 0.0)
        self.assertGreaterEqual(am_put_div["delta"], -1.0)

    def test_american_option_rates_sensitivity_and_greeks(self) -> None:
        """Verify American option pricing and Greeks across various interest rate regimes."""
        from adapter import price_american_binomial

        spot, strike, vol, tenor = 100.0, 100.0, 0.20, 0.5
        m_zero_rate = price_american_binomial(
            spot, strike, 0.0, vol, tenor, "call", steps=512, include_extended_greeks=True
        )
        m_high_rate = price_american_binomial(
            spot, strike, 0.08, vol, tenor, "call", steps=512, include_extended_greeks=True
        )

        # Higher interest rate increases call price, decreases put price
        self.assertGreater(m_high_rate["price"], m_zero_rate["price"])
        self.assertGreater(m_high_rate["rho"], 0.0)

        m_put_zero = price_american_binomial(
            spot, strike, 0.0, vol, tenor, "put", steps=512, include_extended_greeks=True
        )
        m_put_high = price_american_binomial(
            spot, strike, 0.08, vol, tenor, "put", steps=512, include_extended_greeks=True
        )
        self.assertLess(m_put_high["price"], m_put_zero["price"])
        self.assertLess(m_put_high["rho"], 0.0)

        # Deep ITM American put satisfies price >= strike - spot (early exercise boundary)
        deep_itm_put = price_american_binomial(
            50.0, 100.0, 0.05, 0.20, 0.5, "put", steps=512, include_extended_greeks=True
        )
        self.assertGreaterEqual(deep_itm_put["price"], 50.0 - 1e-4)
        self.assertAlmostEqual(deep_itm_put["delta"], -1.0, delta=0.05)

    def test_american_option_short_maturity_and_boundary_behavior(self) -> None:
        """Verify American option CRR stability at very short maturities."""
        from adapter import price_american_binomial

        for short_tenor in (1.0 / 365.0, 0.5 / 365.0, 1e-4):
            # ITM Call -> price converges to spot - strike
            m_itm_c = price_american_binomial(
                105.0, 100.0, 0.03, 0.20, short_tenor, "call", steps=128, include_extended_greeks=True
            )
            self.assertAlmostEqual(m_itm_c["price"], 5.0, delta=0.2)
            self.assertTrue(math.isfinite(m_itm_c["delta"]))
            self.assertTrue(math.isfinite(m_itm_c["gamma"]))
            self.assertTrue(math.isfinite(m_itm_c["vega"]))

            # OTM Put -> price converges to 0
            m_otm_p = price_american_binomial(
                105.0, 100.0, 0.03, 0.20, short_tenor, "put", steps=128, include_extended_greeks=True
            )
            self.assertAlmostEqual(m_otm_p["price"], 0.0, delta=0.1)

    def test_american_option_calendar_to_pricing_conventions(self) -> None:
        """Verify calendar-to-pricing conventions matching QuantLib Actual365Fixed day-counting."""
        import QuantLib as ql
        from adapter import price_american_binomial

        calendar = ql.Taiwan()
        day_count = ql.Actual365Fixed()
        d_val = ql.Date(17, 4, 2026)
        d_mat = ql.Date(17, 10, 2026)
        days = d_mat - d_val
        tenor_from_dates = day_count.yearFraction(d_val, d_mat)
        self.assertEqual(days, 183)
        self.assertAlmostEqual(tenor_from_dates, 183.0 / 365.0, places=6)

        # Price American option with tenor derived from official calendar dates
        res = price_american_binomial(100.0, 100.0, 0.03, 0.22, tenor_from_dates, "call", steps=512)
        self.assertGreater(res["price"], 0.0)
        self.assertGreater(res["delta"], 0.5)
        self.assertGreater(res["vega"], 0.0)


class TestQuantLibCalendarsAndConventions(unittest.TestCase):
    """Verify official QuantLib calendar conventions."""

    def test_supported_calendars(self) -> None:
        import QuantLib as ql

        calendars = {
            "Null": ql.NullCalendar(),
            "TARGET": ql.TARGET(),
            "Taiwan": ql.Taiwan(),
            "US_NYSE": ql.UnitedStates(ql.UnitedStates.NYSE),
            "US_Settlement": ql.UnitedStates(ql.UnitedStates.Settlement),
        }
        ref_date = ql.Date(1, 1, 2026)
        for name, cal in calendars.items():
            self.assertIsNotNone(cal.name())
            advanced = cal.advance(ref_date, 1, ql.Days)
            self.assertGreater(advanced, ref_date)

    def test_calendar_day_count_and_year_fraction(self) -> None:
        import QuantLib as ql

        day_count_365 = ql.Actual365Fixed()
        day_count_act = ql.ActualActual(ql.ActualActual.ISDA)
        d1 = ql.Date(1, 1, 2026)
        d2 = ql.Date(1, 7, 2026)
        yf365 = day_count_365.yearFraction(d1, d2)
        yfact = day_count_act.yearFraction(d1, d2)
        self.assertAlmostEqual(yf365, 181.0 / 365.0, places=5)
        self.assertAlmostEqual(yfact, 181.0 / 365.0, places=5)


class TestZeroAndShortMaturity(unittest.TestCase):
    """Verify zero and short maturity handling in QuantLib numerical engine."""

    def test_zero_maturity_boundary_values(self) -> None:
        from adapter import price_american_binomial, price_european

        # European Call: ITM
        res = price_european(spot=110.0, strike=100.0, rate=0.03, vol=0.20, tenor=0.0, option_type="call")
        self.assertEqual(res["price"], 10.0)
        self.assertEqual(res["delta"], 1.0)
        self.assertEqual(res["gamma"], 0.0)
        self.assertEqual(res["vega"], 0.0)
        self.assertEqual(res["theta"], 0.0)

        # European Call: OTM
        res = price_european(spot=90.0, strike=100.0, rate=0.03, vol=0.20, tenor=0.0, option_type="call")
        self.assertEqual(res["price"], 0.0)
        self.assertEqual(res["delta"], 0.0)

        # European Put: ITM
        res = price_european(spot=90.0, strike=100.0, rate=0.03, vol=0.20, tenor=0.0, option_type="put")
        self.assertEqual(res["price"], 10.0)
        self.assertEqual(res["delta"], -1.0)

        # European Put: OTM
        res = price_european(spot=110.0, strike=100.0, rate=0.03, vol=0.20, tenor=0.0, option_type="put")
        self.assertEqual(res["price"], 0.0)
        self.assertEqual(res["delta"], 0.0)

        # American Binomial at tenor=0.0
        am_call = price_american_binomial(110.0, 100.0, 0.03, 0.20, 0.0, "call")
        self.assertEqual(am_call["price"], 10.0)
        am_put = price_american_binomial(90.0, 100.0, 0.03, 0.20, 0.0, "put")
        self.assertEqual(am_put["price"], 10.0)

    def test_short_maturity_numerical_stability(self) -> None:
        from adapter import price_european

        for short_tenor in (1.0 / 365.0, 0.1 / 365.0, 1e-4):
            res_call = price_european(100.0, 100.0, 0.03, 0.20, short_tenor, "call")
            self.assertGreater(res_call["price"], 0.0)
            self.assertTrue(math.isfinite(res_call["delta"]))
            self.assertTrue(math.isfinite(res_call["gamma"]))
            self.assertTrue(math.isfinite(res_call["vega"]))


class TestQuantLibEdgeCases(unittest.TestCase):
    """Verify deep ITM/OTM, extreme volatility, and interest rate boundaries."""

    def test_deep_in_the_money(self) -> None:
        from adapter import price_european

        # Deep ITM Call
        res = price_european(spot=1000.0, strike=10.0, rate=0.03, vol=0.20, tenor=1.0, option_type="call")
        self.assertAlmostEqual(res["delta"], 1.0, delta=1e-3)
        self.assertAlmostEqual(res["gamma"], 0.0, delta=1e-3)

        # Deep ITM Put
        res_put = price_european(spot=10.0, strike=1000.0, rate=0.03, vol=0.20, tenor=1.0, option_type="put")
        self.assertAlmostEqual(res_put["delta"], -1.0, delta=1e-3)
        self.assertAlmostEqual(res_put["gamma"], 0.0, delta=1e-3)

    def test_deep_out_of_the_money(self) -> None:
        from adapter import price_european

        # Deep OTM Call
        res = price_european(spot=10.0, strike=1000.0, rate=0.03, vol=0.20, tenor=1.0, option_type="call")
        self.assertAlmostEqual(res["price"], 0.0, delta=1e-6)
        self.assertAlmostEqual(res["delta"], 0.0, delta=1e-6)

        # Deep OTM Put
        res_put = price_european(spot=1000.0, strike=10.0, rate=0.03, vol=0.20, tenor=1.0, option_type="put")
        self.assertAlmostEqual(res_put["price"], 0.0, delta=1e-6)
        self.assertAlmostEqual(res_put["delta"], 0.0, delta=1e-6)

    def test_extreme_volatility(self) -> None:
        from adapter import price_european

        # Very low vol
        res_low = price_european(100.0, 100.0, 0.03, 0.0001, 1.0, "call")
        self.assertGreater(res_low["price"], 0.0)

        # High vol
        res_high = price_european(100.0, 100.0, 0.03, 3.0, 1.0, "call")
        self.assertGreater(res_high["price"], res_low["price"])

    def test_zero_and_negative_rates(self) -> None:
        from adapter import price_european

        # Zero rate
        res_zero = price_european(100.0, 100.0, 0.0, 0.20, 1.0, "call")
        self.assertGreater(res_zero["price"], 0.0)

        # Negative rate
        res_neg = price_european(100.0, 100.0, -0.005, 0.20, 1.0, "call")
        self.assertGreater(res_neg["price"], 0.0)


class TestConsumerImportsWithoutQuantLib(unittest.TestCase):
    """Subprocess regression proving DTO, admission, and persona imports without QuantLib."""

    def _find_repo_root(self) -> Path | None:
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "services" / "persona" / "oss_runtime.py").is_file():
                return parent
        return None

    def test_adapter_module_import_succeeds_without_quantlib(self) -> None:
        """Verify adapter.py itself can be imported when QuantLib is not installed."""
        adapter_path = Path(__file__).resolve().parent / "adapter.py"
        script = (
            "import sys\n"
            "sys.modules['QuantLib'] = None\n"
            "import importlib.util\n"
            f"adapter_path = r'{adapter_path}'\n"
            "spec = importlib.util.spec_from_file_location('adapter_isolated', adapter_path)\n"
            "assert spec is not None and spec.loader is not None\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "assert hasattr(mod, 'price_european')\n"
            "assert hasattr(mod, 'price_american_binomial')\n"
            "try:\n"
            "    mod.price_european(100.0, 100.0, 0.03, 0.2, 1.0, 'call')\n"
            "except (RuntimeError, ModuleNotFoundError):\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('price_european must fail when QuantLib is absent')\n"
            "print('ADAPTER_WITHOUT_QUANTLIB_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Subprocess failed with code {proc.returncode}:\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn("ADAPTER_WITHOUT_QUANTLIB_OK", proc.stdout)

    def test_consumer_imports_in_isolated_process_without_quantlib(self) -> None:
        repo_root = self._find_repo_root()
        if repo_root is None:
            self.skipTest("Full repository tree (services/persona) not present in test environment")
        script = (
            "import sys\n"
            "sys.modules['QuantLib'] = None\n"
            "from services.research.quantlib.adapter.quantlib_adapter import (\n"
            "    GovernedMarketSnapshot, GovernedOptionSpec, GovernedBondSpec\n"
            ")\n"
            "from services.research.quantlib.registry_admission_packet import validate_admission_packet\n"
            "import services.persona.oss_runtime\n"
            "import services.research.quantlib as ql_pkg\n"
            "assert hasattr(ql_pkg, 'price_european')\n"
            "assert hasattr(ql_pkg, 'price_american_binomial')\n"
            "try:\n"
            "    ql_pkg.price_european(100.0, 100.0, 0.03, 0.2, 1.0, 'call')\n"
            "except (RuntimeError, ModuleNotFoundError):\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('price_european must fail when QuantLib is absent')\n"
            "print('ISOLATED_CONSUMER_IMPORTS_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Subprocess failed with code {proc.returncode}:\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn("ISOLATED_CONSUMER_IMPORTS_OK", proc.stdout)

    def test_consumer_imports_with_system_python_if_available(self) -> None:
        system_py = "/usr/bin/python3"
        if not Path(system_py).is_file():
            self.skipTest(f"{system_py} is not available on this host")
        repo_root = self._find_repo_root()
        if repo_root is None:
            self.skipTest("Full repository tree (services/persona) not present in test environment")
        script = (
            "from services.research.quantlib.adapter.quantlib_adapter import (\n"
            "    GovernedMarketSnapshot, GovernedOptionSpec, GovernedBondSpec\n"
            ")\n"
            "from services.research.quantlib.registry_admission_packet import validate_admission_packet\n"
            "import services.persona.oss_runtime\n"
            "print('SYSTEM_PYTHON_CONSUMER_IMPORTS_OK')\n"
        )
        proc = subprocess.run(
            [system_py, "-c", script],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"System python subprocess failed with code {proc.returncode}:\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn("SYSTEM_PYTHON_CONSUMER_IMPORTS_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
