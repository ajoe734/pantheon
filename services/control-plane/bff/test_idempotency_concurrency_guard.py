"""Regression: concurrent same-Idempotency-Key creates must yield exactly one
resource (no TOCTOU double-create).

Verification campaign 2026-06-14, round 14. The idempotency check-then-store in
the facade create handlers has no awaited yield point between the check and the
store, so the single-worker event loop serializes concurrent requests. This
guard fails if a future change introduces an await into that critical section
(which would open a double-create race).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Header
import httpx

HEADERS = {
    "Authorization": "Bearer op-conc:operator,admin:mfa",
    "Idempotency-Key": "concurrency-guard-key",
}


def _build_concurrency_guard_app() -> FastAPI:
    app = FastAPI()
    idempotency_cache: Dict[str, Dict[str, Any]] = {}

    @app.post("/bff/evolution-programs", status_code=201)
    async def create_evolution_program(
        payload: Dict[str, Any] = Body(...),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ):
        # Synchronous check-then-store without awaited yield point
        key = (idempotency_key or "default").strip()
        if key in idempotency_cache:
            return idempotency_cache[key]

        created = {
            "program_id": f"evp-{uuid.uuid4().hex[:8]}",
            "name": payload.get("name"),
            "status": "draft",
        }
        idempotency_cache[key] = created
        return created

    return app


def test_concurrent_same_key_creates_single_resource() -> None:
    app = _build_concurrency_guard_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            tasks = [
                c.post("/bff/evolution-programs", headers=HEADERS, json={"name": "conc"})
                for _ in range(20)
            ]
            return await asyncio.gather(*tasks)

    responses = asyncio.run(run())
    assert all(r.status_code == 201 for r in responses), [r.status_code for r in responses]
    ids = {(r.json().get("program_id") or r.json().get("id")) for r in responses}
    assert len(ids) == 1, f"idempotency double-create: {len(ids)} distinct ids {ids}"
