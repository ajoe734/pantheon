"""Supervised Strategy Distillation Loop Controller.

Reconciles normalized SourceRecords (desired state) to StrategySpec draft heads
registered in the Registry Service (actual state), using a durable JSONL job
queue and StrategySpecSeed store.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.controller_state import (
    ControllerState,
    ControllerStateStore,
    read_controller_state,
    utc_now,
)
from services.source_ingestion.distillation_worker import (
    DistillationJobStatus,
    DistillationWorker,
    _synthesize_evidence_item,
    make_distillation_worker,
)
from services.source_ingestion.pg_store import build_source_evidence_repository
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore

REPO_ROOT = Path(__file__).resolve().parents[2]


class DistillationControllerError(RuntimeError):
    """Base exception for the distillation controller."""
    def __init__(self, stage: str, message: str, **kwargs: Any) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = kwargs


@dataclass(frozen=True)
class DistillationControllerConfig:
    database_url: str
    registry_url: str
    interval_seconds: int
    max_ticks: int
    state_path: Path
    alive_path: Path | None
    job_queue_path: Path
    seed_store_path: Path
    evidence_store_path: Path
    source_dirs: list[Path]


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    val = os.getenv(name, "")
    if not val.strip():
        return default
    try:
        parsed = int(val)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer: {val!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"Environment variable {name} must be >= {minimum}: {parsed}")
    return parsed


def config_from_env() -> DistillationControllerConfig:
    database_url = os.getenv("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
    registry_url = os.getenv("PANTHEON_REGISTRY_URL") or os.getenv("REGISTRY_URL") or "http://registry:8087"
    interval = _env_int("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS", 60, minimum=1)
    max_ticks = _env_int("SOURCE_INGEST_CONTROLLER_MAX_TICKS", 0, minimum=0)
    
    state_path = Path(os.getenv("DISTILLATION_CONTROLLER_STATE_PATH") or "data/distillation/controller_state.json")
    alive_path_env = os.getenv("DISTILLATION_CONTROLLER_ALIVE_PATH") or "data/distillation/controller_alive"
    alive_path = Path(alive_path_env) if alive_path_env else None
    
    job_queue_path = Path(os.getenv("DISTILLATION_JOB_QUEUE_PATH") or "data/distillation/job_queue.jsonl")
    seed_store_path = Path(os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or "data/distillation/seeds.jsonl")
    evidence_store_path = Path(os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH") or "data/source-ingest/source_evidence.jsonl")
    
    source_dirs_env = os.getenv("STRATEGY_SPEC_DISTILLATION_SOURCE_DIRS")
    if source_dirs_env:
        source_dirs = [Path(p.strip()) for p in source_dirs_env.split(",") if p.strip()]
    else:
        source_dirs = [
            REPO_ROOT / "docs" / "research" / "notes",
            REPO_ROOT / "tests" / "e2e" / "fixtures",
            REPO_ROOT / "support" / "evidence",
            REPO_ROOT / "docs" / "deployment" / "evidence"
        ]
        
    return DistillationControllerConfig(
        database_url=database_url,
        registry_url=registry_url,
        interval_seconds=interval,
        max_ticks=max_ticks,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_store_path,
        source_dirs=source_dirs,
    )


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
        raise RuntimeError(f"Failed to query registry: {exc}") from exc


def _register_strategy_spec(registry_url: str, payload: dict) -> dict:
    url = f"{registry_url}/api/registry/strategy-specs"
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to register strategy spec: {exc}") from exc


def build_loop_writer(*, dsn: str, state: ControllerState) -> Any:
    if not dsn:
        raise DistillationControllerError("controller_store", "DATABASE_URL is required for durable controller truth")
    module = importlib.import_module("services.loop-control")
    return module.LoopControllerWriter(
        dsn,
        tenant_id=state.tenant_id,
        environment=state.environment,
        controller_id=state.controller_id,
        controller_name=state.controller_name,
        deployment_sha=str(state.deployment.get("git_sha") or "unknown"),
    )


def run_controller_tick(
    *,
    config: DistillationControllerConfig,
    state: ControllerState,
    store: ControllerStateStore,
    writer: Any,
) -> dict[str, Any]:
    state.record_tick_started()
    store.save(state)
    
    loop_id = os.getenv("PANTHEON_LOOP_ID") or "strategy_distillation"
    
    desired_meta: dict[str, Any] = {}
    reconcile_meta: dict[str, Any] = {}
    actual_meta: dict[str, Any] = {}
    
    try:
        # 1. Read desired state: normalized SourceRecords
        try:
            evidence_repo = build_source_evidence_repository(config.evidence_store_path)
            source_records = evidence_repo.list_source_records()
        except Exception as exc:
            raise DistillationControllerError("desired_read", f"Failed to read source evidence repo: {exc}")
            
        # Filter for eligible source records (exclude rejected ones)
        eligible_records = [
            s for s in source_records
            if s.status == SourceRecordStatus.NORMALIZED
        ]
        eligible_ids = [s.source_id for s in eligible_records]
        desired_meta = {"eligible_source_ids": sorted(eligible_ids)}
        
        # 2. Run distillation worker catch_up
        try:
            worker = make_distillation_worker(
                queue_path=config.job_queue_path,
                seed_store_path=config.seed_store_path,
                created_by="strategy-distillation-controller",
            )
            run_result = worker.catch_up(source_records, limit=100)
            reconcile_meta = {
                "processed": run_result.processed,
                "created": run_result.created,
                "refreshed": run_result.refreshed,
                "skipped": run_result.skipped,
                "failed": run_result.failed,
                "enqueued": run_result.enqueued,
            }
        except Exception as exc:
            raise DistillationControllerError("reconcile_worker", f"Failed to run distillation catch-up: {exc}")
            
        # 3. Synchronize seeds to registry
        synced_count = 0
        skipped_immutable = 0
        sync_failures = 0
        
        from services.research.strategy_spec.production_distillation import ProductionStrategySpecDistiller
        from services.research.strategy_spec.conversion import StrategySpecConversionService
        
        seed_store = StrategySpecSeedStore(config.seed_store_path)
        
        for source in eligible_records:
            source_id = source.source_id
            registry_id = f"reg-strategy-spec-{source_id}"
            
            # Fetch materialized seed from store
            seed = seed_store.get_by_bundle_idempotent(
                evidence_bundle_id=f"bundle:{source_id}", # builder stable bundle ID helper
                source_ids=[source_id],
            )
            if seed is None:
                # Try fallback lookup by list
                all_seeds = seed_store.list_all()
                for s in all_seeds:
                    if s.source_id == source_id:
                        seed = s
                        break
            
            if seed is None:
                continue
                
            # Check actual state in registry
            try:
                entry = _get_registry_entry(config.registry_url, registry_id)
            except Exception as exc:
                sync_failures += 1
                continue
                
            if entry is not None:
                artifact_state = entry.get("entry", {}).get("artifact_state") or entry.get("artifact_state")
                if artifact_state in ("approved", "retired"):
                    skipped_immutable += 1
                    continue  # Immutable protection!
                    
            # Distill registry payload
            registry_payload = None
            try:
                distiller = ProductionStrategySpecDistiller(source_dirs=config.source_dirs)
                distiller._resolve_markdown(source_id)
                registry_payload = distiller.distill_registry_payload(source_id)
            except Exception:
                # Fallback to conversion service
                try:
                    item = _synthesize_evidence_item(source)
                    conversion = StrategySpecConversionService().convert_seed(
                        seed=seed,
                        source_records=[source],
                        evidence_items=[item],
                        strategy_id=f"strat-{source_id}",
                        title=source.title,
                        version="1.0.0",
                    )
                    registry_payload = dict(conversion.registry_payload)
                except Exception:
                    sync_failures += 1
                    continue
                    
            if registry_payload is None:
                continue
                
            # Inject deterministic registry_id
            registry_payload["registry_id"] = registry_id
            
            # Perform write
            try:
                _register_strategy_spec(config.registry_url, registry_payload)
                synced_count += 1
            except Exception:
                sync_failures += 1
                
        actual_meta = {
            "synced_count": synced_count,
            "skipped_immutable_count": skipped_immutable,
            "sync_failures_count": sync_failures,
        }
        
        # 4. Save state and write success to LoopControllerWriter
        state.record_success(
            desired_state=desired_meta,
            reconcile=reconcile_meta,
            schedule={},
            actual_readback=actual_meta,
        )
        store.save(state)
        
        # Write success to DB
        asyncio.run(
            writer.record_success(
                loop_id=loop_id,
                truth_level=state.reconcile.get("truth_level") or "reconciled_live_proof",
                summary=(
                    f"Distilled and synchronized strategy specs. "
                    f"Processed: {reconcile_meta['processed']}. Synced: {synced_count}."
                ),
                backlog=reconcile_meta.get("enqueued", 0),
                payload={
                    "desired": desired_meta,
                    "reconcile": reconcile_meta,
                    "actual": actual_meta,
                }
            )
        )
        
        return {
            "status": "success",
            "desired": desired_meta,
            "reconcile": reconcile_meta,
            "actual": actual_meta,
        }
        
    except Exception as exc:
        stage = getattr(exc, "stage", "reconcile")
        reason = str(exc)
        state.record_failure(
            stage=stage,
            reason=reason,
            desired_state=desired_meta,
            reconcile=reconcile_meta,
            schedule={},
            actual_readback=actual_meta,
        )
        store.save(state)
        
        # Write failure to DB
        try:
            asyncio.run(
                writer.record_tick(
                    loop_id=loop_id,
                    truth_level="reconciled_live_proof",
                    payload={
                        "error_stage": stage,
                        "error_reason": reason,
                        "desired": desired_meta,
                        "reconcile": reconcile_meta,
                        "actual": actual_meta,
                    }
                )
            )
        except Exception:
            pass
            
        raise exc
    finally:
        if config.alive_path:
            _write_alive(config.alive_path)


def _write_alive(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(utc_now() + "\n", encoding="utf-8")
    except Exception:
        pass


def _new_state() -> ControllerState:
    git_sha = os.getenv("GIT_SHA") or os.getenv("PANTHEON_DEPLOYMENT_SHA") or "unknown"
    image = os.getenv("IMAGE_DIGEST") or ""
    built = os.getenv("BUILD_TIME") or ""
    
    return ControllerState(
        controller_id=f"distill-controller-{uuid.uuid4().hex[:8]}",
        controller_name=os.getenv("PANTHEON_CONTROLLER_NAME") or "strategy-distillation-controller",
        environment=os.getenv("PANTHEON_ENV") or "dev",
        tenant_id=os.getenv("PANTHEON_TENANT_ID") or "default",
        deployment={
            "git_sha": git_sha,
            "image_digest": image,
            "build_time": built,
        },
    )


def refresh_runtime_identity(state: ControllerState) -> ControllerState:
    state.controller_id = f"distill-controller-{uuid.uuid4().hex[:8]}"
    state.heartbeat_at = utc_now()
    return state


def main() -> int:
    config = config_from_env()
    store = ControllerStateStore(config.state_path)
    loaded_state = store.load()
    state = refresh_runtime_identity(loaded_state) if loaded_state is not None else _new_state()
    startup_missed = state.record_startup_missed(interval_seconds=config.interval_seconds)
    store.save(state)
    
    print(
        json.dumps(
            {
                "event": "startup",
                "controller_id": state.controller_id,
                "deployment": state.deployment,
                "startup_missed_ticks": startup_missed,
                "state_sequence_no": state.sequence_no,
            }
        ),
        flush=True,
    )
    
    writer = build_loop_writer(dsn=config.database_url, state=state)
    tick = 0
    last_tick_failed = False
    
    while True:
        tick += 1
        try:
            result = run_controller_tick(config=config, state=state, store=store, writer=writer)
            last_tick_failed = False
            print(json.dumps({"tick": tick, **result}), flush=True)
        except Exception as exc:
            last_tick_failed = True
            print(
                json.dumps(
                    {
                        "tick": tick,
                        "status": "failed",
                        "stage": getattr(exc, "stage", "controller"),
                        "error": f"{type(exc).__name__}: {exc}",
                        "state_sequence_no": state.sequence_no,
                    }
                ),
                flush=True,
            )
        if config.max_ticks and tick >= config.max_ticks:
            return 1 if last_tick_failed else 0
        time.sleep(config.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
