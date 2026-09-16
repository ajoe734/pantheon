"""Async ASGI fixtures for the prepared BFF core slice."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest


BFF_DIR = Path(__file__).resolve().parents[1]
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))


@pytest.fixture
def asgi_request():
    """Run one request through httpx's async in-process ASGI transport."""

    def request(
        app: Any,
        method: str,
        path: str,
        *,
        timeout_seconds: float = 0.5,
        **kwargs: Any,
    ) -> httpx.Response:
        async def run() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://bff.test",
            ) as client:
                return await asyncio.wait_for(
                    client.request(method, path, **kwargs),
                    timeout=timeout_seconds,
                )

        return asyncio.run(run())

    return request

