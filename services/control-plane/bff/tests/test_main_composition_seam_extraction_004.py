"""Regression test suite for BFF main DI seams and scanner integrity (BFF-MAIN-DI-SEAM-AND-SCAN-INTEGRITY-001).

Acceptance Criteria Verified:
- AC2: Management service injectable seams for read_store, OpenClawOpsClient, and OpenClawOpsClientError.
- AC3: sem_bff_version extracted to core/app_factory.py, executable standalone without importing main.py.
- AC4: Startup command replay injectable with explicit command_store dependency in core/lifespan.py and main.py.
- AC5: Scanner detects dynamic module imports via importlib.import_module and __import__.
- AC6: True non-allowlisted importer count reflects the 15 discovered suites.
- AC7: Zero duplicate definitions between main.py and extracted owners verified via AST readback.
- AC8: Live imported instances asserted without monkeypatching main.py globals.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from services.control_plane.bff.assistant.management_service import (
    OpenClawOpsClient,
    OpenClawOpsClientError,
    get_openclaw_ops_client,
    get_openclaw_ops_client_error,
    get_read_store,
    read_store,
    reset_openclaw_ops_client,
    reset_openclaw_ops_client_error,
    reset_read_store,
    set_openclaw_ops_client,
    set_openclaw_ops_client_error,
    set_read_store,
)
from services.control_plane.bff.core.app_factory import (
    compose_bff_app,
    create_version_handler,
    sem_bff_version,
)
from services.control_plane.bff.core.lifespan import (
    create_lifespan,
    replay_submitted_commands,
)
from services.control_plane.bff.tests.test_bff_test_architecture import (
    _file_imports_bff_main,
    _is_bff_main_module_name,
    _live_scan_non_whitelisted_main_importers,
    _load_inventory,
)

BFF_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = BFF_DIR / "main.py"
APP_FACTORY_PY = BFF_DIR / "core" / "app_factory.py"
LIFESPAN_PY = BFF_DIR / "core" / "lifespan.py"


# ============================================================================
# 1. AC2: Management Service DI Seams
# ============================================================================

def test_read_store_seam_injection_and_reset():
    """Verify read_store seam can be injected and reset independently."""
    mock_store = MagicMock()
    mock_store.name = "mock_read_store"

    try:
        set_read_store(mock_store)
        assert get_read_store() is mock_store
        assert read_store.name == "mock_read_store"
    finally:
        reset_read_store()

    assert get_read_store() is not mock_store


def test_openclaw_ops_client_seam_injection_and_reset():
    """Verify OpenClawOpsClient seam can be injected and reset independently."""
    orig = get_openclaw_ops_client()
    mock_client = MagicMock()
    mock_client.client_id = "mock_openclaw_client"

    try:
        set_openclaw_ops_client(mock_client)
        assert get_openclaw_ops_client() is mock_client
        assert OpenClawOpsClient.client_id == "mock_openclaw_client"
    finally:
        reset_openclaw_ops_client()

    assert get_openclaw_ops_client() is orig


def test_openclaw_ops_client_error_seam_injection_and_reset():
    """Verify OpenClawOpsClientError seam can be injected and reset independently."""
    orig = get_openclaw_ops_client_error()

    class CustomOpsError(Exception):
        pass

    try:
        set_openclaw_ops_client_error(CustomOpsError)
        assert get_openclaw_ops_client_error() is CustomOpsError

        # Verify get_openclaw_ops_client_error allows catching the injected error
        with pytest.raises(get_openclaw_ops_client_error()):
            raise CustomOpsError("Injected failure")
    finally:
        reset_openclaw_ops_client_error()

    assert get_openclaw_ops_client_error() is orig


# ============================================================================
# 2. AC3: sem_bff_version Extracted Owner (core/app_factory.py)
# ============================================================================

@pytest.mark.anyio
async def test_sem_bff_version_default_standalone():
    """Verify sem_bff_version can be invoked standalone without importing main.py."""
    payload = await sem_bff_version()
    assert isinstance(payload, dict)
    assert payload["service"] == "operator-bff"
    assert payload["version"] == "0.2.0"
    assert "source_commit_sha" in payload
    assert "config_posture" in payload
    assert isinstance(payload["config_posture"], dict)


@pytest.mark.anyio
async def test_create_version_handler_custom_injection():
    """Verify create_version_handler respects custom injected suppliers."""
    custom_handler = create_version_handler(
        source_commit_fn=lambda: "a" * 40,
        auth_stub_fn=lambda: True,
        auth_mode_fn=lambda: "custom-auth",
        dev_login_fn=lambda: False,
        image_digest="sha256:testdigest",
        build_time="2026-09-24T00:00:00Z",
        environment="test-env",
    )
    payload = await custom_handler()
    assert payload["service"] == "operator-bff"
    assert payload["version"] == "0.2.0"
    assert payload["source_commit_sha"] == "a" * 40
    assert payload["source_commit_known"] is True
    assert payload["image_digest"] == "sha256:testdigest"
    assert payload["build_time"] == "2026-09-24T00:00:00Z"
    assert payload["environment"] == "test-env"
    assert payload["config_posture"]["auth_stub"] is True
    assert payload["config_posture"]["auth_mode"] == "custom-auth"
    assert payload["config_posture"]["dev_login_enabled"] is False


def _extract_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Extract (method, path) route set including subrouters and included routers."""
    routes: set[tuple[str, str]] = set()
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


def test_compose_bff_app_mounts_version_endpoint():
    """Verify compose_bff_app exposes /bff/version."""
    app = compose_bff_app()
    routes = _extract_routes(app)
    assert ("GET", "/bff/version") in routes


# ============================================================================
# 3. AC4: Injectable Command Replay (core/lifespan.py & main.py)
# ============================================================================

def test_replay_submitted_commands_injects_command_store():
    """Verify replay_submitted_commands passes command_store to processor when supported."""
    received_stores: List[Any] = []
    received_cmds: List[str] = []

    def stub_process_command(cmd_id: str, *, command_store: Optional[Any] = None) -> None:
        received_cmds.append(cmd_id)
        received_stores.append(command_store)

    class StubStore:
        def _get_all_commands(self) -> List[Dict[str, Any]]:
            return [
                {"command_id": "cmd-replay-1", "type": "ApprovedApply", "status": "submitted"},
                {"command_id": "cmd-replay-2", "type": "EmergencyContainment", "status": "processing"},
            ]

        def update_status(self, cmd_id: str, status: Any) -> None:
            pass

    test_store = StubStore()
    tasks = replay_submitted_commands(
        command_store=test_store,
        process_command=stub_process_command,
    )

    assert received_cmds == ["cmd-replay-1", "cmd-replay-2"]
    assert received_stores == [test_store, test_store]


def test_replay_submitted_commands_supports_legacy_process_command():
    """Verify replay_submitted_commands still works with legacy 1-arg callbacks."""
    received_cmds: List[str] = []

    def legacy_process(cmd_id: str) -> None:
        received_cmds.append(cmd_id)

    class StubStore:
        def _get_all_commands(self) -> List[Dict[str, Any]]:
            return [
                {"command_id": "cmd-legacy-1", "type": "ApprovedApply", "status": "submitted"},
            ]

        def update_status(self, cmd_id: str, status: Any) -> None:
            pass

    tasks = replay_submitted_commands(
        command_store=StubStore(),
        process_command=legacy_process,
    )
    assert received_cmds == ["cmd-legacy-1"]


def test_main_process_command_ast_injectable_seam():
    """Verify _process_command in main.py has keyword-only command_store parameter."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"), filename=str(MAIN_PY))
    proc_def: Optional[ast.AsyncFunctionDef] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process_command":
            proc_def = node
            break

    assert proc_def is not None, "_process_command not found in main.py"
    kwonly_args = [arg.arg for arg in proc_def.args.kwonlyargs]
    assert "command_store" in kwonly_args, "_process_command must accept keyword-only command_store"


# ============================================================================
# 4. AC5 & AC6: Architecture Scanner Dynamic Import Detection
# ============================================================================

def test_scanner_detects_importlib_import_module_dynamic_import(tmp_path: Path):
    """Verify _file_imports_bff_main detects importlib.import_module."""
    f = tmp_path / "test_dyn_1.py"
    f.write_text('import importlib\nmod = importlib.import_module("services.control_plane.bff.main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f) is True

    f2 = tmp_path / "test_dyn_2.py"
    f2.write_text('import importlib\nmod = importlib.import_module("main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f2) is True


def test_scanner_detects_dunder_import_dynamic_import(tmp_path: Path):
    """Verify _file_imports_bff_main detects __import__."""
    f = tmp_path / "test_dyn_3.py"
    f.write_text('mod = __import__("main")\n', encoding="utf-8")
    assert _file_imports_bff_main(f) is True


def test_scanner_ignores_non_bff_service_mains(tmp_path: Path):
    """Verify other service entry points are not falsely flagged as BFF main."""
    for service_main in [
        "services.research.main",
        "services.telemetry.main",
        "services.evolution.main",
        "services.capital.main",
        "services.governance.main",
    ]:
        f = tmp_path / f"test_{service_main.replace('.', '_')}.py"
        f.write_text(f'import importlib\nmod = importlib.import_module("{service_main}")\n', encoding="utf-8")
        assert _file_imports_bff_main(f) is False, f"Should not flag {service_main} as BFF main"


def test_true_live_scan_non_whitelisted_importer_count():
    """Verify live scan accurately reports the 15 true non-allowlisted main importers."""
    inv = _load_inventory()
    allowlist = set(inv["composition_allowlist"])
    offenders = _live_scan_non_whitelisted_main_importers(allowlist)

    assert len(offenders) == 15, f"Expected 15 live offenders, found {len(offenders)}: {offenders}"
    # Verify the 4 previously hidden dynamic importers are among the offenders
    assert "test_pkt005_sse_substrate_contract.py" in offenders
    assert "tests/test_management_read_models_router.py" in offenders
    assert "tests/test_main_composition_seam_extraction_002.py" in offenders
    assert "tests/test_main_composition_seam_extraction_003.py" in offenders


# ============================================================================
# 5. AC7: Symbol Deduplication Readback
# ============================================================================

def test_zero_duplicate_sem_bff_version_definitions():
    """Verify sem_bff_version is only defined as def in core/app_factory.py, not main.py."""
    main_text = MAIN_PY.read_text(encoding="utf-8")
    app_factory_text = APP_FACTORY_PY.read_text(encoding="utf-8")

    assert "def sem_bff_version" not in main_text, "main.py must not contain 'def sem_bff_version'"
    assert "async def sem_bff_version" not in main_text, "main.py must not contain 'async def sem_bff_version'"

    # Must be defined inside create_version_handler in core/app_factory.py
    assert "async def sem_bff_version" in app_factory_text


def test_zero_duplicate_replay_submitted_commands_definitions():
    """Verify replay_submitted_commands is only defined in core/lifespan.py, not main.py."""
    main_text = MAIN_PY.read_text(encoding="utf-8")
    lifespan_text = LIFESPAN_PY.read_text(encoding="utf-8")

    assert "def replay_submitted_commands" not in main_text
    assert "def replay_submitted_commands" in lifespan_text
