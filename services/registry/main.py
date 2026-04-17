"""
services/registry — deployable HTTP service entry point.

Mounts the full registry API from service.py and adds /__health__ for
Docker healthcheck compatibility.

Routes (from service.py)
------
  POST /api/registry/entries
  GET  /api/registry/entries/{registry_id}
  GET  /api/registry/strategies/{strategy_id}/entries
  POST /api/registry/entries/{registry_id}/advance
  GET  /api/registry/strategies/{strategy_id}/latest-approved
  GET  /api/registry/strategies/{strategy_id}/deployment-view
  PUT  /api/registry/entries/{registry_id}/deployment-summary
  GET  /health

Additional
----------
  GET  /__health__   Docker-compatible liveness probe
"""
from __future__ import annotations

import os

from .service import app


@app.get("/__health__")
async def health_docker():
    return {"status": "ok", "service": "registry"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8087"))
    uvicorn.run("services.registry.main:app", host="0.0.0.0", port=port, reload=False)
