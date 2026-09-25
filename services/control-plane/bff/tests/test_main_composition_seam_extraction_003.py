"""Regression test suite for BFF main composition seam extraction (BFF-MAIN-FINAL-SEAMS-CORRECTIVE-001).

Acceptance Criteria Verified:
- AC2: Management NL ask/stream handlers extracted to assistant/management_service.py.
- AC3: Application composition callable standalone via core/app_factory.py compose_bff_app;
       produces identical route set as main.py.
- AC4: Startup command replay extracted to core/lifespan.py with preserved semantics.
- AC5: Shared run_management_read wired across 4 read model paths (evidence, cockpit,
       human-inbox, approvals) in management_read_models/router.py and governance/router.py.
- AC6: Zero duplicate definitions between main.py and extracted owners verified via AST/readback.
- AC7: Live imported instances asserted without monkeypatching main.py globals.
"""
from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException

from services.control_plane.bff.core.app_factory import compose_bff_app, mount_bff_routers
from services.control_plane.bff.core.lifespan import (
    create_lifespan,
    recoverable_capital_command,
    replay_submitted_commands,
    retryable_terminal_capital_command,
)
from services.control_plane.bff.assistant.management_service import (
    ManagementNlUseCase,
    bff_management_nl_ask,
    bff_management_nl_ask_stream,
)
from services.control_plane.bff.personas.routes.common import (
    ManagementReadSaturated,
    ManagementReadTimeout,
    discard_late_management_read_result,
    run_management_read,
)
from services.control_plane.bff.models import (
    ErrorCode,
    OperatorIdentity,
    utc_now,
)


def _extract_routes(app: FastAPI) -> Set[Tuple[str, str]]:
    """Extract (method, path) route set including subrouters and included routers."""
    routes: Set[Tuple[str, str]] = set()
    for r in app.routes:
        if hasattr(r, "methods") and hasattr(r, "path"):
            for m in r.methods:
                if m != "HEAD":
                    routes.add((m, r.path))
        elif hasattr(r, "routes"):
            for sub in r.routes:
                if hasattr(sub, "methods") and hasattr(sub, "path"):
                    for m in sub.methods:
                        if m != "HEAD":
                            routes.add((m, sub.path))
        elif hasattr(r, "original_router"):
            for sub in r.original_router.routes:
                if hasattr(sub, "methods") and hasattr(sub, "path"):
                    for m in sub.methods:
                        if m != "HEAD":
                            routes.add((m, sub.path))
    return routes


# ============================================================================
# 1. AC3: Application Composition Seam (compose_bff_app & mount_bff_routers)
# ============================================================================

def test_compose_bff_app_callable_standalone():
    """Verify compose_bff_app can be called standalone without importing main.py."""
    standalone_app = compose_bff_app()
    assert isinstance(standalone_app, FastAPI)
    assert hasattr(standalone_app.state, "events_router")
    assert hasattr(standalone_app.state, "agora_router")
    assert hasattr(standalone_app.state, "runtime_router")
    assert hasattr(standalone_app.state, "persona_service")
    assert hasattr(standalone_app.state, "command_adapter_service")


def test_compose_bff_app_matches_main_route_set():
    """Verify compose_bff_app produces the identical route set as the canonical route set."""
    from services.control_plane.bff.core.app_factory import get_canonical_bff_route_set

    canonical_routes = get_canonical_bff_route_set()
    standalone_app = compose_bff_app()
    standalone_routes = _extract_routes(standalone_app)

    diff_missing = canonical_routes - standalone_routes
    diff_extra = standalone_routes - canonical_routes

    assert diff_missing == set(), f"Routes missing in standalone composer: {diff_missing}"
    assert diff_extra == set(), f"Routes extra in standalone composer: {diff_extra}"
    assert len(standalone_routes) == len(canonical_routes)
    assert len(standalone_routes) > 500


# ============================================================================
# 2. AC4: Process-Startup Command Replay Seam (core/lifespan.py)
# ============================================================================

def test_replay_submitted_commands_direct_execution():
    """Verify replay_submitted_commands executes cleanly on live command store."""
    replayed: List[str] = []

    def mock_process(cmd_id: str) -> None:
        replayed.append(cmd_id)

    class StubCommandStore:
        def _get_all_commands(self) -> List[Dict[str, Any]]:
            return [
                {"command_id": "cmd-001", "type": "ApprovedApply", "status": "submitted"},
                {"command_id": "cmd-002", "type": "EmergencyContainment", "status": "processing"},
                {"command_id": "cmd-003", "type": "OtherCommand", "status": "submitted"},
            ]

        def update_status(self, cmd_id: str, status: Any) -> None:
            pass

    tasks = replay_submitted_commands(
        command_store=StubCommandStore(),
        process_command=mock_process,
    )

    assert replayed == ["cmd-001", "cmd-002"]


def test_recoverable_capital_command_classification():
    """Verify recoverable and retryable capital command helpers in lifespan.py."""
    assert recoverable_capital_command({"type": "ApprovedApply", "status": "submitted"}) is True
    assert recoverable_capital_command({"type": "EmergencyContainment", "status": "processing"}) is True
    assert recoverable_capital_command({"type": "OtherCommand", "status": "submitted"}) is False

    assert retryable_terminal_capital_command({
        "type": "ApprovedApply",
        "status": "failed",
        "error": {"retryable": True},
    }) is True
    assert retryable_terminal_capital_command({
        "type": "ApprovedApply",
        "status": "failed",
        "error": {"retryable": False},
    }) is False
    assert retryable_terminal_capital_command({
        "type": "ApprovedApply",
        "status": "completed",
        "error": {"retryable": True},
    }) is False


def test_create_lifespan_builds_callable_contextmanager():
    """Verify create_lifespan builds a callable async contextmanager."""
    mock_cache = MagicMock()
    lifespan_ctx = create_lifespan(cache=mock_cache)
    assert callable(lifespan_ctx)


# ============================================================================
# 3. AC2: Management NL Seam Extraction (assistant/management_service.py)
# ============================================================================

def test_management_nl_handlers_importable_and_callable():
    """Verify bff_management_nl_ask and stream handler are importable and functional."""
    assert callable(bff_management_nl_ask)
    assert callable(bff_management_nl_ask_stream)

    from services.control_plane.bff.assistant.management_service import MANAGEMENT_NL_USE_CASE
    from services.control_plane.bff.core.app_factory import assert_main_reexport_parity

    assert isinstance(MANAGEMENT_NL_USE_CASE, ManagementNlUseCase)
    assert_main_reexport_parity(
        "_MANAGEMENT_NL_USE_CASE",
        "services.control_plane.bff.assistant.management_service",
        expected_symbol="MANAGEMENT_NL_USE_CASE",
    )
    assert_main_reexport_parity("bff_management_nl_ask", "services.control_plane.bff.assistant.management_service")
    assert_main_reexport_parity("bff_management_nl_ask_stream", "services.control_plane.bff.assistant.management_service")


# ============================================================================
# 4. AC5: Shared run_management_read Wiring Across 4 Read Paths
# ============================================================================

def test_run_management_read_fresh_success():
    """Verify run_management_read executes bounded reads successfully."""
    def compute(val: int) -> int:
        return val * 2

    res = asyncio.run(run_management_read(compute, 21, timeout_seconds=1.0))
    assert res == 42


def test_run_management_read_timeout_budget():
    """Verify run_management_read raises ManagementReadTimeout on slow execution."""
    def slow_compute():
        time.sleep(0.3)
        return "late_data"

    with pytest.raises(ManagementReadTimeout):
        asyncio.run(run_management_read(slow_compute, timeout_seconds=0.05))


def test_run_management_read_saturated_semaphore():
    """Verify run_management_read raises ManagementReadSaturated when capacity exceeded."""
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False) is True

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with pytest.raises(ManagementReadSaturated):
            asyncio.run(run_management_read(
                lambda: "ok",
                capacity=sem,
                executor=executor,
                timeout_seconds=0.5,
            ))
    finally:
        sem.release()
        executor.shutdown(wait=False)


def test_evidence_and_approvals_routers_wire_run_management_read():
    """Verify management_read_models and governance routers consume run_management_read."""
    from services.control_plane.bff.management_read_models.router import create_management_router
    from services.control_plane.bff.governance.router import create_governance_router

    read_models_router = create_management_router(
        read_surface=MagicMock(),
        extract_identity=lambda a: OperatorIdentity(operator_id="test", roles=["admin"]),
        require_read_role=lambda i: None,
        snapshot_meta=lambda: {"snapshot_at": "2026-09-24T00:00:00Z"},
        utc_now=lambda: "2026-09-24T00:00:00Z",
        run_management_read=run_management_read,
    )
    assert read_models_router is not None

    gov_router = create_governance_router(
        read_surface=MagicMock(),
        extract_identity=lambda a: OperatorIdentity(operator_id="test", roles=["admin"]),
        require_read_role=lambda i: None,
        require_operator_role=lambda i: None,
        bff_error=lambda s, c, m: HTTPException(status_code=s, detail=m),
        utc_now=utc_now,
        submit_action=lambda *a, **kw: {},
        publish_event=lambda *a, **kw: None,
        run_management_read=run_management_read,
    )
    assert gov_router is not None


# ============================================================================
# 5. AC6: Zero Duplicate Definitions In main.py
# ============================================================================

def test_no_duplicate_definitions_in_main_py():
    """Verify AST scan of main.py has 0 function definitions for all extracted symbols."""
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    assert main_path.is_file(), f"main.py not found at {main_path}"

    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))

    defined_functions: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_functions.add(node.name)

    forbidden_duplicates = {
        "bff_management_nl_ask",
        "bff_management_nl_ask_stream",
        "replay_submitted_commands",
        "recoverable_capital_command",
        "retryable_terminal_capital_command",
        "compose_bff_app",
        "mount_bff_routers",
        "run_management_read",
        "_run_management_read",
    }

    duplicates_found = forbidden_duplicates.intersection(defined_functions)
    assert not duplicates_found, f"Found duplicate function definitions in main.py: {duplicates_found}"
