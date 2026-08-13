"""Supervised Alpha Replication and Revalidation Loop Controller.

Reconciles approved StrategySpecs (desired state) to AlphaReplicationQueue (actual state)
and executes the AlphaRevalidationWorker to produce non-stub ExperimentRun records.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.controller_state import ControllerState, ControllerStateStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.revalidation_worker import AlphaRevalidationWorker
from services.research.experiment_orchestrator.authority import ExperimentAuthority

REPO_ROOT = Path(__file__).resolve().parents[3]


class ReplicationControllerConfig:
    def __init__(
        self,
        database_url: str | None = None,
        registry_url: str | None = None,
        interval_seconds: int = 60,
        max_ticks: int = 0,
        state_path: Path | None = None,
        data_dir: Path | None = None,
        seed_store_path: Path | None = None,
        authority: ExperimentAuthority | None = None,
        admission_store: ReplicationAdmissionStore | None = None,
    ) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
        self.registry_url = registry_url or os.getenv("PANTHEON_REGISTRY_URL") or "http://registry:8087"
        self.interval_seconds = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS", str(interval_seconds)))
        self.max_ticks = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_MAX_TICKS", str(max_ticks)))
        
        self.data_dir = data_dir or Path(os.getenv("ALPHA_REPLICATION_DATA_DIR") or "data/alpha-replication")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_path = state_path or Path(os.getenv("ALPHA_REPLICATION_CONTROLLER_STATE_PATH") or self.data_dir / "controller_state.json")
        self.seed_store_path = seed_store_path or Path(os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or "data/source-ingest/distill_seeds.jsonl")
        self.authority = authority
        self.admission_store = admission_store or ReplicationAdmissionStore(self.data_dir)


def _get_approved_specs_for_strategy(registry_url: str, strategy_id: str) -> list[dict]:
    url = f"{registry_url}/api/registry/strategies/{strategy_id}/strategy-specs?artifact_state=approved"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            views = json.loads(response.read().decode("utf-8"))
            return [view["entry"] for view in views if "entry" in view]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to query registry for strategy {strategy_id}: {exc}") from exc


def _queue_payload_from_registry_entry(
    entry: Mapping[str, Any],
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Normalize one approved immutable registry view into the queue contract."""

    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("approved StrategySpec registry metadata is required")
    spec = metadata.get("strategy_spec")
    if not isinstance(spec, Mapping):
        raise ValueError("approved StrategySpec inline payload is required")
    bound_tenant = str(metadata.get("tenant_id") or spec.get("tenant_id") or "").strip()
    if not bound_tenant:
        raise ValueError("approved StrategySpec tenant_id is required")
    if bound_tenant != tenant_id:
        raise ValueError(
            f"approved StrategySpec tenant_id={bound_tenant!r} does not match "
            f"controller tenant_id={tenant_id!r}"
        )
    if entry.get("artifact_type") != "strategy_spec":
        raise ValueError("registry entry is not a StrategySpec")
    if entry.get("artifact_state") != "approved":
        raise ValueError("only approved StrategySpec registry entries may enqueue")
    strategy_id = str(entry.get("strategy_id") or "").strip()
    version = str(entry.get("version") or "").strip()
    if str(spec.get("strategy_id") or "").strip() != strategy_id:
        raise ValueError("embedded StrategySpec strategy_id does not match registry entry")
    return {
        "tenant_id": bound_tenant,
        "strategy_spec_id": entry.get("registry_id"),
        "strategy_id": strategy_id,
        "spec_version": version,
        "artifact_state": entry.get("artifact_state"),
        "checksum": entry.get("checksum"),
        "approval_decision_id": entry.get("approval_decision_id"),
        "approver": entry.get("approver"),
        "approved_at": entry.get("approved_at"),
    }


def build_loop_writer(*, dsn: str, state: ControllerState) -> Any:
    if not dsn:
        return None
    try:
        module = importlib.import_module("services.loop-control")
        return module.LoopControllerWriter(
            dsn,
            tenant_id=state.tenant_id,
            environment=state.environment,
            controller_id=state.controller_id,
            controller_name=state.controller_name,
            deployment_sha=str(state.deployment.get("git_sha") or "unknown"),
        )
    except ModuleNotFoundError:
        return None


def run_controller_tick(
    *,
    config: ReplicationControllerConfig,
    state: ControllerState,
    store: ControllerStateStore,
    writer: Any,
) -> dict[str, Any]:
    state.record_tick_started()
    store.save(state)
    
    loop_id = os.getenv("PANTHEON_LOOP_ID") or "alpha_replication"
    
    desired_meta: dict[str, Any] = {}
    reconcile_meta: dict[str, Any] = {}
    actual_meta: dict[str, Any] = {}
    
    try:
        # 1. Read admissions from ReplicationAdmissionStore for current tenant
        admissions = config.admission_store.list_admissions(tenant_id=state.tenant_id)

        # Legacy seed file read: seed store is retained for lineage tracking,
        # but seed source_ids do NOT directly grant admission without an approved ReplicationAdmission.
        seed_lineage_sources: list[str] = []
        if config.seed_store_path.exists():
            try:
                for line in config.seed_store_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload, dict) and "source_id" in payload:
                        source_id = str(payload["source_id"] or "").strip()
                        if source_id and source_id not in seed_lineage_sources:
                            seed_lineage_sources.append(source_id)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to read StrategySpec discovery trigger store for lineage: {exc}"
                ) from exc
        
        # Look up registry entries ONLY for admitted strategy specs
        approved_specs = []
        for adm in admissions:
            strategy_id = adm["strategy_id"]
            strategy_spec_id = adm["strategy_spec_id"]
            try:
                entries = _get_approved_specs_for_strategy(config.registry_url, strategy_id)
                # Filter registry entries to the exact admitted strategy_spec_id
                matched_entries = [e for e in entries if e.get("registry_id") == strategy_spec_id]
                approved_specs.extend(matched_entries)
            except Exception as exc:
                raise RuntimeError(f"Registry lookup failed for strategy {strategy_id}: {exc}") from exc
        
        desired_meta = {
            "approved_spec_count": len(approved_specs),
            "strategy_spec_ids": [entry.get("registry_id") for entry in approved_specs],
            "tenant_id": state.tenant_id,
            "seed_lineage_count": len(seed_lineage_sources),
        }
        
        # 2. Enqueue into AlphaReplicationQueue
        queue = AlphaReplicationQueue(config.data_dir)
        enqueued_count = 0
        for entry in approved_specs:
            try:
                spec_payload = _queue_payload_from_registry_entry(
                    entry,
                    tenant_id=state.tenant_id,
                )
                result = queue.enqueue(
                    spec_payload,
                    enqueued_by="alpha-replication-controller",
                )
                if result is not None:
                    enqueued_count += 1
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to enqueue StrategySpec {entry.get('registry_id')}: {exc}"
                ) from exc
        
        # 3. Process the queue using AlphaRevalidationWorker
        worker = AlphaRevalidationWorker(
            queue,
            config.data_dir,
            worker_id="alpha-revalidation-worker",
            authority=config.authority,
            registry_url=config.registry_url,
        )
        tick_result = worker.run_once(tenant_id=state.tenant_id)
        
        reconcile_meta = {
            "enqueued_new": enqueued_count,
            "processed": tick_result.get("processed", 0),
            "created_run_ids": tick_result.get("created_run_ids", []),
            "created_authority_task_ids": tick_result.get(
                "created_authority_task_ids",
                [],
            ),
            "created_authority_run_ids": tick_result.get(
                "created_authority_run_ids",
                [],
            ),
            "created_experiment_task_ids": tick_result.get(
                "created_experiment_task_ids",
                [],
            ),
            "created_experiment_run_ids": tick_result.get(
                "created_experiment_run_ids",
                [],
            ),
            "authority_receipts": tick_result.get("authority_receipts", []),
            "errors": tick_result.get("errors", []),
            "dispatch_mode": tick_result.get("dispatch_mode"),
        }
        
        # 4. Observe actual state: get metrics
        q_metrics = queue.get_metrics()
        w_metrics = worker.get_metrics()
        actual_meta = {
            "queue_total": q_metrics.get("total", 0),
            "queue_pending": q_metrics.get("pending", 0),
            "queue_revalidated": q_metrics.get("revalidated", 0),
            "queue_claimed": q_metrics.get("claimed", 0),
            "queue_dlq": q_metrics.get("dlq", 0),
            "worker_runs": w_metrics.get("run_count", 0),
            "worker_errors": w_metrics.get("error_count", 0),
        }
        
        state.record_success(
            desired_state=desired_meta,
            reconcile=reconcile_meta,
            schedule={},
            actual_readback=actual_meta,
        )
        store.save(state)
        
        # Write to loop control db
        if writer:
            try:
                import asyncio
                evidence_refs: list[str] = []
                for receipt in tick_result.get("authority_receipts", []):
                    task_ref = (
                        "research-authority://experiment-tasks/"
                        f"{receipt['authority_task_id']}"
                    )
                    run_ref = (
                        "research-authority://experiment-runs/"
                        f"{receipt['authority_run_id']}"
                    )
                    for evidence_ref in (task_ref, run_ref):
                        if evidence_ref not in evidence_refs:
                            evidence_refs.append(evidence_ref)
                
                asyncio.run(writer.record_success(
                    loop_id=loop_id,
                    summary=f"Processed {tick_result.get('processed')} queue entries",
                    backlog=q_metrics.get("pending", 0),
                    evidence_refs=evidence_refs,
                    payload={
                        "queue": q_metrics,
                        "worker": w_metrics,
                    }
                ))
            except Exception as exc:
                print(f"Warning: failed to write to LoopControllerWriter: {exc}", file=sys.stderr)
                
    except Exception as exc:
        state.record_failure(stage="tick", reason=str(exc))
        store.save(state)
        if writer:
            try:
                import asyncio
                asyncio.run(writer.record_failure(
                    loop_id=loop_id,
                    reason=str(exc),
                ))
            except Exception:
                pass
        raise
        
    return state.to_dict()


def main() -> int:
    config = ReplicationControllerConfig()
    
    # Initialize state
    store = ControllerStateStore(config.state_path)
    state = store.load()
    if state is None:
        git_sha = os.getenv("GIT_SHA") or "unknown"
        state = ControllerState(
            controller_id=os.getenv("PANTHEON_CONTROLLER_ID") or f"alpha-replication-controller-{os.getpid()}",
            controller_name=os.getenv("PANTHEON_CONTROLLER_NAME") or "alpha-replication-controller",
            environment=os.getenv("PANTHEON_ENV") or "dev",
            tenant_id=os.getenv("PANTHEON_TENANT_ID") or "default",
            deployment={"git_sha": git_sha},
        )
        store.save(state)
        
    writer = None
    if config.database_url:
        writer = build_loop_writer(dsn=config.database_url, state=state)
        
    print(f"Starting Alpha Replication Controller loop, interval={config.interval_seconds}s")
    
    tick_count = 0
    while True:
        try:
            run_controller_tick(config=config, state=state, store=store, writer=writer)
        except Exception as exc:
            print(f"Error in controller tick: {exc}", file=sys.stderr)
            
        tick_count += 1
        if config.max_ticks > 0 and tick_count >= config.max_ticks:
            print("Max ticks reached. Exiting.")
            break
            
        time.sleep(config.interval_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
