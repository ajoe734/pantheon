"""Typed BFF write port for the Evolution Program owner API (U8A).

Per docs/operations/bff-upstream-v2-20260911/decisions/evolution-lifecycle.md
§3: BFF writes (``create_evolution_program``, ``patch_evolution_program``) go
through this typed command port, which calls the Evolution service's owner
API (``/api/evolution/programs``) via ``evolution_owner_request``. This
is deliberately the *only* write path for program create/patch — the read
surface (``ports/read_surface_ports.py``) stays read-only, and there is no
direct DB fallback or second cache/writer.

U8A implements only create/list/get/PATCH(name). There is no method on this
port for a program lifecycle action (submit_evolution_review,
approve_program, pause_program, ...) — those remain U8B's obligation and
must not be fabricated here.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
from typing import Any, Dict, Optional, Protocol
from urllib.parse import quote

from services.control_plane.bff.command_adapters.base import (
    ActionUnavailableError,
    evolution_owner_request,
    evolution_url,
)


class EvolutionProgramCommandError(Exception):
    """Base error for the Evolution Program command port. ``status_code`` is
    the HTTP status the BFF router should surface to its own caller."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class EvolutionProgramValidationError(EvolutionProgramCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class EvolutionProgramConflictError(EvolutionProgramCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


class EvolutionProgramNotFoundError(EvolutionProgramCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class EvolutionProgramAuthError(EvolutionProgramCommandError):
    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message, status_code=status_code)


class EvolutionProgramUnavailableError(EvolutionProgramCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


class EvolutionProgramCommandPort(Protocol):
    """Typed write port injected into the evolution router in place of the
    read surface's (nonexistent) ``create_evolution_program``/
    ``patch_evolution_program`` methods."""

    async def create_program(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        actor_id: str,
        name: str,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        ...

    async def patch_program_name(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        actor_id: str,
        program_id: str,
        name: str,
        expected_revision: int,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        ...

    async def list_program_runs(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        program_id: str,
    ) -> Dict[str, Any]:
        ...


def _owner_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return f"HTTP {exc.code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else f"HTTP {exc.code}"


def _map_owner_http_error(exc: urllib.error.HTTPError, *, fallback: str) -> EvolutionProgramCommandError:
    detail = _owner_detail(exc)
    status = int(exc.code)
    if status == 422:
        return EvolutionProgramValidationError(detail)
    if status == 409:
        return EvolutionProgramConflictError(detail)
    if status == 404:
        return EvolutionProgramNotFoundError(detail)
    if status in (401, 403):
        return EvolutionProgramAuthError(f"Evolution owner rejected the request: {detail}", status_code=status)
    return EvolutionProgramUnavailableError(f"{fallback}: {detail}")


class EvolutionServiceProgramCommandPort:
    """Concrete command port calling the Evolution service's owner API.

    Every call goes through ``evolution_owner_request`` -- the single BFF->Evolution
    owner transport. The tenant is the verified caller tenant, the credential is the
    configured Evolution service token, and the operator JWT never leaves the BFF.
    """

    async def _call(
        self,
        *,
        method: str,
        path: str,
        payload: Dict[str, Any],
        caller_token: Optional[str],
        tenant_id: Optional[str],
        idempotency_key: Optional[str],
        fallback: str,
    ) -> Dict[str, Any]:
        headers: Dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            # The payload carries no tenant: the helper binds X-Tenant-Id from the caller.
            return await asyncio.to_thread(
                evolution_owner_request,
                evolution_url(path),
                caller_token=caller_token,
                method=method,
                payload=payload,
                tenant_id=tenant_id,
                headers=headers,
            )
        except urllib.error.HTTPError as exc:
            raise _map_owner_http_error(exc, fallback=fallback) from exc
        except ActionUnavailableError as exc:
            status = int(getattr(exc, "downstream_status", 503) or 503)
            if status == 403:
                raise EvolutionProgramAuthError(str(exc), status_code=403) from exc
            raise EvolutionProgramUnavailableError(f"{fallback}: {exc}") from exc
        except Exception as exc:
            raise EvolutionProgramUnavailableError(f"{fallback}: {exc}") from exc

    async def create_program(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        actor_id: str,
        name: str,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        return await self._call(
            method="POST",
            path="/api/evolution/programs",
            payload={"name": name, "actor_id": actor_id},
            caller_token=caller_token,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fallback="create_program unavailable",
        )

    async def patch_program_name(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        actor_id: str,
        program_id: str,
        name: str,
        expected_revision: int,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        return await self._call(
            method="PATCH",
            path=f"/api/evolution/programs/{quote(program_id, safe='')}",
            payload={"name": name, "actor_id": actor_id, "expected_revision": expected_revision},
            caller_token=caller_token,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fallback="patch_program unavailable",
        )

    async def list_program_runs(
        self,
        *,
        caller_token: Optional[str],
        tenant_id: Optional[str],
        program_id: str,
    ) -> Dict[str, Any]:
        """Runs linked to the program, each read back from the owning plane by
        the Evolution owner.  The BFF projects nothing of its own."""
        return await self._call(
            method="GET",
            path=f"/api/evolution/programs/{quote(program_id, safe='')}/runs",
            payload={},
            caller_token=caller_token,
            tenant_id=tenant_id,
            idempotency_key=None,
            fallback="program runs unavailable",
        )
