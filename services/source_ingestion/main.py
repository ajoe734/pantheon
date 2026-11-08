"""Deployable source-ingest service composition root and entrypoint.

Assembles the Source Ingestion runtime, registers health and version probes,
and mounts the five route family routers.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from services.foundation.health import register_fastapi_health_routes

from .routers import (
    create_catalog_controller_router,
    create_ingest_operations_router,
    create_management_router,
    create_observability_router,
    create_proposals_router,
)
from .runtime import SourceIngestionRuntime, create_runtime


def create_app(runtime: SourceIngestionRuntime | None = None) -> FastAPI:
    """Create and compose the FastAPI application instance with all routers."""
    if runtime is None:
        runtime = create_runtime()

    app = FastAPI(title="Pantheon Source Ingest Service", version="0.1.0")

    register_fastapi_health_routes(
        app,
        "pantheon-source-ingest",
        dependencies=lambda: {
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
            "source_freshness": runtime._source_freshness_readiness(),
        },
        metrics=lambda: runtime._source_runtime_metrics(),
        details=lambda: {
            "store_path": str(runtime.SCHEDULE_STORE_PATH),
            "connector_store_path": str(runtime.CONNECTOR_STORE_PATH),
            "source_evidence_path": str(runtime.SOURCE_EVIDENCE_STORE_PATH),
            "market_data_storage_root": str(runtime.MARKET_DATA_STORAGE_ROOT),
            "latest_market_snapshot_path": str(runtime.LATEST_MARKET_SNAPSHOT_PATH),
            "market_snapshot_max_closes": runtime.MARKET_SNAPSHOT_MAX_CLOSES,
            "dlq_path": str(runtime.DLQ_STORE_PATH),
            "audit_path": str(runtime.AUDIT_STORE_PATH),
            "scheduler_max_concurrency": runtime.SCHEDULER_MAX_CONCURRENCY,
            "frontier_max_attempts": runtime.FRONTIER_MAX_ATTEMPTS,
            "frontier_backoff_seconds": runtime.FRONTIER_BACKOFF_SECONDS,
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
        },
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        runtime_metrics = runtime._source_runtime_metrics()
        return {
            "status": "ok",
            "service": "pantheon-source-ingest",
            "store_path": str(runtime.SCHEDULE_STORE_PATH),
            "connector_store_path": str(runtime.CONNECTOR_STORE_PATH),
            "source_evidence_path": str(runtime.SOURCE_EVIDENCE_STORE_PATH),
            "market_data_storage_root": str(runtime.MARKET_DATA_STORAGE_ROOT),
            "latest_market_snapshot_path": str(runtime.LATEST_MARKET_SNAPSHOT_PATH),
            "dlq_path": str(runtime.DLQ_STORE_PATH),
            "audit_path": str(runtime.AUDIT_STORE_PATH),
            **runtime_metrics,
            "scheduler_max_concurrency": runtime.SCHEDULER_MAX_CONCURRENCY,
            "frontier_max_attempts": runtime.FRONTIER_MAX_ATTEMPTS,
            "frontier_backoff_seconds": runtime.FRONTIER_BACKOFF_SECONDS,
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
            "posture_alert_count": runtime.PRODUCTION_POSTURE.alert_count(),
        }

    app.include_router(create_ingest_operations_router(runtime))
    app.include_router(create_catalog_controller_router(runtime))
    app.include_router(create_proposals_router(runtime))
    app.include_router(create_observability_router(runtime))
    app.include_router(create_management_router(runtime))

    return app


# Default singleton runtime and application composition
runtime = create_runtime()
app = create_app(runtime)
