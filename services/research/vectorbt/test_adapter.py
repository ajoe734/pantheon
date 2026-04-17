"""Unit tests for the governed vectorbt input adapter and stub backend."""
from __future__ import annotations

import copy
import sys
import unittest
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
    for i in range(n):
        price = price * (1.0 + (0.005 if i % 3 != 0 else -0.008))
        records.append(
            {
                "instrument": instrument,
                "date": f"2024-01-{i + 1:02d}",
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
        fwd_ret = forward_result.artifact_bundle["per_instrument_metrics"]["AAA"]["total_return"]
        rev_ret = reversed_result.artifact_bundle["per_instrument_metrics"]["AAA"]["total_return"]
        self.assertAlmostEqual(fwd_ret, rev_ret, places=6)


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


if __name__ == "__main__":
    unittest.main()
