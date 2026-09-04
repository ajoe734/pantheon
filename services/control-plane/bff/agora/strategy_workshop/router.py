"""Agora strategy-workshop router -- agora.workshop.v1.

Implements the /bff/agora/workshops/* route family per the AG-BE-SW-001
contract (docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/
03_servant_and_workshop_contracts.md §B).

This module is composition-only (ACG-06-004): it builds the shared store,
canonical-operations client, and command-admission context, then assembles
one APIRouter out of four route-group subrouters that each own a disjoint
slice of the contract:

  routes/session.py   -- create/read/list, messages, events, completeness,
                          cards, readiness, reconstruct
  routes/versions.py  -- list/create/select StrategySpec versions
  routes/execution.py -- research runs, consultations, conclude
  routes/stream.py    -- SSE aggregate stream

Routes implemented (owning module in parens):
  GET  /bff/agora/workshops                          (session)
  POST /bff/agora/workshops                          (session)
  GET  /bff/agora/workshops/{workshop_id}             (session)
  POST /bff/agora/workshops/{workshop_id}/messages    (session)
  GET  /bff/agora/workshops/{workshop_id}/events      (session)
  GET  /bff/agora/workshops/{workshop_id}/completeness (session)
  POST /bff/agora/workshops/{workshop_id}/completeness (session)
  GET  /bff/agora/workshops/{workshop_id}/cards       (session)
  GET  /bff/agora/workshops/{workshop_id}/readiness   (session)
  POST /bff/agora/workshops/{workshop_id}/readiness/reassess (session)
  POST /bff/agora/workshops/{workshop_id}/reconstruct (session)
  GET/POST /bff/agora/workshops/{id}/versions         (versions)
  POST     /bff/agora/workshops/{id}/versions/{ver}/select (versions)
  POST     /bff/agora/workshops/{id}/research-runs    (execution)
  POST     /bff/agora/workshops/{id}/consultations    (execution)
  POST     /bff/agora/workshops/{id}/conclude         (execution)
  GET      /bff/agora/workshops/{id}/stream           (stream)

Routes still in main.py (migration pending -- see router stub comment):
  GET  /bff/agora/training-examples
  POST /bff/agora/training-examples
  ...  (all the old committee/evaluation/persona-lab routes)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from ._admission import build_admission_context
from ._common import _StrategyVersionProjectionError  # noqa: F401  (back-compat re-export)
from .cards import _build_workshop_cards, _merge_cards  # noqa: F401  (back-compat re-export)
from .events import (  # noqa: F401  (back-compat re-export -- see AG-BE-SW-004 tests)
    _WS_SSE_BUFFER_SIZE,
    _workshop_sse_buffers,
    _workshop_sse_subscribers,
    _ws_event_id,
    _ws_get_buffer,
    _ws_get_subscribers,
    _ws_publish,
    _ws_replay_after,
    _ws_sse_format,
    _ws_utc_now,
)
from .operations import CanonicalOperationError, WorkshopCanonicalOperations  # noqa: F401
from .readiness import build_readiness_assessment as _build_readiness_assessment  # noqa: F401
from .reconstruction import StrategyReconstructionResult, reconstruct_strategy_from_events  # noqa: F401
from .routes.execution import build_execution_router
from .routes.session import build_session_router
from .routes.stream import build_stream_router
from .routes.versions import build_versions_router
from .runner import run_reconstruction_worker  # noqa: F401
from .schemas import (  # noqa: F401  (back-compat re-export)
    WorkshopCompletenessSnapshotRequest,
    WorkshopConcludeRequest,
    WorkshopConsultationRequest,
    WorkshopCreateRequest,
    WorkshopMessageRequest,
    WorkshopReadinessReassessRequest,
    WorkshopResearchRunRequest,
    WorkshopVersionCreateRequest,
)
from .store import WorkshopVersionProjectionConflict, make_workshop_store  # noqa: F401

from services.control_plane.privacy.private_content_store import (
    EphemeralKeyProvider,
    MemoryPrivateContentStore,
)


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #

def create_strategy_workshop_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    workshop_store: Any = None,
    private_content_store: Any = None,
    canonical_operations: Any = None,
) -> APIRouter:
    """Build and return the strategy-workshop APIRouter.

    ``workshop_store`` may be injected (e.g. a MemoryWorkshopStore in tests).
    When omitted the store is constructed from AGORA_WORKSHOP_STORE_BACKEND env.
    """
    store = workshop_store if workshop_store is not None else make_workshop_store()
    canonical = (
        canonical_operations
        if canonical_operations is not None
        else WorkshopCanonicalOperations()
    )
    if private_content_store is None:
        private_content_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())

    ctx = build_admission_context(
        store=store,
        canonical=canonical,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )

    # Flatten each subrouter's already-built APIRoute objects directly onto
    # `router.routes` rather than calling `router.include_router(...)`.
    # FastAPI's include_router wraps the child in a lazily-resolved
    # `_IncludedRouter` node, which is transparent to request dispatch and
    # to OpenAPI generation but breaks the (pre-existing, still-relied-on)
    # test pattern of walking `router.routes` for a route's `.path` /
    # `.endpoint` directly. Extending preserves the exact flat route list
    # the single-file factory used to produce.
    router = APIRouter(tags=["agora-workshop"])
    router.routes.extend(build_session_router(
        store=store,
        canonical=canonical,
        private_content_store=private_content_store,
        utc_now=utc_now,
        bff_error=bff_error,
        ctx=ctx,
    ).routes)
    router.routes.extend(build_versions_router(
        store=store,
        canonical=canonical,
        utc_now=utc_now,
        bff_error=bff_error,
        ctx=ctx,
    ).routes)
    router.routes.extend(build_execution_router(
        store=store,
        canonical=canonical,
        utc_now=utc_now,
        bff_error=bff_error,
        ctx=ctx,
    ).routes)
    router.routes.extend(build_stream_router(
        store=store,
        utc_now=utc_now,
        bff_error=bff_error,
        ctx=ctx,
    ).routes)
    return router
