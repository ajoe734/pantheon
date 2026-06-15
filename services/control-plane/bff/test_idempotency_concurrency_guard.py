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
import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

import main as bff_main  # noqa: E402

HEADERS = {
    "Authorization": "Bearer op-conc:operator,admin:mfa",
    "Idempotency-Key": "concurrency-guard-key",
}


def test_concurrent_same_key_creates_single_resource() -> None:
    import httpx

    async def run():
        transport = httpx.ASGITransport(app=bff_main.app)
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
