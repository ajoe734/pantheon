"""Tests for parallel ExperimentTask backend dispatch."""

from __future__ import annotations

import unittest

from services.research.experiment_orchestrator.parallel_dispatch import (
    ParallelDispatchError,
    run_parallel,
)
from services.research.experiments.models import ExperimentRun, ExperimentTask


def _task_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": "etask-exp-v2-001",
        "strategy_id": "strat-cross-sectional-alpha-v1",
        "strategy_spec_version": "1.2.3",
        "requested_by": "persona-researcher",
        "task_type": "factor_eval",
        "backend_id": None,
        "backend_selection_policy_id": "research-parallel-policy-v1",
        "dataset_version_id": "dataset-tw-equities-daily-2026q1-v3",
        "code_version": "git:expv2001",
        "priority": "normal",
        "status": "selecting_backend",
        "idempotency_key": "exp-v2-001-key",
        "trace_id": "trace-exp-v2-001",
        "created_at": "2026-05-17T11:00:00Z",
        "metadata": {"research_only": True},
    }
    payload.update(overrides)
    return payload


def _experiment_task(**overrides: object) -> ExperimentTask:
    return ExperimentTask.from_dict(_task_payload(**overrides))


def _run_for_backend(
    task: ExperimentTask,
    backend_id: str,
    *,
    sharpe: float,
    ic: float,
) -> ExperimentRun:
    return ExperimentRun(
        run_id=f"erun-{backend_id}",
        task_id=task.task_id,
        strategy_id=task.strategy_id,
        strategy_spec_version=task.strategy_spec_version,
        backend_id=backend_id,
        runtime_env="research",
        status="completed",
        started_at="2026-05-17T11:01:00Z",
        finished_at="2026-05-17T11:02:00Z",
        dataset_version_id=task.dataset_version_id,
        code_version=task.code_version,
        input_manifest_ref=f"inline://tests/{backend_id}/input",
        output_manifest_ref=f"inline://tests/{backend_id}/output",
        metric_bundle_id=f"metric-bundle:{backend_id}",
        artifact_refs=[f"artifact://tests/{backend_id}"],
        logs_ref=f"inline://tests/{backend_id}/log",
        trace_id=f"{task.trace_id}:{backend_id}",
        created_at="2026-05-17T11:01:00Z",
        updated_at="2026-05-17T11:02:00Z",
        metadata={
            "metrics": {
                "sharpe": sharpe,
                "ic": ic,
            }
        },
    )


class MockBackend:
    def __init__(self, *, sharpe: float, ic: float) -> None:
        self.sharpe = sharpe
        self.ic = ic

    def __call__(self, task: ExperimentTask, backend_id: str) -> ExperimentRun:
        return _run_for_backend(task, backend_id, sharpe=self.sharpe, ic=self.ic)


class FailingBackend:
    def __call__(self, _task: ExperimentTask, _backend_id: str) -> ExperimentRun:
        raise RuntimeError("backend adapter unavailable")


class TestParallelDispatch(unittest.TestCase):
    def test_dispatches_one_task_to_three_mocked_backends_and_emits_comparison(self) -> None:
        task = _experiment_task()

        result = run_parallel(
            task,
            ["vectorbt", "qlib", "statsmodels"],
            backend_registry={
                "vectorbt": MockBackend(sharpe=1.1, ic=0.20),
                "qlib": MockBackend(sharpe=0.9, ic=0.15),
                "statsmodels": MockBackend(sharpe=0.4, ic=0.05),
            },
        )

        self.assertEqual([run.backend_id for run in result.runs], ["vectorbt", "qlib", "statsmodels"])
        self.assertEqual([run.status for run in result.runs], ["completed", "completed", "completed"])
        self.assertEqual(result.failures, {})
        self.assertEqual(result.comparison_summary["sharpe_by_backend"]["vectorbt"], 1.1)
        self.assertEqual(result.comparison_summary["ic_by_backend"]["qlib"], 0.15)
        self.assertEqual(result.comparison_summary["agreement_score"], 1.0)
        self.assertEqual(result.comparison_summary["compared_backend_count"], 3)

    def test_backend_failure_returns_failed_run_without_blocking_other_backends(self) -> None:
        task = _experiment_task()

        result = run_parallel(
            task,
            ["vectorbt", "qlib", "statsmodels"],
            backend_registry={
                "vectorbt": MockBackend(sharpe=1.1, ic=0.20),
                "qlib": FailingBackend(),
                "statsmodels": MockBackend(sharpe=0.4, ic=0.05),
            },
            created_at="2026-05-17T11:03:00Z",
        )

        self.assertEqual(len(result.runs), 3)
        self.assertEqual(result.runs[0].status, "completed")
        self.assertEqual(result.runs[1].backend_id, "qlib")
        self.assertEqual(result.runs[1].status, "failed")
        self.assertIn("backend adapter unavailable", result.runs[1].failure_reason or "")
        self.assertEqual(result.runs[2].status, "completed")
        self.assertEqual(result.failures, {"qlib": "backend adapter unavailable"})
        self.assertEqual(result.comparison_summary["failed_backends"], ["qlib"])
        self.assertEqual(result.comparison_summary["successful_backends"], ["vectorbt", "statsmodels"])
        self.assertEqual(result.comparison_summary["sharpe_by_backend"]["qlib"], None)
        self.assertEqual(result.comparison_summary["agreement_score"], 1.0)

    def test_duplicate_backend_ids_are_rejected_before_dispatch(self) -> None:
        with self.assertRaisesRegex(ParallelDispatchError, "unique"):
            run_parallel(
                _experiment_task(),
                ["vectorbt", "vectorbt"],
                backend_registry={"vectorbt": MockBackend(sharpe=1.0, ic=0.1)},
            )


if __name__ == "__main__":
    unittest.main()

