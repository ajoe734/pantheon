"""Common definitions and route context for Persona domain subrouters."""
from __future__ import annotations

import asyncio
from concurrent.futures import Executor
from contextvars import copy_context
from dataclasses import dataclass
from functools import partial
import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional
from fastapi import Depends, HTTPException

log = logging.getLogger(__name__)


class ManagementReadTimeout(Exception):
    """Raised when a management read exceeds its bounded wait budget (MGMT-LOAD-005)."""


class ManagementReadSaturated(Exception):
    """Raised before submission when a bounded read executor has no capacity."""


def _management_read_timeout_seconds() -> float:
    """Bound for offloaded management read aggregation (MGMT-LOAD-005)."""
    try:
        return max(0.05, float(os.getenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", "0.6")))
    except (TypeError, ValueError):
        return 0.6


def discard_late_management_read_result(task: "asyncio.Task[Any]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("bff.management_read late worker-thread error after timeout budget: %r", exc)


async def run_management_read(
    func: Callable[..., Any],
    *args: Any,
    timeout_seconds: Optional[float] = None,
    capacity: Optional[threading.BoundedSemaphore] = None,
    executor: Optional[Executor] = None,
    **kwargs: Any,
) -> Any:
    """Run a synchronous read-store aggregation on a worker thread, bounded by a wait budget."""
    budget = _management_read_timeout_seconds() if timeout_seconds is None else timeout_seconds
    if capacity is None:
        task = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    else:
        if not capacity.acquire(blocking=False):
            raise ManagementReadSaturated()
        context = copy_context()
        call = partial(func, *args, **kwargs)
        try:
            worker_future = executor.submit(context.run, call) if executor else None
            if worker_future is None:
                raise RuntimeError("A bounded management read requires an executor")
        except BaseException:
            capacity.release()
            raise

        worker_future.add_done_callback(lambda _future: capacity.release())
        task = asyncio.wrap_future(worker_future)
    done, _pending = await asyncio.wait({task}, timeout=budget)
    if task in done:
        return task.result()
    if capacity is not None:
        worker_future.cancel()
    task.add_done_callback(discard_late_management_read_result)
    raise ManagementReadTimeout()


_run_management_read = run_management_read
_ManagementReadTimeout = ManagementReadTimeout
_ManagementReadSaturated = ManagementReadSaturated
_discard_late_management_read_result = discard_late_management_read_result


@dataclass(frozen=True)
class PersonaRouteContext:
    service: Any
    read_store: Any
    command_store: Any
    ranking_write_owner: Any
    write_owner: Any
    extract_identity: Callable[..., Any]
    require_read_role: Callable[..., None]
    require_operator_role: Callable[..., None]
    bff_error: Callable[..., HTTPException]
    utc_now: Callable[[], str]
    page_slice: Callable[..., Any]
    snapshot_meta: Callable[..., Dict[str, Any]]
    dataset_surface_status: Callable[..., Dict[str, Any]]
    read_surface_meta: Callable[..., Dict[str, Any]]
    raise_if_read_surface_unavailable: Callable[..., None]
    reject_body_idempotency_key: Callable[[Dict[str, Any]], None]
    resolve_final_idempotency_key: Callable[[Optional[str], Optional[str]], str]
    submit_persona_action: Optional[Callable[..., Any]] = None
    run_management_read: Optional[Callable[..., Any]] = None



def make_context_dependency(ctx: PersonaRouteContext):
    async def _bind_service_context():
        from ..service import _current_persona_service
        token = _current_persona_service.set(ctx.service)
        try:
            yield
        finally:
            _current_persona_service.reset(token)

    return Depends(_bind_service_context)
