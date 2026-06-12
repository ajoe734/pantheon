"""Bridge promoted StrategySpecSeed records into research replication tasks.

The bridge is intentionally one-way:
StrategySpecSeed -> StrategySpec candidate -> ExperimentTask queue record.
It never writes registry admission state, creates approved artifacts, or opens
runtime/execution routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from services.research.experiments.models import ExperimentTask
from services.research.store import (
    ResearchOrchestratorStore,
    build_research_orchestrator_store,
)
from services.research.strategy_spec.conversion import (
    StrategySpecConversionResult,
    StrategySpecConversionService,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


class StrategySeedReplicationBridgeError(ValueError):
    """Raised when a seed cannot be submitted for replication."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SeedReplicationSubmission:
    """Result of submitting a seed-backed StrategySpec candidate to research."""

    seed_id: str
    replication_ref: str
    experiment_task_id: str
    strategy_id: str
    strategy_spec_version: str
    experiment_task: Mapping[str, Any]
    research_task: Mapping[str, Any]
    created_at: str
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "replication_ref": self.replication_ref,
            "experiment_task_id": self.experiment_task_id,
            "strategy_id": self.strategy_id,
            "strategy_spec_version": self.strategy_spec_version,
            "experiment_task": dict(self.experiment_task),
            "research_task": dict(self.research_task),
            "created_at": self.created_at,
            "idempotent_replay": self.idempotent_replay,
            "registry_write_performed": False,
            "execution_route": "none",
            "approved_artifact_created": False,
            "deployment_plan_created": False,
            "runtime_binding_created": False,
        }


class StrategySeedReplicationBridge:
    """Submit promoted StrategySpecSeed records to the research task queue."""

    def __init__(
        self,
        *,
        seed_store: StrategySpecSeedStore | None = None,
        research_store: ResearchOrchestratorStore | None = None,
        conversion_service: StrategySpecConversionService | None = None,
    ) -> None:
        self.seed_store = seed_store or StrategySpecSeedStore()
        self.research_store = research_store or build_research_orchestrator_store(_research_data_dir())
        self.conversion_service = conversion_service or StrategySpecConversionService()

    def submit_seed_to_replication(
        self,
        seed_id: str,
        *,
        requested_by: str = "operator",
        idempotency_key: str | None = None,
        created_at: datetime | str | None = None,
        strategy_spec_version: str = "1.0.0",
    ) -> SeedReplicationSubmission:
        """Convert a promoted seed and enqueue a research ExperimentTask."""

        seed = self._load_promoted_seed(seed_id)
        lineage_submission = _submission_from_lineage(seed, self.research_store)
        if lineage_submission is not None:
            return lineage_submission

        actor = _require_text(requested_by, "requested_by")
        timestamp = _iso(created_at or _utc_now())
        resolved_key = _require_text(
            idempotency_key or f"strategy-seed-replication:{seed.seed_id}",
            "idempotency_key",
        )
        conversion = self.conversion_service.convert_seed(
            seed,
            version=strategy_spec_version,
            created_by=actor,
            created_at=timestamp,
            metadata={
                "replication_submission_requested": True,
                "replication_submission_requested_by": actor,
            },
        )
        experiment_task = _build_experiment_task(
            seed=seed,
            conversion=conversion,
            requested_by=actor,
            idempotency_key=resolved_key,
            created_at=timestamp,
            strategy_spec_version=strategy_spec_version,
        )
        replication_ref = _replication_ref(experiment_task.task_id)

        existing = _find_existing_research_task(
            self.research_store,
            seed_id=seed.seed_id,
            experiment_task_id=experiment_task.task_id,
        )
        if existing is not None:
            self.seed_store.record_replication_submission(
                seed.seed_id,
                replication_ref=str(existing.get("replication_ref") or replication_ref),
                experiment_task_id=str(existing.get("experiment_task_id") or experiment_task.task_id),
                strategy_id=experiment_task.strategy_id,
                strategy_spec_version=experiment_task.strategy_spec_version,
                submitted_by=actor,
                submitted_at=timestamp,
                idempotency_key=resolved_key,
                research_task_ref=str(existing.get("task_id") or experiment_task.task_id),
            )
            return _submission_from_record(existing, idempotent_replay=True)

        research_task = _research_task_record(
            seed=seed,
            conversion=conversion,
            experiment_task=experiment_task,
            replication_ref=replication_ref,
            requested_by=actor,
            created_at=timestamp,
            idempotency_key=resolved_key,
        )
        stored_task = self.research_store.put_task(research_task)
        self.research_store.append_event(
            {
                "event_id": f"revt-seed-replication-{_short_hash([experiment_task.task_id, timestamp])}",
                "event_type": "experiment_task_queued",
                "summary": f"StrategySpecSeed {seed.seed_id} submitted to replication.",
                "actor": actor,
                "run_id": None,
                "task_id": experiment_task.task_id,
                "replication_ref": replication_ref,
                "emitted_at": timestamp,
                "sequence_number": 1,
            }
        )
        self.seed_store.record_replication_submission(
            seed.seed_id,
            replication_ref=replication_ref,
            experiment_task_id=experiment_task.task_id,
            strategy_id=experiment_task.strategy_id,
            strategy_spec_version=experiment_task.strategy_spec_version,
            submitted_by=actor,
            submitted_at=timestamp,
            idempotency_key=resolved_key,
            research_task_ref=str(stored_task.get("task_id") or experiment_task.task_id),
        )
        return _submission_from_record(stored_task, idempotent_replay=False)

    def _load_promoted_seed(self, seed_id: str) -> StrategySpecSeed:
        normalized = _require_text(seed_id, "seed_id")
        seed = self.seed_store.get(normalized)
        if seed is None:
            raise StrategySeedReplicationBridgeError(
                "seed_not_found",
                f"StrategySpecSeed not found: {normalized}",
            )
        status = seed.status.value if isinstance(seed.status, StrategySpecSeedStatus) else str(seed.status)
        if status != StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value:
            raise StrategySeedReplicationBridgeError(
                "invalid_seed_status",
                (
                    f"StrategySpecSeed {normalized} must be promoted_to_strategy_spec "
                    f"before replication submission; current status is {status!r}"
                ),
            )
        return seed


def submit_seed_to_replication(
    seed_id: str,
    **kwargs: Any,
) -> SeedReplicationSubmission:
    """Convenience wrapper for one-shot seed replication submission."""

    return StrategySeedReplicationBridge().submit_seed_to_replication(seed_id, **kwargs)


def _build_experiment_task(
    *,
    seed: StrategySpecSeed,
    conversion: StrategySpecConversionResult,
    requested_by: str,
    idempotency_key: str,
    created_at: str,
    strategy_spec_version: str,
) -> ExperimentTask:
    strategy_spec = conversion.strategy_spec
    strategy_payload = strategy_spec.to_dict()
    strategy_metadata = dict(strategy_payload.get("metadata") or {})
    dataset_version_id = _dataset_version_id(seed, strategy_payload, strategy_metadata)
    code_version = _code_version(conversion, strategy_metadata)
    backend_id = _backend_id(seed, strategy_metadata)
    task_id = f"exp-seed-{_short_hash([seed.seed_id, strategy_spec.strategy_id])}"
    replication_ref = _replication_ref(task_id)
    return ExperimentTask(
        task_id=task_id,
        strategy_id=strategy_spec.strategy_id,
        strategy_spec_version=_require_text(strategy_spec_version, "strategy_spec_version"),
        requested_by=requested_by,
        task_type="backtest",
        backend_id=backend_id,
        backend_selection_policy_id="strategy-seed-replication-policy-v1",
        dataset_version_id=dataset_version_id,
        code_version=code_version,
        feature_spec_version=_optional_text(strategy_metadata.get("feature_spec_version")),
        label_spec_version=_optional_text(strategy_metadata.get("label_spec_version")),
        cost_assumption_ref=_optional_text(strategy_metadata.get("cost_assumption_ref")),
        risk_assumption_ref=_optional_text(strategy_metadata.get("risk_assumption_ref")),
        priority="normal",
        status="queued",
        idempotency_key=idempotency_key,
        trace_id=_trace_id(seed, task_id),
        created_at=created_at,
        metadata={
            "task_family": "strategy_seed_replication",
            "source_seed_id": seed.seed_id,
            "evidence_bundle_id": seed.evidence_bundle_id,
            "source_strategy_spec_id": strategy_spec.strategy_id,
            "strategy_spec_candidate": strategy_payload,
            "registry_payload": dict(conversion.registry_payload),
            "replication_ref": replication_ref,
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
            "approved_artifact_created": False,
            "deployment_plan_created": False,
            "runtime_binding_created": False,
        },
    )


def _research_task_record(
    *,
    seed: StrategySpecSeed,
    conversion: StrategySpecConversionResult,
    experiment_task: ExperimentTask,
    replication_ref: str,
    requested_by: str,
    created_at: str,
    idempotency_key: str,
) -> dict[str, Any]:
    strategy_spec = conversion.strategy_spec
    return {
        "id": experiment_task.task_id,
        "task_id": experiment_task.task_id,
        "experiment_task_id": experiment_task.task_id,
        "replication_ref": replication_ref,
        "queue_kind": "experiment_task",
        "title": f"Replicate StrategySpecSeed {seed.seed_id}",
        "objective": (
            f"Replicate research-only StrategySpec candidate {strategy_spec.strategy_id} "
            f"from StrategySpecSeed {seed.seed_id}."
        ),
        "status": "queued",
        "source_refs": [
            {"type": "strategy_spec_seed", "id": seed.seed_id},
            {"type": "evidence_bundle", "id": seed.evidence_bundle_id},
            {"type": "strategy_spec_candidate", "id": strategy_spec.strategy_id},
        ],
        "constraints": {
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
            "no_approved_artifact": True,
            "no_deployment_plan": True,
            "no_runtime_binding": True,
        },
        "created_by": requested_by,
        "created_at": created_at,
        "updated_at": created_at,
        "idempotency_key": idempotency_key,
        "experiment_task": experiment_task.to_dict(),
        "metadata": {
            "source_seed_id": seed.seed_id,
            "strategy_id": strategy_spec.strategy_id,
            "strategy_spec_version": experiment_task.strategy_spec_version,
            "replication_ref": replication_ref,
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
            "approved_artifact_created": False,
            "deployment_plan_created": False,
            "runtime_binding_created": False,
        },
    }


def _submission_from_lineage(
    seed: StrategySpecSeed,
    research_store: ResearchOrchestratorStore,
) -> SeedReplicationSubmission | None:
    lineage = dict(seed.lineage)
    replication_ref = str(lineage.get("replication_ref") or "").strip()
    experiment_task_id = str(lineage.get("experiment_task_id") or "").strip()
    if not replication_ref or not experiment_task_id:
        return None
    record = research_store.get_task(experiment_task_id) or {}
    experiment_task = dict(record.get("experiment_task") or {})
    if not experiment_task:
        experiment_task = {
            "task_id": experiment_task_id,
            "strategy_id": str(lineage.get("strategy_id") or ""),
            "strategy_spec_version": str(lineage.get("strategy_spec_version") or ""),
            "metadata": {
                "source_seed_id": seed.seed_id,
                "replication_ref": replication_ref,
                "registry_write_performed": False,
                "execution_route": "none",
            },
        }
    return SeedReplicationSubmission(
        seed_id=seed.seed_id,
        replication_ref=replication_ref,
        experiment_task_id=experiment_task_id,
        strategy_id=str(lineage.get("strategy_id") or experiment_task.get("strategy_id") or ""),
        strategy_spec_version=str(
            lineage.get("strategy_spec_version")
            or experiment_task.get("strategy_spec_version")
            or ""
        ),
        experiment_task=experiment_task,
        research_task=record,
        created_at=str(lineage.get("submitted_at") or ""),
        idempotent_replay=True,
    )


def _submission_from_record(
    record: Mapping[str, Any],
    *,
    idempotent_replay: bool,
) -> SeedReplicationSubmission:
    experiment_task = dict(record.get("experiment_task") or {})
    metadata = dict(record.get("metadata") or {})
    experiment_metadata = (
        experiment_task.get("metadata")
        if isinstance(experiment_task.get("metadata"), Mapping)
        else {}
    )
    return SeedReplicationSubmission(
        seed_id=str(metadata.get("source_seed_id") or experiment_metadata.get("source_seed_id") or ""),
        replication_ref=str(record.get("replication_ref") or metadata.get("replication_ref") or ""),
        experiment_task_id=str(
            record.get("experiment_task_id")
            or experiment_task.get("task_id")
            or record.get("task_id")
            or ""
        ),
        strategy_id=str(metadata.get("strategy_id") or experiment_task.get("strategy_id") or ""),
        strategy_spec_version=str(
            metadata.get("strategy_spec_version")
            or experiment_task.get("strategy_spec_version")
            or ""
        ),
        experiment_task=experiment_task,
        research_task=dict(record),
        created_at=str(record.get("created_at") or ""),
        idempotent_replay=idempotent_replay,
    )


def _find_existing_research_task(
    research_store: ResearchOrchestratorStore,
    *,
    seed_id: str,
    experiment_task_id: str,
) -> Mapping[str, Any] | None:
    direct = research_store.get_task(experiment_task_id)
    if direct:
        return direct
    for task in research_store.list_tasks():
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        experiment_task = task.get("experiment_task") if isinstance(task.get("experiment_task"), dict) else {}
        experiment_metadata = (
            experiment_task.get("metadata")
            if isinstance(experiment_task.get("metadata"), dict)
            else {}
        )
        if str(metadata.get("source_seed_id") or experiment_metadata.get("source_seed_id") or "") == seed_id:
            return task
    return None


def _dataset_version_id(
    seed: StrategySpecSeed,
    strategy_payload: Mapping[str, Any],
    strategy_metadata: Mapping[str, Any],
) -> str:
    direct = _optional_text(
        strategy_metadata.get("dataset_version_id")
        or seed.metadata.get("dataset_version_id")
        or seed.metadata.get("dataset_ref")
    )
    if direct:
        return direct
    for dependency in strategy_payload.get("data_dependencies") or []:
        if not isinstance(dependency, Mapping):
            continue
        if str(dependency.get("kind") or "").strip().lower() == "dataset":
            ref = _optional_text(dependency.get("ref"))
            if ref:
                return ref
    return f"evidence-bundle:{seed.evidence_bundle_id}"


def _code_version(
    conversion: StrategySpecConversionResult,
    strategy_metadata: Mapping[str, Any],
) -> str:
    direct = _optional_text(strategy_metadata.get("code_version"))
    if direct:
        return direct
    checksum = str(conversion.registry_payload.get("checksum") or "").split(":")[-1]
    if checksum:
        return f"strategy-seed-conversion:{checksum[:12]}"
    return "strategy-seed-conversion:v1"


def _backend_id(seed: StrategySpecSeed, strategy_metadata: Mapping[str, Any]) -> str | None:
    backend = _optional_text(
        strategy_metadata.get("backend_id")
        or strategy_metadata.get("backend_hint")
        or seed.backend_hint
    )
    if backend is None or backend.lower() in {"none", "research"}:
        return None
    return backend


def _trace_id(seed: StrategySpecSeed, task_id: str) -> str:
    for ref in seed.trace_refs:
        text = _optional_text(ref)
        if text:
            return text
    return f"trace-{task_id}"


def _replication_ref(experiment_task_id: str) -> str:
    return f"research-orchestrator://experiment-tasks/{experiment_task_id}"


def _research_data_dir() -> str | Path:
    return os.environ.get("RESEARCH_ORCHESTRATOR_DATA_DIR", "/tmp/pantheon/research-orchestrator")


def _short_hash(parts: list[str]) -> str:
    payload = "\n".join(str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategySeedReplicationBridgeError("invalid_request", f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "SeedReplicationSubmission",
    "StrategySeedReplicationBridge",
    "StrategySeedReplicationBridgeError",
    "submit_seed_to_replication",
]
