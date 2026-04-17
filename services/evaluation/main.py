"""
services/evaluation — minimal HTTP service.

Exposes the evaluation library as a deployable service.

Routes
------
  GET  /__health__   liveness probe
  POST /api/evaluation/results   submit a pre-computed EvaluatorResult
  GET  /api/evaluation/results/{result_id}   read a result
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Pantheon Evaluation Service", version="0.1.0")

_store: Dict[str, Any] = {}


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "evaluation"}


@app.post("/api/evaluation/results", status_code=201)
async def submit_result(payload: Dict[str, Any]):
    result_id = payload.get("result_id") or f"eval-{uuid.uuid4().hex[:12]}"
    _store[result_id] = payload
    return {"result_id": result_id}


@app.get("/api/evaluation/results/{result_id}")
async def get_result(result_id: str):
    entry = _store.get(result_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="result not found")
    return entry


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8084"))
    uvicorn.run("services.evaluation.main:app", host="0.0.0.0", port=port, reload=False)
