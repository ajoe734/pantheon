"""services/telemetry — deployable Flask ingest service.

This is the HTTP surface for TelemetryIngestService (TEL-002).  All telemetry
events from the LEAN runtime flow through this service — LEAN must never write
directly to the canonical Postgres telemetry tables.

Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §2.2:
  - All canonical ingest goes through TelemetryIngestService
  - Production wiring uses build_postgres_write_fn() + asyncpg
  - The service enforces schema validation, evidence contract (E-1 through E-6),
    durable buffering, batch writes, backpressure, and DLQ

Route summary
-------------
POST  /api/telemetry/ingest
    Ingest a single telemetry event.
    Body: telemetry event envelope (see telemetry_event.schema.json).
    Returns 202 on success, 400 on validation failure.

POST  /api/telemetry/ingest/batch
    Ingest a batch of telemetry events.
    Body: { "events": [ ... ] }
    Returns 202 only when at least one event has a durable receipt.

POST  /api/v1/telemetry/infrastructure-health
    Admit one non-trading infrastructure health observation.
    Body: InfrastructureHealthEvent (see the InfrastructureHealthEvent
    definition in telemetry_event.schema.json).
    Requires a verified service JWT whose claims bind the request tenant and an
    allowlisted producer scope; the deployment-wide permissive auth mode and the
    shared static service token are both refused on this route.
    Returns 202 on admission and on an idempotent replay of an event_id that
    already holds a durable receipt, 409 INFRA_EVENT_ID_CONFLICT when an
    event_id is reused for different content, 400 on contract violation, and
    503 when the schema or ledger is unavailable (INFRA_SCHEMA_UNAVAILABLE,
    INFRA_LEDGER_UNCONFIGURED), the deployment has no durable broker behind the
    route (INFRA_BUFFER_NOT_DURABLE), the buffer is full
    (INFRA_BUFFER_OVERFLOW), or no durable receipt exists yet because another
    attempt holds the reservation (INFRA_ADMISSION_IN_FLIGHT,
    INFRA_ADMISSION_FENCED).

    Producers must treat every 503 as "retry with the same event_id": it means
    the observation is not durable yet, so retrying is what makes delivery
    at-least-once while the admission ledger keeps it exactly-once.

POST  /api/v1/telemetry/heartbeats
    Ingest one RuntimeHeartbeat payload and adapt it into a canonical
    TelemetryEvent heartbeat.
    Body: RuntimeHeartbeat (see SD-09).
    Returns 202 on success, 400 on validation failure.

GET   /api/v1/telemetry/runtime/<runtime_id>/heartbeat
    Return the latest RuntimeHeartbeatStatus derived from accepted telemetry.

GET   /api/telemetry/stats
    Service statistics (buffer, writer, DLQ, backpressure).

GET   /api/telemetry/events/<event_id>
    Return the immutable event accepted by this telemetry owner process.

GET   /api/telemetry/dlq
    Dead-letter queue entries.
    Query params:
      tag   — filter by tag (e.g. TAG_WRITER_ERROR, TAG_SCHEMA_VIOLATION)
      limit — max entries to return (default 100)

POST  /api/telemetry/replay
    Replay DLQ write-failure entries through the full ingest path.
    Query params:
      tag — if provided, replay only entries with this tag (default: write-failures)
    Returns { replayed: <count> }.

GET   /api/telemetry/lineage/runtime-bindings/<binding_id>/projection
    Return the derived-only runtime binding lineage projection.

GET   /api/telemetry/lineage/capital-pools/<pool_id>/projection
    Return the derived-only capital pool lineage projection.

GET   /api/telemetry/lineage/events/<event_id>/trace
    Return the derived-only telemetry event trace.

GET   /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry
    Return the derived-only operator trace from source through runtime,
    telemetry, broker/order lifecycle, and evolution refs.

GET   /api/telemetry/lineage/plans/<plan_id>/forensic-trace
    Return the rollback-aware forensic plan trace.

GET   /__health__
    Liveness probe.

Environment variables
---------------------
TELEMETRY_DB_DSN
    asyncpg DSN for the canonical Postgres telemetry store.
    Required for production.  When absent, the service falls back to the
    memory-only dev sink and logs a WARNING at startup.

TELEMETRY_SCHEMA_PATH
    Path to telemetry_event.schema.json.
    Defaults to services/telemetry/telemetry_event.schema.json relative to
    this file.

TELEMETRY_STORAGE_DIR
    Directory for DLQ spill files.  Defaults to /tmp/pantheon/telemetry.

TELEMETRY_RUNTIME_SUMMARY_STORE
    JSON file for the telemetry-owned runtime status projection consumed by
    BFF read paths. Defaults to TELEMETRY_STORAGE_DIR/runtime_summaries.json.

TELEMETRY_RUNTIME_HEARTBEAT_STALE_SECONDS
    Seconds after the last heartbeat before a runtime summary is returned as
    degraded. Defaults to 90.

TELEMETRY_BUFFER_BACKEND
    "jetstream" (default), "redis", or explicit test-only "memory".

TELEMETRY_BUFFER_REDIS_URL
    Redis URL when TELEMETRY_BUFFER_BACKEND=redis.

PANTHEON_NATS_URL
    NATS URL when TELEMETRY_BUFFER_BACKEND=jetstream. The HTTP response waits
    for a JetStream persistence acknowledgement before returning success.

PANTHEON_TELEMETRY_AUTH_MODE
    ``strict`` (default) requires a verified JWT. ``permissive`` additionally
    accepts the shared structured-token form for isolated development.

PANTHEON_TELEMETRY_JWT_SECRET / _JWKS_URI / _OIDC_DISCOVERY_URL
    Telemetry-specific JWT verification settings. Issuer, audience, role-claim,
    and role-map settings use the same ``PANTHEON_TELEMETRY_*`` prefix.

PANTHEON_TELEMETRY_SERVICE_TOKEN
    Exact bearer credential for internal telemetry producers. Its tenant
    authority is restricted by ``PANTHEON_TELEMETRY_SERVICE_TENANTS``.

PANTHEON_TELEMETRY_ALLOWED_TENANTS
    Fallback tenant allowlist when a verified identity token has no tenant
    claim. Every protected request must also send ``X-Tenant-Id``. This fallback
    deliberately does NOT apply to the infrastructure health route, whose tenant
    authority must come from the presented service JWT itself.

PANTHEON_TELEMETRY_INFRA_PRODUCERS
    Explicit allowlist of infrastructure health producer identities admitted by
    this deployment (e.g. ``control-plane-bff``). Wildcards are refused. The
    effective scope is this allowlist intersected with the producer scope in the
    caller's service JWT.

TELEMETRY_INFRASTRUCTURE_HEALTH_LEDGER
    Path to the durable infrastructure health admission ledger. Defaults to
    TELEMETRY_STORAGE_DIR/infrastructure_health_admissions.jsonl. Point every
    replica at the same shared path so a stable event_id is admitted once
    across restarts and replicas.

TELEMETRY_INFRASTRUCTURE_HEALTH_LEASE_SECONDS
    Lease held by one infrastructure health admission reservation before another
    replica may recover it (default 30). Set it above the worst-case durable
    enqueue latency: a crashed owner's reservation only becomes recoverable once
    the lease expires, and until then retries are answered as retryable rather
    than as delivered.

TELEMETRY_BATCH_SIZE
    Max events per write batch (default 500).

TELEMETRY_BATCH_INTERVAL
    Max seconds before flushing a partial batch (default 1.0).

TELEMETRY_MAX_RETRIES
    Max retries for transient write failures (default 5).

TELEMETRY_REPLAY_DLQ_ON_START
    If true, load persisted DLQ spill entries and replay safe write-failure
    entries after the writer starts. Defaults to false because deployment
    bootstrap already performs one explicit replay pass.

TELEMETRY_REPLAY_DLQ_TAG
    Optional explicit DLQ tag for startup replay. When unset, startup replay
    uses the safe default write-failure tag set.

LINEAGE_READ_CORPUS_PATH
    Optional path to the LIN-001A lineage benchmark corpus used to bootstrap
    the lineage read HTTP surface. Defaults to
    services/registry/lineage/lin001a_benchmark_corpus.json.

    LIN-003: this same in-memory lineage graph is also the live write target
    for accepted telemetry — every event ingest() accepts is admitted into it
    immediately (along with its resolved RuntimeBinding, if not already a
    graph node), so the lineage read routes below resolve newly-ingested
    events without waiting for a corpus reload. See
    docs/decisions/LIN-003-live-lineage-write-path.md.

PANTHEON_RUNTIME_MANAGER_URL
    Base URL of the authoritative runtime-manager service
    (e.g. http://runtime-manager:8081).  When set, the ingest service wires
    an authoritative RuntimeBinding store so that events with unknown or
    mismatched binding_id references are rejected at ingest time.  When
    absent, only field-presence and enum checks are applied (dev/test mode).

PANTHEON_RUNTIME_MANAGER_TOKEN
    Bearer token sent to the runtime-manager API.
    Defaults to "runtime-control-internal".

PORT
    HTTP listen port (default 8080).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import types
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from .auth import (
    TelemetryAuthorityError,
    bind_event_producer,
    bind_event_tenant,
    request_authority,
    request_tenant_id,
    require_infrastructure_health_authority,
    require_telemetry_authority,
)
from .heartbeat_service import (
    RuntimeHeartbeatValidationError,
    build_telemetry_event_from_runtime_heartbeat,
    heartbeat_status_from_summary,
)
from .ingest_svc import TelemetryIngestService, build_postgres_write_fn
from .lineage_read import LineageReadService
from .runtime_summary import RuntimeSummaryProjectionStore
from .trade_episode_projection import TradeEpisodeProjectionStore
from services.foundation.health import register_flask_health_routes
from services.runtime_auth import resolve_runtime_manager_auth

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async event loop in a background thread
# ---------------------------------------------------------------------------
# TelemetryIngestService uses an asyncio-based batch writer that must persist
# across requests.  We run a dedicated event loop on a daemon thread and route
# all coroutine calls through it from Flask handlers.

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None

_DEFAULT_STARTUP_TIMEOUT_SECONDS = 180.0


def _start_background_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True, name="telemetry-asyncio-loop")
    thread.start()
    return loop, thread


def _run_async(coro, timeout: float = 30.0):
    """Submit *coro* to the background event loop and block until done."""
    if _loop is None:
        raise RuntimeError("Background event loop not initialised")
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=timeout)


def _startup_timeout_seconds() -> float:
    raw = os.getenv(
        "TELEMETRY_STARTUP_TIMEOUT_SECONDS",
        str(int(_DEFAULT_STARTUP_TIMEOUT_SECONDS)),
    )
    try:
        timeout = float(raw)
    except ValueError:
        log.warning(
            "Invalid TELEMETRY_STARTUP_TIMEOUT_SECONDS=%r; using %.0fs",
            raw,
            _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        )
        return _DEFAULT_STARTUP_TIMEOUT_SECONDS
    return max(1.0, timeout)


# ---------------------------------------------------------------------------
# RuntimeBinding adapter
# ---------------------------------------------------------------------------


class _RuntimeBindingAdapter:
    """Satisfies RuntimeBindingProtocol by calling the runtime-manager HTTP API.

    Converts the JSON dict returned by GET /api/runtime-bindings/{id} into a
    SimpleNamespace so that TelemetryIngestService can access binding attributes
    (runtime_id, capital_pool_id, deployment_mode, etc.) without pulling in the
    full runtime-manager package.

    Returns None for unknown bindings (404) so that ingest rejects the event.
    On transient connectivity errors the lookup also returns None (fail-closed).
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = resolve_runtime_manager_auth(token=token)
        self._timeout = timeout

    def get_binding(self, binding_id: str):
        url = f"{self._base_url}/api/runtime-bindings/{binding_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                **self._auth.headers(),
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
                return types.SimpleNamespace(**data) if isinstance(data, dict) else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            log.warning(
                "runtime-manager returned HTTP %s for binding %s — treating as unknown",
                exc.code,
                binding_id,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "runtime-manager lookup failed for binding %s: %s — treating as unknown",
                binding_id,
                exc,
            )
            return None


# ---------------------------------------------------------------------------
# Service bootstrap
# ---------------------------------------------------------------------------

_svc: TelemetryIngestService | None = None
_lineage_svc: LineageReadService | None = None

_DEFAULT_SCHEMA_PATH = str(Path(__file__).resolve().parent / "telemetry_event.schema.json")
_DEFAULT_STORAGE_DIR = "/tmp/pantheon/telemetry"
_DEFAULT_LINEAGE_CORPUS_PATH = (
    Path(__file__).resolve().parent.parent / "registry" / "lineage" / "lin001a_benchmark_corpus.json"
)
_CANONICAL_TELEMETRY_TABLE = "telemetry_events"
_REQUIRED_TELEMETRY_COLUMNS = ("event_id", "event_type", "created_at", "payload")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_table_name(table: str) -> tuple[str, str]:
    if "." not in table:
        return "public", table
    schema, name = table.split(".", 1)
    return schema or "public", name


async def _probe_canonical_telemetry_table(
    dsn: str,
    table: str = _CANONICAL_TELEMETRY_TABLE,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Verify the canonical telemetry_events table exists with required columns."""
    schema_name, table_name = _split_table_name(table)
    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        return {
            "status": "error",
            "backend": "postgres",
            "dsn_configured": True,
            "table": table,
            "table_exists": None,
            "message": "asyncpg is not installed; telemetry Postgres readiness cannot be checked",
        }

    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=timeout)
        regclass = await conn.fetchval("SELECT to_regclass($1)", table)
        if regclass is None:
            return {
                "status": "error",
                "backend": "postgres",
                "dsn_configured": True,
                "table": table,
                "table_exists": False,
                "message": (
                    "canonical telemetry table is missing; run scripts/db_migrate.sh "
                    "before marking telemetry ready"
                ),
            }

        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            """,
            schema_name,
            table_name,
        )
        columns = {
            str(row["column_name"] if isinstance(row, Mapping) else row[0])
            for row in rows
        }
        missing_columns = [
            column for column in _REQUIRED_TELEMETRY_COLUMNS if column not in columns
        ]
        if missing_columns:
            return {
                "status": "error",
                "backend": "postgres",
                "dsn_configured": True,
                "table": table,
                "table_exists": True,
                "required_columns_present": False,
                "missing_columns": missing_columns,
                "message": (
                    "canonical telemetry table is present but missing required columns; "
                    "run scripts/db_migrate.sh"
                ),
            }

        return {
            "status": "ok",
            "backend": "postgres",
            "dsn_configured": True,
            "table": table,
            "table_exists": True,
            "required_columns_present": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "backend": "postgres",
            "dsn_configured": True,
            "table": table,
            "table_exists": None,
            "message": str(exc),
        }
    finally:
        if conn is not None:
            await conn.close()


def _canonical_telemetry_table_dependency() -> dict[str, Any]:
    dsn = os.getenv("TELEMETRY_DB_DSN", "").strip()
    if not dsn:
        return {
            "status": "ok",
            "backend": "memory",
            "dsn_configured": False,
            "table": _CANONICAL_TELEMETRY_TABLE,
            "table_exists": None,
            "message": "TELEMETRY_DB_DSN is not set; memory-only dev sink active",
        }
    if _loop is None:
        return {
            "status": "error",
            "backend": "postgres",
            "dsn_configured": True,
            "table": _CANONICAL_TELEMETRY_TABLE,
            "table_exists": None,
            "message": "background event loop is unavailable for telemetry Postgres readiness",
        }
    return _run_async(_probe_canonical_telemetry_table(dsn), timeout=3.0)


def _build_service(lineage_write_store: LineageReadService | None = None) -> TelemetryIngestService:
    """Instantiate TelemetryIngestService with production Postgres and RuntimeBinding wiring.

    ``lineage_write_store`` wires the LIN-003 live lineage write path: when
    provided, every accepted event is admitted into that lineage graph
    immediately, so the deployed lineage read surface resolves it without a
    corpus reload.
    """
    db_dsn = os.getenv("TELEMETRY_DB_DSN", "")
    if db_dsn:
        write_fn = build_postgres_write_fn(dsn=db_dsn)
        log.info("TelemetryIngestService: using Postgres write path (asyncpg)")
    else:
        write_fn = None  # falls back to memory-only dev sink
        log.warning(
            "TELEMETRY_DB_DSN is not set — "
            "TelemetryIngestService using memory-only dev sink. "
            "Set TELEMETRY_DB_DSN for production."
        )

    rm_url = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip()
    if rm_url:
        binding_store: _RuntimeBindingAdapter | None = _RuntimeBindingAdapter(
            base_url=rm_url
        )
        log.info(
            "TelemetryIngestService: authoritative RuntimeBinding validation wired to %s", rm_url
        )
    else:
        binding_store = None
        log.warning(
            "PANTHEON_RUNTIME_MANAGER_URL is not set — "
            "TelemetryIngestService will skip authoritative binding validation. "
            "Set PANTHEON_RUNTIME_MANAGER_URL for production."
        )

    schema_path = os.getenv("TELEMETRY_SCHEMA_PATH", _DEFAULT_SCHEMA_PATH)
    storage_dir = os.getenv("TELEMETRY_STORAGE_DIR", _DEFAULT_STORAGE_DIR)
    buffer_backend = os.getenv("TELEMETRY_BUFFER_BACKEND", "jetstream")
    redis_url = os.getenv("TELEMETRY_BUFFER_REDIS_URL", "redis://localhost:6379/0")
    nats_url = os.getenv("PANTHEON_NATS_URL", "nats://localhost:4222")

    try:
        batch_size = int(os.getenv("TELEMETRY_BATCH_SIZE", "500"))
    except ValueError:
        batch_size = 500
    try:
        batch_interval = float(os.getenv("TELEMETRY_BATCH_INTERVAL", "1.0"))
    except ValueError:
        batch_interval = 1.0
    try:
        max_retries = int(os.getenv("TELEMETRY_MAX_RETRIES", "5"))
    except ValueError:
        max_retries = 5
    try:
        heartbeat_stale_after = int(os.getenv("TELEMETRY_RUNTIME_HEARTBEAT_STALE_SECONDS", "90"))
    except ValueError:
        heartbeat_stale_after = 90
    try:
        infrastructure_health_lease = float(
            os.getenv("TELEMETRY_INFRASTRUCTURE_HEALTH_LEASE_SECONDS", "30")
        )
    except ValueError:
        infrastructure_health_lease = 30.0
    replay_dlq_on_start = _env_bool("TELEMETRY_REPLAY_DLQ_ON_START", default=False)
    dlq_replay_tag_filter = os.getenv("TELEMETRY_REPLAY_DLQ_TAG") or None

    Path(storage_dir).mkdir(parents=True, exist_ok=True)
    runtime_summary_path = os.getenv(
        "TELEMETRY_RUNTIME_SUMMARY_STORE",
        str(Path(storage_dir) / "runtime_summaries.json"),
    )
    runtime_summary_store = RuntimeSummaryProjectionStore(
        runtime_summary_path,
        heartbeat_stale_after_seconds=heartbeat_stale_after,
    )

    trade_episode_projections_path = os.getenv(
        "TELEMETRY_TRADE_EPISODE_PROJECTION_STORE",
        str(Path(storage_dir) / "trade_episode_projections.json"),
    )
    trade_episode_events_path = os.getenv(
        "TELEMETRY_TRADE_EPISODE_EVENTS_STORE",
        str(Path(storage_dir) / "trade_episode_events.json"),
    )
    trade_episode_projection_store = TradeEpisodeProjectionStore(
        trade_episode_projections_path,
        trade_episode_events_path,
    )

    return TelemetryIngestService(
        schema_path=schema_path if Path(schema_path).exists() else None,
        storage_dir=storage_dir,
        buffer_backend=buffer_backend,
        buffer_redis_url=redis_url,
        buffer_nats_url=nats_url,
        buffer_stream_name=os.getenv(
            "TELEMETRY_BUFFER_STREAM_NAME",
            "PANTHEON_TELEMETRY_INGEST",
        ),
        buffer_subject=os.getenv(
            "TELEMETRY_BUFFER_SUBJECT",
            "pantheon.telemetry.ingest",
        ),
        buffer_durable_name=os.getenv(
            "TELEMETRY_BUFFER_DURABLE_NAME",
            "telemetry-postgres-writer",
        ),
        batch_size=batch_size,
        batch_interval=batch_interval,
        max_retries=max_retries,
        write_fn=write_fn,
        binding_store=binding_store,
        runtime_summary_store=runtime_summary_store,
        trade_episode_projection_store=trade_episode_projection_store,
        lineage_write_store=lineage_write_store,
        replay_dlq_on_start=replay_dlq_on_start,
        dlq_replay_tag_filter=dlq_replay_tag_filter,
        infrastructure_health_ledger_path=os.getenv(
            "TELEMETRY_INFRASTRUCTURE_HEALTH_LEDGER"
        )
        or None,
        infrastructure_health_lease_seconds=infrastructure_health_lease,
    )


def _get_service() -> TelemetryIngestService:
    global _svc
    if _svc is None:
        raise RuntimeError("Service not started; call startup() first")
    return _svc


def _build_lineage_service() -> LineageReadService | None:
    """Bootstrap the lineage read service from the canonical LIN-001A corpus."""
    corpus_path = Path(
        os.getenv("LINEAGE_READ_CORPUS_PATH", str(_DEFAULT_LINEAGE_CORPUS_PATH))
    ).resolve()
    if not corpus_path.exists():
        log.warning(
            "LineageReadService disabled: corpus file not found at %s",
            corpus_path,
        )
        return None

    svc = LineageReadService()
    try:
        corpus = json.loads(corpus_path.read_text())
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to load lineage corpus from %s", corpus_path)
        raise RuntimeError(f"Failed to load lineage corpus: {exc}") from exc

    svc.load_corpus(corpus)
    log.info("LineageReadService bootstrapped from %s", corpus_path)
    return svc


def _get_lineage_service() -> LineageReadService:
    global _lineage_svc
    if _lineage_svc is None:
        raise RuntimeError(
            "Lineage read service unavailable; ensure LINEAGE_READ_CORPUS_PATH exists"
        )
    return _lineage_svc


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _telemetry_metrics() -> dict[str, Any]:
    try:
        stats = _get_service().stats()
    except Exception:
        return {"service_started": 0}
    service_stats = stats.get("service", {}) if isinstance(stats, dict) else {}
    writer_stats = stats.get("writer", {}) if isinstance(stats, dict) else {}
    dlq_stats = stats.get("dead_letter_queue", {}) if isinstance(stats, dict) else {}
    buffer_stats = stats.get("buffer", {}) if isinstance(stats, dict) else {}
    startup_stats = stats.get("startup", {}) if isinstance(stats, dict) else {}
    infrastructure_stats = (
        stats.get("infrastructure_health", {}) if isinstance(stats, dict) else {}
    )
    infrastructure_ledger = (
        infrastructure_stats.get("ledger", {})
        if isinstance(infrastructure_stats, dict)
        else {}
    )
    tag_counts = dlq_stats.get("tag_counts", {}) if isinstance(dlq_stats, dict) else {}
    write_failure_dlq_entries = (
        int(tag_counts.get("writer_error", 0) or 0)
        + int(tag_counts.get("retry_exhausted", 0) or 0)
    )
    return {
        "service_started": 1 if service_stats.get("started") else 0,
        "accepted_count": service_stats.get("total_ingested", 0),
        "rejected_count": service_stats.get("total_rejected", 0),
        "duplicate_count": service_stats.get("total_duplicates", 0),
        "writer_running": 1 if writer_stats.get("running") else 0,
        "writer_total_written": writer_stats.get("total_written", 0),
        "writer_total_failed": writer_stats.get("total_failed", 0),
        "writer_total_retried": writer_stats.get("total_retried", 0),
        "writer_total_dlq": writer_stats.get("total_dlq", 0),
        "writer_batches_flushed": writer_stats.get("batches_flushed", 0),
        "writer_failure_dlq_entries": write_failure_dlq_entries,
        "writer_seconds_since_last_write_attempt": writer_stats.get("seconds_since_last_write_attempt"),
        "writer_seconds_since_last_successful_write": writer_stats.get("seconds_since_last_successful_write"),
        "writer_seconds_since_last_failed_write": writer_stats.get("seconds_since_last_failed_write"),
        "writer_seconds_since_last_dlq": writer_stats.get("seconds_since_last_dlq"),
        "buffer_size": buffer_stats.get("size", 0),
        "buffer_capacity": buffer_stats.get("capacity", 0),
        "dlq_memory_entries": dlq_stats.get("memory_entries", 0),
        "dlq_total_rejected": dlq_stats.get("total_rejected", 0),
        "dlq_incident_fired": 1 if dlq_stats.get("incident_fired") else 0,
        "startup_dlq_loaded": startup_stats.get("dlq_loaded_from_spill", 0),
        "startup_dlq_replayed": startup_stats.get("dlq_replayed_on_start", 0),
        "infrastructure_health_admitted": infrastructure_stats.get("admitted", 0),
        "infrastructure_health_duplicates": infrastructure_stats.get("duplicates", 0),
        "infrastructure_health_conflicts": infrastructure_stats.get("conflicts", 0),
        "infrastructure_health_in_flight_rejections": infrastructure_stats.get("in_flight_rejections", 0),
        "infrastructure_health_fenced_rejections": infrastructure_stats.get("fenced_rejections", 0),
        "infrastructure_health_non_durable_rejections": infrastructure_stats.get(
            "non_durable_rejections", 0
        ),
        "infrastructure_health_rejected": infrastructure_stats.get("rejected", 0),
        "infrastructure_health_schema_loaded": 1 if infrastructure_stats.get("schema_loaded") else 0,
        # 0 means this deployment cannot admit infrastructure health at all: the
        # route fails closed until a durable broker is configured behind it.
        "infrastructure_health_buffer_durable": 1 if infrastructure_stats.get("buffer_durable") else 0,
        "infrastructure_health_ledger_committed_ids": infrastructure_ledger.get("committed_event_ids", 0),
        "infrastructure_health_ledger_open_reservations": infrastructure_ledger.get("open_reservations", 0),
        "infrastructure_health_ledger_recoverable_reservations": infrastructure_ledger.get(
            "recoverable_expired_reservations", 0
        ),
    }


def _telemetry_dependencies() -> dict[str, Any]:
    try:
        stats = _get_service().stats()
    except Exception as exc:  # noqa: BLE001
        writer_payload: dict[str, Any] = {
            "status": "error",
            "message": str(exc),
        }
        dlq_payload: dict[str, Any] = {"status": "ok", "memory_entries": 0}
        canonical_table_payload = _canonical_telemetry_table_dependency()
    else:
        writer_stats = stats.get("writer", {}) if isinstance(stats, dict) else {}
        service_stats = stats.get("service", {}) if isinstance(stats, dict) else {}
        dlq_stats = stats.get("dead_letter_queue", {}) if isinstance(stats, dict) else {}
        tag_counts = dlq_stats.get("tag_counts", {}) if isinstance(dlq_stats, dict) else {}
        write_failure_dlq_entries = (
            int(tag_counts.get("writer_error", 0) or 0)
            + int(tag_counts.get("retry_exhausted", 0) or 0)
        )
        writer_running = bool(service_stats.get("started")) and bool(writer_stats.get("running"))
        writer_payload = {
            "status": "ok" if writer_running else "error",
            "running": writer_running,
            "total_written": writer_stats.get("total_written", 0),
            "total_failed": writer_stats.get("total_failed", 0),
            "total_retried": writer_stats.get("total_retried", 0),
            "total_dlq": writer_stats.get("total_dlq", 0),
            "batches_flushed": writer_stats.get("batches_flushed", 0),
            "failure_dlq_entries": write_failure_dlq_entries,
            "last_write_attempt_at": writer_stats.get("last_write_attempt_at"),
            "last_successful_write_at": writer_stats.get("last_successful_write_at"),
            "last_failed_write_at": writer_stats.get("last_failed_write_at"),
            "last_dlq_at": writer_stats.get("last_dlq_at"),
            "seconds_since_last_write_attempt": writer_stats.get("seconds_since_last_write_attempt"),
            "seconds_since_last_successful_write": writer_stats.get("seconds_since_last_successful_write"),
            "seconds_since_last_failed_write": writer_stats.get("seconds_since_last_failed_write"),
            "seconds_since_last_dlq": writer_stats.get("seconds_since_last_dlq"),
            "last_error": writer_stats.get("last_error"),
        }
        dlq_payload = {
            "status": "ok",
            "memory_entries": dlq_stats.get("memory_entries", 0),
            "total_rejected": dlq_stats.get("total_rejected", 0),
            "incident_fired": bool(dlq_stats.get("incident_fired")),
            "spill_path": dlq_stats.get("spill_path"),
            "tag_counts": dlq_stats.get("tag_counts", {}),
            "write_failure_entries": write_failure_dlq_entries,
        }
        canonical_table_payload = _canonical_telemetry_table_dependency()

    return {
        "runtime_manager": {
            "status": "ok" if os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip(),
        },
        "lineage_read": {"status": "ok" if _lineage_svc is not None else "degraded"},
        "canonical_telemetry_table": canonical_table_payload,
        "telemetry_writer": writer_payload,
        "dead_letter_queue": dlq_payload,
    }


register_flask_health_routes(
    app,
    "telemetry-ingest",
    dependencies=_telemetry_dependencies,
    metrics=_telemetry_metrics,
    details=lambda: {
        "storage_dir": os.getenv("TELEMETRY_STORAGE_DIR", _DEFAULT_STORAGE_DIR),
        "replay_dlq_on_start": _env_bool("TELEMETRY_REPLAY_DLQ_ON_START", default=False),
        "dlq_replay_tag_filter": os.getenv("TELEMETRY_REPLAY_DLQ_TAG") or None,
    },
)


def _lineage_query_response(query_family: str, *, tenant_id: str, **params):
    """Execute a lineage query family and map service-level errors to HTTP."""
    try:
        result = _get_lineage_service().query(
            query_family,
            tenant_id=tenant_id,
            **params,
        )
    except RuntimeError as exc:
        return jsonify({"error": {"code": "LINEAGE_UNAVAILABLE", "message": str(exc)}}), 503
    except ValueError as exc:
        return jsonify({"error": {"code": "INVALID_LINEAGE_QUERY", "message": str(exc)}}), 400
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected lineage read error for %s", query_family)
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    node_not_found = any(
        marker.get("code") == "node_not_found"
        for marker in result.get("conflict_markers", [])
    )
    if node_not_found:
        return jsonify({
            "error": {
                "code": "LINEAGE_TARGET_NOT_FOUND",
                "message": f"No lineage target found for {query_family}",
                "query_family": query_family,
                "params": params,
            }
        }), 404

    return jsonify(result), 200


def startup():
    """Start the background event loop and the ingest service."""
    global _loop, _loop_thread, _svc, _lineage_svc
    _loop, _loop_thread = _start_background_loop()
    # LIN-003: build the lineage service first so _build_service() can wire
    # it as the ingest live-write target — accepted events become resolvable
    # through the lineage read routes without a corpus reload.
    _lineage_svc = _build_lineage_service()
    _svc = _build_service(lineage_write_store=_lineage_svc)
    startup_timeout = _startup_timeout_seconds()
    _run_async(_svc.start(), timeout=startup_timeout)
    log.info("TelemetryIngestService started within %.0fs startup budget", startup_timeout)


def shutdown():
    """Gracefully stop the ingest service."""
    global _svc, _loop, _lineage_svc
    if _svc is not None:
        try:
            _run_async(_svc.stop(graceful=True), timeout=15.0)
        except Exception as exc:
            log.error(f"Error during graceful shutdown: {exc}")
        _svc = None
    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        _loop = None
    _lineage_svc = None
    log.info("TelemetryIngestService stopped")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/__health__", methods=["GET"])
def health():
    try:
        svc = _get_service()
        stats = svc.stats()
        return jsonify({
            "status": "ok",
            "service": "telemetry-ingest",
            "started": stats["service"]["started"],
        }), 200
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503


@app.route("/api/telemetry/ingest", methods=["POST"])
@require_telemetry_authority(("service", "operator", "admin"))
def ingest_event():
    """Ingest a single telemetry event.

    Returns 202 on success, 400 if the event fails schema or evidence validation.
    """
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Request body must be a JSON object"}}), 400

    try:
        body = bind_event_tenant(body, request_tenant_id())
    except TelemetryAuthorityError as exc:
        payload, status = exc.as_response()
        return jsonify(payload), status

    svc = _get_service()
    try:
        ok = _run_async(svc.ingest(body))
    except Exception as exc:
        log.exception("Unexpected error during ingest")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    if ok:
        return jsonify({"status": "accepted"}), 202
    return jsonify({"status": "rejected", "detail": "Event failed validation; see DLQ for details"}), 400


@app.route("/api/telemetry/ingest/batch", methods=["POST"])
@require_telemetry_authority(("service", "operator", "admin"))
def ingest_batch():
    """Ingest a batch of telemetry events.

    Body: { "events": [ <event>, ... ] }
    Returns 202 when at least one event has a durable receipt. A mixed batch is
    ``partially_accepted`` and reports both counts. Empty or wholly rejected
    batches return 400 so HTTP success never represents zero durable receipts.
    """
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Request body must be a JSON object"}}), 400
    events = body.get("events")
    if not isinstance(events, list):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Body must have an 'events' list"}}), 400
    if not events:
        return jsonify({
            "status": "rejected",
            "ingested": 0,
            "rejected": 0,
            "error": {
                "code": "EMPTY_BATCH",
                "message": "Batch must contain at least one telemetry event",
            },
        }), 400

    if any(not isinstance(event, Mapping) for event in events):
        return jsonify({
            "error": {
                "code": "INVALID_BODY",
                "message": "Every batch event must be a JSON object",
            }
        }), 400
    try:
        events = [
            bind_event_tenant(event, request_tenant_id())
            for event in events
        ]
    except TelemetryAuthorityError as exc:
        payload, status = exc.as_response()
        return jsonify(payload), status

    svc = _get_service()
    try:
        result = _run_async(svc.ingest_batch(events))
    except Exception as exc:
        log.exception("Unexpected error during batch ingest")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    ingested = int(result.get("ingested") or 0)
    rejected = int(result.get("rejected") or 0)
    if ingested == 0:
        return jsonify({
            **result,
            "status": "rejected",
            "error": {
                "code": "BATCH_NOT_ACCEPTED",
                "message": "No telemetry event received a durable acknowledgement",
            },
        }), 400
    return jsonify({
        **result,
        "status": "partially_accepted" if rejected else "accepted",
    }), 202


# Rejection codes returned by TelemetryIngestService.ingest_infrastructure_health()
# mapped onto their HTTP meaning. Anything unmapped is a producer contract
# violation and answers 400.
_INFRASTRUCTURE_HEALTH_ERROR_STATUS = {
    "INFRA_EVENT_ID_CONFLICT": 409,
    "INFRA_SCHEMA_UNAVAILABLE": 503,
    "INFRA_LEDGER_UNCONFIGURED": 503,
    # The deployment has no durable broker behind this route, so nothing may be
    # admitted: a volatile enqueue would be erased by the next crash while the
    # admission ledger kept answering retries as an already-delivered duplicate.
    "INFRA_BUFFER_NOT_DURABLE": 503,
    "INFRA_BUFFER_OVERFLOW": 503,
    # Retryable: no durable receipt exists yet, so the producer must try again
    # rather than treat the observation as delivered.
    "INFRA_ADMISSION_IN_FLIGHT": 503,
    "INFRA_ADMISSION_FENCED": 503,
}


@app.route("/api/v1/telemetry/infrastructure-health", methods=["POST"])
@require_infrastructure_health_authority()
def ingest_infrastructure_health():
    """Admit one non-trading infrastructure health observation.

    This route is the only admission path for infrastructure_health telemetry.
    It carries no RuntimeBinding, so its authority is entirely the caller's
    verified service identity: a strict service JWT that binds the request
    tenant and an allowlisted producer scope. Trading telemetry keeps its own
    binding and lineage validation on the routes above, and an event shaped like
    an infrastructure probe cannot use this route to skip it.
    """
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Request body must be a JSON object"}}), 400

    try:
        authority = request_authority()
        event = bind_event_tenant(body, authority.tenant_id)
        event = bind_event_producer(event, authority)
    except TelemetryAuthorityError as exc:
        payload, status = exc.as_response()
        return jsonify(payload), status

    svc = _get_service()
    try:
        result = _run_async(svc.ingest_infrastructure_health(event))
    except Exception as exc:
        log.exception("Unexpected error during infrastructure health ingest")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    status = result.get("status")
    if status in ("accepted", "duplicate"):
        return jsonify({
            "status": "accepted",
            "duplicate": status == "duplicate",
            "event_id": result.get("event_id"),
            "fingerprint": result.get("fingerprint"),
            "tenant_id": event.get("tenant_id"),
            "producer": event.get("producer"),
        }), 202

    code = str(result.get("code") or "INFRA_REJECTED")
    return jsonify({
        "status": "rejected",
        "error": {"code": code, "message": result.get("message")},
        "event_id": result.get("event_id"),
    }), _INFRASTRUCTURE_HEALTH_ERROR_STATUS.get(code, 400)


@app.route("/api/v1/telemetry/heartbeats", methods=["POST"])
@require_telemetry_authority(("service", "operator", "admin"))
def ingest_runtime_heartbeat():
    """Ingest a RuntimeHeartbeat through the canonical TelemetryEvent path."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Request body must be a JSON object"}}), 400

    svc = _get_service()
    binding = None
    runtime_binding_id = body.get("runtime_binding_id")
    if isinstance(runtime_binding_id, str) and runtime_binding_id.strip():
        try:
            binding = svc.resolve_runtime_binding(runtime_binding_id.strip())
        except Exception as exc:  # noqa: BLE001
            log.exception("RuntimeBinding lookup failed during RuntimeHeartbeat ingest")
            return jsonify({
                "error": {
                    "code": "RUNTIME_BINDING_LOOKUP_FAILED",
                    "message": str(exc),
                }
            }), 503
        if svc.has_runtime_binding_store() and binding is None:
            return jsonify({
                "error": {
                    "code": "BINDING_NOT_FOUND",
                    "message": f"runtime_binding_id {runtime_binding_id!r} was not found",
                }
            }), 400

    try:
        event = build_telemetry_event_from_runtime_heartbeat(body, binding=binding)
    except RuntimeHeartbeatValidationError as exc:
        return jsonify({"error": {"code": exc.code, "message": exc.message}}), 400

    try:
        event = bind_event_tenant(event, request_tenant_id())
        ok = _run_async(svc.ingest(event))
    except TelemetryAuthorityError as exc:
        payload, status = exc.as_response()
        return jsonify(payload), status
    except Exception as exc:
        log.exception("Unexpected error during RuntimeHeartbeat ingest")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    if not ok:
        return jsonify({
            "status": "rejected",
            "detail": "RuntimeHeartbeat failed canonical TelemetryEvent validation; see DLQ for details",
        }), 400

    summary = svc.get_runtime_summary(
        event["runtime_id"],
        tenant_id=request_tenant_id(),
    )
    response: dict[str, Any] = {
        "status": "accepted",
        "event_id": event["event_id"],
        "runtime_id": event["runtime_id"],
        "runtime_binding_id": event["binding_id"],
    }
    if summary is not None:
        response["heartbeat_status"] = heartbeat_status_from_summary(summary)
    return jsonify(response), 202


@app.route("/api/v1/telemetry/runtime/<runtime_id>/heartbeat", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def runtime_heartbeat_status(runtime_id: str):
    """Return the latest RuntimeHeartbeatStatus for one runtime."""
    svc = _get_service()
    summary = svc.get_runtime_summary(runtime_id, tenant_id=request_tenant_id())
    if summary is None or not summary.get("last_heartbeat_at"):
        return jsonify({
            "error": {
                "code": "RUNTIME_HEARTBEAT_NOT_FOUND",
                "message": f"No RuntimeHeartbeat has been accepted for {runtime_id}",
            }
        }), 404
    return jsonify(heartbeat_status_from_summary(summary)), 200


@app.route("/api/telemetry/stats", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def stats():
    """Return TelemetryIngestService statistics."""
    svc = _get_service()
    return jsonify(svc.stats()), 200


@app.route("/api/telemetry/runtime-summaries", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def runtime_summaries():
    """Return telemetry-owned runtime status summaries for BFF read paths."""
    svc = _get_service()
    summaries = svc.list_runtime_summaries(tenant_id=request_tenant_id())
    return jsonify({"summaries": summaries, "count": len(summaries)}), 200


@app.route("/api/telemetry/runtime-summaries/<runtime_id>", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def runtime_summary(runtime_id: str):
    """Return one telemetry-owned runtime status summary."""
    svc = _get_service()
    summary = svc.get_runtime_summary(runtime_id, tenant_id=request_tenant_id())
    if summary is None:
        return jsonify({
            "error": {
                "code": "RUNTIME_SUMMARY_NOT_FOUND",
                "message": f"No runtime summary for {runtime_id}",
            }
        }), 404
    return jsonify(summary), 200


@app.route("/api/telemetry/events/<event_id>", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def accepted_event(event_id: str):
    """Return one exact owner-accepted event without summary races."""

    svc = _get_service()
    event = svc.get_accepted_event(event_id, tenant_id=request_tenant_id())
    if event is None:
        return jsonify({
            "error": {
                "code": "TELEMETRY_EVENT_NOT_FOUND",
                "message": f"No accepted telemetry event for {event_id}",
            }
        }), 404
    return jsonify(event), 200


@app.route("/api/telemetry/trade-episodes", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def trade_episodes():
    """Return trade episode projections with filters, sorting, and pagination."""
    persona_id = request.args.get("persona_id")
    cursor = request.args.get("cursor")
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20
    environment = request.args.get("environment")
    strategy_id = request.args.get("strategy_id")
    instrument_id = request.args.get("instrument_id")
    side = request.args.get("side")
    status = request.args.get("status")
    outcome = request.args.get("outcome")
    reflection_state = request.args.get("reflection_state")
    coverage_state = request.args.get("coverage_state")
    start_time = request.args.get("start_time")
    end_time = request.args.get("end_time")

    svc = _get_service()
    result = svc.list_trade_episode_projections(
        persona_id=persona_id,
        cursor=cursor,
        limit=limit,
        environment=environment,
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        side=side,
        status=status,
        outcome=outcome,
        reflection_state=reflection_state,
        coverage_state=coverage_state,
        start_time=start_time,
        end_time=end_time,
        tenant_id=request_tenant_id(),
    )
    return jsonify(result), 200


@app.route("/api/telemetry/trade-episodes/<trade_episode_id>", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def trade_episode(trade_episode_id: str):
    """Return a single trade episode projection, supporting as-of query parameters."""
    as_of = request.args.get("as_of")
    as_of_sequence_str = request.args.get("as_of_sequence")
    as_of_sequence = None
    if as_of_sequence_str is not None:
        try:
            as_of_sequence = int(as_of_sequence_str)
        except ValueError:
            return jsonify({"error": {"code": "INVALID_AS_OF_SEQUENCE", "message": "as_of_sequence must be an integer"}}), 400

    svc = _get_service()
    projection = svc.get_trade_episode_projection(
        trade_episode_id,
        as_of=as_of,
        as_of_sequence=as_of_sequence,
        tenant_id=request_tenant_id(),
    )
    if projection is None:
        return jsonify({
            "error": {
                "code": "TRADE_EPISODE_NOT_FOUND",
                "message": f"No trade episode projection found for {trade_episode_id}",
            }
        }), 404
    return jsonify(projection), 200


@app.route("/api/telemetry/dlq", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def dlq_entries():
    """Return dead-letter queue entries.

    Query params:
      tag   — filter by DLQ tag
      limit — max entries (default 100)
    """
    tag = request.args.get("tag")
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100

    svc = _get_service()
    entries = svc.get_dlq_entries(
        tag_filter=tag,
        limit=limit,
        tenant_id=request_tenant_id(),
    )
    return jsonify({"entries": entries, "count": len(entries)}), 200


@app.route("/api/telemetry/replay", methods=["POST"])
@require_telemetry_authority(("operator", "admin"))
def replay_dlq():
    """Replay DLQ entries through the full ingest path.

    Query params:
      tag — if provided, replay only entries with this tag.
            Default: replay only write-failure entries (safe).

    Returns { replayed: <count> }.
    """
    tag = request.args.get("tag") or None
    svc = _get_service()
    try:
        count = _run_async(
            svc.replay_dlq(
                tag_filter=tag,
                tenant_id=request_tenant_id(),
            )
        )
    except Exception as exc:
        log.exception("Error during DLQ replay")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    return jsonify({"replayed": count}), 200


@app.route("/api/telemetry/lineage/runtime-bindings/<binding_id>/projection", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def runtime_binding_projection(binding_id: str):
    """Return the derived-only runtime binding lineage projection."""
    return _lineage_query_response(
        "runtime_binding_projection",
        tenant_id=request_tenant_id(),
        binding_id=binding_id,
    )


@app.route("/api/telemetry/lineage/capital-pools/<pool_id>/projection", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def capital_pool_projection(pool_id: str):
    """Return the derived-only capital pool lineage projection."""
    return _lineage_query_response(
        "capital_pool_projection",
        tenant_id=request_tenant_id(),
        pool_id=pool_id,
    )


@app.route("/api/telemetry/lineage/events/<event_id>/trace", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def telemetry_event_trace(event_id: str):
    """Return the derived-only telemetry event lineage trace."""
    return _lineage_query_response(
        "telemetry_event_trace",
        tenant_id=request_tenant_id(),
        event_id=event_id,
    )


@app.route("/api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def source_runtime_telemetry_trace(trace_id: str):
    """Return the operator-facing source-to-runtime-to-telemetry trace."""
    return _lineage_query_response(
        "source_runtime_telemetry_trace",
        tenant_id=request_tenant_id(),
        trace_id=trace_id,
    )


@app.route("/api/telemetry/lineage/plans/<plan_id>/forensic-trace", methods=["GET"])
@require_telemetry_authority(("service", "operator", "reviewer", "admin"))
def forensic_plan_trace(plan_id: str):
    """Return the rollback-aware forensic lineage trace for one plan."""
    return _lineage_query_response(
        "forensic_plan_trace",
        tenant_id=request_tenant_id(),
        plan_id=plan_id,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    startup()

    import atexit
    atexit.register(shutdown)

    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
