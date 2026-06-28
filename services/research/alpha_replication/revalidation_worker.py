"""Scheduled alpha revalidation worker.

Picks up approved StrategySpec entries from the AlphaReplicationQueue and
produces ExperimentTask + stub ExperimentRun records. Production adapters
remain fail-closed; all dispatches default to dispatch_mode=stub until
explicitly activated via PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE.

Liveness metrics (last_success_at, last_failure_at, run_count, error_count)
are written to a JSON file in the data directory so operators can observe
worker health without polling the queue directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .queue import AlphaReplicationQueue, _require_text

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

SAFE_DISPATCH_MODES = frozenset({"stub", "handoff_only", "manual"})
DEFAULT_DISPATCH_MODE = "stub"
DEFAULT_BACKEND_SELECTION_POLICY_ID = "alpha-revalidation-stub-policy"
DEFAULT_DATASET_VERSION_ID = "pending"
DEFAULT_CODE_VERSION = "pending"
DEFAULT_RUNTIME_ENV = "research"
DEFAULT_TASK_TYPE = "rapid_eval"
DEFAULT_BACKEND_ID = "stub"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_hash(*parts: str) -> str:
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ #
# Metrics dataclass                                                    #
# ------------------------------------------------------------------ #


@dataclass
class RevalidationWorkerMetrics:
    run_count: int = 0
    error_count: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None
    last_run_strategy_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


# ------------------------------------------------------------------ #
# Worker                                                               #
# ------------------------------------------------------------------ #


class AlphaRevalidationWorker:
    """Scheduled worker that drains the alpha replication queue into stub ExperimentRun records.

    Instantiate once; call ``run_once()`` from a scheduler or cron-style loop.
    The worker is thread-safe and idempotent: calling ``run_once()`` concurrently
    is safe, duplicate ticks do not create duplicate records.
    """

    def __init__(
        self,
        queue: AlphaReplicationQueue,
        data_dir: str | Path,
        *,
        dispatch_mode: str | None = None,
        worker_id: str = "alpha-revalidation-worker",
    ) -> None:
        self._queue = queue
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._runs_path = self._data_dir / "alpha_revalidation_runs.jsonl"
        self._metrics_path = self._data_dir / "alpha_revalidation_metrics.json"
        self._worker_id = _require_text(worker_id, "worker_id")
        self._lock = threading.Lock()

        raw_mode = (
            dispatch_mode
            or os.getenv("PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE", DEFAULT_DISPATCH_MODE)
        ).strip().lower()
        if raw_mode not in SAFE_DISPATCH_MODES:
            raise ValueError(
                f"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE={raw_mode!r} is not a safe dispatch "
                f"mode; production adapters are fail-closed. Allowed: {sorted(SAFE_DISPATCH_MODES)}"
            )
        self._dispatch_mode = raw_mode
        self._metrics = self._load_metrics()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run_once(self) -> dict[str, Any]:
        """Process all pending queue entries in one tick.

        Returns a summary dict with run_count, created_run_ids, errors.
        Duplicate ticks for already-revalidated specs are silently skipped.
        """
        pending = self._queue.list_pending()
        created_run_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        tick_at = _utc_now()

        with self._lock:
            for entry in pending:
                try:
                    run_record = self._process_entry(entry, tick_at=tick_at)
                    created_run_ids.append(run_record["run_id"])
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        {
                            "strategy_id": entry.get("strategy_id"),
                            "spec_version": entry.get("spec_version"),
                            "error": str(exc),
                        }
                    )

        if created_run_ids and not errors:
            self._metrics.run_count += len(created_run_ids)
            self._metrics.last_success_at = _utc_now()
            self._metrics.last_run_strategy_ids = [
                r.split(":")[0] for r in created_run_ids
            ]
        if errors:
            self._metrics.error_count += len(errors)
            self._metrics.last_failure_at = _utc_now()
            self._metrics.last_failure_reason = errors[0]["error"]

        self._save_metrics()

        return {
            "tick_at": tick_at,
            "processed": len(pending),
            "created_run_ids": created_run_ids,
            "errors": errors,
            "dispatch_mode": self._dispatch_mode,
        }

    def get_metrics(self) -> dict[str, Any]:
        """Return observable worker health metrics."""
        return self._metrics.to_dict()

    def list_runs(
        self,
        strategy_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted ExperimentRun stub records."""
        runs = self._read_runs()
        if strategy_id:
            runs = [r for r in runs if r.get("strategy_id") == strategy_id]
        return runs

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _process_entry(
        self, entry: dict[str, Any], *, tick_at: str
    ) -> dict[str, Any]:
        strategy_id = entry["strategy_id"]
        spec_version = entry["spec_version"]

        # Idempotency: skip if this (strategy_id, spec_version) already has a run.
        existing = [
            r
            for r in self._read_runs()
            if r.get("strategy_id") == strategy_id
            and r.get("spec_version") == spec_version
        ]
        if existing:
            # Already processed; update the queue record and return.
            self._queue.mark_revalidated(
                strategy_id,
                spec_version,
                run_id=existing[0]["run_id"],
                status="skipped_already_exists",
            )
            return existing[0]

        run_id = f"arvrun-{strategy_id}-{spec_version}-{_short_hash(strategy_id, spec_version, tick_at)}"
        task_id = f"arvtask-{strategy_id}-{spec_version}-{_short_hash(strategy_id, spec_version)}"
        idempotency_key = f"alpha-reval-{strategy_id}-{spec_version}"
        trace_id = f"trace-alpha-reval-{_short_hash(strategy_id, spec_version)}"

        run_record: dict[str, Any] = {
            "run_id": run_id,
            "task_id": task_id,
            "strategy_id": strategy_id,
            "spec_version": spec_version,
            "backend_id": DEFAULT_BACKEND_ID,
            "runtime_env": DEFAULT_RUNTIME_ENV,
            "status": "pending",
            "dataset_version_id": DEFAULT_DATASET_VERSION_ID,
            "code_version": DEFAULT_CODE_VERSION,
            "input_manifest_ref": f"alpha-replication://{strategy_id}/{spec_version}",
            "artifact_refs": [],
            "trace_id": trace_id,
            "dispatch_mode": self._dispatch_mode,
            "production_activation": "disabled",
            "idempotency_key": idempotency_key,
            "worker_id": self._worker_id,
            "created_at": tick_at,
        }

        self._append_run(run_record)
        self._queue.mark_revalidated(
            strategy_id,
            spec_version,
            run_id=run_id,
            status="dispatched",
        )
        return run_record

    def _read_runs(self) -> list[dict[str, Any]]:
        if not self._runs_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self._runs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def _append_run(self, record: dict[str, Any]) -> None:
        with self._runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            fh.write("\n")

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
                last_run_strategy_ids=list(data.get("last_run_strategy_ids") or []),
            )
        except Exception:  # noqa: BLE001
            return RevalidationWorkerMetrics()

    def _save_metrics(self) -> None:
        self._metrics_path.write_text(
            json.dumps(self._metrics.to_dict(), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )


__all__ = ["AlphaRevalidationWorker", "RevalidationWorkerMetrics", "SAFE_DISPATCH_MODES"]
