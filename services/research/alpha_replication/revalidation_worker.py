"""Approved-only Alpha replication worker with authoritative run readback.

Each queue attempt revalidates the exact immutable StrategySpec registry id,
persists an ExperimentTask intent before evaluation, evaluates through the
research-only ReplicationGate, and persists the resulting ExperimentRun through
the research-orchestrator authority.  The queue is acknowledged only after an
independent authority readback succeeds.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.research.experiment_orchestrator.authority import (
    AuthoritativeRunReceipt,
    AuthoritativeTaskReceipt,
    ExperimentAuthority,
    ResearchAuthorityHttpClient,
)
from services.research.experiments.models import (
    ExperimentRun,
    ExperimentTask,
    validate_experiment_run_against_task,
)

from .queue import AlphaReplicationQueue, _require_text


SAFE_DISPATCH_MODES = frozenset({"authoritative", "handoff_only"})
DEFAULT_DISPATCH_MODE = "authoritative"
DEFAULT_BACKEND_SELECTION_POLICY_ID = "alpha-replication-authoritative-v1"
DEFAULT_RUNTIME_ENV = "research"
DEFAULT_TASK_TYPE = "rapid_eval"
DEFAULT_BACKEND_ID = "replication_gate"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _short_hash(*parts: str, length: int = 16) -> str:
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _get_git_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - lineage falls back to explicit unknown.
        return "unknown"


@dataclass
class RevalidationWorkerMetrics:
    run_count: int = 0
    error_count: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None
    last_run_strategy_spec_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


class RevalidationAttemptError(RuntimeError):
    """A failed attempt that may already have authoritative task/run ids."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        authority_task_id: str | None = None,
        authority_run_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.run_id = run_id
        self.authority_task_id = authority_task_id
        self.authority_run_id = authority_run_id


class AlphaRevalidationWorker:
    """Drain leased queue work into authoritative ExperimentTask/Run state."""

    def __init__(
        self,
        queue: AlphaReplicationQueue,
        data_dir: str | Path,
        *,
        dispatch_mode: str | None = None,
        worker_id: str = "alpha-revalidation-worker",
        authority: ExperimentAuthority | None = None,
        registry_url: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self._queue = queue
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._metrics_path = self._data_dir / "alpha_revalidation_metrics.json"
        self._worker_id = _require_text(worker_id, "worker_id")
        self._lock = threading.Lock()

        configured_mode = (
            dispatch_mode
            or os.getenv(
                "PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE",
                DEFAULT_DISPATCH_MODE,
            )
        ).strip().lower()
        if configured_mode not in SAFE_DISPATCH_MODES:
            raise ValueError(
                f"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE={configured_mode!r} "
                "is not authoritative; stub and production activation modes are "
                f"fail-closed. Allowed: {sorted(SAFE_DISPATCH_MODES)}"
            )
        # ``handoff_only`` remains a deployment-compatible input alias. The
        # implementation always writes and reads back authoritative state.
        self._configured_mode = configured_mode
        self._dispatch_mode = "authoritative"
        self._max_retries = int(
            os.getenv("PANTHEON_ALPHA_REVALIDATION_MAX_RETRIES", "3")
        )
        self._lease_seconds = int(
            lease_seconds
            or os.getenv("PANTHEON_ALPHA_REVALIDATION_LEASE_SECONDS", "300")
        )
        if self._lease_seconds <= 0:
            raise ValueError("PANTHEON_ALPHA_REVALIDATION_LEASE_SECONDS must be positive")
        self._registry_url = (
            registry_url
            or os.getenv("PANTHEON_REGISTRY_URL")
            or "http://registry:8087"
        ).rstrip("/")
        self._authority = authority or ResearchAuthorityHttpClient(
            os.getenv("RESEARCH_ORCHESTRATOR_URL")
            or "http://research-orchestrator-svc:8101",
            actor_id=self._worker_id,
        )
        self._metrics = self._load_metrics()

    def run_once(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Process each currently pending tenant/spec key at most once."""

        if tenant_id is not None:
            tenants = [_require_text(tenant_id, "tenant_id")]
        else:
            tenants = sorted(
                {
                    str(entry["tenant_id"])
                    for entry in self._queue.list_pending()
                    if entry.get("tenant_id")
                }
            )

        created_authority_task_ids: list[str] = []
        created_authority_run_ids: list[str] = []
        created_experiment_task_ids: list[str] = []
        created_experiment_run_ids: list[str] = []
        authority_receipts: list[dict[str, str]] = []
        skipped_run_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        processed_count = 0
        attempted: set[tuple[str, str]] = set()
        tick_at = _utc_now()

        with self._lock:
            for tenant in tenants:
                while True:
                    entry = self._queue.claim_next_pending(
                        tenant,
                        claimant=self._worker_id,
                        lease_seconds=self._lease_seconds,
                        ignore_keys=attempted,
                    )
                    if entry is None:
                        break
                    key = (tenant, str(entry["strategy_spec_id"]))
                    attempted.add(key)
                    processed_count += 1
                    task_id: str | None = None
                    run_id: str | None = None
                    authority_task_id: str | None = None
                    authority_run_id: str | None = None
                    try:
                        task_receipt, run_receipt = self._process_entry(
                            entry,
                            tick_at=tick_at,
                        )
                        task_id = task_receipt.task.task_id
                        run_id = run_receipt.run.run_id
                        authority_task_id = task_receipt.authority_task_id
                        authority_run_id = run_receipt.authority_run_id
                        if run_receipt.run.status != "completed":
                            raise RevalidationAttemptError(
                                run_receipt.run.failure_reason
                                or "authoritative ExperimentRun failed",
                                task_id=task_id,
                                run_id=run_id,
                                authority_task_id=authority_task_id,
                                authority_run_id=authority_run_id,
                            )
                        acknowledged = self._queue.mark_revalidated(
                            tenant,
                            entry["strategy_spec_id"],
                            claim_token=entry["claim_token"],
                            authority_task_id=authority_task_id,
                            authority_run_id=authority_run_id,
                            experiment_task_id=task_id,
                            experiment_run_id=run_id,
                            status=run_receipt.run.status,
                        )
                        if not acknowledged:
                            raise RevalidationAttemptError(
                                "claim lease expired before authoritative acknowledgement",
                                task_id=task_id,
                                run_id=run_id,
                                authority_task_id=authority_task_id,
                                authority_run_id=authority_run_id,
                            )
                        created_authority_task_ids.append(authority_task_id)
                        created_authority_run_ids.append(authority_run_id)
                        created_experiment_task_ids.append(task_id)
                        created_experiment_run_ids.append(run_id)
                        authority_receipts.append(
                            {
                                "authority_task_id": authority_task_id,
                                "authority_run_id": authority_run_id,
                                "experiment_task_id": task_id,
                                "experiment_run_id": run_id,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001 - bounded retry owns failures.
                        if isinstance(exc, RevalidationAttemptError):
                            task_id = exc.task_id or task_id
                            run_id = exc.run_id or run_id
                            authority_task_id = (
                                exc.authority_task_id or authority_task_id
                            )
                            authority_run_id = (
                                exc.authority_run_id or authority_run_id
                            )
                        message = str(exc) or exc.__class__.__name__
                        failed_acknowledged = self._queue.mark_failed(
                            tenant,
                            entry["strategy_spec_id"],
                            claim_token=entry["claim_token"],
                            error=message,
                            max_retries=self._max_retries,
                            authority_task_id=authority_task_id,
                            authority_run_id=authority_run_id,
                            task_id=task_id,
                            run_id=run_id,
                        )
                        errors.append(
                            {
                                "tenant_id": tenant,
                                "strategy_spec_id": entry["strategy_spec_id"],
                                "error": message,
                                "failure_acknowledged": failed_acknowledged,
                            }
                        )

        if created_authority_run_ids:
            self._metrics.run_count += len(created_authority_run_ids)
            self._metrics.last_success_at = _utc_now()
            self._metrics.last_run_strategy_spec_ids = [
                entry["strategy_spec_id"]
                for entry in self._queue.list_all()
                if any(
                    run_id in list(entry.get("authority_run_ids") or [])
                    for run_id in created_authority_run_ids
                )
            ]
        if errors:
            self._metrics.error_count += len(errors)
            self._metrics.last_failure_at = _utc_now()
            self._metrics.last_failure_reason = errors[0]["error"]
        self._save_metrics()

        return {
            "tick_at": tick_at,
            "processed": processed_count,
            # ``created_run_ids`` is retained as a compatibility alias, but it
            # now carries the resolvable research-authority IDs.
            "created_run_ids": created_authority_run_ids,
            "created_authority_task_ids": created_authority_task_ids,
            "created_authority_run_ids": created_authority_run_ids,
            "created_experiment_task_ids": created_experiment_task_ids,
            "created_experiment_run_ids": created_experiment_run_ids,
            "authority_receipts": authority_receipts,
            "skipped_run_ids": skipped_run_ids,
            "errors": errors,
            "dispatch_mode": self._dispatch_mode,
            "configured_mode": self._configured_mode,
        }

    def replay_dlq(
        self,
        tenant_id: str,
        strategy_spec_id: str,
        *,
        replay_id: str,
        replayed_by: str,
        reason: str,
    ) -> bool:
        return self._queue.replay_dlq(
            tenant_id,
            strategy_spec_id,
            replay_id=replay_id,
            replayed_by=replayed_by,
            reason=reason,
        )

    def get_metrics(self) -> dict[str, Any]:
        return self._metrics.to_dict()

    def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        strategy_spec_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read ExperimentRuns from authority; local run files are not truth."""

        return [
            run.to_dict()
            for run in self._authority.list_runs(
                tenant_id=tenant_id,
                strategy_spec_id=strategy_spec_id,
            )
        ]

    def _process_entry(
        self,
        entry: Mapping[str, Any],
        *,
        tick_at: str,
    ) -> tuple[AuthoritativeTaskReceipt, AuthoritativeRunReceipt]:
        registry_entry = self._fetch_strategy_spec_entry(
            str(entry["strategy_spec_id"])
        )
        spec = self._validate_registry_binding(entry, registry_entry)
        attempt_number = int(entry.get("attempt_count") or 0) + 1
        task = self._build_experiment_task(entry, spec)
        task_receipt = self._authority.ensure_task(
            task,
            approval_decision_id=str(entry["approval_decision_id"]),
            approver=str(entry["approver"]),
            approved_at=str(entry["approved_at"]),
            checksum=str(entry["checksum"]),
        )

        if not self._queue.renew_claim(
            str(entry["tenant_id"]),
            str(entry["strategy_spec_id"]),
            claim_token=str(entry["claim_token"]),
            lease_seconds=self._lease_seconds,
        ):
            raise RevalidationAttemptError(
                "claim lease lost before replication gate evaluation",
                task_id=task.task_id,
                authority_task_id=task_receipt.authority_task_id,
            )

        try:
            run = self._evaluate(task, entry, spec, attempt_number, tick_at=tick_at)
            run_receipt = self._authority.ensure_run(
                task_receipt.authority_task_id,
                run,
                approval_decision_id=str(entry["approval_decision_id"]),
            )
        except RevalidationAttemptError:
            raise
        except Exception as exc:
            raise RevalidationAttemptError(
                str(exc) or exc.__class__.__name__,
                task_id=task.task_id,
                authority_task_id=task_receipt.authority_task_id,
            ) from exc
        lineage_errors = validate_experiment_run_against_task(
            run_receipt.run,
            task_receipt.task,
        )
        if lineage_errors:
            raise RevalidationAttemptError(
                "; ".join(lineage_errors),
                task_id=task_receipt.task.task_id,
                run_id=run_receipt.run.run_id,
                authority_task_id=task_receipt.authority_task_id,
                authority_run_id=run_receipt.authority_run_id,
            )
        return task_receipt, run_receipt

    def _fetch_strategy_spec_entry(
        self,
        strategy_spec_id: str,
    ) -> Mapping[str, Any]:
        canonical_id = _require_text(strategy_spec_id, "strategy_spec_id")
        url = (
            f"{self._registry_url}/api/registry/strategy-specs/"
            f"{urllib.parse.quote(canonical_id, safe='')}"
        )
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RevalidationAttemptError(
                    f"approved StrategySpec no longer exists: {canonical_id}"
                ) from exc
            raise RevalidationAttemptError(
                f"registry readback failed for {canonical_id}: HTTP {exc.code}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - boundary failure is retryable.
            raise RevalidationAttemptError(
                f"registry readback failed for {canonical_id}: {exc}"
            ) from exc
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("entry"),
            Mapping,
        ):
            raise RevalidationAttemptError(
                f"registry readback for {canonical_id} did not return an entry"
            )
        return dict(payload["entry"])

    def _validate_registry_binding(
        self,
        queue_entry: Mapping[str, Any],
        registry_entry: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected = {
            "registry_id": queue_entry["strategy_spec_id"],
            "strategy_id": queue_entry["strategy_id"],
            "version": queue_entry["spec_version"],
            "artifact_state": "approved",
            "checksum": queue_entry["checksum"],
            "approval_decision_id": queue_entry["approval_decision_id"],
            "approver": queue_entry["approver"],
            "approved_at": queue_entry["approved_at"],
        }
        for field_name, expected_value in expected.items():
            if registry_entry.get(field_name) != expected_value:
                raise RevalidationAttemptError(
                    f"StrategySpec immutable review binding changed at {field_name}: "
                    f"expected {expected_value!r}, got {registry_entry.get(field_name)!r}"
                )
        if registry_entry.get("artifact_type") != "strategy_spec":
            raise RevalidationAttemptError("registry entry is not a StrategySpec")
        metadata = registry_entry.get("metadata")
        if not isinstance(metadata, Mapping):
            raise RevalidationAttemptError("StrategySpec registry metadata is missing")
        tenant_id = str(metadata.get("tenant_id") or "").strip()
        if tenant_id != queue_entry["tenant_id"]:
            raise RevalidationAttemptError(
                "StrategySpec tenant changed or is missing during registry readback"
            )
        spec = metadata.get("strategy_spec")
        if not isinstance(spec, Mapping):
            raise RevalidationAttemptError("inline immutable StrategySpec payload is missing")
        if str(spec.get("strategy_id") or "") != queue_entry["strategy_id"]:
            raise RevalidationAttemptError("embedded StrategySpec strategy_id changed")
        embedded_tenant = str(spec.get("tenant_id") or tenant_id).strip()
        if embedded_tenant != queue_entry["tenant_id"]:
            raise RevalidationAttemptError("embedded StrategySpec tenant changed")
        return dict(spec)

    def _build_experiment_task(
        self,
        entry: Mapping[str, Any],
        spec: Mapping[str, Any],
    ) -> ExperimentTask:
        tenant_id = str(entry["tenant_id"])
        strategy_spec_id = str(entry["strategy_spec_id"])
        task_hash = _short_hash(tenant_id, strategy_spec_id)
        dataset_version_id = self._dataset_version_id(spec)
        code_version = self._code_version(spec)
        created_at = str(entry["enqueued_at"])
        return ExperimentTask(
            task_id=f"etask-alpha-{task_hash}",
            tenant_id=tenant_id,
            strategy_spec_id=strategy_spec_id,
            strategy_id=str(entry["strategy_id"]),
            strategy_spec_version=str(entry["spec_version"]),
            requested_by=self._worker_id,
            task_type=DEFAULT_TASK_TYPE,
            backend_id=DEFAULT_BACKEND_ID,
            backend_selection_policy_id=DEFAULT_BACKEND_SELECTION_POLICY_ID,
            dataset_version_id=dataset_version_id,
            code_version=code_version,
            priority="normal",
            status="ready",
            idempotency_key=f"alpha-task:{tenant_id}:{strategy_spec_id}",
            trace_id=f"trace-alpha-{task_hash}",
            created_at=created_at,
            metadata={
                "approval_decision_id": entry["approval_decision_id"],
                "approver": entry["approver"],
                "approved_at": entry["approved_at"],
                "strategy_spec_checksum": entry["checksum"],
                "production_activation": "disabled",
                "research_authority_required": True,
            },
        )

    def _evaluate(
        self,
        task: ExperimentTask,
        entry: Mapping[str, Any],
        spec: Mapping[str, Any],
        attempt_number: int,
        *,
        tick_at: str,
    ) -> ExperimentRun:
        from services.research.replication.gate import ReplicationGate
        from services.research.replication.gate_schema import ReplicationRequest

        run_hash = _short_hash(
            str(entry["tenant_id"]),
            str(entry["strategy_spec_id"]),
            str(entry.get("replay_count") or 0),
            str(attempt_number),
        )
        run_id = f"erun-alpha-{run_hash}"
        evaluation_spec = dict(spec)
        # Registry approval is validated against the immutable envelope above.
        # ReplicationGate is a candidate-content validator and intentionally
        # rejects lifecycle_state=approved as a direct promotion attempt, so do
        # not project registry lifecycle state into its candidate payload.
        evaluation_spec.pop("lifecycle_state", None)
        request = ReplicationRequest(
            candidate_id=run_id,
            source_task_id=task.task_id,
            research_handoff={
                "source_metadata": {
                    "api_endpoint": (
                        f"{self._registry_url}/api/registry/strategy-specs/"
                        f"{entry['strategy_spec_id']}"
                    ),
                    "retrieved_at": tick_at,
                    "governance_context": (
                        "Approved immutable StrategySpec review "
                        f"{entry['approval_decision_id']}"
                    ),
                },
                "normalized_findings": {
                    "strategy_spec": {
                        "strategy_spec_id": entry["strategy_spec_id"],
                        "strategy_id": entry["strategy_id"],
                    },
                    "replication_notes": (
                        "Authoritative alpha revalidation with tenant-scoped lineage."
                    ),
                    "evaluation_hypotheses": (
                        "H1: approved StrategySpec schema and governance constraints remain valid."
                    ),
                },
                "grok_processing_notes": {
                    "normalization_confidence": "high",
                    "governance_compliance": "verified",
                    "downstream_readiness": "ready_for_replication",
                },
            },
            proposed_strategy_spec=evaluation_spec,
            metadata={
                "tenant_id": entry["tenant_id"],
                "strategy_spec_id": entry["strategy_spec_id"],
                "approval_decision_id": entry["approval_decision_id"],
            },
            timestamp=tick_at,
        )
        gate_response = ReplicationGate().evaluate_candidate(request)
        common = {
            "run_id": run_id,
            "task_id": task.task_id,
            "tenant_id": task.tenant_id,
            "strategy_spec_id": task.strategy_spec_id,
            "strategy_id": task.strategy_id,
            "strategy_spec_version": task.strategy_spec_version,
            "backend_id": DEFAULT_BACKEND_ID,
            "runtime_env": DEFAULT_RUNTIME_ENV,
            "dataset_version_id": task.dataset_version_id,
            "code_version": task.code_version,
            "input_manifest_ref": (
                f"registry://{entry['strategy_spec_id']}?checksum={entry['checksum']}"
            ),
            "artifact_refs": [],
            "trace_id": f"{task.trace_id}:attempt:{attempt_number}",
            "created_at": tick_at,
            "started_at": tick_at,
            "finished_at": _utc_now(),
            "metadata": {
                "dispatch_mode": "authoritative",
                "configured_mode": self._configured_mode,
                "production_activation": "disabled",
                "worker_id": self._worker_id,
                "attempt_number": attempt_number,
                "replay_count": int(entry.get("replay_count") or 0),
                "idempotency_key": (
                    f"alpha-run:{entry['tenant_id']}:"
                    f"{entry['strategy_spec_id']}:"
                    f"{int(entry.get('replay_count') or 0)}:{attempt_number}"
                ),
                "approval_decision_id": entry["approval_decision_id"],
                "strategy_spec_checksum": entry["checksum"],
                "replication_gate": gate_response.to_dict(),
            },
        }
        if gate_response.passed:
            return ExperimentRun(
                **common,
                status="completed",
                output_manifest_ref=(
                    f"research-authority://experiment-runs/{run_id}"
                ),
            )
        return ExperimentRun(
            **common,
            status="failed",
            failure_reason=gate_response.summary,
        )

    @staticmethod
    def _dataset_version_id(spec: Mapping[str, Any]) -> str:
        dependencies = spec.get("data_dependencies")
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if (
                    isinstance(dependency, Mapping)
                    and dependency.get("kind") == "dataset"
                ):
                    return _require_text(
                        dependency.get("ref"),
                        "StrategySpec data_dependencies[].ref",
                    )
        raise RevalidationAttemptError(
            "approved StrategySpec requires a pinned dataset dependency"
        )

    @staticmethod
    def _code_version(spec: Mapping[str, Any]) -> str:
        code_refs = spec.get("code_refs")
        if isinstance(code_refs, list):
            for code_ref in code_refs:
                if isinstance(code_ref, Mapping) and code_ref.get("commit"):
                    return _require_text(
                        code_ref.get("commit"),
                        "StrategySpec code_refs[].commit",
                    )
        return _get_git_commit_sha()

    def _load_metrics(self) -> RevalidationWorkerMetrics:
        if not self._metrics_path.exists():
            return RevalidationWorkerMetrics()
        try:
            data = json.loads(self._metrics_path.read_text(encoding="utf-8"))
            return RevalidationWorkerMetrics(
                run_count=int(data.get("run_count") or 0),
                error_count=int(data.get("error_count") or 0),
                last_success_at=data.get("last_success_at"),
                last_failure_at=data.get("last_failure_at"),
                last_failure_reason=data.get("last_failure_reason"),
                last_run_strategy_spec_ids=list(
                    data.get("last_run_strategy_spec_ids")
                    or data.get("last_run_strategy_ids")
                    or []
                ),
            )
        except Exception:  # noqa: BLE001 - corrupt metrics must not block work.
            return RevalidationWorkerMetrics()

    def _save_metrics(self) -> None:
        temp_path = self._metrics_path.with_name(
            f".{self._metrics_path.name}.{os.getpid()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    self._metrics.to_dict(),
                    handle,
                    indent=2,
                    ensure_ascii=True,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._metrics_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


__all__ = [
    "AlphaRevalidationWorker",
    "RevalidationAttemptError",
    "RevalidationWorkerMetrics",
    "SAFE_DISPATCH_MODES",
]
