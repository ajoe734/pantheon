"""StrategyCommandAdapter must call the real Registry owner and verify a
durable readback, never fabricate a resulting_status.

Prior behavior (architecture-resumption-sa-sd.md §2): _execute_strategy_action
mapped action_id -> a static resulting_status and returned it as an
"authoritative_readback" with zero owner I/O. This suite proves the fix:
- update_params performs a real GET (base metadata) + PATCH (CAS) + GET
  (readback) sequence against the Registry owner and returns the verified
  readback, not the PATCH response body.
- submit_review/promote_paper/activate/pause/archive raise
  ActionUnavailableError naming the exact non-Registry owner from
  services.registry.command_contract, instead of returning a fabricated
  status — a caller can no longer mistake "the adapter didn't crash" for
  "the business action happened".
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.command_adapters.strategy_adapter import StrategyCommandAdapter


@pytest.fixture
def adapter():
    return StrategyCommandAdapter()


@pytest.fixture(autouse=True)
def registry_url_env(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry-svc.internal")


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json")
def test_update_params_calls_real_registry_owner_and_returns_verified_readback(mock_http, adapter):
    mock_http.side_effect = [
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "old"}}},  # GET base
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "new"}}},  # PATCH response (ignored as truth)
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "new"}}},  # GET readback
    ]

    result = adapter.execute(
        "cmd-strat-001",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "metadata": {"note": "new"},
        },
        auth_token="test-token",
    )

    assert result["status"] == "metadata_updated"
    # The authoritative_readback must be the *verified GET*, not the PATCH
    # request/response body echoed back as truth.
    assert result["authoritative_readback"]["metadata"] == {"note": "new"}
    assert result["entity_id"] == "strat-alpha"
    assert result["domain_receipt"]["registry_id"] == "reg-001"

    assert mock_http.call_count == 3
    get_base_call, patch_call, get_readback_call = mock_http.call_args_list
    assert get_base_call.kwargs["method"] == "GET"
    assert get_readback_call.kwargs["method"] == "GET"
    assert patch_call.kwargs["method"] == "PATCH"
    assert patch_call.kwargs["payload"]["expected_metadata"] == {"note": "old"}
    assert patch_call.kwargs["payload"]["metadata"] == {"note": "new"}
    assert patch_call.kwargs["payload"]["command_key"] == "cmd-strat-001"


def test_update_params_requires_registry_id(adapter):
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "update_params",
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_REGISTRY_ID"


@pytest.mark.parametrize(
    "action_id,expected_owner",
    [
        ("submit_review", "governance_review"),
        ("promote_paper", "promotion"),
        ("activate", "runtime"),
        ("pause", "runtime"),
        ("archive", "runtime"),
    ],
)
def test_non_registry_actions_fail_explicitly_naming_the_real_owner(adapter, action_id, expected_owner):
    """These must never return a fabricated resulting_status again."""
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-003",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": action_id,
            },
        )
    assert expected_owner in str(excinfo.value)
    assert excinfo.value.error_code == "OWNER_NOT_INTEGRATED"


def test_unrecognized_action_fails_explicitly(adapter):
    with pytest.raises(ActionUnavailableError):
        adapter.execute(
            "cmd-strat-004",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "not_a_real_action",
            },
        )
