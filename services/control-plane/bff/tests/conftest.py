"""Async ASGI fixtures for the prepared BFF core slice.

Also hosts small, test-only, real-router-mounting app builders shared across
several standalone (main.py-independent) test suites in this directory:
``build_auth_session_app`` (auth/session facade),
``build_command_security_app`` / ``ApprovalDecisionReadSurface`` (command
admission + confirm-token/two-man-signature/human-gate security suites), and
``build_consolidated_cross_cutting_app`` (cross-cutting fixture pack and detail
smoke journey suites).
These builders contain zero business logic of their own -- every behavior
they expose comes from the real production modules they import and mount.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest
try:
    from fastapi import FastAPI
    from services.control_plane.bff.agora.router import create_agora_router
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
    from services.control_plane.bff.capital.router import create_capital_router
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    from services.control_plane.bff.command_adapters.service import CommandAdapterService
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.control_loops.router import create_control_loops_router
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
    from services.control_plane.bff.deployment.router import create_deployment_router
    from services.control_plane.bff.evolution.router import create_evolution_router
    from services.control_plane.bff.governance.router import create_governance_router
    from services.control_plane.bff.incidents.router import create_incident_router
    from services.control_plane.bff.jobs.router import create_jobs_router
    from services.control_plane.bff.models import utc_now
    from services.control_plane.bff.personas.router import create_personas_router
    from services.control_plane.bff.personas.service import PersonaService
    from services.control_plane.bff.ports import (
        ReadSurfacePorts,
        create_in_memory_read_surface_ports,
        create_persona_registry_write_owner,
    )
    from services.control_plane.bff.research.router import create_research_router
    from services.control_plane.bff.runtime.router import create_runtime_router
    from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
    from services.control_plane.bff.shared.cross_domain_utils import _surface_degradation_reason
    from services.control_plane.bff.strategies.router import create_strategies_router
    from services.control_plane.bff.tools_integrations.router import create_integrations_router
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


def build_consolidated_cross_cutting_app(read_surface: Any) -> FastAPI:
    """Build a standalone FastAPI app mounting the 13 domain routers for cross-cutting
    and detail smoke test suites (Packs A/B/C, Detail Smoke A/B) without importing main.py.
    """
    if not _HAS_BFF_APP_DEPS:
        raise RuntimeError("FastAPI and BFF dependencies are required to build consolidated cross cutting app")

    app = FastAPI(title="Pantheon BFF Cross-Cutting Consolidated Test App")

    # 1. Strategies
    app.include_router(
        create_strategies_router(
            read_surface=read_surface,
            get_read_store=lambda: read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
            list_strategy_summaries=getattr(read_surface, "list_strategy_summaries", None),
            list_governance_audit_events=getattr(read_surface, "list_governance_audit_events", None),
        )
    )

    # 2. Personas
    class _InMemoryRankingWriteOwner:
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

    commands_path = os.path.join(tempfile.gettempdir(), f"cmd-cross-{uuid.uuid4().hex[:8]}.jsonl")
    command_store = CommandStore(commands_path)
    persona_service = PersonaService(
        read_store=read_surface,
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=_InMemoryRankingWriteOwner(),
        command_store=command_store,
    )
    app.include_router(
        create_personas_router(
            service=persona_service,
            extract_identity_fn=extract_identity_stub,
            require_read_role_fn=require_read_role,
            require_operator_role_fn=require_operator_role,
            bff_error_fn=bff_error,
            utc_now_fn=utc_now,
        )
    )

    # 3. Capital
    app.include_router(
        create_capital_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    # 4. Deployment
    app.include_router(
        create_deployment_router(
            queries=DeploymentReadSurfaceAdapter(read_surface),
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
            page_slice=lambda items, token, limit: (items[:limit], None),
            snapshot_meta=lambda s: {"snapshot_at": s},
            dataset_surface_status=lambda dataset, snapshot_at=None, has_data=None, missing_message=None: (
                getattr(read_surface, "dataset_surface_status", lambda d, **k: {"status": "ok"})(
                    dataset, snapshot_at=snapshot_at or utc_now()
                )
            ),
            composed_surface_status=lambda snapshot_at=None, available=True, missing_message=None: {
                "status": "ok" if available else "unavailable"
            },
            read_surface_meta=lambda dataset, surface_key, snapshot_at=None, surface=None, **kwargs: {
                "snapshot_at": snapshot_at or utc_now(),
                "surfaces": {surface_key: surface or {"status": "ok"}},
            },
            raise_if_read_surface_unavailable=lambda surface, label="": None,
            aggregate_group_surface=lambda *a, **k: {"status": "ok"},
            split_csv_query=lambda q: [x.strip() for x in q.split(",") if x.strip()] if q else None,
            meta_staleness=lambda: None,
            stable_json_hash=lambda p: "",
            resolve_final_idempotency_key=lambda h, b: "",
            reject_body_idempotency_key=lambda p: None,
            request_dry_run_requested=lambda **kw: False,
            gov_bff_idempotency={},
            publish_event=lambda *a, **k: "",
            sse_buffers={},
            sse_subscribers={},
            gov_bff_action_command=lambda *a, **k: {},
            deprecated_bff_path_response=lambda *a, **k: None,
            sem_command_response=lambda *a, **k: None,
            stream_generic_events=lambda *a, **k: None,
            surface_degradation_reason=_surface_degradation_reason,
        )
    )

    # 5. Runtime
    runtime_deps = {
        "_extract_identity": extract_identity_stub,
        "_require_read_role": require_read_role,
        "_require_operator_role": require_operator_role,
        "_bff_error": bff_error,
        "utc_now": utc_now,
        "_dataset_surface_status": lambda dataset, snapshot_at=None: (
            getattr(read_surface, "dataset_surface_status", lambda d, **k: {"status": "ok"})(
                dataset, snapshot_at=snapshot_at or utc_now()
            )
        ),
        "_raise_if_read_surface_unavailable": lambda surface, label="": None,
        "_snapshot_meta": lambda s: {"snapshot_at": s},
        "_page_slice": lambda items, token, limit: (items[:limit], None),
        "_stable_json_hash": lambda p: "",
        "_resolve_final_idempotency_key": lambda h, b: "",
        "_reject_body_idempotency_key": lambda p: None,
        "_meta_staleness": lambda: None,
        "_read_surface_meta": lambda dataset, surface_key, snapshot_at=None, surface=None, **kwargs: {
            "snapshot_at": snapshot_at or utc_now(),
            "surfaces": {surface_key: surface or {"status": "ok"}},
        },
        "_split_csv_query": lambda q: [x.strip() for x in q.split(",") if x.strip()] if q else None,
        "_composed_surface_status": lambda snapshot_at=None, available=True, missing_message=None: {
            "status": "ok" if available else "unavailable"
        },
        "_composed_dataset_surface_status": lambda dataset, snapshot_at=None: {"status": "ok"},
    }
    app.include_router(
        create_runtime_router(
            read_surface=read_surface,
            dependencies=runtime_deps,
        )
    )

    # 6. Evolution
    app.include_router(
        create_evolution_router(
            read_surface=read_surface,
            get_read_store=lambda: read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    # 7. Control Loops
    def _v5_provider(**kw: Any) -> list:
        if hasattr(read_surface, "list_v5_interventions"):
            return read_surface.list_v5_interventions(**kw)
        if hasattr(read_surface, "list_interventions"):
            return read_surface.list_interventions(**kw)
        return []

    app.include_router(
        create_control_loops_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
            intervention_records_provider=_v5_provider,
        )
    )

    # 8. Agora
    app.include_router(
        create_agora_router(
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_write_role=require_operator_role,
            require_operator_role=require_operator_role,
            require_journal_write_role=require_operator_role,
            require_agora_signal_write_role=lambda ident: None,
            require_agora_bulk_feedback_role=lambda ident: None,
            bff_error=bff_error,
            utc_now=utc_now,
            read_surface=read_surface,
            sync_servant_agent=lambda p: {},
        )
    )

    # 9. Research
    app.include_router(
        create_research_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    # 10. Incidents
    app.include_router(
        create_incident_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    # 11. Governance
    app.include_router(
        create_governance_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    # 12. Tools & Integrations
    app.include_router(
        create_integrations_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
        )
    )

    # 13. Jobs
    app.include_router(
        create_jobs_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            page_slice=lambda items, token, limit: (items[:limit], None),
            read_surface_meta=lambda dataset, surface_key, snapshot_at=None, surface=None, **kwargs: {
                "snapshot_at": snapshot_at or utc_now(),
                "surfaces": {surface_key: surface or {"status": "ok"}},
                **({"total": kwargs["total"]} if "total" in kwargs else {}),
            },
            dataset_surface_status=lambda dataset, **kw: {"status": "ok"},
            raise_if_read_surface_unavailable=lambda surface, label="": None,
            reject_body_idempotency_key=lambda payload: None,
            resolve_final_idempotency_key=lambda h, b: "",
            submit_job_action=lambda *a, **k: {},
        )
    )

    register_error_handlers(app)
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

