"""Restart-safe automatic L5 extraction; L6 drains the existing durable handoff."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any
from uuid import uuid4

from services.agora.store import AgoraEvidenceStore

log = logging.getLogger(__name__)


def run_extraction_batch(store: Any, *, worker_id: str, scope_limit: int = 25, batch_size: int = 25) -> dict:
    totals = {"processed": 0, "failed": 0, "handoffs_created": 0}
    for tenant_id, user_id in AgoraEvidenceStore(store).pending_scopes(scope_limit):
        result = store.process_inbox(
            tenant_id=tenant_id, user_id=user_id, worker_id=worker_id, batch_size=batch_size,
        )
        for key in totals:
            totals[key] += result[key]
    return totals


def install_evidence_worker(app: Any, store: Any, *, interval_seconds: float = 5.0) -> None:
    """Wrap the actual app lifespan, including applications supplied to composition."""
    previous = app.router.lifespan_context
    worker_id = f"agora-evidence-{uuid4().hex}"

    @asynccontextmanager
    async def lifespan(application: Any):
        stop = asyncio.Event()

        async def run() -> None:
            while not stop.is_set():
                try:
                    result = await asyncio.to_thread(run_extraction_batch, store, worker_id=worker_id)
                    application.state.agora_evidence_worker = {"status": "ok", **result}
                except Exception:
                    log.exception("Agora evidence extraction unavailable")
                    application.state.agora_evidence_worker = {"status": "unavailable", "reason": "agora_evidence_extraction_failed"}
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    pass

        async with previous(application) as state:
            task = asyncio.create_task(run(), name="agora-evidence-extraction") if store.backend == "postgres" else None
            try:
                yield state
            finally:
                stop.set()
                if task is not None:
                    await task

    app.router.lifespan_context = lifespan
