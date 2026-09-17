"""Async ASGI fixtures for the prepared BFF core slice.

Also hosts small, test-only, real-router-mounting app builders shared across
several standalone (main.py-independent) test suites in this directory:
``build_auth_session_app`` (auth/session facade) and
``build_command_security_app`` / ``ApprovalDecisionReadSurface`` (command
admission + confirm-token/two-man-signature/human-gate security suites).
These builders contain zero business logic of their own -- every behavior
they expose comes from the real production modules they import and mount.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest
try:
    from fastapi import FastAPI
    from services.control_plane.bff.auth.handlers import (
        create_auth_dependencies,
        create_auth_handlers,
    )
    from services.control_plane.bff.auth.policy import (
        bff_error,
        extract_identity_stub,
        require_operator_role,
        require_read_role,
    )
    from services.control_plane.bff.auth.router import create_auth_router
    from services.control_plane.bff.auth.service import AuthFacadeService
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    from services.control_plane.bff.command_adapters.service import CommandAdapterService
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.control_loops.router import create_control_loops_router
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.models import utc_now
    from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports
    from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
    _HAS_BFF_APP_DEPS = True
except ImportError:
    FastAPI = Any  # type: ignore
    ReadSurfacePorts = object  # type: ignore
    SessionLifecycleStore = Any  # type: ignore
    CommandStore = Any  # type: ignore
    _HAS_BFF_APP_DEPS = False


def build_auth_session_app(session_lifecycle_store: SessionLifecycleStore) -> FastAPI:
    """Build a standalone FastAPI app mounting the real BFF auth/session router
    (``auth.router.create_auth_router`` + ``auth.service.AuthFacadeService`` +
    ``auth.handlers.create_auth_handlers``/``create_auth_dependencies``, plus
    the real Pack D error handlers). All auth policy decisions (auth mode,
    stub toggling, JWT verification, role/tenant checks, idempotency, locale
    resolution) are made by the real ``auth.policy``/``auth.handlers``
    defaults, which read the same ``PANTHEON_BFF_*`` environment variables
    that ``main.py`` reads. No symbol is imported from ``main.py``.
    """
    if not _HAS_BFF_APP_DEPS:
        raise RuntimeError("FastAPI and BFF dependencies are required to build auth session app")
    deps = create_auth_dependencies(session_lifecycle_store=session_lifecycle_store)
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(
        local_readiness=handlers["bff_auth_readiness"],
        handlers=handlers,
    )

    app = FastAPI(title="Pantheon BFF Auth/Session Test App")
    app.include_router(create_auth_router(service=service))
    register_error_handlers(app)
    return app


def extract_identity_from_bearer_stub(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    **_kwargs: Any,
) -> Any:
    """Adapt the canonical stub extractor to the ``(auth, mfa_token=...)`` shape
    used by the command-adapters/control-loops router factories."""
    if not _HAS_BFF_APP_DEPS:
        raise RuntimeError("FastAPI and BFF dependencies are required")
    return extract_identity_stub(authorization)


class ApprovalDecisionReadSurface(ReadSurfacePorts):  # type: ignore
    """Real ``ReadSurfacePorts`` composition with an appendable, in-memory
    approval-decisions list wired through the real
    ``ooda_management_kwargs={"approval_decisions": ...}`` seam (the same
    mechanism ``ports.create_in_memory_read_surface_ports`` exposes for
    production callers), so ``get_approval_decision``/``list_approval_decisions``
    use the real ``ports.read_surface_ports`` lookup logic rather than a
    test-local reimplementation.
    """

    def __init__(self) -> None:
        if not _HAS_BFF_APP_DEPS:
            raise RuntimeError("FastAPI and BFF dependencies are required")
        self.approval_decisions: List[Dict[str, Any]] = []
        base = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": self.approval_decisions}
        )
        super().__init__(
            operations_consultation=base.operations_consultation,
            persona_capital_runtime=base.persona_capital_runtime,
            ooda_management=base.ooda_management,
            research_knowledge_source=base.research_knowledge_source,
            lifecycle_telemetry_governance=base.lifecycle_telemetry_governance,
            persona_training=base.persona_training,
        )

    def seed_approval_decision(self, decision: Dict[str, Any]) -> None:
        self.approval_decisions.append(dict(decision))


async def noop_process_command(_command_id: str) -> None:
    return None


def build_command_security_app(
    *,
    command_store: CommandStore,
    read_store: ApprovalDecisionReadSurface,
    validators: Optional[Dict[Any, Callable[..., None]]] = None,
    process_command_task: Optional[Callable[[str], Any]] = None,
) -> FastAPI:
    """Build a standalone FastAPI app mounting the real command-admission
    stack: ``command_adapters.service.CommandAdapterService`` (real
    preconditions, idempotency, confirm-token, two-man-signature, human-gate,
    and audit logic), ``command_adapters.router.create_command_adapters_router``
    (``POST /bff/v1/commands``, ``/bff/confirm-tokens*``, etc.), and
    ``control_loops.router.create_control_loops_router`` (``/bff/v5/interventions/*``
    including ``two-man-sign``, ``remediate``, ``claim``, ``decide``), against
    a real ``command_queue.CommandStore`` and a real
    ``ports.read_surface_ports``-backed read surface. No symbol is imported
    from ``main.py``.
    """
    if not _HAS_BFF_APP_DEPS:
        raise RuntimeError("FastAPI and BFF dependencies are required")
    service = CommandAdapterService(
        command_store=lambda: command_store,
        read_surface=lambda: read_store,
        extract_identity=extract_identity_from_bearer_stub,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        validators=validators or {},
        process_command_task=process_command_task or (lambda cmd_id: noop_process_command(cmd_id)),
    )

    app = FastAPI(title="Pantheon BFF Command Security Test App")
    app.include_router(
        create_control_loops_router(
            extract_identity=extract_identity_from_bearer_stub,
            require_operator_role=require_operator_role,
            require_read_role=require_read_role,
            bff_error=bff_error,
            submit_final_command_admission=service.submit_command_admission,
            submit_sem_command=service.sem_command_response,
        )
    )
    app.include_router(create_command_adapters_router(service=service))
    register_error_handlers(app)
    app.state.command_adapter_service = service
    return app


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

