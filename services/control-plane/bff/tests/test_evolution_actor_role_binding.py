"""Evolution decision commands bind actor_id/actor_role to the verified principal."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from starlette.exceptions import HTTPException

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_adapters import evolution_adapter as adapter_mod
from services.control_plane.bff.command_adapters.evolution_adapter import EvolutionCommandAdapter
from services.control_plane.bff.command_executor import (
    _actor_context,
    _execute_approve_evolution_decision,
    _execute_approve_mutation,
)

EXEC = "services.control_plane.bff.command_executor._post_json"
ADAPTER_HTTP = "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://localhost:5001"}):
        yield


def _approve(params, token):
    return _execute_approve_evolution_decision(
        "cmd-1", {"evolution_decision_id": "evo-1", "approval_action": "approve", **params}, auth_token=token,
    )


@pytest.mark.parametrize("claimed", ["risk_owner", "governance_committee"])
def test_approver_only_cannot_claim_higher_role(claimed):
    with patch(EXEC) as post:
        for key in ("actor_role", "approved_by_role"):
            with pytest.raises(HTTPException) as exc:
                _approve({key: claimed}, "alice:approver")
            assert exc.value.status_code == 403
        post.assert_not_called()


def test_risk_owner_can_approve_medium():
    with patch(EXEC, return_value={"decision_state": "approved"}) as post:
        _approve({"actor_role": "risk_owner"}, "rita:risk_owner,approver")
    payload = post.call_args[0][1]
    assert payload["actor_role"] == "risk_owner"
    # governance APPROVAL_OWNER_MATRIX[MEDIUM] == {risk_owner}


def test_params_cannot_override_actor_id():
    for key in ("actor_id", "approved_by_id"):
        with patch(EXEC) as post:
            with pytest.raises(HTTPException) as exc:
                _approve({key: "someone-else"}, "alice:approver")
            assert exc.value.status_code == 403
            post.assert_not_called()
    with patch(EXEC, return_value={}) as post:
        _approve({"actor_id": "alice"}, "alice:approver")
    assert post.call_args[0][1]["actor_id"] == "alice"


def test_default_role_only_from_held_and_403_when_none():
    assert _actor_context({}, "u:reviewer_on_duty") == ("u", "reviewer_on_duty")
    assert _actor_context({}, "u:viewer,reviewer_on_duty,approver") == ("u", "reviewer_on_duty")
    with pytest.raises(HTTPException) as exc:
        _actor_context({}, "u:viewer")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        _actor_context({"actor_id": "x"}, None)


def test_test_admin_reviews_and_approves_low_paper_retrain_as_reviewer_on_duty():
    roles = set(auth_policy._DEV_LOGIN_IDENTITY_DEFS["test_admin"]["roles"])
    assert "reviewer_on_duty" in roles
    token = "admin-test:" + ",".join(sorted(roles))
    for verb in ("review", "approve"):
        with patch(ADAPTER_HTTP, return_value={"decision_state": verb}) as http:
            EvolutionCommandAdapter().execute(
                "cmd-2",
                "ReviewMutation" if verb == "review" else "ApproveMutation",
                {"evolution_decision_id": "evo-low", "actor_role": "reviewer_on_duty"},
                auth_token=token,
            )
        payload = http.call_args.kwargs["payload"]
        assert payload["actor_id"] == "admin-test"
        assert payload["actor_role"] == "reviewer_on_duty"
        # governance review/approval OWNER_MATRIX[LOW] == {reviewer_on_duty, automated_gate}


@pytest.mark.parametrize(
    "command,params",
    [
        ("ApproveMutation", {"actor_role": "governance_committee"}),
        ("ReviewMutation", {"approved_by_role": "risk_owner"}),
        ("RejectMutation", {"actor_id": "other"}),
        ("ExecuteMutation", {"actor_id": "other"}),
    ],
)
def test_adapter_rejects_unheld_role_or_foreign_actor(command, params):
    with patch(ADAPTER_HTTP) as http:
        with pytest.raises(HTTPException) as exc:
            EvolutionCommandAdapter().execute(
                "cmd-3", command, {"evolution_decision_id": "evo-1", **params}, auth_token="alice:approver",
            )
        assert exc.value.status_code == 403
        http.assert_not_called()


def test_adapter_without_principal_sends_nothing():
    with patch(ADAPTER_HTTP) as http:
        with pytest.raises(HTTPException) as exc:
            EvolutionCommandAdapter().execute("c", "ApproveMutation", {"evolution_decision_id": "e"})
        assert exc.value.status_code == 403
        http.assert_not_called()


def test_approve_mutation_executor_path_is_bound():
    with patch(EXEC) as post:
        with pytest.raises(HTTPException) as exc:
            _execute_approve_mutation(
                "c", {"evolution_decision_id": "e", "approved_by_role": "governance_committee"},
                auth_token="alice:approver",
            )
        assert exc.value.status_code == 403
        post.assert_not_called()


def test_mfa_token_is_threaded_to_identity_resolution(monkeypatch):
    """With MFA required, a valid MFA token resolves; without it the command is forbidden."""
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "true")
    seen = []

    def fake_extract(authorization, mfa_token=None, session_cookie=None):
        seen.append(mfa_token)
        if not mfa_token:
            raise HTTPException(status_code=401, detail="MFA required")
        return auth_policy.OperatorIdentity(operator_id="mia", roles=["approver"], mfa_verified=True)

    monkeypatch.setattr(auth_policy, "extract_identity", fake_extract)
    assert _actor_context({}, "tok", mfa_token="123456") == ("mia", "approver")
    with pytest.raises(HTTPException) as exc:
        _actor_context({}, "tok")
    assert exc.value.status_code == 403
    with patch(EXEC, return_value={}) as post:
        _execute_approve_evolution_decision(
            "cmd-1", {"evolution_decision_id": "evo-1", "approval_action": "approve"},
            auth_token="tok", mfa_token="123456",
        )
    assert post.call_args[0][1]["actor_id"] == "mia"
    assert seen[-1] == "123456"
