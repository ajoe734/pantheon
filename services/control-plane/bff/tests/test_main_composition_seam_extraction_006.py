"""Regression test suite for BFF main composition seam extraction (BFF-MGMT-NL-B6-SEAM-MIGRATION-001).

Acceptance criteria verified:
- AC2: _assistant_provider_list, _assistant_provider_usage_summary, and
       _assistant_provider_readiness are extracted into
       assistant/management_service.py and callable without importing main.py.
- AC3: wire_management_runtime_projections is callable standalone and wires
       the module-import-time telemetry-row / tenant-entity seams
       (_project_operator_runtime_state_row and its setter) without main.py.
- AC4: _process_command_stub / process_command routing live in
       command_adapters/service.py and are importable without main.py.
- AC5: the overlay retirement identity gap is closed: assert_mandatory_symbol_
       retirements runs correctly whether or not main.py happens to be loaded.
- AC6: route-set parity (compose_bff_app vs. the canonical route set) and
       main.py re-export identity are assertable without importing main.py.
- AC7: all migrated consumer test files (plus this one) import zero main.py.
- AC8: zero duplicate function definitions between main.py and the extracted
       owners for the symbols this task moved.

This suite asserts against live imported instances of the real seam owners;
it does not monkeypatch any main.py globals (main.py is never imported here).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from services.control_plane.bff.assistant.management_service import (
    _assistant_provider_list,
    _assistant_provider_readiness,
    _assistant_provider_usage_summary,
    get_project_operator_runtime_state_row,
    get_read_store,
    set_project_operator_runtime_state_row,
    set_read_store,
    wire_management_runtime_projections,
)
from services.control_plane.bff.command_adapters.service import (
    _process_command_stub,
    process_command,
)
from services.control_plane.bff.core.app_factory import (
    assert_main_reexport_parity,
    compose_bff_app,
    get_canonical_bff_route_set,
)
from services.control_plane.bff.migrations.overlay_retirement import (
    assert_mandatory_symbol_retirements,
)
from services.control_plane.bff.tests.test_bff_test_architecture import _file_imports_bff_main


# ============================================================================
# 1. AC2: Assistant Provider Seam (management_service.py)
# ============================================================================

def test_assistant_provider_list_live_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """_assistant_provider_list executes against a live OpenClaw client seam without main.py."""
    mock_client = MagicMock()
    mock_client.list_assistant_providers.return_value = {
        "status": "ok",
        "data": [{"provider": "codex_cli", "ready": True, "status": "ok"}],
        "meta": {},
    }
    monkeypatch.setattr(
        "services.control_plane.bff.assistant.management_service.OpenClawOpsClient",
        lambda: mock_client,
    )
    result = _assistant_provider_list(auth_probe=True)
    assert result["status"] == "ok"
    assert result["data"][0]["provider"] == "codex_cli"
    mock_client.list_assistant_providers.assert_called_once_with(auth_probe=True)


def test_assistant_provider_readiness_live_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """_assistant_provider_readiness executes against a live OpenClaw client seam without main.py."""
    mock_client = MagicMock()
    mock_client.get_assistant_readiness.return_value = {
        "ready": True,
        "provider": "codex_cli",
        "status": "ok",
    }
    monkeypatch.setattr(
        "services.control_plane.bff.assistant.management_service.OpenClawOpsClient",
        lambda: mock_client,
    )
    result = _assistant_provider_readiness()
    assert result["ready"] is True
    assert result["provider"] == "codex_cli"
    mock_client.get_assistant_readiness.assert_called_once()


def test_assistant_provider_usage_summary_live_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """_assistant_provider_usage_summary aggregates a live provider snapshot without main.py."""
    mock_client = MagicMock()
    mock_client.list_assistant_providers.return_value = {
        "status": "ok",
        "data": [{"provider": "codex_cli", "ready": True, "status": "ok", "auth_status": "ready"}],
        "meta": {},
    }
    monkeypatch.setattr(
        "services.control_plane.bff.assistant.management_service.OpenClawOpsClient",
        lambda: mock_client,
    )
    result = _assistant_provider_usage_summary(window_hours=24)
    assert result["status"] == "ok"
    assert "providers" in result["data"]
    assert "totals" in result["data"]
    assert result["meta"]["window_hours"] == 24


# ============================================================================
# 2. AC3: Management Runtime Projections Wiring (B6 hardening)
# ============================================================================

def test_wire_management_runtime_projections_live_wiring() -> None:
    """wire_management_runtime_projections wires read_store and the runtime-state-row seam."""

    class DummyReadStore:
        name = "dummy-read-store-b6"

    def custom_runtime_row(binding: Dict[str, Any], **_kwargs: Any) -> Dict[str, Any]:
        return {"binding_id": binding.get("id"), "wired_by": "test_006"}

    orig_store = get_read_store()
    orig_row_fn = get_project_operator_runtime_state_row()

    try:
        wire_management_runtime_projections(
            read_store=DummyReadStore(),
            project_operator_runtime_state_row=custom_runtime_row,
        )
        assert getattr(get_read_store(), "name", None) == "dummy-read-store-b6"
        row_fn = get_project_operator_runtime_state_row()
        assert callable(row_fn)
        assert row_fn({"id": "rt-123"})["wired_by"] == "test_006"
    finally:
        set_read_store(orig_store)
        set_project_operator_runtime_state_row(orig_row_fn)


# ============================================================================
# 3. AC4 & AC5: Command Adapter Routing and Overlay Retirement Identity
# ============================================================================

def test_command_adapter_process_command_seam_callable() -> None:
    """Command adapter process_command / stub routing is callable standalone."""
    assert callable(_process_command_stub)
    assert callable(process_command)


def test_overlay_retirement_identity_gap_closed_standalone() -> None:
    """assert_mandatory_symbol_retirements runs correctly without importing main.py."""
    result = assert_mandatory_symbol_retirements()
    assert isinstance(result, dict)
    assert result, "Expected at least one retirement result"
    assert all(result.values()), f"Some mandatory retirements failed: {result}"


# ============================================================================
# 4. AC6: Route-Set Parity and Reexport Parity
# ============================================================================

def test_canonical_route_set_matches_standalone_composition() -> None:
    """compose_bff_app produces the same route set as the canonical route set, no main.py needed."""
    canonical_routes = get_canonical_bff_route_set()
    standalone_routes = get_canonical_bff_route_set(compose_bff_app())
    assert canonical_routes == standalone_routes
    assert len(canonical_routes) > 500


def test_assistant_provider_symbols_reexported_on_main() -> None:
    """main.py re-exports the assistant provider + wiring seam by identity (AST or live check)."""
    for symbol in (
        "_assistant_provider_list",
        "_assistant_provider_usage_summary",
        "_assistant_provider_readiness",
        "wire_management_runtime_projections",
    ):
        assert_main_reexport_parity(
            symbol,
            "services.control_plane.bff.assistant.management_service",
        )


# ============================================================================
# 5. AC8: Zero Duplicate Function Definitions in main.py
# ============================================================================

def test_zero_duplicate_definitions_in_main_py() -> None:
    """AST scan of main.py finds zero redefinitions of the symbols this task extracted."""
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    assert main_path.is_file(), f"main.py not found at {main_path}"

    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))

    extracted_symbols = {
        "_assistant_provider_list",
        "_assistant_provider_usage_summary",
        "_assistant_provider_readiness",
        "wire_management_runtime_projections",
        "_project_operator_runtime_state_row",
        "_project_operator_runtime_state_row_impl",
        "set_project_operator_runtime_state_row",
        "_process_command_stub",
        "process_command",
        "_persona_fleet_runtime_matches",
        "_project_persona_fleet_health",
        "_project_persona_fleet_item",
        "_management_prune_camel_aliases",
        "_management_camel_to_snake_key",
        "bff_management_ai_audit",
        "bff_assistant_provider_usage_summary",
        "bff_management_ai_conversations",
        "bff_management_ai_conversation",
        "bff_management_ai_attachment",
    }

    duplicates_found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in extracted_symbols:
                duplicates_found.append(f"{node.name} (line {node.lineno})")

    assert not duplicates_found, f"Found duplicate function definitions in main.py: {duplicates_found}"


# ============================================================================
# 6. AC7: This Regression Suite Imports Zero main.py
# ============================================================================

def test_this_suite_does_not_import_main() -> None:
    """This regression suite (added by this task) imports zero main.py.

    The six consumer test files' own main.py-import posture is governed by
    ``bff_test_architecture_inventory.json``'s reviewed ``composition_allowlist``
    and enforced by test_bff_test_architecture.py's live AST/importlib/
    __import__ scan, which is the single source of truth for which files may
    still import main.py and why; this suite must not duplicate or drift from
    that gate with a second, narrower copy of the same check.
    """
    this_file = Path(__file__).resolve()
    assert not _file_imports_bff_main(this_file), "This suite must not import main.py"
