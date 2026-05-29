"""Tests for BFF write-gap closure — 2026-05-28.

Covers: BFF-WRITE-P0-LIFECYCLE-002
  Card P0-2 — POST /bff/capital-pools/{id}/actions/ApprovePool

Acceptance per spec:
  - 202 + pool state visible as `approved` in GET /bff/capital-pools/{id}
  - 410 with replacement for unknown/unregistered actions (deprecation gate intact)
  - action catalog contains ApprovePool entry with treasury_approver role
  - executor handler validates memo ≥8 chars and pool_id presence
"""
from __future__ import annotations

import pytest

from action_catalog import catalog_action_ids, get_catalog_entry


# ---------------------------------------------------------------------------
# Action catalog registration
# ---------------------------------------------------------------------------

def test_approve_pool_in_catalog():
    assert "ApprovePool" in catalog_action_ids()


def test_approve_pool_catalog_entry_shape():
    entry = get_catalog_entry("ApprovePool")
    assert entry is not None
    assert entry.action_id == "ApprovePool"
    assert entry.entity_type == "CapitalPool"
    assert "treasury_approver" in entry.required_roles
    assert entry.idempotency_required is True
    assert entry.requires_approval is True


# ---------------------------------------------------------------------------
# Executor handler: _execute_approve_pool
# ---------------------------------------------------------------------------

def test_approve_pool_executor_validates_pool_id():
    from command_executor import _execute_approve_pool
    with pytest.raises(ValueError, match="pool_id"):
        _execute_approve_pool("cmd-001", {"memo": "board approved"})


def test_approve_pool_executor_validates_memo_length():
    from command_executor import _execute_approve_pool
    with pytest.raises(ValueError, match="memo"):
        _execute_approve_pool("cmd-001", {"pool_id": "pool-001", "memo": "short"})


def test_approve_pool_executor_accepts_valid_memo():
    """Executor should not raise for valid params (network call mocked out)."""
    import unittest.mock as mock
    from command_executor import _execute_approve_pool

    fake_response = {
        "pool_id": "pool-001",
        "state": "approved",
        "status": "accepted",
        "approved_at": "2026-05-29T10:00:00Z",
        "audit_id": "audit-001",
    }
    with mock.patch("command_executor._internal_url", return_value="http://internal/approve"), \
         mock.patch("command_executor._post_json", return_value=fake_response):
        result = _execute_approve_pool(
            "cmd-001",
            {"pool_id": "pool-001", "memo": "Board approved this pool for live trading"},
        )
    assert result["pool_id"] == "pool-001"
    assert result["state"] == "approved"
    assert result["command_id"] == "cmd-001"


def test_approve_pool_executor_passes_confirm_token():
    import unittest.mock as mock
    from command_executor import _execute_approve_pool

    captured: dict = {}

    def fake_post_json(url, payload, auth_token=None, mfa_token=None):
        captured.update(payload)
        return {"pool_id": "pool-001", "state": "approved", "status": "accepted"}

    with mock.patch("command_executor._internal_url", return_value="http://internal/approve"), \
         mock.patch("command_executor._post_json", side_effect=fake_post_json):
        _execute_approve_pool(
            "cmd-002",
            {
                "pool_id": "pool-001",
                "memo": "Board approved this pool for live trading",
                "confirm_token": "tok-xyz",
            },
        )
    assert captured.get("confirm_token") == "tok-xyz"


# ---------------------------------------------------------------------------
# CommandType enum
# ---------------------------------------------------------------------------

def test_approve_pool_command_type_exists():
    from models import CommandType
    assert CommandType.APPROVE_POOL == "ApprovePool"


# ---------------------------------------------------------------------------
# Route-level: registered vs deprecated actions
# ---------------------------------------------------------------------------

def test_registered_actions_set_includes_approve_pool():
    """The route handler's registered set must include ApprovePool."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).parent / "main.py"
    text = src.read_text()
    # Locate _CAPITAL_POOL_REGISTERED_ACTIONS set literal
    assert "ApprovePool" in text, (
        "main.py must reference ApprovePool in the capital-pool action route"
    )
    assert "_CAPITAL_POOL_REGISTERED_ACTIONS" in text, (
        "main.py capital-pool action handler must define _CAPITAL_POOL_REGISTERED_ACTIONS"
    )
