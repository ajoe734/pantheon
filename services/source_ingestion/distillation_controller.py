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
    DistillationJob,
    DistillationJobQueue,
    RegistrySyncRequest,
    RegistrySyncResult,
    make_distillation_worker,
    source_version_digest,
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
    lease_seconds: int = 30
    retry_base_seconds: int = 5
    max_attempts: int = 3


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
    
    job_queue_path = Path(
        os.getenv("DISTILLATION_JOB_QUEUE_PATH")
        or "data/distillation/job_queue.sqlite3"
    )
    seed_store_path = Path(os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or "data/distillation/seeds.jsonl")
    evidence_store_path = Path(os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH") or "data/source-ingest/source_evidence.jsonl")
    lease_seconds = _env_int("DISTILLATION_JOB_LEASE_SECONDS", 30, minimum=1)
    retry_base_seconds = _env_int(
        "DISTILLATION_JOB_RETRY_BASE_SECONDS",
        5,
        minimum=0,
    )
    max_attempts = _env_int("DISTILLATION_JOB_MAX_ATTEMPTS", 3, minimum=1)
    
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
        lease_seconds=lease_seconds,
        retry_base_seconds=retry_base_seconds,
        max_attempts=max_attempts,
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


def _register_strategy_spec_if_absent(registry_url: str, payload: dict) -> dict:
    """Create a StrategySpec id atomically or return its existing Registry entry."""
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


def _registry_entry_payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = entry.get("entry")
    return nested if isinstance(nested, Mapping) else entry


def _registry_artifact_state(entry: Mapping[str, Any]) -> str:
    payload = _registry_entry_payload(entry)
    return str(payload.get("artifact_state") or "").strip().lower()


def _registry_entry_matches_expected(
    entry: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    """Validate that a Registry readback belongs to this exact source version."""

    payload = _registry_entry_payload(entry)
    compared_fields = (
        "registry_id",
        "strategy_id",
        "version",
        "checksum",
        "producer_run_id",
        "evaluation_summary",
        "rollback_target",
    )
    for field in compared_fields:
        if payload.get(field) != expected.get(field):
            return False
    if str(payload.get("artifact_type") or "") != "strategy_spec":
        return False
    if _drop_nulls(payload.get("lineage")) != _drop_nulls(expected.get("lineage")):
        return False
    if _drop_nulls(payload.get("storage_ref")) != _drop_nulls(expected.get("storage_ref")):
        return False
    payload_metadata = payload.get("metadata") or {}
    expected_metadata = expected.get("metadata") or {}
    if not isinstance(payload_metadata, Mapping) or not isinstance(
        expected_metadata,
        Mapping,
    ):
        return False
    for key, value in expected_metadata.items():
        if payload_metadata.get(key) != value:
            return False
    if payload_metadata.get("strategy_spec") != expected.get("strategy_spec"):
        return False
    expected_distillation = (expected.get("metadata") or {}).get("distillation")
    payload_distillation = (payload.get("metadata") or {}).get("distillation")
    if not isinstance(expected_distillation, Mapping) or not isinstance(
        payload_distillation,
        Mapping,
    ):
        return False
    for field in (
        "source_id",
        "source_digest",
        "source_event_version",
        "distillation_job_id",
    ):
        if payload_distillation.get(field) != expected_distillation.get(field):
            return False
    return True


def _drop_nulls(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _drop_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_nulls(item) for item in value]
    return value


def _versioned_registry_id(source: SourceRecord, job: DistillationJob) -> str:
    digest = (
        job.source_digest.removeprefix("sha256:")
        if job.source_digest
        else hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()
    )
    return f"reg-strategy-spec-{source.source_id}-{digest[:12]}"


def _build_registry_payload(
    *,
    request: RegistrySyncRequest,
    registry_id: str,
) -> dict[str, Any]:
    from services.research.strategy_spec.conversion import (
        StrategySpecConversionService,
    )

    source = request.source
    job = request.job
    committed_digest = source_version_digest(source)
    if job.source_digest and committed_digest != job.source_digest:
        raise RuntimeError(
            f"Registry sync source digest mismatch for {source.source_id}: "
            f"job={job.source_digest}, committed={committed_digest}"
        )
    if request.version_key != (job.source_digest or None):
        raise RuntimeError(
            f"Registry sync version key mismatch for {source.source_id}: "
            f"request={request.version_key}, job={job.source_digest or None}"
        )

    seed_source_ids = tuple(request.seed.source_ids)
    seed_item_ids = tuple(request.seed.evidence_item_ids)
    bundle_source_ids = tuple(request.evidence_bundle.source_ids)
    bundle_item_ids = tuple(request.evidence_bundle.evidence_item_ids)
    if (
        request.seed.evidence_bundle_id
        != request.evidence_bundle.evidence_bundle_id
        or seed_source_ids != bundle_source_ids
        or seed_item_ids != bundle_item_ids
        or request.evidence_item.source_id != source.source_id
        or request.evidence_item.evidence_item_id not in seed_item_ids
        or source.source_id not in seed_source_ids
    ):
        raise RuntimeError(
            f"Registry sync request lineage mismatch for source {source.source_id}"
        )

    # Registry delivery and replay must be a pure projection of the committed
    # SourceRecord plus the exact seed/evidence lineage materialized for this
    # version. Never resolve mutable production-note bytes by source_id here.
    conversion = StrategySpecConversionService().convert_seed(
        seed=request.seed,
        source_records=[source],
        evidence_items=[request.evidence_item],
        strategy_id=f"strat-{source.source_id}-{job.source_digest[-12:]}",
        title=source.title,
        version="1.0.0",
    )
    registry_payload = dict(conversion.registry_payload)

    metadata = dict(registry_payload.get("metadata") or {})
    metadata["distillation"] = {
        "schema_version": "source_to_draft.v1",
        "source_id": source.source_id,
        "source_digest": job.source_digest,
        "source_event_version": job.event_version,
        "distillation_job_id": job.job_id,
    }
    registry_payload["metadata"] = metadata
    registry_payload["registry_id"] = registry_id
    registry_payload["source_digest"] = job.source_digest
    return registry_payload


def _make_registry_sync(
    config: DistillationControllerConfig,
) -> Any:
    """Build the terminal Registry sink used before durable job acknowledgement."""

    def sync(request: RegistrySyncRequest) -> RegistrySyncResult:
        source = request.source
        job = request.job
        registry_id = _versioned_registry_id(source, job)
        payload = _build_registry_payload(
            request=request,
            registry_id=registry_id,
        )
        existing = _get_registry_entry(config.registry_url, registry_id)
        if existing is not None:
            state = _registry_artifact_state(existing)
            if state in {"approved", "retired"}:
                return RegistrySyncResult(
                    registry_id=registry_id,
                    status="immutable",
                    reason=f"Registry artifact {registry_id} is immutable ({state})",
                )
            if not _registry_entry_matches_expected(existing, payload):
                raise RuntimeError(
                    f"Registry artifact {registry_id} exists with conflicting "
                    f"distillation lineage/content for source {source.source_id}"
                )
            # A stable versioned Registry identity makes this the crash-replay
            # readback path: the write landed before the worker ack did.
            return RegistrySyncResult(
                registry_id=registry_id,
                status="already_terminal",
                reason="versioned Registry draft already exists",
            )

        _register_strategy_spec_if_absent(config.registry_url, payload)
        readback = _get_registry_entry(config.registry_url, registry_id)
        if readback is None:
            raise RuntimeError(
                f"Registry write lacked terminal readback for {registry_id}"
            )
        state = _registry_artifact_state(readback)
        if state in {"approved", "retired"}:
            return RegistrySyncResult(
                registry_id=registry_id,
                status="immutable",
                reason=f"Registry artifact {registry_id} became immutable ({state})",
            )
        if not _registry_entry_matches_expected(readback, payload):
            raise RuntimeError(
                f"Registry write readback mismatched distillation lineage/content for {registry_id}"
            )
        return RegistrySyncResult(registry_id=registry_id, status="synced")

    return sync


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
        
        # 2. Transactionally admit source versions and process leased events.
        # A job is acknowledged only after Registry terminal readback.
        try:
            worker = make_distillation_worker(
                queue_path=config.job_queue_path,
                seed_store_path=config.seed_store_path,
                created_by="strategy-distillation-controller",
                worker_id=state.controller_id,
                lease_seconds=config.lease_seconds,
                retry_base_seconds=config.retry_base_seconds,
                max_attempts=config.max_attempts,
                registry_sync=_make_registry_sync(config),
            )
            run_result = worker.catch_up(source_records, limit=100)
            reconcile_meta = {
                "processed": run_result.processed,
                "created": run_result.created,
                "refreshed": run_result.refreshed,
                "skipped": run_result.skipped,
                "failed": run_result.failed,
                "enqueued": run_result.enqueued,
                "registry_synced": run_result.registry_synced,
                "retried": run_result.retried,
                "dead_lettered": run_result.dead_lettered,
            }
        except Exception as exc:
            raise DistillationControllerError("reconcile_worker", f"Failed to run distillation catch-up: {exc}")

        # 3. Read durable outbox/inbox/DLQ actual state.
        queue = DistillationJobQueue(
            config.job_queue_path,
            default_max_attempts=config.max_attempts,
        )
        queue_metrics = queue.metrics()
        actual_meta = {
            "synced_count": run_result.registry_synced,
            "skipped_immutable_count": run_result.skipped,
            "sync_failures_count": run_result.failed,
            "outbox": queue_metrics,
            "pending_dead_letter_count": len(queue.list_dead_letters()),
        }

        if (
            run_result.failed
            or queue_metrics["retry_wait"]
            or queue_metrics["dead_letter"]
            or queue_metrics["failed"]
        ):
            raise DistillationControllerError(
                "registry_sync",
                "Distillation Registry delivery is degraded; durable retry or DLQ remains",
                actual=actual_meta,
            )

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
                    f"Processed: {reconcile_meta['processed']}. "
                    f"Synced: {run_result.registry_synced}."
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
