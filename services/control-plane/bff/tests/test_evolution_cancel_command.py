"""CancelMutation: governed cancel of EvolutionDecision via the owner cancel route."""
from __future__ import annotations

import io
import os
import urllib.error
from unittest.mock import patch

import pytest
from starlette.exceptions import HTTPException

from services.control_plane.bff.command_adapters.base import ActionUnavailableError

from services.control_plane.bff.action_catalog import catalog_action_ids, get_catalog_entry
from services.control_plane.bff.command_adapters.preconditions import _validate_cancel_mutation
from services.control_plane.bff.command_executor import _EXECUTORS, _execute_cancel_mutation
from services.control_plane.bff.models import CommandType

POST = "services.control_plane.bff.command_executor._post_evolution_owner"
OWNER_URL = "http://localhost:5001/api/evolution/proposals/evo-1/cancel"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://localhost:5001"}):
        yield


def _http_error(status, detail):
    return urllib.error.HTTPError(
        OWNER_URL, status, "err", {}, io.BytesIO(('{"detail": "%s"}' % detail).encode())
    )


def test_catalog_and_executor_registered():
    assert "CancelMutation" in catalog_action_ids()
    assert get_catalog_entry("CancelMutation").entity_type == "EvolutionDecision"
    assert _EXECUTORS[CommandType.CANCEL_MUTATION] is _execute_cancel_mutation


def test_cancel_posts_owner_route_with_operator_role_and_note():
    owner = {"decision_id": "evo-1", "decision_state": "canceled", "risk_level": "low",
             "updated_at": "2026-11-11T01:00:00Z"}
    with patch(POST, return_value=owner) as post:
        result = _execute_cancel_mutation(
            "cmd-1", {"decision_id": "evo-1", "note": "stale infra alert"}, auth_token="op-1:operator"
        )
    post.assert_called_once_with(
        OWNER_URL,
        {"actor_id": "op-1", "actor_role": "operator", "note": "stale infra alert"},
        auth_token="op-1:operator",
    )
    assert result["new_state"] == "canceled" and result["decision_state"] == "canceled"
    assert result["command_accepted"] is True
    assert result["committed_at"] == "2026-11-11T01:00:00Z"
    assert result["authoritative_readback"] == owner


def test_admin_plus_operator_resolves_to_cancel_capable_role():
    with patch(POST, return_value={"decision_state": "canceled"}) as post:
        _execute_cancel_mutation("c", {"decision_id": "evo-1", "note": "n"}, auth_token="a:admin,operator")
    assert post.call_args[0][1]["actor_role"] == "operator"


@pytest.mark.parametrize("state", ["executed", "rejected", "canceled"])
def test_owner_rejection_of_terminal_state_propagates(state):
    err = _http_error(422, f"Can only cancel from proposed/reviewed/approved, got {state!r}")
    with patch(POST, side_effect=err):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _execute_cancel_mutation("c", {"decision_id": "evo-1", "note": "n"}, auth_token="op:operator")
    assert exc.value.code == 422


def test_owner_role_rejection_propagates():
    with patch(POST, side_effect=_http_error(422, "actor_role is not allowed to cancel EvolutionDecision")):
        with pytest.raises(urllib.error.HTTPError):
            _execute_cancel_mutation("c", {"decision_id": "evo-1", "note": "n"}, auth_token="op:operator")


def test_other_tenant_decision_is_refused_by_owner():
    with patch(POST, side_effect=_http_error(404, "evolution decision not found")):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _execute_cancel_mutation("c", {"decision_id": "evo-1", "note": "n"}, auth_token="op:operator")
    assert exc.value.code == 404


def test_payload_tenant_must_match_verified_tenant():
    """Real tenant binding (no mocked owner request): foreign tenant never reaches HTTP."""
    from services.control_plane.bff.command_adapters.base import ActionUnavailableError

    with patch.dict(os.environ, {"EVOLUTION_AUTH_TOKEN": "svc"}), \
         patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(ActionUnavailableError):
            from services.control_plane.bff.command_adapters.base import evolution_owner_request
            evolution_owner_request(
                OWNER_URL, caller_token="op:operator", method="POST",
                payload={"tenant_id": "someone-else-tenant", "note": "n"},
            )
        urlopen.assert_not_called()


def test_foreign_actor_id_and_unheld_role_forbidden():
    for params in ({"actor_id": "other"}, {"actor_role": "governance_committee"}):
        with patch(POST) as post:
            with pytest.raises((HTTPException, ActionUnavailableError)) as exc:
                _execute_cancel_mutation(
                    "c", {"decision_id": "evo-1", "note": "n", **params}, auth_token="op:operator"
                )
            assert getattr(exc.value, "status_code", None) == 403 or getattr(exc.value, "downstream_status", None) == 403
            post.assert_not_called()


def test_requires_decision_id_and_note():
    with patch(POST) as post:
        with pytest.raises(ValueError):
            _execute_cancel_mutation("c", {"note": "n"}, auth_token="op:operator")
        with pytest.raises(ValueError):
            _execute_cancel_mutation("c", {"decision_id": "evo-1"}, auth_token="op:operator")
        post.assert_not_called()


class _Err(Exception):
    pass


def _err_fn(status, code, msg, reason, **kw):
    return HTTPException(status_code=status, detail=msg)


def test_precondition_requires_note_and_blocks_terminal_state():
    with pytest.raises(HTTPException) as exc:
        _validate_cancel_mutation({"decision_id": "evo-1"}, object(), bff_error_fn=_err_fn)
    assert exc.value.status_code == 422
    proj = {"decision_state": "executed"}
    with patch("services.control_plane.bff.command_adapters.preconditions._mutation_review_projection",
               return_value=proj):
        with pytest.raises(HTTPException) as exc:
            _validate_cancel_mutation({"decision_id": "evo-1", "note": "n"}, object(), bff_error_fn=_err_fn)
        assert exc.value.status_code == 409
    for ok in ("proposed", "reviewed", "approved"):
        with patch("services.control_plane.bff.command_adapters.preconditions._mutation_review_projection",
                   return_value={"decision_state": ok}):
            _validate_cancel_mutation({"decision_id": "evo-1", "note": "n"}, object(), bff_error_fn=_err_fn)
    with patch("services.control_plane.bff.command_adapters.preconditions._mutation_review_projection",
               return_value=None):
        _validate_cancel_mutation({"decision_id": "evo-1", "note": "n"}, object(), bff_error_fn=_err_fn)
