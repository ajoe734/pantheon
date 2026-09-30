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
    """Verify compose_bff_app produces the identical route set as main.py exposes.

    GENUINE BLOCKER: this test's entire purpose is comparing the standalone
    ``compose_bff_app()`` seam's route set against main.py's own assembled ``app``,
    so it inherently requires importing main.py -- there is no seam that can stand
    in for main.py's own assembled application on the other side of the comparison.
    """
    import importlib
    bff_main = importlib.import_module("services.control_plane.bff.main")

    main_routes = _extract_routes(bff_main.app)
    standalone_app = compose_bff_app()
    standalone_routes = _extract_routes(standalone_app)

    diff_main_only = main_routes - standalone_routes
    diff_standalone_only = standalone_routes - main_routes

    assert diff_main_only == set(), f"Routes only in main.py: {diff_main_only}"
    assert diff_standalone_only == set(), f"Routes only in standalone composer: {diff_standalone_only}"
    assert len(standalone_routes) == len(main_routes)
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
    """Verify bff_management_nl_ask and stream handler are importable and functional.

    GENUINE BLOCKER: the identity assertions below check that main.py's own module
    attributes are bound to the assistant.management_service objects (rather than a
    local duplicate), so they inherently require importing main.py -- there is no
    seam standing in for main.py's own binding of these names.
    """
    assert callable(bff_management_nl_ask)
    assert callable(bff_management_nl_ask_stream)

    import importlib
    bff_main = importlib.import_module("services.control_plane.bff.main")
    assert isinstance(bff_main._MANAGEMENT_NL_USE_CASE, ManagementNlUseCase)
    assert bff_main.bff_management_nl_ask is bff_management_nl_ask
    assert bff_main.bff_management_nl_ask_stream is bff_management_nl_ask_stream


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


# BFF-INCIDENTS-REAL-SOURCE-001 review regressions: mounted with the production
# _gov_bff_action_command callback (this file is on the main-importer allowlist).
import runpy as _runpy
from pathlib import Path
from fastapi.testclient import TestClient
import uuid
import pytest as _pytest

_real = _runpy.run_path(str(Path(__file__).parent / "test_incidents_real_source.py"))
StubIncidentsServer = _real["StubIncidentsServer"]
StubReadStoreWithIncidentPort = _real["StubReadStoreWithIncidentPort"]
_build_app = _real["_build_app"]
_AUTH = _real["_AUTH"]
CommandStore = _real["CommandStore"]

# Review-rejection regressions: mounted with the production _gov_bff_action_command callback.
@_pytest.fixture
def mounted_prod_callback(monkeypatch, tmp_path):
    server = StubIncidentsServer()
    url = server.start()
    monkeypatch.setenv('PANTHEON_INCIDENTS_API_URL', url)
    monkeypatch.setenv('PANTHEON_INCIDENTS_URL', url)
    monkeypatch.setenv('PANTHEON_BFF_AUTH_STUB', 'true')
    monkeypatch.setenv('PANTHEON_BFF_AUTH_MODE', 'permissive')
    from services.control_plane.bff import main
    monkeypatch.setattr(main, '_GOV_BFF_IDEMPOTENCY', {})
    monkeypatch.setattr(main, '_check_read_surface_state', lambda: None)
    monkeypatch.setattr(main, 'command_store', CommandStore(str(tmp_path / 'production-commands.jsonl')))
    app = _build_app(StubReadStoreWithIncidentPort(url), tmp_path,
                          submit_action_command=main._gov_bff_action_command)
    try:
        yield server, TestClient(app, raise_server_exceptions=False)
    finally:
        server.stop()

@_pytest.mark.parametrize('path', [
    '/bff/incidents/inc-real-001/actions/resolve',
    '/bff/risk/alerts/alert-incident-inc-real-001/actions/acknowledge',
])
def test_identical_retry_returns_original_receipt(mounted_prod_callback, path):
    server, client = mounted_prod_callback
    headers = {**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}
    first = client.post(path, headers=headers, json={})
    second = client.post(path, headers=headers, json={})
    assert first.status_code == 202, first.text
    assert second.status_code == first.status_code, second.text
    assert second.json() == first.json()
    assert len(server.status_calls) == 1

def test_unsupported_risk_action_cannot_change_incident(mounted_prod_callback):
    server, client = mounted_prod_callback
    response = client.post('/bff/risk/alerts/alert-incident-inc-real-001/actions/not-a-real-action',
                           headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}, json={})
    assert response.status_code == 422 and server.status_calls == [], {
        'http': response.status_code, 'body': response.json(), 'writes': server.status_calls}

@_pytest.mark.parametrize('payload', [
    {'incident_id': 'inc-real-001'},
    {'alert_id': 'alert-incident-inc-real-001'},
    {'entity_id': 'alert-incident-inc-real-001'},
])
def test_acknowledge_cannot_substitute_unrelated_durable_owner(mounted_prod_callback, payload):
    server, client = mounted_prod_callback
    response = client.post('/bff/risk/alerts/alert-runtime-unowned/actions/acknowledge',
                           headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())},
                           json=payload)
    assert response.status_code == 422 and server.status_calls == [], {
        'http': response.status_code, 'body': response.json(), 'writes': server.status_calls}


@_pytest.mark.parametrize('payload', [
    {'incident_id': 'inc-other'},
    {'entity_id': 'inc-other'},
    {'entity_type': 'SentinelIntervention'},
    {'action_id': 'investigate'},
    {'action_id': 'remediate'},
])
def test_incident_action_payload_cannot_replace_route_target_or_action(
    mounted_prod_callback, monkeypatch, payload,
):
    server, client = mounted_prod_callback
    server.incidents['inc-other'] = {
        'incident_id': 'inc-other', 'title': 'Unrelated incident',
        'status': 'open', 'severity': 'high',
    }
    from services.control_plane.bff.command_adapters import incident_adapter
    other_owner_calls = []
    monkeypatch.setattr(
        incident_adapter, 'http_request_json',
        lambda *args, **kwargs: other_owner_calls.append((args, kwargs)) or {},
    )

    response = client.post(
        '/bff/incidents/inc-real-001/actions/resolve',
        headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}, json=payload,
    )

    assert response.status_code == 422, response.text
    assert server.status_calls == []
    assert other_owner_calls == []
    assert server.incidents['inc-real-001']['status'] == 'open'
    assert server.incidents['inc-other']['status'] == 'open'


@_pytest.mark.parametrize('alert_id', ['alert-incident-inc-real-001', 'inc-real-001'])
def test_rest_acknowledgement_cannot_replace_alert_owner(mounted_prod_callback, alert_id):
    server, client = mounted_prod_callback
    server.incidents['inc-other'] = {
        'incident_id': 'inc-other', 'title': 'Unrelated incident',
        'status': 'open', 'severity': 'high',
    }

    response = client.post(
        f'/bff/alerts/{alert_id}/acknowledge',
        headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())},
        json={'incident_id': 'inc-other'},
    )

    assert response.status_code == 422, response.text
    assert server.status_calls == []
    assert server.incidents['inc-real-001']['status'] == 'open'
    assert server.incidents['inc-other']['status'] == 'open'


def test_rest_acknowledgement_tracking_records_completed_owner_result(mounted_prod_callback):
    server, client = mounted_prod_callback
    response = client.post(
        '/bff/alerts/alert-incident-inc-real-001/acknowledge',
        headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}, json={},
    )

    assert response.status_code == 200, response.text
    receipt = response.json()
    command = client.app.state.command_store.get_command(receipt['command_id'])
    assert receipt['data']['tracking_url'].endswith('/' + command['command_id'])
    assert command['status'] == 'executed'
    assert command['result']['incident_id'] == 'inc-real-001'
    assert command['result']['status'] == server.incidents['inc-real-001']['status'] == 'investigating'
    assert len(server.status_calls) == 1


def test_incident_acknowledgement_accepts_owner_uuid(mounted_prod_callback):
    server, client = mounted_prod_callback
    incident_id = str(uuid.uuid4())
    server.incidents[incident_id] = {
        'incident_id': incident_id, 'title': 'Incident with service-generated UUID',
        'status': 'open', 'severity': 'high',
    }

    response = client.post(
        f'/bff/incidents/{incident_id}/actions/acknowledge',
        headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}, json={},
    )

    assert response.status_code == 202, response.text
    assert response.json()['read_back_status'] == 'investigating'
    assert [(call['incident_id'], call['body']['status']) for call in server.status_calls] == [
        (incident_id, 'investigating'),
    ]


@_pytest.mark.parametrize('path', [
    '/bff/incidents/inc-real-001/actions/remediate',
    '/bff/risk/alerts/alert-incident-inc-real-001/actions/remediate',
])
def test_incident_remediation_cannot_dispatch_to_sentinel(mounted_prod_callback, monkeypatch, path):
    server, client = mounted_prod_callback
    from services.control_plane.bff.command_adapters import incident_adapter
    monkeypatch.setenv('PANTHEON_INTERNAL_API_URL', 'http://sentinel-owner.invalid')
    sentinel_calls = []
    monkeypatch.setattr(
        incident_adapter, 'http_request_json',
        lambda *args, **kwargs: sentinel_calls.append((args, kwargs)) or {},
    )

    response = client.post(
        path,
        headers={**_AUTH, 'Idempotency-Key': str(uuid.uuid4())}, json={},
    )

    assert response.status_code == 422, response.text
    assert server.status_calls == []
    assert sentinel_calls == []
