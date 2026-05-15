"""Unit tests for the governed Qlib data adapter and stub backend."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter.qlib_adapter import (
    ActivationReadyGate,
    GovernedQlibDataAdapter,
    QlibLightGBMBackend,
    QlibWorkflowError,
    StubLightGBMBackend,
    TrainingConfig,
    persist_qlib_run_artifacts,
    run_qlib_workflow,
    validate_activation_ready_dataset,
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

    def test_missing_upstream_backend_raises_explicit_install_error(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fail_qlib_import(name, *args, **kwargs):
            if name == "qlib" or name.startswith("qlib."):
                raise ImportError("qlib missing")
            return real_import(name, *args, **kwargs)

        prepared = GovernedQlibDataAdapter().prepare(MINIMAL_DATASET)
        with patch("builtins.__import__", side_effect=fail_qlib_import):
            with self.assertRaisesRegex(
                QlibWorkflowError,
                "Install services/research/qlib/requirements.txt",
            ):
                QlibLightGBMBackend().train(prepared, TrainingConfig())


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

    def test_candidate_packet_targets_candidate_without_registry_write(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        packet = result.candidate_packet
        self.assertEqual(packet["current_artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["deployment_stage"], "none")
        self.assertEqual(packet["registry_write_authority"], "registry_service_only")
        self.assertEqual(packet["candidate_registry_projection"]["artifact_state"], "candidate")
        self.assertEqual(packet["evaluator_handoff"]["minimum_artifact_state_for_scoring"], "approved")

    def test_model_eval_artifact_refs_target_review_only_outputs(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        refs = result.artifact_refs
        model_ref = refs["model_artifact_ref"]
        evaluation_ref = refs["evaluation_report_ref"]

        self.assertEqual(
            model_ref["artifact_ref"],
            f"{result.registry_entry['registry_id']}@{result.registry_entry['version']}",
        )
        self.assertEqual(model_ref["artifact_state"], "draft")
        self.assertEqual(model_ref["deployment_stage"], "none")
        self.assertEqual(evaluation_ref["artifact_type"], "evaluation_result")
        self.assertEqual(evaluation_ref["artifact_state"], "draft")
        self.assertEqual(evaluation_ref["deployment_stage"], "none")
        self.assertEqual(evaluation_ref["target_artifact_ref"], model_ref["artifact_ref"])
        self.assertEqual(evaluation_ref["target_artifact_checksum"], result.registry_entry["checksum"])
        self.assertEqual(refs["registry_write_authority"], "registry_service_only")
        self.assertTrue(refs["safety_assertions"]["no_registry_write"])
        self.assertTrue(refs["safety_assertions"]["scoring_only_not_direct_action"])
        self.assertEqual(
            {ref["artifact_name"] for ref in refs["refs"]},
            {
                "model_artifact",
                "evaluation_report",
                "artifact_bundle",
                "registry_entry",
                "candidate_packet",
            },
        )

    def test_activation_ready_data_floors_reject_small_dataset(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        with self.assertRaisesRegex(QlibWorkflowError, "Activation-ready Qlib data gates failed"):
            validate_activation_ready_dataset(result.prepared_dataset)

    def test_activation_ready_data_floors_accept_large_dataset(self) -> None:
        result = run_qlib_workflow(
            _activation_ready_dataset(),
            enforce_activation_ready=True,
            config=TrainingConfig(n_estimators=10),
        )
        self.assertEqual(result.prepared_dataset.num_instruments, 50)
        self.assertGreaterEqual(result.prepared_dataset.min_periods_per_instrument, 504)
        self.assertTrue(
            result.registry_entry["metadata"]["entry_criteria_satisfied"][
                "activation_ready_data_volume_met"
            ]
        )

    def test_persist_artifacts_writes_handoff_files(self) -> None:
        result = run_qlib_workflow(MINIMAL_DATASET)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = persist_qlib_run_artifacts(result, tmp)
            self.assertIn("candidate_packet", manifest["files"])
            self.assertIn("artifact_refs", manifest["files"])
            self.assertEqual(manifest["checksum"], result.registry_entry["checksum"])
            for path in manifest["files"].values():
                self.assertTrue(os.path.exists(path))
            artifact_refs = json.loads(Path(manifest["files"]["artifact_refs"]).read_text())
            self.assertEqual(
                artifact_refs["model_artifact_ref"]["registry_id"],
                result.registry_entry["registry_id"],
            )
            self.assertEqual(len(manifest["artifact_refs"]), 5)


class TestActivationReadyGate(unittest.TestCase):
    def test_cli_gate_requires_explicit_flag(self) -> None:
        with self.assertRaises(EnvironmentError):
            ActivationReadyGate.require_cli_flag(False)

    def test_env_gate_requires_explicit_enable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_QLIB_ACTIVATION_READY_ENABLED", None)
            with self.assertRaises(EnvironmentError):
                ActivationReadyGate.require_env()

    def test_env_gate_accepts_enabled_value(self) -> None:
        with patch.dict(os.environ, {"PANTHEON_QLIB_ACTIVATION_READY_ENABLED": "1"}, clear=False):
            ActivationReadyGate.require_env()

    def test_worker_entrypoint_requires_env_gate(self) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "PANTHEON_QLIB_ACTIVATION_READY_ENABLED"
        }
        proc = subprocess.run(
            [sys.executable, str(SERVICE_DIR / "worker.py")],
            cwd=str(SERVICE_DIR),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("disabled by default", proc.stderr)


def _activation_ready_dataset() -> dict:
    records = []
    start = date(2024, 1, 1)
    for inst_idx in range(50):
        instrument = f"SYM{inst_idx:03d}"
        base = 100.0 + inst_idx
        for period_idx in range(504):
            day = start + timedelta(days=period_idx * 2)
            close = base + period_idx * 0.05
            records.append(
                {
                    "instrument": instrument,
                    "date": day.isoformat(),
                    "open": close - 0.2,
                    "high": close + 0.4,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000_000 + inst_idx * 1000 + period_idx,
                }
            )
    return {
        "dataset_id": "dataset:activation-ready-daily",
        "strategy_id": "equity-cross-sectional-alpha",
        "source_dataset_refs": ["dataset:equity-top50-daily-activation-ready"],
        "source_strategy_spec_id": "strategy-spec:rs003-alpha-v1",
        "data_frequency": "daily",
        "records": records,
    }


if __name__ == "__main__":
    unittest.main()
