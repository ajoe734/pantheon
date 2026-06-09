"""
Tests for BFF-FINAL-004: backend canonical action catalog.

Acceptance criteria verified:
  1. GET /bff/actions returns the canonical catalog.
  2. Governance metadata is present on every entry.
  3. Every CommandType has a catalog entry (no undocumented action).
  4. No catalog entry uses a `requires_*` string as a success status.
  5. High-risk entries carry appropriate gate metadata.
  6. Catalog entries can be projected to ActionDescriptor-compatible dicts.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from action_catalog import catalog_action_ids, get_action_catalog, get_catalog_entry
from models import ActionCommandStatus, BffActionCatalogEntry, CommandType, RiskLevel

OPERATOR_TOKEN = "Bearer op-2:operator"
APPROVER_TOKEN = "Bearer op-6:approver"


# --------------------------------------------------------------------------- #
# Catalog module unit tests
# --------------------------------------------------------------------------- #

def test_catalog_is_non_empty() -> None:
    response = get_action_catalog()
    assert len(response.catalog) > 0
    assert response.version == "v1"
    assert response.generated_at  # non-empty timestamp


def test_every_command_type_has_catalog_entry() -> None:
    catalogued = catalog_action_ids()
    missing = [ct.value for ct in CommandType if ct.value not in catalogued]
    assert not missing, f"CommandType values missing from action catalog: {missing}"


def test_catalog_entry_governance_fields_present() -> None:
    for entry in get_action_catalog().catalog:
        assert isinstance(entry.risk_level, RiskLevel), f"{entry.action_id}: risk_level missing"
        assert isinstance(entry.requires_approval, bool), f"{entry.action_id}: requires_approval missing"
        assert isinstance(entry.requires_confirm_token, bool), f"{entry.action_id}: requires_confirm_token missing"
        assert isinstance(entry.requires_two_man, bool), f"{entry.action_id}: requires_two_man missing"
        assert isinstance(entry.cooldown_seconds, int), f"{entry.action_id}: cooldown_seconds missing"
        assert isinstance(entry.idempotency_required, bool), f"{entry.action_id}: idempotency_required missing"
        assert isinstance(entry.required_roles, list), f"{entry.action_id}: required_roles missing"
        assert entry.endpoint, f"{entry.action_id}: endpoint is empty"
        assert entry.entity_type, f"{entry.action_id}: entity_type is empty"


def test_no_catalog_entry_uses_requires_star_as_success_status() -> None:
    forbidden_status_fragments = (
        "requires_approval",
        "requires_confirm_token",
        "requires_two_man",
    )
    valid_action_statuses = {s.value for s in ActionCommandStatus}
    for fragment in forbidden_status_fragments:
        assert fragment not in valid_action_statuses, (
            f"ActionCommandStatus must not include '{fragment}' — "
            "precondition failures are returned as error envelopes, not success statuses"
        )


def test_critical_actions_require_two_man() -> None:
    catalog = get_action_catalog().catalog
    critical = [e for e in catalog if e.risk_level == RiskLevel.CRITICAL]
    assert critical, "Expected at least one CRITICAL-risk action"
    for entry in critical:
        assert entry.requires_two_man, (
            f"{entry.action_id} is CRITICAL but does not require two-man authorization"
        )
        assert entry.requires_confirm_token, (
            f"{entry.action_id} is CRITICAL but does not require confirm token"
        )


def test_catalog_entry_can_be_projected_to_action_descriptor() -> None:
    for entry in get_action_catalog().catalog:
        descriptor = {
            "actionId": entry.action_id,
            "entityType": entry.entity_type,
            "endpoint": entry.endpoint,
            "method": entry.method,
            "riskLevel": entry.risk_level.value,
            "governance": {
                "requiresApproval": entry.requires_approval,
                "requiresConfirmToken": entry.requires_confirm_token,
                "requiresTwoMan": entry.requires_two_man,
                "cooldownSeconds": entry.cooldown_seconds,
                "idempotencyRequired": entry.idempotency_required,
            },
            "requiredRoles": entry.required_roles,
            "description": entry.description,
        }
        assert descriptor["actionId"]
        assert descriptor["riskLevel"] in ("low", "medium", "high", "critical")


def test_get_catalog_entry_lookup() -> None:
    entry = get_catalog_entry("ActivateKillSwitch")
    assert entry is not None
    assert entry.risk_level == RiskLevel.CRITICAL
    assert entry.requires_two_man
    assert entry.requires_confirm_token
    assert entry.requires_approval

    assert get_catalog_entry("NonExistentAction") is None


def test_runtime_repair_actions_are_high_risk_confirmed_and_auditable() -> None:
    action_ids = {
        "RestartPaperRuntime",
        "RestartTelemetryBridge",
        "TerminateStalePaperMonitoringSession",
        "StartPaperMonitoringSession",
        "ProbeTelemetryIngest",
    }
    for action_id in action_ids:
        entry = get_catalog_entry(action_id)
        assert entry is not None
        assert entry.entity_type == "Runtime"
        assert entry.risk_level == RiskLevel.HIGH
        assert entry.requires_confirm_token is True
        assert entry.idempotency_required is True
        assert "runtime_operator" in entry.required_roles


# --------------------------------------------------------------------------- #
# HTTP endpoint tests
# --------------------------------------------------------------------------- #

def test_get_bff_actions_endpoint_returns_catalog() -> None:
    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/actions",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "catalog" in body
    assert len(body["catalog"]) > 0
    assert body["version"] == "v1"
    assert body["generated_at"]


def test_get_bff_actions_requires_auth() -> None:
    client = TestClient(bff_main.app)
    response = client.get("/bff/actions")
    assert response.status_code == 401, response.text


def test_get_bff_actions_entry_schema() -> None:
    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/actions",
        headers={"Authorization": APPROVER_TOKEN},
    )
    assert response.status_code == 200
    for entry in response.json()["catalog"]:
        assert "action_id" in entry
        assert "entity_type" in entry
        assert "endpoint" in entry
        assert "risk_level" in entry
        assert "requires_approval" in entry
        assert "requires_confirm_token" in entry
        assert "requires_two_man" in entry
        assert "cooldown_seconds" in entry
        assert "idempotency_required" in entry
        assert "required_roles" in entry


def test_get_bff_actions_all_command_types_present_in_response() -> None:
    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/actions",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200
    returned_ids = {e["action_id"] for e in response.json()["catalog"]}
    for command_type in CommandType:
        assert command_type.value in returned_ids, (
            f"CommandType.{command_type.value} has no catalog entry in /bff/actions response"
        )
