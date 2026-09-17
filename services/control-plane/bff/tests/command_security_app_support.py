"""Shared test-only scaffolding for the BFF command/security hardening suites.

Builds a minimal standalone FastAPI app that mounts the *real* production
command-admission stack:

  - ``command_adapters.service.CommandAdapterService`` (real preconditions,
    idempotency, confirm-token, two-man-signature, human-gate, and audit
    logic; see ``command_adapters/preconditions.py`` and
    ``command_adapters/contracts.py``)
  - ``command_adapters.router.create_command_adapters_router`` (mounts
    ``POST /bff/v1/commands``, ``/bff/confirm-tokens*``,
    ``/bff/command-confirmations*``, ``/bff/actions``,
    ``/api/v1/operator/commands/{id}``)
  - ``control_loops.router.create_control_loops_router`` (mounts
    ``/bff/v5/interventions/*`` including ``two-man-sign``, ``remediate``,
    ``claim``, ``decide``)

against a real ``command_queue.CommandStore`` and a real
``ports.read_surface_ports`` in-memory read surface, with only the identity
extraction stubbed to the canonical ``auth.policy.extract_identity_stub``
(same technique already used by ``test_v5_interventions.py``).

No symbol is imported from ``main.py``; every guarded-command behavior under
test (confirm tokens, approvals, two-man signatures, idempotency replay,
human-gate preconditions) is exercised through the real
``services/control-plane/bff/command_adapters`` and
``services/control-plane/bff/control_loops`` modules.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


def extract_identity_from_bearer_stub(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    **_kwargs: Any,
) -> Any:
    """Adapt the canonical stub extractor to the ``(auth, mfa_token=...)`` shape
    used by the command-adapters/control-loops router factories."""
    return extract_identity_stub(authorization)


class ApprovalDecisionReadSurface(ReadSurfacePorts):
    """Real ``ReadSurfacePorts`` composition with an appendable, in-memory
    approval-decisions list wired through the real
    ``ooda_management_kwargs={"approval_decisions": ...}`` seam (the same
    mechanism ``ports.create_in_memory_read_surface_ports`` exposes for
    production callers), so ``get_approval_decision``/``list_approval_decisions``
    use the real ``ports.read_surface_ports`` lookup logic rather than a
    test-local reimplementation.
    """

    def __init__(self) -> None:
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
    routers described in the module docstring."""
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
