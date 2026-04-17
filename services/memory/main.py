"""
services/memory — minimal HTTP service.

Exposes the institutional memory store as a deployable service.

Routes
------
  GET  /__health__              liveness probe
  POST /api/memory/entries      store a memory entry
  GET  /api/memory/entries      list entries (optional ?scope= filter)
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query

app = FastAPI(title="Pantheon Memory Service", version="0.1.0")

_entries: List[Dict[str, Any]] = []


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "memory"}


@app.post("/api/memory/entries", status_code=201)
async def store_entry(payload: Dict[str, Any]):
    entry_id = payload.get("entry_id") or f"mem-{uuid.uuid4().hex[:12]}"
    payload.setdefault("entry_id", entry_id)
    _entries.append(payload)
    return {"entry_id": entry_id}


@app.get("/api/memory/entries")
async def list_entries(scope: Optional[str] = Query(default=None)):
    results = _entries if scope is None else [e for e in _entries if e.get("scope") == scope]
    return {"entries": results, "count": len(results)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8086"))
    uvicorn.run("services.memory.main:app", host="0.0.0.0", port=port, reload=False)
