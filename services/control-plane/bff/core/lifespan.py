"""ASGI lifespan helpers for non-blocking provider observability and JWKS prewarm."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
import sys

from fastapi import FastAPI

from ..auth.service import ProviderReadinessCache
from ..models import CommandStatus, CommandType

log = logging.getLogger(__name__)

_RETRYABLE_CAPITAL_COMMAND_TYPES = {
    CommandType.APPROVED_APPLY.value if hasattr(CommandType, "APPROVED_APPLY") else "ApprovedApply",
    CommandType.EMERGENCY_CONTAINMENT.value if hasattr(CommandType, "EMERGENCY_CONTAINMENT") else "EmergencyContainment",
    "ApprovedApply",
    "EmergencyContainment",
    "APPROVED_APPLY",
    "EMERGENCY_CONTAINMENT",
}


def retryable_terminal_capital_command(record: Dict[str, Any]) -> bool:
    """Return True if a failed/timed-out capital command carries a retryable error."""
    cmd_type = record.get("type")
    if hasattr(cmd_type, "value"):
        cmd_type = cmd_type.value
    cmd_status = record.get("status")
    if hasattr(cmd_status, "value"):
        cmd_status = cmd_status.value
    error = record.get("error")
    return bool(
        cmd_type in _RETRYABLE_CAPITAL_COMMAND_TYPES
        and cmd_status in {CommandStatus.FAILED.value, CommandStatus.TIMEOUT.value, "failed", "timeout", "FAILED", "TIMEOUT"}
        and isinstance(error, dict)
        and error.get("retryable") is True
    )


def recoverable_capital_command(record: Dict[str, Any]) -> bool:
    """Return True if a capital command was interrupted or is retryable after crash."""
    cmd_type = record.get("type")
    if hasattr(cmd_type, "value"):
        cmd_type = cmd_type.value
    if cmd_type not in _RETRYABLE_CAPITAL_COMMAND_TYPES:
        return False
    cmd_status = record.get("status")
    if hasattr(cmd_status, "value"):
        cmd_status = cmd_status.value
    if cmd_status in {
        CommandStatus.SUBMITTED.value,
        CommandStatus.PROCESSING.value,
        "submitted",
        "processing",
        "SUBMITTED",
        "PROCESSING",
    }:
        return True
    return retryable_terminal_capital_command(record)


def replay_submitted_commands(
    command_store: Any,
    process_command: Callable[[str], Any],
    *,
    task_factory: Callable[..., asyncio.Task] = asyncio.create_task,
) -> List[asyncio.Task]:
    """Replay pending/recoverable Capital authority commands on startup.

    A crash can leave a durable owner command submitted/processing. Replay
    only the idempotent Capital authority commands; generic adapter commands
    are admission receipts and must not be reinterpreted as mutations.
    """
    tasks: List[asyncio.Task] = []
    if command_store is None:
        return tasks
    getter = getattr(command_store, "_get_all_commands", None) or getattr(command_store, "get_all_commands", None)
    if not callable(getter):
        return tasks
    for record in getter():
        if isinstance(record, dict) and recoverable_capital_command(record):
            command_id = str(record.get("command_id"))
            if hasattr(command_store, "update_status"):
                command_store.update_status(
                    command_id,
                    CommandStatus.SUBMITTED,
                )
            if callable(process_command):
                res = process_command(command_id)
                if asyncio.iscoroutine(res):
                    task = task_factory(res, name=f"replay-command-{command_id}")
                    tasks.append(task)
                elif isinstance(res, asyncio.Task):
                    tasks.append(res)
    return tasks


def _prewarm_jwks_cache() -> None:
    """Populate runtime_auth_inbound JWKS cache before startup."""
    jwks_uri = os.getenv("PANTHEON_BFF_JWKS_URI", "").strip() or os.getenv("PANTHEON_RUNTIME_JWKS_URI", "").strip()
    discovery_url = os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "").strip() or os.getenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL", "").strip()
    if not jwks_uri and not discovery_url:
        return
    try:
        try:
            from services.runtime_auth_inbound import _fetch_jwks_keys, _fetch_oidc_metadata
        except ImportError:
            from runtime_auth_inbound import _fetch_jwks_keys, _fetch_oidc_metadata  # type: ignore[no-redef]
        if jwks_uri:
            _fetch_jwks_keys(jwks_uri)
        elif discovery_url:
            meta = _fetch_oidc_metadata(discovery_url)
            resolved_uri = str(meta.get("jwks_uri", "")).strip()
            if resolved_uri:
                _fetch_jwks_keys(resolved_uri)
    except Exception as exc:  # noqa: BLE001 - warm-up must never block startup
        log.warning("JWKS cache pre-warm failed, first real login will pay the fetch cost: %s", exc)


async def refresh_provider_readiness(
    cache: ProviderReadinessCache,
    *,
    interval_seconds: float,
) -> None:
    """Refresh forever; every individual probe is bounded by the cache."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while True:
        await cache.refresh()
        await asyncio.sleep(interval_seconds)


def create_lifespan(
    cache: ProviderReadinessCache,
    *,
    interval_seconds: float = 30.0,
    task_factory: Callable[..., asyncio.Task] = asyncio.create_task,
    prewarm_jwks: bool = True,
    command_store: Optional[Any] = None,
    process_command: Optional[Callable[[str], Any]] = None,
):
    """Return a lifespan that schedules refresh without awaiting first probe and replays recoverable commands."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.provider_readiness_cache = cache
        if prewarm_jwks:
            await asyncio.to_thread(_prewarm_jwks_cache)
        refresh_task = task_factory(
            refresh_provider_readiness(cache, interval_seconds=interval_seconds),
            name="bff-provider-readiness-refresh",
        )
        app.state.provider_readiness_refresh_task = refresh_task

        # Command replay on startup
        resolved_store = command_store
        if callable(resolved_store) and not hasattr(resolved_store, "_get_all_commands"):
            resolved_store = resolved_store()
        if resolved_store is None:
            resolved_store = getattr(app.state, "command_store", None)
        if resolved_store is None:
            bff_main = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
            if bff_main is not None:
                resolved_store = getattr(bff_main, "command_store", None)
                if resolved_store is None and hasattr(bff_main, "app_deps"):
                    resolved_store = getattr(bff_main.app_deps, "command_store", None)

        resolved_proc = process_command
        if callable(resolved_proc) and getattr(resolved_proc, "__code__", None) and resolved_proc.__code__.co_argcount == 0:
            resolved_proc = resolved_proc()
        if resolved_proc is None:
            resolved_proc = getattr(app.state, "process_command", None)
        if resolved_proc is None:
            bff_main = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
            if bff_main is not None:
                resolved_proc = getattr(bff_main, "_process_command_stub", None)

        replay_tasks: List[asyncio.Task] = []
        if resolved_store is not None and resolved_proc is not None:
            replay_tasks = replay_submitted_commands(
                resolved_store,
                resolved_proc,
                task_factory=task_factory,
            )
        app.state.replay_tasks = replay_tasks

        try:
            yield
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
            for rt in getattr(app.state, "replay_tasks", []):
                if not rt.done():
                    rt.cancel()
                    with suppress(asyncio.CancelledError):
                        await rt

    return lifespan
