"""ASGI lifespan helpers for non-blocking provider observability and JWKS prewarm."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from typing import AsyncIterator, Callable, Optional

from fastapi import FastAPI

from ..auth.service import ProviderReadinessCache

log = logging.getLogger(__name__)


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
):
    """Return a lifespan that schedules refresh without awaiting first probe."""

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
        try:
            yield
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task

    return lifespan
