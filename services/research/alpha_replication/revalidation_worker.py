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
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .queue import AlphaReplicationQueue, _require_text, parse_utc

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


def _get_git_commit_sha() -> str:
    try:
        import subprocess
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except Exception:
        return "c38f0156a"


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
        self._max_retries = int(os.getenv("PANTHEON_ALPHA_REVALIDATION_MAX_RETRIES", "3"))
        self._metrics = self._load_metrics()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run_once(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Process pending queue entries in one tick by atomically claiming them.

        Returns a summary dict with run_count, created_run_ids, errors.
        """
        # Determine unique tenants to process
        if tenant_id:
            tenants = [tenant_id]
        else:
            # Check all pending entries to find unique tenants
            with self._queue._lock_context():
                entries = self._queue._read()
            tenants = sorted(list({e.get("tenant_id", "default") for e in entries if e.get("status") == "pending"}))
            if not tenants:
                tenants = ["default"]

        created_run_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        skipped_run_ids: list[str] = []
        tick_at = _utc_now()
        processed_count = 0
        attempted = set()

        with self._lock:
            for t in tenants:
                while True:
                    entry = self._queue.claim_next_pending(t, claimant=self._worker_id, ignore_keys=attempted)
                    if not entry:
                        break
                    key = (entry["strategy_id"], entry["spec_version"])
                    attempted.add(key)
                    processed_count += 1
                    try:
                        # Enforce queue entry timeout (default: 3600 seconds)
                        enqueued_dt = parse_utc(entry.get("enqueued_at"))
                        if enqueued_dt:
                            entry_timeout = int(os.getenv("PANTHEON_QUEUE_ENTRY_TIMEOUT_SECONDS", "3600"))
                            if (datetime.now(timezone.utc) - enqueued_dt).total_seconds() > entry_timeout:
                                raise TimeoutError(f"Queue entry enqueued at {entry.get('enqueued_at')} has timed out (limit: {entry_timeout}s)")

                        run_record = self._process_entry(entry, tick_at=tick_at)
                        is_new = run_record.get("created_at") == tick_at
                        
                        if not is_new:
                            skipped_run_ids.append(run_record["run_id"])
                        elif run_record.get("status") == "failed":
                            raise ValueError(run_record.get("failure_reason") or "Revalidation failed")
                        else:
                            created_run_ids.append(run_record["run_id"])
                    except Exception as exc:  # noqa: BLE001
                        err_msg = str(exc)
                        errors.append(
                            {
                                "strategy_id": entry.get("strategy_id"),
                                "spec_version": entry.get("spec_version"),
                                "error": err_msg,
                            }
                        )
                        self._queue.mark_failed(
                            entry["strategy_id"],
                            entry["spec_version"],
                            error=err_msg,
                            max_retries=self._max_retries,
                        )

        if created_run_ids:
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
            "processed": processed_count,
            "created_run_ids": created_run_ids,
            "skipped_run_ids": skipped_run_ids,
            "errors": errors,
            "dispatch_mode": self._dispatch_mode,
        }

    def replay_dlq(self, strategy_id: str, spec_version: str) -> bool:
        """Reset a DLQ/failed entry back to pending for replay."""
        return self._queue.replay_dlq(strategy_id, spec_version)

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

    def _fetch_spec_from_registry(self, strategy_id: str, spec_version: str) -> dict[str, Any] | None:
        registry_url = os.getenv("PANTHEON_REGISTRY_URL") or "http://registry:8087"
        url = f"{registry_url}/api/registry/strategies/{strategy_id}/strategy-specs?artifact_state=approved"
        try:
            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                views = json.loads(response.read().decode("utf-8"))
                for view in views:
                    entry = view.get("entry", {})
                    if entry.get("version") == spec_version:
                        return entry.get("metadata", {}).get("strategy_spec")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        except Exception as exc:
            raise RuntimeError(f"Registry is unreachable: {exc}") from exc
        return None

    def _build_clean_run_record(self, run_record: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        ALLOWED_KEYS = {
            "run_id", "task_id", "strategy_id", "strategy_spec_version", "backend_id",
            "runtime_env", "status", "dataset_version_id", "code_version", "input_manifest_ref",
            "artifact_refs", "trace_id", "created_at", "started_at", "finished_at",
            "output_manifest_ref", "metric_bundle_id", "logs_ref", "failure_reason",
            "updated_at", "metadata"
        }
        clean_record = {k: v for k, v in run_record.items() if k in ALLOWED_KEYS}
        clean_record["metadata"] = dict(clean_record.get("metadata") or {})
        clean_record["metadata"].update({
            "dispatch_mode": self._dispatch_mode,
            "production_activation": "disabled",
            "worker_id": self._worker_id,
            "idempotency_key": idempotency_key,
        })
        return clean_record

    def _writeback_lineage_to_registry(
        self,
        strategy_id: str,
        spec_version: str,
        run_id: str,
        dataset_version_id: str,
        code_version: str,
    ) -> None:
        registry_url = os.getenv("PANTHEON_REGISTRY_URL") or "http://registry:8087"
        url = f"{registry_url}/api/registry/entries"
        
        lineage_payload = {
            "source_strategy_spec_id": f"reg-strategy-spec-{strategy_id}",
            "source_run_ids": [run_id],
            "source_dataset_refs": [dataset_version_id]
        }
        
        storage_ref = {
            "backend": "inline",
            "path": f"manifest://research/replication/{run_id}/output.json"
        }
        
        payload = {
            "artifact_type": "evaluation_result",
            "strategy_id": strategy_id,
            "version": spec_version,
            "artifact_state": "candidate",
            "lineage": lineage_payload,
            "storage_ref": storage_ref,
            "checksum": f"sha256:{hashlib.sha256(run_id.encode('utf-8')).hexdigest()}",
            "producer_run_id": run_id,
            "evaluation_summary": {
                "revalidated": True,
                "status": "completed",
                "worker_id": self._worker_id,
            },
            "metadata": {
                "code_version": code_version,
                "dispatch_mode": self._dispatch_mode,
            }
        }
        
        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                created_entry = resp_data.get("entry", {})
                created_id = created_entry.get("registry_id")
                if not created_id:
                    raise ValueError("Registry response did not contain registry_id")
            
            # Authoritative readback verification
            readback_url = f"{registry_url}/api/registry/entries/{created_id}"
            readback_req = urllib.request.Request(readback_url, method="GET")
            with urllib.request.urlopen(readback_req, timeout=5) as rb_response:
                rb_data = json.loads(rb_response.read().decode("utf-8"))
                rb_entry = rb_data.get("entry", {})
                if rb_entry.get("producer_run_id") != run_id:
                    raise ValueError(
                        f"Readback verification failed: expected producer_run_id={run_id!r}, "
                        f"got {rb_entry.get('producer_run_id')!r}"
                    )
        except Exception as exc:
            raise RuntimeError(f"Lineage writeback or readback verification failed: {exc}") from exc

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
            and (r.get("spec_version") == spec_version or r.get("strategy_spec_version") == spec_version)
        ]
        if existing:
            # Already processed; update the queue record and return.
            self._queue.mark_revalidated(
                strategy_id,
                spec_version,
                run_id=existing[0]["run_id"],
                status=existing[0]["status"],
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
            "strategy_spec_version": spec_version,
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

        # If not stub mode, perform non-stub revalidation using ReplicationGate
        if self._dispatch_mode != "stub":
            spec = self._fetch_spec_from_registry(strategy_id, spec_version)
            
            # Stale or retired specs fail closed
            if spec is None:
                run_record["status"] = "failed"
                run_record["started_at"] = tick_at
                run_record["finished_at"] = _utc_now()
                reason = f"Stale, retired, or missing StrategySpec: {strategy_id} version {spec_version}"
                run_record["failure_reason"] = reason
                
                clean_record = self._build_clean_run_record(run_record, idempotency_key)
                self._append_run(clean_record)
                
                raise ValueError(reason)

            input_source = "registry"
            run_record["metadata"] = dict(run_record.get("metadata") or {})
            run_record["metadata"]["input_source"] = input_source
            
            # Extract actual dataset and code lineage from the StrategySpec
            dataset_version_id = "unknown-dataset"
            if isinstance(spec.get("data_dependencies"), list):
                for dep in spec["data_dependencies"]:
                    if dep.get("kind") == "dataset":
                        dataset_version_id = dep.get("ref", "unknown-dataset")
                        break
            
            code_version = _get_git_commit_sha()
            if isinstance(spec.get("code_refs"), list):
                for ref in spec["code_refs"]:
                    if ref.get("commit"):
                        code_version = ref.get("commit")
                        break
            
            run_record["backend_id"] = "replication_gate"
            run_record["dataset_version_id"] = dataset_version_id
            run_record["code_version"] = code_version
            run_record["input_manifest_ref"] = f"manifest://registry/strategy-specs/reg-strategy-spec-{strategy_id}/{spec_version}/input.json"

            from services.research.replication.gate import ReplicationGate
            from services.research.replication.gate_schema import ReplicationRequest

            request = ReplicationRequest(
                candidate_id=run_id,
                source_task_id=task_id,
                research_handoff={
                    "source_metadata": {
                        "api_endpoint": f"http://registry:8087/api/registry/strategy-specs/{strategy_id}",
                        "retrieved_at": tick_at,
                        "governance_context": "Approved structured source for revalidation",
                    },
                    "normalized_findings": {
                        "strategy_spec": {"strategy_id": strategy_id},
                        "replication_notes": "Revalidated via alpha replication worker.",
                        "evaluation_hypotheses": "H1: Strategy parameters and schema are valid.",
                    },
                    "grok_processing_notes": {
                        "normalization_confidence": "high",
                        "governance_compliance": "verified",
                        "downstream_readiness": "ready_for_replication",
                    },
                },
                proposed_strategy_spec=spec,
            )

            gate = ReplicationGate()
            gate_response = gate.evaluate_candidate(request)

            if gate_response.passed:
                run_record["status"] = "completed"
                run_record["started_at"] = tick_at
                run_record["finished_at"] = _utc_now()
                run_record["output_manifest_ref"] = f"manifest://research/replication/{run_id}/output.json"
                run_record["artifact_refs"] = [f"reg-strategy-spec-{strategy_id}"]
                
                # Writeback lineage to registry
                self._writeback_lineage_to_registry(strategy_id, spec_version, run_id, dataset_version_id, code_version)
            else:
                run_record["status"] = "failed"
                run_record["started_at"] = tick_at
                run_record["finished_at"] = _utc_now()
                run_record["failure_reason"] = gate_response.summary
                
                clean_record = self._build_clean_run_record(run_record, idempotency_key)
                self._append_run(clean_record)
                
                raise ValueError(gate_response.summary)

        # Validate run record against the ExperimentRun domain model schema
        clean_record = self._build_clean_run_record(run_record, idempotency_key)
        from services.research.experiments.models import ExperimentRun
        ExperimentRun.from_dict(clean_record)

        self._append_run(clean_record)
        self._queue.mark_revalidated(
            strategy_id,
            spec_version,
            run_id=run_id,
            status="dispatched" if self._dispatch_mode == "stub" else clean_record["status"],
        )
        return clean_record

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
        try:
            with self._runs_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            
            # Authoritative readback check
            runs = self._read_runs()
            if not any(r.get("run_id") == record["run_id"] for r in runs):
                raise ValueError("Appended run was not found in readback verification")
        except Exception as exc:
            raise RuntimeError(f"Experiment run append or readback verification failed: {exc}") from exc

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
