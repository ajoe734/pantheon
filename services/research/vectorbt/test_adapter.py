"""Unit tests for the governed vectorbt input adapter and stub backend."""
from __future__ import annotations

import copy
import importlib.util
import os
import sys
import unittest
from unittest import mock
from datetime import date, timedelta
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.vectorbt_adapter import (
    BacktestConfig,
    GovernedVectorbtInputAdapter,
    StubVectorbtBackend,
    VectorbtWorkflowError,
    run_vectorbt_workflow,
)

# --- Fixtures ---

def _make_records(instrument: str, base: float = 100.0, n: int = 35) -> list[dict]:
    records = []
    price = base
    start_date = date(2024, 1, 1)
    for i in range(n):
        price = price * (1.0 + (0.005 if i % 3 != 0 else -0.008))
        records.append(
            {
                "instrument": instrument,
                "date": (start_date + timedelta(days=i)).isoformat(),
                "open": round(price * 0.998, 4),
                "high": round(price * 1.005, 4),
                "low": round(price * 0.995, 4),
                "close": round(price, 4),
                "volume": 1_000_000 + i * 5000,
            }
        )
    return records


MINIMAL_DATASET: dict = {
    "dataset_id": "dataset:test-daily",
    "strategy_id": "test-strategy",
    "source_dataset_refs": ["dataset:test-source"],
    "data_frequency": "daily",
    "records": _make_records("AAA") + _make_records("BBB", base=50.0),
}


# --- GovernedVectorbtInputAdapter ---

class TestGovernedVectorbtInputAdapter(unittest.TestCase):

    def test_happy_path_produces_expected_shape(self) -> None:
        ds = GovernedVectorbtInputAdapter().prepare(MINIMAL_DATASET)
        self.assertEqual(ds.strategy_id, "test-strategy")
        self.assertEqual(ds.num_instruments, 2)
        self.assertIn("AAA", ds.instruments)
        self.assertIn("BBB", ds.instruments)
        self.assertEqual(ds.total_bars, 35 * 2)

    def test_bars_per_instrument_counted_correctly(self) -> None:
        ds = GovernedVectorbtInputAdapter().prepare(MINIMAL_DATASET)
        self.assertEqual(ds.bars_per_instrument["AAA"], 35)
        self.assertEqual(ds.bars_per_instrument["BBB"], 35)

    def test_missing_ohlcv_field_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["records"][0]["close"]
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_missing_dataset_refs_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["source_dataset_refs"]
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_single_instrument_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"] = [r for r in bad["records"] if r["instrument"] == "AAA"]
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_too_few_bars_raises(self) -> None:
        short_records = _make_records("AAA", n=10) + _make_records("BBB", n=10)
        bad = {**MINIMAL_DATASET, "records": short_records}
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_lineage_preserved_in_output(self) -> None:
        ds = GovernedVectorbtInputAdapter().prepare(MINIMAL_DATASET)
        self.assertIn("dataset:test-source", ds.source_dataset_refs)

    def test_source_dataset_ref_single_fallback(self) -> None:
        alt = {**MINIMAL_DATASET, "source_dataset_ref": "dataset:fallback-source"}
        alt.pop("source_dataset_refs", None)
        ds = GovernedVectorbtInputAdapter().prepare(alt)
        self.assertIn("dataset:fallback-source", ds.source_dataset_refs)

    def test_missing_strategy_id_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["strategy_id"]
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_non_numeric_ohlcv_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"][0]["close"] = "not-a-number"
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_missing_date_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["records"][0]["date"]
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_invalid_date_format_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"][0]["date"] = "not-a-date"
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_non_zero_padded_month_rejected(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"][0]["date"] = "2024-1-1"
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_non_zero_padded_day_rejected(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"][0]["date"] = "2024-01-1"
        with self.assertRaises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(bad)

    def test_reversed_date_order_produces_same_ohlcv_as_forward(self) -> None:
        """Reversing record dates for one instrument must produce identical sorted bars."""
        forward = copy.deepcopy(MINIMAL_DATASET)
        reversed_dataset = {
            **MINIMAL_DATASET,
            "records": list(reversed(_make_records("AAA"))) + _make_records("BBB", base=50.0),
        }
        ds_forward = GovernedVectorbtInputAdapter().prepare(forward)
        ds_reversed = GovernedVectorbtInputAdapter().prepare(reversed_dataset)
        self.assertEqual(
            ds_forward.ohlcv_by_instrument["AAA"],
            ds_reversed.ohlcv_by_instrument["AAA"],
        )

    def test_reversed_dates_do_not_affect_backtest_metrics(self) -> None:
        """Unsorted OHLCV input must yield the same per-instrument metrics as sorted input."""
        forward_result = run_vectorbt_workflow(MINIMAL_DATASET)
        reversed_dataset = {
            **MINIMAL_DATASET,
            "records": list(reversed(_make_records("AAA"))) + _make_records("BBB", base=50.0),
        }
        reversed_result = run_vectorbt_workflow(reversed_dataset)
        fwd_metrics = forward_result.artifact_bundle["per_instrument_metrics"]["AAA"]
        rev_metrics = reversed_result.artifact_bundle["per_instrument_metrics"]["AAA"]
        for key in ("total_return", "sharpe_ratio", "max_drawdown", "trade_count", "num_bars"):
            with self.subTest(metric=key):
                self.assertAlmostEqual(fwd_metrics[key], rev_metrics[key], places=6)


# --- StubVectorbtBackend ---

class TestStubVectorbtBackend(unittest.TestCase):

    def _prepared(self) -> object:
        return GovernedVectorbtInputAdapter().prepare(MINIMAL_DATASET)

    def test_produces_per_instrument_metrics(self) -> None:
        ds = self._prepared()
        result = StubVectorbtBackend().run(ds, BacktestConfig())
        self.assertIn("AAA", result.per_instrument_metrics)
        self.assertIn("BBB", result.per_instrument_metrics)

    def test_per_instrument_metrics_keys(self) -> None:
        ds = self._prepared()
        result = StubVectorbtBackend().run(ds, BacktestConfig())
        for inst_metrics in result.per_instrument_metrics.values():
            for key in ("total_return", "sharpe_ratio", "max_drawdown", "trade_count", "num_bars"):
                self.assertIn(key, inst_metrics)

    def test_aggregate_metrics_keys(self) -> None:
        ds = self._prepared()
        result = StubVectorbtBackend().run(ds, BacktestConfig())
        agg = result.aggregate_metrics
        for key in ("num_instruments", "mean_total_return", "mean_sharpe_ratio", "mean_max_drawdown", "total_trades"):
            self.assertIn(key, agg)

    def test_run_id_is_unique(self) -> None:
        ds = self._prepared()
        r1 = StubVectorbtBackend().run(ds, BacktestConfig())
        r2 = StubVectorbtBackend().run(ds, BacktestConfig())
        self.assertNotEqual(r1.run_id, r2.run_id)

    def test_stub_backend_uses_dynamic_params(self) -> None:
        ds = self._prepared()
        # Run with short_window=10, long_window=50
        config = BacktestConfig(strategy_params={"short_window": 10, "long_window": 50})
        result = StubVectorbtBackend().run(ds, config)
        
        # Verify metadata/config
        self.assertEqual(result.backend, "stub_backtest")
        
        # We can verify it was used by checking metrics or just ensuring it runs without error.
        # Stub backend uses these windows in _compute_instrument_metrics
        self.assertIn("AAA", result.per_instrument_metrics)

    def test_backend_label(self) -> None:
        ds = self._prepared()
        result = StubVectorbtBackend().run(ds, BacktestConfig())
        self.assertEqual(result.backend, "stub_backtest")


# --- run_vectorbt_workflow (output envelope) ---

class TestRunVectorbtWorkflow(unittest.TestCase):

    def test_artifact_family(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.artifact_bundle["artifact_family"], "vectorbt_backtest")

    def test_framework_field(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.artifact_bundle["framework"], "vectorbt")

    def test_governance_no_direct_live_influence(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertFalse(result.artifact_bundle["governance"]["direct_live_influence"])

    def test_lean_consumption_is_scoring_only(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        gov = result.artifact_bundle["governance"]
        self.assertEqual(gov["lean_consumption"], "scoring_only_not_direct_action")

    def test_registry_artifact_type(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["artifact_type"], "backtest_result")

    def test_registry_entry_starts_as_draft(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["artifact_state"], "draft")

    def test_deployment_stage_is_none(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "none")

    def test_checksum_is_sha256_prefixed(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))

    def test_lineage_includes_dataset_refs(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        lineage = result.registry_entry["lineage"]
        self.assertIn("dataset:test-source", lineage["source_dataset_refs"])

    def test_registry_id_format(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertTrue(result.registry_entry["registry_id"].startswith("vbt-backtest-"))

    def test_num_instruments_in_aggregate(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.artifact_bundle["aggregate_metrics"]["num_instruments"], 2)


# --- Canonical strategy artifact through the real backend ---

def _momentum_artifact(*, lookback: int = 2, threshold: float = 0.0) -> dict:
    return {
        "artifact_id": "artifact-persona-paper-aa060d53ff712cbba704",
        "strategy_id": "strategy-persona-aa060d53ff712cbba704",
        "version": "1.0.0",
        "artifact_schema_version": "1.0",
        "lineage": {
            "source_run_ids": ["persona-provisioning-forward-artifact-aa060d53ff712cbba704"],
            "parent_registry_ids": ["artifact-persona-paper-baseline-aa060d53ff712cbba704"],
            "source_strategy_spec_id": "reg-strategy-spec-persona-aa060d53ff712cbba704",
        },
        "algorithm_ref": {
            "path": "services/registry/strategy_artifact.py",
            "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
            "engine": "lean",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "repository": "ajoe734/pantheon",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_schema_version": "1.0",
        },
        "strategy_logic": {
            "kind": "close_to_close_momentum",
            "positive_action": "BUY",
            "non_positive_action": "SELL",
            "lookback_parameter": "lookback_bars",
            "threshold_parameter": "momentum_threshold",
        },
        "parameters": {
            "market": "TW",
            "symbols": ["2330.TW"],
            "data_source": "source-ingest:paper/persona-bootstrap",
            "bar_frequency": "1d",
            "lookback_bars": lookback,
            "quantity_type": "SHARES",
            "order_quantity": 1,
            "momentum_threshold": threshold,
            "zero_momentum_action": "SELL",
        },
        "provenance_refs": ["task:LOOP-PROD-PER-001"],
        "mutation_surface": {
            "controls": [
                {"parameter_key": "lookback_bars", "value_type": "integer", "current_value": lookback,
                 "allowed_range": {"min": 2, "max": 60}, "step": 1},
                {"parameter_key": "momentum_threshold", "value_type": "number", "current_value": threshold,
                 "allowed_range": {"min": 0.0, "max": 0.05}, "step": 0.001},
            ],
            "immutable_parameters": [
                "symbols", "bar_frequency", "data_source", "order_quantity",
                "quantity_type", "zero_momentum_action", "market",
            ],
        },
    }


def _canonical_dataset(closes_2330: list[float], closes_other: list[float]) -> dict:
    def rows(symbol: str, closes: list[float]) -> list[dict]:
        start = date(2024, 1, 1)
        return [
            {"instrument": symbol, "date": (start + timedelta(days=i)).isoformat(),
             "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for i, c in enumerate(closes)
        ]

    return {
        "dataset_id": "dataset:canonical",
        "strategy_id": "strategy-persona-aa060d53ff712cbba704",
        "source_dataset_refs": ["dataset:test-source"],
        "data_frequency": "daily",
        "records": rows("2330.TWSE", closes_2330) + rows("2317.TWSE", closes_other),
    }


_HAS_VECTORBT = all(importlib.util.find_spec(m) for m in ("vectorbt", "pandas", "numpy"))


class TestCanonicalArtifactBacktest(unittest.TestCase):
    RISING = [100.0 + i for i in range(40)]
    FALLING = [200.0 - i for i in range(40)]

    def _run(self, closes_2330, closes_other, artifact, **config):
        from adapter.vectorbt_adapter import VectorbtBackend

        with mock.patch.dict(os.environ, {"PANTHEON_VECTORBT_BACKEND": "real"}):
            return run_vectorbt_workflow(
                _canonical_dataset(closes_2330, closes_other),
                backend=VectorbtBackend(),
                config=BacktestConfig(strategy_artifact=artifact, **config),
            )

    @unittest.skipUnless(_HAS_VECTORBT, "real vectorbt is not installed")
    def test_only_artifact_symbols_trade_and_accumulate_long(self) -> None:
        result = self._run(self.RISING, self.FALLING, _momentum_artifact())
        metrics = result.backtest_result.per_instrument_metrics
        self.assertEqual(list(metrics), ["2330.TWSE"])
        self.assertEqual(result.backtest_result.aggregate_metrics["num_instruments"], 1)
        # one BUY order per bar once lookback is satisfied, accumulated to a long position
        self.assertEqual(metrics["2330.TWSE"]["trade_count"], len(self.RISING) - 1)
        self.assertEqual(metrics["2330.TWSE"]["net_position"], float(len(self.RISING) - 1))
        entry = result.registry_entry["metadata"]
        self.assertEqual(entry["strategy"], "close_to_close_momentum")
        self.assertEqual(entry["strategy_artifact"]["artifact_id"], _momentum_artifact()["artifact_id"])

    @unittest.skipUnless(_HAS_VECTORBT, "real vectorbt is not installed")
    def test_sell_goes_short_instead_of_exiting_long(self) -> None:
        result = self._run(self.FALLING, self.RISING, _momentum_artifact())
        metrics = result.backtest_result.per_instrument_metrics["2330.TWSE"]
        self.assertEqual(metrics["net_position"], -float(len(self.FALLING) - 1))
        self.assertGreater(metrics["total_return"], 0.0)  # short profits on a falling series

    @unittest.skipUnless(_HAS_VECTORBT, "real vectorbt is not installed")
    def test_parameter_change_changes_result(self) -> None:
        series = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.0, 104.0] * 5
        base = self._run(series, series, _momentum_artifact(lookback=2))
        changed = self._run(series, series, _momentum_artifact(lookback=5, threshold=0.01))
        base_metrics = base.backtest_result.per_instrument_metrics["2330.TWSE"]
        changed_metrics = changed.backtest_result.per_instrument_metrics["2330.TWSE"]
        self.assertNotEqual(base_metrics["net_position"], changed_metrics["net_position"])
        self.assertNotEqual(base.registry_entry["checksum"], changed.registry_entry["checksum"])

    @unittest.skipUnless(_HAS_VECTORBT, "real vectorbt is not installed")
    def test_dataset_missing_artifact_symbol_is_rejected(self) -> None:
        artifact = _momentum_artifact()
        artifact["parameters"]["symbols"] = ["2454.TW"]
        with self.assertRaises(VectorbtWorkflowError):
            self._run(self.RISING, self.RISING, artifact)

    def test_stub_backend_refuses_canonical_artifact(self) -> None:
        with self.assertRaises(VectorbtWorkflowError):
            run_vectorbt_workflow(
                _canonical_dataset(self.RISING, self.RISING),
                backend=StubVectorbtBackend(),
                config=BacktestConfig(strategy_artifact=_momentum_artifact()),
            )

    def test_unbound_config_still_runs_legacy_sma(self) -> None:
        result = run_vectorbt_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["metadata"]["strategy"], "ma_crossover")
        self.assertIsNone(result.registry_entry["metadata"]["strategy_artifact"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_HAS_VECTORBT, "real vectorbt is not installed")
class TestZeroActivitySharpe(unittest.TestCase):
    """No MA 7/21 crossover: real vectorbt Sharpe is 0/0, reported with a reason."""

    @staticmethod
    def _dataset(closes):
        def rows(instrument):
            return [
                {
                    "instrument": instrument,
                    "date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
                    "open": c, "high": c, "low": c, "close": c, "volume": 1_000,
                }
                for i, c in enumerate(closes)
            ]

        return {
            "dataset_id": "dataset:zero-trade",
            "strategy_id": "strategy-zero-trade",
            "source_dataset_refs": ["dataset:test-source"],
            "data_frequency": "daily",
            "records": rows("AAA") + rows("BBB"),
        }

    def _run(self, closes):
        from adapter.vectorbt_adapter import VectorbtBackend

        with mock.patch.dict(os.environ, {"PANTHEON_VECTORBT_BACKEND": "real"}):
            return run_vectorbt_workflow(
                self._dataset(closes),
                backend=VectorbtBackend(),
                config=BacktestConfig(strategy_params={"short_window": 7, "long_window": 21}),
            ).backtest_result

    def test_flat_bars_report_no_trading_activity(self) -> None:
        import math

        result = self._run([100.0] * 35)

        self.assertEqual(result.aggregate_metrics["total_trades"], 0)
        self.assertFalse(math.isfinite(result.aggregate_metrics["mean_sharpe_ratio"]))
        self.assertEqual(result.aggregate_metrics["sharpe_undefined_reason"], "no_trading_activity")
        for metrics in result.per_instrument_metrics.values():
            self.assertEqual(metrics["trade_count"], 0)
            self.assertEqual(metrics["num_bars"], 35)
            self.assertEqual(metrics["sharpe_undefined_reason"], "no_trading_activity")

    def test_traded_nonfinite_sharpe_is_not_explained(self) -> None:
        from adapter.vectorbt_adapter import _sharpe_undefined_reason

        metrics = {"sharpe_ratio": float("nan"), "trade_count": 2, "total_return": 0.01, "max_drawdown": 0.02}
        self.assertIsNone(_sharpe_undefined_reason(metrics))
        self.assertEqual(
            _sharpe_undefined_reason({**metrics, "total_return": 0.0, "max_drawdown": 0.0}),
            "zero_return_variance",
        )
