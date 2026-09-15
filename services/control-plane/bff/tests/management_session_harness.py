"""Standalone real-router management session harness.

Provides a standalone FastAPI harness mounting the real routers:
  - create_auth_router
  - create_personas_router
  - create_strategies_router
  - create_management_router

Mounts and tests the six core endpoints:
  - /bff/me
  - /bff/management/persona-fleet
  - /bff/strategies
  - /bff/personas
  - /bff/management/human-inbox
  - /bff/management/evidence

Decouples management session tests from main.py and bff_main globals:
  - Injects an independent SessionLifecycleStore per harness instance.
  - Uses typed in-memory doubles for narrow read/write stores.
  - Provides unified session-logout, role-check, and tenant-scope enforcement.
  - Strict zero reverse imports of main.py.
"""
from __future__ import annotations

import contextvars
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx

from services.control_plane.bff.auth.handlers import (
    create_auth_dependencies,
    create_auth_handlers,
)
from services.control_plane.bff.auth.policy import (
    AuthDependencies,
    OperatorIdentity,
    SessionLogoutGuard,
    extract_identity_jwt,
    extract_identity_stub,
    get_session_key,
)
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.ports import create_persona_registry_write_owner
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.control_plane.bff.strategies.router import create_strategies_router
from services.runtime_auth_inbound import encode_jwt_hs256


class InMemoryRankingWriteOwner:
    """Narrow in-memory test double for ranking snapshot writes."""

    def __init__(self) -> None:
        self.snapshots: Dict[str, Dict[str, Any]] = {}

    def put_ranking_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        sid = str(snapshot.get("snapshot_id") or f"snap-{uuid.uuid4().hex[:8]}")
        self.snapshots[sid] = dict(snapshot)
        return {"status": "created", "snapshot_id": sid, "snapshot": dict(snapshot)}

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self.snapshots.get(snapshot_id)

    def list_ranking_snapshots(self) -> List[Dict[str, Any]]:
        return list(self.snapshots.values())


class InMemoryManagementReadStore:
    """Narrow in-memory test double for management read models and personas queries."""

    def __init__(
        self,
        *,
        approvals: Optional[List[Dict[str, Any]]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        personas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.approvals: List[Dict[str, Any]] = list(approvals or [])
        self.evidence: List[Dict[str, Any]] = list(evidence or [])
        self.personas: List[Dict[str, Any]] = list(personas or [])

    def dataset_source(self, dataset: str) -> str:
        return "read_store"

    def list_approval_records(self) -> List[Dict[str, Any]]:
        return list(self.approvals)

    def list_approval_queue_items(self) -> List[Dict[str, Any]]:
        return list(self.approvals)

    def list_records(self, table: str = "") -> List[Dict[str, Any]]:
        if table == "evidence":
            return list(self.evidence)
        return []

    def list_incident_alerts(self) -> List[Dict[str, Any]]:
        return []

    def list_sentinel_findings(self) -> List[Dict[str, Any]]:
        return []

    def list_loop_executions(self) -> List[Dict[str, Any]]:
        return []

    def list_risk_radar_rows(self) -> List[Dict[str, Any]]:
        return []

    def list_incident_records(self) -> List[Dict[str, Any]]:
        return []

    def list_intervention_records(self) -> List[Dict[str, Any]]:
        return []

    def list_evidence_records(self) -> List[Dict[str, Any]]:
        return list(self.evidence)

    def list_evidence_refs(self) -> List[Dict[str, Any]]:
        return list(self.evidence)

    def list_personas(self) -> List[Dict[str, Any]]:
        return list(self.personas)

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        for p in self.personas:
            if p.get("persona_id") == persona_id or p.get("id") == persona_id:
                return p
        return None

    def list_persona_league(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return []

    def list_bindings(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return []

    def list_runtime_bindings(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return []

    def list_capital_pools(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return []

    def list_incidents(self) -> List[Dict[str, Any]]:
        return []

    def list_evolution_decisions(self) -> List[Dict[str, Any]]:
        return []


class InMemoryStrategyReadStore:
    """Narrow in-memory test double for strategy specs and summaries."""

    def __init__(
        self,
        summaries: Optional[List[Dict[str, Any]]] = None,
        details: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.summaries: List[Dict[str, Any]] = list(summaries or [])
        self.details: Dict[str, Dict[str, Any]] = dict(details or {})

    def list_strategy_summaries(self) -> List[Dict[str, Any]]:
        return list(self.summaries)

    def get_strategy_spec_detail(
        self, strategy_id: str, version_selector: str = "current"
    ) -> Optional[Dict[str, Any]]:
        return self.details.get(strategy_id)

    def get_strategy_spec(
        self, strategy_id: str, version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self.details.get(strategy_id)

    def list_strategy_spec_versions(self, strategy_id: str) -> List[str]:
        return ["v1"]


class ManagementSessionHarness:
    """Standalone FastAPI harness mounting real auth and management domain routers.

    Mounts real routers:
      - create_auth_router
      - create_personas_router
      - create_strategies_router
      - create_management_router

    Providing complete real route coverage for:
      - /bff/me
      - /bff/management/persona-fleet
      - /bff/strategies
      - /bff/personas
      - /bff/management/human-inbox
      - /bff/management/evidence

    With zero imports of main.py or bff_main.
    """

    CORE_ROUTES: tuple[str, ...] = (
        "/bff/me",
        "/bff/management/persona-fleet",
        "/bff/strategies",
        "/bff/personas",
        "/bff/management/human-inbox",
        "/bff/management/evidence",
    )

    def __init__(
        self,
        *,
        store: Optional[SessionLifecycleStore] = None,
        store_path: Optional[Union[str, Path]] = None,
        auth_mode: str = "permissive",
        auth_stub: bool = True,
        jwt_secret: str = "test-session-harness-secret-key-12345",
        jwt_issuer: str = "pantheon-session-harness",
        jwt_audience: str = "bff-operators",
        default_tenant_id: str = "tenant-1",
        allowed_tenants: Optional[Sequence[str]] = None,
        mgmt_read_store: Optional[InMemoryManagementReadStore] = None,
        strat_read_store: Optional[InMemoryStrategyReadStore] = None,
        ranking_write_owner: Optional[InMemoryRankingWriteOwner] = None,
        title: str = "Pantheon Management Session Harness",
    ) -> None:
        self._temp_dir: Optional[str] = None
        if store is not None:
            self.session_lifecycle_store = store
            self._store_path = getattr(store, "path", None)
        elif store_path is not None:
            self._store_path = str(store_path)
            self.session_lifecycle_store = SessionLifecycleStore(self._store_path)
        else:
            self._temp_dir = tempfile.mkdtemp(prefix="bff-mgmt-harness-")
            self._store_path = os.path.join(self._temp_dir, "session_lifecycle.json")
            self.session_lifecycle_store = SessionLifecycleStore(self._store_path)

        self.auth_mode = auth_mode
        self.auth_stub = auth_stub
        self.jwt_secret = jwt_secret
        self.jwt_issuer = jwt_issuer
        self.jwt_audience = jwt_audience
        self.default_tenant_id = default_tenant_id
        self.allowed_tenants = list(allowed_tenants or [default_tenant_id])

        # Configure environment variables needed for JWT verification
        os.environ["PANTHEON_BFF_JWT_SECRET"] = self.jwt_secret
        os.environ["PANTHEON_BFF_JWT_ISSUER"] = self.jwt_issuer
        os.environ["PANTHEON_BFF_JWT_AUDIENCE"] = self.jwt_audience
        if self.auth_stub:
            os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"

        # Request-level context variable for capturing cookies & tenant headers
        self._current_request: contextvars.ContextVar[Optional[Request]] = contextvars.ContextVar(
            f"harness_request_{id(self)}", default=None
        )

        def _unified_extract_identity(
            authorization: Optional[str] = None,
            mfa_token: Optional[str] = None,
            session_cookie: Optional[str] = None,
            **kwargs: Any,
        ) -> OperatorIdentity:
            # Fall back to request cookie if neither session_cookie nor authorization header is provided
            if not session_cookie and not authorization:
                req = self._current_request.get(None)
                if req is not None:
                    session_cookie = req.cookies.get("pantheon_session")

            if self.auth_stub:
                if authorization and authorization.startswith("Bearer "):
                    raw = authorization[len("Bearer "):].strip()
                    if raw.count(".") == 2:
                        try:
                            return extract_identity_jwt(authorization, mfa_token=mfa_token)
                        except Exception:
                            pass
                if not authorization and session_cookie:
                    try:
                        identity = extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
                        return identity.model_copy(update={"token_kind": "cookie"})
                    except Exception:
                        pass
                return extract_identity_stub(authorization)

            if not authorization and session_cookie:
                identity = extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
                return identity.model_copy(update={"token_kind": "cookie"})
            return extract_identity_jwt(authorization, mfa_token=mfa_token)

        self.extract_identity = _unified_extract_identity

        # Auth Dependencies & Service
        self.auth_deps = create_auth_dependencies(
            session_lifecycle_store=self.session_lifecycle_store,
            bff_auth_stub_enabled=lambda: self.auth_stub,
            bff_auth_mode=lambda: self.auth_mode,
            bff_source_commit=lambda: "management-session-harness-v1",
            extract_identity=self.extract_identity,
        )
        self.auth_handlers = create_auth_handlers(dependencies=self.auth_deps)
        self.auth_service = AuthFacadeService(
            local_readiness=self.auth_handlers["bff_auth_readiness"],
            handlers=self.auth_handlers,
        )

        # Unified require_read_role enforcement
        def _unified_require_read_role(identity: Any) -> None:
            # 1. Enforce session logout check
            self.auth_deps.raise_if_session_logged_out(identity)
            # 2. Enforce read role check (raises 403 role_check if role missing)
            self.auth_deps.require_read_role(identity)
            # 3. Enforce tenant scope if header is present
            req = self._current_request.get(None)
            if req is not None:
                req_tenant = req.headers.get("x-tenant-id") or req.headers.get("x-pantheon-tenant")
                if req_tenant:
                    self.auth_deps.bff_me_tenant_payload(identity, requested_tenant=req_tenant)

        self.require_read_role = _unified_require_read_role

        # Stores & Domain Services
        commands_path = (
            os.path.join(self._temp_dir, "commands.jsonl")
            if self._temp_dir
            else os.path.join(tempfile.gettempdir(), f"cmd-{uuid.uuid4().hex[:8]}.jsonl")
        )
        self.command_store = CommandStore(commands_path)
        self.persona_write_owner = create_persona_registry_write_owner()
        self.ranking_write_owner = ranking_write_owner or InMemoryRankingWriteOwner()
        self.mgmt_read_store = mgmt_read_store or InMemoryManagementReadStore()
        self.strat_read_store = strat_read_store or InMemoryStrategyReadStore()

        self.persona_service = PersonaService(
            write_owner=self.persona_write_owner,
            ranking_write_owner=self.ranking_write_owner,
            read_store=self.mgmt_read_store,
            command_store=self.command_store,
        )
        self.mgmt_service = ManagementService(
            get_read_store=lambda: self.mgmt_read_store,
        )

        # Build standalone FastAPI app
        self.app = FastAPI(title=title)

        @self.app.middleware("http")
        async def capture_request_context(request: Request, call_next):
            token = self._current_request.set(request)
            try:
                return await call_next(request)
            finally:
                self._current_request.reset(token)

        # Mount the four real routers
        self.app.include_router(create_auth_router(service=self.auth_service))
        self.app.include_router(
            create_personas_router(
                service=self.persona_service,
                extract_identity_fn=self.extract_identity,
                require_read_role_fn=self.require_read_role,
                bff_error_fn=self.auth_deps.bff_error,
            )
        )
        self.app.include_router(
            create_strategies_router(
                read_surface=self.strat_read_store,
                get_read_store=lambda: self.strat_read_store,
                extract_identity=self.extract_identity,
                require_read_role=self.require_read_role,
                bff_error=self.auth_deps.bff_error,
                list_strategy_summaries=self.strat_read_store.list_strategy_summaries,
                bff_me_tenant_payload=self.auth_deps.bff_me_tenant_payload,
            )
        )
        self.app.include_router(
            create_management_router(
                service=self.mgmt_service,
                read_surface=self.mgmt_read_store,
                get_read_store=lambda: self.mgmt_read_store,
                extract_identity=self.extract_identity,
                require_read_role=self.require_read_role,
                bff_error=self.auth_deps.bff_error,
            )
        )

        self._client: Optional[TestClient] = None

    def create_client(self, **kwargs: Any) -> TestClient:
        """Create a new TestClient connected to the standalone app."""
        return TestClient(self.app, **kwargs)

    @property
    def client(self) -> TestClient:
        """Cached TestClient instance for convenient synchronous test calls."""
        if self._client is None:
            self._client = self.create_client()
        return self._client

    def create_async_client(self, base_url: str = "http://testserver", **kwargs: Any) -> httpx.AsyncClient:
        """Create an httpx.AsyncClient connected via ASGITransport to the standalone app."""
        transport = httpx.ASGITransport(app=self.app)
        return httpx.AsyncClient(transport=transport, base_url=base_url, **kwargs)

    def make_bearer_token(
        self,
        operator_id: str = "test-operator",
        roles: Sequence[str] = ("operator", "reviewer", "admin"),
        tenant_ids: Sequence[str] = ("tenant-1",),
        mfa: bool = False,
    ) -> str:
        """Generate a stub Bearer authorization header value."""
        role_str = ",".join(roles)
        tenant_str = ",".join(tenant_ids)
        if mfa:
            token = f"{operator_id}:{role_str}:mfa::{tenant_str}"
        else:
            token = f"{operator_id}:{role_str}:{tenant_str}"
        return f"Bearer {token}"

    def make_jwt_token(
        self,
        operator_id: str = "test-operator",
        roles: Sequence[str] = ("operator",),
        tenant_ids: Sequence[str] = ("tenant-1",),
        session_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate an HS256 JWT bearer token matching harness secrets and issuer."""
        sid = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        now = int(time.time())
        payload: Dict[str, Any] = {
            "sub": operator_id,
            "roles": list(roles),
            "iss": self.jwt_issuer,
            "aud": self.jwt_audience,
            "iat": now,
            "exp": now + 3600,
            "sid": sid,
            "session_id": sid,
            "tenant_ids": list(tenant_ids),
            "tenant_id": tenant_ids[0] if tenant_ids else self.default_tenant_id,
        }
        if extra:
            payload.update(extra)
        return encode_jwt_hs256(payload, secret=self.jwt_secret)

    def make_cookie_headers(self, token: str) -> Dict[str, str]:
        """Generate Cookie header dictionary for a pantheon_session cookie."""
        return {"Cookie": f"pantheon_session={token}"}

    def login_session(
        self,
        operator_id: str = "test-operator",
        session_id: Optional[str] = None,
        state: str = "active",
        locale: str = "en-US",
    ) -> str:
        """Directly insert an active session into the lifecycle store."""
        sid = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        session_key = f"operator:{operator_id}:session:{sid}"
        now = self.auth_deps.utc_now()
        self.session_lifecycle_store.upsert_session(
            session_key,
            {"state": state, "locale": locale, "updated_at": now},
            now=now,
        )
        return session_key

    def logout_session(
        self,
        operator_id: str = "test-operator",
        session_id: Optional[str] = None,
        identity: Optional[Any] = None,
    ) -> None:
        """Mark a session as logged_out in the lifecycle store."""
        if identity is not None:
            session_key = get_session_key(identity)
        else:
            sid = session_id or f"bff-session-{operator_id}"
            session_key = f"operator:{operator_id}:session:{sid}"
        now = self.auth_deps.utc_now()
        self.session_lifecycle_store.upsert_session(
            session_key,
            {"state": "logged_out", "logged_out_at": now},
            now=now,
        )

    def close(self) -> None:
        """Clean up temporary directory and files if created by this harness."""
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir)
            except OSError:
                pass
            self._temp_dir = None

    def __enter__(self) -> ManagementSessionHarness:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
