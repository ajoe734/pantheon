"""Tests for ExperimentTask and ExperimentRun contracts."""

from __future__ import annotations

import unittest

from jsonschema import Draft7Validator

from .models import (
    ExperimentRun,
    ExperimentTask,
    ExperimentValidationError,
    load_experiment_run_schema,
    load_experiment_task_schema,
    validate_experiment_run_against_task,
    validate_experiment_run_payload,
    validate_experiment_task_payload,
)


def _task_payload(**overrides: object) -> dict:
    payload = {
        "task_id": "etask-20260516-001",
        "strategy_id": "strat-lightgbm-alpha-v1",
        "strategy_spec_version": "1.0.0",
        "tenant_id": "tenant-research-a",
        "strategy_spec_id": "reg-strategy-spec-lightgbm-1.0.0",
        "requested_by": "persona-researcher",
        "task_type": "backtest",
        "backend_id": "vectorbt",
        "backend_selection_policy_id": "research-backend-policy-v1",
        "dataset_version_id": "dataset-tw-equities-daily-2026q1-v3",
        "code_version": "git:abcdef123456",
        "feature_spec_version": "features-lightgbm-v1",
        "label_spec_version": "labels-forward-5d-v1",
        "cost_assumption_ref": "costs:tw-equities-v1",
        "risk_assumption_ref": "risk:research-defaults-v1",
        "priority": "normal",
        "status": "ready",
        "idempotency_key": "exp-task-key-001",
        "trace_id": "trace-exp-001-task",
        "created_at": "2026-05-16T08:00:00Z",
        "updated_at": "2026-05-16T08:01:00Z",
        "metadata": {
            "task_id": "EXP-001",
            "research_only": True,
        },
    }
    payload.update(overrides)
    return payload


def _run_payload(**overrides: object) -> dict:
    payload = {
        "run_id": "erun-20260516-001",
        "task_id": "etask-20260516-001",
        "strategy_id": "strat-lightgbm-alpha-v1",
        "strategy_spec_version": "1.0.0",
        "tenant_id": "tenant-research-a",
        "strategy_spec_id": "reg-strategy-spec-lightgbm-1.0.0",
        "backend_id": "vectorbt",
        "runtime_env": "research",
        "status": "completed",
        "started_at": "2026-05-16T08:05:00Z",
        "finished_at": "2026-05-16T08:07:00Z",
        "dataset_version_id": "dataset-tw-equities-daily-2026q1-v3",
        "code_version": "git:abcdef123456",
        "input_manifest_ref": "manifest://experiments/erun-20260516-001/input.json",
        "output_manifest_ref": "manifest://experiments/erun-20260516-001/output.json",
        "metric_bundle_id": "mbundle-20260516-001",
        "artifact_refs": ["candidate-artifact:alpha-v1"],
        "logs_ref": "logs://experiments/erun-20260516-001",
        "trace_id": "trace-exp-001-run",
        "created_at": "2026-05-16T08:04:00Z",
        "updated_at": "2026-05-16T08:07:00Z",
        "metadata": {
            "registry_write_performed": False,
            "execution_route": "none",
        },
    }
    payload.update(overrides)
    return payload


class TestExperimentContracts(unittest.TestCase):
    def test_schemas_are_valid_draft7(self) -> None:
        Draft7Validator.check_schema(load_experiment_task_schema())
        Draft7Validator.check_schema(load_experiment_run_schema())

    def test_experiment_task_round_trips_through_model_and_schema(self) -> None:
        payload = _task_payload()

        validate_experiment_task_payload(payload)
        task = ExperimentTask.from_dict(payload)

        self.assertEqual(task.strategy_id, "strat-lightgbm-alpha-v1")
        self.assertEqual(task.strategy_spec_version, "1.0.0")
        self.assertEqual(task.tenant_id, "tenant-research-a")
        self.assertEqual(task.strategy_spec_id, "reg-strategy-spec-lightgbm-1.0.0")
        self.assertEqual(task.dataset_version_id, "dataset-tw-equities-daily-2026q1-v3")
        self.assertEqual(task.to_dict(), payload)
        validate_experiment_task_payload(task.to_dict())

    def test_task_allows_backend_selection_before_backend_is_bound(self) -> None:
        payload = _task_payload(status="selecting_backend", backend_id=None)

        task = ExperimentTask.from_dict(payload)

        self.assertIsNone(task.backend_id)
        self.assertEqual(task.status, "selecting_backend")

    def test_task_requires_dataset_version_and_code_version(self) -> None:
        missing_dataset = _task_payload()
        missing_dataset.pop("dataset_version_id")
        with self.assertRaisesRegex(ExperimentValidationError, "dataset_version_id"):
            ExperimentTask.from_dict(missing_dataset)

        missing_code = _task_payload(code_version="")
        with self.assertRaisesRegex(ExperimentValidationError, "code_version"):
            ExperimentTask.from_dict(missing_code)

    def test_task_ready_state_requires_backend_id(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "backend_id"):
            ExperimentTask.from_dict(_task_payload(backend_id=None))

    def test_tenant_and_canonical_strategy_spec_id_must_be_bound_together(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "provided together"):
            ExperimentTask.from_dict(_task_payload(strategy_spec_id=None))
        with self.assertRaisesRegex(ExperimentValidationError, "provided together"):
            ExperimentRun.from_dict(_run_payload(tenant_id=None))

    def test_task_rejects_unknown_top_level_fields(self) -> None:
        payload = _task_payload()
        payload["unexpected"] = True

        with self.assertRaisesRegex(ExperimentValidationError, "Additional properties"):
            ExperimentTask.from_dict(payload)

    def test_experiment_run_round_trips_through_model_and_schema(self) -> None:
        payload = _run_payload()

        validate_experiment_run_payload(payload)
        run = ExperimentRun.from_dict(payload)

        self.assertEqual(run.task_id, "etask-20260516-001")
        self.assertEqual(run.strategy_spec_version, "1.0.0")
        self.assertEqual(run.tenant_id, "tenant-research-a")
        self.assertEqual(run.strategy_spec_id, "reg-strategy-spec-lightgbm-1.0.0")
        self.assertEqual(run.dataset_version_id, "dataset-tw-equities-daily-2026q1-v3")
        self.assertEqual(run.code_version, "git:abcdef123456")
        self.assertEqual(run.to_dict(), payload)
        validate_experiment_run_payload(run.to_dict())

    def test_completed_run_requires_output_manifest_ref(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "output_manifest_ref"):
            ExperimentRun.from_dict(_run_payload(output_manifest_ref=None))

    def test_failed_run_requires_failure_reason(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "failure_reason"):
            ExperimentRun.from_dict(
                _run_payload(
                    status="failed",
                    output_manifest_ref=None,
                    metric_bundle_id=None,
                    failure_reason=None,
                    artifact_refs=[],
                )
            )

    def test_run_rejects_duplicate_artifact_refs(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "artifact_refs"):
            ExperimentRun.from_dict(
                _run_payload(
                    artifact_refs=[
                        "candidate-artifact:alpha-v1",
                        "candidate-artifact:alpha-v1",
                    ]
                )
            )

    def test_run_preserves_parent_task_dataset_code_and_backend_pins(self) -> None:
        task = ExperimentTask.from_dict(_task_payload())
        run = ExperimentRun.from_dict(_run_payload())

        self.assertEqual(validate_experiment_run_against_task(run, task), [])

        mismatched_run = ExperimentRun.from_dict(
            _run_payload(dataset_version_id="dataset-other-v1"),
            validate_schema=True,
        )
        self.assertIn(
            "run.dataset_version_id must match task.dataset_version_id",
            validate_experiment_run_against_task(mismatched_run, task),
        )

        mismatched_version = ExperimentRun.from_dict(_run_payload(strategy_spec_version="2.0.0"))
        self.assertIn(
            "run.strategy_spec_version must match task.strategy_spec_version",
            validate_experiment_run_against_task(mismatched_version, task),
        )

        mismatched_tenant = ExperimentRun.from_dict(_run_payload(tenant_id="tenant-other"))
        self.assertIn(
            "run.tenant_id must match task.tenant_id",
            validate_experiment_run_against_task(mismatched_tenant, task),
        )

    def test_run_rejects_execution_runtime_envs(self) -> None:
        with self.assertRaisesRegex(ExperimentValidationError, "runtime_env"):
            ExperimentRun.from_dict(_run_payload(runtime_env="live"))


if __name__ == "__main__":
    unittest.main()
