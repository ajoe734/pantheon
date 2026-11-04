"""H4: a governance ``evolution_proposal`` approval binds ReviewMutation / ApproveEvolutionDecision.

Governance can only record an Evolution proposal approval with
``target_type=evolution_proposal``; the BFF object is ``EvolutionDecision``.
These tests drive the real ``POST /bff/v1/commands`` admission stack and then
replay the admitted command through the Evolution adapter against the real
Evolution owner app.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Isolate Evolution owner storage BEFORE importing it.
_tmp = tempfile.mkdtemp(prefix="evo_h4_bff_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)

from services.evolution.main import app as evolution_app  # noqa: E402
from services.control_plane.bff.command_adapters.preconditions import (  # noqa: E402
    _approval_decision_applies_to_command,
)
from services.control_plane.bff.command_queue import CommandStore  # noqa: E402
from services.control_plane.bff.models import (  # noqa: E402
    AuditContext,
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorCommand,
    TargetObject,
)
from services.control_plane.bff.tests.conftest import (  # noqa: E402
    ApprovalDecisionReadSurface,
    build_command_security_app,
    noop_process_command,
)

EXEC_POST = "services.control_plane.bff.command_executor._post_evolution_owner"
PRINCIPAL = "rita"
BEARER = f"Bearer {PRINCIPAL}:operator,risk_owner:mfa"
HEADERS = {"Authorization": BEARER, "X-Trace-Id": "trace-h4", "X-Correlation-Id": "corr-h4"}
ADAPTER_TOKEN = f"{PRINCIPAL}:operator,risk_owner"

owner = TestClient(evolution_app)

MEDIUM_FREEZE = dict(
    target_type="candidate_artifact",
    target_id="artifact-h4",
    target_version="v1",
    action_type="freeze",
    rationale="drift incident inc-drift-h4",
    created_by_id="evolution-controller",
    target_stage="paper",
    threshold_snapshots=[
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
            "signal_type": "feature_drift",
            "metric_name": "population_stability_index",
            "comparator": "gt",
            "observed_value": 0.31,
            "threshold_value": 0.30,
        }
    ],
)


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://evolution.test"}):
        yield


class _Ctx:
    def __init__(self, client: TestClient, store: CommandStore, read_store: ApprovalDecisionReadSurface):
        self.client = client
        self.store = store
        self.read_store = read_store

    def seed_approval(self, apv_id: str, target_id: str, *, state: str = "approved", target_type: str = "evolution_proposal") -> None:
        self.read_store.seed_approval_decision({
            "id": apv_id,
            "target_type": target_type,
            "target_id": target_id,
            "decision": state,
            "decision_state": "decided" if state in {"approved", "rejected"} else "pending",
        })

    def post(self, command: str, target: dict, params: dict, key: str):
        return self.client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": key},
            json={
                "command": command,
                "target": target,
                "params": params,
                "audit_context": {"reason": "H4 evolution approval binding"},
            },
        )


@contextmanager
def _ctx() -> Iterator[_Ctx]:
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(f"{td}/commands.jsonl")
        read_store = ApprovalDecisionReadSurface()
        app = build_command_security_app(
            command_store=store,
            read_store=read_store,
            process_command_task=lambda cmd_id: noop_process_command(cmd_id),
        )
        yield _Ctx(TestClient(app), store, read_store)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _propose() -> str:
    did = f"evo-h4-{uuid.uuid4().hex[:8]}"
    r = owner.post(
        "/api/evolution/proposals",
        json={**MEDIUM_FREEZE, "decision_id": did, "target_id": f"artifact-{did}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["decision_state"] == "proposed"
    assert r.json()["risk_level"] == "medium"
    return did


def _evo_target(did: str) -> dict:
    return {"type": "EvolutionDecision", "id": did}


def _review_params(did: str, apv: str) -> dict:
    return {"decision_id": did, "approval_decision_id": apv, "actor_role": "risk_owner"}


def _approve_params(did: str, apv: str) -> dict:
    return {
        "evolution_decision_id": did,
        "approval_action": "approve",
        "approval_decision_id": apv,
        "actor_role": "risk_owner",
    }


def _owner_http(url, payload=None, auth_token=None, **_kw):
    # Service-credential/tenant transport is covered by test_evolution_owner_write_service_auth.py.
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]
    response = owner.post(path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _replay(ctx: _Ctx, response, command_type: str) -> dict:
    """Run an admitted command's stored params through the real command executor."""
    command_id = response.json()["data"]["command_id"]
    record = ctx.store.get_command(command_id)
    params = dict(record["params"])
    from services.control_plane.bff import command_executor

    with patch(EXEC_POST, side_effect=_owner_http):
        status, receipt, error = command_executor.execute_command_with_status(
            command_id, CommandType(command_type), params, auth_token=ADAPTER_TOKEN
        )
    assert status == CommandStatus.EXECUTED, error
    ctx.store.update_status(command_id, CommandStatus.EXECUTED)
    return receipt


def test_evolution_proposal_approval_drives_proposed_reviewed_approved():
    did = _propose()
    with _ctx() as ctx:
        ctx.seed_approval("apv-h4-1", did)

        review = ctx.post("ReviewMutation", _evo_target(did), _review_params(did, "apv-h4-1"), "idem-review")
        assert review.status_code == 202, review.text
        _replay(ctx, review, "ReviewMutation")
        assert owner.get(f"/api/evolution/proposals/{did}").json()["decision_state"] == "reviewed"

        approve = ctx.post("ApproveEvolutionDecision", _evo_target(did), _approve_params(did, "apv-h4-1"), "idem-approve")
        assert approve.status_code == 202, approve.text
        _replay(ctx, approve, "ApproveEvolutionDecision")

    final = owner.get(f"/api/evolution/proposals/{did}").json()
    assert final["decision_state"] == "approved"
    assert final["approval_decision_id"] == "apv-h4-1"
    chain = {step["step_type"]: step for step in final["review_chain"]}
    for step_type in ("reviewed", "approved"):
        assert chain[step_type]["actor_id"] == PRINCIPAL
        assert chain[step_type]["actor_role"] == "risk_owner"


@pytest.mark.parametrize("command", ["ReviewMutation", "ApproveEvolutionDecision"])
def test_other_evolution_decision_id_is_binding_mismatch(command):
    did, other = _propose(), "evo-h4-other"
    with _ctx() as ctx:
        ctx.seed_approval("apv-h4-2", did)
        params = _review_params(other, "apv-h4-2") if command == "ReviewMutation" else _approve_params(other, "apv-h4-2")
        response = ctx.post(command, _evo_target(other), params, f"idem-{command}")
        assert response.status_code == 409, response.text
        assert _reason(response) == "APPROVAL_DECISION_BINDING_MISMATCH"
        # Target id matches but params name another decision.
        mixed = ctx.post(command, _evo_target(did), params, f"idem-mixed-{command}")
        assert mixed.status_code == 409, mixed.text
        assert _reason(mixed) == "APPROVAL_DECISION_BINDING_MISMATCH"


def _binds(command: CommandType, target_type: ObjectType, target_id: str, params: dict | None = None) -> bool:
    cmd = OperatorCommand(
        command=command,
        target=TargetObject(type=target_type, id=target_id),
        params=params or {},
        audit_context=AuditContext(reason="binding check"),
    )
    record = {"id": "apv", "target_type": "evolution_proposal", "target_id": "evo-x", "decision": "approved"}
    return _approval_decision_applies_to_command(record, "apv", cmd)


def test_evolution_commands_bind_only_to_the_same_evolution_decision():
    for command in (CommandType.REVIEW_MUTATION, CommandType.APPROVE_EVOLUTION_DECISION):
        assert _binds(command, ObjectType.EVOLUTION_DECISION, "evo-x")
        assert not _binds(command, ObjectType.EVOLUTION_DECISION, "evo-y")
        assert not _binds(command, ObjectType.EVOLUTION_DECISION, "evo-x", {"decision_id": "evo-y"})
        assert not _binds(command, ObjectType.APPROVAL_DECISION, "evo-x")


@pytest.mark.parametrize(
    "command,target_type",
    [
        (CommandType.APPROVE_DECISION, ObjectType.APPROVAL_DECISION),
        (CommandType.APPROVE_DECISION, ObjectType.EVOLUTION_DECISION),
        (CommandType.ADVANCE_LIFECYCLE, ObjectType.EVOLUTION_DECISION),
        (CommandType.PAUSE_RUNTIME, ObjectType.RUNTIME),
        (CommandType.EXECUTE_EVOLUTION_ACTION, ObjectType.EVOLUTION_DECISION),
    ],
)
def test_evolution_proposal_approval_never_binds_to_other_commands(command, target_type):
    assert not _binds(command, target_type, "evo-x")


@pytest.mark.parametrize("command", ["ReviewMutation", "ApproveEvolutionDecision"])
@pytest.mark.parametrize("state", ["pending", "rejected"])
def test_pending_or_rejected_approval_is_not_approved(command, state):
    did = "evo-h4-state"
    with _ctx() as ctx:
        ctx.seed_approval("apv-h4-4", did, state=state)
        params = _review_params(did, "apv-h4-4") if command == "ReviewMutation" else _approve_params(did, "apv-h4-4")
        response = ctx.post(command, _evo_target(did), params, f"idem-{command}-{state}")
        assert response.status_code == 409, response.text
        assert _reason(response) == "APPROVAL_DECISION_NOT_APPROVED"


def test_other_governance_target_types_stay_unbound_for_evolution_commands():
    did = "evo-h4-other-type"
    with _ctx() as ctx:
        ctx.seed_approval("apv-h4-5", did, target_type="deployment_plan")
        response = ctx.post("ReviewMutation", _evo_target(did), _review_params(did, "apv-h4-5"), "idem-dp")
        assert response.status_code == 409, response.text
        assert _reason(response) == "APPROVAL_DECISION_BINDING_MISMATCH"


@pytest.mark.parametrize("params", [{"actor_id": "someone-else"}, {"actor_role": "governance_committee"}])
def test_actor_binding_still_refuses_foreign_actor_or_unheld_role(params):
    from starlette.exceptions import HTTPException

    did = _propose()
    from services.control_plane.bff import command_executor

    with patch(EXEC_POST) as http:
        with pytest.raises(HTTPException) as exc:
            command_executor._execute_review_mutation(
                "c", {"decision_id": did, "approval_decision_id": "apv", **params},
                auth_token=ADAPTER_TOKEN,
            )
        assert exc.value.status_code == 403
        http.assert_not_called()
