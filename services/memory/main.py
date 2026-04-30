"""HTTP facade for the canonical institutional memory store."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query

from services.foundation.health import register_fastapi_health_routes

from .institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryError,
    InstitutionalMemoryStore,
    build_institutional_memory_store,
)

app = FastAPI(title="Pantheon Memory Service", version="0.1.0")
STORE_BACKEND = os.getenv("PANTHEON_MEMORY_STORE_BACKEND", "json").strip().lower() or "json"
register_fastapi_health_routes(
    app,
    "memory",
    details=lambda: {"store_path": str(_store_path()), "store_backend": STORE_BACKEND},
)


def _store_path() -> Path:
    explicit = os.getenv("PANTHEON_MEMORY_STORE", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("PANTHEON_MEMORY_DATA_DIR", "/tmp/pantheon/memory"))
    return data_dir / "institutional_memory_entries.json"


def _store() -> InstitutionalMemoryStore:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_institutional_memory_store(path)


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "memory"}


@app.post("/api/memory/entries", status_code=201)
async def store_entry(payload: Dict[str, Any]):
    try:
        entry = InstitutionalMemoryEntry.from_dict(payload)
        saved = _store().create(entry)
    except (InstitutionalMemoryError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_entry", "message": str(exc)}) from exc
    return {"entry_id": saved.entry_id}


@app.get("/api/memory/entries")
async def list_entries(
    knowledge_type: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    scope_filter: Optional[str] = Query(default=None),
    contributing_persona_id: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
):
    entries = _store().list(
        knowledge_type=knowledge_type,
        scope=scope,
        scope_filter=scope_filter,
        contributing_persona_id=contributing_persona_id,
        active_only=active_only,
    )
    return {"entries": [entry.to_dict() for entry in entries], "count": len(entries)}


@app.get("/api/memory/entries/{entry_id}")
async def get_entry(entry_id: str):
    entry = _store().get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "entry_not_found", "entry_id": entry_id})
    return entry.to_dict()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8086"))
    uvicorn.run("services.memory.main:app", host="0.0.0.0", port=port, reload=False)
