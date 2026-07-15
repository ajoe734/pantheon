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

from services.research.alpha_replication.controller_state import ControllerState, ControllerStateStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.revalidation_worker import AlphaRevalidationWorker

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
    ) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
        self.registry_url = registry_url or os.getenv("PANTHEON_REGISTRY_URL") or "http://registry:8087"
        self.interval_seconds = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS", str(interval_seconds)))
        self.max_ticks = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_MAX_TICKS", str(max_ticks)))
        
        self.data_dir = data_dir or Path(os.getenv("ALPHA_REPLICATION_DATA_DIR") or "data/alpha-replication")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_path = state_path or Path(os.getenv("ALPHA_REPLICATION_CONTROLLER_STATE_PATH") or self.data_dir / "controller_state.json")
        self.seed_store_path = seed_store_path or Path(os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or "data/source-ingest/distill_seeds.jsonl")


def _get_registry_entry(registry_url: str, registry_id: str) -> dict | None:
    url = f"{registry_url}/api/registry/strategy-specs/{registry_id}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to query registry for {registry_id}: {exc}") from exc


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
        # 1. Read desired state: list of approved strategy specs in the registry
        # We find them by reading the distillation seeds file if it exists, or querying known IDs
        source_ids = []
        if config.seed_store_path.exists():
            try:
                for line in config.seed_store_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload, dict) and "source_id" in payload:
                        source_ids.append(payload["source_id"])
            except Exception as exc:
                print(f"Warning: failed to read seeds store: {exc}", file=sys.stderr)
        
        # Always look up registry entries
        approved_specs = []
        for sid in source_ids:
            registry_id = f"reg-strategy-spec-{sid}"
            try:
                entry_view = _get_registry_entry(config.registry_url, registry_id)
                if entry_view:
                    entry = entry_view.get("entry", {})
                    # If the entry state is approved, it's a desired spec for replication
                    if entry.get("artifact_state") == "approved":
                        spec = entry.get("metadata", {}).get("strategy_spec")
                        if spec:
                            approved_specs.append(entry)
            except Exception as exc:
                print(f"Warning: registry lookup failed for {registry_id}: {exc}", file=sys.stderr)
        
        desired_meta = {"approved_spec_count": len(approved_specs), "strategy_ids": [e["strategy_id"] for e in approved_specs]}
        
        # 2. Enqueue into AlphaReplicationQueue
        queue = AlphaReplicationQueue(config.data_dir)
        enqueued_count = 0
        for entry in approved_specs:
            spec = entry.get("metadata", {}).get("strategy_spec")
            if spec:
                spec_payload = dict(spec)
                spec_payload.setdefault("lifecycle_state", "approved")
                spec_payload.setdefault("spec_version", entry.get("version", "1.0"))
                try:
                    res = queue.enqueue(spec_payload, enqueued_by="alpha-replication-controller")
                    if res is not None:
                        enqueued_count += 1
                except Exception as exc:
                    print(f"Warning: failed to enqueue spec {spec.get('strategy_id')}: {exc}", file=sys.stderr)
        
        # 3. Process the queue using AlphaRevalidationWorker
        worker = AlphaRevalidationWorker(
            queue,
            config.data_dir,
            worker_id="alpha-revalidation-worker",
        )
        tick_result = worker.run_once()
        
        reconcile_meta = {
            "enqueued_new": enqueued_count,
            "processed": tick_result.get("processed", 0),
            "created_run_ids": tick_result.get("created_run_ids", []),
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
                evidence_refs = []
                # Write runs.jsonl as evidence
                runs_path = config.data_dir / "alpha_revalidation_runs.jsonl"
                if runs_path.exists():
                    evidence_refs.append(str(runs_path))
                
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
