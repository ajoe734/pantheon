"""
services/feedback — minimal HTTP service.

Exposes the feedback library as a deployable service.

Routes
------
  GET  /__health__                liveness probe
  POST /api/feedback/events       ingest a feedback event
  GET  /api/feedback/events       list ingested events
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI

app = FastAPI(title="Pantheon Feedback Service", version="0.1.0")

_events: List[Dict[str, Any]] = []


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "feedback"}


@app.post("/api/feedback/events", status_code=201)
async def ingest_event(payload: Dict[str, Any]):
    event_id = payload.get("event_id") or f"fb-{uuid.uuid4().hex[:12]}"
    payload.setdefault("event_id", event_id)
    _events.append(payload)
    return {"event_id": event_id}


@app.get("/api/feedback/events")
async def list_events():
    return {"events": _events, "count": len(_events)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8085"))
    uvicorn.run("services.feedback.main:app", host="0.0.0.0", port=port, reload=False)
