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
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.service_token_file import configured_service_token
from services.source_ingestion.connectors.base import (
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from services.source_ingestion.controller_state import (
    ControllerState,
    ControllerStateError,
    ControllerStateStore,
    read_controller_state,
    utc_now,
)
from services.source_ingestion.distillation_worker import (
    DistillationJob,
    DistillationJobQueue,
    MARKET_SOURCE_SKIP_REASON,
    RegistrySyncRequest,
    RegistrySyncResult,
    make_distillation_worker,
    source_version_digest,
    source_version_digest_matches,
)
from services.source_ingestion.hypothesis_card import ENABLED_ENV
from services.source_ingestion.hypothesis_compiler import compiler_enabled, compiler_from_env
from services.source_ingestion.pg_store import read_source_records_for_tenant
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
    max_lease_expiries: int = 5
    # Consecutive hard-failed ticks after which the alive marker stops being
    # refreshed so the container healthcheck turns unhealthy.
    max_consecutive_failed_ticks: int = 5
    terminal_retention_seconds: int = 7 * 86400
    dead_letter_retention_seconds: int = 30 * 86400
    # Retired-runtime closure (empty = off): explicit tenants plus the enqueue
    # window (epoch seconds) whose pending rows are closed out of scope.
    retired_tenant_ids: tuple[str, ...] = ()
    retired_window_from: float = 0.0
    retired_window_to: float = 0.0


STATUS_WORKING = "working"
STATUS_WORKING_DEAD_LETTERS = "working_with_unresolved_dead_letters"
STATUS_NOT_WORKING = "not_working"
STATUS_DELIVERY_DEGRADED = "working_delivery_degraded"
# Process-local: queue path -> a tick of this process completed normal work.
_NORMAL_WORK_SEEN: dict[str, bool] = {}
LEASE_REDRIVE_PER_TICK = 10
LINEAGE_SETTLE_PER_TICK = 10


# Consecutive hard failures per alive path (process-local; a restart resets it
# and the alive file then simply ages out while ticks keep failing).
_HARD_FAILURES: dict[str, int] = {}
# Pruned payload bytes since the last compaction (process-local).
_PRUNED_BYTES: dict[str, int] = {}
COMPACT_AFTER_PRUNED_BYTES = 64 * 1024 * 1024
# Max auth (401/403) dead letters redriven per tick; bounds recovery Registry load.
AUTH_REDRIVE_PER_TICK = 100
SOURCE_BATCH_LIMIT = 100


def _queue_for(config: "DistillationControllerConfig") -> DistillationJobQueue:
    return DistillationJobQueue(
        config.job_queue_path,
        default_max_attempts=config.max_attempts,
        max_lease_expiries=config.max_lease_expiries,
    )


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
    max_lease_expiries = _env_int("DISTILLATION_JOB_MAX_LEASE_EXPIRIES", 5, minimum=1)
    max_consecutive_failed_ticks = _env_int(
        "DISTILLATION_CONTROLLER_MAX_CONSECUTIVE_FAILED_TICKS", 5, minimum=1,
    )
    terminal_retention_seconds = _env_int(
        "DISTILLATION_QUEUE_TERMINAL_RETENTION_SECONDS", 7 * 86400, minimum=60,
    )
    dead_letter_retention_seconds = _env_int(
        "DISTILLATION_QUEUE_DEAD_LETTER_RETENTION_SECONDS", 30 * 86400, minimum=60,
    )
    
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
        max_lease_expiries=max_lease_expiries,
        max_consecutive_failed_ticks=max_consecutive_failed_ticks,
        terminal_retention_seconds=terminal_retention_seconds,
        dead_letter_retention_seconds=dead_letter_retention_seconds,
        retired_tenant_ids=tuple(
            t.strip()
            for t in (os.getenv("DISTILLATION_RETIRED_TENANT_IDS") or "").split(",")
            if t.strip()
        ),
        retired_window_from=float(os.getenv("DISTILLATION_RETIRED_WINDOW_FROM_EPOCH") or 0),
        retired_window_to=float(os.getenv("DISTILLATION_RETIRED_WINDOW_TO_EPOCH") or 0),
    )


def _get_registry_entry(registry_url: str, registry_id: str) -> dict | None:
    url = f"{registry_url}/api/registry/strategy-specs/{registry_id}"
    try:
        headers = {}
        token = configured_service_token("DISTILLATION_REGISTRY_SERVICE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
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
        headers = {"Content-Type": "application/json"}
        token = configured_service_token("DISTILLATION_REGISTRY_SERVICE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
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


# HYPOTHESIS-CARD-GOVERNED-BINDING-20261125: a compiled card rule is bound to the governed whole-market
# dataset of DISCOVERY-GOVERNED-MARKET-DATASET-20261125 (every Taiwan symbol with governed data, screened by the
# rule's own screen or the declared ``whole_governed_tw_boundary.v1`` default). No fixed symbol list is read.
CARD_DATASET_ENV = "PANTHEON_L2_CARD_DATASET_ID"
GOVERNED_CARD_DATASET_IDS = ("governed-market:tw-daily", "tw_price_daily")
CARD_BINDING_SCOPE = "all_governed_tw"
_PINNED_SNAPSHOT_RE = re.compile(r"^gmd-\d{4}-\d{2}-\d{2}-[0-9a-f]{8,64}$")


def card_binding_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Whether compiled card rules can be bound, with a readable reason when they cannot (fail closed).

    ``bound`` names the governed market dataset (a logical id or a pinned ``gmd-`` snapshot); anything else
    is ``unconfigured`` and every compiled rule stays a research-only draft. Reported in the controller state.
    """
    source = os.environ if env is None else env
    dataset_id = str(source.get(CARD_DATASET_ENV) or "").strip()
    if not dataset_id:
        return {"status": "unconfigured", "dataset_id": None,
                "reason": f"card_binding_unconfigured:{CARD_DATASET_ENV} is not set"}
    if dataset_id not in GOVERNED_CARD_DATASET_IDS and not _PINNED_SNAPSHOT_RE.match(dataset_id):
        return {"status": "unconfigured", "dataset_id": dataset_id,
                "reason": (f"card_binding_not_governed_market:{CARD_DATASET_ENV}={dataset_id!r} is neither "
                           f"{' nor '.join(GOVERNED_CARD_DATASET_IDS)} nor a pinned gmd- snapshot")}
    return {"status": "bound", "dataset_id": dataset_id, "scope": CARD_BINDING_SCOPE, "reason": None}


# Successful bindings only, one per (dataset id, end day, store root); a tick needs one or two. Bounded at
# _BINDING_CACHE_ENTRIES entries of about 60 symbol names each (a few KiB), process lifetime, no file.
_BINDING_CACHE: dict[str, dict[str, Any]] = {}
_BINDING_CACHE_ENTRIES = 4


def _binding_end_day(as_of: datetime) -> str:
    """The last trade day a binding made at ``as_of`` may use: the day before (settled bars only), so the
    same job re-derives the same snapshot on replay even if today's bars land later."""
    value = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    return (value.astimezone(timezone.utc).date() - timedelta(days=1)).isoformat()


def resolve_card_dataset_binding(*, as_of: datetime, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The governed market binding of a compiled card rule made at ``as_of``, or the fail-closed reason.

    Returns ``{"status": "bound", "dataset_id": <pinned gmd- snapshot>, "symbols": <the governed dataset's
    symbols>, "scope": "all_governed_tw", ...}``; otherwise ``status`` is ``unconfigured`` or ``unavailable``
    with a readable ``reason``. The boundary is the governed market of DISCOVERY-GOVERNED-MARKET-DATASET-20261125
    (stored daily bars only, minus suspended, short-history and illiquid symbols); the symbols are the governed
    dataset's own (its documented size cap), never an operator list. One boundary is built per end day and
    cached; it reads parquet and runs on this controller's thread, never on an event loop.
    """
    status = card_binding_status(env)
    if status["status"] != "bound":
        return status
    from services.research import governed_market_dataset as governed

    source = os.environ if env is None else env
    requested = status["dataset_id"]
    pinned = requested if requested.startswith("gmd-") else None
    end = "-".join(requested.split("-")[1:4]) if pinned else _binding_end_day(as_of)
    key = f"{requested}|{end}|{governed.store_root(source)}"
    cached = _BINDING_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    try:
        boundary = governed.build_market_boundary(end_date=end, now=as_of, env=source)
        if pinned and boundary.snapshot_id != pinned:
            raise governed.GovernedMarketDatasetError(
                "snapshot_not_reproducible", f"{pinned} now derives as {boundary.snapshot_id}")
        age = (date.fromisoformat(end) - date.fromisoformat(boundary.window_end)).days
        if not pinned and age > boundary.policy.max_stale_calendar_days:
            raise governed.GovernedMarketDatasetError(
                "daily_bars_store_stale", f"newest stored day {boundary.window_end} is {age} days before {end}"
                f" (limit {boundary.policy.max_stale_calendar_days})")
        symbols = list(boundary.symbols[:governed.MAX_DATASET_SYMBOLS])
        result: dict[str, Any] = {
            "status": "bound", "reason": None, "requested_dataset_id": requested,
            "dataset_id": boundary.snapshot_id, "source_dataset_refs": [f"daily_bars:{boundary.snapshot_id}"],
            "symbols": symbols, "scope": CARD_BINDING_SCOPE, "bar_frequency": "1d",
            "market_boundary": {"window_start": boundary.window_start, "window_end": boundary.window_end,
                                "boundary_symbols": len(boundary.symbols), "bound_symbols": len(symbols),
                                "store_symbols": boundary.store_symbol_count},
        }
    except governed.GovernedMarketDatasetError as exc:
        # never cached: a store that recovers later the same day is bound on the next attempt
        return {"status": "unavailable", "dataset_id": requested, "reason": f"card_binding_unavailable:{exc}"[:300]}
    while len(_BINDING_CACHE) >= _BINDING_CACHE_ENTRIES:
        _BINDING_CACHE.pop(next(iter(_BINDING_CACHE)))
    _BINDING_CACHE[key] = dict(result)
    return result


def card_dataset_binding_from_env(env: Mapping[str, str] | None = None, *,
                                  as_of: datetime | None = None) -> dict[str, Any] | None:
    """Governed dataset reference a compiled hypothesis rule made at ``as_of`` is bound to.

    Reference only (no records): the pinned governed snapshot id and its dataset's symbols; the alpha
    replication worker reproduces that snapshot at gate time. ``None`` when unconfigured (the rule stays a
    research-only draft, reason in ``card_binding_status``); an unavailable governed store yields a binding that
    carries only ``unavailable_reason`` so the draft records why.
    """
    resolved = resolve_card_dataset_binding(as_of=as_of or datetime.now(timezone.utc), env=env)
    if resolved["status"] == "unconfigured":
        return None
    if resolved["status"] != "bound":
        return {"unavailable_reason": resolved["reason"]}
    return {k: resolved[k] for k in ("dataset_id", "source_dataset_refs", "symbols", "scope", "bar_frequency")}


def _binding_now() -> datetime:
    """The controller's clock for the tick-level binding status."""
    return datetime.now(timezone.utc)


def _job_time(job: DistillationJob) -> datetime:
    """When the job was enqueued (the binding's as-of time); unparseable falls back to now."""
    try:
        value = datetime.fromisoformat(str(job.enqueued_at).replace("Z", "+00:00"))
    except ValueError:
        return _binding_now()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _card_binding_scalars(binding: Mapping[str, Any]) -> dict[str, Any]:
    """The binding as the scalar ``reconcile.summary`` the persisted controller state keeps (status, id, reason)."""
    boundary = binding.get("market_boundary") if isinstance(binding.get("market_boundary"), Mapping) else {}
    return {"card_binding_status": binding.get("status"), "card_binding_dataset_id": binding.get("dataset_id"),
            "card_binding_reason": binding.get("reason"),
            "card_binding_bound_symbols": boundary.get("bound_symbols"),
            "card_binding_boundary_symbols": boundary.get("boundary_symbols")}


def _card_binding_summary(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """The controller-state view of the binding (no symbol list, only counts)."""
    return {k: v for k, v in resolved.items() if k != "symbols"}


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
    created_at: str | None = None,
) -> dict[str, Any]:
    from services.research.strategy_spec.conversion import (
        StrategySpecConversionService,
    )

    source = request.source
    job = request.job
    committed_digest = source_version_digest(source)
    if job.source_digest and not source_version_digest_matches(source, job.source_digest):
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
    # Only a seed carrying a compiled card rule needs the governed market binding (one boundary per day).
    card = request.seed.metadata.get("hypothesis_card") if isinstance(request.seed.metadata, Mapping) else None
    binding = (card_dataset_binding_from_env(as_of=_job_time(job))
               if isinstance(card, Mapping) and isinstance(card.get("factor_rule_v1"), Mapping) else None)
    conversion = StrategySpecConversionService(
        dataset_binding=binding
    ).convert_seed(
        seed=request.seed,
        source_records=[source],
        evidence_items=[request.evidence_item],
        strategy_id=f"strat-{source.source_id}-{job.source_digest[-12:]}",
        title=source.title,
        version="1.0.0",
        created_at=created_at,
    )
    registry_payload = dict(conversion.registry_payload)

    metadata = dict(registry_payload.get("metadata") or {})
    # Bind the immutable registry projection to the same tenant scope used by
    # the supervised controller. Ingest records may omit tenant metadata.
    metadata.setdefault(
        "tenant_id",
        str(source.metadata.get("tenant_id") or os.getenv("PANTHEON_TENANT_ID") or "default").strip(),
    )
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


def _registry_entry_matches_replay(
    entry: Mapping[str, Any],
    expected: Mapping[str, Any],
    request: RegistrySyncRequest,
) -> bool:
    """Rebuild a versioned draft using its retained creation time, then verify all content.

    The bounded seed store can evict a seed after the Registry write but before
    queue acknowledgement. Recreating that seed supplies today's timestamp.
    Only the existing draft's timestamp is reused; no other Registry content is
    copied into the expected projection, and the full comparator still applies.
    """
    if _registry_entry_matches_expected(entry, expected):
        return True
    if not request.job.source_digest or _registry_artifact_state(entry) != "draft":
        return False
    payload = _registry_entry_payload(entry)
    metadata = payload.get("metadata")
    spec = metadata.get("strategy_spec") if isinstance(metadata, Mapping) else None
    provenance = spec.get("provenance") if isinstance(spec, Mapping) else None
    created_at = provenance.get("created_at") if isinstance(provenance, Mapping) else None
    if not isinstance(created_at, str) or not created_at.strip():
        return False
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return False
        replay = _build_registry_payload(
            request=request,
            registry_id=str(expected["registry_id"]),
            created_at=created_at,
        )
    except ValueError:
        return False
    return _registry_entry_matches_expected(entry, replay)


def _make_registry_sync(
    config: DistillationControllerConfig,
) -> Any:
    """Build the terminal Registry sink used before durable job acknowledgement."""

    def sync(request: RegistrySyncRequest) -> RegistrySyncResult:
        source = request.source
        if source.source_type == SourceType.MARKET:
            return RegistrySyncResult(
                registry_id="",
                status="skipped",
                reason=MARKET_SOURCE_SKIP_REASON,
            )
        job = request.job
        registry_id = _versioned_registry_id(source, job)
        payload = _build_registry_payload(
            request=request,
            registry_id=registry_id,
        )
        existing = _get_registry_entry(config.registry_url, registry_id)
        if existing is not None:
            state = _registry_artifact_state(existing)
            existing_payload = _registry_entry_payload(existing)
            strategy_id = existing_payload.get("strategy_id") or payload.get("strategy_id")
            version = existing_payload.get("version") or payload.get("version")
            checksum = existing_payload.get("checksum") or payload.get("checksum")
            if state in {"approved", "retired"}:
                return RegistrySyncResult(
                    registry_id=registry_id,
                    strategy_id=strategy_id,
                    version=version,
                    checksum=checksum,
                    status="immutable",
                    reason=f"Registry artifact {registry_id} is immutable ({state})",
                )
            if not _registry_entry_matches_replay(existing, payload, request):
                raise RuntimeError(
                    f"Registry artifact {registry_id} exists with conflicting "
                    f"distillation lineage/content for source {source.source_id}"
                )
            # A stable versioned Registry identity makes this the crash-replay
            # readback path: the write landed before the worker ack did.
            return RegistrySyncResult(
                registry_id=registry_id,
                strategy_id=strategy_id,
                version=version,
                checksum=checksum,
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
        readback_payload = _registry_entry_payload(readback)
        strategy_id = readback_payload.get("strategy_id") or payload.get("strategy_id")
        version = readback_payload.get("version") or payload.get("version")
        checksum = readback_payload.get("checksum") or payload.get("checksum")
        if state in {"approved", "retired"}:
            return RegistrySyncResult(
                registry_id=registry_id,
                strategy_id=strategy_id,
                version=version,
                checksum=checksum,
                status="immutable",
                reason=f"Registry artifact {registry_id} became immutable ({state})",
            )
        if not _registry_entry_matches_replay(readback, payload, request):
            raise RuntimeError(
                f"Registry write readback mismatched distillation lineage/content for {registry_id}"
            )
        return RegistrySyncResult(
            registry_id=registry_id,
            strategy_id=strategy_id,
            version=version,
            checksum=checksum,
            status="synced",
        )

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


TERMINAL_DRAFT_READBACK_LIMIT = 200


def _controller_lease_seconds(config: DistillationControllerConfig) -> int:
    """Lease must outlive one steady-state interval plus one tick budget.

    With L2 extraction on, a tick may spend its extraction budget plus one call
    deadline, so the loop truth must not go stale during a normal tick.
    """
    from services.source_ingestion.hypothesis_card import l2_extraction_tick_ceiling_seconds

    return int(config.interval_seconds) + max(
        30, int(config.lease_seconds), int(l2_extraction_tick_ceiling_seconds() + 0.999),
    )


def _controller_tick_evidence_ref(state: ControllerState) -> str:
    return f"distillation-controller:{state.controller_id}:tick:{state.sequence_no}"


def run_controller_tick(
    *,
    config: DistillationControllerConfig,
    state: ControllerState,
    store: ControllerStateStore,
    writer: Any,
    queue: DistillationJobQueue | None = None,
) -> dict[str, Any]:
    state.record_tick_started()
    store.save(state)
    
    loop_id = os.getenv("PANTHEON_LOOP_ID") or "strategy_distillation"
    
    desired_meta: dict[str, Any] = {}
    # The card path's binding is reported on every tick (success or failure) so an unconfigured binding is
    # readable in the controller state; with L2 extraction off no card is produced and nothing is bound.
    card_binding = (_card_binding_summary(resolve_card_dataset_binding(as_of=_binding_now()))
                    if compiler_enabled() else
                    {"status": "extraction_disabled", "dataset_id": None,
                     "reason": f"card_path_off:{ENABLED_ENV} is not enabled"})
    reconcile_meta: dict[str, Any] = {"card_binding": card_binding, "summary": _card_binding_scalars(card_binding)}
    actual_meta: dict[str, Any] = {}
    hard_failure = False
    alive_status = STATUS_NOT_WORKING

    try:
        # 1. Read desired state: normalized SourceRecords
        try:
            def already_admitted(source: SourceRecord) -> bool:
                nonlocal queue
                if queue is None:
                    queue = _queue_for(config)
                return queue.get(source.source_id, source_version_digest(source)) is not None

            source_summary: dict[str, Any] = {}
            source_records = read_source_records_for_tenant(
                jsonl_path=config.evidence_store_path,
                tenant_id=state.tenant_id,
                limit=SOURCE_BATCH_LIMIT,
                already_admitted=already_admitted,
                summary=source_summary,
            )
        except Exception as exc:
            raise DistillationControllerError("desired_read", f"Failed to read source evidence repo: {exc}")
            
        # Filter for eligible source records (exclude rejected ones)
        eligible_records = [
            s for s in source_records
            if s.status == SourceRecordStatus.NORMALIZED
            and not (s.source_type == SourceType.MARKET and s.metadata.get("collector_market_bundle"))
        ]
        eligible_ids = [s.source_id for s in eligible_records]
        eligible_count = source_summary.get("eligible_source_count", len(eligible_ids))
        desired_meta = {
            **source_summary,
            "eligible_source_count": eligible_count,
            "eligible_source_ids": sorted(eligible_ids),
            "eligible_source_ids_scope": "admission_batch",
        }
        
        # 2. Transactionally admit source versions and process leased events.
        # A job is acknowledged only after Registry terminal readback.
        try:
            if queue is None:
                queue = _queue_for(config)
            worker = make_distillation_worker(
                job_queue=queue,
                seed_store_path=config.seed_store_path,
                created_by="strategy-distillation-controller",
                worker_id=state.controller_id,
                lease_seconds=config.lease_seconds,
                retry_base_seconds=config.retry_base_seconds,
                max_attempts=config.max_attempts,
                registry_sync=_make_registry_sync(config),
                # None unless PANTHEON_L2_EXTRACTION_ENABLED; compilation only
                # ever runs on cards the (gated) extractor produced.
                hypothesis_compiler=compiler_from_env(
                    store_path=Path(config.seed_store_path).with_name("hypothesis_compiler.sqlite"),
                ),
            )
            # Re-drive dead letters from transient Registry outages, but only
            # once the Registry answers again (404 on a probe id is an answer).
            registry_reachable = False
            auth_recovery: dict[str, list[str]] = {"redriven": [], "superseded": []}
            lineage_recovery: dict[str, list[str]] = {"replayed": [], "closed": []}
            if queue.dead_letter_count():
                try:
                    _get_registry_entry(config.registry_url, "distillation-redrive-probe")
                    registry_reachable = True
                except Exception:
                    pass
            if registry_reachable:
                queue.redrive_transient_dead_letters(
                    requested_by="strategy-distillation-controller",
                    note="registry reachable again; transient delivery failure redrive",
                    tenant_id=state.tenant_id,
                )
                # Auth (401/403) dead letters: the probe above is made with the
                # worker's own credential, so reaching this point means the
                # Registry accepts it again. Bounded per tick; superseded
                # versions are resolved without delivery.
                auth_recovery = queue.redrive_auth_dead_letters(
                    requested_by="strategy-distillation-controller",
                    note="registry accepts worker credential again; auth failure redrive",
                    limit=AUTH_REDRIVE_PER_TICK,
                    tenant_id=state.tenant_id,
                )
                lineage_recovery = queue.settle_lineage_conflict_dead_letters(
                    requested_by="strategy-distillation-controller",
                    limit=LINEAGE_SETTLE_PER_TICK,
                    tenant_id=state.tenant_id,
                )
            retired_closure: dict[str, Any] = {"closed": [], "refused": {}}
            if config.retired_tenant_ids and config.retired_window_to > 0:
                retired_closure = queue.close_retired_tenant_pending(
                    retired_tenant_ids=config.retired_tenant_ids,
                    active_tenant_id=state.tenant_id,
                    enqueued_from=config.retired_window_from,
                    enqueued_to=config.retired_window_to,
                    requested_by="strategy-distillation-controller",
                )
            run_result = worker.catch_up(
                source_records,
                limit=SOURCE_BATCH_LIMIT,
                tenant_id=state.tenant_id,
            )
            # Lease-expired dead letters (e.g. an OOM burst) are redriven only
            # after this process completed normal work, bounded per tick/job.
            queue_key = str(config.job_queue_path)
            if (
                run_result.registry_synced
                or run_result.skipped
                or run_result.created
                or run_result.refreshed
            ) and not run_result.lease_lost:
                _NORMAL_WORK_SEEN[queue_key] = True
            lease_redriven: list[str] = []
            if _NORMAL_WORK_SEEN.get(queue_key) and not run_result.lease_lost:
                lease_redriven = queue.redrive_lease_expired_dead_letters(
                    requested_by="strategy-distillation-controller",
                    limit=LEASE_REDRIVE_PER_TICK,
                    tenant_id=state.tenant_id,
                )
            reconcile_meta = {
                "card_binding": card_binding,
                "summary": _card_binding_scalars(card_binding),
                "lease_expired_redriven": len(lease_redriven),
                "lineage_replayed": len(lineage_recovery["replayed"]),
                "lineage_closed": len(lineage_recovery["closed"]),
                "retired_closed": len(retired_closure["closed"]),
                "retired_refused": dict(retired_closure["refused"]),
                "processed": run_result.processed,
                "created": run_result.created,
                "refreshed": run_result.refreshed,
                "skipped": run_result.skipped,
                "deferred": run_result.deferred,
                "failed": run_result.failed,
                "enqueued": run_result.enqueued,
                "registry_synced": run_result.registry_synced,
                "retried": run_result.retried,
                "dead_lettered": run_result.dead_lettered,
                "lease_lost": len(run_result.lease_lost),
                "auth_redriven": len(auth_recovery["redriven"]),
                "auth_superseded": len(auth_recovery["superseded"]),
            }
        except Exception as exc:
            raise DistillationControllerError("reconcile_worker", f"Failed to run distillation catch-up: {exc}")

        # Bounded growth: prune terminal payloads / resolved dead letters a
        # fixed batch per tick. A prune failure must not fail distillation.
        try:
            pruned = queue.prune(
                terminal_retention_seconds=config.terminal_retention_seconds,
                dead_letter_retention_seconds=config.dead_letter_retention_seconds,
            )
            reconcile_meta["pruned"] = pruned
            key = str(config.job_queue_path)
            _PRUNED_BYTES[key] = _PRUNED_BYTES.get(key, 0) + int(pruned.get("bytes", 0))
            if _PRUNED_BYTES[key] >= COMPACT_AFTER_PRUNED_BYTES:
                queue.compact()
                _PRUNED_BYTES[key] = 0
                reconcile_meta["compacted"] = True
        except Exception as exc:
            reconcile_meta["prune_error"] = f"{type(exc).__name__}: {exc}"

        if run_result.lease_lost:
            raise DistillationControllerError(
                "lease_lost",
                "Distillation tick degraded: lease superseded for job(s) "
                + ", ".join(run_result.lease_lost[:20]),
                actual={"lease_lost_jobs": list(run_result.lease_lost[:20])},
            )

        # 3. Read durable outbox/inbox/DLQ actual state.
        queue_metrics = queue.metrics()
        terminal_drafts = [
            {
                "source_id": job.source_id,
                "registry_id": job.registry_id,
                "strategy_id": job.strategy_id,
                "version": job.version,
                "checksum": job.checksum,
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            }
            # Bounded: the most recent terminal drafts inside the retention
            # window, never a scan of total history.
            for job in queue.recent_terminal_drafts(
                limit=TERMINAL_DRAFT_READBACK_LIMIT,
                excluded_skip_reason=MARKET_SOURCE_SKIP_REASON,
            )
        ]
        actual_meta = {
            "synced_count": run_result.registry_synced,
            "skipped_immutable_count": run_result.skipped,
            "sync_failures_count": run_result.failed,
            "outbox": queue_metrics,
            "pending_dead_letter_count": queue.dead_letter_count(),
            "terminal_drafts": terminal_drafts,
        }
        # A durable dead letter alone is not "not working": the loop ran and
        # the unresolved rows are counted. Only live delivery trouble fails.
        controller_status = (
            STATUS_WORKING_DEAD_LETTERS
            if queue_metrics["dead_letter"] or actual_meta["pending_dead_letter_count"]
            else STATUS_WORKING
        )
        actual_meta["controller_status"] = controller_status

        if (
            run_result.failed
            or queue_metrics["retry_wait"] - queue_metrics["deferred"]
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
        observed_at = utc_now()
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
                desired_state={
                    "present": bool(eligible_count),
                    "source": "source-ingest.normalized_source_records",
                    "checked_at": observed_at,
                    "summary": f"{eligible_count} eligible normalized source records",
                    "sources": ["source-ingest.normalized_source_records"],
                },
                downstream_actual_state={
                    "status": "ready",
                    "source": "registry.distillation_job_queue",
                    "checked_at": observed_at,
                    "summary": (
                        f"{len(terminal_drafts)} terminal drafts; "
                        f"{actual_meta['pending_dead_letter_count']} "
                        + (
                            "unresolved dead letters (working_with_unresolved_dead_letters)"
                            if controller_status == STATUS_WORKING_DEAD_LETTERS
                            else "dead letters"
                        )
                    ),
                    "sources": ["registry.distillation_job_queue"],
                },
                evidence_refs=[_controller_tick_evidence_ref(state)],
                lease_duration_seconds=_controller_lease_seconds(config),
                payload={
                    "desired": desired_meta,
                    "reconcile": reconcile_meta,
                    "actual": actual_meta,
                }
            )
        )
        
        alive_status = controller_status
        return {
            "status": "success",
            "controller_status": controller_status,
            "desired": desired_meta,
            "reconcile": reconcile_meta,
            "actual": actual_meta,
        }
        
    except Exception as exc:
        stage = getattr(exc, "stage", "reconcile")
        reason = str(exc)
        # "registry_sync" means durable retry / DLQ remains (work is being
        # tracked, the loop itself runs); every other stage means the loop
        # could not do its job this tick.
        hard_failure = stage != "registry_sync"
        alive_status = STATUS_NOT_WORKING if hard_failure else STATUS_DELIVERY_DEGRADED
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
                writer.record_failure(
                    loop_id=loop_id,
                    reason=f"{stage}: {reason}",
                    truth_level="scheduled_tick",
                    evidence_refs=[_controller_tick_evidence_ref(state)],
                    lease_duration_seconds=_controller_lease_seconds(config),
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
        _refresh_alive(config, hard_failure=hard_failure, status=alive_status)


def _refresh_alive(
    config: DistillationControllerConfig, *, hard_failure: bool, status: str = STATUS_WORKING
) -> None:
    """Refresh the healthcheck marker, unless the loop keeps failing.

    The container healthcheck reads this file's mtime. Refreshing it on every
    tick, failed or not, kept the container healthy for days while every tick
    failed. After ``max_consecutive_failed_ticks`` hard failures in a row the
    marker is left to age out so the container turns unhealthy.
    """
    if not config.alive_path:
        return
    key = str(config.alive_path)
    if hard_failure:
        _HARD_FAILURES[key] = _HARD_FAILURES.get(key, 0) + 1
    else:
        _HARD_FAILURES[key] = 0
    if _HARD_FAILURES[key] >= max(1, int(config.max_consecutive_failed_ticks)):
        return
    _write_alive(config.alive_path, status)


def _write_alive(path: Path, status: str = STATUS_WORKING) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{utc_now()}\n{status}\n", encoding="utf-8")
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
    """Refresh this process, never adopt another scope's business checkpoint."""
    runtime = _new_state()
    if state.tenant_id != runtime.tenant_id or state.environment != runtime.environment:
        raise ControllerStateError(
            "persisted controller state tenant/environment does not match this runtime"
        )
    state.controller_id = runtime.controller_id
    state.controller_name = runtime.controller_name
    state.deployment = runtime.deployment
    state.started_at = runtime.started_at
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
    # One queue per controller process, shared by admission, processing and
    # readback. Corrupt storage fails startup closed; no automatic replacement.
    queue = _queue_for(config)
    tick = 0
    last_tick_failed = False
    
    while True:
        tick += 1
        try:
            result = run_controller_tick(
                config=config, state=state, store=store, writer=writer, queue=queue,
            )
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
