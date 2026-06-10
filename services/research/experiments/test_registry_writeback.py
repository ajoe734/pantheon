"""Tests for controlled ExperimentRun -> registry writeback."""

from __future__ import annotations

import unittest

from services.registry.models import ArtifactState, DeploymentStage
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore

from .models import ExperimentRun
from .registry_writeback import (
    ExperimentRegistryWritebackError,
    build_registry_entry_from_experiment_run,
    registry_entry_view_to_dict,
    write_experiment_run_artifact_to_registry,
)


def _run(**overrides: object) -> ExperimentRun:
    payload = {
        "run_id": "erun-20260516-005",
        "task_id": "etask-20260516-005",
        "strategy_id": "strat-exp005-alpha",
        "strategy_spec_version": "1.2.0",
        "backend_id": "vectorbt",
        "runtime_env": "research",
        "status": "completed",
        "started_at": "2026-05-16T09:00:00Z",
        "finished_at": "2026-05-16T09:03:00Z",
        "dataset_version_id": "dataset-exp005-v1",
        "code_version": "git:exp005",
        "input_manifest_ref": "manifest://exp005/input.json",
        "output_manifest_ref": "manifest://exp005/output.json",
        "metric_bundle_id": "metrics-exp005",
        "artifact_refs": ["artifact-exp005-model"],
        "trace_id": "trace-exp005",
        "created_at": "2026-05-16T08:59:00Z",
        "updated_at": "2026-05-16T09:03:00Z",
    }
    payload.update(overrides)
    return ExperimentRun.from_dict(payload)


def _artifact(**overrides: object) -> dict:
    payload = {
        "artifact_id": "artifact-exp005-model",
        "artifact_type": "model_artifact",
        "artifact_family": "experiment_candidate",
        "storage_ref": "object://experiments/erun-20260516-005/model.pkl",
        "checksum": "sha256:exp005",
        "registry_hints": {
            "artifact_type": "model_artifact",
            "artifact_state": "candidate",
            "version": "1.2.1",
            "source_strategy_spec_id": "reg-strategy-spec-exp005",
            "source_dataset_refs": ["dataset-exp005-v1"],
        },
        "metadata": {"framework": "vectorbt"},
    }
    payload.update(overrides)
    return payload


class TestExperimentRegistryWriteback(unittest.TestCase):
    def test_builds_candidate_registry_payload_with_run_and_strategy_lineage(self) -> None:
        payload = build_registry_entry_from_experiment_run(_run(), _artifact())

        self.assertEqual(payload.artifact_type.value, "model_artifact")
        self.assertEqual(payload.artifact_state, ArtifactState.CANDIDATE)
        self.assertEqual(payload.strategy_id, "strat-exp005-alpha")
        self.assertEqual(payload.version, "1.2.1")
        self.assertEqual(payload.producer_run_id, "erun-20260516-005")
        self.assertEqual(payload.lineage.source_run_ids, ["erun-20260516-005"])
        self.assertEqual(payload.lineage.source_strategy_spec_id, "reg-strategy-spec-exp005")
        self.assertEqual(payload.lineage.source_dataset_refs, ["dataset-exp005-v1"])
        self.assertEqual(payload.metadata["deployment_stage"], "none")

    def test_writes_registry_entry_without_deployment_projection(self) -> None:
        service = RegistryService(RegistryStore())

        view = write_experiment_run_artifact_to_registry(
            _run(),
            _artifact(),
            registry_service=service,
            registry_id="reg-exp005-model",
        )
        data = registry_entry_view_to_dict(view)

        self.assertEqual(data["entry"]["registry_id"], "reg-exp005-model")
        self.assertEqual(data["entry"]["artifact_state"], "candidate")
        self.assertEqual(data["entry"]["producer_run_id"], "erun-20260516-005")
        self.assertEqual(data["deployment_stage"], DeploymentStage.NONE.value)
        self.assertNotIn("deployment_summary", data["entry"])

    def test_rejects_non_completed_run(self) -> None:
        run = _run(
            status="running",
            finished_at=None,
            output_manifest_ref=None,
            metric_bundle_id=None,
            artifact_refs=[],
        )

        with self.assertRaisesRegex(ExperimentRegistryWritebackError, "completed"):
            build_registry_entry_from_experiment_run(run, _artifact())

    def test_rejects_approved_or_deployed_writeback(self) -> None:
        with self.assertRaisesRegex(ExperimentRegistryWritebackError, "draft or candidate"):
            build_registry_entry_from_experiment_run(
                _run(),
                _artifact(registry_hints={"artifact_type": "model_artifact", "artifact_state": "approved"}),
            )

        with self.assertRaisesRegex(ExperimentRegistryWritebackError, "deployment_stage=none"):
            build_registry_entry_from_experiment_run(
                _run(),
                _artifact(registry_hints={"artifact_type": "model_artifact", "deployment_stage": "paper"}),
            )

    def test_rejects_malformed_evaluation_summary_at_writeback_boundary(self) -> None:
        with self.assertRaisesRegex(ExperimentRegistryWritebackError, "evaluation_summary must be a mapping"):
            build_registry_entry_from_experiment_run(
                _run(),
                _artifact(evaluation_summary="invalid-not-a-dict"),
            )


if __name__ == "__main__":
    unittest.main()
