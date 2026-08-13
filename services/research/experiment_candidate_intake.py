"""Research experiment candidate intake for imitation candidates.

Imitation candidates produced by policy-learning are intaken into the
research orchestrator domain as authoritative ExperimentTask and ExperimentRun
records. This intake preserves dataset, candidate, and artifact checksum
lineage without granting deployment authority or enabling runtime effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services.research.experiments.models import (
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentRuntimeEnv,
    ExperimentTask,
    ExperimentTaskPriority,
    ExperimentTaskStatus,
    ExperimentTaskType,
    validate_experiment_run_against_task,
)
from services.research.store import ResearchOrchestratorStore


class ExperimentCandidateIntakeError(ValueError):
    """Raised when an imitation candidate cannot be intaken into Research."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExperimentCandidateIntakeReceipt:
    task_id: str
    run_id: str
    experiment_task: ExperimentTask
    experiment_run: ExperimentRun
    candidate_id: str
    status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "experiment_task": self.experiment_task.to_dict(),
            "experiment_run": self.experiment_run.to_dict(),
            "candidate_id": self.candidate_id,
            "status": self.status,
            "created_at": self.created_at,
        }


def intake_imitation_candidate(
    candidate: Mapping[str, Any],
    *,
    store: Optional[ResearchOrchestratorStore] = None,
    timestamp: Optional[str] = None,
) -> ExperimentCandidateIntakeReceipt:
    """Intake one processed imitation candidate into Research.

    Enforces that:
    1. The candidate is in 'processed' status.
    2. Exactly one ExperimentTask is created for the candidate.
    3. Exactly one ExperimentRun is created keeping candidate/dataset/checksum lineage.
    4. Re-submitting the same candidate is idempotent and returns the existing task/run.
    """
    if not isinstance(candidate, Mapping):
        raise ExperimentCandidateIntakeError("candidate payload must be a JSON object")

    candidate_status = str(candidate.get("status") or "").lower()
    if candidate_status != "processed":
        raise ExperimentCandidateIntakeError(
            f"Only 'processed' candidates can be intaken to Research (got status: '{candidate_status}')"
        )

    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
    if not candidate_id:
        raise ExperimentCandidateIntakeError("candidate_id is required for intake")

    now = timestamp or _utc_now_iso()

    # Derived IDs
    task_id = f"rtask-exp-{candidate_id}"
    run_id = f"rrun-exp-{candidate_id}"

    # Check store for existing records for idempotency
    if store is not None:
        existing_task = store.get_task(task_id)
        existing_run = store.get_run(run_id)
        if existing_task and existing_run:
            task_payload = {k: v for k, v in existing_task.items() if k != "id"}
            run_payload = {k: v for k, v in existing_run.items() if k != "id"}
            exp_task = ExperimentTask.from_dict(task_payload)
            exp_run = ExperimentRun.from_dict(run_payload)
            return ExperimentCandidateIntakeReceipt(
                task_id=task_id,
                run_id=run_id,
                experiment_task=exp_task,
                experiment_run=exp_run,
                candidate_id=candidate_id,
                status="intaken",
                created_at=str(existing_task.get("created_at") or now),
            )

    # Extract lineage fields
    dataset_lineage = candidate.get("dataset_lineage") if isinstance(candidate.get("dataset_lineage"), Mapping) else {}
    dataset_version_id = str(
        dataset_lineage.get("version_id")
        or candidate.get("dataset_version_id")
        or (candidate.get("dataset_ref") if isinstance(candidate.get("dataset_ref"), Mapping) else {}).get("version_id")
        or "ds-v1"
    ).strip()
    tenant_id = str(
        dataset_lineage.get("tenant_id")
        or candidate.get("tenant_id")
        or (candidate.get("dataset_ref") if isinstance(candidate.get("dataset_ref"), Mapping) else {}).get("tenant_id")
        or "tenant-default"
    ).strip()

    artifact_checksum = str(
        candidate.get("artifact_checksum")
        or "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    ).strip()

    strategy_id = str(candidate.get("strategy_id") or f"strat-{candidate_id}").strip()
    strategy_spec_version = str(candidate.get("strategy_spec_version") or "1.0.0").strip()
    strategy_spec_id = str(candidate.get("strategy_spec_id") or f"spec-{strategy_id}").strip()
    code_version = str(candidate.get("code_version") or "git:dev").strip()
    trace_id = str(candidate.get("trace_id") or f"tr-handoff-{candidate_id}").strip()
    idempotency_key = f"handoff-{candidate_id}"

    # Build ExperimentTask
    exp_task = ExperimentTask(
        task_id=task_id,
        strategy_id=strategy_id,
        strategy_spec_version=strategy_spec_version,
        requested_by="policy-learning-imitation",
        task_type=ExperimentTaskType.RAPID_EVAL,
        backend_selection_policy_id="policy_learning_handoff",
        dataset_version_id=dataset_version_id,
        code_version=code_version,
        priority=ExperimentTaskPriority.NORMAL,
        status=ExperimentTaskStatus.READY,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        created_at=now,
        tenant_id=tenant_id,
        strategy_spec_id=strategy_spec_id,
        backend_id="imitation",
        metadata={
            "candidate_id": candidate_id,
            "policy_learning_handoff": True,
            "artifact_checksum": artifact_checksum,
            "dataset_lineage": dict(dataset_lineage),
            "evaluation_summary": dict(candidate.get("evaluation_summary") or {}),
            "training_evaluation": dict(candidate.get("training_evaluation") or {}),
        },
    )

    # Build ExperimentRun
    exp_run = ExperimentRun(
        run_id=run_id,
        task_id=task_id,
        strategy_id=strategy_id,
        strategy_spec_version=strategy_spec_version,
        backend_id="imitation",
        runtime_env=ExperimentRuntimeEnv.RESEARCH,
        status=ExperimentRunStatus.COMPLETED,
        started_at=now,
        finished_at=now,
        dataset_version_id=dataset_version_id,
        code_version=code_version,
        input_manifest_ref=f"candidate-manifest://{candidate_id}",
        output_manifest_ref=f"candidate-output://{candidate_id}",
        artifact_refs=(f"artifact-{candidate_id}",),
        trace_id=trace_id,
        created_at=now,
        tenant_id=tenant_id,
        strategy_spec_id=strategy_spec_id,
        metadata={
            "candidate_id": candidate_id,
            "policy_learning_handoff": True,
            "artifact_checksum": artifact_checksum,
            "dataset_lineage": dict(dataset_lineage),
            "metrics": dict(candidate.get("metrics") or {}),
            "evaluation_summary": dict(candidate.get("evaluation_summary") or {}),
        },
    )

    # Lineage verification
    lineage_errors = validate_experiment_run_against_task(exp_run, exp_task)
    if lineage_errors:
        raise ExperimentCandidateIntakeError(
            f"ExperimentRun lineage validation failed against ExperimentTask: {'; '.join(lineage_errors)}"
        )

    # Store persistence
    if store is not None:
        store.put_task(exp_task.to_dict())
        store.put_run(exp_run.to_dict())

    return ExperimentCandidateIntakeReceipt(
        task_id=task_id,
        run_id=run_id,
        experiment_task=exp_task,
        experiment_run=exp_run,
        candidate_id=candidate_id,
        status="intaken",
        created_at=now,
    )
