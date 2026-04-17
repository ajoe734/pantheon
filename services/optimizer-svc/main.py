"""
services/optimizer-svc — minimal HTTP service.

Exposes the portfolio synthesis library as a deployable service.

Routes
------
  GET  /__health__                       liveness probe
  POST /api/optimizer/synthesize         run a portfolio synthesis from proposals
  GET  /api/optimizer/policies/{policy_id}  read a produced AllocationPolicyArtifact
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Pantheon Optimizer Service", version="0.1.0")

_policies: Dict[str, Any] = {}


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "optimizer-svc"}


@app.post("/api/optimizer/synthesize", status_code=201)
async def synthesize(payload: Dict[str, Any]):
    policy_id = f"policy-{uuid.uuid4().hex[:12]}"
    _policies[policy_id] = {"policy_id": policy_id, "input": payload, "status": "pending"}
    return {"policy_id": policy_id, "status": "pending"}


@app.get("/api/optimizer/policies/{policy_id}")
async def get_policy(policy_id: str):
    entry = _policies.get(policy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="policy not found")
    return entry


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8088"))
    uvicorn.run(app, host="0.0.0.0", port=port)
