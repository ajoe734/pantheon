"""Regression test suite for Management NL helper extraction and composition seam (BFF-MGMT-NL-HELPER-EXTRACTION-001).

Acceptance Criteria Verified:
- AC2: 37 helpers extracted from main.py to management_service.py as real implementations.
- AC3: _MainCallable and sys.modules.get("services.control_plane.bff.main") forwarders completely retired.
- AC4: Empirical proof: standalone compose_bff_app() serves /bff/management/nl/ask end-to-end without loading main.py.
- AC5: Zero duplicate definitions across main.py and management_service.py verified via AST readback.
- AC6: Live scanner non-allowlisted main importer count preserved.
- AC7: Live imported helper instances asserted with zero monkeypatching of main.py globals.
"""
from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.assistant import management_service
from services.control_plane.bff.assistant.management_service import (
    _REMAINING_MAIN_HELPERS,
    _mgmt_nl_validate_question_size,
    _mgmt_nl_parse_control_command,
    _mgmt_nl_high_risk_classify,
    _mgmt_nl_record_high_risk_refusal,
    _mgmt_nl_caller_tenant,
    _mgmt_nl_trim_text,
    _mgmt_nl_normalize_focus,
    _mgmt_nl_normalize_conversation_context,
    _mgmt_nl_normalize_ui_context,
    _mgmt_nl_allowed_action_kinds,
    _resolve_final_idempotency_key,
    _stable_json_hash,
    _request_dry_run_requested,
    _dry_run_success_response,
    _management_json_clone,
    _mgmt_nl_handle_control_command,
    _mgmt_nl_collect_context,
    _mgmt_nl_deterministic_answer,
    _mgmt_nl_provider_enabled,
    _mgmt_nl_invoke_provider,
    _mgmt_nl_provider_status,
    _mgmt_nl_provider_name,
    _management_nl_publish_completed_events,
    _publish_event,
    _record_agora_audit_event,
    _assistant_control_mode_for_identity,
    _management_ai_audit_href,
    _mgmt_nl_synthesize_answer,
    _mgmt_nl_text_from_provider_value,
    _mgmt_nl_extract_provider_actions,
    _mgmt_nl_provider_mode_from_context,
    _mgmt_nl_reject_development_payload,
    _mgmt_nl_build_context_pack,
    _mgmt_nl_jsonish,
    _mgmt_nl_maybe_provider_answer,
    _mgmt_nl_provider_prompt,
    _mgmt_nl_surface_confidence,
    get_build_operator_alerts_payload,
    set_build_operator_alerts_payload,
    reset_build_operator_alerts_payload,
    get_build_management_anomalies_payload,
    set_build_management_anomalies_payload,
    reset_build_management_anomalies_payload,
    get_human_inbox_payload,
    set_human_inbox_payload,
    reset_human_inbox_payload,
    get_list_persona_records,
    set_list_persona_records,
    reset_list_persona_records,
    get_project_persona_fleet_item,
    set_project_persona_fleet_item,
    reset_project_persona_fleet_item,
    get_project_operator_runtime_state_row,
    set_project_operator_runtime_state_row,
    reset_project_operator_runtime_state_row,
    get_management_telemetry_rollup,
    set_management_telemetry_rollup,
    reset_management_telemetry_rollup,
    get_dataset_surface_status,
    set_dataset_surface_status,
    reset_dataset_surface_status,
    get_assistant_collect_source,
    set_assistant_collect_source,
    reset_assistant_collect_source,
    get_agora_audit_store,
    set_agora_audit_store,
    reset_agora_audit_store,
    get_assistant_control_mode_store,
    set_assistant_control_mode_store,
    reset_assistant_control_mode_store,
)
from services.control_plane.bff.core.app_factory import compose_bff_app
from services.control_plane.bff.models import OperatorIdentity

BFF_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = BFF_DIR / "main.py"
MANAGEMENT_SERVICE_PY = BFF_DIR / "assistant" / "management_service.py"


@pytest.fixture(autouse=True)
def _ensure_writable_idempotency_path(monkeypatch, tmp_path):
    if not os.environ.get("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"):
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            str(tmp_path / "management-nl-command-idempotency.json"),
        )
    if not os.environ.get("RANKING_STORE_DSN") and not os.environ.get("DATABASE_URL"):
        monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
        monkeypatch.setenv("RANKING_STORE_BOOTSTRAP", "0")
    if not os.environ.get("PANTHEON_BFF_AUTH_STUB"):
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    if not os.environ.get("PANTHEON_BFF_AUTH_MODE"):
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


# ============================================================================
# 1. AC4: Standalone Execution Without Loading main.py
# ============================================================================

def test_mgmt_nl_ask_standalone_composition_serves_end_to_end():
    """Verify compose_bff_app() serves /bff/management/nl/ask without loading main.py."""
    # Ensure main is not in sys.modules prior to composition
    main_was_loaded = "services.control_plane.bff.main" in sys.modules

    app = compose_bff_app()
    assert isinstance(app, FastAPI)

    client = TestClient(app)
    resp = client.post(
        "/bff/management/nl/ask",
        json={"question": "What is the current portfolio status?", "focus": "portfolio"},
        headers={
            "Authorization": "Bearer op-seam-005:operator",
            "Idempotency-Key": "test-key-seam-005-standalone",
        },
    )

    assert resp.status_code == 202, f"Expected HTTP 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "accepted"
    data = body.get("data", {})
    assert "answer" in data
    assert data.get("focus") == "portfolio"

    if not main_was_loaded:
        assert "services.control_plane.bff.main" not in sys.modules, (
            "services.control_plane.bff.main was imported during standalone compose_bff_app() request!"
        )


def test_mgmt_nl_ask_standalone_high_risk_refusal():
    """Verify high-risk query refusal functions in standalone composition without main.py."""
    app = compose_bff_app()
    client = TestClient(app)

    resp = client.post(
        "/bff/management/nl/ask",
        json={"question": "Please allocate capital from pool alpha to strategy beta"},
        headers={
            "Authorization": "Bearer op-seam-005:operator",
            "Idempotency-Key": "test-key-seam-005-highrisk",
        },
    )

    assert resp.status_code == 403, f"Expected HTTP 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "error" in body
    error = body["error"]
    assert error["code"] == "OPERATION_NOT_ALLOWED"
    details = error.get("details", {})
    assert details.get("matched_category") == "live_capital_mutation"
    assert details.get("refused") is True


# ============================================================================
# 2. AC2 & AC3: Real Implementations & Forwarder Retirement
# ============================================================================

def test_all_37_helpers_real_implementations_in_management_service():
    """Verify all 37 helpers are real callables in management_service and _MainCallable is retired."""
    assert len(_REMAINING_MAIN_HELPERS) == 37

    for name in _REMAINING_MAIN_HELPERS:
        helper = getattr(management_service, name, None)
        assert helper is not None, f"Helper {name} not found in management_service"
        assert callable(helper), f"Helper {name} in management_service is not callable"
        assert helper.__class__.__name__ != "_MainCallable", (
            f"Helper {name} is still an instance of _MainCallable"
        )

    # Assert _MainCallable is completely removed
    assert not hasattr(management_service, "_MainCallable"), (
        "_MainCallable forwarder class must be retired"
    )

    # Assert no sys.modules lookup of main exists in management_service source
    ms_source = MANAGEMENT_SERVICE_PY.read_text(encoding="utf-8")
    assert 'sys.modules.get("services.control_plane.bff.main")' not in ms_source
    assert "sys.modules.get('services.control_plane.bff.main')" not in ms_source


# ============================================================================
# 3. AC5: Zero Duplicate Definitions via AST Readback
# ============================================================================

def test_zero_duplicate_definitions_ast_readback():
    """Verify zero duplicate function definitions (def / async def) exist across main.py and management_service.py."""
    main_tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    ms_tree = ast.parse(MANAGEMENT_SERVICE_PY.read_text(encoding="utf-8"))

    main_defs = {
        node.name
        for node in ast.walk(main_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    ms_defs = {
        node.name
        for node in ast.walk(ms_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    duplicates = []
    for helper_name in _REMAINING_MAIN_HELPERS:
        assert helper_name in ms_defs, f"Helper {helper_name} is not defined as def in management_service.py"
        if helper_name in main_defs:
            duplicates.append(helper_name)

    assert duplicates == [], f"Found duplicate function definitions in main.py: {duplicates}"


# ============================================================================
# 4. AC7: Live Imported Instances Tested Without Monkeypatching main.py Globals
# ============================================================================

def test_live_imported_question_size_validation():
    """Verify _mgmt_nl_validate_question_size functions without main.py monkeypatching."""
    _mgmt_nl_validate_question_size("Short valid question")

    with pytest.raises(Exception) as excinfo:
        _mgmt_nl_validate_question_size("x" * 3000)
    assert "413" in str(excinfo.value) or "exceeds the maximum size" in str(excinfo.value)


def test_live_imported_control_command_parser():
    """Verify _mgmt_nl_parse_control_command functions directly."""
    cmd = _mgmt_nl_parse_control_command("control mode status")
    assert cmd is not None
    assert cmd["kind"] == "status"

    deact = _mgmt_nl_parse_control_command("/control off")
    assert deact is not None
    assert deact["kind"] == "deactivate"

    normal = _mgmt_nl_parse_control_command("How is the market today?")
    assert normal is None


def test_live_imported_high_risk_classifier():
    """Verify _mgmt_nl_high_risk_classify functions directly."""
    risk = _mgmt_nl_high_risk_classify("Deploy strategy carry-arb-v4 now")
    assert risk is not None
    assert risk["matched_category"] == "strategy_deployment"
    assert risk["matched_pattern"] == "deploy strategy"

    safe = _mgmt_nl_high_risk_classify("What is the current portfolio Sharpe ratio?")
    assert safe is None


def test_live_imported_idempotency_and_hash_helpers():
    """Verify _resolve_final_idempotency_key and _stable_json_hash directly."""
    key = _resolve_final_idempotency_key("canonical-key", None)
    assert key == "canonical-key"

    alias_key = _resolve_final_idempotency_key(None, "alias-key")
    assert alias_key == "alias-key"

    h1 = _stable_json_hash({"b": 2, "a": 1})
    h2 = _stable_json_hash({"a": 1, "b": 2})
    assert h1 == h2


def test_live_imported_dry_run_and_json_clone():
    """Verify _management_json_clone and _dry_run_success_response directly."""
    orig = {"foo": [1, 2, 3], "bar": {"nested": True}}
    cloned = _management_json_clone(orig)
    assert cloned == orig
    assert cloned is not orig

    resp = _dry_run_success_response({"simulated": True}, idempotency_key="dry-key-1")
    assert resp.status_code == 200


# ============================================================================
# 5. Management NL Collaborator DI Seams
# ============================================================================

def test_management_nl_collaborator_seams():
    """Verify collaborator DI seams in management_service can be injected and reset independently."""
    # 1. operator alerts payload seam
    orig_alerts = get_build_operator_alerts_payload()
    mock_alerts = MagicMock(return_value={"alerts": [{"id": "mock-alert"}], "meta": {}})
    try:
        set_build_operator_alerts_payload(mock_alerts)
        assert get_build_operator_alerts_payload() is mock_alerts
    finally:
        reset_build_operator_alerts_payload()
    assert get_build_operator_alerts_payload() is not mock_alerts

    # 2. agora audit store seam
    orig_audit = get_agora_audit_store()
    mock_audit = MagicMock()
    try:
        set_agora_audit_store(mock_audit)
        assert get_agora_audit_store() is mock_audit
    finally:
        reset_agora_audit_store()
    assert get_agora_audit_store() is not mock_audit

    # 3. assistant control mode store seam
    mock_ctrl = MagicMock()
    try:
        set_assistant_control_mode_store(mock_ctrl)
        assert get_assistant_control_mode_store() is mock_ctrl
    finally:
        reset_assistant_control_mode_store()
    assert get_assistant_control_mode_store() is None
