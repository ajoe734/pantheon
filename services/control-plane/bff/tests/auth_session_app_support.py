"""Shared test-only scaffolding for the BFF auth/session facade.

Builds a minimal standalone FastAPI app that mounts the *real* production
auth router/service/handlers/policy stack (``auth.router.create_auth_router``,
``auth.service.AuthFacadeService``, ``auth.handlers.create_auth_handlers``,
``auth.policy.create_auth_dependencies``) plus the real Pack D error handlers
(``core.errors.register_error_handlers``).

This module intentionally contains zero business logic of its own: every
behavior under test (auth/session/idempotency/locale/tenant handling) lives in
the real ``services/control-plane/bff/auth/*`` modules. The only test-local
state is the injected ``SessionLifecycleStore`` instance, matching the
canonical pattern already used by ``management_session_harness.py`` and
``test_bff_auth_facade.py``. No symbol is imported from ``main.py``.
"""
from __future__ import annotations

from fastapi import FastAPI

from services.control_plane.bff.auth.handlers import (
    create_auth_dependencies,
    create_auth_handlers,
)
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore


def build_auth_session_app(session_lifecycle_store: SessionLifecycleStore) -> FastAPI:
    """Build a standalone FastAPI app mounting the real BFF auth/session router.

    All auth policy decisions (auth mode, stub toggling, JWT verification,
    role/tenant checks, idempotency, locale resolution) are made by the real
    ``auth.policy``/``auth.handlers`` defaults, which read the same
    ``PANTHEON_BFF_*`` environment variables that ``main.py`` reads. Tests
    drive behavior with ``monkeypatch.setenv(...)`` exactly as they did when
    importing ``main`` directly.
    """
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
