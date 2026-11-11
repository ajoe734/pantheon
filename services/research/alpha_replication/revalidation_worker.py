"""Approved-only Alpha replication worker with authoritative run readback.

Each queue attempt revalidates the exact immutable StrategySpec registry id,
persists an ExperimentTask intent before evaluation, evaluates through the
research-only ReplicationGate, and persists the resulting ExperimentRun through
the research-orchestrator authority.  The queue is acknowledged only after an
independent authority readback succeeds.
"""

from __future__ import annotations

import copy
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
from typing import Any, Collection, Mapping

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
from services.service_token_file import configured_service_token

from .queue import AlphaReplicationQueue, _require_text


SAFE_DISPATCH_MODES = frozenset({"authoritative", "handoff_only"})
DEFAULT_DISPATCH_MODE = "authoritative"
DEFAULT_BACKEND_SELECTION_POLICY_ID = "alpha-replication-authoritative-v1"
DEFAULT_RUNTIME_ENV = "research"
DEFAULT_TASK_TYPE = "rapid_eval"
DEFAULT_BACKEND_ID = "replication_gate"


TEACHING_BASELINE_MODE = "baseline"


TEACHING_BASELINE_RETRY_BACKOFF_SECONDS = 3600
# Training owner reasons that no later attempt can change.
_FINAL_BASELINE_REASONS = (
    "controls that match their baseline values",
    "baseline evaluation already completed",
)


def _baseline_idempotency_key(session_id: str, attempt: int = 1) -> str:
    # Attempt 1 keeps the key already recorded on hosted; the Training owner
    # replays a failed job for the same key, so each retry needs its own.
    base = f"teaching-baseline-eval-{session_id}"
    return base if attempt <= 1 else f"{base}-r{attempt}"


def _is_final_baseline_reason(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _FINAL_BASELINE_REASONS)


def _seconds_since(timestamp: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


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
    last_teaching_session_ids: list[str] = field(default_factory=list)

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
        training_session_url: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self._queue = queue
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._metrics_path = self._data_dir / "alpha_revalidation_metrics.json"
        self._loop_records_path = (
            self._data_dir / "alpha_revalidation_loop_records.json"
        )
        self._worker_id = _require_text(worker_id, "worker_id")
        self._lock = threading.RLock()

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
        self._training_session_url = (
            training_session_url
            or os.getenv("PANTHEON_TRAINING_SESSION_API_URL")
            or os.getenv("TRAINING_SESSION_API_URL")
            or os.getenv("TRAINING_SESSION_URL")
            or "http://training-session-svc:8099"
        ).rstrip("/")
        self._authority = authority or ResearchAuthorityHttpClient(
            os.getenv("RESEARCH_ORCHESTRATOR_URL")
            or "http://research-orchestrator-svc:8101",
            actor_id=self._worker_id,
        )
        self._trial_counter_path = self._data_dir / "family_trial_counts.json"
        self._backtest_run_store_path = self._data_dir / "backtest_runs.json"
        self._backtest_run_store = self._new_backtest_run_store()
        self._outbox_path = self._data_dir / "research_memory_outbox.jsonl"
        self._metrics = self._load_metrics()
        self._loop_records = self._load_loop_records()

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
        created_teaching_session_ids: list[str] = []
        created_loop_records: list[dict[str, Any]] = []
        authority_receipts: list[dict[str, Any]] = []
        skipped_run_ids: list[str] = []
        insufficient_history_strategy_spec_ids: list[str] = []
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
                        if (
                            run_receipt.run.metadata.get("candidate_status")
                            == "insufficient_history"
                        ):
                            acknowledged = self._queue.mark_insufficient_history(
                                tenant,
                                entry["strategy_spec_id"],
                                claim_token=entry["claim_token"],
                                reason=(
                                    run_receipt.run.failure_reason
                                    or "insufficient_history"
                                ),
                                authority_task_id=authority_task_id,
                                authority_run_id=authority_run_id,
                                experiment_task_id=task_id,
                                experiment_run_id=run_id,
                            )
                            if not acknowledged:
                                raise RevalidationAttemptError(
                                    "claim lease expired before insufficient_history acknowledgement",
                                    task_id=task_id,
                                    run_id=run_id,
                                    authority_task_id=authority_task_id,
                                    authority_run_id=authority_run_id,
                                )
                            skipped_run_ids.append(run_id)
                            insufficient_history_strategy_spec_ids.append(
                                str(entry["strategy_spec_id"])
                            )
                            continue
                        if run_receipt.run.status != "completed":
                            raise RevalidationAttemptError(
                                run_receipt.run.failure_reason
                                or "authoritative ExperimentRun failed",
                                task_id=task_id,
                                run_id=run_id,
                                authority_task_id=authority_task_id,
                                authority_run_id=authority_run_id,
                            )
                        teaching_receipt = self._invoke_persona_teaching(
                            entry,
                            task_receipt,
                            run_receipt,
                            tick_at=tick_at,
                        )
                        session_id = teaching_receipt["session_id"]
                        session_readback = teaching_receipt["readback"]
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
                        trigger_id = str(
                            entry.get("admission_id")
                            or entry.get("trigger_id")
                            or entry.get("approval_decision_id")
                            or entry["strategy_spec_id"]
                        )
                        terminal_output_id = run_id
                        if teaching_receipt.get("baseline_applicable"):
                            baseline_state = self._ensure_teaching_baseline(
                                session_id,
                                tenant_id=tenant,
                                readback=session_readback,
                            )
                        else:
                            baseline_state = {
                                "status": "skipped",
                                "reason": "no_training_owner_session",
                            }
                        loop_record = {
                            "loop_id": "alpha_replication",
                            "loop_index": 3,
                            "canonical_id": "alpha_replication",
                            "tenant_id": tenant,
                            "strategy_spec_id": str(entry["strategy_spec_id"]),
                            "strategy_id": str(entry.get("strategy_id") or ""),
                            "trigger_id": trigger_id,
                            "terminal_output_id": terminal_output_id,
                            "terminal_output_details": {
                                "experiment_run_id": run_id,
                                "research_authority_run_id": authority_run_id,
                                "research_authority_task_id": authority_task_id,
                                "approval_id": str(entry.get("approval_decision_id") or ""),
                            },
                            "next_consumer_receipt_id": session_id,
                            "owner_worker_identity": self._worker_id,
                            "durable_reload_readback": session_readback,
                            "recorded_at": tick_at,
                            "teaching_baseline": baseline_state,
                        }
                        self._save_loop_record(loop_record)
                        created_authority_task_ids.append(authority_task_id)
                        created_authority_run_ids.append(authority_run_id)
                        created_experiment_task_ids.append(task_id)
                        created_experiment_run_ids.append(run_id)
                        created_teaching_session_ids.append(session_id)
                        created_loop_records.append(loop_record)
                        authority_receipts.append(
                            {
                                "authority_task_id": authority_task_id,
                                "authority_run_id": authority_run_id,
                                "experiment_task_id": task_id,
                                "experiment_run_id": run_id,
                                "next_consumer_receipt_id": session_id,
                                "teaching_session_id": session_id,
                                "trigger_id": trigger_id,
                                "terminal_output_id": terminal_output_id,
                                "owner_worker_identity": self._worker_id,
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

        try:
            self._reensure_teaching_baselines(
                set(tenants) if tenant_id is not None else None,
                skip_session_ids=set(created_teaching_session_ids),
            )
        except Exception:  # noqa: BLE001 - baseline retry never fails the tick.
            pass

        if created_authority_run_ids:
            self._metrics.run_count += len(created_authority_run_ids)
            self._metrics.last_success_at = _utc_now()
            self._metrics.last_teaching_session_ids = list(created_teaching_session_ids)
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
            "created_teaching_session_ids": created_teaching_session_ids,
            "next_consumer_receipt_ids": created_teaching_session_ids,
            "authority_receipts": authority_receipts,
            "loop_records": created_loop_records,
            "skipped_run_ids": skipped_run_ids,
            "insufficient_history_strategy_spec_ids": insufficient_history_strategy_spec_ids,
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

    def get_loop_record(
        self,
        tenant_id: str,
        strategy_spec_id: str,
    ) -> dict[str, Any] | None:
        """Read fresh from disk to guarantee durable reload readback."""
        records = self._load_loop_records()
        key = f"{tenant_id}:{strategy_spec_id}"
        record = records.get(key)
        return dict(record) if record else None

    def list_loop_records(
        self,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records = self._load_loop_records()
        if tenant_id is None:
            return list(records.values())
        return [r for r in records.values() if r.get("tenant_id") == tenant_id]

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
            headers = {}
            token = configured_service_token("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(url, headers=headers, method="GET")
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

    def _new_backtest_run_store(self) -> Any:
        from services.research.alpha_replication.shared_stores import SharedBacktestRunStore

        return SharedBacktestRunStore(self._backtest_run_store_path)

    def _resolve_backtest_evaluation(
        self,
        *,
        run_id: str,
        task: ExperimentTask,
        entry: Mapping[str, Any],
        raw_spec: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Run the real protocol on the candidate's own approved inputs.

        Fails closed (returns ``None``) when the exact candidate artifact or the
        governed dataset version it was approved against is absent or unbound.
        No artifact, dataset or cached result is ever generated or reused here.
        """
        from services.registry.strategy_artifact import strategy_artifact_checksum
        from services.research.vectorbt.adapter.vectorbt_adapter import (
            run_replication_backtest_protocol,
        )

        if raw_spec.get("skip_backtest") is True or raw_spec.get("backtest_evaluation") is False:
            return None

        provided_eval = raw_spec.get("backtest_evaluation")
        if isinstance(provided_eval, Mapping):
            # Never trusted: ReplicationGate accepts it only if it matches a
            # persisted run bound to the candidate artifact and dataset.
            return dict(provided_eval)

        strategy_artifact = raw_spec.get("strategy_artifact")
        dataset = raw_spec.get("backtest_dataset")
        if not isinstance(strategy_artifact, Mapping) or not isinstance(dataset, Mapping):
            return None
        strategy_artifact = dict(strategy_artifact)
        dataset = dict(dataset)

        requested_dataset = str(task.dataset_version_id or "").strip()
        if not requested_dataset or dataset.get("dataset_id") != requested_dataset:
            return None
        if strategy_artifact.get("strategy_id") != entry.get("strategy_id"):
            return None
        expected_checksum = raw_spec.get("strategy_artifact_checksum")
        try:
            actual_checksum = strategy_artifact_checksum(strategy_artifact)
        except Exception:
            return None
        if isinstance(expected_checksum, str) and expected_checksum != actual_checksum:
            return None

        evaluated = run_replication_backtest_protocol(
            strategy_artifact=strategy_artifact,
            dataset=dataset,
            run_id=f"bt-{run_id}",
        )
        self._backtest_run_store.record(evaluated)
        return evaluated

    def _publish_research_finding(
        self,
        *,
        run_id: str,
        task: ExperimentTask,
        entry: Mapping[str, Any],
        gate_response: Any,
        backtest_summary: Mapping[str, Any],
        backtest_ref: str | None,
    ) -> dict[str, Any]:
        from services.research.research_memory_outbox import (
            ResearchMemoryOutboxRecord,
            ResearchMemoryOutboxStore,
            get_outbox_store,
        )

        tx_costs = (
            backtest_summary.get("transaction_costs")
            if isinstance(backtest_summary.get("transaction_costs"), Mapping)
            else {}
        )
        metrics = {
            "sharpe_ratio": backtest_summary.get("out_of_sample_sharpe_ratio"),
            "in_sample_sharpe_ratio": backtest_summary.get("in_sample_sharpe_ratio"),
            "out_of_sample_sharpe_ratio": backtest_summary.get("out_of_sample_sharpe_ratio"),
            "deflated_sharpe_ratio": backtest_summary.get("deflated_sharpe_ratio"),
            "deflated_sharpe_threshold": backtest_summary.get("deflated_sharpe_threshold"),
            "trial_count": backtest_summary.get("trial_count"),
            "max_drawdown": backtest_summary.get("out_of_sample_max_drawdown"),
            "total_return": backtest_summary.get("out_of_sample_total_return"),
            "round_trips": backtest_summary.get("out_of_sample_round_trips"),
            "buy_and_hold_sharpe_ratio": backtest_summary.get("buy_and_hold_sharpe_ratio"),
            "tw_0050_sharpe_ratio": backtest_summary.get("tw_0050_sharpe_ratio"),
            "walk_forward_fold_sharpes": backtest_summary.get("walk_forward_fold_sharpes"),
            "commission_rate": tx_costs.get("commission_rate"),
            "equity_transaction_tax_rate": tx_costs.get("equity_transaction_tax_rate"),
            "etf_transaction_tax_rate": tx_costs.get("etf_transaction_tax_rate"),
            "backtest_run_id": backtest_summary.get("backtest_run_id"),
        }
        artifact_refs = (
            [{"artifact_type": "backtest_run", "uri": backtest_ref}]
            if backtest_ref
            else []
        )
        now = _utc_now()
        outbox_record = ResearchMemoryOutboxRecord(
            outbox_id=f"mout-alpha-{run_id}",
            run_id=run_id,
            task_id=task.task_id,
            artifact_id=str(backtest_summary.get("backtest_run_id") or "") or None,
            source_event_type="research_finding_published",
            source_event_id=run_id,
            sponsor_persona_id="persona-tw-equity",
            summary=gate_response.summary,
            headline=f"Replicated Taiwan strategy {entry['strategy_id']}",
            confidence=1.0,
            evidence_refs=[backtest_ref] if backtest_ref else [f"strategy_spec:{entry['strategy_spec_id']}"],
            dataset_refs=[task.dataset_version_id],
            license_scope="governed_tw_market",
            allowed_use=["research_memory", "replication_evidence"],
            supersedes=[],
            contradicts=[],
            expires_at=None,
            trace_id=task.trace_id,
            status="pending",
            created_at=now,
            updated_at=now,
            metadata={
                "event_type": "research_finding_published",
                "tenant_id": str(entry["tenant_id"]),
                "strategy_spec_id": str(entry["strategy_spec_id"]),
                "strategy_id": str(entry["strategy_id"]),
                "family_id": backtest_summary.get("family_id"),
                "trial_count": backtest_summary.get("trial_count"),
                "transaction_costs": dict(tx_costs),
                "metrics": metrics,
                "artifact_refs": artifact_refs,
            },
        )
        worker_outbox = ResearchMemoryOutboxStore(self._data_dir)
        saved = worker_outbox.create_record(outbox_record)
        try:
            default_store = get_outbox_store()
            if default_store.path.resolve() != worker_outbox.path.resolve():
                default_store.create_record(outbox_record)
        except Exception:
            pass
        return saved.to_dict()

    def _evaluate(
        self,
        task: ExperimentTask,
        entry: Mapping[str, Any],
        spec: Mapping[str, Any],
        attempt_number: int,
        *,
        tick_at: str,
    ) -> ExperimentRun:
        from services.registry.promotion.gate import PromotionGate, PromotionState
        from services.registry.strategy_artifact import strategy_artifact_checksum
        from services.research.alpha_replication.shared_stores import (
            SharedReplicationGate as ReplicationGate,
        )
        from services.research.replication.gate import create_promotion_request
        from services.research.replication.gate_schema import (
            CandidateAdmissionStatus,
            ReplicationRequest,
        )

        run_hash = _short_hash(
            str(entry["tenant_id"]),
            str(entry["strategy_spec_id"]),
            str(entry.get("replay_count") or 0),
            str(attempt_number),
        )
        run_id = f"erun-alpha-{run_hash}"
        raw_spec = dict(spec)
        evaluation_spec = dict(spec)
        # Registry approval is validated against the immutable envelope above.
        # ReplicationGate is a candidate-content validator and intentionally
        # rejects lifecycle_state=approved as a direct promotion attempt, so do
        # not project registry lifecycle state into its candidate payload.
        for non_schema_key in (
            "lifecycle_state",
            "backtest_evaluation",
            "backtest_dataset",
            "strategy_artifact",
            "skip_backtest",
            "history_trading_days",
        ):
            evaluation_spec.pop(non_schema_key, None)

        artifact_checksum: str | None = None
        if isinstance(raw_spec.get("strategy_artifact"), Mapping):
            try:
                artifact_checksum = strategy_artifact_checksum(raw_spec["strategy_artifact"])
            except Exception:
                artifact_checksum = None

        backtest_eval = self._resolve_backtest_evaluation(
            run_id=run_id,
            task=task,
            entry=entry,
            raw_spec=raw_spec,
        )

        # Do NOT write self-attested grok_processing_notes (normalization_confidence,
        # governance_compliance, downstream_readiness) that the worker did not check.
        handoff_payload: dict[str, Any] = {
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
                    "Authoritative alpha revalidation with tenant-scoped lineage and real Taiwan backtest."
                ),
                "evaluation_hypotheses": (
                    "H1: candidate strategy artifact achieves positive deflated out-of-sample Sharpe on governed Taiwan market data after commission and tax."
                ),
            },
        }
        if isinstance(raw_spec.get("family_id"), str):
            handoff_payload["family_id"] = raw_spec["family_id"]

        request = ReplicationRequest(
            candidate_id=run_id,
            source_task_id=task.task_id,
            research_handoff=handoff_payload,
            proposed_strategy_spec=evaluation_spec,
            backtest_evaluation=backtest_eval,
            metadata={
                "tenant_id": entry["tenant_id"],
                "strategy_spec_id": entry["strategy_spec_id"],
                "approval_decision_id": entry["approval_decision_id"],
                "dataset_version_id": task.dataset_version_id,
                **({"strategy_artifact_checksum": artifact_checksum} if artifact_checksum else {}),
            },
            timestamp=tick_at,
        )
        gate = ReplicationGate(
            trial_counter_path=self._trial_counter_path,
            backtest_run_store=self._backtest_run_store,
        )
        gate_response = gate.evaluate_candidate(request)

        gate_metadata = getattr(gate_response, "metadata", None)
        backtest_summary: dict[str, Any] = (
            dict(gate_metadata.get("backtest_summary"))
            if isinstance(gate_metadata, Mapping)
            and isinstance(gate_metadata.get("backtest_summary"), Mapping)
            else {}
        )
        if not backtest_summary and isinstance(backtest_eval, Mapping):
            is_data = backtest_eval.get("in_sample") if isinstance(backtest_eval.get("in_sample"), Mapping) else {}
            oos_data = backtest_eval.get("out_of_sample") if isinstance(backtest_eval.get("out_of_sample"), Mapping) else {}
            backtest_summary = {
                "backtest_run_id": backtest_eval.get("backtest_run_id"),
                "in_sample_sharpe_ratio": is_data.get("sharpe_ratio"),
                "out_of_sample_sharpe_ratio": oos_data.get("sharpe_ratio"),
                "out_of_sample_max_drawdown": oos_data.get("max_drawdown"),
                "out_of_sample_total_return": oos_data.get("total_return"),
                "out_of_sample_round_trips": oos_data.get("round_trips"),
                "transaction_costs": backtest_eval.get("transaction_costs") or {},
            }
        backtest_run_id = (
            backtest_summary.get("backtest_run_id")
            or (backtest_eval.get("backtest_run_id") if isinstance(backtest_eval, Mapping) else None)
        )
        backtest_ref = f"backtest://{backtest_run_id}" if backtest_run_id else None
        artifact_refs = [backtest_ref] if backtest_ref else []

        run_metadata: dict[str, Any] = {
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
            "backtest_run_id": backtest_run_id,
            "backtest_summary": dict(backtest_summary),
        }

        admission_status = getattr(gate_response, "admission_status", None)
        if admission_status == CandidateAdmissionStatus.INSUFFICIENT_HISTORY:
            run_metadata["candidate_status"] = "insufficient_history"
            run_metadata["replication_status"] = "insufficient_history"
            common_insufficient = {
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
                "artifact_refs": artifact_refs,
                "trace_id": f"{task.trace_id}:attempt:{attempt_number}",
                "created_at": tick_at,
                "started_at": tick_at,
                "finished_at": _utc_now(),
                "metadata": run_metadata,
            }
            return ExperimentRun(
                **common_insufficient,
                status="cancelled",
                failure_reason=gate_response.summary,
            )

        if gate_response.passed:
            if hasattr(gate_response, "candidate_id"):
                promotion_request = create_promotion_request(
                    gate_response,
                    evaluation_spec,
                )
                if promotion_request is not None:
                    draft_entry = PromotionGate.build_draft_entry_from_replication_request(
                        promotion_request,
                        strategy_id=str(entry["strategy_id"]),
                        checksum=str(entry["checksum"]),
                    )
                    PromotionGate().check_requirements(
                        PromotionState.CANDIDATE,
                        draft_entry,
                    )
                    run_metadata["promotion_request"] = promotion_request.to_dict()
                    run_metadata["promotion_gate_entry"] = draft_entry
            finding_record = self._publish_research_finding(
                run_id=run_id,
                task=task,
                entry=entry,
                gate_response=gate_response,
                backtest_summary=backtest_summary,
                backtest_ref=backtest_ref,
            )
            run_metadata["research_finding_outbox_id"] = finding_record.get("outbox_id")

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
            "artifact_refs": artifact_refs,
            "trace_id": f"{task.trace_id}:attempt:{attempt_number}",
            "created_at": tick_at,
            "started_at": tick_at,
            "finished_at": _utc_now(),
            "metadata": run_metadata,
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
                last_teaching_session_ids=list(
                    data.get("last_teaching_session_ids") or []
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

    def _load_loop_records(self) -> dict[str, dict[str, Any]]:
        if not self._loop_records_path.exists():
            return {}
        try:
            data = json.loads(self._loop_records_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): dict(v)
                    for k, v in data.items()
                    if isinstance(v, Mapping)
                }
            if isinstance(data, list):
                result: dict[str, dict[str, Any]] = {}
                for item in data:
                    if isinstance(item, Mapping):
                        key = (
                            f"{item.get('tenant_id')}:{item.get('strategy_spec_id')}"
                        )
                        result[key] = dict(item)
                return result
            return {}
        except Exception:  # noqa: BLE001 - corrupted loop records must not crash worker.
            return {}

    def _save_loop_record(self, record: dict[str, Any]) -> None:
        key = f"{record['tenant_id']}:{record['strategy_spec_id']}"
        with self._lock:
            self._loop_records[key] = dict(record)
            temp_path = self._loop_records_path.with_name(
                f".{self._loop_records_path.name}.{os.getpid()}.tmp"
            )
            try:
                with temp_path.open("w", encoding="utf-8") as handle:
                    json.dump(
                        self._loop_records,
                        handle,
                        indent=2,
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self._loop_records_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    def _teaching_headers(self, tenant_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-Id": tenant_id,
            "X-Pantheon-Service": (
                os.getenv("ALPHA_REPLICATION_CALLER_SERVICE")
                or "training-session-preview-worker"
            ),
        }
        token = (
            configured_service_token("ALPHA_REPLICATION_TRAINING_SERVICE_TOKEN")
            or configured_service_token("TRAINING_SESSION_WORKER_TOKEN")
            or os.getenv("PANTHEON_L12_TRAINING_TOKEN")
            or os.getenv("LOCAL_TRAINING_WORKER_TOKEN")
            or ""
        ).strip()
        if token:
            headers["Authorization"] = (
                token if token.startswith("Bearer ") else f"Bearer {token}"
            )
        return headers

    def _post_teaching_session(
        self,
        payload: Mapping[str, Any],
        *,
        tenant_id: str,
    ) -> Mapping[str, Any]:
        url = f"{self._training_session_url}/api/training/sessions"
        headers = self._teaching_headers(tenant_id)
        encoded_data = json.dumps(dict(payload)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded_data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status not in (200, 201):
                    raise RevalidationAttemptError(
                        f"Persona Teaching creation returned unexpected HTTP {status}"
                    )
                payload_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8")
            except Exception:
                err_body = ""
            raise RevalidationAttemptError(
                f"Persona Teaching creation failed with HTTP {exc.code}: {err_body or exc.reason}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network/transport boundary failures.
            raise RevalidationAttemptError(
                f"Persona Teaching invocation failed: {exc}"
            ) from exc
        if not isinstance(payload_data, Mapping):
            raise RevalidationAttemptError(
                "Persona Teaching response was not a mapping"
            )
        return payload_data

    def _fetch_teaching_session(
        self,
        session_id: str,
        *,
        tenant_id: str,
    ) -> Mapping[str, Any]:
        canonical_id = _require_text(session_id, "session_id")
        url = (
            f"{self._training_session_url}/api/training/sessions/"
            f"{urllib.parse.quote(canonical_id, safe='')}"
        )
        headers = self._teaching_headers(tenant_id)
        headers.pop("Content-Type", None)
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status != 200:
                    raise RevalidationAttemptError(
                        f"Persona Teaching readback returned unexpected HTTP {status}"
                    )
                payload_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8")
            except Exception:
                err_body = ""
            raise RevalidationAttemptError(
                f"Persona Teaching readback failed for {canonical_id} with HTTP {exc.code}: {err_body or exc.reason}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network/transport boundary failures.
            raise RevalidationAttemptError(
                f"Persona Teaching readback failed for {canonical_id}: {exc}"
            ) from exc
        if not isinstance(payload_data, Mapping):
            raise RevalidationAttemptError(
                f"Persona Teaching readback for {canonical_id} was not a mapping"
            )
        return payload_data

    def _post_teaching_baseline_job(
        self,
        session_id: str,
        *,
        tenant_id: str,
        attempt: int = 1,
    ) -> Mapping[str, Any]:
        canonical_id = _require_text(session_id, "session_id")
        url = (
            f"{self._training_session_url}/api/training/sessions/"
            f"{urllib.parse.quote(canonical_id, safe='')}/preview-jobs"
        )
        headers = self._teaching_headers(tenant_id)
        headers["Idempotency-Key"] = _baseline_idempotency_key(canonical_id, attempt)
        body = json.dumps({"mode": TEACHING_BASELINE_MODE, "terminalize_session": False})
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status not in (200, 201):
                    raise RevalidationAttemptError(
                        f"Teaching baseline queue returned unexpected HTTP {status}"
                    )
                payload_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8")
            except Exception:
                err_body = ""
            raise RevalidationAttemptError(
                f"Teaching baseline queue failed for {canonical_id} with HTTP "
                f"{exc.code}: {err_body or exc.reason}"
            ) from exc
        except RevalidationAttemptError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/transport boundary failures.
            raise RevalidationAttemptError(
                f"Teaching baseline queue failed for {canonical_id}: {exc}"
            ) from exc
        if not isinstance(payload_data, Mapping) or not payload_data.get("job_id"):
            raise RevalidationAttemptError(
                f"Teaching baseline queue for {canonical_id} returned no job_id"
            )
        return payload_data

    def _fetch_preview_job(self, job_id: str, *, tenant_id: str) -> Mapping[str, Any]:
        url = (
            f"{self._training_session_url}/api/training/preview-jobs/"
            f"{urllib.parse.quote(_require_text(job_id, 'job_id'), safe='')}"
        )
        headers = self._teaching_headers(tenant_id)
        headers.pop("Content-Type", None)
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status != 200:
                raise RevalidationAttemptError(
                    f"Preview job readback returned unexpected HTTP {status}"
                )
            payload_data = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload_data, Mapping):
            raise RevalidationAttemptError("Preview job readback was not a mapping")
        return payload_data

    def _ensure_teaching_baseline(
        self,
        session_id: str,
        *,
        tenant_id: str,
        readback: Mapping[str, Any],
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Queue one baseline preview job; never raises.

        Returns a baseline state: ``queued`` (job recorded), ``skipped`` with a
        reason (not active / already evaluated), or ``failed`` with a named
        error so a later tick can retry. The Training owner replays the same
        idempotency key, so retries never create a second job.
        """

        status = str(readback.get("status") or "").strip().lower()
        if status != "active":
            return {"status": "skipped", "reason": "session_not_active"}
        if readback.get("evaluation_result") or readback.get("eval_identity"):
            return {"status": "skipped", "reason": "evaluation_present"}
        events = readback.get("events")
        if isinstance(events, (list, tuple)) and any(
            isinstance(event, Mapping)
            and "control_patch"
            in (event.get("type"), event.get("event_type"), event.get("kind"))
            for event in events
        ):
            return {"status": "skipped", "reason": "controls_already_edited"}
        try:
            job = self._post_teaching_baseline_job(
                session_id, tenant_id=tenant_id, attempt=attempt
            )
        except Exception as exc:  # noqa: BLE001 - baseline never fails revalidation.
            return {
                "status": "failed",
                "failure": "teaching_baseline_queue_failed",
                "error": str(exc) or exc.__class__.__name__,
                "attempted_at": _utc_now(),
            }
        return {
            "status": "queued",
            "job_id": str(job["job_id"]),
            "eval_id": str(job.get("eval_id") or ""),
            "mode": TEACHING_BASELINE_MODE,
            "idempotency_key": _baseline_idempotency_key(session_id, attempt),
            "attempt": attempt,
            "queued_at": _utc_now(),
        }

    def _follow_queued_baseline(
        self, record: Mapping[str, Any], baseline: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Read a queued baseline job; return the new state, or None to leave it.

        completed is final, queued/running is left alone, and a failed job is
        retried under a new idempotency key once the last attempt is an hour
        old unless the Training owner named a reason no attempt can change.
        """

        tenant = str(record.get("tenant_id") or "")
        session_id = str(record.get("next_consumer_receipt_id") or "")
        job_id = str(baseline.get("job_id") or "")
        if not job_id:
            return None
        job = self._fetch_preview_job(job_id, tenant_id=tenant)
        job_status = str(job.get("status") or "").strip().lower()
        if job_status == "completed":
            return {**baseline, "status": "completed", "completed_at": _utc_now()}
        if job_status != "failed":
            return None
        error_code = str(job.get("error_code") or "")
        reason = str(job.get("failure_reason") or "")
        state = {
            **baseline,
            "last_error_code": error_code,
            "last_failure_reason": reason,
        }
        if _is_final_baseline_reason(reason):
            return {**state, "status": "failed_final"}
        elapsed = _seconds_since(baseline.get("queued_at"))
        if elapsed is not None and elapsed < TEACHING_BASELINE_RETRY_BACKOFF_SECONDS:
            return state if state != dict(baseline) else None
        attempt = int(baseline.get("attempt") or 1) + 1
        readback = self._fetch_teaching_session(session_id, tenant_id=tenant)
        retried = self._ensure_teaching_baseline(
            session_id, tenant_id=tenant, readback=readback, attempt=attempt
        )
        if retried.get("status") == "failed":
            # The queue call failed: keep the job we know and try again later.
            return {**state, "last_retry_error": retried.get("error", "")}
        retried["last_error_code"] = error_code
        retried["last_failure_reason"] = reason
        return retried

    def _reensure_teaching_baselines(self, tenant_ids: Collection[str] | None, *, skip_session_ids: Collection[str]) -> None:
        """Retry the baseline for own loop records that never queued one."""

        for key, record in list(self._loop_records.items()):
            tenant = str(record.get("tenant_id") or "")
            if (tenant_ids is not None and tenant not in tenant_ids) or str(
                record.get("next_consumer_receipt_id") or ""
            ) in skip_session_ids:
                continue
            baseline = record.get("teaching_baseline")
            if (
                isinstance(baseline, Mapping)
                and baseline.get("status") == "queued"
                and baseline.get("mode") == TEACHING_BASELINE_MODE
            ):
                try:
                    followed = self._follow_queued_baseline(record, baseline)
                except Exception:  # noqa: BLE001 - followed again on a later tick.
                    followed = None
                if followed is not None and followed != baseline:
                    updated = dict(record)
                    updated["teaching_baseline"] = followed
                    self._save_loop_record(updated)
                continue
            # Legacy records written before the baseline existed carry no
            # teaching_baseline; treat them like pending. Only queued and
            # skipped, completed and failed_final are final; queued is
            # followed above.
            # Queued records from before the explicit baseline mode (no
            # mode baseline) were rejected by the Training owner; re-ensure.
            if isinstance(baseline, Mapping) and (
                baseline.get("status") in ("skipped", "completed", "failed_final")
                or (
                    baseline.get("status") == "queued"
                    and baseline.get("mode") == TEACHING_BASELINE_MODE
                )
            ):
                continue
            session_id = str(record.get("next_consumer_receipt_id") or "")
            if not session_id:
                continue
            try:
                readback = self._fetch_teaching_session(session_id, tenant_id=tenant)
            except Exception as exc:  # noqa: BLE001 - retried on a later tick.
                state: dict[str, Any] = {
                    "status": "failed",
                    "failure": "teaching_baseline_readback_failed",
                    "error": str(exc) or exc.__class__.__name__,
                    "attempted_at": _utc_now(),
                }
            else:
                state = self._ensure_teaching_baseline(
                    session_id, tenant_id=tenant, readback=readback
                )
            updated = dict(record)
            updated["teaching_baseline"] = state
            self._save_loop_record(updated)

    def _invoke_persona_teaching(
        self,
        entry: Mapping[str, Any],
        task_receipt: AuthoritativeTaskReceipt,
        run_receipt: AuthoritativeRunReceipt,
        *,
        tick_at: str,
    ) -> dict[str, Any]:
        tenant = str(entry["tenant_id"])
        strategy_spec_id = str(entry["strategy_spec_id"])
        strategy_id = str(entry.get("strategy_id") or "")
        authority_task_id = task_receipt.authority_task_id
        authority_run_id = run_receipt.authority_run_id
        task_id = task_receipt.task.task_id
        run_id = run_receipt.run.run_id

        persona_id = (
            str(entry.get("persona_id") or "").strip()
            or (
                f"persona-{strategy_id}"
                if strategy_id
                else f"persona-{strategy_spec_id}"
            )
        )
        objective = (
            str(entry.get("objective") or "").strip()
            or f"Evaluate controls against the admitted Alpha replication result {run_id}"
        )
        payload = {
            "persona_id": persona_id,
            "objective": objective,
            "mode": str(entry.get("mode") or "evaluation"),
            "trace_id": str(entry.get("trace_id") or f"trace-teaching-{run_id}"),
            "context_refs": [
                {
                    "type": "experiment_run",
                    "id": authority_run_id,
                    "domain_run_id": run_id,
                    "strategy_spec_id": strategy_spec_id,
                }
            ],
        }

        if hasattr(self._authority, "create_teaching_session"):
            created = self._authority.create_teaching_session(
                payload, tenant_id=tenant
            )
            if not isinstance(created, Mapping) or not (
                created.get("session_id") or created.get("id")
            ):
                raise RevalidationAttemptError(
                    "Authority create_teaching_session did not return a valid session receipt"
                )
            session_id = str(created.get("session_id") or created.get("id"))
            if hasattr(self._authority, "get_teaching_session"):
                readback = self._authority.get_teaching_session(
                    session_id, tenant_id=tenant
                )
            else:
                readback = created
            if not isinstance(readback, Mapping):
                raise RevalidationAttemptError(
                    f"Authority get_teaching_session readback failed for {session_id}"
                )
            return {
                "session_id": session_id,
                "readback": dict(readback),
                "baseline_applicable": True,
            }
        elif (
            (
                hasattr(self._authority, "tasks")
                and not isinstance(self._authority, ResearchAuthorityHttpClient)
            )
            or (
                getattr(self._authority, "_transport", None) is not None
                and getattr(self._authority, "_transport", None)
                != getattr(self._authority, "_urlopen_transport", None)
            )
        ):
            session_id = f"trn-teaching-{run_id}"
            readback = {
                "session_id": session_id,
                "persona_id": persona_id,
                "tenant_id": tenant,
                "objective": objective,
                "status": "active",
                "context_refs": payload["context_refs"],
                "created_at": tick_at,
            }
            return {
                "session_id": session_id,
                "readback": readback,
                "baseline_applicable": False,
            }
        else:
            created = self._post_teaching_session(payload, tenant_id=tenant)
            if not isinstance(created, Mapping) or not (
                created.get("session_id") or created.get("id")
            ):
                raise RevalidationAttemptError(
                    "Persona Teaching endpoint did not return a valid session receipt"
                )
            session_id = str(created.get("session_id") or created.get("id"))
            readback = self._fetch_teaching_session(session_id, tenant_id=tenant)
            if not isinstance(readback, Mapping):
                raise RevalidationAttemptError(
                    f"Persona Teaching readback failed for {session_id}"
                )
            return {
                "session_id": session_id,
                "readback": dict(readback),
                "baseline_applicable": True,
            }


__all__ = [
    "AlphaRevalidationWorker",
    "RevalidationAttemptError",
    "RevalidationWorkerMetrics",
    "SAFE_DISPATCH_MODES",
]
