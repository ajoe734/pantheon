"""
services/lineage-read — deployable HTTP wrapper for the LIN-002 read engine.

This process exposes the production lineage projection surface backed by
services.telemetry.lineage_read.LineageReadService.  The small JSON edge store
routes are retained for compatibility with older local smoke tests, but the
service health and /api/v1/lineage/* projection routes are backed by the
canonical LIN-001A corpus loader and iterative graph traverser.

Route summary
-------------
GET   /api/v1/lineage
    Query lineage records.  Optional query params:
      artifact_id  — filter by artifact id
      run_id       — filter by experiment run id
      strategy_id  — filter by strategy id

GET   /api/v1/lineage/{id}
    Retrieve a single lineage record by its id.

GET   /api/v1/lineage/runtime-bindings/{binding_id}/projection
    Return the derived-only runtime binding lineage projection.

GET   /api/v1/lineage/capital-pools/{pool_id}/projection
    Return the derived-only capital pool lineage projection.

GET   /api/v1/lineage/events/{event_id}/trace
    Return the derived-only telemetry event trace.

GET   /api/v1/lineage/traces/{trace_id}/source-runtime-telemetry
    Return the derived-only operator trace from source through runtime,
    telemetry, broker/order lifecycle, and evolution refs.

GET   /api/v1/lineage/plans/{plan_id}/forensic-trace
    Return the rollback-aware forensic plan trace.

POST  /api/v1/lineage
    Write a lineage edge record.
    Body: LineageWriteRequest.
    Returns 201 on success, 422 on validation failure.

GET   /__health__
    Liveness probe.

Environment variables
---------------------
LINEAGE_DATA_DIR
    Directory for the on-disk lineage store.
    Defaults to /tmp/pantheon/lineage.

LINEAGE_READ_CORPUS_PATH
    Path to the LIN-001A lineage benchmark corpus used to bootstrap the read
    model. Defaults to services/registry/lineage/lin001a_benchmark_corpus.json.

PORT
    HTTP listen port (default 8094).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.telemetry.lineage_read import LineageReadService
from services.foundation.health import register_fastapi_health_routes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.getenv("LINEAGE_DATA_DIR", "/tmp/pantheon/lineage")
os.makedirs(DATA_DIR, exist_ok=True)
STORE_PATH = Path(DATA_DIR) / "lineage.json"
PORT = int(os.getenv("PORT", "8094"))
DEFAULT_CORPUS_PATH = (
    REPO_ROOT / "services" / "registry" / "lineage" / "lin001a_benchmark_corpus.json"
)
CORPUS_PATH = Path(os.getenv("LINEAGE_READ_CORPUS_PATH", str(DEFAULT_CORPUS_PATH))).resolve()

_lineage_service: LineageReadService | None = None
_lineage_load_error: str | None = None

# ---------------------------------------------------------------------------
# In-process JSON store (mirrors pattern used by incidents / postmortems)
# ---------------------------------------------------------------------------

_lock = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load() -> List[Dict[str, Any]]:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text())
        except Exception:
            return []
    return []


def _save(records: List[Dict[str, Any]]) -> None:
    STORE_PATH.write_text(json.dumps(records, indent=2))


def _load_lineage_service() -> LineageReadService:
    if not CORPUS_PATH.exists():
        raise RuntimeError(f"LINEAGE_READ_CORPUS_PATH does not exist: {CORPUS_PATH}")

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    service = LineageReadService()
    service.load_corpus(corpus)
    return service


def _get_lineage_service() -> LineageReadService:
    if _lineage_service is None:
        detail = _lineage_load_error or f"lineage corpus not loaded from {CORPUS_PATH}"
        raise HTTPException(status_code=503, detail=detail)
    return _lineage_service


def _startup_lineage_service() -> None:
    global _lineage_service, _lineage_load_error
    try:
        _lineage_service = _load_lineage_service()
        _lineage_load_error = None
        log.info("LineageReadService loaded corpus from %s", CORPUS_PATH)
    except Exception as exc:  # noqa: BLE001
        _lineage_service = None
        _lineage_load_error = str(exc)
        log.error("LineageReadService failed to load corpus from %s: %s", CORPUS_PATH, exc)


def _lineage_query_response(query_family: str, **params: str) -> Dict[str, Any]:
    try:
        result = _get_lineage_service().query(query_family, **params)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    node_not_found = any(
        marker.get("code") == "node_not_found"
        for marker in result.get("conflict_markers", [])
    )
    if node_not_found:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LINEAGE_TARGET_NOT_FOUND",
                "query_family": query_family,
                "params": params,
            },
        )
    return result


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LineageWriteRequest(BaseModel):
    source_type: str = Field(..., description="Type of the source node, e.g. SourceRecord, StrategySpec")
    source_id: str = Field(..., description="ID of the source node")
    target_type: str = Field(..., description="Type of the target node, e.g. ExperimentRun, CandidateArtifact")
    target_id: str = Field(..., description="ID of the target node")
    artifact_id: Optional[str] = Field(None, description="Denormalized artifact reference for fast lookup")
    run_id: Optional[str] = Field(None, description="Denormalized experiment run reference for fast lookup")
    strategy_id: Optional[str] = Field(None, description="Denormalized strategy reference for fast lookup")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Arbitrary additional context")


class LineageRecord(BaseModel):
    id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    artifact_id: Optional[str] = None
    run_id: Optional[str] = None
    strategy_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: str


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pantheon Lineage Read Service",
    version="0.1.0",
    description=(
        "Canonical lineage read model service.  Assembles and exposes the "
        "end-to-end lineage chain across Pantheon domains.  Implements the "
        "read path defined in LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md §3.2."
    ),
)
_startup_lineage_service()
register_fastapi_health_routes(
    app,
    "lineage-read",
    dependencies=lambda: {
        "lineage_model": {
            "status": "ok" if _lineage_service is not None else "error",
            "loaded": _lineage_service is not None,
            "detail": _lineage_load_error,
        }
    },
    metrics=lambda: {"lineage_record_count": len(_load())},
    details=lambda: {"corpus_path": str(CORPUS_PATH), "data_dir": DATA_DIR},
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/__health__")
def health() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "ok" if _lineage_service is not None else "degraded",
        "service": "lineage-read",
        "corpus_path": str(CORPUS_PATH),
        "lineage_model_loaded": _lineage_service is not None,
    }
    if _lineage_load_error:
        payload["detail"] = _lineage_load_error
    if _lineage_service is None:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.get("/api/v1/lineage", response_model=List[LineageRecord])
def list_lineage(
    artifact_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
) -> List[LineageRecord]:
    with _lock:
        records = _load()

    results = records
    if artifact_id is not None:
        results = [r for r in results if r.get("artifact_id") == artifact_id]
    if run_id is not None:
        results = [r for r in results if r.get("run_id") == run_id]
    if strategy_id is not None:
        results = [r for r in results if r.get("strategy_id") == strategy_id]

    return [LineageRecord(**r) for r in results]


@app.get("/api/v1/lineage/{record_id}", response_model=LineageRecord)
def get_lineage(record_id: str) -> LineageRecord:
    with _lock:
        records = _load()

    for r in records:
        if r.get("id") == record_id:
            return LineageRecord(**r)

    raise HTTPException(status_code=404, detail=f"Lineage record {record_id!r} not found")


@app.get("/api/v1/lineage/runtime-bindings/{binding_id}/projection")
def runtime_binding_projection(binding_id: str) -> Dict[str, Any]:
    return _lineage_query_response("runtime_binding_projection", binding_id=binding_id)


@app.get("/api/v1/lineage/capital-pools/{pool_id}/projection")
def capital_pool_projection(pool_id: str) -> Dict[str, Any]:
    return _lineage_query_response("capital_pool_projection", pool_id=pool_id)


@app.get("/api/v1/lineage/events/{event_id}/trace")
def telemetry_event_trace(event_id: str) -> Dict[str, Any]:
    return _lineage_query_response("telemetry_event_trace", event_id=event_id)


@app.get("/api/v1/lineage/traces/{trace_id}/source-runtime-telemetry")
def source_runtime_telemetry_trace(trace_id: str) -> Dict[str, Any]:
    return _lineage_query_response("source_runtime_telemetry_trace", trace_id=trace_id)


@app.get("/api/v1/lineage/plans/{plan_id}/forensic-trace")
def forensic_plan_trace(plan_id: str) -> Dict[str, Any]:
    return _lineage_query_response("forensic_plan_trace", plan_id=plan_id)


@app.post("/api/v1/lineage", response_model=LineageRecord, status_code=201)
def write_lineage(body: LineageWriteRequest) -> LineageRecord:
    record = LineageRecord(
        id=str(uuid.uuid4()),
        created_at=_utc_now(),
        **body.model_dump(),
    )
    with _lock:
        records = _load()
        records.append(record.model_dump())
        _save(records)

    log.info("lineage edge written: %s -> %s [id=%s]", record.source_id, record.target_id, record.id)
    return record


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
