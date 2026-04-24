"""Unit tests for the governed Qlib data adapter and stub backend."""
from __future__ import annotations

import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.qlib_adapter import (
    GovernedQlibDataAdapter,
    QlibLightGBMBackend,
    QlibWorkflowError,
    StubLightGBMBackend,
    TrainingConfig,
    run_qlib_workflow,
)

MINIMAL_DATASET = {
    "dataset_id": "dataset:test-daily",
    "strategy_id": "test-strategy",
    "source_dataset_refs": ["dataset:test-source"],
    "data_frequency": "daily",
    "records": [
        {"instrument": "AAA", "date": "2024-01-01", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1000000},
        {"instrument": "AAA", "date": "2024-01-02", "open": 102.0, "high": 106.0, "low": 101.0, "close": 104.0, "volume": 1100000},
        {"instrument": "AAA", "date": "2024-01-03", "open": 104.0, "high": 107.0, "low": 103.0, "close": 103.0, "volume": 900000},
        {"instrument": "AAA", "date": "2024-01-04", "open": 103.0, "high": 108.0, "low": 102.0, "close": 106.0, "volume": 1050000},
        {"instrument": "AAA", "date": "2024-01-05", "open": 106.0, "high": 109.0, "low": 105.0, "close": 107.0, "volume": 1020000},
        {"instrument": "BBB", "date": "2024-01-01", "open": 50.0, "high": 52.0, "low": 49.0, "close": 51.0, "volume": 500000},
        {"instrument": "BBB", "date": "2024-01-02", "open": 51.0, "high": 53.0, "low": 50.0, "close": 52.5, "volume": 520000},
        {"instrument": "BBB", "date": "2024-01-03", "open": 52.5, "high": 54.0, "low": 52.0, "close": 52.0, "volume": 480000},
        {"instrument": "BBB", "date": "2024-01-04", "open": 52.0, "high": 55.0, "low": 51.5, "close": 54.0, "volume": 530000},
        {"instrument": "BBB", "date": "2024-01-05", "open": 54.0, "high": 56.0, "low": 53.5, "close": 55.0, "volume": 510000},
    ],
}


class TestGovernedQlibDataAdapter(unittest.TestCase):

    def test_happy_path_produces_expected_shapes(self) -> None:
        ds = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        self.assertEqual(ds.strategy_id, "test-strategy")
        self.assertEqual(ds.num_instruments, 2)
        self.assertGreater(ds.num_samples, 0)
        self.assertEqual(ds.num_features, 4)
        self.assertEqual(len(ds.feature_matrix), ds.num_samples)
        self.assertEqual(len(ds.labels), ds.num_samples)

    def test_missing_ohlcv_field_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["records"][0]["close"]
        with self.assertRaises(QlibWorkflowError):
            GovernedQlibDataAdapter().prepare(bad)

    def test_missing_dataset_refs_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        del bad["source_dataset_refs"]
        with self.assertRaises(QlibWorkflowError):
            GovernedQlibDataAdapter().prepare(bad)

    def test_single_instrument_raises(self) -> None:
        bad = copy.deepcopy(MINIMAL_DATASET)
        bad["records"] = [r for r in bad["records"] if r["instrument"] == "AAA"]
        with self.assertRaises(QlibWorkflowError):
            GovernedQlibDataAdapter().prepare(bad)

    def test_lineage_preserved_in_output(self) -> None:
        ds = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        self.assertIn("dataset:test-source", ds.source_dataset_refs)


class TestStubLightGBMBackend(unittest.TestCase):

    def test_produces_metrics_and_model_payload(self) -> None:
        ds = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        result = StubLightGBMBackend().train(ds, TrainingConfig())
        self.assertIn("ic", result.metrics)
        self.assertIn("num_samples", result.metrics)
        self.assertIn("predictor", result.model_payload)
        self.assertEqual(result.backend, "stub_lgbm")

    def test_run_id_is_unique(self) -> None:
        ds = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        r1 = StubLightGBMBackend().train(ds, TrainingConfig())
        r2 = StubLightGBMBackend().train(ds, TrainingConfig())
        self.assertNotEqual(r1.run_id, r2.run_id)


class TestQlibLightGBMBackend(unittest.TestCase):

    def test_wraps_segments_into_qlib_dataset_contract(self) -> None:
        class FakeArray:
            def __init__(self, values):
                if isinstance(values, FakeArray):
                    values = values._values
                self._values = [tuple(v) if isinstance(v, (list, tuple)) else v for v in values]
                self.ndim = 2 if self._values and isinstance(self._values[0], tuple) else 1
                self.shape = (
                    (len(self._values), len(self._values[0]))
                    if self.ndim == 2
                    else (len(self._values),)
                )

            def __getitem__(self, item):
                if isinstance(item, slice):
                    return FakeArray(self._values[item])
                return self._values[item]

            def __iter__(self):
                return iter(self._values)

            def __len__(self):
                return len(self._values)

        class FakeLGBModel:
            fit_dataset = None
            predict_segments = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def fit(self, dataset):
                type(self).fit_dataset = dataset
                train = dataset.prepare("train", col_set=["feature", "label"], data_key="ignored")
                valid = dataset.prepare("valid", col_set=["feature", "label"], data_key="ignored")
                assert dataset.segments == {"train": None, "valid": None}
                assert train["label"].values.ndim == 2
                assert train["label"].values.shape[1] == 1
                assert len(train["feature"].values) == 4
                assert len(valid["feature"].values) == 2

            def predict(self, dataset, segment="valid"):
                type(self).predict_segments.append(segment)
                feature_view = dataset.prepare(segment, col_set="feature", data_key="ignored")
                return [0.05 for _ in range(len(feature_view.values))]

        fake_numpy = types.SimpleNamespace(
            float32="float32",
            asarray=lambda values, dtype=None: FakeArray(values),
        )
        fake_qlib = types.ModuleType("qlib")
        fake_contrib = types.ModuleType("qlib.contrib")
        fake_model = types.ModuleType("qlib.contrib.model")
        fake_gbdt = types.ModuleType("qlib.contrib.model.gbdt")
        fake_gbdt.LGBModel = FakeLGBModel
        fake_model.gbdt = fake_gbdt
        fake_contrib.model = fake_model
        fake_qlib.contrib = fake_contrib

        prepared = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        with patch.dict(
            sys.modules,
            {
                "numpy": fake_numpy,
                "qlib": fake_qlib,
                "qlib.contrib": fake_contrib,
                "qlib.contrib.model": fake_model,
                "qlib.contrib.model.gbdt": fake_gbdt,
            },
            clear=False,
        ):
            result = QlibLightGBMBackend().train(prepared, TrainingConfig())

        self.assertEqual(result.backend, "qlib_lgbm")
        self.assertIn("val_ic", result.metrics)
        self.assertIn("val_mse", result.metrics)
        self.assertEqual(FakeLGBModel.predict_segments, ["valid"])
        self.assertIsNotNone(FakeLGBModel.fit_dataset)


class TestRunQlibWorkflow(unittest.TestCase):

    def test_registry_entry_starts_as_draft_with_none_deployment(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        self.assertEqual(result.registry_entry["artifact_state"], "draft")
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "none")

    def test_registry_id_format(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        self.assertTrue(result.registry_entry["registry_id"].startswith("qlib-alpha-"))

    def test_checksum_is_sha256_prefixed(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))

    def test_lineage_includes_dataset_refs(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        lineage = result.registry_entry["lineage"]
        self.assertIn("dataset:test-source", lineage["source_dataset_refs"])

    def test_governance_no_direct_live_influence(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        self.assertFalse(result.artifact_bundle["governance"]["direct_live_influence"])

    def test_lean_consumption_is_scoring_only(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        gov = result.artifact_bundle["governance"]
        self.assertEqual(gov["lean_consumption"], "scoring_only_not_direct_action")


if __name__ == "__main__":
    unittest.main()
