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
from typing import Any, Dict, Optional

from services.control_plane.bff import main as bff_main

from services.evolution.program_service import ProgramService  # noqa: E402
from services.evolution.program_store import JsonProgramStore  # noqa: E402

HEADERS = {
    "Authorization": "Bearer op-conc:operator,admin:mfa",
    "Idempotency-Key": "concurrency-guard-key",
}


class _DirectProgramCommandPort:
    """U8A note: production ``create_evolution_program`` now writes through
    the typed ``program_commands`` port to the real Evolution service over
    HTTP (see ``services/control-plane/bff/ports/evolution_program_commands.py``),
    not a local read-store mutation — no Evolution service is running in
    this unit test process. This double swaps in the real
    ``ProgramService``/``JsonProgramStore`` implementation (the same code
    the Evolution service itself uses) against a local temp file so this
    guard still proves the same thing it always did: a synchronous
    create-with-receipt path with no awaited yield point between the
    idempotency check and the durable write, so 20 same-key concurrent
    callers commit exactly one resource."""

    def __init__(self, service: ProgramService) -> None:
        self._service = service

    async def create_program(self, *, tenant_id, actor_id, name, idempotency_key):
        program, _replayed = self._service.create_program(
            tenant_id=tenant_id or "pantheon-default",
            actor_id=actor_id,
            name=name,
            idempotency_key=idempotency_key,
        )
        return program

    async def patch_program_name(self, *, tenant_id, actor_id, program_id, name, expected_revision, idempotency_key):
        program, _replayed = self._service.patch_program_name(
            tenant_id=tenant_id or "pantheon-default",
            actor_id=actor_id,
            program_id=program_id,
            name=name,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        return program


def test_concurrent_same_key_creates_single_resource(tmp_path: Path) -> None:
    import httpx

    store = JsonProgramStore(tmp_path / "programs.json")
    service = ProgramService(store)
    original_program_commands = bff_main._evolution_program_commands
    bff_main._evolution_program_commands = _DirectProgramCommandPort(service)

    async def run():
        transport = httpx.ASGITransport(app=bff_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            tasks = [
                c.post("/bff/evolution-programs", headers=HEADERS, json={"name": "conc"})
                for _ in range(20)
            ]
            return await asyncio.gather(*tasks)

    try:
        responses = asyncio.run(run())
    finally:
        bff_main._evolution_program_commands = original_program_commands
    assert all(r.status_code == 201 for r in responses), [r.status_code for r in responses]
    ids = {(r.json().get("program_id") or r.json().get("id")) for r in responses}
    assert len(ids) == 1, f"idempotency double-create: {len(ids)} distinct ids {ids}"
