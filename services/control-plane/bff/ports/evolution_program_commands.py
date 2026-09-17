"""Typed BFF write port for the Evolution Program owner API (U8A).

Per docs/operations/bff-upstream-v2-20260911/decisions/evolution-lifecycle.md
§3: BFF writes (``create_evolution_program``, ``patch_evolution_program``) go
through this typed command port, which calls the Evolution service's owner
API (``/api/evolution/programs``) via ``services/evolution/client.py``. This
is deliberately the *only* write path for program create/patch — the read
surface (``ports/read_surface_ports.py``) stays read-only, and there is no
direct DB fallback or second cache/writer.

U8A implements only create/list/get/PATCH(name). There is no method on this
port for a program lifecycle action (submit_evolution_review,
approve_program, pause_program, ...) — those remain U8B's obligation and
must not be fabricated here.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from services.evolution.client import (
    EvolutionAuthenticationError,
    EvolutionClient,
    EvolutionClientError,
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
        self, *, tenant_id: Optional[str], actor_id: str, name: str, idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        ...

    async def patch_program_name(
        self,
        *,
        tenant_id: Optional[str],
        actor_id: str,
        program_id: str,
        name: str,
        expected_revision: int,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        ...


def _map_client_error(exc: EvolutionClientError, *, fallback: str) -> EvolutionProgramCommandError:
    if isinstance(exc, EvolutionAuthenticationError):
        return EvolutionProgramAuthError(str(exc), status_code=exc.status_code or 401)
    status = exc.status_code or 503
    if status == 422:
        return EvolutionProgramValidationError(str(exc))
    if status == 409:
        return EvolutionProgramConflictError(str(exc))
    if status == 404:
        return EvolutionProgramNotFoundError(str(exc))
    if status in (401, 403):
        return EvolutionProgramAuthError(str(exc), status_code=status)
    return EvolutionProgramUnavailableError(f"{fallback}: {exc}")


class EvolutionServiceProgramCommandPort:
    """Concrete command port calling the Evolution service's owner API."""

    def __init__(self, client: EvolutionClient) -> None:
        self._client = client

    async def create_program(
        self, *, tenant_id: Optional[str], actor_id: str, name: str, idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        try:
            return await self._client.create_program(
                name=name, actor_id=actor_id, idempotency_key=idempotency_key,
            )
        except EvolutionClientError as exc:
            raise _map_client_error(exc, fallback="create_program unavailable") from exc

    async def patch_program_name(
        self,
        *,
        tenant_id: Optional[str],
        actor_id: str,
        program_id: str,
        name: str,
        expected_revision: int,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        try:
            return await self._client.patch_program(
                program_id,
                name=name,
                actor_id=actor_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except EvolutionClientError as exc:
            raise _map_client_error(exc, fallback="patch_program unavailable") from exc
