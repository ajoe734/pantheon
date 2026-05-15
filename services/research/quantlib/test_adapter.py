"""Unit tests for the governed QuantLib adapter."""

from __future__ import annotations

import copy
import datetime as dt
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


if __name__ == "__main__":
    unittest.main()
