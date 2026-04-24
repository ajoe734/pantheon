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
    Returns 202 with { ingested, rejected } counts.

GET   /api/telemetry/stats
    Service statistics (buffer, writer, DLQ, backpressure).

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

TELEMETRY_BUFFER_BACKEND
    "memory" (default) or "redis".

TELEMETRY_BUFFER_REDIS_URL
    Redis URL when TELEMETRY_BUFFER_BACKEND=redis.

TELEMETRY_BATCH_SIZE
    Max events per write batch (default 500).

TELEMETRY_BATCH_INTERVAL
    Max seconds before flushing a partial batch (default 1.0).

TELEMETRY_MAX_RETRIES
    Max retries for transient write failures (default 5).

LINEAGE_READ_CORPUS_PATH
    Optional path to the LIN-001A lineage benchmark corpus used to bootstrap
    the lineage read HTTP surface. Defaults to
    services/registry/lineage/lin001a_benchmark_corpus.json.

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
from pathlib import Path

from flask import Flask, jsonify, request

from .ingest_svc import TelemetryIngestService, build_postgres_write_fn
from .lineage_read import LineageReadService
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


def _build_service() -> TelemetryIngestService:
    """Instantiate TelemetryIngestService with production Postgres and RuntimeBinding wiring."""
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
    buffer_backend = os.getenv("TELEMETRY_BUFFER_BACKEND", "memory")
    redis_url = os.getenv("TELEMETRY_BUFFER_REDIS_URL", "redis://localhost:6379/0")

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

    Path(storage_dir).mkdir(parents=True, exist_ok=True)

    return TelemetryIngestService(
        schema_path=schema_path if Path(schema_path).exists() else None,
        storage_dir=storage_dir,
        buffer_backend=buffer_backend,
        buffer_redis_url=redis_url,
        batch_size=batch_size,
        batch_interval=batch_interval,
        max_retries=max_retries,
        write_fn=write_fn,
        binding_store=binding_store,
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


def _lineage_query_response(query_family: str, **params):
    """Execute a lineage query family and map service-level errors to HTTP."""
    try:
        result = _get_lineage_service().query(query_family, **params)
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
    _svc = _build_service()
    _lineage_svc = _build_lineage_service()
    _run_async(_svc.start())
    log.info("TelemetryIngestService started")


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
def ingest_event():
    """Ingest a single telemetry event.

    Returns 202 on success, 400 if the event fails schema or evidence validation.
    """
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Request body must be a JSON object"}}), 400

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
def ingest_batch():
    """Ingest a batch of telemetry events.

    Body: { "events": [ <event>, ... ] }
    Returns 202 with { ingested, rejected } counts.
    """
    body = request.get_json(force=True) or {}
    events = body.get("events")
    if not isinstance(events, list):
        return jsonify({"error": {"code": "INVALID_BODY", "message": "Body must have an 'events' list"}}), 400

    svc = _get_service()
    try:
        result = _run_async(svc.ingest_batch(events))
    except Exception as exc:
        log.exception("Unexpected error during batch ingest")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    return jsonify(result), 202


@app.route("/api/telemetry/stats", methods=["GET"])
def stats():
    """Return TelemetryIngestService statistics."""
    svc = _get_service()
    return jsonify(svc.stats()), 200


@app.route("/api/telemetry/dlq", methods=["GET"])
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
    entries = svc.get_dlq_entries(tag_filter=tag, limit=limit)
    return jsonify({"entries": entries, "count": len(entries)}), 200


@app.route("/api/telemetry/replay", methods=["POST"])
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
        count = _run_async(svc.replay_dlq(tag_filter=tag))
    except Exception as exc:
        log.exception("Error during DLQ replay")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500

    return jsonify({"replayed": count}), 200


@app.route("/api/telemetry/lineage/runtime-bindings/<binding_id>/projection", methods=["GET"])
def runtime_binding_projection(binding_id: str):
    """Return the derived-only runtime binding lineage projection."""
    return _lineage_query_response(
        "runtime_binding_projection",
        binding_id=binding_id,
    )


@app.route("/api/telemetry/lineage/capital-pools/<pool_id>/projection", methods=["GET"])
def capital_pool_projection(pool_id: str):
    """Return the derived-only capital pool lineage projection."""
    return _lineage_query_response(
        "capital_pool_projection",
        pool_id=pool_id,
    )


@app.route("/api/telemetry/lineage/events/<event_id>/trace", methods=["GET"])
def telemetry_event_trace(event_id: str):
    """Return the derived-only telemetry event lineage trace."""
    return _lineage_query_response(
        "telemetry_event_trace",
        event_id=event_id,
    )


@app.route("/api/telemetry/lineage/plans/<plan_id>/forensic-trace", methods=["GET"])
def forensic_plan_trace(plan_id: str):
    """Return the rollback-aware forensic lineage trace for one plan."""
    return _lineage_query_response(
        "forensic_plan_trace",
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
