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
  POST /api/registry/strategy-specs
  GET  /api/registry/strategy-specs/{registry_id}
  GET  /api/registry/strategies/{strategy_id}/strategy-specs
  POST /api/registry/strategy-specs/{registry_id}/advance
  POST /api/registry/strategy-artifacts
  GET  /api/registry/strategy-artifacts/{registry_id}
  GET  /api/registry/strategies/{strategy_id}/strategy-artifacts
  POST /api/registry/strategy-artifacts/{registry_id}/mutate
  POST /api/registry/strategy-artifacts/{registry_id}/advance
  GET  /health

Additional
----------
  GET  /__health__   Docker-compatible liveness probe
"""
from __future__ import annotations

import os

from services.foundation.health import register_fastapi_health_routes

from .service import app
from .storage import RegistryStore, get_store


def _registry_owner_dependency() -> dict:
    """Report the real selected-owner backend's reachability.

    Reviewer finding 8: readiness previously registered no dependency check
    at all, so /readyz always returned 200/ready=true/dependencies={}
    regardless of whether the selected owner store was actually reachable —
    including when it silently resolved to the in-memory test double. This
    performs a genuine connection probe against the durable Postgres backend
    (a fresh, short-lived connection + ``SELECT 1``, never the cached
    application pool) each time readiness is checked, and explicitly reports
    when the selected backend is the memory test double rather than treating
    that the same as a reachable production owner.
    """
    try:
        store = get_store()
    except Exception as exc:
        return {"status": "error", "detail": f"registry owner store selection failed: {exc}"}
    if isinstance(store, RegistryStore):
        # Explicit, documented test/dev-only backend — never silently
        # reported the same as a reachable durable production owner.
        return {"status": "degraded", "backend": "memory", "detail": "in-memory test double selected"}
    entries = getattr(store, "_entries", None)
    if entries is None or not hasattr(entries, "_connect"):
        return {"status": "error", "detail": "registry postgres store has no connection handle"}
    try:
        with entries._connect() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        return {"status": "error", "detail": f"registry postgres connection failed: {exc}"}
    return {"status": "ok", "backend": "postgres"}


register_fastapi_health_routes(
    app, "registry", dependencies=lambda: {"registry_owner": _registry_owner_dependency()},
)


@app.get("/__health__")
async def health_docker():
    return {"status": "ok", "service": "registry"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8087"))
    uvicorn.run("services.registry.main:app", host="0.0.0.0", port=port, reload=False)
