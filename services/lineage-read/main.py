"""
services/lineage-read — Lineage Read Service

Minimal deployable HTTP service that implements the lineage read model
described in LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md §3.2.

Route summary
-------------
GET   /api/v1/lineage
    Query lineage records.  Optional query params:
      artifact_id  — filter by artifact id
      run_id       — filter by experiment run id
      strategy_id  — filter by strategy id

GET   /api/v1/lineage/{id}
    Retrieve a single lineage record by its id.

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

PORT
    HTTP listen port (default 8094).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.getenv("LINEAGE_DATA_DIR", "/tmp/pantheon/lineage")
os.makedirs(DATA_DIR, exist_ok=True)
STORE_PATH = Path(DATA_DIR) / "lineage.json"
PORT = int(os.getenv("PORT", "8094"))

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/__health__")
def health() -> Dict[str, str]:
    return {"status": "ok"}


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
