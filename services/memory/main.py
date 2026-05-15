"""HTTP facade for the canonical institutional memory store."""
from __future__ import annotations

import os
import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

from .institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryError,
    InstitutionalMemoryStore,
    build_institutional_memory_store,
)


def _split_csv_values(values: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    for value in values or []:
        normalized.extend(part.strip() for part in str(value).split(",") if part.strip())
    return normalized

app = FastAPI(title="Pantheon Memory Service", version="0.1.0")
STORE_BACKEND = os.getenv("PANTHEON_MEMORY_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("memory")
register_fastapi_health_routes(
    app,
    "memory",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    details=lambda: {
        "store_path": str(_store_path()),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
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


def _governance_authz_url() -> str:
    base = (
        os.getenv("PANTHEON_GOVERNANCE_AUTHZ_URL")
        or os.getenv("PANTHEON_GOVERNANCE_API_URL")
        or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/api/governance/authz/check"


def _authorize_memory_retrieve(
    *,
    actor_id: str,
    actor_roles: List[str],
    resource: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    request_payload = {
        "action": "memory.retrieve",
        "actor_id": actor_id,
        "actor_roles": actor_roles,
        "resource": resource,
        "context": context,
    }
    if os.getenv("PANTHEON_MEMORY_AUTHZ_MODE", "").strip().lower() == "local":
        from services.governance.authz import evaluate_authz_request

        decision = evaluate_authz_request(**request_payload)
        return {"allowed": bool(decision.get("allowed")), "reason": str(decision.get("reason") or "unknown")}

    url = _governance_authz_url()
    if not url:
        return {"allowed": False, "reason": "governance_authz_unconfigured"}

    body = json.dumps(request_payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("PANTHEON_GOVERNANCE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("PANTHEON_MEMORY_AUTHZ_TIMEOUT", "2"))) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.HTTPError, OSError, ValueError, json.JSONDecodeError):
        return {"allowed": False, "reason": "governance_authz_unavailable"}
    return {"allowed": bool(payload.get("allowed")), "reason": str(payload.get("reason") or "unknown")}


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


@app.get("/api/memory/retrieve")
async def retrieve_memory(
    actor_id: str = Query(..., min_length=1),
    actor_roles: List[str] = Query(...),
    session_id: str = Query(..., min_length=1),
    persona_id: Optional[str] = Query(default=None),
    session_persona_id: Optional[str] = Query(default=None),
    scope: str = Query(default="both", pattern="^(institutional|persona|both)$"),
    query: str = Query(default=""),
    knowledge_type: Optional[str] = Query(default=None),
    scope_filter: Optional[str] = Query(default=None),
    tags: Optional[List[str]] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
):
    roles = _split_csv_values(actor_roles)
    tag_values = _split_csv_values(tags)
    resource = {"scope": scope}
    if persona_id:
        resource["persona_id"] = persona_id
    context = {"session_id": session_id}
    if session_persona_id:
        context["session_persona_id"] = session_persona_id

    decision = _authorize_memory_retrieve(
        actor_id=actor_id,
        actor_roles=roles,
        resource=resource,
        context=context,
    )
    if not decision.get("allowed"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "memory_retrieve_unauthorized",
                "reason": str(decision.get("reason") or "unknown"),
            },
        )

    hits = []
    if scope in {"institutional", "both"}:
        for hit in _store().retrieve(
            query=query,
            knowledge_type=knowledge_type,
            scope_filter=scope_filter,
            tags=tag_values,
            limit=limit,
        ):
            updated = _store().mark_reused(hit.entry.entry_id)
            hits.append(
                {
                    "type": "institutional",
                    "relevance_score": hit.relevance_score,
                    "entry": updated.to_dict(),
                }
            )

    return {
        "hits": hits[:limit],
        "count": len(hits[:limit]),
        "scope": scope,
        "authz": {
            "allowed": True,
            "reason": str(decision.get("reason") or "authorized"),
            "policy_version": "governance-authz.v1",
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8086"))
    uvicorn.run("services.memory.main:app", host="0.0.0.0", port=port, reload=False)
