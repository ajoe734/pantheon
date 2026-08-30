"""Local auth facade and non-blocking provider-readiness cache.

The browser auth decision belongs to the BFF.  OpenClaw provider health is
useful observability, but it is not authentication authority and must never be
queried on the request path for ``GET /bff/auth/readiness``.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Optional

from fastapi import HTTPException


ProviderProbe = Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
LocalReadiness = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
AuthHandler = Callable[..., Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ProviderReadinessCache:
    """Thread-safe last-known provider state with bounded async refresh.

    ``snapshot`` performs no provider I/O.  A lifespan-owned background task
    calls ``refresh`` and isolates slow synchronous probes in a worker thread.
    """

    def __init__(
        self,
        probe: Optional[ProviderProbe] = None,
        *,
        provider: str = "openclaw",
        timeout_seconds: float = 5.0,
        stale_after_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], str] = _utc_now,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self._probe = probe
        self._provider = str(provider or "openclaw")
        self._timeout_seconds = float(timeout_seconds)
        self._stale_after_seconds = float(stale_after_seconds)
        self._clock = clock
        self._utc_now = utc_now
        self._lock = threading.Lock()
        self._checked_monotonic: Optional[float] = None
        self._snapshot: dict[str, Any] = {
            "provider": self._provider,
            "ready": False,
            "status": "unknown",
            "reason": "not_checked",
            "checkedAt": None,
        }

    @property
    def configured(self) -> bool:
        return self._probe is not None

    def snapshot(self) -> dict[str, Any]:
        """Return last-known state immediately, without invoking the probe."""
        with self._lock:
            value = deepcopy(self._snapshot)
            checked_monotonic = self._checked_monotonic
        value["cached"] = True
        value["stale"] = (
            checked_monotonic is None
            or self._clock() - checked_monotonic > self._stale_after_seconds
        )
        return value

    async def _invoke_probe(self) -> Mapping[str, Any]:
        if self._probe is None:
            return {
                "provider": self._provider,
                "ready": False,
                "status": "unavailable",
                "reason": "provider_probe_not_configured",
            }
        if inspect.iscoroutinefunction(self._probe):
            raw = await self._probe()
        else:
            raw = await asyncio.to_thread(self._probe)
            raw = await _resolve(raw)
        if not isinstance(raw, Mapping):
            raise TypeError("provider readiness probe must return a mapping")
        return raw

    def _normalized(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        ready = bool(raw.get("ready"))
        result: dict[str, Any] = {
            "provider": str(raw.get("provider") or self._provider),
            "ready": ready,
            "status": str(raw.get("status") or ("ready" if ready else "unavailable")),
            "checkedAt": self._utc_now(),
        }
        reason = str(raw.get("reason") or "").strip()
        if reason:
            result["reason"] = reason
        auth_status = str(raw.get("authStatus") or raw.get("auth_status") or "").strip()
        if auth_status:
            result["authStatus"] = auth_status
        return result

    async def refresh(self) -> dict[str, Any]:
        """Refresh once with a hard timeout and publish a degraded snapshot."""
        try:
            raw = await asyncio.wait_for(
                self._invoke_probe(),
                timeout=self._timeout_seconds,
            )
            normalized = self._normalized(raw)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            normalized = self._normalized(
                {
                    "provider": self._provider,
                    "ready": False,
                    "status": "unavailable",
                    "reason": "timeout",
                }
            )
        except Exception as exc:  # provider observability degrades, auth does not
            normalized = self._normalized(
                {
                    "provider": self._provider,
                    "ready": False,
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                }
            )
        with self._lock:
            self._snapshot = normalized
            self._checked_monotonic = self._clock()
        return self.snapshot()


class AuthFacadeService:
    """Dependency-injected auth route service.

    The six mutating/session handlers remain owned by their existing domain
    logic and are injected during assembly.  Readiness is special: its local
    decision is composed with a cache snapshot and never calls provider I/O.
    """

    def __init__(
        self,
        *,
        local_readiness: Optional[LocalReadiness] = None,
        provider_readiness_cache: Optional[ProviderReadinessCache] = None,
        handlers: Optional[Mapping[str, AuthHandler]] = None,
        utc_now: Callable[[], str] = _utc_now,
    ) -> None:
        self._local_readiness = local_readiness
        self.provider_readiness_cache = provider_readiness_cache or ProviderReadinessCache()
        self._handlers = dict(handlers or {})
        self._utc_now = utc_now

    async def invoke(self, handler_name: str, **kwargs: Any) -> Any:
        handler = self._handlers.get(handler_name)
        if handler is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": f"Auth handler {handler_name!r} is not assembled",
                    }
                },
            )
        return await _resolve(handler(**kwargs))

    async def readiness(self, **kwargs: Any) -> dict[str, Any]:
        if self._local_readiness is None:
            local_payload: Mapping[str, Any] = {
                "data": {
                    "ready": False,
                    "authReady": False,
                    "auth": {"strict": False, "sessionReady": False},
                    "authority": {
                        "interaction": "advisory",
                        "execution": "none",
                        "broker": "none",
                        "capital": "none",
                    },
                },
                "meta": {
                    "route": "GET /bff/auth/readiness",
                    "contract": "PINT-016-STRICT-BROWSER-READINESS",
                    "snapshot_at": self._utc_now(),
                },
            }
        else:
            local_payload = await _resolve(self._local_readiness(**kwargs))
        if not isinstance(local_payload, Mapping):
            raise TypeError("local auth readiness must return a mapping")

        result: MutableMapping[str, Any] = deepcopy(dict(local_payload))
        if isinstance(result.get("data"), Mapping):
            data = dict(result["data"])
        else:
            data = dict(result)
            result = {}

        auth_ready = bool(data.get("authReady", data.get("ready", False)))
        provider = self.provider_readiness_cache.snapshot()
        data["ready"] = auth_ready
        data["authReady"] = auth_ready
        data["providerReady"] = bool(provider["ready"])
        data["provider"] = provider
        result["data"] = data
        meta = dict(result.get("meta") or {})
        meta.setdefault("route", "GET /bff/auth/readiness")
        meta.setdefault("contract", "PINT-016-STRICT-BROWSER-READINESS")
        meta.setdefault("snapshot_at", self._utc_now())
        result["meta"] = meta
        return dict(result)

